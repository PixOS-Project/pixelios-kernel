#!/usr/bin/env python3
"""fcap-report: replay a run's frame captures on the host software rasterizer and summarise (sprint GPU 2026-11-09).

  python3 -I tools/fcap-report CAPDIR [--replay BIN] [--driver llvmpipe|softpipe] [--out DIR]

CAPDIR holds the *.fcap files fcap-pull fetched (and *.ftap scanout taps). Runs fcap_replay once over all *.fcap (they share blobs), converts
every ref/dev/diff PPM to one side-by-side PNG (device | software reference | diff x4, red = channel diff > 8/255), converts each .ftap to
PNGs named by the kernel clock, and prints one row per captured frame with the replay's metrics. The verdict is the replay's own
(MATCH iff px_gt8 <= 0.5 % and worst 32x32 tile mean <= 4/255; fixed, stated in fcap_replay.c).
Last line: RESULT: PASS fcap-report frames=N match=N | RESULT: FAIL ... | RESULT: ERROR ...
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import glob, json, os, struct, subprocess, sys

try:
    from PIL import Image
except ImportError:   # lint-silent: expected PIL absent under python3 -I; main() prints the SWEET GAP line for it
    Image = None


def self_test():
    import tempfile
    d = tempfile.mkdtemp()
    rc = main(["fcap-report", d, "--replay", "/bin/false"])   # no capture files: must FAIL loudly, never PASS on nothing
    ok = rc == 1
    print("RESULT: %s fcap-report self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if len(argv) < 2:
        print(__doc__); print("RESULT: ERROR fcap-report usage"); return 2
    cap = argv[1]
    replay = os.path.expanduser("~/pixelios-artifacts/fcap-host/fcap_replay")
    driver = "llvmpipe"; out = None
    i = 2
    while i < len(argv):
        if argv[i] == "--replay": replay = argv[i + 1]; i += 2
        elif argv[i] == "--driver": driver = argv[i + 1]; i += 2
        elif argv[i] == "--out": out = argv[i + 1]; i += 2
        else: print("RESULT: ERROR fcap-report unknown option " + argv[i]); return 2
    out = out or os.path.join(cap, "replay-" + driver)
    if Image is None:
        print("SWEET GAP: fcap-report: PIL not importable (python3 -I hides user site-packages): no side-by-sides, tap frames or panel comparison")
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(cap, "*.fcap")))
    rows = []
    rc = 0
    if files:
        rp = os.path.join(out, "report.jsonl")
        if os.path.exists(rp): os.remove(rp)
        p = subprocess.run([replay, "--driver", driver, "--out", out] + files, capture_output=True, text=True)
        open(os.path.join(out, "replay.log"), "w").write(p.stdout + p.stderr)
        last = [l for l in p.stdout.splitlines() if l.startswith("RESULT:")]
        print("replay:", last[-1] if last else "no RESULT line (rc %d): %s" % (p.returncode, (p.stdout + p.stderr)[-400:]))
        rc = p.returncode
        if os.path.exists(rp):
            rows = [json.loads(l) for l in open(rp) if l.strip()]
    panel_rows = side_and_panels(cap, out, rows) if Image else []
    print("%-44s %4s %-8s %8s %8s %6s %4s %4s %4s %s" % ("capture", "seq", "verdict", "gt8%", "tile", "max", "inh", "cpu", "err", "gaps"))
    nm = 0
    for r in rows:
        if r.get("verdict") == "MATCH" and not r.get("errors"): nm += 1
        print("%-44s %4s %-8s %8.3f %8.2f %6s %4s %4s %4s %s" % (r["file"][-44:], r["seq"], r.get("verdict"), r.get("px_gt8_pct", -1), r.get("worst_tile32_mean", -1),
              r.get("max_abs", "-"), r.get("inherited", "-"), r.get("cpu_views", "-"), r.get("errors", "-"), (r.get("gaps") or "")[:80]))
    for lab, v, pct, worst, seq in panel_rows:
        print("PANEL %-38s vs f%-5s %-9s gt8 %.3f%% tile %.2f" % (lab, seq, v, pct, worst))
    npm = sum(1 for p_ in panel_rows if p_[1] == "MATCH")
    if panel_rows: print("PANEL: %d of %d panel snapshots match the captured frame (the display path agrees)" % (npm, len(panel_rows)))
    if panel_rows and npm != len(panel_rows): nm = -1   # a display-path mismatch fails the report too
    if not rows:
        print("RESULT: FAIL fcap-report no frames (files %d, replay rc %d)" % (len(files), rc)); return 1
    print("RESULT: %s fcap-report frames=%d match=%d out=%s" % ("PASS" if nm == len(rows) else "FAIL", len(rows), nm, out))
    return 0 if nm == len(rows) else 1



def side_and_panels(cap, out, rows):
    """side-by-side PNGs, tap frames and the panel comparison (PIL present)."""
    for r in rows:
        base = os.path.join(out, "%s-f%u" % (r["file"], r["seq"]))
        try:
            ims = [Image.open(base + s + ".ppm") for s in ("-dev", "-ref", "-diff")]
        except OSError as e:
            print("SWEET GAP: fcap-report: no side-by-side for %s: %s" % (base, e), file=sys.stderr)
            continue
        w, h = ims[0].size
        s = Image.new("RGB", (w * 3 + 20, h), (40, 40, 40))
        for k, im in enumerate(ims): s.paste(im, (k * (w + 10), 0))
        s.thumbnail((1200, 1200))
        s.save(base + "-side.png")
    for t in sorted(glob.glob(os.path.join(cap, "*.ftap"))):
        d = open(t, "rb").read()
        if d[:8] != b"FTAP0001": print("tap %s: bad magic" % t); continue
        o, k = 8, 0
        td = t + ".png.d"; os.makedirs(td, exist_ok=True)
        while o + 16 <= len(d):
            ts, w, h = struct.unpack_from("<QII", d, o); o += 16
            n = w * h * 4
            im = Image.frombuffer("RGBA", (w, h), d[o:o + n], "raw", "BGRA", 0, 1).convert("RGB")
            im.save(os.path.join(td, "%03d-%d.png" % (k, ts)))
            o += n; k += 1
        print("tap %s: %d frames -> %s" % (os.path.basename(t), k, td))
    return panel_rows_of(cap, out, rows)


def panel_rows_of(cap, out, rows):
    # patch 0276 panel snapshots: the bytes the display scans, compared with the device RESULT of the capture with the same label (last frame),
    # cropped by the logical offset in the panel header; same fixed tolerance
    panel_rows = []
    for pf in sorted(glob.glob(os.path.join(cap, "snap-*.raw"))):
        d = open(pf, "rb").read()
        mg, w, h, rb, ox, oy, pres, slot = struct.unpack_from("<8I", d, 0)
        if mg != 0x484e5053: print("panel %s: bad magic" % pf); continue
        lab = d[32:80].split(b"\0")[0].decode("ascii", "replace")
        pan = Image.frombuffer("RGBA", (w, h), d[80:80 + w * h * 4], "raw", "BGRA", 0, 1).convert("RGB")
        cands = [r for r in rows if r["file"].endswith(lab)]
        if not cands:   # e.g. the real-timing App Library pass (recorder off): shown for the eye, not compared
            pan.save(os.path.join(out, "panel-%s.png" % lab)); print("PANEL %-38s standalone (no capture with this label) -> panel-%s.png" % (lab, lab)); continue
        # the snapshot is the panel (crop of the logical frame at ox,oy, 2w x 2h) halved by a 2x2 box: do the same to the captured frame. The
        # panel copy runs at 30 Hz behind the GPU, so it may show any frame of the capture (K = 2): the best-matching one is the comparison
        # (gputest-prof1 c11/c14: the panel held the frame before the Clock icon's second-hand tick / the caret blink).
        best = None
        for r in cands:
            dev = Image.open(os.path.join(out, "%s-f%u-dev.ppm" % (r["file"], r["seq"]))).crop((ox, oy, ox + 2 * w, oy + 2 * h)).reduce(2)
            a_, b_ = pan.tobytes(), dev.tobytes()
            gt8 = 0
            tw, th = (w + 31) // 32, (h + 31) // 32
            tsum = [0.0] * (tw * th); tn = [0] * (tw * th)
            for i in range(0, len(a_), 3):
                m = max(abs(a_[i] - b_[i]), abs(a_[i + 1] - b_[i + 1]), abs(a_[i + 2] - b_[i + 2]))
                px = i // 3; t = (px // w // 32) * tw + (px % w) // 32
                tsum[t] += m; tn[t] += 1; gt8 += m > 8
            worst = max(tsum[t] / tn[t] for t in range(len(tsum)) if tn[t])
            pct = 100.0 * gt8 / (w * h)
            if best is None or (pct, worst) < (best[0], best[1]): best = (pct, worst, r, dev)
        pct, worst, r, dev = best
        v = "MATCH" if pct <= 0.5 and worst <= 4.0 else "MISMATCH"
        panel_rows.append((lab, v, pct, worst, r["seq"]))
        s2 = Image.new("RGB", (w * 2 + 10, h), (40, 40, 40)); s2.paste(pan, (0, 0)); s2.paste(dev, (w + 10, 0)); s2.thumbnail((1000, 1100))
        s2.save(os.path.join(out, "panel-%s-side.png" % lab))
    return panel_rows


if __name__ == "__main__":
    sys.exit(main(sys.argv))
