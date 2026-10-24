# 41: changing iOS userland files on the phone without re-flashing userdata

Date 2026-10-24. Research only: nothing run, nothing built, phone not touched. Tags: **[obs]** observed on hardware / in repo logs, **[src file:line]** read from source
(opened this session), **[guess]** not verified. Paths: K = this kernel clone (`~/.t3/worktrees/pixelios-d2/kernel`), S = super repo (`~/.t3/projects/pixelios`),
U = userland clone `S/pixos-project/pixelios-userland`, GU = GPU agent's userland worktree `~/.t3/worktrees/pixelios-gpu/userland`, X = pristine xnu `~/pixelios-artifacts/xnu-pristine`.
Number 41 is free: `git ls-tree` on `wip/gpu-accel` and `wip/w3a-wlan-link` shows no `docs/research/41-*` [obs, checked].

## 0. The problem and the fact that frames it

* Today every userland file change (test client, libsweetgl, launchd job) means: rebuild a root copy with `GU/tools/overlay-ios-root.sh BASE.img OUT.img ...` (qemu guest + fsck + check-ios-root, then
  `cp --sparse` of a 16 GiB image) [src GU/tools/overlay-ios-root.sh:1-2,57-73], flash it (467 s [obs: PLAN-BOOT-SPEED 1.3, S/docs/PLAN-BOOT-SPEED.md:47]), and sit through the first-boot DataMigration
  (>= 1050 s [obs: demo2, PLAN-BOOT-SPEED:25]).
* A boot that is NOT the first after a flash has no migration: `migrationpluginwrapper` exec count 0 in about4 and demo1b, 4 in demo2 [obs: PLAN-BOOT-SPEED:35]; pre-GUI path ~129 s from
  `fastboot boot` [obs: PLAN-BOOT-SPEED:1.1]; B1 early stop + remote reset gets the phone back in fastboot ~95 s after the stop (run `bl1-remote3`, ~200 s per run) [obs: PLAN-BOOT-SPEED:86].
  So the cost to remove is the flash + migration; the boot cycle itself (~3.5-5 min) stays.
* The hard rules allow writing `userdata` and only the iOS root may replace it (AGENTS.md rules 1 and 3). Writing FILES inside the already-flashed HFS+ root through the mounted filesystem
  is writing inside `userdata`; it is not a new partition write. The kernel already mounts that root read-write: `ufsrw=1 rootrw=1` [src K/tools/build-xnu-image.sh:15,28; K/tools/xrun:28 (`rw="--rw"` default)],
  and `sweet_ufs` refuses any write outside the named partition (bounds checked right before each WRITE(10)) [src K/xnu/patches/0030-sweet-ufs-writes.patch:17-20,57-70, partition name default
  `userdata`: 0028-sweet-ufs-root-device.patch:507].

## 1. Building blocks that already exist (all checked)

