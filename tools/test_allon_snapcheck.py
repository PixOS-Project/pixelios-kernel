#!/usr/bin/env python3
"""tools/allon-snapcheck on SYNTHETIC panels (PIL rectangles in the measured positions of the iOS Settings pages; no real panel, no Apple pixels):
every part of the Wi-Fi and Bluetooth proof positive and each part missing on its own, the alert on top, wrong page, half-resolution slot, header
variants, exit codes, and the allon-score wiring (human .verdict overrides the checker; no verdict = the checker decides; neither = UNMEASURED).
Calibration of the rules on 11 real panels (2026-11-12, read-only, not stored here): Wi-Fi + checkmark x4 ok; Wi-Fi without checkmark x4 bad
(one with the Weather location alert on top); Bluetooth Connected without alert x1 ok, Connected with the alert on top x1 bad, no device row x1 bad."""
import importlib.machinery, importlib.util, os, struct, subprocess, sys, tempfile, unittest

from PIL import Image, ImageDraw

TOOLS = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(TOOLS, "allon-snapcheck")
SCORE = os.path.join(TOOLS, "allon-score")
BG, WHITE, BLACK, GREY, BLUE, GREEN = (242, 242, 247), (255, 255, 255), (0, 0, 0), (142, 142, 147), (0, 122, 255), (52, 199, 89)


def page(kind, switch=True, row=True, check=True, glyph=True, sbicon=False, bt_card=True, bt_header=True, bt_status=(662, 906), dim=False, alert=False,
         title=True):
    """one 1080x2400 RGB page. kind wifi|bluetooth|home."""
    im = Image.new("RGB", (1080, 2400), BG)
    d = ImageDraw.Draw(im)
    d.rectangle((70, 35, 165, 65), fill=BLACK)                       # clock
    d.rectangle((985, 35, 1050, 65), fill=(60, 200, 90))             # battery
    if sbicon:
        d.rectangle((900, 28, 960, 62), fill=BLACK)                  # status-bar Wi-Fi icon
    if kind == "home":
        d.rectangle((100, 300, 400, 600), fill=(30, 60, 160))
    else:
        d.rectangle((66, 160, 250, 198), fill=BLUE)                  # Settings back link
        if title:
            d.rectangle((479, 159, 600, 197) if kind == "wifi" else (428, 160, 652, 198), fill=BLACK)
        if kind == "wifi":
            d.rectangle((971, 160, 1052, 198), fill=BLUE)            # Edit
        d.rectangle((26, 352, 1054, 628 if (kind == "wifi" and row) else 482), fill=WHITE)
        d.rectangle((148, 398, 264, 435) if kind == "wifi" else (79, 398, 290, 435), fill=BLACK)   # switch label
        d.rounded_rectangle((854, 371, 1006, 463), 46, fill=GREEN if switch else (233, 233, 234))
        k = 860 + (60 if switch else 0)
        d.ellipse((k, 377, k + 40, 457), fill=WHITE)   # knob
        if kind == "wifi" and row:
            d.rectangle((151, 537, 430, 580), fill=BLACK)            # network name
            d.ellipse((939, 524, 1003, 588), outline=BLUE, width=5)  # (i)
            if check:
                d.line((70, 556, 86, 576, 114, 533), fill=BLUE, width=9)
            if glyph:
                d.rectangle((798, 537, 830, 575), fill=BLACK); d.rectangle((860, 537, 906, 575), fill=BLACK)
        if kind == "wifi":
            d.rectangle((70, 770, 280, 800), fill=GREY)              # NETWORKS header
        if kind == "bluetooth":
            d.rectangle((76, 255, 800, 300), fill=GREY)              # discoverable footnote
            if bt_card:
                d.rectangle((26, 735, 1054, 866), fill=WHITE)
                d.rectangle((77, 782, 219, 819), fill=BLACK)         # device name
                if bt_status:
                    d.rectangle((bt_status[0], 783, bt_status[1], 820), fill=GREY)   # status text
                d.ellipse((940, 780, 1003, 824), outline=BLUE, width=5)
            if bt_header:
                d.rectangle((77, 677, 303, 705) if bt_card else (78, 697, 236, 724), fill=GREY)   # MY DEVICES | DEVICES
    if dim or alert:
        im = Image.blend(im, Image.new("RGB", im.size, BLACK), .2)
    if alert:
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((140, 996, 940, 1428), 40, fill=(239, 238, 238))
        d.rectangle((250, 1060, 830, 1260), fill=BLACK)
    return im


