# 0174 G2 HW-WRITE review, round 3 of 3 (Opus 5.5, 2026-10-28)

**Verdict: SAFE.** No HIGH or MED findings. The four LOWs are a comment, a missing list of equivalent mutants, and one misleading gap line. None of them lets a store through.

## Subject

- Branch `wip/w4-g2`, diff `2c7d469..bb6ffc8`:
  - `tools/test_wlan_wr_mutants.py`: G2L_HARNESS + 78 G2L_MUTANTS.
  - Patch 0174 `sw_g2_list` glue: `ri > sh - 0x9Cu`.
- I also read the r2 F1 code from 2c7d469 (`sw_g2_alias_ok` and its call site), because the harness now tests it. r2 reviewed 3384709, so it never saw that code.
- How lines are cited:
  - P:line = line in `xnu/patches/0174-sweet-wlan-g2-first-dma.patch`.
  - T:line = line in `tools/test_wlan_wr_mutants.py`.
  - The glue was read through `glue_after()`.

## Runs this session [obs, 2026-10-28]

- `python3 -B tools/test_wlan_wr_mutants.py WlanWrMutants.test_g2_list_harness_passes WlanWrMutants.test_g2_list_mutants_are_killed`: `Ran 2 tests in 12.177s`, `OK`.
- Extra check (temporary harness copy; nothing written to the repo):
  - Added a case `sh = 0x10` (expected: refused).
  - Unmutated glue: `pass`.
  - Glue with `sh < SWW_RAM_BASE ||` dropped: still `pass`, so the case is refused even without that clause (see finding 1 for why).
- No shell files in the diff (shellcheck: not applicable).
- Not run, per the brief: full suite, build.sh, gate.

## Requirement 3: glue change against the hard rule

**Deny by default holds.**

- **Evaluation order** [src P:317, C11 6.5.14]: `||` evaluates left to right with a sequence point and stops at the first true operand.
  - `sh - 0x9Cu` is evaluated only after `sh < SWW_RAM_BASE` (0x200000, patch 0129:247) is false, so it cannot wrap.
  - `ri <= sh - 0x9C` and `sh <= SWW_WIN_TAIL - 0x60` give `ri + 0x9C <= sh < 0x400000`, so nothing wraps.
  - The old bounds `ri > TAIL - 0x4C`, `ri >= sh` and `rm > TAIL - 0x50` are all implied by the new one.
- **No list store lands on another list word** [src glue sw_g2_list]. The list stores to:
  - shared +0x34/+0x38/+0x3C/+0x50..+0x5C, inside [sh, sh+0x60);
  - ring_info +0x14..+0x33, inside [ri, ri+0x4C);
  - ring_mem +0x04..+0x4F, inside [rm, rm+0x50), where rm = ri+0x4C;
  - index slots: the first 8 or 12 bytes of each 16-byte array.

  ring_info and ring_mem end at or below sh, so those three blocks are disjoint. Each array is checked against all three blocks and against every other array. `sw_g2_tcm` maps each backplane address to exactly one (window, offset) pair, so the same TCM word cannot appear as two list entries in two windows.
- **Observed layout passes** [obs R89 line 12]:
  - 0x3b1b6c + 0x4C = 0x3b1bb8 = ringmem_ptr;
  - 0x3b1b6c + 0x9C = 0x3b1c08 <= 0x3ced34;
  - 0x3ced34 <= 0x3FFFA0.

  The harness case "F3 layout" builds all 49 entries.
- **Gating in `sw3_g2_ok`** (P:~92-133): a G2 store goes through only if all of these hold, in this order:
  1. level, g2ok, pointer and live window;
  2. the link check (every space except DBI);
  3. the EP precondition for window moves;
  4. a list match (value, mask, old value, once/done, need).

  `h->g2 = 1` is set only after `sw_g2_list` returned 0. A refused list leaves g2n = 0; this is checked by the mutant "list: refusal keeps the list".

## Requirements 1 and 2: harness coverage and vacuity

