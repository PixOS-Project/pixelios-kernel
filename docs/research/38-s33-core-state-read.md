# 38 - S33: read-only core state (ARM CA7, SYS_MEM, PMU) through the cfg 0x80 BAR0 window (goal W-C2, research half)

Status: READ-ONLY desk research, 2026-10-24. No code, table or tool changed; no phone access. Builds on research 37 (`37-bcm4389-fw-download-protocol.md`) and the observed runs `w3a-n31`, `w3a-n32b`.
Tags: **[src: file:line]** = line opened this session; **[obs]** = observed on hardware (log dir) or on a host file; **[guess]**.
Names: `D` = `~/pixelios-artifacts/refs/wlan-stage2/` (Google DHD; **`aidmp.h` is present there now**, it was missing when research 37 was written), `B` = `~/pixelios-artifacts/refs/wlan-stage3/` (Linux v6.12 brcmfmac, `B/brcmfmac-chip.c`, `B/brcmfmac-pcie.c`, `B/bcma_regs.h`), `K` = kernel clone, `L32` = `K/logs/probe/20261024T102654Z-w3a-n32b_`, `L31` = `K/logs/probe/20261024T083141Z-w3a-n31_`.

## 1. Decision: cfg 0x80 (BAR0 window, 4 KiB page), not the cfg 0x70 wrapper window

**Pick cfg 0x80 := page base of the target page, read the registers at BAR0 + (offset in page).** Reasons:
1. Already proven on this chip: S29 wrote 0x18000000, S32 wrote 0x18105000 and read the whole EROM page through it, both with read-back and `restore=ok` [obs: `L32/usb.txt:702-935`, summary `reached=S32 stopped=none restore=ok`; `K/soc/google/gs101/wlan_seq.json` rows 417, T01]. The first cfg 0x80 value seen was `0x18001000` (S27 [obs, research 37 s.0]).
2. 4 KiB alignment is what both reference drivers use: brcmfmac masks every address with `~(BRCMF_PCIE_BAR0_REG_SIZE-1)`, `BRCMF_PCIE_BAR0_REG_SIZE = 0x1000`, and writes it to `BRCMF_PCIE_BAR0_WINDOW = 0x80` [src: `B/brcmfmac-pcie.c:1832-1841`, `:118-119`]; DHD uses `SI_CORE_SIZE = 0x1000` pages [src: `D/include_hndsoc.h:64`, via research 37 s.1.2]. The EROM address-descriptor base is bits 31:12, so every core/wrapper base from the EROM is 4 KiB aligned [src: `B/brcmfmac-chip.c:56-57` via research 37 s.1.3; confirmed by the decoded bases below, all with low 12 bits 0]. The wrapper pages are in the same 0x181xxxxx AXI region as the EROM page 0x18105000 that S32 already read [obs]. brcmfmac itself reads wrapper registers this way: `read32(ctx, core->wrapbase + BCMA_IOCTL)` through the 4 KiB window in `brcmf_chip_ai_iscoreup` [src: `B/brcmfmac-chip.c:271-275`]. Whether the hardware window also accepts an arbitrary page in 0x18xxxxxx without side effects is observed only for 0x18000000, 0x18001000, 0x18105000 [obs]; the four new values are [guess] until S33 runs.
3. The cfg 0x70 window is DHD's second window (`PCIE2_BAR0_WIN2 = 0x70` "map 2nd 4KB of BAR0" [src: `D/include_pcicfg.h:633`], wrapper then at BAR0+0x1000, `D/include_pcicfg.h:640`, `D/aiutils.c:555-587`). It has never been written on this chip (cfg 0x70 = 0x18101000 never changed [obs, research 37 s.0]), would need a second window register in the allowlist, a second restore and a BAR0+0x1000.. read range. It buys nothing here: with cfg 0x80 the wrapper register offsets 0x408/0x800/0x804 are < 0x1000, inside the page. DHD's own `ai_dumpregs` also reads wrappers through a second window only because it wants core and wrapper at once [src: `D/aiutils.c:1455-1475`]. We do not.
Side effect to keep: while cfg 0x80 is on a non-ChipCommon page, every BAR0 offset read in the page is a read of that page. The existing MEM guard (`shim/wlanlink.c:203-206`, window read first, per-window offset rule) must grow per-window EXACT offset lists (not the S32 rule `off < 0xE00`, which would allow wrapper offsets like 0x400/0x404 `ioctrlset/ioctrlclear` [src: `D/aidmp.h:166-167`] whose read behaviour is unknown).

