#!/usr/bin/env python3
"""matrix_wifi_list: conformance-matrix check for SPEC-WIFI Part F (Apple80211/IO80211 Settings rows W1-W17 and X1-X4; PORT-METHOD S2; T-3a86).

  tools/matrix_wifi_list.py --spec docs/specs/SPEC-WIFI.md [--root .]   --root = the kernel clone that holds the impl files
  tools/matrix_wifi_list.py --self-test                                  fixture + mutants (also run by tools/test_matrix_wifi_list.py)
Same status vocabulary and output form as tools/matrix_wifi_join.py (T-5dc1). Rules (each failure prints `MATRIX FAIL: ...`, the run ends `RESULT: FAIL matrix-wifi-list`):
 - exactly the rows W1..W17 and X1..X4 exist once each in the '## 7a. Part F' section (10 columns, last three: status, impl, gap);
 - status is implemented | partial | missing;
 - implemented/partial rows name impl as `<path>:<symbol>`: the file exists under --root and the symbol occurs in it; missing rows have impl `-`;
 - implemented rows have gap `-`; partial/missing rows have a gap cell `WIFI GAP: <id> <text>` (rule 8: no silent hole).
Prints `RESULT: PASS matrix-wifi-list rows=N implemented=N partial=N missing=N gaps=N list=N join=N xw2=N spec=<path>`."""
import argparse, os, re, sys, tempfile

LIST_ROWS = ["W%d" % i for i in range(1, 12)]	# R110 s.8.1 rows 1-11 (list-only milestone)
JOIN_ROWS = ["W%d" % i for i in range(12, 18)]	# R110 s.8.2 rows 12-17
XW2_ROWS = ["X%d" % i for i in range(1, 5)]	# X-W2 conclusions kept as [guess]
WANT = LIST_ROWS + JOIN_ROWS + XW2_ROWS
ROWPAT = r"^\|\s*([WX]\d+)\s*\|"


def check(spec_text, root):
    """Return (errors, counts, gaps) for the Part F rows in spec_text."""
    start = spec_text.find("## 7a. Part F")
    if start < 0:
        return ["spec has no '## 7a. Part F' section"], None, 0
    end = spec_text.find("\n## ", start + 4)
    part = spec_text[start:end if end > 0 else len(spec_text)]
    rows, errs = {}, []
    for line in part.splitlines():
        m = re.match(ROWPAT, line)
        if not m:
            continue
        rid = m.group(1)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if rid in rows:
            errs.append("duplicate row %s" % rid)
        elif len(cells) != 10:
            errs.append("%s has %d columns, want 10" % (rid, len(cells)))
        else:
            rows[rid] = cells
    errs += ["row %s missing from the spec" % r for r in WANT if r not in rows]
    errs += ["unexpected row %s" % r for r in rows if r not in WANT]
    count = {"implemented": 0, "partial": 0, "missing": 0}
    gaps = 0
    for r in WANT:
        if r not in rows:
            continue
        st, impl, gap = (c.strip("`") for c in rows[r][7:10])
        if st not in count:
            errs.append("%s: status %r not implemented|partial|missing" % (r, st))
            continue
        count[st] += 1
        if st == "missing":
            if impl != "-":
                errs.append("%s: missing row must have impl '-'" % r)
        else:
            path, _, sym = impl.partition(":")
            full = os.path.join(root, path)
            if not sym or not path:
                errs.append("%s: impl must be <path>:<symbol>, got %r" % (r, impl))
            elif not os.path.isfile(full):
                errs.append("%s: impl file %s not found under %s" % (r, path, root))
            elif sym not in open(full, errors="replace").read():
                errs.append("%s: impl symbol %s not in %s" % (r, sym, path))
        if st == "implemented":
            if gap != "-":
                errs.append("%s: implemented row must have gap '-'" % r)
        elif re.match(r"WIFI GAP: %s \S" % re.escape(r), gap):
            gaps += 1
        else:
            errs.append("%s: %s row needs a gap cell 'WIFI GAP: %s <text>', got %r" % (r, st, r, gap[:40]))
    return errs, count, gaps


