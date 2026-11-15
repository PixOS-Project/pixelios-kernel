/* SHIM_ASSERT(cond): logged assertion for on-device code (docs/STYLE-C.md rule 4, T-98a7).
 * Device build (freestanding): a false cond logs "SWEET ASSERT <file>:<line> <cond>" (console + RAM log + UART, so pstore and the USB log carry it)
 * and takes the controlled stop (shim_stop(): same path as the panic handler, never returns).
 * An assertion that fails while its own report or stop is running (same CPU) stops without a report; a third entry halts quietly (shim_halt()).
 * Never assert in console.c, ramlog.c, pstore.c, shim_assert.c, the reset/panic path, the USB service hook or secondary_main (docs/STYLE-C.md; lint-c --style refuses it). cond is evaluated exactly once and must have no side effects
 * the caller depends on when assertions are compiled out; they are never compiled out here, so a check can always fail (CODING-RULES CR-01).
 * Host build (hosted gcc, __STDC_HOSTED__) maps to assert(); a host test that wants the device path defines SHIM_ASSERT_DEVICE first. */
#pragma once
#include <stdint.h>

#if defined(SHIM_ASSERT_DEVICE) || !__STDC_HOSTED__
void __attribute__((noreturn)) shim_assert_fail(const char *file, uint32_t line, const char *cond);
#define SHIM_ASSERT_SLOTS 8u	/* per-CPU re-entry depth slots (gs101 has 8 cores) */
/* Defined in shim.c: save the pstore log, then the panic path's reset. Never returns. */
void __attribute__((noreturn)) shim_stop(void);
/* Defined in shim.c: the quiet terminal stop (reset writes only, no console, ramlog or pstore). Never returns. */
void __attribute__((noreturn)) shim_halt(void);
/* Defined in shim.c: this CPU's slot, MPIDR Aff1 like cpu_report[]; 0 when out of range. */
uint32_t shim_cpu_slot(void);
#define SHIM_ASSERT(cond) ((cond) ? (void)0 : shim_assert_fail(__FILE__, __LINE__, #cond))
#else
#include <assert.h>
#define SHIM_ASSERT(cond) assert(cond)
#endif
