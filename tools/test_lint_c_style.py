#!/usr/bin/env python3
"""tools/lint-c --style (tools/lint_c_style.py, docs/STYLE-C.md): one passing and one failing fixture per rule (1 loop bound, 2 recursion, 4 assert density,
5 sized types, 7 function length and 100 columns), the ratchet (an old long function untouched = no finding, the same function edited = finding) and the
style-allow escape. Fixtures are throw-away git repos with a `trunk` branch. Last line: RESULT: PASS cases=N | RESULT: FAIL cases=N failed=M."""
import contextlib, io, os, shutil, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lint-c")
OK_FN = """static uint32_t ok(uint32_t v)
{
	SHIM_ASSERT(v < 100);
	SHIM_ASSERT(v != 7);
	return v + 1;
}
"""
cases, bad = 0, []

def sh(cwd, *a):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

def repo(old, new, tidy=None):
    """git repo, trunk = `old` shim/a.c; working tree = `new` (uncommitted). Returns (dir, env)."""
    d = tempfile.mkdtemp(prefix="lintstyle")
    os.makedirs(d + "/shim/out")
    sh(d, "git", "init", "-q", "-b", "trunk")
    open(d + "/shim/a.c", "w").write(old)
    sh(d, "git", "add", "-A")
    sh(d, "git", "commit", "-q", "-m", "base")
    open(d + "/shim/a.c", "w").write(new)
    env = dict(os.environ, LINT_ROOT=d, LINT_TIDY=tidy or "/bin/true", LINT_CPPCHECK="/bin/true")
    env.pop("LINT_STYLE_BASE", None)   # a caller-set base ref does not exist in the throw-away fixture repo
    return d, env

def run(old, new, tidy=None):
    d, env = repo(old, new, tidy)
    try:
        p = subprocess.run([sys.executable, TOOL, "--style"], env=env, capture_output=True, text=True, cwd=d)
    finally:
        shutil.rmtree(d)
    return p.returncode, p.stdout

def case(name, old, new, want_pass, needle=None, tidy=None):
    global cases
    cases += 1
    rc, out = run(old, new, tidy)
    last = [l for l in out.splitlines() if l.startswith("RESULT:")][-1:] or ["<no RESULT>"]
    good = (rc == 0) == want_pass and (("PASS style new=0" in last[0]) if want_pass else ("FAIL style new=" in last[0] and (needle is None or needle in out)))
    print("%s %s: %s" % ("ok  " if good else "FAIL", name, last[0]))
    if not good:
        bad.append(name)
        print(out)

def fake_tidy(d, text):
    p = os.path.join(d, "fake-tidy")
    open(p, "w").write("#!/bin/sh\ncat <<'X'\n%s\nX\n" % text)
    os.chmod(p, 0o755)
    return p

