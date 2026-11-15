/*
 * usbgadget_host_test.c - host tests for the stage-2 gadget driver
 * against a fake DWC3: a register file keyed on the exact offsets the
 * driver may touch (0xC100..0xCFFF of 0x11110000), a DEPCMD engine
 * whose CMDACT clears itself, event-buffer injection through the pool
 * memory plus a GEVNTCOUNT bump, and a log of EVERY register write.
 * The fake time source advances 1 ms per ticks() call at 1 MHz, so
 * every wait is deterministic without real sleeping.
 *
 * Build (shim/build.sh, next to usbproto_host_test):
 *   gcc -O1 -Wall -Wextra -I. -o out/usbgadget_host_test \
 *       usbproto.c usbgadget.c usbgadget_host_test.c
 */
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "usbproto.h"
#include "usbgadget.h"

#define FAKE_BASE	0x11110000UL	/* usbprobe.c */
#define FAKE_LO		0xC100u
#define FAKE_HI		0xCFFFu
#define FAKE_NWORDS	((FAKE_HI + 1u - FAKE_LO) / 4u)

/* Register offsets used by the tests (all 0071-patch positions). */
#define R_GCTL		0xC110u
#define R_GEVNTADRLO	0xC400u
#define R_GEVNTADRHI	0xC404u
#define R_GEVNTSIZ	0xC408u
#define R_GEVNTCOUNT	0xC40Cu
#define R_DCFG		0xC700u
#define R_DCTL		0xC704u
#define R_DEVTEN	0xC708u
#define R_DSTS		0xC70Cu
#define R_DALEPENA	0xC720u
#define R_DEPCMD(ep)	(0xC80Cu + 0x10u * (ep))
#define R_PAR0(ep)	(0xC808u + 0x10u * (ep))
#define R_PAR1(ep)	(0xC804u + 0x10u * (ep))
#define R_PAR2(ep)	(0xC800u + 0x10u * (ep))

#ifdef USB_GADGET_SS
#define DCFG_BASE_SPD	4u	/* DEVSPD kept as the gadget wrote it (SuperSpeed) */
#else
#define DCFG_BASE_SPD	0u
#endif
#define DCTL_CSFTRST	(1u << 30)
#define DCTL_RUN_STOP	(1u << 31)
#define DEPCMD_CMDACT	(1u << 10)
#define CTL_SETUP	0xC23u	/* TRBCTL_SETUP | HWO | LST | ISP_IMI | IOC */
#define CTL_DATA	0xC53u	/* TRBCTL_DATA  | HWO | LST | ISP_IMI | IOC */
#define CTL_NORMAL	0xC13u	/* TRBCTL_NORMAL (bulk) | HWO | LST | ISP_IMI | IOC */
#define CTL_STATUS2	0xC33u
#define CTL_STATUS3	0xC43u

#define EVT_DEV(t)	(1u | ((t) << 8))
#define EVT_EP(ep, type, status) \
	(((status) << 12) | ((type) << 6) | ((ep) << 1))

/* ---- fake console ---- */

static char con_log[16384];
static unsigned con_len;

void con_str(const char *label, const char *s)
{
	assert(con_len + strlen(label) + strlen(s) + 2 < sizeof(con_log));
	con_len += (unsigned)snprintf(con_log + con_len,
				      sizeof(con_log) - con_len,
				      "%s %s\n", label, s);
}

void con_hex(const char *label, uint64_t v)
{
	(void)label;
	(void)v;
}

static void con_reset(void)
{
	con_len = 0;
	con_log[0] = 0;
}

static void assert_line(const char *expected)
{
	char want[160];
	snprintf(want, sizeof(want), "%s\n", expected);
	const char *hit = strstr(con_log, want);
	if (!hit || (hit != con_log && hit[-1] != '\n'))
		fprintf(stderr, "MISSING: %s\nLOG:\n%s", expected, con_log);
	assert(hit != NULL);
	assert(hit == con_log || hit[-1] == '\n');
}

static unsigned count_lines(const char *expected)
{
	const char *p = con_log;
	size_t len = strlen(expected);
	unsigned n = 0;

	while ((p = strstr(p, expected)) != NULL) {
		if ((p == con_log || p[-1] == '\n') && p[len] == '\n')
			n++;
		p += len;
	}
	return n;
}

/* ---- fake DWC3 ---- */

static uint32_t regs[FAKE_NWORDS];
struct wrec {
	uintptr_t addr;
	uint32_t val;
};
static struct wrec wlog[512];
static unsigned n_writes;
static unsigned n_access;
static unsigned fail_access_at;		/* fail this access and later; 0 = never */
static unsigned outside_writes;
static int stuck_csftrst;
static int hang_depcmd;
static int fake_active[4];	/* a transfer is started on this phys ep */
static unsigned n_endtransfer, n_no_resource;

static unsigned reg_idx(uintptr_t addr)
{
	assert(addr >= FAKE_BASE + FAKE_LO && addr <= FAKE_BASE + FAKE_HI);
	return (unsigned)((addr - FAKE_BASE - FAKE_LO) / 4u);
}

static uint32_t reg_get(uint32_t off)
{
	return regs[reg_idx(FAKE_BASE + off)];
}

/* Most recent value WRITTEN to a register: DEPCMD reads back with
 * CMDACT self-cleared, so assertions on issued commands must look at
 * the log of what the driver wrote. */
static uint32_t last_write_val(uint32_t off)
{
	uintptr_t want = FAKE_BASE + off;
	uint32_t v = 0;
	unsigned i;

	for (i = 0; i < n_writes; i++)
		if (wlog[i].addr == want)
			v = wlog[i].val;
	return v;
}

static void reg_seed(uint32_t off, uint32_t v)
{
	regs[reg_idx(FAKE_BASE + off)] = v;
}

int try_read32(uintptr_t address, uint32_t *value)
{
	n_access++;
	if (fail_access_at && n_access >= fail_access_at)
		return 0;
	if (address < FAKE_BASE + FAKE_LO || address > FAKE_BASE + FAKE_HI ||
	    (address & 3u))
		return 0;
	*value = regs[reg_idx(address)];
	return 1;
}

