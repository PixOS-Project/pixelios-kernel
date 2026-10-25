# xnu/

Numbered patches applied in order to Apple's open-source xnu-8796 by CI (`.github/workflows/xnu.yml`).

- Never commit XNU source. Change behaviour by adding or regenerating `NNNN-name.patch` (contiguous numbering,
  `git apply`-clean on the previous state). Get the patched tree from the `xnu-buildtree` CI artifact to edit and diff.
- Gate device-specific code on the ADT (`compatible`), not on build flags, so `sweet` behaviour stays identical.
- The kernel runs on cores without PAC and without SHA3/SVE: compile risky files with `clang optimize off` if the
  vectoriser emits instructions gs101 lacks (seen on hardware with patch 0081).
- Drivers are polled and single-slot; keep DMA buffers physically contiguous and cache-maintained.
- A new patch is not done until CI is green and a device boot result is recorded in the PR / handoff notes.

- Coding rules: `docs/CODING-RULES.md` (CR-xx ids; each names its check). Read the rules for this area before editing; `tools/check-rules.py` enforces the lint ones in the gate.
