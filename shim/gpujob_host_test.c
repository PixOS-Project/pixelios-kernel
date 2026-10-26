/* Host test for gpujob.c (--gpu-job) against a FAKE Mali-G78: a register file with AS0 (UPDATE -> AS_STATUS active for a few polls), JS0 (COMMAND_NEXT = START runs the job),
 * a page-table walker over fake RAM (4 levels, 4 KiB, the entry formats of kbase's aarch64 mode), a WRITE_VALUE executor (job header type 2, payload address / type 7 /
 * immediate) and fault / hang / S2MPU-block / stuck-AS injection. Checks the exact GPU register write sequence, that every refusal performs ZERO writes, the cleanup
 * (IRQ bits cleared, AS0 back to UNMAPPED) in every outcome, and the allowlist pair by pair. It #includes gpujob.c and gpus2mpu.c (so the statics are reachable) and uses
 * the REAL generated board macros; gpus2mpu.c is linked as a separate unit. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpujob_host_test gpus2mpu.c gpujob_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "gpujob.c"

#if !(SOC_GS101 && defined(GPU_JOB_PTE_LEAF))
#error "needs the bluejay board_config.h"
#endif

#define SC	0x80100000ul
#define RAMSZ	(6u * 4096u)

static uint8_t ram[RAMSZ];			/* PA SC .. SC + RAMSZ (the sixth page is NOT in the allowlist window) */
static uint32_t reg[0x6000 / 4];		/* GPU_MALI window */
static struct { uintptr_t pa; uint32_t v; } glog[256];	/* GPU register writes */
static unsigned ngw, nram, nflush;
static uint64_t now, scratch_pa = SC;
static char lines[2048][RAMLOG_LINE_MAX + 8];
static unsigned nl;
/* injections */
static int stall_on_fault, hang, hang_ignore_stop, mmu_fault, s2mpu_block, as_stuck, as_hang, fail_ram_pa_set, fail_js, no_timer;
static uintptr_t fail_ram_pa;
static unsigned as_busy, stray;
static uint32_t top_stat;

void con_str(const char *label, const char *s)
{
	assert(nl < 2048);
	assert(strlen(s) < RAMLOG_LINE_MAX);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}
uint64_t counter_freq(void) { return no_timer ? 0 : 24576000ull; }
uint64_t touch_ticks(void) { return now += 200; }
uintptr_t gpu_job_scratch(void) { return scratch_pa; }
void gpu_job_cache_flush(uintptr_t pa, unsigned len) { (void)pa; (void)len; nflush++; }

static int in_ram(uintptr_t pa) { return pa >= SC && pa + 4 <= SC + RAMSZ; }
static int in_gpu(uintptr_t pa) { return pa >= GPU_MALI && pa < GPU_MALI + 0x6000; }
static uint32_t ram32(uintptr_t pa) { uint32_t v; memcpy(&v, ram + (pa - SC), 4); return v; }
static void ram_set32(uintptr_t pa, uint32_t v) { memcpy(ram + (pa - SC), &v, 4); }
static uint64_t ram64(uintptr_t pa) { return ram32(pa) | ((uint64_t)ram32(pa + 4) << 32); }
static uint32_t R(uintptr_t off) { return reg[off / 4]; }
static void setR(uintptr_t off, uint32_t v) { reg[off / 4] = v; }

/* translate a GPU VA through AS0 with kbase's 4-level 4 KiB format; 0 on a fault (and the fault is latched) */
static int xlate(uint64_t va, uint64_t *pa, int write)
{
	uint64_t tt = (uint64_t)R(0x2400) | ((uint64_t)R(0x2404) << 32), e;
	unsigned idx[4] = { (va >> 39) & 0x1ff, (va >> 30) & 0x1ff, (va >> 21) & 0x1ff, (va >> 12) & 0x1ff };

	if ((R(0x2430) & 0xf) != 6 || mmu_fault)
		goto fault;
	for (unsigned lvl = 0; lvl < 4; lvl++) {
		uintptr_t a = (uintptr_t)(tt & ~0xfffull) + 8u * idx[lvl];

		if (!in_ram(a))
			goto fault;
		e = ram64(a);
		if ((e & 3) != 3 || !(e & (1u << 10)))	/* table / level-3 page with the access flag */
			goto fault;
		if (lvl == 3) {
			if (((e >> 2) & 7) != 5 || ((e >> 6) & 3) != 1 || (write && ((e >> 6) & 3) != 1))
				goto fault;		/* non-cacheable attribute index 5, read/write */
			*pa = (e & 0x0000fffffffff000ull) | (va & 0xfff);
			return 1;
		}
		tt = e & 0x0000fffffffff000ull;
	}
fault:
	setR(0x2000, R(0x2000) | 1);			/* MMU_IRQ_RAWSTAT PAGE_FAULT(0) */
	setR(0x241c, 0xc3);				/* AS0_FAULTSTATUS: some translation fault */
	setR(0x2420, (uint32_t)va);
	return 0;
}

