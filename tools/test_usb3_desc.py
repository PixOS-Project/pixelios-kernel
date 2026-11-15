#!/usr/bin/env python3
"""Check the SuperSpeed descriptor bytes and the default-ON / opt-out guards of xnu patch 0173 (host test, no phone, no compiler).

    tools/test_usb3_desc.py [--patch FILE]

Reads the '+' lines of xnu/patches/*-sweet-usb3-ss.patch (or --patch, so a mutated copy can be shown to FAIL) and checks:
  - ud_ss_dev_desc / ud_ss_cfg_desc / ud_ss_bos_desc: the initializer has exactly as many bytes as the declared array size
    (C silently zero-fills a short initializer), and the USB structure: bLength chains, wTotalLength, bNumInterfaces,
    bcdUSB 0x0320 + bMaxPacketSize0 9, every endpoint followed by a SuperSpeed companion, bulk 1024, interrupt companion
    wBytesPerInterval == wMaxPacketSize, BOS capability count/types, U1/U2 exit latency 0.
    Layouts: Linux v6.12 include/uapi/linux/usb/ch9.h:253-265, 693-702, 850-858, 899-939; values: composite.c:540-547,
    1833-1836, f_ecm.c:273-338 (cited in the patch).
  - default ON (user order R27; sweetusb3=0 opts out): the HS descriptors are not edited, the HS DCFG write `dwc_wr(DCFG, dcfg | 0)` is kept, ss_mode is only set
    under `usb3 == 1`, and the only registers in the SS write allowlist are DCFG and DCTL; the default (devspd HS) register
    write / endpoint-command sequence of ud_hw_init + ud_run equals, call by call, the one of the removed pre-0173 inline code.
  - review usb3-ss-r1 F2 asserts (a)-(h): ep0/bulk max packet selection, bulk == descriptor, fallback guard and target speed,
    the SS DCFG write goes through ud_ss_wr, no write to GUSB2PHYCFG0/GUSB3PIPECTL0/GTXFIFOSIZ/GHWPARAMS, BOS LPM bit 0,
    every USB3 GAP printf unconditional.
  - F3 (speed change after configuration -> restart), F4 (trigger on not configured, 10000 ms, GAP prints ep0_mps and speed,
    placed before 0172's hurry branch, 0172's lines not removed), F7 (read-only second report), F9 (sweetusb3 parsed once, default 1, GAP only for an EXPLICIT arg on a path that does not reach ud_hw_init,
    the default and opt-out lines), F10 (GAP on a failed HS restart).
  These are string/structure asserts on the patch text; they do not run the C. The write-sequence comparison is a call-list
  comparison, not a register recorder (a host-compiled harness of ud_hw_init/ud_run is still open).
Exit 0 PASS, 1 FAIL, 2 ERROR; last line RESULT:.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib, re, sys, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCH = None


def patch_text():
    if PATCH:
        return pathlib.Path(PATCH).read_text()
    found = sorted((HERE.parent / "xnu" / "patches").glob("*-sweet-usb3-ss.patch"))
    if len(found) != 1:
        raise RuntimeError("expected one *-sweet-usb3-ss.patch, found %d" % len(found))
    return found[0].read_text()


def added(text):
    return "\n".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))


def array(src, name):
    m = re.search(r"static const uint8_t %s\[(\d+)\] = \{(.*?)\};" % re.escape(name), src, re.S)
    if not m:
        raise AssertionError("array %s not found in the + lines" % name)
    body = re.sub(r"/\*.*?\*/", "", m.group(2), flags=re.S)
    vals = [int(t, 0) for t in (x.strip() for x in body.split(",")) if t]
    return int(m.group(1)), vals


def func(src, name):
    """Body text of `static ... name(...) { ... }` (brace matched) in src; AssertionError if absent."""
    m = re.search(r"^%s\([^;{]*\)\s*\{" % re.escape(name), src, re.M)
    if not m:
        raise AssertionError("function %s not found in the + lines" % name)
    i, depth = m.end(), 1
    while depth and i < len(src):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.start():i]


def calls(text, names):
    """Normalized list of `name(args)` calls (balanced parens) in text, in order."""
    out = []
    for m in re.finditer(r"\b(%s)\(" % "|".join(names), text):
        i, depth = m.end(), 1
        while depth and i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        out.append(re.sub(r"\s+", " ", text[m.start():i]))
    return out


def walk(b):
    out, i = [], 0
    while i < len(b):
        n = b[i]
        if n < 2 or i + n > len(b):
            raise AssertionError("bad bLength %d at offset %d" % (n, i))
        out.append(b[i:i + n])
        i += n
    return out


class Usb3Desc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = patch_text()
        cls.src = added(cls.text)

    def merged(self):
        """New side of the patch (context + added lines)."""
        return "\n".join(l[1:] for l in self.text.splitlines() if l[:1] in "+ " and not l.startswith("+++"))

    def sized(self, name):
        size, vals = array(self.src, name)
        self.assertEqual(size, len(vals), "%s: declared [%d] but %d initializer bytes" % (name, size, len(vals)))
        return vals

    def test_device(self):
        d = self.sized("ud_ss_dev_desc")
        self.assertEqual((d[0], d[1]), (18, 1))
        self.assertEqual(d[2] | d[3] << 8, 0x0320)
        self.assertEqual(d[7], 9)                      # 2^9 = 512, ep0 at SuperSpeed
        self.assertEqual(d[17], 1)                     # one configuration

    def test_config(self):
        c = self.sized("ud_ss_cfg_desc")
        ds = walk(c)
        self.assertEqual(ds[0][1], 2)
        self.assertEqual(ds[0][2] | ds[0][3] << 8, len(c), "wTotalLength")
        self.assertEqual(ds[0][8], 500 // 8, "bMaxPower in 8 mA units")
        ifaces = {d[2] for d in ds if d[1] == 4}
        self.assertEqual(len(ifaces), ds[0][4], "bNumInterfaces")
        eps = 0
        for k, d in enumerate(ds):
            if d[1] != 5:
                continue
            eps += 1
            self.assertEqual(len(d), 7)
            self.assertLess(k + 1, len(ds), "endpoint at the end without companion")
            comp = ds[k + 1]
            self.assertEqual((comp[0], comp[1]), (6, 0x30), "endpoint 0x%02x needs a SuperSpeed companion" % d[2])
            mps = d[4] | d[5] << 8
            if d[3] & 3 == 2:
                self.assertEqual(mps, 1024, "bulk 0x%02x" % d[2])
                self.assertEqual(comp[2], 0, "bMaxBurst 0 (DEPCFG burst field 0)")
            elif d[3] & 3 == 3:
                self.assertEqual(comp[4] | comp[5] << 8, mps, "interrupt wBytesPerInterval")
        self.assertEqual(eps, 3)
        # each interface's bNumEndpoints matches the endpoints that follow it
        cur = None
        counts = {}
        for d in ds:
            if d[1] == 4:
                cur = (d[2], d[3]); counts[cur] = [d[4], 0]
            elif d[1] == 5:
                counts[cur][1] += 1
        for key, (want, got) in counts.items():
            self.assertEqual(want, got, "interface %s bNumEndpoints" % (key,))

    def test_bos(self):
        b = self.sized("ud_ss_bos_desc")
        ds = walk(b)
        self.assertEqual((ds[0][0], ds[0][1]), (5, 0x0f))
        self.assertEqual(ds[0][2] | ds[0][3] << 8, len(b), "wTotalLength")
        caps = ds[1:]
        self.assertEqual(ds[0][4], len(caps), "bNumDeviceCaps")
        self.assertEqual([(c[1], c[2]) for c in caps], [(0x10, 2), (0x10, 3)])
        self.assertEqual(len(caps[0]), 7)
        ss = caps[1]
        self.assertEqual(len(ss), 10)
        self.assertEqual(ss[4] | ss[5] << 8, 0x000f)
        self.assertEqual((ss[7], ss[8] | ss[9] << 8), (0, 0), "U1/U2 exit latency 0 (host turns LPM off)")

    def test_default_off_guards(self):
        minus = "\n".join(l for l in self.text.splitlines() if l.startswith("-") and not l.startswith("---"))
        for name in ("ud_dev_desc[18] = {", "ud_cfg_desc[80] = {", "0x81, 0x02, 0x00, 0x02", "0x01, 0x02, 0x00, 0x02"):
            self.assertNotIn(name, minus, "HS descriptor bytes must stay untouched: %s" % name)
        self.assertIn("dwc_wr(DCFG, dcfg | 0);", self.src, "HS DCFG write must stay")
        sets = re.findall(r"^\s*ud\.ss_mode = true;.*$", self.src, re.M)
        self.assertEqual(len(sets), 1, "ss_mode set exactly once")
        m = re.search(r"if \(usb3 == 1\) \{\s*ud\.ss_req = true;\s*ud\.ss_mode = true;", self.src)
        self.assertIsNotNone(m, "ss_mode only under usb3 == 1")
        allow = re.search(r"ud_ss_wr_allow\[\] = \{([^}]*)\}", self.src)
        self.assertIsNotNone(allow)
        self.assertEqual(sorted(x.strip() for x in allow.group(1).split(",") if x.strip()), ["DCFG", "DCTL"])
        self.assertNotIn("SWEET MMIO", self.text, "AP-138")

    # ---- review usb3-ss-r1 F2 asserts (a)-(h): one per mutant M1-M9 (see the commit message) ----
    def test_f2_default_selection(self):                                     # (a) kills M4
        self.assertIn("uint32_t mps = (ud.ss_mode && speed >= DSTS_SPEED_SS) ? UD_SS_EP0_MPS : 64u;", self.src)
        self.assertIn("ud.speed = ud.ss_mode ? speed : 0;", self.src)

    def test_f2_bulk_selection(self):                                        # (b) kills M6
        self.assertIn("uint32_t bulk = ud.speed >= DSTS_SPEED_SS ? UD_SS_BULK_MPS : 512u;", self.src)

    def test_f2_macros_equal_descriptors(self):                              # (c) kills M1
        bulk = re.search(r"#define UD_SS_BULK_MPS\s+(\d+)", self.src)
        ep0 = re.search(r"#define UD_SS_EP0_MPS\s+(\d+)", self.src)
        self.assertTrue(bulk and ep0)
        ds = walk(self.sized("ud_ss_cfg_desc"))
        mpss = {d[4] | d[5] << 8 for d in ds if d[1] == 5 and d[3] & 3 == 2}
        self.assertEqual(mpss, {int(bulk.group(1))}, "UD_SS_BULK_MPS vs bulk descriptor wMaxPacketSize")
        self.assertEqual(1 << self.sized("ud_ss_dev_desc")[7], int(ep0.group(1)), "UD_SS_EP0_MPS vs bMaxPacketSize0")

    def test_f2_fallback_guard_and_target(self):                             # (d) kills M3, M5
        self.assertRegex(self.src, r"if \(ud\.ss_mode && !ud\.ss_fell_back && !ud\.configured && mach_absolute_time\(\) > ud\.ss_deadline\) \{\s*ud_ss_fallback\(\);")
        fb = func(self.src, "ud_ss_fallback")
        self.assertIn("ud.ss_fell_back = true;", fb)
        self.assertIn("ud_restart(DCFG_SPEED_HS);", fb)
        self.assertNotIn("DCFG_SPEED_SS", fb, "fallback must not restart at SuperSpeed")
        self.assertIn("ud_hw_init(devspd)", func(self.src, "ud_restart"))
        self.assertEqual(self.src.count("ud_restart(DCFG_SPEED_HS)"), 2, "fallback + speed-change restart, both at HS")

    def test_f2_dcfg_write_via_allowlist(self):                              # (e) kills M2
        hw = func(self.src, "ud_hw_init")
        m = re.search(r"if \(devspd != DCFG_SPEED_HS\) \{(.*?)\n\t\} else \{", hw, re.S)
        self.assertIsNotNone(m, "SS branch of ud_hw_init")
        self.assertIn("ud_ss_wr(DCFG,", m.group(1))
        self.assertNotRegex(m.group(1), r"\bdwc_wr\(")

    def test_f2_no_phy_fifo_writes(self):                                    # (f) kills M7
        bad = r"(?:dwc_wr|ud_ss_wr|usb_wr)\(\s*(?:GUSB2PHYCFG0|GUSB3PIPECTL0|GTXFIFOSIZ|GHWPARAMS|0x(?:c200|c2c0|c140|c15c|c3[0-9a-f]{2}))\b"
        self.assertNotRegex(self.src, bad)
        minus = "\n".join(l[1:] for l in self.text.splitlines() if l.startswith("-") and not l.startswith("---"))
        old_regs = {c.split("(")[1].split(",")[0].strip() for c in calls(minus, ["dwc_wr"])}
        new_regs = {c.split("(")[1].split(",")[0].strip() for c in calls(self.src, ["dwc_wr", "ud_ss_wr"])}
        new_regs = {r for r in new_regs if re.fullmatch(r"[A-Z][A-Z0-9_]*", r)}     # the allowlist helper's own dwc_wr(off, v) is a variable
        self.assertLessEqual(new_regs, old_regs | {"DCFG", "DCTL"}, "new register written: %s" % sorted(new_regs - old_regs))

    def test_f2_bos_no_lpm(self):                                            # (g) kills M8
        caps = walk(self.sized("ud_ss_bos_desc"))[1:]
        self.assertEqual(list(caps[0][3:7]), [0, 0, 0, 0], "USB 2.0 extension bmAttributes (LPM bit 1)")

    def test_f2_gap_printfs_unconditional(self):                             # (h) kills M9
        lines = self.src.splitlines()
        n = 0
        for k, l in enumerate(lines):
            if '"USB3 GAP:' not in l or l.lstrip().startswith(("#", "/*", "*", "//")):
                continue
            n += 1
            self.assertRegex(l, r'^\s*printf\("USB3 GAP:', "GAP printf must start its statement: %r" % l)
            prev = next(x for x in reversed(lines[:k]) if x.strip())
            self.assertFalse(re.search(r"\)\s*$", prev) and not prev.rstrip().endswith("{"), "single-statement if before: %r" % prev)
            self.assertNotRegex(prev, r"\b(?:if|else)\b.*\b(?:0|false)\b\s*\)?\s*$")
        self.assertGreaterEqual(n, 10, "USB3 GAP printf count")

    # ---- default behaviour byte-identical (default devspd HS): same call sequence as the removed pre-0173 code ----
    def test_default_write_sequence_equals_pre_0173(self):
        hunks = [h for h in self.text.split("\n@@") if "-\tdwc_wr(DCTL" in h]    # the removed inline start-up code (2 hunks)
        self.assertEqual(len(hunks), 2)
        minus = "\n".join(l[1:] for h in hunks for l in h.splitlines() if l.startswith("-"))
        old = calls(minus, ["dwc_wr", "ud_epcmd"])
        hw = func(self.src, "ud_hw_init")
        hs = re.sub(r"if \(devspd != DCFG_SPEED_HS\) \{.*?\} else \{(.*?)\n\t\}", r"\1", hw, count=1, flags=re.S)
        new = calls(hs, ["dwc_wr", "ud_epcmd"]) + calls(func(self.src, "ud_run"), ["dwc_wr", "ud_epcmd"])
        self.assertEqual(new, old)
        self.assertIn("dwc_wr(DCFG, dcfg | 0)", " ".join(new))

    # ---- F3 ----
    def test_f3_speed_change_restart(self):
        self.assertIn("ud.configured = true;\n\tud.bulk_mps = bulk;", self.merged())
        flat_src = re.sub(r"\s+", " ", self.src)
        # r2 F5: one-shot in code, and loud when it blocks a second restart
        self.assertIn("if (ud.ss_mode && !ud.ss_fell_back && ud.configured && ud.bulk_mps != (speed >= DSTS_SPEED_SS ? UD_SS_BULK_MPS : 512u)) {", flat_src)
        self.assertIn("} else if (ud.ss_mode && ud.ss_fell_back && ud.configured && ud.bulk_mps != (speed >= DSTS_SPEED_SS ? UD_SS_BULK_MPS : 512u)) { printf(\"USB3 GAP: speed changed after the high-speed fallback (bulk %u, speed %u): not restarted again\\n\",", flat_src)
        self.assertIn('printf("USB3 GAP: speed changed after configuration (bulk %u -> %u): restarting the gadget\\n",', self.src)
        r = func(self.src, "ud_restart")
        for want in ("ud_ss_wr(DCTL,", "ud_hw_init(devspd)", "ud_run();", "ud.configured = false;", "ud.data_alt = 0;", "ud.speed = 0;"):
            self.assertIn(want, r)
        th = self.merged()
        # r2 F4: the whole restart block in order (deleting `ud.ss_restart = false;` = a soft reset on every loop pass)
        self.assertRegex(th, r"if \(ud\.ss_restart\) \{\s*/\*[^*]*\*/\s*ud\.ss_restart = false;\s*ud\.ss_fell_back = true;\s*ud_restart\(DCFG_SPEED_HS\);\s*continue;\s*\}")
        # r2 F1/F6: TX stopped under the lock and link-down posted BEFORE the RUN_STOP=0 write
        stop = r.index("ud_ss_wr(DCTL,")
        lk = r.index("lck_mtx_lock(ud.lock);")
        for want in ("ud.configured = false;", "ud.data_alt = 0;", "ud.tx_busy = false;", "lck_mtx_unlock(ud.lock);", "ud_link_event(false);"):
            self.assertTrue(lk < r.index(want) < stop, "%s must sit between lck_mtx_lock and the RUN_STOP=0 write" % want)
        self.assertIn('printf("USB3 GAP: restart drops the TX transfer in flight\\n");', r)
        # r2 F2: events acked (not dispatched) inside the halt loop, counted loudly; F2b loud
        loop = r[r.index("for (i = 0; i < 1000 && !(dwc_rd(DSTS) & DSTS_DEVCTRLHLT); i++) {"):]
        loop = loop[:loop.index("delay(1000);")]
        for want in ("dwc_rd(GEVNTCOUNT) & 0xffff", "dwc_wr(GEVNTCOUNT, c);", "ud.evt_pos = (ud.evt_pos + c) % UD_EVT_SIZE;", "dropped += c / 4;"):
            self.assertIn(want, loop)
        self.assertIn('printf("USB3 GAP: %u events dropped while stopping\\n", dropped);', r)
        self.assertIn('printf("USB3 GAP: no EndTransfer before RUN_STOP=0', r)
        # r2 F3: events after the speed-change ConnectDone are skipped and counted
        self.assertRegex(th, r"if \(ud\.ss_restart\) \{\s*skipped\+\+;[^}]*continue;\s*\}\s*ud_event\(ev\);")
        self.assertIn('printf("USB3 GAP: %u events after the speed change not handled (restart)\\n", skipped);', th)

    # ---- F4 ----
    def test_f4_trigger_and_thread_order(self):
        self.assertRegex(self.src, r"#define UD_SS_WAIT_MS\s+10000\b")
        fb = func(self.src, "ud_ss_fallback")
        flat = re.sub(r"\s+", " ", fb)
        self.assertIn('ud.ep0_mps ? "seen" : "not seen", ud.speed, ud.ep0_mps', flat)
        th = self.merged()
        t = th[th.index("if (ud.ss_restart) {"):]
        self.assertLess(t.index("ud_ss_fallback();"), t.index("if (ud_rx_fast && ud_rx_hurry) {"),
                        "fallback check must sit BEFORE 0172's hurry branch")
        self.assertLess(t.index("ud_restart(DCFG_SPEED_HS);"), t.index("if (ud_rx_fast && ud_rx_hurry) {"))
        self.assertIn("ud_rx_fast_wait();      /* short poll", t)
        minus = "\n".join(l for l in self.text.splitlines() if l.startswith("-") and not l.startswith("---"))
        for gone in ("ud_sleep_ms", "ud_rx_fast_wait", "continue;", "ud_rx_hurry"):
            self.assertNotIn(gone, minus, "0173 must not remove 0172's ud_thread line: %s" % gone)

    # ---- F10 ----
    def test_f10_failed_restart_gap(self):
        r = func(self.src, "ud_restart")
        m = re.search(r"if \(!ud_hw_init\(devspd\)\) \{\s*(printf\(\"USB3 GAP: high-speed restart failed: USB gadget stopped \(no USB log\)\\n\"\);)\s*return false;", r)
        self.assertIsNotNone(m)

    # ---- F9 ----
    def test_f9_sweetusb3_parsed_once_and_reported(self):
        self.assertEqual(self.merged().count('PE_parse_boot_argn("sweetusb3"'), 1)
        w = func(self.merged(), "sweet_usb_probe")
        self.assertIn('bool usb3_explicit = PE_parse_boot_argn("sweetusb3", &usb3, sizeof(usb3));', w)
        # R27: GAP only when the arg was given explicitly (the default must not make GAP noise on sweetusb=0/1 or non-gs101)
        # F2 (review usb3-default-r1): the GAP fires when SuperSpeed was requested (default included, ud.ss_req) or the arg was explicit and nonzero
        self.assertRegex(w, r'if \(\(ud\.ss_req \|\| \(usb3_explicit && usb3 != 0\)\) && !ud\.hw_started\) \{\s*printf\("USB3 GAP: sweetusb3=%u ignored: gadget not started%s\\n", usb3, ')
        self.assertRegex(self.src, r"ud\.hw_started = true;\s*if \(!ud_hw_init\(ud\.ss_mode")
        self.assertEqual(self.merged().count("ud.hw_started = true;"), 1)

    def test_f9_default_on(self):                                            # R27: SuperSpeed is the default
        w = func(self.merged(), "sweet_usb_probe")
        self.assertRegex(w, r"uint32_t usb3 = 1;")
        self.assertNotRegex(w, r"uint32_t usb3 = 0;")
        g = func(self.merged(), "sweet_usb_probe_gs101")
        self.assertIn('printf("SWEET USB3: default ON (sweetusb3=0 opts out)\\n");', g)
        # the default line is only for the non-explicit case, printed once (one printf in the whole patch)
        self.assertRegex(g, r"if \(usb3_explicit\) \{[^}]*explicit[^}]*\} else \{\s*printf\(\"SWEET USB3: default ON")
        self.assertEqual(self.src.count("SWEET USB3: default ON"), 1)

    def test_f9_explicit_zero_opt_out_line(self):                            # R27: loud, explicit-only (the default is 1)
        g = func(self.merged(), "sweet_usb_probe_gs101")
        self.assertRegex(g, r'\} else if \(usb3 == 0\) \{\s*printf\("SWEET USB3: sweetusb3=0: opted out, HS-only gadget \(explicit\)\\n"\);')
        self.assertNotIn("ss_mode", g[g.index("else if (usb3 == 0)"):g.index("else if (usb3 == 0)") + 140])
        self.assertIn('printf("USB3 GAP: sweetusb3=%u unsupported (only 0 or 1); SuperSpeed off\\n", usb3);', g)

    def test_f9_no_gap_when_not_requested(self):                             # R27 + F2: sweetusb=0/1, non-gs101: ss_req is never set there, so no noise
        w = func(self.merged(), "sweet_usb_probe")
        self.assertEqual(w.count("USB3 GAP"), 1)
        before = w[:w.index("USB3 GAP")]
        self.assertTrue(before[before.rindex("if ("):].startswith("if ((ud.ss_req || (usb3_explicit && usb3 != 0)) && !ud.hw_started)"),
                        "the only GAP printf sits under the ss_req / explicit guard")
        self.assertEqual(self.merged().count("ud.ss_req = true;"), 1, "ss_req set exactly once")
        g = func(self.merged(), "sweet_usb_probe_gs101")
        self.assertLess(g.index("if (mode < 2) {"), g.index("ud.ss_req = true;"), "ss_req only after the sweetusb<2 / identity returns")
        self.assertRegex(g, r"if \(usb3 == 1\) \{\s*ud\.ss_req = true;")
        self.assertRegex(self.src, r"bool\s+ss_req;")

    def test_f9_explicit_one_on_unstarted_gadget_still_gaps(self):           # explicit 1 + gadget not started -> GAP; default too (F2)
        w = func(self.merged(), "sweet_usb_probe")
        self.assertIn("usb3_explicit && usb3 != 0", w)
        self.assertIn("ud.ss_req", w)
        self.assertIn("!ud.hw_started", w)
        g = func(self.merged(), "sweet_usb_probe_gs101")
        self.assertIn("SWEET USB3: sweetusb3=1: SuperSpeed requested (explicit;", g)

    def test_f2_silent_pn_return_is_loud(self):                              # review usb3-default-r1 F2: the `!pn` return had no line
        self.assertRegex(self.text, r'if \(!pn\) \{\n\+\t\t\tprintf\("USB3 GAP: SWEET USB: DMA buffer has no physical page \(pmap_find_phys 0\): gadget not started\\n"\);\n \t\t\treturn;')

    def test_f3_hardware_status_exact(self):                                 # review usb3-default-r1 F3: no claim beyond usb3-ss-1/-2
        head = self.text[:self.text.index("diff --git")]
        self.assertNotIn("Not observed on hardware yet", head)
        for need in ("EXPLICIT sweetusb3=1", "usb3-ss-1", "usb3-ss-2", "default-ON path", "HS fallback", "unobserved", "hardware run is pending"):
            self.assertIn(need, head)

    # ---- F7 ----
    def test_f7_second_report_read_only(self):
        m = self.merged()
        a = m.index("ud_ss_report(\"handover\")")
        b = m.index("ud_hw_init(ud.ss_mode")
        c = m.index("ud_ss_report(\"after reset\")")
        self.assertTrue(a < b < c)
        rep = func(self.src, "ud_ss_report")
        self.assertEqual(calls(rep, ["dwc_wr", "ud_ss_wr", "ud_epcmd", "usb_wr"]), [], "report must not write")

    # ---- F8 ----
    def test_f8_wording(self):
        self.assertIn("DCFG.DEVSPD, DCTL.RUN_STOP (fallback) and the", self.text)
        self.assertNotIn("DCFG, DCTL only", self.text)


def main():
    global PATCH
    argv = sys.argv[1:]
    if argv[:1] == ["--patch"] and len(argv) >= 2:
        PATCH = argv[1]
    elif argv:
        print(__doc__)
        print("RESULT: ERROR bad arguments")
        return 2
    try:
        patch_text()
    except (OSError, RuntimeError) as e:
        print("RESULT: ERROR %s" % e)
        return 2
    res = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(Usb3Desc))
    ok = res.wasSuccessful()
    print("RESULT: %s %d tests, %d failures, %d errors" % ("PASS" if ok else "FAIL", res.testsRun,
                                                        len(res.failures), len(res.errors)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
