/* Host test for gpurails.c (--gpu-rails) against the FAKE APM of acpm_host_test.c (#included, its main renamed): the ten S2MPG11 READ requests
 * (channel 1, type 1, FUNC_READ, the registers of soc.json acpm.s2mpg11 only), the printed lines and the decode, the first failure stopping the rest,
 * and every refusal (ACPM gates, non-empty queue, unlisted register, null config) with ZERO writes. The same file is built with the gates forced to 0
 * (-DGATE_TEST[ -DGATE_UNIT], sed'ed board header) and with ACPM_S11_KNOWN 0 (-DNULL_TEST). Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -I. -Iout -include out/board_config.h -o out/gpurails_host_test gpurails_host_test.c */
#define main acpm_host_main
#include "acpm_host_test.c"
#undef main
#include "gpurails.c"

static char lines[512][RAMLOG_LINE_MAX + 8];
static unsigned nl;

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
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

/* the request words written to the slot: word 0 and word 1 of every transaction, in order */
static unsigned reqs(uint32_t w0[], uint32_t w1[])
{
	unsigned n = 0, i;

	for (i = 0; i + 1 < nw; i++)
		if (wlog[i].pa >= SRAM + REQ_BASE_O && wlog[i].pa < SRAM + REQ_BASE_O + QLEN * ESZ && (wlog[i].pa - SRAM - REQ_BASE_O) % ESZ == 0) {
			w0[n] = wlog[i].v & 0xffffu;
			w1[n] = wlog[i + 1].v;
			n++;
		}
	return n;
}

