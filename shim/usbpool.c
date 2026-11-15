#include "usbpool.h"

static int overlaps(uint64_t b1, uint64_t e1, uint64_t b2, uint64_t e2)
{
	return b1 < e2 && b2 < e1;
}

static int has_prefix(const char *s, const char *prefix)
{
	/* bounded: prefix is a string literal, ends at its NUL */
	while (*prefix) {
		if (*s++ != *prefix++)
			return 0;
	}
	return 1;
}

int usbpool_choose(const struct fdt_memmap *mm, uint64_t gevntadr,
		   size_t pool_len, const char *required_name,
		   const struct usbpool_avoid *avoid, unsigned n_avoid,
		   uint64_t *pool_out, const char **why)
{
	uint64_t pool, end;
	int inside = 0;

	if (!gevntadr) {
		*why = "GEVNTADR is zero: no ABL pool";
		return 1;
	}
	pool = gevntadr & ~0xFFFull;
	end = pool + pool_len;
	if (!pool_len || end <= pool) {
		*why = "pool range empty or wraps";
		return 1;
	}
	if (mm->n_reserved_dropped) {
		*why = "reserved table truncated";
		return 1;
	}
	for (unsigned i = 0; i < mm->n_reserved && i < FDT_MAX_RESERVED; i++) {
		uint64_t rb = mm->reserved[i].base;
		uint64_t re = rb + mm->reserved[i].size;
		if (re > rb && rb <= pool && end <= re &&
		    (!required_name || has_prefix(mm->reserved[i].name, required_name))) {
			inside = 1;
			break;
		}
	}
	if (!inside) {
		*why = required_name ? "pool not inside the named reserved range"
		                     : "pool not inside one reserved range";
		return 1;
	}
	for (unsigned i = 0; i < n_avoid; i++) {
		if (avoid[i].hi > avoid[i].lo &&
		    overlaps(pool, end, avoid[i].lo, avoid[i].hi)) {
			*why = avoid[i].what;
			return 1;
		}
	}
	*pool_out = pool;
	return 0;
}

#ifdef USB_DMA_HI_BASE
/* Dev experiment: pool at a fixed PA inside a /memory bank (XNU's 0x88.. RAM).
 * Fail closed: unaligned, empty/wrapping, reserved table truncated, not
 * wholly inside one bank, overlapping any reserved entry or avoid[] range. */
int usbpool_choose_hi(const struct fdt_memmap *mm, uint64_t base,
		      size_t pool_len, const struct usbpool_avoid *avoid,
		      unsigned n_avoid, uint64_t *pool_out, const char **why)
{
	uint64_t end = base + pool_len;
	int inside = 0;

	if ((base & 0xFFFull) || !pool_len || end <= base) {
		*why = "hi pool unaligned, empty or wraps";
		return 1;
	}
	if (mm->n_reserved_dropped) {
		*why = "reserved table truncated";
		return 1;
	}
	for (unsigned i = 0; i < mm->n_banks && i < FDT_MAX_BANKS; i++) {
		uint64_t bb = mm->banks[i].base, be = bb + mm->banks[i].size;
		if (be > bb && bb <= base && end <= be)
			inside = 1;
	}
	if (!inside) {
		*why = "hi pool not inside one memory bank";
		return 1;
	}
	for (unsigned i = 0; i < mm->n_reserved && i < FDT_MAX_RESERVED; i++) {
		uint64_t rb = mm->reserved[i].base;
		if (overlaps(base, end, rb, rb + mm->reserved[i].size)) {
			*why = "hi pool overlaps a reserved range";
			return 1;
		}
	}
	for (unsigned i = 0; i < n_avoid; i++) {
		if (avoid[i].hi > avoid[i].lo &&
		    overlaps(base, end, avoid[i].lo, avoid[i].hi)) {
			*why = avoid[i].what;
			return 1;
		}
	}
	*pool_out = base;
	return 0;
}
#endif
