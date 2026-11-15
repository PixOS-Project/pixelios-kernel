/*
 * pixnav: closed-loop stress nav for the ALL-ON GPU oracle (T-31, coordinator 2026-11-11)
 * ==========================================================================================
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 PixOS-Project contributors.
 *
 * Usage: pixnav [-r reps] [-s seed] [-R refs.bin] [-c] [-H scenario] [-F panel] [-b "/abs/prog args"]... [-S script] [-U dumpfile] [-X simcmd]
 *   -S script steps (';'-separated): I:url (intent), G:x0,y0,x1,y1,ms (gesture), L (look), W:state[|state...],timeout_ms (look until the classified
 *   state is one of the listed reference states, T-28 run3: gate an I: step on SpringBoard being back after a restart; a timeout or an unknown
 *   state name prints PIXNAV GAP and stops the script, exit 1).
 *   -c calm (no jitter/bursts), -H host replay, -F classify one panel file, -b start a background load before the nav (pixperf Wi-Fi traffic,
 *   bt-mgrd scan/reconnect: the ALL-ON stress rule wants Wi-Fi + BT busy DURING the nav); its stdout/stderr go to /dev/null so the kernel's
 *   sweetnull mirror (boot-arg, by process name) puts its lines on the USB log; pids + exit states are logged at the end (never killed).
 *
 * Why: the kernel nav (patches 0243/0306) taps fixed coordinates on timers. Under stress it cannot know where the UI is: allon-stress1 lost
 * reps 1-2 (a tap eaten by the moving home page, then blind taps opened Weather), allon-stress2 lost every rep (a recovery tap landed on the
 * Focus row). pixnav looks before every step:
 *   1. wait until the screen settled (kern.sweetfbcount distinct frames flat SETTLE_MS, cap SETTLE_CAP_MS);
 *   2. read the panel (kern.sweetpanel, patch 0276) and classify it (two-stage nearest reference = tools/nav-classify, same refs file);
 *   3. only if the screen is the action's start state: begin marker, the gesture (kern.sweetgesture, patch 0312) with human-like jitter,
 *      inputend marker, then wait + classify the result: end marker when it is the expected state;
 *   4. a mismatch logs the classified state and RECOVERS BY INTENT (URL open via SpringBoardServices, the edge back-swipe, the home swipe:
 *      all position-agnostic), never by a coordinate on an unconfirmed screen; at most MAX_RECOVER per action, then a GAP and the
 *      action is skipped (unmeasured: the scorers fail the run).
 * lane-nav 2026-11-12 (jobs 47f9 / 8a63 / f428: 30-32 x 'SWEET GAP: NAV' on boots whose fps met the bar):
 *   - refs v3: per-state masks + band rects (tools/nav-classify GEOM): a home widget that changes content no longer makes homeA 'unknown';
 *   - after an action that changes the screen, look until the post state shows (POST_WAIT_MS): a look 0.37 s after a tap read the old screen;
 *   - from 'unknown' only the bottom home swipe or an intent (the edge back-swipe on an unrecognised home page opened the Today View); from
 *     'lock' the intent (a bottom swipe does not unlock after a respring); a confirmed location alert (alert-loc, T-27) is dismissed by Cancel;
 *   - no blind START swipe; taps only on cells the start state's references compare; -U FILE keeps unknown panels for new references;
 *   - host: -X CMD = closed-loop simulator (tools/pixnav-sim), sleeps advance a virtual clock (tools/test_pixnav_sim.py).
 * Markers are the kernel nav's ('SWEET NAV: ACTION <name> begin|inputend|end rep R now NS', kernel clock = mach_absolute_time in ns), so
 * tools/gpu-fps-bench and tools/gpu-nav-score score it unchanged. Every recovery prints 'SWEET NAV: RECOVER ...' (counted by allon-score).
 * Rule 7: every failed call prints a 'PIXNAV GAP' line; exit 1 when any action was skipped.
 *
 * Host build (-DPIXNAV_HOST, tests + replay): -H FILE gives one panel file per classify() call (one path per line, '#' = comment);
 * gestures, intents and sleeps are printed, the frame counter is always settled.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
extern char **environ;
#include <stdarg.h>
/* Every line goes to stdout (the resident job's steps.log) AND to /dev/null: with `pixnav` in the boot-arg sweetnull list the kernel mirrors a
 * named process's /dev/null writes onto the USB log, where tools/gpu-fps-bench aligns the SWEET NAV markers with the SWEET FPS frame lines. */
static int devnull = -1;
static int say(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int n = vprintf(fmt, ap);
	va_end(ap);
	if (devnull >= 0) {
		va_start(ap, fmt);
		vdprintf(devnull, fmt, ap);
		va_end(ap);
	}
	return n;
}
#define printf say
#ifndef PIXNAV_HOST
#include <dlfcn.h>
#include <mach/mach_time.h>
#include <sys/sysctl.h>
#endif

#define SETTLE_MS 300
#define SETTLE_CAP_MS 4000
#define MAX_RECOVER 6   /* lock -> intent -> Clock -> home swipe -> homeA -> page swipe: up to 4 legitimate steps, + 2 for an eaten input */
#define POST_WAIT_MS 4000   /* 8a63 [obs]: Settings drawn ~2.3 s after the settings-open inputend; the old single look 0.37 s after it read homeA = MISS */
#define GW 24
#define GH 52
#define BW 135
#define BH 18
#define BAND_MAX 5.0
#define COARSE_MAX 40.0   /* known screens <= 25.3 (5 capture runs, nav-classify eval); farther = not a reference screen at all */
#define NSTATE_MAX 16

/* ---------------------------------------------------------------- classifier (exact port of tools/nav-classify) */
struct ref { uint32_t st; uint8_t g[GW * GH]; uint8_t b[BW * BH]; };
static struct ref *refs;
static uint32_t nrefs, nstates;
static char stname[NSTATE_MAX][17];
static float stceil[NSTATE_MAX];   /* per-state coarse ceiling (tools/nav-classify ceilings()) */
static uint8_t stmask[NSTATE_MAX][GW * GH];   /* refs v3: 1 = the thumbnail cell counts for that state (homeA widget row, spotlight suggestions off) */
static float stband[NSTATE_MAX][4];   /* refs v3: the state's stage-2 rect (x0, y0, x1, y1 panel fractions) */

static int load_refs(const char *path)
{
	FILE *f = fopen(path, "rb");
	uint8_t hdr[16];
	if (!f || fread(hdr, 1, 16, f) != 16 || memcmp(hdr, "NAVR", 4) != 0) {
		printf("PIXNAV GAP: refs %s unreadable or not NAVR\n", path);
		if (f) fclose(f);
		return -1;
	}
	uint32_t ver, n, ns;
	memcpy(&ver, hdr + 4, 4); memcpy(&n, hdr + 8, 4); memcpy(&ns, hdr + 12, 4);
	if ((ver != 2 && ver != 3) || n == 0 || n > 4096 || ns == 0 || ns > NSTATE_MAX) {
		printf("PIXNAV GAP: refs %s version %u n %u states %u refused\n", path, ver, n, ns);
		fclose(f);
		return -1;
	}
	for (uint32_t i = 0; i < ns; i++) {
		if (fread(stname[i], 1, 16, f) != 16) { printf("PIXNAV GAP: refs %s short (names)\n", path); fclose(f); return -1; }
		stname[i][16] = 0;
	}
	if (fread(stceil, sizeof(float), ns, f) != ns) { printf("PIXNAV GAP: refs %s short (ceilings)\n", path); fclose(f); return -1; }
	for (uint32_t i = 0; i < ns; i++) {
		if (ver == 3) {
			if (fread(stmask[i], 1, GW * GH, f) != GW * GH || fread(stband[i], sizeof(float), 4, f) != 4) {
				printf("PIXNAV GAP: refs %s short (v3 geometry)\n", path); fclose(f); return -1;
			}
		} else {   /* v2 = before the masks (47f9: the homeA widget row made the page 'unknown'): whole thumbnail, nav-bar band */
			memset(stmask[i], 1, GW * GH);
			stband[i][0] = .25f; stband[i][1] = .045f; stband[i][2] = .75f; stband[i][3] = .075f;
		}
	}
	if (ver == 2) printf("PIXNAV GAP: refs %s is v2 (no per-state masks: a changed home widget reads as unknown, job 47f9); rebuild with tools/nav-classify refs-bin\n", path);
	refs = calloc(n, sizeof *refs);
	if (!refs) { printf("PIXNAV GAP: no memory for %u refs\n", n); fclose(f); return -1; }
	for (uint32_t i = 0; i < n; i++) {
		if (fread(&refs[i].st, 4, 1, f) != 1 || fread(refs[i].g, 1, sizeof refs[i].g, f) != sizeof refs[i].g ||
		    fread(refs[i].b, 1, sizeof refs[i].b, f) != sizeof refs[i].b || refs[i].st >= ns) {
			printf("PIXNAV GAP: refs %s short or bad state at %u\n", path, i);
			fclose(f);
			return -1;
		}
	}
	fclose(f);
	nrefs = n; nstates = ns;
	return 0;
}

