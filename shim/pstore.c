/*
 * pstore.c - hand the probe RAM log to Android through the Linux ramoops
 * console zone, so it reappears as /sys/fs/pstore/console-ramoops-0 after
 * a warm reset (PSCI SYSTEM_RESET). Warm reset keeps DRAM up; whether ABL
 * scrubs this region on the way to the kernel is UNKNOWN and exactly what
 * the hardware run must test - hence the marker line and fail-safe skips.
 *
 * The zone math mirrors Linux exactly (torvalds/linux master f47d223627f8,
 * fetched 2026-10-20) so the shim writes where the Android kernel will
 * attach:
 *
 * fs/pstore/ram.c:
 *   ramoops_parse_dt() [line 646] reads the /reserved-memory child's reg
 *     plus mem-type, record-size, console-size, ftrace-size, pmsg-size,
 *     ecc-size; absent properties default to 0.
 *   ramoops_probe() [line 722]:
 *     - rounds record/console/ftrace/pmsg sizes down to powers of two
 *       [lines 766-772];
 *     - dump_mem_sz = size - console_size - ftrace_size - pmsg_size
 *       [line 786];
 *     - allocates zones in order: dmesg [line 788], console [794], pmsg
 *       [799], ftrace [807], each advancing paddr.
 *   ramoops_init_przs() [line 485]: mem_sz==0 or record_size==0 -> no
 *     zones and no paddr advance [lines 497-500]; otherwise
 *     cnt = mem_sz / record_size [518], zone_sz = mem_sz / cnt rounded
 *     down to a multiple of 2 [534-535], advancing *paddr per zone [571].
 *     So the console zone starts at region base + cnt*zone_sz, which for
 *     the usual whole-number split equals base + dump_mem_sz; a residue
 *     smaller than one zone is left unused.
 *   ramoops_init_prz() [line 583] places the console prz at the current
 *     paddr with size console_size.
 *
 * fs/pstore/ram_core.c:
 *   struct persistent_ram_buffer { u32 sig; atomic_t start; atomic_t
 *     size; u8 data[]; } [line 32], 12-byte header;
 *   PERSISTENT_RAM_SIG 0x43474244 ('DBGC', little-endian in memory)
 *     [line 39];
 *   buffer_size = zone size - sizeof(header) [line 521];
 *   persistent_ram_post_init() [line 526]: a matching signature with
 *     size==0 && start==0 returns early (no old log) [541]; size >
 *     buffer_size or start > size invalidates the buffer [545-547];
 *     otherwise persistent_ram_save_old() [line 290] copies
 *     data[start..size) then data[0..start) [line 320] into the old log,
 *     which is exactly what /sys/fs/pstore/console-ramoops-0 contains.
 *   persistent_ram_init_ecc(): with ecc_size the data area shrinks by
 *     the total RS parity bytes (buffer_size -= ecc_total) [line 214]
 *     and every block carries Reed-Solomon parity computed by the
 *     kernel. A raw write without that parity would be "corrected" by
 *     decode_rs8 on the next boot, silently corrupting the log; so
 *     ecc-size != 0 means print 'PSTORE: ecc enabled, skipped' and stop.
 *
 * The one-shot encoding is start=0, size=len (the non-wrapped form
 * persistent_ram_write() [line 324] produces first; keeping the TAIL
 * when the log exceeds capacity mirrors its head-dropping at line 332;
 * the marker line is kept as the fixed first line of the retained
 * data so tools/pstore-pull.py can always identify the build;
 * start=0 with size==buffer_size passes the validity checks above).
 *
 * Stores go only to [zone, zone + pow2-floored console_size): every store
 * funnels through zone_put8(), which compares the byte offset against the
 * zone extent computed at init. The MMU is off (Device memory, no cache
 * maintenance); dsb sy barriers order data -> header -> signature, and
 * any preexisting signature is cleared first, so a torn write can only
 * ever look empty or hold one complete generation of the log.
 */
