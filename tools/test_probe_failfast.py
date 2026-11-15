"""Hermetic tests of tools/probe_failfast.py (PLAN-GATE-2X K2a, `test_wlan_wr_mutants.py --probe-failfast N`): a fake runner, no gcc, no phone.
BEHAVIOUR is a list of checks run against any copy of the module; the real module must pass all of them and each honest mutant of probe_failfast.py
(one changed expression: mean for median, an off-by-one percentile, a diff that ignores the full sweep, N=0 accepted, ...) must make at least one fail.
Run: python3 -I tools/test_probe_failfast.py"""
import os
import pathlib
import subprocess
import sys
import types
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import probe_failfast as real

SRC = os.path.join(TOOLS, "probe_failfast.py")

# name -> (ff (status, gcc, test), full (status, gcc, test)); a = early kill, b = middle, c = late, d = survives both, e = survives only the full sweep
FAKE = {
    "a": (("fail", 2.7, 1.0), ("fail", 2.7, 10.0)),
    "b": (("fail", 2.7, 3.0), ("fail", 2.7, 10.0)),
    "c": (("fail", 2.7, 5.0), ("fail", 2.7, 10.0)),
    "d": (("pass", 2.7, 10.0), ("pass", 2.7, 10.0)),
    "e": (("fail", 2.7, 1.0), ("pass", 2.7, 10.0)),      # the kill sets differ: fail-fast kills what the full sweep lets survive
}
CALLS = []


def fake_run(name, hdr, ff):
    CALLS.append((name, hdr, ff))
    st, gcc, test = FAKE[name][0 if ff else 1]
    return st, "msg-" + name, gcc, test


def near(a, b):
    return abs(a - b) < 1e-9


def check_sample(m):
    f = []
    if m.sample(list(range(100)), 4) != [12, 37, 62, 87]:
        f.append("stride sample of 4 of 100 is not [12, 37, 62, 87]: %r" % (m.sample(list(range(100)), 4),))
    if m.sample(list(range(100)), 4) != m.sample(list(range(100)), 4):
        f.append("the sample is not deterministic")
    if m.sample(["x", "y", "z"], 30) != ["x", "y", "z"]:
        f.append("N larger than the list must return the whole list")
    if m.sample(["x", "y", "z"], 3) != ["x", "y", "z"]:
        f.append("N equal to the list must return the whole list")
    if m.sample(list(range(10)), 1) != [5]:
        f.append("a sample of 1 of 10 is the middle item")
    for bad in (0, -1, True, "3", 2.5, None):
        try:
            m.sample([1, 2, 3], bad)
            f.append("N=%r was accepted" % (bad,))
        except ValueError:   # lint-silent: expected the test expects exactly this ValueError; accepting it appends to f
            pass
    return f


def check_percentile(m):
    f = []
    if m.percentile([0.1, 0.3, 0.5], 90) != 0.5:
        f.append("p90 of 3 values must be the 3rd (nearest rank)")
    if m.percentile(list(range(1, 11)), 90) != 9:
        f.append("p90 of 1..10 must be 9")
    if m.percentile([7], 90) != 7 or m.percentile([3, 1, 2], 50) != 2:
        f.append("p90 of one value / p50 of 3,1,2 wrong")
    try:
        m.percentile([], 90)
        f.append("percentile of nothing was accepted")
    except ValueError:   # lint-silent: expected the test expects exactly this ValueError; accepting it appends to f
        pass
    return f


