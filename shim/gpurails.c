/*
 * gpurails.c - read-only S2MPG11 GPU-rail dump (see gpurails.h). Freestanding, no static state, no MMIO of its own.
 *
 * Why (docs/research/27, *guess*): the tiler/L2 (rail BUCK8S) came up but the shader cores (rail BUCK2S = S2S_VDD_G3D) never leave PWRTRANS; all three
 * GPU rails have `regulator-initial-mode = SEC_OPMODE_SUSPEND` in the DT (gs101-pmic.dtsi:760-773,850-860,936-946) i.e. "ON in standby mode",
 * and may still sit in standby / at a retention voltage. This prints what the PMIC says.
 *
 * Decode (the mode field and uV formulas are *read from source*: GK drivers/regulator/s2mpg11-regulator.c:35-46 s2mpg11_of_map_mode, :226-275 descriptors,
 * include/linux/mfd/samsung/s2mpg11-register.h:308-315 / :331-336; the BUCK output register OUT1 being the live output is a *guess*: Linux reads/writes OUT1, the
 * chip also has OUT2 which a DVS pin / mode may select):
 *   BUCK2S, BUCK8S: mode = bits 7:6 of *_CTRL; enabled = mode != 0 (s2m_is_enabled); uV = 200000 + 6250 * OUT1 (256 steps)
 *   LDO1S:          mode = bits 5:4 of LDO_CTRL1; uV = 300000 + 12500 * (L1S_CTRL1 & 0x7F)
 *   mode: 0 off, 1 SUSPEND ("ON in standby mode" by the driver comment; NOT evidence of standby/retention: the DT gives the same initial mode to the S2MPG10 CPU/MIF rails
 *   BUCK1..6, gs101-pmic.dtsi:130-210, and to always-on BUCK2S :764-768), 2 ON in PWREN_MIF mode, 3 ON normal.
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "acpm.h"
#include "gpurails.h"

#if SOC_GS101 && defined(ACPM_S11_NREG) && defined(ACPM_S11_KNOWN) && ACPM_S11_KNOWN

__attribute__((noipa)) static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static unsigned put_dec(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char t[10];
	unsigned n = 0;

	t[n++] = (char)('0' + v % 10u);
	v /= 10u;
	for (unsigned i = 1; i < sizeof(t) && v; i++) {
		t[n++] = (char)('0' + v % 10u);
		v /= 10u;
	}
	while (n && pos + 1 < cap)
		dst[pos++] = t[--n];
	return pos;
}

__attribute__((noipa)) static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint32_t v, unsigned digits)
{
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++) {
		unsigned d = (v >> (4 * (digits - 1 - i))) & 0xfu;

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("GPU", buf);
}

static void say(const char *a, const char *b)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "RAIL ");

	p = put_txt(line, sizeof(line), p, a);
	p = put_txt(line, sizeof(line), p, b);
	emit(line, p);
}

static void say_kv(const char *a, const char *k1, uint32_t v1, const char *k2, uint32_t v2)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "RAIL ");

	p = put_txt(line, sizeof(line), p, a);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_txt(line, sizeof(line), p, k1);
	p = put_txt(line, sizeof(line), p, "=");
	p = put_dec(line, sizeof(line), p, v1);
	if (k2) {
		p = put_txt(line, sizeof(line), p, " ");
		p = put_txt(line, sizeof(line), p, k2);
		p = put_txt(line, sizeof(line), p, "=");
		p = put_dec(line, sizeof(line), p, v2);
	}
	emit(line, p);
}

static const char *rname(unsigned k)
{
	switch (k) {
	case 0: return "STATUS1";
	case 1: return "B2S_CTRL";
	case 2: return "B2S_OUT1";
	case 3: return "B2S_OUT2";
	case 4: return "B8S_CTRL";
	case 5: return "B8S_OUT1";
	case 6: return "B8S_OUT2";
	case 7: return "L1S_CTRL1";
	case 8: return "L1S_CTRL2";
	case 9: return "LDO_CTRL1";
	default: return "REG?";	/* ACPM_S11_NREG is 10 (soc.json): a longer list needs a name here, show it instead of mislabelling it */
	}
}

static const char *mname(uint32_t mode)
{
	switch (mode) {
	case 0: return "off";
	case 1: return "SUSPEND,DT default";
	case 2: return "on-PWREN_MIF";
	case 3: return "on-normal";
	default: return "mode?";	/* the callers mask the field to 2 bits (0..3): unreachable, but never mislabel */
	}
}

/* "RAIL decode <rail> mode=<m>(<name>) enabled=<0|1> uV=<n>" */
static void decode(const char *rail, uint32_t mode, uint32_t uv)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "RAIL decode ");

	p = put_txt(line, sizeof(line), p, rail);
	p = put_txt(line, sizeof(line), p, " mode=");
	p = put_dec(line, sizeof(line), p, mode);
	p = put_txt(line, sizeof(line), p, "(");
	p = put_txt(line, sizeof(line), p, mname(mode));
	p = put_txt(line, sizeof(line), p, ") enabled=");
	p = put_dec(line, sizeof(line), p, mode ? 1u : 0u);
	p = put_txt(line, sizeof(line), p, " uV=");
	p = put_dec(line, sizeof(line), p, uv);
	emit(line, p);
}

/* b2s_uv / b8s_uv (NULL = not wanted): set to the decoded OUT1 voltage of BUCK2S / BUCK8S only when
 * all ten registers were read (return 0); T-a214 review F1 */
