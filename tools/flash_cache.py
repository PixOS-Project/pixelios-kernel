#!/usr/bin/env python3
"""flash_cache: verified Android-sparse copies of userdata root images on the SSD, so a userdata flash skips the raw re-hash and fastboot's raw scan
(user 2026-11-11: "flash faster, image on the SSD, never fill the disk").

  tools/flash_cache.py hash IMG        sha256 of IMG on stdout; an unchanged image (same dev, inode, size, mtime_ns, ctime_ns as when it was last hashed)
                                       is not read again (stamp file in the cache dir). Hashing itself runs `sha256sum` from PATH.
  tools/flash_cache.py prep IMG        build <cache>/<sha>.simg, verify it, record its stats; run it after building a root, BEFORE taking the phone lock
                                       (tools/fflash-userdata.sh also starts it in the background after a raw flash). rc 0 ready, 3 skipped (disk/HDD), 4 busy, 1 error
  tools/flash_cache.py lookup SHA      verified entry -> stdout "<simg path><TAB><stats text>", rc 0; otherwise rc 1 and the reason on stderr
  tools/flash_cache.py promote IMG     make IMG THE standard root, in a safe order: hash == IMG.sha256, prep (cache entry ready), then
                                       standard-root.txt switched atomically (old line kept in standard-root.history). Flashes nothing: the next
                                       fflash / phone-lock park restore puts it on the phone. The ONE way to change the standard root (2026-11-11)
  tools/flash_cache.py status          entries, sizes, last use, free space, limits
  tools/flash_cache.py gc              drop stale *.part files and entries whose file changed since prep verified it
  tools/flash_cache.py --self-test     tiny images in a temp dir; `RESULT: PASS flash_cache self-test N/N`

Why the flash gets faster [measured 2026-11-11, logs/flash-20261111T085310Z.log in the bt-proper worktree: fastboot 266 s = send 99 s + write 77 s
+ ~90 s not in any chunk; the script's sha256 ~64 s and the non-zero scan 20 s ran inside the phone hold too]:
  fastboot 34.0.5 load_buf_fd -> load_sparse_files -> sparse_file_import_auto(fd, false, true) [read from source: platform-tools-34.0.5 fastboot/fastboot.cpp:996,1066]
  reads EVERY block of a raw image to find constant blocks before the first chunk is sent (the ~90 s, 17 GB from the HDD pool); a sparse input is imported from its
  chunk headers only and then resparsed the same way. prep writes the simg with the SAME library fastboot loads (ldd of the fastboot on PATH) and the SAME import
  call, so the chunk list, the bytes sent and the bytes written are the same as for the raw image. prep proves it before an entry exists:
    1. an independent parser (layout: AOSP libsparse/sparse_format.h: header 28 B, chunk header 12 B, RAW 0xCAC1, FILL 0xCAC2, DONT_CARE 0xCAC3, CRC32 0xCAC4)
       expands the simg and its sha256 must equal the raw image's;
    2. zero DONT_CARE chunks (a skipped region would leave stale data on the phone: docs/tool-proposals/TP-025);
    3. libsparse sparse_file_import (fastboot's own probe) accepts it and its expanded length equals the raw size.
  An entry is bound to that check by the simg's dev/inode/size/mtime_ns/ctime_ns; lookup refuses (and removes) an entry whose file changed since.

Disk guard (the SSD root filesystem is shared and was 92 % full on 2026-11-11):
  prep knows the exact output size before writing (sparse_file_len). It evicts least-recently-used entries that are NOT the standard root
  (${STANDARD_ROOT:-<artifacts>/standard-root.txt}, first field) and writes only if afterwards: free space >= FLASH_CACHE_MIN_FREE_GB (15),
  cache total <= FLASH_CACHE_MAX_GB (26), entries <= FLASH_CACHE_MAX_ENTRIES (2). Otherwise `FLASH CACHE SKIP: ...` and rc 3 (the flash still works, slowly).
  After the write the free-space floor is checked again; below it the new entry is deleted. A cache dir on a rotating disk is refused
  (FLASH_CACHE_ALLOW_HDD=1 overrides); a device that cannot be classified is a GAP line, not a refusal.
Locks: <cache>/.prep.lock (one prep at a time), <cache>/.use.lock (fflash holds it shared while fastboot reads an simg; eviction takes it exclusive).
Env: FLASH_CACHE_DIR (default ${ARTIFACTS:-~/pixelios-artifacts}/flash-cache), FLASH_CACHE_LIBSPARSE, FLASH_CACHE_MIN_FREE_GB, FLASH_CACHE_MAX_GB,
FLASH_CACHE_MAX_ENTRIES, FLASH_CACHE_ALLOW_HDD, STANDARD_ROOT, ARTIFACTS."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import ctypes
import fcntl
import json
import os
import re
import struct
import subprocess
import sys
import time

GIB = 1 << 30
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I drops the script dir from sys.path
from flash_cache_lib import MAGIC, libsparse, verify   # noqa: E402


def say(*a):
    print(*a, file=sys.stderr, flush=True)


def cache_dir():
    return os.environ.get("FLASH_CACHE_DIR") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "flash-cache")


def envnum(name, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        raise SystemExit("flash_cache: %s=%r is not a number" % (name, v))


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ident(path):
    st = os.stat(path)
    return "%d %d %d %d %d" % (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def standard_sha():
    f = os.environ.get("STANDARD_ROOT") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "standard-root.txt")
    try:
        for ln in open(f):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                s = ln.split()[0]
                return s if re.fullmatch(r"[0-9a-f]{64}", s) else ""
    except OSError:   # lint-silent: expected no standard-root.txt: then no entry is protected from eviction, said by status
        pass
    return ""


class Locked:
    def __init__(self, name, mode, block=True):
        os.makedirs(cache_dir(), exist_ok=True)
        self.f = open(os.path.join(cache_dir(), name), "a")
        self.mode, self.block = mode, block

    def __enter__(self):
        try:
            fcntl.flock(self.f, self.mode | (0 if self.block else fcntl.LOCK_NB))
        except BlockingIOError:
            self.f.close()
            return None
        return self

    def __exit__(self, *a):
        self.f.close()


# ---------------------------------------------------------------------------------------------------- hash with stamps
def stamps_path():
    return os.path.join(cache_dir(), "hash-stamps.tsv")


def read_stamps():
    out = {}
    try:
        for ln in open(stamps_path()):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 2 and re.fullmatch(r"[0-9a-f]{64}", p[1]):
                out[p[0]] = (p[1], p[3] if len(p) > 3 else "?")
    except OSError:   # lint-silent: expected no stamp file yet: every image is hashed
        pass
    return out


def cmd_hash(img):
    i0 = ident(img)
    hit = read_stamps().get(i0)
    if hit:
        say("FLASH HASH STAMP: %s unchanged since it was hashed at %s (dev/inode/size/mtime/ctime equal): not read again" % (img, hit[1]))
        print(hit[0])
        return 0
    r = subprocess.run(["sha256sum", "--", img], capture_output=True, text=True)
    if r.returncode != 0 or not re.match(r"[0-9a-f]{64}\b", r.stdout):
        say("flash_cache: sha256sum %s failed rc=%d: %s" % (img, r.returncode, (r.stderr or r.stdout).strip()[:200]))
        return 1
    sha = r.stdout.split()[0]
    if ident(img) == i0:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(stamps_path(), "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            lines = [ln for ln in f.read().splitlines() if ln and not ln.startswith(i0 + "\t")][-63:]
            lines.append("%s\t%s\t%s\t%s" % (i0, sha, os.path.abspath(img), utc()))
            f.seek(0); f.truncate(); f.write("\n".join(lines) + "\n")
    else:
        say("FLASH HASH: %s changed while it was hashed: no stamp written" % img)
    print(sha)
    return 0


# ---------------------------------------------------------------------------------------------------- entries
def meta_path(sha):
    return os.path.join(cache_dir(), sha + ".json")


def simg_path(sha):
    return os.path.join(cache_dir(), sha + ".simg")


def entries():
    out = []
    try:
        names = sorted(os.listdir(cache_dir()))
    except OSError:
        return out
    for n in names:
        if re.fullmatch(r"[0-9a-f]{64}\.json", n):
            try:
                m = json.load(open(os.path.join(cache_dir(), n)))
                out.append(m)
            except (OSError, ValueError) as e:
                say("FLASH CACHE GAP: unreadable entry %s: %s" % (n, e))
    return out


def write_meta(m):
    p = meta_path(m["sha"])
    tmp = p + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(m, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def drop(sha, why):
    for p in (simg_path(sha), meta_path(sha)):
        try:
            os.unlink(p)
        except FileNotFoundError:   # lint-silent: expected half an entry (one of the two files) after an interrupted prep
            pass
    say("FLASH CACHE DROP %s: %s" % (sha[:12], why))


def stats_text(m):
    s, b = m["stats"], m["stats"]["block_size"]
    return "%.2f GiB data sent as RAW chunks (%d of %d %d-byte blocks; %d zero-fill, %d other fill, 0 skipped; verified sparse copy %.2f GiB on the SSD, prep %s)" % (
        s["raw_blocks"] * b / GIB, s["raw_blocks"], s["total_blocks"], b, s["fill_zero_blocks"], s["fill_other_blocks"], m["simg_size"] / GIB, m["created"])


def check_entry(m):
    """'' when the entry is still the file prep verified, else the reason."""
    p = simg_path(m["sha"])
    try:
        cur = ident(p)
    except OSError as e:
        return "simg missing (%s)" % e.strerror
    if cur != m.get("simg_id"):
        return "simg changed since prep verified it (dev/inode/size/mtime/ctime %s, verified %s)" % (cur, m.get("simg_id"))
    return ""


def cmd_lookup(sha):
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        say("flash_cache lookup: %r is not a sha256" % sha)
        return 2
    try:
        m = json.load(open(meta_path(sha)))
    except FileNotFoundError:
        lf = last_failed(sha)
        say("FLASH CACHE MISS %s: no entry (tools/flash_cache.py prep IMG builds one)%s" % (sha[:12], "; the last prep of it ended " + lf if lf else ""))
        return 1
    except (OSError, ValueError) as e:
        say("FLASH CACHE MISS %s: entry unreadable: %s" % (sha[:12], e))
        return 1
    why = check_entry(m)
    if why:
        with Locked(".use.lock", fcntl.LOCK_EX, block=False) as lk:   # non-blocking: the caller (fflash) may hold it shared
            if lk is None:
                say("FLASH CACHE MISS %s: %s (left for gc: the use lock is held)" % (sha[:12], why))
                return 1
            drop(sha, why)
        say("FLASH CACHE MISS %s: %s" % (sha[:12], why))
        return 1
    m["last_used"] = utc()
    write_meta(m)
    print("%s\t%s" % (simg_path(sha), stats_text(m)))
    return 0


def rotational(path):
    """'1' / '0' / '' (unknown) for the block device holding PATH."""
    st = os.stat(path)
    base = "/sys/dev/block/%d:%d" % (os.major(st.st_dev), os.minor(st.st_dev))
    try:
        real = os.path.realpath(base)
        for q in (os.path.join(real, "queue", "rotational"), os.path.join(os.path.dirname(real), "queue", "rotational")):
            if os.path.exists(q):
                return open(q).read().strip()
    except OSError:   # lint-silent: expected a virtual filesystem (tmpfs, zfs): unknown, said by the caller
        pass
    return ""


def free_bytes():
    v = os.statvfs(cache_dir())
    return v.f_bavail * v.f_frsize


def cmd_prep(img):
    if not os.path.isfile(img):
        say("flash_cache prep: %s is not a file" % img)
        return 1
    with open(img, "rb") as f:
        if f.read(4) == struct.pack("<I", MAGIC):
            say("flash_cache prep: %s is already an Android sparse image: pass the raw image" % img)
            return 1
    os.makedirs(cache_dir(), exist_ok=True)
    rot = rotational(cache_dir())
    if rot != "0":
        what = "a rotating disk" if rot == "1" else "a device whose type is unknown (no /sys/dev/block queue/rotational: zfs, tmpfs, ...)"
        if os.environ.get("FLASH_CACHE_ALLOW_HDD") != "1":
            say("FLASH CACHE SKIP: %s is on %s; the cache belongs on the SSD (FLASH_CACHE_DIR; FLASH_CACHE_ALLOW_HDD=1 overrides)" % (cache_dir(), what))
            return 3
        say("FLASH CACHE GAP: %s is on %s, allowed by FLASH_CACHE_ALLOW_HDD=1" % (cache_dir(), what))
    with Locked(".prep.lock", fcntl.LOCK_EX, block=False) as lk:
        if lk is None:
            say("FLASH CACHE BUSY: another prep runs (%s/.prep.lock); try again after it" % cache_dir())
            return 4
        return _prep_locked(img)


def _prep_locked(img):
    t0 = time.time()
    out = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "hash", img], capture_output=True, text=True, env=os.environ)
    sys.stderr.write(out.stderr)
    if out.returncode != 0:
        return 1
    sha = out.stdout.strip()
    side = img + ".sha256"
    if os.path.exists(side):
        want = open(side).read().split()[:1]
        if want != [sha]:
            say("flash_cache prep: %s hashes to %s but %s says %s: refused" % (img, sha[:12], side, (want or ["(empty)"])[0][:12]))
            return 1
    try:
        cur = json.load(open(meta_path(sha)))
        if not check_entry(cur):
            say("FLASH CACHE READY %s: entry exists and is unchanged (%s)" % (sha[:12], simg_path(sha)))
            return 0
    except (OSError, ValueError):   # lint-silent: expected no entry yet (the normal case): build it below
        pass
    raw_size = os.path.getsize(img)
    if raw_size % 4096:
        say("flash_cache prep: %s is %d bytes, not a multiple of 4096: no sparse copy (the raw flash path still works)" % (img, raw_size))
        return 1
    L, lp = libsparse()
    say("flash_cache: libsparse %s" % lp)
    rfd = os.open(img, os.O_RDONLY)
    s = None
    part = os.path.join(cache_dir(), "%s.simg.part%d" % (sha, os.getpid()))
    try:
        t1 = time.time()
        s = L.sparse_file_import_auto(rfd, False, True)      # the same call and arguments as fastboot load_sparse_files
        if not s:
            say("flash_cache prep: sparse_file_import_auto failed on %s" % img)
            return 1
        need = L.sparse_file_len(s, True, False)
        if need <= 0:
            say("flash_cache prep: sparse_file_len = %d" % need)
            return 1
        say("flash_cache: raw scan %.0f s, sparse copy will be %.2f GiB" % (time.time() - t1, need / GIB))
        why = make_room(sha, need + (64 << 20))   # + the write-size tolerance below (review 0294 F7)
        if why:
            say("FLASH CACHE SKIP %s: %s" % (sha[:12], why))
            mark_failed(sha, "SKIP: %s" % why)
            return 3
        wfd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            rc = L.sparse_file_write(s, wfd, False, True, False)
            os.fsync(wfd)
        finally:
            os.close(wfd)
        if rc != 0:
            raise OSError("sparse_file_write rc=%d (errno %d)" % (rc, ctypes.get_errno()))
        got = os.path.getsize(part)
        if got != need:   # observed 2026-11-11 on the standard root: 1244 B more than sparse_file_len (its estimate is not exact); content is proven below
            if abs(got - need) > (64 << 20):
                raise OSError("wrote %d bytes, sparse_file_len said %d (more than 64 MiB apart)" % (got, need))
            say("FLASH CACHE NOTE: wrote %d bytes, sparse_file_len estimated %d (%+d B); the content check below decides" % (got, need, got - need))
            need = got
        floor = envnum("FLASH_CACHE_MIN_FREE_GB", 15) * GIB
        if free_bytes() < floor:
            raise OSError("free space %.1f GiB fell below the %.0f GiB floor during the write (something else filled the disk)" % (free_bytes() / GIB, floor / GIB))
        if os.environ.get("FLASH_CACHE_TEST_CORRUPT") == "1":   # self-test only: flip one data byte of the written copy, prep must refuse it
            with open(part, "r+b") as f:
                f.seek(-1, 2); b = f.read(1); f.seek(-1, 2); f.write(bytes([b[0] ^ 1]))
        pre = os.stat(part)
        t2 = time.time()
        st = verify(part, sha, raw_size, L)
        post = os.stat(part)
        if (pre.st_ino, pre.st_size, pre.st_mtime_ns) != (post.st_ino, post.st_size, post.st_mtime_ns):
            raise OSError("the copy changed while it was verified")
        with Locked(".use.lock", fcntl.LOCK_EX):
            os.replace(part, simg_path(sha))
            fin = os.stat(simg_path(sha))
            if (fin.st_ino, fin.st_size, fin.st_mtime_ns) != (post.st_ino, post.st_size, post.st_mtime_ns):
                os.unlink(simg_path(sha))
                raise OSError("the copy changed between verify and rename")
            write_meta(dict(sha=sha, raw_path=os.path.abspath(img), raw_size=raw_size, simg_size=need, simg_id=ident(simg_path(sha)), created=utc(),
                            last_used=utc(), stats=st, libsparse=lp,
                            verified="independent expand sha256 == raw; libsparse expand sha256 == raw; 0 DONT_CARE; libsparse length == raw size"))
        clear_failed(sha)
        say("FLASH CACHE READY %s: %s (write %.0f s, verify %.0f s, total %.0f s)" % (sha[:12], simg_path(sha), t2 - t1, time.time() - t2, time.time() - t0))
        say("FLASH CACHE STATS %s" % stats_text(json.load(open(meta_path(sha)))))
        return 0
    except (OSError, ValueError) as e:
        say("FLASH CACHE FAIL %s: %s (no entry written)" % (sha[:12], e))
        mark_failed(sha, "FAIL: %s" % e)
        return 1
    finally:
        if s:
            L.sparse_file_destroy(s)
        os.close(rfd)
        if os.path.exists(part):
            os.unlink(part)


def failed_path(sha):
    return os.path.join(cache_dir(), sha + ".failed")


def mark_failed(sha, why):
    try:
        with open(failed_path(sha), "w") as f:
            f.write("%s %s\n" % (utc(), why))
    except OSError as e:
        say("FLASH CACHE GAP: cannot record the failed prep: %s" % e)


def clear_failed(sha):
    try:
        os.unlink(failed_path(sha))
    except FileNotFoundError:   # lint-silent: expected no earlier failed prep of this image
        pass


def last_failed(sha):
    try:
        return open(failed_path(sha)).read().strip()
    except OSError:   # lint-silent: expected no failed prep recorded
        return ""


def make_room(sha, need):
    """Evict LRU non-standard entries until the new one fits all three limits; '' when it fits, else why not (nothing is evicted then)."""
    floor = envnum("FLASH_CACHE_MIN_FREE_GB", 15) * GIB
    cap = envnum("FLASH_CACHE_MAX_GB", 26) * GIB
    maxn = int(envnum("FLASH_CACHE_MAX_ENTRIES", 2))
    std = standard_sha()
    ents = [m for m in entries() if m["sha"] != sha]
    free, total = free_bytes(), sum(m.get("simg_size", 0) for m in ents)
    victims = []
    cands = sorted((m for m in ents if m["sha"] != std), key=lambda m: m.get("last_used", ""))

    def fits():
        n = len(ents) - len(victims) + 1
        fr = free + sum(v["simg_size"] for v in victims) - need
        tot = total - sum(v["simg_size"] for v in victims) + need
        return fr >= floor and tot <= cap and n <= maxn, fr, tot, n
    while not fits()[0] and cands:
        victims.append(cands.pop(0))
    ok, fr, tot, n = fits()
    if not ok:
        return ("needs %.2f GiB: free after %.1f GiB (floor %.0f), cache %.1f GiB (cap %.0f), entries %d (max %d), even after evicting every non-standard entry"
                % (need / GIB, fr / GIB, floor / GIB, tot / GIB, cap / GIB, n, maxn))
    if victims:
        with Locked(".use.lock", fcntl.LOCK_EX):
            for v in victims:
                drop(v["sha"], "evicted (least recently used %s) to make room for %s" % (v.get("last_used"), sha[:12]))
    return ""



def std_file():
    return os.environ.get("STANDARD_ROOT") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "standard-root.txt")


def cmd_promote(img):
    """Order matters (coordinator 2026-11-11): park-root restores whatever standard-root.txt names, so the file switches only after the
    new root's cache entry is verified, and nothing is flashed here."""
    img = os.path.abspath(img)
    side = img + ".sha256"
    if not os.path.isfile(img) or not os.path.isfile(side):
        say("flash_cache promote: %s and %s must both exist" % (img, side))
        return 1
    old = standard_sha()
    rc = cmd_prep(img)
    if rc != 0:
        say("STANDARD ROOT NOT PROMOTED: prep of %s ended rc=%d (standard-root.txt unchanged, still %s)" % (img, rc, old[:12] or "none"))
        return 1
    sha = open(side).read().split()[0]
    m = json.load(open(meta_path(sha)))
    if check_entry(m) or m.get("raw_path") != img:
        say("STANDARD ROOT NOT PROMOTED: cache entry of %s is not usable for %s" % (sha[:12], img))
        return 1
    f = std_file()
    try:
        lines = open(f).read().splitlines()
    except OSError:   # lint-silent: expected no standard-root.txt yet: promote writes a fresh one with the default header
        lines = []
    head = [ln for ln in lines if ln.startswith("#")] or ["# standard root (user 2026-11-09; docs/recipes/no-flash.md): <sha256> <path>. Changed only by tools/flash_cache.py promote (2026-11-11)."]
    tmp = f + ".tmp%d" % os.getpid()
    with open(tmp, "w") as fh:
        fh.write("\n".join(head + ["%s %s" % (sha, img)]) + "\n")
    os.replace(tmp, f)
    with open(os.path.join(os.path.dirname(f), "standard-root.history"), "a") as fh:
        fh.write("%s\t%s\t%s\t%s\n" % (utc(), old or "-", sha, img))
    say("STANDARD ROOT PROMOTED %s -> %s (%s): the next fflash / phone-lock park restore puts it on the phone; the old entry is no longer protected from eviction; tell the other threads (they re-baseline)" % (old[:12] or "none", sha[:12], img))
    return 0

