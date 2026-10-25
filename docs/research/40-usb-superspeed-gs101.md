# 40. Why the Pixel 6a only ever shows USB 2 high speed, and what SuperSpeed would take

Date: 2026-10-24. Role: researcher (read-only; no phone access, no adb/fastboot). Tags: [obs] observed on this host or in
repo logs, [src] read from a cited source, [guess] inference or recall, not checked.

Sources opened this session (all fetched with curl/WebFetch, not committed):
- AOSP `kernel/google-modules/soc/gs` branch `android-gs-bluejay-6.1-android16` ("GS" below): `drivers/usb/dwc3/dwc3-exynos.c`,
  `drivers/phy/samsung/phy-exynos-usbdrd.c`, `phy-exynos-usbdp-gen2-v4.c`, `phy-exynos-usb3p1.c`.
  URL base: https://android.googlesource.com/kernel/google-modules/soc/gs/+/refs/heads/android-gs-bluejay-6.1-android16/
- AOSP `kernel/devices/google/gs101` same branch: `dts/gs101.dtsi`, `dts/gs101-common-typec.dtsi`, `gs101_defconfig`.
  URL base: https://android.googlesource.com/kernel/devices/google/gs101/+/refs/heads/android-gs-bluejay-6.1-android16/
- Linux v6.1 `drivers/usb/dwc3/core.h`, `gadget.c` (raw.githubusercontent.com/torvalds/linux/v6.1/...).
- AOSP `platform/system/core` `libsparse/sparse_read.cpp` (main).
- Not fetched: any `android-gs-bluejay-5.10-*` branch (the ref list for `soc/gs` and `devices/google/gs101` only has
  `android-gs-bluejay-6.1-*` and `android-gs-bluejay-android15/16*` branches; no 5.10 branch exists there). The 6.1 sources are
  the same driver family; any difference from the 5.10 tree the phone's stock Android 13 shipped is [guess]: none expected.
- Not fetched: the max77759 TCPC driver (`drivers/usb/typec/tcpm/google/`, directory exists, [src] listing of
  `drivers/usb/typec/` shows `tcpm` -> `google`); its polarity path is therefore only known from the PHY side (below).

## Short answer

1. The USB-C data path on gs101 has NO external SuperSpeed mux chip in the DT: the lane selection per cable orientation is a
   register of the USBDP combo PHY itself (`CMN_REG00B8.LANE_MUX_SEL_DP`), written by the PHY driver from the Type-C polarity
   that the max77759 TCPC reports through extcon [src, GS phy-exynos-usbdp-gen2-v4.c:1507-1524; phy-exynos-usbdrd.c:1555-1576].
   Software must set it per orientation. High speed (D+/D-) has no orientation dependence, which is why HS always works [guess].
2. Every observation of the phone in fastboot (ABL) shows HS: DSTS speed=0 with run_stop=1 and a non-zero device address, DCFG
   speed field 0 (HS) [obs, below]. The most likely reading is that ABL's fastboot caps itself at HS (DCFG.DEVSPD written to 0);
   that is ABL's choice and not changeable by us [guess, not proven: needs an ABL decompile of the DCFG write].
3. Our gadget is HS-only by construction (DCFG forced, 2.0 descriptors, 512 B HS bulk), so even with a perfect cable it would
   be HS [src, below]. And the SS PHY state ABL leaves is unknown: only the USBCON bank at 0x11100000 was ever dumped, never
   PMA 0x110E0000 / PCS 0x110F0000.
4. Recommendation: do not build SuperSpeed into our gadget; do the two zero/low-cost tests first (section 5).

## 1. What Linux needs for SuperSpeed on gs101

### 1.1 Controller and glue
- DWC_usb31, GSNPSID 0x33313130, base 0x11110000, global block +0xC100 [obs, device/google/bluejay/hardware/probe-log-2026-10-21-run1.txt:317;
  src: shim/usbprobe.h:10-23].