static void run_job(void)
{
	uint64_t head = (uint64_t)R(0x1840) | ((uint64_t)R(0x1844) << 32), pa, tpa;
	uint32_t w4, wtype;
	uint64_t addr, imm;

	assert(R(0x1850) == GPU_SHADER_PRESENT && R(0x1858) == GPU_JOB_JS_CONFIG && "affinity / config as kbase writes them");
	if (hang) {
		setR(0x1824, 8);			/* JS0_STATUS: ACTIVE (BASE_JD_EVENT_ACTIVE) forever */
		return;
	}
	if (!xlate(head, &pa, 0)) {
		if (stall_on_fault) { setR(0x1824, 8); return; }	/* kbase: a stalled job stays ACTIVE until it is stopped */
		setR(0x1000, R(0x1000) | 0x10000);
		setR(0x1824, 0x58);
		return;
	}
	w4 = ram32((uintptr_t)pa + 0x10);
	if (((w4 >> 1) & 0x7f) != 2) {
		setR(0x1000, R(0x1000) | 0x10000);
		setR(0x1824, 0x41);
		return;
	}
	addr = ram64((uintptr_t)pa + 0x20);
	wtype = ram32((uintptr_t)pa + 0x28);
	imm = ram64((uintptr_t)pa + 0x30);
	if (wtype != 7 || !xlate(addr, &tpa, 1)) {
		setR(0x1000, R(0x1000) | 0x10000);
		setR(0x1824, 0x58);
		return;
	}
	if (!s2mpu_block) {
		ram_set32((uintptr_t)tpa, (uint32_t)imm);
		ram_set32((uintptr_t)tpa + 4, (uint32_t)(imm >> 32));
	}
	ram_set32((uintptr_t)pa, 0);			/* Exception Status written back */
	setR(0x1000, R(0x1000) | 1);			/* JOB_IRQ_RAWSTAT done, slot 0 */
	setR(0x1824, 1);
}

int try_read32(uintptr_t pa, uint32_t *v)
{
	if (pa == GPU_PMU_G3D_STAT) { *v = top_stat; return 1; }
	if (in_gpu(pa)) {
		uintptr_t off = pa - GPU_MALI;

		if (off == 0x2428) {			/* AS0_STATUS: AS_ACTIVE while an UPDATE is in flight */
			if (as_stuck) { *v = 1; return 1; }
			if (as_busy) { as_busy--; *v = 1; return 1; }
		}
		*v = R(off);
		return 1;
	}
	if (in_ram(pa)) {
		*v = ram32(pa);
		return 1;
	}
	if ((pa >= GPU_S2MPU_G3D0 && pa < GPU_S2MPU_G3D3 + 0x10000)) { *v = 0; return 1; }
	stray++;
	return 0;
}

int try_write32(uintptr_t pa, uint32_t v)
{
	if (in_ram(pa)) {
		nram++;
		if (fail_ram_pa_set && pa == fail_ram_pa)
			return 0;
		ram_set32(pa, v);
		return 1;
	}
	if (!in_gpu(pa)) {
		stray++;
		return 0;
	}
	assert(ngw < 256);
	glog[ngw].pa = pa;
	glog[ngw++].v = v;
	{
		uintptr_t off = pa - GPU_MALI;

		if (fail_js && off == 0x1858)
			return 0;
		if (off == 0x1004) { setR(0x1000, R(0x1000) & ~v); return 1; }
		if (off == 0x2004) { setR(0x2000, R(0x2000) & ~v); return 1; }
		if (off == 0x2418) { as_busy = as_hang ? 0x7fffffffu : 3; return 1; }
		if (off == 0x1820) {
			if (v == 7 && !hang_ignore_stop && hang) {
				hang = 0;
				setR(0x1000, R(0x1000) | 0x10000);	/* the stopped job fails */
				setR(0x1824, 0x03);
			}
			return 1;
		}
		reg[off / 4] = v;
		if (off == 0x1860 && v == 1) {
			run_job();
			setR(0x1860, 0);		/* the job moved from NEXT to HEAD */
		}
	}
	return 1;
}

