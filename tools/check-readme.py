#!/usr/bin/env python3
"""README gate (design: super docs/research/readme-gate.md section 3). Flags drift; cannot prove a claim true.

Rules: link (relative links/src resolve), path (backticked paths exist), cmd (sh fences parse, tools are
executable in the index; nothing is run), works (undated "works" claim), leak (private patterns; both skipped
with --private), known (suppressed by tools/check-readme.known), stale (a .known entry that no longer matches).
Usage: tools/check-readme.py [--private] [ROOT]      (default ROOT = cwd; must be a git repo)
       tools/check-readme.py --self-test
Report: `file:line rule message` per finding, last line `RESULT: PASS|FAIL|ERROR ...`; exit 0/1/2.
A README whose first 3 lines contain `<!-- readme-gate: archive -->` is skipped.
Known file: ROOT/tools/check-readme.known, one TAB-separated entry per line: file, rule, message, date, reason.
"""
import fnmatch, os, re, subprocess, sys, tempfile

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Copied from pixelios-site tools/check-site.py:14-19, plus the extras of the gate design (section 3 check 5).
LEAKS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"), "private key"),
    (re.compile(r"/home/[a-z][\w-]*/"), "local path"),
    (re.compile(r"(?<!\d)\d{15}(?!\d)"), "15-digit number (IMEI?)"),
    (re.compile(r"(?i)\bserial(?:no| number)?\s*[:=]\s*[A-Z0-9]{8,}\b"), "device serial"),
    (re.compile(r"pixelios-artifacts"), "private artifacts name"),
    (re.compile(r"(?<![\w.+/-])(?!git@)[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b(?![\w/-])"), "e-mail address"),
    (re.compile(r"\.env\b"), ".env file"),
]
IMGEXT = {"png", "jpg", "jpeg", "gif", "svg", "webp"}  # logo@2x.png is an image name, not an address
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
REFLINK = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
HTMLREF = re.compile(r"""(?:src|href)=["']([^"']+)["']""")
TICK = re.compile(r"`([^`\n]+)`")
PATHTOK = re.compile(r"^[\w.-]+(/[\w.*-]+)+/?$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\w*)")
LISTITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)")
HEADING = re.compile(r"^#{1,6}\s+(.*)")
SEP = re.compile(r"&&|\|\||;|\|")
PREFIX = ("sudo", "env", "python3", "python", "bash", "sh")
RULES = ("link", "path", "cmd", "works", "leak", "known", "stale")


