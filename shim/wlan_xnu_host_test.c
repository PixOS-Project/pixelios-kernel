/* Host replay test of the stage-1 chip layer in xnu patch 0120 (sweet_wlan_chip.h, extracted from the patch by build.sh): the values observed
 * on the Pixel 6a by the shim runs w3a-n30/n31/n32b/n33 (BCM4389 rev 3, ChipCommon rev 70) are the fake chip; the guard is the one the XNU glue applies. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static int quiet_printf(const char *fmt, ...) { (void)fmt; return 0; }
#define printf quiet_printf
#include "sweet_wlan_chip.h"
#undef printf

/* the 218 EROM dwords of run w3a-n33 (identical to ~/pixelios-artifacts/bwfm-host/erom.hex) */
static const uint32_t erom[] = {
	0x4bf80001, 0x46084411, 0x00000003, 0x18000005, 0x18540135, 0x00008000,
	0x1c000135, 0x04000000, 0x181000c5, 0x18103185, 0x4bf83c01, 0x48084411,
	0x00000103, 0x18001005, 0x18002005, 0x18003005, 0x08000135, 0x08000000,
	0x0000013d, 0x80000000, 0x00000008, 0x80000000, 0x181010c5, 0x18102185,
	0x4bf84701, 0x09004211, 0x00000203, 0x18020005, 0x18027035, 0x00044000,
	0x181200c5, 0x4bf81201, 0x5608c431, 0x00000303, 0x00000413, 0x00000523,
	0x18021005, 0xe8800135, 0x00800000, 0x181210c5, 0x181271c5, 0x181282c5,
	0x18129185, 0x4bf81201, 0x5608c431, 0x00000603, 0x00000713, 0x00000823,
	0x18022005, 0xe8000135, 0x00800000, 0x181220c5, 0x1812a1c5, 0x1812b2c5,
	0x1812c185, 0x4bf81201, 0x56084411, 0x00000903, 0x18023005, 0xe9000135,
	0x00800000, 0x181230c5, 0x1812d185, 0x4bf85101, 0x04084411, 0x00000a03,
	0x18024005, 0x18500135, 0x00030000, 0x181240c5, 0x1812e185, 0x4bf84901,
	0x09080401, 0x18026005, 0x00000135, 0x001db000, 0x00200135, 0x002a0000,
	0x18126185, 0x4bf85701, 0x05084211, 0x00000b03, 0x19000035, 0x01000000,
	0x181080c5, 0x18109085, 0x4bf85501, 0x03000201, 0x18004005, 0x4bf85501,
	0x03000201, 0x18005005, 0x4bf85c01, 0x00000201, 0x18006005, 0x4bf84101,
	0x0a000201, 0x18007005, 0x4bf84101, 0x0a000201, 0x18008005, 0x4bf84101,
	0x0a000201, 0x18009005, 0x4bf84101, 0x0a000201, 0x1800a005, 0x4bf84101,
	0x0a000201, 0x1800b005, 0x4bf84001, 0x14000201, 0x18010015, 0x4bf82701,
	0x28000201, 0x18012005, 0x4bf84a01, 0x64000201, 0x18013005, 0x18014005,
	0x18015005, 0x1801a005, 0x4bf84a01, 0x66000201, 0x18016005, 0x1801b005,
	0x4bf84a01, 0x65000201, 0x18017005, 0x18018005, 0x18019005, 0x1801c005,
	0x4bf85e01, 0x00000201, 0x1801d005, 0x4bf85e01, 0x00000201, 0x1806b005,
	0x43b13501, 0x00080201, 0x18000075, 0x0000c000, 0x18106085, 0x43b13501,
	0x00080201, 0x18010075, 0x0000e000, 0x18107085, 0x43b13501, 0x00080201,
	0x18020075, 0x0004c000, 0x18134085, 0x43b36601, 0x00000201, 0x18105005,
	0x43b36601, 0x00000201, 0x18130005, 0x43b30101, 0x00000201, 0x18200035,
	0x00100000, 0x43b30101, 0x00000201, 0x18300035, 0x00100000, 0x43b42001,
	0x00000201, 0x18400035, 0x00010000, 0x43b03101, 0x00084011, 0x00000c03,
	0x1810a0c5, 0x18131085, 0x43b03101, 0x00084011, 0x00000d03, 0x1810b0c5,
	0x18132085, 0x43b03101, 0x00084011, 0x00000e03, 0x1810c0c5, 0x18133085,
	0x43bfff01, 0x00080201, 0x001db035, 0x00025000, 0x004a0035, 0x07b60000,
	0x10000035, 0x08000000, 0x1800c025, 0x1801e015, 0x18025005, 0x1806c035,
	0x00094000, 0x1810d035, 0x00013000, 0x18125005, 0x18135035, 0x000cb000,
	0x18418035, 0x000e8000, 0x18530035, 0x00010000, 0x18548035, 0x00ab8000,
	0x1a000035, 0x02000000, 0x20000035, 0xc7800000, 0xe9800035, 0x16800000,
	0x18104085, 0x0000000f,
};
static const struct { unsigned id, rev; uint32_t base, wrap; } want_cores[] = {
	{ 0x800, 70, 0x18000000, 0x18100000 },
	{ 0x83c, 72, 0x18001000, 0x18101000 },
	{ 0x847, 9, 0x18020000, 0x18120000 },
	{ 0x812, 86, 0x18021000, 0x18121000 },
	{ 0x812, 86, 0x18022000, 0x18122000 },
	{ 0x812, 86, 0x18023000, 0x18123000 },
	{ 0x851, 4, 0x18024000, 0x18124000 },
	{ 0x849, 9, 0x18026000, 0x18126000 },
	{ 0x857, 5, 0x00000000, 0x18108000 },
	{ 0x840, 20, 0x18010000, 0x00000000 },
	{ 0x827, 40, 0x18012000, 0x00000000 },
	{ 0x135, 0, 0x00000000, 0x18106000 },
	{ 0x135, 0, 0x00000000, 0x18107000 },
	{ 0x135, 0, 0x00000000, 0x18134000 },
	{ 0x031, 0, 0x00000000, 0x1810a000 },
	{ 0x031, 0, 0x00000000, 0x1810b000 },
	{ 0x031, 0, 0x00000000, 0x1810c000 },
	{ 0xfff, 0, 0x1801e000, 0x18104000 },
};
static const struct { uint32_t a, v; } obs[] = {
	{ 0x18000000, 0x10034389 }, { 0x180000FC, 0x18105000 }, { 0x180000AC, 0x000A044E }, { 0x180001E0, 0x02010240 },
	{ 0x18012600, 0x00000180 }, { 0x18012604, 0xC4565F28 }, { 0x18012608, 0x0000412A }, { 0x18012618, 0x060800ED }, { 0x1801261C, 0x0E4FFFFF },
	{ 0x18120408, 0 }, { 0x18120800, 1 }, { 0x18120804, 0 }, { 0x18126408, 0 }, { 0x18126800, 1 }, { 0x18126804, 0 },
	{ 0x18000004, 0x10000000 }, /* not observed (R-row added by W-D2): any value */
	{ 0x18121408, 0 }, { 0x18121800, 1 }, { 0x18121804, 0 }, { 0x18122408, 0 }, { 0x18122800, 1 }, { 0x18122804, 0 },
	{ 0x18123408, 0 }, { 0x18123800, 1 }, { 0x18123804, 0 },
};
#define NOBS (sizeof obs / sizeof obs[0])

