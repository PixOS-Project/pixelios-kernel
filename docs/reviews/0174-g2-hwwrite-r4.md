# 0174 G2 HW-WRITE review, round 4 (Opus 5.5, 2026-10-29), goal W-D3c

**Verdict: SAFE WITH CONDITIONS.** No HIGH or MED findings. Every store is still decided by `sw3_g2_ok`'s exact-word list. During the list build the list holds only the four window entries. cfg 0x84 is back at 0 before `swg_go` and on every refusal path that can still store. The conditions are three LOWs: one refusal is logged under the wrong clause (a rule 8 accuracy issue), the equivalence notes in the test are stale and incomplete, and the brief's verification command does not run the glue harness at all.

## Subject

- Commit `898d531` on `wip/w4-g2` (parent: the r3-reviewed tree).
  - `xnu/patches/0174-sweet-wlan-g2-first-dma.patch`: `sw_g2_win2`, `sw_g2_rdw`, `sw_g2_list` rework, `sw_g2_hook` restructure.
  - `shim/sweet_wlan_g2.h`: `swg_entry` drops the `ri >= sh` stop and adds `swg_apart` plus a last-ring_mem-word check.
  - `shim/wlan_g2_host_test.c`: ri > sh layouts.
  - `tools/test_wlan_wr_mutants.py`: G2L harness with window emulation, plus new mutants.
- How lines are cited:
  - P:line = line in the patch file.
  - H:line = line in `shim/sweet_wlan_g2.h`.
  - W:line = line in `shim/out/sweet_wlan_wr.h` (the generated final header).
  - T:line = line in `tools/test_wlan_wr_mutants.py`.

## Runs this session [obs, 2026-10-29]

- `python3 -B tools/test_wlan_wr_mutants.py --g2` gave `RESULT: PASS g2 mutants 127/127 killed`.
- `python3 -B tools/test_wlan_wr_mutants.py WlanWrMutants.test_g2_list_harness_passes WlanWrMutants.test_g2_list_mutants_are_killed` gave `Ran 2 tests in 27.495s`, `OK`. This is the G2L glue harness; `--g2` does not run it (finding 3).
- Hand mutants and probes, from a scratch script in /tmp that imports the module and its `run_g2_list()` / harness text. Nothing in the repo was written.

  | Mutant or probe | Result | Meaning |
  |---|---|---|
  | `sw_g2_rdw` reads offset `bp & 0x1FFFFF` instead of `bp - win` (wrong-window read) | fail | killed |
  | `sw_g2_rdw` with every bound clause removed | fail | killed |
  | `sw_g2_rdw` with only `bp < SWW_RAM_BASE` removed | pass | survived: equivalent, the callers check first (finding 2) |
  | `sw_g2_win2` read-back `\|\| h->cfg[0x84/4] != win` removed | pass | survived (finding 2) |
  | Added case: index array 0 at 0x300002, unaligned | refused, but as `G2 glue list refused (list full): ... (47 entries)` | finding 1 |

- No shell files in the diff (shellcheck: not applicable).
- Not run, per the brief: full suite, build, gate.

## 1. cfg 0x84 moves during the list build

- **Only window stores can be taken** [src P:342-346, P:333-427]. The first four `sw_g2_add` calls put in the only entries that exist before the reads: cfg 0x80 := SWW_WIN_PCIE / w80, and cfg 0x84 := 0 / SWW_WIN_TAIL.
  - The TCM, DBI, EP and BAR0 entries are added only after `sw_g2_win2(h, 0u)` (P:388-391), and only if `why == NULL`.
  - The list build itself calls `sw3_wr` only through `sw_g2_win2`, which stores only to cfg 0x84 (P:314).
  - The harness proves this for every case: `ndsb != nwin` means "a store that is not a window change" (T:~2462).
- **The move is gated before it happens** [src P:94-133]. While `h->g2 = 1`, `sw3_wr` goes to `sw3_g2_ok`, which checks, in order:
  1. l3 level, g2ok, pointer and live window;
  2. `swl_pre_link`;
  3. `swl_pre_ep(..., h->g2ep)`, with g2ep = 0x0002 set at P:515;
  4. a list match.

  Test evidence [obs]: the harness cases "window move refused" (EP Command 0x0006) and "link down" refuse with an empty list and cfg 0x84 = 0.
