/* Host test of patch 0291: sweet_sandbox_priv_grant grants the 7 Skywalk privileges by entitlement.
 * The REAL section of bsd/kern/sweet_amfi.c (patches applied in order) is included via SWEET_SECTION. */
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <errno.h>

typedef int kauth_cred_t;
#define PRIV_WORK_INTERVAL 1008
#define PRIV_NET_PRIVILEGED_SOCKET_DELEGATE 10001
#define PRIV_NET_PRIVILEGED_NETWORK_STATISTICS 10003
#define PRIV_SKYWALK_REGISTER_USER_PIPE 12000
#define PRIV_SKYWALK_REGISTER_KERNEL_PIPE 12001
#define PRIV_SKYWALK_REGISTER_NET_IF 12002
#define PRIV_SKYWALK_REGISTER_FLOW_SWITCH 12003
#define PRIV_SKYWALK_LOW_LATENCY_CHANNEL 12004
#define PRIV_SKYWALK_OBSERVE_ALL 12010
#define PRIV_SKYWALK_OBSERVE_STATS 12011

static char cur_name[20] = "bluetoothd";
static const char *held[16];
static int nheld;
static char logbuf[16384];

static void proc_selfname(char *b, size_t n) { snprintf(b, n, "%s", cur_name); }
static unsigned kauth_cred_getuid(kauth_cred_t c) { (void)c; return 501; }
static size_t my_strlcpy(char *d, const char *s, size_t n) { snprintf(d, n, "%s", s); return strlen(s); }
#define strlcpy my_strlcpy
static int ent_calls;
static bool IOCurrentTaskHasEntitlement(const char *e)
{
	ent_calls++;
	for (int i = 0; i < nheld; i++) {
		if (strcmp(held[i], e) == 0) {
			return true;
		}
	}
	return false;
}
static int my_printf(const char *f, ...)
{
	va_list ap; va_start(ap, f);
	size_t l = strlen(logbuf);
	int r = vsnprintf(logbuf + l, sizeof(logbuf) - l, f, ap);
	va_end(ap);
	return r;
}
#define printf my_printf

#include SWEET_SECTION

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; fprintf(stderr, "FAIL line %d: " #c "\n", __LINE__); fputs(logbuf, stderr); } } while (0)
#undef printf

static int count(const char *needle)
{
	int n = 0; const char *p = logbuf;
	while ((p = strstr(p, needle)) != NULL) { n++; p += strlen(needle); }
	return n;
}

int main(void)
{
	static const struct { int priv; const char *ent; } m[] = {
		{ 12000, "com.apple.private.skywalk.register-user-pipe" },
		{ 12001, "com.apple.private.skywalk.register-kernel-pipe" },
		{ 12002, "com.apple.private.skywalk.register-net-if" },
		{ 12003, "com.apple.private.skywalk.register-flow-switch" },
		{ 12010, "com.apple.private.skywalk.observe-all" },
		{ 12011, "com.apple.private.skywalk.observe-stats" },
		{ 12004, "com.apple.private.skywalk.low-latency-channel" },
	};
	for (int i = 0; i < 7; i++) {
		/* without the entitlement: denied + logged denied; holding only the OTHER six: still denied */
		logbuf[0] = 0; nheld = 0;
		for (int j = 0; j < 7; j++) { if (j != i) { held[nheld++] = m[j].ent; } }
		CHECK(sweet_sandbox_priv_grant(0, m[i].priv) == EPERM, "denied without own entitlement");
		char want[128]; snprintf(want, sizeof want, "privilege %d denied (uid 501)", m[i].priv);
		CHECK(count(want) == 1, "denied line logged");
		CHECK(count("granted by entitlement") == 0, "no grant line when denied");
		/* with it: granted, one line naming proc, priv, entitlement; second call: granted, no second line */
		logbuf[0] = 0; nheld = 0; held[nheld++] = m[i].ent;
		CHECK(sweet_sandbox_priv_grant(0, m[i].priv) == 0, "granted with entitlement");
		snprintf(want, sizeof want, "SWEET Sandbox: bluetoothd privilege %d granted by entitlement %s\n", m[i].priv, m[i].ent);
		CHECK(strcmp(logbuf, want) == 0, "grant line exact");
		CHECK(sweet_sandbox_priv_grant(0, m[i].priv) == 0, "granted again");
		CHECK(count("granted by entitlement") == 1, "grant logged once per (proc, priv)");
		/* another process, same priv: logged once for it */
		snprintf(cur_name, sizeof cur_name, "otherd");
		CHECK(sweet_sandbox_priv_grant(0, m[i].priv) == 0, "granted other proc");
		CHECK(count("otherd privilege") == 1, "second proc logged");
		snprintf(cur_name, sizeof cur_name, "bluetoothd");
	}
	/* other privileges untouched: no entitlement lookup, still denied, even holding every skywalk entitlement */
	nheld = 0; for (int j = 0; j < 7; j++) { held[nheld++] = m[j].ent; }
	ent_calls = 0; logbuf[0] = 0;
	CHECK(sweet_sandbox_priv_grant(0, PRIV_WORK_INTERVAL) == 0, "work interval still granted");
	CHECK(sweet_sandbox_priv_grant(0, 12005) == EPERM, "12005 (not mapped) denied");
	CHECK(sweet_sandbox_priv_grant(0, 11999) == EPERM, "11999 denied");
	CHECK(sweet_sandbox_priv_grant(0, 10002) == EPERM, "10002 denied");
	CHECK(ent_calls == 0, "no entitlement lookup for other privileges");
	/* existing name-keyed net grants unchanged */
	snprintf(cur_name, sizeof cur_name, "symptomsd");
	CHECK(sweet_sandbox_priv_grant(0, PRIV_NET_PRIVILEGED_NETWORK_STATISTICS) == 0, "symptomsd net stats still granted");
	snprintf(cur_name, sizeof cur_name, "bluetoothd");
	CHECK(sweet_sandbox_priv_grant(0, PRIV_NET_PRIVILEGED_NETWORK_STATISTICS) == EPERM, "net stats denied for bluetoothd");
	/* skywalk privilege is never granted by process name */
	nheld = 0;
	snprintf(cur_name, sizeof cur_name, "symptomsd");
	CHECK(sweet_sandbox_priv_grant(0, 12000) == EPERM, "no name-keyed skywalk grant");
	/* log bound: SWEET_PRIV_LOG_MAX distinct procs, then no more lines but grants continue */
	nheld = 0; held[nheld++] = m[0].ent; logbuf[0] = 0; sweet_priv_sk_logged_count = 0;
	for (int i = 0; i < SWEET_PRIV_LOG_MAX + 10; i++) {
		snprintf(cur_name, sizeof cur_name, "p%d", i);
		CHECK(sweet_sandbox_priv_grant(0, 12000) == 0, "granted beyond log bound");
	}
	CHECK(count("granted by entitlement") == SWEET_PRIV_LOG_MAX, "grant log bounded");
	if (fails) { fprintf(stdout, "RESULT: FAIL %d\n", fails); return 1; }
	puts("RESULT: PASS sweet-sandbox-skywalk");
	return 0;
}