static int state_id(const char *name)
{
	for (uint32_t i = 0; i < nstates; i++)
		if (strcmp(stname[i], name) == 0) return (int)i;
	return -1;
}

/* PIL Image.resize(BOX) on 8-bit grey, horizontal pass first, 8-bit rounding in between (Pillow Resample.c: box filter support 0.5,
 * a source pixel x counts for output o when -0.5 < (x - center + 0.5) / scale <= 0.5, with center = (o + 0.5) * scale, equal weights) */
static void box_bounds(int in, int out, int o, int *x0, int *x1)
{
	double scale = (double)in / out, center = (o + 0.5) * scale, support = 0.5 * scale;
	int a = (int)(center - support + 0.5), b = (int)(center + support + 0.5);
	if (a < 0) a = 0;
	if (b > in) b = in;
	while (a < b && !((a - center + 0.5) / scale > -0.5)) a++;
	while (b > a && !((b - 1 - center + 0.5) / scale <= 0.5)) b--;
	*x0 = a; *x1 = b;
}
static void resize_box(const uint8_t *src, int sw, int sh, int stride, uint8_t *dst, int dw, int dh)
{
	uint8_t *tmp = malloc((size_t)dw * sh);
	if (!tmp) { memset(dst, 0, (size_t)dw * dh); printf("PIXNAV GAP: no memory for resize\n"); return; }
	for (int y = 0; y < sh; y++)
		for (int o = 0; o < dw; o++) {
			int a, b; box_bounds(sw, dw, o, &a, &b);
			uint32_t s = 0;
			for (int x = a; x < b; x++) s += src[(size_t)y * stride + x];
			tmp[(size_t)y * dw + o] = (uint8_t)((s + (uint32_t)(b - a) / 2) / (uint32_t)(b - a));
		}
	for (int o = 0; o < dh; o++) {
		int a, b; box_bounds(sh, dh, o, &a, &b);
		for (int x = 0; x < dw; x++) {
			uint32_t s = 0;
			for (int y = a; y < b; y++) s += tmp[(size_t)y * dw + x];
			dst[(size_t)o * dw + x] = (uint8_t)((s + (uint32_t)(b - a) / 2) / (uint32_t)(b - a));
		}
	}
	free(tmp);
}

/* panel bytes (32 B 'SPNL' live header or 80 B ring slot, then BGRA rows) -> grey image + the two features */
#define NBANDC 4
struct feat { uint8_t g[GW * GH]; uint8_t *grey; int w, h; int nb; float brect[NBANDC][4]; uint8_t b[NBANDC][BW * BH]; };
static int features(const uint8_t *d, size_t len, struct feat *f)
{
	uint32_t mg, w, h;
	if (len < 32) return -1;
	memcpy(&mg, d, 4); memcpy(&w, d + 4, 4); memcpy(&h, d + 8, 4);
	if ((mg != 0x4c4e5053u && mg != 0x484e5053u) || w == 0 || h == 0 || (size_t)w * h * 4 > len) return -1;
	size_t hdr = len - (size_t)w * h * 4;
	if (hdr != 32 && hdr != 80) return -1;
	const uint8_t *px = d + hdr;
	free(f->grey);
	f->grey = malloc((size_t)w * h);
	if (!f->grey) return -1;
	for (size_t i = 0; i < (size_t)w * h; i++) {   /* PIL 'L': ITU-R 601-2 luma, 16-bit fixed point */
		uint32_t B = px[4 * i], G = px[4 * i + 1], R = px[4 * i + 2];
		f->grey[i] = (uint8_t)((R * 19595u + G * 38470u + B * 7471u + 0x8000u) >> 16);
	}
	f->w = (int)w; f->h = (int)h;
	f->nb = 0;
	resize_box(f->grey, (int)w, (int)h, (int)w, f->g, GW, GH);
	return 0;
}

/* the stage-2 band of rect r (panel fractions; int() truncation as PIL crop in nav-classify band_of), cached per panel */
static const uint8_t *band_for(struct feat *f, const float *r)
{
	for (int i = 0; i < f->nb; i++)
		if (memcmp(f->brect[i], r, sizeof f->brect[i]) == 0) return f->b[i];
	int i = f->nb < NBANDC ? f->nb++ : NBANDC - 1;
	memcpy(f->brect[i], r, sizeof f->brect[i]);
	/* nav-classify: int(w * x) with Python floats (double) of the f32 values written by struct.pack('<4f') -> same doubles here */
	int cx0 = (int)(f->w * (double)r[0]), cy0 = (int)(f->h * (double)r[1]), cx1 = (int)(f->w * (double)r[2]), cy1 = (int)(f->h * (double)r[3]);
	resize_box(f->grey + (size_t)cy0 * f->w + cx0, cx1 - cx0, cy1 - cy0, f->w, f->b[i], BW, BH);
	return f->b[i];
}

static double l1(const uint8_t *a, const uint8_t *b, size_t n)
{
	uint64_t s = 0;
	for (size_t i = 0; i < n; i++) s += a[i] > b[i] ? a[i] - b[i] : b[i] - a[i];
	return (double)s / n;
}

static double l1m(const uint8_t *a, const uint8_t *b, const uint8_t *m, size_t n)
{
	uint64_t s = 0, k = 0;
	for (size_t i = 0; i < n; i++)
		if (m[i]) { s += a[i] > b[i] ? a[i] - b[i] : b[i] - a[i]; k++; }
	return k ? (double)s / k : 1e9;
}

/* -1 = unknown; *coarse = the nearest state. v3 (tools/nav-classify classify): per state the nearest reference under that state's mask; states in
 * order of that distance (ties by name); the first state within its ceiling whose band (its own rect) is within BAND_MAX wins. */
static int classify_feat(struct feat *f, double *d0, double *bd, int *coarse)
{
	double best[NSTATE_MAX];
	int order[NSTATE_MAX], n = 0;
	for (uint32_t s = 0; s < nstates; s++) best[s] = -1;
	for (uint32_t i = 0; i < nrefs; i++) {
		uint32_t s = refs[i].st;
		double d = l1m(f->g, refs[i].g, stmask[s], sizeof f->g);
		if (best[s] < 0 || d < best[s]) best[s] = d;
	}
	for (uint32_t s = 0; s < nstates; s++)
		if (best[s] >= 0) order[n++] = (int)s;
	for (int i = 1; i < n; i++)   /* insertion sort by (distance, name) = Python sorted(key=(d, name)) */
		for (int j = i; j > 0; j--) {
			int a = order[j - 1], b = order[j];
			if (best[a] > best[b] || (best[a] == best[b] && strcmp(stname[a], stname[b]) > 0)) { order[j - 1] = b; order[j] = a; }
			else break;
		}
	*coarse = -1;
	for (int i = 0; i < n; i++) {
		int s = order[i];
		const uint8_t *band = band_for(f, stband[s]);
		double bb = 1e9;
		for (uint32_t k = 0; k < nrefs; k++)
			if ((int)refs[k].st == s) {
				double d = l1(band, refs[k].b, BW * BH);
				if (d < bb) bb = d;
			}
		if (*coarse < 0) { *coarse = s; *d0 = best[s]; *bd = bb; }
		if (best[s] <= stceil[s] && bb <= BAND_MAX) { *coarse = s; *d0 = best[s]; *bd = bb; return s; }
		if (best[s] > COARSE_MAX * 2) break;
	}
	return -1;
}

/* ---------------------------------------------------------------- platform: panel, gestures, counters, intents, clock */
static FILE *scen;   /* host replay */
static size_t panellen;   /* bytes of the last panel read */
static uint8_t *panelbuf;
static size_t panelcap;

#ifdef PIXNAV_HOST
/* host: a virtual clock (sleeps advance it) and, with -X CMD, a closed-loop simulator on a pipe (tools/pixnav-sim): every panel read asks it for
 * the current screen, every gesture / intent is sent to it, so recoveries change what the next look sees (the -H list cannot). Protocol, one
 * line each way: 'P <ms>' -> panel path | 'G <ms> x0 y0 x1 y1 dur' -> 'ok' | 'I <ms> url' -> 'ok' or 'refused'. */
