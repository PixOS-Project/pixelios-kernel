#!/usr/bin/env python3
"""Per-file unit test selection for `tools/check-all.sh --quick` (PLAN-GATE-2X D2 / S4). Imported by tools/gate_select.py; the formal gate never uses it.
select_tests(changed, files=None) -> (basenames to run, loud lines): a tools/test_*.py file runs when it changed or any file it depends on changed.
CONSERVATIVE (a wrong skip is worse than a slow run). Dependencies of a test, all read from source, nothing imported or executed:
  - every token ([A-Za-z0-9_./-]+) of its source that names a tracked file (full path or basename), a tracked directory (a token with a slash: every file below it),
    or a patch number (NNNN -> xnu/patches/NNNN-*.patch);
  - a path chain (`ROOT / "dir"`, os.path.join, Path(...)) or a path constant that resolves to a tracked directory below the repo root / tools/: every file below it;
  - a name built at run time into a file name (`name + ".py"`, f"{name}.sh", "%s.py" % name, "{}.py".format(name)): every token of the source + that suffix that names a tracked file;
  - the same, transitively, for every python file in the set (local modules it imports (ast, `import x`, `from tools import x`, `import tools.x`, relative imports) and python tools it names);
  - a shell script in the set (a test wrapping tools/test_NAME.sh, a tool it runs) contributes every token of its text, transitively through the shell scripts it names; a python tool such a script
    names is a one-level dependency like any named python tool. Any tools/ file whose basename is a word of a test (or of a script it runs) is a dependency: over-selection is fine, a wrong skip is not.
UNRESOLVED => always run (`gate: unit test FILE: dependencies unresolved, always run`): syntax error, exec/eval, __import__/import_module with a non-literal name, a
directory listing (glob/rglob/iterdir/listdir/scandir/walk/copytree...) of a repo-rooted path that no tracked directory below the repo root/tools bounds, or `ls-files`.
ALWAYS RUN: the gate's own tests (test_gate*, test_check_all*, test_audit_memo*, test_lint_mutant_needles*, test_tools_sync*, test_unit_select*). ALL tests run when a cross-cutting
input changed (tools/check-all.sh, tools/gate-manifest.json, tools/gate-unit-shards, xnu/patches/, soc/, device/).
CLI: unit_select.py FILE...   -> the loud lines and the selection for these changed files (no git needed beyond `git ls-files`)."""
import ast, fnmatch, pathlib, re, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
EXCL = "test_wlan_wr_mutants.py"        # gate-unit-shards never runs it (its own step wlan-mutants)
CROSS_FILES = ("tools/check-all.sh", "tools/gate-manifest.json", "tools/gate-unit-shards")
CROSS_DIRS = ("xnu/patches/", "soc/", "device/")
# test_tools_sync*: it opens every file tools/common-files.txt lists, so the set of files it reads is data, not source (review gate-cold-s4-r2 N2); 1.5 s, cheaper to always run than to model a data-file closure
ALWAYS = ("test_gate*", "test_check_all*", "test_audit_memo*", "test_lint_mutant_needles*", "test_tools_sync*", "test_unit_select*")
TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
LISTING = {"glob", "rglob", "iglob", "iterdir", "listdir", "scandir", "walk", "copytree"}
NAMES_ONLY = ("tools/gate_select.py", "tools/unit_select.py")   # `git ls-files` there lists names (the changed/untracked files), never content
SHELL_DEPTH = 3         # test -> wrapper script -> tool script -> its helper script; the scripts form one strongly connected graph (heavy-job -> gate -> check-all.sh -> every step), unbounded following selects 45-60 of 98 tests for any change
UNBOUNDED = ("", ".", "tools")          # the repo root and the tools dir itself: "every file", not a bound


def git_files(repo=REPO):
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git ls-files failed: " + r.stderr.strip())
    return r.stdout.split()


