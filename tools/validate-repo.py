#!/usr/bin/env python3
"""Repository integrity checker: patch series, python/shell syntax, proposal register, README gate.

Run from anywhere; the repo root is the parent of this file's directory.
Exit 0 when every check passes, 1 otherwise. Last line is a RESULT: line.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
PATCHES = os.path.join(ROOT, "xnu", "patches")

COLOR = sys.stdout.isatty()


def paint(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if COLOR else text


def ok(msg):
    print(paint("32", "✓ " + msg))


def bad(header, findings):
    print(paint("31", "[-] " + header))
    for f in findings:
        print("    - " + f)


NAME_RE = re.compile(r"^(\d{4})-(.+)\.patch$")
HELD_RE = re.compile(r"^(\d{4})  (\1-\S+\.patch) ")


def held_elsewhere():
    """Numbers listed by `tools/patch-numbers status`; empty set on any failure (fail closed)."""
    try:
        r = subprocess.run([os.path.join(TOOLS, "patch-numbers"), "status"], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        out = r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    held = set()
    for line in out.splitlines():
        m = HELD_RE.match(line)
        if m:
            held.add(int(m.group(1)))
    return held


def check_patches():
    title = "patch series"
    try:
        names = sorted(n for n in os.listdir(PATCHES)
                       if n.endswith(".patch") and not n.startswith("."))
    except OSError as e:
        return False, title, ["cannot list %s: %s" % (PATCHES, e)]
    if not names:
        return False, title, ["no .patch files in xnu/patches"]
    findings = []
    bynum = {}
    for n in names:
        p = os.path.join(PATCHES, n)
        m = NAME_RE.match(n)
        if not m:
            findings.append("%s: invalid name (want NNNN-slug.patch)" % n)
            continue
        if os.path.isdir(p) or not os.path.isfile(p):
            findings.append("%s: not a regular file" % n)
            continue
        bynum_list = bynum.setdefault(int(m.group(1)), [])
        bynum_list.append(n)
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as fh:
                head = fh.read(2048).decode("utf-8", "ignore")
        except OSError as e:
            findings.append("%s: unreadable: %s" % (n, e))
            continue
        if size == 0:
            findings.append("%s: empty file" % n)
        elif not ("--- " in head or "diff " in head or "Index:" in head):
            findings.append("%s: no diff header in first 2048 bytes" % n)
    for num, lst in sorted(bynum.items()):
        if len(lst) > 1:
            findings.append("duplicate number %04d: %s" % (num, ", ".join(lst)))
    if bynum:
        held = held_elsewhere()
        expected = 1
        for num in sorted(bynum):
            if num > expected:
                hole = range(expected, num)
                if not all(x in held for x in hole):
                    findings.append("gap: expected %04d, found %s" % (expected, bynum[num][0]))
            expected = num + 1
    if findings:
        return False, title, findings
    return True, "%d patches, series contiguous" % len(bynum), []


def check_py(verbose):
    files = sorted(f for f in os.listdir(TOOLS) if f.endswith(".py") and not f.startswith("."))
    findings = []
    for f in files:
        p = os.path.join(TOOLS, f)
        if not os.path.isfile(p):
            findings.append("tools/%s: not a regular file" % f)
            continue
        try:
            with open(p, "rb") as fh:
                compile(fh.read(), p, "exec")
            if verbose:
                print("    ok tools/" + f)
        except (SyntaxError, ValueError) as e:
            findings.append("tools/%s: %s" % (f, e))
        except OSError as e:
            findings.append("tools/%s: unreadable: %s" % (f, e))
    if findings:
        return False, "python syntax", findings
    return True, "python syntax: %d files checked" % len(files), []


def check_sh(verbose):
    files = []
    for d in ("tools", "shim", os.path.join("xnu", "patches")):
        full = os.path.join(ROOT, d)
        if os.path.isdir(full):
            files += [os.path.join(d, f) for f in sorted(os.listdir(full))
                      if f.endswith(".sh") and not f.startswith(".")]
    findings = []
    for rel in files:
        if not os.path.isfile(os.path.join(ROOT, rel)):
            findings.append("%s: not a regular file" % rel)
            continue
        try:
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, rel)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            findings.append("%s: cannot run bash: %s" % (rel, e))
            continue
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace").strip().splitlines()
            findings.append("%s: %s" % (rel, err[0] if err else "bash -n failed"))
        elif verbose:
            print("    ok " + rel)
    if findings:
        return False, "shell syntax", findings
    return True, "shell syntax: %d scripts checked" % len(files), []


def run_child(title, cmd):
    try:
        r = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        return False, title, ["cannot run the %s: %s" % (title, e)]
    out = r.stdout.decode("utf-8", "replace").rstrip()
    err = r.stderr.decode("utf-8", "replace").rstrip()
    if r.returncode == 0:
        lines = out.splitlines()
        return True, lines[-1] if lines else title + " passed (no output)", []
    text = out or err or "exit status %d, no output" % r.returncode
    return False, title, text.splitlines()


def main():
    ap = argparse.ArgumentParser(description="Check repo integrity (patches, syntax, proposals, README).")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every file that passes a per-file check")
    args = ap.parse_args()

    print(paint("1", "== validate-repo: %s ==" % ROOT))
    checks = [
        lambda: check_patches(),
        lambda: check_py(args.verbose),
        lambda: check_sh(args.verbose),
        lambda: run_child("tool-proposal register",
                          [sys.executable, os.path.join(TOOLS, "tool-proposals"), "validate"]),
        lambda: run_child("README gate",
                          [sys.executable, "-B", os.path.join(TOOLS, "check-readme.py"), ROOT]),
    ]
    failed = 0
    for fn in checks:
        try:
            good, msg, findings = fn()
        except Exception as e:  # keep running the remaining checks
            good, msg, findings = False, "check crashed", ["%s: %s" % (type(e).__name__, e)]
        if good:
            ok(msg)
        else:
            failed += 1
            bad(msg, findings)
    if failed:
        print(paint("1", "validate-repo FAILED"))
        print("RESULT: FAIL (%d of %d checks failed)" % (failed, len(checks)))
        return 1
    print(paint("1", "validate-repo PASSED"))
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
