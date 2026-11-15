/* Host unit tests for the pstore/ramoops hand-back: FDT parsing (node
 * present, absent, missing optional props), kernel-parity zone math, the
 * on-disk persistent_ram_buffer encoding, tail-keep, and the safety
 * gates. Build (also run by shim/build.sh):
 *   gcc -O1 -Wall -Wextra -I. -o out/pstore_host_test \
 *       fdt.c pstore.c pstore_host_test.c
 * (ramlog.c is NOT linked: the controllable stand-in below replaces it
 * for the pass-1/pass-2 divergence test; the real ring keeps its own
 * ramlog_host_test.) */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fdt.h"
#include "fdt_test_builder.h"
#include "pstore.h"
#include "ramlog.h"

#define con_lines_buf char con_log[16384]
con_lines_buf;
static unsigned con_log_len;

/* D1 test hook: while set, the first "saved bytes=" status (printed
 * from inside pstore_save, before pstore_panic_save returns) triggers
 * a nested pstore_panic_save() on the same CPU - exactly what
 * exc_handler would do if the save itself faulted. */
static int d1_hook_active, d1_nested_ran;

void con_str(const char *label, const char *s)
{
	unsigned n = strlen(label) + 1 + strlen(s) + 1;
	if (con_log_len + n + 1 < sizeof(con_log)) {
		con_log_len += (unsigned)snprintf(con_log + con_log_len, n + 1, "%s %s\n", label, s);
	}
	if (d1_hook_active && !d1_nested_ran && strstr(s, "saved bytes=")) {
		d1_nested_ran = 1;
		pstore_panic_save();
	}
}
void con_hex(const char *label, uint64_t v) { (void)label; (void)v; }

/* Host stand-ins for the linker symbols pstore.c gates against: an
 * empty image range ([x, x)) so the image-overlap gate is a no-op here
 * (the FDT-overlap test exercises the same code branch instead). */
char _head[1], _end[1];

/* ---- controllable ramlog stand-in ----
 * pstore_save reads the lock-free ramlog twice; on the device another
 * CPU can log between the passes. A single-threaded host cannot do
 * that against the real ring, so this binary links a line-array
 * stand-in whose ramlog_copy_line can report MORE bytes for a line's
 * second read than for its first - exactly the pass-1/pass-2
 * divergence. Set fake_grow (after ramlog_reset) to enable it. */
#define FAKE_MAX_LINES 1024
static char fake_lines[FAKE_MAX_LINES][RAMLOG_LINE_MAX];
static unsigned fake_n;
static unsigned fake_calls[FAKE_MAX_LINES];
static unsigned fake_grow;	/* extra bytes every 2nd copy_line reports */

void ramlog_reset(void)
{
	fake_n = 0;
	fake_grow = 0;
	memset(fake_calls, 0, sizeof(fake_calls));
}

void ramlog_line(const char *line)
{
	if (fake_n == FAKE_MAX_LINES) {
		memmove(fake_lines[0], fake_lines[1],
			sizeof(fake_lines) - sizeof(fake_lines[0]));
		fake_n--;
	}
	unsigned n = 0;
	while (line[n] && n + 1 < RAMLOG_LINE_MAX)
		n++;
	memcpy(fake_lines[fake_n], line, n);
	fake_lines[fake_n][n] = 0;
	fake_n++;
}

unsigned ramlog_lines(void)
{
	return fake_n;
}

unsigned ramlog_copy_line(unsigned index, char *dst, unsigned cap)
{
	if (!cap)
		return 0;
	if (index >= fake_n) {
		dst[0] = 0;
		return 0;
	}
	unsigned n = (unsigned)strlen(fake_lines[index]);
	unsigned extra = (++fake_calls[index] == 2) ? fake_grow : 0;
	unsigned out = 0;
	for (unsigned i = 0; i < n && out + 1 < cap; i++)
		dst[out++] = fake_lines[index][i];
	for (unsigned i = 0; i < extra && out + 1 < cap; i++)
		dst[out++] = 'X';
	dst[out] = 0;
	return out;
}