int try_write32(uintptr_t address, uint32_t value)
{
	n_access++;
	if (fail_access_at && n_access >= fail_access_at)
		return 0;
	if (n_writes < sizeof(wlog) / sizeof(wlog[0]))
		wlog[n_writes++] = (struct wrec){ address, value };
	if (address < FAKE_BASE + FAKE_LO || address > FAKE_BASE + FAKE_HI ||
	    (address & 3u)) {
		outside_writes++;
		return 1;
	}
	if (address == FAKE_BASE + R_DCTL && (value & DCTL_CSFTRST) &&
	    !stuck_csftrst)
		value &= ~DCTL_CSFTRST;		/* hardware self-clears */
	if ((address & 0xFu) == 0xCu && address >= FAKE_BASE + 0xC800u &&
	    (value & DEPCMD_CMDACT) && !hang_depcmd) {
		unsigned ep = (unsigned)((address - FAKE_BASE - 0xC800u) / 0x10u);
		uint32_t cmd = value & 0xffu;

		value &= ~DEPCMD_CMDACT;	/* command completes at once */
		if (cmd == 6u) {		/* STARTTRANSFER */
			if (fake_active[ep]) {
				value |= 1u << 12;	/* status 1: No Resource */
				n_no_resource++;
			} else {
				fake_active[ep] = 1;
				value = (value & ~(0x7fu << 16)) | ((ep + 1u) << 16);
			}
		} else if (cmd == 8u) {		/* ENDTRANSFER (ForceRM) */
			if (!(value & (1u << 11)) ||
			    ((value >> 16) & 0x7fu) != ep + 1u)
				value |= 1u << 12;	/* wrong resource index */
			else
				fake_active[ep] = 0;
			n_endtransfer++;
		}
	}
	if (address == FAKE_BASE + R_GEVNTCOUNT) {
		/* acks decrement the pending count (databook) */
		unsigned gi = reg_idx(address);
		assert(value <= regs[gi]);
		regs[gi] -= value;
		return 1;
	}
	regs[reg_idx(address)] = value;
	return 1;
}

/* Fake time: 1 ms per ticks() call at 1 MHz. */
static uint64_t fake_t;

static uint64_t fake_time(void)
{
	fake_t += 1000;
	return fake_t;
}

static const struct usbgadget_hooks hooks = { fake_time, 1000000 };

static _Alignas(0x1000) uint8_t pool[0x8000];

static unsigned n_injected;
static void inject_event(uint32_t ev)
{
	uint32_t pos = (n_injected * 4u) & (USBG_UD_EVT_SIZE - 1u);

	memcpy(pool + USBG_UD_EVT + pos, &ev, 4);
	if (!(ev & 1u) && ((ev >> 6) & 0xfu) == 1u && ((ev >> 1) & 0x1fu) < 4u)
		fake_active[(ev >> 1) & 0x1fu] = 0;	/* XferComplete frees it */
	n_injected++;
	reg_seed(R_GEVNTCOUNT, reg_get(R_GEVNTCOUNT) + 4);
}

static void inject_setup_complete(const uint8_t setup[8])
{
	memcpy(pool + USBG_UD_SETUP, setup, 8);
	inject_event(EVT_EP(0, 1, 0));	/* XferComplete on ep0out */
}

struct trb {
	uint32_t lo, hi, len, ctl;
};

static struct trb trb_at(uint32_t off)
{
	struct trb t;

	memcpy(&t, pool + off, sizeof(t));
	return t;
}

static void poll_all(void)
{
	unsigned i;

	for (i = 0; i < 40; i++)
		usbgadget_poll();
}

static void fake_reset(void)
{
	memset(regs, 0, sizeof(regs));
	memset(pool, 0, sizeof(pool));
	memset(wlog, 0, sizeof(wlog));
	n_writes = 0;
	n_access = 0;
	fail_access_at = 0;
	outside_writes = 0;
	stuck_csftrst = 0;
	hang_depcmd = 0;
	memset(fake_active, 0, sizeof(fake_active));
	n_endtransfer = n_no_resource = 0;
	n_injected = 0;
	fake_t = 0;
	con_reset();
	reg_seed(R_DCFG, 0x00000088u);	/* ABL-handoff style: addr bits, HS */
	reg_seed(R_GCTL, 2u << 12);	/* PRTCAP = device (Gate 0: prtcap=2) */
}

/* The exact register set the driver may write (usbgadget.c w32_allowed):
 * nothing in the 0xC100 window beyond it, in particular never GCTL
 * (0xC110), GUSB2PHYCFG (0xC200), GUSB3PIPECTL (0xC2C0), FIFO sizing or
 * the OTG block. */
static int exact_allowed(uintptr_t off)
{
	switch (off) {
	case R_GEVNTADRLO: case R_GEVNTADRHI: case R_GEVNTSIZ:
	case R_GEVNTCOUNT: case R_DCFG: case R_DCTL: case R_DEVTEN:
	case R_DALEPENA:
		return 1;
	}
	return off >= 0xC800u && off < 0xC840u && !(off & 3u);
}

static void assert_whitelist(void)
{
	unsigned i;

	for (i = 0; i < n_writes; i++) {
		uintptr_t off = wlog[i].addr - FAKE_BASE;
		if (!exact_allowed(off)) {
			fprintf(stderr, "write %u outside the exact set: %p=0x%x\n",
				i, (void *)wlog[i].addr, wlog[i].val);
			assert(0);
		}
	}
	assert(outside_writes == 0);
	assert(n_no_resource == 0);	/* never a STARTTRANSFER on an active ep */
}

/* ---- (e) bring-up writes exactly the expected sequence ---- */

struct expw {
	uint32_t off;
	uint32_t val;
};

