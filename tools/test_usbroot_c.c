/*
 * Host test for the REAL C of patch 0157 (bsd/dev/sweet_usbroot.c): tools/test_usbroot_c.py extracts the file from the
 * patch's + lines into a scratch dir next to this source, stubs the kernel headers (kstub.h, generated there) and builds
 * with -fsanitize=address,undefined. This file #includes the extracted C so the statics (ur, ur_xfer, ur_strategy) are
 * reachable. It drives: the RX validation (CRC, magic, MACs, fragment bounds, seq/stale, total, error replies), the
 * strategy write refusal, the open/write refusal, the rootdev refusals and the board-before-probe order.
 * Prints "RESULT: PASS n checks" / "RESULT: FAIL ..." and exits 0/1.
 */
/* printf is captured (cap, reset with cap_reset) AND echoed, so the loud lines of rule 8 (GAP, stats, trace) are asserted exactly */
#include <stdio.h>
#include <stdarg.h>
static char cap[1 << 20];
static size_t cap_n;
static void cap_reset(void) { cap_n = 0; cap[0] = 0; }
static int
cap_printf(const char *f, ...)
{
	va_list ap;
	va_start(ap, f);
	if (cap_n < sizeof(cap) - 1) {
		int n = vsnprintf(cap + cap_n, sizeof(cap) - cap_n, f, ap);
		if (n > 0) {
			cap_n += (size_t)n < sizeof(cap) - 1 - cap_n ? (size_t)n : sizeof(cap) - 1 - cap_n;
		}
	}
	va_end(ap);
	va_start(ap, f);
	int r = vprintf(f, ap);
	va_end(ap);
	return r;
}
#define printf cap_printf
#include "sweet_usbroot.c"

static int checks, fails;
#define CHECK(c) do { checks++; if (!(c)) { fails++; fprintf(stdout, "FAIL line %d: %s\n", __LINE__, #c); } } while (0)

/* ---- stub state ---- */
int stub_tx_count, stub_probe_count, stub_ufs_active, stub_board_gs101 = 1, stub_usb_gs101 = 1, stub_ready = 1;
int stub_arg_sweetusb = -1, stub_arg_rootrw = -1, stub_arg_cow = -1, stub_kalloc_fail, stub_kalloc_calls;
int stub_lock_depth, stub_spin_depth;
uint64_t stub_now;
const char *stub_arg_fast_str;        /* sweetusbrootfast value; NULL = absent */
int stub_blocks, stub_spurious, stub_late_wake, stub_early_timeout;
uint64_t stub_reply_ns, stub_reply_step_ns;
int stub_fast_enable, stub_hurry_on, stub_hurry_off, stub_wakeups, stub_spin_sleeps;
bool stub_hurry;
static uint8_t last_tx[2048];
static uint32_t last_tx_len;
static int tx_ops_other;                 /* transmitted frames whose op is neither INFO nor READ */
static int host_mode;
static uint8_t pend[40][1600];
static uint32_t pend_len[40];
static int pend_n;

enum { H_SILENT, H_GOOD, H_BADCRC, H_BADSEQ, H_SHORTFRAG, H_ERRPAYLOAD, H_ERR4, H_INFOFRAG };
static int stub_tx_refuse;      /* the next N sweet_usb_raw_tx calls return false (TX queue full) */
#define IMG_BYTES (1ull << 20)
static uint8_t img(uint64_t off) { return (uint8_t)(off * 7 + (off >> 8) + 0x5a); }

/* Build a reply frame as the host would; bad_crc flips a payload byte after the CRC was computed. */
static uint32_t
mkrep(uint8_t *f, uint8_t op, uint16_t status, uint32_t seq, uint32_t total, uint64_t lba, uint32_t off, uint32_t flen,
    const uint8_t *payload, int bad_crc)
{
	memset(f, 0, 1600);
	memcpy(f, ur_phone_mac, 6);
	memcpy(f + 6, ur_host_mac, 6);
	f[12] = 0x88; f[13] = 0xb6;
	uint8_t *h = f + 14;
	memcpy(h, ur_magic_rep, 4);
	h[4] = UR_VERSION; h[5] = op;
	ur_put16(h + 6, status);
	ur_put32(h + 8, seq); ur_put32(h + 12, total); ur_put64(h + 16, lba);
	ur_put32(h + 24, off); ur_put16(h + 28, flen);
	if (flen) {
		memcpy(h + 36, payload, flen);
	}
	ur_put32(h + 32, crc32(crc32(0, h, 32), h + 36, flen));
	if (bad_crc && flen) {
		h[36] ^= 0x01;
	}
	return 14 + 36 + flen;
}

static void
queue(uint32_t len, const uint8_t *f)
{
	if (pend_n >= 40) {             /* frames the reader never consumed pile up (a mutant): a failed check, not an overflow */
		CHECK(!"fake host queue full");
		return;
	}
	memcpy(pend[pend_n], f, len);
	pend_len[pend_n++] = len;
}

