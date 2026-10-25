# PixeliOS: kernel and shim

**Apple's XNU kernel and iOS 16 userland on a Google Pixel 6a (Tensor G1).**

[![xnu build](https://github.com/PixOS-Project/pixelios-kernel/actions/workflows/xnu.yml/badge.svg)](https://github.com/PixOS-Project/pixelios-kernel/actions/workflows/xnu.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

PixeliOS is a research port. A small bare-metal **shim** is loaded with a temporary
`fastboot boot` image, brings up what XNU needs on the Pixel's Tensor G1 (UFS storage, memory
map, a device tree for XNU), and jumps into a patched **XNU** kernel. The iOS 16 root filesystem
then lives in the phone's `userdata` partition. It is inspired by, and builds on,
[ios-sweet](https://github.com/dipa-studio/ios-sweet), which did this for the Redmi Note 10 Pro.

> **AI-driven development.** This project is written mostly by AI coding agents under the direction of
> a human maintainer, who runs everything on the hardware and approves every destructive step.
> Treat the code accordingly: read it before you trust it. Details: [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md).

> **This is not a usable operating system yet.** XNU boots on the Pixel with 8 cores, launchd runs, the iOS lock
> screen and home screen appear and **touch works** (*observed on hardware*, 2026-10-22); the UI is slow, and Wi-Fi, GPU,
> audio, camera and the modem are still to do. See [docs/STATUS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md) for what works and [ROADMAP.md](ROADMAP.md) for what is planned.

> **This is one of three repositories.** `pixelios-kernel` (this one): the pre-kernel shim, the XNU patch series, SoC and
> device trees, and the test and boot tools. [pixelios-userland](https://github.com/PixOS-Project/pixelios-userland): the
> recipe that builds the iOS root filesystem. [pixelios-site](https://github.com/PixOS-Project/pixelios-site): the
> documentation site, status, case study and milestones.

## What works today

- The shim runs on an unlocked Pixel 6a and logs to the screen, to RAM, and over a USB serial gadget.
- UFS cold re-init after the bootloader shuts the controller down, and a switch of the link to
  **HS-G4 (12x faster reads)**. A read-only, CRC-checked dump of `userdata` over USB.
- **XNU boots on the Pixel**, mounts the iOS 16.7 root from `userdata`, runs launchd with ~60 daemons, draws the
  Apple logo and reaches the **iOS lock screen and home screen** (2026-10-21/22, details in [docs/STATUS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md)).
  All 8 CPUs run; **touch works** and Settings -> General -> About opens in about a second.
- **USB network log** from the running iOS: the phone shows up as an Ethernet gadget and streams the kernel log over UDP.
- A **software-driven test loop** (build, boot, read the log by camera, self-reset, back to fastboot) needs no button
  presses ([docs/AUTOMATION.md](docs/AUTOMATION.md)), and the iOS root builds **without sudo**
  ([docs/ROOTLESS_BUILD.md](https://github.com/PixOS-Project/pixelios-userland/blob/main/docs/ROOTLESS_BUILD.md)).
- A **QR-code log channel**: the screen shows the console log as a loop of QR codes that a
  webcam decodes ([docs/QR_LOG.md](docs/QR_LOG.md)). It replaced reading the screen by eye.
- Everything that runs before the kernel is read-only toward storage, and a build-time audit
  rejects any write path ([docs/SAFETY.md](docs/SAFETY.md)).

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

You need an AArch64 bare-metal GCC, Python 3, `adb`/`fastboot`; see [docs/BUILD.md](docs/BUILD.md).
The XNU kernel is built by GitHub Actions on a macOS runner ([workflow](.github/workflows/xnu.yml));
the iOS root image by `tools/build-ios-root.sh` ([docs/BUILD.md](docs/BUILD.md)).

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

- [docs/PORTING.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md): adding a device tree (other Pixels)
- [docs/MAP.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/MAP.md): where everything lives
- [docs/PLAN.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PLAN.md): phases and gates
- [docs/CASE_STUDY.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/CASE_STUDY.md): how the Pixel 6a maps onto what ios-sweet did, and the risks
- [docs/research/](docs/research/): primary-source notes (UFS/PHY bring-up, ABL behaviour, boot path)
- [device/google/bluejay/hardware/](device/google/bluejay/hardware/): raw captures from a real device
- [docs/reviews/](docs/reviews/): independent reviews of the hardware-facing code

## Repository layout (AOSP-style: shared core, SoC trees, device trees)

| Path | What |
|---|---|
| `shim/`, `xnu/patches/`, `qrlog/`, `tools/` | the shared platform: pre-kernel shim, XNU patch series, QR log, builders and receivers |
| `soc/<vendor>/<soc>/` | what every phone with a chip shares: `soc.json`, SoC shim code (gs101 UFS bring-up), research |
| `device/<vendor>/<codename>/` | one phone: `board.json` (DRAM map, framebuffer, ...), gate results, raw hardware captures |
| `docs/` | plan, safety, status, research, reviews |

`tools/pix devices | check | build | new-device` is the entry point. **Another Pixel?** Start with
[Adding a device](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md); Pixel 6 and 6 Pro share the 6a's SoC tree.

Documentation site: <https://pixos-project.github.io/pixelios-site/>.

## Roadmap and contributing

What is planned: [ROADMAP.md](ROADMAP.md). Issues and pull requests are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). Hardware captures
from other Tensor devices are especially useful.

## Credits and license

Built on the idea and groundwork of **ios-sweet** by dipa-studio. Full credits, third-party
licenses and the legal notes are in [docs/CREDITS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/CREDITS.md). Not affiliated with Apple
or Google. Our own code is [MIT](LICENSE); files derived from ios-sweet keep its MIT notice ([LICENSE-ios-sweet.txt](LICENSE-ios-sweet.txt)); the XNU patches in `xnu/patches/` are modifications of Apple's source and stay under Apple's APSL 2.0 (see [xnu/patches/NOTICE.md](xnu/patches/NOTICE.md)). Every third-party component and its licence is listed in [THIRD_PARTY.md](THIRD_PARTY.md); Wi-Fi specifics: [docs/WIFI-LICENSING.md](docs/WIFI-LICENSING.md).

This project is AI-driven: agents write most of the code and docs, a human directs, runs the hardware
and signs off. Code that touches hardware gets an independent (also AI) review
([docs/reviews/](docs/reviews/)) and is tested on a real device before it is called working.
See [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md).
