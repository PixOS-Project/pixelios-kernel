#!/usr/bin/env python3
"""CR-02 for shim/wlan_en0.h (T-4d29, SPEC-WIFI EN0 block): the host test must pass on the real header and FAIL on every mutant, one per rule.
Each mutant is a private copy of the header with ONE exact-string edit (the pattern must occur exactly once, else the runner itself fails: a mutant that no
longer applies is a silent hole). Sequential, python3 -B, no hardware.
Usage: tools/test_wlan_en0_mutants.py [--kernel-root DIR]   (DIR holds shim/, default: the repo of this file)"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "--kernel-root" in sys.argv:
    ROOT = sys.argv[sys.argv.index("--kernel-root") + 1]
SHIM = os.path.join(ROOT, "shim")
HDR = os.path.join(SHIM, "wlan_en0.h")
TEST = os.path.join(SHIM, "wlan_en0_host_test.c")

DEFAULT_IP = ("\t\tc->ip[0] = 192; c->ip[2] = 2; c->ip[3] = 2; c->mask[0] = c->mask[1] = c->mask[2] = 255;\n"
              "\t\tc->gw[0] = 192; c->gw[2] = 2; c->gw[3] = 1;\n\t\treturn 0;\n")
# name, rule row, old, new
MUTANTS = [
    ("rx-pad-0", "EN3 wrong pad", "#define EN0_RX_PAD      2u", "#define EN0_RX_PAD      0u"),
    ("rx-pad-4", "EN3 wrong pad", "#define EN0_RX_PAD      2u", "#define EN0_RX_PAD      4u"),
    ("rx-whole-len-first-buffer", "EN3 whole length on the first buffer",
     "\t\tn = (len - off) < cap ? (len - off) : (uint32_t)cap;\n", "\t\tn = off == 0u ? len : ((len - off) < cap ? (len - off) : (uint32_t)cap);\n"),
    ("rx-no-header-adjust", "EN4 missing header adjust", "\tmbuf_pkthdr_adjustlen(m, -(int)SWDP_ETH_HLEN);\n", ""),
    ("rx-no-pkt-hdr", "EN4 pkt_hdr not recorded", "\tmbuf_pkthdr_setheader(m, mbuf_data(m));\n", ""),
    ("rx-no-bpf-tap", "EN5 no tap before ifnet_input", "\tbpf_tap_in(e->ifp, DLT_EN10MB, m, NULL, 0);\n", ""),
    ("interrupt-registered", "EN7 interrupt registered", "\te->attached = 1u;\n", "\te->attached = 1u;\n\tsweet_register_interrupt();\n"),
    ("hard-coded-address", "EN9 hard-coded default",
     "\tif (s == NULL || *s == '\\0') {\n\t\tSWDP_GAP(EN0_GAP_EN9);\n\t\treturn -1;\n\t}\n",
     "\tif (s == NULL || *s == '\\0') {\n" + DEFAULT_IP + "\t}\n"),
    ("silent-ioctl", "EN10 silent EOPNOTSUPP",
     "\t\tsnprintf(b, sizeof b, \"WIFI GAP: EN10 en0 ioctl 0x%lx unsupported\", cmd);\n\t\tSWDP_GAP(b);\n", "\t\t(void)b;\n"),
    ("nx-noauto-dropped", "EN1 NX_NOAUTO dropped", "\tinit.flags = IFNET_INIT_NX_NOAUTO;", "\tinit.flags = 0u;"),   # start model since a5133a35 (no IFNET_INIT_LEGACY)
    ("tx-mbuf-leak", "EN6 mbuf leak", "\t\tmbuf_freem(m);                            /* always, exactly once (EN6) */\n", ""),
    ("link-on-when-down", "EN8 posts ON when down", "en0_post_link(e, up ? KEV_DL_LINK_ON : KEV_DL_LINK_OFF);\n\tSWDP_GAP(EN0_GAP_EN8);\n\tif (up && !e->addr_done", "en0_post_link(e, KEV_DL_LINK_ON);\n\tSWDP_GAP(EN0_GAP_EN8);\n\tif (up && !e->addr_done"),      # patch 0282: en0_link_only (0281) has the same line, so the pattern names en0_set_link by what follows
    ("lladdr-before-attach", "EN2 MAC set before attach",
     "\tif (ifnet_attach(e->ifp, NULL) != 0) {", "\tifnet_set_lladdr(e->ifp, mac, EN0_ALEN);\n\tif (ifnet_attach(e->ifp, NULL) != 0) {"),
]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlan_keep_mutants  # noqa: E402  patch 0282: the EAPOL divert and the raw tx rows live with the other W-D6 mutants
MUTANTS += wlan_keep_mutants.EN0_ROWS


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def build_and_run(workdir, hdr_path):
    exe = os.path.join(workdir, "t")
    r = run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", '-DEN0_HDR="%s"' % hdr_path, "-I" + SHIM, "-o", exe, TEST])
    if r.returncode:
        return None, "BUILD FAIL: " + r.stderr[:400]
    return run([exe]), ""


def main():
    with open(HDR, encoding="utf-8") as fh:
        src = fh.read()
    with tempfile.TemporaryDirectory(prefix="en0mut-") as d:
        r, err = build_and_run(d, HDR)
        if r is None or r.returncode != 0 or "RESULT: PASS wlan_en0_host_test" not in r.stdout:
            print("RESULT: FAIL wlan_en0_mutants (the unmutated host test does not pass) " + err)
            return 1
        survived, lines = [], []
        for name, row, old, new in MUTANTS:
            if src.count(old) != 1:
                print("RESULT: FAIL wlan_en0_mutants (mutant %s: pattern occurs %d times, must be exactly 1)" % (name, src.count(old)))
                return 1
            p = os.path.join(d, "wlan_en0.h")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(src.replace(old, new))
            r, err = build_and_run(d, p)
            if r is None:
                survived.append(name)
                lines.append("%s (%s): %s" % (name, row, err))
                continue
            first = next((x for x in r.stdout.splitlines() if "FAIL" in x), "")
            if r.returncode == 0:
                survived.append(name)
                lines.append("%s (%s): SURVIVED" % (name, row))
            else:
                lines.append("%s (%s): FAIL as expected: %s" % (name, row, first.strip()[:110]))
        print("\n".join(lines))
        if survived:
            print("RESULT: FAIL wlan_en0_mutants survived=%s" % ",".join(survived))
            return 1
        print("RESULT: PASS wlan_en0_mutants mutants=%d killed=%d" % (len(MUTANTS), len(MUTANTS)))
        return 0


class TestEn0Mutants(unittest.TestCase):
    """The gate's unit step discovers test_*.py with unittest: one test = the whole table (13 small gcc builds), the RESULT line is on stdout."""

    def test_every_mutant_fails_the_host_test(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
