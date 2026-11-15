#!/usr/bin/env python3
"""tools/sweetprobe_audit.py: its self-test (the mutants of shim/sweetprobe.c, shim/sweetprobe_smc.c and the SLC client of shim/acpm.c) must pass."""
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "sweetprobe_audit.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS sweetprobe_audit self-test", r.stdout)


class Wiring(unittest.TestCase):
    """The sweetprobe audit runs from mifclk_audit.audit (test-bluejay-boot.py may not grow, R29): a lost call would drop the audit silently."""

    def test_mifclk_audit_runs_sweetprobe_audit(self):
        import ast
        tree = ast.parse(open(os.path.join(TOOLS, "mifclk_audit.py"), encoding="utf-8").read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "audit")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "sweetprobe_audit" and n.func.attr == "audit"]
        self.assertEqual(len(calls), 1, "mifclk_audit.audit must call sweetprobe_audit.audit exactly once")
        self.assertEqual(len(calls[0].args), 8, "all eight audit helpers are passed through")


class Mutants(unittest.TestCase):
    """The host test of the probe kills the behavioural mutants (a different PSCI id, another opcode, the queue gate, the room check, the channel 11 request, the 32-byte guard)."""

    def test_host_test_kills_mutants(self):
        import shutil, tempfile
        root = os.path.dirname(TOOLS)
        shim = os.path.join(root, "shim")
        if shutil.which("gcc") is None:
            self.skipTest("no gcc")
        with tempfile.TemporaryDirectory() as td:
            hdr = os.path.join(td, "board_config.h")
            subprocess.run([sys.executable, os.path.join(TOOLS, "boards.py"), "bluejay", hdr], check=True, capture_output=True)

            def run(overrides):
                work = os.path.join(td, "w")
                shutil.rmtree(work, ignore_errors=True)
                shutil.copytree(shim, work, ignore=shutil.ignore_patterns("out"))
                for name, (old, new) in overrides.items():
                    p = os.path.join(work, name)
                    s = open(p).read()
                    assert old in s, (name, old)
                    open(p, "w").write(s.replace(old, new, 1))
                exe = os.path.join(td, "t")
                c = subprocess.run(["gcc", "-O1", "-w", "-DSWEET_PROBE", "-I" + work, "-I" + td, "-include", hdr, "-o", exe,
                                    os.path.join(work, "sweetprobe_host_test.c")], capture_output=True, text=True)
                self.assertEqual(c.returncode, 0, c.stderr)
                return subprocess.run([exe], capture_output=True, text=True).returncode

            self.assertEqual(run({}), 0, "unmutated host test must pass")
            mutants = {
                "psci CPU_OFF id": {"sweetprobe.c": ("#define PSCI_ID_OFF\t\t0x84000002u", "#define PSCI_ID_OFF\t\t0x84000003u")},
                "another opcode": {"acpm.c": ("XM_SLCV, 0u, 0u, ACPM_SLC_PT_VERSION, 0u", "XM_SLCV, 0u, 0u, 0xd003u, 0u")},
                "words 4-7 not zero": {"acpm.c": ("i < 4u ? x->req[i] : 0u", "i < 4u ? x->req[i] : 1u")},
                "guard keeps 16 bytes": {"acpm.c": ("g->slot_hi = slot + eb;", "g->slot_hi = slot + 16;")},
                "no queue gate": {"sweetprobe.c": ("if (h10.rr != h10.rf || h10.sr != h10.sf) {", "if (0) {")},
                "channel 11 requested": {"sweetprobe.c": ("(void)slc_desc(c, ACPM_SLC_ASYNC_CH", "(void)slc_desc(c, ACPM_SLC_CH")},
                "no room check": {"sweetprobe.c": ("if (n + SP_TOK_MAX >= cap) {", "if (0) {")},
                "reply branch removed": {"acpm.c": ("if (mode == XM_SLCV) {", "if (0) {")},
            }
            for label, ov in mutants.items():
                self.assertNotEqual(run(ov), 0, "host test did not kill mutant: " + label)


if __name__ == "__main__":
    unittest.main()