static uint64_t vclock = 1000000000ull;
static FILE *sim_w, *sim_r;
static int sim_ask(char *ans, size_t n, const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	vfprintf(sim_w, fmt, ap);
	va_end(ap);
	fflush(sim_w);
	if (!fgets(ans, (int)n, sim_r)) { printf("PIXNAV GAP: simulator closed the pipe\n"); return -1; }
	ans[strcspn(ans, "\r\n")] = 0;
	return 0;
}
static int sim_start(const char *cmd)
{
	int to[2], from[2];
	if (pipe(to) != 0 || pipe(from) != 0) return -1;
	pid_t pid = fork();
	if (pid < 0) return -1;
	if (pid == 0) {
		dup2(to[0], 0); dup2(from[1], 1);
		close(to[1]); close(from[0]);
		execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
		_exit(127);
	}
	close(to[0]); close(from[1]);
	sim_w = fdopen(to[1], "w"); sim_r = fdopen(from[0], "r");
	return sim_w && sim_r ? 0 : -1;
}
#endif
static uint64_t now_ns(void)
{
#ifdef PIXNAV_HOST
	return vclock += 1000000ull;
#else
	static mach_timebase_info_data_t tb;
	if (!tb.denom) mach_timebase_info(&tb);
	return mach_absolute_time() * tb.numer / tb.denom;
#endif
}

static void sleep_ms(unsigned ms)
{
#ifdef PIXNAV_HOST
	vclock += (uint64_t)ms * 1000000ull;
#else
	usleep(ms * 1000u);
#endif
}

static int read_panel(size_t *len)
{
#ifdef PIXNAV_HOST
	char line[1024];
	if (sim_w) {
		if (sim_ask(line, sizeof line, "P %llu\n", (unsigned long long)(vclock / 1000000ull)) != 0) return -1;
	} else
		for (;;) {
			if (!scen || !fgets(line, sizeof line, scen)) { printf("PIXNAV GAP: replay scenario exhausted\n"); return -1; }
			line[strcspn(line, "\r\n")] = 0;
			if (line[0] && line[0] != '#') break;
		}
	FILE *f = fopen(line, "rb");
	if (!f) { printf("PIXNAV GAP: replay panel %s unreadable\n", line); return -1; }
	fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
	if (n <= 0) { fclose(f); return -1; }
	if ((size_t)n > panelcap) { free(panelbuf); panelbuf = malloc((size_t)n); panelcap = panelbuf ? (size_t)n : 0; }
	if (!panelbuf || fread(panelbuf, 1, (size_t)n, f) != (size_t)n) { fclose(f); printf("PIXNAV GAP: replay panel %s short\n", line); return -1; }
	fclose(f);
	printf("PIXNAV REPLAY panel %s\n", line);
	*len = (size_t)n;
	panellen = (size_t)n;
	return 0;
#else
	size_t n = 0;
	if (sysctlbyname("kern.sweetpanel", NULL, &n, NULL, 0) != 0 || n == 0) { printf("PIXNAV GAP: kern.sweetpanel size: %s\n", strerror(errno)); return -1; }
	if (n > panelcap) { free(panelbuf); panelbuf = malloc(n); panelcap = panelbuf ? n : 0; }
	if (!panelbuf) { printf("PIXNAV GAP: no memory for the panel (%zu B)\n", n); return -1; }
	if (sysctlbyname("kern.sweetpanel", panelbuf, &n, NULL, 0) != 0) { printf("PIXNAV GAP: kern.sweetpanel read: %s\n", strerror(errno)); return -1; }
	*len = n;
	panellen = n;
	return 0;
#endif
}

static int fbcount(uint64_t *swaps, uint64_t *distinct)
{
#ifdef PIXNAV_HOST
	*swaps = *distinct = 7;
	return 0;
#else
	uint64_t v[2];
	size_t n = sizeof v;
	if (sysctlbyname("kern.sweetfbcount", v, &n, NULL, 0) != 0 || n != sizeof v) { printf("PIXNAV GAP: kern.sweetfbcount: %s\n", strerror(errno)); return -1; }
	*swaps = v[0]; *distinct = v[1];
	return 0;
#endif
}

struct sweet_gesture { uint32_t magic, id; int32_t x0, y0, x1, y1; uint32_t ms; };
static uint32_t gest_id;
static int gesture(int x0, int y0, int x1, int y1, unsigned ms)
{
	struct sweet_gesture g = { 0x54534753u, ++gest_id, x0, y0, x1, y1, ms };
#ifdef PIXNAV_HOST
	printf("PIXNAV REPLAY gesture %u %d,%d -> %d,%d ms %u\n", g.id, x0, y0, x1, y1, ms);
	if (sim_w) {
		char ans[64];
		if (sim_ask(ans, sizeof ans, "G %llu %d %d %d %d %u\n", (unsigned long long)(vclock / 1000000ull), x0, y0, x1, y1, ms) != 0) return -1;
	}
	sleep_ms(ms + 40);
	return 0;
#else
	for (int tries = 0; tries < 20; tries++) {
		if (sysctlbyname("kern.sweetgesture", NULL, NULL, &g, sizeof g) == 0) {
			sleep_ms(ms + 40);   /* the gesture runs on the HID work loop for ms (+ one 8 ms step) */
			return 0;
		}
		if (errno != EBUSY) break;
		sleep_ms(50);
	}
	printf("PIXNAV GAP: kern.sweetgesture id %u %d,%d -> %d,%d ms %u: %s\n", g.id, x0, y0, x1, y1, ms, strerror(errno));
	return -1;
#endif
}

static const char *last_intent;   /* the URL of the newest intent (any caller): the switch storm never re-opens it */
static int intent(const char *url)
{
	last_intent = url;
	printf("SWEET NAV: INTENT open %s now %llu\n", url, (unsigned long long)now_ns());
#ifdef PIXNAV_HOST
	if (sim_w) {
		char ans[64];
		if (sim_ask(ans, sizeof ans, "I %llu %s\n", (unsigned long long)(vclock / 1000000ull), url) != 0) return -1;
		if (strcmp(ans, "ok") != 0) { printf("PIXNAV GAP: intent %s refused (rc 0)\n", url); return -1; }
	}
	return 0;
#else
	typedef const void *(*cfurl_fn)(const void *, const unsigned char *, long, unsigned, const void *);
	typedef unsigned char (*open_fn)(const void *, unsigned char);
	static open_fn fn;
	static cfurl_fn mk;
	if (!fn) {
		void *sbs = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices", RTLD_NOW);
		void *cf = dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", RTLD_NOW);
		fn = sbs ? (open_fn)dlsym(sbs, "SBSOpenSensitiveURLAndUnlock") : NULL;
		mk = cf ? (cfurl_fn)dlsym(cf, "CFURLCreateWithBytes") : NULL;
		if (!fn || !mk) { printf("PIXNAV GAP: intent: SpringBoardServices/CoreFoundation symbol missing\n"); return -1; }
	}
	const void *u = mk(NULL, (const unsigned char *)url, (long)strlen(url), 0x08000100u /* kCFStringEncodingUTF8 */, NULL);
	if (!u) { printf("PIXNAV GAP: intent: CFURL for %s not built\n", url); return -1; }
	unsigned char rc = fn(u, 1);
	if (!rc) { printf("PIXNAV GAP: intent %s refused (rc 0)\n", url); return -1; }
	return 0;
#endif
}

/* ---------------------------------------------------------------- stress draws (xorshift32), screen state */
static uint32_t rng = 0x9e3779b9u;
static int calm;
static uint32_t rnd(uint32_t lo, uint32_t hi)
{
	rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
	return hi > lo ? lo + rng % (hi - lo + 1) : lo;
}
static int jit(int r) { return calm ? 0 : (int)rnd(0, (uint32_t)(2 * r)) - r; }
static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

static struct feat cur;
/* -U FILE: keep the panels pixnav could not classify (max MAXDUMP per run) so they become references or test inputs (47f9: 30 GAPs from one
 * unseen home page, and no panel of it existed afterwards). FILE is created empty at the start (a resident --pull of it always works) and gets
 * one record per panel: 'PNVU', u32 length, the panel bytes. Host: tools/nav-classify split-dump FILE DIR. */
