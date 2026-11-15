"""Self-check of tools/unit_select.py (PLAN-GATE-2X S4): which unit test files a quick gate runs for a change set.
(a) property over the real tree: every file a test names (path substring or tools/ basename token, computed here without the selector's tokenizer) selects that test;
(b) named cases: gate-summary / heavy-job / docs / check-all.sh; (c) fixture tree incl. unresolved tests; (d) selection sizes of 5 real one-file changes (printed);
(e) MUTANTS: text edits of unit_select.py that the fixture + the real-tree checks must detect (a mutant that survives is a hole in the selector's guard)."""
import importlib.util, os, pathlib, re, shutil, subprocess, sys, tempfile, types, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import unit_select as us

# review gate-cold-s4-r2 N4: (bare command word, a shell line it follows) -> tools/sep-tool.sh calls each; tools/<word> names conf/sep_<word>.cfg, so the cfg is selected only if the word is a followed call
SEPLINES = [
    ("semitool", "echo a; semitool"), ("pipetool", "echo b | pipetool"), ("subtool", "( subtool )"), ("ticktool", "x=`ticktool`"), ("bracetool", "{ bracetool; }"),
    ("dolltool", "y=$(dolltool)"), ("andtool", "true && andtool"), ("ortool", "false || ortool"), ("amptool", "true & amptool"), ("casetool", "case $1 in a) casetool;; esac"),
    ("thentool", "if true; then thentool; fi"), ("dotool", "for i in 1; do dotool; done"), ("elsetool", "if x; then y; else elsetool; fi"), ("eliftool", "if x; then y; elif eliftool; then z; fi"),
    ("iftool", "if iftool; then y; fi"), ("whiletool", "while whiletool; do y; done"), ("untiltool", "until untiltool; do y; done"), ("exectool", "exec exectool"),
    ("xargstool", "ls | xargs xargstool"), ("sudotool", "sudo sudotool"), ("envtool", "env envtool"), ("nicetool", "nice nicetool"), ("timetool", "time timetool"), ("cmdwtool", "command cmdwtool"),
    ("assigntool", "FOO=1 assigntool"), ("flagtool", "sudo -E flagtool"), ("negtool", "! negtool"), ("bashtool", "bash bashtool"), ("shtool", "sh shtool"), ("srctool", "source srctool"),
    ("dottool", ". dottool"), ("py3tool", "python3 py3tool"), ("pytool", "python pytool"),
]
FIXTURE = {
    "tools/test_alpha.py": "import subprocess\nsubprocess.run(['tools/alpha-tool'])\n",
    "tools/alpha-tool": "#!/bin/bash\necho hi\n",
    "tools/test_beta.py": "import helper_mod\n",
    "tools/helper_mod.py": "import deep_mod\nX = 'shim/foo.c'\n",
    "tools/deep_mod.py": "Y = 'conf/deep.cfg'\n",
    "tools/test_gamma.py": "exec('1')\n",
    "tools/test_delta.py": "import pathlib\nROOT = pathlib.Path(__file__).resolve().parent.parent\nfor p in (ROOT / 'data').glob('*.txt'):\n    pass\n",
    "tools/test_eps.py": "A = 'sub/deep/foo.conf'\n",
    "tools/test_eps2.py": "B = 'bar.conf'\n",
    "tools/test_zeta.py": "import pathlib\nHERE = pathlib.Path(__file__).parent\nL = list(HERE.glob('test_*.py'))\n",
    "tools/test_eta.py": "def broken(:\n",
    "tools/test_gate_thing.py": "X = 1\n",
    "tools/test_theta.py": "import pathlib\nTOOLS = pathlib.Path(__file__).parent\nR = [str(TOOLS / 'alpha-tool'), str(TOOLS)]\n",
    "tools/test_iota.py": "import os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nL = os.listdir(os.path.join(ROOT, 'logs', 'runs'))\n",
    "tools/test_mu.py": "N = '0155'\n",
    "tools/test_kappa.py": "D = 'xnu/conf'\n",
    "tools/test_lambda.py": "import subprocess\nsubprocess.run(['tools/named-tool'])\n",
    "tools/named-tool": "#!/usr/bin/env python3\nimport named_dep\n",
    "tools/named_dep.py": "import named_deep\nZ = 1\n",
    "tools/named_deep.py": "ND = 'conf/nd.cfg'\n",
    "conf/nd.cfg": "x\n",
    "tools/test_wlan_wr_mutants.py": "X = 'tools/other.py'\n",
    "tools/test_nu.py": "import pathlib\nP = pathlib.Path(__file__).resolve().parent.parent / 'xnu' / 'conf'\nL = sorted(P.glob('*.cfg'))\n",
    "tools/other.py": "pass\n",
    "docs/README.md": "x\n",
    "shim/foo.c": "int x;\n",
    "conf/deep.cfg": "x\n",
    "data/a.txt": "x\n",
    "sub/deep/foo.conf": "x\n",
    "other/bar.conf": "x\n",
    "xnu/conf/a.cfg": "x\n",
    "xnu/patches/0155-x.patch": "p\n",
    "tools/check-all.sh": "#!/bin/bash\n",
    "tools/gate-manifest.json": "{}\n",
    "tools/gate-unit-shards": "#!/bin/bash\n",
    "soc/gs101/soc.json": "{}\n",
    "device/google/bluejay/board.json": "{}\n",
    # F2: package imports
    "tools/test_pkg.py": "from tools import pkgmod\n",
    "tools/pkgmod.py": "Q = 'conf/pkg.cfg'\n",
    "conf/pkg.cfg": "x\n",
    "tools/test_pkg2.py": "import tools.pkgmod2\n",
    "tools/pkgmod2.py": "pass\n",
    "tools/test_pkg3.py": "from tools.mypkg import inner\nimport helperpkg\nfrom helperpkg import sub\n",
    "tools/mypkg/__init__.py": "",
    "tools/mypkg/inner.py": "from . import sibling\nfrom .sibling import thing\n",
    "tools/mypkg/sibling.py": "S = 'conf/sibling.cfg'\n",
    "conf/sibling.cfg": "x\n",
    "tools/helperpkg/__init__.py": "H = 'conf/pkginit.cfg'\n",
    "tools/helperpkg/sub.py": "pass\n",
    "conf/pkginit.cfg": "x\n",
    # F3: a test wrapping a shell test: the script text is followed (calls, not mentions), SHELL_DEPTH levels
    "tools/test_wrapped.py": "import subprocess\nsubprocess.run(['bash', 'tools/test_wrapped.sh'])\n",
    "tools/test_wrapped.sh": "#!/bin/bash\nhere=$(dirname \"$0\")\nbash $here/real-tool.sh\n$here/noext-tool\n",
    "tools/real-tool.sh": "#!/bin/bash\n. tools/lib-sh.sh\npython3 tools/real_helper.py\n# see tools/commented-tool.sh\necho \"later: hubby\"\ncmdtool --flag\n",
    "tools/lib-sh.sh": "#!/bin/bash\nsource tools/deeper.sh\nL='conf/lib.cfg'\n",
    "tools/deeper.sh": "#!/bin/bash\ntools/deepest.sh\n",
    "tools/deepest.sh": "#!/bin/bash\nD='conf/deepest.cfg'\n",
    "tools/noext-tool": "#!/usr/bin/env bash\nN='conf/noext.cfg'\n",
    "tools/real_helper.py": "import real_dep\nA = 'conf/helper.cfg'\n",
    "tools/real_dep.py": "pass\n",
    "tools/commented-tool.sh": "#!/bin/bash\nC='conf/commented.cfg'\n",
    "tools/hubby": "#!/bin/bash\nH='conf/hub.cfg'\n",
    "tools/cmdtool": "#!/bin/bash\nM='conf/cmd.cfg'\n",
    "conf/lib.cfg": "x\n", "conf/deepest.cfg": "x\n", "conf/noext.cfg": "x\n", "conf/helper.cfg": "x\n",
    "conf/commented.cfg": "x\n", "conf/hub.cfg": "x\n", "conf/cmd.cfg": "x\n",
    # always-run globs and the other unresolved cases
    "tools/test_audit_memo_a.py": "X = 1\n",
    "tools/test_lint_mutant_needles_a.py": "X = 1\n",
    "tools/test_check_all_a.py": "X = 1\n",
    "tools/test_unit_select_a.py": "X = 1\n",
    "tools/test_lsfiles.py": "import subprocess\nsubprocess.run(['git', 'ls-files'])\n",
    "tools/gate_select.py": "import subprocess\nsubprocess.run(['git', 'ls-files'])\n",
    "tools/unit_select.py": "import subprocess\nsubprocess.run(['git', 'ls-files', '--others'])\n",
    "tools/test_gsel.py": "import gate_select\n",
    "tools/test_usel.py": "import unit_select\n",
    "tools/test_ann.py": "import pathlib\nROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent\nL = list(ROOT.glob('*'))\n",
    "tools/test_cwd.py": "import os\nL = os.listdir(os.getcwd())\n",
    # bounded listings by the spelling of the path
    "tools/test_rel.py": "import pathlib\nHERE = pathlib.Path(__file__).parent\nL = list((HERE / 'assets').glob('*'))\n",
    "tools/assets/a.bin": "x\n",
    "tools/test_up.py": "import os\nHERE = os.path.dirname(os.path.abspath(__file__))\nL = os.listdir(os.path.join(HERE, '../updata'))\n",
    "updata/u.txt": "x\n",
    "tools/test_joinp.py": "import pathlib\nROOT = pathlib.Path(__file__).resolve().parent.parent\nL = list(ROOT.joinpath('jdir').glob('*'))\n",
    "jdir/j.txt": "x\n",
    "tools/test_osj.py": "import os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nL = os.listdir(os.path.join(ROOT, 'ojdir'))\n",
    "ojdir/o.txt": "x\n",
    "tools/test_names_gone.py": "T = 'tools/gone_tool.py'\n",
    "tools/test_gone.py": "X = 1\n",     # in the file list but not on disk: a deleted test
    # review gate-cold-s4-r2 N1: a listing whose root comes in through a function parameter (test_selftests_wired: `def read_tree(d=TOOLS): Path(d).iterdir()`)
    "tools/test_param.py": "import pathlib\nTOOLS = pathlib.Path(__file__).resolve().parent\ndef read_tree(d=TOOLS):\n    return sorted(pathlib.Path(d).iterdir())\n",
    "tools/test_param_kw.py": "import pathlib\nTOOLS = pathlib.Path(__file__).resolve().parent\ndef read_tree(*, d=TOOLS):\n    return sorted(pathlib.Path(d).iterdir())\n",
    "tools/test_param_call.py": "import pathlib\nTOOLS = pathlib.Path(__file__).resolve().parent\ndef read_tree(d):\n    return sorted(pathlib.Path(d).iterdir())\nread_tree(TOOLS)\n",
    "tools/test_param_meth.py": "import pathlib, unittest\nTOOLS = pathlib.Path(__file__).resolve().parent\nclass T(unittest.TestCase):\n    def rd(self, d):\n        return sorted(pathlib.Path(d).iterdir())\n    def test_a(self):\n        self.rd(TOOLS)\n",
    "tools/test_param_b.py": "import pathlib\nROOT = pathlib.Path(__file__).resolve().parent.parent\ndef rd(d=ROOT / 'pdata'):\n    return sorted(pathlib.Path(d).iterdir())\n",     # a default that is a bounded directory
    "pdata/p.txt": "x\n",
    # N2: tools/test_tools_sync.py reads the files a data file lists: always run
    "tools/test_tools_sync.py": "X = 1\n",
    # N3: a module loaded by a built file name
    "tools/test_dyn.py": "import importlib.util, pathlib\nHERE = pathlib.Path(__file__).resolve().parent\ndef load(name):\n    return importlib.util.spec_from_file_location(name, HERE / (name + '.py'))\nload('dynmod')\n",
    "tools/dynmod.py": "pass\n",
    "tools/test_dyn_f.py": "def load(name):\n    return open(f'tools/{name}.sh').read()\nload('dynfsh')\n",
    "tools/dynfsh.sh": "#!/bin/bash\n",
    "tools/test_dyn_pct.py": "def load(name):\n    return open('tools/%s.py' % name).read()\nload('dynpct')\n",
    "tools/dynpct.py": "pass\n",
    "tools/test_dyn_fmt.py": "def load(name):\n    return open('tools/{}.py'.format(name)).read()\nload('dynfmt')\n",
    "tools/dynfmt.py": "pass\n",
    # N4: every directory-listing call on an unbounded root is unresolved (one fixture test per LISTING word)
    "tools/test_ls_glob.py": "import pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = list(R.glob('*'))\n",
    "tools/test_ls_rglob.py": "import pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = list(R.rglob('*'))\n",
    "tools/test_ls_iglob.py": "import glob, pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = list(glob.iglob(str(R / '*')))\n",
    "tools/test_ls_iterdir.py": "import pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = list(R.iterdir())\n",
    "tools/test_ls_listdir.py": "import os, pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = os.listdir(R)\n",
    "tools/test_ls_scandir.py": "import os, pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = list(os.scandir(R))\n",
    "tools/test_ls_walk.py": "import os, pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nL = list(os.walk(R))\n",
    "tools/test_ls_copytree.py": "import shutil, pathlib\nR = pathlib.Path(__file__).resolve().parent.parent\nshutil.copytree(R, '/tmp/x')\n",
    # N4: the `./` prefix of a directory token; shell separators and wrappers before a bare command word
    "tools/test_dotslash.py": "D = './dotdir/sub'\n",
    "dotdir/sub/f.txt": "x\n",
    "tools/test_sep.py": "import subprocess\nsubprocess.run(['tools/sep-tool.sh'])\n",
    "tools/sep-tool.sh": "#!/bin/bash\n" + "".join(l + "\n" for _n, l in SEPLINES),
    **{"tools/%s" % n: "#!/bin/bash\nC='conf/sep_%s.cfg'\n" % n for n, _l in SEPLINES},
    **{"conf/sep_%s.cfg" % n: "x\n" for n, _l in SEPLINES},
    "tools/test_param_ckw.py": "import pathlib\nTOOLS = pathlib.Path(__file__).resolve().parent\ndef rd(d=None):\n    return sorted(pathlib.Path(d).iterdir())\nrd(d=TOOLS)\n",
}
GONE = ("tools/test_gone.py",)          # listed in the Tree, never written: a deleted/renamed test file
BASE = {"test_gate_thing.py", "test_gamma.py", "test_zeta.py", "test_eta.py", "test_audit_memo_a.py", "test_lint_mutant_needles_a.py", "test_check_all_a.py", "test_unit_select_a.py",
        "test_lsfiles.py", "test_ann.py", "test_cwd.py", "test_tools_sync.py",
        "test_param.py", "test_param_kw.py", "test_param_call.py", "test_param_meth.py", "test_param_ckw.py",
        "test_ls_glob.py", "test_ls_rglob.py", "test_ls_iglob.py", "test_ls_iterdir.py", "test_ls_listdir.py", "test_ls_scandir.py", "test_ls_walk.py", "test_ls_copytree.py"}   # always run: the gate's own tests (every ALWAYS glob), exec(), unbounded listings (zeta, ann, cwd, param*, ls_*), git ls-files, a syntax error
