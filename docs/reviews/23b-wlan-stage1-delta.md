# Review 23b: delta e4aa7df vs 2cb5be0 (applies review 23 F1,F2,F4-F8)

Reviewer: Claude Sonnet 5.5, adversarial. Scope: `git diff 2cb5be0 e4aa7df`; patch xnu/patches/0120-sweet-wlan-bwfm-core.patch (P:n = patch line n).
Tags: [obs] observed now on the host, [src] read from source file:line, [guess].

## Verdict: SAFE WITH CONDITIONS (no blocking defect; 0 High, 0 Medium, 2 Low, 2 Info)

Host proof [obs]: the awk extraction of the chip header gives 383 lines; `gcc -O1 -Wall -Wextra -Werror ... wlan_xnu_host_test.c` builds and prints
`RESULT: PASS wlan_xnu_host_test`. `python3 -I tools/test_mkadt_wlan.py`: 5 tests OK.

## Requirement 1: status of the earlier findings

| F | Verdict | Evidence |
|---|---|---|
| F1 | FIXED | Glue order and constants checked under req. 2. ELBI map/read, ATU r0 (7 words) and r1 (7 words) are in `swl_pre_link` (chip.h P:~365-392); EP Command/BAR0 in `swl_pre_ep`. `elbi` ADT property added (mkadt.py:338, soc.json `wlan.elbi` = 0x14520000 [obs: json load]) and pinned (P:30, P:125). The cfg 0x04 re-check sits in `sw_rd` on every window change (P:49-54). |
| F2 | PARTIAL (acceptable) | New `negative_cases` and `precheck_cases` (wlan_xnu_host_test.c:~160-243). Mutants [obs, copies in /tmp/rvm]: eromptr pin, ChipCommon base pin, chip id, TYPE=AI, CA7 wrapper pin, no-PMU caps, level<2, `swl_win_ok`/`swl_rd_ok` level terms, iso bit, LTSSM mask, EP Command mask, BAR0-high, an r1 word: all KILLED. SURVIVED: PMU base pin (chip.h `pmu->base != 0x18012000u`), SYS_MEM wrapper pin, D11 wrapper pin. See F-A: they are masked by the allowlist, so no unguarded read follows. Not covered at all from F2's list: missing-PMU core / PMU at 0x18013000 as a separate case, eromptr 0x18106000 (the mutant is killed, so a case exists via a different route), ChipCommon 0x18001000 (killed). sweet_wlan.c itself is still not host-compiled (the glue ISO/ATU/ADT checks have no mutant test); only the pure functions are. Accepted by design (F2's own fix keeps loads in the glue). |
| F4 | FIXED | `swl_win_ok(win, level)` and `swl_rd_ok(win, off, level)` (P:~306-325) refuse 0x18121000/22000/23000 for level < 2; test line 308 asserts it and the `winlvl` mutant (level term removed) is killed [obs]. Condition on the first run (default `--wlan-hold`, level 1 first) stays operational, not code. |
| F5 | FIXED | `if (!(c->cc_caps & SWL_CAPS_PMU)) {... return -1;}` before the PMU reads (chip.h P:~556-559); mutant removing it is killed [obs]. |
| F6 | FIXED | P:120 `csize < sizeof(SW_COMPAT) || strncmp(compat, SW_COMPAT, sizeof(SW_COMPAT)) != 0` (includes the NUL). Not host-tested (glue file). |
| F7 | FIXED | build-xnu-image.sh:28-29 case 1\|2 and the `--wlan-hold` coupling; default `wlanlvl=1` (line 23), so default builds pass. xrun:34/68 only forwards the value quoted and the child validates, exit 2 [src]. `'2 x'`, `3`, `` (empty) are rejected by the case; `--wlan-level 2` without hold is rejected. |
| F8 | FIXED | mkadt.py:328 says 0120; guard `all(k in w for k in ('mem','cfg','atu','elbi','pmu_pcie1_iso'))` (mkadt.py:340); test_mkadt_wlan.py parses the five `#define SW_*` from the patch and compares them (regex matches `+#define SW_X  0x...ull`, passes [obs]). |

## Requirement 2: order in sweet_wlan_init [src P:98-174]

