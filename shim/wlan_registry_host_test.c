/* wlan_registry_host_test.c - host test of shim/wlan_registry.h (P-W4, T-2144; SPEC-WIFI Part F rows W1-W3). No IOKit: the tables are compared with the rows cited in
 * the header, and the consumers (wifid, configd, MobileGestalt) are modelled as the small predicates the reversers read out of the binaries. Each predicate is also run on
 * a mutated copy of the tables to prove the test notices (R110 s.4.1, W-D7 D-1..D-10).
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "wlan_registry.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static const struct wr_prop *P(const struct wr_obj *o, const char *k) { return wr_find(o, k); }
static int str_is(const struct wr_obj *o, const char *k, const char *v)
{
	const struct wr_prop *p = P(o, k);
	return p != NULL && p->kind == WR_STR && p->str != NULL && strcmp(p->str, v) == 0;
}
static int num_is(const struct wr_obj *o, const char *k, uint64_t v)
{
	const struct wr_prop *p = P(o, k);
	return p != NULL && p->kind == WR_NUM && p->num == v;
}

/* wifid IOServiceAddMatchingNotification dictionary {IOPropertyMatch: {IO80211InterfaceRole: "Infrastructure"}} (R110 s.4.1, dict 0x100238c38): no class key */
static int wifid_matches(const struct wr_obj *o) { return str_is(o, "IO80211InterfaceRole", "Infrastructure"); }

/* WiFiDeviceCreate (D-1): IOInterfaceName, else IOInterfaceNamePrefix (CFString) + IOInterfaceUnit (CFNumber) joined "%@%@"; NULL = "Could not obtain ifPrefix and ifUnit" */
static const char *wifid_ifname(const struct wr_obj *o, char *buf, size_t n, int use_name)
{
	const struct wr_prop *name = P(o, "IOInterfaceName"), *pre = P(o, "IOInterfaceNamePrefix"), *unit = P(o, "IOInterfaceUnit");
	if (use_name) {
		return (name != NULL && name->kind == WR_STR) ? name->str : NULL;
	}
	if (pre == NULL || pre->kind != WR_STR || unit == NULL || unit->kind != WR_NUM) {
		return NULL;
	}
	snprintf(buf, n, "%s%llu", pre->str, (unsigned long long)unit->num);
	return buf;
}

/* configd _processNetworkInterface (D-9, D-10): IOInterfaceType of the INTERFACE must be 6; the PARENT must conform to IO80211Controller; the parent's IOMACAddress must be CFData */
static int configd_types_ieee80211(const struct wr_obj *iface, const struct wr_obj *parent)
{
	const struct wr_prop *mac = P(parent, "IOMACAddress");
	return num_is(iface, "IOInterfaceType", 6) && wr_conforms(parent, "IO80211Controller") && mac != NULL && mac->kind == WR_ADT_DATA && mac->len == 6;
}

/* SCNetworkInterfaceCopyAll_IONetworkInterface (D-8): IOServiceMatching("IONetworkInterface") -> metaCast over the class chain */
static int sc_lists(const struct wr_obj *iface) { return wr_conforms(iface, "IONetworkInterface"); }

