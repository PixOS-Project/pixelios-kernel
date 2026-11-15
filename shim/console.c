/*
 * Minimal text console on the ABL splash framebuffer. With the MMU off
 * every access is Device memory, so stay aligned (-mstrict-align).
 */
#include "console.h"
#include "font.h"
#include "ramlog.h"
#ifdef QRLOG
#include "qrlog.h"
#endif

#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8

/* Pager (post-run log replay): hold per page and total replay time, s. */
#define PAGER_HOLD_S	12
#define PAGER_TOTAL_S	600

volatile uint32_t *con_fb = (volatile uint32_t *)FB_BASE;
static unsigned fb_w = FB_W, fb_h = FB_H, fb_stride = FB_STRIDE;
static uint32_t con_bg;
static int row;

uint64_t counter_freq(void)
{
#if SOC_GS101
	return TIMER_FREQ;
#elif defined(__aarch64__)
	uint64_t f;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(f));
	return f;
#else
	return 1;
#endif
}

/* Service hook: the shim installs the USB gadget poll/pump here so the log
 * streams live from inside every console line and every wait loop (a hang
 * then shows as "the last line before silence"). Re-entrancy guarded: the
 * gadget itself logs through con_str. */
void (*con_service_hook)(void);
static int con_in_service;

void con_service(void)
{
	if (con_service_hook && !con_in_service) {
		con_in_service = 1;
		con_service_hook();
		con_in_service = 0;
	}
}

void wait_ticks(uint64_t ticks)
{
#ifdef __aarch64__
	uint64_t cntkctl;
	__asm__ volatile("mrs %0, cntkctl_el1" : "=r"(cntkctl));
	cntkctl |= (1 << 2) | (13 << 4); /* EVNTEN, EVNTI=13 (~2^14 ticks ≈ 0.67 ms @24.576 MHz) */
	__asm__ volatile("msr cntkctl_el1, %0; isb" : : "r"(cntkctl));
	uint64_t start;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(start));
	uint64_t until = start + ticks;
	/* bounded: until = start + ticks (CNTPCT deadline), the break below */
	for (;;) {
		uint64_t now;
		__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(now));
		if (now >= until)
			break;
		con_service();
		__asm__ volatile("wfe");
	}
#else
	(void)ticks;
#endif
}

#if SOC_GS101
static void screen_room(void)
{
	if ((unsigned)(row + 1) * CH_H + MARGIN <= fb_h)
		return;
	wait_ticks(counter_freq() * 3);
	con_clear(con_bg);
}
#else
static void screen_room(void) {}
#endif

#if SOC_GS101
#define UART_UTRSTAT	0x10
#define UART_UFCON	0x08
#define UART_UFSTAT	0x18
#define UART_UTXH	0x20
#define UART_TXE	(1U << 2)
#define UART_TX_FULL	(1U << 24)

static int uart_dead = 0;

static void uart_char(char c)
{
	volatile uint32_t *base = (volatile uint32_t *)UART_BASE;
	if (!UART_BASE || uart_dead)
		return;
#ifdef __aarch64__
	uint64_t start;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(start));
	uint64_t deadline = start + counter_freq() / 1000; /* ~1 ms */
	if (base[UART_UFCON / 4] & 1) {
		/* bounded: 1 ms deadline below; expiry sets uart_dead (no GAP: UART dead) */
		while (base[UART_UFSTAT / 4] & UART_TX_FULL) {
			uint64_t now;
			__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(now));
			if (now >= deadline) {
				uart_dead = 1;
				return;
			}
		}
	} else {
		/* bounded: 1 ms deadline below; expiry sets uart_dead (no GAP: UART dead) */
		while (!(base[UART_UTRSTAT / 4] & UART_TXE)) {
			uint64_t now;
			__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(now));
			if (now >= deadline) {
				uart_dead = 1;
				return;
			}
		}
	}
#endif
	*(volatile uint32_t *)((uintptr_t)base + UART_UTXH) = (uint32_t)(uint8_t)c;
}

static void uart_text(const char *s)
{
	/* bounded: the NUL of the string (literal or a buffer the caller terminated) */
	while (*s)
		uart_char(*s++);
}
#endif

void con_set_video(uintptr_t base, unsigned width, unsigned height, unsigned stride)
{
	con_fb = (volatile uint32_t *)base;
	fb_w = width;
	fb_h = height;
	fb_stride = stride;
	row = 0;
}

