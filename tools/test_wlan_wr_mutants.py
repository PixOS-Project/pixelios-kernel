"""Mutation test of the stage-3 chip writer in xnu patch 0129 (bsd/dev/sweet_wlan_wr.h, W-D3 step 2, docs/research/43 s.3).

Each guard rule of swl_wr() / swl_wr_ok() / the steps is removed or weakened in a copy of the header (every occurrence of the pattern), the host test
shim/wlan_wr_host_test.c is built against the copy and must FAIL; a mutant that still passes is a survivor and fails this test, unless it is listed
EQUIVALENT with the clause that implies it (CR-02). A pattern that matches nothing fails too (the header changed under the mutant list).
Patch 0130 (run B: W8 window, W9 last word, W10 image copy) changes both files; patch 0131 (run C: W11 NVRAM text, W12 length token, W13 rTLV) changes them again; the texts under test are the files
after 0124, 0128, 0129, 0130, 0131 are applied in order (apply_chain). Two host tests are built against every mutant (shim/wlan_wr_host_test.c with the fake bus, shim/wlan_nv_host_test.c for the pure
process_nvram_vars port); a mutant is killed when either fails.
Run: python3 -B tools/test_wlan_wr_mutants.py  (also picked up by `unittest discover -s tools -p 'test_*.py'`)."""
import concurrent.futures
import difflib
import functools
import os
import re
import resource
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH0120 = os.path.join(ROOT, "xnu", "patches", "0124-sweet-wlan-bwfm-core.patch")
PATCH0125 = os.path.join(ROOT, "xnu", "patches", "0129-sweet-wlan-chip-writes.patch")
PATCH0128 = os.path.join(ROOT, "xnu", "patches", "0128-sweet-wlan-fwcheck.patch")
PATCH0130 = os.path.join(ROOT, "xnu", "patches", "0130-sweet-wlan-image-copy.patch")
PATCH0131 = os.path.join(ROOT, "xnu", "patches", "0131-sweet-wlan-nvram-rtlv.patch")
TEST = os.path.join(ROOT, "shim", "wlan_wr_host_test.c")
TEST_NV = os.path.join(ROOT, "shim", "wlan_nv_host_test.c")
HDR_PATH = "bsd/dev/sweet_wlan_wr.h"
GLUE_PATH = "bsd/dev/sweet_wlan.c"


def file_diff(patch, path):
    """The diff section of one file in a patch (its 'diff --git' line up to the next one); '' when the patch does not touch the file."""
    out, on = [], False
    with open(patch, encoding="utf-8") as fh:
        for line in fh.read().split("\n"):
            if line.startswith("diff --git "):
                on = line.endswith(" b/" + path)
            if on:
                out.append(line)
    return "\n".join(out) + "\n" if out else ""


@functools.lru_cache(maxsize=None)
def apply_chain(path, patches):
    """The text of `path` after the given patches (a tuple of files) are applied in order with patch(1), each only for this file."""
    with tempfile.TemporaryDirectory() as d:
        full = os.path.join(d, path)
        os.makedirs(os.path.dirname(full))
        for pt in patches:
            sec = file_diff(pt, path)
            if not sec:
                continue
            r = subprocess.run(["patch", "-s", "-p1", "-d", d], input=sec, capture_output=True, text=True)
            if r.returncode != 0:
                raise ValueError("%s does not apply to %s: %s" % (os.path.basename(pt), path, r.stdout + r.stderr))
        if not os.path.exists(full):
            raise ValueError("no patch creates " + path)
        with open(full, encoding="utf-8") as fh:
            return fh.read()


def final_header():
    return apply_chain(HDR_PATH, (PATCH0125, PATCH0130, PATCH0131))


def glue_before():
    return apply_chain(GLUE_PATH, (PATCH0120, PATCH0128))


def glue_after():
    return apply_chain(GLUE_PATH, (PATCH0120, PATCH0128, PATCH0125, PATCH0130, PATCH0131))


