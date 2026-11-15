/*
 * Host test of the DP15 executor swg_dp_exec (sweet_wlan_g2.h, T-75c7; SPEC-WIFI Part E TRANSPORT block s.1 items 1-7). A scripted bus records EVERY access (space, offset, value); the
 * expected access list of each plan is computed here from the plan itself, literally, not from the header. Clauses (each has a mutant in tools/test_wlan_dp15_mutants.py):
 *   1 env from engine state (wr0 = d4wr, never the caller's)   2 audit + env check before the first access   3 one copy of s.data, written after the audit
 *   4 kind sequences (flow create / post / tx / consume), every other order refused   5 old value (shadow; index slots low 16 bits) + read-back, doorbell never read
 *   6 stop rule (first failure stops: no retry, no doorbell, no further plan, WIFI GAP line)   7 secret plans print no value (canary search)   + ring full / ring-0 wrap / epoch
 * The ASan/UBSan lane builds every plan array and item as an exact-size heap copy. No hardware.
 */
#include <stdio.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static char logbuf[1 << 22];
static unsigned loglen, ngap;
static int cap_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int cap_printf(const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(logbuf + loglen, sizeof logbuf - loglen, fmt, ap);
	va_end(ap);
	if (n > 0 && loglen + (unsigned)n < sizeof logbuf)
		loglen += (unsigned)n;
	return n;
}
static void lg(const char *s) { cap_printf("%s\n", s); }
#define SWDP_LOG(s) lg(s)
#define SWDP_GAP(s) do { ngap++; lg(s); } while (0)
#include "sweet_wlan_data.h"
#define printf cap_printf
#include "sweet_wlan_g2.h"
#undef printf

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

/* ---- the world: the G1 block, the DATA block, the bus ---- */
#define G1PA   0x880000000ull
#define G1LEN  0x100000u
#define DAPA   0x880100000ull
#define DALEN  0x20000u
#define H2DW   0x880001000ull
#define H2DR   0x880001400ull
#define D2HR   0x880002000ull
#define RING0  0x880010000ull
#define RING1  0x880020000ull

static uint32_t g1[G1LEN / 4u], dat[DALEN / 4u];
struct acc { char k; int sp; uint32_t off, val; };      /* R / W / F (a write the bus refused) */
static struct acc A[8192];
static unsigned nA;
struct undo { int sp; uint32_t off, old; };
static struct undo U[8192];
static unsigned nU;
static struct { unsigned nw, fail_wr, corrupt_rb, pcie_reads, bad_sp; uint8_t *tamper; uint32_t tamper_len; } J;

static uint32_t *mem(int sp, uint32_t off)
{
	if (sp == SWG_SP_HOST && off < G1LEN && (off & 3u) == 0u)
		return &g1[off / 4u];
	if (sp == SWG_SP_DATA && off < DALEN && (off & 3u) == 0u)
		return &dat[off / 4u];
	return NULL;
}
static int b_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	uint32_t *m = mem(sp, a);

	(void)ctx;
	if (sp == SWG_SP_PCIE)
		J.pcie_reads++;
	if (m == NULL) {
		J.bad_sp += sp != SWG_SP_PCIE;
		return -1;
	}
	*v = *m;
	if (nA < 8192u)
		A[nA++] = (struct acc){ 'R', sp, a, *v };
	return 0;
}
static int b_wr(void *ctx, int sp, uint32_t a, uint32_t v)
{
	uint32_t *m = mem(sp, a);

	(void)ctx;
	J.nw++;
	if (J.fail_wr == J.nw) {
		A[nA++] = (struct acc){ 'F', sp, a, v };
		return -1;
	}
	A[nA++] = (struct acc){ 'W', sp, a, v };
	if (J.tamper != NULL && J.nw == 1u) {        /* clause 3: the SOURCE of the plan changes under the transport after its first store */
		memset(J.tamper, 0xEE, J.tamper_len);
	}
	if (sp == SWG_SP_PCIE)
		return a == SWG_DB0_OFF ? 0 : -1;
	if (m == NULL)
		return -1;
	U[nU++] = (struct undo){ sp, a, *m };
	*m = v;
	if (J.corrupt_rb == J.nw)
		*m ^= 1u;
	return 0;
}
static void b_delay(void *ctx, unsigned ms) { (void)ctx; (void)ms; }
static const struct swg_bus BUS = { b_rd, b_wr, b_delay, NULL, NULL, NULL, NULL, NULL, NULL };

static struct swg_wx x;
static uint32_t sh_r1[2048u * 8u], sh_fl[64u * 14u], sh_tx[16u * 512u];

static void world_reset(void)
{
	while (nU > 0u) {
		nU--;
		*mem(U[nU].sp, U[nU].off) = U[nU].old;
	}
	memset(&J, 0, sizeof J);
	nA = 0u;
	memset(sh_r1, 0, sizeof sh_r1);
	memset(sh_fl, 0, sizeof sh_fl);
	memset(sh_tx, 0, sizeof sh_tx);
	memset(&x, 0, sizeof x);
	x.b = &BUS;
	x.in.blk = G1PA;
	x.in.blk_len = G1LEN;
	x.in.idx[SWG_IDX(SWD_IDX_H2D_W)] = H2DW;
	x.in.idx[SWG_IDX(SWD_IDX_H2D_R)] = H2DR;
	x.in.idx[SWG_IDX(SWD_IDX_D2H_R)] = D2HR;
	x.in.ring[0] = RING0;
	x.in.ring[1] = RING1;
	x.entered = 1;
	x.d4b = 8;
	x.d4dn = 65u;
	x.d4wr = 5u;
	x.d4rd = 70u;                    /* absolute count: slot 6 */
	x.dpsh[0] = (struct swg_dp_sh){ RING1, 2048u * 8u, sh_r1 };
	x.dpsh[1] = (struct swg_dp_sh){ DAPA, 64u * 14u, sh_fl };
	x.dpsh[2] = (struct swg_dp_sh){ DAPA + 0x4000u, 16u * 512u, sh_tx };
	g1[(H2DW - G1PA) / 4u] = 5u;                    /* the index words hold the cursors mkenv() names: H2D-WR slots 0 / 1 / flow 2, D2H-RD slots 0 / 1 / 2 */
	g1[(H2DW - G1PA) / 4u + 1u] = 9u;
	g1[(H2DW - G1PA) / 4u + 2u] = 0u;
	g1[(H2DR - G1PA) / 4u] = g1[(H2DR - G1PA) / 4u + 1u] = g1[(H2DR - G1PA) / 4u + 2u] = 0u;
	g1[(D2HR - G1PA) / 4u] = 6u;
	g1[(D2HR - G1PA) / 4u + 1u] = 6u;
	g1[(D2HR - G1PA) / 4u + 2u] = 8u;
	loglen = 0u;
	logbuf[0] = '\0';
}
static void set_wr0(uint32_t v)
{
	x.d4wr = v;
	g1[(H2DW - G1PA) / 4u] = v;
}