/* The fake host: sees the phone's request frame and queues replies, delivered at the next thread_block(). */
static void
host_on_tx(const uint8_t *f, uint32_t len)
{
	const uint8_t *h = f + 14;
	uint32_t seq = ur_get32(h + 8), total = ur_get32(h + 12);
	uint64_t lba = ur_get64(h + 16);
	uint8_t op = h[5], pl[1024], fr[1600];
	(void)len;
	if (host_mode == H_SILENT) {
		return;
	}
	if (host_mode == H_ERR4) {
		queue(mkrep(fr, op, UR_ST_RANGE, seq, total, lba, 0, 0, NULL, 0), fr);
		return;
	}
	if (host_mode == H_ERRPAYLOAD) {
		memset(pl, 0xee, 16);
		queue(mkrep(fr, op, UR_ST_IO, seq, total, lba, 0, 16, pl, 0), fr);
		return;
	}
	if (op == UR_OP_INFO) {
		uint8_t info[16];
		ur_put64(info, IMG_BYTES / 512); ur_put32(info + 8, 16384); ur_put32(info + 12, host_mode == H_INFOFRAG ? 512 : 1024);
		queue(mkrep(fr, op, UR_ST_OK, seq, total, lba, 0, 16, info, 0), fr);
		return;
	}
	uint32_t step = host_mode == H_SHORTFRAG ? 512 : 1024;
	for (int k = (int)((total + step - 1) / step) - 1; k >= 0; k--) {   /* reverse order */
		uint32_t off = (uint32_t)k * step, n = MIN(step, total - off);
		for (uint32_t i = 0; i < n; i++) {
			pl[i] = img(lba * 512 + off + i);
		}
		queue(mkrep(fr, op, UR_ST_OK, host_mode == H_BADSEQ ? seq + 7 : seq, total, lba, off, n, pl, host_mode == H_BADCRC), fr);
	}
}

/* ---- stub hooks the extracted C calls ---- */
bool
sweet_usb_raw_tx(const void *frame, uint32_t len)
{
	if (stub_tx_refuse > 0) {
		stub_tx_refuse--;
		return false;
	}
	stub_tx_count++;
	last_tx_len = len;
	memcpy(last_tx, frame, len);
	if (len < 14 + 6 || (((const uint8_t *)frame)[14 + 5] != UR_OP_INFO && ((const uint8_t *)frame)[14 + 5] != UR_OP_READ)) {
		tx_ops_other++;
	}
	host_on_tx(frame, len);
	return true;
}
void sweet_usb_probe(void) { stub_probe_count++; }
bool sweet_usb_ready(void) { return stub_ready; }
bool sweet_usb_is_gs101(void) { return stub_usb_gs101; }
bool sweet_usb_board_is_gs101(void) { return stub_board_gs101; }
bool sweet_ufs_active(void) { return stub_ufs_active; }
bool
PE_parse_boot_argn(const char *name, void *p, size_t sz)
{
	int v = strcmp(name, "sweetusb") == 0 ? stub_arg_sweetusb : strcmp(name, "rootrw") == 0 ? stub_arg_rootrw : strcmp(name, "usbrootcow") == 0 ? stub_arg_cow : -1;
	(void)sz;
	if (v < 0) {
		return false;
	}
	*(uint32_t *)p = (uint32_t)v;
	return true;
}
/* PE_parse_boot_arg_str(const char *arg_string, char *arg_ptr, int size) (pexpert/pexpert/pexpert.h:343); STR case copies at most size-1 bytes and terminates (bootargs.c:149-152) */
boolean_t
PE_parse_boot_arg_str(const char *name, char *p, int size)
{
	if (strcmp(name, "sweetusbrootfast") != 0 || stub_arg_fast_str == NULL) {
		return false;
	}
	snprintf(p, (size_t)size, "%s", stub_arg_fast_str);
	return true;
}
int
thread_block(void *c)
{
	stub_blocks++;
	(void)c;
	for (int i = 0; i < pend_n; i++) {
		sweet_usbroot_rx(pend[i], pend_len[i]);
	}
	pend_n = 0;
	return 0;
}

/* patch 0172: the USB side of the fast receive, and the reader's one sleep (frames land while it sleeps) */
void sweet_usb_rx_fast_enable(void) { stub_fast_enable++; }
void
sweet_usb_rx_hurry(bool on)
{
	stub_hurry = on;
	if (on) {
		stub_hurry_on++;
	} else {
		stub_hurry_off++;
	}
}
uint64_t sweet_usb_rx_short_polls(void) { return 3u * (uint64_t)stub_hurry_on; }
void thread_wakeup(void *e) { CHECK(e == &ur_done_chan && stub_spin_depth == 0); stub_wakeups++; }
int
lck_spin_sleep_deadline(int *l, int action, void *e, int m, uint64_t deadline)
{
	(void)l;
	CHECK(action == LCK_SLEEP_DEFAULT && e == &ur_done_chan && stub_spin_depth == 1 && deadline > stub_now && m == THREAD_UNINT);
	stub_spin_sleeps++;
	stub_lock_depth--; stub_spin_depth--;   /* the lock is dropped while asleep */
	int r = THREAD_AWAKENED;
	if (stub_spurious > 0) {                /* woken with no frame (spurious wake-up): the caller must sleep again */
		stub_spurious--;
		stub_lock_depth++; stub_spin_depth++;
		return r;
	}
	if (stub_early_timeout > 0) {           /* timed out before the clock reached the deadline (jitter): still no old 1 ms loop */
		stub_early_timeout--;
		stub_lock_depth++; stub_spin_depth++;
		return THREAD_TIMED_OUT;
	}
	if (stub_late_wake > 0) {               /* woken with no frame, but the deadline has passed meanwhile */
		stub_late_wake--;
		stub_now = deadline + 1000;
		stub_lock_depth++; stub_spin_depth++;
		return r;
	}
	if (pend_n > 0) {
		stub_now += stub_reply_ns;      /* the reply takes this long (fake clock) */
		stub_reply_ns = stub_reply_step_ns;
	}
	if (pend_n == 0) {
		stub_now = deadline;            /* nothing comes: the deadline passes */
		r = THREAD_TIMED_OUT;
	}
	for (int i = 0; i < pend_n; i++) {
		sweet_usbroot_rx(pend[i], pend_len[i]);
	}
	pend_n = 0;
	stub_lock_depth++; stub_spin_depth++;
	return r;
}

