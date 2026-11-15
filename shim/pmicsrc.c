/* pmicsrc.c - read-only PMIC reset-source latches, see pmicsrc.h. Freestanding, no static state, no
 * MMIO of its own (no try_read32, no try_write32, no SMC): the only hardware access is
 * acpm_pmic_src_read() (and, with --pmic-src-clear only, pmic_src_clear() in pmicsrc_clear.c = the
 * one write). Sources: docs/research/139 section 2 + 5 (S2MPG10_PM_STATUS1..OFFSRC 0xC..0xF,
 * s2mpg10-register.h:39-42; S2MPG11_PM_STATUS1 0xC, OFFSRC 0xD, s2mpg11-register.h:39-40). Bit
 * meaning of OFFSRC / PWRONSRC is NOT public (research 139 section 2): printed raw. The console
 * never reaches the host in a --boot-xnu handover boot, so pmic_src() also formats the values into
 * the caller's token buffer (stack/argument stores only); loader.c appends it to the XNU command
 * line like mifclk.c does, xnu patch 0318 prints it. See pmicsrc.h for the token format. */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "acpm.h"
#include "pmicsrc.h"

#if SOC_GS101

#define NSRC 6u

__attribute__((noipa)) static uint32_t put_txt(char *dst, uint32_t cap, uint32_t pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

#ifdef PMIC_SRC_CLEAR
/* a boot-arg number in DECIMAL, 0..99 (XNU's boot-arg parser reads "A" as a string, not a number:
 * pexpert/gen/bootargs.c getval) */
__attribute__((noipa)) static uint32_t put_dec2(char *dst, uint32_t cap, uint32_t pos, uint32_t v)
{
	if (v >= 10 && pos + 1 < cap)
		dst[pos++] = (char)('0' + (v / 10) % 10);
	if (pos + 1 < cap)
		dst[pos++] = (char)('0' + v % 10);
	return pos;
}
#endif

__attribute__((noipa)) static uint32_t put_hex(char *dst, uint32_t cap, uint32_t pos, uint32_t v,
							  uint32_t digits)
{
	for (uint32_t i = 0; i < digits && pos + 1 < cap; i++) {
		uint32_t d = (v >> (4 * (digits - 1 - i))) & 0xfu;

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, uint32_t pos)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("SWEET", buf);
}

/* the audited register list: chip, register number; names are chosen by index (no pointer tables:
 * build.sh rejects ABS64 relocations) */
static uint32_t src_chip(uint32_t k)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	return k < 4 ? ACPM_SRC_S10 : ACPM_SRC_S11;
}

static uint32_t src_reg(uint32_t k)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	switch (k) {
	case 0: return 0x0C;	/* S2MPG10_PM_STATUS1 */
	case 1: return 0x0D;	/* S2MPG10_PM_STATUS2 */
	case 2: return 0x0E;	/* S2MPG10_PM_PWRONSRC */
	case 3: return 0x0F;	/* S2MPG10_PM_OFFSRC */
	case 4: return 0x0C;	/* S2MPG11_PM_STATUS1 */
	default: return 0x0D;	/* S2MPG11_PM_OFFSRC */
	}
}

static const char *src_name(uint32_t k)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	switch (k) {
	case 0: return "STATUS1";
	case 1: return "STATUS2";
	case 2: return "PWRONSRC";
	case 3: return "OFFSRC";
	case 4: return "STATUS1";
	default: return "OFFSRC";
	}
}

static const char *err_name(int32_t e)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	switch (e) {
	case ACPM_E_UNSURE: return "ACPM_E_UNSURE";
	case ACPM_E_ARG: return "ACPM_E_ARG";
	case ACPM_E_FAULT: return "ACPM_E_FAULT";
	case ACPM_E_DESC: return "ACPM_E_DESC";
	case ACPM_E_BUSY: return "ACPM_E_BUSY";
	case ACPM_E_TIMEOUT: return "ACPM_E_TIMEOUT";
	case ACPM_E_REPLY: return "ACPM_E_REPLY";
	case ACPM_E_RET: return "ACPM_E_RET";
	case ACPM_E_RAIL: return "ACPM_E_RAIL";
	case ACPM_E_ON: return "ACPM_E_ON";
	default: return "ACPM_E_UNKNOWN";
	}
}