static const uint8_t GWM[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 }, MAC[6] = { 0x02, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE };

static struct swdp_env mkenv(void)
{
	struct swdp_env e;

	memset(&e, 0, sizeof e);
	e.g1 = (struct swdp_range){ G1PA, G1LEN };
	e.data = (struct swdp_range){ DAPA, DALEN };
	e.h2d_wr = (struct swdp_range){ H2DW, 43 * 4 };
	e.d2h_rd = (struct swdp_range){ D2HR, 3 * 4 };
	e.ring0 = (struct swdp_range){ RING0, 128 * 40 };
	e.ring1 = (struct swdp_range){ RING1, 2048 * 32 };
	e.flow = (struct swdp_range){ DAPA, 64 * 56 };
	e.txbuf = (struct swdp_range){ DAPA + 0x4000u, 16 * 2048 };
	e.rxbuf = (struct swdp_range){ DAPA + 0xC000u, 16 * 2048 };
	e.depth0 = 128; e.depth1 = 2048; e.depth_flow = 64; e.max_sub = 43; e.flowid = 2; e.flow_isz = 56;
	e.buf_sz = 2048; e.idx_sz = 4; e.flow_open = 1;
	e.wr0 = 5; e.wr1 = 9; e.wrf = 0;
	e.depth_d2h[0] = 64; e.depth_d2h[1] = 256; e.depth_d2h[2] = 256;
	e.rd_d2h[0] = 6; e.rd_d2h[1] = 6; e.rd_d2h[2] = 8;
	return e;
}

/* ---- plans: every item and payload is an exact-size heap copy (AP-323); a plan owns its pointers until plan_free ---- */
struct plan { struct swdp_store s[SWDP_MAX_STORES_RXB + 4u]; uint32_t n; uint8_t *own[2]; };

static uint8_t *dup(const uint8_t *p, size_t n)
{
	uint8_t *q = malloc(n);

	if (q == NULL)
		abort();
	memcpy(q, p, n);
	return q;
}
static void plan_free(struct plan *p) { free(p->own[0]); free(p->own[1]); p->own[0] = p->own[1] = NULL; }

static void plan_fc(const struct swdp_env *e, struct plan *p, uint32_t epoch)
{
	uint8_t it[64];

	swdp_build_flow_create(it, epoch, 0, e->flowid, 0, GWM, MAC, e->flow.pa, e->depth_flow, e->flow_isz);
	p->own[0] = dup(it, SWDP_ISZ_CTRL_SUB);
	p->own[1] = NULL;
	p->n = swdp_plan_flow_create(e, e->wr0, (e->wr0 + 1) % e->depth0, p->own[0], p->s);
}
static void plan_rx(const struct swdp_env *e, struct plan *p, uint32_t buf)
{
	uint8_t it[64];

	swdp_build_rxpost(it, 1, SWDP_RX_RID + buf, e->rxbuf.pa + (uint64_t)buf * e->buf_sz, e->buf_sz);
	p->own[0] = dup(it, SWDP_ISZ_RXPOST);
	p->own[1] = NULL;
	p->n = swdp_plan_post(e, 1, e->ring1.pa, SWDP_ISZ_RXPOST, e->wr1, (e->wr1 + 1) % e->depth1, p->own[0], p->s);
}
/* patch 0357: n rx posts as ONE plan [ITEM x n, WRIDX, DB] at the slots wr1 .. wr1 + n - 1 (heap copy of the n items) */
static void plan_rxb(const struct swdp_env *e, struct plan *p, uint32_t n, uint32_t first_buf)
{
	uint8_t *it = malloc((size_t)n * SWDP_ISZ_RXPOST);
	uint32_t j;

	if (it == NULL)
		abort();
	for (j = 0; j < n; j++)
		swdp_build_rxpost(it + (size_t)j * SWDP_ISZ_RXPOST, 1, SWDP_RX_RID + first_buf + j, e->rxbuf.pa + (uint64_t)(first_buf + j) * e->buf_sz, e->buf_sz);
	p->own[0] = it;
	p->own[1] = NULL;
	p->n = swdp_plan_rxpost_n(e, e->wr1, n, (e->wr1 + n) % e->depth1, p->own[0], p->s);
}
static uint8_t frame_canary[1514];
static void plan_tx(const struct swdp_env *e, struct plan *p, uint32_t buf, uint32_t flen)
{
	uint8_t it[64];
	uint64_t txa = e->txbuf.pa + (uint64_t)buf * e->buf_sz;
	uint32_t i;

	for (i = 0; i < sizeof frame_canary; i++)
		frame_canary[i] = (uint8_t)(i * 7u + 3u);
	swdp_build_txpost(it, e->flow_isz, 0, SWDP_TX_RID + buf, frame_canary, flen, 0, txa);
	p->own[0] = dup(it, e->flow_isz);
	p->own[1] = dup(frame_canary + SWDP_ETH_HLEN, flen - SWDP_ETH_HLEN);
	p->n = swdp_plan_tx(e, txa, p->own[1], flen - SWDP_ETH_HLEN, e->wrf, (e->wrf + 1) % e->depth_flow, p->own[0], p->s);
}
static void plan_cons(const struct swdp_env *e, struct plan *p, uint32_t ring)
{
	p->own[0] = p->own[1] = NULL;
	p->n = swdp_plan_consume(e, ring, (e->rd_d2h[ring - 2u] + 1u) % e->depth_d2h[ring - 2u], p->s);
}

