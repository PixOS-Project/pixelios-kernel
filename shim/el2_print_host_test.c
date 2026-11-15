/*
 * B3p: host test of the EL2 trap probe's real EL1 printer (shim/el2.c el2_print_report / el2_print_ring) against the run-plan rows of
 * S/docs/specs/SPEC-EL2-PROXY-PROBE.md section 3.9 (spec section 6 step B3p, section 3.3.9).
 *
 * SCOPE: Probe A (the shim proxy, spec section 2) is NOT built, so the section 2.5 run-plan rows are not checked here (spec B3p asks for "every 2.5 row"
 * once proxy.c exists; this test covers the 3.9 rows only).
 *
 * The fakes format exactly as shim/console.c:260-283 (con_hex "%s %016llX"; con_hex2 "%s %s%08X %s%08X"; con_str "%s %s") and cap every line at
 * RAMLOG_LINE_MAX (shim/ramlog.h:14, 96 bytes including the NUL): a line of 96 or more bytes fails the test. The lines go to a file and every
 * --expect / --reject regex of 3.9 is run on that file with `grep -E`, the engine tools/probe-run uses (tools/probe-run:24).
 * The two `GICD_*` lines of the normal run are printed by shim.c:640-641 (`con_hex("GICD_TYPER", ..)`), not by el2.c: the test emits them itself in the
 * same format, between the ring entries it prints (they are not the code under test).
 * Build: gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -I. -I$O -include $O/board_config.h -o $O/el2_print_host_test el2.c el2_print_host_test.c
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "el2.h"
#include "ramlog.h"

#ifndef EL2_VTCR
#error "build with -include board_config.h (generated EL2_VTCR)"
#endif

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); dump(); exit(1); } } while (0)

#define MAXL 256
static char lines[MAXL][RAMLOG_LINE_MAX];
static unsigned nl;
static unsigned overlong;	/* stays 0: an overlong line exits at once */

static void dump(void)
{
	for (unsigned i = 0; i < nl; i++)
		fprintf(stderr, "  [%u] %s\n", i, lines[i]);
}

/* a printed line must fit RAMLOG_LINE_MAX including the NUL; returns 1 if it does not */
static int too_long(const char *s)
{
	return strlen(s) >= RAMLOG_LINE_MAX;
}

static void add(const char *s)
{
	if (too_long(s)) {
		overlong++;
		fprintf(stderr, "FAIL: printed line of %zu bytes >= RAMLOG_LINE_MAX %u: %s\n", strlen(s), RAMLOG_LINE_MAX, s);
		exit(1);
	}
	EXPECT(nl < MAXL);
	strcpy(lines[nl++], s);
}

void con_str(const char *label, const char *s)
{
	char b[512];
	snprintf(b, sizeof b, "%s %s", label, s);
	add(b);
}

void con_hex(const char *label, uint64_t v)
{
	char b[512];
	snprintf(b, sizeof b, "%s %016llX", label, (unsigned long long)v);
	add(b);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b2, uint32_t vb)
{
	char b[512];
	if (b2)
		snprintf(b, sizeof b, "%s %s%08X %s%08X", label, a, va, b2, vb);
	else
		snprintf(b, sizeof b, "%s %s%08X", label, a, va);
	add(b);
}

static void reset(void)
{
	memset(el2_ring, 0, sizeof el2_ring);
	memset(&el2_cen, 0, sizeof el2_cen);
	memset(&el2_rb, 0, sizeof el2_rb);
	el2_ring_n = el2_ring_ovf = el2_traps = el2_gaps = el2_bound = el2_fallback = el2_census_ok = el2_s2_on = 0;
	nl = 0;
	overlong = 0;
}

static int has_line(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strcmp(lines[i], s) == 0)
			return 1;
	return 0;
}

static unsigned count_prefix(const char *p)
{
	unsigned n = 0;
	for (unsigned i = 0; i < nl; i++)
		if (strncmp(lines[i], p, strlen(p)) == 0)
			n++;
	return n;
}

