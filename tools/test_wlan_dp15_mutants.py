#!/usr/bin/env python3
"""CR-02 for the DP15 executor swg_dp_exec in shim/sweet_wlan_g2.h (T-75c7, SPEC-WIFI Part E TRANSPORT block s.1): shim/wlan_dp15_host_test.c must pass on the real
header (the null mutant, which must SURVIVE) and FAIL on every other mutant, at least one per clause of s.1. Each mutant is a private copy of the header AS PATCH 0174 ADDS IT
with ONE exact-string edit (the pattern must occur exactly once, else the runner itself fails: a mutant that no longer applies is a silent hole). A mutant is killed when the plain
build or the ASan/UBSan build of the test fails (the lane that killed it is printed). The headers around g2 come from the patch chain exactly as shim/build.sh extracts them.
Not mutated: dropping the swdp_env_ok call in swg_dp_exec is EQUIVALENT (swdp_audit calls swdp_env_ok first, sweet_wlan_data_plan.h swdp_audit). A mutant that deletes the last use of a helper builds with unused warnings allowed (the real header builds with -Werror in shim/build.sh).
Usage: tools/test_wlan_dp15_mutants.py [--kernel-root DIR]   (DIR holds shim/ and xnu/patches/, default: the repo of this file). No hardware."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import concurrent.futures
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if "--kernel-root" in sys.argv:
    ROOT = sys.argv[sys.argv.index("--kernel-root") + 1]
SHIM = os.path.join(ROOT, "shim")
TEST = os.path.join(SHIM, "wlan_dp15_host_test.c")
PATCH0167 = os.path.join(ROOT, "xnu", "patches", "0167-sweet-wlan-g1-host-dma.patch")
PATCH0174 = os.path.join(ROOT, "xnu", "patches", "0174-sweet-wlan-g2-first-dma.patch")

# clause (TRANSPORT s.1 item), name, old, new. "" new = delete.
MUTANTS = [
    ("-", "M00-null", "static const char *const swg_dp_aname[]", "static const char *const swg_dp_aname[]"),
    ("1 env from engine", "wr0-from-caller", "\te->wr0 = x->d4wr;\n", ""),
    ("1 env from engine", "rd2-from-caller", "\te->rd_d2h[0] = x->d4rd % SWG_R2_DEPTH;\n", ""),
    ("1 env from engine", "bind-h2d-wr-dropped", "e->h2d_wr.pa == x->in.idx[SWG_IDX(SWD_IDX_H2D_W)] &&", "1 &&"),
    ("1 env from engine", "bind-d2h-rd-dropped", "e->d2h_rd.pa == x->in.idx[SWG_IDX(SWD_IDX_D2H_R)] &&", "1 &&"),
    ("1 env from engine", "bind-ring0-dropped", "e->ring0.pa == x->in.ring[0] &&", "1 &&"),
    ("1 env from engine", "bind-ring1-dropped", "e->ring1.pa == x->in.ring[1] &&", "1 &&"),
    ("1 env from engine", "bind-g1-dropped", "return e->g1.pa == x->in.blk && e->g1.len == x->in.blk_len &&", "return 1 &&"),
    ("1 env from engine", "bind-depth0-dropped", "e->depth0 == swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items ? 0 : -1;", "1 ? 0 : -1;"),
    ("1 env from engine", "bind-depth2-dropped", "\tif (e->depth_d2h[0] != SWG_R2_DEPTH) {\n\t\treturn -1;\n\t}\n", ""),
    ("1 ring-0 epoch", "epoch-unchecked", "(uint8_t)(itemw[0] >> 24) != swg_ep0(x)", "0"),      # absolute ring-0 epoch since e86af753
    ("2 refuse before store", "audit-skipped", "r = r != 0 ? r : swdp_audit(env, s, n);", "r = r != 0 ? r : 0;"),
    ("2 ring full (N1)", "room-check-dropped", "if ((it->slot + 1u) % depth == rd) {", "if (0) {"),
    ("2 ring full (N1)", "room-slot-0-only", "x->in.idx[SWG_IDX(SWD_IDX_H2D_R)] + 4ull * swdp_h2d_slot(it->ring)", "x->in.idx[SWG_IDX(SWD_IDX_H2D_R)]"),
    ("2 ring full (N1)", "rd-range-dropped", "\tif (rd >= depth) {\n\t\t*why = \"H2D read index outside the ring\";", "\tif (0) {\n\t\t*why = \"H2D read index outside the ring\";"),
    # "ring0-wrap-allowed" retired 2026-11-10: ring 0 wraps by design since e86af753 (the ring-full check covers it, host test "wrap"/"wrap-full"); its successor:
    ("2 ring-0 lap", "ring0-lap-not-counted", "\t\t\tx->d4wrap0 += (s->value & 0xFFFFu) == 0u && x->d4wr != 0u ? 1u : 0u;", ""),
    ("2 soft stop", "refuse-no-dstop", "\tx->d4dstop = 1u;\n\treturn -SWG_E_ORDER;\n}", "\treturn -SWG_E_ORDER;\n}"),
    ("2 GAP line", "refuse-silent", "\tprintf(\"WIFI GAP: DP15 plan %s refused: %s\\n\", name, why);\n", ""),
    ("3 one copy", "second-copy",
     "\tfor (i = 0; i < n && ex == 0; i++) {\n\t\t*at = i + 1u;\n",
     "\tfor (i = 0; i < n && ex == 0; i++) {\n\t\t*at = i + 1u;\n\t\tif (i > 0u) {\n\t\t\tuint32_t a_, b_;\n\t\t\tswg_dp_copy(s, n, &a_, &b_, (uint32_t *)itemw, (uint32_t *)payw);\n\t\t}\n"),
    ("3 whole-word tail", "tail-over-read", "s[i].len - 4u * k < 4u ? s[i].len - 4u * k : 4u)", "4u)"),
    ("4 kind sequence", "fc-first-row-item", "\t\tif (r == 0u) {\n\t\t\treturn SWG_K_H2DW;\n\t\t}\n\t\tr--;", "\t\tif (r == 0u) {\n\t\t\treturn SWG_K_ITEM;\n\t\t}\n\t\tr--;"),
    ("4 kind sequence", "post-index-before-items", "return r < iw ? SWG_K_ITEM : r == iw ? SWG_K_H2DW : r == iw + 1u ? SWG_K_DB : -1;", "return r == 0u ? SWG_K_H2DW : r <= iw ? SWG_K_ITEM : r == iw + 1u ? SWG_K_DB : -1;"),
    ("4 kind sequence", "tx-no-payload-rows", "\tif (op == SWG_OP_DPTX) {\n\t\tif (r < pw) {\n\t\t\treturn SWG_K_PAY;\n\t\t}\n\t\tr -= pw;\n\t}\n", ""),
    ("4 kind sequence", "consume-db-first", "return r == 0u ? SWG_K_D2HR : r == 1u ? SWG_K_DB : -1;\n\t}\n\tif (op == SWG_OP_DPFC)", "return r == 0u ? SWG_K_DB : r == 1u ? SWG_K_D2HR : -1;\n\t}\n\tif (op == SWG_OP_DPFC)"),
    ("4 kind mapping", "data-space-as-host", "\t*sp = SWG_SP_DATA;\n", "\t*sp = SWG_SP_HOST;\n"),
    ("4 order", "doorbell-before-index",
     "\t\t\tex = swg_dp_one(x, e, &s[i]);\n",
     "\t\t\tex = swg_dp_one(x, e, s[i].kind == SWDP_SK_DB ? &s[i - 1u] : &s[i + 1u]);\n"),
    ("5 old value", "old-always-zero", "ex = swg_d4d_st(x, kind, sp, off, *sh, w[k], 0, nm);", "ex = swg_d4d_st(x, kind, sp, off, 0u, w[k], 0, nm);"),
    ("5 old value", "shadow-not-updated", "\t\t*sh = w[k];\n", ""),
    ("5 old value", "ring0-shadow-old-zero", "x->dpzero = *r0;", "x->dpzero = 0u;"),      # e86af753: ring 0 has a real shadow (old ring-0 words after a lap), no longer a fresh 0
    ("5 old value", "index-old-full-word", "(x->dpx != 0u && low16 ? ((old ^ oldv) & 0xFFFFu) != 0u : old != oldv)", "(old != oldv)"),
    ("5 old value", "index-old-cursor-zero", "swdp_h2d_cur(e, s->ring), s->value, 1,", "0u, s->value, 1,"),
    ("5 read-back", "readback-ignored", "\tx->rb = rb;\n\tif (rb != w) {\n\t\treturn swg_stop(x, SWG_E_READBACK);\n\t}\n\tif (x->dpx != 0u && kind == SWG_K_PAY)", "\tx->rb = rb;\n\tif (x->dpx != 0u && kind == SWG_K_PAY)"),
    ("5 dead link", "dead-link-ignored", "if (old == 0xFFFFFFFFu && (kind != SWG_K_PAY || x->dpx != 0u) && !(x->dpx != 0u && oldv == 0xFFFFFFFFu)) {", "if (0) {"),      # T-866b: restated against the merged condition (L24 payload words may read all ones, DP15 treats every all-ones read as a dead link)
    ("5 doorbell never read", "doorbell-read", "\tif (kind != SWG_K_DB) {\n\t\tif (x->b->rd(x->b->ctx, sp, bp, &old)) {", "\tif (1) {\n\t\tif (x->b->rd(x->b->ctx, sp, bp, &old)) {"),
    ("5 books", "d4wr-not-advanced", "\t\t\tx->d4wr = s->value & 0xFFFFu;", ""),
    ("5 books", "d4rd-not-advanced", "\tx->d4rd += ex == 0 && s->ring == 2u ? 1u : 0u;\n", ""),
    ("6 stop rule", "retry-after-failure",
     "\tex = swg_dp_run(x, env, s, n, itemw, payw, &at);\n\tif (x->b->plan != NULL) {",
     "\tex = swg_dp_run(x, env, s, n, itemw, payw, &at);\n\tif (ex != 0) {\n\t\tx->stopped = 0;\n\t\tex = swg_dp_run(x, env, s, n, itemw, payw, &at);\n\t}\n\tif (x->b->plan != NULL) {"),
    ("6 stop rule", "next-plan-after-stop", "\tif (x->stopped || x->d4dstop || !x->entered) {\n\t\treturn swg_dp_refuse(x, name, \"engine stopped\");", "\tif (!x->entered) {\n\t\treturn swg_dp_refuse(x, name, \"engine stopped\");"),
    ("6 stop rule", "no-dstop-on-failure", "\tif (ex != 0) {\n\t\tx->d4dstop = 1u;\n", "\tif (ex != 0) {\n"),
    ("6 GAP line", "failure-silent", "\t\tprintf(\"WIFI GAP: DP15 store %u/%u of plan %s failed code=%s\\n\", at, n, name, swg_ename[-ex]);\n", ""),
    ("6 store index", "failure-store-index-zero", "\t\t*at = i + 1u;\n", "\t\t*at = 1u;\n"),
    ("7 secret", "secret-values-printed", "\tif (x->dpx != 0u && x->dpsec != 0u) {", "\tif (0) {"),
    ("7 secret", "secret-flag-not-set", "\tx->dpsec = secret != 0 ? 1u : 0u;\n", "\tx->dpsec = 0u;\n"),
    ("7 frames never printed", "frame-words-printed", "\tif (x->dpx != 0u && kind == SWG_K_PAY) {\n\t\treturn 0;", "\tif (0) {\n\t\treturn 0;"),
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def load_wr_helpers():
    spec = importlib.util.spec_from_file_location("wr_mutants_helpers", os.path.join(ROOT, "tools", "test_wlan_wr_mutants.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extracted_headers(w):
    """The headers around g2, built the way shim/build.sh does (patch chain)."""
    return {
        "sweet_wlan_wr.h": w.final_header(),
        "sweet_wlan_chip.h": w.chip_header(),
        "sweet_wlan_dma.h": w.new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h"),
        "sweet_wlan_d4b.h": w.new_file(PATCH0174, "bsd/dev/sweet_wlan_d4b.h"),
        "sweet_wlan_g2.h": w.g2_header(),      # T-0a9d: the sub-headers (g2d.h holds swg_dp_exec, g2e.h) put back in place of their #include, as the other mutant sets read it
    }


def build_and_run(workdir, hdrs, g2_text, tag):
    """(lane that failed or '', output) for one header text: plain, then ASan/UBSan."""
    d = os.path.join(workdir, tag)
    os.makedirs(d)
    for name, text in hdrs.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(g2_text if name == "sweet_wlan_g2.h" else text)
    with open(TEST, encoding="utf-8") as fh, open(os.path.join(d, "wlan_dp15_host_test.c"), "w", encoding="utf-8") as out:
        out.write(fh.read())
    for lane, flags in (("plain", ["-O1"]), ("asan", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"])):
        exe = os.path.join(d, "t_" + lane)
        c = run(["gcc"] + flags + ["-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-unused-but-set-variable", "-I" + d, "-I" + SHIM, "-o", exe, os.path.join(d, "wlan_dp15_host_test.c")])
        if c.returncode:
            return lane + "-build", next((x for x in c.stderr.splitlines() if "error" in x), "build failed")[-140:]
        r = run([exe], timeout=300)
        if r.returncode != 0 or "RESULT: PASS wlan_dp15_host_test" not in r.stdout:
            first = next((x for x in (r.stderr + r.stdout).splitlines() if "FAIL" in x or "ERROR" in x or "runtime error" in x), "rc=%d" % r.returncode)
            return lane, first.strip()[:110]
    return "", ""


def main():
    w = load_wr_helpers()
    hdrs = extracted_headers(w)
    src = hdrs["sweet_wlan_g2.h"]
    with tempfile.TemporaryDirectory(prefix="dp15mut-") as d:
        lane, msg = build_and_run(d, hdrs, src, "real")
        if lane != "":
            print("RESULT: FAIL wlan_dp15_mutants (the unmutated host test does not pass in the %s lane: %s)" % (lane, msg))
            return 1
        jobs = []
        for clause, name, old, new in MUTANTS:
            if src.count(old) != 1:
                print("RESULT: FAIL wlan_dp15_mutants (mutant %s: pattern occurs %d times, must be exactly 1)" % (name, src.count(old)))
                return 1
            jobs.append((clause, name, src.replace(old, new), "m%02d" % len(jobs)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
            results = list(ex.map(lambda j: build_and_run(d, hdrs, j[2], j[3]), jobs))
    survived, lines = [], []
    for (clause, name, _, _t), (lane, msg) in zip(jobs, results):
        if lane == "":
            lines.append("%-24s %-26s SURVIVED" % (clause, name))
            survived.append(name)
        elif lane.endswith("-build"):
            lines.append("%-24s %-26s BUILD ERROR (not a kill): %s" % (clause, name, msg))
            survived.append(name + "(build)")
        else:
            lines.append("%-24s %-26s killed by %s lane: %s" % (clause, name, lane, msg))
    print("\n".join(lines))
    unexpected = [n for n in survived if n != "M00-null"]
    if "M00-null" not in survived:
        print("RESULT: FAIL wlan_dp15_mutants (the null mutant did not survive: the runner is broken)")
        return 1
    if unexpected:
        print("RESULT: FAIL wlan_dp15_mutants survived=%s" % ",".join(unexpected))
        return 1
    print("RESULT: PASS wlan_dp15_mutants mutants=%d killed=%d null_survived=1" % (len(MUTANTS), len(MUTANTS) - 1))
    return 0


class TestDp15Mutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table; the RESULT line is on stdout."""

    def test_every_mutant_fails_the_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
