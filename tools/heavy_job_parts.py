"""Shared runner of the tools/heavy-job host test parts (PLAN-GATE-2X K4a: one 190 s file became 6 files, so the unit shards run them side by side).
Each tools/test_heavy_job*.py is ONE test that runs ONE part script tools/test_heavy_job_<part>.sh (setup + helpers: tools/test_heavy_job_lib.sh).
tools/test_heavy_job.sh runs all parts one after the other (manual use). A part prints `RESULT: PASS cases=N`; a part script that is missing is a failure."""
import os
import pathlib
import re
import subprocess

DIR = pathlib.Path(__file__).resolve().parent
# The formal gate runs the unit shards inside a detached heavy-job scope; heavy-job then has no --wait expiry (T-e8ad). The part scripts must clear these themselves (T-6269),
# so the unit test always runs them as the gate does.
DETACHED_ENV = {"HEAVY_JOB_DETACHED": "pixos-hj-unit-test-1-1", "HEAVY_JOB_NOTRUN_FILE": os.devnull}
PARTS = ("basic", "fifo", "prio", "r3", "r4", "offload", "remote", "stale", "light", "cycle", "route")


def wrapper_name(part):
    return "test_heavy_job.py" if part == "basic" else "test_heavy_job_%s.py" % part


def check_registered(tc):
    """Every part has exactly one wrapper (calling run_part with its own part name) and one script, and no wrapper exists without a part: deleting a wrapper or renaming PARTS entries is a failure,
    not a silently smaller test (review gate-cold-k14 r1 F7; the unit shards' count check compares only with what discover finds)."""
    found = {p.name for p in DIR.glob("test_heavy_job*.py")}
    tc.assertEqual(found, {wrapper_name(p) for p in PARTS}, "wrapper files tools/test_heavy_job*.py must match PARTS %r" % (PARTS,))
    for part in PARTS:
        tc.assertIn('run_part(self, "%s")' % part, (DIR / wrapper_name(part)).read_text(), "%s must run its own part" % wrapper_name(part))
        tc.assertTrue((DIR / ("test_heavy_job_%s.sh" % part)).is_file(), "missing part script test_heavy_job_%s.sh" % part)
    m = re.search(r"^for p in ([a-z0-9 ]+); do$", (DIR / "test_heavy_job.sh").read_text(), re.M)
    tc.assertTrue(m, "test_heavy_job.sh (manual run) has no `for p in PARTS; do` loop")
    tc.assertEqual(tuple(m.group(1).split()), PARTS, "test_heavy_job.sh must run exactly the parts in PARTS")


def run_part(tc, part):
    tc.assertIn(part, PARTS)
    check_registered(tc)
    sh = DIR / ("test_heavy_job_%s.sh" % part)
    tc.assertTrue(sh.is_file(), "missing part script %s" % sh)
    r = subprocess.run(["bash", str(sh)], capture_output=True, text=True, timeout=600, env={**os.environ, **DETACHED_ENV})
    tc.assertEqual(r.returncode, 0, r.stdout + r.stderr)
    tc.assertRegex(r.stdout.rstrip().splitlines()[-1] if r.stdout.strip() else "", r"^RESULT: PASS cases=[0-9]+$", r.stdout + r.stderr)