def new_file(patch, path):
    """The text of one new-file hunk of a patch (same extraction as the awk lines in shim/build.sh)."""
    out, on = [], False
    with open(patch, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    for line in lines:
        if line == "+++ b/" + path:
            on = True
        elif on and (line.startswith("diff ") or line.startswith("--- ")):
            break
        elif on and line.startswith("@@"):
            continue
        elif on and line.startswith("+"):
            out.append(line[1:])
    if not out:
        raise ValueError("no new-file hunk for " + path + " in " + patch)
    return "\n".join(out) + "\n"


def body(text, name):
    """The text of one function (from its name line to the closing brace at column 0)."""
    m = re.search(r"^%s\(.*?^}$" % re.escape(name), text, re.S | re.M)
    return m.group(0) if m else ""


def strip_comments(text):
    """Block comments; also the ' * ...' lines of a comment whose opening line is a context line of the patch."""
    return re.sub(r"^ \*.*$", " ", re.sub(r"/\*.*?\*/", " ", text, flags=re.S), flags=re.M)


# Every assignment of the added glue, as (function, statement), pinned (review 0125 R2-1). Anything else, any duplicate and any missing one fails.
GLUE_ASSIGNS = sorted([
    ("sw_prop_u32_is", "uint32_t const *p = NULL;"),
    ("sw_prop_u32_is", "unsigned int sz = 0;"),
    ("sw3_rd", "struct sw_hw *h = ctx;"),
    ("sw3_rd", "volatile uint32_t *p = sw3_ptr(h, space, off);"),
    ("sw3_rd", "*v = *p;"),  # hands the read result to the caller
    ("sw3_wr", "struct sw_hw *h = ctx;"),
    ("sw3_wr", "p = sw3_ptr(h, space, off);"),
    ("sw3_wr", "*p = v;"),  # THE store
    ("sw3_map2", "struct sw_hw *h = ctx;"),
    ("sw3_map2", "h->tcm = sw_map(SWW_TCM_PA, SWW_TCM_BYTES);"),
    ("sweet_wlan_stage3", "wb.rd = sw3_rd;"),
    ("sweet_wlan_stage3", "wb.wr = sw3_wr;"),
    ("sweet_wlan_stage3", "wb.map2 = sw3_map2;"),
    ("sweet_wlan_stage3", "wb.delay_us = sw3_delay;"),
    ("sweet_wlan_stage3", "wb.ctx = hw;"),
    ("sweet_wlan_stage3", "x.b = &wb;"),
    ("sweet_wlan_stage3", "x.level = level;"),
    ("sweet_wlan_stage3", "x.img = sw_fw.p;"),  # patch 0130: the FW entry swf_parse() accepted
    ("sweet_wlan_stage3", "x.img_len = sw_fw.len;"),
    ("sweet_wlan_stage3", "x.img_crc = sw_fw.crc;"),
    ("sweet_wlan_stage3", "x.nv_src = sw_nv.p;"),  # patch 0131: the NVRM entry swf_parse() accepted, a scratch buffer, the entropy source
    ("sweet_wlan_stage3", "x.nv_src_len = sw_nv.len;"),
    ("sweet_wlan_stage3", "x.nv_src_crc = sw_nv.crc;"),
    ("sweet_wlan_stage3", "x.nv = sw_nvbuf;"),
    ("sweet_wlan_stage3", "x.nv_cap = sizeof sw_nvbuf;"),
    ("sweet_wlan_stage3", "wb.rng = sw3_rng;"),
    ("sw_fw_note", "for (i = 0;"),
    ("sw_fw_note", 'i++) { if (memcmp(e[i].tag, "", 4) == 0) { sw_fw.p = base + e[i].offset;'),  # string contents are stripped by the scan; audit_store_path pins "FW  "
    ("sw_fw_note", "sw_fw.p = base + e[i].offset;"),
    ("sw_fw_note", "sw_fw.len = e[i].length;"),
    ("sw_fw_note", "sw_fw.crc = e[i].crc;"),
    ("sw_fw_note", "sw_nv.p = base + e[i].offset;"),
    ("sw_fw_note", "sw_nv.len = e[i].length;"),
    ("sw_fw_note", "sw_nv.crc = e[i].crc;"),
    ("sweet_wlan_init", "hw.level = level > 2 ? 2 : (int)level;"),  # fragments of the function: its header is a context line of the patch
    ("sweet_wlan_init", "hw.atu = atu;"),
    ("sweet_wlan_init", "hw.elbi = elbi;"),
    ("sweet_wlan_init", "hw.iso = &isop[(SW_ISO & 0xFFFull) / 4];"),
    ("sweet_wlan_init", "hw.tcm = NULL;"),
])
ASSIGN_OP = re.compile(r"(?<![=!<>])(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+|--")


def audit_assignments(glue):
    """Operator scan of the comment- and string-stripped glue: every `=`, compound assignment, `++`, `--` must be one of GLUE_ASSIGNS in its function;
    no mem*/atomic call, no use of the wr callback but `wb.wr = sw3_wr;`, no call of the 0124 window reader sw_rd()."""
    f = []
    g = re.sub(r"'(?:\\.|[^'\\])'", "0", re.sub(r'"(?:\\.|[^"\\])*"', '""', strip_comments(glue)))
    rng = [(m.group(1), m.start(), m.end()) for m in re.finditer(r"^(\w+)\(.*?^}$", g, re.S | re.M)]
    seen, got = set(), []
    for m in ASSIGN_OP.finditer(g):
        a = max(g.rfind(c, 0, m.start()) for c in ";{}") + 1
        if a in seen:
            continue
        seen.add(a)
        fn = next((n for n, s, e in rng if s <= m.start() < e), "sweet_wlan_init")
        got.append((fn, " ".join(g[a:g.find(";", m.end()) + 1].split())))
    if sorted(got) != GLUE_ASSIGNS:
        f.append("assignment outside the allowlist: " + "; ".join("%s: %s" % x for x in sorted(set(got) ^ set(GLUE_ASSIGNS)) or [("duplicate", "count")]))
    if re.search(r"\b(?:memcpy|memmove|bcopy|__builtin_mem\w*|os_atomic\w*|atomic_store\w*)\b", g):
        f.append("memory-copy or atomic store call")
    if len(re.findall(r"(?:\.|->)\s*wr\b", g)) != 1 or "wb.wr = sw3_wr;" not in g:
        f.append("wr callback used other than by wb.wr = sw3_wr;")
    if re.search(r"\bsw_rd\s*\(", g):
        f.append("call of sw_rd in the added glue")
    return f


def audit_store_path(hdr, glue):
    """Source audit of the store path (docs/research/43 s.5 step 5): the header has no asm and no pointer made from an integer literal, its one bus store
    callback is called only in swl_store(), swl_store() only from swl_wr() and swl_restore(); the glue has exactly one store through a pointer (sw3_wr) and
    sw3_wr is handed to the bus only as the wr callback. Returns the list of findings (empty = clean)."""
    f = []
    hdr, glue = strip_comments(hdr), strip_comments(glue)
    if re.search(r"\b(__asm__|__asm|asm)\b", hdr + glue):
        f.append("inline asm")
    if re.search(r"\(\s*(?:volatile\s+)?[A-Za-z_][A-Za-z_0-9 ]*\*\s*\)\s*\(?\s*(?:0[xX][0-9A-Fa-f]+|[0-9]+)", hdr + glue):
        f.append("pointer from an integer literal")
    if len(re.findall(r"->wr\(", hdr)) != 1 or "->wr(" not in body(hdr, "swl_store"):
        f.append("bus store callback called outside swl_store")
    for m in re.finditer(r"^(swl_\w+)\(", hdr, re.M):
        fn = m.group(1)
        if fn not in ("swl_store", "swl_wr", "swl_restore") and "swl_store(" in body(hdr, fn):
            f.append("swl_store called from " + fn)
    for fn in ("swl_wr", "swl_restore"):
        if "swl_store(" not in body(hdr, fn):
            f.append("%s does not use swl_store" % fn)
    f.extend(audit_assignments(glue))
    if "off != SWW_CFG_WIN" not in body(glue, "sw3_wr") or "SWW_R2(0x14)" not in body(glue, "sw3_wr") or "SWW_SM_BANKIDX" not in body(glue, "sw3_wr"):
        f.append("sw3_wr does not limit the offsets to the table's")
    if not all(n in body(glue, "sw3_wr") for n in ("SWW_CFG_WIN2", "SWW_WIN_TAIL", "SWW_RAM_BASE", "SWW_LAST_HI_MAX", "space == SWW_BAR2")):
        f.append("sw3_wr does not limit BAR2 and cfg 0x84 to the table's")
    if 'memcmp(e[i].tag, "FW  ", 4)' not in body(glue, "sw_fw_note"):
        f.append("sw_fw_note does not pick the FW entry by its tag")
    if 'memcmp(e[i].tag, "NVRM", 4)' not in body(glue, "sw_fw_note"):
        f.append("sw_fw_note does not pick the NVRM entry by its tag")
    if len(re.findall(r"\bsw3_wr\b", glue)) != 2 or "wb.wr = sw3_wr;" not in glue:
        f.append("sw3_wr used other than as the bus wr callback")
    return f


# (name, [(old, new)], equivalent-because or None). Strings are literal; every occurrence is replaced.
MUTANTS = [
    # --- the allowlist decision
    ("no deny-by-default (unknown store matches tuple 0)", [("\t}\n\treturn -1;\n}\n\nstatic inline int\nswl_wr_ok(", "\t}\n\treturn 0;\n}\n\nstatic inline int\nswl_wr_ok(")], None),
    ("value not compared", [("} else if (p->val == val) {", "} else if (1) {")], None),
    ("bank index may be >= 0x80", [("if (val < SWW_BANK_ROM) {", "if (1) {")], None),
    ("space not compared", [("if (p->space != space || p->win != win || off < p->off || off > (p->hi ? p->hi : p->off) || (off & 3u) != 0) {", "if (p->win != win || off < p->off || off > (p->hi ? p->hi : p->off) || (off & 3u) != 0) {")], None),
    ("window not compared", [("if (p->space != space || p->win != win || off < p->off || off > (p->hi ? p->hi : p->off) || (off & 3u) != 0) {", "if (p->space != space || off < p->off || off > (p->hi ? p->hi : p->off) || (off & 3u) != 0) {")], None),
    ("offset not compared", [("if (p->space != space || p->win != win || off < p->off || off > (p->hi ? p->hi : p->off) || (off & 3u) != 0) {", "if (p->space != space || p->win != win) {")], None),
    # --- the writer's gates
    ("stopped latch ignored", [("if (x->stopped) {\n\t\treturn -SWW_E_STOPPED;", "if (0) {\n\t\treturn -SWW_E_STOPPED;")], None),
    ("level gate of swl_wr removed", [("if ((int)p->run > x->level - SWW_LEVEL0) {\n\t\treturn swl_stop(x, SWW_E_LEVEL);", "if (0) {\n\t\treturn swl_stop(x, SWW_E_LEVEL);")], None),
    ("level gate of the reads removed", [("(int)swl_rt[i].run > x->level - SWW_LEVEL0", "0")], None),
    ("stage 3 below level 3 allowed", [("if (x->level < SWW_LEVEL0 + 1) {", "if (0) {")], None),
    ("tuple consumed more than once", [("if ((p->flags & SWT_ONCE) && (x->done & (1u << t))) {", "if (0) {")], None),
    ("predecessor not required", [("if (p->pred1 && !(x->done & (1u << (p->pred1 - 1u)))) {", "if (0) {")], None),
    ("phase flags not required", [("if ((x->flags & p->need) != p->need) {", "if (0) {")], None),
    ("bank state: nrb pin of swl_wr", [("(p->flags & SWT_BANK) && (x->nrb != SWW_NRB || ", "(p->flags & SWT_BANK) && (")], None),
    ("bank state: nb pin of swl_wr", [("|| x->nb != SWW_NB || x->nbank >= x->nb ||", "|| x->nbank >= x->nb ||")], None),
    ("bank state: at most nb banks", [("|| x->nbank >= x->nb || val", "|| val")], None),
    ("bank state: ascending order", [("|| val != x->nrb + x->nbank)) {", ")) {")], None),
    ("preconditions not re-read before a store", [("\t\tif (swl_wpre(x)) {\n\t\t\treturn swl_stop(x, SWW_E_PRE);\n\t\t}\n\t\tif ((p->space == SWW_BAR0", "\t\tif ((p->space == SWW_BAR0")], None),
    ("live BAR0 window not compared", [("if ((p->space == SWW_BAR0 || p->space == SWW_BAR2) && (swl_rdx(x, SWW_CFG, 0, p->space == SWW_BAR0 ? SWW_CFG_WIN : SWW_CFG_WIN2, &live) || live != p->win)) {", "if (0) {")], None),
    ("live BAR2 window not compared (run B)", [("if ((p->space == SWW_BAR0 || p->space == SWW_BAR2) && (swl_rdx", "if ((p->space == SWW_BAR0) && (swl_rdx")], None),
    ("dead read accepted", [("if (old == 0xFFFFFFFFu) {", "if (0) {")], None),
    ("old-value condition ignored", [("(p->omask && (old & p->omask) != p->oval)", "0")], None),
    ("window select from an unknown window", [("((p->flags & SWT_WIN) && !swl_win_known(old))", "0")], None),
    ("companion condition ignored", [("if (p->cmask && (swl_rdx(x, space, win, p->coff, &comp) || (comp & p->cmask) != p->cval)) {", "if (0) {")], None),
    ("companion read failure ignored", [("if (p->cmask && (swl_rdx(x, space, win, p->coff, &comp) ||", "if (p->cmask && (0 ||")], None),
    ("store failure ignored", [("if (swl_store(x, space, win, off, val)) {\n\t\treturn swl_stop(x, SWW_E_WR);", "swl_store(x, space, win, off, val);\n\tif (0) {\n\t\treturn swl_stop(x, SWW_E_WR);")], None),
    ("no read-back compare", [("if (swl_rdx(x, space, win, off, &rb) || ((rb ^ val) & p->vmask)) {", "if (swl_rdx(x, space, win, off, &rb)) {")], None),
    ("read-back compares all 32 bits", [("((rb ^ val) & p->vmask)", "((rb ^ val) & 0xFFFFFFFFu)")], None),
    ("read-back removed entirely", [("if (swl_rdx(x, space, win, off, &rb) || ((rb ^ val) & p->vmask)) {", "if (0 && swl_rdx(x, space, win, off, &rb)) {")], None),
    ("read-back read failure ignored", [("if (swl_rdx(x, space, win, off, &rb) || ((rb ^ val) & p->vmask)) {", "if (((rb ^ val) & p->vmask)) {")], None),
    ("old read failure ignored", [("if (swl_rdx(x, space, win, off, &old)) {\n\t\t\treturn swl_stop(x, SWW_E_RD);", "if (0) {\n\t\t\treturn swl_stop(x, SWW_E_RD);")], None),
    ("done bit not recorded", [("\tx->done |= 1u << t;\n", "")], None),
    ("bank count not recorded", [("\t\tx->nbank++;\n", "")], None),
    # --- the 0124 preconditions inside swl_wpre
    ("link precondition dropped", [("bad = swl_pre_link(iso, ltssm, a0, a1);", "bad = 0;")], None),
    ("endpoint precondition dropped", [("bad = swl_pre_ep(cmd, b0, b1);", "bad = 0;")], None),
    ("region 2 not pinned once enabled", [("if (x->flags & SWF_W2) {", "if (0) {")], None),
    ("region 2 word compare dropped", [("if (r2 != w2[i]) {\n\t\t\t\treturn 7;", "if (0) {\n\t\t\t\treturn 7;")], None),
    ("precondition read failure ignored", [("if (swl_rdx(x, SWW_ISO, 0, 0, &iso) || swl_rdx(x, SWW_ELBI, 0, 0x2C8u, &ltssm)) {\n\t\treturn 8;", "if (0) {\n\t\treturn 8;")], None),
    # --- reads: deny by default
    ("read table: space not compared", [("if (swl_rt[i].space == space && swl_rt[i].win == win", "if (swl_rt[i].win == win")], None),
    ("read table: window not compared", [("swl_rt[i].space == space && swl_rt[i].win == win && off", "swl_rt[i].space == space && off")], None),
    ("read table: lower bound not compared", [("off >= swl_rt[i].lo && ", "")], None),
    ("read table: upper bound not compared", [("off <= swl_rt[i].hi && ", "")], None),
    ("read table: alignment not compared", [("&& (off & 3u) == 0) {\n\t\t\treturn (int)i;", ") {\n\t\t\treturn (int)i;")], None),
    ("read of an unlisted register allowed", [("if (i < 0 || (int)swl_rt[i].run", "if ((int)swl_rt[i < 0 ? 0 : i].run")], None),
    # --- stop and restore
    ("STOP does not run the restore", [("\t\tswl_restore(x);\n\t}\n\treturn -code;", "\t}\n\treturn -code;")], None),
    ("restore group never armed", [("\tx->armed |= p->rgrp;\n", "")], None),
    ("restore of a finished step still armed", [("\tx->armed = 0;\n\tx->flags |= flag;", "\tx->flags |= flag;")], None),
    ("restore skipped check dropped", [("\t\t\tif (pre) {\n\t\t\t\tprintf(\"SWEET WLAN: restore of group", "\t\t\tif (0) {\n\t\t\t\tprintf(\"SWEET WLAN: restore of group")], None),
    ("restore read-back not compared", [("&& !((rb ^ r->val) & r->vmask);", ";")], None),
    ("restore window select dropped", [("if (live != r->win && (", "if (0 && (")], None),
    ("restore of the CA7 writes 0", [("{ SWW_RG_CA, SWW_BAR0, SWW_WIN_CA7WRAP, SWL_AGENT_RESET_CTL, 1u, 1u }", "{ SWW_RG_CA, SWW_BAR0, SWW_WIN_CA7WRAP, SWL_AGENT_RESET_CTL, 0u, 1u }")], None),
    ("restore of the SYS_MEM writes 0", [("{ SWW_RG_SM, SWW_BAR0, SWW_WIN_SMWRAP, SWL_AGENT_RESET_CTL, 1u, 1u }", "{ SWW_RG_SM, SWW_BAR0, SWW_WIN_SMWRAP, SWL_AGENT_RESET_CTL, 0u, 1u }")], None),
    ("restore of CR2 writes the enable", [("{ SWW_RG_ATU2, SWW_ATU, 0, SWW_R2(0x04), 0u, SWW_ATU_ENABLE }", "{ SWW_RG_ATU2, SWW_ATU, 0, SWW_R2(0x04), SWW_ATU_ENABLE, SWW_ATU_ENABLE }")], None),
    ("restore of CR2 read-back mask 0", [("{ SWW_RG_ATU2, SWW_ATU, 0, SWW_R2(0x04), 0u, SWW_ATU_ENABLE }", "{ SWW_RG_ATU2, SWW_ATU, 0, SWW_R2(0x04), 0u, 0u }")], None),
    ("CR2 arms no restore", [(".rgrp = SWW_RG_ATU2, .flags", ".flags")], None),
    ("SYS_MEM tuples arm no restore", [(".rgrp = SWW_RG_SM, ", "")], None),
    ("CA7 tuples arm no restore", [(".rgrp = SWW_RG_CA, ", "")], None),
    # --- the table rows themselves
    ("CA7 middle IOCTL value", [(".val = 0x23u,", ".val = 0x63u,")], None),
    ("CA7 final IOCTL value", [(".val = 0x21u,", ".val = 0x20u,")], None),
    ("SYS_MEM middle IOCTL value", [(".val = 0x3u, .vmask = SWW_IOCTL_MASK,\n\t                       .coff = SWL_AGENT_RESET_CTL, .cmask = 1u, .cval = 1u, .need = SWF_W3 }", ".val = 0x7u, .vmask = SWW_IOCTL_MASK,\n\t                       .coff = SWL_AGENT_RESET_CTL, .cmask = 1u, .cval = 1u, .need = SWF_W3 }")], None),
    ("ATU limit one too high", [("SWW_TCM_PA + (SWW_TCM_BYTES - 1u), .vmask", "SWW_TCM_PA + SWW_TCM_BYTES, .vmask")], None),
    ("CR2 may be written over an enabled region", [(".omask = 0xFFFFFFFFu, .oval = 0, ", "")], None),
    ("CR2 old value only bit 31 checked", [(".omask = 0xFFFFFFFFu, .oval = 0, ", ".omask = SWW_ATU_ENABLE, .oval = 0, ")], None),
    ("CR2 not last", [(".pred1 = SWT_ATU_TARGET + 1, .off = SWW_R2(0x04)", ".pred1 = SWT_ATU_BASE + 1, .off = SWW_R2(0x04)")], None),
    ("SYS_MEM release without the middle IOCTL", [(".coff = SWL_AGENT_IOCTL, .cmask = SWW_IOCTL_MASK, .cval = 0x3u, .need = SWF_W3 }", ".coff = SWL_AGENT_IOCTL, .cmask = 0, .cval = 0x3u, .need = SWF_W3 }")], None),
    ("IOCTL read-back mask 0x3FFF -> 0", [("#define SWW_IOCTL_MASK  0x3FFFu", "#define SWW_IOCTL_MASK  0x0u")], None),
    ("bankidx tuple without the coreinfo pin", [(".coff = SWW_SM_COREINFO, .cmask = 0x3FFu, .cval = (SWW_NRB << 5) | SWW_NB, ", "")], None),
    ("bank window tuple row dropped from the phase gate", [(".win = SWW_WIN_SYSMEM, .off = SWW_SM_BANKIDX, .vmask = 0xFFFFFFFFu,\n\t                       ", ".win = SWW_WIN_SYSMEM, .off = SWW_SM_BANKIDX, .vmask = 0,\n\t                       ")], None),
    # --- the steps
    ("W0: BAR2 low pin", [("lo != SWW_BAR2_CFG || ", "")], None),
    ("W0: BAR2 high pin", [("hi != 0 || w84 != 0 || s88", "w84 != 0 || s88")], None),
    ("W0: cfg 0x84 pin", [("|| w84 != 0 || s88 != SWW_CFG88", "|| s88 != SWW_CFG88")], None),
    ("W0: cfg 0x88 pin", [(" || s88 != SWW_CFG88) {", ") {")], None),
    ("W0: preconditions", [("\tx->row = 0;\n\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n", "\tx->row = 0;\n")], None),
    ("W1: CR1 must be 0", [("if (r[0] != 0 || ", "if (")], None),
    ("W1: UPPER_BASE must be 0", [("r[3] != 0 || ", "")], None),
    ("W1: UPPER_TARGET must be 0", [("r[6] != 0 || ", "")], None),
    ("W1: CR2 must be 0", [(" || r[1] != 0) {", ") {")], None),
    ("W1: CR2 only bit 31 checked", [(" || r[1] != 0) {", " || (r[1] & SWW_ATU_ENABLE)) {")], None),
    ("W2: BAR2 map failure ignored", [("if (x->b->map2(x->b->ctx)) {", "if (0) {")], None),
    ("W2: map failure does not restore CR2", [("\t\tx->armed = SWW_RG_ATU2;\n", "")], None),
    ("W3: domain 1 request bit", [("|| !(v & SWW_SRPWR_REQ1)", "")], None),
    ("W3: domain 1 status bit", [("|| !(v & SWW_SRPWR_ST1)", "")], None),
    ("W3: dead read", [("if (v == 0xFFFFFFFFu || !(v", "if (!(v")], None),
    ("wait: condition always true", [("if ((v & mask) == want) {", "if (1) {")], None),
    ("wait: timeout is success", [("\treturn swl_stop(x, SWW_E_WAIT);\n}", "\treturn 0;\n}")], None),
    ("reset: final RESET_ST gate", [("|| st != 0) {", ") {")], None),
    ("reset: final RESET_CTL gate", [("|| (ctl & 1u) != 0", "")], None),
    ("reset: final IOCTL gate", [("if ((io & SWW_IOCTL_MASK) != end", "if (0")], None),
    ("W5: coreinfo dead read", [("if (ci == 0xFFFFFFFFu) {", "if (0) {")], None),
    ("W5: nrb pin", [("if (x->nrb != SWW_NRB || x->nb != SWW_NB", "if (x->nb != SWW_NB")], "swl_wr() pins nrb again (killed above as 'bank state: nrb pin of swl_wr') and stops the same way, before the first bankidx store"),
    ("W5: nb pin", [("|| x->nb != SWW_NB || x->nrb + x->nb > SWW_BANK_ROM", "|| x->nrb + x->nb > SWW_BANK_ROM")], None),
    ("W5: nrb+nb below the ROM bit", [("|| x->nrb + x->nb > SWW_BANK_ROM", "")], "implied by the two pins SWW_NRB + SWW_NB = 29 < 0x80; the independent guard is swl_wr_ok (val < SWW_BANK_ROM), killed above"),
    ("W5: bankinfo dead read", [("if (bi == 0xFFFFFFFFu) {", "if (0) {")], None),
    ("reset: 10 us after the assert", [("\tSWW_TRY(swl_wr(x, SWW_BAR0, win, SWL_AGENT_RESET_CTL, 1u));\n\tswl_delay(x, 10);", "\tSWW_TRY(swl_wr(x, SWW_BAR0, win, SWL_AGENT_RESET_CTL, 1u));")], None),
    ("SYS_MEM wrapper window select without SRPWR proven", [(".val = SWW_WIN_SMWRAP, .vmask = 0xFFFFFFFFu, .need = SWF_W3", ".val = SWW_WIN_SMWRAP, .vmask = 0xFFFFFFFFu, .need = 0")], None),
    ("SYS_MEM core window select without the reset", [(".val = SWW_WIN_SYSMEM, .vmask = 0xFFFFFFFFu, .need = SWF_W4", ".val = SWW_WIN_SYSMEM, .vmask = 0xFFFFFFFFu, .need = 0")], None),
    ("CA7 window select without the banks", [(".val = SWW_WIN_CA7WRAP, .vmask = 0xFFFFFFFFu, .need = SWF_W5", ".val = SWW_WIN_CA7WRAP, .vmask = 0xFFFFFFFFu, .need = 0")], None),
    ("W5: 10 us before bankinfo", [("\t\tswl_delay(x, 10);\n\t\tSWW_RD(x, SWW_BAR0, SWW_WIN_SYSMEM, SWW_SM_BANKINFO, bi);", "\t\tSWW_RD(x, SWW_BAR0, SWW_WIN_SYSMEM, SWW_SM_BANKINFO, bi);")], None),
    ("W5: bank size formula", [("SWW_BANK_SZBASE * ((bi & SWW_BANK_SZMASK) + 1u)", "SWW_BANK_SZBASE * (bi & SWW_BANK_SZMASK)")], None),
    ("W5: bank size mask", [("(bi & SWW_BANK_SZMASK)", "(bi & 0xFFu)")], None),
    ("W5: RAM above 4 MiB", [("sum > SWW_RAM_MAX || ", "")], None),
    ("W5: RAM below the image + vars", [(" || sum < SWW_RAM_MIN", "")], None),
    ("W5: RAM at exactly 4 MiB refused", [("sum > SWW_RAM_MAX", "sum >= SWW_RAM_MAX")], None),
    ("W7: cfg 0x84 pin", [("if (w84 != 0) {\n\t\treturn swl_stop(x, SWW_E_STATE);", "if (0) {\n\t\treturn swl_stop(x, SWW_E_STATE);")], None),
    ("W7: all-ones at the RAM base", [("if (a == 0xFFFFFFFFu || b", "if (b")], None),
    ("W7: all-ones at address 0", [(" || b == 0xFFFFFFFFu) {", ") {")], None),
    ("W7: preconditions", [("\tx->row = 7;\n\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n", "\tx->row = 7;\n")], None),
    # --- run B (patch 0130) limits
    ("run B: off range upper bound p->hi ignored", [("off > (p->hi ? p->hi : p->off) || ", "")], None),
    ("run B: off range upper bound p->hi only the single offset", [("off > (p->hi ? p->hi : p->off)", "off > p->off")], None),
    ("run B: alignment check dropped", [(" || (off & 3u) != 0) {\n\t\t\tcontinue;", ") {\n\t\t\tcontinue;")], None),
    ("run B: store past the image end (table hi one word too high)", [(".hi = SWW_IMG_END - 4u, .vmask", ".hi = SWW_IMG_END, .vmask")], None),
    ("run B: store below the RAM base 0x200000 (table off one word too low)", [(".off = SWW_RAM_BASE, .hi = SWW_IMG_END - 4u", ".off = SWW_RAM_BASE - 4u, .hi = SWW_IMG_END - 4u")], None),
    ("run B: image store beyond the image words (img_next bound dropped)", [("\n\t    x->img_next >= (x->img_len + 3u) / 4u || ", "\n\t    ")], None),
    ("run B: image word not compared with the source", [(" || val != swl_img_word(x, x->img_next))) {", ")) {")], None),
    ("run B: image store address not the next word", [("off != SWW_RAM_BASE + 4u * x->img_next ||", "")], None),
    ("run B: cfg 0x84 window write when ram_end <= 0x400000 (W8 gate)", [("if (SWW_RAM_BASE + x->ram_bytes > SWW_TCM_BYTES) {\n\t\tSWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL));", "if (1) {\n\t\tSWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL));")], None),
    ("run B: cfg 0x84 window write when ram_end <= 0x400000 (swl_wr tail gate)", [("if ((p->flags & SWT_TAIL) && SWW_RAM_BASE + x->ram_bytes <= SWW_TCM_BYTES) {", "if (0) {")], None),
    ("run B: last word offset not pinned to ram_end - 4", [("if ((p->flags & SWT_LAST) && off != SWW_RAM_BASE + x->ram_bytes - 4u - p->win) {", "if (0) {")], None),
    ("run B: cfg 0x84 never restored to 0 in W9", [("if (win != 0) {\n\t\tx->row = 8;", "if (0) {\n\t\tx->row = 8;")], None),
    ("run B: cfg 0x84 restore group not armed", [("SWT_ONCE | SWT_TAIL, .off = SWW_CFG_WIN2, .val = SWW_WIN_TAIL", "SWT_ONCE | SWT_TAIL, .rgrp = 0, .off = SWW_CFG_WIN2, .val = SWW_WIN_TAIL")], None),
    ("run B: STOP restore table lacks cfg 0x84", [("\t{ SWW_RG_W84, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu },\n", "")], None),
    ("run B: preconditions and window not re-read at the first word of a chunk", [("((p->flags & SWT_BULK) && ((off - SWW_RAM_BASE) & (4u * SWW_CHUNK_W - 1u)) == 0)", "(p->flags & SWT_BULK) && 0")], None),
    ("run B: chunk CRC compare dropped", [("if (rc != sc) {", "if (0) {")], None),
    ("run B: final CRC compare dropped", [("if (x->img_crc_rb != x->img_crc || x->img_next != words) {", "if (x->img_next != words) {")],
     None),
    ("run B: chunk and final CRC compares both dropped", [("if (rc != sc) {", "if (0) {"), ("if (x->img_crc_rb != x->img_crc || x->img_next != words) {", "if (x->img_next != words) {")], None),
    ("run B: final word count compare dropped", [("if (x->img_crc_rb != x->img_crc || x->img_next != words) {", "if (x->img_crc_rb != x->img_crc) {")],
     "the loop calls swl_wr for every word 0..words-1 and each success increments img_next by exactly one, a failure returns before the compare, so img_next == words whenever the compare is reached"),
    ("run B: source image CRC not checked before the first store", [("if (~src != x->img_crc) {", "if (0) {")], None),
    ("run B: unusable image not refused before the first store (W0)", [("if (x->level >= SWW_LEVEL0 + 2) {      /* an unusable", "if (0) {      /* an unusable")], None),
    ("run B: SWT_LAST_LO lowered into the image range", [(".off = SWW_IMG_END, .hi = SWW_TCM_BYTES - 4u", ".off = SWW_RAM_BASE, .hi = SWW_TCM_BYTES - 4u")], None),
    ("run B: oversize image accepted", [("x->img_len == 0 || x->img_len > SWW_IMG_MAX) {\n\t\tprintf", "x->img_len == 0) {\n\t\tprintf")], None),

    # --- run C (patch 0131): the process_nvram_vars port and the caller's steps (shim/wlan_nv_host_test.c kills these)
    ("run C nv: CR kept", [("if (vb[n] == '\\r') {", "if (0) {")], None),
    ("run C nv: comment text kept", [("if (find_nl && vb[n] != '\\n') {", "if (0) {")], None),
    ("run C nv: a comment never ends", [("\t\tfind_nl = 0;\n\t\tif (vb[n] == '#') {", "\t\tif (vb[n] == '#') {")], None),
    ("run C nv: '#' is not a comment", [("if (vb[n] == '#') {", "if (0) {")], None),
    ("run C nv: empty lines kept", [("if (column == 0) {\n\t\t\t\tcontinue;", "if (0) {\n\t\t\t\tcontinue;")], None),
    ("run C nv: column not counted", [("\t\tvb[dp++] = vb[n];\n\t\tcolumn++;", "\t\tvb[dp++] = vb[n];")], None),
    ("run C nv: column not reset at a line end", [("\t\t\tvb[dp++] = 0;\n\t\t\tcolumn = 0;\n\t\t\tcontinue;", "\t\t\tvb[dp++] = 0;\n\t\t\tcontinue;")], None),
    ("run C nv: pad also when it does not fit", [("if (buf_len + pad <= len) {", "if (1) {")], None),
    ("run C nv: pad only when it fits strictly", [("if (buf_len + pad <= len) {", "if (buf_len + pad < len) {")], None),
    ("run C nv: pad size", [("pad = 4u - buf_len % 4u;", "pad = 3u - buf_len % 4u;")], None),
    ("run C nv: no pad", [("\tif (buf_len % 4u) {\n\t\tpad = 4u - buf_len % 4u;", "\tif (0) {\n\t\tpad = 4u - buf_len % 4u;")], None),
    ("run C nv: rest not zero filled", [("\twhile (dp < len) {\n\t\tvb[dp++] = 0;\n\t}\n\treturn buf_len;", "\treturn buf_len;")], None),
    ("run C nv: returns the unpadded length", [("\treturn buf_len;\n}\n\n/* The caller", "\treturn dp > buf_len ? dp : buf_len;\n}\n\n/* The caller")], None),
    ("run C dhd: empty file processed", [("if (len == 0 || len >= SWW_NV_DHD_MAX) {", "if (len >= SWW_NV_DHD_MAX) {")], None),
    ("run C dhd: no size cap", [("if (len == 0 || len >= SWW_NV_DHD_MAX) {", "if (len == 0) {")], None),
    ("run C dhd: cap one too high", [("len >= SWW_NV_DHD_MAX", "len > SWW_NV_DHD_MAX")], None),
    ("run C dhd: last input byte not forced to NUL", [("\tvb[len - 1u] = 0;\n", "")], None),
    ("run C dhd: only 4 bytes past len zeroed", [("for (i = 0; i < 8u; i++) {\n\t\tvb[len + i] = 0;", "for (i = 0; i < 4u; i++) {\n\t\tvb[len + i] = 0;")], None),
    ("run C dhd: more than 8 bytes past len zeroed", [("for (i = 0; i < 8u; i++) {\n\t\tvb[len + i] = 0;", "for (i = 0; i < 16u; i++) {\n\t\tvb[len + i] = 0;")], None),
    ("run C dhd: length not rounded up", [("\tif (len % 4u) {\n\t\tlen += 4u - len % 4u;", "\tif (0) {\n\t\tlen += 4u - len % 4u;")], None),
    ("run C dhd: no extra NUL in varsz", [("return len ? len + 1u : 0;", "return len;")], None),
    ("run C dhd: nothing to download gives 1", [("return len ? len + 1u : 0;", "return len + 1u;")], None),
    ("run C dhd: extra NUL not stored", [("\tvb[len] = 0;\n\treturn len ? len + 1u : 0;", "\treturn len ? len + 1u : 0;")],
     "the 8 zero bytes past the input and process_nvram_vars' own zero fill already make vb[len] zero (len <= input + 3)"),
    # --- run C: swl_nv_prep (before W0)
    ("run C prep: no entry accepted", [("x->nv_src == NULL || ", "")], None),
    ("run C prep: empty entry accepted", [("x->nv_src_len == 0 || ", "")], None),
    ("run C prep: input bound dropped", [("x->nv_src_len > SWW_NV_VARS_MAX || ", "")], None),
    ("run C prep: input bound one too low", [("x->nv_src_len > SWW_NV_VARS_MAX ||", "x->nv_src_len >= SWW_NV_VARS_MAX ||")], None),
    ("run C prep: no scratch accepted", [("x->nv == NULL || ", "")], None),
    ("run C prep: scratch size dropped", [(" || x->nv_cap < x->nv_src_len + 8u", "")], None),
    ("run C prep: scratch one byte short accepted", [("x->nv_cap < x->nv_src_len + 8u", "x->nv_cap < x->nv_src_len + 7u")], None),
    ("run C prep: scratch one byte too much demanded", [("x->nv_cap < x->nv_src_len + 8u", "x->nv_cap < x->nv_src_len + 9u")], None),
    ("run C prep: container CRC not compared", [("if (~src != x->nv_src_crc) {", "if (0) {")], None),
    ("run C prep: container CRC not computed", [("\t\tsrc = swl_crc_b(src, x->nv[i]);\n", "")], None),
    ("run C prep: text not copied", [("x->nv[i] = x->nv_src[i];", "x->nv[i] = 0;")], None),
    ("run C prep: nothing left is accepted", [("if (varsz == 0 || varsize > SWW_NV_VARS_MAX) {", "if (varsize > SWW_NV_VARS_MAX) {")], None),
    ("run C prep: varsize bound dropped", [("if (varsz == 0 || varsize > SWW_NV_VARS_MAX) {", "if (varsz == 0) {")], None),
    ("run C prep: varsize bound one too low", [("varsize > SWW_NV_VARS_MAX) {\n\t\tprintf(\"SWEET WLAN: W%u NVRAM text", "varsize >= SWW_NV_VARS_MAX) {\n\t\tprintf(\"SWEET WLAN: W%u NVRAM text")], None),
    ("run C prep: varsize not rounded up", [("varsize = (varsz + 3u) & ~3u;", "varsize = varsz;")], None),
    ("run C prep: no generator accepted", [("x->b->rng == NULL || ", "")], None),
    ("run C prep: generator failure ignored", [("x->b->rng(x->b->ctx, x->rtlv, SWW_RTLV_RNG) != 0", "(x->b->rng(x->b->ctx, x->rtlv, SWW_RTLV_RNG), 0)")], None),
    ("run C prep: all-zero draw accepted", [("if (any == 0) {", "if (0) {")], None),
    ("run C prep: draw not looked at", [("any |= x->rtlv[i];", "any &= x->rtlv[i];")], None),
    ("run C prep: token without the complement", [("x->tok = (~w << 16) | (w & 0xFFFFu);", "x->tok = (w << 16) | (w & 0xFFFFu);")], None),
    ("run C prep: token counts bytes", [("w = varsize / 4u;", "w = varsize;")], None),
    ("run C prep: token low half", [("x->tok = (~w << 16) | (w & 0xFFFFu);", "x->tok = (~w << 16);")], None),
    ("run C prep: token low half unmasked", [("x->tok = (~w << 16) | (w & 0xFFFFu);", "x->tok = (~w << 16) | w;")],
     "w = varsize / 4 <= SWW_NV_VARS_MAX / 4 = 6767 < 0x10000, so the mask is a no-op"),
    ("run C prep: state of an earlier refusal kept", [("\tx->nv_varsize = 0;\n\tif (x->nv_src == NULL", "\tif (x->nv_src == NULL")], None),
    ("run C prep: text CRC over varsz, not varsize", [("for (i = 0; i < varsize; i++) {\n\t\tc = swl_crc_b(c, x->nv[i]);", "for (i = 0; i < varsz; i++) {\n\t\tc = swl_crc_b(c, x->nv[i]);")], None),
    ("run C prep: text CRC not complemented", [("x->nv_crc = ~c;", "x->nv_crc = c;")], None),
    ("run C prep: state set before the entropy", [("\tx->tok = (~w << 16) | (w & 0xFFFFu);     /* dhd_pcie.c:10962-10963 */\n\tx->nv_crc = ~c;\n\tx->nv_varsize = varsize;", "\tx->tok = (~w << 16) | (w & 0xFFFFu);     /* dhd_pcie.c:10962-10963 */\n\tx->nv_crc = ~c;")], None),
    # --- run C: geometry, kinds, words
    ("run C geo: unprepared state accepted", [("if (x->nv_varsize == 0 || x->nv_varsize > SWW_NV_VARS_MAX || (x->nv_varsize & 3u) != 0) {", "if (x->nv_varsize > SWW_NV_VARS_MAX || (x->nv_varsize & 3u) != 0) {")], None),
    ("run C geo: varsize bound dropped", [("x->nv_varsize == 0 || x->nv_varsize > SWW_NV_VARS_MAX || ", "x->nv_varsize == 0 || ")], None),
    ("run C geo: varsize alignment dropped", [(" || (x->nv_varsize & 3u) != 0) {\n\t\treturn SWW_E_NV;", ") {\n\t\treturn SWW_E_NV;")], None),
    ("run C geo: RAM above 4 MiB accepted", [("\tif (x->ram_bytes > SWW_RAM_MAX) {\n\t\treturn SWW_E_RAM;\n\t}\n\tg->tok", "\tg->tok")], None),
    ("run C geo: tail below the window accepted", [("return g->rt < SWW_WIN_TAIL ? SWW_E_GEO : 0;", "return 0;")], None),
    ("run C geo: tail may start exactly at the window floor minus nothing (<=)", [("return g->rt < SWW_WIN_TAIL ? SWW_E_GEO : 0;", "return g->rt <= SWW_WIN_TAIL ? SWW_E_GEO : 0;")], None),
    ("run C geo: only the text must lie in the window", [("return g->rt < SWW_WIN_TAIL ? SWW_E_GEO : 0;", "return g->vars < SWW_WIN_TAIL ? SWW_E_GEO : 0;")], None),
    ("run C geo: token not the last word", [("g->tok = SWW_RAM_BASE + x->ram_bytes - 4u;", "g->tok = SWW_RAM_BASE + x->ram_bytes - 8u;")], None),
    ("run C geo: text overlaps the token", [("g->vars = g->tok - x->nv_varsize;", "g->vars = g->tok - x->nv_varsize + 4u;")], None),
    ("run C geo: gap between text and token", [("g->vars = g->tok - x->nv_varsize;", "g->vars = g->tok - x->nv_varsize - 4u;")], None),
    ("run C geo: rTLV overlaps the text", [("g->rt = g->vars - SWW_RTLV_BYTES;", "g->rt = g->vars - SWW_RTLV_BYTES + 4u;")], None),
    ("run C geo: rTLV one word too low", [("g->rt = g->vars - SWW_RTLV_BYTES;", "g->rt = g->vars - SWW_RTLV_BYTES - 4u;")], None),
    ("run C geo: word count", [("g->vwords = x->nv_varsize / 4u;", "g->vwords = x->nv_varsize / 4u + 1u;")], None),
    ("run C kind: rTLV floor", [("if (bp >= g.rt && bp < g.vars) {", "if (bp > g.rt && bp < g.vars) {")], None),
    ("run C kind: rTLV ceiling", [("if (bp >= g.rt && bp < g.vars) {", "if (bp >= g.rt && bp <= g.vars) {")], None),
    ("run C kind: text floor", [("if (bp >= g.vars && bp < g.tok) {", "if (bp > g.vars && bp < g.tok) {")], None),
    ("run C kind: text ceiling", [("if (bp >= g.vars && bp < g.tok) {", "if (bp >= g.vars && bp <= g.tok) {")], None),
    ("run C kind: any other address is the token", [("return bp == g.tok ? SWK_TOKEN : 0;", "return SWK_TOKEN;")], None),
    ("run C kind: rTLV is the text", [("\t\treturn SWK_RTLV;\n\t}\n\tif (bp >= g.vars", "\t\treturn SWK_VARS;\n\t}\n\tif (bp >= g.vars")], None),
    ("run C kind: no geometry gives the token kind", [("\tif (swl_geo_get(x, &g)) {\n\t\treturn 0;\n\t}\n\tif (bp >= g.rt", "\tif (swl_geo_get(x, &g)) {\n\t\treturn SWK_TOKEN;\n\t}\n\tif (bp >= g.rt")], None),
    ("run C words: token count", [("return k == SWK_VARS ? g->vwords : k == SWK_TOKEN ? 1u : SWW_RTLV_WORDS;", "return k == SWK_VARS ? g->vwords : k == SWK_TOKEN ? 2u : SWW_RTLV_WORDS;")], None),
    ("run C words: rTLV count", [("#define SWW_RTLV_WORDS  (SWW_RTLV_BYTES / 4u)", "#define SWW_RTLV_WORDS  (SWW_RTLV_BYTES / 4u - 1u)")], None),
    ("run C words: token base", [("return k == SWK_VARS ? g->vars : k == SWK_TOKEN ? g->tok : g->rt;", "return k == SWK_VARS ? g->vars : k == SWK_TOKEN ? g->vars : g->rt;")], None),
    ("run C words: rTLV base", [("return k == SWK_VARS ? g->vars : k == SWK_TOKEN ? g->tok : g->rt;", "return k == SWK_VARS ? g->vars : k == SWK_TOKEN ? g->tok : g->vars;")], None),
    ("run C words: big endian", [("v |= (uint32_t)b[4u * i + j] << (8u * j);", "v |= (uint32_t)b[4u * i + j] << (24u - 8u * j);")], None),
    ("run C words: rTLV value from the text", [("const uint8_t *b = k == SWK_VARS ? x->nv : x->rtlv;", "const uint8_t *b = x->nv;")], None),
    ("run C words: length word and type swapped", [("return i == SWW_RTLV_RNG / 4u ? SWW_RTLV_LEN : SWW_RTLV_RNGSIG;", "return i == SWW_RTLV_RNG / 4u ? SWW_RTLV_RNGSIG : SWW_RTLV_LEN;")], None),
    ("run C words: length word index", [("i >= SWW_RTLV_RNG / 4u) {", "i > SWW_RTLV_RNG / 4u) {")], None),
    ("run C words: length word with a checksum", [("#define SWW_RTLV_LEN    0x80u ", "#define SWW_RTLV_LEN    0xFF7F0080u ")], None),
    ("run C words: type word", [("#define SWW_RTLV_RNGSIG 0xFEEDC0DEu ", "#define SWW_RTLV_RNGSIG 0xFEEDFE51u ")], None),
    ("run C words: token is not the stored word", [("\tif (k == SWK_TOKEN) {\n\t\treturn x->tok;\n\t}", "\tif (k == SWK_TOKEN) {\n\t\treturn ~x->tok;\n\t}")], None),
    # --- run C: swl_wr / pick / table
    ("run C pick: tuple not replaced when consumed", [("(((swl_wt[t].flags & SWT_ONCE) && (x->done & (1u << t))) || ((swl_wt[t].flags & SWT_TW)", "((0) || ((swl_wt[t].flags & SWT_TW)")], None),
    ("run C pick: tuple not replaced by kind", [("|| ((swl_wt[t].flags & SWT_TW) && swl_wt[t].kind != swl_tw_kind(x, win + off)))) {", "|| 0)) {")], None),
    ("run C pick: kind taken from the offset", [("swl_wt[t].kind != swl_tw_kind(x, win + off)", "swl_wt[t].kind != swl_tw_kind(x, off)")], None),
    ("run C pick: takeover at a level that does not allow it", [("if (n <= t || (int)swl_wt[n].run > x->level - SWW_LEVEL0) {", "if (n <= t) {")], None),
    ("run C pick: no match reports DENIED", [("\t\t\treturn first;\n\t\t}\n\t\tt = n;", "\t\t\treturn -1;\n\t\t}\n\t\tt = n;")], None),
    ("run C pick: not used", [("t = swl_wr_pick(x, space, win, off, val);", "t = swl_wr_ok(space, win, off, val);")], None),
    ("run C ok: tail tuples need the tuple's value", [("} else if (p->flags & (SWT_BULK | SWT_TW)) {", "} else if (p->flags & SWT_BULK) {")], None),
    ("run C wr: geometry not checked", [("\t\tgr = swl_geo_get(x, &g);\n\t\tif (gr) {\n\t\t\treturn swl_stop(x, gr);\n\t\t}\n", "\t\tgr = swl_geo_get(x, &g);\n")], None),
    ("run C wr: kind index not bounded", [("ti >= swl_tw_n(&g, p->kind) || ", "")], None),
    ("run C wr: address not the next word of the kind", [("win + off != swl_tw_base(&g, p->kind) + 4u * ti || ", "")], None),
    ("run C wr: address always the first word", [("win + off != swl_tw_base(&g, p->kind) + 4u * ti", "win + off != swl_tw_base(&g, p->kind)")], None),
    ("run C wr: word not compared", [(" || val != swl_tw_word(x, p->kind, ti)) {", ") {")], None),
    ("run C wr: word counter not advanced", [("\tif (p->flags & SWT_TW) {\n\t\tx->tn[p->kind]++;\n\t\treturn 0;\n\t}", "\tif (p->flags & SWT_TW) {\n\t\treturn 0;\n\t}")], None),
    ("run C wr: word counter advanced by two", [("x->tn[p->kind]++;", "x->tn[p->kind] += 2;")], None),
    ("run C wr: preconditions only at the first word dropped", [(" || ((p->flags & SWT_TW) && ti == 0);", ";")], None),
    ("run C wr: preconditions at every tail word", [("first = !(p->flags & (SWT_BULK | SWT_TW)) ||", "first = !(p->flags & SWT_BULK) ||")], None),
    ("run C wr: preconditions at no tail word but the first of a chunk", [("(p->flags & SWT_TW) && ti == 0", "(p->flags & SWT_TW) && ti == 1000000u")], None),
    ("run C table: window tuple is run 2", [("[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3,", "[SWT_WIN2_TAIL_C] = { .row = 11, .run = 2,")], None),
    ("run C table: window tuple not ONCE", [("[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84, .flags = SWT_ONCE | SWT_TAIL,", "[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84, .flags = SWT_TAIL,")], None),
    ("run C table: window tuple without the restore group", [("[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84,", "[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = 0,")], None),
    ("run C table: window tuple without the tail gate", [("[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84, .flags = SWT_ONCE | SWT_TAIL,", "[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84, .flags = SWT_ONCE,")], None),
    ("run C table: window tuple old value not pinned", [("[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84, .flags = SWT_ONCE | SWT_TAIL, .off = SWW_CFG_WIN2, .val = SWW_WIN_TAIL, .vmask = 0xFFFFFFFFu,\n\t                     .omask = 0xFFFFFFFFu,", "[SWT_WIN2_TAIL_C] = { .row = 11, .run = 3, .space = SWW_CFG, .rgrp = SWW_RG_W84, .flags = SWT_ONCE | SWT_TAIL, .off = SWW_CFG_WIN2, .val = SWW_WIN_TAIL, .vmask = 0xFFFFFFFFu,\n\t                     .omask = 0,")], None),
    ("run C table: window tuple phase", [(".omask = 0xFFFFFFFFu, .oval = 0, .need = SWF_W10 },\n\t[SWT_NV_VARS]", ".omask = 0xFFFFFFFFu, .oval = 0, .need = SWF_W9 },\n\t[SWT_NV_VARS]")], None),
    ("run C table: text tuple is run 2", [("[SWT_NV_VARS]    = { .row = 11, .run = 3,", "[SWT_NV_VARS]    = { .row = 11, .run = 2,")], None),
    ("run C table: token tuple is run 2", [("[SWT_NV_TOKEN]   = { .row = 12, .run = 3,", "[SWT_NV_TOKEN]   = { .row = 12, .run = 2,")], None),
    ("run C table: rTLV tuple is run 2", [("[SWT_NV_RTLV]    = { .row = 13, .run = 3,", "[SWT_NV_RTLV]    = { .row = 13, .run = 2,")], None),
    ("run C table: restore tuple is run 2", [("[SWT_WIN2_ZERO_C] = { .row = 13, .run = 3,", "[SWT_WIN2_ZERO_C] = { .row = 13, .run = 2,")], None),
    ("run C table: text tuple without order", [(".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1,", ".kind = SWK_VARS,")], None),
    ("run C table: token tuple without order", [(".kind = SWK_TOKEN, .pred1 = SWT_WIN2_TAIL_C + 1,", ".kind = SWK_TOKEN,")], None),
    ("run C table: rTLV tuple without order", [(".kind = SWK_RTLV, .pred1 = SWT_WIN2_TAIL_C + 1,", ".kind = SWK_RTLV,")], None),
    ("run C table: restore tuple without order", [(".flags = SWT_ONCE | SWT_TAIL, .pred1 = SWT_WIN2_TAIL_C + 1, .off = SWW_CFG_WIN2, .val = 0,", ".flags = SWT_ONCE | SWT_TAIL, .off = SWW_CFG_WIN2, .val = 0,")], None),
    ("run C table: text tuple needs no image", [(".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL, .off = 0,\n\t                     .hi = SWW_LAST_HI_MAX - 4u, .vmask = 0xFFFFFFFFu, .need = SWF_W10 },", ".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL, .off = 0,\n\t                     .hi = SWW_LAST_HI_MAX - 4u, .vmask = 0xFFFFFFFFu, .need = SWF_W9 },")], None),
    ("run C table: token tuple needs no text", [(".hi = SWW_LAST_HI_MAX, .vmask = 0xFFFFFFFFu, .need = SWF_W11 },", ".hi = SWW_LAST_HI_MAX, .vmask = 0xFFFFFFFFu, .need = SWF_W10 },")], None),
    ("run C table: rTLV tuple needs no token", [(".hi = SWW_LAST_HI_MAX - 4u, .vmask = 0xFFFFFFFFu, .need = SWF_W12 },", ".hi = SWW_LAST_HI_MAX - 4u, .vmask = 0xFFFFFFFFu, .need = SWF_W11 },")], None),
    ("run C table: restore tuple needs no rTLV", [(".omask = 0xFFFFFFFFu, .oval = SWW_WIN_TAIL, .need = SWF_RTLV },", ".omask = 0xFFFFFFFFu, .oval = SWW_WIN_TAIL, .need = SWF_W12 },")], None),
    ("run C table: restore tuple old value not pinned", [(".omask = 0xFFFFFFFFu, .oval = SWW_WIN_TAIL, .need = SWF_RTLV },", ".omask = 0, .oval = SWW_WIN_TAIL, .need = SWF_RTLV },")], None),
    ("run C table: restore tuple not ONCE", [("[SWT_WIN2_ZERO_C] = { .row = 13, .run = 3, .space = SWW_CFG, .flags = SWT_ONCE | SWT_TAIL,", "[SWT_WIN2_ZERO_C] = { .row = 13, .run = 3, .space = SWW_CFG, .flags = SWT_TAIL,")], None),
    ("run C table: restore tuple without the tail gate", [("[SWT_WIN2_ZERO_C] = { .row = 13, .run = 3, .space = SWW_CFG, .flags = SWT_ONCE | SWT_TAIL,", "[SWT_WIN2_ZERO_C] = { .row = 13, .run = 3, .space = SWW_CFG, .flags = SWT_ONCE,")], None),
    ("run C table: text tuple in window 0", [(".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL,", ".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = 0,")], None),
    ("run C table: token tuple in window 0", [(".kind = SWK_TOKEN, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL,", ".kind = SWK_TOKEN, .pred1 = SWT_WIN2_TAIL_C + 1, .win = 0,")], None),
    ("run C table: rTLV tuple in window 0", [(".kind = SWK_RTLV, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL,", ".kind = SWK_RTLV, .pred1 = SWT_WIN2_TAIL_C + 1, .win = 0,")], None),
    ("run C table: text tuple range too high", [(".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL, .off = 0,\n\t                     .hi = SWW_LAST_HI_MAX - 4u,", ".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL, .off = 0,\n\t                     .hi = SWW_LAST_HI_MAX,")], None),
    ("run C table: token tuple range too high", [(".hi = SWW_LAST_HI_MAX, .vmask = 0xFFFFFFFFu, .need = SWF_W11 },", ".hi = SWW_LAST_HI_MAX + 4u, .vmask = 0xFFFFFFFFu, .need = SWF_W11 },")], None),
    ("run C table: rTLV tuple range too high", [(".hi = SWW_LAST_HI_MAX - 4u, .vmask = 0xFFFFFFFFu, .need = SWF_W12 },", ".hi = SWW_LAST_HI_MAX, .vmask = 0xFFFFFFFFu, .need = SWF_W12 },")], None),
    ("run C table: text tuple range starts above 0", [(".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL, .off = 0,", ".kind = SWK_VARS, .pred1 = SWT_WIN2_TAIL_C + 1, .win = SWW_WIN_TAIL, .off = 4,")], None),
    ("run C table: text tuple kind", [(".kind = SWK_VARS,", ".kind = SWK_RTLV,")], None),
    ("run C table: token tuple kind", [(".kind = SWK_TOKEN,", ".kind = SWK_VARS,")], None),
    # --- run C: the steps
    ("run C W11: window not moved", [("\tSWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL));        /* ref: dhd_pcie.c:6193-6197 (setbar1win) */\n", "")], None),
    ("run C W11: one word short", [("for (i = 0; i < g.vwords; i++) {\n\t\tSWW_TRY(swl_wr(x, SWW_BAR2, SWW_WIN_TAIL, g.vars", "for (i = 0; i + 1 < g.vwords; i++) {\n\t\tSWW_TRY(swl_wr(x, SWW_BAR2, SWW_WIN_TAIL, g.vars")], None),
    ("run C W11: text not verified", [("\tSWW_TRY(swl_nv_verify(x));\n\tprintf(\"SWEET WLAN: W11 NVRAM text", "\tprintf(\"SWEET WLAN: W11 NVRAM text")], None),
    ("run C W11: phase not recorded", [("\tx->flags |= SWF_W11;\n", "")], None),
    ("run C W11: geometry not looked at", [("\tx->row = 11;\n\trc = swl_geo_get(x, &g);\n\tif (rc) {\n\t\treturn swl_stop(x, rc);\n\t}\n\tSWW_TRY(swl_wr(x, SWW_CFG", "\tx->row = 11;\n\trc = swl_geo_get(x, &g);\n\tSWW_TRY(swl_wr(x, SWW_CFG")], None),
    ("run C W12: phase not recorded", [("\tx->flags |= SWF_W12;\n", "")], None),
    ("run C W13: one rTLV word short", [("for (i = 0; i < SWW_RTLV_WORDS; i++) {\n\t\tSWW_TRY(swl_wr(x, SWW_BAR2, SWW_WIN_TAIL, g.rt", "for (i = 0; i + 1 < SWW_RTLV_WORDS; i++) {\n\t\tSWW_TRY(swl_wr(x, SWW_BAR2, SWW_WIN_TAIL, g.rt")], None),
    ("run C W13: rTLV not verified", [("\tSWW_TRY(swl_tw_verify(x, SWK_RTLV));\n\tSWW_TRY(swl_nv_verify(x));", "\tSWW_TRY(swl_nv_verify(x));")], None),
    ("run C W13: text not verified again", [("\tSWW_TRY(swl_nv_verify(x));\n\tSWW_TRY(swl_tw_verify(x, SWK_TOKEN));", "\tSWW_TRY(swl_tw_verify(x, SWK_TOKEN));")], None),
    ("run C W13: token not verified again", [("\tSWW_TRY(swl_nv_verify(x));\n\tSWW_TRY(swl_tw_verify(x, SWK_TOKEN));\n", "\tSWW_TRY(swl_nv_verify(x));\n")], None),
    ("run C W13: window not restored", [("\tx->flags |= SWF_RTLV;\n\tSWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, 0u));\n", "\tx->flags |= SWF_RTLV;\n")], None),
    ("run C W13: restore group left armed", [("\treturn swl_done(x, SWF_W13);", "\tx->flags |= SWF_W13;\n\treturn 0;")], None),
    ("run C verify: last word of a kind not read", [("for (i = 0; i < swl_tw_n(&g, k); i++) {", "for (i = 0; i + 1 < swl_tw_n(&g, k); i++) {")], None),
    ("run C verify: first word of a kind not read", [("for (i = 0; i < swl_tw_n(&g, k); i++) {", "for (i = 1; i < swl_tw_n(&g, k); i++) {")], None),
    ("run C verify: word not compared", [("\t\tif (v != swl_tw_word(x, k, i)) {", "\t\tif (0) {")], None),
    ("run C verify: text CRC not compared", [("\tif (x->nv_crc_rb != x->nv_crc) {", "\tif (0) {")], None),
    ("run C verify: text CRC over one byte less", [("c = swl_crc_word(c, v, i, x->nv_varsize);", "c = swl_crc_word(c, v, i, x->nv_varsize - 1u);")], None),
    ("run C verify: last text word not read", [("\tfor (i = 0; i < g.vwords; i++) {\n\t\tSWW_RD(x, SWW_BAR2, SWW_WIN_TAIL, g.vars + 4u * i - SWW_WIN_TAIL, v);", "\tfor (i = 0; i + 1 < g.vwords; i++) {\n\t\tSWW_RD(x, SWW_BAR2, SWW_WIN_TAIL, g.vars + 4u * i - SWW_WIN_TAIL, v);")], None),
    ("run C stage: tail geometry not checked after W7", [("\t\trc = rc ? rc : swl_tail_check(x);\n", "")], None),
    ("run C stage: tail check does not refuse", [("\tif (rc) {\n\t\tprintf(\"SWEET WLAN: W11 tail geometry refused", "\tif (0) {\n\t\tprintf(\"SWEET WLAN: W11 tail geometry refused")], None),
    ("run C stage: NVRAM not prepared", [("\t\trc = swl_nv_prep(x);\n\t\tif (rc) {", "\t\trc = 0;\n\t\tif (rc) {")], None),
    ("run C stage: preparation refusal ignored", [("\t\trc = swl_nv_prep(x);\n\t\tif (rc) {\n\t\t\tprintf(\"SWEET WLAN: stage 3 level %d stopped at W%u restore=%s\\n\", x->level, x->row, swl_rsname(x->restore));\n\t\t\treturn rc;\n\t\t}", "\t\trc = swl_nv_prep(x);\n\t\tif (rc) {\n\t\t\tprintf(\"SWEET WLAN: stage 3 level %d stopped at W%u restore=%s\\n\", x->level, x->row, swl_rsname(x->restore));\n\t\t}")], None),
    ("run C stage: prepared at level 4", [("if (x->level >= SWW_LEVEL0 + 3) {      /* run C: the NVRAM entry is checked", "if (x->level >= SWW_LEVEL0 + 2) {      /* run C: the NVRAM entry is checked")], None),
    ("run C stage: prepared only above level 5", [("if (x->level >= SWW_LEVEL0 + 3) {      /* run C: the NVRAM entry is checked", "if (x->level >= SWW_LEVEL0 + 4) {      /* run C: the NVRAM entry is checked")], None),
    ("run C stage: W11..W13 at level 4", [("if (x->level >= SWW_LEVEL0 + 3) {\n\t\trc = rc ? rc : swl_w11(x);", "if (x->level >= SWW_LEVEL0 + 2) {\n\t\trc = rc ? rc : swl_w11(x);")], None),
    ("run C stage: W11..W13 above level 5 only", [("if (x->level >= SWW_LEVEL0 + 3) {\n\t\trc = rc ? rc : swl_w11(x);", "if (x->level >= SWW_LEVEL0 + 4) {\n\t\trc = rc ? rc : swl_w11(x);")], None),
    ("run C stage: W12 skipped", [("\t\trc = rc ? rc : swl_w12(x);\n", "")], None),
    ("run C stage: final line of level 5 missing", [("if (x->level >= SWW_LEVEL0 + 3) {\n\t\tprintf(\"SWEET WLAN: stage 3 level %d done vars", "if (0) {\n\t\tprintf(\"SWEET WLAN: stage 3 level %d done vars")], None),
    ("run C stage: final line without the read-back CRC", [("x->level, x->nv_varsize, x->tok, x->nv_crc_rb);", "x->level, x->nv_varsize, x->tok, 0u);")], None),
    ("run C stage: final line token", [("x->level, x->nv_varsize, x->tok, x->nv_crc_rb);", "x->level, x->nv_varsize, ~x->tok, x->nv_crc_rb);")], None),
]


def run_bounded(cmd, d, timeout):
    """Run a test binary with its output sent to files capped by RLIMIT_FSIZE (1 MiB): a mutant that loops forever and prints must not fill the parent's memory (a captured pipe did, 6.7 GB). Returns (returncode, stderr tail, stdout tail)."""
    def cap():
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    with open(os.path.join(d, "out.txt"), "wb") as fo, open(os.path.join(d, "err.txt"), "wb") as fe:
        rc = subprocess.run(cmd, stdout=fo, stderr=fe, timeout=timeout, preexec_fn=cap).returncode
    tails = []
    for n, k in (("err.txt", 200), ("out.txt", 600)):
        with open(os.path.join(d, n), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - k))
            tails.append(f.read().decode("utf-8", "replace"))
    return rc, tails[0], tails[1]


