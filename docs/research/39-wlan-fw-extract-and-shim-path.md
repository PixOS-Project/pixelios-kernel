# 39 - BCM4389 firmware/NVRAM/CLM: host extraction (done), how the shim gets the bytes (U14), U9 and U11 closed (goal W-B7)

Status: 2026-10-24. Host-side only; no phone, no adb/fastboot, no root, no code/table/tool changed. Tags: **[obs]** = observed on the host this session, **[src: file:line]** = opened and read, **[guess]**.
No blob byte was printed, read or analysed; the files were only copied and hashed (research 34 s.3: no RE). Nothing here is in git.

Source names: `D` = `~/pixelios-artifacts/refs/wlan-stage2/` (Google DHD, repo `android.googlesource.com/kernel/google-modules/wlan/bcmdhd/bcm4389`, branch `android-gs-bluejay-5.10-android13`, head **commit 865527eecf362ea50a1191057bf295a648f807fe** [obs: `?format=JSON` today]); `dhd_pcie.c` in refs is byte-identical (`cmp`) to that commit's file [obs]. Three files were added to `D` today from the same commit: `bcmutils.c`, `include_dngl_rtlv.h`, `include_bcmrand.h`. `W` = `refs/wlan-stage1/`.

## 1. Extraction (requirement 1) [obs]

