#pragma once
#include <stdint.h>

/*
 * acpm.h - minimal ACPM (APM firmware) client: read / update one S2MPG10 register through the shared-memory IPC channel.
 * Built only from docs/research/23-acpm-regulator-client.md (every constant is cited in acpm.c). Used by the shim touch probe
 * (--touch-probe=railsr / rails) to ask the APM to read or set the enable bit (bit 7) of LDO25M (0x5C) / LDO26M (0x5D).
 *
 * The APM firmware owns the SRAM, so the code is defensive: it reads and validates the channel descriptor first, refuses to
 * write unless the queues are empty and every assumption that doc 23 tags [guess] has been marked verified in soc.json
 * ("acpm": layout_verified, elem_unit_verified, per-rail vsel range), writes only the one request slot it uses, the two queue
 * pointers and the two mailbox registers (all through the allowlisted w32 in acpm.c), and bounds the reply wait. Once the request
 * queue's front pointer is written the request is PUBLISHED: the APM may already be executing it, so nothing is ever rolled back
 * (Linux never does: acpm_ipc.c:762-772). A failure after that point reports the queue state and leaves the channel as it is.
 * No retries, no static state.
 */

/* why a describe / operation refused (acpm_why() gives the text; the "unsure" ones print "ACPM unsure: ...") */
enum {
	ACPM_W_NONE = 0,
	ACPM_W_INITN,		/* ipc_ap_max not in 1..32 */
	ACPM_W_TAB,		/* channel table outside SRAM */
	ACPM_W_NOCH,		/* no descriptor with the PMIC channel id */
	ACPM_W_PTR,		/* a descriptor pointer is unaligned or outside SRAM */
	ACPM_W_QLEN,		/* q_len not in 3..256 */
	ACPM_W_ESZ,		/* q_elem_size * elem_unit is not 16 bytes (the 4-word MFD command) */
	ACPM_W_QBUF,		/* queue buffer outside SRAM */
	ACPM_W_IDX,		/* a front/rear index is >= q_len */
	ACPM_W_OVERLAP,		/* queue buffers / pointer words overlap */
	ACPM_W_BUSY,		/* a queue is not empty */
	ACPM_W_FULL,		/* acpm_ipc.c:712 / :723 queue-full condition */
	ACPM_W_LAYOUT,		/* unsure: layout not verified */
	ACPM_W_UNIT,		/* unsure: element unit not verified */
	ACPM_W_VSEL,		/* unsure: voltage code range unknown */
	ACPM_W_VSELOUT,		/* voltage code outside the configured range */
	ACPM_W_ON,		/* the rail is already enabled: nothing to do */
	ACPM_W_ARG,		/* not one of the two allowed (reg, val, mask) */
	ACPM_W_SEQ,		/* reply with another sequence number / entry count */
	ACPM_W_CHIDX,		/* the descriptor's table index is not the channel id (Linux indexes channel[id]) */
	ACPM_W_TYPE,		/* the descriptor is not a TYPE_QUEUE channel (fw_header/framework.h:247) */
	ACPM_W_TIMER,		/* counter frequency 0: no bounded wait possible */
	ACPM_W_DOM,		/* DVFS: domain unknown, or rate not in the firmware table / above the ceiling */
	ACPM_W_DREPLY,		/* DVFS: reply word 3 (error code) is not 0 */
	ACPM_W_MAX
};

/* return codes */
enum {
	ACPM_OK = 0,
	ACPM_E_UNSURE = 1,	/* a [guess] is not verified: nothing written */
	ACPM_E_ARG = 2,
	ACPM_E_FAULT = 3,	/* an MMIO/SRAM access faulted */
	ACPM_E_DESC = 4,	/* descriptor invalid: nothing written */
	ACPM_E_BUSY = 5,	/* queue not empty / full: nothing written */
	ACPM_E_TIMEOUT = 6,	/* no reply within the bound */
	ACPM_E_REPLY = 7,	/* reply not ours / malformed (the channel is drained) */
	ACPM_E_RET = 8,		/* the APM returned a nonzero RETURN field */
	ACPM_E_RAIL = 9,	/* rail gate refused (vsel range) */
	ACPM_E_ON = 10		/* the rail is already on: nothing written */
};

