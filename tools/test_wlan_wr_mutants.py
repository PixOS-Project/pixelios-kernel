"""Mutation test of the stage-3 chip writer in xnu patch 0125 (bsd/dev/sweet_wlan_wr.h, W-D3 step 2, docs/research/43 s.3).

Each guard rule of swl_wr() / swl_wr_ok() / the steps is removed or weakened in a copy of the header (every occurrence of the pattern), the host test
shim/wlan_wr_host_test.c is built against the copy and must FAIL; a mutant that still passes is a survivor and fails this test, unless it is listed
EQUIVALENT with the clause that implies it (CR-02). A pattern that matches nothing fails too (the header changed under the mutant list).
Run: python3 -B tools/test_wlan_wr_mutants.py  (also picked up by `unittest discover -s tools -p 'test_*.py'`)."""
import concurrent.futures
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH0120 = os.path.join(ROOT, "xnu", "patches", "0120-sweet-wlan-bwfm-core.patch")
PATCH0125 = os.path.join(ROOT, "xnu", "patches", "0125-sweet-wlan-chip-writes.patch")
TEST = os.path.join(ROOT, "shim", "wlan_wr_host_test.c")


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
    # statement-initial stores through a dereferenced or subscripted pointer; `*v = *p;` is sw3_rd handing the read result to its caller
    stores = [m for m in re.findall(r"(?:^|[{;])[ \t]*((?:\*\s*\w+|\w+(?:->\w+)?\s*\[[^\]]*\])\s*=(?!=))", glue, re.M) if not re.match(r"\*\s*v\s*=", m)]
    if len(stores) != 1 or "*p = v;" not in body(glue, "sw3_wr"):
        f.append("a store through a pointer outside sw3_wr")
    if "off != SWW_CFG_WIN" not in body(glue, "sw3_wr") or "SWW_R2(0x14)" not in body(glue, "sw3_wr") or "SWW_SM_BANKIDX" not in body(glue, "sw3_wr"):
        f.append("sw3_wr does not limit the offsets to the table's")
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
    # --- the 0120 preconditions inside swl_wpre
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


def build_and_run(hdr, chip, work):
    d = tempfile.mkdtemp(dir=work)
    for name, text in (("sweet_wlan_chip.h", chip), ("sweet_wlan_wr.h", hdr)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    exe = os.path.join(d, "t")
    cc = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-I" + d, "-o", exe, TEST], capture_output=True, text=True, timeout=120)
    if cc.returncode != 0:
        return "compile", cc.stderr[-400:]
    r = subprocess.run([exe], capture_output=True, text=True, timeout=120)
    return ("pass" if r.returncode == 0 else "fail"), r.stderr[-200:]


def run_all():
    hdr = new_file(PATCH0125, "bsd/dev/sweet_wlan_wr.h")
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


def glue_added_lines():
    """The lines patch 0125 adds to bsd/dev/sweet_wlan.c."""
    out, on = [], False
    with open(PATCH0125, encoding="utf-8") as fh:
        for line in fh.read().split("\n"):
            if line == "+++ b/bsd/dev/sweet_wlan.c":
                on = True
            elif on and line.startswith("diff "):
                break
            elif on and line.startswith("+") and not line.startswith("+++"):
                out.append(line[1:])
    if not out:
        raise ValueError("patch 0125 adds nothing to bsd/dev/sweet_wlan.c")
    return "\n".join(out) + "\n"


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
        self.assertEqual(audit_store_path(new_file(PATCH0125, "bsd/dev/sweet_wlan_wr.h"), glue_added_lines()), [])

    def test_store_path_audit_fails_on_a_hostile_corpus(self):
        hdr = new_file(PATCH0125, "bsd/dev/sweet_wlan_wr.h")
        glue = glue_added_lines()
        bad = [
            (hdr + "\nstatic inline void x(void) { __asm__ volatile(\"dsb sy\"); }\n", glue, "inline asm"),
            (hdr + "\nstatic inline void x(void) { *(volatile uint32_t *)0x14B00404 = 0; }\n", glue, "pointer from an integer literal"),
            (hdr + "\nstatic inline void x(void) { volatile uint32_t *p = (volatile uint32_t *)0x14B00404; (void)p; }\n", glue, "pointer from an integer literal"),
            (hdr.replace("return x->b->wr(x->b->ctx, space, win, off, v);", "return 0;") + "\nstatic inline int\nswl_z(struct swl_wx *x)\n{\n\treturn x->b->wr(x->b->ctx, 1, 0, 0, 0);\n}\n", glue, "bus store callback called outside swl_store"),
            (hdr + "\nstatic inline int\nswl_z(struct swl_wx *x)\n{\n\treturn swl_store(x, 1, 0, 0, 0);\n}\n", glue, "swl_store called from swl_z"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\t*p = v;"), "a store through a pointer outside sw3_wr"),
            (hdr, glue + "\nstatic void sw3_z(volatile uint32_t *p, uint32_t v) { *p = v; }\n", "a store through a pointer outside sw3_wr"),
            (hdr, glue + "\nstatic void sw3_y(void) { sw3_wr(0, 0, 0, 0, 0); }\n", "sw3_wr used other than as the bus wr callback"),
            (hdr, glue.replace("*p = v;", "*p = v;\n\th->cfg[1] = 6;"), "a store through a pointer outside sw3_wr"),
            (hdr, glue + "\nstatic void sw3_x(struct sw_hw *h) { h->atu[0x101] = 0; }\n", "a store through a pointer outside sw3_wr"),
            (hdr, glue + "\nstatic void sw3_q(volatile uint32_t *q) { q[0] = 1; }\n", "a store through a pointer outside sw3_wr"),
            (hdr, glue.replace("(space == SWW_CFG && off != SWW_CFG_WIN) || ", ""), "sw3_wr does not limit the offsets to the table's"),
            (hdr, glue.replace("SWW_R2(0x14)", "SWW_R2(0x18)"), "sw3_wr does not limit the offsets to the table's"),
            (hdr, glue.replace("wb.wr = sw3_wr;", "wb.wr = 0;"), "sw3_wr used other than as the bus wr callback"),
        ]
        for h, g, want in bad:
            self.assertIn(want, audit_store_path(h, g), want)

    def test_extraction_rejects_a_missing_hunk(self):
        with self.assertRaises(ValueError):
            new_file(PATCH0125, "bsd/dev/does_not_exist.h")


if __name__ == "__main__":
    unittest.main()