static void con_log_reset(void)
{
	con_log_len = 0;
	con_log[0] = 0;
}

/* Builds a GS101-shaped tree: /reserved-memory { ramoops@<base> } with
 * console/pmsg/record/ecc per the caller, reg pointing at `base`. */
static uintptr_t build_ramoops_fdt(struct fdt_builder *b, uint64_t base,
				   uint32_t region, uint32_t console,
				   uint32_t pmsg, uint32_t record,
				   uint32_t ecc, int with_compat)
{
	fb_init(b);
	fb_begin_node(b, "");
	fb_prop_u32(b, "#address-cells", 2);
	fb_prop_u32(b, "#size-cells", 1);
	fb_begin_node(b, "reserved-memory");
	fb_prop_u32(b, "#address-cells", 2);
	fb_prop_u32(b, "#size-cells", 1);
	fb_begin_node(b, "ramoops_mem@fd3ff000");
	if (with_compat)
		fb_prop(b, "compatible", "ramoops", 8);
	fb_reg21(b, base, region);
	if (console)
		fb_prop_u32(b, "console-size", console);
	if (pmsg)
		fb_prop_u32(b, "pmsg-size", pmsg);
	if (record)
		fb_prop_u32(b, "record-size", record);
	if (ecc)
		fb_prop_u32(b, "ecc-size", ecc);
	fb_end_node(b);
	fb_end_node(b);
	fb_end_node(b);
	return fb_finish(b);
}

/* ---- kernel-parity zone math ---- */

static void test_offset_math(void)
{
	uint32_t cap = 0;

	/* GS101 gs101-rmem.dtsi shape: 4 MiB region, 2 MiB console, 2 MiB
	 * pmsg, no record-size -> dump_mem_sz == 0 -> console at base+0. */
	assert(pstore_console_offset(0x400000, 0, 0x200000, 0, 0x200000, &cap) == 0);
	assert(cap == 0x200000 - 12);

	/* Task fallback: no pmsg -> dump 2 MiB, record 0 -> no advance. */
	assert(pstore_console_offset(0x400000, 0, 0x200000, 0, 0, &cap) == 0);
	assert(cap == 0x200000 - 12);

	/* dump split into record chunks: console lands at base+dump. */
	assert(pstore_console_offset(0x400000, 0x1000, 0x200000, 0, 0, &cap) == 0x200000);
	assert(pstore_console_offset(0x400000, 0x1000, 0x100000, 0, 0, &cap) == 0x300000);

	/* Rounding residue: dump 517, record 512 (pow2) -> cnt 1, zone_sz
	 * ALIGN_DOWN(517,2)=516 -> console at +516, not +517. */
	assert(pstore_console_offset(1541, 0x200, 0x400, 0, 0, &cap) == 516);
	assert(cap == 0x400 - 12);

	/* Non-pow2 console-size rounds down to 0x100000 (ram.c 766-772). */
	assert(pstore_console_offset(0x400000, 0, 0x180000, 0, 0, &cap) == 0);
	assert(cap == 0x100000 - 12);

	/* console+ftrace+pmsg > region: kernel probe fails -> refuse. */
	assert(pstore_console_offset(0x400000, 0, 0x400000, 0, 0x1000, &cap) == UINT64_MAX);

	/* record_size > dump_mem_sz: cnt == 0 -> kernel probe fails. */
	assert(pstore_console_offset(0x400000, 0x400000, 0x200000, 0, 0, &cap) == UINT64_MAX);

	/* console 0: the kernel probes but creates no console prz. */
	assert(pstore_console_offset(0x400000, 0x1000, 0, 0, 0, &cap) == UINT64_MAX);
}

/* ---- FDT parsing: present / absent / missing optional props ---- */

