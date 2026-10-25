# Review 16: shared tooling, live hooks and the shim/out lock (branch common-tools, eb44e02..bb65e0b)

Reviewer: Claude (Opus 5.5), adversarial, read-only on code, 2026-10-24. Scope: `git diff eb44e02 HEAD -- tools docs/AUTOMATION.md`, plus the
live `pixelios-kernel/.git/hooks/pre-commit` and the super repo `.claude/settings.json`. No phone, no gate, no xrun. All experiments ran on
temp repos or copies under `/tmp` (scripts kept in `/tmp/rv16-scripts/`). Tags: *observed* = I ran it on the host; *source* = read at file:line; *guess* = not verified.

## Verdict: SAFE WITH CONDITIONS

Nothing here writes the phone in a new way. `fflash-userdata.sh` still fails, and does not record the flash, when `fastboot flash` fails.
Four shared-tool problems must be fixed before other agents rely on these tools: H1 (one missing file blocks every agent session), H2
(patch-guard denies normal reserved-patch work from the super cwd), H3 (`tools-sync pull` throws away committed branch work) and M1 (pre-commit
fails closed for every commit). Fix H1 first: today it is one `git worktree remove` or one `git checkout main` away from stopping all agents.

## Tests run (observed)
`test_tools_sync` 5 OK, `test_patch_numbers` 4 OK, `test_patch_guard` 5 OK, `test_phone_lock` 28 OK, `test_xrun_report` 8 OK, `test_usb_log` 11 OK,
`test_usbnet` 11 OK, `test_phone_guard` 3 OK, `test_phone_sm` 11 OK (all with a temp `PHONE_LOCK_DIR`), `tools/probe-run --self-test` PASS.
Mutants (each on a copy in /tmp, the copied test run against it): 11 of 21 caught. **Survived:** tools-sync checkout error ignored; patch-numbers
`next` without the `0*40` old value (non-atomic); `claim` any->all; `claim` without `not name`; precommit ignoring reservations; `status` ignoring
reservation conflicts; main error rc 2->0; patch-guard ignoring "committed elsewhere under the same name"; patch-guard Bash treating an existing
patch as new; patch-guard loading the wrong repo (H2 has no test); patch-guard exceptions not caught. No test covers the shim/out lock or the
private image copy in xrun/probe-run. The probe-run self-test's stub build makes no image, so the copy branch never runs.

## Findings

### H1 (High). A missing hook script blocks every Bash/Write/Edit call in every session
- `~/.t3/projects/pixelios/.claude/settings.json`: patch-guard runs `python3 ~/.t3/worktrees/common-tools/kernel/tools/patch-guard`
  (matcher `Write|Edit|MultiEdit|Bash`). phone-guard runs `python3 "$CLAUDE_PROJECT_DIR/pixos-project/pixelios-kernel/tools/phone-guard"`.
- *Observed:* `python3 /nonexistent/tools/patch-guard` exits **2**. *Read from the Claude Code hooks docs, not tried live:* exit 2 from a PreToolUse hook
  blocks the tool call. Other non-zero codes are non-blocking.
- What breaks it: `git worktree remove` of common-tools (or moving it) blocks every Bash/Write/Edit, including the Edit needed to fix settings.json.
  The user then has to fix it by hand. phone-guard has the same problem: it reads the file from whatever branch the main clone has checked out.
  *Observed:* `tools/phone-guard` is missing on `main`, `origin/main` and `wip/gpu-accel` (`git ls-tree`), so `git checkout main` in the main
  clone blocks every Bash call. The super worktree `~/.t3/worktrees/pixelios-gpu/super` has `.claude/` but no `settings.json` (*observed*).
  Sessions rooted there run with no guard. Once that worktree merges super main it gets a `$CLAUDE_PROJECT_DIR/pixos-project/...` path that does
  not exist there (no `pixos-project/`), and every Bash call is blocked.
- Fix: never let a hook fail with exit 2 by accident. In settings.json, wrap each command:
  `sh -c 'P=~/.t3/worktrees/common-tools/kernel/tools/patch-guard; [ -f "$P" ] || { echo "patch-guard missing: $P" >&2; exit 1; }; python3 "$P"; r=$?; [ $r = 2 ] && exit 1; exit $r'`
  Do the same for phone-guard. Exit 1 is shown to the user but does not block. Also add a test in `test_patch_guard.py` that runs the exact settings.json
  command string with the script path removed and asserts rc != 2. Longer term: copy both guards into the super repo (`.claude/hooks/`) so a branch
  switch in a clone cannot remove them.

