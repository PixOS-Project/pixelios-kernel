"""Step selection of check-all.sh --quick (tools/gate_select.py)."""
import copy, fnmatch, importlib.machinery, importlib.util, io, json, os, pathlib, re, subprocess, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_select as g


def _load_gate_manifest():
    """tools/gate-manifest has no .py suffix: load it by path (the generator whose --check this file wires into the unit step)."""
    path = str(pathlib.Path(__file__).resolve().parent / "gate-manifest")
    spec = importlib.util.spec_from_loader("gate_manifest", importlib.machinery.SourceFileLoader("gate_manifest", path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gm = _load_gate_manifest()


def row_select(files):
    """The steps whose manifest INPUTS match these files, ignoring the class: what a FORMAL gate would run for the variants (the quick lane selects only `build`, PLAN-GATE-LAYERS s.2 L1)."""
    return [r["step"] for r in g.MANIFEST["steps"] if r["inputs"] != ["*"] and any(g.row_match(r, f) for f in files)]


class Select(unittest.TestCase):
    def test_docs_only(self):
        self.assertEqual(g.select(["docs/HANDOFF.md", "AGENTS.md"]), ["public-split", "validate", "rules", "public-guard"])

    def test_xnu_patch_selects_the_unit_tests(self):
        """PLAN-GATE-2X D2 (intended narrowing, was the six unit-class steps): a patch is read by `unit` only; phone-state, hang-recover, probe-run, phone-converge and rootless read tools/soc/device, not the patch stack."""
        self.assertEqual(g.select(["xnu/patches/0092-sweet-touch-cmu-top.patch"]), ["unit", "public-split", "validate", "rules", "public-guard", "xnu-build", "stack-apply", "boot-markers", "lint-silent", "consumed", "inputs"])

    def test_wlan_shim_file_selects_unit(self):
        self.assertIn("unit", g.select(["shim/wlan_wr_host_test.c"]))

    def test_every_patch_a_test_names_selects_unit(self):
        """Literal scan: any xnu/patches/NNNN-... or bare NNNN patch number a tools/test_*.py names selects the unit step when that patch changes."""
        seen = 0
        for t in sorted((g.REPO / "tools").glob("test_*.py")):
            for n in set(re.findall(r"\b(\d{4}-[a-z][\w-]*\.patch)\b", t.read_text())):
                seen += 1
                self.assertIn("unit", g.select(["xnu/patches/" + n]), "%s names %s" % (t.name, n))
        self.assertGreater(seen, 10)

    def test_xnu_patch_used_by_shim_host_tests(self):
        self.assertIn("host-tests", g.select(["xnu/patches/0088-sweet-gs101-touch.patch"]))

    def test_xnu_patches_referenced_by_build_sh(self):
        for n in ("0087-x", "0088-x", "0093-sweet-touch-poke", "0104-sweet-mali-driver-core"):
            self.assertIn("host-tests", g.select(["xnu/patches/%s.patch" % n]), n)

    def test_tools_run_by_build_sh_select_the_builds(self):
        self.assertIn("build", g.select(["tools/boards.py"]))
        self.assertNotIn("build", g.select(["tools/qrmerge.py"]))

    def test_shell_scripts_select_shellcheck(self):
        for f in ("tools/check-all.sh", "tools/xrun", "xnu/patches/0019-stage-libpthread.sh", "tools/shell-files"):
            self.assertIn("shellcheck", g.select([f]), f)
        for f in ("tools/qrmerge.py", "docs/HANDOFF.md", "shim/ufs.c", "tools/gone-away.sh.bak"):
            self.assertNotIn("shellcheck", g.select([f]), f)

    def test_shim_markdown_selects_no_build(self):
        self.assertEqual(g.select(["shim/AGENTS.md"]), ["public-split", "validate", "rules", "public-guard"])

    def test_audit_memo_selects_boot_safety(self):
        self.assertIn("boot-safety", g.select(["tools/audit_memo.py"]))

    def test_shim(self):
        s = g.select(["shim/ufs.c"])
        self.assertEqual(s, g.SHIM + ["public-split", "validate", "rules", "public-guard", "lint-silent", "consumed", "inputs"])
        self.assertNotIn("unit", s)

    def test_tools_and_soc_json(self):
        self.assertEqual(g.select(["tools/qrmerge.py"]), ["unit", "phone-converge", "rootless", "public-split", "validate", "rules", "public-guard", "lint-silent", "inputs"])
        self.assertEqual(g.select(["soc/google/gs101/soc.json"]), g.SHIM + ["unit", "phone-converge", "rootless", "public-split", "validate", "rules", "public-guard", "inputs"])

    def test_run_path_tools_select_the_phone_path_self_tests(self):
        """PLAN-GATE-LAYERS s.3 row 3: only the run-path tools (and their tests) select probe-run / phone-state / hang-recover."""
        for f in ("tools/probe-run", "tools/xrun", "tools/fboot.sh", "tools/phone", "tools/plock.sh", "tools/fflash-userdata.sh", "tools/hang-recover", "tools/phone-lock", "tools/test_probe_failfast.py"):
            self.assertEqual([x for x in ("phone-state", "hang-recover", "probe-run") if x in g.select([f])], ["phone-state", "hang-recover", "probe-run"], f)
        for f in ("tools/qrmerge.py", "tools/gate-times", "tools/heavy-job", "shim/ufs.c", "soc/google/gs101/soc.json"):
            self.assertFalse({"phone-state", "hang-recover", "probe-run"} & set(g.select([f])), f)

    def test_the_selector_s_own_inputs_select_the_full_set(self):
        """PLAN-GATE-LAYERS s.3 last row."""
        for f in ("tools/check-all.sh", "tools/gate_select.py", "tools/gate-manifest.json"):
            self.assertEqual(g.select([f]), g.ALL, f)

    def test_the_variant_builds_are_formal_and_never_selected_by_a_quick_run(self):
        """PLAN-GATE-LAYERS s.2 L1: a shim/ change selects `build` only; the 11 variants run in the formal gate (and when a quick run selected every other step)."""
        self.assertEqual(len(g.VARIANT_BUILDS), 11)
        for v in g.VARIANT_BUILDS:
            self.assertEqual(next(r for r in g.MANIFEST["steps"] if r["step"] == v)["class"], "formal", v)
        for f in ("shim/gpu.c", "shim/wlan_new.c", "shim/ufs.c", "soc/google/gs101/soc.json"):
            self.assertEqual([x for x in g.select([f]) if x.startswith("build")], ["build"], f)

    def test_wlan_mutants_selected_by_their_inputs(self):
        for f in ("shim/wlan_wr_host_test.c", "shim/wlan_nv_host_test.c", "tools/test_wlan_wr_mutants.py", "tools/wlan_mutant_cache.py", "tools/wlan_mutant_pool.py", "xnu/patches/0129-sweet-wlan-chip-writes.patch", "xnu/patches/0135-sweet-wlan-run-f2-low-window.patch"):
            self.assertIn("wlan-mutants", g.select([f]), f)

    def test_wlan_mutants_not_selected_by_unrelated_changes(self):
        for f in ("tools/qrmerge.py", "docs/HANDOFF.md", "xnu/patches/0154-sweet-devoverlay.patch", "shim/ufs.c", "soc/google/gs101/soc.json"):
            self.assertNotIn("wlan-mutants", g.select([f]), f)

    def test_wlan_mutant_inputs_cover_every_patch_the_test_names(self):
        """TP-003 P2 guard: a patch file name or a shim/ file written anywhere in the mutant test must be an input, else a change to it would skip the test.
        Every read must be a literal os.path.join(ROOT, ...) the input list parser sees: ROOT is used nowhere else (review C3)."""
        text = (g.REPO / gm.WLAN_MUT_TEST).read_text()
        joins = len(gm.WLAN_MUT_JOIN_RE.findall(text))
        self.assertGreaterEqual(joins, 13)
        self.assertEqual(text.count("os.path.join(ROOT"), joins, "a join with a non-literal argument")
        self.assertEqual(len(re.findall(r"\bROOT\b", text)), joins + 1, "ROOT used other than as a literal join (one definition)")
        self.assertEqual(len(re.findall(r"__file__", text)), 1)
        names = set(re.findall(r"\b(\d{4}-[a-z][\w-]*\.patch)\b", text))
        self.assertGreater(len(names), 5)
        for name in names:
            self.assertIn("xnu/patches/" + name, g.WLAN_MUT_INPUTS, name)
        for name in set(re.findall(r"\"([\w-]+\.(?:c|h|json|py))\"", text)):
            if (g.REPO / "shim" / name).exists():
                self.assertIn("shim/" + name, g.WLAN_MUT_INPUTS, name)

    def test_all_matches_check_all(self):
        m = re.search(r'^ALL="([^"]*)"', (g.REPO / "tools" / "check-all.sh").read_text(), re.M)
        self.assertEqual(m.group(1).split(), g.ALL)

    def test_xnu_build_follows_the_patch_stack(self):
        """AP-257: only a change to xnu/patches or the tools that build it selects the real XNU build; a shim-only change must not."""
        for f in ("xnu/patches/0123-sweet-iortc.patch", "xnu/patches/series", "tools/gate-xnu-build", "tools/xnu-local-build", "tools/xnu-remote-build", "tools/xnub-line.sh", "tools/xnu-src-mirror", "tools/check-all.sh"):
            self.assertIn("xnu-build", g.select([f]), f)
        for f in ("shim/acpm.c", "tools/lint-c", "docs/X.md"):
            self.assertNotIn("xnu-build", g.select([f]), f)

    def test_a_quick_run_that_selects_everything_also_builds_xnu(self):
        """Intended narrowing: shim/acpm.c no longer selects every build variant (build-fast/usb/gpu/gpupower/udump do not link acpm), so it no longer completes a full run; shim/build.sh does."""
        self.assertNotIn("lint-c", g.select(["shim/acpm.c", "tools/test_wlan_wr_mutants.py", "tools/x.sh"]))
        every = ["shim/build.sh", "xnu/patches/0129-sweet-wlan-chip-writes.patch", "tools/test_wlan_wr_mutants.py", "tools/x.sh", "soc/google/gs101/wlan_msgbuf_l22.tsv"]
        s = g.select(every)
        self.assertEqual(s, g.ALL)
        self.assertIn("lint-c", s)
        self.assertIn("xnu-build", s)
        self.assertIn("stack-apply", s)
        self.assertIn("mutant-needles", s)
        self.assertIn("boot-markers", s)

    def test_stack_apply_follows_the_patch_stack_exactly(self):
        """AP-258: any xnu/patches/** file (patch, stager, series) or the stack tool selects stack-apply; nothing else does."""
        for f in ("xnu/patches/0123-sweet-iortc.patch", "xnu/patches/0001-x.sh", "xnu/patches/series", "tools/stack-apply"):
            self.assertIn("stack-apply", g.select([f]), f)
        for f in ("shim/acpm.c", "tools/lint-c", "tools/gate-xnu-build", "docs/X.md", "tools/test_wlan_wr_mutants.py", "tools/lint-mutant-needles"):
            self.assertNotIn("stack-apply", g.select([f]), f)

    def test_documentation_under_xnu_patches_selects_no_xnu_step(self):
        """r42-gate-r1 F7: xnu/patches/NOTICE.md (and any *.md there) is no stager, patch or series: no multi-minute xnu-build / stack-apply and no boot-markers. Docs-only batches neither."""
        for f in ("xnu/patches/NOTICE.md", "xnu/patches/README.md", "docs/PLAN-DEMO.md", "docs/specs/SPEC-WIFI.md", "AGENTS.md"):
            s = g.select([f])
            for step in ("xnu-build", "stack-apply", "boot-markers"):
                self.assertNotIn(step, s, "%s selects %s" % (f, step))
        # ... while everything that IS the stack still selects them, and the stager is not a boot-print file
        for f in ("xnu/patches/0182-sweet-panic-noregion-reset.patch", "xnu/patches/0019-stage-libpthread.sh", "xnu/patches/series"):
            for step in ("xnu-build", "stack-apply"):
                self.assertIn(step, g.select([f]), "%s misses %s" % (f, step))
        self.assertIn("boot-markers", g.select(["xnu/patches/0182-sweet-panic-noregion-reset.patch"]))
        self.assertNotIn("boot-markers", g.select(["xnu/patches/0019-stage-libpthread.sh"]))

    def test_mutant_needles_follow_the_tables_and_their_targets(self):
        """AP-281: a mutant table, the lint, a patch the wlan tables are built from or a file an el2 row targets selects the presence pass; a docs edit does not."""
        for f in ("tools/test_wlan_wr_mutants.py", "tools/test_el2_mutants.py", "tools/test_future_mutants.py", "tools/lint-mutant-needles", "xnu/patches/0129-sweet-wlan-chip-writes.patch",
                  "shim/el2.c", "shim/el2h.c", "tools/boards.py", "shim/el2_print_host_test.c"):
            self.assertIn("mutant-needles", g.select([f]), f)
        for f in ("docs/X.md", "shim/acpm.c", "tools/lint-c", "xnu/patches/0092-sweet-touch-cmu-top.patch"):
            self.assertNotIn("mutant-needles", g.select([f]), f)
        el2 = gm.el2_mut_inputs(g.REPO)
        self.assertGreaterEqual(len(el2), 4)
        for f in el2:
            self.assertIn("mutant-needles", g.select([f]), f)

    def test_boot_markers_follow_the_patches_and_the_markers(self):
        """AP-229: xnu/patches/** (the prints), tools/xrun_report.py (the markers) or the lint select boot-markers; shim and docs edits do not."""
        for f in ("xnu/patches/0182-sweet-panic-noregion-reset.patch", "tools/xrun_report.py", "tools/lint-boot-markers"):
            self.assertIn("boot-markers", g.select([f]), f)
        for f in ("shim/acpm.c", "docs/X.md", "tools/lint-c"):
            self.assertNotIn("boot-markers", g.select([f]), f)

    def test_inputs_follow_the_steps_that_read_them(self):
        """AP-11/144: inputs (tools/third_party, research/ipsw-x/d22ap.adt) is selected with any build or unit step and with xnu-build; docs-only batches do not need it."""
        for f in ("shim/acpm.c", "tools/qrmerge.py", "xnu/patches/0092-sweet-touch-cmu-top.patch"):
            self.assertIn("inputs", g.select([f]), f)
        for f in ("docs/X.md", "shim/AGENTS.md"):
            self.assertNotIn("inputs", g.select([f]), f)

    def test_lint_c_is_full_gate_only(self):
        self.assertNotIn("lint-c", g.select(["shim/acpm.c"]))
        self.assertNotIn("lint-c", g.select(["tools/lint-c"]))


    def test_missing_stamp_means_full(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(g.base_from_stamp(d + "/nope"))
            p = pathlib.Path(d, "bad")
            p.write_text("deadbeef\n")
            self.assertIsNone(g.base_from_stamp(str(p)))


class Registry(unittest.TestCase):
    """Host-wide full-PASS registry (research 68 D2) in a throwaway git repo; GATE_PASS_DIR points at a temp dir, never the real one."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        self.repo = pathlib.Path(self.td.name, "r")
        (self.repo / "tools").mkdir(parents=True)
        self.reg = pathlib.Path(self.td.name, "reg")
        self.old = os.environ.get("GATE_PASS_DIR")
        os.environ["GATE_PASS_DIR"] = str(self.reg)
        os.chdir(self.repo)
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "tools" / "check-all.sh").write_text("#!/bin/bash\n")
        self.blob = self.git("hash-object", "tools/check-all.sh")
        self.c = [self.commit("c0", "a.txt")]
        self.git("branch", "side")
        self.old_state = g.tree_state
        self.state = ("g1", 1)
        g.tree_state = lambda: self.state

    def tearDown(self):
        g.tree_state = self.old_state
        os.chdir(self.cwd)
        if self.old is None:
            os.environ.pop("GATE_PASS_DIR", None)
        else:
            os.environ["GATE_PASS_DIR"] = self.old
        self.td.cleanup()

    def git(self, *a):
        return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, msg, f):
        (self.repo / f).write_text(msg)
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")

    def register(self, sha, content=None):
        self.reg.mkdir(exist_ok=True)
        (self.reg / sha).write_text((f"{self.blob} g1 1" if content is None else content) + "\n")

    def test_no_registry_no_stamp_means_full(self):
        self.assertIsNone(g.best_base(str(self.repo / "nostamp")))

    def test_registry_entry_is_a_base(self):
        self.register(self.c[0])
        self.commit("c1", "b.txt")
        self.assertEqual(g.best_base(str(self.repo / "nostamp")), self.c[0])

    def test_non_ancestor_ignored(self):
        self.git("checkout", "-q", "side")
        side = self.commit("s", "s.txt")
        self.git("checkout", "-q", "-")
        self.commit("c1", "b.txt")
        self.register(side)
        self.assertIsNone(g.best_base(str(self.repo / "nostamp")))

    def test_fewest_diff_wins(self):
        self.commit("c1", "b.txt")
        near = self.commit("c2", "c.txt")
        self.commit("c3", "d.txt")
        self.register(near)
        stamp = self.repo / "stamp"
        stamp.write_text(self.c[0] + "\n")
        self.assertEqual(g.best_base(str(stamp)), near)

    def test_entry_from_another_check_all_blob_ignored(self):
        self.register(self.c[0], "0" * 40)
        self.commit("c1", "b.txt")
        self.assertIsNone(g.best_base(str(self.repo / "nostamp")))

    def base(self, content, state):
        self.register(self.c[0], content)
        self.n = getattr(self, "n", 0) + 1
        self.commit(f"n{self.n}", f"n{self.n}.txt")
        self.state = state
        return g.best_base(str(self.repo / "nostamp"))

    def test_entry_with_other_third_party_hash_ignored(self):
        self.assertIsNone(self.base(f"{self.blob} g2 1", ("g1", 1)))

    def test_adt_flag_rule_both_directions(self):
        self.assertIsNone(self.base(f"{self.blob} g1 0", ("g1", 1)))  # entry a=0, tree a=1: ignored
        self.reg.joinpath(self.c[0]).unlink()
        self.assertEqual(self.base(f"{self.blob} g1 1", ("g1", 0)), self.c[0])  # entry a=1, tree a=0: allowed

    def test_legacy_one_field_entry_ignored(self):
        self.assertIsNone(self.base(self.blob, ("g1", 1)))

    def test_bad_names_ignored(self):
        self.register("not-a-sha")
        self.assertIsNone(g.best_base(str(self.repo / "nostamp")))


    def test_renamed_and_edited_input_patch_selects_wlan_mutants(self):
        """AP-98 / review C1: git mv + edit of patch 0135 must still select the mutants."""
        old = "xnu/patches/0135-sweet-wlan-run-f2-low-window.patch"
        (self.repo / "xnu" / "patches").mkdir(parents=True)
        (self.repo / old).write_text("a\nb\nc\nd\ne\nf\n")
        base = self.commit("p", "x.txt")
        self.git("mv", old, "xnu/patches/0135-sweet-wlan-run-f2-v2.patch")
        (self.repo / "xnu/patches/0135-sweet-wlan-run-f2-v2.patch").write_text("a\nb\nc\nd\ne\nf\ng\n")
        self.commit("rename", "y.txt")
        self.assertIn("wlan-mutants", g.select(g.changed(base)))

    def test_new_patch_editing_the_chip_writer_selects_wlan_mutants(self):
        old = g.REPO
        try:
            g.REPO = self.repo
            (self.repo / "xnu" / "patches").mkdir(parents=True)
            (self.repo / "xnu/patches/0999-gs101-other.patch").write_text("--- a/bsd/dev/sweet_wlan_wr.h\n+++ b/bsd/dev/sweet_wlan_wr.h\n")
            (self.repo / "xnu/patches/0998-unrelated.patch").write_text("--- a/bsd/dev/sweet_touch.c\n+++ b/bsd/dev/sweet_touch.c\n")
            self.assertIn("wlan-mutants", g.select(["xnu/patches/0999-gs101-other.patch"]))
            self.assertNotIn("wlan-mutants", g.select(["xnu/patches/0998-unrelated.patch"]))
        finally:
            g.REPO = old


ALWAYS = {"validate", "rules", "public-split", "public-guard", "inputs", "shellcheck", "boot-safety", "phone-converge", "mutant-needles"}   # class `always` (PLAN-GATE-2X D2)
EVERY_CHANGE = ("public-split", "validate", "rules", "public-guard")
SAMPLES = [   # 33 changed paths of every kind: docs, shim families, tools, patches, soc, device, qr, an unknown path
    "docs/README.md", "docs/specs/SPEC-X.md", "AGENTS.md", "NOTES.md", "LICENSE", ".gitignore", "shim/AGENTS.md",
    "shim/ufs.c", "shim/acpm.c", "shim/gpu.c", "shim/gpuclk.c", "shim/gpu_new.c", "shim/touch.c", "shim/udump.c", "shim/wlan_wr_host_test.c", "shim/build.sh", "shim/ufs_host_test.c",
    "tools/qrmerge.py", "tools/xrun", "tools/check-all.sh", "tools/boards.py", "tools/x.sh", "tools/test_wlan_wr_mutants.py", "tools/gate_select.py",
    "xnu/patches/0092-sweet-touch-cmu-top.patch", "xnu/patches/0129-sweet-wlan-chip-writes.patch", "xnu/patches/0019-stage-libpthread.sh", "xnu/patches/series", "xnu/patches/NOTICE.md",
    "soc/google/gs101/soc.json", "device/google/bluejay/board.json", "xnu/qr/qr.c", "unknown-dir/new-file.bin",
]


def oracle_hit(row, f):
    """Own fnmatch, not the selector's: does a manifest input of ROW (or its shell predicate) name F."""
    if row["inputs"] == ["*"]:
        return True
    for e in row["inputs"]:
        if isinstance(e, dict):
            if fnmatch.fnmatchcase(f, e["glob"]) and not any(fnmatch.fnmatchcase(f, x) for x in e.get("except", [])):
                return True
        elif fnmatch.fnmatchcase(f, e):
            return True
    return row.get("predicate") == "shell" and f.endswith(".sh")


def check_all_text():
    return (g.REPO / "tools" / "check-all.sh").read_text()


def build_sh_links():
    """{build-* variant: set of shim source stems it LINKS}, derived from shim/build.sh (case flag -> variables, `usb_objs=` link rules, `-c X.c -o $O/y.o`) and the build.sh calls of each
    `step A build-*` line of check-all.sh (`B="./build.sh FLAGS"` bases and `$B FLAGS` calls). Only two things are modelled by hand: --udump implies usb_gadget (build.sh:88) and the
    touch options rails/railsr/dvfs/dvfsr set touch_rails/touch_dvfs (build.sh:206-218)."""
    bs = (g.REPO / "shim" / "build.sh").read_text()
    sets = {f: re.findall(r"(\w+)=1", body) for f, body in re.findall(r"^\t\t(--[\w=*-]+)\) (.*?);;", bs, re.M)}
    sets["--udump"] = ["udump_mib", "usb_gadget"]
    src = {o: s for s, o in re.findall(r"-c (\w+)\.c -o \$O/(\w+)\.o", bs)}
    rules, conds = [], []
    for line in bs.split("abs=$(")[0].splitlines():
        s = line.strip()
        if re.match(r"(if|elif) \[", s):
            conds = re.findall(r"\$(\w+)", s)
        if s.startswith("usb_objs=") and "$O/" in s:
            rules.append((set(conds), {src[o] for o in re.findall(r"\$O/(\w+)\.o", s)}))
    out = {}
    for line in check_all_text().splitlines():
        m = re.match(r"\s*(?:par )?step A (build-[\w-]+)\s", line)
        if not m:
            continue
        base, linked = [], set()
        for seg in line.split("&&"):
            if 'B="./build.sh' in seg:
                base = [t for t in seg.split() if t.startswith("--")]
            elif "./build.sh" in seg or "$B " in seg:
                flags = [t for t in seg.split() if t.startswith("--")] + (base if "$B " in seg else [])
                on = set()
                for f in flags:
                    on |= set(sets.get(f) or sets.get(f.split("=")[0] + "=*") or [])
                    if f.startswith("--touch-probe="):
                        words = f.split("=", 1)[1].split(",")
                        on |= ({"touch_rails"} if {"rails", "railsr"} & set(words) else set()) | ({"touch_dvfs"} if {"dvfs", "dvfsr"} & set(words) else set())
                for c, objs in rules:
                    if c & on:
                        linked |= objs
        out[m.group(1)] = linked
    return out


class Manifest(unittest.TestCase):
    """G3 (PLAN-GATE-2X section 5): the manifest accounts for every step, variant and mutant table; the table is what the selector reads."""

    def rows(self):
        return {r["step"]: r for r in g.MANIFEST["steps"]}

    def test_every_all_step_has_a_complete_row(self):
        allsteps, lanes = gm.parse_check_all(check_all_text())
        self.assertEqual(allsteps, g.ALL)
        rows = self.rows()
        self.assertEqual(sorted(rows), sorted(allsteps))
        for st in allsteps:
            r = rows[st]
            self.assertEqual(r["lane"], lanes.get(st), "%s: lane differs from `step LANE %s` in check-all.sh" % (st, st))
            self.assertIn(r["class"], ("quick", "formal", "always"), st)
            self.assertIsInstance(r["budget_s"], int, st)
            self.assertGreater(r["budget_s"], 0, st)
            self.assertIn("inputs", r, st)
            self.assertTrue(r["inputs"] or r.get("predicate") or r.get("follows") or st == "lint-c", "%s: no inputs" % st)

    def test_each_build_variant_has_its_own_row(self):
        text = check_all_text()
        variants = sorted(set(re.findall(r"\bstep A (build[\w-]*)", text)))
        self.assertGreaterEqual(len(variants), 12)
        rows = self.rows()
        for v in variants:
            self.assertIn(v, rows, v)
        self.assertEqual(len({json.dumps(rows[v]["inputs"], sort_keys=True) for v in variants if v.startswith("build-")}), len([v for v in variants if v.startswith("build-")]) - 4,
                         "variants that link the same families share a glob list (gpu/gpupower, gpuclk/gpurails/gpurailon/gpujob): fewer distinct lists than rows, never one merged row")

    def test_the_always_class_is_exactly_the_documented_list(self):
        self.assertEqual({r["step"] for r in g.MANIFEST["steps"] if r["class"] == "always"}, ALWAYS)
        self.assertEqual(self.rows()["lint-c"]["class"], "formal")

    def test_the_committed_manifest_is_complete(self):
        self.assertEqual(gm.problems(g.MANIFEST, g.REPO), [])
        self.assertEqual(set(gm.mutant_tables(g.REPO)), set(g.MANIFEST["mutant_tables"]))

    def problems_with(self, mutate=None, text=None):
        m = copy.deepcopy(g.MANIFEST)
        if mutate:
            mutate(m)
        return gm.problems(m, g.REPO, text if text is not None else check_all_text())

    def test_a_fixture_manifest_with_a_step_lacking_a_row_fails(self):
        self.assertTrue(any("rootless" in p and "no manifest row" in p for p in self.problems_with(lambda m: m["steps"].remove(self.rows()["rootless"]))))

    def test_a_row_lacking_lane_class_budget_or_inputs_fails(self):
        def lacking(key, val):
            def mutate(m):
                r = next(r for r in m["steps"] if r["step"] == "unit")
                r[key] = val
            return mutate
        for key, val, word in (("lane", None, "lane"), ("class", "fast", "class"), ("budget_s", 0, "budget_s"), ("budget_s", None, "budget_s"), ("inputs", [], "inputs")):
            self.assertTrue(any("unit" in p and word in p for p in self.problems_with(lacking(key, val))), "%s=%r" % (key, val))

    def test_a_new_build_variant_without_a_row_fails(self):
        text = check_all_text().replace('inputs"', 'inputs build-new"', 1)
        self.assertIn("build-new", text)
        self.assertTrue(any("build-new" in p and "no manifest row" in p for p in self.problems_with(text=text)))
        text2 = check_all_text() + "\nlaneZ() { step A build-extra tools/x; }\n"
        self.assertTrue(any("build-extra" in p and "not in ALL" in p for p in self.problems_with(text=text2)))

    def test_a_mutants_table_not_reached_by_the_needles_row_fails(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "tools").mkdir()
            (pathlib.Path(d) / "tools" / "test_foo.py").write_text("FOO_MUTANTS = []\n")
            m = copy.deepcopy(g.MANIFEST)
            probs = gm.problems(m, d, check_all_text())
        self.assertTrue(any("FOO_MUTANTS" in p and "not reached by the mutant-needles row" in p for p in probs), probs)
        self.assertTrue(any("FOO_MUTANTS" in p and "no manifest entry" in p for p in probs), probs)

    def test_a_13th_build_row_is_refused_and_a_removed_one_must_lower_the_ceiling(self):
        """T-cfb7 (port-new-block step 8): `build`/`build-*` rows are capped by tools/gate-manifest-ceilings.json; the cap only lowers."""
        cap = json.loads((g.REPO / gm.CEILINGS).read_text())["build_variants"]
        self.assertEqual(len([r for r in g.MANIFEST["steps"] if r["step"] == "build" or r["step"].startswith("build-")]), cap)
        probs = self.problems_with(lambda m: m["steps"].append(dict(m["steps"][0], step="build-thirteen")))
        self.assertTrue(any("build variants" in p and "exceed the ceiling %d" % cap in p and "build-thirteen" in p for p in probs), probs)
        probs = self.problems_with(lambda m: m["steps"].remove(self.rows()["build-udump"]), text=check_all_text().replace("build-udump", "build"))
        self.assertTrue(any("build variants" in p and "lower the ceiling" in p for p in probs), probs)

    def ceiling_repo(self, d, tables, ceilings=None):
        """A fixture repo: the real ceilings file (or CEILINGS) and tools/test_*.py files with the given {file: source}."""
        (pathlib.Path(d) / "tools").mkdir()
        (pathlib.Path(d) / gm.CEILINGS).write_text(json.dumps(ceilings or json.loads((g.REPO / gm.CEILINGS).read_text())))
        for name, src in tables.items():
            (pathlib.Path(d) / "tools" / name).write_text(src)
        return gm.ceiling_problems(copy.deepcopy(g.MANIFEST), pathlib.Path(d), g.ALL)

    def test_a_new_mutant_table_needs_a_block_tag_and_a_block_has_one_table(self):
        legacy = json.loads((g.REPO / gm.CEILINGS).read_text())["legacy_mutant_tables"]
        keep = {t.split(":")[0] for t in legacy}
        with tempfile.TemporaryDirectory() as d:
            probs = self.ceiling_repo(d, {"test_foo.py": "FOO_MUTANTS = []\n"})
        self.assertTrue(any("test_foo.py:FOO_MUTANTS" in p and "mutant-block" in p for p in probs), probs)
        tagged = "# mutant-block: bt\nFOO_MUTANTS = []\n"
        with tempfile.TemporaryDirectory() as d:
            probs = self.ceiling_repo(d, {"test_foo.py": tagged})
        self.assertFalse([p for p in probs if "FOO_MUTANTS" in p], probs)
        with tempfile.TemporaryDirectory() as d:
            probs = self.ceiling_repo(d, {"test_foo.py": tagged, "test_bar.py": "# mutant-block: bt\nBAR_MUTANTS = []\n"})
        self.assertTrue(any("block bt has 2 mutant tables" in p for p in probs), probs)
        with tempfile.TemporaryDirectory() as d:   # a tag that is not directly above the table does not count
            probs = self.ceiling_repo(d, {"test_foo.py": "# mutant-block: bt\n\nFOO_MUTANTS = []\n"})
        self.assertTrue(any("test_foo.py:FOO_MUTANTS" in p and "mutant-block" in p for p in probs), probs)

    def test_a_legacy_mutant_table_that_is_gone_or_retagged_must_leave_the_list(self):
        with tempfile.TemporaryDirectory() as d:
            probs = self.ceiling_repo(d, {}, {"build_variants": 12, "legacy_mutant_tables": ["test_gone.py:GONE_MUTANTS"]})
        self.assertTrue(any("GONE_MUTANTS no longer exists" in p for p in probs), probs)
        with tempfile.TemporaryDirectory() as d:
            probs = self.ceiling_repo(d, {"test_foo.py": "# mutant-block: bt\nFOO_MUTANTS = []\n"}, {"build_variants": 12, "legacy_mutant_tables": ["test_foo.py:FOO_MUTANTS"]})
        self.assertTrue(any("also in legacy_mutant_tables" in p for p in probs), probs)

    def test_raising_a_ceiling_vs_trunk_is_refused_and_the_escape_is_loud(self):
        """F3 of review cfb7-r1: the ceiling baseline is trunk:tools/gate-manifest-ceilings.json (raise = FAIL unless PIXOS_CEILING_RAISE='reason >= 20 chars')."""
        real = json.loads((g.REPO / gm.CEILINGS).read_text())
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
            git("init", "-q", "-b", "trunk")
            (pathlib.Path(d) / "tools").mkdir()
            (pathlib.Path(d) / gm.CEILINGS).write_text(json.dumps(real))
            git("add", "."); git("commit", "-q", "-m", "base")
            self.assertEqual(gm.baseline_problems(real, pathlib.Path(d)), [])
            raised = dict(real, build_variants=real["build_variants"] + 1)
            self.assertTrue(any("does not rise" in p for p in gm.baseline_problems(raised, pathlib.Path(d))))
            grown = dict(real, legacy_mutant_tables=real["legacy_mutant_tables"] + ["test_new.py:NEW_MUTANTS"])
            self.assertTrue(any("only shrinks" in p and "NEW_MUTANTS" in p for p in gm.baseline_problems(grown, pathlib.Path(d))))
            shrunk = dict(real, build_variants=real["build_variants"] - 1, legacy_mutant_tables=real["legacy_mutant_tables"][1:])
            self.assertEqual(gm.baseline_problems(shrunk, pathlib.Path(d)), [])
            old = os.environ.get("PIXOS_CEILING_RAISE")
            try:
                os.environ["PIXOS_CEILING_RAISE"] = "too short"
                self.assertTrue(gm.baseline_problems(raised, pathlib.Path(d)))
                os.environ["PIXOS_CEILING_RAISE"] = "user approved a 13th variant, ticket T-xxxx"
                self.assertEqual(gm.baseline_problems(raised, pathlib.Path(d)), [])
            finally:
                os.environ.pop("PIXOS_CEILING_RAISE", None) if old is None else os.environ.__setitem__("PIXOS_CEILING_RAISE", old)

    def test_the_ceilings_file_must_exist_and_be_valid(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "tools").mkdir()
            probs = gm.ceiling_problems(copy.deepcopy(g.MANIFEST), pathlib.Path(d), g.ALL)
        self.assertTrue(any("unreadable" in p for p in probs), probs)

    def test_a_missing_manifest_is_a_loud_error_not_an_empty_selection(self):
        with self.assertRaises(OSError):
            g.load_manifest("/nonexistent/gate-manifest.json")

    def test_a_new_non_test_wlan_shim_file_selects_the_variant_builds(self):
        """Review gate-2x-s2-r1 F1: `shim/wlan_*` is not host-only; only *_host_test.c (and the two sweet_wlan_*.h headers) are."""
        for f in ("shim/wlan_new.c", "shim/wlan_new.h", "shim/sweet_wlan_new.c"):
            sel = row_select([f])
            self.assertIn("build-wlan", sel, f)
            self.assertEqual([x for x in sel if x.startswith("build-")], [x for x in g.ALL if x.startswith("build-")], f)
        for f in ("shim/wlan_x_host_test.c", "shim/wlanprobe_host_test.c", "shim/sweet_wlan_sta.h"):
            sel = row_select([f])
            self.assertEqual([x for x in sel if x.startswith("build-")], [], f)
            self.assertEqual([x for x in sel if x in ("build", "host-tests", "boot-safety")], ["build", "host-tests", "boot-safety"], f)

    def test_the_host_only_headers_are_included_by_host_tests_only(self):
        """The pin behind HOST_ONLY's `shim/sweet_wlan_*.h`: any other includer would need the variants rebuilt (`*.in` templates feed xnu patch 0199, not shim/build.sh; host-only headers are checked in turn)."""
        shim = g.REPO / "shim"
        heads = [p.name for p in shim.glob("sweet_wlan_*.h")]
        self.assertGreaterEqual(len(heads), 2)
        for h in heads:
            users = [p.name for p in sorted(shim.iterdir()) if p.is_file() and not p.name.endswith(".in") and p.name != h and re.search(r'#\s*include\s*"%s"' % re.escape(h), p.read_text(errors="replace"))]
            self.assertTrue(users, h)
            for u in users:
                self.assertTrue(u.endswith("_host_test.c") or u in heads, "%s is included by %s: not host-test-only" % (h, u))

    def test_the_variant_table_matches_what_build_sh_links(self):
        """Review gate-2x-s2-r1 F2 (mutants M1, M8): per build-* variant, parse shim/build.sh (flag -> variables, link rules, object -> source) and the variant's build.sh calls in
        check-all.sh; every source (.c and its .h) the variant LINKS must select that variant, and its family must be in gate-manifest VARIANTS."""
        links = build_sh_links()
        self.assertGreaterEqual(len(links), 11)
        self.assertEqual(links["build-wlan"] >= {"acpm", "wlanprobe", "wlanrails", "wlansmc", "wlanen", "usbgadget"}, True, links["build-wlan"])
        self.assertIn("acpm", links["build-gpuclk"])
        self.assertEqual(links["build-fast"], set())
        for v, stems in sorted(links.items()):
            for stem in sorted(stems):
                fam = [k for k, globs in gm.FAMILIES.items() if any(fnmatch.fnmatchcase("shim/%s.c" % stem, x) for x in globs)]
                self.assertEqual(len(fam), 1, "%s: source %s belongs to no single family" % (v, stem))
                self.assertIn(fam[0], gm.VARIANTS[v], "%s links %s.c but gate-manifest VARIANTS lacks family %s" % (v, stem, fam[0]))
                for ext in (".c", ".h"):
                    if (g.REPO / "shim" / (stem + ext)).exists():
                        self.assertIn(v, row_select(["shim/" + stem + ext]), "%s links %s%s but the manifest row does not select it" % (v, stem, ext))

    def test_the_committed_manifest_is_a_fresh_render(self):
        """Pure form of `gate-manifest --check` (so a generator mutant that is not regenerated fails the pure suites too)."""
        self.assertEqual(json.loads(gm.dumps(gm.render(g.REPO))), g.MANIFEST)

    def test_manifest_drift_selects_full_and_names_the_step(self):
        """Review gate-2x-s2-r1 F7: a step of check-all.sh ALL with no manifest row, or a row not in ALL, is never silently run or skipped: loud line + every step."""
        lacking = copy.deepcopy(g.MANIFEST)
        lacking["steps"] = [r for r in lacking["steps"] if r["step"] != "rootless"]
        extra = copy.deepcopy(g.MANIFEST)
        extra["steps"].append(dict(extra["steps"][-1], step="ghost-step"))
        for m, step in ((lacking, "rootless"), (extra, "ghost-step")):
            lines, ran, skipped = g.explain(["docs/README.md"], m)
            self.assertIn("gate: MANIFEST DRIFT: %s" % step, lines)
            self.assertEqual(skipped, 0)
            self.assertIn(step, g.select(["docs/README.md"], m))
            self.assertEqual(set(g.select(["docs/README.md"], m)), set(g.ALL) | {step})
        self.assertFalse([l for l in g.explain(["docs/README.md"])[0] if "DRIFT" in l])


class Property(unittest.TestCase):
    """The selector against an oracle of its own: the always-class is never skipped for any changed file; docs stay cheap; unresolved means FULL."""

    def test_enough_samples(self):
        self.assertGreaterEqual(len(SAMPLES), 30)
        self.assertEqual(len(set(SAMPLES)), len(SAMPLES))

    def test_every_change_selects_validate_rules_public_split(self):
        for f in SAMPLES:
            sel = g.select([f])
            for st in EVERY_CHANGE:
                self.assertIn(st, sel, "%s misses %s" % (f, st))

    def test_the_always_class_is_never_skipped_when_an_input_changed(self):
        rows = [r for r in g.MANIFEST["steps"] if r["class"] == "always"]
        self.assertEqual({r["step"] for r in rows}, ALWAYS)
        for f in SAMPLES:
            sel = g.select([f])
            for r in rows:
                followed = any(x in sel for x in r.get("follows", []))
                if oracle_hit(r, f) or followed:
                    self.assertIn(r["step"], sel, "always step %s skipped for %s" % (r["step"], f))
        # the property has teeth: boot-safety reads every shim file, shellcheck every shell file, mutant-needles the mutant tables, phone-converge the run-path tools
        self.assertIn("boot-safety", g.select(["shim/ufs.c"]))
        self.assertIn("shellcheck", g.select(["tools/x.sh"]))
        self.assertIn("mutant-needles", g.select(["tools/test_el2_mutants.py"]))
        self.assertIn("phone-converge", g.select(["tools/xrun"]))
        self.assertIn("inputs", g.select(["shim/ufs.c"]))

    def test_an_unresolved_file_selects_every_step_and_says_so(self):
        for f in ("unknown-dir/new-file.bin", "Makefile.unknown", "weird"):
            self.assertEqual(g.select([f]), g.ALL, f)
            lines, ran, skipped = g.explain([f])
            self.assertEqual(lines[0], "gate: FULL, unresolved: %s" % f)
            self.assertEqual((ran, skipped), (len(g.ALL), 0))
            self.assertEqual(len([l for l in lines if l.startswith("gate: ran ")]), len(g.ALL))
        self.assertEqual(g.select(["docs/README.md", "unknown-dir/x.bin"]), g.ALL)
        lines, _, _ = g.explain(["shim/ufs.c", "a.unknown", "b.unknown"])
        self.assertEqual([l for l in lines if l.startswith("gate: FULL")], ["gate: FULL, unresolved: a.unknown", "gate: FULL, unresolved: b.unknown"])

    def test_docs_only_selects_at_most_four_steps(self):
        for f in ("docs/README.md", "docs/specs/SPEC-X.md", "AGENTS.md", "NOTES.md", "shim/AGENTS.md", "LICENSE"):
            self.assertLessEqual(len(g.select([f])), 4, f)
        self.assertEqual(g.select(["docs/README.md"]), list(EVERY_CHANGE))
        self.assertEqual(g.explain(["docs/README.md"])[1:], (4, 31))

    def test_a_gpu_only_shim_change_selects_fewer_builds_than_build_sh(self):
        def builds(f):
            return {x for x in row_select([f]) if x.startswith("build")}
        full = builds("shim/build.sh")
        self.assertEqual(len(full), 12)
        for f in ("shim/gpu.c", "shim/gpuclk.c", "shim/gpu_new.c"):
            gpu = builds(f)
            self.assertLess(len(gpu), len(full), f)
            self.assertIn("build-gpu", gpu)
            for other in ("build-fast", "build-usb", "build-wlan", "build-touch", "build-udump"):
                self.assertNotIn(other, gpu, "%s selects %s" % (f, other))

    def test_a_patch_change_selects_one_of_the_six_unit_class_steps(self):
        sel = g.select(["xnu/patches/0092-sweet-touch-cmu-top.patch"])
        six = ("unit", "phone-state", "hang-recover", "probe-run", "phone-converge", "rootless")
        self.assertEqual([x for x in six if x in sel], ["unit"])

    def test_a_patch_the_manifest_does_not_know_that_edits_the_chip_writer_selects_the_wlan_steps(self):
        """The manifest is static: a patch added after `gate-manifest --write` is judged by its content (review gate-runfast-1 C2)."""
        old = g.REPO
        with tempfile.TemporaryDirectory() as d:
            try:
                g.REPO = pathlib.Path(d)
                (g.REPO / "xnu" / "patches").mkdir(parents=True)
                (g.REPO / "xnu/patches/0999-gs101-other.patch").write_text("--- a/bsd/dev/sweet_wlan_wr.h\n+++ b/bsd/dev/sweet_wlan_wr.h\n")
                (g.REPO / "xnu/patches/0998-unrelated.patch").write_text("--- a/bsd/dev/sweet_touch.c\n+++ b/bsd/dev/sweet_touch.c\n")
                for st in ("wlan-mutants", "mutant-needles"):
                    self.assertIn(st, g.select(["xnu/patches/0999-gs101-other.patch"]), st)
                    self.assertNotIn(st, g.select(["xnu/patches/0998-unrelated.patch"]), st)
            finally:
                g.REPO = old

    def test_a_deleted_or_edited_patch_is_judged_by_its_content_at_base(self):
        """Review gate-2x-s2-r1 F3: a patch gone from the work tree (or edited to drop its wlan hunk) still selects the wlan steps, read with `git show BASE:path`."""
        old = (g.REPO, g.BASE)
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, check=True).stdout.strip()
            try:
                git("init", "-q", ".")
                git("config", "user.email", "t@t")
                git("config", "user.name", "t")
                (pathlib.Path(d) / "xnu" / "patches").mkdir(parents=True)
                for n, body in (("9990-gone", "--- a/bsd/dev/sweet_wlan_wr.h\n+++ b/bsd/dev/sweet_wlan_wr.h\n"), ("9991-edited", "--- a/bsd/dev/sweet_wlan_wr.h\n+++ b/bsd/dev/sweet_wlan_wr.h\n"), ("9992-other", "--- a/bsd/dev/sweet_touch.c\n+++ b/bsd/dev/sweet_touch.c\n")):
                    (pathlib.Path(d) / "xnu" / "patches" / (n + ".patch")).write_text(body)
                git("add", "-A")
                git("commit", "-qm", "c0")
                base = git("rev-parse", "HEAD")
                (pathlib.Path(d) / "xnu/patches/9990-gone.patch").unlink()
                (pathlib.Path(d) / "xnu/patches/9991-edited.patch").write_text("--- a/bsd/dev/sweet_touch.c\n+++ b/bsd/dev/sweet_touch.c\n")
                g.REPO = pathlib.Path(d)
                for name in ("9990-gone", "9991-edited"):
                    f = "xnu/patches/%s.patch" % name
                    g.BASE = None
                    self.assertIsNone(g.patch_edits(f, ("bsd/dev/sweet_wlan",)), f + " without a base: the work tree only")
                    g.BASE = base
                    self.assertIsNotNone(g.patch_edits(f, ("bsd/dev/sweet_wlan",)), f)
                    for st in ("wlan-mutants", "mutant-needles"):
                        self.assertIn(st, g.select([f]), "%s %s" % (st, f))
                self.assertIsNone(g.patch_edits("xnu/patches/9992-other.patch", ("bsd/dev/sweet_wlan",)))
            finally:
                g.REPO, g.BASE = old

    def test_explain_lines_and_counts(self):
        for files in (["docs/README.md"], ["shim/gpu.c"], ["tools/qrmerge.py", "xnu/patches/0092-sweet-touch-cmu-top.patch"], ["shim/build.sh"]):
            lines, ran, skipped = g.explain(files)
            self.assertEqual(len(lines), len(g.ALL))
            self.assertEqual(ran + skipped, len(g.ALL))
            ran_l = [l for l in lines if l.startswith("gate: ran ")]
            skip_l = [l for l in lines if l.startswith("gate: skipped ")]
            self.assertEqual((len(ran_l), len(skip_l)), (ran, skipped))
            self.assertEqual(ran, len(g.select(files)))
            self.assertEqual([re.match(r"gate: (?:ran|skipped) ([\w-]+): ", l).group(1) for l in lines], g.ALL)
            self.assertTrue(all(re.match(r"gate: skipped [\w-]+: no input changed \(.+\)$", l) for l in skip_l))


class Wired(unittest.TestCase):
    """The tools as the gate calls them (subprocess): the generator's --check and the --explain CLI."""

    def test_gate_manifest_check_passes(self):
        """The committed tools/gate-manifest.json equals a fresh render and is complete (wired into the unit step through this file)."""
        r = subprocess.run([sys.executable, "-I", str(g.REPO / "tools" / "gate-manifest"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.strip().splitlines()[-1], r"^RESULT: PASS gate-manifest steps=\d+ tables=\d+$")

    def test_explain_cli_format(self):
        r = subprocess.run([sys.executable, "-I", str(g.REPO / "tools" / "gate_select.py"), "--explain", "docs/README.md"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[-1], "RESULT: PASS gate_select ran=4 skipped=31")
        self.assertEqual(len(lines), len(g.ALL) + 1)
        self.assertEqual(sum(l.startswith("gate: ran ") for l in lines), 4)
        self.assertEqual(sum(l.startswith("gate: skipped ") for l in lines), 31)
        r = subprocess.run([sys.executable, "-I", str(g.REPO / "tools" / "gate_select.py"), "--explain", "mystery.bin"], capture_output=True, text=True)
        self.assertEqual(r.stdout.splitlines()[0], "gate: FULL, unresolved: mystery.bin")
        self.assertEqual(r.stdout.splitlines()[-1], "RESULT: PASS gate_select ran=%d skipped=0" % len(g.ALL))

    def test_usage_error_exits_2(self):
        r = subprocess.run([sys.executable, "-I", str(g.REPO / "tools" / "gate_select.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_a_stamp_run_names_the_unresolved_file_on_stderr(self):
        """End to end through a throwaway git repo: base commit, then an untracked unknown file -> stdout lists every step, stderr says why."""
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, check=True).stdout.strip()
            git("init", "-q", ".")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            pathlib.Path(d, "a.txt").write_text("a")
            git("add", "-A")
            git("commit", "-qm", "c0")
            pathlib.Path(d, "stamp").write_text(git("rev-parse", "HEAD") + "\n")
            pathlib.Path(d, "mystery.bin").write_text("x")
            env = dict(os.environ, GATE_PASS_DIR=d + "/no-registry")
            r = subprocess.run([sys.executable, "-I", str(g.REPO / "tools" / "gate_select.py"), d + "/stamp"], cwd=d, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), g.ALL)
            self.assertIn("gate: FULL, unresolved: mystery.bin", r.stderr.splitlines())
            self.assertIn("gate: FULL, unresolved: stamp", r.stderr.splitlines())


# ---- mutants of the selector and the manifest ---------------------------------------------------------------------------------------------------------------
# Same style as tools/test_el2_mutants.py: each mutant edits a SCRATCH copy of gate_select.py / gate-manifest / gate-manifest.json (a fresh mkdtemp, never the repo) and must make
# the pure suites (Select, Manifest, Property: no git, no phone) FAIL with the mutated module swapped in for `g` / `gm`. `input` names what separates the mutant from the original (AP-169).
# The table is called SELECTOR_MUTATIONS, not *_MUTANTS: tools/lint-mutant-needles and the manifest's mutant_tables scan test_*.py for `*_MUTANTS` tables.
# kind: sel = source edit of gate_select.py, gm = source edit of gate-manifest, man = edit of the manifest data (callable on the dict)
def _drop_input(step, entry):
    def mutate(m):
        next(r for r in m["steps"] if r["step"] == step)["inputs"].remove(entry)
    return mutate


def _set(step, key, val):
    def mutate(m):
        next(r for r in m["steps"] if r["step"] == step)[key] = val
    return mutate


def _merge_variants(m):
    m["steps"] = [r for r in m["steps"] if r["step"] not in ("build-gpupower", "build-gpuclk", "build-gpurails", "build-gpurailon", "build-gpujob")]


def _widen(step, entry):
    def mutate(m):
        next(r for r in m["steps"] if r["step"] == step)["inputs"].append(entry)
    return mutate


SELECTOR_MUTATIONS = [
    # (id, kind, edit, input that makes it differ)
    ("drop-always-step", "sel", ('        if r["inputs"] == ["*"]:\n            why', '        if r["inputs"] == ["*"] and r["step"] != "rules":\n            why'), "any change: rules must run"),
    ("unresolved-skipped-silently", "sel", ("    if bad or unresolved(files, m):\n        return every", "    if bad:\n        return every"), "unknown-dir/new-file.bin: must select FULL"),
    ("unresolved-line-text", "sel", ('"gate: FULL, unresolved: %s" % f for f in un', '"gate: FULL unresolved: %s" % f for f in un'), "the printed line without the comma"),
    ("explain-ran-off-by-one", "sel", ("return lines, len(steps), len(m[\"steps\"]) - len(steps)", "return lines, len(steps) + 1, len(m[\"steps\"]) - len(steps)"), "docs/README.md: ran=3 not 4"),
    ("explain-skipped-off-by-one", "sel", ("len(m[\"steps\"]) - len(steps)\n", "len(m[\"steps\"]) - len(steps) - 1\n"), "docs/README.md: skipped=31 not 30"),
    ("shell-predicate-off", "sel", ('row.get("predicate") == "shell" and is_shell(f)', 'row.get("predicate") == "shell" and False'), "tools/x.sh must select shellcheck"),
    ("follows-ignored", "sel", ('fol = [s for s in r.get("follows", []) if s in why]', "fol = []"), "shim/ufs.c must select inputs"),
    ("patch-content-ignored", "sel", ('return patch_edits(f, row["patch_edits"]) if row.get("patch_edits") else None', "return None"), "a new patch that edits sweet_wlan_wr.h"),
    ("inert-matches-everything", "sel", ('fnmatch.fnmatchcase(f, g) for g in m["inert"]', 'fnmatch.fnmatchcase(f, "*") for g in m["inert"]'), "unknown-dir/new-file.bin resolves"),
    ("unresolved-ignores-the-predicate", "sel", ('    if row.get("predicate") == "shell" and is_shell(f):\n        return "shell file"\n    return patch', '    return patch'), "tools/x.sh: shellcheck row skipped (also: a .sh file would resolve only through the predicate)"),
    ("except-ignored", "sel", ('and not any(fnmatch.fnmatchcase(f, x) for x in e.get("except", []))', ""), "shim/AGENTS.md selects a build"),
    ("completes-full-never", "sel", ("    if all(r[\"step\"] in why for r in rows if r[\"step\"] not in cf):", "    if False:"), "shim/build.sh + a patch + a mutants test: lint-c joins"),
    ("drift-ignored", "sel", ("    if bad or unresolved(files, m):\n        return every\n    return decide", "    if unresolved(files, m):\n        return every\n    return decide"), "a manifest without the rootless row must select FULL"),
    ("drift-line-text", "sel", ('"gate: MANIFEST DRIFT: %s" % s', '"gate: MANIFEST DRIFT %s" % s'), "the printed line without the colon"),
    ("drift-one-sided", "sel", ("return [s for s in ca if s not in rows] + [s for s in rows if s not in ca], ca +", "return [s for s in ca if s not in rows], ca +"), "a row that is not a step of ALL"),
    ("patch-base-ignored", "sel", ("    if BASE:      # a patch", "    if False:      # a patch"), "a deleted patch that edited sweet_wlan_wr.h"),
    ("variant-wlan-lacks-acpm", "gm", ('"build-wlan": ["usbgadget", "wlan", "acpm"],', '"build-wlan": ["usbgadget", "wlan"],'), "review M1: shim/acpm.c must select build-wlan"),
    ("variant-gpuclk-lacks-acpm", "gm", ('"build-gpuclk": ["usbgadget", "gpu", "acpm"],', '"build-gpuclk": ["usbgadget", "gpu"],'), "review M8: shim/acpm.c must select build-gpuclk"),
    ("variant-touch-lacks-acpm", "gm", ('"build-touch": ["usbgadget", "touch", "acpm"],', '"build-touch": ["usbgadget", "touch"],'), "touch rails/dvfs link acpm"),
    ("host-only-takes-wlan-sources", "gm", ('HOST_ONLY = ["shim/*_host_test.c", "shim/sweet_wlan_*.h"]', 'HOST_ONLY = ["shim/*_host_test.c", "shim/sweet_wlan_*.h", "shim/wlan_*"]'), "review F1: shim/wlan_new.c must select build-wlan"),
    ("variant-row-lacks-acpm", "man", _drop_input("build-gpuclk", {"glob": "shim/acpm*", "except": ["shim/*_host_test.c"]}), "the data row loses acpm although gm.VARIANTS has it"),
    ("swap-lane", "man", _set("unit", "lane", "C"), "unit is `step B unit`"),
    ("drop-input-glob", "man", _drop_input("unit", "xnu/patches/*"), "a patch change must select unit"),
    ("budget-zero", "man", _set("wlan-mutants", "budget_s", 0), "budget_s > 0"),
    ("variants-merged", "man", _merge_variants, "five build-gpu* variants lose their own rows"),
    ("always-step-class-quick", "man", _set("boot-safety", "class", "quick"), "boot-safety is an always step"),
    ("always-step-class-formal", "man", _set("phone-converge", "class", "formal"), "phone-converge is an always step"),
    ("udump-takes-gpu-files", "man", _widen("build-udump", "shim/gpu*"), "shim/gpu.c must not select build-udump"),
    ("lane-removed", "man", _set("rootless", "lane", None), "every row has a lane"),
    ("validate-input-narrowed", "man", _set("validate", "inputs", ["tools/*"]), "validate runs for every change"),
    # generator (tools/gate-manifest): the fixtures of Manifest must fail on a generator that stopped checking
    ("gm-budget-check-weakened", "gm", ('r["budget_s"] <= 0', 'r["budget_s"] < 0'), "budget_s 0"),
    ("gm-missing-row-ignored", "gm", ('            out.append("step %s of check-all.sh ALL has no manifest row" % s)\n            continue', "            continue"), "a step without a row"),
    ("gm-table-check-off", "gm", ("if not any(match_entry(e, f) for e in needles):", "if False:"), "tools/test_foo.py: FOO_MUTANTS reached by no needles glob"),
    ("gm-build-ceiling-off", "gm", ("    elif len(names) > cap:", "    elif False:"), "a 13th build row"),
    ("gm-ceiling-not-called", "gm", ("    out.extend(ceiling_problems(m, repo, allsteps))\n", ""), "a 13th build row"),
    ("gm-block-dup-allowed", "gm", ("        if len(keys) > 1:", "        if len(keys) > 99:"), "block bt has 2 mutant tables"),
    ("gm-baseline-rise-allowed", "gm", ("c[\"build_variants\"] > base[\"build_variants\"]", "c[\"build_variants\"] > base[\"build_variants\"] + 99"), "does not rise"),
    ("gm-variant-not-in-all", "gm", ("        if s not in allsteps:\n            out.append(\"step %s runs in a lane", "        if False:\n            out.append(\"step %s runs in a lane"), "step A build-extra not in ALL"),
]
PURE = (Select, Manifest, Property)


def load_scratch(path, edit, name, scratch, manifest_path=None):
    """Import a scratch copy of SOURCE PATH with EDIT=(old, new) applied exactly once; REPO pinned to the real repo (the suites read the real check-all.sh / shim / patches)."""
    text = pathlib.Path(path).read_text()
    if edit:
        if text.count(edit[0]) != 1:
            raise AssertionError("mutant pattern found %d times (want 1) in %s: %r" % (text.count(edit[0]), path, edit[0][:70]))
        text = text.replace(edit[0], edit[1])
    repo_line = "REPO = pathlib.Path(__file__).resolve().parent.parent"
    assert text.count(repo_line) == 1
    text = text.replace(repo_line, "REPO = pathlib.Path(%r)" % str(g.REPO))
    if manifest_path:
        line = 'MANIFEST_PATH = REPO / "tools" / "gate-manifest.json"'
        assert text.count(line) == 1
        text = text.replace(line, "MANIFEST_PATH = pathlib.Path(%r)" % str(manifest_path))
    f = pathlib.Path(scratch, name)
    f.write_text(text)
    loader = importlib.machinery.SourceFileLoader(f.stem.replace("-", "_") + "_mut", str(f))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_pure_suites(gmod, gmmod):
    """True when the pure suites PASS with gmod / gmmod swapped in for the module globals `g` / `gm`."""
    me = sys.modules[__name__] if __name__ != "__main__" else sys.modules["__main__"]
    old = (me.g, me.gm)
    me.g, me.gm = gmod, gmmod
    try:
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in PURE)
        return unittest.TextTestRunner(stream=io.StringIO(), failfast=True).run(suite).wasSuccessful()
    finally:
        me.g, me.gm = old


class SelectorMutants(unittest.TestCase):
    def test_the_unmutated_modules_pass_the_same_harness(self):
        """AP-169: a harness that can never pass would 'kill' every mutant for the wrong reason."""
        self.assertTrue(run_pure_suites(g, gm))
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(run_pure_suites(load_scratch(g.REPO / "tools" / "gate_select.py", None, "gate_select.py", d), gm))

    def test_every_mutant_is_killed(self):
        killed, survived = 0, []
        with tempfile.TemporaryDirectory() as d:
            for mid, kind, edit, why in SELECTOR_MUTATIONS:
                if kind == "sel":
                    mg, mgm = load_scratch(g.REPO / "tools" / "gate_select.py", edit, "gate_select.py", d), gm
                elif kind == "gm":
                    mg, mgm = g, load_scratch(g.REPO / "tools" / "gate-manifest", edit, "gate-manifest", d)
                else:
                    m = copy.deepcopy(g.MANIFEST)
                    edit(m)
                    mp = pathlib.Path(d, "gate-manifest.json")
                    mp.write_text(json.dumps(m))
                    mg, mgm = load_scratch(g.REPO / "tools" / "gate_select.py", None, "gate_select.py", d, manifest_path=mp), gm
                if run_pure_suites(mg, mgm):
                    survived.append("%s (%s)" % (mid, why))
                else:
                    killed += 1
        print("\nMUTANTS gate_select/gate-manifest: killed %d/%d" % (killed, len(SELECTOR_MUTATIONS)), file=sys.stderr)
        self.assertEqual(survived, [], "SURVIVED: %s" % survived)
        self.assertEqual(killed, len(SELECTOR_MUTATIONS))



if __name__ == "__main__":
    unittest.main()
