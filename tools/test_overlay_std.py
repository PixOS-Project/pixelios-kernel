#!/usr/bin/env python3
"""Host test for tools/overlay_std.py + tools/hfs_read.py (user 2026-11-09: one standard root; each boot = standard root + own overlay).

The ledger flow runs on a fake flash state and a fake 'image' whose reads are stubbed (no 17 GB image needed). The HFS+ reader is
checked against 7z on the real standard root image (OVERLAY_STD_TEST_IMAGE, else standard-root.txt); a missing image or 7z FAILS (R13: the gate container provides both)."""
import hashlib, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "overlay_std.py")
def standard_root_image():
    """The current standard root image: OVERLAY_STD_TEST_IMAGE (the gate container mounts it, R13), else the path in
    ${STANDARD_ROOT:-~/pixelios-artifacts/standard-root.txt} (changed only by tools/flash_cache.py promote). A hardcoded path went stale at the root5 promotion."""
    if os.environ.get("OVERLAY_STD_TEST_IMAGE"):
        return os.environ["OVERLAY_STD_TEST_IMAGE"]
    f = os.environ.get("STANDARD_ROOT") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "standard-root.txt")
    try:
        for ln in open(f):
            if ln.strip() and not ln.startswith("#"):
                return ln.split()[1]
    except (OSError, IndexError) as e:
        return "<no standard root: %s>" % e
    return "<no standard root line in %s>" % f