static void test_rows(void)
{
	/* R110 s.8.1 row 3 / s.4.1: role "Infrastructure" (string) and IOInterfaceName "en0" (string), on the interface */
	CHECK(str_is(&wr_interface, "IO80211InterfaceRole", "Infrastructure"));
	CHECK(str_is(&wr_interface, "IOInterfaceName", "en0"));
	/* or prefix "en" + unit 0 (number) */
	CHECK(str_is(&wr_interface, "IOInterfaceNamePrefix", "en") && num_is(&wr_interface, "IOInterfaceUnit", 0));
	/* D-9: IOInterfaceType == 6 */
	CHECK(num_is(&wr_interface, "IOInterfaceType", 6));
	/* D-8/D-10: the controller (parent) carries IOMACAddress, 6 bytes, from /chosen mac-address-wifi0 (R110 s.7, mkadt.py) */
	{
		const struct wr_prop *m = P(&wr_controller, "IOMACAddress");
		CHECK(m != NULL && m->kind == WR_ADT_DATA && m->len == 6 && strcmp(m->str, "/chosen mac-address-wifi0") == 0);
	}
	/* property set is exactly the cited one: nothing invented */
	CHECK(wr_interface.nprops == 5 && wr_controller.nprops == 1);
	CHECK(P(&wr_interface, "IOMACAddress") == NULL);           /* configd reads it from the PARENT, never the interface (D-8) */
	CHECK(P(&wr_controller, "IO80211InterfaceRole") == NULL);  /* else wifid would create two devices (D-3 matches the role only) */
	CHECK(P(&wr_controller, "IOInterfaceName") == NULL);
	/* untraced reads stay unpublished and are named in the gaps, never silently */
	{
		static const char *const untraced[] = { "BSD Name", "IOPrimaryInterface", "IOLocation", "IOFeatures", "SelfNamed", "IOBuiltin" };
		static const char *const live[] = { "BSD Name", "IOPrimaryInterface", "IOLocation", "IOBuiltin" };     /* W-UI: set once the ifnet exists, from wr_live_props (not at publish) */
		unsigned i, j, found;
		for (i = 0; i < sizeof untraced / sizeof untraced[0]; i++) {
			CHECK(P(&wr_interface, untraced[i]) == NULL && P(&wr_controller, untraced[i]) == NULL);
			found = 0;
			for (j = 0; j < WR_NGAPS; j++) {
				if (strstr(wr_gaps[j], untraced[i]) != NULL) { found = 1; }
			}
			CHECK(found);
		}
		/* W-UI: the live set (after en0_attach): the four keys, exact kinds/values, none of them in the publish-time tables, no duplicates */
		CHECK(WR_NLIVE == 4);
		for (i = 0; i < sizeof live / sizeof live[0]; i++) {
			unsigned n = 0;
			for (j = 0; j < WR_NLIVE; j++) { if (strcmp(wr_live_props[j].key, live[i]) == 0) { n++; } }
			CHECK(n == 1);
		}
		CHECK(strcmp(wr_live_props[0].key, "BSD Name") == 0 && wr_live_props[0].kind == WR_STR && wr_live_props[0].str == NULL);        /* kIOBSDNameKey (IOBSD.h:36), the ifnet's name at run time */
		CHECK(strcmp(wr_live_props[1].key, "IOPrimaryInterface") == 0 && wr_live_props[1].kind == WR_BOOL && wr_live_props[1].num == 1);
		CHECK(strcmp(wr_live_props[2].key, "IOBuiltin") == 0 && wr_live_props[2].kind == WR_BOOL && wr_live_props[2].num == 1);
		CHECK(strcmp(wr_live_props[3].key, "IOLocation") == 0 && wr_live_props[3].kind == WR_STR && wr_live_props[3].str != NULL && wr_live_props[3].str[0] == 0);
		CHECK(strcmp(WR_BSD_NAME_KEY, "BSD Name") == 0);
	}
	/* keys unique per object */
	{
		size_t i, j;
		for (i = 0; i < wr_interface.nprops; i++) {
			for (j = i + 1; j < wr_interface.nprops; j++) { CHECK(strcmp(wr_interface.props[i].key, wr_interface.props[j].key) != 0); }
		}
	}
}

static void test_classes(void)
{
	/* configd (D-9) and MobileWiFi (D-16): IO80211Controller in the controller's chain; MG name matches are by node name, not class */
	CHECK(wr_conforms(&wr_controller, "IO80211Controller") && wr_conforms(&wr_controller, "IONetworkController") && wr_conforms(&wr_controller, "IOService"));
	CHECK(strcmp(wr_controller.chain[0], "IOService") == 0 && strcmp(wr_controller.chain[1], "IONetworkController") == 0 &&
	      strcmp(wr_controller.chain[2], "IO80211Controller") == 0 && strcmp(wr_controller.chain[3], "SweetWLANController") == 0 && wr_controller.chain[4] == NULL);
	CHECK(wr_conforms(&wr_interface, "IONetworkInterface") && !wr_conforms(&wr_interface, "IO80211Controller"));
	CHECK(strcmp(wr_interface.chain[0], "IOService") == 0 && strcmp(wr_interface.chain[2], "SweetWLANInterface") == 0 && wr_interface.chain[3] == NULL);
}