- **cfg 0x84 is 0 when `swg_go` starts** [src P:388-391]. `sw_g2_win2(h, 0)` runs on both the built and the refused path. It returns nonzero unless the live cfg 0x84 reads back 0 (P:314), and nonzero sets `why`, so the list is refused. `swg_go`'s `x->rt.win = 0` (H:564) therefore holds on the only path that reaches it.
  - Defence in depth: even if the read-back lied, `sw3_rd` (patch 0129:93) and `sw3_g2_ok` compare the live cfg 0x84 with the entry's window. A stale window refuses the access; it cannot redirect it.
- **The refusal paths** [src]:
  - "shared range" refuses before any read, so there is no move.
  - Read refusals after a move put the window back. The "restore refused" harness case shows that a failed put-back is loud (`glue: cfg 0x84 not put back to 0`) and still refuses.
  - In that one case cfg 0x84 is left at 0x400000 and the hook returns 1.
- **W17's restore still covers that case** [src W:2112, W:2218-2226, W:415, W:832-835]. `SWW_RG_W84` is armed by `SWT_WIN2_TAIL_E` (W:368) and stays armed through W17, because only `swl_done` clears `armed` (W:1014-1016). `swl_restore(x)` runs after the hook whatever it returned, with `h->g2 = 0` (P:541), so the normal `sw3_wr` path stores cfg 0x84 := 0.
  - On a dead link that restore is skipped (W:842-846). r3 already had this same residual for `swg_go`'s own window restore; it is not new.

## 2. Bounds, wrap, overlap, straddling

- **No u32 wrap** [src P:349, 355, 364, 371]:
  - `end < SWW_RAM_BASE + 0x9C` is tested before `end - 0x60` / `end - 0x9C` / `end - 16`. A wrapped `SWW_RAM_BASE + ram_bytes` is always < SWW_RAM_BASE, so it is refused too.
  - With `sh <= end - 0x60`, `ri <= end - 0x9C` and `ra <= end - 16`, every `x + size` used in the overlap tests is <= end, which is about 0x4A0000. Nothing wraps.
  - The r2 F3 wrap case ri = 0xFFFFFFF0 is still refused ("ring_info range").
- **Off-by-4** [src]:
  - The list writes up to sh+0x5C, ri+0x33, rm+0x4F and ra+0x0B. The bounds `sh + 0x60 <= end`, `ri + 0x9C <= end` (rm + 0x50 = ri + 0x9C) and `ra + 16 <= end` are tight.
  - The mutants "top bound off by 4" and "too tight by 4" for sh and ri are in G2L_MUTANTS and are killed (T:2709-2720) [obs: unittest OK].
- **Overlap** [src P:364, 376-385]:
  - [ri, ri+0x9C) vs [sh, sh+0x60): both orders are accepted, and touching edges too.
  - Each array [ra, ra+16) is checked against shared, ring_info [ri, +0x4C), ring_mem [rm, +0x50) and every other array.
  - Every list TCM word lies inside its own block (shared +0x34..+0x5C, ring_info +0x14..+0x33, ring_mem +0x04..+0x4F, slots ra+0..+0x0B). So no entry names another entry's word.
- **Every list word is RAM** [src P:291]: `sw_g2_tcm` refuses unaligned addresses, `bp < SWW_RAM_BASE` and `bp > end - 4`.
- **One physical word, one entry** [src P:289-295]: the per-word (window, offset) mapping is a bijection on [0x200000, 0x800000), so the same physical word cannot appear twice in different windows.
- **A straddling block cannot be stored or read through the wrong window** [src P:289, 321-329, 447-485, H:173-196, H:379-394]:
  - Both the list (`sw_g2_tcm`, `sw_g2_rdw`) and the header (`swg_win` before every `swg_rd`) pick the window per 32-bit word.
  - The header's store at `swg_st` comes after `swg_rd(bp)` has selected bp's window.
  - `sw3_g2_ok` refuses a BAR2 store whose entry window is not the live cfg 0x84.
  - Harness [obs]: "shared straddles 0x400000", "ring_info block straddles", "ring_info reads straddle" each expect exactly the moves tail and back to 0.
- **Cites checked** against $OFFLOAD_HOME/pixelios-artifacts/refs/wlan-stage5/dhd_pcie.c [src]:
  - 6154-6155 is `DHD_BUS_BAR1_BPWIN` (addr & ~(bar1_size - 1)).
  - 6232-6241: per access, the window moves only when it changes, and the result is `offset - bpwin`.
  - 13602: `shaddr = dongle_ram_base + ramsize - 4`.
  - 13684-13685: the shared check is only `addr < ram_base || addr > shaddr`.
  - 13872: rings_info_ptr is checked only against [ram_base, shaddr].
  - 14141: `ringmem_ptr` is taken as is, which supports the comment calling "ring_mem right after ring_info" a project rule, not a DHD rule.

