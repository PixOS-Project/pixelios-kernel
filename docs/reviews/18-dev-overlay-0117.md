# Review 18: dev overlay, xnu patch 0117 + tools (branch dev-overlay, 036d265 + ac0b940)

Reviewer: Claude Opus 5.5, 2026-10-24. Adversarial review, read-only on code. Sources: patch
`xnu/patches/0117-sweet-dev-overlay.patch` (P), the patched tree from `tools/stack-apply --keep`
(117/117 + 3 stagers apply, observed; tree `~/pixelios-artifacts/scratch/stack-BW7Oi8`, cited as T/), tools at ac0b940.

## Verdict: SAFE WITH CONDITIONS

No finding lets an archive write outside `userdata` or outside the allowlisted names *by itself*, and the
structure parser is overflow-safe (checked by a differential fuzz of the real kernel code, see "Verification").
Conditions before the first hardware run: fix 1, 2 and 3 (small), and use the first-run plan below (not the
one in `docs/AUTOMATION.md`). Findings 4-12 can follow.

Count: Critical 0, High 0, Medium 4, Low 8.

## What was verified (no finding)

* Structure parser `sd_next` (P:172-198): `off <= len` is kept as an invariant; `plen <= len-off`, `size <= len-off`
  are compared before the additions, the alignments cannot wrap (`len` is `mdSize << 12`, < 2^44, T/bsd/dev/memdev.c:766-767).
  Two passes: the whole archive is walked before the first write (P:501-506); `count <= 4096` (P:497). Read from source.
* crc32: reflected poly 0xedb88320, init ~0, final ~ (P:144-161) = zlib.crc32; table guard `sd_crc_tab[1]` is non-zero
  after init (0x77073096). Observed: fuzz agreement on 3000 archives, and a poly mutant gives 1334 mismatches.
* RAM disk range: IOFindBSDRoot builds the memdev with `ml_static_ptovirt(phys) >> 12`, `phys=0` (T/iokit/bsddev/IOKitBSDInit.cpp:752-766),
  so `mdevgetrange` returns a kernel-virtual static-map address (the same mapping the kernel reads its ADT through, which
  mkpayload places right after the RAM disk, tools/mkpayload.py:171-180). Missing devices return -1 before touching
  memory (T/bsd/dev/memdev.c:758-765). The only other memdev source, boot-arg `pmdX=` (physical!), is compiled only
  with `CONFIG_MEMDEV_INSECURE` (T/bsd/dev/memdev.c:490), which no `bsd/conf` file sets (grep, observed). RAM disk base is
  16 KiB aligned and its size a 16 KiB multiple (tools/mkpayload.py:174-176), so the `>> 12` loses nothing.
* `ARM64_BOARD_CONFIG_SWEET` guard on all three hunks; with `sweetdev` absent `PE_parse_boot_argn` fails and the function
  returns before any output (P:473-476). A bare `sweetdev` or `sweetdev=0` also returns silently (P:477-480).
* Call position (T/bsd/kern/bsd_init.c:1043-1053): after `set_rootvnode` (915, sets `kernproc` `fd_cdir`), devfs mount (1001)
  and `siginit`; before `bsd_utaskbootstrap`, so no user process exists (no TOCTOU on the paths). `vfs_context_kernel()` is valid.
* UFS driver: `sweet_ufs_strategy` is synchronous in the caller's thread under `sweet_ufs_lock`, polls/sleeps with a
  deadline on a timer, needs no other thread (T/bsd/dev/sweet_ufs.c:218-247, 430-486); writes are refused unless `ufsrw=1`,
  and open(FWRITE) fails without it (T/bsd/dev/sweet_ufs.c:420), so a read-only mount is caught by `vfs_isrdonly` (P:508).
  No recursion: the hook does not hold `sweet_ufs_lock`. The log thread takes the same mutex (sweet_ufs.c:942) and just waits.
