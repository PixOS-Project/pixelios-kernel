/*
 * udump.c - framed read-only userdata dump over the USB gadget
 * (framing in udump.h).
 *
 * Read-only by construction: no SCSI knowledge at all (the caller's
 * read hook is ufs.c's bounds-checked ufs_read_userdata), no MMIO (the
 * object audit in tools/test-bluejay-boot.py proves every store lands
 * on the stack or in an argument buffer; there is no static state), and
 * the only hardware calls are usbgadget_write/usbgadget_ready plus the
 * poll hook. Every wait is bounded: 5 s per chunk for flow control, and
 * the caller's window deadline bounds the whole dump so it can never
 * block the shim reset chain.
 */
#include <stdint.h>
#include <stddef.h>
#include "console.h"
#include "udump.h"
#include "usbgadget.h"

#define UDUMP_CHUNK_SECONDS	5u	/* flow-control budget per chunk */

static unsigned put_str(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	dst[pos] = 0;
	return pos;
}

static unsigned put_dec(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char tmp[10];
	int n = 0;
	do {
		tmp[n++] = (char)('0' + v % 10);
		v /= 10;
		/* bounded: v /= 10 reaches 0 (<= 10 digits for 32 bits) */
	} while (v);
	while (n > 0 && pos + 1 < cap)
		dst[pos++] = tmp[--n];
	dst[pos] = 0;
	return pos;
}

static unsigned put_hex8(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < 8 && pos + 1 < cap; i++)
		dst[pos++] = hex[(v >> (4 * (7 - i))) & 0xf];
	dst[pos] = 0;
	return pos;
}

#ifdef __ARM_FEATURE_CRC32
static inline uint32_t hw_crc32b(uint32_t c, uint8_t v) { __asm__("crc32b %w0, %w0, %w1" : "+r"(c) : "r"(v)); return c; }
static inline uint32_t hw_crc32d(uint32_t c, uint64_t v) { __asm__("crc32x %w0, %w0, %x1" : "+r"(c) : "r"(v)); return c; }
#endif

/* zlib CRC32 (reflected, poly 0xEDB88320), bitwise, incremental form:
 * crc32_update(0, p, n) is the CRC32 of p[0..n); chained calls continue
 * a running value. */
static uint32_t crc32_update(uint32_t crc, const uint8_t *p, size_t n)
{
	crc = ~crc;
#ifdef __ARM_FEATURE_CRC32
	/* ARMv8 CRC32 instructions (poly 0xEDB88320, same as zlib): the bitwise loop below
	 * costs ~16 operations per byte, twice per byte dumped, with the MMU off.
	 * Doubleword loads must be 8-byte aligned (MMU off = device memory). */
	/* bounded: n counts down to 0 */
	for (; n && ((uintptr_t)p & 7); n--)
		crc = hw_crc32b(crc, *p++);
	for (; n >= 8; n -= 8, p += 8)
		crc = hw_crc32d(crc, *(const uint64_t *)p);
	/* bounded: n counts down to 0 */
	for (; n; n--)
		crc = hw_crc32b(crc, *p++);
	return ~crc;
#endif
	for (size_t i = 0; i < n; i++) {
		crc ^= p[i];
		for (int k = 0; k < 8; k++)
			crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
	}
	return ~crc;
}

static void put_u32le(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
	p[2] = (uint8_t)(v >> 16);
	p[3] = (uint8_t)(v >> 24);
}

static void put_u64le(uint8_t *p, uint64_t v)
{
	put_u32le(p, (uint32_t)v);
	put_u32le(p + 4, (uint32_t)(v >> 32));
}

static int past(const struct udump_hooks *h, uint64_t deadline)
{
	return (int64_t)(h->ticks() - deadline) >= 0;
}

static uint64_t after(const struct udump_hooks *h, uint64_t seconds)
{
	return h->ticks() + seconds * h->ticks_freq;
}

/* One failure line per code path; printed AFTER the dump has stopped
 * sending, so it never lands inside a record we still intend to
 * complete. */
static void report_stall(uint32_t seq)
{
	char line[24];
	unsigned pos = put_str(line, sizeof line, 0, "seq=");
	put_dec(line, sizeof line, pos, seq);
	con_str("UDUMP stalled at", line);
}

