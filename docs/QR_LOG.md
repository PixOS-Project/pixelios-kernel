# QR log channel

The Pixel has no serial console, so the log is shown on the panel as QR codes that a camera reads.
It works in the shim and inside XNU. Two formats exist; the host reader (`tools/qr-log.py`) decodes both.

Status 2026-10-29: QR is the **fallback** channel. The main debug channel is the USB network log (`tools/xrun --usbnet`, see
`docs/AUTOMATION.md`) (read from source: `AGENTS.md`).

## QL2 (XNU default, `sweetqr_fmt=2`)

The kernel reads its console ring from the first byte, in the background, and turns it into an append-only stream:

1. **Reduce**: lines that only differ in digits from one of the last 32 kept lines are dropped and counted
   (`[~N similar lines dropped]`); panics, `CRASH`, `SWEET*` lines are only dropped when exactly repeated; lines are never cut.
2. **Compress** in independent units of 8 KiB (LZ77 + a Huffman table and a 12 KiB preset dictionary trained on our logs,
   about 6-7x on our logs, measured offline; `qrlog/qrz.c`).
3. **Block and repair**: the compressed stream is cut into blocks (one block per QR code) grouped in generations of 64. Besides the plain
   blocks the phone sends *repair frames* (XOR of a pseudo-random half of a generation), so any ~1.1x as many frames as blocks rebuild a
   generation, in any order, however many were missed. No wait for one specific frame.
4. **Show** up to 2 QR codes per screen (alphanumeric mode, base45, error correction L, a fixed version), new data first, the rest
   rotating. While the code is up the panel is held: iOS frames and the screen-off fade are not presented (`sweet_qr_hold`).

Frame layout, scheduling and limits: `qrlog/qrstream.h`. Measurements against the old format: `docs/research/24-qr-throughput.md`.

```sh
python3 tools/qr-log.py --esp                 # ESP32-CAM stream; or --camera 0, --video clip.mp4, --images 'shots/*.png'
python3 tools/qr-log.py --esp --follow --out DIR --tiles 2 --workers 3 [--average 3]
python3 tools/qr-log.py --selftest            # QL1 + QL2 round trips (loss, duplicates, reordering, corruption, growing log)
python3 tools/qrsim.py modules ...            # camera simulator: decode rate vs QR version / pixels per module
```

The reader prints one status line (frames, blocks received / missing, recovered bytes, gaps) and keeps `qr2-<sid>.txt` (the log so far, a
`[...gap...]` line where data is still missing) plus `qr2-<sid>.json` (coverage) up to date.

Boot-args (all optional except `sweetqr`): `sweetqr=<seconds>` (takeover delay; 0/absent = off), `sweetqr_fmt=1|2`, `sweetqr_ver=10` (QR
version: the main knob, see below), `sweetqr_ecc=0` (0 L .. 3 H), `sweetqr_tiles=2`, `sweetqr_ms=800` (ms per screen), `sweetqr_unit=8192`,
`sweetqr_k=64`, `sweetqr_red=0` (repair frames sent inline per generation, % of K), `sweetqr_fec=1`, `sweetqr_flush=3000` (ms idle before the
open block is padded and sent), `sweetqr_hold=1`.

**Choosing the QR version**: the camera must see at least ~3.5 pixels per QR module (about 2.7 with a good camera). The phone draws modules
of `floor(984 / (4*ver + 25))` panel pixels; multiply by (camera pixels per panel pixel). Version 10 = 15 px modules: fine from 720p when the
phone fills the frame height; larger versions need a higher resolution or a phone lying on its side. Avoid version 21 (OpenCV cannot decode it;
the encoder uses 22 instead).

## QL1 (`sweetqr_fmt=1`, shim `--qrlog`)

The log tail (last ~12 kB, deduplicated) is cut into text chunks (default 200 bytes), each one QR code:

```
QL1 <snapshot id: 8 hex> <index>/<count> <chunk crc32: 8 hex>
<chunk text>
```

Codes cycle about once per 0.8 s; a snapshot is complete only when every code of one loop was read, so a log that keeps growing never
completes. Non-ASCII bytes are shown as `?`, control characters as `.`. Boot-args `sweetqr_chunk`, `sweetqr_ms`, `sweetqr_tail`.

## Enabling it

- **Shim:** `shim/build.sh --board bluejay --avb --qrlog` (replays before the pager; not with `--pstore-fast`; QL1).
- **XNU:** boot-arg `sweetqr=<seconds to wait>` plus the options above.

Tips: camera close, phone filling the frame, no glare, manual focus; `CAM_HOST` may be set in a git-ignored `.env`.

## Gotchas found on hardware

- The kernel's compiler vectorised the encoder into SHA3 instructions (`eor3`), which gs101 does not implement: the XNU copies are built
  with `#pragma clang optimize off`.
- `PE_state.video.v_baseAddr` is a physical address; draw through the console's `vinfo` instead.
- (read from the AppleCLCD source, not yet confirmed on hardware) the vsync timer re-presents a changed iOS frame at 10 Hz and fades the panel on screen-off, which is why the QR vanished a few seconds after takeover; QL2 holds both off.
