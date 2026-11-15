/* PR-3 marker probe, device side: log lines and the per-site run (pr3run.c). build.sh --pr3-marker
 * defines PR3_MARKER; without it pr3_marker_run() is an empty inline (compiled out, not a stub). */
#ifndef PR3RUN_H
#define PR3RUN_H

#ifdef PR3_MARKER
/* Probe site A and site B (pr3marker.h): log each marker state to the console + RAM log and
 * write the next marker. */
void pr3_marker_run(void);
#else
static inline void pr3_marker_run(void) {}
#endif

#endif
