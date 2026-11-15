/*
 * SHIM_ASSERT failure path (shim_assert.h, docs/STYLE-C.md rule 4). Kept out of shim.c so a
 * host test can link it against a fake console and a fake shim_stop().
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "shim_assert.h"

#define ASSERT_LABEL "SWEET ASSERT"
/* Bound on the path scan; a longer __FILE__ is logged from its start. */
#define BASENAME_SCAN_MAX 256u

/*
 * Per-CPU re-entry depth. 0: report, then stop. 1 (the report or the stop itself asserted): stop
 * without reporting. 2 or more (the stop's own logging asserted again): quiet halt, no logging.
 */
static volatile uint32_t assert_depth[SHIM_ASSERT_SLOTS];

static uint32_t put_txt(char *dst, uint32_t cap, uint32_t pos, const char *s)
{
	/* style-allow: assert the failure reporter must not depend on the assertion it reports */
	const char *p = s;

	for (uint32_t n = 0; *p != 0 && pos + 1u < cap && n < RAMLOG_LINE_MAX; n++)
		dst[pos++] = *p++;
	return pos;
}

static uint32_t put_dec(char *dst, uint32_t cap, uint32_t pos, uint32_t v)
{
	/* style-allow: assert the failure reporter must not depend on the assertion it reports */
	char tmp[11];
	uint32_t n = 0;

	do {
		tmp[n++] = (char)('0' + v % 10u);
		v /= 10u;
	} while (v != 0 && n < sizeof tmp);
	while (n != 0 && pos + 1u < cap)
		dst[pos++] = tmp[--n];
	return pos;
}

void __attribute__((noreturn)) shim_assert_fail(const char *file, uint32_t line, const char *cond)
{
	/* style-allow: assert the failure reporter must not depend on the assertion it reports */
	/* The console line is label + ' ' + buf, within RAMLOG_LINE_MAX. */
	char buf[RAMLOG_LINE_MAX - sizeof ASSERT_LABEL];
	uint32_t pos = 0, base = 0;
	uint32_t slot = shim_cpu_slot() % SHIM_ASSERT_SLOTS;
	uint32_t depth = assert_depth[slot];

	assert_depth[slot] = depth + 1u;
	if (depth >= 2u)
		shim_halt();
	if (depth == 1u)
		shim_stop();
	for (uint32_t i = 0; i < BASENAME_SCAN_MAX && file[i] != 0; i++)
		if (file[i] == '/')
			base = i + 1u;
	pos = put_txt(buf, sizeof buf, pos, file + base);
	pos = put_txt(buf, sizeof buf, pos, ":");
	pos = put_dec(buf, sizeof buf, pos, line);
	pos = put_txt(buf, sizeof buf, pos, " ");
	pos = put_txt(buf, sizeof buf, pos, cond);
	buf[pos] = 0;
	con_clear(BLUE);
	con_str(ASSERT_LABEL, buf);
	shim_stop();
}
