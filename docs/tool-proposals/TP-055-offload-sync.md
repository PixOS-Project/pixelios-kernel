---
id: TP-055
tool: "tools/offload-sync"
status: approved
proposed_by: "claude-sonnet-5-5 (implementer 20aj for the driver; USER ORDER R63 2026-10-31 12:09Z + R64 for the root path)"
date: 2026-10-31
exposed_by: "USER ORDER R63: 'remote must be 1:1 synced with the local repo, so we can use the latest tools there'. [obs 2026-10-31] remote checkout was e5bcbcf (2026-10-27, 1214 commits behind), had NO pixos-project/ clones and no kernel trunk; every remote job ships a git bundle by hand (tools/remote-job)."
extends: ""
related: "TP-042, TP-048, TP-050"
usage: "tools/offload-sync [--dry-run | --check | --background] [--repo NAME]   (env OFFLOAD_ROOT, OFFLOAD_HOST, OFFLOAD_MIN_FREE_GB, OFFLOAD_SYNC_SSH, OFFLOAD_SYNC_SRC, OFFLOAD_SYNC_REPOS, OFFLOAD_SYNC_LOGDIR, OFFLOAD_SYNC_TIMEOUT)"
refs: "tools/remote-job, tools/heavy-job (remote route, ssh conventions), tools/super-push, tools/hooks/post-commit (super), tools/install-hooks (kernel branch tools/kernel-hooks)"
---

## checked
**Caller:** the post-commit/post-merge/post-rewrite hooks of the kernel and super repos (`tools/hooks/`), `tools/remote-job` and `tools/heavy-job` (`tools/offload-sync --check` before a remote job) and the driver by hand. Status `approved` = accepted by USER ORDER R63 (the register has no `accepted`). TP-054 lives on branch tools/kernel-hooks; until it merges the register shows a gap at TP-054 (validate-repo "ids not contiguous").

`tools/tool-census` and `ls tools`: no tool syncs a checkout to remote. `tools/remote-job` ships ONE committed HEAD as a git bundle per job and deletes it afterwards; `tools/heavy-job remote run` only queues a command; `tools/remote-stage-xnub` stages XNU build inputs. None keeps a persistent, current clone of the repos there. Read from source: remote-job and heavy-job reach remote with `ssh -o BatchMode=yes -o ConnectTimeout=3 remote@<offload host> bash -s` (the remote login shell is fish, so every remote command is `bash -s` with the script on stdin). [obs 2026-10-31] remote: git 2.56, bash 5.3, `<root>` ext4 with 114 G free, `~/PixOS` = clone of the super repo at e5bcbcf (an ancestor of local main), no `pixos-project/`.

