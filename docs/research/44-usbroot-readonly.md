# 44 - USB root disk, read-only (patches 0157, 0150, tools/usbroot-host.py)

Goal (user, 2026-10-26): iterate on the iOS root without flashing userdata. Design input: docs/research/52 (super repo, behaviour only,
Item 4) plus our own sources; no fork code was read. Tags: [src] read from source, [obs] observed on hardware, [guess].

## What our tree has [src]
- ECM gadget: `bsd/dev/sweet_usb.c` (patches 0070-0075, 0079, 0082, 0091): polled DWC3 device, VID 1209 PID 0001, phone MAC 02:00:5e:77:ee:02, host MAC 02:00:5e:77:ee:01 (0073:126, string 4). One RX TRB (`UD_RX`, 0x800), one TX TRB fed from a 32-slot mbuf queue under `ud.lock`; RX is `ud_rx_done` in the 1 ms polling thread `ud_thread`. Shim twin: `shim/usbgadget.c`.
- Root finder: `IOFindBSDRoot` (iokit/bsddev/IOKitBSDInit.cpp) has the `rd=ufsroot0` branch from 0028; `sweet_ufs.c` registers bdevsw/cdevsw/devfs. Root mounts read-only unless `rootrw=1` (0031, vfs_subr.c).
- `sweet_usb_probe()` only starts the device with `sweetusb=2` (sweet_usb.c `mode < 2` return). Today it is called from `sweet_ufs_init` only.

## Design (own)
- Raw Ethernet frames, EtherType 0x88B6 (IEEE local experimental 2 [guess: from memory, our private value]), fixed MACs above, little-endian, v1, one request outstanding. 36-byte header: magic PXUQ/PXUA, version, op (1 INFO, 2 READ), status, seq, total, lba (512 B sectors), frag_off, frag_len, CRC-32 (zlib, `bsd/libkern/crc32.c:93`) over header[0..32) + payload. Full layout in `tools/usbroot-host.py`.
- Reads are 4 KiB aligned windows of up to 16 KiB per round trip (cached; a 512 B block read fetches its 4 KiB window), answered in 1 KiB fragments (frames <= 1514 B). Status codes 0 ok, 1 version, 2 op, 3 length, 4 range, 5 io; refusals are one reply with no payload. Phone: fresh seq per attempt, 4 tries, 2 s wait (timeouts are [guess], unmeasured), 30 s wait for the link.
- Phone: `bsd/dev/sweet_usbroot.c` (0157): usbroot0/rusbroot0, mode 0440, FWRITE open, write, strategy writes -> EROFS, DKIOCISWRITABLE 0, max write 0. RX hook in `ud_rx_done` before mbuf creation, TX via the existing queue (`sweet_usb_raw_tx`). 64 GiB / 1 MiB named bounds.
- Boot-args: `rd=usbroot0 sweetusb=2` (no new arg). Refuses: rootrw=1 (read-only disk), sweetusb != 2, non-gs101 (checked from the ADT `/sweet-usb` compatible via `sweet_usb_board_is_gs101()` BEFORE `sweet_usb_probe()`, so a non-gs101 board never reaches the Qualcomm/QSCRATCH writes; review F2), UFS root started (`sweet_ufs_active`); `sweet_ufs_init` refuses when `sweet_usbroot_active` (both directions). Root hook = 0150 (copy of the 0028 shape).

## Verified by
- Host: `python3 tools/test_usbroot_host.py` (mock transport, loss/corruption/stale/duplicate/reorder, refusals, read-only, constants parsed out of 0157). `tools/stack-apply` applies 0157/0150 (135/135). `python3 tools/test_usbroot_c.py` compiles the real C extracted from the 0157 patch against stub kernel headers (ASan/UBSan) and drives CRC, fragment bounds, seq/stale, total limit, write refusals, rootrw/board-before-probe order; each of 9 mutants of the patch (CRC, bounds, seq, strategy EROFS, rootrw, total limit, probe-before-board, open FWRITE) makes it FAIL [observed 2026-10-26]. Still no Apple-toolchain build and no hardware run.

## Risks / unverified
- C is not built with the Apple toolchain and never ran on hardware [guess]. Needs CI + a driver run.
- **Writable /private/var is an OPEN PROBLEM; this mode is a mount + read-only boot probe, not a bootable iOS root [read from source, review F4].** The only way our stack makes /private/var writable is `rootrw=1` (0031: `mnt_flag &= ~MNT_RDONLY` on the root, "/private/var lives on the root"). The dev overlay (0117) does NOT cover it: it refuses on a read-only root (0117 patch lines 581-582, "root is not mounted read-write") and `rd=usbroot0` refuses `rootrw=1` (a read-only disk). An earlier version of this note claimed the overlay "may cover it"; that was wrong. Unknown, so no design is implemented and no boot-arg gate for a writable layer exists (it stays closed): (1) whether any in-kernel filesystem in our config (mockfs 0021, sweet_hfs 0026, bindfs 0037; no tmpfs/union fs found in the applied tree) can be mounted writable over /private/var from the kernel before launchd; (2) how a RAM-backed volume would be formatted and seeded empty; (3) which launchd/configd/notifyd paths need persistence across boot; (4) whether sandbox/container paths (0085) tolerate a different mount. Needs reversing/measuring first (rule 7).
- usbroot mode does not start keys/touch/GPU/wlan/log (they hang off `sweet_ufs_init`); whether they are UFS-independent is not checked.
- The UFS node is always in the bluejay ADT, so "no UFS node" is not used; exclusion is a runtime flag. Throughput: ~1 ms event-poll per fragment => ~1 MB/s [guess].
- Host needs CAP_NET_RAW (user grants it; the tool never escalates). `sweet_usb_probe` is not idempotent: only the root path calls it in this mode.
