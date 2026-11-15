---
id: TP-049
tool: "tools/ota-extract"
status: building
proposed_by: "Sonnet 5.5 (delegate 20l, brief driver 20k)"
date: 2026-10-29
exposed_by: "R10 golden BP31: the golden Android boot must be the BP31 stock boot.img (kernel 6.1.134-android14-11 = the phone's vendor_dlkm vermagic), but the phone's boot_b holds the cp3a Magisk image [obs 20261027T222522Z-bootb]; the only BP31 boot source is the Google OTA payload.bin"
extends: ""
related: "TP-037"
usage: "tools/ota-extract OTA.zip --part NAME [--part NAME ...] --out DIR [--list]"
refs: "-"
---

## checked
**Caller:** the golden-bp31 run plan (super repo, driver writes it) and `tools/golden-pull magisk-patch` with `GOLDEN_MAGISK_IN=DIR/boot.img` (pinned sha256 list). Also related to TP-035 (golden-pull / fboot-android --build).
**Replaces:** nothing. `tools/tool-proposals check ota-extract` = NONE; `ls tools | grep -i -E 'ota|payload'` found nothing. Stock `payload-dumper` / `update_engine` host tools are third-party downloads that would be run on an untrusted 2.7 GB file; a stdlib-only script that checks every hash is smaller and reviewable.

## why
Without it the BP31 boot.img / vendor_boot.img would have to come from a downloaded dumper binary or from the phone (whose boot_b is the wrong image). Measured [obs driver 20l 2026-10-29]: `--part boot --part vendor_boot` on bluejay_beta-ota-bp31.250610.004 gave RESULT PASS; boot.img sha256 a18afa36...d04c, vendor_boot.img sha256 e6a8f772...b4a9bcb, both 67108864 B and equal to the manifest's new_partition_info hashes.

## output
Last line `RESULT: PASS|FAIL|ERROR ...`, exit 0 / 1 / 2. Writes DIR/NAME.img, DIR/SHA256SUMS, DIR/manifest.txt (partition, size, sha256, build fingerprint). `--list` prints every partition with size and op counts. Read-only on the zip (stored payload.bin read by offset); full payloads only (minor_version 0, ops REPLACE/REPLACE_BZ/REPLACE_XZ/ZERO), each op data hash and the final partition sha256 + size are checked, a bad partition file is deleted. Test: `python3 -I tools/test_ota_extract.py` (synthetic payload, bad-hash / SOURCE_COPY / incremental cases; mutant "drop the hash check" fails a test).

## risks
No device access (host only). The OTA zip is untrusted input: opened with `python3 -I`, never executed, never extracted wholesale, output stays under ~/pixelios-factory (rule 4: Google firmware never goes into git). No flashing: the extracted images only feed `golden-pull magisk-patch`, whose output is RAM-booted by `fboot-android --build bp31`.

## amendments
2026-10-29 (review golden-bp31-r1): F1 git-work-tree refusal, F2 symlink refusal + O_EXCL|O_NOFOLLOW + stale manifest removal, F3 bounded decompression + 16 GiB cap, F4 .part + fsync + rename after sha, F5 golden-pull hashes the copy it patches: all fixed.
- 2026-10-29 driver 20m, review golden-bp31-r2 (SAFE WITH CONDITIONS): extents and op total bounded by the partition size BEFORE decompression (r2 F3; mutant without it expands an 8 MB bomb); trailing data after a bz2/xz stream refused.
