# T-98a7 review: SHIM_ASSERT / shim_assert_fail / shim_stop (commit b7653284)

Reviewer: Opus 5.5, adversarial, HW-WRITE safety class (the stop path ends in the shim reset chain). Read-only; no source changed.
Scope: `git show b7653284` (branch tools/T-98a7). During the review the branch moved to ed4d18c9 (lint-c style):
that commit changes shim_assert.c/.h/shim.c only in types (unsigned -> uint32_t) and comments [read from source: `git diff b7653284 -- shim/`],
so these findings apply to both. Line numbers are b7653284's. Tags: [src] = read from source, [host] = measured on the host, [guess].

## Verdict
SAFE WITH CONDITIONS. Nothing calls SHIM_ASSERT yet [src: grep over shim/ soc/ tools/], so the commit adds no new reachable stop. Today it is
exactly the exc_handler panic tail (shim.c:1036-1040 vs 1110-1111) [src]. The defect is in the design, not in the current behaviour: the
re-entry guard only covers the reporter. A failing assertion anywhere on the stop's own logging path recurses without bound (finding 1). That
has to be fixed, or ruled out by a guard, before the first caller lands.

## Answers
1. **Context.** Normal flow, poll loop, exc_handler, IRQ: OK. The stop needs no IRQs: reset() uses wait_ticks (WFE plus the event stream,
   console.c:56-76), pager_replay (console.c:394) and bluejay_reset_chain (shim.c:262-277) [src]. An IRQ that is acknowledged but never EOI'd
   does not matter before a reset [src: shim.c:1044-1061]. On bluejay, secondary_main (shim.c:1116) runs on a 4 KiB stack (shim.ld `_sec_stacks`,
   start.S:156-159) with the MMU off. A stop on a secondary runs reset(40) and a 10 min pager concurrently with the primary, which keeps
   running. That is the same as an exc_handler panic on a secondary today [src], so it is not worse, but it is not a controlled stop either
   (condition C3).
2. **Half-done sequences.** The stop runs no teardown. These restores are skipped: wlanen.c restore (:190, :275), wlansmc.c iso clear (:361),
   wlanlink.c teardown (:550, :651) and touchprobe.c clk_restore/pud_restore (:574, :1020, :1122) [src]. The hardware then stays in the written
   state for 40 s + up to PAGER_TOTAL_S = 600 s (console.c:20) + the REBOOT_BL waits (shim.c:250-257), and only then resets. A data abort in
   the same window does the same thing today, so this is not worse than the panic path [src]. Whether ~11 min in such a state is harmless (for
   example a Wi-Fi module powered with WLAN_EN driven, or raised DVFS) is a [guess]; nothing measured. Caller rule: C1.
3. **Bootloader/fastboot.** Plain GS101: PSCI SYSTEM_RESET, then RESET2, then a 1-tick WDT (shim.c:267-276). PSCI_FEATURES reports SYSTEM_RESET
   as unsupported, and the chain as a whole has reset the phone, but which step did it was not recorded (docs/research/10-reboot-to-bootloader-gs101.md:15-16, :126-128,
   observed per NOTES.md:109). The result is a normal boot, not fastboot: tools/to-fastboot.sh:3 says such a reset lands in recovery and the host
   `phone goto fastboot` takes it from there. REBOOT_BL: SYSIP_DAT0=0xfc through the PMU SMC, then SWRESET only if the readback is 0xfc
   (shim.c:243-259, audited by test-bluejay-boot.py:1465-1484). Research 10:17 says this is unverified on hardware. Non-GS101 (sweet) writes
   IMEM_RESTART_REASON=RESTART_BOOTLOADER and then resets (shim.c:1024-1031) [src]. Whether the phone lands in fastboot needs a device result.
   Not claimed.
4. **Recursion.** See findings 1 and 2. The second-entry design stops recursion only inside the reporter (shim_assert.c:37-38). It does not stop
   recursion through shim_stop's callees.
5. **Stack/size.** buf = 96 - 13 = 83 bytes. The line is label(12) + ' ' + at most 82 + NUL = 96 = RAMLOG_LINE_MAX (ramlog.h:14), so it fits
   exactly [src]. put_txt and put_dec keep pos <= cap-1, so `buf[pos] = 0` at :47 is in bounds. tmp[11] holds 10 digits of a 32-bit unsigned
   [src]. Frames from the bluejay ELF (llvm-objdump-19): shim_assert_fail 0x90, con_str 0xa0, pstore_save 0x120, pager_replay 0x120, shim_stop
   0x10 [host]. One stop is about 1 KiB plus a 256 B exception frame, which fits the 4 KiB secondary stack and the 32 KiB primary stack.
   -mgeneral-regs-only and -mstrict-align (build.sh:107) apply; the code is byte stores only [src].
