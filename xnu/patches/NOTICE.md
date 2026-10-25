# Licence notice for `xnu/patches/`

The `.patch` files in this directory (and the `.sh` stagers that fetch third-party sources) are **modifications of Apple's XNU
(xnu-8796.141.3, <https://github.com/apple-oss-distributions/xnu>)**. They contain lines of Apple's source as context and add or change
files in that tree. They are therefore distributed under the **Apple Public Source License, Version 2.0** (full text:
[LICENSE-APSL-2.0.txt](LICENSE-APSL-2.0.txt), the same text as `APPLE_LICENSE` in Apple's repository). The same applies to the source files these
patches create when you apply them to XNU.

- The stack is applied to a pristine checkout by the tools in this repository (`tools/stack-apply`, `tools/xnu-local-build`, the CI workflow).
  Exception [read from source]: `0058-sweet-hid-event-services.patch` adds 32 IOHIDFamily files copied from Apple's open-source tree; each carries Apple's "Apple Public Source License Version 2.0" header, so they stay under the APSL (Apple Public Source License) like the rest of this directory.
- Patches 0001-0059 are files of ios-sweet (<https://github.com/dipa-studio/ios-sweet>, MIT, (c) 2026 dipa-studio; text in [../../LICENSE-ios-sweet.txt](../../LICENSE-ios-sweet.txt)); they are byte-identical to ios-sweet `11da58b` [observed 2026-10-25, hash compare of all 62 files 0001-0059 + stagers]. Under APSL as XNU modifications, and also under ios-sweet's MIT notice.
- Exceptions to the APSL, each keeping its own licence [read from source]: `0081-sweet-qr-log.patch` embeds Project Nayuki's qrcodegen, MIT (its header says "Copyright (c) Project Nayuki. (MIT License)"); `0120-sweet-wlan-bwfm-core.patch` (branch `wip/w3a-s34-skip` only, not on this branch) embeds `bsd/dev/sweet_wlan_chip.h` from OpenBSD `bwfm.c`, ISC, "Copyright (c) 2010-2016 Broadcom Corporation, 2016,2017 Patrick Wildt". The fetched BearSSL (stager 0020) is MIT, see `THIRD_PARTY.md`.
- Everything outside `xnu/patches/` (the shim, tools, tests, documentation) is under the repository's [MIT licence](../../LICENSE), unless a file says otherwise.
- This is a statement of intent by the maintainers, not legal advice. Not affiliated with Apple or Google.
