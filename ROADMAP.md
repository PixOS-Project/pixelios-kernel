# Roadmap

What works is in [docs/STATUS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md) (every row says whether it was *observed on hardware*, *read from source* or is a *guess*).
This page lists what is done, in progress and next. The sections below are as of 2026-10-29 and are partly stale: as of 2026-11-15 GPU acceleration (about 50 fps at the lowest GPU clock), Wi-Fi with real internet, Bluetooth pairing and touch work in the maintainer's runs (see the status in [README.md](README.md)); open next: drawn presents >= 50/s, audio output, the UDP-download loss under load, `lskdd` restart loop. Order and estimates are guesses, not promises.
Run labels name the maintainer's hardware runs (logs are not in git).

## Milestones

1. **iPhone GUI on the Pixel 6a: reached** (observed 2026-10-22). Lock screen, home screen, Settings, touch, on the CPU renderer.
2. **Wi-Fi: in progress.** Broadcom BCM4389 on PCIe. The firmware boots under XNU (observed, wlan-runE-0133, 2026-10-26) and the
   host-side setup stores of the message protocol run and are undone cleanly (observed, wlan-runG2-0174, 2026-10-29). Scan and join
   are not done. Plan: [docs/research/20-wifi-stack-plan.md](docs/research/20-wifi-stack-plan.md); the XNU driver is a port of
   OpenBSD `bwfm` ([docs/WIFI-LICENSING.md](docs/WIFI-LICENSING.md)).

## Done (observed on hardware)

- XNU boot to the lock screen and home screen with 8 CPUs, touch, Settings -> General -> About (2026-10-22).
- Boot time: lock screen about 108 s after the boot command from a migrated root, about 346 s on the first boot after a fresh
  flash (runs bs-q2 / bs-q1, 2026-10-25).
- Remote reset to the bootloader over the USB link (patch 0100, run bl1-remote3, 2026-10-24): test runs end as soon as the answer is in.
- USB network log, also over USB 3 SuperSpeed (run el2-3-f7, 2026-10-29).
- GPU: power-up by the shim (railon1/railon2, 2026-10-23), hand-over to XNU and a user client (gputest25, 2026-10-25), a Mesa GL
  context in backboardd (gputest18, 2026-10-25), the lock screen drawn through the GPU path (gputest41/gputest42, 2026-10-26), the
  Settings app legible on it (gputest82-settings-about3, 2026-10-27).
- Frame statistics (patch 0141): the GPU path is not faster than the CPU renderer yet; both peak at about 22-28 distinct frames/s
  (gputest48 vs bench53-cpu-fps, 2026-10-26).
- Wi-Fi up to the firmware: PCIe link, BAR assignment, chip id and core walk from the shim (2026-10-24); chip layer, firmware and
  NVRAM download and ARM release under XNU (wd2-1b 2026-10-25, wlan-runE-0133 2026-10-26); host-side setup stores (wlan-runG2-0174, 2026-10-29).
- Shim resident at EL2 under XNU with stage-2 translation on, XNU still reaches the lock screen (el2-2-xnu, el2-3-f7, 2026-10-29).
- Dev overlay (patch 0117): replace files in the flashed root at boot without a re-flash (first on hardware gputest29, 2026-10-25).

## In progress

- **Wi-Fi:** after the host-side setup the firmware has not answered yet (an open gap). Next: a read-only poll after "host ready",
  then message rings, control path and events, the `en0` / Apple80211 front end, scan, WPA2 join and traffic, then reliability
  (three cold boots, reconnect).
- **GPU:** home-screen icons and widget content are black tiles on the GPU path (fine on the CPU renderer); blur / intermediate
  render targets are rejected; a compute job from a Mesa-built blob has not run. Current work: why capture framebuffers are only
  partly filled.
- **EL2 tracing:** trap one device-register page under XNU (design and review first).
- **Bluetooth:** a trace of stock Android, a spec and a conformance matrix exist (2026-10-29); the port (device-tree node and
  transport so `bluetoothd` runs) has not started. Until then the root drops the `bluetoothd` job, which otherwise restarts every ~10 s
  (observed, runs ba5-inj8-boot1/boot2, 2026-10-28; change on a pixelios-userland work branch).
- **Crash handling:** a panic or hang should reset the phone to fastboot with its cause logged (planned, not run).

## Next

- **Power-on boot without a PC** (iOS image in `boot_a`, stock `boot_b` kept as the way back): planned, not done.
- **Boot selector**: a key held at boot returns to fastboot. Not started.
- `mac-address` entries in the device tree (removes 5 s waits in MobileGestalt), a real-time-clock service (removes a 30 s boot wait): not scheduled.

## Not started

- Audio, camera, sensors, modem.
- Other Tensor phones (Pixel 6, 6 Pro share the SoC tree): see [docs/PORTING.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md).

## Where help is most useful

- Hardware captures from other Tensor devices (see [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit them).
- Reverse-engineering notes: Broadcom msgbuf/firmware behaviour on the BCM4389, Mali-G78 job and texture paths, ACPM channels. Cite your sources.
- Host-side tests and tooling around the existing test loop.
