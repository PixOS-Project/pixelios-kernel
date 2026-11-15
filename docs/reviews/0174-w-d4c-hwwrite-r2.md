# Review 0174 W-D4c (sweetwlan=16, stock INB_DW sequence) HW-WRITE r2

Reviewer: Opus 5.5, adversarial, 2026-10-30. Scope: `git diff e9b58ca bf8b5de` (shim/sweet_wlan_g2.h, shim/wlan_g2_host_test.c, tools/test_wlan_wr_mutants.py,
xnu/patches/0174-sweet-wlan-g2-first-dma.patch, docs/reviews/0174-w-d4c-rework-brief.md), worktree `wip/w4-d4c` @ bf8b5de. Prior round: docs/reviews/0174-w-d4c-hwwrite-r1.md.
Sources: DHD `~/pixelios-artifacts/refs/wlan-stage5/` (dhd_pcie.c, dhd_msgbuf.c, bcmpcie.h), golden `~/pixelios-artifacts/golden/20261028T034021Z/trace-wifi4.txt` (TW4:n = file line n),
super docs/research/W-D4c-ds-ack.md s.6. Tags: [src] read from source (file:line), [obs] observed (command named), [guess].

## Verdict: SAFE WITH CONDITIONS

The new order A1, C1, W, (E), D, C2 matches DHD and the golden. Every one of the 40 new stores has a list entry with an exact value, an old-value pin, `once` and a chain.
The F4 stops run before the first store, and the host test passes. I found no store that can cause permanent damage. Two LOW findings (F1, F2) cover what happens while the
dongle may be in DEV_SLEEP: a stop between the ack doorbell and the wake doorbell, and the glue's config reads on the C1 and W doorbells. Fix F1 and F2 (code or a written
deviation plus run-plan rows) before the L16 run. The r1 F6 condition still holds: L14 and L15 each need a PASS first. F3 to F5 are cheap cleanups.

## What was checked and holds

(a) Order against DHD [src]:
- DS_ENTER_REQ with INB_DW, state DS_ACTIVE and host_active_cnt 0: set DEV_SLEEP, then `dhdpcie_send_mb_data(bus, H2D_HOST_DS_ACK)` (dhd_pcie.c:12953-12969). This runs inside
  `dhd_prot_process_msgtype` (dhd_msgbuf.c:7526), and `dhd_prot_upd_read_idx` comes after it (dhd_msgbuf.c:7532). So the order is A1 then C1, as in swg_d4c (sweet_wlan_g2.h:1306).
- The mb data goes as a ctrl-ring item when fw_rev >= 6 and use_mailbox is 0 (dhd_pcie.c:12790-12803). `__dhd_prot_ring_write_complete` does flush, then the WR index, then the doorbell
  (dhd_msgbuf.c:11573-11601). That matches item words, slot, then DB in each post. The doorbell is a plain write (`dhdpcie_bus_ringbell_fast`, dhd_pcie.c:12562ff) with no read.
- Wake assert, from DEV_SLEEP: send DEVICE_WAKE_ASSERT (dhd_pcie.c:12259-12275), set DEV_WAKE, then `OSL_DELAY(1000)` (dhd_pcie.c:12308-12313). The exit wait is dead code behind
  `if (1)`. So W, then 1 ms, matches sweet_wlan_g2.h:1311-1316. There is no extra write and no missing delay.
- Exit note: in DEV_WAKE the code takes the "else" branch (dhd_pcie.c:13068-13072). Then, with deep_sleep TRUE, `dhd_bus_set_device_wake(bus, FALSE)` (dhd_pcie.c:13082-13084)
  sends DEVICE_WAKE_DEASSERT, because the state is DEV_WAKE (dhd_pcie.c:12340-12345), and sets DS_ACTIVE (:12351-12352). All of this runs inside process_msgtype, before the RD update.
  That gives D then C2, as in sweet_wlan_g2.h:1365. `dhdpcie_set_dongle_deepsleep` only sets a host flag (dhd_pcie.c:708-715), so it adds no bus write.
