# Review <lab-host>-bootstrap r1 (kernel branch tools/<lab-host>-bootstrap, fea5e56 + 93839fb)

Reviewer: Sonnet 5.5, adversarial. Scope: tools/remote-job:267-285 (+ the line-551 lane wording), tools/test_remote_job.sh:254-268. Read-only; mutants were run on /tmp copies (removed afterwards). No podman/docker/adb/ssh to <offload-host> run.
Tags: [obs] observed on this host (hostname <lab-host>, 2026-10-29), [src] read from source file:line, [guess].

## Verdict: SAFE WITH CONDITIONS

No hardware or data-loss path. The seeding writes only under <work-dir> (and reads ~/.local/opt, tools/gate-image, tools/third_party). Wrongly-seeded content cannot make a run falsely formal: the identity hashes third_party, verlist and the toolchain (tools/gate-identity:15,18,22), and a mismatch gives "NOT formal" (tools/remote-job:571-575). Conditions: fix F1 (seed is a symlink, not a copy, from any worktree) and F2 (half-copied dir accepted forever) before relying on the lane unattended; F3-F6 are test/hardening.

## Findings

F1 (MEDIUM) tools/remote-job:282-284. `cp -r "$tps" .../gate-k/tools/third_party` copies a SYMLINK when `$tps` is a symlink.
- [obs] in this worktree `tools/third_party` is a symlink to the main clone (the AP-8x worktree habit; `ls -la` shows `-> /home/<user>/.t3/projects/pixelios/pixos-project/pixelios-kernel/tools/third_party`), and the main clone's own one is a real dir. `[ -d "$tps" ]` is true for the link. [obs] reproduced in /tmp with GNU cp 9.7: `cp -r link dst/x` leaves `dst/x -> /abs/main/third_party`, `cp -r link/ dst/x` copies the tree.
- Failure scenario: the lane runs from a worktree (the normal case). gate-k/tools/third_party becomes a link to the main clone, contradicting the comment on line 268 ("copies for the other two, a worktree may vanish") and the REMOTE line "seeded". Later edits in the main clone's tools/third_party change the gate input behind the seed (identity would catch it as NOT formal, late), and if the clone is moved or the link target removed the lane dies at the gate, not at the seed. The mount on line 121 (`-v .../gate-k/tools/third_party:...:ro,Z`) then has a symlink as source [guess: podman resolves it on the host; not run].
- Fix: `cp -rL "$tps/." "$tmp/"` into a temp dir (see F2), or at least `cp -r "$tps/" ...` (trailing slash, tree copy [obs]). Test with a symlinked source (see F4).

F2 (MEDIUM) tools/remote-job:276,281 (and 278,284 for the copies). The guards `[ ! -d DIR ]` accept a partial or empty copy.
- Failure scenario: disk full or Ctrl-C/kill during `cp -r` (die on cp failure leaves the half dir; `mkdir -p gate-k/tools` also pre-creates the parent). Next run: `-d` true, no seed, no REMOTE line. The remote script (line 516) does `[ -d "$tp" ] && ln -s` and the gate runs with a missing mkbootimg.py / avbtool.py. The identity then reports third_party mismatch (NOT formal), but the run costs a full gate cycle and the cause (a half seed) is not named. Same for an empty dir made by hand and for gate-image (verlist.sh missing -> GATE_VERLIST unreadable, identity verlist=MISSING).
- Fix: copy into `"$dest.new.$$"`, `rm -rf` it on failure, `mv -T` to `$dest` on success; guard on a marker file instead of the dir (`[ -f "$hlp/gate-k/tools/third_party/mkbootimg.py" ]`, `[ -f "$hlp/gate-image-20261029b/verlist.sh" ]`) and `rm -rf` + re-seed (loud REMOTE line "re-seeding incomplete ...") when the dir exists without it.

F3 (LOW) tools/remote-job:273-274. `opt` guard `[ ! -e ]`: a dangling `opt` link (target removed) is "not existing", then `ln -s` fails with "File exists" and the lane dies with "cannot link" (loud, not wrong). A stale link pointing to the wrong place is never checked (only the armtc identity hash catches it later).
- SELinux/Z question (check 1): [obs] this host has no SELinux (no `getenforce`, no /sys/fs/selinux; LSM list: lockdown,capability,landlock,yama,apparmor,tomoyo,bpf,ipe,ima,evm), so `:ro,Z` is a no-op here and a relabel of ~/.local/opt (465M, toolchain + lz4, no other user files [obs `ls`]) cannot happen. [guess] on an SELinux host podman relabels the resolved target, i.e. the real ~/.local/opt and, via F1, the main clone's tools/third_party; harmless for a container-only toolchain dir but worth knowing. Mount follows the symlink [guess, not run].
- Fix: `[ -d "$hlp/opt/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-elf/bin" ]` as the guard; if `opt` is a link that fails it, `ln -sfn`; die naming the stale target.