- DT node `usb@11110000` compatible `samsung,exynos9-dwusb` (the glue, `dwc3-exynos.c:43`), with a child `dwc3` node compatible
  `synopsys,dwc3`, `dr_mode = "peripheral"`, `phys = <&usbdrd_phy0 0>, <&usbdrd_phy0 1>` named `usb2-phy`, `usb3-phy`
  [src, GS-gs101 dts/gs101.dtsi:630-667]. Clocks aclk=GATE_USB31DRD_SLV_LINK, sclk=MUX_HSI0_USB20_REF, bus=VDOUT_CLK_TOP_HSI0_BUS,
  power domain `pd_hsi0` [src, dts/gs101.dtsi:632-640].
- There is NO `maximum-speed` property in the DT, so the Linux default (SuperSpeedPlus) applies [src, dts/gs101.dtsi:630-667
  has none; Linux dwc3 default is SSP, [guess] from memory of core.c, not opened]. Runtime override: sysfs `force_speed` accepts
  `super-speed-plus|super-speed|high-speed|full-speed` and writes `dwc->maximum_speed` [src, dwc3-exynos.c:1089-1133].
- Glue SS tweaks (DWC31 180A..190A): LLUCTL pipe-reset, `DWC3_FORCE_GEN1` when the DT bool `force_gen1` is set and force_speed is
  not SSP, LSKIPFREQ PM timers [src, dwc3-exynos.c:262-285, 900-901]; GUSB3PIPECTL DEP1P2P3 / DISRXDETINP3 / UX_EXIT_PX tweaks
  [src, dwc3-exynos.c:235-252]. These are tuning, not what makes SS link up.
- Upstream dwc3 SS gadget behaviour that our code lacks, from Linux v6.1 gadget.c: DCFG speed field (SUPERSPEED_PLUS=5,
  SUPERSPEED=4, HIGHSPEED=0, FULLSPEED=1) [src, core.h:415-419]; DSTS connect speed decode and ep0 maxpacket 512 for SS/SSP, 64
  for HS/FS, re-applied on every ConnectDone [src, gadget.c:3942-3984]; DEPCFG burst size field `DWC3_DEPCFG_BURST_SIZE(burst-1)`
  [src, gadget.c:597]; link-state change events (DEVTEN.ULSTCNGEN) [src, gadget.c:2638]; U1/U2 accept in DCTL
  (`DWC3_DCTL_ACCEPTU1ENA`) [src, gadget.c:4132]; TX FIFO resizing (`dwc3_gadget_calc_tx_fifo_size`) [src, gadget.c:661-677, 2945].
  GS's DT sets `tx-fifo-resize = <0>` [src, dts/gs101.dtsi:645], so it relies on the reset FIFO sizes.

### 1.2 PHY: not `phy-exynos5-usbdrd`; Google uses `phy-exynos-usbdrd` + USBDP gen2 v4
- DT node `phy@11100000` compatible `samsung,exynos-usbdrd-phy`, four regions: 0x11100000 (size 0x200, USBCON bank), 0x110E0000
  (0x2800, PMA), 0x110F0000 (0x800, PCS), 0x11110000 (0x10000, the dwc3 itself) [src, dts/gs101.dtsi:669-676];
  ref clock 26 MHz (`phy_ref_clock = <26000000>`), `has_combo_phy = <1>`, `phy_version = <0x301>`, `sub_phy_version = <0x404>`,
  `phy_refsel_clockcore = <1>`, `used_phy_port = <0>` [src, dts/gs101.dtsi:678-727]. The driver maps regions 1/2 as `pma_base`/
  `pcs_base` [src, phy-exynos-usbdrd.c:2362-2383].
