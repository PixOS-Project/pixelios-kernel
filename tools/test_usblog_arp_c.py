#!/usr/bin/env python3
"""Compile and run the REAL C of patch 0184 (usb log ARP wait: ud_log_arp_ready, ud_log_note, ud_log_round) plus the helpers of
patch 0179 (ud_log_send, ud_log_drain) against a stubbed arp_lookup_ip, fake sweet_log_copy and scripted sock_send (host test).

    tools/test_usblog_arp_c.py [--patch FILE] [--keep]

--patch replaces the 0184 patch (a mutated copy must FAIL). Prints the C test's RESULT line; exit 0 PASS, 1 FAIL, 2 ERROR.
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
tgc = _load("test_usblog_gap_c")


def build_arp_inc(patch_text):
    """The first run of '+' lines of the hunk that holds ud_log_round (the includes and the thread edits are other hunks)."""
    hunks, cur = [], None
    for l in tuc.only_file(patch_text, "bsd/dev/sweet_usb.c").split("\n"):
        if l.startswith("@@"):
            cur = []; hunks.append(cur)
        elif cur is not None and not l.startswith("\\"):
            cur.append(l)
    for h in hunks:
        out = []
        for l in h:
            if l.startswith("+"):
                out.append(l[1:])
            elif out:
                break
        if any("ud_log_round" in l for l in out):
            return "\n".join(out) + "\n"
    return None


def main(argv):
    patch, keep, i = None, False, 1
    while i < len(argv):
        if argv[i] == "--patch" and i + 1 < len(argv):
            patch = pathlib.Path(argv[i + 1]); i += 2
        elif argv[i] == "--keep":
            keep = True; i += 1
        else:
            print("usage: test_usblog_arp_c.py [--patch FILE] [--keep]\nRESULT: ERROR bad arguments"); return 2
    pdir = HERE.parent / "xnu" / "patches"
    if patch is None:
        found = sorted(pdir.glob("*-sweet-usblog-arp.patch"))
        if len(found) != 1:
            print("RESULT: ERROR need exactly one *-sweet-usblog-arp.patch"); return 2
        patch = found[0]
    gaps = sorted(pdir.glob("*-sweet-usblog-gap.patch"))
    if len(gaps) != 1 or not shutil.which("gcc"):
        print("RESULT: ERROR need gcc and exactly one *-sweet-usblog-gap.patch"); return 2
    gap_inc = tgc.build_inc(gaps[0].read_text())
    arp_inc = build_arp_inc(patch.read_text())
    for inc in ("#include <netinet/in_arp.h>", "#include <net/if_dl.h>"):   # review r1 F2: the real build needs both
        if ("+" + inc) not in patch.read_text():
            print("RESULT: FAIL patch lacks %s" % inc); return 1
    if gap_inc is None or arp_inc is None:
        print("RESULT: ERROR no helpers found in %s / %s" % (gaps[0], patch)); return 2
    work = pathlib.Path(tempfile.mkdtemp(prefix="usblog-arp-c-"))
    try:
        (work / "gap.inc").write_text(gap_inc)
        (work / "arp.inc").write_text(arp_inc)
        shutil.copy(HERE / "test_usblog_arp_c.c", work / "test_usblog_arp_c.c")
        exe = work / "t"
        cc = ["gcc", "-std=gnu11", "-g", "-O1", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined",
              "-fno-sanitize-recover=all", "-iquote", str(work), str(work / "test_usblog_arp_c.c"), "-o", str(exe)]
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


class UsblogArpC(unittest.TestCase):   # gate-unit-shards runs `unittest discover`
    def test_real_c(self):
        self.assertEqual(main(["test_usblog_arp_c.py"]), 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
