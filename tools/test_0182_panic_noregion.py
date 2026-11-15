#!/usr/bin/env python3
"""Host test for xnu patch 0182 sweet-panic-noregion-reset (no phone, no build). Reads the patch text per file.

    tools/test_0182_panic_noregion.py [--patch FILE]

Checks: (a) the SavePanicInfo guard is NULL-only and SWEET-only and the stock `while (!panic_info || ...)` line is an untouched
context line (nothing removed anywhere); (b) kern_dump returns before do_kern_dump when panic_info is NULL, and the debug.c
corefile block is skipped the same way; (c) the boot GAP line is printed once, at line start, and tools/xrun_report.py's
GAP_ANY counts it. Then the mutants of the patch text, each of which must make the check FAIL.
Exit 0 PASS, 1 FAIL, 2 ERROR.

Reset-path proof (moved from the patch header, full text, every cite read from source):
SWEET no panic region (patch 0182, rule 8). Cites: X/ = the xnu stack before 0182 (tools/stack-apply --keep, then 0182
reversed), one tree for every debug.c line below. The ADT has no pram node and no /chosen/embedded-panic-log-size, so
check_for_panic_log returns early (X/pexpert/arm/pe_init.c:94-131) and panic_info (pe_init.c:66, set only at :158) stays
NULL. SavePanicInfo then starts with `while (!panic_info || panic_info->eph_panic_log_offset == 0) ;`
(X/osfmk/arm/model_dep.c:816), called unconditionally from debugger_collect_diagnostics (X/osfmk/kern/debug.c:1311), so every
panic spun there and never reached a reset. Fix: under ARM64_BOARD_CONFIG_SWEET, SavePanicInfo returns when panic_info ==
NULL ONLY (a mapped region with eph_panic_log_offset == 0 keeps the stock loop unchanged). Reset path after the return, read
from source: with debug=0x8 (DB_KPRT only) DB_NMI is clear so panicDebugging stays FALSE (debug.c:146, :405-407);
kdp_explicitly_requested is false so the KDP trap is skipped (:1414); DB_REBOOT_ALWAYS (:1408) is also reachable now; the
panicDebugging block (:1440-1448, panic_spin_shmcon) is skipped; `if (!panicDebugging)
kdp_machine_reboot_type(kPEPanicRestartCPU, ...)` (:1458-1459) -> PEHaltRestart (:1177) -> PE_halt_restart =
sweet_halt_restart (bsd/dev/sweet_restart.c:70, :230). With debug=0xc (DB_NMI|DB_KPRT; the default image boot-args until T-35ba,
tools/build-xnu-image.sh) panicDebugging is TRUE: panic_spin_shmcon (:1448) then panic_spin_forever (:1463; :1414-1464 is
the tail of debugger_collect_diagnostics), so 0xc still spins, now after the log instead of before it; the boot GAP line says
which case applies. Two more NULL dereferences were in the way and are guarded: (1) kern_dump -> do_kern_dump dereference
panic_info (X/osfmk/kdp/kdp_core.c:1090-1093, :1233-1254; also :629, :761); kern_dump (kdp_core.c:1316) is the single entry
for all three callers (debug.c:1383 KERN_DUMP_DISK, kdp_udp.c:2123 KERN_DUMP_NET, out_shmem.c:514 KERN_DUMP_HW_SHMEM_DBG) and
now returns -1 first when panic_info is NULL; it does not rely on gIOPolledCoreFileVars being NULL (kdp_core.c:170-172). (2)
debug.c:1340-1402 (`if (on_device_corefile_enabled())`) writes panic_info->eph_panic_flags at :1347/:1349 when debug= has
DB_KERN_DUMP_ON_PANIC|NMI (0x400/0x800, debug.h:523-524) and no polled corefile; that block is skipped when panic_info is
NULL. A third: nested panics call PE_update_panicheader_nestedpanic, which asserts on panic_info == NULL in a DEVELOPMENT
build (pe_init.c:680-683, review pr-0182-r1 F5); both call sites (debug.c:765 Stage 1 bailout, :1267-1271 in
debugger_collect_diagnostics) are skipped when panic_info is NULL. Loud, rule 8: debug_log_init (debug.c:469-473; runs from
machine_init, X/osfmk/arm/machine_routines_common.c:1428, and from kdp_init, kdp_udp.c:2239, so a file-scope flag prints
once) prints at boot, at line start, `SWEET GAP: no panic region (...); panic log not saved, panics spin (debug= has DB_NMI
0x4) | reset once /sweet-reset is set up (patch 0182; real fix PR-3)`, choosing by panicDebugging, which panic_init sets from
STARTUP_SUB_TUNABLES (debug.c:433, :405-407) before machine_init runs (osfmk/kern/startup.c:283 initialises upto LOCKS,
machine_init at :504); tools/xrun_report.py:27-29 counts it in gaps=N. The condition is !gPanicBase, which pe_init.c:158 sets
together with panic_info. The reset also needs gIOPlatform and PE_halt_restart set by sweet_restart_start on a board with
/sweet-reset (sweet_restart.c:215-230); a panic before that still hangs in halt_all_cpus after a printf (review F3b). NO line
is printed at panic time: kprintf spins on kprintf_lock without bound (X/pexpert/arm/pe_kprintf.c:114) and a core halted
inside kprintf would turn the reset back into a hang, and paniclog_append_noflush has no buffer (debug_buf_ptr unset,
debug_log_init returned early); the panic text itself is already drawn by sweet_early_panic (debug.c:1077). Real fix: PR-3 (a
reserved panic region). Host test tools/test_0182_panic_noregion.py. Not run on hardware.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import importlib.util, os, pathlib, re, sys, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCH = HERE.parent / "xnu" / "patches" / "0182-sweet-panic-noregion-reset.patch"
GAP = ("SWEET GAP: no panic region (no pram / embedded-panic-log-size in ADT); panic log not saved, panics %s "
       "(patch 0182; real fix PR-3)")
GAP_ARGS = 'panicDebugging ? "spin (debug= has DB_NMI 0x4)" : "reset once /sweet-reset is set up");'
KNOWN_GAPS = pathlib.Path(os.environ.get("KNOWN_GAPS") or os.path.expanduser("~/.t3/projects/pixelios/docs/KNOWN-GAPS.md"))
STOCK = "\twhile (!panic_info || panic_info->eph_panic_log_offset == 0) {"
SWEET = "#if defined(ARM64_BOARD_CONFIG_SWEET)"


def per_file(text):
    """{path: [diff body lines of that file]} (lines after the `+++` header)."""
    out, cur = {}, None
    for l in text.split("\n"):
        if l.startswith("diff --git "):
            cur = out.setdefault(l.split(" b/", 1)[1], [])
            hdr = True
        elif cur is not None and hdr and (l.startswith("@@")):
            hdr = False
            cur.append(l)
        elif cur is not None and not hdr:
            cur.append(l)
    return out


def xrun_mod():
    s = importlib.util.spec_from_file_location("xrun_report", HERE / "xrun_report.py")
    m = importlib.util.module_from_spec(s)
    sys.path.insert(0, str(HERE))
    s.loader.exec_module(m)
    return m


def xrun_gap_any():
    return xrun_mod().GAP_ANY


def known_gap_check():
    """review pr-0182-r1 F4: the boot GAP line (both variants) must be a KNOWN gap in the real KNOWN-GAPS.md. Returns failures."""
    if not KNOWN_GAPS.is_file():
        print("SKIP: %s not found (set KNOWN_GAPS): the all_gaps known-gap assertion was NOT run" % KNOWN_GAPS)
        return []
    mod, bad = xrun_mod(), []
    for variant in ("spin (debug= has DB_NMI 0x4)", "reset once /sweet-reset is set up"):
        top = mod.all_gaps(["[   1.000] " + GAP % variant])["top"]
        if len(top) != 1 or top[0]["new"] is not False:
            bad.append("(f) KNOWN-GAPS.md: the 0182 GAP line (%s) is not a known gap: %r" % (variant, top))
    return bad


def block_after(lines, i):
    """The lines of the `{ ... }` guard that starts at lines[i] (an `if` line ending in `{`), without the +/ prefixes."""
    body = []
    for l in lines[i + 1:]:
        if l[1:] == "\t}":
            return body
        body.append(l[1:].strip())
    return None


def check(text):
    """Return a list of failure strings (empty = PASS)."""
    bad = []
    files = per_file(text)
    for f in ("osfmk/arm/model_dep.c", "osfmk/kdp/kdp_core.c", "osfmk/kern/debug.c"):
        if f not in files:
            bad.append("patch does not touch " + f)
    if bad:
        return bad
    if any(l.startswith("-") for f in files.values() for l in f):
        bad.append("patch removes a line (stock behaviour must stay; only additions allowed)")

    # (a) SavePanicInfo: SWEET-only, panic_info == NULL only, stock loop untouched (context line right after #endif)
    md = files["osfmk/arm/model_dep.c"]
    ok = False
    for i, l in enumerate(md):
        if l == "+\tif (panic_info == NULL) {":
            body = block_after(md, i)
            nxt = md[i + 1 + len(body) + 1:i + 1 + len(body) + 3] if body is not None else []
            gate = [x for x in md[:i] if x.startswith("+#if") or x.startswith("+#endif")]
            ok = (body == ["return;"] and nxt[:1] == ["+#endif"] and nxt[1:2] == [" " + STOCK]
                  and gate and gate[-1] == "+" + SWEET)
    if not ok:
        bad.append("(a) model_dep.c: no SWEET-gated `if (panic_info == NULL) { return; }` right before the stock while loop")
    added = "\n".join(x for x in md if x.startswith("+") and not x.lstrip("+").lstrip().startswith(("*", "/*")))
    if "eph_panic_log_offset" in added:
        bad.append("(a) guard widened: eph_panic_log_offset appears in added code of model_dep.c")
    if STOCK not in [x[1:] for x in md if x.startswith(" ")]:
        bad.append("(a) the stock while loop is not an unchanged context line")

    # (b) kern_dump guard (SWEET, before do_kern_dump / kasan) and debug.c corefile block guard
    kd = files["osfmk/kdp/kdp_core.c"]
    kdp = "\n".join(kd)
    if not re.search(r"\+" + re.escape(SWEET) + r"\n\+\t/\*[^\n]*\*/\n\+\tif \(panic_info == NULL\) \{\n\+\t\treturn ret;\n\+\t\}\n\+#endif", kdp):
        bad.append("(b) kdp_core.c: no SWEET-gated `if (panic_info == NULL) return ret;` at the top of kern_dump")
    if " \tint ret = -1;" not in kd:
        bad.append("(b) kdp_core.c: guard is not placed in kern_dump (after `int ret = -1;`)")
    dbg = "\n".join(files["osfmk/kern/debug.c"])
    cm = r"\+[ \t]*/\*[^\n]*\*/\n"          # one added comment line
    ifs = re.escape("+" + SWEET) + r"\n"
    if not re.search(r"^ \t \*/\n" + ifs + r"\+\t/\*[^\n]*\*/\n\+\tif \(on_device_corefile_enabled\(\) && panic_info != NULL\) \{\n\+#else\n"
                     r" \tif \(on_device_corefile_enabled\(\)\) \{\n\+#endif\n", dbg, re.M):
        bad.append("(b) debug.c: corefile block not skipped when panic_info is NULL under SWEET (#if/comment/#else form)")
    # (e) nested-panic header update skipped when panic_info is NULL (F5): Stage 1 bailout and debugger_collect_diagnostics
    if not re.search(r'^ \t\tpaniclog_append_noflush\("Nested panic count exceeds[^\n]*\n' + ifs + r"\+\t\t/\*[^\n]*\*/\n\+\t\tif \(panic_info != NULL\) \{\n"
                     r"\+\t\t\tPE_update_panicheader_nestedpanic\(\);\n\+\t\t\}\n\+#else\n \t\tPE_update_panicheader_nestedpanic\(\);\n\+#endif\n", dbg, re.M):
        bad.append("(e) debug.c: Stage 1 bailout PE_update_panicheader_nestedpanic not guarded on panic_info under SWEET")
    if not re.search(r"^ \n" + ifs + r"\+\t\t/\*[^\n]*\*/\n\+\t\tif \(CPUDEBUGGERCOUNT > 1 && panic_info != NULL\) \{\n\+#else\n"
                     r" \t\tif \(CPUDEBUGGERCOUNT > 1\) \{\n\+#endif\n", dbg, re.M):
        bad.append("(e) debug.c: debugger_collect_diagnostics nested-panic update not guarded on panic_info under SWEET")

    # (c) the boot GAP line: exact text, once, at line start, SWEET-gated, counted by xrun_report.GAP_ANY
    gaps = re.findall(r'^\+\t\t\tprintf\("(SWEET GAP: [^"]*)\\n",$', dbg, re.M)
    if len(gaps) != 1 or gaps[0] != GAP:
        bad.append("(c) debug.c: expected exactly one printf of the GAP text, found %r" % gaps)
    else:
        m = xrun_gap_any().match(gaps[0] % "reset once /sweet-reset is set up")
        if not m or m.group(1) != "SWEET":
            bad.append("(c) GAP text is not matched by tools/xrun_report.py GAP_ANY")
    # the exact added blocks, in order, with their stock context (review F2/F7): flag at file scope above debug_log_init,
    # once-only printf after the stock gPanicBase error line and before the stock `return;`, both under the SWEET #if
    flag = (r"^ \n" + ifs + cm + r"\+static boolean_t sweet_noregion_gap_printed = FALSE;\n\+#endif\n\+\n void\n debug_log_init\(void\)\n")
    if not re.search(flag, dbg, re.M):
        bad.append("(c) debug.c: no SWEET-gated file-scope once-only flag right above debug_log_init")
    blk = (r'^ \t\tprintf\("debug_log_init: Error!! gPanicBase is still not initialized\\n"\);\n' + ifs +
           r"\+\t\t/\*[^\n]*\*/\n\+\t\tif \(!sweet_noregion_gap_printed\) \{\n\+\t\t\tsweet_noregion_gap_printed = TRUE;\n"
           r'\+\t\t\tprintf\("' + re.escape(GAP) .replace("%s", "%s") + r'\\n",\n\+\t\t\t    ' + re.escape(GAP_ARGS) +
           r"\n\+\t\t\}\n\+#endif\n \t\treturn;\n")
    if not re.search(blk, dbg, re.M):
        bad.append("(c) debug.c: the once-only GAP block in debug_log_init is not the exact expected form")
    return bad


def mutants(t):
    g = "+#if defined(ARM64_BOARD_CONFIG_SWEET)\n+\t/*\n+\t * PixOS patch 0182: this port"
    assert g in t
    return {
        "SavePanicInfo guard removed": re.sub(r"\+\tif \(panic_info == NULL\) \{\n\+\t\treturn;\n\+\t\}\n", "", t, count=1),
        "guard widened (|| eph_panic_log_offset == 0)": t.replace("+\tif (panic_info == NULL) {\n+\t\treturn;",
                                                                  "+\tif (panic_info == NULL || panic_info->eph_panic_log_offset == 0) {\n+\t\treturn;", 1),
        "guard not SWEET-gated": t.replace(g, g.replace("+#if defined(ARM64_BOARD_CONFIG_SWEET)", "+#if 1", 1), 1),
        "stock while loop changed": t.replace(" " + STOCK, " \twhile (!panic_info) {", 1),
        "kern_dump guard removed": re.sub(r"\+\tif \(panic_info == NULL\) \{\n\+\t\treturn ret;\n\+\t\}\n", "", t, count=1),
        "debug.c corefile guard removed": t.replace("+\tif (on_device_corefile_enabled() && panic_info != NULL) {",
                                                    "+\tif (on_device_corefile_enabled()) {", 1),
        "GAP line removed": re.sub(r'\+\t\t\tprintf\("SWEET GAP:[^\n]*\n', "", t, count=1),
        "GAP text not counted (SWEET GAPS:)": t.replace('printf("SWEET GAP: no panic', 'printf("SWEET GAPS: no panic', 1),
        "(i) GAP once-test inverted": t.replace("if (!sweet_noregion_gap_printed) {", "if (sweet_noregion_gap_printed) {", 1),
        "(ii) GAP flag never set": t.replace("+\t\t\tsweet_noregion_gap_printed = TRUE;\n", "", 1),
        "(iii) debug_log_init block #if 1": t.replace("+#if defined(ARM64_BOARD_CONFIG_SWEET)\n+\t\t/* PixOS patch 0182 (rule 8)", "+#if 1\n+\t\t/* PixOS patch 0182 (rule 8)", 1),
        "(iii) file-scope flag #if 1": t.replace("+#if defined(ARM64_BOARD_CONFIG_SWEET)\n+/* PixOS patch 0182 (rule 8)", "+#if 1\n+/* PixOS patch 0182 (rule 8)", 1),
        "(iv) corefile #if 1": t.replace("+#if defined(ARM64_BOARD_CONFIG_SWEET)\n+\t/* PixOS patch 0182: no panic region (panic_info == NULL): the corefile",
                                         "+#if 1\n+\t/* PixOS patch 0182: no panic region (panic_info == NULL): the corefile", 1),
        "GAP prints a fixed state (no panicDebugging)": t.replace("panicDebugging ? ", "1 ? ", 1),
        "file-scope flag declaration removed": t.replace("+static boolean_t sweet_noregion_gap_printed = FALSE;\n", "", 1),
        "stage-1 nestedpanic call unguarded": t.replace("+\t\tif (panic_info != NULL) {\n+\t\t\tPE_update", "+\t\tif (1) {\n+\t\t\tPE_update", 1),
        "collect_diagnostics nestedpanic unguarded": t.replace("if (CPUDEBUGGERCOUNT > 1 && panic_info != NULL) {", "if (CPUDEBUGGERCOUNT > 1) {", 1),
    }


def main(argv):
    patch = PATCH
    if argv[1:2] == ["--patch"] and len(argv) == 3:
        patch = pathlib.Path(argv[2])
    elif len(argv) != 1:
        print("usage: test_0182_panic_noregion.py [--patch FILE]\nRESULT: ERROR bad arguments")
        return 2
    try:
        text = patch.read_text()
        base = check(text) + known_gap_check()
        for b in base:
            print("FAIL:", b)
        caught = 0
        ms = mutants(text)
        for name, mt in ms.items():
            if mt == text:
                print("ERROR: mutant did not mutate:", name)
                return 2
            hit = check(mt)
            print("mutant %-48s %s" % (name, "caught" if hit else "SURVIVED"))
            caught += bool(hit)
    except (OSError, AssertionError, KeyError, IndexError) as e:
        print("RESULT: ERROR %s: %s" % (type(e).__name__, e))
        return 2
    if base or caught != len(ms):
        print("RESULT: FAIL base_failures=%d mutants_caught=%d/%d" % (len(base), caught, len(ms)))
        return 1
    print("RESULT: PASS patch checks ok, mutants_caught=%d/%d" % (caught, len(ms)))
    return 0


class Patch0182(unittest.TestCase):
    """The gate runs `python3 -m unittest discover -s tools -p FILE`; without a TestCase it ran 0 tests (review pr-0182-r1 F1)."""

    def test_patch_and_mutants(self):
        rc = main([sys.argv[0]])
        sys.stdout.flush()  # keep the mutant lines before unittest's summary when both go to one pipe
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
