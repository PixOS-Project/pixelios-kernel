# Review 0174 W-D4a HW-WRITE r1 (adversarial, Opus 5.5, 2026-10-29)

Scope: `git diff 2648c95 91218ac -- shim/sweet_wlan_g2.h shim/wlan_g2_host_test.c tools/test_wlan_wr_mutants.py` in $WORKTREES/w4-d4a
(the 0174 patch embeds shim/sweet_wlan_g2.h byte-identically: checked with tools/test_wlan_wr_mutants.py `g2_header()` == the shim file, `identical True` [obs]).
Line numbers: `S:` = shim/sweet_wlan_g2.h at 91218ac; `T:` = shim/wlan_g2_host_test.c at 91218ac; `P:` = xnu/patches/0174-sweet-wlan-g2-first-dma.patch at 91218ac;
`M:` = tools/test_wlan_wr_mutants.py; refs = $OFFLOAD_HOME/pixelios-artifacts/refs/wlan-stage5/.

Verdict: SAFE WITH CONDITIONS

The diff adds no store and no new register class (read from source: no new `swg_put`/`b->wr` call; the only new bus writes are `swg_win` cfg 0x84 selects of 0 or
SWW_WIN_TAIL, S:183-197, and the glue list allows exactly those two values with no count, P:344). The 45 store rows, their order and the store count are unchanged
(T:474-477 `x.nstore == nwant - 12u` with 12 window moves, `stores=45 mask 0xffffffff` [obs host test]). Conditions: fix F1 and F2 before the run; F3-F6 may ride along.

Verification run [obs, 2026-10-29]: `python3 -B tools/test_wlan_wr_mutants.py --g2` -> `RESULT: PASS g2 mutants 158/158 killed, glue list harness + 103 mutants OK`.
Reviewer's own extra mutants (scratch script, compiled against the same chain): baseline at EP / at RC row KILLED; FWHALT moved after the success test KILLED;
mailbox pointer re-read each poll KILLED; d2h_w slot offset +4 KILLED; no window restore after any stop KILLED (only by F_RESTOREFAIL); **no window restore after
a stop at step READY (baseline or poll) SURVIVED** (F2).
Shellcheck: no shell file in the diff (n/a).

## Checks (each answered)

1. Reads only / glue acceptance. New bus traffic: TCM reads (swg_rd, S:199-206), one BAR0 read of PCIe core +0xC30 (S:550) through `sw_g2_rd` SWG_SP_PCIE
   (P:456-457: `sw_g2_bar0` then `sw3_rd(h, SWW_BAR0, SWW_WIN_PCIE, a, v)`), and cfg 0x84 selects. cfg 0x80 is already SWW_WIN_PCIE since the entry's SRPWR
   read (S:438-442 at the entry; nothing in G2 moves cfg 0x80 afterwards), so `sw_g2_bar0` stores nothing new [src]. After `EP Command := 0x6`, `sw_g2_wr`
   sets `h->g2ep` from the live Command low half (P:475); `sw3_g2_ok` checks window moves against `swl_pre_ep(..., h->g2ep)` (P:117) and matches the
   once=0 entries 0 / SWW_WIN_TAIL (P:344, P:125-126). So a move after the EP store is accepted [src]; the glue harness has that exact case,
   "window move after the EP store (g2ep 0x6)" (M:2050). A refusal is loud: `sw3_g2_ok` gap + line (P:118-119 or P:130-131), then `swg_win` gap
   "BAR2 window select refused" + `cfg 0x84 := ... refused` (S:191-193), then `swg_rd` fails and the baseline/poll stop with SWG_E_ADDR (S:602-604, S:623-625),
   which runs the undo. Not silent. Note: no window move with EP Command 0x6 has ever happened on hardware; on the observed layout (shared 0x427938,
   d2h_w array 0x408c80, mb data 0x44868c, all >= 0x400000 [obs merged.log run G2 lines 546-547]) the new reads cause none either [src arithmetic].
2. Window state. Every stop calls `swg_stop` -> `swg_undo` (S:360-371), then `swg_go` puts cfg 0x84 back to 0 if `x->rt.win != 0` (S:698-700);
   `swg_win` does not test `x->stopped`, so the restore runs after a stop [src]. W17's swl_restore SWW_RG_W84 follows anyway (S:5-6). Code: correct.
   Test: not asserted on the new stop paths (F2).
3. Mailbox pointer. Read once in `swg_entry` (S:489-492) after the shared-structure checks and before the first row (no store precedes it: rows start
   in `swg_go` after the entry). `swg_ram` (S:175-180): `(bp & 3) == 0 && bp >= SWW_RAM_BASE && bp <= end - 4` -> last word end-4 accepted, `end` refused
   (no off-by-one; T F_MBP_OOR uses END). Dereference: poll reads TCM[mbp] (S:540, S:553), matching dhd_pcie.c:6713-6715 [src]. FWHALT 0x10000000 =
   bcmpcie.h:528 `D2H_DEV_FWHALT` [src]; DHD stops on it at dhd_pcie.c:12901 and 13156 [src].
