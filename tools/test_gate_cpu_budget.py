"""GATE_CPU_BUDGET derivation in tools/check-all.sh (AP-186/AP-193), via the GATE_CPU_PRINT_ONLY test hook (no lock, no steps)."""
import os, pathlib, stat, subprocess, tempfile, unittest

SH = pathlib.Path(__file__).resolve().parent / "check-all.sh"
CLEAN = ("GATE_CPU_BUDGET", "GATE_JOBS", "GATE_UNIT_SHARDS", "GATE_MUT_JOBS")


def run(env_extra=None, path=None):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env["GATE_HOST_LOCK"] = "0"   # review gate-cpu-budget-r1 F1: the print-only hook must not queue on the real host gate lock
    env["GATE_CPU_PRINT_ONLY"] = "1"
    env.update(env_extra or {})
    if path:
        env["PATH"] = path + ":" + env["PATH"]
    r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, env=env, timeout=60)
    return r, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


class GateCpuBudget(unittest.TestCase):
    def test_16(self):
        r, l = run({"GATE_CPU_BUDGET": "16"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TEST HOOK", r.stdout)
        self.assertEqual(l, "GATE CPU: budget=16 (env) A.jobs=6 (derived) B.shards=4 (derived) C.mut_jobs=6 (derived)")

    def test_small(self):
        for b in ("4", "1"):
            _, l = run({"GATE_CPU_BUDGET": b})
            self.assertIn("A.jobs=1 (derived) B.shards=1 (derived) C.mut_jobs=1 (derived)", l, b)

    def test_env_wins(self):
        _, l = run({"GATE_CPU_BUDGET": "16", "GATE_JOBS": "2"})
        self.assertIn("A.jobs=2 (env) B.shards=4 (derived) C.mut_jobs=6 (derived)", l)
        _, l = run({"GATE_CPU_BUDGET": "16", "GATE_UNIT_SHARDS": "3"})
        self.assertIn("A.jobs=6 (derived) B.shards=3 (env) C.mut_jobs=6 (derived)", l)
        _, l = run({"GATE_CPU_BUDGET": "16", "GATE_MUT_JOBS": "9"})
        self.assertIn("A.jobs=6 (derived) B.shards=4 (derived) C.mut_jobs=9 (env)", l)

    def test_invalid(self):
        for b in ("0", "abc", "-3", "", "1.5"):
            r, _ = run({"GATE_CPU_BUDGET": b})
            self.assertEqual(r.returncode, 2, b)
            self.assertIn("GATE ERROR GATE_CPU_BUDGET", r.stdout, b)

    def test_default_nproc(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "nproc")
            p.write_text("#!/bin/sh\necho 8\n")
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
            r, l = run(path=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(l, "GATE CPU: budget=8 (nproc) A.jobs=3 (derived) B.shards=2 (derived) C.mut_jobs=3 (derived)")


if __name__ == "__main__":
    unittest.main()
