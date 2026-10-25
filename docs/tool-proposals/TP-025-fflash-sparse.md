---
id: TP-025
tool: "tools/fflash-userdata.sh --sparse"
status: approved
proposed_by: "Claude Sonnet 5.5 (implementer, from decisions-s36.md)"
date: 2026-10-24
exposed_by: "flash of the userdata root sends the full raw image (PLAN-BOOT-SPEED M7: bytes sent not logged); tools-audit.md:25,64"
extends: "tools/sparsediff.py"
related: "TP-020"
usage: "tools/fflash-userdata.sh IMG --yes-erase-userdata [--force] [--sparse]"
refs: "-"
---

## checked
decisions-s36.md section T3 (lines 94-108). tools-audit.md:139: must extend `tools/sparsediff.py` (shared writer), not copy it. `fflash-userdata.sh` is the only flashing tool and has no test.

## why
Convert IMG to Android sparse where all-zero 4 KiB blocks become FILL(0) chunks, never DONT_CARE (stale data would survive; PLAN-BOOT-SPEED.md:63). Keep the `.sha256` of the RAW image and all refusals (lock, hash, same-image, bluejay/unlocked); tee fastboot stdout to a log and print bytes sent (M7). sparsediff.py has MAGIC 0xed26ff3a, RAW 0xcac1, DONT_CARE 0xcac3 (read from source); FILL 0xcac2 is Unknown U2: cite libsparse/sparse_format.h before use. Its DONT_CARE is only valid against a known OLD image.

## output
Sparse file plus unchanged refusals and `RESULT:` line. Files: `tools/fflash-userdata.sh`, shared writer in `tools/sparsediff.py` (or `tools/sparse_writer.py` imported by it), `tools/test_sparse_writer.py`. Mandatory host test before any flash: expand with an independent reader (`simg2img`, Unknown U3 if installed) and compare sha256 with the raw image; a mutant emitting DONT_CARE for a zero run must fail. Verify: `python3 -m unittest tools/test_sparse_writer.py`.

## risks
Writes userdata only (pre-approved partition, AGENTS.md rule 3). Hardware-writing path: UNREVIEWED; Opus review required before first use, first use only on a planned flash. M7 numbers go into PLAN-BOOT-SPEED.

## amendments
- 2026-10-24 driver: status approved (user approved building, 2026-10-24).