- Values: H2D_HOST_DS_ACK 0x2, H2DMB_DS_ACTIVE 0x20 = WAKE_DEASSERT, DEVICE_WAKE 0x40 = WAKE_ASSERT, ENTER_REQ 0x2, EXIT_NOTE 0x4 (bcmpcie.h:485, 489, 490, 504, 505, 511, 512).
  The epochs (254 + index) % 253 = 2, 3, 4 and the doorbell values FF000002/3/4 and DD020001/2 follow from r1's cites (dhd_msgbuf.c:114, 116, 12400-12402).

(b) Golden [obs `sed -n` on TW4]. Every quoted line exists and says what s.6.1 claims:
- TW4:549990 is `ringbell_fast` 0xff000001 (80.056058), 549992 is 0xdd020001 (80.056065), 550000 is 0xff000002 (80.072388, binder), 550032 is 0xff000003 and 550034 is 0xdd020002 (80.072874/877).
- From 549988 to 550034 the only other accesses are C34 reads/writes, the RC IRQ handler and the C30/C34 ISR. There is no TCM access, no si_srpwr_request and no DAR power request.

(c) F4 paths [src]: in swg_d4c the order is: re-read (sweet_wlan_g2.h:1269), dead-link stop (:1272), TRAP/ASSERT/FWHALT stop through swg_d4c_stops (:1275, :1196-1210, swg_stop -> swg_undo :466-476),
then the item-0 memcmp gap (:1280) and the D2H-WR gaps. All of them come before `swd4b_item_mbdata` and the first row (:1293, :1306).
- Each refusal raises `swl_gap("d4c", no, "high")` and prints a line (:1288-1290).
- The glue has no logic of its own here: the patch's bsd/dev/sweet_wlan_g2.h is byte-equal to the shim copy [obs: `cmp` of the base.py extract against shim/sweet_wlan_g2.h: SAME].
- The host cases cover TRAP (nstore 59, restore 1), FWHALT, and item 0 changed in word 0/1/5 (wlan_g2_host_test.c:2169-2194). The pre-answer dead link is covered at :2308-2314.

(d) A1 to end of E, header and fake [src]:
- Host rows read and store only SWG_SP_HOST (sweet_wlan_g2.h:1031-1067), and doorbells are never read (:1140).
- The exit-note poll reads only host words (swg_d4c_hread :1179-1194). Host reads in the glue cause no TLP (sw_g2_rd SW3_HOST: flush plus a RAM read, patch:651-660).
- The store link checks are RC-local (patch:105-110).
- The fake counts every non-host read and every non-doorbell write from the ack doorbell to the deassert doorbell, and the full case requires 0 (wlan_g2_host_test.c:243-245, 405-407, 2057).
- Exception: the glue reads EP config space on every doorbell. See F2.

(e) Mutants [obs logs/g2-evidence/g2t18/mut2.log: `RESULT: PASS g2 mutants 531/531 killed`, 0 "surviv"].
- Swaps of adjacent glue posts: ack/rd1, rd1/wake, wake/deas, deas/rd2 (test_wlan_wr_mutants.py:3092-3095). Unchained rows: 3087-3091, 3112-3129.
- Header: C1 before A1 with and without the d4mask order check (:3608-3609), range truncations (:3610-3619), no delay, 2 ms, delay before W (:3621-3624), TCM or core reads in the poll (:3625-3626), the F4 re-read and compares (:3664ff).
- I name no surviving order mutant. The two that are missing (F3) should be killed by the strstr chain (wlan_g2_host_test.c:2086-2093) plus the d4mask check (sweet_wlan_g2.h:1137) [src, reasoning, not run: AP-303 / brief].