| Block | Fact |
|---|---|
| Payload carries a RAM disk | `tools/mkpayload.py KERNEL ADT OUT --ramdisk IMAGE` appends IMAGE as one more segment right after the kernel, moves `kernel_end` past it and adds `/chosen/memory-map RAMDisk = <phys,size>` to the ADT [src K/tools/mkpayload.py:23-30,171-180]. The shim copies every payload segment [src K/shim/loader.c:100-103]. |
| `md0` appears with a ramdisk even if root is elsewhere | `IOFindBSDRoot` builds the memory device from the `RAMDisk` property under `if (!didRam)`, before and independent of the `rd=` test [src X/iokit/bsddev/IOKitBSDInit.cpp:746-764; the `rd=md0` check follows]. [obs] a `rd=md0` HFS root booted on the phone with this plumbing (boot 2026-10-15, `BSD root: md0 ... hfs: mounted` [S/NOTES.md:475]; 8 MB image). Not observed: ramdisk + `rd=ufsroot0` together (the combination is [guess] fine by the source above). |
| `build-xnu-image.sh` has no ramdisk flag | It calls `mkpayload.py KERNEL ADT P --board bluejay --cmdline ARGS` without `--ramdisk` [src K/tools/build-xnu-image.sh:48]; `--args` only adds boot-args [src :34]. A new `--ramdisk FILE` flag is needed. |
| Size budget | boot image is AVB-signed into a 64 MiB partition size (`AVB_PARTITION_SIZE` default 67108864) [src K/shim/build.sh:396-399]; today's payload ~15.8 MiB [src task brief; `shim/out/shim-boot.img` = 16,117,760 bytes, obs `ls`]; libsweetgl.dylib is 21.8 MB [src GU/tools/sweetgl-eagl/INJECT.md s.2]. So ~25-45 MiB of overlay fits on paper. **Unknown:** the largest ramdisk ABL accepts for `fastboot boot` and whether it overlaps the kernel RAM check [src K/shim/loader.c:95-96] (see U1). |
| Kernel can write root files | In-kernel file creation/writing is normal XNU: `vnode_open(path, O_CREAT|FWRITE, mode, ...)` is used by `kern_shutdown.c:355` (`PROC_SHUTDOWN_LOG`) and `kdebug.c:2927` [src X/bsd/kern/kern_shutdown.c:355; X/bsd/vfs/vfs_subr.c:7443], `vn_rdwr` [src X/bsd/vfs/vfs_vnops.c:911], `vnode_setattr` for mode/owner [src X/bsd/vfs/kpi_vfs.c:2916]. |
| A hook point after the root mount, before launchd | `vfs_mountroot()` at `bsd_init.c:870`, `bsd_utaskbootstrap()` (starts launchd) at `bsd_init.c:1024` [src X/bsd/kern/bsd_init.c:869-870,1023-1024]. Patches 0026/0029/0035 already touch `bsd_init.c` [obs grep of K/xnu/patches]. |
| Files on the root fs are platform binaries; elsewhere not | The stand-in AMFI marks every validly signed vnode as `CS_PLATFORM_BINARY` only when `vfs_flags(vnode_mount(vp)) & MNT_ROOTFS` [src K/xnu/patches/0035-sweet-amfi-entitlements-platform-binaries.patch:240-253; 0036-sweet-amfi-cs-signed.patch:22-25]. So a dylib/binary on a second mount (md0 mounted at /private/var/dev, or a nullfs/bindfs over it) is NOT a platform binary [src]; whether that breaks the Mali client / backboardd injection is [guess: possibly, root-only gate 0105 and entitlements]. This is why "copy into the root fs" beats "mount an overlay". |
| Injection without file changes | `sweetenv` (patch 0110, only on `wip/gpu-accel`, `git show wip/gpu-accel:xnu/patches/0110-sweet-exec-env.patch`): boot-arg `sweetenv=NAME:VAR=VAL[,VAR=VAL][;NAME2:...]` appends env strings to every exec whose basename is NAME (512-byte boot-arg, 128-byte entries). Its comment: launchd drops `DYLD_*` from job EnvironmentVariables (gputest11: backboardd printed nothing from the injected libsweetgl) [src 0110 header comment; obs gputest11 as quoted there]. 0106 (same branch) answers dyld's `AMFI` op 0x5a with flags 0x7b so `DYLD_INSERT_LIBRARIES` is honoured [src 0106 hunk; "never run" per S/docs/HANDOFF.md:236; the INJECT.md s.3 blocker]. |
| USB network | Kernel interface `ue0` is 192.168.7.2/24 (host 192.168.7.1) [src K/xnu/patches/0073-sweet-usb-ecm.patch:327,386], live log to 192.168.7.1:5140 (0075), remote reset listener `sweetremote=1` on UDP 5141 [src K/xnu/patches/0100-sweet-remote-reset.patch hunk "sweetremote=1"]. Host -> phone UDP works [obs: run `bl1-remote3`, PLAN-BOOT-SPEED:86]. Host side address needs one sudo line or the udev rule [src K/tools/sweet-usbnet.sh:11-13,22-26]. |
| Root-side tooling | `/sbin/mount`, `mount_hfs` exist and ran [obs: S/NOTES.md:436-437]; `bindfs` and `nullfs` are in the kernel config [src K/xnu/patches/0037-sweet-config-bindfs.patch:7-8]. Jobs live in the combined cache `System/Library/xpc/launchd.plist`, keyed by plist path [src GU/tools/overlay-ios-root.sh:10,39,45-49]. Test binaries are Mach-Os built by macOS CI with the iPhoneOS SDK and ad-hoc signed [src GU/tools/sweetmali-test/build.sh:6-11]. |
| Job tweaks | `sweet-root-tweaks.py` is not in U (only its patch `U/upstream-patches/tools__sweet-root-tweaks.py.patch`); the file is `S/pixos-project/upstream/tools/sweet-root-tweaks.py` (153 lines, `launchd()` edits the cache at line 57-71) [src]. |

