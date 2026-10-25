# AGENTS.md

Port of Apple XNU + iOS userland to phones that were never meant to run it. First target: Google Pixel 6a
("bluejay", Tensor G1 / gs101); upstream reference port: Redmi Note 10 Pro ("sweet", SM7150).
Read this file, then `README.md` and `docs/PLAN.md`, before touching anything.
Scoped rules live next to the code: `shim/`, `xnu/`, `soc/`, `device/`, `docs/` each have an `AGENTS.md`.
Review criteria are in `REVIEW.md`.

## Naming

The project is called **PixOS**. Use PixOS in prose. The `pixelios-*` repo, directory and remote names are legacy and stay as they are (do not rename them).

## Hard rules (a violation is a bug in the change, not a style issue)

1. **Reversible only.** Never write bootloader, modem, persist, efs, vbmeta, boot*, dtbo or the GPT. Never relock a
   bootloader unless the stock factory image is restored. Never flash an older bootloader than the phone has
   (anti-rollback). `userdata` is the only partition the iOS root may replace, and only after the user confirms.
2. **The human runs the phone.** Run `adb`/`fastboot` only when the attached device reports the expected
   `ro.product.device` / `product` (bluejay for the Pixel 6a). Any other phone attached is never touched. Anything needing root on the host is staged as a command for the user.
3. **Confirm each destructive step** (erase, flash, write to UFS) with the user right before running it. Earlier
   approval for a phase does not carry over.
4. **No secrets or firmware in git.** Serial numbers, IMEI, partition dumps, backups, keys, camera credentials, Apple
   binaries and IPSW contents stay out (see `.gitignore`: `logs/`, `shim/out/`, `rea-out/`, `tools/third_party`, `.env`).
5. **Tag every claim** in docs and handoffs as *observed on hardware*, *read from source*, or *guess*. Never write
   "works" without a device result (date + build id).
6. **Delegates are cheap volume, the driver verifies.** Only the driver agent delegates. Delegate prompts start with
   "Work alone. Do not spawn subagents ..." and carry numbered requirements and a verification command. Review
   hardware-touching code with a different model family than the author, never the same one.
7. **Never assume; check.** Every fact a change relies on (register, offset, unit, layout, flag, API, behaviour) is
   checked against a cited source, a decompile or a measured result before it is implemented. If it is still not certain,
   do not implement it: reverse/measure it first, and keep any gate for it closed until then.
8. **Code that writes hardware and has not passed an independent review stays on a local `wip/*` branch**, unpushed.

## Commands

| Task | Command |
|---|---|
| Quality gate (must print `GATE PASS <hash>`) | `tools/check-all.sh` |
| Build a probe/dump shim | `shim/build.sh --board bluejay --avb --pstore-fast [--usb-gadget] [--udump N_MIB]` |
| Build an XNU boot image | `tools/mkadt.py` -> `tools/mkpayload.py` -> `shim/build.sh --boot-xnu PAYLOAD` (see `docs/`) |
| Kernel CI (macOS runner, ~8 min) | `gh workflow run xnu.yml` then `gh run download <id> -n xnu-kernel` |
| Device helper | `tools/pix devices|info|check|build|new-device` |
| Reverse-engineering container | `tools/rea/rea.sh` (offline) / `tools/rea/rea-phone.sh` (phone access) |

`tools/check-all.sh` rebuilds default images and overwrites `shim/out/*.img`: rebuild with the flags you need right
before `fastboot boot`.

## Workflow

- Small, reviewable commits; one concern each. Commit completed work; push only after the gate passes.
- Record milestones in the PR description or an issue: commits, CI run ids, exact results, unfinished work,
  observations vs guesses.
- Kernel changes are numbered patches in `xnu/patches/` (contiguous, applied in order on xnu-8796). Never edit the
  fetched upstream tree; regenerate the patch.
- Run one heavy job at a time (kernel builds, Ghidra, Rust builds); the dev machine runs out of memory.
- Prefer changing the generator/table (`soc/*/soc.json`, `device/*/*/board.json`) over copying constants into code.

## Do not

- Add a UFS WRITE path to the shim. The iOS root goes in with `fastboot flash userdata`.
- Loosen a safety audit (`tools/test-bluejay-boot.py`) to make a change pass; fix the change.
- Invent hardware constants. Cite the source (Linux driver at a commit, ABL decompile, measured register dump).
- Publish, push to a public remote, or change repo visibility without the user saying so.