- defconfig builds `CONFIG_PHY_SAMSUNG_USB_GEN2_V4=y`, `CONFIG_PHY_EXYNOS_USBDRD=m`, `CONFIG_USB_DWC3_EXYNOS_GS=m`,
  `CONFIG_TYPEC_MAX77759=m` [src, gs101_defconfig grep]. So the PIPE3 init that runs is the `GEN2_V4` branch of
  `exynos_usbdrd_pipe3_init` [src, phy-exynos-usbdrd.c:1544-1577].
- Sequence of `phy_exynos_usbdp_g2_v4_enable()` [src, phy-exynos-usbdp-gen2-v4.c:3187-3231]: `ctrl_pma_ready` -> `aux_force_off` ->
  PMA default SFR table (26 MHz table; separate 19.2 MHz tables exist) -> `set_pcs` -> `tune` -> `pma_lane_mux_sel` ->
  `ctrl_pma_rst_release` -> PLL lock check -> CDR lock check -> 10 ms -> `tune_late` -> offset-cal check. The register tables are
  long (the file is 3255 lines); they were NOT transcribed here. Before it, `exynos_usbdrd_pipe3_init` powers the PHY with
  `phy_isol(inst, 0, mask)` (PMU word at +0x3EB4, USBDP) [src, phy-exynos-usbdrd.c:1549-1551, 624-634; dts pmu_offset_dp 0x3EB4,
  dts/gs101.dtsi:684-686], and `phy_exynos_usb_v3p1_g2_pma_ready()` first [src, :1579].
- The PMU words are written through `rmw_priv_reg` (a secure/SMC path) [src, phy-exynos-usbdrd.c:624-634]; the shim only reads them.
- Control sequences around the PHY (CLKRST `LINK_PCLK_SEL`, `COMBO_PMA_CTRL` ROPLL/LCPLL ref-clk select, low power) are in the
  USBCON bank at 0x11100000 [src, phy-exynos-usbdp-gen2-v4.c:3234-3253 (`disable`)]. The LCPLL/REFCLK is selected there; exact
  bit values for the "up" direction were not read [Unknown U3].

### 1.3 Type-C orientation / SS lane mux: this is the likely blocker
- `used_phy_port` (0 or 1) selects which pair is SS. `pma_lane_mux_sel` writes `CMN_REG00B8.LANE_MUX_SEL_DP` = 0xC for port 0,
  0x3 for port 1, and sets the `ln3`/`ln1` `TX_RXD` overrides accordingly [src, phy-exynos-usbdp-gen2-v4.c:1507-1546].
- `used_phy_port` comes from extcon `EXTCON_PROP_USB_TYPEC_POLARITY` of the TCPC (`max77759tcpc`, I2C `hsi2c_12` address 0x25,
  `orientation-switch`, `usb-role-switch`) [src, phy-exynos-usbdrd.c:1555-1576; dts/gs101-common-typec.dtsi:115-131 (the
  `max77759tcpc@25` node)]. If extcon is unavailable the default is 0 and an error is logged [src, phy-exynos-usbdrd.c:1566-1570].
- There is no external SS crossbar in this DT (the only other Type-C chips are `max20339ovp@35`, an OVP/load switch, and a
  disabled `fusb307b`) [src, dts/gs101-common-typec.dtsi:67-114]. So the "mux" is the PHY lane select: software MUST know the CC
  orientation before it brings the SS lane up. Our shim/XNU has no TCPC driver [guess: no max77759 code in the repo; not grepped],
  so it has no orientation. Options: read the TCPC CC status over I2C (new driver, new register map: Unknown U4), or try both
  lane settings (re-init per attempt; risk of a PLL/CDR lock hang is untested).
- The DT also sets TCPC `sink-vdos` UFP VDO with `UFP_USB32_GEN1` and `DEV_USB3_CAPABLE` (5 Gbit/s) [src,
  dts/gs101-common-typec.dtsi:164-169]; the Pixel 6a public spec says "USB Type-C 3.1" [src, gsmarena Pixel 6a page, WebFetch
  2026-10-24; https://www.gsmarena.com/google_pixel_6a-11229.php]. So the target is Gen 1 (5 Gb/s), not 10 Gb/s [guess: gs101
  hardware can do SSP per the `DWC3_DCFG_SUPERSPEED_PLUS` mention and "gen2" PHY names, but the Type-C advertisement says Gen 1].
  The 10 Gb/s on the host root hubs is the host's, irrelevant to the phone's rate.