class Tree:
    """The tracked files (plus the changed ones: deleted and untracked files are still names a test may mention)."""
    def __init__(self, files, repo=REPO):
        self.repo = pathlib.Path(repo)
        self.files = set(files)
        self.by_base, self.dirs, self.patches = {}, {}, {}
        for f in self.files:
            self.by_base.setdefault(f.rsplit("/", 1)[-1], set()).add(f)
            d = f
            while "/" in d:
                d = d.rsplit("/", 1)[0]
                self.dirs.setdefault(d, set()).add(f)
            m = re.match(r"xnu/patches/(\d{4})-", f)
            if m:
                self.patches.setdefault(m.group(1), set()).add(f)
        self._info, self._clo, self._shell, self._shtok, self._is_py = {}, {}, {}, {}, {}

    def read(self, rel):
        try:
            return (self.repo / rel).read_text(errors="replace")
        except OSError:   # lint-silent: expected a file deleted in the work tree has no text: None, callers handle it
            return None

    def is_py(self, rel):
        if rel.endswith(".py"):
            return True
        if rel not in self._is_py:   # memoised per Tree: it was one open() per question, 160k times per selection
            try:
                with open(self.repo / rel, "rb") as fh:
                    self._is_py[rel] = re.match(rb"#!.*python", fh.readline(100)) is not None
            except OSError:   # lint-silent: expected a deleted file is not python: False
                self._is_py[rel] = False
        return self._is_py[rel]

    def is_shell(self, rel):
        """A tracked script (`.sh` or a sh/bash/dash/zsh shebang) that exists on disk: its text is followed (review gate-cold-s4-r1 F3)."""
        if rel not in self._shell:
            ok = False
            if rel in self.files and not rel.endswith(".py"):
                try:
                    with open(self.repo / rel, "rb") as fh:
                        ok = rel.endswith(".sh") or re.match(rb"#!\s*\S*\b(env\s+)?(ba|da|z|k)?sh\b", fh.readline(100)) is not None
                except OSError:   # lint-silent: expected a deleted file is not a shell script: ok stays False
                    ok = False
            self._shell[rel] = ok
        return self._shell[rel]

    def shell_tokens(self, rel):
        """(every file the script's text names, the files it calls)."""
        if rel not in self._shtok:
            src = self.read(rel)
            self._shtok[rel] = (self.tokens(src), self.call_tokens(src, True)) if src is not None else (set(), set())
        return self._shtok[rel]

    def call_tokens(self, src, shell):
        """Files a text CALLS (what may run), not merely mentions: a token with a slash or a .sh/.py extension; in a shell text also the command word of every statement.
        A bare word such as `gate` in an echo or a comment is a dependency (tokens()) but is not followed, else every script is reachable from every other (76 of 98 tests for one file)."""
        if shell:       # comments (whole-line and trailing ` # ...`) name tools without calling them
            src = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in src.splitlines())
        words = [t for t in TOKEN.findall(src) if "/" in t or t.endswith((".sh", ".py"))]
        if shell:
            for line in src.splitlines():
                for seg in re.split(r"\|\||&&|[;&|()`{}]|\$\(|\b(?:then|do|else|elif|if|while|until|exec|xargs|sudo|env|nice|time|command)\b", line):
                    ws = [w for w in seg.split() if w not in ("!", "bash", "sh", "source", ".", "python3", "python", "-x", "-e", "-eu") and not w.startswith("-") and "=" not in w.split("/")[0]]
                    if ws:
                        words.append(ws[0].strip("\"'$\\"))
        out = set()
        for w in words:
            out |= self.tokens(w)
        return out

    def tokens(self, src):
        out = set()
        for t in TOKEN.findall(src):
            if t.startswith("./"):
                t = t[2:]
            t = t.strip("/").rstrip(".")
            if len(t) < 2:
                continue
            base = t.rsplit("/", 1)[-1]
            if "/" in t and t in self.dirs:
                out |= self.dirs[t]
            out |= self.by_base.get(base, set())   # by basename even for a/b/c.x: the path may be relative to another root (../xnu/patches/N.patch, a temp copy)
            m = re.match(r"(\d{4})(-|$)", base)
            if m:
                out |= self.patches.get(m.group(1), set())
        return out

    def dir_files(self, run):
        """(tracked files, bounded) for a literal path run (relative to the repo root, or to tools/). bounded: the run names something below the repo root/tools
        (a gitignored directory such as logs/runs is bounded with no tracked file: its content cannot be in a diff); the repo root and tools/ themselves are not."""
        comps = [c for c in run.split("/") if c not in ("", ".")]
        while comps and comps[0] == "..":
            comps.pop(0)
        run = "/".join(comps)
        if run in UNBOUNDED:
            return set(), False
        out = set()
        for p in (run, "tools/" + run):
            if p in self.files:
                out.add(p)
            out |= self.dirs.get(p, set())
        return out, True


