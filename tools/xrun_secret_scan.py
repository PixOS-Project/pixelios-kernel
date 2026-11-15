#!/usr/bin/env python3
"""xrun_secret_scan: fail a run when a secret from the kernel .env reached any file of its run dir (TP-051, hard rule 4).

CLI is tools/xrun-secret-scan (this module is its twin; tools/xrun_report.py imports `report_step`).
  tools/xrun-secret-scan RUN_DIR [--env FILE] [--keys PIXOS_WIFI_PSK[,KEY2 ...]] [--ssid-key PIXOS_WIFI_SSID]
Prints per hit only `LEAK: <relative file>:<line> key=<KEY NAME> form=<raw|hex|b64|pmk-raw|pmk-hex>` (first hit per file/key/form), counted
`SKIP:` lines (symlinks, non-regular files, files > 512 MiB) and `UNREADABLE:` lines, then `RESULT: PASS no secret in N files (skipped=S)` /
`RESULT: FAIL leaks=K files=M` / `RESULT: ERROR <why>` (exit 0/1/2). A missing env file, key, SSID or a too-short PSK is an ERROR, never a PASS.
The values are read from the env file inside this process only; nothing is written, cached or printed (exceptions are reduced to class name +
file name). Forms searched per value: raw, hex (lower/upper, plain and with ' ' ':' '-' separators), base64 (the characters that do not depend
on the neighbouring bytes, for all three alignments); for a key ending in _PSK also the 32-byte WPA2 PMK = PBKDF2-HMAC-SHA1(psk, ssid, 4096, 32):
raw + hex (a pmk iovar dump carries the PMK, not the passphrase).
"""
import argparse, base64, glob, hashlib, os, re, stat, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import envfile  # noqa: E402

MAX_FILE = 512 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
SEPS = (b" ", b":", b"-")
FORM_ORDER = ("raw", "hex", "b64", "pmk-raw", "pmk-hex")
PSK_KEY = "PIXOS_WIFI_PSK"
SSID_KEY = "PIXOS_WIFI_SSID"


class ScanError(Exception):
    """Carries a message that never contains a secret (key NAMES, counts and class names only)."""