def _fixture():
    """A minimal valid Part F (21 rows) and an impl file; returns (spec_text, dict of row lines)."""
    lines = {}
    for r in WANT:
        if r == "W16":
            lines[r] = "| W16 | s | f | p | e | src | c | implemented | impl.h:SYM | - |"
        elif r == "W3":
            lines[r] = "| W3 | s | f | p | e | src | c | missing | - | WIFI GAP: W3 nothing yet |"
        else:
            lines[r] = "| %s | s | f | p | e | src | c | partial | impl.h:SYM | WIFI GAP: %s partly |" % (r, r)
    return lines


def _spec(lines):
    return "## 6. earlier\n| W1 | not part F |\n\n## 7a. Part F: x\n" + "\n".join(lines.values()) + "\n\n## 8. later\n"


def self_test():
    fails = []
    with tempfile.TemporaryDirectory() as root:
        open(os.path.join(root, "impl.h"), "w").write("#define SYM 1\n")
        good = _fixture()
        errs, cnt, gaps = check(_spec(good), root)
        if errs or cnt != {"implemented": 1, "partial": 19, "missing": 1} or gaps != 20:
            fails.append("fixture should pass with 1/19/1 and 20 gaps: %r %r %r" % (errs, cnt, gaps))

        def mutant(name, edit, want):
            ls = dict(good)
            edit(ls)
            e, _, _ = check(_spec(ls), root)
            if not any(want in x for x in e):
                fails.append("mutant %s not caught (want %r in %r)" % (name, want, e))
        mutant("bad status", lambda l: l.update(W2=l["W2"].replace("partial", "done")), "not implemented|partial|missing")
        mutant("missing gap", lambda l: l.update(W2=l["W2"].replace("WIFI GAP: W2 partly", "-")), "needs a gap cell")
        mutant("gap names other row", lambda l: l.update(W2=l["W2"].replace("GAP: W2", "GAP: W9")), "needs a gap cell")
        mutant("implemented with gap", lambda l: l.update(W16=l["W16"].replace("| - |", "| WIFI GAP: W16 x |")), "must have gap '-'")
        mutant("missing with impl", lambda l: l.update(W3=l["W3"].replace("| - |", "| impl.h:SYM |", 1)), "must have impl '-'")
        mutant("symbol absent", lambda l: l.update(W2=l["W2"].replace("SYM", "NOPE")), "symbol NOPE not in")
        mutant("file absent", lambda l: l.update(W2=l["W2"].replace("impl.h", "gone.h")), "not found under")
        mutant("row dropped", lambda l: l.pop("W17"), "row W17 missing")
        mutant("extra row", lambda l: l.update(W18=l["W2"].replace("W2", "W18")), "unexpected row W18")
        mutant("short row", lambda l: l.update(W2="| W2 | s | f |"), "W2 has 3 columns")
        e, _, _ = check("## 7. x\n", root)
        if not e:
            fails.append("spec without Part F passed")
        dup = _spec(good).replace("\n## 8.", "\n" + good["W2"] + "\n\n## 8.")
        if not any("duplicate row W2" in x for x in check(dup, root)[0]):
            fails.append("duplicate row not caught")
    for f in fails:
        print("SELF-TEST FAIL:", f)
    print("RESULT: %s matrix_wifi_list self-test" % ("FAIL" if fails else "PASS"))
    return 1 if fails else 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec")
    ap.add_argument("--root", default=".")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.spec:
        ap.error("--spec is required")
    errs, count, gaps = check(open(a.spec).read(), a.root)
    for e in errs:
        print("MATRIX FAIL:", e)
    if errs:
        print("RESULT: FAIL matrix-wifi-list errors=%d" % len(errs))
        return 1
    print("RESULT: PASS matrix-wifi-list rows=%d implemented=%d partial=%d missing=%d gaps=%d list=%d join=%d xw2=%d spec=%s" %
          (len(WANT), count["implemented"], count["partial"], count["missing"], gaps,
           len(LIST_ROWS), len(JOIN_ROWS), len(XW2_ROWS), a.spec))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