ALL = sorted(n[6:] for n in FIXTURE if re.fullmatch(r"tools/test_[^/]*\.py", n) and not n.endswith("wlan_wr_mutants.py") and n not in GONE)
# changed file -> the tests that must run on top of BASE
EXPECT = {
    "tools/alpha-tool": {"test_alpha.py", "test_theta.py"},
    "tools/test_alpha.py": {"test_alpha.py"},
    "tools/helper_mod.py": {"test_beta.py"},
    "tools/deep_mod.py": {"test_beta.py"},
    "conf/deep.cfg": {"test_beta.py"},                           # named by an imported module of an imported module
    "shim/foo.c": {"test_beta.py"},
    "data/a.txt": {"test_delta.py"},                             # ROOT / 'data' listing: a bounded directory
    "sub/deep/foo.conf": {"test_eps.py"},
    "other/bar.conf": {"test_eps2.py"},                          # basename match
    "xnu/conf/a.cfg": {"test_kappa.py", "test_nu.py"},          # a directory token / a bounded listing
    "tools/named_dep.py": {"test_lambda.py"},                    # imported by a python tool the test runs
    "tools/named_deep.py": {"test_lambda.py"},                   # ... and its import, transitively
    "conf/nd.cfg": {"test_lambda.py"},                           # ... and the tokens of that imported module
    "tools/pkgmod.py": {"test_pkg.py"},                          # F2: `from tools import X`
    "conf/pkg.cfg": {"test_pkg.py"},
    "tools/pkgmod2.py": {"test_pkg2.py"},                        # F2: `import tools.X`
    "tools/mypkg/inner.py": {"test_pkg3.py"},                    # `from tools.mypkg import inner`
    "tools/mypkg/sibling.py": {"test_pkg3.py"},                  # a relative import inside the package
    "conf/sibling.cfg": {"test_pkg3.py"},
    "tools/mypkg/__init__.py": {"test_pkg3.py"},                 # the package __init__ is imported with its submodule
    "tools/helperpkg/__init__.py": {"test_pkg3.py"},             # `import pkg` -> pkg/__init__.py
    "tools/helperpkg/sub.py": {"test_pkg3.py"},                  # `from pkg import sub` -> pkg/sub.py
    "conf/pkginit.cfg": {"test_pkg3.py"},
    "tools/test_wrapped.sh": {"test_wrapped.py"},                # F3: the wrapped shell test, and everything its text calls
    "tools/real-tool.sh": {"test_wrapped.py"},
    "tools/real_helper.py": {"test_wrapped.py"},                 # a python tool a followed script runs, and its import
    "tools/real_dep.py": {"test_wrapped.py"},
    "conf/helper.cfg": {"test_wrapped.py"},
    "tools/lib-sh.sh": {"test_wrapped.py"},                      # a sourced library: level 3
    "conf/lib.cfg": {"test_wrapped.py"},
    "tools/deeper.sh": {"test_wrapped.py"},                      # level 4 is a dependency (its name is in lib-sh.sh) ...
    "conf/deepest.cfg": set(),                                   # ... but its text is not followed: SHELL_DEPTH bounds the closure
    "tools/noext-tool": {"test_wrapped.py"},                     # a script recognised by its shebang
    "conf/noext.cfg": {"test_wrapped.py"},
    "tools/cmdtool": {"test_wrapped.py"},                        # a command word of a statement is a call
    "conf/cmd.cfg": {"test_wrapped.py"},
    "tools/hubby": {"test_wrapped.py"},                          # a word in an echo is a dependency ...
    "conf/hub.cfg": set(),                                       # ... but not followed
    "tools/commented-tool.sh": {"test_wrapped.py"},              # a comment names it: dependency ...
    "conf/commented.cfg": set(),                                 # ... not followed
    "tools/gate_select.py": {"test_gsel.py"},                    # `git ls-files` there lists names only: not an unresolved reason
    "tools/unit_select.py": {"test_usel.py"},
    "tools/assets/a.bin": {"test_rel.py"},                       # a path relative to tools/
    "updata/u.txt": {"test_up.py"},                              # a leading ../ is dropped
    "jdir/j.txt": {"test_joinp.py"},                             # Path.joinpath
    "ojdir/o.txt": {"test_osj.py"},                              # os.path.join
    "tools/gone_tool.py": {"test_names_gone.py"},                # a deleted file the changed list names still selects the test that mentions it
    "tools/test_gone.py": set(),                                 # a deleted test: dropped, not selected
    "tools/named-tool": {"test_lambda.py"},
    "pdata/p.txt": {"test_param_b.py"},                          # N1: a parameter default that is a bounded directory
    "tools/dynmod.py": {"test_dyn.py"},                          # N3: `name + ".py"`
    "tools/dynfsh.sh": {"test_dyn_f.py"},                        # f"{name}.sh"
    "tools/dynpct.py": {"test_dyn_pct.py"},                      # "%s.py" % name
    "tools/dynfmt.py": {"test_dyn_fmt.py"},                      # "{}.py".format(name)
    "dotdir/sub/f.txt": {"test_dotslash.py"},                    # N4: a `./dir/sub` token is a directory
    "tools/sep-tool.sh": {"test_sep.py"},
    **{"conf/sep_%s.cfg" % n: {"test_sep.py"} for n, _l in SEPLINES},     # N4: the bare command word after each separator / wrapper / prefix is a call, its text is followed
    "tools/other.py": set(),                                     # no test depends on it
    "docs/README.md": set(),
    "tools/test_wlan_wr_mutants.py": set(),                      # its own step, never a unit shard
}
ALLTESTS_ON = ("tools/check-all.sh", "tools/gate-manifest.json", "tools/gate-unit-shards", "xnu/patches/0155-x.patch", "soc/gs101/soc.json", "device/google/bluejay/board.json")