* Wrappers: `renameat_internal(ctx, fromfd, from, tofd, to, segflg, uflags)` and `mkdir1at(ctx, path, vap, fd, segflg)` match
  T/bsd/vfs/vfs_syscalls.c:8929-8930 and 9719-9720. The only semantic gap is the umask (`mkdir` syscall applies `fd_cmask`):
  irrelevant for 0755. `unlink1` signature matches T/bsd/vfs/vfs_syscalls.c:6267. Rename does not follow the last component.
* Symlinks: every prefix `/a`, `/a/b`, ... is looked up with `VNODE_LOOKUP_NOFOLLOW` as its own last component and must be
  VDIR (P:299-327), so every component of the final path is a checked directory; target must be absent or VREG (P:433);
  `vnode_open(... O_NOFOLLOW)` returns ELOOP on a symlink leaf (T/bsd/vfs/vfs_subr.c:7953-7954). Hard-linked *targets* are
  replaced by rename, never written through. Mode is capped at 0777 (no setuid/setgid/sticky, P:400).
* `IO_NODELOCKED` (P:371) is ignored by HFS (`T/bsd/sweet_hfs/core/hfs_readwrite.c`, no use); harmless.
* Boot-arg budget: ` sweetdev=1` adds 11 bytes, still checked by `mkpayload.check_cmdline` (tools/mkpayload.py:143-148).
  `--args "sweetdev=restore"` wins and is not doubled (observed by test). `DIR` with spaces passes through xrun and
  build-xnu-image intact (observed: `${overlay:+--overlay "$overlay"}` gives two words `[--overlay][/tmp/a b]`).

## Findings

### 1. Medium: a pre-existing `PATH.sweettmp` hard link is written through, and gets chmod/chown (sweet_devoverlay.c P:362-383)
`vnode_open(tmp, O_CREAT|FWRITE|O_NOFOLLOW, 0600)` has no `O_EXCL`, so an existing `X.sweettmp` is opened, truncated
(`vnode_setsize`, return value ignored, P:366) and overwritten, then `vnode_setattr` sets the archive's mode/uid/gid on it.
`O_NOFOLLOW` stops a symlink but not a hard link. Scenario: anything running as root on the phone (a dev tool, a test
daemon) leaves `/usr/lib/libx.dylib.sweettmp` as a hard link to `/System/Library/xpc/launchd.plist` or any other file on
the volume; the next overlay of `/usr/lib/libx.dylib` rewrites that file with the dylib bytes and mode 0755: a write outside
the allowlist, which the header (P:84-86) says cannot happen. The rename that follows only moves the name.
If `vnode_setsize` fails, a shorter new file keeps the old tail bytes and is installed (crc is only checked on the archive).
**Fix:** before `vnode_open`, `(void)sd_rm(tmp);` (ENOENT is fine), then open with `O_CREAT | O_EXCL | FWRITE | O_NOFOLLOW`;
drop the `vnode_setsize` call (a new file is empty). Optional hardening: after writing, re-read the tmp file and compare
with `e->data` via `sd_same(tmp, e, buf)` before the rename (catches UFS write corruption, costs one read).

### 2. Medium: 3.2 KiB of path buffers on a 16 KiB kernel stack, above the deepest call path (P:395)
`sd_one` has `shown[160] + path[1001] + tmp[1016] + orig[1016]` on the stack and calls rename/mkdir/open, which go
namei -> HFS rename/create -> journal -> buf -> `sweet_ufs_strategy` -> polled command. `KERNEL_STACK_SIZE` is
4*4096 on a non-KASAN, non-DEBUG build (T/osfmk/mach/arm/vm_param.h:327); `sweet_devoverlay_run` adds `struct sd_ent`, and
`sd_write_tmp`/`sweet_vfs_mkdir` each add a `struct vnode_attr`. XNU itself keeps MAXPATHLEN buffers off the stack for
this reason (`zalloc(ZV_NAMEI)` in `bsd_rooted_ramdisk`, T/bsd/kern/bsd_init.c:447). A stack overflow is a panic in the
middle of an HFS transaction on every boot with `sweetdev=1`. (Guess on the actual depth; not measured.)
**Fix:** one static work area (the hook is single-threaded and runs once):
`static struct { char shown[160], path[SD_MAXPATH + 1], tmp[SD_MAXPATH + 16], orig[SD_MAXPATH + 16]; } sd_w;`
and use `sd_w.path` etc. in `sd_one` (or allocate it with `kalloc_data(..., Z_WAITOK | Z_ZERO)` next to `buf`).

