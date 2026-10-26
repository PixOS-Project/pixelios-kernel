/*
 * gpujob.c - G2 stage 2: address space 0 + one WRITE_VALUE job (see gpujob.h). Freestanding, no static state, counter-bounded waits.
 * Every register, field and value with its cite: docs/research/28-gpu-stage2-job.md. Kbase = Google android-gs-bluejay-6.1-android16 mali_kbase, local copies in
 * ~/pixelios-artifacts/refs/gpu-stage2/ (file:line below refer to them); Mesa = commit recorded in refs/gpu-stage2/mesa_commit.txt, src/panfrost/genxml/v9.xml.
 *
 * Memory (5 pages of the shim image's own RAM, gpu_job_scratch()): +0x0000 L0 table (TRANSTAB points here), +0x1000 L1, +0x2000 L2, +0x3000 L3, +0x4000 the
 * data page. GPU VA 0x1000 (L3 index 1, L0..L2 index 0) -> the data page, attribute index 5 = non-cacheable, RW. In the data page: the job descriptor at +0x000
 * (GPU VA 0x1000) and the target word at +0x100 (GPU VA 0x1100).
 *
 * Writes: only through w32() -> w32_allowed() (exact register/value pairs below + the scratch window), never retried. No GPU_COMMAND, no soft reset (kbase resets
 * only on a stuck AS / failed power transition, mmu_hw_direct.c:168-186, which a probe must not do), no IRQ mask, no S2MPU write.
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "gpuprobe.h"
#include "gpupower.h"
#include "gpus2mpu.h"
#include "gpujob.h"

#if SOC_GS101 && defined(GPU_MALI) && defined(GPU_JOB_PTE_LEAF) && defined(GPU_SHADER_PRESENT)

#define PG		0x1000u
#define NPAGES		5u
#define J_POLLS		200000u		/* reads per wait; bounded, the watchdogs are off */

/* register addresses (enums.h / regmap_jm_enums.h offsets from GPU_MALI) */
#define A_JOB_RAW	(GPU_MALI + 0x1000u)	/* JOB_IRQ_RAWSTAT */
#define A_JOB_CLEAR	(GPU_MALI + 0x1004u)
#define A_JS_STATUS	(GPU_MALI + 0x1824u)	/* JS0_STATUS */
#define A_JS_CMD	(GPU_MALI + 0x1820u)	/* JS0_COMMAND */
#define A_JS_HEAD_LO	(GPU_MALI + 0x1800u)
#define A_JS_HEAD_NEXT_LO (GPU_MALI + 0x1840u)
#define A_JS_HEAD_NEXT_HI (GPU_MALI + 0x1844u)
#define A_JS_AFF_NEXT_LO (GPU_MALI + 0x1850u)
#define A_JS_AFF_NEXT_HI (GPU_MALI + 0x1854u)
#define A_JS_CFG_NEXT	(GPU_MALI + 0x1858u)
#define A_JS_CMD_NEXT	(GPU_MALI + 0x1860u)
#define A_MMU_RAW	(GPU_MALI + 0x2000u)	/* MMU_IRQ_RAWSTAT */
#define A_MMU_CLEAR	(GPU_MALI + 0x2004u)
#define A_AS_TRANSTAB_LO (GPU_MALI + 0x2400u)	/* AS0 */
#define A_AS_TRANSTAB_HI (GPU_MALI + 0x2404u)
#define A_AS_MEMATTR_LO	(GPU_MALI + 0x2408u)
#define A_AS_MEMATTR_HI	(GPU_MALI + 0x240cu)
#define A_AS_COMMAND	(GPU_MALI + 0x2418u)
#define A_AS_FAULTSTATUS (GPU_MALI + 0x241cu)
#define A_AS_FAULTADDR_LO (GPU_MALI + 0x2420u)
#define A_AS_STATUS	(GPU_MALI + 0x2428u)
#define A_AS_TRANSCFG_LO (GPU_MALI + 0x2430u)
#define A_AS_TRANSCFG_HI (GPU_MALI + 0x2434u)
#define A_GPU_FAULT	(GPU_MALI + 0x003cu)
#define A_JS_PRESENT	(GPU_MALI + 0x001cu)	/* GPU_CONTROL JS_PRESENT, regmap_jm_enums.h:41 */
#define A_JS0_FEAT	(GPU_MALI + 0x00c0u)	/* JS0_FEATURES .. JS2_FEATURES, regmap_jm_enums.h:70 ff. */
#define A_JS1_FEAT	(GPU_MALI + 0x00c4u)
#define A_JS2_FEAT	(GPU_MALI + 0x00c8u)
#define A_SHADER_READY	(GPU_MALI + 0x0140u)

