"""Runs tools/test_kernel_hooks.sh (pre-commit trunk guard + patch numbers, commit-msg trailer/role, install-hooks, claude-settings.json, 19 mutants in
`unshare -r -m -p -f --mount-proc`; scratch repos only, ~35 s). This wrapper is how the gate (tools/gate-unit-shards: every tools/test_*.py) picks the .sh up (TP-054)."""
import pathlib, subprocess, sys, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_kernel_hooks.sh"


class KernelHooks(unittest.TestCase):
    def test_kernel_hooks(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=1800)   # T-4119: 143 cases + 47 mutants, 513-790 s in recent gates, 778 s alone at load 25, over 900 s in a loaded gate (GATE FAIL c475d866)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        if "mutants=SKIPPED" in r.stdout:   # rule 8: never a silent weaker run
            sys.stderr.write("KERNEL-HOOKS TEST WARNING: mutants were SKIPPED (unshare unavailable here); run tools/test_kernel_hooks.sh on a host that has it\n")


if __name__ == "__main__":
    unittest.main()