## why
One order (R63): the local repos are the truth, remote mirrors them exactly so any tool, branch, wip/* or forced rewrite made at the local is on remote within seconds, without a bundle per job. remote NEVER pushes back (one-way). Without it remote runs stale tools (the 2026-10-27 tree).

## design
- **Direction and transport.** Local -> remote only, git objects only. Per repo: `git push --force --prune ssh://remote@HOST/ROOT/DEST '+refs/heads/*:refs/pixos-sync/heads/*'` into the namespace `refs/pixos-sync/heads/*` of the remote repo (a checked-out branch cannot be pushed to; `--prune` carries deletions; `+` carries forced rewrites). Then ONE `bash -s` apply script on remote, only when the tree is clean: the checked-out branch is `reset --hard` to its mirror (the local HEAD branch name is mirrored, e.g. trunk, main), every other `refs/heads/*` is created/moved/deleted with `update-ref` to equal the local's. A branch checked out in another remote worktree is left alone with a loud `OFFLOAD GAP`. The apply script refuses to run (and deletes nothing) when the mirror namespace is empty.
- **Repos (the default table, same layout as the local).** super `ROOT`, kernel `ROOT/pixos-project/pixelios-kernel`, `.../pixelios-userland`, `.../pixelios-site`, `.../upstream`. Missing clones are created (`git init`; the super repo first, because its .gitignore hides `pixos-project/`). Not in the table (OPEN ITEM, driver): `pixos-project/pixelios` (the public release repo, 2 MB) exists at the local; add it with `OFFLOAD_SYNC_REPOS` or the table when wanted.
- **Root (R64).** `OFFLOAD_ROOT` is the ONLY remote path; nothing is hard-coded in the tool, tests or hooks. The tool writes nothing outside it (paths are checked to stay under it; other directories on that disk belong to another T3 instance and are never touched). Free-space guard: sync and `--dry-run` refuse when the filesystem holding ROOT has less than `OFFLOAD_MIN_FREE_GB` (default 20) free: `OFFLOAD GAP: <root> <N>G free < 20G` + `RESULT: FAIL`, exit 1 (tested with the fake ssh).
- **Dirty trees are sacred.** Tracked changes, or untracked non-ignored files, in a remote repo: `OFFLOAD GAP: <repo> dirty, not updated`; nothing is pushed or moved there, never `reset --hard`, never a checked-out branch deleted; the other repos still sync; exit 1 at the end. Ignored files are neither counted nor touched.
- **Never copies ignored files** (.env, logs/, shim/out, tools/third_party, research/, IPSW, keys, the Apple ADT): only git objects travel (rule 4). A branch whose old remote head is not an ancestor of the new one (forced rewrite, or a local remote commit that was never on the local) prints `remote NOTE: <repo> <branch> old <sha> not an ancestor, overwritten (reflog keeps it)`.
- **Failure is loud.** Every ssh has `ConnectTimeout=5`, `ServerAliveInterval`, and a python timeout (`OFFLOAD_SYNC_TIMEOUT`, default 120 s, push 600 s). remote down: `OFFLOAD GAP: unreachable (<reason>)`, no hang, `RESULT: FAIL`. A local repo with uncommitted changes is not a gap: only commits travel.
- **Trigger and locking.** `--background` detaches (setsid, exit 0 at once, never delays or fails a commit) and takes `LOGDIR/offload-sync.lock` (flock). A second run while one is active sets `offload-sync.again`; the active run repeats once more after it, so overlapping commits coalesce and none is lost. Everything goes to `LOGDIR/offload-sync.log` (default `<kernel main checkout>/logs/`, gitignored); the next `--check` prints `remote NOTE: last background sync FAILED ...` from the last RESULT line of that log.
- **`--check` (read only, no writes, no mkdir).** One line per repo: `<repo> local=<sha12> remote=<sha12> <ok|DIFF|DIRTY|MISSING> branches_diff=N`, then `RESULT: PASS|FAIL`. PASS only if every repo head, every branch (incl. trunk/main) is identical, the checked-out branch names match and all trees are clean. `--dry-run` prints what a sync would do (create/push/move) and writes nothing.
- **Hooks (staged, nothing installed outside the repos).** Kernel (branch `tools/offload-sync-hook`, on top of `tools/kernel-hooks`): tracked `tools/hooks/post-commit`, `post-merge`, `post-rewrite` call `tools/offload-sync --background` of the MAIN checkout; a clone enables them with `tools/install-hooks` (TP-054; it copies every `tools/hooks/*` into `<git-common-dir>/pixos-hooks`; re-run it after the branch merges). Super (branch `tools/offload-sync`): `tools/hooks/post-commit` (after its background push) plus new `post-merge` and `post-rewrite`, live once the main checkout has them (core.hooksPath already points at its `tools/hooks`, `tools/install-hooks`).

## output
Prints `OFFLOAD GAP:` / `remote NOTE:` lines, per-repo lines (`sync <repo> pushed=N moved=M created=C deleted=D`), last line `RESULT: PASS|FAIL|ERROR offload-sync ...`. Exit 0 PASS, 1 FAIL (gap, dirty, unreachable, low space, differing --check), 2 usage/config (no such tree, bad env).

## control via T3

## OPEN ITEM (driver): gitignored inputs a remote gate/build needs and lacks
- `tools/third_party` (mkbootimg.py, avbtool.py, gki/): gitignored; remote-job links `gate-k/tools/third_party` into its job dir; a synced tree needs `ln -s $OFFLOAD_ROOT/work/gate-k/tools/third_party tools/third_party`.
- `research/ipsw-x/d22ap.adt` (Apple ADT) and `research/` generally; remote-job ships the ADT to `$OFFLOAD_ROOT/work/adt/d22ap.adt` by sha256.
- `.env` (camera/ESP32 credentials) and `logs/`: NOT needed by a gate; stay local only.
- Golden traces `~/pixelios-artifacts/golden/<TS>/{trace-gpu,trace-dpu}.txt` (tools/test_golden_gpu_analyze.py:14): remote-job ships them to `$OFFLOAD_ROOT/work/golden/TS`.
- The pinned gate image `pixos-gate:trixie-20261029b`, `opt/`, `gate-image-<tag>/` (build context) and the xnub inputs `xnub-art/`: hand-made under the work root.

## risks
No device, no phone. Writes only under `OFFLOAD_ROOT` on remote and `LOGDIR` on the local. `reset --hard` runs only after a clean-status check in the same script; a status error counts as dirty. A forced rewrite on the local overwrites remote (reflog keeps the old head). The kernel repo's `refs/original`/`refs/backup`/`refs/t3` are not mirrored (only heads).

## amendments
- 2026-10-31 (20ak): `ROOT/work` (remote-job's work root) lives inside the super clone. The tool writes `/work/` into the remote super clone's `.git/info/exclude` (local, nothing tracked, never copied back) before every sync, and a root whose only content is `work/` is not "foreign" (remote-job may create it before the first sync). Tests: test_work_dir_in_root_is_not_foreign_and_not_dirty + 2 mutants.
