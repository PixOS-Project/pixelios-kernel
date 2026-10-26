# Review 23: W-D2 stage 1, read-only BCM4389 attach in XNU (kernel commit 07f1f58)

Reviewer: Claude Opus 5.5 (HW-WRITE review, adversarial), 2026-10-25. Scope: `xnu/patches/0120-sweet-wlan-bwfm-core.patch`
(`bsd/dev/sweet_wlan.c`, `bsd/dev/sweet_wlan_chip.h`, `sweet_ufs.c` hook), `tools/mkadt.py` `sweet-wlan` node, `tools/test_mkadt_wlan.py`,
`shim/wlan_xnu_host_test.c` + `shim/build.sh`, `tools/xrun` / `tools/build-xnu-image.sh` `--wlan-hold/--wlan-level`.
Line refs: `P:n` = line n of the patch file; `wlan.c:n` / `chip.h:n` = line n of the new file (wlan.c:n = P:n+40, chip.h:n = P:n+202).

## Verdict: SAFE WITH CONDITIONS

The single write (cfg 0x80, P:83) only takes the windows `swl_win_ok()` names and is read back before the BAR0 load (read from source,
P:82-90). The EROM walk is bounded and cannot overflow the core table (read from source + observed in the host test, below). Nothing in
the patch writes anything else (read from source: the only store through a mapped pointer is P:83). The conditions are F1 (three shim
preconditions missing before the first config/BAR0 TLP), F2 (the identity pins are untested: 8 of 9 mutants survive) and F4 (first
hardware run at `--wlan-level 1` only). None of the findings can reach a forbidden partition or a non-PCIe1 write; the worst case is
an SError panic or a bus hang in XNU, which a reset recovers (guess: no persistent state is written).

Verification command of the brief, run 2026-10-25: `RESULT: PASS wlan_xnu_host_test` (observed, exit 0; extracted header 335 lines).

## Requirement 1: allowlist and preconditions vs shim/wlanlink.c

Shim side (read from source): `r32()` wlanlink.c:250-271 checks, before EVERY BAR0 read, with `try_read32` (fault-tolerant):
live cfg 0x80 + `mem_ok(win, off)` (wlanlink.c:199-214), ATU 0x204 == 0x80000000, cfg 0x04 low 16 == 0x0002, ATU 0x200/0x208/0x210/0x214 ==
0/0x60000000/0x6000FFFF/0x60000000. Every window change goes through `cfg_ok()` (wlanlink.c:336-342), which also requires cfg 0x10 (BAR0
low) == 0x60000004 and only allows each window from its predecessor. The chain before S30 additionally proved LTSSM == L0 (`wait_eq ELBI
0x2C8 == 0x11 mask 0x3F`, wlan_seq.json rows 238/251/266, step 23-25), ATU region 0 programmed and enabled (rows 267-281, S26: 0x000=0x4,
0x004=0x80000000, 0x008=0x60FFE000, 0x00C=0, 0x010=0x60FFFFFF, 0x014=0x01000000, 0x018=0), BAR0/BAR2 assigned (rows 371-375, S28),
and ATU r1 upper base/target 0x20C/0x218 == 0 (`ctx_read` wlanlink.c:397-399, for ATU writes).

Glue side (read from source): PHY isolation bit 0 (P:158-163), ATU r1 five words (P:171-180), cfg 0x04 MEM-only (P:181-187), once,
with plain loads; per read only the allowlist + window write/readback (P:78-90).

Preconditions the shim has and the glue does not (read from source):
- (a) LTSSM L0 (ELBI 0x14520000 + 0x2C8); (b) ATU region 0 (0x000-0x018); (c) cfg 0x10 == 0x60000004 and cfg 0x14 == 0 (BAR0 the
  ATU r1 target points at); (d) ATU 0x20C/0x218 == 0; (e) re-check per BAR0 read (glue: once); (f) fault-tolerant reads (glue: plain
  loads; XNU arm64 has no probe read: `ml_probe_read` panics "unimplemented", xnu-pristine osfmk/arm64/loose_ends.c:739-745, read
  from source); (g) window transitions only along the observed chain (glue: any allowlisted window from any other).

