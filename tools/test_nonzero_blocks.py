"""tools/nonzero-blocks.py on a small SYNTHETIC sparse image (holes, zero-filled data blocks, non-zero blocks, a short last block)."""
import os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "nonzero-blocks.py")
B = 4096


def run(path):
    r = subprocess.run([sys.executable, "-I", TOOL, path], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return tuple(map(int, r.stdout.split()))


class NonzeroBlocks(unittest.TestCase):
    def img(self, build):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "t.img")
        with open(p, "wb") as f:
            build(f)
        return p

    def test_mixed(self):
        def build(f):
            f.truncate(1000 * B)                      # 1000 blocks, all hole
            f.seek(3 * B); f.write(b"\x01" * B)       # block 3 non-zero
            f.seek(10 * B); f.write(bytes(B))         # block 10: explicit zeros (data extent, still zero)
            f.seek(500 * B + 4095); f.write(b"\x02")  # block 500: only its last byte is non-zero
            f.seek(700 * B); f.write(b"\x03" * (3 * B))  # blocks 700-702
        self.assertEqual(run(self.img(build)), (5, 1000))

    def test_all_zero_and_short_tail(self):
        def build(f):
            f.truncate(2 * B + 10)
        self.assertEqual(run(self.img(build)), (0, 3))

    def test_short_tail_nonzero(self):
        def build(f):
            f.truncate(2 * B + 10)
            f.seek(2 * B + 9); f.write(b"\x07")
        self.assertEqual(run(self.img(build)), (1, 3))

    def test_script_uses_helper(self):
        s = open(os.path.join(HERE, "fflash-userdata.sh")).read()
        self.assertIn("tools/nonzero-blocks.py", s)
        self.assertNotIn("stat -c %b", s)


if __name__ == "__main__":
    unittest.main()
