# 0174 G2 HW-WRITE review, round 2 (Opus 5.5, 2026-10-28)

Verdict: SAFE WITH CONDITIONS. The code fixes for F1, F2, F4 and F7 are correct as written. Before the run: fix the F3 run-plan argument (finding 1, a runtime check) and the two test gaps (findings 2, 3).

Subject: `wip/w4-g2` at 3384709 (diff c0bd4c7..3384709), round 1 = docs/reviews/0174-g2-hwwrite-r1.md.

Final texts were extracted with the mutant runner's own chain (`final_header()`, `chip_header()`, `g2_header()`, `glue_after()` of tools/test_wlan_wr_mutants.py) into /tmp/g2r2/h. `diff -q` of g2 header vs shim/sweet_wlan_g2.h: empty [obs].
- C:line = patched `sweet_wlan.c` there.
- G:line = `sweet_wlan_g2.h`.
- K:line = patched `sweet_wlan_chip.h`.
- W:line = patched `sweet_wlan_wr.h`.
- P:line = patch 0174 line.
- T:line = tools/test_wlan_wr_mutants.py.

Runs this session [obs, 2026-10-28]:
- The G2 host test against the extracted headers (gcc -O1 -Wall -Wextra -Werror), same as shim/build.sh:611-617: `RESULT: PASS wlan_g2_host_test`.
- The glue sweep `run_glue()` on the unmutated glue: `PASS accepted 1050579 bad 0`.
- Four extra glue mutants (findings 2, 4, 6). The full mutant suites and the gate were not rerun (brief).
- No shell files in the diff (shellcheck n/a).

Tags: [src] read in the cited file this session; [obs] observed here or as cited; [guess].

## Answers to the brief

1. **F1: correct, same rule as swl_restore; tests incomplete (finding 2).**
   - [src] sw3_g2_ok (C:319-330) refuses every non-DBI store unless swl_pre_link(iso, LTSSM, ATU r0, ATU r1) == 0 (K:138-161) and all 16 reads succeeded.
   - swl_restore's config rows skip on exactly `rpre` 1..4 or 8 (W:836-839). 8 is a read failure in swl_wpre (W:753-760), the same as `bad` here. Checks 5/6/7 (Command, BAR0, ATU r2) do not block a config row there, and do not block one here. Same rule as review 0133 F4.
   - EP undo (CFG 0x04 := 0x2):
     - Refused exactly when swl_restore would skip it (link not known up).
     - Accepted otherwise: entry old omask 0 (C:619 / P:342, `sw_g2_add(..., SW_G2_EP_MEM, ..., 0, 0, 1, ep)`).
     - swg_undo short-circuits the read-back after a refused store (G:333), so no CfgRd follows from the header.
   - RC undo (DBI 0x04): exempt, which is right.
     - DBI is the RC's own register file, no TLP [src K:131-133 comment, R106].
     - Restoring RC Command also turns off the bridge's Bus Master Enable. [guess, PCI-to-PCI bridge semantics] That stops forwarding of the dongle's upstream DMA. So the RC undo is the one store that should run even on a dead link.
   - The window moves are not bypassed. All of these go through sw3_wr while h->g2 == 1, so through sw3_g2_ok, so through the link check:
     - swg_win -> sw_g2_wr SWG_SP_WIN2 -> sw3_wr (C:681).
     - sw_g2_bar0 -> sw3_wr (C:638).
     - The hook's BAR0 put-back (C:728; h->g2 = 0 only at C:733).
   - Residual reads before or without the check: finding 4.
2. **F2: correct.**
   - [src] The header argument holds. Let d = sh - ri > 0. Any overlap of [ri, ri+0x9C) and [sh, sh+0x60) means d < 0x9C.
   - Case d >= 0x4C: flags (sh+0) lies in ring_mem. Its version is 7 (G:448-450), so it is non-zero, but the 20-word ring_mem loop (G:466-474, the whole [rm, rm+0x50)) requires 0. Stop.
   - Case 4 <= d <= 0x48: flags2 lies at ri+d+0x50 in [ri+0x54, ri+0x98], which is inside ring_mem. The loop needs 0 there, and swg_sh_is later needs 0x40943041 (G:500). Whichever value the word has, one of the two stops, before any store.
   - Both cases rely on rm == ri + 0x4C (G:462-464) and ri < sh (G:455).
   - 0x60 is the right extent: the highest shared word G2 stores or reads is the trap hi word at +0x5C (G:51, G:503).
   - No other stored pair can alias:
     - ring_info words +0x14..0x33 vs ring_mem: disjoint by rm == ri + 0x4C.
     - scratch (0x34..0x3F) vs trap (0x58..0x5F): fixed and distinct.
     - slot vs ring_info host-address word: inside [ri, ri+0x9C), refused (F_OVL_RI).
     - slot vs slot: the pairwise check.
   - The glue's own copy (C:587-592) is correct [src] but untested (finding 3).
