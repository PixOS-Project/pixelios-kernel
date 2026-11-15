#!/usr/bin/env python3
"""wlan_glue_rows: mutant row tables of the G2L / glue sweeps of tools/test_wlan_wr_mutants.py (E_G2L_ROWS, E_GLUE_ROWS, L25R_GLUE_ROWS, L25A_GLUE_ROWS and the L25 GLUE_ASSIGNS rows; moved out for R29 by T-2631, rows byte for byte).

  tools/wlan_glue_rows --self-test    checks every row is (name, [(old, new)]) with old != new and the names are unique per table
Last line: RESULT: PASS|FAIL wlan_glue_rows ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import sys


# (name, [(old, new)]) applied to the glue text; each must change it and the sweep must FAIL (a compile error is a broken mutant, not a kill)
def _gm(name, old, new):
    return ("e: " + name, [(old, new)])


GLUE_ASSIGNS_L25 = [
    ('sw_g2_hook', 'gb.stack_room = sw_g2_stack_room;'),     # T-eabd: the read-only headroom probe of swg_hr
    ('sw_g2_hook', 'g.b = NULL;'),     # T-eabd: g is static now, gb dies with this frame
    ('sw_g2_stack_room', 'vm_offset_t bottom = thread_get_kernel_stack(current_thread());'),
    ('sw_g2_stack_room', 'vm_offset_t sp = (vm_offset_t)__builtin_frame_address(0);'),
    # T-2543 (reviewed with T-43af / T-d84f L25 wiring, docs/reviews/T-2543-*): the L25 data block, hook state and store-judge locals; none of them is a bus store
    ('sw_g2_hook', 'h->g2x = &g;'),
    ('sw_g2_hook', 'h->g2x = NULL;'),
    ('sw_g2_hook', 'h->l25arm = 0;'),
    ('sw_g2_hook', 'h->l25arm = 0;'),
    ('sw_g2_hook', 'h->l25arm = swl25g_wl_arm(&g, &sw_l25g, &h->l25w, &h->g2plan, h->g2pa, &sw_cred, NULL, h->dpa, h->dlen, SW_G2_ALIAS_LO, SW_G2_ALIAS_HI, sw_l25w_rd, h) == 1;'),  # T-3784: the arm decision is one pure helper (sweet_wlan_l25_glue.h swl25g_wl_arm, host case J4)
    ('sw_g2_hook', 'h->l25on = 0;'),
    ('sw_g2_hook', 'h->l25on = 0;'),
    ('sw_g2_wr', 'rc = sw3_wr(h, SW3_DATA, 0, a, v);'),
    ('sw_l25w_judge', 'cls = swl25w_ok(&h->l25w, sp, off, v, old, &why);'),
    ('sw_l25w_judge', 'const char *why = "";'),
    ('sw_l25w_judge', 'const int sp = space == SW3_HOST ? SWL25W_SP_HOST : space == SW3_DATA ? SWL25W_SP_DATA : SWL25W_SP_PCIE;'),
    ('sw_l25w_judge', 'h->l25cls = cls;'),
    ('sw_l25w_judge', 'h->l25old = old;'),
    ('sw_l25w_judge', 'h->l25sp = sp;'),
    ('sw_l25w_judge', 'uint32_t old = 0;'),
    ('sw_l25w_live', 'h->l25on = 1;'),
    ('sweet_wlan_data_blk', '*len_out = (uint32_t)SWL25_DATA_MIN;'),
    ('sweet_wlan_data_blk', '*pa_out = pa;'),
    ('sweet_wlan_data_blk', '*va_out = va;'),
    ('sweet_wlan_data_blk', 'pa = ptoa_64(pn);'),
    ('sweet_wlan_data_blk', 'pl = pmap_find_phys(kernel_pmap, (addr64_t)(va + sz - PAGE_SIZE));'),
    ('sweet_wlan_data_blk', 'pn = pmap_find_phys(kernel_pmap, (addr64_t)va);'),
    ('sweet_wlan_data_blk', 'vm_offset_t va = 0;'),
    ('sweet_wlan_data_blk', 'vm_size_t sz = round_page(SWL25_DATA_MIN);'),
    ('sweet_wlan_init', 'hw.dlen = 0;'),
    ('sweet_wlan_init', 'hw.dpa = 0;'),
    ('sweet_wlan_init', 'hw.dva = 0;'),
]


# Dropped as equivalent or unreachable (T-4adb, L22c): 'clmload_status payload length 18' and 'country payload length 19' (n rounds up to the same 5 words, the extra byte is a zero), 'BLOB rep above the payload shadow' (the builder never makes rep > 355; the guard is defence in depth); 'BLOB length field 3 bytes' (the length byte 3 is 0: a chunk is at most 1400 B), 'BLOB name 7 bytes' (byte 7 of the name is its NUL), 'BLOB offset below the entry accepted' (off - eo wraps to a count far above rep); 'clmload_status short answer accepted' (a short answer sets st = 0xFFFFFFFF, so `st != 0u` stops it without the resp_len test) and 'soft stop goes on to WLC_UP' (swg_e_call returns first on d4dstop, so WLC_UP is not sent either way); the 3 B read mutant and the ge.up == 0 checks of the host cases keep the behaviour.
# Dropped as equivalent (T-f1e2): 'event re-post buffers round robin of 7' (word 4 of every event re-post is overwritten with the slot-0 table value and kind SH_RBUF, so the slot the builder passes to sw_g2_d4e_ev no longer reaches the list; the "table base" rows cover the value).
# Dropped as equivalent (22b-impl2): PAYW 21 (UB read, now a static assert), escan payload length 85 and the word round-up (the last partial word of every payload is zero), VER 'only the table value masked' (the table value has bit 16 clear).
# W-D4e (rows, concatenated into G2L_MUTANTS: its lint-mutant-needles adapter covers them): sw_g2_d4e / sw_g2_e_pay and their constants; G2L_HARNESS (want(), the d4b 8, 9, 10 lists) must FAIL on each
# Dropped (T-1142): the 63 / 65 event re-posts and the 47 / 49 / 64-under-CLM / 48-without-CLM cap rows. SW_G2_E_NREP and SW_G2_E_NREP_CLM are _Static_assert-bound to the header's SWG_E_MAX / SWG_E_MAX_CLM (T-4459), so a changed constant does not compile (the compiler kills it), and the list no longer
# depends on them (the event re-posts are 11 repeatable entries, not nrep x 12); the cap itself is enforced at match time and killed by the T8EDE rows 'E_ITEM: no g2ev cap' / 'E_WR: cap ...' in the glue sweep.
E_G2L_ROWS = [
    _gm("buffer stride 0x1000", "#define SW_G2_E_BUFSZ     0x2000u", "#define SW_G2_E_BUFSZ     0x1000u"),
    _gm("event post is a response post", "#define SW_G2_E_W0_EV     0x0080000Du", "#define SW_G2_E_W0_EV     0x0080000Bu"),
    _gm("event post without the phase flag", "#define SW_G2_E_W0_EV     0x0080000Du", "#define SW_G2_E_W0_EV     0x0000000Du"),
    _gm("event ids from 0x30", "#define SW_G2_E_EVID      0x20u ", "#define SW_G2_E_EVID      0x30u "),
    _gm("re-post ids from 13", "#define SW_G2_E_RE0       12u ", "#define SW_G2_E_RE0       13u "),
    _gm("transaction ids from 0x104", "#define SW_G2_E_TRANS     0x103u ", "#define SW_G2_E_TRANS     0x104u "),
    _gm("GET_VAR is 261", "#define SW_G2_E_GET       262u ", "#define SW_G2_E_GET       261u "),
    _gm("SET_VAR is 262", "#define SW_G2_E_SET       263u ", "#define SW_G2_E_SET       262u "),
    _gm("WLC_UP is 3", "#define SW_G2_E_UP        2u ", "#define SW_G2_E_UP        3u "),
    _gm("ring-0 index starts at 10", "sh[SW_G2_E_PAYW] = { SW_G2_D4D_CAP_W0 }, ix = 9u, j, kq = 0, escn = 80u;", "sh[SW_G2_E_PAYW] = { SW_G2_D4D_CAP_W0 }, ix = 10u, j, kq = 0, escn = 80u;"),
    _gm("payload shadow starts at 0", "sh[SW_G2_E_PAYW] = { SW_G2_D4D_CAP_W0 }, ix = 9u, j, kq = 0, escn = 80u;", "sh[SW_G2_E_PAYW] = { 0 }, ix = 9u, j, kq = 0, escn = 80u;"),
    _gm("response posts from buffer 0", "	for (j = 1; j < 8u && last >= 0; j++) {\n		sw_g2_d4d_resp(w, 4u + j,", "	for (j = 0; j < 8u && last >= 0; j++) {\n		sw_g2_d4d_resp(w, 4u + j,"),
    _gm("response ids from 6", "sw_g2_d4d_resp(w, 4u + j, rpa + (uint64_t)j * SW_G2_E_BUFSZ, ix);\n		last = sw_g2_d4d_post(h, ix++, w, last);\n	}\n	for (j = 1;", "sw_g2_d4d_resp(w, 5u + j, rpa + (uint64_t)j * SW_G2_E_BUFSZ, ix);\n		last = sw_g2_d4d_post(h, ix++, w, last);\n	}\n	for (j = 1;"),
    _gm("7 response posts", "	for (j = 1; j < 8u && last >= 0; j++) {\n		sw_g2_d4d_resp(w, 4u + j,", "	for (j = 1; j < 7u && last >= 0; j++) {\n		sw_g2_d4d_resp(w, 4u + j,"),
    _gm("event posts from buffer 0", "	for (j = 1; j < 8u && last >= 0; j++) {\n		sw_g2_d4e_ev(w, SW_G2_E_EVID + j,", "	for (j = 0; j < 8u && last >= 0; j++) {\n		sw_g2_d4e_ev(w, SW_G2_E_EVID + j,"),
    _gm("event posts: id off by one", "sw_g2_d4e_ev(w, SW_G2_E_EVID + j, epa", "sw_g2_d4e_ev(w, SW_G2_E_EVID + j + 1u, epa"),
    _gm("event posts: base is the response buffer", "sw_g2_d4e_ev(w, SW_G2_E_EVID + j, epa + (uint64_t)j * SW_G2_E_BUFSZ, ix);", "sw_g2_d4e_ev(w, SW_G2_E_EVID + j, rpa + (uint64_t)j * SW_G2_E_BUFSZ + (epa & 0u), ix);"),      # T-1142: epa stays used (-Werror unused-variable)
    _gm("event word 0 without the epoch", "	w[0] = SW_G2_E_W0_EV | SW_G2_D4D_EP(idx);", "	w[0] = SW_G2_E_W0_EV;"),
    _gm("L22 at level 21", "for (j = 0; h->d4b >= 9 && j < 5u && last >= 0; j++) {", "for (j = 0; h->d4b >= 8 && j < 5u && last >= 0; j++) {"),
    _gm("L22 only from level 10", "for (j = 0; h->d4b >= 9 && j < 5u && last >= 0; j++) {", "for (j = 0; h->d4b >= 10 && j < 5u && last >= 0; j++) {"),
    _gm("L23 at level 22", "	if (h->d4b >= 10 && last >= 0) {", "	if (h->d4b >= 9 && last >= 0) {"),
    _gm("4 requests", "for (j = 0; h->d4b >= 9 && j < 5u && last >= 0; j++) {", "for (j = 0; h->d4b >= 9 && j < 4u && last >= 0; j++) {"),
    _gm("L21..L23 at level 20", "			if (h->d4b >= 8 && !bad) {", "			if (h->d4b >= 7 && !bad) {"),
    _gm("L21..L23 only from level 22", "			if (h->d4b >= 8 && !bad) {", "			if (h->d4b >= 9 && !bad) {"),
    # review l21 finding 5: the glue stride must fit the G1 plan's response / event blocks
    _gm("buffer stride unchecked against the response block", "h->g2plan.b[SWD_RESPBUF].len < 8u * SW_G2_E_BUFSZ || h->g2plan.b[SWD_EVBUF]", "h->g2plan.b[SWD_EVBUF]"),
    _gm("buffer stride unchecked against the event block", "h->g2plan.b[SWD_RESPBUF].len < 8u * SW_G2_E_BUFSZ || h->g2plan.b[SWD_EVBUF].len < 8u * SW_G2_E_BUFSZ", "h->g2plan.b[SWD_RESPBUF].len < 8u * SW_G2_E_BUFSZ"),
    _gm("buffer stride check one buffer short", "h->g2plan.b[SWD_RESPBUF].len < 8u * SW_G2_E_BUFSZ ||", "h->g2plan.b[SWD_RESPBUF].len < 7u * SW_G2_E_BUFSZ ||"),
    _gm("L21..L23 not chained after the L20 doorbell", "				dl = sw_g2_d4e(h, dl);", "				dl = sw_g2_d4e(h, 0);"),
    _gm("scan_ver name", '{ "scan_ver", SW_G2_E_GET, 9u, 6u }', '{ "scan_vex", SW_G2_E_GET, 9u, 6u }'),
    _gm("scan_ver length", '{ "scan_ver", SW_G2_E_GET, 9u, 6u }', '{ "scan_ver", SW_G2_E_GET, 8u, 6u }'),
    _gm("scan_ver answer size", '{ "scan_ver", SW_G2_E_GET, 9u, 6u }', '{ "scan_ver", SW_G2_E_GET, 9u, 4u }'),
    _gm("sup_wpa name", '{ "sup_wpa", SW_G2_E_GET, 8u, 4u }', '{ "sup_wpb", SW_G2_E_GET, 8u, 4u }'),
    _gm("sup_wpa answer size", '{ "sup_wpa", SW_G2_E_GET, 8u, 4u }', '{ "sup_wpa", SW_G2_E_GET, 8u, 6u }'),
    _gm("event_msgs_ext GET is a SET", '{ "event_msgs_ext", SW_G2_E_GET, 15u, 30u }', '{ "event_msgs_ext", SW_G2_E_SET, 15u, 30u }'),
    _gm("event_msgs_ext SET is a GET", '{ "event_msgs_ext", SW_G2_E_SET, 15u, 30u }', '{ "event_msgs_ext", SW_G2_E_GET, 15u, 30u }'),
    _gm("event_msgs_ext length", '{ "event_msgs_ext", SW_G2_E_SET, 15u, 30u }', '{ "event_msgs_ext", SW_G2_E_SET, 15u, 29u }'),
    _gm("WLC_UP has a payload", '{ NULL, SW_G2_E_UP, 0u, 0u }', '{ NULL, SW_G2_E_UP, 0u, 4u }'),
    _gm("event_msgs_ext version 2", "			arg[0] = 1u;            /* eventmsgs_ext: ver 1", "			arg[0] = 2u;            /* eventmsgs_ext: ver 1"),
    _gm("event_msgs_ext len 25", "			arg[2] = 26u;\n		}\n		if (j == 3u) {", "			arg[2] = 25u;\n		}\n		if (j == 3u) {"),
    _gm("event_msgs_ext SET command 1", "			arg[1] = 3u;            /* command EVENTMSGS_SET_MASK", "			arg[1] = 1u;            /* command EVENTMSGS_SET_MASK"),
    _gm("event_msgs_ext SET bit 68", "arg[4u + 69u / 8u] = (uint8_t)(1u << (69u % 8u));", "arg[4u + 69u / 8u] = (uint8_t)(1u << (68u % 8u));"),
    _gm("event_msgs_ext SET byte 9", "arg[4u + 69u / 8u] = (uint8_t)(1u << (69u % 8u));", "arg[4u + 77u / 8u] = (uint8_t)(1u << (69u % 8u));"),
    _gm("event_msgs_ext also on the GET only", "		if (j == 2u || j == 3u) {", "		if (j == 3u) {"),
    _gm("event_msgs_ext SET also on the GET", "		if (j == 3u) {\n			arg[1] = 3u;", "		if (j >= 2u) {\n			arg[1] = 3u;"),
    _gm("request id", "sw_g2_d4d_req(w, rq[j].cmd, SW_G2_E_TRANS + kq, rq[j].nl + rq[j].argn, ipa, ix);", "sw_g2_d4d_req(w, rq[j].cmd, SW_G2_E_TRANS + kq + 1u, rq[j].nl + rq[j].argn, ipa, ix);"),
    _gm("request length", "sw_g2_d4d_req(w, rq[j].cmd, SW_G2_E_TRANS + kq, rq[j].nl + rq[j].argn, ipa, ix);", "sw_g2_d4d_req(w, rq[j].cmd, SW_G2_E_TRANS + kq, rq[j].nl + rq[j].argn + 1u, ipa, ix);"),
    _gm("request payload address", "sw_g2_d4d_req(w, rq[j].cmd, SW_G2_E_TRANS + kq, rq[j].nl + rq[j].argn, ipa, ix);", "sw_g2_d4d_req(w, rq[j].cmd, SW_G2_E_TRANS + kq, rq[j].nl + rq[j].argn, rpa, ix);"),
    _gm("re-post table base: one buffer up", "h->g2l[last - 7].v = (uint32_t)rpa; ", "h->g2l[last - 7].v = (uint32_t)rpa + SW_G2_E_BUFSZ; "),
    _gm("re-post table base: slot 7 (the range ends at slot 7, slot 0 refused)", "h->g2l[last - 7].v = (uint32_t)rpa; ", "h->g2l[last - 7].v = (uint32_t)rpa + 7u * SW_G2_E_BUFSZ; "),
    _gm("re-post entry: stride not set", "h->g2l[last - 7].stride = SW_G2_E_BUFSZ;", "h->g2l[last - 7].stride = 0u;"),
    _gm("re-post entry: range to 6 strides", "h->g2l[last - 7].vrng = 7u * SW_G2_E_BUFSZ;", "h->g2l[last - 7].vrng = 6u * SW_G2_E_BUFSZ;"),
    _gm("re-post entry: range to 8 strides", "h->g2l[last - 7].vrng = 7u * SW_G2_E_BUFSZ;", "h->g2l[last - 7].vrng = 8u * SW_G2_E_BUFSZ;"),
    _gm("re-post entry not marked SH_RBUF", "h->g2l[last - 7].kind = SW_G2K_SH_RBUF;", "h->g2l[last - 7].kind = SW_G2K_SH_ITEM;"),
    # T-1142: T-f1e2/T-4a7f moved the event re-posts from sw_g2_d4e (epa table base, nrep) to sw_g2_e_rx: word 4 of each E_ITEM entry carries stride / vrng (the table value is computed at match time, so a table-base row would be equivalent)
    _gm("event word 4: stride not set", "\t\t\th->g2l[h->g2n - 1u].stride = SW_G2_E_BUFSZ;", "\t\t\th->g2l[h->g2n - 1u].stride = 0u;"),
    _gm("event word 4: range to 6 strides", "\t\t\th->g2l[h->g2n - 1u].vrng = 7u * SW_G2_E_BUFSZ;", "\t\t\th->g2l[h->g2n - 1u].vrng = 6u * SW_G2_E_BUFSZ;"),
    _gm("event word 4: range to 8 strides", "\t\t\th->g2l[h->g2n - 1u].vrng = 7u * SW_G2_E_BUFSZ;", "\t\t\th->g2l[h->g2n - 1u].vrng = 8u * SW_G2_E_BUFSZ;"),
    _gm("event word 4 stays exact (block dropped)", "if (k == 4u) {      /* the buffer address: ANY", "if (0) {      /* the buffer address: ANY"),
    # T-1142: 'event re-post ids from 0x27' dropped from the G2L rows: the id of an event re-post is computed at match time (sw_g2_e_evexp), which only the glue sweep runs; T8EDE 'E_ITEM/E_WR: id of counter g2ev + 1' kills it there.
    _gm("re-post id", "sw_g2_d4d_resp(w, SW_G2_E_RE0 + kq, rpa", "sw_g2_d4d_resp(w, SW_G2_E_RE0 + kq + 1u, rpa"),
    _gm("no re-post of the last response buffer", "\t\tsw_g2_d4d_resp(w, SW_G2_E_RE0 + kq, rpa + (uint64_t)j * SW_G2_E_BUFSZ, ix);      /* completion c = 2 + j: the id is ours, the buffer is the one the dongle used (any of the 8, kind SH_RBUF) */\n\t\tlast = sw_g2_d4d_post(h, ix++, w, last);\n\t\tif (last >= 0) {", "\t\tsw_g2_d4d_resp(w, SW_G2_E_RE0 + kq, rpa + (uint64_t)j * SW_G2_E_BUFSZ, ix);      /* completion c = 2 + j: the id is ours, the buffer is the one the dongle used (any of the 8, kind SH_RBUF) */\n\t\tlast = j == 4u ? last : sw_g2_d4d_post(h, ix++, w, last);\n\t\tif (last >= 0) {"),
    _gm("escan version 3", "		arg[0] = 2u;                    /* escan version", "		arg[0] = 3u;                    /* escan version"),
    _gm("escan action 3", "		arg[4] = 1u;                    /* action START", "		arg[4] = 3u;                    /* action START"),
    _gm("escan sync low byte", "		arg[6] = 0x34u;", "		arg[6] = 0x12u;"),
    _gm("escan sync high byte", "		arg[7] = 0x12u;", "		arg[7] = 0x34u;"),
    _gm("escan params version 3", "		arg[8] = 2u;                    /* params version", "		arg[8] = 3u;                    /* params version"),
    _gm("escan params length 80", "		arg[10] = 72u;", "		arg[10] = 80u;"),
    _gm("escan bssid not broadcast", "		memset(arg + 48, 0xFF, 6);      /* bssid ff x6 */", "		memset(arg + 48, 0xFF, 5);      /* bssid ff x6 */"),
    _gm("escan bss_type 1", "		arg[54] = 2u;                   /* bss_type", "		arg[54] = 1u;                   /* bss_type"),
    _gm("escan active", "		arg[56] = 1u;                   /* scan_type", "		arg[56] = 0u;                   /* scan_type"),
    _gm("escan times", "		memset(arg + 60, 0xFF, 16);     /* nprobes", "		memset(arg + 60, 0xFF, 12);     /* nprobes"),
    _gm("escan name", 'sw_g2_e_pay(h, sh, "escan", arg, 6u + escn,', 'sw_g2_e_pay(h, sh, "escam", arg, 6u + escn,'),
    _gm("escan version word 1 exact", "(1u << 1) | (1u << 3), last);", "(1u << 3), last);"),
    _gm("escan version word 3 exact", "(1u << 1) | (1u << 3), last);", "(1u << 1), last);"),
    _gm("escan request id", "sw_g2_d4d_req(w, SW_G2_E_SET, SW_G2_E_TRANS + kq, 6u + escn, ipa, ix);", "sw_g2_d4d_req(w, SW_G2_E_SET, SW_G2_E_TRANS + kq - 1u, 6u + escn, ipa, ix);"),
    _gm("escan request length", "sw_g2_d4d_req(w, SW_G2_E_SET, SW_G2_E_TRANS + kq, 6u + escn, ipa, ix);", "sw_g2_d4d_req(w, SW_G2_E_SET, SW_G2_E_TRANS + kq, 6u + escn - 1u, ipa, ix);"),
    # T-0fdd: the 7-channel escan of sweetwlan=24 (d4b >= 11): channel_num, the seven chanspecs, the 96 B argument, the level condition; the shim side has the same rows below (L23: ...)
    _gm("escan 7ch: channel_num 6", "\t\t\targ[76] = 7u;", "\t\t\targ[76] = 6u;"),
    _gm("escan 7ch: channel_num 0 (all channels)", "\t\t\targ[76] = 7u;", "\t\t\targ[76] = 0u;"),
    _gm("escan 7ch: list at arg + 78", "arg[80u + 2u * j] = (uint8_t)(cs[j] & 0xFFu);", "arg[78u + 2u * j] = (uint8_t)(cs[j] & 0xFFu);"),
    _gm("escan 7ch: chanspec high byte lost", "arg[81u + 2u * j] = (uint8_t)(cs[j] >> 8);", "arg[81u + 2u * j] = 0u;"),
    _gm("escan 7ch: chanspec 1 is 0x1002", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1002u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _gm("escan 7ch: chanspec 2 is 0x1007", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1007u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _gm("escan 7ch: chanspec 3 is 0x100c", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Cu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _gm("escan 7ch: chanspec 4 is 5 GHz channel 37", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD025u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _gm("escan 7ch: chanspec 5 is channel 41", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD029u, 0xD02Cu, 0xD030u }"),
    _gm("escan 7ch: chanspec 6 is channel 45", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Du, 0xD030u }"),
    _gm("escan 7ch: chanspec 7 is channel 49", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD031u }"),
    _gm("escan 7ch: 5 GHz band bits lost", "0xD024u, 0xD028u,", "0x1024u, 0xD028u,"),
    _gm("escan 7ch: only 6 chanspecs written", "for (j = 0; j < 7u; j++) {\n\t\t\t\targ[80u", "for (j = 0; j < 6u; j++) {\n\t\t\t\targ[80u"),
    _gm("escan 7ch: payload stays 80 B", "\t\t\tescn = 96u;", "\t\t\tescn = 80u;"),
    _gm("escan 7ch: payload 94 B", "\t\t\tescn = 96u;", "\t\t\tescn = 94u;"),
    _gm("escan 7ch: also below level 24", "if (h->d4b >= 11) {             /* T-0fdd", "if (h->d4b >= 10) {             /* T-0fdd"),
    _gm("escan 7ch: never", "if (h->d4b >= 11) {             /* T-0fdd", "if (0) {             /* T-0fdd"),
    _gm("escan is a GET", "sw_g2_d4d_req(w, SW_G2_E_SET, SW_G2_E_TRANS + kq,", "sw_g2_d4d_req(w, SW_G2_E_GET, SW_G2_E_TRANS + kq,"),
    _gm("payload kind VER dropped", "			h->g2l[last].kind = SW_G2K_VER;\n", ""),
    _gm("payload kind VER for every word", "		if ((verw >> i) & 1u) {", "		if (((verw >> i) | 1u) & 1u) {"),
    _gm("payload equal words not skipped", "		if (v == sh[i]) {\n			continue;\n		}\n", ""),
    _gm("payload shadow not updated", "		sh[i] = v;\n	}\n	return last;", "	}\n	return last;"),
    _gm("payload old is 0", "v, 0xFFFFFFFFu, sh[i], 0xFFFFFFFFu, 1, last);", "v, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, last);"),
    _gm("payload old not compared", "v, 0xFFFFFFFFu, sh[i], 0xFFFFFFFFu, 1, last);", "v, 0xFFFFFFFFu, sh[i], 0u, 1, last);"),
    _gm("payload word offset", "h->g2plan.b[SWD_IOCTBUF].off + 4u * i, v,", "h->g2plan.b[SWD_IOCTBUF].off + 4u * i + 4u, v,"),
    _gm("payload not once", "v, 0xFFFFFFFFu, sh[i], 0xFFFFFFFFu, 1, last);", "v, 0xFFFFFFFFu, sh[i], 0xFFFFFFFFu, 0, last);"),
    _gm("payload name copy", "		b[nl] = (uint8_t)name[nl];\n		nl++;", "		b[nl] = (uint8_t)name[nl] ^ (nl == 7u);\n		nl++;"),
    _gm("payload arg after the NUL", "b[nl + 1u + i] = arg[i];", "b[nl + i] = arg[i];"),
    _gm("d4d returns 0 as the last entry", "	return last < 0 ? -1 : last;\n}", "	return last < 0 ? -1 : 0;\n}"),
    # L22c (T-4adb, sweetwlan=24): the glue list's CLM entries, one per field of sw_g2_e_clm / sw_g2_e_rq / sw_g2_clm_set and the constants; G2L_HARNESS (want(), the d4b 11 list) must FAIL on each
    _gm("L22c: CLM list from level 10", "const int clm = h->d4b >= 11 && sw_g2_clm_set(h);", "const int clm = h->d4b >= 10 && sw_g2_clm_set(h);"),
    _gm("L22c: CLM list without a checked blob", "const int clm = h->d4b >= 11 && sw_g2_clm_set(h);", "const int clm = h->d4b >= 11;"),
    _gm("L22c: CLM entries before event_msgs_ext SET", "if (j == 4u && clm) {", "if (j == 3u && clm) {"),
    _gm("L22c: chunk count rounds down", "const uint32_t nch = (h->g2clmlen + SW_G2_CLMCHUNK - 1u) / SW_G2_CLMCHUNK;", "const uint32_t nch = h->g2clmlen / SW_G2_CLMCHUNK;"),
    _gm("L22c: BLOB entry kind", "h->g2l[last].kind = SW_G2K_BLOB;", "h->g2l[last].kind = SW_G2K_SH_ITEM;"),
    _gm("L22c: BLOB entry chunk number", "h->g2l[last].arg = c;", "h->g2l[last].arg = c + 1u;"),
    _gm("L22c: BLOB entry one word too many", "h->g2l[last].rep = sw_g2_clm_words(h, c);", "h->g2l[last].rep = sw_g2_clm_words(h, c) + 1u;"),
    _gm("L22c: BLOB entry one word short", "h->g2l[last].rep = sw_g2_clm_words(h, c);", "h->g2l[last].rep = sw_g2_clm_words(h, c) - 1u;"),
    _gm("L22c: chunk request length", "sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 12u + sw_g2_clm_len(h, c), last)", "sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 12u + sw_g2_clm_len(h, c) + 1u, last)"),
    _gm("L22c: chunk request is a GET", "sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 12u + sw_g2_clm_len(h, c), last)", "sw_g2_e_rq(h, ix, kq, SW_G2_E_GET, 8u + 12u + sw_g2_clm_len(h, c), last)"),
    _gm("L22c: request number not counted", "	(*kq)++;\n	return last;", "	return last;"),
    _gm("L22c: request item id", "sw_g2_d4d_req(w, cmd, SW_G2_E_TRANS + *kq, n, ipa, *ix);", "sw_g2_d4d_req(w, cmd, SW_G2_E_TRANS + *kq + 1u, n, ipa, *ix);"),
    _gm("L22c: request re-post id", "sw_g2_d4d_resp(w, SW_G2_E_RE0 + *kq, rpa, *ix);", "sw_g2_d4d_resp(w, SW_G2_E_RE0 + *kq + 1u, rpa, *ix);"),
    _gm("L22c: request re-post entry not SH_RBUF", "h->g2l[last - 7].kind = SW_G2K_SH_RBUF;\n	}\n	(*kq)++;", "h->g2l[last - 7].kind = SW_G2K_SH_ITEM;\n	}\n	(*kq)++;"),
    _gm("L22c: request re-post range", "h->g2l[last - 7].vrng = 7u * SW_G2_E_BUFSZ;\n		h->g2l[last - 7].kind = SW_G2K_SH_RBUF;\n	}\n	(*kq)++;", "h->g2l[last - 7].vrng = 6u * SW_G2_E_BUFSZ;\n		h->g2l[last - 7].kind = SW_G2K_SH_RBUF;\n	}\n	(*kq)++;"),
    _gm("L22c: shadow before the first chunk not kept", "\tfor (i = 0; i < SW_G2_E_PAYW; i++) { h->g2clmpre[i] = sh[i]; }", "\tfor (i = 0; i < SW_G2_E_PAYW; i++) { }"),
    _gm("L22c: shadow after the chunks is the last chunk's minus one", "sh[i] = sw_g2_clm_old(h, nch, i);", "sh[i] = sw_g2_clm_old(h, nch - 1u, i);"),
    _gm("L22c: clmload_status name", 'sw_g2_e_pay(h, sh, "clmload_status", NULL,', 'sw_g2_e_pay(h, sh, "clmload_statuz", NULL,'),
    _gm("L22c: clmload_status request is a SET", "sw_g2_e_rq(h, ix, kq, SW_G2_E_GET, 15u + 4u, last)", "sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 15u + 4u, last)"),
    _gm("L22c: clmload_status request length", "sw_g2_e_rq(h, ix, kq, SW_G2_E_GET, 15u + 4u, last)", "sw_g2_e_rq(h, ix, kq, SW_G2_E_GET, 15u + 3u, last)"),
    _gm("L22c: country name", 'sw_g2_e_pay(h, sh, "country", xz,', 'sw_g2_e_pay(h, sh, "countrz", xz,'),
    _gm("L22c: country abbrev", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }", "{ 'X', 'Y', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }"),
    _gm("L22c: country rev", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }", "{ 'X', 'Z', 0, 0, 0, 0, 1, 0, 'X', 'Z', 0, 0 }"),
    _gm("L22c: country ccode", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Y', 0, 0 }"),
    _gm("L22c: country request is a GET", "return last < 0 ? -1 : sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 12u, last);", "return last < 0 ? -1 : sw_g2_e_rq(h, ix, kq, SW_G2_E_GET, 8u + 12u, last);"),
    _gm("L22c: country request length", "return last < 0 ? -1 : sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 12u, last);", "return last < 0 ? -1 : sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 13u, last);"),
    _gm("L22c: blob accepted past 12 chunks", "sw_clm.len > SW_G2_CLMMAX * SW_G2_CLMCHUNK", "sw_clm.len > SW_G2_CLMMAX * SW_G2_CLMCHUNK + SW_G2_CLMCHUNK"),
    _gm("L22c: blob CRC not compared", "if (swf_crc32(sw_clm.p, sw_clm.len) != sw_clm.crc) {", "if (0) {"),
    _gm("L22c: blob CRC compared the wrong way", "if (swf_crc32(sw_clm.p, sw_clm.len) != sw_clm.crc) {", "if (swf_crc32(sw_clm.p, sw_clm.len) == sw_clm.crc) {"),
    _gm("L22c: blob length 0 accepted", "sw_clm.p == NULL || sw_clm.len == 0u ||", "sw_clm.p == NULL ||"),
    _gm("L22c: blob not set on success", "	h->g2clm = sw_clm.p;\n	h->g2clmlen = sw_clm.len;\n	return 1;", "	h->g2clm = NULL;\n	h->g2clmlen = sw_clm.len;\n	return 1;"),
]

# W-D4e (rows, concatenated into GLUE_MUTANTS: its adapter covers them): the escan version word (kind SW_G2K_VER) in sw3_g2_ok; the VER block at the end of GLUE_HARNESS must FAIL on each
E_GLUE_ROWS = [
    _gm("VER: bit 16 compared", "(vv & ~SW_G2_VERBIT) == (ev & ~SW_G2_VERBIT) && (old & e->omask) == eold", "vv == ev && (old & e->omask) == eold"),
    _gm("VER: bit 17 free as well", "(vv & ~SW_G2_VERBIT) == (ev & ~SW_G2_VERBIT) && (old & e->omask) == eold", "(vv & ~(SW_G2_VERBIT | 0x20000u)) == (ev & ~(SW_G2_VERBIT | 0x20000u)) && (old & e->omask) == eold"),
    _gm("VER: old word not compared", "(vv & ~SW_G2_VERBIT) == (ev & ~SW_G2_VERBIT) && (old & e->omask) == eold", "(vv & ~SW_G2_VERBIT) == (ev & ~SW_G2_VERBIT)"),
    _gm("VER: the bit is bit 17", "#define SW_G2_VERBIT    0x00010000u", "#define SW_G2_VERBIT    0x00020000u"),
    _gm("VER: falls through to the exact compare", "		if (e->kind == SW_G2K_VER) {      /* W-D4e:", "		if (0) {      /* W-D4e:"),
    _gm("SH_RBUF: range to 8 strides", "vv - ev <= e->vrng && (vv - ev) % e->stride", "vv - ev <= e->vrng + e->stride && (vv - ev) % e->stride"),
    _gm("SH_RBUF: range to 6 strides", "vv - ev <= e->vrng && (vv - ev) % e->stride", "vv - ev < e->vrng && (vv - ev) % e->stride"),
    _gm("SH_RBUF: misaligned address accepted", " && (vv - ev) % e->stride == 0u", ""),
    _gm("SH_RBUF: any address accepted", "e->stride != 0u && vv - ev <= e->vrng && (vv - ev) % e->stride == 0u && (old & e->omask) == eold", "(old & e->omask) == eold"),
    _gm("SH_RBUF: old word not compared", "(vv - ev) % e->stride == 0u && (old & e->omask) == eold", "(vv - ev) % e->stride == 0u"),
    _gm("SH_RBUF: falls through to the exact compare", "		if (e->kind == SW_G2K_SH_RBUF || (e->kind == SW_G2K_E_ITEM && e->stride != 0u)) {        /* T-af77", "		if (0) {        /* T-af77"),
    _gm("SH_RBUF: not offset-shifted by the DS rounds", "	case SW_G2K_SH_ITEM0:\n	case SW_G2K_SH_RBUF:\n", "	case SW_G2K_SH_ITEM0:\n"),
    # L22c (T-4adb): the BLOB entry's expected and old words in sw_g2_exp / sw_g2_clm_*: the clm replay stream of the glue sweep must be refused at the changed store
    _gm("BLOB: flag base 0x2000", "const uint32_t flag = 0x1000u | (c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);", "const uint32_t flag = 0x2000u | (c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);"),
    _gm("BLOB: DL_BEGIN missing on the first chunk", "(c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);", "(c == 0u ? 0u : 0u) | (c == last ? 0x4u : 0u);"),
    _gm("BLOB: DL_BEGIN on the second chunk", "(c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);", "(c == 1u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);"),
    _gm("BLOB: DL_END missing on the last chunk", "(c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);", "(c == 0u ? 0x2u : 0u) | (c == last && 0u ? 0x4u : 0u);"),
    _gm("BLOB: DL_END on the first chunk", "(c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);", "(c == 0u ? 0x2u : 0u) | (c == 0u && last + 1u ? 0x4u : 0u);"),
    _gm("BLOB: DL_END on the next to last chunk", "(c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);", "(c == 0u ? 0x2u : 0u) | (c + 1u == last ? 0x4u : 0u);"),
    _gm("BLOB: flag low byte", "b = flag & 0xFFu;", "b = (flag & 0xFFu) ^ 1u;"),
    _gm("BLOB: flag high byte", "b = flag >> 8;", "b = (flag >> 8) ^ 1u;"),
    _gm("BLOB: dload_type 1", "b = 2u;         /* DL_TYPE_CLM;", "b = 1u;         /* DL_TYPE_CLM;"),
    _gm("BLOB: length field big endian", "b = (len >> (8u * (q - 12u))) & 0xFFu;", "b = (len >> (8u * (15u - q))) & 0xFFu;"),
    _gm("BLOB: crc not zero", "} else if (q >= 12u && q < 16u) {", "} else if (q == 16u) {\n			b = 1u;\n		} else if (q >= 12u && q < 16u) {"),
    _gm("BLOB: data one byte past the length", "} else if (q >= 20u && q - 20u < len) {", "} else if (q >= 20u && q - 20u <= len) {"),
    _gm("BLOB: data one byte early", "b = h->g2clm[c * SW_G2_CLMCHUNK + (q - 20u)];", "b = h->g2clm[c * SW_G2_CLMCHUNK + (q - 19u)];"),
    _gm("BLOB: data of the first chunk always", "b = h->g2clm[c * SW_G2_CLMCHUNK + (q - 20u)];", "b = h->g2clm[q - 20u];"),
    _gm("BLOB: words without the round-up", "return (8u + 12u + sw_g2_clm_len(h, c) + 3u) / 4u;", "return (8u + 12u + sw_g2_clm_len(h, c)) / 4u;"),
    _gm("BLOB: words one more", "return (8u + 12u + sw_g2_clm_len(h, c) + 3u) / 4u;", "return (8u + 12u + sw_g2_clm_len(h, c) + 4u) / 4u;"),
    _gm("BLOB: last chunk as long as the others", "return h->g2clmlen - c * SW_G2_CLMCHUNK < SW_G2_CLMCHUNK ? h->g2clmlen - c * SW_G2_CLMCHUNK : SW_G2_CLMCHUNK;", "return h != NULL && c != ~0u ? SW_G2_CLMCHUNK : 0u;"),
    _gm("BLOB: old word of a fresh chunk is 0", "	return h->g2clmpre[i];", "	return 0;"),
    _gm("BLOB: old word ignores the shorter earlier chunk", "if (i < sw_g2_clm_words(h, c)) {\n			return sw_g2_clm_word(h, c, i);", "if (1) {\n			return sw_g2_clm_word(h, c, i);"),
    # no "BLOB: misaligned offset accepted" mutant: sw3_ptr (patch 0129) returns NULL for off & 3, and sw3_g2_ok refuses on p == NULL before sw_g2_exp is reached, so the alignment test in the BLOB case is unreachable (equivalent mutant); the glue harness probe stays as the behaviour check
    _gm("BLOB: one word past the entry accepted", "|| (off - eo) / 4u >= e->rep ||", "|| (off - eo) / 4u > e->rep ||"),
    _gm("BLOB: no blob accepted", "if (h->g2clm == NULL || off < eo", "if (off < eo"),
    _gm("BLOB: doorbell of the request 11 entries on", "i = (e - h->g2l) + 12u;         /*", "i = (e - h->g2l) + 11u;         /*"),
    _gm("BLOB: doorbell of the request 13 entries on", "i = (e - h->g2l) + 12u;         /*", "i = (e - h->g2l) + 13u;         /*"),
    _gm("BLOB: stores go on after the doorbell", "if (i >= h->g2n || h->g2l[i].done) {", "if (i >= h->g2n) {"),
    _gm("BLOB: equal word stored", "if (*ev == *eold) {     /* the shim", "if (0) {     /* the shim"),
    _gm("BLOB: expected word of the next chunk", "*ev = sw_g2_clm_word(h, e->arg, i);", "*ev = sw_g2_clm_word(h, e->arg + 1u, i);"),
    _gm("BLOB: old word is the table old", "*eold = sw_g2_clm_old(h, e->arg, i);", "*eold = e->old;"),
]


# T-98d9 (L25 wiring, review T-5173 r2 / T-43af): the judge-alone route of sw3_g2_ok. The "L25 route" block at the start of the review-F4 part of GLUE_HARNESS (tools/wlan_glue_harness_c.py) must FAIL on each
L25R_GLUE_ROWS = [
    _gm("L25 route: judge refusal falls through to the wide entries", "return sw_l25w_judge(h, space, off, v);", "(void)sw_l25w_judge(h, space, off, v);"),
    _gm("L25 route: latch cleared at each call", "if (!h->l25on && h->l25arm && h->g2x != NULL && h->g2x->elv >= 25u) {", "h->l25on = 0; if (!h->l25on && h->l25arm && h->g2x != NULL && h->g2x->elv >= 25u) {"),
    _gm("L25 route: live only from level 26", "h->g2x->elv >= 25u) {", "h->g2x->elv >= 26u) {"),
    _gm("L25 route: live without the armed flag", "if (!h->l25on && h->l25arm && h->g2x != NULL", "if (!h->l25on && h->g2x != NULL"),
    _gm("L25 route: HOST stores not routed", "(space == SW3_HOST || space == SW3_DATA || (space == SWW_BAR0", "(space == SW3_DATA || (space == SWW_BAR0"),
    _gm("L25 route: doorbell stores not routed", "(space == SWW_BAR0 && win == SWW_WIN_PCIE && off == SWL25W_DB0_OFF)", "0"),
    _gm("L25 route: every BAR0 store routed (the stop path 0xC34 := 0 too)", "(space == SW3_HOST || space == SW3_DATA || (space == SWW_BAR0 && win == SWW_WIN_PCIE && off == SWL25W_DB0_OFF))", "(space == SW3_HOST || space == SW3_DATA || space == SWW_BAR0)"),
]

# T-3784 (review T-98d9 r1 F2): the accept side of the same route. The "L25 accept" block (tools/wlan_glue_harness_c.py) must FAIL on each
L25A_GLUE_ROWS = [
    _gm("L25 accept: no return, falls into the g2l[] bookkeeping", "\t\t}\n\t\treturn 0;\n\t}\n\tif (g2i >= 0) {\n\t\th->g2l[g2i].done = 1;", "\t\t}\n\t}\n\tif (g2i >= 0) {\n\t\th->g2l[g2i].done = 1;"),
    _gm("L25 accept: swl25w_done skipped", "swl25w_done(&h->l25w, h->l25sp, off, v, h->l25old, h->l25cls);", ";"),
    _gm("L25 accept: done with the wrong class", "swl25w_done(&h->l25w, h->l25sp, off, v, h->l25old, h->l25cls);", "swl25w_done(&h->l25w, h->l25sp, off, v, h->l25old, SWL25W_L6);"),
    _gm("L25 accept: flow-open scan never asked for", "if (h->l25cls == SWL25W_L8 && off == h->l25w.d2h_r) {", "if (0) {"),
    _gm("L25 accept: flow-open scan after every read-index store", "if (h->l25cls == SWL25W_L8 && off == h->l25w.d2h_r) {", "if (h->l25cls == SWL25W_L8) {"),
    _gm("L25 accept: flow-open scan with the wrong indices", "swl25g_wl_flow(&h->l25w, h->l25old, v);", "swl25g_wl_flow(&h->l25w, v, h->l25old);"),
    _gm("L25 accept: branch never taken (class 9 needed)", "if (g2i >= SW_G2_L25) {", "if (g2i >= SW_G2_L25 + 9) {"),
]


# W-JOIN L24 lineage (T-9c82, T-c3ac): header mutants of the L24 sup_wpa skip and the first-doorbell probe/spin; appended to E_G2_ROWS in the test (row order does not matter), moved out for R29 byte for byte
def _em(name, old, new):
    return ("E: " + name, [(old, new)])


E_G2_L24_ROWS = [
    _em("L24: any H1 status is skipped (T-9c82)", "		if (i == 0u && cm.status == SWG_J_UNSUPP) {", "		if (i == 0u) {"),
    _em("L24: -23 skip applies to every command (T-9c82)", "		if (i == 0u && cm.status == SWG_J_UNSUPP) {", "		if (cm.status == SWG_J_UNSUPP) {"),
    _em("L24: -23 is a stop again (T-9c82)", "		if (i == 0u && cm.status == SWG_J_UNSUPP) {", "		if (0) {"),
    _em("L24: -23 skip says no gap (T-9c82)", "			swl_gap(\"H1\", \"sup_wpa iovar not present (status -23), skipped\", \"medium\");\n", ""),
    _em("L24: -23 skip stops (T-9c82)", "			printf(\"SWEET WLAN: L24 H1 sup_wpa not present, continuing\\n\");\n			continue;", "			printf(\"SWEET WLAN: L24 H1 sup_wpa not present, continuing\\n\");\n			x->d4dstop = 1u;\n			return 0;"),
    _em("L24: no probe before the first doorbell (T-c3ac)", "		swg_l24_probe(x, \"pre\");", "		(void)0;"),
    _em("L24: no probe after the first doorbell (T-c3ac)", "			swg_l24_probe(x, \"post\");", "			(void)0;"),
    _em("L24: every doorbell is probed (T-c3ac)", "			x->l24db = 1u;", "			x->l24db = 0u;"),
    _em("L24: probe pair swapped, post before the doorbell (T-c3ac)", "		swg_l24_probe(x, \"pre\");", "		swg_l24_probe(x, \"post\");"),
    _em("L24: probe reads cfg 0x108, not the AER status (T-c3ac)", "{ 0x104u, \"AER UESta\" }", "{ 0x108u, \"AER UESta\" }"),
    _em("L24: probe reads cfg 0xB8, not LNKSTA (T-c3ac)", "{ 0xBCu, \"LNKCTL/LNKSTA\" }", "{ 0xB8u, \"LNKCTL/LNKSTA\" }"),
    _em("L24: set AER status says no gap (T-c3ac)", "			swl_gap(\"l24pr\", \"L24 probe: AER uncorrectable status is set\", \"high\");\n", ""),
    _em("L24: AER status not judged (T-c3ac)", "} else if (swg_l24_probe_regs[i].off == 0x104u && v != 0u) {", "} else if (0) {"),
    _em("L24: failed probe read says no gap (T-c3ac)", "			swl_gap(\"l24pr\", \"L24 probe: an endpoint config read failed\", \"high\");\n", ""),
    _em("L24: failed probe read not told (T-c3ac)", "READ FAILED\\n\", tag, swg_l24_probe_regs[i].off, swg_l24_probe_regs[i].nm);", "READ FAILED\\n\", tag, swg_l24_probe_regs[i].off, swg_l24_probe_regs[i].nm); if (0)"),
    _em("L24: all-ones probe word says no gap (T-c3ac)", "			swl_gap(\"l24pr\", \"L24 probe: endpoint config reads all ones\", \"high\");\n", ""),
    _em("L24: probe register value not printed (T-c3ac)", "ep cfg %#x (%s) = %#x\\n\", tag, swg_l24_probe_regs[i].off, swg_l24_probe_regs[i].nm, v);", "ep cfg %#x (%s) = %#x\\n\", tag, swg_l24_probe_regs[i].off, swg_l24_probe_regs[i].nm, 0u);"),
    _em("L24: spin never armed (T-c3ac)", "			x->l24spin = 1u;            /* T-c3ac H2", "			x->l24spin = 0u;            /* T-c3ac H2"),
    _em("L24: spin flag leaks to a later wait (T-c3ac)", "	x->l24spin = 0u;        /* T-c3ac: a first doorbell answered", "	(void)0;        /* T-c3ac: a first doorbell answered"),
    _em("L24: spin flag not cleared once used (T-c3ac)", "				x->l24spin = 0u;\n				ex = swg_l24_spin(x);", "				ex = swg_l24_spin(x);"),
    _em("L24: spin runs 99 ms (T-c3ac)", "	for (ms = 1u; ms <= SWG_POLL_MS; ms++) {\n		x->b->delay_ms(x->b->ctx, 1u);", "	for (ms = 1u; ms < SWG_POLL_MS; ms++) {\n		x->b->delay_ms(x->b->ctx, 1u);"),
    _em("L24: spin steps are 2 ms (T-c3ac)", "	for (ms = 1u; ms <= SWG_POLL_MS; ms++) {\n		x->b->delay_ms(x->b->ctx, 1u);", "	for (ms = 1u; ms <= SWG_POLL_MS; ms++) {\n		x->b->delay_ms(x->b->ctx, 2u);"),
    _em("L24: spin prints no line (T-c3ac)", "		if (ms <= 10u || ms % 10u == 0u) {", "		if (0) {"),
    _em("L24: spin prints every line past ms 10 (T-c3ac)", "		if (ms <= 10u || ms % 10u == 0u) {", "		if (1) {"),
    _em("L24: spin does not read the host word (T-c3ac)", "		if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v)) {\n			return swg_stop(x, SWG_E_ADDR);\n		}\n		if (ms <= 10u", "		if (ms <= 10u"),
    _em("L24: spin ignores an all-ones host word (T-c3ac)", "		if (v == 0xFFFFFFFFu) {\n			x->fail = \"host d2h_w[0] reads all ones (dead link) in the L24 spin\";", "		if (0) {\n			x->fail = \"host d2h_w[0] reads all ones (dead link) in the L24 spin\";"),
    _em("L24: spin done said without the spin (T-c3ac)", "	printf(\"SWEET WLAN: L24 spin done: %u ms, timer and host word alive\\n\", SWG_POLL_MS);", "	printf(\"SWEET WLAN: L24 spin done: %u ms, timer and host word alive\\n\", 0u);"),
    _em("L24: spin replaces every delay (T-c3ac)", "			if (x->l24spin != 0u) {\n				x->l24spin = 0u;", "			if (1) {\n				x->l24spin = 0u;"),
]


def self_test():
    bad = 0
    for tn, t in (("E_G2L_ROWS", E_G2L_ROWS), ("E_GLUE_ROWS", E_GLUE_ROWS), ("L25R_GLUE_ROWS", L25R_GLUE_ROWS), ("L25A_GLUE_ROWS", L25A_GLUE_ROWS), ("E_G2_L24_ROWS", E_G2_L24_ROWS)):
        names = [r[0] for r in t]
        if len(set(names)) != len(names):
            print("FAIL %s: duplicate mutant name" % tn)
            bad += 1
        for name, subs in t:
            for old, new in subs:
                if not old or old == new:
                    print("FAIL %s: %s: empty or unchanged substitution" % (tn, name))
                    bad += 1
    n = len(E_G2L_ROWS) + len(E_GLUE_ROWS) + len(L25R_GLUE_ROWS) + len(L25A_GLUE_ROWS) + len(E_G2_L24_ROWS)
    print("RESULT: %s wlan_glue_rows rows=%d assigns=%d" % ("FAIL" if bad else "PASS", n, len(GLUE_ASSIGNS_L25)))
    return 1 if bad else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL wlan_glue_rows is a module of tools/test_wlan_wr_mutants.py: run it with --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