static void
cow_release(void)
{
	free(ur.cow_pool);
	free(ur.cow_tab);
	ur.cow_pool = NULL; ur.cow_tab = NULL; ur.cow_dirty = 0;
}

static void
reset(void)
{
	cow_release();
	memset(&ur, 0, sizeof(ur));
	stub_arg_cow = -1; stub_kalloc_fail = stub_kalloc_calls = tx_ops_other = 0;
	pend_n = 0; stub_tx_refuse = 0; stub_tx_count = stub_probe_count = stub_ufs_active = 0;
	stub_board_gs101 = stub_usb_gs101 = stub_ready = 1;
	stub_arg_sweetusb = stub_arg_rootrw = -1;
	stub_now = 0; ur_fast = false; memset(&ur_fs, 0, sizeof(ur_fs)); stub_arg_fast_str = NULL;
	stub_blocks = stub_spurious = stub_late_wake = stub_early_timeout = 0; stub_reply_ns = stub_reply_step_ns = 0; cap_reset();
	stub_fast_enable = stub_hurry_on = stub_hurry_off = stub_wakeups = stub_spin_sleeps = 0; stub_hurry = false;
	host_mode = H_SILENT;
	ur.active = true; ur.ready = true;
	ur.nbytes = IMG_BYTES; ur.secsize = 512; ur.max_read = 16384;
}

/* Put the exchange state as ur_xfer would (waiting for a READ of total bytes at lba 0 with seq 5). */
static void
arm(uint32_t total)
{
	ur.state = EX_WAITING; ur.seq = 5; ur.op = UR_OP_READ; ur.lba = 0; ur.total = total; ur.mask = 0;
	ur.full_mask = (uint32_t)((1ull << ((total + UR_FRAG - 1) / UR_FRAG)) - 1);
	ur.cache_off = 0x1111; ur.cache_len = 0x2222; ur.cache_valid = true;
	memset(ur.buf, 0, sizeof(ur.buf));
}

static bool
inject(uint32_t len, const uint8_t *f)
{
	return sweet_usbroot_rx(f, len);
}

static bool
canary_ok(void)
{
	return ur.cache_off == 0x1111 && ur.cache_len == 0x2222 && ur.cache_valid;
}

static void
test_rx(void)
{
	uint8_t f[1600], pl[1024];
	uint32_t n;
	memset(pl, 0xab, sizeof(pl));

	reset(); arm(4096);
	n = mkrep(f, 2, 0, 5, 4096, 0, 0, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 1 && ur.buf[0] == 0xab && ur.bad_frames == 0);      /* good fragment taken */
	CHECK(inject(n, f) && ur.mask == 1);                                                  /* duplicate harmless */

	reset(); arm(4096);                                                                   /* CRC checked before use */
	n = mkrep(f, 2, 0, 5, 4096, 0, 0, 1024, pl, 1);
	CHECK(inject(n, f) && ur.mask == 0 && ur.buf[0] == 0 && ur.bad_frames == 1);

	reset(); arm(4096);                                                                   /* stale / wrong seq */
	n = mkrep(f, 2, 0, 4, 4096, 0, 0, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && ur.bad_frames == 1);

	reset(); arm(4096); ur.state = EX_IDLE;                                               /* nobody waiting */
	n = mkrep(f, 2, 0, 5, 4096, 0, 0, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && ur.state == EX_IDLE);

	reset(); arm(4096);                                                                   /* fragment length must be exact */
	n = mkrep(f, 2, 0, 5, 4096, 0, 0, 512, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && ur.bad_frames == 1);

	reset(); arm(16384);                                                                  /* offset == total: out of bounds */
	n = mkrep(f, 2, 0, 5, 16384, 0, 16384, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && canary_ok());

	reset(); arm(4096);                                                                   /* offset beyond total: total - off wraps */
	n = mkrep(f, 2, 0, 5, 4096, 0, 8192, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && ur.buf[0] == 0 && canary_ok());

	reset(); arm(16384);                                                                  /* far offset, idx beyond 32 */
	n = mkrep(f, 2, 0, 5, 16384, 0, 40 * 1024, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && canary_ok());

	reset(); arm(16384);                                                                  /* unaligned offset */
	n = mkrep(f, 2, 0, 5, 16384, 0, 512, 1024, pl, 0);
	CHECK(inject(n, f) && ur.mask == 0 && canary_ok());

	reset(); arm(4096);                                                                   /* length larger than UR_FRAG */
	n = mkrep(f, 2, 0, 5, 4096, 0, 0, 1024, pl, 0);
	ur_put16(f + 14 + 28, 1025);
	CHECK(inject(n, f) && ur.mask == 0);

	reset(); arm(4096);                                                                   /* error reply carries no payload */
	n = mkrep(f, 2, UR_ST_IO, 5, 4096, 0, 0, 16, pl, 0);
	CHECK(inject(n, f) && ur.state == EX_WAITING);
	n = mkrep(f, 2, UR_ST_RANGE, 5, 4096, 0, 0, 0, NULL, 0);
	CHECK(inject(n, f) && ur.state == EX_DONE && ur.status == UR_ST_RANGE);

	reset(); arm(4096);                                                                   /* other protocol/EtherType/MAC */
	n = mkrep(f, 2, 0, 5, 4096, 0, 0, 1024, pl, 0);
	f[13] = 0xb7;
	CHECK(!inject(n, f) && ur.mask == 0);
	f[13] = 0xb6; f[0] ^= 1;
	CHECK(inject(n, f) && ur.mask == 0);
	ur.active = false; f[0] ^= 1;
	CHECK(!inject(n, f) && ur.mask == 0);                                                 /* inactive: not ours */
	CHECK(stub_lock_depth == 0);
}

