#!/usr/bin/env python3
"""CR-02 for the Wi-Fi rx pool + batched refill of xnu patch 0357 (shim/sweet_wlan_data.h swdp_rx_pick / swdp_rx_refill_n / the books, sweet_wlan_data_plan.h audit of a ring-1 batch,
wlan_en0.h en0_rx_batch / en0_rx_refill, sweet_wlan_g2d.h batch executor, sweet_wlan_l25_wl.h judge, sweet_wlan_l25.h pool choice, sweet_wlan_l25_glue.h judge layout, sweet_wlan_g2.h firmware read).
Each mutant is a private copy of ONE header with ONE exact-string edit (the pattern must occur exactly once, else the runner itself fails: a mutant that no longer applies is a silent hole) and
must make at least one of the host tests it names fail (plain build; the test source and every other file are symlinks to shim/, so the mutated copy is the one the quote-includes find).
The null mutant (an edit that changes nothing) must SURVIVE: it proves the runner builds and runs the real tests.
Not mutated (EQUIVALENT, the first line is the audit/judge, the second a bound on a value the first already bounds): the batch cap and the ring-end cut inside en0_rx_batch are mutated (they are reachable by a
direct call, host test H); swg_dp_copy's item-word bound (the audit refuses 33 items first); the plan-shape check of en0l_plan_name (the audit is stricter and runs first); swdp_audit's `n > SWDP_MAX_STORES`
exemption (a second item on any other ring is refused by the item rule first); the `nidx ||` guard on a late item (the doorbell rule needs the index right before it, so no plan can have an item after its index); the judge's `slot < cur`
(slot - cur is unsigned: a slot behind the write index wraps to a huge number and fails the window test already).
Usage: tools/test_wlan_rxpool_mutants.py [--kernel-root DIR]   (DIR holds shim/, default: the repo of this file). No hardware."""
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
    "data": ("wlan_data_host_test.c", [], "RESULT: PASS wlan_data_host_test"),
    "en0": ("wlan_en0_host_test.c", [], "RESULT: PASS wlan_en0_host_test"),
    "dp15": ("wlan_dp15_host_test.c", [], "RESULT: PASS wlan_dp15_host_test"),
    "l25wl": ("wlan_l25_wl_host_test.c", [], "RESULT: PASS wlan_l25_wl_host_test"),
    "live": ("wlan_en0_live_host_test.c", ["-DEN0_LIVE_RXN=255"], "RESULT: PASS wlan_en0_live_host_test"),
    "live-arg": ("wlan_en0_live_host_test.c", ["-DEN0_LIVE_RXN=48", "-DEN0_LIVE_FW=255", "-DEN0_LIVE_ARG=48"], "RESULT: PASS wlan_en0_live_host_test"),
    "live-blk": ("wlan_en0_live_host_test.c", ["-DEN0_LIVE_RXN=72", "-DEN0_LIVE_FW=255", "-DEN0_LIVE_BLKRX=72"], "RESULT: PASS wlan_en0_live_host_test"),
    "g2": ("wlan_g2_host_test.c", [], "RESULT: PASS wlan_g2_host_test"),
    "glue": ("wlan_glue_l25_host_test.c", [], "RESULT: PASS wlan_glue_l25_host_test"),
}

D = "sweet_wlan_data.h"
P = "sweet_wlan_data_plan.h"
E = "wlan_en0.h"
X = "sweet_wlan_g2d.h"
J = "sweet_wlan_l25_wl.h"
L = "sweet_wlan_l25.h"
G = "sweet_wlan_l25_glue.h"
R = "sweet_wlan_g2.h"