struct fake { unsigned nrd, nerom, refused, seen[NOBS], erom_zero, fail_at, failed; const uint32_t *alt; unsigned nalt; };

/* the same decision the XNU glue sw_rd() makes before it touches the BAR */
static int fake_rd(void *ctx, uint32_t a, uint32_t *v)
{
	struct fake *f = ctx;
	uint32_t win = a & ~0xFFFu, off = a & 0xFFFu;
	unsigned i;

	if (!swl_win_ok(win) || !swl_rd_ok(win, off)) {
		f->refused++;
		return -1;
	}
	if (f->fail_at && !f->failed && f->nrd + 1 == f->fail_at) {	/* one failed read, then the bus works again: the walk must still not go on */
		f->failed = 1;
		return -1;
	}
	f->nrd++;
	if (win == SWL_EROM_PAGE) {
		f->nerom++;
		if (f->alt)
			*v = off / 4 < f->nalt ? f->alt[off / 4] : 0;
		else
			*v = f->erom_zero ? 0 : (off / 4 < sizeof erom / 4 ? erom[off / 4] : 0);
		return 0;
	}
	for (i = 0; i < NOBS; i++)
		if (obs[i].a == a) {
			f->seen[i]++;
			*v = obs[i].v;
			return 0;
		}
	fprintf(stderr, "UNOBSERVED read %#x\n", a);
	assert(0);
	return -1;
}

