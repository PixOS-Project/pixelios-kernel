#!/usr/bin/env python3
"""CR-02 for the L25/L26 engine in shim/sweet_wlan_l25.h and shim/sweet_wlan_l25_hs.h (T-38cc): shim/wlan_l25_host_test.c must pass on the real header (null mutant, must SURVIVE) and FAIL on
every other mutant. A mutant is a private copy of one of the two headers with ONE exact-string edit (the pattern must occur exactly once, else the runner fails: no silent hole); it is
killed when the plain or the ASan/UBSan build of the test fails. The other headers come from a finished shim/build.sh output dir (--out DIR, default shim/out).
Usage: tools/test_wlan_l25_mutants.py [--kernel-root DIR] [--out DIR]. No hardware."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import concurrent.futures
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = sys.argv[sys.argv.index("--kernel-root") + 1] if "--kernel-root" in sys.argv else os.path.dirname(HERE)
SHIM = os.path.join(ROOT, "shim")
OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(SHIM, "out")
TESTNAME = "wlan_l25_host_test"
GLUE_TESTNAME = "wlan_glue_l25_host_test"      # patch 0288: the WLC_GET_BSS_INFO target record (sweet_wlan_l25_rsne.h, sweet_wlan_l25_glue.h)
WL_TESTNAME = "wlan_l25_wl_host_test"      # patch 0282: includes the test above and the store whitelist; kills the mutants of sweet_wlan_l25_wl.h
HDR_FILES = ("sweet_wlan_l25.h", "sweet_wlan_l25_hs.h", "sweet_wlan_l25_wl.h", "sweet_wlan_join.h", "sweet_wlan_l25_rsne.h", "sweet_wlan_l25_glue.h")      # the headers under test; every mutant edits exactly one of them (the first two are killed by TESTNAME, the third by WL_TESTNAME)
SHIM_HDRS = ("sweet_wlan_g2.h", "sweet_wlan_g2d.h", "sweet_wlan_g2e.h", "sweet_wlan_g2r.h", "sweet_wlan_data.h", "sweet_wlan_data_plan.h", "sweet_wlan_join.h", "sweet_wlan_join_fw.h", "sweet_wlan_cred.h", "wlan_l25_host_rj.h", "wlan_l25_host_sc.h", "wlan_l25_host_ka.h", "wlan_l25_host_pr.h", "wlan_l25_wl_host_cases.h", "wlan_4way_vectors.h")      # from the kernel tree (build.sh proves g2.h == patch 0174)

MUTANTS = [
    ("M1 mac", "mac-len-unchecked", "if (cm.status != 0 || cm.resp_len < 6u) {", "if (cm.status != 0) {"),
    ("M1 mac", "mac-status-unchecked", "if (cm.status != 0 || cm.resp_len < 6u) {", "if (cm.resp_len < 6u) {"),
    ("M1 mac", "mac-mismatch-unchecked", "if (!swl25_zero(want, 6u) && memcmp(want, mac, 6) != 0) {", "if (0) {"),
    ("M1 mac", "mac-valid-unchecked", "if (swl25_zero(mac, 6u) || (mac[0] & 1u) != 0u) {", "if (0) {"),
    ("M1 mac", "mac-used-from-cfg", "swdp_net_init(&h->net, mac, c->ip, c->gw);", "swdp_net_init(&h->net, c->mac, c->ip, c->gw);"),
    ("M1 mac", "mac-ctx-from-cfg", "swj_ctx_init_ex(&h->sup, c->pmk, mac, c->bssid", "swj_ctx_init_ex(&h->sup, c->pmk, c->mac, c->bssid"),
    ("M1 mac", "mac-get-is-a-set", "SWD4B_WLC_GET_VAR, \"WLC_GET_VAR cur_etheraddr\"", "SWD4B_WLC_SET_VAR, \"WLC_GET_VAR cur_etheraddr\""),
    ("E2 overlap", "overlap-check-dropped", "(d < x->in.blk + x->in.blk_len && d + x->in.data_len > x->in.blk)", "0"),
    ("E2 no block", "no-block-check-dropped", "if (d == 0u || x->in.data_len == 0u) {", "if (0) {"),
    ("E2 too small", "min-size-dropped", "x->in.data_len < SWL25_DATA_MIN || ", ""),
    ("E2 alignment", "align-dropped", " || (d & 0xFFFu) != 0u", ""),
    ("E2 env audit", "env-ok-dropped", "r = swdp_env_ok(e);", "r = 0;"),
    ("E1 shadow", "flow-shadow-not-installed", "\tx->dpsh[1] = (struct swg_dp_sh){ e->flow.pa, sizeof l->sh_fl / 4u, l->sh_fl };\n", ""),
    ("E1 shadow", "ring1-shadow-not-installed", "\tx->dpsh[0] = (struct swg_dp_sh){ e->ring1.pa, sizeof l->sh_r1 / 4u, l->sh_r1 };\n", ""),
    ("F4 epoch", "r2-epoch-offset-wrong", "x->d4rd + SWDP_EPOCH_INIT)", "x->d4rd)"),
    ("F2 status", "create-status-unchecked", "if (swdp_parse_flow_cmpl(cmp, &c) != 0 || swdp_flow_on_create_cmpl(&l->flow, &c) != 0) {", "if (0) {"),
    ("F1 consume", "cmpl-not-consumed", "return swg_dp_exec(x, &l->env, s, n, \"consume ring 2\", 0);", "return 0;"),
    ("F1 consume", "consume-wrong-rd", "(x->d4rd + 1u) % SWG_R2_DEPTH, s)", "(x->d4rd + 2u) % SWG_R2_DEPTH, s)"),
    ("F3 timeout", "no-answer-unnoticed", "if (w <= x->d4rd) {", "if (0) {"),
    ("F1 flow ring", "flow-ring-pa-wrong", "l->env.flow.pa, l->env.depth_flow, l->env.flow_isz) != 0) {", "l->env.txbuf.pa, l->env.depth_flow, l->env.flow_isz) != 0) {"),
    ("F1 flow id", "flow-id-wrong", "#define SWL25_FLOWID     SWDP_FLOWID_FIRST", "#define SWL25_FLOWID     (SWDP_FLOWID_FIRST + 1u)"),
    ("R1 buffer", "rx-buffer-pa-not-per-slot",
     "l->env.rxbuf.pa + (uint64_t)(rid - SWDP_RX_RID) * SWL25_BUF_SZ, SWL25_BUF_SZ) != 0) {\n\t\t\treturn swl25_refuse(", "l->env.rxbuf.pa, SWL25_BUF_SZ) != 0) {\n\t\t\treturn swl25_refuse("),
    ("R1 buffer", "rx-repost-buffer-pa-not-per-slot",          # the re-post copy (d9ac7e20)
     "l->env.rxbuf.pa + (uint64_t)(rid - SWDP_RX_RID) * SWL25_BUF_SZ, SWL25_BUF_SZ) != 0) {\n\t\treturn swl25_rxgap(", "l->env.rxbuf.pa, SWL25_BUF_SZ) != 0) {\n\t\treturn swl25_rxgap("),
    ("R1 count", "fifteen-posts", "#define SWL25_RXPOSTS    16u", "#define SWL25_RXPOSTS    15u"),
    ("R1 stop", "post-error-ignored", "\t\tif (ex != 0 || x->d4dstop) {\n\t\t\treturn ex;\n\t\t}\n\t\tl->env.wr1 = l->r1.wr;", "\t\tl->env.wr1 = l->r1.wr;"),
    ("R3 book", "book-full-unnoticed",
     "if (rid == 0u || swdp_ring_post(&l->r1, &slot, &ep) != 0) {\n\t\t\treturn swl25_refuse(", "if (swdp_ring_post(&l->r1, &slot, &ep) != 0) {\n\t\t\treturn swl25_refuse("),
    # ---- sweet_wlan_l25_hs.h (rx poll, host 4-way, key installs, ping)
    ("H6 consume", "ring-consume-wrong-rd", "n = swdp_plan_consume(&h->l.env, ring, (r->rd + 1u) % r->depth, s);", "n = swdp_plan_consume(&h->l.env, ring, (r->rd + 2u) % r->depth, s);"),
    ("H6 consume", "cursor-not-advanced", "(void)swdp_d2h_consume(r);", ";"),
    ("H6 epoch", "d2h-epoch-unchecked", "ex = swdp_d2h_check(it, isz, r->seq);", "ex = 0;"),
    ("H6 offset", "rx-data-offset-ignored", "(uint64_t)idx * SWL25_BUF_SZ + c.data_offset, c.data_len, fr);", "(uint64_t)idx * SWL25_BUF_SZ, c.data_len, fr);"),
    ("H6 buffer", "rx-buffer-index-ignored", "h->l.env.rxbuf.pa + (uint64_t)idx * SWL25_BUF_SZ + c.data_offset", "h->l.env.rxbuf.pa + c.data_offset"),
    ("H6 length", "rx-frame-one-short", "ex = swl25_frame(x, h, fr, c.data_len);", "ex = swl25_frame(x, h, fr, c.data_len - 1u);"),
    ("H6 dead link", "d2h-dead-link-unchecked", "if (v == 0xFFFFFFFFu) {\n\t\tx->fail = \"an L25 D2H write index", "if (0) {\n\t\tx->fail = \"an L25 D2H write index"),
    ("H6 index", "d2h-write-index-unchecked", "if (pend < 0) {", "if (0) {"),
    # "rx-exhaustion-unnoticed" (H6 exhausted, `if (ring == 4u && h->l.rxb.inflight == 0u)` -> `if (0)`) retired 2026-11-10: equivalent since d9ac7e20. The ring-4 path
    # re-posts right after a successful swdp_book_done, so swl25_rxrepost either takes the freed id (inflight >= 1) or stops first (rxgap); that check is unreachable.
    # Same reason no mutant drops `rid == 0u ||` in swl25_rxrepost (a take after a done never returns 0). rxb is taken only in swl25_rxposts and swl25_rxrepost.
    ("H6 tx id", "tx-status-id-unchecked", "|| idx >= SWDP_NSLOT || !h->txb.used[idx] || (h->txdone & (1u << idx)) != 0u) {", "|| idx >= SWDP_NSLOT) {"),
    ("H6 tx error", "tx-status-error-ignored", "if ((int16_t)c.status != 0 || c.tx_status != 0u) {", "if (0) {"),
    ("H6 rekey", "eapol-after-done-passed-on", "if (h->hs_done) {\n\t\t\tprintf(\"%s\\n\", SWJ_GAP_X_REKEY);", "if (0) {\n\t\t\tprintf(\"%s\\n\", SWJ_GAP_X_REKEY);"),
    ("H7 keys wait", "m4-status-not-awaited", "if (idx < SWDP_NSLOT && (h->txdone & (1u << idx)) == 0u) {", "if (0) {"),
    ("H7 keys trigger", "keys-need-no-m4", "if (h->sup.install_ptk && h->sup.install_gtk) {", "if (0) {"),
    ("H8 pairwise", "pairwise-builds-group-key", "if (swj_wsec_key_pairwise(&h->sup, kb) != 0) {\n\t\treturn swl25_gap(x, \"H8", "if (swj_wsec_key_group(&h->sup, kb) != 0) {\n\t\treturn swl25_gap(x, \"H8"),      # patch 0282: the re-join (swl25_rj_finish) has the same call, so the pattern names the boot path by what follows
    ("H9 group", "group-builds-pairwise-key", "if (swj_wsec_key_group(&h->sup, kb) != 0) {\n\t\treturn swl25_gap(x, \"H9", "if (swj_wsec_key_pairwise(&h->sup, kb) != 0) {\n\t\treturn swl25_gap(x, \"H9"),
    ("H8 secret", "key-call-not-secret", "\tint ex;\n\n\tex = swg_e_call_secret(x, SWD4B_WLC_SET_VAR, \"WLC_SET_VAR wsec_key\", \"wsec_key\", kb, SWJ_WSEC_KEY_SIZE, 9u + SWJ_WSEC_KEY_SIZE, 1, 0u, NULL, 1, &cm);", "\tint ex;\n\n\tex = swg_e_call(x, SWD4B_WLC_SET_VAR, \"WLC_SET_VAR wsec_key\", \"wsec_key\", kb, SWJ_WSEC_KEY_SIZE, 9u + SWJ_WSEC_KEY_SIZE, 1, 0u, NULL, 1, &cm, NULL);"),
    ("H8 status", "key-status-ignored", "if (cm.status != 0) {\n\t\tsnprintf(what,", "if (0) {\n\t\tsnprintf(what,"),
    ("H10 get", "wsec-get-status-ignored", "if (cm.status != 0) {\n\t\tSWG_J_STOP(x, \"H10\", \"wsec get", "if (0) {\n\t\tSWG_J_STOP(x, \"H10\", \"wsec get"),
    ("H10 set", "wsec-set-status-ignored", "if (cm.status != 0) {\n\t\tSWG_J_STOP(x, \"H10\", \"wsec set", "if (0) {\n\t\tSWG_J_STOP(x, \"H10\", \"wsec set"),
    ("H10 merge", "wsec-merge-without-aes", "swj_wsec_merge(cur, SWJ_WSEC_AES));\n\tif (nl == 0u) {", "swj_wsec_merge(cur, 0u));\n\tif (nl == 0u) {"),
    ("H12 budget", "4way-budget-short", "while (h->ms - t0 < SWG_J_BUDGET_MS) {", "while (h->ms - t0 < 1u) {"),
    ("H12 wipe", "key-material-not-wiped", "swl25_wipe(&swl25_H.sup, sizeof swl25_H.sup);       /* pmk, tptk, gtk: on every path */", ";"),
    ("L26 arp", "arp-two-tries", "#define SWL25_ARP_TRIES   3u", "#define SWL25_ARP_TRIES   2u"),
    ("L26 count", "nine-echo-requests", "#define SWL25_PINGS       10u", "#define SWL25_PINGS       9u"),   # 10 since da3cbb9f (W-PING 10/10)
    ("L26 gateway", "silent-gateway-unnoticed", "if (ex == 0 && !x->d4dstop && !h->net.gw_known) {", "if (0) {"),
    ("L26 summary", "summary-not-printed", "\tswdp_ping_summary(&h->net);\n\treturn ex;", "\treturn ex;"),
    ("cfg", "no-context-unchecked", "if (c == NULL) {\n\t\treturn swl25_gap(x, \"L25 not run: no join context", "if (0) {\n\t\treturn swl25_gap(x, \"L25 not run: no join context"),
    ("cfg", "bssid-zero-accepted", "swl25_zero(c->bssid, 6u) || ", ""),
    ("cfg", "multicast-mac-accepted", "(c->mac[0] & 1u) != 0u || ", ""),
    ("cfg", "ip-zero-accepted", "swl25_zero(c->ip, 4u) != swl25_zero(c->gw, 4u)) {", "!swl25_zero(c->ip, 4u) && swl25_zero(c->gw, 4u)) {"),      # patch 0281: ip == gw == 0 is DHCP, exactly one zero is refused; this mutant refuses only the zero gateway
    ("cfg", "gw-zero-accepted", "swl25_zero(c->ip, 4u) != swl25_zero(c->gw, 4u)) {", "swl25_zero(c->ip, 4u) && !swl25_zero(c->gw, 4u)) {"),      # ... and this one only the zero IP
    ("cfg", "one-zero-address-accepted", "swl25_zero(c->ip, 4u) != swl25_zero(c->gw, 4u)) {", "0) {"),
    ("L26 dhcp", "dhcp-credentials-still-ping", "if (swl25_zero(c->ip, 4u) && swl25_zero(c->gw, 4u)) {\n\t\t/* patch 0281: DHCP", "if (0) {\n\t\t/* patch 0281: DHCP"),
    ("cfg", "rsne-refusal-ignored", "!= 0) {\n\t\treturn swl25_gap(x, \"L25 not run: RSNE missing or too long\");", "== 99) {\n\t\treturn swl25_gap(x, \"L25 not run: RSNE missing or too long\");"),
    # ---- T-9d71: the RSNE of the firmware's association request goes into M2
    ("A1 source", "m2-takes-beacon-copy", "h->arsne_len != 0u ? h->arsne : c->rsne_assoc, h->arsne_len != 0u ? h->arsne_len : c->rsne_assoc_len,", "c->rsne_assoc, c->rsne_assoc_len,"),
    ("A1 source", "m2-length-from-cfg", "h->arsne_len != 0u ? h->arsne_len : c->rsne_assoc_len,", "c->rsne_assoc_len,"),
    ("A1 gap", "read-rsne-still-reports-ug-gap", "c->rsne_beacon, c->rsne_beacon_len, h->arsne_len != 0u) != 0) {", "c->rsne_beacon, c->rsne_beacon_len, 0) != 0) {"),
    ("A1 order", "assoc-read-not-run", "ex = swl25_assoc_rsne(x, &swl25_H);", "ex = 0;"),
    ("A2 info", "assoc-info-is-a-set", "SWD4B_WLC_GET_VAR, \"WLC_GET_VAR assoc_info\"", "SWD4B_WLC_SET_VAR, \"WLC_GET_VAR assoc_info\""),
    ("A2 ies", "assoc-ies-is-a-set", "SWD4B_WLC_GET_VAR, \"WLC_GET_VAR assoc_req_ies\"", "SWD4B_WLC_SET_VAR, \"WLC_GET_VAR assoc_req_ies\""),
    ("A2 info status", "assoc-info-status-ignored", "if (cm.status != 0) {\n\t\tsnprintf(gb, sizeof gb, \"assoc_info refused", "if (0) {\n\t\tsnprintf(gb, sizeof gb, \"assoc_info refused"),
    ("A2 ies status", "assoc-ies-status-ignored", "if (cm.status != 0) {\n\t\tsnprintf(gb, sizeof gb, \"assoc_req_ies refused", "if (0) {\n\t\tsnprintf(gb, sizeof gb, \"assoc_req_ies refused"),
    ("A2 info len", "assoc-info-short-answer-taken", "if (cm.resp_len < 12u) {", "if (0) {"),
    ("A2 reassoc", "reassoc-header-not-subtracted", "hdr = 4u + ((flags & 1u) != 0u ? 6u : 0u);", "hdr = 4u;"),
    ("A2 reassoc", "reassoc-flag-inverted", "hdr = 4u + ((flags & 1u) != 0u ? 6u : 0u);", "hdr = 4u + ((flags & 1u) == 0u ? 6u : 0u);"),
    ("A2 bounds", "empty-request-taken", "if (req_len <= hdr || req_len - hdr > SWL25_AIES_LEN) {", "if (req_len - hdr > SWL25_AIES_LEN) {"),
    ("A2 bounds", "oversize-request-taken", "if (req_len <= hdr || req_len - hdr > SWL25_AIES_LEN) {", "if (req_len <= hdr) {"),
    ("A2 bounds", "short-ies-answer-taken", "if (avail < iel) {", "if (0) {"),
    ("A2 bounds", "ies-parsed-beyond-request", "fr = swl25_find_rsne(swl25_ai_rsp, iel, &off, &len);", "fr = swl25_find_rsne(swl25_ai_rsp, avail, &off, &len);"),
    ("A3 parse", "element-id-not-rsne", "if (ies[pos] == 48u) {", "if (ies[pos] == 221u) {"),
    ("A3 parse", "overrunning-element-taken", "if (pos + 2u + el > n) {\n\t\t\treturn -1;", "if (0) {\n\t\t\treturn -1;"),
    ("A3 parse", "rsne-minimum-dropped", "if (len < 4u || len > sizeof h->arsne) {", "if (len > sizeof h->arsne) {"),
    ("A3 parse", "rsne-maximum-dropped", "if (len < 4u || len > sizeof h->arsne) {", "if (len < 4u) {"),
    ("A3 parse", "rsne-off-by-one", "*len = 2u + el;", "*len = 1u + el;"),
    ("A3 parse", "rsne-offset-ignored", "memcpy(h->arsne, swl25_ai_rsp + off, len);", "memcpy(h->arsne, swl25_ai_rsp, len);"),
    ("A3 parse", "arsne-len-not-set", "\th->arsne_len = len;\n", "\t;\n"),
    ("A4 report", "report-equal-always-one", "(h->arsne_len == s->rsne_assoc_len && memcmp(h->arsne, s->rsne_assoc, h->arsne_len) == 0) ? 1 : 0,", "1,"),
    ("A4 report", "report-first-diff-off", "d != 0u ? (int)first : -1);", "-1);"),
    ("A4 report", "report-no-unread-line", "printf(\"SWEET WLAN: L25 rsne assoc=unread m2=%u", "if (0) printf(\"SWEET WLAN: L25 rsne assoc=unread m2=%u"),
]


sys.path.insert(0, HERE)
import wlan_keep_mutants  # noqa: E402  patch 0282: the re-join rows (state machine, kept secrets, EAPOL hook, whitelist rows)
MUTANTS += wlan_keep_mutants.L25_ROWS + wlan_keep_mutants.WL_ROWS + wlan_keep_mutants.BI_ROWS

PATCH0167 = os.path.join(ROOT, "xnu", "patches", "0167-sweet-wlan-g1-host-dma.patch")
PATCH0174 = os.path.join(ROOT, "xnu", "patches", "0174-sweet-wlan-g2-first-dma.patch")
_GEN = {}


def out_headers(work):
    """The headers build.sh writes into shim/out (wr, chip, dma, d4b), as paths. With --out DIR: DIR/*.h. Without it: extracted from the patch chain into WORK
    (the way test_wlan_dp15_mutants.py does), so the unit shard does not depend on a shim/out that lane A of the gate may still be building (T-ba7b)."""
    if "--out" in sys.argv:
        return glob.glob(os.path.join(OUT, "*.h"))
    if work not in _GEN:
        import importlib.util
        spec = importlib.util.spec_from_file_location("wr_mutants_helpers", os.path.join(ROOT, "tools", "test_wlan_wr_mutants.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        d = os.path.join(work, "gen")
        os.makedirs(d)
        for name, text in (("sweet_wlan_wr.h", w.final_header()), ("sweet_wlan_chip.h", w.chip_header()),
                           ("sweet_wlan_dma.h", w.new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h")), ("sweet_wlan_d4b.h", w.new_file(PATCH0174, "bsd/dev/sweet_wlan_d4b.h"))):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        _GEN[work] = glob.glob(os.path.join(d, "*.h"))
    return _GEN[work]


def run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def build_and_run(hdrs, tag, work, tests=(TESTNAME,)):
    d = os.path.join(work, tag)
    os.makedirs(d)
    for f in out_headers(work):
        shutil.copy(f, d)
    for f in SHIM_HDRS:
        shutil.copy(os.path.join(SHIM, f), d)
    for name, text in hdrs.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    for t in (TESTNAME, WL_TESTNAME, GLUE_TESTNAME):
        shutil.copy(os.path.join(SHIM, t + ".c"), d)
    for tn in tests:
        for lane, flags in (("plain", ["-O1"]), ("asan", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"])):
            exe = os.path.join(d, "t_" + tn + "_" + lane)
            c = run(["gcc"] + flags + ["-Wall", "-Wextra", "-Werror", "-D_GNU_SOURCE", "-I" + d, "-o", exe, os.path.join(d, tn + ".c")])
            if c.returncode:
                return lane + "-build", next((x for x in c.stderr.splitlines() if "error" in x), "build failed")[-110:]
            r = run([exe])
            if r.returncode != 0 or "RESULT: PASS " + tn not in r.stdout:
                first = next((x for x in (r.stderr + r.stdout).splitlines() if "FAIL" in x or "ERROR" in x or "runtime error" in x), "rc=%d" % r.returncode)
                return lane, first.strip()[:100]
    return "", ""


def main():
    srcs = {h: open(os.path.join(SHIM, h), encoding="utf-8").read() for h in HDR_FILES}
    with tempfile.TemporaryDirectory(prefix="l25mut-") as work:
        lane, msg = build_and_run(srcs, "real", work, (TESTNAME, WL_TESTNAME, GLUE_TESTNAME))
        if lane:
            print("RESULT: FAIL wlan_l25_mutants (the unmutated test does not pass in the %s lane: %s)" % (lane, msg))
            return 1
        jobs = []
        for clause, name, old, new in MUTANTS:
            hits = [h for h in HDR_FILES if srcs[h].count(old) >= 1]
            if len(hits) != 1 or srcs[hits[0]].count(old) != 1:
                print("RESULT: FAIL wlan_l25_mutants (mutant %s: pattern occurs %s, must be exactly once in exactly one header)" % (name, [(h, srcs[h].count(old)) for h in HDR_FILES]))
                return 1
            mut = dict(srcs)
            mut[hits[0]] = srcs[hits[0]].replace(old, new)
            jobs.append((clause, name, mut, "m%02d" % len(jobs), (WL_TESTNAME,) if hits[0] == "sweet_wlan_l25_wl.h" else (GLUE_TESTNAME,) if hits[0] in ("sweet_wlan_l25_rsne.h", "sweet_wlan_l25_glue.h") else (TESTNAME,)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as ex:
            results = list(ex.map(lambda j: build_and_run(j[2], j[3], work, j[4]), jobs))
    survived = []
    for (clause, name, _t, _tag, _tn), (lane, msg) in zip(jobs, results):
        print("%-8s %-28s %s" % ("KILLED" if lane else "SURVIVED", name, ("%s: %s" % (lane, msg)) if lane else ""))
        if not lane:
            survived.append(name)
    if survived:
        print("RESULT: FAIL wlan_l25_mutants (%d of %d mutants survived: %s)" % (len(survived), len(jobs), ", ".join(survived)))
        return 1
    print("RESULT: PASS wlan_l25_mutants (null mutant survives the build; all %d mutants killed)" % len(jobs))
    return 0


class TestWlanL25Mutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table; the RESULT line is on stdout."""

    def test_every_mutant_fails_the_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