def make_fixture(root):
    for name, text in FIXTURE.items():
        if name in GONE:
            continue
        p = pathlib.Path(root) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return list(FIXTURE)     # includes GONE: in the Tree (git still lists a deleted-unstaged file), absent on disk


def fixture_failures(mod):
    """Every deviation of module `mod` (the real unit_select or a mutant of it) from EXPECT; [] = the selector behaves."""
    bad = []
    with tempfile.TemporaryDirectory() as d:
        files = make_fixture(d)
        try:
            tree = mod.Tree(files, d)       # shared by the changed files the Tree already holds; a name only the changed list holds (a deleted file) builds its own
            for ch, want in EXPECT.items():
                run, lines, total = mod.select_tests([ch], files, d, tree=tree) if ch in files else mod.select_tests([ch], files, d)
                if set(run) != BASE | want:
                    bad.append("%s: ran %s, want %s" % (ch, sorted(set(run) - BASE), sorted(want)))
                if total != len(ALL):
                    bad.append("total %d != %d" % (total, len(ALL)))
            for ch in ALLTESTS_ON:
                run, lines, total = mod.select_tests([ch], files, d)
                if sorted(run) != ALL or not any(l.startswith("gate: unit: ALL, cross-cutting input " + ch) for l in lines):
                    bad.append("%s: not ALL (%d of %d)" % (ch, len(run), len(ALL)))
            run, lines, total = mod.select_tests(["tools/test_gone.py"], files, d)       # F4: a deleted test is dropped, loudly, and is not part of the total
            if "gate: unit_select: dropped deleted test test_gone.py" not in lines or "test_gone.py" in run or total != len(ALL):
                bad.append("deleted test not dropped: %s" % [l for l in lines if "gone" in l])
            run, lines, total = mod.select_tests(["soc/gs101/soc.json", "tools/test_gone.py"], files, d)
            if "gate: unit_select: dropped deleted test test_gone.py" not in lines or "test_gone.py" in run:
                bad.append("deleted test not dropped on a cross-cutting change")
            run, lines, total = mod.select_tests([], files, d)
            if not (set(run) == BASE and "gate: unit test test_zeta.py: dependencies unresolved, always run" in lines
                    and "gate: unit test test_gamma.py: dependencies unresolved, always run" in lines
                    and "gate: unit test test_eta.py: dependencies unresolved, always run" in lines
                    and "gate: skipped unit test test_alpha.py: no input changed" in lines
                    and lines[-1] == "gate: unit shards: ran %d of %d test files" % (len(BASE), len(ALL))
                    and not any("test_iota" in l or "test_theta" in l or "test_nu" in l for l in lines if "unresolved" in l)):
                bad.append("no-change lines: %s" % lines)
            deps, _u = mod.closure(mod.Tree(files, d), "tools/test_mu.py")   # a bare patch number is a dependency on that patch (a patch change selects ALL, so only visible here)
            if "xnu/patches/0155-x.patch" not in deps:
                bad.append("patch number 0155 not resolved to its patch")
            run, _l, _t = mod.select_tests(["tools/test_gate_thing.py"], files, d)
            if "test_gate_thing.py" not in run:
                bad.append("a changed test did not select itself")
        except Exception as e:   # a mutant that crashes the selector is detected too
            bad.append("crash: %r" % (e,))
    return bad


