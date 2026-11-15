#pragma once
#include <stdint.h>

/*
 * touchprobe.h - Pixel 6a touchscreen SPI11 / USI11 bring-up experiment WITHOUT booting iOS
 * (build.sh --touch-probe[=run]).
 * --touch-probe=railsr / rails add the ACPM client of acpm.c (read / set the touch rails LDO25M, LDO26M). A register-level port of the XNU driver
 * xnu/patches/0088-sweet-gs101-touch.patch (+0089 CMU_PERIC1 clock chain, +0090 variants A-E, +0092 CMU_TOP gate)
 * as it runs with boot-args `sweettouchclk=3 sweettouchvar=1`.
 *
 * touch_probe_dump(): phase 1, READ-ONLY (CMU_PERIC1 + CMU_TOP, USI11 mode, pad banks, USI/SPI registers).
 * touch_probe_run(opts): phase 2, the writes of that boot-arg set plus the docs/research/21 experiments chosen by TP_O_*;
 *                     only called by --touch-probe=run[,...].
 * Every constant comes from the generated TOUCH_* board macros (tools/boards.py <- soc.json / board.json, the same
 * keys tools/mkadt.py puts into the /sweet-touch node). Output lines are the XNU driver's "SWEET TOUCH: ..." lines
 * (label "SWEET", text "TOUCH: ..."); long XNU lines are split at spaces to fit the 96-byte RAM-log line.
 *
 * Pure decoders live here so shim/touch_host_test.c can test them.
 */

/* SPI engine layout, spi-s3c64xx.c (0088 patch :235-:257) */
#define TP_SPI_CH_CFG		0x00u
#define TP_SPI_CLK_CFG		0x04u
#define TP_SPI_MODE_CFG		0x08u
#define TP_SPI_CS_REG		0x0cu
#define TP_SPI_INT_EN		0x10u
#define TP_SPI_STATUS		0x14u
#define TP_SPI_TX_DATA		0x18u
#define TP_SPI_RX_DATA		0x1cu
#define TP_SPI_PACKET_CNT	0x20u
#define TP_SPI_PENDING_CLR	0x24u
#define TP_SPI_SWAP_CFG		0x28u
#define TP_SPI_FB_CLK		0x2cu
#define TP_CH_SW_RST		(1u << 5)
#define TP_CH_RXCH_ON		(1u << 1)
#define TP_CH_TXCH_ON		(1u << 0)
#define TP_CH_HS_EN		(1u << 6)
#define TP_PKT_CNT_EN		(1u << 16)
#define TP_MODE_TRAILCNT	(0x3ffu << 19)
#define TP_PND_ERR_CLR		0x1eu
#define TP_CLK_ENCLK		(1u << 8)	/* 0090 variant A */
#define TP_CLK_PSR_MASK		0xffu
#define TP_CS_AUTO		(1u << 1)	/* 0090 variant E */
#define TP_CS_NSC_CNT_2		(2u << 4)
#define TP_USI_CON		0x04u
#define TP_USI_CON_RESET	(1u << 0)
#define TP_USI_OPTION		0x08u
#define TP_USI_OPT_CLKREQ	(1u << 1)
#define TP_USI_OPT_CLKSTOP	(1u << 2)
#define TP_SPI_FIFO_BYTES	64u
#define TP_NVARIANTS		5u		/* A..E, tried in this order */
#define TP_BURST_TIMEOUT_US	20000u		/* 0088 tg_spi_burst */
#define TP_MUX_BUSY_TIMEOUT_US	1000u		/* 0089 tg_cmu_enable */
#define TP_MODE_LOOPBACK	(1u << 3)	/* S3C64XX_SPI_MODE_SELF_LOOPBACK, docs/research/21 X3 */
#define TP_FTM5_CMD_FIFO_READ	0x87u		/* 0088 sweet_ftm5.h */
#define TP_FTM5_EVENT_SIZE	8u
#define TP_FTM5_EVT_CONTROLLER_READY 0x03u

