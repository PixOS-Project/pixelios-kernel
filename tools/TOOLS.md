<!-- GENERATED FILE: do not edit; tools/tool-census --write -->
# tools/ inventory

259 tools: 208 active, 0 deprecated, 51 allowlisted, 0 with no caller, 3 doc-only (34 more flagged but exempt). Status table: `tools/tools-status.tsv`; rules: docs/tool-proposals/TP-043-tool-census.md.

| tool | status | callers (kinds; file, max 3 + count) | doc refs | last run use | TP | note |
|---|---|---|---|---|---|---|
| `abl-dmesg` | active | runplan `super:docs/runplans/ba6-btaudio.md`, runplan `super:docs/runplans/bt-s3.md`, runplan `super:docs/runplans/pr-2-panic-2.md` (+4) | 1 | 2026-11-13 | TP-044 | tests: test_abl_dmesg.py called from super docs/runplans/ba6-btaudio.md:36 (post-run step); read-only fastboot oem dmesg, TP-044 |
| `adt.py` | active | tool `tools/build-xnu-image.sh`, gate `tools/check-all.sh`, tool `tools/gate-import-remote` (+43) | 2 | 2026-11-13 | - |  |
| `affected-tests` | active | tool `tools/gate`, hook `tools/hooks/pre-commit` | 0 | - | - | tests: test_affected_tests.py |
| `agent-guard` | active | tool `tools/agent_guard_extra.py`, tool `tools/agent_guard_tfown.py`, gate `tools/check-all.sh` (+7) | 3 | - | TP-046 | tests: test_agent_guard.py |
| `agent_guard_extra.py` | active | tool `tools/agent-guard` | 0 | - | - |  |
| `agent_guard_tfown.py` | active | tool `tools/agent-guard` | 0 | - | - | tests: test_agent_guard_tfown.py |
| `allon-score` | allowlisted | tool `tools/pixnav.c` | 0 | - | - | NOCALLER tests: test_allon_score.py run by hand and as a resident job --host step on every ALL-ON boot (super docs/GOAL-ALL-ON.md "Scoring"); scores a boot run dir, no gate step boots the phone |
| `allon-snapcheck` | allowlisted | tool `tools/allon-score` | 0 | - | - | NOCALLER tests: test_allon_snapcheck.py called by tools/allon-score --snap (ALL-ON oracle 4/5 screen proof, a resident --host step on every ALL-ON boot) and by hand on a pulled panel; host-only pixel rules on a panel file, never touches a phone; tests: test_allon_snapcheck.py |
| `audit_memo.py` | active | tool `tools/audit_wlanh1.py`, gate `tools/check-all.sh`, tool `tools/gate-manifest` (+2) | 1 | 2026-11-11 | - | tests: test_audit_memo.py |
| `audit_pool.py` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/test-bluejay-boot.py` (+1) | 0 | - | - |  |
| `audit_wlanh1.py` | active | tool `tools/test-bluejay-boot.py` | 0 | - | - | tests: test_audit_wlanh1.py |
| `backup-partitions.sh` | active | - | 1 | - | - | DOC-ONLY documented human-run root recovery backup (kernel AGENTS.md Commands; hard rules 1-3: read-only on the phone, bluejay only); needs Opus review before any change; docs/AUTOMATION.md:506 |
| `bcm-hcd-stack` | active | runplan `super:docs/runplans/bt-up12.md`, runplan `super:docs/runplans/bt-up7p.md` | 0 | - | - | tests: test_bcm_hcd_stack.sh |
| `boards.py` | active | tool `tools/change-class`, tool `tools/change_class_selftest_lib.py`, tool `tools/el2-qemu-test` (+16) | 4 | - | - |  |
| `build-xnu-image.sh` | active | tool `tools/devd-stage`, tool `tools/phone-guard`, tool `tools/resident` (+10) | 3 | - | - |  |
| `cam_bracket.py` | active | tool `tools/xrun` | 0 | - | - | tests: test_cam_bracket.py |
| `ceiling-check` | active | tool `tools/run_preflight_ceiling.py`, tool `tools/run_preflight_plancheck.py`, runplan `super:docs/runplans/TEMPLATE.md` | 0 | - | - | tests: test_ceiling_check.py |
| `change-class` | active | tool `tools/change_class_selftest_lib.py`, tool `tools/gate_select.py` | 1 | - | - | tests: test_change_class.py |
| `change_class_selftest_lib.py` | active | tool `tools/change-class`, tool `tools/gate_select.py` | 0 | - | - |  |
| `check-all.sh` | active | tool `tools/agent-guard`, tool `tools/gate`, tool `tools/gate-import-remote` (+22) | 7 | - | TP-003 |  |
| `check-readme.py` | active | tool `tools/validate-repo.py` | 1 | - | - | tests: test_check_readme.py |
| `check-rules.py` | active | tool `tools/agent-guard`, gate `tools/check-all.sh`, tool `tools/gate_lint_first_lib.py` (+1) | 2 | - | - | tests: test_check_rules.py |
| `check-udump.sh` | active | workflow `AGENTS.md` | 1 | - | - | documented maintainer/human UDUMP cross-check against root dd (kernel AGENTS.md Commands, same row as udump-recv.py); UDUMP read path still built in the gate; remove with udump-recv.py or neither |
| `chg_gate45_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_chg_gate45.py (a unit test, outside the census graph); host test of the patch 0305 pin-4/5 mask in sweet_chg_gate_still_open() |
| `chg_glue_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_chg_probe.py (a unit test, outside the census graph); host build of the kernel glue of xnu/patches/0283 sweet_chg.c against XNU stubs and the mkadt-generated /sweet-chg node |
| `chg_host_env.h` | allowlisted | tool `tools/chg_recheck_test.c` | 0 | - | - | NOCALLER header of tools/chg_host_test.c (fake register file for the host test of xnu/patches/0283) |
| `chg_host_test.c` | allowlisted | tool `tools/chg_host_env.h` | 0 | - | - | NOCALLER compiled and run by tools/test_chg_probe.py (a unit test, outside the census graph); host driver for xnu/patches/0283 sweet_chg.c (flow, gate sweep, allowlist) |
| `chg_i2c_glue_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_chg_i2c.py (a unit test, outside the census graph); host build of the kernel glue of xnu/patches/0284 against XNU stubs, HSI2C12 mapped read-only |
| `chg_i2c_host_env.h` | allowlisted | - | 0 | - | - | NOCALLER header of tools/chg_i2c_host_test.c (log capture for the host test of xnu/patches/0284) |
| `chg_i2c_host_test.c` | allowlisted | tool `tools/chg_icl_host_test.c` | 0 | - | - | NOCALLER compiled and run by tools/test_chg_i2c.py (a unit test, outside the census graph); host driver and fake HSI2C12 controller for xnu/patches/0284 sweet_chg_i2c.c |
| `chg_icl_host_test.c` | allowlisted | tool `tools/sweet_thermal_host_test.c` | 0 | - | - | NOCALLER compiled and run by tools/test_chg_icl.py (a unit test, outside the census graph); host driver, fake HSI2C12 and chip write log for xnu/patches/0287 (sweetchgicl=900, the only chip write) |
| `chg_recheck_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_chg_i2c.py (a unit test, outside the census graph); host test of sweet_chg_gate_still_open() of xnu/patches/0284 |
| `chg_usb_power_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_chg_icl.py (a unit test, outside the census graph); host test of the bMaxPower byte and sweet_usb_ss_configured() blocks of xnu/patches/0287 |
| `ci-kernel` | active | tool `tools/agent-guard`, tool `tools/pix` | 2 | - | TP-005 |  |
| `cpu_timeout.py` | active | tool `tools/wlan_mutant_pool.py`, test `tools/test_normal_runs_silent.py`, test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_cpu_timeout.py imported by tools/test_wlan_wr_mutants.py:30 (gate step wlan-mutants, check-all.sh:172) |
| `cputop_host_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_cputop.py (a unit test, outside the census graph); host model (fake processes and kernel threads) for xnu/patches/0341 sweet_cputop.c |
| `crash_summary.py` | active | tool `tools/xrun_early.py` | 0 | - | - | tests: test_crash_summary.py |
| `devd-stage` | active | tool `tools/devd.c`, tool `tools/devsh`, tool `tools/hot` (+2) | 0 | - | - | tests: test_devd_stage.py |
| `devd.c` | active | tool `tools/devd-stage`, tool `tools/devd_hardening_lib.py`, tool `tools/devd_hardening_misc_lib.py` (+4) | 1 | - | - |  |
| `devd_hardening_lib.py` | active | tool `tools/devd_hardening_misc_lib.py`, tool `tools/devd_hardening_push_lib.py`, tool `tools/devd_hardening_run_lib.py` (+1) | 0 | - | - |  |
| `devd_hardening_misc_lib.py` | active | test `tools/test_devd_hardening.py` | 0 | - | - |  |
| `devd_hardening_push_lib.py` | active | test `tools/test_devd_hardening.py` | 0 | - | - |  |
| `devd_hardening_run_lib.py` | active | test `tools/test_devd_hardening.py` | 0 | - | - |  |
| `devsh` | active | tool `tools/agent_guard_extra.py`, tool `tools/devd.c`, tool `tools/fflash-userdata.sh` (+36) | 2 | - | TP-027 |  |
| `el2-qemu-test` | active | runplan `super:docs/runplans/el2-1-s2trap.md`, runplan `super:docs/runplans/el2-2-xnu.md`, runplan `super:docs/runplans/el2-3-f7.md` | 0 | - | - |  |
| `el2_audit.py` | active | tool `tools/test-bluejay-boot.py` | 0 | - | - |  |
| `envfile.py` | active | tool `tools/mkwlancred.py`, tool `tools/qr-log.py`, tool `tools/worktree-new` (+3) | 0 | - | - | tests: test_envfile.py |
| `erom-decode.py` | active | tool `tools/gen_wlan_seq.py` | 1 | - | - | tests: test_erom_decode.py |
| `fboot-android` | active | tool `tools/agent-guard`, tool `tools/golden-pull`, tool `tools/lint-phone-converge` (+13) | 2 | - | TP-035 |  |
| `fboot.sh` | active | tool `tools/agent-guard`, tool `tools/gate-manifest`, tool `tools/gen_wlan_seq.py` (+20) | 3 | 2026-11-14 | - |  |
| `fcap-boot.sh` | allowlisted | tool `tools/overlay_std.py` | 0 | - | - | tests: test_fcap_boot.py run by hand by the sprint GPU thread (frame-capture loop: fcap-boot.sh starts fcap-pull.sh beside xrun; fcap-report.py replays the pulled captures); no gate or runplan caller in sprint mode |
| `fcap-pull.sh` | allowlisted | tool `tools/fcap-boot.sh` | 0 | - | - | tests: test_fcap_pull.py run by hand by the sprint GPU thread (frame-capture loop: fcap-boot.sh starts fcap-pull.sh beside xrun; fcap-report.py replays the pulled captures); no gate or runplan caller in sprint mode |
| `fcap-report.py` | allowlisted | - | 0 | - | - | NOCALLER tests: test_fcap_report.py run by hand by the sprint GPU thread (frame-capture loop: fcap-boot.sh starts fcap-pull.sh beside xrun; fcap-report.py replays the pulled captures); no gate or runplan caller in sprint mode |
| `fcap-screens.py` | active | tool `tools/fcap-boot.sh` | 0 | - | - |  |
| `fetch-android-tools.sh` | active | - | 2 | - | - | DOC-ONLY documented one-time setup command (kernel AGENTS.md Commands, docs/BUILD.md:19); shim/build.sh needs its tools/third_party output (shim/build.sh:98-101) |
| `fflash-userdata.sh` | active | tool `tools/agent-guard`, tool `tools/agent_guard_extra.py`, tool `tools/devd.c` (+43) | 2 | - | TP-025 | tests: test_fflash_userdata.py |
| `flash_cache.py` | active | tool `tools/fflash-userdata.sh`, tool `tools/flash_cache_selftest_lib.py` | 0 | - | - | tests: test_flash_cache.py |
| `flash_cache_lib.py` | active | tool `tools/flash_cache.py`, tool `tools/flash_cache_selftest_lib.py` | 0 | - | - |  |
| `flash_cache_selftest_lib.py` | active | tool `tools/flash_cache.py` | 0 | - | - |  |
| `fps-wholerun` | allowlisted | tool `tools/allon-score`, tool `tools/pixnav.c` | 0 | - | - | NOCALLER tests: test_fps_wholerun.py whole-run fps scorer of the T-54 heavy bench, called by tools/allon-score O3 (itself allowlisted: run by hand / by resident --host steps on ALL-ON boots) |
| `frame-split` | allowlisted | runplan `super:docs/runplans/gputest-0530-flushcov.md`, runplan `super:docs/runplans/gputest-061a-endclean.md`, runplan `super:docs/runplans/gputest-17dc-hz60.md` (+41) | 0 | - | - | tests: test_frame_split.py GPU frame-split classification helper, called by tools/gpu-nav-score and run by hand on sprint run dirs |
| `g11d-oracle` | allowlisted | runplan `super:docs/runplans/gputest-g11d-applib.md`, runplan `super:docs/runplans/gputest-g11d-applib2.md`, runplan `super:docs/runplans/gputest-g11d-applib3.md` (+8) | 0 | - | - | tests: test_g11d_oracle.py agent-run T0 host oracle for G-11d (blur shader missing vs capture FBO empty/partial) from existing SWEETGL logs; no runplan calls it yet, add the caller with the first runplan that reads it |
| `gate` | active | tool `tools/affected-tests`, tool `tools/agent-guard`, tool `tools/agent_guard_extra.py` (+252) | 7 | - | TP-053 |  |
| `gate-identity` | active | tool `tools/gate-import-remote`, tool `tools/remote-job`, runplan `super:docs/runplans/golden-6b.md` | 0 | - | - |  |
| `gate-import-remote` | active | gate `tools/check-all.sh`, tool `tools/gate-request`, tool `tools/gate-route-remote` (+5) | 0 | - | - | tests: test_gate_import_remote.sh |
| `gate-inputs` | active | gate `tools/check-all.sh`, tool `tools/gate-trunk-check`, tool `tools/gate_lint_first_lib.py` (+1) | 0 | - | - | tests: test_gate_inputs.py |
| `gate-lint-first` | active | tool `tools/gate-request`, tool `tools/gate-trunk-check`, tool `tools/gate_lint_first_lib.py` (+19) | 3 | - | TP-052 | tests: test_gate_lint_first.py called from tools/gate-request (lint-first before a gate slot); also run by hand on a branch head before a gate request (AP-278) |
| `gate-manifest` | active | tool `tools/change-class`, gate `tools/check-all.sh`, tool `tools/gate-summary` (+8) | 0 | - | - |  |
| `gate-nightly` | allowlisted | - | 0 | - | - | NOCALLER tests: test_gate_nightly.py caller is the systemd user timer written by `tools/gate-nightly install` (outside the census graph); runs the L2 formal gate on trunk nightly (PLAN-GATE-LAYERS s.2) |
| `gate-request` | active | gate `tools/check-all.sh`, tool `tools/gate-trunk-check`, tool `tools/gate_lint_first_lib.py` | 0 | - | - |  |
| `gate-route-remote` | active | tool `tools/heavy-job-offload.sh` | 0 | - | - |  |
| `gate-summary` | active | gate `tools/check-all.sh`, tool `tools/gate`, tool `tools/gate-trunk-check` | 0 | - | - | tests: test_gate_summary.py |
| `gate-times` | active | gate `tools/check-all.sh`, tool `tools/gate`, tool `tools/gate-summary` | 0 | - | - | tests: test_gate_times.py |
| `gate-trunk-check` | active | tool `tools/gate`, tool `tools/gate-request`, tool `tools/gate_lint_first_lib.py` | 0 | - | - | tests: test_gate_trunk_check.py |
| `gate-unit-shards` | active | tool `tools/affected-tests`, gate `tools/check-all.sh`, tool `tools/gate-manifest` (+2) | 0 | - | - | tests: test_gate_unit_shards.py |
| `gate-xnu-build` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/gate-request` (+25) | 2 | - | TP-039 | tests: test_gate_xnu_build.py |
| `gate_lint_first_lib.py` | active | tool `tools/gate-trunk-check`, test `tools/test_gate_lint_first.py`, test `tools/test_gate_lint_first_m1.py` (+4) | 0 | - | - |  |
| `gate_select.py` | active | tool `tools/agent-guard`, tool `tools/change-class`, tool `tools/change_class_selftest_lib.py` (+9) | 2 | - | - | tests: test_gate_select.py |
| `gen-ufsphy-tables.py` | active | tool `tools/validate-repo.py` | 0 | - | - | tools/validate-repo.py runs gen-ufsphy-tables.py --check (gate step validate): a drifted ufsphy_tables.h fails the gate |
| `gen-wlan-a80211-patch` | allowlisted | - | 0 | - | - | NOCALLER tests: test_gen_wlan_a80211_patch.py regenerates xnu/patches/0201-sweet-wlan-a80211-glue.patch from the shim sources; its `--check` is run by shim/build.sh (host-tests block of gate step build, `chk "xnu patch 0201 == shim/ sources"`), a script the census does not scan for callers; the regeneration itself is by hand when shim/sweet_wlan_a80211.h, sweet_wlan_sta.h or the glue change |
| `gen-wlan-en0-patch` | allowlisted | - | 0 | - | - | NOCALLER tests: test_gen_wlan_en0_patch.py regenerates xnu/patches/0199-sweet-wlan-en0-ifnet.patch from the shim sources; its `--check` is run by shim/build.sh (host-tests block of gate step build, `chk "xnu patch 0199 == shim/ sources"`), a script the census does not scan for callers; the regeneration itself is by hand when shim/wlan_en0.h, sweet_wlan_data*.h or the glue change |
| `gen-wlan-hunks` | allowlisted | - | 0 | - | - | NOCALLER rewrites/checks the shim-copy new-file hunks of xnu patches 0174/0206; its --check is run by shim/build.sh (host-tests block), a script the census does not scan for callers |
| `gen-wlan-registry-patch` | allowlisted | - | 0 | - | - | NOCALLER tests: test_gen_wlan_registry_patch.py regenerates xnu/patches/0200-sweet-wlan-registry.patch from the shim sources; its `--check` is run by shim/build.sh (host-tests block of gate step build, `chk "xnu patch 0200 == shim/ sources"`), a script the census does not scan for callers; the regeneration itself is by hand when shim/wlan_registry.h or the glue change |
| `gen_wlan_4way_vectors.py` | allowlisted | - | 0 | - | - | NOCALLER by-hand regenerator of shim/wlan_4way_vectors.h (independent python reference for the 4-way host test); needs the python `cryptography` package, which the hermetic gate container does not provide, so no gate step can call it; run it only when the test vectors change |
| `gen_wlan_h1.py` | active | tool `tools/gen_wlan_seq.py` | 0 | - | - | tests: test_gen_wlan_h1.py |
| `gen_wlan_seq.py` | active | tool `tools/boards.py` | 2 | - | - |  |
| `golden-gpu-analyze` | active | runplan `super:docs/runplans/golden-6b-gpu-rerun.md`, runplan `super:docs/runplans/golden-6b.md` | 0 | - | - | tests: test_golden_gpu_analyze.py |
| `golden-pull` | active | tool `tools/agent-guard`, tool `tools/fboot-android`, tool `tools/remote-job` (+16) | 2 | - | - |  |
| `golden-unattended-image` | active | tool `tools/fboot-android`, runplan `super:docs/runplans/golden-4-unattended.md`, runplan `super:docs/runplans/golden-5-su.md` (+3) | 1 | - | TP-037 |  |
| `gpu-fps-bench` | allowlisted | tool `tools/pixnav.c`, tool `tools/xrun` | 0 | 2026-11-11 | - | GPU sprint climb number (frame-time fps of a nav run, witness v2.1); called by the xrun gpu epilogue and by hand |
| `gpu-nav-score` | active | tool `tools/pixnav.c`, tool `tools/qr-log.py`, tool `tools/xrun` (+5) | 0 | - | - | tests: test_gpu_nav_score.py |
| `gpuclk_audit.py` | active | tool `tools/change-class`, tool `tools/gate-manifest`, tool `tools/test-bluejay-boot.py` | 0 | - | - | tests: test_gpuclk_audit.py |
| `hang-recover` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/phone` (+46) | 3 | - | TP-006 |  |
| `heavy-job` | active | tool `tools/agent_guard_extra.py`, gate `tools/check-all.sh`, tool `tools/fflash-userdata.sh` (+38) | 3 | 2026-11-14 | TP-042 | tests: test_heavy_job.py, test_heavy_job.sh |
| `heavy-job-canon.sh` | active | tool `tools/heavy-job` | 0 | - | - |  |
| `heavy-job-cycle.sh` | active | tool `tools/heavy-job` | 0 | - | - | tests: test_heavy_job_cycle.py, test_heavy_job_cycle.sh |
| `heavy-job-light.sh` | active | tool `tools/heavy-job` | 0 | - | - | tests: test_heavy_job_light.py, test_heavy_job_light.sh |
| `heavy-job-offload.sh` | active | tool `tools/heavy-job`, tool `tools/remote-job` | 0 | - | - | tests: test_heavy_job_offload.py, test_heavy_job_offload.sh |
| `heavy-job.conf.example` | active | tool `tools/heavy-job` | 0 | - | - |  |
| `heavy_job_parts.py` | active | tool `tools/gate-trunk-check`, test `tools/test_heavy_job.py`, test `tools/test_heavy_job_cycle.py` (+9) | 0 | - | - |  |
| `hfs_read.py` | active | tool `tools/mkdevoverlay.py`, tool `tools/overlay_std.py`, tool `tools/root_gaps.py` (+1) | 0 | - | - |  |
| `hot` | active | tool `tools/agent-guard`, tool `tools/agent_guard_extra.py`, tool `tools/fcap-pull.sh` (+54) | 1 | 2026-11-13 | - | tests: test_hot.py |
| `hot_shared.py` | active | tool `tools/hot`, tool `tools/live-cut` | 0 | - | - | tests: test_hot_shared.py |
| `imgmeta.py` | active | tool `tools/fboot.sh`, tool `tools/gate-manifest` | 2 | - | - |  |
| `install-hooks` | allowlisted | tool `tools/public-push-guard`, tool `tools/repo-health` | 1 | - | TP-054 | run by hand once per clone by the driver (tools/install-hooks, then --check in the reviewer brief); no gate step can call it: core.hooksPath and .claude/ are local state, absent in the hermetic container; the super docs/WORKFLOW.md Commands row is owed (super repo, driver) |
| `job-wake` | active | tool `tools/heavy-job`, tool `tools/remote-job` | 0 | - | - | tests: test_job_wake.py, test_job_wake.sh |
| `kdbg2perfetto` | allowlisted | - | 0 | - | - | NOCALLER tests: test_kdbg2perfetto.py decodes SWEET KDBG lines (patch 0264) of a run usbnet.log into a Perfetto Chrome JSON trace; run by hand |
| `lint-boot-markers` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/gate_lint_first_lib.py` (+1) | 0 | - | - | tests: test_lint_boot_markers.py |
| `lint-c` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/gate_lint_first_lib.py` (+4) | 4 | - | TP-014 | tests: test_lint_c.py WARN-only full-gate step lint-c in tools/check-all.sh (lane A); findings ride on the GATE line as lint-c warn=N |
| `lint-consumed` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/gate_lint_first_lib.py` | 0 | - | - | tests: test_lint_consumed.py |
| `lint-mutant-needles` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/gate_lint_first_lib.py` | 0 | - | - | tests: test_lint_mutant_needles.py |
| `lint-phone-converge` | active | tool `tools/agent-guard`, gate `tools/check-all.sh`, tool `tools/gate_lint_first_lib.py` | 1 | - | - | tests: test_lint_phone_converge.py |
| `lint-shellcheck-reason` | active | gate `tools/check-all.sh` | 0 | - | - | tests: test_lint_shellcheck_reason.py |
| `lint-silent` | active | tool `tools/agent-guard`, tool `tools/agent_guard_tfown.py`, tool `tools/audit_memo.py` (+62) | 0 | - | - | tests: test_lint_silent.py |
| `lint-size` | active | tool `tools/gate_lint_first_lib.py`, tool `tools/validate-repo.py` | 0 | - | - | tests: test_lint_size.py |
| `lint_c_style.py` | active | tool `tools/lint-c` | 1 | - | - | tests: test_lint_c_style.py |
| `lint_g2_stores.py` | allowlisted | tool `tools/trace2seq.py`, test `tools/test_virtual_mmio.py` | 0 | - | - | run by hand on a Wi-Fi run dir until it is folded into tools/replay (R55) which becomes a gate/runplan caller; review by 2026-11-14 |
| `live-cut` | allowlisted | tool `tools/hot`, tool `tools/resident_session_lib.py`, tool `tools/resident_test_fakes.py` | 0 | - | - | tests: test_live_cut.py called by hand in hot-reload sessions (super docs/recipes/phone-run.md, GPU/BT iteration loops, user 2026-11-09); no gate step runs a live phone session |
| `make-qr2-patch.py` | active | workflow `AGENTS.md` | 0 | - | - | documented regenerate line for patch 0094 in kernel AGENTS.md Commands; freshness gate is tools/test_qrl2.py |
| `matrix_wifi_join.py` | allowlisted | - | 0 | - | - | NOCALLER ticket oracle for the SPEC-WIFI Part E JOIN matrix (RESULT: PASS matrix-wifi-join); it reads the super-repo spec file, which is absent from the kernel clone and the gate container, so no gate step can call it; run by hand with --spec/--header when the matrix or the header changes |
| `matrix_wifi_list.py` | active | workflow `AGENTS.md` | 0 | - | - | tests: test_matrix_wifi_list.py |
| `mg-key` | active | agent `super:.claude/agents/reverser.md` | 1 | 2026-11-12 | TP-013 | tests: test_mg_key.py documented in super docs/WORKFLOW.md Commands and the super .claude/agents/reverser.md (unknown MG key -> tools/mg-key NAME --dsc CACHE) |
| `mifclk_audit.py` | active | tool `tools/gate-manifest`, tool `tools/sweetprobe_audit.py`, tool `tools/test-bluejay-boot.py` (+1) | 0 | - | - | tests: test_mifclk_audit.py |
| `mkadt.py` | active | tool `tools/chg_glue_test.c`, tool `tools/chg_i2c_glue_test.c`, tool `tools/gate-import-remote` (+32) | 2 | 2026-11-13 | - |  |
| `mkbtm49.py` | active | runplan `super:docs/runplans/bt-s4b.md`, runplan `super:docs/runplans/bt-s4c.md`, runplan `super:docs/runplans/bt-s4j.md` (+1) | 0 | - | - | tests: test_mkbtm49.py |
| `mkdevoverlay.py` | active | tool `tools/build-xnu-image.sh`, tool `tools/devd-stage`, tool `tools/devd.c` (+10) | 1 | 2026-11-14 | - | tests: test_mkdevoverlay.py |
| `mkpayload.py` | active | tool `tools/build-xnu-image.sh`, tool `tools/change-class`, tool `tools/gate-manifest` (+19) | 1 | 2026-11-12 | - |  |
| `mkwlancred.py` | active | tool `tools/mkpayload.py`, tool `tools/resident_fit.py`, tool `tools/xrun` (+5) | 0 | - | - | tests: test_mkwlancred.py |
| `nav-classify` | allowlisted | tool `tools/pixnav.c` | 0 | - | - | NOCALLER tests: test_nav_classify.py closed-loop nav state check (ALL-ON T-31): run by hand / by the nav host replay until the on-phone pixnav driver calls the same method; no gate step boots the phone |
| `new-tool` | active | tool `tools/agent_guard_extra.py`, tool `tools/lint-size`, hook `tools/hooks/pre-commit` | 0 | - | - | tests: test_new_tool.py |
| `offload-conf.sh` | active | tool `tools/heavy-job`, tool `tools/offload-stage-xnub`, tool `tools/offload-sync` (+2) | 0 | - | - | tests: test_offload_conf.sh |
| `offload-host.conf.example` | active | tool `tools/heavy-job`, tool `tools/offload-stage-xnub`, tool `tools/offload-sync` (+2) | 0 | - | - |  |
| `offload-stage-xnub` | active | tool `tools/remote-job`, tool `tools/xnu-remote-build` | 1 | - | - |  |
| `offload-sync` | active | hook `tools/hooks/_offload-sync-trigger` | 1 | - | TP-055 | tests: test_offload_sync.py |
| `offload_conf.py` | allowlisted | tool `tools/offload-sync` | 0 | - | - | tests: test_offload_conf.sh caller is tools/offload-sync (python import), which runs from the git post-commit/post-merge/post-rewrite hooks outside the census graph |
| `ota-extract` | active | runplan `super:docs/runplans/golden-bp31-coldboot.md` | 1 | - | TP-049 | tests: test_ota_extract.py |
| `overlay_std.py` | active | tool `tools/devsh`, tool `tools/fcap-boot.sh`, tool `tools/mkdevoverlay.py` (+3) | 0 | - | - | tests: test_overlay_std.py |
| `pack_wlanfw.py` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/lint-consumed` (+2) | 0 | - | - | tests: test_pack_wlanfw.py |
| `panel2png` | allowlisted | tool `tools/allon-score`, tool `tools/allon-snapcheck` | 0 | - | - | NOCALLER tests: test_panel2png.py called by tools/allon-score --snap (ALL-ON oracle 4/5 screen proof), which is itself run by hand / as a resident --host step; no gate step boots the phone |
| `panic-region-check` | active | runplan `super:docs/runplans/pr-2-panic-3.md`, runplan `super:docs/runplans/pr-2-panic-4.md`, runplan `super:docs/runplans/pr3-marker-1.md` (+1) | 0 | - | - |  |
| `panic_capture.py` | active | tool `tools/crash_summary.py`, tool `tools/xrun_report.py`, test `tools/test_0242_panic_usb.py` (+4) | 0 | - | - | tests: test_panic_capture.py |
| `patch-guard` | active | hook `tools/claude-settings.json`, hook `super:.claude/settings.json` | 1 | - | - | tests: test_patch_guard.py PreToolUse hook in super .claude/settings.json:18 |
| `patch-numbers` | active | tool `tools/agent-guard`, tool `tools/patch-guard`, tool `tools/remote-job` (+4) | 2 | - | TP-022 | tests: test_patch_numbers.py |
| `pcsamp-sym` | allowlisted | - | 0 | - | - | NOCALLER symbolises SWEET PCSAMP rows (patch 0263); run by hand on run dirs |
| `phone` | active | tool `tools/abl-dmesg`, tool `tools/agent-guard`, tool `tools/allon-score` (+284) | 13 | 2026-11-15 | TP-011 |  |
| `phone-guard` | active | tool `tools/agent-guard`, tool `tools/change-class`, hook `tools/claude-settings.json` (+2) | 1 | - | - | tests: test_phone_guard.py |
| `phone-lock` | active | tool `tools/abl-dmesg`, tool `tools/agent-guard`, tool `tools/allon-score` (+241) | 6 | 2026-11-14 | TP-016 | tests: test_phone_lock.py |
| `phone-postmortem` | active | tool `tools/xrun`, runplan `super:docs/runplans/golden-bp31-coldboot.md`, runplan `super:docs/runplans/pr-2-panic-2.md` (+3) | 1 | - | TP-032 | tests: test_phone_postmortem.py documented in super docs/WORKFLOW.md Commands and the super .claude/agents/reverser.md (unexplained reset: phone-postmortem --out DIR before adb reboot bootloader) |
| `phone-state` | active | tool `tools/agent-guard`, gate `tools/check-all.sh`, tool `tools/gate-manifest` (+44) | 3 | - | TP-001 |  |
| `phone_lock_end.py` | active | tool `tools/phone-lock`, tool `tools/probe-run`, tool `tools/resident_session_lib.py` (+1) | 0 | - | - | tests: test_phone_lock_end.py |
| `phone_lock_root.py` | active | tool `tools/phone_lock_end.py`, tool `tools/probe-run` | 0 | - | - |  |
| `pix` | active | tool `tools/agent-guard`, tool `tools/backup-partitions.sh`, tool `tools/tool-proposals` | 3 | - | - |  |
| `pixnav-sim` | allowlisted | tool `tools/pixnav.c` | 0 | - | - | NOCALLER tests: test_pixnav_sim.py host-only closed-loop phone model driving the host pixnav build (-X) in tools/test_pixnav_sim.py; replays the recorded nav failures, never touches a phone |
| `pixnav.c` | allowlisted | tool `tools/nav-classify` | 0 | - | - | NOCALLER source of /usr/local/bin/pixnav (closed-loop stress nav for the ALL-ON GPU oracle: state check before every step, recovery by intent), built for iOS with tools/pixping-stage build() + the pixopen entitlements, pushed by a resident job; host test tools/test_pixnav.py |
| `pixnet.c` | allowlisted | tool `tools/pixping-stage` | 0 | - | - | source of /usr/local/bin/pixnet (DNS, TCP, TLS and HTTP proof over en0, SecureTransport trust evaluation, optional clock set from the HTTP Date header), built only by tools/pixping-stage; run as root by a resident job or tools/hot run |
| `pixopen.c` | allowlisted | tool `tools/pixping-stage` | 0 | - | - | source of /usr/local/bin/pixopen (opens a URL, default prefs:root=WIFI, in the foreground), built only by tools/pixping-stage; no direct caller |
| `pixperf-server` | active | tool `tools/pixperf.c`, tool `tools/pixping-stage` | 0 | - | - | tests: test_pixperf_server.py |
| `pixperf.c` | active | tool `tools/pixping-stage` | 0 | - | - |  |
| `pixping-stage` | allowlisted | tool `tools/hot`, tool `tools/pixnet.c`, tool `tools/pixopen.c` (+2) | 0 | - | - | stages the W-IOSPING overlay (pixping binary + boot job) for tools/xrun --overlay; run by hand by the sprint driver, a run plan will become its caller |
| `pixping.c` | allowlisted | tool `tools/pixnet.c`, tool `tools/pixperf.c`, tool `tools/pixping-stage` | 0 | - | - | source of /usr/local/bin/pixping, built only by tools/pixping-stage; no direct caller |
| `pixps.c` | active | tool `tools/hot`, runplan `super:docs/runplans/sprint-hot-reload.md` | 0 | - | - |  |
| `pixstate.c` | allowlisted | tool `tools/pixping-stage` | 0 | - | - | source of /usr/local/bin/pixstate (read-only witness of wifid current network and configd en0 keys), built only by tools/pixping-stage; no direct caller |
| `plock.sh` | active | tool `tools/agent-guard`, tool `tools/gate-manifest`, tool `tools/lint-phone-converge` (+4) | 0 | - | - |  |
| `pmicsrc_audit.py` | active | tool `tools/gate-manifest`, tool `tools/mifclk_audit.py` | 0 | - | - | tests: test_pmicsrc_audit.py |
| `probe-run` | active | tool `tools/agent-guard`, tool `tools/change-class`, gate `tools/check-all.sh` (+32) | 3 | 2026-11-04 | TP-004 |  |
| `probe_failfast.py` | active | test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_probe_failfast.py |
| `pstore-pull.py` | active | tool `tools/remote-job` | 1 | - | - | tests: test_pstore_pull.py |
| `public-push-guard` | allowlisted | gate `tools/check-all.sh`, hook `tools/hooks/commit-msg`, hook `tools/hooks/pre-commit` (+1) | 1 | - | - | tests: test_public_push_guard.sh caller is the git pre-push hook (outside the census graph); R66 content guard for pushes to public remotes (git pre-push hook installed by `tools/public-push-guard install-hook`; fails closed); rules in tools/public-push-guard.rules |
| `qr-log.py` | active | tool `tools/xrun`, runplan `super:docs/runplans/gputest-nav2.md` | 2 | - | - |  |
| `qrcam.py` | active | tool `tools/qr-log.py`, tool `tools/qrl2.py`, tool `tools/qrsim.py` | 0 | - | - |  |
| `qrl2.py` | active | tool `tools/gate-unit-shards`, tool `tools/qr-log.py`, tool `tools/qrsim.py` | 0 | - | - | tests: test_qrl2.py |
| `qrmerge.py` | active | tool `tools/xrun_report.py` | 0 | - | - | tests: test_qrmerge.py |
| `qrsim.py` | active | tool `tools/qrl2.py`, test `tools/test_qrl2.py` | 1 | - | - |  |
| `qrz-train.py` | active | workflow `AGENTS.md` | 0 | - | - | documented retrain line next to make-qr2-patch.py in kernel AGENTS.md Commands (writes tools/qrz_tables.py and qrlog/qrz_tables.h) |
| `qrz_codec.py` | active | tool `tools/qrl2.py`, tool `tools/qrz-train.py` | 0 | - | - |  |
| `qrz_tables.py` | active | tool `tools/make-qr2-patch.py`, tool `tools/qrsim.py`, tool `tools/qrz-train.py` (+2) | 0 | - | - |  |
| `recores_host_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_recores.py (a unit test, outside the census graph); host model of the scheduler's recommended-cores request/apply rule for xnu/patches/0344 sweet_recores_point |
| `remote-job` | active | gate `tools/check-all.sh`, tool `tools/gate-import-remote`, tool `tools/gate-request` (+17) | 1 | - | TP-048 | tests: test_remote_job.sh |
| `remote-job-inputs.sh` | active | tool `tools/remote-job` | 0 | - | - | tests: test_remote_job_inputs.sh |
| `remotezip.py` | active | - | 1 | - | - | DOC-ONLY |
| `replay` | active | tool `tools/agent-guard`, tool `tools/devd.c`, tool `tools/devd_hardening_misc_lib.py` (+156) | 3 | - | - | tests: test_replay.py |
| `repo-health` | active | tool `tools/gate-request` | 0 | - | - | tests: test_repo_health.py |
| `resident` | active | tool `tools/allon-score`, tool `tools/el2-qemu-test`, tool `tools/el2_audit.py` (+14) | 0 | 2026-11-14 | - | tests: test_resident.py |
| `resident_core.py` | active | tool `tools/resident`, tool `tools/resident_fit.py`, tool `tools/resident_notice.py` (+3) | 0 | - | - |  |
| `resident_fit.py` | active | tool `tools/resident`, tool `tools/resident_core.py`, tool `tools/resident_session_lib.py` (+2) | 0 | - | - |  |
| `resident_notice.py` | active | tool `tools/resident_session_lib.py` | 0 | - | - |  |
| `resident_phone_io.py` | active | tool `tools/resident_session_lib.py` | 0 | - | - |  |
| `resident_session_lib.py` | active | tool `tools/resident`, tool `tools/resident_notice.py` | 0 | - | - |  |
| `resident_test_fakes.py` | active | test `tools/test_resident.py` | 0 | - | - |  |
| `resident_test_session.py` | active | test `tools/test_resident.py` | 0 | - | - |  |
| `resident_thermal.py` | active | tool `tools/resident_phone_io.py`, tool `tools/resident_session_lib.py`, tool `tools/resident_test_session.py` (+1) | 0 | - | - |  |
| `rj-local-transport` | active | tool `tools/remote-job` | 0 | - | - | called from tools/remote-job (--where lab-host: ssh/scp stand-in, TP-050 R12 lab-host gate lane); no sshd on lab-host |
| `root_gaps.py` | active | tool `tools/xrun`, tool `tools/xrun_report.py` | 0 | 2026-11-14 | - | tests: test_root_gaps.py |
| `run-preflight` | active | tool `tools/agent-guard`, tool `tools/change-class`, gate `tools/check-all.sh` (+71) | 2 | - | TP-024 | tests: test_run_preflight.py |
| `run_preflight_ceiling.py` | active | tool `tools/run-preflight`, tool `tools/run_preflight_plancheck.py` | 0 | - | - | tests: test_run_preflight_ceiling.py |
| `run_preflight_flash.py` | active | tool `tools/run-preflight` | 0 | - | - | tests: test_run_preflight_flash.py |
| `run_preflight_plancheck.py` | active | tool `tools/run-preflight` | 0 | - | - | tests: test_run_preflight_plancheck.py |
| `runs` | active | tool `tools/affected-tests`, tool `tools/agent-guard`, tool `tools/agent_guard_extra.py` (+283) | 11 | 2026-11-15 | TP-023 | tests: test_runs.py |
| `safety-check` | active | tool `tools/fboot.sh`, tool `tools/phone-guard`, tool `tools/resident_session_lib.py` (+5) | 0 | 2026-11-14 | - | tests: test_safety_check.py |
| `safety_lib.py` | active | tool `tools/resident_test_fakes.py`, tool `tools/resident_test_session.py`, tool `tools/resident_thermal.py` (+3) | 0 | - | - |  |
| `shell-files` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/lint-shellcheck-reason` | 2 | - | - |  |
| `simseams.py` | active | tool `tools/gate-manifest`, tool `tools/overlay_std.py`, tool `tools/phone` (+6) | 0 | - | - |  |
| `stack-apply` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/pix` (+29) | 2 | - | - | tests: test_stack_apply.py |
| `sweet-crash.py` | active | runplan `super:docs/runplans/ba6-cpucap.md` | 0 | - | - |  |
| `sweet-reset-bl` | active | tool `tools/phone` | 0 | - | - |  |
| `sweet-usbnet.sh` | active | tool `tools/devsh`, tool `tools/xrun` | 1 | - | - |  |
| `sweet_thermal_glue_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_sweet_thermal.py (a unit test, outside the census graph); host driver for bsd/dev/sweet_thermal.c with stubbed kernel API (xnu/patches/0336) |
| `sweet_thermal_host_test.c` | allowlisted | - | 0 | - | - | NOCALLER compiled and run by tools/test_sweet_thermal.py (a unit test, outside the census graph); host driver for xnu/patches/0336: policy edges of sweet_thermal.h plus the charger model of chg_icl_host_test.c |
| `sweetprobe_audit.py` | active | tool `tools/mifclk_audit.py` | 0 | - | - | tests: test_sweetprobe_audit.py |
| `test-bluejay-boot.py` | active | tool `tools/agent-guard`, tool `tools/audit_wlanh1.py`, tool `tools/change-class` (+14) | 7 | 2026-10-22 | - |  |
| `to-fastboot.sh` | active | tool `tools/agent-guard`, tool `tools/gate-manifest`, tool `tools/hang-recover` (+15) | 2 | 2026-11-14 | - |  |
| `tool-census` | active | gate `tools/check-all.sh`, tool `tools/gate-lint-first`, tool `tools/gate_lint_first_lib.py` (+6) | 2 | - | TP-043 | tests: test_tool_census.py |
| `tool-proposals` | active | tool `tools/agent-guard`, tool `tools/pix`, tool `tools/tool-census` (+1) | 2 | - | TP-007 | tests: test_tool_proposals.py |
| `tools-sync` | active | gate `tools/check-all.sh`, tool `tools/phone-guard` | 2 | - | TP-021 | tests: test_tools_sync.py |
| `touch_irq0_core_lib.py` | active | test `tools/test_touch_irq0_core.py` | 0 | - | - |  |
| `touch_irq0_core_mut_lib.py` | active | test `tools/test_touch_irq0_core.py` | 0 | - | - |  |
| `touch_irq1_core_lib.py` | active | test `tools/test_touch_irq1_core.py` | 0 | - | - |  |
| `touch_irq1_core_mut_lib.py` | active | test `tools/test_touch_irq1_core.py` | 0 | - | - |  |
| `trace-diff` | active | tool `tools/golden-pull`, tool `tools/lint_g2_stores.py`, tool `tools/replay` (+18) | 2 | - | TP-036 | tests: test_trace_diff.py |
| `trace2seq.py` | allowlisted | - | 1 | - | - | DOC-ONLY tests: test_trace2seq.py spec-draft aid for the S1 spec step, run by hand from docs/PORT-METHOD.md S1 (no automatic caller); review by 2026-11-14 |
| `ucrash_summary.py` | active | tool `tools/xrun_early.py` | 0 | - | - | tests: test_ucrash_summary.py |
| `udump-recv.py` | active | tool `tools/check-udump.sh`, workflow `AGENTS.md` | 0 | - | - | documented UDUMP receiver (kernel AGENTS.md Commands, run before fastboot boot) paired with check-udump.sh; its --selftest passes; remove both or neither |
| `ufs_irq_core_lib.py` | active | test `tools/test_ufs_irq_core.py` | 0 | - | - |  |
| `ufs_irq_core_mut_lib.py` | active | test `tools/test_ufs_irq_core.py` | 0 | - | - |  |
| `unit_select.py` | active | tool `tools/affected-tests`, tool `tools/change-class`, tool `tools/change_class_selftest_lib.py` (+2) | 1 | - | - | tests: test_unit_select.py |
| `usb-log.py` | active | tool `tools/allon-score`, tool `tools/gate-manifest`, tool `tools/probe-run` (+4) | 1 | - | - | tests: test_usb_log.py |
| `usb-port` | active | tool `tools/gate-manifest`, tool `tools/probe-run`, tool `tools/xrun` | 0 | 2026-11-14 | - | tests: test_usb_port.py |
| `usbnet-log.py` | active | tool `tools/xrun`, runplan `super:docs/runplans/usb3-ss.md`, runplan `super:docs/runplans/usblog-cable1.md` (+3) | 1 | - | - |  |
| `usbroot-host.py` | active | tool `tools/devsh`, tool `tools/usbroot-serve`, tool `tools/usbroot-service-install` (+3) | 1 | - | - | tests: test_usbroot_host.py |
| `usbroot-image-check.py` | active | tool `tools/usbroot-host.py` | 1 | - | - | tests: test_usbroot_image_check.py tools/usbroot-host.py runs it at startup before serving (USBROOT GAP line on non-PASS, serving continues); the service and tools/usbroot-serve both run usbroot-host.py |
| `usbroot-serve` | active | runplan `super:docs/runplans/usbroot-probe5-hfs-trace.md` | 1 | - | - |  |
| `usbroot-service-install` | active | tool `tools/usbroot-status` | 1 | - | - |  |
| `usbroot-status` | active | tool `tools/usbroot-serve`, tool `tools/usbroot-service-install`, runplan `super:docs/runplans/usbroot-probe5-hfs-trace.md` (+1) | 1 | - | - |  |
| `validate-repo.py` | active | tool `tools/agent-guard`, gate `tools/check-all.sh`, tool `tools/gate_lint_first_lib.py` (+6) | 2 | - | - |  |
| `wdt-check` | allowlisted | runplan `super:docs/runplans/pr4-wdt-hang-2.md`, runplan `super:docs/runplans/pr4-wdt-hang.md` | 0 | - | - | caller is the run plan pr4-wdt-hang (T-5d2f child, not yet written) and tools/test_wdt_build.py; PR-4 watchdog replay and lint |
| `wdt_host_env.h` | allowlisted | - | 0 | - | - | NOCALLER header of tools/wdt_host_test.c (fake register file for the host test of xnu/patches/0193) |
| `wdt_host_test.c` | allowlisted | tool `tools/wdt_host_env.h` | 0 | - | - | NOCALLER compiled and run by tools/test_wdt_build.py (a unit test, outside the census graph); host driver for xnu/patches/0193 sweet_wdt.c |
| `wlan-tbl-gen` | active | tool `tools/change-class`, gate `tools/check-all.sh`, tool `tools/gate-manifest` | 0 | - | - | tests: test_wlan_tbl_gen.py |
| `wlan_g2l_harness_a.py` | active | test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_wlan_g2l_harness_a.py |
| `wlan_g2l_harness_b.py` | active | test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_wlan_g2l_harness_b.py |
| `wlan_glue_harness_a.py` | active | tool `tools/gate-manifest`, test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_wlan_glue_harness_a.py |
| `wlan_glue_harness_b.py` | active | tool `tools/gate-manifest`, test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_wlan_glue_harness_b.py |
| `wlan_glue_harness_c.py` | active | tool `tools/wlan_glue_harness_b.py` | 0 | - | - | tests: test_wlan_glue_harness_c.py |
| `wlan_glue_rows.py` | active | tool `tools/lint-mutant-needles`, test `tools/test_wlan_wr_mutants.py` | 0 | - | - | tests: test_wlan_glue_rows.py |
| `wlan_keep_mutants.py` | active | test `tools/test_wlan_en0_mutants.py`, test `tools/test_wlan_l25_mutants.py`, test `tools/test_wlan_wr_mutants.py` | 0 | - | - |  |
| `wlan_mutant_cache.py` | active | tool `tools/gate-manifest`, tool `tools/wlan_mutant_pool.py`, test `tools/test_normal_runs_silent.py` (+3) | 0 | - | - |  |
| `wlan_mutant_pool.py` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/lint-mutant-needles` (+3) | 0 | - | - | tests: test_wlan_mutant_pool.py |
| `worktree-new` | active | tool `tools/gate`, tool `tools/gate-inputs`, tool `tools/gate-nightly` (+2) | 0 | - | - | tests: test_worktree_new.py |
| `xnu-build-guard.py` | allowlisted | tool `tools/xnu-local-build` | 0 | - | - | tests: test_xnu_build_guard.py stale-object guard called by tools/xnu-local-build on every build |
| `xnu-lb-prune.sh` | active | tool `tools/xnu-local-build` | 0 | - | - | tests: test_xnu_lb_prune.sh |
| `xnu-local-build` | active | tool `tools/agent-guard`, tool `tools/change-class`, gate `tools/check-all.sh` (+65) | 4 | - | TP-009 |  |
| `xnu-remote-build` | active | gate `tools/check-all.sh`, tool `tools/gate-manifest`, tool `tools/remote-job` (+2) | 2 | - | - | tests: test_xnu_remote_build.py, test_xnu_remote_build.sh |
| `xnu-src-mirror` | active | tool `tools/gate-manifest`, tool `tools/offload-stage-xnub`, tool `tools/remote-job` (+3) | 0 | - | - | tests: test_xnu_src_mirror.py, test_xnu_src_mirror.sh |
| `xnub-line.sh` | active | tool `tools/gate-manifest`, tool `tools/offload-stage-xnub`, tool `tools/remote-job` (+2) | 0 | - | - |  |
| `xrun` | active | tool `tools/agent-guard`, tool `tools/agent_guard_extra.py`, tool `tools/build-xnu-image.sh` (+248) | 9 | 2026-11-04 | TP-002 |  |
| `xrun-secret-scan` | active | tool `tools/xrun_report.py`, tool `tools/xrun_secret_scan.py`, runplan `super:docs/runplans/wlan-runP2-ping.md` | 1 | - | TP-051 | tests: test_xrun_secret_scan.py |
| `xrun_early.py` | active | tool `tools/xrun_report.py` | 0 | - | - |  |
| `xrun_report.py` | active | tool `tools/build-xnu-image.sh`, gate `tools/check-all.sh`, tool `tools/gate-manifest` (+22) | 2 | - | - | tests: test_xrun_report.py |
| `xrun_secret_scan.py` | active | tool `tools/mkwlancred.py`, tool `tools/xrun-secret-scan`, tool `tools/xrun_report.py` (+1) | 0 | - | - | tests: test_xrun_secret_scan.py |

## Deprecated

none

## Allowlisted (backlog)

- `allon-score` since 2026-11-11: run by hand and as a resident job --host step on every ALL-ON boot (super docs/GOAL-ALL-ON.md "Scoring"); scores a boot run dir, no gate step boots the phone
- `allon-snapcheck` since 2026-11-12: called by tools/allon-score --snap (ALL-ON oracle 4/5 screen proof, a resident --host step on every ALL-ON boot) and by hand on a pulled panel; host-only pixel rules on a panel file, never touches a phone; tests: test_allon_snapcheck.py
- `chg_gate45_test.c` since 2026-11-11: compiled and run by tools/test_chg_gate45.py (a unit test, outside the census graph); host test of the patch 0305 pin-4/5 mask in sweet_chg_gate_still_open()
- `chg_glue_test.c` since 2026-11-10: compiled and run by tools/test_chg_probe.py (a unit test, outside the census graph); host build of the kernel glue of xnu/patches/0283 sweet_chg.c against XNU stubs and the mkadt-generated /sweet-chg node
- `chg_host_env.h` since 2026-11-10: header of tools/chg_host_test.c (fake register file for the host test of xnu/patches/0283)
- `chg_host_test.c` since 2026-11-10: compiled and run by tools/test_chg_probe.py (a unit test, outside the census graph); host driver for xnu/patches/0283 sweet_chg.c (flow, gate sweep, allowlist)
- `chg_i2c_glue_test.c` since 2026-11-10: compiled and run by tools/test_chg_i2c.py (a unit test, outside the census graph); host build of the kernel glue of xnu/patches/0284 against XNU stubs, HSI2C12 mapped read-only
- `chg_i2c_host_env.h` since 2026-11-10: header of tools/chg_i2c_host_test.c (log capture for the host test of xnu/patches/0284)
- `chg_i2c_host_test.c` since 2026-11-10: compiled and run by tools/test_chg_i2c.py (a unit test, outside the census graph); host driver and fake HSI2C12 controller for xnu/patches/0284 sweet_chg_i2c.c
- `chg_icl_host_test.c` since 2026-11-10: compiled and run by tools/test_chg_icl.py (a unit test, outside the census graph); host driver, fake HSI2C12 and chip write log for xnu/patches/0287 (sweetchgicl=900, the only chip write)
- `chg_recheck_test.c` since 2026-11-10: compiled and run by tools/test_chg_i2c.py (a unit test, outside the census graph); host test of sweet_chg_gate_still_open() of xnu/patches/0284
- `chg_usb_power_test.c` since 2026-11-10: compiled and run by tools/test_chg_icl.py (a unit test, outside the census graph); host test of the bMaxPower byte and sweet_usb_ss_configured() blocks of xnu/patches/0287
- `cputop_host_test.c` since 2026-11-13: compiled and run by tools/test_cputop.py (a unit test, outside the census graph); host model (fake processes and kernel threads) for xnu/patches/0341 sweet_cputop.c
- `fcap-boot.sh` since 2026-11-09: run by hand by the sprint GPU thread (frame-capture loop: fcap-boot.sh starts fcap-pull.sh beside xrun; fcap-report.py replays the pulled captures); no gate or runplan caller in sprint mode
- `fcap-pull.sh` since 2026-11-09: run by hand by the sprint GPU thread (frame-capture loop: fcap-boot.sh starts fcap-pull.sh beside xrun; fcap-report.py replays the pulled captures); no gate or runplan caller in sprint mode
- `fcap-report.py` since 2026-11-09: run by hand by the sprint GPU thread (frame-capture loop: fcap-boot.sh starts fcap-pull.sh beside xrun; fcap-report.py replays the pulled captures); no gate or runplan caller in sprint mode
- `fps-wholerun` since 2026-11-12: whole-run fps scorer of the T-54 heavy bench, called by tools/allon-score O3 (itself allowlisted: run by hand / by resident --host steps on ALL-ON boots)
- `frame-split` since 2026-11-09: GPU frame-split classification helper, called by tools/gpu-nav-score and run by hand on sprint run dirs
- `g11d-oracle` since 2026-10-31: agent-run T0 host oracle for G-11d (blur shader missing vs capture FBO empty/partial) from existing SWEETGL logs; no runplan calls it yet, add the caller with the first runplan that reads it
- `gate-nightly` since 2026-11-01: caller is the systemd user timer written by `tools/gate-nightly install` (outside the census graph); runs the L2 formal gate on trunk nightly (PLAN-GATE-LAYERS s.2)
- `gen-wlan-a80211-patch` since 2026-11-03: regenerates xnu/patches/0201-sweet-wlan-a80211-glue.patch from the shim sources; its `--check` is run by shim/build.sh (host-tests block of gate step build, `chk "xnu patch 0201 == shim/ sources"`), a script the census does not scan for callers; the regeneration itself is by hand when shim/sweet_wlan_a80211.h, sweet_wlan_sta.h or the glue change
- `gen-wlan-en0-patch` since 2026-11-02: regenerates xnu/patches/0199-sweet-wlan-en0-ifnet.patch from the shim sources; its `--check` is run by shim/build.sh (host-tests block of gate step build, `chk "xnu patch 0199 == shim/ sources"`), a script the census does not scan for callers; the regeneration itself is by hand when shim/wlan_en0.h, sweet_wlan_data*.h or the glue change
- `gen-wlan-hunks` since 2026-11-08: rewrites/checks the shim-copy new-file hunks of xnu patches 0174/0206; its --check is run by shim/build.sh (host-tests block), a script the census does not scan for callers
- `gen-wlan-registry-patch` since 2026-11-03: regenerates xnu/patches/0200-sweet-wlan-registry.patch from the shim sources; its `--check` is run by shim/build.sh (host-tests block of gate step build, `chk "xnu patch 0200 == shim/ sources"`), a script the census does not scan for callers; the regeneration itself is by hand when shim/wlan_registry.h or the glue change
- `gen_wlan_4way_vectors.py` since 2026-11-01: by-hand regenerator of shim/wlan_4way_vectors.h (independent python reference for the 4-way host test); needs the python `cryptography` package, which the hermetic gate container does not provide, so no gate step can call it; run it only when the test vectors change
- `gpu-fps-bench` since 2026-11-09: GPU sprint climb number (frame-time fps of a nav run, witness v2.1); called by the xrun gpu epilogue and by hand
- `install-hooks` since 2026-10-31: run by hand once per clone by the driver (tools/install-hooks, then --check in the reviewer brief); no gate step can call it: core.hooksPath and .claude/ are local state, absent in the hermetic container; the super docs/WORKFLOW.md Commands row is owed (super repo, driver)
- `kdbg2perfetto` since 2026-11-09: decodes SWEET KDBG lines (patch 0264) of a run usbnet.log into a Perfetto Chrome JSON trace; run by hand
- `lint_g2_stores.py` since 2026-10-31: run by hand on a Wi-Fi run dir until it is folded into tools/replay (R55) which becomes a gate/runplan caller; review by 2026-11-14
- `live-cut` since 2026-11-09: called by hand in hot-reload sessions (super docs/recipes/phone-run.md, GPU/BT iteration loops, user 2026-11-09); no gate step runs a live phone session
- `matrix_wifi_join.py` since 2026-11-01: ticket oracle for the SPEC-WIFI Part E JOIN matrix (RESULT: PASS matrix-wifi-join); it reads the super-repo spec file, which is absent from the kernel clone and the gate container, so no gate step can call it; run by hand with --spec/--header when the matrix or the header changes
- `nav-classify` since 2026-11-11: closed-loop nav state check (ALL-ON T-31): run by hand / by the nav host replay until the on-phone pixnav driver calls the same method; no gate step boots the phone
- `offload_conf.py` since 2026-11-01: caller is tools/offload-sync (python import), which runs from the git post-commit/post-merge/post-rewrite hooks outside the census graph
- `panel2png` since 2026-11-11: called by tools/allon-score --snap (ALL-ON oracle 4/5 screen proof), which is itself run by hand / as a resident --host step; no gate step boots the phone
- `pcsamp-sym` since 2026-11-09: symbolises SWEET PCSAMP rows (patch 0263); run by hand on run dirs
- `pixnav-sim` since 2026-11-12: host-only closed-loop phone model driving the host pixnav build (-X) in tools/test_pixnav_sim.py; replays the recorded nav failures, never touches a phone
- `pixnav.c` since 2026-11-11: source of /usr/local/bin/pixnav (closed-loop stress nav for the ALL-ON GPU oracle: state check before every step, recovery by intent), built for iOS with tools/pixping-stage build() + the pixopen entitlements, pushed by a resident job; host test tools/test_pixnav.py
- `pixnet.c` since 2026-11-13: source of /usr/local/bin/pixnet (DNS, TCP, TLS and HTTP proof over en0, SecureTransport trust evaluation, optional clock set from the HTTP Date header), built only by tools/pixping-stage; run as root by a resident job or tools/hot run
- `pixopen.c` since 2026-11-08: source of /usr/local/bin/pixopen (opens a URL, default prefs:root=WIFI, in the foreground), built only by tools/pixping-stage; no direct caller
- `pixping-stage` since 2026-11-08: stages the W-IOSPING overlay (pixping binary + boot job) for tools/xrun --overlay; run by hand by the sprint driver, a run plan will become its caller
- `pixping.c` since 2026-11-08: source of /usr/local/bin/pixping, built only by tools/pixping-stage; no direct caller
- `pixstate.c` since 2026-11-08: source of /usr/local/bin/pixstate (read-only witness of wifid current network and configd en0 keys), built only by tools/pixping-stage; no direct caller
- `public-push-guard` since 2026-10-31: caller is the git pre-push hook (outside the census graph); R66 content guard for pushes to public remotes (git pre-push hook installed by `tools/public-push-guard install-hook`; fails closed); rules in tools/public-push-guard.rules
- `recores_host_test.c` since 2026-11-13: compiled and run by tools/test_recores.py (a unit test, outside the census graph); host model of the scheduler's recommended-cores request/apply rule for xnu/patches/0344 sweet_recores_point
- `sweet_thermal_glue_test.c` since 2026-11-12: compiled and run by tools/test_sweet_thermal.py (a unit test, outside the census graph); host driver for bsd/dev/sweet_thermal.c with stubbed kernel API (xnu/patches/0336)
- `sweet_thermal_host_test.c` since 2026-11-12: compiled and run by tools/test_sweet_thermal.py (a unit test, outside the census graph); host driver for xnu/patches/0336: policy edges of sweet_thermal.h plus the charger model of chg_icl_host_test.c
- `trace2seq.py` since 2026-10-31: spec-draft aid for the S1 spec step, run by hand from docs/PORT-METHOD.md S1 (no automatic caller); review by 2026-11-14
- `wdt-check` since 2026-11-01: caller is the run plan pr4-wdt-hang (T-5d2f child, not yet written) and tools/test_wdt_build.py; PR-4 watchdog replay and lint
- `wdt_host_env.h` since 2026-11-01: header of tools/wdt_host_test.c (fake register file for the host test of xnu/patches/0193)
- `wdt_host_test.c` since 2026-11-01: compiled and run by tools/test_wdt_build.py (a unit test, outside the census graph); host driver for xnu/patches/0193 sweet_wdt.c
- `xnu-build-guard.py` since 2026-11-09: stale-object guard called by tools/xnu-local-build on every build

## Duplicate hints

- `chg_gate45_test.c` ~ `chg_recheck_test.c` (same usage words, 61%)
- `chg_i2c_host_test.c` ~ `chg_icl_host_test.c` (same usage words, 79%)
- `gen-wlan-a80211-patch` ~ `gen-wlan-en0-patch` (same usage words, 64%)
- `gen-wlan-en0-patch` ~ `gen-wlan-registry-patch` (same usage words, 62%)
- `gpuclk_audit.py` ~ `mifclk_audit.py` (same usage words, 60%)

## Data files

- `90-pixel-usbnet.rules`
- `bootargs-extra.txt`
- `bootargs-known.txt`
- `check-readme.known`
- `claude-settings.json`
- `common-files.txt`
- `gate-identity.expected`
- `gate-lint-steps.tsv`
- `gate-manifest-ceilings.json`
- `gate-manifest.json`
- `gate-unit-durations.tsv`
- `license-map.txt`
- `lint-consumed-allow.txt`
- `lint-silent-allow.txt`
- `phone-converge-allow.txt`
- `public-push-guard-baseline.tsv`
- `public-push-guard.rules`
- `resident-profiles.example.json`
- `run-intents.json`
- `super-callers.tsv`
- `tools-lifecycle.tsv`
- `tools-status.tsv`
- `unit-skip-allow.txt`
