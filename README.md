# PixOS: kernel and shim

**Apple's XNU kernel and iOS 16 userland on a Google Pixel 6a (Tensor G1).**

[![xnu build](https://github.com/PixOS-Project/pixelios-kernel/actions/workflows/xnu.yml/badge.svg)](https://github.com/PixOS-Project/pixelios-kernel/actions/workflows/xnu.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

PixOS is a research port. A small bare-metal **shim** is loaded with a temporary
`fastboot boot` image, brings up what XNU needs on the Pixel's Tensor G1 (UFS storage, memory
map, a device tree for XNU), and jumps into a patched **XNU** kernel. The iOS 16 root filesystem
then lives in the phone's `userdata` partition. It is inspired by, and builds on,
[ios-sweet](https://github.com/dipa-studio/ios-sweet), which did this for the Redmi Note 10 Pro.

> **AI-driven development.** This project is written mostly by AI coding agents under the direction of
> a human maintainer, who runs everything on the hardware and approves every destructive step.
> Treat the code accordingly: read it before you trust it. Details: [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md).

> **Status 2026-11-15 (Pixel 6a).** Now working, observed on hardware in the maintainer's runs: **GPU acceleration, Wi-Fi with
> real internet, Bluetooth pairing, and touch.**
> **GPU:** the UI is smooth at about 50 fps with the GPU at its lowest clock (150 MHz): on a 600 s navigation bench the rendered
> frame rate was 50.6 / 50.6 pooled (slowest step 48.4 / 48.6) on two boots (runs 155b, 8177). The frames that actually reach the
> panel and are drawn count 49.0/s (run 18e1): the 50/s bar is **not met yet**.
> **Wi-Fi:** joins WPA2 networks and gets real internet (address, route, DNS, HTTPS; pixnet 9/9 on 155b and 8177), about 190-285 Mbit/s
> TCP download under the full bench load (188.1 / 285.4 Mbit/s). **Bluetooth:** scans, pairs and connects devices; a headset was
> paired and connected by hand (demo boot, job 1308). **Touch:** a real finger was used on the demo boot (5791 touch interrupts).
> **Not done:** no audio playback (no speaker driver; the headset link carries no A2DP stream), camera, cellular, sensors, sleep/wake.
> **Known issues:** UDP download loses packets under heavy load (27 % and 40 % in the two bench boots); the iOS daemon `lskdd` exits
> and restarts about once a second; this config runs 5 of the 8 CPU cores (CPU A76 at 1663 MHz and X1 at 1826 MHz, static rows;
> GPU 150 MHz; memory 1352 MHz). A boot needs the PixOS shim and the iOS `userdata` root (see the build docs); this is **not a daily-driver
> operating system**. Full list with run labels: [docs/STATUS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md)
> and the 2026-11-15 release note on the site; what comes next: [ROADMAP.md](ROADMAP.md).
> **Disclaimer:** AI was used heavily to make this run on the Pixel 6a; it is based on ios-sweet.
> The older single-run notes below (2026-10 / early 2026-11) are kept with their original run labels; where they say "not yet", the status above is newer.

> **This is one of three repositories.** `pixelios-kernel` (this one): the pre-kernel shim, the XNU patch series, SoC and
> device trees, and the test and boot tools. [pixelios-userland](https://github.com/PixOS-Project/pixelios-userland): the
> recipe that builds the iOS root filesystem. [pixelios-site](https://github.com/PixOS-Project/pixelios-site): the
> documentation site, status, case study and milestones.

## What runs on the phone (observed on hardware)

Each item names the run or record it was seen in. Run labels refer to the maintainer's run logs, which are not in git.

- The shim runs on an unlocked Pixel 6a and logs to the screen, to RAM, and over a USB serial gadget
  (observed 2026-10-21, `device/google/bluejay/docs/GATE0_RESULTS.md`).
- UFS cold re-init after the bootloader shuts the controller down, and a switch of the link to
  **HS-G4 (measured 12x faster reads**, observed, [docs/reviews/hs-g4-review-1.md](docs/reviews/hs-g4-review-1.md)). A read-only, CRC-checked dump of `userdata` over USB
  (observed 2026-10-21, `device/google/bluejay/hardware/udump-1mib-2026-10-21.txt`).
- **XNU boots on the Pixel**, mounts the iOS 16.7 root from `userdata`, runs launchd with ~60 daemons, draws the
  Apple logo and reaches the **iOS lock screen and home screen** (2026-10-21/22, details in [docs/STATUS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md)).
  All 8 CPUs run; **touch works** and Settings -> General -> About opens in about a second (2026-10-22).
  A boot from an already migrated root reaches the lock screen about 108 s after the boot command; the first boot after a
  fresh `userdata` flash takes about 346 s (observed, runs bs-q2 / bs-q1, 2026-10-25, 8 CPUs).
- **USB network log** from the running iOS: the phone shows up as an Ethernet gadget and streams the kernel log over UDP
  (observed on every run since 2026-10-22); since 2026-10-29 also over a USB 3 SuperSpeed link (`SWEET USB3: connected at SuperSpeed 5 Gb/s`, run el2-3-f7).
- **GPU (Mali-G78), partly** (observed): the shim powers the GPU (runs railon1/railon2, 2026-10-23) and hands it to XNU, which reads
  `GPU_ID` 0x92020010 and serves a user client that backboardd opens (gputest25, 2026-10-25); a Mesa-based GL context is created
  in backboardd (gputest18, 2026-10-25); the lock screen is drawn through the GPU path (gputest41 / gputest42, 2026-10-26) and
  Settings -> General -> About is legible on it (gputest82-settings-about3, 2026-10-27). **Not yet:** a compute job from a Mesa-built
  blob, home-screen icons (black tiles on the GPU path, CPU renderer fine), blur surfaces; the GPU path is not faster than the
  CPU renderer yet (gputest48 vs bench53-cpu-fps, 2026-10-26). Part of this work is on the work branch wip/gpu-accel, not on trunk.
- **Wi-Fi (Broadcom BCM4389 on PCIe), partly** (observed): the shim brings the PCIe link up and reads the chip id 0x4389 through
  BAR0 (run w3a-n30, 2026-10-24); under XNU the driver walks the chip (wd2-1b, 2026-10-25), loads firmware and NVRAM, releases
  the chip's ARM core and the firmware boots (shared structure version 7, wlan-runE-0133, 2026-10-26); the driver then makes the
  host-side setup stores Android's driver makes when it opens the device, reads them back and undoes them
  (`G2 RESULT done rc=0 stores=45 ... undo=ok`, wlan-runG2-0174, 2026-10-29). **Not yet:** the firmware has not answered after
  that setup (an open gap), no message rings, no scan, no join, no traffic. Firmware is never in this repository; it comes from
  your own phone ([docs/WIFI-LICENSING.md](docs/WIFI-LICENSING.md)). The newest Wi-Fi patches (0137, 0167, 0174) are on the work branch wip/w4-g2, not on trunk.
- **Shim resident at EL2 under XNU** (observed): XNU boots to the lock screen while the shim stays at EL2 with stage-2
  translation on (`SWEET EL2: vm=1 gaps=0 traps=0 bound=0`, runs el2-2-xnu and el2-3-f7, 2026-10-29; el2-3-f7 ran its full 1500 s).
  This is groundwork for tracing device-register accesses; no register is trapped yet.
- A **software-driven test loop** (build, boot, read the USB log, reset over USB or by timer, back to fastboot) needs no button
  presses (observed since 2026-10-22; reset over the USB link since 2026-10-24, run bl1-remote3; [docs/AUTOMATION.md](docs/AUTOMATION.md)), and the iOS root builds **without sudo**
  (observed 2026-10-22, [docs/ROOTLESS_BUILD.md](https://github.com/PixOS-Project/pixelios-userland/blob/main/docs/ROOTLESS_BUILD.md)).
- A **QR-code log channel**: the screen shows the console log as a loop of QR codes that a
  webcam decodes ([docs/QR_LOG.md](docs/QR_LOG.md), with the gotchas observed on hardware). The USB log replaced it as the
  main debug channel; QR is kept as the fallback (read from source: `AGENTS.md`).
- Everything that runs before the kernel is read-only toward storage, and a build-time audit
  rejects any storage write path (read from source: `tools/test-bluejay-boot.py`, [docs/SAFETY.md](docs/SAFETY.md)).

## Hardware and safety

- A **Google Pixel 6a** (`bluejay`) with an **unlocked bootloader**. Unlocking wipes the phone.
- Use a spare phone. Experimental code runs at the highest privilege level on it. Every step up
  to the final `userdata` flash uses `fastboot boot`, which writes nothing, and the phone can
  always be restored from Google's factory image. **Read [docs/SAFETY.md](docs/SAFETY.md) first.**
- No Apple firmware is distributed; you download the iOS restore image yourself.

## Quick start

```sh
git clone https://github.com/PixOS-Project/pixelios-kernel && cd pixelios-kernel
tools/fetch-android-tools.sh     # mkbootimg / avbtool from AOSP
tools/pix build bluejay --avb --qrlog
# phone in fastboot (Power + Vol Down), unlocked; check the device first:
fastboot getvar product          # must say: bluejay
fastboot boot shim/out/shim-boot-avb.img
```

This boots the shim alone (it shows its log on the screen as QR codes and writes nothing to storage). You need an AArch64 bare-metal GCC,
Python 3, `adb`/`fastboot`; see [docs/BUILD.md](docs/BUILD.md) and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).
The XNU kernel is built by GitHub Actions on a macOS runner ([workflow](.github/workflows/xnu.yml)) or locally without a Mac
(`tools/xnu-local-build`, [docs/LOCAL-BUILD.md](docs/LOCAL-BUILD.md)); the iOS root image by `build-ios-root-rootless.sh` in
[pixelios-userland](https://github.com/PixOS-Project/pixelios-userland). A full hardware cycle (build, boot XNU, read the USB log,
one report) is `tools/xrun` ([docs/AUTOMATION.md](docs/AUTOMATION.md)).

## How it fits together

```
fastboot boot -> ABL -> shim (EL2, read-only, MMU off)
                          |  UFS cold init + HS-G4, memory map, device tree (ADT) for XNU
                          v
                    patched XNU 8796 (iOS 16 generation, "SWEET" machine config)
                          |  UFS block driver -> userdata
                          v
                    iOS 16.7 root (HFS+) in userdata -> launchd -> SpringBoard lock screen
```

- [docs/HACKIOS-GUIDE.md](docs/HACKIOS-GUIDE.md): start a port on any device with an LLM, with a copy-paste prompt
- [docs/PORTING.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md): adding a device tree (other Pixels)
- [docs/MAP.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/MAP.md): where everything lives
- [docs/PLAN.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PLAN.md): phases and gates
- [docs/CASE_STUDY.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/CASE_STUDY.md): how the Pixel 6a maps onto what ios-sweet did, and the risks
- [docs/research/](docs/research/): primary-source notes (UFS/PHY bring-up, ABL behaviour, boot path, Wi-Fi, GPU)
- [device/google/bluejay/hardware/](device/google/bluejay/hardware/): raw captures from a real device
- [docs/reviews/](docs/reviews/): independent reviews of the hardware-facing code

## Repository layout (AOSP-style: shared core, SoC trees, device trees)

| Path | What |
|---|---|
| `shim/`, `xnu/patches/`, `qrlog/`, `tools/` | the shared platform: pre-kernel shim, XNU patch series, QR log, builders and receivers |
| `soc/<vendor>/<soc>/` | what every phone with a chip shares: `soc.json`, SoC shim code (gs101 UFS bring-up), research |
| `device/<vendor>/<codename>/` | one phone: `board.json` (DRAM map, framebuffer, ...), gate results, raw hardware captures |
| `docs/` | safety, build, automation, licensing, research, reviews |

`tools/pix devices | check | build | new-device` is the entry point. **Another Pixel?** Start with
[Adding a device](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md); Pixel 6 and 6 Pro share the 6a's SoC tree.

Documentation site: <https://pixos-project.github.io/pixelios-site/>.

## Roadmap and contributing

What is planned: [ROADMAP.md](ROADMAP.md). Issues and pull requests are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). Hardware captures
from other Tensor devices are especially useful.

## Credits and license

Built on the idea and groundwork of **ios-sweet** by dipa-studio. Full credits, third-party
licenses and the legal notes are in [docs/CREDITS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/CREDITS.md). Not affiliated with Apple
or Google. Our own code is [MIT](LICENSE); files derived from ios-sweet keep its MIT notice ([LICENSES/MIT-ios-sweet.txt](LICENSES/MIT-ios-sweet.txt)); the XNU patches in `xnu/patches/` are modifications of Apple's source and stay under Apple's APSL 2.0 (see [xnu/patches/NOTICE.md](xnu/patches/NOTICE.md)). Every third-party component and its licence is listed in [THIRD_PARTY.md](THIRD_PARTY.md); Wi-Fi specifics: [docs/WIFI-LICENSING.md](docs/WIFI-LICENSING.md).

This project is AI-driven: agents write most of the code and docs, a human directs, runs the hardware
and signs off. Code that touches hardware gets an independent (also AI) review
([docs/reviews/](docs/reviews/)) and is tested on a real device before it is called working.
See [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md).
