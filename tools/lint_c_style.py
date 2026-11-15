"""C style ratchet for on-device code (docs/STYLE-C.md, T-98a7), called by `tools/lint-c --style`.

Scope: shim/*.c and soc/**/*.c except host tests (*_host_test.c, host_test.c). Rules checked here (numbers = STYLE-C.md):
  1 loop-bound   every for/while condition compares a counter or deadline (< > <= >= or a decrement) or carries `/* bounded: why */`
  4 assert       at least 2 SHIM_ASSERT per new/changed function on average (one finding per shortfall, aggregate)
  5 sized-types  no bare int/long/short/unsigned in code (use uint32_t etc.)
  7 func-len     at most 70 lines per function (signature line to closing brace)
  7 col-100      added lines at most 100 columns (tab = 4)
Rule 2 (no recursion) is clang-tidy misc-no-recursion, run by lint-c on the files with new/changed functions.
Also refused outright (not a ratchet): SHIM_ASSERT in the stop path (console.c, ramlog.c, pstore.c, shim_assert.c, shim.c reset/panic/USB-service/secondary_main functions).
Rule 3 (static allocation) and 6 (every error handled) are review rules, not machine-checked.
RATCHET: a function is new/changed when `git diff --merge-base BASE` adds or edits a line of CODE inside it (a comment-only edit does not count) (or its file is untracked/new). Findings in new/changed
functions (and added long lines) count as `new` and fail; every other finding is `baseline`, reported only. Escape, counted and printed:
`/* style-allow: <rule> <why> */` inside the function (rule = loop-bound|assert|sized-types|func-len; a marker without a <why> does not apply, so the finding stays).
"""
import os, re, subprocess

MAX_LINES, MAX_COLS, TAB, ASSERTS_PER_FUNC = 70, 100, 4, 2
HOST_TEST = re.compile(r"(^|/)(\w*_)?host_test\.c$|_host_test\.c$")
FUNC_HEAD = re.compile(r"^[A-Za-z_][^;{}()=]*?\b(\w+)\s*\(")
ATTR = re.compile(r"__attribute__\s*\(\(.*?\)\)")
UNSIZED = re.compile(r"(?<![\w$])(?:unsigned|signed|int|long|short)\b")
CAST = re.compile(r"\(\s*(?:unsigned|signed|int|long|short)(?:\s+(?:int|long|char|short))*\s*\)")
LOOP = re.compile(r"\b(for|while)\s*\(")
BOUNDED = re.compile(r"bounded:\s*\S")
ALLOW = re.compile(r"style-allow:\s*([\w-]+)\s*(?!\*/)(\S.*?)?\s*(?:\*/|$)")
RULES = ("loop-bound", "assert", "sized-types", "func-len")
# Code the SHIM_ASSERT report and stop run through (review T-98a7-assert F1, C3, C5): an assertion there can recurse or hide its own report. Refused everywhere, not ratcheted.
STOP_FILES = ("shim/console.c", "shim/ramlog.c", "shim/pstore.c", "shim/shim_assert.c")
STOP_FUNCS = {"shim/shim.c": ("reset", "shim_stop", "shim_halt", "shim_cpu_slot", "exc_handler", "bluejay_reset_chain", "bluejay_reset_bootloader",
                              "bluejay_bootloader_arm", "usbg_service", "secondary_main")}
ASSERT_CALL = re.compile(r"\bSHIM_ASSERT\s*\(")


def comment_only(raw):
    """True for a blank line, a `//` line or a line that is exactly one `/* ... */` comment closed on the same line."""
    t = raw.strip()
    return not t or t.startswith("//") or (t.startswith("/*") and t.endswith("*/") and t.count("/*") == 1 and t.count("*/") == 1)


def in_scope(path):
    return path.endswith(".c") and path.startswith(("shim/", "soc/")) and "/out/" not in path and not HOST_TEST.search(path)


def strip_code(text):
    """text with comments and string/char literals blanked (newlines and columns kept); also returns {line: comment text}."""
    out, comments, i, n, line = [], {}, 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "/" and text[i:i + 2] == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            seg = text[i:j]
            for k, part in enumerate(seg.split("\n")):
                comments[line + k] = comments.get(line + k, "") + part
            out.append(re.sub(r"[^\n]", " ", seg))
            line += seg.count("\n")
            i = j
        elif c == "/" and text[i:i + 2] == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments[line] = comments.get(line, "") + text[i:j]
            out.append(" " * (j - i))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(c + " " * (j - i - 2) + c if j - i >= 2 else c)
            i = j
        else:
            out.append(c)
            if c == "\n":
                line += 1
            i += 1
    return "".join(out), comments


