/* T-34eb host harness for the Mali job state machine in bsd/dev/sweet_mali.c (K1-K4 of docs/reviews/T-34eb-proof.md).
 *
 * The driver source is #included into this translation unit (SM_SRC, set by run.sh), so the static functions check_job()/finish()/sm_ov_ok()/sm_can_queue() and the
 * state (sm, ov, pend[]) are reachable; sweet_mali.c itself is never edited. The XNU headers it includes resolve to the empty files in stub/, xnu_stub.h is force-included.
 *
 * Hardware model design choice (hook AND array):
 *  - the driver's sm.g points at the array regs[]; rr() reads from it, wr() stores to it. Reads therefore return model values.
 *  - sweet_mmio_log() (called by rr()/wr(); 'W' BEFORE the store, 'R' after the load) is the model's WRITE hook: a write of START to JS_COMMAND_NEXT activates the slot,
 *    HARD_STOP to JS_COMMAND arms a stop, JOB_CLEAR/MMU_CLEAR/GPU_IRQ_CLEAR clear RAW bits, GPU_COMMAND soft reset clears both slots. Every write is recorded.
 *  - the hook cannot change a value the store is about to overwrite, so "COMMAND_NEXT reads 0 again after START" is done lazily: m_settle() clears it at the next hook call
 *    and at every test API boundary (the driver never reads a slot's COMMAND_NEXT right after starting that same slot).
 *  - the test completes/fails a slot explicitly (m_complete/m_fail: JOB_RAW bit n / 16+n), raises an MMU fault (m_mmu), or lets a HARD_STOP end the job failed at the next
 *    m_step() unless sl[n].hang is set. A job's final state is "acked" only when the driver writes JOB_CLEAR for its bit; a reset before the ack kills it (K4).
 *
 * Job thread: no thread is started. step() = m_step() (model) + th_poll() (the sm_thread loop body: lock, check_job() if busy, unlock) + 200 us on the fake clock.
 * Sleeping: delay()/lck_mtx_sleep_deadline() never block; they advance the fake clock by the requested time and count a sleep. sweet_mali_submit() loops (sleeps) when it
 * can neither start, park nor overlap: the harness never calls it in that state (would_block(), built from the driver's own predicates sm_can_queue()/sm_ov_ok()), and
 * sub() fails the case if a submit slept. So no sleep stub has to run a model step.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <setjmp.h>
#include <signal.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include SM_SRC   /* "<dir>/bsd/dev/sweet_mali.c" */

/* ======================= fake clock, locks, printf capture ======================= */
static uint64_t now_v;                       /* fake uptime in us */
static unsigned sleeps, lock_depth, wake_n;

void clock_get_uptime(uint64_t *t) { *t = now_v * 1000ull; }
void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns = t; }
void clock_interval_to_deadline(uint32_t interval, uint32_t scale, uint64_t *dl) { *dl = now_v * 1000ull + (uint64_t)interval * scale; }
void delay(uint32_t us) { now_v += us; }
void thread_wakeup(event_t ev) { (void)ev; wake_n++; }
void lck_mtx_lock(lck_mtx_t *l) { (void)l; if (lock_depth != 0) { fprintf(stderr, "HARNESS: lock taken twice\n"); abort(); } lock_depth = 1; }
void lck_mtx_unlock(lck_mtx_t *l) { (void)l; if (lock_depth != 1) { fprintf(stderr, "HARNESS: unlock without lock\n"); abort(); } lock_depth = 0; }
int lck_mtx_sleep_deadline(lck_mtx_t *l, int act, event_t ev, int intr, uint64_t dl)
{
	(void)l; (void)act; (void)ev; (void)intr;
	if (lock_depth != 1) { fprintf(stderr, "HARNESS: sleep without lock\n"); abort(); }
	sleeps++;                                   /* never blocks: the clock jumps to the deadline */
	if (dl > now_v * 1000ull) { now_v = (dl + 999) / 1000; }
	return 0;
}

#define LOGMAX 4000
static char logl[LOGMAX][512];
static unsigned logn, logdrop, cnt_ignored_line, cnt_refused_line;
int sm_host_printf(const char *fmt, ...)
{
	char b[512];
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(b, sizeof(b), fmt, ap);
	va_end(ap);
	if (strstr(b, "OVL submit bit ignored")) { cnt_ignored_line++; }
	if (strstr(b, "overlap start refused")) { cnt_refused_line++; }
	if (logn < LOGMAX) { snprintf(logl[logn++], sizeof(logl[0]), "%s", b); } else { logdrop++; }
	return n;
}

/* ======================= stubs that must never be reached ======================= */
#define UNREACHED(name) do { fprintf(stderr, "HARNESS: unexpected call to %s\n", name); abort(); } while (0)
int PE_parse_boot_argn(const char *name, void *p, int sz) { (void)name; (void)p; (void)sz; return 0; }
int sweet_gpu_precheck(volatile uint32_t *g, int s2mpu_open, const char **why) { (void)g; (void)s2mpu_open; (void)why; UNREACHED("sweet_gpu_precheck"); return 1; }
int sweet_gpu_power_on(volatile uint32_t *g) { (void)g; return 0; }   /* called by sm_hw_config_and_power() during a reset: power-on succeeds, READY regs stay 0 (see sm_reset_do) */
uint64_t thread_get_runtime_self(void) { return 0; }
kern_return_t kmem_alloc_contig(void *map, vm_offset_t *va, vm_size_t sz, vm_offset_t mask, int a, int b, int flags, int tag) { (void)map; (void)va; (void)sz; (void)mask; (void)a; (void)b; (void)flags; (void)tag; UNREACHED("kmem_alloc_contig"); return 1; }
void *kernel_map;
kern_return_t kernel_thread_start(void (*fn)(void *, wait_result_t), void *arg, thread_t *th) { (void)fn; (void)arg; (void)th; UNREACHED("kernel_thread_start"); return 1; }
void thread_deallocate(thread_t th) { (void)th; UNREACHED("thread_deallocate"); }
void thread_block(thread_continue_t c) { (void)c; UNREACHED("thread_block"); }
void *kalloc_data(size_t n, int flags) { (void)n; (void)flags; UNREACHED("kalloc_data"); return NULL; }
void kfree_data(void *p, size_t n) { (void)p; (void)n; UNREACHED("kfree_data"); }
int proc_iterate(int flags, int (*cb)(proc_t, void *), void *arg, void *a, void *b) { (void)flags; (void)cb; (void)arg; (void)a; (void)b; UNREACHED("proc_iterate"); return 0; }
int proc_pid(proc_t p) { (void)p; UNREACHED("proc_pid"); return 0; }
task_t proc_task(proc_t p) { (void)p; UNREACHED("proc_task"); return NULL; }
int cpu_number(void) { return 0; }
processor_t thread_bind(processor_t p) { (void)p; UNREACHED("thread_bind"); return p; }
void thread_terminate_self(void) { UNREACHED("thread_terminate_self"); }
void flush_dcache64(addr64_t a, unsigned n, int c) { (void)a; (void)n; (void)c; UNREACHED("flush_dcache64"); }
vm_offset_t kvtophys(vm_offset_t v) { (void)v; UNREACHED("kvtophys"); return 0; }
unsigned long gPhysBase;
boolean_t pmap_valid_page(ppnum_t p) { (void)p; UNREACHED("pmap_valid_page"); return 0; }
host_t host_self(void) { UNREACHED("host_self"); return NULL; }
kern_return_t host_statistics64(host_t h, host_flavor_t f, host_info64_t i, mach_msg_type_number_t *c) { (void)h; (void)f; (void)i; (void)c; UNREACHED("host_statistics64"); return 1; }
processor_t cpu_to_processor(int cpu) { (void)cpu; UNREACHED("cpu_to_processor"); return NULL; }
void processor_cpu_load_info(processor_t p, natural_t *t) { (void)p; (void)t; UNREACHED("processor_cpu_load_info"); }
unsigned int ml_get_cpu_count(void) { UNREACHED("ml_get_cpu_count"); return 0; }
void sweet_gpu_pll_reprint(unsigned sec) { (void)sec; UNREACHED("sweet_gpu_pll_reprint"); }
char *proc_name_address(void *p) { (void)p; UNREACHED("proc_name_address"); return NULL; }
kern_return_t task_info(task_t t, task_flavor_t f, task_info_t i, mach_msg_type_number_t *c) { (void)t; (void)f; (void)i; (void)c; UNREACHED("task_info"); return 1; }
vm_offset_t ml_io_map(vm_offset_t p, vm_size_t s) { (void)p; (void)s; UNREACHED("ml_io_map"); return 0; }