### H2 (High). patch-guard resolves the repo and the "exists" check from the session cwd, not from the path in the command
- `tools/patch-guard:57-63`: `os.path.exists(os.path.join(cwd, m.group(0)))` and `load_pn(... else cwd)`. Agent cwd resets to the super repo between
  Bash calls, so the normal command is `cd pixos-project/pixelios-kernel && ...`.
- *Observed* (live guard, `/tmp/rv16-scripts/guard_cases.py`):
  - **denied:** cwd=super, `cd pixos-project/pixelios-kernel && cp /tmp/x xnu/patches/0112-sweet-iosurface-refuse-wrap.patch`. 0112 IS reserved for
    that slug in the kernel repo, but the guard surveyed the super repo, which has none of the kernel's reservations.
  - **denied:** a doc heredoc from the super cwd whose body mentions `tee xnu/patches/0120-foo.patch`.
  - **denied:** my own reviewer command `echo a > xnu/patches/<0101-vsync> ` inside a `/tmp` test repo (AP-25).
  - **denied:** `grep -n install xnu/patches/<new>` (the word `install` matches WRITES).
  - **denied:** `git restore --source=HEAD~5 <patch deleted at every tip>`.
  - **allowed only by luck:** "regenerate an existing patch by absolute path" and "git checkout -- existing patch" from the super cwd. The super repo's
    old `t3/*` branches happen to hold `0101-sweet-fb-vsync-period.patch`. A post-split patch (≥0106 on kernel branches only) is denied.
- Fix (patch-guard:53-58):
  1. Take the whole path token: `TOK = re.compile(r"(\S*xnu/patches/(\d{4})-([^/\s'\"]+)\.patch)")`.
  2. Get the effective directory: the last `cd DIR` before the match in the same `&&`/`;` chain (`re.findall(r"(?:^|&&|;)\s*cd\s+(\S+)", cmd[:m.start()])`),
     joined to `cwd`. Join `token` to it unless the token is absolute, then `os.path.normpath`.
  3. Run the exists check and `load_pn(dirname(path))` on that path.
  4. If the path's repo has no `tools/patch-numbers` at its toplevel (a temp repo, the super repo), allow.
  Add test cases: cwd=super with `cd`, an absolute path, and a `/tmp` repo.

### H3 (High). `tools-sync pull` overwrites committed branch-local changes and reports PASS
- `tools/tools-sync:108-117`: a `differs` file is blocked only when `dirty()`. A file the branch changed in a COMMIT is clean, so `git checkout REF -- f`
  replaces it and prints "PASS pulled ...; review and commit them".
- *Observed:* `git diff common-tools wip/w3a-wlan-link -- tools/usb-log.py` shows the `times_file` / `usb.times` sidecar (review 14 M1(d)) only on
  wip/w3a-wlan-link, and probe-run differs too. A `pull` in the Wi-Fi worktree followed by "commit them" silently drops that feature.
- Fix: three-way state. `base = git merge-base HEAD REF`. If `git rev-parse HEAD:f` != `git rev-parse base:f`, mark the file `local-commits`: blocked unless
  `--force`, with `git log --oneline base..HEAD -- f` printed. Pull only files changed on the ref side alone. Test: commit a change to `tools/a` on main,
  then `pull`, and assert rc 1 and the content kept.

### M1 (Medium). The shared pre-commit hook fails CLOSED and blocks commits by every agent
- `tools/patch-numbers:290-296` returns 2 on any exception. The hook (`.git/hooks/pre-commit`, `exec python3 "$T" precommit`) passes that to git.
- *Observed* in a temp repo with the same hook:
  - one stray ref `refs/patch-numbers/junk` gives `int()` ValueError, and **every commit that adds a patch is refused** (rc 1, "RESULT: ERROR invalid literal").
  - `python3` not on PATH: "exec: python3: not found", **every commit refused**, including commits with no patch.
- *Guess (Python behaviour):* a SyntaxError while someone edits `patch-numbers` in the common-tools worktree exits 1 before any check. That blocks
  all commits in all worktrees.
- Fix:
  - Hook: `command -v python3 >/dev/null || exit 0; python3 "$T" precommit; rc=$?; [ $rc = 3 ] && exit 1; [ $rc = 0 ] || echo "patch-numbers: internal error rc=$rc, patch numbers NOT checked" >&2; exit 0`.
  - `cmd_precommit`: return 3 on a refusal, and wrap the body in try/except returning 0 with a message.
  - `reservations()` (:121-125): skip ref names that are not 4 digits.
  - Re-run `install-hook`.

