#!/usr/bin/env python3
"""Host test of tools/usb-port (AP-177) on a fake sysfs tree (SYSFS_USB); never reads the real bus. Run: python3 -m unittest tools/test_usb_port.py"""
import os, subprocess, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb-port")


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.dev("usb1", "1d6b", "0002", "480")   # root hub and an interface dir: never counted
        os.makedirs(os.path.join(self.t.name, "1-6:1.0"))

    def tearDown(self):
        self.t.cleanup()

    def dev(self, bus, vid, pid, speed):
        d = os.path.join(self.t.name, bus)
        os.makedirs(d)
        for k, v in (("idVendor", vid), ("idProduct", pid), ("speed", speed)):
            with open(os.path.join(d, k), "w") as f:
                f.write(v + "\n")

    def run_tool(self, *a):
        p = subprocess.run([TOOL, *a], capture_output=True, text=True, env=dict(os.environ, SYSFS_USB=self.t.name))
        return p.returncode, p.stdout

    G = "sweetusb=2 "   # the gadget word: what xrun prepends (usbnet) / what reaches XNU

    def test_480_default_warns(self):   # R27: SuperSpeed is the default, so a USB 2 link warns without any sweetusb3 word (gadget on)
        self.dev("1-6", "18d1", "4ee0", "480")
        rc, out = self.run_tool("--args", self.G + "sweetremote=1")
        self.assertEqual(rc, 0, out)
        self.assertIn("usb-port: 1-6 speed=480 product=4ee0", out)
        self.assertIn("USB3 WARN: SuperSpeed requested on a 480 Mb/s link (1-6): XNU connects at high speed (usb3-ss-1)", out)
        self.assertEqual(out.splitlines()[-1], "RESULT: PASS usbport 1-6 speed=480 sweetusb3=1")

    def test_no_gadget_prints_nothing(self):   # review usb3-default-r1 F7: sweetusb=0/1 or no sweetusb word = no gadget = no SuperSpeed claim, on either link
        for speed in ("480", "5000"):
            d = os.path.join(self.t.name, "9-9")
            if os.path.isdir(d):
                for k in os.listdir(d):
                    os.remove(os.path.join(d, k))
                os.rmdir(d)
            self.dev("9-9", "18d1", "4ee0", speed)
            for a in ("sweetusb=0", "sweetusb=1", "sweetremote=1", "sweetusb=2 sweetusb=0", "sweetusb=0 sweetusb3=0"):
                rc, out = self.run_tool("--args", a)
                self.assertEqual(rc, 0, (speed, a, out))
                self.assertNotIn("WARN", out)
                self.assertEqual(out.splitlines()[-1], "RESULT: PASS usbport 9-9 speed=%s gadget=off (no sweetusb=2: sweetusb3 not judged)" % speed)

    def test_last_sweetusb_wins(self):   # XNU keeps the last sweetusb= (xrun: base word first, user --args after)
        self.dev("1-6", "18d1", "4ee0", "480")
        self.assertIn("WARN", self.run_tool("--args", "sweetusb=0 sweetusb=2")[1])
        self.assertNotIn("WARN", self.run_tool("--args", "sweetusb=2 sweetusb=1")[1])

    def test_480_opt_out_quiet(self):   # HS-only gadget on a HS link is consistent: no warn, RESULT carries sweetusb3=0
        self.dev("1-6", "18d1", "4ee0", "480")
        rc, out = self.run_tool("--args", self.G + "sweetusb3=0")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARN", out)
        self.assertEqual(out.splitlines()[-1], "RESULT: PASS usbport 1-6 speed=480 sweetusb3=0")

    def test_5000_without_arg_pass(self):   # R27: no hand-typed sweetusb3=1 needed on the USB 3 port
        self.dev("4-3", "18d1", "4ee0", "5000")
        rc, out = self.run_tool("--args", self.G + "sweetremote=1 debug=0x8")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARN", out)
        self.assertEqual(out.splitlines()[-1], "RESULT: PASS usbport 4-3 speed=5000 sweetusb3=1")

    def test_5000_with_arg_pass(self):
        self.dev("4-3", "18d1", "4ee0", "5000")
        for a in ("debug=0x8  sweetusb3=1\tsweetremote=1", "sweetusb3"):   # a bare word is 1 (XNU getval, bootargs.c:483-484)
            rc, out = self.run_tool("--args", self.G + a)
            self.assertEqual(rc, 0, out)
            self.assertNotIn("WARN", out)
            self.assertIn("RESULT: PASS usbport 4-3 speed=5000 sweetusb3=1", out)

    def test_5000_opt_out_warns(self):   # explicit sweetusb3=0 stays usable (user order R27) but loud
        self.dev("4-3", "18d1", "4ee0", "5000")
        rc, out = self.run_tool("--args", self.G + "sweetusb3=0")
        self.assertEqual(rc, 0, out)
        self.assertIn("USB3 WARN: sweetusb3=0 opt-out on a 5000 Mb/s link: HS-only gadget, expect 0 B of USB log (AP-177)", out)
        self.assertEqual(out.splitlines()[-1], "RESULT: PASS usbport 4-3 speed=5000 sweetusb3=0")

    def test_480_with_arg_warn_pass(self):
        self.dev("1-6", "18d1", "4ee0", "480")
        rc, out = self.run_tool("--args", self.G + "sweetusb3=1")
        self.assertEqual(rc, 0, out)
        self.assertIn("USB3 WARN: SuperSpeed requested on a 480 Mb/s link (1-6): XNU connects at high speed (usb3-ss-1)", out)
        self.assertTrue(out.splitlines()[-1].startswith("RESULT: PASS"), out)

    def test_other_values_are_errors(self):   # review usb3-default-r1 F1/F4/F5: only exactly 0 / 1 / the bare word; everything else exit 2 on every link, gadget or not
        for speed in ("480", "5000"):
            d = os.path.join(self.t.name, "9-9")
            if os.path.isdir(d):
                for k in os.listdir(d):
                    os.remove(os.path.join(d, k))
                os.rmdir(d)
            self.dev("9-9", "18d1", "4ee0", speed)
            for a in ("sweetusb3=2", "sweetusb3=10", "sweetusb3=01", "sweetusb3=0x1", "sweetusb3=", "sweetusb3=x", "sweetusb3=1x", "sweetusb3=00"):
                for pre in (self.G, ""):
                    rc, out = self.run_tool("--args", pre + a)
                    self.assertEqual(rc, 2, (speed, pre, a, out))
                    last = out.splitlines()[-1]
                    self.assertTrue(last.startswith("RESULT: ERROR usbport: unsupported '%s'" % a), last)
                    self.assertTrue(last.endswith("use sweetusb3=0 or 1"), last)

    def test_word_match_only(self):   # xsweetusb3=0 / sweetusb3x=0 are not the word
        self.dev("4-3", "18d1", "4ee0", "5000")
        for a in ("xsweetusb3=0", "sweetusb3x=0", "mysweetusb3=0"):
            rc, out = self.run_tool("--args", self.G + a)
            self.assertEqual(rc, 0, (a, out))
            self.assertNotIn("WARN", out)
            self.assertIn("sweetusb3=1", out.splitlines()[-1])

    def test_zero_devices_error(self):
        rc, out = self.run_tool("--args", "sweetusb3=1")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR usbport: 0 Google (18d1) USB devices", out)

    def test_two_devices_error(self):
        self.dev("1-6", "18d1", "4ee0", "480")
        self.dev("4-3", "18d1", "4ee7", "5000")
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("usb-port: 1-6 speed=480", out)
        self.assertIn("usb-port: 4-3 speed=5000", out)
        self.assertIn("RESULT: ERROR usbport: 2 Google (18d1) USB devices", out)

    def test_no_args_reports_only(self):   # without --args a USB 3 link is reported, not judged
        self.dev("4-3", "18d1", "4ee0", "5000")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines()[-1], "RESULT: PASS usbport 4-3 speed=5000")

    def test_bad_usage(self):
        self.assertEqual(self.run_tool("--args")[0], 2)
        self.assertEqual(self.run_tool("--bogus")[0], 2)

    def test_last_sweetusb3_wins(self):   # review r1 F4: XNU's parser takes the last value (read from source: pexpert/gen/bootargs.c loop keeps going)
        self.dev("4-3", "18d1", "4ee0", "5000")
        self.assertTrue(self.run_tool("--args", self.G + "sweetusb3=1 sweetusb3=0")[1].splitlines()[-1].endswith("sweetusb3=0"))
        self.assertTrue(self.run_tool("--args", self.G + "sweetusb3=0 sweetusb3=1")[1].splitlines()[-1].endswith("sweetusb3=1"))
        self.assertTrue(self.run_tool("--args", self.G + "sweetusb3=0 sweetusb3")[1].splitlines()[-1].endswith("sweetusb3=1"))   # bare word after an opt-out = 1 (F5)
        self.assertNotIn("WARN", self.run_tool("--args", self.G + "sweetusb3=0 sweetusb3=1")[1])
        self.assertEqual(self.run_tool("--args", self.G + "sweetusb3=1 sweetusb3=2")[0], 2)   # the LAST word is judged
        self.assertEqual(self.run_tool("--args", self.G + "sweetusb3=2 sweetusb3=1")[0], 0)

    def test_xrun_calls_it_before_build_and_lock(self):   # read from source: soft check before build/lock, both checks again after the lock for every run
        src = open(os.path.join(os.path.dirname(TOOL), "xrun")).read()
        live = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))   # r1 F3: a commented-out check does not count
        pre = live.index('usbport=$("$usbportx" --args "$usbargs") || { grep -q \'usbport: 0 Google\'')
        self.assertLess(live.index('implicit="sweetremote=1"; fi'), pre)
        self.assertLess(pre, live.index('"$bxibin" --kernel'))
        self.assertLess(pre, live.index('plock_take xrun "$lockwait"'))
        lock = live.index('plock_take xrun "$lockwait"')
        post_ud = live.index('userdata=$("${pf[@]}" --userdata-check) || { printf', lock)
        post_usb = live.index('if usbport=$("$usbportx" --args "$usbargs"); then', lock)
        cam = live.index('python3 tools/qr-log.py --esp')
        self.assertLess(live.index('"$tofb" 200'), post_usb)
        self.assertLess(post_ud, cam)
        self.assertLess(post_usb, cam)
        self.assertNotIn("check\" = 1", live[post_ud - 40:post_ud])   # not inside the --no-flash-check-skipped block
        self.assertIn('USBPORT GAP: phone not on the bus after the lock', live)
        self.assertIn('usbportx=${XRUN_USB_PORT:-tools/usb-port}', live)   # sim seam (tools/test_sim_seams.sh): the default is the sibling tool
        self.assertEqual(live.count('$R mark "$D" usb_port="$usbmark"'), 2)

    def test_xrun_usb3_mark(self):   # R27: the run summary shows the opt-out loudly and the USB 2 warning; xrun never adds sweetusb3=1 itself
        src = open(os.path.join(os.path.dirname(TOOL), "xrun")).read()
        live = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        self.assertEqual(live.count('usbmark="$usbmark$(usb3_mark "$usbport")"'), 2)
        self.assertNotIn('implicit="sweetusb3=1"', live)
        # F7: usb-port judges what reaches XNU: build-xnu-image.sh's base sweetusb=2 (usbnet) / 0, then the --args words
        self.assertIn('usbargs="sweetusb=$([ -n "$usbnet" ] && echo 2 || echo 0)${args:+ $args}"', live)
        self.assertLess(live.index("usbargs="), live.index('"$usbportx" --args "$usbargs"'))
        bx = open(os.path.join(os.path.dirname(TOOL), "build-xnu-image.sh")).read()
        self.assertIn("sweetusb=0 rd=ufsroot0", bx)
        self.assertIn('[ "$usbnet" = 1 ] && args=${args/sweetusb=0/sweetusb=2}', bx)
        self.assertLess(bx.index('args=${args/sweetusb=0/sweetusb=2}'), bx.index('[ -n "$extra" ] && args="$args $extra"'))
        fn = src[src.index("usb3_mark() {"):]
        fn = fn[:fn.index("\n}\n") + 3]
        def mark(u):
            p = subprocess.run(["bash", "-c", fn + '\nusb3_mark "$1"', "x", u], capture_output=True, text=True)
            return p.stdout, p.stderr
        o, e = mark("usb-port: 4-3 speed=5000 product=4ee0\nUSB3 WARN: sweetusb3=0 opt-out on a 5000 Mb/s link: HS-only gadget, expect 0 B of USB log (AP-177)\nRESULT: PASS usbport 4-3 speed=5000 sweetusb3=0")
        self.assertIn("sweetusb3=0 OPT-OUT", o); self.assertIn("USB3 WARN: sweetusb3=0 opt-out", e)
        o, e = mark("usb-port: 1-6 speed=480 product=4ee0\nUSB3 WARN: SuperSpeed requested on a 480 Mb/s link (1-6): XNU connects at high speed (usb3-ss-1)\nRESULT: PASS usbport 1-6 speed=480 sweetusb3=1")
        self.assertIn("USB3 WARN (SuperSpeed requested on a USB 2 link)", o); self.assertNotIn("OPT-OUT", o)
        o, e = mark("usb-port: 1-6 speed=480 product=4ee0\nRESULT: PASS usbport 1-6 speed=480 sweetusb3=0")   # opt-out on a USB 2 link: still named
        self.assertIn("sweetusb3=0 OPT-OUT", o)
        o, e = mark("usb-port: 4-3 speed=5000 product=4ee0\nRESULT: PASS usbport 4-3 speed=5000 sweetusb3=1")
        self.assertEqual((o, e), ("", ""))

    def test_fflash_newest_line_and_started_marker(self):   # r1 F6/F7, read from source
        src = open(os.path.join(os.path.dirname(TOOL), "fflash-userdata.sh")).read()
        self.assertIn('[ "${last%% *}" = "$got" ]', src)
        self.assertLess(src.index('echo "flashing $(date -u +%FT%TZ) $img" >> "$state"'), src.index('fastboot flash userdata "$src"'))

if __name__ == "__main__":
    unittest.main()
