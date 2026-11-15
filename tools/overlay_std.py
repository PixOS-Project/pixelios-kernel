#!/usr/bin/env python3
"""overlay_std: every boot starts from the STANDARD ROOT plus its own overlay (user 2026-11-09: one standard root, no root ping-pong).

Why: dev overlays (xnu patch 0117, tools/mkdevoverlay.py) and hot-reload pushes (tools/devsh push) write files onto userdata and those
files STAY. One thread's launchd.plist jobs or dylib then ran in another thread's boot (GPU log 2026-11-08: the Wi-Fi jobs blanked
nav rep 0), which led to every thread flashing its own root (6 full flashes on 2026-11-09). This tool keeps a ledger of every path an
overlay or a push wrote since the last userdata flash and puts the standard copy of each back in the next boot's overlay.

  overlay_std.py prepare --overlay DIR|- --out DIR --label L   build the boot overlay: standard copies of the ledger paths and of
                       ALWAYS_RESET (launchd.plist) that this run's overlay does not carry, then the run's overlay on top. Prints
                       `OVERLAY STD: ...` (what was reset, the residue), `OVERLAY STD REV: R`, `OVERLAY STD DIR: DIR|none`.
  overlay_std.py check --rev R                                 exit 0 when the ledger/flash state is still R (xrun calls it under the lock), 1 = changed
  overlay_std.py record --dir DIR --label L --run RUNDIR       after the boot: if the kernel applied the archive (`SWEET DEV: copy N entries`
                       in RUNDIR/usbnet.log or merged.log), paths equal to the standard copy leave the ledger, the others enter it
  overlay_std.py note PATH --by WHO                             one path written outside an overlay (devsh push): into the ledger
  overlay_std.py boot --label L --run RUNDIR                   count one boot of the standard root (xrun, every booted run); prepare prints
                       `OVERLAY STD BOOTS: N` and, for the first FIRST_BOOTS boots after a flash, `OVERLAY STD FIRST BOOT: ...` (not comparable)
                       and, when the run's overlay ships launchd.plist, `OVERLAY STD JOBS: N added (...), M dropped (...)` vs the standard root
  overlay_std.py status                                         the standard root and the ledger

Standard root = the newest COMPLETED line of flashed-userdata.txt (`<sha256> <utc> <image path>`). An in-progress/failed flash
(`flashing ...`), an Android erase or an unreadable image = state unknown: prepare passes the run's overlay through unchanged and
prints `OVERLAY STD GAP: ...` (rule 7). Standard copies come out of the image with tools/hfs_read.py (7z 25.01 reads 0 bytes of
launchd.plist from these images, observed 2026-11-09) into a cache <artifacts>/std-files/<sha>/.
STANDARD EXTRAS (<artifacts>/std-extras/manifest.tsv: `/abs/path<TAB>sha256<TAB>intents<TAB>why`, the file at std-extras/<path>): files
that belong to the standard state but are not in the root image, e.g. the trunk /usr/lib/libsweetgl.dylib that `xrun --intent gpu`
loads through DYLD_INSERT_LIBRARIES (GPU thread 2026-11-09: no other run loads it). An extra is restored only in a run whose intent is
listed (it is 22 MB: an uncompressed overlay of 21 MiB or more does not boot, tools/mkdevoverlay.py); prepare then prints
`OVERLAY STD COMPRESS: yes` and xrun adds --compress. Other runs leave a changed extra in the ledger, untouched. A ledger path that does not exist in the standard root (a file an overlay ADDED, e.g. libsweetgl.dylib)
is removed with a WHITEOUT entry (xnu patch 0280: an empty <path>.sweetwhiteout in the boot tree) when the booted kernel has 0280
(prepare --kernel K: the image holds 0280's refusal text) and the path is one 0280 may remove (/usr/local/, /private/var/dev/,
/usr/lib/libsweetgl.dylib); otherwise it is printed as RESIDUE on every prepare until the next flash.
Ledger: <artifacts>/overlay-ledger/<sha12>-<flash utc>.json (one per flash; a new flash starts empty; a new flash also has none of the
extras, so the first run of a listed intent brings them). Env: ARTIFACTS, FLASH_STATE (as tools/fflash-userdata.sh). Stdlib only.
"""
import argparse, fcntl, filecmp, hashlib, json, os, plistlib, re, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfs_read  # noqa: E402

