/*
 * Flattened device tree parser with cell-size handling and totalsize bounds.
 * Big-endian, 4-byte aligned tokens.
 */
#include "fdt.h"
#include "console.h"
#include "shim_assert.h"

#define FDT_MAGIC	0xd00dfeed
#define FDT_BEGIN_NODE	1
#define FDT_END_NODE	2
#define FDT_PROP	3
#define FDT_NOP		4
#define FDT_END		9

#define MAX_DEPTH	16

/* Device trees are small; a bigger claimed totalsize is corruption that
 * could push the walker past DRAM into MMIO (which no handler guards). */
#define FDT_MAX_SIZE	(2u * 1024 * 1024)

static uint32_t be32(uintptr_t a)
{
	return __builtin_bswap32(*(volatile uint32_t *)a);
}

static int streq(const char *a, const char *b)
{
	/* bounded: ends at the first NUL or mismatch, so by the shorter string */
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static int has_text(const char *text, const char *needle)
{
	/* bounded: text is a node name; the node walk (fdt_walk) checked its NUL lies before fdt_end */
	while (*text) {
		const char *a = text, *b = needle;
		/* bounded: ends at the first NUL or mismatch, so by the shorter string */
		while (*a && *b && *a == *b)
			a++, b++;
		if (!*b)
			return 1;
		text++;
	}
	return 0;
}

/* Bounded substring search inside a property value that may not be
 * NUL-terminated; the match must complete within len bytes. */
static int has_text_n(const char *text, uint32_t len, const char *needle)
{
	for (uint32_t i = 0; i < len; i++) {
		uint32_t j = i;
		const char *n = needle;
		while (j < len && *n && text[j] == *n)
			j++, n++;
		if (!*n)
			return 1;
	}
	return 0;
}

/* Exact "memory" or "memory@unit" node name; rejects "reserved-memory"
 * and "memory-controller" so their reg (if any) is never treated as DRAM. */
static int name_is_memory(const char *name)
{
	const char *m = "memory";
	/* bounded: ends at the end of the literal "memory" or the first mismatch */
	while (*m && *name == *m)
		m++, name++;
	return *m == 0 && (*name == 0 || *name == '@');
}

static uintptr_t align4(uintptr_t a)
{
	return (a + 3) & ~3UL;
}

static uint64_t read_cells(uintptr_t p, uint32_t cells)
{
	if (cells == 1)
		return be32(p);
	if (cells == 2)
		return ((uint64_t)be32(p) << 32) | be32(p + 4);
	return 0;
}

struct fdt_walk_state {
	uintptr_t fdt;
	uintptr_t fdt_end;
	uintptr_t strings;
	int depth;
	const char *names[MAX_DEPTH];
	uint32_t root_addr_cells;
	uint32_t root_size_cells;
	uint32_t rsv_addr_cells;
	uint32_t rsv_size_cells;
};

struct fdt_ops {
	int (*begin_node)(struct fdt_walk_state *st, const char *name, void *ctx);
	int (*prop)(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx);
	int (*end_node)(struct fdt_walk_state *st, void *ctx);
};

static uint32_t fdt_active_addr_cells(const struct fdt_walk_state *st)
{
	if (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory"))
		return st->rsv_addr_cells;
	return st->root_addr_cells;
}

static uint32_t fdt_active_size_cells(const struct fdt_walk_state *st)
{
	if (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory"))
		return st->rsv_size_cells;
	return st->root_size_cells;
}

static uint32_t fdt_reg_stride(const struct fdt_walk_state *st)
{
	uint32_t ac = fdt_active_addr_cells(st);
	uint32_t sc = fdt_active_size_cells(st);
	if (ac == 0 || ac > 2 || sc == 0 || sc > 2)
		return 0;
	return (ac + sc) * 4;
}

static int fdt_read_reg_entry(const struct fdt_walk_state *st, uintptr_t val, uint32_t off,
			      uint64_t *base, uint64_t *size)
{
	uint32_t ac = fdt_active_addr_cells(st);
	uint32_t sc = fdt_active_size_cells(st);
	if (ac == 0 || ac > 2 || sc == 0 || sc > 2)
		return 0;
	*base = read_cells(val + off, ac);
	*size = read_cells(val + off + (uintptr_t)ac * 4, sc);
	return 1;
}

/*
 * Walk the tree. Returns 0 on a malformed/truncated/out-of-bounds tree,
 * 1 when a callback matched (nonzero return), and 2 on a complete walk
 * that reached FDT_END without a callback match.
 */
static int fdt_walk(uintptr_t fdt, const struct fdt_ops *ops, void *ctx)
{
	if (!fdt || be32(fdt) != FDT_MAGIC)
		return 0;
	uint32_t totalsize = be32(fdt + 4);
	if (totalsize < 40 || totalsize > FDT_MAX_SIZE)
		return 0;
	uintptr_t fdt_end = fdt + totalsize;
	uintptr_t p = fdt + be32(fdt + 8);
	uintptr_t strings = fdt + be32(fdt + 12);
	if (p >= fdt_end || strings >= fdt_end)
		return 0;

	struct fdt_walk_state st;
	st.fdt = fdt;
	st.fdt_end = fdt_end;
	st.strings = strings;
	st.depth = 0;
	st.root_addr_cells = 2;
	st.root_size_cells = 2;
	st.rsv_addr_cells = 2;
	st.rsv_size_cells = 2;
	for (int i = 0; i < MAX_DEPTH; i++)
		st.names[i] = 0;

	/* bounded: p advances 4 per token and the walk returns once p + 4 > fdt_end */
	for (;;) {
		if (p + 4 > fdt_end)
			return 0;
		uint32_t tok = be32(p);
		p += 4;
		switch (tok) {
		case FDT_BEGIN_NODE: {
			if (st.depth >= MAX_DEPTH - 1)
				return 0;
			const char *name = (const char *)p;
			st.names[st.depth] = name;
			while (p < fdt_end && *(volatile char *)p)
				p++;
			if (p >= fdt_end)
				return 0;
			p = align4(p + 1);
			st.depth++;
			if (st.depth == 1) {
				st.root_addr_cells = 2;
				st.root_size_cells = 2;
			} else if (st.depth == 2 && streq(name, "reserved-memory")) {
				st.rsv_addr_cells = st.root_addr_cells;
				st.rsv_size_cells = st.root_size_cells;
			}
			if (ops && ops->begin_node) {
				int rc = ops->begin_node(&st, name, ctx);
				if (rc)
					return rc;
			}
			break;
		}
		case FDT_END_NODE:
			if (ops && ops->end_node) {
				int rc = ops->end_node(&st, ctx);
				if (rc)
					return rc;
			}
			if (st.depth > 0)
				st.depth--;
			break;
		case FDT_PROP: {
			if (p + 8 > fdt_end)
				return 0;
			uint32_t len = be32(p);
			uint32_t nameoff = be32(p + 4);
			uintptr_t val = p + 8;
			if (strings + nameoff >= fdt_end)
				return 0;
			if (val + len > fdt_end || val + len < val)
				return 0;
			const char *prop = (const char *)(strings + nameoff);
			if (st.depth == 1) {
				if (streq(prop, "#address-cells") && len == 4)
					st.root_addr_cells = be32(val);
				else if (streq(prop, "#size-cells") && len == 4)
					st.root_size_cells = be32(val);
			} else if (st.depth == 2 && st.names[1] && streq(st.names[1], "reserved-memory")) {
				if (streq(prop, "#address-cells") && len == 4)
					st.rsv_addr_cells = be32(val);
				else if (streq(prop, "#size-cells") && len == 4)
					st.rsv_size_cells = be32(val);
			}
			if (ops && ops->prop) {
				int rc = ops->prop(&st, prop, val, len, ctx);
				if (rc)
					return rc;
			}
			p = align4(val + len);
			break;
		}
		case FDT_NOP:
			break;
		case FDT_END:
			return 2;
		default:
			return 0;
		}
	}
}

/* ALL-ON T-06: a malformed tree (walk rc 0) must not read as "absent".
 * fdt_probe_report says so too, but a --boot-xnu handover leaves for XNU in
 * loader_boot() (shim.c) before it runs, while fdt_framebuffer (shim.c),
 * fdt_chosen_u64 (loader.c), fdt_ramoops (pstore.c) and fdt_memory_contains
 * (mmu.c) all run earlier. Every walk below goes through this (the macro),
 * named by its caller; the rc is returned unchanged. */
static int32_t fdt_walk_gap(uintptr_t fdt, const struct fdt_ops *ops, void *ctx, const char *who)
{
	SHIM_ASSERT(ops != 0);
	SHIM_ASSERT(who != 0);
	int32_t rc = fdt_walk(fdt, ops, ctx);
	if (rc == 0)
		con_str("FDT GAP: malformed or truncated tree, read as not found by", who);
	return rc;
}
#define fdt_walk(fdt, ops, ctx) fdt_walk_gap((fdt), (ops), (ctx), __func__)

struct chosen_ctx {
	const char *prop;
	uint64_t *out;
};

static int chosen_prop(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx)
{
	struct chosen_ctx *c = ctx;
	if (st->depth == 2 && st->names[1] && streq(st->names[1], "chosen") && streq(prop, c->prop)) {
		if (len == 4)
			*c->out = be32(val);
		else if (len == 8)
			*c->out = ((uint64_t)be32(val) << 32) | be32(val + 4);
		else
			return 0;
		return 1;
	}
	return 0;
}

int fdt_chosen_u64(uintptr_t fdt, const char *prop, uint64_t *out)
{
	struct chosen_ctx ctx = { prop, out };
	struct fdt_ops ops = { .prop = chosen_prop };
	return fdt_walk(fdt, &ops, &ctx) == 1;
}

struct mem_ctx {
	uint64_t base;
	uint64_t size;
	int found;
};

static int mem_prop(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx)
{
	struct mem_ctx *c = ctx;
	if (st->depth == 2 && st->names[1] && name_is_memory(st->names[1]) && streq(prop, "reg")) {
		uint32_t stride = fdt_reg_stride(st);
		if (!stride)
			return 0;
		for (uint32_t off = 0; off + stride <= len; off += stride) {
			uint64_t bank, bytes;
			if (!fdt_read_reg_entry(st, val, off, &bank, &bytes))
				continue;
			if (c->base >= bank && c->base + c->size <= bank + bytes && bank + bytes >= bank) {
				c->found = 1;
				return 1;
			}
		}
	}
	return 0;
}

int fdt_memory_contains(uintptr_t fdt, uint64_t base, uint64_t size)
{
	if (!size || base + size < base)
		return 0;
	struct mem_ctx ctx = { base, size, 0 };
	struct fdt_ops ops = { .prop = mem_prop };
	fdt_walk(fdt, &ops, &ctx);
	return ctx.found;
}

struct collect_ctx {
	struct fdt_memmap *mm;
	unsigned rsv_node_start;	/* first reserved entry of the current node */
	unsigned rsv_node_no_map;
};

static void copy_name(char *dst, const char *src)
{
	unsigned i;
	for (i = 0; i + 1 < sizeof(((struct fdt_memmap *)0)->reserved[0].name) && src[i]; i++)
		dst[i] = src[i];
	dst[i] = 0;
}

static int collect_begin(struct fdt_walk_state *st, const char *name, void *ctx)
{
	struct collect_ctx *c = ctx;
	if (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory")) {
		c->rsv_node_start = c->mm->n_reserved;
		c->rsv_node_no_map = 0;
		(void)name;
	}
	return 0;
}

static int collect_prop(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx)
{
	struct collect_ctx *c = ctx;
	int memory = (st->depth == 2 && st->names[1] && name_is_memory(st->names[1]));
	int reserved = (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory"));
	if (!memory && !reserved)
		return 0;
	if (reserved && streq(prop, "no-map") && len == 0) {
		/* Applies to every reg tuple of this node, whatever order the
		 * properties appear in: mark entries already added and remember
		 * the flag for tuples still to come. */
		for (unsigned i = c->rsv_node_start; i < c->mm->n_reserved; i++)
			c->mm->reserved[i].no_map = 1;
		c->rsv_node_no_map = 1;
		return 0;
	}
	if (!streq(prop, "reg"))
		return 0;
	uint32_t stride = fdt_reg_stride(st);
	if (!stride) {
		/* Unsupported #cells: this reg is invisible to the table.
		 * Count it like an overflow so safety gates fail closed
		 * instead of trusting a map with a hole (review N5). */
		if (reserved)
			c->mm->n_reserved_dropped++;
		return 0;
	}
	for (uint32_t off = 0; off + stride <= len; off += stride) {
		uint64_t base, size;
		if (!fdt_read_reg_entry(st, val, off, &base, &size))
			continue;
		if (memory && c->mm->n_banks < FDT_MAX_BANKS) {
			c->mm->banks[c->mm->n_banks].base = base;
			c->mm->banks[c->mm->n_banks].size = size;
			c->mm->n_banks++;
		} else if (reserved) {
			if (c->mm->n_reserved < FDT_MAX_RESERVED) {
				c->mm->reserved[c->mm->n_reserved].base = base;
				c->mm->reserved[c->mm->n_reserved].size = size;
				c->mm->reserved[c->mm->n_reserved].no_map = c->rsv_node_no_map;
				copy_name(c->mm->reserved[c->mm->n_reserved].name, st->names[2] ? st->names[2] : "");
				c->mm->n_reserved++;
			} else {
				/* Table full: count the drop so safety gates can
				 * fail closed instead of trusting a truncated map. */
				c->mm->n_reserved_dropped++;
			}
		}
	}
	if (reserved && len % stride)
		c->mm->n_reserved_dropped++;	/* partial trailing tuple (N5) */
	return 0;
}

/* The /memreserve/ block (header offset 16): u64 (addr,size) big-endian
 * pairs terminated by an all-zero pair. Reservations here are just as
 * binding as /reserved-memory children, so they join the same table
 * (review D6). An unterminated, misaligned, or out-of-bounds block is
 * corruption: fail the whole collect so callers fail closed. */
static int collect_memreserve(uintptr_t fdt, struct fdt_memmap *mm)
{
	uintptr_t end = fdt + be32(fdt + 4);
	uintptr_t p = fdt + be32(fdt + 16);
	if (p <= fdt || (p & 7))
		return 0;
	/* bounded: p advances 16 per entry and the loop returns once p + 16 > end */
	for (;;) {
		if (p + 16 > end)
			return 0;	/* no (0,0) terminator before the blob ends */
		uint64_t a = ((uint64_t)be32(p) << 32) | be32(p + 4);
		uint64_t s = ((uint64_t)be32(p + 8) << 32) | be32(p + 12);
		if (!a && !s)
			return 1;
		if (mm->n_reserved < FDT_MAX_RESERVED) {
			mm->reserved[mm->n_reserved].base = a;
			mm->reserved[mm->n_reserved].size = s;
			mm->reserved[mm->n_reserved].no_map = 0;
			copy_name(mm->reserved[mm->n_reserved].name, "/memreserve");
			mm->n_reserved++;
		} else {
			mm->n_reserved_dropped++;
		}
		p += 16;
	}
}

int fdt_collect_memmap(uintptr_t fdt, struct fdt_memmap *out)
{
	for (unsigned i = 0; i < FDT_MAX_BANKS; i++) {
		out->banks[i].base = 0;
		out->banks[i].size = 0;
	}
	for (unsigned i = 0; i < FDT_MAX_RESERVED; i++) {
		out->reserved[i].base = 0;
		out->reserved[i].size = 0;
		out->reserved[i].no_map = 0;
		out->reserved[i].name[0] = 0;
	}
	out->n_banks = 0;
	out->n_reserved = 0;
	out->n_reserved_dropped = 0;
	struct collect_ctx ctx = { out, 0, 0 };
	struct fdt_ops ops = { .begin_node = collect_begin, .prop = collect_prop };
	int rc = fdt_walk(fdt, &ops, &ctx);
	if (rc == 0)
		return 0;
	return collect_memreserve(fdt, out);
}

struct fb_ctx {
	struct fdt_video cur[MAX_DEPTH];
	int is_fb[MAX_DEPTH];
	struct fdt_video result;
	int found;
};

static int fb_begin(struct fdt_walk_state *st, const char *name, void *ctx)
{
	struct fb_ctx *c = ctx;
	int d = st->depth;
	if (d < MAX_DEPTH) {
		c->cur[d] = (struct fdt_video){ 0 };
		c->is_fb[d] = 0;
		if (d == 3 && st->names[1] && streq(st->names[1], "reserved-memory") && has_text(name, "splash"))
			c->is_fb[d] = 1;
	}
	return 0;
}

static int fb_prop(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx)
{
	struct fb_ctx *c = ctx;
	int d = st->depth;
	if (d >= MAX_DEPTH)
		return 0;
	if (d == 3 && st->names[1] && streq(st->names[1], "chosen") &&
	    st->names[2] && has_text(st->names[2], "framebuffer")) {
		if (streq(prop, "compatible") && len && has_text_n((const char *)val, len, "simple-framebuffer"))
			c->is_fb[d] = 1;
	}
	if (c->is_fb[d]) {
		if (streq(prop, "reg")) {
			/* A reg shorter than the full cell stride would decode
			 * past the property into the next header; refuse it. */
			uint32_t stride = fdt_reg_stride(st);
			if (stride && len >= stride) {
				uint64_t base, size;
				if (fdt_read_reg_entry(st, val, 0, &base, &size)) {
					c->cur[d].base = base;
					c->cur[d].size = size;
				}
			}
		} else if (streq(prop, "width") && len == 4) {
			c->cur[d].width = be32(val);
		} else if (streq(prop, "height") && len == 4) {
			c->cur[d].height = be32(val);
		} else if (streq(prop, "stride") && len == 4) {
			c->cur[d].stride = be32(val);
		}
	}
	return 0;
}

static int fb_end(struct fdt_walk_state *st, void *ctx)
{
	struct fb_ctx *c = ctx;
	int d = st->depth;
	if (d < MAX_DEPTH && c->is_fb[d] && c->cur[d].base != 0) {
		c->result = c->cur[d];
		c->found = 1;
		return 1;
	}
	return 0;
}

int fdt_framebuffer(uintptr_t fdt, struct fdt_video *video)
{
	struct fb_ctx ctx;
	for (int i = 0; i < MAX_DEPTH; i++) {
		ctx.cur[i] = (struct fdt_video){ 0 };
		ctx.is_fb[i] = 0;
	}
	ctx.result = (struct fdt_video){ 0 };
	ctx.found = 0;

	struct fdt_ops ops = {
		.begin_node = fb_begin,
		.prop = fb_prop,
		.end_node = fb_end,
	};
	fdt_walk(fdt, &ops, &ctx);
	if (ctx.found && ctx.result.base) {
		*video = ctx.result;
		return 1;
	}
	return 0;
}

/* ---- ramoops (/reserved-memory child) ---- */

static int name_starts_with(const char *name, const char *prefix)
{
	/* bounded: ends at the end of prefix (a literal) or the first mismatch */
	while (*prefix && *name == *prefix)
		name++, prefix++;
	return *prefix == 0;
}

struct ramoops_ctx {
	struct fdt_ramoops *ro;
	int active;		/* props now arriving belong to a reserved-memory child */
	int compat_ok, name_ok, seen_reg;
};

static int ramoops_begin(struct fdt_walk_state *st, const char *name, void *ctx)
{
	struct ramoops_ctx *c = ctx;
	if (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory")) {
		c->active = 1;
		c->compat_ok = 0;
		c->name_ok = name_starts_with(name, "ramoops");
		c->seen_reg = 0;
		/* Props stream by node by node; nothing from an earlier
		 * sibling may survive into this one - only what THIS node
		 * itself carries may reach the caller (review D9). */
		c->ro->base = 0;
		c->ro->size = 0;
		c->ro->record_size = 0;
		c->ro->console_size = 0;
		c->ro->ftrace_size = 0;
		c->ro->pmsg_size = 0;
		c->ro->ecc_size = 0;
		c->ro->mem_type = 0;
	} else if (st->depth > 3) {
		c->active = 0;	/* ignore properties of nested subnodes */
	}
	return 0;
}

static int ramoops_prop(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx)
{
	struct ramoops_ctx *c = ctx;
	if (!c->active || st->depth != 3 || !st->names[1] || !streq(st->names[1], "reserved-memory"))
		return 0;
	if (streq(prop, "compatible") && len)
		c->compat_ok = has_text_n((const char *)val, len, "ramoops");
	else if (streq(prop, "reg")) {
		/* First reg tuple only, like the kernel's IORESOURCE_MEM[0]. */
		uint32_t stride = fdt_reg_stride(st);
		uint64_t base, size;
		if (stride && len >= stride && fdt_read_reg_entry(st, val, 0, &base, &size)) {
			c->ro->base = base;
			c->ro->size = size;
			c->seen_reg = 1;
		}
	} else if (len == 4) {
		uint32_t v = be32(val);
		if (streq(prop, "record-size"))
			c->ro->record_size = v;
		else if (streq(prop, "console-size"))
			c->ro->console_size = v;
		else if (streq(prop, "ftrace-size"))
			c->ro->ftrace_size = v;
		else if (streq(prop, "pmsg-size"))
			c->ro->pmsg_size = v;
		else if (streq(prop, "ecc-size"))
			c->ro->ecc_size = v;
		else if (streq(prop, "mem-type"))
			c->ro->mem_type = v;
	}
	return 0;
}

static int ramoops_end(struct fdt_walk_state *st, void *ctx)
{
	struct ramoops_ctx *c = ctx;
	if (!c->active || st->depth != 3)
		return 0;
	c->active = 0;
	if (c->compat_ok || c->name_ok) {
		if (c->seen_reg) {
			c->ro->found = 1;
			return 1;	/* first qualified node with a reg wins */
		}
		c->ro->qualified_no_reg = 1;
	}
	return 0;
}

int fdt_ramoops(uintptr_t fdt, struct fdt_ramoops *out)
{
	out->base = 0;
	out->size = 0;
	out->record_size = 0;
	out->console_size = 0;
	out->ftrace_size = 0;
	out->pmsg_size = 0;
	out->ecc_size = 0;
	out->mem_type = 0;
	out->found = 0;
	out->qualified_no_reg = 0;
	struct ramoops_ctx ctx = { out, 0, 0, 0, 0 };
	struct fdt_ops ops = { ramoops_begin, ramoops_prop, ramoops_end };
	return fdt_walk(fdt, &ops, &ctx) == 1;
}

struct report_ctx {
	int no_map[MAX_DEPTH];
};

static int report_begin(struct fdt_walk_state *st, const char *name, void *ctx)
{
	(void)name;
	struct report_ctx *c = ctx;
	if (st->depth < MAX_DEPTH)
		c->no_map[st->depth] = 0;
	return 0;
}

static int report_prop(struct fdt_walk_state *st, const char *prop, uintptr_t val, uint32_t len, void *ctx)
{
	struct report_ctx *c = ctx;
	if (st->depth == 2 && st->names[1] && streq(st->names[1], "chosen") && streq(prop, "bootargs")) {
		char args[256];
		unsigned n = len < sizeof(args) - 1 ? len : sizeof(args) - 1;
		for (unsigned i = 0; i < n; i++)
			args[i] = *(volatile char *)(val + i);
		args[n] = 0;
		con_str("FDT bootargs", args);
	}
	if (len == 0 && streq(prop, "no-map") && st->depth < MAX_DEPTH)
		c->no_map[st->depth] = 1;
	if (streq(prop, "reg") && len >= 8 && st->depth >= 2) {
		int memory = (st->depth == 2 && st->names[1] && name_is_memory(st->names[1]));
		int reserved = (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory"));
		if (memory || reserved) {
			uint32_t stride = fdt_reg_stride(st);
			if (stride) {
				for (uint32_t off = 0; off + stride <= len; off += stride) {
					uint64_t base, size;
					if (fdt_read_reg_entry(st, val, off, &base, &size)) {
						con_str(memory ? "FDT memory" : "FDT reserved", st->names[st->depth - 1]);
						con_hex(" base", base);
						con_hex(" size", size);
					}
				}
			}
		}
	}
	return 0;
}

static int report_end(struct fdt_walk_state *st, void *ctx)
{
	struct report_ctx *c = ctx;
	if (st->depth == 3 && st->names[1] && streq(st->names[1], "reserved-memory"))
		con_str(c->no_map[st->depth] ? "FDT no-map yes" : "FDT no-map no", st->names[2]);
	return 0;
}

void fdt_probe_report(uintptr_t fdt)
{
	struct report_ctx ctx;
	for (int i = 0; i < MAX_DEPTH; i++)
		ctx.no_map[i] = 0;
	struct fdt_ops ops = {
		.begin_node = report_begin,
		.prop = report_prop,
		.end_node = report_end,
	};
	int rc = fdt_walk(fdt, &ops, &ctx);
	if (rc == 0) {
		if (!fdt || be32(fdt) != FDT_MAGIC)
			con_str("FDT", "invalid");
		else
			con_str("FDT", "truncated: walk aborted mid-tree");
	}
}