#include <stdint.h>
#include "pstore.h"
#include "console.h"
#include "fdt.h"
#include "ramlog.h"

/* The linked image includes both stacks (shim.ld places _stack_top and
 * _sec_stacks below _end), so the image range gate below covers the
 * stacks and every exception frame pushed beneath them. */
/* Linker-script symbols: _head = start.S:4 label (ENTRY, shim.ld:1), _end = shim.ld:19; names fixed by start.S/shim.ld/build.sh, so no rename. */
extern char _head[], _end[]; // NOLINT(bugprone-reserved-identifier)

#define PSTORE_SIG		0x43474244u	/* 'DBGC' LE in memory */
#define PSTORE_HDR		12u		/* sig + start + size */
#define PSTORE_MIN_CONSOLE	4096u		/* kernel MIN_MEM_SIZE */

/* Historical GS101 rmem DTS values (docs/research/01-boot-path.md s.6):
 * UNVERIFIED for this phone; printed loudly when used. */
#define FALLBACK_BASE		0xfd3ff000ULL
#define FALLBACK_SIZE		0x400000u
#define FALLBACK_CONSOLE	0x200000u
#define FALLBACK_RECORD		0u
#define FALLBACK_ECC		0u

const char pstore_marker[] = "PIXELIOS PSTORE v1 build=" PSTORE_BUILD_ID;

static volatile uint8_t *zone_mem;	/* console zone base (sig lives here) */
static uint32_t zone_size;		/* pow2-floored console_size: zone extent */
static uint32_t zone_cap;		/* zone_size - 12 usable data bytes */
static uint64_t zone_base;
static int ready, used_fallback;

static inline void dsb_sy(void)
{
#ifdef __aarch64__
	__asm__ volatile("dsb sy" ::: "memory");
#else
	__asm__ volatile("" ::: "memory");
#endif
}

static uint64_t my_mpidr(void)
{
#ifdef __aarch64__
	uint64_t m;
	__asm__ volatile("mrs %0, mpidr_el1" : "=r"(m));
	return m;
#else
	return 0x1000;
#endif
}

/* Cross-CPU exclusion with same-CPU re-entry allowed: an exception
 * inside a save abandons it, and the panic path re-enters on the same
 * CPU, where a fresh linear overwrite is safe (same-address Device
 * stores from one CPU are ordered). A different CPU gets bounded
 * retries, then skips - nothing here may spin forever. */
static volatile uint64_t save_owner;

static int save_lock(void)
{
	uint64_t self = my_mpidr();
	if (save_owner == self)
		return 1;
#ifdef __aarch64__
	for (int i = 0; i < 64; i++) {
		uint64_t old;
		uint32_t ok;
		__asm__ volatile("ldaxr %0, [%1]" : "=r"(old) : "r"(&save_owner) : "memory");
		if (old != 0)
			continue;
		__asm__ volatile("stxr %w0, %1, [%2]"
				 : "=&r"(ok)
				 : "r"(self), "r"(&save_owner)
				 : "memory");
		if (ok == 0)
			return 1;
	}
	return 0;
#else
	save_owner = self;		/* host tests are single-threaded */
	return 1;
#endif
}

static void save_unlock(void)
{
	if (save_owner == my_mpidr())
		save_owner = 0;
	dsb_sy();
}

static void status(const char *s);

/* Panic-path save (exc_handler). The save lock deliberately allows
 * same-CPU re-entry, so a fault INSIDE pstore_save would recurse
 * exc_handler -> pstore_save -> fault -> ... on one stack until it
 * tramples the image. This guard allows one attempt per panic: a
 * nested entry on the same CPU prints one line and skips the save;
 * the panic path (screen, pager, reset) continues. */
static volatile uint64_t panic_save_cpu;

