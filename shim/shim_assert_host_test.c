/*
 * Host test for SHIM_ASSERT (shim_assert.h / shim_assert.c, T-98a7): the DEVICE path against a fake console, fake shim_stop(),
 * fake shim_halt() and fake CPU slot. Checks: a false assertion logs exactly "SWEET ASSERT <basename>:<line> <cond>", clears the
 * screen, stops once and never returns; a true one does nothing; a failure while reporting stops without a second report; a
 * failure inside the stop's own logging ends in the quiet halt (bounded, no stack growth); CPUs have separate depths; the line
 * fits RAMLOG_LINE_MAX exactly and never overflows the buffer (build with ASan/UBSan, build.sh).
 *   gcc -O1 -Wall -Wextra -Werror -fsanitize=address,undefined -I. -o out/shim_assert_host_test shim_assert_host_test.c
 */
#define SHIM_ASSERT_DEVICE 1
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include "shim_assert.c"

static char lines[8][RAMLOG_LINE_MAX + 32];
static unsigned nlines, nclear, nstop, nhalt, ncon;
static jmp_buf stopped;
static int trip_line;
static int nested_assert;	/* the console trips an assertion once while reporting */
static int con_always_asserts;	/* the console trips an assertion every time (the stop's own logging) */
static int stop_logs;		/* the fake stop logs like reset() does */
static uint32_t cpu_slot;

void con_clear(uint32_t bg) { (void)bg; nclear++; }
uint32_t shim_cpu_slot(void) { return cpu_slot; }

static int trip(int v);

void con_str(const char *label, const char *s)
{
	ncon++;
	if (nlines < 8)
		snprintf(lines[nlines], sizeof lines[0], "%s %s", label, s);
	nlines++;
	if (nested_assert) {
		nested_assert = 0;
		SHIM_ASSERT(0 == 1);	/* re-enters shim_assert_fail with the depth already 1 */
	}
	if (con_always_asserts)
		SHIM_ASSERT(0 == 2);
}

void __attribute__((noreturn)) shim_stop(void)
{
	nstop++;
	if (stop_logs)
		con_str("RESET", "stop");	/* what reset() does first */
	longjmp(stopped, 1);
}

void __attribute__((noreturn)) shim_halt(void)
{
	nhalt++;
	longjmp(stopped, 2);
}

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static void reset_state(void)
{
	nlines = nclear = nstop = nhalt = ncon = 0;
	memset((void *)assert_depth, 0, sizeof assert_depth);
	nested_assert = con_always_asserts = stop_logs = 0;
	cpu_slot = 0;
}

static int trip(int v)	/* 0 = fell through, 1 = stopped, 2 = halted */
{
	int r = setjmp(stopped);

	if (r == 0) {
		trip_line = __LINE__ + 1;
		SHIM_ASSERT(v == 7);
		return 0;
	}
	return r;
}

static void check_line_fits(const char *what, const char *file, uint32_t line, const char *cond, const char *want_prefix)
{
	reset_state();
	if (setjmp(stopped) == 0)
		shim_assert_fail(file, line, cond);
	if (!(nlines == 1 && strlen(lines[0]) <= RAMLOG_LINE_MAX - 1u)) {
		printf("FAIL %s: line too long or missing (%zu)\n", what, strlen(lines[0]));
		fails++;
	}
	if (strncmp(lines[0], want_prefix, strlen(want_prefix)) != 0) {
		printf("FAIL %s: got '%s' want prefix '%s'\n", what, lines[0], want_prefix);
		fails++;
	}
}

int main(void)
{
	char want[RAMLOG_LINE_MAX + 32], cond[200], file[200];

	reset_state();
	CHECK(trip(7) == 0);	/* true: silent */
	CHECK(nlines == 0 && nstop == 0 && nclear == 0);

	reset_state();
	CHECK(trip(8) == 1);	/* false: logs, stops once */
	CHECK(nstop == 1 && nhalt == 0 && nclear == 1 && nlines == 1);
	snprintf(want, sizeof want, "SWEET ASSERT shim_assert_host_test.c:%d v == 7", trip_line);
	CHECK(strcmp(lines[0], want) == 0);
	if (strcmp(lines[0], want))
		printf("got:  %s\nwant: %s\n", lines[0], want);

	reset_state();	/* failure while reporting: the second entry stops without a second report */
	nested_assert = 1;
	CHECK(trip(8) == 1);
	CHECK(nstop == 1 && nlines == 1 && nhalt == 0);

	reset_state();	/* the console asserts EVERY time and the stop logs: bounded, ends in the quiet halt (review F1) */
	con_always_asserts = 1;
	stop_logs = 1;
	CHECK(trip(8) == 2);
	CHECK(nhalt == 1 && nstop <= 2 && ncon <= 4);
	CHECK(assert_depth[0] == 3);

	reset_state();	/* CPU 1 asserts while CPU 0 is mid-report: CPU 1 still reports its own first assertion (review F2) */
	assert_depth[0] = 1;
	cpu_slot = 1;
	CHECK(trip(8) == 1);
	CHECK(nlines == 1 && strncmp(lines[0], "SWEET ASSERT ", 13) == 0);
	reset_state();
	assert_depth[0] = 1;	/* the same CPU re-entering: no report */
	CHECK(trip(8) == 1);
	CHECK(nlines == 0 && nstop == 1);
	reset_state();
	cpu_slot = 99;	/* out-of-range slot wraps into the table, never indexes past it */
	CHECK(trip(8) == 1 && nlines == 1);

	/* path and 32-bit line number: basename only, cut inside the cap */
	memset(cond, 'c', 150);
	cond[150] = 0;
	check_line_fits("path + max line", "a/b/c/dir/file.c", 4294967295u, cond, "SWEET ASSERT file.c:4294967295 cccc");
	/* exact fill: a long condition fills the line to RAMLOG_LINE_MAX - 1 characters (review F3 row 1) */
	reset_state();
	if (setjmp(stopped) == 0)
		shim_assert_fail("x.c", 7, cond);
	CHECK(strlen(lines[0]) == RAMLOG_LINE_MAX - 1u);
	/* ~80 character basename with the largest line number: the number itself hits the cap (review F3 row 2) */
	memset(file, 'f', 80);
	file[80] = 0;
	reset_state();
	if (setjmp(stopped) == 0)
		shim_assert_fail(file, 4294967295u, "x");
	CHECK(strlen(lines[0]) == RAMLOG_LINE_MAX - 1u);
	CHECK(strncmp(lines[0], "SWEET ASSERT ffff", 17) == 0);
	/* basename scan is bounded: a 400 character path without a slash is logged from its start */
	memset(file, 'p', 199);
	file[199] = 0;
	check_line_fits("long path no slash", file, 1, "c", "SWEET ASSERT ppp");

	if (fails) {
		printf("RESULT: FAIL shim_assert_host_test fails=%d\n", fails);
		return 1;
	}
	printf("RESULT: PASS shim_assert_host_test\n");
	return 0;
}