def build_and_run(hdr, chip, work):
    d = tempfile.mkdtemp(dir=work)
    for name, text in (("sweet_wlan_chip.h", chip), ("sweet_wlan_wr.h", hdr)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    res = []
    for i, test in enumerate((TEST_NV, TEST)):         # the cheap pure test first
        exe = os.path.join(d, "t%d" % i)
        cc = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-I" + d, "-o", exe, test], capture_output=True, text=True, timeout=120)
        if cc.returncode != 0:
            return "compile", cc.stderr[-400:]
        r = run_bounded([exe], d, 120)
        if r[0] != 0:
            return "fail", r[1]
        res.append(r[1])
    return "pass", res[-1]


def run_all():
    hdr = final_header()
    chip = new_file(PATCH0120, "bsd/dev/sweet_wlan_chip.h")
    out = {}
    os.makedirs(os.path.join(ROOT, "shim", "out"), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "shim", "out")) as work:
        base = build_and_run(hdr, chip, work)
        jobs = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for name, subs, equiv in MUTANTS:
                m = hdr
                miss = [o for o, _n in subs if o not in m]
                if miss:
                    out[name] = ("nomatch", repr(miss[0])[:120], equiv)
                    continue
                for o, n in subs:
                    m = m.replace(o, n)
                if m == hdr:
                    out[name] = ("nochange", "", equiv)
                    continue
                jobs[name] = (ex.submit(build_and_run, m, chip, work), equiv)
            for name, (fut, equiv) in jobs.items():
                st, msg = fut.result()
                out[name] = (st, msg, equiv)
    return base, out


