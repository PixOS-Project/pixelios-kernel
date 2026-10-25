---
id: TP-029
tool: "tools/physmem-dev (range-guarded phys-mem device + client over TP-027 devsh)"
status: proposed
proposed_by: "Claude Sonnet 5.5 (driver, from the user decision of 2026-10-25)"
date: 2026-10-25
exposed_by: "docs/research/faster-bring-up.md step 11 + User decisions item 3: every Wi-Fi/GPU driver diagnostic change costs a rebuild + reboot"
extends: ""
related: "TP-027"
usage: "tools/physmem-dev read|write ADDR LEN [--range NAME] [--dry-run]  (via devsh)"
refs: "-"
---

## checked
TP-027 devsh (command runner on the booted root) is the transport, not this tool; no existing tool pokes physical ranges (`tools/tool-proposals check physmem`: NONE). Source of the idea: flame-style accelerator in docs/research/faster-bring-up.md section 6 Q3, user decision 2026-10-25: include, OPTIONAL development tooling only.

## why
Iterating the XNU-side Wi-Fi/GPU driver (bwfm port) by rebuild + reboot per diagnostic change is the slow loop; a range-guarded physical-memory device driven from a shell inspects and pokes authorised MMIO/memory without a reboot. Measured cost to fill in from the bwfm port's first iterations (seconds per rebuild+reboot cycle); until measured, the proposal stays DEFERRED. Depends on TP-027's preconditions.

## output
Per-command report ending in `RESULT: PASS|FAIL|ERROR ...`, exit 0/1/2, log dir. Acceptance criteria (all required):
1. Production driver path stays kernel-only; the device and client are development tooling outside the minimum boot and release requirements (built only with an explicit dev flag/boot-arg, absent by default).
2. Only explicitly authorised physical ranges are exposed, listed in a table (named ranges, from soc.json/board.json, not constants in code).
3. Every access validates bounds, rejects length/address overflow and wraparound, invalid or unmapped targets, misaligned access sizes, and any range not in the table.
4. Writes are refused unless the range is marked writable in the table and the write passes the same independent allowlist/audit as shim writers; no write path exists without the existing review (Opus for hardware writes).
5. Host tests with a fake device for every rejection above, plus mutants (each check disabled makes a test fail) in the repo's mutant style; the gate runs them.
6. A refused access is logged and returns an error, never a partial transfer.

## risks
Shell-accessible hardware interface: must never become a permanent dependency or ship enabled; physical-memory access on the phone is hardware-touching and UNREVIEWED: Opus 5.5 review required before any use (AGENTS.md rule 1 and 6). Must not expose bootloader/modem/persist/efs ranges or UFS writes (AGENTS.md 'Do not'). DEFERRED until the bwfm port has an XNU-side driver to iterate and TP-027's preconditions hold.

## amendments
- 2026-10-25 driver: status stays proposed; user decision (include, optional) recorded in docs/research/faster-bring-up.md.
