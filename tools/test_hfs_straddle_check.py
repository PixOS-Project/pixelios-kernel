"""tools/hfs-straddle-check.py on SYNTHETIC HFSX images (catalog nodeSize 8192 on 4096 blocks): contiguous passes, a node split across
two VH extents fails, a node split at a VH/overflow-extent boundary fails, a clean overflow passes, garbage is exit 2.
Real-image run (optional, the gate does not need it): tools/hfs-straddle-check.py /tank/pixelios/ios-root3-inj3.img must FAIL with
3 catalog nodes (12306, 16402, 16411: research 50 item 5, observed on a host image), ios-root4-inj3.img must PASS."""
import os, struct, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hfs-straddle-check.py")
B = 4096


def fork(total, ex):
    ex = list(ex) + [(0, 0)] * (8 - len(ex))
    return struct.pack(">QII", total * B, 0, total) + b"".join(struct.pack(">II", s, c) for s, c in ex)


def btree(ns, total, nrec_leaf=None, recs=()):
    """node 0 header + node 1 (leaf, with recs) ; padded to total nodes"""
    n0 = bytearray(ns)
    n0[8] = 1
    struct.pack_into(">H", n0, 32, ns)
    struct.pack_into(">I", n0, 36, total)
    img = bytearray(n0)
    n1 = bytearray(ns)
    n1[8] = 255
    struct.pack_into(">H", n1, 10, len(recs))
    o, offs = 14, []
    for r in recs:
        offs.append(o)
        n1[o:o + len(r)] = r
        o += len(r)
    for i, off in enumerate(offs):
        struct.pack_into(">H", n1, ns - 2 - 2 * i, off)
    img += n1
    img += bytes(ns * (total - 2))
    return bytes(img)


def make(path, cat_vh_ext, cat_total, overflow_exts=None, cat_blocks_at=None):
    """catalog: 4 nodes of 8192 = 8 blocks. extents file: 1 extent (10,2), nodeSize 4096. overflow_exts: [(sb,[ext])] for fileID 4."""
    cat_ns, cat_nodes = 8192, 4
    img = bytearray(100 * B)
    vh = bytearray(512)
    vh[0:2] = b"HX"
    struct.pack_into(">I", vh, 40, B)
    vh[192:272] = fork(2, [(10, 2)])
    vh[272:352] = fork(cat_total, cat_vh_ext)
    img[1024:1536] = vh
    recs = []
    for sb, ex in overflow_exts or []:
        k = struct.pack(">HBBII", 10, 0, 0, 4, sb)
        recs.append(k + b"".join(struct.pack(">II", s, c) for s, c in (list(ex) + [(0, 0)] * 8)[:8]))
    img[10 * B:12 * B] = btree(4096, 2, recs=recs)
    cat = btree(cat_ns, cat_nodes)
    phys = [s + k for s, c in cat_vh_ext + [e for _, ex in overflow_exts or [] for e in ex] for k in range(c)]
    for lb, p in enumerate(phys):
        img[p * B:(p + 1) * B] = cat[lb * B:(lb + 1) * B]
    with open(path, "wb") as f:
        f.write(img)


def run(*a):
    r = subprocess.run([sys.executable, "-I", TOOL, *a], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout


class Straddle(unittest.TestCase):
    def img(self, **kw):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "t.img")
        make(p, **kw)
        return p

    def test_contiguous_passes(self):
        rc, out = run(self.img(cat_vh_ext=[(20, 4), (30, 4)], cat_total=8))
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS 0 ", out)

    def test_split_node_fails(self):
        rc, out = run(self.img(cat_vh_ext=[(20, 3), (30, 5)], cat_total=8))     # node 1 = logical blocks 2,3 -> phys 22,30
        self.assertEqual(rc, 1, out)
        self.assertIn("catalog: nodeSize 8192 blockSize 4096 nodes 4 straddling 1 [1]", out)
        self.assertIn("RESULT: FAIL 1 ", out)

    def test_overflow_boundary_fails(self):
        rc, out = run(self.img(cat_vh_ext=[(20, 3)], cat_total=8, overflow_exts=[(3, [(40, 5)])]))
        self.assertEqual(rc, 1, out)
        self.assertIn("straddling 1 [1]", out)

    def test_overflow_clean_passes(self):
        rc, out = run(self.img(cat_vh_ext=[(20, 2), (30, 2)], cat_total=8, overflow_exts=[(4, [(40, 4)])]))
        self.assertEqual(rc, 0, out)

    def test_garbage_is_exit_2(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "g.img")
        with open(p, "wb") as f:
            f.write(bytes(8192))
        rc, out = run(p)
        self.assertEqual(rc, 2, out)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("RESULT: ERROR"))
        self.assertEqual(run()[0], 2)


if __name__ == "__main__":
    unittest.main()