static void report_seq_line(const char *label, uint32_t seq)
{
	char line[24];
	unsigned pos = put_str(line, sizeof line, 0, "seq=");
	put_dec(line, sizeof line, pos, seq);
	con_str(label, line);
}

/* Send buf as consecutive <= UDUMP_PIECE usbgadget_write pieces, polling
 * between attempts. Returns 0 sent, 1 gadget stopped being ready, 2 the
 * deadline passed. */
static int send_pieces(const struct udump_hooks *h, const uint8_t *buf,
		       size_t len, uint64_t deadline)
{
	size_t off = 0;
	while (off < len) {
		h->poll();
		if (!usbgadget_ready())
			return 1;
		if (past(h, deadline))
			return 2;
		size_t want = len - off;
		if (want > UDUMP_PIECE)
			want = UDUMP_PIECE;
		off += usbgadget_write((const char *)buf + off, want);
	}
	return 0;
}

static void put_header(uint8_t *hdr, uint32_t seq, uint64_t lba,
		       uint32_t nblocks, uint32_t crc)
{
	hdr[0] = 'U';
	hdr[1] = 'D';
	hdr[2] = 'M';
	hdr[3] = 'P';
	put_u32le(hdr + 4, seq);
	put_u64le(hdr + 8, lba);
	put_u32le(hdr + 16, nblocks);
	put_u32le(hdr + 20, crc);
}