_FUNC = re.compile(r"^(\w+)\(.*?^}$", re.S | re.M)


def _inserted(a, b):
    """The lines of b that are not in a, by difflib opcodes."""
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(b[j1:j2])
    return out


def glue_added_lines():
    """What patches 0129 and 0130 add to bsd/dev/sweet_wlan.c: first the added or changed lines outside the functions and inside the functions that already existed after 0128 (fragments, which the
    audit attributes to sweet_wlan_init), then every function that is new, whole (so the audit sees its first and last line)."""
    before, after = glue_before(), glue_after()
    fb = {m.group(1): m.group(0) for m in _FUNC.finditer(before)}
    fa = {m.group(1): m.group(0) for m in _FUNC.finditer(after)}
    rest_b, rest_a = _FUNC.sub("", before), _FUNC.sub("", after)
    out = _inserted(rest_b.split("\n"), rest_a.split("\n"))
    for name, text in fa.items():
        if name in fb and text != fb[name]:
            out.extend(_inserted(fb[name].split("\n"), text.split("\n")))
    for name, text in fa.items():
        if name not in fb:
            out.extend(text.split("\n"))
    if not out:
        raise ValueError("patches 0129/0130 add nothing to " + GLUE_PATH)
    return "\n".join(out) + "\n"


# ---- the glue limit sw3_ptr..sw3_wr, compiled and swept (review 0125 R2-2/R2-3) ----
GLUE_HARNESS = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "sweet_wlan_wr.h"
#define __builtin_arm_dsb(x) ((void)0)
struct sw_hw {
	volatile uint32_t *cfg, *atu, *elbi, *iso, *mem, *tcm;
	uint32_t win;
	int level;
};
@GLUE@
static uint32_t cfg[0x1000 / 4], atu[0x1000 / 4], elbi[0x2000 / 4], iso[1], mem[0x1000 / 4], tcm[0x400000 / 4];
static uint32_t *const arr[] = { cfg, atu, elbi, iso, mem, tcm };
static const size_t len[] = { 0x1000 / 4, 0x1000 / 4, 0x2000 / 4, 1, 0x1000 / 4, 0x2008 / 4 };
static uint32_t snap[6][0x2008 / 4];

