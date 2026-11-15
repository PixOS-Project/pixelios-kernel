/*
 * Host test of the msgbuf table interpreter (xnu patch 0198, sweet_wlan_tbl.h) against a fake dongle, on the GENERATED table of the proven
 * L1-L22 run (sweet_wlan_tbl_data.h, tools/wlan-tbl-gen). Usage: wlan_tbl_host_test REF.log [EMIT.log]
 *   REF.log  = the proven run's G2-scope store lines (tools/testdata/wlan-tbl/proven-g2-slice.log); the interpreter's own store log must equal it line by line
 *   EMIT.log = where the clean run's log goes, for `tools/replay wifi --baseline` (tools/test_wlan_tbl_replay.py)
 * The fake starts from the `old` word of the first row that touches an address (the entry state the proven run read) and models the Command
 * registers (status half kept), the BAR2 window and 16-bit stores. One mutant per interpreter rule below (changed value, dropped row, reordered
 * row, row outside the envelope, missing restore, wait without timeout, store fault with teardown).
 */
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

static char logbuf[1 << 20];
static unsigned loglen;
static int cap_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int cap_printf(const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(logbuf + loglen, sizeof logbuf - loglen, fmt, ap);
	va_end(ap);
	if (n > 0 && loglen + (unsigned)n < sizeof logbuf) {
		loglen += (unsigned)n;
	}
	return n;
}
#pragma GCC diagnostic ignored "-Wunused-function"      /* static helpers of the shared headers this test does not call */
#define printf cap_printf
#include "sweet_wlan_tbl.h"
#include "sweet_wlan_tbl_data.h"
#undef printf

#define RAM_END 0x4A0000u       /* "ram_end 0x4a0000" of the proven run */
#define HOST_LEN 0x4b080u       /* "block pa ... + 0x4b080" of the proven run */
#define NCELL 4096u

struct cell { int sp; uint32_t a, v; int used; };
static struct cell mem[NCELL];
static uint32_t fwin, nwr, nrst, fault_at;      /* fault_at: the n-th store (1-based) is refused; 0 = none */

static struct cell *cellof(int sp, uint32_t a)
{
	uint32_t h = (a * 2654435761u + (uint32_t)sp) % NCELL;

	while (mem[h].used && (mem[h].sp != sp || mem[h].a != a)) {
		h = (h + 1u) % NCELL;
	}
	mem[h].sp = sp;
	mem[h].a = a;
	return &mem[h];
}

static int winok(int sp, uint32_t a)
{
	return sp != SWG_SP_TCM || fwin == (a >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u);
}

static int f_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	struct cell *c;

	(void)ctx;
	if (!winok(sp, a)) {
		return -1;
	}
	c = cellof(sp, a);
	*v = c->used ? c->v : 0u;
	return 0;
}

static int f_wr(void *ctx, int sp, uint32_t a, uint32_t v)
{
	struct cell *c;

	(void)ctx;
	if (sp == SWG_SP_WIN2) {
		fwin = v;
		return 0;
	}
	if (!winok(sp, a) || (fault_at != 0u && nwr + 1u == fault_at)) {
		return -1;
	}
	nwr++;
	c = cellof(sp, a);
	c->v = (sp == SWG_SP_RC || sp == SWG_SP_EP) ? (c->v & 0xFFFF0000u) | (v & 0xFFFFu) : v;
	c->used = 1;
	return 0;
}

static int f_wr16(void *ctx, int sp, uint32_t a, uint16_t v)
{
	struct cell *c = cellof(sp, a);

	(void)ctx;
	if (!winok(sp, a)) {
		return -1;
	}
	nwr++;
	c->v = (c->v & 0xFFFF0000u) | v;
	c->used = 1;
	return 0;
}

static void f_delay(void *ctx, unsigned ms)
{
	(void)ctx;
	(void)ms;
}

static int f_restore(void *ctx, int space, uint32_t win, uint32_t off, uint32_t val)
{
	(void)ctx;
	(void)win;
	nrst++;
	if (space == 1 && off == SWT_REG_OFF) {
		fwin = val;
	}
	return 0;
}

static const struct swg_bus bus = { f_rd, f_wr, f_delay, 0, f_wr16, 0, NULL, NULL, NULL };
static struct swt_row tbl[SWT_TABLE_ROWS + 4u];
static unsigned ntbl;

