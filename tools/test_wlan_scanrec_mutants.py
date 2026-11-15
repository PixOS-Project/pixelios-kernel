#!/usr/bin/env python3
"""CR-02 for the scan-record parsing and writing (2026-11-11, W-D3 scan records; review 0293 LOW 2): shim/sweet_wlan_radio.h (swr_chflags, swr_ie, the record fields of swr_event, the full-table replace rule, swr_bss_find) and
shim/sweet_wlan_sta.h (swsta_rec, swsta_cd_gap, the Get 103 lookup). shim/wlan_radio_host_test.c, shim/wlan_sta_codec_host_test.c and shim/wlan_a80211_fake_radio_host_test.c must pass on the real headers (null mutant, must SURVIVE) and each mutant
must make one of them FAIL (plain or ASan/UBSan build). A mutant is a private copy of ONE header with ONE exact-string edit (the pattern must occur exactly once, else the runner fails: no silent hole).
No row for an equivalent mutant: `stored == o` in swr_ie (once the block is cut, every later offset is above the cap), `break` vs `o = n` after a bad walk, `ie_len = o` vs `stored` (o is the end of the whole IEs when nothing was cut). The keep-phase read of the record (swg_ke_escan) is covered by tools/wlan_keep_mutants.py ROWS.
Usage: tools/test_wlan_scanrec_mutants.py [--kernel-root DIR] [--out DIR]. No hardware."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import concurrent.futures
import glob
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_wlan_rsne_mutants as base  # noqa: E402  the generated-header helper and the runner plumbing

SHIM = base.SHIM
R = "sweet_wlan_radio.h"
T = "sweet_wlan_sta.h"
HDR_FILES = (R, T)
TESTS = (("wlan_radio_host_test", ["wlan_radio_host_test.c", "wlan_radio_g2_scan.c"]), ("wlan_sta_codec_host_test", ["wlan_sta_codec_host_test.c"]), ("wlan_a80211_fake_radio_host_test", ["wlan_a80211_fake_radio_host_test.c"]))

MUTANTS = [
    # swr_gap1 / swr_chflags
    ("gap1", "gap-every-time", R, "\tif (s->sgap & bit) return;\n", ""),
    ("gap1", "gap-bit-not-set", R, "\ts->sgap |= (uint16_t)bit;\n", ""),
    ("gap1", "gap-bit-narrow", R, "\ts->sgap |= (uint16_t)bit;", "\ts->sgap |= (uint8_t)bit;"),
    ("gap1", "sgap-not-reset-by-replay-scan", R, "s->n = 0; s->cur = 0; s->sgap = 0; s->scans++;", "s->n = 0; s->cur = 0; s->scans++;"),
    ("gap1", "sgap-not-reset-by-tab-reset", R, "t->n = 0; t->cur = 0; t->complete = 0; t->sgap = 0;", "t->n = 0; t->cur = 0; t->complete = 0;"),
    ("flags", "band-mask", R, "band = cs & 0xC000u,", "band = cs & 0x4000u,"),
    ("flags", "bw-mask", R, "bw = cs & 0x3800u;", "bw = cs & 0x3000u;"),
    ("flags", "2g-is-5g", R, "if (band == 0x0000u) f = 0x8u;", "if (band == 0x0000u) f = 0x10u;"),
    ("flags", "5g-is-2g", R, "else if (band == 0xC000u) f = 0x10u;", "else if (band == 0xC000u) f = 0x8u;"),
    ("flags", "2g-no-bit", R, "if (band == 0x0000u) f = 0x8u;", "if (band == 0x0000u) f = 0u;"),
    ("flags", "5g-no-bit", R, "else if (band == 0xC000u) f = 0x10u;", "else if (band == 0xC000u) f = 0u;"),
    ("flags", "band-gap-lost", R, "\telse swr_gap1(s, SWR_SG_BAND, \"channel band is not 2.4/5 GHz, channel flags 0\");\n", "\n"),
    ("flags", "20mhz-is-40", R, "if (bw == 0x1000u) f |= 0x2u;", "if (bw == 0x1000u) f |= 0x4u;"),
    ("flags", "40mhz-is-20", R, "else if (bw == 0x1800u) f |= 0x4u;", "else if (bw == 0x1800u) f |= 0x2u;"),
    ("flags", "40mhz-no-bit", R, "else if (bw == 0x1800u) f |= 0x4u;", "else if (bw == 0x1800u) f |= 0u;"),
    ("flags", "20mhz-no-bit", R, "if (bw == 0x1000u) f |= 0x2u;", "if (bw == 0x1000u) f |= 0u;"),
    ("flags", "80mhz-gets-20-bit", R, "if (bw == 0x2000u) swr_gap1(s, SWR_SG_CHW, \"80 MHz channel flag bit open, band bit only\");", "if (bw == 0x2000u) { f |= 0x2u; swr_gap1(s, SWR_SG_CHW, \"80 MHz channel flag bit open, band bit only\"); }"),
    ("flags", "80mhz-gap-lost", R, "if (bw == 0x2000u) swr_gap1(s, SWR_SG_CHW, \"80 MHz channel flag bit open, band bit only\");", "if (bw == 0x2000u) (void)0;"),
    ("flags", "80mhz-uses-width-text", R, "if (bw == 0x2000u) swr_gap1(s, SWR_SG_CHW,", "if (0) swr_gap1(s, SWR_SG_CHW,"),
    ("flags", "width-gap-lost", R, "else swr_gap1(s, SWR_SG_CHW2, \"channel width flag bit open (not 20/40 MHz), band bit only\");", "else (void)0;"),
    ("flags", "width-gap-shares-80-bit", R, "else swr_gap1(s, SWR_SG_CHW2,", "else swr_gap1(s, SWR_SG_CHW,"),
    ("flags", "width-gap-for-unknown-band", R, "else if (band == 0x0000u || band == 0xC000u) {", "else {"),
    # swr_ie
    ("ie", "ie-header-length-test", R, "if (n - o < 2u || (uint32_t)ie[o + 1] > n - o - 2u) { bad = 1; break; }", "if (n - o < 1u || (uint32_t)ie[o + 1] > n - o - 2u) { bad = 1; break; }"),
    ("ie", "ie-length-test-off-by-one", R, "(uint32_t)ie[o + 1] > n - o - 2u) { bad = 1; break; }", "(uint32_t)ie[o + 1] >= n - o - 2u) { bad = 1; break; }"),
    ("ie", "ie-length-test-dropped", R, "if (n - o < 2u || (uint32_t)ie[o + 1] > n - o - 2u) { bad = 1; break; }", "if (n - o < 2u) { bad = 1; break; }"),
    ("ie", "cap-off-by-one", R, "o + 2u + l <= SWR_IE_CAP)", "o + 2u + l < SWR_IE_CAP)"),
    ("ie", "cap-one-over", R, "o + 2u + l <= SWR_IE_CAP)", "o + 2u + l <= SWR_IE_CAP + 1u)"),
    ("ie", "ie-copied-without-header", R, "SWSTA_MEMCPY(b->ie + o, ie + o, 2u + l);", "SWSTA_MEMCPY(b->ie + o, ie + o + 2u, l);"),
    ("ie", "ie-copy-one-short", R, "SWSTA_MEMCPY(b->ie + o, ie + o, 2u + l);", "SWSTA_MEMCPY(b->ie + o, ie + o, 1u + l);"),
    ("ie", "ie-stored-wrong", R, "stored = o + 2u + l;", "stored = o + 1u + l;"),
    ("ie", "ie-len-not-set", R, "\tb->ie_len = (uint16_t)stored;\n", ""),
    ("ie", "rsn-id", R, "sec = ie[o] == 48u ||", "sec = ie[o] == 47u ||"),
    ("ie", "wpa-id", R, "(ie[o] == 221u && l >= 4u", "(ie[o] == 220u && l >= 4u"),
    ("ie", "wpa-min-length", R, "(ie[o] == 221u && l >= 4u", "(ie[o] == 221u && l >= 3u"),
    ("ie", "wpa-min-length-5", R, "(ie[o] == 221u && l >= 4u", "(ie[o] == 221u && l >= 5u"),
    ("ie", "wpa-oui-0", R, "ie[o + 2] == 0x00u && ie[o + 3]", "ie[o + 3]"),
    ("ie", "wpa-oui-1", R, "ie[o + 3] == 0x50u && ie[o + 4]", "ie[o + 4]"),
    ("ie", "wpa-oui-2", R, "ie[o + 4] == 0xF2u && ie[o + 5]", "ie[o + 5]"),
    ("ie", "wpa-type", R, "ie[o + 5] == 0x01u);", "1);"),
    ("ie", "wpa-type-any-nonzero", R, "ie[o + 5] == 0x01u);", "ie[o + 5] != 0u);"),
    ("ie", "lost-only-rsn", R, "if (sec) lost = 1;", "if (ie[o] == 48u) lost = 1;"),
    ("ie", "lost-always", R, "if (sec) lost = 1;", "lost = 1;"),
    ("ie", "bad-gap-lost", R, "\tif (bad) swr_gap1(s, SWR_SG_IEBAD, \"bss_info IE walk runs past the block, cut at the last whole IE\");\n", ""),
    ("ie", "cut-gap-lost", R, "\tif (cut) swr_gap1(s, SWR_SG_IECUT, \"bss_info IEs above 512 B, cut at an IE boundary\");\n", ""),
    ("ie", "sec-gap-lost", R, "\tif (lost) swr_gap1(s, SWR_SG_IESEC, \"security IE beyond IE cap, network shows as WEP\");\n", ""),
    ("ie", "cut-gap-on-any-block", R, "\tif (cut) swr_gap1(s, SWR_SG_IECUT,", "\tif (cut || stored != 0u) swr_gap1(s, SWR_SG_IECUT,"),
    # swr_event record fields
    ("event", "avail-not-bounded-by-record", R, "\tif (avail > rlen) avail = rlen;", "\t(void)rlen;"),
    ("event", "snr-needs-125", R, "if (avail >= SWR_BSS_SNR_END) {", "if (avail >= SWR_BSS_SNR_END - 1u) {"),
    ("event", "snr-needs-127", R, "if (avail >= SWR_BSS_SNR_END) {", "if (avail >= SWR_BSS_SNR_END + 1u) {"),
    ("event", "snr-end-const", R, "#define SWR_BSS_SNR_END 126u", "#define SWR_BSS_SNR_END 125u"),
    ("event", "snr-offset", R, "b.snr = (int16_t)swr_le16(r + 124);", "b.snr = (int16_t)swr_le16(r + 122);"),
    ("event", "snr-unsigned", R, "b.snr = (int16_t)swr_le16(r + 124);", "b.snr = (int16_t)(swr_le16(r + 124) & 0x7FFFu);"),
    ("event", "beacon-offset", R, "b.beacon = (uint16_t)swr_le16(r + 14);", "b.beacon = (uint16_t)swr_le16(r + 12);"),
    ("event", "noise-unsigned", R, "b.noise = (int16_t)(int8_t)r[80];", "b.noise = (int16_t)r[80];"),
    ("event", "noise-offset", R, "b.noise = (int16_t)(int8_t)r[80];", "b.noise = (int16_t)(int8_t)r[79];"),
    ("event", "flags-not-set", R, "b.chflags = swr_chflags(s, cs);", "b.chflags = 0;"),
    ("event", "flags-from-ctl-ch", R, "b.chflags = swr_chflags(s, cs);", "b.chflags = swr_chflags(s, r[88]);"),
    ("event", "rate-count-offset", R, "nr = swr_le32(r + 52);", "nr = swr_le32(r + 56);"),
    ("event", "rate-clamp-16", R, "if (nr > SWSTA_RATE_MAX) {", "if (nr > SWSTA_RATE_MAX + 1u) {"),
    ("event", "rate-clamp-14", R, "if (nr > SWSTA_RATE_MAX) {", "if (nr >= SWSTA_RATE_MAX) {"),
    ("event", "rate-clamp-gap-lost", R, "swr_gap1(s, SWR_SG_RATES, \"bss_info rate count above 15, clamped\"); nr = SWSTA_RATE_MAX;", "nr = SWSTA_RATE_MAX;"),
    ("event", "rate-clamp-not-applied", R, "swr_gap1(s, SWR_SG_RATES, \"bss_info rate count above 15, clamped\"); nr = SWSTA_RATE_MAX;", "swr_gap1(s, SWR_SG_RATES, \"bss_info rate count above 15, clamped\"); nr = 16u;"),
    ("event", "rate-basic-bit-kept", R, "(r[56 + i] & 0x7Fu) / 2u", "r[56 + i] / 2u"),
    ("event", "rate-units-500k", R, "(r[56 + i] & 0x7Fu) / 2u", "(r[56 + i] & 0x7Fu)"),
    ("event", "rate-rounds-up", R, "(r[56 + i] & 0x7Fu) / 2u", "((r[56 + i] & 0x7Fu) + 1u) / 2u"),
    ("event", "rate-offset", R, "r[56 + i] & 0x7Fu", "r[55 + i] & 0x7Fu"),
    ("event", "ie-fields-always", R, "\t\tif (ilen != 0) {\n", "\t\tif (1) {\n"),
    ("event", "ie-offset-low-bound", R, "if (ioff < SWR_BSS_SNR_END ||", "if (ioff < SWR_BSS_SNR_END - 1u ||"),
    ("event", "ie-offset-low-bound-up", R, "if (ioff < SWR_BSS_SNR_END ||", "if (ioff < SWR_BSS_SNR_END + 1u ||"),
    ("event", "ie-offset-low-bound-dropped", R, "if (ioff < SWR_BSS_SNR_END ||", "if ("),
    ("event", "ie-offset-above-record", R, "ioff > rlen || ilen > rlen - ioff)", "ilen > rlen - ioff)"),
    ("event", "ie-length-above-record", R, "ioff > rlen || ilen > rlen - ioff)", "ioff > rlen)"),
    ("event", "ie-length-above-record-off-by-one", R, "ilen > rlen - ioff) swr_gap1", "ilen >= rlen - ioff) swr_gap1"),
    ("event", "ie-length-vs-event-dropped", R, "if (ioff > avail || ilen > avail - ioff) {", "if (0) {"),
    ("event", "ie-offset-vs-event-dropped", R, "if (ioff > avail || ilen > avail - ioff) {", "if (ilen > avail - ioff) {"),
    ("event", "ie-partial-length", R, "ilen = ioff > avail ? 0 : avail - ioff; }", "ilen = ioff > avail ? 0 : avail - ioff + 1u; }"),
    ("event", "ie-partial-gap-lost", R, "swr_gap1(s, SWR_SG_IEPOS, \"bss_info IEs beyond the bytes read, partial IE block\"); ilen", "ilen"),
    ("event", "ie-outside-gap-lost", R, "swr_gap1(s, SWR_SG_IEPOS, \"bss_info ie_offset/ie_length outside the record, IEs dropped\");", "(void)0;"),
    ("event", "ie-offset-field", R, "ioff = swr_le16(r + 116);", "ioff = swr_le16(r + 114);"),
    ("event", "ie-length-field", R, "ilen = swr_le32(r + 120);", "ilen = swr_le32(r + 124);"),
    ("event", "ie-pointer-at-record-start", R, "swr_ie(s, &b, r + ioff, ilen);", "swr_ie(s, &b, r, ilen);"),
    # the full-table rule (review 0293 LOW 2)
    ("full", "full-always-drops", R, "if (b.rssi > s->bss[w].rssi) { s->bss[w] = b; s->replaced++; } else s->dropped++;", "s->dropped++;"),
    ("full", "full-always-replaces", R, "if (b.rssi > s->bss[w].rssi) { s->bss[w] = b; s->replaced++; } else s->dropped++;", "{ s->bss[w] = b; s->replaced++; }"),
    ("full", "full-tie-replaces", R, "if (b.rssi > s->bss[w].rssi) { s->bss[w] = b;", "if (b.rssi >= s->bss[w].rssi) { s->bss[w] = b;"),
    ("full", "full-replaces-strongest", R, "if (s->bss[i].rssi < s->bss[w].rssi) w = i;", "if (s->bss[i].rssi > s->bss[w].rssi) w = i;"),
    ("full", "full-weakest-scan-skips-last", R, "for (i = 1; i < s->n; i++) if (s->bss[i].rssi < s->bss[w].rssi) w = i;", "for (i = 1; i + 1 < s->n; i++) if (s->bss[i].rssi < s->bss[w].rssi) w = i;"),
    ("full", "full-weakest-last-of-equals", R, "if (s->bss[i].rssi < s->bss[w].rssi) w = i;", "if (s->bss[i].rssi <= s->bss[w].rssi) w = i;"),
    ("full", "full-replace-not-counted", R, "{ s->bss[w] = b; s->replaced++; }", "{ s->bss[w] = b; }"),
    ("full", "full-drop-not-counted", R, "} else s->dropped++;", "} else (void)0;"),
    ("full", "full-gap-every-record", R, "swr_gap1(s, SWR_SG_FULL, \"scan list full (64 BSSID): weakest replaced by a stronger record, else dropped\");", "swr_gap(s, \"scan list full (64 BSSID): weakest replaced by a stronger record, else dropped\");"),
    ("full", "full-gap-lost", R, "\t\tswr_gap1(s, SWR_SG_FULL, \"scan list full (64 BSSID): weakest replaced by a stronger record, else dropped\");\n", ""),
    ("full", "full-gap-shares-bit", R, "swr_gap1(s, SWR_SG_FULL,", "swr_gap1(s, SWR_SG_CHW,"),
    ("full", "full-replaced-not-carried", R, " s->replaced += tab->replaced;", ""),
    ("full", "full-replaced-not-in-line", R, "(unsigned)tab->ignored, (unsigned)ms, (unsigned)tab->replaced);", "(unsigned)tab->ignored, (unsigned)ms, 0u);"),
    ("full", "full-table-bound", R, "if (s->n >= SWR_MAX_BSS) {\t/* review 0293", "if (s->n > SWR_MAX_BSS) {\t/* review 0293"),
    ("full", "replaced-not-reset-by-tab-reset", R, "t->partial = t->replaced = 0;", "t->partial = 0;"),
    # the lookup of the joined AP
    ("find", "find-not-set", R, " s->radio.bss_find = swr_bss_find;", ""),
    ("find", "find-skips-last", R, "\tfor (i = 0; i < s->n; i++) {\n\t\tif (swr_same6(s->bss[i].bssid, bssid)) { hit = i; break; }", "\tfor (i = 0; i + 1 < s->n; i++) {\n\t\tif (swr_same6(s->bss[i].bssid, bssid)) { hit = i; break; }"),
    ("find", "find-any", R, "if (swr_same6(s->bss[i].bssid, bssid)) { hit = i; break; }", "if (1) { hit = i; break; }"),
    ("find", "find-copies-nothing", R, "if (hit < s->n) { *out = s->bss[hit]; return 1; }\n\treturn 0;", "if (hit < s->n) { return 1; }\n\treturn 0;"),
    # swsta_rec / the codec
    ("rec", "gate-and", T, "if (x->noise != 0 || x->snr != 0) {", "if (x->noise != 0 && x->snr != 0) {"),
    ("rec", "gate-noise-only", T, "if (x->noise != 0 || x->snr != 0) {", "if (x->noise != 0) {"),
    ("rec", "gate-snr-only", T, "if (x->noise != 0 || x->snr != 0) {", "if (x->snr != 0) {"),
    ("rec", "gate-always", T, "if (x->noise != 0 || x->snr != 0) {", "if (1) {"),
    ("rec", "gate-value", T, "swsta_p16(b, 0x10, 1u);", "swsta_p16(b, 0x10, 0u);"),
    ("rec", "rec-noise-offset", T, "swsta_p16(b, 0x12, (uint32_t)(uint16_t)x->noise);", "swsta_p16(b, 0x14, (uint32_t)(uint16_t)x->noise);"),
    ("rec", "rec-snr-offset", T, "swsta_p16(b, 0x14, (uint32_t)(uint16_t)x->snr);", "swsta_p16(b, 0x12, (uint32_t)(uint16_t)x->snr);"),
    ("rec", "rec-beacon-offset", T, "swsta_p16(b, 0x18, x->beacon);", "swsta_p16(b, 0x1a, x->beacon);"),
    ("rec", "flags-offset", T, "swsta_p32(b, 0x0c, x->chflags);", "swsta_p32(b, 0x08, x->chflags);"),
    ("rec", "flags-not-written", T, "\t\tswsta_p32(b, 0x0c, x->chflags);\n", ""),
    ("rec", "rate-clamp-dropped", T, "n = x->nrates > SWSTA_RATE_MAX ? SWSTA_RATE_MAX : x->nrates;", "n = x->nrates;"),
    ("rec", "rec-rate-clamp-16", T, "n = x->nrates > SWSTA_RATE_MAX ? SWSTA_RATE_MAX : x->nrates;", "n = x->nrates > SWSTA_RATE_MAX + 1u ? SWSTA_RATE_MAX + 1u : x->nrates;"),
    ("rec", "rec-rate-count-offset", T, "b[0x22] = (uint8_t)n;", "b[0x23] = (uint8_t)n;"),
    ("rec", "rate-stride", T, "swsta_p32(b, 0x24 + 4 * i, x->rates[i]);", "swsta_p32(b, 0x24 + 2 * i, x->rates[i]);"),
    ("rec", "rate-base", T, "swsta_p32(b, 0x24 + 4 * i, x->rates[i]);", "swsta_p32(b, 0x20 + 4 * i, x->rates[i]);"),
    ("rec", "ie-clamp-dropped", T, "n = x->ie_len > SWSTA_IE_CAP ? SWSTA_IE_CAP : x->ie_len;", "n = x->ie_len;"),
    ("rec", "ie-offset", T, "SWSTA_MEMCPY(b + 0x8c, x->ie, n);", "SWSTA_MEMCPY(b + 0x8a, x->ie, n);"),
    ("rec", "ie-not-copied", T, "\t\tSWSTA_MEMCPY(b + 0x8c, x->ie, n);\n", ""),
    ("rec", "ie-len-offset", T, "swsta_p16(b, 0x8a, n);", "swsta_p16(b, 0x88, n);"),
    ("rec", "ie-len-not-written", T, "\t\tswsta_p16(b, 0x8a, n);\n", ""),
    ("rec", "extras-never", T, "\tif (x) {\n\t\tswsta_p32(b, 0x0c, x->chflags);", "\tif (0) {\n\t\tswsta_p32(b, 0x0c, x->chflags);"),
    ("rec", "get11-no-extras", T, "v->bss->rssi, v->bss->sec, v->bss); return 0;", "v->bss->rssi, v->bss->sec, NULL); return 0;"),
    ("rec", "get103-no-extras", T, "v->st.rssi, v->st.sec, v->bss); return 0;", "v->st.rssi, v->st.sec, NULL); return 0;"),
    ("rec", "get103-takes-entry-ssid", T, "swsta_rec(b, v->st.ssid, v->st.ssid_len, v->st.bssid, v->st.channel, v->st.rssi, v->st.sec, v->bss);", "swsta_rec(b, v->bss ? v->bss->ssid : v->st.ssid, v->st.ssid_len, v->st.bssid, v->st.channel, v->bss ? v->bss->rssi : v->st.rssi, v->st.sec, v->bss);"),
    ("rec", "gap-text-inverted", T, "return v && v->bss && v->bss->ie_len ? 0 : \"record IEs empty (no IEs known)\";", "return v && v->bss && v->bss->ie_len ? \"record IEs empty (no IEs known)\" : 0;"),
    ("rec", "gap-text-never", T, "return v && v->bss && v->bss->ie_len ? 0 : \"record IEs empty (no IEs known)\";", "return 0;"),
    ("rec", "gap-text-always", T, "return v && v->bss && v->bss->ie_len ? 0 : \"record IEs empty (no IEs known)\";", "return \"record IEs empty (no IEs known)\";"),
    ("rec", "lookup-not-done", T, "if (type == SWSTA_T_CURRENT_NETWORK && s->radio->bss_find && s->radio->bss_find(s->radio->ctx, st.bssid, &bss) == 1) v.bss = &bss;", ";"),
    ("rec", "lookup-by-ssid-bytes", T, "s->radio->bss_find(s->radio->ctx, st.bssid, &bss) == 1)", "s->radio->bss_find(s->radio->ctx, st.ssid, &bss) == 1)"),
    ("rec", "lookup-result-ignored", T, "s->radio->bss_find(s->radio->ctx, st.bssid, &bss) == 1) v.bss = &bss;", "s->radio->bss_find(s->radio->ctx, st.bssid, &bss) == 1) v.bss = NULL;"),
    ("rec", "lookup-without-null-check", T, "s->radio->bss_find && s->radio->bss_find(", "1 && s->radio->bss_find("),
    # 2026-11-11 W-D3-weak-security: the join target's record answers the first Get 103; Get 2
    ("kept", "find-list-without-ies-wins", R, "if (hit < s->n && s->bss[hit].ie_len != 0) { *out = s->bss[hit]; return 1; }", "if (hit < s->n) { *out = s->bss[hit]; return 1; }"),
    ("kept", "find-kept-always-wins", R, "if (hit < s->n && s->bss[hit].ie_len != 0) { *out = s->bss[hit]; return 1; }", ""),
    ("kept", "find-kept-for-any-bssid", R, "if (s->kept_bss_ok && swr_same6(s->kept_bss.bssid, bssid)) { *out = s->kept_bss; return 1; }", "if (s->kept_bss_ok) { *out = s->kept_bss; return 1; }"),
    ("kept", "find-kept-ignored", R, "if (s->kept_bss_ok && swr_same6(s->kept_bss.bssid, bssid)) { *out = s->kept_bss; return 1; }", ""),
    ("kept", "find-kept-flag-ignored", R, "if (s->kept_bss_ok && swr_same6", "if (swr_same6"),
    ("kept", "find-list-entry-lost", R, "\tif (hit < s->n) { *out = s->bss[hit]; return 1; }\n\treturn 0;", "\treturn 0;"),
    ("kept", "find-first-of-list-only", R, "if (swr_same6(s->bss[i].bssid, bssid)) { hit = i; break; }", "if (i == 0 && swr_same6(s->bss[i].bssid, bssid)) { hit = i; break; }"),
    ("kept", "target-bssid-unchecked", R, "if (bssid == NULL || !s->kept.joined || !swr_same6(s->kept.bssid, bssid)) {", "if (bssid == NULL || !s->kept.joined) {"),
    ("kept", "target-joined-unchecked", R, "if (bssid == NULL || !s->kept.joined || !swr_same6(s->kept.bssid, bssid)) {", "if (bssid == NULL || !swr_same6(s->kept.bssid, bssid)) {"),
    ("kept", "target-null-bssid-unchecked", R, "if (bssid == NULL || !s->kept.joined ||", "if (!s->kept.joined ||"),
    ("kept", "target-refusal-gap-lost", R, "swr_gap(s, \"kept target record is not for the kept BSSID, ignored\");\n\t\treturn 0;", "return 0;"),
    ("kept", "target-refusal-clears-old", R, "\tif (bssid == NULL || !s->kept.joined || !swr_same6(s->kept.bssid, bssid)) {\n", "\ts->kept_bss_ok = 0;\n\tif (bssid == NULL || !s->kept.joined || !swr_same6(s->kept.bssid, bssid)) {\n"),
    ("kept", "target-sgap-kept", R, "\ts->sgap = 0;\n\tb.chflags = swr_chflags(s, chspec);", "\tb.chflags = swr_chflags(s, chspec);"),
    ("kept", "target-flags", R, "b.chflags = swr_chflags(s, chspec);\n\tb.beacon", "b.chflags = 0;\n\tb.beacon"),
    ("kept", "target-beacon", R, "b.beacon = (uint16_t)beacon;\n\tb.noise", "b.beacon = 0;\n\tb.noise"),
    ("kept", "target-noise", R, "b.noise = (int16_t)noise; b.snr = (int16_t)snr;", "b.noise = (int16_t)snr; b.snr = (int16_t)snr;"),
    ("kept", "target-snr", R, "b.noise = (int16_t)noise; b.snr = (int16_t)snr;", "b.noise = (int16_t)noise; b.snr = (int16_t)noise;"),
    ("kept", "target-rate-clamp", R, "if (nrates > SWSTA_RATE_MAX) { swr_gap1(s, SWR_SG_RATES, \"kept target rate count above 15, clamped\"); nrates = SWSTA_RATE_MAX; }", "if (nrates > SWSTA_RATE_MAX + 1u) { swr_gap1(s, SWR_SG_RATES, \"kept target rate count above 15, clamped\"); nrates = SWSTA_RATE_MAX; }"),
    ("kept", "target-rate-clamp-gap-lost", R, "swr_gap1(s, SWR_SG_RATES, \"kept target rate count above 15, clamped\"); nrates = SWSTA_RATE_MAX;", "nrates = SWSTA_RATE_MAX;"),
    ("kept", "target-rate-basic-bit", R, "for (i = 0; i < nrates; i++) b.rates[i] = (uint8_t)((rates[i] & 0x7Fu) / 2u);", "for (i = 0; i < nrates; i++) b.rates[i] = (uint8_t)(rates[i] / 2u);"),
    ("kept", "target-rate-units", R, "for (i = 0; i < nrates; i++) b.rates[i] = (uint8_t)((rates[i] & 0x7Fu) / 2u);", "for (i = 0; i < nrates; i++) b.rates[i] = (uint8_t)(rates[i] & 0x7Fu);"),
    ("kept", "target-ie-null-unchecked", R, "if (ie_len != 0 && ie != NULL) swr_ie(s, &b, ie, ie_len);", "if (ie_len != 0) swr_ie(s, &b, ie, ie_len);"),
    ("kept", "target-ie-not-parsed", R, "if (ie_len != 0 && ie != NULL) swr_ie(s, &b, ie, ie_len);", ""),
    ("kept", "target-ie-short-gap-lost", R, "\tif (ie_short) swr_gap1(s, SWR_SG_IEPOS, \"kept target IE block longer than 1024 B, the tail is not kept\");\n", ""),
    ("kept", "target-ie-short-always", R, "\tif (ie_short) swr_gap1(", "\tif (1) swr_gap1("),
    ("kept", "target-no-ie-gap-lost", R, "\tif (b.ie_len == 0) swr_gap(s, \"kept target record has no IEs: Get 103 answers privacy without RSN IE, Settings will show Weak Security\");\n", ""),
    ("kept", "target-no-ie-gap-always", R, "\tif (b.ie_len == 0) swr_gap(s, \"kept target record has no IEs", "\tif (1) swr_gap(s, \"kept target record has no IEs"),
    ("kept", "target-not-stored", R, "\ts->kept_bss = b;\n", ""),
    ("kept", "target-flag-not-set", R, "\ts->kept_bss_ok = 1;\n", ""),
    ("kept", "target-ssid-not-kept", R, "SWSTA_MEMCPY(b.ssid, s->kept.ssid, s->kept.ssid_len);\n\tb.ssid_len = s->kept.ssid_len;", "b.ssid_len = s->kept.ssid_len;"),
    ("kept", "target-log-lost", R, "\tif (s->cb.log) {\n\t\tchar l[100];", "\tif (0) {\n\t\tchar l[100];"),
    ("kept", "up-other-bssid-keeps-ies", R, "if (s->kept_bss_ok && !swr_same6(s->kept_bss.bssid, bssid)) { s->kept_bss_ok = 0;", "if (s->kept_bss_ok && !swr_same6(s->kept_bss.bssid, bssid)) { "),
    ("kept", "up-other-bssid-gap-lost", R, "s->kept_bss_ok = 0; swr_gap(s, \"kept link UP on another BSSID: the kept target record (IEs) is dropped\");", "s->kept_bss_ok = 0;"),
    ("kept", "up-same-bssid-drops-ies", R, "if (s->kept_bss_ok && !swr_same6(s->kept_bss.bssid, bssid)) {", "if (s->kept_bss_ok) {"),
    ("kept", "up-drop-never", R, "if (s->kept_bss_ok && !swr_same6(s->kept_bss.bssid, bssid)) {", "if (0) {"),
    ("get2", "auth-lower", T, "swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, 8); }", "swsta_p32(b, 0, 1); swsta_p32(b, 4, 0); swsta_p32(b, 8, 8); }"),
    ("get2", "auth-upper", T, "swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, 8); }", "swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, 0x10); }"),
    ("get2", "auth-upper-offset", T, "swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, 8); }", "swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 12, 8); }"),
    ("get2", "auth-version-word", T, "swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, 8); }", "swsta_p32(b, 4, 1); swsta_p32(b, 8, 8); }"),
    ("get2", "auth-any-security", T, "if (v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK) { swsta_p32(b, 0, 1);", "if (v->st.joined) { swsta_p32(b, 0, 1);"),
    ("get2", "auth-never", T, "if (v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK) { swsta_p32(b, 0, 1);", "if (0) { swsta_p32(b, 0, 1);"),
    ("get2", "auth-gap-always", T, "case SWSTA_T_AUTH_TYPE: return v && v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK ? 0 : \"get 2 auth values open, zeros\";", "case SWSTA_T_AUTH_TYPE: return \"get 2 auth values open, zeros\";"),
    ("get2", "auth-gap-never", T, "case SWSTA_T_AUTH_TYPE: return v && v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK ? 0 : \"get 2 auth values open, zeros\";", "case SWSTA_T_AUTH_TYPE: return 0;"),
    ("get2", "auth-gap-for-any-joined", T, "case SWSTA_T_AUTH_TYPE: return v && v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK ? 0 :", "case SWSTA_T_AUTH_TYPE: return v && v->st.joined ? 0 :"),
    ("get103gap", "103-gap-weak-always", T, "return v && v->bss && v->bss->ie_len ? 0 : v && v->st.sec != SWSTA_SEC_OPEN ? \"no IEs: Settings shows Weak Security\" : \"record IEs empty (no IEs known)\";", "return v && v->bss && v->bss->ie_len ? 0 : \"no IEs: Settings shows Weak Security\";"),
    ("get103gap", "103-gap-weak-never", T, "return v && v->bss && v->bss->ie_len ? 0 : v && v->st.sec != SWSTA_SEC_OPEN ? \"no IEs: Settings shows Weak Security\" : \"record IEs empty (no IEs known)\";", "return v && v->bss && v->bss->ie_len ? 0 : \"record IEs empty (no IEs known)\";"),
    ("get103gap", "103-gap-with-ies", T, "return v && v->bss && v->bss->ie_len ? 0 : v && v->st.sec != SWSTA_SEC_OPEN", "return v && v->st.sec != SWSTA_SEC_OPEN"),
    ("get103gap", "103-gap-secured-inverted", T, "v->st.sec != SWSTA_SEC_OPEN ? \"no IEs: Settings shows Weak Security\"", "v->st.sec == SWSTA_SEC_OPEN ? \"no IEs: Settings shows Weak Security\""),
]


def build_and_run(hdrs, tag, work):
    d = os.path.join(work, tag)
    os.makedirs(d)
    for f in glob.glob(os.path.join(SHIM, "sweet_wlan_*.h")):      # every shim header next to the test: a quoted include resolves in the includer's own directory first, so a header found only through -I SHIM would pull the UNMUTATED sweet_wlan_sta.h
        shutil.copy(f, d)
    for f in base.out_headers(work):
        shutil.copy(f, d)
    for name, text in hdrs.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    for t, srcs in TESTS:
        for s in srcs + ["wlan_a80211_glue_host_test.c"]:      # the helper test file includes sweet_wlan_sta.h relative to ITS directory: it must be the mutated copy too
            shutil.copy(os.path.join(SHIM, s), d)
        for lane, flags in (("plain", ["-O1"]), ("asan", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"])):
            exe = os.path.join(d, "t_" + lane)
            c = base.run(["gcc"] + flags + ["-Wall", "-Wextra", "-Werror", "-D_GNU_SOURCE", "-I" + d, "-I" + SHIM, "-o", exe] + [os.path.join(d, s) for s in srcs])
            if c.returncode:
                return t + " " + lane + "-build", next((x for x in c.stderr.splitlines() if "error" in x), "build failed")[-110:]
            r = base.run([exe])
            if r.returncode != 0 or "RESULT: PASS" not in r.stdout:
                first = next((x for x in (r.stderr + r.stdout).splitlines() if "FAIL" in x or "ERROR" in x or "runtime error" in x), "rc=%d" % r.returncode)
                return t + " " + lane, first.strip()[:100]
    return "", ""


def main():
    srcs = {h: open(os.path.join(SHIM, h), encoding="utf-8").read() for h in HDR_FILES}
    with tempfile.TemporaryDirectory(prefix="scanrecmut-") as work:
        lane, msg = build_and_run(srcs, "real", work)
        if lane:
            print("RESULT: FAIL wlan_scanrec_mutants (the unmutated tests do not pass in the %s lane: %s)" % (lane, msg))
            return 1
        jobs = []
        for clause, name, hdr, old, new in MUTANTS:
            if srcs[hdr].count(old) != 1:
                print("RESULT: FAIL wlan_scanrec_mutants (mutant %s: pattern occurs %d times in %s, must be exactly once)" % (name, srcs[hdr].count(old), hdr))
                return 1
            mut = dict(srcs)
            mut[hdr] = srcs[hdr].replace(old, new)
            jobs.append((clause, name, mut, "m%03d" % len(jobs)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 2)) as ex:
            results = list(ex.map(lambda j: build_and_run(j[2], j[3], work), jobs))
    survived = []
    for (clause, name, _t, _tag), (lane, msg) in zip(jobs, results):
        print("%-8s %-34s %s" % ("KILLED" if lane else "SURVIVED", name, ("%s: %s" % (lane, msg)) if lane else ""))
        if not lane:
            survived.append(name)
    if survived:
        print("RESULT: FAIL wlan_scanrec_mutants (%d of %d mutants survived: %s)" % (len(survived), len(jobs), ", ".join(survived)))
        return 1
    print("RESULT: PASS wlan_scanrec_mutants (null mutant survives the build; all %d mutants killed)" % len(jobs))
    return 0


class TestWlanScanrecMutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table; the RESULT line is on stdout."""

    def test_every_mutant_fails_a_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