/* write the printed lines to a file and run grep -E on it: 0 = matched, 1 = no match; anything else fails the test */
static const char *tmp_path;
static int grep_file(const char *re)
{
	FILE *f = fopen(tmp_path, "w");
	EXPECT(f);
	for (unsigned i = 0; i < nl; i++)
		fprintf(f, "%s\n", lines[i]);
	fclose(f);
	char cmd[1024];
	EXPECT(!strchr(re, '\''));
	snprintf(cmd, sizeof cmd, "grep -E -q -e '%s' '%s'", re, tmp_path);
	int st = system(cmd);
	EXPECT(st != -1 && (st & 0x7F) == 0);
	int rc = (st >> 8) & 0xFF;
	EXPECT(rc == 0 || rc == 1);
	return rc == 0;
}

static struct el2_ent served(uint64_t ipa, uint64_t val)
{
	struct el2_ent e;
	memset(&e, 0, sizeof e);
	e.kind = EL2_K_SERVED;
	e.ipa = ipa;
	e.val = val;
	e.esr = 0x93810007u;		/* ldr w1,[x0], EC 0x24, ISV 1, SAS 2, FSC 0x07 (translation fault level 3) */
	e.width = 4;
	e.vm = 1;
	return e;
}

/* section 3.9 rows, --expect side (the row number is part of the failure message) */
static const struct { const char *row, *re; } expect_rows[] = {
	{ "1", "EL2 CENSUS sctlr [0-9A-F]{16}" },
	{ "2", "EL2 S2 ON hcr 0000000080000003" },
	{ "2", "EL2 S2 ON vtcr 000000008002B05C" },
	{ "3", "EL2 TRAP n=01 syn ec=00000024 isv=00000001" },
	{ "3", "EL2 TRAP n=01 sz sas=00000002 dfsc=00000007" },
	{ "4", "EL2 TRAP n=02 ipa 0000000010400004" },
	{ "4", "EL2 TRAP n=02 val 000000000079041E" },
	{ "4", "GICD_TYPER 0+79041E" },
	{ "4b", "EL2 TRAP n=03 ipa 0000000010400004" },
	{ "4b", "EL2 TRAP n=03 val 000000000079041E" },
	{ "4b", "EL2 TRAP n=04 ipa 0000000010400000" },
	{ "4b", "EL2 TRAP n=04 val 0000000000000010" },
	{ "4b", "GICD_CTLR 0+10" },
	{ "5", "EL2 RESULT traps=00000004 gaps=00000000" },
};
static const struct { const char *row, *re; } reject_rows[] = {
	{ "6", "EL2 GAP" },
	{ "6", "PANIC" },
	{ "6b", "EL2 GAP bound" },
};

static void normal_run(void)
{
	reset();
	EXPECT(EL2_VTCR == 0x8002B05CUL);		/* the generated constant is what the row 2 regex pins */
	el2_census_ok = 1;
	el2_cen.sctlr = 0x30D50838ull;			/* values are unknown on hardware (row 1 is a measurement): any 64-bit value must print as 16 digits */
	el2_cen.hcr = 0x80000000ull;
	el2_cen.vtcr = 0;
	el2_cen.vttbr = 0;
	el2_cen.tcr = 0x80823519ull;
	el2_cen.mdcr = 0;
	el2_cen.cptr = 0x33FFull;
	el2_cen.mmfr1 = 0x1011101ull;
	el2_s2_on = 1;
	el2_rb.hcr = 0x80000003ull;	/* SWIO reads 1 on bluejay (SPEC DRAFT 7, U-B9 [obs] el2-1-s2trap) */
	el2_rb.vtcr = EL2_VTCR;
	el2_rb.vttbr = 0x881230000ull;
	struct el2_ent e = served(0x881234000ull, 0x1234);	/* n=1: the T1 test read; the IPA of T1 is a runtime PA, no row pins it */
	el2_account(&e);
	e = served(0x10400004ull, 0x79041E);			/* n=2: the T2 test read, GICD_TYPER */
	el2_account(&e);
	e = served(0x10400004ull, 0x79041E);			/* n=3: shim.c:640 */
	el2_account(&e);
	e = served(0x10400000ull, 0x10);			/* n=4: shim.c:641 GICD_CTLR */
	el2_account(&e);
	/* shim.c:640-641 print these two lines themselves (console.c:260-265 format) right after the reads */
	con_hex("GICD_TYPER", 0x79041E);
	con_hex("GICD_CTLR", 0x10);
	el2_print_report();
}