/* Build options (build.sh --touch-probe=run,mux,cs,osc|top,loop,rst,misopu,railsr|rails), docs/research/21 section 3 */
#define TP_O_MUX	1u	/* drive the TS_SPI_SEL pad low if the AP does not (XNU sweettouchmux=1) */
#define TP_O_CS		2u	/* X1: ABL-exact manual chip select, variant F */
#define TP_O_OSC	4u	/* X2: USI11 clock from oscclk, divider 5 */
#define TP_O_TOP	8u	/* X6: USI11 clock from the TOP source, divider 9 */
#define TP_O_LOOP	16u	/* X3: self-loopback */
#define TP_O_RST	32u	/* X4: pull-ups off, reset pulse, controller-ready poll */
#define TP_O_MISOPU	64u	/* X5: MISO pull-up during the identify */
#define TP_O_RAILSR	128u	/* ACPM: READ the two touch rail enable registers (LDO25M/LDO26M, docs/research/23); no SPI writes */
#define TP_O_RAILS	256u	/* ACPM: UPDATE bit 7 of both (set, never clear), read back; implies railsr */
#define TP_O_EXP	(TP_O_CS | TP_O_OSC | TP_O_TOP | TP_O_LOOP | TP_O_RST | TP_O_MISOPU)

#define TP_MAX_POLLS		100000u		/* belt and braces: a poll loop ends even if the timer is dead */

#define TP_ST_RX_LVL(v)		(((v) >> 15) & 0x1ffu)
#define TP_ST_TX_LVL(v)		(((v) >> 6) & 0x1ffu)

/* FTM5 chip-id read, sweet_ftm5.h (0088 :32-:43) */
#define TP_FTM5_CMD_HW_REG_R	0xFBu
#define TP_FTM5_ADDR_DCHIP_ID	0x20000000u
#define TP_FTM5_DUMMY_BYTES	1u
#define TP_FTM5_SPI_DELAY_CS_US	10u

#ifdef TOUCH_CMU_BIT_GATE_MANUAL
/* 0089 tg_cmu_gate: manual gate open = MANUAL 1, CG_VAL 1, automatic gating 0. */
static inline uint32_t tp_gate_open(uint32_t v)
{
	return (v | (1u << TOUCH_CMU_BIT_GATE_MANUAL) | (1u << TOUCH_CMU_BIT_GATE_VAL)) &
	       ~(1u << TOUCH_CMU_BIT_GATE_AUTO);
}
#define TP_GATE_CHECK_MASK ((1u << TOUCH_CMU_BIT_GATE_MANUAL) | (1u << TOUCH_CMU_BIT_GATE_VAL) | \
			    (1u << TOUCH_CMU_BIT_GATE_AUTO))
#endif

/* ftm5_chip_id_match: 1 = same order, 2 = swapped, 0 = no match */
static inline int tp_chip_id_match(const uint8_t *got, const uint8_t *want)
{
	if (got[0] == want[0] && got[1] == want[1])
		return 1;
	if (got[0] == want[1] && got[1] == want[0])
		return 2;
	return 0;
}

/* Fault-guarded MMIO (shim.c; host tests substitute a fake window). */
int try_read32(uintptr_t address, uint32_t *value);
int try_write32(uintptr_t address, uint32_t value);
uint64_t touch_ticks(void);	/* CNTPCT (shim.c) */

/* L-28 / xnu patch 0367: the CPU DVFS result as XNU boot-arg tokens. TOUCH_DVFS_WRITE images only; the loader appends the string after the other shim tokens.
 * " sweetdvfsk0..2=" asked kHz, " sweetdvfsb0..2=" read before, " sweetdvfsa0..2=" read back after, " sweetdvfsrc=" 0 ok, 1 refused/failed, 2 differs, 3 unread, 4 step not reached.
 * At most 221 characters; TOUCH_DVFS_TOK_MAX is the room the host guards reserve. NULL = this build cannot produce them (not gs101). */
