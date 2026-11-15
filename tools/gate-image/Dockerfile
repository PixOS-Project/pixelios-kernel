# PixOS gate image pixos-gate:trixie-20261111: Debian 13.7 (trixie) pinned to the lab-host package versions (2026-10-27; + 2026-10-29 test deps; + 2026-10-29b lint-c analyzers; + 2026-10-31 xnu-local-build host tools; + 2026-11-11 android-libsparse 7zip).
# Source of truth: pixelios-kernel tools/gate-image/ (this file, requirements.txt, verlist.sh). Build context on offload-host: $OFFLOAD_ROOT/work/gate-image-20261111 (default /mnt/offload-data/pixos/work)
# (these three files + wheels/ = the four .whl files named in requirements.txt, downloaded on the lab-host with pip download --no-deps, hash-checked here).
# 20261029 vs 20261028: python3-capstone (apt) + pillow, numpy, opencv-python-headless (pip, hash-pinned, offline). Without them the container
# unit step skipped 22 tests (test_xrun_report 14, test_mg_key 5, test_qrl2 1; test_golden_gpu_analyze 2 = data, mounted by remote-job) and
# check-all.sh:168 rightly refused to register the PASS (HANDOFF 20b 4(c); batch-20d 621c655 gate-pre).
# 20261029b vs 20261029: cppcheck + clang-tidy-19 (lab-host versions [obs apt-cache policy]). Without them the full-gate lint-c step (63d846b, TP-014)
# printed `lint-c ERROR: RESULT: ERROR missing: cppcheck, clang-tidy files=0` on every formal gate-pre since it landed (first: GPU fbdcd73): it checked nothing.
# 20261031 vs 20261029b: m4 autoconf automake rsync (the pins of Dockerfile.xnub:8, lab-host versions). Without them tools/xnu-local-build:70-71 stops with "missing host tools" and the formal gate-pre
# unit step on offload-host failed test_xnu_remote_build (`RESULT: ERROR rsync not installed`, tools/test_xnu_remote_build.sh:27) [obs 2026-10-31 acc-f3 6b18630]. Dockerfile.xnub is kept: its image still builds FROM the old tag.
# 20261111 vs 20261031: android-libsparse (libsparse.so.0 for tools/flash_cache.py, test_flash_cache, test_fflash_scan) + 7zip (7z for test_overlay_std Reader), lab-host versions [obs dpkg-query 2026-11-11].
# Without them the formal gate bt-defaults-land2 unit step failed 11 of 224 files in this container (R13: a missing piece is added to the image, never skipped; the read-only inputs are mounted by tools/remote-job).
FROM docker.io/library/debian:trixie
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
 gcc=4:14.2.0-1 gcc-14=14.2.0-19 g++=4:14.2.0-1 cpp=4:14.2.0-1 \
 libc6=2.41-12+deb13u4 libc6-dev=2.41-12+deb13u4 libc-bin=2.41-12+deb13u4 libc-dev-bin=2.41-12+deb13u4 \
 binutils=2.44-3 make=4.4.1-2 device-tree-compiler=1.7.2-2+b1 xz-utils=5.8.1-1+deb13u2 \
 openssl=3.5.7-1~deb13u3 python3=3.13.5-1 python3.13=3.13.5-2+deb13u5 python3-pip \
 shellcheck=0.10.0-1 lz4=1.10.0-4 clang=1:19.0-63 clang-19=1:19.1.7-3+b1 llvm-19=1:19.1.7-3+b1 \
 git=1:2.47.3-0+deb13u1 patch=2.8-2 cpio=2.15+dfsg-2 perl=5.40.1-6+deb13u1 curl=8.14.1-2+deb13u7 \
 util-linux=2.41.5-0+deb13u1 gzip=1.13-1+deb13u1 tar=1.35+dfsg-3.1 fakeroot=1.37.1.1-1 \
 qemu-system-arm=1:10.0.13+ds-0+deb13u1 ca-certificates=20250419+deb13u1 procps=2:4.0.4-9 \
 iproute2=6.15.0-1 unzip=6.0-29+deb13u1 file=1:5.46-5 bzip2=1.0.8-6 zstd=1.5.7+dfsg-1 mawk=1.3.4.20250131-1 zlib1g-dev=1:1.3.dfsg+really1.3.1-1+b1 \
 python3-capstone=5.0.7-1~deb13u1 cppcheck=2.17.1-2 clang-tidy-19=1:19.1.7-3+b1 \
 m4=1.4.19-8 autoconf=2.72-3.1 automake=1:1.17-4 rsync=3.5.0+ds1-0+deb13u1 \
 android-libsparse=1:34.0.5-12 7zip=25.01+dfsg-1~deb13u2 \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/req/requirements.txt
COPY wheels/ /tmp/req/wheels/
RUN pip3 install --no-cache-dir --break-system-packages --no-index --no-deps --require-hashes --find-links /tmp/req/wheels -r /tmp/req/requirements.txt \
 && rm -rf /tmp/req
RUN useradd -u 1000 -m -d /home/user -s /bin/bash pixos 2>/dev/null || true
ENV HOME=/home/user
USER 1000
