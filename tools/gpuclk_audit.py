#!/usr/bin/env python3
"""gpuclk_audit: the T-a214 / T-50eb audits of the --gpu-clk-level (DT pair) request, moved out of tools/test-bluejay-boot.py (R29: that file
may not grow). Imported by test-bluejay-boot.py (sys.path has tools/). Text checks only: no process, no file written.

  check_level                            the level-image additions to the gpuclk.c source audit (targets, uV table, request order, PMU first, gaps named)
  source_cases                           the mutants for gpuclk_source_selftest
  check_gpuprobe_pmu_on_text / selftest  gpu_probe_pmu_on, the M1 safety predicate (byte-pinned)
  check_shim_dvfs_handover / selftest    the shim.c call site and the gpu_clk_handover() body in gpuclk.c (byte-pinned)
  audit()                                what _audit_main runs for them

The helpers of test-bluejay-boot.py (comment stripping, function span) are handed in by bind(); there is no second copy of them here.

  tools/gpuclk_audit.py --self-test    binds the helpers of test-bluejay-boot.py and runs both mutation self-tests on the shim sources
Last line: RESULT: PASS|FAIL gpuclk_audit ..."""
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


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def check_level_source(code: str, norm: str, name: str) -> None:
    """Target of each domain (init rate for both, or DT pair GPU_CLK_LEVEL as { G3DL2, G3D }) and the pinned uV table."""
    for need in ("#defineTGT_G3DL2GPU_CLK_KHZ", "#defineTGT_G3D(GPU_CLK_KHZ+0u)", "#defineTGT_G3DL2pairs[GPU_CLK_LEVEL][0]", "#defineTGT_G3Dpairs[GPU_CLK_LEVEL][1]",
                 "staticconstuint32_tpairs[GPU_CLK_NLEVELS][2]={GPU_CLK_PAIRS};", "_Static_assert(GPU_CLK_LEVEL>=0&&GPU_CLK_LEVEL<GPU_CLK_NLEVELS,"):
        if need not in norm:
            raise AssertionError(f"{name}: '{need}' is missing (the target of each domain is fixed: init rate, or one bounded DT pair)")
    if "staticconstuint32_tuvs[GPU_CLK_NLEVELS][2]={GPU_CLK_UVS};" not in norm or "need=uvs[GPU_CLK_LEVEL][k];" not in norm:
        raise AssertionError(f"{name}: the undervolt compare must use the pinned uV table uvs[GPU_CLK_LEVEL] (T-50eb review F1)")
    if code.count("GPU_CLK_LEVEL") != 13:
        raise AssertionError(f"{name}: GPU_CLK_LEVEL may appear exactly 13 times (ifdefs incl. the F2 result text, the uV compare and its stub, the table indexes, "
                             f"the static assert and its message, the target line); got {code.count('GPU_CLK_LEVEL')}")


def check_level(code: str, norm: str, body: str, name: str) -> None:
    """The level-image additions to check_gpuclk_source_text. body = whitespace-free text of gpu_clk()."""
    check_level_source(code, norm, name)
    if body.count("dom=(k^ord)?IDX_G3D:IDX_G3DL2;") != 1 or "ord=up?0:1;" not in body or "up=TGT_G3DL2>=cur[0]&&TGT_G3D>=cur[1];" not in body:
        raise AssertionError(f"{name}: the request order must come from the GET values: up (both rates rise or stay) = G3DL2 then G3D, else G3D then G3DL2 (gpu_dvfs_set_new_level)")
    pre = body[:body.find("acpm_dvfs_reassert(")]
    if pre.find("if(!gpu_probe_pmu_on()){") > pre.find("e=acpm_describe(&c,&h);if(e){"):
        raise AssertionError(f"{name}: the PMU read (review M1) must come before any ACPM traffic")
    if "if(khz==(k?TGT_G3D:TGT_G3DL2))ok++;" not in body:
        raise AssertionError(f"{name}: each domain's read-back is compared with its own asked rate")
    for need in ('gap("nothermalreadout', 'gap("BTSscenariog3d_performance', 'gap(k?"G3D":"G3DL2","alreadyattheaskedratebeforetherequest:""itsreadbackprovesnothing(reviewH1)")'):
        if need not in body:
            raise AssertionError(f"{name}: the known hole {need!r} must stay named as a GPU GAP (AGENTS.md rule 8)")