/* ---- the expected bus trace of a plan, written here literally ---- */
static int where(uint64_t pa, int *sp, uint32_t *off)
{
	if (pa >= G1PA && pa < G1PA + G1LEN) {
		*sp = SWG_SP_HOST;
		*off = (uint32_t)(pa - G1PA);
		return 0;
	}
	if (pa >= DAPA && pa < DAPA + DALEN) {
		*sp = SWG_SP_DATA;
		*off = (uint32_t)(pa - DAPA);
		return 0;
	}
	return -1;
}
static uint32_t le32(const uint8_t *p, uint32_t left)
{
	uint32_t v = 0u, i;

	for (i = 0; i < 4u && i < left; i++)
		v |= (uint32_t)p[i] << (8u * i);
	return v;
}
static void ex_word(struct acc *o, unsigned *n, int sp, uint32_t off, uint32_t v)
{
	o[(*n)++] = (struct acc){ 'R', sp, off, *mem(sp, off) };
	o[(*n)++] = (struct acc){ 'W', sp, off, v };
	o[(*n)++] = (struct acc){ 'R', sp, off, v };
}
/* the accesses of a plan, memory as it is NOW (the upper half of an index word is kept); room reads first */
static unsigned expect(const struct swdp_env *e, const struct plan *p, struct acc *o)
{
	unsigned n = 0u, i, k;
	int sp = 0;
	uint32_t off = 0u;

	for (i = 0; i < p->n; i++) {
		if (p->s[i].kind == SWDP_SK_ITEM) {
			where(H2DR + 4ull * swdp_h2d_slot(p->s[i].ring), &sp, &off);
			o[n++] = (struct acc){ 'R', sp, off, *mem(sp, off) };
		}
	}
	for (i = 0; i < p->n; i++) {
		const struct swdp_store *s = &p->s[i];

		if (s->kind == SWDP_SK_DB) {
			o[n++] = (struct acc){ 'W', SWG_SP_PCIE, (uint32_t)s->addr, s->value };
			continue;
		}
		where(s->addr, &sp, &off);
		if (s->kind == SWDP_SK_WRIDX || s->kind == SWDP_SK_RDIDX) {
			ex_word(o, &n, sp, off, (*mem(sp, off) & 0xFFFF0000u) | (s->value & 0xFFFFu));
			continue;
		}
		for (k = 0; k < (s->len + 3u) / 4u; k++)
			ex_word(o, &n, sp, off + 4u * k, le32(s->data + 4u * k, s->len - 4u * k));
	}
	(void)e;
	return n;
}
static int same(const struct acc *a, unsigned na, const struct acc *b, unsigned nb)
{
	unsigned i;

	if (na != nb)
		return 0;
	for (i = 0; i < na; i++)
		if (a[i].k != b[i].k || a[i].sp != b[i].sp || a[i].off != b[i].off || a[i].val != b[i].val)
			return 0;
	return 1;
}
static int has(const char *s) { return strstr(logbuf, s) != NULL; }

static struct acc E[8192];
/* run a plan against the world; returns the exec result */
static int run(struct swdp_env *e, const struct plan *p, const char *name, int secret) { return swg_dp_exec(&x, e, p->s, p->n, name, secret); }
/* a failed clean run: rc, the first access that differs, the log */
static void diag(const char *name, int rc, unsigned ne)
{
	unsigned i;

	fprintf(stderr, "  diag %s: rc=%d accesses=%u expected=%u pcie_reads=%u bad_sp=%u\n", name, rc, nA, ne, J.pcie_reads, J.bad_sp);
	for (i = 0; i < nA && i < ne; i++) {
		if (A[i].k != E[i].k || A[i].sp != E[i].sp || A[i].off != E[i].off || A[i].val != E[i].val) {
			fprintf(stderr, "  first diff at %u: got %c sp%d %#x %#x want %c sp%d %#x %#x\n", i, A[i].k, A[i].sp, A[i].off, A[i].val, E[i].k, E[i].sp, E[i].off, E[i].val);
			break;
		}
	}
	fprintf(stderr, "%s", logbuf);
}
/* a clean run: rc 0 and the bus trace is exactly the expected one */
static int clean(struct swdp_env *e, const struct plan *p, const char *name, int secret)
{
	unsigned ne = expect(e, p, E);
	int rc;

	nA = 0u;                                        /* one trace per call (a test may make several in one world) */
	J.nw = 0u;
	rc = run(e, p, name, secret);

	if (rc == 0 && same(A, nA, E, ne) && J.pcie_reads == 0u && J.bad_sp == 0u)
		return 1;
	diag(name, rc, ne);
	return 0;
}

/* ---- tests ---- */
static void test_shapes(void)
{
	struct swdp_env e = mkenv();
	struct plan p;
	uint32_t r;

	world_reset();                                  /* flow create: ring 0 at d4wr 5, epoch swg_ep(5) */
	e.flow_open = 0;
	plan_fc(&e, &p, swg_ep(5));
	CHECK(p.n == 4u && clean(&e, &p, "fc", 0));
	CHECK(x.d4wr == 6u && x.eop == SWG_OP_NONE && x.dpx == 0u && x.nstore == 13u);   /* d4wr moved with the ring-0 index store; the engine counted 1 + 10 + 1 + 1 puts */
	CHECK(has("SWEET WLAN: DP15 plan fc done: 4 stores\n"));
	plan_free(&p);

	world_reset();                                  /* ring-1 rxpost */
	e = mkenv();
	plan_rx(&e, &p, 3);
	CHECK(p.n == 3u && clean(&e, &p, "rx", 0));
	CHECK(x.d4wr == 5u && sh_r1[9u * 8u] != 0u);
	plan_free(&p);

	world_reset();                                  /* tx: payload 61 B (odd: the tail word is zero padded), flow post */
	e = mkenv();
	plan_tx(&e, &p, 2, 75);
	CHECK(p.n == 4u && p.s[0].len == 61u && clean(&e, &p, "tx", 0));
	CHECK(has("SWEET WLAN: DP15 payload 61 B\n") && sh_tx[2u * 512u + 15u] == le32(frame_canary + 14 + 60, 1u));
	plan_free(&p);

	for (r = 2; r <= 4; r++) {                      /* consume rings 2, 3, 4 */
		world_reset();
		e = mkenv();
		plan_cons(&e, &p, r);
		CHECK(p.n == 2u && clean(&e, &p, "cons", 0));
		CHECK(x.d4rd == (r == 2u ? 71u : 70u));
		plan_free(&p);
	}
}

