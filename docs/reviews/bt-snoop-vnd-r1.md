# Review bt-snoop-vnd-r1 (reviewer Sonnet 5.5, 2026-10-29)

Commit under review: 4bc1470 (golden-pull bt-snoop-pull also pulls /data/vendor/bluetooth/btsnoop_hci_vnd.log). Merge f373403 not reviewed.

## Verdict: SAFE WITH CONDITIONS

The tool change is correct and read-only. The test rows kill the main mutants but leave four survivors (F1-F4, all test-side, low). No blocking defect in tools/golden-pull.

## Verification [observed, 2026-10-29]
- `bash tools/test_golden_capture.sh` in the worktree: rc=0, `RESULT: PASS test_golden_capture`, 0 FAIL lines. `shellcheck -S warning tools/golden-pull tools/test_golden_capture.sh`: no findings.
- `tools/golden-pull --dry-run bt-snoop-pull`: lists the stat script (with vnd path) and three `would run: adb exec-out su -c "cat ..."` lines incl. vnd; no adb call, no out dir created.

## Requirement check
1. Read-only [read from source, tools/golden-pull:533-535]: vnd is only in `stat`, `[ -e ]` and `cat`. bt_guard receives `$b_sz`, `cat $bs`, `cat $bs.last`, `cat $bv` (line 535); none of the path text matches bt_forbid (golden-pull:171: no `flash`, `rm `, `dd of=`...). OK.
2. Missing/size 0 vnd = INFO (line 543, info++), not bad; wrong magic vnd = INFO (line 547); main missing/size 0/wrong magic = GAP (lines 541, 548), .last missing = INFO (line 543). Branch condition `[ "$bf" = "$bs" ]` keeps main behaviour unchanged. OK.
3. Contents: R writes to `$out/bt/<name>` only (golden-pull:148); the magic check reads via head|od, nothing is echoed. awk `$2==p` with p=vnd path works (test "files" row proves the file is pulled; mutant M3 below). Dry-run OK (see above).
4. Rule 8: R (golden-pull:148-150) turns a failed pull (no `__GP_RC=0` sentinel: su denied, adb failure) and an empty pull into a counted GAP for every file, vnd included. So an existing non-empty vnd file that cannot be read is a GAP, not silent. Consistent with .last. Note (not a defect): this means an unreadable (SELinux/su) vnd file fails the whole action although the path is a [guess]; acceptable, loud, and the file is only attempted after stat proved it exists.
5. Mutants (copy of HEAD in /tmp/20s-rev/wt; baseline there has an unrelated third_party failure, so only FAIL rows count):
   - M1 vnd missing -> GAP: KILLED (2 rows: "no vnd file -> PASS with INFO", "vnd INFO named").
   - M2 vnd contents printed: KILLED ("vnd contents never printed", RESULT line, magic INFO).
   - M3 vnd skipped in the pull loop: KILLED ("files", both INFO-named rows).
   - M5 vnd wrong magic -> GAP: KILLED (2 rows).
   - M4 vnd dropped from the stat script: SURVIVED (F1).
   - M6 vnd dropped from the dry-run listing: SURVIVED (F2).
   - M7 vnd `cat` removed from the bt_guard call: SURVIVED (F3).
   - M10 vnd-missing INFO not counted in info=: SURVIVED (F4).

## Findings
F1 (low) tools/test_golden_capture.sh:37-40: the fake adb answers the stat call with a fixed vnd line whatever the script text, and no row asserts that the sent script contains the vnd path. Dropping `$bv` from `b_sz` (golden-pull:533) passes the test, but on a real phone the vnd file would then never be sized, so never pulled, silently. Fix: after `gb bt-snoop-pull` add `grep -qF /data/vendor/bluetooth/btsnoop_hci_vnd.log "$LOG"; ck "bt-snoop-pull: stat script names the vnd file" 0 $?` (the stdin script is logged in $LOG by the fake su branch).
F2 (low) test_golden_capture.sh:~689: the dry-run row only checks "no adb call". Fix: `gp --dry-run --out "$w/gbd" bt-snoop-pull | grep -qF 'cat /data/vendor/bluetooth/btsnoop_hci_vnd.log'; ck "bt-snoop-pull dry-run: lists the vnd cat" 0 $?`.
F3 (low) test_golden_capture.sh:~688: the "no forbidden word" row only greps what the fake logged, so a `cat $bv` missing from bt_guard (golden-pull:535) is not detected. Harmless today (the string cannot match bt_forbid) but the guard covers the other three. Fix: either keep as is, or add the vnd cat to a grep of `$LOG` for the exact `cat /data/vendor/bluetooth/btsnoop_hci_vnd.log` line (same row as F1 with `-x`-less match on the exec-out line).
F4 (low) test_golden_capture.sh:670-678: no row pins `info=N` on the RESULT line, so the INFO counting for vnd (golden-pull:543,547) is untested (also true for .last before this commit). Fix: `grep -q 'info=1$' "$w/out"` after the FAKE_BT_NOVND and FAKE_BT_VNDMAGIC rows (and `info=0$` after the PASS row).
F5 (info) Untested paths for the new file: vnd size 0 (fake cannot emit it), vnd empty pull (fake vnd branch ignores FAKE_BT_EMPTYPULL, test_golden_capture.sh:37) and vnd pull rc!=0. They go through the shared R/size code already covered for the main file, so risk is low. Optional fix: add `FAKE_BT_VNDSZ` to the fake and a row expecting `GAP: pull of 'cat /data/vendor...' is empty` for the empty case.
F6 (info) golden-pull:546-548: after a failed vnd pull R already counted a GAP and the magic check then adds an INFO about the file (which holds the sentinel text). Noise only; the same happens for the main file with a doubled GAP. No fix needed.
F7 (info, rule 7) The vnd path is a [guess] (header golden-pull:53 and the inline comment say so); the code is read-only so a wrong path costs only an INFO. The first golden-bt-cold run should record whether the file exists and turn the guess into an observed fact in the spec/run result.

## Resolution (driver 20s, 2026-10-29)
F1 (size script row), F2 (dry-run vnd cat row) and F4 (info=0/info=1 rows) applied in test_golden_capture.sh; host test PASS, shellcheck clean. F3 not applied (the vnd cat string cannot match bt_forbid; the guard call is covered by reading). F5/F6 no change. F7: the first golden-bt-cold run records whether the vnd file exists.
