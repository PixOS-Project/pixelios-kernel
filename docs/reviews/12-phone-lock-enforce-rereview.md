# Review 12: re-review of the review-11 fixes (f1f1d04, f546f83), phone lock enforcement (TP-020)

Reviewer: Claude Sonnet 5.5, adversarial, 2026-10-24. Tags: [obs] run on the host with a temp PHONE_LOCK_DIR (no phone, real lock untouched), [src] read from file:line.
Host tests [obs]: test_phone_lock.py 25 OK, test_phone_guard.py 3 OK, test_phone_sm.py 11 OK, probe-run --self-test PASS, hang-recover --self-test PASS.

## Verdict: SAFE WITH CONDITIONS

The two Criticals/Highs of review 11 that made the lock void (F1 any-caller-passes, F4 early drop) are fixed [obs]. Conditions: H1 and H2 below must be fixed
(or documented as accepted) before the lock is called "enforcement"; M1-M3 are small. Findings: 0 Critical, 2 High, 4 Medium, 6 Low.

## Requirement 1: F1..F9

| F | Status | Evidence |
|---|---|---|
| F1 check accepts any live lock | FIXED | [obs] `run --owner A` holding, `check` in a stranger shell -> `no token ... FAIL`, rc 1. phone-lock:193-204 has no owner branch. Env token from info.json copy passes (see L2). |
| F2 shared owner names | PARTLY: acquire/check fixed, `release --owner` NOT fixed | acquire with a taken label is busy (test), token is identity. But [obs] `PHONE_OWNER=drv release` and `release --owner A` (no token) released a live `run` lock: `released lock of A ... PASS`, then `status` = free while the run's CMD kept going. phone-lock:211 (`not token and owner and i.get("owner") == owner`). See H1. |
| F3 SIGKILL frees lock under live child | FIXED for `run`; NOT fixed for xrun/probe-run | `run`: [obs] kill -9 run, `acquire --owner Evil` -> `busy: held by R` (phone-lock:84, :241-242). xrun/probe-run take the lock via plock.sh:10 with `--pid $$` and no pgid (the CLI has no `--pgid`, phone-lock:268); [obs] bare `acquire --pid X` of a script whose child `sleep 25` survives kill -9 of X -> `takeover: stale lock` `PASS owner=Evil` at once. See H2. |
| F4 xrun drops lock early | FIXED | [src] xrun:39-42 `stop_capture` + `cleanup` (drop only in EXIT trap); the "cleanup; cap=" call became `stop_capture; cap=""` (xrun:89); EARLY/TIMEOUT recovery now runs locked. probe-run drops only in its EXIT trap (probe-run:80). |
| F5 takeover race | FIXED, residue (L3) | [src] phone-lock:121-145 flock mutex + re-read + verify after rename + put back. Unit test covers the "different lock" case (test_phone_lock.py:195). |
| F6 spin on ghost queue head | FIXED | [obs] ghost ticket (live pid) with free lock: `acquire --wait 2` -> `busy: waiting for the queue head ghost` after 2.04 s; wait 0 returns at once. phone-lock:177-180. |
| F7 bare acquire ttl-only | MITIGATED only | [src] phone-lock:280-281 prints a warning; behaviour unchanged (1800 s ttl frees it, :85). Acceptable: `check` cannot pass without a token anyway unless `--token` is given. |
| F8 unguarded tools | FIXED | [src] run-probe.sh:19, pstore-pull.py:164-168 call `phone-lock check` (token env inherited by subprocess). hang-recover now needs the lock (its self-test holds its own in a temp dir, hang-recover:75). AUTOMATION.md lists the three unguarded adb readers. |
| F9 phone-guard | MOSTLY FIXED | See requirement 4. |

## Findings

### H1 HIGH: `release` by owner label (no token) is an unauthenticated release of a live lock
- tools/phone-lock:211, :283-284, docs/AUTOMATION.md:416. [obs] with `run --owner A -- sleep 4` live, a shell without the token ran `phone-lock release --owner A` -> `released lock of A`, rc 0; same with `PHONE_OWNER=drv release` against `run --owner drv`. The label is printed by `status` and often shared (PHONE_OWNER). Afterwards the lock is free while the holder's CMD (xrun, fastboot) still runs, so a second agent can take it: the whole F2/F3 chain reopens through one command, and it is the documented release form (AUTOMATION.md:416).
- Fix: `mine = bool(token) and i.get("token") == token`; with no token and no `--force`, refuse. Keep `--owner` only as the label printed in the message. Update AUTOMATION.md:416 to `release --token T` (plock_drop already uses the token, plock.sh:17) and `force-release --yes` for a stuck lock. Add the test "release --owner A without token is refused while A holds".

