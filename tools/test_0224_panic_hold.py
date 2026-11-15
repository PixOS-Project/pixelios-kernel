#!/usr/bin/env python3
"""Host test for xnu patch 0224 sweet-panic-hold and the debug=0x8 default (T-35ba; no phone, no build). Reads the patch text and tools/build-xnu-image.sh.

    tools/test_0224_panic_hold.py

Exit 0 PASS, 1 FAIL."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + __doc__.strip()); raise SystemExit(0)
import pathlib, re, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH = (ROOT / "xnu/patches/0224-sweet-panic-hold.patch").read_text()
IMAGE = (ROOT / "tools/build-xnu-image.sh").read_text()
added = "\n".join(l[1:] for l in PATCH.splitlines() if l.startswith("+") and not l.startswith("+++"))
removed = [l for l in PATCH.splitlines() if l.startswith("-") and not l.startswith("---")]
hold_at = PATCH.find("+\t\tsweet_panic_hold();")
reset_at = PATCH.find(" \t\tkdp_machine_reboot_type(kPEPanicRestartCPU, debugger_panic_options);", hold_at)
body = re.search(r"sweet_panic_hold\(void\)\n\{(.*?)\n\}", added, re.S).group(1)
checks = [
    ("default image boots debug=0x8", re.search(r'^args="debug=0x8 ', IMAGE, re.M)),
    ("no default debug=0xc", not re.search(r'^args="debug=0xc ', IMAGE, re.M)),
    ("patch removes nothing", not removed),
    ("hold is SWEET only", added.count("#if defined(ARM64_BOARD_CONFIG_SWEET)") == 3),
    ("hold sits right before the panic reset", 0 < hold_at < reset_at),
    ("hold is bounded", "MIN(sweet_panic_hold_s, SWEET_PANIC_HOLD_MAX_S)" in body and "#define SWEET_PANIC_HOLD_MAX_S     120" in added),
    ("0 means no hold", "if (secs == 0) {\n\t\treturn;" in body),
    ("panic text printed before the spin", body.find("Original panic string") < body.find("while (mach_absolute_time() < end)")),
    ("no kprintf/printf in panic context", not re.search(r"\b(kprintf|printf)\(", body)),
    ("no hardware write in the hold", not re.search(r"smc|ml_phys_write|try_write|\*\(volatile", body)),
    ("rule 8 cut value logged", "SWEET GAP: sweetpanichold=%u above %u" in added),
    ("boot line says the hold", "SWEET FAULT HOLD: %u s on screen" in added),
]


class PanicHold(unittest.TestCase):
    def test_checks(self):
        self.assertEqual([n for n, ok in checks if not ok], [])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("RESULT: %s panic-hold (%d checks)" % ("PASS" if ok else "FAIL", len(checks)))
    sys.exit(0 if ok else 1)
