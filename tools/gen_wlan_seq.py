#!/usr/bin/env python3
"""Generate soc/google/gs101/wlan_seq.json: the table behind `--wlan-link` (docs/research/29 sections 2 and 14).

Every value is read from the fetched Google sources (default ~/pixelios-artifacts/refs/wlan-stage1, override with $WLAN_REFS), never typed by hand:
  * the PHY power-down-clear, PHY config and PHY power-down tables are PARSED from dwc-whi/pcie-exynos-gs101-rc-cal.c (writel / readl-modify-writel / udelay / mdelay statements),
  * the I/A programming sequence is PARSED from dwc-whi/pcie-exynos-rc.c exynos_pcie_rc_use_ia(),
  * register offsets and bit values of the controller steps come from the #defines of pcie-exynos-rc.h / pcie-exynos-common.h,
  * the pinctrl 'active' state comes from the device-tree nodes (gs101-pinctrl.dtsi, gs101-common-wlan.dtsi) and the register layout of linux-pinctrl-exynos-arm64.c,
  * the block base addresses come from gs101-pcie.dtsi (cross-checked against soc.json "wlan").
Hand-written rows (the GPIO, SMC, delay and wait steps) carry an ANCHOR: the generator fails if the cited source line does not contain the expected text, so a stale cite is a build error.
The output is deterministic; tools/test_wlan_seq.py regenerates it and compares (skipped when the refs are not on the machine); the table hash is pinned in tools/test-bluejay-boot.py.
Usage: tools/gen_wlan_seq.py [--check]   (writes the file, or with --check compares)."""
import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "soc/google/gs101/wlan_seq.json"
REFS = Path(os.environ.get("WLAN_REFS", str(Path.home() / "pixelios-artifacts/refs/wlan-stage1")))
WHI = REFS / "dwc-whi"
CAL, RC, RCH, COM = WHI / "pcie-exynos-gs101-rc-cal.c", WHI / "pcie-exynos-rc.c", WHI / "pcie-exynos-rc.h", WHI / "pcie-exynos-common.h"
DT_PCIE, DT_COMMON = REFS / "dts-gs101-gs101-pcie.dtsi", REFS / "dts-gs101-gs101-common.dtsi"
DT_PINCTRL, DT_WLAN, DT_PINH = REFS / "dts-gs101-gs101-pinctrl.dtsi", REFS / "dts-gs101-gs101-common-wlan.dtsi", REFS / "dts-gs101-gs101-pinctrl.h"
SAMSUNG_H, DHD = REFS / "linux-samsung.h", REFS / "dhd_custom_google.c"
SOC = ROOT / "soc/google/gs101/soc.json"

# the Broadcom DHD tree (research 32 s.5b, fetched 2026-10-23, GPLv2 reference, not in git): chip-side config register names; absent refs make the generator fail like any other missing source
REFS2 = Path(os.environ.get("WLAN_REFS2", str(REFS.parent / "wlan-stage2")))
PCICFG = REFS2 / "include_pcicfg.h"
PCIR = REFS2 / "pci_regs.h"	# copy of the host /usr/include/linux/pci_regs.h, linux-libc-dev 6.12.113-1 (sha-pinned below)
DHDP, SIU, AIU = REFS2 / "dhd_pcie.c", REFS2 / "siutils.c", REFS2 / "aiutils.c"
SRC_NAMES = {PCIR: "linux/pci_regs.h", PCICFG: "bcm4389/include_pcicfg.h", DHDP: "bcm4389/dhd_pcie.c", SIU: "bcm4389/siutils.c", AIU: "bcm4389/aiutils.c", CAL: "cal.c", RC: "rc.c", RCH: "rc.h", COM: "common.h", DT_PCIE: "pcie.dtsi", DT_COMMON: "common.dtsi", DT_PINCTRL: "pinctrl.dtsi", DT_WLAN: "wlan.dtsi", DHD: "dhd_cg.c"}
KINDS = ("gpio_dat", "gpio_con", "mmio_rmw", "mmio_write", "smc_iso", "delay", "wait_eq", "wait_mask", "read_only")
BLOCKS = ("GPP14", "GPH2", "PMA", "PCS", "ELBI", "IA", "DBI", "PMU", "ATU", "CFG")
FULL = 0xFFFFFFFF
TAG_SRC, TAG_GUESS = "read from source", "guess"

_lines_cache = {}


def lines(path):
    if path not in _lines_cache:
        _lines_cache[path] = path.read_text().splitlines()
    return _lines_cache[path]


def anchor(path, line, rx):
    """The cited line must contain the expected text."""
    txt = lines(path)[line - 1]
    if not re.search(rx, txt):
        raise SystemExit(f"anchor failed: {SRC_NAMES.get(path, path.name)}:{line} does not match /{rx}/: {txt.strip()!r}")
    return f"{SRC_NAMES[path]}:{line}"


def find_line(path, rx, start=1):
    for i, t in enumerate(lines(path)[start - 1:], start):
        if re.search(rx, t):
            return i
    raise SystemExit(f"{path.name}: no line matches /{rx}/ after {start}")


# ---- macros of rc.h / common.h ----
_macros = {}
for _p in (RCH, COM):
    for _t in _p.read_text().splitlines():
        m = re.match(r"\s*#define\s+(\w+)\s+(.+?)\s*(?:/\*.*)?$", _t)
        if m:
            _macros.setdefault(m.group(1), m.group(2))
        m = re.match(r"\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,", _t)
        if m:
            _macros.setdefault(m.group(1), m.group(2))


def ev(expr, names=None):
    names = names or {}
    expr = expr.strip()
    tree = ast.parse(expr.replace("BIT(", "_BIT("), mode="eval")

    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in names:
                return names[n.id]
            if n.id in _macros:
                return ev(_macros[n.id])
            raise SystemExit(f"unknown name {n.id} in {expr!r}")
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Invert):
            return ~walk(n.operand) & FULL
        if isinstance(n, ast.BinOp):
            a, b = walk(n.left), walk(n.right)
            return {ast.LShift: lambda: a << b, ast.BitOr: lambda: a | b, ast.BitAnd: lambda: a & b, ast.Add: lambda: a + b}[type(n.op)]()
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_BIT":
            return 1 << walk(n.args[0])
        raise SystemExit(f"cannot evaluate {expr!r}")
    return walk(tree)


# ---- device tree facts ----
def dt_blocks():
    """pcie_1 reg block bases from gs101-pcie.dtsi, by the comment names."""
    txt = DT_PCIE.read_text()
    a = txt.index("pcie_1:")
    seg = txt[a:txt.index("};", a)]
    out = {}
    for base, size, name in re.findall(r"0x0\s+(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)\s*(?:>;)?\s*/\*\s*([^*]+?)\s*\*/", seg):
        out[name.strip()] = int(base, 16)
    return out


def check_dt():
    com = DT_COMMON.read_text()
    a = com.index("&pcie_1 {")
    seg = com[a:com.index("};", a)]
    assert "num-lanes = <1>" in seg and 'use-ia = "true"' in seg and "EP_BCM_WIFI" in seg and 'use-phy-isol-en = "true"' in seg, "pcie_1 DT facts changed"
    assert "ip-ver = <0x984500>" in DT_PCIE.read_text()
    soc = json.loads(SOC.read_text())["wlan"]
    assert re.search(r"0x0\s+0x14800000\s+0x301000\s*/\*\s*DBI base", DT_PCIE.read_text()), "pcie_1 DBI reg is no longer 0x14800000 size 0x301000"
    assert int(soc["atu"], 0) == int(soc["dbi"], 0) + 0x300000, "soc.json wlan atu must be dbi + 0x300000 (pcie-designware.h:202 DEFAULT_DBI_ATU_OFFSET = 0x3 << 20; inside the 0x301000 DBI reg)"
    assert re.search(r"0x0\s+0x60FFE000\s+0x2000\s*/\*\s*configuration space", DT_PCIE.read_text()), "pcie_1 config reg is no longer 0x60FFE000 size 0x2000"
    b = dt_blocks()
    assert int(soc["cfg"], 0) == b["configuration space"] == 0x60FFE000, "soc.json wlan cfg must be the pcie_1 'config' reg 0x60FFE000 (pcie.dtsi:62; pcie_0 has 0x40FFE000 at :20)"
    want = {"elbi base": soc["elbi"], "phy base": soc["pma"], "sysreg base": soc["sysreg"], "DBI base": soc["dbi"], "phy pcs base": soc["pcs"], "I/A space": soc["ia"]}
    for k, v in want.items():
        assert b[k] == int(v, 0), f"soc.json wlan {k} {v} != pcie.dtsi {b[k]:#x}"
    return b


# ---- C statement parsing for the cal.c tables ----
def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def function_body(path, name):
    """(first_line, text) of the function body, comments kept as /*...*/ markers for the step splitting."""
    ls = lines(path)
    start = next(i for i, t in enumerate(ls) if re.match(r"(?:void|int|static\s+\w+)\s+\**" + name + r"\s*\(", t))
    depth, i, seen = 0, start, False
    body = []
    while True:
        for ch in ls[i]:
            if ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
        body.append(ls[i])
        i += 1
        if seen and depth == 0:
            break
    return start + 1, body


BASES = {"phy_base_regs": "PMA", "phy_pcs_base_regs": "PCS", "elbi_base_regs": "ELBI"}