#define MAXDUMP 12
static const char *dumpfile;
static int dumpfd = -1, ndump;
static void dump_unknown(double d0, int coarse)
{
	static uint8_t kept[MAXDUMP][GW * GH];
	if (dumpfd < 0 || ndump >= MAXDUMP || !panelbuf || !panellen || panellen > 0xffffffffu) return;
	for (int i = 0; i < ndump; i++)
		if (l1(kept[i], cur.g, GW * GH) < 1.0) return;   /* the same screen again (a sub-page seen on every look): one copy is enough */
	memcpy(kept[ndump], cur.g, GW * GH);
	uint8_t hdr[8] = { 'P', 'N', 'V', 'U' };
	uint32_t n = (uint32_t)panellen;
	memcpy(hdr + 4, &n, 4);
	if (write(dumpfd, hdr, 8) != 8 || write(dumpfd, panelbuf, panellen) != (ssize_t)panellen) {
		printf("PIXNAV GAP: unknown panel not kept in %s (%s): no more dumps\n", dumpfile, strerror(errno));
		close(dumpfd); dumpfd = -1;
		return;
	}
	ndump++;
	printf("SWEET NAV: UNKNOWN kept #%d in %s (coarse %s d %.1f)\n", ndump, dumpfile, coarse >= 0 ? stname[coarse] : "-", d0);
}
static int settle(void)
{
	uint64_t s, d, last = 0, t0 = now_ns(), tflat = t0;
	if (fbcount(&s, &last) != 0) return -1;
	for (;;) {
		sleep_ms(50);
		if (fbcount(&s, &d) != 0) return -1;
		uint64_t t = now_ns();
		if (d != last) { last = d; tflat = t; }
		if ((t - tflat) / 1000000ull >= SETTLE_MS) return 0;
#ifdef PIXNAV_HOST
		sleep_ms(SETTLE_MS - 50);   /* the host counter is always flat: one settle costs SETTLE_MS of virtual time as on the phone */
		return 0;
#endif
		if ((t - t0) / 1000000ull >= SETTLE_CAP_MS) { printf("PIXNAV GAP: screen never settled in %u ms (distinct still moving)\n", SETTLE_CAP_MS); return 0; }
	}
}
static int look(void)   /* settle, read, classify: state id or -1 unknown, -2 error */
{
	size_t len;
	double d0, bd;
	int coarse;
	settle();
	if (read_panel(&len) != 0 || features(panelbuf, len, &cur) != 0) { printf("PIXNAV GAP: panel not classifiable\n"); return -2; }
	int st = classify_feat(&cur, &d0, &bd, &coarse);
	printf("SWEET NAV: STATE %s (coarse %s d %.1f band %.1f) now %llu\n", st >= 0 ? stname[st] : "unknown", coarse >= 0 ? stname[coarse] : "-", d0, bd,
	    (unsigned long long)now_ns());
	if (st == -1) dump_unknown(d0, coarse);
	return st;
}

/* ---------------------------------------------------------------- actions */
enum kind { TAP, DRAG, WAIT };
struct act { const char *name; const char *pre, *post; enum kind k; int x0, y0, x1, y1; unsigned ms; int tj, dj0, dj1; };
/* coordinates: 1080x2400 touch space, the kernel nav table (patches 0243/0250/0254/0255) [read]; taps run only on a confirmed start screen */
static const struct act ACTS[] = {
	{ "home-page-next", "homeA", "homeB", DRAG, 900, 1200, 200, 1200, 250, 0, 40, 80 },
	{ "home-page-prev", "homeB", "homeA", DRAG, 200, 1200, 900, 1200, 250, 0, 40, 80 },
	{ "settings-open", "homeA", "settings", TAP, 676, 1720, 0, 0, 100, 25, 0, 0 },
	{ "general-open", "settings", "general", TAP, 500, 0, 0, 0, 100, 25, 0, 0 },   /* y from the panel (General row) */
	{ "about-open", "general", "about", TAP, 500, 370, 0, 0, 100, 25, 0, 0 },
	{ "about-scroll-up", "about", "about", DRAG, 540, 1800, 540, 700, 350, 0, 40, 80 },
	{ "about-scroll-down", "about", "about", DRAG, 540, 700, 540, 1800, 350, 0, 40, 80 },
	{ "about-back", "about", "general", DRAG, 5, 1200, 700, 1200, 250, 0, 0, 80 },
	{ "general-back", "general", "settings", DRAG, 5, 1200, 700, 1200, 250, 0, 0, 80 },
	{ "settings-scroll-up", "settings", "settings", DRAG, 540, 1800, 540, 700, 350, 0, 40, 80 },
	{ "settings-scroll-down", "settings", "settings", DRAG, 540, 700, 540, 1800, 350, 0, 40, 80 },
	{ "app-home", "settings", "homeA", DRAG, 540, 2398, 540, 1200, 200, 0, 0, 80 },
	{ "app-launch", "homeA", "clock", TAP, 665, 1158, 0, 0, 100, 25, 0, 0 },
	{ "app-idle", "clock", "clock", WAIT, 0, 0, 0, 0, 3000, 0, 0, 0 },
	{ "app-close", "clock", "homeA", DRAG, 540, 2398, 540, 1200, 200, 0, 0, 80 },
	{ "spotlight-open", "homeA", "spotlight", DRAG, 540, 500, 540, 1500, 300, 0, 40, 80 },
	{ "spotlight-close", "spotlight", "homeA", DRAG, 540, 2398, 540, 1200, 250, 0, 0, 80 },
};
#define NACT (sizeof ACTS / sizeof ACTS[0])

static int is(int st, const char *name) { return st >= 0 && strcmp(stname[st], name) == 0; }

/* the URL intent that opens `want`, or a known app from which `want` is one confirmed gesture away (home pages, Spotlight: Clock, then the home
 * swipe from the confirmed Clock). SBSOpenSensitiveURLAndUnlock unlocks a locked phone first (T-28 run4 [obs]). */
static const char *intent_for(const char *want)
{
	if (strcmp(want, "settings") == 0 || strcmp(want, "general") == 0) return "prefs:root=General";
	if (strcmp(want, "about") == 0) return "prefs:root=General&path=About";
	return "clock-worldclock:";
}

/* Alert 'Turn On Location Services to Allow "Weather" ...' (T-27): its Cancel button, panel x 665-821 y 1353-1391 of 1080x2400 [obs allon-stress1
 * allon-bt.raw, blue text pixels]; tapped only when the screen classified as alert-loc (box + title text matched, tools/nav-classify GEOM). */
#define ALERT_CANCEL_X 743
#define ALERT_CANCEL_Y 1372

/* one recovery step towards `want` from the classified `have` (-1 unknown). Rules (lane-nav 2026-11-12, from the pixnav runs):
 *  - alert-loc: tap its Cancel (the only tap a recovery makes, on a confirmed alert);
 *  - lock: the intent (a bottom swipe does not unlock after a respring: T-28 run3, job 8a63 'RECOVER want homeA have lock' x4 [obs]);
 *  - unknown: never a coordinate and never the edge back-swipe (47f9 [obs]: the back-swipe on an unrecognised home page opened the Today View);
 *    odd attempts the bottom home swipe (leaves apps, Spotlight and the Today View: 47f9 3681.6 -> 3683.0 [obs]), even attempts the intent;
 *  - a confirmed screen: the one gesture that leads on (page swipes, the edge back-swipe from General/About, the home swipe from an app). */
static int recover(const char *want, int have, int attempt, unsigned rep)
{
	const char *h = have >= 0 ? stname[have] : "unknown";
	printf("SWEET NAV: RECOVER want %s have %s attempt %d rep %u now %llu\n", want, h, attempt, rep, (unsigned long long)now_ns());
	if (is(have, "alert-loc")) {
		printf("SWEET NAV: DISMISS alert-loc (Cancel) rep %u\n", rep);
		return gesture(ALERT_CANCEL_X, ALERT_CANCEL_Y, ALERT_CANCEL_X, ALERT_CANCEL_Y, 80);
	}
	if (is(have, "lock")) return intent(intent_for(want));
	if (have < 0) return (attempt & 1) ? gesture(540, 2398, 540, 1200, 200) : intent(intent_for(want));
	int home = is(have, "homeA") || is(have, "homeB");
	if (strcmp(want, "homeA") == 0 || strcmp(want, "homeB") == 0) {
		if (is(have, "homeB")) return gesture(200, 1200, 900, 1200, 250);   /* page back to the first page */
		if (is(have, "homeA")) return gesture(900, 1200, 200, 1200, 250);   /* want homeB */
		return gesture(540, 2398, 540, 1200, 200);   /* home swipe from a confirmed app / Spotlight / App Library */
	}
	if (strcmp(want, "settings") == 0 || strcmp(want, "general") == 0) {
		if (is(have, "about") || (strcmp(want, "settings") == 0 && is(have, "general"))) return gesture(5, 1200, 700, 1200, 250);
		return intent("prefs:root=General");   /* settings: the next look sees General and the back-swipe above pops to the root */
	}
	if (strcmp(want, "spotlight") == 0) {
		if (is(have, "homeA")) return gesture(540, 500, 540, 1500, 300);
		if (is(have, "homeB")) return gesture(200, 1200, 900, 1200, 250);
		return gesture(540, 2398, 540, 1200, 200);
	}
	if (strcmp(want, "about") == 0 || strcmp(want, "clock") == 0) return intent(intent_for(want));
	(void)home;
	printf("PIXNAV GAP: no recovery known for want %s\n", want);
	return -1;
}

/* look until the screen is no longer `prev` (or is `want`), at most ms: a launch or a pop animates for up to ~2.3 s under load (8a63 [obs]) and a
 * look right after the input reads the old screen */
static int look_until(int prev, int want, unsigned ms)
{
	uint64_t t0 = now_ns();
	for (;;) {
		int st = look();
		if (st == want || st == -2 || (st != prev && st >= 0)) return st;
		if ((now_ns() - t0) / 1000000ull >= ms) return st;
		sleep_ms(150);
	}
}