### 3. Medium: the documented first-run checklist replaces an existing binary on another agent's root (docs/AUTOMATION.md, ac0b940 hunk "First-run checklist" item 3)
Item 3 says "overlay = one new `private/var/dev/hello` + one replaced test binary". The installed root belongs to the GPU agent;
a replacement on the very first run of never-run kernel code is avoidable risk (finding 1 and 5 show the replace path has
edge cases). **Fix:** change item 3 to "one NEW file `private/var/dev/hello` only, nothing replaced" and item 4 to
"same run again, expect `skipped`"; move "replace a test binary under `/usr/local/`" to a third run after both pass.
Use the plan in "Hardware first-run plan" below.

### 4. Medium: the kernel C is never compiled or run on the host; the Python twin is a re-implementation
`tools/test_mkdevoverlay.py` only tests `mkdevoverlay.parse/path_check`, so any bug in `sd_next`/`sd_path_check`/`sd_crc32`
reaches hardware untested. Mutating the Python twin (13 mutants, observed) left 8 alive: bounds checks
(`off > n`, `plen > n-off`, `size > n-off`, padding check), `count > MAX_COUNT`, the `0x7f` control byte,
the `& 0o777` mode mask in `collect`, and the D-kind "prefix only" clause (that one is equivalent: `/usr/lib/` already fails
"empty component"). The bounds mutants survive because the Python checks are redundant with each other (slicing never raises).
**Fix:** add `tools/test_devoverlay_kernel.py` that extracts the pure functions from the patch (`rd16`..`sd_path_check`,
between two marker comments `/* host-test begin */` / `/* host-test end */` added to sweet_devoverlay.c), compiles them with
`cc -fsanitize=address,undefined` and a 20-line `main` (structure walk + per-entry `path_check`/crc, as in
`/tmp/rev18/build.sh`), and compares with `mkdevoverlay.parse/path_check` on ~2000 seeded random mutations/truncations of
packed archives (as `/tmp/rev18/fuzz.py`). Add Python cases: path with `\x7f` and `\x1f`, a 0o4755 file packed as 0o755,
a FIFO in DIR (finding 9), `count` = 4097 with 4097 real entries.

### 5. Low: original replaced WITHOUT a backup when `.sweetorig` cannot be looked up for a reason other than ENOENT (P:416, 450)
The backup is taken only if `oerr == ENOENT`; any other error (`ENAMETOOLONG`, `EIO`, `ENOTDIR`) skips the backup and the
replace goes on. Concrete: a file name of 246 bytes: `name.sweettmp` is 255 (allowed), `name.sweetorig` is 256 ->
`ENAMETOOLONG`; the existing file is replaced and the log says `wrote` without "(original kept)"; `restore` cannot undo it.
**Fix:** after P:442 add `if (serr == 0 && oerr != 0 && oerr != ENOENT) { printf("SWEET DEV: refused %s (cannot check .sweetorig, error %d)\n", shown, oerr); sd_nr++; return; }`;
in `path_check` (both twins) refuse a last component longer than 245 bytes.

### 6. Low: failed rollback leaves the target missing, logged only as "write error" (P:457-462)
If `tmp -> path` fails after `path -> orig` succeeded, the rollback `orig -> path` result is discarded. If it also fails,
the file exists only as `.sweetorig` and the log does not say so. **Fix:** `int rb = moved ? sweet_vfs_rename(sd_ctx, orig, path) : 0;`
and print `"SWEET DEV: failed %s write error %d%s\n", ..., rb ? " ORIGINAL ONLY AT .sweetorig (rollback failed)" : ""`.

