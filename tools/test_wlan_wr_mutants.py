"""Mutation test of the stage-3 chip writer in xnu patch 0129 (bsd/dev/sweet_wlan_wr.h, W-D3 step 2, docs/research/43 s.3).

Each guard rule of swl_wr() / swl_wr_ok() / the steps is removed or weakened in a copy of the header (every occurrence of the pattern), the host test
shim/wlan_wr_host_test.c is built against the copy and must FAIL; a mutant that still passes is a survivor and fails this test, unless it is listed
EQUIVALENT with the clause that implies it (CR-02). A pattern that matches nothing fails too (the header changed under the mutant list).
Patch 0130 (run B: W8 window, W9 last word, W10 image copy) changes both files; patch 0131 (run C: W11 NVRAM text, W12 length token, W13 rTLV) changes them again; patch 0132 (run D: W14 reset vector, firmware identity pin) once more; patch 0133 (run E: W16 the CA7 release with the interrupt gate, W17 the shared-word poll, the stop action) once more; patch 0134 (run F1: read-only dumps of the running firmware before the re-halt) once more; patch 0135 (run F2: the dumps F1 had to refuse, through BAR2 window 0 after the re-halt) a last time; the texts under test are the files
after 0124, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137 are applied in order (apply_chain). Two host tests are built against every mutant (shim/wlan_wr_host_test.c with the fake bus, shim/wlan_nv_host_test.c for the pure
process_nvram_vars port); a mutant is killed when either fails.
Run: python3 -B tools/test_wlan_wr_mutants.py  (also picked up by `unittest discover -s tools -p 'test_*.py'`)."""
import concurrent.futures
import difflib
import functools
import os
import re
import resource
import subprocess
import shutil
import signal
import sys
import tempfile
import threading
import time
import json
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import wlan_mutant_cache
import wlan_mutant_pool, wlan_keep_mutants
import cpu_timeout
import probe_failfast
from wlan_glue_rows import _gm, GLUE_ASSIGNS_L25, E_G2_L24_ROWS, E_G2L_ROWS, E_GLUE_ROWS, L25R_GLUE_ROWS, L25A_GLUE_ROWS      # T-2631 (R29): the row tables live in tools/wlan_glue_rows.py

PATCH0120 = os.path.join(ROOT, "xnu", "patches", "0124-sweet-wlan-bwfm-core.patch")
PATCH0125 = os.path.join(ROOT, "xnu", "patches", "0129-sweet-wlan-chip-writes.patch")
PATCH0128 = os.path.join(ROOT, "xnu", "patches", "0128-sweet-wlan-fwcheck.patch")
PATCH0130 = os.path.join(ROOT, "xnu", "patches", "0130-sweet-wlan-image-copy.patch")
PATCH0131 = os.path.join(ROOT, "xnu", "patches", "0131-sweet-wlan-nvram-rtlv.patch")
PATCH0132 = os.path.join(ROOT, "xnu", "patches", "0132-sweet-wlan-reset-vector.patch")
PATCH0133 = os.path.join(ROOT, "xnu", "patches", "0133-sweet-wlan-arm-release.patch")
PATCH0134 = os.path.join(ROOT, "xnu", "patches", "0134-sweet-wlan-run-f1-dumps.patch")
PATCH0135 = os.path.join(ROOT, "xnu", "patches", "0135-sweet-wlan-run-f2-low-window.patch")
PATCH0136 = os.path.join(ROOT, "xnu", "patches", "0136-sweet-wlan-run-f3-live-low-window.patch")
PATCH0137 = os.path.join(ROOT, "xnu", "patches", "0137-sweet-wlan-gap-report.patch")
PATCH0174 = os.path.join(ROOT, "xnu", "patches", "0174-sweet-wlan-g2-first-dma.patch")
G2_TEST = os.path.join(ROOT, "shim", "wlan_g2_host_test.c")
G2_SHIM_HDR = os.path.join(ROOT, "shim", "sweet_wlan_g2.h")
PATCH0167 = os.path.join(ROOT, "xnu", "patches", "0167-sweet-wlan-g1-host-dma.patch")
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
    return apply_chain(HDR_PATH, (PATCH0125, PATCH0130, PATCH0131, PATCH0132, PATCH0133, PATCH0134, PATCH0135, PATCH0136, PATCH0137, PATCH0174))


def chip_header():
    """sweet_wlan_chip.h as the kernel gets it: the 0124 new file, then 0174's hunk (swl_pre_ep want argument), same chain as shim/build.sh."""
    return apply_chain("bsd/dev/sweet_wlan_chip.h", (PATCH0120, PATCH0174))


G2_SUB_HEADERS = ("sweet_wlan_g2d.h", "sweet_wlan_g2e.h", "sweet_wlan_g2r.h")      # new files of patch 0174 that sweet_wlan_g2.h includes, one each (T-4adb g2e, T-0a9d g2d, T-08fd g2r; R29)


def g2_header_raw():
    """sweet_wlan_g2.h exactly as patch 0174 adds it (a new file: no chain); it includes the G2_SUB_HEADERS."""
    return new_file(PATCH0174, "bsd/dev/sweet_wlan_g2.h")


def g2_header():
    """The G2 header with the G2_SUB_HEADERS (g2d: the DP15 transport and ring-2 helpers, g2e: the L21-L23 request sequences, g2r: the replay record; new files of patch 0174) put back in place of their #include,
    so a mutant needle and the audits see one text and the compiled result is the same."""
    raw = g2_header_raw()
    for h in G2_SUB_HEADERS:
        if raw.count('#include "%s"\n' % h) != 1:
            raise ValueError("sweet_wlan_g2.h must include %s exactly once" % h)
        raw = raw.replace('#include "%s"\n' % h, new_file(PATCH0174, "bsd/dev/" + h))
    return raw


G2_TEST_SUB_HEADERS = (("wlan_g2_host_dongle.h", os.path.join(ROOT, "shim", "wlan_g2_host_dongle.h")), ("wlan_g2_host_l24.h", os.path.join(ROOT, "shim", "wlan_g2_host_l24.h")), ("wlan_g2_host_l25wl.h", os.path.join(ROOT, "shim", "wlan_g2_host_l25wl.h")))      # (name, literal join the manifest input scan reads) of the shim/ headers wlan_g2_host_test.c includes, one each (the fake dongle, T-4adb; the scan24/L24 cases, T-0a9d; the L25 real-stream cases, T-5173, included by wlan_g2_host_l24.h so listed after it; R29)


def g2_test_text():
    """shim/wlan_g2_host_test.c with the G2_TEST_SUB_HEADERS put back in place of their #include lines."""
    with open(G2_TEST, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    for h, hp in G2_TEST_SUB_HEADERS:
        at = [i for i, l in enumerate(lines) if l.startswith('#include "%s"' % h)]
        if len(at) != 1:
            raise ValueError("wlan_g2_host_test.c must include %s exactly once" % h)
        with open(hp, encoding="utf-8") as fh:
            sub = fh.read()
        lines = ("\n".join(lines[:at[0]]) + "\n" + sub + "\n".join(lines[at[0] + 1:])).split("\n")
    return "\n".join(lines)


def glue_before():
    return apply_chain(GLUE_PATH, (PATCH0120, PATCH0128))


def glue_after():
    return apply_chain(GLUE_PATH, (PATCH0120, PATCH0128, PATCH0125, PATCH0130, PATCH0131, PATCH0132, PATCH0133, PATCH0134, PATCH0135, PATCH0136, PATCH0137, PATCH0167, PATCH0174))


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
    ("sw3_wr", "put: p = sw3_ptr(h, space, off);"),  # patch 0174: the G2 branch jumps here (goto put), so both paths share the one store
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
    ("sweet_wlan_stage3", "hw->l3 = level;"),  # patch 0132: sw3_wr allows backplane address 0 only at level 6 ...
    ("sweet_wlan_stage3", "hw->rv_ok = level >= 6 && sw_fw.p != NULL && sw_fw.len == SWW_FW_LEN && sw_fw.crc == SWW_FW_CRC;"),  # review 0132 F5: the pin, in the glue too
    ("sweet_wlan_stage3", "hw->rv_done = 0;"),
    ("sweet_wlan_stage3", "hw->rel = 0;"),  # patch 0133: the CA7 release stores made so far (sw3_ca7_ok)
    ("sw3_wr", "h->rel |= sw3_ca7_bit(h, space, win, off, v);"),  # set after the store, from the store's own (win, off, v)
    ("sw3_wr", "h->rv_done = 1;"),  # the one store at backplane address 0 is made once
    ("sweet_wlan_stage3", "hw->rv = hw->rv_ok ? ((uint32_t)sw_fw.p[0] | (uint32_t)sw_fw.p[1] << 8 | (uint32_t)sw_fw.p[2] << 16 | (uint32_t)sw_fw.p[3] << 24) : 0;"),  # ... and only this word
    ("sweet_wlan_stage3", "x.img = sw_fw.p;"),  # patch 0130: the FW entry swf_parse() accepted
    ("sweet_wlan_stage3", "x.img_len = sw_fw.len;"),
    ("sweet_wlan_stage3", "x.img_crc = sw_fw.crc;"),
    ("sweet_wlan_stage3", "x.nv_src = sw_nv.p;"),  # patch 0131: the NVRM entry swf_parse() accepted, a scratch buffer, the entropy source
    ("sweet_wlan_stage3", "x.nv_src_len = sw_nv.len;"),
    ("sweet_wlan_stage3", "x.nv_src_crc = sw_nv.crc;"),
    ("sweet_wlan_stage3", "x.nv = sw_nvbuf;"),
    ("sweet_wlan_stage3", "x.nv_cap = sizeof sw_nvbuf;"),
    ("sweet_wlan_stage3", "x.w15 = hw->w15;"),     # W-D4b D-12: sweetwlan=15 only
    ("sweet_wlan_stage3", "hw->w15 = 0;"),     # W-D4b D-12: the allowance does not outlive stage 3
    ("sweet_wlan_stage3", "wb.rng = sw3_rng;"),
    ("sw_fw_note", "for (i = 0;"),
    ("sw_fw_note", 'i++) { if (memcmp(e[i].tag, "", 4) == 0) { sw_fw.p = base + e[i].offset;'),  # string contents are stripped by the scan; audit_store_path pins "FW  "
    ("sw_fw_note", "sw_fw.p = base + e[i].offset;"),
    ("sw_fw_note", "sw_fw.len = e[i].length;"),
    ("sw_fw_note", "sw_fw.crc = e[i].crc;"),
    ("sw_fw_note", "sw_nv.p = base + e[i].offset;"),
    ("sw_fw_note", "sw_nv.len = e[i].length;"),
    ("sw_fw_note", "sw_nv.crc = e[i].crc;"),
    ("sweet_wlan_init", "level = 5;"),  # review 0132 F2: W14 not run where DHD would read the DAR register
    ("sweet_wlan_init", "level = 6;"),  # patch 0133: run E not run where the mailbox registers are not at 0xC30/0xC34 or W14's test is not DHD's
    ("sweet_wlan_init", "hw.level = level > 2 ? 2 : (int)level;"),  # fragments of the function: its header is a context line of the patch
    ("sweet_wlan_init", "hw.atu = atu;"),
    ("sweet_wlan_init", "hw.elbi = elbi;"),
    ("sweet_wlan_init", "hw.iso = &isop[(SW_ISO & 0xFFFull) / 4];"),
    ("sweet_wlan_init", "hw.tcm = NULL;"),
    # patch 0167 (sweet_wlan_g1, whole: its _Static_assert line no longer hides it) and patch 0174 (run G2: the DBI map, the G2 list, the adapter, the hook), pinned like the rest
    ('sweet_wlan_init', 'bad = swl_pre_ep(hw.cfg[0x04 / 4], hw.cfg[0x10 / 4], hw.cfg[0x14 / 4], 0x2u);'),
    ('sweet_wlan_init', 'hw.dbi = sw_map(SW_DBI, 0x1000);'),
    ('sweet_wlan_init', 'hw.g2ok = hw.dbi != NULL && sweet_wlan_g1(&hw.g2plan, &hw.g2pa, &hw.g2va) == 0;'),
    ('sweet_wlan_init', 'hw.w15 = hw.g2ok && level >= 15;'),     # W-D4b D-12
    ('sw3_g2_ok', 'volatile uint32_t *p = sw3_ptr(h, space, off);'),
    ('sw3_g2_ok', ', a1[7] = { 0 };'),  # the scanner's split after `a0[7] = { 0 }`
    ('sw_g2_alias_ok', 'uint64_t lo = pa & 0xFFFFFFFFull;'),
    ('sweet_wlan_init', 'hw.g2ok = 0;'),
    ('sweet_wlan_init', 'hw.g2ok = 0;'),      # W-D4b D-10: the second clear, in the g2va == 0 refusal (no device access)
    ('sweet_wlan_init', 'hw.g2ok = 0;'),      # W-D4c: the third clear, in the D-cache line > SWD_PAD refusal (no device access)
    ('sw3_g2_ok', 'int bad = 0, have = 0;'),     # review 0174-w-d4b-hwwrite-r1 F6: `have` = the old value was read (only for an entry that needs it)
    ('sw3_g2_ok', 'bad |= sw3_rd(h, SWW_ISO, 0, 0, &iso) != 0 || sw3_rd(h, SWW_ELBI, 0, 0x2C8u, &ltssm) != 0;'),
    ('sw3_g2_ok', 'for (i = 0;'),
    ('sw3_g2_ok', 'i++) { bad |= sw3_rd(h, SWW_ATU, 0, 4u * i, &a0[i]) != 0 || sw3_rd(h, SWW_ATU, 0, 0x200u + 4u * i, &a1[i]) != 0;'),
    ('sw3_g2_ok', 'bad |= sw3_rd(h, SWW_ATU, 0, 4u * i, &a0[i]) != 0 || sw3_rd(h, SWW_ATU, 0, 0x200u + 4u * i, &a1[i]) != 0;'),
    ('sw3_g2_ok', 'old = *p;'),  # a READ through the store's own pointer (the old value under omask), not a store
    ('sw3_g2_ok', 'for (i = 0;'),
    ('sw3_g2_ok', 'i++) { e = &h->g2l[i];'),
    ('sw3_g2_ok', 'e = &h->g2l[i];'),
    ('sw3_wr', 'int g2i = -1;'),
    ('sw3_wr', 'g2i = sw3_g2_ok(h, space, win, off, v, 0);'),
    ('sw3_wr', 'h->c88 = v != SWW_CFG88;'),     # W-D4b D-12: set by the W15 store (1: the restore allowance), cleared by the restore store; no device access
    ('sw3_wr', 'h->w15 = 0;'),     # W15 is once
    ('sw3_g2_ok', 'vv = w16 ? (old & 0xFFFF0000u) | (v & 0xFFFFu) : v;'),     # W-D4b: the word a 16-bit store leaves, for the list match
    ('sw3_g2_ok', 'have = 1;'),
    ('sw3_wr16', 'struct sw_hw *h = ctx;'),
    ('sw3_wr16', 'g2i = sw3_g2_ok(h, space, win, off, v, 1);'),
    ('sw3_wr16', 'p = sw3_ptr(h, space, off);'),
    ('sw3_wr16', '*(volatile uint16_t *)p = v;'),       # W-D4b (sweetwlan=14): THE 16-bit store, only through a w16 entry of g2l[]
    ('sw3_wr16', 'h->g2l[g2i].done = 1;'),
    ('sw3_wr', 'h->g2l[g2i].done = 1;'),
    ('sweet_wlan_stage3', 'x.g2 = hw->g2ok ? sw_g2_hook : NULL;'),
    ('sweet_wlan_g1', 'vm_offset_t va = 0;'),
    ('sweet_wlan_g1', '*va_out = va;'),      # W-D4b D-10: an output pointer of the G1 mapping, no device access
    ('sweet_wlan_g1', 'rc = swd_plan_make(SWD_RW_INDEX_SZ, SWD_MAX_SUB, SWD_MAX_CPL, &plan);'),
    ('sweet_wlan_g1', 'sz = round_page(plan.total);'),
    ('sweet_wlan_g1', 'pn = pmap_find_phys(kernel_pmap, (addr64_t)va);'),
    ('sweet_wlan_g1', 'pl = pmap_find_phys(kernel_pmap, (addr64_t)(va + sz - PAGE_SIZE));'),
    ('sweet_wlan_g1', 'pa = ptoa_64(pn);'),
    ('sweet_wlan_g1', 'rc = swd_audit_all(pa, (uint32_t)sz, &plan);'),
    ('sweet_wlan_g1', 'for (i = 0;'),
    ('sweet_wlan_g1', 'i++) { printf("", plan.b[i].name, (unsigned long long)(pa + plan.b[i].off), plan.b[i].len);'),
    ('sweet_wlan_g1', '*out = plan;'),
    ('sweet_wlan_g1', '*pa_out = pa;'),
    ('sw_g2_add', 'e = &h->g2l[h->g2n];'),
    ('sw_g2_add', 'e->space = space;'),
    ('sw_g2_add', 'e->win = win;'),
    ('sw_g2_add', 'e->off = off;'),
    ('sw_g2_add', 'e->v = v;'),
    ('sw_g2_add', 'e->vmask = vmask;'),
    ('sw_g2_add', 'e->old = old;'),
    ('sw_g2_add', 'e->omask = omask;'),
    ('sw_g2_add', 'e->once = once ? 1 : 0;'),
    ('sw_g2_add', 'e->done = 0;'),
    ('sw_g2_add', 'e->w16 = 0;'),
    ('sw_g2_tcm16', 'int r = sw_g2_tcm(h, end, bp, v, 0x0000FFFFu, 0, 0);'),
    ('sw_g2_tcm16', 'h->g2l[r].w16 = 1;'),
    ('sw_g2_wr16', 'struct sw_hw *h = ctx;'),
    ('sw_g2_wr16', 'uint32_t win = a >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;'),
    ('sw_g2_add', 'e->need = need;'),
    ('sw_g2_add', 'h->g2n++;'),
    ('sw_g2_tcm', 'uint32_t win = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;'),
    ('sw_g2_list', 'h->g2n = 0;'),
    ('sw_g2_list', 'for (k = 0;'),
    ('sw_g2_list', 'k++) { ra[k] = 0;'),
    ('sw_g2_list', 'ra[k] = 0;'),
    ('sw_g2_list', 'for (k = 0;'),
    ('sw_g2_list', 'for (i = 0;'),
    ('sw_g2_list', 'bad |= sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN, SWW_WIN_PCIE, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN, w80, 0xFFFFFFFFu, 0, 0, 0, -1) < 0;'),
    ('sw_g2_list', 'bad |= sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL, 0xFFFFFFFFu, 0, 0, 0, -1) < 0;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, sh + 0x50u, SW_G2_FLAGS2, 0xFFFFFFFFu, SW_G2_FLAGS2_OLD, 0xFFFFFFFFu) < 0 || sw_g2_tcm(h, end, sh + 0x54u, SW_G2_HOSTCAP, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'pa = h->g2pa + h->g2plan.b[SWD_TRAP].off;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, sh + 0x58u, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0 || sw_g2_tcm(h, end, sh + 0x5Cu, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'for (k = 0;'),
    ('sw_g2_list', 'k++) { pa = h->g2pa + h->g2plan.b[SWD_IDX_H2D_W + k].off;'),
    ('sw_g2_list', 'pa = h->g2pa + h->g2plan.b[SWD_IDX_H2D_W + k].off;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, ri + 0x14u + 8u * k, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0 || sw_g2_tcm(h, end, ri + 0x18u + 8u * k, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'for (i = 0;'),
    ('sw_g2_list', 'i++) { pa = h->g2pa + h->g2plan.b[SWD_H2D_CTRL + i].off;'),
    ('sw_g2_list', 'pa = h->g2pa + h->g2plan.b[SWD_H2D_CTRL + i].off;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, rm + 16u * i + 0x08u, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0 || sw_g2_tcm(h, end, rm + 16u * i + 0x0Cu, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, rm + 16u * i + 0x04u, (swd_rings[SWD_H2D_CTRL + i].items & 0xFFFFu) | swd_rings[SWD_H2D_CTRL + i].isz << 16, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'for (k = 0;'),
    ('sw_g2_list', 'k++) { for (i = 0;'),
    ('sw_g2_list', 'for (i = 0;'),
    ('sw_g2_list', 'i++) { bad |= (h->d4b >= 2 ? sw_g2_tcm16(h, end, ra[k] + 4u * i, 0u) : sw_g2_tcm(h, end, ra[k] + 4u * i, 0u, 0x0000FFFFu, 0, 0)) < 0;'),
    ('sw_g2_list', 'bad |= (h->d4b >= 2 ? sw_g2_tcm16(h, end, ra[k] + 4u * i, 0u) : sw_g2_tcm(h, end, ra[k] + 4u * i, 0u, 0x0000FFFFu, 0, 0)) < 0;'),
    ('sw_g2_list', 'pa = h->g2pa + h->g2plan.b[SWD_SCRATCH].off;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, sh + 0x38u, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0 || sw_g2_tcm(h, end, sh + 0x3Cu, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'bad |= sw_g2_tcm(h, end, sh + 0x34u, SW_G2_SCRLEN, 0xFFFFFFFFu, 0, 0xFFFFFFFFu) < 0;'),
    ('sw_g2_list', 'rc = sw_g2_add(h, SW3_DBI, 0, 0x04u, SW_G2_RC, 0xFFFFFFFFu, 0, 0xFFFFu, 1, -1);'),
    ('sw_g2_list', 'bad |= rc < 0 || sw_g2_add(h, SW3_DBI, 0, 0x04u, rcold & 0xFFFFu, 0xFFFFFFFFu, 0, 0, 1, rc) < 0;'),
    ('sw_g2_list', 'ep = sw_g2_add(h, SWW_CFG, 0, 0x04u, SW_G2_EP_DMA, 0xFFFFFFFFu, SW_G2_EP_MEM, 0xFFFFu, 1, -1);'),
    ('sw_g2_list', 'bad |= ep < 0 || sw_g2_add(h, SWW_CFG, 0, 0x04u, SW_G2_EP_MEM, 0xFFFFFFFFu, 0, 0, 1, ep) < 0;'),
    ('sw_g2_list', 'ready = sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x144u, SW_G2_READY, 0xFFFFFFFFu, 0, 0, 1, -1);'),     # review r1 F7: kept, the d4 entries chain on it
    ('sw_g2_list', 'bad |= ready < 0;'),
    ('sw_g2_list', 'h->g2n = 0;'),
    ('sw_g2_rd', 'struct sw_hw *h = ctx;'),
    ('sw_g2_rd', 'uint32_t win = a >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;'),
    ('sw_g2_wr', 'struct sw_hw *h = ctx;'),
    ('sw_g2_wr', 'uint32_t win = a >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;'),
    ('sw_g2_wr', 'int rc = -1;'),
    ('sw_g2_wr', 'case SWG_SP_EP: rc = sw3_wr(h, SWW_CFG, 0, a, v);'),
    ('sw_g2_wr', 'h->g2ep = h->cfg[0x04 / 4] & 0xFFFFu;'),
    ('sw_g2_hook', 'struct sw_hw *h = x->b->ctx;'),
    ('sw_g2_hook', 'uint32_t w80 = h->cfg[SWW_CFG_WIN / 4];'),
    ('sw_g2_hook', 'gb.rd = sw_g2_rd;'),
    ('sw_g2_hook', 'gb.wr = sw_g2_wr;'),
    ('sw_g2_hook', 'gb.wr16 = sw_g2_wr16;'),
    ('sw_g2_hook', 'gb.delay_ms = sw_g2_delay;'),
    ('sw_g2_hook', 'gb.ctx = h;'),
    ('sw_g2_hook', 'g.b = &gb;'),
    ('sw_g2_hook', 'g.in.sh = sh;'),
    ('sw_g2_hook', 'g.in.end = SWW_RAM_BASE + x->ram_bytes;'),
    ('sw_g2_hook', 'for (i = 0;'),
    ('sw_g2_hook', 'i++) { g.in.idx[i] = h->g2pa + h->g2plan.b[SWD_IDX_H2D_W + i].off;'),
    ('sw_g2_hook', 'g.in.idx[i] = h->g2pa + h->g2plan.b[SWD_IDX_H2D_W + i].off;'),
    ('sw_g2_hook', 'for (i = 0;'),
    ('sw_g2_hook', 'i++) { g.in.ring[i] = h->g2pa + h->g2plan.b[SWD_H2D_CTRL + i].off;'),
    ('sw_g2_hook', 'g.in.ring[i] = h->g2pa + h->g2plan.b[SWD_H2D_CTRL + i].off;'),
    ('sw_g2_hook', 'g.in.trap = h->g2pa + h->g2plan.b[SWD_TRAP].off;'),
    ('sw_g2_hook', 'g.in.scratch = h->g2pa + h->g2plan.b[SWD_SCRATCH].off;'),
    ('sw_g2_hook', 'h->g2ep = SW_G2_EP_MEM;'),
    ('sw_g2_hook', 'h->g2 = 1;'),
    ('sw_g2_hook', 'rc = swg_go(&g);'),
    ('sw_g2_hook', 'rc = rc ? rc : -1;'),
    ('sw_g2_hook', 'h->g2 = 0;'),
    ('sw_g2_hook', 'int rc, refused = 0;'),
    ('sw_g2_hook', 'rc = 1;'),
    ('sw_g2_hook', 'refused = 1;'),
    ('sw_g2_list', ', rcold = 0xFFFFFFFFu;'),
    ('sw_g2_list', 'const char *why = NULL;'),
    ('sw_g2_list', 'i++) { if (!(ra[k] + 16u <= ra[i] || ra[i] + 16u <= ra[k])) { why = "";'),
    ('sw_g2_list', 'if ((r = sw_g2_rdw(h, end, ri + 0x04u + 4u * k, &ra[k])) != 0) { why = r == -2 ? "" : "";'),
    ('sw_g2_list', 'if (why == NULL && (r = sw_g2_rdw(h, end, ri, &rm)) != 0) { why = r == -2 ? "" : "";'),
    ('sw_g2_list', 'if (why == NULL && (r = sw_g2_rdw(h, end, sh + 0x30u, &ri)) != 0) { why = r == -2 ? "" : "";'),
    ('sw_g2_list', 'int bad = 0, ep, rc, r, ready;'),
    ('sw_g2_list', 'k++) { if (!(ra[k] + 16u <= sh || sh + 0x60u <= ra[k]) || !(ra[k] + 16u <= ri || ri + 0x4Cu <= ra[k]) || !(ra[k] + 16u <= rm || rm + 0x50u <= ra[k])) { why = "";'),
    ('sw_g2_list', 'uint32_t ri = 0, rm = 0, ra[4] = { 0 }, rcold = 0xFFFFFFFFu;'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = "";'),
    ('sw_g2_list', 'why = r == -2 ? "" : "";'),
    ('sw_g2_list', 'why = r == -2 ? "" : "";'),
    ('sw_g2_list', 'why = r == -2 ? "" : "";'),
    ('sw_g2_list', 'why = why != NULL ? why : "";'),
    ('sw_g2_rdw', 'uint32_t win = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;'),
    # W-D4b (sweetwlan=13): sw_g2_d4 builds the extra list entries; the hook hands G2 the block geometry; the host-block store case of the bus; the gate
    ('sw_g2_d4', 'uint64_t pa = h->g2pa + h->g2plan.b[SWD_EVBUF].off;'),
    ('sw_g2_d4', 'm = sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, ready);'),
    ('sw_g2_d4', 'bad = m < 0;'),
    ('sw_g2_d4', 'last = m;'),
    ('sw_g2_d4', 'for (k = 0;'),
    ('sw_g2_d4', 'k++) { last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + 4u * k, k == 0u ? SW_G2_D4_W0 : k == 1u ? 1u : k == 2u ? SW_G2_D4_LEN : k == 4u ? (uint32_t)pa : k == 5u ? (uint32_t)(pa >> 32) : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, k == 0u ? m : last);'),
    ('sw_g2_d4', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + 4u * k, k == 0u ? SW_G2_D4_W0 : k == 1u ? 1u : k == 2u ? SW_G2_D4_LEN : k == 4u ? (uint32_t)pa : k == 5u ? (uint32_t)(pa >> 32) : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, k == 0u ? m : last);'),
    ('sw_g2_d4', 'bad |= last < 0;'),
    ('sw_g2_d4', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_H2D_W].off, 1u, 0x0000FFFFu, 0, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4', 'bad |= last < 0;'),
    ('sw_g2_d4', 'last = sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x140u, SW_G2_D4_DB0, 0xFFFFFFFFu, 0, 0, 1, last);'),
    ('sw_g2_d4', 'bad |= last < 0 || sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0, 0xFFFFFFFFu, 0, 0, 1, m) < 0;'),
    # W-D4c (sweetwlan=16): the 40 extra list entries are built by sw_g2_d4c_post (ten item words, H2D-WR slot, doorbell) and sw_g2_d4c_rd (D2H-RD slot, doorbell), called five times from sw_g2_d4 in the store order A1, C1, W, D, C2
    ('sw_g2_d4', 'last = sw_g2_d4c_post(h, 1u, SW_G2_D4C_W0_ACK, SW_G2_D4C_MB_ACK, 2u, SW_G2_D4C_DB0_ACK, last);'),
    ('sw_g2_d4', 'last = last < 0 ? -1 : sw_g2_d4c_rd(h, 1u, SW_G2_D4C_DB0_RD1, last);'),
    ('sw_g2_d4', 'last = last < 0 ? -1 : sw_g2_d4c_post(h, 2u, SW_G2_D4C_W0_WAKE, SW_G2_D4C_MB_WAKE, 3u, SW_G2_D4C_DB0_WAKE, last);'),
    ('sw_g2_d4', 'last = last < 0 ? -1 : sw_g2_d4c_post(h, 3u, SW_G2_D4C_W0_DEAS, SW_G2_D4C_MB_DEAS, 4u, SW_G2_D4C_DB0_DEAS, last);'),
    ('sw_g2_d4', 'last = last < 0 ? -1 : sw_g2_d4c_rd(h, 2u, SW_G2_D4C_DB0_RD2, last);'),
    ('sw_g2_d4', 'bad |= last < 0;'),
    ('sw_g2_d4c_post', 'for (k = 0;'),
    ('sw_g2_d4c_post', 'k++) { last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + itm * SW_G2_D4C_ISZ + 4u * k, k == 0u ? w0 : k == 2u ? mb : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4c_post', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + itm * SW_G2_D4C_ISZ + 4u * k, k == 0u ? w0 : k == 2u ? mb : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4c_post', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_H2D_W].off, idx, 0x0000FFFFu, idx - 1u, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4c_rd', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_D2H_R].off, idx, 0x0000FFFFu, idx - 1u, 0xFFFFFFFFu, 1, last);'),
    # W-D4d (sweetwlan=17..20): the 65 list entries are built by sw_g2_d4d (levels 17..20) from sw_g2_d4d_post / sw_g2_d4d_rd with the words of sw_g2_d4d_resp / sw_g2_d4d_req; the hook hands G2 the two buffer PAs
    ('sw_g2_d4d', 'last = last < 0 ? -1 : sw_g2_d4d_post(h, 7u, w, last);'),
    ('sw_g2_d4d', 'last = last < 0 ? -1 : sw_g2_d4d_post(h, 8u, w, last);'),
    ('sw_g2_d4d', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IOCTBUF].off, SW_G2_D4D_CAP_W0, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4d', 'last = sw_g2_d4d_post(h, 4u, w, last);'),
    ('sw_g2_d4d', 'last = sw_g2_d4d_post(h, 5u, w, last);'),
    ('sw_g2_d4d', 'uint64_t rpa = h->g2pa + h->g2plan.b[SWD_RESPBUF].off, ipa = h->g2pa + h->g2plan.b[SWD_IOCTBUF].off;'),
    ('sw_g2_d4d_post', 'for (k = 0;'),
    ('sw_g2_d4d_post', 'k++) { last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + idx * SW_G2_D4D_ISZ + 4u * k, w[k], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4d_post', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + idx * SW_G2_D4D_ISZ + 4u * k, w[k], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4d_post', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_H2D_W].off, idx + 1u, 0x0000FFFFu, idx, 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_d4d_req', 'for (k = 0;'),
    ('sw_g2_d4d_req', 'k++) { w[k] = 0;'),
    ('sw_g2_d4d_req', 'w[0] = SW_G2_D4D_W0_REQ | SW_G2_D4D_EP(idx);'),
    ('sw_g2_d4d_req', 'w[1] = SW_G2_D4D_REQID;'),
    ('sw_g2_d4d_req', 'w[2] = cmd;'),
    ('sw_g2_d4d_req', 'w[3] = trans | len << 16;'),
    ('sw_g2_d4d_req', 'w[4] = len;'),
    ('sw_g2_d4d_req', 'w[6] = (uint32_t)pa;'),
    ('sw_g2_d4d_req', 'w[7] = (uint32_t)(pa >> 32);'),
    ('sw_g2_d4d_req', 'w[k] = 0;'),
    ('sw_g2_d4d_resp', 'for (k = 0;'),
    ('sw_g2_d4d_resp', 'k++) { w[k] = 0;'),
    ('sw_g2_d4d_resp', 'w[0] = SW_G2_D4D_W0_RESP | SW_G2_D4D_EP(idx);'),
    ('sw_g2_d4d_resp', 'w[1] = id;'),
    ('sw_g2_d4d_resp', 'w[2] = SW_G2_D4D_BUFLEN;'),
    ('sw_g2_d4d_resp', 'w[4] = (uint32_t)pa;'),
    ('sw_g2_d4d_resp', 'w[5] = (uint32_t)(pa >> 32);'),
    ('sw_g2_d4d_resp', 'w[k] = 0;'),
    ('sw_g2_hook', 'g.in.ioctbuf = h->g2pa + h->g2plan.b[SWD_IOCTBUF].off;'),
    ('sw_g2_hook', 'g.in.respbuf = h->g2pa + h->g2plan.b[SWD_RESPBUF].off;'),
    ('sweet_wlan_init', 'unsigned dline = 4u << ((__builtin_arm_rsr64("") >> 16) & 0xFu);'),
    ('sw_g2_list', 'bad |= sw_g2_d4(h, ready) < 0;'),
    ('sw_g2_wr', 'rc = sw3_wr(h, SW3_HOST, 0, a, v);'),
    ('sw_g2_hook', 'g.in.blk = h->g2pa;'),
    ('sw_g2_hook', 'g.in.blk_len = h->g2plan.total;'),
    ('sw_g2_hook', 'g.in.evbuf = h->g2pa + h->g2plan.b[SWD_EVBUF].off;'),
    ('sw_g2_hook', 'g.d4b = h->d4b;'),
    ('sweet_wlan_init', 'hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14 ? 2 : level >= 13 ? 1 : 0;'),
    ('sweet_wlan_init', 'swl_miss_defer = 1;'),
    # review 0174-w-d4b-hwwrite-r1 F9 (05ee33d): the RC irq enable reads start at all-ones so an unread enable counts as set (local values, no store)
    ('sw3_g2_irq_off', 'uint32_t en0 = 0xFFFFFFFFu, en1 = 0xFFFFFFFFu, en2 = 0xFFFFFFFFu;'),
    # review 0174-w-d4e-scan24-hwwrite-r1 / 22b: list-building and expectation helpers of the DS rounds (host-memory and stack assignments only: the G2 list entries, g2sh, local pointers and words; none is a device store; the one store stays `*p = v;` in sw3_wr)
    ('sw3_g2_ok', 'uint32_t old = 0, vv, ev, eold, iso = 0, ltssm = 0, a0[7] = { 0 }, a1[7] = { 0 };'),
    ('sw3_g2_ok', 'volatile uint32_t *sl = sw3_ptr(h, SW3_HOST, h->g2plan.b[e->kind == SW_G2K_R_WDB ? SWD_IDX_H2D_W : SWD_IDX_D2H_R].off);'),
    ('sw3_wr', 'h->g2sh++;'),
    ('sw_g2_add', 'e->kind = 0;'),
    ('sw_g2_d4', 'bad |= sw_g2_ds(h, last) < 0;'),
    ('sw_g2_d4d', 'last = sw_g2_d4d_post(h, 6u, w, last);'),
    ('sw_g2_d4d_post', 'h->g2l[last].kind = SW_G2K_SH_DB;'),
    ('sw_g2_d4d_post', 'h->g2l[last].kind = SW_G2K_SH_WR;'),
    ('sw_g2_d4d_post', 'h->g2l[last].kind = k == 0u ? SW_G2K_SH_ITEM0 : SW_G2K_SH_ITEM;'),
    ('sw_g2_d4d_post', 'last = sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x140u, SW_G2_D4D_DB_WR | (idx + 1u), 0xFFFFFFFFu, 0, 0, 1, last);'),
    ('sw_g2_ds', 'for (k = 0;'),
    ('sw_g2_ds', 'for (r = 0;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].kind = SW_G2K_R_ITEM;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].kind = SW_G2K_R_RD;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].kind = SW_G2K_R_RDB;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].kind = SW_G2K_R_WDB;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].kind = SW_G2K_R_WR;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].kind = k == 0u ? SW_G2K_R_ITEM0 : SW_G2K_R_ITEM;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].rep = SW_G2_R0_ITEMS - 1u;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].rep = SW_G2_R0_ITEMS - 1u;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].stride = SW_G2_D4D_ISZ;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].stride = SW_G2_D4D_ISZ;'),
    ('sw_g2_ds', 'h->g2l[h->g2n - 1u].vrng = SW_G2_R2_ITEMS - 1u;'),
    ('sw_g2_ds', 'k++) { if (k == 2u) { for (r = 0;'),
    ('sw_g2_ds', 'r++) { if (sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + 4u * k, mb[r], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need) < 0) { return -1;'),
    ('sw_g2_ds', 'static const uint32_t mb[3] = { 0x2u, 0x40u, 0x20u };'),
    ('sw_g2_exp', '*eold = e->old;'),
    ('sw_g2_exp', '*ev = (e->v & 0x00FFFFFFu) | SW_G2_D4D_EP(i);'),
    ('sw_g2_exp', '*ev = e->v;'),
    ('sw_g2_exp', 'eo = off;'),
    ('sw_g2_exp', 'i = (off - eo) / e->stride;'),
    ('sw_g2_exp', 'sl = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_IDX_H2D_W].off);'),
    ('sw_g2_item_ok', 'for (k = 0;'),
    ('sw_g2_item_ok', 'for (k = 1;'),
    ('sw_g2_item_ok', 'k++) { if (k != 2u && w[k] != 0u) { return 0;'),
    ('sw_g2_item_ok', 'k++) { q = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_H2D_CTRL].off + 40u * j + 4u * k);'),
    ('sw_g2_item_ok', 'q = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_H2D_CTRL].off + 40u * j + 4u * k);'),
    ('sw_g2_item_ok', 'w[k] = *q;'),
    # W-D4e scan24 L21-L23 (review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 6 + the list builders sw_g2_d4e / sw_g2_e_pay / sw_g2_d4e_ev): statements of the list builders only, on locals, the arg / b[] scratch arrays and entries of h->g2l[]; no bus access, no store to a device or host-block address
    ('sw_g2_d4e_ev', 'w[0] = SW_G2_E_W0_EV | SW_G2_D4D_EP(idx);'),
    ('sw_g2_e_pay', 'uint32_t i, nl = 0, v;'),
    ('sw_g2_e_pay', 'for (i = 0;'),
    ('sw_g2_e_pay', 'for (i = 0;'),
    ('sw_g2_e_pay', 'for (i = 0;'),
    ('sw_g2_e_pay', 'i++) { b[i] = 0;'),
    ('sw_g2_e_pay', 'b[i] = 0;'),
    ('sw_g2_e_pay', 'b[nl] = (uint8_t)name[nl];'),
    ('sw_g2_e_pay', 'nl++;'),
    ('sw_g2_e_pay', 'i++) { b[nl + 1u + i] = arg[i];'),
    ('sw_g2_e_pay', 'b[nl + 1u + i] = arg[i];'),
    ('sw_g2_e_pay', 'i++) { v = (uint32_t)b[4u * i] | ((uint32_t)b[4u * i + 1u] << 8) | ((uint32_t)b[4u * i + 2u] << 16) | ((uint32_t)b[4u * i + 3u] << 24);'),
    ('sw_g2_e_pay', 'v = (uint32_t)b[4u * i] | ((uint32_t)b[4u * i + 1u] << 8) | ((uint32_t)b[4u * i + 2u] << 16) | ((uint32_t)b[4u * i + 3u] << 24);'),
    ('sw_g2_e_pay', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IOCTBUF].off + 4u * i, v, 0xFFFFFFFFu, sh[i], 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_e_pay', 'h->g2l[last].kind = SW_G2K_VER;'),
    ('sw_g2_e_pay', 'sh[i] = v;'),
    ('sw_g2_d4e', 'h->g2l[last - 7].v = (uint32_t)rpa;'),     # T-af77: list-building (host memory), the SH_RBUF entry's table value = the slot-0 response buffer address
    ('sw_g2_d4e', 'h->g2l[last - 7].stride = SW_G2_E_BUFSZ;'),     # T-f1e2: list-building (host memory), event re-post SH_RBUF entry
    ('sw_g2_d4e', 'h->g2l[last - 7].vrng = 7u * SW_G2_E_BUFSZ;'),     # T-f1e2: list-building (host memory), event re-post SH_RBUF entry
    ('sw_g2_d4e', 'h->g2l[last - 7].kind = SW_G2K_SH_RBUF;'),     # T-f1e2: list-building (host memory), event re-post SH_RBUF entry
    ('sw_g2_d4e', 'uint64_t rpa = h->g2pa + h->g2plan.b[SWD_RESPBUF].off, epa = h->g2pa + h->g2plan.b[SWD_EVBUF].off, ipa = h->g2pa + h->g2plan.b[SWD_IOCTBUF].off;'),
    ('sw_g2_d4e', 'rq[5] = { { "", SW_G2_E_GET, 9u, 6u }, { "", SW_G2_E_GET, 8u, 4u }, { "", SW_G2_E_GET, 15u, 30u }, { "", SW_G2_E_SET, 15u, 30u }, { NULL, SW_G2_E_UP, 0u, 0u }, };'),
    ('sw_g2_d4e', 'for (j = 1;'),
    ('sw_g2_d4e', 'for (j = 1;'),
    ('sw_g2_d4e', 'j++) { sw_g2_d4d_resp(w, 4u + j, rpa + (uint64_t)j * SW_G2_E_BUFSZ, ix);'),
    ('sw_g2_d4e', 'last = sw_g2_d4d_post(h, ix++, w, last);'),
    ('sw_g2_d4e', 'last = sw_g2_d4d_post(h, ix++, w, last);'),
    ('sw_g2_d4e', 'last = sw_g2_d4d_post(h, ix++, w, last);'),
    ('sw_g2_d4e', 'last = sw_g2_d4d_post(h, ix++, w, last);'),
    ('sw_g2_d4e', 'last = sw_g2_d4d_post(h, ix++, w, last);'),
    ('sw_g2_d4e', 'j++) { sw_g2_d4e_ev(w, SW_G2_E_EVID + j, epa + (uint64_t)j * SW_G2_E_BUFSZ, ix);'),
    ('sw_g2_d4e', 'for (j = 0;'),
    ('sw_g2_d4e', 'arg[0] = 1u;'),
    ('sw_g2_d4e', 'arg[2] = 26u;'),
    ('sw_g2_d4e', 'arg[1] = 3u;'),
    ('sw_g2_d4e', 'arg[4u + 69u / 8u] = (uint8_t)(1u << (69u % 8u));'),
    ('sw_g2_d4e', 'last = sw_g2_e_pay(h, sh, rq[j].name, j >= 2u ? arg : NULL, rq[j].nl + rq[j].argn, 0u, last);'),
    ('sw_g2_d4e', 'arg[0] = 2u;'),
    ('sw_g2_d4e', 'arg[4] = 1u;'),
    ('sw_g2_d4e', 'arg[6] = 0x34u;'),
    ('sw_g2_d4e', 'arg[7] = 0x12u;'),
    ('sw_g2_d4e', 'arg[8] = 2u;'),
    ('sw_g2_d4e', 'arg[10] = 72u;'),
    ('sw_g2_d4e', 'arg[54] = 2u;'),
    ('sw_g2_d4e', 'arg[56] = 1u;'),
    ('sw_g2_d4e', 'last = sw_g2_e_pay(h, sh, "", arg, 6u + escn, (1u << 1) | (1u << 3), last);'),
    ('sw_g2_d4e', 'arg[76] = 7u;'),     # T-0fdd: list-building (stack array arg, then the payload words): channel_num of the 7-channel escan
    ('sw_g2_d4e', 'arg[80u + 2u * j] = (uint8_t)(cs[j] & 0xFFu);'),
    ('sw_g2_d4e', 'arg[81u + 2u * j] = (uint8_t)(cs[j] >> 8);'),
    ('sw_g2_d4e', 'escn = 96u;'),
    ('sw_g2_d4', 'int dl = sw_g2_d4d(h, last);'),
    ('sw_g2_d4', 'bad |= dl < 0;'),
    ('sw_g2_d4', 'bad |= dl < 0;'),
    ('sw_g2_d4', 'dl = sw_g2_d4e(h, dl);'),
    # L22c (T-4adb/T-f6c1, sweetwlan=24; reviewed in docs/reviews/T-4adb-hwwrite-r1.md, pinned T-70d5): CLM container entry, BLOB list entries, request numbering, word copy of the shadow
    ('sw_fw_note', 'sw_clm.crc = e[i].crc;'),
    ('sw_fw_note', 'sw_clm.len = e[i].length;'),
    ('sw_fw_note', 'sw_clm.p = base + e[i].offset;'),
    ('sw_g2_add', 'e->rep = e->stride = e->vrng = e->arg = 0;'),
    ('sw_g2_clm_old', 'while (c-- > 0u) { if (i < sw_g2_clm_words(h, c)) { return sw_g2_clm_word(h, c, i);'),
    ('sw_g2_clm_set', 'h->g2clm = NULL;'),
    ('sw_g2_clm_set', 'h->g2clm = sw_clm.p;'),
    ('sw_g2_clm_set', 'h->g2clmlen = 0;'),
    ('sw_g2_clm_set', 'h->g2clmlen = sw_clm.len;'),
    ('sw_g2_clm_word', 'b = (len >> (8u * (q - 12u))) & 0xFFu;'),
    ('sw_g2_clm_word', 'b = (uint8_t)nm[q];'),
    ('sw_g2_clm_word', 'b = 0;'),
    ('sw_g2_clm_word', 'b = 2u;'),
    ('sw_g2_clm_word', 'b = flag & 0xFFu;'),
    ('sw_g2_clm_word', 'b = flag >> 8;'),
    ('sw_g2_clm_word', 'b = h->g2clm[c * SW_G2_CLMCHUNK + (q - 20u)];'),
    ('sw_g2_clm_word', 'const uint32_t flag = 0x1000u | (c == 0u ? 0x2u : 0u) | (c == last ? 0x4u : 0u);'),
    ('sw_g2_clm_word', 'const uint32_t last = (h->g2clmlen + SW_G2_CLMCHUNK - 1u) / SW_G2_CLMCHUNK - 1u, len = sw_g2_clm_len(h, c);'),
    ('sw_g2_clm_word', 'for (k = 0;'),
    ('sw_g2_clm_word', 'k++) { q = 4u * i + k;'),
    ('sw_g2_clm_word', 'q = 4u * i + k;'),
    ('sw_g2_clm_word', 'static const char nm[8] = "";'),
    ('sw_g2_clm_word', 'uint32_t v = 0, k, q, b;'),
    ('sw_g2_clm_word', 'v |= b << (8u * k);'),
    ('sw_g2_d4e', 'const int clm = h->d4b >= 11 && sw_g2_clm_set(h);'),
    ('sw_g2_d4e', 'j++) { if (j == 4u && clm) { last = sw_g2_e_clm(h, sh, &ix, &kq, last);'),
    ('sw_g2_d4e', 'kq++;'),
    ('sw_g2_d4e', 'last = sw_g2_e_clm(h, sh, &ix, &kq, last);'),
    ('sw_g2_e_clm', 'c++) { last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IOCTBUF].off, 0, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, last);'),
    ('sw_g2_e_clm', 'const uint32_t nch = (h->g2clmlen + SW_G2_CLMCHUNK - 1u) / SW_G2_CLMCHUNK;'),
    ('sw_g2_e_clm', 'for (c = 0;'),
    ('sw_g2_e_clm', 'for (i = 0;'),
    ('sw_g2_e_clm', 'for (i = 0;'),
    ('sw_g2_e_clm', 'h->g2clmpre[i] = sh[i];'),
    ('sw_g2_e_clm', 'h->g2l[last].arg = c;'),
    ('sw_g2_e_clm', 'h->g2l[last].kind = SW_G2K_BLOB;'),
    ('sw_g2_e_clm', 'h->g2l[last].rep = sw_g2_clm_words(h, c);'),
    ('sw_g2_e_clm', 'i++) { h->g2clmpre[i] = sh[i];'),
    ('sw_g2_e_clm', 'i++) { sh[i] = sw_g2_clm_old(h, nch, i);'),
    ('sw_g2_e_clm', 'last = last < 0 ? -1 : sw_g2_e_pay(h, sh, "", xz, 8u + 12u, 0u, last);'),
    ('sw_g2_e_clm', 'last = last < 0 ? -1 : sw_g2_e_rq(h, ix, kq, SW_G2_E_GET, 15u + 4u, last);'),
    ('sw_g2_e_clm', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IOCTBUF].off, 0, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, last);'),
    ('sw_g2_e_clm', 'last = sw_g2_e_pay(h, sh, "", NULL, 15u + 4u, 0u, last);'),
    ('sw_g2_e_clm', 'last = sw_g2_e_rq(h, ix, kq, SW_G2_E_SET, 8u + 12u + sw_g2_clm_len(h, c), last);'),
    ('sw_g2_e_clm', 'sh[i] = sw_g2_clm_old(h, nch, i);'),
    ('sw_g2_e_clm', 'static const uint8_t xz[12] = { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 };'),
    ('sw_g2_e_rq', '(*kq)++;'),
    ('sw_g2_e_rq', 'const uint64_t rpa = h->g2pa + h->g2plan.b[SWD_RESPBUF].off, ipa = h->g2pa + h->g2plan.b[SWD_IOCTBUF].off;'),
    ('sw_g2_e_rq', 'h->g2l[last - 7].kind = SW_G2K_SH_RBUF;'),
    ('sw_g2_e_rq', 'h->g2l[last - 7].stride = SW_G2_E_BUFSZ;'),
    ('sw_g2_e_rq', 'h->g2l[last - 7].v = (uint32_t)rpa;'),
    ('sw_g2_e_rq', 'h->g2l[last - 7].vrng = 7u * SW_G2_E_BUFSZ;'),
    ('sw_g2_e_rq', 'last = sw_g2_d4d_post(h, (*ix)++, w, last);'),
    ('sw_g2_e_rq', 'last = sw_g2_d4d_post(h, (*ix)++, w, last);'),
    ('sw_g2_exp', '*eold = sw_g2_clm_old(h, e->arg, i);'),
    ('sw_g2_exp', '*ev = sw_g2_clm_word(h, e->arg, i);'),
    ('sw_g2_exp', 'eo = off;'),
    ('sw_g2_exp', 'i = (e - h->g2l) + 12u;'),
    ('sw_g2_exp', 'i = (off - eo) / 4u;'),
    ('sw_g2_hook', 'g.in.clm = h->g2clm;'),
    ('sw_g2_hook', 'g.in.clmlen = h->g2clmlen;'),
    # T-1142: the glue grew after this list (T-4a7f/T-84b0/T-48a0: event re-posts, L24 join entries, CLM chunks, WLANCRED credentials). Each entry below was read and is a
    # store to host RAM only (struct sw_hw fields, locals, the static sw_jc, out-parameters); sw3_ptr results here (sw_g2_exp, sw_g2_evitem_ok) are only read through. No device store is added:
    # the one store through a pointer stays `*p = v;` in sw3_wr, and the stale entries these replace (g2sh spelled h->g2sh, nrep, epa) were dropped.
    ('sw3_wr', 'h->g2ev++;'),
    ('sw3_wr', 'h->g2ev0 = h->g2ev;'),
    ('sw3_wr', 'h->g2rd = 1;'),
    ('sw_g2_add', 'e->l24 = 0;'),
    ('sw_g2_d4e', ', ix = 9u, j, kq = 0, escn = 80u;'),
    ('sw_g2_d4e', 'esc = last;'),
    ('sw_g2_d4e', 'h->g2esc = esc;'),
    ('sw_g2_d4e', 'last = sw_g2_d4j(h, sh, (1u << 1) | (1u << 3), ix, kq, last);'),
    ('sw_g2_d4e', 'last = sw_g2_e_rx(h, esc) < 0 ? -1 : last;'),
    ('sw_g2_d4e', 'uint32_t w[10], sh[SW_G2_E_PAYW] = { SW_G2_D4D_CAP_W0 }, ix = 9u, j, kq = 0, escn = 80u;'),
    ('sw_g2_d4e', 'static const uint16_t cs[7] = { 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u };'),
    ('sw_g2_d4e', 'j++) { arg[80u + 2u * j] = (uint8_t)(cs[j] & 0xFFu);'),
    ('sw_g2_d4e', 'for (j = 0;'),
    ('sw_g2_d4j', 'b[15] = 1u;'),
    ('sw_g2_d4j', 'b[16] = 3u;'),
    ('sw_g2_d4j', 'b[17] = 26u;'),
    ('sw_g2_d4j', 'b[19u + bits[i] / 8u] |= (uint8_t)(1u << (bits[i] % 8u));'),
    ('sw_g2_d4j', 'for (i = 0;'),
    ('sw_g2_d4j', 'for (i = n0;'),
    ('sw_g2_d4j', 'for (k = 0;'),
    ('sw_g2_d4j', 'h->g2l[i].l24 = 1;'),
    ('sw_g2_d4j', 'i++) { b[19u + bits[i] / 8u] |= (uint8_t)(1u << (bits[i] % 8u));'),
    ('sw_g2_d4j', 'i++) { h->g2l[i].l24 = 1;'),
    ('sw_g2_d4j', 'k++) { memset(b, 0, sizeof b);'),
    ('sw_g2_d4j', 'key = 0;'),
    ('sw_g2_d4j', 'key = sw_g2_j_is(b, n, "") || sw_g2_j_is(b, n, "");'),      # T-857c: cur_etheraddr is withheld like wsec_key
    ('sw_g2_d4j', 'last = sw_g2_e_rq(h, &ix, &jq, SW_G2_E_SET, n, last);'),
    ('sw_g2_d4j', 'last = sw_g2_j_pay(h, sh, &shv, b, n, last, key);'),
    ('sw_g2_d4j', 'last = sw_g2_j_pay(h, sh, &shv, b, sizeof b, last, 0);'),
    ('sw_g2_d4j', 'n = 15u + 30u;'),
    ('sw_g2_d4j', 'n = h->jc[k - 1u].len;'),
    ('sw_g2_d4j', 'static const uint32_t bits[8] = { 69u, 0u, 16u, 5u, 6u, 11u, 12u, 46u };'),
    ('sw_g2_d4j', 'uint32_t jq = kq + 1u, k, i, n, n0 = h->g2n;'),
    ('sw_g2_e_rx', 'for (k = 0;'),
    ('sw_g2_e_rx', 'h->g2l[h->g2n - 1u].kind = SW_G2K_E_ITEM;'),
    ('sw_g2_e_rx', 'h->g2l[h->g2n - 1u].kind = SW_G2K_E_WR;'),
    ('sw_g2_e_rx', 'h->g2l[h->g2n - 1u].stride = SW_G2_E_BUFSZ;'),
    ('sw_g2_e_rx', 'h->g2l[h->g2n - 1u].vrng = 7u * SW_G2_E_BUFSZ;'),
    ('sw_g2_e_rx', 'k++) { if (sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + 4u * k, 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need) < 0) { return -1;'),
    ('sw_g2_evitem_ok', 'for (k = 0;'),
    ('sw_g2_evitem_ok', 'k++) { q = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_H2D_CTRL].off + 40u * j + 4u * k);'),
    ('sw_g2_evitem_ok', 'q = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_H2D_CTRL].off + 40u * j + 4u * k);'),
    ('sw_g2_exp', '*eold = e->old + sh;'),
    ('sw_g2_exp', '*ev = (e->v & 0x00FFFFFFu) | ((((e->v >> 24) + sh) % 253u) << 24);'),
    ('sw_g2_exp', '*ev = w[(e->off - h->g2plan.b[SWD_H2D_CTRL].off) / 4u];'),
    ('sw_g2_exp', 'case SW_G2K_E_ITEM: sl = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_IDX_H2D_W].off);'),
    ('sw_g2_exp', 'case SW_G2K_SH_DB: *ev = e->v + sh;'),
    ('sw_g2_exp', 'case SW_G2K_SH_ITEM: case SW_G2K_SH_ITEM0: case SW_G2K_SH_RBUF: eo += 40u * sh;'),
    ('sw_g2_exp', 'case SW_G2K_SH_WR: *ev = e->v + sh;'),
    ('sw_g2_exp', 'eo += 40u * i;'),
    ('sw_g2_exp', 'for (k = 0;'),
    ('sw_g2_exp', 'i = *sl & 0xFFFFu;'),
    ('sw_g2_exp', 'k++) { volatile uint32_t *q = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_H2D_CTRL].off + 40u * i + 4u * k);'),
    ('sw_g2_exp', 'uint32_t eo = e->off, i, k, sh = h->g2sh + h->g2ev, w[10];'),
    ('sw_g2_exp', 'volatile uint32_t *q = sw3_ptr(h, SW3_HOST, h->g2plan.b[SWD_H2D_CTRL].off + 40u * i + 4u * k);'),
    ('sw_g2_hook', 'g.jc = h->jc;'),
    ('sw_g2_hook', 'g.njc = h->njc;'),
    ('sw_g2_j_is', 'i++;'),
    ('sw_g2_j_is', 'uint32_t i = 0;'),
    ('sw_g2_j_pay', '*shv &= ~(1u << i);'),
    ('sw_g2_j_pay', 'for (i = 0;'),
    ('sw_g2_j_pay', 'i++) { v = (uint32_t)b[4u * i] | ((uint32_t)b[4u * i + 1u] << 8) | ((uint32_t)b[4u * i + 2u] << 16) | ((uint32_t)b[4u * i + 3u] << 24);'),
    ('sw_g2_j_pay', 'last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IOCTBUF].off + 4u * i, v, 0xFFFFFFFFu, vb ? sh[i] & ~SW_G2_VERBIT : sh[i], vb ? ~SW_G2_VERBIT : 0xFFFFFFFFu, 1, last);'),
    ('sw_g2_j_pay', 'sh[i] = v;'),
    ('sw_g2_j_pay', 'v = (uint32_t)b[4u * i] | ((uint32_t)b[4u * i + 1u] << 8) | ((uint32_t)b[4u * i + 2u] << 16) | ((uint32_t)b[4u * i + 3u] << 24);'),
    ('sw_g2_j_pay', 'vb = (*shv >> i) & 1u;'),
    ('sw_g2_list', 'h->g2esc = -1;'),
    ('sw_g2_list', 'h->g2ev = 0;'),
    ('sw_g2_list', 'h->g2ev0 = 0;'),
    ('sw_g2_list', 'h->g2rd = 0;'),
    ('sweet_wlan_creds', 'for (i = 0;'),
    ('sweet_wlan_creds', 'h->jc = NULL;'),
    ('sweet_wlan_creds', 'h->jc = sw_jc;'),
    ('sweet_wlan_creds', 'h->njc = 0;'),
    ('sweet_wlan_creds', 'h->njc = nc;'), ('sweet_wlan_creds', 'unsigned nc = SWG_J_NCMD;'), ('sweet_wlan_creds', 'have_mac = PE_parse_boot_arg_str("", macs, sizeof macs);'),  ('sweet_wlan_creds', 'if (level < 25u || swj_parse_mac(macs, mac) != 0 || (nc = swj_join_cmds_mac(c, SWG_J_NCMD, mac)) != SWG_J_NCMD + 1u) { swl_gap("", "", "");'),
    ('sweet_wlan_creds', 'i++) { memset(&sw_jc[i], 0, sizeof sw_jc[i]);'),
    ('sweet_wlan_creds', 'static const uint8_t any_bssid[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };'),
    ('sweet_wlan_creds', 'sw_jc[i].cmd = c[i].cmd;'),
    ('sweet_wlan_creds', 'sw_jc[i].len = c[i].len;'),
    ('sweet_wlan_creds', 'sw_jc[i].row = c[i].row;'),
    ('sweet_wlan_creds', 'uint64_t pa = 0, len = 0;'),
    ('sweet_wlan_creds', 'unsigned int psize = 0, i;'),
    ('sweet_wlan_creds', 'void const *p = NULL;'),
    ('sweet_wlan_init', 'int creds_guard __attribute__((unused, cleanup(sweet_wlan_creds_scrub))) = 0;'),
] + GLUE_ASSIGNS_L25)
# T-1142: the copy calls the glue may make (T-4a7f L24 payload build, T-48a0 WLANCRED credentials): each copies between host buffers (locals, the static sw_jc, a DT property
# read into locals); none has a device (BAR/window) destination. The statement text is the comment- and string-stripped one audit_assignments scans.
GLUE_MEMCOPIES = sorted([
    ("sw_g2_d4j", "memcpy(b, \"\", 15);"),
    ("sw_g2_d4j", "memcpy(b, h->jc[k - 1u].buf, n);"),
    ("sweet_wlan_creds", "memcpy(&pa, p, sizeof(pa));"),
    ("sweet_wlan_creds", "memcpy(&len, (uint8_t const *)p + sizeof(pa), sizeof(len));"),
    ("sweet_wlan_creds", "memcpy(sw_jc[i].buf, c[i].buf, c[i].len);"),
])
ASSIGN_OP = re.compile(r"(?<![=!<>])(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+|--")


def audit_assignments(glue):
    """Operator scan of the comment- and string-stripped glue: every `=`, compound assignment, `++`, `--` must be one of GLUE_ASSIGNS in its function;
    no mem*/atomic call, no use of the wr callback but `wb.wr = sw3_wr;`, no call of the 0124 window reader sw_rd()."""
    f = []
    g = re.sub(r"'(?:\\.|[^'\\])'", "0", re.sub(r'"(?:\\.|[^"\\])*"', '""', strip_comments(glue)))
    rng = [(m.group(1), m.start(), m.end()) for m in _FUNC.finditer(g)]
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
    # T-1142: a copy call is allowed only as one of GLUE_MEMCOPIES (function + statement, host buffers); any other, or an atomic store call, is a finding
    copies = []
    for m in re.finditer(r"\b(?:memcpy|memmove|bcopy|__builtin_mem\w*|os_atomic\w*|atomic_store\w*)\b", g):
        a = max(g.rfind(c, 0, m.start()) for c in ";{}") + 1
        copies.append((next((n for n, s, e in rng if s <= m.start() < e), "sweet_wlan_init"), " ".join(g[a:g.find(";", m.end()) + 1].split())))
    if sorted(copies) != GLUE_MEMCOPIES:
        f.append("memory-copy or atomic store call")
    # patch 0174: the one other wr is G2's own bus (struct swg_bus gb in sw_g2_hook), whose wr is sw_g2_wr
    if len(re.findall(r"(?:\.|->)\s*wr\b", g)) != 2 or "wb.wr = sw3_wr;" not in g or "gb.wr = sw_g2_wr;" not in body(g, "sw_g2_hook"):
        f.append("wr callback used other than by wb.wr = sw3_wr; and gb.wr = sw_g2_wr;")
    if re.search(r"\bsw_rd\s*\(", g):
        f.append("call of sw_rd in the added glue")
    # review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 6 (AP-370): struct sw_hw holds g2l[SW_G2_MAX] (61 KB); it must stay static, never a local of sweet_wlan_init (the kernel stack)
    if "static struct sw_hw hw;" not in g or re.search(r"(?<!static )struct sw_hw hw\b", g):
        f.append("sw_hw in sweet_wlan_init is not `static struct sw_hw hw;` (61 KB list, kernel stack)")
    return f


S3CALL = "\t\t\tsweet_wlan_stage3(&hw, level > 10 ? 10 : (int)level);\n"


def audit_rev_gate(glue):
    """Review 0132 F2: in the whole glue, sweet_wlan_init refuses level 6 (runs 5) unless swl_w14_rev_ok(&chip), before it calls sweet_wlan_stage3. Order checks use find() with an explicit -1 test (AP-47)."""
    f = []
    b = body(strip_comments(glue), "sweet_wlan_init")
    gate = b.find("if (level >= 6 && !swl_w14_rev_ok(&chip)) {")
    lower = b.find("level = 5;")
    call = b.find("sweet_wlan_stage3(&hw, level > 10 ? 10 : (int)level)")      # patch 0167: levels 11 and 12 run stage 3 at 10
    if gate < 0 or lower < 0 or call < 0 or not gate < lower < call:
        f.append("sweet_wlan_init does not refuse level 6 on an unchecked PCIe core rev before sweet_wlan_stage3")
    if b.count("swl_w14_rev_ok(") != 1 or "&chip" not in b[gate:lower]:
        f.append("the rev gate does not use the stage-1 chip")
    if "swl_w14_rev_ok(&chip)" in b[:b.find("swl_stage1(")] if b.find("swl_stage1(") >= 0 else True:
        f.append("the rev gate runs before stage 1 filled the chip")
    # patch 0133: level 7 (the CA7 release) is lowered to 6 unless swl_w16_rev_ok(&chip), after the level 6 gate and before the stage 3 call
    g7 = b.find("if (level >= 7 && !swl_w16_rev_ok(&chip)) {")
    l6 = b.find("level = 6;")
    if g7 < 0 or l6 < 0 or call < 0 or not gate < g7 < l6 < call:
        f.append("sweet_wlan_init does not refuse level 7 on an unchecked PCIe core rev before sweet_wlan_stage3")
    if b.count("swl_w16_rev_ok(") != 1 or "&chip" not in b[g7:l6]:
        f.append("the level 7 rev gate does not use the stage-1 chip")
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
    if "h->l3 >= 6" not in body(glue, "sw3_wr") or "v == h->rv" not in body(glue, "sw3_wr") or "off == 0 &&" not in body(glue, "sw3_wr") or "h->rv_ok && !h->rv_done" not in body(glue, "sw3_wr"):
        f.append("sw3_wr does not limit backplane address 0 to level 6 and the container's first dword")
    # patch 0133: the CA7 wrapper stores are limited in the glue independently of the header's tuples, before the store, and the state is updated from the same arguments after it
    w = body(glue, "sw3_wr")
    if "if (!sw3_ca7_ok(h, space, win, off, v)) {" not in w or w.find("sw3_ca7_ok(") < 0 or w.find("*p = v;") < 0 or not w.find("sw3_ca7_ok(") < w.find("*p = v;") or w.count("sw3_ca7_ok(") != 1:
        f.append("sw3_wr does not limit the CA7 wrapper stores before the store")
    if w.count("h->rel |= sw3_ca7_bit(h, space, win, off, v);") != 1 or not w.find("*p = v;") < w.find("h->rel |= sw3_ca7_bit("):
        f.append("sw3_wr does not record the CA7 release stores after the store")
    ok = body(glue, "sw3_ca7_ok")
    if not all(n in ok for n in ("SWW_WIN_CA7WRAP", "SWL_AGENT_IOCTL", "SWL_AGENT_RESET_CTL", "SWW_REL_MID", "SWW_REL_END", "h->l3 >= 7", "0x23u", "0x21u")):
        f.append("sw3_ca7_ok does not limit the CA7 wrapper stores to the table's values")
    if 'memcmp(e[i].tag, "FW  ", 4)' not in body(glue, "sw_fw_note"):
        f.append("sw_fw_note does not pick the FW entry by its tag")
    if 'memcmp(e[i].tag, "NVRM", 4)' not in body(glue, "sw_fw_note"):
        f.append("sw_fw_note does not pick the NVRM entry by its tag")
    # patch 0174 (R107 #1): G2's adapter calls sw3_wr directly, only from sw_g2_* functions, and sw_g2_hook only between h->g2 = 1 and h->g2 = 0 (sw3_wr's G2 branch decides those stores)
    g2c = sum(len(re.findall(r"\bsw3_wr\(", body(glue, n))) for n in ("sw_g2_bar0", "sw_g2_wr", "sw_g2_win2", "sw_g2_hook"))
    if len(re.findall(r"\bsw3_wr\b", glue)) != 2 + g2c or "wb.wr = sw3_wr;" not in glue or g2c == 0:
        f.append("sw3_wr used other than as the bus wr callback and by the sw_g2_* adapter")
    hk = body(glue, "sw_g2_hook")
    on, off = hk.find("h->g2 = 1;"), hk.find("h->g2 = 0;")
    if on < 0 or off < 0 or hk.count("h->g2 = 1;") != 1 or hk.count("h->g2 = 0;") != 1 or not on < hk.find("sw_g2_list(") < hk.find("swg_go(") < off or any(not on < m.start() < off for m in re.finditer(r"\bsw3_wr\(", hk)):
        f.append("sw_g2_hook stores outside h->g2 = 1 .. h->g2 = 0 (the list is built after h->g2 = 1: its window moves go through sw3_wr)")
    gs = strip_comments(glue)
    if len(re.findall(r"\bsw_g2_list\(", gs)) != 2 or "sw_g2_list(" not in hk or len(re.findall(r"\bsw_g2_win2\(", gs)) != 3 or len(re.findall(r"\bsw_g2_rdw\(", gs)) != 4:
        f.append("sw_g2_list is called other than from sw_g2_hook (after h->g2 = 1), or its window helpers from anywhere but the list")
    for n in ("sw_g2_bar0", "sw_g2_wr", "sw_g2_win2", "sw_g2_rdw", "sw_g2_list"):
        if re.search(r"\bh->g2\s*=", body(glue, n)):
            f.append(n + " changes h->g2")
    # review 0131 F1 (AP-74): what is CRC-checked is the private copy that is processed, not the container
    nvp = body(hdr, "swl_nv_prep")
    if "x->nv[i] = x->nv_src[i];" not in nvp or "src = swl_crc_b(src, x->nv[i]);" not in nvp or "nv_src[i])" in nvp.replace("x->nv[i] = x->nv_src[i];", ""):
        f.append("swl_nv_prep does not CRC its private copy")
    # review 0131 F2: the rTLV entropy is drawn in full
    if body(glue, "sw3_rng").count("read_random(buf, n);") != 1:
        f.append("sw3_rng does not draw all n bytes with read_random")
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
    ("level gate of swl_wr removed", [("if ((int)p->run > x->level - SWW_LEVEL0 || ((p->flags & SWT_W15) && !x->w15)) {\n\t\treturn swl_stop(x, SWW_E_LEVEL);", "if (0 || ((p->flags & SWT_W15) && !x->w15)) {\n\t\treturn swl_stop(x, SWW_E_LEVEL);")], None),
    ("level gate of the reads removed", [("(int)swl_rt[i].run > x->level - SWW_LEVEL0", "0")], None),
    ("stage 3 below level 3 allowed", [("if (x->level < SWW_LEVEL0 + 1) {", "if (0) {")], None),
    ("tuple consumed more than once", [("if ((p->flags & SWT_ONCE) && (x->done & (1ull << t))) {", "if (0) {")], None),
    ("predecessor not required", [("if (p->pred1 && !(x->done & (1ull << (p->pred1 - 1u)))) {", "if (0) {")], None),
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
    ("no read-back compare", [("if ((rb ^ val) & p->vmask) {", "if (0) {")], None),
    ("read-back compares all 32 bits", [("((rb ^ val) & p->vmask)", "((rb ^ val) & 0xFFFFFFFFu)")], None),
    ("read-back removed entirely", [("if (swl_rdx(x, space, win, off, &rb)) {", "if (0 && swl_rdx(x, space, win, off, &rb)) {"), ("if ((rb ^ val) & p->vmask) {", "if (0) {")], None),
    ("read-back read failure ignored", [("if (swl_rdx(x, space, win, off, &rb)) {", "if (0) {")], None),
    ("old read failure ignored", [("if (swl_rdx(x, space, win, off, &old)) {\n\t\t\treturn swl_stop(x, SWW_E_RD);", "if (0) {\n\t\t\treturn swl_stop(x, SWW_E_RD);")], None),
    ("done bit not recorded", [("\tx->done |= 1ull << t;\n", "")], None),
    ("bank count not recorded", [("\t\tx->nbank++;\n", "")], None),
    # --- the 0124 preconditions inside swl_wpre
    ("link precondition dropped", [("bad = swl_pre_link(iso, ltssm, a0, a1);", "bad = 0;")], None),
    ("endpoint precondition dropped", [("bad = swl_pre_ep(cmd, b0, b1, 0x2u);", "bad = 0;")], None),
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
    ("restore skipped check dropped", [("\t\t\tif (r->space == SWW_BAR0 ? rpre != 0 : ((rpre >= 1 && rpre <= 4) || rpre == 8)) {\n\t\t\t\tswl_gap(\"restore\", \"register group restore skipped (precondition)\", \"high\");\n\t\t\t\tprintf(\"SWEET WLAN: restore of group", "\t\t\tif (0) {\n\t\t\t\tswl_gap(\"restore\", \"register group restore skipped (precondition)\", \"high\");\n\t\t\t\tprintf(\"SWEET WLAN: restore of group")], None),
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
    ("run C pick: tuple not replaced when consumed", [("(((swl_wt[t].flags & SWT_ONCE) && (x->done & (1ull << t))) || ((swl_wt[t].flags & SWT_TW)", "((0) || ((swl_wt[t].flags & SWT_TW)")], None),
    ("run C pick: tuple not replaced by kind", [("|| ((swl_wt[t].flags & SWT_TW) && swl_wt[t].kind != swl_tw_kind(x, win + off)))) {", "|| 0)) {")], None),
    ("run C pick: kind taken from the offset", [("swl_wt[t].kind != swl_tw_kind(x, win + off)", "swl_wt[t].kind != swl_tw_kind(x, off)")], None),
    ("run C pick: takeover at a level that does not allow it", [("if (n <= t || (int)swl_wt[n].run > x->level - SWW_LEVEL0) {", "if (n <= t) {")], None),
    ("run C pick: no match reports DENIED", [("\t\t\treturn first;\n\t\t}\n\t\tt = n;", "\t\t\treturn -1;\n\t\t}\n\t\tt = n;")], None),
    ("run C pick: not used", [("t = swl_wr_pick(x, space, win, off, val);", "t = swl_wr_ok(space, win, off, val);")], None),
    ("run C ok: tail tuples need the tuple's value", [("} else if (p->flags & (SWT_BULK | SWT_TW | SWT_RV)) {", "} else if (p->flags & (SWT_BULK | SWT_RV)) {")], None),
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
    ("run C stage: tail check does not refuse", [("\tif (rc) {\n\t\tswl_gap(\"nvram\", \"tail geometry refused\", \"high\");\n\t\tprintf(\"SWEET WLAN: W11 tail geometry refused", "\tif (0) {\n\t\tswl_gap(\"nvram\", \"tail geometry refused\", \"high\");\n\t\tprintf(\"SWEET WLAN: W11 tail geometry refused")], None),
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
    # --- run D (patch 0132): the reset vector at backplane address 0 and the firmware identity pin
    ("run D tuple: no phase requirement", [(".flags = SWT_ONCE | SWT_RV, .win = 0, .off = 0, .vmask = 0xFFFFFFFFu, .omask = SWW_SEC_SBOOT, .oval = 0, .need = SWF_W13 }", ".flags = SWT_ONCE | SWT_RV, .win = 0, .off = 0, .vmask = 0xFFFFFFFFu, .need = 0 }")], None),
    # review 0132 F1/F3/F4: DHD parity for the vector word, the whole word must stick, a failed read-back read is not a chip value
    ("run D tuple: secured bit not tested", [(".omask = SWW_SEC_SBOOT, .oval = 0, ", "")], None),
    ("run D tuple: secured mask 0x1", [("#define SWW_SEC_SBOOT   0x2u", "#define SWW_SEC_SBOOT   0x1u")], None),
    ("run D tuple: secured value required set", [(".omask = SWW_SEC_SBOOT, .oval = 0,", ".omask = SWW_SEC_SBOOT, .oval = SWW_SEC_SBOOT,")], None),
    ("run D tuple: only the low half of the word compared", [(".vmask = 0xFFFFFFFFu, .omask = SWW_SEC_SBOOT", ".vmask = 0xFFFFu, .omask = SWW_SEC_SBOOT")], None),
    ("run D swl_wr: read-back read failure named read-back", [("\t\tx->rb = 0;      /* review 0132 F4: a failed read is not a chip value; the stop name says so */\n\t\treturn swl_stop(x, SWW_E_RD);", "\t\tx->rb = 0;      /* review 0132 F4: a failed read is not a chip value; the stop name says so */\n\t\treturn swl_stop(x, SWW_E_READBACK);")], None),
    ("run D swl_wr: read-back read failure ignored", [("\tif (swl_rdx(x, space, win, off, &rb)) {\n\t\tx->rb = 0;", "\tif (swl_rdx(x, space, win, off, &rb) && 0) {\n\t\tx->rb = 0;")], None),
    ("run D rev gate: rev 74 accepted", [("p->rev < 74 ||", "p->rev < 75 ||")], None),
    ("run D rev gate: rev 73 refused", [("p->rev < 74 ||", "p->rev < 73 ||")], None),
    ("run D rev gate: rev 128 refused", [("|| p->rev == 128", "")], None),
    ("run D rev gate: any core", [("swl_core(c, SWW_CORE_PCIE2, 0);", "swl_core(c, SWL_CORE_CC, 0);")], None),
    ("run D rev gate: missing core accepted", [("return p != 0 && (", "return p == 0 || (")], None),
    ("run D tuple: run 3", [(".row = 14, .run = 4,", ".row = 14, .run = 3,")], None),
    ("run D tuple: not once", [(".flags = SWT_ONCE | SWT_RV,", ".flags = SWT_RV,")], None),
    ("run D tuple: the whole of window 0", [(".win = 0, .off = 0, .vmask = 0xFFFFFFFFu, .omask = SWW_SEC_SBOOT, .oval = 0, .need = SWF_W13 }", ".win = 0, .off = 0, .hi = SWW_TCM_BYTES - 4u, .vmask = 0xFFFFFFFFu, .omask = SWW_SEC_SBOOT, .oval = 0, .need = SWF_W13 }")], None),
    ("run D tuple: value from the table", [("SWT_ONCE | SWT_RV, .win = 0", "SWT_ONCE, .win = 0")], None),
    ("run D tuple: allowlist match ignores the value flag", [("(SWT_BULK | SWT_TW | SWT_RV)", "(SWT_BULK | SWT_TW)")], None),
    ("run D swl_wr: guard removed", [("if ((p->flags & SWT_RV) && (x->img == NULL", "if (0 && (x->img == NULL")], None),
    ("run D swl_wr: no image", [("(p->flags & SWT_RV) && (x->img == NULL || x->img_len != SWW_FW_LEN", "(p->flags & SWT_RV) && (x->img_len != SWW_FW_LEN")], None),
    ("run D swl_wr: length not pinned", [("|| x->img_len != SWW_FW_LEN || x->img_crc != SWW_FW_CRC || val != swl_img_word(x, 0)", "|| x->img_crc != SWW_FW_CRC || val != swl_img_word(x, 0)")], None),
    ("run D swl_wr: CRC not pinned", [("|| x->img_len != SWW_FW_LEN || x->img_crc != SWW_FW_CRC || val != swl_img_word(x, 0)", "|| x->img_len != SWW_FW_LEN || val != swl_img_word(x, 0)")], None),
    ("run D swl_wr: value not image word 0", [("|| x->img_crc != SWW_FW_CRC || val != swl_img_word(x, 0)))", "|| x->img_crc != SWW_FW_CRC))")], None),
    ("run D pin: length not compared", [("if (x->img_len != SWW_FW_LEN || x->img_crc != SWW_FW_CRC) {\n\t\tprintf(\"SWEET WLAN: W%u firmware identity", "if (x->img_crc != SWW_FW_CRC) {\n\t\tprintf(\"SWEET WLAN: W%u firmware identity")], None),
    ("run D pin: CRC not compared", [("if (x->img_len != SWW_FW_LEN || x->img_crc != SWW_FW_CRC) {\n\t\tprintf(\"SWEET WLAN: W%u firmware identity", "if (x->img_len != SWW_FW_LEN) {\n\t\tprintf(\"SWEET WLAN: W%u firmware identity")], None),
    ("run D pin: always passes", [("if (x->img_len != SWW_FW_LEN || x->img_crc != SWW_FW_CRC) {\n\t\tprintf(\"SWEET WLAN: W%u firmware identity", "if (0) {\n\t\tprintf(\"SWEET WLAN: W%u firmware identity")], None),
    ("run D pin: another CRC constant", [("#define SWW_FW_CRC      0x1153ccffu", "#define SWW_FW_CRC      0x1153ccfeu")], None),
    ("run D stage: pin before the first store removed", [("if (x->level >= SWW_LEVEL0 + 4) {      /* run D: the firmware identity", "if (0) {      /* run D: the firmware identity")], None),
    ("run D stage: pin from level 5", [("if (x->level >= SWW_LEVEL0 + 4) {      /* run D: the firmware identity", "if (x->level >= SWW_LEVEL0 + 3) {      /* run D: the firmware identity")], None),
    ("run D stage: pin refusal ignored", [("\t\trc = swl_fw_pin(x);\n\t\tif (rc) {\n\t\t\tprintf(\"SWEET WLAN: stage 3 level %d stopped", "\t\trc = swl_fw_pin(x);\n\t\tif (0) {\n\t\t\tprintf(\"SWEET WLAN: stage 3 level %d stopped")], None),
    ("run D stage: W14 at level 5", [("if (x->level >= SWW_LEVEL0 + 4) {\n\t\trc = rc ? rc : swl_w14(x);", "if (x->level >= SWW_LEVEL0 + 3) {\n\t\trc = rc ? rc : swl_w14(x);")], None),
    ("run D stage: W14 above level 6 only", [("if (x->level >= SWW_LEVEL0 + 4) {\n\t\trc = rc ? rc : swl_w14(x);", "if (x->level >= SWW_LEVEL0 + 5) {\n\t\trc = rc ? rc : swl_w14(x);")], None),
    ("run D stage: W14 skipped", [("\t\trc = rc ? rc : swl_w14(x);\n", "")], None),
    ("run D stage: final line of level 6 missing", [("if (x->level >= SWW_LEVEL0 + 4) {\n\t\tprintf(\"SWEET WLAN: stage 3 level %d done w14", "if (0) {\n\t\tprintf(\"SWEET WLAN: stage 3 level %d done w14")], None),
    ("run D stage: final line old and new swapped", [("x->level, x->w14_old, x->rb, x->nv_varsize", "x->level, x->rb, x->w14_old, x->nv_varsize")], None),
    ("run D stage: final line without the CRC", [("x->level, x->w14_old, x->rb, x->nv_varsize, x->tok, x->nv_crc_rb);", "x->level, x->w14_old, x->rb, x->nv_varsize, x->tok, 0u);")], None),
    ("run D W14: image not checked again", [("\tSWW_TRY(swl_img_check(x));\n\tSWW_TRY(swl_fw_pin(x));\n\tSWW_RD(", "\tSWW_TRY(swl_fw_pin(x));\n\tSWW_RD(")], None),
    ("run D W14: pin not checked again", [("\tSWW_TRY(swl_fw_pin(x));\n\tSWW_RD(x, SWW_BAR2, 0, 0, x->w14_old);", "\tSWW_RD(x, SWW_BAR2, 0, 0, x->w14_old);")], None),
    ("run D W14: old value not read", [("\tSWW_RD(x, SWW_BAR2, 0, 0, x->w14_old);\n", "")], None),
    ("run D W14: read failure ignored", [("\tSWW_RD(x, SWW_BAR2, 0, 0, x->w14_old);\n", "\tswl_rd(x, SWW_BAR2, 0, 0, &x->w14_old);\n")], None),
    ("run D W14: refusal ignored", [("\trc = swl_wr(x, SWW_BAR2, 0, 0, swl_img_word(x, 0));\n\tif (rc) {", "\trc = swl_wr(x, SWW_BAR2, 0, 0, swl_img_word(x, 0));\n\tif (0) {")], None),
    ("run D W14: read-back value not printed", [("if (rc == -SWW_E_READBACK) {", "if (0) {")], None),
    ("run D W14: flag not set", [("return swl_done(x, SWF_W14);", "return swl_done(x, 0);")], None),
    ("run D W14: stored value is not image word 0", [("rc = swl_wr(x, SWW_BAR2, 0, 0, swl_img_word(x, 0));", "rc = swl_wr(x, SWW_BAR2, 0, 0, swl_img_word(x, 1));")], None),
    ("run D swl_wr: read-back word not kept (failure)", [("\t\tx->rb = rb;     /* run D logs the word that did not stick */\n", "")], None),
    ("run D swl_wr: read-back word not kept (success)", [("\t}\n\tx->rb = rb;\n", "\t}\n")], None),
]


# ---- patch 0133 (run E): the CA7 release (W16), the shared-word poll and the stop action (W17), the interrupt gate. Built from the final header, so a pattern that no longer matches fails at import (AP-80). ----
def _tuple_text(name):
    m = re.search(r"^\t\[%s\]\s*=\s*\{.*?\},\n" % name, final_header(), re.S | re.M)
    if not m:
        raise ValueError("no tuple " + name)
    return m.group(0)


def _t(label, name, a, b, equiv=None):
    """A mutant of one tuple: `a` (must occur in it) becomes `b` in that tuple only."""
    t = _tuple_text(name)
    if a not in t:
        raise ValueError("%s: %r is not in tuple %s" % (label, a, name))
    return (label, [(t, t.replace(a, b))], equiv)


def _f(label, fn, a, b, equiv=None):
    """A mutant of one function: `a` (must occur in it) becomes `b` in that function only (every occurrence there)."""
    t = body(final_header(), fn)
    if not t or a not in t:
        raise ValueError("%s: %r is not in function %s" % (label, a, fn))
    return (label, [(t, t.replace(a, b))], equiv)


def _fx(label, subs, equiv=None):
    """A mutant of one or more functions: subs = [(function, a, b), ...]; each `a` must occur in its function (the text after the earlier substitutions of the same function), every occurrence there becomes `b`."""
    texts, order = {}, []
    for fn, a, b in subs:
        if fn not in texts:
            texts[fn] = body(final_header(), fn)
            order.append(fn)
        if not texts[fn] or a not in texts[fn]:
            raise ValueError("%s: %r is not in function %s" % (label, a, fn))
        texts[fn] = texts[fn].replace(a, b)
    return (label, [(body(final_header(), fn), texts[fn]) for fn in order], equiv)


def _run_e_mutants():
    out = []
    names = {"TAIL_E": "SWT_WIN2_TAIL_E", "ASSERT": "SWT_REL_ASSERT", "MID": "SWT_REL_IOCTL_MID", "RELEASE": "SWT_REL_RELEASE", "END": "SWT_REL_IOCTL_END", "ZERO_E": "SWT_WIN2_ZERO_E"}
    chain = ("ASSERT", "MID", "RELEASE", "END")
    for k, n in names.items():
        out.append(_t("run E tuple %s: run 4 (not a level 7 only store)" % k, n, ".run = 5,", ".run = 4,"))
    out.append(("run E tuples: run 6 (never allowed at level 7)", [(".row = 16, .run = 5,", ".row = 16, .run = 6,"), (".row = 17, .run = 5,", ".row = 17, .run = 6,")], None))
    out.append(_t("run E tuple TAIL_E: not restored at a stop", names["TAIL_E"], ".rgrp = SWW_RG_W84, ", ""))
    for k in chain:
        out.append(_t("run E tuple %s: does not arm the CA7 restore" % k, names[k], ".rgrp = SWW_RG_CA, ", ""))
    for k in ("TAIL_E", "ZERO_E"):
        out.append(_t("run E tuple %s: not once" % k, names[k], ".flags = SWT_ONCE | SWT_TAIL,", ".flags = SWT_TAIL,"))
        out.append(_t("run E tuple %s: allowed in a RAM that ends at 4 MiB" % k, names[k], ".flags = SWT_ONCE | SWT_TAIL,", ".flags = SWT_ONCE,"))
    for k in chain:
        out.append(_t("run E tuple %s: not once" % k, names[k], ".flags = SWT_ONCE,", ".flags = 0,"))
    out.append(_t("run E tuple MID: no predecessor", names["MID"], ".pred1 = SWT_REL_ASSERT + 1, ", ""))
    out.append(_t("run E tuple RELEASE: no predecessor", names["RELEASE"], ".pred1 = SWT_REL_IOCTL_MID + 1, ", ""))
    out.append(_t("run E tuple RELEASE: the assert is its predecessor", names["RELEASE"], ".pred1 = SWT_REL_IOCTL_MID + 1,", ".pred1 = SWT_REL_ASSERT + 1,"))
    out.append(_t("run E tuple END: no predecessor", names["END"], ".pred1 = SWT_REL_RELEASE + 1, ", ""))
    out.append(_t("run E tuple END: the middle IOCTL is its predecessor", names["END"], ".pred1 = SWT_REL_RELEASE + 1,", ".pred1 = SWT_REL_IOCTL_MID + 1,"))
    out.append(_t("run E tuple ZERO_E: no predecessor", names["ZERO_E"], ".pred1 = SWT_WIN2_TAIL_E + 1, ", ""))
    for k in chain:
        out.append(_t("run E tuple %s: SWF_RELOK not needed" % k, names[k], "SWF_W14 | SWF_RELOK", "SWF_W14"))
        out.append(_t("run E tuple %s: W14 not needed" % k, names[k], "SWF_W14 | SWF_RELOK", "SWF_RELOK"))
    out.append(_t("run E tuple TAIL_E: W14 not needed", names["TAIL_E"], ".need = SWF_W14", ".need = 0"))
    out.append(_t("run E tuple ZERO_E: W16 not needed", names["ZERO_E"], ".need = SWF_W16", ".need = 0"))
    out.append(_t("run E tuple TAIL_E: old window not 0", names["TAIL_E"], ".omask = 0xFFFFFFFFu, .oval = 0, ", ""))
    out.append(_t("run E tuple ZERO_E: old window not the tail window", names["ZERO_E"], ".omask = 0xFFFFFFFFu, .oval = SWW_WIN_TAIL, ", ""))
    out.append(_t("run E tuple TAIL_E: another value", names["TAIL_E"], ".val = SWW_WIN_TAIL,", ".val = 0x200000u,"))
    out.append(_t("run E tuple ZERO_E: another value", names["ZERO_E"], ".val = 0,", ".val = SWW_WIN_TAIL,"))
    out.append(_t("run E tuple ASSERT: value 0", names["ASSERT"], ".val = 1u,", ".val = 0u,"))
    out.append(_t("run E tuple MID: CPUHALT stays set", names["MID"], ".val = SWW_REL_MID,", ".val = 0x23u,"))
    out.append(_t("run E tuple RELEASE: value 1", names["RELEASE"], ".val = 0u,", ".val = 1u,"))
    out.append(_t("run E tuple END: CPUHALT set", names["END"], ".val = SWW_REL_END,", ".val = 0x21u,"))
    out.append(_t("run E tuple ASSERT: reset need not be released before", names["ASSERT"], ".omask = 1u, .oval = 0u, ", ""))
    out.append(_t("run E tuple ASSERT: IOCTL not required to be the halted one", names["ASSERT"], ".coff = SWL_AGENT_IOCTL, .cmask = SWW_IOCTL_MASK, .cval = 0x21u, ", ""))
    out.append(_t("run E tuple ASSERT: IOCTL 0x23 is the halted one", names["ASSERT"], ".cval = 0x21u,", ".cval = 0x23u,"))
    out.append(_t("run E tuple ASSERT: IOCTL compared unmasked", names["ASSERT"], ".cmask = SWW_IOCTL_MASK,", ".cmask = 0xFFFFFFFFu,"))
    out.append(_t("run E tuple ASSERT: read-back not compared", names["ASSERT"], ".vmask = 1u,", ".vmask = 0u,"))
    out.append(_t("run E tuple MID: reset not required to be asserted", names["MID"], ".coff = SWL_AGENT_RESET_CTL, .cmask = 1u, .cval = 1u, ", ""))
    out.append(_t("run E tuple MID: read-back not compared", names["MID"], ".vmask = SWW_IOCTL_MASK,", ".vmask = 0u,"))
    out.append(_t("run E tuple MID: read-back unmasked", names["MID"], ".vmask = SWW_IOCTL_MASK,", ".vmask = 0xFFFFFFFFu,"))
    out.append(_t("run E tuple RELEASE: IOCTL not required to be the release one", names["RELEASE"], ".coff = SWL_AGENT_IOCTL, .cmask = SWW_IOCTL_MASK, .cval = SWW_REL_MID, ", ""))
    out.append(_t("run E tuple RELEASE: IOCTL compared unmasked", names["RELEASE"], ".cmask = SWW_IOCTL_MASK,", ".cmask = 0xFFFFFFFFu,"))
    out.append(_t("run E tuple RELEASE: read-back not compared", names["RELEASE"], ".vmask = 1u,", ".vmask = 0u,"))
    out.append(_t("run E tuple END: old IOCTL not required", names["END"], ".omask = SWW_IOCTL_MASK, .oval = SWW_REL_MID, ", ""))
    out.append(_t("run E tuple END: old IOCTL compared unmasked", names["END"], ".omask = SWW_IOCTL_MASK,", ".omask = 0xFFFFFFFFu,"))
    out.append(_t("run E tuple END: reset not required to be released", names["END"], ".coff = SWL_AGENT_RESET_CTL, .cmask = 1u, .cval = 0u, ", ""))
    out.append(_t("run E tuple END: read-back not compared", names["END"], ".vmask = SWW_IOCTL_MASK,", ".vmask = 0u,"))
    out.append(_t("run E tuple END: read-back unmasked", names["END"], ".vmask = SWW_IOCTL_MASK,", ".vmask = 0xFFFFFFFFu,"))
    # constants
    out.append(("run E: release IOCTL keeps CPUHALT", [("#define SWW_REL_MID     0x3u", "#define SWW_REL_MID     0x23u")], None))
    out.append(("run E: final IOCTL keeps CPUHALT", [("#define SWW_REL_END     0x1u", "#define SWW_REL_END     0x21u")], None))
    out.append(("run E: mailbox mask register at 0xC38", [("#define SWW_MBMASK      0xC34u", "#define SWW_MBMASK      0xC38u")], None))
    out.append(("run E: mailbox int register at 0xC2C", [("#define SWW_MBINT       0xC30u", "#define SWW_MBINT       0xC2Cu")], None))
    out.append(("run E: 99 polls", [("#define SWW_UP_POLLS    100u", "#define SWW_UP_POLLS    99u")], None))
    out.append(("run E: 101 polls", [("#define SWW_UP_POLLS    100u", "#define SWW_UP_POLLS    101u")], None))
    out.append(("run E: polls 40 ms apart", [("#define SWW_UP_US       50000u", "#define SWW_UP_US       40000u")], None))
    out.append(("run E: version is the low 16 bits", [("#define SWW_VER_MASK    0xFFu", "#define SWW_VER_MASK    0xFFFFu")], None))
    out.append(("run E: version 4 accepted", [("#define SWW_VER_MIN     5u", "#define SWW_VER_MIN     4u")], None))
    out.append(("run E: version 5 not accepted", [("#define SWW_VER_MIN     5u", "#define SWW_VER_MIN     6u")], None))
    out.append(("run E: version 8 accepted", [("#define SWW_VER_MAX     7u", "#define SWW_VER_MAX     8u")], None))
    out.append(("run E: version 7 not accepted", [("#define SWW_VER_MAX     7u", "#define SWW_VER_MAX     6u")], None))
    out.append(("run E: rev gate from 63", [("#define SWW_PCIE_REV_MIN 64u", "#define SWW_PCIE_REV_MIN 63u")], None))
    out.append(("run E: rev gate from 65", [("#define SWW_PCIE_REV_MIN 64u", "#define SWW_PCIE_REV_MIN 65u")], None))
    out.append(_f("run E rev gate: rev 74 accepted", "swl_w16_rev_ok", "p->rev < 74", "p->rev <= 74"))
    out.append(_f("run E rev gate: rev 73 refused", "swl_w16_rev_ok", "p->rev < 74", "p->rev < 73"))
    out.append(_f("run E rev gate: missing core accepted", "swl_w16_rev_ok", "return p != 0 &&", "return p == 0 ||"))
    out.append(_f("run E rev gate: any core", "swl_w16_rev_ok", "SWW_CORE_PCIE2", "SWL_CORE_CC"))
    out.append(("run E reads: mailbox range at run 6", [("{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },", "{ 6, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },")], None))
    out.append(("run E reads: mailbox range at run 4", [("{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },", "{ 4, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },")], None))
    out.append(("run E reads: mailbox range wider", [("{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },", "{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK + 4u },")], None))
    out.append(("run E reads: mailbox range from the page start", [("{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },", "{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT - 4u, SWW_MBMASK },")], None))
    out.append(("run E reads: mailbox range in any window", [("{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, SWW_MBMASK },", "{ 5, SWW_BAR0, SWW_WIN_SYSMEM, SWW_MBINT, SWW_MBMASK },")], None))
    # done mask
    out.append(("run E: done mask is 32 bits", [("uint64_t done;", "uint32_t done;")], None))
    out.append(("run E: tuple bit shifted as a 32 bit constant", [("(1ull << t)", "(1u << t)"), ("1ull << t;", "1u << t;"), ("(1ull << (p->pred1 - 1u))", "(1u << (p->pred1 - 1u))")], None))
    # swl_restore / swl_idle_wait
    out.append(_f("run E restore: no wait for pending operations", "swl_restore", "if (r->grp == SWW_RG_CA && (x->flags & SWF_W16)) {", "if (0) {"))
    out.append(_f("run E restore: waits also when the CPU never ran", "swl_restore", "if (r->grp == SWW_RG_CA && (x->flags & SWF_W16)) {", "if (r->grp == SWW_RG_CA) {"))
    out.append(_f("run E restore: waits for every group", "swl_restore", "if (r->grp == SWW_RG_CA && (x->flags & SWF_W16)) {", "if (x->flags & SWF_W16) {"))
    out.append(_f("run E idle wait: one read", "swl_idle_wait", "i < 30", "i < 1"))
    out.append(_f("run E idle wait: 29 reads", "swl_idle_wait", "i < 30", "i < 29"))
    out.append(_f("run E idle wait: 31 reads", "swl_idle_wait", "i < 30", "i < 31"))
    out.append(_f("run E idle wait: a failed read ends it", "swl_idle_wait", "&st) == 0 && st == 0", "&st) == 0 || st == 0"))
    out.append(_f("run E idle wait: does not stop at 0", "swl_idle_wait", "\t\t\tbreak;\n", ""))
    out.append(_f("run E idle wait: no delay", "swl_idle_wait", "\t\tx->b->delay_us(x->b->ctx, 10);\n", ""))
    out.append(_f("run E idle wait: 1 us delay", "swl_idle_wait", "delay_us(x->b->ctx, 10)", "delay_us(x->b->ctx, 1)"))
    out.append(_f("run E idle wait: the value read is not logged", "swl_idle_wait", "st, i);", "0u, i);"))
    # swl_st_spin
    out.append(_f("run E spin: one read", "swl_st_spin", "i < 30", "i < 1"))
    out.append(_f("run E spin: does not stop at 0", "swl_st_spin", "\t\t\tbreak;\n", ""))
    out.append(_f("run E spin: no delay", "swl_st_spin", "\t\t\tswl_delay(x, 10);\n", ""))
    out.append(_f("run E spin: a failed read is ignored", "swl_st_spin", "\t\tSWW_RD(x, SWW_BAR0, win, SWL_AGENT_RESET_ST, *st);\n", "\t\tswl_rd(x, SWW_BAR0, win, SWL_AGENT_RESET_ST, st);\n"))
    # swl_core_release
    out.append(_f("run E release: no wait for pending operations before the assert", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_ST, 0xFFFFFFFFu, 0u, 30, 10));\n\tSWW_TRY(swl_wr(x, SWW_BAR0, win, SWL_AGENT_RESET_CTL, 1u));", "\tSWW_TRY(swl_wr(x, SWW_BAR0, win, SWL_AGENT_RESET_CTL, 1u));"))
    out.append(_f("run E release: no 10 us after the assert", "swl_core_release", "\tswl_delay(x, 10);\n", ""))
    out.append(_f("run E release: no wait for the asserted reset", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_CTL, 1u, 1u, 1, 0));\n", ""))
    out.append(_f("run E release: no wait for pending operations after the assert", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_CTL, 1u, 1u, 1, 0));\n\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_ST, 0xFFFFFFFFu, 0u, 30, 10));\n", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_CTL, 1u, 1u, 1, 0));\n"))
    out.append(_f("run E release: no wait for pending operations before the release", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_IOCTL, SWW_IOCTL_MASK, SWW_REL_MID, 1, 0));\n\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_ST, 0xFFFFFFFFu, 0u, 30, 10));\n", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_IOCTL, SWW_IOCTL_MASK, SWW_REL_MID, 1, 0));\n"))
    out.append(_f("run E release: waits for pending operations 1 time", "swl_core_release", "0xFFFFFFFFu, 0u, 30, 10", "0xFFFFFFFFu, 0u, 1, 10"))
    out.append(_f("run E release: waits for pending operations 29 times", "swl_core_release", "0xFFFFFFFFu, 0u, 30, 10", "0xFFFFFFFFu, 0u, 29, 10"))
    out.append(_f("run E release: waits for pending operations 31 times", "swl_core_release", "0xFFFFFFFFu, 0u, 30, 10", "0xFFFFFFFFu, 0u, 31, 10"))
    out.append(_f("run E release: spin after the start skipped", "swl_core_release", "\tSWW_TRY(swl_st_spin(x, win, &st));\n", ""))
    out.append(_f("run E release: no 1 us before the final reads", "swl_core_release", "\tswl_delay(x, 1);\n", ""))
    out.append(_f("run E release: final IOCTL not compared", "swl_core_release", "(io & SWW_IOCTL_MASK) != SWW_REL_END || ", ""))
    out.append(_f("run E release: final reset state not compared", "swl_core_release", " || (ctl & 1u) != 0", ""))
    out.append(_f("run E release: final IOCTL compared unmasked", "swl_core_release", "(io & SWW_IOCTL_MASK) != SWW_REL_END", "io != SWW_REL_END"))
    out.append(_f("run E release: a pending status after the start is a stop", "swl_core_release", " || (ctl & 1u) != 0", " || (ctl & 1u) != 0 || st != 0"))
    out.append(_f("run E release: final state is not a stop", "swl_core_release", "\t\treturn swl_stop(x, SWW_E_STATE);\n", "\t\treturn 0;\n"))
    out.append(_f("run E release: final line not printed", "swl_core_release", "printf(\"SWEET WLAN: W%u wrapper %#x after the release", "(void)(\"SWEET WLAN: W%u wrapper %#x after the release"))
    # EQUIVALENT: each of the three is a re-read of a register that swl_wr() has just read back under the same mask, and that the next tuple's companion / the final check of swl_core_release() reads again before anything else is stored
    out.append(_f("run E release: no wait for IOCTL after the middle store", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_IOCTL, SWW_IOCTL_MASK, SWW_REL_MID, 1, 0));\n", "",
                  "swl_wr() reads the IOCTL back under SWW_IOCTL_MASK (tuple vmask) and the RELEASE tuple's companion reads it again (IOCTL & mask == 0x3) before the starting store; DHD only reads it back and drops the value (aiutils.c:1268-1269)"))
    out.append(_f("run E release: no wait for IOCTL after the final store", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_IOCTL, SWW_IOCTL_MASK, SWW_REL_END, 1, 0));\n", "",
                  "swl_wr() reads the IOCTL back under SWW_IOCTL_MASK and the final read of this function compares it again (io & mask == 0x1, E_STATE)"))
    out.append(_f("run E release: no wait for the released reset", "swl_core_release", "\tSWW_TRY(swl_wait(x, win, SWL_AGENT_RESET_CTL, 1u, 0u, 10, 10));\n", "",
                  "swl_wr() reads RESET_CTL back (bit 0 == 0), the END tuple's companion reads it again before the final IOCTL store, and the final read compares it again (ctl & 1 == 0, E_STATE)"))
    # W16
    out.append(_f("run E W16: image not checked again", "swl_w16", "\tSWW_TRY(swl_img_check(x));\n", ""))
    out.append(_f("run E W16: pin not checked again", "swl_w16", "\tSWW_TRY(swl_fw_pin(x));\n", ""))
    out.append(_f("run E W16: the checks do not stop it", "swl_w16", "\tSWW_TRY(swl_fw_pin(x));\n\treturn swl_w16_go(x);", "\tswl_fw_pin(x);\n\treturn swl_w16_go(x);"))
    out.append(_f("run E W16: vector not compared", "swl_w16_go", "\tif (v != swl_img_word(x, 0)) {\n", "\tif (0) {\n"))
    out.append(_f("run E W16: vector compared with word 1", "swl_w16_go", "if (v != swl_img_word(x, 0)) {", "if (v != swl_img_word(x, 1)) {"))
    out.append(_f("run E W16: mask register not read (the int register is)", "swl_w16_go", "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_MBMASK, mask);", "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, mask);"))
    out.append(_f("run E W16: mask not a stop", "swl_w16_go", "\tif (mask != 0) {\n", "\tif (0) {\n"))
    out.append(_f("run E W16: only bit 0 of the mask counts", "swl_w16_go", "\tif (mask != 0) {\n", "\tif ((mask & 1u) != 0) {\n"))
    out.append(_f("run E W16: only the low half of the mask counts", "swl_w16_go", "\tif (mask != 0) {\n", "\tif ((mask & 0xFFFFu) != 0) {\n"))
    out.append(_f("run E W16: int register all-ones not a stop", "swl_w16_go", "if (mask == 0xFFFFFFFFu || mint == 0xFFFFFFFFu) {", "if (mask == 0xFFFFFFFFu) {"))
    out.append(_f("run E W16: mask all-ones not a dead read", "swl_w16_go", "if (mask == 0xFFFFFFFFu || mint == 0xFFFFFFFFu) {", "if (mint == 0xFFFFFFFFu) {"))
    out.append(_f("run E W16: the window is not selected", "swl_w16_go", "\tSWW_TRY(swl_win(x, SWW_WIN_PCIE));\n", ""))
    out.append(_f("run E W16: poll window not set", "swl_w16_go", "\tif (win != 0) {\n\t\tSWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, win));\n\t}\n", ""))
    out.append(_f("run E W16: poll window set for RAM that ends at 4 MiB", "swl_w16_go", "win = end > SWW_TCM_BYTES ? SWW_WIN_TAIL : 0u", "win = end >= SWW_TCM_BYTES ? SWW_WIN_TAIL : 0u"))
    out.append(_f("run E W16: poll window never needed", "swl_w16_go", "win = end > SWW_TCM_BYTES ? SWW_WIN_TAIL : 0u", "win = 0u"))
    out.append(_f("run E W16: token baseline not compared", "swl_w16_go", "\tif (v != x->tok) {\n", "\tif (0) {\n"))
    out.append(_f("run E W16: token baseline compared with 0", "swl_w16_go", "\tif (v != x->tok) {\n", "\tif (v == 0) {\n"))
    out.append(_f("run E W16: token baseline not read at the last word", "swl_w16_go", "SWW_RD(x, SWW_BAR2, win, end - 4u - win, v);", "SWW_RD(x, SWW_BAR2, win, end - 8u - win, v);"))
    out.append(_f("run E W16: release allowed without SWF_RELOK", "swl_w16_go", "\tx->flags |= SWF_RELOK;\n", ""))
    out.append(_f("run E W16: CA7 window not selected", "swl_w16_go", "\tSWW_TRY(swl_win(x, SWW_WIN_CA7WRAP));\n", ""))
    out.append(_f("run E release: SWF_W16 not set", "swl_core_release", "x->flags |= SWF_W16;", ""))
    _t0 = body(final_header(), "swl_core_release")
    out.append(("run E release: SWF_W16 set after the starting store", [(_t0, _t0.replace("x->flags |= SWF_W16;", "", 1).replace("\tSWW_TRY(swl_st_spin(x, win, &st));\n", "\tx->flags |= SWF_W16;\n\tSWW_TRY(swl_st_spin(x, win, &st));\n"))], None))
    _t1 = body(final_header(), "swl_w16_go")
    out.append(("run E release: SWF_W16 set before the release (the old place)", [(_t0, _t0.replace("x->flags |= SWF_W16;", "", 1)), (_t1, _t1.replace("\tSWW_TRY(swl_core_release(x, SWW_WIN_CA7WRAP));\n", "\tx->flags |= SWF_W16;\n\tSWW_TRY(swl_core_release(x, SWW_WIN_CA7WRAP));\n"))], None))
    out.append(_f("run E W16: release failure ignored", "swl_w16_go", "\tSWW_TRY(swl_core_release(x, SWW_WIN_CA7WRAP));\n", "\tswl_core_release(x, SWW_WIN_CA7WRAP);\n"))
    out.append(_f("run E W16: row not named", "swl_w16_go", "\tx->row = 16;\n", ""))
    # W17
    out.append(_f("run E W17: preconditions not read before a poll", "swl_w17", "\t\tif (swl_wpre(x)) {\n\t\t\treturn swl_stop(x, SWW_E_PRE);\n\t\t}\n", ""))
    out.append(_f("run E W17: precondition failure ignored", "swl_w17", "\t\tif (swl_wpre(x)) {\n\t\t\treturn swl_stop(x, SWW_E_PRE);\n\t\t}\n", "\t\tswl_wpre(x);\n"))
    out.append(_f("run E W17: counters not reset", "swl_w17", "\tx->polls = 0;\n\tx->shared = 0;\n", ""))
    out.append(_f("run E W17: polls not reset", "swl_w17", "\tx->polls = 0;\n", ""))
    out.append(_f("run E W17: shared not reset", "swl_w17", "\tx->shared = 0;\n", ""))
    out.append(_f("run E W17: polls not counted", "swl_w17", "\t\tx->polls = i + 1;\n", ""))
    out.append(_f("run E W17: last word not kept", "swl_w17", "\t\tx->shared = v;\n", ""))
    out.append(_f("run E W17: every poll printed", "swl_w17", "if (!seen || v != prev) {", "if (1) {"))
    out.append(_f("run E W17: only the first poll printed", "swl_w17", "if (!seen || v != prev) {", "if (!seen) {"))
    out.append(_f("run E W17: a first word of 0 not printed", "swl_w17", "if (!seen || v != prev) {", "if (v != prev) {"))
    out.append(_f("run E W17: no poll printed", "swl_w17", "if (!seen || v != prev) {", "if (0) {"))
    out.append(_f("run E W17: poll time wrong", "swl_w17", "i * (SWW_UP_US / 1000u)", "(i + 1) * (SWW_UP_US / 1000u)"))
    out.append(_f("run E W17: all-ones is not a stop", "swl_w17", "\t\tif (v == 0xFFFFFFFFu) {\n\t\t\treturn swl_stop(x, SWW_E_DEAD);\n\t\t}\n", ""))
    out.append(_f("run E W17: 0 is a shared address", "swl_w17", "if (v != 0 && v != x->tok) {", "if (v != x->tok) {"))
    out.append(_f("run E W17: the token is a shared address", "swl_w17", "if (v != 0 && v != x->tok) {", "if (v != 0) {"))
    out.append(_f("run E W17: first word is taken", "swl_w17", "if (v != 0 && v != x->tok) {", "if (1) {"))
    out.append(_f("run E W17: no delay between the polls", "swl_w17", "\t\t\tswl_delay(x, SWW_UP_US);\n", ""))
    out.append(_f("run E W17: delay after the last poll too", "swl_w17", "if (i + 1 < SWW_UP_POLLS) {", "if (1) {"))
    out.append(_f("run E W17: timeout not recognised (0 only)", "swl_w17", "if (v == 0 || v == x->tok) {", "if (v == 0) {"))
    out.append(_f("run E W17: timeout not recognised (token only)", "swl_w17", "if (v == 0 || v == x->tok) {", "if (v == x->tok) {"))
    out.append(_f("run E W17: timeout never recognised", "swl_w17", "if (v == 0 || v == x->tok) {", "if (0) {"))
    out.append(_f("run E W17: timeout line without the last word", "swl_w17", "x->polls, x->polls * (SWW_UP_US / 1000u), v, x->tok,", "x->polls, x->polls * (SWW_UP_US / 1000u), 0u, x->tok,"))
    out.append(_f("run E W17: timeout line without the CA7 state", "swl_w17", "io, ctl, st, c88,", "0u, 0u, 0u, c88,"))
    out.append(_f("run E W17: timeout line without cfg 0x88", "swl_w17", "io, ctl, st, c88,", "io, ctl, st, 0u,"))
    out.append(_f("run E W17: failed state read not reported", "swl_w17", "rd ? \" (a state read failed)\" : \"\"", "\"\""))
    out.append(_f("run E W17: timeout is success", "swl_w17", "\t\treturn swl_stop(x, SWW_E_UP);\n", "\t\treturn 0;\n"))
    out.append(_f("run E W17: timeout stop without the restore", "swl_w17", "\t\treturn swl_stop(x, SWW_E_UP);\n", "\t\treturn -SWW_E_UP;\n"))
    out.append(_f("run E W17: unaligned shared address accepted", "swl_w17", "(sh & 3u) != 0 || ", ""))
    out.append(_f("run E W17: shared address below the RAM accepted", "swl_w17", " || sh < SWW_RAM_BASE", ""))
    out.append(_f("run E W17: the RAM base is not a shared address", "swl_w17", "sh < SWW_RAM_BASE", "sh <= SWW_RAM_BASE"))
    out.append(_f("run E W17: the last word is a shared address", "swl_w17", "sh >= end - 4u", "sh > end - 4u"))
    out.append(_f("run E W17: only the end of the RAM bounds it", "swl_w17", "sh >= end - 4u", "sh >= end"))
    out.append(_f("run E W17: the end bound is one word too low", "swl_w17", "sh >= end - 4u", "sh >= end - 8u"))
    out.append(_f("run E W17: shared address above the RAM accepted", "swl_w17", " || sh >= end - 4u", ""))
    out.append(_f("run E W17: bad shared address is not a stop", "swl_w17", "\t\treturn swl_stop(x, SWW_E_SHARED);\n", "\t\treturn 0;\n"))
    out.append(_f("run E W17: window split one word late", "swl_w17", "sw = sh >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "sw = sh > SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;"))
    out.append(_f("run E W17: always the tail window", "swl_w17", "sw = sh >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "sw = SWW_WIN_TAIL;"))
    out.append(_f("run E W17: window never changed", "swl_w17", "\tif (sw != win) {\n", "\tif (0) {\n"))
    out.append(_f("run E W17: window changed when it need not be", "swl_w17", "\tif (sw != win) {\n", "\tif (sw == win) {\n"))
    out.append(_f("run E W17: version word of a dead read taken", "swl_w17", "\tif (fl == 0xFFFFFFFFu) {\n", "\tif (0) {\n"))
    out.append(_f("run E W17: version read at the wrong offset", "swl_w17", "SWW_RD(x, SWW_BAR2, sw, sh - sw, fl);", "SWW_RD(x, SWW_BAR2, sw, sh - sw + 4u, fl);"))
    out.append(_f("run E W17: version not stored", "swl_w17", "\tx->version = fl & SWW_VER_MASK;\n", ""))
    out.append(_f("run E W17: version from the wrong bits", "swl_w17", "x->version = fl & SWW_VER_MASK;", "x->version = (fl >> 8) & SWW_VER_MASK;"))
    out.append(_f("run E W17: version below 5 not remarked", "swl_w17", "x->version < SWW_VER_MIN || x->version > SWW_VER_MAX", "x->version > SWW_VER_MAX"))
    out.append(_f("run E W17: version above 7 not remarked", "swl_w17", "x->version < SWW_VER_MIN || x->version > SWW_VER_MAX", "x->version < SWW_VER_MIN"))
    out.append(_f("run E W17: version remark on every version", "swl_w17", "x->version < SWW_VER_MIN || x->version > SWW_VER_MAX", "1"))
    out.append(_f("run E W17: shared structure line without the flags", "swl_w17", "sh, x->polls, fl, x->version,", "sh, x->polls, 0u, x->version,"))
    out.append(_f("run E W17: no stop action on success", "swl_w17", "\tswl_restore(x);\n", ""))
    out.append(_f("run E W17: halted without the read-back", "swl_w17", "x->halted = x->ca_ok;", "x->halted = 1;"))
    out.append(_f("run E W17: halted whatever the restore did but a skip", "swl_w17", "x->halted = x->ca_ok;", "x->halted = x->restore != SWW_RS_SKIPPED;"))
    out.append(_f("run E W17: an unconfirmed halt is success", "swl_w17", "\tif (!x->halted) {\n", "\tif (0) {\n"))
    out.append(_f("run E W17: an unconfirmed halt is not latched", "swl_w17", "\t\tx->stopped = 1;\n", ""))
    out.append(_f("run E W17: an unconfirmed halt has no code", "swl_w17", "\t\tx->stop_code = SWW_E_HALT;\n", ""))
    out.append(_f("run E W17: an unconfirmed halt is another code", "swl_w17", "return -SWW_E_HALT;", "return -SWW_E_STATE;"))
    out.append(_f("run E W17: flag not set", "swl_w17", "\tx->flags |= SWF_W17;\n", ""))
    out.append(_f("run E W17: row not named", "swl_w17", "\tx->row = 17;\n", ""))
    # the stage
    out.append(("run E stage: release at level 6", [("if (x->level >= SWW_LEVEL0 + 5) {      /* run E: the release and the poll", "if (x->level >= SWW_LEVEL0 + 4) {      /* run E: the release and the poll")], None))
    out.append(("run E stage: release only at level 8", [("if (x->level >= SWW_LEVEL0 + 5) {      /* run E: the release and the poll", "if (x->level >= SWW_LEVEL0 + 6) {      /* run E: the release and the poll")], None))
    out.append(("run E stage: W16 skipped", [("\t\trc = rc ? rc : swl_w16(x);\n", "")], None))
    out.append(("run E stage: W16 without its image checks", [("\t\trc = rc ? rc : swl_w16(x);\n", "\t\trc = rc ? rc : swl_w16_go(x);\n")], None))
    out.append(("run E stage: W17 skipped", [("\t\trc = rc ? rc : swl_w17(x);\n", "")], None))
    out.append(("run E stage: W17 before W16", [("\t\trc = rc ? rc : swl_w16(x);\n\t\trc = rc ? rc : swl_w17(x);\n", "\t\trc = rc ? rc : swl_w17(x);\n\t\trc = rc ? rc : swl_w16(x);\n")], None))
    out.append(("run E stage: W16 at level 6 also", [("\tif (x->level >= SWW_LEVEL0 + 4) {\n\t\trc = rc ? rc : swl_w14(x);\n\t}\n", "\tif (x->level >= SWW_LEVEL0 + 4) {\n\t\trc = rc ? rc : swl_w14(x);\n\t\trc = rc ? rc : swl_w16(x);\n\t}\n")], None))
    out.append(("run E stage: final line of level 7 missing", [("if (x->level >= SWW_LEVEL0 + 5) {\n\t\tprintf(\"SWEET WLAN: stage 3 level %d done shared", "if (0) {\n\t\tprintf(\"SWEET WLAN: stage 3 level %d done shared")], None))
    out.append(("run E stage: final line shared and version swapped", [("x->level, x->shared, x->version, x->polls, x->halted);", "x->level, x->version, x->shared, x->polls, x->halted);")], None))
    out.append(("run E stage: final line polls and halted swapped", [("x->level, x->shared, x->version, x->polls, x->halted);", "x->level, x->shared, x->version, x->halted, x->polls);")], None))
    out.append(("run E stage: final line halted as 1", [("x->level, x->shared, x->version, x->polls, x->halted);", "x->level, x->shared, x->version, x->polls, 1);")],
                "the line is printed only when rc == 0, and swl_w17() returns 0 only after x->halted = ca_ok held (it returns -SWW_E_HALT otherwise): x->halted is 1 whenever this line is printed"))
    out.append(("run E stage: the CA7 state of a stop not said", [("\t\tif (x->level >= SWW_LEVEL0 + 5) {      /* run E: say what state the CA7 is left in */", "\t\tif (0) {      /* run E: say what state the CA7 is left in */")], None))
    out.append(("run E stage: the CA7 state said below level 7", [("\t\tif (x->level >= SWW_LEVEL0 + 5) {      /* run E: say what state the CA7 is left in */", "\t\tif (x->level >= SWW_LEVEL0 + 4) {      /* run E: say what state the CA7 is left in */")], None))
    out.append(("run E stage: an unreleased CA7 reported as released", [("!(x->flags & SWF_W16) ? \"never released, halted=1\"", "(x->flags & SWF_W16) ? \"never released, halted=1\"")], None))
    out.append(("run E stage: the CA7 summary from the whole restore, not the CA7 row", [("x->ca_ok ? \"released and reset again (read back), halted=1\"", "x->restore == SWW_RS_OK ? \"released and reset again (read back), halted=1\"")], None))
    out.append(("run E stage: a released CA7 reported halted without the read-back", [("x->ca_ok ? \"released and reset again (read back), halted=1\"", "x->restore != SWW_RS_NONE ? \"released and reset again (read back), halted=1\"")], None))
    out.append(("run E stage: an unconfirmed CA7 reported as halted", [("\"RELEASED AND NOT CONFIRMED IN RESET, halted=0\"", "\"released and reset again (read back), halted=1\"")], None))
    # review 0133 F1: the CA7 re-halt when only the EP Command check failed (swl_restore, swl_cmd_mem)
    ex = "rpre == 5 && r->space == SWW_BAR0 && r->grp == SWW_RG_CA && (x->flags & SWF_W16) && swl_cmd_mem(x, &cmd)"
    out.append(_f("run E F1: Command-change re-halt not attempted", "swl_restore", ex, "0"))
    out.append(_f("run E F1: re-halt attempted with MEM clear", "swl_cmd_mem", " && (*cmd & 0x2u) != 0", ""))
    out.append(_f("run E F1: re-halt attempted with only IO decode on", "swl_cmd_mem", "(*cmd & 0x2u) != 0", "(*cmd & 0x1u) != 0"))
    out.append(_f("run E F1: re-halt attempted with BAR0 moved", "swl_cmd_mem", " && swl_pre_ep(0x2u, b0, b1, 0x2u) == 0", ""))
    out.append(_f("run E F1: re-halt attempted with BAR0 high moved", "swl_cmd_mem", "swl_pre_ep(0x2u, b0, b1, 0x2u)", "swl_pre_ep(0x2u, b0, 0u, 0x2u)"))
    out.append(_f("run E F1: re-halt attempted with BAR0 low moved", "swl_cmd_mem", "swl_pre_ep(0x2u, b0, b1, 0x2u)", "swl_pre_ep(0x2u, 0x60000004u, b1, 0x2u)"))
    out.append(_f("run E F1: Command read not fail-closed (compare inverted)", "swl_cmd_mem", "swl_rdx(x, SWW_CFG, 0, 0x04u, cmd) == 0", "swl_rdx(x, SWW_CFG, 0, 0x04u, cmd) != 0"))
    out.append(_f("run E F1: BAR0 low read not fail-closed (compare inverted)", "swl_cmd_mem", "swl_rdx(x, SWW_CFG, 0, 0x10u, &b0) == 0", "swl_rdx(x, SWW_CFG, 0, 0x10u, &b0) != 0"))
    out.append(_f("run E F1: BAR0 high read not fail-closed (compare inverted)", "swl_cmd_mem", "swl_rdx(x, SWW_CFG, 0, 0x14u, &b1) == 0", "swl_rdx(x, SWW_CFG, 0, 0x14u, &b1) != 0"))
    for lab, a, b, why in (
            ("Command read failure ignored", "swl_rdx(x, SWW_CFG, 0, 0x04u, cmd) == 0", "(swl_rdx(x, SWW_CFG, 0, 0x04u, cmd) | 1u) != 0",
             "a failed read leaves *cmd at the 0 that swl_cmd_mem() stored first, and (0 & 0x2) is 0: the same refusal"),
            ("BAR0 low read failure ignored", "swl_rdx(x, SWW_CFG, 0, 0x10u, &b0) == 0", "(swl_rdx(x, SWW_CFG, 0, 0x10u, &b0) | 1u) != 0",
             "a failed read leaves b0 at the all-ones that swl_cmd_mem() initialised it to, which swl_pre_ep() refuses (6) like a failed read: the same refusal"),
            ("BAR0 high read failure ignored", "swl_rdx(x, SWW_CFG, 0, 0x14u, &b1) == 0", "(swl_rdx(x, SWW_CFG, 0, 0x14u, &b1) | 1u) != 0",
             "a failed read leaves b1 at the all-ones that swl_cmd_mem() initialised it to, which swl_pre_ep() refuses (6) like a failed read: the same refusal")):
        out.append(_f("run E F1: " + lab + " (defence in depth)", "swl_cmd_mem", a, b, why))
    out.append(_f("run E F1: exception for any failed precondition", "swl_restore", "rpre == 5 && r->space", "rpre != 0 && r->space"))
    out.append(_f("run E F1: exception for the Command check and the ATU region 2 one", "swl_restore", "rpre == 5 && r->space", "(rpre == 5 || rpre == 7) && r->space"))
    out.append(_f("run E F1: exception for every group", "swl_restore", "r->space == SWW_BAR0 && r->grp == SWW_RG_CA && (x->flags & SWF_W16) && swl_cmd_mem", "r->space == SWW_BAR0 && (x->flags & SWF_W16) && swl_cmd_mem"))
    out.append(_f("run E F1: exception before the CPU was released", "swl_restore", "r->grp == SWW_RG_CA && (x->flags & SWF_W16) && swl_cmd_mem", "r->grp == SWW_RG_CA && swl_cmd_mem"))
    out.append(_f("run E F1: exception does not clear the precondition", "swl_restore", "\t\t\t\trpre = 0;\n", ""))
    out.append(_f("run E F1: exception not said", "swl_restore", "\"SWEET WLAN: restore: EP command %#x changed, re-halting the CA7 anyway\\n\"", "\"\""))
    # review 0133 F4: the config rows of the restore after the link checks failed
    out.append(_f("run E F4: config rows run without the link precondition", "swl_restore", "r->space == SWW_BAR0 || (r->space == SWW_CFG && (x->flags & SWF_W16))", "r->space == SWW_BAR0"))
    out.append(_f("run E F4: config rows gated before the release too", "swl_restore", "(r->space == SWW_CFG && (x->flags & SWF_W16))", "r->space == SWW_CFG"))
    for lab, b in (("every failed precondition", "rpre != 0"), ("not the first link check", "(rpre >= 2 && rpre <= 4)"), ("not the last link check", "(rpre >= 1 && rpre <= 3)"),
                   ("the Command check too", "(rpre >= 1 && rpre <= 5)"), ("never", "0")):
        out.append(_f("run E F4: config row skipped for " + lab, "swl_restore", "(rpre >= 1 && rpre <= 4)", b))
    out.append(_f("run E R2-F3: config row on code 8", "swl_restore", "((rpre >= 1 && rpre <= 4) || rpre == 8)", "(rpre >= 1 && rpre <= 4)"))
    out.append(_f("run E F4: config row skip not said", "swl_restore", "\"SWEET WLAN: restore of group %u skipped, precondition %d\\n\", r->grp, rpre", "\"\", r->grp, rpre"))
    out.append(_f("run E F4: skipped config row not counted as skipped", "swl_restore", "\t\t\t\tskipped = 1;\n", ""))
    # review 0133 F3: the CA7 row's own read-back
    out.append(_f("run E F3: CA7 row result not recorded", "swl_restore", "\t\t\tx->ca_ok = ok;\n", ""))
    out.append(_f("run E F3: CA7 result taken from any row", "swl_restore", "\t\tif (r->grp == SWW_RG_CA) {\n\t\t\tx->ca_ok = ok;", "\t\tif (1) {\n\t\t\tx->ca_ok = ok;"))
    out.append(_f("run E F3: CA7 result recorded as stored", "swl_restore", "x->ca_ok = ok;", "x->ca_ok = 1;"))
    out.append(_f("run E W17: halted from the whole restore (the old text)", "swl_w17", "x->halted = x->ca_ok;", "x->halted = x->restore == SWW_RS_OK;"))
    # review 0133 F2: the host controller's interrupt enables
    out.append(_f("run E F2: INTx enables not a stop", "swl_w16_go", "if ((en0 & SWW_RC_INTX_EN) != 0) {", "if (0) {"))
    out.append(_f("run E F2: link-down enable not a stop", "swl_w16_go", "if ((en1 & SWW_RC_LDOWN_EN) != 0) {", "if (0) {"))
    out.append(_f("run E F2: MSI enables not a stop", "swl_w16_go", "if ((en2 & SWW_RC_MSI_EN) != 0) {", "if (0) {"))
    # review 0133 R2-F1: any enable bit stops, with its own line
    out.append(_f("run E R2-F1: enables outside the masks not a stop", "swl_w16_go", "if ((en0 | en1 | en2) != 0) {", "if (0) {"))
    out.append(_f("run E R2-F1: IRQ0 enable not in the any-bit stop", "swl_w16_go", "(en0 | en1 | en2)", "(en1 | en2)"))
    out.append(_f("run E R2-F1: IRQ1 enable not in the any-bit stop", "swl_w16_go", "(en0 | en1 | en2)", "(en0 | en2)"))
    out.append(_f("run E R2-F1: IRQ2 enable not in the any-bit stop", "swl_w16_go", "(en0 | en1 | en2)", "(en0 | en1)"))
    out.append(_f("run E F2: INTx status instead of the enable", "swl_w16_go", "(en0 & SWW_RC_INTX_EN)", "(rc0 & SWW_RC_INTX_EN)"))
    out.append(_f("run E F2: link-down status instead of the enable", "swl_w16_go", "(en1 & SWW_RC_LDOWN_EN)", "(rc1 & SWW_RC_LDOWN_EN)"))
    out.append(_f("run E F2: MSI status instead of the enable", "swl_w16_go", "(en2 & SWW_RC_MSI_EN)", "(rc2 & SWW_RC_MSI_EN)"))
    for lab, a, b in (("INTA missing", "#define SWW_RC_INTX_EN  0x00154000u", "#define SWW_RC_INTX_EN  0x00150000u"), ("INTB missing", "#define SWW_RC_INTX_EN  0x00154000u", "#define SWW_RC_INTX_EN  0x00144000u"),
                      ("INTC missing", "#define SWW_RC_INTX_EN  0x00154000u", "#define SWW_RC_INTX_EN  0x00114000u"), ("INTD missing", "#define SWW_RC_INTX_EN  0x00154000u", "#define SWW_RC_INTX_EN  0x00054000u"),
                      ("INTx bit 15 instead of 14", "#define SWW_RC_INTX_EN  0x00154000u", "#define SWW_RC_INTX_EN  0x00158000u"),
                      ("link-down mask 0", "#define SWW_RC_LDOWN_EN 0x00000400u", "#define SWW_RC_LDOWN_EN 0x00000000u"), ("link-down bit 11 instead of 10", "#define SWW_RC_LDOWN_EN 0x00000400u", "#define SWW_RC_LDOWN_EN 0x00000800u"),
                      ("MSI falling missing", "#define SWW_RC_MSI_EN   0x01030000u", "#define SWW_RC_MSI_EN   0x01020000u"), ("MSI rising missing", "#define SWW_RC_MSI_EN   0x01030000u", "#define SWW_RC_MSI_EN   0x01010000u"),
                      ("completion timeout missing", "#define SWW_RC_MSI_EN   0x01030000u", "#define SWW_RC_MSI_EN   0x00030000u"),
                      ("IRQ0 enable at 0x14", "#define SWW_RC_IRQ0_EN  0x010u", "#define SWW_RC_IRQ0_EN  0x014u"), ("IRQ1 enable at 0x10", "#define SWW_RC_IRQ1_EN  0x014u", "#define SWW_RC_IRQ1_EN  0x010u"),
                      ("IRQ2 enable at 0x14", "#define SWW_RC_IRQ2_EN  0x018u", "#define SWW_RC_IRQ2_EN  0x014u"), ("IRQ status at 0x10", "#define SWW_RC_IRQ0     0x000u", "#define SWW_RC_IRQ0     0x010u")):
        out.append(("run E F2: " + lab, [(a, b)], None))
    out.append(_f("run E F2: IRQ0 status/enable swapped in the line", "swl_w16_go", "rc0, en0, rc1, en1, rc2, en2", "en0, rc0, rc1, en1, rc2, en2"))
    out.append(_f("run E F2: IRQ1 status/enable swapped in the line", "swl_w16_go", "rc0, en0, rc1, en1, rc2, en2", "rc0, en0, en1, rc1, rc2, en2"))
    out.append(_f("run E F2: IRQ2 status/enable swapped in the line", "swl_w16_go", "rc0, en0, rc1, en1, rc2, en2", "rc0, en0, rc1, en1, en2, rc2"))
    out.append(_f("run E F2: IRQ1 status read from the IRQ0 enable", "swl_w16_go", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0 + 4u, rc1);", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0_EN, rc1);"))
    out.append(_f("run E F2: IRQ2 status read from the IRQ1 status", "swl_w16_go", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0 + 8u, rc2);", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0 + 4u, rc2);"))
    out.append(_f("run E F2: the gate is not before the window store", "swl_w16_go", "\tif ((en2 & SWW_RC_MSI_EN) != 0) {\n\t\treturn swl_stop(x, SWW_E_IRQ);\n\t}\n", ""))
    out.append(("run E reads: ELBI interrupt range at run 6", [("{ 5, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN },", "{ 6, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN },")], None))
    out.append(("run E reads: ELBI interrupt range at run 4", [("{ 5, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN },", "{ 4, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN },")], None))
    out.append(("run E reads: ELBI interrupt range wider", [("{ 5, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN },", "{ 5, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN + 4u },")], None))
    out.append(("run E reads: ELBI interrupt range narrower", [("{ 5, SWW_ELBI, 0, SWW_RC_IRQ0, SWW_RC_IRQ2_EN },", "{ 5, SWW_ELBI, 0, SWW_RC_IRQ0 + 4u, SWW_RC_IRQ2_EN },")], None))
    # error names
    out.append(("run E: stop name of the interrupt gate", [("\"interrupt unmasked\"", "\"unmasked\"")], None))
    out.append(("run E: stop name of the poll timeout", [("\"firmware did not come up\"", "\"timeout\"")], None))
    out.append(("run E: stop name of the shared address", [("\"shared address\", \"halt not confirmed\"", "\"range\", \"halt not confirmed\"")], None))
    out.append(("run E: stop name of the halt", [("\"halt not confirmed\"", "\"halt\"")], None))
    return out


MUTANTS += _run_e_mutants()


# ---- patch 0134 (run F1): the read-only dumps between the poll and the re-halt. Built from the final header, so a pattern that no longer matches fails at import (AP-80). ----
def _d(label, name, old, new, equiv=None):
    """A mutant of one #define: its value `old` becomes `new` (the line must carry `old` as a token)."""
    m = re.search(r"^#define %s\s+.*$" % re.escape(name), final_header(), re.M)
    if not m or old not in m.group(0):
        raise ValueError("%s: %r is not in the definition of %s" % (label, old, name))
    return (label, [(m.group(0), m.group(0).replace(old, new, 1))], equiv)


def _run_f1_mutants():
    out = []
    # constants: each is checked against a layout restated in the host test (f1_layout_check) or by the dump it feeds
    out.append(_d("run F1: level 7 (F1 below the level that names it)", "SWW_F1_LEVEL", "SWW_LEVEL0 + 6", "SWW_LEVEL0 + 5"))
    out.append(_d("run F1: level 9 (F1 never runs at level 8)", "SWW_F1_LEVEL", "SWW_LEVEL0 + 6", "SWW_LEVEL0 + 7"))
    out.append(_d("run F1: ASSERT flag bit", "SWW_SH_ASSERT", "0x200u", "0x100u"))
    out.append(_d("run F1: TRAP flag bit", "SWW_SH_TRAP", "0x400u", "0x800u"))
    for n, o, v in (("SWW_SH_WORDS", "35u", "34u"), ("SWW_SH_WORDS", "35u", "36u"), ("SWW_SH_CONSOLE", "5u", "4u"), ("SWW_SH_CONSOLE", "5u", "6u"), ("SWW_SH_H2D_MB", "10u", "9u"), ("SWW_SH_H2D_MB", "10u", "11u"),
                    ("SWW_SH_D2H_MB", "11u", "10u"), ("SWW_SH_D2H_MB", "11u", "12u"), ("SWW_SH_RINGINFO", "12u", "11u"), ("SWW_SH_RINGINFO", "12u", "13u"), ("SWW_RI_WORDS", "19u", "18u"), ("SWW_RI_WORDS", "19u", "20u"),
                    ("SWW_RM_N", "5u", "4u"), ("SWW_RM_N", "5u", "6u"), ("SWW_CON_BUF", "8u", "4u"), ("SWW_CON_BUF", "8u", "12u"), ("SWW_CON_IDX", "16u", "12u"), ("SWW_CON_IDX", "16u", "20u"),
                    ("SWW_CON_MAX", "4096u", "4092u"), ("SWW_CON_MAX", "4096u", "4100u"), ("SWW_CON_LINE", "120u", "119u"), ("SWW_CON_LINE", "120u", "121u"), ("SWW_CON_LINES", "128u", "127u"), ("SWW_CON_LINES", "128u", "129u"),
                    ("SWW_PCI_FUNCINT", "0xC10u", "0xC14u"), ("SWW_PCI_PWRINT", "0xC18u", "0xC1Cu"), ("SWW_PCI_PWRINT", "0xC18u", "0xC14u")):
        out.append(_d("run F1: %s %s -> %s" % (n, o, v), n, o, v))
    # the read table
    row = "{ 6, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT, SWW_PCI_PWRINT },"
    out.append(("run F1 reads: PCIe core range at run 5", [(row, "{ 5, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT, SWW_PCI_PWRINT },")], None))
    out.append(("run F1 reads: PCIe core range at run 7", [(row, "{ 7, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT, SWW_PCI_PWRINT },")], None))
    out.append(("run F1 reads: PCIe core range wider", [(row, "{ 6, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT, SWW_PCI_PWRINT + 4u },")], None))
    out.append(("run F1 reads: PCIe core range narrower", [(row, "{ 6, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT + 4u, SWW_PCI_PWRINT },")], None))
    out.append(("run F1 reads: PCIe core range starts lower", [(row, "{ 6, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT - 4u, SWW_PCI_PWRINT },")], None))
    out.append(("run F1 reads: PCIe core range in another window", [(row, "{ 6, SWW_BAR0, SWW_WIN_SYSMEM, SWW_PCI_FUNCINT, SWW_PCI_PWRINT },")], None))
    # the span rule
    sp = "swl_f1_span_ok"
    out.append(_f("run F1 span: the window limit ignored", "swl_f1_lim", "return sw + SWW_TCM_BYTES < end ? sw + SWW_TCM_BYTES : end;", "return end;"))
    out.append(_f("run F1 span: the RAM end ignored", "swl_f1_lim", "return sw + SWW_TCM_BYTES < end ? sw + SWW_TCM_BYTES : end;", "return sw + SWW_TCM_BYTES;"))
    out.append(_f("run F1 span: the limit is the larger", "swl_f1_lim", "sw + SWW_TCM_BYTES < end ?", "sw + SWW_TCM_BYTES > end ?"))
    out.append(_f("run F1 span: empty range accepted", sp, "bytes != 0 && ", ""))
    out.append(_f("run F1 span: unaligned address accepted", sp, "(addr & 3u) == 0 && ", ""))
    out.append(_f("run F1 span: unaligned length accepted", sp, "(bytes & 3u) == 0 && ", ""))
    out.append(_f("run F1 span: address below the RAM accepted", sp, "addr >= SWW_RAM_BASE && ", ""))
    out.append(_f("run F1 span: address below the window accepted", sp, "addr >= sw && ", ""))
    out.append(_f("run F1 span: the end itself accepted", sp, "addr < lim && ", "addr <= lim && ",
                  "implied: an address at lim leaves lim - addr = 0 bytes, and bytes != 0 together with bytes <= lim - addr refuses it (the host test pins the end address in its span table)"))
    out.append(_f("run F1 span: a range past the end accepted", sp, "bytes <= lim - addr", "bytes <= lim - addr + 4u"))
    out.append(_f("run F1 span: a range one word short of the end refused", sp, "bytes <= lim - addr", "bytes < lim - addr"))
    out.append(_f("run F1 span: length not compared", sp, " && bytes <= lim - addr", ""))
    # the block reader
    bl = "swl_f1_block"
    out.append(_f("run F1 block: level not checked", bl, "x->level < SWW_F1_LEVEL", "0"))
    out.append(_f("run F1 block: level checked one too low", bl, "x->level < SWW_F1_LEVEL", "x->level < SWW_F1_LEVEL - 1"))
    out.append(_f("run F1 block: span not checked", bl, "|| !swl_f1_span_ok(SWW_RAM_BASE + x->ram_bytes, sw, addr, 4u * n)", ""))
    out.append(_f("run F1 block: span checked against another window", bl, "swl_f1_span_ok(SWW_RAM_BASE + x->ram_bytes, sw, addr, 4u * n)", "swl_f1_span_ok(SWW_RAM_BASE + x->ram_bytes, 0, addr, 4u * n)"))
    out.append(_f("run F1 block: span checked against another RAM end", bl, "swl_f1_span_ok(SWW_RAM_BASE + x->ram_bytes, sw, addr, 4u * n)", "swl_f1_span_ok(SWW_RAM_BASE + SWW_RAM_MAX, sw, addr, 4u * n)"))
    out.append(_f("run F1 block: span checked for one word", bl, "addr, 4u * n)", "addr, 4u)"))
    out.append(_f("run F1 block: more than 1024 words accepted", bl, "n == 0 || n > 1024u || ", "n == 0 || "))
    out.append(_f("run F1 block: zero words accepted", bl, "n == 0 || n > 1024u || ", "n > 1024u || ",
                  "implied: swl_f1_span_ok refuses bytes == 0 (killed above as 'run F1 span: empty range accepted'), so the block reader returns 1 either way"))
    out.append(_f("run F1 block: the word read is the one before", bl, "addr + 4u * i - sw, v", "addr + 4u * i - sw - 4u, v"))
    out.append(_f("run F1 block: the window of the read is another", bl, "SWW_RD(x, SWW_BAR2, sw, addr + 4u * i - sw, v);", "SWW_RD(x, SWW_BAR2, 0, addr + 4u * i, v);"))
    out.append(_f("run F1 block: words not handed to the caller", bl, "out[i] = v;", "out[i] = 0;"))
    out.append(_f("run F1 block: an all-ones block is not a dead link", bl, "if (n >= 2u && ones == n) {", "if (0) {"))
    out.append(_f("run F1 block (review 0134 F9, M19): a 2 word all-ones block is not a dead link", bl, "n >= 2u && ones == n", "n >= 3u && ones == n"))
    out.append(_f("run F1 block: one all-ones word is a dead link", bl, "n >= 2u && ones == n", "n >= 1u && ones == n"))
    out.append(_f("run F1 block: any all-ones word is a dead link", bl, "n >= 2u && ones == n", "ones != 0"))
    out.append(_f("run F1 block: one all-ones word in the block is a dead link", bl, "n >= 2u && ones == n", "n >= 2u && ones >= 1u"))
    out.append(_f("run F1 block: all-ones words not counted", bl, "ones += v == 0xFFFFFFFFu;", ""))
    out.append(_f("run F1 block: the dead block is not a stop", bl, "return swl_stop(x, SWW_E_DEAD);", "return 0;"))
    out.append(_f("run F1 block: the dead block stops with another name", bl, "return swl_stop(x, SWW_E_DEAD);", "return swl_stop(x, SWW_E_RD);"))
    out.append(_f("run F1 block: a refusal is read as done", bl, "\t\treturn 1;\n\t}\n\tfor", "\t\treturn 0;\n\t}\n\tfor"))
    # the printing
    out.append(_f("run F1 row: hex digits from the wrong end", "swl_f1_row", "(28u - 4u * k)", "(4u * k)"))
    out.append(_f("run F1 row: more than 4 words", "swl_f1_row", "i < n && i < 4u", "i < n"))
    out.append(_f("run F1 row: fewer words", "swl_f1_row", "i < n && i < 4u", "i < n && i < 3u"))
    out.append(_f("run F1 dump: rows of 3", "swl_f1_dump", "i += 4u", "i += 3u"))
    out.append(_f("run F1 dump: the last row too long", "swl_f1_dump", "n - i < 4u ? n - i : 4u", "4u"))
    out.append(_f("run F1 dump: offsets in words", "swl_f1_dump", "swl_f1_row(tag, 4u * i, w + i,", "swl_f1_row(tag, i, w + i,"))
    out.append(_f("run F1 put: LF does not end the line", "swl_f1_put", "if (c == '\\n') {", "if (c == '\\r') {"))
    out.append(_f("run F1 put: CR printed", "swl_f1_put", "c == 0 || c == '\\r'", "c == 0"))
    out.append(_f("run F1 put: NUL printed", "swl_f1_put", "c == 0 || c == '\\r'", "c == '\\r'"))
    out.append(_f("run F1 put: no wrap", "swl_f1_put", "if (l->n == SWW_CON_LINE) {", "if (0) {"))
    out.append(_f("run F1 put: wraps one early", "swl_f1_put", "if (l->n == SWW_CON_LINE) {", "if (l->n == SWW_CON_LINE - 1u) {"))
    out.append(_f("run F1 put: non-printable shown raw", "swl_f1_put", "c >= 0x20u && c < 0x7Fu ? (char)c : '.'", "(char)c"))
    out.append(_f("run F1 put: DEL shown raw", "swl_f1_put", "c < 0x7Fu", "c <= 0x7Fu"))
    out.append(_f("run F1 put: space shown as a dot", "swl_f1_put", "c >= 0x20u &&", "c > 0x20u &&"))
    out.append(_f("run F1 flush: empty lines printed", "swl_f1_flush", "if (l->n != 0) {", "if (1) {"))
    out.append(_f("run F1 flush: lines not counted", "swl_f1_flush", "l->lines++;", ""))
    out.append(_f("run F1 flush: no bound on the printed lines", "swl_f1_flush", "if (l->lines < SWW_CON_LINES) {", "if (1) {"))
    out.append(_f("run F1 flush: dropped lines not counted", "swl_f1_flush", "l->dropped++;", ""))
    out.append(_f("run F1 flush: a dropped line is still printed", "swl_f1_flush", "} else {\n\t\t\tl->dropped++;", "} else {\n\t\t\tprintf(\"SWEET WLAN: F1 con: %s\\n\", l->b);\n\t\t\tl->dropped++;"))
    out.append(_f("run F1 console: the summary counts printed lines only", "swl_f1_console", "ln.lines + ln.dropped, ln.dropped", "ln.lines, ln.dropped"))
    out.append(_f("run F1 flush: line not terminated", "swl_f1_flush", "l->b[l->n] = 0;", ""))
    # the console
    cn = "swl_f1_console"
    out.append(_f("run F1 console: structure pointer not checked", cn, "if (!swl_f1_span_ok(end, sw, cons, SWW_CON_IDX + 4u)) {", "if (0) {"))
    out.append(_f("run F1 console: structure checked for 4 bytes", cn, "cons, SWW_CON_IDX + 4u)", "cons, 4u)"))
    out.append(_f("run F1 console: structure checked for 16 bytes", cn, "cons, SWW_CON_IDX + 4u)", "cons, SWW_CON_IDX)"))
    out.append(_f("run F1 console: buffer fields from another offset", cn, "cons + SWW_CON_BUF, 3, h", "cons + SWW_CON_BUF + 4u, 3, h"))
    out.append(_f("run F1 console: size and index swapped", cn, "size = h[1];\n\tidx = h[2];", "size = h[2];\n\tidx = h[1];"))
    out.append(_f("run F1 console: zero size accepted", cn, "size == 0 || ", "",
                  "implied: swl_f1_span_ok refuses a range of 0 bytes (killed as 'run F1 span: empty range accepted'), so the console is refused either way and idx % size is never reached"))
    out.append(_f("run F1 console: unaligned size accepted", cn, "(size & 3u) != 0 || ", "",
                  "implied: swl_f1_span_ok refuses a length that is not a multiple of 4 (killed as 'run F1 span: unaligned length accepted')"))
    out.append(_f("run F1 console: buffer not checked", cn, "|| !swl_f1_span_ok(end, sw, buf, size)", ""))
    out.append(_f("run F1 console: buffer checked for 4 bytes", cn, "buf, size)", "buf, 4u)"))
    out.append(_f("run F1 console: no bound on the text", cn, "len = size < SWW_CON_MAX ? size : SWW_CON_MAX;", "len = size;"))
    out.append(_f("run F1 console: the first bytes of the buffer, not the last before the index", cn, "p = (idx % size + size - len) % size;", "p = 0;"))
    out.append(_f("run F1 console: starts at the index", cn, "p = (idx % size + size - len) % size;", "p = idx % size;"))
    out.append(_f("run F1 console: the index not wrapped", cn, "p = (idx % size + size - len) % size;", "p = (idx + size - len) % size;",
                  "equivalent: the refusal of idx > size (review 0134 F5) and size <= 4 MiB (span rule) keep idx + size - len below 2^32, and (idx % size + size - len) % size == (idx + size - len) % size without an overflow"))
    out.append(_f("run F1 console (review 0134 F5): index beyond the size accepted", cn, "if (idx > size) {", "if (0) {"))
    out.append(_f("run F1 console (review 0134 F5): index equal to the size refused", cn, "if (idx > size) {", "if (idx >= size) {"))
    out.append(_f("run F1 console (review 0134 F5): index refused only far beyond the size", cn, "if (idx > size) {", "if (idx > size + 4u) {"))
    out.append(_f("run F1 console (review 0134 F5): index beyond the size said but read", cn, "(DHD refuses it), not read\\n\", idx, size);\n\t\treturn 0;", "(DHD refuses it), not read\\n\", idx, size);"))
    out.append(_f("run F1 console (review 0134 F9, M1): header stop swallowed", cn, "rc = swl_f1_block(x, sw, cons + SWW_CON_BUF, 3, h);\n\tif (rc != 0) {\n\t\treturn rc < 0 ? rc : 0;", "rc = swl_f1_block(x, sw, cons + SWW_CON_BUF, 3, h);\n\tif (rc != 0) {\n\t\treturn 0;"))
    out.append(_f("run F1 console: window one byte early", cn, "p = (idx % size + size - len) % size;", "p = (idx % size + size - len + size - 1u) % size;"))
    out.append(_f("run F1 console: chunk over the end of the buffer", cn, "c = size / 4u - wi;\n\t\tc = c < need ? c : need;", "c = need;"))
    out.append(_f("run F1 console: chunk of more than 16 words", cn, "c = c < 16u ? c : 16u;", ""))
    out.append(_f("run F1 console: chunk of 17 words", cn, "c = c < 16u ? c : 16u;", "c = c < 17u ? c : 17u;"))
    out.append(_f("run F1 console: chunk past the bytes left", cn, "c = c < need ? c : need;", ""))
    out.append(_f("run F1 console: the first word read from its start", cn, "for (j = k == 0 ? b : 0; j < 4u && left != 0; j++) {", "for (j = 0; j < 4u && left != 0; j++) {"))
    out.append(_f("run F1 console: bytes in big endian order", cn, "(cw[k] >> (8u * j)) & 0xFFu", "(cw[k] >> (24u - 8u * j)) & 0xFFu"))
    out.append(_f("run F1 console: the position does not wrap at the end", cn, "if (p >= size) {\n\t\t\tp -= size;\n\t\t}", ""))
    out.append(_f("run F1 console: bytes left not counted", cn, "\t\t\t\tleft--;\n", ""))
    out.append(_f("run F1 console: stop of the block swallowed", cn, "return rc < 0 ? rc : 0;\n\t\t}\n\t\tfor", "return 0;\n\t\t}\n\t\tfor"))
    out.append(_f("run F1 console: not recorded", cn, "x->f1 |= SWW_F1_CON;", ""))
    out.append(_f("run F1 console: line buffer not flushed at the end", cn, "\tswl_f1_flush(&ln);\n\tprintf(", "\tprintf("))
    # the host controller state
    ho = "swl_f1_host"
    out.append(_f("run F1 host: IRQ0 enable not a stop", ho, "(en0 | en1 | en2) != 0", "(en1 | en2) != 0"))
    out.append(_f("run F1 host: IRQ1 enable not a stop", ho, "(en0 | en1 | en2) != 0", "(en0 | en2) != 0"))
    out.append(_f("run F1 host: IRQ2 enable not a stop", ho, "(en0 | en1 | en2) != 0", "(en0 | en1) != 0"))
    out.append(_f("run F1 host: only the INTx enables are a stop", ho, "(en0 | en1 | en2) != 0", "(en0 & SWW_RC_INTX_EN) != 0"))
    out.append(_f("run F1 host: the IRQ gate is not a stop", ho, "\t\treturn swl_stop(x, SWW_E_IRQ);", "\t\treturn 0;"))
    out.append(_f("run F1 host: IRQ1 status read from IRQ0", ho, "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0 + 4u, rc1);", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0, rc1);"))
    out.append(_f("run F1 host: IRQ1 enable read from IRQ0", ho, "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ1_EN, en1);", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0_EN, en1);"))
    out.append(_f("run F1 host: IRQ2 enable read from IRQ0", ho, "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ2_EN, en2);", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0_EN, en2);"))
    out.append(_f("run F1 host: IRQ2 status read from IRQ1", ho, "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0 + 8u, rc2);", "SWW_RD(x, SWW_ELBI, 0, SWW_RC_IRQ0 + 4u, rc2);"))
    out.append(_f("run F1 host: EP Command read from another register", ho, "SWW_RD(x, SWW_CFG, 0, 0x04u, cmd);", "SWW_RD(x, SWW_CFG, 0, 0x10u, cmd);"))
    out.append(_f("run F1 host: ELBI not recorded", ho, "x->f1 |= SWW_F1_ELBI;", ""))
    out.append(_f("run F1 host: cfg not recorded", ho, "x->f1 |= SWW_F1_CFG;", ""))
    # the PCIe core group
    pc = "swl_f1_pcie"
    out.append(_f("run F1 pcie: no window select", pc, "SWW_TRY(swl_win(x, SWW_WIN_PCIE));", ""))
    back = "\tSWW_TRY(swl_win(x, SWW_WIN_CA7WRAP));\n"
    out.append(_f("run F1 pcie: window not selected back", pc, back, ""))
    out.append(_f("run F1 pcie: window selected back to another", pc, back, "\tSWW_TRY(swl_win(x, SWW_WIN_SYSMEM));\n"))
    out.append(_f("run F1 pcie: window select back not a stop", pc, back, "\t(void)swl_win(x, SWW_WIN_CA7WRAP);\n"))
    out.append(_fx("run F1 pcie: window select back after the value checks (review 0134 F1)", [(pc, back, ""), (pc, "\tx->f1 |= SWW_F1_PCI;\n\treturn 0;", "\tx->f1 |= SWW_F1_PCI;\n\treturn swl_win(x, SWW_WIN_CA7WRAP);")]))
    for r in ("fi", "fm", "pi", "mint", "mask", "pw"):
        out.append(_f("run F1 pcie: %s all ones is not a dead link" % r, pc, "%s == 0xFFFFFFFFu" % r, "0"))
    out.append(_f("run F1 pcie: dead link not a stop", pc, "\t\treturn swl_stop(x, SWW_E_DEAD);", "\t\treturn 0;"))
    out.append(_f("run F1 pcie: a mailbox mask is not a stop", pc, "if (mask != 0) {", "if (0) {"))
    out.append(_f("run F1 pcie: a mailbox mask stops with another name", pc, "return swl_stop(x, SWW_E_IRQ);", "return swl_stop(x, SWW_E_STATE);"))
    out.append(_f("run F1 pcie: the mailbox int is the mask", pc, "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_MBMASK, mask);", "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_MBINT, mask);"))
    out.append(_f("run F1 pcie: funcmask read from funcint", pc, "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT + 4u, fm);", "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_PCI_FUNCINT, fm);"))
    out.append(_f("run F1 pcie: SRPWR read from the mask", pc, "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_SRPWR, pw);", "SWW_RD(x, SWW_BAR0, SWW_WIN_PCIE, SWW_MBMASK, pw);"))
    out.append(_f("run F1 pcie: not recorded", pc, "x->f1 |= SWW_F1_PCI;", ""))
    # F1 proper
    f1 = "swl_f1"
    out.append(_f("run F1: flags checked for ASSERT only", f1, "(fl & (SWW_SH_ASSERT | SWW_SH_TRAP)) != 0", "(fl & SWW_SH_ASSERT) != 0"))
    out.append(_f("run F1: flags checked for TRAP only", f1, "(fl & (SWW_SH_ASSERT | SWW_SH_TRAP)) != 0", "(fl & SWW_SH_TRAP) != 0"))
    out.append(_f("run F1: flags before the dumps not checked", f1, "if ((fl & (SWW_SH_ASSERT | SWW_SH_TRAP)) != 0) {\n\t\tprintf(\"SWEET WLAN: F1 firmware flags", "if (0) {\n\t\tprintf(\"SWEET WLAN: F1 firmware flags"))
    out.append(_f("run F1: flags before the dumps are not a stop", f1, "report assert/trap\\n\", fl);\n\t\treturn swl_stop(x, SWW_E_FWFAIL);", "report assert/trap\\n\", fl);"))
    out.append(_f("run F1: flags after the dumps checked for ASSERT only", f1, "if ((fl2 & (SWW_SH_ASSERT | SWW_SH_TRAP)) != 0) {", "if ((fl2 & SWW_SH_ASSERT) != 0) {"))
    out.append(_f("run F1: flags after the dumps checked for TRAP only", f1, "if ((fl2 & (SWW_SH_ASSERT | SWW_SH_TRAP)) != 0) {", "if ((fl2 & SWW_SH_TRAP) != 0) {"))
    out.append(_f("run F1: flags after the dumps not checked", f1, "if ((fl2 & (SWW_SH_ASSERT | SWW_SH_TRAP)) != 0) {", "if (0) {"))
    out.append(_f("run F1: flags after the dumps stop with another name", f1, "SWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);\n\tif (fl2 == 0xFFFFFFFFu) {\n\t\treturn swl_stop(x, SWW_E_DEAD);", "SWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);\n\tif (fl2 == 0xFFFFFFFFu) {\n\t\treturn swl_stop(x, SWW_E_RD);"))
    out.append(_f("run F1: all-ones flags after the dumps are not a dead link", f1, "if (fl2 == 0xFFFFFFFFu) {", "if (0) {"))
    out.append(_f("run F1: flags re-read from another word", f1, "SWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);", "SWW_RD(x, SWW_BAR2, sw, sh + 4u - sw, fl2);"))
    out.append(_f("run F1: host state not read", f1, "SWW_TRY(swl_f1_host(x));", ""))
    out.append(_f("run F1: precondition after the host state not checked", f1, "SWW_TRY(swl_f1_host(x));\n\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}", "SWW_TRY(swl_f1_host(x));"))
    pre = "\tif (swl_wpre(x)) {"
    pre_ok = pre + "                      /* review 0134 F4: the firmware may have turned on bus mastering during the ~1100 reads */\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n"
    out.append(_f("run F1 (review 0134 F4): precondition before the console not checked", f1, pre_ok, ""))
    out.append(_f("run F1 (review 0134 F4): precondition before the console ignored", f1, pre_ok, "\t(void)swl_wpre(x);\n"))
    pre_last = "\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n\tSWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);"
    out.append(_f("run F1 (review 0134 F4): precondition before the last flags read not checked", f1, pre_last, "\tSWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);"))
    out.append(_f("run F1 (review 0134 F4): precondition before the last flags read ignored", f1, pre_last, "\t(void)swl_wpre(x);\n\tSWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);"))
    out.append(_f("run F1: console not read", f1, "SWW_TRY(swl_f1_console(x, sw, sd[SWW_SH_CONSOLE]));", ""))
    out.append(_f("run F1: console pointer from another word", f1, "sd[SWW_SH_CONSOLE]", "sd[SWW_SH_CONSOLE + 1u]"))
    out.append(_f("run F1: the console stop swallowed", f1, "SWW_TRY(swl_f1_console(x, sw, sd[SWW_SH_CONSOLE]));", "(void)swl_f1_console(x, sw, sd[SWW_SH_CONSOLE]);"))
    out.append(_f("run F1: shared words clamped to the RAM only", f1, "n = sh < lim ? (lim - sh) / 4u : 0;", "n = sh < SWW_RAM_BASE + x->ram_bytes ? (SWW_RAM_BASE + x->ram_bytes - sh) / 4u : 0;"))
    out.append(_f("run F1: shared words not clamped", f1, "n = n < SWW_SH_WORDS ? n : SWW_SH_WORDS;", "n = SWW_SH_WORDS;"))
    out.append(_f("run F1: shared words clamped to 34", f1, "n = n < SWW_SH_WORDS ? n : SWW_SH_WORDS;", "n = n < SWW_SH_WORDS - 1u ? n : SWW_SH_WORDS - 1u;"))
    out.append(_f("run F1: shared dump stop swallowed", f1, "rc = swl_f1_block(x, sw, sh, n, sd);\n\tif (rc < 0) {\n\t\treturn rc;\n\t}", "rc = swl_f1_block(x, sw, sh, n, sd);"))
    out.append(_f("run F1: shared dump not recorded", f1, "x->f1 |= SWW_F1_SH;", ""))
    out.append(_f("run F1: shared dump printed from the second word", f1, "swl_f1_dump(\"shared\", sd, n);", "swl_f1_dump(\"shared\", sd + 1, n - 1u);"))
    out.append(_f("run F1: h2d mailbox pointer from the d2h word", f1, "swl_f1_block(x, sw, sd[SWW_SH_H2D_MB], 1, &mb[0])", "swl_f1_block(x, sw, sd[SWW_SH_D2H_MB], 1, &mb[0])"))
    out.append(_f("run F1: d2h mailbox pointer from the h2d word", f1, "swl_f1_block(x, sw, sd[SWW_SH_D2H_MB], 1, &mb[1])", "swl_f1_block(x, sw, sd[SWW_SH_H2D_MB], 1, &mb[1])"))
    out.append(_f("run F1: the d2h mailbox word is read after a refusal of the h2d pointer", f1, "ok1 = ok0 != 0 ? ok0 : swl_f1_block(", "ok1 = ok0 < 0 ? ok0 : swl_f1_block("))
    out.append(_f("run F1: the d2h mailbox word is read whatever the h2d read did", f1, "ok1 = ok0 != 0 ? ok0 : swl_f1_block(", "ok1 = swl_f1_block("))
    out.append(_f("run F1: a stopped mailbox read is not returned", f1, "if (ok0 < 0 || ok1 < 0) {\n\t\treturn ok0 < 0 ? ok0 : ok1;\n\t}", ""))
    out.append(_f("run F1: only the first mailbox word counts", f1, "if (ok0 == 0 && ok1 == 0) {", "if (ok0 == 0) {"))
    out.append(_f("run F1: only the second mailbox word counts", f1, "if (ok0 == 0 && ok1 == 0) {", "if (ok1 == 0) {",
                  "implied: ok1 is ok0 whenever ok0 != 0 (the second word is not read after a refusal or a stop of the first), so ok1 == 0 already means ok0 == 0"))
    out.append(_f("run F1: mailbox not recorded", f1, "x->f1 |= SWW_F1_MB;", ""))
    out.append(_f("run F1: ring_info pointer from another word", f1, "sd[SWW_SH_RINGINFO], SWW_RI_WORDS, ri", "sd[SWW_SH_RINGINFO + 1u], SWW_RI_WORDS, ri"))
    out.append(_f("run F1: ring_info of 18 words", f1, "sd[SWW_SH_RINGINFO], SWW_RI_WORDS, ri", "sd[SWW_SH_RINGINFO], SWW_RI_WORDS - 1u, ri"))
    out.append(_f("run F1: ring_info stop swallowed", f1, "rc = swl_f1_block(x, sw, sd[SWW_SH_RINGINFO], SWW_RI_WORDS, ri);\n\tif (rc < 0) {\n\t\treturn rc;\n\t}", "rc = swl_f1_block(x, sw, sd[SWW_SH_RINGINFO], SWW_RI_WORDS, ri);"))
    out.append(_f("run F1: ring_info not recorded", f1, "x->f1 |= SWW_F1_RI;", ""))
    out.append(_f("run F1: ring_mem pointer from the second word", f1, "swl_f1_block(x, sw, ri[0], SWW_RM_WORDS, rm)", "swl_f1_block(x, sw, ri[1], SWW_RM_WORDS, rm)"))
    out.append(_f("run F1: ring_mem of 16 words", f1, "swl_f1_block(x, sw, ri[0], SWW_RM_WORDS, rm)", "swl_f1_block(x, sw, ri[0], SWW_RM_WORDS - 4u, rm)"))
    out.append(_f("run F1: ring_mem stop swallowed", f1, "rc = swl_f1_block(x, sw, ri[0], SWW_RM_WORDS, rm);\n\t\tif (rc < 0) {\n\t\t\treturn rc;\n\t\t}", "rc = swl_f1_block(x, sw, ri[0], SWW_RM_WORDS, rm);"))
    out.append(_f("run F1: ring_mem not recorded", f1, "x->f1 |= SWW_F1_RM;", ""))
    out.append(_f("run F1: ring_mem read although ring_info was refused", f1, "x->f1 |= SWW_F1_RI;\n\t\trc =", "x->f1 |= SWW_F1_RI;\n\t\tri[0] = sd[SWW_SH_RINGINFO];\n\t\trc ="))
    out.append(_f("run F1: ring_mem base halves swapped", f1, "rm[4u * i + 3u], rm[4u * i + 2u]);", "rm[4u * i + 2u], rm[4u * i + 3u]);"))
    out.append(_f("run F1: ring_mem max_item from the length word", f1, "rm[4u * i + 1u] & 0xFFFFu, rm[4u * i + 1u] >> 16", "rm[4u * i + 1u] >> 16, rm[4u * i + 1u] & 0xFFFFu"))
    out.append(_f("run F1: ring_mem type from the index word", f1, "(rm[4u * i] >> 16) & 0xFFu", "(rm[4u * i] >> 8) & 0xFFu"))
    out.append(_f("run F1: ring_mem index not masked", f1, "rm[4u * i] & 0xFFFFu, (rm[4u * i] >> 16)", "rm[4u * i], (rm[4u * i] >> 16)"))
    out.append(_f("run F1: 4 ring_mem lines", f1, "for (i = 0; i < SWW_RM_N; i++) {", "for (i = 0; i + 1u < SWW_RM_N; i++) {"))
    # the hook in W17
    w17 = "swl_w17"
    out.append(_f("run F1: W17 runs F1 at every level", w17, "if (x->level >= SWW_F1_LEVEL) {", "if (1) {"))
    out.append(_f("run F1: W17 never runs F1", w17, "if (x->level >= SWW_F1_LEVEL) {", "if (0) {"))
    out.append(_f("run F1: W17 runs F1 one level early", w17, "if (x->level >= SWW_F1_LEVEL) {", "if (x->level >= SWW_F1_LEVEL - 1) {"))
    out.append(_f("run F1: W17 ignores the stop of F1", w17, "SWW_TRY(swl_f1(x, sh, sw, fl));", "(void)swl_f1(x, sh, sw, fl);"))
    f1blk = "\tif (x->level >= SWW_F1_LEVEL) {         /* run F1 (patch 0134): read-only dumps of the running firmware, then the same stop action */\n\t\tSWW_TRY(swl_f1(x, sh, sw, fl));\n\t}\n"
    out.append(_fx("run F1: W17 runs F1 after the re-halt", [(w17, f1blk, ""), (w17, "\tswl_restore(x);\n\tx->halted = x->ca_ok;", "\tswl_restore(x);\n" + f1blk + "\tx->halted = x->ca_ok;")]))
    # the PCIe core group, run by W17 after the re-halt (review 0134 F1)
    hk = "if (x->level >= SWW_F1_LEVEL && x->restore == SWW_RS_OK) {"
    out.append(_f("run F1 pcie hook: runs at every level", w17, hk, "if (x->restore == SWW_RS_OK) {"))
    out.append(_f("run F1 pcie hook: never runs", w17, hk, "if (0) {"))
    out.append(_f("run F1 pcie hook: runs one level early", w17, hk, "if (x->level >= SWW_F1_LEVEL - 1 && x->restore == SWW_RS_OK) {"))
    out.append(_f("run F1 pcie hook: runs although the restore did not end ok", w17, hk, "if (x->level >= SWW_F1_LEVEL) {"))
    out.append(_f("run F1 pcie hook: stop swallowed", w17, "\t\tif (rc) {\n\t\t\tx->restore = rs;        /* the stop's own swl_restore found nothing armed and wrote SWW_RS_NONE; the CA7 restore above held */\n\t\t\treturn rc;\n\t\t}\n", ""))
    out.append(_f("run F1 pcie hook: restore result not kept", w17, "\t\t\tx->restore = rs;        /* the stop's own swl_restore found nothing armed and wrote SWW_RS_NONE; the CA7 restore above held */\n", ""))
    out.append(_fx("run F1: the PCIe core group before the re-halt (review 0134 F1)",
                   [(w17, "\tif (x->level >= SWW_F1_LEVEL && x->restore == SWW_RS_OK) {   /* run F1 (review 0134 F1): the PCIe core group with the CA7 in reset */\n\t\tint rs = x->restore, rc = swl_f1_pcie(x);\n\n\t\tif (rc) {\n\t\t\tx->restore = rs;        /* the stop's own swl_restore found nothing armed and wrote SWW_RS_NONE; the CA7 restore above held */\n\t\t\treturn rc;\n\t\t}\n\t}\n", ""),
                    ("swl_f1", "\tSWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);", "\tSWW_TRY(swl_f1_pcie(x));\n\tSWW_RD(x, SWW_BAR2, sw, sh - sw, fl2);")]))
    out.append(_f("run F1: W17 hands F1 another window", w17, "swl_f1(x, sh, sw, fl)", "swl_f1(x, sh, win, fl)"))
    out.append(_f("run F1: W17 hands F1 the shared address of the poll word", w17, "swl_f1(x, sh, sw, fl)", "swl_f1(x, v, sw, fl)",
                  "equivalent: sh = v a few lines above (the poll word is the shared address once the loop ended on it)"))
    out.append(("run F1: stop name of the firmware failure", [("\"halt not confirmed\", \"firmware assert or trap\" };", "\"halt not confirmed\", \"trap\" };")], None))
    return out


MUTANTS += _run_f1_mutants()


# ---- patch 0135 (run F2): the dumps through BAR2 window 0 after the re-halt. Same rule as above: built from the final header, a pattern that no longer matches fails at import. ----
def _run_f2_mutants():
    out = []
    f2, f2d, w17, f1 = "swl_f2", "swl_f2_dump", "swl_w17", "swl_f1"       # patch 0136 moved the dumps from swl_f2 into swl_f2_dump (the guard stays in swl_f2)
    out.append(_d("run F2: level 8 (F2 runs at the level of F1)", "SWW_F2_LEVEL", "SWW_LEVEL0 + 7", "SWW_LEVEL0 + 6"))
    out.append(_d("run F2: level 10", "SWW_F2_LEVEL", "SWW_LEVEL0 + 7", "SWW_LEVEL0 + 8"))
    for n, o, v in (("SWW_F2_LOW", "0x01u", "0x02u"), ("SWW_F2_MB", "0x02u", "0x01u"), ("SWW_F2_RI", "0x04u", "0x02u"), ("SWW_F2_RM", "0x08u", "0x04u"), ("SWW_F2_CON", "0x10u", "0x08u")):
        out.append(_d("run F2: %s %s -> %s" % (n, o, v), n, o, v))
    # the entry of swl_f2
    out.append(_f("run F2: level not checked", f2, "if (x->level < SWW_F2_LEVEL) {", "if (0) {"))
    out.append(_f("run F2: level checked one too low", f2, "x->level < SWW_F2_LEVEL", "x->level < SWW_F2_LEVEL - 1"))
    pre1 = "\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n\tSWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);"
    out.append(_f("run F2: precondition at the entry not checked", f2, pre1, "\tSWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);"))
    out.append(_f("run F2: precondition at the entry ignored", f2, pre1, "\t(void)swl_wpre(x);\n\tSWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);"))
    out.append(_f("run F2: row not set", f2, "x->row = 17;", "x->row = 16;"))
    out.append(_f("run F2: the mask of an earlier run is kept", f2, "\tx->f2 = 0;\n", ""))
    # the guard
    out.append(_f("run F2 guard: cfg 0x84 read from cfg 0x80", f2, "SWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);", "SWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN, c84);"))
    out.append(_f("run F2 guard: cfg 0x84 not 0 accepted", f2, "if (c84 != 0) {", "if (0) {"))
    out.append(_f("run F2 guard: cfg 0x84 not 0 is a stop", f2, "not 0, not read\\n\", c84);\n\t\treturn 0;", "not 0, not read\\n\", c84);\n\t\treturn swl_stop(x, SWW_E_STATE);"))
    out.append(_f("run F2 guard: cfg 0x84 not 0 said but read", f2, "not 0, not read\\n\", c84);\n\t\treturn 0;", "not 0, not read\\n\", c84);"))
    out.append(_f("run F2 guard: RAM word read at the next address", f2, "SWW_RD(x, SWW_BAR2, 0, SWW_RAM_BASE, w0);", "SWW_RD(x, SWW_BAR2, 0, SWW_RAM_BASE + 4u, w0);"))
    out.append(_f("run F2 guard: RAM word read in the tail window", f2, "SWW_RD(x, SWW_BAR2, 0, SWW_RAM_BASE, w0);", "SWW_RD(x, SWW_BAR2, SWW_WIN_TAIL, SWW_RAM_BASE, w0);"))
    out.append(_f("run F2 guard: image word 1 instead of word 0", f2, "swl_img_word(x, 0)", "swl_img_word(x, 1)"))
    out.append(_f("run F2 guard: no image is not refused", f2, "x->img != NULL && x->img_len >= 4u ? swl_img_word(x, 0) : 0xFFFFFFFFu", "swl_img_word(x, 0)"))
    out.append(_f("run F2 guard: an image shorter than a word is not refused", f2, "x->img != NULL && x->img_len >= 4u ?", "x->img != NULL ?"))
    out.append(_f("run F2 guard: a RAM word that all ones is not a dead link", f2, "if (w0 == 0xFFFFFFFFu && want != 0xFFFFFFFFu) {", "if (0) {"))
    out.append(_f("run F2 guard: a dead link stops with another name", f2, "return swl_stop(x, SWW_E_DEAD);", "return swl_stop(x, SWW_E_RD);"))
    out.append(_f("run F2 guard: a changed RAM word accepted", f2, "if (w0 != want || want == 0xFFFFFFFFu) {", "if (want == 0xFFFFFFFFu) {"))
    out.append(_f("run F2 guard: an all-ones image word accepted", f2, "if (w0 != want || want == 0xFFFFFFFFu) {", "if (w0 != want) {"))
    out.append(_f("run F2 guard: a changed RAM word is a stop", f2, "RAM changed while the CA7 ran, not read\\n\");\n\t\treturn 0;", "RAM changed while the CA7 ran, not read\\n\");\n\t\treturn swl_stop(x, SWW_E_STATE);"))
    out.append(_f("run F2 guard: not recorded", f2, "x->f2 |= SWW_F2_LOW;", ""))
    # the mailbox words
    out.append(_f("run F2: every block read through the tail window", f2d, "swl_f1_block(x, 0,", "swl_f1_block(x, SWW_WIN_TAIL,"))
    out.append(_f("run F2 mailbox: host to dongle from the other slot", f2d, "swl_f1_block(x, 0, x->f2p[0], 1, &mb[0])", "swl_f1_block(x, 0, x->f2p[1], 1, &mb[0])"))
    out.append(_f("run F2 mailbox: dongle to host from the other slot", f2d, "swl_f1_block(x, 0, x->f2p[1], 1, &mb[1])", "swl_f1_block(x, 0, x->f2p[0], 1, &mb[1])"))
    out.append(_f("run F2 mailbox: the second word read although the first was refused", f2d, "ok1 = ok0 != 0 ? ok0 : swl_f1_block(x, 0, x->f2p[1], 1, &mb[1]);", "ok1 = swl_f1_block(x, 0, x->f2p[1], 1, &mb[1]);"))
    out.append(_f("run F2 mailbox: stop swallowed", f2d, "\tif (ok0 < 0 || ok1 < 0) {\n\t\treturn ok0 < 0 ? ok0 : ok1;\n\t}\n", ""))
    out.append(_f("run F2 mailbox: one refused pointer is enough", f2d, "if (ok0 == 0 && ok1 == 0) {", "if (ok0 == 0 || ok1 == 0) {"))
    out.append(_f("run F2 mailbox: not recorded", f2d, "x->f2 |= SWW_F2_MB;", ""))
    # ring_info and ring_mem
    out.append(_f("run F2 ring_info: read from the mailbox slot", f2d, "swl_f1_block(x, 0, x->f2p[2], SWW_RI_WORDS, ri)", "swl_f1_block(x, 0, x->f2p[1], SWW_RI_WORDS, ri)"))
    out.append(_f("run F2 ring_info: one word short", f2d, "swl_f1_block(x, 0, x->f2p[2], SWW_RI_WORDS, ri)", "swl_f1_block(x, 0, x->f2p[2], SWW_RI_WORDS - 1u, ri)"))
    out.append(_f("run F2 ring_info: stop swallowed", f2d, "rc = swl_f1_block(x, 0, x->f2p[2], SWW_RI_WORDS, ri);\n\tif (rc < 0) {\n\t\treturn rc;\n\t}\n", "rc = swl_f1_block(x, 0, x->f2p[2], SWW_RI_WORDS, ri);\n"))
    out.append(_f("run F2 ring_info: not recorded", f2d, "x->f2 |= SWW_F2_RI;", ""))
    out.append(_f("run F2 ring_info: printed from the second word", f2d, "swl_f1_dump(\"lo ring_info\", ri, SWW_RI_WORDS);", "swl_f1_dump(\"lo ring_info\", ri + 1, SWW_RI_WORDS - 1u);"))
    out.append(_f("run F2 ring_mem: read from the second word of ring_info", f2d, "swl_f1_block(x, 0, ri[0], SWW_RM_WORDS, rm)", "swl_f1_block(x, 0, ri[1], SWW_RM_WORDS, rm)"))
    out.append(_f("run F2 ring_mem: one entry short", f2d, "swl_f1_block(x, 0, ri[0], SWW_RM_WORDS, rm)", "swl_f1_block(x, 0, ri[0], SWW_RM_WORDS - 4u, rm)"))
    out.append(_f("run F2 ring_mem: stop swallowed", f2d, "rc = swl_f1_block(x, 0, ri[0], SWW_RM_WORDS, rm);\n\t\tif (rc < 0) {\n\t\t\treturn rc;\n\t\t}\n", "rc = swl_f1_block(x, 0, ri[0], SWW_RM_WORDS, rm);\n"))
    out.append(_f("run F2 ring_mem: base halves swapped", f2d, "rm[4u * i + 3u], rm[4u * i + 2u]);", "rm[4u * i + 2u], rm[4u * i + 3u]);"))
    out.append(_f("run F2 ring_mem: max_item from the length word", f2d, "rm[4u * i + 1u] & 0xFFFFu, rm[4u * i + 1u] >> 16", "rm[4u * i + 1u] >> 16, rm[4u * i + 1u] & 0xFFFFu"))
    out.append(_f("run F2 ring_mem: type from the index word", f2d, "(rm[4u * i] >> 16) & 0xFFu", "(rm[4u * i] >> 8) & 0xFFu"))
    out.append(_f("run F2 ring_mem: 4 lines", f2d, "for (i = 0; i < SWW_RM_N; i++) {", "for (i = 0; i + 1u < SWW_RM_N; i++) {"))
    out.append(_f("run F2 ring_mem: not recorded", f2d, "x->f2 |= SWW_F2_RM;", ""))
    out.append(_f("run F2 ring_mem: read although ring_info was refused", f2d, "x->f2 |= SWW_F2_RI;\n\t\trc =", "x->f2 |= SWW_F2_RI;\n\t\tri[0] = x->f2p[2];\n\t\trc ="))
    # the console
    pre2 = "\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n\tf1k = x->f1;"
    out.append(_f("run F2: precondition before the console not checked", f2d, pre2, "\tf1k = x->f1;"))
    out.append(_f("run F2: precondition before the console ignored", f2d, pre2, "\t(void)swl_wpre(x);\n\tf1k = x->f1;"))
    out.append(_f("run F2 console: another window", f2d, "swl_f1_console(x, 0, x->f2p[3])", "swl_f1_console(x, SWW_WIN_TAIL, x->f2p[3])"))
    out.append(_f("run F2 console: the ring_info pointer", f2d, "swl_f1_console(x, 0, x->f2p[3])", "swl_f1_console(x, 0, x->f2p[2])"))
    out.append(_f("run F2 console: F1's mask is not set apart", f2d, "\tx->f1 = 0;\n\trc = swl_f1_console", "\trc = swl_f1_console"))
    out.append(_f("run F2 console: F1's mask is not given back", f2d, "\tx->f1 = f1k;\n", ""))
    out.append(_f("run F2 console: F1's mask is not saved", f2d, "f1k = x->f1;", "f1k = 0;"))
    out.append(_f("run F2 console: always recorded", f2d, "if (x->f1 & SWW_F1_CON) {", "if (1) {"))
    out.append(_f("run F2 console: never recorded", f2d, "if (x->f1 & SWW_F1_CON) {", "if (0) {"))
    out.append(_f("run F2 console: stop swallowed", f2d, "\tif (rc < 0) {\n\t\treturn rc;\n\t}\n\tprintf(\"SWEET WLAN: F2 done", "\tprintf(\"SWEET WLAN: F2 done"))
    out.append(_f("run F2: done line without the mask", f2d, "F2 done mask %#x\\n\", x->f2);", "F2 done mask %#x\\n\", 0u);"))
    # F1 hands the pointers over
    for i, nm in ((0, "SWW_SH_H2D_MB"), (1, "SWW_SH_D2H_MB"), (2, "SWW_SH_RINGINFO"), (3, "SWW_SH_CONSOLE")):
        other = ("SWW_SH_D2H_MB", "SWW_SH_H2D_MB", "SWW_SH_CONSOLE", "SWW_SH_RINGINFO")[i]
        out.append(_f("run F2 pointer %d: taken from the other slot" % i, f1, "x->f2p[%d] = sd[%s];" % (i, nm), "x->f2p[%d] = sd[%s];" % (i, other)))
        out.append(_f("run F2 pointer %d: not handed over" % i, f1, "x->f2p[%d] = sd[%s];" % (i, nm), ""))
    out.append(_f("run F2 pointers: not cleared at the start of F1", f1, "\tx->f2p[0] = x->f2p[1] = x->f2p[2] = x->f2p[3] = 0;\n", ""))
    # the hook in W17
    hk = "if (x->level == SWW_F2_LEVEL && x->restore == SWW_RS_OK) {"      # patch 0136: level 9 only (at level 10 RAM reads all ones after the halt; swl_f3 dumps before it)
    out.append(_f("run F3: the F2 block also runs at level 10 (>=)", w17, hk, "if (x->level >= SWW_F2_LEVEL && x->restore == SWW_RS_OK) {"))
    out.append(_f("run F2 hook: runs at every level", w17, hk, "if (x->restore == SWW_RS_OK) {"))
    out.append(_f("run F2 hook: never runs", w17, hk, "if (0) {"))
    out.append(_f("run F2 hook: runs one level early", w17, hk, "if ((x->level == SWW_F2_LEVEL || x->level == SWW_F2_LEVEL - 1) && x->restore == SWW_RS_OK) {"))
    out.append(_f("run F2 hook: runs although the restore did not end ok", w17, hk, "if (x->level == SWW_F2_LEVEL) {"))
    out.append(_f("run F2 hook: runs after a skipped restore too", w17, hk, "if (x->level == SWW_F2_LEVEL && x->restore != SWW_RS_FAILED) {",
                  "equivalent: the hook is reached only with halted == 1 (ca_ok); the groups armed at W17 are CA and W84 only, and one precondition result that lets the CA row run (0, or 5 with memory decode on) skips neither row (swl_restore), so restore is OK or FAILED there, never SKIPPED or NONE"))
    out.append(_f("run F2 hook: stop swallowed", w17, "\t\tif (rc) {\n\t\t\tx->restore = rs;        /* as below: the stop found nothing armed */\n\t\t\treturn rc;\n\t\t}\n", ""))
    out.append(_f("run F2 hook: restore result not kept", w17, "\t\t\tx->restore = rs;        /* as below: the stop found nothing armed */\n", ""))
    out.append(_f("run F2 hook: no line when the dumps are not made", w17, "} else if (x->level == SWW_F2_LEVEL) {", "} else if (0) {"))
    out.append(_f("run F3: the line 'F2 window 0 dumps not made' also at level 10 (>=)", w17, "} else if (x->level == SWW_F2_LEVEL) {", "} else if (x->level >= SWW_F2_LEVEL) {"))
    out.append(_f("run F2 hook: the line at a lower level", w17, "} else if (x->level == SWW_F2_LEVEL) {", "} else if (x->level == SWW_F2_LEVEL || x->level == SWW_F2_LEVEL - 1) {"))
    out.append(_f("run F2 hook: the line without the restore result", w17, "F2 window 0 dumps not made, restore=%s\\n\", swl_rsname(x->restore));", "F2 window 0 dumps not made, restore=%s\\n\", \"ok\");"))
    f2hook = re.search(r"\tif \(x->level == SWW_F2_LEVEL && x->restore == SWW_RS_OK\) \{.*?\} else if \(x->level == SWW_F2_LEVEL\) \{.*?\n\t\}\n", body(final_header(), w17), re.S).group(0)
    out.append(_fx("run F2: the dumps after the PCIe core group", [(w17, f2hook, ""), (w17, "\tx->flags |= SWF_W17;\n\treturn 0;", f2hook + "\tx->flags |= SWF_W17;\n\treturn 0;")]))
    out.append(_fx("run F2: the dumps before the re-halt", [(w17, f2hook, ""), (w17, "\tswl_restore(x);\n\tx->halted = x->ca_ok;", f2hook.replace("x->restore == SWW_RS_OK", "1") + "\tswl_restore(x);\n\tx->halted = x->ca_ok;")]))
    return out


MUTANTS += _run_f2_mutants()


# ---- patch 0136 (run F3): the F2 dumps while the CA7 runs, after one cfg 0x84 := 0. Same rule as above: built from the final header, a pattern that no longer matches fails at import. ----
def _run_f3_mutants():
    out = []
    f3, w17 = "swl_f3", "swl_w17"
    out.append(_d("run F3: level 9", "SWW_F3_LEVEL", "SWW_LEVEL0 + 8", "SWW_LEVEL0 + 7"))
    out.append(_d("run F3: level 11", "SWW_F3_LEVEL", "SWW_LEVEL0 + 8", "SWW_LEVEL0 + 9"))
    # the entry
    out.append(_f("run F3: level not checked", f3, "if (x->level < SWW_F3_LEVEL) {", "if (0) {"))
    out.append(_f("run F3: level checked one too low", f3, "x->level < SWW_F3_LEVEL", "x->level < SWW_F3_LEVEL - 1"))
    out.append(_f("run F3: row not set", f3, "x->row = 17;", "x->row = 16;"))
    out.append(_f("run F3: the mask of an earlier run is kept", f3, "\tx->f2 = 0;\n", ""))
    # the one store
    st = "SWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, 0u));"
    out.append(_f("run F3 store: no select although the tail window is on", f3, "if (sw != 0) {", "if (0) {"))
    out.append(_f("run F3 store: a select although window 0 is on already", f3, "if (sw != 0) {", "if (1) {"))
    out.append(_f("run F3 store: the value is the tail window", f3, st, "SWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL));"))
    out.append(_f("run F3 store: the BAR0 window register", f3, st, "SWW_TRY(swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN, 0u));"))
    out.append(_f("run F3 store: stop swallowed", f3, st, "(void)swl_wr(x, SWW_CFG, 0, SWW_CFG_WIN2, 0u);"))
    out.append(_f("run F3 store: not by swl_wr (no precondition, no read-back)", f3, st, "SWW_TRY(swl_store(x, SWW_CFG, 0, SWW_CFG_WIN2, 0u));"))
    # the guard
    pre1 = "\tif (swl_wpre(x)) {\n\t\treturn swl_stop(x, SWW_E_PRE);\n\t}\n\tSWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);"
    out.append(_f("run F3: precondition before the guard not checked", f3, pre1, "\tSWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);"))
    out.append(_f("run F3: precondition before the guard ignored", f3, pre1, "\t(void)swl_wpre(x);\n\tSWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);"))
    out.append(_f("run F3 guard: cfg 0x84 read from cfg 0x80", f3, "SWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN2, c84);", "SWW_RD(x, SWW_CFG, 0, SWW_CFG_WIN, c84);"))
    out.append(_f("run F3 guard: cfg 0x84 not 0 accepted", f3, "if (c84 != 0) {", "if (0) {"))
    out.append(_f("run F3 guard: cfg 0x84 not 0 is a stop", f3, "not 0, not read\\n\", c84);\n\t\treturn 0;", "not 0, not read\\n\", c84);\n\t\treturn swl_stop(x, SWW_E_STATE);"))
    out.append(_f("run F3 guard: cfg 0x84 not 0 said but read", f3, "not 0, not read\\n\", c84);\n\t\treturn 0;", "not 0, not read\\n\", c84);"))
    out.append(_f("run F3 guard: RAM word read at the next address", f3, "SWW_RD(x, SWW_BAR2, 0, SWW_RAM_BASE, w0);", "SWW_RD(x, SWW_BAR2, 0, SWW_RAM_BASE + 4u, w0);"))
    out.append(_f("run F3 guard: RAM word read in the tail window", f3, "SWW_RD(x, SWW_BAR2, 0, SWW_RAM_BASE, w0);", "SWW_RD(x, SWW_BAR2, SWW_WIN_TAIL, SWW_RAM_BASE, w0);"))
    out.append(_f("run F3 guard: image word 1 instead of word 0", f3, "swl_img_word(x, 0)", "swl_img_word(x, 1)"))
    out.append(_f("run F3 guard: no image is not refused", f3, "x->img != NULL && x->img_len >= 4u ? swl_img_word(x, 0) : 0xFFFFFFFFu", "swl_img_word(x, 0)"))
    out.append(_f("run F3 guard: an image shorter than a word is not refused", f3, "x->img != NULL && x->img_len >= 4u ?", "x->img != NULL ?"))
    out.append(_f("run F3 guard: a RAM word that is all ones is dumped", f3, "if (w0 == 0xFFFFFFFFu) {", "if (0) {"))
    out.append(_f("run F3 guard: a zero RAM word is skipped", f3, "if (w0 == 0xFFFFFFFFu) {", "if (w0 == 0xFFFFFFFFu || w0 == 0) {"))
    out.append(_f("run F3 guard: equality with the image word required", f3, "if (w0 == 0xFFFFFFFFu) {", "if (w0 == 0xFFFFFFFFu || w0 != want) {"))
    out.append(_f("run F3 guard: all ones is a stop", f3, "window 0 dumps not made\\n\", SWW_RAM_BASE);\n\t\treturn 0;", "window 0 dumps not made\\n\", SWW_RAM_BASE);\n\t\treturn swl_stop(x, SWW_E_DEAD);"))
    out.append(_f("run F3 guard: all ones said but dumped", f3, "window 0 dumps not made\\n\", SWW_RAM_BASE);\n\t\treturn 0;", "window 0 dumps not made\\n\", SWW_RAM_BASE);"))
    out.append(_f("run F3 guard: not recorded", f3, "x->f2 |= SWW_F2_LOW;", ""))
    out.append(_f("run F3: stop of the dumps swallowed", f3, "SWW_TRY(swl_f2_dump(x));", "(void)swl_f2_dump(x);"))
    out.append(_f("run F3: done line without the mask", f3, "F3 done mask %#x\\n\", x->f2);", "F3 done mask %#x\\n\", 0u);"))
    # the hook in W17
    hk = "if (x->level >= SWW_F3_LEVEL) {"
    out.append(_f("run F3 hook: never runs", w17, hk, "if (0) {"))
    out.append(_f("run F3 hook: runs at every level", w17, hk, "if (1) {"))
    out.append(_f("run F3 hook: runs one level early", w17, hk, "if (x->level >= SWW_F3_LEVEL - 1) {"))
    out.append(_f("run F3 hook: stop swallowed", w17, "SWW_TRY(swl_f3(x, sw));", "(void)swl_f3(x, sw);"))
    out.append(_f("run F3 hook: handed window 0", w17, "swl_f3(x, sw)", "swl_f3(x, 0)"))
    out.append(_f("run F3 hook: handed the tail window", w17, "swl_f3(x, sw)", "swl_f3(x, SWW_WIN_TAIL)"))
    out.append(_f("run F3 hook: handed the shared address", w17, "swl_f3(x, sw)", "swl_f3(x, sh)"))
    f3blk = re.search(r"\tif \(x->level >= SWW_F3_LEVEL\) \{.*?\n\t\}\n", body(final_header(), w17), re.S).group(0)
    f1blk = re.search(r"\tif \(x->level >= SWW_F1_LEVEL\) \{         /\* run F1 \(patch 0134\).*?\n\t\}\n", body(final_header(), w17), re.S).group(0)
    out.append(_fx("run F3 hook: after the re-halt", [(w17, f3blk, ""), (w17, "\tswl_restore(x);\n\tx->halted = x->ca_ok;", "\tswl_restore(x);\n" + f3blk + "\tx->halted = x->ca_ok;")]))
    out.append(_f("run F3 hook: handed W17's poll window", w17, "swl_f3(x, sw)", "swl_f3(x, win)"))
    out.append(_f("run F2: the guard does not lead to the dumps", "swl_f2", "\treturn swl_f2_dump(x);\n", "\treturn 0;\n"))
    out.append(_fx("run F3 hook: before F1", [(w17, f1blk + f3blk, f3blk + f1blk)]))
    return out


MUTANTS += _run_f3_mutants()


def run_bounded(cmd, d, cpu, wall=None):
    """Run a test binary with its output sent to files capped by RLIMIT_FSIZE (1 MiB): a mutant that loops forever and prints must not fill the parent's memory (a captured pipe did, 6.7 GB). The limit is CPU seconds (RLIMIT_CPU: a SIGSTOPped child uses none, AP-105); `wall` is only a backstop. Returns (returncode, stderr tail, stdout tail)."""
    def cap():
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    with open(os.path.join(d, "out.txt"), "wb") as fo, open(os.path.join(d, "err.txt"), "wb") as fe:
        rc = cpu_timeout.run_cpu_limited(cmd, cpu, wall_factor=max(1, (wall or WALL_LIMIT) / cpu), stdout=fo, stderr=fe, preexec_fn=lambda: (cap(), _nice()))    # cpu = CPU seconds (AP-105); SIGXCPU/wall/stopped raise CpuLimitExpired
    tails = []
    for n, k in (("err.txt", 200), ("out.txt", 600)):
        with open(os.path.join(d, n), "rb") as f:          # at most 1 MiB (RLIMIT_FSIZE)
            text = f.read().decode("utf-8", "replace").replace(d, "<D>")
        tails.append(text[-k:])     # rewrite <D> in the full text BEFORE the cut: a tail starting inside the dir name kept a random fragment (review A6)
    return rc, tails[0], tails[1]


RUN_LIMIT = 120          # CPU seconds a test binary may use inside the pool (CPU time, not wall: pause-robust, PLAN-FAST-GATE L2)
ALONE_LIMIT = 600        # CPU seconds the re-run alone of a mutant that timed out in the pool may use (review 0134 F2)
GCC_CPU = 60             # CPU seconds one gcc may use
WALL_LIMIT = 3600        # wall-clock backstop for anything that neither spins nor ends
HIT_LOG = {}             # mutated header -> cached verdict, for the spot-check
STATS = {}
_LOCK = threading.Lock()


def _count(k):
    with _LOCK:
        STATS[k] = STATS.get(k, 0) + 1


CC_FLAGS = ["-O1", "-Wall", "-Wextra", "-DSWW_FAILFAST=1"]     # a cheaper level only with a kill-set proof (research 71); SWW_FAILFAST: the test stops at its first failed check (research 77 M1), in the key
CC_PIPE = ["-pipe"]                        # output unchanged; not part of the key
def _nice():
    """Children yield to the gates' other work: niceness at least 10, never more than the parent already has (a relative os.nice(10) under a nice'd gate gave 20 and starved the children at load 50, research 71)."""
    if os.nice(0) < 10:
        os.nice(10 - os.nice(0))


def _read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


HANG_MSG = "killed by a hang (re-run, %d CPU s)" % ALONE_LIMIT     # fixed text: a stored hang kill must compare equal in the spot-check (the CpuLimitExpired text holds the temp dir)


def build_and_run(hdr, chip, work, limit=None, use_cache=True, cc_flags=None, timing=None):
    """-> (status, msg). Status "timeout": the test used `limit` CPU seconds (or the wall backstop) and is re-run by run_all; "infra": gcc ran out of CPU/wall or was killed, re-run by run_all, never cached, never a kill.
    The re-run (limit=ALONE_LIMIT) that again goes over its CPU limit returns ("fail", HANG_MSG) and stores it: CPU seconds do not depend on load or pauses (AP-105, AP-114), so a mutant
    that spins 600 CPU s (the unmutated test needs ~27) is a deterministic kill. A wall-backstop or stopped re-run stays "timeout" and is never stored.
    cc_flags / timing are for --probe-failfast only: other gcc flags (with use_cache=False, the key is not built), and a dict that gets child CPU seconds "gcc" / "test" (exact only when nothing else runs children)."""
    limit = RUN_LIMIT if limit is None else limit
    cc_flags = CC_FLAGS if cc_flags is None else cc_flags
    tests = [(os.path.basename(t), _read(t)) for t in (TEST_NV, TEST)]
    if cc_flags is not CC_FLAGS and use_cache:
        raise ValueError("build_and_run: own cc_flags need use_cache=False (the cache key and its verdicts belong to CC_FLAGS)")
    key = wlan_mutant_cache.make_key(chip, hdr, tests, CC_FLAGS) if use_cache else None
    if key:
        hit = wlan_mutant_cache.get(key)
        if hit:
            _count("hits")
            with _LOCK:
                HIT_LOG[hdr] = hit
            return hit
    _count("fresh")
    d = tempfile.mkdtemp(dir=work)
    for name, text in (("sweet_wlan_chip.h", chip), ("sweet_wlan_wr.h", hdr)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    res = []
    if timing is not None:
        timing.update(gcc=0.0, test=0.0)
    for i, test in enumerate((TEST_NV, TEST)):         # the cheap pure test first; the first kill ends the mutant
        exe = os.path.join(d, "t%d" % i)
        # compile a COPY inside d: gcc searches the including file's directory before -I, so a header placed in shim/ (next to the real .c) would silently
        # shadow the mutated one (review gate-runfast-1 #4)
        src = os.path.join(d, os.path.basename(test))
        shutil.copyfile(test, src)
        try:
            with open(os.path.join(d, "cc.txt"), "w+") as fcc:
                c0 = _child_cpu()
                crc = cpu_timeout.run_cpu_limited(["gcc"] + cc_flags + CC_PIPE + ["-I" + d, "-o", exe, src], GCC_CPU, wall_factor=WALL_LIMIT / GCC_CPU, stdout=fcc, stderr=fcc, preexec_fn=_nice)
                if timing is not None:
                    timing["gcc"] += _child_cpu() - c0
                fcc.seek(0)
                cc = subprocess.CompletedProcess([], crc, "", fcc.read())
        except subprocess.TimeoutExpired as e:      # a compiler hang is never a mutant killed by a hang (trunk review F4, rule 8): infra is re-run alone by run_all and fails the gate if still infra, never cached
            return "infra", "gcc hang or CPU limit: %s" % e
        err = cc.stderr.replace(d, "<D>")      # the temp dir name differs per run: keep messages comparable
        if cc.returncode == -signal.SIGKILL or (cc.returncode != 0 and wlan_mutant_cache.infra_text(err)):
            return "infra", "gcc rc %d: %s" % (cc.returncode, err[-300:])
        if cc.returncode != 0:
            v = ("compile", err[-400:])
            if key and wlan_mutant_cache.deterministic("compile", cc.returncode, cc.stderr):
                _store(key, v)
            return v
        c1 = _child_cpu()
        try:
            r = run_bounded([exe], d, limit)
            if timing is not None:
                timing["test"] += _child_cpu() - c1
        except subprocess.TimeoutExpired as e:      # CpuLimitExpired: .kind is cpu / rlimit-cpu / wall-backstop / stopped
            if limit >= ALONE_LIMIT and getattr(e, "kind", None) in ("cpu", "rlimit-cpu"):
                v = ("fail", HANG_MSG)
                if key:
                    _store(key, v)
                return v
            return "timeout", "the test did not end in %d CPU s: %s" % (limit, e)      # never cached
        if r[0] == -signal.SIGKILL:     # OOM killer or an outside kill; the CPU limit raises first (review A5): never a timeout kill, never cached
            return "infra", "the test was SIGKILLed (rc %d, not the CPU limit): %s" % (r[0], r[1][-200:])
        if r[0] != 0:
            v = ("fail", r[1])
            if key and wlan_mutant_cache.deterministic("fail", r[0], r[1]):
                _store(key, v)
            return v
        res.append(r[1])
    v = ("pass", res[-1])
    if key:
        _store(key, v)
    return v


def _store(key, v):
    _count("stored" if wlan_mutant_cache.put(key, *v) else "store_failed")


def _child_cpu():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def run_all():
    hdr = final_header()
    chip = chip_header()
    out = {}
    STATS.clear()
    HIT_LOG.clear()
    run_all.mismatch, run_all.spot = [], (0, 0)
    salt = wlan_mutant_cache.spot_salt()
    os.makedirs(os.path.join(ROOT, "shim", "out"), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "shim", "out")) as work:
        c0 = _child_cpu()
        base = build_and_run(hdr, chip, work, use_cache=False)     # the unmutated run is the timeout-margin probe: always measured, as child CPU seconds (wall time says nothing under load or a pause)
        run_all.base_s = _child_cpu() - c0; run_all.pool_limit = min(max(RUN_LIMIT, 4 * run_all.base_s), ALONE_LIMIT / 2)   # 4x this run's base (CPU s grow with host load: audit 2026-11-11), never the hang limit
        STATS.clear()                                               # the base run is not a mutant
        jobs, subbed, futs = {}, {}, {}
        jobs_n = wlan_mutant_pool.default_jobs()
        gate = wlan_mutant_pool.JobGate(jobs_n)      # PLAN-GATE-2X K1: jobs_n permits, +8 (cap nproc-2) once check-all.sh releases the host lock; the line `lane C jobs 6->14 at +NNNs` goes to stderr
        with concurrent.futures.ThreadPoolExecutor(max_workers=gate.max_workers) as ex:
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
                subbed[name] = m
                if m not in futs:                  # identical mutated header = identical verdict: build it once
                    futs[m] = ex.submit(gate.run, build_and_run, m, chip, work, run_all.pool_limit)
                jobs[name] = (futs[m], equiv)
            for name, (fut, equiv) in jobs.items():
                st, msg = fut.result()
                out[name] = (st, msg, equiv)
            pool_stats = dict(STATS)                # hits/fresh of the first pass only (the spot-check and the alone re-runs are not counted)
            # PLAN-FAST-GATE D2a: re-run a deterministic sample of cache hits fresh, in the same pool; any difference = FAIL, cache quarantined
            hits = {n: (HIT_LOG[subbed[n]], out[n][2], subbed[n]) for n in jobs if subbed[n] in HIT_LOG}
            def fresh(m):
                v = gate.run(lambda: build_and_run(m, chip, work, run_all.pool_limit, use_cache=False))
                return gate.run(lambda: build_and_run(m, chip, work, ALONE_LIMIT, use_cache=False)) if v[0] in ("timeout", "infra") else v      # same path as the pool + re-run below
            if hits:
                checked, bad = wlan_mutant_cache.spot_check(hits, fresh, map_fn=lambda f, xs: list(ex.map(f, xs)), salt=salt)
                run_all.spot, run_all.mismatch = (checked - len(bad), checked), bad
                if bad:
                    print("MUTCACHE MISMATCH %s" % ", ".join(bad), file=sys.stderr)      # the names first: a failing rename must not hide them (review A7)
                    try:
                        print("MUTCACHE MISMATCH: cache dir renamed %s" % wlan_mutant_cache.quarantine(), file=sys.stderr)
                    except OSError as e:
                        print("MUTCACHE MISMATCH: quarantine failed: %s" % e, file=sys.stderr)
        # review 0134 F2: a pool timeout is re-run with ALONE_LIMIT (5x) before it counts as killed: killed only if it FAILs or goes over the CPU limit again (then stored, HANG_MSG).
        # PLAN-FAST-GATE L2: a gcc "infra" verdict is re-run once too; if it is still infra the test fails (never a kill, never a pass).
        # The limits are CPU seconds (pause- and load-robust, AP-105), so the re-runs need not be alone: they run in parallel (AP-114: the serial re-run was ~12 min of every gate).
        tmo = [n for n, (st, _m, _e) in out.items() if st == "timeout"]
        inf = [n for n, (st, _m, _e) in out.items() if st == "infra"]
        again = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=gate.max_workers) as ex2:
            for name in tmo + inf:
                if subbed[name] not in again:
                    again[subbed[name]] = ex2.submit(gate.run, build_and_run, subbed[name], chip, work, ALONE_LIMIT)
            for name in tmo + inf:
                st, msg = again[subbed[name]].result()
                _s, _m, equiv = out[name]
                # a CPU-limit re-run is already ("fail", HANG_MSG); a re-run still "timeout" hit the wall backstop or was stopped: no verdict, so infra (fails
                # the gate loudly), never a kill (review mutants-speed-r1 #2: parallel re-runs make a backstop under load likelier)
                out[name] = ("infra", "the re-run alone reached no verdict (wall backstop or stopped, not the CPU limit): %s" % msg, equiv) if st == "timeout" else (st, msg, equiv)
        gate.report("set header")
        run_all.timeout_kills = sum(1 for n in tmo if out[n][1].startswith("killed by a hang"))
        # rule 8 (watcher item e): name every verdict the cache does not hold after this run (a warm run recomputes exactly these)
        unstored = []
        if wlan_mutant_cache.state() == "on":
            tests = [(os.path.basename(t), _read(t)) for t in (TEST_NV, TEST)]
            unstored = sorted("%s (%s)" % (n, out[n][0]) for n in jobs if wlan_mutant_cache.get(wlan_mutant_cache.make_key(chip, subbed[n], tests, CC_FLAGS)) is None)
            if unstored:
                print("MUTCACHE unstored=%d (recomputed by every run): %s" % (len(unstored), "; ".join(unstored)), file=sys.stderr)
        run_all.unstored = unstored
        cache = wlan_mutant_cache.state()
        if cache == "on":
            cache = "ro" if STATS.get("store_failed") else "ok"      # off / ro / ok: a cold run, a disabled and an unwritable cache look different (review A7, rule 8)
        run_all.line = "MUTCACHE cache=%s hits=%d fresh=%d stored=%d store_failed=%d dedup=%d cpu_timeouts=%d infra=%d spot=%d/%d salt=%s %s" % (
            cache, pool_stats.get("hits", 0), pool_stats.get("fresh", 0), STATS.get("stored", 0), STATS.get("store_failed", 0), len(jobs) - len(futs), len(tmo), len(inf),
            run_all.spot[0], run_all.spot[1], salt, "MISMATCH" if run_all.mismatch else "ok")
        print("timeout kills: %d (re-run), mutants re-run: %d, unmutated build + test: %.1f CPU s, pool CPU limit %.0f s" % (run_all.timeout_kills, len(tmo), run_all.base_s, run_all.pool_limit), file=sys.stderr)
    dump = os.environ.get("GATE_MUT_VERDICTS")
    if dump:       # A-vs-B equality proof (PLAN-FAST-GATE D3): every verdict, sorted
        with open(dump, "w", encoding="utf-8") as f:
            json.dump(sorted([n, st, msg] for n, (st, msg, _e) in out.items()), f, indent=1)
    return base, out


_FUNC = re.compile(r"^(?!_Static_assert\b)(\w+)\(.*?^}$", re.S | re.M)      # a column-0 _Static_assert( (patch 0167) is not a function


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
from wlan_glue_harness_a import GLUE_HARNESS_A
from wlan_glue_harness_b import GLUE_HARNESS_B

GLUE_HARNESS = GLUE_HARNESS_A + GLUE_HARNESS_B     # T-4adb (R29): the C text lives in tools/wlan_glue_harness_a.py and _b.py


def l25wl_header():
    """shim/sweet_wlan_l25_wl.h (the L25 store judge, T-5173): the glue text names its types and the glue harnesses include it as is (T-2543)."""
    with open(os.path.join(ROOT, "shim", "sweet_wlan_l25_wl.h"), encoding="utf-8") as fh:
        return fh.read()


def glue_core_text():
    """sw3_ptr .. the closing brace of sw3_wr16 (W-D4b) from the glue, with the return type of sw3_ptr put back."""
    g = glue_after()
    a = re.search(r"^sw3_ptr\(", g, re.M)
    e = re.search(r"^sw3_wr16\(.*?^}$", g, re.S | re.M)     # W-D4b: sw3_wr16 follows sw3_wr directly
    if not a or not e:
        raise ValueError("sw3_ptr..sw3_wr16 not found in the glue")
    return "static volatile uint32_t *\n" + g[a.start():e.end()] + "\n"


def glue_d4_text():
    """review 0174-w-d4e-scan24-hwwrite-r1 F2/F4: the glue from SW_G2_FLAGS2_OLD to the closing brace of sw_g2_d4 (sw_g2_add, the D4c/D4d helpers, sw_g2_d4d, sw_g2_ds, sw_g2_d4): the REAL list builder, swept with the store streams of the header test."""
    t = g2_list_text()
    e = re.search(r"^sw_g2_d4\(.*?^}$", t, re.S | re.M)
    if not e:
        raise ValueError("sw_g2_d4 not found in the glue")
    return t[:e.end()] + "\n"


def glue_function_text():
    """What the glue sweep compiles and what GLUE_MUTANTS edit (tools/lint-mutant-needles checks them against this): sw3_ptr..sw3_wr16, then the real list builder (review F2/F4)."""
    return glue_core_text() + "\n" + glue_d4_text()


_STREAMS = None


SCAN24_LABELS = ["main", "rxoff", "l22", "ds", "ds0", "lifo", "evlifo", "cap", "clm", "clmref"]      # review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 2: the L21-L23 host-test cases whose store streams are replayed through the glue (host test order)
L24_NEGATIVES = 20       # T-23da: the L24 negatives/controls GLUE_HARNESS prints ("L24 streams N negatives M"): 13 of T-8ede + the early L24 store (F1) + event word at item 127 (F2); the CLI and the unittest both use it (review T-4a7f r2 F2)
L24_LABELS = ["j24", "j24rev", "j24e1", "j24e62", "j24mac", "j24key"]      # T-8ede: the L24 join streams (events inside the join / LINK first / npartial 0 / npartial 61 = 62 + 2 event re-posts, the cap 64 / a wsec_key call), replayed at d4b 11 with their join table
G2_JC = {}      # T-8ede: label -> [(cmd, bytes)] the join commands the host test handed the engine (G2JC lines); the replay sets hw.jc / njc to the same table


def g2_store_streams():
    """Compile shim/wlan_g2_host_test.c against the real header, run it with G2_DUMP_STORES=1 and return (pa, [(label, rounds, [(host, off, value)])]): the host/doorbell stores after the L16 C2 doorbell of ds_tests (i), (i-b), (iii) (review F4)."""
    global _STREAMS
    if _STREAMS is not None:
        return _STREAMS
    with tempfile.TemporaryDirectory() as d:
        for fname, text in (("sweet_wlan_wr.h", final_header()), ("sweet_wlan_chip.h", chip_header()), ("sweet_wlan_dma.h", new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h")), ("sweet_wlan_d4b.h", new_file(PATCH0174, "bsd/dev/sweet_wlan_d4b.h")), ("sweet_wlan_g2.h", g2_header())):
            with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(d, "wlan_g2_host_test.c"), "w", encoding="utf-8") as fh:
            fh.write(g2_test_text())
        shutil.copyfile(os.path.join(ROOT, "shim", "sweet_wlan_join.h"), os.path.join(d, "sweet_wlan_join.h"))      # T-6bef: the L24 cases include the reviewed join payloads
        shutil.copyfile(os.path.join(ROOT, "shim", "sweet_wlan_l25_wl.h"), os.path.join(d, "sweet_wlan_l25_wl.h"))      # T-5173: the L25 store whitelist the host test includes
        shutil.copyfile(os.path.join(ROOT, "shim", "sweet_wlan_join_fw.h"), os.path.join(d, "sweet_wlan_join_fw.h"))      # T-4a7f: split out of sweet_wlan_join.h by T-4115 (included at its end)
        b = os.path.join(d, "t")
        c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + d, "-o", b, os.path.join(d, "wlan_g2_host_test.c")], capture_output=True, text=True, timeout=120)
        if c.returncode != 0:
            raise ValueError("wlan_g2_host_test.c does not compile: " + c.stderr[-600:])
        r = subprocess.run([b], capture_output=True, text=True, timeout=120, cwd=d, env=dict(os.environ, G2_DUMP_STORES="1"))
        if r.returncode != 0:
            raise ValueError("wlan_g2_host_test fails: " + (r.stdout + r.stderr)[-600:])
    if "G2TRUNC" in r.stdout:
        raise ValueError("G2_DUMP_STORES: more stores than the NREC record array of wlan_g2_host_test.c")
    pa, sets = None, []
    for line in r.stdout.splitlines():
        w = line.split()
        if line.startswith("G2PA "):
            pa = int(w[1], 16)
        elif line.startswith("G2SET "):
            sets.append((w[1], int(w[2]), []))
            G2_JC[w[1]] = []
        elif line.startswith("G2JC ") and sets:
            if len(bytes.fromhex(w[3])) != int(w[2]):
                raise ValueError("G2JC line of %s: %d bytes, length says %s" % (sets[-1][0], len(bytes.fromhex(w[3])), w[2]))
            G2_JC[sets[-1][0]].append((int(w[1]), bytes.fromhex(w[3])))
        elif line.startswith("G2S ") and sets:
            sets[-1][2].append((w[1] == "H", int(w[2], 16), int(w[3], 16)))
    if pa is None or [x[0] for x in sets] != ["i", "i-b", "iii"] + SCAN24_LABELS + L24_LABELS or any(not x[2] for x in sets) or any(not G2_JC[l] for l in L24_LABELS) or any(G2_JC[x[0]] for x in sets if x[0] not in L24_LABELS):
        raise ValueError("G2_DUMP_STORES dump: want streams i, i-b, iii, the scan24 ones and the L24 ones (join commands only on L24), got " + repr([(x[0], len(x[2])) for x in sets]))
    _STREAMS = (pa, sets)
    return _STREAMS


def streams_c():
    pa, sets = g2_store_streams()
    out = ["struct rs { int host; uint32_t a, v; };", "static const unsigned long long rs_pa = 0x%xull;" % pa]
    for i, (l, _r, st) in enumerate(sets):
        out.append("static const struct rs rs_%d[] = { %s };" % (i, ", ".join("{ %d, 0x%x, 0x%x }" % (1 if h else 0, a, v) for h, a, v in st)))
        if G2_JC.get(l):
            out.append("static const struct swg_jcmd jc_%d[] = { %s };" % (i, ", ".join('{ %du, "H?", { %s }, %du }' % (c, ", ".join("0x%02x" % x for x in b), len(b)) for c, b in G2_JC[l])))
    out.append("static const struct { const char *label; unsigned rounds, n; const struct rs *s; int d4b; const struct swg_jcmd *jc; unsigned njc; } rsets[] = { %s };" % ", ".join('{ "%s", %d, %d, rs_%d, %d, %s, %d }' % (l, r, len(st), i, 11 if l in L24_LABELS or l in ("clm", "clmref") else 10 if l in SCAN24_LABELS else 7, "jc_%d" % i if G2_JC.get(l) else "NULL", len(G2_JC.get(l, []))) for i, (l, r, st) in enumerate(sets)))
    out.append("static const unsigned nrsets = %d;" % len(sets))
    return "\n".join(out)

def pool_map(fn, items):
    """fn(item) for every item on GATE_MUT_JOBS threads (same rule as run_all); results in input order (AP-368)."""
    jobs_n = int(os.environ.get("GATE_MUT_JOBS", min(12, os.cpu_count() or 1)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs_n) as ex:
        return list(ex.map(fn, items))


def run_glue(glue_text, work, extra=(), stdout_sink=None):
    """Compile the glue functions with gcc -Werror against the real header, sweep them. Returns ('pass'|'fail'|'compile', output)."""
    d = tempfile.mkdtemp(dir=work)
    for text, name in ((chip_header(), "sweet_wlan_chip.h"), (final_header(), "sweet_wlan_wr.h"), (new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h"), "sweet_wlan_dma.h"), (l25wl_header(), "sweet_wlan_l25_wl.h")):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(d, "g.c"), "w", encoding="utf-8") as fh:
        fh.write(GLUE_HARNESS.replace("@STREAMS@", streams_c()).replace("@GLUE@", glue_text))
    c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers", "-Wno-unused-function", "-I" + d] + list(extra) + ["-o", os.path.join(d, "g"), os.path.join(d, "g.c")], capture_output=True, text=True)      # 22b: the sweep table rows name the first 11 fields of struct sw_g2w; the later ones (kind, rep, stride, vrng) default to 0 = an exact word
    if c.returncode != 0:
        return "compile", c.stderr.replace(d, "<D>")[-600:]      # <D>: the temp dir name differs per run, a cached message must compare equal
    r = run_bounded([os.path.join(d, "g")], d, 300)
    if r[0] == -signal.SIGKILL:        # OOM killer or an outside kill: not a verdict (rule 8); the pool reports a raising worker loudly and never stores it
        raise RuntimeError("glue sweep SIGKILLed (rc %d), no verdict: %s" % (r[0], r[1][-200:]))
    if stdout_sink is not None:
        stdout_sink.append(r[2])
    return ("pass" if r[0] == 0 else "fail"), r[2]


GLUE_MUTANT_FLAGS = ["-Wno-parentheses", "-DSWG_FAILFAST=1"]      # mutants only: the harness ends at its first BAD (SWG_BAD), proven kill-set-equal to the full sweep; the unmutated sweep runs in full
_LIM = "\tif ((space == SWW_CFG && off != SWW_CFG_WIN &&"
_MOVE = "@move"  # special: the offset check block is moved behind the store (see test_glue_mutants_are_killed)
_LIMEND = "SWW_LAST_HI_MAX)))) {\n\t\treturn -1;\n\t}\n"

GLUE_MUTANTS = [
    # patch 0174: the G2 branch of sw3_wr (sw3_g2_ok), one per rule; the G2 sweep at the end of GLUE_HARNESS must catch each
    ("G2: branch skipped", [("\tif (h->g2) {", "\tif (0) {")]),
    ("G2: refusal falls through to the old checks", [("\t\tif (g2i < 0) {\n\t\t\treturn -1;\n\t\t}\n\t\tgoto put;", "\t\tif (g2i >= 0) {\n\t\t\tgoto put;\n\t\t}")]),
    ("G2: no level gate", [("h->l3 < SWW_F3_LEVEL || !h->g2ok", "!h->g2ok")]),
    ("G2: no g2ok gate", [("h->l3 < SWW_F3_LEVEL || !h->g2ok || p == NULL", "h->l3 < SWW_F3_LEVEL || p == NULL")]),
    ("G2: window move without the EP precondition", [("(off == SWW_CFG_WIN || off == SWW_CFG_WIN2) && swl_pre_ep(", "(off == SWW_CFG_WIN || off == SWW_CFG_WIN2) && 0 && swl_pre_ep(")]),
    ("G2: window precondition ignores g2ep", [("h->cfg[0x14 / 4], h->g2ep) != 0) {\n\t\tswl_gap(\"g2\"", "h->cfg[0x14 / 4], 0x2u) != 0) {\n\t\tswl_gap(\"g2\"")]),
    ("G2: space not compared", [("e->space != space || e->win != win", "e->win != win")]),
    ("G2: window not compared", [("e->space != space || e->win != win || e->w16", "e->space != space || e->w16")]),
    ("G2: offset not compared", [("\treturn off == eo;\n}", "\treturn 1;\n}")]),
    ("G2: value not compared", [("(e->vrng != 0u ? (vv & e->vmask) >= ev && (vv & e->vmask) - ev <= e->vrng : (vv & e->vmask) == ev) && ", "")]),
    ("G2: kept bits not compared", [("(vv & ~e->vmask) == (old & ~e->vmask) && ", "")]),
    ("G2: old value not compared", [("(old & e->omask) == eold)", "1)")]),
    ("G2: ONCE ignored", [("(e->once && e->done)", "0")]),
    ("G2: need ignored", [("(e->need >= 0 && !h->g2l[e->need].done)", "0")]),
    ("G2: done not recorded", [("\t\th->g2l[g2i].done = 1;\n", "")]),
    # W-D4b gaps: the w16 match, the cfg 0x88 restore store and sw3_wr16 (sweetwlan=14)
    ("G2: w16 match ignored", [("e->w16 != (w16 ? 1 : 0) || ", "")]),
    ("G2: w16 entry matched by a 32-bit store only", [("e->w16 != (w16 ? 1 : 0) || ", "e->w16 != 0 || ")]),
    ("G2: w16 upper half not merged", [("vv = w16 ? (old & 0xFFFF0000u) | (v & 0xFFFFu) : v;", "vv = v;")]),
    ("G2: w16 merge keeps the lower half", [("vv = w16 ? (old & 0xFFFF0000u) | (v & 0xFFFFu) : v;", "vv = w16 ? (old & 0x0000FFFFu) | (v & 0xFFFFu) : v;")]),
    ("G2: w16 flag not passed by sw3_wr16", [("g2i = sw3_g2_ok(h, space, win, off, v, 1);", "g2i = sw3_g2_ok(h, space, win, off, v, 0);")]),
    ("G2: w16 flag passed by sw3_wr", [("g2i = sw3_g2_ok(h, space, win, off, v, 0);\n", "g2i = sw3_g2_ok(h, space, win, off, v, 1);\n")]),
    ("w16: no g2 gate", [("if (!h->g2 || space != SWW_BAR2 || h->d4b < 2) {", "if (space != SWW_BAR2 || h->d4b < 2) {")]),
    ("w16: no space gate", [("if (!h->g2 || space != SWW_BAR2 || h->d4b < 2) {", "if (!h->g2 || h->d4b < 2) {")]),
    ("w16: no level gate", [("if (!h->g2 || space != SWW_BAR2 || h->d4b < 2) {", "if (!h->g2 || space != SWW_BAR2) {")]),
    ("w16: level gate at 1", [("if (!h->g2 || space != SWW_BAR2 || h->d4b < 2) {", "if (!h->g2 || space != SWW_BAR2 || h->d4b < 1) {")]),
    ("w16: list result ignored", [("\tif (g2i < 0) {\n\t\treturn -1;\n\t}\n\tp = sw3_ptr(h, space, off);\n\tif (p == NULL || h->cfg[SWW_CFG_WIN2 / 4] != win) {", "\tp = sw3_ptr(h, space, off);\n\tif (p == NULL || h->cfg[SWW_CFG_WIN2 / 4] != win) {")]),
    ("w16: a 32-bit access", [("*(volatile uint16_t *)p = v;", "*p = v;")]),
    ("w16: the high half", [("*(volatile uint16_t *)p = v;", "*((volatile uint16_t *)p + 1) = v;")]),
    ("w16: done not recorded", [("\t__builtin_arm_dsb(0xf);\n\th->g2l[g2i].done = 1;\n\treturn 0;\n}\n", "\t__builtin_arm_dsb(0xf);\n\treturn 0;\n}\n")]),
    # W-D4b D-12 (sweetwlan=15): the two cfg 0x88 stores outside G2 (W15 and its restore), review 0174-w-d4b-hwwrite-r1 F1/F2; the rc88 table of GLUE_HARNESS must catch each
    ("c88: branch removed", [("\tif (space == SWW_CFG && off == 0x88u) {        /* W-D4b D-12", "\tif (0) {        /* W-D4b D-12")]),
    ("c88: restore without the W15 store", [("if ((h->c88 && v == SWW_CFG88) ||", "if ((v == SWW_CFG88) ||")]),
    ("c88: restore any value", [("if ((h->c88 && v == SWW_CFG88) ||", "if ((h->c88) ||")]),
    ("c88: restore value", [("if ((h->c88 && v == SWW_CFG88) ||", "if ((h->c88 && v == SWW_CFG88 + 1u) ||")]),
    ("c88: restore value is the OR'ed word", [("if ((h->c88 && v == SWW_CFG88) ||", "if ((h->c88 && v == (SWW_CFG88 | SW_G2_C88_BITS)) ||")]),
    ("c88: restore not once", [("h->c88 = v != SWW_CFG88;", "h->c88 = h->c88;")]),
    ("c88: set by every store", [("h->c88 = v != SWW_CFG88;", "h->c88 = 1;")]),
    ("c88: not set by the W15 store", [("h->c88 = v != SWW_CFG88;", "h->c88 = 0;")]),
    ("c88: W15 without h->w15", [("(h->w15 && !h->c88 && h->rel == 0", "(!h->c88 && h->rel == 0")]),
    ("c88: W15 twice", [("(h->w15 && !h->c88 && h->rel == 0", "(h->w15 && h->rel == 0")]),
    ("c88: W15 after the release", [("(h->w15 && !h->c88 && h->rel == 0", "(h->w15 && !h->c88")]),
    ("c88: W15 value not compared", [("&& v == (SWW_CFG88 | SW_G2_C88_BITS) &&", "&&")]),
    ("c88: W15 old not compared", [("&& h->cfg[0x88 / 4] == SWW_CFG88))", "))")]),
    ("c88: W15 value any bits", [("v == (SWW_CFG88 | SW_G2_C88_BITS)", "(v & SWW_CFG88) == SWW_CFG88")]),
    ("c88: W15 not once", [("\t	h->w15 = 0;                     /* W15 is once */\n", "")]),
    ("c88: refusal falls through", [("\t	swl_gap(\"g2\", \"glue: cfg 0x88 store outside the W15 state refused\", \"high\");", "\t	goto put;\n\t	swl_gap(\"g2\", \"glue: cfg 0x88 store outside the W15 state refused\", \"high\");")]),
    ("F9: unmask without the RC irq enable check", [("&& off == 0xC34u && v != 0u && sw3_g2_irq_off(h, v) != 0)", "&& off == 0xC34u && 0 && sw3_g2_irq_off(h, v) != 0)")]),  # keeps the call in the source (unused-function -Werror), never runs it
    ("F9: irq0 enable not checked", [("en0 != 0u || en1 != 0u || en2 != 0u", "en1 != 0u || en2 != 0u")]),
    ("F9: irq1 enable not checked", [("en0 != 0u || en1 != 0u || en2 != 0u", "en0 != 0u || en2 != 0u")]),
    ("F9: irq2 enable not checked", [("en0 != 0u || en1 != 0u || en2 != 0u", "en0 != 0u || en1 != 0u")]),
    ("F9: wrong enable offset", [("sw3_rd(h, SWW_ELBI, 0, SWW_RC_IRQ1_EN, &en1)", "sw3_rd(h, SWW_ELBI, 0, SWW_RC_IRQ1_EN + 0x20u, &en1)")]),
    ("F9: the check blocks the unmask (review r2 F10)", [("&& off == 0xC34u && v != 0u && sw3_g2_irq_off(h, v) != 0)", "&& off == 0xC34u && sw3_g2_irq_off(h, v) != 0)")]),
    ("F9: check result ignored", [("sw3_g2_irq_off(h, v) != 0) {      /* review r1 F9", "sw3_g2_irq_off(h, v) != 0 && 0) {      /* review r1 F9")]),
    ("G2: link check skipped (review r1 F1)", [("if (space != SW3_DBI) {", "if (0) {")]),
    ("G2: link check on the DBI store too", [("if (space != SW3_DBI) {", "if (1) {")]),
    ("G2: link check skipped for config space (review r2 F2)", [("if (space != SW3_DBI) {", "if (space != SW3_DBI && space != SWW_CFG) {")]),
    ("G2: link check skipped for BAR0 (review r2 F2)", [("if (space != SW3_DBI) {", "if (space != SW3_DBI && space != SWW_BAR0) {")]),
    # W-D4b: the host block space (SW3_HOST) in sw3_ptr, and its place behind the same list
    ("G2 host: bound off by one (low)", [("off <= h->g2plan.total - 4u ?", "off < h->g2plan.total - 4u ?")]),
    ("G2 host: no bound", [("h->g2plan.total >= 4u && off <= h->g2plan.total - 4u ?", "1 ?")]),
    ("G2 host: no VA check", [("h->g2va != 0 && h->g2plan.total >= 4u", "h->g2plan.total >= 4u")]),
    ("G2 host: wrong base", [("(volatile uint32_t *)(h->g2va + off)", "(volatile uint32_t *)(h->g2va + off + 4u)")]),
    ("G2 host: space refused", [("\tcase SW3_HOST:\n\t\treturn h->g2va != 0", "\tcase SW3_HOST + 100:\n\t\treturn h->g2va != 0")]),
    ("G2 host: link check skipped for host memory", [("if (space != SW3_DBI) {", "if (space != SW3_DBI && space != SW3_HOST) {")]),
    ("G2: link verdict ignored", [("if (bad || swl_pre_link(iso, ltssm, a0, a1) != 0) {", "if (bad) {")]),
    # no "link read failure ignored" mutant: iso/ltssm start at 0, so an unreadable iso/ELBI word already fails swl_pre_link (equivalent mutant); `bad` is kept as the explicit rule
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
    ("BAR2 stores after the release", [("\tif (space == SWW_BAR2 && (h->rel & 4u)) {", "\tif (0) {")]),
    ("BAR2 not limited", [(" ||\n\t    (space == SWW_BAR2 && !((win == 0 && off >= SWW_RAM_BASE) || (win == 0 && off == 0 && h->l3 >= 6 && h->rv_ok && !h->rv_done && v == h->rv) || (win == SWW_WIN_TAIL && off <= SWW_LAST_HI_MAX)))", "")]),
    # patch 0132: the one word at backplane address 0
    ("reset vector: no level gate", [("off == 0 && h->l3 >= 6 && h->rv_ok", "off == 0 && h->rv_ok")]),
    ("reset vector: level 5 is enough", [("h->l3 >= 6", "h->l3 >= 5")]),
    ("reset vector: once dropped", [("h->rv_ok && !h->rv_done && v == h->rv", "h->rv_ok && v == h->rv")]),
    ("reset vector: pin dropped", [("h->rv_ok && !h->rv_done && v == h->rv", "!h->rv_done && v == h->rv")]),
    ("reset vector: done flag never set", [("\tif (space == SWW_BAR2 && win == 0 && off == 0) {\n\t\th->rv_done = 1;\n\t}\n", "")]),
    ("reset vector: done flag set by every BAR2 store", [("if (space == SWW_BAR2 && win == 0 && off == 0) {\n\t\th->rv_done = 1;", "if (space == SWW_BAR2) {\n\t\th->rv_done = 1;")]),
    ("reset vector: any value", [("h->rv_ok && !h->rv_done && v == h->rv", "h->rv_ok && !h->rv_done")]),
    ("reset vector: any window", [("(win == 0 && off == 0 && h->l3", "(off == 0 && h->l3")]),
    ("reset vector: any of the first words", [("off == 0 && h->l3 >= 6", "off < 16 && h->l3 >= 6")]),
    ("reset vector: any word below the RAM base", [("off == 0 && h->l3 >= 6", "off < SWW_RAM_BASE && h->l3 >= 6")]),
    ("BAR2 store below the RAM base", [("(win == 0 && off >= SWW_RAM_BASE)", "(win == 0)")]),
    ("BAR2 store one word below the RAM base", [("off >= SWW_RAM_BASE)", "off >= SWW_RAM_BASE - 4u)")]),
    ("BAR2 tail window past the last word", [("off <= SWW_LAST_HI_MAX)", "off <= SWW_TCM_BYTES)")]),
    ("BAR2 tail window one word past the last", [("off <= SWW_LAST_HI_MAX)", "off <= SWW_LAST_HI_MAX + 4u)")]),
    ("BAR2 any window below the tail bound", [("(win == SWW_WIN_TAIL && off <= SWW_LAST_HI_MAX)", "(off <= SWW_LAST_HI_MAX)")]),
    ("BAR2 live window not compared", [(" || (space == SWW_BAR2 && h->cfg[SWW_CFG_WIN2 / 4] != win)", "")]),
    ("BAR2 compared against the BAR0 window", [("(space == SWW_BAR2 && h->cfg[SWW_CFG_WIN2 / 4] != win)", "(space == SWW_BAR2 && h->cfg[SWW_CFG_WIN / 4] != win)")]),
    ("store past the 4 MiB window", [("off < SWW_TCM_BYTES ? &h->tcm[off / 4]", "off <= SWW_TCM_BYTES ? &h->tcm[off / 4]")]),
    ("unaligned BAR2 store", [("if ((off & 3u) != 0) {\n\t\treturn NULL;", "if ((off & 1u) != 0) {\n\t\treturn NULL;")]),
    # patch 0133: the CA7 wrapper stores (sw3_ca7_ok / sw3_ca7_bit)
    ("CA7: the limit is not applied", [("if (!sw3_ca7_ok(h, space, win, off, v)) {", "if (!sw3_ca7_ok(h, space, win, off, v) && 0) {")]),
    ("CA7: the limit is applied to no window", [("if (space != SWW_BAR0 || win != SWW_WIN_CA7WRAP) {\n\t\treturn 1;", "if (1 || space != SWW_BAR0 || win != SWW_WIN_CA7WRAP) {\n\t\treturn 1;")]),
    ("CA7: the limit is applied to every BAR0 window", [("if (space != SWW_BAR0 || win != SWW_WIN_CA7WRAP) {\n\t\treturn 1;", "if (space != SWW_BAR0 || win == 0xFFFFFFFFu) {\n\t\treturn 1;")]),
    ("CA7: IOCTL takes any value", [("return v == 0x23u || v == 0x21u ||", "return 1 ||")]),
    ("CA7: IOCTL 0x23 refused", [("return v == 0x23u || v == 0x21u ||", "return v == 0x21u ||")]),
    ("CA7: IOCTL 0x21 refused", [("return v == 0x23u || v == 0x21u ||", "return v == 0x23u ||")]),
    ("CA7: the release IOCTL at level 6 and above", [("h->l3 >= 7", "h->l3 >= 6")]),
    ("CA7: the release IOCTL at level 8 only", [("h->l3 >= 7", "h->l3 >= 8")]),
    ("CA7: IOCTL 0x3 any number of times", [("(v == SWW_REL_MID && !(h->rel & 1u))", "(v == SWW_REL_MID)")]),
    ("CA7: IOCTL 0x1 without the starting store", [("(h->rel & 5u) == 5u", "(h->rel & 1u) == 1u")]),
    ("CA7: IOCTL 0x1 without any release", [(" && (h->rel & 5u) == 5u", "")]),
    ("CA7: IOCTL 0x1 any number of times", [(" && !(h->rel & 2u)", "")]),
    ("CA7: IOCTL 0x1 before IOCTL 0x3", [("(h->rel & 5u) == 5u", "(h->rel & 4u) == 4u")]),
    ("CA7: RESET_CTL takes any value", [("(v == 1u || (v == 0u &&", "(1 || (v == 0u &&")]),
    ("CA7: RESET_CTL 1 refused", [("(v == 1u || (v == 0u &&", "(0 || (v == 0u &&")]),
    ("CA7: RESET_CTL 0 at any time", [("(v == 0u && (!(h->rel & 1u) || (h->l3 >= 7 && !(h->rel & 4u))))", "(v == 0u)")]),
    ("CA7: RESET_CTL 0 never before the release", [("(v == 0u && (!(h->rel & 1u) || (h->l3 >= 7 && !(h->rel & 4u))))", "(v == 0u && (0 || (h->l3 >= 7 && !(h->rel & 4u))))")]),
    ("CA7: the starting RESET_CTL 0 any number of times", [("(h->l3 >= 7 && !(h->rel & 4u))", "(h->l3 >= 7)")]),
    ("CA7: the starting RESET_CTL 0 at any level", [("(h->l3 >= 7 && !(h->rel & 4u))", "(!(h->rel & 4u))")]),
    ("CA7: the starting RESET_CTL 0 never", [("(!(h->rel & 1u) || (h->l3 >= 7 && !(h->rel & 4u)))", "(!(h->rel & 1u))")]),
    ("CA7: state not recorded", [("\th->rel |= sw3_ca7_bit(h, space, win, off, v);\n", "\th->rel |= 0 * sw3_ca7_bit(h, space, win, off, v);\n")]),
    ("CA7: IOCTL 0x3 and 0x1 bits swapped", [("return v == SWW_REL_MID ? 1u : v == SWW_REL_END ? 2u : 0u;", "return v == SWW_REL_MID ? 2u : v == SWW_REL_END ? 1u : 0u;")]),
    ("CA7: IOCTL 0x1 not recorded", [("return v == SWW_REL_MID ? 1u : v == SWW_REL_END ? 2u : 0u;", "return v == SWW_REL_MID ? 1u : 0u;")]),
    ("CA7: IOCTL 0x3 not recorded", [("return v == SWW_REL_MID ? 1u : v == SWW_REL_END ? 2u : 0u;", "return v == SWW_REL_END ? 2u : 0u;")]),
    ("CA7: the starting store recorded without IOCTL 0x3", [("v == 0u && (h->rel & 1u) ? 4u : 0u", "v == 0u && (h->rel | 1u) ? 4u : 0u")]),
    ("CA7: the starting store not recorded", [("v == 0u && (h->rel & 1u) ? 4u : 0u", "v == 0u && (h->rel & 1u) && 0 ? 4u : 0u")]),
    ("CA7: bits recorded for every window", [("if (space != SWW_BAR0 || win != SWW_WIN_CA7WRAP) {\n\t\treturn 0;", "if (space != SWW_BAR0 || win == 0xFFFFFFFFu) {\n\t\treturn 0;")]),
    ("CA7: bits recorded for no window", [("if (space != SWW_BAR0 || win != SWW_WIN_CA7WRAP) {\n\t\treturn 0;", "if (1 || space != SWW_BAR0 || win != SWW_WIN_CA7WRAP) {\n\t\treturn 0;")]),
    # 22b: the DS-round entries (SW_G2K_*), sw_g2_exp + the R_* branches of sw3_g2_ok; the DS block at the end of GLUE_HARNESS must FAIL on each
    ("DS: R_WR needs old + 1", [("(vv & 0xFFFFu) == (old & 0xFFFFu) + 1u && ", "")]),
    ("DS: R_WR accepts +2", [("(vv & 0xFFFFu) == (old & 0xFFFFu) + 1u && ", "(vv & 0xFFFFu) <= (old & 0xFFFFu) + 2u && ")]),
    ("DS: R_WR depth not bounded", [("(vv & 0xFFFFu) < SW_G2_R0_ITEMS && ", "")]),
    ("DS: R_WR depth 129", [("(vv & 0xFFFFu) < SW_G2_R0_ITEMS && ", "(vv & 0xFFFFu) < SW_G2_R0_ITEMS + 1u && ")]),
    ("DS: R_WR upper half not kept", [(" && (vv & 0xFFFF0000u) == (old & 0xFFFF0000u)) {\n\t\t\t\tif (sw_g2_item_ok(", ") {\n\t\t\t\tif (sw_g2_item_ok(")]),
    ("DS: doorbell not tied to the slot", [("vv == ev + (*sl & 0xFFFFu) && ", "")]),
    ("DS: doorbell slot depth not bounded", [(" && (*sl & 0xFFFFu) < (e->kind == SW_G2K_R_WDB ? SW_G2_R0_ITEMS : SW_G2_R2_ITEMS)) {", ") {")]),
    ("DS: doorbell slot depths swapped", [("(e->kind == SW_G2K_R_WDB ? SW_G2_R0_ITEMS : SW_G2_R2_ITEMS)", "(e->kind == SW_G2K_R_WDB ? SW_G2_R2_ITEMS : SW_G2_R0_ITEMS)")]),
    ("DS: doorbells read the wrong slot", [("h->g2plan.b[e->kind == SW_G2K_R_WDB ? SWD_IDX_H2D_W : SWD_IDX_D2H_R].off", "h->g2plan.b[e->kind == SW_G2K_R_WDB ? SWD_IDX_D2H_R : SWD_IDX_H2D_W].off")]),
    ("DS: item index not bounded by rep", [(" || (off - eo) / e->stride >= e->rep) {", ") {")]),
    ("DS: item index bound off by one", [("(off - eo) / e->stride >= e->rep", "(off - eo) / e->stride > e->rep")]),
    ("DS: item stride not checked", [(" || (off - eo) % e->stride != 0u", "")]),
    # (no "item offset below the base not refused" mutant: equivalent. For off < eo the unsigned off - eo is 2^32 - d; it is a stride multiple only for d = 16 mod 40, and then (2^32 - d) / 40 = 107374181 >= rep (127), so the rep bound refuses it; the `off < eo` clause stays as a defensive guard (gate d4d17g survivor, shown equivalent by 22b-fix))
    ("DS: item word 0 epoch of index 0", [("*ev = (e->v & 0x00FFFFFFu) | SW_G2_D4D_EP(i);", "*ev = (e->v & 0x00FFFFFFu) | SW_G2_D4D_EP(i * 0u);")]),
    ("DS: item word 0 keeps the epoch byte of v", [("*ev = (e->v & 0x00FFFFFFu) | SW_G2_D4D_EP(i);", "*ev = e->v + i * 0u;")]),
    ("DS: exact item not moved by g2sh", [("\t\teo += 40u * sh;\n", "")]),
    ("DS: exact item moved by two items per g2sh", [("\t\teo += 40u * sh;\n", "\t\teo += 80u * sh;\n")]),
    ("DS: exact item word 0 epoch not moved", [("(((e->v >> 24) + sh) % 253u) << 24", "(((e->v >> 24) + 0u) % 253u) << 24")]),
    ("DS: exact slot value not moved", [("case SW_G2K_SH_WR:\n\t\t*ev = e->v + sh;", "case SW_G2K_SH_WR:\n\t\t*ev = e->v;")]),
    ("DS: exact slot old value not moved", [("*eold = e->old + sh;", "*eold = e->old;")]),
    ("DS: exact doorbell not moved", [("case SW_G2K_SH_DB:\n\t\t*ev = e->v + sh;", "case SW_G2K_SH_DB:\n\t\t*ev = e->v;")]),
    ("DS: g2sh not counted", [("\t\t\th->g2sh++;      /* a DS round's ring-0 item is posted", "\t\t\t/* a DS round's ring-0 item is posted")]),
    ("DS: g2sh counted for every store", [("if (h->g2l[g2i].kind == SW_G2K_R_WR) {\n\t\t\th->g2sh++;", "if (1) {\n\t\t\th->g2sh++;")]),
    ("DS: g2sh counted twice", [("\t\t\th->g2sh++;      /* a DS round's ring-0 item is posted", "\t\t\th->g2sh += 2u;      /* a DS round's ring-0 item is posted")]),
    ("DS: RD range compared as exact", [("e->vrng != 0u ? (vv & e->vmask) >= ev && (vv & e->vmask) - ev <= e->vrng : (vv & e->vmask) == ev", "(vv & e->vmask) == ev")]),
    ("DS: RD range one too wide", [("(vv & e->vmask) - ev <= e->vrng", "(vv & e->vmask) - ev <= e->vrng + 1u")]),
    # (no "lower bound dropped" mutant: the only ranged entry is the RD slot, v = 0, so (x >= 0) is always true: an equivalent mutant)
    # review 0174-w-d4e-scan24-hwwrite-r1 F1: the H2D-WR commit only over a complete DS-round item at the old slot
    ("F1: R_WR does not check the item", [("if (sw_g2_item_ok(h, old & 0xFFFFu)) {", "if (1) {")]),
    ("F1: R_WR checks the item after the new slot", [("sw_g2_item_ok(h, old & 0xFFFFu)", "sw_g2_item_ok(h, vv & 0xFFFFu)")]),
    ("F1: item epoch ignored", [("w[0] != (0x00800023u | SW_G2_D4D_EP(j))", "(w[0] & 0x00FFFFFFu) != 0x00800023u")]),
    ("F1: item mb data any value", [("(w[2] != 0x2u && w[2] != 0x40u && w[2] != 0x20u)", "0")]),
    ("F1: item mb 0x20 refused", [("(w[2] != 0x2u && w[2] != 0x40u && w[2] != 0x20u)", "(w[2] != 0x2u && w[2] != 0x40u)")]),
    ("F1: item mb 0x40 refused", [("(w[2] != 0x2u && w[2] != 0x40u && w[2] != 0x20u)", "(w[2] != 0x2u && w[2] != 0x20u)")]),
    ("F1: the other eight words unchecked", [("if (k != 2u && w[k] != 0u) {", "if (0) {")]),
    ("F1: item word 4 unchecked", [("if (k != 2u && w[k] != 0u) {", "if (k != 2u && k != 4u && w[k] != 0u) {")]),
    ("F1: item word 9 unchecked", [("for (k = 1; k < 10u; k++) {\n\t\tif (k != 2u", "for (k = 1; k < 9u; k++) {\n\t\tif (k != 2u")]),
    ("F1: item word 1 unchecked", [("for (k = 1; k < 10u; k++) {\n\t\tif (k != 2u", "for (k = 2; k < 10u; k++) {\n\t\tif (k != 2u")]),
    # F2: the 16 masked entries chained after the L16 C2 doorbell entry
    ("F2: sw_g2_ds not chained", [("bad |= sw_g2_ds(h, last) < 0;", "bad |= sw_g2_ds(h, -1) < 0;")]),
    ("F2: item mb entries not chained", [("mb[r], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need)", "mb[r], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, -1)")]),
    ("F2: item word entries not chained", [("k == 0u ? 0x00800023u : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need)", "k == 0u ? 0x00800023u : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, -1)")]),
    ("F2: zero item word entries not chained", [("k == 0u ? 0x00800023u : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need)", "k == 0u ? 0x00800023u : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, k == 0u ? need : -1)")]),
    ("F2: R_WR not chained", [("SWD_IDX_H2D_W].off, 0u, 0x0000FFFFu, 0u, 0u, 0, need)", "SWD_IDX_H2D_W].off, 0u, 0x0000FFFFu, 0u, 0u, 0, -1)")]),
    ("F2: R_WDB not chained", [("SW_G2_D4D_DB_WR, 0xFFFFFFFFu, 0, 0, 0, need)", "SW_G2_D4D_DB_WR, 0xFFFFFFFFu, 0, 0, 0, -1)")]),
    ("F2: R_RD not chained", [("SWD_IDX_D2H_R].off, 0u, 0x0000FFFFu, 0u, 0u, 0, need)", "SWD_IDX_D2H_R].off, 0u, 0x0000FFFFu, 0u, 0u, 0, -1)")]),
    ("F2: R_RDB not chained", [("SW_G2_D4D_DB_RD, 0xFFFFFFFFu, 0, 0, 0, need)", "SW_G2_D4D_DB_RD, 0xFFFFFFFFu, 0, 0, 0, -1)")]),
    ("F2: chained after the first exact entry (index 0)", [("bad |= sw_g2_ds(h, last) < 0;", "bad |= sw_g2_ds(h, 0) < 0;")]),
    # F3: the glue's own cap, items only at the slot's index, the exact slot below the ring depth
    ("F3a: cap not enforced", [("if (h->g2sh >= 3u * SW_G2_DS_MAX) {", "if (0) {")]),
    ("F3a: cap one too wide", [("h->g2sh >= 3u * SW_G2_DS_MAX", "h->g2sh > 3u * SW_G2_DS_MAX")]),
    ("F3a: cap two rounds", [("h->g2sh >= 3u * SW_G2_DS_MAX", "h->g2sh >= 2u * SW_G2_DS_MAX")]),
    ("F3b: round item at any index", [("if (sl == NULL || i != (*sl & 0xFFFFu)) {", "if (sl == NULL) {")]),
    ("F3b: round item below the slot too", [("if (sl == NULL || i != (*sl & 0xFFFFu)) {", "if (sl == NULL || i > (*sl & 0xFFFFu)) {")]),
    ("F3b: round item above the slot too", [("if (sl == NULL || i != (*sl & 0xFFFFu)) {", "if (sl == NULL || i < (*sl & 0xFFFFu)) {")]),
    ("F3c: exact slot not bound to ring 0", [("if ((*ev & 0xFFFFu) >= SW_G2_R0_ITEMS) {", "if (0) {")]),
    ("F3c: exact slot bound one too wide", [("if ((*ev & 0xFFFFu) >= SW_G2_R0_ITEMS) {", "if ((*ev & 0xFFFFu) > SW_G2_R0_ITEMS) {")]),
] + E_GLUE_ROWS + L25R_GLUE_ROWS + L25A_GLUE_ROWS


# ---- patch 0174, review 0174-g2-hwwrite-r2 F3: sw_g2_add / sw_g2_tcm / sw_g2_alias_ok / sw_g2_list compiled and executed (the list sw3_wr's G2 branch takes stores from) ----
from wlan_g2l_harness_a import G2L_HARNESS_A
from wlan_g2l_harness_b import G2L_HARNESS_B

G2L_HARNESS = G2L_HARNESS_A + G2L_HARNESS_B     # T-0a9d (R29): the C text lives in tools/wlan_g2l_harness_a.py and _b.py


def g2_list_text():
    """The glue from `#define SW_G2_FLAGS2_OLD` to the closing brace of sw_g2_list (patch 0174): the G2 list and its helpers."""
    g = glue_after()
    a = g.find("#define SW_G2_FLAGS2_OLD")
    e = re.search(r"^sw_g2_list\(.*?^}$", g, re.S | re.M)
    if a < 0 or not e or e.start() < a:
        raise ValueError("SW_G2_FLAGS2_OLD..sw_g2_list not found in the glue")
    return g[a:e.end()] + "\n"


def run_g2_list(list_text, work, extra=()):
    """Compile sw3_ptr..sw3_wr + the G2 list text against the real headers and run G2L_HARNESS. Returns ('pass'|'fail'|'compile', output)."""
    d = tempfile.mkdtemp(dir=work)
    for text, name in ((chip_header(), "sweet_wlan_chip.h"), (final_header(), "sweet_wlan_wr.h"), (new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h"), "sweet_wlan_dma.h"), (l25wl_header(), "sweet_wlan_l25_wl.h")):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(d, "l.c"), "w", encoding="utf-8") as fh:
        fh.write(G2L_HARNESS.replace("@GLUE@", glue_core_text() + list_text))
    c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I" + d] + list(extra) + ["-o", os.path.join(d, "l"), os.path.join(d, "l.c")], capture_output=True, text=True)
    if c.returncode != 0:
        return "compile", c.stderr[-600:]
    r = run_bounded([os.path.join(d, "l")], d, 60)
    return ("pass" if r[0] == 0 else "fail"), r[2]


# one per check of the list and its helpers; G2L_HARNESS must FAIL on each. Equivalent mutants left out on purpose (rewritten for W-D3c, review r4 LOW 2):
# - sw_g2_rdw's own clauses (alignment, RAM base, RAM end): every caller passes sh + 0x30 or ri + 0x00..0x10 after sh / ri passed their own aligned [SWW_RAM_BASE, end - 0x60 / end - 0x9C] clauses; defence in depth;
# - sw_g2_win2's read-back `h->cfg[SWW_CFG_WIN2 / 4] != win`: a store that did not take leaves the live window wrong, and sw3_rd / sw3_g2_ok compare against that live value, so the next read or store
#   refuses anyway (the harness's cfg store always takes);
# - the sw3_rd result inside sw_g2_rdw (cannot fail for a BAR2 offset < SWW_TCM_BYTES on the live window) and the DBI read result (a failed read leaves rcold all-ones, refused);
# - `rc < 0` / `ep < 0` (sw_g2_add fails only on a full list: the next add fails too), `once ? 1 : 0` with once 0/1 in the list, `items & 0xFFFF` (every ring < 0x10000 items),
#   the ring_info overlap size and the ring_mem overlap array size (ring_mem = ring_info + 0x4C: an array crossing that boundary overlaps the other block too) (review r3 LOW 2).
def _d4c_g2l():
    """W-D4c (sweetwlan=16): the 40 extra list entries (A1 ack, C1 read index, W wake, D deassert, C2 read index) built by sw_g2_d4c_post / sw_g2_d4c_rd from sw_g2_d4. One mutant per gate, doorbell value and offset, item word 0 (epoch), mb value,
    slot index, old value, chain link, once flag, space, mask, item stride and order; G2L_HARNESS (the literal restatement in want()) must FAIL on each."""
    g = "if (h->d4b >= 3) {      /* W-D4c"
    post_item = "last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + itm * SW_G2_D4C_ISZ + 4u * k, k == 0u ? w0 : k == 2u ? mb : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last);"
    post_slot = "last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_H2D_W].off, idx, 0x0000FFFFu, idx - 1u, 0xFFFFFFFFu, 1, last);"
    rd_slot = "last = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_D2H_R].off, idx, 0x0000FFFFu, idx - 1u, 0xFFFFFFFFu, 1, last);"
    db = "return sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x140u, db, 0xFFFFFFFFu, 0, 0, 1, last);"
    post_db = post_slot + "\n\tif (last < 0) {\n\t\treturn -1;\n\t}\n\t" + db       # the doorbell row of sw_g2_d4c_post (its slot row precedes it)
    rd_db = rd_slot + "\n\tif (last < 0) {\n\t\treturn -1;\n\t}\n\t" + db
    c_ack = "last = sw_g2_d4c_post(h, 1u, SW_G2_D4C_W0_ACK, SW_G2_D4C_MB_ACK, 2u, SW_G2_D4C_DB0_ACK, last);"
    c_rd1 = "last = last < 0 ? -1 : sw_g2_d4c_rd(h, 1u, SW_G2_D4C_DB0_RD1, last);"
    c_wake = "last = last < 0 ? -1 : sw_g2_d4c_post(h, 2u, SW_G2_D4C_W0_WAKE, SW_G2_D4C_MB_WAKE, 3u, SW_G2_D4C_DB0_WAKE, last);"
    c_deas = "last = last < 0 ? -1 : sw_g2_d4c_post(h, 3u, SW_G2_D4C_W0_DEAS, SW_G2_D4C_MB_DEAS, 4u, SW_G2_D4C_DB0_DEAS, last);"
    c_rd2 = "last = last < 0 ? -1 : sw_g2_d4c_rd(h, 2u, SW_G2_D4C_DB0_RD2, last);"
    out = [
        ("d4c: list gate off", [(g, "if (0) {      /* W-D4c")]),
        ("d4c: list gate on at sweetwlan=14", [(g, "if (h->d4b >= 2) {      /* W-D4c")]),
        ("d4c: list gate always on", [(g, "if (1) {      /* W-D4c")]),
        ("d4c: ack doorbell value", [("#define SW_G2_D4C_DB0_ACK  0xFF000002u", "#define SW_G2_D4C_DB0_ACK  0xFF000001u")]),
        ("d4c: rd1 doorbell value", [("#define SW_G2_D4C_DB0_RD1  0xDD020001u", "#define SW_G2_D4C_DB0_RD1  0xDD020000u")]),
        ("d4c: wake doorbell value", [("#define SW_G2_D4C_DB0_WAKE 0xFF000003u", "#define SW_G2_D4C_DB0_WAKE 0xFF000002u")]),
        ("d4c: deas doorbell value", [("#define SW_G2_D4C_DB0_DEAS 0xFF000004u", "#define SW_G2_D4C_DB0_DEAS 0xFF000003u")]),
        ("d4c: rd2 doorbell value", [("#define SW_G2_D4C_DB0_RD2  0xDD020002u", "#define SW_G2_D4C_DB0_RD2  0xDD020001u")]),
        ("d4c: ack item epoch", [("#define SW_G2_D4C_W0_ACK   0x02800023u", "#define SW_G2_D4C_W0_ACK   0x01800023u")]),
        ("d4c: wake item epoch", [("#define SW_G2_D4C_W0_WAKE  0x03800023u", "#define SW_G2_D4C_W0_WAKE  0x02800023u")]),
        ("d4c: deas item epoch", [("#define SW_G2_D4C_W0_DEAS  0x04800023u", "#define SW_G2_D4C_W0_DEAS  0x03800023u")]),
        ("d4c: ack item type or phase", [("#define SW_G2_D4C_W0_ACK   0x02800023u", "#define SW_G2_D4C_W0_ACK   0x02000023u")]),
        ("d4c: ack mb value", [("#define SW_G2_D4C_MB_ACK   0x2u", "#define SW_G2_D4C_MB_ACK   0x4u")]),
        ("d4c: wake mb value", [("#define SW_G2_D4C_MB_WAKE  0x40u", "#define SW_G2_D4C_MB_WAKE  0x20u")]),
        ("d4c: deas mb value", [("#define SW_G2_D4C_MB_DEAS  0x20u", "#define SW_G2_D4C_MB_DEAS  0x40u")]),
        ("d4c: ack item mb is the wake one", [(c_ack, c_ack.replace("SW_G2_D4C_MB_ACK", "SW_G2_D4C_MB_WAKE"))]),
        ("d4c: wake item mb is the deas one", [(c_wake, c_wake.replace("SW_G2_D4C_MB_WAKE", "SW_G2_D4C_MB_DEAS"))]),
        ("d4c: deas item mb is the ack one", [(c_deas, c_deas.replace("SW_G2_D4C_MB_DEAS", "SW_G2_D4C_MB_ACK"))]),
        ("d4c: ack item index", [(c_ack, c_ack.replace("post(h, 1u,", "post(h, 2u,"))]),
        ("d4c: wake item index", [(c_wake, c_wake.replace("post(h, 2u,", "post(h, 3u,"))]),
        ("d4c: deas item index", [(c_deas, c_deas.replace("post(h, 3u,", "post(h, 4u,"))]),
        ("d4c: ack slot value", [(c_ack, c_ack.replace("MB_ACK, 2u,", "MB_ACK, 3u,"))]),
        ("d4c: wake slot value", [(c_wake, c_wake.replace("MB_WAKE, 3u,", "MB_WAKE, 4u,"))]),
        ("d4c: deas slot value", [(c_deas, c_deas.replace("MB_DEAS, 4u,", "MB_DEAS, 5u,"))]),
        ("d4c: rd1 slot value", [(c_rd1, c_rd1.replace("rd(h, 1u,", "rd(h, 2u,"))]),
        ("d4c: rd2 slot value", [(c_rd2, c_rd2.replace("rd(h, 2u,", "rd(h, 3u,"))]),
        ("d4c: ack not chained after the L14 doorbell", [(c_ack, c_ack.replace("DB0_ACK, last)", "DB0_ACK, -1)"))]),
        ("d4c: rd1 not chained after the ack", [(c_rd1, c_rd1.replace("sw_g2_d4c_rd(h, 1u, SW_G2_D4C_DB0_RD1, last)", "sw_g2_d4c_rd(h, 1u, SW_G2_D4C_DB0_RD1, -1)"))]),
        ("d4c: wake not chained after rd1", [(c_wake, c_wake.replace("DB0_WAKE, last)", "DB0_WAKE, -1)"))]),
        ("d4c: deas not chained after the wake", [(c_deas, c_deas.replace("DB0_DEAS, last)", "DB0_DEAS, -1)"))]),
        ("d4c: rd2 not chained after the deas", [(c_rd2, c_rd2.replace("sw_g2_d4c_rd(h, 2u, SW_G2_D4C_DB0_RD2, last)", "sw_g2_d4c_rd(h, 2u, SW_G2_D4C_DB0_RD2, -1)"))]),
        ("d4c: ack before rd1 (order)", [(c_ack + "\n\t\t" + c_rd1, c_rd1.replace("last < 0 ? -1 : ", "") + "\n\t\t" + c_ack.replace("last = ", "last = last < 0 ? -1 : "))]),
        ("d4c: wake before rd1 (order)", [(c_rd1 + "\n\t\t" + c_wake, c_wake.replace("last < 0 ? -1 : ", "") + "\n\t\t" + c_rd1)]),
        ("d4c: deas before wake (order)", [(c_wake + "\n\t\t" + c_deas, c_deas + "\n\t\t" + c_wake)]),
        ("d4c: rd2 before deas (order)", [(c_deas + "\n\t\t" + c_rd2, c_rd2 + "\n\t\t" + c_deas)]),
        ("d4c: item size", [("#define SW_G2_D4C_ISZ      40u", "#define SW_G2_D4C_ISZ      36u")]),
        ("d4c: item at the previous item's place", [(post_item, post_item.replace("itm * SW_G2_D4C_ISZ", "(itm - 1u) * SW_G2_D4C_ISZ"))]),
        ("d4c: item stride", [(post_item, post_item.replace("itm * SW_G2_D4C_ISZ + 4u * k", "itm * SW_G2_D4C_ISZ + 8u * k"))]),
        ("d4c: item mb_data at word 3", [(post_item, post_item.replace("k == 2u ? mb", "k == 3u ? mb"))]),
        ("d4c: item word 0 carries the mb", [(post_item, post_item.replace("k == 0u ? w0", "k == 0u ? w0 | mb"))]),
        ("d4c: nine item words", [("for (k = 0; k < 10u; k++) {\n\t\tlast = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + itm", "for (k = 0; k < 9u; k++) {\n\t\tlast = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off + itm")]),
        ("d4c: item words not chained", [(post_item, post_item.replace("1, last);", "1, -1);"))]),
        ("d4c: item old not pinned", [(post_item, post_item.replace("0, 0xFFFFFFFFu, 1, last);", "0, 0, 1, last);"))]),
        ("d4c: item not once", [(post_item, post_item.replace("1, last);", "0, last);"))]),
        ("d4c: item in the TCM space", [(post_item, post_item.replace("SW3_HOST, 0, h->g2plan", "SWW_BAR2, 0, h->g2plan"))]),
        ("d4c: item half mask", [(post_item, post_item.replace("0 : 0u, 0xFFFFFFFFu, 0,", "0 : 0u, 0x0000FFFFu, 0,").replace("mb : 0u, 0xFFFFFFFFu, 0,", "mb : 0u, 0x0000FFFFu, 0,"))]),
        ("d4c: post slot is the h2d_r array", [(post_slot, post_slot.replace("SWD_IDX_H2D_W", "SWD_IDX_H2D_R"))]),
        ("d4c: post slot whole word", [(post_slot, post_slot.replace("idx, 0x0000FFFFu,", "idx, 0xFFFFFFFFu,"))]),
        ("d4c: post slot old is 0", [(post_slot, post_slot.replace("idx - 1u, 0xFFFFFFFFu", "0, 0xFFFFFFFFu"))]),
        ("d4c: post slot old is the new index", [(post_slot, post_slot.replace("idx - 1u, 0xFFFFFFFFu", "idx, 0xFFFFFFFFu"))]),
        ("d4c: post slot old not pinned", [(post_slot, post_slot.replace("idx - 1u, 0xFFFFFFFFu", "idx - 1u, 0"))]),
        ("d4c: post slot not chained after the items", [(post_slot, post_slot.replace("1, last);", "1, -1);"))]),
        ("d4c: post slot not once", [(post_slot, post_slot.replace("1, last);", "0, last);"))]),
        ("d4c: post slot in the TCM space", [(post_slot, post_slot.replace("SW3_HOST, 0,", "SWW_BAR2, 0,"))]),
        ("d4c: post doorbell offset", [(post_db, post_db.replace("0x140u", "0x144u"))]),
        ("d4c: post doorbell not chained after the slot", [(post_db, post_db.replace("0, 0, 1, last)", "0, 0, 1, -1)"))]),
        ("d4c: post doorbell not once", [(post_db, post_db.replace("0, 0, 1, last)", "0, 0, 0, last)"))]),
        ("d4c: post doorbell value mask", [(post_db, post_db.replace("db, 0xFFFFFFFFu, 0, 0, 1", "db, 0x0000FFFFu, 0, 0, 1"))]),
        ("d4c: post doorbell in the wrong window", [(post_db, post_db.replace("SWW_WIN_PCIE", "SWW_WIN_SYSMEM"))]),
        ("d4c: rd slot is the d2h_w array", [(rd_slot, rd_slot.replace("SWD_IDX_D2H_R", "SWD_IDX_D2H_W"))]),
        ("d4c: rd slot whole word", [(rd_slot, rd_slot.replace("idx, 0x0000FFFFu,", "idx, 0xFFFFFFFFu,"))]),
        ("d4c: rd slot old is 0", [(rd_slot, rd_slot.replace("idx - 1u, 0xFFFFFFFFu", "0, 0xFFFFFFFFu"))]),
        ("d4c: rd slot old is the new index", [(rd_slot, rd_slot.replace("idx - 1u, 0xFFFFFFFFu", "idx, 0xFFFFFFFFu"))]),
        ("d4c: rd slot old not pinned", [(rd_slot, rd_slot.replace("idx - 1u, 0xFFFFFFFFu", "idx - 1u, 0"))]),
        ("d4c: rd slot not chained after the previous doorbell", [(rd_slot, rd_slot.replace("1, last);", "1, -1);"))]),
        ("d4c: rd slot not once", [(rd_slot, rd_slot.replace("1, last);", "0, last);"))]),
        ("d4c: rd slot in the TCM space", [(rd_slot, rd_slot.replace("SW3_HOST, 0,", "SWW_BAR2, 0,"))]),
        ("d4c: rd doorbell offset", [(rd_db, rd_db.replace("0x140u", "0x144u"))]),
        ("d4c: rd doorbell not chained after the slot", [(rd_db, rd_db.replace("0, 0, 1, last)", "0, 0, 1, -1)"))]),
        ("d4c: rd doorbell not once", [(rd_db, rd_db.replace("0, 0, 1, last)", "0, 0, 0, last)"))]),
        ("d4c: rd doorbell in the wrong window", [(rd_db, rd_db.replace("SWW_WIN_PCIE", "SWW_WIN_SYSMEM"))]),
    ]
    return out


# mutant-block: wlan-g2l
G2L_MUTANTS = [
    # W-D4b D-12: G2 has no cfg 0x88 entry; the 16-bit slot entries (sweetwlan=14) and the chain of the d4 entries on hostready (review r1 F7)
    ("F7: mask not chained after hostready", [("SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, ready)", "SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, -1)")]),
    ("F7: mask chained after the wrong entry", [("bad |= sw_g2_d4(h, ready) < 0;", "bad |= sw_g2_d4(h, 0) < 0;")]),
    ("F7: d4 built without hostready", [("bad |= sw_g2_d4(h, ready) < 0;", "bad |= sw_g2_d4(h, -2) < 0;")]),
    ("F7: hostready entry is not the one captured", [("ready = sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x144u", "ready = (int)h->g2n - 1; sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x144u")]),
    ("F7: d4 chained at the level of the observation", [("if (h->d4b >= 2) {      /* W-D4b: only at sweetwlan>=14 (stores), chained", "if (h->d4b >= 1) {      /* W-D4b: only at sweetwlan>=14 (stores), chained")]),
    ("tcm16: slots always 32-bit", [("(h->d4b >= 2 ? sw_g2_tcm16(", "(0 ? sw_g2_tcm16(")]),
    ("tcm16: slots 16-bit at every level", [("(h->d4b >= 2 ? sw_g2_tcm16(", "(1 ? sw_g2_tcm16(")]),
    ("tcm16: slots 16-bit at the read-only level", [("(h->d4b >= 2 ? sw_g2_tcm16(", "(h->d4b >= 1 ? sw_g2_tcm16(")]),
    ("tcm16: slot value", [("sw_g2_tcm16(h, end, ra[k] + 4u * i, 0u)", "sw_g2_tcm16(h, end, ra[k] + 4u * i, 1u)")]),
    ("tcm16: not marked w16", [("\t\th->g2l[r].w16 = 1;\n", "")]),
    ("tcm16: whole word value mask", [("sw_g2_tcm(h, end, bp, v, 0x0000FFFFu, 0, 0);\n\n\tif (r >= 0)", "sw_g2_tcm(h, end, bp, v, 0xFFFFFFFFu, 0, 0);\n\n\tif (r >= 0)")]),
    ("tcm16: old pinned", [("sw_g2_tcm(h, end, bp, v, 0x0000FFFFu, 0, 0);\n\n\tif (r >= 0)", "sw_g2_tcm(h, end, bp, v, 0x0000FFFFu, 0, 0xFFFFFFFFu);\n\n\tif (r >= 0)")]),
    ("tcm16: low half not constrained", [("sw_g2_tcm(h, end, bp, v, 0x0000FFFFu, 0, 0);\n\n\tif (r >= 0)", "sw_g2_tcm(h, end, bp, v, 0u, 0, 0);\n\n\tif (r >= 0)")]),
    ("add: w16 not cleared", [("\te->w16 = 0;\n", "")]),
    # W-D4b: sw_g2_d4 (sweetwlan=14) and its gate
    ("d4: gate always on", [("if (h->d4b >= 2) {      /* W-D4b", "if (1) {      /* W-D4b")]),
    ("d4: gate on at the read-only level", [("if (h->d4b >= 2) {      /* W-D4b", "if (h->d4b >= 1) {      /* W-D4b")]),
    ("d4: gate off", [("if (h->d4b >= 2) {      /* W-D4b", "if (0) {      /* W-D4b")]),
    ("d4: mask value", [("#define SW_G2_D4_MASK    0xFFFFu", "#define SW_G2_D4_MASK    0xFFFEu")]),
    ("d4: mask offset", [("SWW_WIN_PCIE, 0xC34u, SW_G2_D4_MASK", "SWW_WIN_PCIE, 0xC38u, SW_G2_D4_MASK")]),
    ("d4: mask old not pinned", [("SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, ready)", "SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0, 1, ready)")]),
    ("d4: mask not once", [("SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, ready)", "SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, ready)")]),
    ("d4: mask value mask", [("SW_G2_D4_MASK, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, ready)", "SW_G2_D4_MASK, 0x0000FFFFu, 0, 0xFFFFFFFFu, 1, ready)")]),
    ("d4: mask in the wrong window", [("sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, SW_G2_D4_MASK", "sw_g2_add(h, SWW_BAR0, SWW_WIN_SYSMEM, 0xC34u, SW_G2_D4_MASK")]),
    ("d4: item word 0", [("#define SW_G2_D4_W0      0x0180000Du", "#define SW_G2_D4_W0      0x0100000Du")]),
    ("d4: item length", [("#define SW_G2_D4_LEN     0x2000u", "#define SW_G2_D4_LEN     0x1000u")]),
    ("d4: request id", [("k == 1u ? 1u :", "k == 1u ? 2u :")]),
    ("d4: buffer lo/hi swapped", [("k == 4u ? (uint32_t)pa : k == 5u ? (uint32_t)(pa >> 32)", "k == 4u ? (uint32_t)(pa >> 32) : k == 5u ? (uint32_t)pa")]),
    ("d4: buffer hi dropped", [("k == 5u ? (uint32_t)(pa >> 32)", "k == 5u ? 0u")]),
    ("d4: buffer pa is the block", [("uint64_t pa = h->g2pa + h->g2plan.b[SWD_EVBUF].off;", "uint64_t pa = h->g2pa;")]),
    ("d4: item base is ring 1", [("h->g2plan.b[SWD_H2D_CTRL].off + 4u * k", "h->g2plan.b[SWD_H2D_RXP].off + 4u * k")]),
    ("d4: item stride", [("h->g2plan.b[SWD_H2D_CTRL].off + 4u * k", "h->g2plan.b[SWD_H2D_CTRL].off + 8u * k")]),
    ("d4: nine item words", [("for (k = 0; k < 10u; k++) {\n\t\tlast = sw_g2_add(h, SW3_HOST", "for (k = 0; k < 9u; k++) {\n\t\tlast = sw_g2_add(h, SW3_HOST")]),
    ("d4: item not after the mask", [("0xFFFFFFFFu, 1, k == 0u ? m : last);", "0xFFFFFFFFu, 1, -1);")]),
    ("d4: item old not pinned", [("0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, k == 0u ? m : last);", "0xFFFFFFFFu, 0, 0, 1, k == 0u ? m : last);")]),
    ("d4: item not once", [("0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, k == 0u ? m : last);", "0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, k == 0u ? m : last);")]),
    ("d4: item in the TCM space", [("sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_H2D_CTRL].off", "sw_g2_add(h, SWW_BAR2, 0, h->g2plan.b[SWD_H2D_CTRL].off")]),
    ("d4: slot offset is h2d_r", [("h->g2plan.b[SWD_IDX_H2D_W].off, 1u,", "h->g2plan.b[SWD_IDX_H2D_R].off, 1u,")]),
    ("d4: slot value", [("h->g2plan.b[SWD_IDX_H2D_W].off, 1u, 0x0000FFFFu", "h->g2plan.b[SWD_IDX_H2D_W].off, 2u, 0x0000FFFFu")]),
    ("d4: slot whole word", [("1u, 0x0000FFFFu, 0, 0xFFFFFFFFu, 1, last)", "1u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, last)")]),
    ("d4: slot not after the items", [("1u, 0x0000FFFFu, 0, 0xFFFFFFFFu, 1, last)", "1u, 0x0000FFFFu, 0, 0xFFFFFFFFu, 1, -1)")]),
    ("d4: doorbell value", [("#define SW_G2_D4_DB0     0xFF000001u", "#define SW_G2_D4_DB0     0xFF000000u")]),
    ("d4: doorbell offset", [("0x140u, SW_G2_D4_DB0", "0x144u, SW_G2_D4_DB0")]),
    ("d4: doorbell not after the slot", [("SW_G2_D4_DB0, 0xFFFFFFFFu, 0, 0, 1, last)", "SW_G2_D4_DB0, 0xFFFFFFFFu, 0, 0, 1, -1)")]),
    ("d4: doorbell not once", [("SW_G2_D4_DB0, 0xFFFFFFFFu, 0, 0, 1, last)", "SW_G2_D4_DB0, 0xFFFFFFFFu, 0, 0, 0, last)")]),
    ("d4: no unmask entry", [("bad |= last < 0 || sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0, 0xFFFFFFFFu, 0, 0, 1, m) < 0;", "bad |= last < 0;")]),
    ("d4: unmask value", [("0xC34u, 0, 0xFFFFFFFFu, 0, 0, 1, m)", "0xC34u, 1, 0xFFFFFFFFu, 0, 0, 1, m)")]),
    ("d4: unmask without the mask first", [("0xC34u, 0, 0xFFFFFFFFu, 0, 0, 1, m)", "0xC34u, 0, 0xFFFFFFFFu, 0, 0, 1, -1)")]),
    ("d4: unmask old pinned", [("0xC34u, 0, 0xFFFFFFFFu, 0, 0, 1, m)", "0xC34u, 0, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, m)")]),
    ("add: no full-list check", [("if (h->g2n >= SW_G2_MAX) {", "if (0) {")]),
    ("add: full-list check off by one", [("if (h->g2n >= SW_G2_MAX) {", "if (h->g2n > SW_G2_MAX) {")]),
    ("add: done not cleared", [("\te->done = 0;\n", "")]),
    ("add: once not normalised", [("e->once = once ? 1 : 0;", "e->once = (uint8_t)once;")]),
    ("tcm: no alignment check", [("(bp & 3u) != 0 || bp < SWW_RAM_BASE", "bp < SWW_RAM_BASE")]),
    ("tcm: no RAM base check", [("|| bp < SWW_RAM_BASE || bp > end - 4u", "|| bp > end - 4u")]),
    ("tcm: RAM end check off", [("bp > end - 4u", "bp > end")]),
    ("tcm: RAM end check off by two", [("bp > end - 4u", "bp > end - 2u")]),
    ("tcm: tail window from above 0x400000", [("bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u", "bp > SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u")]),
    ("tcm: not once", [("return sw_g2_add(h, SWW_BAR2, win, bp - win, v, vmask, old, omask, 1, -1);", "return sw_g2_add(h, SWW_BAR2, win, bp - win, v, vmask, old, omask, 0, -1);")]),
    ("alias: no below-4-GiB pass", [("return (pa >> 32) == 0u || (", "return (")]),
    ("alias: whole pa compared", [("uint64_t lo = pa & 0xFFFFFFFFull;", "uint64_t lo = pa;")]),
    ("alias: LO exclusive", [("lo >= SW_G2_ALIAS_LO", "lo > SW_G2_ALIAS_LO")]),
    ("alias: no total bound (wrap)", [("total <= SW_G2_ALIAS_HI - SW_G2_ALIAS_LO && ", "")]),
    ("alias: end exclusive", [("lo <= (uint64_t)(SW_G2_ALIAS_HI - total)", "lo < (uint64_t)(SW_G2_ALIAS_HI - total)")]),
    ("alias: LO value", [("#define SW_G2_ALIAS_LO   0x822A0000u", "#define SW_G2_ALIAS_LO   0x82290000u")]),
    ("alias: HI value", [("#define SW_G2_ALIAS_HI   0x86000000u", "#define SW_G2_ALIAS_HI   0x86004000u")]),
    ("list: not reset", [("\th->g2n = 0;\n\th->g2ev = 0;", "\th->g2ev = 0;")]),
    ("list: 0x80 window entries swapped", [("sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN, SWW_WIN_PCIE, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN, w80, 0xFFFFFFFFu, 0, 0, 0, -1) < 0", "sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN, w80, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN, SWW_WIN_PCIE, 0xFFFFFFFFu, 0, 0, 0, -1) < 0")]),
    ("list: 0x84 window entries swapped", [("sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL, 0xFFFFFFFFu, 0, 0, 0, -1) < 0", "sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 0, -1) < 0")]),
    ("list: window entries after the 0x80 pair and the reads", [("\tbad |= sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL, 0xFFFFFFFFu, 0, 0, 0, -1) < 0;\n", ""), ("\tif (why == NULL) {\n\t\tbad |= sw_g2_tcm(h, end, sh + 0x50u", "\tbad |= sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 0, -1) < 0 || sw_g2_add(h, SWW_CFG, 0, SWW_CFG_WIN2, SWW_WIN_TAIL, 0xFFFFFFFFu, 0, 0, 0, -1) < 0;\n\tif (why == NULL) {\n\t\tbad |= sw_g2_tcm(h, end, sh + 0x50u")]),
    ("rdw: tail window from above 0x400000", [("sw_g2_rdw(struct sw_hw *h, uint32_t end, uint32_t bp, uint32_t *v)\n{\n\tuint32_t win = bp >= SWW_WIN_TAIL ?", "sw_g2_rdw(struct sw_hw *h, uint32_t end, uint32_t bp, uint32_t *v)\n{\n\tuint32_t win = bp > SWW_WIN_TAIL ?")]),
    ("rdw: always window 0", [("sw_g2_rdw(struct sw_hw *h, uint32_t end, uint32_t bp, uint32_t *v)\n{\n\tuint32_t win = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "sw_g2_rdw(struct sw_hw *h, uint32_t end, uint32_t bp, uint32_t *v)\n{\n\tuint32_t win = 0u;")]),
    ("rdw: window kept after a read (always tail)", [("sw_g2_rdw(struct sw_hw *h, uint32_t end, uint32_t bp, uint32_t *v)\n{\n\tuint32_t win = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "sw_g2_rdw(struct sw_hw *h, uint32_t end, uint32_t bp, uint32_t *v)\n{\n\tuint32_t win = bp >= 0x300000u ? SWW_WIN_TAIL : 0u;")]),
    ("win2: stores even when already there", [("\tif (h->cfg[SWW_CFG_WIN2 / 4] == win) {\n\t\treturn 0;\n\t}\n", "")]),
    ("list: window not put back", [("if (sw_g2_win2(h, 0u) != 0) {      /* the reads", "if (0) {      /* the reads")]),
    ("list: window not put back on a refusal", [("if (sw_g2_win2(h, 0u) != 0) {      /* the reads", "if (why == NULL && sw_g2_win2(h, 0u) != 0) {      /* the reads")]),
    ("list: window restore failure ignored", [("if (sw_g2_win2(h, 0u) != 0) {      /* the reads", "if ((void)sw_g2_win2(h, 0u), 0) {      /* the reads")]),
    ("list: window move failure ignored (read side)", [("if (sw_g2_win2(h, win) != 0) {\n\t\treturn -2;", "if ((void)sw_g2_win2(h, win), 0) {\n\t\treturn -2;")]),
    ("list: no shared alignment check", [("(sh & 3u) != 0 || sh < SWW_RAM_BASE", "sh < SWW_RAM_BASE")]),
    ("list: no shared RAM base bound", [("sh < SWW_RAM_BASE || end < SWW_RAM_BASE + 0x9Cu", "end < SWW_RAM_BASE + 0x9Cu")]),
    ("list: no shared top bound", [(" || sh > end - 0x60u", "")]),
    ("list: shared top bound off by 4", [("sh > end - 0x60u", "sh > end - 0x5Cu")]),
    ("list: shared top bound too tight by 4", [("sh > end - 0x60u", "sh > end - 0x64u")]),
    ("list: no tiny-end guard", [("end < SWW_RAM_BASE + 0x9Cu || ", "")]),
    ("list: no ring_info alignment check", [("(ri & 3u) != 0 || ri < SWW_RAM_BASE", "ri < SWW_RAM_BASE")]),
    ("list: no ring_info RAM base bound", [("ri < SWW_RAM_BASE || ri > end - 0x9Cu", "ri > end - 0x9Cu")]),
    ("list: no ring_info top bound", [(" || ri > end - 0x9Cu", "")]),
    ("list: ring_info top bound off by 4", [("ri > end - 0x9Cu", "ri > end - 0x98u")]),
    ("list: ring_info top bound too tight by 4", [("ri > end - 0x9Cu", "ri > end - 0xA0u")]),
    ("list: no ring_info/shared overlap check", [("if (why == NULL && !(ri + 0x9Cu <= sh || sh + 0x60u <= ri)) {", "if (0) {")]),
    ("list: ring_info below shared only", [("!(ri + 0x9Cu <= sh || sh + 0x60u <= ri)", "!(ri + 0x9Cu <= sh)")]),
    ("list: ring_info above shared only", [("!(ri + 0x9Cu <= sh || sh + 0x60u <= ri)", "!(sh + 0x60u <= ri)")]),
    ("list: ring_info/shared overlap block 0x98", [("ri + 0x9Cu <= sh ||", "ri + 0x98u <= sh ||")]),
    ("list: ring_info/shared overlap shared 0x5C", [("sh + 0x60u <= ri)) {\n\t\twhy = \"ring_info/shared overlap\"", "sh + 0x5Cu <= ri)) {\n\t\twhy = \"ring_info/shared overlap\"")]),
    ("list: ringmem_ptr not checked", [("if (why == NULL && rm != ri + 0x4Cu) {", "if (0) {")]),
    ("list: index array RAM end bound missing", [(" || ra[k] > end - 16u", "")]),
    ("list: index array RAM base bound missing", [("ra[k] < SWW_RAM_BASE || ", "")]),
    ("list: index array alignment unchecked (review r4 LOW 1)", [("(ra[k] & 3u) != 0 || ra[k] < SWW_RAM_BASE", "ra[k] < SWW_RAM_BASE")]),
    ("list: index array RAM end bound off by 4", [("ra[k] > end - 16u", "ra[k] > end - 12u")]),
    ("list: index arrays read 3 of 4", [("for (k = 0; k < 4u && why == NULL; k++) {\n\t\tra[k] = 0;", "for (k = 0; k < 3u && why == NULL; k++) {\n\t\tra[k] = 0;")]),
    ("list: no shared overlap check", [("!(ra[k] + 16u <= sh || sh + 0x60u <= ra[k]) || ", "")]),
    ("list: shared overlap size 0x5C", [("sh + 0x60u <= ra[k]", "sh + 0x5Cu <= ra[k]")]),
    ("list: shared overlap array size 12", [("!(ra[k] + 16u <= sh ||", "!(ra[k] + 12u <= sh ||")]),
    ("list: no ring_info overlap check", [("!(ra[k] + 16u <= ri || ri + 0x4Cu <= ra[k]) || ", "")]),
    ("list: ring_info overlap array size 12", [("!(ra[k] + 16u <= ri ||", "!(ra[k] + 12u <= ri ||")]),
    ("list: no ring_mem overlap check", [(" || !(ra[k] + 16u <= rm || rm + 0x50u <= ra[k])", "")]),
    ("list: ring_mem overlap size 0x4C", [("rm + 0x50u <= ra[k]", "rm + 0x4Cu <= ra[k]")]),
    ("list: no array-array overlap check", [("if (!(ra[k] + 16u <= ra[i] || ra[i] + 16u <= ra[k])) {", "if (0) {")]),
    ("list: array-array overlap one side", [("if (!(ra[k] + 16u <= ra[i] || ra[i] + 16u <= ra[k])) {", "if (!(ra[i] + 16u <= ra[k])) {")]),
    ("list: array-array overlap size 12", [("ra[i] + 16u <= ra[k]))", "ra[i] + 12u <= ra[k]))")]),
    ("list: array-array skips the last pair", [("for (i = 0; i < k; i++) {", "for (i = 0; i + 1u < k; i++) {")]),
    ("list: overlap loop skips array 3", [("for (k = 0; k < 4u && why == NULL; k++) {     /* review r1 F2", "for (k = 0; k < 3u && why == NULL; k++) {     /* review r1 F2")]),
    ("list: RC Command all-ones accepted", [(" || rcold == 0xFFFFFFFFu", "")]),
    ("list: BAR0 window value", [("SWW_CFG_WIN, SWW_WIN_PCIE, 0xFFFFFFFFu, 0, 0, 0, -1)", "SWW_CFG_WIN, SWW_WIN_SYSMEM, 0xFFFFFFFFu, 0, 0, 0, -1)")]),
    ("list: BAR0 window restore value", [("SWW_CFG_WIN, w80, 0xFFFFFFFFu", "SWW_CFG_WIN, w80 | 1u, 0xFFFFFFFFu")]),
    ("list: BAR2 window value", [("SWW_CFG_WIN2, SWW_WIN_TAIL, 0xFFFFFFFFu", "SWW_CFG_WIN2, 0x200000u, 0xFFFFFFFFu")]),
    ("list: window move once", [("SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 0, -1)", "SWW_CFG_WIN2, 0u, 0xFFFFFFFFu, 0, 0, 1, -1)")]),
    ("list: flags2 value", [("#define SW_G2_FLAGS2     0xC0943041u", "#define SW_G2_FLAGS2     0x40943041u")]),
    ("list: flags2 old value", [("#define SW_G2_FLAGS2_OLD 0x40943041u", "#define SW_G2_FLAGS2_OLD 0x40943040u")]),
    ("list: flags2 old mask", [("SW_G2_FLAGS2_OLD, 0xFFFFFFFFu)", "SW_G2_FLAGS2_OLD, 0xFFFFFFFEu)")]),
    ("list: host_cap value", [("#define SW_G2_HOSTCAP    0x10823407u", "#define SW_G2_HOSTCAP    0x00001407u")]),
    ("list: host_cap at +0x50", [("sh + 0x54u, SW_G2_HOSTCAP", "sh + 0x50u, SW_G2_HOSTCAP")]),
    ("list: trap hi = lo", [("sh + 0x5Cu, (uint32_t)(pa >> 32)", "sh + 0x5Cu, (uint32_t)pa")]),
    ("list: trap buffer is scratch", [("pa = h->g2pa + h->g2plan.b[SWD_TRAP].off;", "pa = h->g2pa + h->g2plan.b[SWD_SCRATCH].off;")]),
    ("list: index host address stride 4", [("ri + 0x14u + 8u * k", "ri + 0x14u + 4u * k")]),
    ("list: index host address hi = lo", [("ri + 0x18u + 8u * k, (uint32_t)(pa >> 32)", "ri + 0x18u + 8u * k, (uint32_t)pa")]),
    ("list: index host address order", [("pa = h->g2pa + h->g2plan.b[SWD_IDX_H2D_W + k].off;", "pa = h->g2pa + h->g2plan.b[SWD_IDX_D2H_R - k].off;")]),
    ("list: ring base at +0x04", [("rm + 16u * i + 0x08u", "rm + 16u * i + 0x00u")]),
    ("list: ring base hi = lo", [("rm + 16u * i + 0x0Cu, (uint32_t)(pa >> 32)", "rm + 16u * i + 0x0Cu, (uint32_t)pa")]),
    ("list: ring max len shift", [("isz << 16", "isz << 8")]),
    ("list: ring max of the wrong ring", [("(swd_rings[SWD_H2D_CTRL + i].items", "(swd_rings[SWD_H2D_CTRL].items")]),
    ("list: 4 rings", [("for (i = 0; i < 5u; i++) {", "for (i = 0; i < 4u; i++) {")]),
    ("list: h2d slots 3", [("(k < 2u ? 2u : 3u)", "(k < 2u ? 3u : 3u)")]),
    ("list: d2h slots 2", [("(k < 2u ? 2u : 3u)", "(k < 2u ? 2u : 2u)")]),
    ("list: slot split at 1", [("(k < 2u ? 2u : 3u)", "(k < 1u ? 2u : 3u)")]),
    ("list: slot mask whole word", [("ra[k] + 4u * i, 0u, 0x0000FFFFu, 0, 0)", "ra[k] + 4u * i, 0u, 0xFFFFFFFFu, 0, 0)")]),
    ("list: scratch hi = lo", [("sh + 0x3Cu, (uint32_t)(pa >> 32)", "sh + 0x3Cu, (uint32_t)pa")]),
    ("list: scratch buffer is trap", [("pa = h->g2pa + h->g2plan.b[SWD_SCRATCH].off;", "pa = h->g2pa + h->g2plan.b[SWD_TRAP].off;")]),
    ("list: scratch len value", [("#define SW_G2_SCRLEN     8u", "#define SW_G2_SCRLEN     16u")]),
    ("list: RC Command value", [("#define SW_G2_RC         0x0507u", "#define SW_G2_RC         0x0506u")]),
    ("list: RC Command old-value mask", [("SW_G2_RC, 0xFFFFFFFFu, 0, 0xFFFFu, 1, -1)", "SW_G2_RC, 0xFFFFFFFFu, 0, 0, 1, -1)")]),
    ("list: RC restore not tied", [("rcold & 0xFFFFu, 0xFFFFFFFFu, 0, 0, 1, rc)", "rcold & 0xFFFFu, 0xFFFFFFFFu, 0, 0, 1, -1)")]),
    ("list: RC restore whole word", [("rcold & 0xFFFFu, 0xFFFFFFFFu", "rcold, 0xFFFFFFFFu")]),
    ("list: EP DMA value", [("#define SW_G2_EP_DMA     0x0006u", "#define SW_G2_EP_DMA     0x0007u")]),
    ("list: EP MEM value", [("#define SW_G2_EP_MEM     0x0002u", "#define SW_G2_EP_MEM     0x0006u")]),
    ("list: EP old not checked", [("SW_G2_EP_DMA, 0xFFFFFFFFu, SW_G2_EP_MEM, 0xFFFFu, 1, -1)", "SW_G2_EP_DMA, 0xFFFFFFFFu, SW_G2_EP_MEM, 0, 1, -1)")]),
    ("list: EP restore not tied", [("SW_G2_EP_MEM, 0xFFFFFFFFu, 0, 0, 1, ep)", "SW_G2_EP_MEM, 0xFFFFFFFFu, 0, 0, 1, -1)")]),
    ("list: hostready value", [("#define SW_G2_READY      0x12345678u", "#define SW_G2_READY      0x12345679u")]),
    ("list: hostready window", [("sw_g2_add(h, SWW_BAR0, SWW_WIN_PCIE, 0x144u", "sw_g2_add(h, SWW_BAR0, SWW_WIN_SYSMEM, 0x144u")]),
    ("list: hostready offset", [("SWW_WIN_PCIE, 0x144u", "SWW_WIN_PCIE, 0x140u")]),
    ("list: refusal keeps the list", [("\t\th->g2n = 0;\n\t\treturn -1;", "\t\treturn -1;")]),
    ("list: refusal returns 0", [("\t\th->g2n = 0;\n\t\treturn -1;", "\t\th->g2n = 0;\n\t\treturn 0;")]),
    # 22b: sw_g2_ds (the masked DS-round entries) and the kinds of the exact D4d entries; G2L_HARNESS (want()) must FAIL on each
    ("ds: word 0 is a plain item word", [("h->g2l[h->g2n - 1u].kind = k == 0u ? SW_G2K_R_ITEM0 : SW_G2K_R_ITEM;", "h->g2l[h->g2n - 1u].kind = SW_G2K_R_ITEM;")]),
    ("ds: item count 128", [("h->g2l[h->g2n - 1u].rep = SW_G2_R0_ITEMS - 1u;", "h->g2l[h->g2n - 1u].rep = SW_G2_R0_ITEMS;")]),
    ("ds: item stride 44", [("h->g2l[h->g2n - 1u].stride = SW_G2_D4D_ISZ;", "h->g2l[h->g2n - 1u].stride = 44u;")]),
    ("ds: word 0 value", [("k == 0u ? 0x00800023u : 0u", "k == 0u ? 0x00800022u : 0u")]),
    ("ds: mb value 0x20 missing", [("{ 0x2u, 0x40u, 0x20u }", "{ 0x2u, 0x40u, 0x2u }")]),
    ("ds: mb value 0x21", [("{ 0x2u, 0x40u, 0x20u }", "{ 0x2u, 0x40u, 0x21u }")]),
    ("ds: WR entry kind", [("h->g2l[h->g2n - 1u].kind = SW_G2K_R_WR;", "h->g2l[h->g2n - 1u].kind = SW_G2K_R_RD;")]),
    ("ds: WR slot is the read slot", [("h->g2plan.b[SWD_IDX_H2D_W].off, 0u, 0x0000FFFFu, 0u, 0u, 0, need)", "h->g2plan.b[SWD_IDX_D2H_R].off, 0u, 0x0000FFFFu, 0u, 0u, 0, need)")]),
    ("ds: WR doorbell base", [("SWW_WIN_PCIE, 0x140u, SW_G2_D4D_DB_WR, 0xFFFFFFFFu, 0, 0, 0, need)", "SWW_WIN_PCIE, 0x140u, SW_G2_D4D_DB_RD, 0xFFFFFFFFu, 0, 0, 0, need)")]),
    ("ds: RD range 64", [("h->g2l[h->g2n - 1u].vrng = SW_G2_R2_ITEMS - 1u;", "h->g2l[h->g2n - 1u].vrng = SW_G2_R2_ITEMS;")]),
    ("ds: RD range missing", [("\th->g2l[h->g2n - 1u].vrng = SW_G2_R2_ITEMS - 1u;\n", "")]),
    ("ds: RD doorbell base", [("SWW_WIN_PCIE, 0x140u, SW_G2_D4D_DB_RD, 0xFFFFFFFFu, 0, 0, 0, need)", "SWW_WIN_PCIE, 0x140u, SW_G2_D4D_DB_WR, 0xFFFFFFFFu, 0, 0, 0, need)")]),
    ("ds: RD doorbell kind", [("h->g2l[h->g2n - 1u].kind = SW_G2K_R_RDB;", "h->g2l[h->g2n - 1u].kind = SW_G2K_R_WDB;")]),
    ("ds: entries not chained after the C2 doorbell entry (F2)", [("\t\t\tbad |= sw_g2_ds(h, last) < 0;", "\t\t\tbad |= sw_g2_ds(h, -1) < 0;")]),
    ("ds: entries chained after the first entry (F2)", [("\t\t\tbad |= sw_g2_ds(h, last) < 0;", "\t\t\tbad |= sw_g2_ds(h, 0) < 0;")]),
    ("ds: entries not built", [("\t\t\tbad |= sw_g2_ds(h, last) < 0;", "")]),
    ("ds: entries repeatable only once", [("mb[r], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need)", "mb[r], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, need)")]),
    ("d4d: item word 0 kind", [("h->g2l[last].kind = k == 0u ? SW_G2K_SH_ITEM0 : SW_G2K_SH_ITEM;", "h->g2l[last].kind = SW_G2K_SH_ITEM;")]),
    ("d4d: slot kind", [("h->g2l[last].kind = SW_G2K_SH_WR;", "h->g2l[last].kind = SW_G2K_SH_ITEM;")]),
    ("d4d: doorbell kind", [("h->g2l[last].kind = SW_G2K_SH_DB;", "h->g2l[last].kind = SW_G2K_SH_WR;")]),
    ("d4d: read-index entries back in the chain", [("\t\tsw_g2_d4d_resp(w, 3u, rpa, 6u);      /* 22b:", "\t\tlast = sw_g2_add(h, SW3_HOST, 0, h->g2plan.b[SWD_IDX_D2H_R].off, 4u, 0x0000FFFFu, 2u, 0xFFFFFFFFu, 1, last);\n\t\tsw_g2_d4d_resp(w, 3u, rpa, 6u);      /* 22b:")]),
] + _d4c_g2l() + E_G2L_ROWS


class WlanWrMutants(unittest.TestCase):
    def test_every_guard_rule_is_killed(self):
        if not wlan_mutant_pool.want("header"):      # GATE_MUT_ONLY / --only: one loud skip line, the test still passes
            return
        base, out = run_all()
        print(run_all.line, file=sys.stderr)        # D6: the last line of the step output (rule 8: also on a mismatch)
        self.assertEqual(run_all.mismatch, [], "MUTCACHE MISMATCH " + ", ".join(run_all.mismatch))
        self.assertEqual(base[0], "pass", "the unmutated header must pass the host test: " + base[1])
        self.assertLess(run_all.base_s, ALONE_LIMIT / 8, "the unmutated build + test must stay under 1/8 of the alone re-run limit (the pool limit is 4x it, capped at ALONE_LIMIT/2), else a timeout or hang kill has no margin (review 0134 F2)")
        bad = []
        for name, (st, msg, equiv) in out.items():
            if st == "fail":
                continue
            if st == "pass" and equiv:
                continue
            bad.append("%s: %s %s" % (name, "SURVIVED" if st == "pass" else st, msg))
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertEqual(len(out), len(MUTANTS))

    def test_every_refusal_printf_has_a_gap_call(self):
        """patch 0137 (review F1): a printf that says refused / not read / not touched / skipped / not implemented / STOP must have a swl_gap() call on the line before it, so a
        deleted or forgotten call is found by a text scan (the host test pins only the STOP gap). The 'WIFI GAP' lines themselves and read dumps are not refusals.
        swl_miss() counts as the gap call (dde3967, patch 0137): it is swl_gap() unless deferred, and a deferred miss becomes the gap in swl_miss_flush() unless G2 read the word."""
        rx = re.compile(r'printf\("SWEET WLAN: [^"]*(refused|not read|not touched|skipped|not implemented|STOP:|dumps not made|failed|did not take|changed)')
        miss = []
        for name, text in (("sweet_wlan_wr.h", final_header()), ("sweet_wlan.c", glue_after())):
            L = text.split("\n")
            for i, ln in enumerate(L):
                if rx.search(ln) and not (i > 0 and ("swl_gap(" in L[i - 1] or "swl_miss(" in L[i - 1])):
                    miss.append("%s:%d %s" % (name, i + 1, ln.strip()[:110]))
        self.assertEqual(miss, [])

    def test_nvram_entry_is_not_pinned(self):
        """Patch 0132: only the firmware identity is a compile-time constant. The NVRAM entry (27062 bytes, crc 0xdf5db558 in the lab) is checked against its container record and nothing else (docs/research/43b);
        host test: wlan_wr_host_test.c rund() runs level 6 with a 27062 byte and a 3 byte text."""
        code = strip_comments(final_header())
        self.assertNotIn("27062", code)
        self.assertNotIn("0xdf5db558", code.lower())
        self.assertIsNone(re.search(r"nv_src_len\s*[!=]=\s*[1-9]", code))
        self.assertIsNone(re.search(r"nv_src_crc\s*[!=]=\s*(?:0x[0-9A-Fa-f]+|\d)", code))
        self.assertIsNotNone(re.search(r"#define SWW_FW_LEN\s+1764018u", code))
        self.assertIsNotNone(re.search(r"#define SWW_FW_CRC\s+0x1153ccffu", code))

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
            (hdr, glue.replace("static struct sw_hw hw;", "struct sw_hw hw;"), "sw_hw in sweet_wlan_init is not `static struct sw_hw hw;`"),
            (hdr, glue.replace("static struct sw_hw hw;", "struct sw_hw hw = { 0 };"), "sw_hw in sweet_wlan_init is not `static struct sw_hw hw;`"),
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
            (hdr.replace("src = swl_crc_b(src, x->nv[i]);", "src = swl_crc_b(src, x->nv_src[i]);"), glue, "swl_nv_prep does not CRC its private copy"),
            (hdr, glue.replace("read_random(buf, n);", "read_random(buf, n / 2);"), "sw3_rng does not draw"),
            (hdr, glue.replace("read_random(buf, n);", "(void)buf;"), "sw3_rng does not draw"),
            # patch 0133: the CA7 wrapper limit
            (hdr, glue.replace("if (!sw3_ca7_ok(h, space, win, off, v)) {", "if (0) {"), "sw3_wr does not limit the CA7 wrapper stores before the store"),
            (hdr, glue.replace("\tif (!sw3_ca7_ok(h, space, win, off, v)) {\n\t\treturn -1;\n\t}\n", "").replace("\t*p = v;\n", "\t*p = v;\n\tif (!sw3_ca7_ok(h, space, win, off, v)) {\n\t\treturn -1;\n\t}\n"), "sw3_wr does not limit the CA7 wrapper stores before the store"),
            (hdr, glue.replace("h->rel |= sw3_ca7_bit(h, space, win, off, v);", "h->rel |= 0;"), "sw3_wr does not record the CA7 release stores after the store"),
            (hdr, glue.replace("0x23u", "0x27u"), "sw3_ca7_ok does not limit the CA7 wrapper stores to the table's values"),
            (hdr, glue.replace("h->l3 >= 7", "h->l3 >= 6"), "sw3_ca7_ok does not limit the CA7 wrapper stores to the table's values"),
            (hdr, glue.replace("SWW_REL_MID", "0x23u"), "sw3_ca7_ok does not limit the CA7 wrapper stores to the table's values"),
            # patch 0174: G2's adapter and hook
            (hdr, glue.replace("\th->g2 = 0;\n", "\th->g2 = 0;\n\t(void)sw3_wr(h, SWW_CFG, 0, SWW_CFG_WIN, w80);\n"), "sw_g2_hook stores outside h->g2 = 1 .. h->g2 = 0"),
            (hdr, glue.replace("\th->g2 = 1;\n", "").replace("\trc = swg_go(&g);\n", "\trc = swg_go(&g);\n\th->g2 = 1;\n"), "sw_g2_hook stores outside h->g2 = 1 .. h->g2 = 0"),
            (hdr, glue.replace("\tcase SWG_SP_RC:\n\t\treturn sw3_wr(h, SW3_DBI, 0, a, v);", "\tcase SWG_SP_RC:\n\t\th->g2 = 0;\n\t\treturn sw3_wr(h, SW3_DBI, 0, a, v);"), "sw_g2_wr changes h->g2"),
            (hdr, glue.replace("\tcase SWG_SP_RC:\n\t\treturn sw3_rd(h, SW3_DBI, 0, a, v);", "\tcase SWG_SP_RC:\n\t\treturn sw3_wr(h, SW3_DBI, 0, a, *v);"), "sw3_wr used other than as the bus wr callback"),
            (hdr, glue.replace("gb.wr = sw_g2_wr;", "gb.wr = sw3_wr;"), "wr callback used other than by wb.wr = sw3_wr;"),
            (hdr, glue.replace("old = *p;", "*p = old;"), "assignment outside the allowlist"),
            (hdr, glue.replace("e->vmask = vmask;", "e->vmask = 0;"), "assignment outside the allowlist"),
        ]
        for h, g, want in bad:
            self.assertNotEqual(g, glue, want) if h == hdr else None
            self.assertTrue(any(x.startswith(want) for x in audit_store_path(h, g)), want)

    def test_rev_gate_is_wired(self):
        g = glue_after()
        self.assertEqual(audit_rev_gate(g), [])
        bad = [
            g.replace("level >= 6 && !swl_w14_rev_ok(&chip)", "level >= 7 && !swl_w14_rev_ok(&chip)"),
            g.replace("level >= 6 && !swl_w14_rev_ok(&chip)", "level >= 6 && swl_w14_rev_ok(&chip)"),
            g.replace("\t\t\tlevel = 5;\n", ""),
            g.replace("\t\t\tlevel = 5;\n", "\t\t\tlevel = 6;\n"),
            g.replace("!swl_w14_rev_ok(&chip)", "!swl_w14_rev_ok(&chip) && 0"),
            g.replace("if (level >= 6 && !swl_w14_rev_ok(&chip)) {", "if (0) {"),
            g.replace("swl_w14_rev_ok(&chip)", "swl_w14_rev_ok(0)"),
            # patch 0133: the level 7 gate
            g.replace("level >= 7 && !swl_w16_rev_ok(&chip)", "level >= 8 && !swl_w16_rev_ok(&chip)"),
            g.replace("level >= 7 && !swl_w16_rev_ok(&chip)", "level >= 7 && swl_w16_rev_ok(&chip)"),
            g.replace("level >= 7 && !swl_w16_rev_ok(&chip)", "level >= 7 && !swl_w14_rev_ok(&chip)"),
            g.replace("\t\t\tlevel = 6;\n", ""),
            g.replace("if (level >= 7 && !swl_w16_rev_ok(&chip)) {", "if (0) {"),
            g.replace("swl_w16_rev_ok(&chip)", "swl_w16_rev_ok(0)"),
        ]
        for i, m in enumerate(bad):
            self.assertNotEqual(m, g, i)
            self.assertNotEqual(audit_rev_gate(m), [], "rev gate mutant %d survived: %s" % (i, m[:0]))
        # moved behind the stage-3 call: the order check must catch it
        blk = re.search(r"\t\tif \(level >= 6 && !swl_w14_rev_ok\(&chip\)\) \{.*?\n\t\t}\n", g, re.S)
        self.assertTrue(blk)
        moved = g.replace(blk.group(0), "")
        moved = moved.replace(S3CALL, S3CALL + blk.group(0))
        self.assertNotEqual(moved, g)
        self.assertNotEqual(audit_rev_gate(moved), [])
        blk7 = re.search(r"\t\tif \(level >= 7 && !swl_w16_rev_ok\(&chip\)\) \{.*?\n\t\t}\n", g, re.S)
        self.assertTrue(blk7)
        moved7 = g.replace(blk7.group(0), "")
        moved7 = moved7.replace(S3CALL, S3CALL + blk7.group(0))
        self.assertNotEqual(moved7, g)
        self.assertNotEqual(audit_rev_gate(moved7), [])
        before6 = g.replace(blk7.group(0), "").replace(blk.group(0), blk7.group(0) + blk.group(0))
        self.assertNotEqual(before6, g)
        self.assertNotEqual(audit_rev_gate(before6), [])

    def test_level_gate_names_run_f3(self):
        """Patch 0136: stage 3 runs at most level 10 and names it run F3 (9 stays F2, 8 F1); the rev gate of level 7 covers everything above (>=). Patch 0167: sweetwlan=11 = level 10 + G1 after
        stage 3. L22c (T-4adb): the highest level the glue accepts is 24 (25 refused). Patch 0174: sweetwlan=12 maps the DBI page, plans G1 before stage 3 and arms G2 (g2ok), and only an armed G2 hands W17 its hook; W-D4b D-12: sweetwlan=16 is the highest level the glue
        accepts (a 17 is refused: nothing is touched; W-D4c: sweetwlan=16 = 15 + the DS_ACK), it arms G2 exactly like 12 (and refuses a D-cache line above SWD_PAD) and additionally sets hw.d4b (only >= 13, 3 only from 16, only inside the arming block), which the hook hands to G2."""
        def audit(glue):
            f = []
            b = body(strip_comments(glue), "sweet_wlan_init")
            s3 = body(strip_comments(glue), "sweet_wlan_stage3")
            if "if (level > 26 || SecureDTLookupEntry" not in b:
                f.append("the glue does not refuse levels above 26")
            if 'level > 9 ? "F3" : level > 8 ? "F2" : level > 7 ? "F1" : level > 6 ? "E" :' not in s3:
                f.append("the glue does not name level 10 run F3, level 9 run F2 and level 8 run F1")
            if "level >= 7 && !swl_w16_rev_ok(&chip)" not in b:
                f.append("the rev gate of level 7 does not cover levels 8 and up")
            if b.count("sweet_wlan_stage3(&hw, level > 10 ? 10 : (int)level);") != 1:
                f.append("stage 3 is not clamped to level 10")
            arm, dbi, g1 = b.find("if (level >= 12) {"), b.find("hw.dbi = sw_map(SW_DBI, 0x1000);"), b.find("hw.g2ok = hw.dbi != NULL && sweet_wlan_g1(&hw.g2plan, &hw.g2pa, &hw.g2va) == 0;")
            if arm < 0 or not arm < dbi < g1 < b.find("sweet_wlan_stage3(&hw,") or b.count("hw.g2ok =") - b.count("hw.g2ok = 0;") != 1 or "!sw_g2_alias_ok(hw.g2pa, hw.g2plan.total)" not in b or b.count("hw.dbi =") != 1:
                f.append("G2 is not armed only at level >= 12, before stage 3")
            d4 = b.find("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14 ? 2 : level >= 13 ? 1 : 0;")
            if b.count("hw.d4b =") != 1 or not arm < d4 < dbi or 'printf("SWEET WLAN: D4b not run: G2 not armed\\n");' not in b:
                f.append("hw.d4b is not set only for level >= 13 (2 from 14, 3 from 16), inside the arming block, or an unarmed G2 does not say D4b is not run")
            bn = " ".join(b.split())      # W-D4c: the D-cache line read at the arm (flush_dcache64 as the host-read invalidate needs a line no larger than the G1 pad)
            dl = b.find('unsigned dline = 4u << ((__builtin_arm_rsr64("CTR_EL0") >> 16) & 0xFu);')
            if b.count("dline =") != 1 or not arm < dl < b.find("if (!hw.g2ok) {") or b.count("dline >") != 1 or 'else if (dline < 64u || dline > SWD_PAD) { hw.g2ok = 0; swl_gap("g2", "not armed: D-cache line outside 64..128 B (flush step..pad)", "high");' not in bn or \
                    'printf("SWEET WLAN: D-cache line %u B (CTR_EL0.DminLine), G1 buffers padded to %u B\\n", dline, (unsigned)SWD_PAD);' not in b:
                f.append("G2 is not refused for a D-cache line below the 64 B flush step or above SWD_PAD (loud), or CTR_EL0.DminLine is not read as 4 << [19:16], or the line size is not printed once")
            hk = body(strip_comments(glue), "sw_g2_hook")
            if hk.count("g.d4b = h->d4b;") != 1 or hk.find("g.d4b = h->d4b;") > hk.find("swg_go(&g)"):
                f.append("the hook does not hand hw.d4b to G2 before swg_go")
            if 'if (level >= 12 && !sw_prop_is(node, "dbi", SW_DBI, 0x1000)) {' not in b:
                f.append("level 12 does not pin the ADT dbi property")
            if "x.g2 = hw->g2ok ? sw_g2_hook : NULL;" not in s3 or s3.count("x.g2 =") != 1:
                f.append("the W17 hook is not set only for an armed G2")
            if "if (level == 11) {\n\t\t\t\t(void)sweet_wlan_g1(NULL, NULL, NULL);" not in b:
                f.append("level 11 does not run G1 after stage 3")
            return f
        g = glue_after()
        self.assertEqual(audit(g), [])
        for old, new in (("if (level > 26 || SecureDTLookupEntry", "if (level > 27 || SecureDTLookupEntry"), ("if (level > 26 || SecureDTLookupEntry", "if (level > 23 || SecureDTLookupEntry"), ("if (level > 26 || SecureDTLookupEntry", "if (level > 16 || SecureDTLookupEntry"),
                         ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14 ? 2 : level >= 13 ? 1 : 0;", "hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14 ? 2 : level >= 12 ? 1 : 0;"), ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14 ? 2 : level >= 13 ? 1 : 0;", "hw.d4b = 1;"), ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14 ? 2 : level >= 13 ? 1 : 0;", "hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 13 ? 2 : level >= 13 ? 1 : 0;"),
                         ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14", "hw.d4b = level >= 15 ? 3 : level >= 14"), ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14", "hw.d4b = level >= 17 ? 3 : level >= 14"), ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14", "hw.d4b = level >= 16 ? 2 : level >= 14"), ("hw.d4b = level >= 17 ? (int)level - 13 : level >= 16 ? 3 : level >= 14", "hw.d4b = level >= 14"),
                         ('4u << ((__builtin_arm_rsr64("CTR_EL0")', '1u << ((__builtin_arm_rsr64("CTR_EL0")'), ('4u << ((__builtin_arm_rsr64("CTR_EL0")', '4u >> ((__builtin_arm_rsr64("CTR_EL0")'), ('(__builtin_arm_rsr64("CTR_EL0") >> 16)', '(__builtin_arm_rsr64("CTR_EL0") >> 14)'), ('(__builtin_arm_rsr64("CTR_EL0") >> 16)', '(__builtin_arm_rsr64("CTR_EL0") >> 17)'),
                         ('>> 16) & 0xFu);     /* W-D4c', '>> 16) & 0x7u);     /* W-D4c'), ('__builtin_arm_rsr64("CTR_EL0")', '__builtin_arm_rsr64("MPIDR_EL1")'), ("else if (dline < 64u || dline > SWD_PAD) {", "else if (dline < 64u || dline > 4096u) {"), ("else if (dline < 64u || dline > SWD_PAD) {", "else if (dline < 64u || dline >= SWD_PAD) {"), ("else if (dline < 64u || dline > SWD_PAD) {", "else if (dline < 64u || dline > 2u * SWD_PAD) {"),
                         ("else if (dline < 64u || dline > SWD_PAD) {", "else if (dline > SWD_PAD) {"), ("else if (dline < 64u || dline > SWD_PAD) {", "else if (dline < 32u || dline > SWD_PAD) {"), ("else if (dline < 64u || dline > SWD_PAD) {", "else if (dline <= 64u || dline > SWD_PAD) {"),
                         ("\t\t\t\t\thw.g2ok = 0;\n\t\t\t\t\tswl_gap(\"g2\", \"not armed: D-cache", "\t\t\t\t\tswl_gap(\"g2\", \"not armed: D-cache"), ("\t\t\t\t\tswl_gap(\"g2\", \"not armed: D-cache line outside 64..128 B (flush step..pad)\", \"high\");\n", ""),
                         ('printf("SWEET WLAN: D-cache line %u B (CTR_EL0.DminLine), G1 buffers padded to %u B\\n", dline, (unsigned)SWD_PAD);', '(void)dline;'), ("g.d4b = h->d4b;", "g.d4b = 1;"), ("g.d4b = h->d4b;", "g.d4b = 0;"),
                         ('printf("SWEET WLAN: D4b not run: G2 not armed\\n");', '(void)0;'),
                         ('level > 9 ? "F3"', 'level > 10 ? "F3"'), ('level > 9 ? "F3"', 'level > 8 ? "F3"'), ('level > 8 ? "F2"', 'level > 9 ? "F2"'), ('level > 8 ? "F2"', 'level > 7 ? "F2"'), ('level > 7 ? "F1"', 'level > 8 ? "F1"'), ('level > 7 ? "F1"', 'level > 6 ? "F1"'),
                         ("level >= 7 && !swl_w16_rev_ok(&chip)", "level == 7 && !swl_w16_rev_ok(&chip)"), ("level >= 7 && !swl_w16_rev_ok(&chip)", "level == 7 || level == 8 && !swl_w16_rev_ok(&chip)"),
                         ("sweet_wlan_stage3(&hw, level > 10 ? 10 : (int)level);", "sweet_wlan_stage3(&hw, (int)level);"), ("if (level >= 12) {", "if (level >= 11) {"),
                         ("hw.g2ok = hw.dbi != NULL && sweet_wlan_g1(", "hw.g2ok = sweet_wlan_g1("), ('if (level >= 12 && !sw_prop_is(node, "dbi"', 'if (level >= 13 && !sw_prop_is(node, "dbi"'),
                         ("x.g2 = hw->g2ok ? sw_g2_hook : NULL;", "x.g2 = sw_g2_hook;"), ("if (level == 11) {\n\t\t\t\t(void)sweet_wlan_g1(", "if (level >= 11) {\n\t\t\t\t(void)sweet_wlan_g1(")):
            self.assertIn(old, g)
            self.assertNotEqual(audit(g.replace(old, new)), [], old + " -> " + new)

    def test_glue_sweep_accepts_exactly_the_table(self):
        with tempfile.TemporaryDirectory() as work:
            st, out = run_glue(glue_function_text(), work)
        self.assertEqual(st, "pass", out)

    def test_scan24_streams_replay_through_the_glue(self):
        """review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 2 (replay.py / replay_neg.py of the review, as C in the glue sweep): the header store streams of the L21-L23 host-test cases main, rxoff, l22, ds, ds0, cap go through the REAL glue list
        (sw3_wr, list built at d4b 10): every store accepted, g2sh == 2 x DS rounds (the hold round: ack + wake); the nine negatives on the main stream are each refused at the changed store. A truncated dump or a vacuous negative fails loudly."""
        _pa, sets = g2_store_streams()
        got = {l: len(st) for l, _r, st in sets}
        for lab in SCAN24_LABELS:
            self.assertIn(lab, got)
        self.assertGreater(got["cap"], 1000)        # NREC 2048: the cap case alone makes more than 1000 host/doorbell stores after the L16 C2 doorbell
        self.assertGreater(got["main"], 400)
        sink = []
        with tempfile.TemporaryDirectory() as work:
            st, out = run_glue(glue_function_text(), work, stdout_sink=sink)
        self.assertEqual(st, "pass", out)
        self.assertIn("REPLAY24 streams %d negatives 9\n" % len(sets), sink[0])

    def test_l24_streams_replay_through_the_glue(self):
        """T-8ede: the L24 join streams j24, j24rev, j24e1 (npartial 0), j24e62 (npartial 61: 62 + 2 event re-posts, capped at 48 under the CLM blob), j24key (a wsec_key call) go through the REAL glue list built at d4b 11 from the host test's own join table
        (G2JC lines): every store accepted; 13 negatives/controls (join payload word, event id, event buffer, mixed item, incomplete item at the commit, key canary word, no join table, commit +2, upper half, ring depth 128 + control,
        65th event re-post + control) each at the expected store."""
        _pa, sets = g2_store_streams()
        got = {l: len(st) for l, _r, st in sets}
        for lab in L24_LABELS:
            self.assertIn(lab, got)
            self.assertIn(len(G2_JC[lab]), (6, 7))
        self.assertGreater(got["j24e62"], got["j24"] + 400)     # T-84b0: the L24 streams run under the CLM blob, the event cap is 48: 42 more event re-posts of 12 stores each (504; 58 x 12 under the cap 64 of level 23)
        self.assertEqual(len(G2_JC["j24key"]), 7)
        sink = []
        with tempfile.TemporaryDirectory() as work:
            st, out = run_glue(glue_function_text(), work, stdout_sink=sink)
        self.assertEqual(st, "pass", out)
        self.assertIn("L24 streams %d negatives %d\n" % (len(L24_LABELS), L24_NEGATIVES), sink[0])

    def test_glue_mutants_are_killed(self):
        if not wlan_mutant_pool.want("glue"):
            return
        base = glue_function_text()
        texts = {}
        for name, subs in GLUE_MUTANTS:        # the mutated text of each mutant, serial and without a compiler; the sweeps run pooled and cached below
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
            texts[name] = t
        with tempfile.TemporaryDirectory() as work:
            res = wlan_mutant_pool.run_set("glue", GLUE_MUTANTS, lambda name, subs: run_glue(texts[name], work, GLUE_MUTANT_FLAGS),
                                           [base, GLUE_HARNESS, streams_c(), new_file(PATCH0120, "bsd/dev/sweet_wlan_chip.h"), final_header(), " ".join(GLUE_MUTANT_FLAGS)])
        bad = ["%s: %s %s" % (name, "SURVIVED" if st == "pass" else "does not compile" if st == "compile" else st, out) for (name, _s), (st, out) in zip(GLUE_MUTANTS, res) if st != "fail"]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_glue_clm_constants_restated(self):
        """L22c (T-4adb): the glue's SW_G2_CLMCHUNK / SW_G2_CLMMAX, the G2L harness literals and the header's SWG_E_CLMCHUNK / SWG_E_CLMMAX are the same numbers (R107 #5: the glue restates, it does not include)."""
        g, h = glue_after(), g2_header()
        for gl, hd in (("SW_G2_CLMCHUNK", "SWG_E_CLMCHUNK"), ("SW_G2_CLMMAX", "SWG_E_CLMMAX")):
            m = re.search(r"#define %s\s+(\d+)u" % gl, g)
            k = re.search(r"#define %s\s+(\d+)u" % hd, h)
            t = re.search(r"#define %s\s+(\d+)u" % gl, G2L_HARNESS)
            self.assertTrue(m and k and t, gl)
            self.assertEqual(m.group(1), k.group(1), gl)
            self.assertEqual(t.group(1), k.group(1), gl)

    def test_g2l_header_event_caps_restated(self):
        """T-4459: the G2L harness literals SWG_E_MAX / SWG_E_MAX_CLM equal the header's (the glue's static asserts compare them with SW_G2_E_NREP*)."""
        h = g2_header()
        for name in ("SWG_E_MAX", "SWG_E_MAX_CLM"):
            k = re.search(r"#define %s\s+(\d+)u" % name, h)
            t = re.search(r"#define %s\s+(\d+)u" % name, G2L_HARNESS)
            self.assertTrue(k and t, name)
            self.assertEqual(t.group(1), k.group(1), name)

    def test_glue_ds_max_restated(self):
        """review F3a: the glue's SW_G2_DS_MAX, the sweep harness literal and the header's SWG_D4_DS_MAX are the same number, and the glue asserts it."""
        g = glue_after()
        m = re.search(r"#define SW_G2_DS_MAX\s+(\d+)u", g)
        h = re.search(r"#define SWG_D4_DS_MAX\s+(\d+)u", g2_header())
        t = re.search(r"#define SW_G2_DS_MAX\s+(\d+)u", GLUE_HARNESS)
        self.assertTrue(m and h and t)
        self.assertEqual(m.group(1), h.group(1))
        self.assertEqual(t.group(1), h.group(1))
        self.assertIn("_Static_assert(SW_G2_DS_MAX == SWG_D4_DS_MAX", g)

    def test_t8ede_mutants_are_all_killed(self):
        """T-4459 (review T-48a0 r1 F2, AP-214): every T8EDE row (the T-4a7f / T-48a0 L24 rules, the interim ring-2 witness included) is KILLED by the glue sweep or the G2L harness; a row that survives
        ("pass"), does not compile or whose pattern is gone is a failure here, not a skipped line of the CLI-only `--g2-kill`. (A kill is only as good as the harness behind it: test_g2_list_harness_passes must be green too.)"""
        res = t8ede_kill()
        if res is None:      # GATE_MUT_ONLY skips the g2l set
            return
        bad = ["%s: %s %s" % (name, st, out) for name, st, out in res if st != "fail"]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_g2_list_harness_passes(self):
        with tempfile.TemporaryDirectory() as work:
            st, out = run_g2_list(g2_list_text(), work)
        self.assertEqual(st, "pass", out)

    def test_g2_list_mutants_are_killed(self):
        if not wlan_mutant_pool.want("g2l"):
            return
        base_core, base_list = glue_core_text(), g2_list_text()
        texts = {}
        for name, subs in G2L_MUTANTS:        # the mutated (core, list) texts of each mutant, serial and without a compiler; the sweeps run pooled and cached below
            c, t = base_core, base_list       # T-1142: the E_NREP / E_BUFSZ constants moved to the core text (T-4a7f), so a row's pattern may sit in either text
            for old, new in subs:
                self.assertIn(old, c + t, name + ": pattern not found (glue changed under the mutant)")
                c, t = c.replace(old, new), t.replace(old, new)
            self.assertNotEqual((c, t), (base_core, base_list), name)
            texts[name] = (c, t)
        with tempfile.TemporaryDirectory() as work:
            res = wlan_mutant_pool.run_set("g2l", G2L_MUTANTS, lambda name, subs: _run_g2_list_core(texts[name][0], texts[name][1], work, ["-Wno-parentheses"]),
                                           [base_list, base_core, G2L_HARNESS, chip_header(), final_header(), new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h"), l25wl_header(), "-Wno-parentheses"])
        bad = ["%s: %s %s" % (name, "SURVIVED" if st == "pass" else "does not compile" if st == "compile" else st, out) for (name, _s), (st, out) in zip(G2L_MUTANTS, res) if st != "fail"]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_extraction_rejects_a_missing_hunk(self):
        with self.assertRaises(ValueError):
            new_file(PATCH0125, "bsd/dev/does_not_exist.h")




# ---- run G2 (patch 0174): the shared-structure writer. One mutant per rule of sweet_wlan_g2.h; the unmutated host test must pass first
#      (g2_kill_test). Part of the suite as class G2Mutants; alone: python3 -B tools/test_wlan_wr_mutants.py --g2
def _d4c_g2():
    """W-D4c (sweetwlan=16) header mutants, the stock inband-device-wake sequence: one per doorbell value, epoch, mb value, slot value and old value, per row range and order, the delay, the host-only exit-note poll, every exit-note guard and miss
    (no deassert), the final read stops and the F4 pre-answer stops. The host test shim/wlan_g2_host_test.c (D_MB cases) must FAIL on each."""
    out = []
    A = out.append
    # doorbell values (22b: the formulas of swg_d4_val; the SWG_D4C_DB_* macros are only static-assert references of round 0) and which doorbell each row writes
    for nm, good, bad in (("ack", "SWG_DB_WR | (x->d4c_wr + 1u)", "SWG_DB_WR | (x->d4c_wr + 0u)"), ("rd1", "SWG_DB_RD | ((x->d4c_rd + 1u) % SWG_R2_DEPTH)", "SWG_DB_RD | ((x->d4c_rd + 0u) % SWG_R2_DEPTH)"), ("wake", "SWG_DB_WR | (x->d4c_wr + 2u)", "SWG_DB_WR | (x->d4c_wr + 1u)"), ("deas", "SWG_DB_WR | (x->d4c_wr + 3u)", "SWG_DB_WR | (x->d4c_wr + 2u)"), ("rd2", "SWG_DB_RD | ((x->d4c_rd + 2u) % SWG_R2_DEPTH)", "SWG_DB_RD | ((x->d4c_rd + 1u) % SWG_R2_DEPTH)")):
        A(("d4c: %s doorbell value" % nm, [("return " + good + ";", "return " + bad + ";")]))
    dbx = {"A": "SWG_DB_WR | (x->d4c_wr + 1u)", "C1": "SWG_DB_RD | ((x->d4c_rd + 1u) % SWG_R2_DEPTH)", "W": "SWG_DB_WR | (x->d4c_wr + 2u)", "D": "SWG_DB_WR | (x->d4c_wr + 3u)", "C2": "SWG_DB_RD | ((x->d4c_rd + 2u) % SWG_R2_DEPTH)"}
    for nm, row, own, other in (("ack", "A", "ACK", "RD1"), ("rd1", "C1", "RD1", "WAKE"), ("wake", "W", "WAKE", "DEAS"), ("deas", "D", "DEAS", "RD2"), ("rd2", "C2", "RD2", "ACK")):
        A(("d4c: %s doorbell row writes the %s one" % (nm, other.lower()), [("\tcase SWG_D4C_%s_DB:\n\t\treturn %s;" % (row, dbx[row]), "\tcase SWG_D4C_%s_DB:\n\t\treturn SWG_D4C_DB_%s;" % (row, other))]))
    # item epochs (22b: swg_ep of the round's own indices)
    A(("d4c: ack item built with the wake epoch", [("SWD4B_H2D_DS_ACK, swg_ep(wr))", "SWD4B_H2D_DS_ACK, swg_ep(wr + 1u))")]))
    A(("d4c: wake item built with the deas epoch", [("SWD4B_H2D_DS_WAKE, swg_ep(wr + 1u))", "SWD4B_H2D_DS_WAKE, swg_ep(wr + 2u))")]))
    A(("d4c: deas item built with the ack epoch", [("SWD4B_H2D_DS_DEAS, swg_ep(wr + 2u))", "SWD4B_H2D_DS_DEAS, swg_ep(wr))")]))
    A(("d4c: ack item mb_data is not DS_ACK", [("swd4b_item_mbdata(it, SWD4B_H2D_DS_ACK,", "swd4b_item_mbdata(it, 0u,")]))
    A(("d4c: wake item mb_data is the deassert one", [("swd4b_item_mbdata(it, SWD4B_H2D_DS_WAKE,", "swd4b_item_mbdata(it, SWD4B_H2D_DS_DEAS,")]))
    A(("d4c: deas item mb_data is the wake one", [("swd4b_item_mbdata(it, SWD4B_H2D_DS_DEAS,", "swd4b_item_mbdata(it, SWD4B_H2D_DS_WAKE,")]))
    # slot values and expected old values
    sv = {"A": "(x->d4c_wr + 1u)", "W": "(x->d4c_wr + 2u)", "D": "(x->d4c_wr + 3u)", "C1": "((x->d4c_rd + 1u) % SWG_R2_DEPTH)", "C2": "((x->d4c_rd + 2u) % SWG_R2_DEPTH)"}
    sb = {"A": "(x->d4c_wr + 2u)", "W": "(x->d4c_wr + 1u)", "D": "(x->d4c_wr + 2u)", "C1": "((x->d4c_rd + 2u) % SWG_R2_DEPTH)", "C2": "((x->d4c_rd + 1u) % SWG_R2_DEPTH)"}
    for nm, row in (("ack", "A"), ("wake", "W"), ("deas", "D"), ("rd1", "C1"), ("rd2", "C2")):
        A(("d4c: %s slot value" % nm, [("\tcase SWG_D4C_%s_SLOT:\n\t\treturn (old & 0xFFFF0000u) | %s;" % (row, sv[row]), "\tcase SWG_D4C_%s_SLOT:\n\t\treturn (old & 0xFFFF0000u) | %s;" % (row, sb[row]))]))
    ov = {"A": "x->d4c_wr", "W": "x->d4c_wr + 1u", "D": "x->d4c_wr + 2u", "C2": "(x->d4c_rd + 1u) % SWG_R2_DEPTH"}
    ob = {"A": "x->d4c_wr + 1u", "W": "x->d4c_wr + 2u", "D": "x->d4c_wr + 3u", "C2": "(x->d4c_rd + 2u) % SWG_R2_DEPTH"}
    for nm, row, v in (("ack", "A", 1), ("wake", "W", 2), ("deas", "D", 3), ("rd2", "C2", 1)):
        A(("d4c: %s slot old is the new index" % nm, [("\tcase SWG_D4C_%s_SLOT:\n\t\treturn %s;\n" % (row, ov[row]), "\tcase SWG_D4C_%s_SLOT:\n\t\treturn %s;\n" % (row, ob[row]))]))
        A(("d4c: %s slot old is 0" % nm, [("\tcase SWG_D4C_%s_SLOT:\n\t\treturn %s;\n" % (row, ov[row]), "\tcase SWG_D4C_%s_SLOT:\n\t\treturn 0u;\n" % row)]))
        A(("d4c: %s slot old not checked" % nm, [("if (old != swg_d4_oldv(x, r)) {", "if (r != SWG_D4C_%s_SLOT && old != swg_d4_oldv(x, r)) {" % row)]))
    A(("d4c: rd1 slot old not required 0", [("if (old != swg_d4_oldv(x, r)) {", "if (r != SWG_D4C_C1_SLOT && old != swg_d4_oldv(x, r)) {")]))
    A(("d4c: every old value must be 0", [("if (old != swg_d4_oldv(x, r)) {", "if (old != 0u) {")]))
    for nm, lo, hi in (("ack", "A_ITEM0", "A_SLOT"), ("wake", "W_ITEM0", "W_SLOT"), ("deas", "D_ITEM0", "D_SLOT")):
        A(("d4c: %s item old not required 0" % nm, [("if (old != swg_d4_oldv(x, r)) {", "if ((r < SWG_D4C_%s || r >= SWG_D4C_%s) && old != swg_d4_oldv(x, r)) {" % (lo, hi))]))
    # doorbell rows are never read
    isdb = "#define SWG_D4_ISDB(r) ((r) == SWG_D4_DB0 || (r) == SWG_D4C_A_DB || (r) == SWG_D4C_C1_DB || (r) == SWG_D4C_W_DB || (r) == SWG_D4C_D_DB || (r) == SWG_D4C_C2_DB)"
    A(("d4c: doorbell rows are read and read back", [(isdb, "#define SWG_D4_ISDB(r) ((r) == SWG_D4_DB0)")]))
    for nm, row in (("ack", "A"), ("rd1", "C1"), ("wake", "W"), ("deas", "D"), ("rd2", "C2")):
        A(("d4c: %s doorbell row is read" % nm, [(" || (r) == SWG_D4C_%s_DB" % row, "")]))
    # addresses
    A(("d4c: rd slots are the H2D-RD array", [("\tcase SWG_D4C_C1_SLOT:\n\tcase SWG_D4C_C2_SLOT:\n\t\t*sp = SWG_SP_HOST;\n\t\treturn swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_R)]", "\tcase SWG_D4C_C1_SLOT:\n\tcase SWG_D4C_C2_SLOT:\n\t\t*sp = SWG_SP_HOST;\n\t\treturn swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)]")]))
    A(("d4c: rd slots are the D2H-WR array", [("\tcase SWG_D4C_C1_SLOT:\n\tcase SWG_D4C_C2_SLOT:\n\t\t*sp = SWG_SP_HOST;\n\t\treturn swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_R)]", "\tcase SWG_D4C_C1_SLOT:\n\tcase SWG_D4C_C2_SLOT:\n\t\t*sp = SWG_SP_HOST;\n\t\treturn swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)]")]))
    for nm, row in (("ack", "A"), ("wake", "W"), ("deas", "D")):
        A(("d4c: %s slot row is not the H2D-WR slot" % nm, [("\tcase SWG_D4C_%s_SLOT:\n" % row, "")]))
    A(("d4c: item offset is the ring base", [("x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 1u) * SWD4B_ITEM_LEN + 4u * w", "x->in.ring[0] + 4u * w")]))
    A(("d4c: item offset one item more", [("x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 1u) * SWD4B_ITEM_LEN + 4u * w", "x->in.ring[0] + (uint64_t)(x->d4c_wr + itm) * SWD4B_ITEM_LEN + 4u * w")]))
    A(("d4c: item offset one item less", [("x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 1u) * SWD4B_ITEM_LEN + 4u * w", "x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 2u) * SWD4B_ITEM_LEN + 4u * w")]))
    A(("d4c: item word stride", [("x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 1u) * SWD4B_ITEM_LEN + 4u * w", "x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 1u) * SWD4B_ITEM_LEN + 8u * w")]))
    for nm, row, k in (("ack", "A", 1), ("wake", "W", 2), ("deas", "D", 3)):
        A(("d4c: %s item rows are item %d" % (nm, k % 3 + 1), [("\t\t*itm = %du;\n\t\t*w = (unsigned)(r - SWG_D4C_%s_ITEM0);" % (k, row), "\t\t*itm = %du;\n\t\t*w = (unsigned)(r - SWG_D4C_%s_ITEM0);" % (k % 3 + 1, row))]))
    A(("d4c: item words are the event buffer item", [("return x->d4cw[itm - 1u][w];", "return x->d4w[w];")]))
    A(("d4c: item words are the ack item's", [("return x->d4cw[itm - 1u][w];", "return x->d4cw[0][w];")]))
    A(("d4c: item word index shifted", [("return x->d4cw[itm - 1u][w];", "return x->d4cw[itm - 1u][(w + 1u) % SWG_D4_ITEMW];")]))
    for g in range(3):
        A(("d4c: item %d words not packed" % (g + 1), [("x->d4cw[%d][i] = swd4b_get32(it + 4u * i);" % g, "x->d4cw[%d][i] = 0u * swd4b_get32(it + 4u * i);" % g)]))
        A(("d4c: item %d words packed from the next word" % (g + 1), [("x->d4cw[%d][i] = swd4b_get32(it + 4u * i);" % g, "x->d4cw[%d][i] = swd4b_get32(it + 4u * ((i + 1u) %% SWG_D4_ITEMW));" % g)]))
        # the byte order of swd4b_get32 is pinned by the D4B helper harness (22b: the packing is swd4b_get32 now, no longer open coded)
    # order and extent of the row ranges
    rowsA = "ex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_C1_DB);"
    swap = "ex = swg_d4c_rows(x, SWG_D4C_C1_SLOT, SWG_D4C_C1_DB);\n\tif (ex == 0) {\n\t\tex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_A_DB);\n\t}"
    chk = " || (r != 0 && (x->d4mask & (1ull << (r - 1))) == 0u)"
    A(("d4c: C1 before A1 (order check kept)", [(rowsA, swap)]))
    A(("d4c: C1 before A1 (order check dropped)", [(rowsA, swap), (chk, "")]))
    rowsD = "ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);"
    swapW = "ex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_A_DB);\n\tif (ex == 0) {\n\t\tex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);\n\t}\n\tif (ex == 0) {\n\t\tex = swg_d4c_rows(x, SWG_D4C_C1_SLOT, SWG_D4C_C1_DB);\n\t}"
    swapD = "ex = swg_d4c_rows(x, SWG_D4C_C2_SLOT, SWG_D4C_C2_DB);\n\tif (ex == 0) {\n\t\tex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_D_DB);\n\t}"
    A(("d4c: W rows stored before the C1 rows, A1 first (order check kept)", [(rowsA, swapW)]))
    A(("d4c: W rows stored before the C1 rows, A1 first (order check dropped)", [(rowsA, swapW), (chk, "")]))
    A(("d4c: C2 rows stored before the D rows (order check kept)", [(rowsD, swapD)]))
    A(("d4c: C2 rows stored before the D rows (order check dropped)", [(rowsD, swapD), (chk, "")]))
    A(("d4c: d4sleep not set after the ack doorbell store (no DEV_SLEEP gap on a stop before the wake)", [("\t\tx->d4sleep = 1u;", "\t\tx->d4sleep = 0u;")]))
    A(("d4c: ack range stops before the C1 slot", [(rowsA, "ex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_A_DB);")]))
    A(("d4c: C1 range stops before its doorbell", [(rowsA, "ex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_C1_SLOT);")]))
    A(("d4c: A1 range stops before the ack doorbell", [(rowsA, "ex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_A_SLOT);")]))
    A(("d4c: W range stops before its doorbell", [("ex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);", "ex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_SLOT);")]))
    A(("d4c: W rows never stored", [("ex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);", "ex = 0;")]))
    A(("d4c: W after the exit-note poll (the wake stored only with the deassert)", [("ex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);", "ex = 0;"), ("ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);", "ex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_C2_DB);")]))
    A(("d4c: D range stops before the deassert doorbell", [("ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);", "ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_D_SLOT);")]))
    A(("d4c: C2 range stops before its doorbell", [("ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);", "ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_SLOT);")]))
    A(("d4c: C2 never stored (deassert only)", [("ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);", "ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_D_DB);")]))
    A(("d4c: rows loop stops one row early", [("for (r = from; r <= to; r++) {", "for (r = from; r < to; r++) {")]))
    # the wake delay and the exit-note poll
    A(("d4c: no delay after the wake", [("\tx->b->delay_ms(x->b->ctx, 1u);      /* OSL_DELAY(1000), dhd_pcie.c:12300ff */\n", "")]))
    A(("d4c: 2 ms delay after the wake", [("x->b->delay_ms(x->b->ctx, 1u);", "x->b->delay_ms(x->b->ctx, 2u);")]))
    A(("d4c: the 1 ms is not counted in the waited time", [("\twaited = 1u;\n", "\twaited = 0u;\n")]))
    A(("d4c: the delay comes before the wake rows", [("\tex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);        /* W (the device wake) */\n", "\tx->b->delay_ms(x->b->ctx, 1u);\n\tex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);\n"), ("\tx->b->delay_ms(x->b->ctx, 1u);      /* OSL_DELAY(1000), dhd_pcie.c:12300ff */\n", "")]))
    A(("d4c: exit-note poll also reads TCM", [("if (swg_d4c_hread(x, &e, ns)) {", "if (swg_d4_read(x, &cur, 1u) || swg_d4c_hread(x, &e, ns)) {")]))
    A(("d4c: exit-note poll reads the core mailbox word", [("if (swg_d4c_hread(x, &e, ns)) {", "if (swg_pw_read(x, &cur.pw) || swg_d4c_hread(x, &e, ns)) {")]))
    A(("d4c: exit-note poll reads ring-2 item 0", [("+ 4u * i + (uint64_t)slot * swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].isz, &e->r2[i])", "+ 4u * i, &e->r2[i])")]))
    A(("d4c: exit-note poll reads ring-2 item 2", [("+ 4u * i + (uint64_t)slot * swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].isz, &e->r2[i])", "+ 4u * i + (uint64_t)(slot + 1u) * swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].isz, &e->r2[i])")]))
    A(("d4c: exit-note poll never ends on a write index change", [("\t\tif ((e.d2hw & 0xFFFFu) != ns) {\n\t\t\tbreak;\n\t\t}\n", "")]))
    A(("d4c: exit-note poll ends on index 2 only", [("\t\tif ((e.d2hw & 0xFFFFu) != ns) {\n\t\t\tbreak;\n\t\t}\n", "\t\tif ((e.d2hw & 0xFFFFu) == ne) {\n\t\t\tbreak;\n\t\t}\n")]))
    A(("d4c: exit-note poll without the pause", [("\t\t\tx->b->delay_ms(x->b->ctx, SWG_POLL_MS);\n\t\t\twaited += SWG_POLL_MS;\n\t\t}\n\t}\n\tprintf(\"SWEET WLAN: %s exit-note poll", "\t\t\twaited += SWG_POLL_MS;\n\t\t}\n\t}\n\tprintf(\"SWEET WLAN: %s exit-note poll")]))
    A(("d4c: exit-note poll waits 20 polls", [("\tfor (n = 0; n < SWG_POLLS; n++) {\n\t\tx->fail = \"a d4c exit-note poll read failed\";", "\tfor (n = 0; n < SWG_POLLS + 1u; n++) {\n\t\tx->fail = \"a d4c exit-note poll read failed\";")]))
    A(("d4c: exit-note poll dead link not checked", [("\t\tif (e.h2dr == 0xFFFFFFFFu || e.d2hw == 0xFFFFFFFFu) {", "\t\tif (0) {")]))
    A(("d4c: exit-note poll h2d_r all ones not checked", [("e.h2dr == 0xFFFFFFFFu || e.d2hw == 0xFFFFFFFFu", "e.d2hw == 0xFFFFFFFFu")]))
    A(("d4c: exit-note poll d2h_w all ones not checked", [("e.h2dr == 0xFFFFFFFFu || e.d2hw == 0xFFFFFFFFu", "e.h2dr == 0xFFFFFFFFu")]))
    A(("d4c: exit-note poll line not printed", [("\tprintf(\"SWEET WLAN: %s exit-note poll after %u ms: h2d_r0 0x%x d2h_w0 0x%x ring2 item %u w 0x%x 0x%x 0x%x 0x%x 0x%x 0x%x\\n\", pf, waited, e.h2dr, e.d2hw, rd + 1u, e.r2[0], e.r2[1], e.r2[2], e.r2[3], e.r2[4], e.r2[5]);\n", "")]))
    # the misses: gap, no deassert
    miss = (("no note", "snprintf(gb, sizeof gb, \"no DS_EXIT_NOTE in %u ms after the wake assert\", waited);"),
            ("wrong write index", "snprintf(gb, sizeof gb, \"d2h ring 2 write index %u after the wake, expected %u\", e.d2hw & 0xFFFFu, ne);"),
            ("epoch/xor", "snprintf(gb, sizeof gb, \"ring-2 item %u failed the epoch/xor check (%d)\", rd + 1u, ex);"),
            ("type", "snprintf(gb, sizeof gb, \"ring-2 item %u type 0x%x is not D2H_MAILBOX_DATA\", rd + 1u, type);"),
            ("bits", "snprintf(gb, sizeof gb, \"ring-2 item %u mailbox data 0x%x is not DS_EXIT_NOTE only\", rd + 1u, e.r2[3]);"))
    for nm, t in miss:
        A(("d4c: exit-note miss (%s) goes on to the deassert" % nm, [(t, "gb[0] = '\\0';")]))
    A(("d4c: exit-note miss without a gap", [("\t\t\tswl_gap(\"d4c\", gb, \"high\");\n\t\t\treturn 0;", "\t\t\treturn 0;")]))
    A(("d4c: exit-note index 0 or 3 accepted", [("\t} else if ((e.d2hw & 0xFFFFu) != ne) {", "\t} else if ((e.d2hw & 0xFFFFu) < ne) {")]))
    A(("d4c: exit-note no-note branch also on index 2", [("\tif ((e.d2hw & 0xFFFFu) == ns) {\n\t\tsnprintf(gb, sizeof gb, \"no DS_EXIT_NOTE", "\tif ((e.d2hw & 0xFFFFu) <= ne) {\n\t\tsnprintf(gb, sizeof gb, \"no DS_EXIT_NOTE")]))
    A(("d4c: exit-note epoch/xor not checked", [("\t\tif (ex != 0) {\n\t\t\tsnprintf(gb, sizeof gb, \"ring-2 item %u failed", "\t\tif (0) {\n\t\t\tsnprintf(gb, sizeof gb, \"ring-2 item %u failed")]))
    A(("d4c: exit-note type not checked", [("\t\t} else if (type != SWD4B_MSG_D2H_MBDATA) {", "\t\t} else if (0) {")]))
    A(("d4c: exit-note bits not checked", [("\t\t} else if (e.r2[3] != SWD4B_D2H_DS_EXIT_NOTE) {", "\t\t} else if (0) {")]))
    A(("d4c: exit-note any bit accepted", [("\t\t} else if (e.r2[3] != SWD4B_D2H_DS_EXIT_NOTE) {", "\t\t} else if ((e.r2[3] & SWD4B_D2H_DS_EXIT_NOTE) == 0u) {")]))
    A(("d4c: exit-note read from the wrong word", [("\t\tin[i] = (uint8_t)(e.r2[i / 4u] >> (8u * (i % 4u)));", "\t\tin[i] = (uint8_t)(e.r2[(i / 4u + 1u) % 6u] >> (8u * (i % 4u)));")]))
    A(("d4c: exit-note mailbox word is r2[2]", [("e.r2[3] != SWD4B_D2H_DS_EXIT_NOTE", "e.r2[2] != SWD4B_D2H_DS_EXIT_NOTE")]))
    A(("d4c: handshake done line not printed", [("\tprintf(\"SWEET WLAN: D4c DS handshake done (ack, wake, exit note, deassert)\\n\");\n", "")]))
    # the final full read
    A(("d4c: no final read", [("\tif (swg_d4_read(x, &cur, 0u)) {\n\t\treturn swg_stop(x, SWG_E_ADDR);\n\t}\n\tif (swg_d4_ones(x, &cur)) {\n\t\treturn swg_stop(x, SWG_E_DEAD);\n\t}\n\tex = swg_d4c_stops(x, &cur, \"final read\");\n\tif (ex != 0) {\n\t\treturn ex;\n\t}\n", "")]))
    A(("d4c: final read dead link not checked", [("\tif (swg_d4_ones(x, &cur)) {\n\t\treturn swg_stop(x, SWG_E_DEAD);\n\t}\n\tex = swg_d4c_stops(x, &cur, \"final read\");", "\tex = swg_d4c_stops(x, &cur, \"final read\");")]))
    A(("d4c: final read stops not applied", [("\tex = swg_d4c_stops(x, &cur, \"final read\");\n\tif (ex != 0) {\n\t\treturn ex;\n\t}\n", "")]))
    A(("d4c: final read happens before the C2 rows", [("\t\tex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);   /* D (the deassert), then C2 (the read index) */", "\tex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_D_DB);\n\tif (ex == 0) {\n\t\tex = swg_d4_read(x, &cur, 0u);\n\t}\n\tif (ex == 0) {\n\t\tex = swg_d4c_rows(x, SWG_D4C_C2_SLOT, SWG_D4C_C2_DB);\n\t}")]))
    A(("d4c: stops: TRAP/ASSERT not a stop", [("\tif ((s->pw.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {", "\tif (0) {")]))
    A(("d4c: stops: FWHALT not a stop", [("\tif ((s->pw.mb & SWG_D2H_FWHALT) != 0u) {", "\tif (0) {")]))
    A(("d4c: stops: the stop is not fatal", [("\t\tx->fail = \"the firmware reports assert/trap during the d4c handshake\";\n\t\treturn swg_stop(x, SWG_E_FATAL);", "\t\tx->fail = \"the firmware reports assert/trap during the d4c handshake\";\n\t\treturn 0;")]))
    # review d4c r1 F4: the pre-answer read
    A(("d4c: pre-answer read stops not applied (review d4c r1 F4)", [("\t\tex = swg_d4c_stops(x, &q, \"pre-answer read\");     /* review d4c r1 F4: a trap/assert/FWHALT since the baseline stops before any store */\n\t\tif (ex != 0) {\n\t\t\treturn ex;\n\t\t}\n", "")]))
    A(("d4c: pre-answer item 0 not compared with the baseline (review d4c r1 F4)", [("\t\tif (memcmp(q.r2, b->r2, sizeof q.r2) != 0) {\n\t\t\tno = \"ring-2 item 0 changed since the baseline, not answered\";\n\t\t} else if (n > 1u) {", "\t\tif (0) {\n\t\t\tno = \"ring-2 item 0 changed since the baseline, not answered\";\n\t\t} else if (n > 1u) {")]))
    A(("d4c: pre-answer item 0 compare skips its last word", [("memcmp(q.r2, b->r2, sizeof q.r2)", "memcmp(q.r2, b->r2, sizeof q.r2 - 4u)")]))
    A(("d4c: pre-answer item 0 compare skips its first word", [("memcmp(q.r2, b->r2, sizeof q.r2)", "memcmp(q.r2 + 1, b->r2 + 1, sizeof q.r2 - 4u)")]))
    A(("d4c: pre-answer item 0 compared with itself", [("memcmp(q.r2, b->r2, sizeof q.r2)", "memcmp(q.r2, q.r2, sizeof q.r2)")]))
    return out


# W-D4e (scan24, sweetwlan=21..23): header mutants (rows, concatenated into G2_MUTANTS: its adapter covers them); the scan_tests() cases of wlan_g2_host_test.c must FAIL on each
def _em(name, old, new):
    return ("E: " + name, [(old, new)])


E_G2_ROWS = [
    # constants
    _em("dma_rxoffset read at shared +0x28", "#define SWG_SH_RXOFF     0x24u", "#define SWG_SH_RXOFF     0x28u"),
    _em("7 response buffers in all", "#define SWG_E_RID_BUFS   8u ", "#define SWG_E_RID_BUFS   7u "),
    _em("7 event buffers in all", "#define SWG_E_EV_BUFS    8u ", "#define SWG_E_EV_BUFS    7u "),
    _em("re-post ids start at 13", "#define SWG_E_RID_RE0    12u ", "#define SWG_E_RID_RE0    13u "),
    _em("event ids start at 0x30", "#define SWG_E_EVID0      0x20u ", "#define SWG_E_EVID0      0x30u "),
    _em("transaction ids start at TRANS0 + 4", "#define SWG_E_TRANS0     (SWG_D4D_TRANS0 + 3u)", "#define SWG_E_TRANS0     (SWG_D4D_TRANS0 + 4u)"),
    _em("event cap 63", "#define SWG_E_MAX        64u ", "#define SWG_E_MAX        63u "),
    _em("event cap 65", "#define SWG_E_MAX        64u ", "#define SWG_E_MAX        65u "),
    _em("event cap 47 under sweetwlan=24", "#define SWG_E_MAX_CLM    48u ", "#define SWG_E_MAX_CLM    47u "),
    _em("event cap 49 under sweetwlan=24", "#define SWG_E_MAX_CLM    48u ", "#define SWG_E_MAX_CLM    49u "),
    _em("event cap 48 only from level 25", "x->emax = lv >= 24u ? SWG_E_MAX_CLM : SWG_E_MAX;", "x->emax = lv >= 25u ? SWG_E_MAX_CLM : SWG_E_MAX;"),
    _em("event cap 48 from level 23", "x->emax = lv >= 24u ? SWG_E_MAX_CLM : SWG_E_MAX;", "x->emax = lv >= 23u ? SWG_E_MAX_CLM : SWG_E_MAX;"),
    _em("event cap 64 under sweetwlan=24", "x->emax = lv >= 24u ? SWG_E_MAX_CLM : SWG_E_MAX;", "x->emax = SWG_E_MAX;"),
    _em("budget 7000 ms", "#define SWG_E_BUDGET_MS  8000u ", "#define SWG_E_BUDGET_MS  7000u "),
    _em("budget 9000 ms", "#define SWG_E_BUDGET_MS  8000u ", "#define SWG_E_BUDGET_MS  9000u "),
    _em("sync id 0x1235", "#define SWG_E_SYNC       0x1234u ", "#define SWG_E_SYNC       0x1235u "),
    _em("batch of 7 items", "#define SWG_E_BATCH      16u ", "#define SWG_E_BATCH      7u "),
    _em("payload shadow of 354 words (one short of the longest clmload chunk)", "#define SWG_E_PAYW       355u ", "#define SWG_E_PAYW       354u "),
    _em("frame header 71 B", "#define SWG_E_FRAME      72u ", "#define SWG_E_FRAME      71u "),
    _em("frame header 73 B", "#define SWG_E_FRAME      72u ", "#define SWG_E_FRAME      73u "),
    _em("ethertype 0x886d", "#define SWG_E_ETYPE      0x886Cu ", "#define SWG_E_ETYPE      0x886Du "),
    _em("ESCAN_RESULT is event 70", "#define SWG_E_EV_ESCAN   69u ", "#define SWG_E_EV_ESCAN   70u "),
    _em("SUCCESS is status 1", "#define SWG_E_ST_OK      0u ", "#define SWG_E_ST_OK      1u "),
    _em("PARTIAL is status 9", "#define SWG_E_ST_PARTIAL 8u ", "#define SWG_E_ST_PARTIAL 9u "),
    _em("bss version 110", "#define SWG_E_BSS_VER    109u ", "#define SWG_E_BSS_VER    110u "),
    _em("bss record 88 B", "#define SWG_E_BSS_NEED   89u ", "#define SWG_E_BSS_NEED   88u "),
    _em("bss record 90 B", "#define SWG_E_BSS_NEED   89u ", "#define SWG_E_BSS_NEED   90u "),
    # row kinds / order
    _em("post rows: 9 item words", "return row < 10u ? SWG_K_ITEM : row == 10u ? SWG_K_H2DW", "return row < 9u ? SWG_K_ITEM : row == 9u ? SWG_K_H2DW"),
    _em("post rows: doorbell row 12", "row == 10u ? SWG_K_H2DW : row == 11u ? SWG_K_DB : -1;", "row == 10u ? SWG_K_H2DW : row == 12u ? SWG_K_DB : -1;"),
    _em("consume rows: doorbell first", "return row == 0u ? SWG_K_D2HR : row == 1u ? SWG_K_DB : -1;", "return row == 0u ? SWG_K_DB : row == 1u ? SWG_K_D2HR : -1;"),
    _em("payload row kind", "return row == 0u ? SWG_K_PAY : -1;", "return row == 0u ? SWG_K_ITEM : -1;"),
    _em("rows after the table need level 8", "(x->d4b < 8 || swg_e_kindx(x) != kind)", "(0 || swg_e_kindx(x) != kind)"),
    _em("rows after the table: kind not checked", "(x->d4b < 8 || swg_e_kindx(x) != kind)", "(x->d4b < 8)"),
    _em("d4b ceiling 13 (level 14 refused)", "x->d4b < 4 || x->d4b > 13 ||", "x->d4b < 4 || x->d4b > 12 ||"),
    _em("row counter not kept", "	x->d4dn++;\n	x->erow++;\n", "	x->d4dn++;\n"),
    _em("post does not start a row count", "	x->eop = SWG_OP_POST;\n	x->erow = 0u;\n", "	x->eop = SWG_OP_POST;\n"),
    _em("consume does not start a row count", "	x->eop = SWG_OP_CONS;\n	x->erow = 0u;\n", "	x->eop = SWG_OP_CONS;\n"),
    # L20 line at the higher levels, gaps
    _em("L20 end line only at level 20", "if (lv == n || (n == 20u && lv > 20u)) {", "if (lv == n) {"),
    _em("L20 hands over to L21 at every level", "		if (lv > 20u) {\n			return swg_e(x, lv);", "		if (lv > 21u) {\n			return swg_e(x, lv);"),
    _em("1-response-buffer gap at every level", "	if (lv < 21u) {\n		swl_gap(\"d4d\", \"only 1 ioctl response buffer posted", "	if (1) {\n		swl_gap(\"d4d\", \"only 1 ioctl response buffer posted"),
    _em("1-buffer d4b gap at every level", "		if (x->d4b < 8) {\n			swl_gap(\"d4b\", \"only 1 buffer posted", "		if (1) {\n			swl_gap(\"d4b\", \"only 1 buffer posted"),
    _em("8-event-buffer gap not said", "	swl_gap(\"scan\", \"8 event buffers posted (DHD posts 32)\", \"low\");\n", ""),
    # state at the start of L21
    _em("completions start at 3", "	x->ncpl = 2u;           /* L18 and L20 completions */", "	x->ncpl = 3u;           /* L18 and L20 completions */"),
    _em("event posts after L21 are 7", "	x->evp = SWG_E_EV_BUFS;\n", "	x->evp = SWG_E_EV_BUFS - 1u;\n"),
    _em("payload shadow word 0 starts at 0", "	x->epay[0] = SWG_D4D_CAP_W0;\n", ""),
    _em("dma_rxoffset not read", "	if (swg_rd(x, x->in.sh + SWG_SH_RXOFF, &x->rxoff)) {\n		return swg_stop(x, SWG_E_ADDR);\n	}", "	if (0) {\n		return swg_stop(x, SWG_E_ADDR);\n	}"),
    _em("dma_rxoffset all ones not refused", "	if (x->rxoff == 0xFFFFFFFFu) {", "	if (0) {"),
    _em("dma_rxoffset not applied to the frame", "ex = swg_e_rdb(x, bp, x->rxoff, SWG_E_FRAME, fr);", "ex = swg_e_rdb(x, bp, 0u, SWG_E_FRAME, fr);"),
    _em("dma_rxoffset not applied to the escan data", "ex = swg_e_rdb(x, bp, x->rxoff + SWG_E_FRAME, rb, fr + SWG_E_FRAME);", "ex = swg_e_rdb(x, bp, SWG_E_FRAME, rb, fr + SWG_E_FRAME);"),
    _em("rx offset not in the length bound", "dlen > SWD_EVBUF_LEN - x->rxoff", "dlen > SWD_EVBUF_LEN"),
    _em("rx offset above the buffer not refused", "x->rxoff > SWD_EVBUF_LEN || ", ""),
    _em("short event frame not refused", "if (dlen < SWG_E_FRAME || ", "if ("),
    _em("unaligned read: byte kept at the wrong place", "out[i + k - off] = (uint8_t)(v >> (8u * k));", "out[i + k - off] = (uint8_t)(v >> (8u * (3u - k)));"),
    _em("unaligned read starts at the offset", "for (i = off & ~3u; i < off + n; i += 4u) {", "for (i = off; i < off + n; i += 4u) {"),
    # L21
    _em("L21: response ids from 6", "ex = swg_e_post_resp(x, SWG_D4D_RID0 + 2u + j, j);", "ex = swg_e_post_resp(x, SWG_D4D_RID0 + 3u + j, j);"),
    _em("L21: response buffers from 0", "ex = swg_e_post_resp(x, SWG_D4D_RID0 + 2u + j, j);", "ex = swg_e_post_resp(x, SWG_D4D_RID0 + 2u + j, j - 1u);"),
    _em("L21: response posts start at buffer 0", "	for (j = 1; j < SWG_E_RID_BUFS; j++) {\n		ex = swg_e_post_resp", "	for (j = 0; j < SWG_E_RID_BUFS; j++) {\n		ex = swg_e_post_resp"),
    _em("L21: event posts start at buffer 0", "	for (j = 1; j < SWG_E_EV_BUFS; j++) {\n		ex = swg_e_post_ev(x, j, j);", "	for (j = 0; j < SWG_E_EV_BUFS; j++) {\n		ex = swg_e_post_ev(x, j, j);"),
    _em("L21: event posts first", "	for (j = 1; j < SWG_E_RID_BUFS; j++) {\n		ex = swg_e_post_resp(x, SWG_D4D_RID0 + 2u + j, j);\n		if (ex != 0) {\n			return ex;\n		}\n	}\n	for (j = 1; j < SWG_E_EV_BUFS; j++) {\n		ex = swg_e_post_ev(x, j, j);\n		if (ex != 0) {\n			return ex;\n		}\n	}", "	for (j = 1; j < SWG_E_EV_BUFS; j++) {\n		ex = swg_e_post_ev(x, j, j);\n		if (ex != 0) {\n			return ex;\n		}\n	}\n	for (j = 1; j < SWG_E_RID_BUFS; j++) {\n		ex = swg_e_post_resp(x, SWG_D4D_RID0 + 2u + j, j);\n		if (ex != 0) {\n			return ex;\n		}\n	}"),
    _em("event post: the posted pktid not recorded for the slot", "	if (ex == 0) {\n		x->epid[buf] = id;\n	}", "	if (ex == 0) {\n		x->epid[buf] = 0u;\n	}"),
    _em("event post: type is a response post", "swd4b_item_evbuf(it, id, (uint16_t)SWD_EVBUF_LEN, x->in.evbuf + (uint64_t)buf * SWD_EVBUF_LEN, swg_ep0(x));", "swd4b_item_respbuf(it, id, (uint16_t)SWD_EVBUF_LEN, x->in.evbuf + (uint64_t)buf * SWD_EVBUF_LEN, swg_ep0(x));"),
    _em("event post: response buffer base", "swd4b_item_evbuf(it, id, (uint16_t)SWD_EVBUF_LEN, x->in.evbuf + (uint64_t)buf * SWD_EVBUF_LEN,", "swd4b_item_evbuf(it, id, (uint16_t)SWD_EVBUF_LEN, x->in.respbuf + (uint64_t)buf * SWD_EVBUF_LEN,"),
    _em("response post: stride", "x->in.respbuf + (uint64_t)buf * SWD_RESPBUF_LEN, swg_ep0(x));", "x->in.respbuf + (uint64_t)buf * 4096u, swg_ep0(x));"),
    _em("L21: the watch is not made", "	ex = swg_d4d_wait(x, 0u, &w, &waited);\n	if (ex != 0) {\n		return ex;\n	}\n	if (w < x->d4rd) {\n		SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n		return 0;\n	}\n	if (w > x->d4rd) {", "	w = x->d4rd;\n	if (w < x->d4rd) {\n		SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n		return 0;\n	}\n	if (w > x->d4rd) {"),
    _em("L21: events not consumed", "		if (e > x->d4rd) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n		}\n	}\n	printf(\"SWEET WLAN: L21 posted", "	}\n	printf(\"SWEET WLAN: L21 posted"),
    _em("L21: the item count line", "x->d4wr, x->d4rd - r0);", "x->d4wr, x->d4rd);"),
    _em("L21 done line dropped", "	swg_d4d_done(x, 21u, lv, x->d4wr, x->d4rd);\n", ""),
    _em("L22 done line dropped", "	swg_d4d_done(x, 22u, lv, x->d4wr, x->d4rd);\n", ""),
    _em("level 21 goes on to L22", "	if (lv == 21u) {\n		return 0;\n	}\n	x->elv = 22u;", "	x->elv = 22u;"),
    _em("level 22 goes on to L23", "	if (lv == 22u) {\n		return 0;\n	}\n	x->elv = 23u;", "	x->elv = 23u;"),
    _em("L22 level number in the STOP lines", "	x->elv = 22u;", "	x->elv = 21u;"),
    _em("L23 level number in the STOP lines", "	x->elv = 23u;", "	x->elv = 22u;"),
    _em("L21 soft stop goes on", "	ex = swg_e_l21(x);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}", "	ex = swg_e_l21(x);\n	if (ex != 0) {\n		return ex;\n	}"),
    _em("L22 soft stop goes on", "	ex = swg_e_l22(x, lv >= 24u);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}", "	ex = swg_e_l22(x, lv >= 24u);\n	if (ex != 0) {\n		return ex;\n	}"),
    # events
    _em("event: pktid not checked", "		if (rid != 0u && x->epid[sl] == rid) {", "		if (rid != 0u) {"),
    _em("event: the pktid is not looked up (FIFO id of event q)", "		if (rid != 0u && x->epid[sl] == rid) {", "		if (rid != 0u && (x->epid[sl] == rid || SWG_E_EVID0 + x->evn == rid)) {"),
    _em("event: id 0 (DHD_PKTID_INVALID) matches an empty slot", "		if (rid != 0u && x->epid[sl] == rid) {", "		if (x->epid[sl] == rid) {"),
    _em("event: the consumed pktid stays accepted", "	x->epid[sl] = 0u;       /* consumed", "	x->epid[sl] += 0u;       /* consumed"),
    _em("event: an unposted pktid is not a stop", "	if (sl >= SWG_E_EV_BUFS) {\n		SWG_E_STOP(x, \"event pktid %#x is not a posted event buffer\", rid);\n		return 0;\n	}", "	if (sl >= SWG_E_EV_BUFS) {\n		sl = 0u;\n	}"),
    _em("event: the frame is read from the slot of event q, not of the pktid", "	bp = x->in.evbuf + (uint64_t)sl * SWD_EVBUF_LEN;", "	bp = x->in.evbuf + (uint64_t)(q % SWG_E_EV_BUFS) * SWD_EVBUF_LEN;"),
    _em("event: the consumed slot is not queued (re-post goes to the slot of the post count)", "	x->evqb[x->evq] = sl;", "	x->evqb[x->evq] = x->evp % SWG_E_EV_BUFS;"),
    _em("re-post: the buffer is the post count slot, not the consumed one", "		ex = swg_e_post_ev(x, x->evp, x->evqb[0]);", "		ex = swg_e_post_ev(x, x->evp, x->evp % SWG_E_EV_BUFS);"),
    _em("re-post: the queue is not shifted", "		for (k = 0; k < x->evq; k++) {\n			x->evqb[k] = x->evqb[k + 1u];\n		}\n", ""),
    _em("init: slot 0 holds no L14 pktid", "	x->epid[0] = SWG_D4_REQID;", "	x->epid[0] = 0u;"),
    _em("event: the cap is not enforced", "	if (q >= x->emax) {", "	if (0) {"),
    _em("event: the cap gap not said", "		swl_gap(\"scan\", x->emax == SWG_E_MAX ? \"event cap 64 reached\" : \"event cap 48 reached\", \"high\");\n", ""),
    _em("event cap 64 gap names 48", "x->emax == SWG_E_MAX ? \"event cap 64 reached\" : \"event cap 48 reached\"", "x->emax == SWG_E_MAX ? \"event cap 48 reached\" : \"event cap 48 reached\""),
    _em("event cap 48 gap names 64", "x->emax == SWG_E_MAX ? \"event cap 64 reached\" : \"event cap 48 reached\"", "x->emax == SWG_E_MAX ? \"event cap 64 reached\" : \"event cap 64 reached\""),
    _em("event: ethertype not checked", "if (((uint32_t)fr[12] << 8 | fr[13]) != SWG_E_ETYPE || ", "if ("),
    _em("event: OUI byte 0 not checked", "|| fr[19] != 0x00u ||", "||"),
    _em("event: OUI byte 1 not checked", "|| fr[20] != 0x10u ||", "||"),
    _em("event: OUI byte 2 not checked", "|| fr[21] != 0x18u ||", "||"),
    _em("event: usr_subtype not checked", "|| ((uint32_t)fr[22] << 8 | fr[23]) != 1u) {", ") {"),
    _em("event: type from the wrong offset", "ev->type = swg_e_be32(fr + 28);", "ev->type = swg_e_be32(fr + 32);"),
    _em("event: status from the wrong offset", "ev->status = swg_e_be32(fr + 32);", "ev->status = swg_e_be32(fr + 36);"),
    _em("event: datalen from the wrong offset", "ev->edlen = swg_e_be32(fr + 44);", "ev->edlen = swg_e_be32(fr + 40);"),
    _em("event: type little endian", "ev->type = swg_e_be32(fr + 28);", "ev->type = swd4b_get32(fr + 28);"),
    _em("event: datalen above the frame data allowed", "	if (dlen < SWG_E_FRAME || ev->edlen > dlen - SWG_E_FRAME) {", "	if (0) {"),      # patch 0281: the check lives in swg_e_fparse now
    _em("event: event_data_len from the wrong item byte", "uint32_t dlen = swg_e_le16(in + 12), rb = 0, sl;", "uint32_t dlen = swg_e_le16(in + 14), rb = 0, sl;"),
    _em("event: the first frame header not dumped", "	if (q == 0u) {\n		swg_e_hex(\"first event frame header\", fr, SWG_E_FRAME);\n	}", ""),
    _em("event: the first escan data not dumped", "		if (x->nbss == 0u && ev->status == SWG_E_ST_PARTIAL) {\n			swg_e_hex(\"first escan result data\", fr + SWG_E_FRAME, rb);\n		}", ""),
    _em("event: escan data needs only 11 B", "if (ev->type == SWG_E_EV_ESCAN && ev->edlen >= 12u) {", "if (ev->type == SWG_E_EV_ESCAN && ev->edlen >= 11u) {"),
    _em("event: record read without the size test", "rb = ev->status == SWG_E_ST_PARTIAL && ev->edlen >= 12u + SWG_E_BSS_NEED ? 12u + SWG_E_BSS_NEED : 12u;", "rb = ev->status == SWG_E_ST_PARTIAL ? 12u + SWG_E_BSS_NEED : 12u;"),
    _em("event: sync_id from the wrong offset", "ev->sync = swg_e_le16(fr + SWG_E_FRAME + 8);", "ev->sync = swg_e_le16(fr + SWG_E_FRAME + 4);"),
    _em("event: bss_count from the wrong offset", "ev->bssn = swg_e_le16(fr + SWG_E_FRAME + 10);", "ev->bssn = swg_e_le16(fr + SWG_E_FRAME + 8);"),
    _em("event: bss version from the wrong offset", "ev->bver = swd4b_get32(r);", "ev->bver = swd4b_get32(r + 4);"),
    _em("event: BSSID from the wrong offset", "memcpy(ev->bssid, r + 8, 6);", "memcpy(ev->bssid, r + 9, 6);"),
    _em("event: SSID length from the wrong offset", "ev->sl = r[18];", "ev->sl = r[19];"),
    _em("event: SSID from the wrong offset", "memcpy(ev->ssid, r + 19, 32);", "memcpy(ev->ssid, r + 20, 32);"),
    _em("event: chanspec from the wrong offset", "ev->cs = swg_e_le16(r + 72);", "ev->cs = swg_e_le16(r + 74);"),
    _em("event: RSSI from the wrong offset", "ev->rssi = (int)(int16_t)swg_e_le16(r + 78);", "ev->rssi = (int)(int16_t)swg_e_le16(r + 80);"),
    _em("event: RSSI unsigned", "ev->rssi = (int)(int16_t)swg_e_le16(r + 78);", "ev->rssi = (int)swg_e_le16(r + 78);"),
    _em("event: ctl_ch from the wrong offset", "ev->ctl = r[88];", "ev->ctl = r[87];"),
    _em("event: no bss parsed for bss_count 0", "if (rb > 12u && ev->bssn >= 1u) {", "if (rb > 12u) {"),
    _em("event: consumed events not counted", "	x->evn++;\n	x->evq++;\n	return 0;", "	x->evq++;\n	return 0;"),
    _em("event: consumed events not queued for re-post", "	x->evn++;\n	x->evq++;\n	return 0;", "	x->evn++;\n	return 0;"),
    _em("re-post: the post count not kept", "		x->evp++;\n		x->evq--;", "		x->evq--;"),
    _em("re-post: the queue not drained", "		x->evp++;\n		x->evq--;", "		x->evp++;\n		x->evq -= 0u;"),
    _em("re-post: a deferred event is posted at once in L21/L22", "static int\nswg_e_idle(struct swg_wx *x)\n{", "static int\nswg_e_idle(struct swg_wx *x)\n{\n	if (x->elv >= 21u && x->evq > 0u) {\n		(void)swg_e_evflush(x);\n	}"),
    # item loop
    _em("items: epoch/xor not checked", "		if (chk != 0) {\n			printf(\"SWEET WLAN: D4e ring2 item", "		if (0) {\n			printf(\"SWEET WLAN: D4e ring2 item"),
    _em("items: a 0x24 behind the first is not left", "if (type == SWD4B_MSG_D2H_MBDATA && k != x->d4rd) {", "if (0) {"),
    _em("items: ack with no request", "if (io == NULL || !io->on) {", "if (0) {"),
    _em("items: status not checked", "} else if (f.status != 0 && !(type == SWD4B_MSG_IOCTL_CMPLT && io->soft)) {", "} else if (0) {"),
    _em("items: soft status for every item", "} else if (f.status != 0 && !(type == SWD4B_MSG_IOCTL_CMPLT && io->soft)) {", "} else if (f.status != 0 && !(io->soft)) {"),
    _em("items: no soft status", "} else if (f.status != 0 && !(type == SWD4B_MSG_IOCTL_CMPLT && io->soft)) {", "} else if (f.status != 0) {"),
    _em("items: cmd not checked", "} else if (f.cmd != io->cmd) {", "} else if (0) {"),
    _em("items: ack request_id not checked", "if (f.request_id != SWD4B_IOCTL_REQID) {", "if (0) {"),
    _em("items: second ack not refused", "} else if ((io->got & 1u) != 0u) {", "} else if (0) {"),
    _em("items: completion trans_id not checked", "if (f.trans_id != io->trans) {", "if (0) {"),
    _em("items: completion request_id not checked", "} else if ((sl = swg_e_rslot(x, f.request_id)) >= SWG_E_RID_BUFS) {", "} else if ((sl = swg_e_rslot(x, f.request_id)) >= SWG_E_RID_BUFS && 0) {"),
    _em("items: the old FIFO request_id check is kept (T-af77: id 11 answered, 4 expected)", "} else if ((sl = swg_e_rslot(x, f.request_id)) >= SWG_E_RID_BUFS) {", "} else if ((sl = swg_e_rslot(x, f.request_id)) >= SWG_E_RID_BUFS || f.request_id != SWG_D4D_RID0 + x->ncpl) {"),
    _em("items: the completion slot is not kept", "				io->slot = sl;\n", ""),
    _em("rslot: every id matches slot 0", "		if (x->rpid[j] == id) {", "		if (1) {"),
    _em("rslot: a slot is matched by index, not by pktid", "		if (x->rpid[j] == id) {", "		if (x->rpid[j] != 0u && id == SWG_D4D_RID0 + j) {"),
    _em("post: the pktid of a posted slot is not recorded", "		x->rpid[buf] = id;\n", ""),
    _em("init: slot 0 holds the id of the L20 re-post minus one", "x->rpid[0] = SWG_D4D_RID0 + 2u;", "x->rpid[0] = SWG_D4D_RID0 + 1u;"),
    _em("items: resp_len not bounded", "} else if (f.resp_len > SWD_RESPBUF_LEN) {", "} else if (0) {"),
    _em("items: second completion not refused", "} else if ((io->got & 2u) != 0u) {", "} else if (0) {"),
    _em("items: ack bit is the completion bit", "				io->got |= 1u;\n			} else {", "				io->got |= 2u;\n			} else {"),
    _em("items: the completion is not kept", "				io->cm = f;\n", ""),
    _em("items: unexpected type ignored", "			SWG_E_STOP(x, \"unexpected ring-2 type 0x%x at item %u\", type, k);\n			break;", "			*e = k + 1u;\n			continue;"),
    _em("items: events only judged in L23", "			if (sc == NULL) {\n				*e = k + 1u;\n				continue;\n			}", "			if (sc == NULL) {\n				continue;\n			}"),
    _em("items: a foreign sync_id counted", "} else if (ev.sync != SWG_E_SYNC) {", "} else if (0) {"),
    _em("items: PARTIAL results not counted", "					sc->nres++;\n", ""),
    _em("items: bss_count above 1 not said", "						swl_gap(\"scan\", \"bss_count above 1, only the first record parsed\", \"medium\");\n", ""),
    _em("items: short record not said", "						swl_gap(\"scan\", \"PARTIAL result shorter than one bss record\", \"medium\");\n", ""),
    _em("items: other record version parsed anyway", "} else if (ev.bver != SWG_E_BSS_VER) {", "} else if (0) {"),
    _em("items: channel from chanspec always", "const uint32_t ch = ev.ctl != 0u ? ev.ctl : (ev.cs & 0xFFu);", "const uint32_t ch = ev.cs & 0xFFu;"),
    _em("items: channel from ctl_ch only", "const uint32_t ch = ev.ctl != 0u ? ev.ctl : (ev.cs & 0xFFu);", "const uint32_t ch = ev.ctl;"),
    _em("items: bss number not counted", "						x->nbss++;\n", ""),
    _em("items: channel bitmap word", "x->nchan[(ch >> 5) & 7u] |= 1u << (ch & 31u);", "x->nchan[(ch >> 4) & 7u] |= 1u << (ch & 31u);"),
    _em("items: channel bitmap bit", "x->nchan[(ch >> 5) & 7u] |= 1u << (ch & 31u);", "x->nchan[(ch >> 5) & 7u] |= 1u << (ch & 15u);"),
    _em("items: SUCCESS does not end the scan", "				} else if (ev.status == SWG_E_ST_OK) {\n					sc->done = 1;", "				} else if (ev.status == SWG_E_ST_OK) {\n					sc->done = 0;"),
    _em("items: another status does not end the scan", "					swl_gap(\"scan\", gs, \"high\");\n					sc->done = 2;", "					swl_gap(\"scan\", gs, \"high\");"),
    _em("items: the other-status gap not said", "					swl_gap(\"scan\", gs, \"high\");\n", ""),
    _em("items: SSID printable test", "pr = pr && ev->ssid[i] >= 0x20u && ev->ssid[i] < 0x7Fu;", "pr = pr && ev->ssid[i] >= 0x20u;"),
    _em("items: SSID length bound", "int pr = ev->sl <= 32u;", "int pr = 1;"),
    _em("items: SSID start of printable", "pr = pr && ev->ssid[i] >= 0x20u && ev->ssid[i] < 0x7Fu;", "pr = pr && ev->ssid[i] > 0x20u && ev->ssid[i] < 0x7Fu;"),
    # idle look, call
    _em("idle: the look is not made", "	ex = swg_d4d_waitn(x, 0u, 1u, &w, &waited);\n	if (ex != 0) {\n		return ex;\n	}\n	if (w < x->d4rd) {\n		SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n		return 0;\n	}\n	if (w == x->d4rd) {\n		return 0;\n	}\n	ex = swg_e_items(x, w, &io, NULL, &e);", "	return 0;\n	ex = swg_e_items(x, w, &io, NULL, &e);"),
    _em("idle: events not consumed", "	if (ex != 0 || x->d4dstop || e == x->d4rd) {\n		return ex;\n	}\n	return swg_d4d_consume(x, x->d4rd, e);", "	return ex;"),
    _em("idle: below the read count is not a stop", "	if (w < x->d4rd) {\n		SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n		return 0;\n	}\n	if (w == x->d4rd) {\n		return 0;\n	}", "	if (w <= x->d4rd) {\n		return 0;\n	}"),
    _em("call: no idle look", "	ex = swg_e_idle(x);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	if (name != NULL) {", "	if (name != NULL) {"),
    _em("call: transaction id not advanced", "	x->ereq++;\n", ""),
    _em("call: soft flag dropped", "	io.soft = soft;\n", ""),
    _em("call: response buffer is the FIFO one, not the one of the completion's pktid", "	buf = io.slot;\n", "	buf = (c - 2u) % SWG_E_RID_BUFS;\n"),
    _em("call: response buffer is always slot 0", "	buf = io.slot;\n", "	buf = 0u;\n"),
    _em("call: a consumed slot keeps its pktid", "	x->rpid[buf] = 0u;      /* consumed: the same pktid is not accepted again until it is re-posted under a new id */\n", ""),
    _em("call: re-post id", "return swg_e_post_resp(x, SWG_E_RID_RE0 + c - 2u, buf);", "return swg_e_post_resp(x, SWG_E_RID_RE0 + c, buf);"),
    # T-866b: eight rows on the old response FIFO (x->rbuf, x->nrep, "buf = x->rbuf[c % 16u]") dropped: T-84b0/T-f1e2 replaced the FIFO by the pktid lookup (x->rpid / swg_e_rslot, buf = io.slot, re-post id SWG_E_RID_RE0 + c - 2u); the rows "call: response buffer is the FIFO one, not the one of the completion's pktid", "call: response buffer is always slot 0", "call: a consumed slot keeps its pktid" and "call: re-post id" above kill the same sites, and the one below restates the pktid record.
    _em("post_resp: pktid of the posted slot not recorded", "	if (ex == 0) {\n		x->rpid[buf] = id;\n	}", "	if (ex == 0) {\n	}"),
    # T-866b: the second "call: re-post id" row (x->nrep++) dropped: the real glue posts SWG_E_RID_RE0 + c - 2u (patch 0174), nrep is not in it; the row above kills the same site.
    _em("call: completion count not kept", "	*cmo = io.cm;\n	x->ncpl++;\n", "	*cmo = io.cm;\n"),
    _em("call: no re-post", "	if (repost) {\n		return swg_e_post_resp(", "	if (0) {\n		return swg_e_post_resp("),
    _em("call: events re-posted in L22", "	if (sc != NULL) {\n		ex = swg_e_evflush(x);\n		if (ex != 0) {\n			return ex;\n		}\n	}\n	while (io.got != 3u) {", "	if (1) {\n		ex = swg_e_evflush(x);\n		if (ex != 0) {\n			return ex;\n		}\n	}\n	while (io.got != 3u) {"),
    _em("call: no batch consume", "		if (e > x->d4rd) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			if (sc != NULL) {", "		if (0) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			if (sc != NULL) {"),
    _em("call: no write index below the read count check", "		if (w < x->d4rd) {\n			SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n			return 0;\n		}\n		if (w == x->d4rd) {\n			SWG_E_STOP(x, \"no ioctl", "		if (w == x->d4rd) {\n			SWG_E_STOP(x, \"no ioctl"),
    _em("call: no answer is not a stop", "		if (w == x->d4rd) {\n			SWG_E_STOP(x, \"no ioctl %s in %u ms\", io.got == 0u ? \"answer\" : \"completion\", spent);\n			return 0;\n		}", ""),
    # L22
    _em("L22: scan_ver answer 8 B accepted short", "	if (cm.resp_len < 6u) {", "	if (cm.resp_len < 4u) {"),
    _em("L22: scan_ver major offset", "mj = swg_e_le16(rsp + 4); ", "mj = swg_e_le16(rsp + 2); "),
    _em("L22: only major 2", "if (mj != 2u && mj != 3u) {", "if (mj != 2u) {"),
    _em("L22: only major 3", "if (mj != 2u && mj != 3u) {", "if (mj != 3u) {"),
    # review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 3: the 16-bit major is checked whole (0x0102 refused), 4 refused
    _em("L22: major narrowed to a byte before the check", "mj = swg_e_le16(rsp + 4); ", "mj = (uint8_t)swg_e_le16(rsp + 4); "),
    _em("L22: major 4 accepted", "if (mj != 2u && mj != 3u) {", "if (mj != 2u && mj != 3u && mj != 4u) {"),
    _em("L22: major 1 accepted", "if (mj != 2u && mj != 3u) {", "if (mj != 1u && mj != 2u && mj != 3u) {"),
    _em("L22: major 0 accepted", "if (mj != 2u && mj != 3u) {", "if (mj > 3u) {"),
    _em("L22: scan_ver length 14", "nm[0], NULL, 6u, 9u + 6u, 0, 6u, rsp, 1, &cm, NULL);", "nm[0], NULL, 6u, 9u + 5u, 0, 6u, rsp, 1, &cm, NULL);"),
    _em("L22: scan_ver length field of the line", "swg_e_le16(rsp), swg_e_le16(rsp + 2), mj);", "swg_e_le16(rsp), mj, swg_e_le16(rsp + 2));"),
    _em("L22: sup_wpa length 11", "nm[1], NULL, 4u, 8u + 4u, 1, 4u, rsp, 1, &cm, NULL);", "nm[1], NULL, 4u, 8u + 3u, 1, 4u, rsp, 1, &cm, NULL);"),
    _em("L22: sup_wpa is a hard status", "nm[1], NULL, 4u, 8u + 4u, 1, 4u, rsp, 1, &cm, NULL);", "nm[1], NULL, 4u, 8u + 4u, 0, 4u, rsp, 1, &cm, NULL);"),
    _em("L22: sup_wpa gap not said", "		swl_gap(\"scan\", \"sup_wpa get status non-zero or answer short\", \"medium\");\n", ""),
    _em("L22: sup_wpa value from the wrong place", "cm.resp_len >= 4u ? (int)swd4b_get32(rsp) : 0);", "cm.resp_len >= 4u ? (int)swd4b_get32(rsp + 4) : 0);"),
    _em("L22: event_msgs_ext GET version", "	arg[0] = 1u;\n	arg[2] = 26u;\n	memset(rsp, 0, sizeof rsp);", "	arg[0] = 2u;\n	arg[2] = 26u;\n	memset(rsp, 0, sizeof rsp);"),
    _em("L22: event_msgs_ext GET len 25", "	arg[0] = 1u;\n	arg[2] = 26u;\n	memset(rsp, 0, sizeof rsp);", "	arg[0] = 1u;\n	arg[2] = 25u;\n	memset(rsp, 0, sizeof rsp);"),
    _em("L22: event_msgs_ext GET length 44", "nm[2], arg, 30u, 15u + 30u, 0, 30u, rsp, 1, &cm, NULL);", "nm[2], arg, 30u, 15u + 29u, 0, 30u, rsp, 1, &cm, NULL);"),
    _em("L22: event_msgs_ext answer length not checked", "	if (cm.resp_len < 30u || rsp[2] != 26u) {", "	if (cm.resp_len < 30u) {"),
    _em("L22: event_msgs_ext answer size not checked", "	if (cm.resp_len < 30u || rsp[2] != 26u) {", "	if (rsp[2] != 26u) {"),
    _em("L22: event_msgs_ext SET command 1", "	arg[1] = 3u;\n	arg[2] = 26u;\n	arg[4u + SWG_E_EV_ESCAN / 8u]", "	arg[1] = 1u;\n	arg[2] = 26u;\n	arg[4u + SWG_E_EV_ESCAN / 8u]"),
    _em("L22: event_msgs_ext SET bit 68", "(uint8_t)(1u << (SWG_E_EV_ESCAN % 8u));", "(uint8_t)(1u << ((SWG_E_EV_ESCAN - 1u) % 8u));"),
    _em("L22: event_msgs_ext SET byte 9", "arg[4u + SWG_E_EV_ESCAN / 8u] =", "arg[4u + SWG_E_EV_ESCAN / 8u + 1u] ="),
    _em("L22: event_msgs_ext SET is a GET_VAR", "ex = swg_e_call(x, SWD4B_WLC_SET_VAR, \"WLC_SET_VAR event_msgs_ext\"", "ex = swg_e_call(x, SWD4B_WLC_GET_VAR, \"WLC_SET_VAR event_msgs_ext\""),
    _em("L22: WLC_UP with a payload length", "ex = swg_e_call(x, SWD4B_WLC_UP, \"WLC_UP\", NULL, NULL, 0u, 0u, 0, 0u, NULL, 1, &cm, NULL);", "ex = swg_e_call(x, SWD4B_WLC_UP, \"WLC_UP\", NULL, NULL, 0u, 4u, 0, 0u, NULL, 1, &cm, NULL);"),
    _em("L22: WLC_UP is not re-posted", "ex = swg_e_call(x, SWD4B_WLC_UP, \"WLC_UP\", NULL, NULL, 0u, 0u, 0, 0u, NULL, 1, &cm, NULL);", "ex = swg_e_call(x, SWD4B_WLC_UP, \"WLC_UP\", NULL, NULL, 0u, 0u, 0, 0u, NULL, 0, &cm, NULL);"),
    _em("L22: sup_wpa is not re-posted", "nm[1], NULL, 4u, 8u + 4u, 1, 4u, rsp, 1, &cm, NULL);", "nm[1], NULL, 4u, 8u + 4u, 1, 4u, rsp, 0, &cm, NULL);"),
    # payload
    _em("payload: skip of an unchanged word dropped", "		if (v == x->epay[i]) {\n			continue;\n		}", ""),
    _em("payload: shadow not updated", "		x->epay[i] = v;\n", ""),
    _em("payload: old value is the new one", "off, x->epay[i], v, 0, \"d4e payload word\");", "off, v, v, 0, \"d4e payload word\");"),
    _em("payload: word offset", "swg_hoff(x, x->in.ioctbuf + 4u * i, 4u, &off)", "swg_hoff(x, x->in.ioctbuf + 4u * (i + 1u), 4u, &off)"),
    _em("payload: name copy stops early", "	while (name[nl] != '\\0') {\n		b[nl] = (uint8_t)name[nl];\n		nl++;\n	}", "	while (name[nl] != '\\0' && nl < 4u) {\n		b[nl] = (uint8_t)name[nl];\n		nl++;\n	}"),
    _em("payload: arg starts at the NUL", "b[nl + 1u + i] = arg[i];", "b[nl + i] = arg[i];"),
    _em("payload: the longer than shadow check", "	if (n > 4u * SWG_E_PAYW) {", "	if (0) {"),
    # L23
    _em("L23: escan version not scan_ver's", "	swd4b_put32(arg, emajor);\n", "	swd4b_put32(arg, 2u);\n"),      # patch 0292: the START is built by swg_e_escan_arg now (boot scan and keep scan share it)
    _em("L23: params version not scan_ver's", "	arg[8] = (uint8_t)emajor;\n", "	arg[8] = 2u;\n"),
    _em("L23: action ABORT", "	arg[4] = 1u;\n	arg[6] = (uint8_t)sync;", "	arg[4] = 3u;\n	arg[6] = (uint8_t)sync;"),
    _em("L23: sync low byte", "	arg[6] = (uint8_t)sync;\n", "	arg[6] = (uint8_t)(sync >> 8);\n"),
    _em("L23: sync high byte", "	arg[7] = (uint8_t)(sync >> 8);\n", "	arg[7] = (uint8_t)(sync);\n"),
    _em("L23: params length 80", "	arg[10] = 72u;\n", "	arg[10] = 80u;\n"),
    _em("L23: bssid not broadcast", "	memset(arg + 48, 0xFF, 6);\n", "	memset(arg + 48, 0x00, 6);\n"),
    _em("L23: bss_type infrastructure", "	arg[54] = 2u;\n", "	arg[54] = 1u;\n"),
    _em("L23: active scan", "	arg[56] = 1u;\n", "	arg[56] = 0u;\n"),
    _em("L23: times not -1", "	memset(arg + 60, 0xFF, 16);\n", "	memset(arg + 60, 0xFF, 12);\n"),
    _em("L23: one channel", "	arg[56] = 1u;\n	memset(arg + 60, 0xFF, 16);\n", "	arg[56] = 1u;\n	memset(arg + 60, 0xFF, 16);\n	arg[76] = 1u;\n"),
    _em("L23: escan length 85", "\"escan\", arg, argn, 6u + argn, 0, 0u, rsp, 0, &cm, &sc);", "\"escan\", arg, argn, 6u + argn - 1u, 0, 0u, rsp, 0, &cm, &sc);"),
    _em("L23: escan is a GET_VAR", "ex = swg_e_call(x, SWD4B_WLC_SET_VAR, \"WLC_SET_VAR escan\"", "ex = swg_e_call(x, SWD4B_WLC_GET_VAR, \"WLC_SET_VAR escan\""),
    _em("L23: escan response buffer re-posted", "\"escan\", arg, argn, 6u + argn, 0, 0u, rsp, 0, &cm, &sc);", "\"escan\", arg, argn, 6u + argn, 0, 0u, rsp, 1, &cm, &sc);"),
    _em("L23: no deferred re-post (escan call without the scan state)", "\"escan\", arg, argn, 6u + argn, 0, 0u, rsp, 0, &cm, &sc);", "\"escan\", arg, argn, 6u + argn, 0, 0u, rsp, 0, &cm, NULL);"),
    # T-0fdd: the 7-channel escan of the shim (lv >= 24)
    _em("L23 7ch: channel_num 6", "		arg[76] = (uint8_t)SWG_E_NCS;", "		arg[76] = 6u;"),
    _em("L23 7ch: channel_num 0", "		arg[76] = (uint8_t)SWG_E_NCS;", "		arg[76] = 0u;"),
    _em("L23 7ch: list at arg + 78", "arg[80u + 2u * i] = (uint8_t)swg_e_cs[i];", "arg[78u + 2u * i] = (uint8_t)swg_e_cs[i];"),
    _em("L23 7ch: chanspec high byte lost", "arg[81u + 2u * i] = (uint8_t)(swg_e_cs[i] >> 8);", "arg[81u + 2u * i] = 0u;"),
    _em("L23 7ch: only 6 chanspecs", "for (i = 0; i < SWG_E_NCS; i++) {", "for (i = 0; i < SWG_E_NCS - 1u; i++) {"),
    _em("L23 7ch: chanspec 1 is 0x1002", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1002u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _em("L23 7ch: chanspec 2 is 0x1007", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1007u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _em("L23 7ch: chanspec 3 is 0x100c", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Cu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _em("L23 7ch: chanspec 4 is channel 37", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD025u, 0xD028u, 0xD02Cu, 0xD030u }"),
    _em("L23 7ch: chanspec 5 is channel 41", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD029u, 0xD02Cu, 0xD030u }"),
    _em("L23 7ch: chanspec 6 is channel 45", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Du, 0xD030u }"),
    _em("L23 7ch: chanspec 7 is channel 49", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u }", "{ 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD031u }"),
    _em("L23 7ch: argument 80 B", "const uint32_t argn = lv >= 24u ? 96u : 80u;", "const uint32_t argn = 80u;"),
    _em("L23 7ch: argument 96 B at every level", "const uint32_t argn = lv >= 24u ? 96u : 80u;", "const uint32_t argn = 96u;"),
    _em("L23 7ch: limit also at level 23", "swg_e_escan_arg(arg, x->emajor, lv >= 24u, SWG_E_SYNC);", "swg_e_escan_arg(arg, x->emajor, lv >= 23u, SWG_E_SYNC);"),
    _em("L23 7ch: limit never", "swg_e_escan_arg(arg, x->emajor, lv >= 24u, SWG_E_SYNC);", "swg_e_escan_arg(arg, x->emajor, 0, SWG_E_SYNC);"),
    _em("L23 7ch: limit gap dropped", "		swl_gap(\"scan\", \"escan limited to 7 bring-up channels\", \"low\");\n", ""),
    _em("L23 7ch: start line says all channels", "lv >= 24u ? \"7 bring-up channels (1 6 11 36 40 44 48)\" : \"all channels\"", "\"all channels\""),
    _em("L23: start line dropped", "	printf(\"SWEET WLAN: L23 escan start: version", "	(void)(\"SWEET WLAN: L23 escan start: version"),
    _em("L23: poll polls", "polls = (SWG_E_BUDGET_MS - spent) / SWG_POLL_MS;", "polls = (SWG_E_BUDGET_MS - spent) / SWG_POLL_MS + 1u;"),
    _em("L23: spent without the last poll", "spent += waited + SWG_POLL_MS;", "spent += waited + 1u;"),
    _em("L23: events not consumed", "		if (e > x->d4rd) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			ex = swg_e_evflush(x);", "		if (0) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			ex = swg_e_evflush(x);"),
    _em("L23: events not re-posted", "			ex = swg_e_evflush(x);\n			if (ex != 0) {\n				return ex;\n			}\n		}\n	}\n	for (i = 0; i < 8u; i++) {", "		}\n	}\n	for (i = 0; i < 8u; i++) {"),
    _em("L23: below the read count not a stop", "		if (w < x->d4rd) {\n			SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n			return 0;\n		}\n		if (w == x->d4rd) {\n			break;\n		}", "		if (w <= x->d4rd) {\n			break;\n		}"),
    _em("L23: channel count", "		for (; m != 0u; m &= m - 1u) {\n			nch++;\n		}", "		for (; m != 0u; m &= m - 1u) {\n			nch += 2u;\n		}"),
    _em("L23: channel words counted", "	for (i = 0; i < 8u; i++) {\n		uint32_t m = x->nchan[i];", "	for (i = 0; i < 7u; i++) {\n		uint32_t m = x->nchan[i];"),
    _em("L23: done line dropped", "		printf(\"SWEET WLAN: L23 scan done status 0 bss %u channels %u\\n\", x->nbss, nch);\n", ""),
    _em("L23: level 23 done without the SUCCESS", "	} else if (sc.done == 0) {\n		printf(\"SWEET WLAN: L23 scan not done", "	} else if (sc.done == 0) {\n		swg_d4d_done(x, 23u, lv, x->d4wr, x->d4rd);\n		printf(\"SWEET WLAN: L23 scan not done"),
    _em("L23: level 23 never done", "		swg_d4d_done(x, 23u, lv, x->d4wr, x->d4rd);\n	} else if (sc.done == 2) {", "	} else if (sc.done == 2) {"),
    _em("L23: not-done gap with results", "swl_gap(\"scan\", sc.nres == 0u ? \"no ESCAN_RESULT in 8000 ms\" : \"no SUCCESS in 8000 ms\", \"high\");", "swl_gap(\"scan\", \"no SUCCESS in 8000 ms\", \"high\");"),
    _em("L23: not-done gap without results", "swl_gap(\"scan\", sc.nres == 0u ? \"no ESCAN_RESULT in 8000 ms\" : \"no SUCCESS in 8000 ms\", \"high\");", "swl_gap(\"scan\", \"no ESCAN_RESULT in 8000 ms\", \"high\");"),
    _em("L23: not-done line dropped", "		printf(\"SWEET WLAN: L23 scan not done after %u ms: %u PARTIAL results, %u bss\\n\", spent, sc.nres, x->nbss);\n", ""),
    # T-bd94: a completion with a non-zero status dumps the request and the response bytes before the STOP (boot 6 of T-a0aa: escan -26)
    _em("dumpfail: no dump on a failed completion", "				if (type == SWD4B_MSG_IOCTL_CMPLT) {\n					swg_e_dumpfail(x, io, &f);\n				}\n", ""),
    _em("dumpfail: dump on a failed ack too", "				if (type == SWD4B_MSG_IOCTL_CMPLT) {\n					swg_e_dumpfail(x, io, &f);", "				if (1) {\n					swg_e_dumpfail(x, io, &f);"),
    _em("dumpfail: gap line dropped", "	swl_gap(\"scan\", gb, \"high\");\n	if (io->req != NULL) {", "	if (io->req != NULL) {"),
    _em("dumpfail: request row dropped", "		swg_e_dumprow(\"request\", f->cmd, io->req, io->reqn < SWG_E_DUMP_MAX ? io->reqn : SWG_E_DUMP_MAX);", "		(void)io->reqn;"),
    _em("dumpfail: request length 16 B short", "io->reqn < SWG_E_DUMP_MAX ? io->reqn : SWG_E_DUMP_MAX);", "io->reqn - 16u < SWG_E_DUMP_MAX ? io->reqn - 16u : SWG_E_DUMP_MAX);"),
    _em("dumpfail: request shadow not recorded", "		io.req = b;\n		io.reqn = wlen;\n", ""),
    _em("dumpfail: request length not recorded", "		io.reqn = wlen;\n", "		io.reqn = 6u;\n"),
    _em("dumpfail: response read from slot 0", "swg_hrd(x, x->in.respbuf + (uint64_t)sl * SWD_RESPBUF_LEN + i, &v)", "swg_hrd(x, x->in.respbuf + i, &v)"),
    _em("dumpfail: response read from the next slot", "swg_hrd(x, x->in.respbuf + (uint64_t)sl * SWD_RESPBUF_LEN + i, &v)", "swg_hrd(x, x->in.respbuf + (uint64_t)(sl + 1u) * SWD_RESPBUF_LEN + i, &v)"),
    _em("dumpfail: response cut at 64 B", "#define SWG_E_DUMP_MAX  256u", "#define SWG_E_DUMP_MAX  64u"),
    _em("dumpfail: response shorter than resp_len", "	uint32_t n = f->resp_len < SWG_E_DUMP_MAX ? f->resp_len : SWG_E_DUMP_MAX, sl, i, v;", "	uint32_t n = (f->resp_len < SWG_E_DUMP_MAX ? f->resp_len : SWG_E_DUMP_MAX) & ~3u, sl, i, v;"),
    _em("dumpfail: unposted request_id not a gap", "		swl_gap(\"scan\", \"failed ioctl: request_id not posted, no response dump\", \"high\");\n		return;", "		return;"),
    _em("dumpfail: unposted request_id read anyway", "	if (sl >= SWG_E_RID_BUFS) {\n		swl_gap(\"scan\", \"failed ioctl: request_id not posted", "	if (0) {\n		swl_gap(\"scan\", \"failed ioctl: request_id not posted"),
    _em("dumpfail: response rows 16 B wide", "	for (i = 0; i < n; i += 32u) {\n		char line[80];\n		uint32_t p = 0;\n\n		for (j = i; j < n && j < i + 32u; j++) {\n			p += (uint32_t)snprintf(line + p, sizeof line - p, \"%02x\", b[j]);\n		}\n		printf(\"SWEET WLAN: D4e failed", "	for (i = 0; i < n; i += 16u) {\n		char line[80];\n		uint32_t p = 0;\n\n		for (j = i; j < n && j < i + 16u; j++) {\n			p += (uint32_t)snprintf(line + p, sizeof line - p, \"%02x\", b[j]);\n		}\n		printf(\"SWEET WLAN: D4e failed"),
    _em("dumpfail: STOP lost after the dump", "				SWG_E_STOP(x, \"ring-2 item %u type 0x%x status %d != 0\", k, type, f.status);", "				(void)0;"),
    # L22c (T-4adb, sweetwlan=24): swg_e_l22c, one mutant per field of the clmload chunk, the order and the refusals; the host test (clm, clmref, clmst, clmstshort, ctry cases) must FAIL on each
    _em("L22c: chunk size 1399", "#define SWG_E_CLMCHUNK   1400u", "#define SWG_E_CLMCHUNK   1399u"),
    _em("L22c: chunk size 1401", "#define SWG_E_CLMCHUNK   1400u", "#define SWG_E_CLMCHUNK   1401u"),
    _em("L22c: 11 chunks at most", "#define SWG_E_CLMMAX     12u ", "#define SWG_E_CLMMAX     11u "),
    _em("L22c: 13 chunks at most", "#define SWG_E_CLMMAX     12u ", "#define SWG_E_CLMMAX     13u "),
    _em("L22c: more than the chunk limit not refused", "	if (x->in.clmlen > SWG_E_CLMMAX * SWG_E_CLMCHUNK) {", "	if (0) {"),
    _em("L22c: exactly the chunk limit refused", "	if (x->in.clmlen > SWG_E_CLMMAX * SWG_E_CLMCHUNK) {", "	if (x->in.clmlen >= SWG_E_CLMMAX * SWG_E_CLMCHUNK) {"),
    _em("L22c: no blob not refused", "	if (x->in.clm == NULL || x->in.clmlen == 0u) {", "	if (0) {"),
    _em("L22c: empty blob not refused", "	if (x->in.clm == NULL || x->in.clmlen == 0u) {", "	if (x->in.clm == NULL) {"),
    _em("L22c: chunk count rounds down", "	nch = (x->in.clmlen + SWG_E_CLMCHUNK - 1u) / SWG_E_CLMCHUNK;", "	nch = x->in.clmlen / SWG_E_CLMCHUNK;"),
    _em("L22c: last chunk as long as the others", "		len = x->in.clmlen - n * SWG_E_CLMCHUNK < SWG_E_CLMCHUNK ? x->in.clmlen - n * SWG_E_CLMCHUNK : SWG_E_CLMCHUNK;", "		len = SWG_E_CLMCHUNK;"),
    _em("L22c: flag base 0x2000", "		flag = 0x1000u | (n == 0u ?", "		flag = 0x2000u | (n == 0u ?"),
    _em("L22c: DL_BEGIN missing", "(n == 0u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);", "(n == 0u ? 0u : 0u) | (n == nch - 1u ? 0x4u : 0u);"),
    _em("L22c: DL_BEGIN on the second chunk", "(n == 0u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);", "(n == 1u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);"),
    _em("L22c: DL_END missing", "(n == 0u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);", "(n == 0u ? 0x2u : 0u) | (0 ? 0x4u : 0u);"),
    _em("L22c: DL_END on the first chunk", "(n == 0u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);", "(n == 0u ? 0x2u : 0u) | (n == 0u ? 0x4u : 0u);"),
    _em("L22c: DL_END on the next to last chunk", "(n == 0u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);", "(n == 0u ? 0x2u : 0u) | (n + 2u == nch ? 0x4u : 0u);"),
    _em("L22c: flag low byte", "		ch[0] = (uint8_t)flag;", "		ch[0] = (uint8_t)flag ^ 1u;"),
    _em("L22c: flag high byte", "		ch[1] = (uint8_t)(flag >> 8);", "		ch[1] = (uint8_t)(flag >> 8) ^ 1u;"),
    _em("L22c: dload_type 1", "		ch[2] = 2u;                             /* DL_TYPE_CLM */", "		ch[2] = 1u;                             /* DL_TYPE_CLM */"),
    _em("L22c: length field not stored", "		swd4b_put32(ch + 4, len);", ""),
    _em("L22c: length field one more", "		swd4b_put32(ch + 4, len);", "		swd4b_put32(ch + 4, len + 1u);"),
    _em("L22c: crc not zero", "		memcpy(ch + 12, x->in.clm + n * SWG_E_CLMCHUNK, len);", "		swd4b_put32(ch + 8, 1u);\n		memcpy(ch + 12, x->in.clm + n * SWG_E_CLMCHUNK, len);"),
    _em("L22c: data from the first chunk always", "memcpy(ch + 12, x->in.clm + n * SWG_E_CLMCHUNK, len);", "memcpy(ch + 12, x->in.clm, len);"),
    _em("L22c: data one byte early", "memcpy(ch + 12, x->in.clm + n * SWG_E_CLMCHUNK, len);", "memcpy(ch + 12, x->in.clm + n * SWG_E_CLMCHUNK + 1u, len);"),
    _em("L22c: chunk payload length", '"clmload", ch, 12u + len, 8u + 12u + len, 1, 0u, NULL, 1, &cm, NULL);', '"clmload", ch, 12u + len, 8u + 12u + len + 1u, 1, 0u, NULL, 1, &cm, NULL);'),
    _em("L22c: chunk argument length", '"clmload", ch, 12u + len, 8u + 12u + len, 1, 0u, NULL, 1, &cm, NULL);', '"clmload", ch, 11u + len, 8u + 12u + len, 1, 0u, NULL, 1, &cm, NULL);'),
    _em("L22c: chunk is a GET", 'ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR clmload",', 'ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_SET_VAR clmload",'),
    _em("L22c: refused chunk goes on", "		if (cm.status != 0) {\n			SWG_E_STOP(x, \"clmload chunk", "		if (0) {\n			SWG_E_STOP(x, \"clmload chunk"),
    # "L22c: refused chunk reads no clmload_status" dropped: review T-4adb-hwwrite-r1 C1 removed that GET (the glue list has no request after a refused chunk)
    _em("L22c: refused chunk is no stop", '			SWG_E_STOP(x, "clmload chunk %u refused, status %d", n + 1u, (int)cm.status);', "			(void)0;"),
    _em("L22c: clmload_status non-zero accepted", "	if (cm.status != 0 || cm.resp_len < 4u || st != 0u) {", "	if (cm.status != 0 || cm.resp_len < 4u) {"),
    _em("L22c: clmload_status completion status ignored", "	if (cm.status != 0 || cm.resp_len < 4u || st != 0u) {", "	if (cm.resp_len < 4u || st != 0u) {"),
    _em("L22c: clmload_status answer of 3 B read", "	st = cm.resp_len >= 4u ? swd4b_get32(rsp) : 0xFFFFFFFFu;\n	printf(\"SWEET WLAN: L22c clmload_status %u", "	st = swd4b_get32(rsp);\n	printf(\"SWEET WLAN: L22c clmload_status %u"),
    _em("L22c: clmload_status request length", '"clmload_status", NULL, 4u, 15u + 4u, 1, 4u, rsp, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	st = cm.resp_len', '"clmload_status", NULL, 4u, 15u + 3u, 1, 4u, rsp, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	st = cm.resp_len'),
    _em("L22c: country is a GET", 'ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR country",', 'ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_SET_VAR country",'),
    _em("L22c: country abbrev", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }", "{ 'X', 'Y', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }"),
    _em("L22c: country rev", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }", "{ 'X', 'Z', 0, 0, 0, 0, 1, 0, 'X', 'Z', 0, 0 }"),
    _em("L22c: country ccode", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 }", "{ 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Y', 0, 0 }"),
    _em("L22c: country length 8", '"country", xz, 12u, 8u + 12u, 1,', '"country", xz, 8u, 8u + 12u, 1,'),
    _em("L22c: country request length", '"country", xz, 12u, 8u + 12u, 1,', '"country", xz, 12u, 8u + 11u, 1,'),
    _em("L22c: country refused goes on", "	if (cm.status != 0) {\n		SWG_E_STOP(x, \"country XZ refused", "	if (0) {\n		SWG_E_STOP(x, \"country XZ refused"),
    _em("L22c: not run at all", "	if (clm) {      /* L22c (sweetwlan=24):", "	if (0) {      /* L22c (sweetwlan=24):"),
    _em("L22c: run at every level", "	if (clm) {      /* L22c (sweetwlan=24):", "	if (1) {      /* L22c (sweetwlan=24):"),
    _em("L22c: level 23 runs it", "ex = swg_e_l22(x, lv >= 24u);", "ex = swg_e_l22(x, lv >= 23u);"),
    _em("L22c: level 25 only runs it", "ex = swg_e_l22(x, lv >= 24u);", "ex = swg_e_l22(x, lv >= 25u);"),
    # EQUIVALENT L24 mutants (T-4e39; run t8bee-g2 listed them as survivors; each was shown by reading the engine, none is a gap in join_tests()):
    #  - "name scan runs past n" (swg_j_nlen without `i < n`): a name with no NUL in the buffer gives nl >= sizeof buf; then `nl + 1 > len` (len <= sizeof buf) or `len > sizeof buf` already refuses the command, so the result is the same "malformed, nothing sent"; only an out-of-bounds READ differs (join_tests j24nonul covers the refusal, an ASan run would flag the read)
    #  - "mask bits replace each other" (`|=` -> `=` in swg_j_mask): the three bits 69, 0, 16 live in the three different bytes 8, 0, 2 of an arg that was zeroed, so `=` and `|=` store the same bytes
    #  - "mask line without the status": the mask call is soft = 0, so a non-zero completion status is a STOP inside swg_e_call (swg_e_items) before the line is printed; the printed status is always 0
    #  - "LINK not marked seen" / "link flags not kept" (swg_j_ev): link_seen and link_flags are written and never read (the judgement uses link_up and ssid_*; the "link not up" line prints ev->flags)
    #  - "wait without the budget" (`spent < SWG_J_BUDGET_MS` dropped): at spent == budget polls = 0, swg_d4d_waitn runs no poll and returns w == d4rd, so the `break` after it ends the loop at the same point; spent never exceeds the budget (waited + POLL = polls * POLL, POLL 100 divides 10000)
    #  - "no new item ends the wait loop only by the budget" (`break` -> `continue`): swg_d4d_waitn returns w == d4rd only after all `polls` polls ran (it leaves early only with a pending item), so spent == budget then and the loop condition ends the loop with the same spent
    #  - "done line without the SET_SSID status": the line is reached only with ssid_seen and ssid_status == 0 (a non-zero status or a missing event stops before it), so `ej.ssid_status` is always 0 there
    #  - "runs after a stop" (`x->d4dstop` dropped from the L24 gate in swg_e): swg_e_l23 returns at once on d4dstop and sets d4dlv = 23 only in its final SUCCESS branch (after the loop), so d4dstop != 0 with d4dlv == 23 and ex == 0 cannot happen; `x->d4dlv != 23u` already covers every stop
    # ---- L24 (sweetwlan=24, T-6bef / T-8bee): the join in the msgbuf engine; join_tests() of wlan_g2_host_test.c must FAIL on each ----
    _em("L24: payload shadow one word short of the longest payload (clmload chunk, 355 words)", "#define SWG_E_PAYW       355u ", "#define SWG_E_PAYW       354u "),
    _em("L24: join budget 9000 ms", "#define SWG_J_BUDGET_MS  10000u ", "#define SWG_J_BUDGET_MS  9000u "),
    _em("L24: join budget 11000 ms", "#define SWG_J_BUDGET_MS  10000u ", "#define SWG_J_BUDGET_MS  11000u "),
    _em("L24: 5 join commands are enough", "#define SWG_J_NCMD       6u ", "#define SWG_J_NCMD       5u "),
    _em("L24: 7 join commands at least", "#define SWG_J_NCMD       6u ", "#define SWG_J_NCMD       7u "),
    _em("L24: at most 7 commands", "#define SWG_J_NCMAX      8u ", "#define SWG_J_NCMAX      7u "),
    _em("L24: at most 9 commands", "#define SWG_J_NCMAX      8u ", "#define SWG_J_NCMAX      9u "),
    _em("L24: SET_SSID is event 1", "#define SWG_E_EV_SET_SSID 0u ", "#define SWG_E_EV_SET_SSID 1u "),
    _em("L24: LINK is event 17", "#define SWG_E_EV_LINK    16u ", "#define SWG_E_EV_LINK    17u "),
    _em("L24: link up is flag bit 1", "#define SWG_E_LINK_UP    0x01u ", "#define SWG_E_LINK_UP    0x02u "),
    _em("L24: event flags little endian", "ev->flags = (uint32_t)fr[26] << 8 | fr[27];", "ev->flags = (uint32_t)fr[27] << 8 | fr[26];"),
    _em("L24: event flags read at +24", "ev->flags = (uint32_t)fr[26] << 8 | fr[27];", "ev->flags = (uint32_t)fr[24] << 8 | fr[25];"),
    _em("L24: all-ones payload old value refused", "if (old == 0xFFFFFFFFu && (kind != SWG_K_PAY || x->dpx != 0u) && !(x->dpx != 0u && oldv == 0xFFFFFFFFu)) {", "if (old == 0xFFFFFFFFu && !(x->dpx != 0u && oldv == 0xFFFFFFFFu)) {"),
    _em("L24: all-ones old value of every kind accepted", "if (old == 0xFFFFFFFFu && (kind != SWG_K_PAY || x->dpx != 0u) && !(x->dpx != 0u && oldv == 0xFFFFFFFFu)) {", "if (old == 0xFFFFFFFFu && kind == SWG_K_PAY + 99) {"),
    _em("L24: a secret store line prints the value", "	if (x->esec) {\n		printf(\"SWEET WLAN: G2 store %s host %#x (value withheld)\\n\", nm, bp);", "	if (0) {\n		printf(\"SWEET WLAN: G2 store %s host %#x (value withheld)\\n\", nm, bp);"),
    _em("L24: secret mode never entered", "	x->esec = 1;\n	ex = swg_e_call(x, cmd, what, name, arg, argn, wlen, soft, nrsp, rsp, repost, cmo, NULL);", "	ex = swg_e_call(x, cmd, what, name, arg, argn, wlen, soft, nrsp, rsp, repost, cmo, NULL);"),
    _em("L24: secret mode never left", "	x->esec = 0;\n	return ex != 0 ? ex : ex2;", "	return ex != 0 ? ex : ex2;"),
    _em("L24: secret payload not scrubbed", "		ex2 = swg_e_pay(x, z, sizeof z);", "		ex2 = 0;"),
    _em("L24: secret payload scrubbed with ones", "		memset(z, 0, sizeof z);", "		memset(z, 1, sizeof z);"),
    _em("L24: secret payload scrub skipped after the call", "	if (ex == 0 && !x->d4dstop) {\n		memset(z, 0, sizeof z);", "	if (0) {\n		memset(z, 0, sizeof z);"),
    _em("L24: scrub result dropped", "	return ex != 0 ? ex : ex2;", "	return ex;"),
    _em("L24: wsec_key not recognized", "if (swg_j_isname(x->jc[i].buf, \"wsec_key\") ||", "if (swg_j_isname(x->jc[i].buf, \"wsec_kez\") ||"), _em("L24: cur_etheraddr not secret (T-857c)", "swg_j_isname(x->jc[i].buf, \"cur_etheraddr\")) {", "swg_j_isname(x->jc[i].buf, \"cur_etheradd0\")) {"),
    _em("L24: every command is secret", "if (swg_j_isname(x->jc[i].buf, \"wsec_key\") ||", "if (1 ||"),
    _em("L24: name prefix matches", "	return s[i] == '\\0' && b[i] == 0u;", "	return s[i] == '\\0';"),
    _em("L24: mask version 0", "	arg[0] = 1u;\n	arg[1] = 3u;\n	arg[2] = 26u;\n	for (i = 0; i < nb; i++) {", "	arg[0] = 0u;\n	arg[1] = 3u;\n	arg[2] = 26u;\n	for (i = 0; i < nb; i++) {"),
    _em("L24: mask command 2", "	arg[1] = 3u;\n	arg[2] = 26u;\n	for (i = 0; i < nb; i++) {", "	arg[1] = 2u;\n	arg[2] = 26u;\n	for (i = 0; i < nb; i++) {"),
    _em("L24: mask length 25", "	arg[2] = 26u;\n	for (i = 0; i < nb; i++) {", "	arg[2] = 25u;\n	for (i = 0; i < nb; i++) {"),
    _em("L24: mask without ESCAN", "{ SWG_E_EV_ESCAN, SWG_E_EV_SET_SSID, SWG_E_EV_LINK,", "{ SWG_E_EV_SET_SSID, SWG_E_EV_SET_SSID, SWG_E_EV_LINK,"),
    _em("L24: mask without SET_SSID", "{ SWG_E_EV_ESCAN, SWG_E_EV_SET_SSID, SWG_E_EV_LINK,", "{ SWG_E_EV_ESCAN, SWG_E_EV_ESCAN, SWG_E_EV_LINK,"),
    _em("L24: mask without LINK", "{ SWG_E_EV_ESCAN, SWG_E_EV_SET_SSID, SWG_E_EV_LINK,", "{ SWG_E_EV_ESCAN, SWG_E_EV_SET_SSID, SWG_E_EV_ESCAN,"),
    _em("L24: mask byte offset 3", "arg[4u + bits[i] / 8u] |=", "arg[3u + bits[i] / 8u] |="),
    _em("L24: mask bit position mod 7", "(uint8_t)(1u << (bits[i] % 8u))", "(uint8_t)(1u << (bits[i] % 7u))"),
    _em("L24: mask payload 29 B", "\"event_msgs_ext\", arg, 30u, 15u + 30u, 0, 0u, NULL, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	printf(\"SWEET WLAN: L24", "\"event_msgs_ext\", arg, 29u, 15u + 29u, 0, 0u, NULL, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	printf(\"SWEET WLAN: L24"),
    _em("L24: mask not re-posted", "\"event_msgs_ext\", arg, 30u, 15u + 30u, 0, 0u, NULL, 1, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	printf(\"SWEET WLAN: L24", "\"event_msgs_ext\", arg, 30u, 15u + 30u, 0, 0u, NULL, 0, &cm, NULL);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	printf(\"SWEET WLAN: L24"),
    _em("L24: events not judged inside the ioctl", "			if (x->ej != NULL) {\n				swg_j_ev(x, &ev);\n			}", "			if (0) {\n				swg_j_ev(x, &ev);\n			}"),
    _em("L24: SET_SSID status not kept", "		j->ssid_status = ev->status;", "		j->ssid_status = 0u;"),
    _em("L24: SET_SSID not marked seen", "		j->ssid_seen = 1;\n		j->ssid_status", "		j->ssid_status"),
    _em("L24: link always up", "		j->link_up = (ev->flags & SWG_E_LINK_UP) != 0u;", "		j->link_up = 1;"),
    _em("L24: link up on any flag", "		j->link_up = (ev->flags & SWG_E_LINK_UP) != 0u;", "		j->link_up = ev->flags != 0u;"),
    _em("L24: link down said as up", "		if (j->link_up) {\n			printf(\"SWEET WLAN: join link up\\n\");", "		if (1) {\n			printf(\"SWEET WLAN: join link up\\n\");"),
    _em("L24: join set_ssid line without the status", "printf(\"SWEET WLAN: join set_ssid status=%u\\n\", ev->status);", "printf(\"SWEET WLAN: join set_ssid status=%u\\n\", 0u);"),
    _em("L24: join command sent with the name only", "x->jc[i].buf + nl + 1u, x->jc[i].len - nl - 1u, x->jc[i].len, 1, 0u, NULL, 1, &cm, NULL);\n		}", "x->jc[i].buf + nl + 1u, x->jc[i].len - nl - 1u, x->jc[i].len - 1u, 1, 0u, NULL, 1, &cm, NULL);\n		}"),
    _em("L24: join payload one byte short", "c->buf + nl + 1u, c->len - nl - 1u, c->len, 1, 0u, NULL, 1, &cm, NULL);", "c->buf + nl + 1u, c->len - nl - 1u, c->len - 1u, 1, 0u, NULL, 1, &cm, NULL);"),
    _em("L24: join ioctl status ignored", "	if (cm.status != 0) {\n		SWG_J_STOP(x, \"H5\", \"join ioctl refused status=%d\"", "	if (0) {\n		SWG_J_STOP(x, \"H5\", \"join ioctl refused status=%d\""),
    _em("L24: join ioctl refusal is not a stop", "SWG_J_STOP(x, \"H5\", \"join ioctl refused status=%d\", (int)cm.status);", "swl_gap(\"H5\", \"join ioctl refused\", \"high\");"),
    _em("L24: no event flush after the join", "	ex = swg_e_evflush(x);\n	if (ex != 0) {\n		return ex;\n	}\n	while (!(j->ssid_seen", "	while (!(j->ssid_seen"),
    _em("L24: wait ends at SET_SSID alone", "while (!(j->ssid_seen && (j->ssid_status != 0u || j->link_up)) &&", "while (!j->ssid_seen &&"),
    _em("L24: wait ends at LINK alone", "while (!(j->ssid_seen && (j->ssid_status != 0u || j->link_up)) &&", "while (!(j->link_up || j->ssid_seen && j->ssid_status != 0u) &&"),
    _em("L24: wait never ends on a bad status", "while (!(j->ssid_seen && (j->ssid_status != 0u || j->link_up)) &&", "while (!(j->ssid_seen && j->link_up) &&"),
    _em("L24: waited time not counted", "polls = (SWG_J_BUDGET_MS - spent) / SWG_POLL_MS;\n		ex = swg_d4d_waitn(x, 1u, polls, &w, &waited);\n		if (ex != 0) {\n			return ex;\n		}\n		spent += waited + SWG_POLL_MS;", "polls = (SWG_J_BUDGET_MS - spent) / SWG_POLL_MS;\n		ex = swg_d4d_waitn(x, 1u, polls, &w, &waited);\n		if (ex != 0) {\n			return ex;\n		}\n		spent += SWG_POLL_MS;"),
    _em("L24: poll time not counted", "polls = (SWG_J_BUDGET_MS - spent) / SWG_POLL_MS;\n		ex = swg_d4d_waitn(x, 1u, polls, &w, &waited);\n		if (ex != 0) {\n			return ex;\n		}\n		spent += waited + SWG_POLL_MS;", "polls = (SWG_J_BUDGET_MS - spent) / SWG_POLL_MS;\n		ex = swg_d4d_waitn(x, 1u, polls, &w, &waited);\n		if (ex != 0) {\n			return ex;\n		}\n		spent += waited;"),
    _em("L24: poll count from the whole budget", "		polls = (SWG_J_BUDGET_MS - spent) / SWG_POLL_MS;", "		polls = SWG_J_BUDGET_MS / SWG_POLL_MS;"),
    _em("L24: index below the read count ignored", "		if (w < x->d4rd) {\n			SWG_E_STOP(x, \"d2h write index %u went below the read count %u\", w, x->d4rd);\n			return 0;\n		}\n		if (w == x->d4rd) {\n			break;\n		}\n		ex = swg_e_items(x, w, &io, NULL, &e);", "		if (w < x->d4rd) {\n			(void)w;\n		}\n		if (w == x->d4rd) {\n			break;\n		}\n		ex = swg_e_items(x, w, &io, NULL, &e);"),
    _em("L24: wait loop does not consume", "		if (e > x->d4rd) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			ex = swg_e_evflush(x);\n			if (ex != 0) {\n				return ex;\n			}\n		}\n	}\n	if (j->ssid_seen", "		if (0) {\n			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			ex = swg_e_evflush(x);\n			if (ex != 0) {\n				return ex;\n			}\n		}\n	}\n	if (j->ssid_seen"),
    _em("L24: wait loop does not re-post events", "			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n			ex = swg_e_evflush(x);\n			if (ex != 0) {\n				return ex;\n			}\n		}\n	}\n	if (j->ssid_seen", "			ex = swg_d4d_consume(x, x->d4rd, e);\n			if (ex != 0) {\n				return ex;\n			}\n		}\n	}\n	if (j->ssid_seen"),
    _em("L24: SET_SSID failure not a stop", "	if (j->ssid_seen && j->ssid_status != 0u) {\n		SWG_J_STOP", "	if (0) {\n		SWG_J_STOP"),
    _em("L24: missing event not a stop", "	} else if (!j->ssid_seen || !j->link_up) {", "	} else if (0) {"),
    _em("L24: SET_SSID alone is enough", "	} else if (!j->ssid_seen || !j->link_up) {", "	} else if (!j->ssid_seen) {"),
    _em("L24: LINK alone is enough", "	} else if (!j->ssid_seen || !j->link_up) {", "	} else if (!j->link_up) {"),
    _em("L24: no-event gap without the time", "\"join no SET_SSID/LINK event in %u ms\", spent);", "\"join no SET_SSID/LINK event in %u ms\", 0u);"),
    _em("L24: no join commands is silent", "		swl_gap(\"H5\", \"join not run: no join commands (SSID/credentials not delivered)\", \"high\");\n", ""),
    _em("L24: NULL commands not refused", "	if (x->jc == NULL || x->njc < SWG_J_NCMD || x->njc > SWG_J_NCMAX) {", "	if (x->njc < SWG_J_NCMD || x->njc > SWG_J_NCMAX) {"),
    _em("L24: too few commands accepted", "	if (x->jc == NULL || x->njc < SWG_J_NCMD || x->njc > SWG_J_NCMAX) {", "	if (x->jc == NULL || x->njc < 1u || x->njc > SWG_J_NCMAX) {"),
    _em("L24: too many commands accepted", "	if (x->jc == NULL || x->njc < SWG_J_NCMD || x->njc > SWG_J_NCMAX) {", "	if (x->jc == NULL || x->njc < SWG_J_NCMD) {"),
    _em("L24: not-run line without the count", "x->jc == NULL ? 0u : x->njc, SWG_J_NCMD);", "0u, SWG_J_NCMD);"),
    _em("L24: command class not checked", "if (x->jc[i].cmd != SWD4B_WLC_SET_VAR || nl == 0u", "if (nl == 0u"),
    _em("L24: empty name accepted", "x->jc[i].cmd != SWD4B_WLC_SET_VAR || nl == 0u || nl + 1u", "x->jc[i].cmd != SWD4B_WLC_SET_VAR || nl + 1u"),
    _em("L24: unterminated name accepted", "nl == 0u || nl + 1u > x->jc[i].len ||", "nl == 0u ||"),
    _em("L24: command longer than its buffer accepted", " || x->jc[i].len > sizeof x->jc[i].buf || ", " || "),
    _em("L24: last command need not be the join", "(i + 1u == x->njc && !swg_j_isname(x->jc[i].buf, \"join\"))", "(0)"),
    _em("L24: any last command is a join", "(i + 1u == x->njc && !swg_j_isname(x->jc[i].buf, \"join\"))", "(i + 1u == x->njc && 0)"),
    _em("L24: malformed command not a stop", "SWG_J_STOP(x, \"H5\", \"join command %u malformed, nothing sent\", i);\n			return 0;", "swl_gap(\"H5\", \"join command malformed\", \"high\");\n			return 0;"),
    _em("L24: malformed command sent anyway", "SWG_J_STOP(x, \"H5\", \"join command %u malformed, nothing sent\", i);\n			return 0;", "SWG_J_STOP(x, \"H5\", \"join command %u malformed, nothing sent\", i);"),
    _em("L24: join sent before the mask", "	ex = swg_j_mask(x, lv);\n	if (ex != 0 || x->d4dstop) {\n		return ex;\n	}\n	for (i = 0; i + 1u < x->njc; i++) {", "	for (i = 0; i + 1u < x->njc; i++) {"),
    _em("L24: the join is a plain command too", "	for (i = 0; i + 1u < x->njc; i++) {\n		const char *name", "	for (i = 0; i < x->njc; i++) {\n		const char *name"),
    _em("L24: last pre-join command skipped", "	for (i = 0; i + 1u < x->njc; i++) {\n		const char *name", "	for (i = 0; i + 2u < x->njc; i++) {\n		const char *name"),
    _em("L24: command status ignored", "		if (cm.status != 0) {\n			if (i == 0u) {", "		if (0) {\n			if (i == 0u) {"),
    _em("L24: H1 text for every command", "			if (i == 0u) {\n				SWG_J_STOP(x, \"H1\"", "			if (1) {\n				SWG_J_STOP(x, \"H1\""),
    _em("L24: H1 text for none", "			if (i == 0u) {\n				SWG_J_STOP(x, \"H1\"", "			if (0) {\n				SWG_J_STOP(x, \"H1\""),
    _em("L24: command refusal names the wrong row", "SWG_J_STOP(x, x->jc[i].row, \"%.24s refused status=%d\", name, (int)cm.status);", "SWG_J_STOP(x, \"H4\", \"%.24s refused status=%d\", name, (int)cm.status);"),
    _em("L24: command refusal without the status", "\"%.24s refused status=%d\", name, (int)cm.status);", "\"%.24s refused status=%d\", name, 0);"),
    _em("L24: refused command not a stop", "				SWG_J_STOP(x, x->jc[i].row, \"%.24s refused status=%d\", name, (int)cm.status);\n			}\n			return 0;", "				swl_gap(x->jc[i].row, \"refused\", \"high\");\n			}\n			return 0;"),
    _em("L24: sup_wpa ok said for every command", "		if (i == 0u) {\n			printf(\"SWEET WLAN: sup_wpa set 0 ok\\n\");", "		if (1) {\n			printf(\"SWEET WLAN: sup_wpa set 0 ok\\n\");"),
    _em("L24: sup_wpa ok never said", "		if (i == 0u) {\n			printf(\"SWEET WLAN: sup_wpa set 0 ok\\n\");", "		if (0) {\n			printf(\"SWEET WLAN: sup_wpa set 0 ok\\n\");"),
    _em("L24: command line without the row", "printf(\"SWEET WLAN: L24 %s %s status %d\\n\", x->jc[i].row, name, (int)cm.status);", "printf(\"SWEET WLAN: L24 %s %s status %d\\n\", \"H1\", name, (int)cm.status);"),
    _em("L24: events stay judged after the join", "	ex = swg_j_join(x, &x->jc[x->njc - 1u], &ej);\n	x->ej = NULL;", "	ex = swg_j_join(x, &x->jc[x->njc - 1u], &ej);"),
    _em("L24: join judged only on the first of the commands", "	ex = swg_j_join(x, &x->jc[x->njc - 1u], &ej);", "	ex = swg_j_join(x, &x->jc[0], &ej);"),
    _em("L24: level 24 not recorded", "	swg_d4d_done(x, 24u, lv, x->d4wr, x->d4rd);", "	swg_d4d_done(x, 23u, lv, x->d4wr, x->d4rd);"),
    _em("L24: runs at level 23", "if (ex != 0 || x->d4dstop || lv <= 23u || x->d4dlv != 23u) {", "if (ex != 0 || x->d4dstop || lv <= 22u || x->d4dlv != 23u) {"),
    _em("L24: never runs", "if (ex != 0 || x->d4dstop || lv <= 23u || x->d4dlv != 23u) {", "if (ex != 0 || x->d4dstop || lv <= 24u || x->d4dlv != 23u) {"),
    _em("L24: runs after a scan that did not finish", "if (ex != 0 || x->d4dstop || lv <= 23u || x->d4dlv != 23u) {", "if (ex != 0 || x->d4dstop || lv <= 23u) {"),
    _em("L24: level not recorded in the engine", "	x->elv = 24u;\n#if defined(SWEET_WLAN_DATA_H)", "#if defined(SWEET_WLAN_DATA_H)"),
    _em("L24: call bound off by a word", "if (name != NULL && wlen > 4u * SWG_E_PAYW) {", "if (name != NULL && wlen > 4u * SWG_E_PAYW + 4u) {"),
    _em("L24: call bound lost", "if (name != NULL && wlen > 4u * SWG_E_PAYW) {", "if (0) {"),
    _em("L24: call bound refuses the full shadow", "if (name != NULL && wlen > 4u * SWG_E_PAYW) {", "if (name != NULL && wlen >= 4u * SWG_E_PAYW) {"),
] + wlan_keep_mutants.ROWS      # patch 0281: tools/wlan_keep_mutants.py


# mutant-block: wlan-g2
G2_MUTANTS = [
    # -- ONCE, row order, entered, stopped (swg_st, swg_stop, swg_rd) --
    ("ONCE: a done row may be stored again", [("(x->mask & (1u << st)) != 0u || ", "")]),
    ("row order: row n without row n-1 read back", [(" || (st != 0 && (x->mask & (1u << (st - 1))) == 0u)", "")]),
    ("entered: rows run before swg_entry passed", [("!x->entered || ", "")]),
    ("stopped: swg_st goes on after a stop", [("if (x->stopped) {\n\t\treturn -SWG_E_STOPPED;", "if (0) {\n\t\treturn -SWG_E_STOPPED;")]),
    ("stopped: swg_rd reads after a stop", [("if (x->stopped || !swg_ram(x, bp)", "if (!swg_ram(x, bp)")]),
    ("stopped: a second stop overwrites the first", [("if (!x->stopped) {\n\t\tx->stopped = 1;", "if (1) {\n\t\tx->stopped = 1;")]),
    ("stop -> undo: swg_stop does not undo", [("\t\tswg_undo(x);\n\t}\n\treturn -code;", "\t}\n\treturn -code;")]),
    ("stop: no WIFI GAP line", [('swl_gap("g2", gb, "high");', "")]),
    # -- the old-value rule (swg_old_ok, the dead refusal in swg_st) --
    ("old value: flags2 not compared", [("return old == SWG_FLAGS2_OLD;", "return 1;")]),
    ("old value: other words not required 0", [("\treturn old == 0u;\n}", "\treturn 1;\n}")]),
    ("old value: RC Command low half not required 0", [("return (old & SWG_CMD_MASK) == 0u;", "return 1;")]),
    ("old value: EP Command low half not required MEM only", [("return (old & SWG_CMD_MASK) == SWG_EP_CMD_MEM;", "return 1;")]),
    ("old value: index slot must be 0 (not any non-dead word)", [("if (swg_g2[st].grp == SWG_G_SLOT) {\n\t\treturn old != 0xFFFFFFFFu;", "if (0) {\n\t\treturn old != 0xFFFFFFFFu;")]),
    ("old value: hostready doorbell must read 0", [("case SWG_ST_READY:\n\t\treturn 1;", "case SWG_ST_READY:\n\t\treturn old == 0u;")]),
    ("old value: RC stop is not RCSTUCK", [("return swg_stop(x, st == SWG_ST_RC ? SWG_E_RCSTUCK : SWG_E_OLD);", "return swg_stop(x, SWG_E_OLD);")]),
    ("dead (all-ones) word not refused", [("if (old == 0xFFFFFFFFu && st != SWG_ST_READY) {", "if (0) {")]),
    ("dead (all-ones) refusal also on the doorbell", [("if (old == 0xFFFFFFFFu && st != SWG_ST_READY) {", "if (old == 0xFFFFFFFFu) {")]),
    # -- the store and the read-back rule (swg_st, swg_rb_ok) --
    ("store: a failing store call is ignored", [("if (w16 ? swg_put16(x, sp, bp, (uint16_t)w) : swg_put(x, sp, bp, w)) {\n\t\t\treturn swg_stop(x, SWG_E_WR);\n\t\t}", "if (w16) swg_put16(x, sp, bp, (uint16_t)w); else swg_put(x, sp, bp, w);")]),
    ("store: nstore not counted", [("x->nstore++;\n", "")]),
    ("read-back: whole word not compared", [("\treturn rb == w;\n}", "\treturn 1;\n}")]),
    ("read-back: a failing read-back read is ignored", [("if (sp == SWG_SP_TCM ? swg_rd(x, bp, &rb) : x->b->rd(x->b->ctx, sp, bp, &rb)) {\n\t\t\treturn swg_stop(x, SWG_E_READBACK);", "if (0) {\n\t\t\treturn swg_stop(x, SWG_E_READBACK);")]),
    ("read-back: doorbell compared", [("if (st == SWG_ST_READY) {\n\t\treturn 1;", "if (0) {\n\t\treturn 1;")]),
    ("read-back: Commands compared on the whole word", [("return (rb & SWG_CMD_MASK) == w;", "return rb == w;")]),
    ("read-back: EP Command not compared", [("return (rb & SWG_CMD_MASK) == w;", "return st == SWG_ST_EP || (rb & SWG_CMD_MASK) == w;")]),
    ("read-back: RC Command not compared", [("return (rb & SWG_CMD_MASK) == w;", "return st == SWG_ST_RC || (rb & SWG_CMD_MASK) == w;")]),
    ("read-back: RC stop is not RCSTUCK", [("return swg_stop(x, st == SWG_ST_RC ? SWG_E_RCSTUCK : SWG_E_READBACK);", "return swg_stop(x, SWG_E_READBACK);")]),
    # -- the Commands (values, entry checks) --
    ("EP Command new value 0x0006", [("#define SWG_EP_CMD_DMA   0x00000006u", "#define SWG_EP_CMD_DMA   0x00000007u")]),
    ("RC Command value 0x0507", [("#define SWG_RC_CMD       0x00000507u", "#define SWG_RC_CMD       0x00000517u")]),
    ("entry: EP Command old half not required MEM only", [("if ((x->rt.ep_old & SWG_CMD_MASK) != SWG_EP_CMD_MEM) {", "if (0) {")]),
    ("entry: RC Command old half not required 0", [("if ((x->rt.rc_old & SWG_CMD_MASK) != 0u) {", "if (0) {")]),
    # -- swg_ram (base, end, alignment) --
    ("swg_ram: base one word low", [("bp >= SWW_RAM_BASE && ", "bp >= SWW_RAM_BASE - 4u && ")]),
    ("swg_ram: base word itself refused", [("bp >= SWW_RAM_BASE && ", "bp > SWW_RAM_BASE && ")]),
    ("swg_ram: end one word high", [("bp <= x->in.end - 4u", "bp <= x->in.end")]),
    ("swg_ram: last RAM word refused", [("bp <= x->in.end - 4u", "bp < x->in.end - 4u")]),
    ("swg_ram: end at or below the base (end - 4 wraps)", [("x->in.end > SWW_RAM_BASE && ", "")]),
    ("swg_ram: alignment not required", [("(bp & 3u) == 0u && bp >= ", "bp >= ")]),
    # -- the BAR2 window (swg_win, swg_go) --
    ("window select: tail window from the last word below it", [("uint32_t w = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "uint32_t w = bp >= SWW_WIN_TAIL - 4u ? SWW_WIN_TAIL : 0u;")]),
    ("window select: first tail word still in window 0", [("uint32_t w = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "uint32_t w = bp > SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;")]),
    ("window select: tail window never left (bp < 0x400000)", [("uint32_t w = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "uint32_t w = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : x->rt.win;")]),
    ("window select: tail window never entered (bp >= 0x400000)", [("uint32_t w = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;", "uint32_t w = bp >= SWW_WIN_TAIL ? x->rt.win : 0u;")]),
    ("window select: a refused select is ignored", [("if (x->b->wr(x->b->ctx, SWG_SP_WIN2, SWW_CFG_WIN2, w)) {", "if (0) {")]),
    ("window cache: select on every access", [("if (w == x->rt.win) {\n\t\treturn 0;\n\t}", "if (0) {\n\t\treturn 0;\n\t}")]),
    ("window cache: selected window not remembered", [("x->rt.win = w;", "")]),
    ("window start: G2 assumes the tail window", [("x->rt.win = 0;          /*", "x->rt.win = SWW_WIN_TAIL;          /*")]),
    ("window restore: tail window left selected at the end", [("if (x->rt.win != 0u && swg_win(x, 0u) != 0) {", "if (0) {")]),
    ("window restore: a failed restore replaces the first error", [("rc = rc ? rc : -SWG_E_WR;", "rc = -SWG_E_WR;")]),
    # -- every check of swg_entry --
    ("entry: RC Command read failure not refused", [("x->b->rd(x->b->ctx, SWG_SP_RC, SWG_CMD_OFF, &x->rt.rc_old) || ", "")]),
    ("entry: EP Command read failure not refused", [("x->b->rd(x->b->ctx, SWG_SP_EP, SWG_CMD_OFF, &x->rt.ep_old) ||", "")]),
    ("entry: SRPWR read failure not refused", [("x->b->rd(x->b->ctx, SWG_SP_PCIE, SWG_SRPWR_OFF, &x->rt.srpwr)) {", "0) {")]),
    ("entry: SRPWR request bit not required", [("(x->rt.srpwr & (SWG_SRPWR_REQ1 | SWG_SRPWR_ST1)) != (SWG_SRPWR_REQ1 | SWG_SRPWR_ST1)", "(x->rt.srpwr & SWG_SRPWR_ST1) == 0u")]),
    ("entry: SRPWR status bit not required", [("(x->rt.srpwr & (SWG_SRPWR_REQ1 | SWG_SRPWR_ST1)) != (SWG_SRPWR_REQ1 | SWG_SRPWR_ST1)", "(x->rt.srpwr & SWG_SRPWR_REQ1) == 0u")]),
    ("entry: shared flags word read failure not refused", [("swg_rd(x, sh + SWG_SH_FLAGS, &fl) || ", "")]),
    ("entry: ring_info pointer read failure not refused", [(" || swg_rd(x, sh + SWG_SH_RINGINFO, &x->rt.ri)", "")]),
    ("entry: firmware version not 7 accepted", [("(fl & SWG_FLAGS_VER) != SWG_VER || ", "")]),
    ("entry: TRAP flag not refused", [("(fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {\n\t\treturn swg_stop(x, SWG_E_FLAGS);", "(fl & SWG_FLAGS_ASSERT) != 0u) {\n\t\treturn swg_stop(x, SWG_E_FLAGS);")]),
    ("entry: ASSERT flag not refused", [("(fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {\n\t\treturn swg_stop(x, SWG_E_FLAGS);", "(fl & SWG_FLAGS_TRAP) != 0u) {\n\t\treturn swg_stop(x, SWG_E_FLAGS);")]),
    ("entry: ring_info block overlapping the shared structure accepted", [("if (!swg_apart(x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE, sh, SWG_SH_TRAP + 8u)) {", "if (0) {")]),
    ("entry: ring_info/shared apart check, ring_info block 4 bytes short", [("x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE, sh, SWG_SH_TRAP + 8u)", "x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE - 4u, sh, SWG_SH_TRAP + 8u)")]),
    ("entry: ring_info/shared apart check, shared block 4 bytes short", [("sh, SWG_SH_TRAP + 8u)) {", "sh, SWG_SH_TRAP + 4u)) {")]),
    ("entry: ring_info refused above the shared structure (the old rule) in place of the apart check", [("if (!swg_apart(x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE, sh, SWG_SH_TRAP + 8u)) {", "if (x->rt.ri >= sh) {")]),
    ("entry: ring_info refused below the shared structure in place of the apart check", [("if (!swg_apart(x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE, sh, SWG_SH_TRAP + 8u)) {", "if (x->rt.ri < sh) {")]),
    ("entry: last ring_mem word not required in RAM", [("if (!swg_ram(x, x->rt.rm + SWG_RM_N * SWG_RM_STRIDE - 4u)) {", "if (0) {")]),
    ("entry: last ring_mem word checked one word late", [("x->rt.rm + SWG_RM_N * SWG_RM_STRIDE - 4u)) {", "x->rt.rm + SWG_RM_N * SWG_RM_STRIDE)) {")]),
    ("rd16: high half taken for the low member and the reverse", [("*v = (bp & 2u) != 0u ? w >> 16 : (w & 0xFFFFu);", "*v = (bp & 2u) == 0u ? w >> 16 : (w & 0xFFFFu);")]),
    ("rd16: the low half is not masked", [("*v = (bp & 2u) != 0u ? w >> 16 : (w & 0xFFFFu);", "*v = (bp & 2u) != 0u ? w >> 16 : w;")]),
    ("rd16: reads the member address, not its word", [("swg_rd(x, bp & ~3u, &w)", "swg_rd(x, bp, &w)")]),
    ("entry: max_submission_queues 43 not required", [(" || w != SWG_MAX_SUB", "")]),
    ("entry: max_completion_rings 4 not required", [(" || w != SWG_MAX_CPL", "")]),
    ("entry: ringmem_ptr == ring_info + 0x4C not required", [(" || x->rt.rm != x->rt.ri + SWG_RI_SIZE", "")]),
    ("entry: ring_mem words not required 0", [("if (w != 0u) {\n\t\t\treturn swg_stop(x, SWG_E_OLD);", "if (0) {\n\t\t\treturn swg_stop(x, SWG_E_OLD);")]),
    ("entry: ring_mem scan misses the last word", [("i < SWG_RM_N * SWG_RM_STRIDE / 4u; i++", "i < SWG_RM_N * SWG_RM_STRIDE / 4u - 1u; i++")]),
    ("entry: index pointer not required to be a RAM word", [("!swg_ram(x, x->rt.ra[i]) || ", "")]),
    ("entry: index array end not required in RAM", [(" || !swg_ram(x, x->rt.ra[i] + SWG_IDX_SZ * (SWG_IDX_SLOTS - 1u))", "")]),
    ("entry: index array end checked one slot short", [("(SWG_IDX_SLOTS - 1u)", "(SWG_IDX_SLOTS - 2u)")]),
    ("entry: ring_info host addresses not required 0", [("x->rt.ri + SWG_RI_HOSTADDR + 4u * i, &w) || w != 0u)", "x->rt.ri + SWG_RI_HOSTADDR + 4u * i, &w))")]),
    ("entry: ring_info host address scan misses the last word", [("i < 2u * SWG_IDX_N; i++", "i < 2u * SWG_IDX_N - 1u; i++")]),
    ("entry: shared flags2 not required 0x40943041", [('swg_sh_is(x, SWG_SH_FLAGS2, SWG_FLAGS2_OLD, "flags2 is not 0x40943041")', "0")]),
    ("entry: shared host_cap not required 0", [('swg_sh_is(x, SWG_SH_HOSTCAP, 0u, "host_cap is not 0")', "0")]),
    ("entry: shared scratch len not required 0", [('swg_sh_is(x, SWG_SH_SCR_LEN, 0u, "scratch len is not 0")', "0")]),
    ("entry: shared scratch addr lo not required 0", [('swg_sh_is(x, SWG_SH_SCR_LO, 0u, "scratch addr lo is not 0")', "0")]),
    ("entry: shared scratch addr hi not required 0", [('swg_sh_is(x, SWG_SH_SCR_LO + 4u, 0u, "scratch addr hi is not 0")', "0")]),
    ("entry: shared host_trap_addr lo not required 0", [('swg_sh_is(x, SWG_SH_TRAP, 0u, "host_trap_addr lo is not 0")', "0")]),
    ("entry: shared host_trap_addr hi not required 0", [('swg_sh_is(x, SWG_SH_TRAP + 4u, 0u, "host_trap_addr hi is not 0")', "0")]),
    ("entry: swg_sh_is does not compare", [(" || w != want", "")]),
    # -- the tried key and the tried set --
    ("tried: set only after the store call succeeded", [("x->tried |= 1u << st;\n\t\tif (w16 ? swg_put16(x, sp, bp, (uint16_t)w) : swg_put(x, sp, bp, w)) {\n\t\t\treturn swg_stop(x, SWG_E_WR);\n\t\t}", "if (w16 ? swg_put16(x, sp, bp, (uint16_t)w) : swg_put(x, sp, bp, w)) {\n\t\t\treturn swg_stop(x, SWG_E_WR);\n\t\t}\n\t\tx->tried |= 1u << st;")]),
    ("tried: set only once the row was read back", [("\t\tx->tried |= 1u << st;\n", ""), ("x->mask |= 1u << st;\n", "x->mask |= 1u << st;\n\tx->tried |= 1u << st;\n")]),
    ("tried: every row marks the Commands tried", [("x->tried |= 1u << st;", "x->tried |= 0xFFFFFFFFu;")]),
    ("tried key: EP undo keyed on the RC bit", [("if ((x->tried & (1u << SWG_ST_EP)) != 0u && (swg_put", "if ((x->tried & (1u << SWG_ST_RC)) != 0u && (swg_put")]),
    ("tried key: RC undo keyed on the EP bit", [("if ((x->tried & (1u << SWG_ST_RC)) != 0u && (swg_put", "if ((x->tried & (1u << SWG_ST_EP)) != 0u && (swg_put")]),
    # -- the undo --
    ("undo: EP Command not restored", [("if ((x->tried & (1u << SWG_ST_EP)) != 0u && (swg_put", "if (0 && (swg_put")]),
    ("undo: RC Command not restored", [("if ((x->tried & (1u << SWG_ST_RC)) != 0u && (swg_put", "if (0 && (swg_put")]),
    ("undo: EP Command restored to the DMA value", [("swg_put(x, SWG_SP_EP, SWG_CMD_OFF, SWG_EP_CMD_MEM)", "swg_put(x, SWG_SP_EP, SWG_CMD_OFF, SWG_EP_CMD_DMA)")]),
    ("undo: EP Command read-back not compared", [("|| (now & SWG_CMD_MASK) != SWG_EP_CMD_MEM)) {", ")) {")]),
    ("undo: EP Command failure not counted", [("SWG_EP_CMD_MEM, now);\n\t\tok = 0;", "SWG_EP_CMD_MEM, now);")]),
    ("undo: RC Command restored to the G2 value", [("want = x->rt.rc_old & SWG_CMD_MASK;", "want = SWG_RC_CMD;")]),
    ("undo: RC Command restored with the Status half", [("want = x->rt.rc_old & SWG_CMD_MASK;", "want = x->rt.rc_old;")]),
    ("undo: RC Command read-back not compared", [("|| (now & SWG_CMD_MASK) != want)) {", ")) {")]),
    ("undo: RC Command failure not counted", [("want, now);\n\t\tok = 0;", "want, now);")]),
    ("undo: RC Command skipped after the EP Command failed", [("if ((x->tried & (1u << SWG_ST_RC)) != 0u && (swg_put", "if (ok && (x->tried & (1u << SWG_ST_RC)) != 0u && (swg_put")]),
    ("undo: restore flag always ok", [("x->restore = ok;", "x->restore = 1;")]),
    ("undo: failed undo does not mark the run stopped", [("x->stopped = 1;\n\t\t\t\tx->code = SWG_E_STOPPED;", "")]),      # one level deeper since the G2 keep branch
    ("undo after the poll: not done on success", [("rc = swg_undo(x);\n\t\t\tif (rc) {", "rc = 0;\n\t\t\tif (rc) {")]),
    ("undo after the poll: done before the poll", [("rc = swg_poll(x);", "rc = swg_undo(x) ? -SWG_E_WR : swg_poll(x);")]),
    # -- the poll (W-D4a-1: pointer dereferenced, baseline before hostready, six words compared, FWHALT) --
    ("poll: TRAP/ASSERT not a stop", [("if ((cur.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {\n\t\t\tprintf", "if (0) {\n\t\t\tprintf")]),
    ("poll: ASSERT only", [("if ((cur.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {\n\t\t\tprintf", "if ((cur.fl & SWG_FLAGS_ASSERT) != 0u) {\n\t\t\tprintf")]),
    ("poll: TRAP only", [("if ((cur.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {\n\t\t\tprintf", "if ((cur.fl & SWG_FLAGS_TRAP) != 0u) {\n\t\t\tprintf")]),
    ("poll: all-ones check removed from the poll", [("if (swg_pw_ones(x, &cur)) {", "if (0) {")]),
    ("baseline: all-ones check removed from the baseline", [("if (swg_pw_ones(x, &x->base)) {", "if (0) {")]),
    ("ones: flags all ones not refused", [("if (w->fl == 0xFFFFFFFFu) {", "if (0) {")]),
    ("ones: mailbox data all ones not refused", [("if (w->mb == 0xFFFFFFFFu) {", "if (0) {")]),
    ("ones: d2h_w slot all ones not refused", [("if (w->dw[i] == 0xFFFFFFFFu) {", "if (0) {")]),
    ("ones: d2h_w slot 1 not checked (loop starts at 2)", [("for (i = 0; i < SWG_D2H_SLOTS; i++) {\n\t\tif (w->dw[i] == 0xFFFFFFFFu)", "for (i = 2; i < SWG_D2H_SLOTS; i++) {\n\t\tif (w->dw[i] == 0xFFFFFFFFu)")]),
    ("ones: 0xC30 all ones not refused", [("if (w->c30 == 0xFFFFFFFFu) {", "if (0) {")]),
    ("ones: all-ones stops with another name", [("if (swg_pw_ones(x, &cur)) {\n\t\t\treturn swg_stop(x, SWG_E_DEAD);", "if (swg_pw_ones(x, &cur)) {\n\t\t\treturn swg_stop(x, SWG_E_FATAL);")]),
    ("ones: baseline all-ones stops with another name", [("if (swg_pw_ones(x, &x->base)) {\n\t\treturn swg_stop(x, SWG_E_DEAD);", "if (swg_pw_ones(x, &x->base)) {\n\t\treturn swg_stop(x, SWG_E_ADDR);")]),
    ("ones: checked after the FWHALT test (poll)", [("if (swg_pw_ones(x, &cur)) {\n\t\t\treturn swg_stop(x, SWG_E_DEAD);\n\t\t}\n", ""), ("ch = swg_pw_diff(&cur, &x->base);", "if (swg_pw_ones(x, &cur)) { return swg_stop(x, SWG_E_DEAD); }\n\t\tch = swg_pw_diff(&cur, &x->base);")]),
    ("go: no window restore after a stop at hostready", [("if (x->rt.win != 0u && swg_win(x, 0u) != 0) {", "if (x->rt.win != 0u && !(x->stopped && x->step == SWG_ST_READY) && swg_win(x, 0u) != 0) {")]),
    ("print: a zero word as %#x (mb data in the TRAP line)", [("flags 0x%x mb data 0x%x TRAP/ASSERT", "flags 0x%x mb data %#x TRAP/ASSERT")]),
    ("print: the TRAP line calls the data a pointer again", [("mb data 0x%x TRAP/ASSERT", "d2h mb 0x%x TRAP/ASSERT")]),
    ("poll: FWHALT in the mailbox data not a stop", [("if ((cur.mb & SWG_D2H_FWHALT) != 0u) {", "if (0) {")]),
    ("poll: FWHALT value 0x20000000", [("#define SWG_D2H_FWHALT   0x10000000u", "#define SWG_D2H_FWHALT   0x20000000u")]),
    ("poll: mailbox data read failure ignored (window on the tail)", [("if (mb_first && swg_rd(x, x->rt.mbp, &w->mb)) {", "if (mb_first && swg_rd(x, x->rt.mbp, &w->mb) && 0) {")]),
    ("poll: mailbox data read failure ignored (window on 0)", [("if (!mb_first && swg_rd(x, x->rt.mbp, &w->mb)) {", "if (!mb_first && swg_rd(x, x->rt.mbp, &w->mb) && 0) {")]),
    ("poll: the pointer is read, not dereferenced", [("swg_rd(x, x->rt.mbp, &w->mb)", "swg_rd(x, x->in.sh + SWG_SH_D2H_MB, &w->mb)")]),
    ("poll: mailbox pointer not read at the entry (data read from 0)", [("swg_rd(x, sh + SWG_SH_D2H_MB, &x->rt.mbp) || ", "")]),
    ("entry: mailbox pointer alignment not checked", [("!swg_ram(x, x->rt.mbp)) {", "!(x->rt.mbp >= SWW_RAM_BASE && x->rt.mbp <= x->in.end - 4u)) {")]),
    ("entry: mailbox pointer RAM range not checked", [("!swg_ram(x, x->rt.mbp)) {", "(x->rt.mbp & 3u) != 0u) {")]),
    ("entry: mailbox pointer upper RAM bound not checked", [("!swg_ram(x, x->rt.mbp)) {", "!((x->rt.mbp & 3u) == 0u && x->rt.mbp >= SWW_RAM_BASE)) {")]),
    ("poll: baseline not taken before hostready", [("\t\tif (st == SWG_ST_READY) {\n\t\t\trc = swg_base(x);", "\t\tif (st == SWG_ST_N) {\n\t\t\trc = swg_base(x);"), ("x->fail = \"a poll read failed\";", "x->fail = \"a poll read failed\";\n\tswg_base(x);")]),
    ("poll: baseline not taken at all (zero)", [("\t\tif (st == SWG_ST_READY) {\n\t\t\trc = swg_base(x);", "\t\tif (st == SWG_ST_N) {\n\t\t\trc = swg_base(x);")]),
    ("baseline: read failure ignored", [("if (swg_pw_read(x, &x->base)) {", "if (swg_pw_read(x, &x->base) && 0) {")]),
    ("baseline: no WIFI GAP for the unread host block at level 12", [('\t\tswl_gap("g2", "host block not read (level 12: D4b observation off)", "medium");\n', "")]),
    ("baseline: no baseline line", [('printf("SWEET WLAN: G2 base flags', 'printf("SWEET WLAN: G2 bAse flags')]),
    ("poll: 0xC30 read at 0xC34", [("#define SWG_PCIE_STATUS_OFF 0xC30u", "#define SWG_PCIE_STATUS_OFF 0xC34u")]),
    ("poll: 0xC30 read failure ignored", [("if (x->stopped || x->b->rd(x->b->ctx, SWG_SP_PCIE, SWG_PCIE_STATUS_OFF, &w->c30)) {", "if (x->stopped || (x->b->rd(x->b->ctx, SWG_SP_PCIE, SWG_PCIE_STATUS_OFF, &w->c30) && 0)) {")]),
    ("poll: d2h_w array slot read of the wrong array (d2h_r)", [("x->rt.ra[SWG_IDX(SWD_IDX_D2H_W)]", "x->rt.ra[SWG_IDX(SWD_IDX_D2H_R)]")]),
    ("poll: only two d2h_w slots read", [("#define SWG_D2H_SLOTS    3u", "#define SWG_D2H_SLOTS    2u")]),
    ("poll: slot stride 8", [("SWG_IDX_SZ * i, &w->dw[i]", "2u * SWG_IDX_SZ * i, &w->dw[i]")]),
    ("poll: flags change not compared", [("c |= a->fl != b->fl ? 1u << 0 : 0u;", "")]),
    ("poll: mailbox data change not compared", [("c |= a->mb != b->mb ? 1u << 1 : 0u;", "")]),
    ("poll: d2h_w[0] change not compared", [("c |= a->dw[0] != b->dw[0] ? 1u << 2 : 0u;", "")]),
    ("poll: d2h_w[1] change not compared", [("c |= a->dw[1] != b->dw[1] ? 1u << 3 : 0u;", "")]),
    ("poll: d2h_w[2] change not compared", [("c |= a->dw[2] != b->dw[2] ? 1u << 4 : 0u;", "")]),
    ("poll: c30 change not compared", [("c |= a->c30 != b->c30 ? 1u << 5 : 0u;", "")]),
    ("poll: a change is not named (flags bit mislabelled)", [("1u << 0 : 0u;", "1u << 1 : 0u;")]),
    ("poll: no early exit when a word changed", [("if (ch != 0u) {", "if (0) {")]),
    ("poll: change line prints before the values are complete (no names)", [('printf(" %s", nm[i]);', "")]),
    ("poll: ring_mem idx/type counts as a change", [("ch = swg_pw_diff(&cur, &x->base);", "ch = swg_pw_diff(&cur, &x->base); swg_rm_or(x, &rm); ch |= rm != 0u;")]),
    ("poll: last ring_mem not scanned", [("*rm = 0;\n\tfor (i = 0; i < SWG_RM_N; i++) {", "*rm = 0;\n\tfor (i = 0; i < SWG_RM_N - 1u; i++) {")]),
    ("poll: ring_mem words not ORed", [("*rm |= w;", "*rm = w;")]),
    ("poll: reads the wrong ring_mem word", [("swg_rd(x, x->rt.rm + SWG_RM_STRIDE * i, &w)", "swg_rd(x, x->rt.rm + SWG_RM_STRIDE * i + 4u, &w)")]),
    ("poll: mailbox data read after the others even on the tail window", [("int mb_first = x->rt.win != 0u;", "int mb_first = 0;")]),
    ("poll: mailbox data always read first", [("int mb_first = x->rt.win != 0u;", "int mb_first = 1;")]),
    ("poll: no wait between polls", [("x->b->delay_ms(x->b->ctx, SWG_POLL_MS);", "")]),
    ("poll: wait after the last poll", [("if (n + 1u < SWG_POLLS) {", "if (1) {")]),
    ("poll: 19 polls", [("#define SWG_POLLS        20u", "#define SWG_POLLS        19u")]),
    ("poll: no gap line when nothing changed", [('swl_gap("g2", "no change vs baseline after hostready", "medium");', "")]),
    ("poll: the old gap text again", [('"no change vs baseline after hostready"', '"no dongle write seen after hostready"')]),
    ("go: no gap line for the 32-bit stores", [('\t\tswl_gap("g2", x->d4b >= 2 ? "ring max/len and Command stores still 32-bit (D-9)" : "16-bit golden stores made as 32-bit words (D-9)", "low");\n', "")]),
    # -- the values --
    ("value: index slot written 0 (upper half lost)", [("return old & 0xFFFF0000u;", "return 0u;")]),
    ("value: index slot written unchanged", [("return old & 0xFFFF0000u;", "return old;")]),
    # -- review 0174-g2-hwwrite-r1 F2: index arrays apart from shared, ring_info/ring_mem and each other --
    ("overlap: index array vs shared not checked", [("if (!swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->in.sh, SWG_SH_TRAP + 8u) ||\n\t\t    ", "if (")]),
    ("overlap: index array vs ring_info/ring_mem not checked", [(" ||\n\t\t    !swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE)) {", ") {")]),
    ("overlap: ring_mem not part of the ring_info range", [("x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE)", "x->rt.ri, SWG_RI_SIZE)")]),
    ("overlap: index arrays vs each other not checked", [("if (!swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->rt.ra[w], SWG_IDX_SZ * SWG_IDX_SLOTS)) {", "if (0) {")]),
    ("overlap: array length one slot", [("if (!swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->rt.ra[w], SWG_IDX_SZ * SWG_IDX_SLOTS)) {", "if (!swg_apart(x->rt.ra[i], SWG_IDX_SZ, x->rt.ra[w], SWG_IDX_SZ)) {")]),
    ("overlap: swg_apart touching ranges refused", [("return a + an <= b || b + bn <= a;", "return a + an < b || b + bn < a;")]),
    ("value: flags2 without the old bits", [("return old | SWG_FLAGS2_BIT;", "return SWG_FLAGS2_BIT;")]),
    ("value: hi dword of a 64-bit host address = lo", [("return (uint32_t)(pa >> (32u * i));", "return (uint32_t)pa;")]),
    ("value: 64-bit host address stored hi then lo", [("for (i = 0; i < r->words; i++) {", "for (i = r->words; i-- > 0u;) {")]),
    # -- W-D4b (sweetwlan=13 read-only, 14 the post): the gate, one mutant per store (address, value, order, restore), the unmask-first rule --
    ("d4b gate: runs without the level", [("if (!x->d4b) {\n\t\tprintf(\"SWEET WLAN: D4b not run: sweetwlan", "if (0) {\n\t\tprintf(\"SWEET WLAN: D4b not run: sweetwlan")]),
    ("d4b gate: never runs", [("\t} else {\n\t\trc = swg_d4b(x);\n\t}", "\t} else {\n\t\t(void)0;\n\t}")]),
    ("d4b gate: runs after a stopped G2", [("} else if (rc != 0) {\n\t\tprintf(\"SWEET WLAN: D4b not run: G2 did not reach", "} else if (0) {\n\t\tprintf(\"SWEET WLAN: D4b not run: G2 did not reach")]),
    ("d4b gate: the read-only level stores", [("\tif (x->d4b < 2) {\n\t\treturn 0;\n\t}", "\tif (0) {\n\t\treturn 0;\n\t}")]),
    ("d4b gate: the post level does not store", [("\tif (x->d4b < 2) {\n\t\treturn 0;\n\t}", "\tif (1) {\n\t\treturn 0;\n\t}")]),
    ("d4b gate: event buffer unchecked at the post level", [("\tif (x->d4b >= 2) {\n\t\tx->fail = \"the d4b event buffer", "\tif (0) {\n\t\tx->fail = \"the d4b event buffer")]),
    ("d4b evbuf: alignment not checked", [(" || (x->in.evbuf & (SWD_EVBUF_ALIGN - 1u)) != 0u) {", ") {")]),
    ("d4b evbuf: range not checked", [("if (swg_hoff(x, x->in.evbuf, SWD_EVBUF_LEN, &o) ||", "if (")]),
    ("d4b evbuf: short range", [("swg_hoff(x, x->in.evbuf, SWD_EVBUF_LEN, &o)", "swg_hoff(x, x->in.evbuf, 4u, &o)")]),
    ("d4b hoff: block end not checked", [(" || pa - x->in.blk > (uint64_t)x->in.blk_len || n > x->in.blk_len - (uint32_t)(pa - x->in.blk)", "")]),
    ("d4b hoff: length not checked", [(" || n > x->in.blk_len - (uint32_t)(pa - x->in.blk)", "")]),
    ("d4b mask: value", [("#define SWG_MBX_MASK_ON  0xFFFFu", "#define SWG_MBX_MASK_ON  0xFFFEu")]),
    ("d4b mask: offset", [("#define SWG_MBX_MASK_OFF 0xC34u", "#define SWG_MBX_MASK_OFF 0xC38u")]),
    ("d4b doorbell: value", [("#define SWG_DB0_VAL      0xFF000001u", "#define SWG_DB0_VAL      0xFF000000u")]),
    ("d4b doorbell: offset", [("#define SWG_DB0_OFF      0x140u", "#define SWG_DB0_OFF      0x144u")]),
    ("d4b item: request id", [("#define SWG_D4_REQID     1u", "#define SWG_D4_REQID     2u")]),
    ("d4b item: epoch", [("#define SWG_D4_EPOCH     1u", "#define SWG_D4_EPOCH     2u")]),
    ("d4b item: length", [("(uint16_t)SWD_EVBUF_LEN, x->in.evbuf", "(uint16_t)4096u, x->in.evbuf")]),
    ("d4b item: buffer is the block", [("(uint16_t)SWD_EVBUF_LEN, x->in.evbuf", "(uint16_t)SWD_EVBUF_LEN, x->in.blk")]),
    ("d4b item: base is ring 1", [("x->in.ring[0] + 4u * (uint32_t)(r - SWG_D4_ITEM0)", "x->in.ring[1] + 4u * (uint32_t)(r - SWG_D4_ITEM0)")]),
    ("d4b item: stride", [("x->in.ring[0] + 4u * (uint32_t)(r - SWG_D4_ITEM0)", "x->in.ring[0] + 8u * (uint32_t)(r - SWG_D4_ITEM0)")]),
    ("d4b item: words big endian", [("x->d4w[i] = (uint32_t)it[4u * i] | ((uint32_t)it[4u * i + 1u] << 8) | ((uint32_t)it[4u * i + 2u] << 16) | ((uint32_t)it[4u * i + 3u] << 24);", "x->d4w[i] = (uint32_t)it[4u * i + 3u] | ((uint32_t)it[4u * i + 2u] << 8) | ((uint32_t)it[4u * i + 1u] << 16) | ((uint32_t)it[4u * i] << 24);")]),
    ("d4b slot: index array is h2d_r", [("swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], 4u, bp)", "swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)], 4u, bp)")]),
    ("d4b slot: value", [("return (old & 0xFFFF0000u) | 1u;", "return (old & 0xFFFF0000u) | 2u;")]),
    ("d4b row: old value not checked", [('\tif (old != swg_d4_oldv(x, r)) {     /* a slot holds the previous index (swg_d4_oldv), every other word 0 */\n\t\treturn swg_stop(x, SWG_E_OLD);\n\t}', "")]),
    ("d4b row: dead link not checked", [('\tx->old = old;\n\tif (old == 0xFFFFFFFFu) {\n\t\treturn swg_stop(x, SWG_E_DEAD);\n\t}\n\tif (old != swg_d4_oldv(x, r)) {', '\tx->old = old;\n\tif (old != swg_d4_oldv(x, r)) {')]),
    ("d4b row: read-back not checked", [("\tif (rb != w) {\n\t\treturn swg_stop(x, SWG_E_READBACK);\n\t}\n\tprintf(\"SWEET WLAN: G2 store %s %s %#x old", "\tprintf(\"SWEET WLAN: G2 store %s %s %#x old")]),
    ("F6: doorbell old value read", [("(!SWG_D4_ISDB(r) && x->b->rd(x->b->ctx, sp, bp, &old))", "(x->b->rd(x->b->ctx, sp, bp, &old))")]),
    ("F6: doorbell read-back read", [("\tif (SWG_D4_ISDB(r)) {\n\t\tx->rb = 0;", "\tif (SWG_D4_ISDB(r)) {\n\t\t(void)x->b->rd(x->b->ctx, sp, bp, &rb);\n\t\tx->rb = 0;")]),
    ("F6: doorbell store line without the reason", [(" (old and read-back not made, golden writes only)", "")]),
    ("F6: every row skips its read-back", [("\tif (SWG_D4_ISDB(r)) {\n\t\tx->rb = 0;", "\tif (1) {\n\t\tx->rb = 0;")]),
    ("d4b row: order not checked", [(" || (r != 0 && (x->d4mask & (1ull << (r - 1))) == 0u)", "")]),
    ("d4b row: ONCE not checked", [("(x->d4mask & (1ull << r)) != 0u ||", "")]),
    ("d4b row: store failure ignored", [("if (swg_put(x, sp, bp, w)) {\n\t\treturn swg_stop(x, SWG_E_WR);", "if (swg_put(x, sp, bp, w) && 0) {\n\t\treturn swg_stop(x, SWG_E_WR);")]),
    ("d4b row: attempt not recorded", [("\tx->d4tried |= 1ull << r;\n", "")]),
    ("d4b row: store bit set before the store (review r2 F11)", [("\t}\n\tx->d4tried |= 1ull << r;\n", "\t}\n"), ("\tw = swg_d4_val(x, r, old);\n", "\tw = swg_d4_val(x, r, old);\n\tx->d4tried |= 1ull << r;\n")]),
    ("d4b row: done not recorded", [("\tx->d4mask |= 1ull << r;\n\treturn 0;", "\treturn 0;")]),
    ("d4b unmask: not called by the undo", [("if (swg_d4_unmask(x) != 0) {", "if (0) {")]),
    ("d4b unmask: result ignored", [("if (swg_d4_unmask(x) != 0) {    /* W-D4b Q8: the mask first, before the EP and RC restores (no-op unless the mask store was attempted) */\n\t\tok = 0;\n\t}", "(void)swg_d4_unmask(x);")]),
    ("d4b unmask: after the EP restore", [("\tif (swg_d4_unmask(x) != 0) {    /* W-D4b Q8: the mask first, before the EP and RC restores (no-op unless the mask store was attempted) */\n\t\tok = 0;\n\t}\n", ""), ("\tx->restore = ok;\n", "\tif (swg_d4_unmask(x) != 0) {\n\t\tok = 0;\n\t}\n\tx->restore = ok;\n")]),
    ("d4b unmask: without the mask store", [("if ((x->d4tried & (1ull << SWG_D4_MASK)) == 0u || x->d4unm) {", "if (x->d4unm) {")]),
    ("d4b unmask: never", [("if ((x->d4tried & (1ull << SWG_D4_MASK)) == 0u || x->d4unm) {", "if (1) {")]),
    ("d4b unmask: value", [("swg_put(x, SWG_SP_PCIE, SWG_MBX_MASK_OFF, 0u)", "swg_put(x, SWG_SP_PCIE, SWG_MBX_MASK_OFF, 1u)")]),
    ("d4b unmask: offset", [("swg_put(x, SWG_SP_PCIE, SWG_MBX_MASK_OFF, 0u)", "swg_put(x, SWG_SP_PCIE, SWG_DB0_OFF, 0u)")]),
    ("d4b unmask: read-back not checked", [(" || now != 0u) {\n\t\tswl_gap(\"g2\", \"stop: d4b mask", ") {\n\t\tswl_gap(\"g2\", \"stop: d4b mask")]),
    ("d4b unmask: failure silent", [("\t\tswl_gap(\"g2\", \"stop: d4b mask 0xC34 restore failed\", \"high\");\n", "")]),
    ("d4b unmask: failure without the STOP line", [("\t\tprintf(\"SWEET WLAN: G2 STOP: d4b mask 0xC34 := 0 FAILED", "\t\tprintf(\"SWEET WLAN: G2 d4b mask 0xC34 := 0 FAILED")]),
    ("d4b unmask: failure reports success", [("interrupts may stay masked\\n\", now);\n\t\treturn -1;", "interrupts may stay masked\\n\", now);\n\t\treturn 0;")]),
    ("d4b poll: dead host d2h_w not checked", [("if (s->d2hw == 0xFFFFFFFFu) {", "if (0) {")]),
    ("d4b poll: dead host h2d_r not checked", [("if (s->h2dr == 0xFFFFFFFFu) {", "if (0) {")]),
    ("d4b poll: dead TCM words not checked", [("if (swg_pw_ones(x, &s->pw)) {", "if (0) {")]),
    ("d4b poll: FWHALT ignored", [("if ((cur.pw.mb & SWG_D2H_FWHALT) != 0u) {\n\t\t\tprintf(\"SWEET WLAN: D4b poll", "if (0) {\n\t\t\tprintf(\"SWEET WLAN: D4b poll")]),
    ("d4b poll: TRAP ignored", [("if ((cur.pw.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {\n\t\t\tprintf(\"SWEET WLAN: D4b poll", "if (0) {\n\t\t\tprintf(\"SWEET WLAN: D4b poll")]),
    ("d4b poll: no wait between reads", [("\t\t\tx->b->delay_ms(x->b->ctx, SWG_POLL_MS);\n\t\t\twaited += SWG_POLL_MS;", "\t\t\twaited += SWG_POLL_MS;")]),
    ("d4b poll: does not stop at the host write", [("if ((cur.d2hw & 0xFFFFu) != (x->d4base.d2hw & 0xFFFFu)) {\n\t\t\tbreak;", "if (0) {\n\t\t\tbreak;")]),
    ("d4b poll: post poll breaks on absolute d2hw", [("if ((cur.d2hw & 0xFFFFu) != (x->d4base.d2hw & 0xFFFFu)) {\n\t\t\tbreak;", "if ((cur.d2hw & 0xFFFFu) != 0u) {\n\t\t\tbreak;")]),
    ("d4b poll: post decodes item 0", [("k = x->d4base.d2hw & 0xFFFFu;", "k = 0u;")]),
    ("d4b poll: post item checked against epoch 1", [("swd4b_d2h_check(in, (uint8_t)(SWG_D4_EPOCH + k), &type)", "swd4b_d2h_check(in, (uint8_t)SWG_D4_EPOCH, &type)")]),
    ("d4b poll: post item gaps gated on the baseline", [("if (ex >= 0 && (!raised || k != 0u)) {", "if (ex >= 0 && !raised) {")]),
    ("d4b poll: changes not printed", [("printf(\"SWEET WLAN: D4b poll %u: %s 0x%x -> 0x%x\\n\", n + 1u, nm[i], a[i], b[i]);", "(void)nm;")]),
    ("d4b poll: read failure ignored", [("x->fail = \"a d4b poll read failed\";\n\t\tif (swg_d4_read(x, &cur, k)) {", "x->fail = \"a d4b poll read failed\";\n\t\tif (swg_d4_read(x, &cur, k) && 0) {")]),
    ("d4b poll: tcm d2h_w left out of the poll values", [("cur.pw.c30, cur.pw.dw[0], cur.pw.dw[1], cur.pw.dw[2]);", "cur.pw.c30, 0u, 0u, 0u);")]),
    ("d4b poll: no gap for a silent dongle", [("\t\tswl_gap(\"d4b\", gb, \"high\");\n\t\tif (seen) {", "\t\tif (seen) {")]),
    # -- review 0174-w-d4b-hwwrite-r1 F3: "no dongle write seen" counts host-memory words only; the zero-item check also after the poll --
    ("F3: host-write flag set by any word", [("if (i >= 6u) {      /* review r1 F3", "if (i >= 0u) {      /* review r1 F3")]),
    ("F3: host-write flag never set", [("if (i >= 6u) {      /* review r1 F3", "if (i >= 14u) {      /* review r1 F3")]),
    ("F3: host-write flag misses h2d_r0", [("if (i >= 6u) {      /* review r1 F3", "if (i >= 7u) {      /* review r1 F3")]),
    ("F3: host-write flag takes the TCM d2h_w words", [("if (i >= 6u) {      /* review r1 F3", "if (i >= 2u) {      /* review r1 F3")]),
    ("F3: only-TCM line missing", [("\t\t\tprintf(\"SWEET WLAN: D4b only TCM/core words changed (no host-memory write)\\n\");\n", "")]),
    ("F3: only-TCM line at every silent run", [("\t\tif (seen) {\n\t\t\tprintf(\"SWEET WLAN: D4b only TCM/core", "\t\tif (1) {\n\t\t\tprintf(\"SWEET WLAN: D4b only TCM/core")]),
    ("F3: post-poll zero-item check removed", [("\tif (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3", "\tif (0) {     /* review r1 F3")]),
    ("F3: post-poll check on the host index only", [("\tif (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3", "\tif (((cur.d2hw & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3")]),
    ("F3: post-poll check on the TCM index only", [("\tif (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3", "\tif (((cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3")]),
    ("F3: post-poll check ignores the item", [("\tif (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3", "\tif (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k)) {     /* review r1 F3")]),
    ("F3: post-poll check on any index, not past item k (d4b13 unanswered post)", [("\tif (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3", "\tif (((cur.d2hw | cur.pw.dw[0]) & 0xFFFFu) != 0u && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3")]),
    ("F3: post-poll gap line missing", [("\t\tprintf(\"WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache), after the poll\\n\");\n", "")]),
    ("F3: post-poll gap not high", [("\t\tswl_gap(\"d4b\", \"dongle advanced ring 2 but host item is zero\", \"high\");\n\t\tprintf(\"WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache), after", "\t\tswl_gap(\"d4b\", \"dongle advanced ring 2 but host item is zero\", \"low\");\n\t\tprintf(\"WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache), after")]),
    # -- review r1 F4: a store the level-13 observation makes behind swg_put's back (not counted by nstore) is seen by the bus log --
    ("F4: a raw bus store at level 13", [("\tx->hostrd = 1;", "\tx->hostrd = 1;\n\tif (x->d4b == 1) {\n\t\t(void)x->b->wr(x->b->ctx, SWG_SP_EP, 0x88u, 1u);\n\t}")]),
    ("F4: a window move to a foreign value at level 12", [("\trc = swg_entry(x);\n\tfor (st = 0;", "\trc = swg_entry(x);\n\tif (x->d4b == 0 && rc == 0) {\n\t\t(void)x->b->wr(x->b->ctx, SWG_SP_WIN2, 0x84u, 0x800000u);\n\t}\n\tfor (st = 0;")]),
    ("d4b ro: tcm d2h_w left out of the check", [("(((x->d4base.pw.dw[0] | x->d4base.d2hw) & 0xFFFFu) != 0u &&", "(((x->d4base.d2hw) & 0xFFFFu) != 0u &&")]),
    ("d4b ro: host item zero gap missing", [("\t\tswl_gap(\"d4b\", \"dongle advanced ring 2 but host item is zero\", \"high\");", "")]),
    ("d4b ro: host item zero line missing", [("\t\tprintf(\"WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache)\\n\");\n", "")]),
    ("d4b ro: gap although the item is there", [(" && (x->d4base.r2[0] | x->d4base.r2[1] | x->d4base.r2[2] | x->d4base.r2[3] | x->d4base.r2[4] | x->d4base.r2[5]) == 0u) {\n\t\tswl_gap", ") {\n\t\tswl_gap")]),
    ("d4b ro: gap although the tcm index did not move", [("if ((x->d4base.pw.dw[0] & 0xFFFFu) != 0u && (x->d4base.r2[0]", "if ((x->d4base.pw.dw[0] & 0xFFFFu) == 0u && (x->d4base.r2[0]")]),
    ("d4b ro: epoch not checked against 1", [("swd4b_d2h_check(in, (uint8_t)SWG_D4_EPOCH, &type) : -1;\n\txb = ex == SWD4B_E_XOR || (ex == SWD4B_E_EPOCH && swd4b_d2h_check(in, in[3], &type) == SWD4B_E_XOR);\n\tprintf(\"SWEET WLAN: D4b ro", "swd4b_d2h_check(in, in[3], &type) : -1;\n\txb = ex == SWD4B_E_XOR || (ex == SWD4B_E_EPOCH && swd4b_d2h_check(in, in[3], &type) == SWD4B_E_XOR);\n\tprintf(\"SWEET WLAN: D4b ro")]),
    ("d4b ro: xor verdict inverted", [("ex < 0 ? \"n/a\" : xb ? \"bad\" : \"ok\", x->d4base.pw.c30);", "ex < 0 ? \"n/a\" : xb ? \"ok\" : \"bad\", x->d4base.pw.c30);")]),
    ("d4b ro: epoch/xor gap missing", [('\t\t\tswl_gap("d4b", "epoch/xor mismatch", "high");\n\t\t\tprintf("SWEET WLAN: D4b epoch/xor mismatch: check %d, epoch 0x%x, want 0x%x\\n", ex, in[3], SWG_D4_EPOCH);\n\t\t}\n\t\tif (x->d4b < 3 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {      /* sweetwlan=16: a clean D2H_MAILBOX_DATA item 0 is answered or refused by swg_d4c, which raises its own gap */\n\t\t\tsnprintf(gb, sizeof gb, "ring-2 item type 0x%x not handled", type);\n\t\t\tswl_gap("d4b", gb, "high");\n\t\t}\n\t}\n\tif (x->d4b < 2)', "\t\t}\n\t}\n\tif (x->d4b < 2)")]),
    ("d4b ro: ring-2 words from slot 1", [("x->in.ring[SWD_D2H_CTRL - SWD_H2D_CTRL] + 4u * i", "x->in.ring[SWD_D2H_TXC - SWD_H2D_CTRL] + 4u * i")]),
    ("d4b ro: h2d_r0 read from the h2d_w array", [("x->in.idx[SWG_IDX(SWD_IDX_H2D_R)], &s->h2dr", "x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], &s->h2dr")]),
    ("d4b ro: d2h_w0 read from the d2h_r array", [("x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &s->d2hw", "x->in.idx[SWG_IDX(SWD_IDX_D2H_R)], &s->d2hw")]),
    ("d4b ro: ring-2 item shortened", [("for (i = 0; i < 6u; i++) {\n\t\tif (swg_hrd(", "for (i = 0; i < 5u; i++) {\n\t\tif (swg_hrd(")]),
    ("d4b gap: event buffer size not said", [("\t\tswl_gap(\"d4b\", \"event buffer size unknown, using 8192\", \"medium\");\n", "")]),
    ("d4b gap: interrupts not said", [("\t\tswl_gap(\"d4b\", \"interrupts not enabled, mask 0xFFFF, no ELBI/GIC path\", \"low\");", "")]),
    ("d4b gap: one buffer not said", [("\t\t\tswl_gap(\"d4b\", \"only 1 buffer posted (DHD posts 8+32)\", \"low\");", "\t\t\t(void)0;")]),
    ("d4b base: line missing", [("\tprintf(\"SWEET WLAN: D4b base h2d_r0", "\t(void)printf(\"\"); (void)(\"SWEET WLAN: D4b base h2d_r0")]),
    # Equivalent mutants left out (AP-26): swg_dump's x->con_n is only set on success (mk() zeroes it), so a reset on failure is dead.
    # ---- W-D4b gaps: the dump (swg_dump / swg_con), the miss resolution, the 2BYTE pin, the host-block gap, cfg 0x88 and the 16-bit slot stores ----
    ("dump: not called", [("\treturn swg_dump(x);\n}", "\treturn 0;\n}")]),
    ("dump: h2d mailbox pointer from the d2h offset", [("swg_rd(x, x->in.sh + SWG_SH_H2D_MB, &hp)", "swg_rd(x, x->in.sh + SWG_SH_D2H_MB, &hp)")]),
    ("dump: h2d mailbox data not read through the pointer", [("!swg_rd(x, hp, &mh) && ", "")]),
    ("dump: d2h mailbox data is the pointer", [("!swg_rd(x, x->rt.mbp, &md);", "!swg_rd(x, x->in.sh + SWG_SH_D2H_MB, &md);")]),
    ("dump: mailbox failure ignored", [("\tx->rd_mb = mb_ok;\n", "\tx->rd_mb = 1;\n")]),
    ("dump: ring_info failure ignored", [("\tx->rd_ri = ri_ok;\n", "\tx->rd_ri = 1;\n")]),
    ("dump: console failure ignored", [("\tx->rd_con = con_ok;\n", "\tx->rd_con = 1;\n")]),
    ("dump: ring_info one word short", [("for (i = 0; i < SWG_RI_WORDS; i++) {\n\t\tif (swg_rd(x, x->rt.ri + 4u * i, &ri[i])) {", "for (i = 0; i < SWG_RI_WORDS - 1u; i++) {\n\t\tif (swg_rd(x, x->rt.ri + 4u * i, &ri[i])) {")]),
    ("dump: ring_info words shifted", [("swg_rd(x, x->rt.ri + 4u * i, &ri[i])", "swg_rd(x, x->rt.ri + 4u * i + 4u, &ri[i])")]),
    ("dump: ring_info not printed", [("\t\tprintf(\"SWEET WLAN: G2 rings_info +0x%02x:\", 4u * i);", "\t\t(void)printf(\"\"); (void)(\"SWEET WLAN: G2 rings_info +0x%02x:\", 4u * i);")]),
    ("dump: ring_info line takes five words", [("j < i + 4u && j < SWG_RI_WORDS; j++", "j < i + 5u && j < SWG_RI_WORDS; j++")]),
    ("dump: summary line missing", [("\tprintf(\"SWEET WLAN: G2 read for F1/F2: h2d mb 0x%x d2h mb 0x%x rings_info 0x%x console %u bytes%s\\n\", mh, md, x->rt.ri, x->con_n, con_ok ? \"\" : \" (not read)\");", "")]),
    ("dump: pointers line missing", [("\tprintf(\"SWEET WLAN: G2 read for F1/F2: h2d mb ptr 0x%x d2h mb ptr 0x%x\\n\", hp, x->rt.mbp);", "")]),
    ("dump: no gap for an unread console", [("\t\tswl_gap(\"g2\", \"dump: firmware console not read\", \"medium\");", "")]),
    ("dump: no gap for unread mailbox data", [("\t\tswl_gap(\"g2\", \"dump: mailbox data not read\", \"medium\");", "")]),
    ("dump: no gap for unread rings_info", [("\t\tswl_gap(\"g2\", \"dump: rings_info not read\", \"medium\");", "")]),
    ("dump: console pointer from the wrong word", [("#define SWG_SH_CONSOLE   0x14u", "#define SWG_SH_CONSOLE   0x18u")]),
    ("dump: console buffer offset", [("#define SWG_CON_BUF      0x08u", "#define SWG_CON_BUF      0x04u")]),
    ("dump: console cap", [("#define SWG_CON_MAX      2048u", "#define SWG_CON_MAX      4096u")]),
    ("dump: console line width", [("#define SWG_CON_LINE     120u", "#define SWG_CON_LINE     100u")]),
    ("dump: console size 0 accepted", [("if (size == 0u || (size & 3u) != 0u ||", "if ((size & 3u) != 0u ||")]),
    ("dump: console size alignment not checked", [("if (size == 0u || (size & 3u) != 0u ||", "if (size == 0u ||")]),
    ("dump: console buffer not checked as RAM", [("!swg_ram(x, buf) || size > x->in.end - buf", "size > x->in.end - buf")]),
    ("dump: console end beyond the RAM not checked", [("|| size > x->in.end - buf ||", "||")]),
    ("dump: console index beyond the size accepted", [("|| idx > size) {", ") {")]),
    ("dump: console index equal to the size refused", [("|| idx > size) {", "|| idx >= size) {")]),
    ("dump: console start ignores the index", [("p = (idx % size + size - len) % size;", "p = (size - len) % size;")]),
    ("dump: console start is the index", [("p = (idx % size + size - len) % size;", "p = idx % size;")]),
    ("dump: console wrap off by one", [("p = p + 1u >= size ? 0u : p + 1u", "p = p + 1u > size ? 0u : p + 1u")]),
    ("dump: console never wraps", [("p = p + 1u >= size ? 0u : p + 1u", "p = p + 1u")]),
    ("dump: console byte lane", [("c = (w >> (8u * (p & 3u))) & 0xFFu;", "c = (w >> (8u * (3u - (p & 3u)))) & 0xFFu;")]),
    ("dump: console word address", [("swg_rd(x, buf + (p & ~3u), &w)", "swg_rd(x, buf + p, &w)")]),
    ("dump: console CR kept", [("} else if (c != 0u && c != '\\r') {", "} else if (c != 0u) {")]),
    ("dump: console NUL kept", [("} else if (c != 0u && c != '\\r') {", "} else if (c != '\\r') {")]),
    ("dump: console LF not a line end", [("if (c == '\\n') {\n\t\t\tswg_con_flush(lb, &ln);\n\t\t} else if", "if (0) {\n\t\t\tswg_con_flush(lb, &ln);\n\t\t} else if")]),
    ("dump: console non-printable shown raw", [("lb[ln++] = c >= 0x20u && c < 0x7Fu ? (char)c : '.';", "lb[ln++] = (char)c;")]),
    ("dump: console 0x7F shown raw", [("c >= 0x20u && c < 0x7Fu ?", "c >= 0x20u && c <= 0x7Fu ?")]),
    ("dump: console long line not wrapped", [("\t\t\tif (ln == SWG_CON_LINE) {\n\t\t\t\tswg_con_flush(lb, &ln);\n\t\t\t}\n", "")]),
    ("dump: console last line not flushed", [("\tswg_con_flush(lb, &ln);\n\tx->con_n = len;", "\tx->con_n = len;")]),
    ("dump: console read failure not flushed", [("\t\t\tswg_con_flush(lb, &ln);\n\t\t\tprintf(\"SWEET WLAN: G2 console word", "\t\t\tprintf(\"SWEET WLAN: G2 console word")]),
    ("dump: console prefix", [("printf(\"SWEET WLAN: fw console: %s\\n\", b);", "printf(\"SWEET WLAN: console: %s\\n\", b);")]),
    ("dump: console byte count", [("\tx->con_n = len;", "\tx->con_n = len + 1u;")]),
    ("miss: console resolved without the read", [("\tif (x->rd_con) {\n\t\tswl_miss_resolve(\"f1\", SWL_M_F1_CON);", "\tif (1) {\n\t\tswl_miss_resolve(\"f1\", SWL_M_F1_CON);")]),
    ("miss: console never resolved", [("\t\tswl_miss_resolve(\"f1\", SWL_M_F1_CON);\n", "")]),
    ("miss: console resolved by the mailbox read", [("\tif (x->rd_con) {\n\t\tswl_miss_resolve(\"f1\", SWL_M_F1_CON);", "\tif (x->rd_mb) {\n\t\tswl_miss_resolve(\"f1\", SWL_M_F1_CON);")]),
    ("miss: mailbox resolved without the read", [("\tif (x->rd_mb) {", "\tif (1) {")]),
    ("miss: f1 mailbox never resolved", [("\t\tswl_miss_resolve(\"f1\", SWL_M_F1_MB);\n", "")]),
    ("miss: f2 mailbox never resolved", [("\t\tswl_miss_resolve(\"f2\", SWL_M_F2_MB);\n", "")]),
    ("miss: f2 mailbox resolved by the ring_info read", [("\t\tswl_miss_resolve(\"f2\", SWL_M_F2_MB);\n", ""), ("\t\tswl_miss_resolve(\"f2\", SWL_M_F2_RI);\n", "\t\tswl_miss_resolve(\"f2\", SWL_M_F2_RI);\n\t\tswl_miss_resolve(\"f2\", SWL_M_F2_MB);\n")]),
    ("miss: ring_info resolved without the read", [("\tif (x->rd_ri) {", "\tif (1) {")]),
    ("miss: f1 ring_info never resolved", [("\t\tswl_miss_resolve(\"f1\", SWL_M_F1_RI);\n", "")]),
    ("miss: f2 ring_info never resolved", [("\t\tswl_miss_resolve(\"f2\", SWL_M_F2_RI);\n", "")]),
    ("miss: g1 resolved without the pins", [("\tif (x->pins_ok) {", "\tif (1) {")]),
    ("miss: g1 never resolved", [("\t\tswl_miss_resolve(\"g1\", SWL_M_G1);\n", "")]),
    ("miss: host block resolved without the read", [("\tif (x->hostrd) {", "\tif (1) {")]),
    ("miss: host block never resolved", [("\t\tswl_miss_resolve(\"g2\", SWL_M_G2HOST);\n", "")]),
    ("miss: resolution not run at the end", [("\tswg_miss_end(x);\n\tprintf(\"SWEET WLAN: G2 %s rc=%d", "\tprintf(\"SWEET WLAN: G2 %s rc=%d")]),
    ("pins: 2BYTE not checked", [("\tif ((fl & SWG_FLAGS_2BYTE) != 0u) {\n\t\treturn swg_stop(x, SWG_E_FLAGS);\n\t}\n", "")]),
    ("pins: 2BYTE bit", [("#define SWG_FLAGS_2BYTE  0x00100000u", "#define SWG_FLAGS_2BYTE  0x00200000u")]),
    ("pins: 2BYTE inverted", [("if ((fl & SWG_FLAGS_2BYTE) != 0u) {", "if ((fl & SWG_FLAGS_2BYTE) == 0u) {")]),
    ("pins: 2BYTE stop code", [("if ((fl & SWG_FLAGS_2BYTE) != 0u) {\n\t\treturn swg_stop(x, SWG_E_FLAGS);", "if ((fl & SWG_FLAGS_2BYTE) != 0u) {\n\t\treturn swg_stop(x, SWG_E_ENTRY);")]),
    ("pins: 2BYTE stop without a reason", [("\tx->fail = \"flags: PCIE_SHARED_2BYTE_INDICES (0x100000) set, G1 planned 4-byte index slots\";\n", "")]),
    ("pins: verified before the counts", [("\tx->pins_ok = 1;\n\tprintf(\"SWEET WLAN: G2 pins verified", "\tprintf(\"SWEET WLAN: G2 pins verified")]),
    ("pins: never verified", [("\tx->pins_ok = 1;\n", "")]),
    ("pins: verified line missing", [("\tprintf(\"SWEET WLAN: G2 pins verified: rw_index_sz %u (2BYTE clear), rings %u/%u\\n\", SWG_IDX_SZ, SWG_MAX_SUB, SWG_MAX_CPL);\n", "")]),
    ("pins: verified line sizes", [("(2BYTE clear), rings %u/%u\\n\", SWG_IDX_SZ, SWG_MAX_SUB, SWG_MAX_CPL);", "(2BYTE clear), rings %u/%u\\n\", SWG_IDX_SZ, SWG_MAX_CPL, SWG_MAX_SUB);")]),
    ("host gap: gap at every level", [("\tif (x->d4b == 0) {\n\t\tswl_gap(\"g2\", \"host block not read (level 12: D4b observation off)\", \"medium\");\n\t} else {", "\tif (1) {\n\t\tswl_gap(\"g2\", \"host block not read (level 12: D4b observation off)\", \"medium\");\n\t} else {")]),
    ("host gap: gap at levels 12 and 13", [("\tif (x->d4b == 0) {\n\t\tswl_gap(\"g2\", \"host block not read (level 12: D4b observation off)\"", "\tif (x->d4b <= 1) {\n\t\tswl_gap(\"g2\", \"host block not read (level 12: D4b observation off)\"")]),
    ("host gap: level 13/14 miss not recorded", [("\t\tswl_miss(\"g2\", SWL_M_G2HOST, \"medium\");", "")]),
    ("host gap: level 13/14 plain gap", [("\t\tswl_miss(\"g2\", SWL_M_G2HOST, \"medium\");", "\t\tswl_gap(\"g2\", SWL_M_G2HOST, \"medium\");")]),
    ("host gap: read flag never set", [("\tx->hostrd = 1;", "\t")]),
    ("host gap: read flag set before the read", [("\tx->hostrd = 1;          /* the host block words were read (swg_d4_read ok): the miss of swg_base is resolved */\n", ""), ("\tx->step = SWG_ST_N + 1u;\n\tif (x->d4b >= 2) {", "\tx->step = SWG_ST_N + 1u;\n\tx->hostrd = 1;\n\tif (x->d4b >= 2) {")]),
    ("w16: slots stored as 16-bit at level 13", [("int w16 = r->grp == SWG_G_SLOT && x->d4b >= 2;", "int w16 = r->grp == SWG_G_SLOT && x->d4b >= 1;")]),
    ("w16: slots stored as 16-bit at level 12", [("int w16 = r->grp == SWG_G_SLOT && x->d4b >= 2;", "int w16 = r->grp == SWG_G_SLOT;")]),
    ("w16: slots never 16-bit", [("int w16 = r->grp == SWG_G_SLOT && x->d4b >= 2;", "int w16 = r->grp == SWG_G_SLOT && x->d4b >= 3;")]),
    ("w16: ring max/len stored 16-bit", [("int w16 = r->grp == SWG_G_SLOT && x->d4b >= 2;", "int w16 = (r->grp == SWG_G_SLOT || r->grp == SWG_G_RM) && x->d4b >= 2;")]),
    ("w16: value is not the low half of the word", [("swg_put16(x, sp, bp, (uint16_t)w)", "swg_put16(x, sp, bp, (uint16_t)1u)")]),
    ("w16: value is the high half", [("swg_put16(x, sp, bp, (uint16_t)w)", "swg_put16(x, sp, bp, (uint16_t)(w >> 16))")]),
    ("w16: put16 not counted", [("\tx->nstore++;\n\treturn x->b->wr16 == NULL", "\treturn x->b->wr16 == NULL")]),
    ("w16: missing bus store taken for success", [("\treturn x->b->wr16 == NULL ? -1 : x->b->wr16(x->b->ctx, sp, a, v);", "\treturn x->b->wr16 == NULL ? 0 : x->b->wr16(x->b->ctx, sp, a, v);")]),
    ("w16: put16 stores 32-bit", [("x->b->wr16(x->b->ctx, sp, a, v)", "x->b->wr(x->b->ctx, sp, a, v)")]),
    ("w16: line not marked", [(" (16-bit store, upper half unchanged)", "")]),
    ("w16: line marked at 32-bit rows", [("w16 ? \" (16-bit store, upper half unchanged)\" : \"\"", "\" (16-bit store, upper half unchanged)\"")]),
    ("w16: read-back is only the low half", [("\t\tif (!swg_rb_ok(st, w, rb)) {", "\t\tif (w16 ? (rb & 0xFFFFu) != (w & 0xFFFFu) : !swg_rb_ok(st, w, rb)) {")]),
    ("D-9: level 14 keeps the old gap text", [("x->d4b >= 2 ? \"ring max/len and Command stores still 32-bit (D-9)\" : \"16-bit golden stores made as 32-bit words (D-9)\"", "\"16-bit golden stores made as 32-bit words (D-9)\"")]),
    # -- SPEC-WIFI D-12 / review 0174-w-d4b-hwwrite-r1 F1, F2: stage-3 row W15 (cfg 0x88 |= 0x300, sweetwlan=15 only), mutants of sweet_wlan_wr.h (third element "wr"), killed by w15_tests() of wlan_g2_host_test.c --
    ("W15: armed on the attempt, not the store", [("SWT_ONCE | SWT_ARMST | SWT_W15", "SWT_ONCE | SWT_W15", "wr")]),
    ("W15: never armed by the store", [("\tx->armed |= p->rgrp;    /* SWT_ARMST rows", "\t(void)p->rgrp;    /* SWT_ARMST rows", "wr")]),
    ("W15: restore value is the OR'ed word", [("{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88, 0xFFFFFFFFu },", "{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88 | SWW_C88_BITS, 0xFFFFFFFFu },", "wr")]),
    ("W15: restore row before the CA7 re-halt", [("\t{ SWW_RG_CA, SWW_BAR0, SWW_WIN_CA7WRAP, SWL_AGENT_RESET_CTL, 1u, 1u },\n\t{ SWW_RG_W84, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu },\n\t{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88, 0xFFFFFFFFu },\n", "\t{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88, 0xFFFFFFFFu },\n\t{ SWW_RG_CA, SWW_BAR0, SWW_WIN_CA7WRAP, SWL_AGENT_RESET_CTL, 1u, 1u },\n\t{ SWW_RG_W84, SWW_CFG, 0, SWW_CFG_WIN2, 0u, 0xFFFFFFFFu },\n", "wr")]),
    ("W15: restore row not whole-word", [("{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88, 0xFFFFFFFFu },", "{ SWW_RG_C88, SWW_CFG, 0, 0x88u, SWW_CFG88, 0x000000FFu },", "wr")]),
    ("W15: live at level 14 (the row gate)", [(" || ((p->flags & SWT_W15) && !x->w15)", "", "wr")]),
    ("W15: step runs at level 14", [("\tx->row = 15;\n\tif (!x->w15) {\n\t\treturn 0;\n\t}\n", "\tx->row = 15;\n", "wr")]),
    ("W15: step runs at every level and stores", [("\tx->row = 15;\n\tif (!x->w15) {\n\t\treturn 0;\n\t}\n", "\tx->row = 15;\n\tx->w15 = 1;\n", "wr")]),
    ("W15: allowed after the release started", [("\tif ((x->flags & (SWF_W16 | SWF_RELOK)) != 0u) {", "\tif (0) {", "wr")]),
    ("W15: only W16 counts as the release", [("\tif ((x->flags & (SWF_W16 | SWF_RELOK)) != 0u) {", "\tif ((x->flags & SWF_W16) != 0u) {", "wr")]),
    ("W15: only RELOK counts as the release", [("\tif ((x->flags & (SWF_W16 | SWF_RELOK)) != 0u) {", "\tif ((x->flags & SWF_RELOK) != 0u) {", "wr")]),
    ("W15: refusal after the release without a gap", [("\t\tswl_gap(\"w15\", \"refused: the CA7 release already started\", \"high\");\n", "", "wr")]),
    ("W15: refusal after the release without a stop", [("\t\treturn swl_stop(x, SWW_E_PHASE);\n\t}\n\tSWW_RD(x, SWW_CFG, 0, 0x88u, old);", "\t\treturn 0;\n\t}\n\tSWW_RD(x, SWW_CFG, 0, 0x88u, old);", "wr")]),
    ("W15: row after the release rows", [("\tSWT_C88,                                                        /* W15 (patch 0174 D-12, sweetwlan=15 only): cfg 0x88 |= 0x300 with the ARM halted, before the release */\n", "", "wr"), ("\tSWT_WIN2_ZERO_E,", "\tSWT_C88,\n\tSWT_WIN2_ZERO_E,", "wr")]),
    ("W15: old not pinned", [(".omask = 0xFFFFFFFFu, .oval = SWW_CFG88, .need = SWF_W0 },", ".omask = 0, .oval = SWW_CFG88, .need = SWF_W0 },", "wr")]),
    ("W15: old pinned to the OR'ed word", [(".omask = 0xFFFFFFFFu, .oval = SWW_CFG88, .need = SWF_W0 },", ".omask = 0xFFFFFFFFu, .oval = SWW_CFG88 | SWW_C88_BITS, .need = SWF_W0 },", "wr")]),
    ("W15: without the W0 check", [(".omask = 0xFFFFFFFFu, .oval = SWW_CFG88, .need = SWF_W0 },", ".omask = 0xFFFFFFFFu, .oval = SWW_CFG88, .need = 0 },", "wr")]),
    ("W15: not once", [("SWT_ONCE | SWT_ARMST | SWT_W15", "SWT_ARMST | SWT_W15", "wr")]),
    ("W15: bits", [("#define SWW_C88_BITS    0x300u", "#define SWW_C88_BITS    0x200u", "wr")]),
    ("W15: read-back mask", [(".off = 0x88u, .val = SWW_CFG88 | SWW_C88_BITS, .vmask = 0xFFFFFFFFu,", ".off = 0x88u, .val = SWW_CFG88 | SWW_C88_BITS, .vmask = 0x00000300u,", "wr")]),
    ("W15: C88 restored with the CA7 not re-halted (review r2 F12)", [("if (r->grp == SWW_RG_C88 && (x->flags & SWF_W16) && !x->ca_ok) {", "if (0) {", "wr")]),
    ("W15: wrong restore group", [(".rgrp = SWW_RG_C88, .flags = SWT_ONCE | SWT_ARMST", ".rgrp = SWW_RG_W84, .flags = SWT_ONCE | SWT_ARMST", "wr")]),
    ("W15: keeps no restore armed (swl_done)", [("\tswl_miss_resolve(\"w15\", SWL_M_W15);\n\tx->flags |= SWF_W15;\n\treturn 0;", "\tswl_miss_resolve(\"w15\", SWL_M_W15);\n\treturn swl_done(x, SWF_W15);", "wr")]),
    ("W15: gap resolved by the attempt", [("\trc = swl_wr(x, SWW_CFG, 0, 0x88u, SWW_CFG88 | SWW_C88_BITS);\n\tif (rc) {", "\trc = swl_wr(x, SWW_CFG, 0, 0x88u, SWW_CFG88 | SWW_C88_BITS);\n\tswl_miss_resolve(\"w15\", SWL_M_W15);\n\tif (rc) {", "wr")]),
    ("W15: gap never resolved", [("\tswl_miss_resolve(\"w15\", SWL_M_W15);\n\tx->flags |= SWF_W15;", "\tx->flags |= SWF_W15;", "wr")]),
    ("W15: result line missing", [("\tprintf(\"SWEET WLAN: W15 cfg 0x88 %#x -> %#x read back\\n\", old, x->rb);\n", "", "wr")]),
    ("W15: mismatch line missing", [("\t\t\tprintf(\"SWEET WLAN: W15 cfg 0x88 %#x -> %#x read back %#x (mismatch), restore follows\\n\", old, SWW_CFG88 | SWW_C88_BITS, x->rb);\n", "", "wr")]),
    ("D-9: level 12 gets the level 14 text", [("x->d4b >= 2 ? \"ring max/len and Command stores still 32-bit (D-9)\" : \"16-bit golden stores made as 32-bit words (D-9)\"", "\"ring max/len and Command stores still 32-bit (D-9)\"")]),
    # -- W-D4c (sweetwlan=16): the inband-device-wake sequence; the header mutants of the ack, wake, exit-note poll, deassert and the final read are generated by _d4c_g2() (appended below); these remain from the first level-16 version --
    ('d4c: precondition: wrap not refused', [('if (wrap) {\n\t\tno = ', 'if (0) {\n\t\tno = ')]),
    ('d4c: precondition: no item 0 not refused', [('} else if (ex < 0) {', '} else if (0) {')]),
    ('d4c: precondition: epoch/xor not checked', [('} else if (ex != 0) {\n\t\tno = ', '} else if (0) {\n\t\tno = ')]),
    ('d4c: precondition: type not checked', [('} else if (type != SWD4B_MSG_D2H_MBDATA) {', '} else if (0) {')]),
    ('d4c: precondition: other mailbox bits accepted', [('} else if ((mb & ~SWD4B_D2H_DS_ENTER_REQ) != 0u) {', '} else if (0) {')]),
    ('d4c: precondition: mailbox bits text shows the whole word', [('"d2h mailbox bits 0x%x not handled", mb & ~SWD4B_D2H_DS_ENTER_REQ);', '"d2h mailbox bits 0x%x not handled", mb);')]),
    ('d4c: precondition: no DS_ENTER_REQ bit accepted', [('} else if ((mb & SWD4B_D2H_DS_ENTER_REQ) == 0u) {', '} else if (0) {')]),
    ('d4c: precondition: D2H-WR > 1 accepted', [('} else if (n > 1u) {', '} else if (0) {')]),
    ('d4c: precondition: D2H-WR 0 accepted', [('} else if (n == 0u) {', '} else if (0) {')]),
    ('d4c: precondition: D2H-WR not re-read', [('n = q.d2hw & 0xFFFFu;', 'n = b->d2hw & 0xFFFFu;')]),
    ('d4c: precondition: dead link on the re-read not checked (review d4c r1 F5)', [('\t\tif (swg_d4_ones(x, &q)) {\n\t\t\treturn swg_stop(x, SWG_E_DEAD);\n\t\t}\n\t\tex = swg_d4c_stops', '\t\tex = swg_d4c_stops')]),
    ('d4c: precondition: D2H-WR not checked', [('n = q.d2hw & 0xFFFFu;', 'n = 1u;')]),
    ('d4c: precondition failure still stores', [('\t\treturn 0;\n\t}\n\tx->d4c_wr = 1u;', '\t}\n\tx->d4c_wr = 1u;')]),
    ('d4c: precondition failure without a gap', [('\t\tswl_gap("d4c", no, "high");\n', '')]),
    ('d4c: precondition failure without a line', [('\t\tprintf("SWEET WLAN: D4c not answered: %s (item 0 type 0x%x mailbox data 0x%x epoch/xor check %d)\\n", no, type, mb, ex);\n', '')]),
    ('d4c: answered line not printed', [('\tprintf("SWEET WLAN: D4c ring-2 item 0 DS_ENTER_REQ answered (DS_ACK)\\n");\n', '')]),
    ('d4c: gate at sweetwlan=14', [('ex = x->d4b >= 3 ? swg_d4c(x, wrap) : 0;', 'ex = x->d4b >= 2 ? swg_d4c(x, wrap) : 0;')]),
    ('d4d: gate at sweetwlan=16', [('if (ex == 0 && x->d4b >= 4) {', 'if (ex == 0 && x->d4b >= 3) {')]),
    ('d4d: gate never', [('if (ex == 0 && x->d4b >= 4) {', 'if (ex == 0 && x->d4b >= 99) {')]),
    ('d4c: gate never', [('ex = x->d4b >= 3 ? swg_d4c(x, wrap) : 0;', 'ex = 0;')]),
    ('d4c: baseline type gap dropped for a clean 0x24 only at 15 and below too', [('if (x->d4b < 3 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {', 'if (x->d4b < 2 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {')]),
    ('d4c: baseline type gap dropped for a bad xor', [('if (x->d4b < 3 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {', 'if (x->d4b < 3 || type != SWD4B_MSG_D2H_MBDATA) {')]),
    ('d4c: baseline type gap dropped for any type', [('if (x->d4b < 3 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {', 'if (x->d4b < 3 || ex != 0) {')]),
    ('d4c: baseline type gap always dropped', [('if (x->d4b < 3 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {', 'if (0) {')]),
    ('d4c: flush gap raised again', [('\t/* host reads: flush_dcache64 = dc civac', '\tswl_gap("d4b", "flush_dcache64 used as invalidate (no ivac helper)", "low");\n\t/* host reads: flush_dcache64 = dc civac')]),
    # W-D4d review r1 F5: the two guards no other test reached
    ("W-D4d: payload buffer alignment not checked (level 18, ioctbuf = SWD_IOCTBUF - 128)", [(" || (x->in.ioctbuf & (SWD_D4D_ALIGN - 1u)) != 0u", "")]),
    ("W-D4d: swg_d4d_st does not refuse after the run stopped", [("\tif (x->stopped) {\n\t\treturn -SWG_E_STOPPED;\n\t}\n\tx->step = SWG_ST_N + 1u + SWG_D4_N + x->d4dn;", "\tx->step = SWG_ST_N + 1u + SWG_D4_N + x->d4dn;")]),
    # W-D4d review r2 F4: a stopped L18 must not count as done
    ("W-D4d: L18 done after a STOP", [("\tif (ex == 0 && !x->d4dstop) {\n\t\tswg_d4d_done(x, 18u,", "\tif (ex == 0) {\n\t\tswg_d4d_done(x, 18u,")]),
    # 22b: DS rounds after the L16 one (swg_ds_front / swg_ds_round); the ds_tests() cases of wlan_g2_host_test.c must FAIL on each
    ("ds: any mailbox data is answered", [("	if (mb != SWD4B_D2H_DS_ENTER_REQ) {\n		why =", "	if (0) {\n		why =")]),
    ("ds: the epoch/xor check of the 0x24 item dropped", [("	if (chk != 0) {\n		printf(\"SWEET WLAN: D4 DS STOP, no store: ring-2 item %u is type 0x24 but fails", "	if (0) {\n		printf(\"SWEET WLAN: D4 DS STOP, no store: ring-2 item %u is type 0x24 but fails")]),
    # T-c2e2 DS hold (research 124 s.4): the first extra round sends DS_ACK + wake and NO deassert; a DS_ENTER_REQ after it is a STOP
    ("ds hold: the hold STOP is missing (a request after the hold is answered)", [("	if (x->dshold) {\n		printf(\"SWEET WLAN GAP: D4 DS_ENTER_REQ", "	if (0) {\n		printf(\"SWEET WLAN GAP: D4 DS_ENTER_REQ")]),
    ("ds hold: the hold STOP gap is not counted", [("		swl_gap(\"d4ds\", \"DS_ENTER_REQ after the DS hold, stopped\", \"high\");", "")]),
    ("ds hold: the hold STOP text dropped", [("		printf(\"SWEET WLAN GAP: D4 DS_ENTER_REQ at ring-2 item %u while the DS hold is on", "		(void)(\"SWEET WLAN GAP: D4 DS_ENTER_REQ at ring-2 item %u while the DS hold is on")]),
    ("ds hold: dshold never set", [("		x->dshold = 1u;\n", "")]),
    ("ds hold: the deassert is sent in the hold round", [("	if (rn == 0u) {\n		ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);", "	if (1) {\n		ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);")]),
    ("ds hold: the D rows' d4mask bits are not set (C2 hits the order check)", [("		x->d4mask |= ((1ull << (SWG_D4C_D_DB + 1)) - 1ull) & ~((1ull << SWG_D4C_D_ITEM0) - 1ull);\n", "")]),
    ("ds hold: too many d4mask bits set (C2 counted done without a store)", [("		x->d4mask |= ((1ull << (SWG_D4C_D_DB + 1)) - 1ull)", "		x->d4mask |= ((1ull << (SWG_D4C_C2_DB + 1)) - 1ull)")]),
    ("ds hold: the d4mask bits skip the D item words only", [("		x->d4mask |= ((1ull << (SWG_D4C_D_DB + 1)) - 1ull) & ~((1ull << SWG_D4C_D_ITEM0) - 1ull);", "		x->d4mask |= ((1ull << (SWG_D4C_D_ITEM0 + SWG_D4_ITEMW)) - 1ull) & ~((1ull << SWG_D4C_D_ITEM0) - 1ull);")]),
    ("ds hold: the DEASSERT NOT sent line dropped", [("		printf(\"SWEET WLAN: D4 DS round %u: DEVICE_WAKE_DEASSERT NOT sent", "		(void)(\"SWEET WLAN: D4 DS round %u: DEVICE_WAKE_DEASSERT NOT sent")]),
    ("ds hold: C2 skipped too", [("		ex = swg_d4c_rows(x, SWG_D4C_C2_SLOT, SWG_D4C_C2_DB);   /* C2 (the read index) */", "		ex = 0;")]),
    ("ds hold: the hold round moves WR by 3", [("		x->d4wr = wr + 2u;\n		x->d4ds = (uint8_t)rn;", "		x->d4wr = wr + 3u;\n		x->d4ds = (uint8_t)rn;")]),
    ("ds hold: round 0 moves WR by 2", [("		x->d4wr = wr + 3u;\n		printf(\"SWEET WLAN: D4c DS handshake done", "		x->d4wr = wr + 2u;\n		printf(\"SWEET WLAN: D4c DS handshake done")]),
    ("ds: no ring-0 room check", [("	if (x->d4wr + 4u >= swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items) {", "	if (0) {")]),
    ("ds: ring-0 room one short", [("	if (x->d4wr + 4u >= swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items) {", "	if (x->d4wr + 3u >= swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items) {")]),
    ("ds: the round count is not kept", [("		x->d4ds = (uint8_t)rn;\n", "")]),
    ("ds: round start WR is 1", [("	x->d4c_wr = x->d4wr;\n	x->d4c_rd = x->d4rd;\n	printf(\"SWEET WLAN: D4 DS round", "	x->d4c_wr = 1u;\n	x->d4c_rd = x->d4rd;\n	printf(\"SWEET WLAN: D4 DS round")]),
    ("ds: round start RD is 0", [("	x->d4c_wr = x->d4wr;\n	x->d4c_rd = x->d4rd;\n	printf(\"SWEET WLAN: D4 DS round", "	x->d4c_wr = x->d4wr;\n	x->d4c_rd = 0u;\n	printf(\"SWEET WLAN: D4 DS round")]),
    ("ds: the round moves RD by 1", [("	x->d4rd = rd + 2u;\n", "	x->d4rd = rd + 1u;\n")]),
    ("ds: rows of the previous round not cleared (mask)", [("		x->d4mask &= ~SWG_D4C_ROWMASK;", "		x->d4mask |= 0;")]),
    ("ds: ack epoch of index 1", [("swd4b_item_mbdata(it, SWD4B_H2D_DS_ACK, swg_ep(wr));", "swd4b_item_mbdata(it, SWD4B_H2D_DS_ACK, swg_ep(1u));")]),
    ("ds: wake epoch of the ack", [("swd4b_item_mbdata(it, SWD4B_H2D_DS_WAKE, swg_ep(wr + 1u));", "swd4b_item_mbdata(it, SWD4B_H2D_DS_WAKE, swg_ep(wr));")]),
    ("ds: deassert epoch of the wake", [("swd4b_item_mbdata(it, SWD4B_H2D_DS_DEAS, swg_ep(wr + 2u));", "swd4b_item_mbdata(it, SWD4B_H2D_DS_DEAS, swg_ep(wr + 1u));")]),
    ("ds: note slot fixed", [("ns = (rd + 1u) % SWG_R2_DEPTH", "ns = 1u")]),
    ("ds: note write slot fixed", [("ne = (rd + 2u) % SWG_R2_DEPTH", "ne = 2u")]),
    ("ds: note epoch of index 1", [("ex = swd4b_d2h_check(in, swg_ep(rd + 1u), &type);", "ex = swd4b_d2h_check(in, swg_ep(1u), &type);")]),
    ("ds: a missing note is a soft gap in every round", [("		if (rn == 0u) {\n			swl_gap(\"d4c\", gb, \"high\");", "		if (1) {\n			swl_gap(\"d4c\", gb, \"high\");")]),
    ("ds: the DS hold gap not counted", [("		swl_gap(\"d4ds\", \"DS hold, no DEASSERT\", \"medium\");", "")]),
    ("ds: the answered line dropped", [("		printf(\"SWEET WLAN: D4 DS round %u answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items", "		(void)(\"SWEET WLAN: D4 DS round %u answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items")]),
    ("ds: the front item is not offered in the wait", [("			ex = swg_ds_front(x);\n			if (ex < 0) {\n				return ex;\n			}\n			if (ex == 1) {\n				continue;", "			ex = 0;\n			if (ex < 0) {\n				return ex;\n			}\n			if (ex == 1) {\n				continue;")]),
    ("ds: the start state does not offer the front item", [("		ex = swg_ds_front(x);\n		if (ex < 0) {\n			return ex;\n		}\n		while (ex == 1) {", "		ex = 0;\n		if (ex < 0) {\n			return ex;\n		}\n		while (ex == 1) {")]),
    ("ds: the start state expects ring-0 WR 4", [("	if (wr != x->d4wr) {\n		SWG_D4D_GAP(x, \"ring-0 write index 0x%x, expected %u\", wr, x->d4wr);", "	if (wr != 4u) {\n		SWG_D4D_GAP(x, \"ring-0 write index 0x%x, expected %u\", wr, x->d4wr);")]),
    ("ds: the start state expects ring-2 RD 2", [("	if (rd != x->d4rd % SWG_R2_DEPTH) {", "	if (rd != 2u) {")]),
    ("ds: a post does not advance the write count", [("	x->d4wr = nwr;\n	return ex;", "	return ex;")]),      # ring-0 wrap (e86af753): nwr = (idx + 1) % depth
    ("ds: a consume does not advance the read count", [("	x->d4rd = rd;\n	return ex;", "	return ex;")]),
    ("ds: a consume from a foreign read count", [("	if (rd_old != x->d4rd || rd < rd_old || rd - rd_old >= SWG_R2_DEPTH) {", "	if (0) {")]),
    ("ds: ring-2 depth 128", [("#define SWG_R2_DEPTH     SWD_R2_ITEMS ", "#define SWG_R2_DEPTH     128u ")]),      # 60acad75: depth from sweet_wlan_dma.h
    ("ds: far-behind write slot taken as a backlog", [("behind = pend >= SWG_R2_DEPTH / 2u;", "behind = 0;")]),
    ("ds: the read doorbell carries the absolute count", [("SWG_D4D_DB_RD | (rd % SWG_R2_DEPTH), 0, \"d4d db0 (d2h rd)\"", "SWG_D4D_DB_RD | rd, 0, \"d4d db0 (d2h rd)\"")]),
    ("ds: the read slot carries the absolute count", [("rd_old % SWG_R2_DEPTH, rd % SWG_R2_DEPTH, 1, \"d4d d2h_r slot 2\"", "rd_old, rd, 1, \"d4d d2h_r slot 2\"")]),
    ("ds: the wait ignores the pending count", [("		if (want != 0u ? pend >= want : pend != 0u) {", "		if (want != 0u ? v >= want : pend != 0u) {")]),
    # review 0174-w-d4e-scan24-hwwrite-r1 F5: a round that starts at an absolute ring-2 count of 62 / 63 / 126 (ds_tests "past the wrap" case): each `% SWG_R2_DEPTH` of the round has a mutant that drops it
    ("ds wrap: C1 slot value not modulo the depth", [("return (old & 0xFFFF0000u) | ((x->d4c_rd + 1u) % SWG_R2_DEPTH);", "return (old & 0xFFFF0000u) | (x->d4c_rd + 1u);")]),
    ("ds wrap: C2 slot value not modulo the depth", [("return (old & 0xFFFF0000u) | ((x->d4c_rd + 2u) % SWG_R2_DEPTH);", "return (old & 0xFFFF0000u) | (x->d4c_rd + 2u);")]),
    ("ds wrap: C1 doorbell not modulo the depth", [("return SWG_DB_RD | ((x->d4c_rd + 1u) % SWG_R2_DEPTH);", "return SWG_DB_RD | (x->d4c_rd + 1u);")]),
    ("ds wrap: C2 old value not modulo the depth", [("\t\treturn (x->d4c_rd + 1u) % SWG_R2_DEPTH;", "\t\treturn x->d4c_rd + 1u;")]),
    ("ds wrap: the exit-note slot not modulo the depth", [("ns = (rd + 1u) % SWG_R2_DEPTH,", "ns = (rd + 1u),")]),
    ("ds wrap: the front item read at the absolute count", [("(uint64_t)(k % SWG_R2_DEPTH) * swd_rings", "(uint64_t)(k) * swd_rings")]),
] + _d4c_g2() + E_G2_ROWS + E_G2_L24_ROWS


def g2_kill_test():
    """Compile shim/wlan_g2_host_test.c (a copy, so its quoted include picks up the temp-dir header) against the G2 header after each mutation and require it to fail.
    The mutants run on the GATE_MUT_JOBS pool through wlan_mutant_pool.run_set (set "g2", AP-368), each in its own private dir.
    Returns [(name, status, text)]: status FAIL (killed), SURVIVED, "pattern not found", "does not compile"; a base that does not build and pass gives one ("base", "base FAIL", output);
    None when GATE_MUT_ONLY skips the set."""
    if not wlan_mutant_pool.want("g2"):
        return None
    base = g2_header()
    wr_base = final_header()        # a substitution with a third element "wr" mutates sweet_wlan_wr.h instead of the G2 header (row W15, swl_rs, swl_wr)
    aux = (("sweet_wlan_wr.h", wr_base), ("sweet_wlan_chip.h", chip_header()), ("sweet_wlan_dma.h", new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h")), ("sweet_wlan_d4b.h", new_file(PATCH0174, "bsd/dev/sweet_wlan_d4b.h")))
    test_c = g2_test_text()

    def prep(d, hdr_text, wr_text):
        for fname, text in aux:
            with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
                fh.write(wr_text if fname == "sweet_wlan_wr.h" else text)
        with open(os.path.join(d, "wlan_g2_host_test.c"), "w", encoding="utf-8") as fh:
            fh.write(test_c)
        with open(os.path.join(d, "sweet_wlan_g2.h"), "w", encoding="utf-8") as fh:
            fh.write(hdr_text)
        shutil.copyfile(os.path.join(ROOT, "shim", "sweet_wlan_join.h"), os.path.join(d, "sweet_wlan_join.h"))      # T-6bef
        shutil.copyfile(os.path.join(ROOT, "shim", "sweet_wlan_l25_wl.h"), os.path.join(d, "sweet_wlan_l25_wl.h"))      # T-5173: the L25 store whitelist the host test includes
        shutil.copyfile(os.path.join(ROOT, "shim", "sweet_wlan_join_fw.h"), os.path.join(d, "sweet_wlan_join_fw.h"))      # T-4a7f: split out of sweet_wlan_join.h by T-4115
        return os.path.join(d, "wlan_g2_host_test.c"), os.path.join(d, "t")

    with tempfile.TemporaryDirectory() as work:
        d = tempfile.mkdtemp(dir=work)
        src, b = prep(d, base, wr_base)
        c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + d, "-o", b, src], capture_output=True, text=True, timeout=120)
        if c.returncode != 0:
            return [("base", "base FAIL", "does not compile: " + c.stderr[-600:])]
        r = run_bounded([b], d, 60)
        if r[0] != 0:
            return [("base", "base FAIL", (r[1] + r[2])[-600:])]

        def run_one(label, subs):
            t, w = base, wr_base
            for sub in subs:
                old, new = sub[0], sub[1]
                if len(sub) > 2:
                    if old not in w:
                        return "nomatch", old
                    w = w.replace(old, new)
                else:
                    if old not in t:
                        return "nomatch", old
                    t = t.replace(old, new)
            if t == base and w == wr_base:
                return "nomatch", "mutation changed nothing"
            md = tempfile.mkdtemp(dir=work)
            msrc, mb = prep(md, t, w)
            c = subprocess.run(["gcc", "-O1", "-w", "-DSWG_FAILFAST=1", "-I" + md, "-o", mb, msrc], capture_output=True, text=True, timeout=120)
            if c.returncode != 0:
                return "compile", c.stderr[-400:]
            try:
                r = run_bounded([mb], md, 60)
            except subprocess.TimeoutExpired as e:      # a mutant that drops a stop spins in the wait loop: the unmutated test needs 0.1 CPU s, so 60 CPU s is a deterministic kill (AP-105; same policy as build_and_run's HANG_MSG). A wall-backstop / stopped expiry is NOT a kill: re-raised, the pool reports it as an error
                if getattr(e, "kind", None) in ("cpu", "rlimit-cpu"):
                    return "fail", "killed by a hang (the unmutated test ends in 0.1 CPU s, this one used the 60 CPU s limit)"
                raise
            return ("fail" if r[0] != 0 else "pass"), (r[1] + r[2])[-300:]

        res = wlan_mutant_pool.run_set("g2", G2_MUTANTS, run_one, [base, "\0".join(t for _n, t in aux), test_c, "-O1 -w -DSWG_FAILFAST=1"])
    word = {"fail": "FAIL", "pass": "SURVIVED", "compile": "does not compile", "nomatch": "pattern not found"}
    return [(name, word.get(st, st), msg) for (name, _s), (st, msg) in zip(G2_MUTANTS, res)]


# ---- W-D4b (patch 0174, new file bsd/dev/sweet_wlan_d4b.h): the pure ring-0 event-buffer item builder and the D2H epoch/XOR checker. Not called by anything yet. Each builder field and each checker
#      rule has a mutant; the harness below pre-fills the output with 0xAA so a dropped zeroing is seen. Vector hand computed: msg 0x0D, if 0, flags 0x80, epoch 1, req 0x11223344 (LE 44 33 22 11),
#      len 0x2301 (01 23), PA 0x884DBE000 (lo 0x84DBE000 = 00 E0 DB 84, hi 8 = 08 00 00 00), all else 0.
D4B_HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sweet_wlan_d4b.h"
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); bad = 1; } } while (0)
static void wr32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
int main(void)
{
	static const uint8_t want[40] = { 0x0d, 0x00, 0x80, 0x01, 0x44, 0x33, 0x22, 0x11, 0x01, 0x23, 0, 0, 0, 0, 0, 0,
	    0x00, 0xe0, 0xdb, 0x84, 0x08, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 };
	uint8_t o[40], it[24], t = 0xEE;
	uint32_t w[6];
	int i;

	memset(o, 0xAA, sizeof o);
	swd4b_item_evbuf(o, 0x11223344u, 0x2301, 0x884DBE000ull, 1);
	CHK(memcmp(o, want, 40) == 0);
	w[0] = 0x0100000Eu; w[1] = 0x11223344u; w[2] = 0; w[3] = 0; w[4] = 0x55u; w[5] = w[0] ^ w[1] ^ w[2] ^ w[3] ^ w[4];
	for (i = 0; i < 6; i++) wr32(it + 4 * i, w[i]);
	CHK(swd4b_d2h_check(it, 1, &t) == 0 && t == 0x0E);
	CHK(swd4b_d2h_check(it, 1, NULL) == 0);
	CHK(swd4b_d2h_check(it, 2, &t) == SWD4B_E_EPOCH);
	CHK(SWD4B_E_EPOCH != 0 && SWD4B_E_XOR != 0 && SWD4B_E_EPOCH != SWD4B_E_XOR);
	for (i = 0; i < 6; i++) {          /* a flipped bit in any word (not in the epoch byte) is an XOR error */
		uint8_t b[24];
		memcpy(b, it, 24);
		b[4 * i + (i == 0 ? 1 : 0)] ^= 0x10;
		t = 0xEE;
		CHK(swd4b_d2h_check(b, 1, &t) == SWD4B_E_XOR);
		CHK(t == b[0]);
	}
	t = 0xEE;
	memcpy(o, it, 24);                 /* wrong epoch AND bad xor: still an error, type still reported */
	o[3] = 9;
	CHK(swd4b_d2h_check(o, 1, &t) != 0 && t == 0x0E);
	{       /* W-D4c K1: h2d_mailbox_data_t (bcmmsgbuf.h:626-630), type 0x23, flags 0x80, epoch, request_id 0, mb_data at +8 (dhd_msgbuf.c:12395-12415); constants from bcmmsgbuf.h:259, bcmpcie.h:485, 511 */
		static const uint8_t k1[40] = { 0x23, 0x00, 0x80, 0x02, 0, 0, 0, 0, 0x02, 0, 0, 0 };
		static const uint8_t k1b[40] = { 0x23, 0x00, 0x80, 0x07, 0, 0, 0, 0, 0x44, 0x33, 0x22, 0x11 };

		memset(o, 0xAA, sizeof o);
		swd4b_item_mbdata(o, 2u, 2);
		CHK(memcmp(o, k1, 40) == 0);
		memset(o, 0xAA, sizeof o);
		swd4b_item_mbdata(o, 0x11223344u, 7);
		CHK(memcmp(o, k1b, 40) == 0);
		CHK(SWD4B_MSG_H2D_MBDATA == 0x23u && SWD4B_MSG_D2H_MBDATA == 0x24u && SWD4B_D2H_DS_ENTER_REQ == 0x2u && SWD4B_H2D_DS_ACK == 0x2u && SWD4B_D2H_DS_EXIT_NOTE == 0x4u && SWD4B_H2D_DS_WAKE == 0x40u && SWD4B_H2D_DS_DEAS == 0x20u);
	}
	{       /* W-D4d: the ioctl-response buffer post (type 0x0B) and the ioctl request (type 0x09), vectors hand computed from docs/research/W-D4d-ioctl-get-version.md s.2.1/2.2:
		 * post: msg 0x0B, if 0, flags 0x80, epoch 5, request_id 2, host_buf_len 0x2000 (00 20 at +8), PA 0x884EAF000 (lo 0x84EAF000 = 00 F0 EA 84, hi 8)
		 * request: msg 0x09, flags 0x80, epoch 8, request_id 0xFFFE (FE FF 00 00), cmd 262 (06 01 00 00), trans_id 0x0102 (02 01), input_buf_len 4 (04 00), output_buf_len 1023 (FF 03), PA 0x884EB1000 (00 10 EB 84, hi 8) at +24 / +28 */
		static const uint8_t rp[40] = { 0x0b, 0x00, 0x80, 0x05, 0x02, 0, 0, 0, 0x00, 0x20, 0, 0, 0, 0, 0, 0, 0x00, 0xf0, 0xea, 0x84, 0x08, 0, 0, 0 };
		static const uint8_t rq[40] = { 0x09, 0x00, 0x80, 0x08, 0xfe, 0xff, 0, 0, 0x06, 0x01, 0, 0, 0x02, 0x01, 0x04, 0x00, 0xff, 0x03, 0, 0, 0, 0, 0, 0,
		    0x00, 0x10, 0xeb, 0x84, 0x08, 0, 0, 0 };
		struct swd4b_ioctl_d2h f;
		uint8_t *b;
		int r;

		memset(o, 0xAA, sizeof o);
		swd4b_item_respbuf(o, 2u, 0x2000, 0x884EAF000ull, 5);
		CHK(memcmp(o, rp, 40) == 0);
		memset(o, 0xAA, sizeof o);
		swd4b_item_ioctl(o, 262u, 0x0102, 4, 1023, 0x884EB1000ull, 8);
		CHK(memcmp(o, rq, 40) == 0);
		CHK(SWD4B_MSG_IOCTL_REQ == 0x09u && SWD4B_MSG_IOCTL_ACK == 0x0Au && SWD4B_MSG_RESPBUF == 0x0Bu && SWD4B_MSG_IOCTL_CMPLT == 0x0Cu && SWD4B_IOCTL_REQID == 0xFFFEu && SWD4B_WLC_GET_VERSION == 1u && SWD4B_WLC_GET_VAR == 262u);
		/* the parser, on exact-size heap copies (AP-323): an ack (cmd at +12, +16 is reserved) and a completion (resp_len +12, trans_id +14, cmd +16), then other types */
		{
			static const uint8_t ack[24] = { 0x0a, 0x00, 0x80, 0x05, 0xfe, 0xff, 0, 0, 0xff, 0xff, 0x02, 0x00, 0x06, 0x01, 0, 0, 0x44, 0x33, 0x22, 0x11, 0x99, 0x88, 0x77, 0x66 };
			static const uint8_t cm[24] = { 0x0c, 0x00, 0x80, 0x06, 0x03, 0, 0, 0, 0x03, 0x00, 0x02, 0x00, 0x34, 0x12, 0x78, 0x56, 0xf0, 0xde, 0xbc, 0x9a, 0x99, 0x88, 0x77, 0x66 };
			static const uint8_t mb[24] = { 0x24, 0x00, 0x80, 0x07, 0x09, 0, 0, 0, 0x01, 0x00, 0x02, 0x00, 0x34, 0x12, 0x78, 0x56, 0xf0, 0xde, 0xbc, 0x9a, 0, 0, 0, 0 };

			b = malloc(24);
			memcpy(b, ack, 24);
			memset(&f, 0x55, sizeof f);
			r = swd4b_ioctl_d2h(b, &f);
			CHK(r == 0 && f.type == 0x0a && f.epoch == 5 && f.request_id == 0xFFFEu && f.status == -1 && f.ring_id == 2 && f.cmd == 262u && f.resp_len == 0 && f.trans_id == 0);
			free(b);
			b = malloc(24);
			memcpy(b, cm, 24);
			memset(&f, 0x55, sizeof f);
			r = swd4b_ioctl_d2h(b, &f);
			CHK(r == 0 && f.type == 0x0c && f.epoch == 6 && f.request_id == 3u && f.status == 3 && f.ring_id == 2 && f.cmd == 0x9abcdef0u && f.resp_len == 0x1234 && f.trans_id == 0x5678);
			free(b);
			b = malloc(24);
			memcpy(b, mb, 24);
			memset(&f, 0x55, sizeof f);
			r = swd4b_ioctl_d2h(b, &f);
			CHK(r == -1 && f.type == 0x24 && f.epoch == 7 && f.request_id == 9u && f.status == 0 && f.ring_id == 0 && f.cmd == 0 && f.resp_len == 0 && f.trans_id == 0);
			free(b);
			b = malloc(24);
			memcpy(b, cm, 24);
			b[0] = 0x0d;                       /* the neighbours of 0x0a / 0x0c are no ioctl item */
			CHK(swd4b_ioctl_d2h(b, &f) == -1);
			b[0] = 0x0b;
			CHK(swd4b_ioctl_d2h(b, &f) == -1);
			b[0] = 0x09;
			CHK(swd4b_ioctl_d2h(b, &f) == -1);
			free(b);
		}
	}
	printf(bad ? "D4B FAIL\n" : "D4B OK\n");
	return bad;
}
"""
# mutant-block: wlan-d4b
D4B_MUTANTS = [
    ("d4b builder: msg_type not 0x0D", [("#define SWD4B_MSG_EVBUF     0x0Du", "#define SWD4B_MSG_EVBUF     0x0Bu")]),
    ("d4b builder: flags not 0x80", [("#define SWD4B_PHASE_FIRST   0x80u", "#define SWD4B_PHASE_FIRST   0x00u")]),
    ("d4b builder: flags at the wrong offset", [("out[2] = SWD4B_PHASE_FIRST;", "out[1] = SWD4B_PHASE_FIRST;")]),
    ("d4b builder: epoch not stored", [("out[3] = epoch;", "out[3] = 0;")]),
    ("d4b builder: epoch at the wrong offset", [("out[3] = epoch;", "out[1] = epoch;")]),
    ("d4b builder: request_id at +0", [("swd4b_put32(out + 4, request_id);", "swd4b_put32(out + 0, request_id);")]),
    ("d4b builder: request_id at +8", [("swd4b_put32(out + 4, request_id);", "swd4b_put32(out + 8, request_id);")]),
    ("d4b builder: buf_len low byte lost", [("out[8] = (uint8_t)buf_len;", "out[8] = 0;")]),
    ("d4b builder: buf_len high byte lost", [("out[9] = (uint8_t)(buf_len >> 8);", "out[9] = 0;")]),
    ("d4b builder: buf_len big endian", [("out[8] = (uint8_t)buf_len;\n\tout[9] = (uint8_t)(buf_len >> 8);", "out[9] = (uint8_t)buf_len;\n\tout[8] = (uint8_t)(buf_len >> 8);")]),
    ("d4b builder: PA lo at +20", [("swd4b_put32(out + 16, (uint32_t)buf_pa);", "swd4b_put32(out + 20, (uint32_t)buf_pa);")]),
    ("d4b builder: PA hi dropped", [("swd4b_put32(out + 20, (uint32_t)(buf_pa >> 32));", "swd4b_put32(out + 20, 0);")]),
    ("d4b builder: PA hi at +24", [("swd4b_put32(out + 20, (uint32_t)(buf_pa >> 32));", "swd4b_put32(out + 24, (uint32_t)(buf_pa >> 32));")]),
    ("d4b builder: PA hi is lo again", [("swd4b_put32(out + 20, (uint32_t)(buf_pa >> 32));", "swd4b_put32(out + 20, (uint32_t)buf_pa);")]),
    ("d4b builder: put32 big endian", [("p[0] = (uint8_t)v;\n\tp[1] = (uint8_t)(v >> 8);\n\tp[2] = (uint8_t)(v >> 16);\n\tp[3] = (uint8_t)(v >> 24);", "p[3] = (uint8_t)v;\n\tp[2] = (uint8_t)(v >> 8);\n\tp[1] = (uint8_t)(v >> 16);\n\tp[0] = (uint8_t)(v >> 24);")]),
    ("d4b builder: put32 loses byte 3", [("p[3] = (uint8_t)(v >> 24);", "p[3] = 0;")]),
    ("d4b builder: other bytes not zeroed", [("for (i = 0; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;\n\t}", "(void)i;")]),
    ("d4b builder: last 16 bytes not zeroed", [("i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;", "i < 24u; i++) {\n\t\tout[i] = 0;")]),
    ("d4b builder: if_id not zeroed", [("for (i = 0; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;", "for (i = 2; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;")]),
    ("d4b checker: epoch not compared", [("if (in[3] != want_epoch) {", "if (0) {")]),
    ("d4b checker: epoch compared at +2", [("if (in[3] != want_epoch) {", "if (in[2] != want_epoch) {")]),
    ("d4b checker: epoch code reused for xor", [("#define SWD4B_E_XOR         2 ", "#define SWD4B_E_XOR         1 ")]),
    ("d4b checker: xor not checked", [("if (x != 0u) {", "if (0) {")]),
    ("d4b checker: xor skips the last word", [("for (i = 0; i < SWD4B_D2H_LEN; i += 4) {", "for (i = 0; i + 4 < SWD4B_D2H_LEN; i += 4) {")]),
    ("d4b checker: xor skips the first word", [("for (i = 0; i < SWD4B_D2H_LEN; i += 4) {", "for (i = 4; i < SWD4B_D2H_LEN; i += 4) {")]),
    ("d4b checker: xor accumulates with OR", [("x ^= (uint32_t)", "x |= (uint32_t)")]),
    ("d4b checker: xor accepts any nonzero (inverted)", [("if (x != 0u) {", "if (x == 0u) {")]),
    ("d4b checker: type_out not set", [("*type_out = in[0];", "*type_out = 0;")]),
    ("d4b checker: type_out set only on success", [("\tif (type_out != NULL) {\n\t\t*type_out = in[0];\n\t}\n\tif (in[3] != want_epoch) {", "\tif (in[3] != want_epoch) {")]),
    ("d4b checker: NULL type_out dereferenced", [("if (type_out != NULL) {", "if (1) {")]),
    ('d4c builder: msg_type not 0x23', [('#define SWD4B_MSG_H2D_MBDATA 0x23u', '#define SWD4B_MSG_H2D_MBDATA 0x22u')]),
    ('d4c builder: flags not stored', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);')]),
    ('d4c builder: flags at the wrong offset', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[1] = SWD4B_PHASE_FIRST;')]),
    ('d4c builder: epoch not stored', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = 0;\n\tswd4b_put32(out + 8, mb_data);')]),
    ('d4c builder: epoch at the wrong offset', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[1] = epoch;\n\tswd4b_put32(out + 8, mb_data);')]),
    ('d4c builder: mb_data not stored', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, 0);')]),
    ('d4c builder: mb_data at +4', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 4, mb_data);')]),
    ('d4c builder: mb_data at +12', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 12, mb_data);')]),
    ('d4c builder: request_id not 0', [('out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);', 'out[0] = SWD4B_MSG_H2D_MBDATA;\n\tout[2] = SWD4B_PHASE_FIRST;\n\tout[3] = epoch;\n\tswd4b_put32(out + 8, mb_data);\n\tout[4] = 1;')]),
    ('d4c builder: other bytes not zeroed', [('for (i = 0; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;\n\t}\n\tout[0] = SWD4B_MSG_H2D_MBDATA;', '(void)i;\n\tout[0] = SWD4B_MSG_H2D_MBDATA;')]),
    ('d4c builder: last bytes not zeroed', [('for (i = 0; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;\n\t}\n\tout[0] = SWD4B_MSG_H2D_MBDATA;', 'for (i = 0; i < 24u; i++) {\n\t\tout[i] = 0;\n\t}\n\tout[0] = SWD4B_MSG_H2D_MBDATA;')]),
    ('d4c builder: if_id not zeroed', [('for (i = 0; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;\n\t}\n\tout[0] = SWD4B_MSG_H2D_MBDATA;', 'for (i = 2; i < SWD4B_ITEM_LEN; i++) {\n\t\tout[i] = 0;\n\t}\n\tout[0] = SWD4B_MSG_H2D_MBDATA;')]),
    ('d4c: D2H mailbox type not 0x24', [('#define SWD4B_MSG_D2H_MBDATA 0x24u', '#define SWD4B_MSG_D2H_MBDATA 0x25u')]),
    ('d4c: DS_ENTER_REQ not 0x2', [('#define SWD4B_D2H_DS_ENTER_REQ 0x2u', '#define SWD4B_D2H_DS_ENTER_REQ 0x4u')]),
    ('d4c: DS_ACK not 0x2', [('#define SWD4B_H2D_DS_ACK    0x2u', '#define SWD4B_H2D_DS_ACK    0x4u')]),
    ('d4c: DS_EXIT_NOTE not 0x4', [('#define SWD4B_D2H_DS_EXIT_NOTE 0x4u', '#define SWD4B_D2H_DS_EXIT_NOTE 0x2u')]),
    ('d4c: DEVICE_WAKE_ASSERT not 0x40', [('#define SWD4B_H2D_DS_WAKE   0x40u', '#define SWD4B_H2D_DS_WAKE   0x20u')]),
    ('d4c: DEVICE_WAKE_DEASSERT not 0x20', [('#define SWD4B_H2D_DS_DEAS   0x20u', '#define SWD4B_H2D_DS_DEAS   0x40u')]),
]


def d4b_text():
    return new_file(PATCH0174, "bsd/dev/sweet_wlan_d4b.h")


def d4b_kill_test():
    """Build the harness against the d4b header and each mutant on the GATE_MUT_JOBS pool (set "d4b", run_set, AP-368);
    [(name, status, text)] like g2_kill_test (a base that fails gives one ("base", "base FAIL", ...)); None when GATE_MUT_ONLY skips the set."""
    if not wlan_mutant_pool.want("d4b"):
        return None
    base = d4b_text()

    def prep(d, text):
        src, hdr = os.path.join(d, "h.c"), os.path.join(d, "sweet_wlan_d4b.h")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(D4B_HARNESS)
        with open(hdr, "w", encoding="utf-8") as fh:
            fh.write(text)
        return src, os.path.join(d, "t")

    with tempfile.TemporaryDirectory() as work:
        d = tempfile.mkdtemp(dir=work)
        src, b = prep(d, base)
        c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + d, "-o", b, src], capture_output=True, text=True, timeout=120)
        if c.returncode != 0:
            return [("base", "base FAIL", "does not compile: " + c.stderr[-600:])]
        r = run_bounded([b], d, 60)
        if r[0] != 0:
            return [("base", "base FAIL", (r[1] + r[2])[-600:])]

        def run_one(label, subs):
            t = base
            for old, new in subs:
                if old not in t:
                    return "nomatch", old
                t = t.replace(old, new)
            if t == base:
                return "nomatch", "mutation changed nothing"
            md = tempfile.mkdtemp(dir=work)
            msrc, mb = prep(md, t)
            c = subprocess.run(["gcc", "-O1", "-w", "-I" + md, "-o", mb, msrc], capture_output=True, text=True, timeout=120)
            if c.returncode != 0:
                return "compile", c.stderr[-400:]
            r = run_bounded([mb], md, 60)
            return ("fail" if r[0] != 0 else "pass"), (r[1] + r[2])[-300:]

        res = wlan_mutant_pool.run_set("d4b", D4B_MUTANTS, run_one, [base, D4B_HARNESS, "-O1 -w"])
    word = {"fail": "FAIL", "pass": "SURVIVED", "compile": "does not compile", "nomatch": "pattern not found"}
    return [(name, word.get(st, st), msg) for (name, _s), (st, msg) in zip(D4B_MUTANTS, res)]


# W-D4b D-10: G2 must not arm when the G1 block has a PA but no VA. Textual audit of the glue after the patch chain (the arming code is not host-runnable): the g2va == 0 refusal sits in the arming
# chain, resets g2ok, has a swl_gap and the "G2 not armed" printf; sweet_wlan_g1 takes va_out, NULL-safe, set beside pa_out; every caller passes three arguments.
def g2va_audit(glue):
    """[] if the D-10 wiring is intact, else a list of what is missing."""
    bad = []
    m = re.search(r"\} else if \(hw\.g2pa != 0 && hw\.g2va == 0\) \{(.*?)\n\t\t\t\t\} else", glue, re.S)
    if not m:
        return ["no 'g2pa != 0 && g2va == 0' refusal in the arming chain"]
    if "hw.g2ok = 0;" not in m.group(1):
        bad.append("refusal does not clear g2ok")
    if "swl_gap(" not in m.group(1):
        bad.append("refusal has no swl_gap")
    if 'printf("SWEET WLAN: G2 not armed:' not in m.group(1):
        bad.append("refusal has no 'SWEET WLAN: G2 not armed:' printf")
    if glue.find("g2va == 0") > glue.find("sw_g2_alias_ok(hw.g2pa"):
        bad.append("refusal is after the alias check (the armed print would come first)")
    if not re.search(r"sweet_wlan_g1\(struct swd_plan \*out, uint64_t \*pa_out, vm_offset_t \*va_out\)", glue):
        bad.append("sweet_wlan_g1 has no va_out parameter")
    if not re.search(r"\*pa_out = pa;\n\t\tif \(va_out != NULL\) \{\n\t\t\t\*va_out = va;\n\t\t\}", glue):
        bad.append("va_out is not set NULL-safe beside pa_out")
    if "vm_offset_t g2va;" not in glue:
        bad.append("sw_hw has no g2va")
    calls = re.findall(r"sweet_wlan_g1\((?!struct)([^)]*)\)", glue)
    if not calls or any(len(c.split(",")) != 3 for c in calls):
        bad.append("a sweet_wlan_g1 caller does not pass three arguments: %r" % (calls,))
    if "&hw.g2va" not in glue:
        bad.append("arming does not pass &hw.g2va")
    return bad


# mutant-block: wlan-g2va
G2VA_MUTANTS = [
    ("g2va: refusal removed", [("} else if (hw.g2pa != 0 && hw.g2va == 0) {", "} else if (0) {")]),
    ("g2va: g2ok not cleared", [("\t\t\t\t\thw.g2ok = 0;\n\t\t\t\t\tswl_gap(\"g2\", \"not armed: G1 block has no VA\"", "\t\t\t\t\tswl_gap(\"g2\", \"not armed: G1 block has no VA\"")]),
    ("g2va: no gap line", [("swl_gap(\"g2\", \"not armed: G1 block has no VA\", \"high\");", "")]),
    ("g2va: va_out set unguarded", [("if (va_out != NULL) {\n\t\t\t*va_out = va;\n\t\t}", "*va_out = va;")]),
    ("g2va: va_out never set", [("*va_out = va;", "(void)va;")]),
    ("g2va: caller drops the VA", [("&hw.g2pa, &hw.g2va)", "&hw.g2pa, NULL)")]),
]


def g2host_audit(glue):
    """[] if the W-D4b host-block bus cases flush the line: before the read, after the store, physical address g2pa + offset, 4 bytes, phys TRUE; and the hook hands G2 the block geometry."""
    bad = []
    g = strip_comments(glue)
    rd, wr = body(g, "sw_g2_rd"), body(g, "sw_g2_wr")
    fl = "flush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);"
    m = re.search(r"case SWG_SP_HOST:(.*?)\n\t}", rd, re.S)
    if not m or fl not in m.group(1) or "sw3_rd(h, SW3_HOST, 0, a, v)" not in m.group(1) or m.group(1).find(fl) > m.group(1).find("sw3_rd("):
        bad.append("sw_g2_rd: the host case does not flush the line before the read")
    m = re.search(r"case SWG_SP_HOST:(.*?)case SWG_SP_DATA", wr, re.S)
    if not m or fl not in m.group(1) or "sw3_wr(h, SW3_HOST, 0, a, v)" not in m.group(1) or m.group(1).find(fl) < m.group(1).find("sw3_wr(") or "return rc;" not in m.group(1):
        bad.append("sw_g2_wr: the host case does not flush the line after the store and return its result")
    fld = "flush_dcache64((addr64_t)(h->dpa + a), 4, TRUE);"      # T-2543: the DATA case (T-d84f) flushes the DATA block line (PA dpa + offset), after the store, and returns the store result
    m = re.search(r"case SWG_SP_DATA:(.*?)case SWG_SP_WIN2", wr, re.S)
    if not m or fld not in m.group(1) or "sw3_wr(h, SW3_DATA, 0, a, v)" not in m.group(1) or m.group(1).find(fld) < m.group(1).find("sw3_wr(") or "return rc;" not in m.group(1) or "sw3_ptr(h, SW3_DATA, a) == NULL" not in m.group(1):
        bad.append("sw_g2_wr: the DATA case does not check the block, flush the line after the store and return its result")
    hk = body(g, "sw_g2_hook")
    for want in ("g.in.blk = h->g2pa;", "g.in.blk_len = h->g2plan.total;", "g.in.evbuf = h->g2pa + h->g2plan.b[SWD_EVBUF].off;"):
        if want not in hk:
            bad.append("sw_g2_hook: " + want + " missing")
    return bad


# mutant-block: wlan-g2host
G2HOST_MUTANTS = [
    ("host rd: no flush", [("\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn sw3_rd(h, SW3_HOST", "\t\treturn sw3_rd(h, SW3_HOST")]),
    ("host rd: flush after the read", [("\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn sw3_rd(h, SW3_HOST, 0, a, v);", "\t\trc = sw3_rd(h, SW3_HOST, 0, a, v);\n\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn sw3_rd(h, SW3_HOST, 0, a, v);")]),
    ("host rd: virtual address", [("\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn sw3_rd", "\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, FALSE);\n\t\treturn sw3_rd")]),
    ("host rd: wrong address", [("\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn sw3_rd", "\t\tflush_dcache64((addr64_t)(h->g2pa), 4, TRUE);\n\t\treturn sw3_rd")]),
    ("host wr: no flush", [("\t\trc = sw3_wr(h, SW3_HOST, 0, a, v);\n\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n", "\t\trc = sw3_wr(h, SW3_HOST, 0, a, v);\n")]),
    ("host wr: flush before the store", [("\t\trc = sw3_wr(h, SW3_HOST, 0, a, v);\n\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n", "\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\trc = sw3_wr(h, SW3_HOST, 0, a, v);\n")]),
    ("host wr: result dropped", [("\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn rc;\n\tcase SWG_SP_DATA", "\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\treturn 0;\n\tcase SWG_SP_DATA")]),
    ("data wr: result dropped", [("\t\tflush_dcache64((addr64_t)(h->dpa + a), 4, TRUE);\n\t\treturn rc;\n\tcase SWG_SP_WIN2", "\t\tflush_dcache64((addr64_t)(h->dpa + a), 4, TRUE);\n\t\treturn 0;\n\tcase SWG_SP_WIN2")]),
    ("data wr: no flush", [("\t\tflush_dcache64((addr64_t)(h->dpa + a), 4, TRUE);\n", "")]),
    ("data wr: flush at the G1 block PA", [("flush_dcache64((addr64_t)(h->dpa + a), 4, TRUE);", "flush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);")]),
    ("data wr: block not checked", [("if (sw3_ptr(h, SW3_DATA, a) == NULL) {", "if (0) {")]),
    ("hook: block not handed over", [("\t\tg.in.blk = h->g2pa;\n", "")]),
    ("hook: block length not handed over", [("\t\tg.in.blk_len = h->g2plan.total;\n", "")]),
    ("hook: event buffer not handed over", [("\t\tg.in.evbuf = h->g2pa + h->g2plan.b[SWD_EVBUF].off;\n", "")]),
]


# Review 0174-w-d4b-hwwrite-r1 F5/F6/F7 + SPEC-WIFI D-12 (W15): textual audit of the glue after the patch chain, for the parts the sw3_wr harness cannot see (a cache op before a bound check, a READ that the
# fake memory cannot count, the level wiring of stage 3) and as the independent pin of the sw3_wr cfg 0x88 branch. Mutants below must each make it report something.
def g2fix_audit(glue, hdr):
    """[] if the F5 host-block bounds, the F6 no-needless-read rule, the W15 level wiring and the cfg 0x88 branch are intact, else what is missing."""
    bad = []
    g = strip_comments(glue)
    if "h->g2va != 0 && h->g2plan.total >= 4u && off <= h->g2plan.total - 4u ?" not in body(g, "sw3_ptr"):
        bad.append("sw3_ptr: the host block word is not bounded to the whole 4 bytes (F5)")
    for fn, tail, flush in (("sw_g2_rd", r"\n\t}", "flush_dcache64("), ("sw_g2_wr", r"case SWG_SP_WIN2", "flush_dcache64(")):
        m = re.search(r"case SWG_SP_HOST:(.*?)" + tail, body(g, fn), re.S)
        t = m.group(1) if m else ""
        chk = t.find("if (sw3_ptr(h, SW3_HOST, a) == NULL) {")
        ret = t.find("return -1;", chk) if chk >= 0 else -1
        fl = t.find(flush)
        if not (0 <= chk < ret < fl) or "swl_gap(" not in t[chk:ret]:
            bad.append("%s: the host case does not refuse an unchecked address (with a gap) before the cache op (F5)" % fn)
    ok = body(g, "sw3_g2_ok")
    if ok.count("old = *p;") != 1 or len(re.findall(r"=\s*\*\s*p\b", ok)) != 1 or "*sw3_ptr(" in ok or "uint32_t old = 0, vv, ev, eold," not in ok or "if (!have && (w16 || e->omask != 0 || e->vmask != 0xFFFFFFFFu || e->kind == SW_G2K_R_WR || e->kind == SW_G2K_E_WR)) {\n\t\t\told = *p;\n\t\t\thave = 1;\n\t\t}" not in ok:
        bad.append("sw3_g2_ok: the old value is read other than once, after the entry match, and only for an entry that needs it (F6)")
    _mt = ok.find("if (e->space != space || e->win != win || e->w16 != (w16 ? 1 : 0) || (e->once && e->done) || (e->need >= 0 && !h->g2l[e->need].done) || (e->l24 && (!h->g2rd || h->g2ev <= h->g2ev0)) || !sw_g2_exp(h, e, off, &ev, &eold)) {")
    if _mt < 0 or ok.find("old = *p;") < _mt:      # T-23da: a missing match line is a failure, not a skipped check
        bad.append("sw3_g2_ok: the entry match line (with the T-23da L24 gate) is missing, or the old value is read before the entry is matched (F6)")
    if sorted(a.strip() for a in re.findall(r"\bjc\s*=(?!=)\s*([^;]*);", g)) != ["NULL", "h->jc", "sw_jc"]:       # T-48a0: the hand-off g.jc = h->jc, and sweet_wlan_creds' h->jc = NULL / = sw_jc (nothing else sets join commands)
        bad.append("the glue assigns the join commands other than g.jc = h->jc, h->jc = NULL, h->jc = sw_jc (T-48a0: the L24 gate is the interim ring-2 witness of review T-4a7f r2 F1; any other source of join commands needs a new review)")
    if "h->g2rd = 1;" not in g or "h->g2ev0 = h->g2ev;" not in g or "h->g2esc = esc;" not in g or "h->g2esc = -1;" not in g or "h->g2l[g2i].kind == SW_G2K_R_RD && h->g2esc >= 0 && h->g2l[h->g2esc].done && !h->g2rd" not in g:
        bad.append("the interim L24 ring-2 witness (T-48a0, review T-4a7f r2 F1: g2rd set at the first R_RD commit after the escan doorbell, g2ev0 = g2ev there) is missing or changed")
    cap = re.search(r"#define SW_G2_MAX\s+(\d+)u", g)
    if not cap or int(cap.group(1)) < 1631:
        bad.append("SW_G2_MAX is below the 1631 entries of sweetwlan=24 at the 12-chunk CLM maximum (the 1273 of level 23 - 16 x 12 re-posts under the event cap 48 + 12 x 25 chunk entries + 2 x 24 status/country + 10 payload words; the list build would refuse at the arm)")
    init, st3 = body(g, "sweet_wlan_init"), body(g, "sweet_wlan_stage3")
    if "if (level > 26 || SecureDTLookupEntry(" not in init:
        bad.append("sweet_wlan_init: the highest accepted level is not 26")
    if "hw.w15 = hw.g2ok && level >= 15;" not in init or "sweet_wlan_stage3(&hw, level > 10 ? 10 : (int)level);" not in init:
        bad.append("sweet_wlan_init: hw.w15 is not set from level >= 15 with G2 armed, or stage 3 is not capped at 10")
    if st3.find("hw->w15 = 0;") < st3.find("swl_stage3(&x)") or st3.find("swl_stage3(&x)") < 0 or st3.count("hw->w15 = 0;") != 1:
        bad.append("sweet_wlan_stage3: hw->w15 is not cleared after swl_stage3 (the W15 allowance outlives stage 3)")
    if "x.w15 = hw->w15;" not in st3:
        bad.append("sweet_wlan_stage3: x.w15 is not carried from hw->w15")
    w = body(g, "sw3_wr")
    br = "if ((h->c88 && v == SWW_CFG88) || (h->w15 && !h->c88 && h->rel == 0 && v == (SWW_CFG88 | SW_G2_C88_BITS) && h->cfg[0x88 / 4] == SWW_CFG88)) {\n\t\t\tgoto put;\n\t\t}"
    a = w.find("if (space == SWW_CFG && off == 0x88u) {")
    if a < 0 or br not in w[a:] or not w.find("if (h->g2) {") < a < w.find("if (space != SWW_CFG && space != SWW_ATU"):
        bad.append("sw3_wr: the cfg 0x88 branch (W15 once with the old word pinned before the release, or its restore after the store) is not exactly that, after the G2 branch and before the space check")
    if "swl_gap(\"g2\", \"glue: cfg 0x88 store outside the W15 state refused\", \"high\");" not in w[a:]:
        bad.append("sw3_wr: the cfg 0x88 refusal has no gap")
    sg = body(hdr, "swl_stage3")
    i14, i15, i16 = sg.find("swl_w14(x)"), sg.find("swl_w15(x)"), sg.find("swl_w16(x)")
    if not 0 <= i14 < i15 < i16:
        bad.append("swl_stage3: W15 is not between W14 and W16")
    return bad


# T-8ede: one mutant per new rule of T-4a7f (patch 0174: event re-posts E_ITEM / E_WR, g2ev and the SH shift, sw_g2_d4j: rfifo, J_TRANS / J_RE0, the mask bits, VER old mask, the wsec_key zeroing, the join-table gate).
# Each is applied to every glue text it occurs in; killed = the glue sweep (L21-L24 replay + negatives) OR the G2L list harness (want() rows of d4b 10 / 11) FAILS. A compile error is a broken mutant, not a kill.
# EQUIVALENT, left out with their clause: the VER refusal in sw_g2_j_pay and its key / non-key printf (T-23da F4) (call 0 is the fixed 45 B event_msgs_ext, whose words 1 and 3 never equal the escan version words, and it clears both bits,
# so the branch cannot run); the clamp 24 (G2FIX "T-4a7f: highest level 22/23/25" rows). Not equivalent, so in the table (review T-4a7f r1 F3): the E_WR cap alone (the memory-preset bound case kills it) and the njc bounds
# (G2L rows njc 0 with a table, five commands ending in "join", nine commands ending in "join": each wants the d4b 10 list).
# mutant-block: wlan-g2e
T8EDE_MUTANTS = [
    ("E_ITEM: no prefix check of the item", [("if (q == NULL || (*q != 0u && !sw_g2_evw_ok(w, k, *q))) {", "if (q == NULL) {")]),
    ("E_ITEM: prefix check of word 0 only", [("for (k = 0; k < 10u; k++) {    /* the item at the slot is a prefix", "for (k = 0; k < 1u; k++) {    /* the item at the slot is a prefix")]),
    ("E_ITEM: index 0, not the slot", [("i = *sl & 0xFFFFu;\n\t\teo += 40u * i;\n\t\tsw_g2_e_evexp(h, w, i);", "i = 0u;\n\t\teo += 40u * i;\n\t\tsw_g2_e_evexp(h, w, i);")]),
    ("E_ITEM: no g2ev cap", [("|| h->g2ev >= SW_G2_E_CAP(h) || e->off < h->g2plan.b[SWD_H2D_CTRL].off", "|| e->off < h->g2plan.b[SWD_H2D_CTRL].off")]),
    ("E_ITEM: no ring-depth bound on the slot", [("if (sl == NULL || (*sl & 0xFFFFu) + 1u >= SW_G2_R0_ITEMS || h->g2ev >= SW_G2_E_CAP(h)", "if (sl == NULL || h->g2ev >= SW_G2_E_CAP(h)")]),
    ("E_ITEM: item 127 allowed (slot < 128, T-23da F2)", [("if (sl == NULL || (*sl & 0xFFFFu) + 1u >= SW_G2_R0_ITEMS || h->g2ev >= SW_G2_E_CAP(h)", "if (sl == NULL || (*sl & 0xFFFFu) >= SW_G2_R0_ITEMS || h->g2ev >= SW_G2_E_CAP(h)")]),
    ("L24: no ring-2 witness gate (T-23da F1, T-48a0 interim witness)", [(" || (e->l24 && (!h->g2rd || h->g2ev <= h->g2ev0)) || ", " || ")]),
    ("L24 witness: the gate only needs g2rd (no event after the consume, T-48a0)", [("(e->l24 && (!h->g2rd || h->g2ev <= h->g2ev0))", "(e->l24 && !h->g2rd)")]),
    ("L24 witness: g2ev0 not recorded at the consume (T-48a0)", [("\t\t\th->g2ev0 = h->g2ev;\n", "")]),
    ("L24 witness: any commit counts as the consume (T-48a0)", [("if (h->g2l[g2i].kind == SW_G2K_R_RD && h->g2esc >= 0 && h->g2l[h->g2esc].done && !h->g2rd) {", "if (h->g2esc >= 0 && h->g2l[h->g2esc].done && !h->g2rd) {")]),
    ("L24 witness: the consume before the escan doorbell counts (T-48a0)", [("if (h->g2l[g2i].kind == SW_G2K_R_RD && h->g2esc >= 0 && h->g2l[h->g2esc].done && !h->g2rd) {", "if (h->g2l[g2i].kind == SW_G2K_R_RD && !h->g2rd) {")]),
    ("L24: entries not marked l24 (T-23da F1)", [("\t\th->g2l[i].l24 = 1;", "\t\th->g2l[i].l24 = 0;")]),
    ("refusal line prints an ioctl payload value (T-23da F4)", [("if (space == SW3_HOST && off >= h->g2plan.b[SWD_IOCTBUF].off && off - h->g2plan.b[SWD_IOCTBUF].off < h->g2plan.b[SWD_IOCTBUF].len) {", "if (0) {")]),
    ("refusal line withholds only the first ioctl payload word (T-23da F4)", [("off - h->g2plan.b[SWD_IOCTBUF].off < h->g2plan.b[SWD_IOCTBUF].len) {", "off - h->g2plan.b[SWD_IOCTBUF].off < 4u) {")]),
    ("d4j: no njc bounds (T-23da F3b)", [("if (h->jc == NULL || h->njc < SWG_J_NCMD || h->njc > SWG_J_NCMAX || !sw_g2_j_is(", "if (h->jc == NULL || !sw_g2_j_is(")]),
    ("d4j: no njc lower bound (T-23da F3b)", [("if (h->jc == NULL || h->njc < SWG_J_NCMD || h->njc > SWG_J_NCMAX || !sw_g2_j_is(", "if (h->jc == NULL || h->njc > SWG_J_NCMAX || !sw_g2_j_is(")]),
    ("d4j: no njc upper bound (T-23da F3b)", [("if (h->jc == NULL || h->njc < SWG_J_NCMD || h->njc > SWG_J_NCMAX || !sw_g2_j_is(", "if (h->jc == NULL || h->njc < SWG_J_NCMD || !sw_g2_j_is(")]),
    ("E_ITEM: word 0 for every offset", [("*ev = w[(e->off - h->g2plan.b[SWD_H2D_CTRL].off) / 4u];", "*ev = w[0];")]),
    ("E_ITEM/E_WR: id of counter g2ev + 1", [("sw_g2_d4e_ev(w, SW_G2_E_EVID + 8u + h->g2ev,", "sw_g2_d4e_ev(w, SW_G2_E_EVID + 9u + h->g2ev,")]),
    ("E_ITEM/E_WR: event buffer 1, not buffer 0 (T-f1e2: the item's word 4 is the slot-0 address)", [("h->g2pa + h->g2plan.b[SWD_EVBUF].off + 0u, i);", "h->g2pa + h->g2plan.b[SWD_EVBUF].off + SW_G2_E_BUFSZ, i);")]),
    ("E_WR: any forward step", [("(vv & 0xFFFFu) == (old & 0xFFFFu) + 1u && (vv & 0xFFFFu) < SW_G2_R0_ITEMS && (vv & 0xFFFF0000u) == (old & 0xFFFF0000u) && sw_g2_evitem_ok(", "(vv & 0xFFFFu) > (old & 0xFFFFu) && (vv & 0xFFFFu) < SW_G2_R0_ITEMS && (vv & 0xFFFF0000u) == (old & 0xFFFF0000u) && sw_g2_evitem_ok(")]),
    ("E_WR: no ring-depth bound", [("(old & 0xFFFFu) + 1u && (vv & 0xFFFFu) < SW_G2_R0_ITEMS && (vv & 0xFFFF0000u) == (old & 0xFFFF0000u) && sw_g2_evitem_ok(", "(old & 0xFFFFu) + 1u && (vv & 0xFFFF0000u) == (old & 0xFFFF0000u) && sw_g2_evitem_ok(")]),
    ("E_WR: upper half not kept", [("< SW_G2_R0_ITEMS && (vv & 0xFFFF0000u) == (old & 0xFFFF0000u) && sw_g2_evitem_ok(", "< SW_G2_R0_ITEMS && sw_g2_evitem_ok(")]),
    ("E_WR: no read-back of the item", [("&& (vv & 0xFFFF0000u) == (old & 0xFFFF0000u) && sw_g2_evitem_ok(h, old & 0xFFFFu)) {", "&& (vv & 0xFFFF0000u) == (old & 0xFFFF0000u)) {")]),
    ("E_WR: read-back of the new slot", [("&& sw_g2_evitem_ok(h, old & 0xFFFFu)) {", "&& sw_g2_evitem_ok(h, vv & 0xFFFFu)) {")]),
    ("E_WR: cap and memory-preset item (both caps off)", [("if (h->g2ev < SW_G2_E_CAP(h) && (vv & 0xFFFFu) == (old & 0xFFFFu) + 1u", "if ((vv & 0xFFFFu) == (old & 0xFFFFu) + 1u"), ("|| h->g2ev >= SW_G2_E_CAP(h) || e->off < h->g2plan.b[SWD_H2D_CTRL].off", "|| e->off < h->g2plan.b[SWD_H2D_CTRL].off")]),
    ("E_WR: the E_WR cap alone off (memory-preset bound case)", [("if (h->g2ev < SW_G2_E_CAP(h) && (vv & 0xFFFFu) == (old & 0xFFFFu) + 1u", "if ((vv & 0xFFFFu) == (old & 0xFFFFu) + 1u")]),
    ("g2ev not counted", [("\t\t\th->g2ev++;      /* T-4a7f", "\t\t\t(void)0;      /* T-4a7f")]),
    ("SH shift without g2ev", [("sh = h->g2sh + h->g2ev, w[10];", "sh = h->g2sh, w[10];")]),
    ("e_rx: event words not chained after the escan doorbell", [("0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, need) < 0) {\n\t\t\treturn -1;\n\t\t}\n\t\th->g2l[h->g2n - 1u].kind = SW_G2K_E_ITEM;", "0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, -1) < 0) {\n\t\t\treturn -1;\n\t\t}\n\t\th->g2l[h->g2n - 1u].kind = SW_G2K_E_ITEM;")]),
    ("e_rx: commit not chained after the escan doorbell", [("0u, 0x0000FFFFu, 0u, 0u, 0, need) < 0) {\n\t\treturn -1;\n\t}\n\th->g2l[h->g2n - 1u].kind = SW_G2K_E_WR;", "0u, 0x0000FFFFu, 0u, 0u, 0, -1) < 0) {\n\t\treturn -1;\n\t}\n\th->g2l[h->g2n - 1u].kind = SW_G2K_E_WR;")]),
    # T-1142: T-84b0/T-f1e2 replaced the response FIFO and the SW_G2_J_TRANS / SW_G2_J_RE0 macros by sw_g2_e_rq (SW_G2_E_TRANS / SW_G2_E_RE0 + the request number jq = kq + 1 + call): the rows restate those rules
    ("d4j: L24 request numbers from kq (the escan's number)", [("uint32_t jq = kq + 1u, k, i, n, n0 = h->g2n;", "uint32_t jq = kq, k, i, n, n0 = h->g2n;")]),
    ("e_rq: transaction id one late", [("sw_g2_d4d_req(w, cmd, SW_G2_E_TRANS + *kq, n, ipa, *ix);", "sw_g2_d4d_req(w, cmd, SW_G2_E_TRANS + *kq + 1u, n, ipa, *ix);")]),
    ("e_rq: re-post id one early", [("sw_g2_d4d_resp(w, SW_G2_E_RE0 + *kq, rpa, *ix);", "sw_g2_d4d_resp(w, SW_G2_E_RE0 + *kq - 1u, rpa, *ix);")]),
    ("e_rq: re-post buffer fixed (not any of the 8)", [("\t\th->g2l[last - 7].vrng = 7u * SW_G2_E_BUFSZ;\n\t\th->g2l[last - 7].kind = SW_G2K_SH_RBUF;\n\t}\n\t(*kq)++;", "\t\th->g2l[last - 7].vrng = 0u;\n\t\th->g2l[last - 7].kind = SW_G2K_SH_RBUF;\n\t}\n\t(*kq)++;")]),
    ("d4j: mask bit 17 for 16", [("bits[8] = { 69u, 0u, 16u, 5u, 6u, 11u, 12u, 46u };", "bits[8] = { 69u, 0u, 17u, 5u, 6u, 11u, 12u, 46u };")]),
    ("d4j: mask len 25", [("\t\t\tb[17] = 26u;", "\t\t\tb[17] = 25u;")]),
    ("d4j: no wsec_key zeroing", [("if (key && last >= 0) {", "if (0 && last >= 0) {")]),
    ("d4j: wsec_key zeroing of 40 words", [("last = sw_g2_j_pay(h, sh, &shv, b, sizeof b, last, 0);", "last = sw_g2_j_pay(h, sh, &shv, b, 160u, last, 0);")]),
    ("d4j: no join-last check", [(" || !sw_g2_j_is(h->jc[h->njc - 1u].buf, h->jc[h->njc - 1u].len, \"join\")) {", ") {")]),
    ("d4j: L24 from d4b 10", [("if (h->d4b >= 11 && last >= 0) {        /* T-4a7f: L24", "if (h->d4b >= 10 && last >= 0) {        /* T-4a7f: L24")]),
    ("j_pay: VER old mask on every word", [("vb ? sh[i] & ~SW_G2_VERBIT : sh[i], vb ? ~SW_G2_VERBIT : 0xFFFFFFFFu", "sh[i] & ~SW_G2_VERBIT, ~SW_G2_VERBIT")]),
    ("j_pay: VER old compared in full", [("vb ? ~SW_G2_VERBIT : 0xFFFFFFFFu", "0xFFFFFFFFu")]),
    ("j_pay: VER bit kept after the store", [("\t\tsh[i] = v;\n\t\t*shv &= ~(1u << i);", "\t\tsh[i] = v;")]),
    ("j_pay: shadow not updated", [("\t\tsh[i] = v;\n\t\t*shv &= ~(1u << i);", "\t\t*shv &= ~(1u << i);")]),
    ("j_pay: the escan version words not passed (shv 0)", [("last = sw_g2_d4j(h, sh, (1u << 1) | (1u << 3), ix, kq, last);", "last = sw_g2_d4j(h, sh, 0u, ix, kq, last);")]),
]


def _t8_texts(subs):
    """The three glue texts (sweep, G2L core, G2L list) with the mutant applied; None when a pattern occurs in none of them."""
    out = []
    for base in (glue_function_text(), glue_core_text(), g2_list_text()):
        t = base
        for old, new in subs:
            t = t.replace(old, new)
        out.append(t)
    for old, _new in subs:
        if not any(old in b for b in (glue_function_text(), glue_core_text(), g2_list_text())):
            return None
    return out


def _run_g2_list_core(core, list_text, work, extra=()):
    d = tempfile.mkdtemp(dir=work)
    for text, name in ((chip_header(), "sweet_wlan_chip.h"), (final_header(), "sweet_wlan_wr.h"), (new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h"), "sweet_wlan_dma.h"), (l25wl_header(), "sweet_wlan_l25_wl.h")):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(d, "l.c"), "w", encoding="utf-8") as fh:
        fh.write(G2L_HARNESS.replace("@GLUE@", core + list_text))
    c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I" + d] + list(extra) + ["-o", os.path.join(d, "l"), os.path.join(d, "l.c")], capture_output=True, text=True)
    if c.returncode != 0:
        return "compile", c.stderr[-300:]
    r = run_bounded([os.path.join(d, "l")], d, 60)
    return ("pass" if r[0] == 0 else "fail"), r[2]


def _t8_run_one(label, subs):
    """One T8EDE mutant: ("fail", why) = killed by the glue sweep or the G2L harness, ("pass", "") = SURVIVED, ("compile", msg) = broken. A pattern found in no glue text raises (a loud error, never a kill)."""
    base = (glue_function_text(), glue_core_text(), g2_list_text())
    t = _t8_texts(subs)
    if t is None or tuple(t) == base:
        raise ValueError("T8EDE mutant %s: pattern not found in the glue texts (glue changed under the mutant)" % label)
    with tempfile.TemporaryDirectory() as w:
        if t[0] != base[0]:
            st, out = run_glue(t[0], w, GLUE_MUTANT_FLAGS)
            if st != "pass":
                return st, "glue sweep: " + (out.strip().splitlines()[-1][:160] if out.strip() else "")
        if t[1] != base[1] or t[2] != base[2]:
            st, out = _run_g2_list_core(t[1], t[2], w)
            if st != "pass":
                return st, "G2L: " + (out.strip().splitlines()[-1][:160] if out.strip() else "")
    return "pass", ""


def t8ede_kill():
    """T8EDE_MUTANTS pooled through wlan_mutant_pool.run_set (set gate "g2l", cache key = every input): [(name, st, out)]; None when GATE_MUT_ONLY skips g2l."""
    res = wlan_mutant_pool.run_set("g2l", T8EDE_MUTANTS, _t8_run_one,
                                   [glue_function_text(), glue_core_text(), g2_list_text(), GLUE_HARNESS, G2L_HARNESS, streams_c(), chip_header(), final_header(), new_file(PATCH0167, "bsd/dev/sweet_wlan_dma.h"), l25wl_header(), " ".join(GLUE_MUTANT_FLAGS)])
    if res is None:
        return None
    return [(name, st, out) for (name, _s), (st, out) in zip(T8EDE_MUTANTS, res)]


# mutant-block: wlan-g2fix
G2FIX_MUTANTS = [
    ("F5: host word bound is the old one", [("off <= h->g2plan.total - 4u ?", "off < h->g2plan.total ?")]),
    ("F5: host word bound without the total >= 4 guard", [("h->g2plan.total >= 4u && off <=", "off <=")]),
    ("F5: host read without the address check", [("\t\tif (sw3_ptr(h, SW3_HOST, a) == NULL) {  /* review r1 F5: no cache op on an address the glue did not check */\n\t\t\tswl_gap(\"g2\", \"glue: host block read outside the block refused\", \"high\");\n\t\t\treturn -1;\n\t\t}\n", "")]),
    ("F5: host store without the address check", [("\t\tif (sw3_ptr(h, SW3_HOST, a) == NULL) {  /* review r1 F5: no cache op on an address the glue did not check */\n\t\t\tswl_gap(\"g2\", \"glue: host block store outside the block refused\", \"high\");\n\t\t\treturn -1;\n\t\t}\n", "")]),
    ("F5: host read refusal without a gap", [("\t\t\tswl_gap(\"g2\", \"glue: host block read outside the block refused\", \"high\");\n", "")]),
    ("F5: host store refusal without a gap", [("\t\t\tswl_gap(\"g2\", \"glue: host block store outside the block refused\", \"high\");\n", "")]),
    ("F5: host read flushes first", [("\t\tif (sw3_ptr(h, SW3_HOST, a) == NULL) {  /* review r1 F5: no cache op on an address the glue did not check */\n\t\t\tswl_gap(\"g2\", \"glue: host block read outside the block refused\", \"high\");\n\t\t\treturn -1;\n\t\t}\n\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n", "\t\tflush_dcache64((addr64_t)(h->g2pa + a), 4, TRUE);\n\t\tif (sw3_ptr(h, SW3_HOST, a) == NULL) {\n\t\t\tswl_gap(\"g2\", \"glue: host block read outside the block refused\", \"high\");\n\t\t\treturn -1;\n\t\t}\n")]),
    ("F6: the old word is read for every entry", [("if (!have && (w16 || e->omask != 0 || e->vmask != 0xFFFFFFFFu || e->kind == SW_G2K_R_WR || e->kind == SW_G2K_E_WR)) {", "if (!have) {")]),
    ("F6: the old word is read for omask-0 doorbells again", [("if (!have && (w16 || e->omask != 0 || e->vmask != 0xFFFFFFFFu || e->kind == SW_G2K_R_WR || e->kind == SW_G2K_E_WR)) {", "if (!have && (w16 || e->omask != 0 || e->vmask != 0xFFFFFFFFu || e->kind == SW_G2K_R_WR || e->kind == SW_G2K_E_WR || e->need >= 0)) {")]),
    ("F6: the old word is read before the match", [("\tuint32_t old = 0, vv,", "\tuint32_t old = *sw3_ptr(h, space, off), vv,")]),
    ("W15: highest level 14 again", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 14 || SecureDTLookupEntry(")]),
    ("W-D4e: list capacity 1272 (one short of the 1273 entries of level 23: 1257 exact + 16 masked)", [("#define SW_G2_MAX   1816u", "#define SW_G2_MAX   1272u")]),
    ("L22c: list capacity 1630 (one short of the 1631 entries of level 24 at 12 CLM chunks)", [("#define SW_G2_MAX   1816u", "#define SW_G2_MAX   1630u")]),
    ("W16: highest level 15 again", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 15 || SecureDTLookupEntry(")]),
    ("W-D4d: highest level 16 again", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 16 || SecureDTLookupEntry(")]),
    ("W-D4e: highest level 23", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 23 || SecureDTLookupEntry(")]),
    ("W-D4e: highest level 27", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 27 || SecureDTLookupEntry(")]),
    ("F6: a second read of the old word", [("\t} else if (have) {\n\t\tswl_gap(\"g2\", \"glue: store not in the G2 list, refused\", \"high\");", "\t} else if ((old = *p) != 0u || have) {\n\t\tswl_gap(\"g2\", \"glue: store not in the G2 list, refused\", \"high\");")]),     # T-23da: the refusal now starts with the ioctl-payload branch (F4)
    ("W16: list capacity 64 (the 103 entries of level 16 do not fit)", [("#define SW_G2_MAX   1816u", "#define SW_G2_MAX   64u")]),
    ("W16: list capacity 102 (one short of the 103 entries of level 16)", [("#define SW_G2_MAX   1816u", "#define SW_G2_MAX   102u")]),
    ("T-4a7f: list capacity 515 (one short of the 516 entries of level 23: 489 exact + 16 masked DS + 11 event re-post entries)", [("#define SW_G2_MAX   1816u", "#define SW_G2_MAX   515u")]),
    ("T-4a7f: highest level 22", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 22 || SecureDTLookupEntry(")]),
    ("T-4a7f: highest level 23", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 23 || SecureDTLookupEntry(")]),
    ("T-4a7f: highest level 27", [("if (level > 26 || SecureDTLookupEntry(", "if (level > 27 || SecureDTLookupEntry(")]),
    ("W15: hw.w15 from level 14", [("hw.w15 = hw.g2ok && level >= 15;", "hw.w15 = hw.g2ok && level >= 14;")]),
    ("W15: hw.w15 without G2 armed", [("hw.w15 = hw.g2ok && level >= 15;", "hw.w15 = level >= 15;")]),
    ("W15: stage 3 not capped at 10", [("sweet_wlan_stage3(&hw, level > 10 ? 10 : (int)level);", "sweet_wlan_stage3(&hw, level > 10 ? 15 : (int)level);")]),
    ("W15: allowance outlives stage 3", [("\thw->w15 = 0;    /* patch 0174 D-12: the W15 allowance does not outlive stage 3", "\t(void)hw->w15;    /* patch 0174 D-12: the W15 allowance does not outlive stage 3")]),
    ("W15: allowance cleared before the stage runs", [("\tif (swl_stage3(&x) != 0) {", "\thw->w15 = 0;\n\tif (swl_stage3(&x) != 0) {")]),
    ("W15: x.w15 not carried", [("\tx.w15 = hw->w15;", "\tx.w15 = 0;")]),
    ("W15: x.w15 always set", [("\tx.w15 = hw->w15;", "\tx.w15 = 1;")]),
    ("W15: glue branch without h->w15", [("(h->w15 && !h->c88 && h->rel == 0 &&", "(!h->c88 && h->rel == 0 &&")]),
    ("W15: glue branch after the release", [("(h->w15 && !h->c88 && h->rel == 0 &&", "(h->w15 && !h->c88 &&")]),
    ("W15: glue branch twice", [("(h->w15 && !h->c88 && h->rel == 0 &&", "(h->w15 && h->rel == 0 &&")]),
    ("W15: glue branch old word not pinned", [(" && h->cfg[0x88 / 4] == SWW_CFG88)) {", ")) {")]),
    ("W15: glue branch any value", [("v == (SWW_CFG88 | SW_G2_C88_BITS) && ", "")]),
    ("W15: glue restore without the W15 store", [("if ((h->c88 && v == SWW_CFG88) ||", "if ((v == SWW_CFG88) ||")]),
    ("W15: glue refusal without a gap", [("\t\tswl_gap(\"g2\", \"glue: cfg 0x88 store outside the W15 state refused\", \"high\");\n", "")]),
]


def audit_runner(glue, audit):
    """run_one for a pure-python audit mutant set on the run_set pool (AP-368): every mutant must make audit(mutated glue) report something ("fail" = killed)."""
    def run_one(label, subs):
        t = glue
        for old, new in subs:
            if old not in t:
                return "nomatch", "pattern not found: " + old
            t = t.replace(old, new)
        found = audit(t)
        return ("fail" if found else "pass"), "; ".join(found)
    return run_one


def audit_bad(mutants, res):
    """-> [failure message] for the run_set verdicts of an audit set ([] when res is None: the set was skipped by GATE_MUT_ONLY, want() printed the line)."""
    return [] if res is None else ["%s: %s" % (label, "SURVIVED" if st == "pass" else msg if st == "nomatch" else "%s %s" % (st, msg)) for (label, _s), (st, msg) in zip(mutants, res) if st != "fail"]


class G2Mutants(unittest.TestCase):
    def test_g2_host_bus_flushes_and_hook_geometry(self):
        glue = glue_after()
        self.assertEqual(g2host_audit(glue), [])
        res = wlan_mutant_pool.run_set("g2host", G2HOST_MUTANTS, audit_runner(glue, g2host_audit), [glue])
        bad = audit_bad(G2HOST_MUTANTS, res)
        self.assertEqual(bad, [], "\n".join(bad))

    def test_w_d4b_glue_conditions(self):
        glue = glue_after()
        self.assertEqual(g2fix_audit(glue, final_header()), [])
        res = wlan_mutant_pool.run_set("g2fix", G2FIX_MUTANTS, audit_runner(glue, lambda t: g2fix_audit(t, final_header())), [glue, final_header()])
        bad = audit_bad(G2FIX_MUTANTS, res)
        self.assertEqual(bad, [], "\n".join(bad))
        hdr = final_header()
        for name, old, new in (("swl_stage3 runs W15 after W16", "rc = rc ? rc : swl_w15(x);      /* patch 0174 D-12: sweetwlan=15 only (x->w15), before the release */\n\t\trc = rc ? rc : swl_w16(x);", "rc = rc ? rc : swl_w16(x);\n\t\trc = rc ? rc : swl_w15(x);"),
                               ("swl_stage3 never runs W15", "\t\trc = rc ? rc : swl_w15(x);      /* patch 0174 D-12: sweetwlan=15 only (x->w15), before the release */\n", "")):
            self.assertIn(old, hdr, name + ": pattern not found")
            self.assertNotEqual(g2fix_audit(glue, hdr.replace(old, new)), [], name + " survived")

    def test_g2_shim_header_is_the_patch_header(self):
        """shim/sweet_wlan_g2.h (the copy the shim build includes) is byte-equal to patch 0174's new file (as shim/build.sh checks); read here so a change
        to it alone re-selects this test (tools/gate_select.py inputs, test_gate_select)."""
        with open(G2_SHIM_HDR, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), g2_header_raw())
        with open(os.path.join(ROOT, "shim", "sweet_wlan_g2d.h"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), new_file(PATCH0174, "bsd/dev/sweet_wlan_g2d.h"))
        with open(os.path.join(ROOT, "shim", "sweet_wlan_g2e.h"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), new_file(PATCH0174, "bsd/dev/sweet_wlan_g2e.h"))
        with open(os.path.join(ROOT, "shim", "sweet_wlan_g2r.h"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), new_file(PATCH0174, "bsd/dev/sweet_wlan_g2r.h"))

    def test_d4b_builder_checker_mutants_are_killed(self):
        res = d4b_kill_test()
        if res is None:
            return
        bad = ["%s: %s %s" % (n, st, (msg or "").strip()[-200:]) for n, st, msg in res if st != "FAIL"]
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertEqual(len(res), len(D4B_MUTANTS))

    def test_g2_refuses_a_block_without_va(self):
        glue = glue_after()
        self.assertEqual(g2va_audit(glue), [])
        res = wlan_mutant_pool.run_set("g2va", G2VA_MUTANTS, audit_runner(glue, g2va_audit), [glue])
        bad = audit_bad(G2VA_MUTANTS, res)
        self.assertEqual(bad, [], "\n".join(bad))

    def test_g2_mutants_are_killed(self):
        res = g2_kill_test()
        if res is None:
            return
        bad = ["%s: %s %s" % (n, st, (msg or "").strip()[-200:]) for n, st, msg in res if st != "FAIL"]
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertEqual(len(res), len(G2_MUTANTS))


if __name__ == "__main__" and "--g2-kill" in sys.argv:    # T-8ede: the mutants of the T-4a7f rules (T8EDE_MUTANTS) through the glue sweep and the G2L harness
    _res = t8ede_kill()
    if _res is None:
        print("RESULT: FAIL g2 kill test: GATE_MUT_ONLY skips g2l")
        sys.exit(1)
    for _n, _v, _d in _res:
        print("MUTANT %-8s %s  %s" % ("killed" if _v == "fail" else "SURVIVED" if _v == "pass" else _v, _n, _d))
    _k = sum(1 for _r in _res if _r[1] == "fail")
    _st = "PASS" if _k == len(T8EDE_MUTANTS) else "FAIL"
    print("RESULT: %s g2 kill test %d/%d T-4a7f glue mutants killed (glue sweep %d, G2L %d)" % (_st, _k, len(T8EDE_MUTANTS), sum(1 for _r in _res if _r[1] == "fail" and _r[2].startswith("glue")), sum(1 for _r in _res if _r[1] == "fail" and _r[2].startswith("G2L"))))
    sys.exit(0 if _st == "PASS" else 1)

if __name__ == "__main__" and "--g2-l24" in sys.argv:     # T-8ede: the L24 join streams through the real glue list (patch 0174), with the targeted negatives; no mutants
    with tempfile.TemporaryDirectory() as _w:
        _sink = []
        _st, _out = run_glue(glue_function_text(), _w, stdout_sink=_sink)
    _m = re.search(r"^L24 streams (\d+) negatives (\d+)$", _sink[0] if _sink else "", re.M)
    _acc = re.search(r"^PASS accepted (\d+) bad 0$", _sink[0] if _sink else "", re.M)
    if _st == "pass" and _m and _acc and int(_m.group(1)) == len(L24_LABELS) and int(_m.group(2)) == L24_NEGATIVES:
        print("RESULT: PASS g2 glue list L24 streams %s (%s) negatives %s/%d, glue sweep accepted %s stores" % (_m.group(1), ", ".join(L24_LABELS), _m.group(2), L24_NEGATIVES, _acc.group(1)))
        sys.exit(0)
    print("\n".join(l for l in _out.splitlines() if l.startswith("BAD"))[:3000])
    print("RESULT: FAIL g2 glue list L24 (sweep %s, L24 line %r)" % (_st, _m.group(0) if _m else None))
    sys.exit(1)

if __name__ == "__main__" and "--g2" in sys.argv:
    _skipped = [n for n in ("g2", "g2l", "d4b", "g2va", "g2host", "g2fix") if not wlan_mutant_pool.want(n)]
    if _skipped:        # --g2 is the whole W-D4b set: a skipped set must not look like a pass
        print("RESULT: FAIL g2 mutants: GATE_MUT_ONLY skips %s" % ", ".join(_skipped))
        sys.exit(1)
    res = g2_kill_test()
    # review r4 LOW 3: --g2 also runs the glue list harness and its mutants (sw_g2_list is G2's store list)
    gl = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite([WlanWrMutants("test_g2_list_harness_passes"), WlanWrMutants("test_g2_list_mutants_are_killed")]))
    for name, st, msg in res:
        print("%-16s %s%s" % (st, name, "" if st in ("FAIL", "SURVIVED") else ": " + (msg or "")))
    ok = sum(1 for _, st, _m in res if st == "FAIL")
    # W-D4b: the pure d4b helpers and the D-10 g2va wiring are part of the --g2 set
    dres = d4b_kill_test()
    for name, st, msg in dres:
        print("%-16s %s%s" % (st, name, "" if st in ("FAIL", "SURVIVED") else ": " + (msg or "")))
    dok = sum(1 for _, st, _m in dres if st == "FAIL")
    vt = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite([G2Mutants("test_g2_refuses_a_block_without_va"), G2Mutants("test_g2_host_bus_flushes_and_hook_geometry"), G2Mutants("test_w_d4b_glue_conditions")]))
    d4b_ok = dok == len(dres) == len(D4B_MUTANTS) and vt.wasSuccessful()
    total = ok + dok
    if ok == len(res) == len(G2_MUTANTS) and gl.wasSuccessful() and d4b_ok:
        # W-D4b gaps (dump, miss resolution, 2BYTE pin, host-block gap, cfg 0x88, 16-bit slots): how many of each list are about them
        pre = ("dump:", "miss:", "pins:", "host gap:", "c88:", "w16:", "D-9:", "tcm16:", "add: w16", "G2: w16", "baseline: no WIFI GAP", "F3:", "F4:", "F6:", "F7:", "W15:")
        gh = sum(1 for n, _s in G2_MUTANTS if n.startswith(pre))
        gl = sum(1 for n, _s in G2L_MUTANTS if n.startswith(pre))
        gg = sum(1 for n, _s in GLUE_MUTANTS if n.startswith(pre))
        print("RESULT: PASS g2 mutants %d/%d killed, glue list harness + %d mutants OK, d4b helpers %d/%d killed, g2va gate %d mutants OK, host bus flush audit %d mutants OK, total mutants %d; W-D4b gaps items: %d header + %d glue list + %d glue store mutants (dump/console, miss resolution, 2BYTE pin, host-block gap, cfg 0x88, 16-bit slots)" % (ok, len(G2_MUTANTS), len(G2L_MUTANTS), dok, len(D4B_MUTANTS), len(G2VA_MUTANTS), len(G2HOST_MUTANTS), total + len(G2L_MUTANTS) + len(G2VA_MUTANTS) + len(G2HOST_MUTANTS), gh, gl, gg))
        sys.exit(0)
    print("RESULT: FAIL g2 mutants %d/%d killed, glue list %s, d4b %d/%d, g2va gate %s" % (ok, len(G2_MUTANTS), "OK" if gl.wasSuccessful() else "FAILED", dok, len(D4B_MUTANTS), "OK" if vt.wasSuccessful() else "FAILED"))
    sys.exit(1)

def _header_mutants(hdr):
    """[(name, mutated header)] of the header set exactly as run_all builds it: each distinct changed header once (nomatch / nochange / duplicates dropped)."""
    seen, items = set(), []
    for name, subs, _equiv in MUTANTS:
        m = hdr
        if any(o not in m for o, _n in subs):
            continue
        for o, n in subs:
            m = m.replace(o, n)
        if m != hdr and m not in seen:
            seen.add(m)
            items.append((name, m))
    return items


def probe_failfast_main(n):
    """--probe-failfast N (PLAN-GATE-2X K2a): N header mutants (stride sample), each built and run twice with the cache OFF, sequentially: fail-fast (CC_FLAGS) and full sweep."""
    os.environ["GATE_MUT_CACHE"] = "0"                # the probe never reads or writes the verdict cache (build_and_run is also called with use_cache=False)
    hdr, chip = final_header(), new_file(PATCH0120, "bsd/dev/sweet_wlan_chip.h")
    items = _header_mutants(hdr)
    full_flags = [f for f in CC_FLAGS if f != "-DSWW_FAILFAST=1"]
    if len(full_flags) != len(CC_FLAGS) - 1:
        raise SystemExit("RESULT: FAIL failfast probe: CC_FLAGS has no -DSWW_FAILFAST=1 to remove (%r)" % (CC_FLAGS,))
    picked = probe_failfast.sample(items, n)
    os.makedirs(os.path.join(ROOT, "shim", "out"), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "shim", "out")) as work:
        def run_one(name, mhdr, ff):
            timing = {}
            st, msg = build_and_run(mhdr, chip, work, use_cache=False, cc_flags=CC_FLAGS if ff else full_flags, timing=timing)
            return st, msg, timing["gcc"], timing["test"]
        rows = probe_failfast.run_probe(picked, run_one)
    summary = probe_failfast.summarize(rows, len(items))
    lines, ok = probe_failfast.format_lines(rows, summary)
    print("\n".join(lines))
    return 0 if ok else 1


def _apply_probe_argv(argv):
    """Remove `--probe-failfast N` from argv (in place); -> N or None. A missing, non-numeric or < 1 value exits 2 with the reason."""
    if "--probe-failfast" not in argv:
        return None
    i = argv.index("--probe-failfast")
    try:
        n = int(argv[i + 1])
        probe_failfast.sample([0], n)
    except (IndexError, ValueError) as e:
        print("--probe-failfast needs an integer N >= 1 (%s)" % e, file=sys.stderr)
        sys.exit(2)
    del argv[i:i + 2]
    return n


if __name__ == "__main__":
    wlan_mutant_pool.apply_only_argv(sys.argv)       # --only header,glue,g2,g2l,d4b,audit  (= GATE_MUT_ONLY)
    _probe_n = _apply_probe_argv(sys.argv)           # --probe-failfast N: measurement, no unit tests
    if _probe_n is not None:
        sys.exit(probe_failfast_main(_probe_n))
    unittest.main()
