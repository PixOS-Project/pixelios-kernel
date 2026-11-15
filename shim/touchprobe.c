/*
 * touchprobe.c - touchscreen SPI11/USI11 bring-up probe (see touchprobe.h).
 *
 * Phase 1 (touch_probe_dump) only reads, and reads the USI/SPI registers only when the USI11 sysreg mode already is SPI.
 * Phase 2 (touch_probe_run) writes. Every write goes through the single helper w32(), which refuses unless
 * w32_allowed() agrees; w32_allowed() works from wmask(): per register, the bits that may change (0 = read-only), and
 * w32 reads the old value of partially writable registers so a write that changes any other bit is refused.
 * The allowlist is the union of: the XNU driver's writes with sweettouchclk=3 sweettouchvar=1 (patches 0088-0092),
 * plus the build options of docs/research/21 section 3 (mux, cs = X1, osc/top = X2/X6, loop = X3, rst = X4, misopu = X5)
 * and only while that option is built in. tools/test-bluejay-boot.py audits this file (source: no asm, no volatile,
 * try_write32 only inside w32, w32 checks w32_allowed before writing) and the object (try_write32 referenced only by a bl
 * in w32; every other store stack/argument based).
 * Reads are fault-guarded and a faulting read stops the sequence. Poll loops have a time limit AND a poll-count
 * limit. The fixed delays (a few us, 10 ms for the reset pulse, 5 ms between ready polls) use wait_ticks(), which spins on
 * the architected counter: a dead counter would hang there (the polls use touch_ticks plus a count limit instead).
 * No static state.
 *
 * Order of phase 2: mux -> XNU clock chain (0092/0089) -> osc|top (X2/X6) -> SPI setup (0088; ABL-exact with cs) ->
 * variant F (X1) -> loopback (X3) -> reset pulse (X4) -> identify (+ MISO pull-up, X5) -> variants A..E (only without
 * experiment options, as sweettouchvar=1) -> restore.
 */
#include <stdarg.h>
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "touchprobe.h"
#include "acpm.h"

#if SOC_GS101 && defined(TOUCH_SPI_BASE)

#define SPI	TOUCH_SPI_BASE
#define USI	(TOUCH_SPI_BASE + TOUCH_USI_OFF)
#define CMU	TOUCH_CMU_BASE
#define TOP	TOUCH_CMUTOP_BASE

/* ---- tiny printf: %s %c %d %u %x with flags # - 0, width, l/ll; same output as the kernel's for these ---- */

static inline __attribute__((always_inline)) unsigned put_c(char *o, unsigned cap, unsigned p, char c)
{
	if (p + 1 < cap)
		o[p++] = c;
	return p;
}

static inline __attribute__((always_inline)) void vfmt(char *o, unsigned cap, const char *f, va_list ap)
{
	unsigned p = 0;

	/* bounded: f is the format literal, ends at its NUL (a trailing '%' breaks out below) */
	for (; *f; f++) {
		char t[24];
		const char *s = t;
		unsigned n = 0, w = 0, i;
		int alt = 0, left = 0, zero = 0;

		if (*f != '%') {
			p = put_c(o, cap, p, *f);
			continue;
		}
		/* bounded: stops at the first non-flag character, at the latest the NUL */
		for (f++; *f == '#' || *f == '-' || *f == '0'; f++) {
			alt |= *f == '#';
			left |= *f == '-';
			zero |= *f == '0';
		}
		while (*f >= '0' && *f <= '9')
			w = w * 10 + (unsigned)(*f++ - '0');
		unsigned ll = 0;
		/* bounded: stops at the first non-'l' character, at the latest the NUL */
		while (*f == 'l') {
			ll = 1;
			f++;
		}
		if (*f == 's') {
			s = va_arg(ap, const char *);
			/* bounded: the NUL of the string (literal or a buffer the caller terminated) */
			while (s[n])
				n++;
		} else if (*f == 'c') {
			t[0] = (char)va_arg(ap, int);
			n = 1;
		} else if (*f == 'x' || *f == 'u' || *f == 'd') {
			unsigned long long v = ll ? va_arg(ap, unsigned long long) : va_arg(ap, unsigned);
			unsigned b = *f == 'x' ? 16 : 10;
			char r[20];
			unsigned k = 0;
			do {
				r[k++] = "0123456789abcdef"[v % b];
				v /= b;
				/* bounded: v /= b (b = 10 or 16) reaches 0 (<= 20 digits for 64 bits) */
			} while (v);
			if (zero && !left)
				while (k < w)
					r[k++] = '0';
			if (alt && b == 16 && !(k == 1 && r[0] == '0')) {
				r[k++] = 'x';
				r[k++] = '0';
			}
			/* bounded: k drops by one per pass (k <= sizeof r) */
			while (k)
				t[n++] = r[--k];
		} else {
			p = put_c(o, cap, p, *f ? *f : '?');
			if (!*f)
				break;
			continue;
		}
		for (i = n; !left && i < w; i++)
			p = put_c(o, cap, p, ' ');
		for (i = 0; i < n; i++)
			p = put_c(o, cap, p, s[i]);
		for (i = n; left && i < w; i++)
			p = put_c(o, cap, p, ' ');
	}
	o[p] = 0;
}

/* One XNU-style line "SWEET TOUCH: <fmt>": label "SWEET", text "TOUCH: ...", split at spaces into <= 82-byte texts.
 * The summary uses the prefix "TOUCH RESULT: " (label + text: "SWEET TOUCH RESULT: ..."). */
#define SAY_MAX 82u

static void vsay(const char *pfx, const char *fmt, va_list ap)
{
	char buf[256], line[RAMLOG_LINE_MAX];
	const char *s = buf;
	unsigned pl = 0;

	/* bounded: pfx is a string literal, ends at its NUL */
	while (pfx[pl])
		pl++;
	vfmt(buf, sizeof(buf), fmt, ap);
	/* bounded: s advances by k >= 1 per pass (SAY_MAX - pl > 0) until its NUL */
	while (*s) {
		unsigned n = 0, k, i;
		/* bounded: the NUL of the string (literal or a buffer the caller terminated) */
		while (s[n])
			n++;
		k = n;
		if (n > SAY_MAX - pl) {
			k = SAY_MAX - pl;
			/* bounded: k drops by one per pass until 0 */
			while (k && s[k] != ' ')
				k--;
			if (!k)
				k = SAY_MAX - pl;
		}
		for (i = 0; i < pl; i++)
			line[i] = pfx[i];
		for (i = 0; i < k; i++)
			line[pl + i] = s[i];
		line[pl + k] = 0;
		con_str("SWEET", line);
		s += k;
		if (*s == ' ')
			s++;
	}
}

static void say(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vsay("TOUCH: ", fmt, ap);
	va_end(ap);
}

static void say_result(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vsay("TOUCH RESULT: ", fmt, ap);
	va_end(ap);
}

/* ---- MMIO: reads are fault-guarded; the ONE write path is w32 ---- */

struct tp {
	int fault;		/* sticky: a read faulted or a write was refused; the sequence stops */
	int dump;		/* phase 1: a faulting register is reported and the next line goes on */
	uint32_t last_st, last_ch;
	unsigned opts;		/* TP_O_* build options */
};

struct pin {
	uintptr_t bank;		/* CON register of the pad bank; DAT at +4, PUD at +8 */
	unsigned nr;
};
#define PIN(n)	((struct pin){ TOUCH_PIN_##n##_BANK, TOUCH_PIN_##n##_NR })

/* Pads whose bits may change, per register of the bank: CON (+0, 4-bit function) and DAT (+4, 1 bit) for the SPI pads, and
 * for the mux / reset pad only with their option; PUD (+8, 4-bit) only for the options that program it. */