void con_clear(uint32_t bg)
{
	for (unsigned y = 0; y < fb_h; y++) {
		volatile uint32_t *line = con_fb + (uintptr_t)y * (fb_stride / 4);
		for (unsigned x = 0; x < fb_w; x++)
			line[x] = bg;
	}
	con_bg = bg;
	row = 0;
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > (int)fb_w || y0 + CH_H > (int)fb_h)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				volatile uint32_t *p = con_fb + (uintptr_t)(y0 + gy * SCALE + sy) * (fb_stride / 4)
						       + x0 + (uintptr_t)gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	/* bounded: the NUL of the string (literal or a buffer the caller terminated) */
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

static void fmt_dec(char *buf, unsigned v)
{
	char tmp[10];
	int n = 0;
	do {
		tmp[n++] = (char)('0' + v % 10);
		v /= 10;
		/* bounded: v /= 10 reaches 0 (<= 10 digits for 32 bits) */
	} while (v);
	for (int i = 0; i < n; i++)
		buf[i] = tmp[n - 1 - i];
	buf[n] = 0;
}

/* Compose the plain-text twin of a console line for the RAM log,
 * truncating at cap; returns the NUL position. */
static unsigned log_put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

static unsigned log_put_hex8(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char buf[9];
	fmt_hex(buf, v, 8);
	return log_put_txt(dst, cap, pos, buf);
}

#if SOC_GS101
static void uart_hex(uint32_t value)
{
	char buf[9];
	fmt_hex(buf, value, 8);
	for (int i = 0; i < 8; i++) uart_char(buf[i]);
}
#endif

void con_skip(void)
{
	screen_room();
	row++;
}

void con_str(const char *label, const char *s)
{
	screen_room();
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	char line[RAMLOG_LINE_MAX];
	unsigned pos = log_put_txt(line, sizeof line, 0, label);
	pos = log_put_txt(line, sizeof line, pos, " ");
	pos = log_put_txt(line, sizeof line, pos, s);
	line[pos] = 0;
	ramlog_line(line);
	#if SOC_GS101
	uart_text(label);
	uart_text(": ");
	uart_text(s);
	uart_text("\r\n");
	#endif
	con_service();
	row++;
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	screen_room();
	char buf[9];
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	char line[RAMLOG_LINE_MAX];
	unsigned pos = log_put_txt(line, sizeof line, 0, label);
	pos = log_put_txt(line, sizeof line, pos, " ");
	pos = log_put_txt(line, sizeof line, pos, a);
	pos = log_put_hex8(line, sizeof line, pos, va);
	if (b) {
		pos = log_put_txt(line, sizeof line, pos, " ");
		pos = log_put_txt(line, sizeof line, pos, b);
		pos = log_put_hex8(line, sizeof line, pos, vb);
	}
	line[pos] = 0;
	ramlog_line(line);
	#if SOC_GS101
	uart_text(label); uart_text(" "); uart_text(a);
	uart_hex(va);
	if (b) {
		uart_text(" "); uart_text(b);
		uart_hex(vb);
	}
	uart_text("\r\n");
	#endif
	con_service();
	row++;
}

/* ---- pager: replay the RAM log page by page for photographing ---- */

/* Content rows per page: total rows minus the reserved footer row. */
static unsigned pager_content_rows(void)
{
	if (fb_h < 2 * MARGIN + CH_H)
		return 0;
	return (fb_h - 2 * MARGIN) / CH_H - 1;
}

static void pager_render_line(const char *s)
{
	unsigned n = 0;
	/* bounded: the NUL of the string (literal or a buffer the caller terminated) */
	while (s[n])
		n++;
	unsigned lab = n < LABEL_COLS ? n : LABEL_COLS;
	for (unsigned i = 0; i < lab; i++)
		put_char((int)i, s[i], YELLOW);
	for (unsigned i = LABEL_COLS; i < n; i++)
		put_char((int)i, s[i], WHITE);
}

static void pager_footer(unsigned page, unsigned pages)
{
	char buf[64], d[10];
	unsigned pos = log_put_txt(buf, sizeof buf, 0, "PAGE ");
	fmt_dec(d, page);
	pos = log_put_txt(buf, sizeof buf, pos, d);
	pos = log_put_txt(buf, sizeof buf, pos, "/");
	fmt_dec(d, pages);
	pos = log_put_txt(buf, sizeof buf, pos, d);
	pos = log_put_txt(buf, sizeof buf, pos, "  PHOTO EACH PAGE");
	buf[pos] = 0;
	put_str(0, buf, YELLOW);
}


