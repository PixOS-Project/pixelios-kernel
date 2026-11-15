# Review tools-sync-trunk-r1: tools/tools-sync default ref common-tools -> trunk (commit 81708cd)

Reviewer: Sonnet 5.5, adversarial, 2026-10-29. Diff: `git diff trunk...HEAD` (5 files, 11+/11-). Worktree not edited; experiments ran in /tmp.

## Verdict: SAFE WITH CONDITIONS

The code change is one default string and is correct. Conditions: fix F1 (stale doc sentence the same hunk leaves behind) before merge; F2 and F3 are existing issues with the same root cause and can be follow-ups.

Verification: `python3 tools/test_tools_sync.py` prints `Ran 8 tests` and `OK` [observed]. No shell files in the diff (nothing to shellcheck).

## Findings

**F1 (Medium, doc, fix before merge). docs/AUTOMATION.md:519 still says "must be upstreamed to `common-tools`"** [observed: grep]. The same paragraph (line 517) was just changed to say that branch no longer exists, so a reader follows line 519 to a deleted branch. Fix: replace `upstreamed to \`common-tools\`` with `upstreamed to \`trunk\``.

**F2 (Medium, pre-existing, same root cause, follow-up). The patch-numbers pre-commit guard is silently off.** `tools/patch-numbers:11,192` and the installed hook `<git-common-dir>/hooks/pre-commit` run `$HOME/.t3/worktrees/common-tools/kernel/tools/patch-numbers`. `~/.t3/worktrees/common-tools` exists but is EMPTY (`ls -la`, mtime Oct 25) [observed], so the hook's `[ -f "$T" ] || exit 0` skips the check on every commit with no message (rule 8). Also stale: tools/test_patch_guard.py:111 (a `.replace()` of that path, which then matches nothing in the hook text it reads if the hook ever changes) and docs/AUTOMATION.md:536. Fix (own change): point `T` at the committing repo (`$(git rev-parse --show-toplevel)/tools/patch-numbers`) or the trunk clone, make the missing-script branch print `patch-numbers: script missing, numbers NOT checked` to stderr, reinstall the hook (`tools/patch-numbers install-hook`). Entry added as AP-241.

**F3 (Low, pre-existing, now more visible). `pull --prune` deletes a COMMITTED patch that trunk does not have.** Experiment in /tmp (trunk checkout, then `git checkout -b feat`, commit `xnu/patches/0002-new.patch`, `tools-sync pull --prune`): output `pruned xnu/patches/0002-new.patch`, `PASS`, `git status` shows ` D` [observed]. `only-here` is guarded by `dirty()` only (tools/tools-sync:~121), not by `local_commits()`. With the ref now `trunk`, every stream branch holding its own patches is in this state, so `--prune` is no longer an unusual case. Recoverable (`git checkout -- FILE`), and `--prune` is opt-in, so Low. Fix: in the `only-here` branch also require `not local_commits`-style check (file in `HEAD` but not in the ref and not `--force` => blocked), or document that `--prune` is for stale untracked files only. Also: the same run printed `PASS pulled 1 file(s)` for a deletion (wording only).

**F4 (Low, pre-existing). `--prune` also deletes git-ignored files in a managed directory.** In /tmp with `.gitignore: ign*`, `xnu/patches/ign1` was `pruned` without `--force` because `git status --porcelain` does not list ignored files [observed]. Fix: use `git status --porcelain --ignored -- path` in `dirty()`.

**F5 (Info). Docstring/doc cites have no line.** docs/AUTOMATION.md:517 cites "`tools/tools-sync` `--ref` default" without a line (AGENTS.md rule 5/7 want file:line). The claim "`tools/test_tools_sync.py` runs on the default" is TRUE [observed, mutants below]; it is the only claim in the new sentence that was not tagged. Fix: write `tools/tools-sync:88` and tag it observed.

## Check results

1. **Remaining `common-tools` mentions** (`grep -rn common-tools --exclude-dir=logs --exclude-dir=.git`):
   - Wrong or misleading now: docs/AUTOMATION.md:519 (F1); tools/patch-numbers:11,192, tools/test_patch_guard.py:111, docs/AUTOMATION.md:536 (F2); docs/tool-proposals/TP-021-tools-sync.md:10 usage line `[--ref common-tools]` (Low: a proposal, document-of-record; add a one-line "default is now trunk" or leave as history).
   - Historic and fine: tools/tools-sync:10 (says the branch is gone), docs/AUTOMATION.md:517 (same), TP-022 refs line, docs/reviews/16-*, docs/reviews/18-*.
   - Callers with the old ref: none. `grep tools-sync` over tools/, .claude/, hooks, shim/ finds only the manifest, tools/check-all.sh:51 (a comment) and docs; nothing passes `--ref common-tools` [observed]. `.claude/settings.json` patch-guard path is `$CLAUDE_PROJECT_DIR/pixos-project/pixelios-kernel/tools/patch-guard`, not common-tools [observed].
   - Note: trunk's own `tools/tools-sync:88` still defaults to `common-tools` until this branch merges [observed: `git show trunk:tools/tools-sync`], so until then `--ref trunk` must be passed.
2. **pull with ref == current branch / branch cut from trunk.** Read tools/tools-sync:101-150 and ran it in a /tmp repo [observed]. On the trunk checkout itself a clean `differs` file cannot exist (HEAD == ref), so only dirty files differ and those are blocked without `--force`; a dirty `tools/a` was blocked, kept (`A-dirty`) and overwritten only with `--force` (the documented override). Untracked files in a managed dir are blocked (dirty) unless `--force`. Writes stay inside manifest entries (`files` comes from the ref's common-files.txt plus `os.listdir` of manifest directories only, tools/tools-sync:26-31); `git checkout REF -- FILES` cannot touch other paths. Residual: F3, F4. A branch name `trunk` colliding with a file is harmless (`--` separates).
3. **NOTICE.md line 12 is factually right.** `ls xnu/patches/0124*` lists `0124-sweet-wlan-bwfm-core.patch`; `git log --oneline -1 -- xnu/patches/0124-sweet-wlan-bwfm-core.patch` = `5184f07 Wi-Fi patches 0124/0128/0129 ...`; `git ls-tree trunk xnu/patches/0124-...` shows the blob (0fe160d) [observed]. The sentence also drops "not on this branch", consistent with the worktree containing it.
4. **Test exercises the default ref and fails if it is wrong.** No test calls tools-sync with `--ref trunk` except the negative `--ref nope` case [read: tools/test_tools_sync.py]. Mutants on a /tmp copy: default `common-tools` => 8 of 8 tests fail; default `main` => 7 fail; default `HEAD` => 7 fail [observed]. Gap (Low): no test runs with ref == checked-out branch, nor the `only-here` + committed-file case (F3); add one test each if F3 is fixed.
