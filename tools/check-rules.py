#!/usr/bin/env python3
"""Coding-rules lint (docs/research/coding-rules-draft.md). Rule modules: tools/check_rules/r*.py (RULE, check(root) -> [Finding]).
Ratchet baseline, whole tree: tools/check_rules/baseline/RNN.txt, lines `path<TAB>sha1(stripped line)<TAB>count`, keyed on text not line numbers.
A finding not in the baseline fails; a baseline entry no longer found also fails (remove it), so the baseline only shrinks.
Usage: check-rules.py --strict | --report | --diff REV  [--root DIR] [--baseline-dir DIR] [--write-baseline]
Last line RESULT: PASS|FAIL n new, m baselined. Exit 0 PASS, 1 FAIL, 2 usage."""
import argparse, collections, hashlib, importlib, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def digest(text):
    return hashlib.sha1(text.strip().encode()).hexdigest()


def load_rules():
    mods = []
    for p in sorted((HERE / "check_rules").glob("r*.py")):
        m = importlib.import_module("check_rules." + p.stem)
        if not hasattr(m, "RULE") or not hasattr(m, "check"):
            raise SystemExit(f"RESULT: FAIL {p.name} lacks RULE/check")
        mods.append(m)
    return mods


def read_baseline(bdir, rule):
    base = collections.Counter()
    f = pathlib.Path(bdir) / f"{rule}.txt"
    if f.exists():
        for l in f.read_text().splitlines():
            if l.strip() and not l.startswith("#"):
                path, h, n = l.split("\t")
                base[(path, h)] = int(n)
    return base


def run(mods, root, bdir):
    """-> (new findings, stale baseline lines, baselined count, per-rule Counter of findings)."""
    new, stale, nbase, counts = [], [], 0, {}
    for m in mods:
        found = m.check(root)
        cnt = collections.Counter((f.path, digest(f.text)) for f in found)
        counts[m.RULE] = cnt
        base = read_baseline(bdir, m.RULE)
        left = collections.Counter(base)
        for f in found:
            k = (f.path, digest(f.text))
            if left[k] > 0:
                left[k] -= 1
                nbase += 1
            else:
                new.append(f)
        for (path, h), n in sorted(left.items()):
            if n > 0:
                stale.append(f"{m.RULE} baseline stale: remove it: {path} {h} x{n}")
    return new, stale, nbase, counts


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--strict", action="store_true")
    g.add_argument("--report", action="store_true")
    g.add_argument("--diff", metavar="REV")
    g.add_argument("--write-baseline", action="store_true", help="rewrite RNN.txt from the current findings (a human reviews the diff)")
    ap.add_argument("--root", default=str(HERE.parent))
    ap.add_argument("--baseline-dir", default=str(HERE / "check_rules" / "baseline"))
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    mods = load_rules()
    new, stale, nbase, counts = run(mods, a.root, a.baseline_dir)
    if a.write_baseline:
        pathlib.Path(a.baseline_dir).mkdir(parents=True, exist_ok=True)
        for m in mods:
            f = pathlib.Path(a.baseline_dir) / f"{m.RULE}.txt"
            rows = [f"{p}\t{h}\t{n}" for (p, h), n in sorted(counts[m.RULE].items())]
            f.write_text("".join(r + "\n" for r in rows)) if rows else f.unlink(missing_ok=True)
        print("RESULT: PASS baseline written")
        return 0
    if a.diff:
        r = subprocess.run(["git", "-C", a.root, "diff", "--name-only", a.diff, "--"], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr.strip())
            print("RESULT: FAIL git diff failed")
            return 2
        files = set(r.stdout.split())
        new = [f for f in new if f.path in files]
        stale = []
    for f in new:
        print(f"{f.path}:{f.line}: {f.msg}\n    {f.text.strip()}")
    for s in stale:
        print(s)
    if a.report:
        print(f"RESULT: PASS (report) {len(new)} new, {nbase} baselined, {len(stale)} stale")
        return 0
    bad = bool(new or stale)
    print(f"RESULT: {'FAIL' if bad else 'PASS'} {len(new)} new, {nbase} baselined" + (f", {len(stale)} stale" if stale else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
