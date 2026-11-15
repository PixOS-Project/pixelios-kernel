#!/usr/bin/env python3
"""Host test for T-03ff: no phone boot may spin on a panic screen without a logged reason (no phone, no build).
Layers: tools/build-xnu-image.sh (--panic-spin), tools/fboot.sh (image meta args), tools/phone-guard (stale branch roots)."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
GUARD = os.path.join(TOOLS, "phone-guard")
g = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("pg", importlib.machinery.SourceFileLoader("pg", GUARD)))
importlib.machinery.SourceFileLoader("pg", GUARD).exec_module(g)
REASON = "bisect the touch hang"


def build(*a):
    r = subprocess.run([os.path.join(TOOLS, "build-xnu-image.sh"), *a], capture_output=True, text=True, env=dict(os.environ, XNU_IMAGE_ARGS_ONLY="1"), cwd=ROOT)
    return r.returncode, r.stdout, r.stderr


def args_line(out):
    return [l for l in out.splitlines() if l.startswith("boot-args: ")][0]


class BuildImage(unittest.TestCase):
    def test_default_recovers(self):
        rc, out, _ = build()
        self.assertEqual(rc, 0)
        self.assertTrue(args_line(out).startswith("boot-args: debug=0x8 "))

    def test_spin_without_reason_is_refused(self):
        for a in ("debug=0xc", "debug=4", "debug=0x8 debug=0xc", "x=1 debug=0X1c"):
            rc, _, err = build("--args", a)
            self.assertEqual(rc, 2, a)
            self.assertIn("--panic-spin", err)
        rc, _, err = build("--args", "debug=0xc", "--panic-spin", "short")
        self.assertEqual(rc, 2)

    def test_last_debug_wins(self):
        rc, out, _ = build("--args", "debug=0xc debug=0x8")
        self.assertEqual(rc, 0)
        self.assertTrue(args_line(out).endswith("debug=0x8"))

    def test_spin_with_reason_is_logged(self):
        rc, out, err = build("--args", "debug=0xc", "--panic-spin", REASON)
        self.assertEqual(rc, 0)
        self.assertIn("PANIC SPIN: debug=0xc", err)
        self.assertIn("panic-spin: " + REASON, out)

    def test_reason_without_spin_is_loud(self):
        rc, _, err = build("--panic-spin", REASON)
        self.assertEqual(rc, 0)
        self.assertIn("PANIC SPIN GAP", err)

    def test_unparsable_debug_is_refused(self):
        rc, _, err = build("--args", "debug=zz")
        self.assertEqual(rc, 2)
        self.assertIn("not a number", err)


class Fboot(unittest.TestCase):
    def img(self, d, args):
        p = os.path.join(d, "x.img")
        with open(p, "wb") as fh:
            fh.write(b"\0" * 64)
        subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "imgmeta.py"), "write", p, "--kind", "xnu", "--args", args], cwd=ROOT, check=True, capture_output=True)
        return p

    def run_fboot(self, p, *extra):
        return subprocess.run([os.path.join(TOOLS, "fboot.sh"), "--expect", "xnu", p, *extra], capture_output=True, text=True, cwd=ROOT, env=dict(os.environ, PHONE_LOCK_DIR=p + ".nolock"))

    def test_spin_image_needs_reason(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_fboot(self.img(d, "serial=3 debug=0xc cpus=8"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("--panic-spin", r.stderr)
            r = self.run_fboot(self.img(d, "serial=3 debug=0xc cpus=8"), "--panic-spin", REASON)
            self.assertIn("PANIC SPIN: image boots with debug=0xc", r.stderr)   # then stops at the phone lock (not held in this test)
            self.assertNotIn("REFUSED: the image boot-args", r.stderr)

    def test_xnu_image_needs_safety_pre_stamp(self):   # RULES H9
        import hashlib, json, time
        with tempfile.TemporaryDirectory() as d:
            p = self.img(d, "debug=0x8")
            r = self.run_fboot(p)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no safety-check pre pass for this image", r.stderr)
            with open(p + ".safety", "w") as fh:
                json.dump({"sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(), "at": time.time()}, fh)
            r = self.run_fboot(p)
            self.assertIn("SAFETY PASS stamp", r.stderr)
            self.assertNotIn("no safety-check pre pass", r.stderr)   # then stops at the phone lock (not held in this test)

    def test_recovering_image_passes_the_check(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_fboot(self.img(d, "debug=0xc debug=0x8"))
            self.assertNotIn("DB_NMI", r.stderr)


class StaleBranch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def root(self, name, script, safety=True):
        r = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(r, "tools"))
        with open(os.path.join(r, "tools", "build-xnu-image.sh"), "w") as fh:
            fh.write(script)
        open(os.path.join(r, "tools", "xrun"), "w").close()   # a kernel checkout
        if safety:   # RULES H9: a merged root has tools/safety-check
            open(os.path.join(r, "tools", "safety-check"), "w").close()
        return r

    def test_resident_hot_and_live_session_tree(self):   # RULES H9: refuse by the tree that would serve the boot
        import json
        old = self.root("nosafety2", 'args="debug=0x8"\n# --panic-spin\n', safety=False)
        new = self.root("merged2", 'args="debug=0x8"\n# --panic-spin\n')
        for cmd in (f"{old}/tools/resident submit --stream tools --est 60", f"cd {old} && python3 tools/resident start", f"{old}/tools/hot session start --owner a",
                    f"cd {old} && tools/hot join --owner b --kernel k"):
            self.assertIn("safety-check", g.stale_boot(cmd, "/") or "", cmd)
        for cmd in (f"{old}/tools/resident status", f"{old}/tools/resident end --reason x", f"{old}/tools/hot push a b", f"{new}/tools/resident submit --stream tools --est 60",
                    f"{new}/tools/hot session start --owner a"):
            self.assertIsNone(g.stale_boot(cmd, "/"), cmd)
        art = os.path.join(self.tmp.name, "art")
        os.makedirs(os.path.join(art, "resident"))
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"{old}/tools/resident", "_serve"])
        self.addCleanup(p.kill)
        with open(os.path.join(art, "resident", "session.json"), "w") as fh:
            json.dump({"pid": p.pid, "state": "running"}, fh)
        os.environ["ARTIFACTS"] = art
        self.addCleanup(os.environ.pop, "ARTIFACTS", None)
        why = g.stale_boot(f"{new}/tools/resident submit --stream tools --est 60", "/")
        self.assertIn("serves the live resident session", why or "")
        self.assertIn(old, why)
        self.assertIsNone(g.stale_boot(f"{new}/tools/resident status", "/"))
        with open(os.path.join(art, "resident", "session.json"), "w") as fh:
            json.dump({"pid": p.pid, "state": "ended"}, fh)
        self.assertIsNone(g.stale_boot(f"{new}/tools/resident submit --stream tools --est 60", "/"))

    def test_root_without_safety_check_is_denied(self):
        old = self.root("nosafety", 'args="debug=0x8"\n# --panic-spin\n', safety=False)
        for cmd in (f"{old}/tools/xrun --kernel k", f"cd {old} && tools/xrun --kernel k", f"phone-lock run --owner a -- {old}/tools/xrun --kernel k",
                    f"cd {old}; tools/fboot.sh --expect xnu a.img"):
            why = g.stale_boot(cmd, "/")
            self.assertIsNotNone(why, cmd)
            self.assertIn("safety-check", why)
            self.assertIn("merge trunk", why)
        self.assertIsNone(g.stale_boot(f"{old}/tools/xrun --dry-run --from-dir d", "/"))

    def test_stale_root_is_denied_with_the_fix(self):
        old = self.root("old", 'args="debug=0xc serial=3"\n')
        for cmd in (f"{old}/tools/xrun --kernel k", f"cd {old} && tools/xrun --kernel k", f"bash -c 'cd {old} && tools/probe-run --x'",
                    f"cd {old}; tools/fboot.sh --expect xnu a.img", f"phone-lock run --owner a -- {old}/tools/xrun --kernel k", f"nohup {old}/tools/xrun --kernel k &"):
            why = g.stale_boot(cmd, "/")
            self.assertIsNotNone(why, cmd)
            self.assertIn("debug=0xc", why)
            self.assertIn("merge trunk", why)

    def test_merged_root_passes(self):
        new = self.root("new", 'args="debug=0x8"\n# --panic-spin\n')
        for cmd in (f"{new}/tools/xrun --kernel k", f"cd {new} && tools/xrun --kernel k --args 'debug=0xc' --panic-spin x"):
            self.assertIsNone(g.stale_boot(cmd, "/"), cmd)

    def test_not_a_boot(self):
        old = self.root("old", 'args="debug=0xc"\n')
        for cmd in (f"{old}/tools/xrun --dry-run --from-dir d", f"{old}/tools/xrun --print-bootargs x", f"{old}/tools/xrun -h", f"cat {old}/tools/xrun",
                    f"grep xrun {old}/tools/notes", "tools/xrun --kernel k", f"{old}/tools/other --x"):
            self.assertIsNone(g.stale_boot(cmd, "/nonexistent"), cmd)

    def test_relative_tool_uses_the_hook_cwd(self):
        old = self.root("old", 'args="debug=0xc"\n')
        self.assertIsNotNone(g.stale_boot("tools/xrun --kernel k", old))

    def test_hook_protocol(self):
        old = self.root("old", 'args="debug=0xc"\n')
        r = subprocess.run([sys.executable, GUARD], input=json.dumps({"tool_name": "Bash", "cwd": old, "tool_input": {"command": "tools/xrun --kernel k"}}), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("T-03ff", out["permissionDecisionReason"])

    def test_this_checkout_passes(self):
        self.assertIsNone(g.stale_boot(os.path.join(TOOLS, "xrun") + " --kernel k", "/"))


if __name__ == "__main__":
    unittest.main(verbosity=0)
