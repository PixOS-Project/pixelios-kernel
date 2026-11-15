#!/usr/bin/env python3
"""sweetprobe_audit: the audits of the --sweet-probe read-only probes (shim/sweetprobe.c, shim/sweetprobe_smc.c and the SLC client of shim/acpm.c; xnu patch 0363, docs/research/161),
run from mifclk_audit.audit like pmicsrc_audit (tools/test-bluejay-boot.py may not grow, R29). Text checks only: no process, no file written.

  check_sweetprobe_source_text / source_selftest   shim/sweetprobe.c: no MMIO and no asm of its own; asks the audited ACPM client for two descriptor reads and ONE acpm_slc_version; the PSCI ids
                                                   come from a fixed table; room checked before any query; no retry loops
  check_sweetprobe_smc_source_text                 shim/sweetprobe_smc.c: exactly one `smc #0`, one function, nothing else
  check_acpm_slc_source_text                       shim/acpm.c: the SLC request is ONE fixed PT_VERSION (0xd005) call of xfer_eb; no PT_CHECK / ENABLE / MUTATE / DISABLE / PMON opcode literal
  audit()                                          what _audit_main runs for them (sources, mutants, the audit objects)

  tools/sweetprobe_audit.py --self-test    binds the helpers of test-bluejay-boot.py and runs the mutation self-tests
Last line: RESULT: PASS|FAIL sweetprobe_audit ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
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
    "con_str", "counter_freq",                                                    # console, timer
    "acpm_cfg_init", "acpm_why", "acpm_slc_describe", "acpm_slc_version",          # the audited ACPM client: descriptor reads + the one PT_VERSION (writes audited in acpm.o)
    "sweetprobe_smc",                                                             # the one PSCI query (sweetprobe_smc.o, audited below)
}
OPCODE_RE = re.compile(r"\b0[xX](?:d00[0-4]|6[abAB]|2[0-7])(?![0-9A-Fa-f])")      # PT_ENABLE/DISABLE/CHECK/MUTATE, trace 0x6a/0x6b, PMON 0x20-0x27 [src slc_acpm.c:29-39, slc_pmon_internal.h:18-25]
PSCI_IDS = {"0x84000000u", "0x8400000Au", "0xC4000001u", "0x84000002u", "0x84000001u"}


def check_sweetprobe_source_text(src: str, name: str = "sweetprobe.c") -> None:
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned (the one smc lives in sweetprobe_smc.c)")
    for bad in ("try_write32", "try_read32", "acpm_dvfs_set", "acpm_dvfs_reassert", "acpm_dvfs_mif_top", "acpm_dvfs_get", "acpm_pmic_update", "acpm_pmic_read", "acpm_s11_rail_set", "w32", "xfer", "xfer_eb"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (no MMIO of its own; only acpm_slc_describe / acpm_slc_version reach the ACPM client)")
    if len(re.findall(r"\bvolatile\b", code)) != 3 or code.count("volatile char *cmd") != 3:
        raise AssertionError(f"{name}: 'volatile' is allowed only in cmd_put and the two sweet_probe_cmdline(volatile char *cmd, ...) definitions")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    if OPCODE_RE.search(code):
        raise AssertionError(f"{name}: an SLC opcode literal (PT_ENABLE/DISABLE/CHECK/MUTATE, trace, PMON) appears in the probe")
    if re.findall(r"(?m)^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b", src) != ["if", "elif", "endif"]:
        raise AssertionError(f"{name}: preprocessor conditionals must be exactly #if / #elif / #endif (no #if 0 around a gate)")
    ids = set(re.findall(r"0[xX][0-9A-Fa-f]{8}u", code))
    if ids != PSCI_IDS | {"0xFFFFFFFFu"}:
        raise AssertionError(f"{name}: the 32-bit constants are exactly the five PSCI ids and the NOT_SUPPORTED fill, got {sorted(ids)}")
    if len(re.findall(r"\bsweetprobe_smc\s*\(", code)) != 5 or norm.count("sweetprobe_smc(PSCI_FN_VERSION,0)") != 1 or norm.count("sweetprobe_smc(PSCI_FN_FEATURES,") != 3:
        raise AssertionError(f"{name}: sweetprobe_smc has one declaration and exactly four calls: VERSION and FEATURES x3, ids from the fixed table")
    if norm.count("elsewhere") or len(re.findall(r"\bpsci_call\s*\(", code)) != 2:
        raise AssertionError(f"{name}: psci_call (definition + one call site in psci_run)")
    spans = [_span(code, f) for f in ("put_txt", "put_dec", "sweet_probe_cmdline")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside the string helpers and the command-line scan/copy (retry loops are not allowed: no request is retried)")
    pda, pdb = _span(code, "put_dec")
    pra, prb = _span(code, "psci_run")
    for m in re.finditer(r"\bfor\s*\(", code):
        if not (pda <= m.start() < pdb or pra <= m.start() < prb):
            raise AssertionError(f"{name}: 'for' only inside put_dec and the fixed PSCI loop (a loop around the SLC request is a retry)")
    ca0, cb0 = _span(code, "sweet_probe_cmdline")
    cb0s = re.sub(r"\s+", "", code[ca0:cb0])
    if cb0s.count("while(") != 2 or "while(n+1<cap&&cmd[n])n++;" not in cb0s or "while(s[i]&&n+1<cap)cmd_put(cmd,n++,s[i++]);" not in cb0s:
        raise AssertionError(f"{name}: sweet_probe_cmdline has exactly two bounded while loops (scan and copy)")
    if cb0s.find("n+SP_TOK_MAX>=cap") < 0 or cb0s.find("n+SP_TOK_MAX>=cap") > min(cb0s.find("psci_run("), cb0s.find("slc_run(")):
        raise AssertionError(f"{name}: the command-line room check must run BEFORE any query or ACPM traffic")
    if len(re.findall(r"\bacpm_slc_version\s*\(", code)) != 1 or "e=acpm_slc_version(c,&o->ver,&t);" not in norm:
        raise AssertionError(f"{name}: exactly one request site is allowed: acpm_slc_version(c, &o->ver, &t)")
    if len(re.findall(r"\bacpm_slc_describe\s*\(", code)) != 1 or norm.count("slc_desc(c,ACPM_SLC_CH,") != 1 or norm.count("slc_desc(c,ACPM_SLC_ASYNC_CH,") != 1:
        raise AssertionError(f"{name}: the descriptor read has one call site (slc_desc) used for channel 10 and channel 11 only")
    sa, sb = _span(code, "slc_run")
    body = re.sub(r"\s+", "", code[sa:sb])
    req = body.find("acpm_slc_version(")
    pre = body[:req]
    for need in ("e=slc_desc(c,ACPM_SLC_CH,&h10,&o->c10a,&o->c10b);", "if(e){o->rc=RC_DESC;return;}", "if(h10.rr!=h10.rf||h10.sr!=h10.sf){o->rc=RC_BUSY;"):
        if need not in pre:
            raise AssertionError(f"{name}: '{need}' must come before the request")
    if body.count("acpm_slc_version(") != 1 or body.count("return;") < 2:
        raise AssertionError(f"{name}: slc_run has one request and returns on every refusal before it")


def check_sweetprobe_smc_source_text(src: str, name: str = "sweetprobe_smc.c") -> None:
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if len(re.findall(r"\b__asm__\b", code)) != 5 or norm.count('__asm__volatile("smc#0"') != 1 or norm.count('__asm__("x') != 4:
        raise AssertionError(f"{name}: exactly one asm statement, `smc #0`, plus the four register bindings x0-x3")
    if len(re.findall(r"\bsweetprobe_smc\s*\(", code)) != 1:
        raise AssertionError(f"{name}: exactly one function, sweetprobe_smc")
    if re.search(r"\b(for|while|do|goto|try_write32|try_read32)\b", code):
        raise AssertionError(f"{name}: no loops and no MMIO accessors")
    if any(f'{r}__asm__("{r}")={v};' not in norm for r, v in (("x0", "fn"), ("x1", "a1"), ("x2", "0"), ("x3", "0"))):
        raise AssertionError(f"{name}: the call is x0 = fn, x1 = a1, x2 = x3 = 0")
    if re.findall(r"(?m)^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b", src) != ["ifdef", "endif"]:
        raise AssertionError(f"{name}: one #ifdef SWEET_PROBE only")


def check_acpm_slc_source_text(src: str, name: str = "acpm.c") -> None:
    """The SLC request of acpm.c: ONE fixed PT_VERSION (soc.json acpm.slc.pt_version) through xfer_eb; no other SLC opcode literal; the describe never sends."""
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if len(re.findall(r"\bACPM_SLC_PT_VERSION\b", code)) != 1:
        raise AssertionError(f"{name}: ACPM_SLC_PT_VERSION must be the request word in exactly one place")
    if OPCODE_RE.search(code):
        raise AssertionError(f"{name}: an SLC opcode literal (0xd000-0xd004, 0x6a/0x6b, 0x20-0x27) appears in acpm.c")
    if len(re.findall(r"\bxfer_eb\s*\(", code)) != 3 or len(re.findall(r"\bXM_SLCV\b", code)) != 3:
        raise AssertionError(f"{name}: xfer_eb = definition + the xfer wrapper + the one SLC call; XM_SLCV = enum + the reply branch + the one SLC call")
    call = "e=xfer_eb(&d,XM_SLCV,0u,0u,ACPM_SLC_PT_VERSION,0u,t,&t->a,h.esz_b);"
    if norm.count(call) != 1 or "returnxfer_eb(c,mode,w0,w1,w2,w3,t,x,ACPM_ELEM_BYTES);" not in norm:
        raise AssertionError(f"{name}: the SLC request is exactly {call} and xfer is the 16-byte wrapper")
    i = code.find("int acpm_slc_version(")
    a, b = _span(code[i:], "acpm_slc_version")
    body = re.sub(r"\s+", "", code[i + a:i + b])
    if "describe_n(&d,&h,EB_16|EB_32)" not in body or body.find("describe_n(") > body.find("xfer_eb(") or "refuse(&t->a,e,h.why)" not in body:
        raise AssertionError(f"{name}: acpm_slc_version describes the channel first and refuses on failure before the request")
    if re.search(r"\b(for|while)\b", body):
        raise AssertionError(f"{name}: acpm_slc_version must not loop (no retry)")
    j = code.find("int acpm_slc_describe(")
    a, b = _span(code[j:], "acpm_slc_describe")
    dbody = re.sub(r"\s+", "", code[j + a:j + b])
    if "xfer" in dbody or "try_write32" in dbody or "ch!=ACPM_SLC_CH&&ch!=ACPM_SLC_ASYNC_CH" not in dbody:
        raise AssertionError(f"{name}: acpm_slc_describe reads only and takes channel 10 / 11 only")
    if "if(c->ch_id>=32||(eb!=ACPM_ELEM_BYTES&&eb!=2u*ACPM_ELEM_BYTES))" not in norm:
        raise AssertionError(f"{name}: xfer_eb accepts the element sizes 16 and 32 only")


def _good(root: pathlib.Path):
    return ((root / "shim" / "sweetprobe.c").read_text(), (root / "shim" / "sweetprobe_smc.c").read_text(), (root / "shim" / "acpm.c").read_text())


def sweetprobe_source_selftest() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    good, smc, acpm = _good(root)
    check_sweetprobe_source_text(good)  # controls must pass
    check_sweetprobe_smc_source_text(smc)
    check_acpm_slc_source_text(acpm)
    cases = [
        ("inline asm", check_sweetprobe_source_text, good.replace("static void say(", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic void say(', 1)),
        ("a raw MMIO writer", check_sweetprobe_source_text, good.replace("static void say(", "static void evil(void) { try_write32(0x1c400140UL, 0x80000010u); }\nstatic void say(", 1)),
        ("a pointer cast", check_sweetprobe_source_text, good.replace("static void say(", "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\nstatic void say(", 1)),
        ("a DVFS request", check_sweetprobe_source_text, good.replace("e = acpm_slc_version(c, &o->ver, &t);", "e = acpm_dvfs_set(c, 2, 100000, &t);", 1)),
        ("a PMIC writer", check_sweetprobe_source_text, good.replace("acpm_xfer_zero(&t.a);", "acpm_pmic_update(c, 0, 0, 0, &t); acpm_xfer_zero(&t.a);", 1)),
        ("a second request site", check_sweetprobe_source_text, good.replace("\to->rc = RC_OK;", "\t(void)acpm_slc_version(c, &o->ver, &t);\n\to->rc = RC_OK;", 1)),
        ("a retry loop", check_sweetprobe_source_text, good.replace("\tif (e) {\n\t\to->rc = RC_FAILED;", "\twhile (e)\n\t\te = acpm_slc_version(c, &o->ver, &t);\n\tif (e) {\n\t\to->rc = RC_FAILED;", 1)),
        ("a for retry", check_sweetprobe_source_text, good.replace("\te = acpm_slc_version(c, &o->ver, &t);", "\tfor (e = 1; e; e = acpm_slc_version(c, &o->ver, &t))\n\t\t;", 1)),
        ("PT_CHECK literal", check_sweetprobe_source_text, good.replace("enum { RC_OK = 0,", "#define PT_CHECK 0xd003u\nenum { RC_OK = 0,", 1)),
        ("a PMON opcode literal", check_sweetprobe_source_text, good.replace("enum { RC_OK = 0,", "#define PMON 0x21\nenum { RC_OK = 0,", 1)),
        ("another PSCI id", check_sweetprobe_source_text, good.replace("#define PSCI_ID_OFF\t\t0x84000002u", "#define PSCI_ID_OFF\t\t0x84000003u", 1)),
        ("a PSCI call with a free id", check_sweetprobe_source_text, good.replace("case PQ_VERSION: return (uint32_t)sweetprobe_smc(PSCI_FN_VERSION, 0);", "case PQ_VERSION: return (uint32_t)sweetprobe_smc(q, 0);", 1)),
        ("CPU_ON queried", check_sweetprobe_source_text, good.replace("default: return 0xFFFFFFFFu;", "default: return (uint32_t)sweetprobe_smc(0xC4000003u, 0);", 1)),
        ("channel 11 queried with a request", check_sweetprobe_source_text, good.replace("(void)slc_desc(c, ACPM_SLC_ASYNC_CH, &h11,", "(void)slc_desc(c, 12u, &h11,", 1)),
        ("no describe refusal", check_sweetprobe_source_text, good.replace("\tif (e) {\n\t\to->rc = RC_DESC;\n\t\treturn;\n\t}", "\tif (0) {\n\t\to->rc = RC_DESC;\n\t\treturn;\n\t}", 1)),
        ("queues not checked", check_sweetprobe_source_text, good.replace("if (h10.rr != h10.rf || h10.sr != h10.sf) {", "if (0) {", 1)),
        ("room checked after the queries", check_sweetprobe_source_text, good.replace("n + SP_TOK_MAX >= cap", "0", 1)),
        ("#if 0 around the busy gate", check_sweetprobe_source_text, good.replace("\tif (h10.rr != h10.rf", "#if 0\n\tif (h10.rr != h10.rf", 1).replace("\t\treturn;\n\t}\n\tacpm_xfer_zero(&t.a);", "\t\treturn;\n\t}\n#endif\n\tacpm_xfer_zero(&t.a);", 1)),
        ("a second smc in the smc file", check_sweetprobe_smc_source_text, smc.replace("\treturn x0;", '\t__asm__ volatile("smc #0" : "+r"(x0));\n\treturn x0;', 1)),
        ("another smc immediate", check_sweetprobe_smc_source_text, smc.replace('"smc #0"', '"smc #1"', 1)),
        ("a nonzero x2", check_sweetprobe_smc_source_text, smc.replace('x2 __asm__("x2") = 0', 'x2 __asm__("x2") = fn', 1)),
        ("a second function in the smc file", check_sweetprobe_smc_source_text, smc.replace("#endif", "uint64_t sweetprobe_smc2(uint64_t fn) { return sweetprobe_smc(fn, 0); }\n#endif", 1)),
        ("a free opcode in the request", check_acpm_slc_source_text, acpm.replace("XM_SLCV, 0u, 0u, ACPM_SLC_PT_VERSION, 0u, t, &t->a, h.esz_b)", "XM_SLCV, 0u, 0u, 0xd003u, 0u, t, &t->a, h.esz_b)", 1)),
        ("a request argument", check_acpm_slc_source_text, acpm.replace("XM_SLCV, 0u, 0u, ACPM_SLC_PT_VERSION, 0u, t,", "XM_SLCV, 0u, 1u, ACPM_SLC_PT_VERSION, 0u, t,", 1)),
        ("a second SLC request", check_acpm_slc_source_text, acpm.replace("\tif (!e)\n\t\t*ver = t->a.value;", "\te = xfer_eb(&d, XM_SLCV, 0u, 0u, ACPM_SLC_PT_VERSION, 0u, t, &t->a, h.esz_b);\n\tif (!e)\n\t\t*ver = t->a.value;", 1)),
        ("a retry of the SLC request", check_acpm_slc_source_text, acpm.replace("\tif (!e)\n\t\t*ver = t->a.value;", "\twhile (e)\n\t\te = 0;\n\tif (!e)\n\t\t*ver = t->a.value;", 1)),
        ("describe sends", check_acpm_slc_source_text, acpm.replace("\tcfg_chan(c, &d, ch);\n\treturn describe_n(&d, h, EB_16 | EB_32);", "\tcfg_chan(c, &d, ch);\n\t(void)xfer(&d, XM_DGET, 0, 0, 1, 0, 0, 0);\n\treturn describe_n(&d, h, EB_16 | EB_32);", 1)),
        ("describe takes any channel", check_acpm_slc_source_text, acpm.replace("if (ch != ACPM_SLC_CH && ch != ACPM_SLC_ASYNC_CH) {", "if (0) {", 1)),
        ("an element size of 64", check_acpm_slc_source_text, acpm.replace("(eb != ACPM_ELEM_BYTES && eb != 2u * ACPM_ELEM_BYTES)", "(eb != ACPM_ELEM_BYTES && eb != 4u * ACPM_ELEM_BYTES)", 1)),
        ("the request skips the descriptor check", check_acpm_slc_source_text, acpm.replace("\te = describe_n(&d, &h, EB_16 | EB_32);\n\tif (e)\n\t\treturn refuse(&t->a, e, h.why);\n\te = xfer_eb(&d, XM_SLCV", "\te = xfer_eb(&d, XM_SLCV", 1)),
    ]
    caught = 0
    for label, fn, bad in cases:
        base = {check_sweetprobe_source_text: good, check_sweetprobe_smc_source_text: smc, check_acpm_slc_source_text: acpm}[fn]
        assert bad != base, f"sweetprobe source selftest case did not mutate: {label}"
        try:
            fn(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: sweetprobe source {label} -> {str(exc)[:150]}")
        else:
            raise AssertionError(f"sweetprobe source self-test FAILED to catch: {label}")
    print(f"PASS sweetprobe source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


_SMC_OK_MNEMONICS = {"smc", "ret", "mov", "movz", "nop"}


def check_smc_object_text(disasm: str, undefined: set, name: str = "sweetprobe_smc.o") -> None:
    """The PSCI object: one function, exactly one `smc`, no other instruction family, no undefined symbol, no relocation."""
    funcs = re.findall(r"(?m)^[0-9a-f]+ <([^>]+)>:$", disasm)
    if funcs != ["sweetprobe_smc"]:
        raise AssertionError(f"{name}: the object must hold exactly the function sweetprobe_smc, got {funcs}")
    if undefined:
        raise AssertionError(f"{name}: undefined symbols {sorted(undefined)}")
    if re.search(r"R_AARCH64_", disasm):
        raise AssertionError(f"{name}: a relocation in the PSCI object")
    ins = re.findall(r"(?m)^\s*[0-9a-f]+:\t[0-9a-f]{8}\s+\t?(\w+)", disasm)
    if ins.count("smc") != 1:
        raise AssertionError(f"{name}: expected exactly one smc, found {ins.count('smc')}")
    other = sorted(set(ins) - _SMC_OK_MNEMONICS)
    if other:
        raise AssertionError(f"{name}: instruction(s) outside {sorted(_SMC_OK_MNEMONICS)}: {other}")


def smc_object_selftest(info) -> int:
    disasm, undefined, _local, _sections = info
    check_smc_object_text(disasm, undefined)  # control
    cases = [
        ("a second smc", disasm.replace("\tret\n", "\tsmc\t#0\n  ffff0:\td65f03c0 \tret\n", 1) if "\tret\n" in disasm else disasm + "  ffff0:\td4000003 \tsmc\t#0\n", undefined),
        ("a store", disasm + "  ffff4:\tb90013e0 \tstr\tw0, [sp, #16]\n", undefined),
        ("an undefined symbol", disasm, undefined | {"memcpy"}),
        ("another function", disasm + "0000000000ffff00 <evil>:\n  ffff00:\td65f03c0 \tret\n", undefined),
        ("a relocation", disasm + "\t\t\tffff00: R_AARCH64_CALL26\ttry_write32\n", undefined),
    ]
    caught = 0
    for label, bad, und in cases:
        try:
            check_smc_object_text(bad, und, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: sweetprobe_smc object {label} -> {exc}")
        else:
            raise AssertionError(f"sweetprobe_smc object self-test FAILED to catch: {label}")
    print(f"PASS sweetprobe_smc object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


def audit(root: pathlib.Path, out_dir: pathlib.Path, memo_obj, obj_selftest, obj_check, obj_info, strip_c_comments, c_function_span) -> None:
    """Everything _audit_main runs for --sweet-probe: source audits + mutants, then the -DSWEET_PROBE audit objects and the shipped ones."""
    bind(strip_c_comments, c_function_span)
    sweetprobe_source_selftest()
    good, smc, acpm = _good(root)
    check_sweetprobe_source_text(good)
    check_sweetprobe_smc_source_text(smc)
    check_acpm_slc_source_text(acpm)
    print("PASS sweetprobe source audit: no asm / MMIO accessors / pointer casts; PSCI ids from a fixed table (VERSION, FEATURES x3, no CPU_SUSPEND/CPU_OFF/CPU_ON call); the SLC request is ONE fixed "
          "PT_VERSION through acpm_slc_version after the descriptor and queue gates; channel 11 is described only; room checked before any query; no SLC opcode literal; no retry loops")
    memo_obj(obj_selftest, out_dir / "sweetprobe_audit.o", ALLOWED_UNDEF, "sweetprobe")
    n_stores = obj_check(out_dir / "sweetprobe_audit.o", ALLOWED_UNDEF)
    if "<sweet_probe_cmdline>:" not in obj_info(out_dir / "sweetprobe_audit.o")[0]:
        raise AssertionError("sweetprobe_audit.o is empty: the -DSWEET_PROBE audit object must contain sweet_probe_cmdline")
    obj_check(out_dir / "sweetprobe.o", ALLOWED_UNDEF)
    print(f"PASS sweetprobe object audit: no try_write32/try_read32 reference; {n_stores} stores, all stack/argument based; undef subset (the audited ACPM client, console, timer, sweetprobe_smc)")
    info = obj_info(out_dir / "sweetprobe_smc_audit.o")
    smc_object_selftest(info)
    check_smc_object_text(info[0], info[1], "sweetprobe_smc_audit.o")
    shipped = obj_info(out_dir / "sweetprobe_smc.o")
    if "sweetprobe_smc" in shipped[2]:
        check_smc_object_text(shipped[0], shipped[1], "sweetprobe_smc.o")
    elif "smc\t" in shipped[0] or re.search(r"<\w+>:", shipped[0]):
        raise AssertionError("sweetprobe_smc.o (no --sweet-probe image) must be empty: no function, no smc")
    print("PASS sweetprobe_smc object audit: one function, exactly one smc, no other instruction family, no undefined symbol, no relocation")


def self_test() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("tbb", root / "tools" / "test-bluejay-boot.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root / "tools"))
    spec.loader.exec_module(mod)
    bind(mod._strip_c_comments, mod._c_function_span)
    try:
        n = sweetprobe_source_selftest()
    except AssertionError as exc:
        print(f"RESULT: FAIL sweetprobe_audit self-test: {exc}")
        return 1
    print(f"RESULT: PASS sweetprobe_audit self-test {n} mutants rejected")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL sweetprobe_audit is a module imported by tools/mifclk_audit.py; run with --self-test")  # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
