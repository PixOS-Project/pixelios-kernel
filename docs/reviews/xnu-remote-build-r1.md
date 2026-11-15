# Review r1: kernel commit bc3e74a (R26 dispatcher, tools/xnu-remote-build)

Reviewer: Sonnet 5.5, adversarial. Checkout: /home/<user>/.t3/worktrees/review-bc3e74a (detached bc3e74a, diff 93b5af6..bc3e74a). Spec: /tmp/20x/dispatch-spec.md.
No ssh, no <offload-host>, no phone. Tags: [obs] = observed in a host run now, [src] = read from source (file:line), [guess] = not shown.

## Verdict: SAFE WITH CONDITIONS

No path found that reports a failed or partial <offload-host> build as PASS or reuses a stale kernel on a PASS: the sha256 of both copied kernels is re-computed locally after rsync and compared with the
`sha256:` line the remote build printed (xnu-remote-build:127-130) [src]; the fallback is only reachable before the build starts, and prints on stdout and stderr (:171-177) [src]; the ledger
is only written after the sha check (:131) [src]. The conditions are test wiring (F1, F2), the gate step budget vs the <offload-host> wait (F3), and provenance (F4). None can corrupt a kernel that runs on the phone.

## Test results [obs]
- tools/test_xnu_remote_build.sh: `bash tools/test_xnu_remote_build.sh` RESULT: PASS cases=50 (47 + 3 own mutants). Run directly it fails: `Permission denied` (git mode 100644, F1).
- tools/test_remote_job.sh RESULT: PASS cases=223. tools/test_heavy_job.sh RESULT: PASS cases=104. tools/test_xnu_src_mirror.sh RESULT: PASS cases=48.
- tools/test_xrun_report.py: OK (skipped=16). tools/test_gate_xnu_build.py: OK (note: its two real xnu-local-build rows pass `--timeout 2`, which is now an "unsupported remotely" flag, so they hit the SKIP path, not the dispatcher; they still pass).
- `shellcheck -S warning` on xnu-remote-build, xnub-line.sh, test_xnu_remote_build.sh, xnu-local-build, gate-xnu-build, remote-job, xrun, test_remote_job.sh: no findings.

## Findings

F1 [Medium] tools/test_xnu_remote_build.sh (git mode 100644), tools/check-all.sh (no reference), tools/tools-lifecycle.tsv:131.
The suite is not wired into any gate step (`grep xnu_remote tools/check-all.sh` is empty) and is committed non-executable, so `tools/test_xnu_remote_build.sh` (the command the lifecycle row and the header name)
exits 126 `Permission denied` [obs]. The dispatcher can regress with a green gate (AP-257 pattern: gate selects xnu steps but never the dispatcher test).
Fix: `git update-index --chmod=+x tools/test_xnu_remote_build.sh`; add a check-all step next to the other shell host tests (same list that runs test_remote_job.sh/test_heavy_job.sh) and add the file to the gate_select.py XNU_BUILD_TOOLS/test mapping so a change to xnu-remote-build, xnub-line.sh or xnu-local-build selects it.

F2 [Medium] tools/test_xnu_remote_build.sh (happy path + coarse failure rows) vs tools/xnu-remote-build.
The tests kill the 3 built-in mutants but many behaviour-changing mutants survive. I ran each in a copy under /tmp/20y/mut/<name>/ with `XRB_MUTANT=1 XRB_TOOLS_DIR=<copy>`; control copy = PASS cases=47, and each of these also gave `RESULT: PASS cases=47` (not killed) [obs]:
 1. k2skip: loop `for f in "$k1" "$k2"` (:127) reduced to `"$k1"`: the work-tree kernel (the one gate-xnu-build reads) is no longer verified. Test only changes the REPORTED sha, so k1 alone trips. Likewise k1skip (only k2).
 2. nokill: delete `"$DK" kill "rj-xnub-$NAME"` from cleanup (:75): a running container is left on <offload-host> after an abort; fake docker `kill` is a no-op and no row checks it.
 3. noreported: delete `[ -e "$st/reported" ] || err ...` (:205): a slot killed mid-build would exit with heavy-job's rc (1 = "FAIL" or 0) with no report. No row kills the slot.
 4. labelregex (:164), jobscap (:166), nopass-check (:118: rc 0 without `RESULT: PASS` accepted), nosha-line (:120), unreach-grep (:201: unreachable at queue time) all survive.