- **Every clause of `sw_g2_add`, `sw_g2_tcm`, `sw_g2_alias_ok` and `sw_g2_list` has a killing case or is equivalent** [src T:2511-2590, obs both tests OK]. The four checks below are covered (the claims in the T:2508 comment are checked under findings 1 and 2):
  - Bounds: the clause and its off-by-4 are both killed, e.g. `sh - 0x98` by "ring_mem overlaps shared" and `sh - 0x4C` by the same case.
  - Edges that touch must be accepted:
    - "shared at its top edge": ri = sh - 0x9C;
    - "index arrays touching shared, ring_info, ring_mem": ra = rm + 0x50;
    - "index arrays touching each other".
  - Wrap: ri = 0xFFFFFFF0.
  - Every value, mask, once and need of all 49 entries, compared one by one against `want()`.
- **`want()` is independent where it matters.** It uses literal values: 0x18001000, 0x400000, 0xC0943041, 0x10823407, 0x0507, 0x0006/0x0002, 0x12345678, 8. The literals match patch 0129:264 and the glue defines. Its windows and offsets are its own (the T() macro).
  - It shares `swd_rings[]` and `swd_plan_make()` with the glue. That is the 0167 header's own territory, not a gap of this harness [src].
  - `G2PA` 0xA822A4000 has a hi dword different from its lo dword, so "hi = lo" mutants are killed. The plan offsets differ per buffer, so order and buffer swaps are killed.
- **`lay()` cannot write outside `tcm`:**
  - `sh + 0x30 <= 0x3FFFD4`;
  - the ring_info words are guarded by `ri + 4k >= ri && < sizeof(tcm)` (T:2338-);
  - `tcm` is 4 MiB = SWW_TCM_BYTES (patch 0129:242), so `sw3_ptr`'s bound matches the array.
- **Entries cannot pass the check vacuously.** All entries are pre-set `done = 1` and every built entry is checked for `done == 0`. The "built twice" case catches a list that is not reset.
- **Every refusal case is reached and refused for its intended reason.** I traced each refused layout:
  - each sets `tcm[ri] = rm`, so only the clause under test fails;
  - e.g. "ring_info below the RAM base" uses rm = ri + 0x4C, so the ringmem_ptr check passes and only `ri < SWW_RAM_BASE` refuses.
- **`SW_G2_MAX` and the structs are restated in the harness (T:2253), not taken from the glue.** They are equal today (glue `SW_G2_MAX 56u`, same struct fields) [src]. If they drift, the list fails closed: a smaller real SW_G2_MAX refuses the list, which is safe. This is a note, not a finding.

## Requirement 4: the r2 LOWs

- **LOW 4 (non-posted reads before the link check; `g2ep` re-read after a refused EP store): accepted, upheld.** I found no failure scenario that leads to a store.
  - The early reads (cfg 0x80/0x84 window clause, `h->cfg[0x04]` at P:410) are the same kind of transaction (TLPs to the endpoint) as the G2 header's own TCM poll reads, and those have no link check at all.
  - Worst case on a dead link [guess]: an SError/panic. Linux `dw_pcie_other_conf_map_bus` has a link check for exactly this reason. That ends in a reboot, not a write.
  - About `g2ep = live`: only G2's list can store to EP Command, and only with whole-word values 0x0006 (old low half 0x0002) or 0x0002 (`need` = the first store). So the live low half is 0x2 or 0x6.
  - A dead link (all-ones) or an endpoint reset (BAR0 no longer 0x60000004) is refused first by the link check and then by `swl_pre_ep`'s BAR0 clause, before any window move.
  - Hygiene, optional: `h->g2ep = rc == 0 ? live : 0xFFFFFFFFu`. Then `swl_pre_ep`'s Command clause means "== the value G2 set" rather than "unchanged since the last EP store".
- **LOW 5 (F4 window-before-old-read untested): accepted, upheld.**
  - BAR0 stores in G2 go only through `sw_g2_bar0()`, which moves the window first and returns -1 before `sw3_wr` if the move failed.
  - BAR2 stores take their window from the address.
  - So a wrong live window is reachable only if the header itself moved WIN2. Even then, the old-value read is a MemRd of another TCM word, and the store is refused at `put`.
  - The check matters only as a guard against a future regression; the cheap test r2 described is still recommended.
- **LOW 6: fixed** [src P:99, `a0[7] = { 0 }, a1[7] = { 0 }`].

## Findings