void pstore_panic_save(void)
{
	uint64_t self = my_mpidr();
	if (panic_save_cpu == self) {
		status("panic save re-entered; skipped");
		return;
	}
	if (panic_save_cpu != 0) {
		/* The one-shot guard is a single slot: another CPU mid-panic
		 * owns it. Clobbering it (the old behaviour) re-arms that
		 * CPU's fault recursion; skipping here only loses this CPU's
		 * save, which the save lock would have skipped anyway once the
		 * owner is inside pstore_save (review N1). */
		status("panic save busy on other CPU; skipped");
		return;
	}
	panic_save_cpu = self;
	pstore_save();
	if (panic_save_cpu == self)
		panic_save_cpu = 0;
}

/* The only stores to the zone in this file: each is gated by a runtime
 * compare against the zone extent computed in pstore_init(). The
 * objdump audit for the build shows every strb preceded by this cmp. */
static void zone_put8(uint32_t off, uint8_t v)
{
	if (off < zone_size)
		zone_mem[off] = v;
}

static void zone_put32(uint32_t off, uint32_t v)
{
	for (unsigned i = 0; i < 4; i++)
		zone_put8(off + i, (uint8_t)(v >> (8 * i)));
}

static uint32_t pow2_floor(uint32_t v)
{
	/* bounded: v &= v - 1 clears one set bit per pass, at most 32 passes */
	while (v && (v & (v - 1)))
		v &= v - 1;
	return v;
}

uint64_t pstore_console_offset(uint64_t region_size, uint32_t record_size,
			       uint32_t console_size, uint32_t ftrace_size,
			       uint32_t pmsg_size, uint32_t *cap_out)
{
	record_size = pow2_floor(record_size);
	console_size = pow2_floor(console_size);
	ftrace_size = pow2_floor(ftrace_size);
	pmsg_size = pow2_floor(pmsg_size);
	if (!console_size) {
		if (cap_out)
			*cap_out = 0;
		return UINT64_MAX;		/* kernel probes, but no console prz exists */
	}

	uint64_t used = (uint64_t)console_size + ftrace_size + pmsg_size;
	if (used > region_size) {
		if (cap_out)
			*cap_out = 0;
		return UINT64_MAX;		/* kernel probe fails: no zone at all */
	}

	uint64_t dump = region_size - used;
	uint64_t advance = 0;
	if (dump != 0 && record_size != 0) {
		uint64_t cnt = dump / record_size;
		if (cnt == 0) {
			if (cap_out)
				*cap_out = 0;
			return UINT64_MAX;	/* kernel: record count == 0, probe fails */
		}
		uint64_t zone = dump / cnt;
		zone &= ~1ULL;			/* ALIGN_DOWN(zone_sz, 2) */
		advance = cnt * zone;
	}
	if (cap_out)
		*cap_out = console_size - PSTORE_HDR;
	return advance;
}

/* ---- range helpers ---- */

static int range_overlaps(uint64_t b1, uint64_t s1, uint64_t b2, uint64_t s2)
{
	if (!s1 || !s2)
		return 0;
	if (b1 > UINT64_MAX - s1 || b2 > UINT64_MAX - s2)
		return 1;	/* wrapped extent: assume overlap, fail closed (D6) */
	return b1 < b2 + s2 && b2 < b1 + s1;
}

static int range_covers(uint64_t inner_b, uint64_t inner_s, uint64_t outer_b, uint64_t outer_s)
{
	if (!inner_s)
		return 0;
	return inner_b >= outer_b && inner_s <= outer_s && inner_b + inner_s <= outer_b + outer_s;
}

/* ---- console line formatting (label <= 8 columns like the rest of the probe) ---- */

static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++)
		dst[pos++] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	return pos;
}

static unsigned put_dec(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char tmp[10];
	unsigned n = 0;
	do {
		tmp[n++] = (char)('0' + v % 10);
		v /= 10;
	} while (v && n < sizeof(tmp));
	while (n && pos + 1 < cap)
		dst[pos++] = tmp[--n];
	return pos;
}