def load_mutant(old, new):
    src = (HERE / "unit_select.py").read_text()
    assert src.count(old) == 1, "mutant anchor %r matches %d times" % (old, src.count(old))   # AP-281: re-anchor on an edit, do not weaken
    mod = types.ModuleType("unit_select_mutant")
    mod.__file__ = str(HERE / "unit_select.py")
    exec(compile(src.replace(old, new), mod.__file__, "exec"), mod.__dict__)
    return mod


MUTANTS = [
    ("drop transitive imports", "        todo += list(pi.mods)\n", "        pass\n"),
    ("drop the basename match", "            out |= self.by_base.get(base, set())", "            out |= set()"),
    ("skip the unresolved case", "        if unres:\n            lines.append", "        if False:\n            lines.append"),
    ("exec/eval not unresolved", 'n.func.id in ("exec", "eval")', 'n.func.id in ("exec_",)'),
    ("cross-cutting directories ignored", "c.startswith(CROSS_DIRS)", "c.startswith(('nowhere/',))"),
    ("cross-cutting files ignored", "c in CROSS_FILES or ", "False or "),
    ("always-run list ignored", "        elif any(fnmatch.fnmatchcase(t, g) for g in ALWAYS):", "        elif False:"),
    ("directory token drops its files", '            if "/" in t and t in self.dirs:', "            if False:"),
    ("a bounded listing adds no dependency", "            self.deps |= files\n", "            pass\n"),
    ("an unbounded listing is bounded", "        if not bounded:", "        if False:"),
    ("listing taint ignored", "elif last in LISTING and self._expr_tainted(n, self.tainted):", "elif False:"),
    ("imported-module tokens dropped", "        deps |= pi.deps | pi.mods\n        todo +=", "        deps |= pi.mods\n        todo +="),
    ("named python tool's imports dropped", "        named |= {f for f in pi.deps if tree.is_py(f)}\n", "        pass\n"),
    ("a changed test does not select itself", "    deps, unres, seen, todo, named, calls = {test}, [], set(), [test], set(), set()", "    deps, unres, seen, todo, named, calls = set(), [], set(), [test], set(), set()"),
    ("changed set not intersected with deps", "        elif deps & ch:", "        elif False:"),
    ("syntax error crashes instead of always-run", "except (SyntaxError, ValueError) as e:", "except ValueError as e:"),
    ("repo root / tools dir counted as a bound", "        if run in UNBOUNDED:\n            return set(), False\n", ""),
    ("wlan mutant file not excluded", 'f.rsplit("/", 1)[-1] != EXCL', "True"),
    ("patch-number tokens dropped", "                out |= self.patches.get(m.group(1), set())\n", "                pass\n"),
    # review gate-cold-s4-r1 F2 / F3 / F4 / F6: the package, shell and boundary cases the first 19 could not see
    ("device/ not cross-cutting", 'CROSS_DIRS = ("xnu/patches/", "soc/", "device/")', 'CROSS_DIRS = ("xnu/patches/", "soc/")'),
    ("test_gate* not always-run", '"test_gate*", ', ""),
    ("test_check_all* not always-run", '"test_check_all*", ', ""),
    ("test_audit_memo* not always-run", '"test_audit_memo*", ', ""),
    ("test_lint_mutant_needles* not always-run", '"test_lint_mutant_needles*", ', ""),
    ("test_unit_select* not always-run", ', "test_unit_select*")', ")"),
    ("git ls-files is not unresolved", 'if "ls-files" in src and rel not in NAMES_ONLY:', "if False:"),
    ("gate_select.py not names-only", 'NAMES_ONLY = ("tools/gate_select.py", "tools/unit_select.py")', 'NAMES_ONLY = ("tools/unit_select.py",)'),
    ("unit_select.py not names-only", 'NAMES_ONLY = ("tools/gate_select.py", "tools/unit_select.py")', 'NAMES_ONLY = ("tools/gate_select.py",)'),
    ("tools/-relative directories dropped", 'for p in (run, "tools/" + run):', "for p in (run,):"),
    ("leading ../ kept", '        while comps and comps[0] == "..":\n            comps.pop(0)\n', ""),
    ("joinpath chains dropped", 'if isinstance(n.func, ast.Attribute) and n.func.attr == "joinpath":', "if False:"),
    ("os.path.join chains dropped", 'if d.endswith(("path.join", "Path", "PurePath", "PosixPath")) or d in ("join", "Path"):', 'if d.endswith(("Path",)):'),
    ("`from X import submodule` names dropped", '                    names += ["%s.%s" % (n.module, a.name) for a in n.names]\n', "                    pass\n"),
    ("package __init__ not imported", '"tools/%s/__init__.py" % "/".join(parts[:i])):', '"tools/%s/__init___.py" % "/".join(parts[:i])):'),
    ("annotated assignment taint dropped", "elif isinstance(n, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign))", "elif isinstance(n, (ast.NamedExpr, ast.AugAssign))"),
    ("getcwd taint dropped", '(isinstance(x, ast.Call) and _dotted(x.func).endswith(("getcwd", "cwd")))', "False"),
    ("named tool's imports not transitive", "                stack += list(info(tree, m).mods)\n", "                pass\n"),
    ("named tool's imported-module tokens dropped", "                deps |= info(tree, m).deps | info(tree, m).mods\n", "                deps |= info(tree, m).mods\n"),
    ("changed names not added to the Tree", "| set(changed), repo)", ", repo)"),
    ("`tools.` prefix not stripped (F2)", '        if parts[0] == "tools":', "        if False:"),
    ("relative imports not resolved", "if isinstance(n, ast.ImportFrom) and n.level:", "if False:"),
    ("shell scripts not followed (F3)", "    for _lvl in range(SHELL_DEPTH):", "    for _lvl in range(0):"),
    ("shell depth 1", "SHELL_DEPTH = 3", "SHELL_DEPTH = 1"),
    ("shell depth unbounded", "SHELL_DEPTH = 3", "SHELL_DEPTH = 99"),
    ("shell comments followed", '            src = "\\n".join(re.sub(r"(^|\\s)#.*$", "", l) for l in src.splitlines())', "            pass"),
    ("shell command words are not calls", "                    if ws:\n", "                    if False:\n"),
    ("every shell word is a call", 'words = [t for t in TOKEN.findall(src) if "/" in t or t.endswith((".sh", ".py"))]', "words = TOKEN.findall(src)"),
    ("shebang scripts not recognised", 'ok = rel.endswith(".sh") or re.match(', 'ok = rel.endswith(".sh") or False and re.match('),
    ("shell text tokens dropped", "            deps |= toks\n            named", "            named"),
    ("python tools a script calls dropped", "            named |= {g for g in called if tree.is_py(g)}\n", "            pass\n"),
    ("calls of the test and its imports dropped", "        calls |= pi.calls\n", "        pass\n"),
    ("deleted tests not dropped (F4)", 'return [t for t in _listed_tests(tree) if (tree.repo / "tools" / t).is_file()]', "return _listed_tests(tree)"),
    ("dropped-test line not printed", 'lines = ["gate: unit_select: dropped deleted test %s" % t for t in _listed_tests(tree) if t not in tests]', "lines = []"),
    # review gate-cold-s4-r2 N1 / N2 / N3 / N4
    ("parameter defaults not assignments (N1)", "zip(pos[len(pos) - len(a.defaults):], a.defaults)", "zip([], [])"),
    ("keyword-only defaults not assignments (N1)", "zip(a.kwonlyargs, a.kw_defaults)", "zip([], [])"),
    ("positional call arguments not assigned to the parameters (N1)", "zip((a.posonlyargs + a.args)[1 if meth else 0:], n.args)", "zip([], [])"),
    ("keyword call arguments not assigned to the parameters (N1)", "for k in n.keywords:\n                    if k.arg:", "for k in []:\n                    if k.arg:"),
    ("self.method(...) calls not matched (N1)", "(n.func.attr if meth else None)", "None"),
    ("self not skipped in a method call (N1)", "[1 if meth else 0:]", "[0:]"),
    ("test_tools_sync* not always-run (N2)", '"test_tools_sync*", ', ""),
    ("built file names not followed (N3)", "        if suffixes:", "        if False:"),
    ("`name + \".py\"` not a built name (N3)", "isinstance(n.op, ast.Add) and isinstance(n.right, ast.Constant)", "isinstance(n.op, ast.Sub) and isinstance(n.right, ast.Constant)"),
    ("f-string file name not a built name (N3)", "elif isinstance(n, ast.JoinedStr) and len(n.values) > 1", "elif False and len(n.values) > 1"),
    ("`%s.py` file name not a built name (N3)", 'and "%s" in n.left.value:', "and False:"),
    ("`{}.py`.format file name not a built name (N3)", 'n.func.attr == "format" and', 'n.func.attr == "format_" and'),
    ("copytree destination counted as a bound (N4)", 'args = n.args[:1] + [k.value for k in n.keywords if k.arg == "src"]', "pass"),
    ("`./` prefix not stripped (N4)", '            if t.startswith("./"):\n                t = t[2:]\n', ""),
    ("shell separators dropped (N4)", r"[;&|()`{}]|\$\(|", ""),
]
for _w in ("glob", "rglob", "iglob", "iterdir", "listdir", "scandir", "walk", "copytree"):      # N4: each directory-listing word is a switch
    _all = ["glob", "rglob", "iglob", "iterdir", "listdir", "scandir", "walk", "copytree"]
    MUTANTS.append(("%s dropped from the listing words (N4)" % _w, 'LISTING = {%s}' % ", ".join('"%s"' % x for x in _all), 'LISTING = {%s}' % ", ".join('"%s"' % x for x in _all if x != _w)))