#define TOUCH_DVFS_TOK_MAX 224
struct touch_dvfs_res {	/* caller-owned (touchprobe.o carries no writable static state): shim.c defines the one, loader.c reads it */
	uint32_t on;		/* 1 once touch_probe_dvfs ran on a build that can produce the result (0 = tokens NULL, a GAP in loader.c) */
	uint32_t asked[3];	/* DVFS_KHZ0..2 */
	uint32_t before[3];	/* step 1 FREQ_GET per cluster */
	uint32_t after[3];	/* step 2 read-back (0 = not read) */
	uint32_t rc;		/* 0 every asked cluster read back at the asked rate, 1 request refused or failed, 2 a read-back differs, 3 a read-back failed, 4 step 2 not reached */
};
static inline void touch_dvfs_res_init(struct touch_dvfs_res *r)
{
	uint32_t i;

	r->on = 0;
	for (i = 0; i < 3; i++)
		r->asked[i] = r->before[i] = r->after[i] = 0;
	r->rc = 4;
}
/* One decimal number or literal into buf (cap), position *p; a header function, so the formatting is NOT in the audited touchprobe.o (no register-offset store there). */
static inline void touch_dvfs_put(char *buf, unsigned cap, unsigned *p, const char *lit, uint32_t v, int num)
{
	char d[10];
	unsigned n = 0;

	while (*lit && *p + 1u < cap)
		buf[(*p)++] = *lit++;
	if (!num)
		return;
	do {
		d[n++] = (char)('0' + v % 10u);
		v /= 10u;
	} while (v && n < sizeof(d));
	while (n && *p + 1u < cap)
		buf[(*p)++] = d[--n];
}
extern struct touch_dvfs_res touch_dvfs_res_g;	/* defined in shim.c (TOUCH_DVFS_WRITE builds), read by loader.c */
/* Format the tokens of *res into buf (capacity cap >= TOUCH_DVFS_TOK_MAX; a short cap truncates, the loader never appends a truncated line: it needs the room first). Returns buf, or NULL if this build cannot produce them (probe never ran). */
static inline const char *touch_dvfs_tokens(const struct touch_dvfs_res *res, char *buf, unsigned cap)
{
	static const char kk[3][16] = { " sweetdvfsk0=", " sweetdvfsk1=", " sweetdvfsk2=" };
	static const char kb[3][16] = { " sweetdvfsb0=", " sweetdvfsb1=", " sweetdvfsb2=" };
	static const char ka[3][16] = { " sweetdvfsa0=", " sweetdvfsa1=", " sweetdvfsa2=" };
	unsigned p = 0, i;

	if (!res->on || cap == 0u)
		return 0;
	for (i = 0; i < 3; i++)
		touch_dvfs_put(buf, cap, &p, kk[i], res->asked[i], 1);
	for (i = 0; i < 3; i++)
		touch_dvfs_put(buf, cap, &p, kb[i], res->before[i], 1);
	for (i = 0; i < 3; i++)
		touch_dvfs_put(buf, cap, &p, ka[i], res->after[i], 1);
	touch_dvfs_put(buf, cap, &p, " sweetdvfsrc=", res->rc, 1);
	buf[p] = 0;
	return buf;
}
/* Append tok to the NUL-terminated command line cmd (capacity cap) after its current text. Never truncates. 0 appended, 1 no room (nothing appended), 2 no tokens.
 * A header function so the host test (dvfs_probe_host_test.c) runs the loader's own code. */
static inline int touch_dvfs_append(volatile char *cmd, uint32_t cap, const char *tok)
{
	uint32_t n = 0, len = 0, i;

	if (!tok)
		return 2;
	while (len < TOUCH_DVFS_TOK_MAX && tok[len])
		len++;
	while (n + 1 < cap && cmd[n])
		n++;
	if (n + len >= cap)
		return 1;
	for (i = 0; i < len; i++)
		cmd[n++] = tok[i];
	cmd[n] = 0;
	return 0;
}
void touch_probe_dump(void);	/* phase 1, read-only */
int touch_probe_dvfs(unsigned step, struct touch_dvfs_res *res);	/* ACPM DVFS (TOUCH_DVFS): 0 describe channel 1 (reads), 1 FREQ_GET all domains, 2 FREQ_REQ DVFS_KHZ0..2 + read back; 0 = ok */
int touch_probe_rails(unsigned step);	/* ACPM rails (TOUCH_RAILS): 0 describe (reads), 1 read both regs, 2 update + read back; 0 = ok */
void touch_probe_run(unsigned opts);	/* phase 2: the sweettouchclk=3 sweettouchvar=1 writes + the TP_O_* experiments */