## 3. Header `swg_entry`

- **The `ri >= sh` stop is gone, and nothing later depends on it** [src H:423-512, H:518-556, H:220-243]:
  - `swg_addr` takes ri, rm and ra from the entry state.
  - `swg_poll` reads sh+0x00, sh+0x2C and rm + 16i, each through `swg_rd` (per-word window).
  - The index-array overlap test (H:484-495) already compared ra against [ri, +0x9C) and [sh, +0x60) independently of their order.
  - The old "ring_mem must read 0, so overlap with shared is caught" argument is replaced by an explicit `swg_apart(ri, 0x9C, sh, 0x60)` (H:463). Its sizes are tight: the mutants "4 bytes short" on either side are killed.
- **No wrap in that test**: `ri + 0x48` was read as a RAM word just before it (H:458), so `ri + 0x9C <= end + 0x50`.
- **The last-ring_mem-word check (H:467) is redundant but harmless.** The loop that follows reads every ring_mem word through `swg_rd`. Its mutants are killed through the `fail` text (127/127).
- **The list and the header stay consistent**:
  - The header accepts index arrays with room for 3 slots (ra + 12); the list wants 16 bytes. The list is stricter, so any header address the list does not name is refused by `sw3_g2_ok`.
  - The dongle could change a pointer between the two reads (the CA7 runs). The header would then compute other addresses, which are not in the list, so they are refused and `swg_stop` undoes.

## 4. Hook restructure

- **Exactly one `h->g2 = 1` and one `h->g2 = 0`, with no early return between them** [src P:497-545]. The `!h->g2ok` return (P:509-513) comes before `h->g2 = 1`. The structural test (T:420-430) pins the call sites: `sw_g2_list` 2x, `sw_g2_win2` 3x, `sw_g2_rdw` 4x.
- **The BAR0 window is put back on every path** [src P:536-540]:
  - On the refused path cfg 0x80 never moved: no list code stores 0x80, and `sw_g2_bar0` is reached only from `swg_go`.
  - If it had moved, the put-back would use the list's own 0x80 := w80 entry (once = 0). On a refused list g2n = 0, so that store would be refused loudly. It is unreachable today.
- **Return codes are unchanged for W17** (W:2223): not armed returns 1, refused returns 1, otherwise `swg_go`'s rc, with the BAR0 put-back failure folded in as before.
- **Refusal is loud** (rule 8): `swl_gap` plus `G2 glue list refused (<clause>)`, then the hook's `G2 RESULT not run (glue list refused)`. Run plan `wlan-runG2-0174.md:59` `--expect-absent` still matches both strings. The 49-entry success line (row 3) is unchanged: same entries, same order.

## 5. Tests

- **The window emulation is real** [src T:2296-2320]. `back[]` holds a value unique to each word index. `dsb_hook` re-points `hw.tcm` to `back + cfg[0x84]` after each store and logs every change.
  - A read with the right window but the wrong offset returns another word, so it is killed [obs, hand mutant].
  - A read through window 0 at an offset >= 4 MiB is refused by `sw3_ptr`, so it is killed ("rdw: always window 0").
  - A read with a window that does not match the live cfg 0x84 is refused by `sw3_rd`.
- **A missing window restore is caught.** `one()` fails when cfg 0x84 != 0 on return (T:~2475), and the mutants "window not put back", "... on a refusal" and "restore failure ignored" are killed (T:2705-2707).
- The gaps are under findings 2 and 3.

## Findings

1. **LOW: an unaligned index-array pointer is refused under the wrong clause.**
   - Where: P:371.
   - [src] The index-array range clause checks `ra[k] < SWW_RAM_BASE || ra[k] > end - 16u`, but not `(ra[k] & 3u)`. An unaligned ra passes it and the overlap checks. The list is then refused only when `sw_g2_tcm` rejects the first slot address, which sets `bad`, which sets `why = "list full"` (P:419).
   - [obs] Probe ra[0] = 0x300002 printed `G2 glue list refused (list full): ... (47 entries)`.
   - Fails closed, with no store. But rule 8 and this commit's claim ("refusal names its clause") want the true reason. A field report of "list full" would send a debugger to SW_G2_MAX.
   - Fix: at P:371 use `} else if ((ra[k] & 3u) != 0 || ra[k] < SWW_RAM_BASE || ra[k] > end - 16u) {`. Add `REF("index array not aligned", "index array range", SH, RI, RI + 0x4Cu, RAS(0x300002u, 0x207f40u, 0x3e7d80u, 0x47cf40u), RC, END);` to G2L_HARNESS, and a G2L mutant that drops the new clause.