### 7. Low: reserved-suffix and allowlist checks are case-sensitive; collisions if the root is case-insensitive HFS+ (P:250, 253-256)
`tools/check-ios-root.py:8` accepts HFS+ and HFSX, so whether the root is case-insensitive is not pinned (guess: unknown).
On a case-insensitive volume `/usr/lib/a.SweetOrig` passes the suffix check but names the same file as the backup of
`/usr/lib/a`, and overwrites it (the later `restore` then restores the wrong bytes). No allowlist escape: a different-case
spelling of a prefix is refused, which is the safe side; non-ASCII is below an ASCII prefix, so NFD normalisation cannot
move a name out of it. **Fix:** compare the two suffixes case-insensitively (`strncasecmp` on the last 9/10 bytes) in the
kernel and `b.lower().endswith(...)` in `path_check`; record the volume signature (H+ vs HX) of the flashed root in research 41.

### 8. Low: no volume sync after the loop (P:526-527)
Each file is `VNOP_FSYNC`ed, but the renames are only in the HFS journal in memory. A panic soon after (common in dev boots)
can lose the renames (consistent, but `wrote` was logged for a change that is not on disk), and a crash between the
two renames leaves only `.sweetorig`. **Fix:** before `kfree_data`, `if (sd_nw) (void)VFS_SYNC(vnode_mount(rootvnode), MNT_WAIT, sd_ctx);`
(or `vfs_sync`-style `sync_internal`), and log its error.

### 9. Low: packer opens non-regular files; a FIFO blocks it forever (tools/mkdevoverlay.py `collect`)
`os.walk` lists FIFOs, sockets and device nodes under `files`; `open(full,'rb').read()` on a FIFO blocks (unbounded tool
runtime, AGENTS.md tool rules). **Fix:** `st = os.lstat(full); if not stat.S_ISREG(st.st_mode): sys.exit('mkdevoverlay: not a regular file %s' % full)`
and use `st.st_mode & 0o777`.

### 10. Low: the 64 MiB packer cap is above what the 64 MiB AVB boot image can hold (tools/mkdevoverlay.py MAX_TOTAL)
The boot image is AVB-signed into a 64 MiB partition size and today's image is ~16 MiB (research 41, table "Size budget",
which cites shim/build.sh:396-399; the `--partition_size ${AVB_PARTITION_SIZE:-67108864}` lines are actually shim/build.sh:382,386 at ac0b940: fix that cite), so ~45 MiB is the real ceiling, and the RAM placement vs the shim loader's
overlap check (shim/loader.c:95-96) is still unknown U1. The build fails late (avbtool) instead of in the packer.
**Fix:** `MAX_TOTAL = 20 << 20` until the U1 size ramp is observed (AUTOMATION.md item 5 already says "nothing over ~20 MiB"),
with a `--max-mib` override.

### 11. Low: no way to remove a file the overlay created; nothing records what a run wrote
`restore` only renames `.sweetorig` back; a new file (e.g. `/private/var/dev/hello`) and created directories stay until a
re-flash. xrun does not keep the archive in the run dir, so a later reader cannot tell what went onto the root.
**Fix:** in `tools/xrun`, `[ -z "$overlay" ] || { python3 -I tools/mkdevoverlay.py "$overlay" "$D/devoverlay.bin" && find "$overlay" -type f | sort > "$D/devoverlay.list"; }`
(or have build-xnu-image `--overlay` also copy `$ov` next to `--out`). A delete entry type is optional; say in AUTOMATION.md that
added files are not removed by `restore`.

### 12. Low: `--owner` accepts any uid for system library paths (tools/mkdevoverlay.py `main`)
`--owner 501:501` makes `/usr/lib/X.dylib` writable by `mobile`, i.e. a later non-root process can change a library loaded
by root daemons. Dev-only, but silent. **Fix:** print a warning when uid != 0 and a path is under `/usr/lib/` or `/System/`.

## Python reader vs kernel: differences (read from source + observed fuzz)
Same order and same conditions for magic, version, count, entry header, pathlen, size, padding, components, suffixes,
allowlist (fuzz: 0 mismatches over 3000 archives with ASan/UBSan; kernel mutants for the control byte, K_NAME `/` and the
crc poly were caught). Differences: (a) Python flags crc for every entry; the kernel checks crc (and mode) only in copy
mode, not in `restore` (P:400-403), by design; (b) the mode `> 0777` check is not in `parse`/`path_check` (only the packer's
`& 0o777` enforces it; finding 4 asks for a test); (c) the kernel additionally refuses by filesystem state
(parent not a dir, target not VREG), which has no twin.