4. D2H write array. 0167 enum order H2D_W, H2D_R, D2H_W, D2H_R (P0167:145-148) -> SWG_IDX(SWD_IDX_D2H_W) = 2; ring_info pointer order ringmem,
   h2d_w, h2d_r, d2h_w, d2h_r (bcmpcie.h:306, 313-316) and S:54 / S:495 read `ri + 4 + 4*i` in that order [src]. Three slots at +0, +4, +8 inside the
   16 B array (SWG_IDX_SLOTS 4, range checked at S:495) [src]. Note: this is the TCM array, not the host-memory array the research ranked first (F4).
5. 0xC30. Same path as hostready +0x144 (P:456-457 read, P:479-480 store) and `sw3_ptr` BAR0 `off < 0x1000` (P0129). pcie_core.h:415 `mbox_intstatus /* 0xC30 */`,
   pcie_core.h:1046 `PCIMailBoxInt(rev) (REV_GE_64(rev) ? 0xC30 : 0x48)`. DHD reads it with `si_corereg(..., 0, 0)` (mask 0 = read: dhd_pcie.c:1223, 4918, 9265)
   and clears it only by a write (dhd_pcie.c:1269, 9266); the golden reads it twice with the same value before the W1C write (research W-D4a s.4 row 446-453)
   [src + golden]. No read side effect found. All 0xC30 bits are SBtoPCIE (dongle -> host) bits (pcie_core.h:507-528), so hostready itself should not set one [src; guess for this core].
6. Baseline placement: `swg_base` runs in `swg_go` just before `swg_st(x, SWG_ST_READY)` (S:677-683), i.e. after the EP row and before the hostready store [src];
   host test asserts it (T:481-489: base line before the hostready store line, first 0xC30 read with exactly ir-1 bus writes before it). Diff logic: success =
   any of six words != baseline (S:557-568, S:640-653); window moves change no value; a word read twice in one pass compares equal; TRAP/ASSERT and FWHALT are
   tested before the diff (S:630-639) and a mutant moving FWHALT after it is killed [obs]. False success from all-ones reads: F1.
7. Rule 8. Gap texts 37 and 55 chars (< 63, 0137 `char what[64]`, P0137:194) [obs `${#s}`]; the 67-char string at S:489 is `x->fail`, printed by printf, not a
   gap key. Missing host block: loud gap at S:607 once per run (dedup by text, P0137:209). No-change: gap + final line (S:661-666). No silent success except F1.
8. Tests: see F2 (window end state on the new stop paths), F1 (no all-ones case). Every other new clause has a killing mutant (M diff lines 23-62) [obs run].

## Findings

### F1 MEDIUM: an all-ones read counts as "dongle activity" (false success) or is misnamed FWHALT
S:640-653 counts any difference as success. If the BAR0 read of 0xC30 or a TCM d2h_w slot returns 0xFFFFFFFF (link down / device removed) while the flags word
still read fine in the same pass, the poll prints `change vs baseline in c30` (or `tcm d2h_w[k]`) and returns 0, and `swg_go` ends `done` (S:701). DHD treats
`intstatus == (uint32)-1` as link down, never as an interrupt (dhd_pcie.c:1231-1235, 1484-1489). A mailbox data word of 0xFFFFFFFF hits the FWHALT test
(S:635) and is reported as `FWHALT`, not DEAD. The same holds at the baseline (S:602): an all-ones c30 before hostready is accepted.
Fix: in `swg_poll`, directly after the `cur.fl == 0xFFFFFFFFu` test (S:627-629), add
`if (cur.mb == 0xFFFFFFFFu || cur.c30 == 0xFFFFFFFFu || cur.dw[0] == 0xFFFFFFFFu || cur.dw[1] == 0xFFFFFFFFu || cur.dw[2] == 0xFFFFFFFFu) { x->fail = "an all-ones read after hostready (link down?)"; return swg_stop(x, SWG_E_DEAD); }`
(before the TRAP and FWHALT tests); in `swg_base` after the read (S:602-604) the same test with `x->fail = "an all-ones read before hostready"` and `swg_stop(x, SWG_E_DEAD)`.
Host test: add F_C30_ONES (fake returns 0xFFFFFFFF for 0xC30 after nready > 0) expecting `rc == -SWG_E_DEAD`, undo ok, `!has("change vs baseline in")`; and
F_MB_ONES expecting DEAD, not `FWHALT`. Mutants: drop each of the five clauses.