static void test_fdt_ramoops_present(void)
{
	struct fdt_builder b;
	uintptr_t fdt = build_ramoops_fdt(&b, 0xfd3ff000, 0x400000, 0x200000,
					  0x200000, 0, 0, 1);
	struct fdt_ramoops ro;
	assert(fdt_ramoops(fdt, &ro) == 1);
	assert(ro.found == 1);
	assert(ro.base == 0xfd3ff000);
	assert(ro.size == 0x400000);
	assert(ro.console_size == 0x200000);
	assert(ro.pmsg_size == 0x200000);
	assert(ro.record_size == 0);	/* absent -> 0 */
	assert(ro.ecc_size == 0);
	assert(ro.mem_type == 0);
}

static void test_fdt_ramoops_absent(void)
{
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_begin_node(&b, "some_other@90000000");
	fb_reg21(&b, 0x90000000, 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	struct fdt_ramoops ro;
	assert(fdt_ramoops(fdt, &ro) == 0);
	assert(ro.found == 0);
	assert(ro.qualified_no_reg == 0);
}

static void test_fdt_ramoops_missing_optional_props(void)
{
	struct fdt_builder b;
	/* Name-only match (no compatible), only console-size present. */
	uintptr_t fdt = build_ramoops_fdt(&b, 0xfd3ff000, 0x400000, 0x200000,
					  0, 0, 0, 0);
	struct fdt_ramoops ro;
	assert(fdt_ramoops(fdt, &ro) == 1);
	assert(ro.found == 1);
	assert(ro.console_size == 0x200000);
	assert(ro.pmsg_size == 0 && ro.record_size == 0 && ro.ecc_size == 0);
}

static void test_fdt_ramoops_no_reg(void)
{
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_begin_node(&b, "reserved-memory");
	fb_begin_node(&b, "ramoops@fd3ff000");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	struct fdt_ramoops ro;
	assert(fdt_ramoops(fdt, &ro) == 0);
	assert(ro.found == 0);
	assert(ro.qualified_no_reg == 1);
}

/* ---- pstore_init + pstore_save end to end against a fake zone ---- */

static uint32_t rd32le(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void test_save_into_fake_zone(void)
{
	uint8_t *zone = malloc(0x4000);
	memset(zone, 0xee, 0x4000);
	struct fdt_builder b;
	/* console 0x2000 + pmsg 0x2000 fill the 0x4000 region: console at
	 * offset 0 (the expected live-bluejay layout). */
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x4000);
	fb_prop_u32(&b, "console-size", 0x2000);
	fb_prop_u32(&b, "pmsg-size", 0x2000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 1);
	assert(pstore_ready() == 1);
	assert(pstore_used_fallback() == 0);
	assert(pstore_zone_addr() == (uint64_t)(uintptr_t)zone);

	ramlog_reset();
	ramlog_line("SECTION boot CPU");
	ramlog_line("CPU MIDR id=0 value=414fd0b1");
	pstore_save();

	/* persistent_ram_buffer encoding: sig 'DBGC' LE, start 0, size len. */
	assert(rd32le(zone + 0) == 0x43474244u);
	assert(rd32le(zone + 4) == 0);
	unsigned payload_len = rd32le(zone + 8);
	unsigned marker_len = (unsigned)strlen(pstore_marker);
	const char *l0 = "SECTION boot CPU";
	const char *l1 = "CPU MIDR id=0 value=414fd0b1";
	unsigned expect = marker_len + 1 + (unsigned)strlen(l0) + 1 + (unsigned)strlen(l1) + 1;
	assert(payload_len == expect);
	assert(memcmp(zone + 12, pstore_marker, marker_len) == 0);
	assert(zone[12 + marker_len] == '\n');
	assert(memcmp(zone + 12 + marker_len + 1, l0, strlen(l0)) == 0);
	assert(zone[12 + marker_len + 1 + strlen(l0)] == '\n');
	assert(memcmp(zone + 12 + marker_len + 1 + strlen(l0) + 1, l1, strlen(l1)) == 0);
	assert(zone[12 + payload_len - 1] == '\n');
	/* Nothing past the payload was written (zone_size cap holds). */
	assert(zone[12 + payload_len] == 0xee);

	/* The report line reached the console. */
	assert(strstr(con_log, "PSTORE saved bytes=") != NULL);
	free(zone);
}

static void test_save_tail_keep(void)
{
	/* console-size 0x1000 -> data cap 0xff4; append more than that. */
	uint8_t *zone = malloc(0x1000 + 8);
	memset(zone, 0xee, 0x1000 + 8);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	assert(pstore_init(fdt) == 1);
	ramlog_reset();
	unsigned marker_len = (unsigned)strlen(pstore_marker);
	char expected[RAMLOG_SIZE + RAMLOG_LINE_MAX];
	unsigned e = 0;
	memcpy(expected + e, pstore_marker, marker_len);
	e += marker_len;
	expected[e++] = '\n';
	for (unsigned i = 0; i < 120; i++) {
		char line[RAMLOG_LINE_MAX];
		int n = snprintf(line, sizeof(line), "L%04u padding-padding-padding-padding", i);
		ramlog_line(line);
		memcpy(expected + e, line, (unsigned)n);
		e += (unsigned)n;
		expected[e++] = '\n';
	}
	assert(e > 0xff4);
	pstore_save();

	assert(rd32le(zone + 0) == 0x43474244u);
	assert(rd32le(zone + 4) == 0);
	assert(rd32le(zone + 8) == 0xff4);		/* size == capacity */
	/* D8: the marker line survives tail-keep as the fixed first line,
	 * followed by the newest (cap - marker - 1) payload bytes (which
	 * may start mid-line, mirroring the kernel's head-dropping).
	 * N7: a truncation indicator line sits right after the marker. */
	assert(memcmp(zone + 12, pstore_marker, marker_len) == 0);
	assert(zone[12 + marker_len] == '\n');
	const char *trunc = "[...log truncated...]";
	unsigned tlen = (unsigned)strlen(trunc);
	assert(zone[12 + marker_len + 1] == '[');
	assert(memcmp(zone + 12 + marker_len + 1, trunc, tlen) == 0);
	assert(zone[12 + marker_len + 1 + tlen] == '\n');
	unsigned keep = 0xff4 - marker_len - 1 - (tlen + 1);
	assert(memcmp(zone + 12 + marker_len + 1 + tlen + 1, expected + (e - keep), keep) == 0);
	/* Nothing past the payload was written (zone_size cap holds). The payload
	 * ends at the zone end (12 + 0xff4 == 0x1000), so the guard byte is in the
	 * extra 8 bytes allocated above. */
	assert(zone[0x1000] == 0xee);
	free(zone);
}

static void test_zone_bound_uses_floored_console_size(void)
{
	/* D4: the kernel floors console-size to a power of two, and the
	 * store bound must use the same floored extent. Region 0x2800 with
	 * console-size 0x3000: the real zone is 0x2000, which the 0x2800
	 * reservation covers - the UNfloored 0x3000 extent it does not,
	 * so with the old raw bound init refused this geometry. */
	uint8_t *zone = malloc(0x2800);
	memset(zone, 0xee, 0x2800);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x2800);
	fb_prop_u32(&b, "console-size", 0x3000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	assert(pstore_init(fdt) == 1);	/* floored 0x2000 fits in 0x2800 */
	assert(pstore_ready() == 1);
	ramlog_reset();
	ramlog_line("D4 non-pow2 console bound");
	pstore_save();
	assert(rd32le(zone + 0) == 0x43474244u);
	assert(rd32le(zone + 8) > 0);
	/* Nothing at or past the floored 0x2000 bound was stored. */
	assert(zone[0x2000] == 0xee);
	assert(zone[0x2000 + 12] == 0xee);
	free(zone);
}

static void test_save_clamps_divergent_pass_size_to_cap(void)
{
	/* D5: pass 1 and pass 2 read the ramlog without a lock; another CPU
	 * logging in between makes pass 2 outgrow pass 1's budget. The
	 * stores stay bounded by zone_put8, but the recorded size must also
	 * stay <= cap or the kernel drops the whole buffer. */
	uint8_t *zone = malloc(0x1004);
	memset(zone, 0xee, 0x1004);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	assert(pstore_init(fdt) == 1);
	ramlog_reset();
	/* Fill to just under the cap (marker + padded lines), then make
	 * every line report more bytes on its second read (pass 2). */
	unsigned marker_len = (unsigned)strlen(pstore_marker);
	char line[RAMLOG_LINE_MAX];
	memset(line, 'F', 40);
	line[40] = 0;
	unsigned total = marker_len + 1;
	while (total + 41 <= 0xff4 - 8) {
		ramlog_line(line);
		total += 41;
	}
	fake_grow = 80;
	pstore_save();

	assert(rd32le(zone + 0) == 0x43474244u);
	assert(rd32le(zone + 4) == 0);
	assert(rd32le(zone + 8) <= 0xff4);	/* clamped to the cap */
	assert(zone[0x1000] == 0xee);		/* stores never left the zone */
	free(zone);
}

static void test_memreserve_overlap_refused(void)
{
	/* D6: the /memreserve/ block reserves memory like a reserved-memory
	 * child; a zone landing on such an entry must be refused. */
	uint8_t *zone = malloc(0x1000);
	memset(zone, 0xee, 0x1000);
	struct fdt_builder b;
	fb_init(&b);
	fb_memreserve(&b, (uint64_t)(uintptr_t)zone, 0x800);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "overlaps reserved /memreserve") != NULL);
	free(zone);
}

