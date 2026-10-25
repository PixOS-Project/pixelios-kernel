# THIRD_PARTY: components not written by this project, and their licences

Tags (AGENTS.md rule 5): **[observed]** = a command or hash compare run on 2026-10-25, **[src]** = read from a file (cited), **[guess]** = not verified.
This is not legal advice. Source of the analysis: `docs/PLAN-STANDALONE.md` (rows are numbered as in its licence matrix).

| # | Component | Origin and pin | Licence (evidence) | How it reaches a build |
|---|---|---|---|---|
| 2 | ios-sweet derived files: shim/font.h, gen_font.py, host_test.c, loader.h, shim.ld; tools/adt.py, check-exports.py, remotezip.py, sparsediff.py, sweet-log-pull.sh, sweet-log.py, unpack_bootv2.py, xnu-cc.py; xnu/patches/.keep, 0001-0059 (incl. stagers 0019/0020/0026 .sh); modified: shim/build.sh, console.[ch], fdt.[ch], loader.c, mmu.[ch], shim.c, start.S, ufs.[ch]; tools/mkadt.py, mkpayload.py, qemu-sweet.py, sweet-crash.py, sweet-usbnet.sh, validate-repo.py | https://github.com/dipa-studio/ios-sweet, files taken at `11da58b` (2026-10-19, no LICENSE there); `067c9b3` (2026-10-22, "Add MIT License to the project") adds the LICENSE and is the licensing commit | MIT, "Copyright (c) 2026 dipa-studio" [observed: `git show 067c9b3:LICENSE` blob 526359b5 equals our `LICENSE-ios-sweet.txt` blob, 2026-10-25; the local upstream checkout contains both commits]. Files identical at both commits except `tools/mkadt.py` (licensed version is a superset) [src: PLAN-STANDALONE section 0 item 1, originally observed by `git diff --stat 11da58b 067c9b3`; not re-run here]. xnu/patches/0001-0059: byte-identical to `11da58b` [observed, hash compare, 62 files]. A code contribution from one outside author (ios-sweet PR #1) predates the LICENSE and is not clearly covered [guess]; the plan removes it (task T2) | tracked in this repo; text in `LICENSE-ios-sweet.txt` |
| 5 | XNU | https://github.com/apple-oss-distributions/xnu tag xnu-8796.141.3 | APSL-2.0 [src: `xnu/patches/NOTICE.md`, full text `xnu/patches/LICENSE-APSL-2.0.txt`] | fetched by CI / `tools/xnu-local-build`; only patches are shipped |
| 6 | Apple IOHIDFamily copies inside `xnu/patches/0058-sweet-hid-event-services.patch` (32 files, `iokit/Kernel/arm/hid/IOHID*.{h,cpp}`) | Apple open-source tree, copied by ios-sweet | "Apple Public Source License Version 2.0" in each file header [observed: 27 occurrences of the string in 0058, 2026-10-25] | tracked patch (APSL, as the rest of `xnu/patches/`) |
| 7 | Stagers: libpthread, hfs | https://github.com/apple-oss-distributions/{libpthread,hfs}, pinned in `xnu/patches/0019*.sh` and `0026*.sh` [src] | both repositories carry `APPLE_LICENSE` [PLAN-STANDALONE row 7: `gh api .../contents`; not re-run here] | fetched at build time, not shipped |
| 8 | Stager: BearSSL (portable AES, MD5, SHA-1, SHA-384/512 copied into `libkern/crypto/sweet_bearssl`) | https://www.bearssl.org/git/BearSSL @ `7bea48e5e850ab4cafbe68d3765cdaba13a86d6f` [src: `xnu/patches/0020*.sh:6`] | MIT, "Copyright (c) 2016 Thomas Pornin <pornin@bolet.org>"; full text below. Read from `LICENSE.txt` in a previously staged copy `~/pixelios-artifacts/bt-0082/tree/xnu/libkern/crypto/sweet_bearssl/LICENSE.txt` (copied by `0020:43`) [observed 2026-10-25]; that the staged copy is exactly the pinned commit is [guess] (the stager checks the hash, the artefact itself was not re-verified). The `src/inner.h` header repeats the same MIT text [observed] | fetched at build time; its code ends up in the kernel binary, which therefore needs this notice |
| 9 | Project Nayuki qrcodegen: `qrlog/qrcodegen.[ch]` and the copy inside `xnu/patches/0081-sweet-qr-log.patch` | Project Nayuki | MIT: "Copyright (c) Project Nayuki. (MIT License)" with the full text in the file [observed in 0081 lines 315, 319, 1362] | tracked; 0081 is an MIT island inside the APSL directory |
| 10 | `bsd/dev/sweet_wlan_chip.h` inside `xnu/patches/0120-sweet-wlan-bwfm-core.patch` (branch `wip/w3a-s34-skip` only) | OpenBSD `sys/dev/ic/bwfm.c` v1.118 [PLAN-STANDALONE row 10] | ISC, "Copyright (c) 2010-2016 Broadcom Corporation, Copyright (c) 2016,2017 Patrick Wildt <patrick@blueri.se>" [observed: patch lines 221-224 on that branch, 2026-10-25] | tracked on the Wi-Fi branch only, not yet public |
| 11 | `soc/google/gs101/wlan_seq.json` + generator `tools/gen_wlan_seq.py` | values PARSED mechanically from Google Pixel kernel sources, branch `android-gs-bluejay-6.1-android16`, fetched 2026-10-23 [src: `docs/research/29-wlan-stage1-design.md:10`; exact commit not recorded: guess]; files `dwc-whi/pcie-exynos-gs101-rc-cal.c`, `dwc-whi/pcie-exynos-rc.c`, `pcie-exynos-rc.h`, `pcie-exynos-common.h`, `gs101-pcie.dtsi`, `gs101-pinctrl.dtsi`, `gs101-common-wlan.dtsi`, `linux-pinctrl-exynos-arm64.c` [src: `tools/gen_wlan_seq.py:3-9`] | **facts extracted from GPL-2.0 sources, see LAWYER note.** SPDX headers read locally: rc-cal.c, rc.c, gs101-pcie.dtsi, gs101-pinctrl.dtsi, gs101-common-wlan.dtsi = `GPL-2.0-only` (Samsung / Google copyright); `linux-pinctrl-exynos-arm64.c` = `GPL-2.0+` [observed, `~/pixelios-artifacts/refs/wlan-stage1/`, 2026-10-25] | generated, tracked |
| 12 | `soc/google/gs101/shim/ufsphy_tables.h` + generator `tools/gen-ufsphy-tables.py` | Linux mainline `drivers/phy/samsung/phy-gs101-ufs.c` at commit `72a16bcff25e9f4a1d6d42a243301b1b76db2f32`, via Appendix A of `soc/google/gs101/docs/07-gs101-ufs-bringup.md` [src: `ufsphy_tables.h:1`, doc 07 lines 3-4] | **facts extracted from GPL-2.0 sources, see LAWYER note.** The source file is not on this machine (`/tmp/ufsspec` is gone), so its SPDX header was not read: GPL-2.0 is a [guess] (mainline drivers/phy/samsung is normally GPL-2.0-only) | generated, tracked; compiled into every shim binary |
| 19 | AOSP `mkbootimg.py`, `avbtool.py`, `generate_gki_certificate.py` | android.googlesource.com, branch main, **not pinned** [src: `tools/fetch-android-tools.sh:7-10`] | Apache-2.0 [guess; not read] | fetched into gitignored `tools/third_party/`, not shipped |
| 20 | CI kernel build: blacktop/darwin-xnu-build | GitHub | MIT per `docs/CREDITS.md:23` of the site repo [src, not re-checked] | fetched in CI |