def cal_ops(path, fname, markers=None):
    """Ordered ops of a cal.c function for ONE lane (pcie_1: num-lanes = 1, checked in check_dt): ('write', blk, off, val, line), ('rmw', blk, off, mask, val, first_line, last_line),
    ('delay_us', us, line), and ('marker', text, line) for the top-level comments listed in `markers`. Any statement it does not understand is an error."""
    first, body = function_body(path, fname)
    ops, pending = [], None
    depth, skip_until, skip_stmt, in_comment = 0, None, False, False
    for idx, raw in enumerate(body):
        ln = first + idx
        t = raw.strip()
        if in_comment:
            in_comment = "*/" not in t
            continue
        if t.startswith("/*") and "*/" not in t:
            in_comment = True
            continue
        mc = re.fullmatch(r"/\*\s*(.*?)\s*\*/", t)
        if mc:
            if markers and depth == 1 and skip_until is None:
                for mk in markers:
                    if mk in mc.group(1):
                        ops.append(("marker", mk, ln))
            continue
        t = re.sub(r"/\*.*?\*/", "", t).strip()
        if not t:
            continue
        opens, closes = t.count("{"), t.count("}")
        if skip_until is not None:
            depth += opens - closes
            if depth <= skip_until:
                skip_until = None
            continue
        if skip_stmt:
            skip_stmt = not t.endswith(";")
            continue
        if re.match(r"if\s*\(\s*num_lanes\s*==\s*2\s*\)", t):
            if "{" in t:
                skip_until = depth
                depth += opens - closes
            elif not t.endswith(";") or re.fullmatch(r"if\s*\(\s*num_lanes\s*==\s*2\s*\)", t):
                skip_stmt = True
            continue
        depth += opens - closes
        if t in ("{", "}") or re.match(r"(void|int)\s+\w+\s*\(", t) or re.match(r"for\s*\(\s*i\s*=\s*0\s*;\s*i\s*<\s*num_lanes\s*;\s*i\+\+\s*\)", t):
            continue
        if t.startswith(("phy_base_regs += (i * 0x800);", "phy_base_regs = exynos_pcie->phy_base;")):
            continue                # lane 0: i * 0x800 = 0
        if re.match(r"(void __iomem|int|u32|struct)\b.*;$", t):
            continue
        if t.startswith(("pr_debug", "dev_info")):
            skip_stmt = not t.endswith(";")
            continue
        m = re.match(r"val\s*=\s*readl\((?:exynos_pcie->(phy_base|phy_pcs_base)|(\w+))\s*\+\s*(0x[0-9A-Fa-f]+)\);", t)
        if m:
            pbase = {"phy_base": "PMA", "phy_pcs_base": "PCS"}.get(m.group(1)) or BASES[m.group(2)]
            pending = {"blk": pbase, "off": int(m.group(3), 16), "set": 0, "clr": 0, "line": ln}
            continue
        m = re.match(r"val\s*(\|=|&=)\s*(.+);", t)
        if m and pending:
            x = ev(m.group(2))
            if m.group(1) == "|=":
                pending["set"] |= x
                pending["clr"] &= ~x
            else:
                cleared = (~x) & FULL
                pending["clr"] |= cleared
                pending["set"] &= ~cleared
            continue
        m = re.match(r"writel\((\w+),\s*(?:exynos_pcie->(phy_base|phy_pcs_base)|(\w+))\s*\+\s*(0x[0-9A-Fa-f]+)\);", t)
        if m:
            blk = {"phy_base": "PMA", "phy_pcs_base": "PCS"}.get(m.group(2)) or BASES[m.group(3)]
            off = int(m.group(4), 16)
            if m.group(1) == "val":
                assert pending and pending["blk"] == blk and pending["off"] == off, f"{path.name}:{ln}: writel(val) without the matching readl"
                ops.append(("rmw", blk, off, (pending["set"] | pending["clr"]) & FULL, pending["set"] & FULL, pending["line"], ln))
                pending = None
            else:
                ops.append(("write", blk, off, int(m.group(1), 16), ln))
            continue
        m = re.match(r"(u|m)delay\((\d+)\);", t)
        if m:
            ops.append(("delay_us", int(m.group(2)) * (1 if m.group(1) == "u" else 1000), ln))
            continue
        raise SystemExit(f"{path.name}:{ln}: unparsed statement {t!r}")
    assert pending is None and depth == 0 and skip_until is None and not skip_stmt, (pending, depth)
    return ops


def ia_ops():
    """exynos_pcie_rc_use_ia(): exynos_elbi_write / exynos_ia_write statements, in order."""
    first, body = function_body(RC, "exynos_pcie_rc_use_ia")
    b = dt_blocks()
    sym = {"exynos_pcie->elbi_base_physical_addr": b["elbi base"], "exynos_pcie->phy_base_physical_addr": b["phy base"], "exynos_pcie->ia_base_physical_addr": b["I/A space"]}
    out = []
    for i, t in enumerate(body):
        m = re.match(r"\s*exynos_(elbi|ia)_write\(exynos_pcie,\s*(.+?),\s*(0x[0-9A-Fa-f]+)\);", t)
        if not m:
            continue
        v = m.group(2).strip()
        val = sym[v] if v in sym else int(v, 16)
        out.append(("write", "ELBI" if m.group(1) == "elbi" else "IA", int(m.group(3), 16), val, first + i))
    return out


# ---- the table ----
rows, tear, unknown, keymap = [], [], [], {}
STEP_NAMES = {
    0: "preconditions (reads only: the start state the run expects)",
    1: "PERST output low", 2: "WLAN_EN output low", 3: "WLAN_EN high", 4: "wait for the module", 5: "PHY isolation release (EL3 RMW, audited helper)",
    6: "clocks: none in the clock framework", 7: "pinctrl 'active' state", 8: "PHY all-power-down clear", 9: "initial OC status read", 10: "PHY config A (input clk path, PCS mux W/A)",
    11: "PHY reset pulse (ELBI)", 12: "PHY config B (PMA/PCS tables)", 13: "PHY reset release and extras", 14: "I/A (CDR lock helper)", 15: "PHY lock waits (PLL, CDR, offset calibration)",
    16: "soft power reset", 17: "ELBI device type", 18: "non-sticky reset", 19: "DBI 'EQ off'", 20: "PERST high  <-- the first step that lets the endpoint out of reset",
    21: "L1 / Q-channel / NAK options", 22: "RC setup (dw_pcie_setup_rc + exynos_pcie_setup_rc)  DELIBERATELY SKIPPED (one 0 us delay row; was UNKNOWN)", 23: "LTSSM enable and wait for L0",
    24: "READ-ONLY link report: negotiated link + capability chain of the RC, from its own DBI space (explicit --wlan-link=24 only; no writes)",
    25: "READ-ONLY iATU layout (doc 31 Q2): DBI header/version/viewport words + the unrolled outbound region 0 registers at DBI+0x300000 (explicit --wlan-link=25 only; no writes)",
    26: "iATU outbound region 0 = CFG0 -> bus 1 (7 DBI-window writes at ATU 0x14B00000, each read back, CR2 re-checked once) and 4 reads of the CFG window 0x60FFE000 (explicit --wlan-link=26 only; ATU torn down FIRST)",
    27: "READ-ONLY dump of the endpoint config space (56 dwords of bus 1 dev 0 fn 0 through the CFG window left enabled by S26) + the iATU outbound region 1 reset values (7 reads); 0x8C-0xA8 deliberately not read (0xA4 PCI_BACKPLANE_DATA is a backplane access, 0x90/0x98 interrupt/mailbox), 0xAC kept; explicit --wlan-link=27 only; no write",
}


def add(step, kind, blk=None, off=0, mask=FULL, value=0, us=0, polls=0, cite="", tag=TAG_SRC, note=None, restore=None, probe=False, log=False):
    assert kind in KINDS and (blk is None or blk in BLOCKS) and step in STEP_NAMES
    r = {"id": len(rows), "step": step, "kind": kind, "blk": blk, "off": off, "mask": mask & FULL, "value": value & FULL, "delay_us": us, "polls": polls, "restore": restore, "cite": cite, "tag": tag}
    if probe:
        r["probe"] = True
    if log:
        assert kind == "read_only" and not probe, "log is for non-probe read_only rows"
        r["log"] = True
    if note:
        r["note"] = note
    rows.append(r)
    return r


def add_t(requires, kind, blk=None, off=0, mask=FULL, value=0, us=0, polls=0, cite="", tag=TAG_SRC, note=None, key=None):
    t = {"id": f"T{len(tear) + 1:02d}", "requires": requires, "kind": kind, "blk": blk, "off": off, "mask": mask & FULL, "value": value & FULL, "delay_us": us, "polls": polls, "cite": cite, "tag": tag}
    if note:
        t["note"] = note
    tear.append(t)
    if key and key not in keymap:
        keymap[key] = t["id"]
    return t["id"]


def pin_regs():
    """Samsung exynos850_bank_type_off register layout (linux-pinctrl-exynos-arm64.c:47-50): offsets and field widths."""
    txt = (REFS / "linux-pinctrl-exynos-arm64.c").read_text()
    m = re.search(r"exynos850_bank_type_off\s*=\s*\{\s*\.fld_width\s*=\s*\{\s*([\d, ]+),\s*\},\s*\.reg_offset\s*=\s*\{\s*([^}]+),\s*\},", txt)
    widths = [int(x) for x in m.group(1).replace(" ", "").strip(",").split(",")]
    offs = [int(x, 16) for x in m.group(2).replace(" ", "").strip(",").split(",")]
    return dict(zip(("CON", "DAT", "PUD", "DRV", "CONPDN", "PUDPDN"), zip(offs, widths)))


def dt_node(path, name):
    """(properties, line) of `name: name { samsung,KEY = <tok>; ... };`"""
    txt = path.read_text()
    m = re.search(re.escape(name) + r":\s*" + re.escape(name) + r"\s*\{(.*?)\};", txt, re.S)
    assert m, name
    props = {k: v.strip() for k, v in re.findall(r"samsung,([\w-]+)\s*=\s*<([^>]+)>;", m.group(1))}
    return props, txt.count("\n", 0, m.start()) + 1


def dt_value(tok):
    tok = tok.strip()
    consts = {}
    for p in (SAMSUNG_H, DT_PINH):
        for a, b in re.findall(r"#define\s+(\w+)\s+(\w+)", p.read_text()):
            consts[a] = b

    def res(t):
        t = t.strip()
        if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", t):
            return int(t, 0)
        return res(consts[t])
    return res(tok)


