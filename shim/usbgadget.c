/*
 * usbgadget.c - stage-2 polled DWC3 device-mode driver core for the
 * bluejay shim; see usbgadget.h for the contract. The register map,
 * command codes, TRB bits, event decoding and the ep0 state machine
 * are ported verbatim from the working 0071 patch
 * (xnu/patches/0071-sweet-usb-device-ep0.patch, enumerated on a
 * Qualcomm DWC3 phone); bluejay (gs101) has no QSCRATCH wrapper, so
 * no global/PHY/wrapper register is touched - writes only in the
 * device register set listed in w32_allowed().
 *
 * AUDIT (tools/test-bluejay-boot.py, check_usbgadget_object): every
 * try_write32 call site in the built object must sit inside the
 * symbol w32 below, undefined symbols stay in the sanctioned set,
 * and smc/msr/dc/atomic instructions are banned. Keep w32 the single
 * write path and this file free of inline asm.
 */
#include <stddef.h>
#include <stdint.h>

#include "console.h"
#include "usbproto.h"
#include "usbgadget.h"

#define USBG_DWC3_BASE		0x11110000UL	/* usbprobe.c */

/* Whitelist for w32(): the DWC3 device register window (stage-2 task). */
#define USBG_DEP_LO		0xC800u	/* DEPCMD blocks of phys eps 0..3 */
#define USBG_DEP_HI		0xC840u	/* exclusive */

#define USBG_GCTL		0xc110u	/* 0071 patch; READ ONLY here (PRTCAP check) */
#define USBG_GEVNTADRLO		0xc400u	/* 0071 patch */
#define USBG_GEVNTADRHI		0xc404u	/* 0071 patch */
#define USBG_GEVNTSIZ		0xc408u	/* 0071 patch */
#define USBG_GEVNTCOUNT		0xc40cu	/* 0071 patch */
#define USBG_DCFG		0xc700u	/* 0071 patch */
#define USBG_DCTL		0xc704u	/* 0071 patch */
#define USBG_DEVTEN		0xc708u	/* 0071 patch */
#define USBG_DSTS		0xc70cu	/* 0071 patch */
#define USBG_DALEPENA		0xc720u	/* 0071 patch */
#define USBG_DEPCMDPAR0(n)	(0xc808u + 0x10u * (n))	/* 0071 patch */
#define USBG_DEPCMDPAR1(n)	(0xc804u + 0x10u * (n))	/* 0071 patch */
#define USBG_DEPCMDPAR2(n)	(0xc800u + 0x10u * (n))	/* 0071 patch */
#define USBG_DEPCMD(n)		(0xc80cu + 0x10u * (n))	/* 0071 patch */

#define USBG_DCTL_RUN_STOP	(1u << 31)	/* 0071 patch */
#define USBG_DCTL_CSFTRST	(1u << 30)	/* 0071 patch */
#define USBG_DCTL_ULSTCHNGREQ	(0xfu << 5)	/* Linux dctl_write_safe masks this */
#define USBG_DSTS_DEVCTRLHLT	(1u << 22)	/* 0071 patch */

#define USBG_DEPCMD_SETEPCONFIG		1u	/* 0071 patch */
#define USBG_DEPCMD_SETTRANSFRESOURCE	2u	/* 0071 patch */
#define USBG_DEPCMD_SETSTALL		4u	/* 0071 patch */
#define USBG_DEPCMD_STARTTRANSFER	6u	/* 0071 patch */
#define USBG_DEPCMD_ENDTRANSFER		8u	/* Linux dwc3 core.h */
#define USBG_DEPCMD_FORCERM		(1u << 11)	/* Linux dwc3 HIPRI_FORCERM */
#define USBG_DEPCMD_DEPSTARTCFG		9u	/* 0071 patch */
#define USBG_DEPCMD_CMDACT		(1u << 10)	/* 0071 patch */

#define USBG_TRB_HWO		(1u << 0)	/* 0071 patch */
#define USBG_TRB_LST		(1u << 1)	/* 0071 patch */
#define USBG_TRB_ISP_IMI	(1u << 10)	/* 0071 patch */
#define USBG_TRB_IOC		(1u << 11)	/* 0071 patch */
#define USBG_TRBCTL_SETUP	(2u << 4)	/* 0071 patch */
#define USBG_TRBCTL_STATUS2	(3u << 4)	/* 0071 patch */
#define USBG_TRBCTL_STATUS3	(4u << 4)	/* 0071 patch */
#define USBG_TRBCTL_DATA	(5u << 4)	/* 0071 patch (control data) */
#define USBG_TRBCTL_NORMAL	(1u << 4)	/* Linux dwc3 core.h: bulk/interrupt */

#define USBG_DCFG_DEVSPD_MASK	7u		/* 0071 patch (cleared with 7u) */
#define USBG_DCFG_DEVADDR_MASK	(0x7fu << 3)	/* 0071 patch */
#define USBG_DEVSPD_HS		0u	/* 0071 patch: speed bits cleared = HS */
#ifdef USB_GADGET_SS
/* SuperSpeed (build flag USB_GADGET_SS, port of xnu patch 0173; all field
 * values are 0173's citations, Linux v6.12 drivers/usb/dwc3/core.h:113,
 * 120,137,142,144,342,360,404,435-443,520-532, gadget.c:4105-4143, ep0.c):
 * DCFG.DEVSPD [2:0] SUPERSPEED = 4, DSTS.CONNECTSPD [2:0] SUPERSPEED = 4
 * (SUPERSPEED_PLUS = 5), GUSB2PHYCFG 0xc200, GUSB3PIPECTL 0xc2c0. */
#define USBG_DEVSPD_SS		4u
#define USBG_CONNSPD_SS		4u
#define USBG_GUSB2PHYCFG	0xc200u	/* READ ONLY */
#define USBG_GUSB3PIPECTL	0xc2c0u	/* READ ONLY */
#define USBG_EP0_MPS_SS		512u	/* gadget.c:4105-4123 (0173) */
#define USBG_BULK_MPS_SS	1024u	/* f_ecm.c:293-309 (0173); burst 0 */
#define USBG_BULK_MPS_HS	512u
/* Policy, not hardware (0173 UD_SS_WAIT_MS): not configured this long
 * after RUN in SS mode -> restart once at high speed. */
#define USBG_SS_WAIT_MS		10000u
#endif