int udump_run(const struct udump_hooks *h, void *stage, size_t stage_len,
	      uint64_t ud_first, uint64_t ud_last, uint32_t blocks,
	      uint64_t window_end)
{
	uint8_t *st = stage;
	char line[48];

	/* Bounds are the safety property (dump safety requirement): u64 arithmetic,
	 * overflow-safe, and only [ud_first, ud_first+blocks-1] is ever
	 * read. Every refusal happens before a single byte is sent. */
	if (blocks == 0 || blocks > UDUMP_MAX_BLOCKS) {
		con_str("UDUMP refused", "block count out of range");
		return UDUMP_ERR_REFUSED;
	}
	if (stage_len < (size_t)UDUMP_CHUNK_BLOCKS * UDUMP_BLOCK) {
		con_str("UDUMP refused", "stage buffer too small");
		return UDUMP_ERR_REFUSED;
	}
	if (ud_first == 0) {
		con_str("UDUMP refused", "ud_first=0");
		return UDUMP_ERR_REFUSED;
	}
	if (ud_last < ud_first) {
		con_str("UDUMP refused", "GPT range invalid");
		return UDUMP_ERR_REFUSED;
	}
	uint64_t part_blocks = ud_last - ud_first + 1;
	if ((uint64_t)blocks > part_blocks) {
		con_str("UDUMP refused", "N > partition");
		return UDUMP_ERR_REFUSED;
	}
	if (ud_first > UINT64_MAX - (blocks - 1)) {
		/* Defensive: unreachable through this API (blocks > partition
		 * already caught it) but the spec demands the overflow check. */
		con_str("UDUMP refused", "LBA arithmetic overflow");
		return UDUMP_ERR_REFUSED;
	}
	if (ud_first + blocks - 1 > 0xFFFFFFFFULL) {
		/* read_blocks_dma takes a 32-bit LBA; never truncate silently. */
		con_str("UDUMP refused", "LBA > 32-bit");
		return UDUMP_ERR_REFUSED;
	}

	/* Flow control starts before anything is sent: no host, no dump.
	 * The deadline is computed ONCE - recomputing it each iteration would
	 * let it slide with the clock and the wait would never end. */
	uint64_t ready_dl = after(h, UDUMP_CHUNK_SECONDS);
	/* bounded: past(ready_dl), computed once, returns UDUMP_ERR_NOT_READY */
	while (!usbgadget_ready()) {
		h->poll();
		if (past(h, ready_dl)) {
			con_str("UDUMP refused", "gadget not ready");
			return UDUMP_ERR_NOT_READY;
		}
	}

	unsigned pos = put_str(line, sizeof line, 0, "first=");
	pos = put_dec(line, sizeof line, pos, (uint32_t)ud_first);
	pos = put_str(line, sizeof line, pos, " count=");
	(void)put_dec(line, sizeof line, pos, blocks);
	con_str("UDUMP-BEGIN", line);

	/* Flush the text log (BEGIN included) before the first record byte:
	 * the wire order must be text, records, text (host receiver requirement). */
	uint64_t drain_dl = after(h, UDUMP_CHUNK_SECONDS);
	/* bounded: past(drain_dl), computed once, returns UDUMP_ERR_STALLED */
	while (h->log_pending()) {
		h->poll();
		if (past(h, drain_dl)) {
			report_stall(0);
			return UDUMP_ERR_STALLED;
		}
	}

	uint8_t hdr[UDUMP_HDR];
	uint64_t lba = ud_first;
	uint32_t left = blocks, seq = 0, total_crc = 0;
	uint64_t t_read = 0, t_crc = 0, t_send = 0, t0;
	/* bounded: left drops by nb >= 1 per pass (nb = min(left, UDUMP_CHUNK_BLOCKS)) */
	while (left) {
		uint32_t nb = left < UDUMP_CHUNK_BLOCKS ? left : UDUMP_CHUNK_BLOCKS;
		/* The reader moves at most 2 blocks per command (ufs.c's command
		 * descriptor has two 4 KiB PRD entries), straight into the
		 * staging buffer at 4 KiB-aligned offsets. */
		t0 = h->ticks();
		for (uint32_t b = 0; b < nb; b += 2) {
			uint32_t n2 = nb - b > 1 ? 2 : 1;
			if (h->read(lba + b, n2, st + (size_t)b * UDUMP_BLOCK)) {
				report_seq_line("UDUMP read failed at", seq);
				return UDUMP_ERR_READ;
			}
		}
		t_read += h->ticks() - t0;
		t0 = h->ticks();
		size_t plen = (size_t)nb * UDUMP_BLOCK;
		uint32_t crc = crc32_update(0, st, plen);
		total_crc = crc32_update(total_crc, st, plen);
		t_crc += h->ticks() - t0;
		t0 = h->ticks();

		/* One budget per chunk: 5 s of flow control, never past the
		 * gadget window (whichever ends first; unsigned compare so a
		 * UINT64_MAX "no window" sentinel never clamps). */
		uint64_t dl = after(h, UDUMP_CHUNK_SECONDS);
		if (window_end < dl)
			dl = window_end;
		put_header(hdr, seq, lba, nb, crc);
		int rc = send_pieces(h, hdr, UDUMP_HDR, dl);
		if (!rc)
			rc = send_pieces(h, st, plen, dl);
		if (rc) {
			if (past(h, window_end)) {
				report_seq_line("UDUMP window over at", seq);
				return UDUMP_ERR_WINDOW;
			}
			report_stall(seq);
			return UDUMP_ERR_STALLED;
		}
		t_send += h->ticks() - t0;
		lba += nb;
		left -= nb;
		seq++;
	}

	/* End record: nblocks = 0, crc32 = CRC32 of ALL payload bytes. */
	put_header(hdr, seq, ud_first + blocks, 0, total_crc);
	if (send_pieces(h, hdr, UDUMP_HDR, after(h, UDUMP_CHUNK_SECONDS))) {
		report_stall(seq);
		return UDUMP_ERR_STALLED;
	}

	pos = put_str(line, sizeof line, 0, "total=");
	pos = put_hex8(line, sizeof line, pos, total_crc);
	pos = put_str(line, sizeof line, pos, " blocks=");
	(void)put_dec(line, sizeof line, pos, blocks);
	con_str("UDUMP-END", line);
	pos = put_str(line, sizeof line, 0, "ms read=");
	pos = put_dec(line, sizeof line, pos, (uint32_t)(t_read * 1000 / h->ticks_freq));
	pos = put_str(line, sizeof line, pos, " crc=");
	pos = put_dec(line, sizeof line, pos, (uint32_t)(t_crc * 1000 / h->ticks_freq));
	pos = put_str(line, sizeof line, pos, " send=");
	(void)put_dec(line, sizeof line, pos, (uint32_t)(t_send * 1000 / h->ticks_freq));
	con_str("UDUMP-TIME", line);
	return UDUMP_OK;
}