### M2 (Medium). A renumbering rename escapes the pre-commit check
- `tools/patch-numbers:224`: `--diff-filter=A` with rename detection on (git default), so `git mv 0103-wlan.patch 0102-wlan.patch` is `R` and is not checked.
- *Observed:* the wifi worktree committed `0102-wlan.patch` while gpu holds `0102-gpu-probe.patch`, rc 0.
- Fix: `git diff --cached --name-only --no-renames --diff-filter=A`. Add a test: `git mv` to a colliding number is refused.

### M3 (Medium). Pre-commit treats the committing branch's own old name as "elsewhere"
- `tools/patch-numbers:232`: `others` includes the name in this branch's HEAD.
- *Observed:* on gpu, renaming `0102-gpu-probe.patch` to `0102-gpu-probe2.patch` with new content (so D+A, no rename detected) was refused:
  "0102-gpu-probe2.patch is staged here, but 0102-gpu-probe.patch on gpu". Fixing a slug blocks your own commit.
- Fix: `deleted = set(basename(x) for x in git("diff","--cached","--name-only","--no-renames","--diff-filter=D").stdout.split())`, then
  `others = {k: v for k, v in used.get(n, {}).items() if k != f and not (k in deleted and set(v) <= {current_branch, "HEAD"})}`.
  Simpler: drop names that are in `deleted` and whose only refs are the current branch (`git symbolic-ref --short -q HEAD`).

### M4 (Medium). The gate's fd 9 (the shim/out lock) is inherited by every child and outlives the gate
- `tools/check-all.sh:12` (`exec 9>...; flock 9`).
- *Observed* (`/tmp/rv16-scripts/fd9_exp.sh`, a gate-like subshell on a temp lock file):
  - after `tools/probe-run --self-test` returned, 7 orphaned `sleep 100` (the self-test's usblog stub, `tools/probe-run:142`) still held the lock.
  - the next `flock` got it only after **88 s**.
- Any longer-lived child keeps the lock until it dies, and xrun/probe-run then wait up to 3600 s and fail. *Guess:* an adb server started from a gate
  step would be such a child.
- Fix:
  - In `step()` (`tools/check-all.sh:24+`), run the step command with `9>&-` (the gate shell alone keeps the lock).
  - In the xrun/probe-run subshells, run the build command with `9>&-` as well.
  - In the self-test stub, use `exec sleep 100`.

### M5 (Medium). xrun/probe-run hold the phone while they wait up to an hour for shim/out
- `tools/xrun:59-62`, `tools/probe-run:86-89`. Under `phone-lock run --wait -- tools/xrun ...` (the form AGENTS.md prescribes, and `plock_take` reuses
  that outer lock, `tools/plock.sh:7`), the phone lock is already held when `flock -w 3600` starts. A gate in that worktree then blocks every agent's
  hardware run for the gate's length (source).
- Fix: `if tools/phone-lock check >/dev/null 2>&1; then w=60; else w=3600; fi; flock -x -w $w 9 || ...`, with the message "shim/out busy (a gate
  in this worktree); build first, then phone-lock run". Also put this in docs/AUTOMATION.md.

### M6 (Medium). tools-sync's only "in use" test is the phone lock
- `tools/tools-sync:56-60, 93`. A running gate, or an xrun/probe-run in its build or shim/out wait, holds no phone lock. `pull` then replaces
  `fboot.sh` / `xrun_report.py` / `check-all.sh` while those are later run as fresh processes, so one run mixes versions.
- `phone-lock status` rc 2 (error) counts as "free" (`== 1`).
- Files are checked out one by one, so an error mid-loop leaves a mixed set (that mutant survived).
- Fix:
  - Also refuse when `subprocess.run(["flock", "-n", os.path.join(ROOT, "shim/out/.build.lock"), "true"]).returncode != 0`.
  - Use `returncode != 0` for the phone lock.
  - Run all checks first, then one `git checkout REF -- f1 f2 ...`.

### L1 (Low). patch-guard misses some ways to create a patch
- *Observed* allowed: `cd xnu/patches && git diff > 0150-foo.patch`; `f=xnu/patches/...; > $f`; `python3 -c open(...)`; `git format-patch -o xnu/patches`;
  `sed 'w xnu/patches/...'`; Write to `xnu/patches/../patches/0150-foo.patch`; a slug containing a space.