ART = os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts")
STATE = os.environ.get("FLASH_STATE") or os.path.join(ART, "flashed-userdata.txt")
LEDGERS = os.path.join(ART, "overlay-ledger")
CACHE = os.path.join(ART, "std-files")
EXTRAS = os.path.join(ART, "std-extras")
COMPRESS_AT = 8 << 20   # bytes of overlay data above which xrun packs it compressed (the 21 MiB boot limit, mkdevoverlay.py)
FIRST_BOOTS = 2   # boots 1..2 after a flash are flagged (fcap7 broke on the boot after a settle boot) [obs 2026-11-09]
ALWAYS_RESET = ("/System/Library/xpc/launchd.plist",)   # the job list: every overlay that adds a job rewrites it (observed 2026-11-08)
LINE = re.compile(r"^([0-9a-f]{64}) (\S+) (\S+)$")


def say(msg):
    print(msg, flush=True)


def std_root():
    """(sha, utc, image) of the root on the phone, or (None, why)."""
    try:
        lines = [l.strip() for l in open(STATE) if l.strip()]
    except OSError as e:
        return None, "no flash state %s (%s)" % (STATE, e.strerror)
    if not lines:
        return None, "flash state %s is empty" % STATE
    m = LINE.match(lines[-1])
    if not m:
        return None, "newest flash line is not a completed iOS root flash: %r" % lines[-1][:120]
    sha, utc, img = m.groups()
    if not os.path.isfile(img):
        return None, "the flashed image %s is gone (cannot read standard copies)" % img
    return (sha, utc, img), None


def ledger_path(root):
    return os.path.join(LEDGERS, "%s-%s.json" % (root[0][:12], root[1].replace(":", "")))


def load(root):
    try:
        with open(ledger_path(root)) as f:
            led = json.load(f)
        led.setdefault("extras", {})
        return led
    except FileNotFoundError:   # lint-silent: expected no overlay or push since this flash: an empty ledger
        return {"root": root[0], "flashed": root[1], "paths": {}, "extras": {}, "boots": []}


def save(root, led):
    os.makedirs(LEDGERS, exist_ok=True)
    p = ledger_path(root)
    tmp = p + ".%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(led, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