static void test_wrapped_reserved_entry_refused(void)
{
	/* D6: a reserved neighbour whose base+size wraps the u64 address
	 * space once slipped past the overlap check (b2+s2 wrapped small);
	 * a wrapped extent must read as overlapping, fail closed. */
	uint8_t *zone = malloc(0x1000);
	memset(zone, 0xee, 0x1000);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_begin_node(&b, "neighbor@wrap");
	fb_reg21(&b, 0xfffffffffffff000ULL, 0x2000);	/* base+size wraps */
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "overlaps reserved neighbor@wrap") != NULL);
	free(zone);
}

static void test_fdt_ramoops_no_sibling_leak(void)
{
	/* D9: an unqualified sibling's props must never leak into the
	 * qualified node's result: every child starts from clean fields. */
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "sibling@90000000");
	fb_prop_u32(&b, "console-size", 0x123000);
	fb_prop_u32(&b, "ecc-size", 64);
	fb_reg21(&b, 0x90000000, 0x1000);
	fb_end_node(&b);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, 0xfd3ff000, 0x400000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	struct fdt_ramoops ro;
	assert(fdt_ramoops(fdt, &ro) == 1);
	assert(ro.found == 1);
	assert(ro.base == 0xfd3ff000);
	assert(ro.size == 0x400000);
	assert(ro.console_size == 0);	/* the sibling value leaked before D9 */
	assert(ro.ecc_size == 0);
}

