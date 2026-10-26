# Review 20: research 42 (bwfm port into XNU for the BCM4389), adversarial, 2026-10-25

Reviewed: `docs/research/42-bwfm-xnu-port.md` (166 lines, worktree `w3a-s34`, HEAD c73a059). Reviewer: Claude Sonnet 5.5. No code under review (research note); nothing edited except this file and one ANTIPATTERNS entry.
Tags: [src f:l] = opened this session; [obs] = ran a command or read a log this session.
Method: opened about 60 of the note's citations (refs `wlan-stage2/3/4`, `bwfm-host`, shim, patches, research 36-40, handoff, FB, HX, logs `w3a-n35`/`w3a-n36`).

## Verdict: SAFE WITH CONDITIONS

The chip facts (4389 deltas), the three KEEP/DROP verdicts and the MMIO-guard scope hold up against the sources. There is one false "not in the refs" claim that feeds an Unknown and a drafted step (F1), one wrong "neither exists" about the firmware hand-off (F2), and a block of wrong research-note line cites (F4). None of them unsafe by itself. Nothing in the note authorises a hardware write. Apply F1, F2, F4 and F5 before the note is used as the brief for the implementer (steps 3-8).

## What checked out (so the next reviewer need not repeat it)
- bwfm: rambase default (`W/bwfm.c:1600-1611`), firmware copy/last-word (`W/if_bwfm_pci.c:826-834`), `SRNB 0xf0` and bank loop (`W/bwfmreg.h:100-114`, `W/bwfm.c:1515-1527`), double passive (`W/bwfm.c:955-962`), cc_caps/pmucaps gate (`:973-988`), `pmurev < 17` (`:1386-1391`), `ca7_set_passive` D11 loop (`:1320-1342`), RAMSIZE magic (`W/if_bwfm_pci.c:515-520`, `/tmp/bw_pci.h:108-109`), `buscore_reset` watchdog := 4 + 100 ms + ASPM (`:1845-1872`), cfg 0x80 before every access (`:1826-1843`): all say what is claimed. [src]
- DHD: `CA7_4389_RAM_BASE` (`D/dhd_pcie.c:2338-2358`, `D/include_sbchipc.h:4169`), `DONGLE_TCM_MAP_SIZE` 4 MiB (`D/dhd_pcie.h:42`, check at `dhd_pcie.c:2453`), SYSMEM masks and bank walk (`D/siutils.c:7036-7050`, `G/g4389-include_sbsysmem.h:115-118`), `sysmem_banksize` writes plain `idx` vs SOCRAM memtype shift (`siutils.c:6966-6970` vs `:7064-7068`), `si_srpwr_request` (`siutils.c:9311-9361`), `si_srpwr_cap` (`:9513-9517`), `MULTIBP_CAP` and `_multibp_enable` (`G/g4389-include_siutils.h:963-968`, `g4389-siutils.c:2830-2832`), `SICF_CPUHALT 0x20`, CA7 halt then SYS_MEM reset order (`dhd_pcie.c:10123-10131`), `pwr_req_cmn` (`:795-816`), FLR capability read (`:7206-7212`): all match. [src]
- brcmfmac: `reset_device` watchdog 4 / 100 ms (`B/brcmfmac-pcie.c:651-690`), double set_passive (`brcmfmac-chip.c:1039-1047`), single-D11 `ca7_set_passive` (`:1337-1348`), D11 flag bits (`:81-82`), 4387 case (`:746-747`): match. [src]
- Observed values: `R34 0x00030300`, `R35 row 1371 0x1`, `row 1379 0x115`, `row 1383 0x0F00039F`, `R36 row 1407 0x21`, `summary reached=S36 stopped=none restore=ok` are in `logs/probe/*n35_/usb.txt` and `*n36_/usb.txt` (lines 954-998). [obs]
- Patch/shim facts: last patch is `0117-sweet-dev-overlay.patch` in this worktree and in every other worktree and the clone, so `0118` is free (3 June-window caveat: re-check at apply time). `sweet_usb` ADT pattern (`0082...patch:150-161`, `ml_io_map_wcomb` `:222-228`), USB-before-UFS (`0091...patch:5-20`), `sweet_ufs_init` calling keys/touch/gpu init (`0102...patch:140-156`), `0104` adds `sweet_mali.c` to `bsd/conf/files`, `mkadt.py` `wlan` flag node (`:208-210`) and `sweet-usb`/`sweet-reset` nodes (`:249-269`), `loader.c` overlap refusal and `SWEETXNU` scan (`:75-100`), `mem_ok`/`memw_ok`/`cfg_ok`/`w32_allowed` contents: match. `wlan_seq.json` has 1412 rows, 47 teardown, 296 write rows (`test-bluejay-boot.py:2823`). [src/obs]
- Requirement 3 (user decision 2): the note states the rule correctly (s.0 item 2, FB:234), s.4b limits the guard to "centralised" properties, s.4c lists register meaning, sequence, timing, hand-off state, DMA and firmware behaviour as needing a per-step source, and drafted step 10 forbids extending the write rules without a citation. No sentence says the guard replaces per-step verification. PASS (with F7 as a tightening).

