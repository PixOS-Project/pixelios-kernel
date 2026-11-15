#!/usr/bin/env python3
"""mifclk_audit: the T-5030 audits of the --mif-clk request (shim/mifclk.c), moved out of tools/test-bluejay-boot.py (R29: that file may not grow).
Imported by test-bluejay-boot.py (sys.path has tools/). Text checks only: no process, no file written.

  check_mifclk_source_text / source_selftest   shim/mifclk.c: no MMIO of its own; asks the audited ACPM DVFS client (acpm.c) for two GETs and ONE MIF top-row request
  audit()                                      what _audit_main runs for them (source audit, mutants, the audit object and the normal object)

The helpers of test-bluejay-boot.py (comment stripping, function span) are handed in by bind(); there is no second copy of them here.

  tools/mifclk_audit.py --self-test    binds the helpers of test-bluejay-boot.py and runs the mutation self-test on shim/mifclk.c
Last line: RESULT: PASS|FAIL mifclk_audit ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import importlib.util
import pathlib
import re
import sys

_strip = None
_span = None


def bind(strip_c_comments, c_function_span) -> None:
    global _strip, _span
    _strip, _span = strip_c_comments, c_function_span


ALLOWED_UNDEF = {
    "con_str", "counter_freq", "wait_ticks",                                                # console, timer
    "acpm_cfg_init", "acpm_why", "acpm_describe", "acpm_dvfs_get", "acpm_dvfs_mif_top",     # the audited ACPM client: reads + the MIF-top-only request (writes audited in acpm.o)
}


def check_mifclk_source_text(src: str, name: str = "mifclk.c") -> None:
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in ("try_write32", "try_read32", "acpm_dvfs_set", "acpm_dvfs_reassert", "acpm_pmic_update", "acpm_pmic_read", "acpm_s11_rail_set", "w32"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (no MMIO of its own; only acpm_dvfs_get / acpm_dvfs_mif_top reach the ACPM client)")
    if len(re.findall(r"\bvolatile\b", code)) != 3 or code.count("volatile char *cmd") != 3:
        raise AssertionError(f"{name}: 'volatile' is allowed only in cmd_put and the two mif_clk_cmdline(volatile char *cmd, ...) definitions")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    spans = [_span(code, f) for f in ("put_txt", "put_dec", "mif_clk_cmdline")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside the string helpers and the command-line scan/copy (retry loops are not allowed: no write is retried)")
    pda, pdb = _span(code, "put_dec")
    for m in re.finditer(r"\bfor\s*\(", code):
        if not (pda <= m.start() < pdb):
            raise AssertionError(f"{name}: 'for' only inside put_dec (a loop around mif_run / the request is a retry)")
    ca0, cb0 = _span(code, "mif_clk_cmdline")
    cb0s = re.sub(r"\s+", "", code[ca0:cb0])
    if cb0s.count("while(") != 2 or "while(n+1<cap&&cmd[n])n++;" not in cb0s or "while(s[i]&&n+1<cap)cmd_put(cmd,n++,s[i++]);" not in cb0s:
        raise AssertionError(f"{name}: mif_clk_cmdline has exactly two bounded while loops (scan and copy)")
    if len(re.findall(r"\bmif_run\b", code)) != 2 or len(re.findall(r"\bacpm_dvfs_mif_top\b", code)) != 1 or len(re.findall(r"\bgates\s*\(", code)) != 2:
        raise AssertionError(f"{name}: mif_run (definition + one call), acpm_dvfs_mif_top (one site, no alias) and gates (definition + one call) must each appear exactly so")
    if re.findall(r"(?m)^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b", src) != ["if", "elif", "endif"]:
        raise AssertionError(f"{name}: preprocessor conditionals must be exactly #if / #elif / #endif (no #if 0 around a gate)")
    if len(re.findall(r"\bacpm_dvfs_mif_top\s*\(", code)) != 1 or "e=acpm_dvfs_mif_top(c,&t);" not in norm:
        raise AssertionError(f"{name}: exactly one request site is allowed: acpm_dvfs_mif_top(c, &t)")
    if "#defineIDX_MIFACPM_DVFS_MIF_IDX" not in norm:
        raise AssertionError(f"{name}: IDX_MIF must be the soc.json MIF index")
    a, b = _span(code, "mif_run")
    body = re.sub(r"\s+", "", code[a:b])
    req = body.find("acpm_dvfs_mif_top(")
    pre = body[:req]
    for need in ("if(!gates(c))returnRC_GATE;", 'if(get_dom(c,IDX_MIF,&v[0],&t,"getMIFbefore"))returnRC_GET;',
                 'if(get_dom(c,IDX_INT,&v[1],&t,"getINTbefore"))returnRC_GET;'):
        if need not in pre:
            raise AssertionError(f"{name}: '{need}' must come before the request")
    if body.count("acpm_dvfs_mif_top(") != 1 or "if(e==ACPM_E_ON){" not in body or body.find("wait_ticks(") < req:
        raise AssertionError(f"{name}: the MIF-already-at-top branch and the settle wait must follow the single request")
    ga, gb = _span(code, "gates")
    gbody = re.sub(r"\s+", "", code[ga:gb])
    for need in ("if(!c->dvfs_ch||!c->layout_verified||!c->elem_unit_verified||!counter_freq()){", "e=acpm_describe(c,&h);if(e){", "if(h.rr!=h.rf||h.sr!=h.sf){"):
        if need not in gbody:
            raise AssertionError(f"{name}: gates() must contain '{need}'")
    ca, cb = _span(code, "mif_clk_cmdline")
    cbody = re.sub(r"\s+", "", code[ca:cb])
    if cbody.find("n+MIF_TOK_MAX>=cap") < 0 or cbody.find("n+MIF_TOK_MAX>=cap") > cbody.find("mif_run("):
        raise AssertionError(f"{name}: the command-line room check must run BEFORE mif_run (no hardware write whose result is dropped)")


def mifclk_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "mifclk.c").read_text()
    check_mifclk_source_text(good)  # control must pass
    cases = [
        ("inline asm", good.replace("static void say(", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic void say(', 1)),
        ("a raw MMIO writer", good.replace("static void say(", "static void evil(void) { try_write32(0x1c400140UL, 0x80000010u); }\nstatic void say(", 1)),
        ("a pointer cast", good.replace("static void say(", "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\nstatic void say(", 1)),
        ("the CPU request function", good.replace("e = acpm_dvfs_mif_top(c, &t);", "e = acpm_dvfs_set(c, IDX_INT, 100000, &t);", 1)),
        ("the GPU request function", good.replace("e = acpm_dvfs_mif_top(c, &t);", "e = acpm_dvfs_reassert(c, IDX_INT, 100000, &t);", 1)),
        ("a PMIC writer", good.replace("wait_ticks(counter_freq() / 100);", "acpm_pmic_update(c, 0, 0, 0); wait_ticks(counter_freq() / 100);", 1)),
        ("a second request site", good.replace("wait_ticks(counter_freq() / 100);", "(void)acpm_dvfs_mif_top(c, &t); wait_ticks(counter_freq() / 100);", 1)),
        ("a retry loop", good.replace("\tif (e == ACPM_E_ON) {", "\twhile (e)\n\t\te = acpm_dvfs_mif_top(c, &t);\n\tif (e == ACPM_E_ON) {", 1)),
        ("the other index for MIF", good.replace("#define IDX_MIF\t\tACPM_DVFS_MIF_IDX", "#define IDX_MIF\t\t1", 1)),
        ("no gates() call", good.replace("if (!gates(c))\n\t\treturn RC_GATE;", "", 1)),
        ("a failing MIF GET does not stop", good.replace('if (get_dom(c, IDX_MIF, &v[0], &t, "get MIF before"))\n\t\treturn RC_GET;', 'get_dom(c, IDX_MIF, &v[0], &t, "get MIF before");', 1)),
        ("a failing INT GET does not stop", good.replace('if (get_dom(c, IDX_INT, &v[1], &t, "get INT before"))\n\t\treturn RC_GET;', 'get_dom(c, IDX_INT, &v[1], &t, "get INT before");', 1)),
        ("queues not checked", good.replace("if (h.rr != h.rf || h.sr != h.sf) {", "if (0) {", 1)),
        ("no layout gate", good.replace("!c->layout_verified ||", "", 1)),
        ("no describe refusal", good.replace("\tif (e) {\n\t\tsay(\"GAP: \", acpm_why(h.why), 0, 0);", "\tif (0) {\n\t\tsay(\"GAP: \", acpm_why(h.why), 0, 0);", 1)),
        ("room checked after the request", good.replace("n + MIF_TOK_MAX >= cap", "0", 1)),
        ("a for retry around the request", good.replace("\te = acpm_dvfs_mif_top(c, &t);", "\tfor (e = 1; e; e = acpm_dvfs_mif_top(c, &t))\n\t\t;", 1)),
        ("mif_run called twice", good.replace("\trc = mif_run(&c, v);", "\trc = mif_run(&c, v);\n\trc = mif_run(&c, v);", 1)),
        ("a function-pointer alias of the request", good.replace("static uint32_t mif_run(", "static int (*const req_fn)(const struct acpm_cfg *, struct acpm_trace *) = acpm_dvfs_mif_top;\nstatic uint32_t mif_run(", 1)),
        ("#if 0 around the gates call", good.replace("\tif (!gates(c))\n\t\treturn RC_GATE;", "#if 0\n\tif (!gates(c))\n\t\treturn RC_GATE;\n#endif", 1)),
        ("a while retry of the request", good.replace("\tif (e == ACPM_E_ON) {", "\twhile (e && e != ACPM_E_ON)\n\t\te = acpm_dvfs_mif_top(c, &t);\n\tif (e == ACPM_E_ON) {", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"mifclk source selftest case did not mutate: {label}"
        try:
            check_mifclk_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: mifclk source {label} -> {exc}")
        else:
            raise AssertionError(f"mifclk source self-test FAILED to catch: {label}")
    print(f"PASS mifclk source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def check_mifclk_level_text(gpudom: str, mifdom: str, build_sh: str, acpm_h: str, name: str = "acpm_gpudom.h") -> None:
    """ALL-ON T-02 (--mif-clk-level): the exact set of requests the image can make. MIF is asked at ONE rate, ACPM_DVFS_MIF_REQ_KHZ (the top row, or the validated
    MIF_CLK_LEVEL_KHZ); class 2 passes only that rate on dom MIF; the level must be a generated table row (compile-time); build.sh passes only boards.py's validated output
    to the compiler. Text checks of shim/acpm_gpudom.h, shim/acpm_mifdom.h, shim/acpm.h and shim/build.sh."""
    g = re.sub(r"\s+", "", _strip(gpudom))
    m = re.sub(r"\s+", "", _strip(mifdom))
    b = re.sub(r"\s+", "", build_sh)
    if m.count("dvfs_set(") != 1 or "returndvfs_set(c,ACPM_DVFS_MIF_IDX,ACPM_DVFS_MIF_REQ_KHZ,t,2);" not in m:
        raise AssertionError("acpm_mifdom.h: exactly one request: acpm_dvfs_mif_top = dvfs_set(c, ACPM_DVFS_MIF_IDX, ACPM_DVFS_MIF_REQ_KHZ, t, 2)")
    if "MIF_CLK_LEVEL_KHZ" in m or "ACPM_DVFS_MIF_KHZ" in m:
        raise AssertionError("acpm_mifdom.h: the request rate is ACPM_DVFS_MIF_REQ_KHZ only (no second rate source)")
    if g.count("#defineACPM_DVFS_MIF_REQ_KHZMIF_CLK_LEVEL_KHZ") != 1 or g.count("#defineACPM_DVFS_MIF_REQ_KHZACPM_DVFS_MIF_KHZ") != 1:
        raise AssertionError(f"{name}: ACPM_DVFS_MIF_REQ_KHZ must be defined exactly twice: MIF_CLK_LEVEL_KHZ (level image) and ACPM_DVFS_MIF_KHZ (top row)")
    if "#ifdefMIF_CLK_LEVEL_KHZ#ifndefMIF_CLK#error" not in g or g.count("#error") != 1:
        raise AssertionError(f"{name}: MIF_CLK_LEVEL_KHZ without MIF_CLK must be a #error")
    if "#if defined(MIF_CLK_LEVEL_KHZ) && !defined(MIF_CLK)\n#error \"MIF_CLK_LEVEL_KHZ needs MIF_CLK\"" not in acpm_h:
        raise AssertionError("acpm.h: MIF_CLK_LEVEL_KHZ without MIF_CLK must be a #error where acpm.c always sees it (acpm_gpudom.h is only included by writer images)")
    if '_Static_assert(ACPM_DVFS_MIF_LEVEL_OK(MIF_CLK_LEVEL_KHZ),"' not in g or g.count("_Static_assert(") != 1:
        raise AssertionError(f"{name}: the level must be checked against the generated MIF row list (ACPM_DVFS_MIF_LEVEL_OK) at compile time (exactly one static assert)")
    if g.count("cls!=2||(dom==ACPM_DVFS_MIF_IDX&&khz==ACPM_DVFS_MIF_REQ_KHZ);") != 1 or "MIF_KHZ)" in g.replace("ACPM_DVFS_MIF_REQ_KHZ)", ""):
        raise AssertionError(f"{name}: class 2 must pass exactly dom MIF at ACPM_DVFS_MIF_REQ_KHZ, no other rate comparison")
    if "if(cls<0||cls>2||dvfs_is_gpu" not in g:
        raise AssertionError(f"{name}: the caller class is bounded (0..2): a class without a wrapper must not reach a domain")
    # build.sh: the only -DMIF_CLK_LEVEL_KHZ for the shipped image comes from boards.py's output, after the refusal (|| exit 2)
    if b.count('CFLAGS="$CFLAGS-DMIF_CLK_LEVEL_KHZ=$mif_lvl"') != 1:
        raise AssertionError("build.sh: exactly one CFLAGS -DMIF_CLK_LEVEL_KHZ=$mif_lvl (the validated value)")
    if 'mif_lvl=$("$PYTHON"../tools/boards.py"$board"--mif-clk-level-check"$mif_clk_level")||exit2' not in b or b.find("mif_lvl=$(") > b.find('CFLAGS="$CFLAGS-DMIF_CLK_LEVEL_KHZ='):
        raise AssertionError("build.sh: the level must be validated by boards.py mif_clk_level and refused (exit 2) BEFORE the compiler flag is built")


def mifclk_level_selftest() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    gpudom = (root / "shim" / "acpm_gpudom.h").read_text()
    mifdom = (root / "shim" / "acpm_mifdom.h").read_text()
    build = (root / "shim" / "build.sh").read_text()
    acpm_h = (root / "shim" / "acpm.h").read_text()
    check_mifclk_level_text(gpudom, mifdom, build, acpm_h)  # control must pass
    cases = [
        ("request at the top row again", "m", mifdom.replace("ACPM_DVFS_MIF_REQ_KHZ, t, 2", "ACPM_DVFS_MIF_KHZ, t, 2", 1)),
        ("a second MIF request rate", "m", mifdom.replace("return dvfs_set(c, ACPM_DVFS_MIF_IDX, ACPM_DVFS_MIF_REQ_KHZ, t, 2);", "(void)dvfs_set(c, ACPM_DVFS_MIF_IDX, 421000, t, 2);\n\treturn dvfs_set(c, ACPM_DVFS_MIF_IDX, ACPM_DVFS_MIF_REQ_KHZ, t, 2);", 1)),
        ("request class 3 from the MIF function", "m", mifdom.replace("ACPM_DVFS_MIF_REQ_KHZ, t, 2", "ACPM_DVFS_MIF_REQ_KHZ, t, 3", 1)),
        ("the level macro used directly in the request", "m", mifdom.replace("ACPM_DVFS_MIF_REQ_KHZ, t, 2", "MIF_CLK_LEVEL_KHZ, t, 2", 1)),
        ("level image falls back to the top row", "g", gpudom.replace("#define ACPM_DVFS_MIF_REQ_KHZ\tMIF_CLK_LEVEL_KHZ", "#define ACPM_DVFS_MIF_REQ_KHZ\tACPM_DVFS_MIF_KHZ", 1)),
        ("level without MIF_CLK allowed", "g", gpudom.replace('#error "MIF_CLK_LEVEL_KHZ needs MIF_CLK"', "", 1)),
        ("row static assert removed", "g", gpudom.replace("_Static_assert(ACPM_DVFS_MIF_LEVEL_OK(MIF_CLK_LEVEL_KHZ),", "_Static_assert(1,", 1)),
        ("class 2 compares the top row", "g", gpudom.replace("khz == ACPM_DVFS_MIF_REQ_KHZ", "khz == ACPM_DVFS_MIF_KHZ", 1)),
        ("class 2 compares a row list instead of the one rate", "g", gpudom.replace("khz == ACPM_DVFS_MIF_REQ_KHZ", "ACPM_DVFS_MIF_LEVEL_OK(khz)", 1)),
        ("class bound removed", "g", gpudom.replace("cls < 0 || cls > 2 || dvfs_is_gpu", "dvfs_is_gpu", 1)),
        ("acpm.h level-without-MIF_CLK error removed", "a", acpm_h.replace('#error "MIF_CLK_LEVEL_KHZ needs MIF_CLK"', "", 1)),
        ("build.sh passes the raw text", "b", build.replace("-DMIF_CLK_LEVEL_KHZ=$mif_lvl", "-DMIF_CLK_LEVEL_KHZ=$mif_clk_level", 1)),
        ("build.sh ignores the refusal", "b", build.replace('--mif-clk-level-check "$mif_clk_level") || exit 2', '--mif-clk-level-check "$mif_clk_level") || true', 1)),
        ("build.sh a second flag site", "b", build.replace('echo "NOTE --mif-clk-level:', 'CFLAGS="$CFLAGS -DMIF_CLK_LEVEL_KHZ=$mif_lvl"; echo "NOTE --mif-clk-level:', 1)),
    ]
    caught = 0
    for label, which, bad in cases:
        src = {"g": gpudom, "m": mifdom, "b": build, "a": acpm_h}[which]
        assert bad != src, f"mifclk level selftest case did not mutate: {label}"
        args = {"g": (bad, mifdom, build, acpm_h), "m": (gpudom, bad, build, acpm_h), "b": (gpudom, mifdom, bad, acpm_h), "a": (gpudom, mifdom, build, bad)}[which]
        try:
            check_mifclk_level_text(*args, name="selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: mifclk level {label} -> {exc}")
        else:
            raise AssertionError(f"mifclk level self-test FAILED to catch: {label}")
    print(f"PASS mifclk level self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def audit(root: pathlib.Path, out_dir: pathlib.Path, memo_obj, obj_selftest, obj_check, obj_info, strip_c_comments, c_function_span) -> None:
    """Everything _audit_main runs for --mif-clk: source audit + mutants, then the -DMIF_CLK audit object and the normal object."""
    bind(strip_c_comments, c_function_span)
    mifclk_source_selftest()
    check_mifclk_source_text((root / "shim" / "mifclk.c").read_text())
    mifclk_level_selftest()
    check_mifclk_level_text((root / "shim" / "acpm_gpudom.h").read_text(), (root / "shim" / "acpm_mifdom.h").read_text(), (root / "shim" / "build.sh").read_text(), (root / "shim" / "acpm.h").read_text())
    print("PASS mifclk level audit (--mif-clk-level): one request rate ACPM_DVFS_MIF_REQ_KHZ, class 2 passes only that rate on MIF, the level is a generated mif_int_map row (compile time), "
          "build.sh passes only boards.py's validated value")
    print("PASS mifclk source audit: no asm / pointer casts / MMIO accessors; volatile only in the cmdline signature; no acpm_dvfs_set / reassert / PMIC writer; "
          "one request site acpm_dvfs_mif_top(c, &t) after the gates, the MIF GET and the INT GET; room checked before any ACPM traffic; no retry loops")
    memo_obj(obj_selftest, out_dir / "mifclk_audit.o", ALLOWED_UNDEF, "mifclk")
    n_stores = obj_check(out_dir / "mifclk_audit.o", ALLOWED_UNDEF)
    if "<mif_clk_cmdline>:" not in obj_info(out_dir / "mifclk_audit.o")[0]:
        raise AssertionError("mifclk_audit.o is empty: the -DMIF_CLK audit object must contain mif_clk_cmdline")
    obj_check(out_dir / "mifclk.o", ALLOWED_UNDEF)
    print(f"PASS mifclk object audit: no try_write32/try_read32 reference; {n_stores} stores, all stack/argument based; undef subset (the audited ACPM client, console, timer)")
    # T-24: the PMIC reset-source reader/clear audit (tools/pmicsrc_audit.py) runs from here: same ACPM-client object family and helper set, and
    # test-bluejay-boot.py may not grow (R29) nor _audit_main change (FUNC-LONG). Removing this call drops the audit: test_pmicsrc_audit.Wiring pins it.
    import pmicsrc_audit  # noqa: E402  (sibling module, tools/ is on sys.path via test-bluejay-boot.py)
    pmicsrc_audit.audit(root, out_dir, memo_obj, obj_selftest, obj_check, obj_info, strip_c_comments, c_function_span)
    # xnu patch 0363 (LOWCLK B1/B2): the --sweet-probe read-only probes (PSCI queries, SLC descriptors, one PT_VERSION), same wiring; test_sweetprobe_audit.Wiring pins it.
    import sweetprobe_audit  # noqa: E402
    sweetprobe_audit.audit(root, out_dir, memo_obj, obj_selftest, obj_check, obj_info, strip_c_comments, c_function_span)


def self_test() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("tbb", root / "tools" / "test-bluejay-boot.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root / "tools"))
    spec.loader.exec_module(mod)
    bind(mod._strip_c_comments, mod._c_function_span)
    try:
        n = mifclk_source_selftest() + mifclk_level_selftest()
    except AssertionError as exc:
        print(f"RESULT: FAIL mifclk_audit self-test: {exc}")
        return 1
    print(f"RESULT: PASS mifclk_audit self-test {n} mutants rejected")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL mifclk_audit is a module imported by tools/test-bluejay-boot.py; run with --self-test")  # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