def functions(text):
    """[(name, first_line, last_line)] for definitions whose `{` is alone on a column-0 line and whose `}` is the next column-0 `}` (1-based lines)."""
    lines = text.split("\n")
    funcs, i = [], 0
    while i < len(lines):
        if lines[i] == "{" and i > 0:
            j = i - 1
            while j > 0 and lines[j][:1] in (" ", "\t"):		# continuation lines of a wrapped signature
                j -= 1
            head = ATTR.sub(" ", " ".join(lines[j:i]))
            m = FUNC_HEAD.match(head)
            k = i + 1
            while k < len(lines) and lines[k] != "}":
                k += 1
            if m and k < len(lines) and not head.lstrip().startswith(("#", "}")):
                funcs.append((m.group(1), j + 1, k + 1))
                i = k
        i += 1
    return funcs


def paren_end(code, start):
    depth = 0
    for i in range(start, len(code)):
        depth += (code[i] == "(") - (code[i] == ")")
        if depth == 0:
            return i
    return -1


def loop_findings(code, comments, first, last, line_starts):
    """[(line, text)] for loops in [first,last] whose condition has no counter/deadline comparison and no `bounded:` comment on/just above the loop."""
    found = []
    seg_start, seg_end = line_starts[first - 1], line_starts[last] if last < len(line_starts) else len(code)
    for m in LOOP.finditer(code, seg_start, seg_end):
        end = paren_end(code, m.end() - 1)
        if end < 0:
            continue
        cond = code[m.end():end]
        ln = code.count("\n", 0, m.start()) + 1
        if m.group(1) == "for":
            parts = cond.split(";")
            cond = parts[1] if len(parts) == 3 and parts[1].strip() else ""
        if re.search(r"(?<![<>-])[<>]=?(?![<>])|--|-=", cond):
            continue
        if any(BOUNDED.search(comments.get(k, "")) for k in (ln - 1, ln, ln + 1)):
            continue
        found.append((ln, "%s loop without a counter/deadline bound (add one or /* bounded: why */)" % m.group(1)))
    return found


def check_function(text_lines, code, comments, line_starts, fn):
    """findings of one function: [(rule, line, message)]; allowed rules removed and returned as allowed list."""
    name, first, last = fn
    res, allow, bad_allow = [], set(), []
    for ln in range(first, last + 1):
        for m in ALLOW.finditer(comments.get(ln, "")):
            if m.group(1) in RULES and m.group(2):
                allow.add(m.group(1))
            else:
                bad_allow.append((ln, m.group(1)))
    n = last - first + 1
    if n > MAX_LINES:
        res.append(("func-len", first, "%s is %d lines (max %d)" % (name, n, MAX_LINES)))
    for ln in range(first, last + 1):
        t = text_lines[ln - 1]
        if t.lstrip().startswith("#"):
            continue
        t = CAST.sub("", t)
        m = UNSIZED.search(t)
        if m:
            res.append(("sized-types", ln, "%s: unsized '%s' (use uint32_t etc.)" % (name, m.group(0))))
    for ln, msg in loop_findings(code, comments, first, last, line_starts):
        res.append(("loop-bound", ln, "%s: %s" % (name, msg)))
    for ln, rule in bad_allow:
        res.append(("loop-bound", ln, "%s: style-allow needs a known rule and a reason (got %r)" % (name, rule)))
    kept = [r for r in res if r[0] not in allow]
    return kept, [r for r in res if r[0] in allow], allow


BASE_CANDIDATES = ("trunk", "origin/trunk", "main", "origin/main", "master")


def resolve_base(root, base):
    """base ref to diff against: the given one, or (base empty) the first existing of trunk, origin/trunk, main, origin/main, master (a bundle clone only has origin/*)."""
    for ref in ([base] if base else BASE_CANDIDATES):
        if subprocess.run(["git", "-C", root, "rev-parse", "--verify", "-q", ref + "^{commit}"], capture_output=True).returncode == 0:
            return ref
    raise RuntimeError("no base ref found (%s); pass --base REF" % (base or ", ".join(BASE_CANDIDATES)))


def changed_lines(root, base):
    """({path: set(added line numbers)}, set(new paths), {path: lines of hunks that also removed old lines}) from `git diff --merge-base BASE -U0` plus untracked files; raises RuntimeError on a git failure."""
    def git(*a):
        p = subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError("git %s: %s" % (" ".join(a), p.stderr.strip()[:200]))
        return p.stdout
    git("rev-parse", "--verify", base + "^{commit}")
    added, new, cur, edited = {}, set(), None, {}
    for l in git("diff", "--merge-base", base, "-U0", "--no-color", "--no-ext-diff", "--", "shim", "soc").splitlines():
        if l.startswith("+++ b/"):
            cur = l[6:]
            added.setdefault(cur, set())
        elif l.startswith("+++ "):
            cur = None
        elif l.startswith("@@") and cur:
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", l)
            if m:
                s, c = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                added[cur].update(range(s, s + c))
                if not re.match(r"@@ -\d+,0 ", l):	# old lines removed or replaced: never a comment-only insertion
                    edited.setdefault(cur, set()).update(range(s, s + c))
    for l in git("ls-files", "--others", "--exclude-standard", "--", "shim", "soc").splitlines():
        new.add(l)
    for l in git("diff", "--merge-base", base, "--name-status", "--diff-filter=A", "--", "shim", "soc").splitlines():
        new.add(l.split("\t", 1)[1])
    return added, new, edited