/* ======================= fake Mali register model ======================= */
#define NREGB 0x4000u
static uint32_t regs[NREGB / 4];

enum { J_NEW, J_SUB, J_RUN, J_UOK, J_UFAIL, J_OK, J_FAIL, J_KILL };   /* UOK/UFAIL: ended in hardware, the driver has not cleared its JOB_RAW bit yet */
#define NJOB 80000u
static struct job { uint64_t seq; unsigned req; int state; unsigned faults0; int taint; } jobs[NJOB];
static unsigned next_id, njobs_used;
static uint32_t seqjob[NJOB + 2];              /* seq -> job id + 1 */
static unsigned fault_floor;                   /* g_faults when the driver was last idle: a parked job may only be dropped if a fault came after that */
static unsigned g_faults;                      /* injected failures so far (a parked job may only be dropped after one) */

static struct { int active, stop_req, hang, pend_clear, has_last; uint64_t jc, last_jc; } sl[2];
static unsigned reset_n;
static char viol[300];

#define WLMAX 8192
static struct { unsigned off; uint32_t v; uint64_t t; } wl[WLMAX];
static unsigned wl_n;
static struct { int slot, other_active; uint64_t jc, other_jc; } evs[1024];
static unsigned ev_n, ev_chk;

#define ACTN 64
static char actl[ACTN][160];
static unsigned act_n;
static void act(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(actl[act_n % ACTN], sizeof(actl[0]), fmt, ap);
	va_end(ap);
	act_n++;
}

static void set_viol(const char *fmt, ...)
{
	va_list ap;
	if (viol[0]) { return; }
	va_start(ap, fmt);
	vsnprintf(viol, sizeof(viol), fmt, ap);
	va_end(ap);
}

static struct job *job_of_jc(uint64_t jc)
{
	uint64_t id = (jc - MALI_VA_START) / 8;
	if (jc < MALI_VA_START || id >= NJOB) { set_viol("start of unknown jc %#llx", (unsigned long long)jc); return NULL; }
	return &jobs[id];
}

static uint32_t slot_reg(unsigned n, uint32_t r) { return R_JS_BASE(n) + r; }

static void m_settle(void)
{
	for (unsigned n = 0; n < 2; n++) {
		if (sl[n].pend_clear) { regs[slot_reg(n, JS_COMMAND_NEXT) / 4] = 0; sl[n].pend_clear = 0; }
	}
}

static void m_end(unsigned n, int fail)
{
	regs[R_JOB_RAW / 4] |= 1u << (n + (fail ? 16 : 0));
	sl[n].active = 0;
	sl[n].last_jc = sl[n].jc;
	sl[n].has_last = 1;
	job_of_jc(sl[n].jc)->state = fail ? J_UFAIL : J_UOK;
	if (fail) { g_faults++; }
	act("model: JS%u job ends %s", n, fail ? "FAILED" : "ok");
}

static void m_reset(void)
{
	for (unsigned n = 0; n < 2; n++) {
		if (sl[n].active) { job_of_jc(sl[n].jc)->state = J_KILL; sl[n].active = 0; }
		if (sl[n].has_last) {
			struct job *j = job_of_jc(sl[n].last_jc);
			if (j->state == J_UOK || j->state == J_UFAIL) { j->state = J_KILL; }   /* K4: ended but not yet acknowledged by the driver */
			sl[n].has_last = 0;
		}
		sl[n].stop_req = 0;
		sl[n].hang = 0;
		sl[n].pend_clear = 0;
	}
	regs[R_JOB_RAW / 4] = 0;
	regs[R_MMU_RAW / 4] = 0;
	regs[R_GPU_IRQ_RAW / 4] |= GPU_IRQ_RESET_COMPLETED;
	reset_n++;
	act("model: GPU reset");
}

/* the model's write hook, see the header comment */
void sweet_mmio_log(int dev, char dir, uint32_t addr, int width, uint32_t v)
{
	(void)dev; (void)width;
	m_settle();
	if (dir != 'W') { return; }
	if (wl_n < WLMAX) { wl[wl_n].off = addr; wl[wl_n].v = v; wl[wl_n].t = now_v; wl_n++; }
	if (addr >= R_JS_BASE(0) && addr < R_JS_BASE(2)) {
		unsigned n = (addr - R_JS_BASE(0)) / 0x80u;
		uint32_t r = (addr - R_JS_BASE(0)) % 0x80u;
		if (r == JS_COMMAND_NEXT && v == JS_CMD_START) {
			uint64_t jc = (uint64_t)regs[slot_reg(n, JS_HEAD_NEXT) / 4] | ((uint64_t)regs[(slot_reg(n, JS_HEAD_NEXT) + 4) / 4] << 32);
			struct job *j = job_of_jc(jc);
			if (sl[n].active) { set_viol("START on JS%u while its job (jc %#llx) is active", n, (unsigned long long)sl[n].jc); }
			if (ev_n < 1024) { evs[ev_n].slot = (int)n; evs[ev_n].jc = jc; evs[ev_n].other_active = sl[1 - n].active; evs[ev_n].other_jc = sl[1 - n].jc; ev_n++; }
			if (j) { j->state = J_RUN; }
			sl[n].active = 1;
			sl[n].jc = jc;
			sl[n].pend_clear = 1;     /* COMMAND_NEXT reads 0 again, applied by m_settle() */
			act("model: START JS%u jc=%#llx", n, (unsigned long long)jc);
		} else if (r == JS_COMMAND && v == JS_CMD_HARD_STOP1) {
			sl[n].stop_req = 1;
			act("model: HARD_STOP JS%u (active=%d hang=%d)", n, sl[n].active, sl[n].hang);
		}
	} else if (addr == R_JOB_CLEAR) {
		if (v != 0xFFFFFFFFu) {   /* a per-bit clear is the driver's acknowledgement of a slot's end; the bulk clear of sm_reset_do is not */
			for (unsigned n = 0; n < 2; n++) {
				if ((v & ((1u << n) | (0x10000u << n))) && sl[n].has_last) {
					struct job *j = job_of_jc(sl[n].last_jc);
					if (j->state == J_UOK) { j->state = J_OK; }
					if (j->state == J_UFAIL) { j->state = J_FAIL; }
				}
			}
		}
		regs[R_JOB_RAW / 4] &= ~v;
	} else if (addr == R_MMU_CLEAR) {
		regs[R_MMU_RAW / 4] &= ~v;
	} else if (addr == R_GPU_IRQ_CLEAR) {
		regs[R_GPU_IRQ_RAW / 4] &= ~v;
	} else if (addr == R_GPU_COMMAND && (v == GPU_CMD_SOFT_RESET || v == GPU_CMD_HARD_RESET)) {
		m_reset();
	}
}

