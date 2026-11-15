# AGENTS.md

Port of Apple XNU + iOS userland to phones that were never meant to run it. First target: Google Pixel 6a
("bluejay", Tensor G1 / gs101); upstream reference port: Redmi Note 10 Pro ("sweet", SM7150). The project is called **PixOS** in
prose; the `pixelios-*` repo, directory and remote names are legacy and stay as they are.
Read this file and `README.md` before touching anything. Scoped rules live next to the code: `shim/`, `xnu/`, `soc/`, `device/` each have an `AGENTS.md`.

## Hard rules
The hard rules, their guards and the workflow (goal threads without tickets, commits by path with a worker trailer, one heavy job at a time, numbered patches in `xnu/patches/`,
generator/table over copied constants) are kept once, in the super repo's `AGENTS.md` and `docs/RULES.md`; this repo adds one:

8. **Code that writes hardware and has not passed an independent review stays on a local `wip/*` branch**, unpushed.

In short: reversible changes only (never write bootloader, modem, persist, efs, vbmeta, boot*, dtbo or the GPT; `userdata` is the only partition the iOS root may replace, via `tools/fflash-userdata.sh` with a stated reason); the human runs the phone; no secrets or firmware in git; tag every claim; never assume, check against a cited source.

## Commands
Task commands live in the super repo's `docs/recipes/commands.md`; the quality gate is `tools/check-all.sh` (it must print `GATE PASS <hash>`, rebuilds default images and overwrites `shim/out/*.img`).
Maintainer tools kept as documented commands (tool census callers): regenerate QR log patch 0094 with `python3 -I tools/make-qr2-patch.py XNU_TREE_PATCHED_THROUGH_0093 [out.patch]` and retrain its tables with `tools/qrz-train.py`; userdata read-back check of the UDUMP path (maintainer/human only): `python3 -I tools/udump-recv.py` before `fastboot boot`, then `tools/check-udump.sh`. Wi-Fi conformance matrix for the Apple80211/IO80211 Settings rows (SPEC-WIFI Part F): `python3 -I tools/matrix_wifi_list.py --spec <super>/docs/specs/SPEC-WIFI.md --root .` prints `RESULT: PASS matrix-wifi-list rows=N ...`.

## Do not
- Add a UFS WRITE path to the shim. The iOS root goes in with `fastboot flash userdata`.
- Loosen a safety audit (`tools/test-bluejay-boot.py`) to make a change pass; fix the change.
- Invent hardware constants; cite the source.
- Publish, push to a public remote, or change repo visibility without the user saying so.
