/*
 * pixopen: open a URL in the foreground on PixOS (sprint-wifi, rung W-UI)
 * ==========================================================================
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 PixOS-Project contributors.
 *
 * Usage: pixopen [-d delay_s] [-r tries] [-n] [-l LIBRARY] [URL]
 *
 * Opens URL (default prefs:root=WIFI, i.e. Settings > Wi-Fi) in the foreground, once, from a
 * launchd job started at boot. Scripted touch taps (patch 0243) are unreliable; a URL open
 * does not depend on icon positions.
 *
 *  1. Sleeps -d seconds (default 60) so SpringBoard is up (a plain line every 10 s).
 *  2. dlopen()s SpringBoardServices and dlsym()s SBSOpenSensitiveURLAndUnlock (no private
 *     SDK header needed), builds a CFURL and calls it. -n passes unlock=0.
 *  3. Prints `pixopen: open <url> rc=N` (N = the Boolean the call returned, 1 = accepted).
 *     Every failure prints a `pixopen: GAP ...` line (no silent failure). A false return is
 *     retried up to -r times (default 1 = no retry), 5 s apart.
 *
 * API, read from the iOS 16.7 (20H392) dyld shared cache [bin]:
 *   _SBSOpenSensitiveURLAndUnlock 0x19151ebf8 (_SBSOpenSensitiveURL 0x19151ebf0 = same with w1=0)
 *   takes (CFURLRef url, Boolean unlock). With unlock set it builds the options dictionary
 *   {FBSOpenApplicationOptionKeyUnlockDevice: @YES}, then sends
 *   `[[LSApplicationWorkspace defaultWorkspace] openSensitiveURL:url withOptions:opts]`
 *   (selectors openSensitiveURL / defaultWorkspace / numberWithBool: at the msgSend stubs
 *   0x191590520 / 0x19158e380 / 0x191590360); the result is its BOOL.
 *   LaunchServices (lsd) reads the client's boolean entitlement
 *   com.apple.springboard.opensensitiveurl (CoreServices 0x181cb1348 -> string 0x181d43378;
 *   its text also appears in FBSystemService's trusted-request test, FrontBoard 0x1a26b604a).
 *   So the binary is signed with that entitlement plus com.apple.springboard.launchapplications
 *   (the trusted-caller set of FBSystemService, T-9524-foreground-app-path.md Q2.2).
 *   tools/pixping-stage makes the signature (ldid -Cadhoc -S<plist>) and re-reads it with ldid -e.
 *
 * Exit: 0 open returned true; 1 open returned false (after all tries); 2 usage;
 *       3 library/symbol missing; 4 URL could not be built.
 * stdout is unbuffered so every line reaches the console tee at once.
 *
 * Host build (Linux, tests): there is no CoreFoundation, so CFURLRef is a malloc'ed copy of the
 * URL string and -l LIBRARY loads a stub that exports SBSOpenSensitiveURLAndUnlock.
 */
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifdef __APPLE__
#include <CoreFoundation/CoreFoundation.h>
#define DEFAULT_LIB "/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices"
static CFURLRef url_make(const char *s)
{
	CFStringRef str = CFStringCreateWithCString(kCFAllocatorDefault, s, kCFStringEncodingUTF8);
	if (!str)
		return NULL;
	CFURLRef u = CFURLCreateWithString(kCFAllocatorDefault, str, NULL);
	CFRelease(str);
	return u;
}
static void url_free(CFURLRef u)
{
	CFRelease(u);
}
#else
typedef const void *CFURLRef;
#define DEFAULT_LIB "libSpringBoardServices-missing.so"
static CFURLRef url_make(const char *s)
{
	return strdup(s);
}
static void url_free(CFURLRef u)
{
	free((void *)u);
}
#endif

#define DEFAULT_URL "prefs:root=WIFI"
#define DEFAULT_DELAY 60
#define MAX_DELAY 3600
#define MAX_TRIES 20
#define RETRY_GAP_S 5

typedef unsigned char (*open_fn)(CFURLRef url, unsigned char unlock);

static void usage(void)
{
	printf("pixopen: GAP usage: pixopen [-d delay_s] [-r tries] [-n] [-l LIBRARY] [URL]\n");
}

static int parse_int(const char *s, int lo, int hi, int *out)
{
	char *end;
	errno = 0;
	long v = strtol(s, &end, 10);
	if (errno || end == s || *end || v < lo || v > hi)
		return -1;
	*out = (int)v;
	return 0;
}

/* sleep n seconds, one progress line per 10 s so the console shows we are alive */
static void wait_s(int n)
{
	for (int left = n; left > 0;) {
		int step = left > 10 ? 10 : left;
		sleep((unsigned)step);
		left -= step;
		if (left > 0)
			printf("pixopen: waiting %d s more\n", left);
	}
}

int main(int argc, char **argv)
{
	int delay = DEFAULT_DELAY, tries = 1, unlock = 1, opt;
	const char *lib = DEFAULT_LIB, *url = DEFAULT_URL;

	setvbuf(stdout, NULL, _IONBF, 0);
	while ((opt = getopt(argc, argv, "d:r:nl:")) != -1) {
		switch (opt) {
		case 'd':
			if (parse_int(optarg, 0, MAX_DELAY, &delay)) {
				printf("pixopen: GAP bad -d '%s' (want 0..%d)\n", optarg, MAX_DELAY);
				usage();
				return 2;
			}
			break;
		case 'r':
			if (parse_int(optarg, 1, MAX_TRIES, &tries)) {
				printf("pixopen: GAP bad -r '%s' (want 1..%d)\n", optarg, MAX_TRIES);
				usage();
				return 2;
			}
			break;
		case 'n':
			unlock = 0;
			break;
		case 'l':
			lib = optarg;
			break;
		default:
			usage();
			return 2;
		}
	}
	if (argc - optind > 1) {
		printf("pixopen: GAP more than one URL given\n");
		usage();
		return 2;
	}
	if (optind < argc)
		url = argv[optind];
	if (!*url) {
		printf("pixopen: GAP empty URL\n");
		usage();
		return 2;
	}

	printf("pixopen: start url=%s delay=%d tries=%d unlock=%d\n", url, delay, tries, unlock);
	wait_s(delay);

	void *h = dlopen(lib, RTLD_NOW);
	if (!h) {
		const char *e = dlerror();
		printf("pixopen: GAP dlopen %s failed: %s\n", lib, e ? e : "(no dlerror)");
		return 3;
	}
	open_fn fn = (open_fn)dlsym(h, "SBSOpenSensitiveURLAndUnlock");
	if (!fn) {
		const char *e = dlerror();
		printf("pixopen: GAP dlsym SBSOpenSensitiveURLAndUnlock missing in %s: %s\n", lib, e ? e : "(no dlerror)");
		return 3;
	}

	int rc = 0;
	for (int t = 1; t <= tries; t++) {
		CFURLRef u = url_make(url);
		if (!u) {
			printf("pixopen: GAP cannot build a URL from '%s'\n", url);
			return 4;
		}
		rc = fn(u, (unsigned char)unlock);
		url_free(u);
		printf("pixopen: open %s rc=%d\n", url, rc);
		if (rc)
			return 0;
		printf("pixopen: GAP open %s returned false (try %d of %d)%s\n", url, t, tries,
		       t < tries ? ", retrying" : "");
		if (t < tries)
			sleep(RETRY_GAP_S);
	}
	return 1;
}
