/*
 * pixstate: read-only Wi-Fi state witness for PixOS (sprint-wifi, rung W-UI)
 * ===========================================================================
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 PixOS-Project contributors.
 *
 * Usage: pixstate [-d delay_s] [-n rounds] [-i interval_s]
 *
 * Prints, per round, what the iOS userland believes about Wi-Fi, so a run can tell "wifid has no current
 * network" from "wifid has it but IP configuration is missing" while Settings > Wi-Fi shows a spinner:
 *  1. wifid (through MobileWiFi, the client API Settings uses): number of devices, power, whether
 *     WiFiDeviceClientCopyCurrentNetwork returns a network and the length of its SSID (never the bytes).
 *  2. configd (SCDynamicStore): the State:/Network keys of en0, the global IPv4 key, every service IPv4 key;
 *     IPv4 dictionaries are printed whole (addresses stay in the gitignored run log), the AirPort
 *     dictionary only as its key names (it carries the SSID). The DNS keys (State:/Network/Global/DNS, State:/Network/Service/<id>/DNS,
 *     Setup:/Network/Global/DNS; lowclk 2026-11-13) are printed whole too: ServerAddresses and SearchDomains, so a run shows whether
 *     the DHCP lease carried a resolver. Setup:/Network/Service/.* already covered a service-level Setup DNS key.
 * Nothing is written or changed: it only reads. Every symbol is resolved with dlsym; a missing one is a
 * "pixstate: GAP" line, never a silent skip. Symbols checked in the iOS 16.7 dyld cache with
 * `ipsw dyld symaddr` (MobileWiFi 1635.6, SystemConfiguration 1241.140.6), 2026-11-08.
 * The MobileWiFi client needs the entitlement com.apple.wifi.manager-access (pixping-stage signs it).
 * stdout is unbuffered so every line reaches the console tee (patch 0048, boot arg sweetnull=...,pixstate).
 */
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef const void *Ref;
static Ref (*mwCreate)(CFAllocatorRef, int);
static CFArrayRef (*mwCopyDevices)(Ref);
static Ref (*mwCopyCur)(Ref);
static CFStringRef (*mwGetSSID)(Ref);
static int (*mwGetPower)(Ref);
/* Part A (sprint-wifi 2026-11-09): the RSSI wifid holds for the current network (what Settings shows). MobileWiFi 1635.6 [obs RE, ipsw dyld disass]: _WiFiNetworkGetRSSI 0x1b302a3bc =
 * WiFiNetworkGetProperty(net, CFSTR("RSSI")) -> _CFTypeGetIntValue (CFNumber, kCFNumberCFIndexType), returns long, 0 for NULL/missing; _WiFiNetworkGetProperty 0x1b301c244 = CFDictionaryGetValue
 * of the network record (Get rule: not released). The key's presence is printed so 0 is never mistaken for a reading. */
static long (*mwGetRSSI)(Ref);
static Ref (*mwGetProp)(Ref, CFStringRef);
static Ref (*scCreate)(CFAllocatorRef, CFStringRef, void *, void *);
static CFPropertyListRef (*scCopyValue)(Ref, CFStringRef);
static CFArrayRef (*scCopyKeys)(Ref, CFStringRef);

static void *sym(void *h, const char *lib, const char *name)
{
	void *p = h ? dlsym(h, name) : NULL;
	if (!p)
		printf("pixstate: GAP %s: symbol %s not found\n", lib, name);
	return p;
}

static void pcf(const char *tag, CFTypeRef v)
{
	char b[1536];
	CFStringRef d;
	if (!v) {
		printf("pixstate: %s = (none)\n", tag);
		return;
	}
	d = CFCopyDescription(v);
	if (!d || !CFStringGetCString(d, b, sizeof b, kCFStringEncodingUTF8))
		snprintf(b, sizeof b, "(description unavailable or longer than %zu)", sizeof b - 1);
	if (d)
		CFRelease(d);
	for (char *c = b; *c; c++)
		if (*c == '\n' || *c == '\t')
			*c = ' ';
	printf("pixstate: %s = %s\n", tag, b);
}

static void keynames(const char *tag, CFTypeRef v)
{
	char out[1024] = "", k[96];
	if (!v || CFGetTypeID(v) != CFDictionaryGetTypeID()) {
		printf("pixstate: %s keys = (none)\n", tag);
		return;
	}
	CFIndex n = CFDictionaryGetCount((CFDictionaryRef)v);
	const void **ks = calloc((size_t)n + 1, sizeof *ks);
	CFDictionaryGetKeysAndValues((CFDictionaryRef)v, ks, NULL);
	for (CFIndex i = 0; i < n; i++)
		if (CFGetTypeID(ks[i]) == CFStringGetTypeID() && CFStringGetCString(ks[i], k, sizeof k, kCFStringEncodingUTF8) &&
		    strlen(out) + strlen(k) + 2 < sizeof out) {
			strcat(out, k);
			strcat(out, " ");
		}
	free(ks);
	printf("pixstate: %s keys (%ld) = %s\n", tag, (long)n, out);
}

