# Review bt-dt-symlink-r1: commit 164cd0a "golden-pull bt-static: resolve /proc/device-tree before find"

Reviewer: Sonnet 5.5, adversarial. Date 2026-10-29. Tags: [obs] observed (command run now / artifact read), [src] read from source/file:line, [guess].

## Verdict: SAFE WITH CONDITIONS

Read-only change, no new write path, forbidden-word guard and address skip intact. Conditions: F1 (the cause is proven on GNU/busybox and by artifact, but not on the phone's toybox until the next run shows `ls -ld`; accept the fix as "unverified on hardware" until then) and F2 (b_where DT part should print a line saying what it searched).

Verification run (worktree, absolute paths): `bash tools/test_golden_capture.sh` -> rc=0, `RESULT: PASS test_golden_capture` [obs]. `shellcheck -S warning tools/golden-pull tools/test_golden_capture.sh` -> no findings, exit 0 [obs].

## Findings

**F1 (low, condition) Cause is sound but not yet confirmed on toybox.** [obs] On the host, `find LINK -type d` prints nothing and rc=0 for both GNU find (bfs 4.1.1) and busybox find when LINK is a symlink to a directory; `readlink -f` resolves it. [src] tools/golden-pull:612 (existing comment, by an earlier author) says "/proc/device-tree is a symlink to this" (/sys/firmware/devicetree/base); the new commit message says the same from kernel source but the kernel source was not re-checked here [guess for the kernel side]. [guess] toybox `find` defaults to physical traversal (-H/-L opt-in) like POSIX, so a symlink start point is lstat'd and `-type d` fails; no toybox source exists on this host to read. The artifact pattern fits exactly: [obs] gbtcold-...b2/bt/bt-dt.txt and gbp31b-...b2/bt/bt-dt.txt hold only `__GP_RC=1` (n=0 makes `[ $n -gt 0 ] && echo dt=ok` return 1, nothing else is printed because the `for` loop body never runs). Other causes considered:
- `-iname` unsupported: would print `find: ...` on stderr; BT() merges stderr (`2>&1`, tools/golden-pull:180) and the file has no such text -> not the cause [obs/src].
- `\(` quoting: the script goes through `printf '%s; echo __GP_RC=$?\n' | adb shell su -c sh` (golden-pull:180) on stdin, so no host or adb-shell quoting layer touches it; same mechanism as every other BT script that works [src].
- `for p in $d/*` and the `&&` rc: only reached/relevant when n>0 [src].
- Permission/SELinux on the target: would print "Permission denied" (merged stderr); none present [obs]. Independent oracle that the target is readable and holds matching nodes: [obs] g6-20261028T162457Z-b2/device-tree.tar.list.txt (tar of /sys/firmware/devicetree/base) lists `base/pinctrl@174E0000/bt-host-wake`, `base/pinctrl@10840000/bt-reg-on`, `bt-dev-wake` and `base/serial@175B0000/`, all matching the find patterns, so n=0 cannot be "no such node".
Fix: none needed. The new `ls -ld` line is the right confirmation; the next bt-static run should be read for `lrwxrwxrwx ... -> /sys/firmware/devicetree/base` and `dt=ok` (expect n>=4 nodes, per the tar list) before marking this closed.

**F2 (low, decision) b_where DT part can still be silently empty.** golden-pull:510: `find "$(readlink -f /proc/device-tree)" -iname ...; echo where=ok` prints nothing on no match and always ends `where=ok`. Decision: an empty result is a legitimate answer here ("no DT property name holds an address"), and a failing find is not silent (stderr merged into the file, e.g. `find: '': No such file` if readlink printed nothing). [obs] The stock DT tar list has no `mac-address|bd-addr|bdaddr` entry at all (g6-20261028T162457Z-b2/device-tree.tar.list.txt, though `chosen` is excluded from that tar), so the old empty output was probably also the true answer. But the file cannot show that the search ran on the real tree, which is exactly what hid this bug (rule 8). Fix (exact): in b_where replace `find "$(readlink -f /proc/device-tree)" -iname "*mac-address*" -o -iname "*bd-addr*" -o -iname "*bdaddr*";` with
`dtr=$(readlink -f /proc/device-tree) || dtr=/proc/device-tree; echo "dt-where-root=$dtr"; find "$dtr" -iname "*mac-address*" -o -iname "*bd-addr*" -o -iname "*bdaddr*";`
and add a test row `grep -q '^dt-where-root='`. Not blocking.

**F3 (info) __GP_RC changes meaning.** Old script returned rc 1 on n=0 (`[ ] && echo`); new script ends with `if/else echo`, so `__GP_RC=0` even when no node is found. [src] golden-pull:181 BT() judges only the `dt=ok` marker (`grep -qx`), and prints `GAP: bt-static: bt-dt.txt has no 'dt=ok' marker (a step failed or timed out, see bt-dt.txt)` (golden-pull:181-182), and the file now names the reason (`dt: no bluetooth/btbcm/bt-/175b0000 node under DIR`). So a still-unreadable DT stays a loud GAP (rule 8 satisfied) [src]. No fix needed; do not add a run-plan row that expects `__GP_RC=1`.

**F4 (info) readlink/`||` behaviour.** `dtr=$(readlink -f P) || dtr=P`: the assignment's status is the substitution's, so the fallback fires on a non-zero readlink [obs: busybox sh test, `x=$(false) || x=fb` -> fb]. toybox has `readlink -f` [guess: from memory of toybox's readlink flags -f/-e/-m/-n; not verified on the phone]; `ls -ld` exists in toybox ls [guess]. If readlink exits 0 but prints nothing, `find ""` errors loudly (stderr merged) and `dt-root=` is empty: visible. `"$dtr"` is quoted in find; `$d/*` and `$(find ...)` remain unquoted (pre-existing, DT names have no spaces [obs: tar list]). `$(...)` inside the single-quoted shell variable is intentional: it is expanded by the phone's sh, not the host (bt_guard and the string do not expand it) [src].

**F5 (none) Safety unchanged.** `bt_forbid` words absent from both new scripts (the full test's dry-run runs bt_guard on all seven scripts and passes) [obs]. Address skip `*addr*|*mac*|*bd_*|*bd-*` is on the full path `$d/*`; the new root `/sys/firmware/devicetree/base` contains none of those substrings [src]. Note: because the DT dump now actually runs for the first time (n was 0 in every earlier run), the first real output will contain the nodes listed above (pinctrl bt-* and serial@175B0000 children); their property names in the tar list (samsung,pin-*, reg-io-width, interrupts, clock-names, ...) contain no address-like name [obs]. The host mask `bt_mask` still applies to the file. The test uses fake values only (`secret`, `hcicompat`, temp dir, removed with `rm -rf "$dtt"`) [src: test_golden_capture.sh:659-672].

**F6 (low) Test gaps (mutants run on copies in /tmp/20s-rev2/, harness h.sh runs the new block against a mutated golden-pull).**
| Mutant | Result |
|---|---|
| old b_dt and old b_where (HEAD~1) | 6 rows FAIL (matches the author) [obs] |
| readlink removed from b_where only | killed (1 row: "bt-where: symlinked DT root ...") [obs] |
| else-diagnostic removed | killed (1 row) [obs] |
| address-skip patterns removed | killed (1 row) [obs] |
| `dt=ok` printed always | killed (1 row) [obs] |
| `echo "dt-root=..."` removed | killed (1 row) [obs] |
| `"$dtr"` unquoted in find | SURVIVES [obs] |
| `|| dtr=/proc/device-tree` fallback removed | SURVIVES [obs] |
| `ls -ld /proc/device-tree;` removed | SURVIVES [obs] |
Fixes (exact, in tools/test_golden_capture.sh after line 668):
- `ls -ld` row: the `ls -ld` argument is the replaced fake path, so assert the link target: `grep -q -- "-> $dtt/real\$" <<<"$dt_out"; ck "bt-dt: ls -ld line shows the symlink" 0 $?`.
- unquoted `$dtr`: add a second fake root with a space (`ln -s "$dtt/real" "$dtt/dt link"` and run `dt_run "$dt_script" "$dtt/dt link"`; expect `dt=ok`). Note dt_run substitutes into a bash -c string, so the substituted path needs quoting in the replacement (`"${1//\/proc\/device-tree/\'$2\'}"` or use a path-without-space variant and drop this row). Low value on the phone (no spaces in `/sys/firmware/devicetree/base`); acceptable to leave.
- fallback: add a PATH-shadow `readlink` that exits 1 (`mkdir -p "$dtt/nb"; printf '#!/bin/sh\nexit 1\n' > "$dtt/nb/readlink"; chmod +x ...`) and run the dt script with `PATH=$dtt/nb:$PATH` against the plain (non-symlink) fake root; expect `dt=ok`. Optional.
Also: no row asserts that b_where never prints a property VALUE (only path lines are checked); the script has no `cat`/`od` so this is by construction [src]. Optional.

**F7 (info) Test mechanics.** The test extracts scripts with `sed -n "s/^[[:space:]]*b_dt='\(.*\)'\$/\1/p"` and a `${1//\/proc\/device-tree/$2}` substitution, so it runs the real script text under bash, not mksh/toybox [src: test_golden_capture.sh:662-664]; host `readlink`/`find` are GNU. It proves the script logic, not toybox behaviour (see F1). The two "script found" rows guard against the sed extractor silently matching nothing [src].

## Antipatterns
No new antipattern. Possible entry if the maintainers want one: "host reproduction with GNU find stands in for toybox find on the phone" (covered by F1: confirm with `ls -ld` on the next run).

## Resolution (driver 20s, 2026-10-29)
All findings applied, none noted-and-skipped (user standing order W33 13:2x): F2 `dt-where-root=` line + test row; F6 survivors killed by new rows (ls -ld shows the link, root path with a space, readlink failing falls back); F1 stays a hardware item: the `ls -ld` line of the next phone run confirms the symlink on toybox (cause proven on GNU/busybox and by the artifact pattern). F3/F4/F5/F7 need no change. test_golden_capture PASS, shellcheck clean.