#define AS_CMD_UPDATE	1u		/* AS_COMMAND_COMMAND_UPDATE (hw_access_regmap.h:174) */
#define JS_CMD_START	1u		/* JS_COMMAND_START (regmap_jm_macros.h:149) */
#define JS_CMD_HARDSTOP_1 7u		/* JS_COMMAND_HARD_STOP_1: kbase uses it when the atom carries JOB_CHAIN_FLAG (jm_hw.c:532-537), as ours does */
#define JS_FEATURE_SET_VALUE_JOB (1u << 2)	/* regmap_jm_macros.h:230: the slot can run WRITE_VALUE ("set value") jobs */
#define JS_STATUS_ACTIVE 0x08u	/* BASE_JD_EVENT_ACTIVE, common/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:729 */
#define IRQ_SLOT0_BITS	0x00010001u	/* JOB_IRQ: done bit 0 | failed bit 16 of slot 0 (jm_hw.c:300-303); MMU_IRQ: PAGE_FAULT(0) | BUS_ERROR(0) (hw_access_regmap.h:63-69) */

struct jctx {
	uintptr_t base;		/* scratch, 4 KiB aligned */
	uint32_t l0_lo, l0_hi;	/* TRANSTAB value = PA of the L0 table */
};

/* The allowlist. RAM: any aligned word inside the five scratch pages. Registers: exactly these (address, value) pairs. */
static int w32_allowed(uintptr_t pa, uint32_t v, const struct jctx *j)
{
	if (pa >= j->base && pa < j->base + NPAGES * PG)
		return !(pa & 3u);
	if (pa == A_AS_TRANSCFG_LO)
		return v == GPU_JOB_TRANSCFG_ON || v == GPU_JOB_TRANSCFG_OFF;
	if (pa == A_AS_TRANSCFG_HI)
		return v == 0;
	if (pa == A_AS_MEMATTR_LO)
		return v == GPU_JOB_MEMATTR_LO;
	if (pa == A_AS_MEMATTR_HI)
		return v == GPU_JOB_MEMATTR_HI;
	if (pa == A_AS_TRANSTAB_LO)
		return v == j->l0_lo || v == 0;
	if (pa == A_AS_TRANSTAB_HI)
		return v == j->l0_hi || v == 0;
	if (pa == A_AS_COMMAND)
		return v == AS_CMD_UPDATE;
	if (pa == A_JS_HEAD_NEXT_LO)
		return v == GPU_JOB_JOB_VA;
	if (pa == A_JS_HEAD_NEXT_HI || pa == A_JS_AFF_NEXT_HI)
		return v == 0;
	if (pa == A_JS_AFF_NEXT_LO)
		return v == GPU_SHADER_PRESENT;
	if (pa == A_JS_CFG_NEXT)
		return v == GPU_JOB_JS_CONFIG;
	if (pa == A_JS_CMD_NEXT)
		return v == JS_CMD_START;
	if (pa == A_JS_CMD)
		return v == JS_CMD_HARDSTOP_1;
	if (pa == A_JOB_CLEAR || pa == A_MMU_CLEAR)
		return v == IRQ_SLOT0_BITS;
	return 0;
}

/* noinline keeps the one try_write32 call site inside this symbol (the object audit checks it). */
__attribute__((noinline)) static int w32(uintptr_t pa, uint32_t v, const struct jctx *j)
{
	if (!w32_allowed(pa, v, j))
		return -2;
	return try_write32(pa, v) ? 0 : -1;
}