def check_summary(m):
    f = []
    items = [(n, "H" + n) for n in sorted(FAKE)]
    CALLS.clear()
    rows = m.run_probe(items, fake_run)
    if len(CALLS) != 2 * len(items) or [c[2] for c in CALLS] != [True, False] * len(items):
        f.append("each mutant must be run once fail-fast and once full, in that order: %r" % ([c[2] for c in CALLS],))
    if any(c[1] != "H" + c[0] for c in CALLS):
        f.append("the runner got the wrong header")
    s = m.summarize(rows, 957)
    if not near(s["median_ff_cpu"], 5.7):
        f.append("median_ff_cpu %r != 5.7 (the mean is 6.7)" % s["median_ff_cpu"])
    if not near(s["median_full_cpu"], 12.7):
        f.append("median_full_cpu %r != 12.7" % s["median_full_cpu"])
    if s["median_first_bad"] is None or not near(s["median_first_bad"], 0.3):
        f.append("median_first_bad %r != 0.3 (survivors and the ff-only kill carry no first-bad)" % (s["median_first_bad"],))
    if s["p90_first_bad"] is None or not near(s["p90_first_bad"], 0.5):
        f.append("p90_first_bad %r != 0.5" % (s["p90_first_bad"],))
    if s["first_bad_n"] != 3:
        f.append("first_bad_n %r != 3" % s["first_bad_n"])
    if s["kill_set_diff"] != 1 or s["diff_names"] != ["e"]:
        f.append("kill_set_diff %r %r != 1 ['e'] (a mutant that dies only in fail-fast mode)" % (s["kill_set_diff"], s["diff_names"]))
    if s["gcc_share"] is None or not near(s["gcc_share"], 13.5 / 33.5):
        f.append("gcc_share %r != %r" % (s["gcc_share"], 13.5 / 33.5))
    if not near(s["projected_total_cpu"], 957 * 5.7):
        f.append("projected_total_cpu %r != 957 x 5.7 (the full list, not the sample)" % s["projected_total_cpu"])
    if not (near(s["projected_wall"][6], 957 * 5.7 / 6) and near(s["projected_wall"][14], 957 * 5.7 / 14)):
        f.append("projected wall at 6 / 14 jobs wrong: %r" % (s["projected_wall"],))
    return f


def check_no_diff_and_no_first_bad(m):
    f = []
    rows = m.run_probe([("d", "Hd")], fake_run)
    s = m.summarize(rows, 957)
    if s["kill_set_diff"] != 0 or s["median_first_bad"] is not None or s["p90_first_bad"] is not None:
        f.append("a lone survivor: kill_set_diff 0 and no first-bad expected, got %r" % ({k: s[k] for k in ("kill_set_diff", "median_first_bad", "p90_first_bad")},))
    lines, ok = m.format_lines(rows, s)
    if not ok or not lines[-1].startswith("RESULT: PASS") or "median_first_bad=n/a" not in "\n".join(lines):
        f.append("lone survivor must print median_first_bad=n/a and RESULT: PASS: %r" % (lines[-2:],))
    try:
        m.summarize([], 957)
        f.append("summarize([]) was accepted")
    except ValueError:   # lint-silent: expected the test expects exactly this ValueError; accepting it appends to f
        pass
    return f


def check_lines(m):
    f = []
    rows = m.run_probe([(n, "H" + n) for n in sorted(FAKE)], fake_run)
    s = m.summarize(rows, 957)
    lines, ok = m.format_lines(rows, s)
    text = "\n".join(lines)
    if ok or not lines[-1].startswith("RESULT: FAIL") or "kill_set_diff=1" not in lines[-1]:
        f.append("a kill-set difference must give RESULT: FAIL with kill_set_diff=1: %r" % (lines[-1],))
    if "PROBE a ff_cpu=3.70 full_cpu=12.70 ff_status=fail full_status=fail" not in lines[0]:
        f.append("PROBE line wrong: %r" % (lines[0],))
    for want in ("FAILFAST PROBE n=5 median_ff_cpu=5.70", "median_first_bad=0.30", "p90_first_bad=0.50", "kill_set_diff=1",
                 "gcc 40% of the cpu", "projected HEADER-SET-ONLY cpu", "= 957 x 5.70", "HOST LOAD1", "projected wall at 6 jobs = 909 s, 14 jobs = 390 s", "KILL SET DIFF: e"):
        if want not in text:
            f.append("output lacks %r" % want)
    # a mutant with no verdict is loud and fails the probe even when the statuses agree
    rows2 = m.run_probe([("d", "Hd")], lambda n, h, ff: ("infra", "x", 1.0, 1.0))
    lines2, ok2 = m.format_lines(rows2, m.summarize(rows2, 957))
    if ok2 or "NO VERDICT" not in "\n".join(lines2):
        f.append("two infra verdicts agree but give no kill set: must FAIL loudly")
    return f


BEHAVIOUR = [check_sample, check_percentile, check_summary, check_no_diff_and_no_first_bad, check_lines]