int main(void)
{
#if !(defined(GATE_TEST) || defined(NULL_TEST))
	uint32_t w0[32], w1[32];
	unsigned n;
	static const uint32_t want[10] = { 0x0C, 0x15, 0x16, 0x17, 0x25, 0x26, 0x27, 0x41, 0x42, 0x52 };
#endif

#if defined(GATE_TEST) || defined(NULL_TEST)
	setup(ESZ);
	nl = 0;
	EXPECT(gpu_rails() == 1 && nw == 0 && nbell == 0);
#ifdef NULL_TEST
	EXPECT(has("null/unknown value") && has("no ACPM request sent"));
#elif defined(GATE_UNIT)
	EXPECT(has("refused: ") && has("element unit"));
#else
	EXPECT(has("refused: ") && has("layout"));
#endif
	puts("PASS gpurails gate/null host test");
	return 0;
#else
	/* happy path: ten reads, in the soc.json order, S2MPG11 words (channel 1 << 12, type 1 << 8), FUNC_READ */
	setup(ESZ);
	pmic[0x15] = 0xC0;	/* BUCK2S mode 3 */
	pmic[0x16] = 0x6C;	/* OUT1 108 -> 200000 + 6250 * 108 = 875000 */
	pmic[0x25] = 0x40;	/* BUCK8S mode 1 (standby) */
	pmic[0x26] = 0x50;	/* 80 -> 700000 */
	pmic[0x41] = 0x20;	/* L1S_CTRL1: vsel 0x20 -> 300000 + 12500 * 32 = 700000 */
	pmic[0x52] = 0x10;	/* LDO_CTRL1 bits 5:4 = 1 (standby) */
	nl = 0;
	EXPECT(gpu_rails() == 0);
	n = reqs(w0, w1);
	EXPECT(n == 10 && nbell == 10 && nw == 80);
	for (unsigned i = 0; i < 10; i++)
		EXPECT(w0[i] == (want[i] | 0x1100u) && w1[i] == 0);	/* FUNC_READ = 0 */
	EXPECT(has("GPU RAIL cfg speedy ch=1 pmic_addr=1") && has("GPU RAIL B2S_CTRL reg=0x15 v=0xC0") && has("GPU RAIL B2S_OUT1 reg=0x16 v=0x6C"));
	EXPECT(has("GPU RAIL STATUS1 reg=0x0C v=0x00") && has("GPU RAIL LDO_CTRL1 reg=0x52 v=0x10"));
	EXPECT(has("GPU RAIL decode BUCK2S(VDD_G3D shaders)  mode=3(on-normal) enabled=1 uV=875000"));
	EXPECT(has("GPU RAIL decode BUCK8S(VDD_G3D_L2)  mode=1(SUSPEND,DT default) enabled=1 uV=700000"));
	EXPECT(has("GPU RAIL decode LDO1S(VDD_G3D_M)  mode=1(SUSPEND,DT default) enabled=1 uV=700000"));
	EXPECT(has("GPU RAIL result: all registers read") && has("OUT1 live = guess"));
	/* every write is one of the IPC channel's: slot words, request front, reply rear, the doorbell, INTCR0; the PMIC fake was not modified */
	for (unsigned i = 0; i < nw; i++) {
		uintptr_t pa = wlog[i].pa;

		EXPECT((pa >= SRAM + REQ_BASE_O && pa < SRAM + REQ_BASE_O + QLEN * ESZ) || pa == SRAM + REQ_FRONT_O || pa == SRAM + RSP_REAR_O ||
		       pa == MBOX + ACPM_INTGR1 || pa == MBOX + ACPM_INTCR0);
	}
	EXPECT(pmic[0x15] == 0xC0 && pmic[0x16] == 0x6C && pmic[0x52] == 0x10);
	/* gpu_rails_uv (T-50eb review F1): same ten reads, OUT1 uV through the pointers (BUCK2S then BUCK8S); either pointer may be 0 */
	{
		uint32_t b2s = 1, b8s = 2;

		setup(ESZ);
		pmic[0x16] = 0x6C; pmic[0x26] = 0x50;
		nl = 0;
		EXPECT(gpu_rails_uv(&b2s, &b8s) == 0 && nbell == 10 && b2s == 875000 && b8s == 700000);
		setup(ESZ); pmic[0x16] = 0x6C; pmic[0x26] = 0x50; b2s = 1; b8s = 2;
		nl = 0;
		EXPECT(gpu_rails_uv(0, &b8s) == 0 && b2s == 1 && b8s == 700000);
		setup(ESZ); pmic[0x16] = 0x6C; pmic[0x26] = 0x50; b2s = 1; b8s = 2;
		nl = 0;
		EXPECT(gpu_rails_uv(&b2s, 0) == 0 && b2s == 875000 && b8s == 2);
		/* a failed read leaves both untouched */
		setup(ESZ); mode = M_RET; b2s = 1; b8s = 2;
		nl = 0;
		EXPECT(gpu_rails_uv(&b2s, &b8s) == 1 && b2s == 1 && b8s == 2);
	}

	/* off + the first failure stops the rest: the APM returns an error code for the first read */
	setup(ESZ); mode = M_RET;
	nl = 0;
	EXPECT(gpu_rails() == 1 && nbell == 1 && nw == 8 && has("GPU RAIL STATUS1 reg=0x0C FAILED") && has("result: FAILED") && !has("B2S_CTRL reg"));
	setup(ESZ); mode = M_NEVER;	/* never consumed: published, nothing rolled back, no second request */
	nl = 0;
	EXPECT(gpu_rails() == 1 && nbell == 1 && has("the request stays queued"));

	/* refusals: zero writes */
	setup(ESZ); setS(REQ_FRONT_O, 0);	/* queue not empty */
	nl = 0;
	EXPECT(gpu_rails() == 1 && nw == 0 && nbell == 0 && has("refused: "));
	setup(8);			/* wrong element size */
	nl = 0;
	EXPECT(gpu_rails() == 1 && nw == 0 && nbell == 0);
	{
		struct acpm_trace t;
		uint32_t v = 0;

		setup(ESZ);
		t = trace();
		EXPECT(acpm_s11_read(&cfg, 0x5C, &v, &t) == ACPM_E_ARG && nw == 0);	/* an S2MPG10 rail register is not on the list */
		EXPECT(acpm_s11_read(&cfg, 0x15 + 0x100, &v, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_s11_read(&cfg, 0x14, &v, &t) == ACPM_E_ARG && nw == 0);	/* B1S_OUT2: not listed */
		EXPECT(acpm_s11_read(&cfg, 0x16, &v, &t) == ACPM_OK && nbell == 1);
		EXPECT(t.a.req[0] == (0x16u | 0x1100u | (1u << 16)) && t.a.req[1] == 0);
	}
	puts("PASS gpurails host test");
	return 0;
#endif
}
