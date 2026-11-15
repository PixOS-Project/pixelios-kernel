/* Host unit test for the pure FTM5 protocol layer of the Pixel 6a touch driver (xnu patch 0088,
 * bsd/dev/sweet_ftm5.h). shim/build.sh extracts that header from the patch, so the kernel and this test
 * always see the same bytes. Build (also run by shim/build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -Iout -o out/ftm5_host_test ftm5_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "sweet_ftm5.h"

/* Encode an enter/motion/leave event the way the firmware packs it (fts.c:3068-3069 inverted). */
static void
mk(uint8_t *e, uint8_t id_byte, unsigned id, unsigned type, int x, int y, unsigned remaining)
{
	memset(e, 0, FTM5_EVENT_SIZE);
	e[0] = id_byte;
	e[1] = (uint8_t)(id << 4 | type);
	e[2] = (uint8_t)(x & 0xff);
	e[3] = (uint8_t)(((x >> 8) & 0x0f) | ((y & 0x0f) << 4));
	e[4] = (uint8_t)(y >> 4);
	e[5] = 0x30;                   /* pressure, ignored */
	e[7] = (uint8_t)remaining;
}

int
main(void)
{
	uint8_t e[FTM5_EVENT_SIZE], cmd[5];
	struct ftm5_ev r;
	const uint8_t want[2] = { 0x36, 0x48 };

	/* chip-id register read: 0xFB then the address, most significant byte first */
	assert(ftm5_hwreg_read_cmd(cmd, FTM5_ADDR_DCHIP_ID) == 5);
	assert(cmd[0] == 0xFB && cmd[1] == 0x20 && cmd[2] == 0 && cmd[3] == 0 && cmd[4] == 0);
	assert(ftm5_hwreg_read_cmd(cmd, 0x12345678u) == 5);
	assert(cmd[1] == 0x12 && cmd[2] == 0x34 && cmd[3] == 0x56 && cmd[4] == 0x78);

	/* chip id: straight, swapped, wrong, and the two floating-bus patterns */
	{
		const uint8_t a[2] = { 0x36, 0x48 }, b[2] = { 0x48, 0x36 }, c[2] = { 0x36, 0x40 },
		    f[2] = { 0xff, 0xff }, z[2] = { 0, 0 };
		assert(ftm5_chip_id_match(a, want) == 1);
		assert(ftm5_chip_id_match(b, want) == 2);
		assert(ftm5_chip_id_match(c, want) == 0);   /* SALIXP 0x36 0x40 is a different part */
		assert(ftm5_chip_id_match(f, want) == 0);
		assert(ftm5_chip_id_match(z, want) == 0);
	}

	/* events remaining: low 5 bits of byte 7, capped at FIFO_DEPTH - 1 */
	mk(e, 0x13, 0, 1, 0, 0, 0);
	assert(ftm5_events_remaining(e) == 0);
	e[7] = 5;
	assert(ftm5_events_remaining(e) == 5);
	e[7] = 0xe0 | 31;
	assert(ftm5_events_remaining(e) == 31);
	e[7] = 0x1f;
	assert(ftm5_events_remaining(e) == FTM5_FIFO_DEPTH - 1);

	/* enter: id 3, finger, the panel's far corner (st,max-coords 1079 2399) */
	mk(e, FTM5_EVT_ENTER_POINT, 3, FTM5_TYPE_FINGER, 1079, 2399, 0);
	r = ftm5_parse_event(e);
	assert(r.kind == FTM5_DOWN && r.id == 3 && r.x == 1079 && r.y == 2399);

	/* every coordinate bit survives the packing (12-bit x, 12-bit y) */
	mk(e, FTM5_EVT_MOTION_POINT, 9, FTM5_TYPE_GLOVE, 0xabc, 0x123, 0);
	r = ftm5_parse_event(e);
	assert(r.kind == FTM5_DOWN && r.id == 9 && r.x == 0xabc && r.y == 0x123);
	mk(e, FTM5_EVT_MOTION_POINT, 0, FTM5_TYPE_FINGER, 0x123, 0xfff, 0);
	r = ftm5_parse_event(e);
	assert(r.kind == FTM5_DOWN && r.id == 0 && r.x == 0x123 && r.y == 0xfff);

	/* leave releases for every type, including the ones that are not reported as a touch */
	for (unsigned t = 0; t < 16; t++) {
		mk(e, FTM5_EVT_LEAVE_POINT, 7, t, 10, 20, 0);
		r = ftm5_parse_event(e);
		assert(r.kind == FTM5_UP && r.id == 7);
	}

	/* palm and grip cancel the contact; hover and unknown types are ignored */
	mk(e, FTM5_EVT_ENTER_POINT, 2, FTM5_TYPE_PALM, 100, 100, 0);
	assert(ftm5_parse_event(e).kind == FTM5_CANCEL);
	mk(e, FTM5_EVT_MOTION_POINT, 2, FTM5_TYPE_GRIP, 100, 100, 0);
	assert(ftm5_parse_event(e).kind == FTM5_CANCEL);
	mk(e, FTM5_EVT_MOTION_POINT, 2, FTM5_TYPE_HOVER, 100, 100, 0);
	assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
	mk(e, FTM5_EVT_MOTION_POINT, 2, 0xd, 100, 100, 0);
	assert(ftm5_parse_event(e).kind == FTM5_IGNORED);

	/* touch id 10..15 is invalid for enter, motion and leave */
	for (unsigned id = FTM5_TOUCH_ID_MAX; id < 16; id++) {
		mk(e, FTM5_EVT_ENTER_POINT, id, FTM5_TYPE_FINGER, 1, 1, 0);
		assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
		mk(e, FTM5_EVT_LEAVE_POINT, id, FTM5_TYPE_FINGER, 1, 1, 0);
		assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
	}

	/* empty slot, controller ready, and the event classes this driver does not use */
	memset(e, 0, sizeof(e));
	assert(ftm5_parse_event(e).kind == FTM5_NONE);
	mk(e, FTM5_EVT_CONTROLLER_READY, 0, 0, 0, 0, 0);
	assert(ftm5_parse_event(e).kind == FTM5_READY);
	mk(e, 0x43, 0, 1, 0, 0, 0);    /* status update */
	assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
	mk(e, 0xF3, 0, 2, 0, 0, 0);    /* error: hard fault */
	assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
	/* patch 0348: dispatch on the high nibble (fts.c:4570); bits 3:2 = touch major high bits (fts.c:3083), as seen on hardware */
	mk(e, 0x27, 1, FTM5_TYPE_FINGER, 500, 1200, 0);
	assert(ftm5_parse_event(e).kind == FTM5_DOWN && ftm5_parse_event(e).id == 1);
	assert(ftm5_parse_event(e).x == 500 && ftm5_parse_event(e).y == 1200);
	mk(e, 0x2f, 1, FTM5_TYPE_FINGER, 501, 1201, 0);
	assert(ftm5_parse_event(e).kind == FTM5_DOWN && ftm5_parse_event(e).x == 501);
	mk(e, 0x2b, 1, FTM5_TYPE_FINGER, 502, 1202, 0);
	assert(ftm5_parse_event(e).kind == FTM5_DOWN && ftm5_parse_event(e).y == 1202);
	mk(e, 0x17, 2, FTM5_TYPE_FINGER, 10, 20, 0);
	assert(ftm5_parse_event(e).kind == FTM5_DOWN && ftm5_parse_event(e).id == 2);
	mk(e, 0x3f, 1, FTM5_TYPE_FINGER, 0, 0, 0);     /* the missed leave behind the 5.8 s stuck contact */
	assert(ftm5_parse_event(e).kind == FTM5_UP && ftm5_parse_event(e).id == 1);
	mk(e, 0x14, 0, 1, 0, 0, 0);    /* class 1 = enter whatever the low nibble (Linux does the same) */
	assert(ftm5_parse_event(e).kind == FTM5_DOWN);
	mk(e, 0x07, 0, 0, 0, 0, 0);    /* class 0, non-zero = controller ready handler */
	assert(ftm5_parse_event(e).kind == FTM5_READY);
	mk(e, 0xE3, 0, 0, 0, 0, 0);    /* debug: known, unused */
	assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
	mk(e, 0x53, 0, 0, 0, 0, 0);    /* user report: known, unused */
	assert(ftm5_parse_event(e).kind == FTM5_IGNORED);
	for (unsigned c = 0x6u; c <= 0xDu; c++) {   /* classes with no handler: reported, never silent */
		mk(e, (uint8_t)(c << 4 | 0x3u), 0, 1, 0, 0, 0);
		assert(ftm5_parse_event(e).kind == FTM5_UNKNOWN);
	}

	/* scale: the panel is 1079x2399 raw onto 1080x2400, so it is the identity */
	for (int v = 0; v <= 1079; v++) {
		assert(ftm5_scale(v, 1079, 1080) == v);
	}
	for (int v = 0; v <= 2399; v++) {
		assert(ftm5_scale(v, 2399, 2400) == v);
	}
	assert(ftm5_scale(-5, 1079, 1080) == 0);
	assert(ftm5_scale(5000, 1079, 1080) == 1079);   /* clamped, as fts.c:3089 clamps to x_axis_max */
	assert(ftm5_scale(539, 1079, 540) == 269);      /* a coarser output rounds down */
	assert(ftm5_scale(10, 0, 1080) == 0);           /* no division by zero */
	assert(ftm5_scale(10, 1079, 1) == 0);

	printf("ftm5_host_test: ok\n");
	return 0;
}