static void test_reserved_bad_cells_fail_closed(void)
{
	/* N5: a reserved child whose cells the walker cannot decode used
	 * to vanish uncounted; it now counts as a drop, so init fails
	 * closed like any table overflow. */
	uint8_t *zone = malloc(0x1000);
	memset(zone, 0xee, 0x1000);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_begin_node(&b, "reserved-memory");	/* 3 addr cells: undecodable */
	fb_prop_u32(&b, "#address-cells", 3);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "odd@cells");
	fb_reg21(&b, 0x80000000, 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "reserved overflow") != NULL);
	free(zone);
}

static void test_reserved_partial_tuple_fail_closed(void)
{
	/* N5: a reg with a partial trailing tuple (len % stride) also
	 * counts as a drop now. */
	uint8_t *zone = malloc(0x1000);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_begin_node(&b, "ragged@tuple");
	uint8_t ragged[14] = { 0, 0, 0, 0, 0x80, 0, 0, 0, 0, 0, 0x10, 0, 0x55, 0x55 };
	fb_prop(&b, "reg", ragged, sizeof(ragged));
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(strstr(con_log, "reserved overflow") != NULL);
	free(zone);
}

static void test_panic_save_recursion_guard(void)
{
	/* D1: a fault inside the panic-path save re-enters exc_handler on
	 * the same CPU; the nested pstore_panic_save() must skip (one
	 * printed line) instead of running another full save. */
	uint8_t *zone = malloc(0x4000);
	memset(zone, 0xee, 0x4000);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x4000);
	fb_prop_u32(&b, "console-size", 0x2000);
	fb_prop_u32(&b, "pmsg-size", 0x2000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	assert(pstore_init(fdt) == 1);
	assert(pstore_ready() == 1);
	ramlog_reset();
	ramlog_line("PANIC SYNC");
	ramlog_line("STAGE bluejay probe");

	con_log_reset();
	d1_hook_active = 1;
	d1_nested_ran = 0;
	pstore_panic_save();
	d1_hook_active = 0;

	/* The nested same-CPU entry happened and was skipped with one
	 * line - not a second full save. */
	assert(d1_nested_ran == 1);
	assert(strstr(con_log, "PSTORE panic save re-entered; skipped") != NULL);
	const char *first = strstr(con_log, "PSTORE saved bytes=");
	assert(first != NULL);
	assert(strstr(first + 1, "PSTORE saved bytes=") == NULL);

	/* The outer save completed: valid buffer header in the zone. */
	assert(rd32le(zone + 0) == 0x43474244u);
	assert(rd32le(zone + 4) == 0);
	assert(rd32le(zone + 8) > 0);

	/* The guard re-arms after a completed panic save: a NEW panic on
	 * the same CPU gets one fresh save attempt. */
	con_log_reset();
	pstore_panic_save();
	assert(strstr(con_log, "PSTORE saved bytes=") != NULL);
	free(zone);
}

