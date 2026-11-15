# xnu/patches-archive

Patches removed from the active stack in `xnu/patches/` because they only drive Qualcomm SM7150 ("sweet",
Redmi Note 10 Pro) hardware that does not exist on the Pixel 6a ("bluejay", Tensor gs101). Not applied by CI
(`.github/workflows/xnu.yml` copies only `xnu/patches/*.patch`; `tools/validate-repo.py` only reads `xnu/patches/`).
Evidence and the full per-patch classification: `docs/research/14-patch-audit.md`.

## What is here

| File | What it added | Why it is dead on bluejay |
|---|---|---|
| `0060-sweet-keys-touch-drivers.patch` | `bsd/dev/sweet_keys.c` (PM6150 power/volume keys over the Qualcomm SPMI arbiter), `bsd/dev/sweet_touch.c` (GENI I2C touch), and the `sweet_keys_init()` / `sweet_touch_init()` calls in `sweet_ufs.c` | Qualcomm SPMI/GENI register blocks; the bluejay boot image already passed `sweetkeys=0 sweettouch=0` (`tools/build-xnu-image.sh`) |
| `0062-sweet-touch-power-battery.patch` | RPMh L11C vote for the touch rail (`sweet_rpmh.c`), touch edits, and the SPMI battery (`sweet_spmi.c`, `SweetBattery.cpp`) | The RPMh and touch parts are Qualcomm-only. The battery part is still used (static source on bluejay), so it was kept as the new `0061-sweet-battery-spmi.patch`; `SweetBattery` only touches SPMI with `sweetbattery=1` |
| `0063-sweet-goodix-geni-packing.patch` | GENI FIFO packing fix and the Goodix GT9896 protocol in `sweet_touch.c` | Touch driver for sweet only |

The sweet touch input path (`sweet_hid_touch()` / `sweet_hid_key()`, `SweetHIDTouch`) lives in the kept
`0058` HID patch and stays; only the Qualcomm drivers that fed it were archived.

Kept on purpose although partly Qualcomm: `0072` vbus override (old 0074; `0082` gs101 USB edits its lines),
the USB probe/ep0/ECM/rx-mbuf/live-log/link-state patches (the gs101 DWC3 gadget is built on them), SMP/PSCI.

## Regenerated (context only) when these were removed

Old 0072, 0083, 0085 and 0088 (new 0070, 0081, 0083, 0086) had hunks whose context named `sweet_keys.c`,
`sweet_touch.c`, `sweet_rpmh.c` or the `sweet_keys_init()` / `sweet_touch_init()` lines. The context lines were
removed (the code they add is unchanged; old 0072's call to `sweet_usb_probe()` now follows `sweet_sample_start()`).
The old make-qr-patch.py generated the QR patch (deprecated 2026-10-29, removed 2026-11-13; use `tools/make-qr2-patch.py`) (default output is now `0081-sweet-qr-log.patch`).

## How to restore

The change is one commit. Revert it: `git revert <archive-commit>` (the commit message starts with
"xnu: archive Qualcomm-only touch/keys patches and renumber"). That restores the old file names, the old numbering and
the four context-adjusted patches. Restoring by hand is not enough: moving a file back leaves the numbering and
the context of 0070/0081/0083/0086 inconsistent.

## Renumbering map (old -> new)

Patches 0001-0059 and the staging scripts (`0019`, `0020`, `0026`) keep their numbers. Historical text
(`docs/HANDOFF.md`, `NOTES.md`, dated reviews under `docs/research|reviews|tasks`, `UPSTREAM_README.md`,
CI artifact names like `xnu-0085`) keeps the OLD numbers; translate with this table.

| old | new | name |
|---|---|---|
| 0061 | 0060 | `sweet-iphone-x-display-size` |
| (new) | 0061 | `sweet-battery-spmi` (split out of old 0062, see below) |
| 0064 | 0062 | `sweet-priv-work-interval` |
| 0065 | 0063 | `sweet-smp-psci` |
| 0066 | 0064 | `sweet-crash-fault-region` |
| 0067 | 0065 | `sweet-console-mute` |
| 0068 | 0066 | `sweet-surface-lookup-cached` |
| 0069 | 0067 | `sweet-ufs-faster` |
| 0070 | 0068 | `sweet-pid-hibernate` |
| 0071 | 0069 | `sweet-touch-edge` |
| 0072 | 0070 | `sweet-usb-probe` |
| 0073 | 0071 | `sweet-usb-device-ep0` |
| 0074 | 0072 | `sweet-usb-vbus-override` |
| 0075 | 0073 | `sweet-usb-ecm` |
| 0076 | 0074 | `sweet-usb-rx-mbuf` |
| 0077 | 0075 | `sweet-usb-live-log` |
| 0078 | 0076 | `sweet-smp-8-cores` |
| 0079 | 0077 | `sweet-display-power` |
| 0080 | 0078 | `sweet-net-trace` |
| 0081 | 0079 | `sweet-usb-link-state` |
| 0082 | 0080 | `sweet-ufs-gs101` |
| 0083 | 0081 | `sweet-qr-log` |
| 0084 | 0082 | `sweet-usb-gs101` |
| 0085 | 0083 | `sweet-restart` |
| 0086 | 0084 | `sweet-iokit-quiet-accelerator` |
| 0087 | 0085 | `sweet-sandbox-container-path-appex` |
| 0088 | 0086 | `sweet-restart-poll` |
