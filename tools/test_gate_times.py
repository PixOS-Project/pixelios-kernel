#!/usr/bin/env python3
"""Tests for tools/gate-times (PLAN-GATE-2X G1/G2): median of the last 10 gates per kind, the slowest step, per-step budget breaches, and --check (exit 1 on two
CONSECUTIVE total-budget breaches of one kind: quick 360 s, formal 600 s; 0 otherwise), over fixture ledgers, plus MUTANTS of the tool the suite must kill.

  python3 -I tools/test_gate_times.py        last line: RESULT: PASS|FAIL test_gate_times tests=N mutants=M
"""
import contextlib, importlib.machinery, importlib.util, io, json, pathlib, sys, tempfile, unittest

TOOLS = pathlib.Path(__file__).resolve().parent
TOOL = TOOLS / "gate-times"
MANIFEST = {"steps": [{"step": "build", "budget_s": 60}, {"step": "unit", "budget_s": 180}, {"step": "rules", "budget_s": 30}],
            "totals_s": {"quick": 360, "formal": 600, "cold": 1100}}


def load(path):
    loader = importlib.machinery.SourceFileLoader("gate_times_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def row(epoch, kind, elapsed, steps="build:10,unit:50", label="w", base="abc", cold=None):
    """cold None = a 9-column row (written before 2026-10-31), else the 10th column 0|1"""
    return "\t".join([str(epoch), label, kind, "offload-host", base, "3", "28", str(elapsed), steps] + ([] if cold is None else [str(cold)]))


def run(mod, d, rows, *extra, manifest=True):
    p = pathlib.Path(d) / "led.tsv"
    p.write_text("".join(r + "\n" for r in rows))
    m = pathlib.Path(d) / "m.json"
    m.write_text(json.dumps(MANIFEST))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = mod.main(["--ledger", str(p), "--manifest", str(m if manifest else pathlib.Path(d) / "nope.json"), *extra])
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue()


def behaviour(mod):
    """Every check against module MOD -> list of failure descriptions (empty = good); the mutants run the same function."""
    bad = []

    def eq(name, got, want):
        if got != want:
            bad.append("%s: got %r want %r" % (name, got, want))

    with tempfile.TemporaryDirectory() as d:
        # 1. median of the LAST 10 per kind: 12 quick gates elapsed 1..12 -> last 10 = 3..12 -> median 7.5 -> 7 (the last 9 would give 8, the last 11 gives 7, all 12 give 6)
        rows = [row(1000 + i, "quick", i) for i in range(1, 13)]
        rc, out = run(mod, d, rows)
        eq("median line", "quick: median of last 10 = 7s (budget 360 s)\n" in out, True)
        eq("report rc", rc, 0)
        # a different kind has its own median; an outlier does not move a median (mean would)
        rows = [row(1000 + i, "formal", 10) for i in range(9)] + [row(1100, "formal", 500)] + [row(2000 + i, "quick", 100 + i) for i in range(3)]
        out = run(mod, d, rows)[1]
        eq("formal median ignores the outlier", "formal: median of last 10 = 10s (budget 600 s)\n" in out, True)
        eq("quick median own kind", "quick: median of last 3 = 101s (budget 360 s)\n" in out, True)
        # only the last 10 count: 5 old huge gates then 10 small ones
        rows = [row(1000 + i, "quick", 9000) for i in range(5)] + [row(2000 + i, "quick", 20) for i in range(10)]
        eq("older gates dropped", "quick: median of last 10 = 20s (budget 360 s)\n" in run(mod, d, rows)[1], True)
        # file order is not time order: rows are sorted by epoch before taking the last 10
        rows = [row(5000 + i, "quick", 20) for i in range(10)] + [row(1000 + i, "quick", 9000) for i in range(5)]   # the OLD gates are last in the file
        eq("sorted by epoch", "quick: median of last 10 = 20s" in run(mod, d, rows)[1], True)
        # 2. slowest step = highest MEDIAN step time over the last 10, and a median over budget is listed
        rows = [row(1000 + i, "quick", 100, "build:10,unit:%d,rules:5" % (200 if i % 3 else 20)) for i in range(10)]
        out = run(mod, d, rows)[1]
        eq("slowest step", "quick: slowest step unit median 200s\n" in out, True)
        eq("breach listed", "quick: budget breach unit median 200s > 180s\n" in out, True)
        eq("no breach for steps in budget", "budget breach build" in out or "budget breach rules" in out, False)
        # a median exactly AT the budget is no breach
        rows = [row(1000 + i, "quick", 100, "unit:180") for i in range(3)]
        eq("median at budget", "budget breach" in run(mod, d, rows)[1], False)
        # a step with no budget in the manifest is never a breach (still can be the slowest)
        rows = [row(1000 + i, "quick", 100, "mystery:999") for i in range(3)]
        out = run(mod, d, rows)[1]
        eq("unbudgeted step", ("slowest step mystery median 999s" in out, "budget breach" in out), (True, False))
        # 3. --check: two consecutive total-budget breaches of one kind exit 1
        rows = [row(1000, "formal", 601), row(1001, "formal", 700)]
        rc, out = run(mod, d, rows, "--check")
        eq("check: two formal breaches", (rc, "RESULT: FAIL gate-times" in out and "kinds=formal" in out), (1, True))
        eq("report only: same ledger without --check exits 0 and says so", (run(mod, d, rows)[0], "consecutive-breach=formal" in run(mod, d, rows)[1]), (0, True))
        eq("check: breach then in-budget", run(mod, d, [row(1000, "formal", 700), row(1001, "formal", 600)], "--check")[0], 0)
        eq("check: in-budget then breach", run(mod, d, [row(1000, "formal", 100), row(1001, "formal", 700)], "--check")[0], 0)
        eq("check: breach, ok, breach (not consecutive)", run(mod, d, [row(1000, "formal", 700), row(1001, "formal", 100), row(1002, "formal", 700)], "--check")[0], 0)
        eq("check: exactly at the budget is no breach", run(mod, d, [row(1000, "formal", 600), row(1001, "formal", 600)], "--check")[0], 0)
        eq("check: one gate only", run(mod, d, [row(1000, "formal", 9999)], "--check")[0], 0)
        eq("check: three in a row", run(mod, d, [row(1000 + i, "formal", 700) for i in range(3)], "--check")[0], 1)
        # budgets are per kind: 400 s breaches quick (360) but not formal (600)
        eq("check: quick 400 x2 fails", run(mod, d, [row(1000, "quick", 400), row(1001, "quick", 400)], "--check")[0], 1)
        eq("check: formal 400 x2 passes", run(mod, d, [row(1000, "formal", 400), row(1001, "formal", 400)], "--check")[0], 0)
        # kinds are independent: formal breaches twice, quick is fine -> only formal named; interleaved rows of the other kind do not break a streak
        rows = [row(1000, "formal", 700), row(1001, "quick", 10), row(1002, "formal", 700), row(1003, "quick", 10)]
        rc, out = run(mod, d, rows, "--check")
        eq("check: kinds independent", (rc, "kinds=formal" in out), (1, True))
        # consecutive means by time, not by file order
        eq("check: sorted by epoch", run(mod, d, [row(1001, "formal", 100), row(1000, "formal", 700), row(1002, "formal", 700)], "--check")[0], 0)
        # defaults when the manifest is unreadable: quick 360 / formal 600 (not swapped)
        eq("default totals", (run(mod, d, [row(1000, "quick", 400), row(1001, "quick", 400)], "--check", manifest=False)[0],
                              run(mod, d, [row(1000, "formal", 400), row(1001, "formal", 400)], "--check", manifest=False)[0]), (1, 0))
        # 4. malformed / short lines are skipped, a missing ledger is a clean no-op, an empty ledger passes
        rc, out = run(mod, d, ["garbage", "1\t2\t3", row(1000, "quick", 10), "x" + row(1001, "quick", 99999)])
        eq("malformed lines skipped", (rc, "median of last 1 = 10s" in out), (0, True))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main(["--ledger", str(pathlib.Path(d) / "absent.tsv")])
        eq("no ledger yet", (rc, "no ledger yet" in out.getvalue()), (0, True))
        eq("empty ledger", run(mod, d, [], "--check")[0], 0)
        # 5. the RESULT line
        out = run(mod, d, [row(1000, "quick", 10), row(1001, "formal", 10)])[1]
        eq("RESULT line", out.splitlines()[-1], "RESULT: PASS gate-times kinds=formal,quick gates=2")
        # 6. COLD rows (PLAN-GATE-2X K0; ledger column 10 = 1). 9-column and 10-column warm rows both read as warm; cold rows never enter the warm median
        warm = [row(1000, "formal", 100), row(1001, "formal", 100, cold=0), row(1002, "formal", 100)]
        out = run(mod, d, warm + [row(1003, "formal", 2000, cold=1)])[1]
        eq("warm median ignores a cold row", "formal: median of last 3 = 100s (budget 600 s)\n" in out, True)
        eq("cold median line", "cold median: n=1 median=2000s last=2000s (budget 1100 s)  OVER\n" in out, True)
        eq("cold RESULT line", out.splitlines()[-1], "RESULT: PASS gate-times kinds=formal gates=4 cold-gates=1")
        eq("no cold rows -> no cold line", "cold" in run(mod, d, warm)[1], False)
        eq("cold=0 rows are warm", "formal: median of last 3" in run(mod, d, warm)[1], True)
        out = run(mod, d, [row(1000, "formal", 100, cold=1), row(1001, "formal", 1000, cold=1), row(1002, "formal", 400, cold=1)])[1]
        eq("cold median of the last 3, last by epoch", "cold median: n=3 median=400s last=400s (budget 1100 s)\n" in out, True)
        eq("only cold rows: kinds=-", out.splitlines()[-1], "RESULT: PASS gate-times kinds=- gates=3 cold-gates=3")
        out = run(mod, d, [row(5000, "formal", 700, cold=1)] + [row(1000 + i, "formal", 9000, cold=1) for i in range(12)])[1]
        eq("cold: only the last 10, sorted by epoch", "cold median: n=10 median=9000s last=700s" in out, True)
        # cold budget 1100 s: --check fails on TWO consecutive cold breaches, only
        eq("cold check: two breaches", run(mod, d, [row(1000, "formal", 1101, cold=1), row(1001, "formal", 1500, cold=1)], "--check")[0], 1)
        rc, out = run(mod, d, [row(1000, "formal", 1101, cold=1), row(1001, "formal", 1500, cold=1)], "--check")
        eq("cold check names cold", "RESULT: FAIL gate-times" in out and "kinds=cold" in out, True)
        eq("cold check: report only exits 0", run(mod, d, [row(1000, "formal", 1101, cold=1), row(1001, "formal", 1500, cold=1)])[0], 0)
        eq("cold check: exactly 1100 twice is fine", run(mod, d, [row(1000, "formal", 1100, cold=1), row(1001, "formal", 1100, cold=1)], "--check")[0], 0)
        eq("cold check: breach then ok", run(mod, d, [row(1000, "formal", 1500, cold=1), row(1001, "formal", 1000, cold=1)], "--check")[0], 0)
        eq("cold check: one breach only", run(mod, d, [row(1000, "formal", 1500, cold=1)], "--check")[0], 0)
        eq("cold check: sorted by epoch", run(mod, d, [row(1001, "formal", 100, cold=1), row(1000, "formal", 1500, cold=1), row(1002, "formal", 1500, cold=1)], "--check")[0], 0)
        eq("cold check: warm rows between do not break the cold streak", run(mod, d, [row(1000, "formal", 1500, cold=1), row(1001, "formal", 100, cold=0), row(1002, "formal", 1500, cold=1)], "--check")[0], 1)
        eq("cold 600 s is a cold pass but would be a warm breach", (run(mod, d, [row(1000, "formal", 700, cold=1), row(1001, "formal", 700, cold=1)], "--check")[0],
                                                                run(mod, d, [row(1000, "formal", 700, cold=0), row(1001, "formal", 700, cold=0)], "--check")[0]), (0, 1))
        eq("cold rows never trip the warm formal check", "kinds=formal" in run(mod, d, [row(1000, "formal", 2000, cold=1), row(1001, "formal", 2000, cold=1)], "--check")[1], False)
        eq("warm breach + cold rows: warm still fails alone", run(mod, d, [row(1000, "formal", 700, cold=0), row(1001, "formal", 100, cold=1), row(1002, "formal", 700)], "--check")[0], 1)
        # defaults without a manifest: cold 1100 (not the warm formal 600, not 0)
        eq("default cold budget", (run(mod, d, [row(1000, "formal", 1000, cold=1), row(1001, "formal", 1000, cold=1)], "--check", manifest=False)[0],
                                   run(mod, d, [row(1000, "formal", 1200, cold=1), row(1001, "formal", 1200, cold=1)], "--check", manifest=False)[0]), (0, 1))
        # a malformed 10th column is warm, a short or non-numeric row is still skipped
        eq("junk cold column = warm", "formal: median of last 1 = 100s" in run(mod, d, [row(1000, "formal", 100, cold="x")])[1], True)
        eq("malformed rows still skipped next to cold rows", "cold median: n=1 median=900s" in run(mod, d, ["garbage", "1\t2\t3", row(1000, "formal", 900, cold=1)])[1], True)
    return bad


class GateTimes(unittest.TestCase):
    def test_behaviour(self):
        self.assertEqual(behaviour(load(TOOL)), [])

    def test_mutants(self):
        src = TOOL.read_text()
        killed, survived = 0, []
        with tempfile.TemporaryDirectory() as d:
            for mid, old, new in MUTANTS:
                self.assertEqual(src.count(old), 1, "mutant %s: the tool text it edits is not unique/present: %r" % (mid, old))
                p = pathlib.Path(d) / ("gate-times-" + mid)
                p.write_text(src.replace(old, new, 1))
                try:
                    caught = bool(behaviour(load(p)))
                except Exception:
                    caught = True
                if caught:
                    killed += 1
                else:
                    survived.append(mid)
        print("\nMUTANTS gate-times: killed %d/%d" % (killed, len(MUTANTS)), file=sys.stderr)
        self.assertEqual(survived, [], "SURVIVED: %s" % survived)


# (id, exact text of tools/gate-times, replacement): one realistic defect each
MUTANTS = [
    ("last-9", "LAST = 10", "LAST = 9"),
    ("last-all", "last = k[-LAST:]", "last = k"),
    ("mean-not-median", "int(statistics.median(r[\"elapsed\"] for r in last))", "int(statistics.mean(r[\"elapsed\"] for r in last))"),
    ("any-not-all", 'all(r["elapsed"] > tot for r in k[-2:])', 'any(r["elapsed"] > tot for r in k[-2:])'),
    ("last-one-only", 'all(r["elapsed"] > tot for r in k[-2:])', 'all(r["elapsed"] > tot for r in k[-1:])'),
    ("total-gte", 'all(r["elapsed"] > tot for r in k[-2:])', 'all(r["elapsed"] >= tot for r in k[-2:])'),
    ("step-gte", "if n in per_step and v > per_step[n]]", "if n in per_step and v >= per_step[n]]"),
    ("file-order", 'key=lambda r: r["epoch"])', "key=lambda r: 0)"),
    ("slowest-min", "max(meds.items(), key=lambda kv: kv[1])", "min(meds.items(), key=lambda kv: kv[1])"),
    ("check-ignored", "if a.check and bad:", "if bad:"),
    ("defaults-swapped", 'TOTALS = {"quick": 360, "formal": 600, "cold": 1100}', 'TOTALS = {"quick": 540, "formal": 360, "cold": 1100}'),
    ("single-row-breach", "len(k) >= 2 and ", ""),
    ("budget-any-kind", 'tot = totals.get(kind)', 'tot = totals.get("quick")'),
    # K0 (cold gate)
    ("warm-median-counts-cold", 'rows = [r for r in rows if not r["cold"]]', "rows = list(rows)"),
    ("cold-column-ignored", 'len(f) > 9 and f[9] == "1"', "False"),
    ("cold-default-budget", '"formal": 600, "cold": 1100}', '"formal": 540, "cold": 2200}'),
    ("cold-budget-is-formal", 'tot = totals.get("cold")', 'tot = totals.get("formal")'),
    ("cold-last-only", 'k[-1]["elapsed"] > tot and k[-2]["elapsed"] > tot', 'k[-1]["elapsed"] > tot'),
    ("cold-gte", 'k[-1]["elapsed"] > tot and k[-2]["elapsed"] > tot', 'k[-1]["elapsed"] >= tot and k[-2]["elapsed"] >= tot'),
    ("cold-not-sorted", 'key=lambda c: c["epoch"])', "key=lambda c: 0)"),
    ("cold-breach-not-checked", 'bad.append("cold")', "pass"),
    ("cold-median-of-last-only", "statistics.median([c[\"elapsed\"] for c in lastn])", "statistics.median([k[-1][\"elapsed\"]])"),
    ("cold-window-dropped", "lastn = k[-LAST:]", "lastn = k"),
]

if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    ok = res.wasSuccessful()
    print("RESULT: %s test_gate_times tests=%d mutants=%d" % ("PASS" if ok else "FAIL", res.testsRun, len(MUTANTS)))
    sys.exit(0 if ok else 1)