def index(root):
    r = subprocess.run(["git", "-C", root, "ls-files", "-s"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = {}
    for ln in r.stdout.splitlines():
        meta, _, path = ln.partition("\t")
        out[path] = meta.split()[0]
    return out


def present(idx, dirs, p):
    """A repo-relative path is present if tracked (file, or directory prefix of a tracked file); globs match the index."""
    p = os.path.normpath(p)
    if p == ".":
        return True
    if p.startswith(".."):
        return False
    if any(c in p for c in "*?["):
        return bool(fnmatch.filter(idx, p) or fnmatch.filter(dirs, p))
    return p in idx or p in dirs


def generated(root):
    g = set()
    try:
        for ln in open(os.path.join(root, "upstream-files.txt")):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                g.add(ln)
    except OSError:
        pass
    d = os.path.join(root, "upstream-patches")
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if n.endswith(".patch") and "__" in n:
            g.add(n[:-6].replace("__", "/"))
    return g


def split(lines):
    """Yield (lineno, text, in_fence, fence_lang) per line."""
    fence, lang = None, ""
    for i, ln in enumerate(lines, 1):
        m = FENCE.match(ln)
        if fence is None and m:
            fence, lang = m.group(1)[0] * 3, m.group(2).lower()
            yield i, ln, True, lang
        elif fence is not None:
            if ln.strip().startswith(fence):
                fence = None
            yield i, ln, True, lang
        else:
            yield i, ln, False, ""


def units(lines):
    """Prose units for the works rule: (lineno, text, is_list_item, under_works_heading)."""
    cur, hw = None, False
    for i, ln, fenced, _ in split(lines):
        if fenced or not ln.strip():
            if cur:
                yield cur
            cur = None
            continue
        h, li = HEADING.match(ln), LISTITEM.match(ln)
        if h or li:
            if cur:
                yield cur
            cur = None
        if h:
            hw = bool(re.search(r"works", h.group(1), re.I))
        elif li:
            cur = [i, li.group(1), True, hw]
        elif cur:
            cur[1] += " " + ln.strip()
        else:
            cur = [i, ln.strip(), False, hw]
    if cur:
        yield cur


def check_file(root, rel, idx, gen, private, off):
    out = []
    lines = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read().split("\n")
    if any("<!-- readme-gate: archive -->" in x for x in lines[:3]):
        return out
    here = os.path.dirname(rel)
    dirs = {k.rsplit("/", 1)[0] for k in idx if "/" in k}
    dirs |= {d.rsplit("/", 1)[0] for d in list(dirs) if "/" in d}
    for d in list(dirs):
        while "/" in d:
            d = d.rsplit("/", 1)[0]
            dirs.add(d)
    add = lambda n, rule, msg: rule in off or out.append((rel, n, rule, msg))
    shl = []
    for n, ln, fenced, lang in split(lines):
        if fenced:
            if lang in ("sh", "bash", "shell") and not ln.strip().startswith(("```", "~~~")):
                shl.append((n, ln))
            continue
        for t in LINK.findall(ln) + HTMLREF.findall(ln) + REFLINK.findall(ln):
            if t.startswith("#") or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", t):
                continue
            p = re.split(r"[#?]", t)[0]
            if p and not present(idx, dirs, p.lstrip("/") if p.startswith("/") else os.path.join(here, p)):
                add(n, "link", "broken relative link: " + t)
        for t in TICK.findall(ln):
            if not PATHTOK.match(t) or t in gen or "/out/" in t:
                continue
            if not present(idx, dirs, t):
                add(n, "path", "backticked path does not exist: " + t)
    # fenced sh blocks: contiguous runs of fenced sh lines (gaps in line numbers split them)
    runs, last = [], None
    for n, ln in shl:
        if last is None or n != last + 1:
            runs.append([])
        runs[-1].append((n, ln))
        last = n
    for run in runs:
        r = subprocess.run(["bash", "-n"], input="\n".join(x for _, x in run) + "\n", capture_output=True, text=True)
        if r.returncode != 0:
            add(run[0][0], "cmd", "shell fence does not parse: " + (r.stderr.strip().splitlines() or ["?"])[0].split(": ", 1)[-1])
        for n, ln in run:
            s = ln.strip().lstrip("$ ").strip()
            cmdwords, cwd = set(), ""
            for seg in SEP.split(s.split(" #")[0] if not s.startswith("#") else ""):
                ws = seg.split()
                interp = False
                while ws and (re.match(r"^\w+=", ws[0]) or ws[0] in PREFIX):
                    interp = interp or ws[0] in ("python3", "python", "bash", "sh")
                    ws = ws[1:]
                w = ws[0] if ws else ""
                if w == "cd" and len(ws) > 1:
                    cwd = ws[1].strip("'\"")
                    continue
                if interp or "/" not in w or w.startswith(("/", "~")) or "=" in w:
                    continue
                w = w[2:] if w.startswith("./") else w
                if w in gen or "/out/" in w or w.startswith("-"):
                    continue
                cmdwords.add(w)
                if cwd and "$" not in cwd and not cwd.startswith(("/", "~", "-")):
                    w = os.path.normpath(os.path.join(cwd, w))
                cmdwords.add(w)
                if idx.get(w) != "100755":
                    add(n, "cmd", "fenced command is not an executable tracked file: " + w)
            for t in ln.split():  # K2: every word, comments included, must name an existing path
                if "$" in t:
                    continue
                t = t.strip("()[]{}<>,.;:'\"`$")
                t = t[2:] if t.startswith("./") else t
                if t in cmdwords or t in gen or "/out/" in t or not PATHTOK.match(t):
                    continue
                if not present(idx, dirs, t) and not (cwd and present(idx, dirs, os.path.normpath(os.path.join(cwd, t)))):
                    add(n, "path", "fenced path does not exist: " + t)
    if not private:
        for n, text, li, hw in units(lines):
            if (re.search(r"\bworks\b", text, re.I) or (li and hw)) and not (DATE.search(text) or re.search("observed", text, re.I)):
                add(n, "works", 'claim without date or "observed": ' + text[:50])
        for n, ln, fenced, _ in split(lines):
            for rx, what in LEAKS:
                for m in rx.finditer(ln):
                    if what == "e-mail address" and m.group(0).rsplit(".", 1)[-1].lower() in IMGEXT:
                        continue
                    add(n, "leak", what + ": " + m.group(0)[:40])
    return out


def known_entries(root):
    path = os.path.join(root, "tools", "check-readme.known")
    ents = []
    try:
        for n, ln in enumerate(open(path), 1):
            if ln.strip() and not ln.startswith("#"):
                f = ln.rstrip("\n").split("\t")
                ents.append((n, f) if len(f) == 5 and all(f) and DATE.fullmatch(f[3]) else (n, None))
    except OSError:
        pass
    return ents


def run(root, private=False, off=frozenset()):
    """Return (findings, nfiles, nknown) or None if root is not a git repo."""
    idx = index(root)
    if idx is None:
        return None
    gen = generated(root)
    files = sorted(p for p in idx if os.path.basename(p).startswith("README") and p.endswith(".md"))
    found = []
    for f in files:
        found += check_file(root, f, idx, gen, private, off)
    ents, used, nknown, res = known_entries(root), set(), 0, []
    keys = {}
    for n, f in ents:
        if f and (f[1] != "leak" or f[2].startswith("e-mail address: ")):
            keys.setdefault((f[0], f[1], f[2]), []).append(n)
    for fnd in found:
        k = (fnd[0], fnd[2], fnd[3])
        if "known" not in off and keys.get(k):  # an entry consumes exactly one finding
            used.add(keys[k].pop(0))
            nknown += 1
        else:
            res.append(fnd)
    if "stale" not in off:
        for n, f in ents:
            if f is None:
                res.append(("tools/check-readme.known", n, "stale", "malformed entry (need file, rule, message, date, reason)"))
            elif f[1] == "leak" and not f[2].startswith("e-mail address: "):
                res.append(("tools/check-readme.known", n, "stale", "only e-mail address leaks can be allowlisted (exact text), rule 4"))
            elif n not in used:
                res.append(("tools/check-readme.known", n, "stale", "entry no longer matches a finding: %s %s" % (f[0], f[2])))
    return res, len(files), nknown


# ---------------------------------------------------------------- self-test
def _mk(base, name, files, execs=(), late=None):
    d = os.path.join(base, name)
    for p, c in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
        open(os.path.join(d, p), "w").write(c)
    for p in execs:
        os.chmod(os.path.join(d, p), 0o755)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    for p in execs:  # keep the mode even on filesystems that ignore chmod
        subprocess.run(["git", "-C", d, "update-index", "--chmod=+x", p], check=True)
    for p, c in (late or {}).items():  # exists on disk, not in the index
        os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
        open(os.path.join(d, p), "w").write(c)
    return d


def _fixtures(b):
    ok = "# T\n\nSee [a](docs/A.md) and `tools/ok.sh`.\n\n- Touch works (observed on hardware, 2026-10-22).\n\n```sh\ntools/ok.sh --x\nupstream/gen.sh\n```\n"
    base = {"docs/A.md": "x\n", "tools/ok.sh": "#!/bin/sh\n", "upstream-files.txt": "upstream/gen.sh\n"}
    def fx(name, readme, extra=None, execs=("tools/ok.sh",), late=None):
        return _mk(b, name, {**base, "README.md": readme, **(extra or {})}, execs, late)
    kn = lambda *rows: {"tools/check-readme.known": "".join("\t".join(r) + "\n" for r in rows)}
    L = ("README.md", "link", "broken relative link: docs/CASE_STUDY.md", "2026-10-24", "why")
    return {
        "clean": fx("clean", ok),
        "link": fx("link", "[c](docs/CASE_STUDY.md)\n"),  # S1
        "path": fx("path", "built by `tools/build-ios-root.sh`\n"),  # K5
        "cmd-parse": fx("cmdparse", "```sh\nif then\n```\n"),
        "cmd-exec": fx("cmdexec", "```sh\ntools/noexec.sh\n```\n", {"tools/noexec.sh": "x\n"}),
        "works": fx("works", "## What works today\n\n- The shim runs on the phone.\n"),  # K8
        "works-prose": fx("worksprose", "Everything works fine.\n"),
        "leak": fx("leak", "see /home/alice/x\n"),
        "archive": fx("archive", "<!-- readme-gate: archive -->\n[c](docs/CASE_STUDY.md)\n"),
        "known": fx("known", "[c](docs/CASE_STUDY.md)\n",
                    {"tools/check-readme.known": "README.md\tlink\tbroken relative link: docs/CASE_STUDY.md\t2026-10-24\tREADME not fixed yet\n"}),
        "stale": fx("stale", "ok\n",
                    {"tools/check-readme.known": "README.md\tlink\tbroken relative link: gone.md\t2026-10-24\tREADME not fixed yet\n"}),
        "link-html": fx("linkhtml", '<img src="missing.svg">\n'),
        "link-ref": fx("linkref", "[r]: docs/GONE2.md\n"),
        "clean2": fx("clean2", "[a](docs/A.md#sec) `docs/*.md` `upstream/gen.sh` `docs/` [r]: x\n\n[r]: docs/A.md\n\n- Touch works (2026-10-22).\n\nmail git@github.com:x/y.git\n"),
        "works-undated": fx("worksundated", "- Touch works.\n"),
        "k2": fx("k2", "```sh\ntools/ok.sh  # (see docs/CREDITS.md)\n```\n"),
        "cmd-prefix": fx("cmdprefix", "```sh\npython3 tools/gone.py\nFOO=1 tools/gone2.sh\ncd sub && tools/gone3.sh\n```\n"),
        "untracked": fx("untracked", "[u](untracked.md) `research/notes.md`\n", {".gitignore": "research/\n"},
                        late={"untracked.md": "x\n", "research/notes.md": "x\n"}),
        "known-4col": fx("known4", "[c](docs/CASE_STUDY.md)\n", kn(L[:4])),
        "known-baddate": fx("knownbad", "[c](docs/CASE_STUDY.md)\n", kn(L[:3] + ("soon", "why"))),
        "known-once": fx("knownonce", "[c](docs/CASE_STUDY.md)\n[d](docs/CASE_STUDY.md)\n", kn(L)),
        "leak-email": fx("leakemail", "mail a@b.com and c@d.org\n"),
        "leak-known": fx("leakknown", "mail a@b.com\n", kn(("README.md", "leak", "e-mail address: a@b.com", "2026-10-24", "why"))),
        "leak-fp": fx("leakfp", "see logo@2x.png, uses actions/checkout@v4.1.1 and foo@1.2.3\n"),
        "leak-path-known": fx("leakpathknown", "see /home/alice/x\n", kn(("README.md", "leak", "local path: /home/alice/", "2026-10-24", "why"))),
        "link-dotdot": fx("linkdotdot", "[x](../outside.md)\n"),
        "k2-env": fx("k2env", '```sh\nls "$HOME/pixelios"\n```\n'),
        "k2-cd": fx("k2cd", "```sh\ncd shim && ./build.sh\n```\n", {"shim/build.sh": "#!/bin/sh\n"}, execs=("tools/ok.sh", "shim/build.sh")),
    }


def _tests(fx):
    """Return the names of failing fixture expectations."""
    def rules(name, private=False):
        r = run(fx[name], private)
        return None if r is None else sorted(f[2] for f in r[0])
    want = [
        ("clean", [], False), ("link", ["link"], False), ("path", ["path"], False),
        ("cmd-parse", ["cmd"], False), ("cmd-exec", ["cmd"], False), ("works", ["works"], False),
        ("works-prose", ["works"], False), ("leak", ["leak"], False), ("leak", [], True),
        ("works", [], True), ("archive", [], False), ("known", [], False), ("stale", ["stale"], False),
        ("link-html", ["link"], False), ("link-ref", ["link"], False), ("clean2", [], False),
        ("works-undated", ["works"], False), ("k2", ["path"], False), ("cmd-prefix", ["cmd", "cmd", "path"], False),
        ("untracked", ["link", "path"], False), ("known-4col", ["link", "stale"], False),
        ("known-baddate", ["link", "stale"], False), ("known-once", ["link"], False),
        ("leak-email", ["leak", "leak"], False), ("leak-known", [], False),
        ("leak-fp", [], False), ("leak-path-known", ["leak", "stale"], False), ("link-dotdot", ["link"], False),
        ("k2-env", [], False), ("k2-cd", [], False),
    ]
    bad = [f"{n}{'/private' if p else ''}" for n, w, p in want if rules(n, p) != w]
    ms = [f[3] for f in run(fx["leak-email"])[0]]  # C3: the message carries the matched text
    if ms != ["e-mail address: a@b.com", "e-mail address: c@d.org"]:
        bad.append("leak-message")
    if run(fx["leak-known"])[2] != 1:
        bad.append("leak-known-count")
    return bad


def self_test():
    with tempfile.TemporaryDirectory() as b:
        fx = _fixtures(b)
        bad = _tests(fx)
        for n in bad:
            print("self-test FAIL: fixture", n)
        for rule in RULES:  # mutant: the rule disabled must make some fixture test fail
            orig = run
            globals()["run"] = lambda root, private=False, off=frozenset(), _o=orig, _r=rule: _o(root, private, frozenset({_r}))
            try:
                killed = bool(_tests(fx))
            finally:
                globals()["run"] = orig
            print("mutant %-6s %s" % (rule, "killed" if killed else "SURVIVED"))
            if not killed:
                bad.append("mutant:" + rule)
        if run("/nonexistent-readme-gate") is not None:
            bad.append("not-a-repo")
    print("RESULT: %s self-test (%d rules, %d failures)" % ("FAIL" if bad else "PASS", len(RULES), len(bad)))
    return 1 if bad else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    private = "--private" in argv
    rest = [a for a in argv if a != "--private"]
    if len(rest) > 1 or any(a.startswith("-") for a in rest):
        print("RESULT: ERROR usage: check-readme.py [--private] [ROOT] | --self-test")
        return 2
    root = os.path.abspath(rest[0] if rest else os.getcwd())
    r = run(root, private) if os.path.isdir(root) else None
    if r is None:
        print("RESULT: ERROR %s is not a git repository" % root)
        return 2
    found, nf, nk = r
    for f, n, rule, msg in sorted(found):
        print("%s:%d %s %s" % (f, n, rule, msg))
    print("RESULT: %s %d findings (%d files, %d known)" % ("FAIL" if found else "PASS", len(found), nf, nk))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