Lists, counts and gates [src]:
- Glue: sw_g2_d4c_post adds ten item words (old 0, whole word), the H2D-WR slot (v idx, vmask 0xFFFF, old idx-1 on the whole word) and the DB (exact value, omask 0). sw_g2_d4c_rd adds the D2H-RD slot and its DB.
- Each entry is `once`, chained A1 -> C1 -> W -> D -> C2 after the L14 doorbell (patch:420-446, 472-478).
- The five DB values and the four H2D-WR (value, old) pairs are distinct, so the address-plus-value match in sw3_g2_ok (patch:121-131) cannot pick the wrong entry.
- 12+2+12+12+2 = 40 entries, and 49+14+40 = 103 <= SW_G2_MAX 104 (patch:36). A full list sets `bad` and the whole list is refused (patch:612-617).
- Store counts: 59 + 40 = 99 on the full path (wlan_g2_host_test.c:2037), and 59 + 12 + 2 + 12 = 85 with no note (:2126). Host test [obs base.py run: `RESULT: PASS wlan_g2_host_test`].
- Gates: level > 16 is refused (patch:783). d4b = 3 only at 16 (patch:813). The header gate is `x->d4b >= 3` (sweet_wlan_g2.h:1540) and the glue list gate is `h->d4b >= 3` (patch:472). Mutants for the gates are at test_wlan_wr_mutants.py:3061-3063 and 4085-4086.
- r1 F3 (`dline < 64u`, patch ~829) and r1 F4/F5 (host cases above) are applied.

Shellcheck: the diff has no shell file.

## Findings

### F1 LOW: a stop after the ack doorbell and before the wake doorbell leaves the dongle in DEV_SLEEP, and the undo and the W17 restore then touch it without a gap naming that
- Once SWG_D4C_A_DB is stored, the dongle may enter DEV_SLEEP (dhd_pcie.c:12964-12966). A stop in C1 or W (row old, store or read-back failure, glue refusal) runs swg_undo
  (sweet_wlan_g2.h:441-462). swg_undo writes and reads back EP Command (config TLPs). After that, W17 always re-halts the CA7 and restores cfg 0x84 (patch:718, "whatever this returns").
  Both are backplane or config accesses to a possibly sleeping dongle. W-D4c-ds-ack.md U3 (s.6.6) says it is unknown whether that faults or hangs [guess: worst case a completion
  timeout or SError, which a reboot recovers].
- The host test covers these paths (store fail at dr0 and at r0+88, wlan_g2_host_test.c:2223-2232). It checks only the STOP line and the unmask, not a DEV_SLEEP gap. So the run
  summary would not say the restore ran against a sleeping dongle (rule 8).
- Fix:
  - Add `uint8_t d4sleep;` to struct swg_wx. In swg_d4_row, after a doorbell store, set it when `r == SWG_D4C_A_DB` and clear it when `r == SWG_D4C_W_DB`.
  - In swg_stop, before `swg_undo(x)`, add: `if (x->d4sleep) { swl_gap("d4c", "stop after the DS_ACK before the wake assert: the undo and the W17 restore run against a dongle that may be in DEV_SLEEP (U3)", "high"); }`.
  - In the two store-fail cases above, add `has("WIFI GAP: d4c stop after the DS_ACK before the wake assert")`. In the r0+48 and r0+128 cases, add `!has(...)`.
  - Add a mutant that drops the gap.
  - The L16 run plan names this path and the U3 risk. Keeping the CA7 re-halt is acceptable as long as it is loud. If the driver prefers, the glue can skip the W17 backplane part when the flag is set, with its own gap, but that is a separate decision.

### F2 LOW: the glue reads EP config 0x80 (non-posted CfgRd TLPs) on every doorbell, including C1 and W while the dongle may be in DEV_SLEEP; the header says nothing touches the window in that span
- A doorbell store goes sw_g2_wr SWG_SP_PCIE -> sw_g2_bar0, which reads `h->cfg[SWW_CFG_WIN / 4]` (patch:627). Then sw3_g2_ok reads it again (patch:101) and `put:` reads it a third
  time (patch:199). h->cfg is ATU region 0, the endpoint's config space (0124:63, 175).
