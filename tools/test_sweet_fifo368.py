#!/usr/bin/env python3
"""Patch 0368 (lowclk, research 166 levers 1-3: swap FIFO sweetfb_fifo=N, completion-kicked gate sweetfb_gatekick=1, per-second presents row): host test. Extracts the pure
queue block between SFQ-PURE-BEGIN / SFQ-PURE-END and the Mali kick block (sweet_mali_kick_register / _arm / sm_kick_tick) from xnu/patches/0368-sweet-fb-fifo.patch, compiles
them as C and checks: latest-wins when off, hold/replace/fifo_full, head order, the cap (catchup), the accounting invariant swaps = presents + replaced + queued over a
random run, drawn = more than 4 jobs, the row take/reset; the kick protocol (armed once, fires once, already done = caller acts, no hook = -1). Then text checks of every hook in
the patch, each with a mutant, and mutants of both C blocks.
`RESULT: PASS 0368-fifo cases=N mutants=M`. Not a hardware or kernel-build test."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0368-sweet-fb-fifo.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
%s
%s
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)

/* shadow model: record ids in the two slots, what the caller of the pure block does with them */
struct model { int head, hold; int shown_last; uint32_t swaps; };
static int arrive(struct sfq *q, struct model *m, int id, uint32_t jobs)
{
	m->swaps++;
	int a = sfq_arrive(q, jobs);
	if (a == SFQ_A_HOLD || a == SFQ_A_REPL_HOLD) m->hold = id; else m->head = id;
	return a;
}
static int take(struct sfq *q, struct model *m)   /* returns the shown id, 0 if nothing */
{
	if (m->head == 0) return 0;
	int shown = m->head;
	m->head = 0;
	if (sfq_take(q, false)) { m->head = m->hold; m->hold = 0; }
	return shown;
}
static void tick(struct sfq *q, struct model *m, bool hold_ready, int *shown)
{
	if (sfq_catchup(q, hold_ready)) { m->head = m->hold; m->hold = 0; }
	*shown = take(q, m);
}
static uint32_t queued(const struct model *m) { return (m->head != 0) + (m->hold != 0); }

int main(void)
{
	struct sfq q; struct model m; int shown, a;
	/* ---- off: latest wins, replaced counted, no hold ever */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m);
	CHECK(arrive(&q, &m, 1, 9) == SFQ_A_HEAD);
	CHECK(arrive(&q, &m, 2, 9) == SFQ_A_REPL_HEAD && m.head == 2 && q.r.replaced == 1 && q.r.held == 0 && !q.hold_valid);
	tick(&q, &m, true, &shown); CHECK(shown == 2 && q.r.presents == 1 && q.r.drawn == 1);
	tick(&q, &m, true, &shown); CHECK(shown == 0 && q.r.presents == 1);
	/* ---- fifo 255 (uncapped): 2nd goes to the hold, 3rd replaces the hold */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = SFQ_UNCAPPED;
	CHECK(arrive(&q, &m, 1, 5) == SFQ_A_HEAD);
	CHECK(arrive(&q, &m, 2, 5) == SFQ_A_HOLD && q.r.held == 1 && m.head == 1 && m.hold == 2);
	CHECK(arrive(&q, &m, 3, 5) == SFQ_A_REPL_HOLD && q.r.fifo_full == 1 && q.r.replaced == 1 && m.hold == 3);
	tick(&q, &m, true, &shown); CHECK(shown == 1 && m.head == 3 && q.standing == 1);       /* head shown first, hold promoted */
	tick(&q, &m, true, &shown); CHECK(shown == 3 && m.head == 0 && q.standing == 0);
	CHECK(q.r.presents == 2 && q.r.replaced == 1);
	/* the hold's jobs follow it into the head: drawn is counted for the swap that is shown */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = SFQ_UNCAPPED;
	arrive(&q, &m, 1, 1); arrive(&q, &m, 2, 9);
	tick(&q, &m, true, &shown); CHECK(shown == 1 && q.r.drawn == 0);
	tick(&q, &m, true, &shown); CHECK(shown == 2 && q.r.drawn == 1);
	/* uncapped never catches up, however long it stands */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = SFQ_UNCAPPED;
	arrive(&q, &m, 1, 5);
	for (int i = 2; i < 300; i++) { arrive(&q, &m, i, 5); tick(&q, &m, true, &shown); CHECK(shown == i - 1); }
	CHECK(q.r.catchup == 0 && q.standing == 298);
	/* ---- fifo 1: one standing tick, then the head is dropped for the hold when the hold is complete */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = 1;
	arrive(&q, &m, 1, 5); arrive(&q, &m, 2, 5);
	tick(&q, &m, true, &shown); CHECK(shown == 1 && m.head == 2 && q.standing == 1);       /* standing 1 */
	arrive(&q, &m, 3, 5);                                                                   /* a new hold behind the promoted head */
	tick(&q, &m, false, &shown); CHECK(shown == 2 && q.r.catchup == 0 && q.standing == 2);  /* hold not ready: no catchup, head shown, hold promoted */
	arrive(&q, &m, 4, 5);
	tick(&q, &m, true, &shown); CHECK(shown == 4 && q.r.catchup == 1 && m.head == 0 && q.standing == 0);   /* 3 dropped, 4 shown */
	/* catchup alone: the hold becomes the head, standing restarts */
	memset(&q, 0, sizeof q); q.fifo = 2; q.head_valid = q.hold_valid = true; q.head_jobs = 1; q.hold_jobs = 7; q.standing = 2;
	CHECK(sfq_catchup(&q, true) && q.standing == 0 && !q.hold_valid && q.head_valid && q.head_jobs == 7 && q.r.catchup == 1 && q.r.replaced == 1);
	q.standing = 9; q.hold_valid = true; CHECK(!sfq_catchup(&q, false) && q.hold_valid && q.standing == 9);
	/* catchup needs a hold: nothing to drop to */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = 1; q.standing = 5;
	arrive(&q, &m, 1, 5);
	tick(&q, &m, true, &shown); CHECK(shown == 1 && q.r.catchup == 0);
	/* ---- drawn = MORE than 4 jobs; unknown is not drawn; a redraw (no swap behind it) is not drawn */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m);
	uint32_t js[] = {0, 1, 4, 5, 6, SFQ_JOBS_UNK, 100};
	for (unsigned i = 0; i < sizeof js / sizeof js[0]; i++) { arrive(&q, &m, (int)i + 1, js[i]); tick(&q, &m, true, &shown); }
	CHECK(q.r.presents == 7 && q.r.drawn == 3 && q.r.kicks == 0);
	CHECK(sfq_take(&q, false) == false && q.r.presents == 8 && q.r.drawn == 3);              /* redraw: head not valid */
	/* kicks counted only when by_kick */
	memset(&q, 0, sizeof q); q.head_valid = true; sfq_take(&q, true); CHECK(q.r.kicks == 1 && q.r.presents == 1);
	/* ---- row take: copies and clears the second, keeps the queue state */
	memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = 3;
	arrive(&q, &m, 1, 9); arrive(&q, &m, 2, 9); arrive(&q, &m, 3, 9); tick(&q, &m, true, &shown);
	struct sfq_row row; sfq_row_take(&q, &row);
	CHECK(row.presents == 1 && row.drawn == 1 && row.held == 1 && row.replaced == 1 && row.fifo_full == 1 && row.standing == 1 && sfq_row_nonzero(&row));
	struct sfq_row row2; sfq_row_take(&q, &row2);
	CHECK(row2.presents == 0 && row2.held == 0 && row2.replaced == 0 && !sfq_row_nonzero(&row2) && row2.standing == 1 && q.hold_valid == false && q.head_valid);
	memset(&row, 0, sizeof row); CHECK(!sfq_row_nonzero(&row));
	row.kicks = 1; CHECK(sfq_row_nonzero(&row)); row.kicks = 0; row.presents = 1; CHECK(sfq_row_nonzero(&row)); row.presents = 0; row.held = 1; CHECK(sfq_row_nonzero(&row)); row.held = 0; row.replaced = 1; CHECK(sfq_row_nonzero(&row)); row.replaced = 0; row.fifo_full = 1; CHECK(sfq_row_nonzero(&row)); row.fifo_full = 0;
	row.k_stale = 1; CHECK(sfq_row_nonzero(&row));
	row.k_stale = 0; row.k_notdone = 1; CHECK(sfq_row_nonzero(&row));
	row.k_notdone = 0; row.k_armed = 1; CHECK(sfq_row_nonzero(&row));
	row.k_armed = 0; row.catchup = 1; CHECK(sfq_row_nonzero(&row));
	/* ---- random run, every cap: swaps = presents + replaced + queued; shown ids strictly increase; the queue never holds more than 2 */
	for (uint32_t pass = 0; pass < 2; pass++)
	for (uint32_t cap = 0; cap <= 255; cap += (cap < 5 ? 1 : 50)) {
		memset(&q, 0, sizeof q); memset(&m, 0, sizeof m); q.fifo = cap;
		uint32_t seed = 12345 + cap, id = 0, last = 0, tot_pres = 0, tot_repl = 0;
		for (int step = 0; step < 20000; step++) {
			seed = seed * 1103515245u + 12345u;
			unsigned op = (seed >> 16) %% 5;
			if (op < 2) { a = arrive(&q, &m, (int)++id, (seed >> 8) & 7); (void)a; }
			else { tick(&q, &m, pass ? true : ((seed >> 12) & 1), &shown); if (shown) { CHECK((uint32_t)shown > last); last = (uint32_t)shown; } }
			CHECK(q.head_valid == (m.head != 0) && q.hold_valid == (m.hold != 0));
			CHECK(!(m.hold != 0 && m.head == 0));
			CHECK(m.swaps == q.r.presents + q.r.replaced + queued(&m));
			CHECK(cap != 0 || q.r.held == 0);
			CHECK(!pass || cap == 0 || cap == SFQ_UNCAPPED || q.standing <= cap);   /* with a ready hold the cap holds */
			if (step %% 997 == 0) { tot_pres += q.r.presents; tot_repl += q.r.replaced; sfq_row_take(&q, &row); m.swaps -= row.presents + row.replaced; CHECK(m.swaps == queued(&m)); }
		}
		(void)tot_pres; (void)tot_repl;
	}
	/* ---- Mali kick protocol */
	CHECK(sweet_mali_kick_register(NULL) == EINVAL);
	sm.up = 1; sm.dead = 0; CHECK(sweet_mali_kick_arm(5) == -1);   /* up, no hook registered */
	CHECK(sweet_mali_kick_register(hook) == 0);
	sm.up = 1; sm.dead = 0; sm.seq_done = 3;
	sm_kick_tick(); CHECK(nfire == 0);                                      /* nothing armed */
	CHECK(sweet_mali_kick_arm(5) == 0);                                   /* not done: armed */
	sm_kick_tick(); CHECK(nfire == 0);                                      /* still not done */
	sm.seq_done = 4; sm_kick_tick(); CHECK(nfire == 0);
	sm.seq_done = 5; sm_kick_tick(); CHECK(nfire == 1);                    /* fires once */
	sm_kick_tick(); sm.seq_done = 9; sm_kick_tick(); CHECK(nfire == 1);    /* not twice */
	CHECK(sweet_mali_kick_arm(9) == 1);                                   /* already done: caller acts, hook does not */
	sm_kick_tick(); CHECK(nfire == 1);
	CHECK(sweet_mali_kick_arm(0) == 1);                                   /* no gate seqno: nothing to wait for */
	CHECK(sweet_mali_kick_arm(10) == 0); sm.seq_done = 11; sm_kick_tick(); CHECK(nfire == 2);   /* re-arm after a fire */
	CHECK(sweet_mali_kick_arm(20) == 0); CHECK(sweet_mali_kick_arm(25) == 0); sm.seq_done = 22; sm_kick_tick(); CHECK(nfire == 2);   /* a newer arm replaces the older */
	sm.seq_done = 25; sm_kick_tick(); CHECK(nfire == 3);
	sm.dead = 1; CHECK(sweet_mali_kick_arm(30) == -1);
	sm.dead = 0; sm.up = 0; CHECK(sweet_mali_kick_arm(30) == -1);
	puts("ok");
	return 0;
}
'''

MALI_STUB = r'''
static struct { int up, dead; uint64_t seq_done; } sm;
static int nfire;
static void hook(void) { nfire++; }
'''


def body(text):
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("--- a/"):
            on = True
        if on and l[:1] in ("+", " ") and not l.startswith("+++"):
            out.append(l[1:])
    return "\n".join(out)


def extract(src):
    m = re.search(r"/\* SFQ-PURE-BEGIN \*/\n(.*?)/\* SFQ-PURE-END \*/", src, re.S)
    k = re.search(r"(static void \(\*volatile sm_kick_fn\)\(void\);.*?\nsm_kick_tick\(void\)\n\{.*?\n\}\n)", src, re.S)
    if not m or not k:
        raise SystemExit("RESULT: FAIL 0368-fifo: pure block markers or the Mali kick block not found in the patch")
    return m.group(1), k.group(1)


def run(pure, kick):
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "t.c")
        exe = os.path.join(d, "t")
        with open(c, "w") as f:
            f.write(HARNESS % (pure, MALI_STUB + kick))
        r = subprocess.run(["cc", "-Wall", "-Werror", "-Wno-unused-function", "-o", exe, c], capture_output=True, text=True)
        if r.returncode:
            return 2, r.stderr
        r = subprocess.run([exe], capture_output=True, text=True)
        return r.returncode, r.stderr + r.stdout


TEXT = [
    ("fifo boot-arg range 1..255, else GAP", r"PE_parse_boot_argn\(\"sweetfb_fifo\", &fv, sizeof\(fv\)\) && fv != 0\) \{\s*if \(fv >= 1 && fv <= \(int\)SFQ_UNCAPPED\) \{\s*gSweetFbFifo = fv;.*?\} else \{\s*IOLog\(\"SWEET FB: GAP sweetfb_fifo=%d is not 0..255"),
    ("gatekick needs the gate", r"GAP sweetfb_gatekick=1 needs sweetfb_gate=1"),
    ("gatekick needs 60 Hz", r"GAP sweetfb_gatekick=1 needs sweetfb_hz >= 60"),
    ("gatekick value GAP", r"GAP sweetfb_gatekick=%d is not 0 or 1"),
    ("kick event source on the fb workloop", r"fKick = IOTimerEventSource::timerEventSource\(this, &AppleCLCD::kickFired\);\s*if \(fKick == NULL \|\| fWorkLoop->addEventSource\(fKick\) != kIOReturnSuccess\)"),
    ("row on with copytime, drawn or a lever", r"gSweetFbRow = \(dv != 0 \|\| cv != 0 \|\| gSweetFbFifo != 0 \|\| gSweetFbKick != 0\) \? 1 : 0;"),
    ("swap(): references taken only with the fifo", r"if \(gSweetFbFifo != 0\) \{\s*uint32_t qmask = get32\(rec, 0x104\);.*?nsurf\[i\] = \(\(qmask & \(1u << i\)\) && qid != 0\) \? sweet_surface_copy\(qid\) : NULL;"),
    ("swap(): jobs = seqno delta to the previous swap", r"uint32_t qjobs = \(qok && fJobSeqOk && qsq >= fJobSeqPrev\) \? \(uint32_t\)\(qsq - fJobSeqPrev\) : SFQ_JOBS_UNK;"),
    ("swap(): arrive decides the slot under fLock", r"IOLockLock\(fLock\);\s*int qact = SFQ_A_HEAD;\s*if \(gSweetFbRow\) \{.*?qact = sfq_arrive\(&fQ, qjobs\);"),
    ("swap(): hold slot filled, head untouched", r"if \(qhold\) \{[^\n]*\n\s*memcpy\(fHold, rec, SWEET_FB_SWAP_REC_SIZE\);\s*fHoldSeq = \(qok && gSweetFbGate\) \? qsq : 0;"),
    ("swap(): legacy head write only when not held", r"if \(!qhold\) \{\s*memcpy\(fPending, rec, SWEET_FB_SWAP_REC_SIZE\);\s*fDirty = true;\s*\}\s*if \(gSweetFbGate && !qhold\) \{"),
    ("swap(): replaced references released after the unlock", r"IOLockUnlock\(fLock\);\s*for \(uint32_t i = 0; i < SWEET_FB_LAYERS; i\+\+\) \{[^\n]*\n\s*OSSafeReleaseNULL\(osurf\[i\]\);"),
    ("tick: catchup before the flush decision", r"sfq_catchup\(&fb->fQ, hold_ready\)\) \{\s*fb->promoteHold\(rsurf\);.*?bool flush = fb->fDirty"),
    ("tick: hold readiness uses the Mali watermark, fail open", r"bool hold_ready = !gSweetFbGate \|\| fb->fHoldSeq == 0 \|\| sweet_mali_seq_peek\(&hq, &hd\) != 0 \|\| hd >= fb->fHoldSeq;"),
    ("tick: a deferral remembers the seqno and arms the kick", r"flush = false;\s*if \(gSweetFbKick\) \{\s*kick_seq = fb->fSwapSeq;\s*fb->fDeferred = true;"),
    ("tick: queue bookkeeping after the old inline take", r"if \(gSweetFbRow\) \{\s*fb->takeQueue\(tsurf, false\);"),
    ("tick: references handed to present, then released", r"memcpy\(fb->fPresentSurf, tsurf, sizeof\(tsurf\)\);.*?fb->present\(rec\);.*?fb->fPresentSurf\[i\] = NULL;\s*OSSafeReleaseNULL\(tsurf\[i\]\);"),
    ("tick: dropped head's references released", r"OSSafeReleaseNULL\(rsurf\[i\]\);"),
    ("tick: arm after the unlock, -1 is a GAP and a counter", r"int ka = sweet_mali_kick_arm\(kick_seq\);.*?if \(ka < 0\) \{\s*sweet_fb_kick_unavail\+\+;.*?GAP gatekick: Mali refused the arm"),
    ("tick: already complete = fire the kick ourselves", r"\} else if \(ka == 1\) \{\s*fb->fKick->setTimeoutUS\(0\);"),
    ("row text P D H R K F", r"SWEET FB: presents %u drawn %u held %u replaced %u gatekick %u fifo_full %u\\n\", row\.presents, row\.drawn, row\.held, row\.replaced, row\.kicks, row\.fifo_full"),
    ("row once per second, not at idle", r"rn - fb->fRowT0 >= 1000000000ull.*?row_log = sfq_row_nonzero\(&row\) \|\| row_unavail != 0;"),
    ("takeHead = old copy lines + takeQueue", r"AppleCLCD::takeHead\(.*?\{\s*memcpy\(rec, fPending, SWEET_FB_SWAP_REC_SIZE\);\s*fDirty = false;\s*fGateTicks = 0;\s*fLastPresentCount = count;.*?takeQueue\(surf, by_kick\);"),
    ("takeQueue moves the references and promotes the hold", r"AppleCLCD::takeQueue\(.*?surf\[i\] = fPendSurf\[i\];\s*fPendSurf\[i\] = NULL;.*?if \(sfq_take\(&fQ, by_kick\)\) \{.*?promoteHold\(none\);"),
    ("promoteHold moves record, seqno and references", r"AppleCLCD::promoteHold\(.*?memcpy\(fPending, fHold, SWEET_FB_SWAP_REC_SIZE\);\s*fSwapSeq = fHoldSeq;.*?old_surf\[i\] = fPendSurf\[i\];\s*fPendSurf\[i\] = fHoldSurf\[i\];\s*fHoldSurf\[i\] = NULL;.*?fDirty = true;"),
    ("surfFor: held reference first, table second", r"AppleCLCD::surfFor\(.*?h->id == id\) \{\s*h->retain\(\);\s*return h;\s*\}\s*\}\s*return sweet_surface_copy\(id\);"),
    ("present() copy lookup uses surfFor", r"SweetSurface \*s = surfFor\(id\);"),
    ("present() skip-seed lookup uses surfFor", r"SweetSurface \*ls = surfFor\(lid\);"),
    ("kick: one copy per tick interval and only a deferred head", r"if \(fb->fDirty && fb->fDeferred && !fb->fPowerOff && !sweet_qr_hold && fb->fLastPresentCount < count\) \{"),
    ("kick: complete check, notdone counted and re-armed", r"bool done = fb->fSwapSeq == 0 \|\| sweet_mali_seq_peek\(&sq, &dn\) != 0 \|\| dn >= fb->fSwapSeq;\s*if \(!done\) \{\s*rearm = fb->fSwapSeq;\s*fb->fQ\.r\.k_notdone\+\+;"),
    ("kick: copies through takeHead with by_kick", r"fb->takeHead\(count, rec, tsurf, true\);"),
    ("kick: stale counted", r"fb->fQ\.r\.k_stale\+\+;   /\* a tick already copied this interval, or nothing is waiting \*/"),
    ("kick callback only enters the timer", r"sweet_fb_kick_cb\(void\)[^\n]*\n\{\s*IOTimerEventSource \*t = gSweetFbKickTimer;\s*if \(t != NULL\) \{\s*t->setTimeoutUS\(0\);"),
    ("kick registered with Mali", r"sweet_mali_kick_register\(sweet_fb_kick_cb\) != 0"),
    ("sm_thread calls the hook after the unlock", r"lck_mtx_unlock\(&sm_lock\);\s*sm_kick_tick\(\);\s*/\* patch 0368 \*/"),
    ("members initialised in start()", r"memset\(&fQ, 0, sizeof\(fQ\)\);\s*memset\(fPendSurf, 0, sizeof\(fPendSurf\)\);\s*memset\(fHoldSurf, 0, sizeof\(fHoldSurf\)\);\s*memset\(fPresentSurf, 0, sizeof\(fPresentSurf\)\);"),
]
NEG = [
    ("SWAPT row not touched: no SWAPT IOLog added", r"IOLog\(\"SWEET SWAPT"),
    ("panel copy row not touched", r"IOLog\(\"SWEET FB: panel copy"),
    ("no existing line removed except the replaced lookups and the lines swap() reshapes", r"(?m)^-(?!\s*(SweetSurface \*(s|ls) = sweet_surface_copy\((id|lid)\);|memcpy\(fPending, rec, SWEET_FB_SWAP_REC_SIZE\);|fDirty = true;|if \(gSweetFbGate\) \{   /\* patch 0343: the GPU work))[^-]"),
    ("no register access in the added lines of the framebuffer", r"(?m)^\+[^\n]*\b(wr|rr|writel|OSWriteLE32)\("),
    ("no loop with a sleep or delay in the kick path", r"(?m)^\+[^\n]*\b(IODelay|delay_for_interval|thread_block|IOSleep)\("),
]


def main():
    text = open(PATCH).read()
    src = body(text)
    pure, kick = extract(src)
    rc, out = run(pure, kick)
    if rc:
        print(out)
        print("RESULT: FAIL 0368-fifo: harness")
        return 1
    cases = 1
    added = "\n".join(l for l in text.splitlines() if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---")))
    for name, rx in TEXT:
        if not re.search(rx, src, re.S):
            print("RESULT: FAIL 0368-fifo: patch text check failed: %s" % name)
            return 1
        cases += 1
    for name, rx in NEG:
        if re.search(rx, added):
            print("RESULT: FAIL 0368-fifo: forbidden text in the patch: %s" % name)
            return 1
        cases += 1
    pm = [
        ("off: latest wins removed", pure.replace("if (q->fifo == 0) {\n\t\tq->head_jobs = jobs;\n\t\tq->r.replaced++;\n\t\treturn SFQ_A_REPL_HEAD;\n\t}", "")),
        ("hold not counted", pure.replace("q->r.held++;", "")),
        ("hold replace not counted replaced", pure.replace("q->hold_jobs = jobs;\n\tq->r.replaced++;\n\tq->r.fifo_full++;", "q->hold_jobs = jobs;\n\tq->r.fifo_full++;")),
        ("fifo_full not counted", pure.replace("q->r.fifo_full++;", "")),
        ("third swap replaces the head", pure.replace("q->hold_jobs = jobs;\n\tq->r.replaced++;\n\tq->r.fifo_full++;\n\treturn SFQ_A_REPL_HOLD;", "q->head_jobs = jobs;\n\tq->r.replaced++;\n\tq->r.fifo_full++;\n\treturn SFQ_A_REPL_HEAD;")),
        ("uncapped catches up", pure.replace("q->fifo == SFQ_UNCAPPED || ", "")),
        ("catchup without a ready hold", pure.replace("|| !hold_ready ||", "||")),
        ("catchup too early", pure.replace("q->standing < q->fifo", "q->standing < q->fifo - 1")),
        ("catchup not counted replaced", pure.replace("q->r.catchup++;\n\tq->r.replaced++;", "q->r.catchup++;")),
        ("catchup keeps the hold", pure.replace("q->hold_valid = false;\n\tq->standing = 0;", "q->standing = 0;")),
        ("catchup keeps standing", pure.replace("q->hold_valid = false;\n\tq->standing = 0;", "q->hold_valid = false;")),
        ("drawn at 4 jobs", pure.replace("q->head_jobs > SFQ_DRAWN_JOBS", "q->head_jobs >= SFQ_DRAWN_JOBS")),
        ("drawn at 5 jobs only", pure.replace("q->head_jobs > SFQ_DRAWN_JOBS", "q->head_jobs > SFQ_DRAWN_JOBS + 1")),
        ("unknown jobs drawn", pure.replace("q->head_jobs != SFQ_JOBS_UNK && ", "")),
        ("redraw drawn", pure.replace("q->head_valid && q->head_jobs != SFQ_JOBS_UNK", "q->head_jobs != SFQ_JOBS_UNK")),
        ("present not counted", pure.replace("q->r.presents++;\n\tif (by_kick)", "if (by_kick)")),
        ("kick not counted", pure.replace("if (by_kick) {\n\t\tq->r.kicks++;\n\t}", "")),
        ("kick always counted", pure.replace("if (by_kick) {\n\t\tq->r.kicks++;\n\t}", "q->r.kicks++;")),
        ("hold not promoted", pure.replace("q->head_valid = true;\n\t\tq->head_jobs = q->hold_jobs;\n\t\tq->hold_valid = false;\n\t\tq->standing++;\n\t\treturn true;", "q->standing++;\n\t\treturn false;")),
        ("standing not counted", pure.replace("q->standing++;", "")),
        ("standing not reset on empty", pure.replace("q->standing = 0;\n\treturn false;", "return false;")),
        ("row keeps the counters", pure.replace("memset(&q->r, 0, sizeof(q->r));", "")),
        ("row loses standing", pure.replace("o->standing = q->standing;", "")),
        ("row nonzero ignores kicks", pure.replace(" | o->kicks", "")),
        ("row nonzero ignores notdone", pure.replace(" | o->k_notdone", "")),
        ("jobs of the hold lost on promote", pure.replace("q->head_jobs = q->hold_jobs;\n\t\tq->hold_valid = false;\n\t\tq->standing++;", "q->hold_valid = false;\n\t\tq->standing++;")),
    ]
    km = [
        ("register accepts NULL", kick.replace("if (fn == NULL) {\n\t\treturn EINVAL;\n\t}", "")),
        ("arm without hook succeeds", kick.replace(" || __atomic_load_n(&sm_kick_fn, __ATOMIC_ACQUIRE) == NULL", "")),
        ("arm on a dead Mali succeeds", kick.replace("!sm.up || sm.dead ||", "!sm.up ||")),
        ("arm of an already done seqno not disarmed", kick.replace("if (__atomic_compare_exchange_n(&sm_kick_seq, &exp, 0, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {\n\t\t\treturn 1;\n\t\t}", "return 1;")),
        ("arm never reports done", kick.replace("\t\t\treturn 1;\n\t\t}\n\t}\n\treturn 0;", "\t\t\treturn 0;\n\t\t}\n\t}\n\treturn 0;")),
        ("hook fires early", kick.replace("< ks) {", "< ks - 1) {")),
        ("hook fires twice", kick.replace("if (__atomic_compare_exchange_n(&sm_kick_seq, &ks, 0, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {\n\t\tfn();\n\t}", "fn();")),
        ("hook ignores the armed flag", kick.replace("if (ks == 0 || ", "if (")),
        ("hook never disarms", kick.replace("if (__atomic_compare_exchange_n(&sm_kick_seq, &ks, 0, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {\n\t\tfn();\n\t}", "fn();\n\t(void)ks;")),
    ]
    dead = 0
    for name, m in pm:
        if m == pure:
            print("RESULT: FAIL 0368-fifo: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m, kick)
        if rc == 0:
            print("RESULT: FAIL 0368-fifo: mutant %s survived" % name)
            return 1
        dead += 1
    for name, m in km:
        if m == kick:
            print("RESULT: FAIL 0368-fifo: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(pure, m)
        if rc == 0:
            print("RESULT: FAIL 0368-fifo: mutant %s survived" % name)
            return 1
        dead += 1
    for name, rx in TEXT:
        m = re.sub(rx, "", src, count=1, flags=re.S)
        if m == src or re.search(rx, m, re.S):
            print("RESULT: FAIL 0368-fifo: text mutant %s did not remove the hook" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0368-fifo cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
