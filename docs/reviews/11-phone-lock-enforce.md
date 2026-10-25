# Review 11: commit 97f666a, phone lock enforcement (TP-020)

Reviewer: Claude Sonnet 5.5, adversarial, 2026-10-24. Scope: tools/phone-lock, plock.sh, xrun, probe-run, fboot.sh, fflash-userdata.sh, phone (cmd_goto), phone-guard.
Tags: [obs] = observed by running it on the host (temp PHONE_LOCK_DIR, no phone touched), [src] = read from source file:line.

Host tests [obs]: test_phone_lock.py 19 OK, test_phone_guard.py 3 OK, test_phone_sm.py 11 OK, probe-run --self-test PASS (7 ok).
Mutation check [obs, copy of phone-lock, test_phone_lock.py): killed: ttl-beats-pid, my-turn-always, owner-mismatch-pass, no-dead-prune, stale-pid-always-live.
SURVIVED (tests do not cover): queue ordering (sorted() replaced by reversed listdir), `check --strict` ignored, empty-info stale threshold (10 s -> 10000 s). Also killed: run-no-release; run-no-env (run not exporting PHONE_LOCK_OWNER) SURVIVED too: untested.

## Verdict: UNSAFE (as an enforcement claim); SAFE WITH CONDITIONS only after F1 and F2 are fixed

The main guard does not guard: any caller without PHONE_LOCK_OWNER/PHONE_OWNER passes while another agent holds the lock (F1).

## Findings

### F1 CRITICAL: `phone-lock check` without an owner accepts ANY live lock (transition rule)
- tools/phone-lock:164-165. fboot.sh, fflash-userdata.sh and `phone goto` call `check` with no `--owner` and no `--strict`; the owner defaults to $PHONE_LOCK_OWNER or $PHONE_OWNER (:234), else "".
- Exactly when a second agent gets through: lock held by anybody live (including `phone-lock run`, xrun, probe-run), and the caller has neither env var set. [obs] `acquire --owner A --pid $$` then `check` as B with no env -> `RESULT: PASS`, exit 0, "accepting the live lock of A".
- Scenario: agent 1 is mid `xrun` (lock held by `xrun-1234`). Agent 2 runs `tools/fflash-userdata.sh IMG --yes-erase-userdata` (or `tools/fboot.sh`, `tools/phone goto fastboot`) from its own shell: check passes, fastboot flashes userdata under agent 1's run. Fully reproduces the case the brief fears. Worse, the check passes only when SOMEONE holds the lock, so the only time it refuses is when nobody is using the phone (inverted).
- Also: the lock dir exists but info.json is unreadable (`read()` returns {}, :45, up to 10 s, :61-65) -> owner "?" accepted.
- Fix: in `check`, drop the transition branch: `if not owner: return 1, "no owner: ..."` (always strict). Make `--strict` the default, remove the flag. Callers (fboot/fflash/phone) then need PHONE_LOCK_OWNER, which `phone-lock run` and plock_take already export. Add the test "B without env cannot pass when A holds".

### F2 HIGH: owner is a self-chosen string, shared names defeat the lock
- tools/phone-lock:128-130 (acquire "refreshed own lock"), :166 (check), :177 (release), plock.sh:7.
- If two agents/threads have the same PHONE_OWNER (env inherited from a shared parent, a documented name like "driver", or both use the plock_take default via PHONE_OWNER), the second `acquire`/plock_take refreshes the first's lock and rewrites pid (:129 uses the caller's pid) and both run. [obs] `PHONE_OWNER=A acquire --owner A --pid 1` while A held -> "refreshed own lock". When the second exits, plock_drop releases the lock the first still uses (`release --owner` matches by name, :177), and the first loses protection mid-run.
- Fix: identity must be unique per process tree: in plock_take and `run` use `owner=${tool}-$$` always (ignore PHONE_OWNER for taking); a same-owner refresh must require the caller to be the recorded pid or its child (`i["pid"] == pid` or pid dead), else busy. State in docs that PHONE_OWNER is only an identity for `check`, never for `acquire`.

### F3 HIGH: SIGKILL (or crash) of `phone-lock run` frees the lock while the child keeps touching the phone
- tools/phone-lock:184, :203-204. The lock pid is the `run` process; the child is a plain Popen child (no PDEATHSIG, no new process group).
- [obs] `phone-lock run --owner R -- sleep 30`, `kill -9` the run: the sleep survived, and `acquire --owner Evil` took over ("takeover: stale lock of R"). With xrun as the child, a second agent starts flashing while xrun still runs fboot/capture.
- Same for a bare `xrun` that is SIGKILLed: its children (fboot's fastboot, capture) outlive the pid that backs the lock (plock.sh:9 `--pid $$`).
- Fix: in `run`, `Popen(..., preexec_fn=...)` with `prctl(PR_SET_PDEATHSIG, SIGKILL)` via ctypes, or `start_new_session=True` and make staleness require that no live process of that session/process group remains (`os.killpg(pgid, 0)`); record `pgid` in info.json and have `alive` test the group. At minimum document that SIGKILL leaves orphans.

### F4 HIGH: xrun releases the lock before it is finished with the phone
- tools/xrun:88 `cleanup; cap=""` runs plock_drop (:39); then lines 96-103 still run `tools/to-fastboot.sh` (adb reboot bootloader, via phone goto) and `probe()` (adb get-state, fastboot getvar). EARLY recovery and TIMEOUT probe happen unlocked; another agent that just took the lock gets its phone rebooted to bootloader by the first xrun's EARLY branch (phone goto's check passes via F1, so nothing stops it).
- Fix: do not call `cleanup` at :88; split cleanup into `stop_capture` (touch stop, wait usbpid, kill cap) and call plock_drop only in the EXIT trap (or immediately before `exit $rc` at :106). Keep the report's `phone=` marks inside the lock.