#define USBG_DEVTEN_EVENTS	0x7u	/* 0071 patch: disc + reset + conndone */
#define USBG_GEVNTSIZ_INTMASK	(1u << 31)	/* 0071 patch: polled */
#define USBG_DALEPENA_EP0	0x3u	/* 0071 patch */

#define USBG_DEVEVT_DISCONNECT	0u	/* 0071 patch */
#define USBG_DEVEVT_RESET	1u	/* 0071 patch */
#define USBG_DEVEVT_CONNDONE	2u	/* 0071 patch */
/* UNSURE: device-event type 3 is the link state change event (Linux
 * dwc3 DEVTEN.ULSTCNGEN would enable it); neither the 0071 patch
 * (only types 0/1/2) nor usbprobe.c defines it. DEVTEN stays at the
 * patch's 0x7, so this type should never arrive; decoded for the
 * on-screen log only, never acted on. */
#define USBG_DEVEVT_LINKSTATE	3u

#define USBG_EPEVT_XFERCOMPLETE	1u	/* 0071 patch */
#define USBG_EPEVT_XFERNOTREADY	3u	/* 0071 patch */
#define USBG_EPEVT_CTRLSTAT	2u	/* 0071 patch: status = control status */

/* DEPCFG parameter words. ep0 shapes are verbatim from the 0071 patch:
 * param0 = type<<1 | mps<<3 [| modify 2<<30], param1 = (1<<8) |
 * (1<<10) | (physical ep number<<25) (the patch's "dir<<25" is the LSB
 * of that field: ep0out=0, ep0in=1). The bulk fields below follow
 * Linux dwc3 core.h (DWC3_DEPCFG_EP_TYPE/FIFO_NUMBER/EP_NUMBER);
 * UNSURE on hardware: the patch never configures a bulk endpoint. */
#define USBG_DEPCFG_TYPE_BULK	(2u << 1)	/* Linux dwc3: EP_TYPE, bulk=2 */
#define USBG_DEPCFG_FIFO(n)	((uint32_t)(n) << 17)	/* Linux dwc3: TX FIFO */
#define USBG_DEPCFG_P1_EPNUM(n)	((uint32_t)(n) << 25)	/* Linux dwc3 */
#define USBG_DEPCFG_MPS(m)	((uint32_t)(m) << 3)	/* 0071 patch */
#define USBG_DEPCFG_MODIFY	(2u << 30)	/* 0071 patch (conndone) */
#define USBG_DEPCFG_P1_DONE	(1u << 8)	/* 0071 patch */
#define USBG_DEPCFG_P1_NOTREADY	(1u << 10)	/* 0071 patch */
#define USBG_DEPCFG_P1_DIR_IN	(1u << 25)	/* 0071 patch */

/* Physical endpoint indices: usbprobe.c names DEP block 0 = ep0out,
 * block 1 = ep0in; the same even-OUT/odd-IN numbering gives 3 for
 * the bulk IN pair of logical ep1.
 * UNSURE: extrapolated - the patch only ever uses blocks 0/1
 * (DALEPENA 0x3), never a bulk endpoint. */
#define USBG_EP0OUT		0u
#define USBG_EP0IN		1u
#define USBG_EP1IN		3u

#define USBG_SETUP_LEN		8u	/* 0071 patch (setup TRB of 8 bytes) */
#define USBG_EP0_DATA_CAP	512u	/* 0071 patch (UD_DATA 512 bytes) */
#define USBG_EP0_MPS		64u	/* HS ep0 max packet; OUT TRB length is MPS-aligned (Linux ep0.c) */
#ifdef USB_GADGET_SS
/* ep0 OUT TRB at SS is 512 bytes (multiple of the SS ep0 max packet,
 * ep0.c as 0173 notes); it lands in USBG_UD_DATA, which spans
 * USBG_UD_DATA..+USBG_EP0_DATA_CAP (0x1100..0x1300) before USBG_UD_BULK_TRB. */
_Static_assert(USBG_EP0_MPS_SS <= USBG_EP0_DATA_CAP,
	       "SS ep0 OUT TRB must fit the ep0 data area");
#endif
#define USBG_PRTCAP_DEVICE	2u	/* GCTL bits 13:12 */
#define USBG_BULK_MAX		4096u	/* one 4 KiB bulk buffer */
#define USBG_N_BULK_TRB		4u	/* stage-2 task: 4-TRB bulk ring */
#define USBG_TRB_LEN		16u

/* Timeouts, from the 0071 patch's wait loops (500 iterations of 1 ms;
 * 1 ms per DEPCMD poll). */
#define USBG_RESET_TIMEOUT_MS	500u
#define USBG_CMD_TIMEOUT_MS	10u
#define USBG_RUN_TIMEOUT_MS	500u

_Static_assert(USBG_UD_DATA + USBG_EP0_DATA_CAP <= USBG_UD_BULK_TRB,
	       "ep0 data area must end before the bulk TRB ring");
_Static_assert((USBG_UD_EVT_SIZE & (USBG_UD_EVT_SIZE - 1)) == 0,
	       "event buffer size must be a power of two");
_Static_assert((USBG_N_BULK_TRB & (USBG_N_BULK_TRB - 1)) == 0,
	       "bulk TRB ring must be a power of two");
_Static_assert(USBG_UD_BULK_DATA + USBG_BULK_MAX <= USBG_POOL_MIN,
	       "bulk buffer must fit the minimum pool");

/* ep0 states, named as in the 0071 patch's ud struct. */
enum { EP0_SETUP, EP0_DATA, EP0_WAIT_STATUS2, EP0_WAIT_STATUS3, EP0_STATUS };

static struct {
	int inited;
	int fault;		/* sticky; every entry point checks it */
	uintptr_t pool;		/* CPU view; VA == PA (shim runs MMU off) */
	uint64_t pool_pa;	/* DMA address programmed into the core */
	const struct usbgadget_hooks *hooks;
	struct usbproto proto;
	uint32_t evt_pos;
	unsigned ep0_state;
	int ep0_data_out;	/* completed EP0_DATA stage was OUT */
	uint8_t rsc[4];		/* transfer resource index from the last STARTTRANSFER */
	uint8_t active[4];	/* a transfer is started and not yet completed/ended */
	int ep1_on;
	int bulk_busy;
	unsigned bulk_trb;
	uint32_t evt_printed;	/* one bit per device-event type shown */
#ifdef USB_GADGET_SS
	int ss_mode;		/* DCFG.DEVSPD = SuperSpeed in this session */
	int ss_restarted;	/* the one HS restart was used */
	int ss_restart_req;	/* set in the event handler, run from poll */
	const char *ss_reason;	/* why the restart was requested */
	int ss_confirmed;	/* a SS configuration reached bulk enable */
	uint32_t ep0_mps;	/* 64, or 512 after a SS ConnectDone */
	uint32_t bulk_mps;	/* bulk max packet last programmed, 0 = never */
	uint32_t speed;		/* DSTS.CONNECTSPD at the last ConnectDone */
	uint64_t ss_deadline;	/* ticks: RUN + USBG_SS_WAIT_MS */
#endif
} g;