def parse_env(path):
    """KEY=VALUE lines (optional `export `, optional matching quotes, no escapes, no inline comments). Only called on the scanner's own env file."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except Exception as e:      # message may echo file bytes: class name only
        raise ScanError("cannot read env file %s (%s)" % (os.path.basename(path), type(e).__name__)) from None
    d = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, sep, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or not k:
            continue
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        d[k] = v
    return d


def _hexforms(b):
    lo = b.hex().encode()
    out = [lo, lo.upper()]
    for s in SEPS:
        j = s.join(lo[i:i + 2] for i in range(0, len(lo), 2))
        out += [j, j.upper()]
    return out


def _b64forms(b):
    """Base64 text that is the same wherever the value starts in a 3-byte group: encode with p leading pad bytes, keep only the chars whose
    6 bits lie wholly inside the value (drops the unstable first/last chars)."""
    out = []
    for p in (0, 1, 2):
        enc = base64.b64encode(b"\0" * p + b).decode()
        lo, hi = -(-8 * p // 6), 8 * (p + len(b)) // 6
        if hi > lo:
            out.append(enc[lo:hi].encode())
    return out


def build_needles(env, keys, ssid_key=SSID_KEY):
    """-> [(needle bytes, key name, form)]; raises ScanError (no values in the text)."""
    needles = []
    for k in keys:
        v = env.get(k)
        if v is None:
            raise ScanError("key %s missing in the env file" % k)
        if v == "":
            raise ScanError("key %s is empty" % k)
        vb = v.encode("utf-8")
        pmk = None
        if k.endswith("_PSK"):
            if re.fullmatch(r"[0-9A-Fa-f]{64}", v):     # IEEE 802.11i: 64 hex digits are the PMK itself, 8..63 ASCII chars a passphrase
                pmk = bytes.fromhex(v)
            elif not 8 <= len(vb) <= 63:
                raise ScanError("key %s is not a valid WPA2 passphrase (need 8..63 chars or 64 hex digits)" % k)
            else:
                ssid = env.get(ssid_key)
                if not ssid:
                    raise ScanError("%s needs %s (PMK derivation) but it is missing or empty" % (k, ssid_key))
                sb = ssid.encode("utf-8")
                if len(sb) > 32:
                    raise ScanError("%s is longer than 32 bytes" % ssid_key)
                pmk = hashlib.pbkdf2_hmac("sha1", vb, sb, 4096, 32)
        needles.append((vb, k, "raw"))
        needles += [(n, k, "hex") for n in _hexforms(vb)]
        needles += [(n, k, "b64") for n in _b64forms(vb)]
        if pmk is not None:
            needles.append((pmk, k, "pmk-raw"))
            needles += [(n, k, "pmk-hex") for n in _hexforms(pmk)]
    return list(dict.fromkeys(needles))


def scan_file(path, needles, chunk=CHUNK):
    """-> {(key, form): line number (1-based)} of the first hit per key/form. Streams with overlap so a hit across a chunk edge is found."""
    keep = max(len(n) for n, _, _ in needles) - 1
    found = {}          # (key, form) -> (byte offset, line)
    buf, base, nl_before = b"", 0, 0      # buf = retained tail + new data; base = file offset of buf[0]; nl_before = newlines before buf[0]
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            buf += data
            for n, k, form in needles:
                i = buf.find(n)
                if i >= 0 and ((k, form) not in found or base + i < found[(k, form)][0]):
                    found[(k, form)] = (base + i, nl_before + buf.count(b"\n", 0, i) + 1)
            cut = max(0, len(buf) - keep)
            nl_before += buf.count(b"\n", 0, cut)
            base += cut
            buf = buf[cut:]
    return {kf: v[1] for kf, v in found.items()}


def scan(run_dir, env_path, keys, ssid_key=SSID_KEY, chunk=CHUNK, max_file=MAX_FILE):
    """-> dict(status PASS|FAIL|ERROR, leaks [(rel, line, key, form)], files, skipped, skips [text], unreadable [text], why)."""
    r = {"status": "ERROR", "leaks": [], "files": 0, "skipped": 0, "skips": [], "unreadable": [], "why": ""}
    try:
        if not os.path.isdir(run_dir):
            raise ScanError("run dir %s is not a directory" % run_dir)
        if not env_path or not os.path.isfile(env_path):
            raise ScanError("env file not found (%s)" % (env_path or "none resolved"))
        needles = build_needles(parse_env(env_path), keys, ssid_key)
        for root, dirs, files in os.walk(run_dir, followlinks=False):
            dirs.sort()
            for dn in list(dirs):
                if os.path.islink(os.path.join(root, dn)):
                    dirs.remove(dn)
                    r["skips"].append("SKIP: %s symlink to a directory not followed" % os.path.relpath(os.path.join(root, dn), run_dir))
            for fn in sorted(files):
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, run_dir)
                try:
                    st = os.lstat(p)
                    if not stat.S_ISREG(st.st_mode):
                        r["skips"].append("SKIP: %s not a regular file (symlink/special) not followed" % rel)
                    elif st.st_size > max_file:
                        r["skips"].append("SKIP: %s %d bytes > %d limit" % (rel, st.st_size, max_file))
                    else:
                        r["files"] += 1
                        for (k, form), line in scan_file(p, needles, chunk).items():
                            r["leaks"].append((rel, line, k, form))
                except ScanError:
                    raise
                except Exception as e:      # message could carry file bytes: class + file name only
                    r["unreadable"].append("UNREADABLE: %s %s" % (rel, type(e).__name__))
        r["skipped"] = len(r["skips"])
        r["leaks"].sort(key=lambda x: (x[0], x[2], FORM_ORDER.index(x[3])))
        r["status"] = "FAIL" if r["leaks"] else "ERROR" if r["unreadable"] else "PASS"
        if r["status"] == "ERROR":
            r["why"] = "%d file(s) unreadable, cannot prove the run is clean" % len(r["unreadable"])
    except ScanError as e:
        r["why"] = str(e)
    except Exception as e:
        r["why"] = "unexpected %s" % type(e).__name__
    return r


def render(r):
    out = ["LEAK: %s:%d key=%s form=%s" % x for x in r["leaks"]] + r["skips"] + r["unreadable"]
    if r["status"] == "PASS":
        out.append("RESULT: PASS no secret in %d files (skipped=%d)" % (r["files"], r["skipped"]))
    elif r["status"] == "FAIL":
        out.append("RESULT: FAIL leaks=%d files=%d" % (len(r["leaks"]), len({x[0] for x in r["leaks"]})))
    else:
        out.append("RESULT: ERROR %s" % r["why"])
    return "\n".join(out) + "\n"


def report_step(run_dir):
    """For xrun_report: the scan dict, or a status-SKIP dict (never None, rule 8) when the kernel .env defines no non-empty PIXOS_WIFI_PSK."""
    skip = {"status": "SKIP", "leaks": [], "files": 0, "skipped": 0, "skips": [], "unreadable": [], "why": "no PIXOS_WIFI_PSK in the kernel .env, nothing to scan for"}
    p = envfile.find()
    if not p:
        return skip
    try:
        env = parse_env(p)
    except ScanError as e:
        return {"status": "ERROR", "leaks": [], "files": 0, "skipped": 0, "skips": [], "unreadable": [], "why": str(e)}
    if not env.get(PSK_KEY):
        return skip
    r = scan(run_dir, p, [PSK_KEY])
    r["left"] = leftover_images(os.environ.get("XRUN_SHIM_OUT") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shim", "out"), p)
    if r["status"] == "PASS":
        r["why"] = "skipped=%d" % r["skipped"]      # the run summary prints it behind the file count; the details are in tools/xrun-secret-scan RUN_DIR
    return r


def leftover_images(shim_out, env_path):
    """T-2952 (SPEC-WIFI TRANSPORT s.3 item 5): the boot images shim/out/shim-boot-avb.img* are NOT in the run dir; an image built with the WLANCRED blob
    holds the PSK raw. -> sorted paths of those files that contain any needle form of the PSK (a clean image is no GAP: normal builds leave images
    there too). A file that cannot be read is returned with a `?` suffix (cannot prove it clean, rule 8)."""
    out = []
    try:
        needles = build_needles(parse_env(env_path), [PSK_KEY])
    except ScanError:
        return out      # report_step already reports the unusable env
    for p in sorted(glob.glob(glob.escape(shim_out) + "/shim-boot-avb.img*")):
        try:
            st = os.lstat(p)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                continue
            if scan_file(p, needles):
                out.append(p)
        except Exception:   # message could carry file bytes: class only, the path is enough
            out.append(p + "?")
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="xrun-secret-scan", description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--env", help="env file (default: the kernel clone .env via tools/envfile.py)")
    ap.add_argument("--keys", default=PSK_KEY, help="comma separated key names (default %(default)s)")
    ap.add_argument("--ssid-key", default=SSID_KEY)
    a = ap.parse_args(argv)     # argparse exits 2 on a usage error
    r = scan(a.run_dir, a.env or envfile.find(), [k for k in a.keys.split(",") if k], a.ssid_key)
    sys.stdout.write(render(r))
    return {"PASS": 0, "FAIL": 1}.get(r["status"], 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