def _key(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
        return node.attr
    return None


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _pathlike(e):
    """An expression that is a path (a name, `a / b`, `a.parent`, `a[1]`, Path(..), os.path.f(..), .resolve()): only these carry a parameter's taint, else `tree.read(rel)` taints every string derived from it."""
    if isinstance(e, ast.Name):
        return True
    if isinstance(e, ast.Attribute):
        return _key(e) is not None or _pathlike(e.value)
    if isinstance(e, ast.Subscript):
        return _pathlike(e.value)
    if isinstance(e, ast.BinOp):
        return isinstance(e.op, ast.Div) and (_pathlike(e.left) or _pathlike(e.right))
    if isinstance(e, ast.Call):
        d = _dotted(e.func)
        if isinstance(e.func, ast.Attribute) and e.func.attr in ("resolve", "absolute", "expanduser", "as_posix", "joinpath"):
            return _pathlike(e.func.value)
        if d in ("str", "os.fspath", "fspath") and e.args:
            return _pathlike(e.args[0])
        return d.endswith(("Path", "PurePath", "PosixPath")) or d.startswith("os.path.") or d == "join"
    return False


def _built_suffix(n):
    """The file-name suffix a run-time name is built into: `x + ".py"`, f"{x}.sh", "%s.py" % x, "{}.py".format(x) -> ".py" / ".sh" (review gate-cold-s4-r2 N3); None otherwise."""
    s = None
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and isinstance(n.right, ast.Constant) and isinstance(n.right.value, str):
        s = n.right.value
    elif isinstance(n, ast.JoinedStr) and len(n.values) > 1 and isinstance(n.values[-1], ast.Constant):
        s = n.values[-1].value
    elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod) and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str) and "%s" in n.left.value:
        s = n.left.value.rsplit("%s", 1)[-1]
    elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format" and isinstance(n.func.value, ast.Constant) and isinstance(n.func.value.value, str) and "{" in n.func.value.value:
        s = n.func.value.value.rsplit("}", 1)[-1]
    return s if s and s.endswith((".py", ".sh")) else None


class PyInfo:
    """One python file, analysed alone: direct token deps, local imports (as tracked files), unresolved reasons."""
    def __init__(self, tree, rel, src):
        self.deps, self.mods, self.unres, self.calls = tree.tokens(src), set(), [], tree.call_tokens(src, False)
        try:
            mod = ast.parse(src)
        except (SyntaxError, ValueError) as e:
            self.unres.append("%s: syntax error (%s)" % (rel, e))
            return
        self.tree, self.rel = tree, rel
        self.assigns, self._flat_memo, self._flat_busy, self._parm = {}, {}, set(), set()     # _parm: ids of the values that are parameter defaults / call arguments
        self._nodes = list(ast.walk(mod))   # one walk, reused below (it was five: ~40% of the analysis time); same nodes, same order
        self._walk_memo = {}
        for n in self._nodes:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if _key(t):
                        self.assigns.setdefault(_key(t), []).append(n.value)
            elif isinstance(n, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)) and n.value is not None and _key(n.target):
                self.assigns.setdefault(_key(n.target), []).append(n.value)
        self._param_assigns(mod)
        self.tainted = self._taint()
        self.parent = {c: p for p in self._nodes for c in ast.iter_child_nodes(p)}
        if "ls-files" in src and rel not in NAMES_ONLY:
            self.unres.append("%s: git ls-files (whole-repo scan)" % rel)
        suffixes = {x for x in map(_built_suffix, self._nodes) if x}
        if suffixes:        # a name built into a file name at run time: every token that names a file with that suffix is a dependency (N3)
            for t in TOKEN.findall(src):
                for x in suffixes:
                    self.deps |= tree.by_base.get(t + x, set())
        for n in self._nodes:
            self._visit(n)

    def _param(self, name, value):
        self.assigns.setdefault(name, []).append(value)
        self._parm.add(id(value))

    def _param_assigns(self, mod):
        """A function parameter takes its default and every argument a call in this file passes (N1: `def read_tree(d=TOOLS)`, `read_tree(TOOLS)`): both are assignments of the parameter name."""
        funcs = {n.name: n.args for n in self._nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for a in funcs.values():
            pos = a.posonlyargs + a.args
            for p, v in zip(pos[len(pos) - len(a.defaults):], a.defaults):
                self._param(p.arg, v)
            for p, v in zip(a.kwonlyargs, a.kw_defaults):
                if v is not None:
                    self._param(p.arg, v)
        for n in self._nodes:
            if not isinstance(n, ast.Call):
                continue
            meth = isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id in ("self", "cls")
            name = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if meth else None)
            if name in funcs:
                a = funcs[name]
                for p, v in zip((a.posonlyargs + a.args)[1 if meth else 0:], n.args):
                    self._param(p.arg, v)
                for k in n.keywords:
                    if k.arg:
                        self._param(k.arg, k.value)

    # ---- taint: names derived from __file__ / the cwd are repo-rooted
    def _expr_tainted(self, e, names):
        return any((isinstance(x, ast.Name) and (x.id == "__file__" or x.id in names)) or
                   (isinstance(x, ast.Attribute) and _key(x) in names) or
                   (isinstance(x, ast.Call) and _dotted(x.func).endswith(("getcwd", "cwd"))) for x in self._walk_of(e))

    def _walk_of(self, e):
        """ast.walk(e) as a list, memoised: the taint fixed point asks for the same expressions again on every round."""
        w = self._walk_memo.get(id(e))
        if w is None:
            w = self._walk_memo[id(e)] = (e, list(ast.walk(e)))   # keeps e alive so its id is not reused
        return w[1]

    def _taint(self):
        names = set()
        for strict in (False, True):    # 1: any expression with a tainted name taints its target (parameter values excluded); 2: a path-shaped expression of a tainted name (parameters included, N1)
            while True:
                new = {k for k, vs in self.assigns.items() if k not in names and any(self._expr_tainted(v, names) for v in vs if (strict and _pathlike(v)) or (not strict and id(v) not in self._parm))}
                if not new:
                    break
                names |= new
        return names

    # ---- literal path runs
    def flat(self, n, depth=0):
        """Path pieces: str for a literal, None for anything unknown (a break)."""
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return [n.value]
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            return self.flat(n.left, depth) + self.flat(n.right, depth)
        if isinstance(n, ast.Call):
            d = _dotted(n.func)
            if isinstance(n.func, ast.Attribute) and n.func.attr == "joinpath":
                return self.flat(n.func.value, depth) + [x for a in n.args for x in self.flat(a, depth)]
            if d.endswith(("path.join", "Path", "PurePath", "PosixPath")) or d in ("join", "Path"):
                return [x for a in n.args for x in self.flat(a, depth)]
            if isinstance(n.func, ast.Attribute) and n.func.attr in ("resolve", "absolute", "expanduser", "as_posix"):
                return self.flat(n.func.value, depth)
            return [None]
        if isinstance(n, ast.Attribute) and n.attr in ("parent", "parents"):
            return self.flat(n.value, depth) + [None]
        k = _key(n)
        if k and depth < 8 and k not in self._flat_busy:        # several assignments (a fallback branch): the first one that yields a literal run
            if k not in self._flat_memo:                         # memoised per name: parameters add one assignment per call site, unmemoised the walk is exponential
                self._flat_busy.add(k)
                res = [None]
                for v in self.assigns.get(k, []):
                    f = self.flat(v, depth + 1)
                    if self.runs(f):
                        res = f
                        break
                self._flat_busy.discard(k)
                self._flat_memo[k] = res
            return self._flat_memo[k]
        return [None]

    @staticmethod
    def runs(pieces):
        out, cur = [], []
        for p in pieces + [None]:
            if p is None:
                if cur:
                    out.append("/".join(x.strip("/") for x in cur if x.strip("/")))
                cur = []
            else:
                cur.append(p)
        return [r for r in out if r]

    def _bound(self, run):
        comps = []
        for c in run.split("/"):
            if re.search(r"[*?\[]", c):
                break
            comps.append(c)
        return "/".join(comps)

    # ---- the visitor
    def _visit(self, n):
        t = self.tree
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            if isinstance(n, ast.ImportFrom) and n.level:
                self._relative(n)
            else:
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else ([n.module] if n.module else [])
                if isinstance(n, ast.ImportFrom) and n.module:
                    names += ["%s.%s" % (n.module, a.name) for a in n.names]
                for m in names:
                    self._import(m)
        elif isinstance(n, ast.Call):
            d = _dotted(n.func)
            last = d.rsplit(".", 1)[-1]
            if isinstance(n.func, ast.Name) and n.func.id in ("exec", "eval"):
                self.unres.append("%s:%d: %s()" % (self.rel, n.lineno, n.func.id))
            elif d in ("__import__", "importlib.import_module", "import_module"):
                a = n.args[0] if n.args else None
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    self._import(a.value)
                else:
                    self.unres.append("%s:%d: %s with a non-literal name" % (self.rel, n.lineno, d))
            elif last in LISTING and self._expr_tainted(n, self.tainted):
                self._listing(n)
        if isinstance(n, (ast.BinOp, ast.Call, ast.Name, ast.Attribute)):
            self._chain(n)

    def _import(self, m):
        parts = m.split(".")
        if parts[0] == "tools":           # `from tools import x` / `import tools.x`: tools/ is the package (review gate-cold-s4-r1 F2)
            parts = parts[1:]
        for i in range(len(parts), 0, -1):
            for p in ("tools/%s.py" % "/".join(parts[:i]), "tools/%s/__init__.py" % "/".join(parts[:i])):
                if p in self.tree.files:
                    self.mods.add(p)
                    return

    def _relative(self, n):
        """`from . import x`, `from .pkg import y`: resolved against the importing file's directory."""
        base = self.rel.split("/")[:-1]
        base = base[:len(base) - (n.level - 1)] if n.level > 1 else base
        mod = (n.module or "").split(".") if n.module else []
        for tail in [mod] + [mod + [a.name] for a in n.names]:
            d = "/".join(base + tail)
            for p in (d + ".py", d + "/__init__.py"):
                if p in self.tree.files:
                    self.mods.add(p)

    def _listing(self, n):
        pieces = []
        if isinstance(n.func, ast.Attribute):
            pieces += self.flat(n.func.value)
        args = list(n.args) + [k.value for k in n.keywords]
        if _dotted(n.func).rsplit(".", 1)[-1] == "copytree":      # only the SOURCE is listed: a literal destination (`copytree(ROOT, "/tmp/x")`) is no bound
            args = n.args[:1] + [k.value for k in n.keywords if k.arg == "src"]
        for a in args:
            pieces += [None] + self.flat(a)
        bounded = False
        for r in self.runs(pieces):
            files, b = self.tree.dir_files(self._bound(r))
            self.deps |= files
            bounded = bounded or b
        if not bounded:
            self.unres.append("%s:%d: %s of a repo-rooted path no tracked directory bounds" % (self.rel, n.lineno, _dotted(n.func)))

    def _pathy(self, n):
        return isinstance(n, ast.Call) and (_dotted(n.func).endswith(("path.join", "Path", "PurePath")) or (isinstance(n.func, ast.Attribute) and n.func.attr == "joinpath"))

    def _chain(self, n):
        """Maximal path chains / path constants resolving to a tracked directory: every file below (a definition `X = ROOT / "dir"` itself is not a use, nor is a part of a longer chain)."""
        if not ((isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)) or self._pathy(n) or (_key(n) in self.assigns and isinstance(getattr(n, "ctx", None), ast.Load))):
            return
        p = self.parent.get(n)
        if (isinstance(p, ast.BinOp) and isinstance(p.op, ast.Div)) or (self._pathy(p) and n in p.args) or isinstance(p, ast.Attribute) and p.value is n and isinstance(n, (ast.Name, ast.Attribute)):
            return
        if isinstance(p, (ast.Assign, ast.AnnAssign)) and p.value is n:
            return
        for r in self.runs(self.flat(n)):
            self.deps |= self.tree.dir_files(self._bound(r))[0]


