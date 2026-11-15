#!/usr/bin/env python3
"""Tests for tools/run-preflight: one case per antipattern that motivates a check (fixture repo + run dirs in a tempdir)."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import importlib.machinery, importlib.util, json, os, re, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "run-preflight")
GOOD = "debug=0x8 cpus=8 -noprogress sweettouchpoke=m:10c00640=0=10;d:100;m:10c0180c=5=f;"

def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)

class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        r = self.root = self.t.name
        for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", r, *c], check=True)
        put(r + "/f", "x")
        put(r + "/.gitignore", "shim/\nlogs/\nlock/\nlock.*\n*.txt\nresearch/\n")
        subprocess.run(["git", "-C", r, "add", "f", ".gitignore"], check=True)
        subprocess.run(["git", "-C", r, "commit", "-qm", "c"], check=True)
        self.h = subprocess.run(["git", "-C", r, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        put(r + "/shim/out/gate.result", "GATE PASS %s 123\n" % self.h)
        put(r + "/known.txt", "debug\txnu\ncpus\txnu\nnoprogress\txnu\nsweettouchpoke\tpatch:0093\n")
        put(r + "/extra.txt", "")
        put(r + "/flashed.txt", "9223c0f2aa" + "0" * 54 + " 2026-10-24T13:01:14Z /x.img\n")   # tools/fflash-userdata.sh: a full sha256
        put(r + "/research/ipsw-x/d22ap.adt", "adt")   # gitignored via research/ below; c_adt only needs it to resolve
        self.env = dict(os.environ, PREFLIGHT_ROOT=r, PREFLIGHT_KNOWN=r + "/known.txt", PREFLIGHT_EXTRA=r + "/extra.txt",
                        FLASH_STATE=r + "/flashed.txt", FLASH_REASONS=r + "/reasons.tsv", RUNS_ROOT=r, PHONE_LOCK_DIR=r + "/lock")
        # xrun only checks that CAM_HOST is present (tools/xrun:45); a TEST-NET-1 fixture removes the dependency on a developer .env
        self.env.setdefault("CAM_HOST", "192.0.2.1")

    def tearDown(self):
        self.t.cleanup()

    def pf(self, *a):
        p = subprocess.run([sys.executable, TOOL, *a], capture_output=True, text=True, env=self.env)
        return p.returncode, p.stdout

    def xrun(self, args=GOOD, *more):
        return self.pf("--", "tools/xrun", "--kernel", "K", "--restart", "60", "--args", args, *more)

    def test_pass(self):
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.rstrip().endswith("RESULT: PASS"), out)

    def cmdline_pad(self, total, *more):
        """--args value that makes the built boot-args line exactly `total` bytes for `tools/xrun --args V *more` (base measured through
        xrun --print-bootargs, the same source c_cmdline uses: includes the sweetremote=1 xrun adds itself, AP-154)"""
        base = subprocess.run(["bash", os.path.join(HERE, "xrun"), "--print-bootargs", "--restart", "60", "--args", "sweettouch=0 x=1", *more], capture_output=True, text=True).stdout
        n = len(base.split("boot-args: ", 1)[1].strip())   # includes " sweettouch=0 x=1" (sweettouch=0: the touch defaults of 2026-11-11 stay out of the implicit list)
        put(self.root + "/known.txt", "debug\txnu\ncpus\txnu\nnoprogress\txnu\nsweettouch\txnu\nx\txnu\n")   # the padding boot-arg is named x
        return "sweettouch=0 x=1 x=" + "p" * (total - n - 3)   # " x=" (3 bytes) + padding

    def test_cmdline_limit_matches_mkpayload(self):
        sys.path.insert(0, HERE)
        import mkpayload
        self.assertEqual(mkpayload.BOOT_LINE_LENGTH, 2048)
        loader = importlib.machinery.SourceFileLoader("rp", TOOL); spec = importlib.util.spec_from_loader("rp", loader)
        m = importlib.util.module_from_spec(spec); loader.exec_module(m)
        self.assertEqual(m.BOOT_LINE_LENGTH, mkpayload.BOOT_LINE_LENGTH)

    def test_cmdline_2047_ok_2048_refused(self):
        rc, out = self.xrun(self.cmdline_pad(2047))
        self.assertIn("cmdline  PASS 2047/2047 bytes", out)
        rc, out = self.xrun(self.cmdline_pad(2048))
        self.assertEqual(rc, 1, out)
        self.assertIn("cmdline  FAIL boot-args are 2048 bytes, the limit is 2047", out)
        self.assertIn("RESULT: FAIL cmdline:", out)

    def test_cmdline_1066_bytes(self):
        rc, out = self.xrun(self.cmdline_pad(2090))
        self.assertIn("cmdline  FAIL boot-args are 2090 bytes, the limit is 2047 (mkpayload refuses; 43 over)", out)

    def test_cmdline_counts_xrun_implicit_sweetremote(self):   # AP-154 / TP-040: g112 preflight said 597/607, mkpayload refused 611 (same 4-over case, field now 2048)
        pad = self.cmdline_pad(2037, "--no-remote-reset", "--no-wdt", "--no-chg", "--no-epoch")   # 2037 bytes without the args xrun adds itself (--no-wdt/--no-chg: the default sweetwdt=60 of autorecover 2026-11-09 and sweetchgi2c/icl of charging 2026-11-10)
        rc, out = self.xrun(pad, "--early", "--expect", "x", "--stop-on", "x", "--no-wdt", "--no-chg", "--no-epoch")
        self.assertEqual(rc, 1, out)
        self.assertIn("cmdline  FAIL boot-args are 2051 bytes, the limit is 2047 (mkpayload refuses; 4 over); implicit: sweetremote=1 (+14)", out)
        rc, out = self.xrun(pad, "--early", "--expect", "x", "--no-remote-reset", "--no-wdt", "--no-chg", "--no-epoch")
        self.assertIn("cmdline  PASS 2037/2047 bytes\n", out)   # --no-remote-reset: xrun adds nothing
        rc, out = self.xrun(pad, "--no-usbnet", "--no-wdt", "--no-chg", "--no-epoch")   # no USB link, no remote reset (tools/xrun:50): no implicit arg
        self.assertNotIn("implicit:", [l for l in out.splitlines() if l.startswith("cmdline")][0])
        rc, out = self.xrun(pad + " sweetremote=1", "--no-wdt", "--no-chg", "--no-epoch")   # given explicitly: counted once, not doubled
        self.assertIn("cmdline  FAIL boot-args are 2051 bytes", out)
        self.assertNotIn("implicit: sweetremote", out)
        rc, out = self.xrun(self.cmdline_pad(2047, "--no-wdt", "--no-chg", "--no-epoch"), "--full", "--duration", "60", "--no-wdt", "--no-chg", "--no-epoch")   # tools/xrun:50 adds it to EVERY usbnet run, not only early stops
        self.assertIn("cmdline  PASS 2047/2047 bytes; implicit: sweetremote=1 (+14)", out)

    def test_cmdline_unknown_xrun_option_fails(self):   # xrun's own parser is the source: a flag it does not know cannot pass the preflight
        rc, out = self.xrun(GOOD, "--no-such-flag")
        self.assertEqual(rc, 1, out)
        self.assertIn("cmdline  FAIL xrun --print-bootargs refused the flags: unknown option --no-such-flag", out)

    def test_adt_empty_file_refused(self):   # review dev-speed-gaps-r1 F5: a zero-byte d22ap.adt is no ADT
        open(self.root + "/research/ipsw-x/d22ap.adt", "w").close()
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("adt      FAIL %s/research/ipsw-x/d22ap.adt missing or empty" % self.root, out)

    def test_adt_research_link_missing(self):   # AP-144: gpu-gate10 had no research/ link, three runs with a /product-less ADT
        os.remove(self.root + "/research/ipsw-x/d22ap.adt")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        # the fixture repo is its own main clone: no self-link "fix" (review dev-speed-gaps-r1 F5)
        self.assertIn("adt      FAIL %s/research/ipsw-x/d22ap.adt missing or empty in the kernel clone itself: restore research/ipsw-x (AP-144)" % self.root, out)
        self.assertNotIn("ln -s", out)
        self.assertIn("RESULT: FAIL adt:", out)
        rc, out = self.xrun(GOOD, "--adt", "/x/A.adt")
        self.assertIn("adt      PASS --adt given", out)
        self.assertEqual(self.pf("--", "tools/probe-run", "--timeout", "999")[1].count("adt      SKIP"), 1)

    def test_cmdline_passes_overlay_and_wlanfw_through(self):
        d = tempfile.mkdtemp(dir=self.root); os.makedirs(d + "/usr/lib"); put(d + "/usr/lib/x.dylib", "x")
        rc, out = self.xrun(GOOD, "--overlay", d)
        self.assertRegex(out, r"cmdline  PASS \d+/2047 bytes")
        rc, out = self.xrun(GOOD, "--wlanfw", "/nonexistent/fw.bin")   # needs --wlan-hold: build-xnu-image.sh refuses, so does the preflight
        self.assertIn("cmdline  FAIL xrun --print-bootargs refused the flags: --wlanfw needs --wlan-hold", out)
        rc, out = self.xrun(GOOD, "--wlanfw", "/nonexistent/fw.bin", "--wlan-hold", "--wlan-level", "2")
        self.assertRegex(out, r"cmdline  PASS \d+/2047 bytes")

    def test_ap10_poke_without_semicolon(self):
        rc, out = self.xrun("sweettouchpoke=m:10c0180c=5=f")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL syntax:", out)

    def test_gen_bootargs_patches_only(self):   # 21i 2026-10-29: el2/b2-report passed the gate with sweetel2 (patch 0180) missing from the file
        import hashlib
        pin = lambda xnu: "# xnu-rows-sha256 %s (tools/run-preflight --gen-bootargs from the pristine tree; do not edit)\n" % \
            hashlib.sha256("\n".join(sorted(xnu)).encode()).hexdigest()
        xnu = ["cpus", "debug", "noprogress"]
        rows = "".join("%s\txnu\n" % n for n in xnu)
        put(self.root + "/xnu/patches/0180-x.patch", "+++ b/k.c\n+  PE_parse_boot_argn(\"sweetel2\", &v, sizeof(v));\n")
        put(self.root + "/xnu/patches/0093-y.patch", "+  PE_parse_boot_argn(\"sweettouchpoke\", b, 64);\n+  PE_parse_boot_argn(\"debug\", &d, 4);\n")
        known = self.root + "/known.txt"
        put(known, pin(xnu) + rows + "sweettouchpoke\tpatch:0093\n")
        rc, out = self.pf("--gen-bootargs", "--check", "--patches-only")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL bootargs-known.txt stale", out)
        good = pin(xnu) + rows.replace("noprogress\txnu\n", "noprogress\txnu\nsweetel2\tpatch:0180\n") + "sweettouchpoke\tpatch:0093\n"
        put(known, good)
        rc, out = self.pf("--gen-bootargs", "--check", "--patches-only")
        self.assertEqual(rc, 0, out)   # debug stays an xnu row: the pristine name wins, as in the full walk
        self.assertIn("patches-only", out)
        # review bootargs-gate-r1 F1: a hand-added or deleted xnu row cannot pass (pinned hash of the pristine xnu rows)
        for bad in (good.replace("cpus\txnu\n", "cpus\txnu\nfakearg\txnu\n"), good.replace("cpus\txnu\n", ""), good.split("\n", 1)[1]):
            put(known, bad)
            rc, out = self.pf("--gen-bootargs", "--check", "--patches-only")
            self.assertEqual(rc, 1, out)
            self.assertIn("xnu-rows-sha256", out)
        put(known, "sweetel2\tpatch:0180\n")
        self.assertEqual(self.pf("--gen-bootargs", "--check", "--patches-only")[0], 2)   # no xnu rows: refuse, never a pass
        put(known, good)   # F2: the write refusal is tested with a valid file, which must stay unchanged
        rc, out = self.pf("--gen-bootargs", "--patches-only")
        self.assertEqual(rc, 2, out)
        self.assertIn("needs --check", out)
        self.assertEqual(open(known).read(), good)
        rc, out = self.pf("--gen-bootargs", "--check", "--patches-only", "--pristine", self.root)   # F3: not silently ignored
        self.assertEqual(rc, 2, out)

    def test_unknown_bootarg(self):
        rc, out = self.xrun(GOOD + " nosuchthing=1")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL bootargs:", out)
        self.assertIn("nosuchthing", out)
        put(self.root + "/extra.txt", "nosuchthing  # cited somewhere\n")
        self.assertEqual(self.xrun(GOOD + " nosuchthing=1")[0], 0)

    def test_ap27_expect_after_stop_row(self):
        rc, out = self.pf("--", "tools/probe-run", "--expect", "LINK R36 row=1407 rd=", "--expect", "summary stopped=refused at=S36 row=(1398|1405)")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL reach:", out)
        rc, out = self.pf("--", "tools/probe-run", "--expect", "LINK R36 row=1397 rd=", "--expect", "summary stopped=refused at=S36 row=(1398|1405)")
        self.assertEqual(rc, 0, out)

    # CR-08: the R4:F1 form (docs/reviews/W3A-WLAN-LINK-REVIEW-4.md:12,186-188 in the super repo) is the FAIL fixture, the fixed form the PASS fixture
    BAD_FORM = "LINK summary reached=S23"
    FIXED_FORM = "LINK summary reached=S23 stopped=none restore=ok"

    def form(self, expect, *pre):
        return self.pf(*pre, "--", "tools/probe-run", "--expect", expect)

    def test_cr08_summary_expect_must_be_fault_aware(self):
        rc, out = self.form(self.BAD_FORM)
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL form:", out)
        self.assertIn("restore=ok", out)
        self.assertEqual(self.form(self.FIXED_FORM)[0], 0)
        rc, out = self.form("LINK summary reached=S23 restore=ok")        # no stopped=
        self.assertEqual(rc, 1, out)
        self.assertIn("stopped=", out)
        self.assertEqual(self.form("LINK summary stopped=none")[0], 1)     # no restore=ok
        self.assertEqual(self.form("LINK summary stopped=refused at=S36 row=(1398|1405) restore=ok")[0], 0)
        self.assertEqual(self.form("LINK R36 row=1397 rd=")[0], 0)         # not a summary expect

    def test_cr08_writer_needs_changed_vs(self):
        rc, out = self.form(self.FIXED_FORM, "--writer")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL form:", out)
        self.assertEqual(self.form(self.FIXED_FORM, "--writer", "--changed-vs", "last-good")[0], 1)
        self.assertEqual(self.form(self.FIXED_FORM, "--writer", "--changed-vs", "w3a-n23: HDR_W4 bit 16 only")[0], 0)
        self.assertEqual(self.form(self.FIXED_FORM)[0], 0)                 # a read-only run needs no field

    def test_cr08_mutants_are_killed(self):
        """CR-02: each clause of c_form is needed. A mutant of the tool source (every occurrence replaced) must flip one of the form cases."""
        cases = [(self.BAD_FORM, False, None, "FAIL"), ("LINK summary restore=ok", False, None, "FAIL"), ("LINK summary stopped=none", False, None, "FAIL"),
                 (self.FIXED_FORM, True, None, "FAIL"), (self.FIXED_FORM, True, "x:", "FAIL"), (self.FIXED_FORM, True, "a: b", "PASS"), (self.FIXED_FORM, False, None, "PASS")]
        src = open(TOOL).read()
        muts = [("restore clause", '"restore=ok" not in p', "False"), ("stopped clause", 'if not re.search(r"stopped=(none\\b|\\S+.*\\brow=)", p):', "if False:"),
                ("writer clause", "if not m:", "if False:"), ("summary filter", 'if "LINK summary" not in p:', "if True:")]
        def verdicts(text):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "rp")
                with open(path, "w") as f:
                    f.write(text)
                ldr = importlib.machinery.SourceFileLoader("rpm", path)
                m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rpm", ldr)); ldr.exec_module(m)
                return [m.c_form("probe-run", {"--expect": [e]}, w, c)[0] for e, w, c, _ in cases]
        want = [c[3] for c in cases]
        self.assertEqual(verdicts(src), want)
        for name, old, new in muts:
            self.assertGreaterEqual(src.count(old), 1, "mutant target missing: " + name)
            try:
                got = verdicts(src.replace(old, new))
            except Exception as _err:   # a mutant that crashes is killed
                print(f"test_run_preflight: mutant {name} crashed (killed): {type(_err).__name__}: {_err}", file=sys.stderr)
                continue
            self.assertNotEqual(got, want, "mutant survived: " + name)

    def probe_dir(self, name, secs, args):
        d = self.root + "/logs/probe/" + name
        put(d + "/report.txt", "RESULT: PASS x\n"); put(d + "/run.args", args + "\n")
        put(d + "/fboot.log", "f"); put(d + "/usb.txt", "u")
        os.utime(d + "/fboot.log", (1000, 1000)); os.utime(d + "/usb.txt", (1000 + secs, 1000 + secs))

    def test_timeout_too_short_vs_history(self):   # review B2: the n9/n10 runs have DIFFERENT labels and the same build args
        self.probe_dir("20261023T065219Z-w3a-n8_", 178, "--wlan-link=8 --wlan-probe")
        a = ("--build-args", "--wlan-link=9 --wlan-probe", "--label", "w3a-n9")
        rc, out = self.pf("--", "tools/probe-run", *a, "--timeout", "180")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL timeout:", out)
        rc, out = self.pf("--", "tools/probe-run", *a, "--timeout", "230")   # between mx (178) and mx + margin (238): still FAIL (B6)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.pf("--", "tools/probe-run", *a, "--timeout", "600")[0], 0)
        self.assertEqual(self.pf("--", "tools/probe-run", "--build-args", "--other", "--timeout", "5")[0], 0)   # other form: no history, SKIP

    def test_timeout_default_when_omitted(self):   # --wlan-link defaults to 600 in tools/probe-run, --touch-probe to 240, else 60
        self.probe_dir("20261023T065219Z-x_", 100, "--pstore-fast --touch-probe=run")
        rc, out = self.pf("--", "tools/probe-run", "--touch-probe=run")   # default 240 >= 100 + 60
        self.assertEqual(rc, 0, out)
        self.probe_dir("20261023T065220Z-y_", 200, "--pstore-fast --touch-probe=run")
        rc, out = self.pf("--", "tools/probe-run", "--touch-probe=run")   # 240 < 200 + 60
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL timeout:", out)

    def test_timeout_default_wlan_link_600(self):   # review round 2 B4: default_timeout must give 600 for --wlan-link
        self.probe_dir("20261023T065218Z-w_", 400, "--wlan-link=9")
        self.assertEqual(self.pf("--", "tools/probe-run", "--build-args", "--wlan-link=9")[0], 0)   # 600 >= 400 + 60 (a 60 s default would FAIL)
        self.probe_dir("20261023T065219Z-w_", 560, "--wlan-link=9")
        rc, out = self.pf("--", "tools/probe-run", "--build-args", "--wlan-link=9")   # 600 < 560 + 60
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL timeout:", out)

    def test_timeout_stats_uses_all_worktrees(self):   # review round 2 B4: history lives in other worktrees too
        ldr = importlib.machinery.SourceFileLoader("rp2", TOOL)
        m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rp2", ldr)); ldr.exec_module(m)
        seen = []
        def fake(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="[]\n", stderr="")
        real = m.subprocess.run; m.subprocess.run = fake
        try:
            m.c_timeout("probe-run", {}, 60, ["--build-args", "--wlan-link=9"])
        finally:
            m.subprocess.run = real
        self.assertTrue(seen and "--all-worktrees" in seen[0], seen)

    def test_stale_gate_hash(self):
        put(self.root + "/shim/out/gate.result", "GATE PASS deadbee 123\n")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate:", out)
        put(self.root + "/shim/out/gate.result", "GATE FAIL %s x 123\n" % self.h)
        self.assertEqual(self.xrun()[0], 1)
        os.remove(self.root + "/shim/out/gate.result")
        self.assertEqual(self.xrun()[0], 1)

    def test_gate_tree_changed_after_gate(self):   # review B3
        self.assertEqual(self.xrun()[0], 0)
        put(self.root + "/f", "changed")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate: tree changed", out)

    def test_gate_waiver(self):   # user one-off waiver (2026-10-27, F3): only the stamp check is skipped, loudly
        put(self.root + "/shim/out/gate.result", "GATE FAIL %s x 123\n" % self.h)
        e = self.env
        self.env = dict(e, PREFLIGHT_GATE_WAIVER="short")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn(">= 10 characters", out)
        self.env = dict(e, PREFLIGHT_GATE_WAIVER="user 2026-10-27 F3 once")
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertIn("GATE WAIVED: user 2026-10-27 F3 once", out)
        rc, out = self.xrun("debug=0x8 bogusarg=1")   # every other check still runs
        self.assertEqual(rc, 1, out)
        self.assertNotIn("FAIL gate", out)

    def test_gate_waiver_mark_in_xrun(self):   # rule 8: the waiver lands in the run summary next to the runplan bypass
        src = open(os.path.join(HERE, "xrun")).read()
        self.assertIn('[ -z "${PREFLIGHT_GATE_WAIVER:-}" ] || $R mark "$D" gate_waiver="$PREFLIGHT_GATE_WAIVER"', src)
        self.assertLess(src.index("gate_waiver="), src.index('plock_take xrun "$lockwait"'))

    # ---- T-f32e (user order 2026-11-02): a RAM boot starts after gate-lint-first + the quick gate on the exact head; the formal gate stays for a flash and a trunk merge
    def quick(self, line=None, lint=True):
        put(self.root + "/shim/out/gate.result", line or "GATE PASS %s quick: ran 3 of 9 steps 123\n" % self.h)
        if lint is True:
            lint = "LINT-FIRST PASS %s steps=11 skipped=1\n" % self.h
        if lint:
            put(self.root + "/shim/out/lint-first.result", lint)

    def test_gate_quick_admits_ram_boot(self):
        self.quick()
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertIn("admitted-by=quick", out)
        rc, out = self.pf("--gate-check", "ram")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS gate admitted-by=quick", out)

    def test_gate_quick_without_lint_first_is_refused(self):   # mutant: drop the lint-first half
        self.quick(lint=False)
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate: no shim/out/lint-first.result", out)

    def test_gate_quick_with_stale_or_failed_lint_first_is_refused(self):
        self.quick(lint="LINT-FIRST PASS deadbee steps=11\n")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL gate: newest LINT-FIRST PASS is deadbee", out)
        self.quick(lint="LINT-FIRST FAIL %s steps=11\n" % self.h)
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("gate-lint-first result is FAIL", out)
        self.quick(lint="LINT-FIRST PASS %s+dirty steps=11\n" % self.h)
        self.assertEqual(self.xrun()[0], 1)

    def test_gate_quick_stale_head_or_dirty_tree_is_refused(self):   # the quick gate must be on the EXACT head, tree clean
        self.quick("GATE PASS deadbee quick: ran 3 of 9 steps 123\n")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate: newest GATE PASS is deadbee", out)
        self.quick()
        put(self.root + "/f", "changed")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("tree changed since the gate", out)
        self.quick("GATE FAIL %s quick: unit 123\n" % self.h)
        self.assertEqual(self.xrun()[0], 1)

    def test_gate_formal_is_admitted_by_formal(self):
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertIn("admitted-by=formal", out)
        self.quick("GATE PASS %s quick: selected all steps, ran full 123\n" % self.h, lint=False)   # check-all.sh counts it as a full run
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertIn("admitted-by=formal", out)

    def test_gate_quick_flash_is_refused_formal_flash_allowed(self):   # --gate-check flash is what tools/fflash-userdata.sh runs
        self.quick()
        rc, out = self.pf("--gate-check", "flash")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate: the newest GATE PASS", out)
        self.assertIn("a userdata flash needs the formal gate", out)
        put(self.root + "/shim/out/gate.result", "GATE PASS %s 123\n" % self.h)
        rc, out = self.pf("--gate-check", "flash")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS gate admitted-by=formal", out)

    def test_gate_check_waiver_and_usage(self):
        self.quick(lint=False)
        e = self.env
        self.env = dict(e, PREFLIGHT_GATE_WAIVER="user 2026-11-02 once")
        rc, out = self.pf("--gate-check", "flash")
        self.assertEqual(rc, 0, out)
        self.assertIn("admitted-by=waiver", out)
        self.env = e
        self.assertEqual(self.pf("--gate-check", "bogus")[0], 2)

    def test_xrun_marks_admitted_by_in_the_summary(self):   # rule 8: the run summary names the gate that admitted it
        src = open(os.path.join(HERE, "xrun")).read()
        self.assertIn('--gate-check ram', src)
        self.assertIn('$R mark "$D" admitted_by="${admitted:-unknown}"', src)
        self.assertLess(src.index("admitted_by="), src.index('plock_take xrun "$lockwait"'))
        rep = open(os.path.join(HERE, "xrun_report.py")).read()
        self.assertIn('"admitted-by=%s', rep)

    def test_extra_entry_needs_citation(self):   # review B7
        put(self.root + "/extra.txt", "nosuchthing\n")
        rc, out = self.xrun(GOOD + " nosuchthing=1")
        self.assertEqual(rc, 1, out)
        self.assertIn("without citation", out)

    def test_ap27_expect_equal_to_stop_row(self):   # review B6: late = r >= s
        rc, out = self.pf("--", "tools/probe-run", "--expect", "LINK R36 row=1398 rd=", "--expect", "summary stopped=refused at=S36 row=(1398|1405)")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL reach:", out)

    def test_root_mismatch(self):
        self.assertEqual(self.pf("--root-sha", "9223c0f2", "--", "tools/xrun", "--restart", "60", "--args", GOOD)[0], 0)
        rc, out = self.pf("--root-sha", "ffff", "--", "tools/xrun", "--restart", "60", "--args", GOOD)
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL root:", out)

    def test_root_erased_line_fails(self):   # AP-179: an `erased` line (tools/fboot-android) is not an iOS root, even for a sha prefix "e"
        with open(self.root + "/flashed.txt", "a") as f:
            f.write("erased 2026-10-28T18:51:00Z fboot-android\n")
        for sha in ("9223c0f2", "e"):
            rc, out = self.pf("--root-sha", sha, "--", "tools/xrun", "--restart", "60", "--args", GOOD)
            self.assertEqual(rc, 1, out)
            self.assertIn("RESULT: FAIL root: newest flashed-userdata.txt line is not an iOS root flash: 'erased 2026-10-28T18:51:00Z fboot-android'", out)

    # ---- --userdata-check (tools/xrun before the build, AP-179) ----
    def ud(self, **env):
        p = subprocess.run([sys.executable, TOOL, "--userdata-check"], capture_output=True, text=True, env=dict(self.env, **env))
        return p.returncode, p.stdout, p.stderr

    def test_userdata_ios_line_pass(self):
        rc, out, _ = self.ud()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.splitlines()[-1].startswith("RESULT: PASS userdata 9223c0f2aa"), out)

    def test_userdata_erased_line_error(self):
        with open(self.root + "/flashed.txt", "a") as f:
            f.write("erased 2026-10-28T18:51:00Z fboot-android\n\n")   # newest NON-EMPTY line counts
        rc, out, _ = self.ud()
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: ERROR userdata: newest flashed-userdata.txt line is 'erased 2026-10-28T18:51:00Z fboot-android' "
                         "(Android/erased userdata: re-flash the iOS root with tools/fflash-userdata.sh first)")

    def test_userdata_ios_after_erased_pass(self):   # re-flashed after the golden erase
        with open(self.root + "/flashed.txt", "a") as f:
            f.write("erased 2026-10-28T18:51:00Z fboot-android\n" + "ab" * 32 + " 2026-10-28T19:09:46Z /x.img\n")
        self.assertEqual(self.ud()[0], 0)

    def test_userdata_missing_or_bad_file_error(self):
        rc, out, _ = self.ud(FLASH_STATE=self.root + "/nonexistent.txt")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR userdata: newest flashed-userdata.txt line is '<missing or empty file>'", out)
        put(self.root + "/short.txt", "9223c0f2aa 2026-10-24T13:01:14Z /x.img\n")   # not a full sha256
        self.assertEqual(self.ud(FLASH_STATE=self.root + "/short.txt")[0], 2)

    def test_userdata_bypass_short_reason_refused(self):
        rc, out, err = self.ud(FLASH_STATE=self.root + "/nonexistent.txt", XRUN_USERDATA_BYPASS="short")
        self.assertEqual(rc, 2, out)
        self.assertIn("XRUN_USERDATA_BYPASS needs a reason of >= 10 characters", out)
        self.assertNotIn("USERDATA BYPASS", err)

    def test_userdata_bypass_ok_is_loud(self):
        rc, out, err = self.ud(FLASH_STATE=self.root + "/nonexistent.txt", XRUN_USERDATA_BYPASS="android userdata wanted for test X")
        self.assertEqual(rc, 0, out)
        self.assertIn("USERDATA BYPASS: android userdata wanted for test X", err)
        self.assertIn("RESULT: PASS userdata bypassed", out)

    def test_userdata_check_and_bypass_mark_in_xrun(self):   # read from source: before the build and the lock; the bypass mark runs for real
        src = open(os.path.join(HERE, "xrun")).read()
        i = src.index('userdata=$("${pf[@]}" --userdata-check) || { printf \'%s\\n\' "$userdata"; exit 2; }')
        self.assertLess(i, src.index('"$bxibin" --kernel'))
        self.assertIn("pf=(python3 -I tools/run-preflight)", src)   # sim seam XRUN_RUN_PREFLIGHT (tools/test_sim_seams.sh): the default is the real preflight
        self.assertLess(src.index('userdata_bypass="$XRUN_USERDATA_BYPASS"'), src.index('plock_take xrun "$lockwait"'))
        line = [l for l in src.splitlines() if l.startswith('[ -z "${XRUN_USERDATA_BYPASS:-}" ]')]
        self.assertEqual(len(line), 1)
        d = os.path.join(self.root, "rundir")
        os.makedirs(d)
        subprocess.run(["bash", "-c", 'R="python3 %s/xrun_report.py"; D="$1"; %s' % (HERE, line[0]), "x", d], check=True,
                       env=dict(self.env, XRUN_USERDATA_BYPASS="android userdata wanted for test X"))
        self.assertEqual(json.load(open(d + "/run.json"))["userdata_bypass"], "android userdata wanted for test X")

    def test_fboot_android_erase_marker_then_userdata_refused(self):   # FIX 3a end to end: fake fastboot on PATH, patched-PIN copy (no override option)
        w = self.root + "/fb"
        img = w + "/img.bin"
        put(img, "fake boot image")
        pin = subprocess.run(["sha256sum", img], capture_output=True, text=True).stdout.split()[0]
        src = open(os.path.join(HERE, "fboot-android")).read()
        put(w + "/k/tools/fboot-android", re.sub(r"(?m)^PIN=[0-9a-f]*", "PIN=" + pin, src, count=1))
        os.chmod(w + "/k/tools/fboot-android", 0o755)
        os.symlink(os.path.join(HERE, "phone-lock"), w + "/k/tools/phone-lock")
        put(w + "/bin/fastboot", "#!/bin/bash\necho \"fastboot $*\" >> \"$FAKE_LOG\"\n"
            "[ \"$1\" = devices ] && { printf 'FAKESN1\\tfastboot\\n'; exit 0; }\n"
            "case \"$3 $4\" in 'getvar product') echo 'product: bluejay' >&2 ;; 'getvar unlocked') echo 'unlocked: yes' >&2 ;;\n"
            " 'getvar current-slot') echo 'current-slot: b' >&2 ;; 'getvar is-userspace') echo 'is-userspace: no' >&2 ;;\n"
            " 'erase userdata') exit \"${FAKE_ERASE_RC:-0}\" ;; esac\nexit 0\n")
        os.chmod(w + "/bin/fastboot", 0o755)
        os.makedirs(w + "/home")
        env = dict(self.env, PATH=w + "/bin:" + os.environ["PATH"], HOME=w + "/home", FAKE_LOG=w + "/calls.log")

        def fb(**more):
            return subprocess.run([sys.executable, os.path.join(HERE, "phone-lock"), "run", "--owner", "t", "--wait", "5", "--",
                                   w + "/k/tools/fboot-android", "--image", img, "--erase-userdata", "--yes-erase-userdata"],
                                  capture_output=True, text=True, env=dict(env, **more))
        p = fb()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("fastboot -s FAKESN1 erase userdata", open(w + "/calls.log").read())
        last = open(self.root + "/flashed.txt").read().splitlines()[-1]
        self.assertRegex(last, r"^erased \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ fboot-android$")
        self.assertEqual(self.ud()[0], 2)   # xrun now refuses XNU on the Android userdata
        p = fb(FAKE_ERASE_RC="1")   # a failed erase is marked too (a partial erase is not an iOS root either)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertEqual(len([l for l in open(self.root + "/flashed.txt") if l.startswith("erased ")]), 2)
        p = fb(FLASH_STATE=w + "/no/such/dir/flashed.txt")   # an unwritable marker is loud and on the RESULT line
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("FBOOT GAP: erase marker not written", p.stderr)
        res = [l for l in p.stdout.splitlines() if l.startswith("RESULT: PASS booted")]   # the last line is phone-lock's own RESULT
        self.assertEqual(len(res), 1, p.stdout)
        self.assertIn("erased_userdata=1 gaps=1 ERASE MARKER NOT WRITTEN", res[0])

    def test_lock_held_by_other(self):
        lib = os.path.join(HERE, "phone-lock")
        p = subprocess.Popen([sys.executable, lib, "run", "--owner", "other", "--", "sleep", "30"], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if os.path.exists(self.root + "/lock/info.json"):
                    break
                subprocess.run(["sleep", "0.1"])
            rc, out = self.xrun()
            self.assertEqual(rc, 1, out)
            self.assertIn("RESULT: FAIL lock:", out)
        finally:
            p.terminate(); p.wait()

    def test_bad_args(self):
        for a in ([], ["--", "tools/other"], ["--bogus", "--", "tools/xrun"]):
            rc, out = self.pf(*a)
            self.assertEqual(rc, 2, out)
            self.assertIn("RESULT: ERROR", out)

    def test_generator_names(self):
        ldr = importlib.machinery.SourceFileLoader("rp", TOOL)
        m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rp", ldr)); ldr.exec_module(m)
        src = ('PE_parse_boot_argn("a_arg", &x, 4); PE_parse_boot_argn("-flag", &y, 4); TUNABLE(int, v, "t_arg", 1);\n'
               'TUNABLE_DT(uint32_t, p, "/chosen", "prop", "dt_arg", 0, TUNABLE_DT_NONE);\n'
               'TUNABLE_STR(s, 8, "str_arg", "d"); PE_parse_boot_argn(name_var, &z, 4); /* comment "x" */')
        self.assertEqual(m.names_in(src), {"a_arg", "flag", "t_arg", "dt_arg", "str_arg"})

    # --intent: per-intent required/forbidden flags and boot-args (tools/run-intents.json), checked by tools/xrun before any build
    def xr(self, *a):   # real tools/xrun, no kernel file: it reaches the "--kernel FILE is required" line only if the intent check passed
        p = subprocess.run([os.path.join(HERE, "xrun"), "--kernel", "/nonexistent", "--restart", "60", *a], capture_output=True, text=True, env=dict(self.env, XRUN_NO_RUNPLAN="intent tests, not a run"), cwd=os.path.dirname(HERE))
        return p.returncode, p.stdout + p.stderr

    # AGENTS rule 9: run plan required (c_runplan, reached through the --intent-check call of tools/xrun)
    METHOD = "Phase: P-T1\nBlock: TB\nLower tiers tried:\n- T0: host test passes 12/12\n- T1: replay score=100 first_divergence=none\n- T2: no hot reload for the shim, RAM boot is T3\n\n"
    PLAN = "# Run plan: %s\n\n" + METHOD + "## Questions\n| # | Q | Pred | Src | Rx | Next |\n|---|---|---|---|---|---|\n| 1 | is X? | yes | guess | `A+` `B` `S` | x |\n\n## Command\n`tools/xrun --label L --expect 'CMD1'`\n\n## Other\nOTHERONLY\n"

    SPEC = "# SPEC-TB\n\n## 0. Strategy\nPort the whole OpenBSD driver as a unit and write only the glue; rejected: re-deriving the sequence from a trace, because a driver exists.\nReplay tool: tools/trace-diff\n\n## 1. Unknowns\n"

    def rp(self, plans, *a, env=None, intent=""):
        d = os.path.join(self.root, "plans")
        os.makedirs(d, exist_ok=True)
        for n, t in plans.items():
            put(os.path.join(d, n), t)
        if not os.path.exists(os.path.join(self.root, "specs", "SPEC-TB.md")):
            put(os.path.join(self.root, "specs", "SPEC-TB.md"), self.SPEC)
        put(os.path.join(self.root, "tools", "trace-diff"), "#!/bin/sh\n")
        put(os.path.join(self.root, "GOALS.md"), "| id | demo | oracle | risk | budget |\n| P-T1 | a frame | T1 replay | R1 | 2 boots / 1 day |\n| P-NB | a frame | T1 | R1 | no budget |\n| P-T5 | a frame | T1 replay | R1 | 5 boots / 3 days |\n")
        e = {k: v for k, v in self.env.items() if k != "XRUN_NO_RUNPLAN"}
        e["PIXOS_RUNPLAN_DIR"] = d
        e.update(env or {})
        p = subprocess.run([sys.executable, TOOL, "--intent-check", intent, "--label", a[0], "--", "tools/xrun", "--restart", "60", *a[1:]], capture_output=True, text=True, env=e)
        return p.returncode, p.stdout + p.stderr

    def test_method_ok(self):   # T-336d c_block/c_tiers/c_budget: the fixture plan names P-T1 (budget 2) and fills T0-T2
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out)

    def test_method_no_phase_refused(self):
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("Phase: P-T1\n", "")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("c_block", out)

    def test_method_unknown_phase_refused(self):
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("P-T1", "P-ZZ")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("phase P-ZZ has no row", out)

    def test_method_phase_without_budget_refused(self):
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("P-T1", "P-NB")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("'N boots' budget", out)

    def test_method_tiers_refused(self):
        for cut in ("- T1: replay score=100 first_divergence=none\n", "Lower tiers tried:\n"):
            rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace(cut, "")}, "g1", "--expect", "A+")
            self.assertNotEqual(rc, 0, out); self.assertIn("c_tiers", out)
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("host test passes 12/12", "<result>")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("T0", out)

    def test_method_budget_refused_and_recut(self):
        plans = {"g%d.md" % i: self.PLAN % ("g%d" % i) for i in (1, 2, 3)}
        rc, out = self.rp(plans, "g3", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("c_budget: 3 run plans", out)
        plans["g3.md"] = plans["g3.md"].replace("Phase: P-T1\n", "Phase: P-T1\nRe-cut by: T-12ab\n")
        rc, out = self.rp(plans, "g3", "--expect", "A+")
        self.assertEqual(rc, 0, out)

    def spec_case(self, spec, plan_edit=None):
        put(os.path.join(self.root, "specs", "SPEC-TB.md"), spec) if spec is not None else None
        plan = self.PLAN % "g1"
        return self.rp({"g1.md": plan_edit(plan) if plan_edit else plan}, "g1", "--expect", "A+")

    def test_spec_strategy_refused(self):   # T-cfb7 c_block part 2
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("Block: TB\n", "")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("no 'Block: <BLOCK>' line", out)
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("Block: TB", "Block: NOPE")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("block NOPE has no", out); self.assertIn("SPEC-NOPE.md", out)
        rc, out = self.spec_case(self.SPEC.replace("## 0. Strategy", "## 0. Intro"))
        self.assertNotEqual(rc, 0, out); self.assertIn("no '## 0. Strategy' section", out)
        rc, out = self.spec_case(self.SPEC.replace("Port the whole OpenBSD driver as a unit and write only the glue; rejected: re-deriving the sequence from a trace, because a driver exists.\n", "<strategy>\n"))
        self.assertNotEqual(rc, 0, out); self.assertIn("empty or a placeholder", out)
        rc, out = self.spec_case(self.SPEC.replace("Replay tool: tools/trace-diff\n", ""))
        self.assertNotEqual(rc, 0, out); self.assertIn("no 'Replay tool: tools/<name>' line", out)
        rc, out = self.spec_case(self.SPEC.replace("tools/trace-diff", "tools/not-there"))
        self.assertNotEqual(rc, 0, out); self.assertIn("tools/not-there", out); self.assertIn("does not exist", out)
        rc, out = self.spec_case(self.SPEC.replace("Replay tool: tools/trace-diff", "Replay tool: tools/../etc/passwd"))
        self.assertNotEqual(rc, 0, out)
        rc, out = self.spec_case(self.SPEC)   # the placeholder spec is back to the good one
        self.assertEqual(rc, 0, out)

    def test_spec_strategy_section_ends_at_next_heading(self):   # a Replay tool line in s.1 does not count for s.0
        rc, out = self.spec_case(self.SPEC.replace("Replay tool: tools/trace-diff\n\n## 1. Unknowns\n", "\n## 1. Unknowns\nReplay tool: tools/trace-diff\n"))
        self.assertNotEqual(rc, 0, out); self.assertIn("no 'Replay tool", out)

    RUN = "\n## Result\n| # | PASS/FAIL | observed | divergence |\n|---|---|---|---|\n| 1 | %s | x | y |\n"

    def runs(self, first, second, pred2=None):
        """Two earlier RUN plans g1, g2 of phase P-T5 (budget 5 boots; Result row 1 = `first`, `second`; g1 older than g2 by mtime)."""
        d = os.path.join(self.root, "plans")
        os.makedirs(d, exist_ok=True)
        for i, (k, res) in enumerate((("g1", first), ("g2", second))):
            t = (self.PLAN % k).replace("P-T1", "P-T5") + self.RUN % res
            if k == "g2" and pred2:
                t = t.replace("| 1 | is X? | yes |", "| 1 | is X? | %s |" % pred2)
            put(os.path.join(d, k + ".md"), t)
            os.utime(os.path.join(d, k + ".md"), (1000 + i, 1000 + i))

    def third(self, extra=""):
        return (self.PLAN % "g3").replace("Phase: P-T1\n", "Phase: P-T5\n" + extra)

    def test_nine_b_third_boot_refused_after_two_surprise_fails(self):   # PNB-9b
        self.runs("FAIL", "FAIL")
        rc, out = self.rp({"g3.md": self.third()}, "g3", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("c_nine_b", out); self.assertIn("g1.md, g2.md", out)

    def test_nine_b_non_triggers(self):   # one PASS among the last two, or the FAIL was predicted ('FAIL: ...' prediction)
        for a, b, pred in (("FAIL", "PASS", None), ("PASS", "FAIL", None), ("FAIL", "FAIL", "FAIL: U7 unsettled")):
            self.runs(a, b, pred2=pred)
            rc, out = self.rp({"g3.md": self.third()}, "g3", "--expect", "A+")
            self.assertEqual(rc, 0, "%s %s %s: %s" % (a, b, pred, out))

    def test_nine_b_only_the_last_two_run_plans_count(self):
        self.runs("FAIL", "FAIL")
        d = os.path.join(self.root, "plans")
        put(os.path.join(d, "g2b.md"), (self.PLAN % "g2b").replace("P-T1", "P-T5") + self.RUN % "PASS"); os.utime(os.path.join(d, "g2b.md"), (1002, 1002))
        rc, out = self.rp({"g3.md": self.third()}, "g3", "--expect", "A+")   # g1 FAIL, g2 FAIL, g2b PASS: the last two are g2, g2b
        self.assertEqual(rc, 0, out)

    def test_nine_b_non_pass_statuses_and_non_numeric_ids_count(self):   # review cfb7-r1 F2
        for st in ("FAILED", "PARTIAL", "BLOCKED", "NO BOOT", "**FAIL**"):
            self.runs(st, st)
            rc, out = self.rp({"g3.md": self.third()}, "g3", "--expect", "A+")
            self.assertNotEqual(rc, 0, "%s: %s" % (st, out)); self.assertIn("c_nine_b", out)
        d = os.path.join(self.root, "plans")
        self.runs("FAIL", "FAIL")
        for k in ("g1", "g2"):   # row ids that are not numbers: 'C5' and '10, 11'
            t = open(os.path.join(d, k + ".md")).read().replace("| 1 | FAIL |", "| C5 | FAIL |").replace("| 1 | FAILED |", "| C5 | FAILED |")
            put(os.path.join(d, k + ".md"), t)
        os.utime(os.path.join(d, "g1.md"), (1000, 1000)); os.utime(os.path.join(d, "g2.md"), (1001, 1001))
        rc, out = self.rp({"g3.md": self.third()}, "g3", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("c_nine_b", out)

    def test_nine_b_template_lines_copied_verbatim_do_not_lift_it(self):   # review cfb7-r1 F1: the TEMPLATE hint text must not satisfy the escape
        self.runs("FAIL", "FAIL")
        for l in ("Spec first: (only when the last two RUN plans of this phase each have a FAIL row; else delete this line)", "Spec first: a b c", "Spec first: <!-- one two three four -->", "Re-cut by: (only once the phase's boot budget is spent: the plan ticket T-xxxx)"):
            rc, out = self.rp({"g3.md": self.third(l + "\n")}, "g3", "--expect", "A+")
            self.assertNotEqual(rc, 0, "%r: %s" % (l, out))

    def test_phase_ids_of_other_shapes_and_prefix_collisions(self):   # review cfb7-r1 F4 (G-7) and P-T1 vs P-T10
        put(os.path.join(self.root, "GOALS-GPU.md"), "| G-7 | compute job | result buffer | R1 | 3 boots |\n| P-T10 | a frame | T1 | R1 | 9 boots |\n")
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("P-T1", "G-7").replace("Block: TB\n", "Block: TB\nPLAN-CHECK-WAIVER: user 2026-11-08 \"fixture: this test is about phase ids\"\n")}, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.root, "plans")
        plans = {"g1.md": self.PLAN % "g1", "g4.md": self.PLAN % "g4"}
        for i in (2, 3):
            plans["g%d.md" % i] = (self.PLAN % ("g%d" % i)).replace("P-T1", "P-T10")
        rc, out = self.rp(plans, "g4", "--expect", "A+")   # P-T1 has 2 plans (budget 2); the two P-T10 plans must not count
        self.assertEqual(rc, 0, out)

    def test_min_words_and_thin_goals_rows_and_missing_tiers(self):   # review cfb7-r1 test gaps
        nine = self.SPEC.replace("Port the whole OpenBSD driver as a unit and write only the glue; rejected: re-deriving the sequence from a trace, because a driver exists.", "one two three four five six seven eight nine")
        rc, out = self.spec_case(nine)
        self.assertNotEqual(rc, 0, out); self.assertIn("empty or a placeholder", out)
        rc, out = self.spec_case(nine.replace("nine", "nine ten"))
        self.assertEqual(rc, 0, out)
        for cut in ("- T2: no hot reload for the shim, RAM boot is T3\n", ):
            rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace(cut, "")}, "g1", "--expect", "A+")
            self.assertNotEqual(rc, 0, out); self.assertIn("T2", out)
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("host test passes 12/12", "ok fine")}, "g1", "--expect", "A+")
        self.assertNotEqual(rc, 0, out); self.assertIn("T0", out)
        put(os.path.join(self.root, "GOALS-X.md"), "| P-E1 | | T1 | R1 | 2 boots |\n| P-E2 | a frame | | R1 | 2 boots |\n")
        for ph in ("P-E1", "P-E2"):
            rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace("P-T1", ph)}, "g1", "--expect", "A+")
            self.assertNotEqual(rc, 0, "%s: %s" % (ph, out)); self.assertIn("lacks a demo, an oracle", out)

    def test_nine_b_escapes(self):
        self.runs("FAIL", "FAIL")
        for line, ok in (("Spec first: SPEC-TB s.1 U7 settled\n", True), ("Re-cut by: T-12ab\n", True), ("Spec first: ok\n", False), ("Spec first: we fixed the whole thing\n", False), ("Spec first: <what>\n", False)):
            rc, out = self.rp({"g3.md": self.third(line)}, "g3", "--expect", "A+")
            self.assertEqual(rc == 0, ok, "%r: %s" % (line, out))

    def test_nine_b_unrun_plans_do_not_count(self):   # a plan with no filled Result row was never run
        self.runs("FAIL", "FAIL")
        d = os.path.join(self.root, "plans")
        put(os.path.join(d, "g2.md"), (self.PLAN % "g2").replace("P-T1", "P-T5")); os.utime(os.path.join(d, "g2.md"), (1001, 1001))
        rc, out = self.rp({"g3.md": self.third()}, "g3", "--expect", "A+")
        self.assertEqual(rc, 0, out)

    def test_method_pre_method_exempt_is_loud(self):
        rc, out = self.rp({"g1.md": (self.PLAN % "g1").replace(self.METHOD, ""), ".pre-method.txt": "# old\ng1.md\n"}, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out); self.assertIn("METHOD EXEMPT", out)

    # T-322d: every phone boot arms a USB-free self-reset, or the run plan says why not (c_recovery, reached through the same --intent-check call)
    def rc_(self, plan_text, *argv, label="rec1"):
        d = os.path.join(self.root, "plans")
        os.makedirs(d, exist_ok=True)
        put(os.path.join(d, label + ".md"), plan_text)
        put(os.path.join(self.root, "specs", "SPEC-TB.md"), self.SPEC)   # c_block fixtures (the same ones rp() writes)
        put(os.path.join(self.root, "tools", "trace-diff"), "#!/bin/sh\n")
        put(os.path.join(self.root, "GOALS.md"), "| id | demo | oracle | risk | budget |\n| P-T1 | a frame | T1 replay | R1 | 2 boots / 1 day |\n")
        e = {k: v for k, v in self.env.items() if k != "XRUN_NO_RUNPLAN"}
        e["PIXOS_RUNPLAN_DIR"] = d
        p = subprocess.run([sys.executable, TOOL, "--intent-check", "", "--label", label, "--", "tools/xrun", *argv], capture_output=True, text=True, env=e)
        return p.returncode, p.stdout + p.stderr

    def test_recovery_missing_refused(self):   # Done-when 1: a plan without a recovery path -> FAIL with the reason
        rc, out = self.rc_(self.PLAN % "rec1", "--expect", "A+")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR recovery: no USB-free recovery path armed", out)
        self.assertIn("--restart S", out)
        self.assertIn("no-recovery:", out)

    def test_recovery_restart_flag_passes_and_says_scope(self):   # Done-when 1: with one -> PASS; rule 8: the PASS says what it does not cover
        for argv in (["--restart", "120"], ["--restart", "90,bootloader"], ["--args", "cpus=8 sweetrestart=300"]):
            rc, out = self.rc_(self.PLAN % "rec1", *argv, "--expect", "A+")
            self.assertEqual(rc, 0, out)
            self.assertIn("RECOVERY: sweetrestart armed (XNU-stage timer only; a shim-stage hang before XNU is NOT covered", out)

    def test_recovery_bad_values_refused(self):
        for argv in (["--restart", "0"], ["--restart", ""], ["--restart", "abc"], ["--restart", "-5"], ["--restart", "60,fastboot"], ["--args", "sweetrestart=0"], ["--args", "sweetrestart="]):
            rc, out = self.rc_(self.PLAN % "rec1", *argv, "--expect", "A+")
            self.assertEqual(rc, 2, (argv, out))
            self.assertIn("recovery:", out)

    def test_recovery_last_value_wins(self):   # PE_parse_boot_arg: the last one wins; a good value followed by 0 is NOT armed
        rc, out = self.rc_(self.PLAN % "rec1", "--restart", "60", "--restart", "0", "--expect", "A+")
        self.assertEqual(rc, 2, out)

    def test_recovery_plan_escape_is_loud(self):
        rc, out = self.rc_(self.PLAN % "rec1" + "\nno-recovery: shim-stage probe, a reset would hide the panic screen\n", "--expect", "A+")
        self.assertEqual(rc, 0, out)
        self.assertIn("NO RECOVERY:", out)
        self.assertIn("shim-stage probe", out)

    def test_recovery_plan_escape_needs_a_reason(self):
        rc, out = self.rc_(self.PLAN % "rec1" + "\nno-recovery: none\n", "--expect", "A+")
        self.assertEqual(rc, 2, out)

    def test_recovery_in_full_check_form(self):   # the `-- tools/xrun` form lists the check too
        rc, out = self.pf("--", "tools/xrun", "--kernel", "K", "--args", GOOD)
        self.assertEqual(rc, 1, out)
        self.assertIn("recovery FAIL", out)
        rc, out = self.xrun()
        self.assertIn("recovery PASS", out)

    def test_runplan_by_name(self):
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "A+", "--expect-absent", "B")
        self.assertEqual(rc, 0, out)

    def test_runplan_evidence_order_warns_when_missing(self):   # PORT-METHOD "Evidence order": WARN, never a refusal
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out)
        self.assertIn("run-preflight: RUNPLAN WARN:", out)
        self.assertIn("Evidence order", out)

    def test_runplan_evidence_order_present_is_quiet(self):
        plan = (self.PLAN % "g1") + "\nEvidence order: steps 1-2 cannot answer because the HAL logs the switch only at runtime\n"
        rc, out = self.rp({"g1.md": plan}, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("RUNPLAN WARN", out)

    def test_runplan_evidence_order_unfilled_placeholder_warns(self):
        plan = (self.PLAN % "g1") + "\nEvidence order: steps 1-2 cannot answer because <one line: why>\n"
        rc, out = self.rp({"g1.md": plan}, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out)
        self.assertIn("RUNPLAN WARN", out)

    def test_runplan_by_first_line_whole_word(self):
        plans = {"both.md": self.PLAN % "g108-a + g109-b"}
        self.assertEqual(self.rp(plans, "g109-b")[0], 0)
        rc, out = self.rp(plans, "g109")   # a prefix of a label is not a match
        self.assertEqual(rc, 2, out)

    def test_runplan_missing(self):
        rc, out = self.rp({}, "g1")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR runplan: no plan for label 'g1'", out)

    def test_runplan_no_label(self):
        rc, out = self.rp({"run.md": self.PLAN % "run"}, "run")
        self.assertEqual(rc, 2, out)
        self.assertIn("needs --label", out)

    def test_runplan_empty_questions_table(self):
        rc, out = self.rp({"g1.md": "# g1\n\n## Questions\n| # | Q |\n|---|---|\n\n## Other\n| 1 | x |\n"}, "g1")
        self.assertEqual(rc, 2, out)
        self.assertIn("no question = no run", out)

    def test_runplan_expect_not_in_plan(self):
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "A+", "--expect-absent", "ZZZ")
        self.assertEqual(rc, 2, out)
        self.assertIn("--expect-absent 'ZZZ'", out)
        self.assertNotIn("'A+'", out)

    def test_runplan_label_path_refused(self):
        for bad in ("../x", "/abs/x", ".hidden", "a b"):
            rc, out = self.rp({}, bad)
            self.assertEqual(rc, 2, out)
            self.assertIn("must match", out)

    def test_runplan_bypass_marked_before_lock(self):
        src = open(os.path.join(HERE, "xrun")).read()
        self.assertLess(src.index('runplan_bypass="$XRUN_NO_RUNPLAN"'), src.index('plock_take xrun "$lockwait"'))

    def test_runplan_bypass_short_reason_refused(self):
        rc, out = self.rp({}, "g1", env={"XRUN_NO_RUNPLAN": "short"})
        self.assertEqual(rc, 2, out)
        self.assertIn(">= 10", out)

    def test_runplan_bypass_ok_is_loud(self):
        rc, out = self.rp({}, "g1", env={"XRUN_NO_RUNPLAN": "plan lives in thread X"})
        self.assertEqual(rc, 0, out)
        self.assertIn("run-preflight: RUNPLAN BYPASS: plan lives in thread X", out)

    def test_runplan_bypass_mark_lands_in_run_json(self):   # F4: execute the real mark line of tools/xrun, not a grep of the header
        line = [l for l in open(os.path.join(HERE, "xrun")).read().splitlines() if l.startswith('[ -z "${XRUN_NO_RUNPLAN:-}" ]')]
        self.assertEqual(len(line), 1)
        d = os.path.join(self.root, "rundir")
        os.makedirs(d)
        subprocess.run(["bash", "-c", 'R="python3 %s/xrun_report.py"; D="$1"; %s' % (HERE, line[0]), "x", d], check=True, env=dict(self.env, XRUN_NO_RUNPLAN="plan lives in thread X"))
        self.assertEqual(json.load(open(d + "/run.json"))["runplan_bypass"], "plan lives in thread X")

    def test_xrun_refuses_without_plan_before_any_phone_action(self):   # F5: no bypass, missing plan dir -> rc 2 at the check (xrun:44), before kernel/build/lock
        e = {k: v for k, v in self.env.items() if k != "XRUN_NO_RUNPLAN"}
        e["PIXOS_RUNPLAN_DIR"] = os.path.join(self.root, "noplans")
        p = subprocess.run([os.path.join(HERE, "xrun"), "--kernel", "/nonexistent", "--label", "g1", "--expect", "A"], capture_output=True, text=True, env=e, cwd=os.path.dirname(HERE))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 2, out)
        self.assertIn("runplan: no plan for label 'g1'", out)
        self.assertNotIn("--kernel FILE is required", out)

    def test_runplan_exact_name_beats_first_line(self):   # F1
        plans = {"a-old.md": "# Plan zzz mentions g1\n", "g1.md": self.PLAN % "g1"}
        rc, out = self.rp(plans, "g1", "--expect", "A+")
        self.assertEqual(rc, 0, out)

    def test_runplan_ambiguous_first_line(self):   # F1
        plans = {"a.md": self.PLAN % "g7 first", "b.md": self.PLAN % "g7 second"}
        rc, out = self.rp(plans, "g7")
        self.assertEqual(rc, 2, out)
        self.assertIn("ambiguous", out)
        self.assertIn("a.md", out)
        self.assertIn("b.md", out)

    def test_runplan_regex_must_be_in_questions_or_command(self):   # F2
        plans = {"g1.md": self.PLAN % "g1"}
        self.assertEqual(self.rp(plans, "g1", "--expect", "CMD1")[0], 0)   # Command section
        self.assertEqual(self.rp(plans, "g1", "--expect", "S")[0], 0)      # Questions section
        rc, out = self.rp(plans, "g1", "--expect", "OTHERONLY")            # only in "## Other"
        self.assertEqual(rc, 2, out)
        rc, out = self.rp(plans, "g1", "--expect", "g1")                   # only in the title
        self.assertEqual(rc, 2, out)

    def test_runplan_empty_regex_refused(self):   # F2
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "")
        self.assertEqual(rc, 2, out)
        self.assertIn("(empty)", out)

    def test_runplan_stop_on_fail_on_checked(self):   # F3
        plans = {"g1.md": self.PLAN % "g1"}
        self.assertEqual(self.rp(plans, "g1", "--stop-on", "A+", "--fail-on", "B")[0], 0)
        for f in ("--stop-on", "--fail-on"):
            rc, out = self.rp(plans, "g1", f, "NOTINPLAN")
            self.assertEqual(rc, 2, out)
            self.assertIn(f + " 'NOTINPLAN'", out)

    def test_runplan_questions_row_needs_filled_cells(self):   # F7
        for row in ("| | | | | | |", "| 1 | q | | | | |", "| 1 | q | pred | src | | |"):
            t = "# g1\n\n## Questions\n| # | Q | P | S | R | N |\n|---|---|---|---|---|---|\n" + row + "\n"
            rc, out = self.rp({"g1.md": t}, "g1")
            self.assertEqual(rc, 2, out)
            self.assertIn("filled Prediction and regex", out)

    # F3 run script and plan in their real formats (wlan-runF3-0136, 2026-10-27): built here, never read from the host's /tmp (AP-152)
    F3_SCRIPT = """#!/bin/bash