### H2 HIGH: xrun / probe-run still lose the lock the moment their own pid dies, while their children keep running
- tools/plock.sh:10 (no pgid), tools/phone-lock:84 (`group_alive(i.get("pgid", 0))` is False for 0), :66. [obs] emulated with the same call plock_take makes: kill -9 of the script pid, child `sleep 25` alive, `acquire --owner Evil` -> takeover. For xrun this means kill -9 / OOM kill / a closed terminal (SIGHUP is not trapped past the EXIT trap only when bash itself dies) leaves fboot's fastboot, `usbnet-log` and the capture running while a second agent flashes. Only `phone-lock run -- xrun` is covered.
- Fix: record a group the script owns. In xrun and probe-run, at the top: `[ "$(ps -o pgid= -p $$ | tr -d ' ')" = "$$" ] || exec setsid -w "$0" "$@"` (own group; setsid -w keeps the exit code), and in plock_take pass `--pgid $$` (add `--pgid` to the acquire parser, phone-lock:268, and to `acquire(...)`). Trade-off: Ctrl-C then reaches the script only through the terminal's group, so keep the existing EXIT trap. Alternative with no code: document that xrun/probe-run must run as `phone-lock run --owner N -- tools/xrun ...` and make plock_take refuse (`PLOCK_MSG`) when PHONE_LOCK_TOKEN is unset. Add a test: SIGKILL a script holding via plock_take with a surviving child, assert `acquire` is busy.

