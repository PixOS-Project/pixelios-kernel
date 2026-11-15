#!/usr/bin/env python3
"""audit_wlanh1: the H1 (docs/research/90) rules of tools/test-bluejay-boot.py, in their own module (T-191f: R29 size limits).

H1 is the READ-ONLY probe of the HSI2 S2MPU, its SSMT page and the RC DBI Command word (shim/wlanh1.c). The audit has three layers:
  1. source: tools/test-bluejay-boot.py check_usb_safety() on wlanh1.c / wlanh1.h (no store of any kind);
  2. object: check_usb_object() on wlanh1.o (every store stack-based; `object_audit` here only words the PASS line);
  3. wiring (this file): shim.c calls `wlan_h1_after_hold(wlan_hold_rc);` exactly once, as the statement right after
     `wlan_hold_rc = wlan_link_hold(&wlan_held);` in the #ifdef WLAN_HOLD block (so before loader_boot(dtb)), never calls wlan_h1_run() itself, and
     the inline in shim/wlanh1.h runs wlan_h1_run() once, only inside `if (hold_rc == 0) { ... }`, with an else that logs a WIFI GAP.
Never loosen: a rule here may only get stricter. `cases()` returns the shim.c mutants (every one must be rejected by check_call_site) and first proves
the header mutants (`header_cases`) are all rejected by check_gate; it raises AssertionError when one is not.

  tools/audit_wlanh1.py --self-test    the real shim.c / wlanh1.h pass and every mutant is rejected (also run by tools/test_audit_wlanh1.py)
Last line: RESULT: PASS|FAIL audit_wlanh1 ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib
import re
import sys

CALL = "wlan_h1_after_hold(wlan_hold_rc);"
HOLD = "wlan_hold_rc=wlan_link_hold(&wlan_held);"
ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER = ROOT / "shim" / "wlanh1.h"
_C_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code(text: str) -> str:
    return _C_COMMENT.sub(" ", text)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", _code(text))


def check_call_site(src: str, name: str = "shim.c") -> None:
    """shim.c: wlan_h1_after_hold(wlan_hold_rc) once, right after the wlan_link_hold statement, before loader_boot(dtb); no direct wlan_h1_run()."""
    code, norm = _code(src), _norm(src)
    hi, i, lb = norm.find(HOLD), norm.find(CALL), norm.find("loader_boot(dtb)")
    if (len(re.findall(r"\bwlan_h1_after_hold\s*\(", code)) != 1 or re.search(r"\bwlan_h1_run\s*\(", code) or norm.count(HOLD) != 1
            or not 0 <= hi < i < lb or norm[hi:i + len(CALL)] != HOLD + CALL):
        raise AssertionError(f"{name}: wlan_h1_after_hold(wlan_hold_rc) must be called exactly once, as the statement right after `wlan_hold_rc = wlan_link_hold(&wlan_held);` "
                             "inside the #ifdef WLAN_HOLD block, before loader_boot(dtb), and shim.c must never call wlan_h1_run() itself (the gate on the hold result lives in shim/wlanh1.h)")


def check_gate(header: str, name: str = "wlanh1.h") -> None:
    """The inline wlan_h1_after_hold(): wlan_h1_run() is called once, inside `if (hold_rc == 0) { ... }`, and the else branch logs a WIFI GAP (rule 8)."""
    norm = _norm(header)
    m = re.search(r"staticinlinevoidwlan_h1_after_hold\(int32_thold_rc\)\{", norm)
    if not m:
        raise AssertionError(f"{name}: static inline void wlan_h1_after_hold(int32_t hold_rc) is missing")
    end, depth = m.end(), 1
    while end < len(norm) and depth:
        depth += (norm[end] == "{") - (norm[end] == "}")
        end += 1
    body = norm[m.end():end - 1]
    gate = "if(hold_rc==0){"
    g, run = body.find(gate), body.find("wlan_h1_run();")
    depth = 1
    for ch in body[g + len(gate):run] if 0 <= g < run else "":
        depth += (ch == "{") - (ch == "}")
        if depth <= 0:
            break
    if g < 0 or run < 0 or depth <= 0 or body.count("wlan_h1_run();") != 1 or body.count(gate) != 1 or "}else{" not in body[run:] or "WIFIGAP" not in body[run:]:
        raise AssertionError(f"{name}: wlan_h1_after_hold() must call wlan_h1_run() exactly once, inside `if (hold_rc == 0) {{ ... }}`, with an else that logs `WIFI GAP`")


def header_cases(header: str) -> list:
    """(label, mutated wlanh1.h) pairs; check_gate must reject each."""
    pairs = [
        ("gate removed", "\tif (hold_rc == 0) {", "\t{"),
        ("gate inverted", "if (hold_rc == 0) {", "if (hold_rc != 0) {"),
        ("gate on another value", "if (hold_rc == 0) {", "if (hold_rc == 1) {"),
        ("gate always true", "if (hold_rc == 0) {", "if (1) {"),
        ("a second wlan_h1_run call", "\t\twlan_h1_run();\n", "\t\twlan_h1_run();\n\t\twlan_h1_run();\n"),
        ("wlan_h1_run after the closed gate", "\t\twlan_h1_run();\n\t\tpstore_save();\n\t} else {", "\t\tpstore_save();\n\t}\n\twlan_h1_run();\n\t{"),
        ("the skip is silent", "\t\tcon_hex(\"WIFI GAP: h1 skipped, wlan_link_hold rc (0=held)\", (uint32_t)hold_rc);\n", "\t\t(void)hold_rc;\n"),
    ]
    out = []
    for label, a, b in pairs:
        assert header.count(a) == 1, f"audit_wlanh1: header mutant {label!r} does not apply (the pattern is not unique in wlanh1.h)"
        out.append((label, header.replace(a, b, 1)))
    return out


def cases(shim_src: str) -> list:
    """(label, mutated shim.c) pairs for wlan_shim_selftest; each must be rejected by check_call_site.
    First proves the header gate check: the real header passes check_gate and every header_cases mutant is rejected (AssertionError otherwise)."""
    pathlib.Path(__file__).read_bytes()  # an input of the memoized self-test: editing this module invalidates its entry (tools/audit_memo.py keys on opened files)
    header = HEADER.read_text()
    check_gate(header)
    for label, bad in header_cases(header):
        try:
            check_gate(bad)
        except AssertionError as exc:
            print(f"self-test caught: wlanh1.h {label} -> {exc}")
        else:
            raise AssertionError(f"wlanh1.h gate self-test FAILED to catch: {label}")
    m = re.search(r"^[ \t]*" + re.escape(CALL) + r".*\n", shim_src, re.M)
    assert m, "audit_wlanh1: the call line is not in shim.c"
    call, boot = m.group(0), "\t\tconst char *why = loader_boot(dtb);\n"
    assert shim_src.count(boot) == 1, "audit_wlanh1: loader_boot line not unique in shim.c"
    return [
        ("a second wlan_h1_after_hold call", shim_src.replace(call, call + call, 1)),
        ("wlan_h1_after_hold outside the WLAN_HOLD block", shim_src.replace(call, "#endif\n" + call + "#ifdef WLAN_HOLD\n", 1)),
        ("wlan_h1_after_hold not given the hold result", shim_src.replace(call, "\twlan_h1_after_hold(0);\n", 1)),
        ("wlan_h1_after_hold after loader_boot", shim_src.replace(call, "", 1).replace(boot, boot + call, 1)),
        ("wlan_h1_run called directly (the gate bypassed)", shim_src.replace(call, "\twlan_h1_run();\n", 1)),
        ("wlan_h1_after_hold missing", shim_src.replace(call, "\tpstore_save();\n", 1)),
    ]


def object_audit(out_dir: pathlib.Path, check_usb_object) -> str:
    """Object audit of out/wlanh1.o with the caller's check_usb_object (all stores stack-based; nothing writes the HSI2 S2MPU, SSMT or the RC DBI); returns the PASS line."""
    n = check_usb_object(out_dir / "wlanh1.o")
    return f"PASS wlanh1 object audit: {n} stores, all stack-based (same read-only policy as gpus2mpu.o: the HSI2 S2MPU, SSMT and RC DBI are never written)"


def self_test() -> int:
    shim = (ROOT / "shim" / "shim.c").read_text()
    try:
        check_call_site(shim)
        mutants = cases(shim)
    except AssertionError as exc:
        print(f"RESULT: FAIL audit_wlanh1 the real sources are rejected: {exc}")
        return 1
    for label, bad in mutants:
        if bad == shim:
            print(f"RESULT: FAIL audit_wlanh1 mutant did not mutate: {label}")
            return 1
        try:
            check_call_site(bad, "selftest")
        except AssertionError:   # lint-silent: expected the mutant must be rejected
            continue
        print(f"RESULT: FAIL audit_wlanh1 mutant not caught: {label}")
        return 1
    print(f"RESULT: PASS audit_wlanh1 self-test: real shim.c/wlanh1.h pass; {len(mutants)} shim.c + {len(header_cases(HEADER.read_text()))} header mutants rejected")
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL audit_wlanh1 is a module (tools/test-bluejay-boot.py imports it); run --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
