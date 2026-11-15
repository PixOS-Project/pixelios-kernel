# Review 0174 W-D4b HW-WRITE r1 (Opus, adversarial)

Scope: the non-merge commits 81b4004, e6d6d2a, dde3967 on wip/w4-d4b, as `git diff 659638d e6da3e2 --` of
xnu/patches/0174-sweet-wlan-g2-first-dma.patch, 0137-sweet-wlan-gap-report.patch, 0167-sweet-wlan-g1-host-dma.patch and shim/sweet_wlan_g2.h.
Line numbers are lines of xnu/patches/0174-sweet-wlan-g2-first-dma.patch ("P:n") unless stated otherwise. shim/sweet_wlan_g2.h is byte-identical to the
patch's new sweet_wlan_g2.h (P:844-2139) [observed: diff of the extracted hunk = empty], so header line h = P:(h+843).
Reviewer: Opus 5.5, 2026-10-29. Read-only. No phone, no build beyond the host tests.

## Verdict: SAFE WITH CONDITIONS

- Level 12 and level 13: SAFE. No new store class. The only extra bus writes are BAR2/BAR0 window moves (cfg 0x84 / 0x80) to values the glue list already allowed at 659638d (F4).
- Level 14: SAFE WITH CONDITIONS. Before a level-14 run: F1 (cfg 0x88 position) is decided and written down, and F2 (how the cfg 0x88 restore is armed) is fixed and has a test. F3 must be fixed before anyone uses the run's result to close U2. The other findings are LOW.

Verification (run in this worktree, 2026-10-29) [observed]:
`python3 -B tools/test_wlan_wr_mutants.py --g2` -> rc 0,
`RESULT: PASS g2 mutants 359/359 killed, glue list harness + 158 mutants OK, d4b helpers 30/30 killed, g2va gate 6 mutants OK, host bus flush audit 10 mutants OK, total mutants 563; W-D4b gaps items: 118 header + 21 glue list + 23 glue store mutants (dump/console, miss resolution, 2BYTE pin, host-block gap, cfg 0x88, 16-bit slots)`