F4 (MEDIUM, test) tools/test_remote_job.sh:254-268. The worktree fallback (remote-job:282) and copy-vs-link are untested.
- [obs] mutants on /tmp copies: removing line 282 (fallback to the main clone) -> `RESULT: PASS cases=178`; replacing `cp -r "$tps" ...` by `ln -s "$tps" ...` -> `RESULT: PASS cases=178`. The test's $repo has a real tools/third_party (line 254), and only `-f .../x.py` is checked (line 264), which a link satisfies.
- Fix: add a case with `tools/third_party` absent in the tool repo and present in a main clone reached through git-common-dir (worktree), and one with it as a symlink; assert `[ ! -L "$w/home/work/pixos/gate-k/tools/third_party" ]` and the REMOTE line names the source. Add an "interrupted copy" case for F2.

F5 (LOW) tools/remote-job:282. `git rev-parse --git-common-dir` is relative (".git" in the main clone, [obs]) and the `cd "$(...)/.."` runs in the caller's cwd, not in $tooldir. Only reached when `$tooldir/tools/third_party` is missing and the caller's cwd differs from $tooldir; then it looks at the wrong dir (loud die or the wrong source). Fix: `git -C "$tooldir" rev-parse --path-format=absolute --git-common-dir`.

F6 (LOW) tools/remote-job:267-285. The seeding block runs for every `--where <lab-host>` invocation, including `--collect` and `--kill-orphans`, before the job is known to be a gate-pre, so a collect run can create <work-dir>/* or die on a missing source. Fix: guard with `[ "$job" = gate-pre ] && [ -z "$collect" ]` (same conditions as line 252 area) or accept and document.

F7 (INFO) gate-image seed copies `tooldir/tools/gate-image` (working tree, may be dirty or a different branch's recipe) into a directory named for tag 20261029b (remote-job:276-278, 94). Only verlist.sh is read at gate time (GATE_VERLIST=/gate-image/verlist.sh, line 98), and the identity verlist hash (expected 'ce889e3d...') catches a recipe mismatch as NOT formal. The image `pixos-gate:trixie-20261029b` is NOT seeded; line 99 exits 95 if <lab-host>'s podman lacks it [src]. Not checked by this change (no podman run allowed).

## Check results

1. Symlink + :Z: see F3 (no SELinux [obs]); mount-follows-link is [guess].
2. Stale/empty/partial: not safe, F2 (mutually independent of the later identity guard, which is loud but late).
3. Content: [obs] main clone tools/third_party hashes (identity formula, `find -L`) to f75163e3...4630 = tools/gate-identity.expected:8, so a copy of it is the right content. Copy risks: symlink-as-copy F1; `cp -r` of a real dir keeps inner symlinks as links (none present: `find -type l` empty [obs]) and copies __pycache__ (excluded from the hash, same python 3.13).
4. Identity/formal label: seeding cannot flip the label to formal wrongly (hash inputs: third_party tools/gate-identity:22, verlist :15, armtc :18; label at remote-job:571-575). A bad seed gives NOT formal. Line 551 only changes the failure wording (lane name), no label effect.
5. Test mutants (copies in /tmp, never the worktree), baseline `bash tools/test_remote_job.sh` -> `RESULT: PASS cases=178`:
   - M1 remove opt `ln -s` -> `RESULT: FAIL cases=178 failed=1` (the "gate prerequisites ... seeded" case): killed.
   - M2 remove third_party `cp -r` -> `RESULT: FAIL ... failed=1` (same case): killed.
   - M3 remove gate-image `cp -r` -> `RESULT: FAIL ... failed=1`: killed.
   - M4 remove the worktree fallback line -> `RESULT: PASS cases=178`: SURVIVED (F4).
   - M5 `cp -r` -> `ln -s` for third_party -> `RESULT: PASS cases=178`: SURVIVED (F1/F4).
   Weak point: the "cannot be seeded" case (line 268) passes only because the previous run left `opt` as a link that dangles after `rm -rf $w/home/.local/opt`; the `mv` of third_party and gate-image away there are not asserted. It does verify the die message and that podman `run` was not called.
6. `shellcheck -S warning tools/remote-job tools/test_remote_job.sh` -> no findings, exit 0 [obs].

## Antipattern added
AP-242 in docs/ANTIPATTERNS.md (super repo): `cp -r` of a symlink source copies the link.