def build():
    EL = find_line(RC, r"^static int exynos_pcie_rc_establish_link")
    soc = json.loads(SOC.read_text())["wlan"]
    check_dt()
    pr = pin_regs()
    stp = {}
    pcie = dt_blocks()
    # ---- S00 preconditions ----
    cdt = anchor(DT_COMMON, find_line(DT_COMMON, r"pcie,wlan-gpio = <&gpp14 2"), r"gpp14 2")
    pdt = anchor(DT_PCIE, find_line(DT_PCIE, r"gpios = <&gph2 0 0x1"), r"gph2 0")
    obs = "run w1/wlanen1: WLAN_EN input low, PERST input low (docs/research/29 sections 11, 13)"
    add(0, "wait_eq", "GPP14", pr["DAT"][0], 1 << 2, 0, polls=1, cite=cdt, tag="observed", note="WLAN_EN gpp14-2 reads low; " + obs)
    add(0, "wait_eq", "GPP14", pr["CON"][0], 0xF << 8, 0, polls=1, cite=cdt, tag="observed", note="WLAN_EN CON field 0 = input (the pin config we restore to)")
    add(0, "wait_eq", "GPH2", pr["DAT"][0], 1, 0, polls=1, cite=pdt, tag="observed", note="PERST gph2-0 reads low")
    add(0, "wait_eq", "GPH2", pr["CON"][0], 0xFF, 0, polls=1, cite=pdt, tag="observed", note="PERST (pin 0) and CLKREQ (pin 1) CON fields 0 = input (the pin config we restore to)")
    add(0, "wait_eq", "PMU", 0x3EC4, 1, 0, polls=1, cite=anchor(RCH, find_line(RCH, r"define PCIE_PHY_CONTROL_MASK"), r"0x1"), tag="observed", note="PHY isolation word 0x17463EC4 bit 0 = 0 (isolated), run w1/iso1")
    add(0, "read_only", "PMU", 0x3EC0, cite=anchor(DT_PCIE, find_line(DT_PCIE, r"pmu-offset = <0x3ec0>"), r"0x3ec0"), note="PCIe0 isolation word, logged")

    # ---- S01..S04 gpio ----
    l = find_line(RC, r"devm_gpio_request_one\(dev, exynos_pcie->perst_gpio")
    c01 = anchor(RC, l + 1, r"GPIOF_OUT_INIT_LOW")
    add(1, "gpio_dat", "GPH2", pr["DAT"][0], 1 << 0, 0, cite=c01, note="gpio_request_one OUT_INIT_LOW: DAT first (pinctrl-samsung.c:693-696)", restore="perst_low")
    add(1, "gpio_con", "GPH2", pr["CON"][0], 0xF << 0, 1 << 0, cite=c01, note="then the CON field := OUTPUT (1)", restore="perst_in")
    l = find_line(RC, r"gpio_direction_output\(exynos_pcie->wlan_gpio, 0\)")
    c02 = anchor(RC, l, r"gpio_direction_output")
    add(2, "gpio_dat", "GPP14", pr["DAT"][0], 1 << 2, 0, cite=c02, restore="wlan_low")
    add(2, "gpio_con", "GPP14", pr["CON"][0], 0xF << 8, 1 << 8, cite=c02, restore="wlan_in")
    l = find_line(DHD, r"gpio_direction_output\(wlan_reg_on, 1\)", 595)
    add(3, "gpio_dat", "GPP14", pr["DAT"][0], 1 << 2, 1 << 2, cite=anchor(DHD, l, r"wlan_reg_on, 1"), restore="wlan_low")
    lt = find_line(DHD, r"msleep\(WIFI_TURNON_DELAY\)")
    add(4, "delay", us=200000, cite=anchor(DHD, lt, r"WIFI_TURNON_DELAY") + "; dhd.h:3137-3140 (200 ms)")
    add(5, "smc_iso", "PMU", 0x3EC4, 1, 1, cite=anchor(RC, find_line(RC, r"exynos_pcie_phy_isolation\(exynos_pcie,\s*PCIE_PHY_BYPASS\)", 3380), r"PCIE_PHY_BYPASS"),
        note="EL3 RMW through the audited helper (x1=0x17463EC4 x2=2 x3=1 x4=1); the rc.c:393-407 call", restore="iso_clear")
    add(6, "delay", us=0, cite=anchor(DT_PCIE, find_line(DT_PCIE, r"pcie-clk-num = <0>"), r"pcie-clk-num"), note="no clk-framework clocks: pcie-clk-num = phy-clk-num = 0 (rc.c:1212-1248)")

    # ---- S07 pinctrl active state (DT -> registers) ----
    def pins(path, node, bank_blk, pin, order):
        props, line = dt_node(path, node)
        out = []
        for key, reg in order:
            if key in props:
                v = dt_value(props[key])
                off, w = pr[reg]
                out.append((reg, off, ((1 << w) - 1) << (pin * w), v << (pin * w), line))
        return out
    for node, path, blk, pin in (("pcie1_perst", DT_PINCTRL, "GPH2", 0), ("pcie1_clkreq", DT_PINCTRL, "GPH2", 1), ("cfg_wlanen", DT_WLAN, "GPP14", 2)):
        props, line = dt_node(path, node)
        v = dt_value(props["pin-function"])
        off, w = pr["CON"]
        c = f"{SRC_NAMES[path]}:{line}"
        add(7, "gpio_con", blk, off, ((1 << w) - 1) << (pin * w), v << (pin * w), cite=c, note=f"{node}: pinctrl 'active' mux (pcie_1 pinctrl-0, common-wlan.dtsi:143)", restore={"pcie1_clkreq": "clkreq_in"}.get(node))
    for node, path, blk, pin in (("pcie1_clkreq", DT_PINCTRL, "GPH2", 1), ("pcie1_perst", DT_PINCTRL, "GPH2", 0)):  # M1 (Opus review): no WLAN_EN pad-config rows (pud/drv/conpdn), the pad keeps its S2 state
        for reg, off, mask, val, line in pins(path, node, blk, pin, (("pin-pud", "PUD"), ("pin-drv", "DRV"), ("pin-con-pdn", "CONPDN"), ("pin-pud-pdn", "PUDPDN"))):
            add(7, "mmio_rmw", blk, off, mask, val, cite=f"{SRC_NAMES[path]}:{line}", note=f"{node} {reg} (pinctrl-samsung.c:437-488 writes the raw DT value into the field)")

    # ---- S08 PHY all-power-down clear (cal.c:69-96) ----
    ops = cal_ops(CAL, "exynos_pcie_rc_phy_all_pwrdn_clear")
    add(8, "read_only", "PMA", 0x3F0, cite=anchor(RC, find_line(RC, r"0x03F0, BIT\(3\)"), r"0x03F0"), probe=True, tag=TAG_GUESS, note="BLOCK PROBE: the first access to the PMA; Linux reads this register (PLL lock). Unreadable = stop")
    for op in ops:
        c = f"cal.c:{op[-1]}"
        if op[0] == "write":
            add(8, "mmio_write", op[1], op[2], FULL, op[3], cite=c, restore="phy_pwrdn")
        elif op[0] == "delay_us":
            add(8, "delay", us=op[1], cite=c)
    # ---- S09 ----
    add(9, "read_only", "PMA", 0x5FC, cite=anchor(RC, find_line(RC, r"OC Initial Status check"), r"OC Initial") + " (0x5FC at the next line)", note="logged only")
    # ---- S10..S13 phy_config (cal.c:106-322) ----
    ops = cal_ops(CAL, "exynos_pcie_rc_pcie_phy_config", markers=["PHY CMN_RST, INIT_RST, PORT_RST Assert", "pma_setting", "PHY CMN_RST, PORT_RST Release"])
    step = 10
    probes = {10: ("PCS", 0x008, "the first access to the PCS (the phy_config RMW reads it)"), 11: ("ELBI", 0x2C8, "the first access to the ELBI (RDLH_LINKUP, rc.c:3233)")}
    started = set()
    for op in ops:
        if op[0] == "marker":
            step = {"PHY CMN_RST, INIT_RST, PORT_RST Assert": 11, "pma_setting": 12, "PHY CMN_RST, PORT_RST Release": 13}[op[1]]
            continue
        if step in probes and step not in started:
            started.add(step)
            blk, off, why = probes[step]
            add(step, "read_only", blk, off, cite=f"cal.c:{op[-1]}", probe=True, tag=TAG_GUESS, note="BLOCK PROBE: " + why + ". Unreadable = stop")
        c = f"cal.c:{op[-1]}"
        if op[0] == "write":
            add(step, "mmio_write", op[1], op[2], FULL, op[3], cite=c, restore="phy_pwrdn")
        elif op[0] == "rmw":
            add(step, "mmio_rmw", op[1], op[2], op[3], op[4], cite=f"cal.c:{op[5]}-{op[6]}", restore="phy_pwrdn")
        else:
            add(step, "delay", us=op[1], cite=c)
    # ---- S14 I/A ----
    iops = ia_ops()
    assert iops[0][1] == "ELBI" and iops[0][2] == 0x388
    add(14, "mmio_write", "ELBI", 0x388, FULL, iops[0][3], cite=f"rc.c:{iops[0][4]}", restore="phy_pwrdn")
    add(14, "read_only", "IA", 0x000, cite=f"rc.c:{iops[-1][4]}", probe=True, tag=TAG_GUESS, note="BLOCK PROBE: the first access to the I/A block; Linux only WRITES it (IA_EN = the last write), so readability is a guess. Unreadable = stop")
    for op in iops[1:]:
        add(14, "mmio_write", "IA", op[2], FULL, op[3], cite=f"rc.c:{op[4]}", restore="phy_pwrdn")
    # ---- S15 waits ----
    l0 = find_line(RC, r"check pll lock", EL)
    for name, rx, mask in (("PLL", r"0x03F0", None), ("CDR", r"0x0E0C", None), ("OC", r"0x0DE8", None)):
        l = find_line(RC, rx, l0)
        m = re.search(r"(0x[0-9A-Fa-f]+),\s*(BIT\(\d+\)|0x[0-9a-f]+),\s*(BIT\(\d+\)|0x[0-9a-f]+),", " ".join(lines(RC)[l - 1:l + 1]))
        off, msk, val = int(m.group(1), 16), ev(m.group(2)), ev(m.group(3))
        assert msk == val
        stl = find_line(RC, r"for \(i = 0; i < 10000; i\+\+\)", find_line(RC, r"^static int check_exynos_pcie_reg_status"))
        add(15, "wait_mask", "PMA", off, msk, val, us=1, polls=10000, cite=f"rc.c:{l} (loop rc.c:{stl}-{stl + 8}: 10000 x udelay(1))", note=f"{name}; Linux only logs a failure and goes on, the shim STOPS (later steps need the lock)")
    # ---- S16 soft power reset (rc.c:3143-3152) ----
    spr = ev("SOFT_PWR_RESET")
    ls = find_line(RC, r"Soft Power RST", EL)
    add(16, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), spr, 0, cite=anchor(RC, ls + 2, r"val &= ~SOFT_PWR_RESET"), restore="soft_reset")
    add(16, "delay", us=1000, cite=anchor(RC, ls + 5, r"mdelay\(1\)"))
    add(16, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), spr, spr, cite=anchor(RC, ls + 6, r"val \|= SOFT_PWR_RESET"), restore="soft_reset")
    add(16, "read_only", "PMA", 0xE18, cite=anchor(RC, ls + 9, r"0x0E18"), note="OC ok after the soft power reset: bit 7; logged only")
    # ---- S17 ----
    l = find_line(RC, r"exynos_elbi_write\(exynos_pcie, 0x04, 0x80\)", EL)
    add(17, "mmio_write", "ELBI", 0x80, FULL, 0x04, cite=anchor(RC, l, r"0x04, 0x80"), restore="soft_reset", note="DEVICE_TYPE of the sub controller = RC")
    # ---- S18 non-sticky (rc.c:3164-3173) ----
    ns = ev("SOFT_NON_STICKY_RESET")
    l = find_line(RC, r"NON-sticky RST", EL)
    add(18, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), ns, ns, cite=anchor(RC, l + 2, r"val \|= SOFT_NON_STICKY_RESET"), restore="soft_reset")
    add(18, "delay", us=12, cite=anchor(RC, l + 4, r"usleep_range\(10, 12\)"), note="upper bound of usleep_range(10, 12)")
    add(18, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), ns, 0, cite=anchor(RC, l + 5, r"val &= ~SOFT_NON_STICKY_RESET"), restore="soft_reset")
    add(18, "delay", us=1000, cite=anchor(RC, l + 7, r"mdelay\(1\)"))
    add(18, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), ns, ns, cite=anchor(RC, l + 8, r"val \|= SOFT_NON_STICKY_RESET"), restore="soft_reset")
    # ---- S19 ----
    l = find_line(RC, r"exynos_pcie_rc_wr_own_conf\(pp, 0x890, 4, 0x12000\)", EL)
    add(19, "read_only", "DBI", 0x000, cite=f"rc.c:{l}", probe=True, tag=TAG_GUESS, note="BLOCK PROBE: the first access to the DBI (vendor/device id word); unreadable = stop. Linux's DBI access is refused while the PHY is isolated (rc.c:1323-1324)")
    add(19, "mmio_write", "DBI", 0x890, FULL, 0x12000, cite=anchor(RC, l, r"0x890, 4, 0x12000"), note="EQ off", restore="soft_reset")
    # ---- S20 PERST high ----
    l = find_line(RC, r"gpio_set_value\(exynos_pcie->perst_gpio, 1\)", EL)
    add(20, "gpio_dat", "GPH2", pr["DAT"][0], 1, 1, cite=anchor(RC, l, r"perst_gpio, 1"), restore="perst_low")
    lu = find_line(RC, r"usleep_range\(20000, 22000\)", EL)
    add(20, "delay", us=22000, cite=anchor(RC, lu, r"20000, 22000") + " (EP_BCM_WIFI branch rc.c:3183)", note="upper bound of usleep_range(20000, 22000)")
    # ---- S21 options (rc.c:3189-3213) ----
    lq = find_line(RC, r"val = exynos_elbi_read\(exynos_pcie, PCIE_APP_REQ_EXIT_L1_MODE\)", EL)
    add(21, "mmio_rmw", "ELBI", ev("PCIE_APP_REQ_EXIT_L1_MODE"), ev("APP_REQ_EXIT_L1_MODE") | ev("L1_REQ_NAK_CONTROL_MASTER"), ev("APP_REQ_EXIT_L1_MODE") | ev("L1_REQ_NAK_CONTROL_MASTER"), cite=f"rc.c:{lq}-{lq + 3}", restore="soft_reset")
    add(21, "mmio_write", "ELBI", ev("PCIE_LINKDOWN_RST_CTRL_SEL"), FULL, ev("PCIE_LINKDOWN_RST_MANUAL"), cite=anchor(RC, lq + 4, r"PCIE_LINKDOWN_RST_MANUAL, PCIE_LINKDOWN_RST_CTRL_SEL"), restore="soft_reset")
    lc = find_line(RC, r"val = exynos_elbi_read\(exynos_pcie, PCIE_QCH_SEL\)", EL)
    anchor(RC, lc + 1, r"ip_ver >= 0x889500")
    add(21, "mmio_rmw", "ELBI", ev("PCIE_QCH_SEL"), ev("CLOCK_GATING_PMU_MASK") | ev("CLOCK_GATING_APB_MASK") | ev("CLOCK_GATING_AXI_MASK"), 0, cite=f"rc.c:{lc + 1}-{lc + 7} (ip_ver 0x984500 >= 0x889500, pcie.dtsi:77)", restore="soft_reset")
    add(21, "mmio_write", "ELBI", ev("PCIE_MSTR_PEND_SEL_NAK"), FULL, ev("NACK_ENABLE"), cite=anchor(RC, find_line(RC, r"exynos_elbi_write\(exynos_pcie, NACK_ENABLE", EL), r"NACK_ENABLE"), restore="soft_reset")
    add(21, "mmio_write", "ELBI", ev("PCIE_DBI_L1_EXIT_DISABLE"), FULL, ev("DBI_L1_EXIT_DISABLE"), cite=anchor(RC, find_line(RC, r"exynos_elbi_write\(exynos_pcie, DBI_L1_EXIT_DISABLE", EL), r"DBI_L1_EXIT_DISABLE"), restore="soft_reset")
    # ---- S22: DOCUMENTED SKIP (driver decision 2026-10-23) ----
    # The step exists with exactly ONE delay row (0 us, no write): the interpreter refuses a step without rows, and S23 must stay reachable. The blocker text below is kept in `unknown`.
    add(22, "delay", us=0, cite=anchor(RC, find_line(RC, r"dw_pcie_setup_rc\(pp\);", EL), r"dw_pcie_setup_rc") + " (call site; exynos_pcie_setup_rc on the next line)", tag=TAG_GUESS,
        note="S22 deliberately skipped: RC config-space setup not transcribed (capability offset and MSI address are runtime values, see unknown text); not required for LTSSM link training; "
             "speed target stays at the hardware default; the endpoint is NOT enumerable until S22 exists")
    unknown.append({"step": 22, "what": "dw_pcie_setup_rc() (generic DWC host code, rc.c:3216) and the DBI writes of exynos_pcie_setup_rc() (rc.c:2720-2790)",
                    "why": "SOURCE NOW READ (dwc-generic, android14-6.1) but S22 stays UNKNOWN (and is SKIPPED, see its delay row): it needs runtime values. "
                           "(1) dw_pcie_msi_init() (pcie-designware-host.c:273-284, called at :740) writes DBI 0x820/0x824 (PCIE_MSI_ADDR_LO/HI, rc.h:206-207) with pp->msi_data, a dmam_alloc_coherent() DMA address (pcie-designware-host.c:383-391); "
                           "it runs because pci_msi_enabled() is true by default, no msi_host_init/msi-parent/msi-map exists (rc.c:2805-2807, dts), so has_msi_ctrl is true (:462); a non-constant, and the Exynos driver rewrites it itself (rc.c:2982-2983). "
                           "(2) dw_pcie_setup() -> dw_pcie_link_set_max_speed() (pcie-designware.c:489-521, link_gen = DT max-link-speed GEN2 from common.dtsi:373 via :449) addresses LNKCAP/LNKCTL2 at dw_pcie_find_capability(PCI_CAP_ID_EXP)+off, a DBI cap-list walk (:73-82) whose result is read, not constant (expected 0x70 = PCIE_CAP_OFFSET, rc.h:124: a guess until measured). "
                           "(3) the numeric LINK_SPEED_GEN2 (dt-bindings) and PCI_VENDOR_ID_SAMSUNG (linux pci_ids.h) defines are not in the fetched refs. "
                           "(4) every Linux DBI access on this path is bracketed by clock enable + PCS rx-elecidle ignore/enable (rd_own_conf rc.c:1269-1312, wr_own_conf rc.c:1314-1356), i.e. extra PCS writes the shim would have to replay. "
                           "Resolved for later (read from source): iATU skipped (child_ops = exynos ops, rc.c:2800, vs pcie-designware-host.c:770); N_FTS and cdm-check skipped (not set / not in dts); num-lanes=1 (common.dtsi:368) gives PCIE_PORT_LINK_CONTROL(0x710) RMW and 0x80C width=1; "
                           "dbi_ro_wr_en/dis = RMW of 0x8BC bit0 (pcie-designware.h:390-410); exynos_pcie_rc_wr/rd_own_conf = rc_dbi_base + where, refused while phy_control == PCIE_PHY_ISOLATION (rd :1278, wr :1323-1324). Details: docs/research/29 section 14, S22 row."})
    # ---- S23 ----
    add(23, "mmio_write", "ELBI", ev("PCIE_APP_LTSSM_ENABLE"), FULL, ev("PCIE_ELBI_LTSSM_ENABLE"), cite=anchor(RC, find_line(RC, r"PCIE_ELBI_LTSSM_ENABLE, PCIE_APP_LTSSM_ENABLE", EL), r"LTSSM_ENABLE"), restore="ltssm_off")
    lw = find_line(RC, r"while \(count < MAX_TIMEOUT\)", EL)
    add(23, "wait_eq", "ELBI", ev("PCIE_ELBI_RDLH_LINKUP"), ev("PCIE_ELBI_LTSSM_STATE_MASK"), ev("S_L0"), us=12, polls=ev("MAX_TIMEOUT"), cite=f"rc.c:{lw}-{lw + 9} (MAX_TIMEOUT common.h:20 polls of usleep_range(10, 12))", note="LTSSM == L0")

    # ---- S24: READ-ONLY link report (explicit --wlan-link=24 only). Every row: DBI/ELBI read, logged as `LINK R24 row=<i> rd=0x..`. No write, no teardown row. DBI was probed at S19. ----
    PCI_REGS = "linux/pci_regs.h"
    GEN = REFS / "dwc-generic"

    def gc(path, rx, start=1):
        n = find_line(path, rx, start)
        return f"{SRC_NAMES[path] if path == PCIR else path.parent.name + chr(47) + path.name}:{n}"	# PCIR keeps its old cite name linux/pci_regs.h
    own = gc(RC, r"rd_own_conf\(pp, PCI_VENDOR_ID, 4, &val\)", 3400)
    pcc = gc(RC, r"rc_dbi_base \+ PCI_CAPABILITY_LIST")
    pmo, pco = gc(RCH, r"define PM_CAP_OFFSET\s+0x40"), gc(RCH, r"define PCIE_CAP_OFFSET\s+0x70")
    use_pm, use_pc = gc(RC, r"u32 pm_cap_off = PM_CAP_OFFSET"), gc(RC, r"u32 pcie_cap_off = PCIE_CAP_OFFSET")
    lnkcap, lnkctl2 = gc(RC, r"pcie_cap_off \+ PCI_EXP_LNKCAP, 4, &val", 2700), gc(RC, r"pcie_cap_off \+ PCI_EXP_LNKCTL2, 4, &val", 2700)
    lnkctl = gc(RC, r"exp_cap_off \+ PCI_EXP_LNKCTL,", 3837)
    log = dict(log=True)
    add(24, "read_only", "DBI", 0x000, cite=f"{own} (PCI_VENDOR_ID = 0x00, {PCI_REGS}:38)", note="vendor/device id word (driver writes Samsung/Exynos ids there, rc.c:2738-2740); S19 read 0xA5A5144D (observed)", **log)
    add(24, "read_only", "DBI", 0x004, cite=f"{gc(GEN / 'pcie-designware-host.c', r'dw_pcie_readl_dbi\(pci, PCI_COMMAND\)')} (PCI_COMMAND = 0x04, {PCI_REGS}:40; status in the high half, :53)", note="command/status", **log)
    add(24, "read_only", "DBI", 0x008, cite=f"{gc(REFS / 'dwc' / 'pcie-exynos-rc.c', r'where == PCI_CLASS_REVISION')} (PCI_CLASS_REVISION = 0x08, {PCI_REGS}:71)", note="class (high 24 bits; expect 0x0604xx = PCI bridge) / revision", **log)
    add(24, "read_only", "DBI", 0x034, cite=f"{pcc} (PCI_CAPABILITY_LIST = 0x34, {PCI_REGS}:122)", note="capability pointer (low byte): the runtime start of the chain S22 needs", **log)
    add(24, "read_only", "DBI", 0x040, cite=f"{pmo}, used {use_pm}", tag=TAG_GUESS, note="expected PM capability header (id 0x01, next at byte 1): driver constant, not yet measured", **log)
    add(24, "read_only", "DBI", 0x070, cite=f"{pco}, used {use_pc}", tag=TAG_GUESS, note="expected PCIe capability header (id 0x10): driver constant PCIE_CAP_OFFSET, not yet measured", **log)
    add(24, "read_only", "DBI", 0x07C, cite=f"{lnkcap} (0x70 + PCI_EXP_LNKCAP 0x0c, {PCI_REGS}:534)", tag=TAG_GUESS, note="expected LNKCAP (max speed [3:0], max width [9:4]); valid only if the PCIe capability is at 0x70", **log)
    add(24, "read_only", "DBI", 0x080, cite=f"{lnkctl} (0x70 + PCI_EXP_LNKCTL 0x10, {PCI_REGS}:553; LNKSTA 0x12 :566 = high half)", tag=TAG_GUESS, note="expected LNKCTL (low) / LNKSTA (high: current speed [3:0], negotiated width [9:4]); valid only if the capability is at 0x70", **log)
    add(24, "read_only", "DBI", 0x0A0, cite=f"{lnkctl2} (0x70 + PCI_EXP_LNKCTL2 0x30, {PCI_REGS}:689; LNKSTA2 0x32 :700 = high half)", tag=TAG_GUESS, note="expected LNKCTL2 (target speed [3:0]) / LNKSTA2; valid only if the capability is at 0x70", **log)
    add(24, "read_only", "DBI", 0x710, cite=f"{gc(GEN / 'pcie-designware.h', r'define PCIE_PORT_LINK_CONTROL\s+0x710')}, {gc(RCH, r'define PCIE_PORT_LINK_CONTROL\s+0x710')}", note="DWC port logic PORT_LINK_CONTROL (link mode / lane count)", **log)
    add(24, "read_only", "DBI", 0x80C, cite=f"{gc(GEN / 'pcie-designware.h', r'define PCIE_LINK_WIDTH_SPEED_CONTROL\s+0x80C')}, {gc(RCH, r'define PCIE_LINK_WIDTH_SPEED_CONTROL\s+0x80C')}", note="DWC PCIE_LINK_WIDTH_SPEED_CONTROL (GEN2_CTRL): number of lanes, speed-change bit", **log)
    add(24, "read_only", "DBI", 0x890, cite=f"rc.c:{find_line(RC, r'exynos_pcie_rc_wr_own_conf.pp, 0x890, 4, 0x12000.', EL)} (the S19 write target)", note="read back what S19 wrote (0x12000 expected)", **log)
    add(24, "read_only", "ELBI", ev("PCIE_ELBI_RDLH_LINKUP"), cite=f"{gc(RCH, r'define PCIE_ELBI_RDLH_LINKUP\s+0x2C8')}; same register S23 waits on", note="LTSSM state again (low 6 bits; 0x11 = L0)", **log)

    # ---- S25: READ-ONLY iATU layout (doc 31 Q2). New block ATU = DBI + 0x300000 (probed by a guarded read first). Every other row: read, logged. No write, no teardown row. DBI/ELBI were probed at S19. ----
    pc = lambda path, rx, start=1: gc(path, rx, start)
    dwh, dwc = GEN / "pcie-designware.h", GEN / "pcie-designware.c"
    hostc = GEN / "pcie-designware-host.c"
    ver_n, ver_t = pc(dwh, r"define PCIE_VERSION_NUMBER\s+0x8F8"), pc(dwh, r"define PCIE_VERSION_TYPE\s+0x8FC")
    vd = find_line(dwc, r"readl_dbi\(pci, PCIE_VERSION_NUMBER\)")
    vw = pc(dwh, r"define PCIE_ATU_VIEWPORT\s+0x900")
    vdet = find_line(dwc, r"val = dw_pcie_readl_dbi\(pci, PCIE_ATU_VIEWPORT\)")
    atu_off = pc(dwh, r"define DEFAULT_DBI_ATU_OFFSET \(0x3 << 20\)")
    unr = pc(dwh, r"define PCIE_ATU_UNROLL_BASE")
    add(25, "read_only", "DBI", 0x018, cite=f"{pc(hostc, r'dw_pcie_readl_dbi\(pci, PCI_PRIMARY_BUS\)')} (PCI_PRIMARY_BUS = 0x18, {PCI_REGS}:131; secondary 0x19 :132)", note="primary/secondary/subordinate bus numbers (low 24 bits); Linux sets 0x00ff0100 in dw_pcie_setup_rc", **log)
    add(25, "read_only", "DBI", 0x03C, cite=f"{pc(hostc, r'dw_pcie_readl_dbi\(pci, PCI_INTERRUPT_LINE\)')} (PCI_INTERRUPT_LINE = 0x3c, {PCI_REGS}:125; PIN :126, BRIDGE_CONTROL 0x3e :161 = high half)", note="interrupt line/pin (low half) and bridge control (high half)", **log)
    add(25, "read_only", "DBI", ev("PCIE_MISC_CONTROL"), cite=f"{gc(RCH, r'define PCIE_MISC_CONTROL\s+0x8BC')}, written {gc(RC, r'wr_own_conf.pp, PCIE_MISC_CONTROL, 4, DBI_RO_WR_EN')}", note="PCIE_MISC_CONTROL_1 (DBI_RO_WR_EN = bit 0)", **log)
    add(25, "read_only", "DBI", 0x8F8, cite=f"{ver_n}, read dwc-generic/pcie-designware.c:{vd}-{vd + 12} (dw_pcie_version_detect)", note="DWC IP version number (ASCII, e.g. 0x3532302a = 5.20a; table DW_PCIE_VER_* pcie-designware.h:24-29); zero = older than 4.70a", **log)
    add(25, "read_only", "DBI", 0x8FC, cite=f"{ver_t}, read dwc-generic/pcie-designware.c:{vd + 10}", note="DWC IP version type", **log)
    add(25, "read_only", "DBI", 0x900, cite=f"{vw}, test dwc-generic/pcie-designware.c:{vdet}-{vdet + 1} (== 0xffffffff means unrolled iATU)", note="legacy iATU viewport: 0xFFFFFFFF = unrolled ATU (the Exynos driver hard-codes unrolled offsets, rc.h:286-308)", **log)
    add(25, "read_only", "ATU", ev("PCIE_ATU_CR1_OUTBOUND0") - 0x300000, cite=f"{gc(RCH, r'define PCIE_ATU_CR1_OUTBOUND0\s+0x300000')}; ATU window = DBI + 0x300000 (DEFAULT_DBI_ATU_OFFSET = 0x3 << 20, {atu_off}), inside the 0x301000 DBI reg (pcie.dtsi:60)", probe=True, tag=TAG_GUESS, note="BLOCK PROBE: the first access to the iATU window (phys DBI + 0x300000 = 0x14B00000); unreadable = stop. That the window answers reads is a guess until measured", )
    atu_regs = (("PCIE_ATU_CR1_OUTBOUND0", "CR1 (region type; 0x4 = CFG0 once programmed)"), ("PCIE_ATU_CR2_OUTBOUND0", "CR2 (bit 31 = region enable)"), ("PCIE_ATU_LOWER_BASE_OUTBOUND0", "LOWER_BASE"),
                ("PCIE_ATU_UPPER_BASE_OUTBOUND0", "UPPER_BASE"), ("PCIE_ATU_LIMIT_OUTBOUND0", "LIMIT"), ("PCIE_ATU_LOWER_TARGET_OUTBOUND0", "LOWER_TARGET"), ("PCIE_ATU_UPPER_TARGET_OUTBOUND0", "UPPER_TARGET"))
    for mac, what in atu_regs:
        add(25, "read_only", "ATU", ev(mac) - 0x300000, cite=f"{gc(RCH, 'define ' + mac + r'\s+0x3000')} (DBI + 0x300000 + off; unroll stride 0x200 per region: {unr}-{int(unr.split(':')[1]) + 1})", note=f"outbound region 0 {what}", **log)
    add(25, "read_only", "ELBI", ev("PCIE_ELBI_RDLH_LINKUP"), cite=f"{gc(RCH, r'define PCIE_ELBI_RDLH_LINKUP\s+0x2C8')}; same register S23 waits on", note="LTSSM state again (low 6 bits; 0x11 = L0)", **log)

    # ---- S26: iATU outbound region 0 = CFG0 (doc 31 s.2.2, Linux's exynos_pcie_rc_prog_viewport_cfg0, rc.c:1361-1387 order). New block CFG = the pcie_1 'config' window (probed by a guarded read first).
    # The ATU block was probed in S25. Each write is followed by a logged read-back; CR2 (enable) is last. Every write has a restore (teardown FIRST, before PERST low). ----
    busdev = 1 << 24
    bus_c = gc(RCH, r"define EXYNOS_PCIE_ATU_BUS\(x\)\s+\(\(\(x\) & 0xff\) << 24\)")
    cfg_dt = gc(DT_PCIE, r"0x0\s+0x60FFE000\s+0x2000\s*/\* configuration space")
    cfg_base, cfg_size = 0x60FFE000, 0x2000
    wr_atu = (("PCIE_ATU_LOWER_BASE_OUTBOUND0", cfg_base, "LOWER_BASE := cfg0_base (the 'config' reg)", "atu_lbase", r"LOWER_BASE_OUTBOUND0, 4, pp->cfg0_base"),
              ("PCIE_ATU_UPPER_BASE_OUTBOUND0", 0, "UPPER_BASE := cfg0_base >> 32 = 0", "atu_ubase", r"UPPER_BASE_OUTBOUND0, 4, \(pp->cfg0_base >> 32\)"),
              ("PCIE_ATU_LIMIT_OUTBOUND0", cfg_base + cfg_size - 1, "LIMIT := cfg0_base + cfg0_size - 1 (32-bit write, no UPPER_LIMIT)", "atu_limit", r"pp->cfg0_base \+ pp->cfg0_size - 1"),
              ("PCIE_ATU_LOWER_TARGET_OUTBOUND0", busdev, f"LOWER_TARGET := busdev = BUS(1) (secondary bus 1, dev 0, fn 0; {bus_c})", "atu_ltarget", r"LOWER_TARGET_OUTBOUND0, 4, busdev"),
              ("PCIE_ATU_UPPER_TARGET_OUTBOUND0", 0, "UPPER_TARGET := 0", "atu_utarget", r"UPPER_TARGET_OUTBOUND0, 4, 0\)"),
              ("PCIE_ATU_CR1_OUTBOUND0", 4, "CR1 := PCIE_ATU_TYPE_CFG0 (0x4)", "atu_cr1", r"CR1_OUTBOUND0, 4, PCIE_ATU_TYPE_CFG0"),
              ("PCIE_ATU_CR2_OUTBOUND0", 1 << 31, "CR2 := EXYNOS_PCIE_ATU_ENABLE (bit 31): region 0 ENABLED, the last ATU write", "atu_cr2", r"CR2_OUTBOUND0, 4, EXYNOS_PCIE_ATU_ENABLE"))
    vp = find_line(RC, r"static void exynos_pcie_rc_prog_viewport_cfg0")
    for mac, val, what, key, rx in wr_atu:
        o = ev(mac) - 0x300000
        c = f"{anchor(RC, find_line(RC, rx, vp), rx)}; {gc(RCH, 'define ' + mac + r'\s+0x3000')}; offset = DBI + 0x300000 + {o:#x} (ATU block, S25)" + {
            "PCIE_ATU_CR1_OUTBOUND0": f"; value 0x4 = PCIE_ATU_TYPE_CFG0 defined at {gc(GEN / 'pcie-designware.h', r'define PCIE_ATU_TYPE_CFG0\s+0x4')} (Exynos twin {gc(RCH, r'define EXYNOS_PCIE_ATU_TYPE_CFG0\s+\(0x4')})",
            "PCIE_ATU_CR2_OUTBOUND0": f"; bit 31 = EXYNOS_PCIE_ATU_ENABLE defined at {gc(RCH, r'define EXYNOS_PCIE_ATU_ENABLE\s+\(0x1 << 31\)')}"}.get(mac, "")
        add(26, "mmio_write", "ATU", o, FULL, val, cite=c, tag=TAG_SRC, note=what + "; research 31 s.2.2", restore=key + ("_off" if key == "atu_cr2" else "_rst"))
        add(26, "read_only", "ATU", o, cite=f"read-back of the row above (doc 31 s.2.3 step 3; Linux has no read-back for region 0: rc.c:1379-1386)", tag=TAG_GUESS, note=f"read back {what.split(' ')[0]} (expect {val:#010x})", **log)
    add(26, "wait_eq", "ATU", 0x004, FULL, 1 << 31, us=0, polls=1, tag=TAG_GUESS, cite=f"{gc(RCH, r'define EXYNOS_PCIE_ATU_ENABLE\s+\(0x1 << 31\)')}; review 7 finding 2 (our own gate: Linux has no read-back for region 0, rc.c:1379-1386)",
        note="ONE-SHOT precondition before the CFG probe: ATU CR2 must read back 0x80000000 (region 0 enabled); otherwise the run stops (refused) instead of issuing an untranslated read to 0x60FFE000")
    add(26, "read_only", "CFG", 0x000, cite=f"{cfg_dt} (pcie_1 reg 'config' 0x60FFE000 size 0x2000; the Linux read is {anchor(RC, find_line(RC, r'dw_pcie_read.va_cfg_base . where, size, val.', vp), r'va_cfg_base')}, doc 31 s.2.3 step 2)",
        probe=True, tag=TAG_GUESS, note="BLOCK PROBE: the first access to the CFG window 0x60FFE000 (outbound CFG0 TLP to bus 1 dev 0 fn 0 once region 0 is enabled); unreadable = stop. That the window answers (not hangs / SError) is a guess (doc 31 Q3)")
    for o, what in ((0x00, "vendor (low half) / device (high half); vendor 0x14E4 expected (guess)"), (0x04, "command (low) / status (high)"), (0x08, "revision (low byte) / class code (high 24 bits)"), (0x0C, "cache line / latency / header type (bits 23:16) / BIST")):
        add(26, "read_only", "CFG", o, cite=f"{cfg_dt}; window offset = config register offset (rc.c:1524 va_cfg_base + where; standard PCI type-1 header layout); doc 31 s.2.3 step 4", tag=TAG_GUESS, note=f"CFG window +{o:#x}: {what}", **log)

    # ---- S27: READ-ONLY endpoint config dump (research 32 s.4.2 "step A") through the CFG window S26 left enabled (block already probed in S26), then iATU outbound region 1 reset values (ATU probed in S25).
    # Every row is a 32-bit read printed as `LINK R27 row=<i> rd=0x..`; no write, no restore, no teardown row (S26's ATU teardown covers the run). An offset is a row only if its source is cited, or it is tagged guess with the capability-walk cite (0x40-0x7C, 0xC0-0xFC).
    # 0x8C-0xA8 are DELIBERATELY NOT read: 0xA4 PCI_BACKPLANE_DATA (include_pcicfg.h:496-497) is a backplane access (a read can touch the backplane), 0x90/0x98 are interrupt/mailbox registers (:493, :495); 0xAC (candidate PCIe capability header) is kept. ----
    PCIP = PCIR
    pcr = lambda nm: gc(PCIP, r"define " + nm + r"\s")
    pcg = lambda rx: anchor(PCICFG, find_line(PCICFG, rx), rx)
    rcw = lambda nm: gc(RCH, r"define " + nm + r"\s+0x")
    ss = "research 32 s.4.2 step A"
    capw = f"{ss}: capability walk, id at +0 ({pcr('PCI_CAP_LIST_ID')}), next at +1 ({pcr('PCI_CAP_LIST_NEXT')}); whether this dword is a capability header is read off the dump, not assumed"
    cfg27 = [  # (offset, cite, tag, note)
        *[(0x10 + 4 * n, f"{pcr(f'PCI_BASE_ADDRESS_{n}')}; {ss}", TAG_SRC, f"BAR{n} as left by the chip (unsized, original value to restore later)") for n in range(6)],
        (0x28, f"{pcr('PCI_CARDBUS_CIS')}; {ss}", TAG_SRC, "CardBus CIS pointer (0 expected on PCIe: guess)"),
        (0x2C, f"{pcr('PCI_SUBSYSTEM_VENDOR_ID')}; {ss}", TAG_SRC, "subsystem vendor (low) / subsystem id (high)"),
        (0x34, f"{pcr('PCI_CAPABILITY_LIST')}; {pcg(r'define PCI_CFG_CAPPTR\s+0x34')}; {ss}", TAG_SRC, "capability pointer (low byte): head of the capability chain"),
        (0x3C, f"{pcr('PCI_INTERRUPT_LINE')}; {ss}", TAG_SRC, "interrupt line / pin (low half), min_gnt / max_lat (high half)"),
        *[(o, capw, TAG_GUESS, f"config dword {o:#x} (capability area 0x40..0x7C)") for o in range(0x40, 0x80, 4)],
        (0x80, f"{pcg(r'define\s+PCI_BAR0_WIN\s+0x80')}; {ss}", TAG_SRC, "PCI_BAR0_WIN: backplane address seen through BAR0 (reset value decides whether step C needs a write)"),
        (0x84, f"{pcg(r'define\s+PCI_BAR1_WIN\s+0x84')}; {ss}", TAG_SRC, "PCI_BAR1_WIN: backplane address seen through BAR1"),
        (0x88, f"{pcg(r'define\s+PCI_SPROM_CONTROL\s+0x88')}, PCIE_CFG_SUBSYSTEM_CONTROL on PCIe devices {pcg(r'define\s+PCIE_CFG_SUBSYSTEM_CONTROL\s+0x88')}; read by the attach code, 0xFFFFFFFF aborts attach ({anchor(DHDP, find_line(DHDP, r'val = OSL_PCI_READ_CONFIG.osh, PCI_SPROM_CONTROL'), r'PCI_SPROM_CONTROL')}-{find_line(DHDP, r'goto fail', find_line(DHDP, r'val = OSL_PCI_READ_CONFIG.osh, PCI_SPROM_CONTROL'))}: read + goto fail; {anchor(SIU, find_line(SIU, r'PCI_SPROM_CONTROL, sizeof'), r'PCI_SPROM_CONTROL')}: there it switches to SI_BUS instead); {ss}", TAG_SRC, "PCI_SPROM_CONTROL / PCIE_CFG_SUBSYSTEM_CONTROL (the attach code reads it; 0xFFFFFFFF aborts attach)"),
        (0xAC, f"{pcg(r'define\s+PCI_BAR0_WIN2\s+0xac')} is the non-Gen2 register ({anchor(AIU, find_line(AIU, r'PCI_BAR0_WIN2, 4, wrap') - 3, r'PCIE_GEN2')}-{find_line(AIU, r'PCI_BAR0_WIN2, 4, wrap')}: Gen2 cores use PCIE2_BAR0_WIN2 0x70); {pcg(r'define\s+PCIE_CFG_DEVICE_CAPABILITY\s+0xb0')}, {pcg(r'define\s+PCIE_CFG_DEVICE_CONTROL\s+0xb4')} put DEVCAP/DEVCTL at 0xB0/0xB4 in PCIE devices, so the capability header is at 0xAC; {rcw('WIFI_L1SS_LINKCTRL')} = 0xAC+0x10 ({gc(PCIP, r'define\s+PCI_EXP_LNKCTL\s')}), {rcw('WIFI_PCI_EXP_DEVCTL2')} = 0xAC+0x28 ({gc(PCIP, r'define\s+PCI_EXP_DEVCTL2\s')}); {ss}", TAG_GUESS, "PCIe capability header (cap id 0x10 expected: guess)"),
        (0xB0, f"{pcg(r'define\s+PCIE_CFG_DEVICE_CAPABILITY\s+0xb0')}; {ss}", TAG_SRC, "device capability in PCIE devices (PCIe capability + 0x04 if the capability is at 0xAC: guess)"),
        (0xB4, f"{pcg(r'define\s+PCIE_CFG_DEVICE_CONTROL\s+0xb4')}; {ss}", TAG_SRC, "device control in PCIE devices (MPS / MRRS for step B/C; PCIe capability + 0x08 if the capability is at 0xAC: guess)"),
        (0xB8, f"{pcg(r'define\s+PCIE_CFG_DEVICE_CONTROL\s+0xb4')} (0xB8 = DEVCTL+4); {gc(PCIP, r'define\s+PCI_EXP_LNKCAP\s')}; {ss}", TAG_GUESS, "LNKCAP if the PCIe capability is at 0xAC (guess; the non-PCIe meaning is PCI_GPIO_OUTEN)"),
        (0xBC, f"{rcw('WIFI_L1SS_LINKCTRL')} (link status 0xBE = high half, {rcw('WIFI_LINK_STATUS')}; speed [3:0] = {gc(PCIP, r'define\s+PCI_EXP_LNKSTA_CLS\s')}, width [9:4] = {gc(PCIP, r'define\s+PCI_EXP_LNKSTA_NLW\s')}); {ss}", TAG_SRC, "EP link control (low) / link status (high: speed [3:0], width [9:4])"),
        *[(o, capw, TAG_GUESS, f"config dword {o:#x} (rest of the PCIe capability / any capability above 0x7C)") for o in (0xC0, 0xC4, 0xC8, 0xCC, 0xD0)],
        (0xD4, f"{rcw('WIFI_PCI_EXP_DEVCTL2')}; {ss}", TAG_SRC, "EP device control 2 (completion timeout range)"),
        *[(o, capw, TAG_GUESS, f"config dword {o:#x} (rest of the PCIe capability / any capability above 0x7C)") for o in range(0xD8, 0x100, 4)],
        (0x100, f"{pcg(r'define PCIE_EXTCFG_OFFSET\s+0x100')}, {pcg(r'define PCI_ADV_ERR_CAP\s+0x100')}; {ss}", TAG_SRC, "first extended capability header (AER expected)"),
        (0x1B4, f"{rcw('WIFI_L1SS_LTR_LATENCY')}; {ss}", TAG_SRC, "L1SS LTR latency"),
        *[(0x240 + 4 * n, f"{rcw(nm)}; {ss}", TAG_SRC, f"L1SS {what}") for n, (nm, what) in enumerate((("WIFI_L1SS_CAPID", "capability header"), ("WIFI_L1SS_CAP", "capabilities"), ("WIFI_L1SS_CONTROL", "control 1"), ("WIFI_L1SS_CONTROL2", "control 2")))],
    ]
    extra = {0x48: f"{ss}: PM capability guessed at 0x48 from {rcw('WIFI_PM_MNG_STATUS_CON')} (PM CSR = cap + 4; research 32 s.1.1 item 5)",
             0x4C: f"{rcw('WIFI_PM_MNG_STATUS_CON')}; {pcg(r'define\s+PCIE_CFG_PMCSR\s+0x4C')}; {ss}",
             0x70: f"{pcg(r'define PCIE2_BAR0_WIN2\s+0x70')}; {ss}", 0x74: f"{pcg(r'define PCIE2_BAR0_CORE2_WIN\s+0x74')}; {ss}", 0x78: f"{pcg(r'define PCIE2_BAR0_CORE2_WIN2\s+0x78')}; {ss}"}
    note_x = {0x48: "PM capability header? (guess)", 0x4C: "PM control/status (D-state in the low bits)", 0x70: "PCIE2_BAR0_WIN2 (config register mapping the 2nd 4 KiB of BAR0)", 0x74: "PCIE2_BAR0_CORE2_WIN", 0x78: "PCIE2_BAR0_CORE2_WIN2"}
    for o, c, tg, nt in cfg27:
        if o in extra:
            c, tg, nt = extra[o], (TAG_GUESS if o == 0x48 else TAG_SRC), note_x[o]
        add(27, "read_only", "CFG", o, cite=c, tag=tg, note=f"CFG window +{o:#x}: {nt}", **log)
    for n, what in (("CR1", "CR1 (type)"), ("CR2", "CR2 (bit 31 = enable)"), ("LOWER_BASE", "LOWER_BASE"), ("UPPER_BASE", "UPPER_BASE"), ("LIMIT", "LIMIT"), ("LOWER_TARGET", "LOWER_TARGET"), ("UPPER_TARGET", "UPPER_TARGET")):
        mac = f"PCIE_ATU_{n}_OUTBOUND1"
        add(27, "read_only", "ATU", ev(mac) - 0x300000, cite=f"{gc(RCH, 'define ' + mac + r'\s+0x3002')} (DBI + 0x300000 + off; unroll stride 0x200: region 1 = +0x200); {ss}; research 32 s.2e", tag=TAG_SRC, note=f"outbound region 1 {what}: reset value (needed for the later restore rows; unobserved so far)", **log)

    # ---- teardown (Linux rc_poweroff order, rc.c:3622-3669; PERST low, LTSSM off first; the isolation LAST) ----
    # ATU region 0 FIRST (before PERST low): disable it (CR2 := 0, as Linux's poweroff resets atu_ok and re-programs: rc.c:3647), then the reset values observed in run w3a-n25 (CR1 0, bases 0, LIMIT 0xFFF, targets 0)
    cr2 = anchor(RC, find_line(RC, r"CR2_OUTBOUND0, 4, EXYNOS_PCIE_ATU_ENABLE", vp), r"EXYNOS_PCIE_ATU_ENABLE")
    add_t(26, "mmio_write", "ATU", 0x004, FULL, 0, cite=f"{cr2} (the enable write, undone); reset value 0 observed (run w3a-n25 ATU+0x004)", tag="observed", note="ATU region 0 DISABLE: the first teardown row", key="atu_cr2_off")
    for off, val, key, nm in ((0x000, 0, "atu_cr1_rst", "CR1"), (0x008, 0, "atu_lbase_rst", "LOWER_BASE"), (0x00C, 0, "atu_ubase_rst", "UPPER_BASE"), (0x010, 0xFFF, "atu_limit_rst", "LIMIT"),
                              (0x014, 0, "atu_ltarget_rst", "LOWER_TARGET"), (0x018, 0, "atu_utarget_rst", "UPPER_TARGET")):
        add_t(26, "mmio_write", "ATU", off, FULL, val, cite=f"reset value observed in run w3a-n25 (ATU+{off:#x} = {val:#x}); written by the S26 row {nm}", tag="observed", note=f"ATU {nm} back to its reset value", key=key)
    lp = find_line(RC, r"gpio_set_value\(exynos_pcie->perst_gpio, 0\)", 3560)
    add_t(20, "gpio_dat", "GPH2", pr["DAT"][0], 1, 0, cite=anchor(RC, lp, r"perst_gpio, 0"), note="PERST low", key="perst_low")
    add_t(23, "mmio_write", "ELBI", ev("PCIE_APP_LTSSM_ENABLE"), FULL, ev("PCIE_ELBI_LTSSM_DISABLE"), cite=anchor(RC, lp + 7, r"PCIE_ELBI_LTSSM_DISABLE, PCIE_APP_LTSSM_ENABLE"), note="LTSSM disable", key="ltssm_off")
    ld = find_line(DHD, r"gpio_direction_output\(wlan_reg_on, 0\)", 595)
    add_t(3, "gpio_dat", "GPP14", pr["DAT"][0], 1 << 2, 0, cite=anchor(DHD, ld, r"wlan_reg_on, 0"), note="WLAN_EN low (dhd_wlan_power(0))", key="wlan_low")
    lf = find_line(RC, r"force SOFT_PWR_RESET", 3560)
    add_t(11, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), spr, 0, cite=anchor(RC, lf + 2, r"val &= ~SOFT_PWR_RESET"), note="soft power reset pulse", key="soft_reset")
    add_t(11, "delay", us=20, cite=anchor(RC, lf + 4, r"udelay\(20\)"))
    add_t(11, "mmio_rmw", "ELBI", ev("PCIE_SOFT_RESET"), spr, spr, cite=anchor(RC, lf + 5, r"val \|= SOFT_PWR_RESET"))
    for op in cal_ops(CAL, "exynos_pcie_rc_phy_all_pwrdn"):
        c = f"cal.c:{op[-1]}"
        if op[0] == "write":
            add_t(8, "mmio_write", op[1], op[2], FULL, op[3], cite=c, note="phy_all_pwrdn", key="phy_pwrdn")
        elif op[0] == "rmw":
            add_t(8, "mmio_rmw", op[1], op[2], op[3], op[4], cite=f"cal.c:{op[5]}-{op[6]}", note="phy_all_pwrdn: disable PHY PMA")
    add_t(5, "smc_iso", "PMU", 0x3EC4, 1, 0, cite=anchor(RC, find_line(RC, r"PCIE_PHY_ISOLATION\);", 3640), r"PCIE_PHY_ISOLATION"), note="isolation clear: LAST of Linux's rc_poweroff (rc.c:3667-3669)", key="iso_clear")
    add_t(2, "gpio_con", "GPP14", pr["CON"][0], 0xF << 8, 0, cite="wlanen.c baseline; S00 precondition; pinctrl-samsung.c:656-676", tag=TAG_GUESS, note="WLAN_EN back to an input (the observed start state); Linux itself leaves it an output", key="wlan_in")
    add_t(1, "gpio_con", "GPH2", pr["CON"][0], 0xF << 0, 0, cite="S00 precondition (PERST was an input)", tag=TAG_GUESS, note="PERST back to an input (the observed start state)", key="perst_in")
    add_t(7, "gpio_con", "GPH2", pr["CON"][0], 0xF << 4, 0, cite="S00 precondition (CLKREQ was an input)", tag=TAG_GUESS, note="CLKREQ function back to an input; the pad registers (PUD/DRV/CONPDN/PUDPDN) of S07 stay as the Linux 'active' state (their start values were not recorded: UNKNOWN)", key="clkreq_in")
    for r in rows:
        if r["restore"]:
            r["restore"] = keymap[r["restore"]]
    return {"steps": {str(k): v for k, v in STEP_NAMES.items()}, "unknown": unknown}