## Findings

### F1 (Medium) false claim "`pcie_watchdog_reset` body is not in the refs"; U6 and drafted step 1(c) are built on it
- Note s.2 row 1 ("the body of `pcie_watchdog_reset` and the exact value it writes are NOT in the refs (grep over D and G: only call sites)"), U6 ("not in refs: fetch `siutils.c`/`pcie_core` ..."), s.6 step 1(c) ("fetch the body from Google's tree").
- Observed: the body is in `~/pixelios-artifacts/refs/wlan-stage2/pcie_core.c:70-130`. [src] The note grepped the wrong file set (`pcie_core.c` is in `D` and was not listed in the note's own `D` description). What it shows:
  - `pcie_watchdog_reset(osh, sih, wd_mask, wd_val)`: ASPM off via `configaddr/configdata`, then for `ccrev >= 65`: `watchdog` masked write `wd_mask/wd_val` (DHD passes `WD_ENABLE_MASK` and `WD_SSRESET_PCIE_F0_EN` 0x10000000, `|WD_SSRESET_PCIE_ALL_FN_EN` 0x80000000 when buscorerev < 66, `dhd_pcie.c:1734-1739`, `include_sbchipc.h:1552-1561`), then `watchdog` counter := 4, 2 ms, then clears intstatus; for `ccrev < 65`: `watchdog := 4` and 100 ms, which is the bwfm/brcmfmac form. The function also restores PCIe config registers (`cfg_offset[]`, the CRWLPCIEGEN2-163 snoop-bus WAR).
  - `dhd_pcie.c:2231-2232` comment: "pcie_watchdog_reset will put the ARM back into halt state". Relevant to the DROP: DHD relies on the reset to halt the ARM, and our S36 does the halt explicitly.
- Effect on verdicts: the watchdog DROP stays justified (S36 reached the halt without any reset, [obs] n36), but its stated reason "value 4 is not 4389-cited" is wrong: for ccrev >= 65 DHD does NOT write plain 4 (it first sets the SS-reset enable bits). Which branch applies to the 4389 is not stated (ccrev of the chip never read; the CC rev is in the EROM decode, not in the note).
- Fix: replace the sentence in s.2 row 1 and U6 with the cites above (`pcie_core.c:70-130`, `dhd_pcie.c:1733-1739`, `:2231-2232`), state that the `ccrev` of the 4389 decides the branch (read it from the n33 EROM decode or mark Unknown), keep DROP, and delete the "fetch the body" part of step 1(c) (keep only the ccrev read).

### F2 (Medium) "Neither exists" about the shim-to-XNU firmware hand-off is wrong; an ADT RAMDisk mechanism exists
- Note s.3c last paragraph: "the missing link is an ADT property ... Neither exists. **Unknown U10**"; U11 "Is the ABL initrd range excluded from XNU's usable memory"; step 6.
- Observed: `tools/mkpayload.py:20-26,125-131` already appends `--ramdisk IMAGE` as one more payload segment right after the kernel, moves `kernel_end` past it, and writes `/chosen/memory-map` `RAMDisk = <phys, size>` into the ADT; the docstring says the image lands "inside topOfKernelData (never handed to the VM)". [src] Patch `0117` already reads that RAMDisk memdev (`xnu/patches/0117-sweet-dev-overlay.patch:71,91`, `IOFindBSDRoot` turns the property into a memdev). [src]
- Consequences: (a) route "ADT property" exists today for a blob embedded in the XNU payload, with no `loader.c` change and with U11 answered by the docstring for that route (not for the ABL-initrd route); (b) `mkpayload.py:130` asserts "ADT already has a RAMDisk", and 0117 uses the same single property, so a WLANFWB1 container and the dev overlay collide unless one container format carries both. The note misses both points.
- Fix: rewrite U10/U11 to list route A' = pack WLANFWB1 as the payload ramdisk (existing `RAMDisk` property; XNU finds it with the 0117 lookup; settle the single-RAMDisk collision with `rd=md0`/the dev overlay) next to route A (ABL initrd + new ADT property), and say which was chosen and why. Tag U11 for A' as read-from-source (mkpayload docstring), for A as still unknown.

### F3 (Medium-Low) D11 DROP rests on an argument from absence in one file, and drops both halves of the reset
- Note s.2 row 2: "DHD's download path never names the D11 core ... grep `D11_CORE_ID` in D/dhd_pcie.c: none".
- Observed: the grep claim is true (no hit in `dhd_pcie.c`). [obs] But the D11 reset in DHD happens inside the dongle reset (full chip watchdog, F1), which the note also drops. Result: with `bc_reset` dropped and the D11 loop dropped, nothing in the port puts the 3 D11 wrappers in a known state; their state is UNOBSERVED (the note says so, `W/run-observed.txt` shows `UNOBSERVED` at 251/260/269 [obs]). `D/aiutils.c` has D11-specific `UCM_CORRUPTION_WAR` code (`:1271-1276`, `:1317-1322` [src]) that shows D11 needs special reset handling when it is reset.
- Verdict stands as "DROP for the first download attempt" only because the firmware has not started and D11 is idle after PERST [guess]. It is not "justified by DHD parity".
- Fix: reword the verdict to "DROP for the first attempt; D11 not touched by DHD itself, but DHD's chip reset puts it in reset and we skip that; its state is UNOBSERVED"; make the 3-register read-only D11 check (already in step 2) a precondition to the first ARM release, not an option.

### F4 (Low-Medium) wrong line cites to sibling notes and files (cite exists but does not say what is claimed)
| Note cite | Claimed | Actual |
|---|---|---|
| `R40:11` (s.1b) | SYS_MEM is rev 9 | line 11 is a heading; the EROM decode is `R40:14` (ARM_CA7 rev 9 ... SYS_MEM) and `R40:20` |
| `R40:12` (s.1c) | PCIE2 base `0x18001000` | `R40:14` |
| `R40:15` (s.1c) | "DHD calls it before the first si_setcore(ARMCA7) citing dhd_pcie.c:2203-2215" | `R40:15` is the timing baseline; the DHD fact itself is right at `D/dhd_pcie.c:2203-2210` [src] |
| `R36:12` (s.4a) | cfg 0x70 = 0x18101000 | `R36:10` |
| `R37:12` (s.1a) | cfg 0x84 = 0 observed | `R37:13` and `R36:10` |
| `R39:68-70 (UD)`, `88-89` (s.3c) | CLM after firmware; step 7 text | UD is `R39:72`; step 7 is `R39:84`; the file has 85 lines |
| `FB:128-129`, `FB:127-128` | "What stays useful" and wifid glue list | `FB:141-142` and `FB:129-130` |
| `test_mkadt_wlan.py:33-36` | `wlan` node test | `:26-28` |
| `wlanlink.c:204-205` for "0x18001000 off 0x1E8 in mem_ok" (s.1c) | | `:211-212`; `:204-205` is the 0x18000000/0x18012000 lines |
| `test-bluejay-boot.py:2837-2846` `_WLAN_S34_WINDOWS` | | `:2835` (and `_WLAN_MEM_OFFS` `:2836` is right) |
| `D/dhd_pcie.c:6160-6181` (s.1a) "moves cfg 0x84 only when RAM start/end fall in different windows" | | the function only sets `bar1_switch_enab` (`:6162-6180`); the move happens in the TCM accessor (`R37:73`), not here |
- Fix: correct each cite to the "Actual" column; for the last row reword to "sets `bar1_switch_enab`".

### F5 (Low) a source for 7+ constants lives only in `/tmp`
- `/tmp/bw_pci.h` is cited for `BAR0_WINDOW`, `BAR0_REG_SIZE`, `LINK_STATUS_CTRL`, ASPM mask, `BWFM_RAMSIZE`, magic. It exists now and the hash prefix matches (`0271c0abd035a766`, `$OpenBSD ... v 1.9`) [obs], but `/tmp` is volatile and the note itself says it is "NOT under W". A later reviewer cannot re-check it after a reboot.
- Fix: `cp /tmp/bw_pci.h ~/pixelios-artifacts/bwfm-host/if_bwfm_pci.h`, add a line to `bwfm-host/SOURCE.txt` (file name, `$OpenBSD` id, sha256), and change the cites to `W/if_bwfm_pci.h:...`.

### F6 (Low) uncited numbers stated as fact (AGENTS rule 7)
- "the 436-row table (1412 rows, 296 write rows ...)" (s.3b): 436 appears nowhere else; `test-bluejay-boot.py:2823` says 1412 rows, 47 teardown, 296 write rows. Fix: delete "436-row".
- "15 ms" SRPWR spin (s.1c verdict and s.4c): `PMU_MAX_TRANSITION_DLY` is 15000 us in one build branch and 1500000 in another (`D/include_sbchipc.h:4874-4876` [src]); the spin at `siutils.c:9428-9430` uses it. Fix: cite those lines and say which `#if` applies, or write "DHD's PMU_MAX_TRANSITION_DLY (15 ms or 1.5 s by build)".
- s.2 row 1 "bwfm's version ... clears PMU/SRPWR state" is HX:12's risk note (a guess), tag it [guess] there.
- s.4b item 5 "bounded polls (every wait row has `polls`/`delay_us`)" and "refuse-and-restore on any read of all-ones from a mapped window" are written as existing guard properties. The shim has bounded polls (`wlanlink.c:8,450`) and per-rule dead-read checks (`:224,234,349,388-394,505-507`) but no general all-ones refusal. Fix: cite those lines and call the general version "proposed".

### F7 (Low) tighten the guard scope text so no reader takes "exact value sets" as pre-verified
- s.4b item 3 lists "exact value sets (ATU, ELBI offsets, GPIO field masks, CFG transitions)" as centrally enforceable. Enforcing them is central; deciding them (the S29 ATU values, the 12 ELBI offsets, the second GPH2 pin `2u`, U12) is device-specific and needs its source per value. s.4c does not list "the contents of the allowlists" as needing a per-entry cite.
- s.4a heading "DENY list (hard, enforced by the guard, not by review)" describes a guard that does not exist in XNU yet (it exists in the shim only).
- Fix: add one bullet to s.4c: "the contents of every ALLOW entry (values, masks, offsets) need their own source or measurement; the guard only enforces them"; change the DENY heading to "DENY list (proposed for the XNU guard; the shim enforces the equivalent today)".

### F8 (Info) things the note gets right that are easy to break later
- `0118` is the next free patch number in all worktrees checked (`gate-memo`, `integration`, `w3a-s34`, clone: last is 0117). Re-check at apply time; GPU work lands patches in parallel.
- `memw_ok` refuses the SRPWR write when `id == 0` (`wlanlink.c:223-224`) while DHD's request path (`dhd_pcie.c:795-816`) does not consult `si_srpwr_cap`: the note records this as U2; keep it open until a run shows bit 9 clear.

## Antipattern
Added AP-45 to `docs/ANTIPATTERNS.md` (research notes cite sibling notes by line number without re-opening them; a "not in the refs" claim based on a grep of a subset of the files).

## Verification run by the reviewer
`test -s docs/reviews/20-research-42-bwfm-port.md` (this file). No gate, no phone, no code run.