### 1.4 What ABL leaves configured at handoff [obs from the shim probe logs]
The probes run inside a shim that was entered by `fastboot boot`, i.e. after ABL's fastboot used the USB:

| Log | DCTL | DSTS | DCFG | GUSB3PIPECTL0 | GUSB2PHYCFG0 |
|---|---|---|---|---|---|
| ufsdiag-survey (hardware/ufsdiag-survey-2026-10-21.txt:328-343) | 0x80000000 run_stop=1 | 0x00839B00 speed=0 (HS), halted=0 | 0x002008C0 (devspd bits [2:0]=0, devaddr 0x18) | 0x11021102 susphy=1 | 0x00102400 |
| ufs-gpt-read (ufs-gpt-read-2026-10-21.txt:427-436) | (run_stop=1) | 0x00030FB0 speed=0 (HS) | 0x00200A40 (devspd 0, devaddr 0x48) | 0x11021102 | 0x00102400 |
| ufs-clkfix (ufs-clkfix-attempt-2026-10-21.txt:390-399) | 0x80000000 run_stop=1 | 0x00833918 speed=0 (HS) | 0x002008D8 (devspd 0, devaddr 0x1B) | 0x11021102 | 0x00102400 |
| probe-log run1 (probe-log-2026-10-21-run1.txt:318-327) | 0x00F00000 run_stop=0 | 0x00D22734 speed=4 (SS) halted=1 | 0x00200885 (devspd bits=5, devaddr 0x10) | 0x11021102 | 0x00102400 |

Reading [guess unless stated]:
- Decode: DSTS/DCFG speed 0=HS, 4=SS, 5=SSP [src, Linux core.h:415-419, 510-512]. DSTS in the first three rows is the ABL fastboot
  link already up at HS (run_stop=1, non-zero device address) [obs].
- DCFG devspd=0 while ABL's fastboot ran, vs 5 in the run where ABL's controller was stopped (run1) [obs]. Either ABL wrote
  devspd=HS (a deliberate cap) or this field is not the reset default I assume. The reset-value reading is a guess; settling it
  needs ABL's USB init decompile (`tools/rea`, ABL images are in ~/pixelios-artifacts/abl-code per prior sessions [guess]).
- run1's "speed=4 (SS)" is a halted controller's stale/default DSTS field, not evidence of an SS link (halted=1, run_stop=0) [guess].
- GUSB3PIPECTL0 0x11021102 has SUSPHY (bit 17) set in all rows [obs; bit per Linux core.h:314], i.e. the SS PIPE interface is in
  suspend/powerdown permission, consistent with an unused SS lane but not proof [guess].
- The USBDP PMU word reads 1 (powered?) and the USB20 word 3 in every row [obs, e.g. probe-log-2026-10-21-run1.txt:336;
  `usbdp=00000001`]. Whether bit 0 = powered is [guess] from `phy_isol` writing `mask` to power on [src, phy-exynos-usbdrd.c:624-634].
- The PHY dump in the logs is the 0x11100000 bank only (PHY+00..+3C) [obs, e.g. probe-log:332-335], not PMA/PCS. Whether ABL left
  the SS lane PLL/CDR trained is unknown (U1).

## 2. Host side

