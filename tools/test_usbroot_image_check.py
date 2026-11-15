#!/usr/bin/env python3
"""Synthetic-header test for tools/usbroot-image-check.py. Prints a RESULT line."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import importlib.util, os, struct, sys, unittest
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("uic", os.path.join(here, "usbroot-image-check.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def vh(sig, attrs, lmv, jib, bsize=4096):
    b = bytearray(512); b[0:2] = sig; b[2:4] = struct.pack(">H", 5 if sig == b"HX" else 4)
    b[4:8] = struct.pack(">I", attrs); b[8:12] = lmv; b[12:16] = struct.pack(">I", jib); b[40:44] = struct.pack(">I", bsize)
    return bytes(b)

def run():
    fails = []
    def check(name, cond):
        if not cond: fails.append(name)

    jib = lambda flags: (lambda off: struct.pack(">I", flags) + b"\0" * 12)
    r = m.parse(vh(b"HX", 0x80002100, b"H+Lx", 129), jib(5))   # the ios-root4-inj6 values (research 55)
    check("rw writes journal", r["rw_writes_journal"] and r["rw_late_init"])
    check("patched ro does not", r["patched_ro_writes_journal"] is False)
    r = m.parse(vh(b"HX", 0x80002100, b"H+Lx", 129), jib(1))
    check("no NeedInit: no write", not r["rw_writes_journal"] and r["rw_late_init"])
    r = m.parse(vh(b"H+", 0x80000100, b"10.0", 0), jib(0))
    check("not journaled", not r["journaled"] and not r["rw_late_init"])
    r = m.parse(vh(b"HX", 0x80002100, b"HFSJ", 129), jib(0))
    check("HFSJ: no late init", not r["rw_late_init"] and "note" in r)
    try: m.parse(b"\0" * 512, jib(0)); check("bad sig raises", False)
    except ValueError: pass   # lint-silent: expected the test expects exactly this ValueError: no raise fails the check one line above
    print("RESULT: %s usbroot-image-check (%d failed%s)" % ("FAIL" if fails else "PASS", len(fails), ": " + ", ".join(fails) if fails else ""))
    return fails


class UsbrootImageCheck(unittest.TestCase):   # gate-unit-shards runs `unittest discover`: no work at import time
    def test_synthetic_headers(self):
        self.assertEqual(run(), [])


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
