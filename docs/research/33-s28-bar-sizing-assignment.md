# 33 - S28: BAR sizing + assignment, then (S29) EP command, outbound MEM iATU region 1 and the first BAR0 read

Status: READ-ONLY research, 2026-10-23, goal W-A6a. No code on the shim changed, no phone access. Builds on research 31/32 (super repo `docs/research/`) and the observed run `w3a-n27`.
Tags: **observed** (run `w3a-n27`), **read from source** (file:line, re-opened this session), **guess**.
Names: `D` = `~/pixelios-artifacts/refs/wlan-stage2` (`dhd_pcie.c`, `dhd_pcie_linux.c`, `siutils.c`, `include_pcicfg.h`, `pci_regs.h`, `linux_pci_probe.c` = `kernel/gs` `drivers/pci/probe.c` of `android-gs-bluejay-5.10-android13`, fetched 2026-10-23, sha256 `816b5395d48c...`, reference only);
`pcie.dtsi` = `wlan-stage1/dts-gs101-gs101-pcie.dtsi`; `probe.c` = `D/linux_pci_probe.c`.

## 1. Decision: S28 = sizing + assignment only (no MEM enable, no iATU region 1)
Sizes are unknown (BAR0/BAR2 read `0x4` = unsized 64-bit memory BARs, observed w3a-n27), and the table is static, so S28 measures them with a **guard** instead of hard-coding:
the assigned addresses are chosen so they are valid for ANY size <= 4 MiB, and the run stops (with restore) if a BAR reports a larger size. S28 never writes Command (`+0x04`): decode stays off, so no MEM TLP can be claimed.
The command write, ATU region 1 and the read are S29 (goal W-A7), parameterised with the sizes S28 returns.