IMG = standard_root_image()
SHA = "a" * 64


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.art = os.path.join(self.d, "art")
        os.makedirs(self.art)
        self.img = os.path.join(self.d, "root.img")
        open(self.img, "w").write("x")
        with open(os.path.join(self.art, "flashed-userdata.txt"), "w") as f:
            f.write("flashing 2026-11-09T10:00:00Z %s\n%s 2026-11-09T10:04:00Z %s\n" % (self.img, SHA, self.img))
        # the standard copies the 'image' holds: pre-seed the cache the tool reads first (no HFS read in this test)
        cache = os.path.join(self.art, "std-files", SHA[:16], "root")
        for p, body in (("/System/Library/xpc/launchd.plist", b"STD-PLIST"), ("/usr/lib/libfoo.dylib", b"STD-FOO")):
            os.makedirs(os.path.dirname(cache + p), exist_ok=True)
            open(cache + p, "wb").write(body)
        os.makedirs(os.path.join(self.art, "std-files", SHA[:16]), exist_ok=True)
        open(os.path.join(self.art, "std-files", SHA[:16], ".absent"), "w").write("/usr/local/bin/added\n/usr/lib/libsweetgl.dylib\n")
        ex = os.path.join(self.art, "std-extras", "usr", "lib")
        os.makedirs(ex)
        open(os.path.join(ex, "libsweetgl.dylib"), "wb").write(b"TRUNK-GL")
        with open(os.path.join(self.art, "std-extras", "manifest.tsv"), "w") as f:
            f.write("# c\n/usr/lib/libsweetgl.dylib\t%s\tgpu\ttrunk\n" % hashlib.sha256(b"TRUNK-GL").hexdigest())
        self.env = dict(os.environ, ARTIFACTS=self.art)
        self.env.pop("FLASH_STATE", None)
        self.env.pop("PHONE_LOCK_DIR", None)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)
        open(os.path.join(self.run_dir, "usbnet.log"), "w").write("x\nSWEET DEV: copy 3 entries from a 99 byte archive\n")

    def t(self, *a):
        r = subprocess.run([sys.executable, "-I", TOOL] + list(a), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def overlay(self, name, files):
        d = os.path.join(self.d, name)
        for p, body in files.items():
            os.makedirs(os.path.dirname(d + p), exist_ok=True)
            open(d + p, "wb").write(body)
        return d

    def prep(self, name, ov="-", intent=""):
        out = os.path.join(self.d, "out-" + name)
        o = self.t("prepare", "--overlay", ov, "--out", out, "--label", name, "--intent", intent)
        got = {}
        for top, _, ns in os.walk(out):
            for n in ns:
                got["/" + os.path.relpath(os.path.join(top, n), out)] = open(os.path.join(top, n), "rb").read()
        return o, out, got

    def test_boot_counter_flags_the_first_boots_after_a_flash(self):
        # fast-loop 2026-11-09: boots 1-2 after a flash are not comparable (fcap5 migration screen, fcap7 rep 0)
        o, _, _ = self.prep("b0")
        self.assertIn("OVERLAY STD BOOTS: 0 earlier", o)
        self.assertIn("OVERLAY STD FIRST BOOT: boot 1 after the flash", o)
        self.assertIn("boot 1 of root", self.t("boot", "--label", "b0", "--run", self.run_dir))
        self.assertIn("FIRST BOOT: boot 2", self.prep("b1")[0])
        self.t("boot", "--label", "b1", "--run", self.run_dir)
        o = self.prep("b2")[0]
        self.assertIn("OVERLAY STD BOOTS: 2 earlier", o)
        self.assertNotIn("FIRST BOOT", o)

    def test_old_ledger_without_counter_is_not_called_first_boot(self):
        self.t("note", "/usr/lib/libfoo.dylib", "--by", "x")          # a ledger file exists ...
        led = [os.path.join(self.art, "overlay-ledger", n) for n in os.listdir(os.path.join(self.art, "overlay-ledger"))][0]
        j = json.load(open(led)); j.pop("boots", None); json.dump(j, open(led, "w"))   # ... from before the counter
        o = self.prep("old")[0]
        self.assertIn("at least 0 earlier", o)
        self.assertNotIn("FIRST BOOT", o)

    def test_jobs_diff_names_added_and_dropped_jobs(self):
        import plistlib
        def pl(labels):
            return plistlib.dumps({"LaunchDaemons": {"/x/%s.plist" % l: {"Label": l} for l in labels}}, fmt=plistlib.FMT_BINARY)
        cache = os.path.join(self.art, "std-files", SHA[:16], "root", "System/Library/xpc/launchd.plist")
        open(cache, "wb").write(pl(["com.apple.a", "com.pixos.btapp-launch"]))
        ov = self.overlay("gpuov", {"/System/Library/xpc/launchd.plist": pl(["com.apple.a", "com.pixos.devd"])})
        o = self.prep("gpu", ov)[0]
        self.assertIn("OVERLAY STD JOBS: this run's launchd.plist vs the standard root: 1 added (com.pixos.devd), 1 dropped (com.pixos.btapp-launch)", o)
        o = self.prep("plain")[0]
        self.assertNotIn("OVERLAY STD JOBS", o)                         # no own plist: nothing to diff

    def test_plain_run_resets_launchd_plist_only(self):
        o, _, got = self.prep("bt")
        self.assertEqual(got, {"/System/Library/xpc/launchd.plist": b"STD-PLIST"})
        self.assertIn("extra(s) of another intent not restored in this plain run", o)

    def test_gpu_run_brings_the_extra(self):
        o, _, got = self.prep("g", intent="gpu")
        self.assertEqual(got["/usr/lib/libsweetgl.dylib"], b"TRUNK-GL")
        self.assertNotIn("COMPRESS", o)   # tiny test files

    def test_cross_thread_leak_is_undone(self):
        wifi = self.overlay("wifi", {"/System/Library/xpc/launchd.plist": b"WIFI-JOBS", "/usr/lib/libfoo.dylib": b"WIFI-FOO",
                                     "/usr/local/bin/added": b"BIN"})
        _, out, _ = self.prep("wifi", wifi)
        self.t("record", "--dir", out, "--label", "wifi", "--run", self.run_dir)
        o, _, got = self.prep("gpu", intent="gpu")
        self.assertEqual(got["/System/Library/xpc/launchd.plist"], b"STD-PLIST")
        self.assertEqual(got["/usr/lib/libfoo.dylib"], b"STD-FOO")
        self.assertIn("undoing earlier writes", o)
        self.assertIn("RESIDUE: 1 file(s)", o)
        self.assertIn("/usr/local/bin/added (by wifi)", o)

    def test_own_overlay_wins(self):
        gpu = self.overlay("gpu", {"/usr/lib/libsweetgl.dylib": b"FCAP-GL", "/System/Library/xpc/launchd.plist": b"GPU-PLIST"})
        _, _, got = self.prep("gpu", gpu, "gpu")
        self.assertEqual(got["/usr/lib/libsweetgl.dylib"], b"FCAP-GL")
        self.assertEqual(got["/System/Library/xpc/launchd.plist"], b"GPU-PLIST")

    def test_restored_paths_leave_the_ledger_and_extras_tracked(self):
        gpu = self.overlay("fcap", {"/usr/lib/libsweetgl.dylib": b"FCAP-GL"})
        _, out, _ = self.prep("fcap", gpu, "gpu")
        self.t("record", "--dir", out, "--label", "fcap", "--run", self.run_dir)
        o, out2, got = self.prep("bt")   # a BT run never carries the 22 MB extra
        self.assertNotIn("/usr/lib/libsweetgl.dylib", got)
        _, out3, got3 = self.prep("g2", intent="gpu")
        self.assertEqual(got3["/usr/lib/libsweetgl.dylib"], b"TRUNK-GL")
        self.t("record", "--dir", out3, "--label", "g2", "--run", self.run_dir)
        led = json.load(open([os.path.join(self.art, "overlay-ledger", n) for n in os.listdir(os.path.join(self.art, "overlay-ledger")) if n.endswith(".json")][0]))
        self.assertEqual(led["paths"], {})
        self.assertEqual(led["extras"]["/usr/lib/libsweetgl.dylib"], hashlib.sha256(b"TRUNK-GL").hexdigest())
        _, _, got4 = self.prep("g3", intent="gpu")   # already standard on userdata: not shipped again
        self.assertNotIn("/usr/lib/libsweetgl.dylib", got4)

    def test_record_without_apply_line_counts_as_maybe(self):
        wifi = self.overlay("w", {"/usr/lib/libfoo.dylib": b"W"})
        _, out, _ = self.prep("w", wifi)
        os.unlink(os.path.join(self.run_dir, "usbnet.log"))
        o = self.t("record", "--dir", out, "--label", "w", "--run", self.run_dir)
        self.assertIn("MAYBE applied", o)
        _, _, got = self.prep("next")   # counted: the next boot puts the standard copy back
        self.assertEqual(got["/usr/lib/libfoo.dylib"], b"STD-FOO")

    def test_whiteout_for_added_files_with_a_0280_kernel(self):
        open(os.path.join(self.art, "std-files", SHA[:16], ".absent"), "a").write("/usr/lib/libother.dylib\n")
        wifi = self.overlay("wifi", {"/usr/local/bin/added": b"BIN", "/usr/lib/libother.dylib": b"L"})
        _, out, _ = self.prep("wifi", wifi)
        self.t("record", "--dir", out, "--label", "wifi", "--run", self.run_dir)
        k = os.path.join(self.d, "kernel")
        open(k, "wb").write(b"xx whiteout outside /usr/local/ yy")
        out2 = os.path.join(self.d, "out-wh")
        o = self.t("prepare", "--overlay", "-", "--out", out2, "--label", "bt", "--kernel", k)
        self.assertTrue(os.path.exists(os.path.join(out2, "usr/local/bin/added.sweetwhiteout")))
        self.assertIn("OVERLAY STD REMOVE: 1 file(s)", o)
        self.assertIn("RESIDUE: 1 file(s)", o)   # /usr/lib/libother.dylib: 0280 never removes in /usr/lib (but libsweetgl)
        r = self.t("record", "--dir", out2, "--label", "bt", "--run", self.run_dir)
        self.assertIn("whiteout of /usr/local/bin/added not confirmed", r)   # no `removed` line yet: stays
        with open(os.path.join(self.run_dir, "usbnet.log"), "a") as f:
            f.write("SWEET DEV: removed /usr/local/bin/added 0 ms\n")
        self.t("record", "--dir", out2, "--label", "bt", "--run", self.run_dir)
        o3 = self.t("prepare", "--overlay", "-", "--out", os.path.join(self.d, "o3"), "--label", "bt2", "--kernel", k)
        self.assertNotIn("/usr/local/bin/added", o3)
        o4 = self.t("prepare", "--overlay", "-", "--out", os.path.join(self.d, "o4"), "--label", "old")   # no --kernel: residue as before
        self.assertIn("libother.dylib", o4)

    def test_ledger_path_the_local_packer_refuses_is_skipped(self):
        self.t("note", "/Applications/Other.app/Other", "--by", "a newer checkout")   # not in this packer's allowlist
        o, _, got = self.prep("old")
        self.assertIn("OVERLAY STD GAP: /Applications/Other.app/Other is in the ledger but this checkout's mkdevoverlay refuses it", o)
        self.assertNotIn("/Applications/Other.app/Other", got)

    def test_check_detects_a_change(self):
        o, _, _ = self.prep("a")
        rev = [l.split(": ", 1)[1] for l in o.splitlines() if l.startswith("OVERLAY STD REV: ")][0]
        self.t("check", "--rev", rev)
        self.t("note", "/usr/lib/libfoo.dylib", "--by", "devsh push")
        r = subprocess.run([sys.executable, "-I", TOOL, "check", "--rev", rev], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)

    def test_unknown_state_passes_through(self):
        with open(os.path.join(self.art, "flashed-userdata.txt"), "a") as f:
            f.write("flashing 2026-11-09T11:00:00Z %s\n" % self.img)
        ov = self.overlay("x", {"/usr/lib/libfoo.dylib": b"X"})
        o = self.t("prepare", "--overlay", ov, "--out", os.path.join(self.d, "o"), "--label", "x")
        self.assertIn("OVERLAY STD GAP", o)
        self.assertIn("OVERLAY STD DIR: %s" % ov, o)
        self.assertIn("OVERLAY STD REV: unknown", o)

    def test_bad_extra_sha_is_an_error(self):
        open(os.path.join(self.art, "std-extras", "usr", "lib", "libsweetgl.dylib"), "wb").write(b"OTHER")
        r = subprocess.run([sys.executable, "-I", TOOL, "prepare", "--overlay", "-", "--out", os.path.join(self.d, "o"), "--intent", "gpu"],
                           env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("has sha256", r.stdout + r.stderr)


class Reader(unittest.TestCase):
    def test_matches_7z_and_reads_launchd_plist(self):
        sys.path.insert(0, HERE)
        import hfs_read
        h = hfs_read.HFS(IMG)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        subprocess.run(["7z", "x", "-y", "-bd", "-o" + d, IMG, "iOSRoot/System/Library/LaunchDaemons/com.apple.*"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        base = os.path.join(d, "iOSRoot")
        names = sorted(n for n in os.listdir(os.path.join(base, "System/Library/LaunchDaemons")))[:25]
        self.assertTrue(names)
        for n in names:
            p = "/System/Library/LaunchDaemons/" + n
            ref = open(base + p, "rb").read()
            if ref:
                self.assertEqual(h.read_file(p), ref, p)
        plist = h.read_file("/System/Library/xpc/launchd.plist")   # 7z 25.01 reads 0 bytes of this one
        self.assertTrue(plist.startswith(b"bplist00"))
        self.assertIsNone(h.read_file("/System/Library/xpc/no-such-file"))


if __name__ == "__main__":
    unittest.main()