/* the table's offsets, restated: cfg 0x80, cfg 0x84 only as 0 or 0x400000; ATU region 2 CR2/LOWER_BASE/LIMIT/LOWER_TARGET; BAR0 bankidx on SYS_MEM, IOCTL + RESET_CTL on the two wrappers;
 * BAR2 is swept separately below (its offsets are above 0x200000, the main sweep only reaches 0x2004) */
static int expect(int space, uint32_t off, uint32_t win, int eq, uint32_t v)
{
	if (space == SWW_CFG) return off == 0x80 || (off == 0x84 && (v == 0 || v == 0x400000u));
	if (space == SWW_ATU) return off == 0x404 || off == 0x408 || off == 0x410 || off == 0x414;
	if (space == SWW_BAR0 && eq) {
		if (win == SWW_WIN_SYSMEM) return off == 0x10;
		if (win == SWW_WIN_SMWRAP || win == SWW_WIN_CA7WRAP) return off == 0x408 || off == 0x800;
	}
	return 0;
}

int main(void)
{
	static const uint32_t wins[] = { SWW_WIN_PCIE, SWW_WIN_SYSMEM, SWW_WIN_SMWRAP, SWW_WIN_CA7WRAP, 0x18004000u };
	static const uint32_t vals[] = { 0xDEADBEEFu, 0u, 0x400000u };
	static const uint32_t bwins[] = { 0u, 0x400000u, 0x200000u, 0x800000u, 0x18001000u };
	struct sw_hw hw = { cfg, atu, elbi, iso, mem, tcm, 0, 4 };
	int bad = 0, acc = 0;

	(void)sw3_rd;
	for (unsigned wi = 0; wi < 5; wi++) {
		for (int eq = 0; eq < 2; eq++) {
			for (int space = 0; space < 8; space++) {
				for (uint32_t off = 0; off < 0x2004; off++) {
					for (unsigned vi = 0; vi < (space == SWW_CFG ? 3u : 1u); vi++) {
						for (int a = 0; a < 6; a++) {
							for (size_t i = 0; i < len[a]; i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
							memcpy(snap[a], arr[a], len[a] * 4);
						}
						cfg[0x80 / 4] = eq ? wins[wi] : wins[wi] ^ 0x1000u;
						memcpy(snap[0] + 0x80 / 4, &cfg[0x80 / 4], 4);
						int r = sw3_wr(&hw, space, wins[wi], off, vals[vi]);
						int ch = 0;
						for (int a = 0; a < 6; a++)
							for (size_t i = 0; i < len[a]; i++) ch += arr[a][i] != snap[a][i];
						int want = expect(space, off, wins[wi], eq, vals[vi]);
						if ((r == 0) != want || ch != (r == 0 ? 1 : 0)) {
							if (bad++ < 20) printf("BAD space %d off %#x win %#x live-equal %d val %#x: ret %d changed %d, want accept %d\n", space, off, wins[wi], eq, vals[vi], r, ch, want);
						}
						acc += r == 0;
					}
				}
			}
		}
	}
	/* BAR2 (patch 0130): window 0 from 0x200000 to the end of the 4 MiB, window 0x400000 up to offset 0x1FFFFC (the last word of a 6 MiB RAM), only while the live cfg 0x84 equals the window,
	 * only 4-aligned, and never a store anywhere else: every other word of every array keeps its value (the target word is restored after each call; the sweep ends with a full compare) */
	for (int a = 0; a < 6; a++)
		for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
	for (unsigned wi = 0; wi < 5; wi++) {
		for (int eq = 0; eq < 2; eq++) {
			cfg[0x84 / 4] = eq ? bwins[wi] : bwins[wi] ^ 0x1000u;
			for (uint32_t off = 0; off < 0x400000 + 0x40; off += 4) {
				for (uint32_t u = 0; u < (eq && wi < 2 && (off & 0xFFF) < 8 ? 4u : 1u); u++) {
					uint32_t o = off + u, keep = o < 0x400000 ? tcm[o / 4] : 0;
					int r = sw3_wr(&hw, SWW_BAR2, bwins[wi], o, 0xDEADBEEFu);
					int want = eq && u == 0 && off < 0x400000 && ((bwins[wi] == 0 && off >= 0x200000) || (bwins[wi] == 0x400000 && off <= 0x1FFFFC));
					if ((r == 0) != want || (r == 0 && tcm[o / 4] != 0xDEADBEEFu)) {
						if (bad++ < 20) printf("BAD BAR2 win %#x off %#x live-equal %d: ret %d, want accept %d\n", bwins[wi], o, eq, r, want);
					}
					if (o < 0x400000) tcm[o / 4] = keep;
					acc += r == 0;
				}
			}
		}
	}
	cfg[0x84 / 4] = 0x10000000u + 0x84 / 4;
	for (int a = 0; a < 6; a++)
		for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++)
			if (arr[a][i] != 0x10000000u * (a + 1) + (uint32_t)i && bad++ < 20) printf("BAD BAR2 sweep changed array %d word %zu\n", a, i);
	printf("%s accepted %d bad %d\n", bad ? "FAIL" : "PASS", acc, bad);
	return bad != 0;
}
"""


def glue_function_text():
    """sw3_ptr .. the closing brace of sw3_wr from the glue after patches 0129 and 0130, with the return type of sw3_ptr put back."""
    g = glue_after()
    a = re.search(r"^sw3_ptr\(", g, re.M)
    e = re.search(r"^sw3_wr\(.*?^}$", g, re.S | re.M)
    if not a or not e:
        raise ValueError("sw3_ptr..sw3_wr not found in the glue")
    return "static volatile uint32_t *\n" + g[a.start():e.end()] + "\n"


def run_glue(glue_text, work, extra=()):
    """Compile the glue functions with gcc -Werror against the real header, sweep them. Returns ('pass'|'fail'|'compile', output)."""
    d = tempfile.mkdtemp(dir=work)
    for text, name in ((new_file(PATCH0120, "bsd/dev/sweet_wlan_chip.h"), "sweet_wlan_chip.h"), (final_header(), "sweet_wlan_wr.h")):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(d, "g.c"), "w", encoding="utf-8") as fh:
        fh.write(GLUE_HARNESS.replace("@GLUE@", glue_text))
    c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + d] + list(extra) + ["-o", os.path.join(d, "g"), os.path.join(d, "g.c")], capture_output=True, text=True)
    if c.returncode != 0:
        return "compile", c.stderr[-600:]
    r = run_bounded([os.path.join(d, "g")], d, 300)
    return ("pass" if r[0] == 0 else "fail"), r[2]


_LIM = "\tif ((space == SWW_CFG && off != SWW_CFG_WIN &&"
_MOVE = "@move"  # special: the offset check block is moved behind the store (see test_glue_mutants_are_killed)
_LIMEND = "SWW_LAST_HI_MAX)))) {\n\t\treturn -1;\n\t}\n"
# (name, [(old, new)]) applied to the glue text; each must change it and the sweep must FAIL (a compile error is a broken mutant, not a kill)
GLUE_MUTANTS = [
    ("UPPER_BASE writable", [(" || off == SWW_R2(0x0C)", "")]),
    ("CR1 writable", [("off < SWW_R2(0x04)", "off < SWW_R2(0x00)")]),
    ("any BAR0 offset", [("(space == SWW_BAR0 && off != SWL_AGENT_IOCTL", "(space == SWW_BAR0 || off != SWL_AGENT_IOCTL")]),
    ("silent no-op success", [("SWW_LAST_HI_MAX)))) {\n\t\treturn -1;", "SWW_LAST_HI_MAX)))) {\n\t\treturn 0;")]),
    ("limit disabled", [(_LIM, "\tif (0 && ((space == SWW_CFG && off != SWW_CFG_WIN &&"), ("SWW_LAST_HI_MAX)))) {", "SWW_LAST_HI_MAX))))) {")]),
    ("limit evaluated after the store", [(_MOVE, "")]),
    ("CFG clause kept as a string needle", [("(space == SWW_CFG && off != SWW_CFG_WIN && !(", "(space == SWW_CFG && off > 0x1000 && *\"off != SWW_CFG_WIN\" && !(")]),
    # R2-3 and the live-window compare
    ("window binding removed", [("\tif (space == SWW_BAR0 && !(off == SWW_SM_BANKIDX ?", "\tif (0 && !(off == SWW_SM_BANKIDX ?")]),
    ("bankidx bound to the wrapper window", [("off == SWW_SM_BANKIDX ? win == SWW_WIN_SYSMEM :", "off == SWW_SM_BANKIDX ? win == SWW_WIN_SMWRAP :")]),
    ("CA7 window refused", [("win == SWW_WIN_SMWRAP || win == SWW_WIN_CA7WRAP", "win == SWW_WIN_SMWRAP")]),
    ("live BAR0 window not compared", [("(space == SWW_BAR0 && h->cfg[SWW_CFG_WIN / 4] != win) || (space == SWW_BAR2", "0 || (space == SWW_BAR2")]),
    # patch 0130: cfg 0x84 and BAR2
    ("cfg 0x84 not writable", [(" && !(off == SWW_CFG_WIN2 && (v == 0 || v == SWW_WIN_TAIL))", "")]),
    ("cfg 0x84 any value", [("(v == 0 || v == SWW_WIN_TAIL)", "1")]),
    ("cfg 0x84 only 0", [("(v == 0 || v == SWW_WIN_TAIL)", "(v == 0)")]),
    ("cfg 0x84 only the tail window", [("(v == 0 || v == SWW_WIN_TAIL)", "(v == SWW_WIN_TAIL)")]),
    ("cfg 0x88 writable too", [("(off == SWW_CFG_WIN2 &&", "(off >= SWW_CFG_WIN2 &&")]),
    ("BAR2 refused", [("space != SWW_BAR0 && space != SWW_BAR2) {", "space != SWW_BAR0) {")]),
    ("BAR2 not limited", [(" ||\n\t    (space == SWW_BAR2 && !((win == 0 && off >= SWW_RAM_BASE) || (win == SWW_WIN_TAIL && off <= SWW_LAST_HI_MAX)))", "")]),
    ("BAR2 store below the RAM base", [("(win == 0 && off >= SWW_RAM_BASE)", "(win == 0)")]),
    ("BAR2 store one word below the RAM base", [("off >= SWW_RAM_BASE)", "off >= SWW_RAM_BASE - 4u)")]),
    ("BAR2 tail window past the last word", [("off <= SWW_LAST_HI_MAX)", "off <= SWW_TCM_BYTES)")]),
    ("BAR2 tail window one word past the last", [("off <= SWW_LAST_HI_MAX)", "off <= SWW_LAST_HI_MAX + 4u)")]),
    ("BAR2 any window below the tail bound", [("(win == SWW_WIN_TAIL && off <= SWW_LAST_HI_MAX)", "(off <= SWW_LAST_HI_MAX)")]),
    ("BAR2 live window not compared", [(" || (space == SWW_BAR2 && h->cfg[SWW_CFG_WIN2 / 4] != win)", "")]),
    ("BAR2 compared against the BAR0 window", [("(space == SWW_BAR2 && h->cfg[SWW_CFG_WIN2 / 4] != win)", "(space == SWW_BAR2 && h->cfg[SWW_CFG_WIN / 4] != win)")]),
    ("store past the 4 MiB window", [("off < SWW_TCM_BYTES ? &h->tcm[off / 4]", "off <= SWW_TCM_BYTES ? &h->tcm[off / 4]")]),
    ("unaligned BAR2 store", [("if ((off & 3u) != 0) {\n\t\treturn NULL;", "if ((off & 1u) != 0) {\n\t\treturn NULL;")]),
]


class WlanWrMutants(unittest.TestCase):
    def test_every_guard_rule_is_killed(self):
        base, out = run_all()
        self.assertEqual(base[0], "pass", "the unmutated header must pass the host test: " + base[1])
        bad = []
        for name, (st, msg, equiv) in out.items():
            if st == "fail":
                continue
            if st == "pass" and equiv:
                continue
            bad.append("%s: %s %s" % (name, "SURVIVED" if st == "pass" else st, msg))
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertEqual(len(out), len(MUTANTS))

    def test_store_path_audit_is_clean_on_the_patch(self):
        self.assertEqual(audit_store_path(final_header(), glue_added_lines()), [])

    def test_store_path_audit_fails_on_a_hostile_corpus(self):
        hdr = final_header()
        glue = glue_added_lines()
        bad = [
            (hdr + "\nstatic inline void x(void) { __asm__ volatile(\"dsb sy\"); }\n", glue, "inline asm"),
            (hdr + "\nstatic inline void x(void) { *(volatile uint32_t *)0x14B00404 = 0; }\n", glue, "pointer from an integer literal"),
            (hdr + "\nstatic inline void x(void) { volatile uint32_t *p = (volatile uint32_t *)0x14B00404; (void)p; }\n", glue, "pointer from an integer literal"),
            (hdr.replace("return x->b->wr(x->b->ctx, space, win, off, v);", "return 0;") + "\nstatic inline int\nswl_z(struct swl_wx *x)\n{\n\treturn x->b->wr(x->b->ctx, 1, 0, 0, 0);\n}\n", glue, "bus store callback called outside swl_store"),
            (hdr + "\nstatic inline int\nswl_z(struct swl_wx *x)\n{\n\treturn swl_store(x, 1, 0, 0, 0);\n}\n", glue, "swl_store called from swl_z"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\t*p = v;"), "assignment outside the allowlist"),
            (hdr, glue + "\nstatic void sw3_z(volatile uint32_t *p, uint32_t v) { *p = v; }\n", "assignment outside the allowlist"),
            (hdr, glue + "\nstatic void sw3_y(void) { sw3_wr(0, 0, 0, 0, 0); }\n", "sw3_wr used other than as the bus wr callback"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\th->cfg[1] = 6;"), "assignment outside the allowlist"),
            (hdr, glue + "\nstatic void sw3_x(struct sw_hw *h) { h->atu[0x101] = 0; }\n", "assignment outside the allowlist"),
            (hdr, glue + "\nstatic void sw3_q(volatile uint32_t *q) { q[0] = 1; }\n", "assignment outside the allowlist"),
            (hdr, glue.replace("(space == SWW_CFG && off != SWW_CFG_WIN && !(off == SWW_CFG_WIN2 && (v == 0 || v == SWW_WIN_TAIL))) || ", ""), "sw3_wr does not limit the offsets to the table's"),
            (hdr, glue.replace("SWW_LAST_HI_MAX", "SWW_TCM_BYTES"), "sw3_wr does not limit BAR2 and cfg 0x84 to the table's"),
            (hdr, glue.replace("(win == 0 && off >= SWW_RAM_BASE)", "(win == 0)"), "sw3_wr does not limit BAR2 and cfg 0x84 to the table's"),
            (hdr, glue.replace('"FW  "', '"NVRM"'), "sw_fw_note does not pick the FW entry by its tag"),
            (hdr, glue.replace('"NVRM"', '"CLM "'), "sw_fw_note does not pick the NVRM entry by its tag"),
            (hdr, glue.replace("sw_nv.p = base + e[i].offset;", "sw_nv.p = base;"), "assignment outside the allowlist"),
            (hdr, glue.replace("x.nv_cap = sizeof sw_nvbuf;", "x.nv_cap = sizeof sw_nvbuf + 64;"), "assignment outside the allowlist"),
            (hdr, glue.replace("sw_fw.p = base + e[i].offset;", "sw_fw.p = base;"), "assignment outside the allowlist"),
            (hdr, glue.replace("x.img_len = sw_fw.len;", "x.img_len = sw_fw.len + 4;"), "assignment outside the allowlist"),
            (hdr, glue.replace("SWW_R2(0x14)", "SWW_R2(0x18)"), "sw3_wr does not limit the offsets to the table's"),
            (hdr, glue.replace("wb.wr = sw3_wr;", "wb.wr = 0;"), "sw3_wr used other than as the bus wr callback"),
            # review 0125 R2-1: the 12 spellings the old regex missed
            (hdr, glue + "\nstatic void sw3_c(struct sw_hw *h) { h->cfg[1] |= 6; }\n", "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\th->atu[0x101] += 0;"), "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\t*(h->cfg + 1) = 6;"), "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\thw3.cfg[1] = 6;"), "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\tif (h != NULL)\n\t\th->cfg[1] = 6;"), "assignment outside the allowlist"),
            (hdr, glue + "\nstatic void sw3_v(struct sw_hw *h) { volatile uint32_t *v = &h->cfg[1]; *v = 6; }\n", "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\t__builtin_memcpy((void *)&h->cfg[1], &s, 4);"), "memory-copy or atomic store call"),
            (hdr, glue.replace("*p = v;", "*p++ = v;"), "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\t(void)0, h->cfg[1] = 6;"), "assignment outside the allowlist"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\tuint32_t a = h->cfg[1] = 6;"), "assignment outside the allowlist"),
            (hdr, glue + "\nstatic void sw3_w(struct sw_hw *hw, struct swl_wbus wb) { void *w_ = wb.wr; w_(hw, SWW_CFG, 0, 4, 6); }\n", "wr callback used other than by wb.wr = sw3_wr;"),
            (hdr, glue + "\nstatic void sw3_r(struct sw_hw *h) { uint32_t t_; sw_rd(h, 0x18000000u + 0x804u, &t_); }\n", "call of sw_rd in the added glue"),
        ]
        for h, g, want in bad:
            self.assertTrue(any(x.startswith(want) for x in audit_store_path(h, g)), want)

    def test_glue_sweep_accepts_exactly_the_table(self):
        with tempfile.TemporaryDirectory() as work:
            st, out = run_glue(glue_function_text(), work)
        self.assertEqual(st, "pass", out)

    def test_glue_mutants_are_killed(self):
        base = glue_function_text()
        bad = []
        with tempfile.TemporaryDirectory() as work:
            for name, subs in GLUE_MUTANTS:
                t = base
                for old, new in subs:
                    if old == _MOVE:
                        m = re.search(r"\tif \(\(space == SWW_CFG && off != SWW_CFG_WIN &&.*?\n\t}\n", t, re.S)
                        self.assertTrue(m, name)
                        t = t.replace(m.group(0), "").replace("\t*p = v;\n", "\t*p = v;\n" + m.group(0))
                        continue
                    self.assertIn(old, t, name + ": pattern not found (glue changed under the mutant)")
                    t = t.replace(old, new)
                self.assertNotEqual(t, base, name)
                st, out = run_glue(t, work, ["-Wno-parentheses"])
                if st != "fail":
                    bad.append("%s: %s %s" % (name, "SURVIVED" if st == "pass" else "does not compile", out))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_extraction_rejects_a_missing_hunk(self):
        with self.assertRaises(ValueError):
            new_file(PATCH0125, "bsd/dev/does_not_exist.h")


if __name__ == "__main__":
    unittest.main()