static void test_env_from_engine(void)
{
	struct swdp_env e = mkenv();
	struct plan p;
	unsigned b;
	int rc;

	/* clause 1: the engine's cursor is 7, the caller's env says 5: the plan built for slot 7 runs (wr0 := d4wr) */
	world_reset();
	set_wr0(7u);
	e.flow_open = 0;
	e.wr0 = 7u;
	plan_fc(&e, &p, swg_ep(7));
	e.wr0 = 5u;                                     /* the caller's stale copy */
	CHECK(clean(&e, &p, "fc7", 0) && e.wr0 == 7u && x.d4wr == 8u);
	plan_free(&p);
	/* ... and a plan built for the caller's 5 while the engine is at 7 is refused with no access */
	world_reset();
	set_wr0(7u);
	e = mkenv();
	e.flow_open = 0;
	plan_fc(&e, &p, swg_ep(5));
	rc = run(&e, &p, "fc5", 0);
	CHECK(rc == -SWG_E_ORDER && nA == 0u && x.d4dstop == 1u && has("WIFI GAP: DP15 plan fc5 refused: order\n"));
	plan_free(&p);
	/* the ring-2 read cursor is the engine's too: d4rd 70 -> slot 6; a plan built for the caller's 9 is refused */
	world_reset();
	e = mkenv();
	e.rd_d2h[0] = 9u;
	plan_cons(&e, &p, 2);
	rc = run(&e, &p, "c2", 0);
	CHECK(rc == -SWG_E_ORDER && nA == 0u);
	plan_free(&p);
	/* the env must name the engine's own blocks, rings and index arrays */
	world_reset();
	e = mkenv();
	e.h2d_wr.pa += 0x100u;
	plan_rx(&e, &p, 0);
	CHECK(run(&e, &p, "rxenv", 0) == -SWG_E_ORDER && nA == 0u && has("refused: env is not the engine's\n"));
	plan_free(&p);
	world_reset();
	e = mkenv();
	e.depth_d2h[0] = 128;
	plan_cons(&e, &p, 3);
	CHECK(run(&e, &p, "cdep", 0) == -SWG_E_ORDER && nA == 0u && has("env is not the engine's"));
	plan_free(&p);
	/* every binding on its own: a consistent envelope that names a different block, ring or index array than the engine's is refused */
	for (b = 0; b < 6u; b++) {
		world_reset();
		e = mkenv();
		e.g1.len = b == 0u ? 0xF0000u : e.g1.len;
		e.h2d_wr.pa += b == 1u ? 0x100u : 0u;
		e.d2h_rd.pa += b == 2u ? 0x100u : 0u;
		e.ring0.pa += b == 3u ? 0x2000u : 0u;
		e.ring1.pa += b == 4u ? 0x20000u : 0u;
		e.depth0 = b == 5u ? 64u : e.depth0;
		if (b == 2u)
			plan_cons(&e, &p, 3);
		else
			plan_rx(&e, &p, 0);
		CHECK(swdp_env_ok(&e) == 0 && swdp_audit(&e, p.s, p.n) == 0);
		CHECK(run(&e, &p, "bind", 0) == -SWG_E_ORDER && nA == 0u && has("refused: env is not the engine's\n"));
		plan_free(&p);
	}
	/* clause 1: ring-0 epoch = swg_ep(slot) */
	world_reset();
	e = mkenv();
	e.flow_open = 0;
	plan_fc(&e, &p, swg_ep(5) + 1u);
	rc = run(&e, &p, "fcep", 0);
	if (rc != -SWG_E_ORDER)
		fprintf(stderr, "  fcep rc=%d nA=%u\n%s", rc, nA, logbuf);
	CHECK(rc == -SWG_E_ORDER && nA == 0u && has("ring-0 epoch is not swg_ep0"));
	plan_free(&p);
}

static void test_audit_first(void)
{
	struct swdp_env e = mkenv();
	struct plan p;
	int rc;

	world_reset();                                  /* a tampered doorbell: the audit refuses, no access at all, one GAP line, the engine soft-stops */
	plan_rx(&e, &p, 1);
	p.s[2].value ^= 1u;
	rc = run(&e, &p, "bad", 0);
	CHECK(rc == -SWG_E_ORDER && nA == 0u && x.d4dstop == 1u && !x.stopped && has("WIFI GAP: DP15 plan bad refused: value\n"));
	plan_free(&p);
	world_reset();                                  /* an inconsistent envelope */
	e = mkenv();
	plan_rx(&e, &p, 1);
	e.buf_sz = 100;
	CHECK(run(&e, &p, "badenv", 0) == -SWG_E_ORDER && nA == 0u && has("refused: range\n"));
	plan_free(&p);
	world_reset();                                  /* the audit refuses a payload of a plan with no flow_open */
	e = mkenv();
	e.flow_open = 0;
	plan_tx(&e, &p, 0, 60);
	CHECK(run(&e, &p, "noflow", 0) == -SWG_E_ORDER && nA == 0u && has("refused: order\n"));
	plan_free(&p);
	world_reset();                                  /* a stopped engine takes no plan */
	e = mkenv();
	plan_rx(&e, &p, 1);
	x.d4dstop = 1u;
	CHECK(run(&e, &p, "after", 0) == -SWG_E_ORDER && nA == 0u && has("refused: engine stopped\n"));
	plan_free(&p);
}