1. **LOW: the equivalence argument for `sh < SWW_RAM_BASE` is wrong, and the code comment depends on that clause.**
   - Where: T:2508 (the argument), P:317 (the code comment "no wrap (sh >= SWW_RAM_BASE)").
   - [src] The claim "ri >= SWW_RAM_BASE and ri <= sh - 0x9C imply it" fails for sh < 0x9C. There `sh - 0x9C` wraps to about 0xFFFFFF7x, so any RAM ri passes.
   - [obs] The mutant is still equivalent, for a different reason: the first shared entry, `sw_g2_tcm(sh + 0x50)` with sh + 0x50 < 0x200000, is refused by `bp < SWW_RAM_BASE`. My temporary run with the mutant and an added `sh = 0x10` case printed `PASS bad 0`.
   - Failure scenario: someone trusts the comment and drops the clause as redundant. The wrap guard at P:317 then rests only on `sw_g2_tcm`, and the comment "no wrap" is false.
   - Fix: reword T:2508 to "`sh < SWW_RAM_BASE`: for 0x9C <= sh < SWW_RAM_BASE implied by the ri bounds; for sh < 0x9C sh - 0x9C wraps, but sw_g2_tcm refuses sh + 0x34 < SWW_RAM_BASE". Keep the clause.
2. **LOW: the list of equivalent mutants at T:2508 is incomplete.**
   - These clauses have no mutant and are not named. Each is equivalent [src]:
     - **rm read result:** a failed read leaves rm = 0, and `rm != ri + 0x4C` refuses; a failure is possible only for a misaligned ri.
     - **ra[k] read results:** they cannot fail once the ri read succeeded (same window 0, aligned, ri + 0x10 < 0x400000). Even if one did fail, ra = 0 is refused by `sw_g2_tcm` (below RAM).
     - **`bad ||` prefixes and `&& !bad`:** they only skip reads or checks once bad = 1 is already set.
     - **`rc < 0` / `ep < 0`:** unreachable, since 45 entries come before them and SW_G2_MAX is 56.
   - Failure scenario: none at run time. A later reviewer cannot tell "considered and equivalent" from "missed" (AP-26 says to name it in the comment).
   - Fix: append these four to the T:2508 comment.
3. **LOW: an alias refusal also prints a second, false gap** (2c7d469 code, never reviewed by r2).
   - Where: P:513 and P:519.
   - [src] When `sw_g2_alias_ok` fails, `hw.g2ok = 0`. Then the following `if (!hw.g2ok)` also runs and emits `swl_gap("g2", "not armed: G1 block or DBI map failed")` plus "G2 not armed (G1 block or DBI map failed)".
   - Failure scenario: the most likely refusal on this run (see the note below) is reported with two gaps, `gaps` is inflated by one, and one of the two lines blames the G1 block or the DBI map, which did not fail. Rule 8 wants loud lines, not wrong ones.
   - Fix: test the map/G1 failure first, and run the alias check only in its `else`:
     ```c
     if (!hw.g2ok) { /* map/G1 gap + printf */ } else if (!sw_g2_alias_ok(...)) { hw.g2ok = 0; /* alias gap + printf */ } else { /* armed printf */ }
     ```
4. **LOW (run-plan note, not code)** [guess]: G2 arms only when pa lies in [0x8822A0000, 0x886000000).
   - [src board.json xnu_ram] XNU RAM starts at 0x880000000. [guess] For the low 32 bits of the block to fall in [0x822A0000, 0x86000000), pa must lie in [0x8822A0000, 0x886000000): about 61 MiB near the start of XNU RAM. A 0x9xxxxxxxx pa can only alias there above 0x980000000, which is past the end of xnu_ram.
   - The G1 run's 0x884e84000 was inside it [obs r2]. A fresh allocation may not be. That fails closed (safe), but the run may end with "G2 not armed".
   - Fix: make sure the run plan's `--expect` and its predictions name that outcome as a possible, non-failing result. Then a refused arm is read as "check worked", not as a G2 failure.

## Not checked

- r2 F2's link-down sweep cases and the two new glue mutants (2c7d469): not rerun (brief: not the full suite).
- R94/R106 spec values behind the literals: checked by r1/r2, not again here.
- Gate, build.sh, `tools/gate-xnu-build` on bb6ffc8: left to the driver.
