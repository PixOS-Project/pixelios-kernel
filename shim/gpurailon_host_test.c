/* Host test for gpurailon.c (--gpu-rail-on, the S2MPG11 BUCK2S mode writer) against the FAKE APM of acpm_host_test.c (#included, main renamed):
 * enable ok (exact request words: ONE update 0x00C04002 on register 0x15 channel 1 type 1, nothing else), restore (0x00C00002), the refusals (mode already on /
 * another mode / OUT1 outside the window / ACPM gates / non-empty queue / null config) with ZERO PMIC writes, a failing update, a read-back mismatch (restore is still
 * attempted), a failing restore (said loudly), and that the sequence the shim runs is "enable, power only if ON_OK, restore". Built like gpurails_host_test.c
 * (-DGATE_TEST[ -DGATE_UNIT] with a sed'ed board header, -DNULL_TEST with ACPM_S11_ON_KNOWN 0). Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -DGPU_RAIL_ON -I. -Iout -include out/board_config.h -o out/gpurailon_host_test gpurailon_host_test.c */
#define main acpm_host_main
#include "acpm_host_test.c"
#undef main
#include "gpurailon.c"

static char lines[512][RAMLOG_LINE_MAX + 8];
static unsigned nl;

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
	assert(strlen(s) < RAMLOG_LINE_MAX);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}
void wait_ticks(uint64_t t) { now += t; }

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); for (unsigned i_ = 0; i_ < nl; i_++) fprintf(stderr, "  %s\n", lines[i_]); assert(0); } } while (0)

/* every request slot written: word 0 (low 16 bits), word 1 */
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

/* the whole log: only reads (w1 == 0) of the listed S2MPG11 registers and UPDATEs of 0x15 with the two audited words, all on channel 1 / type 1 */
static unsigned updates(void)
{
	uint32_t w0[64], w1[64];
	unsigned n = reqs(w0, w1), u = 0, i;

	for (i = 0; i < n; i++) {
		EXPECT((w0[i] & 0xff00u) == 0x1100u);	/* TYPE 1, CHANNEL 1 */
		if (w1[i] == 0) {
			EXPECT(w0[i] == 0x1115u || w0[i] == 0x1116u || w0[i] == 0x1117u);
		} else {
			EXPECT(w0[i] == 0x1115u && (w1[i] == 0x00C04002u || w1[i] == 0x00C00002u));
			u++;
		}
	}
	return u;
}

static void setrail(uint32_t ctrl, uint32_t out1)
{
	pmic[0x15] = ctrl;
	pmic[0x16] = out1;
	pmic[0x17] = 0x30;
}

int main(void)
{
#if defined(GATE_TEST) || defined(NULL_TEST)
	setup(ESZ);
	setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_REFUSED && nw == 0 && nbell == 0 && pmic[0x15] == 0x38);
#ifdef NULL_TEST
	EXPECT(has("null/unknown"));
#elif defined(GATE_UNIT)
	EXPECT(has("on refused: ") && has("element unit"));
#else
	EXPECT(has("on refused: ") && has("layout"));
#endif
	gpu_rail_restore(RAIL_REFUSED, RAIL_POWER_SKIPPED);
	EXPECT(nw == 0);
	puts("PASS gpurailon gate/null host test");
	return 0;