#define PADW(P, con_en, pud_en) do { \
	if ((con_en) && pa == TOUCH_PIN_##P##_BANK) \
		m |= 0xfu << (TOUCH_PIN_##P##_NR * 4u); \
	if ((con_en) && pa == TOUCH_PIN_##P##_BANK + 4) \
		m |= 1u << TOUCH_PIN_##P##_NR; \
	if ((pud_en) && pa == TOUCH_PIN_##P##_BANK + 8) \
		m |= 0xfu << (TOUCH_PIN_##P##_NR * 4u); \
} while (0)

static uint32_t pad_mask(uintptr_t pa, unsigned opts)
{
	uint32_t m = 0;
	int rst = !!(opts & TP_O_RST), mux = !!(opts & TP_O_MUX), mpu = !!(opts & TP_O_MISOPU);

	PADW(CLK, 1, rst);
	PADW(MOSI, 1, rst);
	PADW(MISO, 1, rst || mpu);
	PADW(CS, 1, rst);
	PADW(MUX, mux, 0);	/* TS_SPI_SEL: only the mux option, only its own bit */
	PADW(RESET, rst, rst);
	return m;
}

/* The bits of the register at pa that a write may change; 0 = not writable, 0xffffffff = the whole register. */
static uint32_t wmask(uintptr_t pa, unsigned opts)
{
	if (pa >= SPI && pa < SPI + 0x30) {	/* SPI engine, not STATUS (0x14) and not RX_DATA (0x1c) */
		unsigned off = (unsigned)(pa - SPI);
		return (off == TP_SPI_STATUS || off == TP_SPI_RX_DATA || (off & 3u)) ? 0u : 0xffffffffu;
	}
	if (pa == USI + TP_USI_CON || pa == USI + TP_USI_OPTION)
		return 0xffffffffu;
	if (pa == TOUCH_USI_MODE_REG)
		return TOUCH_USI_MODE_MASK;	/* the mode field only */
	if (pa == CMU + TOUCH_CMU_MUX_USI)
		return 1u << TOUCH_CMU_BIT_MUX_SEL;
	if (pa == CMU + TOUCH_CMU_GATE_IPCLK || pa == CMU + TOUCH_CMU_GATE_PCLK || pa == CMU + TOUCH_CMU_GATE_RSTN)
		return TP_GATE_CHECK_MASK;
	if (pa == CMU + TOUCH_CMU_QCH)	/* XNU clk=2 write; docs/research/21 advises against it, so not with experiments */
		return (opts & TP_O_EXP) ? 0u : (1u << TOUCH_CMU_BIT_QCH_ENABLE) | (1u << TOUCH_CMU_BIT_QCH_REQ);
	if (pa == CMU + TOUCH_CMU_DIV)	/* the USI11 divider field, only for osc / top (X2 / X6) */
		return (opts & (TP_O_OSC | TP_O_TOP)) ? 0xfu : 0u;
	if (pa == TOP + TOUCH_CMUTOP_GATE)	/* CG_VAL only; never the CMU_TOP mux or divider */
		return 1u << TOUCH_CMUTOP_GATE_BIT;
	return pad_mask(pa, opts);
}

static int w32_allowed(uintptr_t pa, uint32_t old, uint32_t v, unsigned opts)
{
	uint32_t m = wmask(pa, opts);

	return m != 0 && ((old ^ v) & ~m) == 0;
}

/* noinline keeps every try_write32 call site inside this one symbol (the object audit checks it). */
__attribute__((noinline)) static int w32(uintptr_t pa, uint32_t v, unsigned opts)
{
	uint32_t old = v;

	if (wmask(pa, opts) != 0xffffffffu && !try_read32(pa, &old))
		return -1;
	if (!w32_allowed(pa, old, v, opts))
		return -2;
	return try_write32(pa, v) ? 0 : -1;
}

static int r32(uintptr_t pa, uint32_t *v)
{
	return try_read32(pa, v);
}

static void soft(struct tp *t)
{
	if (t->dump)
		t->fault = 0;
}

static uint32_t rd(struct tp *t, uintptr_t pa)
{
	uint32_t v = 0;

	if (!r32(pa, &v)) {
		say("unreadable pa=%#llx (value shown as 0)", (unsigned long long)pa);
		t->fault = 1;
		return 0;
	}
	return v;
}

static void wr(struct tp *t, uintptr_t pa, uint32_t v)
{
	int e;

	if (t->fault)
		return;
	e = w32(pa, v, t->opts);
	if (e) {
		say("write %s pa=%#llx, sequence stops", e == -2 ? "REFUSED" : "FAULTED", (unsigned long long)pa);
		t->fault = 1;
	}
}

/* ---- time ---- */

static uint64_t us(void)
{
	uint64_t f = counter_freq();

	return f ? touch_ticks() * 1000000ull / f : 0;
}

static void delay_us(unsigned u)
{
	wait_ticks(counter_freq() * u / 1000000u);
}

/* true once lim_us elapsed since t0 or the poll budget is spent */
static int expired(uint64_t t0, unsigned lim_us, unsigned *polls)
{
	return ++*polls > TP_MAX_POLLS || us() - t0 > lim_us;
}

/* ---- pads (0088 tg_pin_*) ---- */

static uint32_t pin_func(struct tp *t, struct pin p)
{
	return (rd(t, p.bank) >> (p.nr * 4u)) & 0xfu;
}

static void pin_set_func(struct tp *t, struct pin p, uint32_t f)
{
	uint32_t v = rd(t, p.bank);

	v &= ~(0xfu << (p.nr * 4u));
	v |= (f & 0xfu) << (p.nr * 4u);
	wr(t, p.bank, v);
}

static int pin_get(struct tp *t, struct pin p)
{
	return (int)((rd(t, p.bank + 4) >> p.nr) & 1u);
}

static void pin_put(struct tp *t, struct pin p, int val)
{
	uint32_t v = rd(t, p.bank + 4);

	v = val ? (v | (1u << p.nr)) : (v & ~(1u << p.nr));
	wr(t, p.bank + 4, v);
}

static uint32_t pin_pud(struct tp *t, struct pin p)
{
	return (rd(t, p.bank + 8) >> (p.nr * 4u)) & 0xfu;
}

static void pin_set_pud(struct tp *t, struct pin p, uint32_t u)
{
	uint32_t v = rd(t, p.bank + 8);

	v &= ~(0xfu << (p.nr * 4u));
	v |= (u & 0xfu) << (p.nr * 4u);
	wr(t, p.bank + 8, v);
}

static void pin_report(struct tp *t, const char *name, struct pin p)
{
	soft(t);	/* dump: a faulting pad does not hide the next one */
	say("pin %-5s bank %#llx[%u] func=%#x dat=%d pud=%#x", name, (unsigned long long)p.bank, p.nr,
	    pin_func(t, p), pin_get(t, p), pin_pud(t, p));
}

/* The pads whose pull the rst option sets to none (docs/research/21 X4: Linux EXYNOS_PIN_PULL_NONE). */
static struct pin pud_pad(unsigned i)
{
	switch (i) {
	case 0: return PIN(CLK);
	case 1: return PIN(MOSI);
	case 2: return PIN(MISO);
	case 3: return PIN(CS);
	default: return PIN(RESET);
	}
}
#define NPUD_PADS 5u

/* ---- phase 1: reads only (0089 tg_cmu_dump + 0092 cmu_top line, 0088 phase 0, 0090 tg_spi_diag) ---- */

static void dump_cmu(struct tp *t)
{
	soft(t);
	say("cmu base %#llx: OPTION[%#x]=%#x usi_mux[%#x]=%#x bus_mux[%#x]=%#x bus_auto[%#x]=%#x "
	    "usi_auto[%#x]=%#x div[%#x]=%#x", (unsigned long long)CMU,
	    TOUCH_CMU_OPTION, rd(t, CMU + TOUCH_CMU_OPTION), TOUCH_CMU_MUX_USI, rd(t, CMU + TOUCH_CMU_MUX_USI),
	    TOUCH_CMU_MUX_BUS, rd(t, CMU + TOUCH_CMU_MUX_BUS), TOUCH_CMU_MUX_BUS_AUTO,
	    rd(t, CMU + TOUCH_CMU_MUX_BUS_AUTO), TOUCH_CMU_MUX_USI_AUTO, rd(t, CMU + TOUCH_CMU_MUX_USI_AUTO),
	    TOUCH_CMU_DIV, rd(t, CMU + TOUCH_CMU_DIV));
	soft(t);
	say("cmu_top %#llx: PERIC1_IP mux[%#x]=%#x div[%#x]=%#x gate[%#x]=%#x", (unsigned long long)TOP,
	    TOUCH_CMUTOP_MUX, rd(t, TOP + TOUCH_CMUTOP_MUX), TOUCH_CMUTOP_DIV, rd(t, TOP + TOUCH_CMUTOP_DIV),
	    TOUCH_CMUTOP_GATE, rd(t, TOP + TOUCH_CMUTOP_GATE));
	soft(t);
	say("cmu gates: ipclk_4[%#x]=%#x pclk_4[%#x]=%#x rstnsync[%#x]=%#x QCH[%#x]=%#x",
	    TOUCH_CMU_GATE_IPCLK, rd(t, CMU + TOUCH_CMU_GATE_IPCLK), TOUCH_CMU_GATE_PCLK,
	    rd(t, CMU + TOUCH_CMU_GATE_PCLK), TOUCH_CMU_GATE_RSTN, rd(t, CMU + TOUCH_CMU_GATE_RSTN),
	    TOUCH_CMU_QCH, rd(t, CMU + TOUCH_CMU_QCH));
}

/* Returns 1 when the USI11 sysreg mode already is SPI (only then are the USI/SPI registers read). */
static int dump_mode_pins(struct tp *t)
{
	uint32_t m;

	soft(t);
	m = rd(t, TOUCH_USI_MODE_REG);
	say("sysreg USI11 mode (%#llx) = %#x (SPI = %#x, so %s)", (unsigned long long)TOUCH_USI_MODE_REG, m,
	    (unsigned)TOUCH_USI_MODE_SPI, (m & TOUCH_USI_MODE_MASK) == TOUCH_USI_MODE_SPI ? "already SPI" : "not SPI yet");
	pin_report(t, "clk", PIN(CLK));
	pin_report(t, "mosi", PIN(MOSI));
	pin_report(t, "miso", PIN(MISO));
	pin_report(t, "cs", PIN(CS));
	pin_report(t, "irq", PIN(IRQ));
	pin_report(t, "reset", PIN(RESET));
	pin_report(t, "mux", PIN(MUX));
	return !t->fault && (m & TOUCH_USI_MODE_MASK) == TOUCH_USI_MODE_SPI;
}

static void spi_diag(struct tp *t, const char *when)
{
	soft(t);
	say("diag %s: USI_CON=%#x USI_OPTION=%#x USI mode=%#x", when, rd(t, USI + TP_USI_CON),
	    rd(t, USI + TP_USI_OPTION), rd(t, TOUCH_USI_MODE_REG));
	soft(t);
	say("diag %s: ch=%#x clk=%#x mode=%#x cs=%#x inten=%#x status=%#x", when, rd(t, SPI + TP_SPI_CH_CFG),
	    rd(t, SPI + TP_SPI_CLK_CFG), rd(t, SPI + TP_SPI_MODE_CFG), rd(t, SPI + TP_SPI_CS_REG),
	    rd(t, SPI + TP_SPI_INT_EN), rd(t, SPI + TP_SPI_STATUS));
	soft(t);
	say("diag %s: pktcnt=%#x pend=%#x swap=%#x fb=%#x (TX/RX data not read)", when,
	    rd(t, SPI + TP_SPI_PACKET_CNT), rd(t, SPI + TP_SPI_PENDING_CLR), rd(t, SPI + TP_SPI_SWAP_CFG),
	    rd(t, SPI + TP_SPI_FB_CLK));
}

void touch_probe_dump(void)
{
	struct tp t = { 0, 1, 0, 0, 0 };

	dump_cmu(&t);
	if (dump_mode_pins(&t))
		spi_diag(&t, "before");
	else
		say("diag before: skipped: usi mode=%#x (the USI/SPI registers are not read unless the mode is SPI)",
		    rd(&t, TOUCH_USI_MODE_REG));
}

/* ---- phase 2 ---- */

/* 0088 tg_report_and_check_mux. Returns 1 when the AP owns the bus (already, or driven low by the mux option). */
static int mux_step(struct tp *t, int *how)
{
	uint32_t f = pin_func(t, PIN(MUX));
	int d = pin_get(t, PIN(MUX));

	if (t->fault) {	/* a faulting read returned 0: that is not "the AP drives it low" */
		say("AP/AOC mux: unreadable, nothing written");
		return 0;
	}
	if (f == 1u && d == 0) {
		say("AP/AOC mux: AP drives it low = the AP owns the bus");
		*how = 1;
		return 1;
	}
	say("AP/AOC mux: AP is NOT driving it low (func=%#x dat=%d), the bus may belong to the AOC", f, d);
	if (!(t->opts & TP_O_MUX)) {
		say("leaving the bus alone; build option mux drives it low; nothing written");
		*how = 2;
		return 0;
	}
	pin_put(t, PIN(MUX), 0);	/* 0088: data low first, then the pad becomes an output */
	pin_set_func(t, PIN(MUX), 1);
	f = pin_func(t, PIN(MUX));
	d = pin_get(t, PIN(MUX));
	say("mux option: mux driven low, now func=%#x dat=%d", f, d);
	if (t->fault || f != 1u || d != 0) {
		say("mux read-back mismatch, nothing else touched");
		*how = 3;
		return 0;
	}
	*how = 4;
	return 1;
}

/* 0089 tg_cmu_write_check: write, read back, print before/after; bits in check must read back as written. */
static int cmu_write_check(struct tp *t, const char *what, uintptr_t pa, uint32_t off, uint32_t val, uint32_t check)
{
	uint32_t before = rd(t, pa), after;

	wr(t, pa, val);
	after = rd(t, pa);
	say("cmu %s [%#x]: %#x -> wrote %#x -> %#x", what, off, before, val, after);
	if (t->fault || (after & check) != (val & check)) {
		say("cmu %s: read-back mismatch (mask %#x), clock sequence aborted", what, check);
		return 0;
	}
	return 1;
}

static int cmu_gate(struct tp *t, const char *what, uint32_t off)
{
	return cmu_write_check(t, what, CMU + off, off, tp_gate_open(rd(t, CMU + off)), TP_GATE_CHECK_MASK);
}

/* Poll the BUSY bit of a CMU mux/divider register until clear, bounded (1 ms and TP_MAX_POLLS). */
static int cmu_wait_idle(struct tp *t, uintptr_t pa)
{
	uint32_t busy = 1u << TOUCH_CMU_BIT_MUX_BUSY;
	unsigned polls = 0;
	uint64_t t0 = us();

	/* bounded: expired(): TP_MAX_POLLS or TP_MUX_BUSY_TIMEOUT_US; caller checks BUSY */
	while ((rd(t, pa) & busy) && !t->fault && !expired(t0, TP_MUX_BUSY_TIMEOUT_US, &polls)) {
	}
	return !t->fault && !(rd(t, pa) & busy);
}

/* 0092 + 0089 tg_cmu_enable with clk_mode = 3 (QCH only without experiment options). Returns 0 if aborted. */
static int cmu_enable(struct tp *t)
{
	uint32_t sel = 1u << TOUCH_CMU_BIT_MUX_SEL, v, g, b;
	uint32_t q = (1u << TOUCH_CMU_BIT_QCH_ENABLE) | (1u << TOUCH_CMU_BIT_QCH_REQ);

	g = rd(t, TOP + TOUCH_CMUTOP_GATE);	/* CG_VAL of the TOP gate only; mux and divider are left as ABL set them */
	b = 1u << TOUCH_CMUTOP_GATE_BIT;
	wr(t, TOP + TOUCH_CMUTOP_GATE, g | b);
	say("cmu_top gate [%#x]: %#x -> wrote %#x -> %#x", TOUCH_CMUTOP_GATE, g, g | b, rd(t, TOP + TOUCH_CMUTOP_GATE));
	v = rd(t, CMU + TOUCH_CMU_MUX_USI);
	say("cmu: sweettouchclk=3, forcing the USI11 clock chain");
	if (t->fault)
		return 0;
	if (v & sel) {
		say("cmu usi mux [%#x] = %#x: already selects the TOP source, not written", TOUCH_CMU_MUX_USI, v);
	} else {
		if (!cmu_write_check(t, "usi mux", CMU + TOUCH_CMU_MUX_USI, TOUCH_CMU_MUX_USI, v | sel, sel))
			return 0;
		if (!cmu_wait_idle(t, CMU + TOUCH_CMU_MUX_USI)) {
			say("cmu usi mux still BUSY after 1 ms, clock sequence aborted");
			return 0;
		}
	}
	if (!cmu_gate(t, "ipclk_4 gate", TOUCH_CMU_GATE_IPCLK) || !cmu_gate(t, "pclk_4 gate", TOUCH_CMU_GATE_PCLK) ||
	    !cmu_gate(t, "rstnsync gate", TOUCH_CMU_GATE_RSTN))
		return 0;
	if (!(t->opts & TP_O_EXP) &&
	    !cmu_write_check(t, "QCH", CMU + TOUCH_CMU_QCH, TOUCH_CMU_QCH, rd(t, CMU + TOUCH_CMU_QCH) | q, q))
		return 0;
	say("cmu: clock sequence done");
	return 1;
}

/* State to put back at the end (restore). */
struct sv {
	int clk;				/* 1 once mux/div were changed by osc or top */
	uint32_t mux, div;			/* their values before */
	int pud;				/* 1 once the pull-ups of the rst option were changed */
	uint32_t pud_old;			/* nibble i = old PUD of pud_pad(i) */
	int miso;				/* 1 while the misopu option holds MISO at pull-up */
	uint32_t miso_old;
	uint32_t mode, fb;			/* MODE_CFG / FB_CLK before the loopback */
};

/* X2 (osc, ABL rule for SCK 1 MHz: mux bit4 := 0, divider 5) and X6 (top: bit4 := 1, divider 9, 40 MHz ipclk).
 * Mux first, poll BUSY, then the divider, poll BUSY. Returns 0 (after restoring) if a step failed. */
static int clk_source(struct tp *t, struct sv *s)
{
	uint32_t sel = 1u << TOUCH_CMU_BIT_MUX_SEL, osc = !!(t->opts & TP_O_OSC);
	uint32_t m = rd(t, CMU + TOUCH_CMU_MUX_USI), d = rd(t, CMU + TOUCH_CMU_DIV);

	say("clk source %s: usi mux %#x div %#x before", osc ? "osc (24.576 MHz, div 5)" : "top (400 MHz, div 9)", m, d);
	if (t->fault)
		return 0;
	s->clk = 1;
	s->mux = m;
	s->div = d;
	if (!cmu_write_check(t, "usi mux", CMU + TOUCH_CMU_MUX_USI, TOUCH_CMU_MUX_USI, osc ? m & ~sel : m | sel, sel) ||
	    !cmu_wait_idle(t, CMU + TOUCH_CMU_MUX_USI) ||
	    !cmu_write_check(t, "usi div", CMU + TOUCH_CMU_DIV, TOUCH_CMU_DIV, (d & ~0xfu) | (osc ? 5u : 9u), 0xfu) ||
	    !cmu_wait_idle(t, CMU + TOUCH_CMU_DIV)) {
		say("clk source: a step failed or stayed BUSY, restoring");
		return 0;
	}
	return 1;
}

static void clk_restore(struct tp *t, struct sv *s)
{
	uint32_t sel = 1u << TOUCH_CMU_BIT_MUX_SEL;

	if (!s->clk)
		return;
	s->clk = 0;
	t->fault = 0;	/* a restore is attempted even after a failure */
	(void)cmu_write_check(t, "usi mux restore", CMU + TOUCH_CMU_MUX_USI, TOUCH_CMU_MUX_USI,
			      (rd(t, CMU + TOUCH_CMU_MUX_USI) & ~sel) | (s->mux & sel), sel);
	(void)cmu_wait_idle(t, CMU + TOUCH_CMU_MUX_USI);
	(void)cmu_write_check(t, "usi div restore", CMU + TOUCH_CMU_DIV, TOUCH_CMU_DIV,
			      (rd(t, CMU + TOUCH_CMU_DIV) & ~0xfu) | (s->div & 0xfu), 0xfu);
	(void)cmu_wait_idle(t, CMU + TOUCH_CMU_DIV);
}

/* 0088 tg_spi_flush */
static void spi_flush(struct tp *t)
{
	uint64_t t0;
	unsigned polls = 0;

	wr(t, SPI + TP_SPI_PACKET_CNT, 0);
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) & ~(TP_CH_RXCH_ON | TP_CH_TXCH_ON));
	wr(t, SPI + TP_SPI_CH_CFG, (rd(t, SPI + TP_SPI_CH_CFG) | TP_CH_SW_RST) & ~TP_CH_HS_EN);
	t0 = us();
	/* bounded: expired(): TP_MAX_POLLS polls or 1000 us */
	while (TP_ST_TX_LVL(rd(t, SPI + TP_SPI_STATUS)) && !t->fault && !expired(t0, 1000u, &polls)) {
	}
	t0 = us();
	polls = 0;
	/* bounded: expired(): TP_MAX_POLLS polls or 1000 us */
	while (TP_ST_RX_LVL(rd(t, SPI + TP_SPI_STATUS)) && !t->fault && !expired(t0, 1000u, &polls))
		(void)rd(t, SPI + TP_SPI_RX_DATA);
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) & ~TP_CH_SW_RST);
	wr(t, SPI + TP_SPI_MODE_CFG, TP_MODE_TRAILCNT);	/* byte-wide channel and bus, no DMA, no 4-burst */
}

