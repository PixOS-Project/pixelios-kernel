# W3a wlan-link review 8: S27 read-only endpoint config dump + iATU region 1 reset values

Reviewer: Claude Opus 5.5 (adversarial, independent; brief: assume a defect exists). Date 2026-10-23. Target: kernel clone
`wip/w3a-wlan-link`, commit 605bad4 (on f5cfd38). I did not touch the phone, did not switch branches and did not commit.

## What I ran vs only read

- **Ran:** `python3 tools/gen_wlan_seq.py --check` -> `wlan_seq.json is up to date`.
- **Ran:** `python3 tools/test-bluejay-boot.py shim/out/shim-boot-avb.img` -> exit 0. Without the image argument it stops with
  `boot_image required`. `shim/out/gate.log` already has these lines:
  - `PASS wlan_seq table self-test: 66/66`
  - `PASS wlan_seq table audit: sha256 5b671020056a4ad7 ... S00..S27`
  - `PASS wlanlink_host_test27`
- **Ran:** host tests N=26 and N=27, built in a /tmp copy of `shim/` -> both PASS.
- **Ran:** 11 C mutants against the host test (`/tmp/rev8/mut.sh`) and 6 table mutants with the sha pin off (`/tmp/rev8/pm.py`). Results are in check 5.
- **Read:**
  - the whole 605bad4 diff
  - `shim/wlanlink.c:140-453` (unchanged by the commit)
  - `shim/shim.c:130-160` (try_read32 / try_write32)
  - research 32 (super repo `docs/research/32-stage6-bar-assignment.md`) sections 1.1, 2e, 2f, 4 and 6
  - review 7 (super `docs/reviews/W3A-WLAN-LINK-REVIEW-7.md`)
  - every cited source line (listed in check 2)
  - DHD `aiutils.c:580-605, 1455-1468`, `dhd_pcie.c:2158-2170`, `siutils.c:2752-2770`
  - the observed S26 run `logs/probe/20261023T130553Z-w3a-n26_/usb.txt`

## Checks

1. **Every S27 row is a read** (read + ran).
   - **The rows.** The JSON has 43 S27 rows (ids 287..329, `soc/google/gs101/wlan_seq.json`, S27 rows). All are `kind read_only`,
     `mask 0xFFFFFFFF`, `restore null`, `delay_us 0`, `polls 0` and `log true`. In `out/wlan_seq.h` lines 305-347 they are
     `WLK_READ_ONLY` with a = 2.
   - **No write path.** The interpreter's read path (`wlanlink.c:333-341`) calls only `r32` -> `try_read32`. A row with a = 2 never
     sets `probed`. The writer `w32` refuses every non-write kind (`wlanlink.c:266`).
   - **No write would land even if a row became a write.**
     - `w32_allowed` (`wlanlink.c:233-253`) has no CFG clause, so every CFG address is refused.
     - ATU writes go only through `atu_ok` (`wlanlink.c:226-230`), whose offsets are 0x000..0x018 only. Region 1 (+0x200..+0x218) is never writable.
   - **Per-offset side effects** (PCI semantics: a guess from the spec; DHD usage: read from source):
     - BAR0..5, CIS, subsystem, cap pointer, int line (0x10-0x3C): plain config registers with no read side effect.
     - 0x40-0x7C: the capability area. DHD itself reads 0x70/0x74/0x78 (`aiutils.c:1455-1465` reads `PCIE2_BAR0_CORE2_WIN2`).
     - 0x80 and 0x88: DHD reads both at attach (`siutils.c:2757`, `:2765`; `dhd_pcie.c:2165`).
     - 0xAC/0xBC/0xD4: PCIe capability registers (see finding 2).
     - 0x100: AER header. AER status bits are RW1C, so they change only on a write.
     - 0x240-0x24C: L1SS, which the Exynos RC driver itself reads and writes (`rc.h:234-237`).
     - Unimplemented dwords inside the 4 KiB function space read as 0 per PCIe; they are not UR.
     - **Left out on purpose? Yes.** The legacy Broadcom registers 0x8C-0xA8 are not read. This is the important one:
       `PCI_BACKPLANE_DATA` 0xA4 (`include_pcicfg.h:497`) is a backplane access through whatever 0xA0 holds, so reading it is a
       real side effect. `PCI_INT_STATUS` 0x90 and `PCI_TO_SB_MB` 0x98 (`:493`, `:495`) are also skipped. Good, but undocumented (finding 1).
     - ATU region 1 (DBI+0x300200..0x300218): DBI register reads. Region 1 exists in Linux's use (`rc.h:294-300`; programmed at
       `rc.c:1408-1414` per research 32 s.2e). It is inside the 0x1000 ATU window (`wlanlink.c:167`).
   - **Bus abort or hang.** Same envelope as S26, which completed on hardware (w3a-n26: vendor 0x14E4/device 0x4441, status
     0x0010, class 0x028000). A completion error is caught by `try_read32` (`shim.c:138-148`). A completion that never returns
     still means a manual long-press reset, as review 7 check 4 found. Nothing persistent is written.