for _c in ";&|(`{)":      # N4: each separator character is a switch
    MUTANTS.append(("shell separator %r dropped (N4)" % _c, "[;&|()`{}]", "[%s]" % "".join(x for x in ";&|()`{}" if x != _c)))
for _w in ("then", "do", "else", "elif", "if", "while", "until", "exec", "xargs", "sudo", "env", "nice", "time", "command"):    # N4: each wrapper/keyword word is a switch
    MUTANTS.append(("shell word %r not split (N4)" % _w, r"then|do|else|elif|if|while|until|exec|xargs|sudo|env|nice|time|command", "|".join(x for x in "then do else elif if while until exec xargs sudo env nice time command".split() if x != _w)))
for _w in ("!", "bash", "sh", "source", ".", "python3", "python"):    # N4: each skipped command-prefix word is a switch
    MUTANTS.append(("shell prefix word %r kept as the command (N4)" % _w, '("!", "bash", "sh", "source", ".", "python3", "python", "-x", "-e", "-eu")', '(%s "-x", "-e", "-eu")' % "".join('"%s", ' % x for x in ("!", "bash", "sh", "source", ".", "python3", "python") if x != _w)))
MUTANTS += [
    ("option words kept as the command (N4)", 'and not w.startswith("-") and', "and"),
    ("env-assign prefix kept as the command (N4)", ' and "=" not in w.split("/")[0]]', "]"),
]


