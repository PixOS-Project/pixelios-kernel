# Review 20: dev overlay fixes, round 2 (branch dev-overlay, diff f8efa70..HEAD: 40dc8f9, 05928e9, f2db956)

Reviewer: Claude Opus 5.5, 2026-10-24. Adversarial hardware-safety re-review; read-only on code. Sources:
`xnu/patches/0117-sweet-dev-overlay.patch` at HEAD (cited P:line = patch-file line), patched tree
`~/pixelios-artifacts/scratch/stack-BW7Oi8` (T/), spec `docs/reviews/19-dev-overlay-fixes.md`.

## Verdict: SAFE WITH CONDITIONS

C1 is fixed, and the fix fails safe: a truncated name now refuses the entry instead of writing elsewhere. No new defect
can write outside the allowlist or lose an original. Conditions before the first hardware run: findings 1 and 2 (a test-only
fix and a doc/one-token kernel fix). Findings 3-5 can follow.

Count: Critical 0, High 0, Medium 0, Low 3, Info 2.

## Status of the review-19 items

| Item | Status | Evidence |
|---|---|---|
| C1 tmp/orig truncated | **fixed** | [src] P:311-318 `sd_names` uses the passed caps and returns ENAMETOOLONG on `a<0`, `a>=tcap`, `b<0`, `b>=ocap`. P:475 calls it with `sizeof(sd_w.tmp)`/`sizeof(sd_w.orig)` and refuses (`name too long`, `sd_nr++`, return) before any VFS call. Sizes: path <= SD_MAXPATH 1000 (P:120, P:174), tmp needs 1000+9+1 = 1010, orig 1000+10+1 = 1011, buffers are SD_MAXPATH+16 = 1016 (P:453): never truncates in practice, and both fit MAXPATHLEN 1024. Component <= 245 (P:276) + `.sweetorig` (10) = 255 = NAME_MAX; with ASCII-only paths (L2) bytes = UTF-16 units, so the review-19 NFD caveat is gone. No other `sizeof` of a `char *` in sd_one [src: P:456-543 read in full]. |
| M1-new (a) static check + sd_names helper | **partly**: helper and harness fixed; static check scans the wrong region, see finding 1 | [src] test:35-38 prints tmp/orig, small-buffer refusal; test:202 assertIn of the exact call. |
| M1-new (b) mkmin printed and twinned | fixed | [src] test:40 `mk`, test:77-82 Python twin. [obs] mutants `mkmin D`, `mkmin NAME`, my `*mkmin = pl` killed. |
| M1-new (c) corpus + sd_show 160 | fixed | [src] test:103-108, test:36 `sh[160]` exact-size. [obs] `sd_show cap` and my `out[n]=0` removal killed. |
| M1-new (d) no silent non-sanitizer fallback | fixed | [src] test:63-65. |
| M1-new surviving mutants added | fixed | [src] test:176-185. |
| L1 `.sweetorig` not VREG | fixed | [src] P:515-519, after the oerr check P:510 and before any write (P:525). Restore path already required `ot == VREG` (P:485). |
| L2 non-ASCII + case collisions | fixed | [src] kernel P:258, twin mkdevoverlay.py:28, packer mkdevoverlay.py:121-123 (files only; a file/dir case clash is refused by the kernel at P:373-374 "parent is not a directory"). Tests test_mkdevoverlay.py:100, 104-107. |
| L3 VFS_SYNC | fixed, with gaps (findings 2, 4) | [src] P:599-604. Types match `int VFS_SYNC(mount_t, int, vfs_context_t)` (T/bsd/sys/mount.h:856, T/bsd/vfs/kpi_vfs.c:328); MNT_WAIT = 1 (T/bsd/sys/mount.h:395); header via `sys/mount_internal.h` (P:104). `rootvnode` was checked non-NULL and rw at P:580-584 and nothing in between changes it; `sd_ctx` = `vfs_context_kernel()` set at P:590 before the loop. Called before `kfree_data` (P:605); the sync does not touch `buf`. |
| L4 fail-on regex | fixed | [src] docs/AUTOMATION.md:107 `sweetdev=.* but no SWEETDEV` matches P:566; `--gui` exists in xnu (tools/xrun:3, :34). |
| L5 comment | fixed | [src] tools/build-xnu-image.sh:36. |

Sync hang/panic early in boot [src]: `VFS_SYNC` -> `hfs_sync` (T/bsd/sweet_hfs/core/hfs_vfsops.c:2399-2510) is the same
function `sync(2)` reaches through `sync_callback` (T/bsd/vfs/vfs_syscalls.c:3137); it returns 0 during a freeze/changefs and
EROFS on a read-only volume, iterates only cached vnodes, then `hfs_flush(HFS_FLUSH_JOURNAL)`. Each written file already did
`VNOP_FSYNC(MNT_WAIT)` (P:441), which goes through the same journal flush (T/bsd/sweet_hfs/core/hfs_vnops.c:3234-3240), so the
sync adds no new I/O kind on the polled UFS driver. [guess] no hang beyond what P:441 would already show on run 1.

## Findings