def tracked_files(root):
    p = subprocess.run(["git", "-C", root, "ls-files", "-co", "--exclude-standard", "--", "shim", "soc"], capture_output=True, text=True)
    return sorted(f for f in p.stdout.splitlines() if in_scope(f) and os.path.exists(os.path.join(root, f)))


def run_style(root, base="trunk", files=None):
    """-> dict(new=[...], baseline=int, allowed=int, changed_files=[...], functions=int, asserts=int, unbounded=[...]); each new item = (path, line, rule, message);
    unbounded = (path, line, message) of EVERY loop-bound finding in every in-scope function, changed or not (`lint-c --style --all`, T-ca25)."""
    base = resolve_base(root, base)
    added, newfiles, edited = changed_lines(root, base)
    new, baseline, allowed, nfuncs, nasserts, changed_files, short, unbounded = [], 0, 0, 0, 0, set(), [], []
    for path in (files if files is not None else tracked_files(root)):
        if not in_scope(path):
            continue
        text = open(os.path.join(root, path), encoding="utf-8", errors="replace").read()
        code, comments = strip_code(text)
        text_lines = code.split("\n")
        line_starts, pos = [], 0
        for l in text_lines:
            line_starts.append(pos)
            pos += len(l) + 1
        touched = added.get(path, set())
        is_new = path in newfiles
        raw_lines = text.split("\n")
        for ln, raw in enumerate(raw_lines, 1):
            cols = len(raw.expandtabs(TAB))
            if cols <= MAX_COLS:
                continue
            if is_new or ln in touched:
                new.append((path, ln, "col-100", "line is %d columns (max %d, tab = %d)" % (cols, MAX_COLS, TAB)))
            else:
                baseline += 1
        for m in (ASSERT_CALL.finditer(code) if path in STOP_FILES else ()):
            new.append((path, code.count("\n", 0, m.start()) + 1, "stop-path", "SHIM_ASSERT in code the assert report/stop runs through (docs/STYLE-C.md)"))
        for fn in functions(code):
            name, first, last = fn
            if name in STOP_FUNCS.get(path, ()):
                for m in ASSERT_CALL.finditer(code, line_starts[first - 1], line_starts[last] if last < len(line_starts) else len(code)):
                    new.append((path, code.count("\n", 0, m.start()) + 1, "stop-path", "SHIM_ASSERT in %s, on the assert stop path (docs/STYLE-C.md)" % name))
            kept, allow_hits, allow = check_function(text_lines, code, comments, line_starts, fn)
            allowed += len(allow_hits) + (1 if "assert" in allow else 0)
            unbounded.extend((path, ln, msg) for rule, ln, msg in kept if rule == "loop-bound")
            # a pure insertion of whole-line comments / blank lines (`/* bounded: why */`, T-ca25) leaves the function's code as it was: not changed.
            # A line that replaced old lines, a code line, a string/preprocessor line, or a comment that is not closed on its own line still counts.
            changed = is_new or any(first <= ln <= last and (ln in edited.get(path, ()) or not comment_only(raw_lines[ln - 1])) for ln in touched if ln <= len(raw_lines))
            if not changed:
                baseline += len(kept)
                continue
            changed_files.add(path)
            for rule, ln, msg in kept:
                new.append((path, ln, rule, msg))
            if "assert" not in allow:
                a = len(re.findall(r"\bSHIM_ASSERT\s*\(", code[line_starts[first - 1]:line_starts[last] if last < len(line_starts) else len(code)]))
                nfuncs += 1
                nasserts += a
                if a < ASSERTS_PER_FUNC:
                    short.append("%s:%d %s=%d" % (path, first, name, a))
    if nasserts < ASSERTS_PER_FUNC * nfuncs:
        new.append(("-", 0, "assert", "%d SHIM_ASSERT in %d new/changed functions (need >= %d on average); below 2: %s%s" % (
            nasserts, nfuncs, ASSERTS_PER_FUNC * nfuncs, ", ".join(short[:6]), " ..." if len(short) > 6 else "")))
    return {"new": new, "baseline": baseline, "allowed": allowed, "changed_files": sorted(changed_files), "functions": nfuncs, "asserts": nasserts, "unbounded": unbounded}