## 2. Options

### (a) payload-embedded overlay, copied into the root fs by a kernel hook before launchd  [recommended]
* **How:** a host script `tools/mkdevoverlay.py DIR OUT.bin` packs `DIR` (relative path, mode, uid, gid, bytes; header magic `SWEETDEV`, version, count, per-file crc32 or size) into a flat archive.
  `tools/build-xnu-image.sh --overlay DIR` passes it as `mkpayload.py --ramdisk OUT.bin` and adds `sweetdev=1` to the boot-args (cmdline budget 607 bytes [src K/tools/mkpayload.py:44-47,
  `BOOT_LINE_LENGTH = 608`]). New patch `sweet_devoverlay.c` (name via `tools/patch-numbers next`): called from `bsd_init` just before `bsd_utaskbootstrap()` [src X/bsd/kern/bsd_init.c:1023-1024],
  it finds the `RAMDisk` physical range from the ADT the same way IOKitBSDInit does [src X/iokit/bsddev/IOKitBSDInit.cpp:753-764] (or reads `/dev/md0` after `mdevadd`), and for each entry:
  path allowlist check, compare with the existing file (size + memcmp via `vn_rdwr`), skip if equal, else write `path.tmp`, `vnode_setattr` mode/owner, rename over the target. First overwrite of an existing
  file saves `path.sweetorig` once. Every action is a `printf` line (`SWEET DEV: wrote /usr/lib/libsweetgl.dylib 21801234 bytes`) so the UDP log shows it.
* **Needs:** one kernel patch (M), `mkpayload`/`build-xnu-image.sh` flag, the packer, a host test. **No root change, no flash**: the existing flashed root (ios-root3 / ios-root3-gpu4 style) is enough for
  replacing files that already exist or are created in existing dirs. New launchd jobs: ship a replacement `System/Library/xpc/launchd.plist` in the overlay (generated by the existing guest "get" step of
  `overlay-ios-root.sh`, which reads the plist from BASE [src GU/tools/overlay-ios-root.sh:34-37]).
* **Hard rules:** writes only inside the mounted `userdata` HFS+ root (rule 1, 3 hold; no shim UFS write path added, the `AGENTS.md` "Do not add a UFS WRITE path to the shim" is untouched: this is the XNU driver that is already RW). Payload RAM only otherwise. The patch writes hardware-adjacent storage -> reviewer tier per AGENTS rule 6 (Opus review).
* **Clean first boot:** inert unless the payload carries the ramdisk segment and `sweetdev=1`; a normal `xrun` image has neither, so the first boot after a fresh flash is unchanged. Do not use an overlay payload for the
  migration boot [guess: harmless, but the migration is 14+ min and wants a quiet root]. Risk: a bad overlay (broken launchd.plist) leaves a root that does not boot -> `sweetdev=restore` (copy `.sweetorig` back)
  and finally a reflash (still only userdata). Kernel panics in the hook are fatal for that boot only.