3. **F4/F7: correct.**
   - The window clause is now ahead of `old = *p` (C:315 vs C:335).
   - No wrap anywhere [src]:
     - `end - 16u` cannot wrap: end = SWW_RAM_BASE + ram_bytes >= 0x200000 (W:92, W:2159).
     - `sh > SWW_WIN_TAIL - 0x60u` admits sh = 0x3FFFA0, whose last word is +0x5C = 0x3FFFFC (the last window-0 word). ri and rm likewise.
     - The overlap sums are bounded by those checks.
   - Untested (finding 5).
4. **F3 note: not true to the run it guards, not enough (finding 1).**
5. **Includes: fine.**
   - [src] sweet_wlan_g2.h uses printf, snprintf (G:355), NULL and _Static_assert. NULL comes from <stddef.h> through sweet_wlan_wr.h. printf/snprintf come from <sys/systm.h>, included first by sweet_wlan.c. That is the same arrangement as sweet_wlan_wr.h, which also calls snprintf (W:60-62, W:884).
   - The host test includes stdio/string/stdlib before the header.
   - No mem*/str* use is left.
   - Not rerun: the driver's `tools/gate-xnu-build` PASS on 3384709.
6. **Tests: two rules untested, one equivalence argument incomplete (findings 2, 3, 5, 6).**

## Findings