class Locked:
    """serialise ledger read-modify-write between parallel xrun/devsh processes."""
    def __enter__(self):
        os.makedirs(LEDGERS, exist_ok=True)
        self.f = open(os.path.join(LEDGERS, ".lock"), "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        self.f.close()


def rev(root, led):
    """identity of what a prepared overlay assumed: the flash and the ledger content."""
    if root is None:
        return "unknown"
    h = hashlib.sha256(json.dumps([led.get("paths", {}), led.get("extras", {})], sort_keys=True).encode()).hexdigest()[:16]
    return "%s-%s-%s" % (root[0][:12], root[1], h)


def extras():
    """{path: (file, sha, intents)} of the standard extras; a bad line or a file whose sha differs is an error (never a silent skip)."""
    out = {}
    try:
        lines = open(os.path.join(EXTRAS, "manifest.tsv")).read().splitlines()
    except FileNotFoundError:   # lint-silent: expected no extras defined: the root image alone is the standard state
        return out
    for l in lines:
        if not l.strip() or l.startswith("#"):
            continue
        f = l.split("\t")
        if len(f) < 3 or not f[0].startswith("/"):
            raise SystemExit("OVERLAY STD ERROR: bad line in %s/manifest.tsv: %r" % (EXTRAS, l[:120]))
        src = os.path.join(EXTRAS, f[0].lstrip("/"))
        try:
            got = hashlib.sha256(open(src, "rb").read()).hexdigest()
        except OSError as e:
            raise SystemExit("OVERLAY STD ERROR: extra %s missing at %s (%s)" % (f[0], src, e.strerror))
        if got != f[1]:
            raise SystemExit("OVERLAY STD ERROR: extra %s at %s has sha256 %s, manifest says %s" % (f[0], src, got[:16], f[1][:16]))
        out[f[0]] = (src, f[1], set(x for x in f[2].split(",") if x))
    return out


def std_copy(root, path, ext=None):
    """local file with the standard copy of PATH (an extra first, else the root image), or None when the standard state has none."""
    ext = extras() if ext is None else ext
    if path in ext:
        return ext[path][0]
    base = os.path.join(CACHE, root[0][:16])
    dst = os.path.join(base, "root" + path)
    absent = os.path.join(base, ".absent")
    if os.path.isfile(dst):
        return dst
    try:
        if path in open(absent).read().split("\n"):
            return None
    except FileNotFoundError:   # lint-silent: expected no path was found absent yet for this root
        pass
    try:
        data = hfs_read.HFS(root[2]).read_file(path)
    except (hfs_read.Bad, OSError) as e:
        raise SystemExit("OVERLAY STD ERROR: cannot read %s from %s: %s" % (path, root[2], e))
    if data is None:
        os.makedirs(base, exist_ok=True)
        with open(absent, "a") as f:
            f.write(path + "\n")
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".%d" % os.getpid()
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o755 if path.startswith(("/usr/lib/", "/usr/local/bin/")) else 0o644)
    os.replace(tmp, dst)
    return dst


WH_SUFFIX = ".sweetwhiteout"
WH_ALLOW = (("/usr/local/", "D"), ("/private/var/dev/", "D"), ("/usr/lib/libsweetgl.dylib", "X"))   # = sd_wh_allow[] of patch 0280
WH_MARK = b"whiteout outside /usr/local/"                                                         # a string only 0280 puts in the kernel


def kernel_whiteouts(kernel):
    if not kernel:
        return False
    try:
        with open(kernel, "rb") as f:
            return WH_MARK in f.read()
    except OSError as e:
        say("OVERLAY STD GAP: cannot read the kernel %s (%s): no whiteouts this boot" % (kernel, e.strerror))
        return False


def wh_allowed(p):
    return any((p.startswith(x) and len(p) > len(x)) if k == "D" else p == x for x, k in WH_ALLOW)


def packer():
    """the LOCAL tools/mkdevoverlay.py (this checkout's version decides what the image build accepts), or None (GAP)."""
    try:
        import mkdevoverlay
        return mkdevoverlay
    except ImportError as e:
        say("OVERLAY STD GAP: tools/mkdevoverlay.py not importable here (%s): no path check against the packer" % e)
        return None


def files(d):
    out = []
    for top, _, names in os.walk(d):
        for n in names:
            out.append("/" + os.path.relpath(os.path.join(top, n), d))
    return sorted(out)