static void
test_xfer_and_strategy(void)
{
	int error;
	uint32_t status = 0;
	struct buf b;
	static uint8_t data[8192];
	int i, ok;

	reset();                                                                              /* total limits, no frame sent */
	error = ur_xfer(UR_OP_READ, 0, UR_READ_MAX + 1, &status);
	CHECK(error == EINVAL && stub_tx_count == 0);
	error = ur_xfer(UR_OP_READ, 0, 0, &status);
	CHECK(error == EINVAL && stub_tx_count == 0);
	error = ur_xfer(UR_OP_READ, 0, 0x10000000u, &status);
	CHECK(error == EINVAL && stub_tx_count == 0);

	reset(); host_mode = H_GOOD;                                                          /* end to end read, 8 KiB at 4 KiB */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 8; b.count = 8192; b.data = (caddr_t)data;
	ur_strategy(&b);
	ok = b.done && b.error == 0 && b.resid == 0;
	for (i = 0; i < 8192; i++) {
		ok &= data[i] == img(8 * 512 + i);
	}
	CHECK(ok);
	CHECK(last_tx_len == UR_TX_MIN && ur_get32(last_tx + 14 + 32) == crc32(0, last_tx + 14, 32));   /* request CRC */

	reset(); host_mode = H_BADCRC;                                                        /* corrupt data is never delivered */
	memset(data, 0, sizeof(data));
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 0; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0 && data[0] == 0 && stub_tx_count == UR_TRIES);

	reset(); host_mode = H_BADSEQ;                                                        /* replies to another request */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0);

	reset(); host_mode = H_SHORTFRAG;
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0);

	reset(); host_mode = H_ERR4;                                                          /* host error status -> EIO */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == EIO && stub_tx_count == 1);

	reset(); host_mode = H_ERRPAYLOAD;
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0 && stub_tx_count == UR_TRIES);

	reset(); host_mode = H_GOOD; stub_tx_refuse = 100;                                    /* TX refused: retried, no try spent */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 0; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == 0 && stub_tx_count == 1 && stub_tx_refuse == 0 && data[0] == img(0));

	reset(); host_mode = H_GOOD; stub_tx_refuse = 1 << 30;                                /* TX never works: bounded, error */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 0; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0 && stub_tx_count == 0);

	reset(); host_mode = H_SILENT;                                                        /* host gone: fail fast after N */
	for (int k = 0; k < UR_FAST_N; k++) {
		memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 0; b.count = 1024; b.data = (caddr_t)data;
		ur_strategy(&b);
		CHECK(b.done && b.error != 0 && stub_tx_count == (k + 1) * UR_TRIES);
	}
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 0; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0 && stub_tx_count == UR_FAST_N * UR_TRIES);              /* no new request sent */
	ur.fail_until = 0; host_mode = H_GOOD;                                                /* window over, host back */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 0; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == 0 && ur.timeouts == 0 && data[0] == img(0));

	reset(); host_mode = H_GOOD;                                                          /* writes are refused first */
	memset(&b, 0, sizeof(b)); b.flags = 0; b.blkno = 0; b.count = 512; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == EROFS && stub_tx_count == 0);

	reset(); host_mode = H_GOOD;                                                          /* range checks */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = -1; b.count = 512; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.error == EINVAL && stub_tx_count == 0);
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = (daddr64_t)(IMG_BYTES / 512 - 1); b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.error == EINVAL && stub_tx_count == 0);

	reset();                                                                              /* open / write */
	CHECK(ur_open(0, FWRITE, 0, NULL) == EROFS && ur_open(0, 0, 0, NULL) == 0);
	CHECK(ur_write(0, NULL, 0) == EROFS);
	ur.ready = false;
	CHECK(ur_open(0, 0, 0, NULL) == ENXIO);
	CHECK(stub_lock_depth == 0);
}