/* 0090 tg_spi_engine_init: s3c64xx_spi_hwinit + flush; also the reset in front of every variant */
static void spi_engine_init(struct tp *t)
{
	wr(t, SPI + TP_SPI_INT_EN, 0);
	wr(t, SPI + TP_SPI_MODE_CFG, 0);
	wr(t, SPI + TP_SPI_PACKET_CNT, 0);
	wr(t, SPI + TP_SPI_PENDING_CLR, TP_PND_ERR_CLR);
	wr(t, SPI + TP_SPI_PENDING_CLR, 0);
	wr(t, SPI + TP_SPI_SWAP_CFG, 0);
	wr(t, SPI + TP_SPI_CH_CFG, 0);	/* CPOL = 0, CPHA = 0, master */
	wr(t, SPI + TP_SPI_FB_CLK, 1);	/* samsung,spi-feedback-delay = <1> */
	spi_flush(t);
}

/* 0088 tg_spi_setup: USI to SPI mode, SPI pads, engine init. With the cs option the engine init is the ABL's spi_init
 * instead (docs/research/21 section 1.2: USI_CON |= 1, USI_OPTION |= 1, FB_CLK = (FB_CLK & ~3) | 1, USI_CON &= ~1;
 * MODE_CFG, INT_EN, SWAP_CFG are not written). */