/* Sticky fault: prints once, then the gadget stays dead (fail closed). */
static int fault_set(void)
{
	if (!g.fault) {
		g.fault = 1;
		con_str("USB", "gadget fault");
	}
	return -1;
}

/* The ONLY function in this file allowed to call try_write32; the
 * object audit checks every bl site in the built .o. noinline keeps
 * the call sites inside this one symbol (GCC would otherwise inline
 * it into every caller and scatter the MMIO writes). */
static int w32_allowed(uint32_t off)
{
	switch (off) {
	case USBG_GEVNTADRLO: case USBG_GEVNTADRHI: case USBG_GEVNTSIZ:
	case USBG_GEVNTCOUNT: case USBG_DCFG: case USBG_DCTL:
	case USBG_DEVTEN: case USBG_DALEPENA:
		return 1;
	default:
		/* DEPCMD/PAR blocks of physical endpoints 0..3 only. */
		return off >= USBG_DEP_LO && off < USBG_DEP_HI && !(off & 3u);
	}
}

__attribute__((noinline)) static int w32(uint32_t off, uint32_t v)
{
	if (!w32_allowed(off))
		return (int)fault_set();
	if (!try_write32(USBG_DWC3_BASE + off, v))
		return (int)fault_set();
	return 0;
}

static int r32(uint32_t off, uint32_t *v)
{
	if (!try_read32(USBG_DWC3_BASE + off, v))
		return fault_set();
	return 0;
}

#ifdef USB_GADGET_SS
/* Line builder (con_str is the only console symbol the audit allows). */
static unsigned cat_str(char *b, unsigned p, unsigned cap, const char *t)
{
	while (*t && p + 1 < cap)
		b[p++] = *t++;
	b[p] = 0;
	return p;
}

static unsigned cat_hex(char *b, unsigned p, unsigned cap, uint32_t v)
{
	int sh;

	p = cat_str(b, p, cap, "0x");
	for (sh = 28; sh >= 0; sh -= 4) {
		if (p + 1 < cap)
			b[p++] = "0123456789abcdef"[(v >> sh) & 0xfu];
	}
	b[p] = 0;
	return p;
}

/* Read-only report (rule 8: shown, not guessed): the PHY/PIPE/DCFG/DSTS
 * words before and after the soft reset. Reads only, no writes. */
static int ss_report(const char *what)
{
	uint32_t p2, p3, dcfg, dsts;
	char b[128];
	unsigned p = 0;

	if (r32(USBG_GUSB2PHYCFG, &p2) || r32(USBG_GUSB3PIPECTL, &p3) ||
	    r32(USBG_DCFG, &dcfg) || r32(USBG_DSTS, &dsts))
		return -1;
	p = cat_str(b, p, sizeof(b), what);
	p = cat_str(b, p, sizeof(b), " GUSB2PHYCFG=");
	p = cat_hex(b, p, sizeof(b), p2);
	p = cat_str(b, p, sizeof(b), " GUSB3PIPECTL=");
	p = cat_hex(b, p, sizeof(b), p3);
	p = cat_str(b, p, sizeof(b), " DCFG=");
	p = cat_hex(b, p, sizeof(b), dcfg);
	p = cat_str(b, p, sizeof(b), " DSTS=");
	cat_hex(b, p, sizeof(b), dsts);	/* last field: cat_hex NUL-terminates b, the end index is not needed */
	con_str("USB3:", b);
	return 0;
}
#endif

static uint64_t deadline_ms(unsigned ms)
{
	return g.hooks->ticks() + g.hooks->ticks_freq * ms / 1000u;
}

static int timed_out(uint64_t deadline)
{
	return g.hooks->ticks() >= deadline;
}

/* One endpoint command; parameter and completion handling as in the
 * 0071 patch's ud_epcmd (PAR0, PAR1, PAR2, then CMD|CMDACT; poll
 * CMDACT; bits 15:12 of the result are the command status). */
static int epcmd(unsigned ep, uint32_t cmd, uint32_t p0, uint32_t p1,
		 uint32_t p2)
{
	uint64_t dl;

	if (w32(USBG_DEPCMDPAR0(ep), p0) || w32(USBG_DEPCMDPAR1(ep), p1) ||
	    w32(USBG_DEPCMDPAR2(ep), p2) ||
	    w32(USBG_DEPCMD(ep), cmd | USBG_DEPCMD_CMDACT))
		return fault_set();
	dl = deadline_ms(USBG_CMD_TIMEOUT_MS);
	/* bounded: timed_out(dl), USBG_CMD_TIMEOUT_MS, below */
	for (;;) {
		uint32_t v;
		if (r32(USBG_DEPCMD(ep), &v))
			return -1;
		if (!(v & USBG_DEPCMD_CMDACT)) {
			if ((v >> 12) & 0xfu) {
				con_str("USB", "epcmd status != 0");
				return fault_set();
			}
			if ((cmd & 0xffu) == USBG_DEPCMD_STARTTRANSFER && ep < 4) {
				/* Result: transfer resource index, bits 22:16 (Linux
				 * dwc3_send_gadget_ep_cmd); needed for ENDTRANSFER. */
				g.rsc[ep] = (uint8_t)((v >> 16) & 0x7fu);
				g.active[ep] = 1;
			}
			return 0;
		}
		if (timed_out(dl)) {
			con_str("USB", "epcmd timeout");
			return fault_set();
		}
	}
}

/* End a started transfer (Linux dwc3 ENDTRANSFER with ForceRM): required
 * on bus reset / disconnect for every active endpoint (databook, Linux
 * gadget.c dwc3_stop_active_transfers) so the next STARTTRANSFER does not
 * hit "No Resource". No-op when nothing is active. */
static int end_transfer(unsigned ep)
{
	if (ep >= 4 || !g.active[ep])
		return 0;
	g.active[ep] = 0;
	return epcmd(ep, USBG_DEPCMD_ENDTRANSFER | USBG_DEPCMD_FORCERM |
			 ((uint32_t)g.rsc[ep] << 16), 0, 0, 0);
}

