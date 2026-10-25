# Review 19: dev overlay fixes (branch dev-overlay, 336620f + merge f8efa70; diff ac0b940..HEAD)

Reviewer: Claude Opus 5.5, 2026-10-24. Adversarial re-review of the review-18 fixes, read-only on code. Sources:
`xnu/patches/0117-sweet-dev-overlay.patch` at HEAD (cited as P:line, patch-file lines), patched tree
`~/pixelios-artifacts/scratch/stack-BW7Oi8` (T/; its VFS hunks are identical to HEAD, only sweet_devoverlay.c changed).

## Verdict: UNSAFE (as committed); SAFE WITH CONDITIONS once C1 is fixed and its check added

The M2 fix broke the temp/backup file names (C1). The first run (one new file) would still log `wrote ... 6 bytes`
and PASS, but it first creates `/privat` in the volume's root directory, outside the allowlist, and then renames it.
That breaks the "one new file and nothing else" rule, and a PASS would hide a bug that makes every later replacement
a no-op that is still logged as success, and makes `restore` unable to work. Fix C1 (two tokens) and add the static check
from T1 first. After that, the first run as documented (docs/AUTOMATION.md:105-113) is acceptable.

Count: Critical 1, High 0, Medium 1, Low 5.

## Status of the review-18 conditions