static void spi_setup(struct tp *t)
{
	uint32_t v = rd(t, TOUCH_USI_MODE_REG);

	if ((v & TOUCH_USI_MODE_MASK) != TOUCH_USI_MODE_SPI)
		wr(t, TOUCH_USI_MODE_REG, (v & ~(uint32_t)TOUCH_USI_MODE_MASK) | TOUCH_USI_MODE_SPI);
	v = rd(t, USI + TP_USI_CON);	/* only now, with the mode written, are USI/SPI registers read */
	say("USI_CON=%#x (bit0 = reset), USI mode now %#x", v, rd(t, TOUCH_USI_MODE_REG));
	if (v & TP_USI_CON_RESET) {
		wr(t, USI + TP_USI_CON, v & ~TP_USI_CON_RESET);
		delay_us(1);
	}
	pin_put(t, PIN(CS), 1);	/* chip select high (inactive) before it becomes an output */
	pin_set_func(t, PIN(CS), 1);
	pin_set_func(t, PIN(CLK), TOUCH_SPI_FUNC);
	pin_set_func(t, PIN(MOSI), TOUCH_SPI_FUNC);
	pin_set_func(t, PIN(MISO), TOUCH_SPI_FUNC);
	say("SPI regs before init: ch=%#x clk=%#x mode=%#x status=%#x fb=%#x", rd(t, SPI + TP_SPI_CH_CFG),
	    rd(t, SPI + TP_SPI_CLK_CFG), rd(t, SPI + TP_SPI_MODE_CFG), rd(t, SPI + TP_SPI_STATUS),
	    rd(t, SPI + TP_SPI_FB_CLK));
	if (t->opts & TP_O_CS) {
		wr(t, USI + TP_USI_CON, rd(t, USI + TP_USI_CON) | TP_USI_CON_RESET);
		wr(t, USI + TP_USI_OPTION, rd(t, USI + TP_USI_OPTION) | TP_USI_OPT_CLKREQ);
		wr(t, SPI + TP_SPI_FB_CLK, (rd(t, SPI + TP_SPI_FB_CLK) & ~3u) | 1u);
		wr(t, USI + TP_USI_CON, rd(t, USI + TP_USI_CON) & ~TP_USI_CON_RESET);
	} else {
		spi_engine_init(t);
	}
	say("SPI regs after init:  ch=%#x clk=%#x mode=%#x status=%#x fb=%#x (the actual SCK = CMU ipclk / 4)",
	    rd(t, SPI + TP_SPI_CH_CFG), rd(t, SPI + TP_SPI_CLK_CFG), rd(t, SPI + TP_SPI_MODE_CFG),
	    rd(t, SPI + TP_SPI_STATUS), rd(t, SPI + TP_SPI_FB_CLK));
}

/* 0090 tg_spi_burst_ex: one burst of len bytes, chip select already low. 0 or -1 on timeout. *rx (if given) receives
 * the bytes packed little-endian (byte i in bits 8i..8i+7), so len <= 8 then: the object audit allows no indexed store
 * through a pointer argument. */
static int spi_burst(struct tp *t, const uint8_t *tx, uint64_t *rx, unsigned len, int chan_first)
{
	uint32_t chcfg;
	uint64_t t0, acc = 0;
	unsigned i, polls = 0;

	if (len == 0 || len > (rx ? 8u : TP_SPI_FIFO_BYTES))
		return -1;
	spi_flush(t);
	chcfg = (rd(t, SPI + TP_SPI_CH_CFG) & ~TP_CH_TXCH_ON) | TP_CH_RXCH_ON;
	wr(t, SPI + TP_SPI_PACKET_CNT, len | TP_PKT_CNT_EN);
	if (chan_first)	/* variant B: the reverse of Linux (FIFO first, CH_CFG last) */
		wr(t, SPI + TP_SPI_CH_CFG, chcfg | TP_CH_TXCH_ON);
	for (i = 0; i < len; i++)
		wr(t, SPI + TP_SPI_TX_DATA, tx ? tx[i] : 0u);	/* byte per 32-bit write: use_32bit_io */
	if (!chan_first)
		wr(t, SPI + TP_SPI_CH_CFG, chcfg | TP_CH_TXCH_ON);
	t0 = us();
	while (TP_ST_RX_LVL(rd(t, SPI + TP_SPI_STATUS)) < len) {
		if (t->fault || expired(t0, TP_BURST_TIMEOUT_US, &polls)) {
			t->last_st = rd(t, SPI + TP_SPI_STATUS);
			t->last_ch = rd(t, SPI + TP_SPI_CH_CFG);
			wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) & ~(TP_CH_RXCH_ON | TP_CH_TXCH_ON));
			return -1;
		}
	}
	for (i = 0; i < len; i++) {
		uint32_t d = rd(t, SPI + TP_SPI_RX_DATA);

		if (rx)
			acc |= (uint64_t)(d & 0xffu) << (8u * i);
	}
	if (rx)
		*rx = acc;
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) & ~(TP_CH_RXCH_ON | TP_CH_TXCH_ON));
	return 0;
}

/* X1: the ABL's spi_xfer chunk (docs/research/21 section 1.2, ABL 0xc5e4-0xc6ec): no MODE_CFG / INT_EN / SWAP_CFG writes.
 * PACKET_CNT = n | EN, TX bytes, CH_CFG |= TX|RX, poll RX level >= n, read RX, poll TX level 0, CH_CFG &= ~(TX|RX),
 * PACKET_CNT = 0, SW_RST pulse + drain (SW_RST is cleared at the start of the next chunk). 0 or -1 on timeout. */
static int abl_burst(struct tp *t, const uint8_t *tx, uint64_t *rx, unsigned len)
{
	uint64_t t0, acc = 0;
	unsigned i, polls = 0;
	int err = 0;

	if (len == 0 || len > (rx ? 8u : TP_SPI_FIFO_BYTES))
		return -1;
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) & ~TP_CH_SW_RST);
	wr(t, SPI + TP_SPI_PACKET_CNT, len | TP_PKT_CNT_EN);
	for (i = 0; i < len; i++)
		wr(t, SPI + TP_SPI_TX_DATA, tx ? tx[i] : 0u);
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) | TP_CH_RXCH_ON | TP_CH_TXCH_ON);
	t0 = us();
	while (TP_ST_RX_LVL(rd(t, SPI + TP_SPI_STATUS)) < len) {
		if (t->fault || expired(t0, TP_BURST_TIMEOUT_US, &polls)) {
			t->last_st = rd(t, SPI + TP_SPI_STATUS);
			t->last_ch = rd(t, SPI + TP_SPI_CH_CFG);
			err = -1;
			break;
		}
	}
	if (!err) {
		for (i = 0; i < len; i++) {
			uint32_t d = rd(t, SPI + TP_SPI_RX_DATA);

			if (rx)
				acc |= (uint64_t)(d & 0xffu) << (8u * i);
		}
		if (rx)
			*rx = acc;
		polls = 0;
		t0 = us();
		/* bounded: expired(): TP_MAX_POLLS polls or 1000 us */
		while (TP_ST_TX_LVL(rd(t, SPI + TP_SPI_STATUS)) && !t->fault && !expired(t0, 1000u, &polls)) {
		}
	}
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) & ~(TP_CH_RXCH_ON | TP_CH_TXCH_ON));
	wr(t, SPI + TP_SPI_PACKET_CNT, 0);
	wr(t, SPI + TP_SPI_CH_CFG, rd(t, SPI + TP_SPI_CH_CFG) | TP_CH_SW_RST);
	polls = 0;
	t0 = us();
	/* bounded: expired(): TP_MAX_POLLS polls or 1000 us */
	while (TP_ST_RX_LVL(rd(t, SPI + TP_SPI_STATUS)) && !t->fault && !expired(t0, 1000u, &polls))
		(void)rd(t, SPI + TP_SPI_RX_DATA);
	return err;
}

