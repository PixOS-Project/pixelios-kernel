/* Host test for mifclk.c (--mif-clk, T-5030; --mif-clk-level KHZ with -DMIF_CLK_LEVEL_KHZ=KHZ, ALL-ON T-02) against the FAKE APM of dvfs_host_test.c (#included, its main renamed): MIF / INT GET, the one
 * FREQ_REQ (dom 0, rate ACPM_DVFS_MIF_REQ_KHZ = the table top row 3172000, or the --mif-clk-level row, reply-expected path of the reviewed CPU / GPU clients), the read-back, the sweetmif* tokens
 * appended to the command line, and every refusal with ZERO writes (ACPM gates, non-empty queue) or ZERO requests (failing GET, MIF already at the requested row).
 * acpm_dvfs_mif_top / dvfs_set class 2 refuse INT, every CPU / GPU domain and every rate but the one requested row (a sweep over every domain, row +-1 kHz and class 0..4 must send exactly ONE triple); the CPU and GPU callers refuse MIF / INT.
 * Built two ways by build.sh: with the real board macros and (-DGATE_TEST) with ACPM_LAYOUT_VERIFIED forced to 0 by a sed'ed copy of the header. Build:
 *   gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DMIF_CLK -I. -Iout -include out/board_config.h -o out/mifclk_host_test mifclk_host_test.c */
#define main dvfs_host_main
#include "dvfs_host_test.c"
#undef main
#include "mifclk.c"

#ifndef MIF_CLK
#error "build with -DMIF_CLK"
#endif

/* the rate the image asks (top row, or the --mif-clk-level row) and a start rate that differs from it */
#define REQ ACPM_DVFS_MIF_REQ_KHZ
#if ACPM_DVFS_MIF_REQ_KHZ == 2730000
#define START 3172000
#else
#define START 2730000
#endif
#define STR_(x) #x
#define STR(x) STR_(x)
/* independent oracle of the MIF table rows (gs101.dtsi mif_int_map column 1, tools/boards.py _MIF_INT_MAP) */
static const uint32_t mif_rows[] __attribute__((unused)) = { 3172000, 2730000, 2535000, 2288000, 2028000, 1716000, 1539000, 1352000, 1014000, 845000, 676000, 546000, 421000 };

static char lines[512][RAMLOG_LINE_MAX + 8];
static unsigned nl;
static volatile char cmd[1024];

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}
void wait_ticks(uint64_t t) { now += t; }

static void fresh(const char *start)	/* a clean fake APM + empty log + a command line holding `start` */
{
	unsigned i;

	setup(ESZ);
	nl = 0;
	for (i = 0; i < sizeof(cmd); i++)
		cmd[i] = 0;
	for (i = 0; start[i]; i++)
		cmd[i] = start[i];
	cur[0] = START;
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

static void dump_lines(void)
{
	for (unsigned i = 0; i < nl; i++)
		fprintf(stderr, "  %s\n", lines[i]);
	fprintf(stderr, "  cmd=%s\n", (const char *)cmd);
}

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); dump_lines(); assert(0); } } while (0)

static int cmd_has(const char *s)
{
	return strstr((const char *)cmd, s) != NULL;
}

/* the (domain, word1, word2) of every request slot written, in order */
static unsigned reqs(uint32_t dom[], uint32_t w1[], uint32_t w2[])
{
	unsigned n = 0, i;

	for (i = 0; i + 3 < nw; i++)
		if (wlog[i].pa >= SRAM + REQ_BASE_O && wlog[i].pa < SRAM + REQ_BASE_O + QLEN * ESZ && (wlog[i].pa - SRAM - REQ_BASE_O) % ESZ == 0) {
			dom[n] = wlog[i].v & 0xffffu;
			w1[n] = wlog[i + 1].v;
			w2[n] = wlog[i + 2].v;
			n++;
		}
	return n;
}

