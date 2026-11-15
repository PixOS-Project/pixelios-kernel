/* Host test of patch 0189 (bsd/dev/sweet_bt.c): the REAL file is #included, kernel headers are stubs (tools/test_sweet_bt_c.py).
 * Oracle: docs/research/109-bt-conformance-matrix.md rows M10' M11 M15-M17 M19 M20 M21-M23 and the rule-8 GAP texts of section 4. */
#include "kstub.h"
#include "sweet_bt.c"
#undef printf   /* the driver's printf is the log recorder; the test's own reports use stdio */

/* ---- fakes and recorders ---- */
static char logbuf[1 << 16];
static size_t loglen;
int sbt_printf(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap);
	va_end(ap);
	if (n > 0) loglen += (size_t)n;
	return n;
}
static void log_reset(void) { logbuf[0] = 0; loglen = 0; }
static int count_in(const char *needle)
{
	int n = 0;
	for (const char *p = logbuf; (p = strstr(p, needle)) != NULL; p += strlen(needle)) n++;
	return n;
}
static int lines_with(const char *needle) { return count_in(needle); }

struct kern_ctl_reg regs[8];
int nregs, ctl_fail_name_idx = -1;
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref)
{
	if (nregs == ctl_fail_name_idx) { nregs++; return EEXIST; }
	regs[nregs] = *r;
	*ref = (kern_ctl_ref)(uintptr_t)(0x1000 + nregs);
	nregs++;
	return 0;
}
int dt_has_node = 1, nodes;
char node_names[8][32];
int node_modes[8], node_uid[8], node_gid[8];
int cdev_calls; const struct cdevsw *cdev_seen;
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; cdev_calls++; cdev_seen = c; return 77; }
void *devfs_make_node(dev_t dev, int type, uid_t u, gid_t g, int mode, const char *fmt, ...)
{
	va_list ap; va_start(ap, fmt);
	vsnprintf(node_names[nodes], 32, fmt, ap);
	va_end(ap);
	node_modes[nodes] = mode; node_uid[nodes] = (int)u; node_gid[nodes] = (int)g;
	if (type != DEVFS_CHAR || major(dev) != 77 || minor(dev) != (unsigned)nodes) { fprintf(stderr, "bad node dev\n"); exit(1); }
	nodes++;
	return (void *)1;
}
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; return (dt_has_node && !strcmp(p, "/bluetooth")) ? kSuccess : 1; }
int freed;
errno_t ctl_enqueuedata(kern_ctl_ref r, u_int32_t u, void *d, size_t l, u_int32_t f) { (void)r; (void)u; (void)d; (void)l; (void)f; return 0; }
int attach_calls;
void sweet_bt_uart_attach(void) { attach_calls++; }   /* the real backend is tested by test_sweet_bt_uart.c */
void mbuf_freem(mbuf_t m) { freed++; (void)m; }

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL line %d: %s -- ", __LINE__, #c); printf(__VA_ARGS__); printf("\n  log: %s\n", logbuf); } } while (0)

typedef int (*openfn)(dev_t, int, int, struct proc *);

/* backend fake */
static int be_calls[8], be_ret = 0;
static int be_get(void *b, size_t *l) { (void)b; (void)l; be_calls[0]++; return be_ret; }
static int be_set(const void *b, size_t l) { (void)b; (void)l; be_calls[1]++; return be_ret; }
static int be_tx(const void *d, size_t l) { (void)d; be_calls[2] += (int)l; return be_ret; }
static int be_cts(int *c) { *c = 0x2000; be_calls[3]++; return be_ret; }
static int be_open(int w) { be_calls[4] += 1 + w; return be_ret; }
static int be_close(int w) { be_calls[5] += 1 + w; return be_ret; }
static const struct sweet_bt_ops fake_ops = { be_get, be_set, be_tx, be_cts, be_open, be_close };

