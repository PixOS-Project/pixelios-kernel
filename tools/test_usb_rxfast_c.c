/*
 * Host test for the sweet_usb.c half of patch 0172 (USB root fast receive): tools/test_usb_rxfast_c.py extracts the added
 * code (ud_rx_fast_wait, sweet_usb_rx_hurry, sweet_usb_rx_fast_enable, and the ud_thread sleep tail) from the patch into
 * rxfast.inc, stubs the scheduler and the fake clock below, and builds with -fsanitize=address,undefined (review
 * usbroot-rxfast-r1 F3, cases a-e). Every scheduler call is recorded in order, so the asserts are exact: deadline
 * values, interruptible mode, urgency, leeway, block vs clear_wait. Prints "RESULT: PASS n checks" / "RESULT: FAIL ..."; exit 0/1.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

/* values from osfmk/kern/kern_types.h:87,88,95,179,188 and osfmk/mach/clock_types.h (NSEC_PER_*) */
#define THREAD_WAITING -1
#define THREAD_AWAKENED 0
#define THREAD_UNINT 0
#define THREAD_ABORTSAFE 2
#define THREAD_CONTINUE_NULL NULL
#define TIMEOUT_URGENCY_SYS_CRITICAL 0x01
#define TIMEOUT_URGENCY_USER_NORMAL 0x00
#define NSEC_PER_USEC 1000ull
#define NSEC_PER_MSEC 1000000ull

static int checks, fails;
#define CHECK(c) do { checks++; if (!(c)) { fails++; fprintf(stdout, "FAIL line %d: %s\n", __LINE__, #c); } } while (0)

enum { E_WAIT_DL, E_WAIT_LEEWAY, E_BLOCK, E_CLEAR, E_WAKE, E_SLEEP_MS };
struct ev { int kind; void *chan; int mode; int urgency; uint64_t deadline, leeway; int arg; };
static struct ev evs[64];
static int nev;
static uint64_t stub_now = 5000000;     /* fake clock (ns) */
static int stub_race;                   /* assert_wait_deadline: a hurry(true) lands right after the assert (case b) */
static char cap[1 << 16];
static size_t cap_n;
static int
cap_printf(const char *f, ...)
{
	va_list ap;
	va_start(ap, f);
	int n = vsnprintf(cap + cap_n, sizeof(cap) - cap_n, f, ap);
	va_end(ap);
	if (n > 0 && cap_n + (size_t)n < sizeof(cap)) {
		cap_n += (size_t)n;
	}
	return n;
}
#define printf cap_printf

static struct { uint64_t rx_frames, tx_frames; } ud;
static int fake_thread;
static void *current_thread(void) { return &fake_thread; }
static void clock_interval_to_deadline(unsigned a, uint64_t scale, uint64_t *d) { *d = stub_now + a * scale; }
static void ev_add(struct ev e) { if (nev < 64) { evs[nev++] = e; } }
static void ud_sleep_ms(unsigned ms) { struct ev e = { .kind = E_SLEEP_MS, .arg = (int)ms }; ev_add(e); }
static void thread_wakeup(void *c) { struct ev e = { .kind = E_WAKE, .chan = c }; ev_add(e); }
static int hurry_after_assert(void);
static void ud_thread_tail_model(unsigned iterations);
static int
assert_wait_deadline(void *c, int m, uint64_t d)
{
	struct ev e = { .kind = E_WAIT_DL, .chan = c, .mode = m, .deadline = d };
	ev_add(e);
	(void)hurry_after_assert();
	return THREAD_WAITING;
}
static int
assert_wait_deadline_with_leeway(void *c, int m, int urgency, uint64_t d, uint64_t leeway)
{
	struct ev e = { .kind = E_WAIT_LEEWAY, .chan = c, .mode = m, .urgency = urgency, .deadline = d, .leeway = leeway };
	ev_add(e);
	return THREAD_WAITING;
}
static int thread_block(void *c) { (void)c; struct ev e = { .kind = E_BLOCK }; ev_add(e); return 0; }
static int clear_wait(void *t, int r) { (void)t; struct ev e = { .kind = E_CLEAR, .arg = r }; ev_add(e); return 0; }

#include "rxfast.inc"                   /* the real added code of 0172's sweet_usb.c half */

static int
hurry_after_assert(void)
{
	if (stub_race) {
		stub_race = 0;
		sweet_usb_rx_hurry(true);       /* a usbroot request starts between the check and the assert */
		nev--;                          /* (its wake-up is not a scheduler event of the case under test) */
		return 1;
	}
	return 0;
}

static void
clear(void)
{
	nev = 0; ud_rx_fast = false; ud_rx_hurry = false; ud_rx_short_polls = 0; stub_race = 0; cap_n = 0; cap[0] = 0;
	memset(evs, 0, sizeof(evs));
}

