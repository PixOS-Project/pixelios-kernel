/*
 * usbprobe.c - stage-1 STRICTLY READ-ONLY DWC3 USB probe for the
 * bluejay shim (stage 1).
 *
 * One boot answers: is the gs101 USB controller still alive
 * after ABL's fastboot-boot handoff, which IP family is it, did ABL
 * leave the device running/halted/at which speed, and where did ABL
 * put the event buffer (the DMA region stage 2 may reuse)?
 *
 * Register semantics follow the Linux dwc3 driver
 * (drivers/usb/dwc3/core.h, fetched 2026-10-20): GSNPSID families
 * 0x5533 (dwc3) / 0x3331 (dwc31) / 0x3332 (dwc32) / 0x3430 (dwc4),
 * GCTL.PRTCAP bits 13:12, DCFG.DEVADDR bits 9:3, DCTL RUN_STOP bit 31
 * and CSFTRST bit 30, DSTS CONNECTSPD bits 2:0 (0 HS, 1 FS, 2/3 low/
 * full-speed variants, 4 SS, 5 SSP per the driver's speed macros) and
 * DEVCTRLHLT bit 22, GEVNTSIZ size in bytes in bits 15:0
 * (DWC3_GEVNTSIZ_SIZE(evt->length) writes the DMA byte count),
 * GEVNTCOUNT count bits 15:2 masked DWC3_GEVNTCOUNT_MASK 0xfffc.
 * The physical endpoint command blocks live at +0xC800 + n*0x10.
 *
 * Safety: every register access goes through try_read32(), whose
 * probe_guard skip in exc_handler turns a bus fault on a gated clock/
 * power domain into a printed "unreadable" instead of a crash. This
 * file performs no MMIO stores of any kind - tools/test-bluejay-boot.py
 * (check_usb_safety) rejects store spellings in it, and the object
 * audit lists every str/stp as stack-only (there is no writable static
 * state here at all).
 *
 * Host-tested in shim/usbprobe_host_test.c against a fake register
 * window: report formatting, the unreadable path, and the
 * skip-when-GSNPSID-unreadable path.
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "pstore.h"
#include "ramlog.h"
#include "usbprobe.h"

/* Linked image bounds (shim.ld); the event-buffer overlap flag compares
 * against the same range pstore_init uses. */
/* Linker-script symbols: _head = start.S:4 label (ENTRY, shim.ld:1), _end = shim.ld:19; names fixed by start.S/shim.ld/build.sh, so no rename. */
extern char _head[], _end[]; // NOLINT(bugprone-reserved-identifier)

/* Result of one fault-guarded register read. */
struct usb_reg {
	int ok;
	uint32_t v;
};

static struct usb_reg usb_read(uintptr_t addr)
{
	struct usb_reg r;
	r.ok = try_read32(addr, &r.v);
	if (!r.ok)
		r.v = 0;
	return r;
}

static struct usb_reg usb_read_off(uint32_t off)
{
	return usb_read(USBPROBE_DWC3_BASE + off);
}

/* ---- line building (same pattern as pstore.c's status lines) ---- */

static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++)
		dst[pos++] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	return pos;
}

static unsigned put_dec(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char tmp[10];
	unsigned n = 0;
	do {
		tmp[n++] = (char)('0' + v % 10);
		v /= 10;
	} while (v && n < sizeof(tmp));
	while (n && pos + 1 < cap)
		dst[pos++] = tmp[--n];
	return pos;
}

/* Append "key=HEX8" or "key=unreadable" for a register read result. */
static unsigned put_reg(char *dst, unsigned cap, unsigned pos, const char *key,
			const struct usb_reg *r)
{
	pos = put_txt(dst, cap, pos, key);
	pos = put_txt(dst, cap, pos, "=");
	if (!r->ok)
		return put_txt(dst, cap, pos, "unreadable");
	return put_hex(dst, cap, pos, r->v, 8);
}

static void emit(const char *label, char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str(label, buf);
}

/* con_str draws the value at column LABEL_COLS (8), so every label in
 * this file is "USB" and the register name leads the VALUE instead:
 * on screen "USB GEVNTADR" and "USB GEVNTCOUNT" stay distinguishable
 * (review-1 L1), while ramlog/pstore/UART still read the full
 * "USB <REG> ..." line. */

static unsigned put_bit(char *dst, unsigned cap, unsigned pos, const char *key,
			const struct usb_reg *r, uint32_t bit)
{
	pos = put_txt(dst, cap, pos, key);
	pos = put_txt(dst, cap, pos, "=");
	return put_dec(dst, cap, pos, (r->v >> bit) & 1u);
}