A=(tools/xrun --kernel K --restart 360 --wlan-hold --wlan-level 2 --wlanfw /x/wlanfw.bin --args "sweetwlan=10 sweetlogts=1" --intent wlan --label wlan-runF3-0136
 --expect 'SWEET WLAN: stage 3 level 10 done shared=0x[0-9a-f]+ version=7 polls=[0-9]+ halted=1'
 --expect 'F3 live window 0: cfg 0x84 0, \\[0x200000\\] (?!0xffffffff,)(0|0x[0-9a-f]+),'
 --expect 'F2 mailbox data: host to dongle \\[0x[0-9a-f]+\\] = (0|0x[0-9a-f]+), dongle to host'
 --expect 'restore space 1 win 0 off 0x84 := 0 ok'
 --expect-absent 'F3 \\[0x200000\\] reads all ones'
 --expect-absent 'F2 (ringmem_ptr|rings_info_ptr) .* not a readable range'
 --fail-on 'SWEET WLAN: stage 3 level 10 CA7 ' --fail-on 'Kernel trap at|panic\\(cpu|panic:|Assertion failed' --duration 400)
tools/run-preflight -- "${A[@]}" || exit 1
exec env PREFLIGHT_GATE_WAIVER="user waive once" "${A[@]}"
"""
    F3_PLAN = """# Run plan: wlan-runF3-0136 (fixture)