static void
test_rootdev(void)
{
	dev_t dev = 0;
	int e;

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 0;   /* rootrw=1 without overlay refused */
	e = sweet_usbroot_rootdev(&dev);
	CHECK(e == EROFS && stub_probe_count == 0 && stub_tx_count == 0 && !ur.cow);
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 100000;   /* absurd cap refused */
	CHECK(sweet_usbroot_rootdev(&dev) == EROFS && stub_probe_count == 0 && !ur.cow && ur.cow_tab == NULL);
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_kalloc_fail = 1;   /* no memory */
	CHECK(sweet_usbroot_rootdev(&dev) == ENOMEM && stub_probe_count == 0 && !ur.cow && ur.cow_tab == NULL && ur.cow_pool == NULL);
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 1; stub_kalloc_fail = 2;   /* pool fails, table was allocated */
	CHECK(sweet_usbroot_rootdev(&dev) == ENOMEM && stub_probe_count == 0 && !ur.cow && ur.cow_tab == NULL && ur.cow_pool == NULL && ur.cow_cap == 0);
	/* finding 2: a later error path frees the overlay and leaves the root read-only (leak checked by ASan at exit) */
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 1; stub_board_gs101 = 0;
	CHECK(sweet_usbroot_rootdev(&dev) == ENODEV && !ur.cow && ur.cow_tab == NULL && ur.cow_pool == NULL && ur.cow_cap == 0 && ur.cow_tsize == 0);
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 1; host_mode = H_ERRPAYLOAD;
	CHECK(sweet_usbroot_rootdev(&dev) != 0 && !ur.cow && ur.cow_tab == NULL && ur.cow_pool == NULL && ur.cow_dirty == 0);

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 1;
	CHECK(sweet_usbroot_rootdev(&dev) == EINVAL && stub_probe_count == 0);

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_ufs_active = 1;
	CHECK(sweet_usbroot_rootdev(&dev) == EPERM && !ur.active && stub_probe_count == 0);

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_board_gs101 = 0;   /* board before probe (F2) */
	CHECK(sweet_usbroot_rootdev(&dev) == ENODEV && stub_probe_count == 0 && stub_tx_count == 0);

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_usb_gs101 = 0;
	CHECK(sweet_usbroot_rootdev(&dev) == ENODEV && stub_probe_count == 1 && stub_tx_count == 0);

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; host_mode = H_GOOD;     /* full accept path */
	e = sweet_usbroot_rootdev(&dev);
	CHECK(e == 0 && ur.ready && ur.nbytes == IMG_BYTES && stub_probe_count == 1);
	CHECK(stub_lock_depth == 0);

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; host_mode = H_INFOFRAG; /* host fragment size must be UR_FRAG */
	CHECK(sweet_usbroot_rootdev(&dev) == EINVAL && !ur.ready && stub_lock_depth == 0);
}

static void
wr(buf_t b, uint8_t *data, long long lba, uint32_t count, int flags)
{
	memset(b, 0, sizeof(*b)); b->flags = flags; b->blkno = lba; b->count = count; b->data = (caddr_t)data;
	ur_strategy(b);
}

static bool
data_is_img(const uint8_t *d, uint64_t off, uint32_t n)
{
	for (uint32_t i = 0; i < n; i++) {
		if (d[i] != img(off + i)) {
			return false;
		}
	}
	return true;
}

static bool
data_is(const uint8_t *d, uint8_t v, uint32_t n)
{
	for (uint32_t i = 0; i < n; i++) {
		if (d[i] != v) {
			return false;
		}
	}
	return true;
}

