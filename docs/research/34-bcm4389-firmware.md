# 34. BCM4389 Wi-Fi firmware, NVRAM, CLM: names, location, licence, extraction plan (goal W-B5)

Date 2026-10-24. Researcher note. Nothing was downloaded except public web pages (text); no device or partition touched; no firmware bytes in this note.
Tags: **[src]** = read from source (file:line or URL, opened by me), **[obs]** = observed (on host files or web page, today), **[guess]**.
`R1` = `~/pixelios-artifacts/refs/wlan-stage1`, `R2` = `~/pixelios-artifacts/refs/wlan-stage2`.
`CIS` = `dhd_custom_cis.c` of branch `android-gs-bluejay-5.10-android13`, fetched as text from
`https://android.googlesource.com/kernel/google-modules/wlan/bcmdhd/bcm4389/+/refs/heads/android-gs-bluejay-5.10-android13/dhd_custom_cis.c?format=TEXT`
(not in our refs; line numbers below are of that decoded file, 2035 lines [obs]). Whether that branch equals the pinned commit
`de86f6ec...` of doc 20 is **unknown** (doc 20:18 pins a commit; I did not diff).

## 1. Files the Google driver asks for

### 1.1 Base names (what `request_firmware` is given) [src]
- Build flags for gs101 / PCIe: `-DDHD_LINUX_STD_FW_API`, `DHD_FW_NAME="fw_bcmdhd.bin"`, `DHD_NVRAM_NAME="bcmdhd.cal"`,
  `DHD_CLM_NAME="bcmdhd_clm.blob"`, `DHD_MAP_NAME="fw_bcmdhd.map"`: `R1/bcm4389-Kbuild:955-959` (inside `ifneq ($(CONFIG_BCMDHD_PCIE),)`, line 954 context).
- With `DHD_LINUX_STD_FW_API` the default fw/nvram names are the bare names: `R1/bcm4389-dhd_linux.c:9791-9794` (`fw = DHD_FW_NAME; nv = DHD_NVRAM_NAME;`).
  Module params `firmware_path` / `nvram_path` / `clm_path` override them: `R1/bcm4389-dhd_linux.c:450-458,529-530,9820-9835`.
- Absolute paths exist in the same Kbuild for the non-std-API branch (used only if `DHD_LINUX_STD_FW_API` were off): `/vendor/firmware/fw_bcmdhd.bin`,
  `/vendor/etc/wifi/bcmdhd.cal`, `/vendor/etc/wifi/bcmdhd_clm.blob`, `/vendor/etc/wifi/fw_bcmdhd.map` (`CONFIG_SOC_GOOGLE` branch): `R1/bcm4389-Kbuild:57-61`.
  `VENDOR_PATH` is `""`: `R1/bcm4389-dhd.h:3264`. These paths are the only hint of directory (see 2).
- CLM blob: `dhd_set_blob_support` probes `DHD_CLM_NAME` with `dhd_os_get_img_fwreq`: `R1/bcm4389-dhd_linux.c:21111-21134`; the `_blob` suffix on the fw name is
  added only `#if defined(CONCATE_BLOB)`, and `CONCATE_BLOB` does not appear in the Kbuild (grep: no match) [obs], so no `fw_bcmdhd.bin_blob`.
  `DHD_BLOB_EXISTENCE_CHECK` is on: `R1/bcm4389-Kbuild:481`.
- Default CLM header fallback `CONFIG_BCMDHD_CLM_PATH "/etc/wifi/bcmdhd_clm.blob"` if not set: `R1/bcm4389-dhd.h:520-522` (overridden by Kbuild:60).
- **txcap**: the driver has `dhd_apply_default_txcap(dhd, txcap_path)` (`R1/bcm4389-dhd.h:3726`), `MAX_TXCAP_BUF_SIZE 16 KiB` (`R1/bcm4389-dhd.h:511`),
  `cached_txcap` (`:1546-1547`) and `dhdpcie_get_fwpath_otp(..., clm_path, txcap_path)` (`R2/dhd_pcie.h:942-943`), but no default txcap file name is defined
  anywhere in R1/R2 (grep `txcap`: only those declarations) [obs]. **Unknown** (needs `dhd_linux.c` of the pinned commit; the refs hold no definition, and `dhd_apply_default_txcap` is not called in R1).