int main(void)
{
	struct termios t; size_t len; void *ui = NULL; struct sockaddr_ctl sac = {0};
	struct mbuf m2 = { "xyz", 3, NULL }, m1 = { "ab", 2, &m2 };

	_Static_assert(sizeof(struct termios) == 0x48, "termios size");

	/* T1: no /bluetooth node -> nothing registered, one GAP, and a second init stays a no-op */
	dt_has_node = 0; sbt_printf("");
	sweet_bt_init();
	CHECK(nregs == 0 && cdev_calls == 0 && nodes == 0, "registered without the ADT node");
	CHECK(lines_with("BT GAP: node-bluetooth") == 1, "no node GAP");
	sweet_bt_init();
	CHECK(lines_with("BT GAP: node-bluetooth") == 1, "init not idempotent");

	/* T2: with the node */
	memset(&sbt, 0, sizeof(sbt)); log_reset(); dt_has_node = 1;
	sweet_bt_init(); sweet_bt_init();
	CHECK(nregs == 2, "ctl_register calls %d", nregs);
	CHECK(!strcmp(regs[0].ctl_name, "com.apple.uart.bluetooth") && !strcmp(regs[1].ctl_name, "com.apple.uart.sk.bluetooth"), "names");
	for (int i = 0; i < 2; i++) {
		CHECK((regs[i].ctl_flags & 0x1) == 0 && regs[i].ctl_flags == 0, "flags %#x must be 0 (no PRIVILEGED, SOCK_DGRAM)", regs[i].ctl_flags);
		CHECK(regs[i].ctl_connect && regs[i].ctl_disconnect && regs[i].ctl_send && regs[i].ctl_setopt && regs[i].ctl_getopt, "callbacks");
	}
	CHECK(nodes == 3 && !strcmp(node_names[0], "btwake") && !strcmp(node_names[1], "btpoweroff") && !strcmp(node_names[2], "btreset"), "dev nodes");
	CHECK(node_modes[0] == 0666, "node mode");
	CHECK(attach_calls == 1 && lines_with("BT GAP: skywalk rows M12-M14") == 1, "boot GAP lines");

	ctl_connect_func conn = regs[0].ctl_connect;
	ctl_disconnect_func disc = regs[0].ctl_disconnect;
	kern_ctl_ref r0 = (kern_ctl_ref)(uintptr_t)0x1000, r1 = (kern_ctl_ref)(uintptr_t)0x1001;

	/* T3: connect (accepted, loudly), second connect EBUSY, disconnect frees, other control independent */
	log_reset();
	CHECK(conn(r0, &sac, &ui) == 0 && ui != NULL, "first connect");
	CHECK(lines_with("BT GAP: kctl com.apple.uart.bluetooth connect accepted but no UART backend") == 1, "connect GAP");
	CHECK(conn(r0, &sac, &ui) == EBUSY && lines_with("already has a client") == 1, "second connect");
	CHECK(regs[1].ctl_connect(r1, &sac, &ui) == EBUSY && lines_with("already has a client") == 2, "sk control refused while the plain one is connected (one UART, one client; review T-e930 r1 F6)");
	CHECK(disc(r0, 1, ui) == 0 && regs[1].ctl_connect(r1, &sac, &ui) == 0, "disconnect frees the UART for the other control");
	CHECK(regs[1].ctl_disconnect(r1, 1, ui) == 0 && conn(r0, &sac, &ui) == 0, "reconnect after disconnect");
	CHECK(conn((kern_ctl_ref)(uintptr_t)0x9999, &sac, &ui) == EINVAL, "unknown ref");

	/* T4: sockopts without a backend are errors with GAP lines */
	log_reset();
	CHECK(regs[0].ctl_setopt(r0, 1, ui, (int)0x80487414, &t, sizeof(t)) == ENOTSUP && lines_with("BT GAP: kctl TIOCSETA ignored") == 1, "SETA");
	len = sizeof(t);
	CHECK(regs[0].ctl_getopt(r0, 1, ui, (int)0x40487413, &t, &len) == ENOTSUP && lines_with("BT GAP: kctl TIOCGETA unsupported") == 1, "GETA");
	CHECK(regs[0].ctl_setopt(r0, 1, ui, (int)0x80487414, &t, 5) == EINVAL && lines_with("TIOCSETA length 5") == 1, "SETA bad len");
	len = 5;
	CHECK(regs[0].ctl_getopt(r0, 1, ui, (int)0x40487413, &t, &len) == EINVAL && lines_with("TIOCGETA buffer 5") == 1, "GETA bad len");
	CHECK(regs[0].ctl_setopt(r0, 1, ui, 0x1234, &t, sizeof(t)) == ENOPROTOOPT && lines_with("setsockopt 0x1234 unknown") == 1, "unknown setopt");
	len = sizeof(t);
	CHECK(regs[0].ctl_getopt(r0, 1, ui, 0x1234, &t, &len) == ENOPROTOOPT && lines_with("getsockopt 0x1234 unknown") == 1, "unknown getopt");

	/* T5: send frees the chain and fails loudly */
	log_reset(); freed = 0;
	CHECK(regs[0].ctl_send(r0, 1, ui, &m1, 0) == ENXIO && freed == 1 && lines_with("BT GAP: kctl read/write path absent") == 1, "send");

	/* T6: TIOCMGET hook */
	log_reset(); int cts = 0;
	CHECK(sweet_bt_kctl_ioctl("com.apple.uart.bluetooth", 0x4004746aul, &cts) == ENOTSUP && lines_with("BT GAP: kctl TIOCMGET unsupported") == 1, "TIOCMGET");
	CHECK(sweet_bt_kctl_ioctl("com.apple.uart.sk.bluetooth", 0x4004746aul, &cts) == ENOTSUP && lines_with("BT GAP: kctl TIOCMGET unsupported") == 2, "TIOCMGET sk");
	CHECK(sweet_bt_kctl_ioctl("com.apple.uart.bluetooth", 0x1ul, &cts) == ENOTSUP && lines_with("ioctl 0x1 unknown") == 1, "other ioctl");
	log_reset();
	CHECK(sweet_bt_kctl_ioctl("com.apple.net.utun_control", 0x4004746aul, &cts) == ENOTSUP && sweet_bt_kctl_ioctl(NULL, 0x4004746aul, &cts) == ENOTSUP && loglen == 0, "other controls are untouched and silent");

	/* T7: devnodes refuse (never 0) with their own key; close of a refused open is fine; bad minor */
	log_reset();
	const struct cdevsw *cd = cdev_seen; openfn op = (openfn)cd->d_open, cl = (openfn)cd->d_close;
	for (int i = 0; i < 3; i++) {
		CHECK(op(makedev(77, i), 0, 0, NULL) == ENXIO, "open minor %d", i);
		CHECK(cl(makedev(77, i), 0, 0, NULL) == 0, "close minor %d", i);
	}
	CHECK(lines_with("BT GAP: devnode /dev/btwake ") == 1 && lines_with("BT GAP: devnode /dev/btpoweroff ") == 1 && lines_with("BT GAP: devnode /dev/btreset ") == 1, "devnode GAPs");
	CHECK(op(makedev(77, 3), 0, 0, NULL) == ENXIO && cl(makedev(77, 3), 0, 0, NULL) == ENXIO, "minor 3");

	/* T8: rate limit: 100 more opens print n<=8 and powers of two only, with the count */
	log_reset();
	for (int i = 0; i < 100; i++) (void)op(makedev(77, 0), 0, 0, NULL);
	{ int n = lines_with("BT GAP: devnode /dev/btwake"); CHECK(n == 8, "T7 made 3 counted refusals; calls 4..103 print counts 4..8 and 16,32,64 = 8 lines, got %d", n); }
	CHECK(lines_with(" x64\n") == 1 && lines_with(" x65\n") == 0 && lines_with(" x9\n") == 0, "power-of-two count suffix");

	/* T9: ctl_register failing for one name is reported and the other still works */
	memset(&sbt, 0, sizeof(sbt)); log_reset(); nregs = 0; nodes = 0; ctl_fail_name_idx = 0;
	sweet_bt_init();
	CHECK(lines_with("BT GAP: kctl com.apple.uart.bluetooth not registered (ctl_register error") == 1, "register failure GAP");
	CHECK(nregs == 2 && !strcmp(regs[1].ctl_name, "com.apple.uart.sk.bluetooth"), "second still registered");
	CHECK(sbt_index((kern_ctl_ref)(uintptr_t)0x1000) == -1 && sbt_index((kern_ctl_ref)(uintptr_t)0x1001) == 1, "failed ref not tracked, second tracked");
	ctl_fail_name_idx = -1;

	/* T10: with a backend every hook delegates, returns its value and prints no GAP */
	memset(&sbt, 0, sizeof(sbt)); log_reset(); nregs = 0; nodes = 0;
	sweet_bt_init(); sweet_bt_set_ops(&fake_ops); log_reset();
	r0 = (kern_ctl_ref)(uintptr_t)0x1000;
	CHECK(regs[0].ctl_connect(r0, &sac, &ui) == 0, "connect with backend");
	CHECK(regs[0].ctl_setopt(r0, 1, ui, (int)0x80487414, &t, sizeof(t)) == 0 && be_calls[1] == 1, "set delegates");
	len = sizeof(t);
	CHECK(regs[0].ctl_getopt(r0, 1, ui, (int)0x40487413, &t, &len) == 0 && be_calls[0] == 1, "get delegates");
	freed = 0;
	CHECK(regs[0].ctl_send(r0, 1, ui, &m1, 0) == 0 && be_calls[2] == 5 && freed == 1, "tx delegates over the chain (%d bytes)", be_calls[2]);
	CHECK(sweet_bt_kctl_ioctl("com.apple.uart.bluetooth", 0x4004746aul, &cts) == 0 && cts == 0x2000, "cts delegates");
	CHECK(op(makedev(77, 2), 0, 0, NULL) == 0 && be_calls[4] == 3 && cl(makedev(77, 2), 0, 0, NULL) == 0 && be_calls[5] == 3, "switch delegates");
	CHECK(loglen == 0, "a working backend must not print GAPs: %s", logbuf);
	be_ret = EIO;
	CHECK(regs[0].ctl_send(r0, 1, ui, &m1, 0) == EIO && op(makedev(77, 0), 0, 0, NULL) == EIO, "backend errors pass through");

	if (fails) { printf("RESULT: FAIL bt-transport-patch host test (%d checks failed)\n", fails); return 1; }
	printf("RESULT: PASS bt-transport-host-test\n");
	return 0;
}
