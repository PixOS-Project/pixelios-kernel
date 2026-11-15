/* wlan_registry.h - the IORegistry objects wifid and configd look for, as plain tables (P-W4, T-2144; SPEC-WIFI Part F rows W1-W3).
 *
 * Pure C, no IOKit: the C++ glue (wlan_registry_glue.cpp.in -> xnu patch 0200) walks these tables and calls setProperty(), so the names and values below
 * are exactly what the host test (shim/wlan_registry_host_test.c) compares against the cited rows. Nothing here is a hardware constant.
 *
 * Sources (obs-RE = read from the iOS 16.7 binaries by the reversers):
 *   docs/research/110-apple80211-io80211-ios167.md s.4.1 (wifid discovery), s.4.4 (configd), s.7 (MobileGestalt), s.8.1 rows 1-3
 *   docs/research/W-D7-wifid-startup-reads.md D-1 (interface reads), D-2 (parent), D-3 (complete wifid registry reads), D-8..D-10 (SystemConfiguration), D-16
 * The objects (what is published):
 *   controller  class chain IOService > IONetworkController > IO80211Controller > SweetWLANController, attached under the IOService root,
 *               property IOMACAddress (6 bytes, CFData) read by configd from the interface's PARENT (D-8, D-10).
 *   interface   class chain IOService > IONetworkInterface > SweetWLANInterface, attached under the controller, published with registerService().
 * Every property that the reversers saw read but whose value is not decided is listed in wr_gaps[] and printed as a `WIFI GAP:` line at publish time (rule 8).
 */
#ifndef WLAN_REGISTRY_H
#define WLAN_REGISTRY_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>

enum wr_kind { WR_STR = 1, WR_NUM = 2, WR_BOOL = 3, WR_ADT_DATA = 4 };

struct wr_prop {
	const char *key;
	enum wr_kind kind;
	const char *str;        /* WR_STR value; WR_ADT_DATA: "<adt path> <property>" the 6 bytes come from at publish time */
	uint64_t num;           /* WR_NUM / WR_BOOL value */
	uint32_t bits;          /* WR_NUM width, as setProperty(key, value, bits) */
	uint32_t len;           /* WR_ADT_DATA: expected length in bytes */
};

#define WR_MAXCHAIN 6u

struct wr_obj {
	const char *name;                       /* what the glue calls it in logs */
	const char *chain[WR_MAXCHAIN];         /* class chain root first, leaf last, NULL padded */
	const struct wr_prop *props;
	size_t nprops;
};

/* The ADT inputs of the same ticket (tools/mkadt.py): launchd's device-tree gate and the MAC source. */
#define WR_ADT_GATE_NAME  "wlan"            /* IODTFindMatchingEntries(root, kIODTRecursive, "wlan"): the gate launchd evaluates (R110 s.1) */
#define WR_MAC_SRC        "/chosen mac-address-wifi0"
#define WR_MAC_LEN        6u
#define WR_BOOTARG        "sweetwlanreg"    /* =0 skips the whole publish (A/B runs); default publish */

#define WR_IFNAME_PREFIX  "en"
#define WR_IFNAME_UNIT    0u
#define WR_IFNAME         "en0"             /* R110 s.4.1: IOInterfaceName="en0" (or prefix "en" + unit 0) */
#define WR_IFTYPE_ETH     6u                /* D-9: IOInterfaceType == 6 (ethernet family) is the precondition of the IO80211Controller conformance test */
#define WR_ROLE           "Infrastructure"  /* wifid match dict 0x100238c38 and D-3 compare string 0x1000903a8 */

static const struct wr_prop wr_controller_props[] = {
	/* D-8/D-10: configd reads IOMACAddress from the parent of the interface and requires CFData; 6 bytes copied from /chosen mac-address-wifi0 by the glue. */
	{ "IOMACAddress", WR_ADT_DATA, WR_MAC_SRC, 0, 0, WR_MAC_LEN },
};

static const struct wr_prop wr_interface_props[] = {
	{ "IOInterfaceName",       WR_STR, WR_IFNAME,        0, 0, 0 },                      /* D-1: first choice of WiFiDeviceCreate */
	{ "IOInterfaceNamePrefix", WR_STR, WR_IFNAME_PREFIX, 0, 0, 0 },                      /* D-1 fallback pair, also read by configd (D-8, D-10) */
	{ "IOInterfaceUnit",       WR_NUM, NULL,             WR_IFNAME_UNIT, 32, 0 },        /* D-1: must be a number; same keys read by configd */
	{ "IOInterfaceType",       WR_NUM, NULL,             WR_IFTYPE_ETH, 8, 0 },          /* D-9: tested == 6 before the IO80211Controller conformance */
	{ "IO80211InterfaceRole",  WR_STR, WR_ROLE,          0, 0, 0 },                      /* wifid IOPropertyMatch key (R110 s.4.1, D-3) */
};