#define ACPM_TIMEOUT_US		50000u		/* reply bound: 50 ms */
#define ACPM_MAX_POLLS		1000000u	/* belt and braces: the poll ends even if the timer is dead */
#define ACPM_ELEM_BYTES		16u		/* the one element size we speak: 4 words (acpm_mfd.c: unsigned int command[4]) */
#define ACPM_ELEM_WORDS		(ACPM_ELEM_BYTES / 4u)
#define ACPM_MAX_AP_CH		32u
#define ACPM_DESC_WORDS		18u		/* one descriptor = 0x48 bytes */

struct acpm_rail {
	uint32_t reg;		/* S2MPG10 PMIC register, enable = bit 7 */
	uint32_t vsel_known;	/* 0: the accepted voltage code range is unknown, the update refuses */
	uint32_t vsel_min, vsel_max;	/* accepted reg[5:0] before the enable */
};

struct acpm_cfg {
	uintptr_t mbox_base, sram_base;
	uint32_t sram_size, initdata_off, ch_id, speedy, pmic_addr;
	uint32_t intgr1, intcr0, intsr0, desc_stride;
	uint32_t layout_verified, elem_unit, elem_unit_verified;
	uint32_t timeout_us;
	struct acpm_rail rail[2];
	uint32_t dvfs_ch;		/* ACPM DVFS channel id (docs/research/25); 0 = no DVFS on this SoC */
};

/* one validated channel (read only; filled by acpm_describe) */
struct acpm_chan {
	uint32_t init[8];		/* INIT + 0x00..0x1c raw */
	uint32_t n, tab, idx;		/* ipc_ap_max, channel table SRAM offset, index of the PMIC descriptor */
	uint32_t have_desc;		/* 1 once d[] holds the PMIC channel's descriptor (even if it then failed validation) */
	uint32_t d[ACPM_DESC_WORDS];	/* the descriptor, raw */
	uint32_t qlen, esz, esz_w, esz_b;	/* q_len, q_elem_size raw, words per element, bytes per element (esz * elem_unit) */
	uint32_t ap_poll;
	uintptr_t req_rear_pa, req_front_pa, req_base_pa;	/* AP -> APM queue (descriptor "rx_*") */
	uintptr_t rsp_rear_pa, rsp_front_pa, rsp_base_pa;	/* APM -> AP queue (descriptor "tx_*") */
	uint32_t rr, rf, sr, sf;	/* request rear/front, reply rear/front */
	uint32_t intsr0;
	uint32_t why;			/* ACPM_W_* of the first failed check, ACPM_W_NONE if it validated */
};

/* one transaction (trace for the screen / USB log) */
struct acpm_xfer {
	uint32_t seq, req[4], rep[4];
	int err;			/* ACPM_* */
	uint32_t why;			/* ACPM_W_* */
	uint32_t ret, value;		/* reply RETURN [31:24], DEST [15:8] */
	uint32_t n;			/* writes completed */
	uint32_t published;		/* 0 the APM cannot see a request, 1 front write attempted (APM may run it), 2 doorbell rung */
	uint32_t had_reply;		/* DVFS request only: 1 a reply was read and drained, 0 none arrived (the request was consumed) */
	uint32_t state[4];		/* after a failure with published != 0: request rear/front, reply rear/front (~0 = unreadable) */
	uint64_t t_bell, t_end;		/* CNTPCT at the doorbell / at the reply or the give-up */
};

struct acpm_trace {
	uint32_t seq;			/* next sequence number 1..63 (caller initialises with 1) */
	uint32_t n;			/* transactions done: a, then b */
	struct acpm_xfer a, b;		/* read = a; update = a (the pre-read), then b (the update) */
};