#define RECOVER_WAIT_MS 3000
static int reach(const char *want, unsigned rep)   /* 0 = on `want`, -1 = gave up */
{
	int w = state_id(want);
	if (w < 0) { printf("PIXNAV GAP: state %s not in the refs\n", want); return -1; }
	int st = look();
	for (int a = 1; a <= MAX_RECOVER; a++) {
		if (st == w) return 0;
		if (st == -2) break;
		if (recover(want, st, a, rep) != 0 && st >= 0) break;   /* a refused intent on an unknown screen: the next attempt swipes */
		st = look_until(st, w, RECOVER_WAIT_MS);
	}
	if (st == w) return 0;
	printf("SWEET GAP: NAV pixnav could not reach %s (rep %u) after %d recoveries (last %s)\n", want, rep, MAX_RECOVER, st >= 0 ? stname[st] : "unknown");
	return -1;
}

/* a tap lands only on a cell the start state's references compare (stmask): what sits in a masked cell (homeA widget row) is not confirmed */
static int tap_confirmed(int st, int x, int y)
{
	int cx = x * GW / 1080, cy = y * GH / 2400;
	return st >= 0 && cx >= 0 && cx < GW && cy >= 0 && cy < GH && stmask[st][cy * GW + cx];
}

static int general_y(void);
static int run_action(const struct act *a, unsigned rep)
{
	if (reach(a->pre, rep) != 0) return -1;
	int x0 = a->x0, y0 = a->y0, x1 = a->x1, y1 = a->y1;
	if (strcmp(a->name, "general-open") == 0) {
		y0 = general_y();
		if (y0 <= 0) { printf("SWEET GAP: NAV pixnav no General row on the confirmed Settings screen (rep %u)\n", rep); return -1; }
	}
	if (!calm) sleep_ms(rnd(10, 150));   /* think time once the screen is confirmed: impatient */
	printf("SWEET NAV: ACTION %s begin rep %u now %llu\n", a->name, rep, (unsigned long long)now_ns());
	int rc = 0;
	if (a->k == TAP) {
		int x = clampi(x0 + jit(a->tj), 0, 1079), y = clampi(y0 + jit(a->tj), 0, 2399);
		if (!tap_confirmed(state_id(a->pre), x, y)) {
			printf("SWEET GAP: NAV pixnav tap %s at %d,%d is in a masked (unconfirmed) area of %s: not tapped (rep %u)\n", a->name, x, y, a->pre, rep);
			return -1;
		}
		rc = gesture(x, y, x, y, calm ? a->ms : rnd(40, 110));
	} else if (a->k == DRAG) {
		int bursts = (!calm && strstr(a->name, "scroll")) ? (int)rnd(0, 2) : 0;
		for (int b = 0; b <= bursts && rc == 0; b++) {
			int sx = clampi(x0 + (x0 <= 5 ? 0 : jit(a->dj0)), 0, 1079), sy = clampi(y0 + (y0 >= 2390 ? 0 : jit(a->dj0)), 0, 2399);
			rc = gesture(sx, sy, clampi(x1 + jit(a->dj1), 0, 1079), clampi(y1 + jit(a->dj1), 0, 2399), calm ? a->ms : rnd(60, 150));
			if (b < bursts) sleep_ms(rnd(30, 120));   /* the next flick lands while the list still moves */
		}
	} else {
		sleep_ms(a->ms);
	}
	printf("SWEET NAV: ACTION %s inputend rep %u now %llu\n", a->name, rep, (unsigned long long)now_ns());
	int pre = state_id(a->pre), want = state_id(a->post);
	/* closed-loop post check: an action that changes the screen gets up to POST_WAIT_MS to show `post` (a look 0.37 s after the input read the
	 * old screen while the launch had not begun, 8a63 [obs]); one that keeps it (scroll, idle) is looked at once */
	int st = rc != 0 ? -2 : want == pre ? look() : look_until(pre, want, POST_WAIT_MS);
	if (st != want) {
		printf("SWEET NAV: MISS %s rep %u want %s have %s\n", a->name, rep, a->post, st >= 0 ? stname[st] : "unknown");
		printf("SWEET NAV: ACTION %s end rep %u now %llu\n", a->name, rep, (unsigned long long)now_ns());
		return 1;   /* measured but off-path: the next action's reach() recovers */
	}
	printf("SWEET NAV: ACTION %s end rep %u now %llu\n", a->name, rep, (unsigned long long)now_ns());
	return 0;
}

/* General row of the Settings root list on the confirmed Settings screen: the kernel probe of patch 0269 (sweet_fb_probe_general) on the BGRA
 * panel: in the icon column (x 40..215 every 4 px, panel = touch space at full resolution, half for a ring slot) a row is grey (|R-G|,|G-B| < 14,
 * 90 <= R <= 205: the General / Control Center icon) or purple (B > R+50, B > G+50: Focus / Screen Time); the centre of the first grey run of
 * >= 30 rows that starts below a purple row is the General row. 0 = none (the caller logs a GAP and skips; no fallback coordinate). */
static int general_y(void)
{
	uint32_t w, h;
	if (!panelbuf || panellen < 32) return 0;
	memcpy(&w, panelbuf + 4, 4); memcpy(&h, panelbuf + 8, 4);
	if ((size_t)w * h * 4 > panellen) return 0;
	const uint8_t *px = panelbuf + (panellen - (size_t)w * h * 4);
	int sc = w >= 1000 ? 1 : 2;   /* ring slots are half resolution */
	int run0 = -1, purple_last = -1, seen_purple = 0;
	for (int ty = 200; ty < 2380; ty++) {
		int y = ty / sc;
		if (y >= (int)h) break;
		int grey = 0, purple = 0;
		for (int tx = 40; tx <= 215; tx += 4) {
			const uint8_t *q = px + ((size_t)y * w + (size_t)(tx / sc)) * 4;
			int B = q[0], G = q[1], R = q[2], dr = R - G, dg = G - B;
			if (dr < 0) dr = -dr;
			if (dg < 0) dg = -dg;
			if (dr < 14 && dg < 14 && R >= 90 && R <= 205) grey++;
			else if (B > R + 50 && B > G + 50) purple++;
		}
		if (purple >= 5) { seen_purple = 1; purple_last = ty; }
		int isgrey = grey >= 5;
		if (isgrey && run0 < 0) run0 = ty;
		else if (!isgrey && run0 >= 0) {
			if (seen_purple && purple_last < run0 && ty - run0 >= 30) return (run0 + ty - 1) / 2;
			run0 = -1;
		}
	}
	return 0;
}

#define MAXLOAD 4
static pid_t loadpid[MAXLOAD];
static char *loadcmd[MAXLOAD];
static char *loadorig[MAXLOAD];   /* the untouched command (loadcmd is tokenised in place): heavy mode restarts a load that ended */
static int nload;
static int start_load(char *cmd)
{
	if (nload < MAXLOAD) loadorig[nload] = strdup(cmd);
	char *argv[24], *save = NULL, *dup = strdup(cmd);
	int n = 0;
	if (!dup) { printf("PIXNAV GAP: load %s: no memory\n", cmd); return -1; }
	for (char *t = strtok_r(dup, " ,", &save); t && n < 23; t = strtok_r(NULL, " ,", &save)) argv[n++] = t;   /* commas too: a resident --run value is split on spaces */
	argv[n] = NULL;
	if (n == 0 || argv[0][0] != '/') { printf("PIXNAV GAP: load '%s' refused (needs an absolute program path)\n", cmd); free(dup); return -1; }
	if (nload >= MAXLOAD) { printf("PIXNAV GAP: load '%s' refused (max %d)\n", cmd, MAXLOAD); free(dup); return -1; }
#ifdef PIXNAV_HOST
	printf("PIXNAV REPLAY load %s\n", cmd);
	loadpid[nload] = 0; loadcmd[nload++] = dup;
	return 0;
#else
	posix_spawn_file_actions_t fa;
	pid_t pid;
	posix_spawn_file_actions_init(&fa);
	posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", O_WRONLY, 0);
	posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0);
	int rc = posix_spawn(&pid, argv[0], &fa, NULL, argv, environ);
	posix_spawn_file_actions_destroy(&fa);
	if (rc != 0) { printf("PIXNAV GAP: load %s not started: %s\n", cmd, strerror(rc)); free(dup); return -1; }
	printf("PIXNAV LOAD started pid %d: %s\n", (int)pid, cmd);
	loadpid[nload] = pid; loadcmd[nload++] = dup;
	return 0;
#endif
}
static unsigned load_ends[MAXLOAD];   /* how many times each slot's load ended (reaped) */
/* "ended rc R|signal S" line for a reaped load; bounded: the first 10 ends of a slot, then every 10th (n = this slot's end count, 1-based).
 * Returns 1 and fills buf when the line is to be printed, 0 when it is suppressed by the bound. */
