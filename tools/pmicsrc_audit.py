#!/usr/bin/env python3
"""pmicsrc_audit: the audits of the READ-ONLY PMIC reset-source reader (shim/pmicsrc.c, --pmic-src, docs/research/139), moved out of tools/test-bluejay-boot.py (R29: that file may not grow).
Imported by test-bluejay-boot.py (sys.path has tools/). Text checks only: no process, no file written.

  check_pmicsrc_source_text / pmicsrc_source_selftest   shim/pmicsrc.c: no MMIO of its own; the only hardware access is acpm_pmic_src_read over six pinned (chip, register) pairs, after every ACPM gate
  check_pmicsrc_clear_source_text / check_acpm_clear_source_text / pmicsrc_clear_selftest   --pmic-src-clear (docs/research/142), the ONE PMIC write: pmicsrc_clear.c + acpm.c acpm_pmic_src_clear
                                                         (3 pinned (chip, register) pairs, value 0 only, FUNC_WRITE once, #ifdef PMIC_SRC_CLEAR only, called from nowhere else)
  audit()                                                what _audit_main runs for it (source audits, mutants, the objects; the write must be absent from an image built without the flag)

The helpers of test-bluejay-boot.py (comment stripping, function span) are handed in by bind().

  tools/pmicsrc_audit.py --self-test    binds the helpers of test-bluejay-boot.py and runs the mutation self-test on shim/pmicsrc.c
Last line: RESULT: PASS|FAIL pmicsrc_audit ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # help: usage before any import or side effect
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


ALLOWED_UNDEF = {"con_str", "counter_freq", "acpm_cfg_init", "acpm_why", "acpm_describe", "acpm_pmic_src_read"}
REGS = ["0x0C", "0x0D", "0x0E", "0x0F", "0x0C", "0x0D"]


def check_pmicsrc_source_text(src: str, name: str = "pmicsrc.c") -> None:
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in ("try_write32", "try_read32", "volatile", "acpm_pmic_update", "acpm_pmic_read", "acpm_s10_read", "acpm_s11_read", "acpm_s11_rail_set", "acpm_dvfs_set",
                "acpm_dvfs_get", "acpm_dvfs_reassert", "w32", "wait_ticks", "acpm_pmic_src_clear"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (read-only, no MMIO of its own: the only hardware access is acpm_pmic_src_read; the clear write lives in pmicsrc_clear.c)")
    # --pmic-src-clear: the ONE call into pmicsrc_clear.c, flagged, after the reads, only when every gate passed and all six reads succeeded
    if len(re.findall(r"\bpmic_src_clear\s*\(", code)) != 1 or "#ifdefPMIC_SRC_CLEAR" not in norm:
        raise AssertionError(f"{name}: pmic_src_clear() is called exactly once, inside #ifdef PMIC_SRC_CLEAR")
    pin = "if(!why&&!failed&&got==NSRC&&cap>=PMIC_SRC_TOK_MAX){uint32_trb=0;uint32_tna=0,i;clr=pmic_src_clear(&c,&t,&rb,&na);"
    cl = norm.find(pin)
    if cl < 0 or norm[:cl].rfind("#ifdefPMIC_SRC_CLEAR") < norm[:cl].rfind("#endif") or cl < norm.find("e=acpm_pmic_src_read(&c,src_chip(k),src_reg(k),&v,&t);"):
        raise AssertionError(f"{name}: the clear runs only under `if (!why && !failed && got == NSRC && cap >= PMIC_SRC_TOK_MAX)` (gates passed, all six reads ok, room for the result token), after the reads, inside #ifdef PMIC_SRC_CLEAR")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    spans = [_span(code, "put_txt")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside put_txt (no retry loops)")
    if len(re.findall(r"\bacpm_pmic_src_read\s*\(", code)) != 1 or "e=acpm_pmic_src_read(&c,src_chip(k),src_reg(k),&v,&t);" not in norm:
        raise AssertionError(f"{name}: exactly one read site is allowed: acpm_pmic_src_read(&c, src_chip(k), src_reg(k), &v, &t)")
    ra, rb = _span(code, "src_reg")
    regs = [r.upper().replace("0X", "0x") for r in re.findall(r"return(0x[0-9A-Fa-f]+);", re.sub(r"\s+", "", code[ra:rb]))]
    if regs != REGS:
        raise AssertionError(f"{name}: src_reg() must return exactly {REGS} in index order, got {regs}")
    ca, cb = _span(code, "src_chip")
    if re.sub(r"\s+", "", code[ca:cb]) != "staticuint32_tsrc_chip(uint32_tk){returnk<4?ACPM_SRC_S10:ACPM_SRC_S11;}":
        raise AssertionError(f"{name}: src_chip() must be `k < 4 ? ACPM_SRC_S10 : ACPM_SRC_S11`")
    if "#defineNSRC6u" not in norm or "for(k=0;k<NSRC;k++){" not in norm:
        raise AssertionError(f"{name}: the six registers must be read in one bounded loop")
    ga, gb = _span(code, "pmic_src")
    body = re.sub(r"\s+", "", code[ga:gb])
    if not 0 <= body.find("why=gates(&c,&h);") < body.find("acpm_pmic_src_read("):
        raise AssertionError(f"{name}: every ACPM gate must run before the first read")
    if not re.search(r"if\(why\)\{.*?\}elseif\(failed\)\{.*?\}else\{e=acpm_pmic_src_read", body):
        raise AssertionError(f"{name}: a refusal or an earlier failure must skip the read (no retry)")
    ka, kb = _span(code, "gates")
    gbody = re.sub(r"\s+", "", code[ka:kb])
    for need in ("if(!c->layout_verified)", "if(!c->elem_unit_verified)", "if(!counter_freq())", "if(acpm_describe(c,h))", "if(h->rr!=h->rf||h->sr!=h->sf)"):
        if need not in gbody:
            raise AssertionError(f"{name}: gate '{need}' is missing")


CLR_REGS = ["0x0F", "0x0E", "0x0D"]
CLR_BANNED = ("try_write32", "try_read32", "volatile", "acpm_pmic_update", "acpm_pmic_read", "acpm_s10_read", "acpm_s11_read", "acpm_s11_rail_set", "acpm_dvfs_set", "acpm_dvfs_get",
              "acpm_dvfs_reassert", "w32", "wait_ticks", "xfer", "con_str", "goto", "while", "do")
CLR_ALLOWED_UNDEF = {"acpm_pmic_src_clear", "acpm_pmic_src_read"}


def check_pmicsrc_clear_source_text(src: str, name: str = "pmicsrc_clear.c") -> None:
    """pmicsrc_clear.c: the orchestration of the three writes; its only hardware calls are acpm_pmic_src_clear (3 writes of 0, in order) then acpm_pmic_src_read (3 read-backs), no retry, first failure returns."""
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in CLR_BANNED:
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (the hardware calls are acpm_pmic_src_clear and acpm_pmic_src_read, in two bounded for loops)")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code) or re.search(r"for\s*\(\s*;", code):
        raise AssertionError(f"{name}: explicit pointer cast or unbounded for is banned")
    if "#if" not in norm or "defined(PMIC_SRC_CLEAR)" not in norm or "#defineNCLR3u" not in norm:
        raise AssertionError(f"{name}: the code must sit under #if SOC_GS101 && defined(PMIC_SRC_CLEAR) with NCLR 3u")
    if len(re.findall(r"\bacpm_pmic_src_clear\s*\(", code)) != 1 or len(re.findall(r"\bacpm_pmic_src_read\s*\(", code)) != 1:
        raise AssertionError(f"{name}: exactly one write site and one read-back site")
    w = "for(k=0;k<NCLR;k++)if(acpm_pmic_src_clear(c,clr_chip(k),clr_reg(k),0,t))returnPMIC_CLR_WRITE+k;"
    r = "for(k=0;k<NCLR;k++){if(acpm_pmic_src_read(c,clr_chip(k),clr_reg(k),&v,t))returnPMIC_CLR_READ+k;"
    if w not in norm or r not in norm:
        raise AssertionError(f"{name}: write loop `{w}` and read-back loop (first failure returns, value literal 0) are pinned")
    if not norm.find(w) < norm.find(r) < norm.find("if(k!=1&&((*rb>>(8*(NCLR-1-k)))&0xffu)!=0)returnPMIC_CLR_NONZERO+k;"):
        raise AssertionError(f"{name}: order must be write x3, read back x3, then the non-zero check")
    ra, rb = _span(code, "clr_reg")
    regs = [x.upper().replace("0X", "0x") for x in re.findall(r"return(0x[0-9A-Fa-f]+);", re.sub(r"\s+", "", code[ra:rb]))]
    if regs != CLR_REGS:
        raise AssertionError(f"{name}: clr_reg() must return exactly {CLR_REGS} in order (S10 OFFSRC, S10 PWRONSRC, S11 OFFSRC), got {regs}")
    ca, cb = _span(code, "clr_chip")
    if re.sub(r"\s+", "", code[ca:cb]) != "staticuint32_tclr_chip(uint32_tk){returnk<2?ACPM_SRC_S10:ACPM_SRC_S11;}":
        raise AssertionError(f"{name}: clr_chip() must be `k < 2 ? ACPM_SRC_S10 : ACPM_SRC_S11`")


def check_acpm_clear_source_text(src: str, name: str = "acpm.c") -> None:
    """acpm.c: acpm_pmic_src_clear is the only FUNC_WRITE user; own allowlist, value 0 only, refusals before xfer(), all under #ifdef PMIC_SRC_CLEAR."""
    code = _strip(src)
    norm = re.sub(r"\s+", "", code)
    if len(re.findall(r"\bFUNC_WRITE\b", code)) != 2 or "#ifdefPMIC_SRC_CLEAR#defineFUNC_WRITE1u#endif" not in norm:
        raise AssertionError(f"{name}: FUNC_WRITE appears exactly in its #define (under #ifdef PMIC_SRC_CLEAR, value 1u) and in acpm_pmic_src_clear")
    if "#ifdefPMIC_SRC_CLEARstaticconststructsrc_regclr_regs[]={{ACPM_SRC_S10,0x0F},{ACPM_SRC_S10,0x0E},{ACPM_SRC_S11,0x0D},};int32_tacpm_pmic_src_clear(" not in norm:
        raise AssertionError(f"{name}: the clear sits under #ifdef PMIC_SRC_CLEAR, table exactly (S10,0x0F) (S10,0x0E) (S11,0x0D)")
    fa, fb = _span(code, "acpm_pmic_src_clear")
    body = re.sub(r"\s+", "", code[fa:fb])
    if body.count("xfer(") != 1 or "returnxfer(c,XM_PMIC,w0,FUNC_WRITE,0,0,t,&t->a);" not in body:
        raise AssertionError(f"{name}: acpm_pmic_src_clear has exactly one xfer: FUNC_WRITE with cmd[2], cmd[3] = 0 and the value 0 in cmd[1]")
    if not body.find("if(val!=0)returnrefuse(&t->a,ACPM_E_ARG,ACPM_W_ARG);") >= 0 or not body.find("if(val!=0)") < body.find("for(k=0;") < body.find("if(k==sizeof(clr_regs)/sizeof(clr_regs[0]))returnrefuse(&t->a,ACPM_E_ARG,ACPM_W_ARG);") < body.find("xfer("):
        raise AssertionError(f"{name}: acpm_pmic_src_clear must refuse a value != 0 and a pair outside clr_regs (ACPM_E_ARG) before xfer()")
    if re.search(r"\bwhile\b|\bgoto\b|\bdo\b", body) or "w0=pmic_w0(c,reg);" not in body or "w0=(reg&0xffu)|((uint32_t)ACPM_S11_PMIC_ADDR<<8)|((uint32_t)ACPM_S11_SPEEDY<<12);" not in body:
        raise AssertionError(f"{name}: acpm_pmic_src_clear builds w0 exactly like acpm_pmic_src_read and has no loop beyond the table scan")
    if code.count("acpm_pmic_src_clear(") != 2:
        raise AssertionError(f"{name}: acpm_pmic_src_clear is defined for gs101 and stubbed for non-ACPM SoCs, nothing else calls it")


