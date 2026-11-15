/* Host test for pmicsrc.c (--pmic-src, docs/research/139) against the FAKE APM of acpm_host_test.c (#included, its main renamed): the six READ requests
 * (S2MPG10 channel 0 type 1: 0xC..0xF; S2MPG11 channel 1 type 1: 0xC, 0xD; FUNC_READ = 0), the printed lines, a failed read printed as ERR (never a value), refusals
 * with zero IPC traffic, the allowlist of acpm_pmic_src_read (INT1..INT6, a touch rail, a wrong chip, S2MPG11 0xC..0xF other than 0xC/0xD all refused with zero writes) and
 * that every write in this binary is an IPC request-slot / queue / doorbell word (the PMIC fake is never modified). pmicsrc.c has no write path of its own.
 * Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -I. -Iout -include out/board_config.h -o out/pmicsrc_host_test pmicsrc_host_test.c */
#define main acpm_host_main
#include "acpm_host_test.c"
#undef main
#include "pmicsrc.c"

static char lines[64][RAMLOG_LINE_MAX + 8];
static unsigned nl;
static char tok[PMIC_SRC_TOK_MAX];
char pmic_src_tok[PMIC_SRC_TOK_MAX];

void con_str(const char *label, const char *s)
{
	assert(nl < 64);
	assert(strlen(s) < RAMLOG_LINE_MAX);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); for (unsigned i_ = 0; i_ < nl; i_++) fprintf(stderr, "  %s\n", lines[i_]); assert(0); } } while (0)

static void world(void)
{
	setup(ESZ);
	nl = 0;
	pmic[0x0C] = 0x11;	/* STATUS1 */
	pmic[0x0D] = 0x22;	/* STATUS2 / S2MPG11 OFFSRC (the fake has one register file: values are checked through the request words) */
	pmic[0x0E] = 0x04;	/* PWRONSRC */
	pmic[0x0F] = 0x20;	/* OFFSRC */
}

static void only_ipc_writes(void)
{
	for (unsigned i = 0; i < nw; i++) {
		uintptr_t pa = wlog[i].pa;

		EXPECT((pa >= SRAM + REQ_BASE_O && pa < SRAM + REQ_BASE_O + QLEN * ESZ) || pa == SRAM + REQ_FRONT_O || pa == SRAM + RSP_REAR_O ||
		       pa == MBOX + ACPM_INTGR1 || pa == MBOX + ACPM_INTCR0);
	}
}