#else
	/* enable ok, then restore: the rail ends as it was found */
	setup(ESZ); setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_ON_OK);
	EXPECT(pmic[0x15] == 0x78 && pmic[0x16] == 0x40 && pmic[0x17] == 0x30);	/* only bits 7:6 changed (mode 1, what Linux writes), the voltage untouched */
	EXPECT(nbell == 7 && updates() == 1);				/* OUT2, pre-read, OUT1, update, read-back x3 */
	EXPECT(has("GPU RAIL on before B2S_CTRL=0x38 mode=0 B2S_OUT1=0x40 uV=600000 OUT2=0x30") && has("GPU RAIL on after B2S_CTRL=0x78 mode=1 B2S_OUT1=0x40 uV=600000 OUT2=0x30"));
	EXPECT(has("GPU RAIL on result: rail is on, voltage unchanged"));
	gpu_rail_restore(RAIL_ON_OK, RAIL_POWER_RAN);
	EXPECT(pmic[0x15] == 0x38 && pmic[0x16] == 0x40 && updates() == 2);
	EXPECT(has("GPU RAIL restore after B2S_CTRL=0x38 mode=0") && has("GPU RAILON enable=ok readback=ok restore=ok power=ran") && !has("!!!"));

	/* restore after the power cycle was skipped, same result text with power=skipped */
	setup(ESZ); setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_ON_OK);
	gpu_rail_restore(RAIL_ON_OK, RAIL_POWER_SKIPPED);
	EXPECT(pmic[0x15] == 0x38 && has("restore=ok power=skipped"));

	/* the rail alone (no --gpu-power): enable, read back, restore, one clear summary line */
	setup(ESZ); setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_ON_OK);
	gpu_rail_restore(RAIL_ON_OK, RAIL_POWER_NONE);
	EXPECT(pmic[0x15] == 0x38 && has("GPU RAILON enable=ok readback=ok restore=ok power=not-requested"));

	/* every OUT1 code (the window edges are 56 = 550000 uV and 80 = 700000 uV): accepted exactly inside the window, and then it is the only update */
	for (unsigned v = 0; v < 256; v += 1) {		/* every OUT1 code: accepted exactly inside the window, and then it is the only update */
		setup(ESZ); setrail(0x38, v);
		nl = 0;
		EXPECT(gpu_rail_enable() == ((v >= 56 && v <= 80) ? RAIL_ON_OK : RAIL_REFUSED));
		EXPECT(updates() == ((v >= 56 && v <= 80) ? 1u : 0u));
	}

	/* refused unless the mode is EXACTLY off: on (3), standby (1), MIF (2): zero updates, nothing to restore */
	for (uint32_t ctrl = 0x40; ctrl <= 0xFF; ctrl += 0x40) {
		setup(ESZ); setrail(0x38 | ctrl, 0x40);
		nl = 0;
		EXPECT(gpu_rail_enable() == RAIL_REFUSED && updates() == 0 && pmic[0x15] == (0x38 | ctrl) && nbell == 2);
		{
			unsigned before = nbell;

			gpu_rail_restore(RAIL_REFUSED, RAIL_POWER_SKIPPED);
			EXPECT(nbell == before && has("GPU RAILON enable=refused readback=n/a restore=none power=skipped"));
		}
	}

	/* the update itself fails (an error code in the reply): effect unknown, no power cycle, the restore finds it off and reports ok */
	setup(ESZ); setrail(0x38, 0x40); fail_upd = 1;
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_UNVERIFIED && updates() == 1 && has("update published but failed"));
	fail_upd = 0;
	gpu_rail_restore(RAIL_UNVERIFIED, RAIL_POWER_SKIPPED);
	EXPECT(pmic[0x15] == 0x38 && updates() == 1 && has("restore=ok") && !has("!!!"));

	/* acknowledged but not applied (read-back mode 0): MISMATCH, no power cycle, the restore has nothing to do and says ok */
	setup(ESZ); setrail(0x38, 0x40); ignore_upd = 1;
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_MISMATCH && has("MISMATCH") && has("no power cycle"));
	ignore_upd = 0;
	gpu_rail_restore(RAIL_MISMATCH, RAIL_POWER_SKIPPED);
	EXPECT(has("GPU RAILON enable=unverified readback=mismatch restore=ok power=skipped"));
	EXPECT(pmic[0x15] == 0x38 && has("restore=ok"));

	/* applied but OUT1 moved: MISMATCH, the restore still switches the rail back off */
	setup(ESZ); setrail(0x38, 0x40); bump_out1 = 1;
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_MISMATCH && pmic[0x15] == 0x78 && has("MISMATCH"));
	bump_out1 = 0;
	gpu_rail_restore(RAIL_MISMATCH, RAIL_POWER_SKIPPED);
	EXPECT(pmic[0x15] == 0x38 && updates() == 2 && has("restore=ok"));

	/* a failing restore update: the rail stays on, the summary says FAILED and the lines are loud */
	setup(ESZ); setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_ON_OK);
	fail_upd = 1;
	gpu_rail_restore(RAIL_ON_OK, RAIL_POWER_RAN);
	EXPECT(pmic[0x15] == 0x78 && has("!!! RESTORE FAILED") && has("GPU RAILON enable=ok readback=ok restore=FAILED power=ran") && !has("restore=ok"));

	/* the APM never takes the restore request: published, nothing rolled back, FAILED */
	setup(ESZ); setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_ON_OK);
	mode = M_NEVER;
	gpu_rail_restore(RAIL_ON_OK, RAIL_POWER_RAN);
	EXPECT(has("!!! RESTORE FAILED") && has("restore=FAILED"));

	/* L1: a failed OUT1 read made only reads: no PMIC write, not 'published', nothing to restore, no alarm */
	setup(ESZ); setrail(0x38, 0x40); fail_r16 = 1;
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_REFUSED && updates() == 0 && pmic[0x15] == 0x38 && nbell == 3 && !has("published") && has("REFUSED, nothing written to the PMIC"));
	fail_r16 = 0;
	gpu_rail_restore(RAIL_REFUSED, RAIL_POWER_SKIPPED);
	EXPECT(!has("!!!") && has("restore=none"));

	/* a pre-read failure of the enable: nothing written to the PMIC, state REFUSED */
	setup(ESZ); setrail(0x38, 0x40); mode = M_RET;
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_REFUSED && updates() == 0 && pmic[0x15] == 0x38);

	/* ACPM refusals before anything is sent: non-empty queue, wrong element size */
	setup(ESZ); setrail(0x38, 0x40); setS(REQ_FRONT_O, 0);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_REFUSED && nw == 0 && nbell == 0 && has("on refused: "));
	setup(8); setrail(0x38, 0x40);
	nl = 0;
	EXPECT(gpu_rail_enable() == RAIL_REFUSED && nw == 0 && nbell == 0);

	/* acpm_s11_rail_set directly: nothing but the audited pair */
	{
		struct acpm_trace t = { .seq = 1 };	/* the caller initialises seq (acpm.h:114); unset = stack garbage, reply seq check fails with optimisation */
		uint32_t o = 0;

		setup(ESZ); setrail(0x38, 0x40);
		t = trace();
		EXPECT(acpm_s11_rail_set(&cfg, 1, &t, &o) == ACPM_OK && o == 0x40 && pmic[0x15] == 0x78 && updates() == 1);
		t = trace();
		EXPECT(acpm_s11_rail_set(&cfg, 1, &t, &o) == ACPM_E_ON && updates() == 1);	/* already on: refused, nothing sent */
		t = trace();
		EXPECT(acpm_s11_rail_set(&cfg, 0, &t, 0) == ACPM_OK && pmic[0x15] == 0x38 && updates() == 2);
		t = trace();
		EXPECT(acpm_s11_rail_set(&cfg, 0, &t, 0) == ACPM_E_ON && updates() == 2);	/* already off: nothing to restore */
	}
	puts("PASS gpurailon host test");
	return 0;
#endif
}
