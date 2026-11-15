#!/usr/bin/env python3
"""Compile and run the REAL C of patch 0172's sweet_usb.c half (ud_rx_fast_wait, sweet_usb_rx_hurry, the ud_thread sleep
tail) against a stub scheduler and fake clock (host test, no phone). Review usbroot-rxfast-r1 F3.

    tools/test_usb_rxfast_c.py [--patch FILE] [--keep]

Extracts the sweet_usb.c section of the patch (default: xnu/patches/*-sweet-usbroot-rxfast.patch) with the same helper as
tools/test_usbroot_c.py (only_file): hunk 1 is all '+' lines; hunk 2 (ud_thread) is the new side from its first to its last
'+' line, wrapped in a loop. --patch lets a mutated copy be tested (a mutant must make this FAIL).
Prints the C test's RESULT line; exit 0 PASS, 1 FAIL, 2 ERROR.
"""
import importlib.util, pathlib, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("test_usbroot_c", HERE / "test_usbroot_c.py")
tuc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(tuc)


def new_side(hunk):
    """New-side text of one hunk, first '+' line to last '+' line."""
    lines = [l for l in hunk if l and l[0] in "+ "]
    plus = [i for i, l in enumerate(lines) if l[0] == "+"]
    return [l[1:] for l in lines[plus[0]:plus[-1] + 1]] if plus else []


def build_inc(patch_text):
    sec = tuc.only_file(patch_text, "bsd/dev/sweet_usb.c").split("\n")
    hunks, cur = [], None
    for l in sec:
        if l.startswith("@@"):
            cur = []; hunks.append(cur)
        elif cur is not None and not l.startswith("\\"):
            cur.append(l)
    if len(hunks) != 2:
        return None
    body = "\n".join(new_side(hunks[1]))
    return ("\n".join(new_side(hunks[0])) + "\n\nstatic void\nud_thread_tail_model(unsigned iterations)\n{\n\tuint32_t ticks = 0;\n"
            "\tfor (unsigned it = 0; it < iterations; it++) {\n" + body + "\n\t}\n}\n")


def main(argv):
    patch, keep, i = None, False, 1
    while i < len(argv):
        if argv[i] == "--patch" and i + 1 < len(argv):
            patch = pathlib.Path(argv[i + 1]); i += 2
        elif argv[i] == "--keep":
            keep = True; i += 1
        else:
            print("usage: test_usb_rxfast_c.py [--patch FILE] [--keep]\nRESULT: ERROR bad arguments"); return 2
    if patch is None:
        found = sorted((HERE.parent / "xnu" / "patches").glob("*-sweet-usbroot-rxfast.patch"))
        if len(found) != 1:
            print("RESULT: ERROR need exactly one *-sweet-usbroot-rxfast.patch"); return 2
        patch = found[0]
    if not shutil.which("gcc"):
        print("RESULT: ERROR gcc not found"); return 2
    inc = build_inc(patch.read_text())
    if inc is None or "ud_rx_fast_wait" not in inc:
        print("RESULT: ERROR no two-hunk sweet_usb.c section in %s" % patch); return 2
    work = pathlib.Path(tempfile.mkdtemp(prefix="usb-rxfast-c-"))
    try:
        (work / "rxfast.inc").write_text(inc)
        shutil.copy(HERE / "test_usb_rxfast_c.c", work / "test_usb_rxfast_c.c")
        exe = work / "t"
        cc = ["gcc", "-std=gnu11", "-g", "-O1", "-Wall", "-Wno-format", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-iquote", str(work), str(work / "test_usb_rxfast_c.c"), "-o", str(exe)]
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


class UsbRxfastC(unittest.TestCase):   # gate-unit-shards runs `unittest discover`: the real C test as one case
    def test_real_c(self):
        self.assertEqual(main(["test_usb_rxfast_c.py"]), 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
