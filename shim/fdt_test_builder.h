#pragma once
/*
 * Minimal in-memory FDT builder for host tests, shared by
 * fdt_host_test.c and pstore_host_test.c. The buffer belongs to the
 * caller (kept in a struct so tests can point ramoops regs at it).
 */
#include <stdint.h>
#include <string.h>

struct fdt_builder {
	uint8_t buf[12288] __attribute__((aligned(8)));
	uint32_t struct_pos;
	uint32_t str_pos;
	uint32_t rsv_pos;	/* next free /memreserve/ slot (starts at 40) */
};

static uint32_t fb_align4(uint32_t pos)
{
	return (pos + 3) & ~3U;
}

static void fb_init(struct fdt_builder *b)
{
	memset(b->buf, 0, sizeof(b->buf));
	b->struct_pos = 64; /* header is 64 bytes */
	b->str_pos = 6000;  /* string table starts at 6000 */
	b->rsv_pos = 40;    /* /memreserve/ block sits right after the header */
}

static void fb_put32(struct fdt_builder *b, uint32_t v)
{
	b->buf[b->struct_pos++] = (uint8_t)(v >> 24);
	b->buf[b->struct_pos++] = (uint8_t)(v >> 16);
	b->buf[b->struct_pos++] = (uint8_t)(v >> 8);
	b->buf[b->struct_pos++] = (uint8_t)v;
}

static uint32_t fb_add_string(struct fdt_builder *b, const char *s)
{
	uint32_t off = b->str_pos - 6000;
	size_t len = strlen(s) + 1;
	memcpy(b->buf + b->str_pos, s, len);
	b->str_pos += (uint32_t)len;
	return off;
}

static void fb_begin_node(struct fdt_builder *b, const char *name)
{
	fb_put32(b, 1); /* FDT_BEGIN_NODE */
	size_t len = strlen(name) + 1;
	memcpy(b->buf + b->struct_pos, name, len);
	b->struct_pos = fb_align4(b->struct_pos + (uint32_t)len);
}

static void fb_end_node(struct fdt_builder *b)
{
	fb_put32(b, 2); /* FDT_END_NODE */
}

static void fb_prop(struct fdt_builder *b, const char *name, const void *val, uint32_t len)
{
	uint32_t str_off = fb_add_string(b, name);
	fb_put32(b, 3); /* FDT_PROP */
	fb_put32(b, len);
	fb_put32(b, str_off);
	if (len) {
		memcpy(b->buf + b->struct_pos, val, len);
		b->struct_pos = fb_align4(b->struct_pos + len);
	}
}

static void fb_prop_u32(struct fdt_builder *b, const char *name, uint32_t v)
{
	uint8_t bytes[4] = { (uint8_t)(v >> 24), (uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v };
	fb_prop(b, name, bytes, 4);
}

/* reg tuple helper: #address-cells=2, #size-cells=1 style (12 bytes). */
static inline void fb_reg21(struct fdt_builder *b, uint64_t base, uint32_t size)
{
	uint8_t bytes[12] = {
		(uint8_t)(base >> 56), (uint8_t)(base >> 48), (uint8_t)(base >> 40), (uint8_t)(base >> 32),
		(uint8_t)(base >> 24), (uint8_t)(base >> 16), (uint8_t)(base >> 8), (uint8_t)base,
		(uint8_t)(size >> 24), (uint8_t)(size >> 16), (uint8_t)(size >> 8), (uint8_t)size,
	};
	fb_prop(b, "reg", bytes, sizeof(bytes));
}

/* /memreserve/ entry (u64 addr, u64 size, big-endian). Call right after
 * fb_init, before any node: the struct block moves up to make room. */
static inline void fb_memreserve(struct fdt_builder *b, uint64_t base, uint64_t size)
{
	uint8_t *p = b->buf + b->rsv_pos;
	for (int i = 0; i < 8; i++)
		p[i] = (uint8_t)(base >> (56 - 8 * i));
	for (int i = 0; i < 8; i++)
		p[8 + i] = (uint8_t)(size >> (56 - 8 * i));
	b->rsv_pos += 16;
	if (b->struct_pos < b->rsv_pos + 16)	/* keep room for the (0,0) terminator */
		b->struct_pos = fb_align4(b->rsv_pos + 16);
}

static uintptr_t fb_finish(struct fdt_builder *b)
{
	fb_put32(b, 9); /* FDT_END */
	uint32_t totalsize = b->str_pos;
	uint32_t struct_off = b->rsv_pos > 40 ? fb_align4(b->rsv_pos + 16) : 64;
	/* Write standard FDT header at offset 0 */
	uint8_t *h = b->buf;
	#define FB_WBE(off, val) do { 						\
		uint32_t v_ = (val); 						\
		h[(off)] = v_ >> 24; h[(off)+1] = v_ >> 16; h[(off)+2] = v_ >> 8; h[(off)+3] = v_; 	\
	} while (0)
	FB_WBE(0, 0xd00dfeed);      /* magic */
	FB_WBE(4, totalsize);       /* totalsize */
	FB_WBE(8, struct_off);      /* off_dt_struct */
	FB_WBE(12, 6000);           /* off_dt_strings */
	FB_WBE(16, 40);             /* off_mem_rsvmap */
	FB_WBE(20, 17);             /* version */
	FB_WBE(24, 16);             /* last_comp_version */
	FB_WBE(28, 0);              /* boot_cpuid_phys */
	FB_WBE(32, b->str_pos - 6000); /* size_dt_strings */
	FB_WBE(36, b->struct_pos - struct_off);/* size_dt_struct */
	#undef FB_WBE
	return (uintptr_t)b->buf;
}