/* ---- range helpers (same semantics as pstore.c's) ---- */

static int range_overlaps(uint64_t b1, uint64_t s1, uint64_t b2, uint64_t s2)
{
	if (!s1 || !s2)
		return 0;
	return b1 < b2 + s2 && b2 < b1 + s1;
}

static int range_covers(uint64_t inner_b, uint64_t inner_s, uint64_t outer_b, uint64_t outer_s)
{
	if (!inner_s)
		return 0;
	return inner_b >= outer_b && inner_s <= outer_s && inner_b + inner_s <= outer_b + outer_s;
}

/* if/else chains, not switch: a dense case range makes GCC emit a
 * jump table, whose entries are ABS64 relocations the build rejects. */
static const char *connect_speed_name(uint32_t s)
{
	if (s == 0)
		return "(HS)";
	else if (s == 1 || s == 3)	/* 1 and 3 are both full speed (header comment); the raw value is printed by the caller */
		return "(FS)";
	else if (s == 2)
		return "(LS)";
	else if (s == 4)
		return "(SS)";
	else if (s == 5)
		return "(SSP)";
	return "(?)";
}

static const char *gsnpsid_family(uint32_t gsnpsid)
{
	uint32_t ip = (gsnpsid >> 16) & 0xffffu;
	if (ip == 0x5533u)
		return "dwc3 0x5533 family";
	else if (ip == 0x3331u)
		return "dwc31 0x3331 family";
	else if (ip == 0x3332u)
		return "dwc32 0x3332 family";
	else if (ip == 0x3430u)
		return "dwc4 0x3430 family";
	return "UNKNOWN family (expected dwc3 0x5533)";
}