6. **Audits.** shim_assert.o is in the ABS64 list (build.sh:417). The built object has only ADR_PREL/ADD_ABS_LO12/LDST32/CALL26 relocations
   and no ABS64. Its undefined symbols are con_clear, con_str and shim_stop. Its .bss holds assert_depth [host: readelf/nm out/t98/shim_assert.o].
   No object audit in test-bluejay-boot.py looks at shim_assert.o, and no audited object references it yet, so no audit changed. A future
   SHIM_ASSERT in an audited object adds `shim_assert_fail` to its `nm -u`. That fails closed against the sanctioned sets (test-bluejay-boot.py:466, :556, :1238).
   usbproto.o must have no undefined symbols at all (:1076), so it can never use SHIM_ASSERT (condition C4).

## Findings
1. **MEDIUM (latent): unbounded recursion if an assertion fails anywhere on the stop path.** shim_assert.c:37-38 sends the second entry straight
   to shim_stop(), and shim_stop logs through con_str. On GS101 that happens at shim.c:1016 (reset -> `con_str("RESET", ...)`), through
   pstore_panic_save -> status -> con_str (pstore.c:172, :299-301) and at shim.c:267-275 (the chain's con_hex/con_str). Scenario: a future
   `SHIM_ASSERT` in ramlog_line, con_str, uart_text, the USB service hook (con_str -> con_service, console.c:256), pstore_save, wait_ticks or
   pager_replay fails. Then assert -> stop -> con_str -> assert (depth >= 1) -> stop -> con_str -> ... and every level adds about 0.3-0.6 KiB.
   The 32 KiB primary stack overflows downward into .bss (ramlog, pstore state, assert_depth) and then into .data/.text. The image is linked
   -N RWX with the MMU off on bluejay [src: shim.ld, build.sh:420], so the code is trampled and the reset chain is never reached; the phone
   hangs or runs garbage. [host] model /tmp/t98/rec/t.c (shim_assert.c unchanged; con_str always asserts; fake stop calls con_str like reset()
   does) prints `UNBOUNDED: shim_stop entered 1001 times on one CPU`. pstore_panic_save's guard (pstore.c:168-188) does not help, because it
   returns and the caller goes on logging.
   **Fix:** make the depth three-state. Entry 0 reports and calls shim_stop(). Entry 1 calls shim_stop() without reporting. Any later entry
   calls a new non-logging terminal `shim_halt()` in shim.c:
   `void __attribute__((noreturn)) shim_halt(void) { smc(PSCI_SYSTEM_RESET,0,0,0); smc(PSCI_SYSTEM_RESET2,0x80000000UL,0,0); if (WDT_CL0_BASE) { (void)try_write32(WDT_CL0_BASE+4,1); (void)try_write32(WDT_CL0_BASE+8,1); (void)try_write32(WDT_CL0_BASE,(3U<<3)|(1U<<5)|1U); } for (;;) __asm__ volatile("wfi"); }`
   It uses the same writes as bluejay_reset_chain without any con_*; on non-GS101 it does the IMEM + PSCI + PSHOLD tail. In shim_assert_fail:
   `uint32_t d = assert_depth++; if (d >= 2u) shim_halt(); if (d == 1u) shim_stop();`.
   Also add a source guard (a test-bluejay-boot.py row, or a lint-c rule) that refuses `SHIM_ASSERT` in console.c, ramlog.c, pstore.c,
   shim_assert.c and in shim.c's reset/bluejay_reset_*/shim_stop/exc_handler/pager paths, with one mutant row.
   Add a host-test row: con_str always asserts and the fake shim_stop calls con_str. The row must reach the halt fake with nstop <= 2.
2. **LOW: the global, non-atomic depth hides a second CPU's assertion (rule 8).** `static volatile uint32_t assert_depth` (shim_assert.c:10)
   is shared by all CPUs, and `++` is a plain ldr/add/str. If CPU A is reporting and CPU B asserts, B sees depth != 0 and goes to shim_stop with
   no `SWEET ASSERT` line, so its cause is lost (or both read 0 and both report). [src] A pure race; not reproduced.
   **Fix:** a per-CPU depth array indexed like cpu_report (MPIDR Aff1 on gs101, `< NR_CPUS`, falling back to slot 0 when out of range), so each
   CPU reports its own first assertion and only re-entry on the same CPU skips the report.
3. **LOW: host-test gaps (3 surviving mutants).** [host] Against shim_assert_host_test.c these mutants survive: (a) buf shrunk to 60;
   (b) put_txt `pos + 2u < cap`; (c) put_dec `pos < cap`. Mutant (c) is a 1-byte stack overflow at :47 when a long basename makes the line number
   reach the cap. Killed mutants: depth no-op, put_txt `pos < cap`, basename `i`, no con_clear, buf +1, put_dec order.
   **Fix:** add two rows: an exact fill (a 105-char condition gives `strlen(lines[0]) == RAMLOG_LINE_MAX - 1`), and a basename of about 80 chars
   with line 4294967295 and the same strlen check. Compile the chk with `-fsanitize=address,undefined` (build.sh:474). Measured in
   /tmp/t98/mut2: the control passes and all three mutants fail; (c) fails by ASan stack-buffer-overflow.

## Conditions (for SAFE WITH CONDITIONS)
- C1. Never SHIM_ASSERT between a hardware write and its matching restore (wlanen/wlansmc/wlanlink/touchprobe windows, any ACPM/PMIC set that
  has an undo, and while a link is held for XNU). Inside such a window, record the failure and fall through to the restore, as the existing code
  does (touchprobe.c:581, wlanen.c:275-279). Assert before the first write or after the restore.
- C2. Apply finding 1 (halt tier plus source guard) before the first SHIM_ASSERT caller merges.
- C3. No SHIM_ASSERT in secondary_main or anything it calls (MMU off, 4 KiB stack, concurrent with the primary). A secondary records into
  cpu_report and lets the primary decide.
- C4. Adding `shim_assert_fail` to an audited object's sanctioned undefined set is a reviewed change. The audit row must name it as the stop path
  (it reaches the SMC and WDT writes in shim.o, which the per-object audits do not follow). usbproto.o stays assert-free.
- C5. Do not use SHIM_ASSERT inside the USB service hook (usbg_service, shim.c:487): con_in_service stays 1 (console.c:50), so the line never
  reaches the USB log, only the screen and pstore.
- C6. No "returns to fastboot" claim for the assert stop until a device run (date + build id) shows it, for both plain and REBOOT_BL builds.

Verification: `gcc -O1 -Wall -Wextra -Werror -I. -o /tmp/t98/sa shim_assert_host_test.c && /tmp/t98/sa` -> `RESULT: PASS shim_assert_host_test` [host].

AP-CANDIDATE: a re-entry guard that protects the reporter but not the stop's own logging callees (shim_assert.c:37 -> shim.c:1016 -> con_str)
[host /tmp/t98/rec] | guard: tools/test-bluejay-boot.py source row refusing SHIM_ASSERT on the stop path, plus a host row in which con_str always asserts.

VERDICT: SAFE WITH CONDITIONS

## Driver resolution of findings and conditions (after commit cfaf46d1; not part of the reviewer's verdict)
- F1 (recursion through the stop's own logging): fixed. `shim_assert_fail` is three-state per CPU (report+stop / stop / `shim_halt()`), `shim_halt()` in shim.c makes reset writes only; host rows `con_always_asserts` + logging stop end in the halt with `nstop <= 2`; source guard: `tools/lint-c --style` rule `stop-path` (console.c, ramlog.c, pstore.c, shim_assert.c and the named shim.c functions), fixtures in `tools/test_lint_c_style.py`, mutant `STOP_FILES = ()` fails it.
- F2 (global depth): fixed, per-CPU `assert_depth[SHIM_ASSERT_SLOTS]` indexed by `shim_cpu_slot()` (MPIDR Aff1 like `cpu_report`, 0 when out of range); host rows for a second CPU, the same CPU and an out-of-range slot.
- F3 (host-test gaps): fixed. Rows: exact fill (95 characters), 80-character basename with line 4294967295, 199-character path without a slash; `shim/build.sh` compiles the test with ASan/UBSan when the host has the runtime (loud NOTE when not). All three surviving mutants (buf 60, put_txt cap+1, put_dec cap) now die, as do halt-tier, per-CPU and no-con_clear mutants.
- C1, C3, C5, C6 and C4 are written into docs/STYLE-C.md ("Where SHIM_ASSERT must not go"); C3/C5 are also enforced by the `stop-path` rule (secondary_main, usbg_service). C1 is review-only. C2 is done (halt tier + source guard before any caller exists; there is none yet).
- Build: `shim/build.sh --board bluejay --out out/t98` after the fixes ends with `PASS header v4 (lz4) ... host tests & safety checks passed` and lists `RESULT: PASS shim_assert_host_test` [observed, host build, branch tools/T-98a7].