Accesses the glue makes that the shim never made on hardware (read from source vs the rows above):
- ChipCommon 0x18000000 + 0x04 (capabilities, chip.h:113, P:483): NEW, never read on hardware (`_WLAN_MEM_OFFS`
  test-bluejay-boot.py:2836 has 0, 0xAC, 0xFC, 0x1E0 only).
- D11 wrappers 0x18121000/0x18122000/0x18123000 + 0x408/0x800/0x804 and cfg 0x80 := these three windows (chip.h:118, P:320, P:524-533):
  NEW (docs/research/42-bwfm-xnu-port.md:57 "D11 wrapper state is UNOBSERVED").
- PMU 0x17463EC4 read (P:158): not new (shim r32 PMU 0x3EC4 via try_read32, wlanlink.c:258; plain MMIO read of it in wlansmc.h `SMC cmp`).
- ATU DBI reads 0x200-0x214 (P:171-175): not new (S27 rows 343-349). cfg 0x04 / 0x80 reads (P:181-182): not new (r32 wlanlink.c:263).

## Findings

### F1 (Medium, condition) The glue skips three shim preconditions before its first config TLP and BAR0 read
Where: wlan.c:114-147 (P:154-187). The shim proves link L0, ATU region 0 and BAR0 = 0x60000004 before any BAR0 read (above); the glue
reads cfg 0x04/0x80 through region 0 (P:181-182) without checking region 0 or the link, and selects windows without the `c->bar0 ==
0x60000004` term `cfg_ok` puts on every forward cfg 0x80 write (wlanlink.c:337-341). Reachable state (read from source): the shim
teardown is best effort and continues past failed rows (wlanlink.c:546-556); with T03 (ATU 0x204 := 0) and T44 (iso clear) both failing,
the glue sees iso = 1 and ATU r1 enabled while PERST (T20) / WLAN_EN (T22) dropped the link, and issues a config read to a down link.
Also untested: whether the link stays in L0 for the tens of seconds between the shim hold and `sweet_ufs_init` (guess: no ASPM was
enabled, so it should; not observed). A config/MEM read to a down link on this DWC controller: SError or all-ones is unknown (guess).
This is the CR-19 "bad" pattern (docs/CODING-RULES.md:206: "PMU bit 0 only, then plain loads").
Fix: add an ADT property `elbi` = (0x14520000, 0x2000) (soc.json `wlan.elbi`, mkadt + test), pin it like the others, and in this order
after the iso check and before the ATU r1 check: `if ((elbi[0x2C8/4] & 0x3F) != 0x11) refuse;` then ATU region 0
`atu[0]==0x4 && atu[1]==0x80000000 && atu[2]==0x60FFE000 && atu[3]==0 && atu[4]==0x60FFFFFF && atu[5]==0x01000000 && atu[6]==0`, then
ATU r1 plus `atu[0x20C/4]==0 && atu[0x218/4]==0`; after the cfg 0x04 check: `hw.cfg[0x10/4]==0x60000004 && hw.cfg[0x14/4]==0` else refuse.
Re-read cfg 0x04 inside `sw_rd` only when the window changes (cheap; keeps (e) without doubling every TLP).

### F2 (Medium, condition) Every identity pin of swl_stage1 is untested: 8 of 9 header mutants survive
Where: shim/wlan_xnu_host_test.c:139-194; pins chip.h:272-331 (P:474-533). Observed (mutants on copies in /tmp/rvm, same gcc command):
`eromptr != SWL_EROM_PAGE` removed: SURVIVED; PMU base 0x18012000 pin removed: SURVIVED; D11 wrapper address pin removed: SURVIVED;
`TYPE != AI` removed: SURVIVED; chip id 0x4389 removed: SURVIVED; ChipCommon base pin removed: SURVIVED; CA7/SYS_MEM wrapper pins removed:
SURVIVED; second-descriptor-not-COMPONENT `return -1` -> `continue` (chip.h:215-217): SURVIVED; `level < 2` -> `level < 1`: killed. The
test only replays the good chip, so the pins that stop the next block from being touched are never exercised. `sweet_wlan.c` (ISO / ATU /
Command / ADT checks, window readback) is not host-compiled at all (build.sh only extracts chip.h), so its mutants cannot fail anything.
Fix: add negative cases, each asserting `swl_stage1(...) == -1` AND that no read of the next block happened (`f.seen[]` of the PMU / wrapper
rows == 0): chip id 0x4388, TYPE 0 (SB), eromptr 0x18106000, an EROM where ChipCommon base is 0x18001000, where the PMU core is missing
or at 0x18013000, CA7 wrap 0x18121000, D11 #1 wrap 0x18124000, and a COMPONENT followed by a non-COMPONENT dword. For the glue: move
`sw_rd` and the precheck decision into a pure `static inline int swl_precheck(uint32_t iso, uint32_t ltssm, const uint32_t atu[], uint32_t
cmd, uint32_t bar0lo, uint32_t bar0hi)` in chip.h and table-test it (each word off by one bit refuses); keep only the loads in sweet_wlan.c.