int
main(void)
{
	/* (a) fast, idle: one 1 ms deadline wait on ud_rx_chan, then block */
	clear(); ud_rx_fast = true;
	ud_rx_fast_wait();
	CHECK(nev == 2 && evs[0].kind == E_WAIT_DL && evs[1].kind == E_BLOCK);
	CHECK(evs[0].chan == &ud_rx_chan && evs[0].mode == THREAD_UNINT && evs[0].deadline == stub_now + 1 * NSEC_PER_MSEC);
	CHECK(ud_rx_short_polls == 0);

	/* (b) a request starts between the check and the assert: clear_wait(THREAD_AWAKENED), no block */
	clear(); ud_rx_fast = true; stub_race = 1;
	ud_rx_fast_wait();
	CHECK(nev == 2 && evs[0].kind == E_WAIT_DL && evs[1].kind == E_CLEAR && evs[1].arg == THREAD_AWAKENED);
	CHECK(evs[0].deadline == stub_now + 1 * NSEC_PER_MSEC && ud_rx_hurry && ud_rx_short_polls == 0);

	/* (c) hurry: SYS_CRITICAL, leeway 0, deadline now + 100 us, uninterruptible, block, short_polls++ */
	clear(); ud_rx_fast = true; ud_rx_hurry = true;
	ud_rx_fast_wait();
	CHECK(nev == 2 && evs[0].kind == E_WAIT_LEEWAY && evs[1].kind == E_BLOCK);
	CHECK(evs[0].chan == &ud_rx_chan && evs[0].mode == THREAD_UNINT && evs[0].urgency == TIMEOUT_URGENCY_SYS_CRITICAL);
	CHECK(evs[0].leeway == 0 && evs[0].deadline == stub_now + 100 * NSEC_PER_USEC && UD_RX_HURRY_US == 100);
	CHECK(ud_rx_short_polls == 1);
	ud_rx_fast_wait();
	CHECK(ud_rx_short_polls == 2);

	/* (d) hurry(true) wakes the event thread once on ud_rx_chan; hurry(false) does not wake and the next wait is 1 ms */
	clear(); ud_rx_fast = true;
	sweet_usb_rx_hurry(true);
	CHECK(ud_rx_hurry && nev == 1 && evs[0].kind == E_WAKE && evs[0].chan == &ud_rx_chan);
	sweet_usb_rx_hurry(false);
	CHECK(!ud_rx_hurry && nev == 1);
	ud_rx_fast_wait();
	CHECK(nev == 3 && evs[1].kind == E_WAIT_DL && evs[1].deadline == stub_now + NSEC_PER_MSEC && evs[2].kind == E_BLOCK);
	CHECK(ud_rx_short_polls == 0);

	/* hurry(true) with fast off: flag set but nobody woken */
	clear();
	sweet_usb_rx_hurry(true);
	CHECK(ud_rx_hurry && nev == 0);

	/* fast_enable is loud and switches the mode on */
	clear();
	sweet_usb_rx_fast_enable();
	CHECK(ud_rx_fast && strstr(cap, "SWEET USB: usbroot fast receive on: event thread polls every 100 us while a reply is due (patch 0172)\n") != NULL);
	CHECK(sweet_usb_rx_short_polls() == 0);

	/* the ud_thread sleep tail, one pass per case */
	/* (e) fast off: exactly ud_sleep_ms(1), no scheduler call of the new path, even with hurry set */
	clear(); ud_rx_hurry = true;
	ud_thread_tail_model(1);
	CHECK(nev == 1 && evs[0].kind == E_SLEEP_MS && evs[0].arg == 1);
	clear();
	ud_thread_tail_model(1);
	CHECK(nev == 1 && evs[0].kind == E_SLEEP_MS && evs[0].arg == 1);
	/* fast on, idle: ud_rx_fast_wait 1 ms, never ud_sleep_ms */
	clear(); ud_rx_fast = true;
	ud_thread_tail_model(1);
	CHECK(nev == 2 && evs[0].kind == E_WAIT_DL && evs[1].kind == E_BLOCK && ud_rx_short_polls == 0);
	/* fast on, hurry: short poll, never ud_sleep_ms */
	clear(); ud_rx_fast = true; ud_rx_hurry = true;
	ud_thread_tail_model(1);
	CHECK(nev == 2 && evs[0].kind == E_WAIT_LEEWAY && evs[1].kind == E_BLOCK && ud_rx_short_polls == 1);
	/* the 30 s counter line counts 1 ms ticks only: 30000 short polls print nothing, 30000 idle passes print one line */
	clear(); ud_rx_fast = true; ud_rx_hurry = true;
	ud_thread_tail_model(30000);
	CHECK(strstr(cap, "ue0 rx") == NULL && ud_rx_short_polls == 30000);
	clear(); ud_rx_fast = true;
	nev = 0; ud_thread_tail_model(30000);
	CHECK(strstr(cap, "SWEET USB: ue0 rx 0 tx 0 frames\n") != NULL);

	if (fails) {
		fprintf(stdout, "RESULT: FAIL %d of %d checks\n", fails, checks);
		return 1;
	}
	fprintf(stdout, "RESULT: PASS %d checks\n", checks);
	return 0;
}
