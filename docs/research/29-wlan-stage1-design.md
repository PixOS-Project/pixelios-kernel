# 29 - W2 design: BCM4389 + PCIe1 power-up on bluejay, from "everything off" to LTSSM L0 (DESIGN ONLY)

Status 2026-10-23. **No code that writes hardware exists for this; no hardware was touched to write this.** Tags: *observed* (hardware run, log), *read from source*
(`file:line` in the local copies), *guess*. A fact that could not be cited is listed as **UNKNOWN** (section 8), never filled in. Starting point is the W1 result
(research 20 section 3a, run `w1`, 2026-10-23, *observed*): `PMU_PCIE1_ISO (0x17463EC4)=0` (PHY isolated), `WLAN_EN` gpp14-2 func=0 lvl=0 (CON `0x33`, DAT `0x3`),
`PERST` gph2-0 func=0 lvl=0, `CLKREQ` gph2-1 lvl=1, `DEVWAKE` lvl=0, `HOSTWAKE` gpa7-0 lvl=0 (CON 0), level 2 gated, no hang.

## 0. Sources and corrections

Branch `android-gs-bluejay-6.1-android16`, fetched 2026-10-23 into `~/pixelios-artifacts/refs/wlan-stage1/` (`W/` below). Names used in cites:

| Cite name | File (under `W/`) | Origin |
|---|---|---|
| `rc.c`, `rc.h`, `common.h`, `cal.c` | `dwc-whi/pcie-exynos-{rc.c,rc.h,common.h,gs101-rc-cal.c}` | `kernel/google-modules/soc/gs` `drivers/pci/controller/dwc-whi/` |
| `soc-Kbuild`, `defconfig` | `soc-gs-Kbuild`, `gs101_defconfig` | soc-gs `Kbuild`; `kernel/devices/google/gs101` `gs101_defconfig` |
| `pcie.dtsi`, `common.dtsi`, `wlan.dtsi`, `pinctrl.dtsi`, `pmic.dtsi`, `pmdom.dtsi`, `bj-pmic.dtsi` | `dts-gs101-gs101-{pcie,common,common-wlan,pinctrl,pmic,pm-domains}.dtsi`, `dts-bluejay-gs101-bluejay-pmic.dtsi` | `kernel/devices/google/gs101` and `.../bluejay` `dts/` |
| `el3.c`, `el3.h` | `exynos-pd_el3.c`, `exynos-el3_mon.h` | soc-gs `drivers/soc/google/`, `include/soc/google/` |
| `dhd_cg.c`, `dhd.h` | `dhd_custom_google.c`, `bcm4389-dhd.h`; `bcm4389-Kbuild` | `kernel/google-modules/wlan/bcmdhd/bcm4389` @ `de86f6ec77fd3ea86aba02b8c045a4f9fac69732` (the commit research 12 pins) |
| `s10reg.h`, `s10reg.c` | `s2mpg10-register.h`, `s2mpg10-regulator.c` | soc-gs `include/linux/mfd/samsung/`, `drivers/regulator/` |
| `pinctrl-arm64.c`, `pinctrl.h`, `samsung.h`, `exynos-pmu.c` | `linux-pinctrl-exynos-arm64.c`, `linux-pinctrl-exynos.h`, `linux-samsung.h`, `linux-exynos-pmu.c` | Linux v6.12 (pinctrl), Google android15-6.6 `include/dt-bindings/pinctrl/samsung.h`, Linux v6.16 (PMU SMC) |
| `flexpmu.h` | `flexpmu_cal_system_gs101.h` | soc-gs `drivers/soc/google/cal-if/gs101/` (byte-identical to the research 20 copy) |

Also saved: `dwc/` (the *zuma* generation of the same driver; not used on gs101, see C1), `s2mpg10-core.c`, `s2mpg10.h`, `bcm4389-*` (headers/Kbuild/dhd_linux.c), `exynos-pmu-if.[ch]`.

**Corrections to earlier docs found while writing this one**
- **C1 (which driver).** gs101 builds `drivers/pci/controller/dwc-whi/`: `soc-Kbuild:8-14` (`ifeq ($(CONFIG_SOC_ZUMA),y)` -> `dwc/`, `else` -> `dwc-whi/`) and `defconfig:19-20`
  (`CONFIG_PCI_EXYNOS_GS=m`, `CONFIG_PCI_EXYNOS_CAL_GS101=m`) [read from source]. The branch-head `dwc-whi/` files are **byte-identical** to the copies W1 used (`cmp` of rc.c, rc.h,
  common.h, cal.c; `~/pixelios-artifacts/refs/wlan/`), so every `rc.c:` line number in research 20 stays valid. The `dwc/` tree has a different establish_link (no PLL/CDR waits,
  extra `soc`/`udbg` resources); do **not** use it as a reference.
- **C2 (rails).** `L17M_PCIE1` / `L19M_PCIE1` are the supplies of the **SoC's PCIe1 PHY** (`pcie_1`'s `vreg1/vreg2-supply`, `common.dtsi:376-377`), not of the BCM4389. The driver never
  calls a regulator function (`rc.c` has only the two `#include`s at `:36-37`; no `regulator_*` call anywhere in `dwc-whi/`, grep) [read from source]. Research 20 row 4 ("radio rails")
  must read "PHY rails". **What powers the BCM4389 module (VBAT, VDDIO, 32 kHz LPO) is not in the DT at all**; `WLAN_EN` is the only handle Linux has (section 8, U3).
