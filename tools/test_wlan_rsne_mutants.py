#!/usr/bin/env python3
"""CR-02 for the L23 RSNE hook (shim/sweet_wlan_l25_rsne.h and its one call site in shim/sweet_wlan_g2.h swg_e_event; T-37d8 step 1) and the glue-side inputs (shim/sweet_wlan_l25_glue.h, steps 2-3):
shim/wlan_rsne_host_test.c and shim/wlan_glue_l25_host_test.c must pass on the real headers (null mutant, must SURVIVE) and each mutant must make one of them FAIL. A mutant is a private copy of ONE header with ONE exact-string edit (the pattern must occur exactly once in that header,
else the runner fails: no silent hole); it is killed when the plain or the ASan/UBSan build of the test fails. The other headers come from a finished shim/build.sh output dir (--out DIR, default shim/out).
Usage: tools/test_wlan_rsne_mutants.py [--kernel-root DIR] [--out DIR]. No hardware."""
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
TESTNAMES = ("wlan_rsne_host_test", "wlan_glue_l25_host_test")
HDR_FILES = ("sweet_wlan_l25_rsne.h", "sweet_wlan_g2.h", "sweet_wlan_l25_glue.h")      # the headers under test; every mutant edits exactly one of them (g2.h: from shim/, build.sh proves it equals patch 0174)
RSNE = "sweet_wlan_l25_rsne.h"
G2 = "sweet_wlan_g2.h"
GL = "sweet_wlan_l25_glue.h"
HOOK = "ex = x->bssn == NULL ? 0 : x->bssn(x, ev, bp, x->rxoff + SWG_E_FRAME + 12u, ev->edlen - 12u);"