/* Zero every field by hand (freestanding: no memset, and the compiler must not turn a loop into one). */
static inline void acpm_xfer_zero(struct acpm_xfer *x)
{
	unsigned i;

	x->seq = 0;
	x->err = 0;
	x->why = 0;
	x->ret = x->value = x->n = x->published = x->had_reply = 0;
	x->t_bell = x->t_end = 0;
	for (i = 0; i < 4; i++)
		x->req[i] = x->rep[i] = x->state[i] = 0;
}

/* Fault-guarded MMIO (shim.c; host tests substitute a fake window) and the counter (shim.c, CNTPCT; console.c, Hz). */
int try_read32(uintptr_t address, uint32_t *value);
int try_write32(uintptr_t address, uint32_t value);
uint64_t touch_ticks(void);
uint64_t counter_freq(void);

void acpm_cfg_init(struct acpm_cfg *c);		/* from the generated ACPM_* macros (zeroed on a SoC without ACPM) */
const char *acpm_why(uint32_t why);
int acpm_unsure(uint32_t why);			/* 1 if why is one of the "ACPM unsure" reasons */
int acpm_describe(const struct acpm_cfg *c, struct acpm_chan *h);	/* reads only */
/* Reads only: word i of the request (rsp = 0) or reply (rsp = 1) queue buffer, from the descriptor's raw base, bounded to the SRAM
 * window, whatever the validation result (so a refused describe still shows what is in the buffers). 0 if unavailable / faulted. */
int acpm_chan_id(const struct acpm_cfg *c, const struct acpm_chan *h, uint32_t idx, uint32_t *id);	/* after why == ACPM_W_NOCH: table[idx].id */
int acpm_qword(const struct acpm_cfg *c, const struct acpm_chan *h, int rsp, uint32_t i, uint32_t *v);
int acpm_pmic_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t);
/* S2MPG11 register read (soc.json acpm.s2mpg11 only; refuses a register not listed there or an unknown config), for --gpu-rails */
int acpm_s11_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t);
/* S2MPG10 register read of the four PCIe-PHY LDO control registers of soc.json wlan (s10_l16m..l19m_ctrl only), for --wlan-rails (docs/research/29); no PMIC write is expressible */
int acpm_s10_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t);
/* Reset-source latches for --pmic-src (docs/research/139): exactly S2MPG10 STATUS1/STATUS2/PWRONSRC/OFFSRC (0xC..0xF) and S2MPG11 STATUS1/OFFSRC (0xC, 0xD); any other pair (INT1..INT6 included) is ACPM_E_ARG. FUNC_READ only. */
enum { ACPM_SRC_S10 = 0, ACPM_SRC_S11 = 1 };
int32_t acpm_pmic_src_read(const struct acpm_cfg *c, uint32_t chip, uint32_t reg, uint32_t *val, struct acpm_trace *t);
#ifdef PMIC_SRC_CLEAR
/* --pmic-src-clear images ONLY (docs/research/142, ALL-ON T-02/T-24): the one PMIC write of that flag, a FUNC_WRITE of the VALUE 0 to exactly S2MPG10 OFFSRC 0xF, S2MPG10 PWRONSRC 0xE or
 * S2MPG11 OFFSRC 0xD (what Google's BCL driver does after reading them). Any other (chip, register) or any value != 0 is ACPM_E_ARG before any IPC. One request, no retry. */
int32_t acpm_pmic_src_clear(const struct acpm_cfg *c, uint32_t chip, uint32_t reg, uint32_t val, struct acpm_trace *t);
#endif
/* GPU_RAIL_ON images only (--gpu-rail-on): switch the mode field of S2MPG11 BUCK2S_CTRL (soc.json acpm.s2mpg11.rail_on): on = off -> on-normal (only from exactly 'off' and
 * with OUT1 inside the window, *out1 = OUT1), off = restore to off (nothing to do if already off: ACPM_E_ON). t->a = pre-read, t->b = the update (or the refusal). The only S2MPG11 write. */