def write_raw(im, path, slot=False, magic=b"SPNL"):
    w, h = im.size
    px = im.convert("RGBA").tobytes("raw", "BGRA")
    hdr = struct.pack("<8I", struct.unpack("<I", magic)[0], w, h, w * 4, 0, 0, 1, 1)
    with open(path, "wb") as f:
        f.write(hdr + (b"snap-label".ljust(48, b"\0") if slot else b"") + px)


def _w(path, data, mode="wb"):
    with open(path, mode) as f:
        f.write(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)

    def run_snap(self, kind, im, *flags, slot=False):
        p = os.path.join(self.t.name, "p.raw")
        write_raw(im, p, slot)
        r = subprocess.run([sys.executable, SNAP, kind, p, *flags], capture_output=True, text=True)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stdout + r.stderr)
        return r.returncode, lines[0]

    def ok(self, kind, im, *a, **k):
        rc, line = self.run_snap(kind, im, *a, **k)
        self.assertEqual((rc, line.startswith("ALLON SNAP %s ok: " % kind)), (0, True), line)
        return line

    def bad(self, kind, im, *must, **k):
        rc, line = self.run_snap(kind, im, **k)
        self.assertEqual((rc, line.startswith("ALLON SNAP %s bad: " % kind)), (1, True), line)
        for m in must:
            self.assertIn(m, line)
        return line