- `logstrs.bin`, `rtecdc.bin`, `roml.bin` (debug string maps, not needed to run): `R1/bcm4389-dhd_linux.c:711-727`.

### 1.2 Suffix logic (which variant is chosen) [src]
Flags for BCM4389 on gs101: `SUPPORT_MULTIPLE_NVRAM`, `SUPPORT_MULTIPLE_CLMBLOB` (`R1/bcm4389-Kbuild:931`); `SUPPORT_MULTIPLE_REVISION`, `SUPPORT_MIXED_MODULES`,
`USE_CID_CHECK`, `SUPPORT_MULTIPLE_CHIPS`, `CONCAT_DEF_REV_FOR_NOMATCH_VID` (`:946-948`); PCI ids `0x4441` (OTP programmed) / `0x4389` (no OTP) (`:944-945`).
`USE_DIRECT_VID_TAG` is **not** in the flag list (grep) [obs], so the `#ifdef USE_DIRECT_VID_TAG` branch (`R2/dhd_pcie.c:3619-3640`: `_4389` + vendor/rev hex, `_b0/_c0/_c1` by chiprev) is dead for this build.

Active path (all read):
1. `dhdpcie_download_firmware` calls `concate_revision(bus, fw_path, nv_path)`: `R2/dhd_pcie.c:3783`; `BCM4389_CHIP_ID` routes to `concate_revision_from_cisinfo`: `R2/dhd_pcie.c:3701-3702`.
2. `concate_revision_from_cisinfo` (USE_CID_CHECK branch): module type from `dhd_check_module_bcm` (reads `.cid.info`, `CIS:1912-1945`) else from OTP/CIS via `dhd_find_naming_info_by_chip_rev` (`CIS:412-466`);
   then appends `info->nvram_ext` to the nvram name and `info->fw_ext` to the firmware name: `R2/dhd_pcie.c:3571-3606`.
3. BCM4389 naming table (`cid_ext`, `nvram_ext`, `fw_ext`): `CIS:280-312`. Examples: `e53_es23 -> _ES10_semco_b0 / _b0`; `e58_es30 -> _ES30_semco_c0 / _c0`; `e53_es40 -> _ES40_semco_c1 / _c1`;
   `1wk_es21 -> _1wk_es21_b0 / _b0`; `1wk_es40 -> _1wk_es40_c0 / _c0`; `1wk_es50 -> _1wk_es50_c1 / _c1`; `usi_es10 -> _ES10 / _c0`. So firmware is `fw_bcmdhd.bin_b0|_c0|_c1` (+ sometimes none), NVRAM is `bcmdhd.cal` + module tag.
   If nothing matches, `CONCAT_DEF_REV_FOR_NOMATCH_VID` appends `_c0` to both fw and nvram: `R2/dhd_pcie.c:3652-3671,3709-3713`.