### M1 MEDIUM: `run` writes info.json a second time after Popen and mishandles every failure of that write
- tools/phone-lock:241-242, :245-247. If the lock dir vanished between acquire and the write (force-release, disk error), `write` raises FileNotFoundError, which the `except OSError` reports as `cannot run ...` (rc 127) while the child is ALREADY running and is neither killed nor recorded. [obs] (copy of the tool with a 1.5 s pause before the write, force-release in the window): `cannot run sleep: [Errno 2] ... info.json.PID`, `command exit 127`, and `sleep 4` still alive afterwards with no lock.
- Same window, other order [obs]: force-release + `acquire --owner N --token ntok` by a taker, then run's write `os.replace`s INTO the taker's directory; info.json now shows owner R / run's token: the taker's lock is silently stolen (check by the taker fails, run's dead token holds the phone until its pid dies).
- Also a SIGKILL of `run` between :220 and :242 leaves pgid 0 in info.json (no group protection) [src].
- Fix: start the child stopped and write once: `Popen(..., start_new_session=True, preexec_fn=lambda: os.kill(os.getpid(), signal.SIGSTOP))`, then `write(..., child.pid)` guarded by a re-read (`read().get("token") == token`, else `os.killpg(child.pid, SIGKILL)` and exit 2), then `os.killpg(child.pid, SIGCONT)`. Never map an OSError of the write to "cannot run" once the child exists.

### M2 MEDIUM: `run` releases the lock while processes of CMD's group are still alive
- tools/phone-lock:243, :250-253. [obs] `run --owner L -- bash -c 'sleep 6 & exit 0'` -> `status` free, `kill -0` of the sleep succeeds. The group semantic exists only for the SIGKILL-of-run case; on a normal exit a background fastboot/adb/capture of CMD (`cmd &` in a manual slot) outlives the lock.
- Fix: after `child.wait()`, `if group_alive(child.pid): os.killpg(child.pid, SIGTERM)`, wait up to 5 s, then SIGKILL, and only then release; print `killed N leftover processes`. Test it.

### M3 MEDIUM: the token is readable by anyone who can read the lock dir and is passed on a command line
- tools/phone-lock:95 writes `token` to info.json (default umask 022, world readable [obs: `cat info.json` showed it]); tools/plock.sh:10 passes `--token "$tok"` in argv (visible in `ps`, /proc/PID/cmdline) while acquire runs. [obs] `PHONE_LOCK_TOKEN=$(python3 -c '...json.load(...)["token"]') phone-lock check` -> PASS for a stranger. All agents run as one Unix user, so a mode change does not stop a deliberate agent; this is a guard rail, but a one-line `cat info.json` (which agents do to "see who holds it") now yields the key.
- Fix: write info.json with `os.open(tmp, O_WRONLY|O_CREAT, 0o600)`; keep `status` from printing the token (it does not); pass the token to acquire via env (`PHONE_LOCK_TOKEN_NEW`), not argv; add to the docs "never copy the token out of info.json". Accepted risk otherwise.

### M4 MEDIUM: tests miss three mutants of the new code
[obs] mutation check on a copy (tools/test_phone_lock.py, one mutant at a time). KILLED: check without token compare, stale without group_alive, release-by-label-any, check without stale, `start_new_session=False`, run not exporting the token, run not recording pgid, ghost deadline removed, check accepting empty token. SURVIVED:
1. `take_over_stale` without the mutex re-read (`if j is None or j != i` -> `if j is None`): the test at test_phone_lock.py:195 passes because the post-rename verify (:138) also catches it. Test the re-read alone by making the verify unreachable (monkeypatch `os.rename`) or assert via a lock replaced between read and call with an identical-looking owner.
2. `take_over_stale` without the post-rename verify (`if not same` -> `if False`): same overlap; needs a test that replaces the dir between the re-read and the rename (monkeypatch `os.rename` to swap the lock first).
3. `run` finally removing the lock without the token check (phone-lock:252 `if i and token ==` -> `if i`): no test force-releases + retakes during a run. Add: `run -- sleep 2`, force-release, `acquire` by another with a token, wait for run to exit, assert the other lock is still there (this is also the M1 scenario).

### L1 LOW: signal forwarding after `start_new_session`
- phone-lock:231-235, :241. Ctrl-C / SIGHUP from the terminal now reaches only `run` (the child is in its own session); `fwd` sends the signal to the child pid alone, not its group, so grandchildren (fastboot under fboot.sh under xrun) do not get SIGINT as they did before. xrun's own EXIT trap still stops its capture, but a fastboot call in progress runs to its end first (bash defers the trap). Also a signal between `Popen` returning and `child =` assignment raises SystemExit with a started, unrecorded child (microseconds, F11 of review 11 remains). Fix: `os.killpg(child.pid, sig)` in `fwd` for SIGINT/SIGTERM/SIGHUP, and block the three signals with `signal.pthread_sigmask` around Popen.
- No tty problem found: stdin/stdout/stderr are inherited fds; only job-control-dependent tools (none in tools/) would notice the lost controlling terminal [src; guess for third-party tools].

### L2 LOW: `acquire --token T` accepts any caller-supplied token
- phone-lock:171-172: `acquire --token T` returns 0 "already held with this token" for the live lock whose token is T, and a token you pick yourself is simply stored (:151). A caller cannot guess a uuid4 [src], but acquire with a chosen token on a FREE lock (e.g. `--token hr-token` in hang-recover:75) gives that lock a guessable identity; fine for tests, a hole if an agent does it by habit. Also the "already held" branch precedes `stale()`, so a stale lock with that token returns 0. Fix: in `main`, accept `--token` only when `PHONE_LOCK_ALLOW_TOKEN=1` or document it as internal; check `stale` before the token branch.

### L3 LOW: takeover residues
- phone-lock:130-143: while the stale dir is renamed away the lock path does not exist, so a concurrent live holder's `check` says "no lock held" for that instant (spurious REFUSED, fail-closed); if the put-back `os.rename(old, LOCK)` fails because another process already created LOCK, the displaced lock stays in `LOCK.stale.PID` forever and its holder loses the lock (fail-closed, tiny window since acquire's mkdir does not take the mutex). Fix: take the mutex in the mkdir path too (`acquire` around :164-169), and remove leftover `*.stale.*` dirs whose pid is dead.

### L4 LOW: `group_alive` and the caller's own group
- phone-lock:66-67: `pgid == os.getpgrp()` returns False. A process inside a dead `run`'s leftover group that calls `acquire` therefore sees the group empty and takes over (it is the leftover child, which should not be allowed to), and `check` from inside the group of a SIGKILLed run reports "stale" (fail-closed, fine). The exclusion exists for callers running as the group's leader; the right test is "group has a member other than me", which killpg cannot tell. Fix: record also the leader's start time and compare `/proc/<pgid>/stat`, or simply drop the `== getpgrp()` exclusion (a lock's pgid is never the pgid of a legitimate taker). PermissionError is treated as "alive" in both helpers, which is the safe direction [src :60-61, :72-73].