/* Chip select around one transaction: GPIO low (+ CS_REG &= ~1 in the ABL style), and the reverse at the end. */
static void cs_begin(struct tp *t)
{
	pin_put(t, PIN(CS), 0);	/* active low */
	if (t->opts & TP_O_CS)
		wr(t, SPI + TP_SPI_CS_REG, rd(t, SPI + TP_SPI_CS_REG) & ~1u);
	delay_us(1);
}

static void cs_end(struct tp *t)
{
	delay_us(TP_FTM5_SPI_DELAY_CS_US);
	pin_put(t, PIN(CS), 1);
	if (t->opts & TP_O_CS)
		wr(t, SPI + TP_SPI_CS_REG, rd(t, SPI + TP_SPI_CS_REG) | 1u);
	delay_us(2);
}

static int burst(struct tp *t, const uint8_t *tx, uint64_t *rx, unsigned len)
{
	return (t->opts & TP_O_CS) ? abl_burst(t, tx, rx, len) : spi_burst(t, tx, rx, len, 0);
}

/* The FTM5 transaction shape (0088 tg_xfer + tg_read): cmd bytes, then the reply stream (1 dummy byte + n bytes, n <= 15)
 * in one chip select, as bursts of <= 8 bytes: reply byte k is in bits 8k.. of *w0 (k < 8) or of *w1 (k >= 8). */
static int xfer_rd(struct tp *t, const uint8_t *cmd, unsigned ncmd, uint64_t *w0, uint64_t *w1, unsigned n)
{
	unsigned total = TP_FTM5_DUMMY_BYTES + n;
	int err;

	if (total > 16u)
		return -1;
	cs_begin(t);
	err = burst(t, cmd, 0, ncmd);
	if (!err)
		err = burst(t, 0, w0, total > 8u ? 8u : total);
	if (!err && total > 8u)
		err = burst(t, 0, w1, total - 8u);
	cs_end(t);
	return err;
}

/* byte k (0 = the dummy byte) of the reply stream */
static unsigned rbyte(uint64_t w0, uint64_t w1, unsigned k)
{
	return (unsigned)(((k < 8u ? w0 : w1) >> (8u * (k & 7u))) & 0xffu);
}

/* ftm5_hwreg_read_cmd: opcode + big-endian register address */
static void id_cmd(uint8_t *cmd)
{
	cmd[0] = TP_FTM5_CMD_HW_REG_R;
	cmd[1] = (uint8_t)(TP_FTM5_ADDR_DCHIP_ID >> 24);
	cmd[2] = (uint8_t)(TP_FTM5_ADDR_DCHIP_ID >> 16);
	cmd[3] = (uint8_t)(TP_FTM5_ADDR_DCHIP_ID >> 8);
	cmd[4] = (uint8_t)TP_FTM5_ADDR_DCHIP_ID;
}

/* What the experiments saw, for the final TOUCH RESULT line. 0 = not run, 1 = no data (stall), 2 = data moved. */
struct res {
	int mux;	/* 0 not run, 1 AP owned already, 2 refused, 3 option failed, 4 option drove it low */
	int clk;	/* 0 not run, 1 failed, 2 applied */
	int f, loop, rst, id;
	int loop_match;
	int ready;
	unsigned idb0, idb1;
};

/* 0088 tg_identify */
static int identify(struct tp *t, struct res *r)
{
	uint8_t cmd[5], id[4], want[2];
	uint64_t w0 = 0, w1 = 0;
	unsigned i;
	int match;

	want[0] = (uint8_t)TOUCH_CHIP_ID0;
	want[1] = (uint8_t)TOUCH_CHIP_ID1;
	id_cmd(cmd);
	t->last_st = t->last_ch = 0;
	if (xfer_rd(t, cmd, sizeof(cmd), &w0, &w1, sizeof(id))) {
		say("identify: SPI transfer timed out (no clock, or the pads are not the SPI function)");
		say("identify timeout: SPI status=%#x ch=%#x clk=%#x", rd(t, SPI + TP_SPI_STATUS),
		    rd(t, SPI + TP_SPI_CH_CFG), rd(t, SPI + TP_SPI_CLK_CFG));
		r->id = 1;
		return 0;
	}
	for (i = 0; i < sizeof(id); i++)
		id[i] = (uint8_t)rbyte(w0, w1, TP_FTM5_DUMMY_BYTES + i);
	r->id = 2;
	r->idb0 = id[0];
	r->idb1 = id[1];
	match = tp_chip_id_match(id, want);
	say("identify: reg %#x = %02x %02x %02x %02x, want %02x %02x -> %s", TP_FTM5_ADDR_DCHIP_ID, id[0], id[1],
	    id[2], id[3], want[0], want[1],
	    match == 1 ? "FTM5 answers" : match == 2 ? "FTM5 answers (bytes swapped)" :
	    (id[0] == 0xff && id[1] == 0xff) ? "no answer, MISO high (rails off, in reset, or bus not ours?)" :
	    (id[0] == 0 && id[1] == 0) ? "no answer, MISO low (rails off, in reset, or bus not ours?)" :
	    "unexpected value");
	return match != 0;
}

/* 0090 tg_variant: A..E; returns 1 when the 5-byte burst completed. */
static int variant(struct tp *t, unsigned n)
{
	uint8_t cmd[5];
	uint32_t v;
	int err;

	switch (n) {
	case 0:	/* A: CLK_CFG |= ENCLK, PSR = 3 */
		v = rd(t, SPI + TP_SPI_CLK_CFG);
		wr(t, SPI + TP_SPI_CLK_CFG, (v & ~TP_CLK_PSR_MASK) | 3u | TP_CLK_ENCLK);
		break;
	case 2:	/* C: USI_CON reset released, USI_OPTION CLKREQ_ON set, CLKSTOP_ON clear */
		wr(t, USI + TP_USI_CON, rd(t, USI + TP_USI_CON) & ~TP_USI_CON_RESET);
		delay_us(1);
		v = rd(t, USI + TP_USI_OPTION);
		wr(t, USI + TP_USI_OPTION, (v & ~TP_USI_OPT_CLKSTOP) | TP_USI_OPT_CLKREQ);
		break;
	case 3:	/* D: USI_CON reset set then cleared, SW_CONF written again */
		wr(t, USI + TP_USI_CON, rd(t, USI + TP_USI_CON) | TP_USI_CON_RESET);
		delay_us(1);
		wr(t, USI + TP_USI_CON, rd(t, USI + TP_USI_CON) & ~TP_USI_CON_RESET);
		delay_us(1);
		v = rd(t, TOUCH_USI_MODE_REG);
		wr(t, TOUCH_USI_MODE_REG, (v & ~(uint32_t)TOUCH_USI_MODE_MASK) | TOUCH_USI_MODE_SPI);
		break;
	case 4:	/* E: engine-driven chip select, cs pad switched to the SPI function for this variant only */
		wr(t, SPI + TP_SPI_CS_REG, TP_CS_AUTO | TP_CS_NSC_CNT_2);
		pin_set_func(t, PIN(CS), TOUCH_SPI_FUNC);
		pin_report(t, "cs(E)", PIN(CS));
		break;
	default:	/* B: nothing but the order in the burst */
		break;
	}
	spi_engine_init(t);	/* the engine reset of spi_setup, after the USI/clock change */
	t->last_st = t->last_ch = 0;
	id_cmd(cmd);
	if (n == 4) {	/* the chip select is not driven around the burst */
		err = spi_burst(t, cmd, 0, sizeof(cmd), 0);
	} else {
		pin_put(t, PIN(CS), 0);
		delay_us(1);
		err = spi_burst(t, cmd, 0, sizeof(cmd), n == 1);
	}
	delay_us(TP_FTM5_SPI_DELAY_CS_US);
	pin_put(t, PIN(CS), 1);
	if (n == 4)	/* back to the GPIO chip select */
		pin_set_func(t, PIN(CS), 1);
	delay_us(2);
	say("VARIANT %c: %s, status=%#x ch=%#x (at timeout: status=%#x ch=%#x) clk=%#x", (char)('A' + n),
	    err ? "timeout" : "completed", rd(t, SPI + TP_SPI_STATUS), rd(t, SPI + TP_SPI_CH_CFG), t->last_st,
	    t->last_ch, rd(t, SPI + TP_SPI_CLK_CFG));
	return err == 0;
}

static void say_rx(const char *what, uint64_t w, unsigned n)
{
	say("%s rx: %02x %02x %02x %02x %02x (n=%u)", what, (unsigned)(w & 0xff), n > 1 ? (unsigned)((w >> 8) & 0xff) : 0,
	    n > 2 ? (unsigned)((w >> 16) & 0xff) : 0, n > 3 ? (unsigned)((w >> 24) & 0xff) : 0,
	    n > 4 ? (unsigned)((w >> 32) & 0xff) : 0, n);
}

/* X1, variant F: ABL-exact manual chip select. The setup already skipped MODE_CFG / INT_EN / SWAP_CFG (spi_setup, cs). */
static void exp_variant_f(struct tp *t, struct res *r)
{
	uint8_t cmd[5];
	uint64_t rx = 0;
	int err;

	id_cmd(cmd);
	t->last_st = t->last_ch = 0;
	cs_begin(t);
	say("VARIANT F: cs_reg=%#x after CS_REG &= ~1 and GPIO low", rd(t, SPI + TP_SPI_CS_REG));
	err = abl_burst(t, cmd, &rx, sizeof(cmd));
	cs_end(t);
	say("VARIANT F: %s, status=%#x ch=%#x (at timeout: status=%#x ch=%#x) clk=%#x cs=%#x",
	    err ? "timeout" : "completed", rd(t, SPI + TP_SPI_STATUS), rd(t, SPI + TP_SPI_CH_CFG), t->last_st,
	    t->last_ch, rd(t, SPI + TP_SPI_CLK_CFG), rd(t, SPI + TP_SPI_CS_REG));
	if (!err)
		say_rx("VARIANT F", rx, sizeof(cmd));
	r->f = err ? 1 : 2;
}

