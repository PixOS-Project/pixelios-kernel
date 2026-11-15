# Review dev-speed-gaps r1 (branch tools/dev-speed-gaps, head 9c22f2a, merge-base a69c4cc)

Verdict: SAFE WITH CONDITIONS. No defect that can write the phone or turn a build failure into a PASS. One rule-level gap (F1, one-heavy-job) and four low items.
Tags: [obs] = observed by running it here, [src] = read from source (file:line).

## 1. xrun TP-040: old vs new boot-args (harness /tmp/rv/cmp.sh, /tmp/rv/old|new = git archive of both commits)
Old xrun had no --print-bootargs, so the old side is the old xrun copied up to the sweetremote line, plus its verbatim old build-xnu-image.sh call under XNU_IMAGE_ARGS_ONLY=1 (the old `$rw $rails ... "${dvfs[@]}"` expansion). New side is `tools/xrun --print-bootargs`. [obs]
Result: 21 option combinations, 21 byte-identical boot-args lines (len = bytes of the line after "boot-args: ").

| combination | implicit (new) | len | old==new |
|---|---|---|---|
| (none) / --expect foo / --early --expect foo | sweetremote=1 | 236 | SAME |
| --no-remote-reset | none | 222 | SAME |
| --no-usbnet (+/- --no-remote-reset) | none | 222 | SAME |
| --args a=1 | sweetremote=1 | 240 | SAME |
| --args a=1 --no-remote-reset | none | 226 | SAME |
| --args sweetremote=1 (+/- --no-remote-reset) | none (not doubled) | 236 | SAME |
| --cpus 4 | sweetremote=1 | 236 | SAME |
| --cpus 2 --restart 60,x / --restart 90 | sweetremote=1 | 254 / 252 | SAME |
| --remote-reset --no-usbnet | sweetremote=1 | 236 | SAME |
| --usbdma | sweetremote=1 | 250 | SAME |
| --gui --rails --gpu-handover | sweetremote=1 | 290 | SAME |
| --wlan-hold --wlan-level 2 | sweetremote=1 | 248 | SAME |
| --dvfs read / --dvfs 5 --rw | sweetremote=1 | 236 | SAME |
| --qr on --full (both refuse: "--qr needs seconds or off") | - | 0 | SAME |
| --args foo=bar --restart 30 --cpus 6 --gpu-handover --dvfs read --gui | sweetremote=1 | 314 | SAME |

sweetremote=1 claim: [src] tools/xrun:54 (`noremote=1 || usbnet empty || remote=1`), :55 adds it unless already in --args; identical to the old line. [obs] added with default usbnet, not with --no-remote-reset or --no-usbnet. Pre-existing quirk, unchanged: an explicit --remote-reset wins over --no-usbnet and over --no-remote-reset (remote is set in the option loop and never cleared), so sweetremote=1 is added there too; old behaviour is the same.
Argv-quoting change: old `$rw $rails ...` was word-split, new `${rw:+"$rw"}` is quoted; all values are single fixed flags, so no difference [obs above]. `${dvfs[@]+"${dvfs[@]}"}` is safe under set -u.
Behaviour changes found: (a) run-preflight now passes the full argv to xrun, so an unknown xrun option is a cmdline FAIL (old preflight silently skipped unknown words); intended and stricter. (b) F3.

## 2. gate-pre + gate-xnu-build (TP-039)
- Build failure blocks loudly: [src] remote-job:159-166 dies with "gate-pre refused before shipping ... (RESULT line)" on any non-zero rc; gate-xnu-build:55-70 maps rc 1 -> "RESULT: FAIL xnu-build errors=N first=FILE:LINE", other rc -> ERROR rc 2. [obs] mutation `[ "$xrc" = 0 ] || die` -> `true || die` makes test_remote_job.sh FAIL (case "XNU stack that does not build is refused before ssh").
- Missing toolchain / timeout cannot become PASS: [src] gate-xnu-build rc 0 needs a "RESULT: PASS" line, a sha256 line, the kernel file in the label's work tree and sha256(kernel)==reported sha (lines 50-58); xnu-local-build maps timeout to "RESULT: ERROR timeout" exit 2 (xnu-local-build:41-42, default 3600 s) and ERROR to exit 2 (:37). [obs] mutations: rc1->exit 0, drop sha compare, drop PASS-line check, drop SKIP guard, drop flock each make test_gate_xnu_build.py FAIL (5/5 killed).
- Unrelated checks: [src] remote-job:159 `[ "$job" = gate-pre ]` only (not gate-identity, not other jobs); offload-only/--where local refusals come first (:149-150).
- Lock: label flock only (gate-xnu-build:40-41). See F1.
Tests: test_gate_xnu_build.py `Ran 7 tests ... OK`; test_remote_job.sh `RESULT: PASS cases=99`. [obs]

## 3. run-preflight adt / cmdline
- adt [obs via c_adt with PREFLIGHT_ROOT]: real dir with d22ap.adt PASS; worktree where research is a valid symlink PASS; dangling symlink FAIL; missing FAIL; research/ipsw-x/d22ap.adt a directory FAIL; --adt given PASS; non-xrun SKIP. Check is os.path.isfile(ROOT/research/ipsw-x/d22ap.adt) = the exact path mkadt tests (tools/mkadt.py:46 uses tools/../research/ipsw-x/d22ap.adt, os.path.exists). No false FAIL for the kernel clone (real research dir). Zero-byte file passes (F5).
- cmdline: [src] counts len(line after "boot-args: ").encode() against BOOT_LINE_LENGTH-1 = 607 (run-preflight:172); mkpayload.py:39,148-150 checks len(cmdline.encode()) > 607 on the same string (`--cmdline "$args"`, build-xnu-image.sh) and xrun builds it from the same bxi array that --print-bootargs uses. [obs] a 380-char --args gives a 618-byte line and a FAIL path. Counted string == sent string.
Tests: test_run_preflight.py `Ran 53 tests ... OK`; mutations adt->always PASS and dropping the implicit add both FAIL the suite. [obs]