static void test_node_absent_fallback_refused(void)
{
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_begin_node(&b, "unrelated@80000000");
	fb_reg21(&b, 0x80000000, 0x100000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_used_fallback() == 1);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "fallback") != NULL);
	assert(strstr(con_log, "skipped") != NULL);
	/* Saving after a refused init is a no-op, not a crash. */
	pstore_save();
}

static void test_reserved_truncation_refused(void)
{
	/* D2: a usable ramoops node at a real zone, but more reserved
	 * children than the 16-entry table holds. The dropped ranges are
	 * invisible to the overlap gate, so init must fail closed. */
	uint8_t *zone = malloc(0x1000);
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "ramoops@zone");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)zone, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	/* FDT_MAX_RESERVED+4 non-overlapping fillers: > FDT_MAX_RESERVED. */
	for (int i = 0; i < FDT_MAX_RESERVED + 4; i++) {
		char name[24];
		snprintf(name, sizeof(name), "filler%d@far", i);
		fb_begin_node(&b, name);
		fb_reg21(&b, 0x10000000ULL + (uint64_t)i * 0x10000ULL, 0x1000);
		fb_end_node(&b);
	}
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "reserved overflow") != NULL);
	free(zone);
}

static void test_fallback_foreign_range_refused(void)
{
	/* D3: no ramoops node, but a big reserved range that CONTAINS the
	 * fallback zone with a different base. Old code accepted this
	 * (containment + no-overlap were its only gates); the fallback must
	 * only ever land in the exact GS101 rmem window. */
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "vendor_carveout@fd000000");
	fb_reg21(&b, 0xfd000000ULL, 0x1000000);	/* covers 0xfd3ff000 + 2 MiB */
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_used_fallback() == 1);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "fallback not in GS101 window; skipped") != NULL);
	/* Saving after a refused init is a no-op, not a crash. */
	pstore_save();
}

