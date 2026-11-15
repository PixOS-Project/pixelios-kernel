#!/usr/bin/env python3
"""tools/gate_select.py tree_state: __pycache__/*.pyc must not change g (their bytes differ per checkout: embedded source path). Before this, every checkout had its own g, the host
gate-pass registry never matched across checkouts, and quick gates ran the full gate (61 of 65 in the 24 h to 2026-11-03)."""
import pathlib, shutil, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_select as g


class TreeState(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        self.tp = d / "tools" / "third_party"
        (self.tp / "gki" / "__pycache__").mkdir(parents=True)
        (self.tp / "mkbootimg.py").write_text("x")
        self.addCleanup(setattr, g, "REPO", g.REPO)
        g.REPO = d

    def test_pyc_bytes_do_not_change_g(self):
        base = g.tree_state()[0]
        pyc = self.tp / "gki" / "__pycache__" / "a.cpython-313.pyc"
        pyc.write_bytes(b"from checkout one")
        self.assertEqual(g.tree_state()[0], base)
        pyc.write_bytes(b"from checkout two")
        self.assertEqual(g.tree_state()[0], base)

    def test_real_third_party_change_changes_g(self):
        base = g.tree_state()[0]
        (self.tp / "mkbootimg.py").write_text("y")
        self.assertNotEqual(g.tree_state()[0], base)

    def test_a_py_file_inside_pycache_still_counts(self):
        base = g.tree_state()[0]
        (self.tp / "gki" / "__pycache__" / "not_bytecode.py").write_text("z")
        self.assertNotEqual(g.tree_state()[0], base)   # the same exclusion as tools/gate-identity: only *.pyc


if __name__ == "__main__":
    unittest.main()
