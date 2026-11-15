"""Camera-side QR decoding shared by tools/qr-log.py and tools/qrsim.py.

A QR frame is found first (OpenCV detectMulti: it locates codes whose modules are too small to decode), then each code is cut
out, rectified or magnified x4 and sharpened, and decoded with both OpenCV decoders. That reads codes at ~2.7 camera pixels per
module where decoding the whole image needs ~3.3 (tools/qrsim.py, docs/research/24-qr-throughput.md). Whole-image decoding at
several scales stays as the fallback."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def detectors(cv2):
    # the Aruco-based detector reads codes the classic one misses (and vice versa)
    return [d() for d in (getattr(cv2, "QRCodeDetectorAruco", None), cv2.QRCodeDetector) if d]


def _single(cv2, det, im):
    try:
        t, _pts, _ = det.detectAndDecode(im)
    except cv2.error:        # degenerate candidate (zero-area contour): not a QR, move on   # lint-silent: expected a degenerate candidate is not a QR: skipped (comment)
        return None
    return t or None


def _multi(cv2, det, im):
    try:
        ok, infos, _pts, _ = det.detectAndDecodeMulti(im)
    except cv2.error:   # lint-silent: expected a degenerate candidate yields no multi-code result
        return []
    return [t for t in infos if t] if ok else []


def sharpen(cv2, im, sigma=2.0):
    return cv2.addWeighted(im, 2.0, cv2.GaussianBlur(im, (0, 0), sigma), -1.0, 0)


def find_quads(cv2, np, img, dets, want):
    """Corner quads (4 x 2, float) of the QR codes in img, biggest first, duplicates (one code seen by both detectors) removed."""
    quads = []
    for det in dets:
        try:
            ok, pts = det.detectMulti(img)
        except cv2.error:   # lint-silent: expected a degenerate candidate yields no quad
            continue
        for q in (pts if ok and pts is not None else []):
            q = q.reshape(4, 2)
            c, side = q.mean(axis=0), np.linalg.norm(q[1] - q[0]) + 1e-6
            if not any(np.linalg.norm(c - o.mean(axis=0)) < 0.3 * side for o in quads):
                quads.append(q)
        if len(quads) >= want:
            break
    quads.sort(key=lambda q: -abs(float((q[1] - q[0])[0] * (q[3] - q[0])[1] - (q[1] - q[0])[1] * (q[3] - q[0])[0])))
    return quads


def rectify(cv2, np, img, q):
    """The code straightened to a square of 3x its camera size, plus a quiet zone."""
    S = int(round(float(np.linalg.norm(q[1] - q[0])) * 3))
    pad = int(S * 0.08)
    dst = np.float32([[pad, pad], [pad + S, pad], [pad + S, pad + S], [pad, pad + S]])
    M = cv2.getPerspectiveTransform(q.astype(np.float32), dst)
    return cv2.warpPerspective(img, M, (S + 2 * pad, S + 2 * pad), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def magnify(cv2, np, img, q, k=4):
    c = q.mean(axis=0)
    r = max(float(np.ptp(q[:, 0])), float(np.ptp(q[:, 1]))) * 0.75
    x0, y0 = int(max(0, c[0] - r)), int(max(0, c[1] - r))
    x1, y1 = int(min(img.shape[1], c[0] + r)), int(min(img.shape[0], c[1] + r))
    return cv2.resize(img[y0:y1, x0:x1], None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)


def decode_all(cv2, img, dets, want=1, accept=lambda t: True, fast=False):
    """Distinct texts in img that `accept` (stops once `want` are in hand). fast: ROI route only."""
    import numpy as np
    found = []
    for q in find_quads(cv2, np, img, dets, want)[:max(2, want + 1)]:
        for make in (rectify, magnify):
            im = sharpen(cv2, make(cv2, np, img, q))
            hit = None
            for det in dets:
                t = _single(cv2, det, im)
                if t and accept(t):
                    hit = t
                    break
            if hit:
                if hit not in found:
                    found.append(hit)
                break
        if len(found) >= want:
            return found
    if fast:
        return found
    for s, interp in ((1.0, None), (0.5, cv2.INTER_AREA), (0.35, cv2.INTER_AREA), (2.0, cv2.INTER_CUBIC)):
        im = img if interp is None else cv2.resize(img, None, fx=s, fy=s, interpolation=interp)
        for det in dets:
            for t in ([_single(cv2, det, im)] if want == 1 else _multi(cv2, det, im)):
                if t and t not in found and accept(t):
                    found.append(t)
            if len(found) >= want:
                return found
    return found


def decode_whole(cv2, img, dets, accept=lambda t: True):
    """The old route (QL1 era): decode the whole image at 1x / 0.5x / 0.35x. Kept for comparison in tools/qrsim.py."""
    found = []
    for s in (1.0, 0.5, 0.35):
        im = img if s == 1.0 else cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        for det in dets:
            t = _single(cv2, det, im)
            if t and accept(t):
                return [t]
    return found
