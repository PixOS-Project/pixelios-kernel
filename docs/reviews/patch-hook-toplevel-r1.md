# Review r1: tools/patch-hook-toplevel, commit 1e9f06e (reviewer, Sonnet 5.5, 2026-10-29)

Scope: `git diff trunk...HEAD` = tools/patch-numbers (HOOK string, usage text) + tools/test_patch_guard.py. Tags: [obs] = observed in /tmp temp repos or a run now; [src] = read from source.

## Verdict: SAFE WITH CONDITIONS

The new hook does what it claims: it checks the committing worktree's index and branch, and it is loud when the script is missing. Condition: the fix is not live until `tools/patch-numbers install-hook` is re-run in the kernel clone (F1). Two tests gaps and two minor wording/noise items follow.

## Answers to the brief

1. `git rev-parse --show-toplevel` inside the hook [obs, /tmp/phr/main, /tmp/phr/wt]:
   - normal clone: top = clone root, pwd = clone root.
   - linked worktree (hook shared via common dir): top = the worktree (`/tmp/phr/wt`), GIT_DIR = `.../main/.git/worktrees/wt`.
   - `git -C OTHERDIR commit` and `cd /tmp; GIT_DIR=... GIT_WORK_TREE=... git commit`: top = the real worktree (git chdirs to the work tree before running the hook).
   - `git commit` from `sub/`: pwd and top = repo root.
   - GIT_DIR set, GIT_WORK_TREE unset, cwd /tmp: git itself treats cwd as the work tree, so top = /tmp, T = `/tmp/tools/patch-numbers`, loud skip (consistent with what git commits; not a defect).
   - bare repo (hook run by hand): rev-parse fails (`fatal: this operation must be run in a work tree`, hidden by 2>/dev/null), T = `/tools/patch-numbers`, hook prints "patch-numbers: /tools/patch-numbers not found, ... NOT checked" to stderr and exits 0. Loud and safe (a bare repo has no commits through pre-commit anyway); the path in the text is misleading, see F4.
