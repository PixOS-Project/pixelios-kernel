/*
 * --el2-trap: the shim's stage-2 map configuration (what el2_build() maps): board.json dram_banks and the trap target T2 from the generated board_config.h (tools/boards.py).
 * Built at run time: a static table of pointers would be an absolute relocation and the shim's load address is not known (build.sh rejects ABS64).
 */
#ifdef EL2_TRAP

#include "el2.h"

#ifndef EL2_DRAM_BANKS
#error "build with -include board_config.h (generated EL2_* macros, tools/boards.py)"
#endif

static const struct el2_bank banks[] = { EL2_DRAM_BANKS };
_Static_assert(sizeof(banks) / sizeof(banks[0]) == EL2_DRAM_NBANKS, "EL2_DRAM_NBANKS matches EL2_DRAM_BANKS");

const struct el2_cfg *el2_cfg_get(void)
{
	static struct el2_cfg c;
	c.banks = banks;
	c.nbanks = EL2_DRAM_NBANKS;
	c.vtcr = EL2_VTCR;
	c.t2_ipa = EL2_T2_IPA;
	return &c;
}

#endif