### 1. Low: the C1 static check scans the wrong region (tools/test_devoverlay_kernel.py:201)
`split('host-test end')[1]`: the words `host-test end` also occur in the begin-marker comment (P:115), so `[1]` is the text
between P:115 and P:320, i.e. the host-tested pure functions, not `sd_one` (observed: region contains `sd_names(const`, not
`sd_one`). The regex therefore never sees the code where C1 was. Mutant `sd_show(e, shown, sizeof(shown))` at P:462
**survived** (observed). Harm today: none on disk (only the log name would be cut to 7 chars, and run 1's expect would FAIL);
the sd_names call itself is still guarded by the assertIn at test:202 (mutant killed, observed).
**Fix:** test:201: `src = t[t.index('+++ b/bsd/dev/sweet_devoverlay.c'):]; self.assertEqual(src.count('/* host-test end */'), 1); self.assertIsNone(re.search(r'sizeof\((shown|path|tmp|orig)\)', src.split('/* host-test end */')[1]))`.
Add a test that the `sizeof(shown)` mutant of the patch text makes this check fail. (Antipattern AP-30 added.)

### 2. Low: a sync failure does not fail the run, and failed entries are not synced (P:599-604, docs/AUTOMATION.md:104,107)
`SWEET DEV: sync error %d` is a new log line (P:602) that neither the run-1 `--fail-on` (AUTOMATION.md:107) nor the log-line
list (AUTOMATION.md:104) knows; a run with `wrote ...` + `done: 1 written` + `sync error 5` PASSes. Also the sync runs only
when `sd_nw` > 0: an entry that failed after `rename(path, orig)` and a rollback, or after `sd_parents` created
directories, changes the volume but counts in `sd_nr`, so with zero writes nothing is synced.
**Fix:** P:599 `if (sd_nw || sd_nr) {`; AUTOMATION.md:107 fail-on `SWEET DEV: (refused|failed|sync error|bad archive|sweetdev=.* but no SWEETDEV|root is not mounted|out of memory)`; add `sync error N` to the log-line list at :104.

### 3. Low (pre-existing gap): K_NAME exact-prefix mutant survives (P:296)
Mutant `if (n == pl || memchr` -> `if (memchr` **survived** (observed): no corpus path equals the K_NAME prefix, so
`/System/Library/LaunchDaemons/org.pixelios.` (a file named `org.pixelios.` that launchd would read) would be accepted
undetected. **Fix:** add `'/System/Library/LaunchDaemons/org.pixelios.'` to `paths` (test:103-108) and the mutant
`('NAME exact prefix', 'if (n == pl || memchr', 'if (memchr')` to MUTANTS.

### 4. Info: VFS_SYNC without vfs_busy (P:600)
hfs_sync's contract says "always called with the filesystem marked MPBUSY" (T/bsd/sweet_hfs/core/hfs_vfsops.c:2395);
`sync_callback` gets that from `vfs_iterate`. Here there is one caller in bsd_init before `bsd_utaskbootstrap` (P:24-31),
so no unmount can race: safe [src]. Optional hardening: `mount_t mp = vnode_mount(rv); if (vfs_busy(mp, LK_NOWAIT) == 0) { se = VFS_SYNC(mp, MNT_WAIT, sd_ctx); vfs_unbusy(mp); }`.
Side effect [src]: `hfs_hotfilesync` (T/bsd/sweet_hfs/core/hfs_hotfiles.c:1788-1795) may start hot-file recording; the first
`sync(2)` after boot does the same, so nothing new.

### 5. Info: the write path still has no host coverage
`sd_one`, `sd_parents`, `sd_write_tmp` and the L1/L3 blocks are after the marker and never compiled on the host. Mutants
`L1 block removed`, `VFS_SYNC removed` and `sd_names refusal ignored` (patch-text mutants) **survived** (observed). Accepted:
review 19 asked only for sd_names; run 1/2 of the checklist and the restore step are the check. No fix required.

## Verification done by this review (observed, 2026-10-24, worktree HEAD f2db956)
* `python3 -I tools/test_devoverlay_kernel.py` 4 OK; `tools/test_mkdevoverlay.py` 20 OK; `tools/test_build_overlay_args.py` 6 OK.
* `tools/stack-apply`: `RESULT: PASS 117/117 patches (+ 3 stagers)`; `tools/patch-numbers status`: `RESULT: PASS`, next free 0118.
* Mutants (`/tmp/rev20/mut.py`, host gcc + ASan/UBSan): killed 10/12 kernel-text mutants (sd_names `b>ocap`, never-refuse,
  both suffixes; sd_show NUL; `>0x7f`, `>=0x80`; component 246; suffix compare; mkmin `pl`). Survived: `a>tcap`
  (equivalent while tcap <= ocap, P:475 and test:37 use equal caps) and K_NAME exact prefix (finding 3). Patch-text mutants:
  `sizeof(tmp)` in the sd_names call killed; `sizeof(shown)` in sd_one survived (finding 1); L1/VFS_SYNC/refusal removal
  survived (finding 5).
* Not done: XNU build, hardware.
