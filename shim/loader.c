/*
 * Milestone 4: act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ABL ramdisk, copy the
 * segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU
 * and caches off, x0 = boot_args (physical).
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "loader.h"
#ifdef TOUCH_DVFS_WRITE
#include "touchprobe.h"
#endif
#include "mifclk.h"
#include "sweetprobe.h"
#include "pmicsrc.h"
#include "payload.h"
#include "shim_assert.h"
#ifdef EL2_RESIDENT
#include "el2.h"
extern char _head[], _end[];
extern volatile uint64_t probe_entry_el;
#endif

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

#ifdef PMIC_SRC
/* --pmic-src: append the sweetps10/sweetps11/sweetpsrc tokens (pmicsrc.h) to the XNU command
 * line, after the sweetmif* ones. Never truncated: no room = nothing appended and a console
 * line (the host guard tools/resident_core.py line_limit keeps the line short enough, so this
 * is the second fence). */
static void pmic_src_append(volatile char *cmd, uint32_t cap)
{
	uint32_t n = 0, len = 0, i;

	SHIM_ASSERT(cmd != 0);
	SHIM_ASSERT(cap > 0u);
	if (!pmic_src_tok[0]) {
		con_str("PMIC SRC",
			"GAP: no tokens to append (pmic_src did not run), XNU gets no sweetps*");
		return;
	}
	while (len < PMIC_SRC_TOK_MAX && pmic_src_tok[len])
		len++;
	while (n + 1 < cap && cmd[n])
		n++;
	if (n + len >= cap) {
		con_str("PMIC SRC",
			"GAP: no room in the XNU command line for the sweetps* tokens, nothing appended");
		return;
	}
	for (i = 0; i < len; i++)
		cmd[n++] = pmic_src_tok[i];
	cmd[n] = 0;
	con_str("PMIC SRC", "tokens appended to the XNU command line");
}
#endif

#ifdef TOUCH_DVFS_WRITE
/* --dvfs (xnu patch 0367, tracker L-28): append the CPU DVFS tokens
 * (touchprobe.h touch_dvfs_tokens: asked / read before / read back / result)
 * to the XNU command line, after every other shim token, so the USB log can
 * prove a CPU-row boot. Never truncated: no room = nothing appended and a
 * console GAP (tools/build-xnu-image.sh refuses boot-args that leave less
 * than TOUCH_DVFS_TOK_MAX bytes: the second fence). */
static void dvfs_tok_append(volatile char *cmd, uint32_t cap)
{
	SHIM_ASSERT(cmd != 0);
	char tok[TOUCH_DVFS_TOK_MAX];

	SHIM_ASSERT(cap > 0u);
	switch (touch_dvfs_append(cmd, cap, touch_dvfs_tokens(&touch_dvfs_res_g, tok, sizeof(tok)))) {
	case 0:
		con_str("DVFS", "tokens appended to the XNU command line");
		break;
	case 1:
		con_str("DVFS", "GAP: no room in the XNU command line for the "
			"sweetdvfs* tokens, nothing appended");
		break;
	default:
		con_str("DVFS", "GAP: this build cannot read back the CPU DVFS result, "
			"XNU gets no sweetdvfs*");
		break;
	}
}
#endif

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

/* Copy one segment to its place, then wipe its source in the initrd: segments can hold the RAM-only
 * WLANCRED blob (tools/mkpayload.py --wlancred), which must not stay readable there. The ADT and
 * the header are not segment sources and stay. The source was checked by payload_segs_check. */
static void load_seg(const struct payload_seg *s, uintptr_t src, uintptr_t dst)
{
	uint64_t r = (s->file_size + 7) & ~7ULL;

	SHIM_ASSERT(r >= s->file_size);
	SHIM_ASSERT((src & 7) == 0 && (dst & 7) == 0);
	copy64(dst, src, s->file_size);
	zero64(src, r);
	if (s->vmsize > r)	/* Opus r1 F6: vmsize between file_size and r must not wrap */
		zero64(dst + r, s->vmsize - r);
}


/* Check, then load, every segment: the table, the ADT and each source must lie inside the initrd,
 * in order and disjoint, or nothing is copied or wiped (Opus r1 F1/F5,
 * docs/reviews/T-1142-loader-wipe-opus-r1.md). NULL = done, else why not (loud: rule 8). */
static const char *load_segs(const struct payload *p, uintptr_t payload_addr, uint64_t rd_end,
			     uint64_t pa_of_va_base)
{
	uint32_t bad_seg, i;
	const char *bad = payload_segs_check(p, rd_end - payload_addr, &bad_seg);

	if (bad) {
		if (bad_seg < p->nsegs) {
			con_hex2("SEGSRC refused", "i=", bad_seg, "off=", p->segs[bad_seg].file_off);
			con_hex("SEGSRC size", p->segs[bad_seg].file_size);
		}
		return bad;
	}
	SHIM_ASSERT(payload_addr + p->adt_off + p->adt_size <= rd_end);
	SHIM_ASSERT(p->nsegs <= (rd_end - payload_addr) / sizeof(struct payload_seg));
	SHIM_ASSERT(bad_seg == ~0U);
	for (i = 0; i < p->nsegs; i++)
		load_seg(&p->segs[i], payload_addr + p->segs[i].file_off,
			 p->segs[i].vmaddr + pa_of_va_base);
	con_hex2("SEGS", "n=", p->nsegs, "end=", p->kernel_end + pa_of_va_base);
	return 0;
}

