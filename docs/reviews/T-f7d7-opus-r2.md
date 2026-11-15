# T-f7d7 review (Opus, round 2): commit 0ea8a44a, L25 flow ring item size 56 -> 48

Verdict: **SAFE WITH CONDITIONS**. No CRITICAL or HIGH finding. The store judge is complete and tight for 48 B items: it judges all 12 words of a txpost and limits DATA stores to [0, 16*48) plus the tx buffers. The conditions are findings 1 and 2, two test gaps where a surviving mutant shows that a regression in this change would go unnoticed. Findings 3 and 4 are stale size invariants and comments.

## Requirement checks
R1. Store judge for 48 B items [read from source, mutants observed]
- DATA store window. `sweet_wlan_l25_wl.h:403-426`: L5 is [0x4000, 0x4000+16*2048). L4 is `off < SWL25W_TX_N * SWL25W_FLOW_ISZ` = [0, 768), with slot = off/48 and k = off%48/4 in 0..11, and `slot >= SWL25W_TX_N` is refused. Every other DATA offset is refused (`:426`). The flow ring region [DATA+0, DATA+16*48) is enforced.
- Commit judge. `swl25w_item_ok` `:323` reads n = 48/4 = 12 words at `slot*48 + 4k` (`:329`). All 12 words go through `swl25w_tx_word`.
- L1 create word 7. `:189` requires `64 | 48<<16`. That matches `swdp_build_flow_create` (`sweet_wlan_data.h:188`: max_items at +28, len_item at +30).
- Word layout vs `host_txbuf_post_v1` (bcmmsgbuf.h:1062-1093):

| bytes | field | judge |
|---|---|---|
| 0-7 | cmn_hdr | w0, w1 |
| 8-21 | txhdr | w2-w4 and the low half of w5, free |
| 22 / 23 | flags / seg_cnt | upper half of w5 = 0x0101 |
| 24-31 | metadata addr | w6, w7 = 0 |
| 32-39 | data addr | w8, w9 |
| 40 / 42 | metadata_buf_len / data_len | w10 |
| 44-47 | ext_flags union / marker | w11 = 0 |

  The total is 48. `swdp_build_txpost` (`sweet_wlan_data.h:208-221`) writes the same offsets and memsets `isz`. v1 is the right type because flags2 has neither TXCSO 0x200000 nor TXPOST_EXT 0x400000 (bcmpcie.h:153-154, bcmmsgbuf.h:1145-1149).
- flags2 bit check: 0x40943041 & 0x400000 = 0 (worked by hand). The engine only runs with flags2 == 0x40943041 (`sweet_wlan_g2.h:26,869`). The table's flags2 store 0x40943041 -> 0xc0943041 (`sweet_wlan_tbl_data.h:5`) sets only bit 31.

R2. Remaining 56 / TX_EXT use in the L25 path [read from source]
- `sweet_wlan_l25.h:45` `sh_fl`: finding 3.
- `sweet_wlan_l25_hs.h:159` `it[SWDP_ISZ_TX_EXT]` is an upper-bound buffer, and the build uses `env.flow_isz` (`:176`). OK.
- `sweet_wlan_g2d.h:5` `SWG_DP_ITEMW_MAX 14` is a maximum. OK.
- `wlan_en0.h:328` uses `env.flow_isz`. OK.
- `sweet_wlan_data_plan.h:79,86,121,177` are parameterised by `flow_isz`. OK.
- DATA layout: the flow ring is 64*48 = 3072 B and ends below 0x4000. OK.
- Patch 0206: all 8 new-file hunks are byte-equal to shim/ (awk extract + cmp, observed). The `index 00000000..0a0993a1` / `..06b13698` blob ids in the hunk headers are stale (now fb7042a6 / 90bb524c). Git does not check them for new-file hunks, and the build compares content, so they are cosmetic.