/* RAM copy-on-write overlay (patch 0158). */
static void
test_cow(void)
{
	dev_t dev = 0;
	static uint8_t d[16384], w[16384];
	struct buf b;
	int tx;

	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 1; host_mode = H_GOOD;
	CHECK(sweet_usbroot_rootdev(&dev) == 0 && ur.cow && ur.cow_cap == 256 && ur.cow_tsize == 512 && ur.cow_dirty == 0);
	CHECK(ur_open(0, FWRITE, 0, NULL) == 0 && ur_write(0, NULL, 0) == 0);

	/* partial write (512 B at lba 1) fetches the host block once, then reads see overlay + host bytes */
	tx = stub_tx_count; memset(w, 0xa5, 512);
	wr(&b, w, 1, 512, 0);
	CHECK(b.done && b.error == 0 && b.resid == 0 && ur.cow_dirty == 1 && stub_tx_count == tx + 1);
	wr(&b, d, 0, 1024, B_READ);
	CHECK(b.error == 0 && data_is_img(d, 0, 512) && data_is(d + 512, 0xa5, 512));
	wr(&b, d, 2, 512, B_READ);
	CHECK(b.error == 0 && data_is_img(d, 1024, 512));                                /* rest of the dirty block = host bytes */

	/* full block write needs no host fetch; a 16 KiB read straddles dirty and clean blocks */
	memset(w, 0x3c, 4096);
	tx = stub_tx_count;
	wr(&b, w, 24, 4096, 0);                                                          /* block 3 */
	CHECK(b.error == 0 && ur.cow_dirty == 2 && stub_tx_count == tx);
	wr(&b, d, 0, 16384, B_READ);
	CHECK(b.error == 0 && b.resid == 0 && data_is_img(d, 0, 512) && data_is(d + 512, 0xa5, 512) && data_is_img(d + 1024, 1024, 3072) &&
	    data_is_img(d + 4096, 4096, 8192) && data_is(d + 12288, 0x3c, 4096));

	/* write straddling blocks 1 (tail) and 2 (head) */
	memset(w, 0x77, 1024);
	wr(&b, w, 15, 1024, 0);
	CHECK(b.error == 0 && ur.cow_dirty == 4);
	wr(&b, d, 14, 2048, B_READ);
	CHECK(b.error == 0 && data_is_img(d, 7168, 512) && data_is(d + 512, 0x77, 1024) && data_is_img(d + 1536, 8704, 512));

	/* nothing but INFO/READ requests ever left the phone: every frame since reset() is counted, not only the last */
	CHECK(stub_tx_count > 0 && tx_ops_other == 0);
	CHECK(last_tx[14 + 5] == UR_OP_READ || last_tx[14 + 5] == UR_OP_INFO);
	{
		uint32_t wr_ok = 0, wr_off = 1, ro = 1;                                  /* DKIOCISWRITABLE follows ur.cow */
		(void)wr_ok;
		CHECK(ur_ioctl(0, DKIOCISWRITABLE, (caddr_t)&wr_off, 0, NULL, 0) == 0 && wr_off == 1);
		ur.cow = false;
		CHECK(ur_ioctl(0, DKIOCISWRITABLE, (caddr_t)&ro, 0, NULL, 0) == 0 && ro == 0);
		ur.cow = true;
	}

	/* bounds: refused before any change */
	tx = ur.cow_dirty;
	wr(&b, w, -1, 512, 0);
	CHECK(b.error == EINVAL && ur.cow_dirty == (uint32_t)tx);
	wr(&b, w, (long long)(IMG_BYTES / 512 - 1), 1024, 0);
	CHECK(b.error == EINVAL && ur.cow_dirty == (uint32_t)tx);
	wr(&b, w, (long long)(IMG_BYTES / 512 + 1), 512, 0);
	CHECK(b.error == EINVAL && ur.cow_dirty == (uint32_t)tx);

	/* finding 1: the strategy path never allocates (the pool was allocated in rootdev) */
	stub_kalloc_calls = 0;
	wr(&b, w, 8 * 40, 4096, 0);                                                      /* block 40 */
	CHECK(b.error == 0 && stub_kalloc_calls == 0 && ur.cow_dirty == (uint32_t)tx + 1);

	/* cap: ENOSPC beyond it, no overflow, existing blocks still writable, reads still correct */
	ur.cow_cap = 6;
	memset(w, 0x11, 4096);
	wr(&b, w, 8 * 50, 4096, 0);                                                      /* 6th distinct block */
	CHECK(b.error == 0 && ur.cow_dirty == 6);
	wr(&b, w, 8 * 51, 4096, 0);
	CHECK(b.error == ENOSPC && b.resid == 4096 && ur.cow_dirty == 6);
	wr(&b, w, 8 * 50, 4096, 0);
	CHECK(b.error == 0 && ur.cow_dirty == 6);
	memset(w, 0x22, 8192);
	wr(&b, w, 8 * 50, 8192, 0);                                                      /* 1 ok + 1 over the cap */
	CHECK(b.error == ENOSPC && b.resid == 4096);
	wr(&b, d, 8 * 50, 4096, B_READ);
	CHECK(b.error == 0 && data_is(d, 0x22, 4096));
	wr(&b, d, 8 * 51, 4096, B_READ);
	CHECK(b.error == 0 && data_is_img(d, 51 * 4096, 4096));                          /* refused write left the host view */

	CHECK(tx_ops_other == 0);

	/* hash probe wrap: two keys on the last slot, the second must wrap to slot 0 (key & 7 == slot for this multiplier) */
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_arg_rootrw = 1; stub_arg_cow = 1; host_mode = H_GOOD;
	CHECK(sweet_usbroot_rootdev(&dev) == 0 && ur.cow);
	free(ur.cow_tab);
	ur.cow_tsize = 8; ur.cow_cap = 4; ur.cow_tab = calloc(8, sizeof(*ur.cow_tab));
	memset(w, 0x5a, 4096); wr(&b, w, 8 * 6, 4096, 0);                                /* block 6, key 7: slot 7 */
	memset(w, 0x6b, 4096); wr(&b, w, 8 * 14, 4096, 0);                               /* block 14, key 15: slot 7 taken, wraps to 0 */
	CHECK(b.error == 0 && ur.cow_dirty == 2 && ur.cow_tab[7].key == 7 && ur.cow_tab[0].key == 15);
	wr(&b, d, 8 * 6, 4096, B_READ);
	CHECK(b.error == 0 && data_is(d, 0x5a, 4096));
	wr(&b, d, 8 * 14, 4096, B_READ);
	CHECK(b.error == 0 && data_is(d, 0x6b, 4096));
	memset(w, 0x7c, 4096); wr(&b, w, 8 * 22, 4096, 0);                               /* a third key on slot 7: probes 7, 0, then 1 */
	CHECK(b.error == 0 && ur.cow_tab[1].key == 23 && ur.cow_dirty == 3);
	CHECK(tx_ops_other == 0);

	/* overlay off: writes refused again */
	cow_release();
	ur.cow = false;
	wr(&b, w, 0, 512, 0);
	CHECK(b.error == EROFS && ur_open(0, FWRITE, 0, NULL) == EROFS && ur_write(0, NULL, 0) == EROFS);
	CHECK(stub_lock_depth == 0);
}