## 2. Facts (every one re-opened)
1. Linux sizing order, per BAR: read Command, clear MEM|IO if set; read BAR `l`; write `l | ~0`; read `sz`; write `l` back (`probe.c:188-200`); for a 64-bit BAR (flag from type bits `0x4`, `pci_regs.h:108`): read the high dword, write `~0`, read, write the original (`probe.c:239-242`); restore Command (`probe.c:249-250`). Size = lowest set bit of `sz & ~0xF` over the combined 64 bits (`probe.c:110-118`). (read from source)
2. Our start state: Command = 0 (so Linux's clear/restore is not needed, but it is checked as a precondition, observed w3a-n26/27), BAR0 = BAR2 = `0x00000004`, BAR1 = BAR3 = 0 (observed w3a-n27). `0x4` = memory space, type 64-bit, non-prefetchable.
3. DHD expects BAR0 = PCI resource 0 (mapped 32 KiB, `D/dhd_pcie.h:41`, `dhd_pcie_linux.c:1951,1964`) and the TCM BAR = resource 2 (mapped max(size, 4 MiB), `dhd_pcie.h:42`, `dhd_pcie_linux.c:1952-1967`); it fails if resource 2 has size 0 or start 0 (`dhd_pcie_linux.c:1957-1960`). So both BARs must be assigned. (read from source)
4. Address space for them: PCI MEM range `0x60000000..0x60FEFFFF`, PCI address == CPU address (`pcie.dtsi:75-76`), config window at `0x60FFE000` above it (no overlap). (read from source; "offset = 0" for the later iATU target derives from the equal addresses)
5. BAR layout chosen: **BAR0 at `0x60000000`, BAR2 at `0x60400000`.** For sizes <= 4 MiB both are naturally aligned (a BAR must sit on a multiple of its size, PCI spec: guess), BAR0 ends <= `0x603FFFFF` < BAR2, BAR2 ends <= `0x607FFFFF` < `0x60FEFFFF`. Guard: after writing all-ones the low dword must read bits `[31:22]` all 1 (size <= 4 MiB; `wait_mask` mask `0xFFC00000`) and the high dword must read `0xFFFFFFFF` (size < 4 GiB). If not, STOP + restore, and design a bigger layout from the measured size.
6. The attach code writes `PCI_BAR0_WIN` (cfg `0x80`, `include_pcicfg.h:488`) := `SI_ENUM_BASE` before the first BAR0 read (`dhd_pcie.c:2157-2158`, `siutils.c:2769`); observed value now `0x18001000` (w3a-n27), so S29 needs that ONE config write (see 5). (read from source + observed)

## 3. S28 rows (drafted by `tools/gen_wlan_seq.py --draft28`, NOT in `wlan_seq.json`; 27 rows, all block CFG = window `0x60FFE000`)
| # | Row | Kind | Offset | Value | Restore / guard |
|---|---|---|---|---|---|
| 0 | precondition Command == 0 | wait_eq | 0x04 | mask 0xFFFF == 0 | refuse otherwise |
| 1-2 | BAR0 low == 0x4, high == 0 | wait_eq | 0x10, 0x14 | observed originals | refuse otherwise |
| 3 | BAR0 low := 0xFFFFFFFF | mmio_write | 0x10 | all ones | teardown: := 0x4 |
| 4 | guard size <= 4 MiB | wait_mask | 0x10 | mask 0xFFC00000 | STOP + restore |
| 5 | log sizing readback | read_only | 0x10 | | decode size = ~(v & 0xFFFFFFF0)+1 |
| 6 | BAR0 low := 0x4 | mmio_write | 0x10 | original | (Linux order, `probe.c:200`) |
| 7-10 | same for high half | | 0x14 | all ones, guard `0xFFFFFFFF`, log, := 0 | teardown: := 0 |
| 11-20 | same sequence for BAR2 | | 0x18 / 0x1C | | |
| 21-23 | BAR0 low := `0x60000004`, read back low and high | mmio_write + 2 read_only | 0x10, 0x14 | address + type bits as read | teardown: := 0x4 |
| 24-26 | BAR2 low := `0x60400004`, read back low and high | | 0x18, 0x1C | | teardown: := 0x4 |
Type bits are written as read (`0x4`) because they are read-only (guess, PCI spec) and the readback then has a single expected value. High halves are never written during assignment (they stay 0: both addresses are < 4 GiB).
**Teardown (new rows, to be generated in W-A6b):** CFG `0x10 := 4`, `0x14 := 0`, `0x18 := 4`, `0x1C := 0` as the FIRST teardown rows, i.e. before the existing ATU region 0 disable (the window dies with it, `gen_wlan_seq.py` teardown T01). Teardown must be idempotent against a mid-run stop (writes the observed originals unconditionally).
**Allowlist (independent of the table, W-A6b):** CFG writes only at offsets `0x10,0x14,0x18,0x1C`; per-offset value set {all ones, observed original, assigned address}; and only while the previous value is in the expected transition (e.g. all ones only when the register holds its original); never offset `0x04` in S28.

## 4. Hazards (guess unless a source is given)
- H1 BAR holding all ones while Command.MEM = 0 claims nothing; the precondition row forbids the sequence if Command != 0 (`probe.c:188-191` is why Linux does the same).
- H2 The first config WRITE through the window is a new write path (S26 only read): read-back after every write is in the rows; a write completion problem would show as a wrong readback (Q16 of research 32).
- H3 A mid-run stop between rows 3 and 6 leaves a BAR at all ones until teardown writes the original: the teardown rows are mandatory and tested by a host mutant (drop the BAR teardown rows -> test fails).
- H4 BAR2 may be larger than 4 MiB (DHD maps >= 4 MiB: `dhd_pcie.h:42`): the guard stops the run; this is a result, not a failure (budget: 2 runs).
- H5 Linux also programs the root-port bridge windows (cfg `0x20-0x24`); not done in S28 and not needed for outbound iATU traffic (research 31 Q5, still unverified): decide with the S29 read result.

## 5. S29 sketch (goal W-A7, parameterised by the S28 sizes S0 for BAR0)
Order: (1) ATU region 1 per research 32 s.2e with BASE/TARGET `0x60000000` and **LIMIT = `0x60007FFF`** (32 KiB = DHD's BAR0 map, `dhd_pcie.h:41`; Linux uses 2 MiB `rc.c:1408`, so no parity claim), CR1 `0x0`, CR2 enable last; (2) EP Command 16-bit `0x0002` (MEM only; MASTER not needed for a read; `pci_regs.h:42-43`); (3) cfg `0x80 := 0x18000000` (only if the readback differs; DHD cite 2.6); (4) one guarded `readl(0x60000000)` = chipcommon `chipid`, expect bits `[15:0] = 0x4389` (`include_bcmdevs.h:193`, `dhd_pcie.c` attach order); teardown order: ATU1 CR2 := 0, `0x80` back to its observed value `0x18001000`, Command := 0, BARs := originals, then the existing rows. Open: Q13/Q14 of research 32 (does a MEM read complete with RC command/bus numbers unwritten; what a UR/CTO does on this SoC - guarded read helper).

## 6. Verification
Re-opened this session: `probe.c` 95-130, 176-260 (sizing); `dhd_pcie_linux.c` 1940-1970; `dhd_pcie.h` 41-42; `dhd_pcie.c` 2148-2164; `siutils.c` 2750-2792; `include_pcicfg.h:488`; `pci_regs.h` 40-43, 96-110; the generator's S26/S27 blocks and teardown; HANDOFF "Session 23 (cont.)" for the observed values. The draft rows are produced by the generator with anchors (a stale cite fails the run of `--draft28`). `--check` confirms `wlan_seq.json` is unchanged (table hash pin untouched).

## 7. Implemented (W-A6b, 2026-10-23) and review 9 (Opus 5.5: SAFE WITH CONDITIONS)
S28 is in the table (377 rows), `wlanlink.c` `cfg_ok` is the independent CFG allowlist, 4 BAR restore rows are the FIRST teardown rows. Review conditions: M2 applied (assignment and high read-backs are one-shot `wait_eq` checks, exact for sizes <= 4 MiB). M1 (teardown overwrites a BAR S28 never wrote when the precondition refuses) NOT applied: the sizing readback depends on the unknown BAR size, so any tighter restore condition could block the restore after a size-guard stop; restore-always is the safe side (the endpoint was just reset by this run's PERST, Command is 0, PERST low follows). Added hazard H6: the first Type 0 config write makes the endpoint latch its bus/device number (PCIe spec; harmless). PCI spec facts (BAR type bits read-only, size alignment) remain tagged guess here until cited.
