# C style for on-device code (shim/, soc/)

A small, safety-first subset of [TigerStyle](https://github.com/tigerbeetle/tigerbeetle/blob/main/docs/TIGER_STYLE.md) (chosen by the user, 2026-11-01).
Our C runs with the MMU off on a phone nobody can attach a debugger to, with the watchdogs disabled: a hang stays a hang and a silent error costs a phone run.

**Scope**: `shim/*.c` and `soc/**/*.c`. Not in scope: `*_host_test.c`, `shim/host_test.c`, `xnu/patches`, vendored code, Python tools, headers.

## The seven rules
1. **Every loop has a fixed upper bound.** A `for`/`while` condition compares a counter or a deadline (`<`, `>`, `<=`, `>=`, or a decrement); a hardware poll has a timeout that logs what it waited for (AGENTS rule 8). A loop that is infinite on purpose (halt after reset) carries `/* bounded: why */` on or next to the loop line. *Why:* an unbounded poll on a register that never changes is a frozen phone with no log.
2. **No recursion.** *Why:* the stack is small and fixed; recursion depth is data-dependent. Checked with clang-tidy `misc-no-recursion` on the files you changed.
3. **Static allocation only.** No allocator exists on the shim; size buffers at compile time. *Why:* nothing can fail at run time for lack of memory. Review rule, not machine-checked.
4. **`SHIM_ASSERT(cond)`, at least 2 per new or changed function on average.** A failure logs `SWEET ASSERT <file>:<line> <cond>` and takes the controlled stop (`shim_stop()`: pstore checkpoint, then the panic path's reset). *Why:* an assertion turns a wrong state into a located stop instead of a wrong register write later. Assert pre/postconditions and invariants, not things the hardware may legitimately do (a poll timeout is an error to handle and log, rule 6, not an assertion). Do not assert inside the failure/stop path itself (`/* style-allow: assert <why> */`). The macro: `shim/shim_assert.h`; review: `docs/reviews/T-98a7-assert.md`.
5. **Explicitly sized types and units last.** `uint32_t`, `uint64_t`, `size_t`, not bare `int`/`long`/`unsigned`; names put the unit or qualifier last: `timeout_us_max`, `base_pa`. *Why:* register widths and units are where our bugs were.
6. **Every error is handled and logged.** No ignored return value, no stub that returns success unlogged (AGENTS rule 8). Review rule, not machine-checked.
7. **At most 70 lines per function (signature to closing brace) and 100 columns** (a tab counts 4). *Why:* a function you can read on one screen can be reviewed against its source.

Dropped on purpose: Zig-only rules, zero dependencies, scripts in Zig, the forced nested if/else rewrite.

## Where `SHIM_ASSERT` must not go (review `docs/reviews/T-98a7-assert.md`, SAFE WITH CONDITIONS)
- Not in `console.c`, `ramlog.c`, `pstore.c`, `shim_assert.c`, nor in `shim.c`'s `reset`, `shim_stop`, `shim_halt`, `exc_handler`, `bluejay_*`, `usbg_service`, `secondary_main`: the report and the stop run through them, so an assertion there recurses or hides itself. `tools/lint-c --style` refuses it (rule `stop-path`, not a ratchet). The failure path itself is bounded: depth 0 reports then stops, depth 1 stops without a report, depth 2+ halts quietly (`shim_halt()`, reset writes only); depth is per CPU.
- Not between a hardware write and its matching restore (wlanen, wlansmc, wlanlink, touchprobe windows, an ACPM/PMIC set with an undo, a link held for XNU): the stop runs no teardown and the hardware stays as written until the reset. Record the failure, fall through to the restore, assert before the first write or after the restore. Review-only.
- Not in an object whose undefined-symbol set is audited (`tools/test-bluejay-boot.py`; `usbproto.o` must have none): adding `shim_assert_fail` there is a reviewed change.
- The stop is not proven to land in fastboot: a plain gs101 build resets into a normal boot, REBOOT_BL builds only reach the bootloader if the register reads back as armed. Claim nothing until a device run shows it (date + build id).

## Enforcement: a ratchet, not a reformat
`tools/lint-c --style` (code: `tools/lint_c_style.py`; also run by `tools/validate-repo.py`, so by `tools/gate-lint-first` and the gate) checks rules 1, 4, 5, 7 and, via clang-tidy, 2.
Only code **new or changed against the merge base** (`git diff --merge-base trunk`) must comply: a function is changed when any line inside it is added or edited, a new file counts whole, a long line counts when it is added. Everything else is the **baseline**: counted and printed, never failing.
Output: `RESULT: PASS|FAIL style new=N baseline=N`. No mass reformat: it would conflict with every in-flight branch.

Escape, counted and printed (`allowed (style-allow) N`): `/* style-allow: <rule> <why> */` inside the function; rule = `loop-bound`, `assert`, `sized-types`, `func-len`; a marker without a why does not apply. Use it for a function that cannot honestly meet a rule (the assertion reporter, a long register table), not to silence a finding you could fix.

Baseline on 2026-11-01 (before this change): 74 files, 19876 lines in shim/ + soc/ minus host tests; no assert call in device code, about 36 functions over 70 lines, 1429 lines over 100 columns, 93 `while (` loops in shim/*.c.

Tests: `python3 tools/test_lint_c_style.py` (a passing and a failing fixture per rule, the ratchet, the escape); `shim/shim_assert_host_test.c` (the macro's log line).
