#!/usr/bin/env python3
"""CR-02 for the LOWCLK B3 read-only Wi-Fi probes of xnu patch 0363 (shim/sweet_wlan_l25_wl.h three new whitelist rows, shim/sweet_wlan_l25_hs.h swl25_probe_wifi at its two points, shim/sweet_wlan_sta.h the
Set 383 key log swsta_p383). Each mutant is a private copy of ONE header with ONE exact-string edit (the pattern must occur exactly once, else the runner itself fails: a mutant that no longer applies is a silent
hole) and must make at least one of the host tests it names fail (plain build; the test source and every other file are symlinks to shim/, so the mutated copy is the one the quote-includes find).
Not mutated (EQUIVALENT): the text bound of the first `<key>` search in swsta_p383 (the `</key>` search is bounded by the same L bytes and always comes after it, so a `<key>` found past L still ends in an unexpected shape).
The null mutant (an edit that changes nothing) must SURVIVE: it proves the runner builds and runs the real tests.
Usage: tools/test_sweetprobe_wifi_mutants.py [--kernel-root DIR]   (DIR holds shim/, default: the repo of this file). No hardware."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import concurrent.futures
import glob
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

W = ["-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-unused-but-set-variable", "-D_GNU_SOURCE", "-I."]
# test name -> (source, extra flags, PASS marker)
TESTS = {
    "l25": ("wlan_l25_host_test.c", [], "RESULT: PASS wlan_l25_host_test"),
    "l25wl": ("wlan_l25_wl_host_test.c", [], "RESULT: PASS wlan_l25_wl_host_test"),
    "sta": ("wlan_sta_host_test.c", [], "RESULT: PASS wlan_sta_host_test"),
}

J = "sweet_wlan_l25_wl.h"
H = "sweet_wlan_l25_hs.h"
S = "sweet_wlan_sta.h"

# (file, name, old, new, tests that must fail)
MUTANTS = [
    (J, "M00-null", "#define SWL25W_WLC_GET_PM      85u", "#define SWL25W_WLC_GET_PM      85u", []),
    # --- the three whitelist rows
    (J, "row-pm-length", '{ "", SWL25W_WLC_GET_PM, 4u, 0u },', '{ "", SWL25W_WLC_GET_PM, 8u, 0u },', ["l25wl"]),
    (J, "row-pm-as-var", '{ "", SWL25W_WLC_GET_PM, 4u, 0u },', '{ "", SWL25W_WLC_GET_VAR, 4u, 0u },', ["l25wl"]),
    (J, "row-pm-write", '{ "", SWL25W_WLC_GET_PM, 4u, 0u },', '{ "", 86u, 4u, 0u },', ["l25wl"]),
    (J, "row-country-length", '{ "country", SWL25W_WLC_GET_VAR, 20u, 0u },', '{ "country", SWL25W_WLC_GET_VAR, 32u, 0u },', ["l25wl"]),
    (J, "row-country-set", '{ "country", SWL25W_WLC_GET_VAR, 20u, 0u },', '{ "country", SWL25W_WLC_SET_VAR, 20u, 0u },', ["l25wl"]),
    (J, "row-chanspecs-length", '{ "chanspecs", SWL25W_WLC_GET_VAR, 1200u, 0u },', '{ "chanspecs", SWL25W_WLC_GET_VAR, 1400u, 0u },', ["l25wl"]),
    (J, "row-chanspecs-set", '{ "chanspecs", SWL25W_WLC_GET_VAR, 1200u, 0u },', '{ "chanspecs", SWL25W_WLC_SET_VAR, 1200u, 0u },', ["l25wl"]),
    (J, "request-word-admits-set-pm", "|| v == SWL25W_WLC_GET_PM ? 0 :", "|| v == SWL25W_WLC_GET_PM || v == 86u ? 0 :", ["l25wl"]),
    (J, "request-word-refuses-get-pm", "|| v == SWL25W_WLC_GET_PM ? 0 :", "? 0 :", ["l25wl"]),
    (J, "get-bytes-after-name-free", "(cmd != SWL25W_WLC_SET_VAR && swl25w_iovars[r].chk != SWL25W_CHK_BSSINFO)); i++) {", "(cmd != SWL25W_WLC_SET_VAR && swl25w_iovars[r].chk != SWL25W_CHK_BSSINFO && cmd != SWL25W_WLC_GET_PM && nl > 0u)); i++) {", ["l25wl"]),
    # --- the probe: gate, points, order, request shapes
    (H, "probe-default-on", "swl25_probe_arm = on != 0u ? SWL25_PROBE_ARM_ON : 0u;", "swl25_probe_arm = SWL25_PROBE_ARM_ON;", ["l25"]),
    (H, "probe-p1-missing", 'ex = swl25_probe_wifi(x, "l25start");', "ex = 0;", ["l25"]),
    (H, "probe-p2-missing", 'ex = swl25_probe_wifi(x, "joined");', "ex = 0;", ["l25"]),
    (H, "probe-p1-repeats", "swl25_probe_arm &= ~1u;", "swl25_probe_arm &= ~0u;", ["l25"]),
    (H, "probe-p2-repeats", "swl25_probe_arm &= ~2u;", "swl25_probe_arm &= ~0u;", ["l25"]),
    (H, "probe-arm-includes-p1", "#define SWL25_PROBE_ARM_ON  2u", "#define SWL25_PROBE_ARM_ON  3u", ["l25"]),
    (H, "probe-unreached-silent", "if (swl25_probe_arm != 0u) {\n\t\tprintf(\"SWEET PROBE: GAP wifi probes armed", "if (0) {\n\t\tprintf(\"SWEET PROBE: GAP wifi probes armed", ["l25"]),
    (H, "probe-p1-label-bringup", 'ex = swl25_probe_wifi(x, "l25start");', 'ex = swl25_probe_wifi(x, "bringup");', ["l25"]),
    (H, "probe-p2-label", 'ex = swl25_probe_wifi(x, "joined");', 'ex = swl25_probe_wifi(x, "l25start");', ["l25"]),
    (H, "probe-skips-country", "	ex = swl25_probe_country(x, at);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n", "", ["l25"]),
    (H, "probe-pm-after-country", "	ex = swl25_probe_pm(x, at);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	ex = swl25_probe_country(x, at);", "	ex = swl25_probe_country(x, at);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	ex = swl25_probe_pm(x, at);", ["l25"]),
    (H, "probe-pm-reply-room", 'NULL, 0u, 4u, 1, 4u, rsp, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	if (cm.status != 0 || cm.resp_len < 4u) {\n		swl25_probe_gap("pm"', 'NULL, 0u, 4u, 1, 8u, rsp, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	if (cm.status != 0 || cm.resp_len < 4u) {\n		swl25_probe_gap("pm"', ["l25"]),
    (H, "probe-country-length", '"country", NULL, 0u, 8u + 12u, 1, 12u, rsp', '"country", NULL, 0u, 8u + 12u, 1, 8u, rsp', ["l25"]),
    (H, "probe-country-sends-data", '"country", NULL, 0u, 8u + 12u,', '"country", rsp, 4u, 8u + 12u,', ["l25"]),
    (H, "probe-chanspecs-length", '"chanspecs", NULL, 0u, SWL25_CHSP_LEN, 1, SWL25_CHSP_LEN,', '"chanspecs", NULL, 0u, SWL25_CHSP_LEN, 1, 1000u,', ["l25"]),
    # --- the probe: decode and GAPs
    (H, "probe-pm-range-unchecked", "} else if (swd4b_get32(rsp) > 2u) {", "} else if (0) {", ["l25"]),
    (H, "probe-pm-status-ignored", '	if (cm.status != 0 || cm.resp_len < 4u) {\n		swl25_probe_gap("pm", &cm);', '	if (cm.resp_len < 4u) {\n		swl25_probe_gap("pm", &cm);', ["l25"]),
    (H, "probe-pm-short-ignored", '	if (cm.status != 0 || cm.resp_len < 4u) {\n		swl25_probe_gap("pm", &cm);', '	if (cm.status != 0) {\n		swl25_probe_gap("pm", &cm);', ["l25"]),
    (H, "probe-country-short-ignored", "	if (cm.status != 0 || cm.resp_len < 12u) {", "	if (cm.status != 0) {", ["l25"]),
    (H, "probe-country-empty-silent", "	if (ab[0] == '\\0' && cc[0] == '\\0') {", "	if (0) {", ["l25"]),
    (H, "probe-country-rev-unsigned", "(int)swd4b_get32(rsp + 4u), cc, at);", "(int)(swd4b_get32(rsp + 4u) & 0xFFFFu), cc, at);", ["l25"]),
    (H, "probe-chanspecs-count-unbounded", "	if (count > SWL25_CHSP_MAX || count > (cm.resp_len < SWL25_CHSP_LEN ? cm.resp_len : SWL25_CHSP_LEN) / 4u - 1u) {", "	if (count > 100000u) {", ["l25"]),
    (H, "probe-chanspecs-resp-len-ignored", "	if (count > SWL25_CHSP_MAX || count > (cm.resp_len < SWL25_CHSP_LEN ? cm.resp_len : SWL25_CHSP_LEN) / 4u - 1u) {", "	if (count > SWL25_CHSP_MAX) {", ["l25"]),
    (H, "probe-chanspecs-5g-as-2g", "} else if (bw == 0x1000u && band == 0xC000u) {\n			swl25_probe_list_add(l5, sizeof l5,", "} else if (bw == 0x1000u && band == 0xC000u) {\n			swl25_probe_list_add(l2, sizeof l2,", ["l25"]),
    (H, "probe-chanspecs-6g-dropped", "} else if (bw == 0x1000u && band == 0x4000u) {\n			swl25_probe_list_add(l6, sizeof l6, cs & 0xFFu);\n		} else if", "} else if (bw == 0x1000u && band == 0x4000u) {\n		} else if", ["l25"]),
    (H, "probe-chanspecs-40-as-80", "} else if (bw == 0x1800u) {\n			n40++;", "} else if (bw == 0x1800u) {\n			n80++;", ["l25"]),
    (H, "probe-chanspecs-160-lost", "} else if (bw == 0x2800u) {\n			n160++;", "} else if (bw == 0x2800u) {", ["l25"]),
    (H, "probe-chanspecs-other-lost", "		} else {\n			other++;\n		}", "		} else {\n		}", ["l25"]),
    (H, "probe-list-overrun", "	if (n + 6u >= cap) {", "	if (n + 6u >= cap + 64u) {", ["l25"]),
    (H, "probe-gap-no-resp-len", 'status=%d resp_len=%u: value unread\\n", what, (int)cm->status, (unsigned)cm->resp_len);', 'status=%d resp_len=%u: value unread\\n", what, (int)cm->status, 0u);', ["l25"]),
    # --- the Set 383 key log
    (S, "p383-hook-ignores-probe", "	if (s->probe && set && type == SWSTA_T_SET_PROPERTY) swsta_p383(", "	if (set && type == SWSTA_T_SET_PROPERTY) swsta_p383(", ["sta"]),
    (S, "p383-hook-get", "	if (s->probe && set && type == SWSTA_T_SET_PROPERTY) swsta_p383(", "	if (s->probe && type == SWSTA_T_SET_PROPERTY) swsta_p383(", ["sta"]),
    (S, "p383-hook-any-type", "	if (s->probe && set && type == SWSTA_T_SET_PROPERTY) swsta_p383(", "	if (s->probe && set) swsta_p383(", ["sta"]),
    (S, "p383-first-word", "swsta_g32(b, 0) != 1u) {", "swsta_g32(b, 0) > 1u) {", ["sta"]),
    (S, "p383-length-zero", "if (l1 < 1u || l1 > 0x1000u || l1 > len - 8u)", "if (l1 > 0x1000u || l1 > len - 8u)", ["sta"]),
    (S, "p383-length-vs-buffer", "if (l1 < 1u || l1 > 0x1000u || l1 > len - 8u)", "if (l1 < 1u || l1 > 0x1000u)", ["sta"]),
    (S, "p383-null-buffer", "if (!b || len < 12 || swsta_g32(b, 0) != 1u)", "if (len < 12 || swsta_g32(b, 0) != 1u)", ["sta"]),
    (S, "p383-key-span", "ke = swsta_p383_find(b + 8, n, \"</key>\", 6, ks, 64u);", "ke = swsta_p383_find(b + 8, n, \"</key>\", 6, ks, 65u);", ["sta"]),
    (S, "p383-key-span-short", "ke = swsta_p383_find(b + 8, n, \"</key>\", 6, ks, 64u);", "ke = swsta_p383_find(b + 8, n, \"</key>\", 6, ks, 63u);", ["sta"]),
    (S, "p383-empty-key", "if (ke == 0xFFFFFFFFu || ke == ks)", "if (ke == 0xFFFFFFFFu)", ["sta"]),
    (S, "p383-nonprintable", "name[i] = (c >= 0x20 && c < 0x7f) ? (char)c : '?';", "name[i] = (char)c;", ["sta"]),
    (S, "p383-key-cut", "i + 1u < SWSTA_P383_KEY; i++) { uint8_t c", "i + 1u < SWSTA_P383_KEY + 8u; i++) { uint8_t c", ["sta"]),
    (S, "p383-dedup-by-prefix", "if (!SWSTA_MEMCMP(s->p383[k].key, name, i + 1)) {", "if (!SWSTA_MEMCMP(s->p383[k].key, name, 2)) {", ["sta"]),
    (S, "p383-table-overflow", "if (s->p383_used >= SWSTA_P383_MAX) {", "if (s->p383_used > SWSTA_P383_MAX) {", ["sta"]),
    (S, "p383-table-full-silent", "if (!s->p383_over++) { SWSTA_SNPRINTF(line, sizeof line, \"GAP set383 key table full", "if (!s->p383_over++) { SWSTA_SNPRINTF(line, sizeof line, \"set383 key table full", ["sta"]),
    (S, "p383-gap-every-time", "	if (s->p383_shape++) return;", "	s->p383_shape++;", ["sta"]),
    (S, "p383-gap-never", "	SWSTA_PROBE_LOG(line);\n}\n\nstatic inline void swsta_p383(", "}\n\nstatic inline void swsta_p383(", ["sta"]),
    (S, "p383-logs-value", "\"set383 key=%s len=%u count=%u%s\", e->key, (unsigned)n,", "\"set383 key=%s len=%u count=%u%s\", (const char *)b + 8, (unsigned)n,", ["sta"]),
    (S, "p383-count-every-time", "if (e->n == 1u || e->n == 10u || e->n == 100u || e->n == 1000u || e->n == 10000u) {", "if (1) {", ["sta"]),
    (S, "p383-first-seen-silent", "if (e->n == 1u || e->n == 10u", "if (e->n == 10u", ["sta"]),
    (S, "p383-summary-missing", "	if (s->probe) {	/* LOWCLK B3 (patch 0363): the Set 383 keys seen", "	if (0) {	/* LOWCLK B3 (patch 0363): the Set 383 keys seen", ["sta"]),
    (S, "p383-summary-when-off", "	if (s->probe) {	/* LOWCLK B3 (patch 0363): the Set 383 keys seen", "	if (1) {	/* LOWCLK B3 (patch 0363): the Set 383 keys seen", ["sta"]),
]

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


_EXTRA = {}


def extra_headers():
    """the headers that exist only inside the patch chain (sweet_wlan_wr.h, chip.h, dma.h, d4b.h), extracted the way shim/build.sh and the DP15 mutant runner do"""
    if not _EXTRA:
        import importlib.util
        spec = importlib.util.spec_from_file_location("dp15_mutants_helpers", os.path.join(ROOT, "tools", "test_wlan_dp15_mutants.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        hd = mod.extracted_headers(mod.load_wr_helpers())
        for k in ("sweet_wlan_wr.h", "sweet_wlan_chip.h", "sweet_wlan_dma.h", "sweet_wlan_d4b.h"):
            _EXTRA[k] = hd[k]
    return _EXTRA


def lay_out(d, rel_mutated, text):
    """a directory of symlinks to shim/ with ONE regular file: the mutated header (plus the patch-only headers as plain files)"""
    for n, body in extra_headers().items():
        if not os.path.exists(os.path.join(SHIM, n)):
            with open(os.path.join(d, n), "w", encoding="utf-8") as fh:
                fh.write(body)
    for p in glob.glob(os.path.join(SHIM, "*.h")) + glob.glob(os.path.join(SHIM, "*.c")) + glob.glob(os.path.join(SHIM, "*.in")):
        n = os.path.basename(p)
        if n != rel_mutated:
            os.symlink(p, os.path.join(d, n))
    with open(os.path.join(d, rel_mutated), "w", encoding="utf-8") as fh:
        fh.write(text)


def build_and_run(workdir, tag, rel, text, tests):
    """'' when every named test still passes (the mutant SURVIVED), else '<test>: first failure line'"""
    d = os.path.join(workdir, tag)
    os.makedirs(d)
    lay_out(d, rel, text)
    for t in tests:
        src, extra, ok = TESTS[t]
        exe = os.path.join(d, "t_" + t)
        c = run(["gcc"] + W + extra + ["-o", exe, src], cwd=d)
        if c.returncode:
            return t + "-build: " + next((x for x in c.stderr.splitlines() if "error" in x), "build failed")[-140:]
        r = run([exe], cwd=d, timeout=600)
        if r.returncode != 0 or ok not in r.stdout:
            first = next((x for x in (r.stderr + r.stdout).splitlines() if "FAIL" in x or "runtime error" in x), "rc=%d" % r.returncode)
            return t + ": " + first.strip()[:110]
    return ""


def main():
    srcs = {}
    for rel in {m[0] for m in MUTANTS}:
        with open(os.path.join(SHIM, rel), encoding="utf-8") as fh:
            srcs[rel] = fh.read()
    jobs = []
    for rel, name, old, new, tests in MUTANTS:
        if srcs[rel].count(old) != 1:
            print("RESULT: FAIL sweetprobe_wifi_mutants (mutant %s: pattern occurs %d times in %s, must be exactly 1)" % (name, srcs[rel].count(old), rel))
            return 1
        if name != "M00-null" and not tests:
            print("RESULT: FAIL sweetprobe_wifi_mutants (mutant %s names no test)" % name)
            return 1
        jobs.append((name, rel, srcs[rel].replace(old, new), tests, "m%03d" % len(jobs)))
    with tempfile.TemporaryDirectory(prefix="rxpoolmut-") as d:
        real = {}
        for t in TESTS:                      # the unmutated tree: every test must pass, or the runner is broken
            msg = build_and_run(d, "real-" + t, J, srcs.get(J) or open(os.path.join(SHIM, J), encoding="utf-8").read(), [t])
            if msg != "":
                print("RESULT: FAIL sweetprobe_wifi_mutants (the unmutated host test %s does not pass: %s)" % (t, msg))
                return 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as ex:
            results = list(ex.map(lambda j: build_and_run(d, j[4], j[1], j[2], j[3]) if j[3] else "", jobs))
    survived, lines = [], []
    for (name, rel, _, tests, _t), msg in zip(jobs, results):
        if not tests:
            lines.append("%-40s %-24s null mutant (not run)" % (name, rel))
        elif msg == "":
            lines.append("%-40s %-24s SURVIVED (%s)" % (name, rel, ",".join(tests)))
            survived.append(name)
        elif "-build:" in msg.split(" ")[0] + ":":
            lines.append("%-40s %-24s BUILD ERROR (not a kill): %s" % (name, rel, msg))
            survived.append(name + "(build)")
        else:
            lines.append("%-40s %-24s killed by %s" % (name, rel, msg))
    print("\n".join(lines))
    if survived:
        print("RESULT: FAIL sweetprobe_wifi_mutants survived=%s" % ",".join(survived))
        return 1
    print("RESULT: PASS sweetprobe_wifi_mutants mutants=%d killed=%d" % (len(MUTANTS) - 1, len(MUTANTS) - 1))
    return 0


class TestSweetprobeWifiMutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table; the RESULT line is on stdout."""

    def test_every_mutant_fails_a_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