| Item | Status | Evidence |
|---|---|---|
| M1 hard-link write-through | fixed, but the new defect C1 hits it | P:407-409 `sd_rm(tmp)` then `O_CREAT\|O_EXCL\|FWRITE\|O_NOFOLLOW`; `vnode_setsize` gone. vn_open returns EEXIST for any existing leaf with O_EXCL (T/bsd/vfs/vfs_vnops.c:503-504), so a hard link, symlink or directory is never opened; O_NOFOLLOW maps to NOFOLLOW (T/bsd/vfs/vfs_subr.c:7468-7474). Flag values T/bsd/sys/fcntl.h:113,125,127,129 (read from source). A directory tmp: unlink gives EPERM (T/bsd/sweet_hfs/core/hfs_vnops.c:3946-3948, POSIX), the open gives EEXIST, and the entry is logged `failed`: no harm. No race: there is one caller, in bsd_init before `bsd_utaskbootstrap` (P:24-31), so no user process exists. But the tmp NAME is wrong, see C1. |
| M2 path buffers off the stack | fixed, introduced C1 | P:443-450 static `sd_w`. The only caller is bsd_init (P:27; grep: no other reference). sd_one does not recurse, and nothing in VFS calls back into it. Bounds: `sd_show(..., sizeof(sd_w.shown))` P:453 is correct, and `sd_path_check` writes at most plen+1 <= 1001 bytes into path[1001]. **P:466-467 still use `sizeof(tmp)`/`sizeof(orig)`, which are now `char *`: 8 bytes.** Remaining stack use: `arg[16]`, two `struct sd_ent`, one `vnode_attr` each in sd_write_tmp/sweet_vfs_mkdir: acceptable. |
| M3 first run adds one new file | fixed (docs) | docs/AUTOMATION.md:105-113 matches review 18's plan. The expect/fail-on strings match the printfs: `wrote %s %llu bytes%s %llu ms` P:524, `done: %u written, %u skipped, %u refused/failed` P:583, `refused` P:462/477/489/499, `failed` P:480/494/520, `bad archive` P:556/560, `root is not mounted` P:565, `out of memory` P:570. xrun always passes `--rw` (tools/xrun:28), so `rootrw=1` is set. One fail-on alternative never matches, see L4. |
| M4 kernel host test | fixed, with gaps | tools/test_devoverlay_kernel.py:45-51 compiles the patch's own `+` lines between the markers (P:115-311); it does not compile a copy. 16 mutants, all caught (observed). Gaps: see M1-new and T1. |
| L1 (r18 #5) `.sweetorig` lookup error | fixed (P:498-502); a related gap remains, see L1 below | |
| L2 (r18 #6) rollback log | fixed | P:518-520 |
| L3 (r18 #7) case-insensitive suffix | partly fixed | P:228-246, mkdevoverlay.py `b.lower()`. ASCII only; HFS+ ignorable code points remain, see L2 below |
| L5 (r18 #9) FIFO in DIR | fixed | mkdevoverlay.py `os.lstat` + `S_ISREG`, test `test_packer_refuses_fifo_without_opening` |
| L6 (r18 #10) size cap | fixed (40 MiB, not 20 + override; acceptable) | mkdevoverlay.py MAX_TOTAL |
| L8 (r18 #12) `--owner` warning | fixed | mkdevoverlay.py `collect` WARNING + test |
| r18 #8 (VFS_SYNC), #11 (record/remove created files) | not addressed (not conditions) | P:582 has no sync |

245-byte limit (P:276; twin in mkdevoverlay.py): 245 + `.sweetorig` (10) = 255 = NAME_MAX bytes, and `.sweettmp` is
shorter. For UTF-8 input, UTF-16 units are never more than bytes, but HFS+ NFD decomposition can expand: U+0390 is 2 bytes
and 3 units (guess from Unicode data, not measured). Such a name then fails at create with ENAMETOOLONG and the entry is
logged `failed` or `refused` (P:498). That is safe, so there is no finding. The limit is applied to every component, not
only the last one: harmless.

Merge (requirement 3, observed): boot-speed-patches (29be8e3) is an ancestor of HEAD.
`git diff boot-speed-patches HEAD -- 0115 0116` is empty. The .patch files are numbered 0001-0117 with no gap (the
duplicate 0019/0020/0026 entries are the `.sh` stagers). `tools/patch-numbers status`: `RESULT: PASS`, next free 0118.
`tools/stack-apply`: `RESULT: PASS 117/117 patches (+ 3 stagers)`.

## Findings

### C1. Critical: the temp and backup names are truncated to 7 bytes (P:466-467)
`snprintf(tmp, sizeof(tmp), "%s.sweettmp", path); snprintf(orig, sizeof(orig), "%s.sweetorig", path);` stayed the same
after `tmp`/`orig` became `char *` (P:450), so the size is 8. For `/private/var/dev/hello`, tmp = orig = `/privat`
(observed with a host build of the same lines: `[/privat]`). The compiler does not warn under the kernel flags: host clang with
`-fno-builtin -ffreestanding -Weverything` gives no warning (observed), and XNU builds with `-fno-builtin`
(T/makedefs/MakeInc.def:310). So `-Werror` does not help.
Failing scenarios, read from source:
* **First run (new file):** P:408 unlinks `/privat` and P:409 creates `/privat` in the volume root (outside the allowlist).
  P:515 then renames it to `/private/var/dev/hello`. The log says `wrote /private/var/dev/hello 6 bytes` and the run PASSes.
  A panic between P:409 and P:515 leaves a stray `/privat`. If a file `/privat` (or `/usr/li`, `/usr/lo`) already exists, it
  is deleted.
* **Replacement (checklist step 3):** tmp == orig == `/usr/lo`. P:511 renames the original over the new tmp file, which destroys
  the new data. P:515 moves the original back. The log says `wrote ... (original kept)`, but nothing changed and no
  `.sweetorig` exists, so `restore` reports `no .sweetorig`. A crash between P:511 and P:515 leaves `/usr/local/X`
  missing, and its only copy is at `/usr/lo`, where `restore` never looks.
* **`/System/...` entries:** tmp = orig = `/System` (a directory). P:471 sees orig as an existing VDIR, so no backup is taken
  and the write fails with EEXIST.
**Fix:** at P:466-467 use `sizeof(sd_w.tmp)` and `sizeof(sd_w.orig)`. Also check that `snprintf` did not truncate:
`if (snprintf(tmp, sizeof(sd_w.tmp), ...) >= (int)sizeof(sd_w.tmp) || snprintf(orig, ...) >= (int)sizeof(sd_w.orig)) { refuse "name too long"; }`.
Add the T1 check so this cannot come back.

### M1-new. Medium: the kernel host test does not cover the code that writes (tools/test_devoverlay_kernel.py:12-42)
The harness calls only `sd_hdr`, `sd_walk`, `sd_next`, `sd_path_check` and `sd_crc32`. Everything after the
`host-test end` marker is never compiled on the host: `sd_one` (where C1 is), `sd_parents`, `sd_write_tmp` and
`sweet_devoverlay_run`. The test therefore passed with C1 in place (observed: 3 tests OK). Extra mutants run by this review
(script `/tmp/rev19/mut.py`, observed) all **survived with 0 mismatches**:
* `*mkmin = pl - 1` -> `0` (allows mkdir above the allowlist prefix);
* K_NAME `*mkmin = -1` -> `0`;
* K_EXACT `if (n != pl) continue` dropped: `/System/Library/xpc/launchd.plistX` would be accepted, because no corpus path
  extends the exact entry;
* `sd_show` cap `cap - 1` -> `cap` (a one-byte overflow of `shown`; sd_show is inside the markers but never called).

`build()` (lines 58-62) also falls back to a build WITHOUT sanitizers if the sanitizer build fails, and says nothing.
On this host the sanitizer build works (observed rc 0).
**Fix (smallest):**
(a) T1, static check, catches C1: in the test, read the whole sweet_devoverlay.c text and
`assertIsNone(re.search(r'sizeof\((shown|path|tmp|orig)\)', full_src))`. Better: move the name construction into a marked
pure helper `static int sd_names(const char *path, char *tmp, size_t tcap, char *orig, size_t ocap)`, call it from
sd_one with `sizeof(sd_w.tmp)`/`sizeof(sd_w.orig)`, and have the harness print tmp/orig for every `ok` entry and compare
them with `p + '.sweettmp'`/`p + '.sweetorig'`.
(b) Print `mk` in MAIN (line 34) and have `path_check` return the same `mkmin` in Python (D: len(pfx)-1, X/N: -1).
(c) Add `'/System/Library/xpc/launchd.plistX'` and `'/System/Library/xpc/launchd.plist/x'` to `paths` (line 86), and call
`sd_show` with a 160-byte buffer on a 300-byte path in MAIN.
(d) In `build()`, return an error when the sanitizer build fails, instead of falling back without telling.
Add the four surviving mutants above to MUTANTS.

### L1. Low: replace without a backup when `.sweetorig` exists but is not a regular file (P:498, 510)
P:498 only refuses when the lookup fails. If `X.sweetorig` exists as a directory or a symlink (oerr == 0, ot != VREG), P:510
takes no backup and the original is replaced. `restore` then says `no .sweetorig` (P:473), so the original is lost.
**Fix:** after P:502: `if (serr == 0 && oerr == 0 && ot != VREG) { printf("SWEET DEV: refused %s (.sweetorig is not a regular file)\n", shown); sd_nr++; return; }`.

### L2. Low: on a case-insensitive HFS+ root, ignorable code points still reach the reserved names (P:228-246, 256-262)
HFS+ case-insensitive comparison skips "ignorable" code points such as U+200C/U+200D/U+FEFF (TN1150 FastUnicodeCompare;
guess: the root's case sensitivity is still not recorded). On such a root, `/usr/lib/a.sweetor‌ig` names the same file as
the backup of `/usr/lib/a`. Archive entries that differ only by case (`/usr/local/Foo` and `/usr/local/foo`) also collide,
and the packer accepts both. The second entry then backs up the first entry's bytes as the "original".
**Fix:** refuse any byte >= 0x80 in the path in both twins (`if (c < 0x20 || c >= 0x7f) return "control or non-ASCII byte in path";`
and `any(c < 0x20 or c >= 0x7f ...)` in path_check). Update the `'/usr/lib/\xe9'` corpus expectation. In `collect`, refuse two
paths that are equal after `.lower()`.

### L3. Low: no volume sync after the loop (review 18 #8, still open; P:582)
A panic soon after the hook can lose the renames while the log already said `wrote`. Run 2 of the checklist ("skipped")
is the only check that the data reached the disk. **Fix:** as in review 18 #8:
`if (sd_nw) { int se = VFS_SYNC(vnode_mount(rootvnode), MNT_WAIT, sd_ctx); if (se) printf("SWEET DEV: sync error %d\n", se); }` before `kfree_data`.

### L4. Low: the checklist's fail-on alternative `no SWEETDEV` never matches (docs/AUTOMATION.md:107)
The real line is `SWEET DEV: sweetdev=%s but no SWEETDEV archive ...` (P:549). `SWEET DEV: (...|no SWEETDEV|...)` needs
`no` right after `SWEET DEV: `. The run still FAILs, because the `wrote` expect is missing, but only after `--restart 600`.
**Fix:** use `--fail-on "SWEET DEV: (refused|failed|bad archive|sweetdev=.* but no SWEETDEV|root is not mounted|out of memory)"`.
The PASS rule "reaches the GUI marker" (line 108) is not encoded in the command. Add `--gui` and the owning agent's GUI
`--expect` if that is the criterion.

### L5. Low: comment contradiction in tools/build-xnu-image.sh:36
Line 36 says "--args sweetdev=restore wins (first match)", but line 28 (from the merge) says the LAST key=value wins. The
code is correct either way, because it never adds a second `sweetdev=`. **Fix:** change the comment to "never doubled".

## Verification done by this review (observed)
* `python3 tools/test_mkdevoverlay.py` 19 OK; `test_build_overlay_args.py` 6 OK; `test_devoverlay_kernel.py` 3 OK;
  `tools/validate-repo.py` PASSED; `tools/stack-apply` PASS 117/117; `tools/patch-numbers status` PASS.
* C1 reproduced on the host (`/tmp/rev19/s.c`: `[/privat]`). No warning under `-fno-builtin -ffreestanding -Weverything`.
* 5 extra mutants on the extracted kernel text: allowlist `/usr/lib/`->`/usr/` caught (71). mkmin x2, K_EXACT, sd_show cap
  survived (0).
* Not done: XNU build, hardware.