static void expect_writes(const struct expw *e, unsigned n)
{
	unsigned i;

	if (n_writes != n) {
		fprintf(stderr, "expected %u writes, saw %u\n", n, n_writes);
		assert(0);
	}
	for (i = 0; i < n; i++) {
		if (wlog[i].addr != FAKE_BASE + e[i].off ||
		    wlog[i].val != e[i].val) {
			fprintf(stderr,
				"write %u: got %p=0x%x, want 0x%x=0x%x\n",
				i, (void *)wlog[i].addr, wlog[i].val,
				e[i].off, e[i].val);
			assert(0);
		}
	}
}

/* The last n recorded writes must equal e[] exactly. */
static void expect_tail(const struct expw *e, unsigned n)
{
	unsigned i;

	assert(n_writes >= n);
	for (i = 0; i < n; i++) {
		struct wrec *w = &wlog[n_writes - n + i];

		if (w->addr != FAKE_BASE + e[i].off || w->val != e[i].val) {
			fprintf(stderr,
				"tail write %u: got %p=0x%x, want 0x%x=0x%x\n",
				i, (void *)w->addr, w->val, e[i].off, e[i].val);
			assert(0);
		}
	}
}

static void test_bringup_sequence(void)
{
	struct expw e[40];
	unsigned n = 0;
	uint64_t evt_pa = (uint64_t)(uintptr_t)pool + USBG_UD_EVT;
	uint64_t trb_pa = (uint64_t)(uintptr_t)pool + USBG_UD_TRB_OUT;

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);

	e[n++] = (struct expw){ R_DCTL, 0x40000000u };		/* CSFTRST */
#ifdef USB_GADGET_SS
	e[n++] = (struct expw){ R_DCFG, 0x00000004u };		/* DEVSPD SuperSpeed, addr 0 */
#else
	e[n++] = (struct expw){ R_DCFG, 0x00000000u };		/* HS, addr 0 */
#endif
	e[n++] = (struct expw){ R_GEVNTADRLO, (uint32_t)evt_pa };
	e[n++] = (struct expw){ R_GEVNTADRHI, (uint32_t)(evt_pa >> 32) };
	e[n++] = (struct expw){ R_GEVNTSIZ, 0x80001000u };
	e[n++] = (struct expw){ R_GEVNTCOUNT, 0 };
	e[n++] = (struct expw){ R_DEVTEN, 0x7u };
	/* DEPSTARTCFG ep0 */
	e[n++] = (struct expw){ R_PAR0(0), 0 };
	e[n++] = (struct expw){ R_PAR1(0), 0 };
	e[n++] = (struct expw){ R_PAR2(0), 0 };
	e[n++] = (struct expw){ R_DEPCMD(0), 0x409u };
	/* DEPCFG ep0out, mps 512 */
	e[n++] = (struct expw){ R_PAR0(0), 512u << 3 };
	e[n++] = (struct expw){ R_PAR1(0), 0x500u };
	e[n++] = (struct expw){ R_PAR2(0), 0 };
	e[n++] = (struct expw){ R_DEPCMD(0), 0x401u };
	/* transfer resource ep0out */
	e[n++] = (struct expw){ R_PAR0(0), 1 };
	e[n++] = (struct expw){ R_PAR1(0), 0 };
	e[n++] = (struct expw){ R_PAR2(0), 0 };
	e[n++] = (struct expw){ R_DEPCMD(0), 0x402u };
	/* DEPCFG ep0in */
	e[n++] = (struct expw){ R_PAR0(1), 512u << 3 };
	e[n++] = (struct expw){ R_PAR1(1), 0x500u | (1u << 25) };
	e[n++] = (struct expw){ R_PAR2(1), 0 };
	e[n++] = (struct expw){ R_DEPCMD(1), 0x401u };
	/* transfer resource ep0in */
	e[n++] = (struct expw){ R_PAR0(1), 1 };
	e[n++] = (struct expw){ R_PAR1(1), 0 };
	e[n++] = (struct expw){ R_PAR2(1), 0 };
	e[n++] = (struct expw){ R_DEPCMD(1), 0x402u };
	/* STARTTRANSFER: primed setup TRB */
	e[n++] = (struct expw){ R_PAR0(0), (uint32_t)(trb_pa >> 32) };
	e[n++] = (struct expw){ R_PAR1(0), (uint32_t)trb_pa };
	e[n++] = (struct expw){ R_PAR2(0), 0 };
	e[n++] = (struct expw){ R_DEPCMD(0), 0x406u };
	e[n++] = (struct expw){ R_DALEPENA, 0x3u };
	e[n++] = (struct expw){ R_DCTL, 0x80000000u };		/* RUN_STOP */
	expect_writes(e, n);

	assert_line("USB gadget: running");
#ifdef USB_GADGET_HS_ONLY
	assert_line("USB2 GAP: gadget built HS-only (--usb2)");
	assert(last_write_val(R_DCFG) == 0);
#else
	assert(last_write_val(R_DCFG) == 4);
#endif
	/* setup TRB primed into pool memory */
	struct trb t = trb_at(USBG_UD_TRB_OUT);
	assert(t.lo == (uint32_t)((uintptr_t)pool + USBG_UD_SETUP));
	assert(t.len == 8);	/* 0071 patch: setup TRB transfers 8 bytes */
	assert(t.ctl == CTL_SETUP);
	assert(usbgadget_ready() == 0);
	assert_whitelist();
}

/* ---- (e) a fault on the 3rd access aborts, no further writes ---- */

static void test_fault_third_access(void)
{
	fake_reset();
	fail_access_at = 3;
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) != 0);
	assert_line("USB gadget fault");
	/* access 1 = DCTL write, 2 = DCTL read (cleared), 3 = DCFG read: */
	assert(n_access == 3);
#ifdef USB_GADGET_SS
	/* the read-only SS report (GUSB2PHYCFG, GUSB3PIPECTL) runs before the
	 * soft reset, so the 3rd access fails before any write */
	assert(n_writes == 0);
#else
	assert(n_writes == 1);