int main(void)
{
	static const uint32_t want_reg[6] = { 0x0C, 0x0D, 0x0E, 0x0F, 0x0C, 0x0D };
	static const uint32_t want_w0[6] = { 0x0C | 0x0100u, 0x0D | 0x0100u, 0x0E | 0x0100u, 0x0F | 0x0100u,
					     0x0C | ((uint32_t)ACPM_S11_PMIC_ADDR << 8) | ((uint32_t)ACPM_S11_SPEEDY << 12),
					     0x0D | ((uint32_t)ACPM_S11_PMIC_ADDR << 8) | ((uint32_t)ACPM_S11_SPEEDY << 12) };

	/* happy path: exactly six requests, FUNC_READ, the listed registers, nothing else written */
	world();
	EXPECT(pmic_src(tok, sizeof(tok)) == 0);
	EXPECT(has("SWEET PMIC SRC: s2mpg10 STATUS1=0x11 STATUS2=0x22 PWRONSRC=0x04 OFFSRC=0x20"));
	EXPECT(has("SWEET PMIC SRC: s2mpg11 STATUS1=0x11 OFFSRC=0x22 (read-only, not cleared)"));
	EXPECT(nl == 2 && nbell == 6 && nw == 6 * 8);
	{
		uint32_t w0[8], w1[8], n = 0;

		for (unsigned i = 0; i + 1 < nw; i++)
			if (wlog[i].pa >= SRAM + REQ_BASE_O && wlog[i].pa < SRAM + REQ_BASE_O + QLEN * ESZ && (wlog[i].pa - SRAM - REQ_BASE_O) % ESZ == 0) {
				w0[n] = wlog[i].v & 0xffffu;
				w1[n] = wlog[i + 1].v;
				n++;
			}
		EXPECT(n == 6);
		for (unsigned i = 0; i < 6; i++)
			EXPECT((w0[i] & 0xffu) == want_reg[i] && w0[i] == want_w0[i] && (w1[i] & 0xffu) == 0);	/* FUNC_READ = 0 */
	}
	only_ipc_writes();
	EXPECT(pmic[0x0C] == 0x11 && pmic[0x0D] == 0x22 && pmic[0x0E] == 0x04 && pmic[0x0F] == 0x20);	/* the fake PMIC was not modified */

	/* command-line tokens (pmicsrc.h): MSB-first bytes; the fake has one register file, so S11 OFFSRC (0x0D) reads 0x22 */
	EXPECT(!strcmp(tok, " sweetps10=0x11220420 sweetps11=0x1122 sweetpsrc=0") && strlen(tok) <= PMIC_SRC_TOK_MAX - 1);
	{
		char big[8 * PMIC_SRC_TOK_MAX];

		world(); pmic[0x0C] = 0xFF; pmic[0x0D] = 0xAB; pmic[0x0E] = 0xCD; pmic[0x0F] = 0xEF;	/* the worst-case width: every byte two digits */
		memset(big, 'Z', sizeof(big));
		EXPECT(pmic_src(big, sizeof(big)) == 0 && !strcmp(big, " sweetps10=0xFFABCDEF sweetps11=0xFFAB sweetpsrc=0") && strlen(big) == PMIC_SRC_TOK_MAX - 1);
		memset(big, 'Z', sizeof(big));
		world();
		EXPECT(pmic_src(big, PMIC_SRC_TOK_MAX - 1) == 0 && big[0] == 'Z');	/* a too small buffer: no token, nothing written */
	}

	/* the first failed read prints ERR and the rest "skipped": no made-up value */
	world(); mode = M_RET;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nbell == 1);
	EXPECT(!strcmp(tok, " sweetpsrc=1"));	/* index 0 failed: no value token at all */
	EXPECT(has("STATUS1=ERR ACPM_E_RET") && has("PMIC SRC FAIL why: ") && has("PMIC SRC STATE: ret=0x02 nothing left queued") && has("STATUS2=ERR skipped") && has("OFFSRC=ERR skipped") && !has("STATUS1=0x") && !has("OFFSRC=0x"));
	world(); mode = M_NEVER;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nbell == 1 && has("STATUS1=ERR ACPM_E_TIMEOUT") && !has("STATUS1=0x"));
	EXPECT(has("PMIC SRC FAIL why: ") && has("request stays queued, later ACPM steps get BUSY") && !strcmp(tok, " sweetpsrc=1"));

	/* a failure in the middle: the S10 token needs all four registers (failed index 3 = OFFSRC leaves none), the S11 token both (failed index 5 keeps ps10 only) */
	world(); fail_rd_n = 4;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && !strcmp(tok, " sweetpsrc=4") && has("OFFSRC=ERR ACPM_E_RET") && has("STATUS1=ERR skipped") && !has("OFFSRC=0x20") && nbell == 4);
	world(); fail_rd_n = 6;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && !strcmp(tok, " sweetps10=0x11220420 sweetpsrc=6") && nbell == 6);
	world(); fail_rd_n = 1;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && !strcmp(tok, " sweetpsrc=1"));

	/* ACPM gate refusals: zero IPC writes, every register ERR refused */
	world(); setS(REQ_FRONT_O, 0);	/* queue not empty */
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nw == 0 && nbell == 0 && has("STATUS1=ERR refused: ") && has("PWRONSRC=ERR refused: ") && !strcmp(tok, " sweetpsrc=7") && !has("STATUS1=0x") && !has("OFFSRC=0x"));
	world(); setup(8);		/* wrong element size */
	nl = 0;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nw == 0 && nbell == 0 && has("ERR refused: ") && !has("PMIC SRC FAIL") && !has("stays queued"));

	/* the allowlist: nothing but the six pairs; refusals send nothing */
	{
		struct acpm_trace t;
		uint32_t v = 0, r;

		world();
		t = trace();
		for (r = 0; r <= 0x1ff; r++) {	/* every register of both chips, plus a >8 bit alias */
			int ok_s10 = r >= 0x0C && r <= 0x0F, ok_s11 = r == 0x0C || r == 0x0D;

			if (!ok_s10) {
				EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, r, &v, &t) == ACPM_E_ARG && nw == 0);
			}
			if (!ok_s11) {
				EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S11, r, &v, &t) == ACPM_E_ARG && nw == 0);
			}
		}
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, 0x00, &v, &t) == ACPM_E_ARG && nw == 0);	/* INT1 */
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, 0x05, &v, &t) == ACPM_E_ARG && nw == 0);	/* INT6 */
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S11, 0x02, &v, &t) == ACPM_E_ARG && nw == 0);	/* S2MPG11 INT3 */
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S11, 0x0E, &v, &t) == ACPM_E_ARG && nw == 0);	/* S2MPG11 COMMON_CTRL1 */
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, 0x5C, &v, &t) == ACPM_E_ARG && nw == 0);	/* a touch rail */
		EXPECT(acpm_pmic_src_read(&cfg, 2, 0x0C, &v, &t) == ACPM_E_ARG && nw == 0);		/* unknown chip */
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, 0x10C, &v, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, 0x0F, &v, &t) == ACPM_OK && nbell == 1 && v == 0x20);
		EXPECT(t.a.req[0] == (0x0Fu | 0x0100u | (1u << 16)) && t.a.req[1] == 0);
	}
	puts("PASS pmicsrc host test");
	return 0;
}