# (file, name, old, new, tests that must fail)
MUTANTS = [
    (D, "M00-null", "#define SWDP_RX_MAX  255u", "#define SWDP_RX_MAX  255u", []),
    # --- the books
    (D, "book-take-ignores-cap", "	for (i = 0; i < swdp_book_cap(b); i++) {", "	for (i = 0; i < SWDP_RX_MAX; i++) {", ["data"]),
    (D, "book-take-at-ignores-cap", "	if (i >= swdp_book_cap(b) || b->used[i]) {", "	if (b->used[i]) {", ["en0"]),
    (D, "book-set-cap-no-max", "	if (n < SWDP_NSLOT || n > SWDP_RX_MAX || n < swdp_book_cap(b))", "	if (n < SWDP_NSLOT || n < swdp_book_cap(b))", ["en0"]),
    (D, "book-set-cap-shrinks", "	if (n < SWDP_NSLOT || n > SWDP_RX_MAX || n < swdp_book_cap(b))", "	if (n < SWDP_NSLOT || n > SWDP_RX_MAX)", ["en0"]),
    (D, "book-init-cap-zero", "memset(b, 0, sizeof *b); b->base = base; b->cap = SWDP_NSLOT; }", "memset(b, 0, sizeof *b); b->base = base; b->cap = SWDP_RX_MAX; }", ["data"]),
    # --- the pool choice
    (D, "pick-fw-zero-not-255", "if (n == 0u) { s->notes |= SWDP_RXN_FWZERO; n = SWDP_RX_MAX; }", "if (n == 0u) { s->notes |= SWDP_RXN_FWZERO; n = SWDP_NSLOT; }", ["data"]),
    (D, "pick-fw-zero-silent", "if (n == 0u) { s->notes |= SWDP_RXN_FWZERO; n = SWDP_RX_MAX; }", "if (n == 0u) { n = SWDP_RX_MAX; }", ["data"]),
    (D, "pick-fw-above-255-not-clamped", "if (n > SWDP_RX_MAX) { s->notes |= SWDP_RXN_FWCLAMP; n = SWDP_RX_MAX; }", "", ["data"]),
    (D, "pick-fw-below-16-not-held", "if (n < SWDP_NSLOT) { s->notes |= SWDP_RXN_FWLOW; n = SWDP_NSLOT; }", "", ["data"]),
    (D, "pick-fw-ones-is-a-count", "} else if (fw_state == SWDP_RXFW_OK && fw != 0xFFFFu) {", "} else if (fw_state == SWDP_RXFW_OK) {", ["data"]),
    (D, "pick-failed-read-takes-fw", "} else if (fw_state == SWDP_RXFW_OK && fw != 0xFFFFu) {", "} else if (fw_state != SWDP_RXFW_UNREAD && fw != 0xFFFFu) {", ["data"]),
    (D, "pick-failed-read-not-16", "		s->notes |= SWDP_RXN_FWFAIL;\n		n = SWDP_NSLOT;", "		s->notes |= SWDP_RXN_FWFAIL;\n		n = SWDP_RX_MAX;", ["data"]),
    (D, "pick-failed-read-silent", "		s->notes |= SWDP_RXN_FWFAIL;\n		n = SWDP_NSLOT;", "		n = SWDP_NSLOT;", ["data"]),
    (D, "pick-arg-ignored", "	if (has_arg) {", "	if (0) {", ["data"]),
    (D, "pick-arg-not-clamped", "		if (n < SWDP_NSLOT || n > SWDP_RX_MAX) { s->notes |= SWDP_RXN_ARGCLAMP; n = n < SWDP_NSLOT ? SWDP_NSLOT : SWDP_RX_MAX; }", "", ["data"]),
    (D, "pick-arg-clamp-silent", "{ s->notes |= SWDP_RXN_ARGCLAMP; n = n < SWDP_NSLOT", "{ n = n < SWDP_NSLOT", ["data"]),
    (D, "pick-block-ceiling-dropped", "	if (cap < ceil) { ceil = cap; }\n", "", ["data"]),
    (D, "pick-ring-ceiling-dropped", "	if (ringlim < ceil) { ceil = ringlim; }\n", "", ["data"]),
    (D, "pick-ring-holds-depth", "const uint32_t ringlim = ring_depth != 0u ? ring_depth - 1u : 0u;", "const uint32_t ringlim = ring_depth;", ["data"]),
    (D, "pick-ceiling-not-applied", "	if (n > ceil) {", "	if (0) {", ["data"]),
    (D, "pick-ceiling-silent", "		s->notes |= ringlim <= cap ? SWDP_RXN_RING : SWDP_RXN_BLOCK;\n", "", ["data"]),
    (D, "pick-floor-dropped", "	if (n < SWDP_NSLOT) { n = SWDP_NSLOT; }\n	s->n = n;", "	s->n = n;", ["data"]),
    # --- the refill rule
    (D, "thr-not-capped-at-32", "	if (thr > SWDP_RXB_MAX) { thr = SWDP_RXB_MAX; }\n	if (thr == 0u || all || pool <= SWDP_NSLOT)", "	if (thr == 0u || all || pool <= SWDP_NSLOT)", ["data"]),
    (D, "thr-pool-16-batches", "	if (thr == 0u || all || pool <= SWDP_NSLOT) { thr = 1u; }", "	if (thr == 0u || all) { thr = 1u; }", ["data", "en0"]),
    (D, "thr-all-ignored", "	if (thr == 0u || all || pool <= SWDP_NSLOT) { thr = 1u; }", "	if (thr == 0u || pool <= SWDP_NSLOT) { thr = 1u; }", ["data", "en0"]),
    (D, "refill-below-thr-not-held", "	if (freeb == 0u || freeb < thr || depth == 0u || wr >= depth) {", "	if (freeb == 0u || depth == 0u || wr >= depth) {", ["data", "en0"]),
    (D, "refill-at-thr-waits", "	if (freeb == 0u || freeb < thr || depth == 0u || wr >= depth) {", "	if (freeb == 0u || freeb <= thr || depth == 0u || wr >= depth) {", ["data", "en0"]),
    (D, "refill-wr-at-end-posts", "	if (freeb == 0u || freeb < thr || depth == 0u || wr >= depth) {", "	if (freeb == 0u || freeb < thr || depth == 0u) {", ["data"]),
    (D, "refill-batch-above-32", "	n = freeb < SWDP_RXB_MAX ? freeb : SWDP_RXB_MAX;", "	n = freeb;", ["data", "en0"]),
    (D, "refill-over-posts-the-ring", "	if (n > ring_free) { n = ring_free; }\n", "", ["data", "en0"]),
    (D, "refill-crosses-the-ring-end", "	if (n > depth - wr) { n = depth - wr; }\n", "", ["data", "en0"]),
    # --- the audit of a batch
    (P, "env-rxn-above-255", "	if (e->rx_slots != 0u && (e->rx_slots < SWDP_NSLOT || e->rx_slots > SWDP_RX_MAX))", "	if (e->rx_slots != 0u && e->rx_slots < SWDP_NSLOT)", ["data"]),
    (P, "env-rxn-below-16", "	if (e->rx_slots != 0u && (e->rx_slots < SWDP_NSLOT || e->rx_slots > SWDP_RX_MAX))", "	if (e->rx_slots != 0u && e->rx_slots > SWDP_RX_MAX)", ["data"]),
    (P, "env-rx-range-of-16", "e->rxbuf.len < (uint64_t)swdp_env_rxn(e) * e->buf_sz)", "e->rxbuf.len < (uint64_t)SWDP_NSLOT * e->buf_sz)", ["data"]),
    (P, "item-rid-below-16", "swdp_g32(it + 20) != 0u || i >= swdp_env_rxn(e)) return SWDP_A_VALUE;", "swdp_g32(it + 20) != 0u || i >= SWDP_NSLOT) return SWDP_A_VALUE;", ["data", "en0", "live"]),
    (P, "item-rid-above-pool", "swdp_g32(it + 20) != 0u || i >= swdp_env_rxn(e)) return SWDP_A_VALUE;", "swdp_g32(it + 20) != 0u || i >= SWDP_RX_MAX) return SWDP_A_VALUE;", ["data"]),
    (P, "audit-many-stores-any-plan", "	if (n == 0u || n > SWDP_MAX_STORES_RXB || (n > SWDP_MAX_STORES && !(s[0].kind == SWDP_SK_ITEM && s[0].ring == SWDP_RING_RXPOST))) return SWDP_A_ORDER;", "	if (n == 0u || n > 40u) return SWDP_A_ORDER;", ["data"]),
    (P, "audit-batch-of-33", "n > SWDP_MAX_STORES_RXB || (n > SWDP_MAX_STORES", "n > 40u || (n > SWDP_MAX_STORES", ["data", "dp15"]),
    (P, "audit-two-items-any-ring", "(nitem && !(s[i].ring == SWDP_RING_RXPOST && item_ring == SWDP_RING_RXPOST))", "0", ["data"]),
    (P, "audit-slot-hole", "if (s[i].slot != swdp_h2d_cur(e, s[i].ring) + nitem) return SWDP_A_ORDER;", "if (s[i].slot < swdp_h2d_cur(e, s[i].ring) + nitem) return SWDP_A_ORDER;", ["data", "dp15"]),
    (P, "audit-slot-not-advancing", "if (s[i].slot != swdp_h2d_cur(e, s[i].ring) + nitem) return SWDP_A_ORDER;", "if (s[i].slot != swdp_h2d_cur(e, s[i].ring)) return SWDP_A_ORDER;", ["data", "en0"]),
    (P, "audit-duplicate-rid", "if (s[k].kind == SWDP_SK_ITEM && s[i].ring == SWDP_RING_RXPOST && swdp_g32(s[k].data + 4) == swdp_g32(s[i].data + 4)) return SWDP_A_VALUE;", "if (0) return SWDP_A_VALUE;", ["data"]),
    (P, "audit-index-needs-one-item", "if (nitem == 0u || nidx++ || s[i].ring != item_ring", "if (nitem != 1u || nidx++ || s[i].ring != item_ring", ["data", "en0"]),
    (P, "plan-db-names-first-slot", "swdp_st(&o[n + 1u], SWDP_SP_PCIE, SWDP_SK_DB, 4u, SWDP_RING_RXPOST, 0u, SWDP_DB0_OFF, swdp_db_wr(SWDP_RING_RXPOST, new_wr), 0);", "swdp_st(&o[n + 1u], SWDP_SP_PCIE, SWDP_SK_DB, 4u, SWDP_RING_RXPOST, 0u, SWDP_DB0_OFF, swdp_db_wr(SWDP_RING_RXPOST, slot0), 0);", ["data", "en0"]),
    # --- the engine
    (E, "batch-not-capped-at-32", "	if (n > SWDP_RXB_MAX)\n		n = SWDP_RXB_MAX;\n	if (r.depth", "	if (r.depth", ["en0"]),
    (E, "batch-crosses-ring-end", "	if (r.depth != 0u && n > r.depth - r.wr)\n		n = r.depth - r.wr;\n", "", ["en0"]),
    (E, "batch-plan-failure-keeps-buffers", "		for (j = 0; j < got; j++)\n			swdp_book_done(&e->rxb, rid[j], NULL);\n		return 0u;", "		return 0u;", ["en0"]),
    (E, "batch-cursor-not-committed", "	e->rxpost = r;\n	return got;", "	return got;", ["en0", "live"]),
    (E, "batch-failed-item-keeps-buffer", "			swdp_book_done(&e->rxb, rid[got], NULL);\n			break;", "			break;", ["en0"]),
    (E, "refill-first-fill-uses-threshold", "swdp_rx_refill_n(pool, e->rxb.inflight, swdp_ring_free(&e->rxpost), e->rxpost.wr, e->rxpost.depth, all);", "swdp_rx_refill_n(pool, e->rxb.inflight, swdp_ring_free(&e->rxpost), e->rxpost.wr, e->rxpost.depth, 0u);", ["en0"]),
    (E, "refill-not-in-poll", "	if (n > 0u && !e->cons_fail)\n		en0_rx_refill(e, 0u);", "", ["en0", "live"]),
    (E, "refill-after-failed-consume", "	if (n > 0u && !e->cons_fail)\n		en0_rx_refill(e, 0u);", "	if (n > 0u)\n		en0_rx_refill(e, 0u);", ["live", "en0"]),
    (E, "refill-batches-not-counted", "		e->rx_batches++;\n", "", ["en0", "live"]),
    (E, "refill-batch-max-not-kept", "		if (got > e->rx_batch_max)\n			e->rx_batch_max = got;", "", ["en0"]),
    (E, "refill-bufs-not-counted", "		e->rx_batch_bufs += got;\n", "", ["en0"]),
    (E, "refill-trough-not-kept", "	if (!all && (e->rx_posted_min == 0u || e->rxb.inflight + 1u < e->rx_posted_min))\n		e->rx_posted_min = e->rxb.inflight + 1u;", "", ["en0"]),
    (E, "refill-trough-counts-the-fill", "	if (!all && (e->rx_posted_min == 0u", "	if ((e->rx_posted_min == 0u", ["en0"]),
    (E, "refill-peak-not-kept", "	if (e->rxb.inflight > e->rx_posted_peak)\n		e->rx_posted_peak = e->rxb.inflight;", "", ["en0", "live"]),
    (E, "refill-due-not-done-silent", "		if ((e->rx_refill_fail & (e->rx_refill_fail - 1u)) == 0u)\n			en0_gapf(", "		if (0)\n			en0_gapf(", ["en0"]),
    (E, "refill-due-not-done-always-prints", "		if ((e->rx_refill_fail & (e->rx_refill_fail - 1u)) == 0u)\n			en0_gapf(", "		if (1)\n			en0_gapf(", ["en0"]),
    (E, "refill-due-not-counted", "		e->rx_refill_fail++;\n", "", ["en0", "live"]),
    (E, "refill-due-at-thr-ignored", "	if (pool - (e->rxb.inflight < pool ? e->rxb.inflight : pool) >= swdp_rx_thr(pool, all)) {", "	if (pool - (e->rxb.inflight < pool ? e->rxb.inflight : pool) > swdp_rx_thr(pool, all)) {", ["en0"]),
    (E, "rx-completion-slot-16", "slot >= swdp_book_cap(&e->rxb) || !e->rxb.used[slot]) {", "slot >= SWDP_NSLOT || !e->rxb.used[slot]) {", ["en0", "live"]),
    (E, "book-cap-not-widened", "	if (swdp_book_set_cap(&e->rxb, pool) != 0 && swdp_book_cap(&e->rxb) != pool) {", "	if (0) {", ["en0", "live"]),
    # --- the executor (DP15)
    (X, "exec-items-overwrite", "(s[i].kind == SWDP_SK_ITEM ? itemw + *iw : payw)[k]", "(s[i].kind == SWDP_SK_ITEM ? itemw : payw)[k]", ["dp15", "live"]),
    (X, "exec-item-offset-kept", "			io += s[i].len / 4u;", "", ["dp15", "live"]),
    (X, "exec-item-words-not-summed", "*(s[i].kind == SWDP_SK_ITEM ? iw : pw) = s[i].kind == SWDP_SK_ITEM ? *iw + nw : nw;", "*(s[i].kind == SWDP_SK_ITEM ? iw : pw) = nw;", ["dp15", "live"]),
    # --- the judge
    (J, "judge-window-33", "slot - cur >= SWL25W_RXB_MAX || slot >= w->depth[1]", "slot - cur > SWL25W_RXB_MAX || slot >= w->depth[1]", ["l25wl"]),
    (J, "judge-window-dropped", "if (swl25w_h2d_cur(w, 1, &cur) || slot < cur || slot - cur >= SWL25W_RXB_MAX || slot >= w->depth[1]) {", "if (swl25w_h2d_cur(w, 1, &cur)) {", ["l25wl"]),
    (J, "judge-rx-cap-off-by-one", "b >= swl25w_rxn(w))) {", "b > swl25w_rxn(w))) {", ["l25wl"]),
    (J, "judge-rx-cap-fixed-16", "b >= swl25w_rxn(w))) {", "b >= SWL25W_RX_N)) {", ["l25wl", "live"]),
    (J, "judge-rxn-default-zero", "return w->rx_n != 0u ? w->rx_n : SWL25W_RX_N;", "return w->rx_n;", ["l25wl"]),
    (J, "judge-rxn-default-255", "return w->rx_n != 0u ? w->rx_n : SWL25W_RX_N;", "return w->rx_n != 0u ? w->rx_n : SWL25W_RX_MAX;", ["l25wl"]),
    (J, "judge-layout-rxn-unchecked", "	if (w->rx_n != 0u && (w->rx_n < SWL25W_RX_N || w->rx_n > SWL25W_RX_MAX || w->data_len < SWL25W_RX_OFF + w->rx_n * SWL25W_BUF_SZ)) {\n		return 14;", "	if (0) {\n		return 14;", ["l25wl"]),
    (J, "judge-index-32-plus", "if (end <= wp || end - wp > SWL25W_RXB_MAX) {", "if (end <= wp || end - wp >= SWL25W_RXB_MAX) {", ["l25wl", "live"]),
    (J, "judge-index-no-step", "if (end <= wp || end - wp > SWL25W_RXB_MAX) {", "if (end < wp || end - wp > SWL25W_RXB_MAX) {", ["l25wl"]),
    (J, "judge-index-no-limit", "if (end <= wp || end - wp > SWL25W_RXB_MAX) {", "if (end <= wp) {", ["l25wl"]),
    (J, "judge-index-end-at-zero", "const uint32_t end = (v & 0xFFFFu) == 0u ? w->depth[1] : (v & 0xFFFFu);", "const uint32_t end = (v & 0xFFFFu);", ["l25wl"]),
    (J, "judge-index-first-item-only", "for (i = wp; i < end; i++) {", "for (i = wp; i < wp + 1u; i++) {", ["l25wl"]),
    (J, "judge-lap-not-counted", "if (i == 1u && (v & 0xFFFFu) == 0u && (old & 0xFFFFu) != 0u) {", "if (i == 1u && (v & 0xFFFFu) == 0u && (old & 0xFFFFu) == w->depth[1] - 1u) {", ["l25wl"]),
    (J, "judge-ring-room-single-slot", "slot - cur >= ((now & 0xFFFFu) + w->depth[1] - cur - 1u) % w->depth[1])) {", "(slot + 1u) % w->depth[1] == (now & 0xFFFFu))) {", ["l25wl"]),
    (J, "judge-ring-room-off-by-one", "slot - cur >= ((now & 0xFFFFu) + w->depth[1] - cur - 1u) % w->depth[1])) {", "slot - cur > ((now & 0xFFFFu) + w->depth[1] - cur - 1u) % w->depth[1])) {", ["l25wl"]),
    (D, "pick-arg-above-firmware", "	if (has_arg && fw_state == SWDP_RXFW_OK && fw != 0u && fw != 0xFFFFu && n > fw) {", "	if (0) {", ["data"]),
    (D, "pick-arg-above-firmware-silent", "		s->notes |= SWDP_RXN_ARGFW;\n", "", ["data"]),
    (D, "pick-arg-fw-zero-capped", "has_arg && fw_state == SWDP_RXFW_OK && fw != 0u &&", "has_arg && fw_state == SWDP_RXFW_OK &&", ["data"]),
    (D, "refill-pool16-batches", "	if (pool <= SWDP_NSLOT) { n = 1u; }", "", ["data", "en0"]),
    # --- the glue and the engine glue
    (G, "glue-judge-layout-no-rxn", "	w->rx_n = data_len >= SWL25_RX_OFF + SWDP_NSLOT * SWL25_BUF_SZ ? (data_len - SWL25_RX_OFF) / SWL25_BUF_SZ : 0u;", "	w->rx_n = 0u;", ["glue"]),
    (G, "glue-judge-layout-rxn-uncapped", "	if (w->rx_n > SWL25W_RX_MAX) {\n		w->rx_n = SWL25W_RX_MAX;\n	}\n", "", ["glue"]),
    (L, "l25-env-rx-slots-unset", "		e->rx_slots = sel.n;\n", "", ["live"]),
    (L, "l25-env-rx-range-of-16", "e->rxbuf = (struct swdp_range){ d + SWL25_RX_OFF, (uint64_t)sel.n * SWL25_BUF_SZ };", "e->rxbuf = (struct swdp_range){ d + SWL25_RX_OFF, (uint64_t)SWDP_NSLOT * SWL25_BUF_SZ };", ["live"]),
    (L, "l25-env-fw-state-ignored", "swdp_rx_pick(&sel, x->rxfw_state, x->rxfw,", "swdp_rx_pick(&sel, SWDP_RXFW_FAIL, x->rxfw,", ["live"]),
    (L, "l25-env-boot-arg-ignored", "x->in.rxarg_set, x->in.rxarg, e->depth1, cap);", "0u, 0u, e->depth1, cap);", ["live-arg"]),
    (L, "l25-env-block-cap-ignored", "const uint32_t cap = (x->in.data_len - SWL25_RX_OFF) / SWL25_BUF_SZ;", "const uint32_t cap = SWDP_RX_MAX;", ["live-blk"]),
    (L, "l25-env-pool-line-silent", "		SWDP_LOG(lb);\n		if (sel.notes != 0u) {", "		if (sel.notes != 0u) {", ["live"]),
    # --- the firmware read
    (R, "g2-read-wrong-offset", "#define SWG_SH_RXBUFS    0x22u", "#define SWG_SH_RXBUFS    0x26u", ["g2"]),
    (R, "g2-read-low-half", "#define SWG_SH_RXBUFS    0x22u", "#define SWG_SH_RXBUFS    0x20u", ["g2"]),
    (R, "g2-read-ones-accepted", "	if (swg_rd16(x, x->in.sh + SWG_SH_RXBUFS, &x->rxfw) != 0 || x->rxfw == 0xFFFFu) {", "	if (swg_rd16(x, x->in.sh + SWG_SH_RXBUFS, &x->rxfw) != 0) {", ["g2"]),
    (R, "g2-read-failure-silent", "		printf(\"WIFI GAP: shared max_host_rxbufs (+0x22) unreadable or all ones (%#x): the rx pool stays 16\\n\", x->rxfw);\n", "", ["g2"]),
    (R, "g2-read-state-not-set", "		x->rxfw_state = 1u;\n", "", ["g2"]),
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
            print("RESULT: FAIL wlan_rxpool_mutants (mutant %s: pattern occurs %d times in %s, must be exactly 1)" % (name, srcs[rel].count(old), rel))
            return 1
        if name != "M00-null" and not tests:
            print("RESULT: FAIL wlan_rxpool_mutants (mutant %s names no test)" % name)
            return 1
        jobs.append((name, rel, srcs[rel].replace(old, new), tests, "m%03d" % len(jobs)))
    with tempfile.TemporaryDirectory(prefix="rxpoolmut-") as d:
        real = {}
        for t in TESTS:                      # the unmutated tree: every test must pass, or the runner is broken
            msg = build_and_run(d, "real-" + t, D, srcs.get(D) or open(os.path.join(SHIM, D), encoding="utf-8").read(), [t])
            if msg != "":
                print("RESULT: FAIL wlan_rxpool_mutants (the unmutated host test %s does not pass: %s)" % (t, msg))
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
        print("RESULT: FAIL wlan_rxpool_mutants survived=%s" % ",".join(survived))
        return 1
    print("RESULT: PASS wlan_rxpool_mutants mutants=%d killed=%d" % (len(MUTANTS) - 1, len(MUTANTS) - 1))
    return 0


class TestRxpoolMutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table; the RESULT line is on stdout."""

    def test_every_mutant_fails_a_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