static void test_fallback_gs101_window_accepted(void)
{
	/* The exact GS101 rmem window (base 0xfd3ff000, size >= 4 MiB),
	 * node name irrelevant: the fallback may arm, loudly. No save is
	 * attempted here - the zone address is real MMIO on target. */
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "rmem@fd3ff000");
	fb_reg21(&b, 0xfd3ff000ULL, 0x400000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 1);
	assert(pstore_used_fallback() == 1);
	assert(pstore_ready() == 1);
	assert(pstore_zone_addr() == 0xfd3ff000ULL);
	assert(strstr(con_log, "fallback in GS101 window (UNVERIFIED)") != NULL);
}

static void test_ecc_refused(void)
{
	uint8_t *zone = malloc(0x4000);
	struct fdt_builder b;
	uintptr_t fdt = build_ramoops_fdt(&b, (uintptr_t)zone, 0x4000,
					  0x2000, 0, 0, 16, 1);
	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	/* The exact contract line from the requirements. */
	assert(strstr(con_log, "PSTORE: ecc enabled, skipped") != NULL);
	free(zone);
}

static void test_zone_overlapping_fdt_refused(void)
{
	struct fdt_builder b;
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	/* ramoops reg points straight at the FDT blob itself: containment
	 * passes (this node is a reserved range), the blob-overlap gate
	 * must refuse. */
	fb_begin_node(&b, "ramoops@self");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)b.buf, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "overlaps FDT blob") != NULL);
}

static void test_zone_overlapping_neighbor_refused(void)
{
	uint8_t *scratch = malloc(0x4000);
	struct fdt_builder b;
	/* The zone is contained by its own ramoops reservation, but a
	 * second reserved range overlaps it: the other-reserved-range gate
	 * must refuse the write. */
	fb_init(&b);
	fb_begin_node(&b, "");
	fb_prop_u32(&b, "#address-cells", 2);
	fb_prop_u32(&b, "#size-cells", 1);
	fb_begin_node(&b, "reserved-memory");
	fb_begin_node(&b, "ramoops@far");
	fb_prop(&b, "compatible", "ramoops", 8);
	fb_reg21(&b, (uint64_t)(uintptr_t)scratch, 0x1000);
	fb_prop_u32(&b, "console-size", 0x1000);
	fb_end_node(&b);
	fb_begin_node(&b, "neighbor@overlap");
	fb_reg21(&b, (uint64_t)(uintptr_t)scratch, 0x800);
	fb_end_node(&b);
	fb_end_node(&b);
	fb_end_node(&b);
	uintptr_t fdt = fb_finish(&b);

	con_log_reset();
	assert(pstore_init(fdt) == 0);
	assert(pstore_ready() == 0);
	assert(strstr(con_log, "overlaps reserved neighbor@overlap") != NULL);
	free(scratch);
}

int main(void)
{
	test_offset_math();
	test_fdt_ramoops_present();
	test_fdt_ramoops_absent();
	test_fdt_ramoops_missing_optional_props();
	test_fdt_ramoops_no_reg();
	test_fdt_ramoops_no_sibling_leak();
	test_save_into_fake_zone();
	test_save_tail_keep();
	test_zone_bound_uses_floored_console_size();
	test_save_clamps_divergent_pass_size_to_cap();
	test_memreserve_overlap_refused();
	test_wrapped_reserved_entry_refused();
	test_reserved_bad_cells_fail_closed();
	test_reserved_partial_tuple_fail_closed();
	test_panic_save_recursion_guard();
	test_node_absent_fallback_refused();
	test_fallback_foreign_range_refused();
	test_fallback_gs101_window_accepted();
	test_reserved_truncation_refused();
	test_ecc_refused();
	test_zone_overlapping_fdt_refused();
	test_zone_overlapping_neighbor_refused();
	printf("PASS pstore host test: fdt ramoops parse, zone math, encoding, tail-keep, safety gates\n");
	return 0;
}
