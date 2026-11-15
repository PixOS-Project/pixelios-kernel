#!/usr/bin/env python3
"""Compile and run the REAL C of patch 0179 (usb log GAP: ud_log_send, struct ud_log_gap, ud_log_drain) against a fake
sweet_log_copy and a scripted sock_send (host test, no phone). Review usblog-gap-0179-r1 F1/F4.

    tools/test_usblog_gap_c.py [--patch FILE] [--keep]

Extracts the sweet_usb.c section of the patch (default: xnu/patches/*-sweet-usblog-gap.patch), the first run of '+' lines
of its first hunk (the helpers; the thread call-site edits follow context). --patch lets a mutated copy be tested (a mutant must FAIL).
Prints the C test's RESULT line; exit 0 PASS, 1 FAIL, 2 ERROR.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import importlib.util, pathlib, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    s = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


tuc = _load("test_usbroot_c")


def build_inc(patch_text):
    hunks, cur = [], None
    for l in tuc.only_file(patch_text, "bsd/dev/sweet_usb.c").split("\n"):
        if l.startswith("@@"):
            cur = []; hunks.append(cur)
        elif cur is not None and not l.startswith("\\"):
            cur.append(l)
    if not hunks:
        return None
    out = []                    # the first contiguous run of '+' lines (the helpers); the thread call-site edits come after context
    for l in hunks[0]:
        if l.startswith("+"):
            out.append(l[1:])
        elif out:
            break
    return "\n".join(out) + "\n"


def main(argv):
    patch, keep, i = None, False, 1
    while i < len(argv):
        if argv[i] == "--patch" and i + 1 < len(argv):
            patch = pathlib.Path(argv[i + 1]); i += 2
        elif argv[i] == "--keep":
            keep = True; i += 1
        else:
            print("usage: test_usblog_gap_c.py [--patch FILE] [--keep]\nRESULT: ERROR bad arguments"); return 2
    if patch is None:
        found = sorted((HERE.parent / "xnu" / "patches").glob("*-sweet-usblog-gap.patch"))
        if len(found) != 1:
            print("RESULT: ERROR need exactly one *-sweet-usblog-gap.patch"); return 2
        patch = found[0]
    if not shutil.which("gcc"):
        print("RESULT: ERROR gcc not found"); return 2
    inc = build_inc(patch.read_text())
    if inc is None or "ud_log_drain" not in inc:
        print("RESULT: ERROR no ud_log_drain in the sweet_usb.c section of %s" % patch); return 2
    work = pathlib.Path(tempfile.mkdtemp(prefix="usblog-gap-c-"))
    try:
        (work / "gap.inc").write_text(inc)
        shutil.copy(HERE / "test_usblog_gap_c.c", work / "test_usblog_gap_c.c")
        exe = work / "t"
        cc = ["gcc", "-std=gnu11", "-g", "-O1", "-Wall", "-Wno-format", "-Wno-unused-function", "-fsanitize=address,undefined",
              "-fno-sanitize-recover=all", "-iquote", str(work), str(work / "test_usblog_gap_c.c"), "-o", str(exe)]
        r = subprocess.run(cc, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-1500:]); print("RESULT: ERROR compile failed"); return 2
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        sys.stdout.write(r.stdout[-3000:])
        if r.returncode and "RESULT:" not in r.stdout:
            sys.stdout.write(r.stderr[-1500:]); print("RESULT: FAIL sanitizer or crash (exit %d)" % r.returncode)
        return 1 if r.returncode else 0
    finally:
        if keep:
            print("kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)


class UsblogGapC(unittest.TestCase):   # gate-unit-shards runs `unittest discover`
    def test_real_c(self):
        self.assertEqual(main(["test_usblog_gap_c.py"]), 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