def stray_files():
    """Files no entry accounts for: (part files, orphan simg files without metadata), each as (name, bytes)."""
    parts, orphans = [], []
    try:
        names = os.listdir(cache_dir())
    except OSError:
        return parts, orphans
    for p in names:
        fp = os.path.join(cache_dir(), p)
        if re.fullmatch(r"[0-9a-f]{64}\.simg\.part\d+", p):
            parts.append((p, os.path.getsize(fp)))
        elif re.fullmatch(r"[0-9a-f]{64}\.simg", p) and not os.path.exists(meta_path(p[:64])):
            orphans.append((p, os.path.getsize(fp)))
    return parts, orphans


def cmd_status():
    std = standard_sha()
    ents = entries()
    parts, orphans = stray_files()
    print("flash cache %s: %d entr%s, %.2f GiB; free %.1f GiB; limits: floor %.0f GiB free, cap %.0f GiB, max %d entries; disk rotational=%s"
          % (cache_dir(), len(ents), "y" if len(ents) == 1 else "ies", sum(m.get("simg_size", 0) for m in ents) / GIB, free_bytes() / GIB,
             envnum("FLASH_CACHE_MIN_FREE_GB", 15), envnum("FLASH_CACHE_MAX_GB", 26), int(envnum("FLASH_CACHE_MAX_ENTRIES", 2)), rotational(cache_dir()) or "unknown"))
    if not std:
        print("  GAP: no standard root sha256: no entry is protected from eviction")
    for m in sorted(ents, key=lambda m: m.get("last_used", ""), reverse=True):
        bad = check_entry(m)
        print("  %s%s %.2f GiB last_used=%s raw=%s%s" % (m["sha"][:12], " [standard]" if m["sha"] == std else "", m["simg_size"] / GIB, m.get("last_used"),
                                                       m.get("raw_path"), " STALE: " + bad if bad else ""))
    for name, size in parts:
        print("  part %s %.2f GiB (a prep in progress, or a killed one: gc removes it once its process is gone)" % (name, size / GIB))
    for name, size in orphans:
        print("  ORPHAN %s %.2f GiB (no metadata: gc removes it)" % (name, size / GIB))
    for p in sorted(os.listdir(cache_dir())) if os.path.isdir(cache_dir()) else []:
        if p.endswith(".failed"):
            print("  last prep of %s: %s" % (p[:12], last_failed(p[:64])))
    return 0