static void test_one_copy(void)
{
	struct swdp_env e = mkenv();
	struct plan p;
	unsigned ne;
	int rc;

	/* clause 3: the bus overwrites the plan's source buffers after the first store; the stores that follow still carry the audited bytes */
	world_reset();
	plan_tx(&e, &p, 1, 60);
	ne = expect(&e, &p, E);
	J.tamper = p.own[0];
	J.tamper_len = e.flow_isz;
	rc = run(&e, &p, "tx1", 0);
	CHECK(rc == 0 && same(A, nA, E, ne));
	plan_free(&p);
	world_reset();
	e = mkenv();
	plan_tx(&e, &p, 1, 60);
	ne = expect(&e, &p, E);
	J.tamper = p.own[1];
	J.tamper_len = 46u;
	rc = run(&e, &p, "tx2", 0);
	CHECK(rc == 0 && same(A, nA, E, ne));
	plan_free(&p);
}

/* every store of the pool passes the single-store audit; an accepted sequence must run with the exact trace, every other one must touch nothing */
static void test_enumerate(void)
{
	struct swdp_env e;
	struct plan fc, rx, tx, c2, c3, c4, q;
	const struct swdp_store *pool[17];
	unsigned np, len, idx[4], acc[2] = { 0u, 0u }, i, f, total = 0u, cnt, c, t, ne;
	int ok, rc;

	for (f = 0u; f < 2u; f++) {
		e = mkenv();
		e.flow_open = f;
		plan_fc(&e, &fc, swg_ep(5)); plan_rx(&e, &rx, 3); plan_tx(&e, &tx, 2, 60);
		plan_cons(&e, &c2, 2); plan_cons(&e, &c3, 3); plan_cons(&e, &c4, 4);
		np = 0u;
		for (i = 0; i < fc.n; i++) pool[np++] = &fc.s[i];
		for (i = 0; i < rx.n; i++) pool[np++] = &rx.s[i];
		for (i = 0; i < tx.n; i++) pool[np++] = &tx.s[i];
		for (i = 0; i < c2.n; i++) pool[np++] = &c2.s[i];
		for (i = 0; i < c3.n; i++) pool[np++] = &c3.s[i];
		for (i = 0; i < c4.n; i++) pool[np++] = &c4.s[i];
		CHECK(np == 17u);
		for (len = 1u; len <= 4u; len++) {
			cnt = 1u;
			for (i = 0; i < len; i++) cnt *= np;
			for (c = 0u; c < cnt; c++) {
				for (i = 0, t = c; i < len; i++, t /= np) idx[i] = t % np;
				q.n = len;
				for (i = 0; i < len; i++) q.s[i] = *pool[idx[i]];
				world_reset();
				e = mkenv();
				e.flow_open = f;
				ok = swdp_audit(&e, q.s, q.n) == 0;
				ne = ok ? expect(&e, &q, E) : 0u;
				rc = swg_dp_exec(&x, &e, q.s, q.n, "enum", 0);
				total++;
				if (ok) {
					acc[f]++;
					CHECK(rc == 0 && same(A, nA, E, ne));
				} else {
					CHECK(rc == -SWG_E_ORDER && nA == 0u);
				}
			}
		}
		plan_free(&fc); plan_free(&rx); plan_free(&tx); plan_free(&c2); plan_free(&c3); plan_free(&c4);
	}
	CHECK(acc[0] == 5u && acc[1] == 5u);            /* flow create + rxpost + 3 consumes without flow_open; rxpost + tx + 3 consumes with it: the 6 legal operations */
	CHECK(total > 40000u);
	world_reset();
}

/* the plan-store index (1-based) a bus store belongs to, from the plan's own layout */
static unsigned store_of(const struct plan *p, unsigned wr_k)
{
	unsigned i, k, n = 0u;

	for (i = 0; i < p->n; i++) {
		k = p->s[i].kind == SWDP_SK_DB ? 1u : p->s[i].kind == SWDP_SK_WRIDX || p->s[i].kind == SWDP_SK_RDIDX ? 1u : (p->s[i].len + 3u) / 4u;
		if (wr_k < n + k)
			return i + 1u;
		n += k;
	}
	return 0u;
}

/* clauses 5, 6: fault injection at every bus store of the tx plan (payload 61 B, 4 plan stores): a write error, an old-value mismatch, a read-back mismatch, an all-ones word */
static void test_faults(void)
{
	struct swdp_env e;
	struct plan p;
	unsigned total, k, nw, i, j, ne, hits = 0u;
	uint32_t keep;
	int rc, kind;
	const int want[4] = { SWG_E_WR, SWG_E_OLD, SWG_E_READBACK, SWG_E_DEAD };
	char line[128];

	world_reset();
	e = mkenv();
	plan_tx(&e, &p, 2, 75);
	ne = expect(&e, &p, E);
	for (i = 0, total = 0u; i < ne; i++)
		total += E[i].k == 'W';
	plan_free(&p);
	for (kind = 0; kind < 4; kind++) {
		for (k = 0; k < total; k++) {
			world_reset();
			e = mkenv();
			plan_tx(&e, &p, 2, 75);
			ne = expect(&e, &p, E);
			for (i = 0, nw = 0u, j = 0u; i < ne; i++) {          /* the k-th written word */
				if (E[i].k == 'W') {
					j = nw++ == k ? i : j;
				}
			}
			if (kind != 0 && E[j].sp == SWG_SP_PCIE) {          /* the doorbell is never read: no old, no read-back to fail */
				plan_free(&p);
				continue;
			}
			J.fail_wr = kind == 0 ? k + 1u : 0u;
			J.corrupt_rb = kind == 2 ? k + 1u : 0u;
			keep = E[j].sp == SWG_SP_PCIE ? 0u : *mem(E[j].sp, E[j].off);
			if (kind == 1) *mem(E[j].sp, E[j].off) ^= 0x40u;     /* low bit flip: differs from the shadow (0) and from the cursor in the low 16 bits */
			if (kind == 3) *mem(E[j].sp, E[j].off) = 0xFFFFFFFFu;
			rc = run(&e, &p, "tx", 0);
			if (kind == 1 || kind == 3)
				*mem(E[j].sp, E[j].off) = keep;                  /* the corruption is not in the undo log */
			hits++;
			CHECK(rc == -want[kind] && x.stopped == 1 && x.d4dstop == 1u && J.pcie_reads == 0u);
			snprintf(line, sizeof line, "WIFI GAP: DP15 store %u/4 of plan tx failed code=%s\n", store_of(&p, k), swg_ename[want[kind]]);
			CHECK(has(line));
			if (J.nw != (kind == 1 || kind == 3 ? k : k + 1u))
				fprintf(stderr, "  fault kind %d k %u: rc %d nw %u store_of %u\n", kind, k, rc, J.nw, store_of(&p, k));
			CHECK(J.nw == (kind == 1 || kind == 3 ? k : k + 1u));       /* no retry, nothing after the failing store */
			for (i = 0; i < nA; i++)
				CHECK(!(A[i].k == 'W' && A[i].sp == SWG_SP_PCIE));         /* no doorbell after a failed store of the same plan */
			plan_free(&p);
		}
	}
	CHECK(hits > 100u);
	/* a failure of the doorbell store itself: 4/4, the earlier stores stay made */
	world_reset();
	e = mkenv();
	plan_rx(&e, &p, 0);
	J.fail_wr = 8u + 1u + 1u;
	CHECK(run(&e, &p, "rx", 0) == -SWG_E_WR && has("WIFI GAP: DP15 store 3/3 of plan rx failed code=store\n"));
	/* no further plan after a failure */
	nA = 0u;
	CHECK(run(&e, &p, "rx2", 0) == -SWG_E_ORDER && nA == 0u && has("WIFI GAP: DP15 plan rx2 refused: engine stopped\n"));
	plan_free(&p);
}