## Questions (each one answered by this run; no question = no run)
| # | Question | Prediction | Source of the prediction | Log line that answers it (regex) | If wrong, next step |
|---|---|---|---|---|---|
| 1 | Level-10 stage done? | `halted=1` | F1 [obs] | `SWEET WLAN: stage 3 level 10 done shared=0x[0-9a-f]+ version=7 polls=[0-9]+ halted=1` | stop |
| 2 | BAR2 live? | not ffffffff | F2 [obs] | `F3 live window 0: cfg 0x84 0, \\[0x200000\\] (?!0xffffffff,)(0|0x[0-9a-f]+),` | stop |

## Command
Script strings (verbatim from the script):
- `--expect` `SWEET WLAN: stage 3 level 10 done shared=0x[0-9a-f]+ version=7 polls=[0-9]+ halted=1`
- `--expect` `F3 live window 0: cfg 0x84 0, \\[0x200000\\] (?!0xffffffff,)(0|0x[0-9a-f]+),`
- `--expect` `F2 mailbox data: host to dongle \\[0x[0-9a-f]+\\] = (0|0x[0-9a-f]+), dongle to host`
- `--expect` `restore space 1 win 0 off 0x84 := 0 ok`
- `--expect-absent` `F3 \\[0x200000\\] reads all ones`
- `--expect-absent` `F2 (ringmem_ptr|rings_info_ptr) .* not a readable range`
- `--fail-on` `SWEET WLAN: stage 3 level 10 CA7 `
- `--fail-on` `Kernel trap at|panic\\(cpu|panic:|Assertion failed`
"""

    def test_runplan_real_f3_argv(self):   # the F3 script's argv (array parsed as the run script holds it, env-prefixed exec line) against a plan in the real format
        import shlex
        txt = self.F3_SCRIPT
        a = txt.index("A=(") + 3   # the array ends at the first ")" + newline; the run-preflight line may carry an env prefix (PREFLIGHT_GATE_WAIVER=...)
        argv = shlex.split(txt[a:txt.index(")\n", a)])
        self.assertEqual(argv.count("--expect"), 4)
        def check(plan):   # what tools/xrun:44 runs: --intent-check wlan --label L -- ARGV
            put(os.path.join(self.root, "plans", "wlan-runF3-0136.md"), plan)
            put(os.path.join(self.root, "plans", ".pre-method.txt"), "wlan-runF3-0136.md\n")   # a real pre-recipe plan: exempt from c_method
            e = {k: v for k, v in self.env.items() if k != "XRUN_NO_RUNPLAN"}
            e["PIXOS_RUNPLAN_DIR"] = os.path.join(self.root, "plans")
            p = subprocess.run([sys.executable, TOOL, "--intent-check", "wlan", "--label", "wlan-runF3-0136", "--", *argv], capture_output=True, text=True, env=e)
            return p.returncode, p.stdout + p.stderr
        rc, out = check(self.F3_PLAN)
        self.assertEqual(rc, 0, out)
        drop = self.F3_PLAN.replace("- `--expect-absent` `F2 (ringmem_ptr|rings_info_ptr) .* not a readable range`\n", "")
        self.assertNotEqual(drop, self.F3_PLAN)
        rc, out = check(drop)   # strict: one regex missing from the plan = refused
        self.assertEqual(rc, 2, out)
        self.assertIn("lacks 1 regex(es) verbatim", out)

    def test_intent_gpu_implies_handover(self):
        rc, out = self.xr("--intent", "gpu", "--label", "gputest26")
        self.assertEqual(rc, 2, out)
        self.assertIn("--kernel FILE is required", out)   # --intent gpu implies --gpu-handover
        rc, out = self.xr("--intent", "gpu", "--gpu-handover", "--label", "gputest26")
        self.assertEqual(rc, 2, out)
        self.assertIn("--kernel FILE is required", out)   # passed the intent check, stopped later
        self.assertNotIn("intent gpu", out)

    def test_intent_timing(self):
        rc, out = self.xr("--intent", "timing", "--args", "cpus=8")
        self.assertEqual((rc, "intent timing: missing sweetlogts=1" in out), (2, True), out)
        rc, out = self.xr("--intent", "timing", "--args", "sweetlogts=1 sweetlog=0")
        self.assertEqual((rc, "forbidden sweetlog=0" in out), (2, True), out)
        rc, out = self.xr("--intent", "timing", "--args", "cpus=8 sweetlogts=1")
        self.assertIn("--kernel FILE is required", out)

    def test_intent_wlan_unknown_and_none(self):
        rc, out = self.xr("--intent", "wlan", "--label", "wd2-1")   # wlan row has no required flag (xrun lacks --wlan-hold): must pass the intent check
        self.assertIn("--kernel FILE is required", out)
        rc, out = self.xr("--label", "wdog")   # `wd` alone no longer implies wlan
        self.assertIn("--kernel FILE is required", out)
        rc, out = self.xr("--label", "wd2-2")
        self.assertEqual(rc, 2, out)
        self.assertIn("suggests --intent wlan", out)
        self.assertIn("unknown --intent nope", self.xr("--intent", "nope")[1])
        rc, out = self.xr("--label", "gputest26")   # no --intent but the label implies one: exit 2 (closes the E23 bypass for scripts calling xrun)
        self.assertEqual(rc, 2, out)
        self.assertIn("suggests --intent gpu (none given): pass --intent gpu", out)
        rc, out = self.xr("--intent", "none", "--label", "gputest26")   # opt-out on a gpu label is exit 2 too
        self.assertEqual(rc, 2, out)
        rc, out = self.xr("--intent", "timing", "--args", "sweetlogts=1", "--label", "gputest26")   # mismatch
        self.assertEqual(rc, 2, out)
        rc, out = self.xr("--intent", "timing", "--args", "sweetlogts=1", "--label", "GPUtest26")   # case-insensitive
        self.assertEqual(rc, 2, out)
        self.env["XRUN_INTENT_OK"] = "1"   # explicit escape for a run that mixes purposes
        rc, out = self.xr("--intent", "none", "--label", "gputest26")
        self.assertIn("--kernel FILE is required", out)
        del self.env["XRUN_INTENT_OK"]
        rc, out = self.xr("--label", "zzz-unknown")   # unknown label, no --intent: unchanged
        self.assertIn("--kernel FILE is required", out)

    def test_intent_label_escalation_mutant(self):
        src = open(TOOL).read()
        old = 'if hint and not (os.environ.get("XRUN_INTENT_OK") == "1" and name):'
        self.assertEqual(src.count(old), 1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rp"); open(path, "w").write(src.replace(old, "if False:"))
            ldr = importlib.machinery.SourceFileLoader("rpm", path)
            m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rpm", ldr)); ldr.exec_module(m); m.INTENTS = os.path.join(HERE, "run-intents.json")
            self.assertEqual(m.c_intent([], "", "gputest26")[0], "PASS")   # mutant: the real tool must differ (asserted below)
        ldr = importlib.machinery.SourceFileLoader("rpr", TOOL)
        m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rpr", ldr)); ldr.exec_module(m)
        self.assertEqual(m.c_intent([], "", "gputest26")[0], "FAIL")
        self.assertEqual(m.c_intent([], "gpu", "gputest26")[0], "PASS")   # --gpu-handover is implied by the intent now
        self.assertEqual(m.c_intent(["--gpu-handover"], "gpu", "gputest26")[0], "PASS")
        self.assertEqual(m.c_intent([], "", "zzz")[0], "PASS")

    def test_intent_in_preflight_check(self):
        rc, out = self.pf("--", "tools/xrun", "--restart", "60", "--kernel", "K", "--args", GOOD, "--intent", "timing")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL intent:", out)

    # ---- platform: unentitled_ios_sim_launch=1 + DEVELOPMENT kernel (research 97 5a.1, pristine kern_exec.c:195-197,7391-7413) ----
    def fake_kernel(self, objroot):
        """a file holding the version string as pristine config/version.c:39 lays it out (observed: kernel-usbroot-probe4.sweet has
        '@(#)VERSION: Darwin Kernel Version 22.6.0: ...; offload-host:xnu.obj/DEVELOPMENT_ARM64_SWEET')"""
        d = tempfile.mkdtemp(); self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))   # outside the fixture repo: the gate check needs it clean
        p = os.path.join(d, "k-" + objroot.replace("/", "_"))
        with open(p, "wb") as f:
            f.write(b"\xcf\xfa\xed\xfe" + b"\0" * 64 + b"@(#)VERSION: Darwin Kernel Version 22.6.0: Mon Oct 26; u:" + objroot.encode() + b"\0tail")
        return p

    def platform_line(self, out):
        return [l for l in out.splitlines() if l.startswith("platform ")]

    def test_platform_pass_development_kernel_default_args(self):   # the real build-xnu-image.sh default line has the boot-arg
        rc, out = self.pf("--", "tools/xrun", "--restart", "60", "--kernel", self.fake_kernel("xnu.obj/DEVELOPMENT_ARM64_SWEET"), "--args", GOOD)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.platform_line(out), ["platform PASS unentitled_ios_sim_launch=1, kernel DEVELOPMENT_ARM64_SWEET"], out)

    def test_platform_release_kernel_refused(self):
        rc, out = self.pf("--", "tools/xrun", "--restart", "60", "--kernel", self.fake_kernel("xnu.obj/RELEASE_ARM64_SWEET"), "--args", GOOD)
        self.assertEqual(rc, 1, out)
        self.assertIn("platform FAIL kernel config RELEASE (RELEASE_ARM64_SWEET), not DEVELOPMENT/DEBUG", out)
        self.assertIn("RESULT: FAIL platform:", out)

    def test_platform_pass_debug_kernel(self):   # review usbroot-hfs-trace-r1 F1
        rc, out = self.pf("--", "tools/xrun", "--restart", "60", "--kernel", self.fake_kernel("xnu.obj/DEBUG_ARM64_SWEET"), "--args", GOOD)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.platform_line(out), ["platform PASS unentitled_ios_sim_launch=1, kernel DEBUG_ARM64_SWEET"], out)

    def test_platform_kernel_without_version_refused(self):
        p = os.path.join(self.root, "shim", "noversion"); put(p, "not a kernel")   # shim/ is gitignored in the fixture
        rc, out = self.pf("--", "tools/xrun", "--restart", "60", "--kernel", p, "--args", GOOD)
        self.assertEqual(rc, 1, out)
        self.assertIn("platform FAIL no '@(#)VERSION:' string in", out)

    def test_platform_bootarg_zero_refused(self):   # a later =0 in --args would win (build-xnu-image.sh:35 comment, review 17 L5)
        put(self.root + "/known.txt", open(self.root + "/known.txt").read() + "unentitled_ios_sim_launch\txnu\n")   # bootargs check passes
        k = self.fake_kernel("xnu.obj/DEVELOPMENT_ARM64_SWEET")
        rc, out = self.pf("--", "tools/xrun", "--restart", "60", "--kernel", k, "--args", GOOD + " unentitled_ios_sim_launch=0")
        self.assertEqual(rc, 1, out)
        self.assertIn("bootargs PASS", out)
        self.assertIn("platform FAIL boot-args have unentitled_ios_sim_launch=1 unentitled_ios_sim_launch=0 (need exactly", out)
        self.assertIn("RESULT: FAIL platform:", out)

    def test_platform_missing_kernel_is_skip_not_pass(self):
        rc, out = self.xrun()
        self.assertIn("platform SKIP unentitled_ios_sim_launch=1 ok; kernel config NOT checked: --kernel K is not a file", out)

    def test_platform_line_without_bootarg_refused(self):   # mutant of build-xnu-image.sh's default line: the arg deleted
        ldr = importlib.machinery.SourceFileLoader("rpp", TOOL)
        m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rpp", ldr)); ldr.exec_module(m)
        k = self.fake_kernel("xnu.obj/DEVELOPMENT_ARM64_SWEET")
        real, _, why = m.built_line(["--kernel", k])
        self.assertIsNone(why)
        self.assertIn(" unentitled_ios_sim_launch=1 ", " " + real + " ")
        m._LINES[("--kernel", k)] = (real.replace("unentitled_ios_sim_launch=1", "").strip(), "none", None)
        st, d = m.c_platform("xrun", ["--kernel", k], {"--kernel": [k]})
        self.assertEqual((st, d.startswith("boot-args lack unentitled_ios_sim_launch")), ("FAIL", True), d)
        m._LINES[("--kernel", k)] = (real + " -unentitled_ios_sim_launch", "none", None)   # bare flag form: not exactly =1
        self.assertEqual(m.c_platform("xrun", ["--kernel", k], {"--kernel": [k]})[0], "FAIL")
        self.assertEqual(m.c_platform("probe-run", [], {})[0], "SKIP")


if __name__ == "__main__":
    unittest.main()