static void round_wifi(int r)
{
	if (!mwCreate || !mwCopyDevices) {
		printf("pixstate: r%d wifid: GAP MobileWiFi client symbols missing\n", r);
		return;
	}
	Ref mgr = mwCreate(kCFAllocatorDefault, 0);
	if (!mgr) {
		printf("pixstate: r%d wifid: GAP WiFiManagerClientCreate returned NULL\n", r);
		return;
	}
	CFArrayRef devs = mwCopyDevices(mgr);
	CFIndex nd = devs ? CFArrayGetCount(devs) : 0;
	printf("pixstate: r%d wifid: devices=%ld\n", r, (long)nd);
	for (CFIndex i = 0; i < nd; i++) {
		Ref dev = CFArrayGetValueAtIndex(devs, i);
		int pw = mwGetPower ? mwGetPower(dev) : -1;
		Ref net = mwCopyCur ? mwCopyCur(dev) : NULL;
		CFStringRef ssid = (net && mwGetSSID) ? mwGetSSID(net) : NULL;
		printf("pixstate: r%d wifid: dev %ld power=%d current_network=%s ssid_len=%ld\n", r, (long)i, pw, net ? "yes" : "NO",
		       ssid ? (long)CFStringGetLength(ssid) : -1L);
		if (net && mwGetRSSI && mwGetProp) {
			Ref v = mwGetProp(net, CFSTR("RSSI"));
			if (v)
				printf("pixstate: r%d wifid: current network RSSI=%ld dBm (key RSSI present)\n", r, mwGetRSSI(net));
			else
				printf("pixstate: r%d wifid: GAP current network has no RSSI key\n", r);
		} else if (net) {
			printf("pixstate: r%d wifid: GAP WiFiNetworkGetRSSI/GetProperty symbols missing\n", r);
		}
		if (net)
			CFRelease(net);
	}
	if (devs)
		CFRelease(devs);
	CFRelease(mgr);
}

static void round_sc(int r)
{
	char t[64];
	if (!scCreate || !scCopyValue || !scCopyKeys) {
		printf("pixstate: r%d configd: GAP SCDynamicStore symbols missing\n", r);
		return;
	}
	Ref st = scCreate(kCFAllocatorDefault, CFSTR("pixstate"), NULL, NULL);
	if (!st) {
		printf("pixstate: r%d configd: GAP SCDynamicStoreCreate returned NULL\n", r);
		return;
	}
	CFArrayRef ks = scCopyKeys(st, CFSTR("(State:/Network/(Interface/en0/.*|Global/IPv4|Global/DNS|Service/.*/IPv4|Service/.*/DNS)|Setup:/Network/(Global/IPv4|Global/DNS|Service/.*|Interface/en0/.*))"));
	CFIndex n = ks ? CFArrayGetCount(ks) : 0;
	printf("pixstate: r%d configd: %ld matching keys\n", r, (long)n);
	for (CFIndex i = 0; i < n; i++) {
		CFStringRef k = CFArrayGetValueAtIndex(ks, i);
		char kb[160];
		if (!CFStringGetCString(k, kb, sizeof kb, kCFStringEncodingUTF8))
			continue;
		CFPropertyListRef v = scCopyValue(st, k);
		snprintf(t, sizeof t, "r%d %s", r, kb);
		if (strstr(kb, "AirPort"))
			keynames(t, v);
		else
			pcf(t, v);
		if (v)
			CFRelease(v);
	}
	if (ks)
		CFRelease(ks);
	CFRelease(st);
}

int main(int argc, char **argv)
{
	int delay = 0, rounds = 1, interval = 15, opt;
	setvbuf(stdout, NULL, _IONBF, 0);
	while ((opt = getopt(argc, argv, "d:n:i:")) != -1) {
		int v = atoi(optarg);
		if (v < 0 || v > 3600) {
			printf("pixstate: GAP bad value for -%c\n", opt);
			return 2;
		}
		if (opt == 'd')
			delay = v;
		else if (opt == 'n')
			rounds = v ? v : 1;
		else if (opt == 'i')
			interval = v;
		else {
			printf("pixstate: GAP usage: pixstate [-d delay_s] [-n rounds] [-i interval_s]\n");
			return 2;
		}
	}
	printf("pixstate: start delay=%d rounds=%d interval=%d\n", delay, rounds, interval);
	void *mw = dlopen("/System/Library/PrivateFrameworks/MobileWiFi.framework/MobileWiFi", RTLD_NOW);
	void *sc = dlopen("/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration", RTLD_NOW);
	if (!mw)
		printf("pixstate: GAP dlopen MobileWiFi failed\n");
	if (!sc)
		printf("pixstate: GAP dlopen SystemConfiguration failed\n");
	mwCreate = sym(mw, "MobileWiFi", "WiFiManagerClientCreate");
	mwCopyDevices = sym(mw, "MobileWiFi", "WiFiManagerClientCopyDevices");
	mwCopyCur = sym(mw, "MobileWiFi", "WiFiDeviceClientCopyCurrentNetwork");
	mwGetSSID = sym(mw, "MobileWiFi", "WiFiNetworkGetSSID");
	mwGetPower = sym(mw, "MobileWiFi", "WiFiDeviceClientGetPower");
	mwGetRSSI = sym(mw, "MobileWiFi", "WiFiNetworkGetRSSI");
	mwGetProp = sym(mw, "MobileWiFi", "WiFiNetworkGetProperty");
	scCreate = sym(sc, "SystemConfiguration", "SCDynamicStoreCreate");
	scCopyValue = sym(sc, "SystemConfiguration", "SCDynamicStoreCopyValue");
	scCopyKeys = sym(sc, "SystemConfiguration", "SCDynamicStoreCopyKeyList");
	if (delay > 0)
		sleep((unsigned)delay);
	for (int r = 0; r < rounds; r++) {
		if (r > 0)
			sleep((unsigned)interval);
		round_wifi(r);
		round_sc(r);
	}
	printf("pixstate: done rounds=%d\n", rounds);
	return 0;
}
