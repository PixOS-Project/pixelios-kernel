Verdict: SAFE WITH CONDITIONS | RUN-READY: yes (N=34 first, then 35, then 36, each after the previous run's result is read; condition 1 below before the first)

# Review 17: W-C3 delta re-review, S34-S36 first BAR0 chip writes (kernel ec50939 -> 4a456b8)

Reviewer: Opus 5.5, 2026-10-25. Branch `wip/w3a-wlan-link`, HEAD 4a456b8. Scope: `git diff ec50939 4a456b8` (8 files, +57/-27) and whether each finding of review 16 is closed. Review 16 is at super `docs/reviews/16-s34-s36-chip-writes.md`, not in the kernel clone as the brief said.
No hardware was touched. All builds and tests ran on a private copy, `/tmp/revw.17/tree` (`git archive 4a456b8`). `shim/out` was not touched and the gate was not run.
Tags: [obs] = observed on this host in this review; [src] = read from source (file:line opened in this review); [guess].
Refs: `D` = `~/pixelios-artifacts/refs/wlan-stage2`, `G` = `.../wlan-stage4`, `B` = `.../wlan-stage3` (brcmfmac).

## Conditions
1. The driver's `GATE PASS` on 4a456b8, read from the log (AP-21). I did not run the gate.
2. This review file must also exist in super `docs/reviews/`. Research 40 s.9 (`docs/research/40-first-chip-writes-sysmem-arm.md:150`) makes that a precondition of every run (finding 1).

Everything else below is documentation, LOW or INFO. None of it blocks a run.

## Review 16 findings: closure, each proved by a mutant that must fail

| R16 | Fix in 4a456b8 | Proof (in /tmp/revw.17, `python3 -I -B`) |
|---|---|---|
| 1 Medium: the N=36 `--expect` row 1407 comes after the accepted stops | `docs/research/40...md:153` now expects `LINK R36 row=1391 rd=`. Row 1391 is the last pre-write log of S36 (read_only log, step 36), and 1391 < 1398 [obs: JSON dump] | `tools/probe-run --dry-run` against fixture usb.txt files [obs]. Stops at 1398 and 1405 give PASS with row 1391 and FAIL with the old row 1407. A clean run gives PASS with both (`/tmp/revw.17/f1.sh`). **Closed.** |
| 2 Low: the SRPWR write needs PowerDomain id != 0 | `shim/wlanlink.c:222` adds `(old & 0xF0000000u) != 0u`. Mirrored in `spec_memw` (`shim/wlanlink_host_test.c:184`), `_memw_spec` (`tools/test-bluejay-boot.py:4336-4338`), the source pin (`:3733`), 3 source mutants (`:4021-4023`), an object grid (`:4376-4381`), 3 object mutants (`:4554-4557`), host chip_mode 14 (`wlanlink_host_test.c:2055-2058`) and the explicit tuples (`:1903-1904`) | [obs] Clause removed: source audit FAIL and host N=34 FAIL. Bit 31 only: FAIL/FAIL. Inverted: FAIL/FAIL. Dead-read clause removed: FAIL/FAIL. The equivalent form `(old >> 28) != 0u` passes the host test (it is equivalent) and fails the source pin. Object self-test on a cross-built `wlanlink.o`: **118/118** bad objects rejected (`/tmp/revw.17/obj.log`). The harness asserts that every mutant changes the text (`tools/test-bluejay-boot.py`, "did not mutate"). **Closed.** |
| 3 Low: ARM must be held in reset before S35/S36 | `tools/gen_wlan_seq.py:829`: row 1332 is now `wait_eq MEM 0x800 mask 0xFFFFFFFF value 1 polls 1`, tag observed. Table pin `tools/test-bluejay-boot.py:3031`, 4 table mutants `:3590-3593`, host test `wlanlink_host_test.c:1681-1682` and `:1745-1748` (`arm_rc = 0`: refused at S33, `mem_wr_n == 0`), s33-decode E2 `v == 1`, `tools/test_s33_decode.py:69` | [obs] Row 1332 back to `&0xFFFFFFFE == 0`: table audit FAIL, host N=33 FAIL. Changed to `== 0`: FAIL/FAIL. `&1 == 1`: FAIL/FAIL. Row removed: table FAIL. The s33-decode E2 lambda back to the upper-bit test: `test_s33_decode` FAIL (1 failure). The start state the gate relies on was confirmed in the w3a-n33 log: `LINK R33 row=1330 rd=0x00000001` (ARM RESET_CTL = 1) [obs: `logs/probe/20261024T115618Z-w3a-n33_/usb.txt:943`]. **Closed.** |
| 4 Low: the doc write count | `docs/research/40...md:146`: "10 BAR0 writes plus 4 cfg 0x80 window moves" | Text read. **Closed.** |
| Info 1: 10 us delay before coreinfo; cite for the order | Not applied | Finding 6 |
| Info 2: equivalent `tear \|\|` mutant to be noted in the pins | Not applied | Finding 7 [obs: removing `tear \|\|` at `wlanlink.c:433` still passes host N=36; only the w32 sha pin catches it] |
| Info 3: comment drift | `wlanlink.c:221`: "(no-op if already set)" | **Closed** |
| Info 4: wording of the row-1405 stop | `research 40:153` reworded | **Closed** |

Controls at 4a456b8 [obs]: host tests N=33/34/35/36 PASS; table audit PASS (sha `2ecd5871...` matches the JSON); source audit PASS; object audit plus self-test PASS; `test_s33_decode` 12/12 OK; `test_s35_decode` 16/16 OK.

## Re-verification of S34-S36 against the sources (requirement 2)

**Addresses** [obs]. `tools/erom-decode.py` (b5b0785 table) run on the n33 log gives:
- PCIE2 base `0x18001000`;
- ARM_CA7 base `0x18020000`, wrapper `0x18120000`;
- SYS_MEM base `0x18026000`, wrapper `0x18126000`;
- result: PASS, 36 components.

**Wrapper registers** [src]:
- `ioctrl 0x408`, `resetctrl 0x800`, `resetstatus 0x804` (`D/aidmp.h:168,175,176`); `AIRC_RESET 1` (`:372`);
- `SICF_FGC 0x2`, `SICF_CLOCK_EN 0x1` (`D/include_hndsoc.h:266-267`).

**CPUHALT 0x20** [src]: `G/g4389-include_siutils.h:306` (DHD, Google bcm4389 tree mirror, see `G/SOURCE.txt`); `B/brcmfmac-chip.c:78`.
- brcmfmac applies it to the CA7: `brcmf_chip_disable_arm` case CA7 runs `resetcore(core, val & HALT, HALT, HALT)` (`B/brcmfmac-chip.c:1068-1075`).
- `ai_coredisable` on a core already in reset writes `IOCTL = reset | FGC | CLK = 0x23`. `ai_resetcore` then clears RESET_CTL and writes `postreset | CLK = 0x21` (`:350-381`, `:434-470`).
- DHD does `si_core_reset(SICF_CPUHALT, SICF_CPUHALT)` on the CA7 (`D/dhd_pcie.c:10123-10125`).
- So rows 1393/1397/1401/1404 (`1`, `0x23`, `0`, `0x21`) match both drivers. The extra RESET_CTL := 1 assert is DHD `_ai_core_reset` `D/aiutils.c:1262`.
- The GOALS W-C3 note "DHD value a guess" is out of date (finding 5).
- Whether 0x20 halts this CA7 is still **unverified** (U-N1): the read-back only shows that the bit latches.

**Sequence** [src]: `D/aiutils.c:1252-1325`:
- RESET_ST SPINWAIT 300 us before and after the assert (rows 1356/1360, 1392/1396: 30 x 10 us);
- 10 us delay;
- IOCTL write with read-back;
- release loop;
- final IOCTL;
- 1 us delay.

The table adds read-backs after every write: 1359, 1362, 1367, 1369, 1384, 1395, 1398, 1403, 1405, and 1346 for SRPWR [obs: JSON dump]. Each of these is a stop, not a continue.

**SRPWR** [src]:
- `powerctl` at 0x1E8 (`D/include_pcie_core.h:296`, `:1041`).
- Fields: REQON [11:8] (`D/include_sbchipc.h:5199`), STATUS [19:16] (`:5202`), DMN_ID [31:28] (`:5208-5209`); DMN1 mask `1<<1` (`:5181-5183`). So the request is 0x200 and the status is 0x20000.
- Algorithm `D/siutils.c:9311-9352`. The capability check `si_srpwr_cap` / `si_srpwr_domain` reads the same register field [31:28] (`:9487-9517`).
- Wait budget: `PMU_MAX_TRANSITION_DLY 15000` (`D/include_sbchipc.h:4874-4878`). Row 1347 uses 150 x 100 us.
- MULTIBP request mask DMN1 only: `D/dhd_pcie.c:795-816`.
- Window to the core page instead of the BAR0+0x2000 alias: still a [guess] (U-N3). brcmfmac does the same for PCIE2 (`B/brcmfmac-pcie.c:625-647`, `:686-700`: select the core page by cfg 0x80, then BAR0-relative core registers) [src].

**Bank sizing** [src]:
- sysmemregs layout: `coreinfo` 0x0, `bankidx` 0x10, `bankinfo` 0x40 (`G/g4389-include_sbsysmem.h:37-56`, and `SYSMEM_BANKIDX_REG 0x10` / `SYSMEM_BANKINFO_REG 0x40` `:166-167`).
- rev < 12 masks `ROMNB 0x3E0 >> 5`, `SRNB 0x1F` (`:115-118`, `D/siutils.c:7038-7040`).
- bankidx := 0 with nrb == 0 selects RAM bank 0. Rev 9 itself is EROM-observed.

## Teardown, allowlist, caps (requirement 3), read from source at 4a456b8

**Teardown rows** [src]:
- T01 is at `soc/google/gs101/wlan_seq.json:1426` (cfg 0x80 := 0x18000000, requires 32) and T02 at `:1427`.
- **T20 PERST low is at `:1445` (requires 20). T22 WLAN_EN low is at `:1447` (requires 3).** The `~:1351/:1353` in the brief and GOALS is stale (finding 4).

**Every stop reaches them** [src]: `shim/wlanlink.c:556-597`.
- Any non-OK row breaks out of the step loop.
- The teardown loop runs whenever `started`. It skips only rows with `step > hi` or an unprobed block (GPH2/GPP14 are probed in S01-S03).
- A failing teardown row only increments `tear_bad` and the loop continues.
- The only returns before the teardown (`:543-555`) come before any write.

**Allowlist independent of the table** [src]:
- `memw_ok` constants are in C (`wlanlink.c:217-234`).
- `w32` reads the live window, the old value and the predecessor via `r32` (`:430-435`), and refuses in teardown (`:433`, plus `w32_allowed :376`).
- The host spec (`spec_memw`) and the Python `_memw_spec` are separate tables.
- Every other window returns 0.

**Caps** [src]: `poll_row` limits the total wait to 2.5 s and polls to POLL_CAP plus a counter limit (`:449-470`); delays are at most `DELAY_MAX_US`.
- A backplane stall is not a fault and cannot be stopped by a row. The hang signature is in research 40 s.5; recovery is Power+VolDown [guess: GPIO/WLAN_EN default low after the reset].

## Gates not loosened (requirement 4)
I read `git diff ec50939 4a456b8 -- tools/test-bluejay-boot.py`. Every change is a tightening or a re-keying:
- the S33 exact list now requires `BIGW, 1`;
- two S33 table mutants were re-keyed to `value=1` and two were added;
- the two SRPWR source mutants were re-anchored to the new text with the same defect class, and three were added;
- the object mutant `ccmn #1 -> #2` no longer matches the folded compare (`old + 0xF0000000 <u 0xEFFFFFFF`). It was replaced by three constant mutants, all caught (118/118) [obs];
- `_WLAN_SEQ_COUNTS` and `_WLAN_SEQ_TARGET_BLOCKS` are unchanged;
- the sha pin was updated to the regenerated table (the control audit passes) [obs].

In the host test, the tuple `memw_ok(0x18001000,0x1E8,0,0x200,0)` flipped from expected-true to expected-false. That is correct for the new rule. Nothing was removed without a replacement.

## Findings

### 1. LOW (process): this review must exist where the run precondition looks
- Tag: read from source.
- Where: `docs/research/40-first-chip-writes-sysmem-arm.md:150` requires "a review file in super `docs/reviews/`" before any N=34/35/36 run. AP-17 says the same, and review 16 lives there.
- Problem: the brief put review 17 in the kernel clone and gave review 16's location as the kernel clone (it is not there). That is an AP-17 recurrence.
- **Fix:** copy this file to super `docs/reviews/17-w-c3-delta.md`, cite that path in `docs/HANDOFF.md`, and append "Again: 2026-10-25 (review 17 brief)" to AP-17.

### 2. LOW (doc): the research 40 s.5 allowlist row is stale
- Tag: read from source.
- Where: `docs/research/40-first-chip-writes-sysmem-arm.md:104` says `only 0 -> 1 | old != 0xFFFFFFFF`.
- Problem: the code (`wlanlink.c:222`) also allows the no-op 1 -> 1 and needs `(old & 0xF0000000) != 0`. `_memw_spec` claims to be research 40 s.5.
- **Fix:** change row `:104` to `RMW: only bit 0x200 set (no-op if already set), other bits as read | old != 0xFFFFFFFF and PowerDomain id [31:28] != 0 (si_srpwr_cap, D/siutils.c:9513-9517)`.

### 3. LOW (doc): the s.5 time-budget run form contradicts s.9
- Tag: read from source.
- Where: `docs/research/40...md:113` gives the N=36 run form as `--expect "LINK summary reached=S36 stopped=none restore=ok"`.
- Problem: with that form, the accepted stops at 1398 and 1405 report FAIL (AP-27).
- **Fix:** replace the run form in `:113` with "run forms: s.9 (AP-27)".

### 4. LOW (doc): stale teardown line cites
- Tag: observed.
- Where: super `docs/GOALS.md:28` cites T20 `:1351` and T22 `:1353`; `docs/research/40...md:99` cites `:1380/:1382` and T05 `:1365`.
- At 4a456b8: T20 `wlan_seq.json:1445`, T22 `:1447`, T05 `:1430`, T01 `:1426`.
- **Fix:** cite by row id (`T20`, `T22`, `T05`), not by JSON line, in both docs. The line numbers move with every table change.

### 5. LOW (doc): the GOALS W-C3 criterion is out of date
- Tag: read from source.
- Where: `docs/GOALS.md:28` still says "DHD value a guess" and "no SRPWR write unless W-C2 shows them needed".
- Research 40 s.1.2 closes the CPUHALT value from source (`G/g4389-include_siutils.h:306`, `D/dhd_pcie.c:10123-10125`).
- s.1.1/s.2 explain the SRPWR request: 4389 is MULTIBP (`G/g4389-include_siutils.h:963-968`), and the S33 reads cannot show domain state. It is now gated on the domain id.
- **Fix:** update the GOALS row to cite research 40 s.1/s.2 and record that the driver accepts the SRPWR deviation. Keep U-N1 (halt effect) and U-N3 (register alias) as the open items.

### 6. LOW (carried from review 16 Info 1, not applied): no explicit 10 us delay after the SYS_MEM reset before coreinfo
- Tag: read from source / guess.
- Where: DHD does `OSL_DELAY(SI_CORE_RESET_DELAY_USEC)` with value 10 (`D/siutils.c:6983`, `:7021-7023`) after `si_core_reset(0,0)` and before `coreinfo`.
- The table goes from row 1370 (1 us) to coreinfo row 1379 with 6 BAR0 reads, 3 log lines and a window move in between, which is likely far over 10 us [guess].
- The SYS_MEM-before-ARM order cite (`D/dhd_pcie.c:2327`, `:2340-2341`) is also not in research 40 s.3/s.4.
- **Fix (optional, not a run blocker):** in `tools/gen_wlan_seq.py` S35, add a `delay 10` row ("DHD SI_CORE_RESET_DELAY_USEC, D/siutils.c:6983") right before the move to 0x18026000. Then update the S35 exact list, the counts and the sha pin. Add the order cite to research 40 s.3.

### 7. INFO: the equivalent mutant is still undocumented in the pins
- Tag: observed.
- Removing `tear ||` from `wlanlink.c:433` still passes host N=36. It is caught only by the w32 sha pin. `w32_allowed :376` refuses MEM in teardown as a second layer.
- **Fix:** one comment next to the w32 sha pin in `tools/test-bluejay-boot.py`: "`tear ||` in w32 is defence in depth, equivalent to w32_allowed's `!c->tear` (AP-26); pinned by sha, not by a behavioural mutant".

### 8. INFO: the kernel clone working tree is dirty
- Tag: observed.
- Unrelated public-release work: `D tools/setup-upstream.sh`, `upstream-patches/*` and `upstream-files.txt` (staged), plus `M README.md` and others.
- None of it is under `shim/` or `soc/`. No `*.sh`/`*.py` references the deleted files [obs: grep].
- **Fix:** none needed for safety. Before the run, confirm `git status --short -- shim soc tools/test-bluejay-boot.py tools/gen_wlan_seq.py` is empty, which it is at 4a456b8 [obs].

## Not verified
- The gate (`tools/check-all.sh`) on 4a456b8; that is the driver's job.
- Any hardware behaviour: U-N1 (0x20 halts the CA7), U-N3 (the core page equals the SRPWR alias), U-N5 (persistence) and U-N8 remain open as research 40 s.6 says.
- With the new S33 gate, a persisted out-of-reset CA7 after an N=36 run would make the next run stop at S33. That is safe and informative.