MUTANTS = [
    ("P1 once", "ok-rsne-replaced", RSNE, "t->state == SWL25R_OK || swl25r_zero(t->bssid, 6u)", "swl25r_zero(t->bssid, 6u)"),
    ("P4b wildcard", "wildcard-looked-at", RSNE, " || swl25r_zero(t->bssid, 6u) || memcmp", " || memcmp"),
    ("P3 target", "other-bssid-read", RSNE, " || memcmp(ev->bssid, t->bssid, 6) != 0) {", ") {"),
    ("P12 version", "version-unchecked", RSNE, "if (ev->bver != SWG_E_BSS_VER) {", "if (0) {"),
    ("P11b head", "short-head-unchecked", RSNE, "if (avail < SWL25R_HEAD) {\n\t\treturn swl25r_bad(t, \"record shorter", "if (0) {\n\t\treturn swl25r_bad(t, \"record shorter"),
    ("P11 length", "length-above-event-unchecked", RSNE, "if (avail < SWL25R_HEAD || len > avail) {", "if (avail < SWL25R_HEAD) {"),
    ("P10 ieoff", "ieoff-below-head-unchecked", RSNE, "if (*ieo < SWL25R_HEAD || *iel > len ||", "if (*iel > len ||"),
    ("P18 iel", "iel-above-length-unchecked", RSNE, "*iel > len || *ieo > len - *iel", "*ieo > len - *iel"),
    ("P9 ieoff+iel", "ie-end-unchecked", RSNE, " || *ieo > len - *iel) {", ") {"),
    ("P9 sum", "length-sum-unchecked", RSNE, "if (*ieo + ((*iel + 3u) & ~3u) != len) {", "if (0) {"),
    ("P1 round", "length-not-rounded", RSNE, "((*iel + 3u) & ~3u)", "*iel"),
    ("P8 ie end", "ie-overrun-unchecked", RSNE, "if (pos + 2u + eh[1] > ieo + iel) {", "if (0) {"),
    ("P7 ie end", "ie-end-off-by-one", RSNE, "if (pos + 2u + eh[1] > ieo + iel) {", "if (pos + 2u + eh[1] >= ieo + iel) {"),
    ("P1 id", "wrong-element-id", RSNE, "#define SWL25R_IE_RSN    48u", "#define SWL25R_IE_RSN    221u"),
    ("P6 min", "short-rsne-accepted", RSNE, "eh[1] < 2u || ", ""),
    ("P6 max", "long-rsne-accepted", RSNE, "2u + eh[1] > SWL25R_RSNE_MAX)", "2u + eh[1] > SWL25R_RSNE_MAX + 1u)"),
    ("P1 len", "rsne-len-without-header", RSNE, "t->rsne_len = 2u + eh[1];", "t->rsne_len = eh[1];"),
    ("P1 copy", "rsne-copy-without-header", RSNE, "ex = swl25r_read(x, src, pos, 2u + eh[1], t->rsne);", "ex = swl25r_read(x, src, pos + 2u, eh[1], t->rsne);"),
    ("P1 state", "ok-state-not-set", RSNE, "t->state = SWL25R_OK;\n", ""),
    ("P17 cap", "ie-cap-off-by-one", RSNE, "k < SWL25R_MAXIE && pos", "k <= SWL25R_MAXIE && pos"),
    ("P17 cap", "ie-cap-removed", RSNE, "k < SWL25R_MAXIE && pos", "pos"),
    ("P17 msg", "cap-message-lost", RSNE, "k >= SWL25R_MAXIE ? \"no RSNE in the first 128 IEs\" : ", ""),
    ("P5 bad", "bad-state-not-set", RSNE, "\tt->state = SWL25R_BAD;\n", ""),
    ("P5 gap", "bad-gap-not-printed", RSNE, "\tprintf(\"WIFI GAP: %s\\n\", gb);\n\treturn 0;\n}", "\treturn 0;\n}"),
    ("P15 read", "length-read-error-ignored", RSNE, "\tif (ex != 0) {\n\t\treturn ex;\n\t}\n\tlen =", "\tlen ="),
    ("P15 read", "ie-read-error-ignored", RSNE, "ex = swl25r_read(x, src, pos, 2u, eh);\n\t\tif (ex != 0) {\n\t\t\treturn ex;\n\t\t}", "ex = swl25r_read(x, src, pos, 2u, eh);"),
    ("P4 status", "status-wildcard-ok", RSNE, "if (swl25r_zero(t->bssid, 6u)) {\n\t\twhy =", "if (0) {\n\t\twhy ="),
    ("P3 status", "status-never-seen-ok", RSNE, "} else if (t->state == SWL25R_NONE) {", "} else if (0) {"),
    ("P5 status", "status-bad-ok", RSNE, "} else if (t->state != SWL25R_OK) {", "} else if (0) {"),
    ("P1 status", "status-always-gap", RSNE, "\tif (why == NULL) {\n\t\treturn 0;\n\t}", "\tif (0) {\n\t\treturn 0;\n\t}"),
    ("P1 hook", "hook-not-called", G2, HOOK, "ex = 0;"),
    ("P2 hook", "hook-rx-offset-dropped", G2, "x->bssn(x, ev, bp, x->rxoff + SWG_E_FRAME + 12u,", "x->bssn(x, ev, bp, SWG_E_FRAME + 12u,"),
    ("P2 hook", "hook-record-offset-wrong", G2, "x->bssn(x, ev, bp, x->rxoff + SWG_E_FRAME + 12u,", "x->bssn(x, ev, bp, x->rxoff + SWG_E_FRAME + 8u,"),
    ("P11 hook", "hook-avail-includes-header", G2, "ev->edlen - 12u);", "ev->edlen);"),
    ("P15 hook", "hook-stop-ignored", G2, "\t\t\tif (ex != 0) {\n\t\t\t\treturn ex;\n\t\t\t}\n\t\t}\n\t}\n\tx->epid[sl] = 0u;", "\t\t}\n\t}\n\tx->epid[sl] = 0u;"),      # T-866b: anchored on the statement that follows the hook call now (x->epid[sl] = 0u precedes x->evn++)
    ("P16 hook", "hook-without-null-check", G2, "ex = x->bssn == NULL ? 0 : x->bssn(", "ex = x->bssn("),
    ("D1 missing", "data-missing-unchecked", GL, "if (pa == 0u || len == 0u) {", "if (0) {"),
    ("D3 size", "data-size-unchecked", GL, "len < SWL25_DATA_MIN || ", ""),
    ("D4 align", "data-align-unchecked", GL, " || (pa & 0xFFFu) != 0u) {", ") {"),
    ("D8 wrap", "data-wrap-unchecked", GL, "} else if (pa + len < pa) {", "} else if (0) {"),
    ("D5 overlap", "data-overlap-unchecked", GL, "} else if (pa < in->blk + in->blk_len && pa + len > in->blk) {", "} else if (0) {"),
    ("D10 edge", "data-overlap-end-inclusive", GL, "pa + len > in->blk) {", "pa + len >= in->blk) {"),
    ("D10 edge", "data-overlap-start-inclusive", GL, "pa < in->blk + in->blk_len &&", "pa <= in->blk + in->blk_len &&"),
    ("D6 alias", "data-alias-unchecked", GL, "} else if ((pa >> 32) != 0u && !(", "} else if (0 && !("),
    ("D7 alias", "data-alias-below-4g-checked", GL, "(pa >> 32) != 0u && !(", "!("),
    ("D7 alias", "data-alias-low-edge", GL, "lo >= alias_lo &&", "lo > alias_lo &&"),
    ("D7 alias", "data-alias-high-edge", GL, "lo <= (uint64_t)(alias_hi - len)", "lo < (uint64_t)(alias_hi - len)"),
    ("D11 alias", "data-alias-window-size", GL, "len <= alias_hi - alias_lo && ", ""),
    ("A1 data", "data-not-stored", GL, "\tin->data = pa;\n", ""),
    ("A1 data", "data-len-not-stored", GL, "\tin->data_len = len;\n\treturn 0;", "\treturn 0;"),
    ("B1 pmk", "pmk-zero-accepted", GL, "if (cr == NULL || swl25g_zero(cr->pmk, 32u)) {", "if (cr == NULL) {"),
    ("B2 ip", "ip-zero-accepted", GL, "if (cr == NULL || swl25g_zero(cr->ip, 4u)) {", "if (cr == NULL) {"),
    ("B3 gw", "gw-zero-accepted", GL, "if (cr == NULL || swl25g_zero(cr->gw, 4u)) {", "if (cr == NULL) {"),
    ("B3 dhcp", "dhcp-branch-removed", GL, "if (cr != NULL && swl25g_zero(cr->ip, 4u) && swl25g_zero(cr->gw, 4u)) {", "if (0) {"),      # patch 0281: ip == gw == 0 arms (DHCP); without the branch it is refused
    ("B4 bssid", "bssid-zero-accepted", GL, "if ((bssid == NULL || swl25g_zero(bssid, 6u)) && (", "if (bssid == NULL && ("),
    ("B5 bssid", "bssid-null-deref", GL, "if ((bssid == NULL || swl25g_zero(bssid, 6u)) && (", "if ((swl25g_zero(bssid, 6u)) && ("),
    ("B6 all", "missing-input-stops-early", GL, "\t\tswl25g_gap(\"PMK missing\");\n\t\tbad = 1;", "\t\tswl25g_gap(\"PMK missing\");\n\t\treturn -1;"),
    ("A1 cfg", "pmk-not-copied", GL, "memcpy(g->cfg.pmk, cr->pmk, 32);", ";"),
    ("A1 cfg", "ip-not-copied", GL, "memcpy(g->cfg.ip, cr->ip, 4);", ";"),
    ("A1 cfg", "gw-not-copied", GL, "memcpy(g->cfg.gw, cr->gw, 4);", ";"),
    ("A1 cfg", "gw-and-ip-swapped", GL, "memcpy(g->cfg.ip, cr->ip, 4);", "memcpy(g->cfg.ip, cr->gw, 4);"),
    ("A1 cfg", "bssid-not-copied", GL, "memcpy(g->cfg.bssid, bssid, 6);", ";"),
    ("H1 tgt", "target-not-set", GL, "memcpy(g->tgt.bssid, bssid, 6);", ";"),
    ("X1 arm", "arm-last-check-wins", GL, "bad |= swl25g_cfg(g, cr, bssid) != 0;", "bad = swl25g_cfg(g, cr, bssid) != 0;"),
    ("X1 arm", "arm-data-check-skipped-on-cfg", GL, "bad = swl25g_data(&x->in, data_pa, data_len, alias_lo, alias_hi) != 0;\n\tbad |= swl25g_cfg(g, cr, bssid) != 0;", "bad = swl25g_cfg(g, cr, bssid) != 0;\n\tbad = bad || swl25g_data(&x->in, data_pa, data_len, alias_lo, alias_hi) != 0;"),
    ("B arm", "arm-data-kept-on-refusal", GL, "\t\tx->in.data = 0;\n\t\tx->in.data_len = 0;\n\t\tswc_wipe", "\t\tswc_wipe"),
    ("D arm", "arm-cfg-kept-on-refusal", GL, "\t\tswc_wipe(g, (uint32_t)sizeof *g);\n\t\treturn 0;", "\t\treturn 0;"),
    ("A1 arm", "arm-l25c-not-set", GL, "\tx->l25c = &g->cfg;\n\tx->bssn", "\tx->bssn"),
    ("A1 arm", "arm-hook-not-set", GL, "\tx->bssn = swl25g_note;\n", ""),
    ("A1 arm", "arm-hook-ctx-not-set", GL, "\tx->bssc = g;\n\tx->bssget = swl25g_bss_info;\n\treturn 1;", "\tx->bssget = swl25g_bss_info;\n\treturn 1;"),
    ("H1 note", "note-rsne-not-copied", GL, "memcpy(g->cfg.rsne_assoc, g->tgt.rsne, g->tgt.rsne_len);", ";"),
    ("H1 note", "note-beacon-not-copied", GL, "memcpy(g->cfg.rsne_beacon, g->tgt.rsne, g->tgt.rsne_len);", ";"),
    ("H1 note", "note-len-assoc-only", GL, "g->cfg.rsne_assoc_len = g->cfg.rsne_beacon_len = g->tgt.rsne_len;", "g->cfg.rsne_assoc_len = g->tgt.rsne_len;"),
    ("H1 note", "note-copy-before-ok", GL, "g->tgt.state == SWL25R_OK && g->cfg", "g->cfg"),
    ("H1 note", "note-error-swallowed", GL, "\tif (ex == 0) {\n\t\tswl25g_adopt(g);\n\t}\n\treturn ex;\n}", "\tif (ex == 0) {\n\t\tswl25g_adopt(g);\n\t}\n\treturn 0;\n}"),
    ("W1 disarm", "disarm-no-wipe", GL, "\tx->bssc = NULL;\n\tswc_wipe(g, (uint32_t)sizeof *g);\n}", "\tx->bssc = NULL;\n}"),
    ("W1 disarm", "disarm-l25c-kept", GL, "\tx->l25c = NULL;\n\tx->bssn = NULL;\n\tx->bssget = NULL;\n\tx->bssc = NULL;\n\tswc_wipe", "\tx->bssn = NULL;\n\tx->bssget = NULL;\n\tx->bssc = NULL;\n\tswc_wipe"),
    # T-3784 (review T-98d9 r1 F1): the arm decision of sw_g2_hook (swl25g_wl_arm), host case J4
    ("J4 wl_arm", "wlarm-disarm-skipped", GL, "\tif (!armed) {\n\t\tswl25g_disarm(x, g);\n\t} else {", "\tif (0) {\n\t\tswl25g_disarm(x, g);\n\t} else {"),
    ("J4 wl_arm", "wlarm-escan-sync-not-wired", GL, "\t\tw->escan_sync = &x->escan_sync;", "\t\t(void)0;"),
    ("J4 wl_arm", "wlarm-layout-ignored", GL, "swl25g_arm(x, g, cr, bssid, data_pa, data_len, alias_lo, alias_hi) == 1 && lb == 0;", "swl25g_arm(x, g, cr, bssid, data_pa, data_len, alias_lo, alias_hi) == 1;"),
    ("J4 wl_arm", "wlarm-layout-gap-lost", GL, "\tif (lb > 0) {\n\t\tswl_gap(\"l25\", \"glue: L25 store judge layout invalid, L25 not armed\", \"high\");\n", "\tif (lb > 0) {\n"),
    ("J4 wl_arm", "wlarm-layout-log-lost", GL, "\t\tprintf(\"SWEET WLAN: L25 glue: store judge layout clause %d invalid, L25 not armed\\n\", lb);\n", "\t\t(void)lb;\n"),
    ("J4 wl_arm", "wlarm-arm-skipped-after-layout-refusal", GL, "swl25g_arm(x, g, cr, bssid, data_pa, data_len, alias_lo, alias_hi) == 1 && lb == 0;", "lb == 0 && swl25g_arm(x, g, cr, bssid, data_pa, data_len, alias_lo, alias_hi) == 1;"),
    ("J4 wl_arm", "wlarm-layout-tried-without-data", GL, "data_pa == 0u ? -1 : swl25g_wl_layout(w, p,", "swl25g_wl_layout(w, p,"),
    # No row for `keep` error ignored at the call site, for the beacon read (the next read fails too) or for xf not cleared at the start: the engine latches x->stopped on the first failed read, so every later read fails too and the result is discarded (equivalent).
    # 2026-11-11 (W-D3-weak-security): the target record's IE block and plain fields kept for the Get 103 of the kept link (swl25r_keep)
    ("K1 keep", "keep-not-called", RSNE, "\tex = swl25r_keep(x, src, t, len, ieo, iel);\n\tif (ex != 0) {\n\t\treturn ex;\n\t}\n", ""),
    ("K2 beacon", "keep-beacon-offset", RSNE, "ex = swl25r_read(x, src, 14u, 2u, b2);", "ex = swl25r_read(x, src, 16u, 2u, b2);"),
    ("K3 rates", "keep-rate-count-offset", RSNE, "ex = swl25r_read(x, src, 52u, 20u, rs);", "ex = swl25r_read(x, src, 56u, 20u, rs);"),
    ("K3 rates", "keep-rate-read-short", RSNE, "ex = swl25r_read(x, src, 52u, 20u, rs);", "ex = swl25r_read(x, src, 52u, 19u, rs);"),
    ("K3 rates", "keep-rate-clamp", RSNE, "t->nrates = (uint8_t)(n > 16u ? 16u : n);", "t->nrates = (uint8_t)(n > 17u ? 17u : n);"),
    ("K3 rates", "keep-rate-clamp-dropped", RSNE, "t->nrates = (uint8_t)(n > 16u ? 16u : n);", "t->nrates = (uint8_t)n;"),
    ("K3 rates", "keep-rate-index", RSNE, "t->rates[k] = rs[4u + k];", "t->rates[k] = rs[3u + k];"),
    ("K4 chanspec", "keep-chanspec-offset", RSNE, "ex = swl25r_read(x, src, 72u, 2u, b2);", "ex = swl25r_read(x, src, 70u, 2u, b2);"),
    ("K4 chanspec", "keep-chanspec-not-stored", RSNE, "t->chspec = (uint16_t)swg_e_le16(b2);", ""),
    ("K5 noise", "keep-noise-offset", RSNE, "ex = swl25r_read(x, src, 80u, 1u, &nz);", "ex = swl25r_read(x, src, 81u, 1u, &nz);"),
    ("K5 noise", "keep-noise-unsigned", RSNE, "t->noise = (int16_t)(int8_t)nz;", "t->noise = (int16_t)nz;"),
    ("K6 snr", "keep-snr-offset", RSNE, "ex = swl25r_read(x, src, 124u, 2u, b2);", "ex = swl25r_read(x, src, 122u, 2u, b2);"),
    ("K6 snr", "keep-snr-guard-124", RSNE, "if (ieo >= 126u && len >= 126u) {", "if (ieo >= 124u && len >= 126u) {"),
    ("K6 snr", "keep-snr-guard-127", RSNE, "if (ieo >= 126u && len >= 126u) {", "if (ieo >= 127u && len >= 126u) {"),
    ("K6 snr", "keep-snr-always", RSNE, "if (ieo >= 126u && len >= 126u) {", "if (1) {"),
    ("K6 snr", "keep-snr-not-reset", RSNE, "\tt->snr = 0;\n", ""),
    ("K6 snr", "keep-snr-unsigned", RSNE, "t->snr = (int16_t)swg_e_le16(b2);", "t->snr = (int16_t)(swg_e_le16(b2) & 0x7FFFu);"),
    ("K7 ies", "keep-ie-cap-off-by-one", RSNE, "n = iel > SWL25R_IE_KEEP ? SWL25R_IE_KEEP : iel;", "n = iel > SWL25R_IE_KEEP ? SWL25R_IE_KEEP - 1u : iel;"),
    ("K7 ies", "keep-ie-cap-dropped", RSNE, "n = iel > SWL25R_IE_KEEP ? SWL25R_IE_KEEP : iel;", "n = iel;"),
    ("K7 ies", "keep-ie-offset", RSNE, "ex = swl25r_read(x, src, ieo, n, t->ie);", "ex = swl25r_read(x, src, ieo + 1u, n, t->ie);"),
    ("K7 ies", "keep-ie-read-error-ignored", RSNE, "ex = swl25r_read(x, src, ieo, n, t->ie);\n\t\tif (ex != 0) {\n\t\t\treturn ex;\n\t\t}", "ex = swl25r_read(x, src, ieo, n, t->ie);"),
    ("K7 ies", "keep-ie-len-not-set", RSNE, "\tt->ie_len = n;\n", ""),
    ("K7 ies", "keep-ie-short-always", RSNE, "t->ie_short = iel > SWL25R_IE_KEEP ? 1u : 0u;", "t->ie_short = 1u;"),
    ("K7 ies", "keep-ie-short-at-cap", RSNE, "t->ie_short = iel > SWL25R_IE_KEEP ? 1u : 0u;", "t->ie_short = iel >= SWL25R_IE_KEEP ? 1u : 0u;"),
    ("K7 ies", "keep-ie-short-never", RSNE, "t->ie_short = iel > SWL25R_IE_KEEP ? 1u : 0u;", "t->ie_short = 0u;"),
    ("K8 flag", "keep-xf-not-set", RSNE, "\tt->xf = 1u;\n\treturn 0;", "\treturn 0;"),
]


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


