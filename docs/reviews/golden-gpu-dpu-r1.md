# Review golden-gpu-dpu r1 (kernel 32a5ad1 vs 9ba7f58)

Verdict: SAFE WITH CONDITIONS. No hardware-unsafe path found. The tool code is correct; the host test does not prove the new paths (F1-F3).

Tags: [obs] = run now, [src] = read from file:line.

Verified [obs]/[src]:
- Test: `RESULT: PASS test_golden_capture`; `OK` for test_trace_diff.py; shellcheck -S warning clean on both shell files.
- Req 1: the wifi path is unchanged [src diff]. Module list, filter string, file names (sfx="" for wifi), and the GAP/REFUSED conditions are the same. Only the GAP text now prints `$cmd`, which equals the old literal for wifi.
- Req 2: the arg-check list (golden-pull:57) is the only way into `mmio-filter-*` (:185); anything else hits `*) err` (:61), which is before the first adb call (:76). Observed: mmio-filter-nope, `mmio-filter-` and mmio-filter-gpu-x all give `RESULT: ERROR usage`. MMIO_MODS has all 5 keys, so the module list cannot be empty. Empty-filter risk: none.
- Req 3: screen-off, screen-on and gpu-state only send input events or read sysfs/dumpsys. No setprop, settings or partition write. R() creates the file by redirect before the check, so `ls -t | head -1` always finds the file just written (an adb failure leaves an empty file and a GAP, never a stale pass).
- Req 4: module names match .modinfo name= (exynos_drm, gs_panel, mali_kbase, mali_pixel, exynos_pd, cmupmucal, s2mpu) [obs blobs.tar]. "mali 1c500000.mali" and "autosuspend delay set to 500ms for TOP domain" are in dmesg-coldboot [obs]. lsmod: exynos_pd and cmupmucal used by mali_kbase, s2mpu used by pcie_exynos_gs [obs]. The trace-diff prefixes exist as text symbols in the four .ko [obs nm].

Findings

F1 (medium) tools/test_golden_capture.sh:~232-233, REFUSED tests are not specific. `ck` compares only the exit code (1). Mutants run [obs]:
 - dpu list replaced with "mali_kbase": PASS (it fails 1 on the filter-readback GAP, not on a missing module).
 - gpu-pmu without cmupmucal, or without exynos_pd: PASS (the other one is absent, so both mutants die on the same input).
 - wifi-s2mpu list cut to the wifi modules only: PASS (no test touches wifi-s2mpu).
 Fix: for each case assert the REFUSED line names the module: `ck ... "REFUSED: mmio-filter-dpu: exynos_drm missing" "$(grep -o 'REFUSED: [^(]*' "$w/out")"`. Add a gpu-pmu OK input that has all 4 modules and one that lacks only cmupmucal. Add a wifi-s2mpu PASS test with an s2mpu line and a REFUSED test without it. Assert the exact `grep -E '^(...) '` string for dpu, gpu-pmu and wifi-s2mpu as is done for gpu.

F2 (medium) test_golden_capture.sh:~236-241, screen-on and gpu-state are not really checked. Mutants that PASS [obs]: screen-on changed to `swipe 1 1 1 1; setprop persist.x 1`; the sysfs path changed to runtime_statusX; `ls -t` removed; `grep -x` loosened. Only the KEYCODE_SLEEP mutant is killed. Fix: add a screen-on test that asserts the whole `$LOG` script text equals exactly the intended line (and has no `setprop|settings put|dd|flash`), and a gpu-state test that asserts `$LOG` contains `/sys/devices/platform/1c500000.mali/power/runtime_status`. Add a case with `active` plus a pre-existing newer-looking stale `gpu-state-000000.txt` with bad content.

F3 (low) test_golden_capture.sh:~235, the "mmio-filter-nope -> ERROR" check does not assert that no adb call was made. Fix: `ck "no adb before ERROR" "" "$(cat "$LOG")"`. I confirmed by reading that it holds today.