static void status(const char *s)
{
	con_str("PSTORE", s);
}

static void status_hex2(const char *what, uint64_t a, uint64_t b)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, what);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_hex(line, sizeof(line), p, a, 16);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_hex(line, sizeof(line), p, b, 16);
	line[p] = 0;
	status(line);
}

static void status_hex4(const char *what, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, what);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_hex(line, sizeof(line), p, a, 8);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_hex(line, sizeof(line), p, b, 8);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_hex(line, sizeof(line), p, c, 8);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_hex(line, sizeof(line), p, d, 8);
	line[p] = 0;
	status(line);
}

static uint32_t fdt_totalsize(uintptr_t fdt)
{
	if (!fdt)
		return 0;
	return __builtin_bswap32(*(volatile uint32_t *)(fdt + 4));
}

int pstore_init(uintptr_t fdt)
{
	pstore_reset();

	struct fdt_ramoops ro;
	int from_fdt = fdt_ramoops(fdt, &ro);

	if (from_fdt) {
		status("ramoops node in live FDT");
	} else {
		ro.base = FALLBACK_BASE;
		ro.size = FALLBACK_SIZE;
		ro.record_size = FALLBACK_RECORD;
		ro.console_size = FALLBACK_CONSOLE;
		ro.ftrace_size = 0;
		ro.pmsg_size = 0;
		ro.ecc_size = FALLBACK_ECC;
		ro.mem_type = 0;
		used_fallback = 1;
		status(ro.qualified_no_reg
			       ? "FDT ramoops node has no usable reg: GS101 fallback (UNVERIFIED)"
			       : "no FDT ramoops node: GS101 fallback (UNVERIFIED)");
	}

	status_hex2("base/size", ro.base, ro.size);
	status_hex4("rec/con/pmsg/ftr", ro.record_size, ro.console_size, ro.pmsg_size, ro.ftrace_size);
	status_hex4("ecc/mem-type/flg/0", ro.ecc_size, ro.mem_type, 0, 0);

	if (ro.ecc_size) {
		/* Exact contract line; grep this in the pulled log. */
		con_str("PSTORE:", "ecc enabled, skipped");
		return 0;
	}

	uint32_t cap = 0;
	uint64_t off = pstore_console_offset(ro.size, ro.record_size, ro.console_size,
					     ro.ftrace_size, ro.pmsg_size, &cap);
	if (off == UINT64_MAX || cap < PSTORE_MIN_CONSOLE - PSTORE_HDR) {
		status("no usable console zone (sizes); skipped");
		return 0;
	}
	/* The kernel's zone size is console_size rounded DOWN to a power of
	 * two (ram.c 766-772), which is exactly what pstore_console_offset
	 * already floored into `cap`: derive the extent from it so the wrap
	 * check, the containment gate and the store bound all match the
	 * kernel's zone for non-pow2 console-size too (review D4). */
	uint64_t zs = (uint64_t)cap + PSTORE_HDR;
	if (ro.base + off < ro.base || ro.base + off + zs < ro.base + off) {
		status("zone address wraps; skipped");
		return 0;
	}
	uint64_t zb = ro.base + off;

	/* ---- safety gates: fail safe means print and skip ---- */

	struct fdt_memmap mm;
	if (!fdt_collect_memmap(fdt, &mm)) {
		status("FDT memmap walk failed; skipped");
		return 0;
	}
	if (mm.n_reserved_dropped) {
		/* A truncated reserved table could hide a range that overlaps
		 * the zone (or the real containing range); fail closed. */
		char line[RAMLOG_LINE_MAX];
		unsigned p = put_txt(line, sizeof(line), 0, "reserved overflow +");
		p = put_dec(line, sizeof(line), p, mm.n_reserved_dropped);
		p = put_txt(line, sizeof(line), p, "; skipped");
		line[p] = 0;
		status(line);
		return 0;
	}

	int containing = -1;
	for (unsigned i = 0; i < mm.n_reserved; i++)
		if (range_covers(zb, zs, mm.reserved[i].base, mm.reserved[i].size)) {
			containing = (int)i;
			break;
		}
	if (containing < 0) {
		status("zone NOT inside any FDT reserved range; skipped");
		return 0;
	}
	{
		char line[RAMLOG_LINE_MAX];
		unsigned p = put_txt(line, sizeof(line), 0, "zone inside reserved: ");
		p = put_txt(line, sizeof(line), p, mm.reserved[containing].name);
		line[p] = 0;
		status(line);
	}

	if (used_fallback) {
		/* The fallback address is a historical guess, so only the
		 * genuine GS101 rmem window may host it: the containing
		 * reservation must start exactly at FALLBACK_BASE and span at
		 * least FALLBACK_SIZE, whatever its node name. A range that
		 * merely covers the address (different base / smaller size)
		 * could be any driver's carve-out - skip. Only the
		 * exact FALLBACK_BASE/FALLBACK_SIZE window is accepted. */
		if (mm.reserved[containing].base != FALLBACK_BASE ||
		    mm.reserved[containing].size < FALLBACK_SIZE) {
			status("fallback not in GS101 window; skipped");
			return 0;
		}
		status("fallback in GS101 window (UNVERIFIED)");
	}

	for (unsigned i = 0; i < mm.n_reserved; i++) {
		if ((int)i == containing)
			continue;
		if (range_overlaps(zb, zs, mm.reserved[i].base, mm.reserved[i].size)) {
			char line[RAMLOG_LINE_MAX];
			unsigned p = put_txt(line, sizeof(line), 0, "overlaps reserved ");
			p = put_txt(line, sizeof(line), p, mm.reserved[i].name);
			p = put_txt(line, sizeof(line), p, "; skipped");
			line[p] = 0;
			status(line);
			return 0;
		}
	}

	/* Image range covers the boot and secondary stacks (see _head/_end
	 * note above). */
	uint64_t img_lo = (uint64_t)(uintptr_t)_head;
	uint64_t img_hi = (uint64_t)(uintptr_t)_end;
	if (img_hi > img_lo && range_overlaps(zb, zs, img_lo, img_hi - img_lo)) {
		status("overlaps shim image+stacks; skipped");
		return 0;
	}

	uint32_t fdtsz = fdt_totalsize(fdt);
	if (fdtsz && range_overlaps(zb, zs, (uint64_t)fdt, fdtsz)) {
		status("overlaps FDT blob; skipped");
		return 0;
	}

	uint64_t ird_s = 0, ird_e = 0;
	if (fdt_chosen_u64(fdt, "linux,initrd-start", &ird_s) &&
	    fdt_chosen_u64(fdt, "linux,initrd-end", &ird_e) && ird_e > ird_s &&
	    range_overlaps(zb, zs, ird_s, ird_e - ird_s)) {
		status("overlaps initrd; skipped");
		return 0;
	}

	zone_base = zb;
	zone_mem = (volatile uint8_t *)(uintptr_t)zb;
	zone_size = (uint32_t)zs;
	zone_cap = cap;
	ready = 1;
	status_hex2("zone/cap", zb, cap);
	status("write enabled");
	return 1;
}