/* X3: self-loopback (MODE_CFG bit 3) with FB_CLK 0 and 1; the RX bytes must equal the TX bytes. */
static void exp_loop(struct tp *t, struct sv *s, struct res *r)
{
	uint8_t tx[5];
	unsigned pass, i;

	id_cmd(tx);
	s->mode = rd(t, SPI + TP_SPI_MODE_CFG);
	s->fb = rd(t, SPI + TP_SPI_FB_CLK);
	wr(t, SPI + TP_SPI_MODE_CFG, s->mode | TP_MODE_LOOPBACK);
	r->loop = 1;
	r->loop_match = 0;
	for (pass = 0; pass < 2 && !t->fault; pass++) {
		int err, match = 1;
		uint64_t rx = 0xeeeeeeeeeeull;

		wr(t, SPI + TP_SPI_FB_CLK, pass);	/* FB_CLK 0 first, then 1 */
		t->last_st = t->last_ch = 0;
		err = abl_burst(t, tx, &rx, sizeof(tx));
		for (i = 0; i < sizeof(tx); i++)
			match &= (unsigned)((rx >> (8u * i)) & 0xffu) == tx[i];
		say("loopback FB_CLK=%u: %s, status=%#x ch=%#x (at timeout: status=%#x ch=%#x)", pass,
		    err ? "timeout" : (match ? "rx == tx" : "rx != tx"), rd(t, SPI + TP_SPI_STATUS),
		    rd(t, SPI + TP_SPI_CH_CFG), t->last_st, t->last_ch);
		if (!err) {
			say_rx("loopback", rx, sizeof(tx));
			r->loop = 2;
			r->loop_match |= match << pass;
		}
	}
	t->fault = 0;	/* put the engine back even after a failure */
	wr(t, SPI + TP_SPI_MODE_CFG, s->mode);
	wr(t, SPI + TP_SPI_FB_CLK, s->fb);
}

/* X4: pads PUD := 0 (as Linux), reset pad low 10 ms then high (0088 tg_reset_and_ready), then up to 1.5 s polling the
 * FIFO (the same tg_read shape) for the controller-ready event 0x03. */
static void exp_rst(struct tp *t, struct sv *s, struct res *r)
{
	static const uint8_t cmd[1] = { TP_FTM5_CMD_FIFO_READ };
	uint64_t t0;
	unsigned i, polls = 0, rounds = 0, logged = 0;

	s->pud_old = 0;
	for (i = 0; i < NPUD_PADS; i++) {
		s->pud_old |= pin_pud(t, pud_pad(i)) << (4u * i);
		s->pud = 1;
	}
	for (i = 0; i < NPUD_PADS; i++)
		pin_set_pud(t, pud_pad(i), 0);
	say("rst: PUD of clk/mosi/miso/cs/reset (nibbles, low = clk) %#x -> 0", s->pud_old);
	pin_put(t, PIN(RESET), 0);
	pin_set_func(t, PIN(RESET), 1);
	delay_us(10000);
	pin_put(t, PIN(RESET), 1);
	r->rst = 1;
	t0 = us();
	while (!t->fault && rounds++ < 400u && !expired(t0, 1500000u, &polls)) {
		uint64_t w0 = 0, w1 = 0;
		unsigned e0, e1, e2, e3, e4, e5, e6, e7;

		if (xfer_rd(t, cmd, 1, &w0, &w1, TP_FTM5_EVENT_SIZE)) {
			say("rst: FIFO read timed out");
			break;
		}
		r->rst = 2;
		e0 = rbyte(w0, w1, 1);
		e1 = rbyte(w0, w1, 2);
		e2 = rbyte(w0, w1, 3);
		e3 = rbyte(w0, w1, 4);
		e4 = rbyte(w0, w1, 5);
		e5 = rbyte(w0, w1, 6);
		e6 = rbyte(w0, w1, 7);
		e7 = rbyte(w0, w1, 8);
		if (e0 != 0 && logged++ < 8u)	/* the RAM log is small: first 8 only */
			say("rst: boot ev %02x %02x %02x %02x %02x %02x %02x %02x", e0, e1, e2, e3, e4, e5, e6, e7);
		if (e0 == TP_FTM5_EVT_CONTROLLER_READY) {
			r->ready = 1;
			break;
		}
		delay_us(5000);
	}
	say("rst: %s", r->ready ? "controller-ready event seen" : "no controller-ready event");
}

static void pud_restore(struct tp *t, struct sv *s)
{
	unsigned i;

	t->fault = 0;
	if (s->miso) {
		pin_set_pud(t, PIN(MISO), s->miso_old);
		s->miso = 0;
	}
	if (s->pud) {
		for (i = 0; i < NPUD_PADS; i++)
			pin_set_pud(t, pud_pad(i), (s->pud_old >> (4u * i)) & 0xfu);
		s->pud = 0;
	}
}

static const char *stage(int v, const char *none, const char *stall, const char *moved)
{
	return v == 0 ? none : v == 1 ? stall : moved;
}

static void summary(const struct res *r, unsigned opts)
{
	char mv[24];
	unsigned p = 0;
	const char *mux = r->mux == 0 ? "na" : r->mux == 1 ? "ap-owned" : r->mux == 2 ? "refused" :
			  r->mux == 3 ? "failed" : "driven-low";

	mv[0] = 0;
	if (r->f == 2)
		mv[p++] = 'F', mv[p++] = ',';
	if (r->loop == 2)
		mv[p++] = 'L', mv[p++] = ',';
	if (r->rst == 2)
		mv[p++] = 'R', mv[p++] = ',';
	if (r->id == 2)
		mv[p++] = 'I', mv[p++] = ',';
	if (p)
		mv[p - 1] = 0;
	else
		mv[0] = '-', mv[1] = 0;
	const char *loop = r->loop == 0 ? "na" : r->loop == 1 ? "stall" : r->loop_match == 3 ? "rx==tx" :
			   r->loop_match == 1 ? "fb0-only" : r->loop_match == 2 ? "fb1-only" : "rx!=tx";
	const char *id = r->id == 2 ? (r->idb0 == TOUCH_CHIP_ID0 && r->idb1 == TOUCH_CHIP_ID1 ? "chip-id-ok" : "rx-no-chip") :
			 stage(r->id, "na", "stall", "");

	/* moved: F = variant F, L = loopback, R = reset FIFO read, I = identify (docs/AUTOMATION.md) */
	say_result("opts=%#x mux=%s clk=%s F=%s loop=%s rst=%s id=%s moved=%s", opts, mux, stage(r->clk, "na", "failed", "applied"),
		   stage(r->f, "na", "stall", "data"), loop, stage(r->rst, "na", "stall", r->ready ? "ready" : "data"), id, mv);
}

void touch_probe_run(unsigned opts)
{
	struct tp t = { 0, 0, 0, 0, opts };
	struct sv s = { 0, 0, 0, 0, 0, 0, 0, 0, 0 };
	struct res r = { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 };
	int ok = 0;

	if (!mux_step(&t, &r.mux)) {
		summary(&r, opts);
		return;
	}
	(void)cmu_enable(&t);	/* an aborted sequence is reported; the identify below shows whether it mattered */
	if (!t.fault && (opts & (TP_O_OSC | TP_O_TOP)))
		r.clk = clk_source(&t, &s) ? 2 : 1;
	if (t.fault) {
		say("a register faulted, sequence stopped before the SPI engine");
		clk_restore(&t, &s);
		summary(&r, opts);
		return;
	}
	spi_setup(&t);
	spi_diag(&t, "after setup");
	if (t.fault) {
		say("a register faulted during the SPI setup, sequence stopped");
		summary(&r, opts);
		return;
	}
	if (opts & TP_O_CS)
		exp_variant_f(&t, &r);
	if ((opts & TP_O_LOOP) && !t.fault)
		exp_loop(&t, &s, &r);
	if ((opts & TP_O_RST) && !t.fault)
		exp_rst(&t, &s, &r);
	if ((opts & TP_O_MISOPU) && !t.fault) {
		s.miso_old = pin_pud(&t, PIN(MISO));
		s.miso = 1;
		pin_set_pud(&t, PIN(MISO), 3);
		say("misopu: MISO PUD %#x -> 3 for the identify", s.miso_old);
	}
	if (!t.fault)
		ok = identify(&t, &r);
	if (!ok && !t.fault && !(opts & TP_O_EXP)) {
		for (unsigned n = 0; !ok && n < TP_NVARIANTS && !t.fault; n++) {
			if (variant(&t, n)) {
				say("VARIANT %c completes, identifying again", (char)('A' + n));
				spi_diag(&t, "after variant");
				ok = identify(&t, &r);
				break;
			}
		}
	}
	/* restore: the pulls always; the clock source and the mux/div only if no burst moved data */
	pud_restore(&t, &s);
	if (!(r.f == 2 || r.rst == 2 || r.id == 2))
		clk_restore(&t, &s);
	if (t.fault)
		say("a register faulted or a write was refused, sequence stopped");
	say(ok ? "phase 1 result: chip answered" : "phase 1 result: chip did not answer; nothing else is touched");
	summary(&r, opts);
}