#endif
	/* sticky: poll and write never touch hardware again */
	usbgadget_poll();
	assert(usbgadget_write("x", 1) == 0);
	assert(n_access == 3);
#ifdef USB_GADGET_SS
	assert(n_writes == 0);
#else
	assert(n_writes == 1);
#endif
}

/* ---- (e) soft-reset timeout returns error, bounded ---- */

static void test_soft_reset_timeout(void)
{
	fake_reset();
	stuck_csftrst = 1;
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) != 0);
	assert_line("USB gadget: soft reset stuck");
	assert(strstr(con_log, "gadget fault") == NULL);	/* no access failed */
	assert(n_writes == 1);
	assert_whitelist();
}

/* ---- (e) enumeration: descriptors, SET_ADDRESS timing, ep1 enable ---- */

static void status_in_roundtrip(void)
{
	/* status stage for a no-data / data-OUT request: IN on ep0in */
	inject_event(EVT_EP(1, 3, 2));	/* XferNotReady, ctrl status */
	poll_all();
	inject_event(EVT_EP(1, 1, 0));	/* XferComplete */
	poll_all();
}

static void status_out_roundtrip(void)
{
	/* status stage after a data-IN transfer: OUT on ep0out */
	inject_event(EVT_EP(0, 3, 2));
	poll_all();
	inject_event(EVT_EP(0, 1, 0));
	poll_all();
}