* **Iteration time:** pack (seconds) + image build (`shim/build.sh`, tens of seconds [guess]) + `xrun` boot ~129 s to GUI [obs] + copy time [guess: tens of seconds for 22 MB at the UFS bounce-buffer rate; **unmeasured**, log `elapsed` in the hook]
  + reset back ~95 s [obs]. About 4-6 min vs 25-40 min. **Size M.**

### (b) host-pushed over the USB network (userland fetcher or kernel receiver)
* **Userland fetcher:** a launchd job fetching a tarball from 192.168.7.1. **What the root contains is not established:** no repo doc lists `curl`, `nc`, `tar`, `sh` in the root (grep of `S/docs`, `S/NOTES.md`,
  `S/pixos-project/upstream/tools`, `U/tools` found none; the only shells mentioned are inside the build guest, `U/tools/build-ios-root-rootless.sh:7-19`). Stock iOS ships none of them [guess]. So a fetcher means a new
  CI-built, ad-hoc-signed Mach-O (`devsync`, same recipe as sweetmali-test) plus a one-time root change (the job plist + binary) = one flash, M-L. Userland TCP on `ue0` is [guess: works, only the kernel UDP
  paths are observed]; the sandbox is a permissive stand-in [src K/xnu/patches/0035 title; GU INJECT.md s.3 quotes "permissive"], the job runs as root.
* **Kernel receiver (extend the 0100 UDP listener):** host sends `SWEET-PUT path mode len` + numbered chunks, phone acks; same write routine as (a). No userland tool, no flash, and files can land while iOS runs
  (test jobs with `StartInterval` pick up new binaries without a reboot [src GU/tools/overlay-ios-root.sh:46-49]). Costs: a reliable-transfer protocol over UDP (L), more code in a network thread, an
  unauthenticated file-write port (must be behind `sweetremote=1`-style opt-in, and path allowlist). Replacing a running dylib needs a process restart anyway.
* **Hard rules:** same as (a) (root fs only). **Clean first boot:** opt-in boot-arg. **Time:** push seconds; process/job restart seconds to minutes; reboot not required for job-level changes. **Size L.** Do (a) first; (b) kernel receiver reuses (a)'s writer.

### (c) change single files from the host while the phone is in fastboot
* **No.** The shim has no UFS write path by rule (AGENTS.md "Do not"); `fastboot flash userdata` replaces the whole partition. A sparse image with only the changed blocks (`tools/sparsediff.py` RAW + DONT_CARE chunks
  [src K/tools/sparsediff.py:1-12,40-55]) is safe only if the phone's HFS+ is byte-identical to OLD. It is not after any boot: the phone rewrites catalog/bitmap/journal/volume header on every run (the premise note in
  PLAN-BOOT-SPEED B3 says exactly this: "its OLD-image premise is false here, the phone rewrites userdata every run" [src S/docs/PLAN-BOOT-SPEED.md:63]). Overwriting those blocks with the overlay image's versions
  would desynchronise the allocation bitmap/catalog from files created since (migration data, logs): silent corruption [src reasoning from the HFS+ layout; guess on exact failure]. Reading the partition back to build a
  correct diff needs a UFS read path the host does not have (the shim `--udump` is a probe dump, 64 MiB max [src K/shim/build.sh:71]). Also whether the bootloader accepts a sparse `flash userdata` as "write
  only these blocks" or formats first is [guess: unknown]. Rejected. (B3 in PLAN-BOOT-SPEED is still valid but for FULL fresh flashes: FILL chunks, ~250 s saved per flash.)

### (d) second small partition / `boot_b`-style slots
* Forbidden by AGENTS.md rule 1 (never write boot*, GPT, etc.) unless the user lifts it. Not designed.

