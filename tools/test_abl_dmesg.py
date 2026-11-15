"""Host test for tools/abl-dmesg (TP-044): fake fastboot on PATH, fake phone-lock, tmp dirs only (AP-210)."""
import os, shutil, subprocess, sys, tempfile, time, unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abl-dmesg")
SER = "FAKESER123"
RING = ("[   0.1] boot\n  RST_STAT: 0x1 - cold\n  Reboot mode: 0x4 - bootloader\nReboot Info:\n"
        "fastboot enter reason: x\n")

FAKE = r"""#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
d = os.environ["FAKE_DIR"]
open(os.path.join(d, "calls"), "a").write(" ".join(a) + "\n")
n = int(os.environ.get("FAKE_NDEV", "1"))
if a == ["devices"]:
    sys.stdout.write("".join("%s%d\tfastboot\n" % (os.environ["FAKE_SER"], i) if n > 1 else "%s\tfastboot\n" % os.environ["FAKE_SER"] for i in range(n)))
elif a[:1] == ["-s"] and a[1].startswith(os.environ["FAKE_SER"]) and a[2:] == ["getvar", "product"]:
    if os.environ.get("FAKE_MODE") == "hang_getvar":
        import time; time.sleep(60)
    sys.stderr.write("product: %s\nFinished. Total time: 0.001s\n" % os.environ.get("FAKE_PRODUCT", "bluejay"))
elif a[:1] == ["-s"] and a[1].startswith(os.environ["FAKE_SER"]) and a[2:] == ["oem", "dmesg"]:
    m = os.environ.get("FAKE_MODE", "ok")
    if m == "hang":
        import time
        open(os.path.join(d, "pid"), "w").write(str(os.getpid()))
        time.sleep(60)
    if m == "fail":
        sys.stderr.write("FAILED (remote: 'x')\nfastboot: error: Command failed\n"); sys.exit(1)
    if m == "rc1":
        sys.exit(1)
    if m == "locked":
        sys.stderr.write("FAILED (remote: 'oem command (dmesg) is not allowed when locked')\nfastboot: error: Command failed\n"); sys.exit(1)
    ring = open(os.path.join(d, "ring")).read()
    k = len(ring) // 3
    for part in (ring[:k + 1], ring[k + 1:2 * k + 2], ring[2 * k + 2:]):  # 3 TEXT chunks, boundaries mid-line
        sys.stderr.write(part); sys.stderr.flush()
    if m != "notail":
        sys.stderr.write("OKAY [  0.010s]\nFinished. Total time: 0.011s\n")
else:
    sys.stderr.write("UNEXPECTED %r\n" % a); sys.exit(9)
"""


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="abl-dmesg-test-")
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "bin"))
        os.makedirs(os.path.join(self.d, "tools"))
        for name, body in (("bin/fastboot", FAKE), ("tools/phone-lock", '#!/bin/sh\n[ "$FAKE_LOCK" = 1 ]\n')):
            p = os.path.join(self.d, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)
        self.tool = os.path.join(self.d, "tools", "abl-dmesg")  # copy next to the fake phone-lock: the tool uses its sibling
        shutil.copy(SRC, self.tool)
        self.out = os.path.join(self.d, "out")
        self.set_ring(RING)

    def set_ring(self, s):
        with open(os.path.join(self.d, "ring"), "w") as f:
            f.write(s)

    def run_tool(self, *args, **env):
        e = dict(os.environ, PATH=os.path.join(self.d, "bin") + os.pathsep + os.environ["PATH"], FAKE_DIR=self.d, FAKE_SER=SER,
                 FAKE_LOCK="1", HEAVY_JOB_HELD="test", ABL_DMESG_TIMEOUT="2", HOME=self.d)
        e.update(env)
        pre = [] if "--out" in args else ["--out", self.out]
        p = subprocess.run([sys.executable, self.tool] + pre + list(args), capture_output=True, text=True, env=e, timeout=30)
        self.stderr = p.stderr
        return p.returncode, p.stdout, self.calls()

    def calls(self):
        f = os.path.join(self.d, "calls")
        if not os.path.exists(f):
            return []
        with open(f) as fh:
            return fh.read().splitlines()

    def last(self, out):
        return out.strip().splitlines()[-1]

    def test_lock_not_held(self):
        rc, out, calls = self.run_tool(FAKE_LOCK="0")
        self.assertEqual(rc, 2); self.assertIn("RESULT: ERROR", self.last(out)); self.assertEqual(calls, [])

    def test_device_counts(self):
        for n in ("0", "2"):
            rc, out, calls = self.run_tool(FAKE_NDEV=n)
            self.assertEqual(rc, 2, out); self.assertIn("RESULT: ERROR", self.last(out))
            self.assertEqual(set(calls), {"devices"})  # the fake accepts any FAKESER* serial, so only the count check stops it

    def test_not_bluejay(self):
        rc, out, calls = self.run_tool(FAKE_PRODUCT="sweet")
        self.assertEqual(rc, 2); self.assertIn("RESULT: ERROR", self.last(out)); self.assertFalse([c for c in calls if "oem" in c])

    def test_product_exact(self):
        for prod in ("bluejay2", "xbluejay", "bluejay-x", "sweet"):
            rc, out, calls = self.run_tool(FAKE_PRODUCT=prod)
            self.assertEqual(rc, 2, prod); self.assertIn("RESULT: ERROR", self.last(out))
            self.assertFalse([c for c in calls if "oem" in c], prod)

    def test_generic_fail_and_rc1(self):
        rc, out, _ = self.run_tool(FAKE_MODE="fail")
        self.assertEqual(rc, 1); self.assertTrue(self.last(out).startswith("RESULT: FAIL fastboot rc=1"), out)
        self.assertEqual(len(out.strip().splitlines()), 1)  # F3: one line, RESULT last
        rc, out, _ = self.run_tool(FAKE_MODE="rc1")
        self.assertEqual(rc, 1); self.assertTrue(self.last(out).startswith("RESULT: FAIL fastboot rc=1"), out)

    def test_ring_with_fail_words_and_rc0_is_pass(self):
        self.set_ring(RING + "FAILED (x)\nnote: oem command (dmesg) is not allowed when locked\n")
        rc, out, _ = self.run_tool()
        self.assertEqual(rc, 0, out); self.assertRegex(self.last(out), r"^RESULT: PASS ")

    def test_bad_labels(self):
        for lab in ("a b", "../x", "", "a/b"):
            rc, out, calls = self.run_tool("--label", lab)
            self.assertEqual(rc, 2, repr(lab)); self.assertIn("RESULT: ERROR", self.last(out)); self.assertEqual(calls, [])

    def test_no_fastboot_bad_timeout_unwritable_out_usage(self):
        rc, out, _ = self.run_tool(PATH=os.path.join(self.d, "nonexistent"))
        self.assertEqual(rc, 2); self.assertIn("RESULT: ERROR", self.last(out)); self.assertNotIn("Traceback", self.stderr)
        rc, out, _ = self.run_tool(ABL_DMESG_TIMEOUT="abc")
        self.assertEqual(rc, 2); self.assertIn("RESULT: ERROR", self.last(out)); self.assertNotIn("Traceback", self.stderr)
        rc, out, _ = self.run_tool("--out", "/proc/nope/x")
        self.assertEqual(rc, 2); self.assertIn("RESULT: ERROR", self.last(out)); self.assertNotIn("Traceback", self.stderr)
        for args in (["--help"], ["--bogus"]):
            rc, out, calls = self.run_tool(*args)
            self.assertEqual(rc, 2); self.assertEqual(self.last(out), "RESULT: ERROR usage: tools/abl-dmesg [--out DIR] [--label LABEL] [--dry-run]")

    def test_getvar_timeout_is_error(self):
        rc, out, calls = self.run_tool(FAKE_MODE="hang_getvar")
        self.assertEqual(rc, 2); self.assertIn("getvar product timed out", self.last(out)); self.assertFalse([c for c in calls if "oem" in c])

    def test_missing_tail_warns(self):
        rc, out, _ = self.run_tool(FAKE_MODE="notail")
        self.assertEqual(rc, 0, out); self.assertIn("WARN no `OKAY", out); self.assertTrue(self.last(out).endswith("warn=1"))
        rc, out, _ = self.run_tool()
        self.assertTrue(self.last(out).endswith("warn=0"))

    def test_locked(self):
        rc, out, _ = self.run_tool(FAKE_MODE="locked")
        self.assertEqual(rc, 1); self.assertIn("RESULT: FAIL locked", self.last(out))

    def test_timeout(self):
        rc, out, _ = self.run_tool(FAKE_MODE="hang")
        self.assertEqual(rc, 1); self.assertIn("RESULT: FAIL timeout", self.last(out))
        with open(os.path.join(self.d, "pid")) as f:
            pid = int(f.read())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:   # lint-silent: expected ProcessLookupError is the awaited fact: the fake child is gone
                break
            time.sleep(0.1)
        else:
            self.fail("fake fastboot child survived the timeout")

    def test_pass_split_chunks_and_serial_masked(self):
        ring = RING + "serial %s here\n" % SER
        self.set_ring(ring)
        rc, out, calls = self.run_tool("--label", "t1")
        self.assertEqual(rc, 0, out)
        self.assertRegex(self.last(out), r"^RESULT: PASS bytes=\d+ reboot_mode=0x4 rst_stat=0x1 file=")
        path = self.last(out).split("file=")[1].split(" warn=")[0]
        got = open(path).read()
        self.assertEqual(got, ring.replace(SER, "<redacted>"))
        self.assertNotIn(SER, got); self.assertNotIn(SER, out)
        self.assertTrue(path.endswith("-t1.txt"))
        self.assertEqual(calls.count("-s %s oem dmesg" % SER), 1)

    def test_chunk_boundary_mid_line(self):
        # the fake writes the ring in one go; 3 chunks as fastboot prints them are plain concatenation (TextMessage adds nothing)
        parts = ["  RST_STAT: 0", "x1 - cold\n  Reboot mo", "de: 0x4 - bl\n"]
        self.set_ring("".join(parts))
        rc, out, _ = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("reboot_mode=0x4 rst_stat=0x1", self.last(out))

    def test_partial_and_empty(self):
        self.set_ring("[ 1.0] nothing useful\n")
        rc, out, _ = self.run_tool()
        self.assertEqual(rc, 1); self.assertRegex(self.last(out), r"^RESULT: PARTIAL .*reboot_mode=unknown rst_stat=unknown")
        self.set_ring("")
        rc, out, _ = self.run_tool()
        self.assertEqual(rc, 1); self.assertIn("RESULT: FAIL empty", self.last(out))

    def test_dry_run_sends_nothing(self):
        rc, out, calls = self.run_tool("--dry-run")
        self.assertEqual(rc, 0); self.assertFalse([c for c in calls if "oem" in c]); self.assertIn("would send", out)

    def test_argv_allowlist(self):
        code = ("import sys; ns = {'__name__': 'x', '__file__': sys.argv[1]}; exec(compile(open(sys.argv[1]).read(), sys.argv[1], 'exec'), ns)\n"
                "assert ns['ALLOWED'] == {('devices',), ('getvar', 'product'), ('oem', 'dmesg')}\n"
                "bad = [('reboot',), ('flash', 'boot'), ('erase', 'userdata'), ('oem', 'other'), ('oem', 'dmesg', 'x')]\n"
                "for v in bad:\n"
                "    try: ns['fb']('S', *v)\n"
                "    except RuntimeError: continue\n"
                "    sys.exit('verb accepted: %r' % (v,))\n")
        for flags in ([], ["-O"]):  # -O strips assert: the check must still hold (review F6)
            p = subprocess.run([sys.executable] + flags + ["-c", code, self.tool], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, (flags, p.stderr))
        # every call recorded by the fake in a full run is one of the allowed shapes, with -s after devices
        self.run_tool()
        self.assertEqual(self.calls(), ["devices", "-s %s getvar product" % SER, "-s %s oem dmesg" % SER])


if __name__ == "__main__":
    unittest.main()