def pmicsrc_clear_selftest() -> int:
    root = pathlib.Path(__file__).resolve().parents[1] / "shim"
    clr, acpm, main_ = (root / "pmicsrc_clear.c").read_text(), (root / "acpm.c").read_text(), (root / "pmicsrc.c").read_text()
    check_pmicsrc_clear_source_text(clr)
    check_acpm_clear_source_text(acpm)
    check_pmicsrc_source_text(main_)
    cases = [
        ("clear: a raw writer", check_pmicsrc_clear_source_text, clr, "static uint32_t clr_chip(", "static void evil(void) { try_write32(0x17463EC4UL, 1); }\nstatic uint32_t clr_chip(", None),
        ("clear: value 1", check_pmicsrc_clear_source_text, clr, "clr_reg(k), 0, t))", "clr_reg(k), 1, t))", None),
        ("clear: an INT register", check_pmicsrc_clear_source_text, clr, "case 0: return 0x0F;", "case 0: return 0x00;", None),
        ("clear: another order", check_pmicsrc_clear_source_text, clr, "case 1: return 0x0E;", "case 1: return 0x0D;", None),
        ("clear: another chip split", check_pmicsrc_clear_source_text, clr, "return k < 2 ?", "return k < 1 ?", None),
        ("clear: a retry", check_pmicsrc_clear_source_text, clr, "\t\tif (acpm_pmic_src_clear(", "\t\twhile (acpm_pmic_src_clear(c, clr_chip(k), clr_reg(k), 0, t)) ;\n\t\tif (acpm_pmic_src_clear(", None),
        ("clear: a failed write does not stop", check_pmicsrc_clear_source_text, clr, "return PMIC_CLR_WRITE + k;", "(void)k;", None),
        ("clear: a second write site", check_pmicsrc_clear_source_text, clr, "\treturn 0;\n}", "\t(void)acpm_pmic_src_clear(c, 0, 0x00, 0, t);\n\treturn 0;\n}", None),
        ("clear: a PMIC update", check_pmicsrc_clear_source_text, clr, "if (acpm_pmic_src_clear(c, clr_chip(k), clr_reg(k), 0, t))", "if (acpm_pmic_update(c, 0x5C, 0x80, 0x80, t))", None),
        ("clear: PWRONSRC read-back becomes an error again", check_pmicsrc_clear_source_text, clr, "if (k != 1 && ((*rb", "if (((*rb", None),
        ("clear: not behind the flag", check_pmicsrc_clear_source_text, clr, "#if SOC_GS101 && defined(PMIC_SRC_CLEAR)", "#if SOC_GS101", None),
        ("acpm: the value check is gone", check_acpm_clear_source_text, acpm, "\tif (val != 0)\n\t\treturn refuse(&t->a, ACPM_E_ARG, ACPM_W_ARG);\n\tfor (k = 0; k < sizeof(clr_regs)", "\tfor (k = 0; k < sizeof(clr_regs)", None),
        ("acpm: an INT register in the table", check_acpm_clear_source_text, acpm, "{ ACPM_SRC_S10, 0x0F }, { ACPM_SRC_S10, 0x0E }, { ACPM_SRC_S11, 0x0D },", "{ ACPM_SRC_S10, 0x0F }, { ACPM_SRC_S10, 0x00 }, { ACPM_SRC_S11, 0x0D },", None),
        ("acpm: a fourth register", check_acpm_clear_source_text, acpm, "{ ACPM_SRC_S11, 0x0D },\n};\n\nint32_t acpm_pmic_src_clear", "{ ACPM_SRC_S11, 0x0D }, { ACPM_SRC_S10, 0x0C },\n};\n\nint32_t acpm_pmic_src_clear", None),
        ("acpm: FUNC_UPDATE instead of FUNC_WRITE", check_acpm_clear_source_text, acpm, "XM_PMIC, w0, FUNC_WRITE, 0, 0, t, &t->a);", "XM_PMIC, w0, FUNC_UPDATE, 0, 0, t, &t->a);", None),
        ("acpm: a value in cmd[1]", check_acpm_clear_source_text, acpm, "XM_PMIC, w0, FUNC_WRITE, 0, 0, t, &t->a);", "XM_PMIC, w0, FUNC_WRITE | (val << 8) | 0x100, 0, 0, t, &t->a);", None),
        ("acpm: the table check is gone", check_acpm_clear_source_text, acpm, "\tif (k == sizeof(clr_regs) / sizeof(clr_regs[0]))\n\t\treturn refuse(&t->a, ACPM_E_ARG, ACPM_W_ARG);\n\tif (chip == ACPM_SRC_S10) {\n\t\tw0 = pmic_w0(c, reg);\n\t} else {\n#if defined(ACPM_S11_KNOWN) && ACPM_S11_KNOWN && defined(ACPM_S11_PMIC_ADDR) \\\n\t&& defined(ACPM_S11_SPEEDY)\n\t\tw0 = (reg & 0xffu) | ((uint32_t)ACPM_S11_PMIC_ADDR << 8)\n\t\t\t| ((uint32_t)ACPM_S11_SPEEDY << 12);\n#else\n\t\treturn refuse(&t->a, ACPM_E_UNSURE, ACPM_W_ARG);\n#endif\n\t}\n\treturn xfer(", "\tif (chip == ACPM_SRC_S10) {\n\t\tw0 = pmic_w0(c, reg);\n\t} else {\n#if defined(ACPM_S11_KNOWN) && ACPM_S11_KNOWN && defined(ACPM_S11_PMIC_ADDR) \\\n\t&& defined(ACPM_S11_SPEEDY)\n\t\tw0 = (reg & 0xffu) | ((uint32_t)ACPM_S11_PMIC_ADDR << 8)\n\t\t\t| ((uint32_t)ACPM_S11_SPEEDY << 12);\n#else\n\t\treturn refuse(&t->a, ACPM_E_UNSURE, ACPM_W_ARG);\n#endif\n\t}\n\treturn xfer(", None),
        ("acpm: FUNC_WRITE defined outside the flag", check_acpm_clear_source_text, acpm, "#ifdef PMIC_SRC_CLEAR\n#define FUNC_WRITE", "#define FUNC_WRITE", None),
        ("acpm: another FUNC_WRITE user", check_acpm_clear_source_text, acpm, "int32_t acpm_pmic_src_read(", "static uint32_t evil_w1(void) { return FUNC_WRITE; }\nint32_t acpm_pmic_src_read(", None),
        ("src: clear without the all-reads condition", check_pmicsrc_source_text, main_, "got == NSRC && cap >= PMIC_SRC_TOK_MAX", "cap >= PMIC_SRC_TOK_MAX", None),
        ("src: clear without the gate condition", check_pmicsrc_source_text, main_, "if (!why && !failed && got == NSRC && cap", "if (!failed && got == NSRC && cap", None),
        ("src: a second clear call", check_pmicsrc_source_text, main_, "\t\tclr = pmic_src_clear(&c, &t, &rb, &na);", "\t\tclr = pmic_src_clear(&c, &t, &rb, &na);\n\t\tclr = pmic_src_clear(&c, &t, &rb, &na);", None),
        ("src: the write called directly", check_pmicsrc_source_text, main_, "\t\tclr = pmic_src_clear(&c, &t, &rb, &na);", "\t\t(void)acpm_pmic_src_clear(&c, 0, 0x0F, 0, &t);\n\t\tclr = pmic_src_clear(&c, &t, &rb, &na);", None),
    ]
    caught = 0
    for label, fn, good, old, new_, _ in cases:
        bad = good.replace(old, new_, 1)
        assert bad != good, f"pmicsrc clear selftest case did not mutate: {label}"
        try:
            fn(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: pmicsrc {label} -> {str(exc)[:150]}")
        else:
            raise AssertionError(f"pmicsrc clear self-test FAILED to catch: {label}")
    print(f"PASS pmicsrc clear self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def pmicsrc_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "pmicsrc.c").read_text()
    check_pmicsrc_source_text(good)  # control must pass
    cases = [
        ("inline asm", good.replace("static void emit(", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic void emit(', 1)),
        ("a raw writer", good.replace("static void emit(", "static void evil(void) { try_write32(0x17463EC4UL, 1); }\nstatic void emit(", 1)),
        ("a raw reader", good.replace("static void emit(", "static void evil(uint32_t *v) { (void)try_read32(0x17463EC4UL, v); }\nstatic void emit(", 1)),
        ("a PMIC update", good.replace("e = acpm_pmic_src_read(&c, src_chip(k), src_reg(k), &v, &t);", "e = acpm_pmic_update(&c, 0x5C, 0x80, 0x80, &t);", 1)),
        ("a literal register", good.replace("src_reg(k), &v", "0x00, &v", 1)),
        ("an INT register in the list", good.replace("case 0: return 0x0C;", "case 0: return 0x00;", 1)),
        ("a second read site", good.replace("\tp = put_txt(line, sizeof(line), p, \" (read-only", "\t(void)acpm_pmic_src_read(&c, 0, 0x0C, &v, &t);\n\tp = put_txt(line, sizeof(line), p, \" (read-only", 1)),
        ("a retry loop", good.replace("\t\t\tif (e) {", "\t\t\twhile (e)\n\t\t\t\te = acpm_pmic_src_read(&c, src_chip(k), src_reg(k), &v, &t);\n\t\t\tif (e) {", 1)),
        ("no queue check", good.replace("if (h->rr != h->rf || h->sr != h->sf)", "if (0)", 1)),
        ("no describe", good.replace("if (acpm_describe(c, h))", "if (0)", 1)),
        ("failure does not stop", good.replace("\t\t} else if (failed) {", "\t\t} else if (0) {", 1)),
        ("another index->register map", good.replace("default: return 0x0D;", "default: return 0x0E;", 1)),
        ("another chip split", good.replace("return k < 4 ?", "return k < 2 ?", 1)),
        ("volatile", good.replace("static void emit(", "static volatile int evil_v;\nstatic void emit(", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"pmicsrc source selftest case did not mutate: {label}"
        try:
            check_pmicsrc_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: pmicsrc source {label} -> {exc}")
        else:
            raise AssertionError(f"pmicsrc source self-test FAILED to catch: {label}")
    print(f"PASS pmicsrc source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def audit(root: pathlib.Path, out_dir: pathlib.Path, memo_obj, obj_selftest, obj_check, obj_info, strip_c_comments, c_function_span) -> None:
    """Everything _audit_main runs for --pmic-src / --pmic-src-clear: source audits + mutants, then the objects of out_dir."""
    bind(strip_c_comments, c_function_span)
    pmicsrc_source_selftest()
    check_pmicsrc_source_text((root / "shim" / "pmicsrc.c").read_text())
    print("PASS pmicsrc source audit: read-only; no asm / volatile / pointer casts / MMIO of its own; the only hardware access is acpm_pmic_src_read over the six cited registers, after every ACPM gate; the first failure stops the reads")
    pmicsrc_clear_selftest()
    check_pmicsrc_clear_source_text((root / "shim" / "pmicsrc_clear.c").read_text())
    check_acpm_clear_source_text((root / "shim" / "acpm.c").read_text())
    print("PASS pmicsrc clear source audit: the one write = acpm_pmic_src_clear over (S10,0xF) (S10,0xE) (S11,0xD), value 0, FUNC_WRITE once, under #ifdef PMIC_SRC_CLEAR; called only from pmicsrc_clear.c after the reads")
    info = obj_info(out_dir / "pmicsrc.o")
    clear_build = "pmic_src_clear" in info[1]
    allowed = ALLOWED_UNDEF | ({"pmic_src_clear"} if clear_build else set())
    memo_obj(obj_selftest, out_dir / "pmicsrc.o", allowed, "pmicsrc")
    n_stores = obj_check(out_dir / "pmicsrc.o", allowed)
    print(f"PASS pmicsrc object audit: no try_write32 / try_read32 reference; {n_stores} stores, all stack/argument based; undef subset (the audited ACPM client, console)")
    acpm_defines = "acpm_pmic_src_clear" in obj_info(out_dir / "acpm.o")[2]
    if clear_build != acpm_defines or (out_dir / "pmicsrc_clear.o").exists() != clear_build:
        raise AssertionError(f"pmicsrc clear object state is inconsistent: pmicsrc.o calls pmic_src_clear={clear_build}, acpm.o defines acpm_pmic_src_clear={acpm_defines}, "
                             f"pmicsrc_clear.o present={(out_dir / 'pmicsrc_clear.o').exists()} (the write exists in all of them or in none)")
    if not clear_build:
        print("PASS pmicsrc clear object audit: no --pmic-src-clear in this build: acpm.o has no acpm_pmic_src_clear, pmicsrc.o has no call, no pmicsrc_clear.o")
        return
    memo_obj(obj_selftest, out_dir / "pmicsrc_clear.o", CLR_ALLOWED_UNDEF, "pmicsrc_clear")
    n_stores = obj_check(out_dir / "pmicsrc_clear.o", CLR_ALLOWED_UNDEF)
    print(f"PASS pmicsrc clear object audit: pmicsrc_clear.o has no try_write32 / try_read32 reference; {n_stores} stores, all stack/argument based; undef subset = acpm_pmic_src_clear, acpm_pmic_src_read")


def self_test() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("tbb", root / "tools" / "test-bluejay-boot.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root / "tools"))
    spec.loader.exec_module(mod)
    bind(mod._strip_c_comments, mod._c_function_span)
    try:
        n = pmicsrc_source_selftest() + pmicsrc_clear_selftest()
    except AssertionError as exc:
        print(f"RESULT: FAIL pmicsrc_audit self-test: {exc}")
        return 1
    print(f"RESULT: PASS pmicsrc_audit self-test {n} mutants rejected")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL pmicsrc_audit is a module imported by tools/test-bluejay-boot.py; run with --self-test")  # no silent stub
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