const char *loader_boot(uintptr_t dtb)
{
	/* style-allow: func-len long linear boot-args sequence, one call added by T-5030 (--mif-clk) */
	/* style-allow: assert pre-existing loader sequence; every refusal returns a reason string */
	uint64_t rd_start, rd_end;

	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
		return "no initrd in /chosen";
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);

	uint64_t t_scan0, t_scan1, frq;	/* boot speed: CNTPCT around the payload search */
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t_scan0));
	uintptr_t payload_addr = 0;
	uint32_t tail_hit = 0;
#ifdef SHIM_PAYLOAD_SIZE
	payload_addr = shim_find_payload_tail(rd_start, rd_end, SHIM_PAYLOAD_SIZE, 64 * 1024);
	tail_hit = payload_addr != 0;
#endif
	if (!payload_addr)
		payload_addr = shim_find_magic(rd_start, rd_end, "SWEETXNU", 8);
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t_scan1));
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frq));
	uint32_t scan_ms = (uint32_t)(frq >= 1000 ? (t_scan1 - t_scan0) / (frq / 1000) : 0);
	con_hex2("SCAN", "ms=", scan_ms, "tail=", tail_hit);
	con_hex2("SCAN", "size=", (uint32_t)(rd_end - rd_start), "off=",
		 (uint32_t)(payload_addr ? payload_addr - rd_start : 0));
	if (!payload_addr)
		return "no SWEETXNU payload";
	if (payload_addr > rd_end || rd_end - payload_addr < sizeof(struct payload))
		return "truncated payload header";
	const struct payload *p = (const struct payload *)payload_addr;
	const char *bad = payload_check(p);
	if (bad) {
		con_hex2("PAYLOAD", "version=", p->version, "want=", PAYLOAD_VERSION);
		return bad;
	}
	if ((p->adt_off & 7) || (payload_addr & 7))
		return "bad payload header";

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", p->phys_base, "size=", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/* The payload must not overlap where the kernel goes. */
	if (payload_ranges_overlap(rd_start, rd_end, p->phys_base, p->phys_base + p->mem_size))
		return "ramdisk overlaps kernel memory";

#ifdef EL2_RESIDENT
	/* review el2-b2-report-r1 F3: stage 2 stays live and EL2 owns the vectors, tables and stack inside [_head, _end); copy64/zero64 or XNU would overwrite them. Checked, not inferred (rule 7). */
	if (payload_ranges_overlap((uint64_t)(uintptr_t)_head, (uint64_t)(uintptr_t)_end, p->phys_base, p->phys_base + p->mem_size)) {
		con_hex("EL2 GAP: image overlaps XNU RAM, _head", (uint64_t)(uintptr_t)_head);
		con_hex("EL2 GAP: image overlaps XNU RAM, _end", (uint64_t)(uintptr_t)_end);
		return "EL2 image inside XNU RAM";
	}
#endif
	bad = load_segs(p, payload_addr, rd_end, pa_of_va_base);
	if (bad)
		return bad;
	SHIM_ASSERT(p->nsegs <= (rd_end - payload_addr) / sizeof(p->segs[0]));
	SHIM_ASSERT(p->adt_off <= rd_end - payload_addr);
	/* style-allow: func-len legacy: 88 lines at trunk, 83 now (segment loop moved) */

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, payload_addr + p->adt_off, p->adt_size);
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
#ifdef EL2_RESIDENT
	uint64_t bootflags = payload_bootflags(1, el2_s2_on);
#else
	uint64_t bootflags = 0;
#endif
	payload_fill_boot_args(ba, p, top, adt_pa - pa_of_va_base, bootflags);	/* xnu reads deviceTreeP with the MMU on: a VA */
	/* --mif-clk (T-5030): MIF/INT GET + one MIF request, sweetmif* tokens; no-op otherwise */
	mif_clk_cmdline(ba->CommandLine, BOOT_LINE_LENGTH);
	/*
	 * --sweet-probe (xnu patch 0363, LOWCLK B1/B2): PSCI queries + SLC descriptors + one
	 * PT_VERSION, sweetprobe_* tokens; no-op otherwise
	 */
	sweet_probe_cmdline(ba->CommandLine, BOOT_LINE_LENGTH);
#ifdef PMIC_SRC
	/* --pmic-src: the reset-source values read at shim start, after the sweetmif* tokens */
	pmic_src_append(ba->CommandLine, BOOT_LINE_LENGTH);
#endif
#ifdef TOUCH_DVFS_WRITE
	/* --dvfs (xnu patch 0367, L-28): the CPU DVFS result, last on the line */
	dvfs_tok_append(ba->CommandLine, BOOT_LINE_LENGTH);
#endif
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	con_hex2("BOOTARG", "pa=", ba_pa, "top=", top);
#ifdef EL2_RESIDENT
	/* SPEC 3.4 (b) design item 2: what XNU is told and what EL2 looks like at the jump. No EL2 register is written on this path: stage 2, VBAR_EL2 and the EL2 stack stay live (item 1 (iii)). */
	con_hex("ENTRY EL", probe_entry_el);
	con_hex("EL2 IMG _head", (uint64_t)(uintptr_t)_head);
	con_hex("EL2 IMG _end", (uint64_t)(uintptr_t)_end);
	el2_print_report();		/* census + the S2 readbacks `EL2 S2 ON hcr/vtcr/vttbr` (+ the empty ring and RESULT traps=0 gaps=0) */
	el2_print_resident(bootflags);
#endif

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	con_hex("JUMP", entry_pa);
	con_str("XNU", "jumping (fbcon clears screen)");

	/* Everything was written with the MMU and caches off; drop any stale I-cache lines too. */
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
	return "xnu returned";
}