### (e) things the repo already has
1. **`sweetenv` (0110) + AMFI dyld policy (0106)**: injection and per-process parameters become boot-args, i.e. part of the payload that every `xrun` rebuilds anyway: `--args "sweetenv=backboardd:DYLD_INSERT_LIBRARIES=/usr/lib/libsweetgl.dylib;sweetgl-test:SWEETGL_MODE=x"`.
   That removes the launchd.plist `EnvironmentVariables` edit of `INJECT.md` s.6 (which 0110 says launchd ignores for DYLD_* anyway [src 0110 header]) and any flash for flags/modes. It does NOT deliver files; it only
   needs a path that exists. Also enables `DYLD_LIBRARY_PATH` / `DYLD_FRAMEWORK_PATH` overrides for a dev dir [guess: dyld honours them with flags 0x7b "path variables" per 0106 comment]. Both patches live only on `wip/gpu-accel`;
   they never ran together on hardware as far as the repo says ("never run" S/docs/HANDOFF.md:236; 0111 observed per PLAN-BOOT-SPEED:85 kernel 3da8118 with 0100-0111 [obs]).
2. **`SWEETGL_DISABLE=1` / `SWEETGL_FORCE=1`** env of the dylib [src GU INJECT.md s.5] can be set through `sweetenv` too.
3. **Mount-based overlay (nullfs/bindfs/md0 as HFS+)**: possible (`bindfs` is built [src 0037:8], `mount_hfs` exists [obs NOTES.md:436]) but files lose root-fs platform status (see table) and it needs a root change for the mount job: worse than (a).
4. hackios's `kern.exec_inject` sysctl (runtime-editable injection list) [src S/docs/research/22 s.1.3 as quoted in GU INJECT.md s.1]: not needed once `sweetenv` exists.
5. No existing doc or tool proposal for a dev overlay: `grep -rn overlay` in `S/docs/GOALS*.md`, `ROADMAP.md` found only `overlay-ios-root.sh` itself [obs grep]. Check `docs/tool-proposals/` before filing the proposal (TP rules).

## 3. Ranking

| Rank | Option | Size | Flash needed | Why |
|---|---|---|---|---|
| 1 | (a) payload ramdisk + kernel copy-in before launchd, with `sweetenv` for env | M | none | deterministic order before launchd, root-fs platform status kept, no new userland binary, only boot-arg/payload per run |
| 2 | (b) kernel UDP receiver (live push, no reboot for job-level changes) | L | none | built on (a)'s writer; do after (a) proved the write path |
| 3 | (b) userland `devsync` fetcher | M-L | once | needs a new CI binary and unverified userland TCP |
| - | (c) sparse partial flash | - | - | unsafe: the phone's HFS+ differs from any OLD image |
| - | (d) other partitions | - | - | forbidden |

## 4. Implementer step list for (a) (no code here)