static void test_enumeration(void)
{
	static const uint8_t get_dev[8] =
		{ 0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00 };
	static const uint8_t set_addr[8] =
		{ 0x00, 0x05, 0x2A, 0x00, 0x00, 0x00, 0x00, 0x00 };
	static const uint8_t set_cfg[8] =
		{ 0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	static const uint8_t set_dtr[8] =
		{ 0x21, 0x22, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	uint32_t dcfg;

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);

	/* bus reset in the setup phase: address dropped, the still-armed
	 * setup TRB is NOT re-armed (a second STARTTRANSFER on the active
	 * endpoint would fail with No Resource), printed once */
	{
		unsigned starts = 0, i;

		for (i = 0; i < n_writes; i++)
			if (wlog[i].addr == FAKE_BASE + R_DEPCMD(0) &&
			    wlog[i].val == 0x406u)
				starts++;
		inject_event(EVT_DEV(1));
		poll_all();
		assert_line("USB EVT reset");
		assert((reg_get(R_DCFG) & 0x3F8u) == 0);
		assert(trb_at(USBG_UD_TRB_OUT).ctl == CTL_SETUP);
		inject_event(EVT_DEV(1));
		poll_all();
		assert(count_lines("USB EVT reset") == 1);
		for (i = 0; i < n_writes; i++)
			if (wlog[i].addr == FAKE_BASE + R_DEPCMD(0) &&
			    wlog[i].val == 0x406u)
				starts--;
		assert(starts == 0);
		assert(strstr(con_log, "gadget fault") == NULL);
		assert(n_endtransfer == 0);
	}

	/* connect done: DSTS speed printed, ep0 reconfigured mps 64 */
	reg_seed(R_DSTS, 0x00000000u);		/* speed 0 = high speed */
	inject_event(EVT_DEV(2));
	poll_all();
	assert_line("USB EVT conndone speed=0");
	assert(last_write_val(R_DEPCMD(0)) == 0x401u);
	assert(reg_get(R_PAR0(0)) == ((64u << 3) | (2u << 30)));
	assert(last_write_val(R_DEPCMD(1)) == 0x401u);
	assert(reg_get(R_PAR0(1)) == ((64u << 3) | (2u << 30)));

	/* GET_DESCRIPTOR(device): IN TRB -> data buffer, right length */
	inject_setup_complete(get_dev);
	poll_all();
	struct trb tin = trb_at(USBG_UD_TRB_IN);
	assert(tin.lo == (uint32_t)((uintptr_t)pool + USBG_UD_DATA));
	assert(tin.len == 18);
	assert(tin.ctl == CTL_DATA);
	assert(memcmp(pool + USBG_UD_DATA, usbproto_device_desc, 18) == 0);
	dcfg = reg_get(R_DCFG);
	inject_event(EVT_EP(1, 1, 0));		/* data IN complete */
	poll_all();
	/* WAIT_STATUS3: the status TRB must not appear before XferNotReady */
	assert(trb_at(USBG_UD_TRB_IN).ctl == CTL_DATA);
	assert(trb_at(USBG_UD_TRB_OUT).ctl == CTL_SETUP);
	assert(reg_get(R_DCFG) == dcfg);
	status_out_roundtrip();
	assert(trb_at(USBG_UD_TRB_OUT).ctl == CTL_SETUP);
	assert(reg_get(R_DCFG) == dcfg);

	/* SET_ADDRESS: DCFG written in the setup phase (Linux ep0.c:624-627,
	 * 0071 patch); the core applies it after the status stage itself */
	inject_setup_complete(set_addr);
	poll_all();
	assert(reg_get(R_DCFG) == ((0x2A << 3) | DCFG_BASE_SPD));	/* immediately */
	inject_event(EVT_EP(1, 3, 2));
	poll_all();
	assert(reg_get(R_DCFG) == ((0x2A << 3) | DCFG_BASE_SPD));
	assert(trb_at(USBG_UD_TRB_IN).ctl == CTL_STATUS2);
	assert(trb_at(USBG_UD_TRB_IN).len == 0);
	inject_event(EVT_EP(1, 1, 0));
	poll_all();
	assert(reg_get(R_DCFG) == ((0x2A << 3) | DCFG_BASE_SPD));
	assert(trb_at(USBG_UD_TRB_OUT).ctl == CTL_SETUP);
	assert(usbgadget_ready() == 0);

	/* SET_CONFIGURATION(1): ep1 bulk enabled via DEP block 3 */
	inject_setup_complete(set_cfg);
	poll_all();
	{
		/* The full status-completion write burst: DEPCFG ep1, transfer
		 * resource, DALEPENA, the re-armed setup TRB, then the poll's
		 * GEVNTCOUNT ack for the consumed event. */
		uint64_t trb_pa = (uint64_t)(uintptr_t)pool + USBG_UD_TRB_OUT;
		struct expw en[18];
		unsigned k = 0;

		/* DEPSTARTCFG idx 2 through ep0 (Linux ep0.c:663), not ep 3 */
		en[k++] = (struct expw){ R_PAR0(0), 0 };
		en[k++] = (struct expw){ R_PAR1(0), 0 };
		en[k++] = (struct expw){ R_PAR2(0), 0 };
		en[k++] = (struct expw){ R_DEPCMD(0), 0x20409u };
		/* bulk (2<<1) | fifo 1 (1<<17) | mps 512; physical ep 3 */
		en[k++] = (struct expw){ R_PAR0(3), (2u << 1) | (1u << 17) | (512u << 3) };
		en[k++] = (struct expw){ R_PAR1(3), 0x500u | (3u << 25) };
		en[k++] = (struct expw){ R_PAR2(3), 0 };
		en[k++] = (struct expw){ R_DEPCMD(3), 0x401u };
		en[k++] = (struct expw){ R_PAR0(3), 1 };
		en[k++] = (struct expw){ R_PAR1(3), 0 };
		en[k++] = (struct expw){ R_PAR2(3), 0 };
		en[k++] = (struct expw){ R_DEPCMD(3), 0x402u };
		en[k++] = (struct expw){ R_DALEPENA, 0x3u | (1u << 3) };
		en[k++] = (struct expw){ R_PAR0(0), (uint32_t)(trb_pa >> 32) };
		en[k++] = (struct expw){ R_PAR1(0), (uint32_t)trb_pa };
		en[k++] = (struct expw){ R_PAR2(0), 0 };
		en[k++] = (struct expw){ R_DEPCMD(0), 0x406u };
		en[k++] = (struct expw){ R_GEVNTCOUNT, 4 };
		status_in_roundtrip();
		expect_tail(en, k);
	}
	assert(usbgadget_ready() == 0);		/* DTR still clear */

	/* SET_CONTROL_LINE_STATE (DTR): streaming becomes ready */
	inject_setup_complete(set_dtr);
	poll_all();
	status_in_roundtrip();
	assert(usbgadget_ready() == 1);

	/* SET_LINE_CODING: 7-byte data OUT captured into line coding */
	{
		static const uint8_t slc[8] =
			{ 0x21, 0x20, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00 };
		static const uint8_t glc[8] =
			{ 0xA1, 0x21, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00 };
		static const uint8_t lc[USBPROTO_LINE_CODING_LEN] =
			{ 0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08 };
		inject_setup_complete(slc);
		poll_all();
		struct trb tout = trb_at(USBG_UD_TRB_OUT);
		assert(tout.len == 64);		/* MPS-aligned (Linux ep0.c:1009) */
		assert(tout.ctl == CTL_DATA);
		memcpy(pool + USBG_UD_DATA, lc, sizeof(lc));
		inject_event(EVT_EP(0, 1, 0));	/* data OUT complete */
		poll_all();
		status_in_roundtrip();
		inject_setup_complete(glc);	/* GET_LINE_CODING echoes it */
		poll_all();
		assert(memcmp(pool + USBG_UD_DATA, lc, sizeof(lc)) == 0);
		inject_event(EVT_EP(1, 1, 0));
		poll_all();
		status_out_roundtrip();
	}
	assert_whitelist();
}

/* ---- (e) bulk write after DTR: TRB bytes + STARTTRANSFER ---- */

static void test_bulk_write(void)
{
	char big[4096];

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	unsigned writes = n_writes;
	assert(usbgadget_write("x", 1) == 0);	/* unconfigured */
	assert(n_writes == writes);

	uint8_t set_cfg[8] = { 0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	uint8_t set_dtr[8] = { 0x21, 0x22, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	inject_event(EVT_DEV(2));
	poll_all();
	inject_setup_complete(set_cfg);
	poll_all();
	status_in_roundtrip();
	inject_setup_complete(set_dtr);
	poll_all();
	status_in_roundtrip();
	assert(usbgadget_ready() == 1);

	/* 4096-byte TRB of 0xAB */
	memset(big, 0xAB, sizeof(big));
	assert(usbgadget_write(big, sizeof(big)) == sizeof(big));
	struct trb t = trb_at(USBG_UD_BULK_TRB);
	assert(t.lo == (uint32_t)((uintptr_t)pool + USBG_UD_BULK_DATA));
	assert(t.len == 4096);
	assert(t.ctl == CTL_NORMAL);	/* bulk is TRBCTL_NORMAL, not control data */
	assert(pool[USBG_UD_BULK_DATA] == 0xAB);
	assert(pool[USBG_UD_BULK_DATA + 4095] == 0xAB);
	assert(last_write_val(R_DEPCMD(3)) == 0x406u);	/* STARTTRANSFER */
	{
		uint64_t trb_pa = (uint64_t)(uintptr_t)pool + USBG_UD_BULK_TRB;

		assert(last_write_val(R_PAR0(3)) == (uint32_t)(trb_pa >> 32));
		assert(last_write_val(R_PAR1(3)) == (uint32_t)trb_pa);
	}
	/* busy: refused while in flight */
	assert(usbgadget_write("!", 1) == 0);
	/* host drains; next write takes ring slot 1 */
	inject_event(EVT_EP(3, 1, 0));
	poll_all();
	assert(usbgadget_write("ok", 2) == 2);
	t = trb_at(USBG_UD_BULK_TRB + 16);
	assert(t.len == 2);
	assert(memcmp(pool + USBG_UD_BULK_DATA, "ok", 2) == 0);
	/* oversize request: only 4096 accepted */
	inject_event(EVT_EP(3, 1, 0));
	poll_all();
	char huge[5000];
	memset(huge, 7, sizeof(huge));
	assert(usbgadget_write(huge, sizeof(huge)) == 4096);
	assert(trb_at(USBG_UD_BULK_TRB + 32).len == 4096);
	assert_whitelist();
}

/* ---- (c) poll is bounded to 16 events and acks what it consumed ---- */

static void test_poll_bounded(void)
{
	unsigned i;

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	for (i = 0; i < 20; i++)
		inject_event(EVT_DEV(3));	/* linkstate: log-only */
	assert(reg_get(R_GEVNTCOUNT) == 80);
	usbgadget_poll();
	assert(reg_get(R_GEVNTCOUNT) == 16);	/* acked 64, 16 events left */
	usbgadget_poll();
	assert(reg_get(R_GEVNTCOUNT) == 0);
	assert(count_lines("USB EVT linkstate") == 1);
	assert_whitelist();
}

/* ---- init argument checks: fail closed before touching registers ---- */

static void test_init_arg_checks(void)
{
	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, USBG_POOL_MIN - 1, &hooks) != 0);
	assert(n_writes == 0);
	assert(usbgadget_init((uintptr_t)pool + 0x800, USBG_POOL_MIN, &hooks) !=
	       0);
	assert(n_writes == 0);
	assert(usbgadget_init((uintptr_t)pool, USBG_POOL_MIN, 0) != 0);
	assert(n_writes == 0);
	assert_whitelist();
}

/* ---- DEPCMD never completing: the command wait must time out ---- */

static void test_depcmd_timeout(void)
{
	fake_reset();
	hang_depcmd = 1;
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) != 0);
	assert_line("USB gadget fault");	/* command timeout fails closed */
	assert(strstr(con_log, "epcmd timeout") != NULL);
	/* writes stop at the first DEPCMD (DEPSTARTCFG on ep0out): 7
	 * bring-up writes + PAR0/1/2 + DEPCMD = 11 total. */
	assert(n_writes == 11);
	assert_whitelist();
}

