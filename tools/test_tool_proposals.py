#!/usr/bin/env python3
"""Tests for tools/tool-proposals (fixture repos in a tempdir, TP_ROOT)."""
import os, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool-proposals")

BODY = "\n## checked\nx\n\n## why\nx\n\n## output\nx\n\n## risks\nnone\n\n## amendments\n(none)\n"


def prop(n, tool="tools/alpha", status="proposed", extra=""):
    return ('---\nid: TP-%03d\ntool: "%s"\nstatus: %s\nproposed_by: "me (driver)"\ndate: 2026-10-22\n'
            'exposed_by: "a run"\nextends: ""\nrelated: ""\nusage: "%s --x"\nrefs: "-"\n%s---\n' % (n, tool, status, tool, extra)) + BODY


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = self.d.name
        os.makedirs(os.path.join(self.root, "docs", "tool-proposals"))
        os.makedirs(os.path.join(self.root, "tools"))
        open(os.path.join(self.root, "tools", "existing-tool"), "w").close()
        self.put("TP-001-alpha.md", prop(1))
        self.put("TP-002-beta.md", prop(2, "tools/beta", "done"))
        self.index()

    def tearDown(self):
        self.d.cleanup()

    def put(self, name, text):
        with open(os.path.join(self.root, "docs", "tool-proposals", name), "w") as f:
            f.write(text)

    def index(self):
        out = self.run_tool("list", "--index").stdout
        with open(os.path.join(self.root, "docs", "TOOL-PROPOSALS.md"), "w") as f:
            f.write("# Tool proposals\n\n## Index\n\n" + out)

    def run_tool(self, *a):
        return subprocess.run([sys.executable, TOOL, *a], capture_output=True, text=True,
                              env=dict(os.environ, TP_ROOT=self.root))

    def test_prune_stale(self):
        r = self.run_tool("prune", "--stale-days", "0")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("STALE TP-001", r.stdout)
        self.assertEqual(self.run_tool("prune", "--stale-days", "99999").returncode, 0)

    def test_valid(self):
        r = self.run_tool("validate")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RESULT: PASS", r.stdout)

    def test_missing_field(self):
        self.put("TP-002-beta.md", prop(2, "tools/beta", "done").replace("refs: \"-\"\n", ""))
        r = self.run_tool("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing field refs", r.stdout)

    def test_bad_status(self):
        self.put("TP-002-beta.md", prop(2, "tools/beta", "wip"))
        r = self.run_tool("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("status 'wip'", r.stdout)

    def test_duplicate_id(self):
        self.put("TP-003-gamma.md", prop(2, "tools/gamma"))
        r = self.run_tool("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("duplicate id TP-002", r.stdout)

    def test_index_out_of_sync(self):
        self.put("TP-003-gamma.md", prop(3, "tools/gamma"))
        r = self.run_tool("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("index out of sync", r.stdout)

    def test_bad_reference(self):
        self.put("TP-002-beta.md", prop(2, "tools/beta", "done").replace('related: ""', 'related: "TP-009"'))
        self.index()
        r = self.run_tool("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown 'TP-009'", r.stdout)

    def test_check_hit_and_none(self):
        r = self.run_tool("check", "existing")
        self.assertEqual(r.returncode, 1)
        self.assertIn("EXISTS tools/existing-tool", r.stdout)
        r = self.run_tool("check", "alpha")
        self.assertIn("PROPOSED TP-001 (proposed)", r.stdout)
        self.assertIn("RESULT: ALREADY-PROPOSED", r.stdout)
        r = self.run_tool("check", "frobnicate")
        self.assertEqual(r.returncode, 0)
        self.assertIn("RESULT: NONE", r.stdout)

    def test_new_refuses_duplicate_then_creates(self):
        r = self.run_tool("new", "alpha")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "docs", "tool-proposals", "TP-003-alpha.md")))
        r = self.run_tool("new", "frobnicate", "--related", "TP-001")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.strip(), "docs/tool-proposals/TP-003-frobnicate.md")
        # unfilled template + index not updated: validate must fail
        self.assertEqual(self.run_tool("validate").returncode, 1)
        self.assertEqual(self.run_tool("new", "alpha", "--force").returncode, 0)


if __name__ == "__main__":
    unittest.main()