static int r32(uintptr_t pa, uint32_t *v)
{
	return try_read32(pa, v);
}

__attribute__((noipa)) static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++) {
		unsigned d = (unsigned)((v >> (4 * (digits - 1 - i))) & 0xf);

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("GPU", buf);
}

static void say(const char *a, const char *b)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "JOB ");

	p = put_txt(line, sizeof(line), p, a);
	p = put_txt(line, sizeof(line), p, b);
	emit(line, p);
}

/* "JOB <name> pa=XXXXXXXXX v=XXXXXXXX" (or unreadable); the value is returned in *v when read */
static int rd(const char *name, uintptr_t pa, uint32_t *v)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "JOB ");
	int ok = r32(pa, v);

	p = put_txt(line, sizeof(line), p, name);
	p = put_txt(line, sizeof(line), p, " pa=");
	p = put_hex(line, sizeof(line), p, pa, 9);
	if (ok) {
		p = put_txt(line, sizeof(line), p, " v=");
		p = put_hex(line, sizeof(line), p, *v, 8);
	} else {
		p = put_txt(line, sizeof(line), p, " unreadable");
		*v = 0xdeadbeefu;
	}
	emit(line, p);
	return ok;
}

static uint64_t limit_100ms(void)
{
	return counter_freq() / 10u;
}

/* 0 = ok; the first failing write is returned, later ones are not attempted by the callers */
static int put64(const struct jctx *j, uintptr_t off, uint32_t lo, uint32_t hi)
{
	int e = w32(j->base + off, lo, j);

	return e ? e : w32(j->base + off + 4u, hi, j);
}

/* a page-table entry for table `at` (offset in the scratch) pointing to the page at scratch offset `next`: the next-level PA is derived HERE from j->base, so the GPU's
 * translation can only ever lead into the five scratch pages (the audit pins this function's text) */
static int entry(const struct jctx *j, uintptr_t at, uintptr_t next, uint32_t flags)
{
	uint64_t pa = (uint64_t)(j->base + next);

	return put64(j, at, (uint32_t)pa | flags, (uint32_t)(pa >> 32));
}

/* zero the five pages, then the four table entries and the job (kbase entry formats: mmu_mode_aarch64.c:111-190; Mesa v9.xml Job Header / Write Value Job Payload) */
static int build(const struct jctx *j)
{
	int e = 0;

	for (unsigned i = 0; i < NPAGES * PG / 4u && !e; i++)
		e = w32(j->base + 4u * i, 0, j);
	if (e)
		return e;
	e = entry(j, 0x0000u, 0x1000u, GPU_JOB_PTE_TABLE);				/* L0[0] -> L1 */
	if (!e)
		e = entry(j, 0x1000u, 0x2000u, GPU_JOB_PTE_TABLE);			/* L1[0] -> L2 */
	if (!e)
		e = entry(j, 0x2000u, 0x3000u, GPU_JOB_PTE_TABLE);			/* L2[0] -> L3 */
	if (!e)
		e = entry(j, 0x3000u + 8u * 1u, 0x4000u, GPU_JOB_PTE_LEAF);		/* L3[1] (VA 0x1000) -> data page, RW, non-cacheable */
	/* job header at data+0x00: word 4 = Index 1 << 16 | Type (Write value = 2) << 1; every other header word 0 (no dependencies, Next = 0, Barrier 0) */
	if (!e)
		e = w32(j->base + 0x4000u + 0x10u, GPU_JOB_HDR_W4, j);
	/* payload at data+0x20: Address (64) = target VA, Type (32) at +0x28 = Immediate 64, Immediate Value (64) at +0x30 */
	if (!e)
		e = put64(j, 0x4000u + 0x20u, GPU_JOB_TARGET_VA, 0);
	if (!e)
		e = w32(j->base + 0x4000u + 0x28u, GPU_JOB_WV_IMM64, j);
	if (!e)
		e = put64(j, 0x4000u + 0x30u, GPU_JOB_MAGIC_LO, GPU_JOB_MAGIC_HI);
	/* target word at data+0x100 starts as a known pattern, so "unchanged" is distinguishable from "zero written" */
	if (!e)
		e = put64(j, 0x4000u + 0x100u, 0xa5a5a5a5u, 0xa5a5a5a5u);
	return e;
}