#if defined(TOUCH_RAILS) || defined(TOUCH_DVFS)
/* ---- ACPM rails (acpm.c, docs/research/23): the touch rails LDO25M / LDO26M enable = bit 7 of S2MPG10 0x5C / 0x5D ----
 * Steps (shim.c checkpoints after each): 0 describe the channel (reads only), 1 read both registers, 2 (TOUCH_RAILS_WRITE) update + read back.
 * Every transaction is printed whole: sequence number, command words, reply words, return code, elapsed ticks. */

#ifdef TOUCH_RAILS
static const char *rail_name(unsigned i)
{
	return i ? "L26M" : "L25M";
}

static void say_xfer(const char *what, unsigned i, const struct acpm_xfer *x)
{
	unsigned long long f = counter_freq(), dt = x->t_end - x->t_bell;

	/* one prefix per line ("grep ACPM" finds all of it); each stays inside the 82-byte split of say() */
	say("ACPM %s %s seq=%u req=%08x %08x %08x %08x", what, rail_name(i), x->seq, x->req[0], x->req[1], x->req[2], x->req[3]);
	say("ACPM %s %s rep=%08x %08x %08x %08x", what, rail_name(i), x->rep[0], x->rep[1], x->rep[2], x->rep[3]);
	say("ACPM %s %s ret=%u val=%#x err=%d", what, rail_name(i), x->ret, x->value, x->err);
	say("ACPM %s %s writes=%u published=%u ticks=%llu us=%llu", what, rail_name(i), x->n, x->published, dt,
	    f ? dt * 1000000ull / f : 0ull);
	if (x->err)
		say("ACPM %s: %s (%s)", acpm_unsure(x->why) ? "unsure" : "refused", acpm_why(x->why),
		    x->n ? "writes were made, see writes/published" : "nothing written");
	if (x->err && x->published) {	/* nothing was rolled back: this is the queue as the failure left it */
		say("ACPM %s %s STATE seq=%u req rear=%d front=%d rsp rear=%d front=%d", what, rail_name(i), x->seq,
		    (int)x->state[0], (int)x->state[1], (int)x->state[2], (int)x->state[3]);
		say("ACPM %s %s the request stays queued; channel left as is, rerun only after reading this", what, rail_name(i));
	}
}

#endif

#define QDUMP_MAX	48u	/* words of each queue buffer printed (12 lines): a 16-byte stride shows as one element per line */

static void qdump(const struct acpm_cfg *c, const struct acpm_chan *h, int rsp)
{
	uint32_t i, n = h->qlen >= 1 && h->qlen * 4u < QDUMP_MAX ? h->qlen * 4u : QDUMP_MAX, w[4];

	for (i = 0; i < n; i += 4) {
		uint32_t j;

		for (j = 0; j < 4; j++)
			if (i + j >= n || !acpm_qword(c, h, rsp, i + j, &w[j]))
				w[j] = 0xdeadbeefu;	/* beyond the dump, or unreadable (bounded to the SRAM window, a fault is caught) */
		say("ACPM %s buf+%03x %08x %08x %08x %08x", rsp ? "rsp" : "req", 4u * i, w[0], w[1], w[2], w[3]);
	}
}

/* Prints the descriptor, the pointers and the raw queue-buffer words WHATEVER the validation said (they are what a refusal needs),
 * then the verdict. The buffer dump is how a human confirms the element size: slots 16 bytes apart (one per line here) whose words
 * look like commands (seq in bits 21:16, 0x01 in bits 11:8 of word 0) mean q_elem_size is in bytes. */
/* One guarded read, announced BEFORE it happens and given time to reach the USB host: if the bus never answers (a hang, which no fault
 * guard can catch) the last printed "ACPM pre: reading ..." line names the address that did it. */
static void rails_pre_read(const char *what, uintptr_t pa)
{
	uint32_t v = 0;

	say("ACPM pre: reading %s %#llx", what, (unsigned long long)pa);
	wait_ticks(counter_freq() / 5);		/* the console wait loop pumps the USB log */
	if (try_read32(pa, &v))
		say("ACPM pre: %s %#llx = %08x", what, (unsigned long long)pa, v);
	else
		say("ACPM pre: %s %#llx unreadable (fault)", what, (unsigned long long)pa);
	wait_ticks(counter_freq() / 10);
}

static int rails_describe(const struct acpm_cfg *c)
{
	struct acpm_chan h;
	int e;
	uint32_t i;

	rails_pre_read("APM SRAM init word", c->sram_base + c->initdata_off);
	rails_pre_read("AP2APM mailbox INTSR0", c->mbox_base + c->intsr0);
	e = acpm_describe(c, &h);

	say("ACPM cfg mbox=%#llx sram=%#llx init=+%#x", (unsigned long long)c->mbox_base, (unsigned long long)c->sram_base,
	    c->initdata_off);
	say("ACPM cfg ch=%u speedy=%u pmic=%u stride=%#x", c->ch_id, c->speedy, c->pmic_addr, c->desc_stride);
	say("ACPM cfg layout_verified=%u elem_unit=%u elem_unit_verified=%u", c->layout_verified, c->elem_unit, c->elem_unit_verified);
	say("ACPM init[0..3]=%08x %08x %08x %08x", h.init[0], h.init[1], h.init[2], h.init[3]);
	say("ACPM init[4..7]=%08x %08x %08x %08x", h.init[4], h.init[5], h.init[6], h.init[7]);
	if (e == ACPM_E_FAULT) {
		say("ACPM describe: an SRAM/mailbox read faulted, nothing else is touched");
		return e;
	}
	say("ACPM table n=%u tab=+%#x idx=%u", h.n, h.tab, h.idx);
	if (h.why == ACPM_W_NOCH) {	/* no such channel: show the ids that are there, 6 per line */
		for (i = 0; i < h.n; i += 6) {
			uint32_t j, id[6];

			for (j = 0; j < 6; j++)
				if (i + j >= h.n || !acpm_chan_id(c, &h, i + j, &id[j]))
					id[j] = 0xdeadbeefu;
			say("ACPM ids[%u..]=%x %x %x %x %x %x", i, id[0], id[1], id[2], id[3], id[4], id[5]);
		}
	}
	if (h.have_desc) {
		say("ACPM desc[0..5]=%08x %08x %08x %08x %08x %08x", h.d[0], h.d[1], h.d[2], h.d[3], h.d[4], h.d[5]);
		say("ACPM desc[6..11]=%08x %08x %08x %08x %08x %08x", h.d[6], h.d[7], h.d[8], h.d[9], h.d[10], h.d[11]);
		say("ACPM desc[12..17]=%08x %08x %08x %08x %08x %08x", h.d[12], h.d[13], h.d[14], h.d[15], h.d[16], h.d[17]);
		say("ACPM q len=%u elem=%u words=%u bytes=%u ap_poll=%u INTSR0=%#x", h.qlen, h.esz, h.esz_w, h.esz_b, h.ap_poll, h.intsr0);
		say("ACPM req(AP->APM) rear@%#llx=%u front@%#llx=%u", (unsigned long long)h.req_rear_pa, h.rr,
		    (unsigned long long)h.req_front_pa, h.rf);
		say("ACPM req base@%#llx", (unsigned long long)h.req_base_pa);
		say("ACPM rsp(APM->AP) rear@%#llx=%u front@%#llx=%u", (unsigned long long)h.rsp_rear_pa, h.sr,
		    (unsigned long long)h.rsp_front_pa, h.sf);
		say("ACPM rsp base@%#llx", (unsigned long long)h.rsp_base_pa);
		qdump(c, &h, 0);
		qdump(c, &h, 1);
	}
	if (e) {
		say("ACPM describe refused: %s (why=%u)", acpm_why(h.why), h.why);
		return e;
	}
	say("ACPM descriptor validated; queues %s", h.rr == h.rf && h.sr == h.sf ? "empty" : "NOT empty (the shim will refuse to write)");
	if (!c->layout_verified)
		say("ACPM unsure: %s", acpm_why(ACPM_W_LAYOUT));
	if (!c->elem_unit_verified)
		say("ACPM unsure: %s", acpm_why(ACPM_W_UNIT));
	return ACPM_OK;
}

#ifdef TOUCH_RAILS
static void rail_line(unsigned i, uint32_t v)
{
	say("ACPM %s value=%#x enable(bit7)=%u vsel(bits5:0)=%#x", rail_name(i), v, (v >> 7) & 1u, v & 0x3fu);
}

