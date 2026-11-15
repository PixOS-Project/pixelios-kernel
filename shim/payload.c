#include "payload.h"
#include "shim_assert.h"

uintptr_t shim_find_magic(uintptr_t start, uintptr_t end, const char *magic, unsigned len)
{
	if (!len || end < start || end - start < len)
		return 0;
	for (uintptr_t p = start; p <= end - len; p++) {
		unsigned i;
		for (i = 0; i < len; i++)
			if (*(volatile const uint8_t *)(p + i) != (uint8_t)magic[i])
				break;
		if (i == len)
			return p;
	}
	return 0;
}

/* Boot speed (2026-11-09): our payload is the boot ramdisk, which ABL loads LAST (boot image
 * header v4: vendor ramdisk(s) first, bootconfig may follow), so its header sits at most
 * `window` bytes below hi - size. Same byte compares as shim_find_magic, top down; a hit must
 * also pass payload_check. 0 = not found there (the caller then scans the whole range).
 * Reason: the full scan from lo took ~30 s per boot with the MMU off (bt-mgr6 camera). */
uintptr_t shim_find_payload_tail(uintptr_t lo, uintptr_t hi, uint64_t size, uint64_t window)
{
	if (!size || hi < lo || hi - lo < size || size < sizeof(struct payload))
		return 0;
	uintptr_t top = hi - size;
	uintptr_t bottom = (top - lo > window) ? top - window : lo;
	SHIM_ASSERT(top >= lo && top <= hi);
	SHIM_ASSERT(bottom >= lo && bottom <= top);
	for (uintptr_t p = top + 1; p-- > bottom;) {
		uint32_t i;
		for (i = 0; i < 8; i++)
			if (*(volatile const uint8_t *)(p + i) != (uint8_t)"SWEETXNU"[i])
				break;
		if (i == 8 && (p & 7) == 0 && hi - p >= sizeof(struct payload) &&
		    !payload_check((const struct payload *)p))
			return p;
	}
	return 0;
}

const char *payload_check(const struct payload *p)
{
	/* An image packed by another mkpayload (other cmdline size, other seg table offset): never boot it. */
	if (p->version != PAYLOAD_VERSION)
		return "payload version mismatch: shim and tools/mkpayload.py disagree (rebuild with tools/build-xnu-image.sh)";
	/* mkpayload always writes the NUL; do not hand XNU an unterminated boot-args line (review bootline-1024-r1 F3). */
	if (p->cmdline[BOOT_LINE_LENGTH - 1])
		return "payload cmdline not NUL-terminated";
	return 0;
}

uint32_t payload_seg_src_ok(const struct payload_seg *s, uint64_t lo, uint64_t hi)
{
	uint64_t n;

	SHIM_ASSERT(s != 0);
	n = (s->file_size + 7) & ~7ULL;
	SHIM_ASSERT(n >= s->file_size || s->file_size > ~0ULL - 7);	/* n wraps only past that */
	if (s->file_size == 0)
		return 1;	/* nothing is read or written for an empty segment */
	if (s->file_size > ~0ULL - 7 || (s->file_off & 7) != 0)
		return 0;
	return s->file_off >= lo && lo <= hi && n <= hi - lo && s->file_off <= hi - n;
}

const char *payload_segs_check(const struct payload *p, uint64_t avail, uint32_t *bad)
{
	uint64_t lo;
	uint32_t i;

	SHIM_ASSERT(p != 0 && bad != 0);
	SHIM_ASSERT(avail >= sizeof(struct payload));
	*bad = ~0U;
	if (p->nsegs > (avail - sizeof(struct payload)) / sizeof(struct payload_seg))
		return "payload segment table outside the initrd";
	if (p->adt_off > avail || p->adt_size > avail - p->adt_off)
		return "payload ADT outside the initrd";
	lo = sizeof(struct payload) + (uint64_t)p->nsegs * sizeof(struct payload_seg);
	for (i = 0; i < p->nsegs; i++) {
		if (!payload_seg_src_ok(&p->segs[i], lo, p->adt_off)) {
			*bad = i;
			return "payload segment overlaps the header, the ADT or another segment, "
			       "or lies outside the initrd";
		}
		if (p->segs[i].file_size)
			lo = p->segs[i].file_off + ((p->segs[i].file_size + 7) & ~7ULL);
	}
	SHIM_ASSERT(*bad == ~0U && lo <= avail);
	return 0;
}

int payload_ranges_overlap(uint64_t a_lo, uint64_t a_hi, uint64_t b_lo, uint64_t b_hi)
{
	return a_lo < a_hi && b_lo < b_hi && a_hi > b_lo && a_lo < b_hi;	/* half-open [lo, hi) ranges; an empty range overlaps nothing */
}

uint64_t payload_bootflags(int resident, uint32_t s2_on)
{
	return resident && s2_on == 1 ? kBootFlagsSweetEL2 : 0;
}

void payload_fill_boot_args(volatile struct boot_args *ba, const struct payload *p, uint64_t top, uint64_t dt_va, uint64_t bootflags)
{
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->deviceTreeP = dt_va;
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->bootFlags = bootflags;
	ba->memSizeActual = p->mem_size;
}
