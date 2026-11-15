#!/usr/bin/env python3
"""gen_wlan_h1: the HSI2 S2MPU/SSMT register-pair check of tools/gen_wlan_seq.py check_dt() (H1 probe, docs/research/90).

Lives in its own module so tools/gen_wlan_seq.py (a legacy file over the R29 size limit) does not grow (T-191f); gen_wlan_seq.py calls check_hsi2_pair().

  tools/gen_wlan_h1.py --self-test    a fixture dtsi passes, a changed soc.json pair is rejected (also run by tools/test_gen_wlan_h1.py)
Last line: RESULT: PASS|FAIL gen_wlan_h1 ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib
import re
import sys
import tempfile

S2MPU_HSI2 = re.compile(
    r"s2mpu_hsi2:\s*s2mpu@14480000\s*\{.*?reg = <0 (0x[0-9A-Fa-f]+) 0x10000>,\s*"
    r"<0 (0x[0-9A-Fa-f]+) 0x10000>;\s*reg-names = \"s2mpu\", \"ssmt\"", re.S)


def check_hsi2_pair(dt_pcie, soc):
    """soc.json "wlan" hsi2_s2mpu / hsi2_ssmt must be the s2mpu_hsi2 reg pair of dts-gs101-gs101-s2mpu.dtsi, next to the pcie dtsi (ValueError otherwise)."""
    s2 = (pathlib.Path(dt_pcie).parent / "dts-gs101-gs101-s2mpu.dtsi").read_text()
    m = S2MPU_HSI2.search(s2)
    if not (m and int(m.group(1), 0) == int(soc["hsi2_s2mpu"], 0) and int(m.group(2), 0) == int(soc["hsi2_ssmt"], 0)):
        raise ValueError("soc.json wlan hsi2_s2mpu/hsi2_ssmt must be the s2mpu_hsi2 reg pair (dts-gs101-gs101-s2mpu.dtsi:18-23)")


_FIXTURE = """s2mpu_hsi2: s2mpu@14480000 {
\tcompatible = "google,s2mpu";
\treg = <0 0x14480000 0x10000>,
\t      <0 0x144F0000 0x10000>;
\treg-names = "s2mpu", "ssmt";
};
"""


def self_test() -> int:
    good = {"hsi2_s2mpu": "0x14480000", "hsi2_ssmt": "0x144F0000"}
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "dts-gs101-gs101-s2mpu.dtsi").write_text(_FIXTURE)
        dt = pathlib.Path(d, "dts-gs101-gs101-pcie.dtsi")
        check_hsi2_pair(dt, good)
        for label, soc in (("s2mpu base moved", dict(good, hsi2_s2mpu="0x14490000")), ("ssmt base moved", dict(good, hsi2_ssmt="0x14500000"))):
            try:
                check_hsi2_pair(dt, soc)
            except ValueError:   # lint-silent: expected the mutant must be rejected
                continue
            print(f"RESULT: FAIL gen_wlan_h1 mutant not caught: {label}")
            return 1
        pathlib.Path(d, "dts-gs101-gs101-s2mpu.dtsi").write_text(_FIXTURE.replace("s2mpu_hsi2:", "s2mpu_other:"))
        try:
            check_hsi2_pair(dt, good)
        except ValueError:
            print("RESULT: PASS gen_wlan_h1 self-test: fixture passes; 3 mutants (2 soc.json values, node missing) rejected")
            return 0
    print("RESULT: FAIL gen_wlan_h1 mutant not caught: s2mpu_hsi2 node missing")
    return 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL gen_wlan_h1 is a module (tools/gen_wlan_seq.py imports it); run --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