static void test_consumers(void)
{
	char buf[32];
	const char *n;

	CHECK(wifid_matches(&wr_interface) && !wifid_matches(&wr_controller));
	n = wifid_ifname(&wr_interface, buf, sizeof buf, 1);
	CHECK(n != NULL && strcmp(n, "en0") == 0);
	n = wifid_ifname(&wr_interface, buf, sizeof buf, 0);
	CHECK(n != NULL && strcmp(n, "en0") == 0);                 /* the prefix+unit route gives the same name */
	CHECK(configd_types_ieee80211(&wr_interface, &wr_controller));
	CHECK(sc_lists(&wr_interface));
}

/* mutants: each predicate must reject a table that breaks its rule */
static void test_mutants(void)
{
	struct wr_prop ip[5], cp[1];
	struct wr_obj mi, mc;
	char buf[32];
	size_t i;

	memcpy(ip, wr_interface_props, sizeof ip);
	memcpy(cp, wr_controller_props, sizeof cp);

	mi = wr_interface; mi.props = ip;
	mc = wr_controller; mc.props = cp;
	CHECK(wifid_matches(&mi) && configd_types_ieee80211(&mi, &mc));

	for (i = 0; i < 5; i++) {                                  /* role value changed (case) */
		if (strcmp(ip[i].key, "IO80211InterfaceRole") == 0) { ip[i].str = "infrastructure"; }
	}
	CHECK(!wifid_matches(&mi));
	memcpy(ip, wr_interface_props, sizeof ip);

	for (i = 0; i < 5; i++) {                                  /* key renamed */
		if (strcmp(ip[i].key, "IO80211InterfaceRole") == 0) { ip[i].key = "IO80211VirtualInterfaceRole"; }
	}
	CHECK(!wifid_matches(&mi));
	memcpy(ip, wr_interface_props, sizeof ip);

	for (i = 0; i < 5; i++) {                                  /* name not "en0" => a different BSD name than the ifnet */
		if (strcmp(ip[i].key, "IOInterfaceName") == 0) { ip[i].str = "wlan0"; }
	}
	CHECK(!str_is(&mi, "IOInterfaceName", "en0"));
	memcpy(ip, wr_interface_props, sizeof ip);

	for (i = 0; i < 5; i++) {                                  /* unit became a string => the prefix+unit route must fail (D-1 needs a CFNumber) */
		if (strcmp(ip[i].key, "IOInterfaceUnit") == 0) { ip[i].kind = WR_STR; ip[i].str = "0"; }
	}
	CHECK(wifid_ifname(&mi, buf, sizeof buf, 0) == NULL);
	memcpy(ip, wr_interface_props, sizeof ip);

	for (i = 0; i < 5; i++) {                                  /* type not 6 => configd types it as something else (D-9) */
		if (strcmp(ip[i].key, "IOInterfaceType") == 0) { ip[i].num = 7; }
	}
	CHECK(!configd_types_ieee80211(&mi, &mc));
	memcpy(ip, wr_interface_props, sizeof ip);

	mc.chain[2] = "IONetworkController";                      /* controller without IO80211Controller in its chain => wired Ethernet branch */
	CHECK(!configd_types_ieee80211(&mi, &mc));
	mc = wr_controller; mc.props = cp;

	cp[0].len = 4;                                             /* MAC of the wrong size */
	CHECK(!configd_types_ieee80211(&mi, &mc));
	cp[0] = wr_controller_props[0];

	mi.chain[1] = "IOService";                                 /* interface not an IONetworkInterface => SCNetworkInterfaceCopyAll does not list it */
	CHECK(!sc_lists(&mi));
}

int main(void)
{
	size_t g;
	test_rows(); test_classes(); test_consumers(); test_mutants();
	for (g = 0; g < WR_NGAPS; g++) {
		CHECK(strncmp(wr_gaps[g], "WIFI GAP: W", 11) == 0);    /* rule 8: every open item is a visible GAP line with its Part F row */
	}
	if (fails) { printf("RESULT: FAIL wlan_registry_host_test fails=%d\n", fails); return 1; }
	printf("RESULT: PASS wlan_registry_host_test\n");
	return 0;
}