- **C3 (OSCCLK/TCXO).** For ch1 Linux also touches `PMU+0x3C10` (bits 2,3,10 = "TXCO always on", `rc.c:80,2692-2702`) and `CMU_HSI2+0x800` bit 28 ("automatic clock gating on
  OSCCLK": cleared in `resumed_phydown` `rc.c:2704-2706`, set at the end of `poweron` `rc.c:3540-3553`). Neither is on the L0 path; both are read in section 4.

## 1. Fixed facts of this board (all *read from source*)

| Item | Value | Cite |
|---|---|---|
| RC | `pcie_1` = HSI2 GEN4A_1, ch-num 1, ip-ver `0x984500`, **1 lane, Gen2**, `ep-device-type = EP_BCM_WIFI`, `use-ia`, `use-sicd`, `use-l1ss`, `use-phy-isol-en` all "true"; `use-sysmmu`/`use-cache-coherency`/`use-secure-atu` false (default) | `pcie.dtsi:54-95`; `common.dtsi:366-378` |
| Windows | ELBI `0x14520000`/0x2000, PMA `0x14550000`/0x2000, SYSREG `0x14420000`/0x2000, DBI `0x14800000`/0x301000, PCS `0x14540000`/0x1000, config `0x60FFE000`/0x2000, I/A `0x14500000`/0x1000; MEM `0x60000000` size `0xFF0000` | `pcie.dtsi:57-63,76` |
| PMU PHY isolation | PMU alive base + `pmu-offset 0x3EC4`; mask 1; `PCIE_PHY_ISOLATION=0`, `PCIE_PHY_BYPASS=1`; PMU base `0x17460000` from the syscon `reg` (`rc.c:1968-1979`, value per research 10 / 12) | `pcie.dtsi:91`; `rc.h:120`; `common.h:51-52`; `rc.c:393-407` |
| No clk-framework clocks | `pcie-clk-num = <0>`, `phy-clk-num = <0>`; the clock helpers loop over 0 clocks | `pcie.dtsi:80-81`; `rc.c:1212-1248` |
| GPIOs | `WLAN_EN` gpp14-2 (`wl_reg_on`, `pcie,wlan-gpio`); `PERST` gph2-0; `CLKREQ` gph2-1 (func 2); `dev_wake` gph2-5 (input, pull-down); `host_wake` gpa7-0 (func F, pull-down) | `wlan.dtsi:109-153`; `common.dtsi:375`; `pcie.dtsi:56`; `pinctrl.dtsi:367-380` |
| Pinctrl layout | CON `+0x00` 4 bit/pin, DAT `+0x04` 1 bit/pin, PUD `+0x08` 4, DRV `+0x0C` 4, CONPDN `+0x10` 2, PUDPDN `+0x14` 4; banks gpp14 `+0x1C0` (PERIC0 `0x10840000`), gph2 `+0x0` (HSI2 `0x14440000`), gpa7 `+0x20` (FAR_ALIVE `0x174E0000`) | `pinctrl-arm64.c:47-50,988,1016,955`; `pinctrl.dtsi:277,383` |
| Function codes | INPUT 0, OUTPUT 1, EINT/F 0xF; pull none 0 / down 1 / up 3; PDN PREV 3 | `samsung.h:20-32,77-85` |
| Rail registers | S2MPG10 (speedy ch 0, client 0x01): `L17M_CTRL` = reg `0x54`, `L19M_CTRL` = reg `0x56` (mode = bits 7:6, vsel = bits 5:0; `vsel_reg == enable_reg`); LDO17M uV = 725000 + 12500*sel, LDO19M uV = 700000 + 25000*sel; DT ranges 0.725-1.3 V / 1.6-1.95 V, `regulator-always-on`, `regulator-initial-mode = SEC_OPMODE_SUSPEND` | `s10reg.h:99-103,316-320,333`; `s10reg.c:259-264,33-44,108-122`; `pmic.dtsi:450-481` |

## 2. The sequence (1): every step from "off as ABL left it" to L0

State at start (*observed*, W1): PHY isolated; WLAN_EN, PERST, DEVWAKE, HOSTWAKE all **inputs reading 0** (CON bits 0); CLKREQ reads 1. Linux's order is
`rc probe` -> `dhd: WLAN_EN high, 200 ms` -> `exynos_pcie_pm_resume()` = `exynos_pcie_rc_poweron()` (`dhd_cg.c:529-566,638-657`; `rc.c:3705-3711`). "Tag" is for the *cited
behaviour* (read from source); **nothing in this table has run on hardware**; the only *observed* inputs are the start state above and the register offsets read in W1 (ELBI/PMA reads were never reached: gated).

Notation: `G(r)` = a 32-bit store to PA r; "wait" counters are the bounds our shim would use (Linux's own bound in the cite). Delays are minimums in the shim (busy-wait on `cntvct`, as the rest of the shim).

| # | Step | Target and exact write | Delay / wait (bound) | Cite | Tag |
|---|---|---|---|---|---|
| S01 | PERST output, low | gph2 `0x14440000` CON bits 3:0 := 1; DAT `0x14440004` bit 0 := 0 (write DAT first, then CON) | - | `rc.c:2015-2023` (`devm_gpio_request_one(..., GPIOF_OUT_INIT_LOW)`) | read from source |
| S02 | WLAN_EN output, low | gpp14 `0x108401C0` CON bits 11:8 := 1; DAT `0x108401C4` bit 2 := 0 | - | `rc.c:1989-1995` (`gpio_direction_output(wlan_gpio, 0)`); function code `wlan.dtsi:112`, `samsung.h:78` | read from source |
| S03 | **WLAN_EN high** (module power) | DAT `0x108401C4` bit 2 := 1 | - | `dhd_cg.c:542,555-561,601` | read from source |
| S04 | wait for the module | busy-wait **200 ms** | 200 ms (`WIFI_TURNON_DELAY` = 200, not overridden: only `-DWIFI_TURNOFF_DELAY=10` in `bcm4389-Kbuild:522`) | `dhd_cg.c:566`; `dhd.h:3137-3140` | read from source |
| S05 | **PHY isolation -> bypass (EL3)** | `smc #0`: `x0=0x82000504, x1=0x17463EC4, x2=2 (RMW), x3=mask 0x1, x4=val 0x1`; success = `x0==0` | EL3 call, no Linux delay; shim: stop and restore on `x0!=0` (Linux falls back to a plain regmap write on nonzero, which would fault here) | `rc.c:393-407,3388-3389`; `el3.c:66-77`; `el3.h:21-23`; `common.h:51-52`; `common.dtsi:372` (use-phy-isol-en) | read from source (0x82000504 and arg order *observed to be accepted for other PMU regs*, section 3) |
| S06 | clocks | none (0 clocks) | - | `rc.c:1212-1248,3395`; `pcie.dtsi:80-81` | read from source |
| S07 | pinctrl "active" (all five pins in one state) | gph2: pin1 CLKREQ CON:=2, PUD:=3 (up), DRV:=3 (10 mA), CONPDN:=3; pin0 PERST CON:=1, DRV:=3, CONPDN:=3 (**level untouched**); gpp14-2 CON:=1, PUD:=0, DRV:=0, CONPDN:=3, PUDPDN:=3; gpa7-0 CON:=0xF, PUD:=1, DRV:=0; gph2-5 CON:=0, PUD:=1, DRV:=0 | - | `rc.c:3420-3421`; `pinctrl.dtsi:367-380`; `wlan.dtsi:109-139,143` (the `pcie_1` `pinctrl-0` list) | read from source |
| S08 | PHY all-power-down clear | PMA(`0x14550000`)+`0xD8` := `0x28`; **1 ms** (`mdelay(1)`); then `0x00` to PMA `+0x404,+0x408,+0x40C,+0x800,+0x804,+0xA40,+0xA44,+0xA48,+0xA4C,+0xA50,+0x1000,+0x1004,+0x1240,+0x1244,+0x1248,+0x124C,+0x1250` (17 stores) | 1 ms | `cal.c:69-96`; `rc.c:3423-3425` | read from source |
| S09 | **Retry loop starts** (<= 10 tries, `rc.c:3253,3264`); read PMA+`0x5FC` (log only) | read | - | `rc.c:3114-3116` | read from source |
| S10 | PHY config A: input clk path, PCS mux W/A | PMA+`0xD8` := `0x28`; PCS(`0x14540000`)+`0x008` |= `0x80` (lane-1 `+0x808` only if num_lanes==2: not here) | - | `cal.c:116-126`; `rc.c:3119` -> `rc.c:2657-2671` | read from source |
| S11 | PHY reset pulse (ELBI) | ELBI(`0x14520000`): `+0x1404`:=1, `+0x1408`:=1, `+0x1400`:=1, then `+0x1404`:=0, `+0x1408`:=0, `+0x1400`:=0; 10 us; `+0x1404`:=1; 10 us | 10 us + 10 us | `cal.c:128-138` | read from source |
| S12 | PHY config B: PMA/PCS tables | PMA common (`cal.c:140-168`, 24 stores incl. `0x400:=0x80`, `0x458:=0x02`, `0x5B0:=0x34`, `0x450:=0x20`), PMA per-lane table for lane 0 (`cal.c:170-266`, ~95 stores), PCS (`cal.c:268-300`: `+0x004:=0`, `+0x154:=0x700D5`, `+0x150:=0x300FF`, `+0x170:=0x40`, `+0x008` RMW bits 5:4 := 01). **Total in `phy_config` 106-322: 133 `writel`** (incl. S10, S11, S13) | - | `cal.c:140-300` | read from source (values are Google's PHY tuning; re-derive by reading the file, do not retype) |
| S13 | PHY release + extras | ELBI `+0x1400`:=1, `+0x1408`:=1; PMA+`0x5D0` RMW (set bit 4, clear bit 3); PMA+`0xBF4` := `0x4` | - | `cal.c:302-321` | read from source |
| S14 | I/A (CDR-lock helper) | ELBI+`0x388`:=`0x400`; I/A(`0x14500000`) `+0x30`:=ELBI PA, `+0x34`:=PMA PA, `+0x38`:=I/A PA, then 34 more words `+0x40..+0x17C`, last `+0x000`:=1 (**37 `ia_write`s**) | - | `rc.c:2530-2655` (called from `rc.c:2669-2670`) | read from source |
| S15 | PHY locks | poll PMA `+0x3F0` bit 3 (PLL), `+0xE0C` bit 2 (CDR), `+0xDE8 & 0xF == 0xF` (offset cal); Linux continues on failure and only logs | each <= 10000 polls of ~1 us (`rc.c:917-926`) | `rc.c:3121-3136,908-940` | read from source |
| S16 | soft power reset | ELBI+`0x3A4` bit 1 clear; **1 ms**; set; read PMA+`0xE18` bit 7 (log) | 1 ms | `rc.c:3142-3152`; `rc.h:58-62` | read from source |
| S17 | device type | ELBI+`0x80` := `0x04` | - | `rc.c:3161-3162` | read from source |
| S18 | non-sticky reset | ELBI+`0x3A4` bit 3 set; 10-12 us; clear; **1 ms**; set | 1 ms | `rc.c:3164-3173`; `rc.h:62` | read from source |
| S19 | "EQ off" | DBI(`0x14800000`)+`0x890` := `0x12000` (only legal with the PHY in bypass: `rc.c:1323-1324` returns NOT_FOUND while isolated) | - | `rc.c:3175-3176` | read from source |
| S20 | **PERST high** | gph2 DAT bit 0 := 1 | **20-22 ms** (`EP_BCM_WIFI`) | `rc.c:3178-3187` | read from source |
| S21 | L1 / Q-channel / NAK options | ELBI `+0x3BC` |= (`0x1 | 1<<4`); `+0x3A0` := `0x2`; `+0x3A8` &= ~(`0xF00|0xF0|0xF`) (ip_ver >= 0x889500); `+0x474` := 1; `+0x1078` := 1 | - | `rc.c:3189-3213`; `rc.h:64,86-88,55-56,106-109` | read from source |
| S22 | RC setup | `dw_pcie_setup_rc()` (generic DWC, fetched and read 2026-10-23, **still UNKNOWN: MSI target = dmam_alloc_coherent() address (`pcie-designware-host.c:273-284,383-391`), PCIe-cap offset from a runtime cap-list walk (`pcie-designware.c:73-82,489-521`); see `unknown[]` in wlan_seq.json**, U5) then `exynos_pcie_setup_rc`: DBI `0x8BC`:=1 (RO write enable), vendor/device id, LNKCAP, `0xB40`:=`0x1A`, `0xB44`:=`0xEA`, PM ctrl, LNKCTL2 target = Gen2, DEVCTL2 | - | `rc.c:3215-3217,2720-2790`; `rc.h:167-176` | read from source (setup_rc: partly UNKNOWN) **DOCUMENTED SKIP (driver decision 2026-10-23, tag guess):** `wlan_seq.json` has exactly one 0 us `delay` row for S22 so the interpreter reaches S23; RC config-space setup is NOT transcribed, the speed target stays at the hardware default and the endpoint is NOT enumerable until S22 exists. "Not required for LTSSM link training" is a guess (hardware), not measured. |
| S23 | **LTSSM enable, wait for L0** | ELBI+`0x54` := 1; poll ELBI+`0x2C8` & `0x3F` == `0x11` | **<= 4800 polls x 10-12 us (~48-58 ms; shim: bound 100 ms by `cntvct`)** | `rc.c:3229-3239`; `rc.h:44-51`; `common.h:20,88` | read from source |
| S24 | on timeout (Linux) | PERST low, LTSSM off (ELBI+`0x54`:=0), phy clock off (no-op), retry from S09 (<= 10) | - | `rc.c:3241-3264` | read from source; **our shim: no retry**, teardown (section 6) and report |

**Sequence length: 23 steps to L0** (S01-S23; S24 is the failure branch). **Stores: ~230** (133 `phy_config` + 18 `pwrdn_clear` + 38 I/A = 189 table stores, ~40 control stores) plus **1 SMC**.
After L0 Linux also waits 3 ms, checks Gen2, clears ELBI IRQ0/1/2, enables interrupts and programs the iATU (cfg0 + mem windows, `rc.c:3301-3357`); that is stage 2 (config space), not part of "link up".

Not needed for L0 (deliberately skipped, *guess* that they are not needed): `host_wake` IRQ, `dev_wake`, SysMMU/S2MPU, cache coherency/SYSREG sharability (`rc.c:1649-1711`, only with `use_cache_coherency`), MSI, the `OSCCLK` bit (C3), L1ss.

## 3. The PMU-alive (EL3) write: how Linux does it, and what the shim has

- **Linux** (`exynos_pcie_phy_isolation`, `rc.c:393-407`): `rmw_priv_reg(pmu_alive_pa + 0x3EC4, mask=PCIE_PHY_CONTROL_MASK (=1, `rc.h:120`), val)`; if it returns nonzero, falls back to
  `regmap_update_bits(pmureg, ...)` (plain MMIO). `rmw_priv_reg` = `arm_smccc_smc(SMC_CMD_PRIV_REG, reg, PRIV_REG_OPTION_RMW (2), mask, val, 0,0,0, &res)`, returns `(int)res.a0`
  (`el3.c:66-77`; `el3.h:21-23`). Mainline's copy of the same interface: `TENSOR_SMC_PMU_SEC_REG 0x82000504`, WRITE=1, RMW=2, "returns -EINVAL if access isn't allowed or 0",
  "PMU_ALIVE registers can only be written from EL3, but are still read accessible", "only registers that can be written are readable via SMC" (`exynos-pmu.c:28-31,45-53,62-69,79-86,89-93`, v6.16) [read from source].
- **Our shim already has an SMC**: `static uint64_t smc(fn,a1,a2,a3)` in `shim/shim.c:111-123` (`smc #0`, x0-x3 in/out, x4-x17 clobbered). It has been used for the PMU function `0x82000504` **WRITE**
  (option 1) of `SYSIP_DAT0` `0x17460810` and `CPU_INFORM` `0x17460860+4n` (`shim.c:217,297`) - *observed* to return 0 (research 20 row 3 cited HANDOFF.md:357,365, which no longer exists: the observed `SMC ret 0 after 0xFC` is `docs/archive/HANDOFF-s01-s06.md:374,382`; the CPU_INFORM result has no cite; ABL itself uses the same SMC for
  WRITE and for RMW of `0x17463A00`, research 10:35,82 [read from ABL]). **It has never been called with RMW (`x2=2`)**, and it cannot be: RMW needs `x4`. So a writer needs a 5-argument variant
  (`register uint64_t x4 asm("x4")`; the clobber list loses x4), written as its own noipa function in its own object so the audit can allow exactly one `smc #0` with constant `x0/x1/x2/x3/x4`
  (today the audits ban `smc` in the USB/probe objects: `usbgadget.c:14`; `wlanprobe.c:5` header). Alternative without a new asm helper: WRITE (option 1) of `read | 1` after a plain MMIO read of
  `0x17463EC4` (non-atomic, but nothing else writes that register in our single-threaded shim); Linux uses RMW, so RMW is the faithful choice. *Open for the review* (Q-SMC1).
- **What nobody knows**: whether EL3 accepts `0x17463EC4` at all (U1). Linux's copy of the allow list is not fetched for this register (research 10:107 only lists `INFORM0..SYSIP_DAT0`, `SYSTEM_CONFIGURATION..`, and the range around `0x3E9C`); EL3 enforces its own list.
  The cheapest EL3-side test is the SMC **READ** (option 0) of `0x17463EC4`: per mainline "only registers that can be written are readable via SMC" (`exynos-pmu.c:91-92`), so a successful read
  is (*guess*) evidence of write permission and a refusal is evidence against, with **no state change**. What `x0` contains on success (the value? 0?) is UNKNOWN (U2): print `x0` and compare with the plain MMIO read.
- **Other EL3 writes?** None on the L0 path: `rc.c:1343` (`SMC_SECURE_ATU_SETUP`) is only reached with `use_secure_atu`, false for this node (`pcie.dtsi:90`). The `PMU+0x3C10`
  (`rc.c:2692-2702`) and `CMU_HSI2+0x800` (`rc.c:3544-3550`) writes are plain `writel`s (CMU) / plain MMIO to the PMU (`ioremap(pmu_alive_pa + 0x3C10)`, **a PMU alive store that works without SMC in Linux?** UNKNOWN, U4) and are off the L0 path.
- **Therefore the only write that needs EL3 is S05.** It is also the first write that touches a security boundary; every other write is plain EL1 MMIO that the existing allowlisted-writer pattern (`w32`/`try_write32`, fault-guarded) can express.

## 4. Rails and clocks: what to read first (read-only, precedent: `--gpu-rails`)

Precedent: `shim/gpurails.c` reads S2MPG11 rails through `acpm_s11_read` and decodes mode/uV (research 27). For S2MPG10 the same client has `acpm_pmic_read(cfg, reg, &val, &trace)` (`shim/acpm.h:147`, speedy ch 0, client 0x01;
used by the touch rails, reg `0x5C/0x5D`). No ACPM write is involved.

| What | How | Decode | Why |
|---|---|---|---|
| `L17M_CTRL` reg `0x54`, `L19M_CTRL` reg `0x56` (PHY supplies); also `L16M_CTRL 0x53`, `L18M_CTRL 0x55` (PCIe0 = modem, for comparison) | `acpm_pmic_read` (needs `acpm.o`, i.e. a new `--wlan-rails` flag that links it like `--touch-rails`) | mode = bits 7:6 (0 off, 1 "ON in standby" `s10reg.c:33-44`, 2 PWREN_MIF, 3 normal), `enabled = mode != 0` (`s10reg.c:108-122`); sel = bits 5:0; uV per section 1 | Linux turns them on in the PMIC probe (`regulator-always-on`); ABL may not. If a mode is 0 the PHY has no supply and S12-S15 can never lock (U6) |
| `CMU_HSI2+0x620` (`PLL_CON0_MUX_CLKCMU_HSI2_PCIE_USER`), `+0x800` (bit 28 OSCCLK auto gating) | plain MMIO read | raw word | `flexpmu.h:355`; `rc.c:2693,3544`. The mux bit-4 "selected" meaning is a *guess* (W1 gate) |
| `CMU_HSI2+0x7018` (`GPIO_HSI2_QCH`), `+0x7060` (`QE_PCIE_GEN4A_HSI2_QCH`), `+0x7064` (`GEN4B`), `+0x707C` (`SYSREG_HSI2_QCH`), `+0x7068` (`QE_UFS_EMBD_HSI2_QCH`, **reference: UFS works**) | plain MMIO read | raw words; bit meaning **UNKNOWN** (U7): compare PCIE vs UFS word | `flexpmu.h:1313-1340` (`DBG_NFO_QCH_CON_*`, PMUCAL_READ). Answers "is the GPIO/PCIe QCH clocked" without guessing a layout |
| `PMU+0x3C10` | plain MMIO read (PMU alive reads work: *observed* `PMU 3ec8` in `ufs-gpt-read-2026-10-21.txt:294`) | bits 2,3,10 expected 1 for ch1 | `rc.c:80,2692-2702` |
| `PMU+0x3EC4`, `+0x3EC8` | already read (W1: `0x3EC4`=0) | | |

Present state of everything above: **UNKNOWN** (nothing has been read yet except the W1 values). Rail state at XNU entry is the single biggest unknown for S08-S15.

**Minimum read-only extension of `--wlan-probe` (proposal, W2-R; one boot, no writes):**
1. Level 1 prints, ungated, the five CMU_HSI2 words and `PMU+0x3C10` above (CMU/PMU reads: low risk, UFS in the same CMU domain works *observed*; the CMU reads themselves have not been done yet).
2. New `--wlan-rails` (requires acpm): prints `WLAN RAIL L17M reg=54 raw=.. mode=.. sel=.. uV=..` and L19M (+ L16M/L18M comparison), through the existing `acpm_pmic_read`; the ACPM validation path stays the one the touch/gpu rails use.
3. Gate unchanged (`wlan_gate()`); the new reads sit before it. Output keeps the one-summary-line convention (`WLAN RAILS l17=<mode>/<uV> l19=<mode>/<uV> cmu=.. pmu3c10=..`).
Audit: same read-only object policy as `wlanprobe.o` (no store, no `try_write32`, no `smc`).

## 5. Risk table (every write step) and the Opus-review questions

Legend: H = bus/EL3 hang or unexpected reset (recover with the power button, nothing persistent is written, *guess* that PMU alive bits do not survive a cold power-off), B = brick (writes a
non-volatile or boot-critical location: **none of these steps does**), O = overvolt/over-current, E = EL3 refusal. "-" = not applicable.

| Step | H | B | O | E | Review questions |
|---|---|---|---|---|---|
| S01 PERST out low | low (plain GPIO, same block as the W1 reads) | - | - | - | Is `DAT` before `CON` the right order (no glitch)? Does PERST have an external pull that conflicts with driving low? (schematic UNKNOWN) |
| S02/S03 WLAN_EN out, high | low | - | low-medium: the module draws its boot current from rails we do not control (U3); no PMIC rail is written | - | Is it acceptable to power the module with the link impossible (PERST low, PHY isolated)? Which line proves the chip is alive (CLKREQ/HOSTWAKE/current)? Restore = DAT:=0 then CON:=0 |
| S04 delay | - | - | - | - | 200 ms is Linux's, not a datasheet value (*guess* that less is unsafe) |
| S05 PHY isolation SMC | medium: first EL3 RMW from this shim; a bad `x1` is a wrong-register write | - | - | **high (U1)** | Is RMW (new x4 helper) preferable to WRITE-after-read? Must the SMC READ test come first? What does the shim do on `x0 != 0` (nothing further, restore, report)? |
| S07 pinctrl active (5 pins) | low | - | - | - | Which of the five pins' PUD/DRV/CONPDN stores are needed (function only vs full state)? Minimum: gph2 CON for pins 0,1 |
| S08 pwrdn clear (18 PMA stores) | **medium: first PMA stores; unclocked PMA hangs the bus (UNKNOWN QCH state, U7)** | - | - | - | Is the CMU/QCH state proven by the section-4 reads before the first PMA store? Must the PMA be read first (W1 level 2) after S05 and before any store? |
| S10-S14 (phy_config, I/A: ~170 stores) | medium (same as S08) | - | low (PHY analog on L17M/L19M if on) | - | Are the tables copied from the file by a generator (no retyping)? One pstore checkpoint every 16 stores? |
| S15 lock waits | low (counter-bounded, 10 ms) | - | - | - | What does the shim do when the locks fail (Linux continues): stop before S16? |
| S16-S18 resets, S17 device type | low | - | - | - | Order per Linux; no extra |
| S19, S22 DBI stores | medium: DBI is dead while isolated or unclocked (`rc.c:1323-1324`) | - | - | - | Is DBI proven readable first (vendor/device id at `+0x0`, expect `0x144D`/`0xECEC` per research 12)? |
| S20 PERST high | low | - | - | - | The EP may now start link training: nothing to damage; the teardown is S24' |
| S21 ELBI options | low | - | - | - | none |
| S23 LTSSM enable | medium (a link that never trains cannot hang, but a half-programmed RC could; 100 ms bound) | - | - | - | Print the LTSSM state names (`common.h:88-130`) on timeout; teardown order |
| Teardown (section 6) | low | - | - | possible (iso RMW back) | Is it safe to leave WLAN_EN high after a failure? No: always power off |

## 6. Staged proposal and restore steps

Smallest first writer: **W2b `--wlan-en` = S01 + S02 + S03 + S04 + sample + restore: GPIO only, plain EL1 MMIO, no EL3, no PHY, no PMIC.**
Rationale: it is the least dangerous write in the whole sequence (the same three pins W1 already reads, so the block answers), it needs nothing the shim lacks (`try_write32` behind a single allowlisted
writer like `gpurailon.c`), and it is exactly what Linux does first. It answers "does the pin write take effect" (read-back of CON/DAT) and gives the first signs of life of the module (CLKREQ gph2-1,
DEVWAKE gph2-5, HOSTWAKE gpa7-0 sampled at 0/50/200 ms; whether they move is UNKNOWN, so a quiet result is *not* a failure). Its weakness: little observable payload by itself; its value is
that it removes the pin-write unknown before the larger stages. (Alternative first writer: S05 alone with read-back + restore - rejected as first because it is the only EL3 write and U1 could cost a reset.)

| Stage | Flag (proposed) | Content | Restore | Gate to proceed |
|---|---|---|---|---|
| W2-R | `--wlan-probe` + `--wlan-rails` | section 4 reads only | - | rails on (mode != 0); QCH words for PCIe look like the UFS one |
| W2a | `--wlan-smc-probe` | SMC READ (option 0) of `0x17463EC4`, print `x0`; compare with MMIO | none (no state change) | `x0` accepted -> U1 narrowed |
| **W2b** | `--wlan-en` | S01, S02, S03, S04, sample pins, restore | save CON/DAT words of gpp14 and gph2 first; restore: DAT bit2 := 0, gpp14 CON := saved, gph2 DAT/CON := saved; read back and print `restored=1` | read-back equals saved |
| W2c | `--wlan-iso` | (with W2b done) S05 RMW bit0 := 1; read back `0x17463EC4`; now open level 2 (SYSREG/ELBI/DBI/PMA **reads** as in W1); RMW bit0 := 0; read back | RMW back; if RMW back is refused, report (the bit is volatile PMU state) | level-2 reads return plausible values (DBI id `0x144D`...) |
| W2d | `--wlan-phy` | S07-S15 (stores + lock words), no PERST, no LTSSM | PHY power-down (`cal.c:37-67`), iso back, WLAN_EN low | PLL/CDR/OC lock bits |
| W2e | `--wlan-link` | S16-S23 + teardown | `rc.c:3622-3669` order: PERST low, LTSSM off, soft power reset pulse (20 us), `pwrdn` (`cal.c:37-67`), iso := 0, WLAN_EN low, 10 ms (`bcm4389-Kbuild:522`) | `link=11` |

Every stage needs an Opus review of its writer and an object audit (stack-only stores, one allowlisted writer, bounded loops, pstore checkpoints), a host test with a fake register window, and
an explicit user go-ahead before hardware. The shim must never retry the whole sequence (no loop of 10 like Linux): one attempt, teardown, report.

## 7. What this design does *not* decide

- It does not prove the L0 path is sufficient for the BCM4389 to enumerate (`ep-device-type EP_BCM_WIFI` only changes the PERST delay in the cited code, `rc.c:3183`).
- It copies Google's PHY tables verbatim from `cal.c`; whether our timing (busy-wait, no scheduler) matters is *guess*-level.
- BAR/config space, firmware download, DMA, interrupts: research 20 stages 2-3.

## 8. UNKNOWN list (nothing here was guessed into the sequence)

| # | Unknown | What would settle it |
|---|---|---|
| U1 | Whether EL3 accepts `0x82000504` on `0x17463EC4` from EL1 (write or RMW) | W2a (SMC READ) then W2c |
| U2 | What `x0` returns for a successful SMC READ | print it in W2a and compare with the MMIO read |
| U3 | What powers the BCM4389 module (VBAT, VDDIO, 32 kHz LPO/sleep clock): no DT node; `WLAN_EN` is the only handle; "WLAN-BT on O6" is only a power-meter channel label (`pmic.dtsi:1164`) | board schematic (not available) or a current measurement during W2b |
| U4 | Whether the plain PMU store at `PMU+0x3C10` (`rc.c:2692-2702`) works without SMC in Linux, i.e. which PMU registers EL3 leaves writable | not on the L0 path; read first (section 4) |
| U5 | The body of `dw_pcie_setup_rc()` (generic DWC host code) and the iATU programming | fetch Linux `pcie-designware-host.c` for the matching 6.1 tree before W2e |
| U6 | State of LDO17M/LDO19M (and L16M/L18M) at XNU entry | W2-R |
| U7 | State and bit layout of the HSI2 QCH debug words (`+0x7018`, `+0x7060`, ...) and of `CMU_HSI2+0x620`/`+0x800` | W2-R (compare PCIe vs UFS words) |
| U8 | Timing needs beyond Linux's (the module's real power-up time; 200 ms is Linux's value) | W2b sampling |
| U9 | Whether PMU alive state (iso bit) survives a warm/watchdog reset (it would then be set at the next ABL/Android boot) | read `0x17463EC4` after a reset following W2c |

## 9. Implementation status (2026-10-23, built, host-tested, audited; NOT run on hardware)

- W2-R = `--wlan-rails` (`shim/wlanrails.c`): the section-4 reads. Deviation from section 4: the S2MPG10 reads use a new `acpm_s10_read()` in `acpm.c` (the existing `acpm_pmic_read` only accepts the two touch rails 0x5C/0x5D), restricted to the four LDO control registers 0x53..0x56
  of soc.json `wlan` (`s10_l16m..l19m_ctrl`); the CMU_HSI2 words are read only behind the UFS HCI VER gate (readable and nonzero; observed 0x300).
- W2b = `--wlan-en` (`shim/wlanen.c`): narrower than section 6: **PERST is not driven** (read only, must be low), only WLAN_EN. Write set, cites and preconditions: `docs/AUTOMATION.md`. Order = Linux `samsung_gpio_direction_output` (DAT, then CON; `linux-pinctrl-samsung.c:693-696`,
  local copy `~/pixelios-artifacts/refs/wlan-stage1/`), restore = DAT low then CON input (the restore deliberately drives the pin low before releasing it, as `dhd_wlan_power(0)` does).
- The pin latch of an input pin is not observable (DAT reads the pin), so the restore writes DAT and CON unconditionally whenever an enable write was attempted.


## 10. Observed: rails and clocks before any write (run w2rails, 2026-10-23, `logs/probe/20261023T024430Z-w2rails_`, `--wlan-probe --wlan-rails`, read-only)
*Observed on hardware.* S2MPG10 L16M/L17M (PCIe PHY 0.85 V) = 0xCA, L18M/L19M (1.8 V) = 0xEC: all four **already ON** (mode 3) at the DT voltages (850000 / 1800000 uV, `dt_window=1`; selector decode from source, live-output assumption is a guess). So no PMIC write is needed for the PHY supply. CMU_HSI2: UFS HCI VER 0x300 (gate open), `PCIE_MUX (0x14400620)=0x10` (bit 4 set), `OSC (0x14400800)=0xF1000000`, `QCH_GPIO/PCIE_A/PCIE_B/SYSREG = 0x4`, `QCH_UFS = 0` (bit layout UNKNOWN: the QCH words of the PCIe blocks read like the always-needed GPIO/SYSREG ones, not like UFS; guess only). PMU `TXCO (0x17463C10) = 0x00000F0F` (bits 2,3,10 set = PMU_CHECK_MASK, rc.c:80,2698: satisfied). `PMU_PCIE1_ISO = 0` (isolated). Remaining steps of the chain are therefore: WLAN_EN (and unknown module supplies), PHY isolation release (EL3 SMC), PHY/controller init, PERST release, link training.

## 11. RESULT W2b: WLAN_EN raised and restored (run wlanen1, 2026-10-23, `logs/probe/20261023T025528Z-wlanen1_`)
*Observed on hardware* (`--wlan-probe --wlan-en`, `fastboot boot` only): preconditions held (WLAN_EN low, CON field 0, PERST low). GPP14 CON 0x33 -> 0x133 (pin 2 output), DAT 0x3 -> 0x7 (WLAN_EN high), 200 ms later the sample was `en=1 perst=0 clkreq=0 devwake=0 hostwake=0`, then restore CON=0x33 DAT=0x3 (`restore=ok`). Pads unchanged (PUD 0x1133, DRV 0, CONPDN 0xAA, PUDPDN 0x1111). **CLKREQ (gph2-1) went from 1 (before: GPH2.DAT=0x2) to 0 (after: GPH2.DAT=0) while WLAN_EN was high**: something pulled the clock-request line low once the module was enabled (*guess*: the BCM4389 asserting CLKREQ# = it is powered and requests the reference clock; not proven). PMU PCIe1 ISO stayed 0 (still isolated). No hang, no side effects after restore.
Next (research 29 sections 4-7): release the PHY isolation (EL3 SMC, needs an audited 5-argument helper; first a read-only SMC READ test W2a), then PHY/controller init, PERST release, link training. Each is a new writer with an Opus review.

## 12. W2a built (`--wlan-smc-read`) and the delta to W2c (the isolation-release write)

**W2a (built, host-tested, audited, NOT run).** The image can issue exactly these SMCs: `smc #0` with `x0 = 0x82000504`, `x1 = 0x17463EC4` then `0x17463EC0`, `x2 = 0`, `x3 = 0`; nothing else (`shim/wlansmc.c`, docs/AUTOMATION.md). Facts re-verified against the local sources:
- Function id `SMC_CMD_PRIV_REG = 0x82000504` (`exynos-smc.h:45`, a fast SMC32 call, owner SIP: bit31 = 1, bit30 = 0, owner 2, `linux-arm-smccc.h:20-23,48`); options READ/WRITE/RMW = 0/1/2 (`exynos-el3_mon.h:21-23`); `get_priv_reg()` = `arm_smccc_smc(SMC_CMD_PRIV_REG, reg, PRIV_REG_OPTION_READ, 0,0,0,0,0, &res)`, returns `(int)res.a0` (`exynos-pd_el3.c:54-63`) [read from source].
- Results: SMCCC returns in x0..x3; Google's callers use only `res.a0`. Success value of a READ is UNKNOWN (U2): the run prints a0..a3; errors: `-EINVAL` for a refused register (mainline `exynos-pmu.c:65,82`), `-1` (SMCCC_RET_NOT_SUPPORTED, `linux-arm-smccc.h:247-249`) for an unknown function id [read from source / SMCCC].
- Conduit: the shim runs at EL1 (start.S drops from EL2; `HCR_EL2 = RW` only, `start.S:43-44`, SMC not trapped) and already issues `smc #0` for PSCI (answered, observed) and for this function id with option 1 on `SYSIP_DAT0` (`shim.c:219`, observed `SMC ret 0 after 0xFC`, `docs/archive/HANDOFF-s01-s06.md:374,382`; the CPU_INFORM clear at `shim.c:299` has no cited result); the DT PSCI method is SMC (soc.json notes.psci). So the conduit/EL is the one EL3 already serves.
- The existing `smc()` in `shim.c:113-123` is `static`, 4-argument, used for PSCI (`:608-634,911,1019,1029,1183,1290`), the reboot-to-bootloader experiment (`:219,229`) and the CPU_INFORM clear (`:299`); the new helper is a separate, constant-only function in its own object so its audit can pin the registers.
- *Guess:* a successful READ of `0x17463EC4` means it is writable (mainline `exynos-pmu.c:91-92`: "only registers that can be written are readable via SMC"); a refusal is evidence against. Neither is proof for RMW.

**W2c design delta: the exact isolation-release write** (not built; needs an Opus review; do NOT widen `wlansmc.c`):
- Call: `smc #0`, `x0 = 0x82000504`, `x1 = 0x17463EC4`, `x2 = 2` (PRIV_REG_OPTION_RMW), `x3 = 0x1` (mask), `x4 = 0x1` (value = PCIE_PHY_BYPASS, `common.h:51-52`); return `x0 == 0` = accepted (Linux: `rmw_priv_reg(pmu_alive_pa + pmu_offset, PCIE_PHY_CONTROL_MASK, val)`, `rc.c:401-402`; arg layout `exynos-pd_el3.c:66-77`; mask `rc.h:120`). Restore: the same call with `x4 = 0` (PCIE_PHY_ISOLATION).
- Semantics: `new = (old & ~mask) | val` is the *guess* (Linux's `regmap_update_bits` meaning; the EL3 code is not available); Linux always passes `val` inside `mask`, so the helper must too (`val` constant 1/0, `mask` constant 1). The 5-argument form needs an inline-asm helper with a constant x4 (the existing `smc()` has four): the audit must pin x0 = 0x82000504, x1 = 0x17463EC4 (constant, no parameter), x2 = 2, x3 = 1, x4 in {0, 1} as the last writes before the one `smc`, exactly like `check_wlansmc_object_text`.
- Order in the run: read MMIO (`0x17463EC4` must be 0) -> W2a-style SMC READ (must not be `error`, else stop) -> RMW set -> MMIO read-back must be 1 -> (optionally the gated level-2 reads of `--wlan-probe`, which open once the bit is 1 and the CMU mux bit 4 is set) -> RMW clear -> read-back must be 0 -> loud `!!! RESTORE FAILED` otherwise. One attempt, no retry.
- Review questions (Opus): (1) Is the READ result consistent (a0 value vs status) before an RMW is attempted; may W2c run with `ambiguous=1`? (2) Must WLAN_EN stay low during W2c (no module powered while the PHY is un-isolated)? Proposal: yes, W2c is independent of W2b. (3) What does an un-isolated, unconfigured PHY do to the pads (CLKREQ/REFCLK) with the module off? (*guess*: nothing) (4) If the RMW returns 0 but the MMIO read-back stays 0, is the restore still attempted (yes: an RMW clear is idempotent)? (5) May the gated level-2 reads (SYSREG/ELBI/DBI/PMA) run while the bit is set, given the CMU/QCH words observed in run w2rails? (6) A fault of EL3 (hang/reset) is recoverable by the buttons; nothing persistent is written; confirm the PMU bit does not survive a cold power-off (U9).

### 12.1 W2c built (`--wlan-iso`, 2026-10-23, host-tested and audited, NOT run)
- Re-derived from the source copies: Linux releases the isolation as the **first** step of `exynos_pcie_rc_poweron()` for state LINK_DOWN (`rc.c:3387-3389`), before `clock_enable`, `pinctrl_select_state` and `phy_all_pwrdn_clear`/`phy_config` (`:3395-3425`), with **no delay** (no udelay/msleep in `:3387-3425`); the isolation is restored as the last step of `rc_poweroff` (`:3667-3669`, after PERST low, LTSSM off, soft power reset, `phy_all_pwrdn`). Bit 0 of `0x17463EC4`: `PCIE_PHY_ISOLATION = 0`, `PCIE_PHY_BYPASS = 1` (`common.h:51-52`), mask `PCIE_PHY_CONTROL_MASK = 1` (`rc.h:120`): **1 = bypassed = released** (the isolation cells between the PHY domain and the core; the PHY's supplies are the LDO rails, observed on in W2; what the cells do exactly is a *guess*), consistent with the probe gate. The call is `rmw_priv_reg(pmu_alive_pa + pmu_offset, PCIE_PHY_CONTROL_MASK, val)`, falling back to `regmap_update_bits` only if the SMC returned nonzero (`rc.c:393-407`) - which on this phone would be a plain PMU store (not tried by W2c).
- ABI: `arm_smccc_smc(a0..a7)` copies a0..a7 to x0..x7 (`linux-arm-smccc.h:380-390`) and `rmw_priv_reg` passes `(SMC_CMD_PRIV_REG, reg, PRIV_REG_OPTION_RMW, mask, val)` = **x0, x1, x2, x3, x4** (`exynos-pd_el3.c:66-77`; ids `exynos-smc.h:45`, options `exynos-el3_mon.h:21-23`). x4 therefore exists in the call; SMCCC returns x0..x3 (only a0 is used by Linux).
- Built exactly as the section above proposed, with these choices: the release is never repeated; the bounded wait is ours (10 x 100 us); the clear is sent only when the bit reads set or the read-back failed (so an RMW that was accepted but not applied is NOT followed by a clear - the late-apply risk below); the helpers `smc_priv_rmw_set` / `smc_priv_rmw_clear` are two separate noipa symbols with constants only; `wlansmc_iso.o` is linked only with `--wlan-iso`, a plain `--wlan-smc-read` image has no RMW code. Run order in `shim.c`: rails, smc read, iso, WLAN_EN writer; W2c requires WLAN_EN low (the module is NOT powered while the PHY is un-isolated).
- Opus review questions that remain: (1) an EL3 that accepts the RMW but applies it after the 1 ms poll leaves the isolation released (no clear is sent because the read-back shows 0): add a clear whenever a0 == 0? (2) the observed word is 0, so a full-overwrite EL3 is not distinguishable here; is a probe write of another value acceptable (no)? (3) should the gated level-2 reads (SYSREG/ELBI/DBI/PMA) run in the released window (they are the point of W2c; proposed as the next flag, not mixed into this writer)? (4) the fallback of Linux (plain regmap write on SMC failure) is deliberately not implemented. (5) is a dwell time between set and clear needed to see anything (none now: the set/clear pair is ~1 ms)?

### 12.2 Fixes after review 2 (docs/reviews/wlan-stage1-review-2.md), 2026-10-23, host-tested and audited, NOT run
- F1: the one clear RMW follows EVERY sent set (idempotent when the bit is 0), then a 10 ms wait and one final read (`ISO final`); the restore verdict needs bit 0 == 0 in the poll AND in the final read. The late-apply EL3 of the host test now ends cleared; an apply that survives the clear and lands at the final look is reported `restore=FAILED`. Answers 12.1 Q1/Q5 (no dwell).
- F2: a `--wlan-iso` image compiles out level 2 and its SUMMARY (`l2 skipped`), and cannot contain `--wlan-en` (build.sh refuses, `#error` in shim.c); pinned in `check_shim_wlan_sequence` with three mutations. Answers Q3: the gated level-2 reads need their own flag and review.
- F3: the SMC READ value must equal the MMIO word before the set (at the observed word 0 a status 0 and a value 0 agree: allowed). F4: the W2a mutation "option as a parameter" is back. F5: the "SMCs the image can issue" sentence is scoped to the `wlansmc_iso.o` object. F6: the cite is `docs/archive/HANDOFF-s01-s06.md:374,382` (SYSIP_DAT0 option 1: `SMC ret 0 after 0xFC`); the CPU_INFORM clear has no cited result. F7: x5..x7 = 0 in both RMW helpers like Linux's `arm_smccc_smc(.., 0, 0, 0)`, pinned in the object audit. F8: bit 0 = 1 is worded "isolation cells bypassed / released" (*guess* on what they do; the PHY's supplies are the LDO rails, observed on), and "a successful READ means it is writable" is tagged *guess*.


## 13. RESULT W2c: PCIe1 PHY isolation released and restored via EL3 (run iso1, 2026-10-23, `logs/probe/20261023T033946Z-iso1_`)
*Observed on hardware* (`--wlan-probe --wlan-iso`, Opus review wlan-stage1-review-2 applied): preconditions ok; `SMC RMW (fid 0x82000504, option 2, x1=0x17463EC4, mask 1, value 1)` returned 0; MMIO read-back `PMU_PCIE1_ISO` 0 -> **1** (PCIE0_ISO unchanged 0, other bits unchanged); the unconditional clear (value 0) returned 0 and the final read (10 ms later) shows 0: `WLAN ISO release=ok readback=0x00000001 restore=ok`. So EL3 accepts the RMW with mask/value semantics as assumed (readback proves them, no longer a guess for this register). Next in the chain (section 4): controller/PHY init, PERST release, link training; the isolation release must be followed by the PHY register programming in the same run.