/* wait AS_STATUS.AS_ACTIVE == 0, bounded: 0 = idle */
static int as_idle(void)
{
	uint64_t t0 = touch_ticks(), lim = limit_100ms();
	uint32_t st;

	for (unsigned i = 0; i < J_POLLS; i++) {
		if (r32(A_AS_STATUS, &st) && !(st & 1u))
			return 0;
		if (touch_ticks() - t0 > lim)
			return 1;
	}
	return 1;
}

/* AS0: TRANSCFG, TRANSTAB, MEMATTR, then wait AS_ACTIVE == 0 and UPDATE (kbase_mmu_hw_configure, mmu_hw_direct.c:323-356: the idle wait is in write_cmd BEFORE the command, :194-210;
 * JM mode has no wait after UPDATE, only CSF does, :356-358). Our extra wait after the UPDATE is a diagnostic, not kbase's. */
static int as_update(const struct jctx *j, uint32_t transcfg, uint32_t tablo, uint32_t tabhi)
{
	int e = w32(A_AS_TRANSCFG_LO, transcfg, j);

	if (!e)
		e = w32(A_AS_TRANSCFG_HI, 0, j);
	if (!e)
		e = w32(A_AS_TRANSTAB_LO, tablo, j);
	if (!e)
		e = w32(A_AS_TRANSTAB_HI, tabhi, j);
	if (!e)
		e = w32(A_AS_MEMATTR_LO, GPU_JOB_MEMATTR_LO, j);
	if (!e)
		e = w32(A_AS_MEMATTR_HI, GPU_JOB_MEMATTR_HI, j);
	if (e)
		return e;
	if (as_idle())				/* the previous command has not finished: no UPDATE on top of it */
		return 1;
	e = w32(A_AS_COMMAND, AS_CMD_UPDATE, j);
	if (e)
		return e;
	return as_idle();
}

/* the one hard-stop site (kbase: JS_COMMAND_HARD_STOP_1 for an atom with JOB_CHAIN_FLAG, jm_hw.c:532-537); 1 = written */
static int hardstop(const struct jctx *j)
{
	return !w32(A_JS_CMD, JS_CMD_HARDSTOP_1, j);
}

/* wait for a job event (done/failed bit of slot 0) or any MMU fault; 1 = an event arrived, 0 = timeout */
static int wait_event(void)
{
	uint64_t t0 = touch_ticks(), lim = limit_100ms();
	uint32_t jr, mr;

	for (unsigned i = 0; i < J_POLLS; i++) {
		if (r32(A_JOB_RAW, &jr) && (jr & IRQ_SLOT0_BITS))
			return 1;
		if (r32(A_MMU_RAW, &mr) && mr)
			return 1;
		if (touch_ticks() - t0 > lim)
			return 0;
	}
	return 0;
}

/* read-only preconditions; NULL = fine */
static const char *precheck(const struct jctx *j)
{
	uint32_t v;

	if (!j->base || (j->base & (PG - 1u)))
		return "scratch-bad";
	if (!counter_freq())
		return "no-timer";
	if (!r32(A_JS_PRESENT, &v) || !(v & 1u))
		return "js0-absent";
	if (!r32(A_JS0_FEAT, &v) || !(v & JS_FEATURE_SET_VALUE_JOB))
		return "js0-no-set-value";
	if (!r32(A_SHADER_READY, &v) || v != GPU_SHADER_PRESENT)
		return "shaders-not-ready";
	if (!r32(A_JOB_RAW, &v) || v)
		return "job-irq-pending";
	if (!r32(A_MMU_RAW, &v) || v)
		return "mmu-irq-pending";
	if (!r32(A_AS_STATUS, &v) || (v & 1u))
		return "as0-active";
	if (!r32(A_JS_CMD_NEXT, &v) || v)
		return "js0-busy";
	if (!r32(A_GPU_FAULT, &v) || v)
		return "gpu-fault";
	return 0;
}

