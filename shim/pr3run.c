/* PR-3 marker probe (docs/research/pr3-marker-run-design.md section 5): per site read the 64 B
 * marker, log its state and counter, write the next one. Accesses are 32-bit
 * try_read32/try_write32 (pr3marker.c); a failed access = "fault". Device only (console.h). */
#include "pr3marker.h"
#include "pr3run.h"
#include "console.h"
#include "shim_assert.h"

#ifdef PR3_MARKER	/* build.sh compiles this file in every build; empty without --pr3-marker */

/* Every line buffer is PR3_LB = 96 (RAMLOG_LINE_MAX). con_str prepends "PR3 ", so a text
 * longer than 91 would be cut by the ramlog; the longest texts (computed from the strings):
 * state line 7+16+1+30 = 54 ("readback skipped (write fault)"), ctr line 7+16+5+16+7+16 = 67,
 * first line 7+16+7+16+7+8 = 61, done line 86 (+4 = 90 <= 95; wfail is 2 digits, 8 gives 96). */
#define PR3_LB 96u

/* Bounded append: stops at cap-1, always NUL-terminated (no libc: no memcpy/memset). */
static void pr3_cat(char *buf, uint32_t cap, uint32_t *p, const char *str)
{
	SHIM_ASSERT(buf != 0 && p != 0 && str != 0);
	SHIM_ASSERT(*p < cap);
	while (*str && *p + 1 < cap)
		buf[(*p)++] = *str++;
	buf[*p] = 0;
}

static void pr3_cat_hex(char *buf, uint32_t cap, uint32_t *p, uint64_t v, uint32_t digits)
{
	SHIM_ASSERT(buf != 0 && p != 0);
	SHIM_ASSERT(*p < cap && digits <= 16);
	for (uint32_t i = 0; i < digits && *p + 1 < cap; i++)
		buf[(*p)++] = "0123456789ABCDEF"[(v >> (4 * (digits - 1 - i))) & 15];
	buf[*p] = 0;
}

static void pr3_head(char *b, uint32_t *p, uint64_t site)
{
	SHIM_ASSERT(b != 0 && p != 0);
	SHIM_ASSERT(*p == 0);
	pr3_cat(b, PR3_LB, p, "MARKER ");
	pr3_cat_hex(b, PR3_LB, p, site, 16);
	pr3_cat(b, PR3_LB, p, " ");
}

static void pr3_line(uint64_t site, const char *state)
{
	char b[PR3_LB];
	uint32_t p = 0;

	SHIM_ASSERT(state != 0);
	pr3_head(b, &p, site);
	pr3_cat(b, PR3_LB, &p, state);
	SHIM_ASSERT(p < PR3_LB);
	con_str("PR3", b);
}

static void pr3_ctr_line(uint64_t site, const char *what, uint64_t v,
			 const char *what2, uint64_t v2)
{
	char c[PR3_LB];
	uint32_t q = 0;

	SHIM_ASSERT(what != 0);
	pr3_head(c, &q, site);
	pr3_cat(c, PR3_LB, &q, what);
	pr3_cat(c, PR3_LB, &q, " ");
	pr3_cat_hex(c, PR3_LB, &q, v, 16);
	if (what2) {
		pr3_cat(c, PR3_LB, &q, " ");
		pr3_cat(c, PR3_LB, &q, what2);
		pr3_cat(c, PR3_LB, &q, " ");
		pr3_cat_hex(c, PR3_LB, &q, v2, 16);
	}
	SHIM_ASSERT(q < PR3_LB);
	con_str("PR3", c);
}

/* "first <8 bytes> cs ok|bad self ok|bad" for a marker that is neither zero nor intact. */
static void pr3_first_line(uint64_t site, const struct pr3_info *in)
{
	char c[PR3_LB];
	uint32_t q = 0;

	SHIM_ASSERT(in != 0);
	pr3_head(c, &q, site);
	pr3_cat(c, PR3_LB, &q, "first ");
	pr3_cat_hex(c, PR3_LB, &q, in->first, 16);
	pr3_cat(c, PR3_LB, &q, in->cs_ok ? " cs ok" : " cs bad");
	pr3_cat(c, PR3_LB, &q, in->self_ok ? " self ok" : " self bad");
	SHIM_ASSERT(q < PR3_LB);
	con_str("PR3", c);
}

