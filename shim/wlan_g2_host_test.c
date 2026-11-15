/*
 * Host test of the G2 shared-structure writer in xnu patch 0174 (sweet_wlan_g2.h, extracted from the patch by shim/build.sh). The fake dongle starts from what run F3 left
 * (R89 s.1 [obs]: shared 0x3ced34, ring_info 0x3b1b6c, ring_mem 0x3b1bb8, TCM index arrays 0x407800 / 0x207f40 / 0x3e7d80 / 0x47cf40, EP Command 0x00100002, RC Command
 * 0x00100000, SRPWR 0x30300, RAM end 0x4A0000) and the live G1 plan (a block at 0x884e84000, hi dword 8). The fake enforces the BAR2 window: a backplane access outside
 * the window cfg 0x84 selects fails, like the hardware maps another 4 MiB there. The expected stores are LITERAL addresses (want[] below), not taken from swg_g2[], so a
 * change of the table order shows here. Each rule of the header has a case that fails when the rule is removed (mutants: tools/test_wlan_wr_mutants.py --g2). No hardware.
 */
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static unsigned fails;
#ifndef SWG_FAILFAST
#define SWG_FAILFAST 0
#endif
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; if (SWG_FAILFAST) { fprintf(stderr, "RESULT: FAIL wlan_g2_host_test (fail-fast)\n"); exit(1); } } } while (0)

static char logbuf[1 << 21];
static unsigned loglen;
static int cap_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int cap_printf(const char *fmt, ...)
{
	va_list ap;
	int n;
	va_start(ap, fmt);
	n = vsnprintf(logbuf + loglen, sizeof logbuf - loglen, fmt, ap);
	va_end(ap);
	if (n > 0 && loglen + (unsigned)n < sizeof logbuf)
		loglen += (unsigned)n;
	return n;
}
#define printf cap_printf
#include "sweet_wlan_g2.h"
#define SWJ_LOG(str) cap_printf("%s\n", (str))
#define SWJ_GAP(str) cap_printf("%s\n", (str))
#define SWJ_RANDOM(buf, len) memset((buf), 0x42, (len))
#include "sweet_wlan_join.h"        /* L24: the reviewed join payloads (swj_join_cmds) the engine must send byte for byte */
#undef printf

#define SH   0x3ced34u
#define RI   0x3b1b6cu
#define RM   0x3b1bb8u
#define I_HW 0x407800u          /* h2d_w (tail window) */
#define I_HR 0x207f40u          /* h2d_r */
#define I_DW 0x3e7d80u          /* d2h_w */
#define I_DR 0x47cf40u          /* d2h_r (tail window) */
#define END  0x4A0000u          /* SWW_RAM_BASE + 0x2A0000 (21 banks, run F3) */
#define PA   0x884e84000ull
#define RAMW (0x600000u / 4u)
#define MBH  0x448688u          /* the h2d mailbox DATA word (shared+0x28 holds this pointer), tail window */
#define CON  0x3f0000u          /* hnd_cons_t (shared+0x14 holds this pointer): buffer, size, write index at +8/+12/+16; window 0 */
#define CBUF 0x3f0100u
#define MBP  0x44868cu          /* the d2h mailbox DATA word: shared+0x2C holds this POINTER (W-D4a s.0 [obs] run G2: 0x44868c, in the tail window) */

enum { F_OK, F_RC_START, F_EP, F_SRPWR, F_FLAGS, F_VERSION, F_SH_OUT, F_RI_OVL_SH, F_RI_OVL_LO, F_RI_OVL_HI, F_MAXSUB, F_MAXCPL, F_RINGMEM, F_RM_NONZERO, F_IDXPTR, F_IDXEND, F_HOSTADDR,
       F_FLAGS2, F_HOSTCAP, F_SCRLEN, F_SCRLO, F_TRAP, F_CMD_RDFAIL,
       F_EP_RDFAIL, F_SRPWR_RDFAIL, F_SRPWR_ST, F_FLAGS_ASSERT, F_SH_END, F_IDXEND2, F_IDXALIGN, F_OVL_RING, F_OVL_SH, F_OVL_RI, F_OVL_IDX, F_RM_END, F_SCRHI, F_TRAPLO, F_MBP_ALIGN, F_MBP_OOR, F_MBP_LOW,       /* last entry case: F_MBP_LOW */
       F_NOWRITE, F_LATE, F_LATE2, F_SLOTDROP, F_RC_STUCK, F_EP_STUCK, F_ONES, F_RDFAIL, F_WINFAIL, F_FATAL, F_NOTAKE, F_UNDOFAIL,
       F_RC_LATE, F_EP_LATE, F_READY_OLD, F_READY_ONES, F_EP_WRFAIL, F_RC_WRFAIL, F_RC_UNDOFAIL, F_POLL_DEAD, F_FATAL_TRAP, F_POLL_RDFAIL, F_TAKE4,
       F_RBFAIL, F_RESTOREFAIL, F_VDEVS, F_IDXBASE, F_IDXEND3, F_WINEDGE, F_OVL_EDGE,
       F_CH_FL, F_CH_MB, F_CH_DW0, F_CH_DW1, F_CH_DW2, F_CH_C30, F_FWHALT, F_C30_RDFAIL, F_POLL_RDFAIL2, F_C30_ONES, F_MB_ONES, F_DW_ONES, F_BASE_ONES, F_N };

/* the cases that must stop at the entry, before any store */
#define ENTRY_CASE(k) ((k) >= F_RC_START && (k) <= F_MBP_LOW)

struct rec { int sp; uint32_t a, v; };

/* W-D4b: the fake host block (the G1 block, 11 x 16 KiB) and the dongle's answer to the ring-0 post. d4kind picks the failure of one case; want_d4b is the sweetwlan=13 gate */
enum { D_OK, D_NODONGLE, D_BADEPOCH, D_BADXOR, D_MASK_OLD, D_MASK_RB, D_ITEM_WRFAIL, D_ITEM_RB, D_ITEM_OLD, D_SLOT_WRFAIL, D_SLOT_OLD, D_DB0_WRFAIL, D_UNMASK_FAIL, D_UNMASK_WRFAIL,
       D_FWHALT, D_C30_ONES, D_TRAP, D_BASE_ONES, D_POLL_HRDFAIL, D_RO_HOST, D_RO_TCM_ITEM, D_RO_BADEPOCH, D_MASK_DEAD, D_POLL_D2HW_ONES,
       D_C30_ONLY, D_D2HW_ZERO, D_H2DR_ONLY, D_RO_HRDFAIL, D_S16_WRFAIL, D_S16_DROP, D_S16_WIDE, D_MASK_WRFAIL, D_PRE_ITEM, D_PRE_WRAP, D_PRE_NOANS, D_MB, D_N };
static int d4kind, want_d4b;
static uint32_t rd_fail_host_a[8], rd_fail_host_n;   /* 2026-11-11: host-space READS of these word offsets fail (the extra bss record read of a keep-phase ESCAN_RESULT); n = 0: none */
static uint32_t mb_type, mb_mb, mb_epoch, mb_dw, wr_fail_host_a;   /* D_MB (W-D4c): the item 0 the fake dongle pre-writes at hostready (type, d2h_mailbox_data, epoch byte, D2H-WR slot 0); a host store to wr_fail_host_a fails (0 = none) */
static int mb_xorbad, mb_after;   /* mb_after: what the dongle does when the deassert doorbell (0xFF000004) rings: 2 FWHALT, 3 TRAP, 5 h2d_r[0] all ones; mb_tamper: a value the dongle writes over the H2D-WR slot after the L14 doorbell (0 = none) */
static int mb_note, mb_nxor, mb_nlate, mb_f4, mb_f4_n;   /* W-D4c exit note: mb_note 1 = the dongle answers the wake doorbell 0xFF000003 with ring-2 item 1 + D2H-WR slot (mb_ntype/mb_nmb/mb_nepoch/mb_ndw), 2 = h2d_r[0] reads all ones instead, 3 = the D2H-WR slot does; mb_nlate = the note lands at the n-th delay after the wake doorbell
                                                  * (0 = at the doorbell); mb_f4: 1 TRAP, 2 FWHALT, 3 / 4 / 5 item 0 word 1 / 5 / 0 changed, at the pre-answer re-read (review d4c r1 F4) */
static uint32_t mb_twk_db, mb_twk_off, mb_twk_val;     /* at the doorbell mb_twk_db the dongle (or the host) writes mb_twk_val over the host block word mb_twk_off (0 = none) */
static uint32_t mb_ntype = 0x24u, mb_nmb = 0x4u, mb_nepoch = 2u, mb_ndw = 2u;
static int mb_dead_d2hw, mb_d2hw_last;      /* review d4c r1 F5: D2H-WR slot 0 reads all ones from the 2nd read after the L14 poll's last wait on (= swg_d4c's pre-answer re-read, not the poll) */
static uint32_t mb_tamper, mb_dirty_a, mb_late_dw;     /* mb_dirty_a: a host block word that is 5 before the run (a row's old value must be 0); mb_late_dw: D2H-WR slot 0 the dongle writes at the 2nd wait of the L14 poll */
static uint32_t wr_corrupt_host_a, wr_corrupt_v, mb_dirty_v = 5u;     /* a host store of wr_corrupt_v to wr_corrupt_host_a lands with its low bit flipped (the read-back differs); mb_dirty_v = the value mb_dirty_a gets */
static uint32_t rd_fail_bp;     /* a TCM read of this backplane address fails (0 = none) */
static uint32_t wr_fail_zero_a;     /* a host store of 0 to this host block address fails, a store of any other value lands (0 = none; T-4e39: the scrub of a secret payload) */
#define HBW (311296u / 4u)           /* the G1 block of the run (W-D4e: 8 + 8 buffers): 307328 B planned -> 19 pages of 16 KiB */
#define NREC 8192u     /* review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 2: the cap case makes 1321 stores (NREC 256 truncated every L22/L23 case, AP-371) */

struct fake {
	uint32_t ram[RAMW];
	uint32_t win2, rc, ep, srpwr;
	unsigned nw, nready, ntcm, nrd, nep, nrc, ndelay, msdelay, nrdhr, dropped;
	uint32_t c30;
	unsigned c30_pre, c30_post, mb_pre, mb_post, nw_c30;    /* W-D4a: reads of 0xC30 / the mailbox data before and after the hostready store; stores made at the first 0xC30 read */
	struct rec w[NREC];
	int kind;
	uint32_t hb[HBW];               /* W-D4b host block words */
	uint32_t c34, c140;
	unsigned ndbrd, ndbwr, ndbwake, ndbdeas, ndbrd2;          /* W-D4c doorbells seen: 0xDD020001 (C1), 0xFF000002 (A1 ack), 0xFF000003 (wake), 0xFF000004 (deassert), 0xDD020002 (C2) */
	int awin, late_note;            /* awin: between the ack doorbell and the deassert doorbell / the unmask: nrdwin / nwrwin count every non-host read / non-doorbell write inside (the handshake touches host memory only); late_note: delays left before the note lands */
	unsigned nrdwin, nwrwin;
	unsigned npoll_d2hw, nh_after_c2, nh_deas_c2;       /* host reads of the D2H-WR slot between the wake and the deassert doorbell (= exit-note poll reads, one per poll); host reads after the C2 doorbell (the final read); nh_deas_c2: between the deassert and the C2 doorbell */
	unsigned nhrd, ndb0, nunm, late;        /* late: D_PRE_ITEM, the post was seen, the reply is written at the 2nd wait */
	unsigned nc88w, nc88r, nrd140, n16, n16rec;     /* cfg 0x88 stores / reads seen (G2 must make none: SPEC-WIFI D-12), reads of the PCIe core doorbell 0x140 (review r1 F6: none), 16-bit stores seen, 16-bit stores recorded */
};
static const struct swd_plan *fplan;     /* the plan the host-block offsets of the fake come from */

static struct fake f;
static uint32_t tpr[3];            /* T-c3ac: what ep cfg 0xBC, 0x100, 0x104 read */
static uint32_t tpr_fail;          /* 1 + the offset whose read is refused, 0 = none */
static unsigned npr_rd[3];         /* reads of the three probe registers */
static unsigned tdb_defer, tdb_left, tdb_pend;   /* T-c3ac: the first L24 doorbell is held for tdb_defer host delays before the fake dongle sees it (the real dongle took ~107 ms, T-5323) */
static int tdb_seen;
static int tspin_ones, tspin_on;   /* T-c3ac: the host d2h_w word reads all ones once the L24 spin has run its first 1 ms step */

/* W-D3c: the layout of run F3 (SH, RI) moved to sh / ri: the shared block [SH, +0x60) and ring_info + ring_mem [RI, +0x9C) are carried over, the two pointers rewritten, nothing else moves (the index arrays keep their places) */
static uint32_t ov_sh, ov_ri;
/* review 0174 W-D4a r1 F2 / AP-239: the layout with every polled word on the tail window (shared + ring_info as the a3 layout, the d2h_w array and the mailbox data above 0x400000), so a stop in the poll or the baseline leaves cfg 0x84 on the tail and the restore has work to do */
static int tail_all;
#define SHA  (ov_sh != 0u ? ov_sh : SH)
#define I_DWT 0x420000u

static void
reloc(uint32_t *dst, const uint32_t *src, uint32_t sh, uint32_t ri)
{
	static uint32_t shb[0x60 / 4], rib[0x9C / 4];
	memcpy(shb, &src[SH / 4u], sizeof shb);
	memcpy(rib, &src[RI / 4u], sizeof rib);
	if (dst != src) {
		memcpy(dst, src, sizeof f.ram);
	}
	memset(&dst[SH / 4u], 0, sizeof shb);
	memset(&dst[RI / 4u], 0, sizeof rib);
	memcpy(&dst[sh / 4u], shb, sizeof shb);
	memcpy(&dst[ri / 4u], rib, sizeof rib);
	dst[(sh + 0x30u) / 4u] = ri;
	dst[ri / 4u] = ri + 0x4Cu;
}

/* ring_info (the 0x4C bytes at RI, with the fresh ring_mem zeros) copied to ri, the two pointers made to match */
static void
put_ri(uint32_t ri)
{
	memcpy(&f.ram[ri / 4u], &f.ram[RI / 4u], 0x4Cu);
	f.ram[ri / 4u] = ri + 0x4Cu;
	f.ram[(SH + 0x30u) / 4u] = ri;
}

static void
fw_init(int kind)
{
	memset(&f, 0, sizeof f);
	f.kind = kind;
	f.ram[SH / 4u] = 0x70050107u;
	f.ram[(SH + 0x30u) / 4u] = RI;
	f.ram[(SH + 0x2Cu) / 4u] = MBP;                 /* the POINTER; the data word at MBP is 0 */
	f.ram[(SH + 0x28u) / 4u] = MBH;
	f.ram[(SH + 0x14u) / 4u] = CON;
	f.ram[(CON + 8u) / 4u] = CBUF;
	f.ram[(CON + 12u) / 4u] = 64u;
	f.ram[(CON + 16u) / 4u] = 23u;                  /* the text below is the 23 bytes before the write index; the other 41 bytes are zeros */
	memcpy(&f.ram[CBUF / 4u], "RTE 1\nwl0: ok\r\n\x01x\x7f\nend\n", 23u);
	f.ram[(SH + 0x50u) / 4u] = 0x40943041u;
	f.ram[RI / 4u] = RM;
	f.ram[(RI + 0x04u) / 4u] = I_HW;
	f.ram[(RI + 0x08u) / 4u] = I_HR;
	f.ram[(RI + 0x0Cu) / 4u] = I_DW;
	f.ram[(RI + 0x10u) / 4u] = I_DR;
	f.ram[(RI + 0x34u) / 4u] = 0x002b0028u;         /* max_tx_flowrings 0x28 | max_submission_queues 0x2b << 16 [obs] */
	f.ram[(RI + 0x38u) / 4u] = 4u;                  /* max_completion_rings 4, max_vdevs 0 [obs] */
	f.ram[I_HW / 4u] = 0x00010003u;                 /* slot contents are unknown (U9): a non-zero upper half must be kept */
	f.ram[I_DR / 4u + 2u] = 0xBEEF0000u;
	f.rc = 0x00100000u;
	f.ep = 0x00100002u;
	f.srpwr = 0x00030300u;
	switch (kind) {
	case F_RC_START:   f.rc = 0x00100507u; break;
	case F_EP:         f.ep = 0x00100006u; break;
	case F_SRPWR:      f.srpwr = 0x00030100u; break;
	case F_FLAGS:      f.ram[SH / 4u] |= 0x400u; break;
	case F_VERSION:    f.ram[SH / 4u] = 0x70050106u; break;
	case F_RI_OVL_SH:  /* W-D3c: ring_info + ring_mem overlapping the shared structure (ri = sh + 0x20); every word the entry reads before the apart check is well-formed, so only that check refuses it */
		put_ri(SH + 0x20u);
		break;
	case F_RI_OVL_LO:  /* the block [ri, ri + 0x9C) ends 4 bytes into the shared structure */
		put_ri(SH + 4u - 0x9Cu);
		break;
	case F_RI_OVL_HI:  /* the block starts at the shared structure's last word */
		put_ri(SH + 0x60u - 4u);
		break;
	case F_RM_END:     /* W-D3c: ring_info 0x60 below the RAM end: ring_info and ringmem_ptr read fine, the last ring_mem word (ri + 0x98) is beyond the end */
		put_ri(END - 0x60u);
		break;
	case F_MAXSUB:     f.ram[(RI + 0x34u) / 4u] = 0x002a0028u; break;
	case F_MAXCPL:     f.ram[(RI + 0x38u) / 4u] = 5u; break;
	case F_RINGMEM:    f.ram[RI / 4u] = RM + 4u; break;
	case F_RM_NONZERO: f.ram[(RM + 0x4Cu) / 4u] = 0x80000000u; break;
	case F_IDXPTR:     f.ram[(RI + 0x0Cu) / 4u] = 0x1ffffcu; break;     /* below the RAM, its last slot inside */
	case F_IDXEND:     f.ram[(RI + 0x0Cu) / 4u] = END - 8u; break;      /* inside the RAM, its 4th slot beyond the end */
	case F_HOSTADDR:   f.ram[(RI + 0x30u) / 4u] = 8u; break;
	case F_FLAGS2:     f.ram[(SH + 0x50u) / 4u] ^= 2u; break;
	case F_HOSTCAP:    f.ram[(SH + 0x54u) / 4u] = 0x1407u; break;
	case F_SCRLEN:     f.ram[(SH + 0x34u) / 4u] = 128u; break;
	case F_SCRLO:      f.ram[(SH + 0x38u) / 4u] = 0x1000u; break;
	case F_TRAP:       f.ram[(SH + 0x5Cu) / 4u] = 8u; break;
	case F_TRAPLO:     f.ram[(SH + 0x58u) / 4u] = 8u; break;
	case F_MBP_ALIGN:  f.ram[(SH + 0x2Cu) / 4u] = MBP + 2u; break;  /* W-D4a: not 4-aligned */
	case F_MBP_OOR:    f.ram[(SH + 0x2Cu) / 4u] = END; break;       /* first word beyond the RAM */
	case F_MBP_LOW:    f.ram[(SH + 0x2Cu) / 4u] = 0x1ffffcu; break; /* below the RAM base (also what a firmware that never set it would hold: 0 is refused the same way) */
	case F_SCRHI:      f.ram[(SH + 0x3Cu) / 4u] = 8u; break;
	case F_SRPWR_ST:   f.srpwr = 0x00000300u; break;        /* request bit set, status bit clear */
	case F_FLAGS_ASSERT: f.ram[SH / 4u] |= 0x200u; break;
	case F_SH_END:     break;       /* mk() puts the shared structure 0x30 below the RAM end: ring_info would be the first word beyond it */
	case F_IDXEND2:    f.ram[(RI + 0x0Cu) / 4u] = END - 12u; break;     /* its 4th slot is the first word beyond the end */
	case F_IDXEND3:    f.ram[(RI + 0x0Cu) / 4u] = END - 16u; break;     /* its 4th slot is the last RAM word: allowed */
	case F_IDXALIGN:   f.ram[(RI + 0x0Cu) / 4u] = I_DW + 2u; break;
	case F_OVL_RING:   f.ram[(RI + 0x0Cu) / 4u] = RM + 0x08u; break;   /* review r1 F2: d2h_w slot 0 = ring 0's base lo: the slot store would clear it */
	case F_OVL_SH:     f.ram[(RI + 0x0Cu) / 4u] = SH + 0x50u; break;   /* on flags2 */
	case F_OVL_RI:     f.ram[(RI + 0x0Cu) / 4u] = RI + 0x14u; break;   /* on the h2d_w host address */
	case F_OVL_IDX:    f.ram[(RI + 0x0Cu) / 4u] = I_HR + 0x0Cu; break; /* its slot 0 is h2d_r's slot 3 */
	case F_OVL_EDGE:   f.ram[(RI + 0x0Cu) / 4u] = RM + 0x50u; break;   /* the first word after ring_mem: touching, not overlapping, allowed */
	case F_IDXBASE:    f.ram[(RI + 0x0Cu) / 4u] = SWW_RAM_BASE; break;  /* the first RAM word: allowed */
	case F_WINEDGE:    f.ram[(RI + 0x10u) / 4u] = SWW_WIN_TAIL - 4u; break;     /* slot 0 in window 0, slots 1 and 2 in the tail window */
	case F_VDEVS:      f.ram[(RI + 0x38u) / 4u] = 0x00030004u; break;   /* max_vdevs (the high half) is not G2's business */
	case F_BASE_ONES:  f.c30 = 0xFFFFFFFFu; break;   /* the baseline read of 0xC30 is all ones */
	default:           break;
	}
	if (ov_sh != 0u) {      /* W-D3c: the default layout moved (the shared structure and ring_info + ring_mem blocks only), reloc() */
		reloc(f.ram, f.ram, ov_sh, ov_ri);
	}
	if (tail_all) {
		f.ram[((ov_ri != 0u ? ov_ri : RI) + 0x0Cu) / 4u] = I_DWT;
	}
	loglen = 0;
	logbuf[0] = 0;
}

/* the backplane word bp through the BAR2 window cfg 0x84 selects (4 MiB), NULL when the window does not map it */
static uint32_t *
tcm(uint32_t bp)
{
	if ((bp & 3u) != 0u || bp < f.win2 || bp - f.win2 >= SWW_TCM_BYTES || bp / 4u >= RAMW) {
		return NULL;
	}
	return &f.ram[bp / 4u];
}

static int
b_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	uint32_t *p;
	(void)ctx;
	f.nrd++;
	if (f.awin && sp != SWG_SP_HOST) {
		f.nrdwin++;
	}
	switch (sp) {
	case SWG_SP_TCM:
		if (rd_fail_bp != 0u && a == rd_fail_bp) {
			return -1;
		}
		if (f.kind == F_RDFAIL && f.nw > 0u && a == RM + 0x08u) {        /* the old-value read of ring 0's base fails */
			return -1;
		}
		if (f.kind == F_RBFAIL && a == I_HR && ++f.nrdhr == 2u) {        /* the read-back of the h2d_r slot (the 2nd read of it) fails */
			return -1;
		}
		if (a == MBP) {
			if (f.nready == 0u) {
				f.mb_pre++;
			} else {
				f.mb_post++;
			}
		}
		if (((f.kind == F_POLL_RDFAIL && f.mb_post == 1u) || (f.kind == F_POLL_RDFAIL2 && f.mb_post == 2u)) && f.nready > 0u && a == MBP) {      /* the d2h mailbox DATA read of the poll fails: the 1st (window on the tail, read first) or the 2nd (window on 0, read last) */
			return -1;
		}
		if (f.kind == F_POLL_DEAD && f.nready > 0u && a == SHA) {
			*v = 0xFFFFFFFFu;
			return 0;
		}
		if (f.kind == F_LATE2 && a == SH + 0x5Cu) {
			f.ram[(SH + 0x50u) / 4u] ^= 1u;         /* flags2 changes after the entry read it, before its row */
		}
		if (f.kind == F_ONES && f.nw > 0u && a == RM + 0x24u) {
			*v = 0xFFFFFFFFu;
			return 0;
		}
		p = tcm(a);
		if (p == NULL) {
			return -1;
		}
		*v = *p;
		return 0;
	case SWG_SP_EP:
		if (a == 0x88u) {       /* G2 never touches cfg 0x88 (SPEC-WIFI D-12, row W15 of the wr header does): counted, refused */
			f.nc88r++;
			return -1;
		}
		if (a == 0xBCu || a == 0x100u || a == 0x104u) {     /* T-c3ac: the L24 probe registers (LNKSTA, AER header, AER UESta); tpr_fail = 1 + the register to refuse (0 = none) */
			npr_rd[a == 0xBCu ? 0 : a == 0x100u ? 1 : 2]++;
			if (tpr_fail == 1u + a) {
				return -1;
			}
			*v = tpr[a == 0xBCu ? 0 : a == 0x100u ? 1 : 2];
			return 0;
		}
		if (a != 0x04u || f.kind == F_EP_RDFAIL) {
			return -1;
		}
		*v = f.ep;
		return 0;
	case SWG_SP_RC:
		if (a != 0x04u || f.kind == F_CMD_RDFAIL) {
			return -1;
		}
		*v = f.rc;
		return 0;
	case SWG_SP_HOST:
		for (uint32_t rk = 0; rk < rd_fail_host_n; rk++) {
			if (a == rd_fail_host_a[rk]) {
				return -1;
			}
		}
		f.nhrd++;
		f.nh_after_c2 += f.ndbrd2 > 0u;
		f.nh_deas_c2 += f.ndbdeas > 0u && f.ndbrd2 == 0u;
		f.npoll_d2hw += f.ndbwake > 0u && f.ndbdeas == 0u && fplan != NULL && a == fplan->b[SWD_IDX_D2H_W].off;
		if (tspin_on && fplan != NULL && a == fplan->b[SWD_IDX_D2H_W].off) {
			*v = 0xFFFFFFFFu;
			return 0;
		}
		if (d4kind == D_RO_HRDFAIL) {
			return -1;
		}
		if (a >= HBW * 4u || (a & 3u) != 0u || (d4kind == D_POLL_HRDFAIL && f.ndb0 > 0u)) {
			return -1;
		}
		if (d4kind == D_MB && mb_dead_d2hw && f.ndb0 > 0u && f.ndelay == SWG_POLLS - 1u && fplan != NULL && a == fplan->b[SWD_IDX_D2H_W].off && ++mb_d2hw_last >= 2) {
			*v = 0xFFFFFFFFu;
			return 0;
		}
		if (d4kind == D_MB && mb_f4 && f.ndb0 > 0u && f.ndelay == SWG_POLLS - 1u && fplan != NULL && a == fplan->b[SWD_IDX_D2H_W].off && ++mb_f4_n == 1) {      /* the L14 poll's last D2H-WR read: the dongle changes something right after it, so the pre-answer re-read (the next one) sees it */
			*v = f.hb[a / 4u];
			if (mb_f4 == 1) {
				f.ram[SH / 4u] |= 0x400u;
			} else if (mb_f4 == 2) {
				f.ram[MBP / 4u] = 0x10000000u;
			} else if (mb_f4 == 3) {
				f.hb[fplan->b[SWD_D2H_CTRL].off / 4u + 1u] = 0x5u;      /* item 0 word 1 changes */
			} else if (mb_f4 == 4) {
				f.hb[fplan->b[SWD_D2H_CTRL].off / 4u + 5u] ^= 0x10u;    /* word 5 (the XOR word) changes */
			} else {
				f.hb[fplan->b[SWD_D2H_CTRL].off / 4u] ^= 0x10u;         /* word 0 changes */
			}
			return 0;
		}
		if (d4kind == D_BASE_ONES && f.nw > 0u && a == fplan->b[SWD_IDX_H2D_R].off) {
			*v = 0xFFFFFFFFu;
			return 0;
		}
		if (d4kind == D_ITEM_RB && a == fplan->b[SWD_H2D_CTRL].off + 8u && f.hb[a / 4u] != 0u) {
			*v = f.hb[a / 4u] ^ 1u;         /* the read-back of item word 2 differs */
			return 0;
		}
		*v = f.hb[a / 4u];
		return 0;
	case SWG_SP_PCIE:
		if (a == 0xC34u || a == 0x140u) {
			if (want_d4b < 2) {
				return -1;      /* G2 alone never touches them */
			}
			if (a == 0xC34u) {
				*v = d4kind == D_MASK_RB && f.c34 == 0xFFFFu ? 0x00FFu : f.c34;
			} else {
				f.nrd140++;     /* review r1 F6: the golden never reads the doorbell */
				*v = 0;         /* a doorbell reads back 0 */
			}
			return 0;
		}
		if (a == 0x1E8u && f.kind != F_SRPWR_RDFAIL) {
			*v = f.srpwr;
			return 0;
		}
		if (a == 0xC30u && f.kind != F_C30_RDFAIL) {
			if (d4kind == D_C30_ONES && f.ndb0 > 0u) {
				*v = 0xFFFFFFFFu;
				return 0;
			}
			if (f.nready == 0u) {
				if (f.c30_pre++ == 0u) {
					f.nw_c30 = f.nw;
				}
			} else {
				f.c30_post++;
			}
			*v = f.c30;
			return 0;
		}
		if (a == 0x144u) {
			*v = f.kind == F_READY_OLD ? 0xA5A5A5A5u : f.kind == F_READY_ONES ? 0xFFFFFFFFu : 0u;     /* a doorbell: any value is fine */
			return 0;
		}
		return -1;
	}
	return -1;
}

/* the dongle's answer to the wake: ring-2 item 1 (a DS_EXIT_NOTE as DHD sees it, flags 0x80) and the D2H-WR slot (bcmmsgbuf.h:900-907: d2h_mailbox_data u32 at byte 12) */
static void
put_note(void)
{
	uint32_t *r2 = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u + 6u];
	r2[0] = mb_nepoch << 24 | 0x80u << 16 | mb_ntype;
	r2[3] = mb_nmb;
	r2[5] = r2[0] ^ r2[3] ^ (mb_nxor ? 1u : 0u);
	f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = mb_ndw;
}

/* W-D4d: the fake dongle's answers to the ioctl requests. io_mode picks what it does at the doorbell of request io_req (1 = WLC_GET_VERSION at ring-0 index 5, doorbell 0xFF000006; 2 = GET_VAR cap at index 7, doorbell 0xFF000008);
 * the other request is always answered with ack + completion (IO_BOTH). io_l17 = what it does at the response-buffer post doorbell 0xFF000005: 0 nothing, 1 an ack item, 2 a D2H_MAILBOX_DATA item at ring-2 index 2. */
enum { IO_NONE, IO_BOTH, IO_ONEBY, IO_ACKONLY, IO_CMPLTONLY, IO_ACK_STATUS, IO_CM_STATUS, IO_ACK_CMD, IO_CM_CMD, IO_CM_TRANS, IO_CM_RID, IO_ACK_RID, IO_EPOCH, IO_XOR, IO_BIGLEN, IO_T24, IO_TODD, IO_THIRD, IO_ZEROLEN, IO_DUPACK, IO_FULL, IO_LOWW, IO_MANY, IO_UPPER, IO_N };
static int io_mode, io_req, io_l17;
static unsigned io_late, io_n, ndb_d4d;
static uint32_t io_ver = 0x0B0A0504u, io_db[24];
static const char *io_cap = " 802.11 sup_wpa idsup wme ";
static int io_nocap;    /* 1 = the fake dongle answers the cap request but leaves response buffer word 0 as it was (no DMA write) */
static unsigned io_caplen;      /* the cap response length (0 = strlen + 1) */

/* one 24 B ring-2 item at index k, little endian, the XOR word fixed to make all six words XOR to xorfix */
static void
io_put(uint32_t k, uint32_t type, uint32_t rid, int status, uint32_t cmd, uint32_t trans, uint32_t resp_len, uint32_t epoch, uint32_t xorfix)
{
	uint32_t *r = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u + 6u * k];
	r[0] = epoch << 24 | 0x80u << 16 | type;
	r[1] = rid;
	r[2] = (uint32_t)(uint16_t)status | 2u << 16;
	if (type == 0xCu) {
		r[3] = resp_len | trans << 16;
		r[4] = cmd;
	} else {
		r[3] = type == 0xAu ? cmd : 2u;
		r[4] = 0u;
	}
	r[5] = r[0] ^ r[1] ^ r[2] ^ r[3] ^ r[4] ^ xorfix;
}

static void
io_second(void)
{
	uint32_t k0 = io_n == 1u ? 2u : 4u, cmd = io_n == 1u ? 1u : 262u, len = io_n == 1u ? 4u : io_caplen != 0u ? io_caplen : (uint32_t)strlen(io_cap) + 1u;
	uint32_t *rsp = &f.hb[fplan->b[SWD_RESPBUF].off / 4u];
	if (io_n == 1u) {
		rsp[0] = io_ver;
	} else if (!io_nocap) {
		memcpy(rsp, io_cap, len);
	}
	io_put(k0 + 1u, 0xCu, 1u + io_n, 0, cmd, 0x100u + io_n, len, (254u + k0 + 1u) % 253u, 0u);
	f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = k0 + 2u;
}

static void
io_answer(unsigned n)
{
	const uint32_t k0 = n == 1u ? 2u : 4u, cmd = n == 1u ? 1u : 262u, e0 = (254u + k0) % 253u, e1 = (254u + k0 + 1u) % 253u;
	const uint32_t trans = 0x100u + n, rid = 1u + n;
	const uint32_t len = n == 1u ? 4u : io_caplen != 0u ? io_caplen : (uint32_t)strlen(io_cap) + 1u;
	uint32_t *rsp = &f.hb[fplan->b[SWD_RESPBUF].off / 4u], *d2hw = &f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u];
	int m = io_req == (int)n || io_req == 0 ? io_mode : IO_BOTH;
	f.hb[fplan->b[SWD_IDX_H2D_R].off / 4u] = 4u + n * 2u;       /* the dongle consumed the request */
	io_n = n;
	if (n == 1u) {
		rsp[0] = io_ver;
	} else if (!io_nocap) {
		memcpy(rsp, io_cap, len);
	}
	switch (m) {
	case IO_NONE:
		return;
	case IO_LOWW:
		*d2hw = 1u;
		return;
	case IO_UPPER:
		io_put(k0, 0xAu, 0xFFFEu, 0, cmd, 0u, 0u, e0, 0u);
		io_put(k0 + 1u, 0xCu, rid, 0, cmd, trans, len, e1, 0u);
		*d2hw = (k0 + 2u) | 0x10000u;
		return;
	case IO_DUPACK:
		io_put(k0, 0xAu, 0xFFFEu, 0, cmd, 0u, 0u, e0, 0u);
		io_put(k0 + 1u, 0xAu, 0xFFFEu, 0, cmd, 0u, 0u, e1, 0u);
		*d2hw = k0 + 2u;
		return;
	case IO_CMPLTONLY:
		io_put(k0, 0xCu, rid, 0, cmd, trans, len, e0, 0u);
		*d2hw = k0 + 1u;
		return;
	case IO_ONEBY:
		io_put(k0, 0xAu, 0xFFFEu, 0, cmd, 0u, 0u, e0, 0u);
		*d2hw = k0 + 1u;
		io_late = 1u;
		return;
	default:
		break;
	}
	io_put(k0, 0xAu, m == IO_ACK_RID ? 0xFFFDu : 0xFFFEu, m == IO_ACK_STATUS ? -1 : 0, m == IO_ACK_CMD ? cmd + 1u : cmd, 0u, 0u, e0, 0u);
	if (m == IO_ACKONLY) {
		*d2hw = k0 + 1u;
		return;
	}
	if (m == IO_T24) {
		io_put(k0 + 1u, 0x24u, 0u, 0, 0u, 0u, 0u, e1, 0u);
	} else if (m == IO_TODD) {
		io_put(k0 + 1u, 0x10u, 0u, 0, 0u, 0u, 0u, e1, 0u);
	} else {
		io_put(k0 + 1u, 0xCu, m == IO_CM_RID ? rid + 1u : rid, m == IO_CM_STATUS ? 3 : 0, m == IO_CM_CMD ? cmd + 1u : cmd, m == IO_CM_TRANS ? trans + 1u : trans,
		       m == IO_BIGLEN ? 8193u : m == IO_ZEROLEN ? 0u : m == IO_FULL ? 8192u : len, m == IO_EPOCH ? e1 + 1u : e1, m == IO_XOR ? 1u : 0u);
	}
	if (m == IO_MANY) {
		unsigned j;
		for (j = 2u; j < 6u; j++) {
			io_put(k0 + j, 0xCu, rid, 0, cmd, trans, len, (254u + k0 + j) % 253u, 0u);
		}
		*d2hw = k0 + 6u;
	} else if (m == IO_THIRD) {
		io_put(k0 + 2u, 0xCu, rid, 0, cmd, trans, len, (254u + k0 + 2u) % 253u, 0u);
		*d2hw = k0 + 3u;
	} else {
		*d2hw = k0 + 2u;
	}
}

#include "sweet_wlan_l25_wl.h"     /* T-5173: the L25 store whitelist judges the real ioctl stream (wlan_g2_host_l25wl.h) */
static unsigned lw_on;
static void lw_judge(int sp, uint32_t a, uint32_t v);
#include "wlan_g2_host_dongle.h"   /* the generic fake dongle gd_* / the D4D fake d4d_db (moved out for R29, T-4adb) */

static int
b_wr(void *ctx, int sp, uint32_t a, uint32_t v)
{
	uint32_t *p;
	(void)ctx;
	if (lw_on) {
		lw_judge(sp, a, v);
	}
	if (f.nw < NREC) {
		f.w[f.nw].sp = sp;
		f.w[f.nw].a = a;
		f.w[f.nw].v = v;
	}
	f.nw++;
	if (f.awin && sp != SWG_SP_HOST && !(sp == SWG_SP_PCIE && (a == 0x140u || (a == 0xC34u && v == 0u)))) {
		f.nwrwin++;
	}
	if (f.kind == F_RC_LATE && sp == SWG_SP_TCM && a == SH + 0x34u) {
		f.rc = 0x00100001u;             /* the RC Command changes after the entry read it, before its row */
	}
	switch (sp) {
	case SWG_SP_TCM:
		f.ntcm++;
		if (f.kind == F_NOWRITE || ((f.kind == F_SLOTDROP || f.kind == F_RESTOREFAIL) && a == I_HW)) {
			f.dropped = 1;
			return 0;
		}
		if (f.kind == F_LATE && a == SH + 0x50u) {
			f.ram[(SH + 0x54u) / 4u] = 1u;          /* host_cap changes after the entry read it 0 */
		}
		p = tcm(a);
		if (p == NULL) {
			return -1;
		}
		*p = v;
		return 0;
	case SWG_SP_WIN2:
		if (a != 0x84u || (v != 0u && v != SWW_WIN_TAIL) || (f.kind == F_WINFAIL && v != 0u) || (f.kind == F_RESTOREFAIL && f.dropped && v == 0u)) {
			return -1;
		}
		f.win2 = v;
		return 0;
	case SWG_SP_EP:
		f.nep++;
		if (a == 0x88u) {
			f.nc88w++;
			return -1;
		}
		if (a != 0x04u || (f.kind == F_EP_WRFAIL && v == 6u)) {
			return -1;
		}
		if (f.kind == F_EP_STUCK || (f.kind == F_UNDOFAIL && v == 2u)) {
			return 0;
		}
		f.ep = 0x00100000u | (v & 0xFFFFu);     /* the Status half is W1C: a 0 there changes nothing */
		return 0;
	case SWG_SP_RC:
		f.nrc++;
		if (a != 0x04u || (f.kind == F_RC_WRFAIL && v == 0x507u)) {
			return -1;
		}
		if (f.kind == F_EP_LATE) {
			f.ep = 0x00100006u;             /* the EP Command changes after the entry read it, before its row */
		}
		if (f.kind != F_RC_STUCK && !(f.kind == F_RC_UNDOFAIL && v == 0u)) {
			f.rc = 0x00100000u | (v & 0xFFFFu);
		}
		return 0;
	case SWG_SP_HOST:
		if (a >= HBW * 4u || (a & 3u) != 0u || want_d4b < 2) {
			return -1;
		}
		if ((d4kind == D_ITEM_WRFAIL && a == fplan->b[SWD_H2D_CTRL].off + 8u) || (d4kind == D_SLOT_WRFAIL && a == fplan->b[SWD_IDX_H2D_W].off)) {
			return -1;
		}
		if (wr_fail_host_a != 0u && a == wr_fail_host_a) {
			return -1;
		}
		if (wr_fail_zero_a != 0u && a == wr_fail_zero_a && v == 0u) {
			return -1;
		}
		if (wr_corrupt_host_a != 0u && a == wr_corrupt_host_a && v == wr_corrupt_v) {
			f.hb[a / 4u] = v ^ 1u;
			return 0;
		}
		f.hb[a / 4u] = v;
		return 0;
	case SWG_SP_PCIE:
		if (want_d4b >= 2 && a == 0xC34u) {
			if (d4kind == D_MASK_WRFAIL && v == 0xFFFFu) {
				return -1;      /* the glue refused the mask (an RC irq enable set, review r1 F9): nothing stored */
			}
			if (v == 0u) {
				f.nunm++;
				f.awin = 0;
				if (d4kind == D_UNMASK_WRFAIL) {
					return -1;
				}
				if (d4kind == D_UNMASK_FAIL) {
					return 0;       /* the register ignores the write */
				}
			}
			f.c34 = v;
			return 0;
		}
		if (gd.on && tdb_defer != 0u && !tdb_seen && (v >> 16) == 0xFF00u && strstr(logbuf, "L24 quiet wait") != NULL) {      /* T-c3ac: hold the first doorbell after the quiet wait */
			tdb_seen = 1;
			tdb_pend = v;
			tdb_left = tdb_defer;
			return 0;
		}
		if (gd.on && want_d4b >= 3 && a == 0x140u && gd_db(v)) {      /* 22b: the generic fake dongle takes every doorbell after the L14 one */
			return 0;
		}
		if (want_d4b >= 4 && a == 0x140u && d4d_db(v)) {
			return 0;
		}
		if (want_d4b >= 3 && a == 0x140u && (v == 0xDD020001u || v == 0xFF000002u || v == 0xFF000003u || v == 0xFF000004u || v == 0xDD020002u)) {      /* W-D4c: the five doorbells after the L14 one: C1, A1, W, D, C2 */
			if (v == 0xDD020001u) {
				f.ndbrd++;
			} else if (v == 0xDD020002u) {
				f.ndbrd2++;
			} else if (v == 0xFF000002u) {
				f.ndbwr++;
				f.awin = 1;     /* the no-access window starts at the ack doorbell (FF000002), not at the first A1 item store (review d4c r2 F4) */
			} else if (v == 0xFF000003u) {
				f.ndbwake++;
				if (d4kind == D_MB && mb_note == 2 && fplan != NULL) {
					f.hb[fplan->b[SWD_IDX_H2D_R].off / 4u] = 0xFFFFFFFFu;     /* dead link in the exit-note poll */
				} else if (d4kind == D_MB && mb_note == 3 && fplan != NULL) {
					f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 0xFFFFFFFFu;     /* dead link in the exit-note poll, D2H-WR slot */
				} else if (d4kind == D_MB && mb_note == 1 && fplan != NULL) {
					if (mb_nlate > 0) {
						f.late_note = mb_nlate;
					} else {
						put_note();
					}
				}
			} else {
				f.ndbdeas++;
				f.awin = 0;
				if (d4kind == D_MB && mb_after == 2) {
					f.ram[MBP / 4u] = 0x10000000u;
				} else if (d4kind == D_MB && mb_after == 3) {
					f.ram[SH / 4u] |= 0x400u;
				} else if (d4kind == D_MB && mb_after == 5 && fplan != NULL) {
					f.hb[fplan->b[SWD_IDX_H2D_R].off / 4u] = 0xFFFFFFFFu;     /* dead link */
				}
			}
			if (d4kind == D_MB && mb_twk_db == v && fplan != NULL) {
				f.hb[mb_twk_off / 4u] = mb_twk_val;
			}
			return 0;
		}
		if (want_d4b >= 2 && a == 0x140u && v == 0xFF000001u) {
			if (d4kind == D_DB0_WRFAIL) {
				return -1;
			}
			f.ndb0++;
			f.c140 = v;
			if (d4kind == D_MB && mb_tamper != 0u && fplan != NULL) {
				f.hb[fplan->b[SWD_IDX_H2D_W].off / 4u] = mb_tamper;
			}
			if (d4kind == D_PRE_ITEM || d4kind == D_PRE_WRAP || d4kind == D_PRE_NOANS || d4kind == D_MB) {
				f.late = d4kind == D_PRE_ITEM;
				return 0;
			}
			if (d4kind == D_NODONGLE) {
				return 0;
			}
			if (d4kind == D_FWHALT) {
				f.ram[MBP / 4u] = 0x10000000u;
			} else if (d4kind == D_POLL_D2HW_ONES) {
				f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 0xFFFFFFFFu;
			} else if (d4kind == D_C30_ONLY) {
				f.c30 = 5u;     /* review r1 F3: only a core word moved on its own; no host memory changed */
			} else if (d4kind == D_H2DR_ONLY) {
				f.hb[fplan->b[SWD_IDX_H2D_R].off / 4u] = 1u;     /* the dongle consumed the post: host h2d_r[0] moved, nothing else */
			} else if (d4kind == D_D2HW_ZERO) {
				f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 1u;     /* the index landed, ring-2 item 0 is still zero */
			} else if (d4kind == D_TRAP) {
				f.ram[SH / 4u] |= 0x400u;
			} else {
				uint32_t *r2 = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u];
				f.hb[fplan->b[SWD_IDX_H2D_R].off / 4u] = 1u;
				r2[0] = (d4kind == D_BADEPOCH ? 2u : 1u) << 24 | 0x0Eu;     /* type 0x0E (arbitrary, the fake), epoch byte at +3 */
				r2[1] = 0x11111111u;
				r2[2] = 0x22222222u;
				r2[5] = r2[0] ^ r2[1] ^ r2[2] ^ (d4kind == D_BADXOR ? 1u : 0u);
				f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 1u;
			}
			return 0;
		}
		if (a != 0x144u || v != 0x12345678u) {
			return -1;
		}
		f.nready++;
		if (f.kind == F_FATAL) {
			f.ram[SH / 4u] |= 0x200u;
		} else if (f.kind == F_FATAL_TRAP) {
			f.ram[SHA / 4u] |= 0x400u;
		} else if (f.kind == F_TAKE4) {
			f.ram[((ov_ri != 0u ? ov_ri + 0x4Cu : RM) + 0x40u) / 4u] = 0x00000001u;  /* only a ring_mem idx/type word: informational, not a change (W-D4a s.0 item 4) */
		} else if (f.kind == F_CH_FL) {
			f.ram[SH / 4u] |= 0x1000u;
		} else if (f.kind == F_CH_MB) {
			f.ram[MBP / 4u] = 2u;           /* the DATA word, not the pointer */
		} else if (f.kind == F_CH_DW0 || f.kind == F_CH_DW1 || f.kind == F_CH_DW2) {
			f.ram[I_DW / 4u + (unsigned)(f.kind - F_CH_DW0)] = 1u;
		} else if (f.kind == F_CH_C30) {
			f.c30 = 1u;
		} else if (f.kind == F_FWHALT) {
			f.ram[MBP / 4u] = 0x10000000u;
		} else if (f.kind == F_C30_ONES) {
			f.c30 = 0xFFFFFFFFu;
		} else if (f.kind == F_MB_ONES) {
			f.ram[MBP / 4u] = 0xFFFFFFFFu;
		} else if (f.kind == F_DW_ONES) {
			f.ram[(tail_all ? I_DWT : I_DW) / 4u + 1u] = 0xFFFFFFFFu;
		} else if (f.kind != F_NOTAKE && f.kind != F_POLL_DEAD && f.kind != F_POLL_RDFAIL && f.kind != F_POLL_RDFAIL2) {
			f.ram[((ov_ri != 0u ? ov_ri + 0x4Cu : RM) + 0x10u) / 4u] = 0x00000001u;  /* ring_mem moves with ring_info */
			f.ram[I_DW / 4u] = 0x00000001u;                                           /* the dongle posts: d2h_w slot 0 (the default change the poll must find) */
		}
		if (d4kind == D_PRE_WRAP && fplan != NULL) {
			f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 64u;      /* D2H-WR slot 0 at ring 2 max_item (64): the next item index is past the ring */
		}
		if ((d4kind == D_PRE_ITEM || d4kind == D_PRE_NOANS) && fplan != NULL) {
			uint32_t *r2 = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u];
			r2[0] = 1u << 24 | 0x80u << 16 | 0x24u;     /* run d4b13 [obs merged.log 7083.186]: item 0 = 0x1800024 (type 0x24 D2H_MAILBOX_DATA, flags 0x80, epoch 1), mailbox data 0x2 at +12 (bcmmsgbuf.h:900-907), D2H-WR slot 0 = 1, all before any post */
			r2[1] = 0u;
			r2[3] = 0x2u;
			r2[5] = r2[0] ^ r2[3];
			f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 1u;
		}
		if (d4kind == D_MB && fplan != NULL) {
			uint32_t *r2 = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u];

			r2[0] = mb_epoch << 24 | (mb_type != 0u ? 0x80u << 16 : 0u) | mb_type;       /* run d4b13 [obs merged.log 7083.186]: item 0 = 0x1800024, type 0x24, flags 0x80 (an empty slot, type 0, stays all zero), epoch 1, d2h_mailbox_data u32 at byte 12 = r2[3] (bcmmsgbuf.h:900-907) */
			r2[3] = mb_mb;
			r2[5] = r2[0] ^ r2[3] ^ (mb_xorbad ? 1u : 0u);
			f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = mb_dw;
		}
		return 0;
	}
	return -1;
}

/* the 16-bit store: the low half of the word (recorded with sp | 0x100); D_S16_DROP ignores it, D_S16_WIDE is a faulty access that also clears the upper half */
static int
b_wr16(void *ctx, int sp, uint32_t a, uint16_t v)
{
	uint32_t *p;

	(void)ctx;
	if (lw_on) {
		lw_judge(sp == SWG_SP_HOST ? 0 : sp, a, v);     /* the fake host takes 32-bit stores only: a 16-bit one is judged as unknown, refused */
	}
	f.n16++;
	if (f.nw < NREC) {
		f.w[f.nw].sp = sp | 0x100;
		f.w[f.nw].a = a;
		f.w[f.nw].v = v;
	}
	f.nw++;
	if (sp != SWG_SP_TCM || d4kind == D_S16_WRFAIL) {
		return -1;
	}
	p = tcm(a);
	if (p == NULL) {
		return -1;
	}
	f.n16rec++;
	if (d4kind == D_S16_DROP) {
		return 0;
	}
	*p = d4kind == D_S16_WIDE ? v : (*p & 0xFFFF0000u) | v;
	return 0;
}

static void
b_delay(void *ctx, unsigned ms)
{
	(void)ctx;
	f.ndelay++;
	f.msdelay += ms;
	if (tdb_pend != 0u && --tdb_left == 0u) {
		uint32_t dv = tdb_pend;

		tdb_pend = 0u;
		gd_db(dv);
	}
	if (tspin_ones && ms == 1u && strstr(logbuf, "L24 hb: WLC_SET_VAR event_msgs_ext posted") != NULL) {
		tspin_on = 1;
	}
	if (io_late > 0u && --io_late == 0u && fplan != NULL) {
		io_second();
	}
	if (d4kind == D_MB && f.late_note > 0 && --f.late_note == 0 && fplan != NULL) {
		put_note();
	}
	if (gd.on && ge.jlate > 0u && ge.jn >= 6u && --ge.jlate == 0u) {
		ge_join_events();
	}
	if (gd.on && ge.jn == 0u && ge.qlate[0] != 0u && strstr(logbuf, "L24 hb poll") != NULL) {      /* L24 W-A: a late event inside the quiet wait */
		unsigned k;

		for (k = 0; k < 2u; k++) {
			if (ge.qlate[k] != 0u && --ge.qlate[k] == 0u) {
				ge.eflags = 1u;
				ge_event(16u, 0u, NULL, 0u);
				ge.eflags = 0u;
				if (k == 0u && ge.qlate[1] == 0u) {
					break;
				}
			}
		}
	}
	if (gd.on && ge.jlow > 0u && ge.jn >= 6u && --ge.jlow == 0u && fplan != NULL) {
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = (gd.w + 40u) % 64u;       /* the write index is 24 items behind the read count, as the wait sees it (T-4e39) */
	}
	if (d4kind == D_MB && mb_late_dw != 0u && f.ndelay == 2u && fplan != NULL) {
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = mb_late_dw;
	}
	if (d4kind == D_PRE_ITEM && f.late && f.ndelay == 2u && fplan != NULL) {
		uint32_t *r2 = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u + 6u];      /* item 1 (24 B = 6 words) */

		r2[0] = 2u << 24 | 0x0Eu;
		r2[1] = 0x11111111u;
		r2[2] = 0x22222222u;
		r2[5] = r2[0] ^ r2[1] ^ r2[2];
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 2u;
	}
}

/* T-eabd: the stack-headroom probe of the engine (swg_hr): 9000 B at the first call, minus room_dec per call after it; room_off = not readable (~0ull, the `unknown` path) */
static unsigned room_dec, room_calls;
static int room_off;
static uint64_t
b_room(void *ctx)
{
	(void)ctx;
	return room_off ? ~0ull : 9000ull - (uint64_t)room_dec * room_calls++;
}

static const struct swg_bus bus = { b_rd, b_wr, b_delay, NULL, b_wr16, b_room, NULL, NULL, NULL };
static const struct swg_bus bus_no16 = { b_rd, b_wr, b_delay, NULL, NULL, b_room, NULL, NULL, NULL };

static void clm_tests(struct swd_plan *p);
static const uint8_t *tclm;      /* T-0f36: the blob the test hands the shim as the container's CLM entry */
static uint32_t tclmlen;

static void
mk(struct swg_wx *x, const struct swd_plan *p, int kind)
{
	unsigned i;

	fw_init(kind);
	f.ram[(SHA + 0x20u) / 4u] |= ge.rxbufs << 16;                             /* patch 0357: max_host_rxbufs (u16 at +0x22; total_lfrag_pkt_cnt at +0x20 is 0) */
	f.ram[(SHA + 0x24u) / 4u] = ge.rxtcm != 0u ? ge.rxtcm : ge.rxoff;       /* W-D4e: dma_rxoffset */
	memset(swl_gap_tab, 0, sizeof swl_gap_tab);     /* each case sees its gap lines at count 1 */
	swl_gap_nd = swl_gap_tot = swl_gap_over = 0;
	memset(x, 0, sizeof *x);
	room_calls = 0;
	x->b = &bus;
	x->in.sh = kind == F_SH_OUT ? 0x1ffff0u : kind == F_SH_END ? END - 0x30u : ov_sh != 0u ? ov_sh : SH;
	x->in.end = END;
	for (i = 0; i < SWG_IDX_N; i++) {
		x->in.idx[i] = PA + p->b[SWD_IDX_H2D_W + i].off;
	}
	for (i = 0; i < SWG_RM_N; i++) {
		x->in.ring[i] = PA + p->b[SWD_H2D_CTRL + i].off;
	}
	x->in.trap = PA + p->b[SWD_TRAP].off;
	x->in.scratch = PA + p->b[SWD_SCRATCH].off;
	x->in.blk = PA;
	x->in.blk_len = p->total;
	x->in.evbuf = PA + p->b[SWD_EVBUF].off;
	x->in.respbuf = PA + p->b[SWD_RESPBUF].off;
	x->in.ioctbuf = PA + p->b[SWD_IOCTBUF].off;
	x->in.clm = tclm;               /* T-0f36: the CLM the L22c level (sweetwlan=24) sends, NULL = none */
	x->in.clmlen = tclmlen;
	x->d4b = want_d4b;
	fplan = p;
	if (d4kind == D_MASK_OLD) {
		f.c34 = 1u;
	}
	if (d4kind == D_MASK_DEAD) {
		f.c34 = 0xFFFFFFFFu;
	}
	if (d4kind == D_MB && mb_dirty_a != 0u) {
		f.hb[mb_dirty_a / 4u] = mb_dirty_v;
	}
	if (d4kind == D_RO_HOST || d4kind == D_RO_TCM_ITEM || d4kind == D_RO_BADEPOCH) {      /* the item and the host index words are there before the observation (the dongle posted by itself) */
		uint32_t *r2 = &f.hb[p->b[SWD_D2H_CTRL].off / 4u];

		r2[0] = (d4kind == D_RO_BADEPOCH ? 2u : 1u) << 24 | 0x0Eu;
		r2[1] = 0x11111111u;
		r2[2] = 0x22222222u;
		r2[5] = r2[0] ^ r2[1] ^ r2[2];
		f.hb[p->b[SWD_IDX_D2H_W].off / 4u] = d4kind == D_RO_TCM_ITEM ? 0u : 1u;      /* D_RO_TCM_ITEM: only the TCM index moved, the item is there */
		f.hb[p->b[SWD_IDX_H2D_R].off / 4u] = 1u;
	}
	if (d4kind == D_ITEM_OLD) {
		f.hb[p->b[SWD_H2D_CTRL].off / 4u + 5u] = 0x77u;
	}
	if (d4kind == D_SLOT_OLD) {
		f.hb[p->b[SWD_IDX_H2D_W].off / 4u] = 0x00040000u;
	}
}

static int
has(const char *s)
{
	return strstr(logbuf, s) != NULL;
}

/* patch 0292: the scan sink the keep phase hands ESCAN_RESULT events to (the glue's is sweet_wlan_a80211_scan_event); records every call */
static struct { unsigned n; uint32_t st[8], len[8]; uint8_t b[8][12 + SWG_E_BSS_RMAX]; } g_sk;
static void g_sink(void *cx, uint32_t status, const uint8_t *res, uint32_t len)
{
	(void)cx;
	if (g_sk.n < 8u && len <= sizeof g_sk.b[0]) {
		g_sk.st[g_sk.n] = status; g_sk.len[g_sk.n] = len; memcpy(g_sk.b[g_sk.n], res, len);
	}
	g_sk.n++;
}

/* the stop code each entry case must end in (a case that stops for the wrong reason has not tested its rule) */
static int
entry_code(int k)
{
	switch (k) {
	case F_RC_START:
		return SWG_E_RCSTUCK;
	case F_EP: case F_SRPWR: case F_MAXSUB: case F_MAXCPL: case F_CMD_RDFAIL: case F_EP_RDFAIL: case F_SRPWR_RDFAIL: case F_SRPWR_ST:
		return SWG_E_ENTRY;
	case F_FLAGS: case F_VERSION: case F_FLAGS_ASSERT:
		return SWG_E_FLAGS;
	case F_SH_OUT: case F_RI_OVL_SH: case F_RI_OVL_LO: case F_RI_OVL_HI: case F_RM_END: case F_RINGMEM: case F_IDXPTR: case F_IDXEND: case F_SH_END: case F_IDXEND2: case F_IDXALIGN:
	case F_OVL_RING: case F_OVL_SH: case F_OVL_RI: case F_OVL_IDX: case F_MBP_ALIGN: case F_MBP_OOR: case F_MBP_LOW:
		return SWG_E_ADDR;
	default:
		return SWG_E_OLD;       /* ring_mem not 0, a host address / shared word not 0, flags2 changed */
	}
}

/* index of the first recorded store (sp, a, v) at or after `from`, or f.nw when none */
static unsigned
find_rec(int sp, uint32_t a, uint32_t v, unsigned from)
{
	unsigned i;

	for (i = from; i < f.nw && i < NREC; i++) {
		if (f.w[i].sp == sp && f.w[i].a == a && f.w[i].v == v) {
			return i;
		}
	}
	return f.nw;
}

#define T SWG_SP_TCM
#define W SWG_SP_WIN2
#define LO(k) ((uint32_t)(PA + p.b[k].off))
#define HI(k) ((uint32_t)((PA + p.b[k].off) >> 32))

/*
 * Stage-3 row W15 (SPEC-WIFI D-12, review 0174-w-d4b-hwwrite-r1 F1/F2): cfg 0x88 := SWW_CFG88 | 0x300 with the ARM halted, before the release, sweetwlan=15 only; the restore (swl_rs group C88)
 * is armed by the STORE that was made and stored after the CA row. A small fake of struct swl_wbus: the link/endpoint words the preconditions read, cfg 0x80/0x84/0x88, and the CA7 wrapper's RESET_CTL.
 */
struct w15f {
	uint32_t c88, win, ctl;
	unsigned nw88, nwr, mode;       /* stores of cfg 0x88 seen (made or refused), all stores, failure mode */
	uint32_t w88[4];                /* the cfg 0x88 values stored, in order */
	unsigned ord88, ordctl;         /* store number (1-based) of the first cfg 0x88 store / of the first RESET_CTL store */
};
enum { W15_OK, W15_REFUSE, W15_MISMATCH, W15_DROP, W15_CAFAIL };
static struct w15f wf;

static int
w15_rd(void *c, int space, uint32_t win, uint32_t off, uint32_t *v)
{
	static const uint32_t a0[7] = { 0x4u, 0x80000000u, 0x60FFE000u, 0u, 0x60FFFFFFu, 0x01000000u, 0u };
	static const uint32_t a1[7] = { 0u, 0x80000000u, 0x60000000u, 0u, 0x6000FFFFu, 0x60000000u, 0u };

	(void)c;
	(void)win;
	switch (space) {
	case SWW_ISO:
		*v = 1u;
		return 0;
	case SWW_ELBI:
		*v = 0x11u;
		return 0;
	case SWW_ATU:
		*v = off < 0x100u ? a0[off / 4u] : a1[(off - 0x200u) / 4u];
		return 0;
	case SWW_CFG:
		*v = off == 0x04u ? 0x00100002u : off == 0x10u ? 0x60000004u : off == 0x14u ? 0u : off == SWW_CFG_WIN ? wf.win : off == SWW_CFG_WIN2 ? 0u : off == 0x88u ? wf.c88 : 0u;
		return 0;
	case SWW_BAR0:
		*v = off == SWL_AGENT_RESET_CTL ? wf.ctl : 0u;
		return 0;
	}
	return -1;
}

static int
w15_wr(void *c, int space, uint32_t win, uint32_t off, uint32_t v)
{
	(void)c;
	(void)win;
	wf.nwr++;
	if (space == SWW_CFG && off == 0x88u) {
		if (wf.nw88 < 4u) {
			wf.w88[wf.nw88] = v;
		}
		if (wf.nw88++ == 0u) {
			wf.ord88 = wf.nwr;
		}
		if (wf.mode == W15_REFUSE && v == (SWW_CFG88 | SWW_C88_BITS)) {
			return -1;
		}
		if (wf.mode == W15_DROP && v == (SWW_CFG88 | SWW_C88_BITS)) {
			return 0;       /* accepted and ignored: the read-back differs */
		}
		wf.c88 = wf.mode == W15_MISMATCH && v == (SWW_CFG88 | SWW_C88_BITS) ? v ^ 1u : v;
		return 0;
	}
	if (space == SWW_CFG && off == SWW_CFG_WIN) {
		wf.win = v;
		return 0;
	}
	if (space == SWW_BAR0 && off == SWL_AGENT_RESET_CTL) {
		if (wf.ordctl == 0u) {
			wf.ordctl = wf.nwr;
		}
		if (wf.mode == W15_CAFAIL) {
			return -1;      /* the CA7 re-halt store fails (review r2 F12) */
		}
		wf.ctl = v;
		return 0;
	}
	return -1;
}

static int w15_map2(void *c) { (void)c; return 0; }
static int w15_rng(void *c, uint8_t *b, uint32_t n) { (void)c; (void)b; (void)n; return -1; }
static void w15_dl(void *c, unsigned us) { (void)c; (void)us; }
static const struct swl_wbus w15_bus = { w15_rd, w15_wr, w15_map2, w15_rng, w15_dl, NULL };

static void
w15_mk(struct swl_wx *x, int w15, unsigned mode)
{
	memset(&wf, 0, sizeof wf);
	wf.c88 = SWW_CFG88;
	wf.mode = mode;
	memset(x, 0, sizeof *x);
	x->b = &w15_bus;
	x->level = SWW_F3_LEVEL;        /* stage 3 stays capped at level 10; W15 is gated by x->w15, not by the level */
	x->flags = SWF_W0;
	x->w15 = w15;
	loglen = 0;
	logbuf[0] = 0;
	memset(swl_gap_tab, 0, sizeof swl_gap_tab);
	swl_gap_nd = swl_gap_tot = swl_gap_over = 0;
}

static void
w15_tests(void)
{
	struct swl_wx x;
	unsigned i, ica = 99u, ic88 = 99u;
	int rc;

	/* the table: the C88 restore row stores exactly SWW_CFG88, whole word, AFTER the CA7 re-halt row (the re-halt first, then the config word) */
	for (i = 0; i < sizeof swl_rs / sizeof swl_rs[0]; i++) {
		if (swl_rs[i].grp == SWW_RG_CA) {
			ica = i;
		}
		if (swl_rs[i].grp == SWW_RG_C88) {
			ic88 = i;
			CHECK(swl_rs[i].space == SWW_CFG && swl_rs[i].off == 0x88u && swl_rs[i].val == SWW_CFG88 && swl_rs[i].vmask == 0xFFFFFFFFu);
		}
	}
	CHECK(ica < 99u && ic88 < 99u && ic88 > ica);
	/* the row: one 32-bit store, old pinned to the W0 value, SWT_ONCE, armed by the store, live only at level 15, ordered before the release rows */
	CHECK(swl_wt[SWT_C88].row == 15 && swl_wt[SWT_C88].space == SWW_CFG && swl_wt[SWT_C88].off == 0x88u && swl_wt[SWT_C88].val == 0x80380u && swl_wt[SWT_C88].vmask == 0xFFFFFFFFu && swl_wt[SWT_C88].omask == 0xFFFFFFFFu &&
	      swl_wt[SWT_C88].oval == 0x80080u && swl_wt[SWT_C88].need == SWF_W0 && swl_wt[SWT_C88].rgrp == SWW_RG_C88 && (swl_wt[SWT_C88].flags & (SWT_ONCE | SWT_ARMST | SWT_W15)) == (SWT_ONCE | SWT_ARMST | SWT_W15));
	CHECK(SWT_C88 < SWT_REL_ASSERT && SWT_C88 < SWT_WIN2_TAIL_E && SWT_C88 > SWT_RESET0);

	/* level 15: the store is made, read back, the line is printed, the gap is resolved, and the restore (after the re-halt) stores SWW_CFG88 back */
	w15_mk(&x, 1, W15_OK);
	swl_miss_defer = 1;
	swl_miss_n = 0;
	memset(swl_miss_tab, 0, sizeof swl_miss_tab);
	swl_miss("w15", SWL_M_W15, "medium");
	rc = swl_w15(&x);
	logbuf[loglen] = 0;
	CHECK(rc == 0 && wf.nw88 == 1u && wf.w88[0] == 0x80380u && wf.c88 == 0x80380u && (x.armed & SWW_RG_C88) && (x.flags & SWF_W15) && (x.done & (1ull << SWT_C88)) && x.stopped == 0);
	CHECK(has("SWEET WLAN: W15 cfg 0x88 0x80080 -> 0x80380 read back\n") && swl_miss_tab[0].res == 1u && !has("WIFI GAP: w15"));
	swl_miss_defer = 0;
	swl_restore(&x);
	logbuf[loglen] = 0;
	CHECK(wf.nw88 == 2u && wf.w88[1] == SWW_CFG88 && wf.c88 == SWW_CFG88 && x.restore == SWW_RS_OK && has("restore space 1 win 0 off 0x88 := 0x80080 ok\n") && !has("FAILED") && (x.armed & SWW_RG_C88) == 0u);
	/* ONCE: a second W15 is a stop and stores nothing */
	CHECK(swl_w15(&x) != 0 && wf.nw88 == 2u);

	/* the restore order: with the CA7 re-halt and the config word both armed, the RESET_CTL store comes first */
	w15_mk(&x, 1, W15_OK);
	x.armed = SWW_RG_CA | SWW_RG_C88;
	wf.ctl = 0u;
	swl_restore(&x);
	logbuf[loglen] = 0;
	CHECK(wf.ordctl != 0u && wf.ord88 != 0u && wf.ordctl < wf.ord88 && wf.ctl == 1u && wf.c88 == SWW_CFG88);
	/* review r2 F12: after the release (SWF_W16) a failed CA7 re-halt keeps cfg 0x88 as W15 left it (no store under a running ARM), loud; a confirmed one restores it */
	for (i = 0; i < 2u; i++) {
		w15_mk(&x, 1, i == 0u ? W15_CAFAIL : W15_OK);
		x.flags |= SWF_W16;
		x.armed = SWW_RG_CA | SWW_RG_C88;
		wf.c88 = SWW_CFG88 | SWW_C88_BITS;
		swl_restore(&x);
		logbuf[loglen] = 0;
		if (i == 0u) {
			CHECK(wf.ordctl != 0u && wf.nw88 == 0u && wf.c88 == (SWW_CFG88 | SWW_C88_BITS) && x.ca_ok == 0 && x.restore == SWW_RS_FAILED && has("WIFI GAP: restore cfg 0x88 restore skipped: CA7 not re-halted") &&
			      has("SWEET WLAN: restore of group 16 skipped, CA7 not re-halted (cfg 0x88 keeps 0x300)\n"));
		} else {
			CHECK(wf.nw88 == 1u && wf.c88 == SWW_CFG88 && x.ca_ok == 1 && x.restore == SWW_RS_OK && !has("CA7 not re-halted"));
		}
	}

	/* levels <= 14 (x->w15 = 0): no store, no line, nothing armed, the w15 gap stays; the row itself refuses a direct store (a stop, no bus store) */
	w15_mk(&x, 0, W15_OK);
	swl_miss_defer = 1;
	swl_miss_n = 0;
	memset(swl_miss_tab, 0, sizeof swl_miss_tab);
	swl_miss("w15", SWL_M_W15, "medium");
	rc = swl_w15(&x);
	logbuf[loglen] = 0;
	CHECK(rc == 0 && wf.nwr == 0u && x.armed == 0u && !(x.flags & SWF_W15) && swl_miss_tab[0].res == 0u && !has("W15 cfg 0x88"));
	swl_miss_defer = 0;
	rc = swl_wr(&x, SWW_CFG, 0, 0x88u, SWW_CFG88 | SWW_C88_BITS);
	CHECK(rc == -SWW_E_LEVEL && wf.nw88 == 0u && x.stopped == 1 && x.armed == 0u && x.restore == SWW_RS_NONE);

	/* the store is refused (the glue says no): no restore is armed, no restore store, no FAILED line (review r1 F2); the w15 gap stays open */
	w15_mk(&x, 1, W15_REFUSE);
	swl_miss_defer = 1;
	swl_miss_n = 0;
	memset(swl_miss_tab, 0, sizeof swl_miss_tab);
	swl_miss("w15", SWL_M_W15, "medium");
	rc = swl_w15(&x);
	logbuf[loglen] = 0;
	swl_miss_defer = 0;
	CHECK(rc == -SWW_E_WR && wf.nw88 == 1u && x.armed == 0u && x.restore == SWW_RS_NONE && !has("FAILED") && !has("restore space") && has("WIFI GAP: stop W15 STOP") && swl_miss_tab[0].res == 0u && !(x.flags & SWF_W15));

	/* the store is made but the read-back differs: the restore stores SWW_CFG88 (not the OR'ed value) and ends with the word at 0x80080 */
	w15_mk(&x, 1, W15_MISMATCH);
	swl_miss_defer = 1;
	swl_miss_n = 0;
	memset(swl_miss_tab, 0, sizeof swl_miss_tab);
	swl_miss("w15", SWL_M_W15, "medium");
	rc = swl_w15(&x);
	swl_miss_defer = 0;
	CHECK(swl_miss_tab[0].res == 0u);       /* a store that did not read back does not close the gap */
	logbuf[loglen] = 0;
	CHECK(rc == -SWW_E_READBACK && wf.nw88 == 2u && wf.w88[0] == 0x80380u && wf.w88[1] == SWW_CFG88 && wf.c88 == SWW_CFG88 && x.restore == SWW_RS_OK && has("off 0x88 := 0x80080 ok\n") &&
	      has("SWEET WLAN: W15 cfg 0x88 0x80080 -> 0x80380 read back 0x80381 (mismatch), restore follows\n"));
	/* accepted and ignored: the read-back is the old word, a stop, and the restore runs (the store was made as far as the bus said) */
	w15_mk(&x, 1, W15_DROP);
	rc = swl_w15(&x);
	CHECK(rc == -SWW_E_READBACK && wf.nw88 == 2u && wf.w88[1] == SWW_CFG88 && wf.c88 == SWW_CFG88 && x.restore == SWW_RS_OK);

	/* the old word is not the one W0 pinned (0x80081, or 0x300 already set): a stop BEFORE any store */
	w15_mk(&x, 1, W15_OK);
	wf.c88 = 0x80081u;
	rc = swl_w15(&x);
	CHECK(rc == -SWW_E_OLD && wf.nw88 == 0u && x.armed == 0u);
	w15_mk(&x, 1, W15_OK);
	wf.c88 = 0x80380u;
	rc = swl_w15(&x);
	CHECK(rc == -SWW_E_OLD && wf.nw88 == 0u && x.armed == 0u);

	/* after the release started (W16 / RELOK): refused with a gap, nothing stored */
	for (i = 0; i < 2u; i++) {
		w15_mk(&x, 1, W15_OK);
		x.flags |= i == 0u ? SWF_W16 : SWF_RELOK;
		rc = swl_w15(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWW_E_PHASE && wf.nw88 == 0u && wf.nwr == 0u && x.armed == 0u && has("WIFI GAP: w15 refused: the CA7 release already started") && has("SWEET WLAN: W15 refused: flags"));
	}
	/* W0 not done: the row's need refuses a direct store */
	w15_mk(&x, 1, W15_OK);
	x.flags = 0u;
	rc = swl_wr(&x, SWW_CFG, 0, 0x88u, SWW_CFG88 | SWW_C88_BITS);
	CHECK(rc == -SWW_E_PHASE && wf.nw88 == 0u);
	/* any other cfg 0x88 value: the allowlist denies it */
	w15_mk(&x, 1, W15_OK);
	rc = swl_wr(&x, SWW_CFG, 0, 0x88u, SWW_CFG88 | 0x100u);
	CHECK(rc == -SWW_E_DENIED && wf.nw88 == 0u);
}

/* ---- W-D4d (sweetwlan 17..20) ---- */
static unsigned
d4d_after_c2(void)
{
	return find_rec(SWG_SP_PCIE, 0x140u, 0xDD020002u, 0u);
}

/* the stores after the C2 doorbell, in order, without the cfg 0x84 moves: the first n entries of c[] must be there in exactly this order, then the unmask and the EP and RC restores (or nothing else when stopped = 0) */
static int
d4d_stores(const struct rec *c, unsigned n, unsigned tail)
{
	unsigned k, j = d4d_after_c2(), at, nd = 0;

	if (j >= f.nw) {
		return 0;
	}
	for (k = 0, j++; k < n; k++) {
		at = find_rec(c[k].sp, c[k].a, c[k].v, j);
		if (at >= f.nw) {
			fprintf(stderr, "d4d store %u (sp %d %#x := %#x) missing after record %u\n", k, c[k].sp, c[k].a, c[k].v, j);
			return 0;
		}
		j = at + 1u;
	}
	for (k = d4d_after_c2() + 1u; k < f.nw && k < NREC; k++) {
		nd += f.w[k].sp != SWG_SP_WIN2;
	}
	return nd == n + tail;
}

static unsigned d4d_case;
static const char *dump_label;      /* F4 (review 0174-w-d4e-scan24-hwwrite-r1): set before a ds_tests run; with G2_DUMP_STORES=1 the (space, off, value) stores after the L16 C2 doorbell are printed for tools/test_wlan_wr_mutants.py to replay through the real glue list */

static void
dump_stores(const char *label, const struct swg_wx *x)
{
	unsigned k, n = 0, c2 = find_rec(SWG_SP_PCIE, 0x140u, 0xDD020002u, 0u);

	for (k = c2 + 1u; c2 < f.nw && k < f.nw && k < NREC; k++) {
		n += f.w[k].sp == SWG_SP_HOST || (f.w[k].sp == SWG_SP_PCIE && f.w[k].a == 0x140u);
	}
	if (f.nw > NREC) {
		printf("G2TRUNC %u\n", f.nw);       /* more stores than the record array: the replay would miss some (the consumer fails on this line) */
	}
	printf("G2PA %#llx\nG2SET %s %u %u\n", (unsigned long long)PA, label, (unsigned)x->d4ds, n);
	for (k = 0; x->jc != NULL && k < x->njc; k++) {        /* T-8ede: the join commands handed to the engine (L24), so the replay builds the glue list from the same table: cmd, len, the bytes in hex */
		unsigned b;

		printf("G2JC %u %u ", (unsigned)x->jc[k].cmd, (unsigned)x->jc[k].len);
		for (b = 0; b < x->jc[k].len && b < sizeof x->jc[k].buf; b++) {
			printf("%02x", x->jc[k].buf[b]);
		}
		printf("\n");
	}
	for (k = c2 + 1u; c2 < f.nw && k < f.nw && k < NREC; k++) {
		if (f.w[k].sp == SWG_SP_HOST || (f.w[k].sp == SWG_SP_PCIE && f.w[k].a == 0x140u)) {
			printf("G2S %c %#x %#x\n", f.w[k].sp == SWG_SP_HOST ? 'H' : 'P', f.w[k].a, f.w[k].v);
		}
	}
}

static const struct swg_jcmd *t_jc;      /* L24: the join commands handed to the engine (NULL = none, every level below 24) */
static uint32_t t_njc;

static void
d4d_go(struct swd_plan *p, struct swg_wx *x, int *rc)
{
	io_late = 0u;
	ndb_d4d = 0u;
	memset(io_db, 0, sizeof io_db);
	mk(x, p, F_OK);
	x->jc = t_jc;
	x->njc = t_njc;
	*rc = swg_go(x);
	logbuf[loglen] = 0;
	d4d_case++;
	if (getenv("D4D_CASE") != NULL && (unsigned)atoi(getenv("D4D_CASE")) == d4d_case) {
		fprintf(stderr, "---- d4d case %u rc %d stores %u ndelay %u msdelay %u\n%s----\n", d4d_case, *rc, x->nstore, f.ndelay, f.msdelay, logbuf);
	}
	if (dump_label != NULL) {
		if (getenv("G2_DUMP_STORES") != NULL) {
			dump_stores(dump_label, x);
		}
		dump_label = NULL;
	}
}

static void
d4d_cfg(int lvl, int mode, int req, int l17)
{
	want_d4b = lvl - 13;
	d4kind = D_MB;
	mb_type = 0x24u; mb_mb = 0x2u; mb_epoch = 1u; mb_dw = 1u; mb_xorbad = 0; wr_fail_host_a = 0u; wr_fail_zero_a = 0u; mb_note = 1;
	mb_ntype = 0x24u; mb_nmb = 0x4u; mb_nepoch = 2u; mb_ndw = 2u; mb_nxor = 0; mb_nlate = 0;
	io_caplen = 0u;
	io_nocap = 0;
	wr_corrupt_host_a = 0u; wr_corrupt_v = 0u; mb_dirty_v = 5u; mb_dirty_a = 0u; mb_twk_db = 0u;
	io_mode = mode; io_req = req; io_l17 = l17;
}

static void
d4d_run(struct swd_plan *p, struct swg_wx *x, int lvl, int mode, int req, int l17, int *rc)
{
	d4d_cfg(lvl, mode, req, l17);
	d4d_go(p, x, rc);
}

static unsigned
d4d_longest_line(void)
{
	unsigned best = 0, cur = 0;
	const char *c;

	for (c = logbuf; *c != '\0'; c++) {
		if (*c == '\n') {
			best = cur > best ? cur : best;
			cur = 0;
		} else {
			cur++;
		}
	}
	return best > cur ? best : cur;
}

static void
gd_arm(void)
{
	memset(&gd, 0, sizeof gd);
	memset(&ge, 0, sizeof ge);
	tclm = NULL;
	tclmlen = 0u;
	ge.major = 2u;
	ge.pfin = -1;
	ge.npartial = 3u;
	gd.on = 1;
	gd.h2dr = 1u;      /* the L14 item 0 is consumed */
	gd.w = 1u;         /* ring-2 item 0 (the first DS_ENTER_REQ) is there */
}

/* 22b: DS_ENTER_REQ rounds after the L16 one (task 10): every case runs the generic fake dongle */
static void
ds_tests(struct swd_plan *p)
{
	struct swg_wx x;
	const uint32_t r0 = p->b[SWD_H2D_CTRL].off;
	int rc;

	/* (i) the F1 log: a type-0x24 item (data 0x2) at ring-2 index 2 before L17 -> answered, L17 proceeds, L20 ends with the shifted indices */
	gd_arm();
	gd.inj[0].n = 3u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	dump_label = "i";
	d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
	gd.on = 0;
	CHECK(rc == 0 && !x.d4dstop && x.d4ds == 1u && x.dshold == 1u && x.d4wr == 11u && x.d4rd == 8u && x.restore == 1);
	CHECK(has("SWEET WLAN: D4d L20 start: h2d_w0 0x6 d2h_r0 0x4 d2h_w0 0x4 (expected ring-0 write index 6, ring-2 read index 4)\n") && !has("not answered"));
	CHECK(has("SWEET WLAN: D4 DS round 1 answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items 2..3, ring-0 items 4..5, ring-0 WR 4 -> 6, ring-2 RD 2 -> 4\n"));
	CHECK(has("SWEET WLAN: D4 DS round 1: DEVICE_WAKE_DEASSERT NOT sent, device_wake held (deep_sleep 0 equivalent, dhd_pcie.c:8132-8148, 13076-13085)\n") && !has("D4 DS round 1 answered (DS_ACK, wake, exit note, deassert)"));
	CHECK(has("WIFI GAP: d4ds DS hold, no DEASSERT x1 [medium]") && !has("host_active_cnt deferral") && !has("DS round cap"));
	CHECK(has("SWEET WLAN: D4d L17 ioctl-response buffer post: id 2, 8192 B at pa ") && has("ring-0 idx 6 epoch 7, H2D-WR 7, doorbell 0xff000007\n") && has("SWEET WLAN: D4d L20 done (ring-0 WR 11, ring-2 RD 8)\n"));
	CHECK(f.hb[r0 / 4u + 40u] == 0x05800023u && f.hb[r0 / 4u + 42u] == 2u && f.hb[r0 / 4u + 50u] == 0x06800023u && f.hb[r0 / 4u + 52u] == 0x40u && f.hb[r0 / 4u + 60u] == 0x0780000Bu && f.hb[r0 / 4u + 62u] != 0x20u);      /* items 4, 5 = DS_ACK, wake; item 6 is already the L17 post: the hold round wrote no deassert item */
	/* (i-b) the same request arriving while the host waits (after the L17 post, doorbell n = 4): the wait offers the front item first, the round is made, the watch goes on */
	gd_arm();
	gd.inj[0].n = 4u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	dump_label = "i-b";
	d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
	gd.on = 0;
	CHECK(rc == 0 && !x.d4dstop && x.d4ds == 1u && x.dshold == 1u && x.d4wr == 11u && x.d4rd == 8u && x.restore == 1);
	CHECK(has("SWEET WLAN: D4 DS round 1 answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items 2..3, ring-0 items 5..6, ring-0 WR 5 -> 7, ring-2 RD 2 -> 4\n") && has("SWEET WLAN: D4d L20 done (ring-0 WR 11, ring-2 RD 8)\n") && !has("not answered"));
	/* (iv) every other mailbox bit is a STOP with no store: D3_ACK 0x1, 0x8, FWHALT, DS_ENTER_REQ with another bit */
	{
		static const uint32_t bad[] = { 0x1u, 0x8u, 0x10000000u, 0x6u, 0x4u };
		unsigned k;

		for (k = 0; k < sizeof bad / sizeof bad[0]; k++) {
			gd_arm();
			gd.inj[0].n = 3u;
			gd.inj[0].mb = bad[k];
			gd.ninj = 1u;
			d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
			gd.on = 0;
			CHECK(rc < 0 && x.d4ds == 0u && x.d4wr == 4u && x.restore == 1 && x.d4dn == 0u && has("SWEET WLAN: D4 DS STOP, no store: ring-2 item 2 mailbox data 0x") && !has("D4 DS round 1"));
			CHECK(f.hb[r0 / 4u + 40u] == 0u && has("G2 STOP: fatal"));
		}
	}
	/* (iii) the DS hold: the request after round 0 is answered by a hold round (no deassert), and a request that arrives AFTER it (doorbell 6 = the L17 post: U1, a firmware that ignores the hold) is a STOP with its own text and no store */
	gd_arm();
	gd.inj[0].n = 3u;
	gd.inj[0].mb = 2u;
	gd.inj[1].n = 6u;
	gd.inj[1].mb = 2u;
	gd.ninj = 2u;
	dump_label = "iii";
	d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
	gd.on = 0;
	CHECK(rc < 0 && x.d4ds == 1u && x.dshold == 1u && x.d4wr == 7u && x.d4rd == 4u && x.restore == 1 && has("SWEET WLAN: D4 DS round 1 answered (DS_ACK, wake, exit note, DS hold, no deassert)") && !has("D4 DS round 2") && !has("D4 DS round 1 STOP"));
	CHECK(has("SWEET WLAN GAP: D4 DS_ENTER_REQ at ring-2 item 4 while the DS hold is on (device_wake asserted, no deassert sent, research 124 s.4)") && has("WIFI GAP: d4ds DS_ENTER_REQ after the DS hold, stopped x1 [high]") && has("D4d L17 ioctl-response buffer post"));
	CHECK(f.hb[r0 / 4u + 70u] == 0u && has("G2 STOP: fatal"));      /* nothing stored at ring-0 item 7: no second hold round, no deassert, no L18 post */
	/* (iii-b) a straggler right behind the hold round's exit note (U2): the round itself stops on the ring-2 write index, no deassert, nothing consumed */
	gd_arm();
	gd.reinj = 1u;
	gd.straggle = 2u;
	d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
	gd.on = 0;
	CHECK(rc < 0 && x.d4ds == 0u && x.dshold == 0u && x.restore == 1 && has("SWEET WLAN: D4 DS round 1 STOP, no deassert: d2h ring 2 write index 5 after the wake, expected 4") && !has("D4 DS round 1 answered") && f.hb[r0 / 4u + 60u] == 0u);
	/* (v) an exit note that never comes inside an extra round: STOP (no deassert), the undo ran */
	gd_arm();
	gd.inj[0].n = 3u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	gd.nonote = 2u;
	d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
	gd.on = 0;
	CHECK(rc < 0 && x.d4ds == 0u && x.restore == 1 && x.d4sleep == 0u && has("SWEET WLAN: D4 DS round 1 STOP, no deassert: no DS_EXIT_NOTE in 1901 ms after the wake assert\n") && !has("D4 DS round 1 answered"));
	/* a stop between the ack doorbell and the wake doorbell of an extra round takes the d4sleep path (the wake item word cannot be stored) */
	gd_arm();
	gd.inj[0].n = 3u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	d4d_cfg(20, IO_BOTH, 0, 0);
	wr_fail_host_a = r0 + 40u * 5u + 8u;
	d4d_go(p, &x, &rc);
	gd.on = 0;
	wr_fail_host_a = 0u;
	CHECK(rc < 0 && x.d4sleep == 1u && x.restore == 1 && has("SWEET WLAN: D4c stop between the DS_ACK doorbell and the wake doorbell"));
	/* swg_d4d_consume across the ring-2 wrap: the host count 62 -> 66 stores the SLOT 2 and the doorbell 0xDD020002 (not the count); a foreign start count, a backwards update and a span of the whole ring are refused with no store */
	{
		const uint32_t rs = p->b[SWD_IDX_D2H_R].off;

		gd_arm();
		d4d_cfg(20, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4rd = 62u;
		x.d4dn = 24u;
		f.hb[rs / 4u] = 62u;
		x.d4b = 7;
		CHECK(swg_d4d_consume(&x, 62u, 66u) == 0 && x.d4rd == 66u && f.hb[rs / 4u] == 2u && find_rec(SWG_SP_PCIE, 0x140u, 0xDD020002u, 0u) != f.nw);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4rd = 5u;
		x.d4dn = 24u;
		x.d4b = 7;
		CHECK(swg_d4d_consume(&x, 3u, 5u) == -SWG_E_ORDER && x.nstore == 0u);
		CHECK(swg_d4d_consume(&x, 5u, 4u) == -SWG_E_ORDER || x.stopped);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4rd = 5u;
		x.d4b = 7;
		CHECK(swg_d4d_consume(&x, 5u, 69u) == -SWG_E_ORDER && x.nstore == 0u);
		gd.on = 0;
	}
	/* sprint 2026-11-08 (keep, K1): swg_go with x->keep skips the undo (and cfg 0x84 := 0) after a finished L26 (d4dlv >= 26), and runs the normal undo on a stop or a lower level. The G2-only run (no D4b) cannot reach L26 by itself:
	 * d4dlv is set by hand to the value swg_d4d_done(x, 26, ...) leaves, the branch under test is the one in swg_go. */
	{
		unsigned n0, n1, n2, n3;
		int rc0, rc1, rc2, rc3;
		const int sv_b = want_d4b, sv_k = d4kind;

		want_d4b = 0;      /* G2 only: a D4b/D4d run would overwrite d4dlv with the level it reached */
		d4kind = 0;
		mk(&x, p, F_OK);
		rc0 = swg_go(&x);
		n0 = f.nw;
		logbuf[loglen] = 0;
		CHECK(rc0 == 0 && !x.kept && x.restore == 1 && has("SWEET WLAN: G2 undo ok") && !has("G2 keep") && find_rec(SWG_SP_EP, 0x04u, 0x2u, 0u) != f.nw);      /* baseline: keep off = today's behaviour, the undo stores EP Command := 0x2 */
		mk(&x, p, F_OK);
		x.keep = 1;
		x.d4dlv = 26u;
		rc1 = swg_go(&x);
		n1 = f.nw;
		logbuf[loglen] = 0;
		CHECK(rc1 == 0 && x.kept == 1 && x.restore == 0 && n1 + 2u <= n0 && !has("SWEET WLAN: G2 undo") && find_rec(SWG_SP_EP, 0x04u, 0x2u, 0u) == f.nw && find_rec(SWG_SP_RC, 0x04u, 0u, 0u) == f.nw);
		CHECK(has("SWEET WLAN: G2 keep: dongle left running (undo skipped: EP bus master stays on, RC command kept)\n") && has("WIFI GAP: g2 keep: not restored: EP Command 0x2 (bus master stays ON)") && has("restore=kept (undo skipped)"));
		mk(&x, p, F_OK);
		x.keep = 1;
		x.d4dlv = 26u;
		x.d4dstop = 1u;
		rc2 = swg_go(&x);
		n2 = f.nw;
		logbuf[loglen] = 0;
		CHECK(rc2 == 0 && !x.kept && x.restore == 1 && n2 == n0 && has("SWEET WLAN: G2 undo ok") && has("SWEET WLAN: G2 keep not taken") && !has("G2 keep: dongle left running"));      /* keep on + a stop = the normal undo */
		mk(&x, p, F_OK);
		x.keep = 1;
		x.d4dlv = 25u;
		rc3 = swg_go(&x);
		n3 = f.nw;
		logbuf[loglen] = 0;
		CHECK(rc3 == 0 && !x.kept && x.restore == 1 && n3 == n0 && has("SWEET WLAN: G2 keep not taken"));       /* keep on + L26 not finished = the normal undo */
		want_d4b = sv_b;
		d4kind = sv_k;
	}
	/* sprint 2026-11-08 (keep, K4): swg_keep_r2 reads and consumes ring-2 items it does not answer: event, DS_ENTER_REQ (not acked), an unknown type; counts and prints each; one consume store pair; a bad epoch or a firmware halt word stops with no store */
	{
		const uint32_t rs = p->b[SWD_IDX_D2H_R].off, ws = p->b[SWD_IDX_D2H_W].off, r2 = p->b[SWD_D2H_CTRL].off;
		unsigned k, w, nw0;
		uint32_t *it;

		gd_arm();
		d4d_cfg(20, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4dn = 24u;
		x.d4b = 7;
		x.kept = 1;
		CHECK(swg_keep_r2(&x) == 0 && x.kr2n == 0u && x.nstore == 0u);        /* nothing pending: no store */
		for (k = 0; k < 3u; k++) {
			it = &f.hb[r2 / 4u + 6u * k];
			memset(it, 0, 24u);
			it[0] = swg_ep(k) << 24 | (k == 0u ? 0x0Eu : k == 1u ? 0x24u : 0x77u);
			it[3] = k == 1u ? 0x2u : 0u;
			it[5] = it[0] ^ it[1] ^ it[2] ^ it[3] ^ it[4];
		}
		f.hb[ws / 4u] = 3u;
		f.hb[rs / 4u] = 0u;
		CHECK(swg_keep_r2(&x) == 0 && x.d4rd == 3u && x.kr2n == 3u && x.kr2ev == 1u && x.kr2ds == 1u && x.kr2o == 1u && !x.d4dstop && f.hb[rs / 4u] == 3u && find_rec(SWG_SP_PCIE, 0x140u, 0xDD020003u, 0u) != f.nw);
		logbuf[loglen] = 0;
		CHECK(has("WIFI GAP: keep event item 0: pktid 0 is not a posted event buffer, not decoded, no buffer to re-post, item_len 0\n") && !has("keep ring-2 item 0 type 0xe") && has("WIFI GAP: keep ring-2 item 1 type 0x24 (DS_ENTER_REQ NOT ANSWERED, class count 1, mailbox data 0x2) not handled") &&
		      has("WIFI GAP: keep ring-2 item 2 type 0x77 (type, class count 1, mailbox data 0) not handled") && swl_gap_tot >= 3u && x.nstore == 2u);
		nw0 = f.nw;
		CHECK(swg_keep_r2(&x) == 0 && f.nw == nw0);      /* the next pass: nothing new, no store */
		/* a bad epoch: a loud stop, no consume store */
		it = &f.hb[r2 / 4u + 6u * 3u];
		memset(it, 0, 24u);
		it[0] = (swg_ep(3u) ^ 0x55u) << 24 | 0x0Eu;
		it[5] = it[0];
		f.hb[ws / 4u] = 4u;
		nw0 = f.nw;
		w = x.nstore;
		CHECK(swg_keep_r2(&x) == 0 && x.d4dstop == 1u && x.kr2c == 1u && x.d4rd == 3u && f.nw == nw0 && x.nstore == w);
		logbuf[loglen] = 0;
		CHECK(has("SWEET WLAN: L26 STOP, no further store: keep: ring-2 item 3 epoch/xor check failed") || has("STOP, no further store: keep: ring-2 item 3 epoch/xor check failed"));
		CHECK(swg_keep_r2(&x) == 0 && f.nw == nw0);       /* stopped: nothing at all */
		/* a firmware halt word: consumed in the log, loud stop, no store */
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4dn = 24u;
		x.d4b = 7;
		x.kept = 1;
		it = &f.hb[r2 / 4u];
		memset(it, 0, 24u);
		it[0] = swg_ep(0u) << 24 | 0x24u;
		it[3] = 0x10000000u;
		it[5] = it[0] ^ it[3];
		f.hb[ws / 4u] = 1u;
		f.hb[rs / 4u] = 0u;
		nw0 = f.nw;
		CHECK(swg_keep_r2(&x) == 0 && x.d4dstop == 1u && x.kr2c == 1u && f.nw == nw0);
		logbuf[loglen] = 0;
		CHECK(has("firmware halt/trap code: the data path ends") && has("keep: firmware halt/trap mailbox data 0x10000000"));
		/* not kept: the function is inert */
		mk(&x, p, F_OK);
		x.entered = 1;
		f.hb[ws / 4u] = 2u;
		nw0 = f.nw;
		CHECK(swg_keep_r2(&x) == 0 && f.nw == nw0 && x.kr2n == 0u);
		gd.on = 0;
	}
	/* patch 0281 (W-D6 boot A): the pure frame parse on a captured-style frame (the layout table of docs/research/W-D6-rejoin-dhcp.md s.2: a type-69 escan result, status 8, datalen 460, item length 534 as observed in cold1), its error returns,
	 * the link truth table (every WLC_E code the detector looks at), the detector (UP -> DOWN once, link-up while DOWN logged), and swg_ke_event on well-formed, short, foreign and unknown-buffer items. */
	{
		uint8_t fr[SWG_E_FRAME], in[SWD4B_D2H_LEN];
		struct swg_ev ev;
		static const struct { uint32_t type, flags, status; int want; } tt[] = {
			{ 16u, 0x0u, 0u, SWG_KL_DOWN }, { 16u, 0x2u, 0u, SWG_KL_DOWN }, { 16u, 0x1u, 0u, SWG_KL_UP }, { 16u, 0x3u, 0u, SWG_KL_UP },   /* LINK: bit 0x01 clear = down */
			{ 5u, 0u, 0u, SWG_KL_DOWN }, { 6u, 0u, 0u, SWG_KL_DOWN }, { 11u, 0u, 0u, SWG_KL_DOWN }, { 12u, 0u, 0u, SWG_KL_DOWN }, { 5u, 1u, 0u, SWG_KL_DOWN },   /* DEAUTH, DEAUTH_IND, DISASSOC, DISASSOC_IND */
			{ 0u, 0u, 0u, SWG_KL_UP }, { 0u, 1u, 3u, SWG_KL_DOWN }, { 0u, 0u, 8u, SWG_KL_DOWN },   /* SET_SSID: status 0 = up, non-zero = down */
			{ 3u, 0u, 0u, SWG_KL_NONE }, { 7u, 0u, 0u, SWG_KL_NONE }, { 19u, 0u, 0u, SWG_KL_NONE }, { 20u, 0u, 0u, SWG_KL_NONE }, { 37u, 0u, 0u, SWG_KL_NONE }, { 40u, 0u, 0u, SWG_KL_NONE },
			{ 46u, 0u, 0u, SWG_KL_NONE }, { 54u, 0u, 0u, SWG_KL_NONE }, { 56u, 0u, 0u, SWG_KL_NONE }, { 69u, 0u, 8u, SWG_KL_NONE }, { 0xFFFFFFFFu, 0u, 0u, SWG_KL_NONE } };
		unsigned k, gp0;
		const uint32_t evoff = p->b[SWD_EVBUF].off;

		memset(fr, 0, sizeof fr);
		fr[12] = 0x88u; fr[13] = 0x6Cu; fr[14] = 0x80u; fr[15] = 0x01u; fr[19] = 0x00u; fr[20] = 0x10u; fr[21] = 0x18u; fr[23] = 1u;
		fr[25] = 2u; fr[27] = 0x01u;                                     /* wl_event_msg version 2, flags 0x0001 (big endian u16 at +26) */
		fr[31] = 69u; fr[35] = 8u;                                       /* event_type 69 (ESCAN_RESULT), status 8 (PARTIAL), big endian u32 at +28 / +32 */
		fr[38] = 1u; fr[39] = 5u; fr[43] = 2u;                           /* reason 0x105 = 261 at +36, auth_type 2 at +40: a little endian reading would give 0x05010000 / 0x02000000 */
		fr[46] = 0x01u; fr[47] = 0xCCu;                                  /* datalen 460 at +44 */
		memset(fr + 48, 0xAA, 6);                                        /* an address field: never printed */
		memset(&ev, 0xEE, sizeof ev);
		CHECK(swg_e_fparse(fr, 534u, &ev) == 0 && ev.type == 69u && ev.status == 8u && ev.flags == 1u && ev.reason == 261u && ev.auth == 2u && ev.edlen == 460u && ev.dlen == 534u);
		CHECK(swg_e_fparse(fr, 72u + 460u, &ev) == 0 && swg_e_fparse(fr, 72u + 459u, &ev) == 2 && swg_e_fparse(fr, 40u, &ev) == 2);       /* the data length must fit the item */
		for (k = 0; k < 5u; k++) {
			static const unsigned fb[5] = { 12u, 19u, 20u, 21u, 23u };
			uint8_t g[SWG_E_FRAME];

			memcpy(g, fr, sizeof g);
			g[fb[k]] ^= 0x01u;
			CHECK(swg_e_fparse(g, 534u, &ev) == 1);       /* ethertype, the three OUI bytes and usr_subtype are all checked */
		}
		for (k = 0; k < sizeof tt / sizeof tt[0]; k++) {
			CHECK(swg_ke_class(tt[k].type, tt[k].flags, tt[k].status) == tt[k].want);
		}
		/* the detector: UP -> DOWN exactly once, a link-up event while DOWN is a line and nothing else */
		gd_arm();
		mk(&x, p, F_OK);
		x.kept = 1;
		loglen = 0;
		memset(&ev, 0, sizeof ev);
		ev.type = 69u; ev.status = 8u;
		swg_ke_note(&x, &ev);
		CHECK(x.klink == 0u && x.kl_new == 0u);
		ev.type = 16u; ev.flags = 1u; ev.status = 0u;
		swg_ke_note(&x, &ev);
		CHECK(x.klink == 0u && x.kl_new == 0u && x.kl_up_n == 0u);       /* up while UP: nothing */
		ev.type = 12u; ev.status = 0u; ev.reason = 4u;
		swg_ke_note(&x, &ev);
		logbuf[loglen] = 0;
		CHECK(x.klink == 1u && x.kl_new == 1u && x.kl_cause == 12u && x.kl_reason == 4u && has("SWEET WLAN: keep link DOWN cause 12 status 0 reason 4\n"));
		x.kl_new = 0u;
		ev.type = 5u; ev.reason = 2u;
		swg_ke_note(&x, &ev);
		CHECK(x.klink == 1u && x.kl_new == 0u && x.kl_cause == 12u);     /* DOWN again: no second edge */
		ev.type = 16u; ev.flags = 1u;
		gp0 = swl_gap_tot;
		swg_ke_note(&x, &ev);
		logbuf[loglen] = 0;
		CHECK(swl_gap_tot == gp0 + 1u);       /* review 0281 F2: counted in gaps=N */
		CHECK(x.klink == 1u && x.kl_new == 0u && x.kl_up_n == 1u && has("WIFI GAP: keep link UP event outside a re-join attempt ignored (type 16)\n") && x.rj_state == SWG_RJ_DOWN);       /* stays DOWN for iOS (patch 0282: the GAP is for an UP event outside an attempt) */
		/* patch 0282: the re-join states. The DOWN edge sets rj_state DOWN (above); while JOINING the two answers are recorded and raise no GAP, anything else is only logged; while KEYING a loss is logged, nothing is recorded */
		x.rj_state = SWG_RJ_JOINING;
		x.rj_ssid = x.rj_link = 0u;
		gp0 = swl_gap_tot;
		loglen = 0;
		ev.type = 0u; ev.flags = 0u; ev.status = 3u;
		swg_ke_note(&x, &ev);
		logbuf[loglen] = 0;
		CHECK(x.rj_ssid == 0u && x.rj_link == 0u && x.rj_lost == 0u && x.rj_state == SWG_RJ_JOINING && has("SWEET WLAN: keep rejoin: event type 0 status 3 noted while joining, not a link-up answer\n") && x.rj_ev_n == 1u);
		ev.type = 16u; ev.flags = 0u; ev.status = 0u;      /* ev.reason is still 2 (WLC_E_LINK_DISASSOC) and the cause was DISASSOC_IND (12) */
		swg_ke_note(&x, &ev);
		logbuf[loglen] = 0;
		CHECK(x.rj_ssid == 0u && x.rj_link == 0u && x.rj_lost == 0u && x.kl_tail == 1u && has("trailing LINK down (reason 2 = disassoc) of the lost association (cause 12) noted while joining"));       /* T-32: the first one is the tail of the loss */
		swg_ke_note(&x, &ev);
		CHECK(x.rj_ssid == 0u && x.rj_link == 0u);       /* a link-DOWN event while joining records no answer */
		CHECK(x.rj_lost == 1u);       /* ... but a second one is a loss (review 0282 F3) */
		{       /* T-32: the tail rule itself: only a LINK down, reason 2, once, after a DEAUTH/DISASSOC cause */
			struct swg_wx t;
			struct swg_ev e;
			memset(&t, 0, sizeof t); memset(&e, 0, sizeof e);
			e.type = 16u; e.reason = 2u;
			t.kl_cause = 16u; CHECK(swg_ke_tail(&t, &e) == 0);
			t.kl_cause = 5u; CHECK(swg_ke_tail(&t, &e) == 1);
			t.kl_cause = 6u; CHECK(swg_ke_tail(&t, &e) == 1);
			t.kl_cause = 11u; CHECK(swg_ke_tail(&t, &e) == 1);
			t.kl_cause = 12u; CHECK(swg_ke_tail(&t, &e) == 1);
			t.kl_cause = 0u; CHECK(swg_ke_tail(&t, &e) == 0);       /* a SET_SSID failure cause has no tail */
			t.kl_cause = 6u; t.kl_tail = 1u; CHECK(swg_ke_tail(&t, &e) == 0);
			t.kl_tail = 0u; e.reason = 1u; CHECK(swg_ke_tail(&t, &e) == 0);
			e.reason = 2u; e.flags = 1u; CHECK(swg_ke_tail(&t, &e) == 0);
			e.flags = 0u; e.type = 6u; CHECK(swg_ke_tail(&t, &e) == 0);
		}
		{       /* T-32: every new DOWN edge starts a new tail window (a stale flag of the last episode would fail the next first attempt) */
			struct swg_wx t;
			struct swg_ev e;
			memset(&t, 0, sizeof t); memset(&e, 0, sizeof e);
			t.kl_tail = 1u; t.rj_state = SWG_RJ_UP;
			e.type = 6u; e.reason = 6u;
			swg_ke_note(&t, &e);
			CHECK(t.klink == 1u && t.kl_tail == 0u && t.kl_cause == 6u);
			memset(&t, 0, sizeof t);
			e.type = 16u; e.reason = 2u;
			swg_ke_note(&t, &e);
			CHECK(t.klink == 1u && t.kl_tail == 1u && t.kl_cause == 16u);       /* a LINK-down cause: no tail to wait for */
		}
		x.rj_lost = 0u;
		ev.type = 0u; ev.flags = 0u; ev.status = 0u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_ssid == 1u && x.rj_link == 0u);
		ev.type = 16u; ev.flags = 1u; ev.status = 0u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_ssid == 1u && x.rj_link == 1u && swl_gap_tot == gp0 && x.kl_up_n == 1u && x.rj_state == SWG_RJ_JOINING && x.klink == 1u);       /* the answer to the join: no GAP, no new DOWN edge, the state is the machine's to move */
		CHECK(x.rj_lost == 0u);       /* review 0282 F3: SET_SSID status 3 (a firmware retry) is no loss while JOINING */
		ev.type = 5u; ev.flags = 0u; ev.status = 0u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 1u);
		x.rj_lost = 0u;
		ev.type = 12u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 1u);
		x.rj_lost = 0u;
		ev.type = 16u; ev.flags = 0u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 1u);
		x.rj_lost = 0u;
		ev.type = 6u; swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 1u);
		x.rj_lost = 0u;
		ev.type = 11u; swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 1u);
		x.rj_lost = 0u;
		ev.type = 16u; ev.flags = 1u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 0u && x.rj_link == 1u);       /* the up event is the answer, not a loss */
		ev.type = 56u; ev.flags = 0u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 0u);
		x.rj_link = 0u; x.rj_ssid = 0u;
		x.rj_state = SWG_RJ_KEYING;
		loglen = 0;
		ev.type = 5u; ev.flags = 0u; ev.status = 0u; ev.reason = 7u;
		swg_ke_note(&x, &ev);
		logbuf[loglen] = 0;
		CHECK(has("SWEET WLAN: keep rejoin: link-loss event type 5 status 0 reason 7 noted during the 4-way, the attempt fails\n") && x.rj_lost == 1u && x.rj_state == SWG_RJ_KEYING && x.rj_ssid == 0u && x.kl_new == 0u && swl_gap_tot == gp0);
		x.rj_lost = 0u;
		ev.type = 0u; ev.status = 3u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_lost == 1u);       /* while KEYING any link-down class event (a SET_SSID failure too) is a loss */
		x.rj_lost = 0u;
		ev.type = 16u; ev.flags = 1u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_link == 0u && x.rj_state == SWG_RJ_KEYING && swl_gap_tot == gp0);       /* a link-up event while KEYING: nothing recorded, no GAP */
		x.rj_state = SWG_RJ_DOWN;
		x.rj_ev_n = 0u;
		CHECK(swg_rj_backoff_ms(1u) == 2000u && swg_rj_backoff_ms(2u) == 4000u && swg_rj_backoff_ms(3u) == 8000u && swg_rj_backoff_ms(4u) == 16000u && swg_rj_backoff_ms(5u) == 30000u && swg_rj_backoff_ms(9u) == 30000u && swg_rj_backoff_ms(0u) == 2000u);
		x.rj_state = SWG_RJ_UP;
		x.klink = 0u;
		x.rj_ssid = x.rj_link = 1u;       /* stale answers: the DOWN edge starts the machine clean */
		x.rj_lost = 1u;
		ev.type = 5u;
		swg_ke_note(&x, &ev);
		CHECK(x.rj_state == SWG_RJ_DOWN && x.rj_ssid == 0u && x.rj_link == 0u && x.rj_lost == 0u && x.klink == 1u);       /* a loss while UP starts the machine */
		x.kl_new = 0u;
		/* swg_ke_event on item `in` (request_id = pktid at +4, event_data_len at +12): a well-formed frame in event buffer 3 */
		gd_arm();
		mk(&x, p, F_OK);
		x.kept = 1;
		x.epid[3] = 0x2Bu;
		memcpy((uint8_t *)&f.hb[(evoff + 3u * SWD_EVBUF_LEN) / 4u], fr, sizeof fr);
		memset(in, 0, sizeof in);
		in[4] = 0x2Bu; in[12] = (uint8_t)(534u & 0xFFu); in[13] = (uint8_t)(534u >> 8);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(has("SWEET WLAN: keep event type 69 flags 0x1 status 8 reason 261 auth 2 datalen 460 item_len 534\n") && x.kev_n == 1u && x.kev_gap == 0u && x.kevq == 1u && x.kevb[0] == 3u && x.epid[3] == 0u && x.klink == 0u);
		CHECK(!strstr(logbuf, "aa") && !strstr(logbuf, "AA") && !strstr(logbuf, "170"));       /* the address bytes of the frame (0xAA) are in no log line */
		CHECK(x.evq == 0u && x.evn == 0u && x.emax == 0u && x.nbss == 0u);     /* the L23 scan state and the boot event books are untouched */
		/* the same slot again (its pktid was consumed): not a posted buffer, a GAP line, nothing queued twice */
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 10u) == 0);
		logbuf[loglen] = 0;
		CHECK(has("WIFI GAP: keep event item 10: pktid 0x2b is not a posted event buffer, not decoded, no buffer to re-post, item_len 534\n") && x.kev_n == 1u && x.kev_gap == 1u && x.kevq == 1u);
		/* an item shorter than the frame header: GAP, the buffer is still queued for its re-post */
		x.epid[4] = 0x2Cu;
		in[4] = 0x2Cu; in[12] = 40u; in[13] = 0u;
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 11u) == 0);
		logbuf[loglen] = 0;
		CHECK(has("WIFI GAP: keep event item 11: item_len 40 is shorter than the 72 B frame header or outside the buffer (rx offset 0), not decoded\n") && x.kev_gap == 2u && x.kevq == 2u && x.kevb[1] == 4u && x.kev_n == 1u);
		/* a foreign frame (ethertype flipped): GAP, queued */
		x.epid[5] = 0x2Du;
		memcpy((uint8_t *)&f.hb[(evoff + 5u * SWD_EVBUF_LEN) / 4u], fr, sizeof fr);
		((uint8_t *)&f.hb[(evoff + 5u * SWD_EVBUF_LEN) / 4u])[12] = 0x08u;
		in[4] = 0x2Du; in[12] = (uint8_t)(534u & 0xFFu); in[13] = (uint8_t)(534u >> 8);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 12u) == 0);
		logbuf[loglen] = 0;
		CHECK(has("WIFI GAP: keep event item 12: not a BCM event frame (ethertype / OUI / usr_subtype), item_len 534, not decoded\n") && x.kev_gap == 3u && x.kevq == 3u);
		/* a link-loss frame: LINK flags 0 -> the DOWN line and the edge for the keep thread */
		x.epid[6] = 0x2Eu;
		memcpy((uint8_t *)&f.hb[(evoff + 6u * SWD_EVBUF_LEN) / 4u], fr, sizeof fr);
		((uint8_t *)&f.hb[(evoff + 6u * SWD_EVBUF_LEN) / 4u])[27] = 0x00u;
		((uint8_t *)&f.hb[(evoff + 6u * SWD_EVBUF_LEN) / 4u])[31] = 16u;
		((uint8_t *)&f.hb[(evoff + 6u * SWD_EVBUF_LEN) / 4u])[35] = 0u;
		in[4] = 0x2Eu;
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 13u) == 0);
		logbuf[loglen] = 0;
		CHECK(has("SWEET WLAN: keep event type 16 flags 0x0 status 0 reason 261 auth 2 datalen 460 item_len 534\n") && has("SWEET WLAN: keep link DOWN cause 16 status 0 reason 261\n") && x.klink == 1u && x.kl_new == 1u);
	}
	/* patch 0292 (WIFI-COVERAGE D3/D4): ESCAN_RESULT events in the keep phase (swg_ke_escan). Real items through swg_ke_event into event buffer 3: the layout is the one swg_e_event reads at L23 (12 B wl_escan_result header: sync_id u16 at +8, bss_count u16 at +10;
	 * a PARTIAL one carries SWG_E_BSS_NEED = 89 record bytes). With no scan collecting the event is a counted stray (line as before + one GAP); while sc_on, the first bytes go to the sink and no per-event line is printed; another sync_id is not handed on */
	{
		uint8_t ee[SWG_E_FRAME + 12u + SWG_E_BSS_NEED], in[SWD4B_D2H_LEN];
		const uint32_t evoff = p->b[SWD_EVBUF].off;
		unsigned gp0, ev0;

		gd_arm();
		mk(&x, p, F_OK);
		x.kept = 1;
		x.scansink = g_sink;
		x.escan_sync = 0x1234u;       /* review 0292 F2: the id of the scan that is running */
		memset(&g_sk, 0, sizeof g_sk);
		/* a builder for one event: status, datalen, sync_id; the record: version 109, length 89, an address field of 0xAA bytes, ssid_len 3 "abc" */
#define ESC_EV(status_, edlen_, sync_) do { \
	memset(ee, 0, sizeof ee); \
	ee[12] = 0x88u; ee[13] = 0x6Cu; ee[14] = 0x80u; ee[15] = 0x01u; ee[19] = 0x00u; ee[20] = 0x10u; ee[21] = 0x18u; ee[23] = 1u; ee[25] = 2u; ee[27] = 0x01u; ee[31] = 69u; ee[35] = (uint8_t)(status_); \
	ee[46] = (uint8_t)((edlen_) >> 8); ee[47] = (uint8_t)(edlen_); \
	ee[SWG_E_FRAME + 8] = (uint8_t)(sync_); ee[SWG_E_FRAME + 9] = (uint8_t)((sync_) >> 8); ee[SWG_E_FRAME + 10] = 1u; \
	ee[SWG_E_FRAME + 12] = 109u; ee[SWG_E_FRAME + 16] = 89u; memset(ee + SWG_E_FRAME + 12 + 8, 0xAA, 6); ee[SWG_E_FRAME + 12 + 18] = 3u; memcpy(ee + SWG_E_FRAME + 12 + 19, "abc", 3); \
	ee[SWG_E_FRAME + 12 + 72] = 0x06u; ee[SWG_E_FRAME + 12 + 73] = 0x10u; ee[SWG_E_FRAME + 12 + 78] = 0xC5u; ee[SWG_E_FRAME + 12 + 79] = 0xFFu; ee[SWG_E_FRAME + 12 + 88] = 6u; \
	memcpy((uint8_t *)&f.hb[(evoff + 3u * SWD_EVBUF_LEN) / 4u] + x.rxoff, ee, sizeof ee); \
	x.epid[3] = 0x2Bu; memset(in, 0, sizeof in); in[4] = 0x2Bu; in[12] = (uint8_t)((SWG_E_FRAME + (edlen_)) & 0xFFu); in[13] = (uint8_t)((SWG_E_FRAME + (edlen_)) >> 8); } while (0)
		/* 1: no scan collecting (sc_on 0): printed like any event, counted as a stray, ONE GAP the first time, the buffer is re-posted, the sink is not called */
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
		gp0 = swl_gap_tot;
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(has("SWEET WLAN: keep event type 69 flags 0x1 status 8 reason 0 auth 0 datalen 101 item_len 173\n") && has("WIFI GAP: keep: ESCAN_RESULT event outside a scan (late event of an earlier scan, or none was requested), ignored (1 so far)\n") &&
		      x.sc_stray == 1u && g_sk.n == 0u && swl_gap_tot == gp0 + 1u && x.kevq == 1u && x.kevb[0] == 3u && x.sc_ev == 0u);
		x.kevq = 0u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(x.sc_stray == 2u && !has("WIFI GAP: keep: ESCAN_RESULT event outside") && swl_gap_tot == gp0 + 1u);       /* the second one: counted, no second GAP line (the 64th gets one again) */
		x.kevq = 0u;
		x.sc_on = 1u;
		x.scansink = NULL;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.sc_stray == 3u && g_sk.n == 0u);       /* sc_on without a sink: nothing to hand to, counted loudly as a stray */
		x.kevq = 0u;
		x.scansink = g_sink;
		/* 2: collecting: a PARTIAL with a full record -> the sink gets (8, 12 + 89 bytes, the event's bytes), no per-event line, no address bytes in the log, counted, buffer re-posted */
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
		loglen = 0;
		ev0 = x.kev_n;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(g_sk.n == 1u && g_sk.st[0] == 8u && g_sk.len[0] == 12u + SWG_E_BSS_NEED && memcmp(g_sk.b[0], ee + SWG_E_FRAME, 12u + SWG_E_BSS_NEED) == 0);
		CHECK(x.sc_ev == 1u && x.sc_part == 1u && x.sc_complete == 0u && x.sc_other == 0u && x.kev_n == ev0 + 1u && x.kevq == 1u && x.kevb[0] == 3u && x.epid[3] == 0u);
		CHECK(!has("keep event type 69") && !has("abc") && !has("aa") && !has("AA") && !has("170") && loglen == 0u);
		x.kevq = 0u;
		/* 3: another sync_id (the firmware's own scan): not handed on, counted */
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1236u);       /* review 0292 F1/F2: ours is 0x1234 here; the id of the NEXT keep scan (0x1235) is foreign too */
		gp0 = swl_gap_tot;
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(g_sk.n == 1u && x.sc_other == 0u && x.sc_foreign == 1u && x.sc_ev == 1u && x.sc_termd == 0u);
		CHECK(has("WIFI GAP: keep scan: ESCAN_RESULT of another sync id 0x1236 ignored (1 so far; ours is 0x1234)\n") && swl_gap_tot == gp0 + 1u);       /* F1: the first one is told */
		x.kevq = 0u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1235u);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.sc_foreign == 2u && loglen == 0u && swl_gap_tot == gp0 + 1u && g_sk.n == 1u);       /* the second: counted, silent */
		x.kevq = 0u;
		x.sc_foreign = 62u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1235u);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(x.sc_foreign == 63u && loglen == 0u);
		x.kevq = 0u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1235u);
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(x.sc_foreign == 64u && has("another sync id 0x1235 ignored (64 so far; ours is 0x1234)\n") && swl_gap_tot == gp0 + 2u && g_sk.n == 1u && x.sc_ev == 1u);       /* the 64th again */
		x.sc_foreign = 0u;
		x.kevq = 0u;
		/* 4: a PARTIAL whose data is shorter than a record (50 B): the sink gets the 12 B header only (it reports "without a full bss record" itself) */
		ESC_EV(8u, 50u, 0x1234u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(g_sk.n == 2u && g_sk.st[1] == 8u && g_sk.len[1] == 12u && x.sc_ev == 2u && x.sc_part == 2u);
		x.kevq = 0u;
		/* 5: shorter than the header (8 B): a GAP, the sink is not called, counted */
		ESC_EV(8u, 8u, 0x1234u);
		gp0 = swl_gap_tot;
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(g_sk.n == 2u && x.sc_other == 1u && has("WIFI GAP: keep scan: ESCAN_RESULT event with 8 data bytes (< the 12 B header), ignored\n") && swl_gap_tot == gp0 + 1u && x.kevq == 1u);
		x.kevq = 0u;
		/* 6: status 0 (SUCCESS): the sink gets the header, sc_complete is set; another status is handed on and counted as other (the sink logs it) */
		ESC_EV(0u, 12u, 0x1234u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(g_sk.n == 3u && g_sk.st[2] == 0u && g_sk.len[2] == 12u && x.sc_complete == 1u && x.sc_ev == 3u && x.sc_part == 2u);
		x.kevq = 0u;
		x.sc_complete = 0u;
		ESC_EV(1u, 12u, 0x1234u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(g_sk.n == 4u && g_sk.st[3] == 1u && x.sc_complete == 0u && x.sc_other == 2u && x.sc_ev == 4u);
		CHECK(x.sc_termd == 1u && x.sc_term == 1u);       /* review 0292 F5: a status other than 0 / 8 of our sync id is an END, noted for swl25_sc_step */
		x.sc_termd = 0u; x.sc_term = 0u;
		x.kevq = 0u;
		/* 6b: a SUCCESS event whose data is as long as a record is still read as a header only (the record bytes are PARTIAL's) */
		ESC_EV(0u, 12u + SWG_E_BSS_NEED, 0x1234u);
		x.sc_complete = 0u;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(g_sk.n == 5u && g_sk.st[4] == 0u && g_sk.len[4] == 12u && x.sc_complete == 1u);
		x.kevq = 0u;
		x.sc_complete = 0u;
		/* 6c: the event frame starts rx_dataoffset bytes into the buffer (pciedev_shared dma_rxoffset): the result is read behind it */
		x.rxoff = 16u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(g_sk.n == 6u && g_sk.st[5] == 8u && g_sk.len[5] == 12u + SWG_E_BSS_NEED && memcmp(g_sk.b[5], ee + SWG_E_FRAME, 12u + SWG_E_BSS_NEED) == 0);
		x.rxoff = 0u;
		x.kevq = 0u;
		/* 6d: an event of another type while a scan is collecting is not a scan result: printed and judged by the link detector as before (here LINK down) */
		ESC_EV(0u, 12u, 0x1234u);
		((uint8_t *)&f.hb[(evoff + 3u * SWD_EVBUF_LEN) / 4u])[31] = 16u;
		((uint8_t *)&f.hb[(evoff + 3u * SWD_EVBUF_LEN) / 4u])[27] = 0u;
		loglen = 0;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		logbuf[loglen] = 0;
		CHECK(g_sk.n == 6u && has("SWEET WLAN: keep event type 16 flags 0x0 status 0 reason 0 auth 0 datalen 12 item_len 84\n") && x.klink == 1u && x.kl_new == 1u);
		x.klink = 0u; x.kl_new = 0u; x.rj_state = SWG_RJ_UP; x.kevq = 0u;
		/* 7: the scan detector is not fooled: a scan result never moves the link state, even while the link is DOWN and a re-join is JOINING */
		x.klink = 1u; x.rj_state = SWG_RJ_JOINING; x.rj_ev_n = 0u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.klink == 1u && x.rj_state == SWG_RJ_JOINING && x.rj_ev_n == 0u && x.rj_ssid == 0u && x.rj_link == 0u && x.rj_lost == 0u);
		x.klink = 0u; x.rj_state = SWG_RJ_UP; x.kevq = 0u;
		/* 8: the host read of the result fails (the block ends inside it): the engine stops (a dead host read is the bus's), nothing is handed to the sink */
		{
			const uint32_t blk_len = x.in.blk_len;
			const unsigned n0 = g_sk.n;

			ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1234u);
			x.in.blk_len = evoff + 3u * SWD_EVBUF_LEN + SWG_E_FRAME + 4u;       /* the frame header reads, the first result word reads, the second is outside */
			CHECK(swg_ke_event(&x, in, 9u) < 0 && x.stopped == 1 && g_sk.n == n0);
			x.in.blk_len = blk_len;
		}
		/* 9 (review 0292 F4): the scan ended by a link loss (sc_on 0, sc_await 1): the first end status (not PARTIAL) of ITS sync id sets sc_await_done and sc_term; a PARTIAL or another sync id does not; every one is still a counted stray */
		x.stopped = 0; x.code = 0;       /* case 8 stopped the engine on purpose */
		x.sc_on = 0u; x.sc_await = 1u; x.sc_await_done = 0u; x.escan_sync = 0x1236u; x.sc_term = 0u;
		ESC_EV(8u, 12u + SWG_E_BSS_NEED, 0x1236u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.sc_await_done == 0u);
		x.kevq = 0u;
		ESC_EV(4u, 12u, 0x1235u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.sc_await_done == 0u);
		x.kevq = 0u;
		ESC_EV(4u, 12u, 0x1236u);
		gp0 = x.sc_stray;
		{ const unsigned n0 = g_sk.n;
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.sc_await_done == 1u && x.sc_term == 4u && x.sc_stray == gp0 + 1u && g_sk.n == n0); }
		x.kevq = 0u;
		x.sc_await = 0u; x.sc_await_done = 0u;
		ESC_EV(4u, 12u, 0x1236u);
		CHECK(swg_ke_event(&x, in, 9u) == 0);
		CHECK(x.sc_await_done == 0u);       /* nobody waits: nothing is set */
		x.kevq = 0u;
		x.sc_on = 0u; x.scansink = NULL;
#undef ESC_EV
	}
	/* patch 0292: swg_e_escan_arg, the ONE builder of the passive escan START (boot scan L23 and the keep-phase scan), against bytes typed here from the layout comment of swg_e_l23: version u32, action u16 = 1, sync_id u16 = 0x1234, params version u16, length u16 = 72,
	 * bssid ff x6 at +48, bss_type 2 at +54, scan_type 1 (passive) at +56, nprobes / active / passive / home = -1 (ff x16 from +60), channel_num 7 at +76, the seven chanspecs at +80 (only with chans); chans == 0: the same without the channel_num and the list */
	{
		static const uint8_t cs[14] = { 0x01, 0x10, 0x06, 0x10, 0x0B, 0x10, 0x24, 0xD0, 0x28, 0xD0, 0x2C, 0xD0, 0x30, 0xD0 };
		uint8_t got[96], want[96];
		unsigned vers, chans, i;

		static const uint16_t syncs[3] = { 0x1234u, 0x1235u, 0x1333u };       /* review 0292 F2: the boot id and the first / last keep id */
		unsigned si;

		for (si = 0; si < 3u; si++)
		for (vers = 2u; vers <= 3u; vers++) {
			for (chans = 0u; chans <= 1u; chans++) {
				memset(got, 0x5A, sizeof got);       /* the builder writes all 96 bytes */
				swg_e_escan_arg(got, vers, (int)chans, syncs[si]);
				memset(want, 0, sizeof want);
				want[0] = (uint8_t)vers; want[4] = 1u; want[6] = (uint8_t)syncs[si]; want[7] = (uint8_t)(syncs[si] >> 8); want[8] = (uint8_t)vers; want[10] = 72u;
				for (i = 48; i < 54u; i++) want[i] = 0xFFu;
				want[54] = 2u; want[56] = 1u;
				for (i = 60; i < 76u; i++) want[i] = 0xFFu;
				if (chans) {
					want[76] = 7u;
					memcpy(want + 80, cs, sizeof cs);
				}
				CHECK(memcmp(got, want, sizeof got) == 0);
			}
		}
	}
	/* review 0174-w-d4e-scan24-hwwrite-r1 F5: a round that starts near / past the ring-2 wrap (absolute read counts 62, 63, 126 = 64 + 62): every slot value, doorbell, the exit-note slot and the old value of C2 use the count modulo 64
	 * (C1 := (rd + 1) % 64 + DD02xxxx, the note read from slot (rd + 1) % 64, C2 := (rd + 2) % 64 + DD02xxxx); the six `% SWG_R2_DEPTH` of the header (C1/C2 slot value, C1 doorbell, C2 old, ns, swg_d4d_r2 slot) each have a mutant */
	{
		static const uint32_t rdc[] = { 62u, 63u, 126u };
		const uint32_t rs = p->b[SWD_IDX_D2H_R].off;
		char want_note[64];
		unsigned k;

		for (k = 0; k < sizeof rdc / sizeof rdc[0]; k++) {
			const uint32_t rd = rdc[k], c1 = (rd + 1u) % 64u, c2 = (rd + 2u) % 64u;

			gd_arm();
			d4d_cfg(20, IO_BOTH, 0, 0);
			mk(&x, p, F_OK);
			CHECK(swg_entry(&x) == 0);        /* the entry state (rt: ring_info, index arrays, the mailbox pointer) the round's final read needs */
			x.d4wr = 4u;
			x.d4rd = rd;
			x.d4dn = 24u;
			x.d4b = 7;
			x.d4mask = (1ull << SWG_D4C_A_ITEM0) - 1ull;        /* the d4b rows before the round's rows are done (the order rule of swg_d4_row) */
			gd.w = rd;
			gd.h2dr = 4u;
			gd_put(0x24u, 0u, 0, 0x2u, 0u);        /* ring-2 item rd = DS_ENTER_REQ, D2H-WR slot (rd + 1) % 64 */
			f.hb[rs / 4u] = rd % 64u;
			f.hb[p->b[SWD_IDX_H2D_W].off / 4u] = 4u;
			snprintf(want_note, sizeof want_note, "ring2 item %u w 0x%x ", rd + 1u, ((254u + rd + 1u) % 253u) << 24 | 0x80u << 16 | 0x24u);
			CHECK(swg_ds_front(&x) == 1 && x.d4ds == 1u && x.d4rd == rd + 2u && x.d4wr == 6u && x.dshold == 1u && x.restore == 0);
			gd.on = 0;
			logbuf[loglen] = 0;
			CHECK(find_rec(SWG_SP_HOST, rs, c1, 0u) != f.nw && find_rec(SWG_SP_PCIE, 0x140u, 0xDD020000u | c1, 0u) != f.nw && find_rec(SWG_SP_HOST, rs, c2, 0u) != f.nw && find_rec(SWG_SP_PCIE, 0x140u, 0xDD020000u | c2, 0u) != f.nw);
			CHECK(f.hb[rs / 4u] == c2 && has(want_note) && has("SWEET WLAN: D4 DS round 1 answered") && !has("STOP") && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000000u | 7u, 0u) == f.nw);      /* no deassert doorbell (4 + 3) */
		}
	}
	/* swg_ds_front called directly: a non-0x24 front item is left alone (0, no store); a 0x24 with a bad epoch, and a round with no ring-0 room, are STOPs with no store */
	{
		uint32_t *r2;
		static const struct { uint32_t w0, d4wr; int want; const char *msg; } fc[] = {
			{ 0x0180000Au, 4u, 0, NULL },                                              /* an ack-type item: not ours */
			{ 0x02800024u, 4u, -SWG_E_FATAL, "is type 0x24 but fails the epoch/xor check (1)" },     /* epoch 2 at item 0 (1 expected) */
			{ 0x01800024u, 124u, -SWG_E_ADDR, "leaves no room for a DS round (depth 128)" },
		};
		unsigned k;

		for (k = 0; k < sizeof fc / sizeof fc[0]; k++) {
			gd_arm();
			d4d_cfg(20, IO_BOTH, 0, 0);
			mk(&x, p, F_OK);
			x.entered = 1;
			x.d4wr = fc[k].d4wr;
			x.d4rd = 0u;
			r2 = &f.hb[p->b[SWD_D2H_CTRL].off / 4u];
			r2[0] = fc[k].w0;
			r2[1] = r2[2] = r2[4] = 0u;
			r2[3] = 2u;
			r2[5] = r2[0] ^ r2[3];
			f.hb[p->b[SWD_IDX_D2H_W].off / 4u] = 1u;
			CHECK(swg_ds_front(&x) == fc[k].want && x.nstore == 0u && x.d4ds == 0u && (fc[k].msg == NULL || has(fc[k].msg)));
			gd.on = 0;
		}
	}
}

#include "wlan_g2_host_l24.h"   /* the scan24 helpers and the L24 join cases (moved out for R29, T-0a9d) */

static void
scan_tests(struct swd_plan *p)
{
	struct swg_wx x;
	const uint32_t r0 = p->b[SWD_H2D_CTRL].off, ib = p->b[SWD_IOCTBUF].off;
	const uint32_t ilo = (uint32_t)(PA + ib), ihi = (uint32_t)((PA + ib) >> 32);
	int rc;
	unsigned k;

	/* (A) the whole run: scan_ver 2, sup_wpa 0, an unsolicited event (type 54) before the sup_wpa ack, 3 PARTIAL + 1 SUCCESS results */
	gd_arm();
	ge.einj[0].n = 25u;
	ge.einj[0].type = 54u;
	ge.einj[0].pre = 1;
	ge.neinj = 1u;
	scan_run(p, &x, 23, &rc, "main");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.restore == 1 && x.d4wr == 39u && x.d4rd == 23u && x.nbss == 3u && x.evn == 5u && x.evq == 0u && x.evp == 13u && x.ncpl == 8u && x.ereq == 6u && x.rxoff == 0u && x.d4ds == 0u);
	CHECK(x.rpid[0] == 12u && x.rpid[1] == 13u && x.rpid[2] == 14u && x.rpid[3] == 15u && x.rpid[4] == 16u && x.rpid[5] == 0u && x.rpid[6] == 10u && x.rpid[7] == 11u);       /* FIFO: ids 4..9 answered and re-posted as 12..16 (the escan's id 9 in slot 5 is not re-posted), 10 and 11 still posted */
	CHECK(has("SWEET WLAN: D4d L20 done (ring-0 WR 9, ring-2 RD 6)\n") && has("SWEET WLAN: L21 posted 7 respbuf + 7 evbuf, ring-0 WR 23, ring-2 new items 0\n"));
	CHECK(has("SWEET WLAN: L22 scan_ver version 1 length 6 major 2\n") && has("SWEET WLAN: L22 sup_wpa get: status 0 value 0\n") && has("SWEET WLAN: L22 event_msgs_ext get: len 26 status 0\n"));
	CHECK(has("SWEET WLAN: L22 event_msgs_ext set: bit 69 on, status 0\n") && has("SWEET WLAN: L22 WLC_UP status 0\n") && has("SWEET WLAN: L22 event_msgs_ext mask (default, ver 1 command 0) 101112131415161718191a1b1c1d1e1f20212223242526272829\n"));
	CHECK(x.escan_sync == 0x1234u);       /* review 0292 F2: the boot scan tells the judge its id */
	CHECK(has("SWEET WLAN: L23 escan start: version 2 action 1 sync 0x1234 passive all channels, status 0\n") && has("SWEET WLAN: L23 escan event 69 status 8 sync 0x1234 bss_count 1\n") && has("SWEET WLAN: L23 escan event 69 status 0 sync 0x1234 bss_count 0\n"));
	CHECK(has("SWEET WLAN: L23 bss 0 chan 1 rssi -40 chanspec 0x1003 bssid 02:11:22:33:44:50 ssid \"net0\"\n") && has("SWEET WLAN: L23 bss 1 chan 6 rssi -41 chanspec 0x1008 bssid 02:11:22:33:44:51 ssid \"net1\"\n") && has("SWEET WLAN: L23 bss 2 chan 36 rssi -42 chanspec 0x1026 "));
	CHECK(has("SWEET WLAN: L23 scan done status 0 bss 3 channels 3\n") && has("SWEET WLAN: L22 event item 8: type 54 status 0 datalen 72 pktid 0x1 rx offset 0\n") && has("WIFI GAP: scan 8 event buffers posted (DHD posts 32) x1 [low]"));
	CHECK(has("SWEET WLAN: D4e dma_rxoffset (shared +0x24) 0\n") && !has("only 1 ioctl response buffer posted") && !has("only 1 buffer posted") && !has("STOP") && !has("WIFI GAP: scan no") && has("first event frame header hex 000000000000000000000000886c8001") && has("SWEET WLAN: D4e first escan result data hex 000000006d00000034120100"));
	/* the stores: ring-0 index 9 is response buffer id 5 in slot 1, 16 the first event post (id 0x21, slot 1), 23 the scan_ver request, 33 the escan START, 34 the first event re-post (id 0x28, slot 0) */
	CHECK(f.hb[r0 / 4u + 90u] == 0x0A80000Bu && f.hb[r0 / 4u + 91u] == 5u && f.hb[r0 / 4u + 94u] == (uint32_t)(PA + p->b[SWD_RESPBUF].off + 8192u));
	CHECK(f.hb[r0 / 4u + 160u] == 0x1180000Du && f.hb[r0 / 4u + 161u] == 0x21u && f.hb[r0 / 4u + 164u] == (uint32_t)(PA + p->b[SWD_EVBUF].off + 8192u));
	CHECK(f.hb[r0 / 4u + 230u] == 0x18800009u && f.hb[r0 / 4u + 231u] == 0xFFFEu && f.hb[r0 / 4u + 232u] == 262u && f.hb[r0 / 4u + 233u] == (0x103u | 15u << 16) && f.hb[r0 / 4u + 234u] == 15u && f.hb[r0 / 4u + 236u] == ilo && f.hb[r0 / 4u + 237u] == ihi);
	CHECK(f.hb[r0 / 4u + 240u] == 0x1980000Bu && f.hb[r0 / 4u + 241u] == 12u && f.hb[r0 / 4u + 244u] == (uint32_t)(PA + p->b[SWD_RESPBUF].off));
	CHECK(f.hb[r0 / 4u + 330u] == 0x22800009u && f.hb[r0 / 4u + 332u] == 263u && f.hb[r0 / 4u + 333u] == (0x108u | 86u << 16) && f.hb[r0 / 4u + 334u] == 86u);
	CHECK(f.hb[r0 / 4u + 340u] == 0x2380000Du && f.hb[r0 / 4u + 341u] == 0x28u && f.hb[r0 / 4u + 344u] == (uint32_t)(PA + p->b[SWD_EVBUF].off) && f.hb[r0 / 4u + 380u] == 0x2780000Du && f.hb[r0 / 4u + 381u] == 0x2Cu && f.hb[r0 / 4u + 384u] == (uint32_t)(PA + p->b[SWD_EVBUF].off + 4u * 8192u));
	CHECK(count_of("G2 store d4e payload word") == 25u);
	CHECK(f.hb[r0 / 4u + 250u] == 0x1A800009u && f.hb[r0 / 4u + 252u] == 262u && f.hb[r0 / 4u + 253u] == (0x104u | 12u << 16) && f.hb[r0 / 4u + 254u] == 12u && f.hb[r0 / 4u + 260u] == 0x1B80000Bu && f.hb[r0 / 4u + 261u] == 13u && f.hb[r0 / 4u + 264u] == (uint32_t)(PA + p->b[SWD_RESPBUF].off + 8192u));
	CHECK(f.hb[r0 / 4u + 270u] == 0x1C800009u && f.hb[r0 / 4u + 272u] == 262u && f.hb[r0 / 4u + 273u] == (0x105u | 45u << 16) && f.hb[r0 / 4u + 274u] == 45u && f.hb[r0 / 4u + 280u] == 0x1D80000Bu && f.hb[r0 / 4u + 281u] == 14u && f.hb[r0 / 4u + 284u] == (uint32_t)(PA + p->b[SWD_RESPBUF].off + 2u * 8192u));
	CHECK(f.hb[r0 / 4u + 290u] == 0x1E800009u && f.hb[r0 / 4u + 292u] == 263u && f.hb[r0 / 4u + 293u] == (0x106u | 45u << 16) && f.hb[r0 / 4u + 294u] == 45u && f.hb[r0 / 4u + 300u] == 0x1F80000Bu && f.hb[r0 / 4u + 301u] == 15u && f.hb[r0 / 4u + 304u] == (uint32_t)(PA + p->b[SWD_RESPBUF].off + 3u * 8192u));
	CHECK(f.hb[r0 / 4u + 310u] == 0x20800009u && f.hb[r0 / 4u + 312u] == 2u && f.hb[r0 / 4u + 313u] == 0x107u && f.hb[r0 / 4u + 314u] == 0u && f.hb[r0 / 4u + 320u] == 0x2180000Bu && f.hb[r0 / 4u + 321u] == 16u && f.hb[r0 / 4u + 324u] == (uint32_t)(PA + p->b[SWD_RESPBUF].off + 4u * 8192u));
	/* the payloads the fake dongle read: event_msgs_ext SET = ver 1, command 3, len 26, ONLY mask bit 69; escan = version 2, action 1, sync 0x1234, params version 2 length 72, bssid ff, bss_type 2, scan_type 1 (passive), -1 x4, channel_num 0 */
	{
		static const uint8_t ems[30] = { 1, 3, 26, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x20 };
		static const uint8_t esc[80] = { 2, 0, 0, 0, 1, 0, 0x34, 0x12, 2, 0, 72, 0, 0, 0, 0, 0, [48] = 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 2, 0, 1, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

		CHECK(memcmp(ge.ems, ems, 30) == 0 && memcmp(ge.esc, esc, 80) == 0 && ge.up == 1u && ge.nescan == 1u);
		CHECK(ge.escin == 86u && !has("escan limited"));      /* T-0fdd: below level 24 the escan is the all-channel 86 B payload, no limit gap */
		CHECK(ge.emsget[0] == 1u && ge.emsget[1] == 0u && ge.emsget[2] == 26u && ge.emsget[3] == 0u);
	}
	if (getenv("E_DBG")) { fprintf(stderr, "DBG pay %08x %08x %08x %08x %08x\n", f.hb[ib / 4u], f.hb[ib / 4u + 1u], f.hb[ib / 4u + 2u], f.hb[ib / 4u + 3u], f.hb[ib / 4u + 4u]); }
	CHECK(f.hb[ib / 4u] == 0x61637365u && f.hb[ib / 4u + 1u] == 0x0002006Eu && f.hb[ib / 4u + 2u] == 0x00010000u && f.hb[ib / 4u + 3u] == 0x00021234u && f.hb[ib / 4u + 4u] == 0x48u);
	/* the transcript of the unaligned frame header (rx offset 6) and scan_ver 3: the frame is found at buffer + 6, the version 3 goes into both version fields */
	gd_arm();
	ge.rxoff = 6u;
	ge.major = 3u;
	scan_run(p, &x, 23, &rc, "rxoff");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.rxoff == 6u && x.nbss == 3u && ge.esc[0] == 3u && ge.esc[8] == 3u && f.hb[ib / 4u + 1u] == 0x0003006Eu && f.hb[ib / 4u + 3u] == 0x00031234u);
	CHECK(has("SWEET WLAN: D4e dma_rxoffset (shared +0x24) 6\n") && has("SWEET WLAN: L23 escan start: version 3 action 1 sync 0x1234 passive all channels, status 0\n") && has("rx offset 6\n"));
	CHECK(x.rxfw == 0u && x.rxfw_state == 1u && has("SWEET WLAN: D4e max_host_rxbufs (shared +0x22) 0\n") && !has("WIFI GAP: shared max_host_rxbufs"));       /* patch 0357: read once, read-only; the word held 0 here (Linux would then use 255, swdp_rx_pick does) */
	gd_arm();
	ge.rxbufs = 255u;                                                                  /* patch 0357: the firmware advertises 255 at shared +0x22 */
	scan_run(p, &x, 21, &rc, "rxbufs");
	CHECK(rc == 0 && !x.d4dstop && x.rxfw == 255u && x.rxfw_state == 1u && has("SWEET WLAN: D4e max_host_rxbufs (shared +0x22) 255\n") && x.rxoff == 0u);
	gd_arm();
	ge.rxbufs = 0xFFFFu;                                                               /* all ones in the u16: a dead read, not a count: GAP, state FAIL, the run goes on (the pool stays 16) */
	scan_run(p, &x, 21, &rc, "rxbufsones");
	CHECK(rc == 0 && !x.d4dstop && x.rxfw_state == 2u && has("WIFI GAP: shared max_host_rxbufs (+0x22) unreadable or all ones (0xffff): the rx pool stays 16\n"));
	ge.rxbufs = 0u;
	/* levels 21 and 22 stop where they say; 20 is unchanged */
	gd_arm();
	scan_run(p, &x, 21, &rc, "l21");
	CHECK(rc == 0 && x.d4dlv == 21u && x.d4wr == 23u && x.d4rd == 6u && x.nbss == 0u && has("SWEET WLAN: D4d L21 done (ring-0 WR 23, ring-2 RD 6)\n") && !has("L22 scan_ver") && !has("SWEET WLAN: D4d L20 done (ring-0 WR 9, ring-2 RD 6)\n") == 0);
	gd_arm();
	scan_run(p, &x, 22, &rc, "l22");
	CHECK(rc == 0 && x.d4dlv == 22u && x.d4wr == 33u && x.d4rd == 16u && has("SWEET WLAN: D4d L22 done (ring-0 WR 33, ring-2 RD 16)\n") && has("L22 WLC_UP status 0") && !has("L23 escan start") && ge.nescan == 0u);
	/* an unsolicited event at the end of L21 (watched), a DS round between two L22 ioctls, and the DS round of the F1 log before L21 */
	gd_arm();
	ge.einj[0].n = 22u;
	ge.neinj = 1u;
	ge.einj[0].type = 7u;
	scan_run(p, &x, 21, &rc, "l21ev");
	CHECK(rc == 0 && x.d4dlv == 21u && x.d4rd == 7u && x.evn == 1u && x.evq == 1u && x.evp == 8u && has("SWEET WLAN: L21 event item 6: type 7 status 0 datalen 72 pktid 0x1 rx offset 0\n") && has("ring-0 WR 23, ring-2 new items 1\n"));
	gd_arm();
	gd.inj[0].n = 26u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	scan_run(p, &x, 23, &rc, "ds");
	CHECK(rc == 0 && !x.d4dstop && x.d4ds == 1u && x.d4dlv == 23u && x.d4wr == 38u + 2u && x.d4rd == 22u + 2u && x.nbss == 3u);
	CHECK(has("SWEET WLAN: D4 DS round 1 answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items 10..11, ring-0 items 27..28, ring-0 WR 27 -> 29, ring-2 RD 10 -> 12\n") && has("SWEET WLAN: L22 event_msgs_ext get: len 26 status 0\n") && has("SWEET WLAN: L23 scan done status 0 bss 3 channels 3\n"));
	CHECK(f.hb[r0 / 4u + 270u] == 0x1C80000Eu - 0x0Eu + 0x23u && f.hb[r0 / 4u + 272u] == 2u && f.hb[r0 / 4u + 280u] == 0x1D800023u && f.hb[r0 / 4u + 282u] == 0x40u && f.hb[r0 / 4u + 290u] == 0x1E800009u);      /* ack 27, wake 28, then the next ioctl post is item 29 (no deassert item) */
	gd_arm();
	gd.inj[0].n = 3u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	scan_run(p, &x, 23, &rc, "ds0");
	CHECK(rc == 0 && x.d4ds == 1u && x.d4dlv == 23u && x.d4wr == 40u && x.nbss == 3u && has("SWEET WLAN: D4d L20 done (ring-0 WR 11, ring-2 RD 8)\n") && has("ring-0 WR 25, ring-2 new items 0\n"));
	/* ten PARTIAL results over 8 channels (36 is above 31: the bitmap word 1), the channel from the chanspec when ctl_ch is 0, a hex SSID (non-printable bytes), a bss_count above 1 (only the first record is parsed) */
	gd_arm();
	ge.npartial = 10u;
	scan_run(p, &x, 23, &rc, "chan");
	CHECK(rc == 0 && x.d4dlv == 23u && x.nbss == 10u && has("SWEET WLAN: L23 scan done status 0 bss 10 channels 8\n") && has("SWEET WLAN: L23 bss 7 chan 250 rssi -47 chanspec 0x10fc ") && x.evn == 11u && x.evp == 19u && x.d4wr == 45u);
	gd_arm();
	ge.noctl = 1;
	ge.hexssid = 1;
	ge.bssn = 2u;
	scan_run(p, &x, 23, &rc, "noctl");
	CHECK(rc == 0 && x.d4dlv == 23u && has("SWEET WLAN: L23 bss 0 chan 3 rssi -40 chanspec 0x1003 bssid 02:11:22:33:44:50 ssid hex 01024142\n"));
	CHECK(has("SWEET WLAN: L23 bss 1 chan 8 rssi -41 chanspec 0x1008 bssid 02:11:22:33:44:51 ssid hex 01024142\n") && has("WIFI GAP: scan bss_count above 1, only the first record parsed x") && x.nbss == 3u);
	gd_arm();
	ge.sl33 = 1;
	scan_run(p, &x, 23, &rc, "sl33");
	CHECK(rc == 0 && has("ssid hex 6161616161616161") && !has("ssid \"a"));
	/* a record of exactly 89 B (through ctl_ch) is parsed, one of 88 B is not: a loud gap, no bss line */
	gd_arm();
	ge.reclen = 89u;
	scan_run(p, &x, 23, &rc, "rec89");
	CHECK(rc == 0 && x.d4dlv == 23u && x.nbss == 3u && !has("PARTIAL result shorter"));
	gd_arm();
	ge.reclen = 88u;
	scan_run(p, &x, 23, &rc, "rec88");
	CHECK(rc == 0 && x.d4dlv == 23u && x.nbss == 0u && has("WIFI GAP: scan PARTIAL result shorter than one bss record x1 [medium]") && has("scan done status 0 bss 0 channels 0\n"));
	/* an event of another type while the scan runs: consumed, re-posted, a gap */
	gd_arm();
	ge.einj[0].n = 33u;
	ge.einj[0].type = 54u;
	ge.einj[0].pre = 1;
	ge.neinj = 1u;
	scan_run(p, &x, 23, &rc, "oth");
	CHECK(rc == 0 && x.d4dlv == 23u && x.nbss == 3u && has("WIFI GAP: scan event other than a full ESCAN_RESULT seen x1 [medium]") && x.evn == 5u && x.d4wr == 39u);
	/* a DS request right behind an ioctl answer: the answer's items are judged, the request stays for the next idle look and is answered there */
	gd_arm();
	gd.inj[0].n = 27u;
	gd.inj[0].mb = 2u;
	gd.ninj = 1u;
	scan_run(p, &x, 23, &rc, "ds2");
	CHECK(rc == 0 && !x.d4dstop && x.d4ds == 1u && x.d4dlv == 23u && x.nbss == 3u && x.d4wr == 38u + 2u && has("SWEET WLAN: D4 DS round 1 answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items 12..13, ring-0 items 29..30, ring-0 WR 29 -> 31, ring-2 RD 12 -> 14\n"));
	/* all 8 event buffers delivered an event at the end of L21: one batch of 8 items */
	gd_arm();
	for (k = 0; k < 8u; k++) {
		ge.einj[k].n = 22u;
		ge.einj[k].type = 7u;
	}
	ge.neinj = 8u;
	scan_run(p, &x, 21, &rc, "burst");
	CHECK(rc == 0 && x.d4dlv == 21u && x.d4rd == 14u && x.evn == 8u && x.evq == 8u && has("ring-0 WR 23, ring-2 new items 8\n"));
	/* SSIDs: a space is printable, DEL is not, 33 bytes are too long for an SSID (hex of the first 32), bss_count 0 on a PARTIAL (no record parsed), a SUCCESS result of 11 B is not a full ESCAN_RESULT */
	gd_arm();
	ge.ssid2 = 1;
	scan_run(p, &x, 23, &rc, "ssid2");
	CHECK(rc == 0 && has("ssid \" a\"\n"));
	gd_arm();
	ge.ssid3 = 1;
	scan_run(p, &x, 23, &rc, "ssid3");
	CHECK(rc == 0 && has("ssid hex 7f\n") && !has("ssid \"\x7f"));
	gd_arm();
	ge.bssn0 = 1;
	scan_run(p, &x, 23, &rc, "bssn0");
	CHECK(rc == 0 && x.nbss == 0u && has("WIFI GAP: scan PARTIAL result shorter than one bss record x1 [medium]"));
	gd_arm();
	ge.sdlen11 = 1;
	scan_run(p, &x, 23, &rc, "sdlen11");
	CHECK(rc == 0 && x.d4dlv == 22u && x.nbss == 3u && has("WIFI GAP: scan event other than a full ESCAN_RESULT seen x1 [medium]") && has("WIFI GAP: scan no SUCCESS in 8000 ms x1 [high]"));
	/* frame bytes flipped one at a time: the OUI (3 bytes) and the usr_subtype (2 bytes) are checked, not only the ethertype */
	{
		static const unsigned fb[5] = { 19u, 20u, 21u, 22u, 23u };

		for (k = 0; k < 5u; k++) {
			gd_arm();
			ge.fault = EF_FLIP;
			ge.fault_n = 1u;
			ge.fb = fb[k];
			scan_run(p, &x, 23, &rc, "flip");
			CHECK(rc == 0 && x.d4dstop && x.d4dlv == 22u && has("SWEET WLAN: L23 STOP, no further store: event frame ethertype 0x886c oui "));
		}
	}
	/* dma_rxoffset: all ones (a dead read) is a hard stop; 9000 (above the buffer) and a data length of 8192 with offset 6 are soft stops with the offset in the line */
	gd_arm();
	ge.rxtcm = 0xFFFFFFFFu;
	scan_run(p, &x, 21, &rc, "rxdead");
	CHECK(rc < 0 && x.code == SWG_E_DEAD && x.restore == 1 && x.d4dlv == 20u);
	gd_arm();
	ge.rxtcm = 9000u;
	scan_run(p, &x, 23, &rc, "rxhigh");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 22u && has("SWEET WLAN: L23 STOP, no further store: event data length 180, rx offset 9000\n"));
	gd_arm();
	ge.rxoff = 6u;
	ge.fault = EF_DLENMAX;
	ge.fault_n = 1u;
	scan_run(p, &x, 23, &rc, "dlenmax");
	CHECK(rc == 0 && x.d4dstop && has("SWEET WLAN: L23 STOP, no further store: event data length 8192, rx offset 6\n"));
	/* an event between two ioctls (nobody is waiting): the idle look consumes it; a D2H-WR slot far behind the read count is a stop there and in the L23 poll */
	gd_arm();
	ge.einj[0].n = 24u;
	ge.einj[0].type = 54u;
	ge.neinj = 1u;
	scan_run(p, &x, 23, &rc, "idle");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.evn == 5u && has("SWEET WLAN: L22 event item 8: type 54 status 0 datalen 72 pktid 0x1 rx offset 0\n") && x.d4wr == 39u);
	gd_arm();
	ge.einj[0].n = 24u;
	ge.einj[0].type = 0xFFu;
	ge.neinj = 1u;
	scan_run(p, &x, 23, &rc, "idlelow");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 21u && has("SWEET WLAN: L22 STOP, no further store: d2h write index 1 went below the read count 8\n"));
	gd_arm();
	ge.npartial = 0u;
	ge.pfin = -2;
	ge.einj[0].n = 33u;
	ge.einj[0].type = 54u;
	ge.einj[0].pre = 1;
	ge.einj[1].n = 34u;
	ge.einj[1].type = 0xFFu;
	ge.neinj = 2u;
	scan_run(p, &x, 23, &rc, "l23low");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 22u && has("SWEET WLAN: L23 STOP, no further store: d2h write index 1 went below the read count 19\n"));
	/* swg_d4d_st past the table: level 7 makes no more rows even with a matching operation; level 8 needs the operation's kind; a payload longer than the shadow is refused with no store */
	{
		uint8_t pay[4u * SWG_E_PAYW + 4u] = { 0 };

		d4d_cfg(20, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4dn = 65u;
		x.eop = SWG_OP_POST;
		x.erow = 11u;
		CHECK(swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000000u | 20u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u);
		d4d_cfg(21, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4dn = 65u;
		x.eop = SWG_OP_POST;
		x.erow = 0u;
		CHECK(swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000000u | 20u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u);
		d4d_cfg(21, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4dn = 65u;
		x.eop = SWG_OP_POST;
		x.erow = 11u;
		rc = swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000006u, 0, "t");
		CHECK(rc == 0 && x.nstore == 1u && x.erow == 12u);
		d4d_cfg(21, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		CHECK(swg_e_pay(&x, pay, 4u * SWG_E_PAYW + 4u) == -SWG_E_ORDER && x.nstore == 0u);
	}
	/* no scan result at all / results but no SUCCESS inside the 8000 ms budget: the gap, polling ends, level 23 is not done */
	gd_arm();
	ge.npartial = 0u;
	ge.pfin = -2;
	scan_run(p, &x, 23, &rc, "quiet");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 22u && x.nbss == 0u && has("SWEET WLAN: L23 scan not done after 8000 ms: 0 PARTIAL results, 0 bss\n") && has("WIFI GAP: scan no ESCAN_RESULT in 8000 ms x1 [high]") && !has("scan done status"));
	gd_arm();
	ge.npartial = 2u;
	ge.pfin = -2;
	scan_run(p, &x, 23, &rc, "nosucc");
	CHECK(rc == 0 && x.d4dlv == 22u && x.nbss == 2u && has("L23 scan not done after 8000 ms: 2 PARTIAL results, 2 bss\n") && has("WIFI GAP: scan no SUCCESS in 8000 ms x1 [high]"));
	/* one fault at a time: (fault, n, expected text, hard stop = rc < 0). Soft stops end the run with rc 0 and d4dlv below the level */
	{
		static const struct { int fault; uint32_t n; int hard; const char *msg; } fc[] = {
			{ EF_PKTID, 1u, 0, "SWEET WLAN: L23 STOP, no further store: event pktid 0x2 is not a posted event buffer\n" },
			{ EF_STALEID, 2u, 0, "SWEET WLAN: L23 STOP, no further store: event pktid 0x1 is not a posted event buffer\n" },
			{ EF_ID0, 2u, 0, "SWEET WLAN: L23 STOP, no further store: event pktid 0 is not a posted event buffer\n" },
			{ EF_ETYPE, 1u, 0, "SWEET WLAN: L23 STOP, no further store: event frame ethertype 0x86c oui 001018\n" },
			{ EF_DLEN, 1u, 0, "SWEET WLAN: L23 STOP, no further store: event data length 40, rx offset 0\n" },
			{ EF_EDLEN, 1u, 0, "SWEET WLAN: L23 STOP, no further store: event datalen 4108 above the frame data 108\n" },
			{ EF_EPOCH, 16u, 0, "SWEET WLAN: L23 STOP, no further store: ring-2 item 16 epoch/xor check failed (1)\n" },
			{ EF_STATUS, 3u, 0, "SWEET WLAN: L22 STOP, no further store: ring-2 item 7 type 0xc status -5 != 0\n" },
			{ EF_STATUS, 8u, 0, "SWEET WLAN: L23 STOP, no further store: ring-2 item 17 type 0xc status -5 != 0\n" },
			{ EF_ACKSTATUS, 4u, 0, "SWEET WLAN: L22 STOP, no further store: ring-2 item 8 type 0xa status -1 != 0\n" },
			{ EF_CMD, 5u, 0, "SWEET WLAN: L22 STOP, no further store: ring-2 item 10 cmd 263, expected 262\n" },
			{ EF_ACKRID, 3u, 0, "SWEET WLAN: L22 STOP, no further store: ack request_id 0xfffd, expected 0xfffe\n" },
			{ EF_DUPACK, 3u, 0, "SWEET WLAN: L22 STOP, no further store: second ioctl ack at ring-2 item 7\n" },
			{ EF_TRANS, 3u, 0, "SWEET WLAN: L22 STOP, no further store: completion trans_id 0x104, expected 0x103\n" },
			{ EF_CRID, 3u, 0, "SWEET WLAN: L22 STOP, no further store: completion request_id 104 is not a posted response buffer\n" },
			{ EF_CRIDUSED, 3u, 0, "SWEET WLAN: L22 STOP, no further store: completion request_id 3 is not a posted response buffer\n" },
			{ EF_CRIDUSED, 5u, 0, "SWEET WLAN: L22 STOP, no further store: completion request_id 3 is not a posted response buffer\n" },
			{ EF_BIGLEN, 3u, 0, "SWEET WLAN: L22 STOP, no further store: completion resp_len 8193 above the 8192 B buffer\n" },
			{ EF_DUPCPL, 3u, 0, "SWEET WLAN: L22 STOP, no further store: second ioctl completion at ring-2 item 8\n" },
			{ EF_TYPE, 3u, 0, "SWEET WLAN: L22 STOP, no further store: unexpected ring-2 type 0x10 at item 7\n" },
			{ EF_NOANS, 3u, 0, "SWEET WLAN: L22 STOP, no further store: no ioctl answer in 1900 ms\n" },
			{ EF_NOCPL, 3u, 0, "SWEET WLAN: L22 STOP, no further store: no ioctl completion in 1900 ms\n" },
			{ EF_LOWW, 3u, 0, "SWEET WLAN: L22 STOP, no further store: d2h write index 1 went below the read count 6\n" },
			{ EF_SCANVER_SHORT, 3u, 0, "SWEET WLAN: L22 STOP, no further store: scan_ver answer is 4 B, not 6\n" },
			{ EF_MAJOR, 3u, 0, "SWEET WLAN: L22 STOP, no further store: scan_ver major 5 is not 2 or 3, no escan\n" },
			{ EF_MAJOR1, 3u, 0, "SWEET WLAN: L22 STOP, no further store: scan_ver major 1 is not 2 or 3, no escan\n" },
			{ EF_MAJOR4, 3u, 0, "SWEET WLAN: L22 STOP, no further store: scan_ver major 4 is not 2 or 3, no escan\n" },
			{ EF_MAJOR0, 3u, 0, "SWEET WLAN: L22 STOP, no further store: scan_ver major 0 is not 2 or 3, no escan\n" },
			{ EF_MAJORHI, 3u, 0, "SWEET WLAN: L22 STOP, no further store: scan_ver major 258 is not 2 or 3, no escan\n" },
			{ EF_EMS_LEN, 5u, 0, "SWEET WLAN: L22 STOP, no further store: event_msgs_ext answer 30 B len 25, expected 30 B len 26\n" },
			{ EF_EMS_SHORT, 5u, 0, "SWEET WLAN: L22 STOP, no further store: event_msgs_ext answer 20 B len 26, expected 30 B len 26\n" },
		};

		for (k = 0; k < sizeof fc / sizeof fc[0]; k++) {
			gd_arm();
			ge.fault = fc[k].fault;
			ge.fault_n = fc[k].n;
			scan_run(p, &x, 23, &rc, "fault");
			if (!has(fc[k].msg)) {
				fprintf(stderr, "scan fault %u (%d, n %u): text missing: %s", k, fc[k].fault, fc[k].n, fc[k].msg);
				if (getenv("E_DBG")) { fprintf(stderr, "%s", logbuf + (loglen > 6000u ? loglen - 6000u : 0u)); }
			}
			CHECK(x.d4dstop && x.restore == 1 && has(fc[k].msg) && has("G2 RESULT") == 0 && x.d4dlv < 23u);
			CHECK(rc == 0 && has("WIFI GAP: scan "));
		}
	}
	/* T-bd94: a non-zero completion status dumps the request the host posted and the response bytes of the buffer the dongle used, then stops (boot 6 of T-a0aa: escan -26, 86 B) */
	gd_arm();
	ge.fault = EF_ESCAN26;
	ge.fault_n = 0u;
	scan_run(p, &x, 23, &rc, "escan26");
	CHECK(rc == 0 && x.d4dstop && x.restore == 1 && x.d4dlv < 23u && ge.nescan == 1u);
	CHECK(has("SWEET WLAN: L23 STOP, no further store: ring-2 item ") && has("type 0xc status -26 != 0\n"));
	CHECK(has("WIFI GAP: scan ioctl cmd 263 status -26: bytes dumped x1 [high]"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 request bytes 0..31 of 86 hex 657363616e000200000001003412020048000000000000000000000000000000\n"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 request bytes 32..63 of 86 hex 00000000000000000000000000000000000000000000ffffffffffff02000100\n"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 request bytes 64..85 of 86 hex 0000ffffffffffffffffffffffffffffffff00000000\n"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 response bytes 0..31 of 86 hex a0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebf\n"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 response bytes 32..63 of 86 hex c0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedf\n"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 response bytes 64..85 of 86 hex e0e1e2e3e4e5e6e7e8e9eaebecedeeeff0f1f2f3f4f5\n"));
	CHECK(has("SWEET WLAN: D4e failed ioctl cmd 263 status -26 resp_len 86 buffer slot "));
	/* an earlier failing ioctl with no response bytes (EF_STATUS on the scan_ver request, resp_len 6): dumped too, the L22 STOP unchanged */
	gd_arm();
	ge.fault = EF_STATUS;
	ge.fault_n = 3u;
	scan_run(p, &x, 23, &rc, "status22");
	CHECK(rc == 0 && x.d4dstop && has("WIFI GAP: scan ioctl cmd 262 status -5: bytes dumped x1 [high]") && has("SWEET WLAN: D4e failed ioctl cmd 262 request bytes 0..") && has("SWEET WLAN: D4e failed ioctl cmd 262 response bytes 0..5 of 6 hex "));
	/* a failed ACK (EF_ACKSTATUS) is not a completion: no dump */
	gd_arm();
	ge.fault = EF_ACKSTATUS;
	ge.fault_n = 4u;
	scan_run(p, &x, 23, &rc, "ackstatus");
	CHECK(rc == 0 && x.d4dstop && has("type 0xa status -1 != 0\n") && !has("failed ioctl cmd") && !has("bytes dumped"));
	/* a failed ioctl whose completion names no posted buffer: the request is dumped, the response is a gap, the STOP follows */
	gd_arm();
	ge.fault = EF_STATUS;
	ge.fault_n = 3u;
	ge.crid_bad = 1;
	scan_run(p, &x, 23, &rc, "status22crid");
	CHECK(rc == 0 && x.d4dstop && has("WIFI GAP: scan failed ioctl: request_id not posted, no response dump x1 [high]") && has("SWEET WLAN: D4e failed ioctl cmd 262 request bytes 0..") && !has("failed ioctl cmd 262 response bytes"));
	/* T-af77: the dongle chooses the response buffer (boot 3 of T-382c: request_id 11 when ids 4..11 were posted): the newest posted buffer answers every request, the whole run goes through, each answer is read from the buffer of the id it carries */
	gd_arm();
	ge.lifo = 1;
	scan_run(p, &x, 23, &rc, "lifo");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.restore == 1 && x.nbss == 3u && x.ncpl == 8u && x.ereq == 6u && !has("STOP"));
	CHECK(has("SWEET WLAN: L22 scan_ver version 1 length 6 major 2\n") && has("SWEET WLAN: L22 sup_wpa get: status 0 value 0\n") && has("SWEET WLAN: L22 event_msgs_ext get: len 26 status 0\n") && has("SWEET WLAN: L23 scan done status 0 bss 3 channels 3\n"));
	CHECK(x.rpid[0] == 4u && x.rpid[1] == 5u && x.rpid[2] == 6u && x.rpid[3] == 7u && x.rpid[4] == 8u && x.rpid[5] == 9u && x.rpid[6] == 10u && x.rpid[7] == 0u);       /* ids 11, 12 .. 15 answered in slot 7 and re-posted there, the escan took id 16 (slot 7) and it is not re-posted */
	CHECK(has("SWEET WLAN: D4e respbuf post: id 12 buffer 7,") && has("SWEET WLAN: D4e respbuf post: id 16 buffer 7,"));
	/* T-fad2: the dongle chooses the event buffer too (boot 8 of T-fad2: pktid 0x27, the newest of 1, 0x21..0x27, came first): the newest posted buffer takes every event, the whole run goes through, each consumed buffer is re-posted in ITS slot under a new id */
	gd_arm();
	ge.lifo = 1;            /* the firmware of boot 8 chose the newest response buffer as well (T-af77) */
	ge.evlifo = 1;
	scan_run(p, &x, 23, &rc, "evlifo");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.restore == 1 && x.nbss == 3u && x.ncpl == 8u && x.ereq == 6u && x.evn == 4u && x.evq == 0u && x.evp == 12u && !has("STOP"));
	CHECK(has("SWEET WLAN: L23 scan done status 0 bss 3 channels 3\n") && has("pktid 0x27 rx offset 0\n") && has("SWEET WLAN: D4e evbuf post: id 40 buffer 7,"));
	CHECK(x.epid[0] == 1u && x.epid[1] == 0x21u && x.epid[2] == 0x22u && x.epid[3] == 0x23u && x.epid[4] == 43u && x.epid[5] == 42u && x.epid[6] == 41u && x.epid[7] == 40u && has("SWEET WLAN: D4e evbuf post: id 41 buffer 6,") && has("SWEET WLAN: D4e evbuf post: id 43 buffer 4,"));       /* the 4 events took 0x27, 0x26, 0x25, 0x24 (slots 7..4), each slot re-posted under ids 40..43 */
	/* the event cap: the 65th event is a STOP, no re-post for it, the highest ring-0 index is 97 (the design maximum) */
	gd_arm();
	ge.fault = EF_CAP;
	scan_run(p, &x, 23, &rc, "cap");
	CHECK(rc == 0 && x.d4dstop && x.evn == 64u && x.d4wr == 98u && x.restore == 1 && has("SWEET WLAN: L23 STOP event cap 64 reached, no further store\n") && has("WIFI GAP: scan event cap 64 reached x1 [high]") && x.d4dlv == 22u);
	/* a record version other than 109, a final status other than 0 / 8, a foreign sync_id, a sup_wpa answer with a status: recorded, loud, the run goes on or ends as said */
	gd_arm();
	ge.fault = EF_BSSVER;
	scan_run(p, &x, 23, &rc, "bssver");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.nbss == 0u && has("SWEET WLAN: L23 bss_info record version 110, expected 109: not parsed\n") && has("WIFI GAP: scan bss_info version is not 109, not parsed x1 [high]") && has("scan done status 0 bss 0 channels 0\n"));
	gd_arm();
	ge.fault = EF_STATUS4;
	scan_run(p, &x, 23, &rc, "st4");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 22u && x.nbss == 3u && has("WIFI GAP: scan status 4 in an ESCAN_RESULT, scan ended x1 [high]") && !has("scan done status") && !has("L23 scan not done") && has("SWEET WLAN: L23 scan ended by another status after 3 PARTIAL results, 3 bss\n"));
	gd_arm();
	ge.fault = EF_SYNC;
	scan_run(p, &x, 23, &rc, "sync");
	CHECK(rc == 0 && x.d4dlv == 22u && x.nbss == 0u && has("SWEET WLAN: L23 escan event 69 status 8 sync 0x4321 (ours 0x1234), ignored\n") && has("WIFI GAP: scan ESCAN_RESULT sync_id differs from ours x") && has("WIFI GAP: scan no ESCAN_RESULT in 8000 ms"));
	gd_arm();
	ge.supst = -5;
	ge.supwpa = 9u;
	scan_run(p, &x, 23, &rc, "supwpa");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && has("SWEET WLAN: L22 sup_wpa get: status -5 value 9\n") && has("WIFI GAP: scan sup_wpa get status non-zero or answer short x1 [medium]"));
	/* an ioctl ack nobody asked for during the L21 watch, a second event pktid out of FIFO order, a 0x24 item with another bit */
	gd_arm();
	ge.einj[0].n = 22u;
	ge.einj[0].type = 0xAu;
	ge.neinj = 1u;
	scan_run(p, &x, 21, &rc, "noreq");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 20u && has("SWEET WLAN: L21 STOP, no further store: ring-2 item 6 (type 0xa) but no request was sent\n"));
	/* the first request that is a hard stop: a payload word whose old value is not the shadow (the dongle-side buffer was written by something else) */
	gd_arm();
	d4d_cfg(23, IO_BOTH, 0, 0);
	mk(&x, p, F_OK);
	f.hb[ib / 4u + 1u] = 0x55u;
	rc = swg_go(&x);
	gd.on = 0;
	CHECK(rc < 0 && x.code == SWG_E_OLD && x.restore == 1 && x.d4dlv == 21u && has("d4e payload word"));
	gd.on = 0;
}

static void
d4d_tests(struct swd_plan *p)
{
	struct swg_wx x;
	const uint32_t r0 = p->b[SWD_H2D_CTRL].off, hw0 = p->b[SWD_IDX_H2D_W].off, dr0 = p->b[SWD_IDX_D2H_R].off, ib = p->b[SWD_IOCTBUF].off;
	const uint32_t rlo = (uint32_t)(PA + p->b[SWD_RESPBUF].off), rhi = (uint32_t)((PA + p->b[SWD_RESPBUF].off) >> 32), ilo = (uint32_t)(PA + ib), ihi = (uint32_t)((PA + ib) >> 32);
	/* the expected stores, restated with literals from docs/research/W-D4d-ioctl-get-version.md s.2.1/2.2 (every value counted by hand): ring-0 item idx at r0 + 40 * idx */
	struct rec c[72];
	unsigned n = 0, k, lv, i;
	int rc;
	char want[320];

#define H(ad_, vl_) (c[n].sp = SWG_SP_HOST, c[n].a = (ad_), c[n].v = (vl_), n++)
#define ITEM(idx, w0, w1, w2, w3, w4, w5, w6, w7) do { uint32_t b_ = r0 + 40u * (idx); const uint32_t w_[10] = { w0, w1, w2, w3, w4, w5, w6, w7, 0u, 0u }; for (k = 0; k < 10u; k++) { H(b_ + 4u * k, w_[k]); } } while (0)
#define DB(vl_) (c[n].sp = SWG_SP_PCIE, c[n].a = 0x140u, c[n].v = (vl_), n++)
#define POST(idx, w0, w1, w2, w3, w4, w5, w6, w7) do { ITEM(idx, w0, w1, w2, w3, w4, w5, w6, w7); H(hw0, (idx) + 1u); DB(0xFF000000u | ((idx) + 1u)); } while (0)
	POST(4u, 0x0580000Bu, 2u, 8192u, 0u, rlo, rhi, 0u, 0u);                                 /* L17: type 0x0B, flags 0x80, epoch (254 + 4) % 253 = 5, request_id 2, host_buf_len 8192 */
	POST(5u, 0x06800009u, 0xFFFEu, 1u, 4u << 16 | 0x0101u, 4u, 0u, ilo, ihi);                 /* L18: type 0x09, epoch 6, request_id 0xFFFE, cmd 1, trans_id 0x101 | input_buf_len 4 << 16, output_buf_len 4, payload pa */
	H(dr0, 4u); DB(0xDD020004u);                                                            /* L19: ring-2 read index 4, doorbell 0xDD02 0004 */
	POST(6u, 0x0780000Bu, 3u, 8192u, 0u, rlo, rhi, 0u, 0u);                                 /* the re-post: epoch 7, request_id 3 */
	H(ib, 0x00706163u);                                                                     /* L20: "cap\0" */
	POST(7u, 0x08800009u, 0xFFFEu, 262u, 1023u << 16 | 0x0102u, 1023u, 0u, ilo, ihi);        /* GET_VAR: epoch 8, cmd 262, trans_id 0x102, input_buf_len 1023, output_buf_len 1023 */
	H(dr0, 6u); DB(0xDD020006u);                                                            /* ring-2 read index 6 */
	POST(8u, 0x0980000Bu, 4u, 8192u, 0u, rlo, rhi, 0u, 0u);                                 /* epoch 9, request_id 4 */
#undef POST
#undef DB
#undef ITEM
#undef H
	CHECK(n == 65u);
	/* the word offsets in the expected table are host block offsets: ib is the payload word, hw0 / dr0 the index slots */

	/* the clean run of every level: the stores of the level and none of the next, in this exact order, the expected end state in host memory */
	{
		static const unsigned rows[4] = { 12u, 24u, 38u, 65u };
		static const unsigned total[4] = { 111u, 123u, 137u, 164u };

		for (lv = 17; lv <= 20; lv++) {
			d4d_run(p, &x, (int)lv, IO_BOTH, 0, 0, &rc);
			if (rc != 0 || x.nstore != total[lv - 17u]) {
				fprintf(stderr, "d4d level %u: rc %d stores %u\n%s", lv, rc, x.nstore, logbuf);
			}
			CHECK(rc == 0 && x.nstore == total[lv - 17u] && x.restore == 1 && x.d4dn == rows[lv - 17u] && !x.d4dstop && f.nunm == 1u && f.c34 == 0u);
			CHECK(d4d_stores(c, rows[lv - 17u], 3u));
			CHECK(x.d4dlv == lv);       /* review r2 F4: the level-done field names the level reached */
			CHECK(f.nrdwin == 0u);     /* the window of the d4c handshake ended before the first d4d store */
			CHECK(!has("WIFI GAP: d4c") && !has("STOP") && has("D4c DS handshake done"));
			CHECK(has("WIFI GAP: d4d only 1 ioctl response buffer posted (DHD posts 8) x1 [low]") && has("WIFI GAP: d4d ring-0 posts without a DS wake assert (stock asserts, U6) x1 [medium]"));
			CHECK(d4d_longest_line() < 200u);
			CHECK(f.hb[hw0 / 4u] == (lv == 17u ? 5u : lv == 18u ? 6u : lv == 19u ? 7u : 9u) && f.hb[dr0 / 4u] == (lv <= 18u ? 2u : lv == 19u ? 4u : 6u));
			CHECK(ndb_d4d == (lv == 17u ? 1u : lv == 18u ? 2u : lv == 19u ? 4u : 7u));
			CHECK(!has("WIFI GAP: d4d unexpected") && !has("no ioctl answer") && !has("D4d STOP") && f.nrd140 == 0u);
		}
	}
	/* L17: nothing arrives in the watch (19 waits of 100 ms after the L14 poll and the wake): the printed expectation, no gap beyond the two standing ones */
	d4d_run(p, &x, 17, IO_BOTH, 0, 0, &rc);
	CHECK(has("SWEET WLAN: D4d L17 ioctl-response buffer post: id 2, 8192 B at pa ") && has("ring-0 idx 4 epoch 5, H2D-WR 5, doorbell 0xff000005\n"));
	CHECK(has("SWEET WLAN: D4d L17 watch after 1900 ms: d2h_w0 2\n") && has("SWEET WLAN: D4d L17 no new ring-2 item in 1900 ms (expected: no request sent)\n") && has("SWEET WLAN: D4d L17 done (ring-0 WR 5, ring-2 RD 2)\n") && !has("D4d L18 done"));
	CHECK(f.ndelay == 39u && f.msdelay == 3801u && io_db[0] == 0xFF000005u && ndb_d4d == 1u);
	CHECK(f.hb[r0 / 4u + 40u] == 0x0580000Bu && f.hb[r0 / 4u + 41u] == 2u && f.hb[r0 / 4u + 42u] == 8192u && f.hb[r0 / 4u + 44u] == rlo && f.hb[r0 / 4u + 45u] == rhi && f.hb[r0 / 4u + 43u] == 0u && f.hb[r0 / 4u + 46u] == 0u && f.hb[r0 / 4u + 49u] == 0u);
	CHECK(f.hb[r0 / 4u + 39u] == 0u && f.hb[r0 / 4u + 50u] == 0u);       /* ring-0 item 3 (the deassert) untouched in its last word, item 5 not written at L17 */
	/* L17: an item nobody asked for (an ack; a D2H_MAILBOX_DATA = the U2 DS_ENTER_REQ) is printed raw and stops the later levels with a gap; the post (12 rows) was made, nothing else */
	d4d_run(p, &x, 20, IO_BOTH, 0, 1, &rc);
	CHECK(rc == 0 && x.nstore == 111u && x.d4dstop && x.d4dn == 12u && has("WIFI GAP: d4d ring 2 holds 1 new items, expected 0 x1 [high]") && has("WIFI GAP: d4d ring-2 item 2 (type 0xa) but no request was sent x1 [high]"));
	CHECK(has("SWEET WLAN: D4d ring2 item 2 raw 0x3800a 0x") == 0 && has("SWEET WLAN: D4d ring2 item 2 raw 0x") && ndb_d4d == 1u);
	/* 22b: a D2H_MAILBOX_DATA item at the front of ring 2 is a DS request, answered by a round (ds_tests below), not a gap any more */

	/* L18: ack and completion together / one by one; both parsed, no read-index update, no re-post */
	d4d_run(p, &x, 18, IO_BOTH, 0, 0, &rc);
	CHECK(has("SWEET WLAN: D4d L18 done (ring-0 WR 6, ring-2 RD 2)\n") && !has("D4d L17 done") && !has("D4d L19 done"));
	CHECK(has("SWEET WLAN: D4d request WLC_GET_VERSION: cmd 1 trans_id 0x101 in/out len 4 payload pa ") && has("ring-0 idx 5 epoch 6, H2D-WR 6, doorbell 0xff000006\n"));
	CHECK(has("SWEET WLAN: D4d answer poll after 0 ms: d2h_w0 4 (base 2, want 4)\n"));
	CHECK(has("SWEET WLAN: D4d ring2 item 2 type 0xa epoch 0x3 request_id 0xfffe status 0 ring_id 2 cmd 1 trans_id 0x0 resp_len 0\n"));
	CHECK(has("SWEET WLAN: D4d ring2 item 3 type 0xc epoch 0x4 request_id 0x2 status 0 ring_id 2 cmd 1 trans_id 0x101 resp_len 4\n"));
	CHECK(has("SWEET WLAN: D4d ring2 item 2 raw 0x0380000a 0x0000fffe 0x00020000 0x00000001 0x00000000 0x") && has("SWEET WLAN: D4d ring2 item 3 raw 0x0480000c 0x00000002 0x00020000 0x01010004 0x00000001 0x") && has("epoch/xor check 0\n"));
	CHECK(!has("D4d consume") && !has("D4d re-post") && !has("response bytes") && ndb_d4d == 2u && f.hb[dr0 / 4u] == 2u);
	CHECK(f.hb[r0 / 4u + 50u] == 0x06800009u && f.hb[r0 / 4u + 51u] == 0xFFFEu && f.hb[r0 / 4u + 52u] == 1u && f.hb[r0 / 4u + 53u] == 0x00040101u && f.hb[r0 / 4u + 54u] == 4u && f.hb[r0 / 4u + 55u] == 0u &&
	      f.hb[r0 / 4u + 56u] == ilo && f.hb[r0 / 4u + 57u] == ihi && f.hb[r0 / 4u + 58u] == 0u && f.hb[r0 / 4u + 59u] == 0u);
	d4d_run(p, &x, 18, IO_ONEBY, 0, 0, &rc);
	CHECK(rc == 0 && x.nstore == 123u && !x.d4dstop && has("SWEET WLAN: D4d answer poll after 100 ms: d2h_w0 4 (base 2, want 4)\n") && has("SWEET WLAN: D4d ring2 item 3 type 0xc epoch 0x4 request_id 0x2 status 0 ring_id 2 cmd 1 trans_id 0x101 resp_len 4\n") && !has("WIFI GAP: d4d"
	      " ioctl"));
	/* L19: the response bytes (the int 0x0B0A0504, little endian), the read index and the re-post */
	d4d_run(p, &x, 19, IO_BOTH, 0, 0, &rc);
	CHECK(has("SWEET WLAN: D4d L19 done (ring-0 WR 7, ring-2 RD 4)\n") && !has("D4d L18 done"));
	CHECK(has("SWEET WLAN: D4d WLC_GET_VERSION resp_len 4\n") && has("SWEET WLAN: D4d response bytes 0..3 hex 04050a0b\n") && has("SWEET WLAN: D4d consume: D2H-RD 2 -> 4, doorbell 0xdd020004\n"));
	CHECK(has("SWEET WLAN: D4d re-post: ioctl-response buffer id 3, ring-0 idx 6 epoch 7, H2D-WR 7, doorbell 0xff000007\n"));
	CHECK(io_db[0] == 0xFF000005u && io_db[1] == 0xFF000006u && io_db[2] == 0xDD020004u && io_db[3] == 0xFF000007u && strstr(logbuf, "response bytes 0..3") < strstr(logbuf, "D4d consume"));
	/* L19 completion with resp_len 0: a gap, still consumed (nothing to print) */
	d4d_run(p, &x, 19, IO_ZEROLEN, 0, 0, &rc);
	CHECK(rc == 0 && x.nstore == 137u && has("WIFI GAP: d4d completion resp_len is 0, no response bytes x1 [high]") && has("SWEET WLAN: D4d WLC_GET_VERSION resp_len 0\n"));
	/* L20: the cap string with sup_wpa and idsup, with only one, with neither */
	d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
	CHECK(rc == 0 && x.nstore == 164u && has("SWEET WLAN: D4d request WLC_GET_VAR cap: cmd 262 trans_id 0x102 in/out len 1023 payload pa ") && has("ring-0 idx 7 epoch 8, H2D-WR 8, doorbell 0xff000008\n"));
	CHECK(has("SWEET WLAN: D4d ring2 item 4 type 0xa epoch 0x5 request_id 0xfffe status 0 ring_id 2 cmd 262 trans_id 0x0 resp_len 0\n") && has("SWEET WLAN: D4d ring2 item 5 type 0xc epoch 0x6 request_id 0x3 status 0 ring_id 2 cmd 262 trans_id 0x102 resp_len 27\n"));
	CHECK(has("SWEET WLAN: D4d cap 27 bytes read, text 26 chars up to the first NUL\n") && has("SWEET WLAN: D4d cap[0..25]  802.11 sup_wpa idsup wme \n") && has("SWEET WLAN: D4d cap supplicant tokens: sup_wpa present idsup present\n") && !has("neither sup_wpa nor idsup"));
	CHECK(has("SWEET WLAN: D4d consume: D2H-RD 4 -> 6, doorbell 0xdd020006\n") && has("SWEET WLAN: D4d re-post: ioctl-response buffer id 4, ring-0 idx 8 epoch 9, H2D-WR 9, doorbell 0xff000009\n") && f.hb[ib / 4u] == 0x00706163u);
	CHECK(has("SWEET WLAN: D4d L20 done (ring-0 WR 9, ring-2 RD 6)\n") && has("SWEET WLAN: D4d respbuf word0 before cap 0xb0a0504\n") && !has("respbuf word 0 unchanged"));
	{
		static const struct { const char *cap, *line, *gap; } cp[] = {
			{ " a sup_wpa b ", "sup_wpa present idsup absent", NULL },
			{ " a idsup b ", "sup_wpa absent idsup present", NULL },
			{ " a sup_wpa2 idsupx xsup_wpa b ", NULL, "WIFI GAP: d4d cap has neither sup_wpa nor idsup x1 [high]" },
			{ "sup_wpa", "sup_wpa present idsup absent", NULL },
			{ "idsup", "sup_wpa absent idsup present", NULL },
			{ "", NULL, "WIFI GAP: d4d cap has neither sup_wpa nor idsup x1 [high]" },
			{ " wme 802.11 ", NULL, "WIFI GAP: d4d cap has neither sup_wpa nor idsup x1 [high]" },
		};

		for (k = 0; k < sizeof cp / sizeof cp[0]; k++) {
			io_cap = cp[k].cap;
			d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
			if (rc != 0 || x.nstore != 164u || (cp[k].line != NULL ? !has(cp[k].line) : !has(cp[k].gap))) {
				fprintf(stderr, "d4d cap case %u: rc %d stores %u\n%s", k, rc, x.nstore, logbuf);
			}
			CHECK(rc == 0 && x.nstore == 164u);
			if (cp[k].line != NULL) {
				snprintf(want, sizeof want, "SWEET WLAN: D4d cap supplicant tokens: %s\n", cp[k].line);
				CHECK(has(want) && !has("neither sup_wpa nor idsup"));
			} else {
				CHECK(has(cp[k].gap) && !has("D4d cap supplicant tokens"));
			}
		}
		/* a long cap string (1022 printable characters): lines of at most 120 characters, none above 200, the NUL and a non-printable byte handled */
		{
			static char big[1100];

			memset(big, 'x', 1022);
			memcpy(big + 100, " sup_wpa ", 9);
			big[500] = '\x01';
			big[1022] = '\0';
			io_cap = big;
			d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
			CHECK(rc == 0 && x.nstore == 164u && has("SWEET WLAN: D4d cap 1023 bytes read, text 1022 chars up to the first NUL\n") && has("SWEET WLAN: D4d cap supplicant tokens: sup_wpa present idsup absent\n") && d4d_longest_line() < 200u);
			CHECK(has("SWEET WLAN: D4d cap[480..599] ") && has("xxxxxxxxxxxxxxxxxxx.xxxxxxxxxxxxxxxxxxxx") && has("SWEET WLAN: D4d cap[960..1021] "));
			io_cap = " 802.11 sup_wpa idsup wme ";
		}
	}
	/* every failure of the answer: its gap, the remaining levels stopped, NO read-index update, no re-post (stores = L18's 123 at level 19/20); mode, request, expected gap */
	{
		static const struct { int mode; const char *gap; } fl[] = {
			{ IO_NONE, "WIFI GAP: d4d no ioctl answer in 1900 ms x1 [high]" },
			{ IO_ACKONLY, "WIFI GAP: d4d ioctl completion missing after 1900 ms x1 [high]" },
			{ IO_CMPLTONLY, "WIFI GAP: d4d ioctl ack missing after 1900 ms x1 [high]" },
			{ IO_ACK_STATUS, "WIFI GAP: d4d ring-2 item 2 type 0xa status -1 != 0 x1 [high]" },
			{ IO_CM_STATUS, "WIFI GAP: d4d ring-2 item 3 type 0xc status 3 != 0 x1 [high]" },
			{ IO_ACK_CMD, "WIFI GAP: d4d ring-2 item 2 cmd 2, expected 1 x1 [high]" },
			{ IO_CM_CMD, "WIFI GAP: d4d ring-2 item 3 cmd 2, expected 1 x1 [high]" },
			{ IO_CM_TRANS, "WIFI GAP: d4d completion trans_id 0x102, expected 0x101 x1 [high]" },
			{ IO_CM_RID, "WIFI GAP: d4d completion request_id 3, expected 2 x1 [high]" },
			{ IO_ACK_RID, "WIFI GAP: d4d ack request_id 0xfffd, expected 0xfffe x1 [high]" },
			{ IO_EPOCH, "WIFI GAP: d4d ring-2 item 3 epoch/xor check failed (1) x1 [high]" },
			{ IO_XOR, "WIFI GAP: d4d ring-2 item 3 epoch/xor check failed (2) x1 [high]" },
			{ IO_BIGLEN, "WIFI GAP: d4d completion resp_len 8193 above the 8192 B buffer x1 [high]" },
			{ IO_T24, "WIFI GAP: d4d ring-2 item 3: 0x24 behind an unconsumed item, not answered x1 [high]" },      /* 22b: the ack (item 2) is still unconsumed, so the 0x24 is not at the front of ring 2 */
			{ IO_TODD, "WIFI GAP: d4d unexpected ring-2 type 0x10 x1 [high]" },
			{ IO_THIRD, "WIFI GAP: d4d ring 2 holds 3 new items, expected 2 x1 [high]" },
			{ IO_DUPACK, "WIFI GAP: d4d second ioctl ack at ring-2 item 3 x1 [high]" },
			{ IO_LOWW, "WIFI GAP: d4d d2h write index 1 went below 2 x1 [high]" },
			{ IO_MANY, "WIFI GAP: d4d ring 2 holds 6 new items, expected 2 x1 [high]" },
		};

		for (k = 0; k < sizeof fl / sizeof fl[0]; k++) {
			for (i = 0; i < 3u; i++) {
				const int lvl = i == 0u ? 18 : i == 1u ? 19 : 20;

				d4d_run(p, &x, lvl, fl[k].mode, 1, 0, &rc);
				if (rc != 0 || x.nstore != 123u || !has(fl[k].gap)) {
					fprintf(stderr, "d4d answer failure %u level %d: rc %d stores %u\n%s", k, lvl, rc, x.nstore, logbuf);
				}
				CHECK(rc == 0 && x.nstore == 123u && x.d4dn == 24u && x.restore == 1 && has(fl[k].gap) && x.d4dstop);
				CHECK(find_rec(SWG_SP_PCIE, 0x140u, 0xDD020004u, 0u) == f.nw && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000007u, 0u) == f.nw && f.hb[dr0 / 4u] == 2u && f.hb[hw0 / 4u] == 6u);
				CHECK(!has("response bytes") && !has("D4d consume") && has("SWEET WLAN: D4d STOP, no further store: "));
				CHECK(x.d4dlv == 17u && !has("D4d L18 done"));      /* review r2 F4: a stopped L18 is not "done" */
			}
		}
		/* the same at the second request (GET_VAR cap): the stores stop at the 150 of L20's request (137 + payload + 12) */
		for (k = 0; k < sizeof fl / sizeof fl[0]; k++) {
			d4d_run(p, &x, 20, fl[k].mode, 2, 0, &rc);
			if (rc != 0 || x.nstore != 150u) {
				fprintf(stderr, "d4d cap failure %u: rc %d stores %u\n%s", k, rc, x.nstore, logbuf);
			}
			CHECK(rc == 0 && x.nstore == 150u && x.d4dn == 51u && x.restore == 1 && x.d4dstop);
			CHECK(find_rec(SWG_SP_PCIE, 0x140u, 0xDD020006u, 0u) == f.nw && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000009u, 0u) == f.nw && f.hb[dr0 / 4u] == 4u && f.hb[hw0 / 4u] == 8u && !has("D4d cap supplicant") && !has("D4d cap "));
			CHECK(has("WIFI GAP: d4d ") && has("D4d STOP, no further store: "));
			CHECK(x.d4dlv == 19u && !has("D4d L20 done"));        /* review r2 F4 */
		}
	}
	/* the third item of IO_THIRD is shown and judged too */
	d4d_run(p, &x, 18, IO_THIRD, 1, 0, &rc);
	CHECK(has("SWEET WLAN: D4d ring2 item 4 type 0xc epoch 0x5 request_id 0x2") && has("WIFI GAP: d4d second ioctl completion at ring-2 item 4 x1 [high]"));
	/* a full 8192 B completion is accepted (not above the buffer): printed up to 1024 B, the gap names the cut; the cap string of 1100 characters likewise */
	d4d_run(p, &x, 18, IO_MANY, 1, 0, &rc);
	CHECK(has("SWEET WLAN: D4d ring2 item 5 raw ") && !has("SWEET WLAN: D4d ring2 item 6 raw ") && has("SWEET WLAN: D4d ring2 item 2 raw "));       /* at most 4 items are shown */
	d4d_run(p, &x, 18, IO_UPPER, 1, 0, &rc);
	CHECK(rc == 0 && x.nstore == 123u && !x.d4dstop && has("SWEET WLAN: D4d answer poll after 0 ms: d2h_w0 4 (base 2, want 4)\n"));       /* the upper half of the slot is no part of the index */
	d4d_run(p, &x, 19, IO_FULL, 1, 0, &rc);
	CHECK(rc == 0 && x.nstore == 137u && !x.d4dstop && has("SWEET WLAN: D4d WLC_GET_VERSION resp_len 8192\n") && has("WIFI GAP: d4d resp_len above 1024: only the first 1024 B printed x1 [medium]") && has("SWEET WLAN: D4d response bytes 992..1023 hex ") &&
	      !has("response bytes 1024"));
	{
		static char big2[1200];

		memset(big2, 'y', 1100);
		big2[1100] = '\0';
		io_cap = big2;
		d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
		CHECK(rc == 0 && x.nstore == 164u && has("WIFI GAP: d4d resp_len above 1024: only the first 1024 B printed x1 [medium]") && has("SWEET WLAN: D4d cap 1024 bytes read, text 1024 chars up to the first NUL\n") && has("SWEET WLAN: D4d cap[960..1023] ") && !has("D4d cap[1080"));
		io_cap = " 802.11 sup_wpa idsup wme ";
	}
	/* a NUL ends the cap text, the bytes behind it are not part of it (resp_len 19 = "abc\0 sup_wpa idsup") */
	{
		static const char nul[] = "abc\0 sup_wpa idsup";

		io_cap = nul;
		d4d_cfg(20, IO_BOTH, 0, 0);
		io_caplen = 19u;
		d4d_go(p, &x, &rc);
		CHECK(rc == 0 && x.nstore == 164u && has("SWEET WLAN: D4d cap 19 bytes read, text 3 chars up to the first NUL\n") && has("SWEET WLAN: D4d cap[0..2] abc\n") && has("WIFI GAP: d4d cap has neither sup_wpa nor idsup x1 [high]"));
		io_cap = " 802.11 sup_wpa idsup wme ";
		io_caplen = 0u;
	}
	/* F8: the dongle answers the cap request but writes no response bytes: word 0 is still the L18 answer, a medium gap (the default case above has none) */
	d4d_cfg(20, IO_BOTH, 0, 0);
	io_nocap = 1;
	d4d_go(p, &x, &rc);
	CHECK(rc == 0 && x.nstore == 164u && !x.d4dstop && has("SWEET WLAN: D4d respbuf word0 before cap 0xb0a0504\n") && has("WIFI GAP: d4d respbuf word 0 unchanged after cap (stale L18 answer?) x1 [medium]"));
	io_nocap = 0;
	/* the printable range of the cap text: 0x1F and 0x7F and 0xFF become '.', 0x20 and 0x7E stay */
	{
		static char pr[200];

		memset(pr, 'z', 100);
		pr[10] = '\x1f'; pr[11] = ' '; pr[12] = '~'; pr[13] = '\x7f'; pr[14] = (char)0xFF;
		pr[100] = '\0';
		io_cap = pr;
		d4d_run(p, &x, 20, IO_BOTH, 0, 0, &rc);
		CHECK(rc == 0 && x.nstore == 164u && has("SWEET WLAN: D4d cap[0..99] zzzzzzzzzz. ~..zzzzz"));
		io_cap = " 802.11 sup_wpa idsup wme ";
	}
	/* L17 variants: the write index goes DOWN (below the read index), reads all ones (STOP), an item of an unknown type */
	d4d_run(p, &x, 17, IO_BOTH, 0, 3, &rc);
	CHECK(rc == 0 && x.nstore == 111u && x.d4dstop && has("SWEET WLAN: D4d L17 watch after 0 ms: d2h_w0 1\n") && has("WIFI GAP: d4d d2h write index 1 below the read index 2 x1 [high]"));
	d4d_run(p, &x, 17, IO_BOTH, 0, 4, &rc);
	CHECK(rc == -SWG_E_DEAD && x.nstore == 111u && x.restore == 1 && has("host d2h_w[0] reads all ones (dead link) in the d4d poll"));
	d4d_run(p, &x, 20, IO_BOTH, 0, 5, &rc);
	CHECK(rc == 0 && x.nstore == 111u && x.d4dstop && has("WIFI GAP: d4d unexpected ring-2 type 0x10 x1 [high]") && has("SWEET WLAN: D4d ring2 item 2 raw 0x0380"));
	/* a read-back that differs (the dongle's memory dropped a bit): STOP with the read-back code, for an item word and for the slot; the next store is never made */
	d4d_cfg(17, IO_BOTH, 0, 0);
	wr_corrupt_host_a = r0 + 160u + 8u; wr_corrupt_v = 8192u;
	d4d_go(p, &x, &rc);
	CHECK(rc == -SWG_E_READBACK && x.d4dn == 3u && x.restore == 1 && find_rec(SWG_SP_HOST, r0 + 160u + 12u, 0u, 0u) == f.nw);
	d4d_cfg(17, IO_BOTH, 0, 0);
	wr_corrupt_host_a = hw0; wr_corrupt_v = 5u;
	d4d_go(p, &x, &rc);
	CHECK(rc == -SWG_E_READBACK && x.d4dn == 11u && x.restore == 1 && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000005u, 0u) == f.nw);
	d4d_cfg(17, IO_BOTH, 0, 0);
	mb_dirty_a = r0 + 160u + 4u; mb_dirty_v = 0xFFFFFFFFu;
	d4d_go(p, &x, &rc);
	CHECK(rc == -SWG_E_DEAD && x.d4dn == 1u && x.restore == 1);
	mb_dirty_a = 0u;
	/* the preconditions: no store when the L16 handshake did not finish, the write/read index is not where the glue expects it, a leftover ring-2 item, a block without the buffers */
	mb_note = 0;
	want_d4b = 4;
	d4kind = D_MB; mb_type = 0x24u; mb_mb = 0x2u; mb_epoch = 1u; mb_dw = 1u; mb_xorbad = 0; io_mode = IO_BOTH; io_req = 0; io_l17 = 0; io_late = 0u; ndb_d4d = 0u;
	mk(&x, p, F_OK);
	rc = swg_go(&x);
	logbuf[loglen] = 0;
	CHECK(rc == 0 && x.nstore == 85u && x.d4dn == 0u && has("WIFI GAP: d4d not run: the L16 DS handshake did not complete x1 [high]") && has("SWEET WLAN: D4d not run: the L16 DS handshake (ack, wake, exit note, deassert) did not complete\n") && ndb_d4d == 0u);
	mb_note = 1;
	{
		static const struct { uint32_t db, off_kind, val; const char *gap; } pre[] = {
			{ 0xDD020002u, 0, 5u, "WIFI GAP: d4d ring-0 write index 0x5, expected 4 x1 [high]" },
			{ 0xDD020002u, 0, 0x10004u, "WIFI GAP: d4d ring-0 write index 0x10004, expected 4 x1 [high]" },
			{ 0xDD020002u, 1, 3u, "WIFI GAP: d4d ring-2 read index 0x3, expected 2 x1 [high]" },
		};

		for (k = 0; k < sizeof pre / sizeof pre[0]; k++) {
			d4d_cfg(17, IO_BOTH, 0, 0);
			mb_twk_db = pre[k].db;
			mb_twk_off = pre[k].off_kind == 0 ? hw0 : dr0;
			mb_twk_val = pre[k].val;
			d4d_go(p, &x, &rc);
			mb_twk_db = 0u;
			if (rc != 0 || !has(pre[k].gap) || x.nstore != 99u) {
				fprintf(stderr, "d4d precondition %u: rc %d stores %u\n%s", k, rc, x.nstore, logbuf);
			}
			CHECK(rc == 0 && x.nstore == 99u && x.d4dn == 0u && has(pre[k].gap) && ndb_d4d == 0u);
		}
	}
	/* ring-2 write index 3 before the first post (a leftover item): printed and refused; index 1 likewise */
	d4d_cfg(17, IO_BOTH, 0, 0);
	mb_twk_db = 0xDD020002u; mb_twk_off = p->b[SWD_IDX_D2H_W].off; mb_twk_val = 3u;
	d4d_go(p, &x, &rc);
	CHECK(rc == 0 && x.nstore == 99u && x.d4dn == 0u && has("WIFI GAP: d4d ring-2 write index 0x3 before the first post, expected 2 x1 [high]") && has("WIFI GAP: d4d ring 2 holds 1 new items, expected 0 x1 [high]") && has("SWEET WLAN: D4d ring2 item 2 raw 0x"));
	mb_twk_val = 1u;
	d4d_go(p, &x, &rc);
	CHECK(rc == 0 && x.nstore == 99u && x.d4dn == 0u && has("WIFI GAP: d4d ring-2 write index 0x1 before the first post, expected 2 x1 [high]"));
	mb_twk_val = 0xFFFFFFFFu;
	mb_twk_off = hw0;
	d4d_go(p, &x, &rc);
	CHECK(rc == -SWG_E_DEAD && x.nstore == 99u && x.d4dn == 0u && has("a d4d index slot reads all ones (dead link)"));
	mb_twk_db = 0u;
	/* the buffers: a response or payload buffer outside the block, or not 4 KiB aligned */
	for (k = 0; k < 5u; k++) {
		d4d_run(p, &x, k < 2u || k == 4u ? 17 : 18, IO_BOTH, 0, 0, &rc);
		mk(&x, p, F_OK);
		x.in.respbuf = k == 0u ? PA + p->total : k == 1u ? PA + p->b[SWD_RESPBUF].off + 128u : x.in.respbuf;
		x.in.ioctbuf = k == 2u || k == 4u ? PA + p->total : k == 3u ? PA + p->b[SWD_IOCTBUF].off + 128u : x.in.ioctbuf;
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		if (k == 4u) {
			CHECK(rc == 0 && x.nstore == 111u && !has("WIFI GAP: d4d response or payload buffer"));      /* level 17 never uses the payload buffer */
		} else {
			CHECK(rc == 0 && x.nstore == 99u && x.d4dn == 0u && has("WIFI GAP: d4d response or payload buffer outside the G1 block x1 [high]"));
		}
	}
	/* F5a: level 18, the payload buffer inside the block but not 4 KiB aligned (SWD_IOCTBUF - 128: respbuf + 8192 + 128 would also run past the block end, which the range check alone refuses): the alignment clause alone refuses it, no W-D4d store */
	d4d_cfg(18, IO_BOTH, 0, 0);
	mk(&x, p, F_OK);
	ndb_d4d = 0u;
	x.in.ioctbuf = PA + p->b[SWD_IOCTBUF].off - 128u;
	rc = swg_go(&x);
	logbuf[loglen] = 0;
	CHECK(rc == 0 && x.nstore == 99u && x.d4dn == 0u && x.d4dstop && has("WIFI GAP: d4d response or payload buffer outside the G1 block x1 [high]") && has("SWEET WLAN: D4d STOP, no further store: response or payload buffer outside the G1 block\n") && ndb_d4d == 0u);
	/* F5b: a direct store after the run stopped: the stopped error, no bus write */
	mk(&x, p, F_OK);
	x.entered = 1;
	x.stopped = 1;
	CHECK(swg_d4d_st(&x, SWG_K_ITEM, SWG_SP_HOST, r0 + 160u, 0u, 1u, 0, "t") == -SWG_E_STOPPED && x.nstore == 0u && f.nw == 0u);
	/* a refused store (the item word 2 of the response post / the payload word / the doorbell is not reached): STOP with the write code, the undo made */
	d4d_cfg(17, IO_BOTH, 0, 0);
	wr_fail_host_a = r0 + 160u + 8u;
	d4d_go(p, &x, &rc);
	wr_fail_host_a = 0u;
	CHECK(rc == -SWG_E_WR && x.d4dn == 2u && x.restore == 1 && f.nunm == 1u && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000005u, 0u) == f.nw);
	d4d_cfg(20, IO_BOTH, 0, 0);
	wr_fail_host_a = ib;
	d4d_go(p, &x, &rc);
	wr_fail_host_a = 0u;
	CHECK(rc == -SWG_E_WR && x.d4dn == 38u && x.restore == 1 && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000008u, 0u) == f.nw && f.hb[ib / 4u] == 0u);
	/* T-98d9 (W-JOIN L25, review T-5173 r2 N3): the same refusal at d4b 12 and 13 (sweetwlan=25 and 26). The stop path (0xC34 := 0, EP Command, RC Command) is outside the L25 judge: the undo is tried in full
	 * and ends `undo ok (ep restored, rc restored)` with the mask cleared, the endpoint and root complex Command words back at their entry values and no doorbell made */
	for (k = 25; k <= 26; k++) {
		d4d_cfg((int)k, IO_BOTH, 0, 0);
		wr_fail_host_a = r0 + 160u + 8u;
		d4d_go(p, &x, &rc);
		wr_fail_host_a = 0u;
		CHECK(x.d4b == (int)k - 13);
		CHECK(rc == -SWG_E_WR && x.d4dn == 2u && x.restore == 1 && f.nunm == 1u && f.c34 == 0u && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000005u, 0u) == f.nw);
		CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)") && !has("G2 undo FAILED"));
		CHECK(has("G2 STOP:") && strstr(logbuf, "G2 store d4b unmask") != NULL && strstr(logbuf, "G2 store d4b unmask") < strstr(logbuf, "G2 undo ok"));
	}
	/* old values: an item word that is not 0, a slot that is not the previous index: STOP (old value) before the store */
	d4d_cfg(17, IO_BOTH, 0, 0);
	mb_dirty_a = r0 + 160u + 12u;
	d4d_go(p, &x, &rc);
	mb_dirty_a = 0u;
	CHECK(rc == -SWG_E_OLD && x.d4dn == 3u && x.restore == 1 && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000005u, 0u) == f.nw);
	d4d_cfg(20, IO_BOTH, 0, 0);
	mb_dirty_a = r0 + 280u + 4u;
	d4d_go(p, &x, &rc);
	mb_dirty_a = 0u;
	CHECK(rc == -SWG_E_OLD && x.d4dn == 40u && x.restore == 1 && f.hb[ib / 4u] == 0x00706163u && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000008u, 0u) == f.nw);
	/* the ioctl payload start must be zero before GET_VERSION (the version int is read by the dongle from there) */
	d4d_cfg(18, IO_BOTH, 0, 0);
	mb_dirty_a = ib;
	d4d_go(p, &x, &rc);
	mb_dirty_a = 0u;
	CHECK(rc == 0 && x.nstore == 111u && x.d4dstop && has("WIFI GAP: d4d payload buffer word 0 is 0x5, expected 0 x1 [high]"));
	/* the store table itself: a store of the wrong kind, past the level's rows, or by a level below 17 is refused as an ORDER stop before any bus write */
	want_d4b = 4;
	mk(&x, p, F_OK);
	x.entered = 1;
	CHECK(swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000005u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u && x.stopped);
	mk(&x, p, F_OK);
	x.entered = 1;
	x.d4dn = 12u;
	CHECK(swg_d4d_st(&x, SWG_K_ITEM, SWG_SP_HOST, r0 + 200u, 0u, 1u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u);
	mk(&x, p, F_OK);
	x.entered = 1;
	x.d4b = 3;
	CHECK(swg_d4d_st(&x, SWG_K_ITEM, SWG_SP_HOST, r0 + 160u, 0u, 1u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u);
	mk(&x, p, F_OK);
	CHECK(swg_d4d_st(&x, SWG_K_ITEM, SWG_SP_HOST, r0 + 160u, 0u, 1u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u);     /* not entered */
	/* the table edges, called directly (entered, level 4..7): one row below the level's cut is a doorbell and is made, the cut itself and anything above is refused before any bus write; d4b 12 is no level (W-D4e: 8..11 are L21..L24) */
	{
		static const struct { int d4b; unsigned last, idx; } edge[4] = { { 4, 11u, 5u }, { 5, 23u, 6u }, { 6, 37u, 7u }, { 7, 64u, 9u } };

		for (k = 0; k < 4u; k++) {
			d4d_cfg(edge[k].d4b + 13, IO_BOTH, 0, 0);
			mk(&x, p, F_OK);
			x.entered = 1;
			x.d4dn = edge[k].last;
			CHECK(swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000000u | edge[k].idx, 0, "t") == 0 && x.d4dn == edge[k].last + 1u && x.nstore == 1u);
			CHECK(swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000000u | edge[k].idx, 0, "t") == -SWG_E_ORDER && x.nstore == 1u);
		}
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4b = 14;       /* T-d84f: sweetwlan=25 and 26 are d4b 12 and 13 (L25/L26 store through swg_d4d_st); 14 is past the last level */
		CHECK(swg_d4d_st(&x, SWG_K_ITEM, SWG_SP_HOST, r0 + 160u, 0u, 1u, 0, "t") == -SWG_E_ORDER && x.nstore == 0u);
		/* T-2543: the other side of the ceiling: d4b 12 (L25) and 13 (L26) are levels, the same doorbell row that d4b 14 is refused for is made (kills a ceiling of 12) */
		for (k = 12; k <= 13; k++) {
			d4d_cfg(17, IO_BOTH, 0, 0);
			mk(&x, p, F_OK);
			x.entered = 1;
			x.d4b = (int)k;
			x.d4dn = 11u;
			CHECK(swg_d4d_st(&x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, 0xFF000005u, 0, "t") == 0 && x.d4dn == 12u && x.nstore == 1u);
		}
	}
	/* the slot keeps the upper half of its old word (a 16-bit index in a 32-bit slot) and only a slot is merged */
	d4d_cfg(17, IO_BOTH, 0, 0);
	mk(&x, p, F_OK);
	x.entered = 1;
	x.d4dn = 10u;
	f.hb[hw0 / 4u] = 0x00010004u;
	CHECK(swg_d4d_st(&x, SWG_K_H2DW, SWG_SP_HOST, hw0, 0x00010004u, 5u, 1, "t") == 0 && f.hb[hw0 / 4u] == 0x00010005u);
	mk(&x, p, F_OK);
	x.entered = 1;
	x.d4dn = 10u;
	f.hb[hw0 / 4u] = 0x00020004u;
	CHECK(swg_d4d_st(&x, SWG_K_H2DW, SWG_SP_HOST, hw0, 0x00020004u, 0x00010005u, 1, "t") == 0 && f.hb[hw0 / 4u] == 0x00020005u);       /* only the low half of the new value is stored */
	/* the ring-0 post refuses an index whose write index would wrap (idx + 1 == 128) before any store, and goes on at 126 */
	{
		uint8_t it[SWD4B_ITEM_LEN];

		memset(it, 0, sizeof it);
		d4d_cfg(17, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		x.d4wr = 127u;
		CHECK(swg_d4d_post(&x, it) == -SWG_E_ADDR && x.nstore == 0u);
		mk(&x, p, F_OK);
		x.entered = 1;
		f.hb[hw0 / 4u] = 126u;
		x.d4wr = 126u;
		CHECK(swg_d4d_post(&x, it) == -SWG_E_WR && x.d4dn == 11u && x.nstore == 12u);     /* the 10 words and the slot are made, the doorbell 0xFF00007F is no value of the fake */
	}
	/* 22b: ring 2 wraps on the read side: a scan of the absolute items 62..65 reads the slots 62, 63, 0, 1 (the epoch follows the absolute count); there is no "past the ring" gap any more */
	{
		struct swd4b_ioctl_d2h cm;
		unsigned got = 0;

		d4d_cfg(18, IO_BOTH, 0, 0);
		mk(&x, p, F_OK);
		x.entered = 1;
		memset(&cm, 0, sizeof cm);
		CHECK(swg_d4d_scan(&x, 62u, 66u, 1, 1u, 0x101u, 2u, &cm, &got) == 0 && !has("past the ring") && has("SWEET WLAN: D4d ring2 item 63 raw ") && has("SWEET WLAN: D4d ring2 item 65 raw ") && !has("SWEET WLAN: D4d ring2 item 66 raw "));
	}
	ds_tests(p);
	scan_tests(p);
	clm_tests(p);
	join_tests(p);
	lw_tests(p);
	/* levels below 17 make no d4d store and print no d4d line */
	d4d_run(p, &x, 16, IO_BOTH, 0, 0, &rc);
	CHECK(rc == 0 && x.nstore == 99u && x.d4dn == 0u && !has("D4d") && !has("WIFI GAP: d4d") && ndb_d4d == 0u);
	want_d4b = 0;
	d4kind = D_OK;
	mb_note = 0;
	mb_type = mb_mb = mb_epoch = mb_dw = 0u;
	io_mode = IO_BOTH;
}

/* T-0f36: L22c (sweetwlan=24): the CLM chunks, clmload_status and country XZ before WLC_UP, against the fake dongle. The blob is 15,932 B = 11 x 1400 + 532, the size of the packed bcmdhd_clm.blob [src 108 s.9.1] */
static uint8_t clm_blob[16900];

static void
clm_fill(void)
{
	unsigned i;

	for (i = 0; i < sizeof clm_blob; i++) {
		clm_blob[i] = (uint8_t)(i * 7u + 3u + (i >> 8));
	}
}

static void
clm_case(struct swd_plan *p, struct swg_wx *x, const uint8_t *blob, uint32_t n, int *rc, const char *name)
{
	gd_arm();
	tclm = blob;
	tclmlen = n;
	scan_run(p, x, 24, rc, name);
}

static void
clm_tests(struct swd_plan *p)
{
	static const uint8_t xz[12] = { 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 };
	struct swg_wx x;
	int rc;
	unsigned k;

	clm_fill();
	/* (good) 12 chunks: flags 0x1002 first, 0x1004 last, 0x1000 between, len 1400 x 11 + 532, crc 0, type 2; clmload_status read once (0), country XZ set once, then WLC_UP and the escan; the blob arrives byte for byte */
	clm_case(p, &x, clm_blob, 15932u, &rc, "clm");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && x.restore == 1 && ge.nclm == 12u && ge.nclmst == 1u && ge.nctry == 1u && ge.up == 1u && ge.nescan == 1u && x.nbss == 3u && x.emax == SWG_E_MAX_CLM);
	CHECK(ge.cflag[0] == 0x1002u && ge.cflag[11] == 0x1004u && ge.clen[0] == 1400u && ge.clen[10] == 1400u && ge.clen[11] == 532u);
	for (k = 1; k < 11u; k++) {
		CHECK(ge.cflag[k] == 0x1000u && ge.clen[k] == 1400u);
	}
	for (k = 0; k < 12u; k++) {
		CHECK(ge.ctype[k] == 2u && ge.ccrc[k] == 0u);
	}
	CHECK(ge.clmoff == 15932u && memcmp(ge.clm, clm_blob, 15932u) == 0 && memcmp(ge.ctry, xz, 12) == 0);
	CHECK(x.ereq == 6u + 12u + 2u && x.d4wr < 128u && has("SWEET WLAN: L22c clmload chunk 12 of 12: flag 0x1004 len 532, status 0\n") && has("SWEET WLAN: L22c clmload_status 0 (answer 4 B, status 0)\n") && has("SWEET WLAN: L22c country XZ set: status 0\n") && ge.clmstin == 15u + 4u && ge.ctryin == 8u + 12u);       /* the request lengths: name + NUL + the 4 B answer area / the 12 B wl_country_t */
	/* the order is clmload chunk 1 .. 12 -> clmload_status -> country -> WLC_UP -> escan (the log lines appear in this order) */
	CHECK(strstr(logbuf, "L22c clmload chunk 1 of 12") != NULL && strstr(logbuf, "L22c clmload chunk 1 of 12") < strstr(logbuf, "L22c clmload chunk 12 of 12") && strstr(logbuf, "L22c clmload chunk 12 of 12") < strstr(logbuf, "L22c clmload_status 0") &&
	      strstr(logbuf, "L22c clmload_status 0") < strstr(logbuf, "L22c country XZ set") && strstr(logbuf, "L22c country XZ set") < strstr(logbuf, "L22 WLC_UP status") && strstr(logbuf, "L22 WLC_UP status") < strstr(logbuf, "L23 escan start"));
	CHECK(count_of("WIFI GAP: scan event cap") == 0u);
	/* a one-chunk blob carries BEGIN and END together (0x1006) */
	clm_case(p, &x, clm_blob, 100u, &rc, "clm1");
	CHECK(rc == 0 && !x.d4dstop && ge.nclm == 1u && ge.cflag[0] == 0x1006u && ge.clen[0] == 100u && ge.nctry == 1u && ge.nescan == 1u);
	/* (short / absent blob) no blob at all, or a zero length: a loud gap, no clmload, no UP, no escan, nothing stored for it */
	clm_case(p, &x, NULL, 0u, &rc, "clmnone");
	CHECK(rc == 0 && x.d4dstop && ge.nclm == 0u && ge.nclmst == 0u && ge.nctry == 0u && ge.up == 0u && ge.nescan == 0u && has("WIFI GAP: scan no CLM blob (absent or failed the glue CRC) x1 [high]\n"));
	clm_case(p, &x, clm_blob, 0u, &rc, "clmzero");
	CHECK(rc == 0 && x.d4dstop && ge.nclm == 0u && ge.up == 0u && has("WIFI GAP: scan no CLM blob (absent or failed the glue CRC) x1 [high]\n"));
	/* a blob of more than 12 chunks (16,801 B) is refused before any chunk */
	clm_case(p, &x, clm_blob, 16801u, &rc, "clmbig");
	CHECK(rc == 0 && x.d4dstop && ge.nclm == 0u && ge.up == 0u && has("WIFI GAP: scan CLM blob 16801 B is more than 12 chunks x1 [high]\n"));
	clm_case(p, &x, clm_blob, 16800u, &rc, "clm12max");
	CHECK(rc == 0 && !x.d4dstop && ge.nclm == 12u && ge.clen[11] == 1400u && ge.cflag[11] == 0x1004u);
	/* (refused chunk) the 5th chunk completes with status -23: the run stops at once (T-4adb-hwwrite-r1 C1: the glue list has no clmload_status after a refused chunk, so none is read), no further chunk, no country, no UP */
	gd_arm();
	ge.clmref = 5u;
	ge.clmst = 9u;
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 24, &rc, "clmref");
	CHECK(rc == 0 && x.d4dstop && ge.nclm == 5u && ge.nclmst == 0u && ge.nctry == 0u && ge.up == 0u && ge.nescan == 0u && has("WIFI GAP: scan clmload chunk 5 refused, status -23 x1 [high]\n"));
	/* (clmload_status) non-zero after the last chunk: gap, no country, no UP */
	gd_arm();
	ge.clmst = 7u;
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 24, &rc, "clmst");
	CHECK(rc == 0 && x.d4dstop && ge.nclm == 12u && ge.nclmst == 1u && ge.nctry == 0u && ge.up == 0u && has("WIFI GAP: scan clmload_status 7 (answer 4 B, status 0) x1 [high]\n"));
	/* a clmload_status answer shorter than 4 B is a stop, not a pass */
	gd_arm();
	ge.clmstshort = 1;
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 24, &rc, "clmstshort");
	CHECK(rc == 0 && x.d4dstop && ge.nctry == 0u && ge.up == 0u && has("WIFI GAP: scan clmload_status 4294967295 (answer 2 B, status 0) x1 [high]\n"));
	/* clmload_status completes with status -23 but a 4 B zero answer: a stop, not a pass */
	gd_arm();
	ge.clmstbad = 1;
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 24, &rc, "clmstbad");
	CHECK(rc == 0 && x.d4dstop && ge.nclmst == 1u && ge.nctry == 0u && ge.up == 0u && has("WIFI GAP: scan clmload_status 0 (answer 4 B, status -23) x1 [high]\n"));
	/* (country refused) completion status -23: gap, no UP, no escan */
	gd_arm();
	ge.ctryref = 1;
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 24, &rc, "ctry");
	CHECK(rc == 0 && x.d4dstop && ge.nclm == 12u && ge.nctry == 1u && ge.up == 0u && ge.nescan == 0u && has("WIFI GAP: scan country XZ refused, status -23 x1 [high]\n"));
	/* the event cap under sweetwlan=24 is 48: the 49th event is a STOP, no re-post for it, the gap names 48 (the 64 of sweetwlan=23 would let it through) */
	gd_arm();
	ge.fault = EF_CAP;
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 24, &rc, "cap24");
	CHECK(rc == 0 && x.d4dstop && x.evn == 48u && x.d4wr == 110u && x.restore == 1 && x.d4dlv == 22u && ge.nclm == 12u && ge.up == 1u && has("SWEET WLAN: L23 STOP event cap 48 reached, no further store\n") && has("WIFI GAP: scan event cap 48 reached x1 [high]") && !has("event cap 64"));       /* 110 = the 98 of the 64 cap - 16 + the 28 ring-0 items of the 14 CLM / status / country requests */
	/* sweetwlan=23 is unchanged: no CLM request, no country, the 64 event cap */
	gd_arm();
	tclm = clm_blob;
	tclmlen = 15932u;
	scan_run(p, &x, 23, &rc, "clm23");
	CHECK(rc == 0 && !x.d4dstop && ge.nclm == 0u && ge.nclmst == 0u && ge.nctry == 0u && x.emax == SWG_E_MAX && x.ereq == 6u && ge.up == 1u && ge.nescan == 1u);
}

int
main(void)
{
	struct swd_plan p;
	struct swg_wx x;
	unsigned i, nwant;
	int rc;

	CHECK(swd_plan_make(4u, 43u, 4u, &p) == 0);
	CHECK(HI(SWD_TRAP) == 8u);

	/* Android's order (R94 s.2 steps 1-12): shared words, the four index host addresses (d2h_w, h2d_r, h2d_w, d2h_r), per ring base lo/hi, max|len, WR slot, RD slot
	 * (with the window moves the slot addresses need), scratch, scratch len, RC Command, EP Command, hostready; then the undo EP 0x2, RC old */
	const struct rec want[] = {
		{ W, 0x84u, 0x400000u }, { W, 0x84u, 0u },      /* swg_dump (reads only): the mailbox data words sit in the tail window, read last; the first store then moves back */
		{ T, SH + 0x50u, 0xC0943041u }, { T, SH + 0x54u, 0x10823407u }, { T, SH + 0x58u, LO(SWD_TRAP) }, { T, SH + 0x5Cu, 8u },
		{ T, RI + 0x24u, LO(SWD_IDX_D2H_W) }, { T, RI + 0x28u, 8u }, { T, RI + 0x1Cu, LO(SWD_IDX_H2D_R) }, { T, RI + 0x20u, 8u },
		{ T, RI + 0x14u, LO(SWD_IDX_H2D_W) }, { T, RI + 0x18u, 8u }, { T, RI + 0x2Cu, LO(SWD_IDX_D2H_R) }, { T, RI + 0x30u, 8u },
		{ T, RM + 0x08u, LO(0) }, { T, RM + 0x0Cu, 8u }, { T, RM + 0x04u, 128u | 40u << 16 },
		{ W, 0x84u, 0x400000u }, { T, I_HW, 0x00010000u }, { W, 0x84u, 0u }, { T, I_HR, 0u },
		{ T, RM + 0x18u, LO(1) }, { T, RM + 0x1Cu, 8u }, { T, RM + 0x14u, 2048u | 32u << 16 },
		{ W, 0x84u, 0x400000u }, { T, I_HW + 4u, 0u }, { W, 0x84u, 0u }, { T, I_HR + 4u, 0u },
		{ T, RM + 0x28u, LO(2) }, { T, RM + 0x2Cu, 8u }, { T, RM + 0x24u, 64u | 24u << 16 },
		{ T, I_DW, 0u }, { W, 0x84u, 0x400000u }, { T, I_DR, 0u },
		{ W, 0x84u, 0u }, { T, RM + 0x38u, LO(3) }, { T, RM + 0x3Cu, 8u }, { T, RM + 0x34u, 2048u | 24u << 16 },
		{ T, I_DW + 4u, 0u }, { W, 0x84u, 0x400000u }, { T, I_DR + 4u, 0u },
		{ W, 0x84u, 0u }, { T, RM + 0x48u, LO(4) }, { T, RM + 0x4Cu, 8u }, { T, RM + 0x44u, 1024u | 40u << 16 },
		{ T, I_DW + 8u, 0u }, { W, 0x84u, 0x400000u }, { T, I_DR + 8u, 0xBEEF0000u },
		{ W, 0x84u, 0u }, { T, SH + 0x38u, LO(SWD_SCRATCH) }, { T, SH + 0x3Cu, 8u }, { T, SH + 0x34u, 8u },
		{ SWG_SP_RC, 0x04u, 0x507u }, { SWG_SP_EP, 0x04u, 6u },
		{ W, 0x84u, 0x400000u },        /* W-D4a: the baseline reads the mailbox data (tail window) last, before hostready; the poll then reads it first and moves the window back once */
		{ SWG_SP_PCIE, 0x144u, 0x12345678u }, { W, 0x84u, 0u },
		{ SWG_SP_EP, 0x04u, 2u }, { SWG_SP_RC, 0x04u, 0u },
	};
	nwant = (unsigned)(sizeof want / sizeof want[0]);

	mk(&x, &p, F_OK);
	rc = swg_go(&x);
	logbuf[loglen] = 0;
	CHECK(rc == 0);
	if (rc != 0 || getenv("G2LOG")) {
		fputs(logbuf, stderr);
	}
	CHECK(f.nw == nwant);
	for (i = 0; i < nwant && i < f.nw; i++) {
		if (f.w[i].sp != want[i].sp || f.w[i].a != want[i].a || f.w[i].v != want[i].v) {
			fprintf(stderr, "store %u: got sp %d %#x := %#x, want sp %d %#x := %#x\n", i, f.w[i].sp, f.w[i].a, f.w[i].v, want[i].sp, want[i].a, want[i].v);
			CHECK(0);
		}
	}
	CHECK(x.mask == 0xFFFFFFFFu && x.nstore == nwant - 14u);   /* 14 window moves go through the bus but are not table rows */
	CHECK(f.nready == 1u && f.win2 == 0u && f.ep == 0x00100002u && f.rc == 0x00100000u);
	CHECK(f.ram[I_HW / 4u] == 0x00010000u && f.ram[I_DR / 4u + 2u] == 0xBEEF0000u);
	CHECK(has("SWEET WLAN: G2 entry ok ring_mem 0x3b1bb8 index arrays 0x407800 0x207f40 0x3e7d80 0x47cf40\n"));
	CHECK(has("SWEET WLAN: G2 poll 1: change vs baseline in tcm d2h_w[0]\n"));
	CHECK(has("SWEET WLAN: G2 poll values flags 0x70050107 mb ptr 0x44868c mb data 0x0 tcm d2h_w 0x1 0x0 0x0 host - c30 0x0 ring_mem idx/type 0x1\n"));
	/* W-D4a: the baseline is taken before the hostready store: 0xC30 and the mailbox data read once before it, the baseline line printed before the store line, the stores before the 0xC30 read are
	 * all of them but the window move and hostready itself; the mailbox word that was read is the DATA (0), not the pointer */
	CHECK(has("SWEET WLAN: G2 base flags 0x70050107 mb ptr 0x44868c mb data 0x0 tcm d2h_w 0x0 0x0 0x0 host - c30 0x0\n"));
	CHECK(strstr(logbuf, "G2 base ") != NULL && strstr(logbuf, "G2 store hostready") != NULL && strstr(logbuf, "G2 base ") < strstr(logbuf, "G2 store hostready"));
	{
		unsigned ir = 0;

		while (ir < nwant && want[ir].sp != SWG_SP_PCIE) {
			ir++;
		}
		CHECK(f.c30_pre == 1u && f.mb_pre >= 1u && f.nw_c30 + 1u == ir);
		CHECK(f.c30_post == 1u && f.mb_post >= 1u);
	}
	CHECK(has("WIFI GAP: g2 host block not read (level 12: D4b observation off)") && !has("G2 has no VA"));
	CHECK(has("WIFI GAP: g2 16-bit golden stores"));
	CHECK(has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
	CHECK(has("SWEET WLAN: G2 done rc=0 stores=45 mask 0xffffffff restore=ok\n"));
	CHECK(f.ndelay == 0u);          /* the dongle answered at the first poll: no wait */
	CHECK(!has("no dongle write seen"));

	/* W-D3c: shared / ring_info anywhere in RAM, either order (DHD: dhd_pcie.c:13684-13713, 13872). The same run on moved layouts must end `done` with the final RAM equal to the default run's, blocks moved:
	 * wlan-runG2-0174 a2 / a3 [obs] (refused by the old rules), the Android golden shared address (R100 s.2) with ring_info below and above it, straddling 0x400000, and the RAM edges */
	{
		static const struct { uint32_t sh, ri; const char *what; } lay[] = {
			{ 0x3b2228u, 0x4852e0u, "a2: ri > sh, both layouts above and below 0x400000" }, { 0x40a67cu, 0x482884u, "a3: both above 0x400000" },
			{ 0x408c74u, 0x3b029cu, "golden shared, ring_info below" }, { 0x408c74u, 0x482884u, "golden shared, ring_info above" },
			{ 0x3fffd0u, RI, "shared straddles 0x400000" }, { SH, 0x3fff80u, "ring_info block straddles 0x400000" }, { SH, 0x3ffff8u, "ring_info words straddle 0x400000" },
			{ SH, END - 0x9Cu, "ring_info at end - 0x9C" }, { END - 0x60u, RI, "shared at end - 0x60" }, { SH, SH + 0x60u, "ring_info right after shared" }, { SH, SH - 0x9Cu, "ring_info right before shared" },
		};
		static uint32_t ram0[RAMW], exp[RAMW];

		memcpy(ram0, f.ram, sizeof ram0);       /* the default run's final RAM (f is still the F_OK run) */
		for (i = 0; i < sizeof lay / sizeof lay[0]; i++) {
			unsigned j, bad = 0;

			reloc(exp, ram0, lay[i].sh, lay[i].ri);
			ov_sh = lay[i].sh;
			ov_ri = lay[i].ri;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			ov_sh = ov_ri = 0u;
			if (rc != 0) {
				fprintf(stderr, "moved layout %s: rc %d\n%s", lay[i].what, rc, logbuf);
			}
			CHECK(rc == 0 && x.mask == 0xFFFFFFFFu && x.nstore == nwant - 14u);
			CHECK(f.nready == 1u && f.win2 == 0u && f.ep == 0x00100002u && f.rc == 0x00100000u);
			CHECK(has("SWEET WLAN: G2 done rc=0 stores=45 mask 0xffffffff restore=ok\n"));
			for (j = 0; j < RAMW; j++) {
				if (f.ram[j] != exp[j] && bad++ < 3u) {
					fprintf(stderr, "moved layout %s: ram %#x is %#x, want %#x\n", lay[i].what, j * 4u, f.ram[j], exp[j]);
				}
			}
			CHECK(bad == 0u);
		}
		fw_init(F_OK);
	}

	/* order and ONCE, called directly: a row out of order or a second time stops (and undoes nothing it did not try) */
	mk(&x, &p, F_OK);
	CHECK(swg_entry(&x) == 0);
	CHECK(swg_st(&x, SWG_ST_HOSTCAP) == -SWG_E_ORDER && f.nw == 1u && has("G2 STOP: order"));       /* f.nw: the one window move of the dump (to the tail, mailbox data) */
	mk(&x, &p, F_OK);
	CHECK(swg_entry(&x) == 0 && swg_st(&x, SWG_ST_FLAGS2) == 0);
	CHECK(swg_st(&x, SWG_ST_FLAGS2) == -SWG_E_ORDER && f.nw == 3u);
	mk(&x, &p, F_OK);
	CHECK(swg_st(&x, SWG_ST_FLAGS2) == -SWG_E_ORDER);       /* no entry, no rt: stops before a store */
	CHECK(f.nw == 0u);

	/* after a stop nothing reads, stores or stops again: the first reason (and its undo) stands */
	mk(&x, &p, F_OK);
	CHECK(swg_entry(&x) == 0 && swg_st(&x, SWG_ST_HOSTCAP) == -SWG_E_ORDER && x.stopped == 1 && x.code == SWG_E_ORDER);
	CHECK(swg_stop(&x, SWG_E_WR) == -SWG_E_WR && x.code == SWG_E_ORDER && f.nw == 1u && f.nep == 0u && f.nrc == 0u);
	CHECK(swg_st(&x, SWG_ST_FLAGS2) == -SWG_E_STOPPED && f.nw == 1u);
	{
		uint32_t v = 0;

		CHECK(swg_rd(&x, SH, &v) != 0);
	}
	/* a RAM end at or below the RAM base leaves no RAM word at all (end - 4 would wrap) */
	mk(&x, &p, F_OK);
	x.in.end = 0u;
	rc = swg_go(&x);
	CHECK(rc == -SWG_E_ADDR && f.nw == 0u);

	for (i = 1; i < (unsigned)F_N; i++) {
		mk(&x, &p, (int)i);
		x.rt.rc_old = 0u;               /* stale state of an earlier read: a failed read must not pass on it */
		x.rt.ep_old = SWG_EP_CMD_MEM;
		x.rt.srpwr = 0x00030300u;
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		if (ENTRY_CASE(i)) {
			if (rc != -entry_code((int)i) || f.ntcm != 0u || f.nep != 0u || f.nrc != 0u) {
				fprintf(stderr, "entry case %u: rc %d (want %d) stores %u\n%s", i, rc, -entry_code((int)i), f.nw, logbuf);
			}
			CHECK(rc == -entry_code((int)i));
			CHECK(f.ntcm == 0u && f.nep == 0u && f.nrc == 0u && x.nstore == 0u);      /* no store (a BAR2 window select is not one: F_SH_END needs the tail window) */
			CHECK(f.win2 == 0u && x.step == SWG_ST_N && x.mask == 0u && x.entered == 0);     /* refused by the entry, not by the first row's own rule */
			CHECK(has("G2 STOP: "));
			CHECK(has("WIFI GAP: g2 STOP: "));
			CHECK(has("SWEET WLAN: G2 undo ok (ep not stored, rc not stored)"));
			CHECK((i < F_OVL_RING || i > F_OVL_IDX) || has("an index array overlaps"));        /* review r1 F2: the overlap check, not an earlier one */
			CHECK((i != F_RI_OVL_SH && i != F_RI_OVL_LO && i != F_RI_OVL_HI) || has("ring_info + ring_mem overlaps the shared structure"));       /* W-D3c: the apart check, not an earlier one */
			CHECK(i != F_RM_END || has("the last ring_mem word is not a RAM word"));
			CHECK((i != F_MBP_ALIGN && i != F_MBP_OOR && i != F_MBP_LOW) || has("the d2h mailbox pointer (shared + 0x2C) is not a 4-aligned RAM word"));       /* W-D4a: refused at the entry, before any store */
			continue;
		}
		switch (i) {
		case F_NOWRITE:
			CHECK(rc == -SWG_E_READBACK && f.nw == 3u && f.nready == 0u);       /* the 2 window moves of the dump, then the flags2 store */
			CHECK(has("G2 STOP: read-back (code 8) at step 0 flags2"));
			break;
		case F_LATE:
			CHECK(rc == -SWG_E_OLD && x.step == SWG_ST_HOSTCAP && f.nw == 3u);
			break;
		case F_LATE2:
			CHECK(rc == -SWG_E_OLD && x.step == SWG_ST_FLAGS2 && f.nw == 2u);
			break;
		case F_SLOTDROP:
			CHECK(rc == -SWG_E_READBACK && f.nready == 0u && x.step == SWG_ST_R0_WR);
			CHECK(f.win2 == 0u && f.w[f.nw - 1u].sp == W && f.w[f.nw - 1u].v == 0u);   /* the window goes back to 0 for W17 */
			CHECK(f.nep == 0u && f.nrc == 0u);
			break;
		case F_RC_STUCK:
			CHECK(rc == -SWG_E_RCSTUCK && f.nready == 0u && f.nep == 0u);
			CHECK(f.nrc == 2u && f.w[f.nw - 1u].sp == SWG_SP_RC && f.w[f.nw - 1u].v == 0u);       /* tried, so undone */
			CHECK(has("SWEET WLAN: G2 undo ok (ep not stored, rc restored)"));
			break;
		case F_EP_STUCK:
			CHECK(rc == -SWG_E_READBACK && f.nready == 0u && x.step == SWG_ST_EP);
			CHECK(f.nep == 2u && f.w[f.nw - 2u].sp == SWG_SP_EP && f.w[f.nw - 2u].v == 2u);       /* a store that did not read back is still undone */
			CHECK(f.rc == 0x00100000u && f.w[f.nw - 1u].sp == SWG_SP_RC);
			CHECK(has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_ONES:
			CHECK(rc == -SWG_E_DEAD && has("G2 STOP: dead link") && x.step == SWG_ST_R2_MAX);
			break;
		case F_RDFAIL:
			CHECK(rc == -SWG_E_ADDR && has("G2 STOP: address") && x.step == SWG_ST_R0_BA && f.nw == 14u);
			break;
		case F_WINFAIL:
			CHECK(rc == -SWG_E_ADDR && x.step == SWG_ST_R0_WR && f.win2 == 0u);
			CHECK(has("WIFI GAP: g2 BAR2 window select refused"));
			break;
		case F_FATAL:
			CHECK(rc == -SWG_E_FATAL && f.nready == 1u && has("TRAP/ASSERT"));
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_NOTAKE:
			CHECK(rc == 0 && f.nready == 1u && f.ndelay == 19u && f.msdelay == 1900u);       /* a wait between the polls, none after the last */
			CHECK(has("WIFI GAP: g2 no change vs baseline after hostready") && !has("no dongle write seen"));
			CHECK(has("SWEET WLAN: G2 poll: no change in 20 polls; final flags 0x70050107 mb ptr 0x44868c mb data 0x0 tcm d2h_w 0x0 0x0 0x0 host - c30 0x0 ring_mem idx/type 0x0\n"));
			CHECK(!has("change vs baseline in") && !has("G2 poll values"));       /* a poll line is printed only for a change (W-D4a) */
			break;
		case F_UNDOFAIL:
			CHECK(rc < 0 && f.nready == 1u);
			CHECK(has("WIFI GAP: g2 stop: ep command restore failed") && has("restore=FAILED"));
			CHECK(f.rc == 0x00100000u);     /* the RC undo still runs after the EP undo failed */
			CHECK(x.stopped == 1 && x.code == SWG_E_STOPPED && x.restore == 0);
			break;
		case F_RC_UNDOFAIL:
			CHECK(rc < 0 && f.nready == 1u && f.ep == 0x00100002u);
			CHECK(has("WIFI GAP: g2 stop: rc command restore failed") && has("restore=FAILED") && !has("ep command restore failed"));
			CHECK(x.stopped == 1 && x.code == SWG_E_STOPPED && x.restore == 0);
			break;
		case F_RC_LATE:
			CHECK(rc == -SWG_E_RCSTUCK && x.step == SWG_ST_RC && f.nrc == 0u && f.nep == 0u && f.nready == 0u);
			CHECK(has("SWEET WLAN: G2 undo ok (ep not stored, rc not stored)"));
			break;
		case F_EP_LATE:
			CHECK(rc == -SWG_E_OLD && x.step == SWG_ST_EP && f.nep == 0u && f.nrc == 2u && f.nready == 0u && f.rc == 0x00100000u);
			CHECK(has("SWEET WLAN: G2 undo ok (ep not stored, rc restored)"));
			break;
		case F_READY_OLD: case F_READY_ONES:
			CHECK(rc == 0 && f.nready == 1u && x.mask == 0xFFFFFFFFu);      /* the doorbell has no old-value rule, not even the dead-link refusal */
			break;
		case F_EP_WRFAIL:
			CHECK(rc == -SWG_E_WR && x.step == SWG_ST_EP && f.nready == 0u && f.nep == 2u && f.nrc == 2u && f.ep == 0x00100002u && f.rc == 0x00100000u);
			CHECK(has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));        /* a store that returned an error may have landed: tried, so undone */
			break;
		case F_RC_WRFAIL:
			CHECK(rc == -SWG_E_WR && x.step == SWG_ST_RC && f.nready == 0u && f.nep == 0u && f.nrc == 2u && f.rc == 0x00100000u);
			CHECK(has("SWEET WLAN: G2 undo ok (ep not stored, rc restored)"));
			break;
		case F_POLL_DEAD:
			CHECK(rc == -SWG_E_DEAD && f.nready == 1u && has("G2 STOP: dead link") && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_FATAL_TRAP:
			CHECK(rc == -SWG_E_FATAL && f.nready == 1u && has("TRAP/ASSERT") && f.ep == 0x00100002u && f.rc == 0x00100000u);
			CHECK(has("SWEET WLAN: G2 poll 1 flags 0x70050507 mb data 0x0 TRAP/ASSERT\n"));       /* AP-85: 0x%x prints the zero word; the label says data (the line prints the data, not the pointer) */
			break;
		case F_POLL_RDFAIL: case F_POLL_RDFAIL2:
			CHECK(rc == -SWG_E_ADDR && f.nready == 1u && x.step == SWG_ST_READY && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			CHECK(f.ndelay == (i == F_POLL_RDFAIL ? 0u : 1u));
			break;
		case F_TAKE4:
			CHECK(rc == 0 && f.ndelay == 19u && has("WIFI GAP: g2 no change vs baseline after hostready") && !has("change vs baseline in"));   /* ring_mem idx/type is informational only */
			CHECK(has("host - c30 0x0 ring_mem idx/type 0x1\n"));        /* the last ring's word is read and printed in the final line */
			break;
		case F_RBFAIL:
			CHECK(rc == -SWG_E_READBACK && x.step == SWG_ST_R0_RD && f.nready == 0u && has("G2 STOP: read-back"));
			break;
		case F_RESTOREFAIL:
			CHECK(rc == -SWG_E_READBACK && f.nready == 0u && f.win2 == SWW_WIN_TAIL);       /* the window could not go back: the first reason stays the answer */
			CHECK(has("WIFI GAP: g2 BAR2 window select refused"));
			break;
		case F_CH_FL: case F_CH_MB: case F_CH_DW0: case F_CH_DW1: case F_CH_DW2: case F_CH_C30:
			/* W-D4a: each of the six words alone is detected and named; the other five are not */
			CHECK(rc == 0 && f.nready == 1u && f.ndelay == 0u && !has("no change"));
			CHECK(i != F_CH_FL || has("SWEET WLAN: G2 poll 1: change vs baseline in flags\n"));
			CHECK(i != F_CH_MB || has("SWEET WLAN: G2 poll 1: change vs baseline in mb data\n"));
			CHECK(i != F_CH_DW0 || has("SWEET WLAN: G2 poll 1: change vs baseline in tcm d2h_w[0]\n"));
			CHECK(i != F_CH_DW1 || has("SWEET WLAN: G2 poll 1: change vs baseline in tcm d2h_w[1]\n"));
			CHECK(i != F_CH_DW2 || has("SWEET WLAN: G2 poll 1: change vs baseline in tcm d2h_w[2]\n"));
			CHECK(i != F_CH_C30 || has("SWEET WLAN: G2 poll 1: change vs baseline in c30\n"));
			CHECK(i != F_CH_MB || has("mb ptr 0x44868c mb data 0x2 "));    /* the mailbox DATA was read (the pointer word never changes) */
			break;
		case F_FWHALT:
			CHECK(has("SWEET WLAN: G2 poll 1 mb data 0x10000000 FWHALT\n"));
			CHECK(rc == -SWG_E_FATAL && f.nready == 1u && has("FWHALT") && has("G2 STOP: fatal") && !has("change vs baseline in"));
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_C30_RDFAIL:
			CHECK(rc == -SWG_E_ADDR && f.nready == 0u && x.step == SWG_ST_READY && has("G2 STOP: address") && !has("G2 base "));       /* the baseline failed: hostready is never stored */
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_C30_ONES: case F_MB_ONES: case F_DW_ONES:
			/* review r1 F1: an all-ones word of the poll is a dead link, named, before the change test (the word changed from the baseline too) */
			CHECK(rc == -SWG_E_DEAD && f.nready == 1u && has("G2 STOP: dead link") && !has("change vs baseline in") && !has("FWHALT"));
			CHECK(i != F_C30_ONES || has("G2 STOP: PCIe core 0xC30 reads all ones"));
			CHECK(i != F_MB_ONES || has("G2 STOP: the d2h mailbox data word reads all ones"));
			CHECK(i != F_DW_ONES || has("G2 STOP: tcm d2h_w[1] reads all ones"));
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_BASE_ONES:
			CHECK(rc == -SWG_E_DEAD && f.nready == 0u && x.step == SWG_ST_READY && has("G2 STOP: dead link") && has("G2 STOP: PCIe core 0xC30 reads all ones") && !has("G2 base "));
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
			break;
		case F_VDEVS: case F_IDXBASE: case F_IDXEND3: case F_WINEDGE: case F_OVL_EDGE:
			CHECK(rc == 0 && x.mask == 0xFFFFFFFFu && f.nready == 1u && f.win2 == 0u);       /* edges that are inside: max_vdevs ignored, first / last RAM word, window edge */
			break;
		}
	}
	/* review 0174 W-D4a r1 F2 / AP-239: every stop at step READY (baseline or poll) on the layout with every polled word on the tail window: cfg 0x84 is on the tail when the stop comes, and the run must put it back to 0 */
	{
		static const struct { int kind; int code; unsigned nready; const char *what; } st[] = {
			{ F_POLL_DEAD, SWG_E_DEAD, 1u, "G2 STOP: dead link" }, { F_FATAL_TRAP, SWG_E_FATAL, 1u, "TRAP/ASSERT" }, { F_POLL_RDFAIL, SWG_E_ADDR, 1u, "G2 STOP: address" },
			{ F_POLL_RDFAIL2, SWG_E_ADDR, 1u, "G2 STOP: address" }, { F_FWHALT, SWG_E_FATAL, 1u, "FWHALT" }, { F_C30_RDFAIL, SWG_E_ADDR, 0u, "G2 STOP: address" },
			{ F_C30_ONES, SWG_E_DEAD, 1u, "PCIe core 0xC30 reads all ones" }, { F_MB_ONES, SWG_E_DEAD, 1u, "the d2h mailbox data word reads all ones" },
			{ F_DW_ONES, SWG_E_DEAD, 1u, "tcm d2h_w[1] reads all ones" }, { F_BASE_ONES, SWG_E_DEAD, 0u, "PCIe core 0xC30 reads all ones" },
		};
		unsigned k;

		tail_all = 1;
		ov_sh = 0x40a67cu;
		ov_ri = 0x482884u;
		for (k = 0; k < sizeof st / sizeof st[0]; k++) {
			mk(&x, &p, st[k].kind);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			if (rc != -st[k].code || f.win2 != 0u) {
				fprintf(stderr, "tail layout stop case %u: rc %d (want %d) win %#x\n%s", k, rc, -st[k].code, f.win2, logbuf);
			}
			CHECK(rc == -st[k].code && x.step == SWG_ST_READY && f.nready == st[k].nready && has(st[k].what));
			CHECK(f.win2 == 0u);            /* the window went back to 0 although it sat on the tail at the stop */
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo ok (ep restored, rc restored)"));
		}
		tail_all = 0;
		ov_sh = ov_ri = 0u;
	}

	/* ---- W-D4b: the ring-0 post (sweetwlan=13). The default boot (level 12, want_d4b 0) is the run above: 45 stores, no host-block or 0xC34/0x140 access ---- */
	{
		const uint32_t ev = (uint32_t)(PA + p.b[SWD_EVBUF].off);
		const uint32_t r0 = p.b[SWD_H2D_CTRL].off, hw0 = p.b[SWD_IDX_H2D_W].off;
		const struct rec d4[] = {
			{ SWG_SP_PCIE, 0xC34u, 0xFFFFu },
			{ SWG_SP_HOST, r0 + 0u, 0x0180000Du }, { SWG_SP_HOST, r0 + 4u, 1u }, { SWG_SP_HOST, r0 + 8u, 0x2000u }, { SWG_SP_HOST, r0 + 12u, 0u }, { SWG_SP_HOST, r0 + 16u, ev },
			{ SWG_SP_HOST, r0 + 20u, 8u }, { SWG_SP_HOST, r0 + 24u, 0u }, { SWG_SP_HOST, r0 + 28u, 0u }, { SWG_SP_HOST, r0 + 32u, 0u }, { SWG_SP_HOST, r0 + 36u, 0u },
			{ SWG_SP_HOST, hw0, 1u }, { SWG_SP_PCIE, 0x140u, 0xFF000001u }, { SWG_SP_PCIE, 0xC34u, 0u }, { SWG_SP_EP, 0x04u, 2u }, { SWG_SP_RC, 0x04u, 0u },
		};
		unsigned k, base, j;

		CHECK(HI(SWD_EVBUF) == 8u && p.b[SWD_EVBUF].off % 4096u == 0u && p.b[SWD_EVBUF].len == 65536u);
		/* the default boot with the D4b machinery present: the gate prints once, nothing of D4b is stored or read */
		want_d4b = 0;
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.nstore == 45u && f.nhrd == 0u && f.ndb0 == 0u && f.nunm == 0u && f.c34 == 0u);
		CHECK(has("SWEET WLAN: D4b not run: sweetwlan level 13/14 not set (G2 only)\n") && !has("D4b base") && !has("WIFI GAP: d4b"));
		CHECK(has("SWEET WLAN: G2 done rc=0 stores=45 mask 0xffffffff restore=ok\n"));
		for (k = 0; k < f.nw; k++) {
			CHECK(f.w[k].sp != SWG_SP_HOST);
		}
		/* gate: the level is 13 but G2 stopped before hostready (the first store did not read back): D4b not run, said once */
		want_d4b = 2;
		mk(&x, &p, F_NOWRITE);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWG_E_READBACK && has("SWEET WLAN: D4b not run: G2 did not reach hostready and the poll with rc 0") && f.nhrd == 0u && f.c34 == 0u && f.ndb0 == 0u);
		/* the post (sweetwlan=14): stores in order, each once, the unmask before the EP and RC restores */
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		if (rc != 0) {
			fputs(logbuf, stderr);
		}
		CHECK(f.ndelay == 0u);          /* the poll stops at the first host write */
		CHECK(rc == 0 && x.nstore == 59u && x.d4mask == ((1ull << (SWG_D4_DB0 + 1u)) - 1u) + (1ull << SWG_D4_UNMASK) && x.restore == 1);     /* sweetwlan=14: the W-D4b rows and the unmask, no W-D4c row */
		base = find_rec(SWG_SP_PCIE, 0x144u, 0x12345678u, 0u);
		CHECK(base < f.nw);
		for (k = 0, j = base + 1u; k < sizeof d4 / sizeof d4[0]; k++) {
			unsigned at = find_rec(d4[k].sp, d4[k].a, d4[k].v, j);

			if (at == f.nw) {
				fprintf(stderr, "d4b store %u (sp %d %#x := %#x) missing after record %u\n", k, d4[k].sp, d4[k].a, d4[k].v, j);
			}
			CHECK(at < f.nw);
			j = at + 1u;
		}
		{
			unsigned nd = 0;

			for (k = base + 1u; k < f.nw; k++) {
				nd += f.w[k].sp != SWG_SP_WIN2;
			}
			CHECK(nd == sizeof d4 / sizeof d4[0]);     /* nothing else after hostready but window moves: each store exactly once */
		}
		CHECK(f.c34 == 0u && f.ndb0 == 1u && f.nunm == 1u && f.hb[hw0 / 4u] == 1u && f.hb[r0 / 4u] == 0x0180000Du && f.hb[r0 / 4u + 4u] == ev && f.hb[r0 / 4u + 5u] == 8u);
		CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && f.win2 == 0u);
		CHECK(has("SWEET WLAN: D4b base h2d_r0 0x0 d2h_w0 0x0 ring2 w0 0x0 c30 0x0\n"));
		CHECK(has("G2 store d4b mask off 0xc34 old 0 := 0xffff read back 0xffff\n") && has("G2 store d4b item w5 host ") && has("G2 store d4b h2d_w slot 0 host ") && has("G2 store d4b db0 off 0x140 := 0xff000001 (old and read-back not made, golden writes only)\n"));
		CHECK(has("G2 store d4b unmask off 0xc34 := 0 read back 0\n"));
		CHECK(strstr(logbuf, "G2 store d4b mask") < strstr(logbuf, "G2 store d4b item w0") && strstr(logbuf, "G2 store d4b item w9") < strstr(logbuf, "G2 store d4b h2d_w slot 0") &&
		      strstr(logbuf, "G2 store d4b h2d_w slot 0") < strstr(logbuf, "G2 store d4b db0") && strstr(logbuf, "G2 store d4b db0") < strstr(logbuf, "G2 store d4b unmask") &&
		      strstr(logbuf, "G2 store d4b unmask") < strstr(logbuf, "G2 undo ok"));
		CHECK(has("SWEET WLAN: D4b poll 1: h2d_r0 0x0 -> 0x1\n") && has("SWEET WLAN: D4b poll 1: d2h_w0 0x0 -> 0x1\n"));
		CHECK(has("SWEET WLAN: D4b poll h2d_r0 0x1 d2h_w0 0x1 ring2 item 0 type 0xe epoch 0x1 xor ok c30 0x0 tcm d2h_w 0x1 0x0 0x0\n"));
		CHECK(has("WIFI GAP: d4b event buffer size unknown, using 8192 x1 [medium]") && has("WIFI GAP: d4b interrupts not enabled, mask 0xFFFF, no ELBI/GIC path x1 [low]") && has("WIFI GAP: d4b interrupts not enabled, mask 0xFFFF with no ELBI/GIC path (poll only)\n") &&
		      has("WIFI GAP: d4b only 1 buffer posted (DHD posts 8+32) x1 [low]") && !has("flush_dcache64 used as invalidate") &&
		      has("WIFI GAP: d4b ring-2 item type 0xe not handled x1 [high]"));
		CHECK(!has("no dongle write seen") && !has("epoch/xor mismatch") && !has("D4b not run"));
		CHECK(has("SWEET WLAN: G2 done rc=0 stores=59 mask 0xffffffff restore=ok\n"));
		/* run d4b13: item 0 and D2H-WR slot 0 = 1 are there before the post; the reply (item 1, D2H-WR = 2) comes at the 3rd poll. The poll must not stop at poll 1 on the old item and must decode item 1 */
		d4kind = D_PRE_ITEM;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		if (rc != 0) {
			fputs(logbuf, stderr);
		}
		CHECK(rc == 0 && x.nstore == 59u && f.ndb0 == 1u && f.late == 1u && f.ndelay == 2u);
		CHECK(has("SWEET WLAN: D4b ro h2d_r0 0x0 d2h_w0 host 0x1 tcm 0x1 ring2 w 0x1800024 0x0 0x0 0x2 0x0 0x1800026 type 0x24 epoch 0x1 xor ok c30 0x0\n"));
		CHECK(has("SWEET WLAN: D4b poll h2d_r0 0x0 d2h_w0 0x2 ring2 item 1 type 0xe epoch 0x2 xor ok c30 0x0 tcm d2h_w 0x1 0x0 0x0\n"));
		CHECK(!has("SWEET WLAN: D4b poll 1: d2h_w0") && has("SWEET WLAN: D4b poll 3: d2h_w0 0x1 -> 0x2\n") && !has("no dongle write seen") && !has("ring 2 wrap"));
		CHECK(has("WIFI GAP: d4b ring-2 item type 0x24 not handled x1 [high]") && f.c34 == 0u && f.nunm == 1u);
		CHECK(has("WIFI GAP: d4b ring-2 item type 0xe not handled x1 [high]") && !has("epoch/xor mismatch"));     /* item 1 has epoch 2 (1 + k) and raises its own type gap */
		/* run d4b13 + no reply to the post (the likely L14 case: an event buffer completes only with an event): D2H-WR stays 1 = item 0 only, item 1 stays zero. That is the no-write gap,
		 * NOT "host item is zero" (no index passed item 1); the baseline 0x24 gap stays */
		d4kind = D_PRE_NOANS;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.nstore == 59u && f.ndb0 == 1u && f.late == 0u && f.ndelay == 19u && has("WIFI GAP: d4b no dongle write seen in 1900 ms x1 [high]"));
		CHECK(!has("host item is zero") && has("WIFI GAP: d4b ring-2 item type 0x24 not handled x1 [high]"));
		CHECK(has("SWEET WLAN: D4b poll h2d_r0 0x0 d2h_w0 0x1 ring2 item 1 type 0x0 epoch 0x0 xor n/a c30 0x0 tcm d2h_w 0x1 0x0 0x0\n"));
		/* the baseline slot is at the ring end (wrap before the post): loud gap, nothing decoded */
		d4kind = D_PRE_WRAP;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && has("WIFI GAP: d4b d2h ring 2 wrap before the post, item not decoded x1 [high]") && has("WIFI GAP: d4b d2h ring 2 wrap before the post (baseline d2h_w0 0x40, max items 64), item not decoded\n") && !has("ring-2 item type") && has("ring2 item 0 type 0x0 epoch 0x0 xor n/a"));
		/* no answer: the gap, not a failure; the poll waited its 19 x 100 ms */
		d4kind = D_NODONGLE;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && f.ndb0 == 1u && f.c34 == 0u && f.nunm == 1u && f.ndelay == 19u && f.msdelay == 1900u && has("WIFI GAP: d4b no dongle write seen in 1900 ms x1 [high]"));
		CHECK(!has("only TCM/core words changed"));      /* nothing changed at all: no "only TCM" line */
		CHECK(has("WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache), after the poll\n"));     /* TCM d2h_w[0] is 1 (set at hostready), the item is zero: the post-poll check (review r1 F3) */
		CHECK(has("SWEET WLAN: D4b poll h2d_r0 0x0 d2h_w0 0x0 ring2 item 0 type 0x0 epoch 0x0 xor n/a c30 0x0 tcm d2h_w 0x1 0x0 0x0\n") && !has("ring-2 item type"));
		/* review r1 F3: a change of a core word alone (0xC30 here; the dongle moves it and TCM d2h_w[0] on its own) is NOT a dongle write into host memory: the high gap still prints, with its own line for what did change */
		d4kind = D_C30_ONLY;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && f.ndb0 == 1u && f.ndelay == 19u && has("SWEET WLAN: D4b poll 1: c30 0x0 -> 0x5\n") && has("WIFI GAP: d4b no dongle write seen in 1900 ms x1 [high]") && has("SWEET WLAN: D4b only TCM/core words changed (no host-memory write)\n"));
		/* the dongle's index landed (host d2h_w[0] = 1) but ring-2 item 0 is still zero: a write was seen (no "no dongle write" gap) and the post-poll zero-item check is the high gap */
		d4kind = D_D2HW_ZERO;
		mk(&x, &p, F_NOTAKE);      /* the hostready store leaves TCM d2h_w[0] at 0, so only the host index d2h_w[0] makes the post-poll check true */
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && !has("no dongle write seen") && !has("only TCM/core words changed") && has("SWEET WLAN: D4b poll 1: d2h_w0 0x0 -> 0x1\n"));
		CHECK(has("WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache), after the poll\n") && has("WIFI GAP: d4b dongle advanced ring 2 but host item is zero x1 [high]"));
		/* host h2d_r[0] alone moved: a host-memory write was seen */
		d4kind = D_H2DR_ONLY;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && !has("no dongle write seen") && !has("only TCM/core words changed") && has("SWEET WLAN: D4b poll 1: h2d_r0 0x0 -> 0x1\n"));
		/* and a real answer (item + index) raises neither */
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && !has("after the poll") && !has("only TCM/core words changed") && !has("no dongle write seen"));
		d4kind = D_BADEPOCH;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && has("ring2 item 0 type 0xe epoch 0x2 xor ok c30 0x0 tcm") && has("WIFI GAP: d4b epoch/xor mismatch x1 [high]") && has("WIFI GAP: d4b ring-2 item type 0xe not handled"));
		d4kind = D_BADXOR;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && has("ring2 item 0 type 0xe epoch 0x1 xor bad c30 0x0 tcm") && has("WIFI GAP: d4b epoch/xor mismatch x1 [high]"));
		/* stops: every exit path after Q2 unmasks FIRST (before the EP and RC restores); a stop before Q2 stores nothing of D4b */
		{
			static const struct { int kind; int code; int mask_stored; unsigned nitems; int slot, db0;      /* slot: the slot store was attempted */ const char *what; } st[] = {
				{ D_BASE_ONES, SWG_E_DEAD, 0, 0u, 0, 0, "G2 STOP: host h2d_r[0] reads all ones" },
				{ D_MASK_OLD, SWG_E_OLD, 0, 0u, 0, 0, "G2 STOP: old value" },
				{ D_MASK_DEAD, SWG_E_DEAD, 0, 0u, 0, 0, "G2 STOP: dead link" },
				{ D_MASK_RB, SWG_E_READBACK, 1, 0u, 0, 0, "G2 STOP: read-back" },
				{ D_MASK_WRFAIL, SWG_E_WR, 0, 0u, 0, 0, "G2 STOP: store" },       /* review r2 F11: a refused mask arms no unmask, no FAILED line */
				{ D_ITEM_WRFAIL, SWG_E_WR, 1, 3u, 0, 0, "G2 STOP: store" },
				{ D_ITEM_RB, SWG_E_READBACK, 1, 3u, 0, 0, "G2 STOP: read-back" },
				{ D_ITEM_OLD, SWG_E_OLD, 1, 5u, 0, 0, "G2 STOP: old value" },
				{ D_SLOT_WRFAIL, SWG_E_WR, 1, 10u, 1, 0, "G2 STOP: store" },
				{ D_SLOT_OLD, SWG_E_OLD, 1, 10u, 0, 0, "G2 STOP: old value" },
				{ D_DB0_WRFAIL, SWG_E_WR, 1, 10u, 1, 0, "G2 STOP: store" },
				{ D_FWHALT, SWG_E_FATAL, 1, 10u, 1, 1, "FWHALT" },
				{ D_TRAP, SWG_E_FATAL, 1, 10u, 1, 1, "TRAP/ASSERT" },
				{ D_C30_ONES, SWG_E_DEAD, 1, 10u, 1, 1, "PCIe core 0xC30 reads all ones" },
				{ D_POLL_D2HW_ONES, SWG_E_DEAD, 1, 10u, 1, 1, "host d2h_w[0] reads all ones" },
				{ D_POLL_HRDFAIL, SWG_E_ADDR, 1, 10u, 1, 1, "G2 STOP: address" },
			};

			for (k = 0; k < sizeof st / sizeof st[0]; k++) {
				unsigned um, epi, rci, nh;

				d4kind = st[k].kind;
				mk(&x, &p, F_OK);
				rc = swg_go(&x);
				logbuf[loglen] = 0;
				if (rc != -st[k].code) {
					fprintf(stderr, "d4b stop case %u: rc %d (want %d)\n%s", k, rc, -st[k].code, logbuf);
				}
				CHECK(rc == -st[k].code && has(st[k].what));
				nh = 0;
				for (j = base; j < f.nw; j++) {
					nh += f.w[j].sp == SWG_SP_HOST;
				}
				CHECK(f.c34 == (st[k].kind == D_MASK_OLD ? 1u : st[k].kind == D_MASK_DEAD ? 0xFFFFFFFFu : 0u) && f.ep == 0x00100002u && f.rc == 0x00100000u && f.win2 == 0u && x.restore == 1);
				CHECK(f.ndb0 == (unsigned)st[k].db0);
				CHECK(f.nunm == (unsigned)st[k].mask_stored);            /* the unmask is stored if and only if the mask store was made (review r2 F11) */
				CHECK(find_rec(SWG_SP_PCIE, 0xC34u, 0xFFFFu, 0u) < f.nw ? (st[k].mask_stored == 1 || st[k].kind == D_MASK_WRFAIL) : st[k].mask_stored == 0);    /* a refused store is recorded too */
				CHECK(!has("0xC34 := 0 FAILED") && !has("d4b mask 0xC34 restore failed"));
				CHECK(nh == st[k].nitems + (unsigned)st[k].slot);   /* the host-block stores made (a refused one is recorded too): items, then the slot */
				CHECK(find_rec(SWG_SP_HOST, hw0, 1u, 0u) < f.nw ? st[k].slot == 1 : st[k].slot == 0);
				if (st[k].mask_stored) {
					um = find_rec(SWG_SP_PCIE, 0xC34u, 0u, 0u);
					epi = find_rec(SWG_SP_EP, 0x04u, 2u, um + 1u);
					rci = find_rec(SWG_SP_RC, 0x04u, 0u, um + 1u);
					CHECK(um < f.nw && um < epi && epi < rci && rci < f.nw);       /* Q8: 0xC34 := 0 before the EP restore before the RC restore */
					CHECK(strstr(logbuf, "G2 store d4b unmask") != NULL && strstr(logbuf, "G2 store d4b unmask") < strstr(logbuf, "G2 undo ok"));
				}
			}
		}
		/* the unmask cannot take: loud (WIFI GAP + a STOP line), restore FAILED, the EP and RC restores still run */
		for (k = 0; k < 2u; k++) {
			d4kind = k == 0u ? D_UNMASK_FAIL : D_UNMASK_WRFAIL;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc != 0 && x.restore == 0 && f.nunm == 1u && has("SWEET WLAN: G2 STOP: d4b mask 0xC34 := 0 FAILED (read back ") && has("WIFI GAP: g2 stop: d4b mask 0xC34 restore failed"));
			CHECK(f.ep == 0x00100002u && f.rc == 0x00100000u && has("SWEET WLAN: G2 undo FAILED") && has("restore=FAILED"));
		}
		/* the event buffer must be a 4 KiB aligned range inside the block: refused before any D4b store */
		for (k = 0; k < 4u; k++) {
			d4kind = D_OK;
			mk(&x, &p, F_OK);
			x.in.evbuf = k == 0u ? x.in.evbuf - 128u : k == 1u ? x.in.evbuf + 0x100000u : k == 3u ? PA + (p.total & ~4095u) : x.in.evbuf;      /* k 3: 4 KiB aligned, starts inside the block, its 8192 B run past the end (total is not a multiple of 4096 since W-D4d) */
			if (k == 2u) {
				x.in.evbuf -= 4096u;
				x.in.blk = x.in.evbuf + 4096u;         /* the buffer starts before the block */
			}
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_ADDR && f.c34 == 0u && f.ndb0 == 0u && f.nunm == 0u && has("G2 STOP: address") && !has("D4b base"));
		}
		/* order, called directly: a row out of order or a second time stops */
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		CHECK(swg_entry(&x) == 0 && swg_d4_row(&x, SWG_D4_ITEM0) == -SWG_E_ORDER && f.c34 == 0u && f.nunm == 0u);
		mk(&x, &p, F_OK);
		CHECK(swg_entry(&x) == 0 && swg_d4_row(&x, SWG_D4_MASK) == 0 && swg_d4_row(&x, SWG_D4_DB0) == -SWG_E_ORDER && f.ndb0 == 0u && f.nunm == 1u && f.c34 == 0u);
		mk(&x, &p, F_OK);
		CHECK(swg_entry(&x) == 0 && swg_d4_row(&x, SWG_D4_MASK) == 0 && (x.stopped = 0, swg_d4_row(&x, SWG_D4_MASK)) == -SWG_E_ORDER);
		want_d4b = 0;
		d4kind = D_OK;

		/* sweetwlan=13: read-only. No store beyond G2's 45, no host write, no 0xC34 / 0x140 access; the dongle advanced TCM d2h_w[0] by itself (run G2-d4a) while the host item is still zero: the gap */
		want_d4b = 1;
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.nstore == 45u && f.ndb0 == 0u && f.nunm == 0u && f.c34 == 0u && f.nhrd == 8u);
		for (k = 0; k < f.nw; k++) {
			CHECK(f.w[k].sp != SWG_SP_HOST);
		}
		CHECK(has("SWEET WLAN: D4b ro h2d_r0 0x0 d2h_w0 host 0x0 tcm 0x1 ring2 w 0x0 0x0 0x0 0x0 0x0 0x0 type 0x0 epoch 0x0 xor n/a c30 0x0\n"));
		CHECK(has("WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache)\n") && has("WIFI GAP: d4b dongle advanced ring 2 but host item is zero x1 [high]"));
		CHECK(!has("D4b base") && !has("G2 store d4b") && !has("event buffer size") && !has("D4b poll") && !has("D4b not run") && !has("flush_dcache64 used as invalidate"));
		CHECK(has("SWEET WLAN: G2 done rc=0 stores=45 mask 0xffffffff restore=ok\n"));
		/* the host item and index are there: the check runs on it */
		d4kind = D_RO_HOST;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.nstore == 45u && f.ndb0 == 0u);
		CHECK(has("SWEET WLAN: D4b ro h2d_r0 0x1 d2h_w0 host 0x1 tcm 0x1 ring2 w 0x100000e 0x11111111 0x22222222 0x0 0x0 0x3233333d type 0xe epoch 0x1 xor ok c30 0x0\n"));
		CHECK(has("WIFI GAP: d4b ring-2 item type 0xe not handled x1 [high]") && !has("host item is zero") && !has("epoch/xor mismatch"));
		/* the item is there and the TCM index moved, the host index is still 0: the check runs (either index) */
		d4kind = D_RO_TCM_ITEM;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && has("d2h_w0 host 0x0 tcm 0x1 ring2 w 0x100000e 0x11111111 0x22222222 0x0 0x0 0x3233333d type 0xe epoch 0x1 xor ok c30 0x0\n") && !has("host item is zero"));
		/* a wrong epoch is named */
		d4kind = D_RO_BADEPOCH;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && has("type 0xe epoch 0x2 xor ok c30 0x0\n") && has("WIFI GAP: d4b epoch/xor mismatch x1 [high]") && has("WIFI GAP: d4b ring-2 item type 0xe not handled x1 [high]"));
		/* a dead link in the observation stops, nothing stored */
		d4kind = D_BASE_ONES;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWG_E_DEAD && has("G2 STOP: host h2d_r[0] reads all ones") && f.c34 == 0u && f.nunm == 0u && f.ep == 0x00100002u && f.rc == 0x00100000u);
		want_d4b = 0;
		d4kind = D_OK;
	}
	/* ---- W-D4b gaps: what G2 reads for F1/F2, the 2BYTE pin, the host-block gap, the deferral (a gap prints iff nobody read the word) ---- */
	{
		unsigned k, eqat, readyat;
		static const char *const miss_keys[][2] = {
			{ "f1", SWL_M_F1_CON }, { "f1", SWL_M_F1_MB }, { "f1", SWL_M_F1_RI }, { "f2", SWL_M_F2_MB }, { "f2", SWL_M_F2_RI }, { "w15", SWL_M_W15 }, { "g1", SWL_M_G1 }, { "g2", SWL_M_G2HOST },
		};

		/* the dump at level 12: reads only, the lines, the console text (CR/NUL dropped, 0x01 shown as '.'), the window moves 0 and the tail only */
		want_d4b = 0;
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.rd_mb == 1 && x.rd_ri == 1 && x.rd_con == 1 && x.con_n == 64u && x.pins_ok == 1);
		CHECK(has("SWEET WLAN: G2 read for F1/F2: h2d mb 0x0 d2h mb 0x0 rings_info 0x3b1b6c console 64 bytes\n"));
		CHECK(has("SWEET WLAN: G2 read for F1/F2: h2d mb ptr 0x448688 d2h mb ptr 0x44868c\n"));
		CHECK(has("SWEET WLAN: G2 rings_info +0x00: 0x3b1bb8 0x407800 0x207f40 0x3e7d80\n") && has("SWEET WLAN: G2 rings_info +0x30: 0x0 0x2b0028 0x4 0x0\n") && has("SWEET WLAN: G2 rings_info +0x40: 0x0 0x0 0x0\n"));
		CHECK(has("SWEET WLAN: G2 console 0x3f0000: buffer 0x3f0100 size 64 write index 23\n"));
		CHECK(has("SWEET WLAN: fw console: RTE 1\n") && has("SWEET WLAN: fw console: wl0: ok\n") && has("SWEET WLAN: fw console: .x.\n") && has("SWEET WLAN: fw console: end\n") && !has("\r"));
		CHECK(strstr(logbuf, "G2 read for F1/F2: h2d mb 0x0") < strstr(logbuf, "G2 store flags2") && strstr(logbuf, "G2 pins verified") < strstr(logbuf, "G2 read for F1/F2: h2d mb 0x0"));
		CHECK(has("SWEET WLAN: G2 pins verified: rw_index_sz 4 (2BYTE clear), rings 43/4\n"));
		/* reads only: no store before flags2 but the two window moves of the dump */
		CHECK(f.w[0].sp == W && f.w[1].sp == W && f.w[2].sp == T && f.w[2].a == SH + 0x50u);
		/* a word G2 cannot read is a line and a gap of its own, never silent, and G2 goes on; the F1/F2 misses (deferred) are resolved exactly for the words that were read */
		{
			static const struct { const char *what; uint32_t at, v, at2, v2, failbp; int mb, con, ri; unsigned n; const char *msg; } bad_[] = {
				{ "h2d mailbox pointer 0", SH + 0x28u, 0u, 0u, 0u, 0u, 0, 1, 1, 64u, NULL },
				{ "h2d mailbox pointer unaligned", SH + 0x28u, MBH + 2u, 0u, 0u, 0u, 0, 1, 1, 64u, NULL },
				{ "h2d mailbox pointer below the RAM", SH + 0x28u, 0x1ffffcu, 0u, 0u, 0u, 0, 1, 1, 64u, NULL },
				{ "console pointer 0", SH + 0x14u, 0u, 0u, 0u, 0u, 1, 0, 1, 0u, "hnd_cons_t not readable" },
				{ "console pointer unaligned", SH + 0x14u, CON + 2u, 0u, 0u, 0u, 1, 0, 1, 0u, "hnd_cons_t not readable" },
				{ "console size 0", CON + 12u, 0u, CON + 16u, 0u, 0u, 1, 0, 1, 0u, "is not a word aligned part of the RAM, not read" },
				{ "console size not a word multiple", CON + 12u, 62u, 0u, 0u, 0u, 1, 0, 1, 0u, "is not a word aligned part of the RAM, not read" },
				{ "console index beyond the size", CON + 16u, 65u, 0u, 0u, 0u, 1, 0, 1, 0u, "is not a word aligned part of the RAM, not read" },
				{ "console buffer unaligned", CON + 8u, CBUF + 2u, 0u, 0u, 0u, 1, 0, 1, 0u, "is not a word aligned part of the RAM, not read" },
				{ "console buffer below the RAM", CON + 8u, 0x1ffffcu, 0u, 0u, 0u, 1, 0, 1, 0u, "is not a word aligned part of the RAM, not read" },
				{ "console index equal to the size (allowed)", CON + 16u, 64u, 0u, 0u, 0u, 1, 1, 1, 64u, NULL },
				{ "ring_info last word unreadable", 0u, 0u, 0u, 0u, RI + 0x48u, 1, 1, 0, 64u, NULL },
				{ "ring_info word +0x3c unreadable", 0u, 0u, 0u, 0u, RI + 0x3Cu, 1, 1, 0, 64u, NULL },
				{ "console word unreadable mid-line", 0u, 0u, 0u, 0u, CBUF + 0x10u, 1, 0, 1, 0u, "SWEET WLAN: fw console: .\nSWEET WLAN: G2 console word" },
				{ "h2d mailbox data unreadable", 0u, 0u, 0u, 0u, MBH, 0, 1, 1, 64u, NULL },
			};

			swl_miss_defer = 1;
			for (k = 0; k < sizeof bad_ / sizeof bad_[0]; k++) {
				memset(swl_miss_tab, 0, sizeof swl_miss_tab);
				swl_miss_n = 0;
				mk(&x, &p, F_OK);
				if (bad_[k].at != 0u) {
					f.ram[bad_[k].at / 4u] = bad_[k].v;
				}
				if (bad_[k].at2 != 0u) {
					f.ram[bad_[k].at2 / 4u] = bad_[k].v2;
				}
				rd_fail_bp = bad_[k].failbp;
				swl_miss("f1", SWL_M_F1_CON, "medium");
				swl_miss("f1", SWL_M_F1_MB, "high");
				swl_miss("f1", SWL_M_F1_RI, "high");
				swl_miss("f2", SWL_M_F2_MB, "high");
				swl_miss("f2", SWL_M_F2_RI, "high");
				rc = swg_go(&x);
				rd_fail_bp = 0u;
				logbuf[loglen] = 0;
				if (rc != 0 || x.rd_mb != bad_[k].mb || x.rd_con != bad_[k].con || x.rd_ri != bad_[k].ri || x.con_n != bad_[k].n) {
					fprintf(stderr, "dump case %s: rc %d mb %d con %d ri %d n %u\n", bad_[k].what, rc, x.rd_mb, x.rd_con, x.rd_ri, x.con_n);
				}
				CHECK(rc == 0 && x.restore == 1 && x.rd_mb == bad_[k].mb && x.rd_con == bad_[k].con && x.rd_ri == bad_[k].ri && x.con_n == bad_[k].n);
				CHECK(swl_miss_tab[0].res == (unsigned)bad_[k].con && swl_miss_tab[1].res == (unsigned)bad_[k].mb && swl_miss_tab[2].res == (unsigned)bad_[k].ri &&
				      swl_miss_tab[3].res == (unsigned)bad_[k].mb && swl_miss_tab[4].res == (unsigned)bad_[k].ri);
				CHECK(bad_[k].msg == NULL || has(bad_[k].msg));
				CHECK(bad_[k].ri || has("WIFI GAP: g2 dump: rings_info not read x1"));
				CHECK(!bad_[k].ri || !has("dump: rings_info not read"));
				CHECK(bad_[k].mb || has("WIFI GAP: g2 dump: mailbox data not read x1"));
				CHECK(!bad_[k].mb || !has("dump: mailbox data not read"));
				CHECK(bad_[k].con || (has("console 0 bytes (not read)") && has("WIFI GAP: g2 dump: firmware console not read x1")));
				CHECK(!bad_[k].con || (!has("(not read)") && !has("dump: firmware console not read")));
			}
			swl_miss_defer = 0;
			memset(swl_miss_tab, 0, sizeof swl_miss_tab);
			swl_miss_n = 0;
		}
		/* a long console line is wrapped at 120 characters; the cap and the line end are exact */
		mk(&x, &p, F_OK);
		f.ram[(CON + 12u) / 4u] = 0x200u;
		f.ram[(CON + 16u) / 4u] = 300u;
		memset(&f.ram[CBUF / 4u], 0, 0x200u);
		memset(&f.ram[CBUF / 4u], 'z', 300u);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.con_n == 0x200u && has("SWEET WLAN: fw console: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz\n"));
		{
			char *l1 = strstr(logbuf, "fw console: zzz"), *l2 = l1 != NULL ? strstr(l1 + 1, "fw console: zzz") : NULL, *l3 = l2 != NULL ? strstr(l2 + 1, "fw console: zzz") : NULL, *l4 = l3 != NULL ? strstr(l3 + 1, "fw console: zzz") : NULL;

			CHECK(l1 && l2 && l3 && !l4 && (size_t)(l2 - l1) == strlen("fw console: ") + 120u + 1u + strlen("SWEET WLAN: ") && (size_t)(l3 - l2) == (size_t)(l2 - l1));       /* 120, 120, 60 characters */
			CHECK(l3 != NULL && strstr(l3, "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz\n") == l3 + strlen("fw console: ") && l3[strlen("fw console: ") + 60u] == '\n');
		}
		/* the console wraps and crosses the window edge: the buffer ends at 0x400010 and the write index is 8 words in: the text is read word by word through both windows */
		mk(&x, &p, F_OK);
		memset(&f.ram[CBUF / 4u], 0, 64u);
		f.ram[(CON + 8u) / 4u] = 0x3fffe0u;
		f.ram[(CON + 12u) / 4u] = 0x30u;
		f.ram[(CON + 16u) / 4u] = 0x18u;
		memcpy(&f.ram[0x3fffe0u / 4u], "AAAAAAAAAAAAAAAAAAAAAAAA", 24u);      /* bytes 0..23 */
		memcpy(&f.ram[0x3ffff8u / 4u], "bbbbbbbbbbbbbbbbbbbbbbbb", 24u);      /* bytes 24..47: 8 bytes below 0x400000, 16 above */
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.con_n == 0x30u && has("SWEET WLAN: fw console: bbbbbbbbbbbbbbbbbbbbbbbbAAAAAAAAAAAAAAAAAAAAAAAA\n"));
		CHECK(find_rec(W, 0x84u, 0x400000u, 0u) < f.nw);
		/* a console bigger than the cap prints only the last 2048 bytes before the index */
		mk(&x, &p, F_OK);
		f.ram[(CON + 8u) / 4u] = 0x200000u + 0x1000u;
		f.ram[(CON + 12u) / 4u] = 0x1000u;                       /* 4096-byte buffer, all 'x' except the first 8 bytes */
		f.ram[(CON + 16u) / 4u] = 0x1000u;
		memset(&f.ram[(0x200000u + 0x1000u) / 4u], 'x', 0x1000u);
		memset(&f.ram[(0x200000u + 0x1000u) / 4u], 'y', 8u);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.con_n == 2048u && has("SWEET WLAN: fw console: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n") && !has("yyyy"));
		/* a console buffer that runs past the RAM end is refused (not read, a gap) */
		mk(&x, &p, F_OK);
		f.ram[(CON + 8u) / 4u] = END - 0x20u;
		f.ram[(CON + 12u) / 4u] = 0x40u;
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.rd_con == 0 && has("is not a word aligned part of the RAM, not read") && has("WIFI GAP: g2 dump: firmware console not read x1"));

		/* the 2BYTE pin (flags 0x100000 set): a STOP at the entry before any store; clear: the verified line */
		mk(&x, &p, F_OK);
		f.ram[SH / 4u] |= 0x100000u;
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWG_E_FLAGS && f.nw == 0u && x.pins_ok == 0 && !has("G2 pins verified") && has("PCIE_SHARED_2BYTE_INDICES (0x100000) set") && has("G2 STOP: firmware flags"));
		/* the ring counts failing: no verified line either */
		mk(&x, &p, F_MAXSUB);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWG_E_ENTRY && x.pins_ok == 0 && !has("G2 pins verified"));

		/* the deferral: with swl_miss_defer set the F1/F2, w15, g1 and host-block gaps are only recorded; G2's reads resolve them, swl_miss_flush() prints the rest as gaps. Level 12: the dump resolves
		 * the five F1/F2 words and the g1 pins, the host-block gap is the level-12 one at once (D4b off), w15 stays open (its store is stage-3 row W15 at level 15, SPEC-WIFI D-12) */
		swl_miss_defer = 1;
		swl_miss_n = 0;
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		mk(&x, &p, F_OK);
		for (k = 0; k < 8u; k++) {
			swl_miss(miss_keys[k][0], miss_keys[k][1], "high");
		}
		CHECK(swl_miss_n == 8u && !has("WIFI GAP: f1") && !has("WIFI GAP: g1") && !has("WIFI GAP: w15"));
		CHECK(has("SWEET WLAN: miss f1 console pointer outside the RAM window, console not read (deferred: a G2 read may resolve it)\n"));
		swl_miss("f1", SWL_M_F1_CON, "medium");                    /* a repeat is one entry */
		CHECK(swl_miss_n == 8u);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0);
		for (k = 0; k < 5u; k++) {
			char line[160];

			snprintf(line, sizeof line, "SWEET WLAN: F1/F2 miss %s %s resolved by G2 read\n", miss_keys[k][0], miss_keys[k][1]);
			CHECK(has(line) && swl_miss_tab[k].res == 1u);
		}
		CHECK(has("SWEET WLAN: F1/F2 miss g1 rw_index_sz and the ring counts are pinned from run F3, not re-read from the dongle resolved by G2 read\n") && swl_miss_tab[6].res == 1u);
		CHECK(swl_miss_tab[5].res == 0u && swl_miss_tab[7].res == 0u && !has("resolved by G2 read\nSWEET WLAN: F1/F2 miss w15"));      /* w15: level 15 only (stage 3, not G2); host block: swg_base took the level-12 gap at once, the miss stays open */
		{
			char *m = strstr(logbuf, "F1/F2 miss f1 console pointer");

			CHECK(m != NULL && strstr(logbuf, "G2 done rc=0") > m);      /* resolved at the end of the run */
		}
		{
			uint32_t n0 = swl_gap_tot;

			swl_miss_flush();
			logbuf[loglen] = 0;
			CHECK(swl_gap_tot - n0 == 2u && swl_miss_n == 0u);       /* exactly the two unresolved ones become gaps */
			CHECK(has("WIFI GAP: w15 cfg 0x88 OR 0x300 (DHD attach write) not implemented x1 [high]") && has("WIFI GAP: g2 host block not read (D4b read did not run) x1 [high]"));
			CHECK(!has("WIFI GAP: f1 console pointer") && !has("WIFI GAP: f2 mailbox") && !has("WIFI GAP: g1 rw_index_sz"));
		}
		/* G2 reads nothing (stopped at its first entry check): every miss stays open, so every gap prints (the original text) */
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		swl_miss_n = 0;
		mk(&x, &p, F_RC_START);
		for (k = 0; k < 8u; k++) {
			swl_miss(miss_keys[k][0], miss_keys[k][1], "medium");
		}
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWG_E_RCSTUCK && x.rd_mb == 0 && x.rd_ri == 0 && x.rd_con == 0 && x.pins_ok == 0);
		for (k = 0; k < 8u; k++) {
			CHECK(swl_miss_tab[k].res == 0u);
		}
		swl_miss_flush();
		logbuf[loglen] = 0;
		CHECK(has("WIFI GAP: f1 console pointer outside the RAM window, console not read x1") && has("WIFI GAP: f2 rings_info_ptr not readable in window 0, not read x1") && has("WIFI GAP: g1 rw_index_sz and the ring counts are pinned"));
		/* the entry stops AFTER the 2BYTE/count pins were verified (ring_mem pointer): the g1 miss is resolved, the F1/F2 ones are not (the dump never ran) */
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		swl_miss_n = 0;
		mk(&x, &p, F_RINGMEM);
		for (k = 0; k < 8u; k++) {
			swl_miss(miss_keys[k][0], miss_keys[k][1], "medium");
		}
		rc = swg_go(&x);
		CHECK(rc == -SWG_E_ADDR && x.pins_ok == 1 && swl_miss_tab[6].res == 1u && swl_miss_tab[0].res == 0u && swl_miss_tab[3].res == 0u);
		swl_miss_n = 0;
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		/* a full table is a gap at once, never lost; with the defer flag clear a miss is a plain gap */
		mk(&x, &p, F_OK);
		swl_miss_n = SWL_MISS_MAX;
		swl_miss("f1", "some other word", "low");
		logbuf[loglen] = 0;
		CHECK(swl_miss_n == SWL_MISS_MAX && has("WIFI GAP: f1 some other word x1 [low]"));
		swl_miss_n = 0;
		swl_miss_defer = 0;
		mk(&x, &p, F_OK);
		swl_miss("f2", SWL_M_F2_MB, "high");
		logbuf[loglen] = 0;
		CHECK(swl_miss_n == 0u && has("WIFI GAP: f2 mailbox data pointers not readable in window 0, not read x1 [high]"));
		CHECK(swl_miss_resolve("f2", SWL_M_F2_MB) == 0);

		/* sweetwlan=13: the host block is read; with the defer flag the host-block miss is recorded by swg_base and resolved by swg_d4b; if D4b does not read it, the flush prints the gap (original text) */
		swl_miss_defer = 1;
		want_d4b = 1;
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && x.hostrd == 1 && swl_miss_n == 1u && swl_miss_tab[0].res == 1u && !has("level 12: D4b observation off") &&
		      has("SWEET WLAN: F1/F2 miss g2 host block not read (D4b read did not run) resolved by G2 read\n"));
		swl_miss_flush();
		logbuf[loglen] = 0;
		CHECK(!has("WIFI GAP: g2 host block not read"));
		d4kind = D_RO_HRDFAIL;                                      /* the host block cannot be read: the observation stops, hostrd stays 0 and the miss stays open (the gap prints at the flush) */
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		swl_miss_n = 0;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc == -SWG_E_ADDR && x.hostrd == 0 && swl_miss_n == 1u && swl_miss_tab[0].res == 0u);
		swl_miss_flush();
		logbuf[loglen] = 0;
		CHECK(has("WIFI GAP: g2 host block not read (D4b read did not run) x1 [medium]"));
		swl_miss_n = 0;
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		d4kind = D_BASE_ONES;                                       /* the observation stops at its first read: the host block was read, but a dead link is a STOP; the flag is set by the read, not by the verdict */
		mk(&x, &p, F_NOWRITE);                                      /* G2 stops at its first store: D4b not run, the miss is only recorded when swg_base ran: it did not */
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		CHECK(rc != 0 && x.hostrd == 0 && swl_miss_n == 0u);
		swl_miss_n = 0;
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		d4kind = D_OK;
		mk(&x, &p, F_NOTAKE);
		rc = swg_go(&x);                                            /* G2 runs through the poll without a change: still rc 0, D4b runs and reads */
		CHECK(rc == 0 && x.hostrd == 1);
		swl_miss_n = 0;
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		swl_miss_defer = 0;

		/* ---- sweetwlan=14: the 16-bit index-slot stores and the ring-0 post. cfg 0x88 |= 0x300 is NOT G2's any more (SPEC-WIFI D-12, review 0174-w-d4b-hwwrite-r1 F1): stage-3 row W15, tested below ---- */
		want_d4b = 2;
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		logbuf[loglen] = 0;
		if (rc != 0) {
			fputs(logbuf, stderr);
		}
		CHECK(rc == 0 && f.nc88w == 0u && f.nc88r == 0u && x.nstore == 59u && x.mask == 0xFFFFFFFFu && !has("cfg 0x88"));
		CHECK(f.nrd140 == 0u && f.ndb0 == 1u && has("G2 store d4b db0 off 0x140 := 0xff000001 (old and read-back not made, golden writes only)\n"));     /* review r1 F6 */
		/* the w15 gap: a miss that no G2 level resolves (the store is stage-3 row W15 at level 15) */
		swl_miss_defer = 1;
		for (k = 0; k < 3u; k++) {
			want_d4b = k == 0u ? 2 : (int)(k - 1u);
			memset(swl_miss_tab, 0, sizeof swl_miss_tab);
			swl_miss_n = 0;
			mk(&x, &p, F_OK);
			swl_miss("w15", SWL_M_W15, "medium");
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && swl_miss_tab[0].res == 0u && !has("F1/F2 miss w15"));
		}
		swl_miss_defer = 0;
		memset(swl_miss_tab, 0, sizeof swl_miss_tab);
		swl_miss_n = 0;
		want_d4b = 2;
		d4kind = D_OK;
		mk(&x, &p, F_OK);
		rc = swg_go(&x);
		/* position: the EP Command store, then the baseline, then the hostready store; no cfg 0x88 line anywhere */
		eqat = find_rec(SWG_SP_EP, 0x04u, 6u, 0u);
		readyat = find_rec(SWG_SP_PCIE, 0x144u, 0x12345678u, 0u);
		CHECK(eqat < readyat && readyat < f.nw);
		CHECK(strstr(logbuf, "G2 store ep command") < strstr(logbuf, "G2 base flags") && strstr(logbuf, "G2 base flags") < strstr(logbuf, "G2 store hostready") && strstr(logbuf, "cfg 0x88") == NULL);
		/* the ten index slots: 16-bit stores of 0 (recorded with sp | 0x100), no 32-bit store of a slot address, the upper half kept */
		{
			unsigned n16 = 0, n32slot = 0;
			static const uint32_t slots[] = { I_HW, I_HW + 4u, I_HR, I_HR + 4u, I_DW, I_DW + 4u, I_DW + 8u, I_DR, I_DR + 4u, I_DR + 8u };

			for (k = 0; k < f.nw && k < NREC; k++) {
				unsigned s;

				for (s = 0; s < sizeof slots / sizeof slots[0]; s++) {
					if (f.w[k].a == slots[s] && f.w[k].sp == (T | 0x100)) {
						n16++;
						CHECK(f.w[k].v == 0u);
					}
					if (f.w[k].a == slots[s] && f.w[k].sp == T) {
						n32slot++;
					}
				}
			}
			CHECK(n16 == 10u && n32slot == 0u && f.n16 == 10u);
			CHECK(f.ram[I_HW / 4u] == 0x00010000u && f.ram[I_DR / 4u + 2u] == 0xBEEF0000u);     /* the upper halves are kept, the low halves are 0 */
			CHECK(has("G2 store ring0 wr slot bp 0x407800 old 0x10003 := 0x10000 read back 0x10000 (16-bit store, upper half unchanged)\n"));
		}
		CHECK(has("WIFI GAP: g2 ring max/len and Command stores still 32-bit (D-9)") && !has("WIFI GAP: g2 16-bit golden stores made as 32-bit words"));
		/* levels 12 and 13 store the slots as 32-bit words (the old D-9 gap) and make no store beyond the 45 of G2 (review r1 F4): every bus write is either one of want[] (compared as a set, the table rows) or a cfg 0x84 move to 0 / SWW_WIN_TAIL;
		 * none to the host block, 0xC34, 0x140 or cfg 0x88. The glue's own cfg 0x80 moves (sw_g2_bar0) are list entries of four values (SWW_WIN_PCIE, the entry window, 0, SWW_WIN_TAIL), pinned by the glue list harness. */
		for (k = 0; k < 2u; k++) {
			unsigned j, nrow = 0, nwin = 0;

			want_d4b = (int)k;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && f.nc88w == 0u && f.nc88r == 0u && f.n16 == 0u && x.nstore == 45u && f.nrd140 == 0u);
			CHECK(has("WIFI GAP: g2 16-bit golden stores made as 32-bit words (D-9)") && !has("cfg 0x88") && !has("(16-bit store"));
			CHECK(find_rec(T, I_HW, 0x00010000u, 0u) < f.nw);
			for (j = 0; j < f.nw && j < NREC; j++) {
				unsigned m, inwant = 0;

				if (f.w[j].sp == SWG_SP_WIN2) {
					CHECK(f.w[j].a == 0x84u && (f.w[j].v == 0u || f.w[j].v == SWW_WIN_TAIL));
					nwin++;
					continue;
				}
				for (m = 0; m < nwant; m++) {
					inwant |= want[m].sp == f.w[j].sp && want[m].a == f.w[j].a && want[m].v == f.w[j].v;
				}
				CHECK(inwant && f.w[j].sp != SWG_SP_HOST);
				nrow++;
			}
			CHECK(nrow == 45u && nrow + nwin == f.nw);
		}
		want_d4b = 2;
		/* the 16-bit store: a failing store, a dropped store (the slot keeps its value: the read-back differs) and an access that clears the upper half are STOPs, never a pass; a bus without wr16 refuses */
		{
			static const struct { int kind; int code; } s16[] = { { D_S16_WRFAIL, SWG_E_WR }, { D_S16_DROP, SWG_E_READBACK }, { D_S16_WIDE, SWG_E_READBACK } };

			for (k = 0; k < sizeof s16 / sizeof s16[0]; k++) {
				d4kind = s16[k].kind;
				mk(&x, &p, F_OK);
				rc = swg_go(&x);
				logbuf[loglen] = 0;
				if (rc != -s16[k].code) {
					fprintf(stderr, "s16 case %u: rc %d (want %d)\n%s", k, rc, -s16[k].code, logbuf);
				}
				CHECK(rc == -s16[k].code && f.n16 >= 1u && f.nready == 0u && x.restore == 1 && f.ep == 0x00100002u);
			}
			d4kind = D_OK;
			mk(&x, &p, F_OK);
			x.b = &bus_no16;
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_WR && f.n16 == 0u && has("G2 STOP: store"));
		}
		/* ---- W-D4c (sweetwlan=16, docs/research/W-D4c-ds-ack.md s.6.5): the dongle pre-wrote ring-2 item 0 = D2H_MAILBOX_DATA with DS_ENTER_REQ (run d4b13, F1); the host runs the stock inband-device-wake sequence:
		 * A1 ack (ring-0 item 1, mb 0x2, epoch 2, H2D-WR := 2, doorbell 0xFF000002), C1 (D2H-RD := 1, 0xDD020001), W (item 2, mb 0x40, epoch 3, H2D-WR := 3, 0xFF000003, 1 ms), the exit-note poll on host memory only,
		 * then on the note D (item 3, mb 0x20, epoch 4, H2D-WR := 4, 0xFF000004) and C2 (D2H-RD := 2, 0xDD020002). Values are literals (F3, F4), not the header's macros. ---- */
		{
			const uint32_t r0 = p.b[SWD_H2D_CTRL].off, hw0 = p.b[SWD_IDX_H2D_W].off, dr0 = p.b[SWD_IDX_D2H_R].off;
			const struct rec c[] = {
				{ SWG_SP_HOST, r0 + 40u, 0x02800023u }, { SWG_SP_HOST, r0 + 44u, 0u }, { SWG_SP_HOST, r0 + 48u, 2u }, { SWG_SP_HOST, r0 + 52u, 0u }, { SWG_SP_HOST, r0 + 56u, 0u },
				{ SWG_SP_HOST, r0 + 60u, 0u }, { SWG_SP_HOST, r0 + 64u, 0u }, { SWG_SP_HOST, r0 + 68u, 0u }, { SWG_SP_HOST, r0 + 72u, 0u }, { SWG_SP_HOST, r0 + 76u, 0u },
				{ SWG_SP_HOST, hw0, 2u }, { SWG_SP_PCIE, 0x140u, 0xFF000002u },
				{ SWG_SP_HOST, dr0, 1u }, { SWG_SP_PCIE, 0x140u, 0xDD020001u },
				{ SWG_SP_HOST, r0 + 80u, 0x03800023u }, { SWG_SP_HOST, r0 + 84u, 0u }, { SWG_SP_HOST, r0 + 88u, 0x40u }, { SWG_SP_HOST, r0 + 92u, 0u }, { SWG_SP_HOST, r0 + 96u, 0u },
				{ SWG_SP_HOST, r0 + 100u, 0u }, { SWG_SP_HOST, r0 + 104u, 0u }, { SWG_SP_HOST, r0 + 108u, 0u }, { SWG_SP_HOST, r0 + 112u, 0u }, { SWG_SP_HOST, r0 + 116u, 0u },
				{ SWG_SP_HOST, hw0, 3u }, { SWG_SP_PCIE, 0x140u, 0xFF000003u },
				{ SWG_SP_HOST, r0 + 120u, 0x04800023u }, { SWG_SP_HOST, r0 + 124u, 0u }, { SWG_SP_HOST, r0 + 128u, 0x20u }, { SWG_SP_HOST, r0 + 132u, 0u }, { SWG_SP_HOST, r0 + 136u, 0u },
				{ SWG_SP_HOST, r0 + 140u, 0u }, { SWG_SP_HOST, r0 + 144u, 0u }, { SWG_SP_HOST, r0 + 148u, 0u }, { SWG_SP_HOST, r0 + 152u, 0u }, { SWG_SP_HOST, r0 + 156u, 0u },
				{ SWG_SP_HOST, hw0, 4u }, { SWG_SP_PCIE, 0x140u, 0xFF000004u },
				{ SWG_SP_HOST, dr0, 2u }, { SWG_SP_PCIE, 0x140u, 0xDD020002u },
				{ SWG_SP_PCIE, 0xC34u, 0u }, { SWG_SP_EP, 0x04u, 2u }, { SWG_SP_RC, 0x04u, 0u },
			};
			static const struct { uint32_t type, mb, epoch, dw; int xorbad; const char *gap, *line; } no[] = {
				{ 0x24u, 0x4u, 1u, 1u, 0, "WIFI GAP: d4c d2h mailbox bits 0x4 not handled x1 [high]", "D4c not answered: d2h mailbox bits 0x4 not handled (item 0 type 0x24 mailbox data 0x4 epoch/xor check 0)" },
				{ 0x24u, 0x6u, 1u, 1u, 0, "WIFI GAP: d4c d2h mailbox bits 0x4 not handled x1 [high]", "D4c not answered: d2h mailbox bits 0x4 not handled (item 0 type 0x24 mailbox data 0x6 epoch/xor check 0)" },
				{ 0x24u, 0x0u, 1u, 1u, 0, "WIFI GAP: d4c ring-2 item 0 mailbox data has no DS_ENTER_REQ, not answered x1 [high]", "D4c not answered: ring-2 item 0 mailbox data has no DS_ENTER_REQ, not answered (item 0 type 0x24 mailbox data 0x0 epoch/xor check 0)" },
				{ 0x24u, 0x2u, 1u, 1u, 1, "WIFI GAP: d4c ring-2 item 0 failed the epoch/xor check, not answered x1 [high]", "D4c not answered: ring-2 item 0 failed the epoch/xor check, not answered (item 0 type 0x24 mailbox data 0x2 epoch/xor check 2)" },
				{ 0x24u, 0x2u, 2u, 1u, 0, "WIFI GAP: d4c ring-2 item 0 failed the epoch/xor check, not answered x1 [high]", "D4c not answered: ring-2 item 0 failed the epoch/xor check, not answered (item 0 type 0x24 mailbox data 0x2 epoch/xor check 1)" },
				{ 0x25u, 0x2u, 1u, 1u, 0, "WIFI GAP: d4c ring-2 item 0 is not D2H_MAILBOX_DATA, not answered x1 [high]", "D4c not answered: ring-2 item 0 is not D2H_MAILBOX_DATA, not answered (item 0 type 0x25 mailbox data 0x2 epoch/xor check 0)" },
				{ 0x24u, 0x2u, 1u, 2u, 0, "WIFI GAP: d4c d2h ring 2 holds items 1..1, not consumed, no answer x1 [high]", "D4c not answered: d2h ring 2 holds items 1..1, not consumed, no answer (item 0 type 0x24 mailbox data 0x2 epoch/xor check 0)" },
				{ 0x24u, 0x2u, 1u, 3u, 0, "WIFI GAP: d4c d2h ring 2 holds items 1..2, not consumed, no answer x1 [high]", "D4c not answered: d2h ring 2 holds items 1..2, not consumed, no answer (item 0 type 0x24 mailbox data 0x2 epoch/xor check 0)" },
				{ 0x24u, 0x2u, 1u, 0u, 0, "WIFI GAP: d4c d2h_w0 is 0 now, item 0 is not there, no answer x1 [high]", "D4c not answered: d2h_w0 is 0 now, item 0 is not there, no answer (item 0 type 0x24 mailbox data 0x2 epoch/xor check 0)" },
				{ 0x24u, 0x2u, 1u, 64u, 0, "WIFI GAP: d4c d2h ring 2 wrapped before the post, nothing answered x1 [high]", "D4c not answered: d2h ring 2 wrapped before the post, nothing answered (item 0 type 0x24 mailbox data 0x2 epoch/xor check 0)" },
				{ 0u, 0u, 0u, 0u, 0, "WIFI GAP: d4c no ring-2 item 0 at the baseline, nothing to answer x1 [high]", "D4c not answered: no ring-2 item 0 at the baseline, nothing to answer (item 0 type 0x0 mailbox data 0x0 epoch/xor check -1)" },
			};
			char want[320];
			unsigned k, base, j, nd;
			/* the exit-note misses: the dongle answers the wake with a wrong item (or none); each is its own gap, no deassert (stores = 85 = 59 + A1 12 + C1 2 + W 12) */
			static const struct { uint32_t type, mb, epoch, dw; int xorbad; const char *gap; } nm[] = {
				{ 0x24u, 0x6u, 2u, 2u, 0, "WIFI GAP: d4c ring-2 item 1 mailbox data 0x6 is not DS_EXIT_NOTE only x1 [high]" },
				{ 0x24u, 0x0u, 2u, 2u, 0, "WIFI GAP: d4c ring-2 item 1 mailbox data 0x0 is not DS_EXIT_NOTE only x1 [high]" },
				{ 0x24u, 0x2u, 2u, 2u, 0, "WIFI GAP: d4c ring-2 item 1 mailbox data 0x2 is not DS_EXIT_NOTE only x1 [high]" },
				{ 0x24u, 0x4u, 3u, 2u, 0, "WIFI GAP: d4c ring-2 item 1 failed the epoch/xor check (1) x1 [high]" },
				{ 0x24u, 0x4u, 2u, 2u, 1, "WIFI GAP: d4c ring-2 item 1 failed the epoch/xor check (2) x1 [high]" },
				{ 0x25u, 0x4u, 2u, 2u, 0, "WIFI GAP: d4c ring-2 item 1 type 0x25 is not D2H_MAILBOX_DATA x1 [high]" },
				{ 0x24u, 0x4u, 2u, 3u, 0, "WIFI GAP: d4c d2h ring 2 write index 3 after the wake, expected 2 x1 [high]" },
				{ 0x24u, 0x4u, 2u, 0u, 0, "WIFI GAP: d4c d2h ring 2 write index 0 after the wake, expected 2 x1 [high]" },
			};

			want_d4b = 3;
			d4kind = D_MB;
			mb_type = 0x24u;
			mb_mb = 0x2u;
			mb_epoch = 1u;
			mb_dw = 1u;
			mb_xorbad = 0;
			wr_fail_host_a = 0u;
			mb_note = 1;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			if (rc != 0) {
				fputs(logbuf, stderr);
			}
			CHECK(rc == 0 && x.nstore == 99u && x.restore == 1 && has("SWEET WLAN: G2 done rc=0 stores=99 mask 0xffffffff restore=ok\n"));
			CHECK(f.ndelay == 20u && f.msdelay == 1901u);     /* the 19 L14 poll delays (the fake never answers the event buffer post), the wake's one delay of 1 ms, the note is there at the first exit-note poll: no more */
			CHECK(x.d4mask == (1ull << SWG_D4_N) - 1u);       /* sweetwlan=16: all 54 rows read back */
			base = find_rec(SWG_SP_PCIE, 0x140u, 0xFF000001u, 0u);
			CHECK(base < f.nw);
			for (k = 0, j = base + 1u; k < sizeof c / sizeof c[0]; k++) {
				unsigned at = find_rec(c[k].sp, c[k].a, c[k].v, j);

				if (at == f.nw) {
					fprintf(stderr, "d4c store %u (sp %d %#x := %#x) missing after record %u\n", k, c[k].sp, c[k].a, c[k].v, j);
				}
				CHECK(at < f.nw);
				j = at + 1u;
			}
			for (k = base + 1u, nd = 0; k < f.nw; k++) {
				nd += f.w[k].sp != SWG_SP_WIN2;
			}
			CHECK(nd == sizeof c / sizeof c[0]);     /* after the L14 doorbell: the 40 W-D4c stores, the unmask and the EP and RC restores, nothing else, in exactly this order */
			CHECK(f.ndbrd == 1u && f.ndbwr == 1u && f.ndbwake == 1u && f.ndbdeas == 1u && f.ndbrd2 == 1u && f.ndb0 == 1u && f.nunm == 1u && f.c34 == 0u);
			CHECK(f.npoll_d2hw == 1u && f.nh_after_c2 == 8u && f.nh_deas_c2 == 2u);     /* the note is there at the first poll: one D2H-WR read; ONE final read (8 host words) only after the C2 doorbell, between the deassert and the C2 doorbell only the C2 slot row's old and read-back reads */
			CHECK(f.nrdwin == 0u && f.nwrwin == 0u);     /* from the ack doorbell to the deassert doorbell (the exit-note poll included): no TCM, core, config or window access, host memory and doorbells only */
			CHECK(f.hb[dr0 / 4u] == 2u && f.hb[hw0 / 4u] == 4u && f.hb[r0 / 4u + 10u] == 0x02800023u && f.hb[r0 / 4u + 12u] == 2u && f.hb[r0 / 4u + 20u] == 0x03800023u && f.hb[r0 / 4u + 22u] == 0x40u &&
			      f.hb[r0 / 4u + 30u] == 0x04800023u && f.hb[r0 / 4u + 32u] == 0x20u && f.hb[r0 / 4u + 11u] == 0u && f.hb[r0 / 4u + 31u] == 0u && f.hb[r0 / 4u + 39u] == 0u);
			CHECK(f.hb[r0 / 4u] == 0x0180000Du);     /* ring-0 item 0 (the L14 event buffer post) untouched */
			snprintf(want, sizeof want, "G2 store d4c rd1 d2h_r slot 2 host %#x old 0 := 0x1 read back 0x1\n", dr0);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c rd2 d2h_r slot 2 host %#x old 0x1 := 0x2 read back 0x2\n", dr0);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c ack h2d_w slot 0 host %#x old 0x1 := 0x2 read back 0x2\n", hw0);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c wake h2d_w slot 0 host %#x old 0x2 := 0x3 read back 0x3\n", hw0);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c deas h2d_w slot 0 host %#x old 0x3 := 0x4 read back 0x4\n", hw0);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c ack item w0 host %#x old 0 := 0x2800023 read back 0x2800023\n", r0 + 40u);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c ack item w2 host %#x old 0 := 0x2 read back 0x2\n", r0 + 48u);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c wake item w0 host %#x old 0 := 0x3800023 read back 0x3800023\n", r0 + 80u);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c wake item w2 host %#x old 0 := 0x40 read back 0x40\n", r0 + 88u);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c deas item w0 host %#x old 0 := 0x4800023 read back 0x4800023\n", r0 + 120u);
			CHECK(has(want));
			snprintf(want, sizeof want, "G2 store d4c deas item w2 host %#x old 0 := 0x20 read back 0x20\n", r0 + 128u);
			CHECK(has(want));
			CHECK(has("G2 store d4c ack db0 off 0x140 := 0xff000002 (old and read-back not made, golden writes only)\n") && has("G2 store d4c rd1 db0 off 0x140 := 0xdd020001 (old and read-back not made, golden writes only)\n") &&
			      has("G2 store d4c wake db0 off 0x140 := 0xff000003 (old and read-back not made, golden writes only)\n") && has("G2 store d4c deas db0 off 0x140 := 0xff000004 (old and read-back not made, golden writes only)\n") &&
			      has("G2 store d4c rd2 db0 off 0x140 := 0xdd020002 (old and read-back not made, golden writes only)\n"));
			CHECK(strstr(logbuf, "G2 store d4b db0 off") < strstr(logbuf, "G2 store d4c ack item w0") && strstr(logbuf, "G2 store d4c ack item w9") < strstr(logbuf, "G2 store d4c ack h2d_w slot 0") &&
			      strstr(logbuf, "G2 store d4c ack h2d_w slot 0") < strstr(logbuf, "G2 store d4c ack db0") && strstr(logbuf, "G2 store d4c ack db0") < strstr(logbuf, "G2 store d4c rd1 d2h_r slot 2") &&
			      strstr(logbuf, "G2 store d4c rd1 d2h_r slot 2") < strstr(logbuf, "G2 store d4c rd1 db0") && strstr(logbuf, "G2 store d4c rd1 db0") < strstr(logbuf, "SWEET WLAN: D4c ring-2 item 0 DS_ENTER_REQ answered (DS_ACK)\n") &&
			      strstr(logbuf, "SWEET WLAN: D4c ring-2 item 0 DS_ENTER_REQ answered (DS_ACK)\n") < strstr(logbuf, "G2 store d4c wake item w0") && strstr(logbuf, "G2 store d4c wake item w9") < strstr(logbuf, "G2 store d4c wake h2d_w slot 0") &&
			      strstr(logbuf, "G2 store d4c wake h2d_w slot 0") < strstr(logbuf, "G2 store d4c wake db0") && strstr(logbuf, "G2 store d4c wake db0") < strstr(logbuf, "SWEET WLAN: D4c exit-note poll after 1 ms:") &&
			      strstr(logbuf, "SWEET WLAN: D4c exit-note poll after 1 ms:") < strstr(logbuf, "G2 store d4c deas item w0") && strstr(logbuf, "G2 store d4c deas item w9") < strstr(logbuf, "G2 store d4c deas h2d_w slot 0") &&
			      strstr(logbuf, "G2 store d4c deas h2d_w slot 0") < strstr(logbuf, "G2 store d4c deas db0") && strstr(logbuf, "G2 store d4c deas db0") < strstr(logbuf, "G2 store d4c rd2 d2h_r slot 2") &&
			      strstr(logbuf, "G2 store d4c rd2 d2h_r slot 2") < strstr(logbuf, "G2 store d4c rd2 db0") && strstr(logbuf, "G2 store d4c rd2 db0") < strstr(logbuf, "SWEET WLAN: D4c DS handshake done (ack, wake, exit note, deassert)\n") &&
			      strstr(logbuf, "SWEET WLAN: D4c DS handshake done (ack, wake, exit note, deassert)\n") < strstr(logbuf, "G2 store d4b unmask") && strstr(logbuf, "G2 store d4b unmask") < strstr(logbuf, "G2 undo ok"));
			CHECK(has("SWEET WLAN: D4c exit-note poll after 1 ms: h2d_r0 0x0 d2h_w0 0x2 ring2 item 1 w 0x2800024 0x0 0x0 0x4 0x0 0x2800020\n"));
			CHECK(!has("WIFI GAP: d4c") && !has("D4c not answered") && !has("ring-2 item type 0x24 not handled") && !has("epoch/xor mismatch") && !has("D4c only TCM/core"));
			CHECK(has("SWEET WLAN: D4b ro h2d_r0 0x0 d2h_w0 host 0x1 tcm 0x1 ring2 w 0x1800024 0x0 0x0 0x2 0x0 0x1800026 type 0x24 epoch 0x1 xor ok c30 0x0\n"));
			/* every failed precondition: its gap and line, and not one D4c store (59 stores = the L14 count), no doorbell */
			for (k = 0; k < sizeof no / sizeof no[0]; k++) {
				mb_type = no[k].type;
				mb_mb = no[k].mb;
				mb_epoch = no[k].epoch;
				mb_dw = no[k].dw;
				mb_xorbad = no[k].xorbad;
				mk(&x, &p, F_OK);
				rc = swg_go(&x);
				logbuf[loglen] = 0;
				if (rc != 0 || !has(no[k].gap) || x.nstore != 59u) {
					fprintf(stderr, "d4c refusal case %u: rc %d stores %u\n%s", k, rc, x.nstore, logbuf);
				}
				CHECK(rc == 0 && has(no[k].gap) && x.nstore == 59u && x.restore == 1);
				snprintf(want, sizeof want, "SWEET WLAN: %s\n", no[k].line);
				CHECK(has(want));
				CHECK(f.ndbrd == 0u && f.ndbwr == 0u && f.ndbwake == 0u && f.hb[dr0 / 4u] == 0u && f.hb[hw0 / 4u] == 1u && f.hb[r0 / 4u + 10u] == 0u);
				CHECK(find_rec(SWG_SP_PCIE, 0x140u, 0xDD020001u, 0u) == f.nw && find_rec(SWG_SP_PCIE, 0x140u, 0xFF000002u, 0u) == f.nw && !has("DS_ENTER_REQ answered") && !has("G2 store d4c"));
			}
			mb_type = 0x24u; mb_mb = 0x2u; mb_epoch = 1u; mb_dw = 1u; mb_xorbad = 0;
			/* no DS_EXIT_NOTE (the dongle never answers the wake): the poll waits its 19 x 100 ms after the 1 ms, WIFI GAP, no deassert, no second read index, rc 0 (stock stays DEV_WAKE); the window holds no access at all */
			mb_note = 0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			if (rc != 0 || x.nstore != 85u) {
				fputs(logbuf, stderr);
			}
			CHECK(rc == 0 && x.nstore == 85u && x.restore == 1 && has("SWEET WLAN: G2 done rc=0 stores=85 mask 0xffffffff restore=ok\n") && has("WIFI GAP: d4c no DS_EXIT_NOTE in 1901 ms after the wake assert x1 [high]") && !has("WIFI GAP: d4c d2h ring 2 write index") && !has("WIFI GAP: d4c ring-2 item 1"));
			CHECK(f.ndbrd == 1u && f.ndbwr == 1u && f.ndbwake == 1u && f.ndbdeas == 0u && f.ndbrd2 == 0u && f.hb[hw0 / 4u] == 3u && f.hb[dr0 / 4u] == 1u && f.hb[r0 / 4u + 30u] == 0u);
			CHECK(f.ndelay == 39u && f.msdelay == 3801u && f.nrdwin == 0u && f.nwrwin == 0u);
			CHECK(f.npoll_d2hw == 20u && f.nh_after_c2 == 0u);     /* exactly 20 polls (literal), 19 waits of 100 ms between them; no read after a C2 doorbell that was never rung */
			CHECK(find_rec(SWG_SP_PCIE, 0x140u, 0xFF000004u, 0u) == f.nw && find_rec(SWG_SP_PCIE, 0x140u, 0xDD020002u, 0u) == f.nw && !has("DS handshake done") && !has("G2 store d4c deas") && !has("G2 store d4c rd2"));
			CHECK(has("SWEET WLAN: D4c exit-note poll after 1901 ms: h2d_r0 0x0 d2h_w0 0x1 ring2 item 1 w 0x0 0x0 0x0 0x0 0x0 0x0\n"));
			/* a wrong item, index or bits is its own gap and no deassert either */
			mb_note = 1;
			for (k = 0; k < sizeof nm / sizeof nm[0]; k++) {
				mb_ntype = nm[k].type;
				mb_nmb = nm[k].mb;
				mb_nepoch = nm[k].epoch;
				mb_ndw = nm[k].dw;
				mb_nxor = nm[k].xorbad;
				mk(&x, &p, F_OK);
				rc = swg_go(&x);
				logbuf[loglen] = 0;
				if (rc != 0 || !has(nm[k].gap) || x.nstore != 85u) {
					fprintf(stderr, "d4c exit-note case %u: rc %d stores %u\n%s", k, rc, x.nstore, logbuf);
				}
				CHECK(rc == 0 && x.nstore == 85u && x.restore == 1 && has(nm[k].gap) && !has("no DS_EXIT_NOTE in"));
				CHECK(f.ndbwake == 1u && f.ndbdeas == 0u && f.ndbrd2 == 0u && f.ndelay == 20u && !has("DS handshake done") && !has("G2 store d4c deas") && f.nrdwin == 0u && f.nwrwin == 0u);
			}
			mb_ntype = 0x24u; mb_nmb = 0x4u; mb_nepoch = 2u; mb_ndw = 2u; mb_nxor = 0;
			/* the note lands at the 3rd delay after the wake doorbell (the 1 ms, then two polls of 100 ms): accepted, 201 ms */
			mb_nlate = 3;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && x.nstore == 99u && f.ndelay == 22u && f.msdelay == 2101u && has("SWEET WLAN: D4c exit-note poll after 201 ms: h2d_r0 0x0 d2h_w0 0x2 ring2 item 1 w 0x2800024 0x0 0x0 0x4 0x0 0x2800020\n") && has("DS handshake done") && !has("WIFI GAP: d4c"));
			mb_nlate = 0;
			/* dead link in the exit-note poll (h2d_r[0] reads all ones): STOP, no deassert, the unmask and the restores run */
			mb_note = 2;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_DEAD && x.nstore == 85u && f.ndbdeas == 0u && f.nunm == 1u && x.restore == 1 && has("dead link) in the d4c exit-note poll") && !has("DS handshake done") && f.nrdwin == 0u);
			mb_note = 3;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_DEAD && x.nstore == 85u && f.ndbdeas == 0u && f.nunm == 1u && x.restore == 1 && has("dead link) in the d4c exit-note poll") && f.nrdwin == 0u);
			mb_note = 1;
			/* review d4c r1 F4: the pre-answer re-read sees a trap / FWHALT (STOP) or a changed ring-2 item 0 (gap) since the baseline: not one d4c store, no doorbell */
			mb_f4 = 1;
			mb_f4_n = 0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_FATAL && mb_f4_n == 2 && x.nstore == 59u && f.ndbwr == 0u && f.ndbrd == 0u && f.nunm == 1u && x.restore == 1 && has("SWEET WLAN: D4c pre-answer read flags ") && has("TRAP/ASSERT\n") && !has("G2 store d4c"));
			mb_f4 = 2;
			mb_f4_n = 0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_FATAL && x.nstore == 59u && f.ndbwr == 0u && f.nunm == 1u && x.restore == 1 && has("SWEET WLAN: D4c pre-answer read mb data 0x10000000 FWHALT\n") && !has("G2 store d4c"));
			mb_f4 = 3;
			mb_f4_n = 0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && x.nstore == 59u && f.ndbwr == 0u && f.ndbrd == 0u && x.restore == 1 && has("WIFI GAP: d4c ring-2 item 0 changed since the baseline, not answered x1 [high]") && has("D4c not answered: ring-2 item 0 changed since the baseline, not answered (") && !has("G2 store d4c"));
			for (k = 4; k <= 5u; k++) {
				mb_f4 = (int)k;
				mb_f4_n = 0;
				mk(&x, &p, F_OK);
				rc = swg_go(&x);
				logbuf[loglen] = 0;
				CHECK(rc == 0 && x.nstore == 59u && f.ndbwr == 0u && f.ndbrd == 0u && has("WIFI GAP: d4c ring-2 item 0 changed since the baseline, not answered x1 [high]") && !has("G2 store d4c"));
			}
			mb_f4 = 0;
			mb_f4_n = 0;
			/* a clean 0x24 item 0 is not reported "not handled" by d4b, any other item keeps that gap; a bad XOR keeps it too (d4c adds its own) */
			mb_xorbad = 1;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && has("WIFI GAP: d4b ring-2 item type 0x24 not handled x1 [high]") && has("WIFI GAP: d4b epoch/xor mismatch x1 [high]"));
			mb_type = 0x25u; mb_xorbad = 0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && has("WIFI GAP: d4b ring-2 item type 0x25 not handled x1 [high]"));
			/* sweetwlan=14 with the same pre-written item: no D4c line, no D4c store, the d4b gap stays */
			want_d4b = 2;
			mb_type = 0x24u; mb_mb = 0x2u; mb_epoch = 1u; mb_dw = 1u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && x.nstore == 59u && f.ndbrd == 0u && f.ndbwr == 0u && !has("D4c") && !has("WIFI GAP: d4c") && has("WIFI GAP: d4b ring-2 item type 0x24 not handled x1 [high]"));
			/* a failing store is a STOP at its row, the unmask and the restores still run, the row's doorbell is not rung: an ack item word, the C1 slot (the ack was made), a wake item word (ack and C1 made), a deassert item word (no deassert doorbell) */
			want_d4b = 3;
			wr_fail_host_a = r0 + 48u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_WR && f.ndbrd == 0u && f.ndbwr == 0u && f.nunm == 1u && f.hb[hw0 / 4u] == 1u && f.ep == 0x00100002u && f.rc == 0x00100000u && has("G2 STOP: store") && !has("WIFI GAP: d4c stop after the DS_ACK before the wake assert"));
			wr_fail_host_a = dr0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_WR && f.ndbwr == 1u && f.ndbrd == 0u && f.ndbwake == 0u && f.nunm == 1u && f.hb[dr0 / 4u] == 0u && has("G2 STOP: store") && has("WIFI GAP: d4c stop after the DS_ACK before the wake assert"));
			wr_fail_host_a = r0 + 88u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_WR && f.ndbrd == 1u && f.ndbwake == 0u && f.hb[hw0 / 4u] == 2u && f.nunm == 1u && has("G2 STOP: store") && has("WIFI GAP: d4c stop after the DS_ACK before the wake assert"));
			wr_fail_host_a = r0 + 128u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_WR && f.ndbwake == 1u && f.ndbdeas == 0u && f.hb[hw0 / 4u] == 3u && f.nunm == 1u && has("G2 STOP: store") && !has("WIFI GAP: d4c stop after the DS_ACK before the wake assert"));
			wr_fail_host_a = 0u;
			/* after the deassert doorbell the final full read stops on FWHALT, a TRAP, or a dead link (STOP, all 99 stores made, the unmask and restores still run) */
			mb_after = 2;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_FATAL && x.nstore == 99u && has("SWEET WLAN: D4c final read mb data 0x10000000 FWHALT\n") && !has("DS handshake done") && f.nunm == 1u && x.restore == 1 && f.ep == 0x00100002u && f.rc == 0x00100000u);
			mb_after = 3;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_FATAL && x.nstore == 99u && has("SWEET WLAN: D4c final read flags ") && has("TRAP/ASSERT\n") && !has("DS handshake done") && f.nunm == 1u && x.restore == 1);
			mb_after = 5;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_DEAD && x.nstore == 99u && has("host h2d_r[0] reads all ones (dead link)") && !has("DS handshake done") && f.nunm == 1u && x.restore == 1);
			mb_after = 0;
			/* a row's old value: C1's D2H-RD slot must be 0 (the ack was made, C1's doorbell is not rung), a wake item word, a deassert item word, the L14 post's slot changed (3 instead of the 1 the ack's slot expects) */
			mb_dirty_a = dr0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbwr == 1u && f.ndbrd == 0u && f.hb[dr0 / 4u] == 5u && f.nunm == 1u && x.restore == 1);
			mb_dirty_a = r0 + 44u;     /* ring-0 item 1 (the ack) word 1 */
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbwr == 0u && f.ndbrd == 0u && f.hb[r0 / 4u + 11u] == 5u && f.hb[hw0 / 4u] == 1u && f.nunm == 1u && x.restore == 1);
			mb_dirty_a = r0 + 84u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbrd == 1u && f.ndbwake == 0u && f.hb[r0 / 4u + 21u] == 5u && f.hb[hw0 / 4u] == 2u && f.nunm == 1u && x.restore == 1);
			mb_dirty_a = r0 + 124u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbwake == 1u && f.ndbdeas == 0u && f.hb[r0 / 4u + 31u] == 5u && f.hb[hw0 / 4u] == 3u && f.nunm == 1u && x.restore == 1);
			mb_dirty_a = 0u;
			mb_tamper = 3u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbwr == 0u && f.ndbrd == 0u && f.hb[hw0 / 4u] == 3u && f.nunm == 1u && x.restore == 1 && !has("DS_ENTER_REQ answered"));
			mb_tamper = 0u;
			/* the H2D-WR slot changes under the host at a doorbell (the dongle or the host): the next slot row wants the previous index (wake: 2, deassert: 3) and the C2 slot wants the 1 C1 stored */
			mb_twk_db = 0xFF000002u; mb_twk_off = hw0; mb_twk_val = 9u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbrd == 1u && f.ndbwake == 0u && f.hb[hw0 / 4u] == 9u && f.nunm == 1u && x.restore == 1);
			mb_twk_db = 0xFF000003u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbwake == 1u && f.ndbdeas == 0u && f.hb[hw0 / 4u] == 9u && f.nunm == 1u && x.restore == 1);
			mb_twk_db = 0xFF000004u; mb_twk_off = dr0; mb_twk_val = 7u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_OLD && f.ndbdeas == 1u && f.ndbrd2 == 0u && f.hb[dr0 / 4u] == 7u && f.nunm == 1u && x.restore == 1);
			mb_twk_db = 0u;
			/* the D2H-WR slot moves to 2 during the L14 poll (item 1 appears after the baseline): the answer re-reads it and refuses, the baseline value 1 would not */
			mb_late_dw = 2u;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == 0 && x.nstore == 59u && f.ndbrd == 0u && f.ndbwr == 0u && has("WIFI GAP: d4c d2h ring 2 holds items 1..1, not consumed, no answer x1 [high]") && !has("DS_ENTER_REQ answered"));
			mb_late_dw = 0u;
			/* review d4c r1 F5: the pre-answer re-read of D2H-WR reads all ones (dead link): STOP before any d4c store, no doorbell, undo made */
			mb_dead_d2hw = 1;
			mb_d2hw_last = 0;
			mk(&x, &p, F_OK);
			rc = swg_go(&x);
			logbuf[loglen] = 0;
			CHECK(rc == -SWG_E_DEAD && mb_d2hw_last == 2 && x.nstore == 59u && f.ndbrd == 0u && f.ndbwr == 0u && f.hb[dr0 / 4u] == 0u && x.restore == 1 && !has("G2 store d4c") && !has("DS_ENTER_REQ answered") && has("d2h_w[0] reads all ones"));
			mb_dead_d2hw = 0;
			d4kind = D_OK;
			mb_type = mb_mb = mb_epoch = mb_dw = 0u;
			mb_note = 0;
		}
		want_d4b = 0;
		d4kind = D_OK;
	}
	d4d_tests(&p);
	w15_tests();
	if (fails) {
		printf("RESULT: FAIL wlan_g2_host_test (%u failed checks)\n", fails);
		return 1;
	}
	printf("RESULT: PASS wlan_g2_host_test\n");
	return 0;
}