### F3 (Medium) Second hand-copied allowlist, no drift audit; no patch audit for the one store
Where: chip.h:106-129 (P:308-331) duplicates `mem_ok` (wlanlink.c:199-214) by hand, already different by design (+0x04, +3 D11 windows,
-0x18001000/-0x18026000). CR-19 (docs/CODING-RULES.md:207, "bad: a second hand-copied refusal list in the patch") and no audit in
tools/test-bluejay-boot.py names patch 0120 (grep `0120\|sweet_wlan`: no hit, read from source). A future edit of either list drifts silently,
and a second store in sweet_wlan.c would not be flagged.
Fix: in test-bluejay-boot.py add an audit over patch 0120 `+` lines: (i) exactly one assignment through `h->cfg[`/`hw.cfg[`/`->mem[` (the
`h->cfg[0x80 / 4] = win;` of P:83) and no other `= ` into a `volatile` mapping; (ii) the set {(win, off)} accepted by `swl_rd_ok` (evaluate the
extracted header with a tiny C driver, as the host test does) equals `_WLAN_MEM_OFFS` restricted to {0x18000000, 0x18012000, 0x18120000,
0x18126000} plus 0x18105000 < 0xE00, plus exactly the cited new rows {(0x18000000, 0x04)} and D11 {0x18121000, 0x18122000,
0x18123000} x {0x408, 0x800, 0x804}; any other difference fails.