/* Fill one TRB in the pool (DMA memory, not a register). */
static void trb_write(uintptr_t off, uint64_t buf_pa, uint32_t len,
		      uint32_t ctl)
{
	volatile uint32_t *t = (volatile uint32_t *)(g.pool + off);

	t[0] = (uint32_t)buf_pa;	/* 0071 patch ud_start */
	t[1] = (uint32_t)(buf_pa >> 32);
	t[2] = len;
	t[3] = ctl | USBG_TRB_HWO | USBG_TRB_LST | USBG_TRB_ISP_IMI |
	       USBG_TRB_IOC;	/* 0071 patch */
}

/* Start a transfer on ep0 out/in; the TRB lives at the ep's own slot
 * (0071 patch ud_start: ep0out at UD_TRB_OUT, ep0in at UD_TRB_IN). */
static int start_ep_trb(unsigned ep, uint32_t len, uint32_t ctl)
{
	uintptr_t trb_off = ep ? USBG_UD_TRB_IN : USBG_UD_TRB_OUT;
	uint64_t tpa = g.pool_pa + trb_off;	/* 0071 patch */

	trb_write(trb_off, g.pool_pa + USBG_UD_DATA, len, ctl);
	if (epcmd(ep, USBG_DEPCMD_STARTTRANSFER, (uint32_t)(tpa >> 32),
		  (uint32_t)tpa, 0))
		return -1;
	return 0;
}

static int start_bulk_trb(uint32_t len)
{
	uintptr_t trb_off = USBG_UD_BULK_TRB + g.bulk_trb * USBG_TRB_LEN;
	uint64_t tpa = g.pool_pa + trb_off;

	trb_write(trb_off, g.pool_pa + USBG_UD_BULK_DATA, len,
		  USBG_TRBCTL_NORMAL);
	if (epcmd(USBG_EP1IN, USBG_DEPCMD_STARTTRANSFER,
		  (uint32_t)(tpa >> 32), (uint32_t)tpa, 0))
		return -1;
	return 0;
}

static void con_dec_l(const char *label, const char *head, uint32_t v);
static void con_dec(const char *head, uint32_t v)
{
	con_dec_l("USB", head, v);
}

/* Current ep0 max packet: 64 (HS), 512 after a SuperSpeed ConnectDone. */
static uint32_t ep0_mps(void)
{
#ifdef USB_GADGET_SS
	return g.ep0_mps;
#else
	return USBG_EP0_MPS;
#endif
}

/* Prime the setup TRB (0071 patch ud_start_setup). */
static int arm_setup(void)
{
	uintptr_t trb_off = USBG_UD_TRB_OUT;
	uint64_t tpa = g.pool_pa + trb_off;

	g.ep0_state = EP0_SETUP;
	trb_write(trb_off, g.pool_pa + USBG_UD_SETUP, USBG_SETUP_LEN,
		  USBG_TRBCTL_SETUP);
	if (epcmd(USBG_EP0OUT, USBG_DEPCMD_STARTTRANSFER,
		  (uint32_t)(tpa >> 32), (uint32_t)tpa, 0))
		return -1;
	return 0;
}

static int ep0_stall(void)
{
	/* 0071 patch ud_stall: SETSTALL on ep0, then re-arm setup. */
	if (epcmd(USBG_EP0OUT, USBG_DEPCMD_SETSTALL, 0, 0, 0))
		return -1;
	return arm_setup();
}

/* Enable bulk IN ep1 after SET_CONFIGURATION(1) completed its status
 * stage (stage-2 task (c): DEPCFG / transfer resource / DALEPENA). */
static int enable_ep1(void)
{
	uint32_t bulk_mps = 512;

	if (g.ep1_on)
		return 0;
#ifdef USB_GADGET_SS
	/* 1024 with burst 0 at SuperSpeed (0173: gadget.c:586-592, f_ecm.c),
	 * 512 at HS. */
	bulk_mps = g.proto.ss ? USBG_BULK_MPS_SS : USBG_BULK_MPS_HS;
#endif
	/* Linux dwc3 (ep0.c:663, gadget.c:553-569): DEPSTARTCFG with XferRscIdx
	 * 2 (DEPCMD bits 31:16) once per configuration, issued through ep0
	 * (physical ep 0), never through the endpoint being configured. */
	if (epcmd(USBG_EP0OUT, USBG_DEPCMD_DEPSTARTCFG | (2u << 16), 0, 0, 0) ||
	    epcmd(USBG_EP1IN, USBG_DEPCMD_SETEPCONFIG,
		  USBG_DEPCFG_TYPE_BULK | USBG_DEPCFG_FIFO(1) |
		  USBG_DEPCFG_MPS(bulk_mps),
		  USBG_DEPCFG_P1_DONE | USBG_DEPCFG_P1_NOTREADY |
		  USBG_DEPCFG_P1_EPNUM(USBG_EP1IN), 0) ||
	    epcmd(USBG_EP1IN, USBG_DEPCMD_SETTRANSFRESOURCE, 1, 0, 0) ||
	    w32(USBG_DALEPENA, USBG_DALEPENA_EP0 | (1u << USBG_EP1IN)))
		return -1;
	g.ep1_on = 1;
#ifdef USB_GADGET_SS
	g.bulk_mps = bulk_mps;
	g.ss_confirmed = 1;	/* configured (SS or HS): no fallback needed */
	if (g.proto.ss) {
		con_dec_l("USB3:", "bulk max packet ", bulk_mps);
	}
#endif
	return 0;
}

/* Handle one SETUP packet: pure decision via usbproto, hardware moves
 * here. Mirrors the 0071 patch's ud_setup + Linux ep0.c staging. */