static void test_normal(void)
{
	normal_run();
	EXPECT(overlong == 0);
	EXPECT(el2_traps == 4 && el2_gaps == 0);
	for (unsigned i = 0; i < sizeof expect_rows / sizeof expect_rows[0]; i++) {
		if (!grep_file(expect_rows[i].re)) {
			fprintf(stderr, "FAIL: row %s --expect '%s' matches no printed line\n", expect_rows[i].row, expect_rows[i].re);
			dump();
			exit(1);
		}
	}
	for (unsigned i = 0; i < sizeof reject_rows / sizeof reject_rows[0]; i++) {
		if (grep_file(reject_rows[i].re)) {
			fprintf(stderr, "FAIL: row %s --reject '%s' matches a printed line of a normal run\n", reject_rows[i].row, reject_rows[i].re);
			dump();
			exit(1);
		}
	}
	/* the census prints one line per register, all 16 digits */
	EXPECT(count_prefix("EL2 CENSUS ") == 8);
	EXPECT(has_line("EL2 CENSUS sctlr 0000000030D50838"));
	/* the 16 ring entries x 5 lines fit; the per-entry lines (esr) too */
	EXPECT(has_line("EL2 TRAP n=01 esr 0000000093810007"));
	EXPECT(has_line("EL2 S2 ON vttbr 0000000881230000"));
}

static void test_bound(void)
{
	/* r5 F3: el2_bound = 1 and gaps = 1 with an empty ring, el2_print_ring(): exactly "EL2 GAP bound", which matches `EL2 GAP` and the 6b reject */
	reset();
	struct el2_ent b;
	memset(&b, 0, sizeof b);
	b.kind = EL2_K_BOUND;
	el2_account(&b);
	EXPECT(el2_bound == 1 && el2_gaps == 1 && el2_ring_n == 0);
	el2_print_ring();
	EXPECT(nl == 1 && strcmp(lines[0], "EL2 GAP bound") == 0);
	EXPECT(grep_file("EL2 GAP"));
	EXPECT(grep_file("EL2 GAP bound"));
	/* and it is not printed when the bound is clear */
	reset();
	el2_print_ring();
	EXPECT(nl == 0);
}

static void test_gap_lines(void)
{
	struct el2_ent e;
	reset();
	for (unsigned i = 0; i < 4; i++) {		/* ring slots 1-4: served reads, so the next entry is n=05 */
		e = served(0x10400004ull, 0x79041E);
		el2_account(&e);
	}
	memset(&e, 0, sizeof e);			/* n=05: class-1 GAP (the example of spec 3.3.9) */
	e.kind = EL2_K_GAP_EC;
	e.esr = 0x93810007u;
	e.ipa = 0x10400004ull;
	e.vm = 0;
	el2_account(&e);
	memset(&e, 0, sizeof e);			/* n=06: hvc */
	e.kind = EL2_K_HVC;
	e.elr = 0x1234ABCD00ull;
	e.vm = 1;
	el2_account(&e);
	memset(&e, 0, sizeof e);			/* n=07: nisv */
	e.kind = EL2_K_NISV;
	e.ipa = 0x10400000ull;
	e.vm = 1;
	el2_account(&e);
	e = served(0x10400004ull, 0);			/* n=08: faulted read */
	e.fault = 1;
	el2_account(&e);
	nl = 0;
	el2_print_ring();
	EXPECT(has_line("EL2 GAP n=05 ec=00000024 ipa=10400004"));
	EXPECT(has_line("EL2 GAP n=05 ipa64 0000000010400004"));
	EXPECT(has_line("EL2 GAP n=05 vm cleared"));
	EXPECT(has_line("EL2 GAP n=06 hvc 0000001234ABCD00"));
	EXPECT(has_line("EL2 GAP n=07 nisv 0000000010400000"));
	EXPECT(has_line("EL2 GAP n=08 fault 0000000010400004"));
	EXPECT(count_prefix("EL2 GAP n=06 vm") == 0);
	EXPECT(overlong == 0);
	/* ring full: 16 slots, then one more event */
	reset();
	e = served(0x10400004ull, 0x79041E);
	for (unsigned i = 0; i < 17; i++)
		el2_account(&e);
	el2_print_ring();
	EXPECT(has_line("EL2 TRAP n=10 ipa 0000000010400004"));	/* slot 16 prints as hex 10 */
	EXPECT(count_prefix("EL2 TRAP n=11") == 0);
	EXPECT(has_line("EL2 GAP ring full"));
	EXPECT(!has_line("EL2 GAP bound"));
	EXPECT(overlong == 0);
	/* a kind-4 record cannot reach EL1; if it does it prints loudly, not nothing */
	reset();
	memset(&e, 0, sizeof e);
	e.kind = EL2_K_PARK;
	e.esr = 0x62000000u;
	e.vm = 1;
	el2_account(&e);
	el2_print_ring();
	EXPECT(has_line("EL2 GAP n=01 kind=00000004 esr=62000000"));
}

