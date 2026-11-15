# shim/

Bare-metal AArch64 code that runs under `fastboot boot` before XNU (MMU off, EL2->EL1, no libc, `-mgeneral-regs-only
-mstrict-align`). Every bug here can hang or reset the phone.

- Freestanding C only: no libc, no stdlib allocation, no static initialisers holding absolute pointers (build.sh rejects
  ABS64 relocations); build pointer tables at run time.
- Every wait is counter-bounded; the watchdogs are disabled, so a hang stays a hang.
- UFS is read-only: the only commands that may reach the doorbell are NOP OUT, TEST UNIT READY, READ CAPACITY(10) and
  READ(10) inside the `userdata` GPT range. `ufs.c` checks the real UPIU bytes at the doorbell; do not bypass it.
- MMIO writes go through the allowlisted helpers (`usbgadget.c` `w32_allowed`, `ufsinit.c` register set). Adding a
  register means citing the Linux/ABL source in the comment and extending the audit in `tools/test-bluejay-boot.py`.
- Test with host tests first (`*_host_test.c`, fake hardware), then `tools/check-all.sh`, then hardware.
- SoC-specific code lives under `soc/<vendor>/<soc>/shim/`; keep `shim/` generic and key code off `SOC_GS101`.
- Per-experiment flags (`--touch-probe`, `--gpu-*`, `--wlan-*`, ...) each cite their source, write only through the allowlisted helpers and extend the audit, the host test and the negative tests together; a new flag is a boot-arg or table entry, not an essay here. Per-flag notes: `docs/archive/shim-probe-flags.md`. PMIC/ACPM/PMU writes are allowed since 2026-10-22 (docs/SAFETY.md): values from cited sources, audited allowlist, read before write, bounded waits; fuses/OTP/PMIC trim and charger limits stay off limits.

- Coding rules: `docs/CODING-RULES.md` (CR-xx ids; each names its check). Read the rules for this area before editing; `tools/check-rules.py` enforces the lint ones in the gate.
- C style (bounded loops, no recursion, `SHIM_ASSERT`, sized types, 70 lines / 100 columns), enforced on new or changed code only: `docs/STYLE-C.md`, `tools/lint-c --style`.