static void reset(void)
{
	memset(ram, 0xcc, sizeof(ram));
	memset(reg, 0, sizeof(reg));
	memset(glog, 0, sizeof(glog));
	ngw = nram = nflush = nl = stray = as_busy = 0;
	now = 1000;
	scratch_pa = SC;
	stall_on_fault = hang = hang_ignore_stop = mmu_fault = s2mpu_block = as_stuck = as_hang = fail_ram_pa_set = fail_js = no_timer = 0;
	top_stat = 1;
	setR(0x140, GPU_SHADER_PRESENT);	/* SHADER_READY: the power window is up */
	setR(0x01c, 1);				/* JS_PRESENT: slot 0 */
	setR(0x0c0, 0x20e);			/* JS0_FEATURES: SET_VALUE_JOB (bit 2) among others (the usual JM value is a guess) */
	setR(0x0c4, 0x20e);
	setR(0x0c8, 0x20e);
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); for (unsigned i_ = 0; i_ < nl; i_++) fprintf(stderr, "  %s\n", lines[i_]); assert(0); } } while (0)

static void gw(unsigned i, uintptr_t off, uint32_t v)
{
	if (!(i < ngw && glog[i].pa == GPU_MALI + off && glog[i].v == v))
		fprintf(stderr, "GPU write %u: got pa=%#lx v=%#x, want off=%#lx v=%#x (n=%u)\n", i, i < ngw ? (unsigned long)glog[i].pa : 0ul, i < ngw ? glog[i].v : 0u, (unsigned long)off, v, ngw);
	assert(i < ngw && glog[i].pa == GPU_MALI + off && glog[i].v == v);
}

static void refused(const char *reason)
{
	EXPECT(gpu_job_run() == 1 && ngw == 0 && nram == 0 && has("JOB refused: ") && has(reason));
}