static int ep0_handle_setup(void)
{
	const volatile uint8_t *s = (const volatile uint8_t *)
				    (g.pool + USBG_UD_SETUP);
	uint8_t setup[USBG_SETUP_LEN];
	const uint8_t *data = 0;
	uint16_t len = 0;
	unsigned i;
	int rc;

	for (i = 0; i < USBG_SETUP_LEN; i++)
		setup[i] = s[i];
	rc = usbproto_setup(setup, &g.proto, &data, &len);
#ifdef USB_GADGET_SS
	if (rc == USBPROTO_STALL && g.proto.ss) {
		/* Rule 8: every request refused at SuperSpeed is shown. */
		char b[96];
		unsigned p = cat_str(b, 0, sizeof(b),
				     "GAP: ep0 request stalled bmRT/bReq=");
		p = cat_hex(b, p, sizeof(b),
			    ((uint32_t)setup[0] << 8) | setup[1]);
		p = cat_str(b, p, sizeof(b), " wValue=");
		cat_hex(b, p, sizeof(b),
			(uint32_t)(setup[2] | ((uint32_t)setup[3] << 8)));
		con_str("USB3", b);
	}
	if (rc == USBPROTO_OK_SET_SEL) {
		/* ep0.c SET_SEL (0173): 6 data bytes accepted, not programmed
		 * (no DGCMD periodic parameters; U1/U2 stay off). */
		con_str("USB3", "GAP: SET_SEL acknowledged, not programmed");
		g.ep0_state = EP0_DATA;
		g.ep0_data_out = 2;	/* OUT stage, bytes discarded */
		return start_ep_trb(USBG_EP0OUT, ep0_mps(), USBG_TRBCTL_DATA);
	}
	if (rc == USBPROTO_OK_ISOCH_DELAY) {
		con_dec_l("USB3:", "SET_ISOCH_DELAY ns=",
			(uint32_t)(setup[2] | ((uint32_t)setup[3] << 8)));
		g.ep0_state = EP0_WAIT_STATUS2;
		return 0;
	}
#endif
	if (rc == USBPROTO_STALL)
		return ep0_stall();
	if (rc == USBPROTO_OK_DATA_IN && len == 0)
		rc = USBPROTO_OK_NO_DATA;	/* wLength 0: two-stage request */
	if (rc == USBPROTO_OK_SET_ADDRESS) {
		/* Linux ep0.c:624-627 and the 0071 patch write DCFG now; the
		 * core applies the address itself after the status stage. */
		uint32_t dcfg;
		if (r32(USBG_DCFG, &dcfg) ||
		    w32(USBG_DCFG, (dcfg & ~USBG_DCFG_DEVADDR_MASK) |
				   ((uint32_t)(g.proto.address & 0x7fu) << 3)))
			return -1;
		g.ep0_state = EP0_WAIT_STATUS2;
		return 0;
	}
	if (rc == USBPROTO_OK_NO_DATA) {
		g.ep0_state = EP0_WAIT_STATUS2;
		return 0;
	}
	if (rc == USBPROTO_OK_DATA_IN) {
		volatile uint8_t *d = (volatile uint8_t *)(g.pool + USBG_UD_DATA);
		if (len > USBG_EP0_DATA_CAP)
			len = USBG_EP0_DATA_CAP;
		for (i = 0; i < len; i++)
			d[i] = data[i];
		g.ep0_state = EP0_DATA;
		g.ep0_data_out = 0;
		return start_ep_trb(USBG_EP0IN, len, USBG_TRBCTL_DATA);
	}
	if (rc == USBPROTO_OK_DATA_OUT) {
		uint16_t wlen = (uint16_t)(setup[6] | ((uint16_t)setup[7] << 8));
		if (!wlen || wlen > USBG_EP0_MPS)
			return ep0_stall();
		g.ep0_state = EP0_DATA;
		g.ep0_data_out = 1;
		/* Linux ep0.c:1009-1036 bounces non-MPS-aligned OUT lengths up to
		 * a multiple of maxpacket: use a full-MPS TRB, copy the real
		 * (<= 7) bytes on completion. */
		return start_ep_trb(USBG_EP0OUT, ep0_mps(), USBG_TRBCTL_DATA);
	}
	return ep0_stall();
}

/* Decimal print helper (no libc): "EVT conndone speed=13". */
static void con_dec_l(const char *label, const char *head, uint32_t v)
{
	char buf[32], tmp[10];
	unsigned p = 0, k = 0;

	while (*head && p + 1 < sizeof(buf))
		buf[p++] = *head++;
	do {
		tmp[k++] = (char)('0' + v % 10u);
		v /= 10u;
	} while (v && k < sizeof(tmp));
	while (k && p + 1 < sizeof(buf))
		buf[p++] = tmp[--k];
	buf[p] = 0;
	con_str(label, buf);
}

/* Every distinct device event type is shown once per boot. */
static void print_evt_once(unsigned type, const char *msg)
{
	if (type < 32 && !(g.evt_printed & (1u << type))) {
		g.evt_printed |= 1u << type;
		con_str("USB", msg);
	}
}

/* Drop enumeration state on bus reset or disconnect. The armed setup TRB
 * is LEFT ALONE while ep0 is in the setup phase (databook 8.1.2, Linux
 * gadget.c dwc3_ep0_reset_state; the 0071 patch never re-arms on reset):
 * a second STARTTRANSFER on the active setup endpoint fails with "No
 * Resource". Any other active transfer (bulk, or ep0 data/status) is ended
 * first; ep0 is then re-armed only if its setup TRB was consumed. */
static int drop_enumeration(void)
{
	uint32_t dcfg;
	int ep0_busy = g.ep0_state != EP0_SETUP;

	if (r32(USBG_DCFG, &dcfg) ||
	    w32(USBG_DCFG, dcfg & ~USBG_DCFG_DEVADDR_MASK))
		return -1;
	usbproto_init(&g.proto);
	g.ep1_on = 0;
	g.bulk_busy = 0;
	g.bulk_trb = 0;
	g.ep0_data_out = 0;
	if (end_transfer(USBG_EP1IN))
		return -1;
	if (ep0_busy) {
		if (end_transfer(USBG_EP0OUT) || end_transfer(USBG_EP0IN) ||
		    arm_setup())
			return -1;
	}
	return 0;
}