static int load_end_line(char *buf, size_t sz, int slot, long pid, int stt, uint64_t ms, const char *cmd, unsigned n)
{
	if (n > 10 && n % 10 != 0) return 0;
	snprintf(buf, sz, "PIXNAV LOAD slot %d pid %ld ended %s %d after %llu ms (end #%u): %s", slot, pid, WIFEXITED(stt) ? "rc" : "signal",
	    WIFEXITED(stt) ? WEXITSTATUS(stt) : WTERMSIG(stt), (unsigned long long)ms, n, cmd ? cmd : "?");
	return 1;
}
static void report_load(void)
{
	for (int i = 0; i < nload; i++) {
		if (load_ends[i]) printf("PIXNAV LOAD slot %d ended %u time(s) during the nav (lines bounded: first 10, then every 10th)\n", i, load_ends[i]);
#ifdef PIXNAV_HOST
		printf("PIXNAV LOAD %s (replay, not run)\n", loadcmd[i]);
#else
		int stt = 0;
		if (loadpid[i] <= 0) {   /* heavy mode: ended and waiting for its restart (never waitpid(-1/-2): any child / a process group) */
			printf("PIXNAV LOAD slot %d not running at the nav end (ended, restart pending): %s\n", i, loadorig[i] ? loadorig[i] : "?");
			continue;
		}
		pid_t r = waitpid(loadpid[i], &stt, WNOHANG);
		if (r == 0) printf("PIXNAV LOAD pid %d still running at the nav end (left running): %s\n", (int)loadpid[i], loadcmd[i] ? loadcmd[i] : "?");
		else if (r == loadpid[i]) printf("PIXNAV LOAD pid %d ended %s %d during the nav: %s\n", (int)loadpid[i], WIFEXITED(stt) ? "rc" : "signal",
		    WIFEXITED(stt) ? WEXITSTATUS(stt) : WTERMSIG(stt), loadcmd[i] ? loadcmd[i] : "?");
		else printf("PIXNAV GAP: load pid %d waitpid: %s\n", (int)loadpid[i], strerror(errno));
#endif
	}
}

/* ---------------------------------------------------------------- script steps (-S): I:url | G/g:x0,y0,x1,y1,ms | L | W:state[|state...],timeout_ms */
/* W: the closed-loop gate between steps (T-28 run2: the intent was sent while SpringBoard was still coming back after a backboardd restart and
 * SBSOpenSensitiveURLAndUnlock refused it). Looks (settle, read the panel, classify) until the state is one of the listed ones; bounded by
 * timeout_ms of pixnav's own clock. 0 = reached, -1 = refused (bad list) or timed out; both print a PIXNAV GAP so the job fails fast. */
#define WAIT_POLL_MS 500
#define WAIT_MAX_MS 600000u
static int wait_step(const char *spec)
{
	char buf[128];
	const char *comma = strrchr(spec, ',');
	char *end = NULL;
	unsigned long tmo = comma ? strtoul(comma + 1, &end, 10) : 0;
	int want[NSTATE_MAX], nwant = 0;
	if (!comma || comma == spec || end == comma + 1 || *end || tmo == 0 || tmo > WAIT_MAX_MS || (size_t)(comma - spec) >= sizeof buf) {
		printf("PIXNAV GAP: wait step 'W:%s' not understood (W:state[|state...],timeout_ms 1..%u)\n", spec, WAIT_MAX_MS);
		return -1;
	}
	memcpy(buf, spec, (size_t)(comma - spec));
	buf[comma - spec] = 0;
	char *save = NULL;
	for (char *t = strtok_r(buf, "|", &save); t; t = strtok_r(NULL, "|", &save)) {
		int id = state_id(t);
		if (id < 0 || nwant >= NSTATE_MAX) {
			printf("PIXNAV GAP: wait state '%s' is not a reference state of the refs file (or too many states): refused\n", t);
			return -1;
		}
		want[nwant++] = id;
	}
	if (nwant == 0) { printf("PIXNAV GAP: wait step 'W:%s' lists no state\n", spec); return -1; }
	uint64_t t0 = now_ns();
	int last = -2;
	for (;;) {
		last = look();
		unsigned long el = (unsigned long)((now_ns() - t0) / 1000000ull);
		for (int i = 0; i < nwant; i++)
			if (last == want[i]) { printf("SWEET NAV: WAIT %s ok after %lu ms\n", stname[last], el); return 0; }
		if (el >= tmo) {
			printf("PIXNAV GAP: wait %.*s timed out after %lu ms (last %s)\n", (int)(comma - spec), spec, el, last >= 0 ? stname[last] : last == -1 ? "unknown" : "panel error");
			return -1;
		}
		sleep_ms(WAIT_POLL_MS);
	}
}

/* returns the number of failed steps, or -1 when a W or I step failed and the script stopped there (nothing after it ran) */
static int run_script(const char *script)
{
	char *dup = strdup(script), *save = NULL;
	int bad = 0;
	if (!dup) { printf("PIXNAV GAP: no memory for the script\n"); return 1; }
	for (char *t = strtok_r(dup, ";", &save); t; t = strtok_r(NULL, ";", &save)) {
		int x0, y0, x1, y1;
		unsigned ms;
		if (t[0] == 'W' && t[1] == ':') {
			if (wait_step(t + 2) != 0) { bad = -1; break; }
		} else if (t[0] == 'I' && t[1] == ':') {
			if (intent(t + 2) != 0) { printf("PIXNAV GAP: script stopped at '%s' (put a W:<start state> before an I: step)\n", t); bad = -1; break; }
			sleep_ms(2500);
		} else if ((t[0] == 'G' || t[0] == 'g') && sscanf(t + 2, "%d,%d,%d,%d,%u", &x0, &y0, &x1, &y1, &ms) == 5) {
			printf("SWEET NAV: AB gesture %d,%d -> %d,%d ms %u%s now %llu\n", x0, y0, x1, y1, ms, t[0] == 'g' ? " (no settle)" : "", (unsigned long long)now_ns());
			if (gesture(x0, y0, x1, y1, ms) != 0) bad++;
			if (t[0] == 'G') sleep_ms(1200);   /* 'g' = the next step follows at once (T-36: does the home swipe need recent touches?) */
		} else if (t[0] == 'L' && t[1] == 0) {
			look();
		} else {
			printf("PIXNAV GAP: script step '%s' not understood (I:url | G:x0,y0,x1,y1,ms | L | W:state[|state...],timeout_ms)\n", t);
			bad++;
		}
	}
	free(dup);
	return bad;
}

/* ---------------------------------------------------------------- heavy bench (-B SECS), ALL-ON T-54 stage 1 (user 2026-11-12 ~22:2xZ: "it barely
 * stresses the phone, it is like best case scenario always"). The closed-loop nav above waits for a settled, classified screen before every action
 * and idles 3 s in Clock. Here the input never waits for the screen to settle: a GLANCE (panel read + classify, no settle) says where the phone is,
 * and the next block starts at once while the last animation still runs. Gestures are only the position-agnostic ones (bottom home swipe, page
 * swipes on a glanced home page, URL intents) and fling bursts on a glanced list screen (settings/general/about); a confirmed location alert is
 * dismissed by its Cancel; a lock screen is left by an intent. Apps: only those that reach the front on our boots [obs 183 logs 2026-11-11/12:
 * Preferences and MobileTimer via intents; MobileCal/AppStore exec only as 96 KB prewarms]. Loads (-b) are restarted when they end, so Wi-Fi traffic
 * and BT scan/reconnect run the whole time. Markers: 'SWEET NAV: ACTION <block> begin|inputend|end rep <n>' (tools/fps-wholerun steps). */
static const char *const SWITCH_URLS[] = { "prefs:root=WIFI", "clock-worldclock:", "prefs:root=Bluetooth", "prefs:root=General&path=About",
                                           "prefs:root=General", "clock-worldclock:" };