## 2. Core bases (from the EROM, observed)
`python3 -I K/tools/erom-decode.py L32` [obs, RESULT: PASS 36 components, EOT seen]:

| Core | id | rev | base | wrapper |
|---|---|---|---|---|
| ARM_CA7 | 0x847 | 9 | 0x18020000 | **0x18120000** (nmw=1, nsw=0) |
| SYS_MEM | 0x849 | 9 | **0x18026000** | **0x18126000** |
| PMU | 0x827 | 40 | **0x18012000** | none (nmw=nsw=0) |
| ChipCommon | 0x800 | 70 | 0x18000000 | 0x18100000 |

Separate PMU core is confirmed: CC `capabilities_ext` (CC+0xAC) = `0x000A044E` [obs: `L31/usb.txt` `R31 row=410`], bit 0x40 (`CC_CAP_EXT_AOB_PRESENT` [src: `D/include_sbchipc.h:872`]) is set (0x44E & 0x40 = 0x40, my decode). With AOB the PMU registers are in the PMU core and the CC copy is "deprecated and removed" [src: `D/include_sbchipc.h:46-51` (comment block above the AOB `pmuregs_t`)]; DHD reads them via `pmu + offset` of the PMU core [src: `D/siutils.c:448-460`]. So the PMU registers below are read at PMU core base + offset, not at CC+0x600.
Start-state value for U6 (already in research 37): clk_ctl_st `0x02010240` [obs `L31 row=409`]: bit 16 ALPAVAIL set, bit 17 HTAVAIL clear (my decode of `D/include_hndsoc.h:296-312`).

## 3. Window list and the registers read inside each (all 32-bit loads, no write other than cfg 0x80)
Wrapper register offsets: IOCTL 0x408, RESET_CTL 0x800, RESET_ST 0x804 [src: `B/bcma_regs.h:29,41,43`; DHD little-endian build `D/aidmp.h:316-321` `AI_IOCTRL 0x408, AI_RESETCTRL 0x800, AI_RESETSTATUS 0x804`; struct comments `D/aidmp.h:168,175,176`]. The big-endian `#if` variant at `D/aidmp.h:305-312` swaps them for BCMHND74K only; arm64 LE is the `#else` branch. RESET_CTL bit 0 = reset (`AIRC_RESET 1` [src: `D/aidmp.h:372`; `B/bcma_regs.h:42`]). IOCTL bits: CLK 0x1, FGC 0x2, core bits 0x3FFC, PME_EN 0x4000, BIST_EN 0x8000 [src: `B/bcma_regs.h:30-34`; `D/include_hndsoc.h:262-267`]. A core is "up" iff `(IOCTL & (FGC|CLK)) == CLK` and `(RESET_CTL & RESET) == 0` [src: `D/aiutils.c:936-937`; `B/brcmfmac-chip.c:271-275`].