int gpu_rails_uv(uint32_t *b2s_uv, uint32_t *b8s_uv)
{
	struct acpm_cfg c;
	struct acpm_trace t;
	struct acpm_chan h;
	static const uint32_t regs[ACPM_S11_NREG] = { ACPM_S11_REGS };
	uint32_t v[ACPM_S11_NREG];
	unsigned k, got = 0;
	int e = ACPM_OK;
	/* style-allow: func-len one linear read sequence (gates, ten reads, decode), byte-pinned */
	/* style-allow: sized-types int is the acpm_* status type and this module's API (0 = ok) */
	/* style-allow: assert audited read-only object: every refusal is handled and logged */

	acpm_cfg_init(&c);
	say_kv("cfg speedy", "ch", ACPM_S11_SPEEDY, "pmic_addr", ACPM_S11_PMIC_ADDR);
	say_kv("gates layout_verified", "v", c.layout_verified, "elem_unit_verified", c.elem_unit_verified);
	if (!c.layout_verified) {
		say("refused: ", acpm_why(ACPM_W_LAYOUT));
		return 1;
	}
	if (!c.elem_unit_verified) {
		say("refused: ", acpm_why(ACPM_W_UNIT));
		return 1;
	}
	if (!counter_freq()) {
		say("refused: ", acpm_why(ACPM_W_TIMER));
		return 1;
	}
	e = acpm_describe(&c, &h);
	if (e) {
		say("refused: ", acpm_why(h.why));
		say("describe failed, ", "no request sent");
		return 1;
	}
	if (h.rr != h.rf || h.sr != h.sf) {
		say("refused: ", acpm_why(ACPM_W_BUSY));
		say("queues not empty, ", "no request sent");
		return 1;
	}
	acpm_xfer_zero(&t.a);
	acpm_xfer_zero(&t.b);
	t.seq = 1;
	t.n = 0;
	for (k = 0; k < ACPM_S11_NREG; k++) {
		char line[RAMLOG_LINE_MAX];
		unsigned p;

		v[k] = 0;
		e = acpm_s11_read(&c, regs[k], &v[k], &t);
		p = put_txt(line, sizeof(line), 0, "RAIL ");
		p = put_txt(line, sizeof(line), p, rname(k));
		p = put_txt(line, sizeof(line), p, " reg=0x");
		p = put_hex(line, sizeof(line), p, regs[k], 2);
		if (e) {
			p = put_txt(line, sizeof(line), p, " FAILED err=");
			p = put_dec(line, sizeof(line), p, (uint32_t)e);
			p = put_txt(line, sizeof(line), p, " ");
			p = put_txt(line, sizeof(line), p, acpm_why(t.a.why));
			emit(line, p);
			if (t.a.published)
				say("STATE: ", "the request stays queued; channel left as is, rerun only after reading this");
			break;
		}
		p = put_txt(line, sizeof(line), p, " v=0x");
		p = put_hex(line, sizeof(line), p, v[k], 2);
		emit(line, p);
		got++;
	}
	if (got == ACPM_S11_NREG) {
		decode("BUCK2S(VDD_G3D shaders) ", (v[1] >> 6) & 3u, ACPM_S11_BUCK_MIN_UV + ACPM_S11_BUCK_STEP_UV * v[2]);
		decode("BUCK8S(VDD_G3D_L2) ", (v[4] >> 6) & 3u, ACPM_S11_BUCK_MIN_UV + ACPM_S11_BUCK_STEP_UV * v[5]);
		if (b2s_uv)
			*b2s_uv = ACPM_S11_BUCK_MIN_UV + ACPM_S11_BUCK_STEP_UV * v[2];
		if (b8s_uv)
			*b8s_uv = ACPM_S11_BUCK_MIN_UV + ACPM_S11_BUCK_STEP_UV * v[5];
		decode("LDO1S(VDD_G3D_M) ", (v[9] >> 4) & 3u, ACPM_S11_LDO1_MIN_UV + ACPM_S11_LDO1_STEP_UV * (v[7] & ACPM_S11_LDO1_VSEL_MASK));
		say("decode note: ", "mode 1 is the DT default of CPU rails too; OUT1 live = guess");
	}
	say("result: ", got == ACPM_S11_NREG ? "all registers read" : "FAILED, see above");
	return got == ACPM_S11_NREG ? 0 : 1;
}

int gpu_rails(void)
{
	/* style-allow: sized-types int is this module's status type (0 = ok), as in gpu_rails_uv */
	/* style-allow: assert audited object (read-only): a wrapper, the callee logs every refusal */
	return gpu_rails_uv(0, 0);
}

#else	/* not gs101 / config unknown (null value): refuse, zero ACPM traffic */

int gpu_rails_uv(uint32_t *b2s_uv, uint32_t *b8s_uv)
{
	/* style-allow: sized-types int is this module's status type (0 = ok), as in gpu_rails_uv */
	/* style-allow: assert audited object (read-only): the refusal below is logged */
	(void)b2s_uv;
	(void)b8s_uv;
	con_str("GPU", "RAIL refused: soc.json acpm.s2mpg11 has a null/unknown value, no ACPM request sent");
	return 1;
}

int gpu_rails(void)
{
	/* style-allow: sized-types int is this module's status type (0 = ok), as in gpu_rails_uv */
	/* style-allow: assert audited object (read-only): a wrapper, the callee logs every refusal */
	return gpu_rails_uv(0, 0);
}

#endif