int acpm_s11_rail_set(const struct acpm_cfg *c, int on, struct acpm_trace *t, uint32_t *out1);
/* reg must be a configured rail register and val == mask == 0x80 (set bit 7, never clear): no generic poke */
int acpm_pmic_update(const struct acpm_cfg *c, uint32_t reg, uint32_t val, uint32_t mask, struct acpm_trace *t);

/*
 * CPU cluster frequency through the ACPM DVFS channel (docs/research/25; Linux exynos_acpm_get_rate / exynos_acpm_set_rate,
 * soc/gs cal-if/acpm_dvfs.c). dom is the ACPM_DVFS_* index (CPUCL0/1/2 = 2/3/4) and must be one of the configured domains.
 * acpm_dvfs_get: FREQ_GET (a reply is expected), *khz = reply word 1. Reads and one request-queue entry; changes no clock.
 * acpm_dvfs_set (only in images built with TOUCH_DVFS_WRITE): khz must be a row of the firmware's own table for that domain AND
 * not above the configured ceiling, else it refuses before any write. It reads the current rate first (t->a), does nothing if
 * it already equals khz, else sends FREQ_REQ (t->b) and waits for its reply: the firmware replies to every request (Linux relies on it,
 * acpm_ipc.c:446-448, :747-756 [src]); no reply within the bound is ACPM_E_TIMEOUT with the request published. The reply says nothing about
 * the rate: the caller MUST read it back with acpm_dvfs_get and compare (touchprobe.c does).
 */
int acpm_dvfs_ndom(void);			/* number of configured domains (0 if none) */
int acpm_dvfs_dom(int i, uint32_t *idx);	/* the i-th configured domain's index */
int acpm_dvfs_get(const struct acpm_cfg *c, uint32_t dom, uint32_t *khz, struct acpm_trace *t);
int acpm_dvfs_set(const struct acpm_cfg *c, uint32_t dom, uint32_t khz, struct acpm_trace *t);
/* GPU_CLK images only: like acpm_dvfs_set for G3D (5) / G3DL2 (6) ONLY, but the request is also sent when the pre-read equals khz. */
int acpm_dvfs_reassert(const struct acpm_cfg *c, uint32_t dom, uint32_t khz, struct acpm_trace *t);
/* MIF_CLK images only (T-5030): FREQ_REQ for MIF (dom 0) to the table top row (soc.json acpm.dvfs MIF ceiling) and nothing else; pre-read in t->a, request in t->b. */
int acpm_dvfs_mif_top(const struct acpm_cfg *c, struct acpm_trace *t);
#if defined(MIF_CLK_LEVEL_KHZ) && !defined(MIF_CLK)
#error "MIF_CLK_LEVEL_KHZ needs MIF_CLK"
#endif
#ifdef SWEET_PROBE
/* --sweet-probe images ONLY (xnu patch 0363, docs/research/161 s.1): the SLC service on ACPM channel soc.json acpm.slc.ch_id (10) / async_ch_id (11).
 * acpm_slc_describe: reads only (channel 10 or 11, element 16 or 32 B accepted; another channel is ACPM_E_ARG).
 * acpm_slc_version: ONE fixed PT_VERSION (0xd005) request on channel 10, *ver = reply word 1 = (major << 16) | minor; t->a = the transaction. No argument parameter: PT_CHECK / PT_ENABLE / PT_MUTATE / PT_DISABLE and the PMON opcodes are not expressible. */
int acpm_slc_describe(const struct acpm_cfg *c, uint32_t ch, struct acpm_chan *h);
int acpm_slc_version(const struct acpm_cfg *c, uint32_t *ver, struct acpm_trace *t);
#endif
/* Images that contain code able to build a FREQ_REQ (acpm.c dvfs_set): the CPU, GPU or MIF client. */
#if defined(TOUCH_DVFS_WRITE) || defined(GPU_CLK) || defined(MIF_CLK)
#define ACPM_DVFS_WRITER 1
#endif