def info(tree, rel):
    if rel not in tree._info:
        src = tree.read(rel)
        tree._info[rel] = PyInfo(tree, rel, src) if src is not None else None
    return tree._info[rel]


def closure(tree, test):
    """(dependency files, unresolved reasons) of tools/<test>. Analysed in full (tokens, path chains, unresolved reasons): the test and the local modules it imports, transitively.
    A python tool it only names (runs) adds itself, its tokens and its imports as dependencies, one level, without its unresolved reasons: agent-guard's whole-repo walk must not make every test that runs it an always-run test."""
    deps, unres, seen, todo, named, calls = {test}, [], set(), [test], set(), set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        pi = info(tree, rel)
        if pi is None:
            continue
        unres += pi.unres
        deps |= pi.deps | pi.mods
        todo += list(pi.mods)
        named |= {f for f in pi.deps if tree.is_py(f)}
        calls |= pi.calls
    shells, level = set(), {f for f in calls if tree.is_shell(f)}   # scripts the test (or a module it imports) calls: wrapped shell tests, tools it runs; their text, SHELL_DEPTH levels deep
    for _lvl in range(SHELL_DEPTH):
        nxt = set()
        for f in level - shells:
            shells.add(f)
            toks, called = tree.shell_tokens(f)
            deps |= toks
            named |= {g for g in called if tree.is_py(g)}
            nxt |= {g for g in called if tree.is_shell(g)}
        level = nxt
    for rel in named - seen:
        pi = info(tree, rel)
        if pi is not None:
            deps |= pi.deps | pi.mods
            mods, stack = set(), list(pi.mods)
            while stack:                      # its imports, transitively (tokens of imported modules count)
                m = stack.pop()
                if m in mods or info(tree, m) is None:
                    continue
                mods.add(m)
                deps |= info(tree, m).deps | info(tree, m).mods
                stack += list(info(tree, m).mods)
    return deps, unres