def source_cases(good: str) -> list:
    return [
        ("no PMU check (review M1)", good.replace("if (!gpu_probe_pmu_on()) {", "if (0) {", 1)),
        ("PMU read after the describe", good.replace("if (!gpu_probe_pmu_on()) {", "if (0) {", 1).replace("\tacpm_xfer_zero(&t.a);", "\tif (!gpu_probe_pmu_on()) {\n\t\treturn 1;\n\t}\n\tacpm_xfer_zero(&t.a);", 1)),
        ("no G3DL2 >= G3D start check", good.replace("if (cur[0] < cur[1]) {", "if (0) {", 1)),
        ("mixed direction not refused", good.replace("if (!up && !(TGT_G3DL2 <= cur[0] && TGT_G3D <= cur[1])) {", "if (0) {", 1)),
        ("the order always G3DL2 first", good.replace("ord = up ? 0 : 1;", "ord = 0;", 1)),
        ("the order inverted", good.replace("ord = up ? 0 : 1;", "ord = up ? 1 : 0;", 1)),
        ("direction from G3D only", good.replace("up = TGT_G3DL2 >= cur[0] && TGT_G3D >= cur[1];", "up = TGT_G3D >= cur[1];", 1)),
        ("level targets swapped", good.replace("#define TGT_G3DL2\tpairs[GPU_CLK_LEVEL][0]", "#define TGT_G3DL2\tpairs[GPU_CLK_LEVEL][1]", 1)),
        ("another pair row", good.replace("#define TGT_G3D\t\tpairs[GPU_CLK_LEVEL][1]", "#define TGT_G3D\t\tpairs[GPU_CLK_LEVEL + 1][1]", 1)),
        ("a hard-coded target", good.replace("#define TGT_G3D\t\t(GPU_CLK_KHZ + 0u)", "#define TGT_G3D\t\t848000", 1)),
        ("level index not bounded", good.replace("_Static_assert(GPU_CLK_LEVEL >= 0 && GPU_CLK_LEVEL < GPU_CLK_NLEVELS,", "_Static_assert(1,", 1)),
        ("readback against one rate", good.replace("if (khz == (k ? TGT_G3D : TGT_G3DL2))", "if (khz == TGT_G3D)", 1)),
        ("thermal gap dropped", good.replace('gap("no thermal readout', 'gap("thermal ok', 1)),
        ("BTS gap dropped", good.replace('gap("BTS scenario g3d_performance', 'gap("bandwidth ok', 1)),
        ("same-rate readback not named", good.replace('"its readback proves nothing (review H1)"', '"ok"', 1)),
    ]


# ---- gpu_probe_pmu_on (gpuprobe.c) ----
def check_gpuprobe_pmu_on_text(src: str, name: str = "gpuprobe.c") -> None:
    """T-50eb review F3: gpu_probe_pmu_on is the M1 safety predicate of gpu_clk (both GPU power domains on, both words read). The host test replaces it with a fake in
    gpuclk_host_test.c, so its body is pinned here byte for byte (whitespace aside): two read-only reads, bit 0 of BOTH words, both reads must have succeeded."""
    code = _strip(src)
    a, b = _span(code, "gpu_probe_pmu_on")
    body = _norm(code[a:b])
    want = ('intgpu_probe_pmu_on(void){uint32_ttop=0,emb=0;inta=rd("PMU_G3D_STAT",GPU_PMU_G3D_STAT,&top);'
            'intb=rd("PMU_EMB_STAT",GPU_PMU_EMB_STAT,&emb);returna&&b&&(top&1u)&&(emb&1u);}')
    if body != want:
        raise AssertionError(f"{name}: gpu_probe_pmu_on differs from the audited predicate (both reads ok AND bit 0 of PMU_G3D_STAT AND of PMU_EMB_STAT, nothing else)")


