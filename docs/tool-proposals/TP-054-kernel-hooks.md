---
id: TP-054
tool: "tools/install-hooks"
status: proposed
proposed_by: "claude-sonnet-5-5 (delegate of driver 20ad, plan docs/plans/PLAN-FAST-PATH-2026-10-31.md E10/E11)"
date: 2026-10-31
exposed_by: "the Gemini incident 2026-10-31: kernel commit 7d7a4cd landed directly on trunk, ungated, with the trailer 'PixOS-Worker: fix-21' although no fix-21 thread had run (super docs/HANDOFF.md watcher tick, W42 audit)"
extends: ""
related: "TP-022"
usage: "tools/install-hooks [--check]"
refs: "kernel branch tools/kernel-hooks"
---

## checked
- tools/patch-numbers install-hook (TP-022) [read from source tools/patch-numbers:190-215]: writes ONE untracked .git/hooks/pre-commit (patch numbers only). Nothing in the kernel repo tracks hooks, nothing stops a non-merge commit on trunk, nothing checks the trailer. `tools/tool-proposals check hooks` = NONE [observed 2026-10-31].
- Super repo [read from source]: tools/install-hooks + tools/hooks/{commit-msg,post-commit} (R41 H3, R42 G2) already do core.hooksPath for the SUPER repo; this proposal is the same mechanism for the kernel clone, plus the trunk guard and the role check.
- Agent hooks [read from source]: super .claude/settings.json wires phone-guard, patch-guard, agent-guard (--pre/--post/--context/--session) and tools/role hook with `$CLAUDE_PROJECT_DIR/pixos-project/pixelios-kernel/tools/...`; a session rooted in the kernel clone or in ~/.t3/worktrees/* has no .claude/settings.json at all [observed: `ls` of the worktree, 2026-10-31] so it runs with NO guard.
- Trailer-to-role mapping [read from source]: tools/role maps thread id -> role only (docs/ROLES.tsv columns role, thread_id, generation, since_utc, predecessor_id, model, status; tools/role:37 COLS). There is NO table of worker labels (20ad, 21y). Labels appear only in the ROLES.tsv header comments (infra-20: 20r..20z, fix-21: 21l..21u) and in commit trailers (git log: "PixOS-Worker: 20ad/20ac" by infra-20, "21x/21y" by fix-21). Delegates inherit the parent's T3CODE_THREAD_ID [observed: this delegate's env value equals the current infra-20 row].
- **Caller:** the driver runs `tools/install-hooks` once per clone after review and `tools/install-hooks --check` in reviews; tools/test_kernel_hooks.sh (picked up by tools/gate-unit-shards via tools/test_kernel_hooks.py) is the gate caller of the hooks and of install-hooks. Status row: allowlisted (census, because core.hooksPath/.claude are local state a hermetic gate cannot check); the super docs/WORKFLOW.md Commands row is owed (super repo).
- **Replaces:** the untracked .git/hooks/pre-commit written by `tools/patch-numbers install-hook` (its logic is carried over unchanged in tools/hooks/pre-commit; `install-hook` stays for repos without this).

## why
A direct trunk commit (7d7a4cd) had to be found by the watcher's tick audit and reverted by hand (branch tools/revert-gemini-l16); a wrong trailer was only noticed because the watcher cross-checked threads. Kernel sessions/workers run without any guard hook. Cost removed: one ungated trunk commit per incident (a full gate + revert round), and the trailer audit by hand.

## output
- pre-commit: (a) tools/patch-numbers precommit as before (rc 3 blocks; an internal error is a loud line and lets the commit through); (b) a non-merge commit on branch `trunk` (no .git/MERGE_HEAD) is denied with why + do-instead (branch + gate + merge) + the escape `PIXOS_TRUNK_DIRECT="reason"` (non-empty); the escape prints a NOTE.
- commit-msg: every commit needs a `PixOS-Worker:` trailer (escape PIXOS_ALLOW_NO_TRAILER=1, as in the super hook); with T3CODE_THREAD_ID set the trailer must be (a) the thread's role name, or (b) a numeric label NN[a-z]+ when the role is NAME-NN (infra-20 -> 20.., fix-21 -> 21..), and never another registered role. Everything not decidable prints a `NOTE ... NOT checked` line; escape `PIXOS_TRAILER_ROLE_OVERRIDE="reason"`.
- tools/install-hooks [--check]: sets core.hooksPath to the MAIN checkout's tools/hooks; copies tools/claude-settings.json to .claude/settings.json in the clone and in every `git worktree list` path under $PIXOS_WORKTREES_DIR (default /home/<user>/.t3/worktrees); `.claude/` is in .gitignore. Idempotent; --check changes nothing. Last line `RESULT: PASS|FAIL|ERROR ...`, exit 0 / 1 (something missing) / 2 (usage, hooks missing, not a repo).
- Test: tools/test_kernel_hooks.sh (68 cases in scratch repos, 19 mutants in `unshare -r -m -p -f --mount-proc`, all killed [observed 2026-10-31]).

## risks
- No device. Hook bugs block commits: each rule has an escape that prints a NOTE, and a broken environment (no python3, no registry, no thread id) only prints a skip line (never blocks, never silent).
- Open question 1 (label table): the label check relies on the NAME-NN <-> NN[a-z]+ convention read from ROLES.tsv comments. A role without a numeric suffix (gpu, watcher) cannot check labels (printed NOT checked). A `labels` column in ROLES.tsv (tools/role, super repo) would make it exact.
- Open question 2: delegates inherit the parent's T3CODE_THREAD_ID, so a driver's delegate must use the driver's role/labels; a delegate that commits under another stream's label is denied (override exists).
- Open question 3: `.claude/settings.json` copies hard-code the absolute super path for tools/role and the kernel main clone for guard fallbacks (single-host layout, same as the existing hooks).
- The trunk guard does not see merges git concludes without pre-commit (clean merges: they run pre-merge-commit, not pre-commit); they are allowed by design and still need a trailer (commit-msg).
- The new write path (git config, .claude/ copies) is unreviewed; install-hooks writes only `core.hooksPath` and `.claude/settings.json`, never outside the clone and $PIXOS_WORKTREES_DIR.

## amendments
(none)