/* patch 0357: the batched rx post through the executor: the bus trace is the items' words in order, ONE index store, ONE doorbell; every slot's shadow is updated; faults at any store stop it with no doorbell */
static void test_rx_batch(void)
{
	static const uint32_t ns[] = { 1u, 2u, 5u, 31u, 32u };
	struct swdp_env e;
	struct plan p;
	unsigned i, k, ne, nw, j, db, hits = 0u;
	uint32_t s0;
	int rc;

	for (i = 0; i < sizeof ns / sizeof ns[0]; i++) {
		world_reset();
		e = mkenv();
		e.rx_slots = 40u; e.rxbuf.len = 40u * 2048u;
		plan_rxb(&e, &p, ns[i], 7u);
		CHECK(p.n == ns[i] + 2u && clean(&e, &p, "rxb", 0));
		CHECK(x.d4wr == 5u && x.eop == SWG_OP_NONE && x.dpx == 0u && has("done: ") && !x.d4dstop);
		for (k = 0, db = 0u; k < nA; k++)
			db += A[k].k == 'W' && A[k].sp == SWG_SP_PCIE;
		CHECK(db == 1u);                                                     /* ONE doorbell for the batch */
		CHECK((g1[(H2DW - G1PA) / 4u + 1u] & 0xFFFFu) == 9u + ns[i] && sh_r1[9u * 8u] != 0u && sh_r1[(9u + ns[i] - 1u) * 8u + 7u] == (uint32_t)(DAPA >> 32) + 0u);       /* the index moved by n; the shadows of the first and the last item are filled */
		plan_free(&p);
	}
	/* the same plan for 7 and for n single plans leaves the same ring words (the batch is not a different message) */
	{
		static uint32_t one[40u * 8u], bat[40u * 8u];

		world_reset(); e = mkenv(); e.rx_slots = 40u; e.rxbuf.len = 40u * 2048u;
		plan_rxb(&e, &p, 7u, 3u); CHECK(clean(&e, &p, "rxb", 0)); plan_free(&p);
		memcpy(bat, &g1[(RING1 - G1PA) / 4u + 9u * 8u], 7u * 32u);
		world_reset(); e = mkenv(); e.rx_slots = 40u; e.rxbuf.len = 40u * 2048u;
		for (j = 0; j < 7u; j++) { plan_rx(&e, &p, 3u + j); CHECK(clean(&e, &p, "rx", 0)); plan_free(&p); e.wr1 = (e.wr1 + 1u) % e.depth1; }
		memcpy(one, &g1[(RING1 - G1PA) / 4u + 9u * 8u], 7u * 32u);
		CHECK(memcmp(one, bat, 7u * 32u) == 0);
	}
	/* a store fault anywhere in a 3-item batch (write error, old-word mismatch, read-back mismatch, all-ones word): the engine stops, no retry, no doorbell */
	world_reset();
	e = mkenv();
	plan_rxb(&e, &p, 3u, 0u);
	ne = expect(&e, &p, E);
	for (i = 0, nw = 0u; i < ne; i++)
		nw += E[i].k == 'W';
	plan_free(&p);
	for (j = 0; j < 4u; j++) {
		for (k = 0; k < nw; k++) {
			uint32_t keep, jj = 0u;
			unsigned q, c;

			world_reset();
			e = mkenv();
			plan_rxb(&e, &p, 3u, 0u);
			ne = expect(&e, &p, E);
			for (q = 0, c = 0u; q < ne; q++)
				if (E[q].k == 'W')
					jj = c++ == k ? q : jj;
			if (j != 0u && E[jj].sp == SWG_SP_PCIE) { plan_free(&p); continue; }
			J.fail_wr = j == 0u ? k + 1u : 0u;
			J.corrupt_rb = j == 2u ? k + 1u : 0u;
			keep = E[jj].sp == SWG_SP_PCIE ? 0u : *mem(E[jj].sp, E[jj].off);
			if (j == 1u) *mem(E[jj].sp, E[jj].off) ^= 0x40u;
			if (j == 3u) *mem(E[jj].sp, E[jj].off) = 0xFFFFFFFFu;
			rc = run(&e, &p, "rxb", 0);
			if (j == 1u || j == 3u) *mem(E[jj].sp, E[jj].off) = keep;
			hits++;
			CHECK(rc != 0 && x.stopped == 1 && x.d4dstop == 1u);
			for (q = 0; q < nA; q++)
				CHECK(!(A[q].k == 'W' && A[q].sp == SWG_SP_PCIE));                 /* no doorbell after a failed store of the batch */
			CHECK((g1[(H2DW - G1PA) / 4u + 1u] & 0xFFFFu) == 9u || k + 2u >= nw);                   /* a fault before the index store leaves the index alone */
			plan_free(&p);
		}
	}
	CHECK(hits > 60u);
	/* refused before the first store: a hole in the slots, 33 items, a batch that would run into the dongle's read index, the ring end */
	world_reset(); e = mkenv(); plan_rxb(&e, &p, 4u, 0u); p.s[2].slot += 1u; p.s[2].addr += 32u;
	nA = 0u; CHECK(run(&e, &p, "rxb", 0) == -SWG_E_ORDER && nA == 0u && has("WIFI GAP: DP15 plan rxb refused: order\n")); plan_free(&p);
	world_reset(); e = mkenv(); plan_rxb(&e, &p, 33u, 0u);
	nA = 0u; CHECK(run(&e, &p, "rxb", 0) == -SWG_E_ORDER && nA == 0u); plan_free(&p);
	world_reset(); e = mkenv(); e.rx_slots = 40u; e.rxbuf.len = 40u * 2048u;
	g1[(H2DR - G1PA) / 4u + 1u] = (9u + 10u) % 2048u;                              /* the dongle's read index: the 10th item of the batch would make the ring look empty */
	plan_rxb(&e, &p, 10u, 0u);
	nA = 0u; CHECK(run(&e, &p, "rxb", 0) == -SWG_E_ORDER && has("ring full"));
	for (i = 0; i < nA; i++)
		CHECK(A[i].k == 'R');                                                          /* the room checks only read: no store of the refused batch */
	plan_free(&p);
	g1[(H2DR - G1PA) / 4u + 1u] = 0u;
	world_reset(); e = mkenv(); e.wr1 = 2045u; g1[(H2DW - G1PA) / 4u + 1u] = 2045u;
	plan_rxb(&e, &p, 5u, 0u);                                                         /* slots 2045 .. 2049: across the ring end */
	nA = 0u; CHECK(run(&e, &p, "rxb", 0) != 0 && nA == 0u); plan_free(&p);
	world_reset(); e = mkenv(); e.wr1 = 2045u; g1[(H2DW - G1PA) / 4u + 1u] = 2045u; g1[(H2DR - G1PA) / 4u + 1u] = 2040u;     /* the dongle has read up to slot 2039 */
	plan_rxb(&e, &p, 3u, 0u);                                                         /* exactly to the end: new index 0 */
	CHECK(p.s[3].value == 0u && clean(&e, &p, "rxb", 0) && (g1[(H2DW - G1PA) / 4u + 1u] & 0xFFFFu) == 0u);
	plan_free(&p);
	s0 = x.nstore; (void)s0;
}