/* ---- review fixes: reset mid-transfer, bulk end, role check, stop ---- */

static void enumerate_and_open(void)
{
	static const uint8_t set_cfg[8] =
		{ 0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	static const uint8_t set_dtr[8] =
		{ 0x21, 0x22, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };

	inject_event(EVT_DEV(2));
	poll_all();
	inject_setup_complete(set_cfg);
	poll_all();
	status_in_roundtrip();
	inject_setup_complete(set_dtr);
	poll_all();
	status_in_roundtrip();
	assert(usbgadget_ready() == 1);
}

/* H1 + M2: a bus reset while a control data stage is in flight ends the
 * transfer (ForceRM, right resource index) and re-arms setup exactly once;
 * a reset with bulk data in flight ends the bulk transfer too. */
static void test_reset_mid_transfer(void)
{
	static const uint8_t get_dev[8] =
		{ 0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00 };
	unsigned i, starts = 0;

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	inject_setup_complete(get_dev);		/* data IN started on ep0in */
	poll_all();
	assert(fake_active[1] == 1);
	inject_event(EVT_DEV(1));		/* reset before it completes */
	poll_all();
	/* ENDTRANSFER|ForceRM, resource index ep+1 = 2 on ep0in */
	assert(last_write_val(R_DEPCMD(1)) == (8u | (1u << 11) | (2u << 16)) + 0 + DEPCMD_CMDACT);
	assert(n_endtransfer == 1 && fake_active[1] == 0);
	assert(fake_active[0] == 1);		/* setup re-armed */
	for (i = 0; i < n_writes; i++)
		if (wlog[i].addr == FAKE_BASE + R_DEPCMD(0) && wlog[i].val == 0x406u)
			starts++;
	assert(starts == 2);			/* init + the one re-arm */
	assert(strstr(con_log, "gadget fault") == NULL);
	assert_whitelist();

	/* bulk in flight at disconnect/reset */
	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	enumerate_and_open();
	assert(usbgadget_write("log", 3) == 3);
	assert(fake_active[3] == 1);
	inject_event(EVT_DEV(0));		/* disconnect */
	poll_all();
	assert(n_endtransfer == 1 && fake_active[3] == 0);
	assert(last_write_val(R_DEPCMD(3)) == (8u | (1u << 11) | (4u << 16)) + DEPCMD_CMDACT);
	assert(usbgadget_ready() == 0);
	assert(strstr(con_log, "gadget fault") == NULL);
	assert_whitelist();
}

/* M4: refuse unless the core is in device role; nothing is written. */
static void test_role_check(void)
{
	fake_reset();
	reg_seed(R_GCTL, 1u << 12);		/* PRTCAP = host */
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) != 0);
	assert(n_writes == 0);
	assert(strstr(con_log, "core not in device mode") != NULL);
	assert_whitelist();
}

/* M5: stop halts the core and makes the gadget inert. */
static void test_stop(void)
{
	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	reg_seed(R_DSTS, 1u << 22);		/* core reports halted */
	usbgadget_stop();
	assert(last_write_val(R_DCTL) == 0u);	/* RUN_STOP cleared */
	assert(usbgadget_ready() == 0);
	unsigned w = n_writes;
	inject_event(EVT_DEV(1));
	usbgadget_poll();			/* inert: no events consumed */
	assert(n_writes == w);
	usbgadget_stop();			/* idempotent */
	assert(n_writes == w);
	assert(usbgadget_faulted() == 0);
	assert_whitelist();
}

/* L4: a zero-length GET_DESCRIPTOR is a two-stage request: no data TRB. */
static void test_zero_length_get(void)
{
	static const uint8_t get_dev0[8] =
		{ 0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00 };

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	inject_setup_complete(get_dev0);
	poll_all();
	assert(fake_active[1] == 0);		/* nothing started on ep0in */
	inject_event(EVT_EP(1, 3, 2));		/* status stage */
	poll_all();
	assert(trb_at(USBG_UD_TRB_IN).ctl == CTL_STATUS2);
	assert(strstr(con_log, "gadget fault") == NULL);
	assert_whitelist();
}