int touch_probe_rails(unsigned step)
{
	struct acpm_cfg c;
	struct acpm_trace t;
	uint32_t v[2], a[2];
	int e = ACPM_OK, i, got = 0, after = 0;

	acpm_cfg_init(&c);
	if (step == 0)
		return rails_describe(&c);
	v[0] = v[1] = a[0] = a[1] = 0;
	acpm_xfer_zero(&t.a);
	acpm_xfer_zero(&t.b);
	t.seq = 1;
	t.n = 0;
	if (step == 1) {
		for (i = 0; i < 2 && !e; i++) {
			e = acpm_pmic_read(&c, c.rail[i].reg, &v[i], &t);
			say_xfer("read", (unsigned)i, &t.a);
			if (!e) {
				rail_line((unsigned)i, v[i]);
				got++;
			}
		}
		say_result("rails read: L25M=%s L26M=%s %s", got > 0 ? (v[0] & 0x80 ? "on" : "off") : "?",
			   got > 1 ? (v[1] & 0x80 ? "on" : "off") : "?", e ? "REFUSED-or-FAILED, nothing more is touched" : "ok");
		return e;
	}
#ifndef TOUCH_RAILS_WRITE
	say_result("rails update: not in this image (build with --touch-probe=rails)");
	(void)after;
	(void)a;
	return ACPM_E_UNSURE;
#else
	/* step 2: DVDD (LDO25M) before AVDD (LDO26M) (doc 23 section 4, order is a guess), no second try after a failure.
	 * Each update first reads its register itself (the "pre-read" lines are the before value). */
	for (i = 0; i < 2 && !e; i++) {
		if (i)
			wait_ticks(counter_freq() / 1000);	/* >= 1 ms between DVDD and AVDD, as ftm5_fts.c:5395-5422 [src] */
		e = acpm_pmic_update(&c, c.rail[i].reg, 0x80u, 0x80u, &t);
		say_xfer("pre-read", (unsigned)i, &t.a);
		if (!t.a.err)
			v[i] = t.a.value;
		if (t.n > 1)
			say_xfer("update", (unsigned)i, &t.b);
		if (e == ACPM_E_ON)	/* already on (ABL left it): nothing to switch, still do the other rail */
			e = ACPM_OK;
	}
	wait_ticks(counter_freq() / 100);	/* ~10 ms settle (doc 23 section 4, a guess) */
	for (i = 0; i < 2; i++) {	/* read back even after a failed update: it shows what state the rails are in */
		t.n = 0;
		if (!acpm_pmic_read(&c, c.rail[i].reg, &a[i], &t)) {
			rail_line((unsigned)i, a[i]);
			after += (a[i] & 0x80u) ? 1 : 0;
		}
		say_xfer("readback", (unsigned)i, &t.a);
	}
	say_result("rails update: L25M %#x->%#x L26M %#x->%#x on=%d/2 %s", v[0], a[0], v[1], a[1], after, e ? "REFUSED-or-FAILED" : "ok");
	return e;
#endif
}
#endif

#ifdef TOUCH_DVFS
/* ---- ACPM DVFS (acpm.c, docs/research/25): CPU cluster rates. Steps: 0 describe the DVFS channel (reads only), 1 FREQ_GET of every
 * configured domain, 2 (TOUCH_DVFS_WRITE) FREQ_REQ of the rates in DVFS_KHZ0/1/2 (0 = leave that cluster) and read every domain back. */

static const char *dvfs_name(uint32_t dom)
{
	return dom == 2 ? "CL0" : dom == 3 ? "CL1" : dom == 4 ? "CL2" : "CL?";
}

static void dvfs_xfer_line(const char *what, uint32_t dom, const struct acpm_xfer *x)
{
	unsigned long long f = counter_freq(), dt = x->t_end - x->t_bell;

	say("DVFS %s %s seq=%u req=%08x %08x %08x %08x", what, dvfs_name(dom), x->seq, x->req[0], x->req[1], x->req[2], x->req[3]);
	say("DVFS %s %s rep=%08x %08x %08x %08x", what, dvfs_name(dom), x->rep[0], x->rep[1], x->rep[2], x->rep[3]);
	say("DVFS %s %s err=%d val=%u reply=%u writes=%u published=%u us=%llu", what, dvfs_name(dom), x->err, x->value, x->had_reply, x->n,
	    x->published, f ? dt * 1000000ull / f : 0ull);
	if (x->err)
		say("DVFS %s: %s (%s)", acpm_unsure(x->why) ? "unsure" : "refused", acpm_why(x->why),
		    x->n ? "writes were made, see writes/published" : "nothing written");
	if (x->err && x->published) {
		say("DVFS %s %s STATE seq=%u req rear=%d front=%d rsp rear=%d front=%d", what, dvfs_name(dom), x->seq, (int)x->state[0],
		    (int)x->state[1], (int)x->state[2], (int)x->state[3]);
		say("DVFS %s %s the request stays queued; channel left as is, rerun only after reading this", what, dvfs_name(dom));
	}
}

#ifdef TOUCH_DVFS_WRITE
static uint32_t dvfs_want(unsigned i)
{
	static const uint32_t want[3] = { DVFS_KHZ0, DVFS_KHZ1, DVFS_KHZ2 };

	return i < 3 ? want[i] : 0;
}
#endif

#ifdef TOUCH_DVFS_WRITE
/* L-28 (xnu patch 0367): what the screen lines say, kept for the XNU command
 * line. before = step 1 FREQ_GET, after = the step 2 read-back (0 = not read),
 * rc: 0 every asked cluster read back at the asked rate, 1 request refused or
 * failed, 2 a read-back differs, 3 a read-back failed, 4 step 2 not reached. */
#endif

int touch_probe_dvfs(unsigned step, struct touch_dvfs_res *res)
{
	/* style-allow: func-len one linear probe (describe, step 1 read, step 2 write + read-back),
	 * reviewed as a whole */
	/* style-allow: sized-types int is the status type of the acpm_* calls (0 = ok) */
	/* style-allow: assert audited object (touchprobe.o): every refusal is handled and logged
	 * (rule 6), no shim_assert_fail */
	struct acpm_cfg c;
	struct acpm_trace t;
	uint32_t dom, khz, v[8];
	int e = ACPM_OK, i, n = acpm_dvfs_ndom(), got = 0;

#ifndef TOUCH_DVFS_WRITE
	(void)res;	/* only a TOUCH_DVFS_WRITE image records the CPU result */
#else
	if (res) {
		res->on = 1;
		res->asked[0] = dvfs_want(0);
		res->asked[1] = dvfs_want(1);
		res->asked[2] = dvfs_want(2);
	}
#endif
	acpm_cfg_init(&c);
	if (step == 0) {
		c.ch_id = c.dvfs_ch;		/* describe the DVFS channel, not the PMIC one */
		return rails_describe(&c);
	}
	if (n < 1 || n > 8)
		return ACPM_E_UNSURE;
	acpm_xfer_zero(&t.a);
	acpm_xfer_zero(&t.b);
	t.seq = 1;
	t.n = 0;
	for (i = 0; i < n; i++)
		v[i] = 0;
	if (step == 1) {
		for (i = 0; i < n && !e; i++) {
			(void)acpm_dvfs_dom(i, &dom);
			e = acpm_dvfs_get(&c, dom, &v[i], &t);
			dvfs_xfer_line("get", dom, &t.a);
			if (!e) {
				say("DVFS %s rate=%u kHz", dvfs_name(dom), v[i]);
				got++;
			}
		}
#ifdef TOUCH_DVFS_WRITE
		if (res) {	/* constant offsets: the audit allows no variable-index store here */
			res->before[0] = v[0];
			res->before[1] = v[1];
			res->before[2] = v[2];
		}
#endif
		say_result("dvfs read: %d/%d domains %s", got, n, e ? "REFUSED-or-FAILED, nothing more is touched" : "ok");
		return e;
	}
#ifndef TOUCH_DVFS_WRITE
	say_result("dvfs set: not in this image (build with --touch-probe=dvfs --dvfs K0,K1,K2)");
	(void)khz;
	return ACPM_E_UNSURE;
#else
	/* step 2: the clusters one at a time in index order; a refusal or failure stops the rest (no second try). The read-back below
	 * is printed even after a failure: it shows what the firmware did, and it (not the request return codes) decides the result. */
	int asked = 0, match = 0, differ = 0, unver = 0;
	uint32_t av[3] = { 0, 0, 0 };

	for (i = 0; i < n && !e; i++) {
		(void)acpm_dvfs_dom(i, &dom);
		khz = dvfs_want((unsigned)i);
		if (!khz)
			continue;
		e = acpm_dvfs_set(&c, dom, khz, &t);
		if (t.a.seq || t.a.err)
			dvfs_xfer_line("pre-read", dom, &t.a);	/* also a refusal: it carries the reason */
		if (t.n > 1)
			dvfs_xfer_line("request", dom, &t.b);
		if (e == ACPM_E_ON)	/* already at that rate: not a failure */
			e = ACPM_OK;
	}
	wait_ticks(counter_freq() / 100);	/* ~10 ms for the firmware to switch (a guess; the read-back shows whether it did) */
	for (i = 0; i < n; i++) {
		uint32_t want;

		(void)acpm_dvfs_dom(i, &dom);
		want = dvfs_want((unsigned)i);
		t.n = 0;
		khz = 0;
		if (!acpm_dvfs_get(&c, dom, &khz, &t))
			say("DVFS %s readback rate=%u kHz (asked %u)", dvfs_name(dom), khz, want);
		if (i < 3)
			av[i] = t.a.err ? 0 : khz;
		dvfs_xfer_line("readback", dom, &t.a);
		if (want) {
			asked++;
			if (t.a.err)
				unver++;
			else if (khz == want)
				match++;
			else
				differ++;
		}
	}
	if (res) {
		res->after[0] = av[0];
		res->after[1] = av[1];
		res->after[2] = av[2];
		res->rc = asked && match == asked ? 0 : differ ? 2 : unver ? 3 : 1;
	}
	if (asked && match == asked) {
		say_result("dvfs set: ok %d/%d read back", match, asked);
		return ACPM_OK;
	}
	say_result("dvfs set: %s %d/%d read back (differ %d, unread %d)", differ ? "MISMATCH" : unver ? "UNVERIFIED" : "REFUSED-or-FAILED",
		   match, asked, differ, unver);
	return e ? e : ACPM_E_REPLY;
#endif
}
#endif
#endif

#else	/* not gs101 (or no touch macros): empty stubs */

void touch_probe_dump(void) {}
int touch_probe_rails(unsigned step) { (void)step; return -1; }
int touch_probe_dvfs(unsigned step, struct touch_dvfs_res *res)
{
	/* style-allow: sized-types int is the status type of the acpm_* calls (0 = ok) */
	/* style-allow: assert audited object (touchprobe.o): no shim_assert_fail */
	(void)step;
	(void)res;
	return -1;
}
void touch_probe_run(unsigned opts) { (void)opts; }

#endif