/* patch 0172: fast receive (sweetusbrootfast=1) keeps every v1 result; off it never touches the new hooks */
static void
test_fast(void)
{
	struct buf b;
	static uint8_t data[16384];
	dev_t dev = 0;
	int ok, i;

	reset(); host_mode = H_GOOD;                                          /* default off: old 1 ms loop, no new hook */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 8; b.count = 8192; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == 0 && stub_wakeups == 0 && stub_spin_sleeps == 0 && stub_hurry_on == 0 && stub_hurry_off == 0);

	reset(); ur_fast = true; host_mode = H_GOOD;                          /* on: same bytes, one wake, hurry on/off paired */
	memset(data, 0, sizeof(data));
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 32; b.count = 16384; b.data = (caddr_t)data;
	ur_strategy(&b);
	ok = b.done && b.error == 0 && b.resid == 0;
	for (i = 0; i < 16384; i++) {
		ok &= data[i] == img(32 * 512 + i);
	}
	CHECK(ok);
	CHECK(stub_tx_count == 1 && stub_wakeups == 1 && stub_spin_sleeps == 1 && stub_hurry_on == 1 && stub_hurry_off == 1 && !stub_hurry);
	CHECK(ur_fs.total == 1 && ur_fs.n == 1 && stub_lock_depth == 0);
	CHECK(stub_blocks == 0);                                              /* fast: the old 1 ms loop (ur_sleep_ms -> thread_block) never runs (M10) */

	reset(); ur_fast = true; host_mode = H_GOOD; stub_reply_ns = 7300000;  /* the reply takes 7.3 ms on the fake clock */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 32; b.count = 16384; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == 0 && stub_blocks == 0 && stub_tx_count == 1);
	CHECK(strstr(cap, "attempt 0 answered after 7 ms, status 0, 0 bad frames\n") != NULL);   /* W comes from the clock (M16) */
	CHECK(ur_fs.sum_us == 7300 && ur_fs.min_us == 7300 && ur_fs.max_us == 7300 && ur_fs.slow == 0);

	reset(); ur_fast = true; host_mode = H_GOOD; stub_spurious = 2;       /* two wake-ups with no frame: sleep again (M1b) */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = 32; b.count = 16384; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == 0 && stub_tx_count == 1 && stub_spin_sleeps == 3 && stub_wakeups == 1 && stub_spurious == 0);

	reset(); ur_fast = true; host_mode = H_SILENT; stub_early_timeout = UR_TRIES;   /* early timeout: waited < UR_WAIT_MS, the old loop must not run (M10) */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == ETIMEDOUT && stub_tx_count == UR_TRIES && stub_spin_sleeps == UR_TRIES && stub_blocks == 0);
	CHECK(strstr(cap, "attempt 0 unanswered after 0 ms, status 5") != NULL);

	reset(); ur_fast = true; host_mode = H_SILENT; stub_late_wake = 1;    /* woken after the deadline with no frame: no second sleep (M2) */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == ETIMEDOUT && stub_tx_count == UR_TRIES && stub_spin_sleeps == UR_TRIES && stub_late_wake == 0);

	reset(); ur_fast = true; host_mode = H_SILENT;                        /* on, host silent: UR_TRIES tries, ETIMEDOUT */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == ETIMEDOUT && stub_tx_count == UR_TRIES && stub_spin_sleeps == UR_TRIES);
	CHECK(stub_wakeups == 0 && stub_hurry_on == UR_TRIES && stub_hurry_off == UR_TRIES && !stub_hurry && ur_fs.total == 0);
	CHECK(stub_now == (uint64_t)UR_TRIES * UR_WAIT_MS * NSEC_PER_MSEC && stub_lock_depth == 0 && stub_blocks == 0);

	reset(); ur_fast = true; host_mode = H_BADCRC;                        /* on: corrupt replies never complete or wake */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error != 0 && stub_wakeups == 0 && ur.bad_frames > 0 && !stub_hurry);

	reset(); ur_fast = true; host_mode = H_ERR4;                          /* on: host error status still EIO after one try */
	memset(&b, 0, sizeof(b)); b.flags = B_READ; b.count = 1024; b.data = (caddr_t)data;
	ur_strategy(&b);
	CHECK(b.done && b.error == EIO && stub_tx_count == 1 && stub_wakeups == 1 && !stub_hurry);

	reset(); ur_fast = true; host_mode = H_GOOD;                          /* stats window: printed and restarted at N, exact text (M8, AP-140) */
	{
		const unsigned N = UR_FAST_STATS_N;
		char l1[256], l2[256];
		uint64_t sum1 = 500 + 9000 + 2000ull * (N - 2);
		for (i = 0; i < 2 * (int)N + 3; i++) {
			stub_reply_ns = (i == 0 ? 500000ull : i == 1 ? 9000000ull : 2000000ull);
			memset(&b, 0, sizeof(b)); b.flags = B_READ; b.blkno = (daddr64_t)(i % 64) * 32; b.count = 4096; b.data = (caddr_t)data;
			ur.cache_valid = false;
			ur_strategy(&b);
		}
		CHECK(ur_fs.total == 2 * N + 3 && ur_fs.n == 3 && ur_fs.polls0 == 3u * 2 * N);
		snprintf(l1, sizeof(l1), "SWEET USBROOT: fast stats: %u answers (total %u), request->answer us mean %llu min 500 max 9000, "
		    "over %u us 1, event-thread short polls %u, bad frames 0\n", N, N, (unsigned long long)(sum1 / N), UR_FAST_SLOW_US, 3 * N);
		snprintf(l2, sizeof(l2), "SWEET USBROOT: fast stats: %u answers (total %u), request->answer us mean 2000 min 2000 max 2000, "
		    "over %u us 0, event-thread short polls %u, bad frames 0\n", N, 2 * N, UR_FAST_SLOW_US, 3 * N);
		CHECK(strstr(cap, l1) != NULL);
		CHECK(strstr(cap, l2) != NULL);
		CHECK(strstr(cap, "fast stats") != NULL && strstr(strstr(cap, "fast stats") + 1, "fast stats") != NULL &&
		    strstr(strstr(strstr(cap, "fast stats") + 1, "fast stats") + 1, "fast stats") == NULL);   /* exactly two lines (M17) */
	}

	/* boot-arg sweetusbrootfast, read as a string: exactly "1" on, "0" and absent off and silent, anything else a GAP line (F7) */
	static const char *const gap_fmt = "SWEET USBROOT: GAP: sweetusbrootfast='%s' ignored (only 0 or 1), fast receive stays off\n";
	static const char *const bad[] = { "2", "on", "", "11", "-1", "1x", "12345678901" };
	static const char *const bad_shown[] = { "2", "on", "", "11", "-1", "1x", "1234567" };   /* the 8-byte buffer keeps 7 chars */
	char want[160];
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; host_mode = H_GOOD; stub_arg_fast_str = "1";   /* on */
	CHECK(sweet_usbroot_rootdev(&dev) == 0 && ur_fast && stub_fast_enable == 1 && stub_wakeups == 1 && !stub_hurry);
	CHECK(strstr(cap, "SWEET USBROOT: fast receive on (sweetusbrootfast=1, patch 0172)") != NULL && strstr(cap, "GAP") == NULL);
	CHECK(strstr(cap, "stats every 256 answers\n") != NULL);
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; host_mode = H_GOOD; stub_arg_fast_str = "0";   /* 0: off, silent */
	CHECK(sweet_usbroot_rootdev(&dev) == 0 && !ur_fast && stub_fast_enable == 0 && stub_hurry_on == 0);
	CHECK(strstr(cap, "GAP") == NULL && strstr(cap, "sweetusbrootfast") == NULL && strstr(cap, "fast receive") == NULL);
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; host_mode = H_GOOD;                            /* absent: off, silent */
	CHECK(sweet_usbroot_rootdev(&dev) == 0 && !ur_fast && stub_fast_enable == 0 && stub_hurry_on == 0);
	CHECK(strstr(cap, "GAP") == NULL && strstr(cap, "sweetusbrootfast") == NULL && strstr(cap, "fast receive") == NULL);
	for (i = 0; i < (int)(sizeof(bad) / sizeof(bad[0])); i++) {                                                  /* anything else: loud, off */
		reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; host_mode = H_GOOD; stub_arg_fast_str = bad[i];
		CHECK(sweet_usbroot_rootdev(&dev) == 0 && !ur_fast && stub_fast_enable == 0 && stub_hurry_on == 0);
		snprintf(want, sizeof(want), gap_fmt, bad_shown[i]);
		CHECK(strstr(cap, want) != NULL);
		CHECK(strstr(cap, "fast receive on") == NULL);
	}
	reset(); ur.active = ur.ready = false; stub_arg_sweetusb = 2; stub_board_gs101 = 0; stub_arg_fast_str = "1";  /* refused board first */
	CHECK(sweet_usbroot_rootdev(&dev) == ENODEV && !ur_fast && stub_fast_enable == 0);
	CHECK(stub_lock_depth == 0);
}

int
main(void)
{
	test_rx();
	test_xfer_and_strategy();
	test_rootdev();
	test_cow();
	test_fast();
	cow_release();
	if (fails) {
		fprintf(stdout, "RESULT: FAIL %d of %d checks\n", fails, checks);
		return 1;
	}
	fprintf(stdout, "RESULT: PASS %d checks\n", checks);
	return 0;
}
