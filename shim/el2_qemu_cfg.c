/*
 * QEMU nested-fault test (tools/el2-qemu-test), C side: the stage-2 map configuration of the QEMU `virt` machine (S4 of the spec's open list) and no-op console fakes for el2.c.
 * virt RAM is 0x40000000 + 128 MiB (`info mtree`, QEMU 10.0.13); T2 is 0x0c000000, a hole of the virt map (no region there, so an access aborts: the control read of RAM must work, the hole read must fault).
 */
#ifdef EL2_TRAP

#include "el2.h"

static const struct el2_bank banks[] = { { 0x40000000ull, 0x8000000ull } };

const struct el2_cfg *el2_cfg_get(void)
{
	static struct el2_cfg c;
	c.banks = banks;
	c.nbanks = 1;
	c.vtcr = EL2_VTCR;
	c.t2_ipa = 0x0c000000ull;
	return &c;
}

void con_str(const char *l, const char *s) { (void)l; (void)s; }
void con_hex(const char *l, uint64_t v) { (void)l; (void)v; }
void con_hex2(const char *l, const char *a, uint32_t va, const char *b, uint32_t vb) { (void)l; (void)a; (void)va; (void)b; (void)vb; }

#endif