def cmd_gc():
    n = 0
    with Locked(".prep.lock", fcntl.LOCK_EX, block=False) as pl:
        with Locked(".use.lock", fcntl.LOCK_EX):
            for m in entries():
                bad = check_entry(m)
                if bad:
                    drop(m["sha"], bad); n += 1
            parts, orphans = stray_files()
            for p, _ in parts:
                pid = p.rsplit("part", 1)[1]
                if pl is not None or not os.path.exists("/proc/%s" % pid):
                    os.unlink(os.path.join(cache_dir(), p)); n += 1
                    say("FLASH CACHE DROP part %s: its prep process is gone" % p)
            if pl is None and orphans:
                say("FLASH CACHE: a prep runs; orphan simg files are left for the next gc")
            for p, _ in orphans if pl is not None else []:
                os.unlink(os.path.join(cache_dir(), p)); n += 1
                say("FLASH CACHE DROP orphan %s: no metadata (a prep killed between rename and metadata write)" % p)
    print("RESULT: PASS flash_cache gc removed=%d" % n)
    return 0


def _term(signum, _frame):
    raise SystemExit(128 + signum)   # run the finally blocks: a killed prep removes its part file (review 0294 F4)


def main(argv):
    if argv[:1] in (["-h"], ["--help"]) or not argv:
        print(__doc__)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    import signal
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGHUP, _term)
    if cmd == "--self-test":
        import flash_cache_selftest_lib
        return flash_cache_selftest_lib.self_test(sys.modules[__name__])
    if cmd in ("hash", "prep", "lookup", "promote") and len(rest) == 1:
        return dict(hash=cmd_hash, prep=cmd_prep, lookup=cmd_lookup, promote=cmd_promote)[cmd](rest[0])
    if cmd == "status" and not rest:
        return cmd_status()
    if cmd == "gc" and not rest:
        return cmd_gc()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
