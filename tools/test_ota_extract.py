#!/usr/bin/env -S python3 -I
"""Host test of tools/ota-extract with a synthetic full payload (REPLACE + REPLACE_XZ + ZERO) in a zip. No phone, no network, seconds.
Includes a killed mutant: the sha256 check of the written image is removed and the bad-hash case must then stop failing."""
import bz2, hashlib, lzma, os, struct, subprocess, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "ota-extract")
BS = 4096


def vi(n):
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        if n: out += bytes([b | 0x80])
        else: return out + bytes([b])


def fv(fn, n): return vi(fn << 3) + vi(n)
def fb(fn, b): return vi(fn << 3 | 2) + vi(len(b)) + b
def ext(s, n): return fb(0, b"")[:0] + (fv(1, s) + fv(2, n))


def op(t, data=None, off=None, extents=(), sha=True):
    m = fv(1, t)
    if data is not None:
        m += fv(2, off) + fv(3, len(data))
    for s, n in extents: m += fb(6, ext(s, n))
    if data is not None and sha: m += fb(8, hashlib.sha256(data).digest())
    return m


def part(name, size, img_hash, ops):
    return fb(13, fb(1, name.encode()) + b"".join(fb(8, o) for o in ops) + fb(7, fv(1, size) + fb(2, img_hash)))


def build(path, minor=0, bad_hash=False, src_copy=False, props=True, bomb=False, oext=1, trail=b""):
    """Partition 'boot': 4 blocks = REPLACE(block0) + REPLACE_XZ(blocks1-2) + ZERO(block3). Partition 'other': REPLACE_BZ block0."""
    b0, b12 = os.urandom(BS), os.urandom(2 * BS)
    img = b0 + b12 + bytes(BS)
    blobs = b""; ops = []
    for t, data, extents in ((0, b0, [(0, 1)]), (8, lzma.compress(b12), [(1, 2)])):
        ops.append(op(t, data, len(blobs), extents)); blobs += data
    ops.append(op(6, extents=[(3, 1)]))
    if src_copy: ops.append(op(4, extents=[(0, 1)]))
    h = hashlib.sha256(img).digest()
    if bad_hash: h = bytes(32)
    o0 = os.urandom(BS); z = bz2.compress(bytes(8 << 20) if bomb else o0) + trail
    parts = part("boot", len(img), h, ops) + part("other", BS, hashlib.sha256(o0).digest(), [op(1, z, len(blobs), [(0, oext)])])
    blobs += z
    manifest = fv(3, BS) + parts + (fv(12, minor) if minor else b"")
    payload = b"CrAU" + struct.pack(">QQI", 2, len(manifest), 0) + manifest + blobs
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("payload.bin", payload)
        if props: zf.writestr("payload_properties.txt", "FILE_HASH=x\n")
        zf.writestr("META-INF/com/android/metadata", "post-build=google/bluejay/bluejay:16/BP31.250610.004/1:user/release-keys\n")
    return img, o0