static int handle_device_event(unsigned type)
{
	if (type == USBG_DEVEVT_RESET) {
		print_evt_once(type, "EVT reset");
		return drop_enumeration();
	}
	if (type == USBG_DEVEVT_CONNDONE) {
		uint32_t dsts;
		if (r32(USBG_DSTS, &dsts))
			return -1;
		uint32_t mps = 64;
#ifdef USB_GADGET_SS
		/* 512 at SuperSpeed, 64 at HS/FS (gadget.c:4105-4143, 0173) */
		if ((dsts & 7u) >= USBG_CONNSPD_SS)
			mps = USBG_EP0_MPS_SS;
		g.speed = dsts & 7u;
		g.ep0_mps = mps;
		g.proto.ss = mps == USBG_EP0_MPS_SS;
		if (g.ss_mode && !g.ss_restarted && !g.ss_restart_req &&
		    g.bulk_mps &&
		    g.bulk_mps != (g.proto.ss ? USBG_BULK_MPS_SS :
						USBG_BULK_MPS_HS)) {
			/* contradicts the programmed bulk max packet; the
			 * restart runs from usbgadget_poll after the ack */
			g.ss_restart_req = 1;
			g.ss_reason = "speed contradicts the programmed bulk max packet";
		} else if (g.ss_restarted && g.bulk_mps &&
			   g.bulk_mps != (g.proto.ss ? USBG_BULK_MPS_SS :
						       USBG_BULK_MPS_HS)) {
			con_str("USB3", "GAP: speed contradicts the bulk max packet, not restarted again");
		}
#endif
		/* ep0 max packet 64 at high/full speed, modify action
		 * (0071 patch connect-done path). */
		if (epcmd(USBG_EP0OUT, USBG_DEPCMD_SETEPCONFIG,
			  USBG_DEPCFG_MPS(mps) | USBG_DEPCFG_MODIFY,
			  USBG_DEPCFG_P1_DONE | USBG_DEPCFG_P1_NOTREADY,
			  0) ||
		    epcmd(USBG_EP0IN, USBG_DEPCMD_SETEPCONFIG,
			  USBG_DEPCFG_MPS(mps) | USBG_DEPCFG_MODIFY,
			  USBG_DEPCFG_P1_DONE | USBG_DEPCFG_P1_NOTREADY |
			  USBG_DEPCFG_P1_DIR_IN, 0))
			return -1;
		con_dec("EVT conndone speed=", dsts & 7u);	/* 0071 patch */
#ifdef USB_GADGET_SS
		con_dec_l("USB3:", "conndone speed=", dsts & 7u);
#endif
		return 0;
	}
	if (type == USBG_DEVEVT_DISCONNECT) {
		print_evt_once(type, "EVT disconnect");
		return drop_enumeration();
	}
	if (type == USBG_DEVEVT_LINKSTATE) {
		print_evt_once(type, "EVT linkstate");
		return 0;
	}
	print_evt_once(type, "EVT unknown device event");
	return 0;
}

/* ep0 control transfer machine, per the 0071 patch's ud_event + Linux
 * ep0.c: XferComplete while SETUP dispatches, while DATA moves to the
 * status wait, and while STATUS re-arms SETUP; the status stage is
 * started on XferNotReady with status "control status". */
static int handle_ep_event(unsigned ep, unsigned type, unsigned status)
{
	if (type == USBG_EPEVT_XFERCOMPLETE && ep < 4)
		g.active[ep] = 0;
	if (ep == USBG_EP1IN) {
		if (type == USBG_EPEVT_XFERCOMPLETE)
			g.bulk_busy = 0;
		return 0;
	}
	if (ep > USBG_EP0IN)
		return 0;
	if (type == USBG_EPEVT_XFERCOMPLETE) {
		if (g.ep0_state == EP0_SETUP)
			return ep0_handle_setup();
		if (g.ep0_state == EP0_DATA) {
			volatile uint8_t *d = (volatile uint8_t *)
					      (g.pool + USBG_UD_DATA);
			unsigned i;
			if (g.ep0_data_out == 1)
				for (i = 0; i < USBPROTO_LINE_CODING_LEN; i++)
					g.proto.line_coding[i] = d[i];
			g.ep0_state = EP0_WAIT_STATUS3;
			return 0;
		}
		if (g.ep0_state == EP0_STATUS) {
			/* SET_CONFIGURATION takes effect after its status stage. */
			if (g.proto.configured && enable_ep1())
				return -1;
			return arm_setup();
		}
		return 0;
	}
	if (type == USBG_EPEVT_XFERNOTREADY && status == USBG_EPEVT_CTRLSTAT) {
		uint32_t ctl;
		if (g.ep0_state != EP0_WAIT_STATUS2 &&
		    g.ep0_state != EP0_WAIT_STATUS3)
			return 0;
		ctl = g.ep0_state == EP0_WAIT_STATUS3 ?
		      USBG_TRBCTL_STATUS3 : USBG_TRBCTL_STATUS2; /* 0071 patch */
		g.ep0_state = EP0_STATUS;
		return start_ep_trb(ep, 0, ctl);
	}
	return 0;
}

static int handle_event(uint32_t ev)
{
	if (ev & 1u) {	/* device-specific event (0071 patch) */
		if (ev & 0xfeu)	/* type bits 7:1 != 0: carkit / I2C, not ours */
			return 0;
		return handle_device_event((ev >> 8) & 0xfu);
	}
	/* Endpoint event (0071 patch). */
	return handle_ep_event((ev >> 1) & 0x1fu, (ev >> 6) & 0xfu,
			       (ev >> 12) & 0xfu);
}

/* Clear RUN_STOP (keeping ULSTCHNGREQ masked) and wait <= 500 ms for the
 * core to halt (Linux dwc3_gadget_run_stop(false)). Best effort: the
 * write helper already fails closed on a bad register access. */
static void stop_core(void)
{
	uint32_t v;
	uint64_t dl;

	if (r32(USBG_DCTL, &v) ||
	    w32(USBG_DCTL, v & ~(USBG_DCTL_RUN_STOP | USBG_DCTL_ULSTCHNGREQ)))
		return;
	dl = deadline_ms(USBG_RUN_TIMEOUT_MS);
	/* bounded: timed_out(dl), USBG_RUN_TIMEOUT_MS, is the loop condition */
	while (!timed_out(dl)) {
		if (r32(USBG_DSTS, &v) || (v & USBG_DSTS_DEVCTRLHLT))
			return;
	}
}

void usbgadget_stop(void)
{
	if (!g.inited)
		return;
	stop_core();
	g.inited = 0;	/* poll/write/ready are inert from here on */
}

int usbgadget_faulted(void)
{
	return g.fault;
}

/* Steps 1-6 of the bring-up: soft reset .. RUN. Called by usbgadget_init
 * (ss = USB_GADGET_SS build) and once more at HS by the fallback. */
