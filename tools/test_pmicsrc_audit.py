#!/usr/bin/env python3
"""tools/pmicsrc_audit.py: its self-test (the mutants of shim/pmicsrc.c) must pass."""
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "pmicsrc_audit.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS pmicsrc_audit self-test", r.stdout)


class Wiring(unittest.TestCase):
    """The pmicsrc audit runs from mifclk_audit.audit (test-bluejay-boot.py may not grow, R29): a lost call would drop the audit silently."""

    def test_mifclk_audit_runs_pmicsrc_audit(self):
        import ast
        here = os.path.dirname(os.path.abspath(__file__))
        tree = ast.parse(open(os.path.join(here, "mifclk_audit.py"), encoding="utf-8").read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "audit")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "pmicsrc_audit" and n.func.attr == "audit"]
        self.assertEqual(len(calls), 1, "mifclk_audit.audit must call pmicsrc_audit.audit exactly once")
        self.assertEqual(len(calls[0].args), 8, "all eight audit helpers are passed through")


if __name__ == "__main__":
    unittest.main()