def all_tests(tree):
    """The test files that exist on disk (a deleted or renamed test is in the changed list, hence in the Tree, but is no test any more: review gate-cold-s4-r1 F4)."""
    return [t for t in _listed_tests(tree) if (tree.repo / "tools" / t).is_file()]


def _listed_tests(tree):
    return sorted(f.rsplit("/", 1)[-1] for f in tree.files if re.fullmatch(r"tools/test_[^/]*\.py", f) and f.rsplit("/", 1)[-1] != EXCL)


def select_tests(changed, files=None, repo=REPO, tree=None):
    """(selected test basenames, loud lines, total). `files`: the tracked file list (default `git ls-files`); changed files are added to it.
    `tree`: a prebuilt Tree (the self-check reuses one for many selections; it must already hold the changed names)."""
    changed = [c for c in changed if c]
    tree = tree or Tree(set(files if files is not None else git_files(repo)) | set(changed), repo)
    tests = all_tests(tree)
    lines = ["gate: unit_select: dropped deleted test %s" % t for t in _listed_tests(tree) if t not in tests]
    cross = next((c for c in changed if c in CROSS_FILES or c.startswith(CROSS_DIRS)), None)
    if cross:
        return tests, lines + ["gate: unit: ALL, cross-cutting input %s" % cross, "gate: unit shards: ran %d of %d test files" % (len(tests), len(tests))], len(tests)
    ch, run = set(changed), []
    for t in tests:
        deps, unres = tree._clo.get(t) or tree._clo.setdefault(t, closure(tree, "tools/" + t))
        if unres:
            lines.append("gate: unit test %s: dependencies unresolved, always run" % t)
            lines += ["gate: unit unresolved: " + u for u in dict.fromkeys(unres)]
            run.append(t)
        elif any(fnmatch.fnmatchcase(t, g) for g in ALWAYS):
            run.append(t)
        elif deps & ch:
            run.append(t)
        else:
            lines.append("gate: skipped unit test %s: no input changed" % t)
    lines.append("gate: unit shards: ran %d of %d test files" % (len(run), len(tests)))
    return run, lines, len(tests)


if __name__ == "__main__":
    run, lines, total = select_tests(sys.argv[1:])
    print("\n".join(lines))
    print("RESULT: PASS unit_select ran=%d of %d" % (len(run), total))