1. `tools/patch-numbers next --owner <you> --name sweet-dev-overlay`; write `xnu/patches/NNNN-sweet-dev-overlay.patch` with the Write tool (AP-25: not via a Bash heredoc); patch `bsd/kern/bsd_init.c` (call before `bsd_utaskbootstrap`, guarded by `ARM64_BOARD_CONFIG_SWEET`) and add `bsd/dev/sweet_devoverlay.c` + file list entry (see how 0026/0080 add `sweet_*.c` to `bsd/conf/files`).
2. Archive format (document in the patch header): `SWEETDEV\0` | u32 version | u32 count | per entry: u16 pathlen, u16 mode, u32 uid, u32 gid, u64 size, u32 crc32, path, bytes (16-byte aligned). Host packer `tools/mkdevoverlay.py DIR OUT.bin [--owner 0:0] [--manifest mf.json]` + `tools/test_mkdevoverlay.py` (round trip).
3. Kernel hook: boot-arg `sweetdev` (`1` copy, `restore` copy `.sweetorig` back, absent/0 = nothing); find ramdisk range (ADT `/chosen/memory-map RAMDisk`); validate header and `end <= size`; for each entry: reject paths not starting `/`, containing `..`, or outside an allowlist (`/usr/lib/`, `/usr/local/`, `/private/var/dev/`, `/System/Library/xpc/launchd.plist`, `/System/Library/LaunchDaemons/org.pixelios.`); create parent dirs; skip if existing file has identical size and bytes; else write `.sweettmp`, `vnode_setattr` mode/uid/gid, rename; first time save `.sweetorig`; `printf("SWEET DEV: ...")` per file plus a final `n written, n skipped, elapsed ms`; never panic, on error print and continue. Mark it `rootrw=1`-only (if the root is read-only print and return).
4. `tools/build-xnu-image.sh --overlay DIR` (new flag): run the packer, `mkpayload.py ... --ramdisk`, add `sweetdev=1`; `tools/xrun` pass-through `--overlay DIR`. Host test in `tools/` for the argument handling (pattern: `tools/test_mkpayload_cmdline.py`).
5. Size ramp on hardware first (U1): a dummy 40 MiB overlay with a kernel that only prints the md0 size, one `xrun --early` run, expect `mdevadd`/`md0` size in the log and the shim's `INITRD lo= hi=` line; abort criteria `ramdisk overlaps kernel memory` [src K/shim/loader.c:95-96].
6. First hardware run on the existing flashed root (no flash): overlay = one new file `/private/var/dev/hello` (mode 0644) plus one replaced test binary; expect `SWEET DEV: wrote ...` lines, then a second run with the same overlay expecting `skipped`. Then the real use: `libsweetgl.dylib` to `/usr/lib/` with `--args "sweetenv=backboardd:DYLD_INSERT_LIBRARIES=/usr/lib/libsweetgl.dylib"` (needs 0106+0110 from `wip/gpu-accel` in the kernel under test).
7. Review: adversarial, Opus (writes the filesystem); include the path-allowlist and `.sweetorig` logic; run `tools/stack-apply` and the gate (driver).
8. GPU workflow change: `GU/tools/overlay-ios-root.sh` stays for creating a NEW base root; day-to-day iteration becomes `tools/xrun --overlay DIR --args "sweetenv=..."`.

**Manual step for the user:** none for the data path. The one-time prerequisite is that the phone already holds a flashed iOS root (it does: userdata = ios-root3 family [S/docs/HANDOFF.md:98]). Only if the dev workflow needs a launchd job that the current root lacks, one regular flash of a root that contains it (AGENTS.md rule 3, pre-approved) - or ship a replacement `launchd.plist` through the overlay and skip even that.

## 5. Unknowns (what would settle each)

* **U1** largest ramdisk ABL/`fastboot boot` accepts and RAM placement vs kernel range: size-ramp run (step 5).
* **U2** whether `rd=ufsroot0` plus a `RAMDisk` ADT node boots cleanly (the source says yes; only `rd=md0` was observed): the same run.
* **U3** UFS write throughput for 22 MB from the HFS path: the `elapsed` line of step 6.
* **U4** that a file written this way is accepted by dyld/AMFI exactly like one from the image build (same vnode on `MNT_ROOTFS`, ad-hoc signature): step 6 with libsweetgl, and `sweetgl: loaded in backboardd` in the log (INJECT.md s.5).
* **U5** whether the root contains `curl`/`nc`/`tar`/`sh` (decides option (b) userland): list the root with the rootless guest (`U/tools/rootless/vm.sh run list`, as `selftest` does [src U/tools/build-ios-root-rootless.sh:86-90]), read-only, on a COPY of an image.
* **U6** userland TCP over `ue0` (only kernel UDP is observed): a `devsync`-style test or `ping 192.168.7.2` from the host (research 09 s.183 expects ICMP answer [src K/docs/research/09-xnu-usb-gs101.md:183]).
* **U7** HFS+ journal state when a hard reset follows a copy: every `xrun` ends with a reset today [obs], no corruption reported in PLAN-BOOT-SPEED; still run `fsck.hfsplus -n` on a pulled image once if an image is ever read back.
