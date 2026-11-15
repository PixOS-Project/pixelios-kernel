# 0174 G2 HW-WRITE review, round 1 (Opus 5.5, 2026-10-28)

Subject: `wip/w4-g2` at c0bd4c7, `xnu/patches/0174-sweet-wlan-g2-first-dma.patch` (P:line = patch line) and `shim/sweet_wlan_g2.h` (G:line; identical to the patch's new file, `diff -q` empty [obs]), `tools/mkadt.py` diff.
Final headers were extracted with the mutant runner's own chain (`final_header()`, `chip_header()`, `glue_after()` of tools/test_wlan_wr_mutants.py) into /tmp/g2r1/h; W:line = patched `sweet_wlan_wr.h`, C:line = patched `sweet_wlan.c` there.
Host test: `gcc -O1 -Wall -Wextra -Werror` of shim/wlan_g2_host_test.c against those headers: `RESULT: PASS wlan_g2_host_test` [obs, 2026-10-28]. Full mutant suite and gate not rerun (brief). No shell files in the diff (shellcheck n/a).
Tags: [src] read in the cited file this session; [obs] observed here or on hardware as cited; [guess].

## Verdict: SAFE WITH CONDITIONS

No path was found by which a store reaches hardware without being admitted by the glue's list `g2l[]` (address, value under vmask, old value under omask read through the same pointer, ONCE, need) [src P:94-122, P:132-156]. Every table row is also a header row of `swg_g2[]`. Exceptions that are glue-list-only (not table rows) are the window selects (finding 6). Conditions before the run: fix or explicitly accept findings 1 and 2, and settle finding 3 from the memory map (a read, no phone cycle). Findings 4-10 are LOW. Fix them in the same patch if cheap.

## Findings

1. **MED: the EP Command undo (and every G2 config/BAR store) has no link check, unlike swl_restore.** P:94-122 (sw3_g2_ok), G:322-333 (swg_undo).
   [src] swl_restore only makes config stores "while the link is not known to be down" (W:820-845, review 0133 F4). It checks the RC-local ISO/LTSSM/ATU state first through swl_wpre (W:746-768). W17 calls swl_wpre before every poll read (W:2167). G2 makes about 45 TLP-generating stores and a 2 s poll without one RC-local link check. On a stop with code DEAD (G:379-380 or G:507-508, all-ones read), swg_undo still issues the EP Command CfgWr 0x0002 if the EP row was tried (G:328). sw3_g2_ok admits it: entry `need ep`, omask 0, any old [src P:323-324]. That is a non-posted config write to a link known to be down, the case review 0133 F4 excluded. The window-restore stores are refused on a dead link, but only indirectly: swl_pre_ep sees BAR0 = all-ones [src P:106]. The EP Command store gets no such check.
   Failure scenario [guess]: the link drops during the poll. The flags read returns 0xFFFFFFFF, G2 stops DEAD, and the undo sends CfgWr to the dead link. On Exynos DWC that can end in a completion timeout or SError.
   Fix: in sw3_g2_ok, before `old = *p` and for every space except SW3_DBI, require the RC-local link state: `h->iso[0] & 1` and `(h->elbi[0x2C8/4] & 0x3F) == 0x11`. These are reads of RC registers and generate no TLP, the same checks as swl_pre_link/swl_wpre. Refuse loud with `swl_gap("g2", "glue: link not up, store refused", "high")`. The EP undo then fails loud on a dead link, which matches swl_restore's "skipped (precondition)". Add one host-test case (dead link, EP tried: no EP store, `undo FAILED` line) and one glue mutant.

2. **MED: no overlap check between the index-array pointers and the structures G2 already stored, and no verify pass before bus mastering is turned on.** G:470-475 (swg_entry, ra[] only bounded to RAM), P:313-317 (sw_g2_list slot entries), G:371-399 (swg_st).
   [src] `ra[k]` comes from the running firmware (ri + 0x04..0x10). It is checked only for "RAM word with room for 4 slots" (G:472) and RAM bounds in the glue (P:276). Nothing checks that `[ra[k], ra[k]+16)` does not overlap the shared words G2 stores (sh+0x34..0x5C), ring_info +0x14..0x33 or ring_mem (rm..rm+0x50). The slot rows run after the ring base rows (G:137-141: base, max, wr slot, rd slot). If ra[k] aliased a ring-base word, the slot RMW would zero the low half of a host PA that had already been stored and read back. Both lists would accept it: the glue's slot entry is vmask 0xFFFF, omask 0 [src P:315], and the header's old rule is only `!= 0xFFFFFFFF` [src G:294-295]. The dongle would then DMA to `pa & ~0xFFFF`, outside the G1 block, into host RAM. Nothing re-reads the earlier words before RC/EP bus mastering (G:157-158 rows run directly after SCRLEN). The glue list also does not repeat the header's structure checks (ri < sh, rm == ri + 0x4C, counts) [src P:292-293 vs G:450-460].
   Likelihood [guess]: low. It needs the firmware to hand an aliased pointer, and the F3 values were sane (R89 s.1). The consequence is host-RAM corruption, and the fix is reads only.
   Fix (either one; both is best): (a) in swg_entry and sw_g2_list, refuse unless each `[ra[k], ra[k]+16)` is disjoint from `[sh, sh+0x60)`, `[ri, ri+0x4C)` and `[rm, rm+0x50)`; the glue also repeats `ri < sh` and `rm == ri + 0x4C`. (b) Before row SWG_ST_RC, add a reads-only pass that re-reads every TCM word stored so far and requires equality with the value written (stop OLD/READBACK, then undo). Add mutants for both.

3. **MED (run condition, [guess]): hi-dword host PAs.** P:302-319, G:239-243, R91 am.8, R100 F7/U-N1/U-N2, R106 s.3.6.
   [src] G1 ran at PA 0x884e84000 (hi 8) [obs wlan-runG1-0167, R91:17]. Every golden PA is below 4 GiB (R100 F7). If any agent on the path (dongle firmware sh_addr_t handling, dongle PCIe DMA engine, inbound path) drops the hi dword, the dongle's first DMA (index updates, trap data, completions) lands at `pa & 0xFFFFFFFF` = 0x84e84000.. in the low DRAM bank. R100 U-N2 records that bank's owner as unknown: shim, kernelcache, TZ carve-outs? A write there corrupts memory silently. It is not caught by any read-back, because the G2 read-backs check the TCM words, not where the DMA went.
   Condition: before run G2, read the generated ADT `/chosen/memory-map` / the shim's memory layout from the G1 run dir (host-only). Record in `docs/runplans/wlan-runG2-0174.md` whether `[block_pa & 0xFFFFFFFF, +total)` is unused, XNU-free memory or live data. If it is live data, or EL3/TZ-owned, do not run G2 with hi != 0. Allocate below 4 GiB (U-N2) or accept the risk in writing (driver decision, logged in HANDOFF). Optional cheap telemetry: after the poll, read 16 B at the expected d2h_w index array PA. This is already in the G1 block; it needs no new store.

4. **LOW: sw3_g2_ok reads the old value before the live-window check (open point B-a).** P:111 (`old = *p`) vs P:148 (window check at `put`).
   [src] For SWW_BAR2 the pointer is `&tcm[off/4]` under whatever cfg 0x84 is live. If the header's `rt.win` and the live window disagree (live TAIL, entry window 0), the read goes to backplane `0x400000 + off`, with off >= 0x200000. That is 0x600000..0x7FFFFC, above the RAM maximum 0x600000 (SWW_RAM_BASE + SWW_RAM_MAX, W:92-93). An unknown backplane region [guess: may hang the backplane or return an error completion].
   Reachability with the current header [src]: not reachable. Every TCM store is preceded by swg_rd of the same bp (G:375, then G:387). swg_rd goes through sw3_rd, which refuses on a live-window mismatch (C:265-276), so a divergence stops before the glue's old read. For BAR0 the adapter moves the window first (P:383-384), and the only BAR0 entry is PCIE +0x144. So this is defence in depth against a header defect only.
   Fix: in sw3_g2_ok, add `(space == SWW_BAR0 && h->cfg[SWW_CFG_WIN/4] != win) || (space == SWW_BAR2 && h->cfg[SWW_CFG_WIN2/4] != win)` to the refusal before `old = *p`, with a loud swl_gap. Add one glue mutant.

5. **LOW: `g2ep` copies the live EP Command after any EP-space store, so the Command half of the window-move precondition becomes a tautology.** P:377-380.
   [src] `h->g2ep = h->cfg[0x04/4] & 0xFFFF` runs whatever the store did, even when sw3_wr refused it. sw3_g2_ok then checks `swl_pre_ep(live cmd, ..., g2ep)` (P:106). That is "the Command equals what it was after the last EP-space store", not "the Command is the value G2 set". Example: the EP store 0x6 reads back 0x0004 (MEM off). The header stops on the read-back, but later window moves still pass the Command check. The only remaining guard is the BAR0 = 0x60000004 part. Impact is limited to config-space window stores, and W17's swl_restore applies its own 0x2/swl_cmd_mem rule afterwards (W:841-845).
   Fix: set g2ep to `v` only when `rc == 0 && (live & 0xFFFF) == v && (v == SW_G2_EP_MEM || v == SW_G2_EP_DMA)`, else 0xFFFFFFFF (all window moves refused, loud).

6. **LOW: window selects are stores that are not swg_g2[] rows, are not counted, and are not logged; one header comment is false (part of answer A, open point B-c).** G:92, G:182, P:299-300, P:337-344, P:433.
   [src] cfg 0x84 := 0/TAIL comes from swg_win (G:182), which calls `x->b->wr` directly, although G:92 says "called only by swg_put()". cfg 0x80 := SWW_WIN_PCIE (sw_g2_bar0, also during swg_entry for the SRPWR read, so "reads only" at G:416 is not exact) and cfg 0x80 := w80 (P:433) are made by the glue itself. None of these is counted in `nstore` or printed when it succeeds. They are bounded: four fixed values (w80 = the live value at hook entry), and the pre_ep check gates them (P:106). Not ONCE does not open an unaudited store: TCM/BAR0 entries still need the live window equal to the entry window at put (P:148). So B-c is answered no, apart from finding 4's read.
   Fix: correct G:92 and G:416; print every successful G2 window move (`G2 window cfg %#x := %#x`), or count them in a separate `nwin`. The run plan's `stores=45` stays valid.

7. **LOW: unsigned wrap in sw_g2_list's bounds.** P:292-293.
   [src] `sh + 0x60u > SWW_WIN_TAIL`, `ri + 0x4Cu > ...` and `rm + 0x50u > ...` wrap for values >= 0xFFFFFFA0/B4/B0, for example ri = 0xFFFFFFFF on a dead link. Today the next step catches it (sw3_ptr off >= 4 MiB gives NULL; sw_g2_tcm's bp check), so there is no store. It is fragile.
   Fix: write `ri > SWW_WIN_TAIL - 0x4Cu`, and likewise for sh and rm. Add a glue-sweep case with ri = 0xFFFFFFF0.

8. **LOW: the only typed dongle address is the PCIe core page SWW_WIN_PCIE 0x18001000 (open question F).** W:109, P:325, G:36-37.
   [src] It is used for the SRPWR read (+0x1E8) and hostready (+0x144). It was pinned from run w3a-n33's EROM decode [obs per W:109], and stage 3 already uses it, so it is not new to G2. All other addresses come from reads: sh is W17's poll; ri, rm and ra come from TCM; host PAs come from the glue's G1 plan copy (P:302-319, P:422-429), and no host PA is typed. R91 am.8 / R107 #5 are met for the shared-structure stores.
   Optional: compare SWW_WIN_PCIE with the stage-1 EROM walk result at hook entry, if the glue keeps it.

9. **LOW: R106 s.3.2 and s.3.3 disagree, and the code follows s.3.3 (open point B-b).** R106:16 says a non-sticking RC Command "aborts before any EP Command / ring store"; R106:17 orders rings -> RC -> EP. The code stores the rings first (M2, G:157) and stops on RCSTUCK before the EP row (G:394-395). That is safe: the ring words are TCM only, and bus mastering stays off.
   B-b itself is correct [src]: the RC entry is v 0x0507, vmask all, old 0 under omask 0xFFFF (P:321), as R106 s.3.4 says (mask 0x0000FFFF == 0, H1b observed 0x00100000). The Status half is written as 0 (W1C, R106 s.3.1). The undo writes the glue's own `rcold & 0xFFFF` (P:322) and the header's own `rc_old & 0xFFFF` (G:326). These are computed independently and are equal by the entry rule.
   Fix: amend R106 s.3.2 to "aborts before the EP Command store".

10. **LOW: index-slot RMW is not atomic against the running CA7.** G:275, P:315, R94 step 9, D-9.
   [src] The header reads old, the glue reads old again (P:111) and checks the kept upper half, then stores. A dongle update to the same 32-bit index between the glue read and the store would be lost. Android's 16-bit store cannot lose the upper half. [guess] Before hostready the firmware does not touch these slots. Already covered by the D-9 WIFI GAP line. Document only, no code change.

## Answers to the brief's questions

- **A.** No store reaches hardware unless it is a g2l[] entry. While `h->g2` is 1, sw3_wr takes nothing else (P:132-138, P:146-156). The stores and their callers:
  - Table rows: TCM through sw_g2_wr. The RC DBI row is P:321. The EP rows are P:323-324.
  - Undo stores: list entries with `need` (P:322, P:324).
  - Window selects: list entries P:299-300. They are not swg_g2[] rows (finding 6).
  - No other store path runs during G2. sw_rd's direct cfg 0x80 store (C:96-109) is a stage-1 path and is not reachable from the G2 adapter, which uses sw3_rd/sw3_wr only (P:347-390).
  - No list entry names ATU, ELBI or ISO.
  - Residual gaps: findings 1 (link), 2 (aliasing) and 4 (old read before the window check).
- **B-a** finding 4 (wrong-window read possible only after a header defect; harmful [guess], since backplane 0x600000+ is above RAM). **B-b** correct (finding 9). **B-c** no unaudited store (finding 6). **B-d** acceptable under rule 8:
  - Every G2 failure path prints a swl_gap line (counted into the WIFI GAP summary/gaps) and the `G2 RESULT STOPPED` line (P:439).
  - The W17 hook line prints the return value (P:558).
  - The run plan `--expect`s `G2 RESULT done ... undo=ok` (runplans/wlan-runG2-0174.md:48), so a stopped G2 fails the run summary even though stage 3's result is unchanged.
  - The not-armed and list-refused paths are loud (P:415-416, P:481-482, P:553-555).

  **B-e** checked in code, twice: W17 calls the hook only with `f2 & SWW_F2_LOW` (P:553). Every list read is `sw3_rd(SWW_BAR2, 0, ...)`, which refuses unless live cfg 0x84 == 0 (C:270), so a non-zero window refuses the list. swg_go's `rt.win = 0` (G:542) is an assumption, but every header TCM read and store is checked against the live window by the glue (C:270, P:148), except the old read of finding 4.
- **C.** The order matches R94 s.2 [src G:125-160 vs R94:12-23]:
  - Steps 1-3: flags2, host_cap, trap.
  - Steps 4-7: d2h_w (R+0x24), h2d_r (+0x1C), h2d_w (+0x14), d2h_r (+0x2C). The arg mapping G:146-149 was checked against SWD_IDX order (dma.h:32-35) and the glue's `ri + 0x14 + 8k` (P:304-307).
  - Steps 8-9: per ring, base, then max|len (one 32-bit word, M5/D-9), then the WR slot and the RD slot. Rings 0-1 use the h2d arrays at slots 0 and 1; rings 2-4 use the d2h arrays at slots 0-2.
  - Steps 10-11: scratch address, then length.
  - Then RC 0x0507, then EP 0x2->0x6 (R106 s.3.3, SPEC C5), then hostready (step 12).

  Ring depths 128/2048/64/2048/1024 match R94 step 8 (dma.h:58-64). Values match R94: flags2 0xC0943041, host_cap 0x10823407, scratch length 8, hostready 0x12345678.

  Timing against swl_pre_ep:
  - The SRPWR read is at entry, before any store, with EP = 0x2 (G:424-425).
  - After EP = 0x6, the only window move is the hostready BAR0 select, under want = g2ep = 6 (P:106, P:384). Reads through sw3_rd make no pre_ep call.
  - After the undo, g2ep = 2, so the final 0x84/0x80 restores and W17's swl_wpre (want 0x2) pass.
  - If the EP undo fails, swl_restore's swl_cmd_mem path still re-halts the CA7, loud (W:841-845). The run plan has `--fail-on restore: EP command changed` (runplan:57).
- **D.** Every exit path undoes the Command stores and prints a gap line:
  - Entry refusal: nothing tried, undo prints "not stored" (G:426-487).
  - Mid-row stop, poll timeout (medium gap, then undo, G:531-553), trap/assert (G:510-513) and undo failure (G:329-336, restore=0 printed): all run swg_undo, which keys on `tried`.
  - Window: restored at G:559-561 and P:433-437, loud on failure.
  - The CA7 re-halt and cfg 0x84 := 0 follow in swl_restore whatever G2 returned (P:552-561).
  - Gap: the dead-link undo (finding 1).
- **E.** Levels <= 11 are unchanged:
  - `hw = { 0 }` (C:731), so g2ok = 0 and `x.g2 = NULL` (P:173). W17 skips the hook, and the only side effect is `g2rc = 1`.
  - Level 11 still runs G1 after stage 3 with the same output (P:488-490).
  - Level > 12 is refused (P:449). Level 12 requires the ADT `dbi` == 0x14800000+0x1000 (P:457, sw_prop_is C:134-147, loud on mismatch).
  - mkadt emits `dbi` from soc.json wlan.dbi = "0x14800000" (soc.json wlan.dbi) with a fixed length of 0x1000.
  - A rev downgrade below 12 leaves g2ok = 0 (C:818-827).
- **F.** No typed host address and no typed dongle RAM address. One typed dongle core page (finding 8).

## Not checked
- Glue mutants and G2_MUTANTS were not rerun (the brief says the driver runs them). Their kill status for findings 1, 2, 4, 5 and 7 is moot, because those checks do not exist yet.
- On-hardware behaviour of a CfgWr to a dead link on gs101, dongle hi-dword DMA handling, and backplane reads above 0x600000: all [guess] as tagged.