### F2 MEDIUM: window end state is not asserted on the new stop paths (a mutant survives)
The new paths can stop with cfg 0x84 on the tail: F_POLL_RDFAIL fails the mailbox read with the window on SWW_WIN_TAIL (T:216). The cases F_POLL_RDFAIL /
F_POLL_RDFAIL2 (T:671-673), F_FWHALT (T:697-699), F_FATAL_TRAP (T:668-669) and F_C30_RDFAIL (T:701-703) never check `f.win2`. Reviewer mutant
`if (x->rt.win != 0u && !(x->stopped && x->step == SWG_ST_READY) && swg_win(x, 0u) != 0)` at S:698 SURVIVED [obs]. Code is correct today; the brief's check 2
is untested. Fix: add `CHECK(f.win2 == 0u);` to each of those five cases,
and add to G2_MUTANTS in M: `("go: no window restore after a stop at hostready", [("if (x->rt.win != 0u && swg_win(x, 0u) != 0) {", "if (x->rt.win != 0u && !(x->stopped && x->step == SWG_ST_READY) && swg_win(x, 0u) != 0) {")])`.
Confirm F_POLL_RDFAIL actually stops with `x->rt.win == SWW_WIN_TAIL` (it does in the default layout: baseline ends on the tail, poll 1 reads the mailbox first).

### F3 LOW: new print fields use `%#x` for words that are normally 0 (AP-85)
S:578, S:608, S:665 print mb data, d2h_w, c30 and ring_mem with `%#x`, so 0 prints as `0` and non-zero as `0x...` (host test expects `mb data 0`, T:479-482).
The run plan will regex these lines. Fix: use `0x%08x` for mb data, the three d2h_w, c30 and ring_mem idx/type in all three printf (and the TRAP/FWHALT
lines S:631, S:636), update the T: expected strings. Also rename `d2h mb %#x` in the TRAP/ASSERT line (S:631) to `mb data 0x%08x`: it now prints the data,
the old label printed the pointer (AP-235).

### F4 LOW: the host-block gap reason is inaccurate, and the research's rank-1 signal is not read
S:607 says "no kernel mapping of the G1 block", but the block has a permanent kernel mapping: `kmem_alloc_contig(... KMA_PERMANENT ...)` (P:207) and is flushed
with `flush_dcache64` (P:235); only the VA is not handed to G2 (P:240-242 keep plan and PA). The research ranks the host D2H write-index slots first (W-D4a s.2 (a),
s.9 step 2); the TCM d2h_w array G2 reads instead has no source that the firmware updates it when DMA indices are in use [guess]. A "no change" result will
therefore not settle U-a. Fix (text only, no new mapping in this change): gap text `"host block not read (G1 VA not passed to G2)"` (45 chars), and the run plan
must state that "no change" leaves U-a open for host memory. Passing the VA (out param of sweet_wlan_g1) + `invalidate_dcache64` before each read is a separate change for the driver to decide.

### F5 LOW: comment claim "each pass moves cfg 0x84 at most once" is false in general
S:530-531. True for the host-test layout (mb on the tail, everything else on 0) and trivially for the observed run (no moves). For shared on the tail and
the d2h_w array on 0 (allowed by W-D3c), a pass with `win == 0` reads flags (-> tail), d2h_w (-> 0), mb (-> tail): three moves. Harmless (allowed entries,
once=0, P:344) but rule 5. Fix: reword to "the order keeps the observed layout at no move and the default test layout at one move per pass; other layouts may move more (each a glue-list entry)".

### F6 INFO
- Brief req 8 asked the gap text to list the words; the 63-char limit forbids it and the list is in the final line (S:661-663). Accepted.
- `G2 STOP ... at step 31 hostready` is printed for a baseline failure although hostready was not stored (S:601 sets step READY); `x->fail` names the baseline.
  Run plan: match the fail line, not the step name.
- Neither new gap text is in docs/KNOWN-GAPS-WIFI.md (super) [obs grep]: the run report will list them as NEW; the run plan should predict that.
- 0xC30 being non-zero with mask 0 (U-d) is not distinguished from a dongle reaction: a c30-only change should be decoded by bit in the run plan (pcie_core.h:507-528).

## Driver resolution (G2 thread 10, 2026-10-29)
- F1 MEDIUM fixed in 1d4077e: `swg_pw_ones()` stops DEAD on an all-ones flags / mb data / d2h_w slot / 0xC30 read, in the baseline and every poll, before TRAP/FWHALT/change (host cases F_C30_ONES, F_MB_ONES, F_DW_ONES, F_BASE_ONES + mutants) [obs host: --g2 170/170 killed].
- F2 MEDIUM fixed in 1d4077e: second stop-at-READY pass with every polled word on the tail window asserts `f.win2 == 0`; restore-skip mutant killed (AP-239).
- F3-F5 LOW fixed in 1d4077e: `0x%x` prints, TRAP line says `mb data`, gap text `host block not read (G2 has no VA of the G1 block)`, window-move comment corrected.
- F4 run-plan note carried into docs/runplans/wlan-runG2-d4a.md (super): a "no change" result leaves U-a (host memory) open.
- Light checks on b18bddf (1d4077e + trunk 68d2cf3) [obs host]: --g2 170/170, validate-repo PASS, check-rules 0 new. No further Opus round (fixes are test/print/stop-path only; cap AP Opus-3).