#define NSWITCH (sizeof SWITCH_URLS / sizeof SWITCH_URLS[0])
static unsigned hv_unconfirmed, hv_intents, hv_gestures, hv_restarts, hv_glances, hv_unknown;
static int glance(void)
{
	size_t len;
	double d0, bd;
	int coarse;
	hv_glances++;
	if (read_panel(&len) != 0 || features(panelbuf, len, &cur) != 0) { printf("PIXNAV GAP: glance: panel not classifiable\n"); return -2; }
	int st = classify_feat(&cur, &d0, &bd, &coarse);
	printf("SWEET NAV: GLANCE %s (coarse %s d %.1f band %.1f) now %llu\n", st >= 0 ? stname[st] : "unknown", coarse >= 0 ? stname[coarse] : "-", d0, bd,
	    (unsigned long long)now_ns());
	if (st == -1) { hv_unknown++; dump_unknown(d0, coarse); }
	return st;
}
static void hv_g(int x0, int y0, int x1, int y1, unsigned ms) { if (gesture(x0, y0, x1, y1, ms) == 0) hv_gestures++; }
static void hv_i(const char *url) { if (intent(url) == 0) hv_intents++; }
static int is_list(int st) { return is(st, "settings") || is(st, "general") || is(st, "about"); }
static void fling_burst(int n)   /* impatient flicks: 60-150 ms each, the next one 30-120 ms later while the list still moves */
{
	for (int k = 0; k < n; k++) {
		int up = rnd(0, 3) != 0, x = (int)rnd(300, 800), ya = (int)rnd(1500, 1950), yb = (int)rnd(500, 900);
		hv_g(x, up ? ya : yb, x + jit(30), up ? yb : ya, rnd(60, 150));
		sleep_ms(rnd(30, 120));
	}
}
static void page_burst(int n, int st)   /* home page flicks, alternating direction from the glanced page */
{
	int right = is(st, "homeA");
	for (int k = 0; k < n; k++, right = !right) {
		int y = 1200 + jit(150);
		if (right) hv_g(900 + jit(40), y, 150 + jit(40), y + jit(40), rnd(70, 160));
		else hv_g(150 + jit(40), y, 900 + jit(40), y + jit(40), rnd(70, 160));
		sleep_ms(rnd(40, 140));
	}
}
static void home_swipe(void) { hv_g(540, 2398, 540 + jit(60), 1200 + jit(150), rnd(120, 220)); }
static uint64_t load_t[MAXLOAD];   /* when each load was (re)started, ns */
#ifndef PIXNAV_HOST
static pid_t lastpid[MAXLOAD];   /* pid of the load that ended (kept while loadpid is the -2 backoff marker) */
static unsigned load_fast[MAXLOAD];   /* restarts within 5 s of the start (phone build only: the host replay runs no loads) */
#endif
#define LOAD_BACKOFF_NS 20000000000ull   /* a load that ends at once (pixperf with its server down, 79a2 rc 1) is retried every 20 s, not per block */
static void restart_loads(void)
{
#ifndef PIXNAV_HOST
	for (int i = 0; i < nload; i++) {
		int stt = 0;
		pid_t prev = loadpid[i];   /* the real pid for the restart line (loadpid becomes the -2 backoff marker) */
		if (loadpid[i] == -2) {   /* ended earlier, waiting out the backoff */
			if (now_ns() - load_t[i] < LOAD_BACKOFF_NS) continue;
		} else if (!(loadpid[i] > 0 && waitpid(loadpid[i], &stt, WNOHANG) == loadpid[i])) {
			continue;
		} else {
			uint64_t ran = now_ns() - load_t[i];
			char ln[1024];
			if (load_end_line(ln, sizeof ln, i, (long)prev, stt, ran / 1000000ull, loadorig[i], ++load_ends[i])) printf("%s\n", ln);
			if (ran < 5000000000ull && ++load_fast[i] == 2)
				printf("PIXNAV GAP: load %s ends within 5 s of each start (server/peer down?): retried every 20 s, the stress is missing it\n",
				    loadorig[i] ? loadorig[i] : "?");
			if (ran < LOAD_BACKOFF_NS) { loadpid[i] = -2; lastpid[i] = prev; continue; }
		}
		{
			printf("PIXNAV LOAD slot %d (pid %d) restarting %s\n", i, (int)(loadpid[i] == -2 ? lastpid[i] : prev), loadorig[i] ? loadorig[i] : "?");
			char *cmd = loadorig[i];
			loadorig[i] = NULL;
			free(loadcmd[i]);
			loadcmd[i] = NULL;
			int keep = nload;
			nload = i;   /* start_load appends at nload: reuse slot i (it sets loadorig/loadcmd/loadpid of slot i) */
			if (cmd && start_load(cmd) == 0) hv_restarts++;
			else { loadpid[i] = -2; if (!loadorig[i]) loadorig[i] = cmd ? strdup(cmd) : NULL; }   /* spawn failed: retried after the backoff */
			load_t[i] = now_ns();
			nload = keep;
			free(cmd);
		}
	}
#endif
}
/* one block; returns its name for the markers */
static const char *heavy_block(unsigned n)
{
	static const char *names[] = { "hv-home-pages", "hv-settings-flings", "hv-about-flings", "hv-switch-storm", "hv-spotlight", "hv-home-swipe" };
	unsigned b = rnd(0, 5);
	printf("SWEET NAV: ACTION %s begin rep %u now %llu\n", names[b], n, (unsigned long long)now_ns());
	int st = glance();
	if (is(st, "alert-loc")) {
		printf("SWEET NAV: DISMISS alert-loc (Cancel) rep %u\n", n);
		hv_g(ALERT_CANCEL_X, ALERT_CANCEL_Y, ALERT_CANCEL_X, ALERT_CANCEL_Y, 80);
		sleep_ms(rnd(300, 700));
	} else if (is(st, "lock")) {   /* one unlock intent, then let it start: an intent per glance on a lock screen is a storm (host replay rall: 110k) */
		hv_i("prefs:root=General");
		sleep_ms(rnd(1500, 2500));
	} else switch (b) {
	case 0:   /* page flicks on the home screen; not on it: the home swipe first, then flick only if the glance says home */
		if (!is(st, "homeA") && !is(st, "homeB")) { home_swipe(); sleep_ms(rnd(150, 400)); st = glance(); }
		if (is(st, "homeA") || is(st, "homeB")) page_burst((int)rnd(4, 8), st);
		else hv_unconfirmed++;
		break;
	case 1:   /* Settings root / General lists: intent, then a fling burst as soon as a glance confirms a list (mid-push counts: no settle) */
		hv_i(rnd(0, 1) ? "prefs:root=General" : "prefs:root=WIFI");
		sleep_ms(rnd(200, 450));
		st = glance();
		if (is(st, "general") || is(st, "about")) { hv_g(5, 1200, 700, 1200, rnd(150, 250)); sleep_ms(rnd(80, 200)); st = glance(); }
		if (is_list(st)) fling_burst((int)rnd(5, 10));
		else hv_unconfirmed++;
		break;
	case 2:   /* About: deep link, fling burst, two back-swipes (each only from a glanced sub-page), flings on what is left */
		hv_i("prefs:root=General&path=About");
		sleep_ms(rnd(200, 450));
		st = glance();
		if (is_list(st)) fling_burst((int)rnd(5, 10)); else hv_unconfirmed++;
		for (int k = 0; k < 2; k++) {
			st = glance();
			if (is(st, "about") || is(st, "general")) { hv_g(5, 1200, 700 + jit(80), 1200 + jit(60), rnd(120, 250)); sleep_ms(rnd(60, 180)); }
		}
		st = glance();
		if (is_list(st)) fling_burst((int)rnd(5, 10));
		break;
	case 3: {   /* app switch storm: 6-12 intents 250-600 ms apart (Settings deep links <-> Clock), never waiting for a page to finish */
		int k = (int)rnd(6, 12);
		for (int i = 0; i < k; i++) {
			/* every intent is a real switch (user 2026-11-14): re-opening the page already in front draws nothing (18 of 18 >= 1 s gaps of 74a5/c1f4/9ec5 were
			 * such repeats). Same list, weights and pacing; a pick equal to the newest intent is drawn again (the list holds 4 distinct URLs, so this ends) */
			const char *u;
			do u = SWITCH_URLS[rnd(0, NSWITCH - 1)]; while (last_intent && strcmp(u, last_intent) == 0);
			hv_i(u);
			sleep_ms(rnd(250, 600));
		}
		break;
	}
	case 4:   /* Spotlight pull then the home swipe 200-500 ms later (the keyboard still sliding in) */
		if (!is(st, "homeA") && !is(st, "homeB")) { home_swipe(); sleep_ms(rnd(150, 400)); st = glance(); }
		if (is(st, "homeA") || is(st, "homeB")) { hv_g(540 + jit(80), 500, 540 + jit(80), 1500, rnd(150, 300)); sleep_ms(rnd(200, 500)); home_swipe(); }
		else hv_unconfirmed++;
		break;
	default:   /* from wherever: the bottom home swipe, then page flicks if the glance says home */
		home_swipe();
		sleep_ms(rnd(120, 300));
		st = glance();
		if (is(st, "homeA") || is(st, "homeB")) page_burst((int)rnd(2, 4), st);
		break;
	}
	printf("SWEET NAV: ACTION %s inputend rep %u now %llu\n", names[b], n, (unsigned long long)now_ns());
	printf("SWEET NAV: ACTION %s end rep %u now %llu\n", names[b], n, (unsigned long long)now_ns());
	return names[b];
}
static int heavy(unsigned secs)
{
	uint64_t t0 = now_ns();
	unsigned n = 0;
	for (int i = 0; i < nload; i++) load_t[i] = now_ns();
	printf("SWEET NAV: HEAVY start secs %u seed 0x%x refs %u states %u loads %d (no settle, fling bursts, switch storms, loads restarted)\n", secs, rng,
	    nrefs, nstates, nload);
	while ((now_ns() - t0) / 1000000000ull < secs) {
		uint64_t tb = now_ns();
		heavy_block(n++);
		restart_loads();
		if (now_ns() - tb < 150000000ull) sleep_ms(rnd(60, 150));   /* a block that sent no burst still takes a human-fast beat */
	}
	report_load();
	printf("SWEET NAV: HEAVY done secs %llu blocks %u gestures %u intents %u glances %u unknown %u unconfirmed %u load_restarts %u\n",
	    (unsigned long long)((now_ns() - t0) / 1000000000ull), n, hv_gestures, hv_intents, hv_glances, hv_unknown, hv_unconfirmed, hv_restarts);
	return 0;
}