static void m_step(void)
{
	for (unsigned n = 0; n < 2; n++) {
		if (sl[n].stop_req) {
			sl[n].stop_req = 0;
			if (sl[n].active && !sl[n].hang) { m_end(n, 1); }   /* HARD_STOP ends the job failed unless the test says it hangs */
		}
	}
}

/* ======================= test infrastructure ======================= */
static jmp_buf case_jb;
static char case_reason[600];
#define FAILC(...) do { snprintf(case_reason, sizeof(case_reason), __VA_ARGS__); longjmp(case_jb, 1); } while (0)
#define CHECK(c, ...) do { if (!(c)) { FAILC(__VA_ARGS__); } } while (0)

static void setup(unsigned pend_depth, unsigned ovl)
{
	memset(regs, 0, sizeof(regs));
	memset(sl, 0, sizeof(sl));
	memset(jobs, 0, sizeof(jobs));
	memset(seqjob, 0, sizeof(seqjob));
	viol[0] = 0;
	wl_n = ev_n = ev_chk = logn = logdrop = act_n = 0;
	cnt_ignored_line = cnt_refused_line = 0;
	next_id = 0;
	njobs_used = 0;
	g_faults = 0;
	reset_n = 0;
	now_v = 1000000;
	sleeps = 0;
	/* driver state back to its post-init values (the cases run several scenarios in one process in S6) */
	sm.busy = 0; sm.busy_slot = 0; sm.busy_seq = sm.seq_next = sm.seq_done = sm.busy_start_us = 0; sm.stopped = sm.faulted = 0;
	memset(sm.fail_seq, 0, sizeof(sm.fail_seq));
	memset(&ov, 0, sizeof(ov));
	memset(pend, 0, sizeof(pend));
	pend_head = pend_n = 0;
	ovl_started = ovl_ring_started = ovl_refused = ovl_ignored = ovl_jfirst = ovl_ffirst = ovl_hidden_us = ovl_drop_next = 0;
	fault_floor = 0;
	sm.g = regs;
	sm.up = 1;
	sm.dead = 0;
	pend_on = pend_depth;
	ovl_on = ovl;
}

static void check_events(void)
{
	while (ev_chk < ev_n) {
		unsigned i = ev_chk++;
		struct job *j = job_of_jc(evs[i].jc);
		if (!j) { continue; }
		if (evs[i].slot == 0 && evs[i].other_active) {
			set_viol("K2: JS0 start (seq %llu) while JS1 job seq %llu is active", (unsigned long long)j->seq, (unsigned long long)job_of_jc(evs[i].other_jc)->seq);
		}
		if (evs[i].slot == 1 && evs[i].other_active) {
			struct job *a = job_of_jc(evs[i].other_jc);
			if (!(j->req & SWEET_MALI_REQ_OVL) || (j->req & SWEET_MALI_REQ_FS) || j->seq == 0 || a->seq == 0 || j->seq != a->seq + 1 || !(a->req & SWEET_MALI_REQ_FS)) {
				set_viol("K1: JS1 start beside active JS0 job without the K1 conditions: J seq=%llu req=%u, F seq=%llu req=%u", (unsigned long long)j->seq, j->req, (unsigned long long)a->seq, a->req);
			}
		}
	}
}

static void post_check(void)
{
	m_settle();
	check_events();
	if (viol[0]) { FAILC("model/K-rule violation: %s", viol); }
}

static void dump_trace(void)
{
	unsigned a0 = act_n > ACTN ? act_n - ACTN : 0;
	fprintf(stdout, "  trace: state seq_next=%llu seq_done=%llu busy=%d busy_slot=%u busy_seq=%llu ov.on=%d ov.done=%d ov.seq=%llu pend_n=%u faulted=%d stopped=%d dead=%d resets=%u fake_now_us=%llu\n",
	    (unsigned long long)sm.seq_next, (unsigned long long)sm.seq_done, sm.busy, sm.busy_slot, (unsigned long long)sm.busy_seq, ov.on, ov.done, (unsigned long long)ov.seq, pend_n,
	    sm.faulted, sm.stopped, sm.dead, reset_n, (unsigned long long)now_v);
	for (unsigned i = a0; i < act_n; i++) { fprintf(stdout, "  trace: act[%u] %s\n", i, actl[i % ACTN]); }
	unsigned w0 = wl_n > 50 ? wl_n - 50 : 0;
	for (unsigned i = w0; i < wl_n; i++) { fprintf(stdout, "  trace: write #%u t=%llu off=%#x val=%#x\n", i, (unsigned long long)wl[i].t, wl[i].off, wl[i].v); }
	unsigned l0 = logn > 12 ? logn - 12 : 0;
	for (unsigned i = l0; i < logn; i++) { fprintf(stdout, "  trace: log: %s", logl[i]); if (!strchr(logl[i], '\n')) { fputc('\n', stdout); } }
}

/* driver predicate for "sweet_mali_submit would loop" (the while condition at the top of sweet_mali_submit_tm) */
static int would_block(unsigned req)
{
	unsigned slot = (req & SWEET_MALI_REQ_FS) ? 0u : 1u;
	int try_ov = ovl_on && (req & SWEET_MALI_REQ_OVL) && slot == 1;
	return sm.busy && !sm.dead && !sm_can_queue() && !(try_ov && sm_ov_ok());
}