Fix: add rows: (a) corrupt only the work-tree copy (fake rsync alters the 2nd transfer) -> RESULT: FAIL; (b) fake ssh rc 255 after the build started -> exit 2, ERROR, no FALLBACK, `kill` seen in docker.log; (c) SIGTERM the slot mid-build -> exit 2 "ended ... without a report"; (d) remote rc 0 without PASS line; (e) `--label ../x` and `--jobs 9` rows; (f) ssh unreachable only at queue time (hj.err contains `<offload-host> unreachable`) -> FALLBACK. Add 3 of these as built-in mutants.

F3 [Medium] tools/xnu-remote-build:168 (XNU_REMOTE_WAIT 5400), tools/check-all.sh:121 and :163, tools/gate-xnu-build.
The check-all `xnu-build` step has a 2400 s timeout (`xnu-build) b=2400`) [src], but the dispatcher waits up to 5400 s in the <offload-host> FIFO. The <lab-host> gate holds the <lab-host> heavy-job lock (HEAVY_JOB_HELD set) while it waits, and a formal gate-pre on <offload-host> holds the one <offload-host> slot for ~20 min or more. So a busy <offload-host> turns the <lab-host> gate's xnu step into a `timeout` kill (step FAIL, cause only in the step log) after holding the <lab-host> lock the whole time; previously it was a 63 s warm local build. This is the AP-261 shape (lane A queues on lane B's lock inside a held lock). The warning text at check-all.sh:163 still says "queue up to 1800 s" (now 5400/10800) [src].
Fix: in the check-all xnu-build step command export `XNU_REMOTE_WAIT=${XNU_REMOTE_WAIT:-1800}` (less than the step budget minus the build) and, when `HEAVY_JOB_HELD` is set and the wait expires, fall back to the local build (nested, takes no second lock) with `XNU REMOTE FALLBACK: <offload-host> queue busy for Ns`; update the stale warning; add a test row (queue busy + HEAVY_JOB_HELD set -> FALLBACK). Keep ERROR-without-fallback for the hand-started (no lock held) case if the spec stays as is.

F4 [Medium] tools/xnu-remote-build:81 (rsync of `$ROOT/tools/`), :82, :131; tools/xnu-local-build:683.
Provenance gap (spec gap 3, confirmed). The container runs the WORKING-TREE tools/ of `$ROOT` (uncommitted edits and untracked files included, only third_party/__pycache__ excluded) while gate-xnu-build ships the committed stack (`git archive HEAD xnu/patches`) and gate-pre ships a committed bundle [src]. The only identity checked is "copy sha == sha the remote build printed"; nothing ties the built kernel to a commit or a stack tree, and the ledger line is `date sha256 label where reason` (no commit sha, no dirty flag) [src]. Scenario: a driver edits tools/xnu-local-build, runs the gate; <offload-host> builds with the dirty tool, the ledger says `<offload-host> (label L)`, and the run summary cannot tell the kernel came from a dirty tools/ tree. Same exposure as a local build, but the local build was never recorded as "provably" anything and the dispatcher's docs claim a verified identity.
Fix: in the dispatcher compute `dirty=$(git -C "$ROOT" status --porcelain -- tools xnu/patches)` and `head=$(git -C "$ROOT" rev-parse --short HEAD)`; print `XNU REMOTE: WARNING building from a dirty tools/ tree (N files)` and put `head=<sha> dirty=N stack=<sha256 of "cd stack && find . -type f | sort | xargs sha256sum | sha256sum">` into the ledger reason column; have ledger-line print them. Optionally echo the stack hash from inside the container (`sha256sum` of /job/stack) and compare. State in the header that the identity is "sha256 of the kernel" only.