def draft28():
    """DRAFT S28 rows (research 33): BAR0/BAR2 sizing + assignment through the CFG window. Printed, NOT added to wlan_seq.json (the shim has no CFG write path yet: goal W-A6b). Usage: --draft28."""
    LP = REFS2 / "linux_pci_probe.c"	# Linux probe.c of the bluejay android13 5.10 tree (sha256 816b5395d48c...)
    pc = lambda nm: anchor(PCIR, find_line(PCIR, r"define\s+" + nm + r"\s"), nm)
    out, ob = [], {"BAR0": 4, "BAR1": 0, "BAR2": 4, "BAR3": 0}	# originals OBSERVED in run w3a-n27
    def row(kind, off, mask, value, cite, tag, note, restore=None):
        out.append({"kind": kind, "blk": "CFG", "off": off, "mask": mask, "value": value, "cite": cite, "tag": tag, "note": note, **({"restore": restore} if restore else {})})
    sz = "Linux __pci_read_base order: write all ones, read, write original (probe.c:198-200; high half 239-242)"
    row("wait_eq", 0x04, 0xFFFF, 0, f"{pc('PCI_COMMAND')}; probe.c:188-191 (Linux itself clears the decode bits while sizing); observed command 0x0000 (w3a-n26/27)", TAG_SRC, "precondition: Command == 0 (MEM/IO decode off) before ANY BAR write")
    for nm, lo, sz_guard in (("BAR0", 0x10, 0xFFC00000), ("BAR2", 0x18, 0xFFC00000)):
        hi = lo + 4
        row("wait_eq", lo, FULL, ob[nm], "observed w3a-n27 (BAR0 = BAR2 = 0x00000004)", "observed", f"precondition: {nm} low still holds the observed original")
        row("wait_eq", hi, FULL, 0, "observed w3a-n27 (BAR1 = BAR3 = 0)", "observed", f"precondition: {nm} high half still 0")
        row("mmio_write", lo, FULL, FULL, f"{pc('PCI_BASE_ADDRESS_' + str(lo // 4 - 4))}; {sz}", TAG_SRC, f"{nm} low := 0xFFFFFFFF (sizing)", restore=f"{nm}_lo")
        row("wait_mask", lo, sz_guard, sz_guard, f"{sz}; size = lowest set bit of (read & ~0xF) (probe.c:116-118)", TAG_SRC, f"{nm} low read-back: bits [31:22] all 1 = size <= 4 MiB (the layout of 2b needs it; else STOP, restore)")
        row("read_only", lo, FULL, 0, "same", TAG_SRC, f"log {nm} low sizing readback (decode: size = ~(v & 0xFFFFFFF0) + 1)")
        row("mmio_write", lo, FULL, ob[nm], f"probe.c:200 (restore the original)", TAG_SRC, f"{nm} low := original")
        row("mmio_write", hi, FULL, FULL, f"probe.c:240 (only valid because the type bits read 0x4 = 64-bit: {pc('PCI_BASE_ADDRESS_MEM_TYPE_64')})", TAG_SRC, f"{nm} high := 0xFFFFFFFF (sizing)", restore=f"{nm}_hi")
        row("wait_mask", hi, FULL, FULL, "probe.c:241-243; a high half that is not all ones means size > 4 GiB", TAG_SRC, f"{nm} high read-back must be 0xFFFFFFFF (size < 4 GiB) else STOP, restore")
        row("read_only", hi, FULL, 0, "same", TAG_SRC, f"log {nm} high sizing readback")
        row("mmio_write", hi, FULL, 0, "probe.c:242", TAG_SRC, f"{nm} high := original (0)")
    for nm, lo, addr in (("BAR0", 0x10, 0x60000000), ("BAR2", 0x18, 0x60400000)):
        row("mmio_write", lo, FULL, addr | 4, "research 33 s.3: PCI address == CPU address (pcie.dtsi:75-76), type bits kept as read", TAG_GUESS, f"{nm} low := {addr:#x} | type bits (0x4)", restore=f"{nm}_lo")
        row("read_only", lo, FULL, 0, "read-back (decode check: address bits [31:4] == written, low nibble 0x4)", TAG_GUESS, f"log {nm} low after assignment (expect {addr | 4:#010x})")
        row("read_only", lo + 4, FULL, 0, "read-back", TAG_GUESS, f"log {nm} high after assignment (expect 0; never written in this step)")
    return out


