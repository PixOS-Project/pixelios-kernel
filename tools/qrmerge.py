#!/usr/bin/env python3
"""Merge QR-log snapshots (tools/qr-log.py --keep-going) into one chronological log.

  tools/qrmerge.py DIR|FILE... [-o merged.txt]     (DIR = every qr-*.txt in it, in name order)

QL2 logs (tools/qr-log.py, qrlog/qrstream.h) need no merging: qr2-<sid>.txt is the whole log recovered so far, with `[...gap...]` lines
where units are missing; load_qr2() reads those (+ the .json sidecar: coverage, when each part arrived).

Each QL1 snapshot is the TAIL (~12 kB) of the phone's log at one moment, so successive ones overlap. A new snapshot is aligned
to the end of the merged log by whole-line matching (difflib, so lines the phone's sanitiser dropped from one snapshot but
not the other do not break the alignment); only the lines after the overlap are appended. No overlap = the log scrolled
past between two snapshots: a `[...gap...]` line is inserted. Prints a one-line coverage summary to stderr.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import difflib, glob, os, sys

GAP = "[...gap...]"
TAIL = 800      # merged lines a new snapshot is aligned against (a snapshot is ~12 kB = a few hundred lines)


def merge(texts):
    """texts: snapshot strings, oldest first -> (lines, snap_of_line, gaps). snap_of_line[i] = first snapshot with line i (None for a gap marker)."""
    lines, snap, gaps = [], [], 0
    for si, text in enumerate(texts):
        new = text.split("\n")
        if new and new[-1] == "":
            new.pop()
        if not new:
            continue
        start = 0
        if lines:
            tail = lines[-TAIL:]
            need = min(3, len(new))
            ends = [m.b + m.size for m in difflib.SequenceMatcher(None, tail, new, autojunk=False).get_matching_blocks()
                    if m.size >= need]
            if ends:
                start = ends[-1]
            else:
                lines.append(GAP); snap.append(None); gaps += 1
        lines.extend(new[start:]); snap.extend([si] * (len(new) - start))
    return lines, snap, gaps


def load_qr2(files):
    """qr2-<sid>.txt files (one per phone boot, oldest first) -> (lines, sidx, times, gaps, from_boot, text) with the same meaning as merge():
    sidx[i] indexes `times` (when the part holding line i arrived; None for a gap marker); from_boot = the first session starts at the
    first byte the phone logged; text = a one-line coverage summary (bytes recovered of total, gaps, frames)."""
    import json
    sess = []
    for f in files:
        try:
            with open(f[:-4] + ".json") as fh:
                meta = json.load(fh)
        except FileNotFoundError:   # lint-silent: expected a capture without its .json sidecar has no metadata: the file mtime orders it
            meta = {}
        except (OSError, ValueError) as _err:
            print(f"qrmerge GAP: {f[:-4]}.json unreadable ({type(_err).__name__}: {_err}): ordered by file mtime", file=sys.stderr)
            meta = {}
        sess.append((meta.get("t_first") or os.path.getmtime(f), f, meta))
    lines, sidx, times, gaps, info = [], [], [], 0, []
    for _, f, meta in sorted(sess):
        with open(f, encoding="latin-1") as fh:
            new = fh.read().split("\n")[:-1]
        if lines:
            lines.append(GAP); sidx.append(None); gaps += 1
        runs = meta.get("line_times") or [[0, meta.get("t_last") or os.path.getmtime(f)]]
        starts = [r[0] for r in runs]
        j = 0
        for i, ln in enumerate(new):
            while j + 1 < len(starts) and starts[j + 1] <= i:
                j += 1
            lines.append(ln)
            if ln == GAP:
                sidx.append(None); gaps += 1
            else:
                sidx.append(len(times) + j)
        times += [r[1] for r in runs]
        info.append(meta)
    first = info[0] if info else {}
    from_boot = bool(info) and not first.get("lo") and not (first.get("gaps") and first["gaps"][0][0] == 0) and first.get("start", 0) == 0
    have, total = sum(m.get("bytes", 0) for m in info), sum(m.get("total", 0) for m in info)
    text = "QL2 %d session(s): %d of %d reduced bytes recovered (%d%%), %d block(s) of %d, %d frame(s) (%d duplicate)" % (
        len(info), have, total, 100 * have // max(1, total), sum(m.get("blocks", 0) for m in info), sum(m.get("hi", 0) - m.get("lo", 0) for m in info),
        sum(m.get("frames", 0) for m in info), sum(m.get("dups", 0) for m in info))
    if gaps:
        text += "; %d gap(s) %s" % (gaps, " ".join("@%d+%d" % tuple(g) for m in info for g in m.get("gaps", [])[:3]))
    return lines, sidx, times, gaps, from_boot, text


def expand(args):
    out = []
    for a in args:
        out += sorted(glob.glob(os.path.join(a, "qr2-*.txt")) or glob.glob(os.path.join(a, "qr-*.txt"))) if os.path.isdir(a) else [a]
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    dest = None
    if "-o" in a:
        i = a.index("-o"); dest = a[i + 1]; del a[i:i + 2]
    files = expand(a)
    if not files:
        sys.exit("usage: qrmerge.py DIR|FILE... [-o merged.txt]")
    q2 = [f for f in files if os.path.basename(f).startswith("qr2-")]
    if q2:
        lines, _s, _t, gaps, _b, cov = load_qr2(q2)
        print("qrmerge: " + cov, file=sys.stderr)
        files = [f for f in files if f not in q2]
        if files:
            sys.exit("qrmerge: do not mix qr2-* and qr-* files")
    else:
        lines, _snap, gaps = merge([open(f, encoding="latin-1").read() for f in files])
    text = "\n".join(lines) + "\n"
    if dest:
        open(dest, "w", encoding="latin-1").write(text)
    else:
        sys.stdout.write(text)
    print("qrmerge: %d %s, %d lines, %d gaps" % (len(files) or len(q2), "sessions" if q2 else "snapshots", len(lines), gaps), file=sys.stderr)