def run(tool, *args):
    p = subprocess.run([sys.executable, "-I", tool] + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.zip = os.path.join(self.d, "o.zip"); self.out = os.path.join(self.d, "out")

    def test_pass(self):
        img, o0 = build(self.zip)
        rc, o = run(TOOL, self.zip, "--part", "boot", "--part", "other", "--out", self.out)
        self.assertEqual(rc, 0, o); self.assertIn("RESULT: PASS", o)
        self.assertEqual(open(self.out + "/boot.img", "rb").read(), img)
        self.assertEqual(open(self.out + "/other.img", "rb").read(), o0)
        self.assertIn(hashlib.sha256(img).hexdigest() + "  boot.img", open(self.out + "/SHA256SUMS").read())
        self.assertIn("BP31.250610.004", open(self.out + "/manifest.txt").read())

    def test_list(self):
        build(self.zip)
        rc, o = run(TOOL, self.zip, "--list")
        self.assertEqual(rc, 0, o); self.assertIn("REPLACE_XZ:1", o); self.assertIn("ZERO:1", o)

    def bad_hash_fails(self, tool):
        build(self.zip, bad_hash=True)
        rc, o = run(tool, self.zip, "--part", "boot", "--out", self.out)
        gone = not os.path.exists(self.out + "/boot.img")
        return rc == 1 and "RESULT: FAIL" in o and "new_partition_info.hash" in o and gone

    def test_bad_hash(self): self.assertTrue(self.bad_hash_fails(TOOL))

    def test_mutant_without_hash_check_is_killed(self):
        src = open(TOOL).read()
        self.assertEqual(src.count("if got_sha != p[\"hash\"]:"), 1, "mutation site moved: update the test")
        mut = os.path.join(self.d, "ota-extract-mutant")
        open(mut, "w").write(src.replace("if got_sha != p[\"hash\"]:", "if False:"))
        self.assertFalse(self.bad_hash_fails(mut), "mutant (hash check removed) still FAILs: test_bad_hash would not detect it")

    def test_source_copy_in_requested_partition(self):
        build(self.zip, src_copy=True)
        rc, o = run(TOOL, self.zip, "--part", "boot", "--out", self.out)
        self.assertEqual(rc, 1, o); self.assertIn("SOURCE_COPY", o)
        rc, o = run(TOOL, self.zip, "--part", "other", "--out", self.out)   # same payload, the other partition is clean
        self.assertEqual(rc, 0, o)

    def test_incremental(self):
        build(self.zip, minor=1)
        rc, o = run(TOOL, self.zip, "--part", "boot", "--out", self.out)
        self.assertEqual(rc, 1, o); self.assertIn("incremental OTA", o)

    def test_missing_part_and_bad_args(self):
        build(self.zip)
        rc, o = run(TOOL, self.zip, "--part", "nope", "--out", self.out); self.assertEqual(rc, 1, o)
        rc, o = run(TOOL, self.zip, "--part", "../x", "--out", self.out); self.assertEqual(rc, 2, o)
        rc, o = run(TOOL, self.zip); self.assertEqual(rc, 2, o)

    def test_not_a_payload(self):
        with zipfile.ZipFile(self.zip, "w") as zf: zf.writestr("x", "y")
        rc, o = run(TOOL, self.zip, "--list"); self.assertEqual(rc, 2, o)
        with zipfile.ZipFile(self.zip, "w") as zf: zf.writestr("payload.bin", b"NOPE" + bytes(40))
        rc, o = run(TOOL, self.zip, "--list"); self.assertEqual(rc, 1, o); self.assertIn("CrAU", o)

    def test_compressed_payload_refused(self):
        with zipfile.ZipFile(self.zip, "w", zipfile.ZIP_DEFLATED) as zf: zf.writestr("payload.bin", b"CrAU" + bytes(100))
        rc, o = run(TOOL, self.zip, "--list"); self.assertEqual(rc, 1, o); self.assertIn("not STORED", o)

    # --- review golden-bp31-r1 findings F1-F5 ---
    def mutant(self, *subs):
        src = open(TOOL).read()
        for a, b in subs:
            self.assertIn(a, src, "mutation site moved: update the test"); src = src.replace(a, b)
        mut = os.path.join(self.d, "ota-extract-mutant"); open(mut, "w").write(src)
        return mut

    def git_case(self, tool):
        build(self.zip); repo = os.path.join(self.d, "repo"); os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        out = os.path.join(repo, "sub", "fw")
        rc, o = run(tool, self.zip, "--part", "boot", "--out", out)
        return rc, o, os.path.exists(os.path.join(out, "boot.img")), os.path.exists(os.path.join(repo, "sub"))

    def test_f1_git_tree_refused(self):
        rc, o, img, sub = self.git_case(TOOL)
        self.assertEqual(rc, 1, o); self.assertIn("RESULT: FAIL", o); self.assertIn("git work tree", o)
        self.assertFalse(img); self.assertFalse(sub, "refusal must come before any mkdir")

    def test_f1_mutant_without_git_check_is_killed(self):
        rc, o, img, sub = self.git_case(self.mutant(("    check_out(out)\n", "    pass\n")))
        self.assertEqual(rc, 0, "mutant should extract into the repo: " + o); self.assertTrue(img)

    def symlink_case(self, tool):
        build(self.zip); os.makedirs(self.out); victim = os.path.join(self.d, "victim"); open(victim, "w").write("keep")
        os.symlink(victim, self.out + "/boot.img")
        rc, o = run(tool, self.zip, "--part", "boot", "--out", self.out)
        return rc, o, open(victim).read()

    def test_f2_symlink_refused(self):
        rc, o, v = self.symlink_case(TOOL)
        self.assertEqual(rc, 1, o); self.assertIn("symlink", o); self.assertEqual(v, "keep")
        for nm in ("SHA256SUMS", "manifest.txt"):   # symlinked manifest outputs are never written through either
            for f in os.listdir(self.out): os.unlink(os.path.join(self.out, f))
            os.symlink(os.path.join(self.d, "victim"), self.out + "/" + nm)
            rc, o = run(TOOL, self.zip, "--part", "boot", "--out", self.out)
            self.assertEqual(open(os.path.join(self.d, "victim")).read(), "keep", o)
        link = os.path.join(self.d, "linkout"); os.symlink(self.out, link)
        rc, o = run(TOOL, self.zip, "--part", "boot", "--out", link); self.assertEqual(rc, 1, o)

    def test_f2_mutant_without_symlink_checks_is_killed(self):
        mut = self.mutant(("os.path.islink(q)", "False"), ("os.path.islink(out)", "False"), ("os.O_NOFOLLOW", "0"))
        rc, o, v = self.symlink_case(mut)
        self.assertNotEqual((rc, v), (1, "keep"), "mutant still refuses: test_f2_symlink_refused would not detect it")

    def test_f2_stale_manifest_removed_on_failure(self):
        build(self.zip); rc, o = run(TOOL, self.zip, "--part", "boot", "--out", self.out); self.assertEqual(rc, 0, o)
        build(self.zip, bad_hash=True); rc, o = run(TOOL, self.zip, "--part", "boot", "--out", self.out); self.assertEqual(rc, 1, o)
        self.assertFalse(os.path.exists(self.out + "/SHA256SUMS")); self.assertFalse(os.path.exists(self.out + "/manifest.txt"))

    def test_f3_bz2_bomb_refused(self):
        build(self.zip, bomb=True)
        rc, o = run(TOOL, self.zip, "--part", "other", "--out", self.out)
        self.assertEqual(rc, 1, o); self.assertIn("expands beyond", o)
        self.assertFalse(os.path.exists(self.out + "/other.img"))

    def test_f3_size_cap(self):
        build(self.zip)
        src = open(TOOL).read().replace("MAX_PART = 16 << 30", "MAX_PART = 100")
        mut = os.path.join(self.d, "capped"); open(mut, "w").write(src)
        rc, o = run(mut, self.zip, "--part", "boot", "--out", self.out); self.assertEqual(rc, 1, o); self.assertIn("cap", o)

    def test_r2_f3_extents_beyond_partition_refused_before_decompress(self):
        build(self.zip, bomb=True, oext=4096)   # extents claim 16 MB in a 4 KB partition: the bomb must never be expanded
        rc, o = run(TOOL, self.zip, "--part", "other", "--out", self.out)
        self.assertEqual(rc, 1, o); self.assertIn("beyond the partition size", o); self.assertNotIn("expands beyond", o)

    def test_r2_trailing_data_after_stream_refused(self):
        build(self.zip, trail=b"junk")
        rc, o = run(TOOL, self.zip, "--part", "other", "--out", self.out)
        self.assertEqual(rc, 1, o); self.assertIn("after the compressed stream", o)

    def test_f4_part_removed_on_sha_mismatch(self):
        build(self.zip, bad_hash=True)
        rc, o = run(TOOL, self.zip, "--part", "boot", "--out", self.out)
        self.assertEqual(rc, 1, o); self.assertEqual(os.listdir(self.out), [])


if __name__ == "__main__":
    unittest.main()