1. **MED (run condition): the F3 note analyses the G1 run's block address, not the one G2 will get, and its detection sentence is false.**
   Where: super docs/runplans/wlan-runG2-0174.md:10, C:849 vs C:857, G:514-551, runplan:39.
   - [src] At level 12 the block is allocated by `sweet_wlan_g1(&hw.g2plan, &hw.g2pa)` before stage 3 (C:849). The G1 run (level 11) allocated it after stage 3 (C:857). The note reasons from that run's 0x884e84000.
   - The level-12 PA is a fresh kmem_alloc_contig anywhere in xnu_ram [0x880000000, 0x980000000) (board.json:10). [guess] Different time in boot, so a different address is likely.
   - The run plan's `--expect` (runplan:39) accepts any `pa 0x[0-9a-f]+`.
   - So the low alias `pa & 0xFFFFFFFF` can fall in several places [src probe-log-2026-10-21-run1.txt:132-170]:
     - sec_dram 0xB7200000-0xBFFFFFFF (TZ, no-map);
     - sec_pt 0xE0000000-0xE24FFFFF;
     - abl@0xF8800000;
     - for pa >= 0x900000000, the range 0x0-0x7FFFFFFF, below DRAM: SoC MMIO.
   - The note's last sentence ("A dropped hi dword shows as `G2 poll: no dongle write seen` with the G1 block untouched") has no code behind it. swg_poll reads only TCM words: flags, d2h mailbox, ring_mem idx/type (G:514-551). A dongle that takes the rings returns "wrote a ring_mem idx/type word" and `G2 RESULT done` wherever its index DMA went. Nothing reads the G1 block.
   - Smaller point [obs golden iomem.txt:184-191]: "plain System RAM 80000000-901fffff with no carve-out" is only true of the analysed 0x84e84000-0x84eaffff. That range lies between Kernel data (ends 0x8229ffff) and reserved 0x86000000. The bank itself has reserved sub-ranges (0x81be0000, 0x86000000-0x87f19fff, 0x87fff000, 0x8a000000, 0x90000000).

   Failure scenario [guess]: the G2 boot gets pa 0x8B7300000. The dongle drops the hi dword. Its first index/completion DMA goes to 0xB7300000 in sec_dram (TZASC fault or secure-memory corruption). Or, for pa 0x9xxxxxxxx, it goes to an MMIO register. The run log still reads `G2 RESULT done ... undo=ok`.

   Fix:
   - In the glue at level 12, right after C:849, refuse G2 (g2ok = 0, `swl_gap("g2", "G1 block low alias outside the checked range", "high")`, loud printf with pa and alias) unless both hold:
     - `(pa >> 32) == 8`;
     - `[pa & 0xFFFFFFFF, + round_page(total))` lies inside one range named in the run plan as checked, e.g. [0x822A0000, 0x86000000): no FDT carve-out [obs probe log], plain RAM under stock [obs iomem]. ABL use is still unknown [guess].
   - Print the alias.
   - Add a run-plan `--expect` on that line.
   - Rewrite the note's detection sentence: a dropped hi dword is NOT detected by this run. Or add r1 F3's optional telemetry (read the d2h_w index array in the G1 block through its kernel VA after the poll; non-zero = the dongle DMA'd to the right place).
   - Add one host test for the range check.

2. **MED: the F1 link rule is untested for the stores it was added for (EP Command undo, window moves, hostready).**
   Where: T:2066-2072 (lc[] has only BAR2 flags2 rows and one DBI row), C:319.
   - [obs] Two mutants survive the glue sweep (`PASS accepted 1050579 bad 0` for both):
     - `if (space != SW3_DBI && space != SWW_CFG)`: exempts exactly round 1's F1 scenario, the EP undo on a dead link;
     - `if (space != SW3_DBI && space != SWW_BAR0)`: exempts hostready.
   - Fix: add lc[] rows, each followed by the done-flag check already in the loop:
     - CFG 0x04 := 0x2 (EP undo; set the EP forward entry done first; old 0x6) with ld 1..4 -> refused, and with ld 0 -> accepted;
     - CFG 0x84 := 0x400000 (window move) with ld 2 -> refused;
     - BAR0 win SWW_WIN_PCIE off 0x144 := 0x12345678 with ld 3 -> refused.
   - Add both mutants above to GLUE_MUTANTS.

3. **MED: the glue's F2/F7 checks in sw_g2_list have no behavioural test and no mutant.**
   Where: C:579-592, P:296-318; T:229-243 only pins their text in the assignment audit.
   - [src] glue_function_text() (T:2106-2113) compiles sw3_ptr..sw3_wr only, so sw_g2_list never runs in a test.
   - Round 1's F7 asked for a ri = 0xFFFFFFF0 case. None exists (grep: no `0xFFFFFFF0`, no `F7` in the tests).
   - The glue list is the independent second layer (R107 #5). A typo in its overlap test (e.g. `16u` -> `4u`, or the rm clause dropped) would pass the gate.
   - I read the logic as correct [src]. This is a coverage defect, not a known logic defect.
   - Fix: extend glue_function_text() (or a second harness) to compile sw_g2_tcm + sw_g2_add + sw_g2_list with a fake TCM, then:
     - run the base with the R89 values (expect 49 entries);
     - run cases ra[k] on sh+0x50, rm+8, ri+0x14, another ra+0xC (each -> -1, g2n 0);
     - run touching edges (ra = sh - 16, ra = rm + 0x50: allowed);
     - run ri = 0xFFFFFFF0, rm = 0xFFFFFFF0, sh = 0x3FFFA4 (-> -1).
   - Mutate each clause.
   - Also add the two cheap structural checks round 1 F2(a) asked for: `ri < sh` and `rm == ri + 0x4Cu`. Without them the glue alone allows ring_info/ring_mem/shared aliasing between full-omask, old-0 entries. That aliasing is self-detecting only while every admitted value is non-zero. A PA lo word is 0 when a sub-buffer starts at a 4 GiB multiple (0x900000000 is inside xnu_ram).

4. **LOW: non-posted reads still happen before or without the link check.**
   Where: C:315 (cfg 0x80/0x84 read for the BAR0/BAR2 window clause, before C:319), C:674 (`g2ep` re-read of cfg 0x04 even after sw3_wr refused, on a dead link too), C:728 (put-back condition reads cfg 0x80), C:643-660 (every G2 TCM/EP read, including the 20-poll loop, has no link check).
   - [src] W17 runs swl_wpre before every poll read (W:2167).
   - Impact [guess]: a few CfgRd/MemRd on a link already known dead. The first all-ones read stops G2 (G:524-526). The rest are of the same kind as the read that detected it.
   - Fix:
     - move the C:319-330 block above the C:315 window clause, for non-DBI spaces;
     - in sw_g2_wr set g2ep from the live word only when `rc == 0`, else 0xFFFFFFFF (also closes round 1's F5, still open);
     - optionally run the same link check in sw_g2_rd for non-DBI spaces.

5. **LOW: F4 (window before the old read) has no killing test.**
   Where: C:315.
   - [obs] A mutant that drops both window clauses from sw3_g2_ok's first `if` survives the glue sweep. The store is still refused at `put` (C:382-385), and a volatile read of a plain array leaves nothing to see.
   - Fix: have the sweep record the last swl_gap text and require "store outside the G2 state" (not "not in the G2 list") for a case with live cfg 0x84 != win and an otherwise matching entry.

6. **LOW: the dropped "link read failure ignored" mutant is equivalent, but not for the stated reason.**
   Where: C:311, T:2155.
   - [src] The stated reason (iso/ltssm start at 0, so swl_pre_link fails) covers only iso and ELBI. a0[7]/a1[7] are uninitialized (C:311).
   - The `bad` term is what stops a failed ATU read from comparing stack garbage against w0/w1.
   - The mutant is equivalent only because sw3_rd(SWW_ATU, const off < 0x1000) cannot fail: sw3_ptr has no NULL check for h->atu (C:248-249).
   - [obs] gcc -Werror rejects a mutant that ignores the ATU read result (maybe-uninitialized).
   - Fix: `a0[7] = { 0 }, a1[7] = { 0 }`. Then 0 fails w0[1]/w1[1] = 0x80000000, and the comment at T:2155 becomes true for all 16 reads.

## Not checked
- The full mutant suites, the gate, and `tools/gate-xnu-build` on 3384709 (brief: the driver runs them).
- What ABL/the shim leave in the low DRAM bank under our boot.
- Whether the gs101 PCIe path has an S2MPU/SysMMU that would block a stray DMA [guess: unknown].
- Dongle hi-dword handling: only the run answers it.
