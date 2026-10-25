# Requirements: building and running the shim and the XNU kernel (pixelios-kernel)

Tags: *observed* = measured on the maintainer's machine (Debian 13, 16 cores, 24 GB RAM, 2026-10-22); *read* = read from the scripts and docs.

## Shim (pre-kernel code, `shim/build.sh`)
- An AArch64 bare-metal GCC (`aarch64-linux-gnu-` on `PATH`, or Arm GNU Toolchain `aarch64-none-elf`; `CROSS_PREFIX` overrides), GNU binutils, Python 3, a host C compiler for the host tests.
- AOSP boot-image tools: `tools/fetch-android-tools.sh` downloads `mkbootimg.py` and `avbtool.py` into the ignored `tools/third_party/`.
- Not a git checkout (zip download, generated tree)? `shim/build.sh` and `tools/imgmeta.py` fall back to the id `nogit` (build id: the date) and hash the source files directly, so the build works; the freshness check still refuses a stale image.
- *Observed*: the shim builds twice with the same flags differ only in the embedded git hash and in the inode/mtime fields of an empty cpio archive (checked 2026-10-22 on the split trees).

## XNU kernel (patch stack on pristine xnu-8796.141.3)
Two ways, same patch stack:
1. **CI** (`.github/workflows/xnu.yml`): a macOS runner with Xcode 15.0.1, about 4-8 minutes. This is the reference build.
2. **Local, no Mac** (`tools/xnu-local-build`, `docs/LOCAL-BUILD.md`): Debian clang as a Mach-O cross compiler, a Mach-O cctools tarball, MacOSX14.0.sdk, mig, unifdef and tcsh built from source.
   Host tools: `git curl xz make clang perl python3 m4 autoconf automake gawk rsync dpkg apt-get` (flex/bison are unpacked from `.deb` files if missing, no root).
   Disk about 3.2 GB under `~/pixelios-artifacts/localbuild`; `make -j4` (capped at 4). *Observed*: cold build 71 s, one changed patch 16 s, plus a first run that downloads the SDKs and builds the helper tools.
   All download sources are pinned (git tags/commits, two tarballs checked against sha256); see the table in `docs/LOCAL-BUILD.md`.
- 59 of the 117 patches and the three stagers (`xnu/patches/*.sh`) began as ios-sweet files (MIT, see `LICENSES/MIT-ios-sweet.txt`); the other 58 are ours. `tools/stack-apply` checks that the whole stack applies to pristine xnu-8796.
- *Observed*: a kernel built from the generated kernel tree has the same external symbols (24163), the same SWEET symbols (460) and the same strings as the one built from the private monorepo, except the build timestamp and the work-directory path length (the `__cstring` section differs by that); it is not bit-identical.

## Apple-derived input you must extract yourself
- `research/ipsw-x/d22ap.adt`: the iPhone X device tree (`DeviceTree.d22ap.im4p` from the iOS 16.7.16 IPSW, extracted with `ipsw img4 im4p extract ... -o d22ap.adt`; `tools/remotezip.py` fetches single files from the IPSW).
  `tools/mkadt.py` builds the phone's device tree from it, so `tools/xrun` and the image builders need it; the unit test `test_mkadt_identity` is skipped without it. It is Apple's data and is never in git.

## Running on a phone (Pixel 6a "bluejay")
- Unlocked bootloader (unlocking wipes the phone), `adb`/`fastboot`, a USB cable, a spare phone. **Read `docs/SAFETY.md` first.** Only `userdata` is ever written; everything else uses `fastboot boot`.
- The iOS root image comes from pixelios-userland. Host USB network for the debug log needs one `sudo` line the tools print (an address on the new interface).
- One hardware cycle: `tools/xrun` (builds the image, boots it, reads the log, waits for the self-reset, one report ending in `RESULT: PASS|FAIL|PARTIAL|ERROR`).
- Tests: `tools/check-all.sh` (prints `GATE PASS <hash>`); run one heavy job at a time, the kernel build and the root build both need memory.