2. **Offsets and citations** (read). I printed every cited line, and each says what the cite claims:
   - `pci_regs.h:96-101, 115, 116, 122, 125, 210, 232`
   - `pcie-exynos-rc.h:234-237, 239-242, 294-300`
   - `include_pcicfg.h:164, 449, 487, 488, 490, 499, 543, 633-635`

   The generator anchors every cite by regex (`gc` / `anchor` / `find_line`, `gen_wlan_seq.py:49-62, 499-501`), and `--check` is
   clean. Two cites carry meanings that are wrong for this chip (findings 2 and 3). The pci_regs.h cites point at the host's
   `/usr/include` copy (finding 5).
3. **Teardown always runs and restores** (read + ran).
   - **Teardown bound and order.** S27 adds no teardown row. The loop runs every row with `step(requires) <= hi`
     (`wlanlink.c:425`). A run that reaches S27 has hi = 27, so T01 (ATU CR2 := 0, first), T02-T07 (region 0 reset values),
     PERST low and so on all run, with the isolation clear last. The table order is unchanged; host N=27 checks it with
     `teardown_tail_is_table_order(27)`.
   - **CFG window.** It has no enable of its own; T01 closes it.
   - **Fault mid-S27.** The host test injects a fault at CFG+0x100 (`wlanlink_host_test.c:698`) and expects `stopped=fault at=S27`
     and `restore=ok`. The rows before the fault are printed and the rows after it are not.
   - **ATU unreadable.** S25 stops, and S27 never starts.
   - **Mutant.** My C mutant that skips the ATU teardown when hi > 26 is caught by N=27.
   - **Remaining hazard.** A hang inside an S27 read leaves region 0 enabled until the forced reset. This is the same as review 7.
4. **Allowlist independence** (read).
   - The checks are hardcoded in C and do not come from the table: `size_of`, `pa_of` (alignment and bounds), `need_probe`,
     `w32_allowed` and `atu_ok`. The `wlanlink.c` source sha is unchanged, because the commit does not touch the interpreter.
   - **Reads.** Any aligned offset below 0x2000 (CFG) or 0x1000 (ATU) can be read once the block's probe has passed. That covers
     0x240+0xC and 0x200..0x218. There is no per-offset read allowlist; the table audit pins the offsets instead
     (`test-bluejay-boot.py:2805-2810`).
   - **Writes.** No CFG write at all. For ATU, only region 0 at the 12 (offset, value) pairs.
   - **Latitude.** CFG reads at 0x1000..0x1FFC are allowed although the function config space is 4 KiB (finding 4).
5. **Tests catch a write or a moved offset** (ran).
   - **C mutants on `out/wlan_seq.h` / `wlanlink.c`.** Each one makes the N=27 host test abort:
     - M1: CFG 0x10 read -> mmio_write
     - M2: 0x88 -> 0x8C
     - M3: ATU 0x204 -> write 0x80000000
     - M4: ATU 0x200 -> 0x000
     - M5: 0x24C -> 0x1000
     - M6: CFG made writable in w32_allowed (N=26 catches it too)
     - M7: atu_ok allows 0x204 (N=26 catches it too)
     - M8: the log flag dropped
     - M9: a = 1 (probe)
     - M10: ATU teardown skipped at hi > 26
     - M11: the last region 1 read moved to region 0 CR2

     N=26 does not catch the S27-row mutants, which is expected.
   - **Table mutants with pin=None.** Each one is rejected:
     - a partial mask
     - a wait_eq CFG row
     - ATU rows reordered before CFG
     - a CFG read in S25
     - a 5 us delay on an S27 row

     A tag-only change is not caught, which is expected because tags are not semantic.
   - **Gate selftest.** Its 66 table mutants include 9 new S27 cases (`test-bluejay-boot.py:3161-3169`).