R3. Create fields vs DHD [read from source]
- len_item: DHD sends `H2DRING_TXPOST_ITEMSIZE` (dhd_msgbuf.c:12509; the commit message's "12437+73" = 12510 is off by one). That constant is 48 unless the build defines `BCMPCIE_EXT_TXPOST_SUPPORT` (bcmmsgbuf.h:54-58). 48 is consistent with the device flags.
- max_items: **not equal**. DHD sends `flow_ring->max_items` (:12508). It is attached with `prot->h2d_max_txpost` (:11260-11261, :4257), which defaults to `H2DRING_TXPOST_SIZE_V1/V2` = 512 (:521, :535, :1137, :1169). L25 sends 64. This value did not change in this commit; see unproven.

## Findings
1. LOW (test gap) at `sweet_wlan_l25_wl.h:323`. No test pins the 12th word of the commit judge.
   - Proof [observed]: mutant `SWL25W_FLOW_ISZ / 4u - 1u` (11 words judged) passes `wlan_l25_host_test`, `wlan_l25_wl_host_test` and `wlan_glue_l25_host_test` (all rc=0).
   - Fix: in `wlan_l25_wl_host_cases.h` `case_partial`, after `acc(SWL25W_SP_HOST, HO(H2DW) + 8u, 1u, SWL25W_L7);` add:
     ```
     *bptr(SWL25W_SP_DATA, 44u) = 1u;
     mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW) + 8u, 1u);
     *bptr(SWL25W_SP_DATA, 44u) = 0u;
     ```
   - Checked in a scratch copy [observed]: the candidate still passes, and the mutant now fails `wlan_l25_wl_host_test` (rc=1).
2. LOW (test gap) at `sweet_wlan_l25.h:88`. The flow range length is not pinned.
   - Proof [observed]: mutant `SWL25_FLOW_DEPTH * SWDP_ISZ_TX_EXT` in `e->flow` passes all three tests.
   - Fix: in `wlan_l25_host_test.c` after line 396 (`CHECK(swl25_env(&x, &L) == 0 ...)`) add `CHECK(L.env.flow.len == 64u * 48u && L.env.flow_isz == 48u);`.
   - Checked in a scratch copy [observed]: the candidate passes, and the mutant fails `wlan_l25_host_test` and `wlan_l25_wl_host_test`.
3. LOW at `sweet_wlan_l25.h:45` and `:24`. The flow shadow is still sized for 56 B items.
   - `sh_fl[16 * 56 / 4]` = 224 words = 896 B, which is 18.67 items of 48 B.
   - `swg_dp_shadow` (`sweet_wlan_g2d.h:90`) therefore returns a shadow for pa = flow.pa + 768..895 (slots 16, 17 and part of 18).
   - This contradicts the invariant at `:24`: "a later slot has no shadow and stops the engine (SWG_E_ADDR)".
   - Not reachable today: `sweet_wlan_l25_hs.h:169` refuses wr >= 16 first, and the judge refuses at `sweet_wlan_l25_wl.h:414/417`. One of three layers is loosened.
   - Fix: `uint32_t sh_fl[SWL25_SHADOW_ITEMS * SWDP_ISZ_TX_BASE / 4u];`, mirrored into patch 0206. Checked in a scratch copy [observed]: all three tests pass with it.
4. LOW (doc / dead code) at `sweet_wlan_l25_wl.h:247` and `:262`.
   - The comment still cites "swdp_build_txpost, isz 56".
   - `case 11: case 12: case 13:` reads "ext flags / pktinfo / pad". v1 has only word 11 (ext_flags/scale/rate/exp_time or marker, bcmmsgbuf.h:1081-1092), so cases 12 and 13 are now unreachable (k <= 11 by `:323` and `:415`).
   - Fix: change the comment to "isz 48 (v1)" and make the case `case 11: return v == 0u ? 0 : "L4 tx post: ext_flags/marker word is 0";`. Mirror both into 0206.

## Unproven (no severity)
- Whether the dongle firmware accepts max_items 64 (DHD uses 512), and which field produced BCMPCIE_BADOPTION = 3 (bcmmsgbuf.h:743) in the observed run. Without a firmware decompile, the source does not settle whether 48 alone clears status 3.
- The observed flags2 value and the status-3 line of run 20261107T130420Z-wlan-runG2-join25: I did not find the run log under ~/pixelios-artifacts (depth-limited search). I checked them only against the shim's pin `SWG_FLAGS2_OLD` and the docs/reviews/0174-g2-hwwrite-r2.md:43 reference.

## Ran
- `./build.sh --board bluejay | grep -a 'FAIL|RESULT.*l25'`: the only output was two "self-test caught ... restore=FAILED" lint self-test lines. The host tests were served from the cache (digest .ok, build.sh:509), so no `RESULT ... l25` line printed.
- Rebuilt and ran `wlan_l25_host_test`, `wlan_l25_wl_host_test` and `wlan_glue_l25_host_test` in a scratch copy: all rc=0.
- Ran 7 mutants:
  - Killed: M1 l25 `flow_isz` EXT, M4 word 11 free, M6 wl ISZ 56, M7 metadata length free.
  - Survived: M2 flow range 56 (finding 2), M3 11 words (finding 1), M5 L4 range 16*56 (equivalent: the slot >= 16 check refuses the same offsets).
- Ran the patch 0206 extract + cmp.

## Read only
bcmmsgbuf.h:36-60, 740-752, 1062-1149; bcmpcie.h:150-158; dhd_msgbuf.c:521-535, 1125-1175, 4250-4260, 11250-11266, 12437-12530; the shim files cited above.