/* clause 7: a secret plan prints no value; a plain plan prints the item words but never a frame word */
static void test_secret(void)
{
	struct swdp_env e = mkenv();
	struct plan p;
	char hex[16];
	unsigned i, w;

	world_reset();
	plan_tx(&e, &p, 0, 14 + 64);                    /* PTK/GTK/TK/PMK canaries planted in the payload */
	memcpy(p.own[1], "PMKcanaryPMKcanaryPTKcanaryPTKcanaryGTKcanaryGTKcanaryTK_canaryTK_canaryTK_canaryTK_canar", 64);
	CHECK(p.s[0].data == p.own[1]);
	CHECK(run(&e, &p, "ptk", 1) == 0);
	CHECK(!has("canary") && !has("PMK") && !has("PTK") && !has("GTK"));
	for (i = 0; i < 9u; i++) {
		w = le32((const uint8_t *)"PMKcanaryPMKcanaryPTKcanaryPTKcanary" + 4u * i, 4u);
		snprintf(hex, sizeof hex, "%x", w);
		CHECK(!has(hex));
		snprintf(hex, sizeof hex, "%X", w);
		CHECK(!has(hex));
	}
	CHECK(has("dp15 item word host ") && has("(value withheld)\n") && !has("read back"));
	CHECK(has("SWEET WLAN: DP15 payload 64 B\n"));
	plan_free(&p);
	world_reset();
	e = mkenv();
	plan_tx(&e, &p, 0, 14 + 64);
	memcpy(p.own[1], "PMKcanaryPMKcanaryPTKcanaryPTKcanaryGTKcanaryGTKcanaryTK_canaryTK_canaryTK_canaryTK_canar", 64);
	CHECK(run(&e, &p, "plain", 0) == 0);
	CHECK(!has("canary") && !has("PMK") && has("read back") && !has("(value withheld)") && has("SWEET WLAN: DP15 payload 64 B\n"));
	for (i = 0; i < 9u; i++) {                      /* a plain plan prints its item words, never a frame word */
		w = le32((const uint8_t *)"PMKcanaryPMKcanaryPTKcanaryPTKcanary" + 4u * i, 4u);
		snprintf(hex, sizeof hex, "%x", w);
		CHECK(!has(hex));
	}
	plan_free(&p);
}

