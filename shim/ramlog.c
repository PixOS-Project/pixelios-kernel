#include <stdint.h>
#include "ramlog.h"

/*
 * Circular buffer of complete lines. head is the oldest stored byte,
 * used the byte count; bytes (head + i) modulo RAMLOG_SIZE. Every write
 * appends one line ending in '\n', and eviction advances head past a
 * whole line, so the invariant "used bytes are whole lines" always holds.
 */

static char ramlog_buf[RAMLOG_SIZE];
static unsigned ramlog_head;
static unsigned ramlog_used;
static unsigned ramlog_appended;	/* lines ever appended: stable cursor across eviction */
/*
 * Sequential-read cursor for ramlog_copy_line: the byte offset (from head)
 * where retained line cur_idx starts. Every reader walks the lines in order
 * (pstore_save x2, USB pump, pager); without it each call rescanned from the
 * oldest byte, O(lines x bytes) per pstore save. MMU off (Device memory), a
 * full 32 KiB log and 2 saves per WLAN hold step made the shim take minutes
 * before the XNU jump (all_demo4 [obs 2026-11-11]: USB 260 s after fastboot
 * boot, 52 s with the cursor). Cleared on every append and reset (an append
 * can evict and move head).
 */
static uint32_t cur_idx, cur_off, cur_ok;

static unsigned idx(unsigned i)
{
	i += ramlog_head;
	while (i >= RAMLOG_SIZE)
		i -= RAMLOG_SIZE;
	return i;
}

void ramlog_reset(void)
{
	/* style-allow: assert ramlog.c is on the assert report path (STYLE-C) */
	ramlog_head = 0;
	ramlog_used = 0;
	ramlog_appended = 0;
	cur_ok = 0;
}

/* Advance head past the oldest stored line (or drop everything if the
 * stored region somehow holds no newline). */
static void drop_oldest_line(void)
{
	for (unsigned k = 0; k < ramlog_used; k++) {
		if (ramlog_buf[idx(k)] == '\n') {
			ramlog_head = idx(k + 1);
			ramlog_used -= k + 1;
			return;
		}
	}
	ramlog_head = 0;
	ramlog_used = 0;
}

void ramlog_line(const char *line)
{
	/* style-allow: assert ramlog.c is on the assert report path (STYLE-C) */
	uint32_t len = 0;
	while (line[len] && len < RAMLOG_LINE_MAX - 1)
		len++;
	while (ramlog_used + len + 1 > RAMLOG_SIZE)
		drop_oldest_line();
	uint32_t pos = ramlog_used;
	for (uint32_t i = 0; i < len; i++)
		ramlog_buf[idx(pos + i)] = line[i];
	ramlog_buf[idx(pos + len)] = '\n';
	ramlog_used += len + 1;
	ramlog_appended++;
	cur_ok = 0;
}

unsigned ramlog_total(void)
{
	return ramlog_appended;
}

unsigned ramlog_lines(void)
{
	unsigned count = 0;
	for (unsigned k = 0; k < ramlog_used; k++)
		if (ramlog_buf[idx(k)] == '\n')
			count++;
	return count;
}

uint32_t ramlog_copy_line(uint32_t index, char *dst, uint32_t cap)
{
	/* style-allow: assert ramlog.c is on the assert report path (STYLE-C) */
	if (!cap)
		return 0;
	uint32_t seen = 0, out = 0, k = 0;
	/* resume at a known line start (same content: no append since) */
	if (cur_ok && index >= cur_idx && cur_off <= ramlog_used) {
		seen = cur_idx;
		k = cur_off;
	}
	for (; k < ramlog_used; k++) {
		char c = ramlog_buf[idx(k)];
		if (c == '\n') {
			if (seen == index) {
				dst[out] = 0;
				cur_idx = index + 1;
				cur_off = k + 1;
				cur_ok = 1;
				return out;
			}
			seen++;
			continue;
		}
		if (seen == index && out + 1 < cap)
			dst[out++] = c;
	}
	dst[0] = 0;
	return 0;
}

unsigned ramlog_page_count(unsigned rows_per_page)
{
	if (!rows_per_page)
		return 0;
	unsigned lines = ramlog_lines();
	return (lines + rows_per_page - 1) / rows_per_page;
}
