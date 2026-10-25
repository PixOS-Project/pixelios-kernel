# Licence notice for `xnu/patches/`

The `.patch` files in this directory (and the `.sh` stagers that fetch third-party sources) are **modifications of Apple's XNU
(xnu-8796.141.3, <https://github.com/apple-oss-distributions/xnu>)**. They contain lines of Apple's source as context and add or change
files in that tree. They are therefore distributed under the **Apple Public Source License, Version 2.0** (full text:
[LICENSE-APSL-2.0.txt](LICENSE-APSL-2.0.txt), the same text as `APPLE_LICENSE` in Apple's repository). The same applies to the source files these
patches create when you apply them to XNU.

- The stack is applied to a pristine checkout by the tools in this repository (`tools/stack-apply`, `tools/xnu-local-build`, the CI workflow);
  nothing in this directory contains a copy of Apple's tree.
- The first patches began as files of ios-sweet (<https://github.com/dipa-studio/ios-sweet>, MIT, (c) 2026 dipa-studio; text in [../../LICENSE-ios-sweet.txt](../../LICENSE-ios-sweet.txt)); they are included here.
- Everything outside `xnu/patches/` (the shim, tools, tests, documentation) is under the repository's [MIT licence](../../LICENSE), unless a file says otherwise.
- This is a statement of intent by the maintainers, not legal advice. Not affiliated with Apple or Google.