/* clauses 2, 5: the ring-full check (r2 N1), ring-0 wrap, a read index outside the ring, the old-value shadow, the 16-bit index compare */
static void test_state(void)
{
	struct swdp_env e = mkenv();
	struct plan p;
	int rc;

	world_reset();                                  /* ring 1: wr1 9, read index 10 -> the next post fills the ring */
	g1[(H2DR - G1PA) / 4u + 1u] = 10u;
	plan_rx(&e, &p, 0);
	rc = run(&e, &p, "full", 0);
	CHECK(rc == -SWG_E_ORDER && has("WIFI GAP: DP15 plan full refused: ring full\n") && x.d4dstop == 1u);
	CHECK(J.nw == 0u);
	plan_free(&p);
	g1[(H2DR - G1PA) / 4u + 1u] = 0u;
	world_reset();                                  /* read index 9 (empty ring would be wr == rd; one slot ahead is room) */
	e = mkenv();
	g1[(H2DR - G1PA) / 4u + 1u] = 9u;
	plan_rx(&e, &p, 0);
	CHECK(clean(&e, &p, "room", 0));
	plan_free(&p);
	g1[(H2DR - G1PA) / 4u + 1u] = 0u;
	world_reset();                                  /* read index outside the ring */
	e = mkenv();
	g1[(H2DR - G1PA) / 4u + 1u] = 2048u;
	plan_rx(&e, &p, 0);
	CHECK(run(&e, &p, "rdout", 0) == -SWG_E_ORDER && has("refused: H2D read index outside the ring\n") && J.nw == 0u);
	plan_free(&p);
	g1[(H2DR - G1PA) / 4u + 1u] = 0u;
	world_reset();                                  /* sprint 2026-11-08: ring 0 at its last slot wraps (127 -> 0); the dongle's read index 127 = the ring would be full */
	e = mkenv();
	set_wr0(127u);
	e.flow_open = 0;
	e.wr0 = 127u;
	g1[(H2DR - G1PA) / 4u] = 0u;
	plan_fc(&e, &p, swg_ep(127));
	CHECK(run(&e, &p, "wrap-full", 0) == -SWG_E_ORDER && has("refused: ring full\n") && J.nw == 0u);
	plan_free(&p);
	world_reset();
	e = mkenv();
	set_wr0(127u);
	e.flow_open = 0;
	e.wr0 = 127u;
	g1[(H2DR - G1PA) / 4u] = 100u;
	plan_fc(&e, &p, swg_ep(127));
	CHECK(run(&e, &p, "wrap", 0) == 0 && x.d4wr == 0u && x.d4wrap0 == 1u);
	plan_free(&p);
	g1[(H2DR - G1PA) / 4u] = 0u;
	/* clause 5, ring 0 on its second lap (2026-11-10, kills ring0-shadow-old-zero): slot 127 still holds the item of the last lap; the old word of each
	   item store is the ring-0 shadow (x.r0sh), not 0. Memory changed behind the shadow's back is an OLD stop. */
	{
		const unsigned w0 = (unsigned)((RING0 - G1PA) / 4u) + 127u * 10u, s0 = 127u * 10u;
		unsigned k;

		for (int lap = 0; lap < 2; lap++) {
			world_reset();
			e = mkenv();
			set_wr0(127u);
			e.flow_open = 0;
			e.wr0 = 127u;
			g1[(H2DR - G1PA) / 4u] = 100u;
			for (k = 0; k < 10u; k++) {
				g1[w0 + k] = x.r0sh[s0 + k] = 0xA5A50000u | k;
			}
			if (lap == 1)
				g1[w0 + 3u] ^= 1u;                  /* someone else wrote the slot */
			plan_fc(&e, &p, swg_ep(127));
			nA = 0u;
			rc = run(&e, &p, lap == 0 ? "lap2" : "lap2-old", 0);
			if (lap == 0)
				CHECK(rc == 0 && x.d4wr == 0u);
			else
				CHECK(rc == -SWG_E_OLD && has("failed code=old value\n"));
			plan_free(&p);
			world_reset();
			for (k = 0; k < 10u; k++) {
				g1[w0 + k] = 0u;
			}
			g1[(H2DR - G1PA) / 4u] = 0u;
		}
	}
	/* clause 5: the flow ring wraps (cursor 63 -> 0) and the same flow slot and tx buffer are written a second time: the old value is the shadow of the first write; memory changed behind the shadow's back is an OLD stop */
	world_reset();
	e = mkenv();
	e.wrf = 63;
	g1[(H2DW - G1PA) / 4u + 2u] = 63u;
	g1[(H2DR - G1PA) / 4u + 2u] = 10u;               /* the dongle has read up to slot 10: slot 63 -> 0 is room */
	plan_tx(&e, &p, 1, 60);
	CHECK(clean(&e, &p, "tx-a", 0) && g1[(H2DW - G1PA) / 4u + 2u] == 0u);
	plan_free(&p);
	g1[(H2DW - G1PA) / 4u + 2u] = 63u;               /* the same slot again (the index array would hold 63 after another lap) */
	plan_tx(&e, &p, 1, 70);
	CHECK(clean(&e, &p, "tx-b", 0));
	plan_free(&p);
	world_reset();
	e = mkenv();
	e.wrf = 63;
	g1[(H2DW - G1PA) / 4u + 2u] = 63u;
	g1[(H2DR - G1PA) / 4u + 2u] = 10u;               /* the dongle has read up to slot 10: slot 63 -> 0 is room */
	plan_tx(&e, &p, 1, 60);
	CHECK(clean(&e, &p, "tx-a", 0));
	plan_free(&p);
	g1[(H2DW - G1PA) / 4u + 2u] = 63u;
	plan_tx(&e, &p, 1, 70);
	dat[(e.txbuf.pa - DAPA) / 4u + 512u] ^= 1u;      /* someone else wrote the buffer */
	nA = 0u;
	CHECK(run(&e, &p, "tx-c", 0) == -SWG_E_OLD && has("failed code=old value\n"));
	plan_free(&p);
	dat[(e.txbuf.pa - DAPA) / 4u + 512u] ^= 1u;
	/* a region with no shadow is an address stop, loud */
	world_reset();
	e = mkenv();
	x.dpsh[2].w = NULL;
	plan_tx(&e, &p, 1, 60);
	CHECK(run(&e, &p, "noshadow", 0) == -SWG_E_ADDR && has("failed code=address\n"));
	plan_free(&p);
	/* the 16-bit compare: the upper half of an index word is not ours; it is kept, and the old compare ignores it */
	world_reset();
	e = mkenv();
	g1[(H2DW - G1PA) / 4u + 1u] = 0xABCD0000u | 9u;
	plan_rx(&e, &p, 0);
	CHECK(clean(&e, &p, "hi16", 0) && g1[(H2DW - G1PA) / 4u + 1u] == (0xABCD0000u | 10u));
	plan_free(&p);
	g1[(H2DW - G1PA) / 4u + 1u] = 0u;
}

int main(void)
{
	(void)swg_go;                                    /* the header's other entry point, unused by this test */
	test_shapes();
	test_env_from_engine();
	test_audit_first();
	test_one_copy();
	test_enumerate();
	test_faults();
	test_rx_batch();
	test_secret();
	test_state();
	world_reset();
	if (fails) {
		fprintf(stderr, "RESULT: FAIL wlan_dp15_host_test (%u checks)\n", fails);
		return 1;
	}
	printf("RESULT: PASS wlan_dp15_host_test\n");
	return 0;
}