| # | cfg 0x80 := | Reads (BAR0 offset, name, cite) | Reading the result |
|---|---|---|---|
| W1 | `0x18012000` (PMU core) | +0x600 `pmucontrol`, +0x604 `pmucapabilities`, +0x608 `pmustatus`, +0x618 `min_res_mask`, +0x61C `max_res_mask` [src: `D/include_sbchipc.h:55-62` (AOB `pmuregs_t`, PAD[384] puts the first field at 0x600)] | caps low byte = pmurev (`PCAP_REV_MASK 0xFF`, `pmurev = pmucaps & PCAP_REV_MASK` [src: `D/include_sbchipc.h:1961`, `D/siutils.c:529`]); good = `0x28` (40), the EROM component rev [obs, `erom-decode`]; whether pmurev equals the EROM rev is a **[guess]**. pmustatus: ALPAVAIL 0x8, HTAVAIL 0x4, SBCLKST 0x30 [src: `D/include_sbchipc.h:1947-1959`]. Bad = 0xFFFFFFFF (no completion) |
| W2 | `0x18120000` (ARM_CA7 wrapper) | +0x408 IOCTL, +0x800 RESET_CTL, +0x804 RESET_ST | U7: CPUHALT = IOCTL bit 0x20 [src: `B/brcmfmac-chip.c:78`; DHD value **[guess]** 0x20, `grep CPUHALT D/*.h` is empty]; RESET_CTL bit 0 set = core held in reset. "ARM up and halted" = IOCTL & 0x23 == 0x21 and RESET_CTL == 0. Any combination is a valid outcome and is only logged |
| W3 | `0x18126000` (SYS_MEM wrapper) | +0x408 IOCTL, +0x800 RESET_CTL, +0x804 RESET_ST | U3 "up?": up iff IOCTL & 3 == 1 and RESET_CTL & 1 == 0 [src: `D/aiutils.c:936-937`] |
| W4 | `0x18026000` (SYS_MEM core), **only if W3 says up** | +0x000 `coreinfo` [src: `B/brcmfmac-chip.c:165-166` (`struct sbsocramregs`, coreinfo first field; `SYSMEMREGOFFS` uses the same struct `:203`); DHD `sysmemregs_t` is in `sbsysmem.h`, **not in refs**, so offset 0 is a [guess] for DHD, `R_REG(&regs->coreinfo)` `D/siutils.c:7031`] | Bad = 0xFFFFFFFF (DHD treats it as invalid, `D/siutils.c:7032-7034`). Bank count field: brcmfmac `nb = (coreinfo & 0xF0) >> 4` (research 37 s.4) for old revs; DHD rev >= 12 uses `SYSMEM_SRCI_NEW_*` masks that are **not in refs** (U3), so only LOG the word |

Why W4 is gated on W3: DHD never reads `coreinfo` of a core that is not up; `si_sysmem_size` first resets a down core (a write) and only then reads [src: `D/siutils.c:7014-7024`, `:7031`]. Reading registers of a down core is the classic backplane stall; whether it stalls here is **[guess]**, so the S33 table makes the read conditional (stop rule below) and puts W4 last.

Excluded on purpose (not read in S33):
- `ioctrlset/ioctrlclear` 0x400/0x404, `iostatus` 0x500, `resetreadid/resetwriteid` 0x808/0x80C [src: `D/aidmp.h:166-167,170,177`]: set/clear-style or ID registers whose read behaviour is not documented in refs (could be read-to-clear or tracking); the three needed registers answer U3/U7.
- `pmutimer` 0x614, `pmuwatchdog` 0x634, `res_req_timer` 0x644, `res_state` 0x60C, `res_pending` 0x610: `pmutimer`/`pmuwatchdog`/`res_req_timer` are "ILP sensitive" (async; DHD reads twice and compares) [src: `D/siutils.c:115-117`, `D/include_sbchipc.h` comment above `pmucontrol` in the CC copy]; res_state/res_pending not needed. Only 5 PMU registers are read; DHD's own diagnostic dump reads control/caps/status/min/max as plain loads [src: `D/siutils.c:5594-5602`, CC view of the same registers] and polls `pmustatus` in a loop [src: `D/siutils.c:4238-4243`], so a read does not clear anything there [src: read from source; no clear-on-read statement exists in the refs, **residual guess**].
- `bankidx` (+0x10) write and `bankinfo` (+0x40) read (`B/brcmfmac-chip.c:165-201` field order; offsets by counting fields: bankidx 0x10, bankinfo 0x40): a bank read needs the write of `bankidx` [src: `D/siutils.c:6966-6980` via research 37 s.4]; reading `bankinfo` without selecting a bank returns the currently selected one and is meaningless. Left to S34 (it is a chip write).
- SRPWR status (`BAR0+0x2000+0x1E8`, U5): needs the "fixed alias of the PCIe core" guess (`D/include_pcicfg.h:652`, `D/siutils.c:9285-9306`) and `MULTIBP_CAP` is not in refs (`grep` shows only the use `D/siutils.c:2824`, `D/dhd_pcie.c:10064`). Not in S33. It also needs a MEM-guard rule for a window-independent alias that nothing has validated.

## 4. Draft S33 rows (ordered; `--wlan-link=33` is cumulative: S1..S32 run first, as in `w3a-n32b`)
Precondition: the cumulative run reaches S33 after S32's EROM dump (EOT seen). S32 leaves cfg 0x80 = `0x18105000` (its restore to 0x18000000 is a TEARDOWN row only, `shim/wlanlink.c:265-266`), so the chain starts from `0x18105000`. (Alternative if the implementer wants a run without the S32 dump: start from `0x18000000`; do not support both.)