F5 [Medium] tools/xnu-remote-build:83, tools/xnub-line.sh:31-36 and :40, :46-48 (AP-270 repeated).
`xnub_check_mirror` runs on the <offload-host> HOST as the login uid (`cd "$JD" && xnub_check_mirror` via `bash -s`), while the offline build runs as `--user 1000:1000 --userns=keep-id` in the container with its own git: "dubious ownership" is decided there. The shared function moved the same host-side probe into a second caller instead of fixing it (AP-270 / offload-xnub-r2 F1) [src]. The login uid being 1000 on <offload-host> is not shown [guess]. Consequence: precheck PASS, then the in-container stager 0019 fails with a RESULT: FAIL/ERROR after the build started, no fallback. Same for the guard `case $W in "$HOME"/work/pixos/xnub-art/localbuild/work)` and `${d:?}` in xnub_prune: every caller passes the constant `$XA/localbuild/work`, so the refusal branch can never fire and no test calls it (`grep -n 'REFUSING to prune' tools/test_*` is empty) [obs].
Fix: run the mirror check inside the container: `"${XF[@]}" bash tools/xnu-src-mirror env --stack /job/stack --dir /home/<user>/pixelios-artifacts/git-mirror` (same image, uid, mounts); for xnub_prune either add a test that calls it with a foreign dir / symlink / empty work dir and expects rc 98 / WARNING / `pruned 0`, or drop the guard and say the path is a constant.

F6 [Low] tools/xnu-remote-build:131 (with :33).
`cmd_ledger_add ... || echo "WARNING ledger entry not written" >&2` is dead code: cmd_ledger_add reports failures through `err`, which does `exit 2`, so a failing append (full disk, unwritable $LB) exits the slot with `RESULT: ERROR xnu-remote-build: ledger-add: ...` AFTER the kernel was copied back and verified [obs: bash snippet `f || echo` with f calling `exit 2` prints no WARNING and exits 2]. Not dishonest (fail closed), but the intended behaviour and the doc ("xrun prints unknown") do not match, and no row covers it. The <lab-host> side (xnu-local-build:683) runs ledger-add as a separate process, so there the `|| say WARNING` does work.
Fix: run it as `( cmd_ledger_add ... ) || echo WARNING` (subshell) or make ledger-add return instead of exit when called from slot(); add a row with `built-on.tsv` made a directory.

F7 [Low] tools/gate-xnu-build:18 and the RESULT line (:~79), tools/check-all.sh GATE line.
A <lab-host> FALLBACK or SKIP is loud in the step log (`grep -a -E '^XNU REMOTE|^XNUB:'` repeated on the gate console) and in the xrun mark, but gate-xnu-build's final `RESULT: PASS xnu-build sha=... label=... stack=... Ns` carries no `where=`, and the GATE summary line does not tag it. A gate that built on the <lab-host> through the fallback reads identically to a <offload-host> one in the summary the driver and watcher read (rule 8: "show up in the run summary") [src].
Fix: have gate-xnu-build extract the last `XNU REMOTE FALLBACK|SKIP|built on <offload-host>` line and append `where=<offload-host>|<lab-host>-fallback|<lab-host>-local` to its RESULT line; let check-all tag the GATE line when where is not <offload-host>.

F8 [Low] tools/xnu-remote-build:199-204 vs spec item 3.
The spec lists "remote-job's load probe refuses" among the pre-build fallback reasons, but heavy-job <offload-host> run keeps re-probing admission until --wait, then errors `offload-queue: not admitted within Ns`; the dispatcher then exits 2 "slot not granted ... no fallback while waiting". A <offload-host> that stays overloaded (stuck foreign process, orphan rj- container) therefore never falls back, only errors after 5400 s [src]. Spec is self-contradictory (also "waiting is not a fallback reason"); needs a decision, combine with F3.
Fix: decide and document; recommended: admission refusal for longer than a short threshold (e.g. 300 s, `XNU_REMOTE_ADMIT_WAIT`) falls back loudly with the reason `<offload-host> not admitted: <heavy-job reason>`, queue position waits keep the long wait.