static void prime(void)		/* queue state of a quiet channel, as gpuclk_host_test.c does it */
{
	setS(REQ_REAR_O, 4);
	setS(REQ_FRONT_O, 4);
	setS(RSP_REAR_O, 2);
	setS(RSP_FRONT_O, 2);
}

int main(void)
{
#ifdef GATE_TEST
	/* the board macros say layout_verified = 0 (sed'ed header): refuse, say why, write nothing; the tokens still tell XNU rc=4 */
	fresh("a=1");
	prime();
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && nbell == 0 && nreq == 0 && nget == 0 && has("MIF GAP: gate") && cmd_has(" sweetmifrc=4"));
	puts("PASS mifclk gate host test");
	return 0;
#else
	uint32_t dom[16], w1[16], w2[16];
	unsigned n;
	struct acpm_trace t = { .seq = 1 };	/* the caller initialises seq (acpm.h:114) */

	/* happy path: GET MIF, GET INT, pre-GET MIF + REQ MIF top, GET MIF, GET INT */
	fresh("debug=0xc cpus=5");
	prime();
	mif_clk_cmdline(cmd, sizeof(cmd));
	n = reqs(dom, w1, w2);
	EXPECT(n == 6 && dom[0] == 0 && w2[0] == 1 && dom[1] == 1 && w2[1] == 1);
	EXPECT(dom[2] == 0 && w2[2] == 1 && dom[3] == 0 && w2[3] == 0 && w1[3] == REQ);	/* FREQ_REQ = 0, the requested row */
	EXPECT(dom[4] == 0 && w2[4] == 1 && dom[5] == 1 && w2[5] == 1);
	EXPECT(nreq == 1 && nget == 5 && cur[0] == REQ && cur[1] == 465000);
	EXPECT(cmd_has("debug=0xc cpus=5 sweetmif0b=" STR(START) " sweetmif1b=465000 sweetmif0a=" STR(REQ) " sweetmif1a=465000 sweetmifrc=0"));
	EXPECT(!has("GAP"));
	only_channel_writes();

	/* MIF already at the requested row: no request, the GET is the witness, token rc=2 */
	fresh("x"); prime(); cur[0] = REQ;
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nreq == 0 && has("already at the requested row") && cmd_has("x sweetmif0b=" STR(REQ) " sweetmif1b=465000 sweetmif0a=0 sweetmif1a=0 sweetmifrc=2"));

	/* the firmware answers the request but the rate does not change: both readbacks are reported, the tokens carry the unchanged rate */
	fresh("x"); prime(); ignore_req = 1;
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nreq == 1 && cmd_has("sweetmif0b=" STR(START)) && cmd_has("sweetmif0a=" STR(START)) && cmd_has("sweetmifrc=0"));

	/* the first GET fails (error code in reply word 3): nothing is requested, rc=3, the failure is a GAP line */
	fresh("x"); prime(); get_err = 5;
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nreq == 0 && nget == 1 && has("MIF GAP: get MIF before") && cmd_has("sweetmif0b=0") && cmd_has("sweetmifrc=3"));

	/* a non-empty request queue: nothing is sent, rc=4 */
	fresh("x"); prime(); setS(REQ_FRONT_O, 3);
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && nbell == 0 && has("MIF GAP: gate") && cmd_has("sweetmifrc=4"));

	/* the INT GET fails (error code in reply word 3, MIF answered): NOTHING is requested, rc=3 (review r1 finding 3) */
	fresh("x"); prime(); get_err = 5; get_err_dom = 2;
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nreq == 0 && nget == 2 && has("MIF GAP: get INT before") && cmd_has("sweetmif0b=" STR(START) " sweetmif1b=0") && cmd_has("sweetmifrc=3"));

	/* the FREQ_REQ itself is refused (reply word 3 != 0): rc=1, a GAP line with the request, both read-backs still taken */
	fresh("x"); prime(); req_err = 9;
	mif_clk_cmdline(cmd, sizeof(cmd));
	EXPECT(nreq == 1 && cmd_has("sweetmifrc=1") && has("MIF GAP: MIF request") && has("reply word 3"));

	/* the settle wait is taken between the request and the read-back (about 10 ms) */
	fresh("x"); prime();
	{
		uint64_t t0 = now;

		mif_clk_cmdline(cmd, sizeof(cmd));
		EXPECT(now - t0 >= counter_freq() / 100);
	}

	/* no room: the command line is left untouched, a GAP is printed and NOTHING reaches the APM (finding 2: no write before the room check) */
	{
		char big[1024];
		unsigned i;

		for (i = 0; i < sizeof(big) - 10; i++)
			big[i] = 'a';
		big[i] = 0;
		fresh(big);
		prime();
		mif_clk_cmdline(cmd, sizeof(cmd));
		EXPECT(!cmd_has("sweetmif") && has("MIF GAP: no room") && nreq == 0 && nget == 0 && nw == 0 && nbell == 0);
	}

	/* acpm_dvfs_mif_top: MIF and the ONE requested row only; the class-2 path refuses everything else before anything is sent */
	fresh("x");
	prime();
	t = trace();
	EXPECT(dvfs_set(&cfg, 1, 100000, &t, 2) == ACPM_E_ARG && nw == 0);		/* INT: never requested, even at its lowest row */
	EXPECT(dvfs_set(&cfg, 1, 533000, &t, 2) == ACPM_E_ARG && nw == 0);
	EXPECT(dvfs_set(&cfg, 0, REQ, &t, 0) == ACPM_E_ARG && dvfs_set(&cfg, 0, REQ, &t, 1) == ACPM_E_ARG && nw == 0);	/* the CPU and GPU callers cannot reach MIF */
	EXPECT(dvfs_set(&cfg, 1, 100000, &t, 0) == ACPM_E_ARG && dvfs_set(&cfg, 1, 100000, &t, 1) == ACPM_E_ARG && nw == 0);
	EXPECT(acpm_dvfs_mif_top(&cfg, &t) == ACPM_OK && nreq == 1 && cur[0] == REQ);
	only_channel_writes();
	/* sweep: over every domain 0..7, every MIF row and row +-1 kHz and 0, and every class -1..4, the image sends a FREQ_REQ for exactly ONE (dom, rate, class) triple */
	{
		unsigned sent = 0;

		for (uint32_t d = 0; d < 8; d++)
			for (unsigned r = 0; r <= sizeof(mif_rows) / sizeof(mif_rows[0]); r++)
				for (int dk = -1; dk <= 1; dk++)
					for (int c = -1; c <= 4; c++) {
						uint32_t k = r == sizeof(mif_rows) / sizeof(mif_rows[0]) ? 0 : mif_rows[r] + dk;
						int e;

						fresh("x");
						prime();
						t = trace();
						e = dvfs_set(&cfg, d, k, &t, c);
						if (nreq != 0 || e == ACPM_OK) {
							EXPECT(d == 0 && k == REQ && c == 2 && e == ACPM_OK && nreq == 1);
							sent++;
						} else {
							EXPECT(nw == 0 && nbell == 0);
						}
					}
		EXPECT(sent == 1);	/* exactly the (dom 0, REQ, class 2) triple */
	}
	/* every row is a table row of the generated macro; the level of this image is one of them */
	for (unsigned r = 0; r < sizeof(mif_rows) / sizeof(mif_rows[0]); r++)
		EXPECT(ACPM_DVFS_MIF_LEVEL_OK(mif_rows[r]));
	EXPECT(!ACPM_DVFS_MIF_LEVEL_OK(REQ + 1) && !ACPM_DVFS_MIF_LEVEL_OK(REQ - 1) && !ACPM_DVFS_MIF_LEVEL_OK(0));
	/* the CPU --dvfs table is untouched by the memory rows: the CPU client still sees exactly three domains */
	EXPECT(acpm_dvfs_ndom() == 3 && ACPM_DVFS_NALL == 7);
	puts("PASS mifclk host test");
	return 0;
#endif
}
