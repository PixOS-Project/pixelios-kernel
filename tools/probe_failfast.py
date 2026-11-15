"""Fail-fast probe of the wlan header-mutant harness (PLAN-GATE-2X section 8, slice K2a). Pure aggregation, no gcc: the caller passes the runner.

A sample of N header mutants is built and run twice, cache off, sequentially: once fail-fast (the CC_FLAGS the gate uses) and once with the
fail-fast define removed. Per mutant it gets CPU seconds of gcc and of the test (child rusage, not wall). It answers three questions:
 - how much does a fresh (cache-cold) mutant cost today (median_ff_cpu, and which share of it is gcc),
 - how far into the full sweep is the first failed check (median_first_bad = ff test cpu / full test cpu, killed mutants that ran a test),
 - is the kill set the same in both modes (kill_set_diff: mutants whose status differs).
Projections (957 x median_ff_cpu, divided by jobs) are labelled `projected`: perfect scaling, no load, rule 5 guess, not a measurement."""
import os
import statistics

SAMPLE_BASIS = "stride"        # sample i of N = item floor((i + 0.5) * len / N): deterministic, no random seed needed (a fixed list gives a fixed sample)
JOBS_PROJECTED = (6, 14)       # the gate's lane-C budget while lanes A/B run, and after check-all releases the host (PLAN-GATE-2X K1)
BAD_STATUS = ("timeout", "infra")


def sample(items, n):
    """A deterministic stride sample of n items; n larger than the list returns the whole list; n < 1 is refused (a probe of nothing proves nothing)."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("--probe-failfast needs N >= 1, got %r" % (n,))
    items = list(items)
    if n >= len(items):
        return items
    return [items[int((i + 0.5) * len(items) / n)] for i in range(n)]


def percentile(values, q):
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    v = sorted(values)
    if not v:
        raise ValueError("percentile of nothing")
    k = max(1, -(-len(v) * q // 100))      # ceil(len * q / 100), at least rank 1
    return v[int(k) - 1]


def run_probe(items, run_one):
    """items: [(name, header)]; run_one(name, header, failfast: bool) -> (status, msg, gcc_cpu, test_cpu). Returns one row dict per item, in order."""
    rows = []
    for name, hdr in items:
        ff = run_one(name, hdr, True)
        full = run_one(name, hdr, False)
        rows.append({"name": name,
                     "ff_status": ff[0], "ff_gcc": ff[2], "ff_test": ff[3], "ff_cpu": ff[2] + ff[3],
                     "full_status": full[0], "full_gcc": full[2], "full_test": full[3], "full_cpu": full[2] + full[3]})
    return rows


def summarize(rows, total_mutants):
    """Aggregate rows into the numbers the probe prints. total_mutants: size of the full header-mutant list for the projection."""
    if not rows:
        raise ValueError("no probe rows")
    ff = [r["ff_cpu"] for r in rows]
    full = [r["full_cpu"] for r in rows]
    # first-bad fraction: only mutants the fail-fast build killed with a failed check (a test ran, so full test cpu > 0); survivors and compile errors have none
    fb = [r["ff_test"] / r["full_test"] for r in rows if r["ff_status"] == "fail" and r["full_status"] == "fail" and r["full_test"] > 0]
    diff = [r["name"] for r in rows if r["ff_status"] != r["full_status"]]
    bad = [r["name"] for r in rows if r["ff_status"] in BAD_STATUS or r["full_status"] in BAD_STATUS]
    ff_sum = sum(ff)
    med_ff = statistics.median(ff)
    s = {"n": len(rows), "median_ff_cpu": med_ff, "median_full_cpu": statistics.median(full),
         "median_first_bad": statistics.median(fb) if fb else None, "p90_first_bad": percentile(fb, 90) if fb else None,
         "first_bad_n": len(fb), "kill_set_diff": len(diff), "diff_names": diff, "bad_status": bad,
         "gcc_share": (sum(r["ff_gcc"] for r in rows) / ff_sum) if ff_sum > 0 else None,
         "projected_total_cpu": total_mutants * med_ff, "total_mutants": total_mutants}
    s["projected_wall"] = {j: s["projected_total_cpu"] / j for j in JOBS_PROJECTED}
    return s


def _f(v, spec="%.2f"):
    return "n/a" if v is None else spec % v


def format_lines(rows, s):
    """The probe output: one PROBE line per mutant, the FAILFAST PROBE line, the cost split, the projections, and RESULT."""
    out = ["PROBE %s ff_cpu=%.2f full_cpu=%.2f ff_status=%s full_status=%s" % (r["name"], r["ff_cpu"], r["full_cpu"], r["ff_status"], r["full_status"]) for r in rows]
    out.append("FAILFAST PROBE n=%d median_ff_cpu=%.2f median_full_cpu=%.2f median_first_bad=%s (ff test cpu / full test cpu, over %d killed-by-a-check mutants) p90_first_bad=%s kill_set_diff=%d"
               % (s["n"], s["median_ff_cpu"], s["median_full_cpu"], _f(s["median_first_bad"]), s["first_bad_n"], _f(s["p90_first_bad"]), s["kill_set_diff"]))
    gcc = s["gcc_share"]
    out.append("COST SPLIT per fail-fast mutant: gcc %s of the cpu, test %s (research mutant-run-speed.md section 4: gcc 2.7 s)"
               % (_f(None if gcc is None else gcc * 100, "%.0f%%"), _f(None if gcc is None else (1 - gcc) * 100, "%.0f%%")))
    out.append("projected HEADER-SET-ONLY cpu (median of this sample x all header mutants; NOT glue/g2/d4b/audit sets, NOT hanging mutants) = %d x %.2f = %.0f cpu-s; projected wall at %s (perfect scaling, not a measurement)"
               % (s["total_mutants"], s["median_ff_cpu"], s["projected_total_cpu"], ", ".join("%d jobs = %.0f s" % (j, w) for j, w in s["projected_wall"].items())))
    if s["diff_names"]:
        out.append("KILL SET DIFF: %s" % ", ".join(s["diff_names"]))
    if s["bad_status"]:
        out.append("NO VERDICT (timeout/infra): %s" % ", ".join(s["bad_status"]))
    ok = s["kill_set_diff"] == 0 and not s["bad_status"]
    try:
        load = "%.1f" % os.getloadavg()[0]
    except OSError:
        load = "?"
    out.append("HOST LOAD1 %s at print time (cpu seconds are inflated by SMT/cache contention under load: AP-186/193; judge only on an idle host)" % load)
    out.append("RESULT: %s failfast probe n=%d kill_set_diff=%d no_verdict=%d" % ("PASS" if ok else "FAIL", s["n"], s["kill_set_diff"], len(s["bad_status"])))
    return out, ok