void usbprobe_report(uintptr_t fdt)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p;

	/* Core identity: unreadable GSNPSID means the core's register
	 * block does not answer (clock/power gated), so skip everything. */
	struct usb_reg gsnpsid = usb_read_off(USBPROBE_GSNPSID_OFF);
	if (!gsnpsid.ok) {
		con_str("USB", "GSNPSID unreadable");
		con_str("USB", "probe skipped: core not responding");
		return;
	}
	p = 0;
	p = put_txt(line, sizeof(line), p, "GSNPSID ");
	p = put_reg(line, sizeof(line), p, "value", &gsnpsid);
	p = put_txt(line, sizeof(line), p, " ip=");
	p = put_hex(line, sizeof(line), p, (gsnpsid.v >> 16) & 0xffffu, 4);
	p = put_txt(line, sizeof(line), p, " ");
	p = put_txt(line, sizeof(line), p, gsnpsid_family(gsnpsid.v));
	emit("USB", line, p);

	struct usb_reg gctl = usb_read_off(USBPROBE_GCTL_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "GCTL ");
	p = put_reg(line, sizeof(line), p, "raw", &gctl);
	if (gctl.ok) {
		uint32_t prtcap = (gctl.v >> 12) & 3u;
		p = put_txt(line, sizeof(line), p, " prtcap=");
		p = put_dec(line, sizeof(line), p, prtcap);
		p = put_txt(line, sizeof(line), p,
			    prtcap == 1 ? "(host)" : prtcap == 2 ? "(device)" :
			    prtcap == 3 ? "(otg)" : "(?)");
	}
	emit("USB", line, p);

	struct usb_reg dcfg = usb_read_off(USBPROBE_DCFG_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "DCFG ");
	p = put_reg(line, sizeof(line), p, "raw", &dcfg);
	if (dcfg.ok) {
		p = put_txt(line, sizeof(line), p, " devaddr=");
		p = put_hex(line, sizeof(line), p, (dcfg.v >> 3) & 0x7fu, 2);
	}
	emit("USB", line, p);

	struct usb_reg dctl = usb_read_off(USBPROBE_DCTL_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "DCTL ");
	p = put_reg(line, sizeof(line), p, "raw", &dctl);
	if (dctl.ok) {
		p = put_bit(line, sizeof(line), p, " run_stop", &dctl, 31);
		p = put_bit(line, sizeof(line), p, " csftrst", &dctl, 30);
	}
	emit("USB", line, p);

	struct usb_reg dsts = usb_read_off(USBPROBE_DSTS_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "DSTS ");
	p = put_reg(line, sizeof(line), p, "raw", &dsts);
	if (dsts.ok) {
		p = put_txt(line, sizeof(line), p, " speed=");
		p = put_dec(line, sizeof(line), p, dsts.v & 7u);
		p = put_txt(line, sizeof(line), p, connect_speed_name(dsts.v & 7u));
		p = put_bit(line, sizeof(line), p, " halted", &dsts, 22);
	}
	emit("USB", line, p);

	struct usb_reg dalepena = usb_read_off(USBPROBE_DALEPENA_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "DALEPENA ");
	p = put_reg(line, sizeof(line), p, "raw", &dalepena);
	if (dalepena.ok) {
		p = put_bit(line, sizeof(line), p, " ep0out", &dalepena, 0);
		p = put_bit(line, sizeof(line), p, " ep0in", &dalepena, 1);
	}
	emit("USB", line, p);

	struct usb_reg evlo = usb_read_off(USBPROBE_GEVNTADRLO_OFF);
	struct usb_reg evhi = usb_read_off(USBPROBE_GEVNTADRHI_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "GEVNTADR ");
	p = put_reg(line, sizeof(line), p, "lo", &evlo);
	p = put_reg(line, sizeof(line), p, " hi", &evhi);
	emit("USB", line, p);

	struct usb_reg evsiz = usb_read_off(USBPROBE_GEVNTSIZ_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "GEVNTSIZ ");
	p = put_reg(line, sizeof(line), p, "raw", &evsiz);
	if (evsiz.ok) {
		p = put_txt(line, sizeof(line), p, " bytes=");
		p = put_dec(line, sizeof(line), p, evsiz.v & 0xffffu);
		p = put_bit(line, sizeof(line), p, " intmask", &evsiz, 31);
	}
	emit("USB", line, p);

	struct usb_reg evcnt = usb_read_off(USBPROBE_GEVNTCOUNT_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "GEVNTCOUNT ");
	p = put_reg(line, sizeof(line), p, "raw", &evcnt);
	if (evcnt.ok) {
		p = put_txt(line, sizeof(line), p, " count=");
		p = put_dec(line, sizeof(line), p, evcnt.v & 0xfffcu);
		p = put_bit(line, sizeof(line), p, " ehb", &evcnt, 31);
	}
	emit("USB", line, p);

	struct usb_reg p2cfg = usb_read_off(USBPROBE_GUSB2PHYCFG0_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "GUSB2PHYCFG0 ");
	p = put_reg(line, sizeof(line), p, "raw", &p2cfg);
	if (p2cfg.ok) {
		p = put_bit(line, sizeof(line), p, " susphy", &p2cfg, 6);
		p = put_bit(line, sizeof(line), p, " softrst", &p2cfg, 31);
	}
	emit("USB", line, p);

	struct usb_reg p3ctl = usb_read_off(USBPROBE_GUSB3PIPECTL0_OFF);
	p = 0;
	p = put_txt(line, sizeof(line), p, "GUSB3PIPECTL0 ");
	p = put_reg(line, sizeof(line), p, "raw", &p3ctl);
	if (p3ctl.ok) {
		p = put_bit(line, sizeof(line), p, " susphy", &p3ctl, 17);
		p = put_bit(line, sizeof(line), p, " softrst", &p3ctl, 31);
	}
	emit("USB", line, p);

	/* ep0 out/in endpoint command state: DEPCMD is read only (issuing
	 * a command would be a register write); DEPCMDPAR0 holds the TRB
	 * pointer of a started transfer - the address stage 2 wants. */
	for (unsigned ep = 0; ep < 2; ep++) {
		const char *tag = ep == 0 ? "EP0OUT " : "EP0IN ";
		struct usb_reg cmd = usb_read_off(USBPROBE_DEPCMD_OFF(ep));
		struct usb_reg par0 = usb_read_off(USBPROBE_DEPCMDPAR0_OFF(ep));
		struct usb_reg par1 = usb_read_off(USBPROBE_DEPCMDPAR1_OFF(ep));
		struct usb_reg par2 = usb_read_off(USBPROBE_DEPCMDPAR2_OFF(ep));

		p = 0;
		p = put_txt(line, sizeof(line), p, tag);
		p = put_reg(line, sizeof(line), p, "cmd", &cmd);
		p = put_reg(line, sizeof(line), p, " par0", &par0);
		emit("USB", line, p);

		p = 0;
		p = put_txt(line, sizeof(line), p, tag);
		p = put_reg(line, sizeof(line), p, "par1", &par1);
		p = put_reg(line, sizeof(line), p, " par2", &par2);
		emit("USB", line, p);
	}

	/* USB2 PHY common register bank: first 0x40 bytes as words. */
	for (unsigned row = 0; row < USBPROBE_PHY_DUMP_WORDS / 4; row++) {
		p = 0;
		p = put_txt(line, sizeof(line), p, "PHY+");
		p = put_hex(line, sizeof(line), p, (uint64_t)row * 16u, 2);
		p = put_txt(line, sizeof(line), p, " ");

		for (unsigned col = 0; col < 4; col++) {
			struct usb_reg w = usb_read(USBPROBE_PHY_BASE +
						    (uintptr_t)(row * 4 + col) * 4u);
			if (col)
				p = put_txt(line, sizeof(line), p, " ");
			if (w.ok)
				p = put_hex(line, sizeof(line), p, w.v, 8);
			else
				p = put_txt(line, sizeof(line), p, "unreadable");
		}
		emit("USB", line, p);
	}

	struct usb_reg pmu20 = usb_read(USBPROBE_PMU_PHY_USB20);
	struct usb_reg pmudp = usb_read(USBPROBE_PMU_PHY_USBDP);
	p = 0;
	p = put_txt(line, sizeof(line), p, "PMU PHY ");
	p = put_reg(line, sizeof(line), p, "usb20", &pmu20);
	p = put_reg(line, sizeof(line), p, " usbdp", &pmudp);
	emit("USB", line, p);

	/* Event-buffer range flags: where does GEVNTADR point, and is that
	 * inside an FDT reserved range or inside the shim image? */
	if (!evlo.ok || !evhi.ok || !evsiz.ok) {
		con_str("USB", "EVBUF GEVNTADR/SIZ unreadable; no range flags");
		return;
	}
	uint64_t evaddr = (uint64_t)evhi.v << 32 | evlo.v;
	uint32_t evbytes = evsiz.v & 0xffffu;
	if (!evbytes) {
		con_str("USB", "EVBUF size 0: no event buffer programmed");
		return;
	}
	if (!evaddr) {
		con_str("USB", "EVBUF addr 0: no event buffer programmed");
		return;
	}
	if (evaddr + evbytes < evaddr) {
		con_str("USB", "EVBUF range wraps; no range flags");
		return;
	}
	p = 0;
	p = put_txt(line, sizeof(line), p, "EVBUF addr=");
	p = put_hex(line, sizeof(line), p, evaddr, 16);
	p = put_txt(line, sizeof(line), p, " end=");
	p = put_hex(line, sizeof(line), p, evaddr + evbytes, 16);
	p = put_txt(line, sizeof(line), p, " (");
	p = put_dec(line, sizeof(line), p, evbytes);
	p = put_txt(line, sizeof(line), p, "B)");
	emit("USB", line, p);

	struct fdt_memmap mm;
	if (!fdt_collect_memmap(fdt, &mm)) {
		con_str("USB", "EVBUF FDT memmap walk failed; no reserved flag");
	} else {
		if (mm.n_reserved_dropped) {
			p = 0;
			p = put_txt(line, sizeof(line), p, "EVBUF reserved table truncated +");
			p = put_dec(line, sizeof(line), p, mm.n_reserved_dropped);
			p = put_txt(line, sizeof(line), p, "; flag unreliable");
			emit("USB", line, p);
		}
		int containing = -1;
		for (unsigned i = 0; i < mm.n_reserved; i++) {
			if (range_covers(evaddr, evbytes, mm.reserved[i].base,
					 mm.reserved[i].size)) {
				containing = (int)i;
				break;
			}
		}
		if (containing < 0) {
			con_str("USB", "EVBUF not inside any FDT reserved range");
		} else {
			p = 0;
			p = put_txt(line, sizeof(line), p, "EVBUF in reserved ");
			p = put_txt(line, sizeof(line), p, mm.reserved[containing].name);
			emit("USB", line, p);
		}
	}

	uint64_t img_lo = (uint64_t)(uintptr_t)_head;
	uint64_t img_hi = (uint64_t)(uintptr_t)_end;
	if (img_hi > img_lo && range_overlaps(evaddr, evbytes, img_lo, img_hi - img_lo))
		con_str("USB", "EVBUF OVERLAPS the shim image");
	else
		con_str("USB", "EVBUF clear of the shim image");

	/* Same check against the pstore/ramoops console zone: if ABL left
	 * RUN_STOP=1 the controller can keep DMA-writing events while the
	 * shim runs, and a live buffer inside the zone would corrupt
	 * exactly the log being pulled (review-1 L2). */
	uint64_t rz_base = pstore_zone_addr();
	uint64_t rz_size = pstore_zone_size();
	if (!rz_size)
		con_str("USB", "EVBUF ramoops zone off; no flag");
	else if (range_overlaps(evaddr, evbytes, rz_base, rz_size))
		con_str("USB", "EVBUF OVERLAPS ramoops zone");
	else
		con_str("USB", "EVBUF clear of ramoops zone");
}
