#!/usr/bin/env python3
"""Board definitions for the bare-metal shim, payload and ADT builders.

The data lives next to the hardware it describes, AOSP style:

  soc/<vendor>/<soc>/soc.json            what every device with that SoC shares
  device/<vendor>/<codename>/board.json  one device: names its SoC, adds what differs

A device's board is its SoC's values overlaid with its own. Keys other than the build
constants (name, codename, soc, status, marketing, notes) are metadata. Integers may be
written as "0x..." strings. Run `tools/pix devices` to list them.

  boards.py BOARD OUT.h          write the generated C header
  boards.py BOARD --get KEY      print one value (shell use), e.g. soc_dir
"""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_KEYS = {"name", "codename", "soc", "status", "marketing", "notes"}


def _num(v):
    if isinstance(v, str):
        try:
            return int(v, 0)
        except ValueError:
            return v		# e.g. a CPU compatible string
    if isinstance(v, list):
        items = [_num(x) for x in v]
        return tuple(items)
    return v


def _load(path):
    with open(path) as f:
        return json.load(f)


def load_devices(root=ROOT):
    """Return {codename: (device_dir, soc_dir, meta, values)}."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "device", "*", "*", "board.json"))):
        dev = _load(path)
        ddir = os.path.dirname(path)
        sdir = os.path.join(root, "soc", *dev["soc"].split("/"))
        soc = _load(os.path.join(sdir, "soc.json"))
        merged = dict(soc)
        merged.update(dev)
        values = {k: _num(v) for k, v in merged.items() if k not in META_KEYS}
        out[dev["codename"]] = (ddir, sdir, merged, values)
    return out


_DEVICES = load_devices()
BOARDS = {name: v[3] for name, v in _DEVICES.items()}


def device_dir(board):
    return os.path.relpath(_DEVICES[board][0], ROOT)


def soc_dir(board):
    return os.path.relpath(_DEVICES[board][1], ROOT)


def _touch_macros(v):
    """TOUCH_* macros for shim/touchprobe.c: the same soc.json/board.json keys tools/mkadt.py puts in /sweet-touch
    (xnu patches 0088/0089/0092 read them from the ADT), so the shim probe and the XNU driver cannot disagree."""
    out = []

    def d(name, value):
        out.append(f"#define TOUCH_{name} {value:#x}UL")

    usi = v["touch_usi"]
    d("SPI_BASE", v[f"usi{usi}_spi_base"])
    d("USI_OFF", v["usi_regs_offset"])
    d("USI_MODE_REG", v[f"usi{usi}_mode_reg"])
    d("USI_MODE_SPI", v["usi_mode_spi"])
    d("USI_MODE_MASK", v["usi_mode_mask"])
    d("SPI_FUNC", v["touch_spi_pin_func"])
    d("CHIP_ID0", v["touch_chip_id"][0])
    d("CHIP_ID1", v["touch_chip_id"][1])
    for pin in ("clk", "mosi", "miso", "cs", "irq", "reset", "mux"):
        bank, idx = v[f"touch_pin_{pin}"]
        d(f"PIN_{pin.upper()}_BANK", v[f"{bank}_bank"])
        d(f"PIN_{pin.upper()}_NR", idx)
    c = v["cmu_peric1"]
    d("CMU_BASE", int(c["base"], 0))
    for k, off in c["offsets"].items():
        d(f"CMU_{k.upper()}", int(off, 0))
    for k, bit in c["bits"].items():
        d(f"CMU_BIT_{k.upper()}", bit)
    t = c["top"]
    d("CMUTOP_BASE", int(t["base"], 0))
    for k in ("mux", "div", "gate"):
        d(f"CMUTOP_{k.upper()}", int(t[k], 0))
    d("CMUTOP_GATE_BIT", t["gate_bit"])
    return out


# Constants of the ACPM client that come from the Google kernel (android-gs-bluejay-5.10-android13-qpr3), pinned here so that an edit
# of soc.json alone cannot move a write address or the PMIC target: changing one means changing this table (and its citation) too.
_ACPM_PINS = {
    "mbox_base": 0x17610000, "sram_base": 0x02039000, "sram_size": 0x40000, "initdata_off": 0xA000,  # gs101.dtsi:496-499
    "ch_id": 2,                                                              # gs101-pmic.dtsi:38 (acpm-ipc-channel)
    "speedy_ch": 0, "pmic_addr": 1,                                          # s2mpg10-core.c:30,58 (S2MPG10: channel 0, type 1)
    "intgr1": 0x40, "intcr0": 0x24, "intsr0": 0x2c,                          # acpm_ipc.h:106-116 (APM_INTGR = INTGR1, AP_INTCR, AP_INTSR)
    "desc_stride": 0x48,                                                     # framework.h:97-133 (guess until layout_verified)
}
_ACPM_RAIL_REGS = (0x5C, 0x5D)  # S2MPG10_PM_L25M_CTRL / L26M_CTRL, enable = bit 7 (gk_s2mpg10-register.h:108-109, regulator.c:275-278)


_GPU_PINS = {"mali": 0x1C500000, "id_expect": 0x92020010, "shader_present": 0x779FFF, "tiler_present": 0x1, "l2_present": 0x1, "stack_present": 0x7F,
             "fvmap_sram": 0x206A000, "fvmap_idx_g3d": 5, "fvmap_idx_g3dl2": 6, "clk_khz": 151000,
             "s2mpu_g3d0": 0x20080000, "s2mpu_g3d1": 0x20090000, "s2mpu_g3d2": 0x200A0000, "s2mpu_g3d3": 0x200B0000}  # mali = gs101-gpu.dtsi reg (docs/research/17 s2.1), the rest observed g0 2026-10-23 (docs/research/27)


def _gpu_job_values():
    """The numeric values shim/gpujob.c writes, DERIVED from the kbase field definitions (every field with its cite in docs/research/28-gpu-stage2-job.md;
    kbase = android-gs-bluejay-6.1-android16 mali_kbase, local copies in ~/pixelios-artifacts/refs/gpu-stage2/). tools/test_boards_gpuclk.py pins the results."""
    # AS_MEMATTR_ATTRIBUTE0 byte: ALLOC_W bit0, ALLOC_R bit1, ALLOC_SEL bits 3:2 (IMPL 2, ALLOC 3), MEMORY_TYPE bits 7:6 (NON_CACHEABLE 1, WRITE_BACK 2)  [hw_access_regmap.h:203-250]
    def attr(w=0, r=0, sel=0, mt=0):
        return w | (r << 1) | (sel << 2) | (mt << 6)
    impl, alloc, nc, wb = 2, 3, 1, 2
    # kbase_mmu_get_as_setup (mmu_jm.c:34-50) with KBASE_MEMATTR_* (mem.h:114-157) at KBASE_MEMATTR_INDEX_* 0..5 (mem.h:165-175)
    attrs = [attr(sel=impl, mt=wb), attr(1, 1, alloc, wb), attr(1, 0, alloc, wb), attr(sel=impl, mt=wb), attr(1, 0, alloc, wb), attr(sel=alloc, mt=nc)]
    memattr = sum(x << (8 * i) for i, x in enumerate(attrs))
    # kbase_mmu_hw_configure (mmu_hw_direct.c:323-356): MODE AARCH64_4K (6, or UNMAPPED 1 to disable), PTW_MEMATTR WRITE_BACK (2) << 24, R_ALLOCATE (1) << 30;
    # AS_TRANSCFG_PTW_SH is set only when system_coherency != NONE (unknown here: we flush by hand and leave it 0)
    tc = lambda mode: mode | (2 << 24) | (1 << 30)
    # kbase_job_hw_submit (jm_hw.c:151-203): cfg = as_nr | START_FLUSH_CLEAN_INVALIDATE (3 << 8) | END_FLUSH_CLEAN_INVALIDATE (3 << 12) | THREAD_PRI(8) << 16 | JOB_CHAIN_FLAG (1 << 11, first job)
    js_config = 0 | (3 << 8) | (3 << 12) | (8 << 16) | (1 << 11)
    # mali_kbase_mmu_mode_aarch64.c: ACCESS_BIT 1 << 10, ENTRY_IS_PTE 3, ATE at level 3 = 3, ACCESS_RW 1 << 6, memattr index at bits 4:2
    # job header word 4 (Mesa v9.xml:1444-1458 at commit e130c230): Type bits 7:1 = Write value (2); Index bits 31:16 = 1 because Mesa numbers jobs from 1 (pan_jc.h:159 `index = ++jc->job_index`, packed into header.index)
    pte_table = (1 << 10) | 3
    pte_leaf = (5 << 2) | (1 << 6) | (1 << 10) | 3
    return {"TRANSCFG_ON": tc(6), "TRANSCFG_OFF": tc(1), "MEMATTR_LO": memattr & 0xFFFFFFFF, "MEMATTR_HI": memattr >> 32, "JS_CONFIG": js_config,
            "PTE_TABLE": pte_table, "PTE_LEAF": pte_leaf, "HDR_W4": (2 << 1) | (1 << 16), "WV_IMM64": 7, "JOB_VA": 0x1000, "TARGET_VA": 0x1100,
            "MAGIC_LO": 0x4C494F53, "MAGIC_HI": 0x50495845}   # 0x504958454C494F53 = "PIXELIOS" read as 8 ASCII bytes big-endian


def _gpu_check(g):
    """Fail the build if the gpu block moved a value shim/gpupower.c is allowed to write (it writes the PRESENT masks)."""
    for k, want in _GPU_PINS.items():
        if k not in g or _num(g[k]) != want:
            raise ValueError(f"gpu.{k} = {g.get(k)!r}: pinned to {want:#x} (tools/boards.py _GPU_PINS, observed in docs/research/27); change the pin and its citation together")


def _acpm_check(a):
    """Fail the build if the acpm block moved a pinned constant, or if a rail's voltage-code window is unknown or wrong while
    layout_verified / elem_unit_verified is 1. The window must equal what the regulator group (min + step * code) and the device-tree
    limits give: code = (uV - group_min_uv) / step_uv, 0..63 (vsel_mask 63)."""
    for k, want in _ACPM_PINS.items():
        if _num(a[k]) != want:
            raise ValueError(f"acpm.{k} = {a[k]!r}: pinned to {want:#x} (tools/boards.py _ACPM_PINS, cited there); change the pin and its citation together")
    if _num(a["elem_unit"]) not in (1, 4) or _num(a["layout_verified"]) not in (0, 1) or _num(a["elem_unit_verified"]) not in (0, 1):
        raise ValueError("acpm.elem_unit must be 1 (bytes) or 4 (words); the verified flags 0 or 1")
    if len(a["rails"]) != len(_ACPM_RAIL_REGS):
        raise ValueError("acpm.rails: expected the two touch rails LDO25M and LDO26M")
    armed = _num(a["layout_verified"]) or _num(a["elem_unit_verified"])
    for r, reg in zip(a["rails"], _ACPM_RAIL_REGS):
        if _num(r["reg"]) != reg:
            raise ValueError(f"acpm.rails {r['name']}: reg {r['reg']!r}, pinned to {reg:#x}")
        lo, hi = r["vsel_min"], r["vsel_max"]
        if lo is None or hi is None:
            if lo is not None or hi is not None or armed:
                raise ValueError(f"acpm.rails {r['name']}: vsel_min/vsel_max are null (or half set) while layout_verified/elem_unit_verified is 1: "
                                 "the rail voltage gate would be open")
            continue
        gmin, step = _num(r["group_min_uv"]), _num(r["step_uv"])
        codes = []
        for key in ("dt_min_uv", "dt_max_uv"):
            code, rem = divmod(_num(r[key]) - gmin, step)
            if rem or not 0 <= code <= 63:
                raise ValueError(f"acpm.rails {r['name']}: {key} {r[key]} is not group_min_uv + step_uv * (0..63)")
            codes.append(code)
        if (_num(lo), _num(hi)) != tuple(codes):
            raise ValueError(f"acpm.rails {r['name']}: vsel_min/vsel_max {lo}/{hi} != {codes[0]}/{codes[1]} derived from group_min_uv, step_uv and the DT limits")


# DVFS (docs/research/25): channel and domain indexes are pinned like the rest of the ACPM block.
_ACPM_DVFS_CH = 1                                     # gs101.dtsi:720 (exynos_dm acpm-ipc-channel = <1>)
_ACPM_DVFS_DOMS = {"CPUCL0": 2, "CPUCL1": 3, "CPUCL2": 4,   # dt-bindings/clock/gs101.h:305-307 (GET_IDX = id & 0xFFFF)
                   "G3D": 5, "G3DL2": 6}                    # gs101-gpu.dtsi:46-47 (ACPM_DVFS_G3DL2 / _G3D); indices OBSERVED: fvmap header type == index (runs fvmap1, g0b)
_ACPM_DVFS_GPU = ("G3D", "G3DL2")                           # --gpu-clk only: ceiling = the init level, never a CPU --dvfs target
_ACPM_DVFS_MAXLV = 24


# S2MPG11 registers read by --gpu-rails (GK include/linux/mfd/samsung/s2mpg11-register.h:26-100, regulator descriptors s2mpg11-regulator.c:226-275, group constants register.h:308-315,
# core s2mpg11-core.c:29 (I2C_ADDR_PMIC) and :55,73,91,109,127 (channel = 1)); pinned so that an edit of soc.json alone cannot move a read.
_ACPM_S11_PINS = {"speedy_ch": 1, "pmic_addr": 1, "buck_min_uv": 200000, "buck_step_uv": 6250, "ldo1_min_uv": 300000, "ldo1_step_uv": 12500, "ldo1_vsel_mask": 0x7F}
_ACPM_S11_REGS = {"STATUS1": 0x0C, "B2S_CTRL": 0x15, "B2S_OUT1": 0x16, "B2S_OUT2": 0x17, "B8S_CTRL": 0x25, "B8S_OUT1": 0x26, "B8S_OUT2": 0x27,
                  "L1S_CTRL1": 0x41, "L1S_CTRL2": 0x42, "LDO_CTRL1": 0x52}


# the one S2MPG11 register --gpu-rail-on may write (pins; cites in soc.json acpm.s2mpg11 note): BUCK2S_CTRL mode field, two values; the window is derived
_ACPM_S11_ON_PINS = {"reg": 0x15, "mask": 0xC0, "val_on": 0x40, "val_off": 0x00, "out1_reg": 0x16, "min_uv": 550000, "max_uv": 700000, "soft_start_us": 130}


def _s11_vsel(uv, s11):
    """uV -> OUT1 code with the group-1 formula; the shipped window must be exact steps."""
    d = uv - s11["buck_min_uv"]
    if d < 0 or d % s11["buck_step_uv"]:
        raise ValueError(f"acpm.s2mpg11.rail_on: {uv} uV is not 200000 + 6250 * n")
    return d // s11["buck_step_uv"]


def _acpm_s11_on_check(s11):
    """Returns True when rail_on is fully known; a moved pin fails the build; the voltage window must lie inside the Linux DT window 450000..1300000 uV."""
    r = s11.get("rail_on")
    if r is None or any(r.get(k) is None for k in _ACPM_S11_ON_PINS):
        return False
    for k, want in _ACPM_S11_ON_PINS.items():
        if _num(r[k]) != want:
            raise ValueError(f"acpm.s2mpg11.rail_on.{k} = {r[k]!r}: pinned to {want:#x} (tools/boards.py _ACPM_S11_ON_PINS, cited in soc.json); change the pin and its citation together")
    if _num(r["val_on"]) & ~_num(r["mask"]) or _num(r["val_off"]) & ~_num(r["mask"]) or _num(r["val_on"]) == _num(r["val_off"]):
        raise ValueError("acpm.s2mpg11.rail_on: val_on / val_off must be distinct values inside the mask")
    if not 450000 <= _num(r["min_uv"]) <= _num(r["max_uv"]) <= 1300000:
        raise ValueError("acpm.s2mpg11.rail_on: the window must lie inside the DT range 450000..1300000 uV (gs101-pmic.dtsi:766-768)")
    lo, hi = _s11_vsel(_num(r["min_uv"]), s11), _s11_vsel(_num(r["max_uv"]), s11)
    if not 0 <= lo <= hi <= 255:
        raise ValueError("acpm.s2mpg11.rail_on: bad window")
    if _num(r["out1_reg"]) != [_num(x["reg"]) for x in s11["regs"] if x["name"] == "B2S_OUT1"][0] or _num(r["reg"]) != [_num(x["reg"]) for x in s11["regs"] if x["name"] == "B2S_CTRL"][0]:
        raise ValueError("acpm.s2mpg11.rail_on: reg/out1_reg must be the B2S_CTRL / B2S_OUT1 entries of regs")
    return True


def _acpm_s11_check(a):
    """Returns True when every value is known (non-null); fails the build if a known value moved a pin. Null = unknown: --gpu-rails then refuses."""
    s11 = a["s2mpg11"]
    known = True
    for k, want in _ACPM_S11_PINS.items():
        if s11.get(k) is None:
            known = False
        elif _num(s11[k]) != want:
            raise ValueError(f"acpm.s2mpg11.{k} = {s11[k]!r}: pinned to {want:#x} (tools/boards.py _ACPM_S11_PINS, cited there); change the pin and its citation together")
    names = [r["name"] for r in s11["regs"]]
    if names != list(_ACPM_S11_REGS):
        raise ValueError(f"acpm.s2mpg11.regs: expected exactly {list(_ACPM_S11_REGS)} in that order, got {names}")
    for r in s11["regs"]:
        if r["reg"] is None:
            known = False
        elif _num(r["reg"]) != _ACPM_S11_REGS[r["name"]]:
            raise ValueError(f"acpm.s2mpg11 {r['name']}: reg {r['reg']!r}, pinned to {_ACPM_S11_REGS[r['name']]:#x}")
    return known


def _acpm_dvfs_check(a):
    """The rate table of a domain must be strictly decreasing kHz (the fvmap order, observed run fvmap1), the ceiling one of its rows."""
    dv = a["dvfs"]
    if _num(dv["ch_id"]) != _ACPM_DVFS_CH:
        raise ValueError(f"acpm.dvfs.ch_id = {dv['ch_id']!r}: pinned to {_ACPM_DVFS_CH} (tools/boards.py _ACPM_DVFS_CH, cited there)")
    names = [d["name"] for d in dv["domains"]]
    if names != list(_ACPM_DVFS_DOMS):
        raise ValueError(f"acpm.dvfs.domains: expected exactly {list(_ACPM_DVFS_DOMS)} in that order, got {names}")
    for d in dv["domains"]:
        if _num(d["idx"]) != _ACPM_DVFS_DOMS[d["name"]]:
            raise ValueError(f"acpm.dvfs {d['name']}: idx {d['idx']!r}, pinned to {_ACPM_DVFS_DOMS[d['name']]}")
        r = d["rates_khz"]
        if not 2 <= len(r) <= _ACPM_DVFS_MAXLV or any(not isinstance(x, int) or x <= 0 for x in r) or any(x <= y for x, y in zip(r, r[1:])):
            raise ValueError(f"acpm.dvfs {d['name']}: rates_khz must be 2..{_ACPM_DVFS_MAXLV} strictly decreasing positive integers")
        if d["ceiling_khz"] not in r:
            raise ValueError(f"acpm.dvfs {d['name']}: ceiling_khz {d['ceiling_khz']} is not one of the table rows")
        if d["name"] in _ACPM_DVFS_GPU and (d["ceiling_khz"] != r[-1] or d["ceiling_khz"] != _GPU_PINS["clk_khz"]):
            raise ValueError(f"acpm.dvfs {d['name']}: the GPU ceiling must be the firmware's init level {_GPU_PINS['clk_khz']} kHz = its last row (observed run g0b)")


def dvfs_requests(board, csv):
    """'K0,K1,K2' (kHz, 0 = leave that cluster) -> three ints, each 0 or a row of the domain's table at or below its ceiling.
    The shim re-checks the same in acpm.c; refusing here keeps a bad request out of the image altogether."""
    doms = [d for d in BOARDS[board]["acpm"]["dvfs"]["domains"] if d["name"] not in _ACPM_DVFS_GPU]	# CPU clusters only
    vals = [int(x) for x in csv.split(",")]
    if len(vals) != len(doms):
        raise ValueError(f"--dvfs needs {len(doms)} comma separated kHz values (CL0,CL1,CL2; 0 = leave), got {len(vals)}")
    for d, v in zip(doms, vals):
        if v != 0 and (v not in d["rates_khz"] or v > d["ceiling_khz"]):
            raise ValueError(f"--dvfs {d['name']}: {v} kHz is not a firmware table row at or below the ceiling {d['ceiling_khz']} "
                             f"(rows: {d['rates_khz']})")
    return vals


def _acpm_macros(v):
    """ACPM_* macros for shim/acpm.c (soc.json "acpm", docs/research/23). null vsel_min/vsel_max = unknown (VSEL_KNOWN 0)."""
    a = v["acpm"]
    _acpm_check(a)
    out = []

    def n(name, value):
        out.append(f"#define ACPM_{name} {_num(value)}")

    def h(name, value):
        out.append(f"#define ACPM_{name} {_num(value):#x}UL")

    h("MBOX_BASE", a["mbox_base"])
    h("SRAM_BASE", a["sram_base"])
    h("SRAM_SIZE", a["sram_size"])
    h("INITDATA_OFF", a["initdata_off"])
    n("CH_ID", a["ch_id"])
    n("SPEEDY", a["speedy_ch"])
    n("PMIC_ADDR", a["pmic_addr"])
    for k in ("intgr1", "intcr0", "intsr0", "desc_stride"):
        h(k.upper(), a[k])
    n("LAYOUT_VERIFIED", a["layout_verified"])
    n("ELEM_UNIT", a["elem_unit"])
    n("ELEM_UNIT_VERIFIED", a["elem_unit_verified"])
    for i, r in enumerate(a["rails"]):
        n(f"RAIL{i}_REG", r["reg"])
        known = r["vsel_min"] is not None and r["vsel_max"] is not None
        n(f"RAIL{i}_VSEL_KNOWN", 1 if known else 0)
        n(f"RAIL{i}_VSEL_MIN", r["vsel_min"] if known else 0)
        n(f"RAIL{i}_VSEL_MAX", r["vsel_max"] if known else 0)
    if "s2mpg11" in a:
        s11 = a["s2mpg11"]
        known = _acpm_s11_check(a)
        n("S11_KNOWN", 1 if known else 0)
        if known:
            n("S11_SPEEDY", s11["speedy_ch"])
            n("S11_PMIC_ADDR", s11["pmic_addr"])
            n("S11_NREG", len(s11["regs"]))
            out.append("#define ACPM_S11_REGS " + ", ".join(f"{_num(r['reg']):#x}" for r in s11["regs"]))
            for r in s11["regs"]:
                out.append(f"#define ACPM_S11_R_{r['name']} {_num(r['reg']):#x}")
            n("S11_BUCK_MIN_UV", s11["buck_min_uv"])
            n("S11_BUCK_STEP_UV", s11["buck_step_uv"])
            n("S11_LDO1_MIN_UV", s11["ldo1_min_uv"])
            n("S11_LDO1_STEP_UV", s11["ldo1_step_uv"])
            out.append(f"#define ACPM_S11_LDO1_VSEL_MASK {_num(s11['ldo1_vsel_mask']):#x}")
            on_known = _acpm_s11_on_check(s11)
            n("S11_ON_KNOWN", 1 if on_known else 0)
            if on_known:
                r = s11["rail_on"]
                out.append(f"#define ACPM_S11_ON_REG {_num(r['reg']):#x}")
                out.append(f"#define ACPM_S11_ON_OUT1 {_num(r['out1_reg']):#x}")
                out.append(f"#define ACPM_S11_ON_MASK {_num(r['mask']):#x}")
                out.append(f"#define ACPM_S11_ON_VAL_ON {_num(r['val_on']):#x}")
                out.append(f"#define ACPM_S11_ON_VAL_OFF {_num(r['val_off']):#x}")
                n("S11_ON_VSEL_MIN", _s11_vsel(_num(r["min_uv"]), s11))
                n("S11_ON_VSEL_MAX", _s11_vsel(_num(r["max_uv"]), s11))
                n("S11_ON_SOFT_US", r["soft_start_us"])
    if "dvfs" in a:
        _acpm_dvfs_check(a)
        doms = a["dvfs"]["domains"]
        n("DVFS_CH", a["dvfs"]["ch_id"])
        n("DVFS_N", len([d for d in doms if d["name"] not in _ACPM_DVFS_GPU]))	# CPU clusters: what the touch probe iterates
        n("DVFS_NALL", len(doms))									# all configured domains (CPU + GPU): the acpm.c tables
        out.append("#define ACPM_DVFS_GPU_IDXS " + ", ".join(str(d["idx"]) for d in doms if d["name"] in _ACPM_DVFS_GPU))
        n("DVFS_MAXLV", _ACPM_DVFS_MAXLV)
        out.append("#define ACPM_DVFS_IDXS " + ", ".join(str(d["idx"]) for d in doms))
        out.append("#define ACPM_DVFS_CEILS " + ", ".join(str(d["ceiling_khz"]) for d in doms))
        out.append("#define ACPM_DVFS_NRS " + ", ".join(str(len(d["rates_khz"])) for d in doms))
        out.append("#define ACPM_DVFS_ROWS " + ", ".join("{ " + ", ".join(str(x) for x in d["rates_khz"]) + " }" for d in doms))
    return out


def c_header(board: str) -> str:
    values = BOARDS[board]
    names = {
        "GICD_BASE": "gicd_base", "GICD_SIZE": "gicd_size",
        "GICR_BASE": "gicr_base", "GICR_SIZE": "gicr_size",
        "GICR_PE_SIZE": "gicr_stride", "VTIMER_INTID": "vtimer_intid",
        "TIMER_FREQ": "timer_freq",
        "FB_BASE": "fb_base", "FB_SIZE": "fb_size", "FB_W": "fb_width",
        "FB_H": "fb_height", "FB_STRIDE": "fb_stride", "UFS_BASE": "ufs_base",
        "UFS_PRDT_BYTE_GRAN": "ufs_prdt_byte_gran", "UART_BASE": "uart_base",
        "DECON_BASE": "decon_base", "NR_CPUS": "cpu_count",
        "PSCI_SYSTEM_RESET": "psci_system_reset",
    }
    lines = ["/* Generated from tools/boards.py; do not edit. */", "#pragma once"]
    for macro, key in names.items():
        value = 8 if key == "cpu_count" else values[key]
        lines.append(f"#define {macro} {value}" if key == "cpu_count" else f"#define {macro} {value:#x}UL")
    for i, mpidr in enumerate(values["cpu_mpidrs"]):
        lines.append(f"#define BOARD_MPIDR_{i} {mpidr:#x}UL")
    watchdogs = values["watchdog_bases"] + (0, 0)
    lines.append(f"#define WDT_CL0_BASE {watchdogs[0]:#x}UL")
    lines.append(f"#define WDT_CL1_BASE {watchdogs[1]:#x}UL")
    # SoC family macros: code paths are selected by SoC, so siblings (Pixel 6 / 6 Pro share gs101
    # with the 6a) need no source change. BOARD_BLUEJAY is kept as an alias for out-of-tree users.
    this_soc = _DEVICES[board][2]["soc"].split("/")[1].upper()
    for name in sorted({os.path.basename(os.path.dirname(p)).upper()
                        for p in glob.glob(os.path.join(ROOT, "soc", "*", "*", "soc.json"))}):
        lines.append(f"#define SOC_{name} {1 if name == this_soc else 0}")
    for key, addr in sorted(values.get("wlan", {}).items()):
        lines.append(f"#define WLAN_{key.upper()} {int(addr, 0):#x}UL")
    if "gpu" in values:
        _gpu_check(values["gpu"])
        for k, v in _gpu_job_values().items():
            lines.append(f"#define GPU_JOB_{k} {v:#x}u")
        if "acpm" in values and _num(values["gpu"]["fvmap_sram"]) != _num(values["acpm"]["sram_base"]) + 0x31000:
            raise ValueError("gpu.fvmap_sram must be acpm.sram_base + 0x31000 (gs101.dtsi fvmap_addr, docs/research/25)")
    for key, addr in sorted(values.get("gpu", {}).items()):
        lines.append(f"#define GPU_{key.upper()} {int(addr, 0):#x}UL")
    if "touch_chip_id" in values:
        lines += _touch_macros(values)
    if "acpm" in values:
        lines += _acpm_macros(values)
    lines.append(f"#define BOARD_BLUEJAY {1 if this_soc == 'GS101' else 0}")
    lines.append(f"#define BOOT_HEADER_VERSION {values['boot_header_version']}")
    return "\n".join(lines) + "\n"


_WLAN_SEQ_KINDS = ("delay", "read_only", "wait_eq", "wait_mask", "gpio_dat", "gpio_con", "mmio_rmw", "mmio_write", "smc_iso")	# enum WLK_* order of the generated header
_WLAN_SEQ_BLOCKS = ("GPP14", "GPH2", "PMA", "PCS", "ELBI", "IA", "DBI", "PMU", "ATU", "CFG", "MEM")					# enum WLB_* order


def wlan_seq_header(board: str) -> str:
    """shim/out/wlan_seq.h for --wlan-link (docs/research/29 section 14): the rows of soc/<vendor>/<soc>/wlan_seq.json as a const table of plain integers (no pointers: build.sh rejects ABS64
    relocations), plus the table hash. The hash is recomputed here from the rows, so a hand edit of the JSON that keeps the old hash fails the build."""
    path = os.path.join(ROOT, soc_dir(board), "wlan_seq.json")
    return wlan_seq_render(json.load(open(path)), path)


def wlan_seq_render(doc, path="wlan_seq.json") -> str:
    """The pure part of wlan_seq_header (also used by the audit's mutation tests)."""
    import hashlib
    core = {"rows": doc["rows"], "teardown": doc["teardown"]}
    sha = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if sha != doc["table_sha256"]:
        raise ValueError(f"{path}: table_sha256 {doc['table_sha256']} does not match the rows ({sha}); regenerate with tools/gen_wlan_seq.py")
    rows, tear = doc["rows"], doc["teardown"]
    last = int(doc["last_step"])
    prev = -1
    for i, r in enumerate(rows):
        if r["id"] != i or r["kind"] not in _WLAN_SEQ_KINDS or (r["blk"] is not None and r["blk"] not in _WLAN_SEQ_BLOCKS) or not prev <= r["step"] <= last:
            raise ValueError(f"{path}: row {i} is malformed or out of order")
        prev = r["step"]
        if r["kind"] not in ("delay",) and r["blk"] is None:
            raise ValueError(f"{path}: row {i} needs a block")
    for i, t in enumerate(tear):
        if t["id"] != f"T{i + 1:02d}" or t["kind"] not in _WLAN_SEQ_KINDS or t["blk"] not in _WLAN_SEQ_BLOCKS + (None,) or not 0 <= t["requires"] <= last:
            raise ValueError(f"{path}: teardown row {i} is malformed")

    def cell(r, step):
        a = r["delay_us"] if r["kind"] in ("delay", "wait_eq", "wait_mask") else (1 if r.get("probe") else 0) | (2 if r.get("log") else 0) | (4 if r.get("eot") else 0) | (8 if r.get("eot") == 2 else 0)
        b = r["polls"]
        blk = "WLB_NONE" if r["blk"] is None else "WLB_" + r["blk"]
        return f"\t{{ {step}u, WLK_{r['kind'].upper()}, {blk}, {r['off']:#x}u, {r['mask']:#x}u, {r['value']:#x}u, {a}u, {b}u }},"

    out = ["/* Generated from soc/google/gs101/wlan_seq.json by tools/boards.py; do not edit. Plain integers only (no pointers). */", "#pragma once", "#include <stdint.h>", "",
           f'#define WLAN_SEQ_SHA256 "{sha}"', f"#define WLAN_SEQ_NROWS {len(rows)}u", f"#define WLAN_SEQ_NTEAR {len(tear)}u", f"#define WLAN_SEQ_LAST {last}u",
           f"#define WLAN_SEQ_DEFAULT_N {int(doc['default_last_step'])}u", "",
           "enum { " + ", ".join(f"WLK_{k.upper()}" for k in _WLAN_SEQ_KINDS) + " };",
           "enum { " + ", ".join(f"WLB_{b}" for b in _WLAN_SEQ_BLOCKS) + ", WLB_NONE };", "",
           "/* kind WLK_*, blk WLB_*; a = delay_us (delay) / poll interval us (wait_*) / read_only: 1 = block probe, 2 = log the value (LINK R line), 4 = EROM dump row (stop after a 0xF dword), 8 = last dump row (refuse without an EOT); b = polls. In wlan_tear, step = the step that must have STARTED for the action to run. */",
           "struct wlan_row { uint16_t step; uint8_t kind, blk; uint32_t off, mask, val, a, b; };", "",
           "static const struct wlan_row wlan_rows[WLAN_SEQ_NROWS] = {"]
    out += [cell(r, r["step"]) for r in rows]
    out += ["};", "", "static const struct wlan_row wlan_tear[WLAN_SEQ_NTEAR] = {"]
    out += [cell(t, t["requires"]) for t in tear]
    out += ["};", ""]
    return "\n".join(out)


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("board", choices=BOARDS)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--wlan-seq", action="store_true", help="write the --wlan-link table header (soc wlan_seq.json) instead of board_config.h")
    parser.add_argument("--get", metavar="KEY", help="print device_dir | soc_dir | any board value")
    parser.add_argument("--dvfs-check", metavar="K0,K1,K2", help="validate a --dvfs request, print the three values")
    args = parser.parse_args()
    if args.dvfs_check is not None:
        try:
            print(" ".join(str(x) for x in dvfs_requests(args.board, args.dvfs_check)))
        except (ValueError, KeyError) as exc:
            raise SystemExit(f"error: {exc}")
    elif args.get:
        if args.get == "device_dir":
            print(device_dir(args.board))
        elif args.get == "soc_dir":
            print(soc_dir(args.board))
        elif args.get == "soc":
            print(_DEVICES[args.board][2]["soc"])
        else:
            print(BOARDS[args.board][args.get])
    elif args.wlan_seq:
        args.output.write_text(wlan_seq_header(args.board))
    else:
        args.output.write_text(c_header(args.board))