static int hw_start(int ss)
{
	uint32_t v;
	uint64_t dl;
	int cleared = 0, running = 0;

#ifndef USB_GADGET_SS
	(void)ss;
#else
	if (ss_report(ss ? "before soft reset (SS)" :
			  "before soft reset (HS restart)"))
		return -1;
#endif
/* 1. Soft reset of the device controller, <= 500 ms (0071). */
	if (w32(USBG_DCTL, USBG_DCTL_CSFTRST))
		return -1;
	dl = deadline_ms(USBG_RESET_TIMEOUT_MS);
	/* bounded: timed_out(dl), USBG_RESET_TIMEOUT_MS, is the loop condition */
	while (!timed_out(dl)) {
		if (r32(USBG_DCTL, &v))
			return -1;
		if (!(v & USBG_DCTL_CSFTRST)) {
			cleared = 1;
			break;
		}
	}
	if (!cleared) {
		con_str("USB", "gadget: soft reset stuck");
		return -1;
	}
	/* Linux core.c:363-367 waits 50 ms after the clear on older DWC_usb31;
	 * the revision here is unknown, so always wait (bounded, tick based). */
	dl = deadline_ms(50u);
	/* bounded: timed_out(dl), 50 ms, is the loop condition (a fixed wait) */
	while (!timed_out(dl))
		;

	/* 2. High speed (speed bits cleared), address 0 (0071). With
	 * USB_GADGET_SS and ss: DEVSPD = SuperSpeed (4); NUMP and every other
	 * DCFG bit stay as read (0173; Linux core.h DCFG fields above). */
#ifdef USB_GADGET_SS
	if (ss_report("after soft reset"))
		return -1;
#endif
	if (r32(USBG_DCFG, &v))
		return -1;
	v &= ~(USBG_DCFG_DEVSPD_MASK | USBG_DCFG_DEVADDR_MASK);
#ifdef USB_GADGET_SS
	if (ss)
		v |= USBG_DEVSPD_SS;
#endif
	if (w32(USBG_DCFG, v))
		return -1;

	/* 3. Event buffer 0 in our pool (0071). */
	if (w32(USBG_GEVNTADRLO, (uint32_t)(g.pool_pa + USBG_UD_EVT)) ||
	    w32(USBG_GEVNTADRHI, (uint32_t)((g.pool_pa + USBG_UD_EVT) >> 32)) ||
	    w32(USBG_GEVNTSIZ, USBG_GEVNTSIZ_INTMASK | USBG_UD_EVT_SIZE))
		return -1;
	if (r32(USBG_GEVNTCOUNT, &v) || w32(USBG_GEVNTCOUNT, v & 0xffffu))
		return -1;	/* 0071 patch: drop stale count */

	/* 4. Device events (0071). */
	if (w32(USBG_DEVTEN, USBG_DEVTEN_EVENTS))
		return -1;

	/* 5. ep0 OUT/IN: start config, control config (mps 512 until the
	 * connect-done reconfig), transfer resource (0071). */
	if (epcmd(USBG_EP0OUT, USBG_DEPCMD_DEPSTARTCFG, 0, 0, 0) ||
	    epcmd(USBG_EP0OUT, USBG_DEPCMD_SETEPCONFIG, USBG_DEPCFG_MPS(512),
		  USBG_DEPCFG_P1_DONE | USBG_DEPCFG_P1_NOTREADY, 0) ||
	    epcmd(USBG_EP0OUT, USBG_DEPCMD_SETTRANSFRESOURCE, 1, 0, 0) ||
	    epcmd(USBG_EP0IN, USBG_DEPCMD_SETEPCONFIG, USBG_DEPCFG_MPS(512),
		  USBG_DEPCFG_P1_DONE | USBG_DEPCFG_P1_NOTREADY |
		  USBG_DEPCFG_P1_DIR_IN, 0) ||
	    epcmd(USBG_EP0IN, USBG_DEPCMD_SETTRANSFRESOURCE, 1, 0, 0))
		return -1;

	/* 6. Prime the setup TRB, then enable ep0, then run (0071; the
	 * task (b) order puts the primed setup TRB before DALEPENA). */
	if (arm_setup())
		return -1;
	if (w32(USBG_DALEPENA, USBG_DALEPENA_EP0))
		return -1;
	if (r32(USBG_DCTL, &v) ||
	    w32(USBG_DCTL, (v & ~USBG_DCTL_ULSTCHNGREQ) | USBG_DCTL_RUN_STOP))
		return -1;

	/* Observation only (0071 prints the same): did the core leave
	 * halt? Not a failure - poll() simply never sees events. */
	dl = deadline_ms(USBG_RUN_TIMEOUT_MS);
	/* bounded: timed_out(dl), USBG_RUN_TIMEOUT_MS, is the loop condition */
	while (!timed_out(dl)) {
		if (r32(USBG_DSTS, &v)) {
			stop_core();	/* RUN_STOP is set: do not leave it running */
			return -1;
		}
		if (!(v & USBG_DSTS_DEVCTRLHLT)) {
			running = 1;
			break;
		}
	}
	con_str("USB", running ? "gadget: running" :
				"gadget: RUN_STOP set, core still halted");
#ifdef USB_GADGET_SS
	g.ss_mode = ss;
	g.ss_deadline = deadline_ms(USBG_SS_WAIT_MS);
#endif
	g.inited = 1;
	return 0;
}

/* Per-session software state (also run before the HS restart). */
static void session_reset(void)
{
	g.evt_printed = 0;
	g.evt_pos = 0;
	g.ep0_state = EP0_SETUP;
	g.ep1_on = 0;
	g.bulk_busy = 0;
	g.bulk_trb = 0;
	g.ep0_data_out = 0;
	for (unsigned i = 0; i < 4; i++)
		g.rsc[i] = g.active[i] = 0;
	usbproto_init(&g.proto);
#ifdef USB_GADGET_SS
	g.ss_mode = 0;
	g.ss_restart_req = 0;
	g.ss_confirmed = 0;
	g.ep0_mps = USBG_EP0_MPS;
	g.bulk_mps = 0;
	g.speed = 0;
#endif
}

#ifdef USB_GADGET_SS
/* One restart at high speed, run from usbgadget_poll only (never from the
 * event handler: the event buffer is rewritten and the core soft reset;
 * 0173 notes the same). */
/* Restart-specific stop (Linux v6.6 gadget.c:2682-2689: events in the
 * buffer must be acknowledged before the core can halt; 0173 ud_restart
 * does the same with a 1000 ms wait, ABL 0x24124-0x2414c). Acked events are
 * dropped, not dispatched. Rule 8: a timeout and dropped events are logged. */