/* one chip's line(s): the prefix again when a line would pass the 96 byte cap */
static void put_item(char *line, uint32_t *pos, const char *chip, const char *name, const char *val)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	uint32_t need = 1;
	const char *s;

	for (s = name; *s && need < RAMLOG_LINE_MAX; s++)
		need++;
	for (s = val; *s && need < RAMLOG_LINE_MAX; s++)
		need++;
	if (*pos + need + 2 >= RAMLOG_LINE_MAX) {
		emit(line, *pos);
		*pos = put_txt(line, RAMLOG_LINE_MAX, 0, "PMIC SRC: ");
		*pos = put_txt(line, RAMLOG_LINE_MAX, *pos, chip);
	}
	*pos = put_txt(line, RAMLOG_LINE_MAX, *pos, " ");
	*pos = put_txt(line, RAMLOG_LINE_MAX, *pos, name);
	*pos = put_txt(line, RAMLOG_LINE_MAX, *pos, "=");
	*pos = put_txt(line, RAMLOG_LINE_MAX, *pos, val);
}

/* the ACPM gates, same as wlan_rails(): returns the refusal text, or 0 when a request may be
 * sent */
static const char *gates(struct acpm_cfg *c, struct acpm_chan *h)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	acpm_cfg_init(c);
	if (!c->layout_verified)
		return acpm_why(ACPM_W_LAYOUT);
	if (!c->elem_unit_verified)
		return acpm_why(ACPM_W_UNIT);
	if (!counter_freq())
		return acpm_why(ACPM_W_TIMER);
	if (acpm_describe(c, h))
		return acpm_why(h->why);
	if (h->rr != h->rf || h->sr != h->sf)
		return acpm_why(ACPM_W_BUSY);
	return 0;
}

/* review 0316 F1: a failed read names its reason, the APM RETURN code and whether the request stays
 * queued (it does after a timeout: later ACPM users refuse BUSY) */
static void fail_report(char *line, const struct acpm_trace *t)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	uint32_t p = put_txt(line, RAMLOG_LINE_MAX, 0, "PMIC SRC FAIL why: ");

	p = put_txt(line, RAMLOG_LINE_MAX, p, acpm_why(t->a.why));
	emit(line, p);
	p = put_txt(line, RAMLOG_LINE_MAX, 0, "PMIC SRC STATE: ret=0x");
	p = put_hex(line, RAMLOG_LINE_MAX, p, t->a.ret, 2);
	p = put_txt(line, RAMLOG_LINE_MAX, p,
		    t->a.published && !t->a.had_reply
		    ? " no reply, request stays queued, later ACPM steps get BUSY"
		    : " nothing left queued");
	emit(line, p);
}

/* the command-line tokens: a register's token exists only when EVERY register it holds was read
 * (never a value for a failed read, rule 7) */
static uint32_t put_tokens(char *tok, uint32_t cap, const uint32_t *r, uint32_t okn, uint32_t rc,
			   uint32_t clr, int32_t pw)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	uint32_t p = 0, i;
	uint32_t w;

	if (okn >= 4) {
		w = 0;
		for (i = 0; i < 4; i++)
			w = (w << 8) | (r[i] & 0xffu);
		p = put_txt(tok, cap, p, " sweetps10=0x");
		p = put_hex(tok, cap, p, w, 8);
	}
	if (okn >= NSRC) {
		w = ((r[4] & 0xffu) << 8) | (r[5] & 0xffu);
		p = put_txt(tok, cap, p, " sweetps11=0x");
		p = put_hex(tok, cap, p, w, 4);
	}
	p = put_txt(tok, cap, p, " sweetpsrc=");
	p = put_hex(tok, cap, p, rc, 1);
#ifdef PMIC_SRC_CLEAR
	p = put_txt(tok, cap, p, " sweetpsclr=");
	p = put_dec2(tok, cap, p, clr);
	if (pw >= 0) {	/* the S2MPG10 PWRONSRC value read back after the write 0 (pmicsrc.h) */
		p = put_txt(tok, cap, p, " sweetpspw=0x");
		p = put_hex(tok, cap, p, (uint32_t)pw, 2);
	}
#else
	(void)clr;
	(void)pw;
#endif
	tok[p] = 0;
	return p;
}