/* L5: non-device-specific event types (carkit/I2C) are ignored. */
static void test_foreign_event(void)
{
	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	inject_event(1u | (3u << 1) | (1u << 8));	/* type 3, would look like reset */
	poll_all();
	assert(strstr(con_log, "EVT reset") == NULL);
}

#ifdef USB_GADGET_SS
/* ---- SuperSpeed (USB_GADGET_SS) ---- */

static unsigned count_substr(const char *needle)
{
	unsigned n = 0;
	const char *p = con_log;

	while ((p = strstr(p, needle)) != NULL) {
		n++;
		p += strlen(needle);
	}
	return n;
}

static void ss_enumerate(uint32_t dsts)
{
	static const uint8_t set_cfg[8] =
		{ 0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };

	reg_seed(R_DSTS, dsts);
	inject_event(EVT_DEV(2));
	poll_all();
	inject_setup_complete(set_cfg);
	poll_all();
	status_in_roundtrip();
}

static void test_ss_bringup_and_speed(void)
{
	static const uint8_t set_sel[8] =
		{ 0x00, 0x30, 0, 0, 0, 0, 6, 0 };
	static const uint8_t isoch[8] =
		{ 0x00, 0x31, 100, 0, 0, 0, 0, 0 };
	static const uint8_t set_u1[8] =
		{ 0x00, 0x03, 48, 0, 0, 0, 0, 0 };
	static const uint8_t get_bos[8] =
		{ 0x80, 0x06, 0x00, 0x0f, 0, 0, 0xff, 0 };
	static const uint8_t get_qual[8] =
		{ 0x80, 0x06, 0x00, 0x06, 0, 0, 0x0a, 0 };
	static const uint8_t set_dtr[8] =
		{ 0x21, 0x22, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	struct trb t;

	/* DEVSPD 4 written, NUMP and all other DCFG bits kept, address 0 */
	fake_reset();
	reg_seed(R_DCFG, 0x00200888u);
	reg_seed(R_DSTS, 0x00000004u);
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	assert(last_write_val(R_DCFG) == 0x00200804u);
	/* read-only reports before and after the soft reset, no PHY writes */
	assert(count_substr("USB3: before soft reset (SS) GUSB2PHYCFG=0x") == 1);
	assert(count_substr("USB3: after soft reset GUSB2PHYCFG=0x") == 1);
	assert_whitelist();

	/* SS ConnectDone: ep0 512, bulk 1024 after SET_CONFIGURATION */
	ss_enumerate(0x00000004u);
	assert_line("USB EVT conndone speed=4");
	assert_line("USB3: conndone speed=4");
	{	/* the ep0 DEPCFG modify writes: find the 512 one */
		unsigned i, hit = 0;

		for (i = 0; i < n_writes; i++)
			if (wlog[i].addr == FAKE_BASE + R_PAR0(0) &&
			    wlog[i].val == ((512u << 3) | (2u << 30)))
				hit++;
		assert(hit == 1);
	}
	{
		unsigned i, hit = 0;

		for (i = 0; i < n_writes; i++)
			if (wlog[i].addr == FAKE_BASE + R_PAR0(3) &&
			    wlog[i].val == ((2u << 1) | (1u << 17) | (1024u << 3)))
				hit++;
		assert(hit == 1);
	}
	assert_line("USB3: bulk max packet 1024");
	assert(count_lines("USB3 GAP: speed contradicts the bulk max packet, not restarted again") == 0);

	/* BOS served at SS (22 bytes), qualifier stalls with a GAP line */
	inject_setup_complete(get_bos);
	poll_all();
	t = trb_at(USBG_UD_TRB_IN);
	assert(t.len == 22);
	assert(pool[USBG_UD_DATA] == 5 && pool[USBG_UD_DATA + 1] == 0x0f &&
	       pool[USBG_UD_DATA + 16] == 0x0f);	/* BOS hdr, SS capability speeds */
	inject_event(EVT_EP(1, 1, 0));	/* data IN complete */
	poll_all();
	status_out_roundtrip();
	con_reset();
	inject_setup_complete(get_qual);
	poll_all();
	assert(strstr(con_log, "USB3 GAP: ep0 request stalled") != NULL);
	/* SET_SEL: 6-byte OUT stage, TRB length = ep0 max packet 512, in the data area */
	con_reset();
	inject_setup_complete(set_sel);
	poll_all();
	t = trb_at(USBG_UD_TRB_OUT);
	assert(t.len == 512 && t.lo == (uint32_t)((uintptr_t)pool + USBG_UD_DATA));
	assert(USBG_UD_DATA + t.len <= USBG_UD_BULK_TRB);
	assert_line("USB3 GAP: SET_SEL acknowledged, not programmed");
	inject_event(EVT_EP(0, 1, 0));	/* OUT data complete */
	poll_all();
	status_in_roundtrip();
	/* line coding untouched by the discarded SET_SEL bytes */
	assert(usbgadget_ready() == 0);
	inject_setup_complete(set_dtr);
	poll_all();
	status_in_roundtrip();
	assert(usbgadget_ready() == 1);
	con_reset();
	inject_setup_complete(isoch);
	poll_all();
	status_in_roundtrip();
	assert_line("USB3: SET_ISOCH_DELAY ns=100");
	con_reset();
	inject_setup_complete(set_u1);
	poll_all();
	assert(strstr(con_log, "USB3 GAP: ep0 request stalled") != NULL);
	assert(strstr(con_log, "gadget fault") == NULL);
	assert(usbgadget_faulted() == 0);
}

static void test_hs_connect_in_ss_build(void)
{
	static const uint8_t get_bos[8] =
		{ 0x80, 0x06, 0x00, 0x0f, 0, 0, 0xff, 0 };
	unsigned i, hit = 0;

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	ss_enumerate(0x00000000u);	/* HS ConnectDone */
	assert_line("USB3: conndone speed=0");
	for (i = 0; i < n_writes; i++) {
		if (wlog[i].addr == FAKE_BASE + R_PAR0(0) &&
		    wlog[i].val == ((64u << 3) | (2u << 30)))
			hit++;
		assert(!(wlog[i].addr == FAKE_BASE + R_PAR0(0) &&
			 wlog[i].val == ((512u << 3) | (2u << 30))));
	}
	assert(hit == 1);
	hit = 0;
	for (i = 0; i < n_writes; i++)
		if (wlog[i].addr == FAKE_BASE + R_PAR0(3) &&
		    wlog[i].val == ((2u << 1) | (1u << 17) | (512u << 3)))
			hit++;
	assert(hit == 1);
	inject_setup_complete(get_bos);	/* BOS stalls at HS */
	poll_all();
	assert(strstr(con_log, "gadget fault") == NULL);
	/* configured at HS: the SS timer must not fire */
	fake_t += 20000u * 1000u;
	poll_all();
	assert(count_substr("restarting at high speed") == 0);
}

static void test_ss_fallback(void)
{
	unsigned i, dcfg_ss = 0, dcfg_hs = 0;

	fake_reset();
	reg_seed(R_DCFG, 0x00200888u);
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	poll_all();
	assert(count_substr("restarting at high speed") == 0);	/* before the timeout */
	fake_t += 9000u * 1000u;
	poll_all();
	assert(count_substr("restarting at high speed") == 0);
	fake_t += 2000u * 1000u;	/* past 10000 ms after RUN */
	poll_all();
	assert_line("USB3 GAP: not configured in 10000 ms: restarting at high speed");
	for (i = 0; i < n_writes; i++)
		if (wlog[i].addr == FAKE_BASE + R_DCFG) {
			if ((wlog[i].val & 7u) == 4u)
				dcfg_ss++;
			else if (wlog[i].val == 0x00200800u)
				dcfg_hs++;
		}
	assert(dcfg_ss == 1 && dcfg_hs == 1);
	assert(last_write_val(R_DCFG) == 0x00200800u);
	assert(last_write_val(R_DCTL) & DCTL_RUN_STOP);
	assert(usbgadget_faulted() == 0);
	/* never a second restart */
	fake_t += 60000u * 1000u;
	poll_all();
	poll_all();
	assert(count_substr("restarting at high speed") == 1);
	/* the restarted session enumerates at HS normally */
	ss_enumerate(0x00000000u);
	assert_line("USB3: conndone speed=0");
	assert(usbgadget_faulted() == 0);
	assert_whitelist();
}

static void test_ss_speed_contradiction(void)
{
	static const uint8_t set_dtr[8] =
		{ 0x21, 0x22, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00 };
	unsigned i, start, end_i = 0, stop_i = 0;
	const uint32_t endxfer = 8u | (1u << 11) | (4u << 16) | DEPCMD_CMDACT;

	fake_reset();
	assert(usbgadget_init((uintptr_t)pool, sizeof(pool), &hooks) == 0);
	ss_enumerate(0x00000004u);	/* bulk programmed at 1024 */
	assert_line("USB3: bulk max packet 1024");
	inject_setup_complete(set_dtr);
	poll_all();
	status_in_roundtrip();
	assert(usbgadget_ready() == 1);
	assert(usbgadget_write("x", 1) == 1);	/* EP1IN transfer is now active */
	con_reset();
	start = n_writes;
	/* No bus reset first (it would end EP1IN itself): the restart must
	 * end the active transfer. A HS ConnectDone contradicts the 1024,
	 * followed by 19 events of the old session: 15 skipped in the batch
	 * of 16, 4 still pending in the buffer when the core is stopped. */
	reg_seed(R_DSTS, 0x00000000u);
	inject_event(EVT_DEV(2));
	for (i = 0; i < 19; i++)
		inject_event(EVT_DEV(1));
	poll_all();
	assert_line("USB3 GAP: speed contradicts the programmed bulk max packet: restarting at high speed");
	assert_line("USB3 GAP: batch skipped: 15");
	assert_line("USB3 GAP: events dropped: 4");
	assert_line("USB3 GAP: controller not halted 1000 ms after RUN_STOP=0; soft reset anyway");
	assert_line("USB3 high-speed restart done");
	assert(count_substr("restarting at high speed") == 1);
	assert(usbgadget_faulted() == 0);
	/* the restart ended EP1IN before it cleared RUN_STOP */
	for (i = start; i < n_writes; i++) {
		if (!end_i && wlog[i].addr == FAKE_BASE + R_DEPCMD(3) &&
		    wlog[i].val == endxfer)
			end_i = i + 1;
		if (!stop_i && wlog[i].addr == FAKE_BASE + R_DCTL &&
		    !(wlog[i].val & DCTL_RUN_STOP))
			stop_i = i + 1;
	}
	assert(end_i && stop_i && end_i < stop_i);
	/* the old session is gone: nothing is ready, nothing is written */
	assert(usbgadget_ready() == 0);
	assert(usbgadget_write("x", 1) == 0);
	/* second contradiction: no second restart */
	ss_enumerate(0x00000000u);
	assert(count_substr("restarting at high speed") == 1);
}
#endif

int main(void)
{
	test_bringup_sequence();
	test_fault_third_access();
	test_soft_reset_timeout();
	test_enumeration();
	test_bulk_write();
	test_poll_bounded();
	test_init_arg_checks();
	test_depcmd_timeout();
	test_reset_mid_transfer();
	test_role_check();
	test_stop();
	test_zero_length_get();
	test_foreign_event();
#ifdef USB_GADGET_SS
	test_ss_bringup_and_speed();
	test_hs_connect_in_ss_build();
	test_ss_fallback();
	test_ss_speed_contradiction();
#endif
	printf("PASS usbgadget host test: bring-up sequence, fault/sticky, "
	       "soft-reset timeout, ep0 machine, SET_ADDRESS immediate, "
	       "ep1 enable, bulk write, poll bound, arg checks, DEPCMD "
	       "timeout, reset mid-transfer, role check, stop, zero-length, foreign event\n");
	return 0;
}
