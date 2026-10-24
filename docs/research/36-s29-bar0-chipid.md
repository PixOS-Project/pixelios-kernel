# 36 - S29: EP Command MEM, outbound MEM iATU region 1 and the first BAR0 read (ChipCommon chip id) of the BCM4389 behind pcie_1

Status: READ-ONLY research, 2026-10-24, goal W-A7. No code, table or tool changed; no phone access. Builds on research 33 s.5 (kernel clone), research 31/32 (super repo `docs/research/`), the observed runs `w3a-n27` / `w3a-n28c`.
Tags: **observed** (run dir + row), **read from source** (file:line, every line re-opened this session), **guess**.
Names: `D` = `~/pixelios-artifacts/refs/wlan-stage2` (`dhd_pcie.c`, `dhd_pcie_linux.c`, `siutils.c`, `include_pcicfg.h`, `include_sbchipc.h`, `include_hndsoc.h`, `include_bcmdevs.h`, `pci_regs.h`); `W` = `~/pixelios-artifacts/refs/wlan-stage1`; `rc.c`/`rc.h` = `W/dwc-whi/pcie-exynos-rc.{c,h}`; `pcie.dtsi` = `W/dts-gs101-gs101-pcie.dtsi`; `wlanlink.c`, `shim.c`, `gen.py` (= `tools/gen_wlan_seq.py`), `audit.py` (= `tools/test-bluejay-boot.py`) are in the kernel clone `pixos-project/pixelios-kernel`; `n28c` = `logs/probe/20261024T060726Z-w3a-n28c_/usb.txt` (kernel clone, gitignored); `HO` = super repo `docs/HANDOFF.md`.