static void fresh(void)
{
	unsigned i;

	memset(mem, 0, sizeof mem);
	fwin = nwr = nrst = fault_at = 0u;
	loglen = 0;
	logbuf[0] = 0;
	for (i = 0; i < ntbl; i++) {
		if (tbl[i].kind == SWT_K_STORE) {
			struct cell *c = cellof(swt_busspace(tbl[i].sp), tbl[i].addr);

			if (!c->used) {
				c->v = tbl[i].old;
				c->used = 1;
			}
		}
	}
}

static int run(struct swt_x *x)
{
	memset(x, 0, sizeof *x);
	x->b = &bus;
	x->env.ram_end = RAM_END;
	x->env.host_len = HOST_LEN;
	x->restore = f_restore;
	return swt_run(x, tbl, ntbl);
}

/* the store/undo/restore lines of logbuf, one per output line, from "SWEET WLAN:" on; returns the count */
static unsigned lines(const char *buf, char out[][200], unsigned max)
{
	unsigned n = 0;
	const char *p = buf;

	while (*p && n < max) {
		const char *e = strchr(p, '\n');
		const char *s = strstr(p, "SWEET WLAN: ");
		size_t l = e ? (size_t)(e - p) : strlen(p);

		if (s && s < p + l && (strstr(s, "G2 store ") == s + 12 || strstr(s, "restore space") == s + 12 || strstr(s, "G2 undo ok") == s + 12)) {
			size_t k = l - (size_t)(s - p);

			if (k > 199) {
				k = 199;
			}
			memcpy(out[n], s, k);
			out[n][k] = 0;
			if (strchr(out[n], ';')) {      /* the W17 context tail of the undo line is not the interpreter's to print */
				*strchr(out[n], ';') = 0;
			}
			n++;
		}
		if (!e) {
			break;
		}
		p = e + 1;
	}
	return n;
}

static char refl[800][200], runl[800][200];
static unsigned nref;

/* 1 when the interpreter's store log equals the reference line by line */
static int same_as_ref(void)
{
	unsigned n = lines(logbuf, runl, 800), i;

	if (n != nref) {
		return 0;
	}
	for (i = 0; i < n; i++) {
		if (strcmp(refl[i], runl[i]) != 0) {
			return 0;
		}
	}
	return 1;
}

static void load_ref(const char *path)
{
	FILE *f = fopen(path, "r");
	static char buf[1 << 20];
	size_t n;

	if (!f) {
		fprintf(stderr, "RESULT: ERROR cannot read %s\n", path);
		exit(2);
	}
	n = fread(buf, 1, sizeof buf - 1, f);
	buf[n] = 0;
	fclose(f);
	nref = lines(buf, refl, 800);
}

static void reload(void)
{
	memcpy(tbl, swt_table, sizeof swt_table);
	ntbl = SWT_TABLE_ROWS;
}

static void test_clean(const char *emit)
{
	struct swt_x x;
	int rc;

	reload();
	fresh();
	rc = run(&x);
	CHECK(rc == 0 && x.gaps == 0);
	CHECK(nref == SWT_TABLE_ROWS - 1u + 0u || nref > 500u);
	CHECK(same_as_ref());
	CHECK(nrst == 3u);
	CHECK(strstr(logbuf, "G2 undo ok (ep restored, rc restored)") != 0);
	if (emit) {
		FILE *f = fopen(emit, "w");

		if (f) {
			fwrite(logbuf, 1, loglen, f);
			fclose(f);
		}
	}
}

static unsigned first_row(int kind)
{
	unsigned i;

	for (i = 0; i < ntbl; i++) {
		if (tbl[i].kind == kind) {
			return i;
		}
	}
	return 0;
}

static void test_value_mutants(void)
{
	struct swt_x x;
	unsigned a = first_row(SWT_K_STORE) + 10u, i, b;
	struct swt_row t;

	reload();
	tbl[a].val ^= 0x10u;                    /* changed value: same old, different store */
	fresh();
	(void)run(&x);
	CHECK(!same_as_ref());
	reload();                               /* dropped row */
	for (i = a; i + 1u < ntbl; i++) {
		tbl[i] = tbl[i + 1u];
	}
	ntbl--;
	fresh();
	(void)run(&x);
	CHECK(!same_as_ref());
	reload();                               /* reordered rows */
	b = a + 1u;
	t = tbl[a];
	tbl[a] = tbl[b];
	tbl[b] = t;
	fresh();
	(void)run(&x);
	CHECK(!same_as_ref());
}