4. Then, with `SUPPORT_MULTIPLE_NVRAM`, `dhd_get_platform_naming_for_nvram_clmblob_file(NVRAM, ...)` tries `"bcmdhd.cal" + ext_name[i]` for each available ext and takes the first that exists:
   `R1/dhd_custom_google.c:386-418`. The CLM blob goes through the same function with `"bcmdhd_clm.blob" + ext`: `R1/dhd_custom_google.c:402-405`, called at `R1/bcm4389-dhd_linux.c:11163-11174`.
   Ext candidates, from device-tree chosen nodes `/chosen/plat` (hw stage/major/minor -> `DEV|PROTO|EVT|DVT|PVT|MP<maj>.<min>`) and `/chosen/config` property `sku` (table of `G....` ids -> `MMW|ROW|JPN|...`):
   `R1/dhd_custom_google.c:149-170,251-262,430-509`; composed as `_<rev>_<sku>`, `_<rev>`, `_<sku>`, and with chip version `<nvram_ext>_<rev>_<sku>`, `<nvram_ext>_<rev>`, `<nvram_ext>_<sku>`, `<nvram_ext>`, plus the empty extension (`NO_EXT_NAME`, set under `USE_CID_CHECK`): `R1/dhd_custom_google.c:281-330`.
   Example of what that yields (assuming a `_c0` module tag and hw stage MP, sku ROW [guess for the 6a]): `bcmdhd.cal_ES30_semco_c0_MP1.0_ROW`, ..., `bcmdhd.cal` last.
   Priority = bit index order of the loop `for i in 0..7` (`R1/dhd_custom_google.c:409-418`), with enum `CHIP_REV_SKU=0, CHIP_REV=1, CHIP_SKU=2, CHIP=3, REV_SKU=4, REV_ONLY=5, SKU_ONLY=6, NO_EXT_NAME=7`: `R1/dhd_custom_google.c:239-249` [src]. So the most specific chip+rev+sku name wins, the bare `bcmdhd.cal` is the last resort.