6. **Unverified facts tagged as source** (read). See findings 2 and 3. The 0x40-0x7C rows and 0x48 are correctly tagged `guess`.

## Findings

1. **Medium (functional; it can cost an extra hardware cycle).** The S27 offset set (`gen_wlan_seq.py:582-599`, pinned at
   `test-bluejay-boot.py:2805`) is a silent subset of research 32 s.4.2 step A. Step A asked for 0x00..0xFF, 0x100, 0x1B4,
   0x240-0x24C and the candidate window registers including 0x84. The commit drops:
   - 0x84 `PCI_BAR1_WIN` (`include_pcicfg.h:489`; named in step A).
   - 0xB0/0xB4/0xB8 (DEVCAP / DEVCTL / LNKCAP if the PCIe capability is at 0xAC; `include_pcicfg.h:501`, `:503`). Step B/C needs
     MPS/MRRS from these.
   - 0xC0-0xD0 and 0xD8-0xFC (rest of the PCIe capability and any capability the 0x34 walk points to above 0x7C). The walk can
     dead-end without them.
   - 0x1B4 (`rc.h:238` `WIFI_L1SS_LTR_LATENCY`).

   Nothing in the commit, the step text or the handoff records the deviation. Keeping 0x8C-0xA8 out is correct (check 1), but that
   decision is also unrecorded.

   **Fix:**
   - Add CFG reads 0x84, 0xB0, 0xB4, 0xB8, 0xC0, 0xC4, 0xC8, 0xCC, 0xD0, 0xD8..0xFC and 0x1B4, with these cites:
     `include_pcicfg.h:489/501/503`, `rc.h:238`, and the `capw` walk cite tagged guess for 0xC0-0xFC.
   - Add a generator comment and a step-text clause: "0x8C-0xA8 deliberately not read: 0xA4 PCI_BACKPLANE_DATA
     (`include_pcicfg.h:496-497`) is a backplane access, 0x90/0x98 interrupt/mailbox (`:493`, `:495`)".
   - Extend `_WLAN_S27_CFG`, `cfg_offs[]` and `CFG27` to match, and add an audit check that no S27/CFG row lies in 0x8C..0xA8.

   If the driver prefers to keep 36 rows, record the deviation and the reason in research 32 s.4.2 and the handoff instead.
2. **Low (tag / cite: a guess filed as source).** Row 315 (CFG +0xAC; `gen_wlan_seq.py:591`) is tagged `read from source` and
   cites `include_pcicfg.h:499` `PCI_BAR0_WIN2`. The same DHD source shows that `PCI_BAR0_WIN2` is the non-Gen2 register, and that
   Gen2 cores use `PCIE2_BAR0_WIN2` 0x70 instead (`aiutils.c:584-587`). `include_pcicfg.h:501/503` place DEVCAP/DEVCTL at
   0xB0/0xB4 "in PCIE devices", which puts the PCIe capability header at 0xAC. `rc.h:239/241` give the same answer: 0xBC-0x10 and
   0xD4-0x28 (`pci_regs.h:553`, `:666`). The "BAR0_WIN2" reading is therefore unsupported for this PCIe chip. If it were carried
   into step C, it could put a window write at the PCIe capability header. Whether the BCM4389 core is `PCIE_GEN2` is not in the refs
   (the macro is not defined in the fetched files), so any claim about it is a guess.

   **Fix:** set the note to "PCIe capability header (cap id 0x10 expected: guess)". Cite `include_pcicfg.h:501, :503`,
   `rc.h:239, :241` and `pci_regs.h:553, :666`, plus `aiutils.c:584-587` ("PCI_BAR0_WIN2 is the non-Gen2 register"). Either keep
   the tag `read from source` for the offset arithmetic and drop the BAR0_WIN2 meaning, or tag the row `guess`.
