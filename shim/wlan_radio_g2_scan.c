/* wlan_radio_g2_scan.c - link rows of wlan_radio_host_test.c (T-08fd, goal W-D7 / P-W4).
 * They run the REAL G2 L23 escan code (sweet_wlan_g2e.h swg_e_l23, sweet_wlan_g2.h
 * swg_e_items, the bytes of xnu patch 0174) against the fake dongle of wlan_g2_host_test.c.
 * A separate translation unit because the two host tests define the same helper names: the
 * G2 test is included here under a renamed main and only g2l_*() leave it. The record the
 * scan leaves is read through sweet_wlan_g2_escan_rec(), the function the kernel glue
 * (patch 0201, sw_radio_replay) hands to swr_replay_escan. Built and linked with
 * wlan_radio_host_test.c by shim/build.sh (gcc -Wall -Wextra -Werror, ASan/UBSan clean). */
#define main wlan_g2_suite_main
#include "wlan_g2_host_test.c"
#undef main

/* One scan through swg_go's L23 level against the fake dongle. mode 0: 3 PARTIAL + SUCCESS
 * (scenario "main" of the G2 test), 1: 10 PARTIAL + SUCCESS, 2: no result at all (the 8000 ms
 * budget runs out), 3: 2 PARTIAL and no SUCCESS. Returns the run's rc (-101 on a hard stop);
 * *nbss = the bss lines L23 printed, *lv = the level reached, *logp = the run log. */
int g2l_scan(int mode, unsigned *nbss, unsigned *lv, const char **logp);

int
g2l_scan(int mode, unsigned *nbss, unsigned *lv, const char **logp)
{
	struct swd_plan p;
	struct swg_wx x;
	int rc = -1;

	if (swd_plan_make(4u, 43u, 4u, &p) != 0) {
		return -100;
	}
	gd_arm();
	if (mode == 0) {
		ge.einj[0].n = 25u;
		ge.einj[0].type = 54u;
		ge.einj[0].pre = 1;
		ge.neinj = 1u;
	} else if (mode == 1) {
		ge.npartial = 10u;
	} else if (mode == 2) {
		ge.npartial = 0u;
		ge.pfin = -2;
	} else if (mode == 3) {
		ge.npartial = 2u;
		ge.pfin = -2;
	}
	loglen = 0u;
	logbuf[0] = '\0';
	scan_run(&p, &x, 23, &rc, "g2l");
	logbuf[loglen] = '\0';
	*nbss = x.nbss;
	*lv = x.d4dlv;
	*logp = logbuf;
	return x.d4dstop ? -101 : rc;
}

unsigned g2l_fails(void);
unsigned
g2l_fails(void)
{
	return fails;
}

/* n results into the replay record, status = index (the record has SWG_E_MAX slots).
 * Returns the number of WIFI GAP lines the additions raised (swl_gap_tot). */
unsigned g2l_fill(unsigned n);
unsigned
g2l_fill(unsigned n)
{
	uint8_t b[SWG_REC_RES];
	const uint32_t t0 = swl_gap_tot;
	unsigned i;

	memset(&swg_rec, 0, sizeof swg_rec);
	memset(b, 0xA5, sizeof b);
	for (i = 0; i < n; i++) {
		swg_rec_add(i, b, SWG_REC_RES);
	}
	return swl_gap_tot - t0;
}