1. Precondition (wait_eq, one-shot, else STOP): cfg 0x80 == `0x18105000` (S32 end state; [src: `wlan_seq.json` row 417]).
2. cfg 0x80 := `0x18012000` (write; allowed only when current value is exactly `0x18105000`, forward row, same Command/BAR0 guards as S32), read-back wait_eq `0x18012000` (else STOP + restore).
3. W1 reads in this order, each logged `R33 row=..`: `0x604` caps, `0x600`, `0x608`, `0x618`, `0x61C`. After the five logs: guard wait_eq on `0x604` mask `0xFF` value `0x28` (stop if not 0x28; placed AFTER the logs so a wrong pmurev guess loses no data).
4. cfg 0x80 := `0x18120000` (allowed only from exactly `0x18012000`), read-back == `0x18120000`.
5. W2 reads: `0x408`, `0x800`, `0x804` (logged). Guard: `0x800` wait_eq mask `0xFFFFFFFE` value 0 (RESET_CTL has only bit 0 defined [src: `D/aidmp.h:372`; upper bits reading zero is a **[guess]**; if it trips, the data is already logged and the run ends with restore).
6. cfg 0x80 := `0x18126000` (only from exactly `0x18120000`), read-back == `0x18126000`.
7. W3 reads: `0x408`, `0x800`, `0x804` (logged). Guards: `0x800` mask `0xFFFFFFFE` value 0 (as 5); then **"SYS_MEM up" gate**: `0x408` wait_eq mask `0x3` value `0x1`, then `0x800` wait_eq mask `0x1` value `0`. A failure here is the EXPECTED-possible outcome "SYS_MEM down": the run stops (summary `stopped=` names the row), `restore=ok`, and W4 is not executed. That outcome is informative (U3: needs core reset in S35), not a defect.
8. cfg 0x80 := `0x18026000` (only from exactly `0x18126000`), read-back == `0x18026000`.
9. W4: read `0x000`, logged, no guard: the static table has no `!=` rule (review 13 L2), so an all-ones `coreinfo` is reported by the log reader, not by the run (it is the last read, nothing is lost).
10. Link/AER logs (CFG 0xBC, 0x104) as S30/S31.
11. Teardown, first row: cfg 0x80 := `0x18000000` from ANY of the new windows (`0x18012000`, `0x18120000`, `0x18126000`, `0x18026000`; extend the existing S32 teardown allowlist, `shim/wlanlink.c:265`), read-back == `0x18000000`, then the S29/S32 teardown rows unchanged. The allowlist for forward cfg 0x80 writes after S33 is exactly: `0x18012000` (from `0x18105000`), `0x18120000` (from `0x18012000`), `0x18126000` (from `0x18120000`), `0x18026000` (from `0x18126000`), plus the existing entries.
Per-window MEM read allowlist (exact): `0x18012000`: {0x600,0x604,0x608,0x618,0x61C}; `0x18120000` and `0x18126000`: {0x408,0x800,0x804}; `0x18026000`: {0x000}. Everything else refused, as for S32.

Stop rules summary: every window read-back mismatch; first read of a window returning all-ones (add a first-row guard on RESET_CTL / caps as above, the only ones expressible); `0x604 & 0xFF != 0x28`; RESET_CTL upper bits non-zero; SYS_MEM not-up gate (expected possible). A bus hang cannot be stopped by a row; it would show as the last line `LINK S33 start` (as in S30), so the order is by risk: PMU (always on), ARM wrapper, SYS_MEM wrapper, SYS_MEM core page last.

## 5. Size and time (AP-22)
- Measured baseline `w3a-n32b` [obs: `L32/usb.times`]: first line at 39450.8 s, `S31 start` at +525 s, `S32 start` +552 s, dump of 218 lines +13 s (R32 row=419 at +552.3 s to row=637 at +572.6 s), summary line at +615 s (teardown after the dump took ~43 s), 935 usb lines.
- S33 adds: 4 window writes with read-backs (logged or not, <= 8 lines), 14 `R33` read lines, 2 link/AER lines, `S33 ok`: about 25 lines => at most one extra 3 s console pause (3 s per 49 lines, `shim/console.c:80-86`) plus a few ms of MMIO. Expected summary at about 620-640 s from `fboot`.
- Run form: `--timeout 900` (review 14 condition 1); the USB stream is the full copy (ramlog 32 KiB, `shim/ramlog.h:13`; S33 adds < 2 KB). Compare `usb.txt` mtime with `fboot.log + --timeout` before calling anything a HANG.

## 6. Unknowns
| # | Unknown | Settles with |
|---|---|---|
| U3 | SYS_MEM `coreinfo` field layout for rev 9 under DHD (`sbsysmem.h` not in refs), bank count, `bankinfo` size field, `ramsize`; whether SYS_MEM is up | S33 W3/W4 values; fetch Broadcom `sbsysmem.h` (same way as `aidmp.h`, cite commit) before S34 |
| U5 | MULTIBP_CAP body; SRPWR register present and its status field | not read in S33; fetch `siutils.h` macro, or one later read `BAR0+0x2000+0x1E8` after the alias is validated |
| U7 | ARM CA7 already halted / in reset after PERST (needs CPUHALT = 0x20 in DHD's `hndsoc.h`; only brcmfmac cited) | S33 W2 |
| U17 | cfg 0x80 accepts the four new pages without a stall | S33 run |
| U18 | pmurev (caps low byte) equals the EROM PMU component rev 40 | S33 W1 |
| U19 | Reading a down core's wrapper does not stall (DHD does it in `_ai_core_reset` before enabling, but with the core in reset only) | S33 W2/W3 |
| U20 | RESET_CTL upper bits read 0; RESET_ST layout (only RESET_CTL bit 0 defined in `aidmp.h:372`) | S33 W2 value |

## 7. Implementer steps (no code here)
1. Add the four window values to `soc/google/gs101/wlan_seq.json` step 33 (new step text in `steps`), as `mmio_write` CFG rows with exact predecessors; each with a following `wait_eq` read-back row; teardown row `T01` family extended so the restore to `0x18000000` works from each new window (`requires: 33`).
2. Add the 14 `read_only` MEM rows with the exact offsets of section 4 (logged), the guard `wait_eq` rows, and the SYS_MEM up-gate before W4; place W4 last.
3. Extend the MEM guard in `shim/wlanlink.c` (`:203-206`) with the per-window exact offset sets; keep `off >= 0xE00` out of the new windows.
4. Extend the cfg 0x80 write allowlist (`:265` region) with exactly the pairs above; teardown any-new-window to `0x18000000`.
5. Host tests: window x offset grid for the new windows (all other pairs refused), forward chain, restore from each window, mutants (wrong window value, offset +4, missing read-back, missing first-row teardown), the object-audit evaluator `_wlanlink_r32_window_model` extended (AP-16: audit behaviour, not instruction order). Add `--wlan-link=33` to the host-test loop in `shim/build.sh` (AP-13).
6. Table `sha` pin, hash update, decoder: teach `tools/erom-decode.py` nothing; add a small `S33` printer (IOCTL/RESET decode, pmurev, up/halted flags) as a script or in the report.
7. Gate: `GATE PASS` read from the log (AP-21), Opus 5.5 review file in `docs/reviews/` (window writes), then `tools/phone-lock run --owner wifi -- tools/probe-run --build-args "--pstore-fast --wlan-link=33" --expect "LINK summary reached=S33 stopped=none restore=ok" --label w3a-n33 --timeout 900` (a SYS_MEM-down stop would read `stopped=<row>`: an accepted outcome, expect string then `restore=ok` only).

## 8. Opened this session
`D/aidmp.h` 43-44, 160-180, 296-325, 371-372; `D/include_sbchipc.h` 40-70, 429-436, 733-734, 872, 1946-1963; `D/include_hndsoc.h` 262-267; `D/include_pcicfg.h` 488-510, 626-653; `D/aiutils.c` 560-590, 931-938, 1186-1194, 1455-1475; `D/siutils.c` 440-470, 525-530, 4225-4243, 5588-5606, 6985-7062; `B/bcma_regs.h` 25-45; `B/brcmfmac-chip.c` 140-205, 264-278, 354-366; `B/brcmfmac-pcie.c` 118-119, 1828-1855; `K/shim/wlanlink.c` 195-215; `K/soc/google/gs101/wlan_seq.json` rows 407-417, T01-T05; `L31/usb.txt` 702-719; `L32/usb.txt`, `usb.times`; `erom-decode.py L32`.