def finish():
    meta = build()
    srcs = {SRC_NAMES[p]: hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in SRC_NAMES}
    core = {"rows": rows, "teardown": tear}
    sha = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    doc = {"_doc": "GENERATED by tools/gen_wlan_seq.py from ~/pixelios-artifacts/refs/wlan-stage1 and wlan-stage2 (docs/research/29 sections 2 and 14); do not edit. sha256 covers rows + teardown and is pinned in tools/test-bluejay-boot.py.",
           "table_sha256": sha, "sources_sha256_16": srcs, "default_last_step": 19, "last_step": 27, "steps": meta["steps"], "unknown": meta["unknown"],
           "kinds": list(KINDS), "blocks": list(BLOCKS), "rows": rows, "teardown": tear}
    return doc


def dump(doc):
    out = ["{"]
    keys = list(doc)
    for i, k in enumerate(keys):
        v = doc[k]
        comma = "," if i < len(keys) - 1 else ""
        if k in ("rows", "teardown"):
            out.append(f' "{k}": [')
            for j, r in enumerate(v):
                out.append("  " + json.dumps(r, sort_keys=True, separators=(", ", ": ")) + ("," if j < len(v) - 1 else ""))
            out.append(" ]" + comma)
        else:
            out.append(f' "{k}": ' + json.dumps(v, sort_keys=True, separators=(", ", ": ")) + comma)
    out.append("}")
    return "\n".join(out) + "\n"


if __name__ == "__main__" and "--draft28" in sys.argv:
    build()
    for i, r in enumerate(draft28()):
        print(i, json.dumps(r, sort_keys=True))
elif __name__ == "__main__":
    text = dump(finish())
    if "--check" in sys.argv:
        if OUT.read_text() != text:
            raise SystemExit(f"{OUT} differs from the regenerated table")
        print("wlan_seq.json is up to date")
    else:
        OUT.write_text(text)
        d = json.loads(text)
        print(f"wrote {OUT}: {len(d['rows'])} rows, {len(d['teardown'])} teardown rows, sha256 {d['table_sha256']}")
