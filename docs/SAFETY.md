# Safety: everything must be reversible

Hard rule from the user: the Pixel 6a must always be restorable to stock Android with
Google's factory image. Every agent and every patch follows this file. If a step can't meet
it, stop and ask.

## Why a Pixel 6a can be restored

The bootloader (`bootloader_a/b`) lives in its own partitions. As long as those, and the
per-device partitions below, are never written, **Power + Volume Down always reaches fastboot**,
and `flash-all.sh` from the factory image restores the phone. Then `fastboot flashing lock`
returns it to a locked, stock state.

## Never

- Write `bootloader_*`, `radio_*`/`modem*`, `persist`, `efs*`, `devinfo`, `misc`, `vbmeta*`,
  `boot_*`, `vendor_boot_*`, `dtbo_*`, `init_boot_*`, or the GPT. No agent issues any
  `fastboot flash` except `userdata` (pre-approved by the user for the iOS root, AGENTS.md rule 3).
- Touch UFS LUNs other than the one holding `userdata`. In particular, never write boot LUNs.
- Write fuses/OTP or PMIC trim/OTP, change charger or battery-protection limits, or load/replace secure-world code.
  (Updated 2026-10-22 on the user's instruction: **ACPM and the PMIC are NOT off limits any more.** The volatile
  PMU SMC (`0x82000504`), the ACPM mailbox and PMIC register access (rail enables, voltages inside the range the
  Linux regulator driver / device tree allows for that rail, clocks, GPIOs) are allowed because they are volatile
  (lost on a power cycle, the phone is restored by Power+VolDown) and the dev loop needs them: touch rails,
  Wi-Fi power, display. Rules: read first, write only registers/values cited from Linux or ABL sources, keep every
  write behind the audited allowlist, bound every wait.)
- Flash any partition other than `userdata` without the maintainer's explicit go-ahead for that partition. Such a go-ahead
  (maintainer rule, 2026-10-28) covers only a partition that cannot brick the phone, lose unique data or trip anti-rollback,
  after a recovery path was exercised on this phone and written down in the run plan. `bootloader_*`/`abl`, `modem`, `persist`,
  `efs`, the GPT, relocking and an older bootloader are never covered unless the maintainer names them.
- `fastboot flashing lock` while anything non-stock is flashed. Relocking with a custom image
  can leave the phone unbootable with flashing disabled. Relock only right after `flash-all.sh`.
- Flash a factory image older than the one on the phone. The 6a has bootloader anti-rollback;
  always use the current or newer build.
- Run any `adb`/`fastboot` command unless `fastboot getvar product` says `bluejay`.

## How the code enforces it

- **Phase 0 shim**: read-only. `shim/ufs.c` contains no SCSI WRITE/UNMAP/FORMAT and no UFS
  write-type query/attribute/flag requests. A host check fails the build if a write opcode appears.
- **XNU UFS driver (Phase 2+)**: writes are refused unless the LBA range is inside the
  GPT partition named `userdata`, on the LUN where it was found. Upstream patch 0030 already
  does this (`ufs_write_blocks` bounds check); keep it and add the LUN check for gs101.
- **Temporary boot only**: `fastboot boot` writes nothing to the phone. Phases 0–1 never write
  storage at all.
- **Hardware register writes** in the shim (Wi-Fi PCIe bring-up, GPU power) and in the XNU Wi-Fi driver are table-driven,
  checked against their own allowlist, and probes undo what they changed in an always-run teardown; the hand-over modes
  that leave state on for XNU on purpose (`--wlan-hold`, `--gpu-handover`) are documented in `shim/build.sh`. Each such writer
  gets an independent review before it runs (read from source: `shim/wlanlink.c`, `tools/test-bluejay-boot.py`, `docs/reviews/`).

## Restore procedure

```sh
# Power + Volume Down -> fastboot
fastboot getvar product            # must say bluejay
./flash-all.sh                     # from the matching or newer factory image (wipes userdata)
fastboot flashing lock             # optional, only after flash-all
```

Unlocking itself wipes userdata (Android's rule), so back up personal data first.