- Pre-commit backs these up, except for renames (M2).
- Fix: add `format-patch` and `sed\b[^;|&]*\bw\s` to WRITES, `os.path.normpath(fp)` before matching, and the slug class `[^/]+?`.

### L2 (Low). Slugs are compared as substrings
- `tools/patch-guard:33`, `patch-numbers:145, 198, 209, 235`: `name in fname`.
- *Observed:* `claim 0102 --name .patch` passed the "committed elsewhere" check (it stopped only at the existing reservation). A reservation "wrap"
  lets `0112-x-...-wrap-evil.patch` through.
- Fix: compare `PAT.match(fname).group(2) == name` everywhere.

### L3 (Low). Pre-commit reservations race with `next`, and leak
- `tools/patch-numbers:247` ignores the `update-ref` result. If `next` reserves the same N between `reservations()` and `update-ref`, the commit goes
  ahead under a number reserved for another slug.
- A commit aborted after pre-commit (empty message, commit-msg hook) leaves a reservation behind.
- Fix: check rc. On failure, re-read the reservation and refuse (return 3) when its slug differs.

### L4 (Low). A fractional `--lock-wait` kills xrun after the phone is taken
- `--lock-wait 1.5` is accepted by `phone-lock` (`type=float`, `tools/phone-lock:273`).
- *Observed* (bash, `set -e`): `$(( lockwait / 60 + 30 ))` (`tools/xrun:95`, `tools/probe-run:100`) is then a syntax error. xrun exits rc 1 with no
  RESULT line, after the phone lock and the camera were taken.
- Fix: at parse time, `[[ $lockwait =~ ^[0-9]+$ ]] || { echo "RESULT: ERROR --lock-wait needs whole seconds"; exit 2; }` in both tools.

### L5 (Low). `image.meta.json` may describe a different image
- `tools/xrun:65` still copies `shim/out/shim-boot-avb.img.meta.json` to `image.meta.json` AFTER the lock is released, so it can describe a gate's image.
- Fix: `cp "$D/shim-boot-avb.img.meta.json" "$D/image.meta.json"`.

### L6 (Low). xrun_report calls 18d1:d001 "recovery"
- `tools/xrun_report.py:378` maps `18d1:d001` to "recovery". `tools/phone:24` and `tools/phone-state:6,30` list d001 as a **fastboot** id (source).
- Fix: map only the ids that have a citation (`18d1:4ee0` fastboot, `18d1:d001` fastboot), else print the raw id.

### L7 (Low). EARLY + `--remote-reset` with the phone off USB gives a wrong report line
- `tools/xrun:111-113`. If no device is on USB, `to-fastboot` runs 90 s of "check cable" prompts (bounded by `timeout 120`, source `tools/phone:169-178`).
  The mark then says "reachable but to-fastboot failed", which is wrong.
- Fix: choose the text from `tools/phone state` (absent vs ios).

### L8 (Low). Doc drift in xrun
- `tools/xrun:15` still says the hard timeout counts "from xrun start" (now it counts from the lock, `:69`).
- `--no-camera` says "no SCREEN line", but the report prints `SCREEN: no camera frames` (`xrun_report.py:238,474`).

### L9 (Low). Each private image copy is 64 MiB
- *Observed:* `shim-boot-avb.img` is 67108864 bytes, and every run copies it into `logs/runs/*` or `logs/probe/*`.
- Fix: `rm -f "$D/shim-boot-avb.img"` after fboot (keep the meta), or `cp --sparse=always`.

## Checked and fine
- `fflash-userdata.sh` (*observed* with a stub fastboot): flash rc 1 makes the script rc 1 and adds no state line; rc 0 adds one line. `pipefail` covers `| tee`.
- `next` is atomic (`update-ref <ref> <sha> 0{40}` creates only if absent).
- The hook sees the committing worktree's index from the common-tools copy (*observed*: worktree collision refused; `commit -- path` with a temp index works).
- No lock-order cycle: the gate takes shim/out only, xrun/probe-run take phone then shim/out.
- The gate's own steps never take the build lock: probe-run's self-test runs in a temp tree (`tools/probe-run:150`).
- `fboot.sh` still refuses a stale or changed image: imgmeta checks sha, kind and source hash (`tools/imgmeta.py:67-90`) against the private copy and its meta.
  Probe-run boots without meta → "no meta" REFUSED (fails closed).
- Hook cost (*observed*, 20 calls): patch-guard 21 ms per non-patch call, 110 ms per patch-path call; phone-guard 17 ms. `patch-numbers status` 50 ms on 10 refs.