2. **LOW: the equivalent-mutant notes are stale and incomplete.**
   - Where: T:2673-2678.
   - Stale [src]:
     - "for sh >= 0x9C, ri >= SWW_RAM_BASE and ri <= sh - 0x9C imply it" no longer holds: the bound is now `ri <= end - 0x9C`, and `sh < SWW_RAM_BASE` has its own mutant.
     - "sw3_rd cannot fail for a BAR2 offset < SWW_TCM_BYTES with window 0" no longer holds: reads now move windows, and a read can fail with -2.
   - Not named:
     - `sw_g2_rdw`'s own bound clauses at P:323 are implied by the caller checks (sh range, ri range; ri+4..+0x10 < ri+0x9C). [obs] "rdw: no RAM base" survived.
     - The `sw_g2_win2` read-back at P:314 survived [obs]: the harness cfg store always takes.
   - Fix:
     - Rewrite the comment for the W-D3c bounds.
     - Name the `sw_g2_rdw` clauses as equivalent, kept as defence in depth.
     - For the read-back, add a harness knob (e.g. `static int stuckwin;` which, when set, makes `dsb_hook` write `cfg[0x84/4] = lastwin` back before re-pointing) and a case "window store does not take: refused (window move)". That kills the read-back mutant. Otherwise name it as equivalent and cite the `sw3_rd`/`sw3_g2_ok` live-window checks as the reason.

3. **LOW: the brief's verification command does not exercise the code under review.**
   - Where: T:3222-3231.
   - [src] `--g2` runs only `g2_kill_test()` over `G2_MUTANTS`: the pure header, 127 mutants. The G2L glue harness and `G2L_MUTANTS`, where most of this commit's safety logic lives (window moves, bounds, overlap, refusal clauses), run only as `WlanWrMutants.test_g2_list_harness_passes` / `test_g2_list_mutants_are_killed`.
   - The commit message's "--g2 127/127 killed" is therefore no evidence for the glue. Its "full mutants OK" is.
   - Fix: either make `--g2` also run `run_g2_list` (base must pass, every G2L mutant must fail) and print a combined `RESULT`, or have the run plan and the next brief name both commands. Until then, quote the unittest `OK` line for the glue.

4. **INFO (not new): the dead-link residual.**
   - Where: P:388-391, W:842-846.
   - If the link drops after a move to 0x400000, both G2's put-back and W17's restore refuse, and cfg 0x84 stays at 0x400000. This is loud on both sides. A dead link makes the stale window moot for writes, because every later store needs `swl_pre_link`.
   - No change asked.

## Not checked

- Research W-D3c s.2/s.5: I checked only the DHD line cites that the code relies on (section 2 above), not every claim in it.
- Host test `shim/wlan_g2_host_test.c` ri > sh cases: run indirectly (`--g2` baseline passed); not traced case by case.
- Full suite, build.sh, gate: left to the driver.

## Driver resolution (G2 thread 9, 2026-10-29)

- LOW 1 fixed: index-array clause now `(ra[k] & 3u) != 0 || ra[k] < SWW_RAM_BASE || ra[k] > end - 16u` (refused as "index array range"); G2L case "index array 1 unaligned (review r4 LOW 1)" (ra 0x300002) + mutant "list: index array alignment unchecked" (killed).
- LOW 2 fixed: equivalent-mutant comment above G2L_MUTANTS rewritten for W-D3c; names sw_g2_rdw's own clauses and sw_g2_win2's read-back with the reason.
- LOW 3 fixed: `--g2` now also runs WlanWrMutants.test_g2_list_harness_passes + test_g2_list_mutants_are_killed. [obs] `RESULT: PASS g2 mutants 127/127 killed, glue list harness + 103 mutants OK`; validate-repo `RESULT: PASS`; check-rules `RESULT: PASS 0 new, 70 baselined`.
- INFO 4: no change (as the review asks).
Conditions met; no further Opus round (cap).
