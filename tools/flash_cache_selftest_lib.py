"""flash_cache_selftest_lib: tools/flash_cache.py --self-test (split out for lint-size R29). Tiny images in a temp dir, real libsparse."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from flash_cache_lib import DONT_CARE, FILL, MAGIC, RAW, expand, libsparse, libsparse_expand_sha, libsparse_path, verify

tmp = me = L = lp = a = sha_a = b = sha_b = c = sha_c = None
fails, n = [], [0]


def check(name, ok, extra=""):
    n[0] += 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + extra[-600:]))
    if not ok:
        fails.append(name)

def run(*a, env=None):
    r = subprocess.run(me + list(a), capture_output=True, text=True, env=dict(os.environ, **(env or {})))
    return r.returncode, r.stdout, r.stderr

def mkimg(name, blocks):
    """blocks: list of b'z' (zero), b'f' (0xab fill), b'd' (random data), b'h' (hole)"""
    p = os.path.join(tmp, name)
    with open(p, "wb") as f:
        for k in blocks:
            if k == b"h":
                f.seek(4096, 1)
            else:
                f.write({b"z": b"\0" * 4096, b"f": b"\xab" * 4096, b"d": os.urandom(4096)}[k])
        f.truncate(len(blocks) * 4096)
    sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
    open(p + ".sha256", "w").write("%s  %s\n" % (sha, p))
    return p, sha



def self_test(fc):
    global tmp, me, fails, n, L, lp, a, sha_a, b, sha_b, c, sha_c
    for k in ("meta_path", "simg_path", "failed_path", "cache_dir", "rotational"):
        globals()[k] = getattr(fc, k)
    tmp = tempfile.mkdtemp(prefix="flash-cache-test-")
    os.environ.update(FLASH_CACHE_DIR=os.path.join(tmp, "cache"), STANDARD_ROOT=os.path.join(tmp, "std.txt"), FLASH_CACHE_MIN_FREE_GB="0",
                      FLASH_CACHE_MAX_GB="1", FLASH_CACHE_MAX_ENTRIES="2", FLASH_CACHE_ALLOW_HDD="1")
    fails, n = [], [0]
    me = [sys.executable, "-I", os.path.abspath(fc.__file__)]

    try:
        L, lp = libsparse()
    except OSError as e:
        print("RESULT: FAIL flash_cache self-test: libsparse not loadable (%s): %s" % (libsparse_path(), e))
        return 1
    for part in (_p1_hash_prep_lookup, _p2_verifier_disk_guard, _p3_content_checks, _p4_strays_hdd_sigterm, _p5_promote):
        part()
    shutil.rmtree(tmp)
    print("RESULT: %s flash_cache self-test %d/%d (libsparse %s)" % ("FAIL" if fails else "PASS", n[0] - len(fails), n[0], lp))
    return 1 if fails else 0


def _p1_hash_prep_lookup():
    global tmp, me, fails, n, L, lp, a, sha_a, b, sha_b, c, sha_c
    a, sha_a = mkimg("a.img", [b"d", b"z", b"z", b"h", b"h", b"f", b"d", b"d", b"z", b"h"])
    rc, o, e = run("hash", a)
    check("hash prints the sha256", rc == 0 and o.strip() == sha_a, e)
    rc, o, e = run("hash", a)
    check("second hash of an unchanged image uses the stamp", rc == 0 and o.strip() == sha_a and "FLASH HASH STAMP" in e, e)
    os.utime(a)   # new mtime/ctime, same content
    rc, o, e = run("hash", a)
    check("a touched image is hashed again", rc == 0 and o.strip() == sha_a and "FLASH HASH STAMP" not in e, e)
    rc, o, e = run("lookup", sha_a)
    check("lookup before prep is a miss rc 1", rc == 1 and "FLASH CACHE MISS" in e, e)
    rc, o, e = run("prep", a)
    check("prep builds a verified entry", rc == 0 and "FLASH CACHE READY" in e, e)
    m = json.load(open(meta_path(sha_a)))
    check("stats: 3 data blocks, 0 skipped, zero/other fill counted", m["stats"]["raw_blocks"] == 3 and m["stats"]["dont_care_blocks"] == 0
          and m["stats"]["fill_other_blocks"] == 1 and m["stats"]["fill_zero_blocks"] == 6, json.dumps(m["stats"]))
    esha, _ = expand(simg_path(sha_a), os.path.getsize(a))
    check("independent expand of the simg equals the raw image", esha == sha_a)
    rc, o, e = run("lookup", sha_a)
    check("lookup hit prints path TAB stats", rc == 0 and o.startswith(simg_path(sha_a) + "\t") and "RAW chunks" in o, e)
    rc, o, e = run("prep", a)
    check("prep again: already ready", rc == 0 and "entry exists" in e, e)
    os.utime(simg_path(sha_a))
    rc, o, e = run("lookup", sha_a)
    check("a changed simg is refused and dropped", rc == 1 and "changed since prep" in e and not os.path.exists(simg_path(sha_a)), e)
    run("prep", a)


def _p2_verifier_disk_guard():
    global tmp, me, fails, n, L, lp, a, sha_a, b, sha_b, c, sha_c
    # verifier mutants: a DONT_CARE chunk, a flipped data byte, a wrong total
    def craft(chunks, total_blks):
        p = os.path.join(tmp, "x.simg")
        with open(p, "wb") as f:
            f.write(struct.pack("<IHHHHIIII", MAGIC, 1, 0, 28, 12, 4096, total_blks, len(chunks), 0))
            for t, nb, data in chunks:
                f.write(struct.pack("<HHII", t, 0, nb, 12 + len(data)) + data)
        return p
    p = craft([(RAW, 1, b"\1" * 4096), (DONT_CARE, 1, b"")], 2)
    _, st = expand(p, 8192)
    check("verifier counts DONT_CARE blocks (prep refuses them)", st["dont_care_blocks"] == 1)
    p = craft([(RAW, 1, b"\1" * 4096)], 2)
    try:
        expand(p, 8192); ok = False
    except ValueError:
        ok = True
    check("verifier refuses chunks that do not cover the image", ok)
    raw1 = os.path.join(tmp, "one.raw"); open(raw1, "wb").write(b"\1" * 4096)
    p = craft([(RAW, 1, b"\1" * 4095 + b"\2")], 1)
    check("verifier hash differs on one flipped byte", expand(p, 4096)[0] != hashlib.sha256(open(raw1, "rb").read()).hexdigest())
    # disk guard: entry cap, standard root never evicted, LRU eviction, impossible = SKIP rc 3 with nothing evicted
    b, sha_b = mkimg("b.img", [b"d", b"d", b"z"])
    c, sha_c = mkimg("c.img", [b"d", b"z", b"d", b"d"])
    open(os.environ["STANDARD_ROOT"], "w").write("%s %s\n" % (sha_a, a))
    rc, o, e = run("prep", b)
    check("second entry fits (max 2)", rc == 0, e)
    rc, o, e = run("prep", c)
    check("third entry evicts the LRU non-standard entry, never the standard one",
          rc == 0 and os.path.exists(simg_path(sha_a)) and not os.path.exists(simg_path(sha_b)) and os.path.exists(simg_path(sha_c)) and "evicted" in e, e)
    rc, o, e = run("prep", b, env=dict(FLASH_CACHE_MAX_ENTRIES="1"))
    check("no room without evicting the standard root: SKIP rc 3, nothing evicted",
          rc == 3 and "FLASH CACHE SKIP" in e and os.path.exists(simg_path(sha_a)) and os.path.exists(simg_path(sha_c)), e)
    rc, o, e = run("prep", b, env=dict(FLASH_CACHE_MIN_FREE_GB="100000"))
    check("free-space floor: SKIP rc 3", rc == 3 and "floor" in e, e)
    open(os.path.join(cache_dir(), sha_b + ".simg.part999999999"), "w").close()
    rc, o, e = run("gc")
    check("gc drops a part file whose prep is gone", rc == 0 and not os.path.exists(os.path.join(cache_dir(), sha_b + ".simg.part999999999")), o + e)
    with open(b + ".sha256", "w") as f:
        f.write("0" * 64 + "  x\n")
    rc, o, e = run("prep", b)
    check("prep refuses an image that does not match its .sha256", rc == 1 and "refused" in e, e)
    rc, o, e = run("status")
    check("status names the standard entry", rc == 0 and "[standard]" in o, o + e)


def _p3_content_checks():
    global tmp, me, fails, n, L, lp, a, sha_a, b, sha_b, c, sha_c
    # review 0294 F1: every content check of prep has a failing case
    d, sha_d = mkimg("d.img", [b"d", b"z", b"d"])
    rc, o, e = run("prep", d, env=dict(FLASH_CACHE_TEST_CORRUPT="1"))
    check("prep refuses a copy with one flipped byte (end to end), no entry, failure recorded",
          rc == 1 and "expanded sha256" in e and not os.path.exists(simg_path(sha_d)) and os.path.exists(failed_path(sha_d)), e)
    rc, o, e = run("lookup", sha_d)
    check("a miss names the last failed prep", rc == 1 and "the last prep of it ended" in e and "FAIL" in e, e)
    rc, o, e = run("prep", d)
    check("a later good prep clears the failure", rc == 0 and not os.path.exists(failed_path(sha_d)), e)
    good = simg_path(sha_d)
    def vcase(name, mutate, want):
        q = os.path.join(tmp, "v.simg")
        shutil.copy(good, q)
        mutate(q)
        try:
            verify(q, sha_d, os.path.getsize(d), L); got = "accepted"
        except (OSError, ValueError) as ex:
            got = str(ex)
        check("verify refuses " + name, want in got, got)
    def flip_last(q):
        with open(q, "r+b") as f:
            f.seek(-1, 2); b0 = f.read(1); f.seek(-1, 2); f.write(bytes([b0[0] ^ 1]))
    vcase("a flipped data byte", flip_last, "expanded sha256")
    def to_dont_care(q):   # rewrite the zero FILL chunk as DONT_CARE: same expansion hash (zeros), must still be refused
        raw = bytearray(open(q, "rb").read()); off = 28
        while off < len(raw):
            t, _r, nb, tsz = struct.unpack_from("<HHII", raw, off)
            if t == FILL and raw[off + 12:off + 16] == b"\0\0\0\0":
                struct.pack_into("<HHII", raw, off, DONT_CARE, 0, nb, 12); del raw[off + 12:off + 16]; break
            off += tsz
        open(q, "wb").write(bytes(raw))
    vcase("a DONT_CARE chunk even when it hashes like zeros", to_dont_care, "DONT_CARE")
    vcase_ok = []
    q = os.path.join(tmp, "v.simg"); shutil.copy(good, q)
    try:
        verify(q, "f" * 64, os.path.getsize(d), L)
    except OSError as ex:
        vcase_ok.append(str(ex))
    check("verify compares against the given raw sha", vcase_ok and "expanded sha256" in vcase_ok[0], str(vcase_ok))
    s2 = L.sparse_file_import(os.open(good, os.O_RDONLY), True, False)
    check("libsparse's own expansion hashes to the raw sha256", libsparse_expand_sha(L, s2, os.path.getsize(d)) == sha_d)
    L.sparse_file_destroy(s2)


def _p4_strays_hdd_sigterm():
    global tmp, me, fails, n, L, lp, a, sha_a, b, sha_b, c, sha_c
    # F4: strays are listed and cleaned
    open(os.path.join(cache_dir(), sha_b + ".simg.part999999998"), "w").write("x")
    open(os.path.join(cache_dir(), sha_b + ".simg"), "w").write("orphan")
    rc, o, e = run("status")
    check("status lists a part file and an orphan simg", "part " + sha_b in o and "ORPHAN " + sha_b in o, o)
    rc, o, e = run("gc")
    check("gc removes the orphan simg and the dead part", not os.path.exists(os.path.join(cache_dir(), sha_b + ".simg")) and "orphan" in e, o + e)
    # F5: an unknown or rotating device is refused without the override
    if rotational(cache_dir()) != "0":
        env = dict(os.environ); env.pop("FLASH_CACHE_ALLOW_HDD")
        r = subprocess.run(me + ["prep", b], capture_output=True, text=True, env=env)
        check("prep refuses a cache dir that is not on a known SSD", r.returncode == 3 and "belongs on the SSD" in r.stderr, r.stderr)
    # F4: SIGTERM during prep removes the part file
    big, sha_big = mkimg("big.img", [b"d"] * 6000)
    pr = subprocess.Popen(me + ["prep", big], stderr=subprocess.PIPE, text=True, env=dict(os.environ, FLASH_CACHE_MAX_GB="10", FLASH_CACHE_MAX_ENTRIES="9"))
    for _ in range(200):
        if any(".simg.part" in x for x in os.listdir(cache_dir())):
            break
        time.sleep(0.01)
    pr.terminate(); pr.wait(30)
    check("SIGTERM during prep (killed, rc 143) leaves no part file", pr.returncode == 143 and not any(".simg.part" in x for x in os.listdir(cache_dir())),
          "rc=%s files=%s" % (pr.returncode, os.listdir(cache_dir())))


def _p5_promote():
    global tmp, me, fails, n, L, lp, a, sha_a, b, sha_b, c, sha_c
    std = os.environ["STANDARD_ROOT"]
    big = dict(FLASH_CACHE_MAX_ENTRIES="20", FLASH_CACHE_MAX_GB="10")
    before = open(std).read()
    f, sha_f = mkimg("f.img", [b"d", b"z", b"f"])
    open(f + ".sha256", "w").write("0" * 64 + "  x\n")
    rc, o, e = run("promote", f, env=big)
    check("promote refuses an image that does not match its .sha256, standard-root.txt unchanged", rc == 1 and open(std).read() == before, e)
    g, sha_g = mkimg("g.img", [b"d", b"d", b"z", b"z"])
    rc, o, e = run("promote", g, env=dict(big, FLASH_CACHE_MIN_FREE_GB="100000"))
    check("promote with a failed prep (disk floor) leaves standard-root.txt unchanged", rc == 1 and "NOT PROMOTED" in e and open(std).read() == before, e)
    open(std, "w").write("# header kept\n" + before)
    rc, o, e = run("promote", g, env=big)
    lines = [ln for ln in open(std).read().splitlines() if ln and not ln.startswith("#")]
    check("promote: entry verified first, then standard-root.txt names the new root (header kept)",
          rc == 0 and lines == ["%s %s" % (sha_g, os.path.abspath(g))] and open(std).read().startswith("# header kept") and os.path.exists(simg_path(sha_g)), e + open(std).read())
    hist = open(os.path.join(os.path.dirname(std), "standard-root.history")).read()
    check("promote records old -> new in standard-root.history", sha_g in hist and sha_a in hist, hist)
    rc, o, e = run("status")
    check("after promote the new entry is the protected [standard] one", ("%s [standard]" % sha_g[:12]) in o, o)