def prepare(overlay, out, label, intent="", kernel=None):
    root, why = std_root()
    own = files(overlay) if overlay != "-" else []
    if root is None:
        say("OVERLAY STD GAP: %s: no reset to the standard root this boot (earlier overlays' files may still be on userdata)" % why)
        say("OVERLAY STD REV: unknown")
        say("OVERLAY STD DIR: %s" % ("none" if overlay == "-" else overlay))
        return 0
    with Locked():
        led = load(root)
    ext = extras()
    mine = {p for p, (_, sha, intents) in ext.items() if intent in intents and led["extras"].get(p) != sha}
    skip = {p for p in ext if intent not in ext[p][2]}   # an extra of another intent: never in this run's archive (size), stays in the ledger
    want = sorted(((set(led["paths"]) | set(ALWAYS_RESET)) - skip | mine) - set(own))
    reset, residue, removed, skipped = [], [], [], []
    mk = packer()
    wh = kernel_whiteouts(kernel) and mk is not None and hasattr(mk, "WHITEOUT_SUFFIX")   # an older packer has no whiteout
    if mk is not None:   # version skew (2026-11-09): a path a NEWER checkout put in the ledger that this packer refuses would fail the build
        keep = []
        for p in want:
            why = mk.path_check(p)
            (skipped.append((p, why)) if why else keep.append(p))
        want = keep
    os.makedirs(out, exist_ok=True)
    for p in want:
        src = std_copy(root, p, ext)
        if src is None:
            if wh and wh_allowed(p):
                mark = os.path.join(out, p.lstrip("/") + WH_SUFFIX)
                os.makedirs(os.path.dirname(mark), exist_ok=True)
                open(mark, "wb").close()
                removed.append(p)
            else:
                residue.append(p)
            continue
        dst = os.path.join(out, p.lstrip("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        reset.append(p)
    if overlay != "-":
        for p in own:
            dst = os.path.join(out, p.lstrip("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(overlay, p.lstrip("/")), dst)
    changed = [p for p in reset if p in led["paths"]]
    say("OVERLAY STD: root %s (flashed %s): %d standard cop%s (%d undoing earlier writes%s), own overlay %d file(s)" % (
        root[0][:12], root[1], len(reset), "y" if len(reset) == 1 else "ies", len(changed),
        (": " + ", ".join("%s by %s" % (p, led["paths"][p].get("by", "?")) for p in changed[:6])) if changed else "", len(own)))
    for p, why in skipped:
        say("OVERLAY STD GAP: %s is in the ledger but this checkout's mkdevoverlay refuses it (%s): not reset this boot; pull trunk" % (p, why))
    if removed:
        say("OVERLAY STD REMOVE: %d file(s) an overlay or push added go away this boot (patch 0280 whiteout): %s" % (len(removed), ", ".join(removed[:8])))
    if residue:
        say("OVERLAY STD RESIDUE: %d file(s) an overlay or push ADDED stay on userdata until the next flash (no delete in the overlay format): %s" % (
            len(residue), ", ".join("%s (by %s)" % (p, led["paths"].get(p, {}).get("by", "?")) for p in residue[:8])))
    held = sorted(p for p in skip if p in led["paths"] or led["extras"].get(p) != ext[p][1])
    if held:
        say("OVERLAY STD: extra(s) of another intent not restored in this %s run (they only matter there): %s" % (intent or "plain", ", ".join(held)))
    if overlay != "-" and ALWAYS_RESET[0] in own:
        jobs_diff(root, overlay, ext)
    nb = len(led.get("boots", []))
    uncounted = "boots" not in led or led.get("boots_uncounted_before")
    say("OVERLAY STD BOOTS: %s%d earlier boot(s) of this root since its flash %s" % ("at least " if uncounted else "", nb, root[1]))
    if nb < FIRST_BOOTS and not uncounted:
        say("OVERLAY STD FIRST BOOT: boot %d after the flash: iOS migration/first-launch work runs now (observed 2026-11-09: gputest-fcap5 migration "
            "screen all session, fcap7 rep 0 broken); timings of this boot are not comparable with later boots" % (nb + 1))
    total = sum(os.path.getsize(os.path.join(top, n)) for top, _, ns in os.walk(out) for n in ns)
    if total > COMPRESS_AT:
        say("OVERLAY STD COMPRESS: yes (%d bytes)" % total)
    say("OVERLAY STD REV: %s" % rev(root, led))
    say("OVERLAY STD DIR: %s" % (out if (reset or own) else "none"))
    return 0


def jobs(path):
    """{label} of the LaunchDaemons in a launchd.plist (binary or XML), or None when it cannot be read (printed as a GAP by the caller)."""
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
        return {(v.get("Label") if isinstance(v, dict) else None) or k for k, v in d.get("LaunchDaemons", {}).items()}
    except (OSError, plistlib.InvalidFileException, ValueError, AttributeError) as e:
        say("OVERLAY STD GAP: launchd.plist %s not readable as a plist (%s): job diff skipped" % (path, e))
        return None


def jobs_diff(root, overlay, ext):
    """fast-loop 2026-11-09: an overlay that ships its own launchd.plist (copied from an older root) silently adds/drops jobs
    (gputest-fcap9: com.pixos.btapp-launch from a copied plist opened Preferences in the unlock window). Name them every boot."""
    mine = os.path.join(overlay, ALWAYS_RESET[0].lstrip("/"))
    std = std_copy(root, ALWAYS_RESET[0], ext)
    if std is None:
        say("OVERLAY STD GAP: no standard launchd.plist to diff the overlay's job list against")
        return
    a, b = jobs(std), jobs(mine)
    if a is None or b is None:
        return
    add, drop = sorted(b - a), sorted(a - b)
    say("OVERLAY STD JOBS: this run's launchd.plist vs the standard root: %d added%s, %d dropped%s" % (
        len(add), (" (" + ", ".join(add[:12]) + (" ..." if len(add) > 12 else "") + ")") if add else "",
        len(drop), (" (" + ", ".join(drop[:12]) + (" ..." if len(drop) > 12 else "") + ")") if drop else ""))


def boot(label, run):
    """count one boot of the standard root (any run, overlay or not): the first boots after a flash run iOS's migration."""
    root, why = std_root()
    if root is None:
        say("OVERLAY STD GAP: boot not counted (%s)" % why)
        return 0
    with Locked():
        led = load(root)
        if "boots" not in led:   # a ledger from before the counter: earlier boots were not counted
            led["boots"], led["boots_uncounted_before"] = [], True
        led["boots"].append({"label": label, "run": os.path.basename(run.rstrip("/")), "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        save(root, led)
    say("OVERLAY STD: boot %d of root %s since its flash %s (%s)" % (len(led["boots"]), root[0][:12], root[1], label))
    return 0


def check(r):
    root, _ = std_root()
    if root is None:
        return 0 if r == "unknown" else 1
    with Locked():
        led = load(root)
    now = rev(root, led)
    if now == r:
        return 0
    say("OVERLAY STD: changed while this run waited (%s -> %s): the boot overlay is rebuilt" % (r, now))
    return 1


def applied(run):
    for n in ("usbnet.log", "merged.log"):
        try:
            with open(os.path.join(run, n), errors="replace") as f:
                for l in f:
                    m = re.search(r"SWEET DEV: copy (\d+) entries", l)
                    if m:
                        return int(m.group(1))
        except FileNotFoundError:   # lint-silent: expected this run has no such log (no USB log, or the merge did not run)
            continue
    return None


def gone_paths(run):
    """paths the kernel logged as removed or absent (patch 0280) in this run's logs: only those left userdata."""
    out = set()
    for n in ("usbnet.log", "merged.log"):
        try:
            with open(os.path.join(run, n), errors="replace") as f:
                for l in f:
                    m = re.search(r"SWEET DEV: (?:removed|absent) (/\S+)", l)
                    if m:
                        out.add(m.group(1))
        except FileNotFoundError:   # lint-silent: expected this run has no such log
            continue
    return out


def record(d, label, run):
    root, why = std_root()
    if root is None:
        say("OVERLAY STD GAP: ledger not updated (%s)" % why)
        return 0
    n = applied(run)   # None = no evidence either way (no USB log): assume it MAY be applied, add differing paths, remove none
    with Locked():
        led = load(root)
        add = rem = 0
        gone = gone_paths(run)
        ext = extras()
        for p in files(d):
            if p.endswith(WH_SUFFIX):   # a removal: gone only when the kernel logged it removed|absent (refused = still there)
                q = p[:-len(WH_SUFFIX)]
                if q in gone and led["paths"].pop(q, None) is not None:
                    rem += 1
                elif q not in gone:
                    say("OVERLAY STD GAP: whiteout of %s not confirmed by the kernel log (refused or no log): it stays in the ledger" % q)
                continue
            src = std_copy(root, p, ext)
            mine = os.path.join(d, p.lstrip("/"))
            if p in ext and n is not None:   # an extra: remember which version is on userdata now (only when known applied)
                led["extras"][p] = hashlib.sha256(open(mine, "rb").read()).hexdigest()
            if src is not None and filecmp.cmp(src, mine, shallow=False):
                if n is not None and led["paths"].pop(p, None) is not None:
                    rem += 1
            else:
                led["paths"][p] = {"by": label, "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "in_std": src is not None}
                add += 1
        save(root, led)
    say("OVERLAY STD: recorded %s: %s; %d path(s) now differ from the standard root, %d back to standard; ledger %d path(s)" % (
        label, "archive applied (%d entries)" % n if n is not None else "no `SWEET DEV: copy` line in %s (no USB log?): counted as MAYBE applied" % run,
        add, rem, len(led["paths"])))
    return 0


def note(path, by):
    root, why = std_root()
    if root is None:
        say("OVERLAY STD GAP: push of %s not recorded (%s)" % (path, why))
        return 0
    with Locked():
        led = load(root)
        led["paths"][path] = {"by": by, "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "in_std": None}
        save(root, led)
    return 0


def status():
    root, why = std_root()
    if root is None:
        say("standard root unknown: %s" % why)
        return 1
    with Locked():
        led = load(root)
    say("standard root %s flashed %s image %s" % (root[0][:12], root[1], root[2]))
    for p, v in sorted(led["paths"].items()):
        say("  %s by %s at %s%s" % (p, v.get("by"), v.get("when"), "" if v.get("in_std") is not False else " (ADDED: residue)"))
    say("ledger %d path(s), rev %s" % (len(led["paths"]), rev(root, led)))
    return 0


def sandboxed():
    """a sim/host-test run (PHONE_LOCK_DIR is a test lock) without its own ARTIFACTS dir under $TMPDIR: never touch the real ledger."""
    import tempfile
    import simseams
    if not simseams.in_sandbox():
        return False
    tmp = os.path.realpath(tempfile.gettempdir()).rstrip(os.sep) + os.sep
    return not os.path.realpath(ART).startswith(tmp)


def main():
    ap = argparse.ArgumentParser(prog="overlay_std.py")
    sub = ap.add_subparsers(dest="op", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--overlay", required=True); p.add_argument("--out", required=True); p.add_argument("--label", default="run"); p.add_argument("--intent", default=""); p.add_argument("--kernel")
    c = sub.add_parser("check"); c.add_argument("--rev", required=True)
    r = sub.add_parser("record"); r.add_argument("--dir", required=True); r.add_argument("--label", default="run"); r.add_argument("--run", required=True)
    b = sub.add_parser("boot"); b.add_argument("--label", default="run"); b.add_argument("--run", required=True)
    n = sub.add_parser("note"); n.add_argument("path"); n.add_argument("--by", required=True)
    sub.add_parser("status")
    sub.add_parser("boots")   # prints "BOOTS N" (boots of the standard root since its flash) or "BOOTS ? <why>"; for fcap-boot's settle rule
    a = ap.parse_args()
    if sandboxed():
        say("OVERLAY STD: skipped (sim sandbox: PHONE_LOCK_DIR is a test lock and ARTIFACTS is not a test dir)")
        if a.op == "prepare":
            say("OVERLAY STD REV: unknown")
            say("OVERLAY STD DIR: %s" % ("none" if a.overlay == "-" else a.overlay))
        return 0
    if a.op == "prepare":
        return prepare(a.overlay, a.out, a.label, a.intent, a.kernel)
    if a.op == "check":
        return check(a.rev)
    if a.op == "record":
        return record(a.dir, a.label, a.run)
    if a.op == "note":
        return note(a.path, a.by)
    if a.op == "boots":
        root, why = std_root()
        if root is None:
            print("BOOTS ? %s" % why); return 0
        led = load(root)
        if "boots" not in led or led.get("boots_uncounted_before"):
            print("BOOTS ? uncounted ledger"); return 0
        print("BOOTS %d" % len(led.get("boots", []))); return 0
    if a.op == "boot":
        return boot(a.label, a.run)
    return status()


if __name__ == "__main__":
    sys.exit(main())