#ifdef QRLOG
/* ---- QR replay: the tail of the RAM log as a looping series of QR codes (tools/qr-log.py reads them) ---- */
#ifndef QRLOG_TAIL
#define QRLOG_TAIL	6000u	/* bytes of log shown (most recent) */
#endif
#ifndef QRLOG_CHUNK
#define QRLOG_CHUNK	200u	/* text bytes per QR: smaller = bigger modules = easier for a webcam */
#endif
#ifndef QRLOG_FRAME_MS
#define QRLOG_FRAME_MS	800u
#endif
#ifndef QRLOG_SECONDS
#define QRLOG_SECONDS	120u
#endif

static char qr_snap[QRLOG_TAIL + RAMLOG_LINE_MAX + 2];
static char qr_frame[QRLOG_CHUNK + QRLOG_HDR_MAX + 1];

static void qr_replay(void)
{
	unsigned lines = ramlog_lines(), first = 0, bytes = 0;
	char line[RAMLOG_LINE_MAX];
	/* drop oldest whole lines until the rest fits in QRLOG_TAIL */
	for (unsigned i = lines; i > 0; i--) {
		unsigned l = ramlog_copy_line(i - 1, line, sizeof line) + 1;
		if (bytes + l > QRLOG_TAIL)
			break;
		bytes += l;
		first = i - 1;
	}
	unsigned len = 0;
	for (unsigned i = first; i < lines; i++) {
		len += ramlog_copy_line(i, qr_snap + len, sizeof line);
		qr_snap[len++] = '\n';
	}
	unsigned n = qrlog_nframes(len, QRLOG_CHUNK);
	uint64_t end_ticks = counter_freq() * QRLOG_SECONDS, spent = 0;
	uint64_t per = counter_freq() / 1000 * QRLOG_FRAME_MS;
	while (spent < end_ticks)
		for (unsigned i = 0; i < n && spent < end_ticks; i++, spent += per) {
			qrlog_make_frame(qr_snap, len, QRLOG_CHUNK, i, qr_frame);
			qrlog_draw(con_fb, fb_w, fb_h, fb_stride, qr_frame);
			wait_ticks(per);
		}
}
#endif

/* Replay the whole RAM log on screen, one screen-sized page at a time,
 * each page held ~PAGER_HOLD_S seconds with a "page N/M" footer, looping
 * the full log for ~PAGER_TOTAL_S seconds. wait_ticks (wfe) only; the
 * pager never logs its own pages. */
void pager_replay(void)
{
	unsigned rows = pager_content_rows();
	unsigned pages = ramlog_page_count(rows);
	char line[RAMLOG_LINE_MAX];
	unsigned remaining = PAGER_TOTAL_S;
#ifdef QRLOG
	qr_replay();
#endif
	if (!pages) {
		con_clear(0xff000000U);
		row = 0;
		pager_render_line("PAGER log empty");
		wait_ticks(counter_freq() * 3);
		return;
	}
	/* bounded: remaining drops by PAGER_HOLD_S (12) per page and the inner loop breaks at 0 */
	while (remaining) {
		for (unsigned p = 1; p <= pages; p++) {
			con_clear(0xff000000U);
			unsigned total = ramlog_lines();
			unsigned first = (p - 1) * rows;
			unsigned count = total > first ? total - first : 0;
			if (count > rows)
				count = rows;
			for (unsigned i = 0; i < count; i++) {
				ramlog_copy_line(first + i, line, sizeof line);
				row = (int)i;
				pager_render_line(line);
			}
			row = (int)rows;
			pager_footer(p, pages);
			#if SOC_GS101
			char dp[10], dt[10];
			fmt_dec(dp, p);
			fmt_dec(dt, pages);
			uart_text("PAGER page ");
			uart_text(dp);
			uart_text("/");
			uart_text(dt);
			uart_text("\r\n");
			#endif
			wait_ticks(counter_freq() * PAGER_HOLD_S);
			remaining = remaining > PAGER_HOLD_S ? remaining - PAGER_HOLD_S : 0;
			if (!remaining)
				break;
		}
	}
}