int32_t pmic_src(char *tok, uint32_t cap)
{
	/* style-allow: func-len audit pins this shape, see pmicsrc_audit */
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	char line[RAMLOG_LINE_MAX], val[64];
	uint32_t regs[NSRC] = { 0, 0, 0, 0, 0, 0 };
	struct acpm_cfg c;
	struct acpm_trace t;
	struct acpm_chan h;
	const char *why;
	uint32_t v = 0;
	uint32_t k, p = 0, got = 0, failed = 0, rc = 0, clr = PMIC_CLR_SKIPPED;
	int32_t e, pw = -1;

	why = gates(&c, &h);
	if (why)
		rc = PMIC_SRC_RC_REFUSED;
	if (!why) {
		acpm_xfer_zero(&t.a);
		acpm_xfer_zero(&t.b);
		t.seq = 1;
		t.n = 0;
	}
	for (k = 0; k < NSRC; k++) {
		if (k == 0 || k == 4) {
			if (k == 4)
				emit(line, p);
			p = put_txt(line, sizeof(line), 0, k == 0 ? "PMIC SRC: s2mpg10" : "PMIC SRC: s2mpg11");
		}
		val[0] = 0;
		if (why) {
			uint32_t q = put_txt(val, sizeof(val), 0, "ERR refused: ");

			q = put_txt(val, sizeof(val), q, why);
			val[q] = 0;
		} else if (failed) {
			uint32_t q = put_txt(val, sizeof(val), 0, "ERR skipped");

			val[q] = 0;
		} else {
			e = acpm_pmic_src_read(&c, src_chip(k), src_reg(k), &v, &t);
			if (e) {
				uint32_t q = put_txt(val, sizeof(val), 0, "ERR ");

				q = put_txt(val, sizeof(val), q, err_name(e));
				val[q] = 0;
				failed = 1;
				rc = k + 1;
			} else {
				regs[k] = v;
				uint32_t q = put_txt(val, sizeof(val), 0, "0x");

				q = put_hex(val, sizeof(val), q, v, 2);
				val[q] = 0;
				got++;
			}
		}
		put_item(line, &p, k < 4 ? "s2mpg10" : "s2mpg11", src_name(k), val);
	}
#ifdef PMIC_SRC_CLEAR
	p = put_txt(line, sizeof(line), p, " (read before the clear)");
#else
	p = put_txt(line, sizeof(line), p, " (read-only, not cleared)");
#endif
	emit(line, p);
	if (!why && failed)
		fail_report(line, &t);
#ifdef PMIC_SRC_CLEAR
	/* the ONE write of --pmic-src-clear (pmicsrc.h "Clear"): only after every gate passed and all
	 * six reads succeeded; their values are already in regs[] for the tokens */
	if (!why && !failed && got == NSRC && cap >= PMIC_SRC_TOK_MAX) {
		uint32_t rb = 0;
		uint32_t na = 0, i;

		clr = pmic_src_clear(&c, &t, &rb, &na);
		p = put_txt(line, sizeof(line), 0,
			    "PMIC SRC CLEAR: read back s10 OFFSRC,PWRONSRC s11 OFFSRC:");
		for (i = 0; i < na; i++) {
			p = put_txt(line, sizeof(line), p, " 0x");
			p = put_hex(line, sizeof(line), p, (rb >> (8 * (na - 1 - i))) & 0xffu, 2);
		}
		p = put_txt(line, sizeof(line), p, " rc=");
		p = put_dec2(line, sizeof(line), p, clr);
		if (na >= 2)
			pw = (int32_t)((rb >> (8 * (na - 2))) & 0xffu);	/* step 1 = S10 PWRONSRC */
		emit(line, p);
		if (clr >= PMIC_CLR_WRITE && clr < PMIC_CLR_NONZERO)
			fail_report(line, &t);
		else if (clr != 0)
			con_str("SWEET",
				"PMIC SRC CLEAR GAP: an OFFSRC read back non-zero after the write 0 (see rc)");
	} else {
		con_str("SWEET",
			"PMIC SRC CLEAR GAP: not attempted (gate refused, read failed or no token room)");
	}
#endif
	/* tok has room by contract (PMIC_SRC_TOK_MAX); a smaller buffer gets no token and the loader
	 * appends nothing */
	if (cap >= PMIC_SRC_TOK_MAX)
		(void)put_tokens(tok, cap, regs, got, rc, clr, pw);
	return got == NSRC ? 0 : 1;
}

#else	/* not gs101: refuse, no ACPM traffic */

int32_t pmic_src(char *tok, uint32_t cap)
{
	/* style-allow: func-len audit pins this shape, see pmicsrc_audit */
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	if (cap)
		tok[0] = 0;
	con_str("SWEET", "PMIC SRC refused: gs101 only, nothing read");
	return 1;
}

#endif
