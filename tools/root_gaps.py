#!/usr/bin/env python3
"""root_gaps: print the BT/... GAP lines of known stand-in settings found in an iOS root image (host-side, read-only, tools/hfs_read.py); xrun marks them as run.json root_gaps

A stand-in is a setting the root carries because the hardware or our port lacks what iOS expects. It must show in every run summary (rule 7),
not only in the root build log, so xrun calls this on the image `run-preflight --userdata-check` names and stores the lines in run.json
(`root_gaps`); tools/xrun_report.py counts them with the other GAP lines.

  tools/root_gaps.py IMAGE        one "<AREA> GAP: ..." line per stand-in present, then RESULT: PASS root_gaps found=N
  tools/root_gaps.py --self-test  inline checks (also run by tools/test_root_gaps.py)
Exit 0 = image read (found=0 is a pass); 2 = image unreadable: one "ROOT GAP: ..." line + RESULT: ERROR (never a silent pass).

Known stand-ins (STANDINS):
  Astronomy poster moved out (userland tweak_metal_posters, ALL-ON T-04): POSTER GAP while System/Library/CoreServices.disabled/AegirProxyApp.app exists.
  bluetoothd defaults LE/SupportsMultipleAdvertising=false (userland tools/sweet-root-tweaks.py tweak_btconfig): this BCM4389 firmware answers
  Apple's multi-advertising VSC 0xfd48 with 0x01 Unknown HCI Command; with the setting unset bluetoothd never sets its LE random address, LE Set
  Scan Enable fails 0x12 and bluetoothd exits 1 every ~2 s (run bt-bw2 2026-11-10 [obs]: 95 exits, CoreBluetooth discovered=0, LE connect FAIL).
  User 2026-11-10: the setting is the fix, named by a GAP line. Cost: one LE advertising instance instead of several."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import os
import plistlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfs_read  # noqa: E402

# (path in the root, check(plist dict) -> bool, GAP line)
STANDINS = [
    ("/private/var/mobile/Library/Preferences/com.apple.MobileBluetooth.debug.plist",
     lambda pl: isinstance(pl.get("LE"), dict) and pl["LE"].get("SupportsMultipleAdvertising") is False,
     "BT GAP: root sets bluetoothd defaults LE/SupportsMultipleAdvertising=false (stand-in: the BCM4389 firmware has no 0xfd48 multi-advertising; one LE advertising instance)"),
    # ALL-ON T-04 (user 2026-11-11): userland tools/sweet-root-tweaks.py tweak_metal_posters moves the Metal-only Astronomy poster out of CoreServices
    ("/System/Library/CoreServices.disabled/AegirProxyApp.app/Info.plist",
     lambda pl: pl.get("CFBundleIdentifier") == "com.apple.NanoUniverse.AegirProxyApp",
     "POSTER GAP: root has the Astronomy poster (AegirProxyApp) moved to CoreServices.disabled: it needs Metal and PixOS has no Metal runtime (nil MTLBuffer abort x64-96 per boot otherwise, research 137)"),
]


def gaps(read):
    """read(path) -> bytes or None (absent). Returns (gap lines, problem lines). A file that exists but is not a plist is a problem, not a silent miss."""
    out, bad = [], []
    for path, check, line in STANDINS:
        data = read(path)
        if data is None:
            continue
        try:
            pl = plistlib.loads(data)
        except Exception as e:      # noqa: BLE001  (any parse failure is reported below, rule 7)
            bad.append("ROOT GAP: %s is not a readable plist (%s): stand-in state unknown" % (path, type(e).__name__))
            continue
        if isinstance(pl, dict) and check(pl):
            out.append(line)
    return out, bad


def self_test():
    good = plistlib.dumps({"LE": {"SupportsMultipleAdvertising": False}}, fmt=plistlib.FMT_BINARY)
    cases = [
        ("setting present", {STANDINS[0][0]: good}, 1, 0),
        ("file absent (measured default)", {}, 0, 0),
        ("setting true", {STANDINS[0][0]: plistlib.dumps({"LE": {"SupportsMultipleAdvertising": True}})}, 0, 0),
        ("other LE keys only", {STANDINS[0][0]: plistlib.dumps({"LE": {"Other": False}})}, 0, 0),
        ("garbled file", {STANDINS[0][0]: b"not a plist"}, 0, 1),
        ("LE not a dict", {STANDINS[0][0]: plistlib.dumps({"LE": False})}, 0, 0),
        ("aegir moved", {STANDINS[1][0]: plistlib.dumps({"CFBundleIdentifier": "com.apple.NanoUniverse.AegirProxyApp"})}, 1, 0),
        ("other bundle in disabled", {STANDINS[1][0]: plistlib.dumps({"CFBundleIdentifier": "com.example.other"})}, 0, 0),
        ("both stand-ins", {STANDINS[0][0]: good, STANDINS[1][0]: plistlib.dumps({"CFBundleIdentifier": "com.apple.NanoUniverse.AegirProxyApp"})}, 2, 0),
    ]
    fails = 0
    for name, files, want_g, want_b in cases:
        g, b = gaps(files.get)
        ok = len(g) == want_g and len(b) == want_b and all(" GAP: " in x for x in g + b)
        fails += not ok
        print("%s %s (gaps=%d problems=%d)" % ("ok  " if ok else "FAIL", name, len(g), len(b)))
    print("RESULT: %s root_gaps self-test cases=%d failed=%d" % ("PASS" if not fails else "FAIL", len(cases), fails))
    return 1 if fails else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: root_gaps.py IMAGE | --self-test")
        print("RESULT: ERROR root_gaps bad arguments")
        return 2
    try:
        h = hfs_read.HFS(argv[0])
        g, b = gaps(h.read_file)
    except (hfs_read.Bad, OSError) as e:
        print("ROOT GAP: %s not readable as an HFS+ root (%s): stand-in settings unknown" % (argv[0], e))
        print("RESULT: ERROR root_gaps image unreadable")
        return 2
    for line in g + b:
        print(line)
    print("RESULT: %s root_gaps found=%d problems=%d" % ("PASS" if not b else "ERROR", len(g), len(b)))
    return 0 if not b else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