- [obs] Right now `/sys/bus/usb/devices/1-5` is product "Sweet iOS (USB Ethernet)" (OUR gadget, not fastboot), `speed` 480, on
  bus 1 (xhci root hub, 10 ports, 480M); buses 2 and 4 are xhci 4-port 10000M root hubs with nothing attached; bus 3 is 480M with an
  HID at 12M (`lsusb -t` and `/sys/bus/usb/devices/usb{1..4}/speed`, 2026-10-24). So the phone hangs off the USB 2 side of the host.
  For fastboot (18d1:4ee0) the same port/bus is expected, but at the moment of this research the phone was not in fastboot,
  so the premise "fastboot at 480M on 1-5" is [guess] from the brief, though consistent with the DSTS HS rows above [obs].
- A USB-C host port presents the USB 2 and USB 3 root hubs side by side; the phone shows on the SS root hub (bus 2/4) only if both
  ends link-train SS over a cable with the SS pairs. So seeing bus 1 is consistent with any of: HS-only cap in ABL, non-SS cable,
  or no SS PHY training [guess].
- Repo evidence about the cable/port: none found. `grep -ri "cable\|superspeed\|usb 3\|5000M\|10000M"` over
  `~/.t3/projects/pixelios/docs`, `NOTES.md` and the kernel `docs/` only returned user-action wording ("check cable")
  and the USB gadget history; no document records which cable or port is used or the fastboot link speed. NOTES.md:307 says the
  host "enumerates the phone at high speed" for the sweet port, which is the sweet gadget, not bluejay fastboot. HANDOFF/GATE0
  results record the bluejay gadget as `conndone speed=0` (HS) [obs, device/google/bluejay/docs/GATE0_RESULTS.md:25].