Reviewer mutants, run on a `git archive` copy in /tmp (the worktree was not touched; the copy has been deleted) [observed]:
- M-a: `if (g.c88_tried)` -> `if (g.c88_done)` at P:680. **Survives** (`--g2` RESULT PASS, same line as above).
- M-b: swl_rs row `{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88, ... }` -> value `SWW_CFG88 | 0x300u` at P:791. **Survives** `--g2`. shim/wlan_wr_host_test.c never mentions 0x88/C88 [observed grep], so the default suite most likely does not kill it either [guess, not run: the full suite is the driver's job].

Shellcheck: the diff contains no shell file (patches and C headers only) [observed]. Nothing to run.

## Checks with no finding (what was verified)

1. **New reads at level 12** [read from source]. All new reads are in swg_dump (h:623, called from swg_entry h:790), and every one goes through swg_rd (h:246: swg_ram bounds, 4-aligned, below `end`):
   rings_info words ri+0..ri+0x48 (19 words); shared+0x14 (console_addr); hnd_cons_t +8/+12/+16; at most SWG_CON_MAX=2048 console bytes (one 32-bit TCM read per byte); shared+0x28 (h2d mailbox pointer) and the word it points to; TCM[mbp] (d2h mailbox data).
   All of them are TCM RAM words (BAR2). None is a core register, so none can be W1C, a FIFO or clear-on-read. The console write index is only read; out_idx is never written.
   The other new level-12 behaviour: swg_entry stops on flags 0x100000 (2BYTE). The observed flags 0x70050107 have that bit clear, so nothing changes on this chip. The G1 plan also grows by evbuf (total 167936 -> 176128, still inside the same round_page allocation of 180224 B) [src 0167:4, 0167 SWD_EVBUF hunk].
   The 0xC30 read (DAR mailbox int status, W1C on WRITE only) existed at 659638d.
2. **Level 13** [src]. swg_c88 returns at d4b<2 (h:514). The w16 path needs d4b>=2 (h swg_st). sw_g2_d4 and sw_g2_tcm16 are built only when h->d4b>=2 (P:510, 520, 524). sw3_wr16 refuses d4b<2 (P:208).
   swg_d4b returns before any swg_d4_row when d4b<2 (h:1159 `if (x->d4b < 2) return 0`). The reads swg_d4_read makes are swg_pw_read (TCM + 0xC30) and swg_hrd (host block, reads only).
   The host test counts stores: nstore == 45 at 13, the same as at 12 (shim/wlan_g2_host_test.c:1145, 1158). This excludes window moves (F4).
3. **Store pinning (level 14)** [src]. Each new store has one glue entry built from the glue's own constants and its own plan copy. All are `once` and chained:
   mask 0xC34:=0xFFFF, old 0 (P:386) -> item words 0..9, old 0, each after the previous one (P:389-392) -> H2D-WR slot low half :=1, old 0 (P:393) -> DB0 0x140 :=0xFF000001 (P:395). Unmask 0xC34:=0 after the mask (P:396). cfg 0x88 := 0x80380 with old pinned to 0x80080, after the EP Command entry (P:521).
   The restore store is exactly SWW_CFG88, made once, and only while h->c88 (P:160-166, 183-190). The header checks the same things itself (swg_d4_row h:1085-1120, swg_c88 h:510-549).
   The item layout matches bcmmsgbuf.h:181-192, 410-420 (msg_type 0xD at :237). epoch 1 = 254 % 253 (bcmmsgbuf.h:38-39; dhd_msgbuf.c:1823, 6357). Phase 0x80 on the first item at the ring base (dhd_msgbuf.c:10777-10787). DB0 = 0xFF000000 | idx<<16 | wr (dhd_msgbuf.c:116, 11600) [src refs/wlan-stage5].
4. **Undo order** [src]. swg_undo calls swg_d4_unmask FIRST (h:421-426), before the EP and RC Command restores. swg_stop runs swg_undo on every stop path (ORDER, ADDR, OLD, WR, READBACK, DEAD, FATAL/TRAP/FWHALT in the d4b poll). swg_go runs it on rc==0.
   Every negative return in swg_d4b comes from swg_stop or is -SWG_E_STOPPED (undo already ran). The unmask uses swg_put plus a direct rd, not swg_rd, so it still runs after `stopped`.
   **cfg 0x88 restored after the CA7 re-halt** (swl_rs order SM, CA, W84, C88, P:787-791; swl_restore walks the table in order, 0133:287+). This is safe with respect to DMA: EP Command := 0x0002 (bus master off) and RC Command := old (0) are both stored in swg_undo BEFORE W17's re-halt. The 0x300 bits (PCIE_BARCOHERENTACCEN, BAR1/BAR2 host->dongle coherency, W-gaps s.2 [src include_pcicfg.h via research]) do not gate dongle->host DMA. So their order relative to the halt cannot open a DMA window.
5. **Host block and cache** [src+obs]. All PAs come from the audited G1 plan. swg_hoff (h swg_hoff) bounds [pa, pa+n) to [blk, blk+blk_len) and 4-alignment. evbuf is checked for its 8192 B range and 4 KiB alignment before use (h:1136-1139). It sits after the trap page, inside total 176128.
   The index arrays (+0x27a80 h2d_w 0x100 B, +0x27b80 h2d_r, +0x27c80 d2h_w 0x80 B, +0x27d00 d2h_r; R-D4b line 24 [obs LOG:236-239]) start on 64 B boundaries and are at least 0x80 apart. Every buffer is padded to 128 B, so no 64 B line holds both a host-written word and a dongle-written word.
   The host writes only ring-0 item 0 and h2d_w slot 0. Their lines are dongle-READ only. Every dongle-written line (h2d_r, d2h_w, ring 2, evbuf) was cleaned+invalidated by G1 after KMA_ZERO (P:293) and never stored by the host afterwards. So the clean half of `dc civac` can never write back over dongle data: the lines are never dirty.
   flush_dcache64 is real here: SWEET.h does not define `__ARM_COHERENT_IO__` (docs/reviews/gpu-xnu-job-review-1.md:112), so osfmk/arm/caches.c:56-91 is built. FlushPoC_DcacheRegion ends in `dsb sy` (osfmk/arm64/caches_asm.s ~420) [src xnu-pristine]. So host store -> civac+dsb -> DB0 device store is ordered.
   Remaining hazard: a speculative refill between the invalidate and the load. It only gives a stale read, not corruption, and it is printed as a GAP (`flush_dcache64 used as invalidate`).
6. **sw3_wr16** [src]. h->tcm = sw_map(SWW_TCM_PA, ...) (0129:133) -> ml_io_map (0124:105-109) -> io_map(..., VM_WIMG_IO, ...) (xnu osfmk/arm64/machine_routines.c:1544): Device memory, so a 16-bit access is architecturally fine.
   The cite machine.c:1050-1051 is the `case 2: *(volatile uint16_t *)vaddr` of ml_io_write [src xnu-pristine; ml_io_write16 itself is at :1148].
   DHD writes 16-bit TCM words the same way (dhd_pcie.c:6269-6283). The offset is an exact entry match (entries are 4-aligned, P:358), and `&tcm[off/4]` + little endian means the low half.
   Read-back: 32-bit, compared to `old & 0xFFFF0000` (h swg_val SLOT). The 32-bit/16-bit entry split is exclusive (P:135 `e->w16 == (w16 ? 1 : 0)`).
7. **Console** [src]. swg_con (h:582): size != 0, size%4 == 0, buf in RAM, `size <= end - buf` (no underflow, since swg_ram(buf) gives buf <= end-4), idx <= size. The loop runs exactly len <= 2048 times. p < size always, so every read is in [buf, buf+size) inside RAM, and each one is re-checked by swg_rd. A corrupt hnd_cons_t cannot loop or leave the window.
8. **Deferred gaps** [src]. A miss is resolved only when its flag is set after a successful read: rd_con (swg_con==0), rd_mb (all three mailbox reads ok), rd_ri (all 19 words), pins_ok (2BYTE and counts verified), hostrd (swg_d4_read ok), c88_done.
   Unresolved misses are printed by swl_miss_flush inside swl_gap_summary (0137:303-307). A full table falls back to an immediate gap. I found no path that resolves a miss whose word was not read.

## Findings

### F1 MEDIUM: cfg 0x88 |= 0x300 is stored while the CA7 runs firmware; DHD stores it only with the ARM halted (rule 7)
- Where: swg_c88 called at SWG_ST_READY (h swg_go, P:2101 `rc = swg_c88(x)`); glue entry P:521; comment "golden seq 423 ... before hostready" (h:22-23, P:318).
- Source: refs/wlan-stage5/dhd_pcie.c:2224-2228 makes the write in `dhdpcie_dongle_attach` (starts :2088), right before `if (!dhdpcie_is_arm_halted(bus)) { ... "FW is already running! Abort." }` (:2235-2239). So DHD sets the BAR1/BAR2 coherent-access bits BEFORE firmware download and ARM release.
  Your own research said the same: W-gaps-runG2-d4a.md:45 "at golden position (after cfg 0x208, before the BAR0/ARM release)".
  The golden order file only shows that seq 423 comes after the attach config writes (393/397 BAR, 420/421) and before hostready 431. BAR2 memcpy and wrapper traffic are not all in that file, so it does not show the ARM state.
  KNOWN-GAPS-WIFI.md:107 ("after the C5 stores, before hostready") and the code follow the weaker reading.
- Failure scenario: the coherency bits flip under a running CA7 that already has the shared structure and ring_info cached, after 45 host BAR2 stores made with the bits clear. Effect unknown [guess]: possibly stale lines or a firmware trap. Not permanent: config space is reset by the next power cycle or PERST. But it turns the level-14 result into a two-variable experiment (post + coherency change at once).
- Fix: either (a) move the RMW to the DHD position: stage 3, after W0's `cfg88 == SWW_CFG88` check and before the CA7 release, as its own swl_wt row W15 with the C88 restore armed from that row. Or (b) keep it at READY but add a SPEC-WIFI decision row (D-12) saying "deviation from dhd_pcie.c:2224-2228: set with the CA7 running", and print `WIFI GAP: w15 cfg 0x88 set with the CA7 running (DHD: at attach, ARM halted)` [medium]. Even better: put it on its own level (e.g. 15) so that level 14 tests the post alone.

### F2 MEDIUM: the cfg 0x88 restore is armed from the header's `c88_tried`, not from the glue's "store happened"; two mutants on the restore survive
- Where: P:680-682 `if (g.c88_tried) x->armed |= SWW_RG_C88;`; swl_rs row P:791.
- Failure scenarios:
  (1) The code is correct today, but nothing pins it: mutant M-a (`c88_done`) survives. With it, a store that took but whose read-back failed or mismatched (swg_c88 h:536-542) leaves cfg 0x88 = 0x80380 with no restore.
  (2) When swg_put is refused by the glue (no store happened, h->c88 stays 0), c88_tried=1 still arms the restore. swl_restore then gets a refusal and prints `restore ... FAILED` plus the high gap "glue: cfg 0x88 store outside the G2 state refused". That is a false alarm (loud, not unsafe).
  (3) The swl_rs value is untested: mutant M-b (restore value 0x80380) survives `--g2`. The glue would refuse it, so it is loud, but it is not caught.
- Fix: arm from the glue's own truth, `if (h->c88) { x->armed |= SWW_RG_C88; }`, set right after `*p = v` (P:183-185). Keep `g.c88_tried && !h->c88` as a printed line ("cfg 0x88 store attempted but not made, no restore armed").
  Add host tests: (i) hook + fake swl_restore with a read-back mismatch: cfg 0x88 ends at 0x80080; (ii) store refused: no restore attempt and no FAILED line; (iii) wlan_wr_host_test: swl_rs C88 row value == SWW_CFG88 and its index > the SWW_RG_CA row index (kills M-b and a row-order swap).
  Pin `x->armed |= SWW_RG_C88` together with its condition in the GLUE_ASSIGNS audit, or add mutants for the condition.

### F3 MEDIUM: "no dongle write seen" is suppressed by changes in words the dongle changes on its own; the zero-item gap is checked only at the baseline (rule 8)
- Where: swg_d4b poll loop h:1206-1218 (`seen = 1` on ANY of 14 words: flags, mb data, tcm d2h_w[0..2], c30, host words) -> h:1241 `if (!seen)`. The zero-item gap is only at h:1163-1169 (baseline). After the poll (h:1228-1240) a nonzero d2h_w with an all-zero item gives `ex = -1` and no gap.
- Failure scenario [obs merged.log runG2-d4a]: hostready alone already moved 0xC30 bit 0 and TCM d2h_w[0]. After DB0, a c30 or mailbox-data change sets seen=1, so `WIFI GAP: d4b no dongle write seen` [high] does not print, although no host-memory word changed. That is exactly the U2 question (inbound DMA to 0x8_ PAs) the run is meant to answer.
  The same goes for host d2h_w advancing while ring-2 item 0 still reads zero (DMA of the index landed, the item did not, or a stale line): no gap, only raw values in a line.
- Fix: track `hseen` = a change in h2dr, d2hw or r2[0..5] only. Print the gap `no dongle write seen in N ms` when !hseen. Print TCM/c30/flags changes as before but under their own line, e.g. `d4b only TCM/core words changed (no host-memory write)`.
  After the poll, repeat the zero-item check with `((cur.d2hw | cur.pw.dw[0]) & 0xFFFF) != 0 && r2 all zero` -> the high gap. Add two host-test cases (c30-only change; d2hw != 0 with a zero item) that must print the gaps.

### F4 LOW: levels 12/13 add window stores; the claims "reads only" / "no new store" are not literally true
- Where: swg_dump (h:623) and swg_d4_read (h swg_d4_read -> swg_pw_read h:797) go through swg_rd -> swg_win (h:229-243), which stores cfg 0x84 (0 or SWW_WIN_TAIL). The c30 read goes through sw_g2_bar0 (P:543-549, cfg 0x80 := SWW_WIN_PCIE). Comments h:9, h:329, P:1 and SPEC Q0 say "no new store".
- Failure scenario: none on hardware. The values and the precondition (swl_pre_ep with g2ep, P:124) are the 659638d list entries. But the audit claim is wrong, and nstore does not count these moves (host test :1145), so a future non-window store at level 13 hidden among window moves is not separated by any test.
- Fix: reword the claim to "no new store class; extra cfg 0x84/0x80 window moves to the listed values". In wlan_g2_host_test, record the full bus-write log at 12 and 13 and assert that every write not in the 659638d set is a cfg 0x80/0x84 move to {0, SWW_WIN_TAIL, SWW_WIN_PCIE, w80}.

### F5 LOW: glue host-block path: flush before its own bound check; sw3_ptr(SW3_HOST) checks neither alignment nor the 4-byte end
- Where: P:567-569 `flush_dcache64(g2pa + a, 4, TRUE)` runs before sw3_rd validates `a`. P:89-90 `off < h->g2plan.total` (an offset of total-2 would access 2 bytes past the plan; still inside the round_page allocation).
- Failure scenario: only through a header bug, because the header's swg_hoff bounds `a`. But R107 #5 says the glue must not trust the header. Clean+invalidate of an arbitrary kernel PA preserves data and is not a corruption, but it is an unbounded cache op on a PA the glue did not check.
- Fix: in sw3_ptr SW3_HOST require `(off & 3) == 0 && off <= h->g2plan.total - 4`. In sw_g2_rd/sw_g2_wr SWG_SP_HOST, return -1 with a gap if `sw3_ptr(h, SW3_HOST, a) == NULL` before calling flush_dcache64.

### F6 LOW: DB0 (PCIe core +0x140) is read twice before and once after the store; the golden only writes it (rule 7)
- Where: header swg_d4_row h:1095 (old) and h:1110 (read-back) also for SWG_D4_DB0. Glue sw3_g2_ok P:129 `old = *p` for every entry, including omask-0 doorbells.
- Source: golden 437/438 are writes only, with no read of 0x140. DHD's ringbell only writes (dhd_msgbuf.c:11616-11626). The 0x144 precedent (golden 431/433 read it) does not cover 0x140. The read side effect of the H2D mailbox register is not documented in any source the patch cites.
- Failure scenario: [guess] a read of the doorbell register is benign on BCM PCIe cores, but it is unverified, and the run would not tell us.
- Fix: in swg_d4_row skip both reads for SWG_D4_DB0 (print "read not made, golden writes only"). In the glue, skip `old = *p` when `e->omask == 0` (match the entry first on space/win/off/v, then read old only if omask != 0), or add an entry flag `noread`.

### F7 LOW: the glue does not chain the d4b entries after hostready
- Where: P:386 mask entry `need = -1`; hostready entry P:523 (`0x144`, need -1).
- Failure scenario: a header bug that runs swg_d4b before hostready (or with the hostready store refused) would get mask/item/slot/DB0 accepted by the glue. The header gate (h swg_go: d4b only on rc == 0 after swg_poll) is then the only defence, against R107 #5.
- Fix: `int ready = sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x144u, ...)`, pass it into sw_g2_d4(h, ready), and use it as `need` of the mask entry. Add a glue harness case "mask before hostready -> refused".

### F8 LOW: stale safety text in the new header and patch
- Where: P:2146-2147 (sweet_wlan_d4b.h) says "NOT called by anything yet (the host-block stores, 0xC34 and DB0 are a later reviewed step)". swg_d4b calls both helpers. P:1 and h:11 say "unreviewed / review pending".
- Failure scenario: the next reviewer trusts "not called" and skips the helpers.
- Fix: replace with "called by swg_d4b (sweet_wlan_g2.h) at sweetwlan>=13 (check) and 14 (item)". Update the "unreviewed" markers with this review's id after F1/F2 are closed.

### F9 LOW: unmask 0xFFFF relies on "no ELBI/GIC path", which is not checked on this kernel
- Where: h:1144-1147 gap text "interrupts not enabled, mask 0xFFFF, no ELBI/GIC path"; mask store P:386.
- Failure scenario: [guess] if anything under XNU enables the PCIe1 RC's interrupt (ELBI irq enable, MSI, or the GIC SPI), unmasking all 16 DAR mailbox bits could assert a level interrupt that nothing acks, from the mask store until the unmask in swg_undo (at most ~2 s). Golden 439-441 show the RC irq handler path is live under Android.
- Fix: before the mask store, make one read-only check: the ELBI interrupt enable word the golden reads (0x...49a004, golden 441, read 0) through the existing ELBI mapping. Print it, and refuse the mask store if it is nonzero. Otherwise mark the gap claim as [guess] in KNOWN-GAPS-WIFI.md.

## New antipattern (reported here, not added to ANTIPATTERNS.md: reviewer is read-only outside this file)
- "Restore armed from the caller's intent flag (`tried`) instead of the store primitive's own 'store happened' state". This one was right by luck, and the inverse mutant (`done`) survived. A restore must be armed where the store is made (glue), and a mutant must cover the arming condition.
- "Evidence flag computed over a word set that includes words known to move on their own (0xC30, TCM d2h_w[0] after hostready), which suppresses the gap the run exists to measure." The success or no-change test must use only the words that answer the question.