## 0. Start state S29 inherits (all observed)
- Link: EP LNKCTL (cfg 0xBC low half) = 0 (no ASPM enabled on the EP side), LNKSTA (0xBE) = 0x1011 = Gen1 x1; LNKCAP 0x0046F812 (n28c, `R27 row=319,320`; `HO:218`). The RC side ASPM/L1 state is NOT observed (guess: Linux's ASPM setup is not replayed by the shim).
- cfg 0x04 dword = `0x00100000` (Command 0x0000, Status 0x0010 = capability-list bit only, `D/pci_regs.h:56`; n28c `R26 row=284`). No RW1C status bit is set.
- cfg 0x80 (`PCI_BAR0_WIN`) = `0x18001000`, 0x70 (`PCIE2_BAR0_WIN2`) = `0x18101000`, 0x84 = 0, 0x88 = `0x00080080` (n28c `R27 rows=309,313,314,315`).
- S28 (same run, before S29): BAR0 = `0x60000004`/0, BAR2 = `0x60400004`/0 assigned and read back; sizes BAR0 = 64 KiB (sizing readback `0xFFFF0004`), BAR2 = 4 MiB (`0xFFC00004`) (n28c `R28 rows=355,365`; `HO:249`). Correction to research 33 s.5: BAR0 is 64 KiB (not 32 KiB); DHD maps only 32 KiB of it (`D/dhd_pcie.h:41`).
- ATU region 0 (CFG0, bus 1 dev 0 fn 0, window `0x60FFE000..0x60FFFFFF`) enabled by S26; region 1 at reset: all registers 0 and LIMIT `0x00000FFF` (`HO:218`, S27 rows in `gen.py:614-619`). Unrolled iATU (DBI 0x900 = 0xFFFFFFFF, `gen.py:541`), ATU block = DBI 0x14800000 + 0x300000 = `0x14B00000` (`soc/google/gs101/soc.json:33,48`).
- CFG/MEM CPU addresses are Device-nGnRnE by default; the 32 MiB block holding the CFG window (`0x60FFE000`) is the same block as `0x60000000` (block = addr >> 25 = 48) (read from source `shim/mmu.c:1-12`; whether that block carries a special attribute: guess no, it is neither image nor framebuffer).

## 1. Command register (requirement 1)
| Item | Fact | Tag |
|---|---|---|
| Register | cfg 0x04: Command = bits [15:0], Status = bits [31:16] (RW1C status bits) | `D/pci_regs.h:40,53` |
| Bits | MEMORY = 0x2 (`pci_regs.h:42`), MASTER = 0x4 (`:43`) | read from source |
| What DHD does | `pci_enable_device` + `pci_set_master` BEFORE the first BAR0 access (`D/dhd_pcie_linux.c:1946-1950`, also `:1276-1281`); Linux `pci_enable_device` sets MEM (and IO if an IO BAR exists), `pci_set_master` sets MASTER | read from source (DHD) + guess (what Linux's helpers write) |
| Recommendation | write **MEM only**: cfg 0x04 := `0x00000002` (32-bit window store, upper half 0) | guess (see 1.1) |
| Why not RMW | a read-modify-write of the dword would write back the status half; today it is `0x0010` (read-only cap-list bit, no W1C set) so it would be harmless, but a 32-bit full write with a zero status half cannot clear any RW1C bit (PCI spec, guess). The shim's `w32` only accepts full-word CFG writes (`wlanlink.c:292,274`) | read from source + guess |
| Status bits to check | after the write: Command low 16 == `0x0002` (one-shot `wait_eq`, mask `0xFFFF`). After the read: log cfg 0x04 high half (Status) is NOT needed; log EP AER Uncorrectable Status (cfg 0x104, `PCI_ERR_UNCOR_STATUS` = AER base 0x100 + 0x04, `D/pci_regs.h:755`; AER header observed at 0x100 = `0x13C10001`, `HO:218`) and Link Status (0xBC) for evidence; UR = bit 20, CTO = bit 14 (`pci_regs.h:767,761`) | read from source + guess for the base 0x100 meaning |
| Restore | cfg 0x04 := `0x00000000` (observed original Command 0), unconditional, FIRST of the CFG teardown rows (before the BAR restores: S28's sizing assumes Command 0 and restoring BARs with decode on is pointless) | observed original |

### 1.1 Is MASTER needed for a pure BAR0 read? Recommendation: no
- A host read of BAR0 is a downstream Memory Read request; the EP answers with a completion. Bus Master Enable only gates requests the EP *originates* (DMA, MSI) (PCI spec, **guess**; not in the refs). DHD sets MASTER because it later does DMA ring buffers; it is not needed to read the chip id.
- MASTER is the riskier bit: an EP with MASTER on can issue upstream writes/reads (e.g. from a half-initialised firmware ring) into a host that has no inbound iATU/IOMMU set up. Skip until the first read worked; a later stage adds it.
- If the read returns UR/all-ones with MEM only, the next run adds MASTER (one more row, its own run). Do not add it speculatively.

## 2. Outbound iATU region 1 (requirement 2)
Existing shim practice for region 0 (the same RC, same block): ATU rows are 32-bit stores at `0x14B00000 + off` through `w32` -> `try_write32` (`wlanlink.c:280-308`), allowed only by `atu_ok` (`wlanlink.c:228-232`: offsets 0x000..0x018, value sets), generated in `gen.py:549-571` in Linux's order (`rc.c:1379-1386`), with a one-shot read-back of CR2 (`gen.py:570`) and region-0 teardown rows (`gen.py:629-634`). Region 1 uses the same layout 0x200 above (unrolled stride 0x200, `gen.py:617-618`; `rc.h:294-300`). No viewport registers (DBI 0x900 reads 0xFFFFFFFF = unrolled, observed). (read from source + observed)

Linux programs region 1 with `exynos_pcie_rc_prog_viewport_mem_outbound` (`rc.c:1389-1415`, called after region 0 at `rc.c:3356-3357`): CR1 := TYPE_MEM, LOWER_BASE := start, UPPER_BASE := start >> 32, LIMIT := start + SZ_2M - 1, LOWER_TARGET := start - offset, UPPER_TARGET, CR2 := ENABLE (last). `use-secure-atu = "false"` so the function is not skipped (`pcie.dtsi:90`; early return on `use_secure_atu` at `rc.c:1396-1398`). TYPE_MEM = 0 (`rc.h:195`), ENABLE = bit 31 (`rc.h:200`). (read from source)

| Reg | Offset from ATU base | Value for S29 | Source / reasoning | Restore |
|---|---|---|---|---|
| CR1 | +0x200 | stays `0x0` (= TYPE_MEM, equals the observed reset): precondition `wait_eq == 0`, NO write | `rc.c:1401`, `rc.h:195,294`; reset observed (`HO:218`) | none (never written) |
| LOWER_BASE | +0x208 | `0x60000000` | `rc.c:1402`, `rc.h:296`; CPU window = PCI range start (`pcie.dtsi:76`: ranges `0x82000000 0 0x60000000 0 0x60000000 0 0xFF0000`) | `0` |
| UPPER_BASE | +0x20C | stays 0 (precondition `wait_eq == 0`, no write) | `rc.c:1403`; reset observed | none |
| LIMIT | +0x210 | `0x6000FFFF` (= BAR0 observed 64 KiB, base + 0x10000 - 1) | Linux uses `start + SZ_2M - 1` (`rc.c:1408`); we deliberately cover only BAR0 (no parity claim); S26 convention LIMIT = base + size - 1 (`gen.py:557`, observed region-0 LIMIT `0x60FFFFFF`, n28c `R26 row=272`). A 4 KiB window (`0x60000FFF`) would also do (only offset 0 is read): guess, the whole BAR is the more natural bound | `0x00000FFF` (observed reset) |
| LOWER_TARGET | +0x214 | `0x60000000` (PCI address == CPU address: `ranges` above, offset 0 = guess from the equal numbers; it is also the value S28 wrote into the EP BAR0, observed) | `rc.c:1409-1410`, `rc.h:299` | `0` |
| UPPER_TARGET | +0x218 | stays 0 (precondition, no write) | `rc.c:1411-1413` | none |
| CR2 | +0x204 | `0x80000000`, LAST; one-shot read-back must be `0x80000000` | `rc.c:1414`, `rc.h:200,295`; read-back as S26 (`gen.py:570`) | `0` (FIRST teardown row of S29) |

Region 1 `[0x60000000, 0x6000FFFF]` does not overlap region 0 `[0x60FFE000, 0x60FFFFFF]` nor BAR2 `0x60400000` (read from source/observed addresses). Four ATU writes (LOWER_BASE, LIMIT, LOWER_TARGET, CR2) instead of Linux's seven: the three skipped registers are already at the value Linux would write (observed reset), so a precondition read replaces the write (fewer writes, same end state) (guess: the end state is what matters; DWC databook not in the refs).

## 3. Backplane window cfg 0x80 (requirement 3)
- Register: `PCI_BAR0_WIN` = cfg 0x80, "backplane address space accessed by BAR0" (`D/include_pcicfg.h:488`); `PCIE2_BAR0_WIN2` 0x70 maps the 2nd 4 KiB of BAR0 (`:633`), BAR0 window size 32 KiB for PCIe gen2 (`:638` `PCIE2_BAR0_WINSZ 0x8000`). (read from source)
- DHD writes it before the first chip access: `dhdpcie_bus_cfg_set_bar0_win(bus, si_enum_base(devid))` (`D/dhd_pcie.c:2158-2159`; the helper is `OSL_PCI_WRITE_CONFIG(PCI_BAR0_WIN, 4, data)`, `dhd_pcie.c:3107-3110`) and again in `si_doattach` (`D/siutils.c:2765-2769`: read `savewin`, if not a good core address use ENUM, then write `SI_ENUM_BASE`). `si_enum_base()` returns `SI_ENUM_BASE_DEFAULT` = `0x18000000` (`D/siutils.c:246-249`, `D/include_hndsoc.h:43`). Note: research 33 s.5 cited `dhd_pcie.c:2157-2158`; the exact lines are 2158-2159. (read from source)
- Why the observed value is `0x18001000` (explanation, **guess**): ChipCommon is the first core at `SI_ENUM_BASE` and each core gets `SI_CORE_SIZE = 0x1000` of register space (`D/include_hndsoc.h:64`), so `0x18001000` is the second core's register page; `0x18101000` in 0x70 = `SI_WRAP_BASE_DEFAULT 0x18100000` (`:47`) + 0x1000 = the wrapper of that same second core. So the chip's reset default maps BAR0+0 to the core after ChipCommon, not to ChipCommon; BAR0+0 would NOT read the chip id without the write. This is consistent with DHD always writing the window (it does not rely on the reset value) but which core sits in slot 1 is unknown.
- Write: cfg 0x80 := `0x18000000`, 32-bit store; read-back `wait_eq == 0x18000000` (one-shot). Precondition (S29 start): `wait_eq 0x80 == 0x18001000` (so the restore constant is true).
- Restore: cfg 0x80 := `0x18001000` (observed original), unconditional, second in the S29 teardown (after Command := 0).
- cfg 0x88 (`PCI_SPROM_CONTROL`, `D/include_pcicfg.h:490`) is read by DHD right after the window write and must not be all-ones (`D/dhd_pcie.c:2161-2169`); observed `0x00080080` before; log it again after the window write (a changed or all-ones value = stop). (read from source + observed)

## 4. The first BAR0 read (requirement 4)
- Address: CPU `0x60000000 + 0x0` (BAR0 base from S28, observed assigned and read back; window register points at `0x18000000`). Register: ChipCommon `chipid` at CC offset `0x0` (`D/include_sbchipc.h:219`, struct field comment `/* 0x0 */`); DHD reads it as `w = R_REG(osh, &cc->chipid)` with `cc = regs` = BAR0 mapping (`D/siutils.c:2809`, `:2762-2769`; comment "We assume we can read chipid at offset 0", `:2803-2805`). 32-bit load: `R_REG` on a `uint32` field. (read from source)
- Decode (`D/siutils.c:2814-2818`, masks `D/include_sbchipc.h:830-838`): id = `w & 0x0000FFFF`, rev = `(w & 0x000F0000) >> 16`, pkg = `(w & 0x00F00000) >> 20`, core count `(w & 0x0F000000) >> 24` (`CID_CC`), type = `w >> 28`. (read from source)
- Expected: id = `0x4389` (`BCM4389_CHIP_ID`, `D/include_bcmdevs.h:193`); device id in PCI config is also 0x4389/0x4441 family (cfg 0x2C = `0x438914E4`, observed). The upper 16 bits (rev, pkg, cc, type) are NOT predicted: log the whole word. **Expected, not observed.**
- Access width: a single aligned 32-bit load. The shim's `rd32` is `*(volatile uint32_t *)a` (`shim.c:77`), `try_read32` wraps it (`shim.c:138-148`). The mapping is Device-nGnRnE (`shim/mmu.c:1-12`), so the load is not speculated, merged or split. 8/16-bit loads are not needed. (read from source)
- Fault handling in the shim (read from source): `try_read32` sets `probe_guard`, loads, then `dsb sy; msr daifclr,#4; isb; msr daifset,#4` so a pending SError is taken inside the guard (`shim.c:138-148`); `exc_handler` with `probe_guard` set turns a Data Abort from the current EL (ESR EC 0x25, slot 4) into `probe_fault = 1`, records FAR and skips the instruction (`elr += 4`), and turns an SError (slot 7) into `probe_fault = 1` (`shim.c:985-999`). The caller then sees `ST_FAULT` and the run stops with teardown (`wlanlink.c:357-360,392-470`).
- UR / completer-abort behaviour on this SoC is **unobserved** (research 32 Q14 still open): three possibilities (guess): (a) all-ones data without a fault (the DWC core returns 0xFFFFFFFF for UR/CA) - then `try_read32` returns ok with `0xFFFFFFFF`: the chip-id `wait_eq` row (mask 0xFFFF == 0x4389) fails and the run stops via refusal (value was logged first); (b) synchronous external abort / SError - caught by the guard above; (c) completion timeout with no fault routed = bus stall: **not recoverable** by the guard; precedent: S09 stalled the whole phone, no fault (`docs/research/30-pcie1-phy-access-hang.md` s.2, run `w3a-n9`, observed). ITMON (the Exynos interconnect monitor) has an HSI2 hook in Linux (`rc.c:4652-4680`, registered `rc.c:5395`) so an interconnect-level error is a real outcome (read from source); the shim has no ITMON handler.
- The shim has no "all-ones means failure" rule: the table row after the read must check the value (draft below).

## 5. Risks and the stop-on-first-surprise order (requirement 5)
### 5.1 Does ChipCommon need PMU / clock state first? (cite)
- DHD's attach reads the chip id before any clock request: `dhdpcie_dongle_attach` -> write BAR0 window -> read SPROM control -> `si_attach` (`D/dhd_pcie.c:2092,2150-2181`); `si_buscore_prep` is a no-op for a PCIe host build (`BCMDONGLEHOST` is defined, `W/bcm4389-Kbuild:103`; the clock code in `si_buscore_prep` is `!BCMDONGLEHOST` or `BCMSDIO` only, `D/siutils.c:354-430`; `BCMSDIO` is only set for the SDIO config branches, `Kbuild:779,848`, PCIe branch `:801`). The chip is still at `alp_only = TRUE` at that point (`D/dhd_pcie.c:2146`), i.e. DHD does not raise HT before the chip id (HT is requested later, `:2294`). `dhd_dongle_attach` runs before the firmware download (call site `dhd_pcie.c:973`). So ChipCommon should answer from power-up/ROM state without firmware and without a PMU request. (read from source for the order; "answers at power-up" is the implication, **guess** until the read is observed)
- DHD waits `WIFI_TURNON_DELAY` after WL_REG_ON (`W/dhd_custom_google.c:564-566`); the shim has been up for minutes (S04 wait, tens of seconds in S20-S28), so settle time is not the issue.
- MULTIBP/power-request (`dhd_bus_pcie_pwr_req_nolock`, `dhd_pcie.c:2203-2204`) happens AFTER `si_attach` (after the chip-id read): not needed for the first read (read from source).

### 5.2 Hazards (guess unless a source is given)
- **R1 Bus stall / hang.** An outbound MEM read that gets no completion may stall the CPU and reset the phone (precedent `w3a-n9`, observed); the guard cannot help; the teardown never runs. Mitigations: only 1 read, window = BAR0 only, MEM enable is the last write before the read, short log flush before the read (`pstore_save` runs at each step start, `wlanlink.c:428`: put the read at the START of its own step so the last line names it, i.e. make the read S30, or use step S29 = setup, S30 = read; see the row table).
- **R2 Teardown after a faulting read.** If the read faults (UR/CTO/SError) the link or EP may be in error; the teardown then writes cfg registers through region 0 to a possibly dead link (a further stall or `restore=FAILED`). Order the teardown so the dangerous write is first: ATU1 CR2 := 0 (RC-local, cannot stall), then Command := 0, then the rest.
- **R3 MEM enable side effects.** With MEM on, the EP decodes the whole BAR0 (64 KiB) and BAR2 (4 MiB): only region 1 reaches BAR0 and nothing accesses BAR2 (region 1 limit = BAR0 end). Enabling MEM with BARs assigned (done by S28, observed) is the order the PCI spec wants (BARs before MEM, research 32 H2).
- **R4 Link retrain / L1.** EP ASPM is off (LNKCTL low half 0, observed); the EP is at Gen1 (LNKSTA `0x1011`) although LNKCAP says Gen2 (observed); Linux may retrain to Gen2 after enumeration, the shim does not. RC-side ASPM/L1/L1SS state: unobserved. If the link is in L1 the first TLP forces an exit (guess) adding latency; log LNKSTA (cfg 0xBC) before and after the read and treat a changed link status as a stop (post-read row logs it; a retrain mid-read would show as a changed value).
- **R5 Backplane window side effect.** The window write changes which core BAR0+0 reaches; reading ChipCommon with the chip in an odd state returns a backplane error (a "backplane timeout" is a completion with error status, guess). DHD does exactly this write+read at attach on a fresh chip (`D/siutils.c:2765-2809`).
- **R6 RC bridge windows (cfg 0x20-0x24 of the RC) and RC bus numbers are unprogrammed.** They gate downstream forwarding of TYPE-1 traffic, not iATU-originated TLPs (guess; research 31 Q5 and research 32 s.2d, both unverified; DWC databook not in the refs). If the first read returns all-ones with no AER on the EP, this is the first suspect; fix = a separate run adding research 32 s.2d E1-E3 (one run each).
- **R7 First-use of new write kinds.** New CFG offsets 0x04 and 0x80 and new ATU offsets 0x204-0x214 in the allowlist = hardware-writing code: independent review (Opus) and a local `wip/*` branch, as for S26/S28 (AGENTS.md hard rules).

### 5.3 Order (read-only first, arm last)
1. Preconditions (reads only): Command == 0; BAR0 == `0x60000004`/0; cfg 0x80 == `0x18001000`; region 1 CR2 disabled, CR1/UPPER_* == 0; log LNKSTA(0xBC), SPROM(0x88), AER UESta(0x104).
2. RC-local writes: ATU region 1 LOWER_BASE, LIMIT, LOWER_TARGET, then CR2 enable with a one-shot read-back (still no EP-visible effect: nobody accesses the window and the EP Command is 0).
3. EP config write: cfg 0x80 := `0x18000000` + read-back (harmless to the bus; decode still off).
4. ARM: cfg 0x04 := `0x00000002` + one-shot read-back (the only step that makes the EP answer MEM).
5. The read (own step, so the pstore checkpoint names it): probe read (block-probe flag) -> log row -> log LNKSTA/AER -> one-shot check id == 0x4389.
6. Teardown: ATU1 CR2 := 0; Command := 0; cfg 0x80 := `0x18001000`; the four S28 BAR rows; ATU1 LOWER_BASE/LIMIT/LOWER_TARGET to reset; the existing ATU0 + power rows.
MASTER is never written in S29.

## 6. Drafted S29 rows (requirement 6; format of `gen_wlan_seq.py --draft28`: dicts `kind, blk, off, mask, value, cite, tag, note, restore`; generator NOT edited)
Notation: `FULL` = 0xFFFFFFFF. "1x" = one-shot (`polls=1`) `wait_eq`/`wait_mask`; a failing one-shot is a refusal -> stop + teardown (`wlanlink.c:367-369`). Blocks: ATU = `0x14B00000`, CFG = `0x60FFE000`, and ONE NEW block `MEM` = `0x60000000`, size 0x1000, read-only (new `WLB_MEM` in the table, `base_of`/`size_of`/`need_probe`, `wlanlink.c:136-178`). Step split proposal: **S29 = rows 0-17 (setup + arm), S30 = rows 18-22 (the read)**, so `pstore_save` + the `LINK S30 start` line precede the only risky access (`wlanlink.c:426-428`; pstore_save is line 428). Bump `last_step` to 30 (`gen.py` `finish()`, `audit.py:2863-2864`, `shim/build.sh:113-114,422-423`).

| # | kind | blk | off | mask | value | Tag | Cite / note | restore key |
|---|---|---|---|---|---|---|---|---|
| 0 | wait_eq 1x | CFG | 0x04 | 0xFFFF | 0 | observed | precondition: Command == 0 (n28c `R26 row=284`; S28 row 0 pattern) | |
| 1 | wait_eq 1x | CFG | 0x10 | FULL | 0x60000004 | observed | S28 assignment latched (n28c, in-table read-back) | |
| 2 | wait_eq 1x | CFG | 0x14 | FULL | 0 | observed | BAR0 high still 0 | |
| 3 | wait_eq 1x | CFG | 0x80 | FULL | 0x18001000 | observed | precondition = the restore constant (n28c `R27 row=313`) | |
| 4 | wait_eq 1x | ATU | 0x204 | 0x80000000 | 0 | observed | region 1 disabled (reset, `HO:218`) | |
| 5 | wait_eq 1x | ATU | 0x200 | FULL | 0 | observed | CR1 = TYPE_MEM already (`rc.h:195`) | |
| 6 | wait_eq 1x | ATU | 0x20C | FULL | 0 | observed | UPPER_BASE 0 | |
| 7 | wait_eq 1x | ATU | 0x218 | FULL | 0 | observed | UPPER_TARGET 0 | |
| 8 | mmio_write | ATU | 0x208 | FULL | 0x60000000 | read from source | `rc.c:1402`, `rc.h:296` | atu1_lbase_rst (0) |
| 9 | wait_eq 1x | ATU | 0x208 | FULL | 0x60000000 | guess | read-back (Linux has none, `rc.c:1379-1386`; own gate) | |
| 10 | mmio_write | ATU | 0x210 | FULL | 0x6000FFFF | read from source / guess | `rc.c:1408` (Linux 2 MiB; ours = BAR0 64 KiB, observed size) | atu1_limit_rst (0xFFF) |
| 11 | wait_eq 1x | ATU | 0x210 | FULL | 0x6000FFFF | guess | read-back | |
| 12 | mmio_write | ATU | 0x214 | FULL | 0x60000000 | read from source / guess | `rc.c:1409-1410`; offset 0 = guess | atu1_ltarget_rst (0) |
| 13 | wait_eq 1x | ATU | 0x214 | FULL | 0x60000000 | guess | read-back | |
| 14 | mmio_write | ATU | 0x204 | FULL | 0x80000000 | read from source | `rc.c:1414`, `rc.h:200`; LAST ATU write | atu1_cr2_off (0) |
| 15 | wait_eq 1x | ATU | 0x204 | FULL | 0x80000000 | guess | region 1 enabled (S26 row 281 pattern) | |
| 16 | mmio_write | CFG | 0x80 | FULL | 0x18000000 | read from source | `D/dhd_pcie.c:2158-2159`, `D/siutils.c:2769`, `D/include_hndsoc.h:43` | bar0win_rst (0x18001000) |
| 17 | wait_eq 1x | CFG | 0x80 | FULL | 0x18000000 | guess | read-back | |
| 17a | read_only (log) | CFG | 0x88 | FULL | | read from source | `D/dhd_pcie.c:2165`: DHD aborts if all-ones; observed before `0x00080080` | |
| 17b | read_only (log) | CFG | 0xBC | FULL | | observed | LNKCTL/LNKSTA before | |
| 17c | read_only (log) | CFG | 0x104 | FULL | | guess | EP AER UESta before (AER base 0x100 observed header, `pci_regs.h:755`) | |
| 18 | mmio_write | CFG | 0x04 | FULL | 0x00000002 | guess | ARM: MEM only (`pci_regs.h:42`); status half 0 | cmd_rst (0) |
| 19 | wait_eq 1x | CFG | 0x04 | 0xFFFF | 0x0002 | guess | Command latched | |
| 20 | **S30** read_only probe (a=1) | MEM | 0x0 | FULL | | guess | block probe = the first MEM TLP: ChipCommon `chipid` (`D/include_sbchipc.h:219`, `D/siutils.c:2809`); fault -> stop | |
| 21 | **S30** read_only log (a=2) | MEM | 0x0 | FULL | | guess | prints `R30 row=.. rd=0x..`; decode id/rev/pkg (`siutils.c:2814-2818`) | |
| 22 | **S30** read_only log | CFG | 0xBC | FULL | | guess | link after | |
| 23 | **S30** read_only log | CFG | 0x104 | FULL | | guess | EP AER UESta after (UR bit 20, CTO bit 14) | |
| 24 | **S30** wait_eq 1x | MEM | 0x0 | 0xFFFF | 0x4389 | guess | `BCM4389_CHIP_ID` `D/include_bcmdevs.h:193`; mismatch (incl. 0xFFFFFFFF) = refusal | |
(17a-c are numbered as rows between 17 and 18 to keep the arm write adjacent to its check; the implementer renumbers.)

Teardown rows (new, `requires` = 29, then 30 for none; unconditional, idempotent; ORDER matters). They go before the existing 4 S28 BAR rows (`gen.py:626-628`) except the ATU1 resets, which go next to the ATU0 resets (`gen.py:629-634`):
| T | kind | blk | off | value | Note |
|---|---|---|---|---|---|
| T1 | mmio_write | ATU | 0x204 | 0 | region 1 DISABLE: FIRST teardown row (RC-local) |
| T2 | mmio_write | CFG | 0x04 | 0x00000000 | Command := observed original |
| T3 | mmio_write | CFG | 0x80 | 0x18001000 | BAR0 window := observed original |
| (existing) | CFG 0x10,0x14,0x18,0x1C | | | 4,0,4,0 | S28 BAR restores |
| T4 | mmio_write | ATU | 0x208 | 0 | LOWER_BASE reset |
| T5 | mmio_write | ATU | 0x210 | 0x00000FFF | LIMIT reset (observed) |
| T6 | mmio_write | ATU | 0x214 | 0 | LOWER_TARGET reset |
| (existing) | ATU0 CR2 := 0 and resets | | | | |
Expected log: `LINK S29 ok rows=..`, `LINK S30 start`, `WLAN LINK R30 row=.. rd=0x????4389`, `LINK summary reached=S30 stopped=none restore=ok`. Run form (same as `HO:236-251`): `tools/phone-lock run --owner wifi -- tools/probe-run --build-args "--pstore-fast --wlan-link=30" --expect "LINK summary reached=S30" --label w3a-n30 --timeout 600` (>=600 s: AP-14; run in the background with an explicit timeout, `HO:249`).

## 7. What the independent allowlist must permit (and nothing else)
All in `wlanlink.c` (independent of the table) plus its twin in `audit.py` (`_WLAN_S28_*` at `audit.py:2807-2809`, CFG/step checks `:2909`, counts `:2964`), and host tests.
1. `atu_ok` (`wlanlink.c:228-232`), full-word writes only (`mask == FULL`, `wlanlink.c:272`): extend with `0x204` in {0x80000000, 0}, `0x208` in {0x60000000, 0}, `0x210` in {0x6000FFFF, 0x00000FFF}, `0x214` in {0x60000000, 0}. **Not** 0x200/0x20C/0x218 (never written), nothing above 0x218. Better: state-dependent like `cfg_ok` (the enable 0x80000000 only when LOWER_BASE/LIMIT/LOWER_TARGET already hold the S29 values; the disable and resets from any state).
2. `cfg_ok` (`wlanlink.c:237-250`) currently refuses every offset except the four BAR dwords; add: offset `0x04`: value 0x00000002 only if `old & 0xFFFF == 0` and BAR0 low reads `0x60000004` (needs one more guarded CFG read inside `w32`, like the existing Command read at `wlanlink.c:302`), value 0x00000000 from any state; offset `0x80`: value 0x18000000 only if `old == 0x18001000`, value 0x18001000 from any state. Nothing else (never 0x88, never cfg 0x20-0x24, never MASTER 0x4, no status-half bits set in a 0x04 write: `(v & 0xFFFF0000) == 0`).
3. Block MEM: reads only, offset 0 only (`r32` must refuse every other offset and every write kind; `w32_allowed` has no MEM case, `wlanlink.c:253-276`), reachable only after `probed` has the MEM bit (`wlanlink.c:349-350`) AND after the allowlist-independent precondition that region 1 reads back enabled and Command low 16 bits read 0x0002 (the CFG probe path; else a read of an unmapped window could stall). The mask check `(v & 0xFFFF) == 0x4389` is a table row (not an allowlist item).
4. `w32` must keep the single `try_write32` call site and the old/cmd reads (`wlanlink.c:280-308`); MASTER (0x4 bit) must be unreachable: `cfg_ok` rejects any 0x04 value other than 0 and 2.
5. Host test additions (AP-13): N=29 and N=30 in `shim/build.sh:422-423`; fake SoC models of region 1, cfg 0x80/0x04, MEM block (id 0x4389; mutants: all-ones read, read fault, stall-free refusal); table mutants for each new row (drop the T1-T3 rows, MASTER value, wrong LIMIT).

## 8. Open / guess
1. UR/CA/CTO behaviour of an outbound MEM read on this SoC (all-ones vs abort vs stall) - unobserved (research 32 Q14); settled only by the run itself. A stall costs one phone reset.
2. Whether the RC command register / bus numbers / bridge memory window (DBI 0x04, 0x18, 0x20-0x24) must be set for iATU-originated MEM TLPs (research 31 Q5, 32 Q13): unverified; DWC databook not in refs. Settle: S30 result; if all-ones with no EP AER, add E1-E3 one per run.
3. The value of upper 16 bits (rev/pkg/cc/type) of the chip id word: not in the refs; log it.
4. Which core sits at `0x18001000` (reset window): guess "slot 1 = next core"; settle by logging a BAR0 read BEFORE the window write (a second probe read; not recommended as it exercises the same unknown first).
5. RC-side ASPM/L1/L1SS state and Gen2 retrain: unobserved; the EP side is 0 (observed). Settle: read DBI link-control (0x70+0x10 per PCIe cap) in a later read-only step.
6. That the 32 MiB block of `0x60000000` is Device-nGnRnE like `0x60FFE000`: read from `mmu.c` header only, not re-checked in the page-table builder code; settle by reading the L2/L3 setup in `shim/mmu.c`.
7. That a zero status half in a 32-bit cfg 0x04 write leaves the status unchanged (RW1C semantics): PCI spec knowledge, not in the refs. The 16-bit store alternative needs a new CFG write width in the shim (`dw.c:155-156` shows Linux does it) and is NOT proposed.
8. DWC behaviour when LIMIT is not 4 KiB aligned beyond base (ours is `…FFFF`, aligned) and that skipping the CR1/UPPER_* writes equals Linux's end state: guess (reset values observed 0, TYPE_MEM = 0).
9. MASTER enable: deliberately not in S29; whether the EP answers reads without it: PCI spec guess (1.1).
10. `HANG` vs timeout: use `--timeout 600` (AP-14); a read that stalls shows as the last line `LINK S30 start`.
11. Corrections to earlier notes: research 33 s.5 said BAR0 32 KiB LIMIT `0x60007FFF` and "cfg 0x80 write only if readback differs"; observed BAR0 is 64 KiB and 0x80 = `0x18001000` != `0x18000000`, so the write is mandatory and the LIMIT proposal here is `0x6000FFFF`.

## 9. Implementer steps (no code)
1. Branch `wip/w3a-s29` off `wip/w3a-wlan-link`; symlink `tools/third_party`, `research`, `upstream` if in a worktree (AP-11); `git add` new files before the gate (AP-12).
2. `soc/google/gs101/soc.json`: add block `mem` = `0x60000000` (pinned like WLAN_CFG with a `_Static_assert` in `wlanlink.c`); extend `tools/boards.py` `wlan_seq.h` generation (`WLB_MEM`, `WLAN_MEM`), `BLOCKS` in `gen.py:38`.
3. `tools/gen_wlan_seq.py`: add `draft29()` with the rows of s.6 (anchors on `rc.c:1402-1414`, `rc.h:195,200,295-300`, `D/dhd_pcie.c:2158-2159`, `D/siutils.c:2769,2809,2814-2818`, `D/include_bcmdevs.h:193`, `D/pci_regs.h:42,755`), the teardown rows T1-T6 in the order of s.6, `STEP_NAMES[29]`/`[30]`, `last_step` 30, new restore keys; regenerate `wlan_seq.json` (new sha pin).
4. `shim/wlanlink.c`: MEM block (`base_of`, `size_of`, `need_probe`, `r32` offset 0 only, no write), extend `atu_ok` and `cfg_ok` exactly as in s.7 (including the extra BAR0 read for the 0x04 arm rule), keep one `try_write32` site.
5. `tools/test-bluejay-boot.py`: update the audit twins (`_WLAN_S28_*`, step coverage 0..30, CFG offset/value sets, ATU region-1 sets, MEM read-only/offset-0 rule, row counts) WITHOUT loosening any existing check.
6. `shim/build.sh`: allow N<=30 (`:113-114`), add 29 and 30 to the host-test loop (`:422-423`); `shim/wlanlink_host_test.c`: fake region 1 / cfg 0x04 / 0x80 / MEM models and the mutants of s.7 item 5.
7. `tools/check-all.sh --quick` then the full gate (driver), independent review (Opus 5.5, hardware-writing, adversarial), only then the run (command in s.6); read the `R30` line, tick W-A7 and WIFI-COVERAGE with the run label; update `docs/HANDOFF.md`/`PROGRESS.md`.
8. If the read returns all-ones: do NOT add MASTER first; read EP AER (`R30` rows), then plan E1-E3 (research 32 s.2d) one per run.

## 10. Verification (this session)
Opened: `docs/research/33` (all), `32` s.2c-2f, 3, 4, 6; `shim/wlanlink.c` 1-487; `shim/shim.c` 77, 88-165, 970-1015; `shim/mmu.c` 1-24; `tools/gen_wlan_seq.py` 549-571, 612-725; `soc/google/gs101/wlan_seq.json` rows 278-321 (printed); `n28c/usb.txt` (R26-R28 lines); `W/dwc-whi/pcie-exynos-rc.c` 1314-1330, 1389-1415, 3340-3360, 4652-4690; `rc.h` 190-204, 284-312; `D/dhd_pcie.c` 2088-2215, 3100-3115, 968-976; `D/dhd_pcie_linux.c` 1272-1283, 1940-1972; `D/siutils.c` 240-262, 350-430, 2735-2830; `D/include_pcicfg.h` 484-512, 625-640; `D/include_hndsoc.h`, `include_sbchipc.h` 219, 826-840, `include_bcmdevs.h` 193, `pci_regs.h` 38-56, 755-788; `W/dhd_custom_google.c` 562-568; `W/bcm4389-Kbuild` grep hits 103, 239, 779, 801, 848; `tools/test-bluejay-boot.py`, `shim/build.sh` grep hits for the cited line numbers. Not re-opened: `dw.c:155-156`, `HO:236-251` beyond the quoted lines, `docs/research/30` beyond s.1-2.
