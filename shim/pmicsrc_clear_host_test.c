/* Host test for the --pmic-src-clear write (docs/research/142, ALL-ON T-02/T-24): pmicsrc.c + pmicsrc_clear.c + acpm.c's acpm_pmic_src_clear against the FAKE APM of acpm_host_test.c
 * (#included, its main renamed), built with -DPMIC_SRC_CLEAR. Checks: exactly three FUNC_WRITEs, value 0, in Android's order, request words per chip, then three read-backs; every
 * read before the first write; a failed / refused write stops the sequence (no retry, no read-back); a read-back that fails or is non-zero gives the code of pmicsrc.h; a failed
 * read or a gate refusal means ZERO writes; the allowlist of acpm_pmic_src_clear (every other chip / register incl. INT1..INT6 and STATUS, every value != 0) is refused with zero IPC;
 * every memory write in this binary is an IPC request-slot / queue / doorbell word.
 *   gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -DPMIC_SRC_CLEAR -I. -Iout -include out/board_config.h -o out/pmicsrc_clear_host_test pmicsrc_clear_host_test.c */
#define main acpm_host_main
#include "acpm_host_test.c"
#undef main
#include "pmicsrc.c"
#include "pmicsrc_clear.c"

static char lines[64][RAMLOG_LINE_MAX + 8];
static unsigned nl;
static char tok[PMIC_SRC_TOK_MAX];

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
	pmic[0x0D] = 0x22;	/* STATUS2 / S2MPG11 OFFSRC (one register file in the fake) */
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
	static const uint32_t s10 = 0x0100u;
	const uint32_t s11 = ((uint32_t)ACPM_S11_PMIC_ADDR << 8) | ((uint32_t)ACPM_S11_SPEEDY << 12);
	struct acpm_trace t;
	uint32_t v = 0;
	unsigned chip, reg, i;

	/* happy path: 6 reads, then W W W in Android's order (S10 OFFSRC 0xF, S10 PWRONSRC 0xE, S11 OFFSRC 0xD), then 3 read-backs; nothing else */
	world();
	EXPECT(pmic_src(tok, sizeof(tok)) == 0);
	EXPECT(!strcmp(fnseq, "RRRRRRWWWRRR") && nbell == 12);
	EXPECT(nwr == 3 && wrlog[0].w0 == (0x0Fu | s10) && wrlog[1].w0 == (0x0Eu | s10) && wrlog[2].w0 == (0x0Du | s11));
	for (i = 0; i < 3; i++)
		EXPECT(wrlog[i].w1 == 1u);	/* FUNC_WRITE = 1, WRITE_VAL [15:8] = 0, nothing else */
	EXPECT(pmic[0x0F] == 0 && pmic[0x0E] == 0 && pmic[0x0D] == 0 && pmic[0x0C] == 0x11);	/* only the three latches changed */
	EXPECT(!strcmp(tok, " sweetps10=0x11220420 sweetps11=0x1122 sweetpsrc=0 sweetpsclr=0 sweetpspw=0x00"));	/* the values are the ones read BEFORE the clear */
	EXPECT(has("SWEET PMIC SRC: s2mpg10 STATUS1=0x11 STATUS2=0x22 PWRONSRC=0x04 OFFSRC=0x20") && has("(read before the clear)") && !has("not cleared"));
	EXPECT(has("SWEET PMIC SRC CLEAR: read back s10 OFFSRC,PWRONSRC s11 OFFSRC: 0x00 0x00 0x00 rc=0"));
	only_ipc_writes();
	{
		char big[8 * PMIC_SRC_TOK_MAX];

		world(); pmic[0x0C] = 0xFF; pmic[0x0D] = 0xAB; pmic[0x0E] = 0xCD; pmic[0x0F] = 0xEF;
		memset(big, 'Z', sizeof(big));
		EXPECT(pmic_src(big, sizeof(big)) == 0 && !strcmp(big, " sweetps10=0xFFABCDEF sweetps11=0xFFAB sweetpsrc=0 sweetpsclr=0 sweetpspw=0x00") && strlen(big) == 78 && strlen(big) < PMIC_SRC_TOK_MAX);
		world();
		memset(big, 'Z', sizeof(big));
		EXPECT(pmic_src(big, PMIC_SRC_TOK_MAX - 1) == 0 && big[0] == 'Z' && nwr == 0 && !strcmp(fnseq, "RRRRRR") && has("CLEAR GAP: not attempted"));	/* no room to report: no write */
	}

	/* a refused / failed write stops the sequence: no retry, no read-back, the code names the step */
	world(); fail_wr_n = 1;
	EXPECT(pmic_src(tok, sizeof(tok)) == 0 && !strcmp(fnseq, "RRRRRRW") && nwr == 1 && !strcmp(tok, " sweetps10=0x11220420 sweetps11=0x1122 sweetpsrc=0 sweetpsclr=1"));
	EXPECT(pmic[0x0E] == 0x04 && has("PMIC SRC FAIL why: ") && has("PMIC SRC STATE: ret=0x02 nothing left queued"));
	world(); fail_wr_n = 2;
	EXPECT(pmic_src(tok, sizeof(tok)) == 0 && !strcmp(fnseq, "RRRRRRWW") && nwr == 2 && pmic[0x0F] == 0 && pmic[0x0E] == 0x04 && !strcmp(tok + strlen(tok) - 13, " sweetpsclr=2"));
	world(); fail_wr_n = 3;
	EXPECT(pmic_src(tok, sizeof(tok)) == 0 && !strcmp(fnseq, "RRRRRRWWW") && !strcmp(tok + strlen(tok) - 13, " sweetpsclr=3"));
	world(); mode = M_NEVER;	/* never answers: the six reads already fail first, so no write at all */
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nwr == 0 && !strcmp(tok, " sweetpsrc=1 sweetpsclr=10") && has("CLEAR GAP: not attempted"));

	/* read-back failure: the 7th/8th/9th read request */
	for (i = 0; i < 3; i++) {
		char want[32];

		world(); fail_rd_n = 7 + i;
		EXPECT(pmic_src(tok, sizeof(tok)) == 0 && nwr == 3 && nbell == 10 + i);
		snprintf(want, sizeof(want), " sweetpsclr=%u", 4 + i);
		if (i < 2)
			EXPECT(!strcmp(tok + strlen(tok) - 13, want) && !strstr(tok, "sweetpspw") && has("PMIC SRC FAIL why: "));
		else	/* two read-backs done: PWRONSRC (the 2nd) is reported although the 3rd failed */
			EXPECT(strstr(tok, " sweetpsclr=6 sweetpspw=0x00") && has("PMIC SRC FAIL why: "));
	}

	/* the write was acknowledged but an OFFSRC did not clear: a non-zero read-back is a code and a GAP, never silent; the PWRONSRC read-back (step 1) is only REPORTED (sweetpspw) */
	world(); ignore_wr = 1;
	EXPECT(pmic_src(tok, sizeof(tok)) == 0 && nwr == 3 && strstr(tok, " sweetpsclr=7 sweetpspw=0x04") && has("CLEAR GAP: an OFFSRC read back non-zero") && has("0x20 0x04 0x22 rc=7"));
	world(); ignore_wr = 1; pmic[0x0F] = 0;	/* S10 OFFSRC clear, PWRONSRC 0x04 stays, S11 OFFSRC 0x22 stays */
	EXPECT(pmic_src(tok, sizeof(tok)) == 0 && strstr(tok, " sweetpsclr=9 sweetpspw=0x04"));
	world(); ignore_wr = 1; pmic[0x0F] = 0; pmic[0x0D] = 0;	/* both OFFSRC clear, PWRONSRC non-zero (the hardware run): NOT an error, all three writes were sent */
	EXPECT(pmic_src(tok, sizeof(tok)) == 0 && nwr == 3 && strstr(tok, " sweetpsclr=0 sweetpspw=0x04") && has("rc=0") && !has("GAP: an OFFSRC") && !strstr(tok, "sweetpsclr=8"));

	/* a failed read earlier, or an ACPM gate refusal: ZERO writes */
	for (i = 1; i <= 6; i++) {
		world(); fail_rd_n = i;
		EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nwr == 0 && nfn == i && !strcmp(tok + strlen(tok) - 14, " sweetpsclr=10") && has("CLEAR GAP: not attempted"));
	}
	world(); setS(REQ_FRONT_O, 0);	/* queue not empty */
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nw == 0 && nbell == 0 && nwr == 0 && !strcmp(tok, " sweetpsrc=7 sweetpsclr=10"));
	world(); setup(8);		/* wrong element size */
	nl = 0;
	EXPECT(pmic_src(tok, sizeof(tok)) == 1 && nw == 0 && nbell == 0 && nwr == 0 && has("CLEAR GAP: not attempted"));

	/* the allowlist of acpm_pmic_src_clear: exactly (S10,0xF) (S10,0xE) (S11,0xD) with value 0; everything else is refused before any IPC */
	for (chip = 0; chip < 3; chip++)
		for (reg = 0; reg < 0x200; reg++) {
			static const uint32_t vals[3] = { 0, 1, 0xff };
			int ok = (chip == ACPM_SRC_S10 && (reg == 0x0F || reg == 0x0E)) || (chip == ACPM_SRC_S11 && reg == 0x0D);

			for (i = 0; i < 3; i++) {
				world();
				t.seq = 1;	/* the caller initialises seq (acpm.h:114, pmicsrc.c:229); left unset it is stack garbage and the reply seq check fails at -O2 / gcc 16 */
				if (ok && vals[i] == 0) {
					EXPECT(acpm_pmic_src_clear(&cfg, chip, reg, 0, &t) == ACPM_OK && nbell == 1 && nwr == 1 && wrlog[0].w1 == 1u);
					EXPECT(wrlog[0].w0 == ((reg & 0xffu) | (chip == ACPM_SRC_S10 ? s10 : s11)));
				} else {
					EXPECT(acpm_pmic_src_clear(&cfg, chip, reg, vals[i], &t) == ACPM_E_ARG && nw == 0 && nbell == 0 && nfn == 0);
				}
			}
		}
	world();
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S10, 0x00, 0, &t) == ACPM_E_ARG && nw == 0);	/* INT1 */
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S10, 0x05, 0, &t) == ACPM_E_ARG && nw == 0);	/* INT6 */
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S10, 0x0C, 0, &t) == ACPM_E_ARG && nw == 0);	/* STATUS1 */
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S10, 0x0D, 0, &t) == ACPM_E_ARG && nw == 0);	/* STATUS2 */
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S11, 0x0C, 0, &t) == ACPM_E_ARG && nw == 0);	/* S2MPG11 STATUS1 */
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S11, 0x0F, 0, &t) == ACPM_E_ARG && nw == 0);
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S10, 0x5C, 0, &t) == ACPM_E_ARG && nw == 0);	/* a touch rail */
	EXPECT(acpm_pmic_src_clear(&cfg, ACPM_SRC_S10, 0x10F, 0, &t) == ACPM_E_ARG && nw == 0);	/* regmap view of OFFSRC, not the ACPM register byte */
	EXPECT(acpm_pmic_src_read(&cfg, ACPM_SRC_S10, 0x0F, &v, &t) == ACPM_OK && nwr == 0);	/* the read allowlist stays read-only */
	only_ipc_writes();
	puts("PASS pmicsrc clear host test");
	return 0;
}
