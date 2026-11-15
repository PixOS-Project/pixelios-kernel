# Review bt-debug-clk-r1: golden-pull bt-debug per-clock read (branch tools/bt-debug-clk-20u, ab1d413 on base c504db5)

Reviewer: Sonnet 5.5, adversarial, read-only. Files: tools/golden-pull 178-185 + 560-565, tools/test_golden_capture.sh 857-871.
Tags: [src] read from source with cite, [obs] observed on the host now, [guess].
Sources: CORE `kernel/common` android14-6.1 drivers/clk/clk.c and GS `soc/gs` @ 2040787587 (composite.c, clk-gs101.c) were fetched from android.googlesource.com into /tmp/rv2/src (not present in ~/pixelios-artifacts; the same heads the research note used, NOT the exact phone build ab13615798 [guess that the phone build matches]). Line numbers below are in the fetched head and are ~2 lower than the note's 3682-3695.
`shellcheck -S warning tools/golden-pull tools/test_golden_capture.sh`: no findings, exit 0 [obs].
`bash tools/test_golden_capture.sh` (copy in /tmp/rv2/wt): `RESULT: PASS test_golden_capture`, exit 0 [obs]. Mutants ran on a reduced test (lines 1-117, 617, bt-debug rows 851-870) in /tmp/rv2/m: 98 ok rows, PASS [obs].

## Verdict: SAFE WITH CONDITIONS
Hardware safety (the point of the change): SAFE [src]. The script reads no file that recalculates a rate or touches a CMU register. But the script as written is functionally broken on the real phone (F1: it can never PASS) and its UNREADABLE detection is racy (F2). Fix F1 and F2 before the run; F3-F6 are test/doc follow-ups.

## Requirement 1: per-file classification (clk_debug_create_one, clk.c:3672-3710) [src]
| file | handler | hw op? |
|---|---|---|
| clk_enable_count / clk_prepare_count / clk_protect_count | `debugfs_create_u32` on `core->enable_count` etc. (clk.c:3685-3688) | no, plain field read |
| clk_phase | `debugfs_create_u32("clk_phase", ..., &core->phase)` (clk.c:3684) | no: the cached field, NOT `clk_core_get_phase`/`ops->get_phase` (that is only the summary/dump path, clk.c:3038-3050) |
| clk_flags | `clk_flags_show` prints `core->flags` bits (clk.c:3514-3530) | no |
| clk_min_rate / clk_max_rate | `clk_core_get_boundaries` under `clk_prepare_lock` reads `core->min_rate/max_rate` and the `clk_user` list (clk.c:762-776, 3644-3668) | no hw op; takes the global prepare mutex (blocks while another clock op holds it, bounded by the 90 s BT timeout) |
| clk_possible_parents | `possible_parents_show` -> `clk_core_get_parent_by_index` -> `clk_core_fill_parent_index` (clk.c:532-540, 3572-3582) | no: name/index lookup, no ops call |
No `ops->debug_init` hook in GS composite.c (grep: none) so the GS driver adds no extra files [src]. `clk_rate` (clk.c:3481-3492, recalc), `clk_duty_cycle` and `clk_summary`/`clk_dump`/`clk_orphan_*` are NOT read by the script [obs: sent text printed below]. Every file the script cats is a cached/no-hw file.

