---
id: TP-035
tool: "tools/fboot-android + tools/golden-pull"
status: done
proposed_by: "claude-opus-5-5 (implementer, golden-capture)"
date: 2026-10-27
exposed_by: "docs/research/85-golden-trace-procedure.md s.8 step 1: no tool boots a stock/Magisk image (tools/fboot.sh refuses non-shim images) and the adb copy steps would be hand-typed"
extends: ""            # reuses the tools/fboot.sh + tools/fflash-userdata.sh checks (product/unlocked, phone-lock, RESULT style)
related: "TP-020"
usage: "tools/phone-lock run --owner golden -- tools/fboot-android [--erase-userdata --yes-erase-userdata] [--cmdline STR] [--slot-ok X] [--dry-run];  tools/golden-pull [--out DIR] [--dry-run] identity|boot-capture|start|kprobes|mark ROW [done]|stop-pull BLOCK"
refs: "trunk 358816f (merge of tools/golden-capture 3517408); docs/research/85-golden-trace-procedure.md"
---

## checked
`tools/tool-proposals check` was not run by the implementer; by hand: fboot.sh only accepts shim images (imgmeta), fflash-userdata.sh only flashes. [read from source]

## why
Research 85 s.6 steps 2-4, 7, 9, 10 would otherwise be typed by hand (AGENTS.md "Tools"). Both tools exist to make the unsafe variants impossible.

## output
fboot-android: refuses unless the phone lock is held, sha256 of a private copy of the image equals the pin (no override) (Magisk 30.7 image 98cbf66b...bf0), `product=bluejay`, `unlocked=yes`, `current-slot=b`
(or `--slot-ok X` naming the observed slot). Records current-slot and version-bootloader only (no `getvar all`, no serial). `--erase-userdata` needs the literal
`--yes-erase-userdata` and runs `fastboot erase userdata` only (never `-w`, AP-116), then `fastboot boot --cmdline STR IMG` (default: the research-85 trace cmdline).
`--dry-run` prints the commands, makes no fastboot call. golden-pull: adb copy of steps 7/9/10 and the s.4.4 kprobes into `~/pixelios-artifacts/golden/<stamp>/` with
SHA256SUMS; refuses unless `ro.product.device` = bluejay and the out dir is outside every git checkout; props/cmdline are filtered for serial/imei/mac/ssid. Both end in `RESULT: PASS|FAIL|ERROR`.
Test: `bash tools/test_golden_capture.sh` (fake fastboot/adb on PATH).

## risks
Hardware-writing path (`erase userdata`, `fastboot boot`): Opus r1 UNSAFE -> fixed (c7f1a72), Opus r2 SAFE WITH CONDITIONS -> N1-N7 fixed by the driver (af2de06), see docs/reviews/golden-capture-r2.md (super). Before the session: one read-only `fastboot devices` with the Pixel in fastboot to confirm column 2 is `fastboot` (r2 C1; the tool fails safe otherwise). After the session (research 85 step 14) the driver re-checks product/current-slot by hand with `tools/fboot-android --dry-run` identity output compared to ~/pixelios-artifacts/golden/getvar-before.txt; no tool does this yet. Unverified on hardware [guess]: that `su -c sh` with the script on
stdin works with Magisk 30.7, that ABL honours `--cmdline`, that kprobes on the pci_bus_* symbols register (research 85 s.4.4 unknown). golden-pull needs the user to have granted `su`. During the session the user must decline Magisk's "Direct install"/"Install" (it writes boot_x) [guess on the app flow]. Raw dmesg/kallsyms/trace files may hold serial/MAC: never copy them into git. Opus review r1 (docs/reviews/golden-capture-r1.md) findings 1-10 are addressed in the follow-up commit (no --sha256 override, one fastboot device with -s and timeouts, exec-out fix, image copy, cmdline whitelist); re-reviewed in r2.

## amendments
- 2026-10-29 (R10 golden BP31, delegate 20l): `tools/fboot-android --build cp3a|bp31` (default cp3a = unchanged; bp31 uses PIN_BP31, a placeholder that FAILs loudly, also with --dry-run, until the driver pins the Magisk-patched BP31 boot image; --dry-run prints `build=... PIN=...`). `tools/golden-pull`: boot-read also reads vendor_boot_b (cat only, twice, cmp, VNDRBOOT magic); magisk-patch accepts `GOLDEN_MAGISK_IN=PATH` whose sha256 must be in a pinned list (BP31 stock boot from tools/ota-extract, TP-049), output boot-<sha8>-magisk.img. Tests: tools/test_golden_capture.sh.