3. **Low (cite gaps).**
   - Row 314 (0x88; `gen_wlan_seq.py:590`) claims "the attach code reads it; 0xFFFFFFFF aborts attach" but cites only the define.
     On PCIe devices the same offset is `PCIE_CFG_SUBSYSTEM_CONTROL` (`include_pcicfg.h:491`).
     **Fix:** cite `dhd_pcie.c:2162-2168` (read + `goto fail`), `siutils.c:2756-2757` (there it switches to SI_BUS rather than
     aborting) and `include_pcicfg.h:491`.
   - Row 316 (0xBC) gives the bit fields "speed [3:0], width [9:4]" without a source.
     **Fix:** cite `pci_regs.h:567` (`PCI_EXP_LNKSTA_CLS`) and `:574` (`PCI_EXP_LNKSTA_NLW`).
4. **Low (defence in depth, pre-existing latitude that S27 now relies on).** `size_of(WLB_CFG)` = 0x2000 (`wlanlink.c:165`) lets
   `r32` read CFG offsets 0x1000..0x1FFC. Those offsets translate to target 0x01001000+, with bit 12 set, which is outside the 4 KiB
   function config space. What the DWC core sends for them is unverified. No row uses them today; only the table audit keeps them out.
   **Fix:** in `r32`, refuse `blk == WLB_CFG && off >= 0x1000u`, and add a host-test `EXPECT` that a CFG read at 0x1000 fails.
   Cite PCIe 4 KiB config space, `pci_regs.h:30` `PCI_CFG_SPACE_EXP_SIZE`.
5. **Low (reproducibility).** The `linux/pci_regs.h:<n>` cites come from `/usr/include/linux/pci_regs.h`
   (`gen_wlan_seq.py:496, 576-577`). That file is not hashed in `sources_sha256_16`. A different `linux-libc-dev` changes the line
   numbers, and `--check` then fails on another host. S27 adds 11 such cites.
   **Fix:** copy `pci_regs.h` into `~/pixelios-artifacts/refs/wlan-stage2/` and add it to `SRC_NAMES` so it is sha-pinned (record
   which kernel version it came from), then regenerate.
6. **Nit.**
   - The generator comment "An offset whose source cannot be cited is NOT a row" (`gen_wlan_seq.py:575`) contradicts the 16
     guess-tagged 0x40-0x7C rows. Reword it to "...is a row only if tagged guess with the walk cite".
   - `STEP_NAMES` has 27 before 26 (`gen_wlan_seq.py:275-276`; this is cosmetic, because the JSON sorts keys).
   - The JSON `_doc` still says it is generated from `wlan-stage1` only, but S27 also reads `wlan-stage2`.
   - The `wlanlink_host_test.c:3` header still lists N=7..24.
7. **Info (run hygiene, unchanged from review 7).** If a hang happens inside S27, pstore holds only `LINK S27 start`. The live USB
   log is needed to name the row: the last `R27` line + 1. 43 extra lines (~1.7 KB) fit in the 32 KiB ramlog, given that the n26
   run's usb.txt was 21 KB.

No Critical or Important finding. S27 adds no write of any kind. The C allowlist refuses every CFG write and every ATU region 1
write on its own, without help from the table. The teardown and its order are unchanged and run for every stop inside S27. Each
row-to-write or offset move I tried makes the N=27 host test or the table audit fail.

## Verdict

**SAFE WITH CONDITIONS.** Running `--wlan-link=27` on hardware is acceptable under these conditions:

1. Build from 605bad4 (or a successor whose only changes are findings 1-6) with the explicit `--wlan-link=27`, and pass the gate first.
2. Run `tools/xrun` / probe-run with `--timeout 600` (AP-14), with the USB log live (finding 7). Tell the user beforehand that the worst case is a manual long-press reset.
3. Before the run, either apply finding 1 (cheaper than a second cycle) or record why the 36-dword subset is enough.
4. Do not use the +0xAC result as a "BAR0_WIN2" value anywhere until finding 2 is fixed. Treat it as the PCIe capability header candidate.

Before a push: findings 2-6.
