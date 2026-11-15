"""tools/fflash-userdata.sh refuses, before ANY fastboot call: an image bigger than the bluejay userdata partition (board.json), the same through a
symlink (review fork-fixes F1), and an Android sparse image (F2). A fitting raw image gets past those checks to the product check.
Runs a copy of the script in a scratch tree with a fake fastboot that records every call; seconds, no phone."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


MIB = 2          # the scratch board.json says userdata is 2 MiB: every image stays tiny (hashing a real-size 110 GiB file took minutes)


def board_mib(root):
    return int(subprocess.run(["python3", "-I", os.path.join(root, "tools", "boards.py"), "bluejay", "--get", "userdata_mib"],
                              capture_output=True, text=True, check=True).stdout)


class FflashSize(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = t = td.name
        os.makedirs(os.path.join(t, "tools"))
        os.makedirs(os.path.join(t, "bin"))
        for f in ("fflash-userdata.sh", "boards.py", "flash_cache.py", "flash_cache_lib.py"):
            shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(t, "tools", f))
        for d in ("device", "soc"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(t, d))
        self.fb_log = os.path.join(t, "fb.log")
        self.exe(os.path.join(t, "tools", "phone-lock"), "#!/bin/sh\nexit 0\n")
        # T-f32e: the formal-gate check is tools/run-preflight --gate-check flash (its own tests: tools/test_run_preflight.py); a stub whose exit code the test picks
        self.exe(os.path.join(t, "tools", "run-preflight"), '#!/usr/bin/env python3\nimport os, sys\nassert sys.argv[1:] == ["--gate-check", "flash"], sys.argv\n'
                 'rc = int(os.environ.get("STUB_GATE_RC", "0"))\nprint("RESULT: PASS gate admitted-by=formal" if rc == 0 else "RESULT: FAIL gate: the newest GATE PASS abc is a --quick run")\nsys.exit(rc)\n')
        self.exe(os.path.join(t, "bin", "fastboot"), '#!/bin/sh\necho "$@" >>%s\nexit 0\n' % self.fb_log)
        bj = os.path.join(t, "device", "google", "bluejay", "board.json")
        with open(bj) as f:
            text = f.read()
        real = '"userdata_mib": %d,' % board_mib(ROOT)
        self.assertEqual(text.count(real), 1, "board.json userdata_mib line changed: update this test")
        with open(bj, "w") as f:
            f.write(text.replace(real, '"userdata_mib": %d,' % MIB))
        self.mib = board_mib(t)
        self.assertEqual(self.mib, MIB)

    @staticmethod
    def exe(p, text):
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, 0o755)

    def image(self, name, size, head=b""):
        p = os.path.join(self.t, name)
        with open(p, "wb") as f:
            f.write(head)
            f.truncate(size)           # sparse on disk: the apparent size is what the script must see
        return p

    REASON = "root image rebuilt: new launchd.plist"

    def flash(self, img, reason=REASON, extra=(), yes=True, **envx):
        sha = subprocess.run(["sha256sum", img], capture_output=True, text=True, check=True).stdout
        with open(img + ".sha256", "w") as f:
            f.write(sha)
        env = dict(os.environ, PATH=os.path.join(self.t, "bin") + os.pathsep + os.environ["PATH"], FLASH_STATE=os.path.join(self.t, "state"),
                   FLASH_REASONS=os.path.join(self.t, "reasons.tsv"), STANDARD_ROOT=os.path.join(self.t, "standard-root.txt"),
                   FLASH_CACHE_DIR=os.path.join(self.t, "flash-cache"), FLASH_CACHE_AUTOPREP="0")   # never the real cache or hash stamps   # never the real flash-reasons file
        env.update(envx)
        args = ["--reason", reason] if reason is not None else []
        r = subprocess.run(["bash", os.path.join(self.t, "tools", "fflash-userdata.sh"), img, *(["--yes-erase-userdata"] if yes else []), *args, *extra], capture_output=True, text=True, env=env, timeout=120)
        self.stdout = r.stdout   # the RESULT line is the last line of stdout
        return r.returncode, r.stdout + r.stderr

    def assert_refused_before_fastboot(self, img, why):
        rc, out = self.flash(img)
        self.assertEqual(rc, 1, out)
        self.assertIn(why, out)
        self.assertFalse(os.path.exists(self.fb_log), "fastboot was called: " + (open(self.fb_log).read() if os.path.exists(self.fb_log) else ""))

    def test_quick_gated_flash_is_refused(self):   # T-f32e: a quick gate admits RAM boots only; the flash is refused before any size/hash/fastboot step
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        sha = subprocess.run(["sha256sum", img], capture_output=True, text=True, check=True).stdout
        open(img + ".sha256", "w").write(sha)
        rc, out = self.flash(img, STUB_GATE_RC="1")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED: no formal GATE PASS", out)
        self.assertIn("--quick run", out)
        self.assertFalse(os.path.exists(self.fb_log), "fastboot was called")
        self.assertFalse(os.path.exists(os.path.join(self.t, "state")), "state file written")

    def test_formal_gated_flash_reaches_the_product_check(self):   # mutant partner: gate rc 0 must not refuse
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"), STUB_GATE_RC="0")
        self.assertNotIn("no formal GATE PASS", out)
        self.assertIn("REFUSED: product=", out)

    def test_oversize_raw_image_is_refused(self):
        self.assert_refused_before_fastboot(self.image("big.img", (self.mib + 1) << 20), "userdata partition is %d MiB" % self.mib)

    def test_oversize_image_behind_a_symlink_is_refused(self):
        big = self.image("big.img", (self.mib + 1) << 20)
        link = os.path.join(self.t, "link.img")
        os.symlink(big, link)
        self.assert_refused_before_fastboot(link, "userdata partition is %d MiB" % self.mib)

    def test_sparse_image_is_refused(self):
        self.assert_refused_before_fastboot(self.image("s.img", 1 << 20, bytes.fromhex("3aff26ed")), "Android sparse image")

    def test_fitting_raw_image_reaches_the_product_check(self):
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
        self.assertEqual(rc, 1, out)
        self.assertNotIn("userdata partition is", out)
        self.assertNotIn("sparse", out)
        self.assertIn("REFUSED: product=", out)       # the fake fastboot prints no product: stops there, nothing flashed
        self.assertNotIn("flash", open(self.fb_log).read())

    def test_one_byte_over_is_refused(self):
        self.assert_refused_before_fastboot(self.image("over.img", (self.mib << 20) + 1), "userdata partition is %d MiB" % self.mib)

    def test_exactly_full_size_is_allowed(self):
        rc, out = self.flash(self.image("full.img", self.mib << 20))
        self.assertNotIn("userdata partition is", out)
        self.assertIn("REFUSED: product=", out)

    def test_non_numeric_userdata_mib_is_refused(self):  # review fork-fixes-r2 F2 (mutant m5 survived)
        bj = os.path.join(self.t, "device", "google", "bluejay", "board.json")
        with open(bj) as f:
            text = f.read()
        for bad in ('"2.5"', "2.5", '"abc"'):
            with open(bj, "w") as f:
                f.write(text.replace('"userdata_mib": %d,' % MIB, '"userdata_mib": %s,' % bad))
            rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
            self.assertEqual(rc, 1, out)
            self.assertIn("REFUSED: userdata_mib for bluejay is not a number", out, bad)
            self.assertFalse(os.path.exists(self.fb_log), bad)

    def test_image_changed_after_the_hash_is_refused(self):  # review fork-fixes-r2 F3: TOCTOU between the checks and fastboot flash
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        real = shutil.which("sha256sum")
        # the fake sha256sum reports the true hash, then grows the file: what the script hashed is not what fastboot would send
        self.exe(os.path.join(self.t, "bin", "sha256sum"), '#!/bin/sh\n%s "$@"\nfor f; do :; done\nprintf x >>"$f"\n' % real)
        self.fake_bluejay()
        rc, out = self.flash(img)
        self.assertEqual(rc, 1, out)
        self.assertIn("changed after the size and hash checks", out)
        self.assertNotIn("flash userdata", open(self.fb_log).read())
        self.assertFalse(os.path.exists(os.path.join(self.t, "state")), "no 'flashing' line for a refused image")

    def fake_bluejay(self):
        self.exe(os.path.join(self.t, "bin", "fastboot"),
                 '#!/bin/sh\necho "$@" >>%s\ncase "$*" in "getvar product") echo "product: bluejay" ;; "getvar unlocked") echo "unlocked: yes" ;; esac\nexit 0\n' % self.fb_log)

    def test_same_size_rewrite_with_mtime_restored_is_refused(self):  # review fork-fixes-r3 F1: probe B (in-place rewrite, then touch -r)
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        real = shutil.which("sha256sum")
        self.exe(os.path.join(self.t, "bin", "sha256sum"),
                 '#!/bin/sh\n%s "$@"\nfor f; do :; done\ncp -p "$f" "$f.ref"\nprintf abcd | dd of="$f" conv=notrunc status=none\ntouch -r "$f.ref" "$f"\n' % real)
        self.fake_bluejay()
        rc, out = self.flash(img)
        self.assertEqual(rc, 1, out)
        self.assertIn("changed after the size and hash checks", out)
        self.assertNotIn("flash userdata", open(self.fb_log).read())

    def test_fitting_image_behind_a_symlink_is_flashed(self):  # review fork-fixes-r3 F2: kills mutant m2 (compare without stat -L)
        link = os.path.join(self.t, "link.img")
        os.symlink(self.image("ok.img", 1 << 20, b"\0\0\0\0"), link)
        self.fake_bluejay()
        rc, out = self.flash(link)
        self.assertEqual(rc, 0, out)
        self.assertIn("flash userdata", open(self.fb_log).read())

    def test_failing_getvar_is_a_loud_refusal(self):  # review fork-fixes-r2 F5: pipefail + set -e used to end the script silently
        self.exe(os.path.join(self.t, "bin", "fastboot"), '#!/bin/sh\necho "$@" >>%s\nexit 1\n' % self.fb_log)
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED: product='' unlocked=''", out)
        self.assertNotIn("flash userdata", open(self.fb_log).read())

    # ---- R54: --reason is required, checked first; every outcome ends with a RESULT line ----
    def test_missing_or_short_reason_is_refused_before_everything(self):
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        for bad in (None, "", "short", "   x      ", "two\nlines of reason", "tab\tseparated reason", "nel\u0085 in the reason", "line\u2028separator", "caf\u00e9 au lait reason"):
            rc, out = self.flash(img, reason=bad)
            self.assertEqual(rc, 1, (bad, out))
            self.assertIn("RESULT: FAIL --reason", out)
            for part in ("a userdata flash costs minutes", "Instead:", "tools/devsh push", "tools/xrun", "Escape:"):
                self.assertIn(part, out)
            self.assertFalse(os.path.exists(self.fb_log), "fastboot was called")
            self.assertFalse(os.path.exists(os.path.join(self.t, "state")))
        rc, out = self.flash(self.image("big.img", (self.mib + 1) << 20), reason=None)   # reason first: not the size message
        self.assertIn("--reason", out)
        self.assertNotIn("userdata partition is", out)

    def test_missing_erase_flag_is_refused_before_fastboot(self):   # review flash-guard-t3360 F3: RULES H3 names this refusal
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"), yes=False)
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL this erases userdata", out)
        self.assertFalse(os.path.exists(self.fb_log), "fastboot was called")

    def test_signal_during_the_flash_still_ends_with_a_result_line(self):   # review F2
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        self.exe(os.path.join(self.t, "bin", "fastboot"), '#!/bin/sh\ncase "$*" in *getvar*) echo "product: bluejay" >&2; echo "unlocked: yes" >&2;; *flash*) kill -TERM $PPID; sleep 5;; esac\nexit 0\n')
        rc, out = self.flash(img)
        if "REFUSED: product=" in out or "REFUSED:" in out:
            self.skipTest("fake fastboot does not reach the flash step: " + out[-200:])
        self.assertIn("RESULT: FAIL", out)

    def test_every_caller_passes_reason(self):   # review F1: golden-6b.sh restore was left without --reason
        calls = subprocess.run(["git", "grep", "-n", "-E", r"tools/fflash-userdata\.sh +\"?\$", "--", "docs/golden", "tools", ":!tools/test_*"],
                               cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
        calls += subprocess.run(["git", "grep", "-n", "-E", r"tools/fflash-userdata\.sh +[A-Za-z/.\"$]+ .*--yes-erase-userdata", "--", "../docs", "../../docs", "tools", ":!tools/test_*"],
                                cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
        self.assertTrue(calls, "no caller found: the grep went stale")
        for c in calls:
            self.assertIn("--reason", c, c)

    def test_flash_records_reason_and_ends_with_result_pass(self):
        self.fake_bluejay()
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
        self.assertEqual(rc, 0, out)
        rows = [l.split("\t") for l in open(os.path.join(self.t, "reasons.tsv")).read().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0][0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertRegex(rows[0][1], r"^[0-9a-f]{64}$")
        self.assertEqual(rows[0][2], self.REASON)
        self.assertRegex(out, r"FLASH: reason=%s image=%s time=%s" % (self.REASON, rows[0][1][:12], rows[0][0]))
        self.assertEqual(self.stdout.strip().splitlines()[-1], "RESULT: PASS flashed userdata flashes_today=1 reason=" + self.REASON)
        state = open(os.path.join(self.t, "state")).read().splitlines()   # format unchanged: tools/run-preflight parses it
        self.assertRegex(state[-1], r"^[0-9a-f]{64} \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ /")

    def std_root(self, sha):
        with open(os.path.join(self.t, "standard-root.txt"), "w") as f:
            f.write("%s /tank/x/root.img\n" % sha)

    def test_non_standard_root_is_refused_before_fastboot(self):   # user 2026-11-10: no root ping-pong (super docs/recipes/no-flash.md)
        self.fake_bluejay()
        self.std_root("c" * 64)
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        for bad in (None, "", "short", "two\nlines here"):
            rc, out = self.flash(img, extra=() if bad is None else ("--not-overlayable", bad))
            self.assertEqual(rc, 1, (bad, out))
            self.assertIn("is not the standard root (cccccccccccc", out)
            self.assertIn("docs/recipes/no-flash.md", out)
            self.assertIn("Escape: --not-overlayable", out)
            self.assertFalse(os.path.exists(self.fb_log), "fastboot was called: " + (open(self.fb_log).read() if os.path.exists(self.fb_log) else ""))
            self.assertFalse(os.path.exists(os.path.join(self.t, "state")))

    def test_non_standard_root_with_not_overlayable_is_flashed_and_recorded(self):   # mutant partner of the refusal
        self.fake_bluejay()
        self.std_root("c" * 64)
        why = "new IPSW build: /System differs, no overlay path"
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"), extra=("--not-overlayable", why))
        self.assertEqual(rc, 0, out)
        self.assertIn("FLASH NOT STANDARD: image ", out)
        rows = [l.split("\t") for l in open(os.path.join(self.t, "reasons.tsv")).read().splitlines()]
        self.assertEqual(rows[-1][2], "%s [not-overlayable: %s]" % (self.REASON, why))

    def test_standard_root_passes_without_the_flag(self):
        self.fake_bluejay()
        img = self.image("ok.img", 1 << 20, b"\0\0\0\0")
        self.std_root(subprocess.run(["sha256sum", img], capture_output=True, text=True, check=True).stdout.split()[0])
        rc, out = self.flash(img)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("FLASH NOT STANDARD", out)
        self.assertNotIn("fflash-userdata GAP: no standard root", out)

    def test_missing_standard_root_file_is_a_loud_gap(self):   # rule 7: no silent skip
        self.fake_bluejay()
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
        self.assertEqual(rc, 0, out)
        self.assertIn("fflash-userdata GAP: no standard root sha256 in ", out)

    def test_flashes_today_counts_todays_lines_only(self):
        self.fake_bluejay()
        with open(os.path.join(self.t, "reasons.tsv"), "w") as f:
            f.write("2020-01-01T00:00:00Z\t%s\told day\n" % ("a" * 64))
            f.write("%s\t%s\tearlier today\n" % (time.strftime("%Y-%m-%dT00:00:01Z", time.gmtime()), "b" * 64))
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS flashed userdata flashes_today=2 ", out)

    def test_every_refusal_and_failed_flash_ends_with_a_result_line(self):
        rc, out = self.flash(self.image("big.img", (self.mib + 1) << 20))
        self.assertEqual((rc, self.stdout.strip().splitlines()[-1].split(" ")[:2]), (1, ["RESULT:", "FAIL"]), out)
        r = subprocess.run(["bash", os.path.join(self.t, "tools", "fflash-userdata.sh"), os.path.join(self.t, "nonexistent.img"),
                            "--yes-erase-userdata", "--reason", self.REASON], capture_output=True, text=True, timeout=60)   # usage: exit 2
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1].split(" ")[:2]), (2, ["RESULT:", "ERROR"]), r.stdout + r.stderr)
        self.exe(os.path.join(self.t, "bin", "fastboot"),   # the flash itself fails
                 '#!/bin/sh\ncase "$*" in "getvar product") echo "product: bluejay" ;; "getvar unlocked") echo "unlocked: yes" ;; "flash userdata"*) exit 1 ;; esac\nexit 0\n')
        rc, out = self.flash(self.image("ok.img", 1 << 20, b"\0\0\0\0"))
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.stdout.strip().splitlines()[-1][:19], "RESULT: FAIL rc=1 (", out)
        self.assertFalse(os.path.exists(os.path.join(self.t, "reasons.tsv")), "a failed flash records no reason")


if __name__ == "__main__":
    unittest.main()