static void test_refusals(void)
{
	struct swt_x x;
	unsigned i, a = first_row(SWT_K_STORE) + 5u;
	int rc;

	reload();                               /* row outside the envelope: an unreviewed BAR0 offset */
	for (i = 0; i < ntbl; i++) {
		if (tbl[i].sp == SWT_SP_PCIE && tbl[i].kind == SWT_K_DOORBELL) {
			tbl[i].addr = 0x148u;
			break;
		}
	}
	fresh();
	rc = run(&x);
	CHECK(rc == -SWT_E_ENV && nwr == 0u && nrst == 0u && strstr(logbuf, "WIFI GAP: table row") != 0 && x.gaps == 1u);
	reload();                               /* a host word beyond the block */
	tbl[a].sp = SWT_SP_HOST;
	tbl[a].addr = HOST_LEN;
	fresh();
	CHECK(run(&x) == -SWT_E_ENV && nwr == 0u);
	reload();                               /* missing restore: the window restore row dropped */
	for (i = 0; i < ntbl; i++) {
		if (tbl[i].kind == SWT_K_RESTORE && tbl[i].sp == SWT_SP_R1 && tbl[i].addr == SWT_REG_OFF) {
			tbl[i] = tbl[ntbl - 1u];
			ntbl--;
			break;
		}
	}
	fresh();
	CHECK(run(&x) == -SWT_E_RESTORE && nwr == 0u);
	reload();                               /* missing restore: the command undo mark dropped */
	for (i = 0; i < ntbl; i++) {
		if (tbl[i].kind == SWT_K_MARK) {
			tbl[i] = tbl[ntbl - 1u];
			ntbl--;
			break;
		}
	}
	fresh();
	CHECK(run(&x) == -SWT_E_RESTORE && nwr == 0u);
}

static void add_poll(unsigned tmo, uint32_t want)
{
	struct swt_row *p = &tbl[ntbl++];

	memset(p, 0, sizeof *p);
	p->kind = SWT_K_POLL;
	p->sp = SWT_SP_PCIE;
	p->cls = SWT_C_D4B;
	p->addr = SWG_PCIE_STATUS_OFF;
	p->mask = 0xFFFFFFFFu;
	p->val = want;
	p->timeout_ms = tmo;
	strcpy(p->name, "poll c30");
}

static void test_polls_and_faults(void)
{
	struct swt_x x;
	int rc;

	reload();
	add_poll(0u, 0u);                       /* wait without timeout */
	fresh();
	CHECK(run(&x) == -SWT_E_TMO && nwr == 0u);
	reload();
	add_poll(100u, 1u);                     /* wait that never ends: GAP, teardown still runs */
	fresh();
	rc = run(&x);
	CHECK(rc == -SWT_E_TMO && nrst == 3u && strstr(logbuf, "poll timed out") != 0 && strstr(logbuf, "G2 undo ok") != 0);
	reload();
	add_poll(100u, 0u);                     /* wait that ends at once */
	fresh();
	CHECK(run(&x) == 0 && nrst == 3u);
	reload();                               /* a refused store in the middle: stop, undo, restore, no silent success */
	fresh();
	fault_at = 40u;
	rc = run(&x);
	CHECK(rc == -SWT_E_STORE && nwr < 100u && nrst == 3u && x.gaps >= 1u && strstr(logbuf, "store refused") != 0);
	reload();                               /* an entry word that differs from the table: stop on the old value */
	fresh();
	cellof(SWG_SP_TCM, tbl[first_row(SWT_K_STORE)].addr)->v ^= 1u;
	CHECK(run(&x) == -SWT_E_OLD);
}

int main(int argc, char **argv)
{
	/* The gate's host-tests step runs every listed binary with no arguments, from shim/: default to the committed proven-run slice (T-8b3c). */
	const char *ref = argc > 1 ? argv[1] : "../tools/testdata/wlan-tbl/proven-g2-slice.log";
	if (argc < 2)
		fprintf(stdout, "wlan_tbl_host_test: no REF.log argument, using %s\n", ref);
	load_ref(ref);
	test_clean(argc > 2 ? argv[2] : 0);
	test_value_mutants();
	test_refusals();
	test_polls_and_faults();
	if (fails) {
		printf("RESULT: FAIL wlan_tbl_host_test fails=%u\n", fails);
		return 1;
	}
	fprintf(stdout, "RESULT: PASS wlan_tbl_host_test rows=%u sha=%s mutants=7\n", SWT_TABLE_ROWS, SWT_TABLE_SHA256);
	return 0;
}
