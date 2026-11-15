#!/usr/bin/env python3
"""Tests for tools/tool-census (fake kernel + super repos in a tempdir, TOOL_CENSUS_KERNEL / TOOL_CENSUS_SUPER)."""
import json, os, shutil, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool-census")
HDR = "tool\tstatus\treplaced_by\treason\tevidence\tsince\n"
LHDR = "date\ttool\told\tnew\treason\tevidence\n"
EV = "docs/reviews/x-r1.md"
TODAY = "2026-11-20"


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.k, self.s = os.path.join(self.d, "k"), os.path.join(self.d, "s")
        for p in ("k/tools", "k/docs/tool-proposals", "s/docs/runplans"):
            os.makedirs(os.path.join(self.d, p))
        self.put(self.s, "AGENTS.md", "x\n")
        self.put(self.s, "docs/reviews/x-r1.md", "r\n")
        self.put(self.s, "docs/reviews/rm-r1.md", "r\n")
        self.put(self.s, "docs/ANTIPATTERNS.md", "## AP-150 trap\n")
        self.no_md = False
        self.put(self.k, "AGENTS.md", "x\n")
        self.put(self.k, "tools/tools-status.tsv", HDR)
        self.names, self.rows, self.auto_life = [], [], True
        self.life_rows()

    def tearDown(self):
        shutil.rmtree(self.d)

    def put(self, root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def tool(self, name, text="#!/bin/sh\n"):
        self.put(self.k, "tools/" + name, text)
        if not name.startswith("test_"):
            self.names.append(name)
            self.life_rows()

    def status(self, *rows):
        """rows: (tool, status, replaced_by, reason, since); evidence EV is inserted"""
        self.rows = [r[:4] + (EV,) + r[4:] for r in rows]
        self.put(self.k, "tools/tools-status.tsv", HDR + "".join("\t".join(r) + "\n" for r in self.rows))
        self.life_rows()

    def life_rows(self, *extra):
        """auto lifecycle log matching tools + status rows (each change with a reason + EV), plus extra raw rows"""
        if not self.auto_life and not extra:
            return
        st = {r[0]: r for r in self.rows}
        out = []
        for n in self.names:
            out.append(("2026-10-28", n, "-", "active", "new tool, test", "test_x"))
            if n in st and st[n][1] != "active":
                out.append((st[n][5], n, "active", st[n][1], st[n][3] or "why", EV))
        self.put(self.k, "tools/tools-lifecycle.tsv", LHDR + "".join("\t".join(r) + "\n" for r in out + list(extra)))

    def run_(self, *args, today=TODAY):
        if "--check" in args and not self.no_md:
            self.run_("--write", today=today)
        env = dict(os.environ, TOOL_CENSUS_KERNEL=self.k, TOOL_CENSUS_SUPER=self.s, TOOL_CENSUS_TODAY=today)
        r = subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout

    def test_caller_active(self):
        self.tool("foo.sh")
        self.put(self.s, "docs/runplans/x.md", "run tools/foo.sh now\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS tools=1 active=1 deprecated=0 allowlisted=0 nocaller=0 doc_only=0", out)

    def test_nocaller_fails_then_allowlist_passes(self):
        self.tool("lonely")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/lonely", out)
        self.assertIn("nocaller=1", out)
        self.status(("lonely", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("allowlisted=1", out)

    def test_allowlist_needs_reason_and_row_needs_tool(self):
        self.tool("lonely")
        self.status(("lonely", "allowlisted", "", "", "2026-10-28"), ("ghost", "active", "", "", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("ALLOWLIST-NO-REASON tools/lonely", out)
        self.assertIn("STATUS-ROW-NO-TOOL tools/ghost", out)

    def test_deprecated_without_caller_is_not_nocaller(self):
        self.tool("old", "#!/bin/sh\necho 'DEPRECATED: use nothing (tools/tools-status.tsv)' >&2\nexit 2\n")
        self.status(("old", "deprecated", "", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_("--check", today="2026-12-15")
        self.assertEqual(rc, 1)
        self.assertIn("STALE-DEPRECATED tools/old", out)

    def test_deprecated(self):
        self.tool("old", "#!/bin/sh\necho 'DEPRECATED: use new (tools/tools-status.tsv)' >&2\nexit 2\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "deprecated", "new", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_("--check", today="2026-12-15")
        self.assertEqual(rc, 1)
        self.assertIn("STALE-DEPRECATED tools/old", out)

    def test_deprecated_without_line(self):
        self.tool("old", "#!/bin/sh\n" + "# pad\n" * 20 + "# DEPRECATED: use new\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.status(("old", "deprecated", "new", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NO-DEPRECATED-LINE tools/old", out)

    def tp(self, n, date, checked):
        self.put(self.k, "docs/tool-proposals/TP-%03d-x.md" % n,
                 "---\nid: TP-%03d\ntool: \"tools/x\"\nstatus: done\ndate: %s\n---\n\n## checked\n%s\n\n## why\nw\n" % (n, date, checked))

    def test_tp_rule(self):
        self.tp(50, "2026-10-01", "nothing")  # backdated: still checked (number only)
        self.tp(5, "2026-10-01", "nothing")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("TP-NO-CALLER-OR-REPLACES docs/tool-proposals/TP-050-x.md", out)
        self.assertNotIn("TP-005", out)
        self.tp(50, "2026-10-28", "**Caller:**   \n**Replaces:** \n")  # empty values
        rc, out = self.run_("--check")
        self.assertIn("TP-NO-CALLER-OR-REPLACES", out)
        self.tp(50, "2026-10-28", "**Caller:** a.")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.tp(50, "2026-10-28", "**Replaces:** b.")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)

    def test_doc_only_warns(self):
        self.tool("docced")
        self.put(self.k, "docs/A.md", "see tools/docced\n")
        self.status(("docced", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN DOC-ONLY tools/docced", out)
        self.assertIn("doc_only=0 exempt=1", out)   # T-584b: an exempt row moves the flag from doc_only to exempt
        self.status()
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("doc_only=1 exempt=0", out)   # no row: counted

    def test_exempt_counts_separately_from_unexempted(self):   # T-584b decision A
        self.tool("dead")
        self.tool("dead2", "#!/bin/sh\necho 'DEPRECATED: use nothing (tools/tools-status.tsv)' >&2\nexit 2\n")
        self.tool("docced")
        self.put(self.k, "docs/A.md", "see tools/docced\n")
        self.status(("dead", "allowlisted", "", "backlog", "2026-10-28"), ("dead2", "deprecated", "none", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("nocaller=0 doc_only=1 exempt=2", out)   # docced has no row: still counted
        self.status(("dead", "allowlisted", "", "backlog", "2026-10-28"), ("dead2", "deprecated", "none", "reason long enough", "2026-11-15"),
                    ("docced", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertIn("nocaller=0 doc_only=0 exempt=3", out)
        rc, out = self.run_("--check", today="2026-12-15")   # STALE-DEPRECATED still forces removal: the exemption is not a free pass
        self.assertEqual(rc, 1)
        self.assertIn("STALE-DEPRECATED tools/dead2", out)

    def test_duplicate_hint(self):
        words = "# alpha bravo charlie delta echoes foxtrot golfer hotel india juliet\n"
        self.tool("one", "#!/bin/sh\n" + words)
        self.tool("two", "#!/bin/sh\n" + words)
        self.tool("foo", "#!/bin/sh\n# zzzz\n")
        self.tool("foo-v2", "#!/bin/sh\n# yyyy\n")
        self.put(self.s, "docs/runplans/x.md", "one two foo foo-v2\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("DUPLICATE? tools/one ~ tools/two", out)
        self.assertIn("DUPLICATE? tools/foo ~ tools/foo-v2", out)

    def test_tests_not_callers_and_write(self):
        self.tool("bar.py")
        self.tool("test_bar.py", "import bar\n")
        self.put(self.k, "tools/data.json", "{}")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/bar.py", out)
        self.status(("bar.py", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--write")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.k, "tools", "TOOLS.md")) as f:
            md = f.read()
        self.assertIn("do not edit; tools/tool-census --write", md)
        self.assertEqual(md.count("| `bar.py` |"), 1)
        self.assertNotIn("| `test_bar.py` |", md)
        self.assertIn("test_bar.py", md)
        self.assertIn("`data.json`", md)

    # lifecycle (user 2026-10-28): reason + evidence before every status change
    def test_deprecate_needs_reason_and_evidence(self):
        self.tool("old", "#!/bin/sh\necho 'DEPRECATED: use new (tools/tools-status.tsv)' >&2\nexit 2\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "deprecated", "new", "", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("DEPRECATE-NO-REASON tools/old", out)
        self.rows = [("old", "deprecated", "new", "outdated tool", "none at all", "2026-11-15")]
        self.put(self.k, "tools/tools-status.tsv", HDR + "\t".join(self.rows[0]) + "\n")
        rc, out = self.run_("--check")
        self.assertIn("DEPRECATE-NO-REASON tools/old", out)
        self.status(("old", "deprecated", "new", "outdated tool", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)

    def test_lifecycle_rows_need_reason_entry_and_match(self):
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a b\n")
        self.tool("b")
        self.auto_life = False
        self.put(self.k, "tools/tools-lifecycle.tsv", LHDR + "2026-10-28\ta\t-\tactive\t\t\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("LIFECYCLE-NO-REASON tools/tools-lifecycle.tsv:2 a", out)
        self.assertIn("LIFECYCLE-NO-ENTRY tools/b", out)
        self.put(self.k, "tools/tools-lifecycle.tsv", LHDR + "2026-10-28\ta\t-\tactive\tr\te\n"
                 "2026-10-28\tb\t-\tactive\tr\te\n2026-10-29\tb\tactive\tdeprecated\tlong enough reason\tvague\n")
        rc, out = self.run_("--check")
        self.assertIn("LIFECYCLE-WEAK-EVIDENCE tools/tools-lifecycle.tsv:4 b", out)
        self.assertIn("LIFECYCLE-MISMATCH tools/b is active", out)
        self.put(self.k, "tools/tools-lifecycle.tsv", LHDR + "2026-10-28\ta\t-\tactive\tr\te\n"
                 "2026-10-28\tb\t-\tactive\tr\te\n2026-10-29\tb\tallowlisted\tactive\tr\te\n")
        rc, out = self.run_("--check")
        self.assertIn("LIFECYCLE-GAP tools/tools-lifecycle.tsv:4 b", out)
        os.remove(os.path.join(self.k, "tools", "tools-lifecycle.tsv"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("LIFECYCLE-MISSING", out)

    def test_removed_tool_and_repeat_warning(self):
        self.tool("keep")
        self.put(self.s, "docs/runplans/x.md", "keep\n")
        gone = [("2026-10-28", g, "-", "active", "r", "e") for g in ("g1", "g2")]
        dep = [("2026-10-29", g, "active", "deprecated", "same trap AP-150", "run label x-20261029T010203Z") for g in ("g1", "g2")]
        self.life_rows(*gone, *dep)
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("LIFECYCLE-GONE tools/g1", out)
        self.assertIn("WARN LIFECYCLE-REPEAT AP-150 cited by 2 deprecations (g1, g2)", out)
        rm = [("2026-11-12", g, "deprecated", "removed", "cycle is over", "docs/reviews/rm-r1.md") for g in ("g1", "g2")]
        self.life_rows(*gone, *dep, *rm)
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)

    # review r1 findings
    def test_f1_active_row_does_not_exempt_nocaller(self):
        self.tool("orphan")
        self.status(("orphan", "active", "", "", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/orphan", out)

    def test_f2_replaced_gets_deprecated_checks(self):
        self.tool("oldie", "#!/bin/sh\n")
        self.tool("caller", "#!/bin/sh\noldie\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("oldie", "replaced", "new", "reason long enough", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE-DEPRECATED tools/oldie", out)
        self.assertIn("NO-DEPRECATED-LINE tools/oldie", out)

    def test_cycle_boundary(self):  # M1: age == cycle passes, age == cycle + 1 fails
        self.tool("old", "#!/bin/sh\n# DEPRECATED: use new\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "deprecated", "new", "reason long enough", "2026-11-06"))
        rc, out = self.run_("--check")  # 14 days
        self.assertEqual(rc, 0, out)
        rc, out = self.run_("--check", today="2026-11-21")
        self.assertIn("STALE-DEPRECATED tools/old", out)

    def test_no_replaced_by_warns(self):  # M7
        self.tool("old", "#!/bin/sh\n# DEPRECATED: use new\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "deprecated", "", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN NO-REPLACED-BY tools/old", out)

    def test_replaced_by_none_is_documented(self):  # R5: `none` + reason = no WARN
        self.tool("old", "#!/bin/sh\n# DEPRECATED: use nothing\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "deprecated", "none", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("NO-REPLACED-BY", out)
        self.run_("--write")  # review r1 F1: the rendered text is what changed
        md = open(os.path.join(self.k, "tools", "TOOLS.md")).read()
        self.assertIn("deprecated (no replacement)", md)
        self.assertIn("(deprecated since 2026-11-15): (no replacement) reason long enough", md)
        self.assertNotIn("(by none)", md)

    def test_replaced_with_none_is_an_error(self):  # review r1 F3
        self.tool("old", "#!/bin/sh\n# DEPRECATED: use nothing\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "replaced", "none", "reason long enough", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1, out)
        self.assertIn("REPLACED-NO-TOOL tools/old", out)

    def test_f3_future_dates(self):
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        self.status(("a", "allowlisted", "", "backlog", "2099-01-01"))
        rc, out = self.run_("--check")
        self.assertIn("BAD-STATUS-ROW tools/tools-status.tsv:2 a: since 2099-01-01 is in the future", out)
        self.assertIn("BAD-LIFECYCLE-ROW tools/tools-lifecycle.tsv:3 a: date 2099-01-01 is in the future", out)

    def test_f4_lifecycle_order_noop_and_date(self):
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        self.life_rows(("2026-10-27", "a", "active", "allowlisted", "reason long enough", EV))
        rc, out = self.run_("--check")
        self.assertIn("LIFECYCLE-ORDER tools/tools-lifecycle.tsv:3 a", out)
        self.life_rows(("2026-10-30", "a", "active", "active", "reason long enough", EV))
        rc, out = self.run_("--check")
        self.assertIn("LIFECYCLE-NOOP tools/tools-lifecycle.tsv:3 a", out)
        self.status(("a", "active", "", "", "2026-10-28"))
        self.life_rows(("2026-10-30", "a", "active", "allowlisted", "reason long enough", EV),
                       ("2026-10-31", "a", "allowlisted", "active", "reason long enough", EV))
        rc, out = self.run_("--check")
        self.assertIn("LIFECYCLE-MISMATCH-DATE tools/a", out)

    def test_f5_evidence_resolves(self):
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        self.status(("a", "allowlisted", "", "backlog", "2026-10-30"))
        self.life_rows(("2026-10-30", "a", "active", "allowlisted", "reason long enough", "AP-999 TP-777 docs/reviews/nope.md run label ok-20261029T010203Z"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        for x in ("AP-999 is not in", "no docs/tool-proposals/TP-777", "docs/reviews/nope.md exists in neither"):
            self.assertIn(x, out)
        self.assertNotIn("ok-2026", out)

    def test_f5_missing_super_warns(self):
        self.tool("a")
        shutil.rmtree(self.s)
        self.status(("a", "allowlisted", "", "backlog", "2026-10-30"))
        self.life_rows(("2026-10-30", "a", "active", "allowlisted", "reason long enough", "AP-150"))
        rc, out = self.run_("--check")
        self.assertIn("WARN EVIDENCE-UNCHECKED", out)
        self.assertIn("super=missing", out)
        self.assertIn("NOTE: no super repo", out)

    def test_f5_short_reason(self):
        self.tool("old", "#!/bin/sh\n# DEPRECATED: use new\n")
        self.tool("caller", "#!/bin/sh\nold\n")
        self.put(self.s, "docs/runplans/x.md", "caller\n")
        self.status(("old", "deprecated", "new", "short", "2026-11-15"))
        rc, out = self.run_("--check")
        self.assertIn("DEPRECATE-NO-REASON tools/tools-status.tsv:2 old: reason shorter", out)
        self.assertIn("LIFECYCLE-NO-REASON tools/tools-lifecycle.tsv:3 old: reason shorter", out)

    def test_f6_comments_and_reachability(self):
        self.tool("used", "#!/bin/sh\n")
        self.tool("viacomment", "#!/bin/sh\n")
        self.tool("py.py", '#!/usr/bin/env python3\n"""docs: tools/viacomment\n  more tools/viadoc\n"""\n# tools/viacomment\n')
        self.tool("viadoc", "#!/bin/sh\n")
        self.tool("ping", "#!/bin/sh\npong\n")
        self.tool("pong", "#!/bin/sh\nping\n")
        self.tool("root", "#!/bin/sh\nused\n# viacomment\n")
        self.put(self.s, "docs/runplans/x.md", "root py.py\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        for t in ("viacomment", "viadoc", "ping", "pong"):
            self.assertIn("NOCALLER tools/" + t, out)
        self.assertNotIn("NOCALLER tools/used", out)
        self.assertNotIn("NOCALLER tools/py.py", out)
        self.assertIn("mentioned only by unreached tools", out)
        self.put(self.k, "docs/A.md", "tools/ping\n")  # a doc mention: DOC-ONLY (warning) instead of NOCALLER
        rc, out = self.run_("--check")
        self.assertIn("WARN DOC-ONLY tools/ping", out)
        self.assertIn("NOCALLER tools/pong", out)

    def test_f7_toolsmd_stale(self):
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        self.no_md = True
        rc, out = self.run_("--check")
        self.assertIn("TOOLS-MD-STALE", out)
        self.run_("--write")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        p = os.path.join(self.k, "tools", "TOOLS.md")
        with open(p) as f:
            md = f.read()
        self.assertIn("| - |", md)
        with open(p, "w") as f:  # a changed time-dependent `last run use` cell must not count
            f.write(md.replace("| - |", "| 2020-01-01 |"))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        with open(p, "w") as f:
            f.write(md + "junk\n")
        rc, out = self.run_("--check")
        self.assertIn("TOOLS-MD-STALE", out)

    def test_f8_untracked_dir(self):
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        self.put(self.k, "tools/newdir/run.sh", "x\n")
        self.put(self.k, "tools/testdata/f", "x\n")
        rc, out = self.run_("--check")
        self.assertIn("UNTRACKED-DIR tools/newdir/", out)
        self.assertNotIn("UNTRACKED-DIR tools/testdata", out)

    def test_f10_skipped_note_and_nul_skip(self):  # M3
        self.tool("target")
        self.tool("blob")
        with open(os.path.join(self.k, "tools", "blob"), "wb") as f:
            f.write(b"\0\0 tools/target\n")
        self.put(self.s, "docs/runplans/x.md", "blob\n")
        rc, out = self.run_("--check")
        self.assertIn("NOCALLER tools/target", out)
        self.assertIn("NOTE: skipped 1 files", out)
        self.assertIn("binary (NUL byte)", out)

    def test_duplicate_status_row(self):  # M4
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        self.status(("a", "allowlisted", "", "backlog", "2026-10-28"), ("a", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--check")
        self.assertIn("duplicate row for a", out)

    def test_jaccard_threshold(self):  # M2 (0.99 and 0.3 both die)
        w = [c * 4 for c in "abcdefghijklmno"]
        hi = "#!/bin/sh\n# " + " ".join(w[:10]) + "\n"
        hi2 = "#!/bin/sh\n# " + " ".join(w[:9] + [w[10]]) + "\n"  # 9 shared / 11 = 0.82
        lo = "#!/bin/sh\n# " + " ".join(w[5:15]) + "\n"  # 5 shared / 15 = 0.33
        self.tool("one", hi)
        self.tool("two", hi2)
        self.tool("six", lo)
        self.put(self.s, "docs/runplans/x.md", "one two six\n")
        rc, out = self.run_("--check")
        self.assertIn("DUPLICATE? tools/one ~ tools/two", out)
        self.assertNotIn("DUPLICATE? tools/six", out)
        self.assertNotIn("~ tools/six", out)

    def test_worktree_run_dirs(self):  # M5
        self.tool("a")
        self.put(self.s, "docs/runplans/x.md", "a\n")
        g = lambda *a: subprocess.run(["git", "-C", self.k, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
        g("init", "-q")
        g("commit", "-q", "--allow-empty", "-m", "x")
        wt = os.path.join(self.d, "wt")
        g("worktree", "add", "-q", wt)
        self.put(wt, "logs/runs/20261101T000000Z-x/xrun.log", "ran tools/a ok\n")
        rc, out = self.run_("--json")
        self.assertEqual(json.loads(out)["tools"]["a"]["last_use"], "2026-11-01")

    def test_exit_2(self):  # M6
        rc, out = self.run_("--bogus")
        self.assertEqual(rc, 2)
        rc, out = self.run_("--check", today="not-a-date")
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: ERROR", out)

    # ---- super snapshot (tools/super-callers.tsv)
    def env_run(self, *args, sup=None, today=TODAY, strict=False):
        e = dict(os.environ, TOOL_CENSUS_KERNEL=self.k, TOOL_CENSUS_TODAY=today, TOOL_CENSUS_SUPER=sup or os.path.join(self.d, "nosuper"))
        e.pop("TOOL_CENSUS_STRICT_SUPER", None)
        if strict:
            e["TOOL_CENSUS_STRICT_SUPER"] = "1"
        r = subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True, env=e)
        return r.returncode, r.stdout

    def snap_setup(self):
        self.tool("gi")
        self.tool("lonely")
        self.status(("lonely", "allowlisted", "", "backlog", "2026-10-28"))
        self.put(self.s, "docs/runplans/x.md", "run tools/gi now\n")
        self.put(self.s, "docs/runplans/y.md", "also gi here\n")

    def test_snap_i_same_result_without_super(self):
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        snap = open(os.path.join(self.k, "tools/super-callers.tsv")).read()
        self.assertIn("gi\trunplan\tsuper:docs/runplans/x.md\n", snap)

    def test_snap_line_shift_not_stale(self):
        # review r2 F3: edges carry the file, not the line, so a line shift in a super doc keeps the snapshot and TOOLS.md fresh
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        self.put(self.s, "docs/runplans/x.md", "intro\n\nrun tools/gi now\n")
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("STALE", out)
        rc1, o1 = self.env_run("--check", sup=self.s)
        rc2, o2 = self.env_run("--check")
        self.assertEqual((rc1, rc2), (0, 0), o1 + o2)
        last = lambda o: o.strip().splitlines()[-1]
        self.assertTrue(last(o2).endswith(" super=snapshot"), o2)
        self.assertEqual(last(o1), last(o2).replace(" super=snapshot", ""))
        self.assertNotIn("TOOLS-MD-STALE", o2)
        self.assertIn("NOTE: no super repo: super-side callers from tools/super-callers.tsv (2 edges, checked against super by the local gate)", o2)

    def test_kernel_caller_line_shift_not_stale(self):
        # W26 ask 2026-10-29: TOOLS.md names the caller file, not its line, so a line shift in a kernel-side caller keeps it fresh
        self.snap_setup()
        self.tool("user", "#!/bin/sh\ntools/gi\n")
        self.status(("lonely", "allowlisted", "", "backlog", "2026-10-28"), ("user", "allowlisted", "", "backlog", "2026-10-28"))
        self.env_run("--write", sup=self.s)
        self.assertIn("tool `tools/user`", open(os.path.join(self.k, "tools", "TOOLS.md")).read())
        self.put(self.k, "tools/user", "#!/bin/sh\n# moved down\n\ntools/gi\n")
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("TOOLS-MD-STALE", out)

    def test_caller_cell_max_three_files_plus_count_json_keeps_lines(self):
        # review census-render-stable-r1 F3: mutant nd[:3] -> nd[:2] survived
        self.snap_setup()
        for n in ("u1", "u2", "u3", "u4"):
            self.tool(n, "#!/bin/sh\n\ntools/gi\n")
        self.status(("lonely", "allowlisted", "", "backlog", "2026-10-28"),
                    *[(n, "allowlisted", "", "backlog", "2026-10-28") for n in ("u1", "u2", "u3", "u4")])
        self.env_run("--write", sup=self.s)
        row = [l for l in open(os.path.join(self.k, "tools", "TOOLS.md")) if l.startswith("| `gi` |")][0]
        cell = row.split("|")[3]
        self.assertEqual(cell.count("`tools/u"), 3, row)
        self.assertNotRegex(cell, r":\d", row)
        self.assertIn("(+3)", cell, row)   # u4 + the two super runplans
        rc, out = self.env_run("--json", sup=self.s)
        self.assertIn("tools/u1:3", out)

    def test_snap_ii_stale_snapshot(self):
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        p = os.path.join(self.k, "tools/super-callers.tsv")
        open(p, "a").write("gi\tskill\tsuper:.claude/skills/z.md:1\n")
        rc, out = self.env_run("--check", sup=self.s)
        # W26 2026-10-29: commit-only verdict, a stale row is a counted WARN (the FAIL here is TOOLS-MD-STALE: the tsv was hand-edited)
        self.assertIn("WARN SUPER-CALLERS-STALE gi skill super:.claude/skills/z.md:1 (in tools/super-callers.tsv, not in super", out)
        self.assertRegex(out.strip().splitlines()[-1], r" super_stale=1 \(.*tools/tool-census --write\)")
        f = json.loads(self.env_run("--json", "--check", sup=self.s)[1])["findings"]
        self.assertEqual([x for x in f if x.startswith("SUPER-CALLERS")], [], f)
        rc, out = self.env_run("--check", sup=self.s, strict=True)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("super_stale=", out.strip().splitlines()[-1])   # strict: a finding, not a counted warning
        f = json.loads(self.env_run("--json", "--check", sup=self.s, strict=True)[1])["findings"]
        self.assertTrue(any(x.startswith("SUPER-CALLERS-STALE gi skill") for x in f), f)
        os.remove(p)
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 1, out)   # a missing snapshot is a property of the commit: FAIL also without strict
        self.assertIn("SUPER-CALLERS-STALE tools/super-callers.tsv is missing", out)

    def test_snap_new_super_mention_warns_only(self):  # (a)
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        self.put(self.s, "docs/runplans/z.md", "run tools/gi too\n")
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"SUPER-CALLERS-NEW gi runplan super:docs/runplans/z.md \(in super, not in tools/super-callers.tsv")
        self.assertRegex(out.strip().splitlines()[-1], r"^RESULT: PASS .* super_new=1 \(.*tools/tool-census --write\)")
        self.assertNotIn("STALE", out)   # includes TOOLS-MD-STALE: the render uses the snapshot

    def test_snap_edge_gone_from_super_warns(self):  # (b) AP-200 removal case: a super doc edit cannot flip the kernel verdict
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        os.remove(os.path.join(self.s, "docs/runplans/y.md"))
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertIn("SUPER-CALLERS-STALE gi runplan super:docs/runplans/y.md", out)
        self.assertRegex(out.strip().splitlines()[-1], r"^RESULT: PASS .* super_stale=1 \(")
        self.assertFalse(any(f.startswith("SUPER-CALLERS") for f in json.loads(self.env_run("--json", "--check", sup=self.s)[1])["findings"]))
        rc, out = self.env_run("--check", sup=self.s, strict=True)
        self.assertEqual(rc, 1, out)
        self.assertTrue(any(f.startswith("SUPER-CALLERS-STALE gi") for f in json.loads(self.env_run("--json", "--check", sup=self.s, strict=True)[1])["findings"]))

    def test_snap_fake_edge_cannot_hide_nocaller(self):  # (c)
        self.snap_setup()
        self.tool("orphan")
        self.env_run("--write", sup=self.s)
        open(os.path.join(self.k, "tools/super-callers.tsv"), "a").write("orphan\trunplan\tsuper:docs/runplans/fake.md\n")
        rc, out = self.env_run("--check", sup=self.s, strict=True)   # the watcher's full audit
        self.assertEqual(rc, 1, out)
        self.assertIn("SUPER-CALLERS-STALE orphan runplan super:docs/runplans/fake.md", out)
        self.assertTrue(any(f.startswith("SUPER-CALLERS-STALE orphan") for f in json.loads(self.env_run("--json", "--check", sup=self.s, strict=True)[1])["findings"]))
        rc, out = self.env_run("--check", sup=self.s)   # local gate: still loud and counted (residual: review r1 item 7)
        self.assertIn("SUPER-CALLERS-STALE orphan runplan super:docs/runplans/fake.md", out)
        self.assertIn(" super_stale=1 (", out.strip().splitlines()[-1])

    def test_snap_write_uses_live_super(self):  # (d)
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        self.put(self.s, "docs/runplans/z.md", "run tools/lonely now\n")
        self.env_run("--write", sup=self.s)
        snap = open(os.path.join(self.k, "tools/super-callers.tsv")).read()
        self.assertIn("lonely\trunplan\tsuper:docs/runplans/z.md\n", snap)
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("SUPER-CALLERS", out)

    def test_snap_super_analysis_equals_snapshot_analysis(self):  # (e)
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        self.put(self.s, "docs/runplans/q.md", "run tools/lonely now\n")   # live-only: must not change the verdict
        os.remove(os.path.join(self.s, "docs/runplans/y.md"))
        _, j1 = self.env_run("--json", sup=self.s)
        _, j2 = self.env_run("--json")
        a, b = json.loads(j1), json.loads(j2)
        self.assertEqual(a["tools"], b["tools"])
        self.assertEqual(a["counts"], b["counts"])
        self.assertEqual(a["findings"], b["findings"])

    def test_snap_iii_no_super_no_snapshot(self):
        self.snap_setup()
        rc, out = self.env_run("--check")
        self.assertEqual(rc, 1, out)
        self.assertIn("NOCALLER tools/gi", out)
        self.assertTrue(out.strip().splitlines()[-1].endswith(" super=missing"), out)

    def test_snap_iv_malformed_rows(self):
        self.snap_setup()
        self.env_run("--write", sup=self.s)
        p = os.path.join(self.k, "tools/super-callers.tsv")
        for bad, why in (("gi\trunplan\n", "wants 3"), ("gi\tnonsense\tsuper:x:1\n", "unknown kind"), ("ghost\trunplan\tsuper:x:1\n", "not a tool")):
            good = open(p).read()
            open(p, "a").write(bad)
            rc, out = self.env_run("--check")
            self.assertEqual(rc, 1, out)
            self.assertIn("BAD-SNAPSHOT-ROW tools/super-callers.tsv", out)
            self.assertIn(why, out)
            open(p, "w").write(good)

    def test_snap_v_write_needs_super(self):
        self.snap_setup()
        before = sorted(os.listdir(os.path.join(self.k, "tools")))
        rc, out = self.env_run("--write")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR", out)
        self.assertEqual(before, sorted(os.listdir(os.path.join(self.k, "tools"))))

    def test_write_on_trunk_prints_the_census_commit_escape(self):
        """review kernel-hooks-r1 F1: the trunk pre-commit hook denies the census commit; --write on branch trunk prints the exact PIXOS_TRUNK_DIRECT command"""
        self.snap_setup()
        git = lambda *a: subprocess.run(["git", "-C", self.k, *a], check=True, capture_output=True)
        git("init", "-q", "-b", "other")
        rc, out = self.env_run("--write", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("PIXOS_TRUNK_DIRECT", out)
        git("symbolic-ref", "HEAD", "refs/heads/trunk")
        rc, out = self.env_run("--write", sup=self.s)
        self.assertEqual(rc, 0, out)
        self.assertIn('PIXOS_TRUNK_DIRECT="census-write after BRANCH merge" git commit --trailer "PixOS-Worker: ROLE" -- tools/TOOLS.md tools/tools-status.tsv tools/super-callers.tsv', out)

    def test_snap_vi_super_present_nocaller_still_fails(self):
        self.snap_setup()
        self.tool("orphan")
        self.env_run("--write", sup=self.s)
        rc, out = self.env_run("--check", sup=self.s)
        self.assertEqual(rc, 1, out)
        self.assertIn("NOCALLER tools/orphan", out)

    # --- 2026-10-29 callers of kind `test` (import only) and `hook` (settings.json hook commands only)
    def test_test_import_without_own_tool_is_root_caller(self):
        self.tool("lib.py")
        self.tool("test_other.py", "import os\nimport lib\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("nocaller=0", out)

    def test_own_test_import_is_no_caller(self):
        self.tool("bar.py")
        self.tool("test_bar.py", "import bar\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/bar.py", out)

    def test_test_text_mention_is_no_caller(self):
        self.tool("lib.py")
        self.tool("test_other.py", '"""runs tools/lib.py"""\n# import lib\nX = "import lib"\n')
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/lib.py", out)

    def test_test_of_unreached_tool_does_not_reach(self):
        self.tool("lib.py")
        self.tool("mid.py")
        self.tool("test_mid.py", "from lib import x\n")   # mid's own test: edge mid -> lib, mid itself unreached
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/lib.py", out)
        self.put(self.s, "docs/runplans/x.md", "run tools/mid.py\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("nocaller=0", out)

    def test_hook_command_is_root_caller(self):
        self.tool("guard")
        self.put(self.s, ".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [{"matcher": "x", "hooks": [
            {"type": "command", "command": "sh -c 'python3 $P/tools/guard; exit 0'"}]}]}}))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("nocaller=0", out)
        self.run_("--write")
        with open(os.path.join(self.k, "tools", "super-callers.tsv")) as f:
            self.assertIn("guard\thook\tsuper:.claude/settings.json", f.read())

    def test_git_hook_file_is_root_caller(self):   # T-e23b
        self.tool("synctool")
        self.put(self.k, "tools/hooks/post-commit", "#!/bin/sh\n# mentions tools/synctool in a comment\n\"$(dirname \"$0\")/../synctool\" --background\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("nocaller=0", out)

    def test_git_hook_comment_only_mention_is_nocaller(self):   # T-e23b
        self.tool("synctool")
        self.put(self.k, "tools/hooks/post-commit", "#!/bin/sh\n# runs tools/synctool someday\nexit 0\n")
        rc, out = self.run_("--check")
        self.assertIn("NOCALLER tools/synctool", out)

    def hook_json(self, tool):
        return json.dumps({"hooks": {"PreToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": "python3 tools/" + tool}]}]}})

    def git_k(self, *a):
        subprocess.run(["git", "-C", self.k, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)

    def test_untracked_kernel_settings_is_not_a_caller_and_render_is_hermetic(self):   # tools/census-hermetic
        self.tool("guard")
        self.git_k("init", "-q")
        self.git_k("add", "-A")
        self.git_k("commit", "-q", "-m", "x")
        rc0, out0 = self.run_("--write")
        with open(os.path.join(self.k, "tools", "TOOLS.md")) as f:
            md0 = f.read()
        self.put(self.k, ".claude/settings.json", self.hook_json("guard"))   # untracked (gitignored in the real repo)
        rc, out = self.run_("--write")
        with open(os.path.join(self.k, "tools", "TOOLS.md")) as f:
            md1 = f.read()
        self.assertEqual(md0, md1)
        self.assertNotIn(".claude/settings.json", md1)
        self.assertIn("untracked", out)   # loud NOTE, rule 8
        rc, out = self.run_("--check")
        self.assertNotIn("TOOLS-MD-STALE", out)

    def test_tracked_template_still_counts(self):
        self.tool("guard")
        self.put(self.k, "tools/claude-settings.json", self.hook_json("guard"))
        self.git_k("init", "-q")
        self.git_k("add", "-A")
        self.git_k("commit", "-q", "-m", "x")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("nocaller=0", out)
        with open(os.path.join(self.k, "tools", "TOOLS.md")) as f:
            self.assertIn("hook `tools/claude-settings.json`", f.read())

    def test_permissions_allow_is_no_hook_caller(self):
        self.tool("guard")
        self.put(self.s, ".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(tools/guard:*)"]}, "hooks": {}}))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/guard", out)

    def test_malformed_settings_json_warns_loudly(self):
        self.tool("guard")
        self.put(self.s, ".claude/settings.json", "{ not json tools/guard")
        self.status(("guard", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--check")   # rc 1: --write refuses, so TOOLS.md/snapshot are missing; the loud WARN is the point
        self.assertIn("WARN HOOK-SETTINGS-UNPARSABLE super:.claude/settings.json", out)
        self.assertIn("hook_bad=1", out)

    def hook(self, root, cmd, extra=None):
        d = {"hooks": {"PreToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": cmd}]}]}}
        d.update(extra or {})
        self.put(root, ".claude/settings.json", json.dumps(d))

    def test_write_refused_on_unparsable_settings(self):   # review r5-lifecycle-r1 F1
        self.tool("guard")
        self.hook(self.s, "python3 tools/guard")
        rc, out = self.run_("--write")
        self.assertEqual(rc, 0, out)
        snap = open(os.path.join(self.k, "tools/super-callers.tsv")).read()
        md = open(os.path.join(self.k, "tools/TOOLS.md")).read()
        self.assertIn("guard\thook", snap)
        self.put(self.s, ".claude/settings.json", "{bad")
        rc, out = self.run_("--write")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR --write refused", out)
        self.assertEqual(open(os.path.join(self.k, "tools/super-callers.tsv")).read(), snap)
        self.assertEqual(open(os.path.join(self.k, "tools/TOOLS.md")).read(), md)

    def test_settings_without_hooks_key_is_quiet(self):   # F2
        self.tool("guard")
        self.status(("guard", "allowlisted", "", "backlog", "2026-10-28"))
        self.put(self.s, ".claude/settings.json", json.dumps({"permissions": {}}))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("HOOK-SETTINGS", out)
        self.assertNotIn("hook_bad", out)

    def test_one_malformed_event_is_named_others_count(self):   # F2, cmdstr/typeerr
        self.tool("guard")
        self.tool("other")
        d = {"hooks": {"A": [{"hooks": [{"command": "tools/guard"}]}], "B": {"x": 1},
                       "C": [{"hooks": [{"command": ["tools/other"]}]}]}}
        self.put(self.s, ".claude/settings.json", json.dumps(d))
        self.status(("other", "allowlisted", "", "backlog", "2026-10-28"))
        rc, out = self.run_("--check")   # rc 1: generated data cannot be written while a settings.json is bad
        self.assertIn("WARN HOOK-SETTINGS-UNPARSABLE super:.claude/settings.json event B", out)
        self.assertIn("hook_bad=1", out)
        self.assertIn("nocaller=0", out)   # other: the list command is not a string, so it has no caller; its allowlisted row exempts it (T-584b)
        self.assertIn("exempt=1", out)
        self.assertNotIn("NOCALLER tools/guard", out)

    def test_syntax_error_in_test_is_loud(self):   # F3
        self.tool("lib.py")
        self.tool("test_other.py", "import lib\ndef (:\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("WARN TEST-UNPARSABLE tools/test_other.py", out)
        self.assertIn("test_bad=1", out)
        self.assertIn("NOCALLER tools/lib.py", out)

    def test_relative_import_does_not_count(self):   # F4 level
        self.tool("lib.py")
        self.tool("test_other.py", "from . import lib\nfrom .lib import x\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/lib.py", out)

    def test_dotted_import_counts_top_module(self):   # F4 toplevelmod
        self.tool("lib.py")
        self.tool("test_other.py", "import lib.sub\n")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)

    def test_hook_matcher_and_permissions_mention_do_not_count(self):   # F4 hookall
        self.tool("guard")
        self.tool("other")
        self.put(self.s, "docs/runplans/x.md", "run tools/other\n")
        d = {"hooks": {"PreToolUse": [{"matcher": "tools/guard", "hooks": [{"type": "command", "command": "tools/other"}]}]},
             "permissions": {"allow": ["Bash(tools/guard:*)"]}}
        self.put(self.s, ".claude/settings.json", json.dumps(d))
        rc, out = self.run_("--check")
        self.assertEqual(rc, 1)
        self.assertIn("NOCALLER tools/guard", out)

    def test_kernel_settings_hook_counts(self):   # F4 kernelhook
        self.tool("guard")
        self.hook(self.k, "python3 tools/guard")
        rc, out = self.run_("--check")
        self.assertEqual(rc, 0, out)

    def test_own_test_not_listed_as_caller(self):   # F4 ownskip
        self.tool("bar.py")
        self.tool("test_bar.py", "import bar\n")
        self.put(self.s, "docs/runplans/x.md", "run tools/bar.py\n")
        rc, out = self.run_("--write")
        self.assertEqual(rc, 0, out)
        row = [l for l in open(os.path.join(self.k, "tools/TOOLS.md")).read().splitlines() if l.startswith("| `bar.py` |")][0]
        self.assertNotIn("test_bar.py", row.split("|")[3])   # callers cell (the tests cell may name it)

    def test_nocaller_text_names_all_exemptions(self):   # F7
        self.tool("lonely")
        rc, out = self.run_("--check")
        self.assertIn("`allowlisted`, `deprecated` or `replaced`", out)

    def test_git_excluded_file_under_tools_is_not_a_tool(self):   # T-b0e1
        self.tool("real")
        self.put(self.k, "tools/real-caller.sh", "real\n")
        g = lambda *a: subprocess.run(["git", "-C", self.k, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
        g("init", "-q")
        self.put(self.k, ".git/info/exclude", "tools/private-host.conf\n")
        self.put(self.k, "tools/private-host.conf", "x\n")
        g("add", "-A")
        rc, out = self.run_("--write")
        rc, out = self.run_("--check")
        self.assertNotIn("private-host", out)
        self.assertNotIn("private-host", open(os.path.join(self.k, "tools/TOOLS.md")).read())

    def test_dates_are_utc(self):
        src = open(TOOL).read()
        self.assertIn("datetime.timezone.utc", src)


if __name__ == "__main__":
    res = unittest.main(exit=False).result
    ok = res.wasSuccessful()
    print("RESULT: %s cases=%d%s" % ("PASS" if ok else "FAIL", res.testsRun, "" if ok else " failed=%d" % (len(res.failures) + len(res.errors))))
    sys.exit(0 if ok else 1)