def gpuprobe_pmu_on_selftest(root: pathlib.Path) -> int:
    good = (root / "shim" / "gpuprobe.c").read_text()
    check_gpuprobe_pmu_on_text(good)  # control must pass
    ret = "return a && b && (top & 1u) && (emb & 1u);"
    cases = [
        ("the EMB bit dropped", good.replace(ret, "return a && b && (top & 1u);", 1)),
        ("the bit tests dropped", good.replace(ret, "return a && b;", 1)),
        ("an unreadable word accepted", good.replace(ret, "return (top & 1u) && (emb & 1u);", 1)),
        ("either domain enough", good.replace(ret, "return a && b && ((top & 1u) || (emb & 1u));", 1)),
        ("the top read dropped", good.replace('int a = rd("PMU_G3D_STAT", GPU_PMU_G3D_STAT, &top);', "int a = 1;", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"gpuprobe pmu_on selftest case did not mutate: {label}"
        try:
            check_gpuprobe_pmu_on_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpuprobe pmu_on {label} -> {exc}")
        else:
            raise AssertionError(f"gpuprobe pmu_on self-test FAILED to catch: {label}")
    print(f"PASS gpuprobe pmu_on self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- the handover DVFS step: the shim.c call site + gpu_clk_handover() in gpuclk.c ----
_HANDOVER_BODY = """void gpu_clk_handover(uint32_t rail_on_ok)
{
	uint32_t uv_b2s = 0, uv_b8s = 0;

	pstore_save();
	con_str("SECTION", "GPU handover (DVFS pair)");
	if (rail_on_ok) {
		if (gpu_clk())
			con_str("GPU", "GAP: DVFS pair request refused or failed (lines above); "
				"XNU boots at the clocks the ABL left");
		if (gpu_rails_uv(&uv_b2s, &uv_b8s))
			con_str("GPU", "GAP: rail readback after the DVFS pair failed: "
				"the voltage the APM set is UNKNOWN");
		else if (gpu_clk_uv_check(uv_b2s, uv_b8s))
			con_str("GPU", "GAP: a GPU rail reads below its fvmap voltage (lines above): "
				"a hang or bus error of the XNU GPU run is not a driver bug "
				"until this is explained");
	} else {
		con_str("GPU", "GAP: DVFS pair request skipped, the shader rail is not verified on; "
			"XNU boots at the clocks the ABL left");
	}
	pstore_save();
}
"""
_CALL = "gpu_clk_handover(gpu_ho==RAIL_ON_OK);"


def check_shim_dvfs_handover(shim_src: str, clk_src: str, name: str = "shim.c") -> None:
    """T-a214: the DVFS pair request of a --gpu-handover --gpu-clk-level image. shim.c holds ONE call, gpu_clk_handover(gpu_ho == RAIL_ON_OK), as the last statement of the
    handover rail-on block (after the restore for UNVERIFIED/MISMATCH, before loader_boot); the probe-flow gpu_clk (the only other request) is compiled out of a level image, so the
    image has one request site. The function body in gpuclk.c is pinned (whitespace aside): pstore, gpu_clk() only for a verified rail, a refusal named as a GPU GAP (XNU boots either
    way), the read-only gpu_rails_uv() and the undervolt compare, each failure a GAP; no retry, no restore."""
    code = _strip(shim_src)
    norm = _norm(code)
    if code.count("gpu_clk()") != 1 or norm.count("gpu_clk_handover(") != 1 or norm.count(_CALL) != 1:
        raise AssertionError(f"{name}: exactly one gpu_clk() (probe flow) and one gpu_clk_handover(gpu_ho == RAIL_ON_OK) call; a third request site or a retry is not allowed")
    if "#ifdefined(GPU_CLK)&&!defined(GPU_CLK_LEVEL)con_str(\"SECTION\",\"GPUclk\");if(gpu_clk())skip_power=1;" not in norm:
        raise AssertionError(f"{name}: the probe-flow gpu_clk must be compiled out of a --gpu-clk-level image (no second request site, no request before the power cycle)")
    bx = code.find('con_str("SECTION", "boot XNU")')
    lb = code.find("loader_boot(dtb)", bx)
    h = code.find("#if defined(GPU_HANDOVER) && defined(GPU_RAIL_ON)\n", bx)
    he = code.find("#endif", h)
    hb = _norm(code[h:he]) if h >= 0 and he >= 0 else ""
    last = "if(gpu_ho==RAIL_UNVERIFIED||gpu_ho==RAIL_MISMATCH)gpu_rail_restore(gpu_ho,RAIL_POWER_NONE);" + _CALL
    if code.count("defined(GPU_CLK_LEVEL)") != 1 or not (0 <= bx < h < he < lb) or not hb.endswith(last):
        raise AssertionError(f"{name}: the handover call must be the last statement of the handover rail-on block, after the restore for UNVERIFIED/MISMATCH, before loader_boot")
    cc = _strip(clk_src)
    a, b = _span(cc, "gpu_clk_handover")
    if _norm(cc[a:b]) != _norm(_HANDOVER_BODY) or cc.count("gpu_clk()") != 1:
        raise AssertionError("gpuclk.c: gpu_clk_handover differs from the audited one (gpu_clk only for a verified rail, a failure logged as a GAP, then gpu_rails_uv, nothing else)")


def gpuclk_shim_selftest(root: pathlib.Path) -> int:
    shim_src = (root / "shim" / "shim.c").read_text()
    clk = (root / "shim" / "gpuclk.c").read_text()
    check_shim_dvfs_handover(shim_src, clk)  # control must pass
    n = 0

    def one(label: str, s: str, c: str) -> None:
        nonlocal n
        assert (s, c) != (shim_src, clk), f"gpuclk shim selftest case did not mutate: {label}"
        try:
            check_shim_dvfs_handover(s, c, "selftest")
        except AssertionError as exc:
            n += 1
            print(f"self-test caught: gpuclk shim {label} -> {exc}")
        else:
            raise AssertionError(f"gpuclk shim self-test FAILED to catch: {label}")

    gap1 = '\t\t\tcon_str("GPU", "GAP: DVFS pair request refused or failed (lines above); "\n\t\t\t\t"XNU boots at the clocks the ABL left");'
    gap2 = '\t\t\tcon_str("GPU", "GAP: rail readback after the DVFS pair failed: "\n\t\t\t\t"the voltage the APM set is UNKNOWN");'
    gap3 = '"a hang or bus error of the XNU GPU run is not a driver bug "\n\t\t\t\t"until this is explained");'
    gap4 = '\t\tcon_str("GPU", "GAP: DVFS pair request skipped, the shader rail is not verified on; "\n\t\t\t"XNU boots at the clocks the ABL left");'
    skip = "\t} else {\n\t\tcon_str(\"GPU\", \"GAP: DVFS pair request skipped"
    refused = "\t\tif (gpu_clk())\n\t\t\tcon_str(\"GPU\", \"GAP: DVFS pair request refused"
    for lbl, old, new in (("a request for an unverified rail", "\tif (rail_on_ok) {", "\tif (1) {"),
                          ("a failure ignored silently", gap1, "\t\t\t(void)0;"),
                          ("a retry", refused, "\t\tgpu_clk();\n" + refused),
                          ("a failed rail readback ignored (F5)", gap2, "\t\t\t(void)0;"),
                          ("the undervolt compare dropped (F1)", "\t\telse if (gpu_clk_uv_check(uv_b2s, uv_b8s))", "\t\telse if (0)"),
                          ("the undervolt gap silent (F1)", gap3, "(void)0;"),
                          ("a rail restore inside the block", skip, "\t\tgpu_rail_restore(1, 0);\n" + skip),
                          ("the skip is silent", gap4, "\t\t(void)0;"),
                          ("the first pstore checkpoint dropped", "\tpstore_save();\n\tcon_str(\"SECTION\", \"GPU handover (DVFS pair)\");", "\tcon_str(\"SECTION\", \"GPU handover (DVFS pair)\");"),
                          ("the rail registers read before the request", refused, "\t\tgpu_rails_uv(&uv_b2s, &uv_b8s);\n" + refused)):
        one(lbl, shim_src, clk.replace(old, new, 1))
    one("the call no longer follows the rail result", shim_src.replace("gpu_clk_handover(gpu_ho == RAIL_ON_OK);", "gpu_clk_handover(1);", 1), clk)
    one("the probe-flow request kept in a level image", shim_src.replace("#if defined(GPU_CLK) && !defined(GPU_CLK_LEVEL)", "#ifdef GPU_CLK", 1), clk)
    one("a request after loader_boot returned", shim_src.replace("\t\tdelay_ms(3000);\n\t}\n#endif\n#ifdef USB_GADGET", "\t\tgpu_clk_handover(1);\n\t\tdelay_ms(3000);\n\t}\n#endif\n#ifdef USB_GADGET", 1), clk)
    one("a second call in the block", shim_src.replace("gpu_clk_handover(gpu_ho == RAIL_ON_OK);", "gpu_clk_handover(gpu_ho == RAIL_ON_OK);\n\tgpu_clk_handover(gpu_ho == RAIL_ON_OK);", 1), clk)
    print(f"PASS gpuclk shim self-test: {n}/{n} bad sources rejected")
    return n


def audit(root: pathlib.Path, out_dir: pathlib.Path, memo_obj, obj_selftest, obj_check, allowed_undef, strip_c_comments, c_function_span) -> None:
    """Everything _audit_main runs for the DT pair request except the gpuclk.c source audit itself (check_level is called from it)."""
    bind(strip_c_comments, c_function_span)
    gpuprobe_pmu_on_selftest(root)
    check_gpuprobe_pmu_on_text((root / "shim" / "gpuprobe.c").read_text())
    gpuclk_shim_selftest(root)
    check_shim_dvfs_handover((root / "shim" / "shim.c").read_text(), (root / "shim" / "gpuclk.c").read_text())
    print("PASS gpuclk shim audit: the handover DVFS step (one gpu_clk_handover() call after the rail-on block, before loader_boot; its body: one gpu_clk() for RAIL_ON_OK only, "
          "a failure logged as GPU GAP, then read-only gpu_rails_uv) is pinned; the probe-flow request is compiled out of a --gpu-clk-level image (one request site)")
    memo_obj(obj_selftest, out_dir / "gpuclk_level.o", allowed_undef, "gpuclk_level")
    n_stores = obj_check(out_dir / "gpuclk_level.o", allowed_undef)
    print(f"PASS gpuclk_level object audit (DT pair 3 variant): no try_write32/try_read32 reference; {n_stores} stores, all stack/argument based; same undef subset")


def self_test() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("tbb", root / "tools" / "test-bluejay-boot.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root / "tools"))
    spec.loader.exec_module(mod)
    bind(mod._strip_c_comments, mod._c_function_span)
    try:
        n = gpuprobe_pmu_on_selftest(root) + gpuclk_shim_selftest(root)
    except AssertionError as exc:
        print(f"RESULT: FAIL gpuclk_audit self-test: {exc}")
        return 1
    print(f"RESULT: PASS gpuclk_audit self-test {n} mutants rejected")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL gpuclk_audit is a module imported by tools/test-bluejay-boot.py; run with --self-test")  # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