### L5 LOW: phone-guard residual bypasses and one wrapper hole
[obs] with tools/phone-guard, 70 forms tried. All tools/*.sh invocations (fboot.sh, fflash-userdata.sh, phone goto, bash tools/xrun, bash -c 'tools/fboot.sh x') were allowed: no wrongly denied tools. Correctly denied: for/while/if loops, `timeout 60 adb reboot bootloader`, `adb -s X reboot`, `fastboot -s X flash`, `nohup ... &`, `sudo`/`env`/`time`/`nice`/`stdbuf`/`watch`/`xargs` prefixes, `bash -lc`, `sh -c "adb reboot"`, `cd x && fastboot erase`, `(fastboot reboot)`, quoted `'fastboot' flash`, `adb shell getprop x && adb shell reboot`. Correctly allowed: `git commit -m "fix adb reboot"`, `grep adb file`, `echo fastboot flash`, heredoc text, `git log --grep='adb reboot'`, `command -v fastboot`, `which fastboot`, `adb pull`, `adb logcat`, `adb shell getprop ...`, `fastboot getvar all`, `timeout 5 fastboot getvar product`.
Missed (allowed, should be denied):
1. `ssh h adb reboot` : phone-guard offset bug, for `ssh` the inner string starts at `rest[k+1]` but the code uses `rest[k+(prog!="eval")]` with k>0 -> `reboot` only (phone-guard:~63-66). Fix: for ssh join `rest[1:]`.
2. `script -qc 'fastboot flash a' /dev/null` (not recursed).
3. `tools/phone-lock run --owner x -- fastboot flash a b` : the lock is held but the bluejay check is skipped; `phone-lock` is not a recursing wrapper. Fix: treat `phone-lock run ... --` like `timeout` (recurse into the words after `--`).
4. Variable indirection (`ADB=adb; $ADB reboot`) : documented limit.
Wrongly denied (inconvenient, by design): `adb install`, `adb push`, `adb shell ls` (only `adb shell getprop` passes). Mention in the docs.

### L6 LOW: docs
- docs/AUTOMATION.md:431 still says "Stale (holder pid dead, or ttl over; ...) ... Same owner re-acquiring refreshes." That contradicts the new rules (pid + group, ttl only for pid-less locks, same label is busy). Fix the paragraph. AUTOMATION.md:416 still documents `release --owner NAME` (see H1).
- hang-recover now needs the lock for `phone goto`; a stuck-phone recovery by an agent that does not hold one is REFUSED. Say in hang-recover's usage line to run it as `phone-lock run --owner N -- tools/hang-recover` (stuck phone and busy lock: the user force-releases).

## Requirement 2 / 3 summary (token design checks)
- Token leaking: `run` sets the token only in its own environment and CMD inherits it (phone-lock:227-229); the agent's shell that launched `run` never gets it [obs: check in the parent shell fails]. Inside CMD everything inherits it, including a daemonised `adb start-server` (guess: not verified here, no adb run); harmless unless something later reads /proc/PID/environ (same-user).
- plock.sh: the token is exported only after `check --token` succeeded (plock.sh:11-12); a failed acquire leaves `PLOCK_TOOK=0` and nothing exported [src]. One gap: if acquire succeeded but the verifying `check` failed (stale in the same instant), the function returns 1 with PLOCK_TOOK=0, so the lock is not dropped (released by pid death later). Negligible.
- xrun exit paths: normal/`die`/SIGTERM/SIGINT run the EXIT trap (drop by token, plock.sh:17). `exec` is only on the dry-run path before the lock (xrun:31). kill -9 / OOM leaves a pid-dead lock and orphaned children, and the lock is immediately takeable: H2.
- Token check on `stale`: `check` rejects a stale lock even with the right token (phone-lock:202) [src].

## What I verified correct
- Ghost queue head: bounded (F6). Waiters in arrival order and no queue jumping: tests pass, mutants not in this set were killed in review 11.
- `check` with empty token, wrong token, no lock, stale: all FAIL [src + test_check].
- run exit codes, signal release, busy path: tests pass; token exported to CMD [obs via test_run_exports_token].

## Priority order of fixes
H1 (one line), H2, M1, M2, M4 tests, M3, L1, L5, L6, L2-L4.

## Driver follow-up (2026-10-24)
Applied after this review: H1 `release` needs the token for any lock that has a pid (label release only for pid-less bare-acquire locks); H2 `acquire --pgid`, plock.sh records the process group of xrun/probe-run so a SIGKILLed script keeps the lock while its children run; M1 `run` rewrites info.json only if the lock still carries its token; M2 `run` keeps the lock up to 30 s while its process group is not empty; M3 info.json is mode 0600 (the token still appears on the `acquire` command line of xrun/probe-run: accepted, local single-user host). Tests added: release needs token, pgid keeps lock, info mode. Not applied: surviving mutants (mutex re-read, post-rename verify, run's final token check), phone-guard misses (`ssh h adb ...`, `script -c`, `phone-lock run -- fastboot flash`): guard rail, not a sandbox.