5. `.cid.info` location: `PLATFORM_PATH ".cid.info"` (`CIS:516`) with `PLATFORM_PATH` default `/data/misc/conn/` (`R1/bcm4389-dhd.h:3283`) -- but `DHD_EXPORT_CNTL_FILE` is defined together with that default (`:3285`), in which case the string comes from a sysfs-exported `cidinfostr` instead of the file (`CIS:1921-1928`). Whether the 6a build takes the file or sysfs path: **Unknown** (needs the pinned commit's `PLATFORM_PATH` Kbuild value; R1 Kbuild has no `PLATFORM_PATH`: grep [obs]).

**Consequence for us [guess]:** the *base names* are certain; the exact variant file names on a given phone depend on the module (Semco/Murata/USI) of the user's 6a, its OTP, and the hw/sku nodes. We should not hardcode names: ship a name-resolver that tries base+suffix candidates against the extracted directory, or just read the listing from the factory image (section 3), which also shows which module variants Google ships.

Pixel 6a Wi-Fi/BT module vendor: **Unknown** (no source in refs; teardown or `.cid.info` on the phone would tell).

## 2. Which stock partition holds them

- The factory image zip `image-bluejay-cp3a.260905.009.zip` (inside `~/pixelios-factory/bluejay-cp3a.260905.009-factory-a69bd72a.zip`) contains, among others: `vendor.img` (645 MB), `vendor_dlkm.img` (54 MB), `vendor_boot.img`, `system_ext.img`, `system.img`, `product.img`, `super_empty.img`, `modem.img`, `ldfw.img`, ...: from `unzip -l` (names and sizes only) [obs, 2026-10-24]. Build id `cp3a.260905.009`; the phone runs `BP31.250610.004` (AP-06: do not assume they match; blobs are chip firmware, still compare the file hashes against the phone if ever possible).
- Directory: `/vendor/firmware/` for `fw_bcmdhd.bin*` and `/vendor/etc/wifi/` for `bcmdhd.cal*`, `bcmdhd_clm.blob*`, `fw_bcmdhd.map*`: from Kbuild `CONFIG_SOC_GOOGLE` branch `R1/bcm4389-Kbuild:57-61` [src]. This is a build-time constant of the *non*-std-API path; with `DHD_LINUX_STD_FW_API` the kernel firmware loader searches its own path list (`/vendor/firmware` is the usual Android entry) [guess]. The driver `.ko` itself is probably in `vendor_dlkm.img` or `vendor_boot.img` [guess].
- => The files are most likely inside `vendor.img` (ext4/erofs inside the factory zip, not inside `super`; fastboot assembles `super` from these) [guess]. Not verified: I did not open `vendor.img`. A listing of that image settles it (3, step 3).
- Doc 12:105-107 and doc 20:103-104 say the same (user was to run `adb shell ls /vendor/firmware /vendor/etc/wifi`); we do not need the phone: the factory zip is already on the host.
- Doc 32 does not exist in `docs/research/` (listing shows 29, 33, no 30-32 other than 33) [obs]; nothing to cite there.

## 3. Licence situation

- Broadcom firmware in linux-firmware is under `LICENCE.broadcom_bcm43xx` ("Redistributable", `SPDX LicenseRef-LICENCE.broadcom_bcm43xx`): `WHENCE` lines 3041-3042 (brcmsmac) and 3071-3072 (brcmfmac) [obs, fetched `https://gitlab.com/kernel-firmware/linux-firmware/-/raw/main/WHENCE`, 513,960 bytes].
- Terms (text via ScanCode LicenseDB, which copies the linux-firmware file; I could not fetch the raw file from kernel.org/gitlab, both 404 today) [src: `https://scancode-licensedb.aboutcode.org/broadcom-linux-firmware.html`]:
  grant to use and integrate; reproduce and distribute "complete, unmodified, and as provided by Broadcom, solely for use with Broadcom proprietary integrated circuit product(s)"; must ship the Agreement; must keep copyright notices;
  **no modification, reverse engineering, decompile or disassembly**; terminates on breach; export-law clause. Category "Proprietary Free".
- BCM4389 is **not** in linux-firmware: `grep -n "4389\|4387\|4378" WHENCE` returns nothing [obs]. The `brcmfmac` list there stops at 4350/43569/4358/43602/4366/4371/4373 and cypress files [obs, WHENCE:3061-3111]. Mainline `brcmfmac` does not list 4389 either (doc 20:30 [src: doc]).
- The Pixel's `fw_bcmdhd.bin` is a Google/Broadcom "hw2 customer" build (`-DDHD_CAP_CUSTOMER="hw2 "`, `R1/bcm4389-Kbuild:922`) [src]; no public licence statement for the Pixel copy found. The DHD driver itself is GPL (kernel module), the firmware blob is a separate binary [guess; no licence header read].
- Conclusion [guess, not legal advice]: we cannot put the blobs in git or in a public image (rule 4; the Broadcom text above permits redistribution only unmodified, with the licence, for Broadcom ICs, and the Pixel copy's terms are unknown). The legitimate path is: **the user extracts them from their own factory image/phone at build time**, they stay in `~/pixelios-artifacts` outside git, and the iOS root builder consumes them from there. Treat "never decompile the firmware" as binding: our work is host-side protocol code from the public GPL driver/brcmfmac only (doc 20:99); do not run `tools/rea` on the firmware.

## 4. Extraction plan (plan only, nothing run)

Source: the host already has `~/pixelios-factory/bluejay-cp3a.260905.009-factory-a69bd72a.zip` and its `unpacked/.../image-bluejay-cp3a.260905.009.zip` [obs, 2026-10-24]. Tools on the host: `debugfs` only (`/usr/sbin/debugfs`); `lpunpack`, `simg2img`, `fsck.erofs`, `dump.erofs`, `unsquashfs` are absent [obs, `which`]. `tools/fetch-android-tools.sh` exists (contents not read) [obs].

1. Write all work into a new directory outside git: `~/pixelios-artifacts/wlan-fw/` (`stock/`, `files/`), gitignored by being outside the repo; never in the clone.
2. Extract only `vendor.img` (and, to settle the `.ko` location, list `vendor_dlkm.img`) from `image-bluejay-cp3a.260905.009.zip` with `unzip -j` into `stock/` (a copy of a file already on the host; no network).
3. Identify the filesystem (`file`, magic): Android vendor images are ext4 or erofs [guess]. If sparse, convert with `simg2img` (from `tools/fetch-android-tools.sh` or the Android platform-tools); if ext4, list read-only with `debugfs -R "ls -l /firmware" stock/vendor.img` and `debugfs -R "ls -l /etc/wifi"`; if erofs, `fsck.erofs --extract=files/ --path=...` or `dump.erofs`: both need a new host package or a rootless build: ask the user (AGENTS: root only by the user; mounting loops needs root, so use debugfs/erofs tools instead of `mount`).
4. Copy out only: `firmware/fw_bcmdhd.bin*`, `etc/wifi/bcmdhd.cal*`, `etc/wifi/bcmdhd_clm.blob*`, and any `*txcap*` found; record the file list (names + sizes + sha256) in `~/pixelios-artifacts/wlan-fw/MANIFEST.txt` (outside git). Do not copy `.map`, debug or non-4389 firmware.
5. Pick the variant: needs the user's phone module tag (`.cid.info` or driver log) or just carry the whole variant set and let our msgbuf driver choose by the OTP/CIS read from the dongle.
6. Wire into the iOS-root build as an optional, local-only input (`pixelios-userland` build step takes a directory argument; absent = Wi-Fi feature off), never tracked.
7. Update this note's Unknowns once step 3 lists the image.

## 5. Unknowns (with what settles each)

| # | Unknown | Settled by |
|---|---|---|
| U1 | exact variant names on the 6a (`bcmdhd.cal_*`, `bcmdhd_clm.blob_*`, `fw_bcmdhd.bin_*`) | listing of `vendor.img` (plan step 3) |
| U2 | partition really `vendor.img` (not `vendor_dlkm`/`system_ext`) | same listing |
| U3 | txcap file name/path (driver references a txcap blob, no default name in refs) | pinned-commit `dhd_linux.c` (`dhd_apply_default_txcap`) or the listing |
| U5 | whether `.cid.info` file or exported sysfs string is used (`DHD_EXPORT_CNTL_FILE`) | pinned-commit Kbuild `PLATFORM_PATH`, or phone log |
| U6 | 6a's module vendor (Semco/Murata/USI) and the `/chosen/plat`, `/chosen/config sku` values our ADT would have to supply | OTP/CIS read from the dongle; bluejay DT in `R1/dts-bluejay-gs101-bluejay.dtsi` (not read here) |
| U7 | whether `android-gs-bluejay-5.10-android13` `dhd_custom_cis.c` equals commit `de86f6ec` | `git diff` of the two |
| U8 | licence of the Pixel-specific `fw_bcmdhd.bin` (Google copy) | licence headers in the vendor image / Google Pixel binary terms; assume proprietary |
| U9 | factory image `cp3a.260905.009` firmware = phone's `BP31.250610.004` firmware | hash comparison with a phone copy (user pulls read-only) |
| U10 | does the firmware need Android-side services (doc 12:268) | bring-up on hardware |

## 6. Implementer steps (drafted, no code)

1. Create `~/pixelios-artifacts/wlan-fw/` and confirm it is outside every git tree (`git -C ... check-ignore` or path check).
2. Unzip `vendor.img` (+ `vendor_dlkm.img` for the listing) from the factory image zip already at `~/pixelios-factory/...`.
3. Decide the filesystem; obtain a read-only extractor (debugfs for ext4; erofs-utils if erofs: ask the user to install, host root is theirs).
4. List `/firmware` and `/etc/wifi`; write `MANIFEST.txt` with names, sizes, sha256 (no contents).
5. Copy out the four families (fw, cal, clm, txcap); resolve U1-U3 in this note.
6. Add a docs-only mapping "driver base name -> candidate suffixes" (section 1.2) to the stage-2 design (doc 20/29) so the msgbuf driver loads by candidate list.
7. Make the iOS-root builder accept `--wlan-fw DIR` (optional, local); verify with a test that no file from DIR is tracked (`git ls-files` empty for those names; add the patterns to `.gitignore`).
8. Before any hardware step, compare hashes with the phone's copy if the user provides one (U9).