- Source: `~/pixelios-factory/unpacked/bluejay-cp3a.260905.009/image-bluejay-cp3a.260905.009.zip` (the user's own factory zip, already on the host; no download). `unzip -j ... vendor.img` -> `~/pixelios-artifacts/wlan-fw/stock/vendor.img`, 645,443,584 bytes, sha256 `708d773c33701957...` (full hash in MANIFEST).
- **Correction to research 34 s.4 step 3:** `file` says ext4 (extents, huge_files), not sparse and **not erofs**. No erofs tool and no root are needed; host `debugfs` (`/usr/sbin/debugfs`, 1.47.2) lists and dumps read-only (`debugfs -R "dump /firmware/NAME files/NAME" vendor.img`).
- **Correction to research 34 s.2/Kbuild:** the files are all in `/firmware/` of `vendor.img` (the image's top level is the `/vendor` root), not in `/etc/wifi/` (that holds only `coex_table.xml`, `p2p_supplicant_overlay.conf`, `wpa_supplicant*.conf`) [obs: `ls -l /firmware`, `/etc/wifi`]. U2 settled: `vendor.img`. U1 settled (below). No `*txcap*` file exists in `/firmware`, `/firmware/brcm` (only `BCM.hcd`, `BTFW_*.hcd` = Bluetooth, not copied) or `/etc` [obs] -> U3 still open for the name only, nothing to extract.
- Not copied: `fw_bcmdhd.map` (1,855,772 B, debug), `google_wifi_firmware_config_info.pb` (699 B; probably the variant selector; copied then removed: contents not needed and I did not read it), all non-Wi-Fi firmware.
- Files in `~/pixelios-artifacts/wlan-fw/files/` (names, sizes, sha256); `MANIFEST.txt` next to it holds the same plus the vendor.img hash:

| File | Bytes | sha256 |
|---|---|---|
| fw_bcmdhd.bin | 1,764,018 | 3a6dbf5a726304f83e154ff1b265a06c77d669506eb5c784fd864f10bdda6533 |
| bcmdhd.cal | 27,062 | 49437aa63d15cff7393a92b884ef886480cd18b2710259b00201f6f68beae424 |
| bcmdhd.cal_EU, _JPN, _ROW | 27,062 each | same as bcmdhd.cal (identical bytes) |
| bcmdhd.cal_MMW | 27,061 | f9feec8de9a9ddcb0ed06fd381e9235eb1ad8fbf505ff93afa2e02d9f762535e |
| bcmdhd.cal_ES19 | 24,059 | f51f654ec1e66e26248c5916f5e0cf7415130731a2b0dfcefd7b3f3d1e3bd957 |
| bcmdhd_clm.blob | 15,932 | eea06b8f963dd6ff771a5edd25114b5fe1f857fefa342cd8f200c4cbc65b3e2e |
| bcmdhd_clm.blob_EU, _JPN, _ROW | 15,932 each | same as bcmdhd_clm.blob |
| bcmdhd_clm.blob_MMW | 15,596 | 6216a53ca7117ff1a043933645fad566b5b88c60812198a4b113fac25e577f4d |

- U1: there is exactly **one** firmware (`fw_bcmdhd.bin`, no suffix variants) and 5 cal + 4 distinct-name clm variants of which only 3 cal / 2 clm differ in content. Default pick (what Kbuild loads without a region suffix): `bcmdhd.cal`, `bcmdhd_clm.blob`, `fw_bcmdhd.bin` [src: `W/bcm4389-Kbuild:58-60`, `:957-958`]. Minimum set for the shim: 1,764,018 + 27,062 + 15,932 = **1,807,012 B (1.72 MiB)** [obs, arithmetic]. The mapping region -> suffix is not needed for bring-up (U1 remainder: only for a real region choice later).
- The firmware file is dated like the image (build `cp3a.260905.009`); the phone runs `BP31.250610.004` (AP-06). U9(34) (same firmware as the phone) stays open: a hash of the phone's `/vendor/firmware/fw_bcmdhd.bin`, pulled read-only by the user, settles it.
- `git -C <clone> status` for `~/.t3/projects/pixelios` (super), `pixelios-kernel`, `pixelios-userland`, `pixelios-site`: no blob path listed (checked below, s.6); `~/pixelios-artifacts` is outside every clone [obs].

## 2. Why this is a design question (U14)

- The shim has no UFS read path and must not get a write path (AGENTS "Do not"); blobs cannot be in git (rule 4, research 34 s.3). The bytes must therefore ride in with the boot image, built locally from `~/pixelios-artifacts/wlan-fw/files/`.
- Budget [src/obs]: AVB footer partition size `${AVB_PARTITION_SIZE:-67108864}` = 64 MiB for the boot images (`shim/build.sh:398`, `:402`); current probe images: `out/Image` 348,160 B, `out/shim-boot.img` 356,352 B, ramdisk 98 B [obs: `ls -l shim/out`]. A 15.8 MiB raw XNU payload already goes through this ABL as the boot ramdisk and boots [obs: `docs/PLAN-BOOT-SPEED.md:66,72`]. 1.72 MiB is 2.7 % of the 64 MiB partition and about 11 % of a payload that is known to work. Size is not a constraint.
- Ramdisk path today: `shim/build.sh:373-376` uses `$payload` as `out/ramdisk.bin` or a one-file cpio; the loader finds a magic inside `[linux,initrd-start, linux,initrd-end)` from `/chosen` with `shim_find_magic` (`shim/loader.c:75-82`, `shim/payload.c:3-15`). The USB pool code already treats that range as occupied (`shim/shim.c:420-434`, `avoid[2]`).

## 3. Options for U14

**A (recommended): firmware container as the boot ramdisk.** A new host script packs the three files into one container (magic `WLANFWB1`, version, count, per entry: tag, length, offset 8-aligned, CRC32; 8-byte aligned start like `loader.c:81`). `shim/build.sh --wlan-fw DIR` uses it as `ramdisk.bin` (same switch as `$payload`, `:373-374`). The shim scans the initrd range for the magic (as `loader.c:79`), checks lengths/CRC, and later copies straight from where ABL left it (no relocation, MMU off, 8-byte loads like `loader.c:50-56`) into BAR2 TCM. Pros: no relink, same proven channel as the 15.8 MiB XNU payload, shim image unchanged and the check-all default images contain no blob; the produced `shim/out/*.img` is gitignored (AGENTS rule 4: `shim/out/`). Cons: the ramdisk and the cpio fallback are mutually exclusive (probe shim needs no cpio content; ramdisk is not read otherwise [guess: nothing reads it, `ramdisk.bin` content is 98 B of cpio today]); XNU boot would later need the container concatenated with `SWEETXNU` (the magic scan tolerates this).
**B: link into the shim.** Generated `out/wlanfw.S` with `.incbin` (or `objcopy -I binary`), blob in `.rodata`; `Image` grows 0.35 -> ~2.2 MiB. Pros: nothing to scan, `avoid[0]` (shim image) protects it from the USB pool automatically (`shim/shim.c:431`). Cons: the ABL load address/size behaviour for a kernel `Image` that large is unmeasured [guess: fine]; every firmware change relinks and re-audits the object (`shim/build.sh:365-366` relocation check; wlanlink object audits, AP-16); the lz4 image path (`shim/build.sh:389-390`) doubles the problem; the blob ends up inside an ELF that CI-style tooling may archive.
**C: push from the host over USB at run time.** No existing host->shim data path (the gadget is a device->host log channel, `tools/usbnet-log.py`) [guess; not read here]; needs a new protocol and write buffers. Rejected: most new code, most new failure modes, no benefit over A.

**Recommendation: A.** RAM placement: do not copy anywhere; read in place from the ABL initrd range. Settled by one measurement before any chip write (can ride in the next read-only S3x run): build with a synthetic 2 MiB container (random bytes we generate, not Google's), log `INITRD lo/hi`, the magic address, the container CRC32 as the shim computes it and the host value; PASS = CRC equal, range does not overlap the shim image, FDT, pstore zone or framebuffer. Then repeat once with the real container to prove ABL does not truncate at 1.72 MiB (CRC again equal; expect-string, the hash goes in the run report, not the blob).

## 4. U9 closed: `process_nvram_vars` (commit 865527ee, `bcmutils.c`)

Declared `include_bcmutils.h:1179`; body `D/bcmutils.c:5101-5146` (header comment `:5095-5100`) [src]:
- Input: text of `name=value\n` lines ending in a NUL (or already NUL-separated). Per byte over `len`: `'\r'` dropped; a `'#'` starts a comment skipped to the next `'\n'`; `'\n'` becomes a NUL **only if the line has >= 1 kept byte** (empty lines vanish); everything else copied. Not trimmed otherwise (spaces kept).
- Result length `buf_len = dp - varbuf`; if not a multiple of 4 and `buf_len + pad <= len`, pad up to 4 (`:5135-5140`); the rest of the original span is zero-filled (`while (dp < varbuf + n) *dp++ = 0`), so the buffer ends in at least two NULs when the file had slack. It returns `buf_len`.
- Caller `dhdpcie_download_nvram` (`D/dhd_pcie.c:4221-4252`): `bufp[len-1] = 0` first (forces the last input byte to NUL), calls `process_nvram_vars` only if the file exists (`:4224-4233`), then rounds `len` up to 4 again, appends **one more NUL** (`*bufp++ = 0`) and downloads `len + 1` bytes (`:4246-4253`). `write_vars` (research 37 s.4 vars bullet; `D/dhd_pcie.c:10886-10978`) places them so they end 4 B below RAM end and writes the length token `(~words << 16) | words` at `phys_size - 4`.
- Implementable host-side with a self-written sample text (research 37 U9 plan): a pure function, test cases = CR, comment, empty line, last-byte-no-newline, pad boundary. Max input `MAX_NVRAMBUF_SIZE = 32 KiB` [src: `W/bcm4389-dhd.h:509`]; the default cal is 27,062 B [obs], under the cap, with 5,706 B slack.

## 5. U11 closed: rTLV constants (commit 865527ee)

- `typedef uint32 dngl_rtlv_type_t; typedef uint32 dngl_rtlv_len_t;` `D/include_dngl_rtlv.h:38-39` [src]. Types: `RNG_SIGNATURE 0xFEEDC0DE` (`:67`), `FW_SIGNATURE 0xFEEDFE51` (`:69`), `NVRAM_SIGNATURE 0xFEEDFE52` (`:71`), `FWSIGN_MEM_MAP 0xFEEDFE53` (`:73`), `FWSIGN_STATUS 0xFEEDFE54` (`:75`), `HOST_PAGE_LOCATION 0xFEED10C5` (`:77`), `END_MARKER 0xFEED0E2D` (`:81`).
- `BCM_ENTROPY_HOST_NBYTES = 128u` (`D/include_bcmrand.h:50`), `BCM_ENTROPY_SEED_NBYTES = 64u` (`:48`), `BCM_ENTROPY_MAX_NBYTES = 512u` (128 only with `DBG_RNG_SEC_TEST`, `:53-55`); layout comment `:36-43`.
- Layout written by `dhdpcie_download_rtlv` (`D/dhd_pcie.c:10472-10520`, already in research 37): `dest = ramtop - 4 - 4 - roundup4(len)`; value at `dest`, **length word at `dest+size`**, **type word at `dest+size+4`**, `ramtop := dest`. For `RNG_SIGNATURE` the length word is just `size` (128 -> `0x00000080`, "firmware crashes if checksum is present", `:10496-10503`); all other types use `(~size << 16) | size`. `dhdpcie_wrt_rnd` sends 128 random bytes (`:17224-17236`); `ramtop_addr` starts as `varaddr` set by `write_vars` (`:10891`). So the final top of RAM reads, low to high: ... entropy(128) | 0x00000080 | 0xFEEDC0DE | vars (padded) | token. DHD calls it right after `write_vars` for the CA7 path (`:10176-10190`).
- Whether the shim must use real randomness: DHD uses `dhd_get_random_bytes` [src: `:17232`]; the firmware uses it to randomise heap layout (comment `:17219-17221`). A fixed pattern is probably accepted [guess]; not needed to settle for liveness. (Entropy source on the shim: unknown, see s.7.)
- `END_MARKER` is only written in the FW_SIGNATURE path (`:10595`), which the 4389 build does not define (research 37 s.4). Not needed.

## 6. Verification [obs]

`git status --short` and `git ls-files | grep -i -E "bcmdhd|fw_bcm|\.blob|\.cal"` in super, pixelios-kernel, pixelios-site, pixelios-userland, pixelios (old) and upstream: no blob path tracked or untracked (only this note is new) [obs]. Blobs live only under `~/pixelios-artifacts/wlan-fw/`, outside all clones.

## 7. Unknowns

| # | Unknown | Settled by |
|---|---|---|
| UA | Does ABL deliver the whole 1.72 MiB boot ramdisk at `linux,initrd-start` unchanged (and with vendor_boot ramdisk before it, research 03:276 [guess])? | s.3 measurement (CRC of synthetic then real container) |
| UB | Which cal/clm variant the Pixel 6a uses at runtime, and whether `bcmdhd.cal` equals it | phone's SKU/log, or `google_wifi_firmware_config_info.pb` (not read); bring-up works with the default |
| UC | Entropy source for the 128-byte RNG rTLV on the shim (counter timer, boot ID, pstore) and whether a constant pattern is accepted | firmware behaviour on hardware (S3x); `dhd_get_random_bytes` body not read |
| UD | CLM is not downloaded into TCM: DHD applies it after the firmware runs, via `dhd_apply_default_clm` (`D/dhd_linux.c` calls at `:10994`, `:11904`) [src, call sites only]; the iovar format was not read | later stage; needs a running firmware (research 37 U-list) |
| UE | U3 txcap name, U9(34) firmware = phone's | phone hash; no file in the image |
| UF | Variant mapping region -> suffix | not needed for bring-up |

## 8. Implementer steps (drafted, no code)

1. Extraction is done (s.1). Re-running it: `unzip -j IMAGE_ZIP vendor.img` into an empty dir, `debugfs -R "dump /firmware/NAME out/NAME" vendor.img` for the 3 default names (or all variants), `sha256sum` into `MANIFEST.txt`. No root, no erofs.
2. Add `tools/pack-wlanfw.py` (host-only, `python3 -I`): inputs fw, nvram, clm paths; output the container of option A (magic, version, count, tag/len/off/crc32, 8-aligned). Host test with synthetic random inputs (never the real files).
3. Add a host-side `process_nvram_vars` port (C or Python) with the s.4 semantics and tests (CR, comment, blank, last line without newline, pad-to-4 only if it fits, 32 KiB cap); the shim copy is `static` pure C, no libc.
4. `shim/build.sh --wlan-fw DIR`: refuse if DIR is under any git work tree (`git -C DIR rev-parse` succeeds -> error), pack into `out/ramdisk.bin`, never build this flavor from `check-all` default images; add `WLAN_FW` to the board_config only for that flavor.
5. Shim: `wlanfw.c`: scan `[initrd-start, initrd-end)` for `WLANFWB1` (reuse `shim_find_magic`), validate lengths and CRC32, log `lo/hi/addr/crc` (one line per entry), expose pointers; host test with a synthetic container including truncated/overlapping entries; allowlist unchanged (no chip access in this step).
6. Hardware step (read-only for the chip; ride on the next S3x run after W-C2): run with a synthetic 2 MiB container then the real one; PASS = CRCs equal host MANIFEST-derived values, ranges do not overlap shim/FDT/pstore/framebuffer, `restore=ok`.
7. Later (S3x download steps, own review): TCM placement per research 37 s.4: image at `dongle_ram_base` (U4 0x200000), vars at `ram_end - 4 - roundup4(varsz)` + length token at `ram_end - 4`, rTLV entropy below the vars (s.5), CLM after boot via the iovar (UD).
8. Update `docs/research/34` U1/U2/U3/U9 pointers and the coverage B5 note to cite this note; record that `vendor.img` is ext4 and the files live in `/firmware`.