F9 [Low] tools/xnu-remote-build:108-116 vs tools/xnu-local-build:638.
On a <offload-host> FAIL/ERROR the previous `$LB/out/<label>/kernel.development.sweet` and `$LB/work/<label>/fakeroot/.../kernel.development.sweet` stay. The local build does `rm -f "$KERN"` at its start so no stale work-tree kernel survives a failure; the dispatcher only clears them on the PASS path (:122). gate-xnu-build is safe (it reads the kernel only after rc 0), but a hand caller who ignores the FAIL and runs `tools/xrun --kernel out/L/...` runs the old kernel, and the xrun mark reads `XNU BUILD: <offload-host> (label L)` for it (the ledger entry of the OLD sha) with no staleness hint [src].
Fix: `rm -f "$k1" "$k2"` before the rsync-back branch decision (right after `el=...`), i.e. on every outcome once the build started, or print `XNU REMOTE: previous kernel $k1 left in place (build failed)`.

F10 [Low] tools/xnu-remote-build:171-177, tools/heavy-job:263-266 [guess].
Lock-order inversion is possible only if a caller inside a <offload-host> slot (OFFLOAD_QUEUE_HELD set, no HEAVY_JOB_HELD) calls xnu-local-build and the dispatcher falls back: it then takes the <lab-host> lock while holding the <offload-host> slot, the reverse of a <lab-host> gate (<lab-host> lock, then <offload-host> slot). Today no such caller exists (the <offload-host> gate-pre route uses GATE_XNU_BUILD=0 and XNUB_IN_SLOT=1 in the build container; I found no other caller) [src]; both waits are bounded (10800 / 5400 s) so it would be a long stall, not a permanent deadlock.
Fix: in `unavailable()` when `OFFLOAD_QUEUE_HELD` is set and no `HEAVY_JOB_HELD`, print the reason and exit 2 instead of taking the <lab-host> lock; one test row.

F11 [Low] tools/test_xnu_remote_build.sh (fake ssh/docker), docs/LOCAL-BUILD.md (last bullet).
The fake ssh does `HOME=$FAKE_HOME exec "$@"` (a local bash), so quoting through the real <offload-host> login shell (fish) + `bash -s` is untested, as are the real container uid and `--network=none` (AP-262). The doc already tags the flow "guess until the first run"; the run plan for that first real build must exist before the dispatcher is made the default for the gate (rule 9). `xnub_check_art` (stdin script, `%q` values) is the same text remote-job already sends, which lowers the risk for the precheck only [src].
Fix: before merge, one real `XNU_BUILD_WHERE=<offload-host> tools/xnu-local-build --label r26-first` (human-approved <offload-host> run) with output quoted in the merge note; until then keep `XNU_BUILD_WHERE=local` documented as the escape hatch.

F12 [Info] tools/xnu-remote-build:92, tools/xnub-line.sh:37-50.
The shared prune after every <offload-host> build deletes every other label's work tree except `offload-gate` and the current one; two drivers alternating labels each get a cold rebuild (71 s cold per docs/LOCAL-BUILD.md:33, acceptable) and anything unusual placed in `work/` is deleted. Intended by the spec, noted only so the 71 s cold figure is not read as incremental.

## Checked and found sound [src unless tagged]
- Fallback honesty: `unavailable()` prints the same FALLBACK line on stdout and stderr, ledger records `<lab-host>-fallback` + reason through XNU_LEDGER_WHERE/REASON, XNU_BUILD_WHERE=<offload-host> never falls back (tests exist, [obs] killed by the silent-fallback mutant).
- No fallback after `started` exists; a slot that ended without `reported` is an ERROR (:205), a copy-back failure is ERROR rc 2 (:123-126), sha mismatch is FAIL rc 1 (:129).
- Nesting: heavy-job's `<offload-host> run` does not look at HEAVY_JOB_HELD; OFFLOAD_QUEUE_HELD runs the slot nested without a ticket (heavy-job:263-266, test row 1c); XNUB_IN_SLOT=1 is in the container argv (xnub-line.sh:22) and the hook skips on XLB_INNER / XNUB_IN_SLOT / XNU_BUILD_WHERE=local (xnu-local-build:31). HEAVY_JOB_WAIT 10800 applies only to the local path and is bounded by nothing in a held gate (nested, no wait).
- xrun: `tools/xnu-remote-build ledger-line` runs after `cd` to the repo root (xrun:36), failure degrades to `XNU BUILD: unknown (ledger lookup failed ...)`.
- remote-job refactor (-20 lines) keeps behaviour: test_remote_job.sh 223/223.