/* The readback verdict line (in->readback: 0 ok, 1 mismatch, 2 skipped after a write fault,
 * 4 read fault). */
static void pr3_readback_line(uint64_t site, const struct pr3_info *in)
{
	char c[PR3_LB];
	uint32_t q = 0;

	SHIM_ASSERT(in != 0);
	SHIM_ASSERT(in->readback <= 4);
	if (in->readback == 0) {
		pr3_line(site, "readback ok");
	} else if (in->readback == 1) {
		pr3_line(site, "readback MISMATCH");
	} else if (in->readback == 4) {
		pr3_head(c, &q, site);
		pr3_cat(c, PR3_LB, &q, "readback fault at +0x");
		pr3_cat_hex(c, PR3_LB, &q, (uint64_t)in->fault_off, 2);
		con_str("PR3", c);
	} else {
		pr3_line(site, "readback skipped (write fault)");
	}
}

static enum pr3_state pr3_site(uint64_t site, uint64_t build, uint32_t *wfail)
{
	uint64_t rd, wr;
	struct pr3_info in;
	enum pr3_state st = pr3_probe_site(site, build, &rd, &wr, wfail, &in);

	SHIM_ASSERT(st <= PR3_FAULT);
	SHIM_ASSERT(*wfail <= 3);
	/* the state word is the loud signal for changed/fault (no GAP convention in the shim);
	 * an if-chain, not a name table: a table of pointers is an ABS64 reloc the build refuses */
	if (st == PR3_ZERO)
		pr3_line(site, "zero");
	else if (st == PR3_INTACT)
		pr3_line(site, "intact");
	else if (st == PR3_CHANGED)
		pr3_line(site, "changed");
	else
		pr3_line(site, "fault");
	if (st == PR3_INTACT && in.build != build)
		pr3_line(site, "intact (other build)");
	pr3_ctr_line(site, "ctr", rd, "build", in.build);
	if (st == PR3_CHANGED)
		pr3_first_line(site, &in);
	if (*wfail == 3) {	/* read fault: pr3_probe_site wrote nothing */
		pr3_line(site, "write skipped (read fault)");
		return st;
	}
	if (*wfail == 1)
		pr3_line(site, "write-fault");
	else
		pr3_ctr_line(site, "wrote ctr", wr, 0, 0);
	pr3_readback_line(site, &in);
	return st;
}

void pr3_marker_run(void)
{
	uint64_t build = pr3_hash_str(PSTORE_BUILD_ID);
	static const uint64_t sites[] = { PR3_SITE_A, PR3_SITE_B };
	uint32_t n[4] = { 0, 0, 0, 0 };
	uint32_t wf = 0;
	char d[PR3_LB];
	uint32_t p = 0;

	for (uint32_t i = 0; i < 2; i++) {
		uint32_t w = 0;

		n[pr3_site(sites[i], build, &w)]++;
		if (w)
			wf++;
	}
	SHIM_ASSERT(n[0] + n[1] + n[2] + n[3] == 2);
	SHIM_ASSERT(wf <= 2);
	pr3_cat(d, PR3_LB, &p, "MARKER done n=2 intact=");
	pr3_cat_hex(d, PR3_LB, &p, n[PR3_INTACT], 8);
	pr3_cat(d, PR3_LB, &p, " changed=");
	pr3_cat_hex(d, PR3_LB, &p, n[PR3_CHANGED], 8);
	pr3_cat(d, PR3_LB, &p, " zero=");
	pr3_cat_hex(d, PR3_LB, &p, n[PR3_ZERO], 8);
	pr3_cat(d, PR3_LB, &p, " fault=");
	pr3_cat_hex(d, PR3_LB, &p, n[PR3_FAULT], 8);
	pr3_cat(d, PR3_LB, &p, " wfail=");
	pr3_cat_hex(d, PR3_LB, &p, wf, 2);
	SHIM_ASSERT(p < PR3_LB);
	con_str("PR3", d);
}

#endif
