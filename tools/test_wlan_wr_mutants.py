"""Mutation test of the stage-3 chip writer in xnu patch 0129 (bsd/dev/sweet_wlan_wr.h, W-D3 step 2, docs/research/43 s.3).

Each guard rule of swl_wr() / swl_wr_ok() / the steps is removed or weakened in a copy of the header (every occurrence of the pattern), the host test
shim/wlan_wr_host_test.c is built against the copy and must FAIL; a mutant that still passes is a survivor and fails this test, unless it is listed
EQUIVALENT with the clause that implies it (CR-02). A pattern that matches nothing fails too (the header changed under the mutant list).
Patch 0130 (run B: W8 window, W9 last word, W10 image copy) changes both files; the texts under test are the files after 0124, 0128, 0129, 0130 are applied in order (apply_chain).
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
TEST = os.path.join(ROOT, "shim", "wlan_wr_host_test.c")
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
    return apply_chain(HDR_PATH, (PATCH0125, PATCH0130))


def glue_before():
    return apply_chain(GLUE_PATH, (PATCH0120, PATCH0128))


def glue_after():
    return apply_chain(GLUE_PATH, (PATCH0120, PATCH0128, PATCH0125, PATCH0130))


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
    ("sw_fw_note", "for (i = 0;"),
    ("sw_fw_note", 'i++) { if (memcmp(e[i].tag, "", 4) == 0) { sw_fw.p = base + e[i].offset;'),  # string contents are stripped by the scan; audit_store_path pins "FW  "
    ("sw_fw_note", "sw_fw.p = base + e[i].offset;"),
    ("sw_fw_note", "sw_fw.len = e[i].length;"),
    ("sw_fw_note", "sw_fw.crc = e[i].crc;"),
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
    if len(re.findall(r"\bsw3_wr\b", glue)) != 2 or "wb.wr = sw3_wr;" not in glue:
        f.append("sw3_wr used other than as the bus wr callback")
    return f


# (name, [(old, new)], equivalent-because or None). Strings are literal; every occurrence is replaced.
MUTANTS = [
    # --- the allowlist decision
    ("no deny-by-default (unknown store matches tuple 0)", [("\t}\n\treturn -1;\n}\n\nstatic inline int\nswl_win_known", "\t}\n\treturn 0;\n}\n\nstatic inline int\nswl_win_known")], None),
    ("value not compared", [("} else if (p->val == val) {", "} else if (1) {")], None),
    ("bank index may be >= 0x80", [("if (val < SWW_BANK_ROM) {", "if (1) {")], None),
    ("space not compared", [("if (p->space != space || p->win != win || p->off != off) {", "if (p->win != win || p->off != off) {")], None),
    ("window not compared", [("if (p->space != space || p->win != win || p->off != off) {", "if (p->space != space || p->off != off) {")], None),
    ("offset not compared", [("if (p->space != space || p->win != win || p->off != off) {", "if (p->space != space || p->win != win) {")], None),
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
    ("preconditions not re-read before a store", [("\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n\tif (p->space == SWW_BAR0", "\tif (p->space == SWW_BAR0")], None),
    ("live BAR0 window not compared", [("if (p->space == SWW_BAR0 && (swl_rdx(x, SWW_CFG, 0, SWW_CFG_WIN, &live) || live != p->win)) {", "if (0) {")], None),
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
    ("old read failure ignored", [("if (swl_rdx(x, space, win, off, &old)) {\n\t\treturn swl_stop(x, SWW_E_RD);", "if (0) {\n\t\treturn swl_stop(x, SWW_E_RD);")], None),
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
    exe = os.path.join(d, "t")
    cc = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-I" + d, "-o", exe, TEST], capture_output=True, text=True, timeout=120)
    if cc.returncode != 0:
        return "compile", cc.stderr[-400:]
    r = run_bounded([exe], d, 120)
    return ("pass" if r[0] == 0 else "fail"), r[1]


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
