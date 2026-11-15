#pragma once
#include <stdint.h>

/*
 * RAM log for the probe console: every console line is also appended here
 * (fixed .bss buffer, no malloc) so the whole report survives the live run
 * and the pager can replay it after the probe finishes. When the buffer
 * fills, whole oldest lines are evicted, so no stored line is ever torn.
 *
 * Pure C with no board or asm dependencies: shim/ramlog_host_test.c
 * exercises wraparound/overflow directly on the host.
 */

#define RAMLOG_SIZE	32768u		/* bytes */
#define RAMLOG_LINE_MAX	96u		/* per-line cap, including the NUL */

/* Append one complete line (no '\n'); lines longer than the cap truncate. */
void ramlog_line(const char *line);

/* Drop everything (host tests only; the device never resets the log). */
void ramlog_reset(void);

/* Complete lines currently retained. */
unsigned ramlog_lines(void);

/* Copy retained line `index` (0 = oldest) into dst, NUL-terminated and
 * truncated to cap. Returns the copied length; 0 for an absent line. */
uint32_t ramlog_copy_line(uint32_t index, char *dst, uint32_t cap);

/* Pages needed at rows_per_page rows; 0 when the log is empty. */
unsigned ramlog_page_count(unsigned rows_per_page);

/* Lines ever appended (wraps at 2^32). Line `index` of the retained log is
 * absolute line (ramlog_total() - ramlog_lines() + index), so a stream
 * cursor survives eviction: lines already evicted are simply skipped. */
unsigned ramlog_total(void);
