#!/usr/bin/env python3
"""Gate tests must not use a fixed $HOME path (AP-210, audit row A33: three test files wrote ~/pixelios-artifacts/scratch, which is not
writable in the gate container and collides between parallel runs). Scans tools/test_*.py and tools/test_*.sh for /home/<user>/, ~/,
expanduser("~..."), Path.home() and $HOME. A hit is allowed when
  - the file points HOME at a scratch dir itself (Python: the string "HOME" appears; shell: a `HOME=` assignment), or
  - the line takes the path from the environment with a default (os.environ / getenv / ${VAR:-...} / `x or os.path.expanduser`), or
  - the line is a comment.
BASELINE lists the files that hit today with the reason each is acceptable; it only shrinks (a baseline file that no longer hits fails
this test, so delete its row). A new test file with a fixed home path fails here, in the gate's unit step. A grep is a start, not
proof: it cannot tell a write from a read, so a read-only use goes to BASELINE with a reason a reviewer can check."""
import glob, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = re.compile(r"/home/[A-Za-z0-9_.-]+/|(?<![\w.$])~/|expanduser\(\s*[\"']~|Path\.home\(\)|\$\{?HOME\b")
GUARD = re.compile(r"os\.environ|\$\{[A-Za-z_]+:[-=]|getenv|\bor\s+os\.path\.expanduser")
SET_PY = re.compile(r"[\"']HOME[\"']")
SET_SH = re.compile(r"(^|[\s;])(export\s+)?HOME=", re.M)

BASELINE = {
    "test_el2_mutants.py": "hit is the docstring line that cites AP-210 itself",
    "test_golden_gpu_analyze.py": "reads one fixed golden capture under ~/pixelios-artifacts, writes nothing there",
    "test_hermetic_inputs.py": "the hit is a string fixture of the scanner's own self-test",
    "test_kernel_hooks.sh": "absolute paths are data compared against claude-settings.json, never written (SUPER_REAL is env-overridable)",
    "test_qrl2.py": "sys.path.insert of ~/pixelios-artifacts/pylib: a read-only import path",
}


def hits(name, text):
    """[(line number, stripped line)] of fixed-home uses in a test file; [] when the file sets HOME itself."""
    if (SET_PY if name.endswith(".py") else SET_SH).search(text):
        return []
    return [(i, l.strip()[:120]) for i, l in enumerate(text.splitlines(), 1)
            if HOME.search(l) and not l.lstrip().startswith("#") and not GUARD.search(l)]


class NoFixedHome(unittest.TestCase):
    def test_no_new_fixed_home_paths(self):
        bad, stale = [], []
        for f in sorted(glob.glob(os.path.join(HERE, "test_*.py")) + glob.glob(os.path.join(HERE, "test_*.sh"))):
            name = os.path.basename(f)
            with open(f, errors="replace") as fh:
                h = hits(name, fh.read())
            if h and name not in BASELINE:
                bad += ["%s:%d: %s" % (name, n, l) for n, l in h]
            if not h and name in BASELINE:
                stale.append(name)
        self.assertEqual(bad, [], "tests use a fixed $HOME path (AP-210): create a tempfile.mkdtemp()/mktemp -d dir, set HOME to it for the "
                                  "tool under test, or take the path from an env var with a default")
        self.assertEqual(stale, [], "BASELINE rows that no longer hit: delete them from tools/test_no_fixed_home.py")
        for name in BASELINE:
            self.assertTrue(os.path.isfile(os.path.join(HERE, name)), "BASELINE names a missing file: " + name)

    # fixtures: the scanner itself. Strings are built so this file does not match its own patterns when scanned.
    def test_deny_fixtures(self):
        t = "~" + "/pixelios-artifacts/scratch"
        self.assertTrue(hits("t.py", 'p = os.path.expanduser("%s")\nopen(p + "/x", "w")' % t))
        self.assertTrue(hits("t.py", 'p = pathlib.Path.' + 'home() / "scratch"'))
        self.assertTrue(hits("t.py", 'p = "/ho' + 'me/someone/scratch"'))
        self.assertTrue(hits("t.sh", 'mkdir -p "$HO' + 'ME/pixelios-artifacts/scratch"'))
        self.assertTrue(hits("t.sh", 'out=%s/x' % t))

    def test_allow_fixtures(self):
        t = "~" + "/pixelios-artifacts/scratch"
        # the test points HOME at a scratch dir itself
        self.assertEqual(hits("t.py", 'env = dict(os.environ, HO' + 'ME=d)\nenv["HO' + 'ME"] = d\np = os.path.expanduser("%s")' % t), [])
        self.assertEqual(hits("t.sh", 'export HO' + 'ME=$w/home\nmkdir -p "$HO' + 'ME/pixelios-artifacts"'), [])
        self.assertEqual(hits("t.sh", 'w=$(mktemp -d); HO' + 'ME=$w/home tools/x; ls "$HO' + 'ME"'), [])
        # env override with a default, comments, and a path that merely contains a tilde
        self.assertEqual(hits("t.py", 'D = os.environ.get("D") or os.path.expanduser("%s")' % t), [])
        self.assertEqual(hits("t.sh", 'R=${R:-$HO' + 'ME/x}'), [])
        self.assertEqual(hits("t.py", '# reads ' + t), [])
        self.assertEqual(hits("t.py", 'x = "a~b"\ny = tempfile.mkdtemp()'), [])


if __name__ == "__main__":
    unittest.main()