int main(int argc, char **argv)
{
	unsigned reps = 3;
	const char *refp = "/usr/local/share/pixnav/refs.bin", *scenp = NULL;
	int opt;
	setvbuf(stdout, NULL, _IONBF, 0);
	devnull = open("/dev/null", O_WRONLY);
	if (devnull < 0) printf("PIXNAV GAP: /dev/null not opened (%s): markers reach only stdout, not the USB log\n", strerror(errno));
	rng = (uint32_t)now_ns() | 1u;
	const char *one = NULL;
	char *loads[MAXLOAD];
	int nl = 0, detach = 0;
	const char *script = NULL;
	unsigned heavy_secs = 0;
	while ((opt = getopt(argc, argv, "r:s:R:cH:F:b:DS:U:X:B:E:")) != -1) {
		switch (opt) {
		case 'r': reps = (unsigned)atoi(optarg); break;
		case 's': rng = (uint32_t)strtoul(optarg, NULL, 0) | 1u; break;
		case 'R': refp = optarg; break;
		case 'c': calm = 1; break;
		case 'H': scenp = optarg; break;
		case 'F': one = optarg; break;
		case 'D': detach = 1; break;
		case 'U': dumpfile = optarg; break;
		case 'B': heavy_secs = (unsigned)atoi(optarg); break;
#ifdef PIXNAV_HOST
		case 'X': if (sim_start(optarg) != 0) { printf("PIXNAV GAP: simulator '%s' not started\n", optarg); return 2; } break;
#else
		case 'X': printf("PIXNAV GAP: -X (host simulator) exists only in the host build\n"); return 2;
#endif
#ifdef PIXNAV_HOST
		case 'E': {   /* host test hook: -E "slot,wait-status,ms,end#,cmd" prints the load-end line for a fake reaped status (loads are not run on the host) */
			int sl, st, off = 0;
			unsigned long long ms;
			unsigned n;
			char ln[1024];
			if (sscanf(optarg, "%d,%d,%llu,%u,%n", &sl, &st, &ms, &n, &off) < 4 || off <= 0) { printf("PIXNAV GAP: -E %s not understood\n", optarg); return 2; }
			if (load_end_line(ln, sizeof ln, sl, 4242, st, ms, optarg + off, n)) printf("%s\n", ln);
			else printf("PIXNAV LOAD end line suppressed by the bound (end #%u)\n", n);
			return 0;
		}
#else
		case 'E': printf("PIXNAV GAP: -E exists only in the host build\n"); return 2;
#endif
		case 'S': script = optarg; break;   /* gesture A/B: 'I:url;G:x0,y0,x1,y1,ms;L;W:st|st,ms;...' (I = intent, G = one gesture, L = look, W = wait for a state) */
		case 'b': if (nl < MAXLOAD) loads[nl++] = optarg; else printf("PIXNAV GAP: -b %s ignored (max %d loads)\n", optarg, MAXLOAD); break;
		default: printf("usage: pixnav [-r reps] [-s seed] [-R refs.bin] [-c] [-H scenario] [-F panel] [-b \"/abs/prog args\"]... [-D] [-S script] [-U dumpfile] [-X simcmd (host)] [-B secs (heavy bench)]\n"); return 2;
		}
	}
	if (dumpfile && (dumpfd = open(dumpfile, O_WRONLY | O_CREAT | O_TRUNC, 0644)) < 0)
		printf("PIXNAV GAP: -U %s not created (%s): unknown panels are not kept\n", dumpfile, strerror(errno));
	if (reps == 0 || reps > 10) { printf("PIXNAV GAP: reps %u refused (1..10)\n", reps); return 2; }
	if (scenp && !(scen = fopen(scenp, "r"))) { printf("PIXNAV GAP: scenario %s unreadable\n", scenp); return 2; }
	if (load_refs(refp) != 0) return 2;
#ifndef PIXNAV_HOST
	if (!one) {   /* a kernel without patch 0312 has neither sysctl: stop now instead of a GAP per step (job e462 on kernel-demo) */
		int mib[8];
		size_t ml = 8;
		uint64_t s0, d0;
		if (sysctlnametomib("kern.sweetgesture", mib, &ml) != 0 || fbcount(&s0, &d0) != 0) {
			printf("PIXNAV GAP: kernel without patch 0312 (kern.sweetgesture / kern.sweetfbcount missing: %s): no nav, exit 2\n", strerror(errno));
			return 2;
		}
	}
#endif
	if (one) {   /* classify one panel file (host parity test with tools/nav-classify, or a pulled snapshot) */
		FILE *f = fopen(one, "rb");
		long n;
		double d0, bd;
		int coarse;
		if (!f) { printf("PIXNAV GAP: %s unreadable\n", one); return 2; }
		fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
		panelbuf = n > 0 ? malloc((size_t)n) : NULL;
		if (!panelbuf || fread(panelbuf, 1, (size_t)n, f) != (size_t)n) { fclose(f); printf("PIXNAV GAP: %s short\n", one); return 2; }
		fclose(f);
		panellen = panelcap = (size_t)n;
		if (features(panelbuf, panellen, &cur) != 0) { printf("PIXNAV GAP: %s not a panel readback\n", one); return 2; }
		int st = classify_feat(&cur, &d0, &bd, &coarse);
		printf("NAV STATE %s d=%.1f band=%.1f coarse=%s file=%s\n", st >= 0 ? stname[st] : "unknown", d0, bd, stname[coarse], one);
		return 0;
	}
#ifndef PIXNAV_HOST
	if (detach) {   /* devd kills a RUN after DEVD_RUN_TIMEOUT_SEC 30 (tools/devd.c:200, job 9431 rc 124): run in the background, lines only via the
	                 * /dev/null mirror (sweetnull=pixnav); the job waits for 'SWEET NAV: done reps N' on the USB log */
		pid_t pid = fork();
		if (pid < 0) { printf("PIXNAV GAP: fork for -D failed: %s\n", strerror(errno)); return 2; }
		if (pid > 0) { printf("PIXNAV detached pid %d (lines on the USB log via sweetnull=pixnav)\n", (int)pid); return 0; }
		setsid();
		int fd = open("/dev/null", O_RDWR);
		if (fd >= 0) { dup2(fd, 0); dup2(fd, 1); dup2(fd, 2); if (fd > 2) close(fd); }
		if (devnull >= 0) { close(devnull); devnull = -1; }   /* stdout IS /dev/null now: one copy per line */
	}
#else
	if (detach) printf("PIXNAV REPLAY -D: no detach in a host replay (runs in the foreground)\n");
#endif
	for (int i = 0; i < nl; i++) start_load(loads[i]);
	if (script) {   /* T-36 home-swipe A/B (allon-758e: the injected home swipe never left Settings/Clock): run the steps, log each look */
		int bad = run_script(script);
		report_load();
		if (bad < 0) printf("SWEET NAV: AB aborted (script stopped at a failed step)\n");
		else printf("SWEET NAV: AB done (bad %d)\n", bad);
		return bad ? 1 : 0;
	}
	if (heavy_secs) {
		if (heavy_secs < 60 || heavy_secs > 1500) { printf("PIXNAV GAP: -B %u refused (60..1500 s)\n", heavy_secs); return 2; }
		return heavy(heavy_secs);
	}
	printf("SWEET NAV: STRESS %s (pixnav closed loop) seed 0x%x reps %u refs %u states %u loads %d\n", calm ? "off" : "on", rng, reps, nrefs, nstates, nload);
	/* no blind start gesture: the first action's reach() looks first; a lock screen is left by the intent (a bottom swipe after a respring does
	 * not unlock: T-28 run3, 8a63 [obs]) */
	printf("SWEET NAV: START closed loop (first look decides) now %llu\n", (unsigned long long)now_ns());
	unsigned skipped = 0, missed = 0;
	for (unsigned rep = 0; rep < reps; rep++)
		for (unsigned i = 0; i < NACT; i++) {
			int rc = run_action(&ACTS[i], rep);
			if (rc < 0) { skipped++; printf("SWEET GAP: NAV pixnav skipped %s rep %u (start screen not reached)\n", ACTS[i].name, rep); }
			else if (rc > 0) missed++;
		}
	report_load();
	printf("SWEET NAV: done reps %u (pixnav: skipped %u missed %u)\n", reps, skipped, missed);
	return skipped ? 1 : 0;
}