class UnitSelectFixture(unittest.TestCase):
    def test_fixture_selection(self):
        self.assertEqual(fixture_failures(us), [])

    def test_unresolved_fixture_is_always_run_with_the_exact_line(self):
        with tempfile.TemporaryDirectory() as d:
            files = make_fixture(d)
            run, lines, _t = us.select_tests(["docs/README.md"], files, d)
            for t in ("test_zeta.py", "test_gamma.py", "test_eta.py"):
                self.assertIn(t, run)
                self.assertIn("gate: unit test %s: dependencies unresolved, always run" % t, lines)

    def test_every_mutant_is_killed(self):
        survived = []
        for name, old, new in MUTANTS:
            if not fixture_failures(load_mutant(old, new)):
                survived.append(name)
        print("\nunit_select mutants killed: %d of %d" % (len(MUTANTS) - len(survived), len(MUTANTS)))
        self.assertEqual(survived, [])

    def test_at_least_8_mutants(self):
        self.assertGreaterEqual(len(MUTANTS), 8)


class UnitSelectCli(unittest.TestCase):
    """tools/gate_select.py STAMP in a throwaway git repo (never the real shim/out/gate: a running gate owns unit-only.txt there)."""
    def run_select(self, edit, delete=(), base_extra=None):
        with tempfile.TemporaryDirectory() as d:
            r = pathlib.Path(d)
            (r / "tools").mkdir()
            for n in ("gate_select.py", "unit_select.py", "gate-manifest.json", "check-all.sh"):
                shutil.copy(HERE / n, r / "tools" / n)
            (r / "tools" / "test_a.py").write_text("import subprocess\nsubprocess.run(['tools/atool'])\n")
            (r / "tools" / "test_b.py").write_text("X = 1\n")
            (r / "tools" / "atool").write_text("#!/bin/bash\n")
            (r / ".gitignore").write_text("__pycache__/\nshim/out/\n")
            for name, text in (base_extra or {}).items():
                (r / name).write_text(text)
            git = lambda *a: subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True)
            git("init", "-q"); git("add", "-A"); git("commit", "-q", "-m", "base")
            stamp = pathlib.Path(d + ".stamp")      # outside the repo: an untracked file in it would be a changed, unaccounted input
            stamp.write_text(git("rev-parse", "HEAD").stdout)
            for name, text in edit.items():
                (r / name).write_text(text)
            for name in delete:
                git("rm", "-q", name)                        # staged deletion: in `git diff` against the base, absent from ls-files
            env = dict(os.environ, GATE_PASS_DIR=str(r / "none"))
            p = subprocess.run([sys.executable, str(r / "tools" / "gate_select.py"), str(stamp)], capture_output=True, text=True, env=env, cwd=d)
            only = r / "shim" / "out" / "gate" / "unit-only.txt"
            res = (p, only.read_text().split() if only.exists() else None)
        stamp.unlink()
        return res

    def test_unit_only_file_and_loud_lines(self):
        p, only = self.run_select({"tools/atool": "#!/bin/bash\necho changed\n"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("unit", p.stdout.split())
        self.assertEqual(only, ["test_a.py"])                       # test_b.py does not depend on atool
        self.assertIn("gate: skipped unit test test_b.py: no input changed", p.stderr)
        self.assertIn("gate: unit shards: ran 1 of 2 test files", p.stderr)

    def test_deleted_or_renamed_test_is_not_listed_and_is_reported(self):
        """F4: a deleted/renamed test file is in the changed list; it must not reach unit-only.txt (gate-unit-shards would exit 2) and the drop is loud."""
        p, only = self.run_select({"tools/atool": "#!/bin/bash\necho changed\n"}, delete=("tools/test_b.py",), base_extra={"tools/test_c.py": "X = 2\n"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(only, ["test_a.py"])                       # test_c.py is unrelated, test_b.py is gone
        self.assertIn("gate: unit_select: dropped deleted test test_b.py", p.stderr)
        self.assertIn("gate: unit shards: ran 1 of 2 test files", p.stderr)
        p, only = self.run_select({}, delete=("tools/test_b.py",), base_extra={"tools/test_c.py": "X = 2\n"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("gate: unit_select: dropped deleted test test_b.py", p.stderr)
        self.assertNotIn("test_b.py", only or [])

    def test_cross_cutting_change_writes_no_file(self):
        p, only = self.run_select({"tools/check-all.sh": (HERE / "check-all.sh").read_text() + "\n# changed\n"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(only)                                      # absent = all test files
        self.assertIn("gate: unit: ALL, cross-cutting input tools/check-all.sh", p.stderr)

    def test_check_all_hands_the_file_to_the_shards_only_in_a_quick_run(self):
        """The wiring no stub covers: the export is conditioned on $quick, uses the path gate_select writes, and the formal gate unsets any inherited value first."""
        ca = (HERE / "check-all.sh").read_text()
        self.assertIn('unset GATE_UNIT_ONLY; [ -n "$quick" ] && [ -s shim/out/gate/unit-only.txt ] && export GATE_UNIT_ONLY=shim/out/gate/unit-only.txt', ca)
        self.assertIn('"shim" / "out" / "gate" / "unit-only.txt"', (HERE / "gate_select.py").read_text())
        self.assertIn('GATE_UNIT_ONLY', (HERE / "gate-unit-shards").read_text())
        # F1/F7/F8: only the quick run passes --selected (so gate-unit-shards honours the variable), and only the quick run tags the GATE line
        self.assertIn('step B unit         tools/gate-unit-shards ${GATE_UNIT_ONLY:+--selected}', ca)
        self.assertIn('[ -n "$quick" ] && [ -n "$x" ] && tag="$tag unit-partial=${x#*=}"', ca)
        self.assertNotIn("GATE_UNIT_ONLY", (HERE / "remote-job").read_text())   # remote-job's unit job is a full run: it passes no flag

    def test_docs_only_selects_no_unit_step_and_no_file(self):
        p, only = self.run_select({"docs.md": "x\n"})
        self.assertNotIn("unit", p.stdout.split())
        self.assertIsNone(only)


class UnitSelectRealTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = us.git_files()
        cls.fileset = set(cls.files)
        cls.tree = us.Tree(cls.files)
        cls.tests = us.all_tests(cls.tree)
        cls.base = set(us.select_tests([], tree=cls.tree)[0])

    def sel(self, *changed):
        return set(us.select_tests(list(changed), tree=self.tree)[0])

    def is_script(self, f):
        if f not in self.fileset or f.endswith(".py"):
            return False
        try:
            with open(HERE.parent / f, "rb") as fh:
                return f.endswith(".sh") or re.match(rb"#!.*\b(ba|da|z|k)?sh\b", fh.readline(100)) is not None
        except OSError:   # lint-silent: expected a deleted file is not a shell script: False (mirrors tools/unit_select.py)
            return False

    @staticmethod
    def words(text, comments=True):
        toks = set()
        if not comments:
            text = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in text.splitlines())
        for w in re.findall(r"[A-Za-z0-9_./-]+", text):
            w = w[2:] if w.startswith("./") else w
            toks |= {w, w.rstrip(".")}
        return toks

    def files_of(self, toks):
        """Tracked files a word set names (computed here, without the selector's tokenizer): a maximal path-like word that is a tracked path, or a tools/ file's basename."""
        out = {w for w in toks if w in self.fileset and len(w) > 5}
        out |= {"tools/" + w for w in toks if "/" not in w and len(w) > 8 and "tools/" + w in self.fileset}
        return out

    def scripts_reached(self, test, depth=3):
        """Shell scripts a test names by a path or a .sh word (not by a bare word), then the scripts those name the same way: `depth` levels (the selector follows 3)."""
        callable_ = lambda toks: {w for w in toks if "/" in w or w.endswith(".sh")}
        level = {f for f in self.files_of(callable_(self.words((HERE / test).read_text()))) if self.is_script(f)}
        seen = set()
        for _ in range(depth):
            nxt = set()
            for f in level - seen:
                seen.add(f)
                nxt |= {g for g in self.files_of(callable_(self.words((HERE.parent / f).read_text(errors="replace"), comments=False))) if self.is_script(g)}
            level = nxt
        return seen

    def named_files(self, test):
        """Files a test names, directly or through the shell scripts it calls (review gate-cold-s4-r1 F6: the first version read the .py text only, so wrapped shell tests were invisible)."""
        out = self.files_of(self.words((HERE / test).read_text()))
        for sc in self.scripts_reached(test):
            out |= self.files_of(self.words((HERE.parent / sc).read_text(errors="replace")))
        return out - {"tools/" + test}

    def test_every_dependency_of_every_test_selects_it(self):
        n = nsh = 0
        for t in self.tests:
            self.assertIn(t, self.sel("tools/" + t), "a changed test must select itself")
            for f in sorted(self.named_files(t)):
                n += 1
                self.assertIn(t, self.sel(f), "%s names %s but a change to it does not select the test" % (t, f))
            nsh += len(self.scripts_reached(t))
        self.assertGreater(n, 600)          # direct words and the words of the scripts they call
        self.assertGreater(nsh, 30)         # the shell scripts the property really walked
        print("\nproperty check: %d (test, dependency) pairs over %d test files, %d (test, script) walks" % (n, len(self.tests), nsh))

    def test_review_examples_F2_F3(self):
        """The wrong skips of review gate-cold-s4-r1, as named cases on the real tree."""
        for changed, tests in {
            "tools/fflash-userdata.sh": ("test_fflash_scan.py",), "tools/flash_cache.py": ("test_fflash_scan.py",), "tools/boards.py": ("test_fflash_scan.py",),
            "tools/heavy-job": ("test_heavy_job.py", "test_xnu_src_mirror.py"), "tools/xnu-remote-build": ("test_heavy_job.py", "test_xnu_src_mirror.py"),
            "tools/gate-xnu-build": ("test_xnu_remote_build.py",), "tools/xnub-line.sh": ("test_xnu_remote_build.py",),
            "tools/xnu-local-build": ("test_heavy_job.py", "test_xnu_remote_build.py", "test_xnu_src_mirror.py"),
        }.items():
            self.assertLessEqual(set(tests), self.sel(changed), changed)

    def test_from_tools_import_selects_the_importers(self):
        """F2: boards.py / mkadt.py / adt.py / mkpayload.py are imported as `from tools import X` / `import tools.X` by 9 tests."""
        nine = {"test_mkadt_touch.py", "test_mkadt_identity.py", "test_mkadt_usb.py", "test_mkadt_wlan.py", "test_boards_acpm.py", "test_boards_el2.py", "test_boards_gpuclk.py", "test_pack_wlanfw.py", "test_bootline_layout.py"}
        union = set()
        for mod in ("mkadt", "adt", "boards", "mkpayload"):
            importers = {t for t in self.tests if re.search(r"^\s*from tools import [^\n]*\b%s\b|^\s*import tools\.%s\b|^\s*from tools\.%s import" % (mod, mod, mod), (HERE / t).read_text(), re.M)}
            self.assertTrue(importers, mod)
            self.assertLessEqual(importers, self.sel("tools/%s.py" % mod), mod)
            union |= importers
        self.assertLessEqual(nine, union)
        self.assertLessEqual({"test_mkadt_touch.py", "test_mkadt_identity.py", "test_mkadt_usb.py", "test_mkadt_wlan.py"}, self.sel("tools/mkadt.py"))

    def test_gate_summary_and_heavy_job(self):
        self.assertIn("test_gate_summary.py", self.sel("tools/gate-summary"))
        heavy = {p.name for p in HERE.glob("test_*.py") if "heavy-job" in p.read_text() or any("heavy-job" in (HERE.parent / sc).read_text(errors="replace") for sc in self.scripts_reached(p.name))}   # the shell texts too (F6)
        self.assertTrue(heavy)
        self.assertIn("test_heavy_job.py", heavy)
        self.assertLessEqual(heavy - {"test_gate_unit_shards.py", "test_wlan_wr_mutants.py"}, self.sel("tools/heavy-job"))   # the wlan mutant test is its own gate step (K14 merge: it now reaches heavy-job), never a unit shard

    def test_review_r2_wrong_skips_N1_N2_N3(self):
        """The wrong skips of review gate-cold-s4-r2 as named cases on the real tree."""
        # N1: test_selftests_wired lists the real tools/ dir through `def read_tree(d=TOOLS)`: a new unwired --self-test tool must run it
        self.assertTrue(us.closure(self.tree, "tools/test_selftests_wired.py")[1], "listing through a parameter default is unresolved")
        self.assertIn("test_selftests_wired.py", us.select_tests(["tools/zz-newtool"], tree=us.Tree(self.files + ["tools/zz-newtool"]))[0])
        # N2: test_tools_sync opens every file tools/common-files.txt lists
        shared = [l.strip() for l in (HERE / "common-files.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
        self.assertIn("test_tools_sync.py", self.base)
        for f in shared:
            if f.endswith(".py"):
                self.assertIn("test_tools_sync.py", self.sel(f), f)
        # N3: test_usblog_arp_c loads test_usbroot_c / test_usblog_gap_c by `name + ".py"`
        self.assertLessEqual({"test_usblog_arp_c.py"}, self.sel("tools/test_usbroot_c.py"))
        self.assertLessEqual({"test_usblog_arp_c.py"}, self.sel("tools/test_usblog_gap_c.py"))

    def test_review_r2_N4_real_tree_survivors(self):
        """copytree / shell separators / `./` prefix: the three mutants that changed real selections and survived the 53 fixture mutants."""
        for t in ("test_el2_mutants.py", "test_fflash_userdata.py"):         # copytree of a repo-rooted path: unresolved, always run
            self.assertTrue(us.closure(self.tree, "tools/" + t)[1], t)
        for t in ("test_devoverlay_kernel.py", "test_gate_xnu_build.py"):    # dependencies reached only through a command word after a shell separator
            deps = us.closure(self.tree, "tools/" + t)[0]
            self.assertLessEqual({"tools/gate-request", "tools/gate-times", "tools/gate-summary", "tools/gate_select.py", "tools/wlan_mutant_cache.py"}, deps, t)    # [obs] 2026-10-31: lost without the separator rule
        # `./qrlog/stubs/xnu/...` is a directory token of test_boards_el2 & 13 more tests (the `./` prefix)
        self.assertIn("qrlog/stubs/xnu/machine/limits.h", us.closure(self.tree, "tools/test_boards_el2.py")[0])
        self.assertEqual(us.Tree(["a/b/c.x", "a/b/d.y"]).tokens("./a/b"), {"a/b/c.x", "a/b/d.y"})   # `./a/b` is the directory a/b

    def test_docs_readme_selects_no_test_beyond_the_always_run_ones(self):
        self.assertEqual(self.sel("docs/README.md"), self.base)
        for t in ("test_gate_select.py", "test_gate_unit_shards.py", "test_check_all_stub.py", "test_unit_select.py", "test_audit_memo.py", "test_lint_mutant_needles.py"):
            self.assertIn(t, self.base, t)

    def test_check_all_selects_all(self):
        run, lines, total = us.select_tests(["tools/check-all.sh"], tree=us.Tree(self.files))
        self.assertEqual((len(run), total), (len(self.tests), len(self.tests)))
        self.assertIn("gate: unit: ALL, cross-cutting input tools/check-all.sh", lines)
        self.assertEqual(self.tests, sorted(self.tests))
        self.assertNotIn("test_wlan_wr_mutants.py", self.tests)
        for f in ("tools/gate-manifest.json", "tools/gate-unit-shards", "xnu/patches/0129-sweet-wlan-chip-writes.patch", "soc/gs101/soc.json"):
            self.assertEqual(len(self.sel(f)), len(self.tests), f)

    def test_selection_sizes_of_5_real_one_file_changes(self):
        rows = []
        for f in ("tools/gate-summary", "tools/qrmerge.py", "tools/boards.py", "tools/heavy-job", "tools/audit_memo.py"):
            self.assertIn(f, self.files)
            rows.append("%s: %d of %d" % (f, len(self.sel(f)), len(self.tests)))
            self.assertLess(len(self.sel(f)), len(self.tests), f)
        print("\nselection sizes (one-file changes): " + "; ".join(rows))

    def test_the_real_tests_unresolved_are_a_minority(self):
        un = [t for t in self.tests if us.closure(self.tree, "tools/" + t)[1]]
        print("\nunresolved (always run) test files: %d of %d" % (len(un), len(self.tests)))
        self.assertLess(len(un), len(self.tests) // 4)


if __name__ == "__main__":
    unittest.main()