int main(void)
{
	/* happy path */
	reset();
	EXPECT(gpu_job_run() == 0);
	EXPECT(ram64(SC + 0x4000 + 0x100) == 0x504958454C494F53ull);
	EXPECT(has("JOB JOB_IRQ_RAWSTAT_CLEARED pa=01C501000 v=00000000") && has("JOB SUMMARY submit=ok wait=done hardstop=no target=MAGIC as0_off=ok") && has("GPU JOB target=504958454C494F53"));
	EXPECT(has("GPU JOB JS_PRESENT pa=01C50001C v=00000001") && has("GPU JOB JS0_FEATURES pa=01C5000C0 v=0000020E") && has("GPU JOB JS2_FEATURES pa=01C5000C8") && has("GPU JOB JS0_COMMAND_NEXT pa=01C501860 v=00000000"));
	EXPECT(has("S2MPU probe_pa=080100000") && has("GPU JOB cfg scratch=080100000 job_va=1000 target_va=1100"));
	/* the exact GPU register write sequence: AS0, JS0, IRQ clear, AS0 off */
	EXPECT(ngw == 13 + 7 + 1);
	gw(0, 0x2430, GPU_JOB_TRANSCFG_ON); gw(1, 0x2434, 0); gw(2, 0x2400, (uint32_t)SC); gw(3, 0x2404, 0);
	gw(4, 0x2408, GPU_JOB_MEMATTR_LO); gw(5, 0x240c, GPU_JOB_MEMATTR_HI); gw(6, 0x2418, 1);
	gw(7, 0x1840, 0x1000); gw(8, 0x1844, 0); gw(9, 0x1850, GPU_SHADER_PRESENT); gw(10, 0x1854, 0); gw(11, 0x1858, GPU_JOB_JS_CONFIG); gw(12, 0x1860, 1);
	gw(13, 0x2430, GPU_JOB_TRANSCFG_OFF); gw(14, 0x2434, 0); gw(15, 0x2400, 0); gw(16, 0x2404, 0); gw(17, 0x2408, GPU_JOB_MEMATTR_LO); gw(18, 0x240c, GPU_JOB_MEMATTR_HI); gw(19, 0x2418, 1);
	gw(20, 0x1004, 0x10001);						/* kbase order: AS0 off first, the IRQ bits are cleared last */
	EXPECT(R(0x1000) == 0 && R(0x2430) == GPU_JOB_TRANSCFG_OFF && R(0x2400) == 0);	/* IRQ bit cleared, AS0 unmapped */
	EXPECT(nram == 5 * 1024 + 4 * 2 + 1 + 2 + 1 + 2 + 2 && nflush == 3);
	/* page tables as written: L0[0] -> L1 | 0x403, L3[1] -> data | 0x457; the job header word 4 = 4; payload; the unwritten rest zero */
	EXPECT(ram64(SC) == (SC + 0x1000) + 0x403 && ram64(SC + 0x1000) == (SC + 0x2000) + 0x403 && ram64(SC + 0x2000) == (SC + 0x3000) + 0x403);
	EXPECT(ram64(SC + 0x3000 + 8) == (SC + 0x4000) + 0x457 && ram64(SC + 0x3000) == 0);
	EXPECT(ram32(SC + 0x4000 + 0x10) == 0x10004 && ram64(SC + 0x4000 + 0x20) == 0x1100 && ram32(SC + 0x4000 + 0x28) == 7 && ram64(SC + 0x4000 + 0x30) == 0x504958454C494F53ull);
	EXPECT(ram32(SC + 5 * 4096) == 0xcccccccc);			/* the sixth page was never touched */

	/* a high scratch PA: TRANSTAB gets both words */
	reset();
	EXPECT(gpu_job_run() == 0 && glog[2].v == (uint32_t)SC);

	/* every precondition separately: refused with ZERO writes */
	reset(); setR(0x1000, 1); refused("job-irq-pending");
	reset(); setR(0x2000, 1); refused("mmu-irq-pending");
	reset(); as_stuck = 1; refused("as0-active");
	reset(); setR(0x1860, 1); refused("js0-busy");
	reset(); setR(0x003c, 1); refused("gpu-fault");
	reset(); setR(0x140, 0); refused("shaders-not-ready");
	reset(); setR(0x01c, 0); refused("js0-absent");
	reset(); setR(0x0c0, 0x208); refused("js0-no-set-value");			/* the features are printed even when refused */
	EXPECT(has("GPU JOB JS0_FEATURES pa=01C5000C0 v=00000208") && has("GPU JOB JS1_FEATURES"));
	reset(); scratch_pa = SC + 0x10; refused("scratch-bad");
	reset(); scratch_pa = 0; refused("scratch-bad");
	reset(); no_timer = 1; refused("no-timer");

	/* the job hangs: after 100 ms ONE hard stop (JS0_COMMAND = 7), the stopped job fails; cleanup as usual */
	reset(); hang = 1;
	EXPECT(gpu_job_run() == 1 && has("wait=failed hardstop=yes target=unchanged as0_off=ok"));
	{
		unsigned stops = 0;

		for (unsigned i = 0; i < ngw; i++)
			if (glog[i].pa == GPU_MALI + 0x1820) { stops++; EXPECT(glog[i].v == 7); }
		EXPECT(stops == 1);
	}
	EXPECT(R(0x1000) == 0 && R(0x2430) == GPU_JOB_TRANSCFG_OFF);
	reset(); hang = 1; hang_ignore_stop = 1;			/* the hard stop does not help: reported, power-off follows */
	EXPECT(gpu_job_run() == 1 && has("wait=timeout hardstop=yes target=unchanged as0_off=ok"));

	/* an MMU fault (bad translation): the MMU IRQ bit is cleared again, the job did not run */
	reset(); mmu_fault = 1;
	EXPECT(gpu_job_run() == 1 && has("wait=failed") && has("target=unchanged") && R(0x2000) == 0 && R(0x1000) == 0);
	{
		unsigned mc = 0;

		for (unsigned i = 0; i < ngw; i++)
			if (glog[i].pa == GPU_MALI + 0x2004) { mc++; EXPECT(glog[i].v == 0x10001); }
		EXPECT(mc == 1);
	}

	/* an MMU fault that leaves the job ACTIVE (stalled): kbase's kill order = hard stop, AS0 -> UNMAPPED, then clear the IRQ bits */
	reset(); mmu_fault = 1; stall_on_fault = 1;
	EXPECT(gpu_job_run() == 1 && has("hardstop=yes") && R(0x2000) == 0 && R(0x2430) == GPU_JOB_TRANSCFG_OFF);
	{
		int hs = -1, off = -1, clr = -1;

		for (unsigned i = 0; i < ngw; i++) {
			if (glog[i].pa == GPU_MALI + 0x1820 && hs < 0) hs = (int)i;
			if (glog[i].pa == GPU_MALI + 0x2430 && glog[i].v == GPU_JOB_TRANSCFG_OFF) off = (int)i;
			if ((glog[i].pa == GPU_MALI + 0x2004 || glog[i].pa == GPU_MALI + 0x1004) && clr < 0) clr = (int)i;
		}
		EXPECT(hs >= 0 && off > hs && clr > off);
	}

	/* the S2MPU drops the GPU's write: the job completes, the word stays, the report says so */
	reset(); s2mpu_block = 1;
	EXPECT(gpu_job_run() == 1 && has("wait=done") && has("target=unchanged"));

	/* AS0 UPDATE never completes (AS_ACTIVE stays set): nothing submitted (no JS0 write), the disable is still attempted and reported */
	reset(); as_hang = 1;
	EXPECT(gpu_job_run() == 1 && has("as0: ") && has("nothing submitted") && has("as0_off=FAILED") && has("JOB SUMMARY submit=no"));
	{
		unsigned cmds = 0;

		for (unsigned i = 0; i < ngw; i++) {
			EXPECT(glog[i].pa >= GPU_MALI + 0x2400 && glog[i].pa <= GPU_MALI + 0x2438);
			cmds += glog[i].pa == GPU_MALI + 0x2418;
		}
		EXPECT(cmds == 1);			/* the OFF update waits for AS_ACTIVE first (kbase write_cmd) and is NOT sent on top of the hung one */
	}
	/* write faults: a RAM word of the page tables, a JS0 register */
	reset(); fail_ram_pa_set = 1; fail_ram_pa = SC + 0x2000;
	EXPECT(gpu_job_run() == 1 && has("tables: ") && has("nothing submitted"));
	for (unsigned i = 0; i < ngw; i++)
		EXPECT(glog[i].pa >= GPU_MALI + 0x2400 && glog[i].pa <= GPU_MALI + 0x2438);	/* only the AS0 disable ran */
	reset(); fail_js = 1;
	EXPECT(gpu_job_run() == 1 && has("submit: ") && has("JOB SUMMARY submit=no"));

	/* the allowlist, pair by pair: exactly these across the whole GPU window and the neighbouring blocks */
	{
		struct jctx j;
		static const uint32_t vals[] = { 0, 1, 2, 3, 6, 7, 0x1000, 0x10001, 0x42000001, 0x42000006, 0x83b00, 0x888d8f88, 0x4c8d, 0x779fff, 0x80100000u, 0xffffffffu };
		unsigned ok = 0;

		j.base = SC; j.l0_lo = (uint32_t)SC; j.l0_hi = 0;
		for (uintptr_t pa = GPU_MALI - 0x1000; pa < GPU_MALI + 0x6000; pa += 4)
			for (unsigned i = 0; i < sizeof(vals) / sizeof(vals[0]); i++)
				if (w32_allowed(pa, vals[i], &j))
					ok++;
		/* 13 single-value registers... counted explicitly below: TRANSCFG_LO 2 values, TRANSCFG_HI 1, MEMATTR_LO 1, MEMATTR_HI 1, TRANSTAB_LO 2, TRANSTAB_HI 1 (0; l0_hi is 0 too),
		 * AS_COMMAND 1, HEAD_NEXT_LO 1, HEAD_NEXT_HI 1, AFF_NEXT_LO 1, AFF_NEXT_HI 1, CFG_NEXT 1, CMD_NEXT 1, JS_CMD 1, JOB_CLEAR 1, MMU_CLEAR 1 */
		EXPECT(ok == 2 + 1 + 1 + 1 + 2 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1);
		EXPECT(!w32_allowed(GPU_MALI + 0x30, 1, &j) && !w32_allowed(GPU_MALI + 0x30, 0x100, &j));		/* no GPU_COMMAND (soft reset 0x01 / 0x100) */
		EXPECT(!w32_allowed(GPU_MALI + 0x1860, 0, &j) && !w32_allowed(GPU_MALI + 0x1820, 3, &j) && !w32_allowed(GPU_MALI + 0x1820, 1, &j));	/* only START and HARD_STOP_1 */
		EXPECT(!w32_allowed(GPU_MALI + 0x1008, 0xffffffffu, &j) && !w32_allowed(GPU_MALI + 0x2008, 0xffffffffu, &j));	/* no IRQ mask */
		EXPECT(!w32_allowed(GPU_MALI + 0x2410, 0, &j) && !w32_allowed(GPU_MALI + 0x2438, 0, &j) && !w32_allowed(GPU_MALI + 0x2440 + 0x2430, 1, &j));	/* no LOCKADDR, no AS1 */
		EXPECT(!w32_allowed(GPU_S2MPU_G3D0, 0, &j) && !w32_allowed(GPU_S2MPU_G3D0 + 0x4004, 6, &j) && !w32_allowed(GPU_PMU_G3D_CFG, 1, &j));	/* never the S2MPU / PMU */
		EXPECT(w32_allowed(SC, 1, &j) && w32_allowed(SC + 5 * 4096 - 4, 1, &j) && !w32_allowed(SC + 5 * 4096, 1, &j) && !w32_allowed(SC - 4, 1, &j) && !w32_allowed(SC + 2, 1, &j));
	}
	puts("PASS gpujob host test");
	return 0;
}