def failures(m):
    out = []
    for chk in BEHAVIOUR:
        try:
            got = chk(m)
        except Exception as e:      # a mutant that crashes a check is killed by it
            got = ["raised %s: %s" % (type(e).__name__, e)]
        out += ["%s: %s" % (chk.__name__, g) for g in got]
    return out


def load(src):
    mod = types.ModuleType("probe_failfast_mutant")
    exec(compile(src, "probe_failfast_mutant", "exec"), mod.__dict__)
    return mod


# honest mutants: one changed expression each, needle must occur exactly once
MUTANTS = [
    ("mean-for-median-ff-cpu", [("med_ff = statistics.median(ff)", "med_ff = statistics.mean(ff)")]),
    ("percentile-off-by-one", [("k = max(1, -(-len(v) * q // 100))", "k = max(1, len(v) * q // 100)")]),
    ("diff-ignores-the-full-sweep", [('diff = [r["name"] for r in rows if r["ff_status"] != r["full_status"]]', 'diff = [r["name"] for r in rows if r["ff_status"] not in ("fail", "pass")]')]),
    ("n-zero-accepted", [("or n < 1:", "or n < 0:")]),
    ("first-bad-inverted", [('r["ff_test"] / r["full_test"] for r', 'r["full_test"] / r["ff_test"] for r')]),
    ("first-bad-includes-survivors", [('if r["ff_status"] == "fail" and r["full_status"] == "fail" and r["full_test"] > 0', 'if r["full_test"] > 0')]),
    ("projection-uses-the-sample", [('"projected_total_cpu": total_mutants * med_ff', '"projected_total_cpu": len(rows) * med_ff')]),
    ("n-above-list-truncates", [("    if n >= len(items):\n        return items\n", "    if n > len(items):\n        raise ValueError('too many')\n")]),
    ("result-ignores-diff", [('ok = s["kill_set_diff"] == 0 and not s["bad_status"]', 'ok = not s["bad_status"]')]),
    ("run-order-swapped", [("        ff = run_one(name, hdr, True)\n        full = run_one(name, hdr, False)\n", "        full = run_one(name, hdr, False)\n        ff = run_one(name, hdr, True)\n")]),
    ("gcc-share-over-full", [('sum(r["ff_gcc"] for r in rows) / ff_sum', 'sum(r["full_gcc"] for r in rows) / sum(full)')]),
]


class Probe(unittest.TestCase):
    def test_real_module_passes_every_check(self):
        self.assertEqual(failures(real), [])

    def test_every_probe_mutant_is_killed(self):
        base = pathlib.Path(SRC).read_text(encoding="utf-8")
        survived = []
        for name, subs in MUTANTS:
            src = base
            for old, new in subs:
                self.assertEqual(src.count(old), 1, "%s: needle must occur exactly once: %r" % (name, old))
                src = src.replace(old, new)
            self.assertNotEqual(src, base, name)
            got = failures(load(src))
            if not got:
                survived.append(name)
            else:
                print("killed %-32s by %s" % (name, ",".join(sorted({g.split(":")[0] for g in got}))), file=sys.__stderr__)
        self.assertEqual(survived, [], "SURVIVED: %s" % survived)
        self.assertGreaterEqual(len(MUTANTS), 4)
        print("RESULT: PASS probe_failfast mutants killed %d/%d" % (len(MUTANTS), len(MUTANTS)), file=sys.__stderr__)

    def test_cli_refuses_n_zero_and_junk(self):
        """The real entry point refuses before it builds anything (exit != 0, the reason on stderr)."""
        for val in ("0", "-3", "abc"):
            r = subprocess.run([sys.executable, "-B", os.path.join(TOOLS, "test_wlan_wr_mutants.py"), "--probe-failfast", val], capture_output=True, text=True, timeout=120)
            self.assertNotEqual(r.returncode, 0, val)
            self.assertIn("--probe-failfast needs an integer N >= 1", r.stderr, val)
        r = subprocess.run([sys.executable, "-B", os.path.join(TOOLS, "test_wlan_wr_mutants.py"), "--probe-failfast"], capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--probe-failfast needs an integer N >= 1", r.stderr)


if __name__ == "__main__":
    unittest.main()