### F5 MEDIUM: stale-takeover race can destroy a freshly taken live lock
- tools/phone-lock:131-140. `stale(i)` is decided on an old read; between that read and `os.rename(LOCK, old)` another process can finish a takeover and create a new valid lock; the late renamer then moves the NEW live lock away and rmtrees it.
- [obs, deterministic replay of the interleaving] B reads stale view, A removes it, C `acquire` gets (0, None), B `os.rename(LOCK, ...)` succeeds -> `read()` is None while C believes it holds the lock; D can mkdir -> two holders.
- Fix: rename only the lock you judged stale: re-read inside the takeover and compare `start`+`pid`+`owner` with `i` immediately before the rename, or first `os.rename(LOCK, old)` and then verify `old/info.json` equals `i`; if it does not, `os.rename(old, LOCK)` back (or mkdir fails -> rmtree old only if it differs). Or use fcntl.flock on a sibling file for takeover.

### F6 MEDIUM: `wait=0` (and expired wait) acquire spins forever when the lock is free but the queue head never takes it
- tools/phone-lock:116-127. `my_turn` False and `read()` None -> `continue` (:127) with no sleep and no deadline check. A ticket whose pid is alive but not a waiter (pid reuse, SIGSTOPped waiter, a hung `acquire --wait`) blocks the queue and all other acquirers burn 100% CPU past their `--wait`.
- [obs] ticket `...-<live pid>-ghost` in `.queue`, lock free: `acquire --owner X` and `acquire --owner X --wait 2` both ran until killed by `timeout 5` (exit 124/143), not RESULT busy after 2 s. plock_take (plock.sh:9) has no timeout, so xrun/probe-run hang at the lock step.
- Fix: after `if i is None:` do the deadline check and a sleep before `continue` (`if time.time() >= end: return 1, "busy: queue head ..."`; `time.sleep(0.05)`). Also expire tickets: drop a ticket older than `MAXWAIT+60` s and record the ticket creator's start time or pid+`/proc/pid/stat` start time to defeat pid reuse.

### F7 MEDIUM: bare `acquire` (pid 0) is ttl-only: 1800 s default; a flash longer than that is taken over
- tools/phone-lock:66-68 and :240, :19. [obs] `acquire --owner Z --ttl 1`, after 1.5 s `acquire --owner Y` -> takeover. The documented manual flow (acquire, then fflash/fboot by hand) loses the lock after 30 min regardless of liveness; the caller is told nothing.
- Fix: make `acquire` without `--pid` an error for agents (require `--pid` or use `run`), or default `--pid` to the parent pid (`os.getppid()`) so the lock lives as long as the calling shell.

### F8 MEDIUM: unprotected phone-touching tools
Not covered by the lock check [src grep of tools/]:
- tools/run-probe.sh:20-32 (`adb reboot bootloader`, `fastboot boot`): no check at all.
- tools/pstore-pull.py:164-177 (`run`: `fastboot boot`, `adb reboot bootloader`): no check.
- (tools/sweet-log-pull.sh, since removed), backup-partitions.sh, check-udump.sh: adb shell/su reads of the phone (non-mutating, but they run `su dd` on userdata/blocks; check-udump reads userdata) with no lock.
- tools/hang-recover:31 calls `phone goto` (checked, but via F1 passes) and is the one tool intended for stuck phones.
- Covered: fboot.sh (check before `fastboot getvar`, :13-14), fflash-userdata.sh (check before `getvar`, after the local sha/state tests, :16-17), phone goto (check after observe_state, which only does adb devices/getvar reads; before the first mutating step), xrun/probe-run via fboot and goto.
- Fix: add `tools/phone-lock check --strict` (or plock_take) at the start of the mutating path in run-probe.sh and pstore-pull.py `run`; list them in TP-020 as "not guarded".