static int allowed_count(void)
{
	uint32_t win, off;
	int n = 0;

	for (win = 0x18000000u; win < 0x18200000u; win += 0x1000u)
		for (off = 0; off < 0x1000u; off += 4)
			if (swl_win_ok(win) && swl_rd_ok(win, off))
				n++;
	return n;
}

int main(void)
{
	static struct swl_chip c;
	struct fake f;
	struct swl_bus b = { fake_rd, &f };
	unsigned i, total;

	/* level 2: everything runs, every read is an observed address (plus the new rows), the cores equal the shim/OpenBSD walk */
	memset(&f, 0, sizeof f);
	assert(swl_stage1(&b, &c, 2) == 0);
	assert(f.refused == 0 && f.nerom >= 218);
	total = f.nrd;
	assert(SWL_CHIPID_ID(c.chipid) == 0x4389 && SWL_CHIPID_REV(c.chipid) == 3 && c.cc_caps == 0x10000000u && c.caps_ext == 0x000A044Eu);
	assert(c.ncores == sizeof want_cores / sizeof want_cores[0]);
	for (i = 0; i < c.ncores; i++)
		assert(c.cores[i].id == want_cores[i].id && c.cores[i].rev == want_cores[i].rev && c.cores[i].base == want_cores[i].base && c.cores[i].wrap == want_cores[i].wrap);
	for (i = 0; i < NOBS; i++)
		assert(f.seen[i] == 1);

	/* level 1 never reads a D11 wrapper */
	memset(&f, 0, sizeof f);
	assert(swl_stage1(&b, &c, 1) == 0);
	for (i = 0; i < NOBS; i++)
		assert(f.seen[i] == (obs[i].a >= 0x18121000u && obs[i].a < 0x18124000u ? 0u : 1u));

	/* a read that fails anywhere stops the run (every position) */
	for (unsigned k = 1; k <= total; k++) {
		memset(&f, 0, sizeof f);
		f.fail_at = k;
		assert(swl_stage1(&b, &c, 2) == -1);
		assert(f.nrd == k - 1);
	}

	/* an EROM without an EOT cannot make the walk run away: the allowlist ends it inside the EROM page */
	memset(&f, 0, sizeof f);
	f.erom_zero = 1;
	assert(swl_stage1(&b, &c, 2) == -1);
	assert(f.nerom <= SWL_EROM_LIMIT / 4 && f.refused == 1);

	/* more components than the core table holds: refused, not written past the table */
	{
		static uint32_t big[4 * 40 + 1];
		unsigned n;

		for (n = 0; n < 40; n++) {
			big[4 * n + 0] = 0x00000001u | (0x812u << SWL_COMP_PARTNUM_S);
			big[4 * n + 1] = 0x00000001u | (1u << SWL_COMP_NMWRAP_S);
			big[4 * n + 2] = (0x18200000u + n * 0x1000u) | SWL_DESC_ADDRESS;
			big[4 * n + 3] = (0x18300000u + n * 0x1000u) | SWL_DESC_ADDRESS | (SWL_SL_TYPE_SWRAP << SWL_SL_TYPE_S);
		}
		big[4 * 40] = SWL_DESC_EOT;
		memset(&f, 0, sizeof f);
		f.alt = big;
		f.nalt = 4 * 40 + 1;
		memset(&c, 0, sizeof c);
		assert(swl_stage1(&b, &c, 2) == -1 && c.ncores == SWL_MAXCORES);
	}

	/* the allowlist: exact count (deny by default) and the write-side offsets of the wrappers / CC never pass */
	assert(allowed_count() == (int)(SWL_EROM_LIMIT / 4) + 5 + 5 + 5 * 3);
	assert(!swl_rd_ok(0x18120000u, 0x400) && !swl_rd_ok(0x18120000u, 0x404) && !swl_rd_ok(0x18120000u, 0x500) && !swl_rd_ok(0x18120000u, 0x808) &&
	       !swl_rd_ok(0x18120000u, 0x80C) && !swl_rd_ok(0x18105000u, 0xE00) && !swl_rd_ok(0x18000000u, 0x80) && !swl_rd_ok(0x18000000u, 0x1E8) &&
	       !swl_rd_ok(0x18001000u, 0x1E8) && !swl_rd_ok(0x18124000u, 0x408) && !swl_rd_ok(0x18105000u, 0x2));
	printf("RESULT: PASS wlan_xnu_host_test\n");
	return 0;
}