## 4. worktree-new
[src] tools/worktree-new:27-31: base default trunk (:15), refuses existing branch (:28) and existing/dangling-link dir (:30), only `git worktree add -b`, `ln -s`, `mkdir -p logs`; no rm/overwrite anywhere. A failed link after creation leaves the worktree and exits 1 naming the gap. shellcheck -S warning: clean. test_worktree_new.py `Ran 6 tests ... OK`.

## 5. AP-152 hermetic
[obs] grep of tools/test_*.py|sh for /tmp: no remaining fixed-path read (hits are command strings under test, /data/local/tmp, and $w/tmp). F3 plan/script now built in-test. test_hermetic_inputs.py `Ran 2 tests ... OK`. test_cpu_timeout.py `Ran 13 tests ... OK` (CPU-time bound instead of wall).

## 6. .gitignore
`git ls-files research logs` -> 0 entries [obs]; `/research` and `/logs` are anchored, `docs/research` is not ignored [obs git check-ignore]. Safe.

## 7. Tool results [obs]
shellcheck -S warning tools/gate-xnu-build tools/remote-job tools/xrun tools/worktree-new tools/test_remote_job.sh -> no output, rc 0 (run-preflight is Python, not shell). Tests: test_gate_xnu_build OK(7), test_hermetic_inputs OK(2), test_run_preflight OK(53), test_worktree_new OK(6), test_cpu_timeout OK(13), test_remote_job.sh RESULT: PASS cases=99.

## Findings
F1 (medium, rule: one heavy job) tools/gate-xnu-build:40-41. The only lock is per label (`.<label>.gate-lock`, default `gate-<worktree dir>`), and tools/xnu-local-build has no flock (grep: none). Two worktrees running gate-pre at once run two XNU builds (-j4 each, plus a possible concurrent first-time toolchain fetch into the shared $LOCALBUILD). Fix: after the label lock, also take a global lock `exec 8>>"$lb/work/.gate-xnu-build.heavy.lock"; echo "xnu-build: waiting for another XNU build" ; flock -w 3600 8 || err "another XNU build holds the heavy lock >1 h"`, and add a test with two labels.
F2 (low-medium) tools/gate-xnu-build:27-29. "SKIPPED" when HEAD:xnu/patches == trunk's tree assumes trunk compiles; nothing proves it (guess), which is the AP-147 failure mode if a bad patch landed on trunk. Fix: write `${ht}` to `$lb/work/.built-ok-trees` on PASS and skip only when trunk's tree is listed there; otherwise build.
F3 (low) tools/xrun:44 vs :58. `--print-bootargs --dry-run --from-dir D` runs the dry-run branch first, which rewrites report.txt/merged.log in D (xrun header line 25-26). run-preflight on a `--dry-run` command would therefore have a side effect (and FAIL cmdline without --from-dir). Fix: make the dry branch `if [ "$dry" = 1 ] && [ "$printba" = 0 ]` or have c_cmdline return SKIP when "--dry-run" in argv.
F4 (low) tools/remote-job:159-166. The local XNU build (up to 3600 s cold) runs before <offload-host> is probed, and outside --timeout; a down <offload-host> refuses the job after the build. Fix: acceptable; or move the block after the host probe and say in the header that --timeout does not cover it.
F5 (low) tools/run-preflight:c_adt, tools/gate-xnu-build:45. (a) isfile accepts a zero-byte d22ap.adt (mkadt then dies in adt.parse, loud but late): add `and os.path.getsize(want) > 0`. (b) gate-xnu-build traps only EXIT, so SIGTERM from a timeout leaves /tmp/gate-xnu-stack.*: use `trap 'rm -rf -- "$stack"' EXIT INT TERM`. (c) the FAIL fix text in the kernel clone itself would read `ln -s <clone>/research <clone>/research`: print "restore research/ipsw-x/d22ap.adt" when main == ROOT.
No finding touches a hardware path; no secrets added.

## Driver 14 disposition (2026-10-28)
- F1 FIXED: global flock `$LOCALBUILD/.build-lock` in tools/xnu-local-build (outer process; the timeout child inherits it); busy -> waits up to --timeout, then `RESULT: ERROR another xnu-local-build held ...`. Test XnuLocalBuildLock (test_gate_xnu_build.py); the lock-removed mutant FAILS it [obs].
- F2 ACCEPTED (follow-up): SKIPPED on a patches tree equal to trunk's relies on trunk having been compiled; every stack change reaches trunk only through this gate from now on, so the gap closes going forward. Not proven for today's trunk a69c4cc; driver 14 compiled the usbroot-hfs-trace stack (= next trunk) by hand: xnu-local-build PASS.
- F3 FIXED: xrun's --dry-run branch is skipped when --print-bootargs is set; checked by hand: report.txt in --from-dir unchanged [obs].
- F4 ACCEPTED: documented here; the local build runs before the <offload-host> probe and outside the remote --timeout, bounded by its own --timeout 3600.
- F5 FIXED: adt check refuses a zero-byte d22ap.adt (new test), no self-link fix text when the worktree is the kernel clone (test updated), gate-xnu-build `trap 'exit 143' TERM INT` so the EXIT cleanup runs on a signal.
