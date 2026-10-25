"""tools/check_rules r01/r03/r06/r21/r22: replay fixtures (tools/testdata/check-rules/RNN/{fail,pass}, taken once with git show, headed with their
source; no git at test time) and one killing mutant per pattern (CR-02: disable it, the fail fixture must stop failing). R03 has no replay pair
(backlog row 7: de66867 is still literal), so it uses synthetic samples."""
import json, pathlib, re, shutil, sys, tempfile, unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from check_rules import r01_vacuous as r01, r03_derived as r03, r06_ppnum as r06, r21_retain as r21, r22_irqmask as r22

FIX = TOOLS / "testdata" / "check-rules"


def run(mod, fixture, dest, **kw):
    """Findings of mod.check on one fixture file placed at dest (repo-relative) under a temp root; pattern names (2nd word of msg), as a sorted list."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture, root / dest)
        return sorted(f.msg.split()[1].rstrip(":") for f in mod.check(root, **kw))


def tmp_tree(files):
    d = tempfile.TemporaryDirectory()
    for rel, text in files.items():
        p = pathlib.Path(d.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return d


class Fixtures(unittest.TestCase):
    def test_every_fixture_is_headed_with_its_source(self):
        for rule in ("R01", "R06", "R21", "R22"):
            for p in (FIX / rule).rglob("*"):
                if p.is_file():
                    self.assertIn("fixture source:", p.read_text().splitlines()[0], p.name)

    def test_r01_c(self):
        self.assertEqual(run(r01, FIX / "R01/fail/kernel-4a456b8-wlanlink_host_test.c.txt", "shim/wlanlink_host_test.c"), ["or-true"])
        self.assertEqual(run(r01, FIX / "R01/pass/kernel-fixed-wlanlink_host_test.c.txt", "shim/wlanlink_host_test.c"), [])

    def test_r06(self):
        dest = "xnu/patches/0108-x.patch"
        self.assertEqual(run(r06, FIX / "R06/fail/synthetic-72f1323-0108.patch.txt", dest), ["shift-page-shift"])
        for p in (FIX / "R06/pass").iterdir():          # the real fix keeps the text in a '+' comment: comments are ignored
            self.assertEqual(run(r06, p, dest), [], p.name)

    def test_r21(self):
        dest = "xnu/patches/0105-x.patch"
        self.assertEqual(run(r21, FIX / "R21/fail/kernel-7500955^-0105.patch.txt", dest), ["pass-unretained"])
        self.assertEqual(run(r21, FIX / "R21/pass/kernel-7500955-0105.patch.txt", dest), [])

    def test_r22(self):
        dest = "xnu/patches/0104-x.patch"
        self.assertEqual(run(r22, FIX / "R22/fail/kernel-7500955^-0104.patch.txt", dest), ["nonzero-irq-mask"])
        self.assertEqual(run(r22, FIX / "R22/pass/kernel-7500955-0104.patch.txt", dest), [])


class Synthetic(unittest.TestCase):
    def test_r01_python(self):
        src = "def f():\n    assert x\n\ndef check_a():\n    def inner():\n        assert y\n    if z:\n        raise ValueError\n"
        self.assertEqual(r01.bare_asserts(src, None), [2, 6])
        self.assertEqual(r01.bare_asserts(src, "check_"), [6])
        d = tmp_tree({"tools/gen_wlan_seq.py": src, "tools/test-bluejay-boot.py": src})
        got = sorted((f.path, f.line) for f in r01.check(d.name))
        d.cleanup()
        self.assertEqual(got, [("tools/gen_wlan_seq.py", 2), ("tools/gen_wlan_seq.py", 6), ("tools/test-bluejay-boot.py", 6)])

    def test_r01_c_variants(self):
        for l, bad in [("EXPECT(a || 1);", True), ("CHECK_X(a || true );", True), ("EXPECT(a || b);", False), ("EXPECT(a || 10);", False), ("x = a || 1;", False)]:
            self.assertEqual(bool(r01.C_PATTERNS["or-true"][0].search(l)), bad, l)

    def test_r21_variants(self):
        def hits(*body):
            return [n for _, n in r21.scan(["+" + b for b in body])]
        self.assertEqual(hits("IOMemoryDescriptor *md = s->memory;", "md->retain();"), [])
        self.assertEqual(hits("IOMemoryDescriptor *md = s->memory;", "x();"), ["local-unretained"])
        self.assertEqual(hits("fBo[slot].md = s->memory;"), ["store-unretained"])
        self.assertEqual(hits("s->memory->retain();", "fBo[slot].md = s->memory;"), [])
        self.assertEqual(hits("OSSharedPtr<IOMemoryDescriptor> md = s->memory;"), [])
        self.assertEqual(hits("/* mapMemory(s->memory, 0) */"), [])
        self.assertEqual(hits("ret = mapMemory(s->memory, len, 0);"), ["pass-unretained"])

    def test_r22_variants(self):
        def hits(*body, **kw):
            return r22.scan(["+" + b for b in body], **kw)
        self.assertEqual(hits("wr(R_GPU_IRQ_MASK, 0);"), [])
        self.assertEqual(hits("wr(R_GPU_IRQ_MASK, 0u);"), [])
        self.assertEqual(hits("wr(R_JOB_IRQ_MASK, 0xFFFFFFFFu);"), [1])
        self.assertEqual(hits("wr(R_GPU_IRQ_MASK, GPU_IRQ_RESET_COMPLETED);"), [1])
        self.assertEqual(hits("ml_install_interrupt_handler(x);", "wr(R_GPU_IRQ_MASK, 1);"), [])
        self.assertEqual(hits("/* wr(R_GPU_IRQ_MASK, 1); */"), [])
        self.assertEqual(hits("wr(R_GPU_IRQ_CLEAR, 1);"), [])

    def test_r03(self):
        names = ["GPP14", "GPH2", "PMA", "PCS", "ELBI", "IA", "DBI", "PMU", "ATU", "CFG", "MEM"]
        d = tmp_tree({"soc/google/gs101/wlan_seq.json": json.dumps({"blocks": names}),
                      "tools/a.py": 'for b in ("PMA", "PCS", "ELBI", "IA", "DBI"):\n    pass\nx = ("PMA", "PCS")\ny = ["PMA", "foo", "PCS", "IA"]\n',
                      "shim/w.c": "/* S00..S36 */\nint x; /* S00-S36 */\n",
                      "docs/n.md": "teardown at wlan_seq.json:1412 performs\nsoc.json:7\nT14 (CFG 0x80)\n",
                      "docs/CODING-RULES.md": "wlan_seq.json:1412\n"})
        got = sorted((f.path, f.line, f.msg.split()[1].rstrip(":")) for f in r03.check(d.name))
        self.assertEqual(got, [("docs/n.md", 1, "json-cite"), ("docs/n.md", 2, "json-cite"), ("shim/w.c", 1, "step-range"), ("tools/a.py", 1, "block-tuple")])
        for kind in ("block-tuple", "step-range", "json-cite"):     # CR-02: each kind is needed
            left = {f.msg.split()[1].rstrip(":") for f in r03.check(d.name, kinds=tuple(k for k in ("block-tuple", "step-range", "json-cite") if k != kind))}
            self.assertNotIn(kind, left)
            self.assertEqual(len(left), 2, kind)
        d.cleanup()


class Mutants(unittest.TestCase):
    """Disable one pattern: its fail fixture must stop failing, so Fixtures would catch the mutant."""
    def test_r01(self):
        self.assertEqual(run(r01, FIX / "R01/fail/kernel-4a456b8-wlanlink_host_test.c.txt", "shim/wlanlink_host_test.c", c_patterns={}), [])
        src = "def check_a():\n    assert x\n"
        with mock.patch.object(r01, "bare_asserts", return_value=[]):
            d = tmp_tree({"tools/boards.py": src})
            self.assertEqual(r01.check(d.name), [])      # the AST scan is the only source of the python finding
            d.cleanup()
        d = tmp_tree({"tools/boards.py": src})
        self.assertEqual(len(r01.check(d.name)), 1)
        self.assertEqual(r01.check(d.name, py=False), [])
        d.cleanup()

    def test_r06(self):
        self.assertEqual(run(r06, FIX / "R06/fail/synthetic-72f1323-0108.patch.txt", "xnu/patches/0108-x.patch", patterns={}), [])
        with mock.patch.object(r06, "strip_c_comments", lambda s: s):     # comment stripping is needed: the real fix has the text in a comment
            self.assertEqual(run(r06, FIX / "R06/pass/kernel-72f1323-0108.patch.txt", "xnu/patches/0108-x.patch"), ["shift-page-shift"])

    def test_r21(self):
        on = {k: v for k, v in r21.PATTERNS.items() if k != "pass-unretained"}
        self.assertEqual(run(r21, FIX / "R21/fail/kernel-7500955^-0105.patch.txt", "xnu/patches/0105-x.patch", enabled=on), [])
        # the other two patterns have no replay line (known gap): killed by the synthetic cases below
        lines = ["+IOMemoryDescriptor *md = s->memory;", "+x();", "+fBo[1].md = s->memory;"]
        self.assertEqual(sorted(n for _, n in r21.scan(lines)), ["local-unretained", "store-unretained"])
        for name in ("local-unretained", "store-unretained"):
            self.assertNotIn(name, [n for _, n in r21.scan(lines, {k: 1 for k in r21.PATTERNS if k != name})])

    def test_r22(self):
        dest = "xnu/patches/0104-x.patch"
        fail = FIX / "R22/fail/kernel-7500955^-0104.patch.txt"
        self.assertEqual(run(r22, fail, dest, handler_rx=re.compile(r"\bwr\b")), [])                # a handler match anywhere suppresses
        with mock.patch.object(r22, "ZERO_RX", re.compile(r"(?!)")):                                # zero must be exempt
            self.assertIn("nonzero-irq-mask", run(r22, FIX / "R22/pass/kernel-7500955-0104.patch.txt", dest))
        with mock.patch.object(r22, "WRITE_RX", re.compile(r"(?!)")):
            self.assertEqual(run(r22, fail, dest), [])


class TrackedOnly(unittest.TestCase):
    """lib.tracked: in a git work tree an untracked violating file is not scanned; a non-git dir is scanned whole."""
    BAD = "static void f(void) { int x = (1 ? 2 : 3); }\n"

    def test_untracked_skipped_and_nongit_scans_all(self):
        from check_rules import lib
        import subprocess
        files = {"a/tracked.c": self.BAD, "a/untracked.c": self.BAD}
        with tmp_tree(files) as d:
            self.assertEqual([r for r, _ in lib.tracked(d, "a/*.c")], ["a/tracked.c", "a/untracked.c"])   # non-git: everything
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            subprocess.run(["git", "-C", d, "add", "a/tracked.c"], check=True)
            self.assertEqual([r for r, _ in lib.tracked(d, "a/*.c")], ["a/tracked.c"])


if __name__ == "__main__":
    unittest.main()