F4 (medium) tools/trace-diff:60 (CALLERS) and :20-21 (help). The dpu regex `^(...|exynos_|...|gs_)` is broad [obs python]: `exynos_pcie_rc_*` (Wi-Fi PCIe), `exynos_pd_*` and `gs_*` all match dpu, and the gpu regex matches `pixel_*`/`gpu_*` of any module. On an unfiltered trace (or the wifi/gpu-pmu traces) the dpu block pulls in PCIe and PD rows. The text says the prefixes are "text symbols of ... .ko" but does not say the trace must be module-filtered (mmio-filter-dpu) or that the regex alone is not a separator. The test (test_trace_diff.py test_gpu_dpu_callers) has no negative case with `exynos_pcie_rc_` for dpu. Fix: add to the help `gpu/dpu regexes are prefixes and overlap other drivers (exynos_ also matches exynos_pcie_rc_ and exynos_pd_); use them only on a trace taken with mmio-filter-gpu/-dpu, or pass --iomem/--iomem-match`. Add a test where `exynos_pcie_rc_x [pcie_exynos_gs]` with block=dpu is documented and asserted. Alternatively narrow to `exynos_drm` symbols, as the old regex did.

F5 (low) tools/golden-pull:~25 (comment on MMIO_MODS gpu-pmu). "the G3D power-domain writes" is untagged. Only the lsmod use-count supports it. The exynos_pd_el3 and exynos_pmu_if modules (used by mali_kbase/exynos_drm per lsmod [obs]) are not in the filter, so PMU/SMC-routed writes may be missed. Fix: tag it "guess", and say that rows made from exynos_pmu_if/exynos_pd_el3 are not captured, or add them to gpu-pmu and the dpu list after checking the overrun.

F6 (low) tools/golden-pull gpu-state line: the path /sys/devices/platform/1c500000.mali/power/runtime_status is built from the dmesg device name only [obs name]. The sysfs location is not shown anywhere (guess). The GAP check requires exactly active|suspended, so a transitional suspending/resuming state also raises a GAP. That is loud, not silent, but it is noise. Fix: tag the path as guess in the comment (the dry-run/first run will show it); optionally accept `suspending|resuming` as "transitional" with its own GAP text.

F7 (low) golden-pull gpu-state: the file name is `gpu-state-$(date +%H%M%S).txt`, no date, and the name is computed in the R call but re-derived by `ls -t` for the check. Two calls in the same second overwrite each other (the first sample is lost without a GAP), and the same time of day on a later day in the same dir would overwrite too. Fix: compute `n=gpu-state-$(date -u +%H%M%S).txt` once, use `[ ! -e "$out/$n" ]` or add `%N`, and check `$out/$n` instead of `ls -t`.

F8 (low) dpu module list omits phy_exynos_mipi_dsim (loaded per lsmod [obs]): DSIM PHY MMIO is not caught by the caller filter if the writes come from the phy module. Guess whether that matters; mention it in the comment or add it after checking the lsmod row.

No AP entry added (nothing new beyond AP-124-style "one example per class" in F1, which is already covered).

## Resolution (method driver 6, 2026-10-28)
- F1/F3: each of dpu, gpu-pmu and wifi-s2mpu now has 4 checks: refused, reads exactly its module list, refused for the named first missing module, and no filter written. The unknown block makes no adb call (empty fake-adb log). Mutant `[dpu]="mali_kbase"` is now killed (3 FAILs) [obs host run].
- F2: screen-on has an exact command check; gpu-state checks the sysfs path, keeps two files, accepts `resuming`, and rejects a substring.
- F4: the dpu regex now lists exynos_drm/crtc/partial/hibernation/atomic/plane/dqe/tui/recovery, gs_panel/dsi/dcs, exynos_mipi and __set_phy_ (nm of exynos-drm.ko, gs-panel.ko and phy-exynos-mipi-dsim.ko). The help says gpu/dpu are for module-filtered traces only. New negative test: exynos_pcie_rc_ / exynos_pd_ / gs_chipid_ give other=3.
- F5: tagged [guess] (the write may go via exynos_pmu_if/exynos_pd_el3/ACPM); golden-2 row 3 covers the none case.
- F6: the path is tagged [guess until the first pull]; suspending/resuming are accepted.
- F7: the file name is now date + ns, and the check uses the name just written.
- F8: phy_exynos_mipi_dsim added to dpu (modinfo name= and lsmod [obs]).
Checks: test_golden_capture PASS, test_trace_diff 38 OK, shellcheck clean, validate-repo PASS.