static int sub_raw(unsigned req, uint64_t *seq_out)
{
	unsigned id = next_id++;
	uint64_t seq = 0;
	unsigned s0 = sleeps;
	jobs[id].req = req;
	jobs[id].state = J_SUB;
	jobs[id].faults0 = fault_floor;
	if (id + 1 > njobs_used) { njobs_used = id + 1; }
	act("submit id=%u req=%s%s", id, (req & SWEET_MALI_REQ_FS) ? "FS" : "JS1", (req & SWEET_MALI_REQ_OVL) ? "|OVL" : "");
	CHECK(!would_block(req), "harness: sub() called in a state where sweet_mali_submit would loop (req=%u)", req);
	int rc = sweet_mali_submit(MALI_VA_START + 8ull * id, req, &seq);
	CHECK(sleeps == s0, "sweet_mali_submit slept %u times (it looped)", sleeps - s0);
	if (rc == 0) {
		jobs[id].seq = seq;      /* the seq is only known now; the START events of this call are checked after it */
		seqjob[seq] = id + 1;
		act("  -> seq %llu", (unsigned long long)seq);
	} else {
		act("  -> rc %d", rc);
	}
	post_check();
	if (rc != 0) { jobs[id].state = J_NEW; }
	*seq_out = seq;
	return rc;
}

static uint64_t sub(unsigned req)
{
	uint64_t seq;
	int rc = sub_raw(req, &seq);
	CHECK(rc == 0, "sweet_mali_submit(req=%u) returned %d", req, rc);
	return seq;
}

static void th_poll(void)
{
	lck_mtx_lock(&sm_lock);
	if (sm.busy) { check_job(); }
	lck_mtx_unlock(&sm_lock);
}

static void step(void)
{
	m_step();
	th_poll();
	now_v += 200;
	post_check();
}

static void m_complete(unsigned n) { CHECK(sl[n].active, "harness: m_complete JS%u not active", n); act("test: complete JS%u", n); m_end(n, 0); }
static void m_fail(unsigned n) { CHECK(sl[n].active, "harness: m_fail JS%u not active", n); act("test: fail JS%u", n); m_end(n, 1); }
/* K4: the fault takes every job that is in the hardware (or ended but not yet acknowledged) with it, even one that ends ok before the driver stops it */
static void m_mmu(void)
{
	act("test: MMU fault");
	regs[R_MMU_RAW / 4] |= 1u;
	g_faults++;
	for (unsigned i = 0; i < njobs_used; i++) { if (jobs[i].state == J_RUN || jobs[i].state == J_UOK || jobs[i].state == J_UFAIL) { jobs[i].taint = 1; } }
}
static void adv(uint64_t us) { now_v += us; act("test: clock +%llu us", (unsigned long long)us); }
static void wl_clear(void) { wl_n = 0; }

static unsigned count_w(uint32_t off, uint32_t v)
{
	unsigned c = 0;
	for (unsigned i = 0; i < wl_n; i++) { if (wl[i].off == off && wl[i].v == v) { c++; } }
	return c;
}
static int w_wait(uint64_t seq)
{
	int rc = sweet_mali_wait(seq, 1000);
	act("wait(%llu) = %d", (unsigned long long)seq, rc);
	return rc;
}
static uint64_t seq_of_slot(unsigned n) { return jobs[(sl[n].jc - MALI_VA_START) / 8].seq; }

/* ======================= cases S1..S12 ======================= */
static const char *dsc;   /* PASS text set by the case */

static void s1(void)
{
	setup(3, 0);
	uint64_t f = sub(SWEET_MALI_REQ_FS);
	uint64_t j = sub(SWEET_MALI_REQ_OVL);
	CHECK(f == 1 && j == 2, "seqs %llu,%llu", (unsigned long long)f, (unsigned long long)j);
	CHECK(sl[0].active && !sl[1].active, "JS1 started while JS0 runs (active0=%d active1=%d)", sl[0].active, sl[1].active);
	CHECK(ovl_ignored == 1 && cnt_ignored_line == 1, "ovl_ignored=%llu lines=%u", (unsigned long long)ovl_ignored, cnt_ignored_line);
	uint64_t j2 = sub(SWEET_MALI_REQ_OVL);
	(void)j2;
	CHECK(ovl_ignored == 2 && cnt_ignored_line == 1, "second ignored: ovl_ignored=%llu lines=%u (the line is printed once)", (unsigned long long)ovl_ignored, cnt_ignored_line);
	CHECK(ovl_started == 0 && !sl[1].active, "overlap started with the knob off");
	dsc = "knob off: flagged JS1 parked, never beside JS0, ovl_ignored counted, one log line";
}