- Public knowledge about Pixel 6a fastboot link speed: none found. A web search (2026-10-24) returned only driver/cable
  troubleshooting threads (e.g. https://discuss.grapheneos.org/d/1920-pixel-6a-please-help-with-windows-driver-and-device-recognition)
  and no measurement of fastboot USB speed. The port spec is "USB Type-C 3.1" [src, gsmarena, above]. Whether stock fastboot
  negotiates SS on Pixel 6a is [guess: unknown; I recall reports that Pixel fastboot runs at USB 2 speed on some devices but have no
  source, treat as unverified].

## 3. Measured flash throughput

- [obs] userdata flash of `ios-root3.img`: 467 s (docs/HANDOFF.md:53); the image is 16 GiB apparent, 7.3 GiB allocated
  (docs/PLAN-BOOT-SPEED.md:47). 16 GiB / 467 s = 36.8 MB/s (35.1 MiB/s) if the whole image crossed the wire; 7.3 GiB / 467 s =
  ~16.8 MB/s if only allocated data did [calc].
- Ceiling of USB 2 HS bulk: theoretical max 53 MB/s ("maximum theoretical data throughput 53 MByte/s" [src, Wikipedia USB 2.0,
  WebFetch 2026-10-24: https://en.wikipedia.org/wiki/USB_2.0]); that equals 13 bulk packets x 512 B x 8000 microframes/s = 53.2 MB/s
  [calc]. Practical sustained bulk ~35-45 MB/s [guess, common figure, not sourced here].
- Does the whole 16 GiB cross the wire? Open question. libsparse turns each block whose words are all equal into a FILL chunk
  (a header, not data): `sparse_block` check then `sparse_file_add_fill(s, buf[0], ...)`, comment "add flag to use skip instead of
  fill for buf[0] == 0" [src, AOSP libsparse sparse_read.cpp:460-494]. Whether `fastboot flash` of a raw image goes through this
  path (it calls `sparse_file_import_auto`) is [guess, not read in fastboot's source]. If yes, ~8.7 GiB of zeros are sent as FILL
  chunks and ABL must still write them to UFS, so the data crossing the wire is ~7.3 GiB at ~16.8 MB/s, about a third of the HS
  ceiling. This contradicts the guess in docs/PLAN-BOOT-SPEED.md:63 ("fastboot has no hole detection for raw files", marked guess
  there); the flash log's "Sending sparse N/M" count and the per-part sizes in `fflash-userdata.sh`'s log settle it (U5).
- UFS write side: UFS runs HS-G4, 2 lanes after ABL/shim negotiation [src, soc/google/gs101/docs/07-gs101-ufs-bringup.md:256-262].
  The link is multi-GB/s; the device's sustained sequential write is typically a few hundred MB/s [guess, not measured here; no
  repo measurement of UFS write rate found].
- Implication [guess, conditional]: if the full 16 GiB crossed the wire, 36.8 MB/s is ~70% of the 53 MB/s ceiling and ~80-90% of a
  realistic HS ceiling, so USB 2 would be the bottleneck and SS could roughly halve the flash time (to the UFS/ABL limit). If only
  7.3 GiB crossed (libsparse behaviour), USB 2 is NOT the bottleneck (16.8 MB/s is ~1/3 of the ceiling): the time is then ABL's
  per-chunk handling, the 8.7 GiB of zero FILL writes to UFS, and host-side sparse splitting. Either way, speeding the flash up needs
  ABL to run SS, which is outside our code; (b) does nothing for it.

## 4. Cost/benefit for SuperSpeed in OUR gadget (b)

Our traffic: UDP log over ECM and the ECM ping used for bring-up (usbnet-log), plus the live log patches [src, AGENTS.md]. It is
small packets at low rate, far below HS bulk capability; the log is paced by the XNU/driver side, not by the link [guess; no
measured throughput of the UDP log found in the repo: `grep -i "mbit|MB/s|throughput|iperf"` over docs found nothing for the gadget].

Where the HS assumptions live in our code [src]:
- DCFG: `USBG_DCFG_DEVSPD_MASK`, `USBG_DEVSPD_HS = 0`, cleared with the mask at bring-up [shim/usbgadget.c:69-71, 654-657];
  XNU 0071: `dwc_wr(DCFG, dcfg | 0)` "High speed, address 0" [xnu/patches/0071-sweet-usb-device-ep0.patch:400-402].
- ep0 DEPCFG MPS 512 until ConnectDone [shim/usbgadget.c:672-678; 0071:406-410] (correct for SS, wrong for HS but fixed up at
  ConnectDone, shim/usbgadget.c:469); data EP MPS 512 (HS bulk) [shim/usbgadget.c:335; 0073:157-159]. SS bulk needs 1024 and an
  endpoint companion descriptor.
- Descriptors: bcdUSB 2.00 and qualifier "high speed only" [shim/usbproto.c:38, 52-58]; 512-byte bulk endpoint descriptors
  [shim/usbproto.c:135, 142]; ECM 0073:118-119. No BOS (USB 2.0.1 extension + SuperSpeed capability), no SS endpoint companion,
  no bcdUSB 3.00 SS device descriptor with bMaxPacketSize0 = 9 (512 = 2^9) [guess from the USB 3 spec, not opened].
- Events: DEVTEN 0x7 only (disc/reset/conndone); link-state events are not enabled and are decoded for display only
  [shim/usbgadget.c:73-85]. SS needs U1/U2 handling and LTSSM (recovery) handling [src, gadget.c:2638, 4132].
- The shim/XNU path performs a DCTL soft reset and leaves "PHY and global setup as ABL left them" [0071:382-383, 0073:456].

| # | Work item | Size | Notes |
|---|---|---|---|
| 1 | DCFG devspd = SS (4) in shim and XNU 0071/0073/0082; ConnectDone decode of speed 4/5 | S | Registers known [src core.h:415-419]. Alone it just makes the controller wait for an SS link that never trains if the PHY is not up. |
| 2 | Descriptors: bcdUSB 0x0300/0x0210 + BOS + SS endpoint companion; bMaxPacketSize0 9; 1024-byte bulk; ECM/ACM functional descriptors unchanged; rewrite `usbproto.c`, its host test, XNU 0073 tables | M | Host-testable bytes. Two descriptor sets (HS fallback must still work: the same gadget will meet USB 2-only cables). |
| 3 | DEPCFG for SS (burst size, ep companion fields), TX FIFO sizing for 1024-byte bursts, `GTXFIFOSIZ` check | M | Reset FIFO sizes may suffice (GS sets `tx-fifo-resize = <0>`, dts/gs101.dtsi:645) [guess]. Needs a read of GTXFIFOSIZ/GRXFIFOSIZ from hardware (Unknown U6). |
| 4 | Link-state events and U1/U2 accept (DEVTEN.ULSTCNGEN, DCTL ACCEPTU1/U2 enable), plus recovery handling | M | New hardware state machine paths; no log evidence of any SS event on this phone. |
| 5 | SS PHY bring-up: PMU USBDP power (secure write via SMC path, [src phy-exynos-usbdrd.c:624-634]), USBCON CLKRST/COMBO_PMA_CTRL, PMA 0x110E0000 table, PCS 0x110F0000 table, lane mux, PLL/CDR lock polls | L | Thousands of register values (gen2-v4.c is 3255 lines); must be reproduced from source with cites, per AGENTS rule 7; hang risk on the first wrong write (PCIe PHY hang is documented in docs/research/30-pcie1-phy-access-hang.md). Possibly unnecessary if ABL already trained the lane (U1): a read-only dump of PMA/PCS settles this first. |
| 6 | Type-C orientation: read the max77759 CC status over I2C (hsi2c_12, 0x25) or try both lane muxes; VBUS/role is already set by ABL | M-L | New TCPC register map to document (U4). This is the gating unknown. Without it the SS lane is a coin flip per replug. |
| 7 | XNU gadget changes (0071/0073/0082): speed handling, 1024-B bulk, zero-length-packet rule at 1024 (`len % 512` today, 0073:276-278), companion descriptors | M | Patch stack is contiguous; edits go into new patches or amend per the stack rules. |
| 8 | Hardware runs + reviews (Opus, hardware-writing: PHY and PMU writes) | M | At least a dump run, a PHY run, an enumerate run; the reviewer must audit every PHY write against the Google source. |

Expected gain for our traffic: negligible [guess]. The UDP log and ECM ping are latency/rate-limited far below the HS ceiling;
SS would shave microseconds of transfer time per frame. It does NOT speed up the userdata flash (that is ABL's fastboot). The one
plausible benefit is the link-bring-up behaviour (HS enumeration already works, observed repeatedly). The cost is the L item (5)
plus an unmapped TCPC dependency, in a code path that can hang the SoC.

## 5. Recommendation: do the cheap test first, do NOT build SS into our gadget now

Cheap tests, ordered by cost:
1. (zero code) Put the phone in fastboot (via `tools/phone goto fastboot`, human attaches), connect with a cable known to carry the
   SS pairs into a host port that belongs to bus 2/4 (the 10 Gb/s root hubs), and read `/sys/bus/usb/devices/*/speed` and
   `product` (via `tools/phone state`/lsusb -t). If fastboot goes SS on a good cable, the "HS" was just the cable/port and
   ABL does support SS. If not, ABL caps itself at HS (consistent with DCFG devspd=0) and (a) is closed: we cannot change it.
2. (read-only shim extension, S-M) Add PMA 0x110E0000 (first 0x80 words), PCS 0x110F0000 and USBCON CLKRST/COMBO_PMA_CTRL reads
   to `shim/usbprobe.c`/`usbprobe.h` and run once in fastboot-booted state, with a port that DID train SS in test 1. It answers
   U1 (does ABL leave the SS PHY trained?). Reads there may hang if the block is clock-gated; the existing probe logic reads the
   0x11100000 bank without trouble [obs probe-log:332-335] but PMA/PCS reads are untested (R7: check against the allowlist).
3. (decompile, no phone) Reverse ABL's USB init for the DCFG write and for any SS PHY init (`tools/rea`), to see whether ABL ever
   brings up the SS lane at all and where its TCPC polarity read lives.

Reason: the expected gain for our traffic is negligible, the largest item (PHY/PMA/PCS tables plus a TCPC orientation source) is L
with hang risk, and Wi-Fi (milestone 2) does not depend on USB speed. Tests 1 and 3 cost no hardware writes.

## Unknown (with what would settle it)

- U1: does ABL leave the USBDP SS lane trained? Read-only dump of PMA 0x110E0000 / PCS 0x110F0000 / USBCON CLKRST and
  COMBO_PMA_CTRL in a probe run (test 2).
- U2: does ABL's fastboot cap DCFG at HS? Decompile of the ABL USB init (`tools/rea`) or test 1 with a verified SS cable.
- U3: exact "up" values for COMBO_PMA_CTRL ref-clk selects and CLKRST (read `phy_exynos_usbdp_g2_v4_ctrl_pma_ready`,
  `phy_exynos_usb_v3p1_g2_pma_ready` in GS phy-exynos-usb3p1.c; not read this session).
- U4: how to read CC polarity from the max77759 TCPC (register map; GS `drivers/usb/typec/tcpm/google/` max77759 driver, directory
  exists, files not opened). Alternatively the TCPC's I2C reg from a live Android dump of the stock phone (outside this brief).
- U5: how many bytes the userdata flash really sent (the `flash stats` / "Sending sparse N/M" lines of `tools/fflash-userdata.sh`
  logs; and fastboot's raw->sparse path in AOSP `fastboot/` sources) to decide whether USB 2 limits the flash.
- U6: GTXFIFOSIZ/GRXFIFOSIZ values at handoff (probe extension, not in the current logs).
- U7: physical cable/port used for the bluejay fastboot observations (not recorded anywhere in the repo).
- U8: the tested value of Linux dwc3's default `maximum_speed` when DT has no property (core.c not opened).
- U9: whether the Pixel 6a is Gen 1 or Gen 2 on the wire (DT says UFP_USB32_GEN1 [src, dts/gs101-common-typec.dtsi:169]; hardware
  capability not otherwise confirmed).

## Drafted steps for an implementer (no code)

Phase 0 (no code, required before anything else):
1. Run test 1 (cable / bus-2 port, fastboot, read speed) and record the cable, port and result in HANDOFF.
2. Reverse ABL's USB init for the DCFG write and any SS PHY or TCPC access (`tools/rea`), cite function addresses.

Phase 1 (read-only, only if phase 0 shows ABL can run SS or it is unclear):
3. Extend `shim/usbprobe.h/.c` with read-only dumps of PMA, PCS, USBCON CLKRST/COMBO_PMA_CTRL and GTXFIFOSIZ/GRXFIFOSIZ; add the
   addresses to the probe host test and to the safety allowlist (`tools/test-bluejay-boot.py`) without loosening it.
4. Run once in a fastboot-booted shim attached SS; compare against the Google sources (`phy-exynos-usbdp-gen2-v4.c`).

Phase 2 (only if the gain justifies it; hardware-writing, Opus review):
5. Write a source-cited table of every PHY write used by `phy_exynos_usbdp_g2_v4_enable` (PMA defaults for 26 MHz, PCS, tune,
   lane mux) into `soc/google/gs101/soc.json` or a doc, each entry with file:line.
6. Document the max77759 TCPC CC-status register and the I2C access path; decide on orientation sourcing.
7. shim: DCFG devspd SS; BOS and SS companion descriptors; 1024-byte bulk; link-state events; keep an HS fallback; host-test the
   descriptor bytes (`shim/usbproto.c` host test).
8. XNU: new patch(es) after 0082 for the same, never edit the fetched tree; `tools/stack-apply`.
9. Review (adversarial, Opus because PHY/PMU writes), then one hardware run via `tools/xrun`, read with `--dry-run --from-dir`.
10. Keep the HS path as default until an SS result is observed on hardware (date + build id), per AGENTS rule 5.