/* Append bytes of one segment at data offset *dst, dropping the first
 * `drop` bytes of THIS segment (tail-keep); every store is gated by
 * zone_put8's runtime compare. */
static void stream(const char *s, unsigned len, unsigned *dst, unsigned drop)
{
	for (unsigned i = 0; i < len; i++) {
		if (i < drop)
			continue;
		zone_put8(PSTORE_HDR + *dst, (uint8_t)s[i]);
		(*dst)++;
	}
}

void pstore_save(void)
{
	if (!ready)
		return;
	if (!save_lock()) {
		status("save lock busy; skipped");
		return;
	}

	/* Clear any previous signature first: a torn write must never
	 * present a mix of old and new data under a valid signature. */
	zone_put32(0, 0);
	dsb_sy();

	unsigned marker_len = 0;
	/* bounded: pstore_marker is a string literal, ends at its NUL */
	while (pstore_marker[marker_len])
		marker_len++;

	/* Pass 1 (bounded by RAMLOG_SIZE): total payload size. */
	unsigned total = marker_len + 1;
	unsigned lines = ramlog_lines();
	char buf[RAMLOG_LINE_MAX];
	for (unsigned i = 0; i < lines; i++)
		total += ramlog_copy_line(i, buf, sizeof(buf)) + 1;

	unsigned skip = total > zone_cap ? total - zone_cap : 0;

	static const char trunc_msg[] = "[...log truncated...]";
	const unsigned trunc_len = (unsigned)sizeof(trunc_msg) - 1;
	unsigned dst = 0, drop = skip;
	if (skip) {
		/* The retained tail starts mid-line; say so right after the
		 * marker. The indicator line is paid for from the same
		 * head-drop budget, so written still lands exactly on the cap
		 * (review N7). */
		drop += trunc_len + 1;
	}

	/* Pass 2: write the surviving bytes. The marker line is kept as
	 * the FIXED first line so pstore-pull.py can identify the build
	 * even when the log outgrew the zone; tail-keep then drops the
	 * oldest bytes of the log payload (the retained part may start
	 * mid-line, like the kernel's own head-dropping write). */
	stream(pstore_marker, marker_len, &dst, 0);
	stream("\n", 1, &dst, 0);
	if (skip) {
		stream(trunc_msg, trunc_len, &dst, 0);
		stream("\n", 1, &dst, 0);
	}
	for (unsigned i = 0; i < lines; i++) {
		unsigned len = ramlog_copy_line(i, buf, sizeof(buf));
		unsigned d = drop > len ? len : drop;
		stream(buf, len, &dst, d);
		drop -= d;
		d = drop > 1 ? 1 : drop;
		stream("\n", 1, &dst, d);
		drop -= d;
	}
	unsigned written = dst;

	/* Pass 1 and pass 2 read the lock-free ramlog; another CPU can log
	 * in between, so pass 2 can outgrow pass 1's budget and dst can run
	 * past the cap (the stores themselves stayed bounded by zone_put8).
	 * The recorded size must stay within the cap or the kernel drops the
	 * whole buffer (size > buffer_size, review D5). */
	if (written > zone_cap)
		written = zone_cap;

	/* Data is out (Device stores are ordered); barrier, then size and
	 * start, then the signature last, then barrier. */
	dsb_sy();
	zone_put32(8, written);		/* size */
	zone_put32(4, 0);		/* start */
	zone_put32(0, PSTORE_SIG);	/* sig, last */
	dsb_sy();

	save_unlock();

	/* Report into the console log (visible on the pager and inside the
	 * NEXT save); `written` counts exactly the bytes stored above. */
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "saved bytes=");
	p = put_dec(line, sizeof(line), p, written);
	p = put_txt(line, sizeof(line), p, " zone=");
	p = put_hex(line, sizeof(line), p, zone_base, 16);
	line[p] = 0;
	status(line);
}

int pstore_ready(void)
{
	return ready;
}

uint64_t pstore_zone_addr(void)
{
	return zone_base;
}

uint64_t pstore_zone_size(void)
{
	return zone_size;	/* 0 unless pstore_init armed the zone */
}

int pstore_used_fallback(void)
{
	return used_fallback;
}

void pstore_reset(void)
{
	zone_mem = 0;
	zone_size = 0;
	zone_cap = 0;
	zone_base = 0;
	ready = 0;
	used_fallback = 0;
	save_owner = 0;
	panic_save_cpu = 0;
}