### F4 (Medium, condition on the first run) Level 2 D11 reads and ChipCommon 0x04 were never read through the guarded shim path
Where: P:320, P:483, P:521-533. Research 42 planned these rows in the shim first (docs/research/42-bwfm-xnu-port.md:160, 177: "Add the
read-only check (0x18000000, 0x04) to mem_ok ... one confirming run"; D11 triples drafted as shim `read_only` rows); the patch puts them
straight into XNU plain loads. Mitigation present (read from source): D11 reads are gated by `sweetwlan=2` (P:141-144, P:521), ordered
last (after every level-1 line is printed), and each wrapper address is first pinned to the EROM value (P:526). AI wrapper (DMP) registers
of a core held in reset are readable on Broadcom AI chips (guess: brcmfmac `ai_iscoreup`-style reads of IOCTL/RESET_CTL are done on cores in
reset; not observed for the 4389 D11).
Fix / condition: the first hardware run is `--wlan-hold` with the default `--wlan-level 1`; `--wlan-level 2` only after the level-1 run shows
`stage 1 done level 1`. Optionally make the allowlist level-aware: `swl_rd_ok(win, off, level)` refusing the three D11 windows for level < 2,
so the guard itself (not only the caller order) keeps them closed.

### F5 (Low) PMU registers read without the capabilities check bwfm makes; `SWL_CAPS_PMU` defined and unused
Where: chip.h:36 (P:238) defines `SWL_CAPS_PMU`, chip.h:302-306 (P:504-508) read the PMU block unconditionally; OpenBSD reads PMU caps only
`if (ch_cc_caps & BWFM_CHIP_REG_CAPABILITIES_PMU)` (bwfm-host/chip.c:122-128, read from source). The new 0x04 read gates nothing.
Fix: `if (!(c->cc_caps & SWL_CAPS_PMU)) { printf("SWEET WLAN: cc_caps %#x has no PMU, stop\n", c->cc_caps); return -1; }` before P:504.
(Host test value 0x10000000 passes, chip.h observed caps unknown: the hardware run decides.)

### F6 (Low) compatible check is a prefix match
Where: wlan.c:106 (P:146): `csize <= sizeof(SW_COMPAT) - 1 || strncmp(compat, SW_COMPAT, sizeof(SW_COMPAT) - 1)` accepts
"google,gs101-bcm4389-pcieX". Precedent patch 0088:855 compares including the NUL.
Fix: `csize < sizeof(SW_COMPAT) || strncmp(compat, SW_COMPAT, sizeof(SW_COMPAT)) != 0`.

### F7 (Low) `--wlan-level` not validated; flows into the kernel command line
Where: tools/build-xnu-image.sh:26, 36; tools/xrun:34. `--wlan-level "2 sweetgpujob=1"` appends a WRITE-enabling boot-arg; `--wlan-level 3`
builds an image whose glue then refuses; `--wlan-level` without `--wlan-hold` is silently ignored.
Fix: after option parsing in build-xnu-image.sh: `case $wlanlvl in 1|2) ;; *) echo "--wlan-level needs 1 or 2" >&2; exit 2 ;; esac` and
`[ "$wlan" = 1 ] || [ "$wlanlvl" = 1 ] || { echo "--wlan-level needs --wlan-hold" >&2; exit 2; }`.

### F8 (Low) Stale cite and unchecked keys in mkadt
Where: tools/mkadt.py:328 says "patch 0118"; the patch is 0120. mkadt.py:335-337 index `w['atu']` / `w['pmu_pcie1_iso']` while the guard
(`'mem' in w and 'cfg' in w`) checks only two keys (KeyError on a board with a partial wlan block). test_mkadt_wlan.py pins soc.json, not the
patch's `SW_*` constants, so a change of `SW_MEM`/`SW_CFG`/`SW_ATU`/`SW_ISO` (P:61-65) is caught only on hardware ("refused", the safe direction).
Fix: "patch 0120"; guard `all(k in w for k in ('mem', 'cfg', 'atu', 'pmu_pcie1_iso'))`; in test_mkadt_wlan.py parse the four `#define SW_*`
lines out of the patch and compare them to the node.

### F9 (Info) No restore of cfg 0x80 and no teardown of the held link in XNU
Where: P:188-194. After stage 1 the window stays on the last one read (0x18126000 or 0x18123000), and on any refusal the shim-held link
(PHY de-isolated, WLAN_EN high) stays up for the whole XNU session (read from source: shim.c:618-624 releases only when `loader_boot`
returns). By design (read-only stage); a later writing stage must not assume cfg 0x80 == 0x18000000. Fix: none required now; state it in the
patch header.

## Requirement 2: hang/fault order, mappings, the window store

- Order (read from source): PMU iso (always-on PMU, shim-read) -> ATU r1 (DBI, RC-local, no TLP) -> cfg 0x04/0x80 (TLP) -> BAR0. ATU before the
  first TLP is safer than the shim's own r32 order (cfg 0x80 first, wlanlink.c:263). Missing link/region-0/BAR checks: F1.
- D11 at level 2: boot-arg gated and last (F4).
- Mappings: `ml_io_map` -> `io_map(..., VM_WIMG_IO)` (xnu-pristine osfmk/arm64/machine_routines.c:1540-1545); `io_map` truncs/rounds to the 16 KiB
  page itself (osfmk/arm/io_map.c:95-99) and returns `start + start_offset` (:132); `wimg_to_pte` maps VM_WIMG_IO non-DRAM as
  CACHE_ATTRINDX_DISABLE = Device-nGnRnE (osfmk/arm/pmap/pmap.c:5651-5662, proc_reg.h:1117) (all read from source). Device memory is not
  speculatively read (ARM ARM, guess from memory: not re-checked here), so the extra mapped bytes 0x60004000-0x6000FFFF beyond what is read, and
  0x60FFC000-0x60FFDFFF below the config window (outside ATU region 0, which starts at 0x60FFE000), are mapped but never touched by the code
  (read from source: offsets used are 0x04/0x80 cfg and < 0x1000 BAR0). 0x60000000 is below gs101 DRAM (guess: DRAM base 0x80000000), so not
  the DRAM Device-GRE case.
- The store `h->cfg[0x80/4] = win` (dword) matches the shim's full-word cfg 0x80 writes (rows mask 0xFFFFFFFF, cfg_ok wlanlink.c:336) and the
  readback compare is a control dependency, so the BAR0 load cannot be issued before the readback returns (guess from the Device-memory rule).
  The readback proves the EP register took the value; it does not prove BAR0 decode (F1 (c)).

## Requirement 3: bounds

- EROM walk (read from source, chip.h:131-241): every outer iteration of `swl_dmp_regaddr` advances `*erom` by >= 4 or returns; the scan
  advances by 4 per dword; reads beyond 0x18105E00 are refused by `swl_rd_ok` (chip.h:110) and end the walk with -1. Observed: the host test's
  all-zero EROM stops with `nerom <= 0xE00/4` and one refusal; 40 components stop at `ncores == 32` (assert passes). No 32-bit wrap possible
  (start pinned to 0x18105000). After EOT no further dword is read (dmp_regaddr returns 1 without advancing, the scan re-reads the EOT dword
  and exits), same as OpenBSD.
- No path reads outside the allowlist: every BAR0 load goes through `sw_rd` (P:78) (read from source).
- `reg` parsing: `sz < 2 * sizeof(uintptr_t)` refused, extra bytes ignored (Low, not a hazard); values must equal the pins. `level > 2`
  refused, uint32 (P:141-144). compatible: F6.

## Requirement 4: ADT

mkadt node (tools/mkadt.py:328-339) emits compatible, reg (0x60000000, 0x10000), cfg (0x60FFE000, 0x1000), atu (0x14B00000, 0x1000),
pmu-iso (0x17463EC4, 4) from soc.json (soc/google/gs101/soc.json wlan keys pmu_pcie1_iso, atu, cfg, mem) as `<2Q` (mkadt.py:62); these equal the glue pins P:61-65 and the
lengths P:150-151 (read from source). A wrong value is refused before any mapping (P:150-152, `sw_prop_is` P:118). CR-20 satisfied; CR-25
satisfied for the allowlist (deny by default, `return 0` chip.h:121). Gaps: F6, F8.

## Requirement 5: fidelity to OpenBSD (bwfm-host/chip.c:269-384, bwfmreg.h)

All descriptor masks/shifts, core ids and register offsets match bwfmreg.h (read from source: :22-34, 42-78, 121-164). Differences:
1. Second dword not COMPONENT: OpenBSD returns keeping the cores found (chip.c:295-298); glue returns -1 (stricter; untested, F2).
2. Core order: OpenBSD `LIST_INSERT_HEAD` (chip.c:319), so `bwfm_chip_get_core` returns the LAST match; glue `swl_core(.., 0)` returns the FIRST.
   Same result for single-instance cores; for D11 the index order is pinned per wrapper (P:526).
3. `bwfm_chip_get_pmu` falls back to ChipCommon when AOB is set but no PMU core exists (chip.c:174-181); glue stops (stricter).
4. PMU caps read only if `cc_caps & PMU` in OpenBSD (chip.c:122-128); glue reads PMU unconditionally (F5).
5. `mpnum` not decoded (unused in OpenBSD too); nmw/nsw uint32 vs uint8 (no behavioural change).
6. Bus read can fail and ends the walk; walk bounded by the allowlist; fixed table of 32; no writes (set_passive/reset/ramsize dropped), as
   the header states (chip.h:20-25).

## Requirement 6: host test

The test includes the header extracted from the patch by the same awk as `shim/build.sh` (build.sh new lines 554-557; observed: extracted
file is 335 lines = the hunk `@@ -0,0 +1,335 @@`, P:202). Mutants (observed, /tmp/rvm): 9 run, 1 killed, 8 survived (F2). Also: checks are
`assert()`, so a `-DNDEBUG` build would pass vacuously (Low; use an `EXPECT` that counts failures, like wlanlink_host_test.c). `fake_rd`
re-implements the glue decision instead of testing it (F2 fix).

## Conditions for SAFE

F1 and F2 fixed and re-reviewed (Sonnet is enough for the re-check); the first hardware run at `--wlan-level 1` (F4).
F3, F5-F8 before the stage-2 (writing) patch.