## common-tools hunks (tools/common-files.txt)
Only `tools/xrun` is in `tools/common-files.txt` (line 12, observed); `tools/build-xnu-image.sh` is NOT listed, so it stays
on this branch. xrun hunks to send to common-tools: (1) `overlay=""` added to the defaults line (tools/xrun:29); (2) the case
arm `--overlay) overlay=$2; shift 2 ;;` (tools/xrun:35); (3) `${overlay:+--overlay "$overlay"}` in the build-xnu-image call
(tools/xrun:68). Safe for trees without 0117: without `--overlay` all three are inert; with `--overlay` on a tree whose
build-xnu-image.sh lacks the option, it exits 2 "unknown option", and xrun dies in the build step before the phone is
touched. A kernel without 0117 that gets `sweetdev=1` ignores an unknown boot-arg; the `RAMDisk` node still becomes an
unused md0 (T/iokit/bsddev/IOKitBSDInit.cpp:763), `rd=ufsroot0` keeps the UFS root (research 41 U2: not yet observed).
`test_xrun_passes_overlay_through` greps xrun and must travel with it (or move to the common-tools tests).

## Hardware first-run plan (replaces AUTOMATION.md item 3)
Preconditions: fixes 1-3 applied; host tests incl. finding 4 pass; the phone's flashed root has finished its first
(DataMigration) boot; `tools/phone-lock` held by this agent, and the GPU agent told that its root gets one new file.
1. `DIR/private/var/dev/hello` = 6 bytes `hello\n`, nothing else. `tools/xrun --kernel K --overlay DIR --label dev1
   --expect "SWEET DEV: wrote /private/var/dev/hello 6 bytes" --expect "SWEET DEV: done: 1 written, 0 skipped, 0 refused/failed"
   --fail-on "SWEET DEV: (refused|failed|bad archive|no SWEETDEV|root is not mounted|out of memory)" --restart 600`.
   PASS = both expects, no fail-on, and the run then reaches the usual GUI marker of the GPU agent's runs (SpringBoard line)
   as before; the ADT/loader lines must not contain `ramdisk overlaps kernel memory`.
2. Same image again: expect `SWEET DEV: skipped /private/var/dev/hello 6 bytes (identical)` and `done: 0 written, 1 skipped`.
   This proves the read-back path and that the first write reached the disk.
3. Only then a replacement, under `/usr/local/` (not `/usr/lib/`), and a `sweetdev=restore` run for it.
Abort/recovery: any `panic`/`Kernel trap` during step 1/2: boot the previous image WITHOUT `--overlay` (the hook is inert,
HFS replays its journal); if that boot also fails to reach the GUI, the root is damaged: stop, tell the GPU agent, and
re-flash its image with `tools/fflash-userdata.sh IMG --yes-erase-userdata` (25-40 min incl. migration) after it confirms
which image. Never run `fsck`/writes on the root from the host.

## Verification done by this review
* `tools/stack-apply --keep`: `RESULT: PASS 117/117 patches (+ 3 stagers)` (observed).
* `python3 tools/test_mkdevoverlay.py` 14 OK, `python3 tools/test_build_overlay_args.py` 6 OK, `python3 tools/validate-repo.py` PASS (observed).
* Kernel functions `rd16..sd_path_check` copied verbatim from the patched tree, compiled with ASan/UBSan, differential
  fuzz vs `mkdevoverlay.parse/path_check`: 0 mismatches / 3000; mutants: control byte 2, K_NAME 2, crc poly 1334 mismatches;
  `size > len-off` +16 mutant 0 (equivalent: the padding check `off > len` catches the same archives). Scripts: `/tmp/rev18/`.
* Python-twin mutation run: 5 of 13 caught (finding 4).
* Not done: XNU build of the patch (no build allowed in this brief), any hardware.