static void stop_core_acking(void)
{
	uint32_t v, c, dropped = 0;
	uint64_t dl;

	if (r32(USBG_DCTL, &v) ||
	    w32(USBG_DCTL, v & ~(USBG_DCTL_RUN_STOP | USBG_DCTL_ULSTCHNGREQ)))
		return;
	dl = deadline_ms(1000u);	/* 0173 ud_restart: 1000 ms (ABL 0x24124-0x2414c) */
	/* bounded: timed_out(dl), 1000 ms, below */
	for (;;) {
		if (r32(USBG_DSTS, &v))
			return;
		if (v & USBG_DSTS_DEVCTRLHLT)
			break;
		if (r32(USBG_GEVNTCOUNT, &c))
			return;
		c &= 0xffffu;
		if (c) {	/* Linux v6.6 gadget.c:2682-2689: ack, not dispatched */
			if (w32(USBG_GEVNTCOUNT, c))
				return;
			g.evt_pos = (g.evt_pos + c) & (USBG_UD_EVT_SIZE - 1);
			dropped += c / 4;
		}
		if (timed_out(dl)) {
			con_str("USB3", "GAP: controller not halted 1000 ms after RUN_STOP=0; soft reset anyway");
			break;
		}
	}
	if (dropped)
		con_dec_l("USB3", "GAP: events dropped: ", dropped);
}

static void ss_restart_hs(void)
{
	char buf[96];
	unsigned p;

	g.ss_restart_req = 0;
	g.ss_restarted = 1;	/* never more than one restart */
	p = cat_str(buf, 0, sizeof(buf), "GAP: ");
	p = cat_str(buf, p, sizeof(buf), g.ss_reason ? g.ss_reason : "unknown");
	cat_str(buf, p, sizeof(buf), ": restarting at high speed");
	con_str("USB3", buf);
	/* End every started transfer first (as drop_enumeration: databook,
	 * Linux gadget.c dwc3_stop_active_transfers), else the re-armed setup
	 * TRB hits "No Resource"; then halt the core (existing stop_core). */
	if (end_transfer(USBG_EP1IN) || end_transfer(USBG_EP0OUT) ||
	    end_transfer(USBG_EP0IN))
		return;
	g.inited = 0;
	stop_core_acking();
	if (g.fault)
		return;
	session_reset();
	if (hw_start(0)) {
		con_str("USB3", "GAP: HS restart failed");
		fault_set();
		return;
	}
	con_str("USB3", "high-speed restart done");
}
#endif

int usbgadget_init(uintptr_t pool_pa, size_t pool_len,
		   const struct usbgadget_hooks *hooks)
{
	uint32_t v;

	g.inited = 0;
	g.fault = 0;
	g.hooks = hooks;
	g.pool = pool_pa;
	g.pool_pa = pool_pa;
#ifdef USB_GADGET_SS
	g.ss_restarted = 0;
#endif
	session_reset();

#ifdef USB_GADGET_HS_ONLY
	con_str("USB2", "GAP: gadget built HS-only (--usb2)");	/* rule 8 */
#endif
	if (!hooks || !hooks->ticks || !hooks->ticks_freq) {
		con_str("USB", "gadget: no time hooks");
		return -1;
	}
	if (pool_len < USBG_POOL_MIN || (pool_pa & 0xFFFu) != 0) {
		/* The caller validates the pool against the FDT; this is
		 * only the size/alignment re-check. */
		con_str("USB", "gadget: bad DMA pool");
		return -1;
	}

	/* Bring-up order per the stage-2 task (b) = the 0071 patch's
	 * sweet_usb_gadget_start, minus everything Qualcomm-specific. */

	/* 0. Refuse unless the controller is in device role (read only; Linux
	 * skips the soft reset in host role, core.c:332-333). */
	if (r32(USBG_GCTL, &v))
		return -1;
	if (((v >> 12) & 3u) != USBG_PRTCAP_DEVICE) {
		con_str("USB", "gadget: core not in device mode");
		return -1;
	}

#ifdef USB_GADGET_SS
	return hw_start(1);
#else
	return hw_start(0);
#endif
}

void usbgadget_poll(void)
{
	uint32_t count;
	unsigned consumed = 0;
#ifdef USB_GADGET_SS
	unsigned skipped = 0;
#endif

	if (!g.inited || g.fault)
		return;
	if (r32(USBG_GEVNTCOUNT, &count))
		return;
	count &= 0xffffu;	/* 0071 patch */
	while (count >= 4 && consumed < 16) {
		const volatile uint32_t *ev = (const volatile uint32_t *)
			(g.pool + USBG_UD_EVT + g.evt_pos);
#ifdef USB_GADGET_SS
		if (g.ss_restart_req)	/* speed change: old session is dead */
			skipped++;
		else
#endif
		if (handle_event(*ev))
			return;	/* faulted: stay unacked, dead */
		g.evt_pos = (g.evt_pos + 4) & (USBG_UD_EVT_SIZE - 1); /* 0071 patch */
		consumed++;
		count -= 4;
	}
	if (consumed)
		w32(USBG_GEVNTCOUNT, consumed * 4);	/* 0071 patch: ack */
#ifdef USB_GADGET_SS
	if (skipped)
		con_dec_l("USB3", "GAP: batch skipped: ", skipped);
#endif
#ifdef USB_GADGET_SS
	/* After the ack (the event is consumed): HS fallback, at most once. */
	if (g.ss_mode && !g.ss_restarted && !g.fault) {
		if (!g.ss_restart_req && !g.ss_confirmed &&
		    timed_out(g.ss_deadline)) {
			g.ss_restart_req = 1;
			_Static_assert(USBG_SS_WAIT_MS == 10000u, "update the ss_reason text");
			g.ss_reason = "not configured in 10000 ms";
		}
		if (g.ss_restart_req)
			ss_restart_hs();
	}
#endif
}

size_t usbgadget_write(const char *buf, size_t len)
{
	volatile uint8_t *d = (volatile uint8_t *)(g.pool + USBG_UD_BULK_DATA);
	size_t i;

	if (!g.inited || g.fault || g.bulk_busy || !len)
		return 0;
	if (!usbgadget_ready())
		return 0;
	if (len > USBG_BULK_MAX)
		len = USBG_BULK_MAX;
	for (i = 0; i < len; i++)
		d[i] = buf[i];
	if (start_bulk_trb((uint32_t)len))
		return 0;	/* faulted */
	g.bulk_busy = 1;
	g.bulk_trb = (g.bulk_trb + 1) & (USBG_N_BULK_TRB - 1);
	return len;
}

int usbgadget_ready(void)
{
	return g.inited && !g.fault && g.proto.configured != 0 &&
	       g.proto.dtr != 0;
}