static void test_fallbacks(void)
{
	reset();
	el2_print_report();
	EXPECT(nl == 1 && strcmp(lines[0], "EL2 GAP: census not taken") == 0);
	static const struct { unsigned fb; const char *line; } fb[] = {
		{ EL2_FB_SCTLR_M, "EL2 GAP: SCTLR_EL2.M=1" },
		{ EL2_FB_MDCR, "EL2 GAP: MDCR_EL2 traps" },
		{ EL2_FB_OUTSIDE_BANKS, "EL2 GAP: shim outside dram_banks" },
	};
	for (unsigned i = 0; i < sizeof fb / sizeof fb[0]; i++) {
		reset();
		el2_census_ok = 1;
		el2_fallback = fb[i].fb;
		el2_print_report();
		EXPECT(count_prefix("EL2 CENSUS ") == 8);
		EXPECT(has_line(fb[i].line));
		EXPECT(count_prefix("EL2 S2 ON") == 0 && count_prefix("EL2 RESULT") == 0);	/* no RESULT: row 5 stays unmatched, loud by absence */
	}
	reset();
	el2_census_ok = 1;
	el2_fallback = 99;
	el2_print_report();
	EXPECT(has_line("EL2 GAP: unknown fallback 0000000000000063"));
	reset();
	el2_census_ok = 1;
	el2_print_report();
	EXPECT(has_line("EL2 GAP: stage 2 not enabled"));
}

/* B2 / EL2_RESIDENT (SPEC 3.4 (b) design items 2 and 6): the lines printed before the jump, against the --expect / --reject rows of el2-2-xnu */
static void test_resident(void)
{
	reset();
	el2_census_ok = 1;
	el2_s2_on = 1;
	el2_rb.hcr = 0x80000003ull;
	el2_rb.vtcr = EL2_VTCR;
	el2_rb.vttbr = 0x881230000ull;
	el2_print_report();			/* what loader.c prints: the readbacks + RESULT of an empty ring ... */
	el2_print_resident(1ull << 62);		/* ... then the handshake line */
	EXPECT(overlong == 0);
	EXPECT(grep_file("EL2 S2 ON hcr 0000000080000003"));
	EXPECT(grep_file("EL2 RESIDENT bootflags 4000000000000000"));
	EXPECT(has_line("EL2 RESIDENT bootflags 4000000000000000") && strlen("EL2 RESIDENT bootflags 4000000000000000") < RAMLOG_LINE_MAX);
	EXPECT(grep_file("EL2 RESULT traps=00000000 gaps=00000000"));
	EXPECT(!grep_file("EL2 GAP"));		/* row --reject 'EL2 GAP' */
	/* stage 2 not on: the flag is 0 and the line says so (the run plan's expect row then fails, loudly) */
	reset();
	el2_print_resident(0);
	EXPECT(has_line("EL2 RESIDENT bootflags 0000000000000000") && !grep_file("EL2 RESIDENT bootflags 4000000000000000"));
}

static void test_truncation_check(void)
{
	/* the cap detector itself: 95 bytes fit, 96 do not (RAMLOG_LINE_MAX includes the NUL) */
	char s[128];
	memset(s, 'x', sizeof s);
	s[95] = 0;
	EXPECT(!too_long(s));
	s[95] = 'x';
	s[96] = 0;
	EXPECT(too_long(s));
}

int main(void)
{
	char path[] = "el2_print_host_test_XXXXXX";
	const char *dir = getenv("TMPDIR");
	char full[512];
	snprintf(full, sizeof full, "%s/%s", dir && *dir ? dir : "/tmp", path);
	int fd = mkstemp(full);
	if (fd < 0) {
		perror("mkstemp");
		return 1;
	}
	close(fd);
	tmp_path = full;
	test_truncation_check();
	test_normal();
	test_bound();
	test_gap_lines();
	test_fallbacks();
	test_resident();
	unlink(full);
	puts("el2_print_host_test: ALL OK");
	return 0;
}