- Stock rings with one posted write to a cached address and reads nothing (dhd_pcie.c:12562-12600). The golden cannot show config accesses: there are 0 config call sites in TW4
  [obs `grep -o 'rwmmio_...' | uniq -c`].
- The PCIe core must be up to take the wake doorbell, so a CfgRd should complete [guess]. Still, the comment at sweet_wlan_g2.h:1231 ("Nothing touches TCM, 0xC30 or the backplane
  from A1 to the end of E") is true only of the header. The brief's (d) "window access (reads included)" does not hold at the glue level.
- Fix (either one):
  - (a) For SWW_BAR0 with `h->g2` set, compare against the glue's own cached window `h->win` and not `h->cfg[SWW_CFG_WIN / 4]`, in sw3_g2_ok (patch:101), the `put:` check (patch:199)
    and sw_g2_bar0 (patch:627). h->win must be updated on every cfg 0x80 store. Add a glue-harness check that a doorbell store makes 0 cfg reads.
  - (b) Keep the code. Change sweet_wlan_g2.h:1231 to "... no TCM, 0xC30 or backplane access; the glue reads EP config 0x80 three times per doorbell (CfgRd, PCIe core) [guess:
    answered in DS]". Add this as a U-item in W-D4c-ds-ack.md s.6.6 and in SPEC-WIFI D-13.

### F3 LOW: two adjacent-swap order mutants of the header are missing (the brief asks for a swap of any two adjacent writes)
- The header has only "C1 before A1" (test_wlan_wr_mutants.py:3608-3609). "W before C1" and "C2 before D" are not in the suite. They should be killed (see (e)), but nothing shows it.
- Fix: in _d4c_g2, next to :3609, add these four mutants:
  - `("d4c: W before C1 (order check kept)", [(A1+C1 rows call, A_ITEM0..A_DB, then W_ITEM0..W_DB, then C1_SLOT..C1_DB)])`;
  - the same with the d4mask clause `chk` removed;
  - `("d4c: C2 before D (order check kept)", [("ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);", "ex = swg_d4c_rows(x, SWG_D4C_C2_SLOT, SWG_D4C_C2_DB);\n\tif (ex == 0) {\n\t\tex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_D_DB);\n\t}")])`;
  - the same with `chk` removed.

### F4 INFO: the fake's no-access window starts at the ack doorbell, not at A1 as the brief and the comment say
- `f.awin = 1` only on FF000002 (wlan_g2_host_test.c:497). A TCM read added between the first ack item store and the ack doorbell would pass nrdwin. It is harmless on hardware,
  because the dongle has not seen the ack yet.
- Fix: change the comment at sweet_wlan_g2.h:1231 to "from the ack doorbell to the end of E", or set `f.awin = 1` on the first store to `r0 + 40` and keep the check.

### F5 INFO: stale text and cites
- patch:606 says "sweetwlan=16 adds the 14 W-D4c entries". It is 40.
- patch:813 says "16 = 15 + W-D4c (the DS_ACK, SPEC-WIFI K2 + K1)". It should say "(the INB_DW sequence A1, C1, W, D, C2)".
- SWG_D4C_DB_WAKE (sweet_wlan_g2.h:103) and SW_G2_D4C_DB0_WAKE (patch:352) cite dhd_pcie.c:12255-12310, but OSL_DELAY(1000) is at :12313. Cite 12255-12313.
- The W timing deviation is not written down: stock rings W 16.3 ms after the ack (TW4:549992 -> 550000) and G2 rings it microseconds later. Whether the firmware then sends
  an EXIT_NOTE is unknown [guess]. The miss path is loud and makes no deassert (sweet_wlan_g2.h:1337-1341). The L16 run plan must predict both outcomes and name this deviation.

### F6 CONDITION (carried from r1 F6): run L16 only after L14 and L15 each have a PASS; the L16 run plan does not exist yet [obs `ls docs/runplans | grep d4c`: none].