static void s2(void)
{
	/* baseline: what sm_start_locked writes on slot 1 (idle GPU, unflagged JS1 submit of the same jc), taken in a forked child */
	struct { unsigned n; unsigned off[32]; uint32_t v[32]; } *shared = mmap(NULL, sizeof(*shared), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
	CHECK(shared != MAP_FAILED, "mmap");
	pid_t pid = fork();
	if (pid == 0) {
		setup(3, 1);
		next_id = 1;
		uint64_t s;
		int rc = sub_raw(0, &s);
		shared->n = 0;
		if (rc == 0) {
			for (unsigned i = 0; i < wl_n && shared->n < 32; i++) {
				if (wl[i].off >= R_JS_BASE(1) && wl[i].off < R_JS_BASE(2)) { shared->off[shared->n] = wl[i].off; shared->v[shared->n] = wl[i].v; shared->n++; }
			}
		} else { shared->n = 999; }
		_exit(0);
	}
	int st;
	waitpid(pid, &st, 0);
	CHECK(shared->n != 0 && shared->n != 999, "baseline JS1 start did not write (n=%u)", shared->n);
	setup(3, 1);
	uint64_t f = sub(SWEET_MALI_REQ_FS);
	CHECK(f == 1, "F seq %llu", (unsigned long long)f);
	CHECK(sl[0].active, "F not running");
	wl_clear();
	next_id = 1;
	uint64_t j = sub(SWEET_MALI_REQ_OVL);
	CHECK(j == 2, "J seq %llu, expected 2", (unsigned long long)j);
	CHECK(sl[0].active && sl[1].active, "J not started at once (active0=%d active1=%d pend_n=%u)", sl[0].active, sl[1].active, pend_n);
	CHECK(ovl_started == 1 && ov.on && ov.seq == 2, "ovl_started=%llu ov.on=%d ov.seq=%llu", (unsigned long long)ovl_started, ov.on, (unsigned long long)ov.seq);
	unsigned k = 0;
	for (unsigned i = 0; i < wl_n; i++) {
		if (wl[i].off >= R_JS_BASE(1) && wl[i].off < R_JS_BASE(2)) {
			CHECK(k < shared->n && shared->off[k] == wl[i].off && shared->v[k] == wl[i].v, "slot-1 write #%u is off=%#x val=%#x, sm_start_locked writes off=%#x val=%#x", k, wl[i].off, wl[i].v,
			    k < shared->n ? shared->off[k] : 0, k < shared->n ? shared->v[k] : 0);
			k++;
		}
	}
	CHECK(k == shared->n, "slot-1 write count %u, sm_start_locked writes %u", k, shared->n);
	dsc = "flagged JS1 starts at once beside FS (seq 2); slot-1 writes identical to sm_start_locked's";
}

static void s3(void)
{
	setup(3, 1);
	uint64_t f = sub(SWEET_MALI_REQ_FS), j = sub(SWEET_MALI_REQ_OVL);
	CHECK(f == 1 && j == 2 && sl[1].active, "setup f=%llu j=%llu", (unsigned long long)f, (unsigned long long)j);
	m_complete(1);
	step();
	CHECK(sm.seq_done == 0, "J done first: seq_done=%llu, expected 0", (unsigned long long)sm.seq_done);
	CHECK(sm.busy && sm.busy_slot == 0 && sm.busy_seq == 1, "F must stay the busy job (busy=%d slot=%u seq=%llu)", sm.busy, sm.busy_slot, (unsigned long long)sm.busy_seq);
	CHECK(w_wait(1) == ETIMEDOUT && w_wait(2) == ETIMEDOUT, "a wait returned before F completed");
	m_complete(0);
	step();
	CHECK(sm.seq_done == 2 && !sm.busy, "F done: seq_done=%llu busy=%d", (unsigned long long)sm.seq_done, sm.busy);
	CHECK(w_wait(1) == 0 && w_wait(2) == 0, "waits not ok");
	dsc = "J first: watermark stays 0; then F: seq_done 2, idle";
}

static void s4(void)
{
	setup(3, 1);
	uint64_t f = sub(SWEET_MALI_REQ_FS), j = sub(SWEET_MALI_REQ_OVL);
	CHECK(f == 1 && j == 2, "setup");
	uint64_t f3 = sub(SWEET_MALI_REQ_FS);
	CHECK(f3 == 3 && pend_n == 1, "parked FS seq=%llu pend_n=%u", (unsigned long long)f3, pend_n);
	m_complete(0);
	step();
	CHECK(sm.seq_done == 1, "F first: seq_done=%llu, expected 1", (unsigned long long)sm.seq_done);
	CHECK(sm.busy && sm.busy_slot == 1 && sm.busy_seq == 2, "busy_slot=%u busy_seq=%llu, expected 1 and 2", sm.busy_slot, (unsigned long long)sm.busy_seq);
	CHECK(!sl[0].active && pend_n == 1, "parked FS started beside J (active0=%d pend_n=%u)", sl[0].active, pend_n);
	step();
	CHECK(!sl[0].active, "parked FS started while J still runs");
	m_complete(1);
	step();
	CHECK(sm.seq_done == 2, "J done: seq_done=%llu", (unsigned long long)sm.seq_done);
	CHECK(sl[0].active && seq_of_slot(0) == 3, "parked FS seq3 did not start after J completed (active0=%d)", sl[0].active);
	dsc = "F first: seq_done 1, busy J (slot 1, seq 2); parked FS3 waits for J, then starts";
}

static void s4b(void)
{
	setup(3, 1);
	uint64_t f = sub(SWEET_MALI_REQ_FS), j = sub(SWEET_MALI_REQ_OVL);
	CHECK(f == 1 && j == 2, "setup");
	m_complete(0);
	step();
	CHECK(sm.busy && sm.busy_slot == 1 && sm.busy_seq == 2 && sm.seq_done == 1, "setup after F first");
	uint64_t j3 = sub(SWEET_MALI_REQ_OVL);   /* busy_seq == seq_next, nothing parked, but the busy job is on JS1: K1 needs F on JS0 */
	CHECK(j3 == 3, "seq %llu", (unsigned long long)j3);
	CHECK(sl[1].active && seq_of_slot(1) == 2 && !sl[0].active && pend_n == 1, "flagged JS1 submit beside a busy JS1 job was not parked (pend_n=%u)", pend_n);
	m_complete(1);
	step();
	CHECK(sl[1].active && seq_of_slot(1) == 3 && sm.seq_done == 2, "parked J3 did not start after J2 (seq_done=%llu)", (unsigned long long)sm.seq_done);
	dsc = "extra: flagged JS1 submit while the busy job is a JS1 job is parked (K1 needs F on JS0)";
}

static void s5(void)
{
	setup(3, 1);
	uint64_t f = sub(SWEET_MALI_REQ_FS), j = sub(0);
	CHECK(f == 1 && j == 2, "setup");
	CHECK(!sl[1].active && pend_n == 1, "unflagged JS1 submit not parked (active1=%d pend_n=%u)", sl[1].active, pend_n);
	CHECK(ovl_started == 0, "ovl_started=%llu", (unsigned long long)ovl_started);
	dsc = "unflagged JS1 submit while JS0 runs is parked, not started";
}

static void s6(void)
{
	/* (1) parked [FS a, flagged JS1 a+1]: both start when the running job ends */
	setup(3, 1);
	uint64_t f1 = sub(SWEET_MALI_REQ_FS), f2 = sub(SWEET_MALI_REQ_FS), j3 = sub(SWEET_MALI_REQ_OVL);
	CHECK(f1 == 1 && f2 == 2 && j3 == 3 && pend_n == 2, "setup f=%llu,%llu,%llu pend_n=%u", (unsigned long long)f1, (unsigned long long)f2, (unsigned long long)j3, pend_n);
	CHECK(pend[(pend_head + 1) % PEND_MAX].ovl == 1, "flagged JS1 was not parked as flagged");
	m_complete(0);
	step();
	CHECK(sl[0].active && seq_of_slot(0) == 2 && sl[1].active && seq_of_slot(1) == 3, "ring: FS2 and flagged JS1 3 not both started (active0=%d active1=%d)", sl[0].active, sl[1].active);
	CHECK(ovl_ring_started == 1 && pend_n == 0, "ovl_ring_started=%llu pend_n=%u", (unsigned long long)ovl_ring_started, pend_n);
	CHECK(ov.on && ov.seq == 3 && sm.busy_seq == 2, "ov.on=%d ov.seq=%llu busy_seq=%llu", ov.on, (unsigned long long)ov.seq, (unsigned long long)sm.busy_seq);
	m_complete(0); m_complete(1);
	step();
	CHECK(sm.seq_done == 3, "seq_done=%llu", (unsigned long long)sm.seq_done);
	/* (2) a flagged JS1 parked behind a JS1 job does not start beside it */
	setup(3, 1);
	f1 = sub(SWEET_MALI_REQ_FS);
	uint64_t a = sub(0), b = sub(SWEET_MALI_REQ_OVL);
	CHECK(f1 == 1 && a == 2 && b == 3 && pend_n == 2, "setup2");
	m_complete(0);
	step();
	CHECK(sl[1].active && seq_of_slot(1) == 2 && !sl[0].active && ovl_ring_started == 0 && pend_n == 1, "flagged JS1 behind a JS1 started (active0=%d ring=%llu pend_n=%u)", sl[0].active, (unsigned long long)ovl_ring_started, pend_n);
	/* (3) seq gap: [FS, flagged JS1 whose seq is not FS.seq + 1]. The gap cannot arise through the public API (seqs are consecutive), so the parked entry's seq is
	 * changed in place (pend[] is static state reached via the #include) to make the condition false. */
	setup(3, 1);
	f1 = sub(SWEET_MALI_REQ_FS);
	f2 = sub(SWEET_MALI_REQ_FS);
	j3 = sub(SWEET_MALI_REQ_OVL);
	CHECK(pend_n == 2, "setup3");
	pend[(pend_head + 1) % PEND_MAX].seq += 5;
	act("test: poked parked flagged JS1 seq %llu -> %llu", (unsigned long long)j3, (unsigned long long)pend[(pend_head + 1) % PEND_MAX].seq);
	m_complete(0);
	step();
	CHECK(sl[0].active && !sl[1].active && ovl_ring_started == 0 && pend_n == 1, "flagged JS1 with a seq gap started beside FS (active1=%d ring=%llu)", sl[1].active, (unsigned long long)ovl_ring_started);
	dsc = "ring variant: [FS a, flagged JS1 a+1] start together (ovl_ring_started 1); flagged behind a JS1 or with a seq gap does not (gap by in-place poke of pend[])";
}

static void s7(void)
{
	setup(3, 1);
	uint64_t a = sub(SWEET_MALI_REQ_FS | SWEET_MALI_REQ_OVL);
	CHECK(a == 1 && sl[0].active && !sl[1].active, "flagged JS0 not serial (active0=%d active1=%d)", sl[0].active, sl[1].active);
	CHECK(ovl_ignored == 1 && cnt_ignored_line == 1, "ignored=%llu line=%u", (unsigned long long)ovl_ignored, cnt_ignored_line);
	uint64_t b = sub(SWEET_MALI_REQ_FS | SWEET_MALI_REQ_OVL);
	CHECK(b == 2 && !sl[1].active && pend_n == 1 && ovl_ignored == 2 && ovl_started == 0, "second flagged JS0: active1=%d pend_n=%u ignored=%llu started=%llu", sl[1].active, pend_n, (unsigned long long)ovl_ignored, (unsigned long long)ovl_started);
	dsc = "flagged JS0 (FS|OVL): ignored counted, serial";
}

static void both_active(uint64_t *f, uint64_t *j)
{
	*f = sub(SWEET_MALI_REQ_FS);
	*j = sub(SWEET_MALI_REQ_OVL);
	CHECK(*f == 1 && *j == 2 && sl[0].active && sl[1].active, "setup: no overlap (f=%llu j=%llu)", (unsigned long long)*f, (unsigned long long)*j);
}

static void s8(void)
{
	setup(3, 1);
	uint64_t f, j;
	both_active(&f, &j);
	uint64_t p3 = sub(SWEET_MALI_REQ_FS), p4 = sub(0);
	CHECK(p3 == 3 && p4 == 4 && pend_n == 2, "parked jobs");
	wl_clear();
	m_mmu();
	for (int i = 0; i < 8 && sm.busy; i++) { step(); }
	CHECK(count_w(slot_reg(0, JS_COMMAND), JS_CMD_HARD_STOP1) == 1, "JS0 HARD_STOP writes: %u, expected 1", count_w(slot_reg(0, JS_COMMAND), JS_CMD_HARD_STOP1));
	CHECK(count_w(slot_reg(1, JS_COMMAND), JS_CMD_HARD_STOP1) == 1, "JS1 HARD_STOP writes: %u, expected 1 (K4: stop BOTH slots)", count_w(slot_reg(1, JS_COMMAND), JS_CMD_HARD_STOP1));
	CHECK(count_w(R_GPU_COMMAND, GPU_CMD_SOFT_RESET) == 1 && reset_n == 1, "soft reset writes %u, model resets %u", count_w(R_GPU_COMMAND, GPU_CMD_SOFT_RESET), reset_n);
	CHECK(!sm.busy && !sm.dead && pend_n == 0, "busy=%d dead=%d pend_n=%u after the fault", sm.busy, sm.dead, pend_n);
	CHECK(w_wait(f) == EIO, "wait(F) != EIO");
	CHECK(w_wait(j) == EIO, "wait(J) != EIO");
	CHECK(w_wait(p3) == EIO && w_wait(p4) == EIO, "parked jobs not EIO");
	CHECK(!sl[0].active && !sl[1].active, "a job is still active after the reset");
	dsc = "MMU fault with two active: HARD_STOP to both, reset, F/J/parked all EIO";
}

static void s9(void)
{
	setup(3, 1);
	uint64_t f, j;
	both_active(&f, &j);
	uint64_t p3 = sub(SWEET_MALI_REQ_FS), p4 = sub(0);
	m_fail(1);
	step();
	CHECK(sm.seq_done == 0 && sm.busy_slot == 0, "J failed first: seq_done=%llu busy_slot=%u", (unsigned long long)sm.seq_done, sm.busy_slot);
	m_complete(0);
	step();
	CHECK(sm.seq_done >= 2 || w_wait(2) != ETIMEDOUT, "J/F finished but seq_done=%llu", (unsigned long long)sm.seq_done);
	for (int i = 0; i < 4 && sm.busy; i++) { step(); }
	CHECK(w_wait(f) == 0, "wait(F) != 0 (F completed ok)");
	CHECK(w_wait(j) == EIO, "wait(J) != EIO");
	CHECK(w_wait(p3) == EIO && w_wait(p4) == EIO, "parked jobs not EIO (p3 and p4)");
	CHECK(!sm.busy && reset_n == 0 && !sl[0].active, "busy=%d resets=%u (a plain failure must not reset)", sm.busy, reset_n);
	dsc = "J fails, F ok: wait(F)==0, wait(J)==EIO, parked EIO, no reset";
}

static void s10(void)
{
	setup(3, 1);
	uint64_t f, j;
	both_active(&f, &j);
	uint64_t p3 = sub(SWEET_MALI_REQ_FS);
	m_fail(0);
	step();
	CHECK(sm.seq_done == 1 && sm.busy && sm.busy_slot == 1 && sm.busy_seq == 2, "F failed first: seq_done=%llu busy=%d slot=%u seq=%llu", (unsigned long long)sm.seq_done, sm.busy, sm.busy_slot, (unsigned long long)sm.busy_seq);
	CHECK(w_wait(f) == EIO, "wait(F) != EIO");
	CHECK(!sl[0].active, "parked job started behind a failed F");
	m_complete(1);
	step();
	CHECK(w_wait(j) == 0, "wait(J) != 0 (J completed ok)");
	CHECK(w_wait(p3) == EIO, "parked job not EIO");
	CHECK(!sm.busy && !sl[0].active && reset_n == 0, "busy=%d active0=%d resets=%u", sm.busy, sl[0].active, reset_n);
	dsc = "F fails while J runs: J becomes busy, completes ok (wait 0), parked job EIO";
}

static void s11a(void)
{
	setup(3, 1);
	uint64_t f, j;
	both_active(&f, &j);
	m_complete(0);
	step();
	CHECK(sm.seq_done == 1 && sm.busy_slot == 1 && sm.busy_seq == 2, "F done first");
	sl[1].hang = 1;
	wl_clear();
	adv(JOB_TIMEOUT_US + 1000);
	step();
	CHECK(count_w(slot_reg(1, JS_COMMAND), JS_CMD_HARD_STOP1) == 1, "JS1 HARD_STOP writes %u, expected 1", count_w(slot_reg(1, JS_COMMAND), JS_CMD_HARD_STOP1));
	CHECK(count_w(slot_reg(0, JS_COMMAND), JS_CMD_HARD_STOP1) == 0, "HARD_STOP on JS0 (F is already done)");
	CHECK(sm.stopped && sm.busy && reset_n == 0, "stopped=%d busy=%d resets=%u", sm.stopped, sm.busy, reset_n);
	step();
	CHECK(sm.busy && reset_n == 0, "reset before the grace period");
	adv(STOP_GRACE_US + 1000);
	step();
	CHECK(reset_n == 1 && !sm.busy && !sm.dead, "no reset after the grace period (resets=%u busy=%d)", reset_n, sm.busy);
	CHECK(w_wait(f) == 0, "wait(F) != 0");
	CHECK(w_wait(j) == EIO, "wait(J) != EIO");
	dsc = "F done, J times out: HARD_STOP JS1 only, then past STOP_GRACE_US still running -> reset, J EIO";
}

static void s11b(void)
{
	setup(3, 1);
	uint64_t f, j;
	both_active(&f, &j);
	sl[0].hang = sl[1].hang = 1;
	wl_clear();
	adv(JOB_TIMEOUT_US + 1000);
	step();
	unsigned h0 = count_w(slot_reg(0, JS_COMMAND), JS_CMD_HARD_STOP1), h1 = count_w(slot_reg(1, JS_COMMAND), JS_CMD_HARD_STOP1);
	CHECK(h0 == 1 && h1 == 1, "both overdue: HARD_STOP writes JS0=%u JS1=%u, expected 1 each", h0, h1);
	adv(STOP_GRACE_US + 1000);
	for (int i = 0; i < 4 && sm.busy; i++) { step(); }
	CHECK(reset_n == 1 && !sm.busy && !sm.dead, "resets=%u busy=%d dead=%d", reset_n, sm.busy, sm.dead);
	CHECK(w_wait(f) == EIO && w_wait(j) == EIO, "both must end EIO");
	dsc = "both overdue: HARD_STOP to both, then one reset, F and J both EIO";
}

static void s12x(int kind)
{
	setup(3, 1);
	uint64_t f = sub(SWEET_MALI_REQ_FS);
	CHECK(f == 1, "setup");
	if (kind == 0) { regs[slot_reg(1, JS_COMMAND_NEXT) / 4] = 1; }
	if (kind == 1) { regs[R_JOB_RAW / 4] |= 0x20000u; }
	if (kind == 2) { regs[R_MMU_RAW / 4] |= 1u; }
	wl_clear();
	uint64_t j = sub(SWEET_MALI_REQ_OVL);
	CHECK(j == 2, "seq %llu", (unsigned long long)j);
	CHECK(cnt_refused_line == 1 && ovl_refused == 1, "refused line=%u ovl_refused=%llu", cnt_refused_line, (unsigned long long)ovl_refused);
	CHECK(!sl[1].active && ovl_started == 0 && !ov.on, "J started beside F (active1=%d)", sl[1].active);
	CHECK(pend_n == 1, "J neither parked nor waiting (pend_n=%u)", pend_n);
	for (unsigned i = 0; i < wl_n; i++) { CHECK(!(wl[i].off >= R_JS_BASE(1) && wl[i].off < R_JS_BASE(2)), "register write to JS1 (off %#x) in the refused start", wl[i].off); }
	dsc = kind == 0 ? "JS1 COMMAND_NEXT nonzero: refused, logged, ovl_refused 1, J parked" : kind == 1 ? "extra: JS1 JOB_RAW fail bit set: refused, J parked" : "extra: MMU_RAW set: refused, J parked";
}
static void s12(void) { s12x(0); }
static void s12b(void) { s12x(1); }
static void s12c(void) { s12x(2); }

/* ======================= S13 fuzz ======================= */
static uint64_t rs;
static uint32_t rnd(void) { rs ^= rs >> 12; rs ^= rs << 25; rs ^= rs >> 27; return (uint32_t)((rs * 0x2545F4914F6CDD1Dull) >> 32); }

static uint64_t last_done, checked;
static unsigned fz_taint_ok;
static unsigned fz_ebusy;

static void verify_upto(uint64_t done)
{
	while (checked < done) {
		uint64_t s = ++checked;
		CHECK(s <= NJOB && seqjob[s] != 0, "seq %llu <= seq_done has no job", (unsigned long long)s);
		struct job *j = &jobs[seqjob[s] - 1];
		int rc = sweet_mali_wait(s, 0);
		switch (j->state) {
		case J_OK:
			/* a job that ended ok in the race window of an MMU fault may be reported ok or failed here; the literal K4 reading (always failed) is case S14 */
			CHECK(j->taint ? (rc == EIO || rc == 0) : rc == 0, "seq %llu completed ok in the model (tainted by an MMU fault: %d) but wait()=%d", (unsigned long long)s, j->taint, rc);
			if (j->taint && rc == 0) { fz_taint_ok++; }
			break;
		case J_FAIL: case J_KILL: CHECK(rc == EIO, "seq %llu failed/killed in the model but wait()=%d", (unsigned long long)s, rc); break;
		case J_SUB:
			CHECK(rc == EIO && g_faults > j->faults0, "seq %llu never started (parked): wait()=%d, injected faults since the GPU was last idle=%u (dropped without cause or reported ok)", (unsigned long long)s, rc, g_faults - j->faults0);
			break;
		default: FAILC("seq %llu <= seq_done=%llu but the job is still %s in the model", (unsigned long long)s, (unsigned long long)done, j->state == J_RUN ? "running" : "ended-but-unacknowledged");
		}
	}
}

static void invariants(void)
{
	CHECK(sm.seq_done >= last_done, "seq_done went backwards: %llu -> %llu", (unsigned long long)last_done, (unsigned long long)sm.seq_done);
	last_done = sm.seq_done;
	verify_upto(sm.seq_done);
	if (!sm.busy) { fault_floor = g_faults; }
	CHECK(sm.seq_done <= sm.seq_next, "seq_done %llu > seq_next %llu", (unsigned long long)sm.seq_done, (unsigned long long)sm.seq_next);
	CHECK(sm.busy || (!sl[0].active && !sl[1].active), "driver idle (busy=0) but model has active jobs (active0=%d active1=%d)", sl[0].active, sl[1].active);
	CHECK(!ov.on || (sm.busy && sm.busy_slot == 0), "ov.on without a busy JS0 job (busy=%d slot=%u)", sm.busy, sm.busy_slot);
	CHECK(!(sl[0].active && sl[1].active) || ov.on, "two active jobs but ov.on=0");
}

static void fz_step(void)
{
	step();
	invariants();
	wl_n = 0;
}

static void fz_sub(unsigned req)
{
	unsigned slot = (req & SWEET_MALI_REQ_FS) ? 0u : 1u;
	if (would_block(req)) { return; }
	if (ovl_on && (req & SWEET_MALI_REQ_OVL) && slot == 1 && sm_ov_ok() && !sm_can_queue() &&
	    ((regs[R_JOB_RAW / 4] & 0x20002u) || regs[R_MMU_RAW / 4] || regs[slot_reg(1, JS_COMMAND_NEXT) / 4])) {
		return;   /* the start would be refused and the retry could loop: not called in that state */
	}
	uint64_t seq;
	int rc = sub_raw(req, &seq);
	if (rc == EBUSY) { fz_ebusy++; } else { CHECK(rc == 0, "submit rc=%d", rc); }
	invariants();
}

static void fuzz_one(uint64_t seed, unsigned steps)
{
	setup(3, 1);
	rs = seed * 0x9E3779B97F4A7C15ull + 1;
	last_done = checked = 0;
	for (unsigned i = 0; i < steps; i++) {
		unsigned r = rnd() % 100;
		if (r < 14) { fz_sub(SWEET_MALI_REQ_FS); }
		else if (r < 24) { fz_sub(0); }
		else if (r < 32) { fz_sub(SWEET_MALI_REQ_OVL); }
		else if (r < 36) { fz_sub(SWEET_MALI_REQ_FS | SWEET_MALI_REQ_OVL); }
		else if (r < 50) { fz_sub(SWEET_MALI_REQ_FS); fz_sub(SWEET_MALI_REQ_OVL); }   /* the pair Mesa produces */
		else if (r < 66) { unsigned n = rnd() & 1; if (sl[n].active) { m_complete(n); } }
		else if (r < 69) { unsigned n = rnd() & 1; if (sl[n].active) { m_fail(n); } }
		else if (r < 70) { if (sm.busy && !regs[R_MMU_RAW / 4]) { m_mmu(); } }
		else if (r < 71) { sl[rnd() & 1].hang = (int)(rnd() & 1); }
		else if (r < 72) { if (sm.busy) { adv(JOB_TIMEOUT_US + 1000); g_faults++; } }
		else if (r < 73) { if (sm.busy) { adv(STOP_GRACE_US + 1000); } }
		fz_step();
	}
	/* drain: end every job, jump the clock for hung ones, until idle; everything submitted must be accounted for */
	for (unsigned i = 0; i < 400 && (sm.busy || pend_n); i++) {
		for (unsigned n = 0; n < 2; n++) { if (sl[n].active && (i < 3 || (rnd() & 1))) { sl[n].hang = 0; m_complete(n); } }
		if (i > 10 && sm.busy) { adv(i % 2 ? JOB_TIMEOUT_US + 1000 : STOP_GRACE_US + 1000); g_faults++; }
		fz_step();
	}
	CHECK(!sm.busy && pend_n == 0 && !sl[0].active && !sl[1].active, "drain did not reach idle (busy=%d pend_n=%u)", sm.busy, pend_n);
	CHECK(sm.seq_done == sm.seq_next, "after drain seq_done=%llu seq_next=%llu", (unsigned long long)sm.seq_done, (unsigned long long)sm.seq_next);
	verify_upto(sm.seq_done);
}

static uint64_t fz_seed0 = 1;
static void s13(void)
{
	static char d[200];
	unsigned total = 0;
	for (unsigned k = 0; k < 4; k++) {
		uint64_t seed = fz_seed0 + k;
		fprintf(stdout, "  S13 seed=%llu steps=20000\n", (unsigned long long)seed);
		fflush(stdout);
		fuzz_one(seed, 20000);
		total += 20000;
	}
	snprintf(d, sizeof(d), "seeded fuzz, 4 seeds from %llu x 20000 steps = %u steps, ebusy=%u, tainted-but-ok-reported=%u (see S14)", (unsigned long long)fz_seed0, total, fz_ebusy, fz_taint_ok);
	dsc = d;
}

/* S14: K4 literal. "MMU fault ... hard stop both slots, soft reset, BOTH jobs and every parked job reported failed". Here J ends OK in the same window as the fault
 * (the HARD_STOP for it is already written but J was faster): the driver acknowledges J as done and reports it ok. */
static void s14(void)
{
	setup(3, 1);
	uint64_t f, j;
	both_active(&f, &j);
	m_mmu();
	m_complete(1);                    /* J finishes in the same window as the fault (its bit is already in JOB_RAW when the driver looks) */
	step();                           /* driver sees MMU_RAW and J's done bit: hard stop to both, J acknowledged ok */
	for (int i = 0; i < 6 && sm.busy; i++) { step(); }
	CHECK(reset_n == 1 && !sm.busy, "resets=%u busy=%d", reset_n, sm.busy);
	CHECK(w_wait(f) == EIO, "wait(F) != EIO");
	CHECK(w_wait(j) == EIO, "K4 literal: wait(J)=%d, expected EIO (J ended ok while an MMU fault was handled and the GPU was reset)", sweet_mali_wait(j, 0));
	dsc = "K4 literal: job that ends ok in the window of an MMU fault is still reported failed";
}

/* ======================= runner ======================= */
static const struct { const char *name; void (*fn)(void); } cases[] = {
	{ "S1", s1 }, { "S2", s2 }, { "S3", s3 }, { "S4", s4 }, { "S4b", s4b }, { "S5", s5 }, { "S6", s6 }, { "S7", s7 }, { "S8", s8 }, { "S9", s9 }, { "S10", s10 },
	{ "S11a", s11a }, { "S11b", s11b }, { "S12", s12 }, { "S12b", s12b }, { "S12c", s12c }, { "S13", s13 }, { "S14", s14 },
};

int main(int argc, char **argv)
{
	unsigned fails = 0, n = sizeof(cases) / sizeof(cases[0]);
	const char *e = getenv("SWEET_MALI_SEED");
	if (e) { fz_seed0 = strtoull(e, NULL, 0); }
	setvbuf(stdout, NULL, _IOLBF, 0);
	(void)argc; (void)argv;
	for (unsigned i = 0; i < n; i++) {
		fflush(stdout);
		pid_t pid = fork();
		if (pid == 0) {
			alarm(120);
			if (setjmp(case_jb) == 0) {
				dsc = "";
				cases[i].fn();
				fprintf(stdout, "PASS %s: %s\n", cases[i].name, dsc);
				fflush(stdout);
				_exit(0);
			}
			fprintf(stdout, "FAIL %s: %s\n", cases[i].name, case_reason);
			dump_trace();
			fflush(stdout);
			_exit(1);
		}
		int st = 0;
		waitpid(pid, &st, 0);
		if (WIFSIGNALED(st)) {
			fprintf(stdout, "FAIL %s: harness/driver crashed with signal %d\n", cases[i].name, WTERMSIG(st));
			fails++;
		} else if (WEXITSTATUS(st) != 0) {
			fails++;
		}
	}
	if (fails) { fprintf(stdout, "RESULT: FAIL sweet-mali-host failed=%u\n", fails); return 1; }
	fprintf(stdout, "RESULT: PASS sweet-mali-host cases=%u\n", n);
	return 0;
}