1. Boot-arg and level>2 check, ADT node, compatible, then the five `sw_prop_is` pins: no MMIO.
2. Only MMIO read before the isolation test is the ISO word itself (P:128-133); it is the first read and its bit 0 is tested at P:134 before any other mapping is even created (ATU/ELBI/cfg/mem `sw_map` at P:138-141 are mappings, not loads). OK.
3. `ltssm = elbi[0x2C8/4]` and the 14 ATU loads (`a0[i]=atu[i]` offsets 0x00..0x18; `a1[i]=atu[0x200/4+i]` offsets 0x200..0x218, i<7, in bounds of the 0x1000 map) are RC-local, then `swl_pre_link` (P:153) decides. The first `hw.cfg[]` load (cfg 0x80, P:159) is after `swl_pre_link` returns 0. OK.
4. `swl_pre_link` constants vs shim/wlanlink.c: iso bit0; `& 0x3F == 0x11` (wlanprobe.c:14 and wlanlink_host_test.c:762); w0 = {4, 0x80000000, 0x60FFE000, 0, 0x60FFFFFF, 0x01000000, 0} equals atu_ok S26 values (wlanlink.c:316-318: 0x10 = WLAN_CFG+0x1fff); w1 = {0, 0x80000000, 0x60000000, 0, 0x6000FFFF, 0x60000000, 0} equals the r1 reads at wlanlink.c:263-267 and 397-399 (0x200=0, 0x204=0x80000000, 0x208, 0x20C=0, 0x210, 0x214, 0x218=0). All match.

## Requirement 3: sw_rd [src P:39-67]

`swl_win_ok` and `swl_rd_ok` (both with `h->level`, the only call site P:45) run first; then, on a window change, `swl_pre_ep(cfg 0x04, 0x10, 0x14)` (P:51) runs before the store `h->cfg[0x80/4] = win` (P:55). `h->level` is `(int)level` with level <= 2 enforced at P:118, so level < 2 can never reach a D11 window; `swl_stage1` has the same level in its own `level < 2` return. `off` is `a & 0xFFF` against a 0x1000 map: in bounds. The window readback compare (P:56) also exists.

## Findings

### F-A (Low) Three pin mutants survive; the allowlist masks them
chip.h: `pmu->base != 0x18012000u`, `sm->wrap != 0x18126000u`, `d->wrap != 0x18121000u + 0x1000u*i` can each be deleted with the host test still passing [obs]. Equivalent in effect today because `swl_win_ok/swl_rd_ok` refuse any other window or offset (a wrong PMU base reads nothing, ChipCommon as PMU is refused at offset PMUCONTROL in window 0x18000000), but the test no longer proves the pins themselves.
Fix: in `negative_cases` add three cases that assert the `printf` path is taken before the allowlist, e.g. add a `refused` counter to `struct fake` and assert `f.refused == 0` on: PMU core at 0x18013000, SYS_MEM wrap 0x18127000, D11 #2 wrap 0x18125000 (with those, the pin returns -1 and `refused` stays 0; without the pin the allowlist refuses and `refused == 1`).

### F-B (Low) The glue (sweet_wlan.c) still has no host compile
Compatible check (F6), prop pins and the init order of req. 2 are verified only by reading. A swap of the two statements at P:128-137 or P:153-159 would pass every test.
Fix: in shim/build.sh also extract sweet_wlan.c, compile with stubs (SecureDTGetProperty, ml_io_map returning arrays, PE_parse_boot_argn, printf) and assert: iso=0 -> zero loads except the ISO word; one bad a0/a1 word -> zero `cfg[]` loads. Not blocking; the first run is read-only and has a hold mode.

### F-C (Info) [guess] iso bit 0 proves PHY de-isolation, not that the HSI2 power domain and clocks are still up
If a failed shim teardown left iso = 1 while the HSI2 domain or its clock was gated, the ELBI load at P:146 could hang the bus (SError) before `swl_pre_link` can refuse it. Not reachable on the documented `--wlan-hold` path (shim keeps the domain on, shim wlanlink.c:546-556 is best effort). Not observed. Condition: only run with a fresh `--wlan-hold` flavour on the same boot.

### F-D (Info) Hunk counts verified
New-file hunk of sweet_wlan.c: header `+1,171`, `+` lines counted = 171 [obs]. sweet_wlan_chip.h: header `+1,383`, awk-extracted lines = 383 [obs]. Both equal.

## Requirement 4: new defects from the delta
None found: `a0/a1` loops i<7 with arrays [7]; offsets as above; `ltssm` offset 0x2C8 < 0x2000 map; `level` is uint32 printed with %u and cast to int after the >2 refusal; `swl_pre_link` takes `const uint32_t[7]` and loops 7; no unsigned wrap in `off`/`win` arithmetic (`win = a & ~0xFFFu`). Shell: wlanlvl default 1 keeps old invocations working.

## Conditions
1. First hardware run: `--wlan-hold` level 1; level 2 only after "stage 1 done level 1".
2. Apply F-A (cheap) before relying on the pin tests; F-B can wait for the writing stage.