## Requirement 2
- `ls $d` is a readdir of the debugfs clk directory (dentry cache); no clk op is reachable from readdir [src: debugfs_create_dir only; guess for toybox `ls` stat'ing entries: debugfs getattr is generic, no clk code]. `[ -d "$d/$c" ]` is a stat of a dentry, same. SAFE.
- `cat` of a nonexistent path: open() -> ENOENT in the VFS, no driver involvement; harmless [src: debugfs lookup]. It does print a `cat: ...: No such file or directory` line (see F1/F2).

## Findings

### F1 (HIGH, functional, rule 8/fail-always) tools/golden-pull:184 (`bt_clk_files` includes `clk_possible_parents`)
`clk_possible_parents` is created only `if (core->num_parents > 1)` (clk.c:3708-3710) [src]. Every GS vclk is registered with `init.num_parents = (list->parent ? 1 : 0)` (composite.c:1629 in the fetched head; the NOCACHE flag is the line 1627 above it) [src], and both clocks are vclks with a NULL parent: `HWACG_VCLK(MUX_APM_FUNC, ..., "MUX_APM_FUNC", NULL, 0, VCLK_GATE, NULL)` (clk-gs101.c:47-53) and `VCLK(DOUT_CLK_APM_USI1_UART, ..., "DOUT_CLK_APM_USI1_UART", 0, 0, NULL)` (clk-gs101.c:1097-1102) [src]. So num_parents is 0 or 1 for both and `clk_possible_parents` does not exist for either (neither does `clk_parent` for num_parents == 0) [src; guess that the phone build matches].
Reproduced [obs]: the sent text run against a fake tree WITHOUT that file prints, per clock, `cat: .../clk_possible_parents: No such file or directory` + `UNREADABLE <clk> clk_possible_parents` -> 2 GAPs per `bt-debug` run, so `bt-debug on|off` ends FAIL every time (the real values are still captured, but the golden run is never green and the 2 permanent GAPs hide a real one). The host test cannot see it: its fake tree (test:864) creates `clk_possible_parents` itself, encoding the same assumption (the AP-248 trap).
Fix: delete `clk_possible_parents` from `bt_clk_files` (golden-pull:184), from the literal in test:860 (`"clk_enable_count clk_prepare_count clk_protect_count clk_phase clk_flags clk_min_rate clk_max_rate"`), from the fake-tree file loop (test:864) and change the expected counts `16 0 1` -> `14 0 1` (test:866). Optionally add `clk_accuracy` (`debugfs_create_ulong` on `core->accuracy`, clk.c:3683, cached) and `clk_notifier_count` (clk.c:3690) in its place. If the file is wanted, make it optional with an explicit `[ -e "$d/$c/$f" ] || { echo "ABSENT $c $f (num_parents<=1)"; continue; }` that is NOT counted as a GAP, and add a test row with the file absent.

### F2 (MEDIUM, silent-miss race) tools/golden-pull:185 (`cat "$d/$c/$f" || echo "UNREADABLE $c $f"`) and :565 (`grep -E '^(MISSING clk|UNREADABLE) '`)
The line prefix is written with `printf '%s %s '` (no newline) and `cat`'s error goes to stderr while `UNREADABLE ...` goes to stdout. BT() merges with `2>&1` on the HOST, where `adb shell` (shell protocol) delivers device stdout and stderr as separate streams, so their relative order is not guaranteed [guess: adbd reads the two pipes independently]. When stderr arrives first the file is `C F cat: ...` / `UNREADABLE C F` (anchored match works); when it arrives late the stdout stream is `C F UNREADABLE C F` on one line and `^UNREADABLE ` does not match: the GAP is silently missed. Reproduced [obs]: stdout and stderr captured separately and concatenated stdout-first -> `grep -cE '^UNREADABLE '` = 0 while the stdout file holds 2 `UNREADABLE` words. The host test runs `sh 2>&1` (one pipe, in-order) so it cannot show it.
Fix (script text): replace the per-file step with one atomic stdout line and no stderr: `if v=$(cat "$d/$c/$f" 2>/dev/null); then echo "$c $f $v"; else echo "UNREADABLE $c $f"; fi`. (Each file then prints exactly one line, `UNREADABLE` always starts a line, and the blank `echo` can go.) Also make the host grep order-proof: `grep -E '^(MISSING clk|UNREADABLE) |(^| )UNREADABLE [A-Z]' ...` and keep the 16/14-row test, adding a row that feeds a line `C F UNREADABLE C F` through the GAP loop.

### F3 (LOW, test strength) tools/test_golden_capture.sh:857-871 (the `ls $d | grep | sed` part is unpinned)
Mutant M12 (drop the `ls $d | grep -E ... | sed 's/^/clk-dir /'` step) leaves all rows green [obs]. The `clk-dir` lines are the only evidence of the real clock names when a name is MISSING, so the dir listing should be pinned: add a fake-tree row asserting `clk-dir MUX_APM_FUNC` and `clk-dir DOUT_CLK_APM_USI1_UART` are printed (and, with the tree missing a name, that the sibling `MUX_APM_FUNCSRC`-style name appears).
Other mutants, all killed [obs]: re-add clk_summary (M1, 1 row), drop UNREADABLE from the grep (M2, 2 rows), drop MISSING from the grep (M3, 2), rename one file (M4, 2), add `clk_rate` to the file list (M5, 3), swap a clock name (M6, 3), drop the final marker (M7, 7), drop the MISSING echo (M8, 5), drop the UNREADABLE echo (M9, 1), delete the GAP loop (M10, 4), loop without `bad` increment (M11, 2). Rows bind. The weakest is M1: only the "never reads clk_summary" row catches it, and it only greps the LOG (acceptable, it is the sent text).

### F4 (LOW, wording) tools/golden-pull:185 / absent debugfs
When debugfs is not mounted or the dir is root-only for a non-root `sh`, the sent text prints `ls: ...: No such file` + `MISSING clk MUX_APM_FUNC` + `MISSING clk DOUT_CLK_APM_USI1_UART` + `debugfs=ok` [obs on a nonexistent dir]. That is loud (2 GAPs via the host grep, so no silent PASS; the question "all clocks MISSING with debugfs=ok" is a GAP, not a PASS) but the message says "MISSING clk", not "debugfs absent", and `debugfs=ok` is printed regardless. gpio/regulator files catch the mount first (they have no `debugfs=ok` when the mount is missing), so the clk file adds only noise there. Fix (optional): `[ -d "$d" ] || { echo "UNREADABLE debugfs-clk-dir $d"; }` before the loop so the GAP names the cause.

### F5 (LOW, docs) docs/runplans/golden-debugfs.md:15,19,31, docs/runplans/golden-bt-s0.md:17, docs/runplans/golden-bt-cold.md (`bt-debug on`)
These still say bt-debug reads `clk_summary`. Update them to "per-clock cached files of MUX_APM_FUNC and DOUT_CLK_APM_USI1_UART (bt-debug-<on|off>-clk.txt, lines `<clk> <file> <value>`; GAP on `MISSING clk` / `UNREADABLE`)". golden-debugfs.md:19 hazard text names clk_summary as one of two candidate reads; after this change only regulator_summary remains (see F6). No other caller reads bt-debug-*-clk.txt: grep over tools/ and docs/ (super repo) and the kernel worktree found only docs/golden/golden-bt.sh:124-126 (calls the action, does not parse the file) [obs]. tools/golden-pull:636-638 (`inventory` skip of clk_summary/regulator_summary) is unrelated and stays.

### F6 (LOW, safety carry-over) tools/golden-pull:561 (`cat /sys/kernel/debug/regulator/regulator_summary`)
The research note analyses only the clk path; regulator_summary is not covered by any source reading and golden-debugfs.md:19 says "which read = unknown" for the 2026-10-27 reboot [read from docs]. The change keeps this read, now FIRST of the two risky ones, in the same `bt-debug` action. AP-252's "rebooted twice" is tagged [obs] for clk_summary only. Not a defect of this commit, but the run plan should state that regulator_summary is still an unproven hazard [guess] and that pstore capture (research steps 5) is armed before the run.

### F7 (INFO) mask and quoting
- `bt_guard "$b_c"` passes for the real string (no `rm `, `flash`, `dd if=` ...) [obs: eval of bt_forbid on the extracted bt_clk_script printed GUARD-OK].
- Quoting survives: the double-quoted `bt_clk_script` yields the literal text `d=/sys/kernel/debug/clk; ls $d | grep -E 'APM_FUNC|APM_USI._UART' | sed 's/^/clk-dir /'; for c in ...; echo debugfs=ok` [obs]; BT() appends `; echo __GP_RC=$?` as a separate command (the `&&` chain is not needed because `debugfs=ok` is the last command), `tr -d '\r'` handles CRLF, no `set -e`/`-u` in the sent sh.
- bt_mask (golden-pull:175) cannot hit these values: clock names have `_`/letters, counts and rates are decimal and a 20-digit `ULONG_MAX` max_rate has no `\b[0-9A-Fa-f]{12}\b` hit [obs: 0 masked on a fake tree with 18446744073709551615]. Only a rate of exactly 12 decimal digits (>= 100 GHz) would be masked; not plausible for these clocks [guess].
- `su` error output / denied: no `debugfs=ok` -> the BT() marker GAP fires (existing test row "su denied -> FAIL" stays green).

## Summary of required changes
F1 (drop the nonexistent file) and F2 (atomic line, order-proof grep) before the run; F3-F6 may follow. After F1/F2 re-run the gate and the mutant list M1-M12 above.

## Status (driver 20u)
F1 (clk_possible_parents dropped: absent on parentless vclks) and F2 ($(cat) captures each value, one whole line) fixed; the fake tree now mirrors the real one (no clk_possible_parents) and a row pins the 2 clk-dir evidence lines (F3). F5 (runplans still say clk_summary) -> sent to 21p. F6 regulator_summary: not part of this change (not the D8 reboot; own research would be needed). Host test RESULT: PASS test_golden_capture.