int gpu_job_run(void)
{
	struct jctx jc;
	const struct jctx *j = &jc;
	uintptr_t sc = gpu_job_scratch();
	uint32_t v, jraw = 0, mraw = 0, jraw2 = 0, jraw3 = 0, mraw2 = 0, jsst = 0, t_lo = 0, t_hi = 0, hexc = 0, ev;
	const char *why;
	char line[RAMLOG_LINE_MAX];
	unsigned p;
	int e, sub = 0, waited = 0, stopped = 0, off;

	jc.base = sc;
	jc.l0_lo = (uint32_t)sc;
	jc.l0_hi = (uint32_t)(sc >> 32);
	p = put_txt(line, sizeof(line), 0, "JOB cfg scratch=");
	p = put_hex(line, sizeof(line), p, sc, 9);
	p = put_txt(line, sizeof(line), p, " job_va=");
	p = put_hex(line, sizeof(line), p, GPU_JOB_JOB_VA, 4);
	p = put_txt(line, sizeof(line), p, " target_va=");
	p = put_hex(line, sizeof(line), p, GPU_JOB_TARGET_VA, 4);
	emit(line, p);
	gpu_s2mpu_dump(sc);					/* read-only: what the S2MPU says about the granule of the scratch */
	rd("JS_PRESENT", A_JS_PRESENT, &v);			/* printed whether or not the precheck accepts them */
	rd("JS0_FEATURES", A_JS0_FEAT, &v);
	rd("JS1_FEATURES", A_JS1_FEAT, &v);
	rd("JS2_FEATURES", A_JS2_FEAT, &v);
	why = precheck(j);
	if (why) {
		say("refused: ", why);
		return 1;
	}
	gpu_job_cache_flush(sc, NPAGES * PG);			/* nothing stale of an earlier cacheable owner may be written back over our words later */
	e = build(j);
	gpu_job_cache_flush(sc, NPAGES * PG);
	if (e) {
		say("tables: ", "a RAM write failed, nothing submitted");
	} else {
		e = as_update(j, GPU_JOB_TRANSCFG_ON, jc.l0_lo, jc.l0_hi);
		rd("AS0_STATUS", A_AS_STATUS, &v);
		if (e) {
			say("as0: ", "UPDATE failed or did not complete, nothing submitted");
		} else {
			/* kbase_job_hw_submit (jm_hw.c:119-262): wait COMMAND_NEXT == 0 (precheck), HEAD_NEXT, AFFINITY_NEXT, CONFIG_NEXT, COMMAND_NEXT = START */
			e = w32(A_JS_HEAD_NEXT_LO, GPU_JOB_JOB_VA, j);
			if (!e)
				e = w32(A_JS_HEAD_NEXT_HI, 0, j);
			if (!e)
				e = w32(A_JS_AFF_NEXT_LO, GPU_SHADER_PRESENT, j);
			if (!e)
				e = w32(A_JS_AFF_NEXT_HI, 0, j);
			if (!e)
				e = w32(A_JS_CFG_NEXT, GPU_JOB_JS_CONFIG, j);
			if (!e)
				e = w32(A_JS_CMD_NEXT, JS_CMD_START, j);
			sub = !e;
			if (!e) {
				waited = wait_event();
				if (!waited) {			/* 100 ms: kbase hard-stops a hung job (jm_hw.c:532-537) */
					stopped = hardstop(j);
					if (stopped)
						waited = wait_event();
				}
			} else {
				say("submit: ", "a JS0 write failed");
			}
		}
	}
	/* report everything (read-only) */
	rd("JOB_IRQ_RAWSTAT", A_JOB_RAW, &jraw);
	rd("MMU_IRQ_RAWSTAT", A_MMU_RAW, &mraw);
	rd("JS0_STATUS", A_JS_STATUS, &jsst);
	rd("JS0_COMMAND_NEXT", A_JS_CMD_NEXT, &v);		/* a START still pending here is not touched by HARD_STOP (review L3) */
	rd("JS0_HEAD_LO", A_JS_HEAD_LO, &v);
	rd("AS0_FAULTSTATUS", A_AS_FAULTSTATUS, &ev);
	rd("AS0_FAULTADDR_LO", A_AS_FAULTADDR_LO, &v);
	rd("GPU_FAULTSTATUS", A_GPU_FAULT, &v);
	gpu_job_cache_flush(sc + 0x4000u, PG);
	r32(sc + 0x4000u + 0x100u, &t_lo);
	r32(sc + 0x4000u + 0x104u, &t_hi);
	r32(sc + 0x4000u, &hexc);				/* the job's Exception Status word, written back by the GPU */
	p = put_txt(line, sizeof(line), 0, "JOB target=");
	p = put_hex(line, sizeof(line), p, ((uint64_t)t_hi << 32) | t_lo, 16);
	p = put_txt(line, sizeof(line), p, " header_exc=");
	p = put_hex(line, sizeof(line), p, hexc, 8);
	emit(line, p);
	/* ALWAYS, in kbase's kill order (hard-stop a job that is still running, AS0 -> UNMAPPED, then clear the IRQ bits): mmu_mode_aarch64.c:71-80, jm_hw.c:532-537 */
	if (sub && !stopped && jsst == JS_STATUS_ACTIVE) {	/* e.g. stalled on an MMU fault: it never raised its own job bit */
		stopped = hardstop(j);
		if (stopped)
			(void)wait_event();
	}
	off = as_update(j, GPU_JOB_TRANSCFG_OFF, 0, 0);
	rd("AS0_STATUS_OFF", A_AS_STATUS, &v);
	rd("JOB_IRQ_RAWSTAT_END", A_JOB_RAW, &jraw2);
	rd("MMU_IRQ_RAWSTAT_END", A_MMU_RAW, &mraw2);
	if (jraw2 & IRQ_SLOT0_BITS)
		(void)w32(A_JOB_CLEAR, IRQ_SLOT0_BITS, j);
	if (mraw2 & IRQ_SLOT0_BITS)
		(void)w32(A_MMU_CLEAR, IRQ_SLOT0_BITS, j);
	rd("JOB_IRQ_RAWSTAT_CLEARED", A_JOB_RAW, &jraw3);	/* the _END read above is taken BEFORE the clear (G-4 item a): this one shows whether it took */
	jraw |= jraw2;
	mraw |= mraw2;
	p = put_txt(line, sizeof(line), 0, "JOB SUMMARY submit=");
	p = put_txt(line, sizeof(line), p, sub ? "ok" : "no");
	p = put_txt(line, sizeof(line), p, " wait=");
	p = put_txt(line, sizeof(line), p, !sub ? "n/a" : waited ? ((jraw & 0x10000u) ? "failed" : (mraw ? "mmu-fault" : "done")) : "timeout");
	p = put_txt(line, sizeof(line), p, stopped ? " hardstop=yes" : " hardstop=no");
	p = put_txt(line, sizeof(line), p, " target=");
	p = put_txt(line, sizeof(line), p, (t_lo == GPU_JOB_MAGIC_LO && t_hi == GPU_JOB_MAGIC_HI) ? "MAGIC" : (t_lo == 0xa5a5a5a5u && t_hi == 0xa5a5a5a5u) ? "unchanged" : "other");
	p = put_txt(line, sizeof(line), p, off ? " as0_off=FAILED" : " as0_off=ok");
	emit(line, p);
	return (t_lo == GPU_JOB_MAGIC_LO && t_hi == GPU_JOB_MAGIC_HI) ? 0 : 1;
}

#else	/* not gs101 (or no job constants): refuse */

int gpu_job_run(void) { return 1; }

#endif