2. ROOT/index [obs, /tmp/phr2]: branch `gpu` holds 0102-gpu; a linked worktree `wifi` (from trunk) staging 0102-wlan.patch is refused, rc=1 ("0102: 0102-wlan.patch is staged here, but 0102-gpu.patch on gpu"); same in the main clone; same via `git -C /tmp/phr2/wifi commit` run from /tmp. The old-style hook (script of a different clone, main clone commit, GIT_DIR unset) let the identical collision through, rc=0 (it read the other clone's empty index): the fix is real, not cosmetic. [src] tools/patch-numbers:20 ROOT = dirname(dirname(__file__)), :25 `git -C ROOT`; in a linked worktree git also exports GIT_DIR/GIT_INDEX_FILE [obs], which is why the main-clone case was the one that broke.
3. Branch without tools/patch-numbers [obs, wifi worktree after `git rm tools/patch-numbers`]: every commit prints `patch-numbers: /tmp/phr2/wifi/tools/patch-numbers not found, patch numbers NOT checked for this commit (merge trunk into this branch)`, rc=0. Acceptable: the alternative is the silent skip that caused AP-241. The wording is actionable (path + consequence + fix). It is noisy only for branches that predate the script, and merging trunk ends it.
4. Mutation of a COPY under /tmp/phr_m [obs] (each: `python3 test_patch_guard.py`):
   - old hook line (`$HOME/.t3/worktrees/common-tools/...`) -> FAIL test_installed_hook_fails_open_but_blocks_a_collision (assertNotIn "common-tools").
   - message removed (`exit 0` only) -> FAIL test_installed_hook_missing_script_is_loud.
   - T pointing at another worktree's script -> 2 FAIL.
   - missing-script branch changed to `exit 1` -> FAIL (rc check).
   - SURVIVE: `T=$PWD/tools/patch-numbers` (equivalent mutant: hooks run at the toplevel; harmless); dropping the `command -v python3` silent line (not covered, F3).
   Baseline [obs]: `Ran 10 tests` / `OK`.
5. HOOK text extracted to /tmp/phr_hook.sh [obs]: `sh -n` ok; `shellcheck -S warning` clean (exit 0). `shellcheck -S warning tools/patch-numbers` only reports the python shebang (not a shell file).

## Findings

F1 (medium, condition). The fix does not take effect until someone re-runs `tools/patch-numbers install-hook` for the kernel clone. [obs] The live `<kernel clone>/.git/hooks/pre-commit` still contains `T=/home/<user>/.t3/worktrees/common-tools/kernel/tools/patch-numbers` / `[ -f "$T" ] || exit 0` (read only, head -8). Scenario: the branch merges, nobody re-installs, every commit keeps skipping silently, two streams pick 0102. Fix: after merge, the driver runs `python3 tools/patch-numbers install-hook` once in the kernel clone (install-hook overwrites a hook that contains "patch-numbers", tools/patch-numbers:~205) and then checks `grep -c show-toplevel .git/hooks/pre-commit` = 1; put that step in the merge note/HANDOFF. Better, optional: make `tools/check-all.sh` (or a tools/ self-check) fail when the installed hook lacks `show-toplevel` or points at a missing file, so staleness cannot return silently.

F2 (low, test gap). No test commits from a linked worktree, which is the actual failure case (hook shared via the common dir). The tests commit in the main repo via `git -C d`. A regression to e.g. `T=$(git rev-parse --git-common-dir)/../tools/patch-numbers` would pass all 10 tests but read the main clone's index for worktree commits [src: tools/test_patch_guard.py:108-126]. Fix: add a test that installs the hook, does `git worktree add -b wifi2 WT main`, stages `xnu/patches/0102-wlan.patch` in WT and asserts `git -C WT commit` returns non-zero with "overlapping".

F3 (low, rule 8). The line `command -v python3 >/dev/null 2>&1 || exit 0` (tools/patch-numbers HOOK, 4th line) is still a silent skip, the same class as AP-241. Fix: `command -v python3 >/dev/null 2>&1 || { echo "patch-numbers: python3 not found, patch numbers NOT checked for this commit" >&2; exit 0; }` plus a test with PATH reduced (git, sh only).

F4 (low, wording). With an empty toplevel the message prints `/tools/patch-numbers`, which reads like a real path. Fix: `TOP=$(git rev-parse --show-toplevel 2>/dev/null) || TOP=; T=$TOP/tools/patch-numbers`, and in the message use `${TOP:-<no work tree>}`; or add "(not in a work tree)" when TOP is empty. Cosmetic: still loud, still exit 0.

F5 (info, noise). [obs] A clean new-patch commit prints a Python `DeprecationWarning: datetime.datetime.utcnow()` from tools/patch-numbers:180 on stderr on Python 3.12+ (every such commit). It sits next to the loud line and trains people to ignore hook stderr. Fix: `datetime.datetime.now(datetime.timezone.utc)` (same format string). Pre-existing, not part of this diff.

F6 (info). The hook now runs the branch's own working-tree copy of tools/patch-numbers, not trunk's and not the staged blob: an old or locally edited copy defines the policy for that branch. That is the design requested (and what makes the ROOT/index fact true); the loud message's "merge trunk" advice is the mitigation. No fix needed; mention in the merge note.

F7 (info, docs). AP-241 in the super docs/ANTIPATTERNS.md (line ~1395) still says the hook path is stale; update it to "fixed in 1e9f06e, live after install-hook re-run (F1)". The super file also contains two entries numbered AP-243 (xrun-el2-b3-r1 and runplan-gputest113g-capv7); renumber one. Not in this diff.

No AP entry added by this review (F1 is already AP-241's "grep every reference" lesson; the new part, re-installing a generated hook after changing its template, is covered by the F1 fix).