def run_all():
    """Run every fixture and print the report; nothing runs at import (gate-unit-shards counts tests by importing this file)."""
    global cases
    cases = 0
    bad.clear()
    HDR = "#include <stdint.h>\n"
    case("base: compliant function passes", HDR, HDR + OK_FN, True)
    # rule 1: loop bound
    case("rule1 pass: counter-bounded loop", HDR, HDR + "static void w(void)\n{\n\tSHIM_ASSERT(1);\n\tSHIM_ASSERT(2);\n\tfor (uint32_t i = 0; i < 10; i++)\n\t\tSHIM_ASSERT(i < 10);\n}\n", True)
    case("rule1 pass: deadline loop", HDR, HDR + "static void w(uint64_t dl)\n{\n\tSHIM_ASSERT(dl);\n\tSHIM_ASSERT(dl > 1);\n\twhile (now() < dl)\n\t\t;\n}\n", True)
    case("rule1 pass: bounded comment", HDR, HDR + "static void w(void)\n{\n\tSHIM_ASSERT(1);\n\tSHIM_ASSERT(2);\n\t/* bounded: halt loop, the phone is stopped */\n\tfor (;;)\n\t\t__asm__ volatile(\"wfe\");\n}\n", True)
    case("rule1 fail: poll without timeout", HDR, HDR + "static void w(void)\n{\n\tSHIM_ASSERT(1);\n\tSHIM_ASSERT(2);\n\twhile (rd32(0x10) & 1)\n\t\t;\n}\n", False, "loop-bound")
    case("rule1 fail: for(;;) without comment", HDR, HDR + "static void w(void)\n{\n\tSHIM_ASSERT(1);\n\tSHIM_ASSERT(2);\n\tfor (;;)\n\t\tspin();\n}\n", False, "loop-bound")
    # --all (T-ca25): the unbounded count covers loops in functions the diff did not touch
    old = HDR + "static void w(void)\n{\n\twhile (rd32(0x10) & 1)\n\t\t;\n}\n"
    for name, src, want in (("--all fail: untouched unbounded loop is counted", old, False),
                            ("--all pass: bounded comment", old.replace("\twhile", "\t/* bounded: test */\n\twhile"), True)):
        cases += 1
        d, env = repo(src, src)
        p = subprocess.run([sys.executable, TOOL, "--style", "--all"], env=env, capture_output=True, text=True, cwd=d)
        shutil.rmtree(d)
        last = p.stdout.strip().splitlines()[-1]
        good = (p.returncode == 0) == want and last.endswith("unbounded=%d" % (0 if want else 1)) and (want or "a.c:" in p.stdout)
        print("%s %s: %s" % ("ok  " if good else "FAIL", name, last))
        if not good:
            bad.append(name); print(p.stdout)
    # ratchet: a comment-only edit does not make an old function "changed"; a code edit does
    oldf = HDR + "static uint32_t f(uint32_t v)\n{\n\treturn v;\n}\n"
    case("ratchet pass: comment-only edit of an old function", oldf, oldf.replace("\treturn", "\t/* why */\n\treturn"), True)
    case("ratchet fail: code line turned into a comment", oldf, oldf.replace("\treturn v;", "\t/* return v; */"), False, "SHIM_ASSERT in 1 new/changed")
    case("ratchet fail: code edit of the same function", oldf, oldf.replace("return v;", "return v + 1;"), False, "SHIM_ASSERT in 1 new/changed")
    # CLI: plain --style prints no unbounded=, --all alone is refused
    d, env = repo(HDR, HDR + OK_FN)
    p1 = subprocess.run([sys.executable, TOOL, "--style"], env=env, capture_output=True, text=True, cwd=d)
    p2 = subprocess.run([sys.executable, TOOL, "--all"], env=env, capture_output=True, text=True, cwd=d)
    shutil.rmtree(d)
    for name, good in (("--style prints no unbounded=", p1.returncode == 0 and "unbounded=" not in p1.stdout),
                       ("--all without --style is refused", p2.returncode == 2 and "--all needs --style" in p2.stderr)):
        cases += 1
        print("%s %s" % ("ok  " if good else "FAIL", name))
        if not good:
            bad.append(name)
    # rule 4: asserts
    case("rule4 pass: two asserts", HDR, HDR + OK_FN, True)
    case("rule4 fail: none", HDR, HDR + "static uint32_t f(uint32_t v)\n{\n\treturn v;\n}\n", False, "SHIM_ASSERT in 1 new/changed")
    case("rule4 pass: average of two (3+1 over 2 functions)", HDR, HDR + OK_FN.replace("ok", "a") + "static uint32_t b(uint32_t v)\n{\n\tSHIM_ASSERT(v);\n\tSHIM_ASSERT(v > 1);\n\tSHIM_ASSERT(v > 2);\n\treturn v;\n}\n" + "static uint32_t c(uint32_t v)\n{\n\tSHIM_ASSERT(v);\n\treturn v;\n}\n", True)
    # rule 5: sized types
    case("rule5 pass: sized types", HDR, HDR + OK_FN, True)
    case("rule5 fail: bare unsigned", HDR, HDR + "static void f(unsigned v)\n{\n\tSHIM_ASSERT(v);\n\tSHIM_ASSERT(v > 1);\n}\n", False, "sized-types")
    case("rule5 fail: bare int local", HDR, HDR + "static void f(void)\n{\n\tint v = 3;\n\tSHIM_ASSERT(v);\n\tSHIM_ASSERT(v > 1);\n}\n", False, "sized-types")
    case("rule5 pass: int in comment and string", HDR, HDR + "static void f(void)\n{\n\tSHIM_ASSERT(1); /* an int here */\n\tSHIM_ASSERT(2);\n\tlog(\"int unsigned\");\n}\n", True)
    # rule 7: function length and columns
    def fn_of(n):
        """a function of exactly n lines (signature, brace, n-3 asserts, closing brace)"""
        return "static void f(void)\n{\n" + "".join("\tSHIM_ASSERT(%d);\n" % i for i in range(n - 3)) + "}\n"

    case("rule7 pass: exactly 70 lines", HDR, HDR + fn_of(70), True)
    case("rule7 fail: 71 lines", HDR, HDR + fn_of(71), False, "func-len")
    case("rule7 pass: 100 columns", HDR, HDR + OK_FN + "/* " + "x" * 94 + " */\n", True)
    case("rule7 fail: 101 columns", HDR, HDR + OK_FN + "/* " + "x" * 95 + " */\n", False, "col-100")
    # ratchet: an old long, unsized, unbounded function
    OLD = HDR + "static void old(unsigned v)\n{\n\twhile (rd32(v) & 1)\n\t\t;\n" + "\tx++;\n" * 80 + "}\n"
    case("ratchet pass: old bad function untouched, other code added", OLD, OLD + OK_FN, True)
    case("ratchet fail: the same old function edited", OLD, OLD.replace("x++;", "x--;", 1), False, "func-len")
    case("ratchet pass: long old line left alone", HDR + "/* " + "y" * 120 + " */\n", HDR + "/* " + "y" * 120 + " */\n" + OK_FN, True)
    # escape
    case("allow pass: style-allow with a reason", HDR, HDR + "static void f(void)\n{\n\t/* style-allow: assert reporter cannot assert */\n\tx();\n}\n", True)
    case("allow fail: style-allow without a reason", HDR, HDR + "static void f(void)\n{\n\t/* style-allow: loop-bound */\n\twhile (x())\n\t\t;\n}\n", False, "loop-bound")
    # stop path: SHIM_ASSERT is refused in the files/functions the report and stop run through (review T-98a7-assert F1)
    def case_in_file(name, path, old, new, want_pass, needle=None):
        global cases
        cases += 1
        d, env = repo(old, new)
        os.makedirs(os.path.dirname(os.path.join(d, path)), exist_ok=True)
        try:
            src = new
            open(os.path.join(d, path), "w").write(src)
            if path != "shim/a.c":
                os.remove(d + "/shim/a.c")
                sh(d, "git", "rm", "-q", "--cached", "shim/a.c")
            p = subprocess.run([sys.executable, TOOL, "--style"], env=env, capture_output=True, text=True, cwd=d)
        finally:
            shutil.rmtree(d)
        last = [l for l in p.stdout.splitlines() if l.startswith("RESULT:")][-1:] or ["<no RESULT>"]
        good = (p.returncode == 0) == want_pass and (needle is None or needle in p.stdout)
        print("%s %s: %s" % ("ok  " if good else "FAIL", name, last[0]))
        if not good:
            bad.append(name)
            print(p.stdout)

    case_in_file("stop-path pass: SHIM_ASSERT in an ordinary file", "shim/a.c", HDR, HDR + OK_FN, True)
    case_in_file("stop-path fail: SHIM_ASSERT in console.c", "shim/console.c", HDR, HDR + OK_FN, False, "stop-path")
    case_in_file("stop-path fail: SHIM_ASSERT in shim.c reset()", "shim/shim.c", HDR,
                 HDR + "static void reset(void)\n{\n\tSHIM_ASSERT(1);\n\tSHIM_ASSERT(2);\n}\n", False, "stop-path")
    case_in_file("stop-path pass: SHIM_ASSERT in another shim.c function", "shim/shim.c", HDR,
                 HDR + "static void other(void)\n{\n\tSHIM_ASSERT(1);\n\tSHIM_ASSERT(2);\n}\n", True)
    # rule 2: recursion (clang-tidy output, fake and real)
    cases += 1
    d, env = repo(HDR, HDR + OK_FN + "static uint32_t r(uint32_t v)\n{\n\tSHIM_ASSERT(v);\n\tSHIM_ASSERT(v > 1);\n\treturn r(v - 1);\n}\n")
    tidy = fake_tidy(d, "shim/a.c:11:1: warning: function 'r' is within a recursive call chain [misc-no-recursion]")
    env["LINT_TIDY"] = tidy
    p = subprocess.run([sys.executable, TOOL, "--style"], env=env, capture_output=True, text=True, cwd=d)
    good = p.returncode == 1 and "style recursion" in p.stdout and "FAIL style new=1" in p.stdout
    print("%s rule2 fail: recursion reported by clang-tidy: %s" % ("ok  " if good else "FAIL", p.stdout.strip().splitlines()[-1]))
    if not good:
        bad.append("rule2 fail"); print(p.stdout)
    cases += 1
    env["LINT_TIDY"] = "/bin/true"		# tidy finds nothing: the same file passes
    p = subprocess.run([sys.executable, TOOL, "--style"], env=env, capture_output=True, text=True, cwd=d)
    good = p.returncode == 0 and "PASS style new=0" in p.stdout
    print("%s rule2 pass: no recursion reported: %s" % ("ok  " if good else "FAIL", p.stdout.strip().splitlines()[-1]))
    if not good:
        bad.append("rule2 pass"); print(p.stdout)
    shutil.rmtree(d)
    real = shutil.which("clang-tidy-19") or shutil.which("clang-tidy")
    if real:
        for name, body, want in (("rule2 real clang-tidy fail: direct recursion", "static uint32_t r(uint32_t v)\n{\n\tSHIM_ASSERT(v);\n\tSHIM_ASSERT(v > 1);\n\treturn v ? r(v - 1) : 0;\n}\n", False),
                                 ("rule2 real clang-tidy pass: iteration", "static uint32_t r(uint32_t v)\n{\n\tSHIM_ASSERT(v);\n\tSHIM_ASSERT(v > 1);\n\treturn v - 1;\n}\n", True)):
            cases += 1
            pre = HDR + "#define SHIM_ASSERT(c) ((void)(c))\n"
            d, env = repo(pre, pre + body)
            env["LINT_TIDY"] = real
            p = subprocess.run([sys.executable, TOOL, "--style"], env=env, capture_output=True, text=True, cwd=d)
            good = (p.returncode == 0) == want and (want or "style recursion" in p.stdout)
            print("%s %s: %s" % ("ok  " if good else "FAIL", name, p.stdout.strip().splitlines()[-1]))
            if not good:
                bad.append(name); print(p.stdout)
            shutil.rmtree(d)
    else:
        print("NOTE real clang-tidy not installed: the 2 real-analyzer recursion cases are not run (the fake-analyzer cases above are)")
    print("RESULT: %s cases=%d%s" % ("FAIL" if bad else "PASS", cases, " failed=%d (%s)" % (len(bad), ", ".join(bad)) if bad else ""))

class LintCStyle(unittest.TestCase):
    """gate-unit-shards runs every test file through `unittest discover`: the fixtures run inside the test (stdout captured), never at import."""

    def test_all_fixtures(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            run_all()
        self.assertEqual(bad, [], "failed fixtures: %s\n%s" % (bad, out.getvalue()))
        self.assertGreater(cases, 0)

if __name__ == "__main__":
    run_all()
    sys.exit(1 if bad else 0)