def build_and_run(hdrs, tag, work):
    d = os.path.join(work, tag)
    os.makedirs(d)
    for f in out_headers(work):
        shutil.copy(f, d)
    for name, text in hdrs.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    for t in TESTNAMES:
        shutil.copy(os.path.join(SHIM, t + ".c"), d)
        for lane, flags in (("plain", ["-O1"]), ("asan", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"])):
            exe = os.path.join(d, "t_" + lane)
            c = run(["gcc"] + flags + ["-Wall", "-Wextra", "-Werror", "-D_GNU_SOURCE", "-I" + d, "-I" + SHIM, "-o", exe, os.path.join(d, t + ".c")])
            if c.returncode:
                return t + " " + lane + "-build", next((x for x in c.stderr.splitlines() if "error" in x), "build failed")[-110:]
            r = run([exe])
            if r.returncode != 0 or "RESULT: PASS " + t not in r.stdout:
                first = next((x for x in (r.stderr + r.stdout).splitlines() if x.startswith("FAIL") or "ERROR" in x or "runtime error" in x), "rc=%d" % r.returncode)
                return t + " " + lane, first.strip()[:100]
    return "", ""


def main():
    srcs = {h: open(os.path.join(SHIM, h), encoding="utf-8").read() for h in HDR_FILES}
    with tempfile.TemporaryDirectory(prefix="rsnemut-") as work:
        lane, msg = build_and_run(srcs, "real", work)
        if lane:
            print("RESULT: FAIL wlan_rsne_mutants (the unmutated test does not pass in the %s lane: %s)" % (lane, msg))
            return 1
        jobs = []
        for clause, name, hdr, old, new in MUTANTS:
            if srcs[hdr].count(old) != 1:
                print("RESULT: FAIL wlan_rsne_mutants (mutant %s: pattern occurs %d times in %s, must be exactly once)" % (name, srcs[hdr].count(old), hdr))
                return 1
            mut = dict(srcs)
            mut[hdr] = srcs[hdr].replace(old, new)
            jobs.append((clause, name, mut, "m%02d" % len(jobs)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as ex:
            results = list(ex.map(lambda j: build_and_run(j[2], j[3], work), jobs))
    survived = []
    for (clause, name, _t, _tag), (lane, msg) in zip(jobs, results):
        print("%-8s %-30s %s" % ("KILLED" if lane else "SURVIVED", name, ("%s: %s" % (lane, msg)) if lane else ""))
        if not lane:
            survived.append(name)
    if survived:
        print("RESULT: FAIL wlan_rsne_mutants (%d of %d mutants survived: %s)" % (len(survived), len(jobs), ", ".join(survived)))
        return 1
    print("RESULT: PASS wlan_rsne_mutants (null mutant survives the build; all %d mutants killed)" % len(jobs))
    return 0


class TestWlanRsneMutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table; the RESULT line is on stdout."""

    def test_every_mutant_fails_the_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