class Wifi(Base):
    def test_positive(self):
        line = self.ok("wifi", page("wifi"))
        for part in ("title Wi-Fi", "switch ON", "joined-network row", "blue checkmark", "lock+signal glyph drawn", "status-bar Wi-Fi icon absent"):
            self.assertIn(part, line)

    def test_ring_slot_header(self):
        self.ok("wifi", page("wifi"), slot=True)

    def test_half_resolution_slot(self):
        im = page("wifi").resize((540, 1200), Image.NEAREST)
        p = os.path.join(self.t.name, "h.raw")
        write_raw(im, p, slot=True, magic=b"SPNH")
        r = subprocess.run([sys.executable, SNAP, "wifi", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_no_checkmark(self):
        line = self.bad("wifi", page("wifi", check=False), "checkmark MISSING")
        self.assertIn("seen: title Wi-Fi", line)

    def test_no_joined_row(self):
        self.bad("wifi", page("wifi", row=False), "no joined-network row")

    def test_switch_off(self):
        self.bad("wifi", page("wifi", switch=False), "switch not ON")

    def test_alert_on_top(self):
        self.bad("wifi", page("wifi", alert=True), "alert on top")

    def test_alert_and_missing_checkmark_both_named(self):
        self.bad("wifi", page("wifi", check=False, alert=True), "checkmark MISSING", "alert on top")

    def test_dimmed_without_box(self):
        self.bad("wifi", page("wifi", dim=True), "dimmed overlay on top")

    def test_wrong_page_bluetooth(self):
        self.bad("wifi", page("bluetooth"), "page is not Settings > Wi-Fi", "it is Settings > Bluetooth")

    def test_wrong_page_home(self):
        self.bad("wifi", page("home"), "page is not Settings > Wi-Fi")

    def test_no_title(self):
        self.bad("wifi", page("wifi", title=False), "page is not Settings > Wi-Fi")

    def test_statusbar_info_and_required(self):
        self.assertIn("status-bar Wi-Fi icon present", self.ok("wifi", page("wifi", sbicon=True)))
        p = os.path.join(self.t.name, "s.raw"); write_raw(page("wifi"), p)
        r = subprocess.run([sys.executable, SNAP, "wifi", p, "--need-statusbar"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("status-bar Wi-Fi icon missing", r.stdout)
        write_raw(page("wifi", sbicon=True), p)
        self.assertEqual(subprocess.run([sys.executable, SNAP, "wifi", p, "--need-statusbar"], capture_output=True, text=True).returncode, 0)


class Bluetooth(Base):
    def test_positive(self):
        line = self.ok("bluetooth", page("bluetooth"))
        for part in ("title Bluetooth", "switch ON", "MY DEVICES header", "'Connected'"):
            self.assertIn(part, line)

    def test_alert_on_top(self):
        line = self.bad("bluetooth", page("bluetooth", alert=True), "alert on top")
        self.assertIn("'Connected'", line)   # the parts are still reported

    def test_switch_off(self):
        self.bad("bluetooth", page("bluetooth", switch=False), "switch not ON")

    def test_no_device_row(self):
        self.bad("bluetooth", page("bluetooth", bt_card=False), "no MY DEVICES row")

    def test_not_connected_text(self):
        self.bad("bluetooth", page("bluetooth", bt_status=(526, 906)), "no 'Connected'")

    def test_status_text_missing(self):
        self.bad("bluetooth", page("bluetooth", bt_status=None), "no 'Connected'")

    def test_header_missing(self):
        self.bad("bluetooth", page("bluetooth", bt_header=False), "MY DEVICES header missing")

    def test_wrong_page_wifi(self):
        self.bad("bluetooth", page("wifi"), "page is not Settings > Bluetooth", "it is Settings > Wi-Fi")


class Usage(Base):
    def test_unreadable_and_usage(self):
        p = os.path.join(self.t.name, "junk.raw"); _w(p, b"junk" * 40)
        r = subprocess.run([sys.executable, SNAP, "wifi", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("ALLON SNAP wifi bad: panel unreadable"), r.stdout)
        r = subprocess.run([sys.executable, SNAP, "wifi", os.path.join(self.t.name, "absent.raw")], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.startswith("ALLON SNAP wifi bad: panel unreadable")), (1, True))
        self.assertEqual(subprocess.run([sys.executable, SNAP, "audio", p], capture_output=True, text=True).returncode, 2)
        self.assertEqual(subprocess.run([sys.executable, SNAP], capture_output=True, text=True).returncode, 2)

    def test_png(self):
        p = os.path.join(self.t.name, "p.raw"); write_raw(page("wifi"), p)
        out = os.path.join(self.t.name, "o.png")
        r = subprocess.run([sys.executable, SNAP, "wifi", p, "--png", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(Image.open(out).size, (1080, 2400))


class ScoreWiring(Base):
    def score(self):
        spec = importlib.util.spec_from_loader("allon_score_t", importlib.machinery.SourceFileLoader("allon_score_t", SCORE))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.SNAP_DIRS.append(self.t.name)
        return m

    def put(self, name, im):
        write_raw(im, os.path.join(self.t.name, name + ".raw"))

    def test_checker_decides_without_verdict(self):
        m = self.score()
        self.put("allon-wifi", page("wifi"))
        v, t = m.snap_verdict("allon-wifi", "w", "wifi")
        self.assertEqual(v, "PASS"); self.assertIn("allon-snapcheck (no human .verdict)", t)
        self.put("allon-wifi", page("wifi", check=False))
        v, t = m.snap_verdict("allon-wifi", "w", "wifi")
        self.assertEqual(v, "FAIL"); self.assertIn("checkmark MISSING", t)
        self.put("allon-bt", page("bluetooth"))
        self.assertEqual(m.snap_verdict("allon-bt", "b", "bluetooth")[0], "PASS")
        self.put("allon-bt", page("bluetooth", alert=True))
        v, t = m.snap_verdict("allon-bt", "b", "bluetooth")
        self.assertEqual(v, "FAIL"); self.assertIn("alert on top", t)

    def test_human_verdict_overrides(self):
        m = self.score()
        self.put("allon-bt", page("bluetooth", alert=True))
        _w(os.path.join(self.t.name, "allon-bt.verdict"), "ok: Connected visible above the alert\n", "w")
        v, t = m.snap_verdict("allon-bt", "b", "bluetooth")
        self.assertEqual(v, "PASS"); self.assertIn("read by a human", t)
        self.put("allon-wifi", page("wifi"))
        _w(os.path.join(self.t.name, "allon-wifi.verdict"), "bad: reader saw the wrong network\n", "w")
        v, t = m.snap_verdict("allon-wifi", "w", "wifi")
        self.assertEqual(v, "FAIL"); self.assertIn("read by a human", t)

    def test_unconvertible_and_absent_unmeasured(self):
        m = self.score()
        _w(os.path.join(self.t.name, "allon-wifi.raw"), b"x" * 100)
        self.assertEqual(m.snap_verdict("allon-wifi", "w", "wifi")[0], "UNMEASURED")
        self.assertEqual(m.snap_verdict("allon-bt", "b", "bluetooth")[0], "UNMEASURED")


if __name__ == "__main__":
    unittest.main()