### F9 MEDIUM: phone-guard bypasses (guard rail, not a sandbox)
Ranked by how likely an agent types them by accident. All [obs] with `tools/phone-guard`, "allow" means the hook let it through.
1. Compound commands: `if ...; then fastboot flash a; fi`, `for c in reboot; do fastboot $c; done`, `{ fastboot flash a; }`, `! fastboot ...` (the first word of the segment is `then`/`do`/`{`/`!`; tools/phone-guard:50-56 only skips WRAP words). Likely in agent loops.
2. `bash -c "fastboot flash ..."`, `sh -c "adb reboot bootloader"`, `eval "..."`, `script -c`, `ssh host ...` (quoted text is one word, :23-40; `shlex` word never re-parsed).
3. Unguarded scripts that wrap the commands: `bash tools/run-probe.sh`, `python3 tools/pstore-pull.py run IMG` (see F8).
4. `/usr/bin/env fastboot flash a`, `sudo -u me fastboot ...`, `env -u X fastboot ...`, `setsid`, `watch`, `xargs` (prefix list WRAP :13 matches only exact word; `env` with a path or an option that takes a value is not skipped; `sudo -u USER` is treated as program `me`).
5. Variable indirection `F=fastboot; $F flash`, `ADB=adb; $ADB reboot`, `$(echo fastboot) ...`, `python3 -c subprocess...`: unlikely by accident.
- False positives that block legitimate tool use [obs]: `adb shell getprop ro.x`, `adb logcat`, `adb pull`, `adb wait-for-device`, `command -v fastboot` (wrap word `command` then `-v` skipped, `fastboot` seen as the program). `adb shell getprop ro.product.device` is the sanctioned device check in AGENTS.md rule 2; the hook denies it and tells the agent to use tools instead. (`fastboot getvar product` is allowed.)
- Not wired for worktrees: the hook command is `$CLAUDE_PROJECT_DIR/pixos-project/pixelios-kernel/tools/phone-guard` in the super repo's .claude/settings.json; a thread whose project dir is a kernel worktree has no such path, the hook then fails (non-zero, non-blocking by Claude Code semantics) or is absent (guess, not verified).
- Fix: (a) in `offending`, also treat `then do else elif while until { ! if` as skippable prefixes, resolve `env`/`sudo` by basename and skip `-u X`/`-i`, (b) recurse into the string argument of `bash|sh|zsh -c`, `eval`, `ssh`, `script -c`, (c) allow `adb shell getprop`, `adb logcat`, `adb pull`, `adb wait-for-device`, `command -v`, (d) add `run-probe.sh`/`pstore-pull.py` as denied wrappers or guard them (F8). Say in the file docstring: "guard rail for accidents, not a sandbox".

### F10 LOW: queue and ticket details
- tools/phone-lock:112: owner with `/` or length >200 makes the ticket path invalid -> `error: [Errno 2]` exit 2 (harmless, fails closed) [obs]. Sanitize owner for the ticket name (`re.sub`). Owners with `-` are parsed correctly (`split("-", 2)[2]`, pid at [1]) [obs/src :91,95]. A dead waiter between ticket creation and unlink is pruned by the next `queue()` (:97-100), so it only delays for one poll. Ordering uses wall-clock `time_ns` (:112): a clock step can reorder waiters; use `time.monotonic_ns()` is NOT cross-process safe on all hosts, so prefer a counter file or ctime of the ticket. Ordering is untested (mutant survived).
- :154 `check` with a ttl-only (pid 0) lock accepts whoever passes the owner string (:166); no process binding.
- plock.sh:6 first `check --strict` with env owner from an earlier `run` returns 0 if names equal even though another process of the same name holds it (F2).

### F11 LOW: `run` signal handling
- tools/phone-lock:193-197, 203. A signal arriving between lock acquisition (:184) and `child = Popen` (:203), or during Popen before assignment, raises SystemExit with the lock released by the `finally` while a spawned child may exist unrecorded (window of microseconds). Block signals with `signal.pthread_sigmask` around Popen, or set `child` before the handler can fire.
- `release()` (:179) and run's finally (:213-214) read then rmtree; a force-release + takeover between them deletes the new holder's lock (tiny window). Same fix as F5 (verify identity of the directory being removed).

### F12 LOW: test gaps
Mutants that survive: queue ordering, `--strict`, empty-info staleness window. Add: three waiters in arrival order; `check --strict` without owner fails; `check` as another owner fails while A holds (F1 fix test); `run` child sees PHONE_LOCK_OWNER; SIGKILL of `run` documented/fixed (F3).

## What I verified correct [obs/src]
- plock_take (plock.sh:3-12): a failed acquire is re-verified by `check --strict --owner` (:10) and returns 1, so xrun `die`s (xrun:62) and probe-run `report ERROR` (probe-run:38) before touching the phone. Lock is taken after the build in both, `to-fastboot` moved after (xrun:63-67), nothing in probe-run touches the phone before plock_take (phone-state calls start at :39).
- Release: xrun `trap cleanup EXIT` (xrun:41), `die` -> exit -> trap; bash runs the EXIT trap on SIGTERM [obs: "trap-ran", rc=143]; probe-run trap (:31) covers `report` exits. Under `phone-lock run -- xrun` PHONE_LOCK_OWNER is set and the first `check --strict` passes (plock.sh:6), PLOCK_TOOK stays 0, so no double release and the parent releases once. SIGKILL of xrun leaves a pid-dead lock that is correctly taken over (pid beats ttl otherwise, tested).
- Stale takeover never takes a lock with a live pid (:66-67; mutant killed). Dead-waiter pruning works (mutant killed).

## Priority order of fixes
F1, F2 (enforcement is void without them), F4, F3, F6, F5, F8, F9, F7, then Low.