## BearSSL licence text (row 8, verbatim, observed)

```
Copyright (c) 2016 Thomas Pornin <pornin@bolet.org>

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
(Trailing whitespace of the original lines is dropped here.)

## LAWYER note (rows 11 and 12; decision D3 = option A, user, 2026-10-25)

- **Where the values come from [src].** `wlan_seq.json` holds register offsets, bit values, delays and pin settings that `tools/gen_wlan_seq.py` parses
  out of the Google/Samsung GPL-2.0 sources listed in row 11. `ufsphy_tables.h` holds PHY register/value rows that `tools/gen-ufsphy-tables.py`
  produces from the table in Linux `phy-gs101-ufs.c` (row 12).
- **The extraction is mechanical [src].** The generators read the sources; the values are not typed by hand. The output contains numbers and
  register names plus line cites, not source text or code structure [read from the generated files; not independently measured here, but
  `docs/reviews/license-similarity.md` reports no run of more than 4 lines against any GPL tree].
- **No claim is made either way.** This project does not claim that such facts are free of the source licence, and does not claim they are derived
  works. The table is kept in the tree as it is (option A); the alternative (generate it at build time from fetched sources) is documented in
  `docs/PLAN-STANDALONE.md` task T7 (B).
- **No lawyer has reviewed this.** Everything above is a maintainer's description, not a legal opinion.
