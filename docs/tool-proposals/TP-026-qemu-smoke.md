---
id: TP-026
tool: "QEMU runner --smoke (runner removed in standalone T2)"
status: rejected
proposed_by: "Claude Sonnet 5.5 (implementer, from decisions-s36.md)"
date: 2026-10-24
exposed_by: "no host check that today's kernel reaches PID 1 before a hardware cycle (dev-tooling-gaps.md:34,77)"
extends: ""
related: ""
usage: "(removed QEMU runner) PAYLOAD OUT --kernel K --smoke [--timeout 120]"
refs: "-"
---

## checked
decisions-s36.md section T4 (lines 110-118). tools-audit.md:138 says record as a flag on the QEMU runner (since removed), not a new tool (the wrapper is a mode of it). The runner already exited 2 on panic and 3 on blank/undecodable with `--fail-on-panic` (read from source).

## why
Step 0 (feasibility, no code): run the QEMU runner (since removed) with `--fail-on-panic` on today's bluejay kernel + sweet-init RAM disk. Kernels 0020-0035 reached PID 1 on virt (NOTES.md:439); today's is unmeasured. If PID 1 is not reached within 2 tries, drop this proposal and record why.

## output
If kept: `RESULT: PASS pid1` / `FAIL panic|no-pid1` / `ERROR qemu`; exit 0/1/2. PID-1 marker is Unknown U4 (dev-tooling-gaps.md:56 says `init running as PID 1`; confirm in the sweet-init source). Files: flag in the QEMU runner (since removed), `tools/test_qemu_smoke.py` (canned fb.txt/result.json). Add a gate step only if PID 1 is reached reliably. Verify: `python3 -m unittest tools/test_qemu_smoke.py`.

## risks
Host only, no device; feasibility gate first.

## amendments
- 2026-10-24 driver: status approved (user approved building, 2026-10-24).
- 2026-10-25 standalone T2: the QEMU runner was deleted (legendxdevil-derived); proposal closed as rejected.