/* W-UI (sprint 2026-11-08): the properties that exist only once the en0 ifnet does, set by sweet_wlan_registry_set_bsd_name() after en0_attach (xnu patch 0268 keep hand-over). The glue fills WR_BSD_NAME with the
 * ifnet's name (if_name). Sources: "BSD Name" = kIOBSDNameKey, iokit/IOKit/IOBSD.h:36 (an OSString) of the XNU tree, read by configd through copyIORegistryProperties / IODictionaryCopyBSDName (D-8, D-10);
 * IOPrimaryInterface is in the 11 interface keys configd reads (D-8; a Boolean, the value true is [guess]: how configd uses it is not traced); IOBuiltin: D-10 says an IEEE80211 interface is forced builtin and the
 * property is optional, set true anyway [no harm]; IOLocation: D-10 drops an empty string (0x19064a954-0x19064a97c), so '' is the "no location" answer. All four are interface-side reads (D-8). */
#define WR_BSD_NAME_KEY "BSD Name"
#define WR_BSD_NAME     NULL            /* the value is the ifnet's name, supplied at run time */
static const struct wr_prop wr_live_props[] = {
	{ WR_BSD_NAME_KEY,       WR_STR,  WR_BSD_NAME, 0, 0, 0 },    /* D-5/D-8/D-10: the key configd needs to list en0 */
	{ "IOPrimaryInterface",  WR_BOOL, NULL,        1, 0, 0 },    /* D-8 (value is a guess) */
	{ "IOBuiltin",           WR_BOOL, NULL,        1, 0, 0 },    /* D-8/D-10 */
	{ "IOLocation",          WR_STR,  "",          0, 0, 0 },    /* D-8/D-10: an empty string is dropped by configd */
};
#define WR_NLIVE (sizeof wr_live_props / sizeof wr_live_props[0])

static const struct wr_obj wr_controller = {
	"controller",
	{ "IOService", "IONetworkController", "IO80211Controller", "SweetWLANController", NULL, NULL },
	wr_controller_props, sizeof wr_controller_props / sizeof wr_controller_props[0],
};

static const struct wr_obj wr_interface = {
	"interface",
	{ "IOService", "IONetworkInterface", "SweetWLANInterface", NULL, NULL, NULL },
	wr_interface_props, sizeof wr_interface_props / sizeof wr_interface_props[0],
};

/* Reads that the reversers saw and whose value this ticket does not choose; the glue prints each as a WIFI GAP line when it publishes. */
static const char *const wr_gaps[] = {
	"WIFI GAP: W3 BSD Name, IOPrimaryInterface, IOBuiltin and IOLocation exist only once the en0 ifnet does: they are set by sweet_wlan_registry_set_bsd_name (patch 0268 keep hand-over, line `SWEET WIFI: registry`); without it configd D-5 will not list en0",
	"WIFI GAP: W3 IOFeatures and SelfNamed (parent) are read by configd (D-8, D-10) but no value is traced as required; none is published",
	"WIFI GAP: W3 IO80211ControllerInfo and the IO80211Skywalk interface properties are named in CoreWiFi strings only (D-17 open); none is published",
	"WIFI GAP: W3 no IOUserClient: IOServiceOpen on the controller/interface fails (W6)",
};
#define WR_NGAPS (sizeof wr_gaps / sizeof wr_gaps[0])

static inline const struct wr_prop *wr_find(const struct wr_obj *o, const char *key)
{
	size_t i;
	for (i = 0; i < o->nprops; i++) {
		if (strcmp(o->props[i].key, key) == 0) {
			return &o->props[i];
		}
	}
	return NULL;
}

/* class chain contains `cls` (what IOObjectConformsTo / IOServiceMatching's metaCast evaluate) */
static inline int wr_conforms(const struct wr_obj *o, const char *cls)
{
	unsigned i;
	for (i = 0; i < WR_MAXCHAIN && o->chain[i] != NULL; i++) {
		if (strcmp(o->chain[i], cls) == 0) {
			return 1;
		}
	}
	return 0;
}

#endif /* WLAN_REGISTRY_H */
