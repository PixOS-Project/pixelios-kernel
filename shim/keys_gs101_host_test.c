/* Host test of the button debounce in xnu patch 0087 (header extracted from the patch by build.sh). */
#include <assert.h>
#include <stdio.h>

#include "sweet_keys_gs101_debounce.h"

static int feed(struct keys_deb *d, const char *s, unsigned need, int *events)
{
	int n = 0;
	for (; *s; s++) {
		int ev = keys_deb_step(d, *s == '1', need);
		if (ev >= 0)
			events[n++] = ev;
	}
	return n;
}

int main(void)
{
	struct keys_deb d = {0}, h = {0}, o = {0};
	int ev[16];

	/* raw level: active low means bit 0 = down */
	assert(keys_raw_down(0x0, 3, 1) == 1);
	assert(keys_raw_down(0x8, 3, 1) == 0);
	assert(keys_raw_down(0x8, 3, 0) == 1);
	assert(keys_raw_down(0x0, 3, 0) == 0);
	assert(keys_raw_down(0xfffffff7u, 3, 1) == 1);

	/* idle, then a clean press and release */
	assert(feed(&d, "0000", 3, ev) == 0);
	assert(feed(&d, "11", 3, ev) == 0);		/* only 2 of 3 */
	assert(feed(&d, "1", 3, ev) == 1 && ev[0] == 1);
	assert(feed(&d, "1111", 3, ev) == 0);		/* held: no repeat */
	assert(feed(&d, "000", 3, ev) == 1 && ev[0] == 0);

	/* bounce: glitches shorter than need never report */
	assert(feed(&d, "0101001000", 3, ev) == 0);
	/* bouncy press: 1,0,1 resets the count, then 3 in a row reports once */
	assert(feed(&d, "101111", 3, ev) == 1 && ev[0] == 1);
	/* bouncy release */
	assert(feed(&d, "1001000", 3, ev) == 1 && ev[0] == 0);

	/* the first sample only sets the level: key held at start emits nothing, release reports */
	assert(feed(&h, "1111", 3, ev) == 0);
	assert(feed(&h, "000", 3, ev) == 1 && ev[0] == 0);

	/* need = 1 reports every change at once */
	assert(feed(&o, "0110", 1, ev) == 2 && ev[0] == 1 && ev[1] == 0);

	puts("keys_gs101 host test OK");
	return 0;
}
