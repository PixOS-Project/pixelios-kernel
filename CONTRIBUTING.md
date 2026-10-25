# Contributing

Thanks for looking. This is hardware bring-up, so a few rules keep it safe and honest.

- **Safety first.** Read [docs/SAFETY.md](docs/SAFETY.md). Code that runs before the kernel
  (`shim/`) must stay read-only toward storage unless a change is reviewed as a hardware writer.
  `tools/test-bluejay-boot.py` audits the built objects and fails the build when a write path
  appears; do not weaken it to make a change pass.
- **Observations vs. guesses.** Notes and PRs say what was seen on a device and what is inferred.
  Include the build/commit under test.
- **Never commit private data.** No device serials, IMEI, partition dumps, Apple firmware,
  keys or `.env` files. `tools/validate-repo.py` and the `.gitignore` help, they do not replace care.
- **Tests.** Host tests live next to the code (`shim/*_host_test.c`, `tools/test_*.py`) and run in
  `tools/check-all.sh`. New logic gets one.
- **New devices** go in their own tree (`tools/pix new-device`, see [docs/PORTING.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md)); do not
  add per-phone conditionals to the shared shim.
- **Hardware you do not own.** Please do not open "works on my device" PRs for other phones
  without logs; say which board and bootloader you used.

**AI assistance is part of how this project works.** You may use it too: you are responsible for what you
submit, say which parts were generated and what you tested on hardware, and never feed secrets, serials or
firmware into prompts or commits. See [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md).

## Getting started

1. Read [README.md](README.md), [docs/STATUS.md](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md) and [ROADMAP.md](ROADMAP.md) to see what exists and what is wanted.
2. Set up: the build steps in [docs/BUILD.md](docs/BUILD.md). Run `tools/check-all.sh` before you push; it must print `GATE PASS`.
3. Kernel changes are numbered patches in `xnu/patches/` (contiguous, applied in order on xnu-8796). Never edit the xnu tree; add or regenerate a patch.
   `tools/stack-apply` checks that the stack applies on pristine xnu-8796.
4. Prefer changing a table (`soc/*/soc.json`, `device/*/*/board.json`) over copying constants into code, and cite the source of every hardware constant
   (Linux driver at a commit, bootloader decompile, or a measured register dump). Do not invent values.

## Pull requests

- One concern per PR, small commits, a short description of what changed and why.
- Say what you tested: host tests, CI, or a real device (board, bootloader version, build id). Attach the log, with serials and IMEI removed.
- Tag claims in docs: *observed on hardware*, *read from source* or *guess*. "Works" needs a device result.
- Anything that writes to a partition other than `userdata` needs a prior discussion in an issue; the shim stays read-only toward storage.

## Reporting a hardware result or a bug

Use the issue templates. Include: phone model and codename, bootloader version, the exact command, the log (USB log or QR capture), and what you expected.
Security or bricking risks: see [SECURITY.md](SECURITY.md). Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Small, focused PRs with a clear description are easiest to review.
