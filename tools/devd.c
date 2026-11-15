/*
 * devd: Lightweight Hot-Reload Daemon for PixOS (TP-027)
 * ======================================================
 * Runs on Google Pixel 6a (bluejay) under iOS / XNU userland.
 * Binds strictly to ue0 (192.168.7.2:41140) or 127.0.0.1 in test mode.
 * Gate, auth, peer and RUN policy: review gemini-devd-r1 F1, research 111.
 *
 * Hardened Implementation (Complies with CERT C, MISRA-C, and Stevens UNP):
 *   H1. Deadline-Bounded I/O: every peer interaction (command line read,
 *       response send, PUSH payload) runs against a monotonic deadline via
 *       nonblocking send/recv + poll(). SO_SNDTIMEO alone is not enough: a
 *       timed-out send() may return a partial count and the retry loop
 *       re-earns the timeout, wedging the serial daemon 40s+ on a deaf
 *       client (observed via /proc while flooded by `RUN yes`). A dead or
 *       trickling peer now costs at most one deadline window, and a peer
 *       that stalls mid-RUN/PULL is dropped on the spot.
 *   H2. Drained Line Framing: Consumes oversized commands to next '\n' to prevent
 *       protocol desynchronization.
 *   H2b. PUSH Desync Containment (review gemini-devd-r1 F2): the payload of a
 *       PUSH is exactly <len> bytes after the header line, whether or not the
 *       verdict is already out. A PUSH rejected on path policy (valid length
 *       1..MAX_PUSH_BYTES) is answered with ERR first, then its <len> payload
 *       bytes are skipped under one deadline and discarded, never parsed as
 *       commands, even when the client sends them after the ERR; the next byte
 *       is the next command, so a command pipelined behind the payload is not
 *       swallowed. A header whose payload cannot be attributed (bad format,
 *       length 0 needs none, length over the cap or past size_t) is answered
 *       with ERR and cut right after it (length 0 too, review devd-r1 F1: the
 *       bytes of a file that reported size 0 were run as commands). A PUSH accepted by policy but failing in open(), the transfer
 *       or rename() also drops the connection after its ERR. A peer that
 *       stalls or resets while its payload is skipped is dropped. PUSH parsing
 *       refuses trailing tokens and any %zu integer overflow.
 *   H3. Path Allowlist: PUSH/PULL paths must equal the kernel overlay's
 *       allowlist (xnu/patches/0117-sweet-dev-overlay.patch sd_allow[]):
 *       /usr/lib/, /usr/local/, /private/var/dev/ (anything below), exactly
 *       /System/Library/xpc/launchd.plist, and
 *       /System/Library/LaunchDaemons/org.pixelios.<name> (no further '/'), and since patch 0280 exactly
 *       /Applications/BTApp.app/BTApp and /private/var/mobile/Library/Preferences/com.apple.MobileBluetooth.debug.plist;
 *       + /tmp/ in test mode. tools/test_devd_hardening.py compares k_allow[]
 *       with the patch. Traversal (/..) and empty segments (//) rejected. The
 *       policy is re-enforced on the realpath()-resolved path, so a symlink
 *       inside an allowed root cannot escape to /private/etc/. PUSH also applies
 *       the kernel's sd_path_check() rules (review devd-r1 F4): no .sweettmp or
 *       .sweetorig name, no '.' or empty component, no component over 245 bytes,
 *       and no symlink component at all (lstat walk of the client's path).
 *   H3b. PUSH CAN BRICK THE USERLAND (review devd-r1 F4). The allowlist holds
 *       /usr/lib/ (dyld, libSystem), /System/Library/xpc/launchd.plist and the
 *       org.pixelios.* LaunchDaemons: replacing one with a bad file can stop the
 *       userland from booting, and devd persists across boots (H12). Only
 *       userdata is reachable, so it stays reversible (AGENTS rule 1). A
 *       replaced regular file keeps its FIRST original as <path>.sweetorig (as
 *       the kernel overlay does, 0117:527-529). Recovery, either: (1) a kernel
 *       overlay RAM boot carrying the original file (tools/mkdevoverlay.py +
 *       sweetdev=1 copies it back in bsd_init before launchd [read from source,
 *       0117 header]; sweetdev=restore renames .sweetorig back [read from source,
 *       never run on hardware]); (2) the pre-approved userdata reflash
 *       (tools/fflash-userdata.sh). A NEW pushed file gets mode 0755 under
 *       /usr/local/ and /private/var/dev/ (/tmp/ in test mode) and 0644
 *       elsewhere; a REPLACED regular file keeps its own permission bits (capped
 *       at 0777, review devd-r2 N1: a 0644 dyld stops every exec). Set with
 *       fchmod() (not umask dependent).
 *   H4. PUSH Guards: Bounds 1 <= len <= 40 MiB (zero-length truncation blocked).
 *       Atomic write via .sweettmp with fsync() before rename. The temp is a
 *       fresh regular file: a stale one is unlinked, a non-regular one refused,
 *       open is O_CREAT|O_EXCL|O_NOFOLLOW; it is unlinked when the rename fails.
 *   H5. PULL Guards: S_ISREG enforced (FIFOs/devices blocked from hanging daemon).
 *   H6. RUN Execution Bounds: NO shell. `RUN /abs/path [arg ...]` is split
 *       on single spaces (max 16 args, no quoting, no metacharacter
 *       interpretation) and run with fork + execv. The program must be an
 *       absolute path under /usr/local/ or /private/var/dev/ (the dev-pushed
 *       locations; + /tmp/ in test mode), no `..`, realpath()-checked again,
 *       a regular file with an exec bit; anything else is rejected with a
 *       SWEET GAP line. Answer framing (review devd-r1 F3):
 *       `RUNID <16 random hex>` first, then the output, then `RUNEND <same id>
 *       RC: <n>` on a line of its own (a break is added when the output lacked
 *       one); a refused request gets one ERR line and no RUNID. The client
 *       trusts only that exact last line plus EOF. The output pipe is blocking
 *       but every read follows a poll() against a monotonic deadline (30s). The child runs in its own process group; on expiry the
 *       whole group is SIGKILLed, so a backgrounded grandchild leaves no
 *       orphans. Before any blocking waitpid the child is checked with WNOHANG
 *       and killed if still alive (stalled client + pipe-blocked child used to
 *       freeze devd in waitpid forever).
 *   H7. Signal Whitelist: Restricts to SIGHUP, SIGINT, SIGKILL, SIGUSR1,
 *       SIGUSR2, SIGTERM (SIGQUIT dropped: default action dumps core).
 *       Membership implies signo > 0, so kill()'s negative-signo
 *       process-GROUP semantics are unreachable. Rejects pid <= 1 (covers
 *       pid 0 = our own group and negative pids = group kills) and
 *       pid == getpid() (self-kill blocked).
 *   H8. Key-File Gate (review F1, research 111 1.7): devd starts only if
 *       /private/var/dev/devd.key exists, is a regular file, owner uid 0,
 *       mode 0600, content 64 lowercase hex chars (+ optional newline) = 32
 *       random bytes; else `SWEET GAP: devd gate: ...` and exit 1. The
 *       sweetdevd boot-arg is not read (patch 0177 hides it). The overlay
 *       (kernel patch 0117, itself gated by sweetdev=1) puts the file there at
 *       stage time; devd refuses every PUSH/PULL of it (H12). `--key-file PATH` (with --test-mode only)
 *       overrides the path and then expects the file owned by the caller.
 *   H10. Challenge-Response Auth: on accept devd sends `CHALLENGE <32 hex>`
 *       (16 random bytes); the first client line must be `AUTH <64 hex>` =
 *       HMAC-SHA256(key, challenge) in lowercase hex, compared in constant
 *       time. Anything else, or 5 s of silence, => `ERR: auth`, close and a
 *       `SWEET GAP: devd rejected AUTH reason=...` line. No command is parsed
 *       before auth; every connection gets a new challenge (no replay).
 *       SHA-256/HMAC are self-contained; `devd --selftest` checks them against
 *       FIPS 180 and RFC 4231 vectors.
 *   H11. Peer/Interface Check: phone mode binds 192.168.7.2 only, requires at
 *       startup that 192.168.7.2 belongs to interface ue0 (getifaddrs), and
 *       drops (logged) every peer that is not 192.168.7.1 after accept().
 *       Test mode accepts only 127.0.0.1. The interface, addresses and the key
 *       owner uid are the DEVD_PHONE_* constants below; they are overridable at
 *       compile time only so tools/test_devd_hardening.py (test_phone_mode) can
 *       run this phone path on loopback without --test-mode (review devd-r1 F2).
 *   H9. Auditing: Logs all operations with DEVD: prefix to stderr.
 *   H12. Persistence and "devd off" (review devd-r1 F12). The key file, the
 *       binary and the launchd job (tools/devd-stage) live on userdata and
 *       persist: devd starts on EVERY later boot, boots without sweetdev=1
 *       included. sweetdev=restore renames back only the <path>.sweetorig of
 *       archive entries (0117 header), so the key and the binary stay. "devd
 *       off" = restore the launchd plist: an overlay RAM boot with sweetdev=
 *       restore whose archive lists /System/Library/xpc/launchd.plist [read
 *       from source; never run on hardware], or the userdata reflash.
 *   H13. Trust (review devd-r1 F16). RUN plus PUSH is arbitrary root code for
 *       whoever holds the key: the allowlists guard against mistakes, they are
 *       not a security boundary. devsh does not authenticate devd (anything
 *       answering on the phone address receives a HMAC of its own challenge and
 *       the pushed bytes). One client at a time: a silent unauthenticated peer
 *       delays the next one by up to 5 s (backlog 10).
 *   H14. RUN child (review devd-r1 F13): fixed environment (PATH, HOME), SIGPIPE
 *       at its default action; the process group is SIGKILLed on timeout, on a
 *       lost client AND after a normal exit (survivors get a SWEET GAP line), so
 *       a daemon started with RUN must setsid() to stay alive.
 *   H15. accept() (review devd-r1 F14): ECONNABORTED/ENOBUFS/ENOMEM/EMFILE/ENFILE
 *       are retried after 1 s with a SWEET GAP line; exit 1 after 10 in a row.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <limits.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <poll.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include <stdint.h>
#include <ifaddrs.h>

#define DEVD_VERSION "1.3.0"
#define DEFAULT_PORT 41140
#define BUFFER_SIZE 16384
#define MAX_PUSH_BYTES (40u * 1024u * 1024u) /* 40 MiB kernel overlay limit */
#define DEVD_CMD_CAP 1024u
/* Phone-mode constants (review devd-r1 F2). Compile-time overridable ONLY so a host test can build the
 * phone code path against loopback (-DDEVD_PHONE_IFACE=\"lo\" -DDEVD_PHONE_ADDR=\"127.0.0.2\"
 * -DDEVD_PHONE_PEER=\"127.0.0.1\" -DDEVD_PHONE_KEY_UID=<n> -DDEVD_KEY_PATH=\"...\") and run it WITHOUT
 * --test-mode; the shipped binary uses the defaults below. */
#ifndef DEVD_PHONE_IFACE
#define DEVD_PHONE_IFACE "ue0"
#endif
#ifndef DEVD_PHONE_ADDR
#define DEVD_PHONE_ADDR "192.168.7.2"
#endif
#ifndef DEVD_PHONE_PEER
#define DEVD_PHONE_PEER "192.168.7.1"
#endif
#ifndef DEVD_PHONE_KEY_UID
#define DEVD_PHONE_KEY_UID 0
#endif
#ifndef DEVD_KEY_PATH
#define DEVD_KEY_PATH "/private/var/dev/devd.key"
#endif
#define DEVD_RUN_MAX_ARGS 16
#ifndef DEVD_AUTH_TIMEOUT_MS
#define DEVD_AUTH_TIMEOUT_MS 5000u /* AUTH line must arrive within this */
#endif
#ifndef DEVD_IO_TIMEOUT_SEC
#define DEVD_IO_TIMEOUT_SEC 10
#endif
/* accept() errors that can pass (review devd-r1 F14): retried every DEVD_ACCEPT_RETRY_SEC with a GAP line;
 * devd exits 1 after DEVD_ACCEPT_FAIL_MAX in a row (nothing restarts it, KeepAlive is off on purpose). */
#ifndef DEVD_ACCEPT_FAIL_MAX
#define DEVD_ACCEPT_FAIL_MAX 10
#endif
#ifndef DEVD_ACCEPT_RETRY_SEC
#define DEVD_ACCEPT_RETRY_SEC 1
#endif
#define DEVD_PUSH_DEADLINE_MS 60000u /* total wall budget per PUSH payload */
/* Compile-time overridable so host tests exercise the timeout path fast. */
#ifndef DEVD_RUN_TIMEOUT_SEC
#define DEVD_RUN_TIMEOUT_SEC 30
#endif

/* PUSH/PULL allowlist: MUST equal sd_allow[] of xnu/patches/0117-sweet-dev-overlay.patch
 * (same entries, same order, same kinds; tools/test_devd_hardening.py parses both and
 * fails if they diverge). K_DIR: anything below the directory; K_EXACT: that path only;
 * K_NAME: one name starting with the prefix, no further '/'. */
enum { K_DIR, K_NAME, K_EXACT };
static const struct { const char *pfx; int kind; } k_allow[] = {
    { "/usr/lib/", K_DIR }, { "/usr/local/", K_DIR }, { "/private/var/dev/", K_DIR },
    { "/System/Library/xpc/launchd.plist", K_EXACT },
    { "/System/Library/LaunchDaemons/org.pixelios.", K_NAME },
    { "/Applications/BTApp.app/BTApp", K_EXACT },                                          /* patch 0280 */
    { "/private/var/mobile/Library/Preferences/com.apple.MobileBluetooth.debug.plist", K_EXACT },
    { "/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist", K_EXACT },   /* patch 0317 (MG cache, ALL-ON T-36) */
};
/* PULL-only exact paths (ALL-ON T-18): read-only witness files that live outside the overlay roots. Never a PUSH
 * target, so they are NOT part of the kernel-equal k_allow[] (0117 governs writes only). The resolved path must
 * match too (realpath re-check), so a /var/tmp symlink to anywhere else is refused. */
static const char *const k_pull_only[] = {
    "/private/var/tmp/sweetgl.log",   /* libsweetgl log, tools/sweetgl-eagl/dylib_main.m opens /var/tmp/sweetgl.log */
};
/* RUN programs: only the dev-pushed locations. */
static const char *const k_run_prefixes[] = {
    "/usr/local/",
    "/private/var/dev/",
};
static const char k_test_prefix[] = "/tmp/";
/* The RUN child's whole environment (review devd-r1 F13): devd's own environment is never inherited. */
static char *const k_run_env[] = { "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "HOME=/var/root", NULL };

static void devd_log(const char *fmt, ...) {
    va_list ap;
    fputs("DEVD: ", stderr);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

/* F3 (review gemini-devd-r1): every descriptor devd opens is close-on-exec so
 * a RUN child that outlives its connection cannot keep the listening or a
 * client socket alive (a restart then failed with "Address already in use").
 * fcntl() instead of SOCK_CLOEXEC/O_CLOEXEC/accept4(): not available on every
 * Darwin SDK; devd is single-threaded and forks only after the fds exist.
 * Returns fd, or -1 (fd closed) when the flag cannot be set. */
static int set_cloexec(int fd) {
    int fl = fcntl(fd, F_GETFD);
    if (fl < 0 || fcntl(fd, F_SETFD, fl | FD_CLOEXEC) < 0) {
        int e = errno;
        devd_log("FD_CLOEXEC failed on fd %d (errno=%d)", fd, e);
        errno = e;
        return -1;
    }
    return 0;
}

static int cloexec_fd(int fd) {
    if (fd < 0)
        return fd;
    if (set_cloexec(fd) < 0) {
        int e = errno;
        close(fd);
        errno = e;
        return -1;
    }
    return fd;
}

/* Rule 8: every rejected request is loud. `cmd` is the request verb ("?" when
 * the line had none we could name); `reply` is the ERR line sent to the peer,
 * its text after "ERR: " up to the first newline is the reason. */
static void devd_gap(const char *cmd, const char *reply) {
    char reason[96];
    size_t n = 0;
    const char *r = strncmp(reply, "ERR: ", 5) == 0 ? reply + 5 : reply;
    while (r[n] != '\0' && r[n] != '\n' && n < sizeof(reason) - 1) {
        reason[n] = r[n];
        n++;
    }
    reason[n] = '\0';
    fprintf(stderr, "SWEET GAP: devd rejected %s reason=%s\n", cmd, reason);
    fflush(stderr);
}

/* Logs the gap, then sends the ERR line. */
static void reject(int fd, const char *cmd, const char *reply);

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

/* Reads and discards exactly n bytes of a rejected PUSH's payload under one
 * deadline (the total PUSH budget). 0 = skipped, -1 = EOF, error or deadline
 * (caller drops the connection). */
static int skip_payload(int fd, size_t n) {
    char d[BUFFER_SIZE];
    uint64_t deadline = now_ms() + DEVD_PUSH_DEADLINE_MS;
    while (n > 0) {
        size_t want = n < sizeof(d) ? n : sizeof(d);
        ssize_t r = recv(fd, d, want, MSG_DONTWAIT);
        if (r > 0) {
            n -= (size_t)r;
            continue;
        }
        if (r == 0) {
            devd_log("PUSH: peer closed with %zu payload bytes still to skip", n);
            return -1;
        }
        if (errno == EINTR) continue;
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
            devd_log("PUSH: payload skip recv failed (errno=%d)", errno);
            return -1;
        }
        {
            struct pollfd pf;
            int64_t remain = (int64_t)(deadline - now_ms());
            int pr;
            if (remain <= 0) {
                devd_log("PUSH: payload skip exceeded its budget (%zu bytes left)", n);
                return -1;
            }
            pf.fd = fd;
            pf.events = POLLIN;
            pf.revents = 0;
            pr = poll(&pf, 1, (int)remain);
            if (pr < 0 && errno != EINTR) {
                devd_log("PUSH: payload skip poll failed (errno=%d)", errno);
                return -1;
            }
        }
    }
    return 0;
}

/* Deadline-bounded all-or-nothing sender. A blocking send() guarded only
 * by SO_SNDTIMEO is NOT sufficient: a timed-out send() can return a
 * partial count, and each retry re-earns the timeout, so a deaf client
 * wedged this serial daemon for 40s+ per RUN (observed via /proc). Every
 * send here is nonblocking and POLLOUT is polled against one monotonic
 * budget covering the whole buffer. Returns -1 when the peer cannot take
 * the data within the budget. */
static int send_all(int fd, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    uint64_t deadline = now_ms() + (uint64_t)DEVD_IO_TIMEOUT_SEC * 1000u;
    while (len > 0) {
        ssize_t n = send(fd, p, len, MSG_DONTWAIT);
        if (n < 0) {
            struct pollfd pf;
            int64_t remain;
            int pr;
            if (errno == EINTR) continue;
            if (errno != EAGAIN && errno != EWOULDBLOCK) {
                devd_log("send failed (errno=%d)", errno);
                return -1;
            }
            remain = (int64_t)(deadline - now_ms());
            if (remain <= 0) {
                devd_log("send stalled: peer not reading within %ds budget",
                         DEVD_IO_TIMEOUT_SEC);
                return -1;
            }
            pf.fd = fd;
            pf.events = POLLOUT;
            pf.revents = 0;
            pr = poll(&pf, 1, (int)remain);
            if (pr < 0) {
                if (errno == EINTR) continue;
                devd_log("send poll failed (errno=%d)", errno);
                return -1;
            }
            if (pr == 0) {
                devd_log("send stalled: peer not reading within %ds budget",
                         DEVD_IO_TIMEOUT_SEC);
                return -1;
            }
            continue;
        }
        if (n == 0) {
            devd_log("send returned 0 (fd=%d)", fd);
            return -1;
        }
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static void send_str(int fd, const char *s) {
    (void)send_all(fd, s, strlen(s));
}

static void reject(int fd, const char *cmd, const char *reply) {
    devd_gap(cmd, reply);
    send_str(fd, reply);
}

static void tune_client_socket(int fd) {
    struct timeval tv;
    tv.tv_sec = DEVD_IO_TIMEOUT_SEC;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

/* ---- SHA-256 (FIPS 180-4) and HMAC-SHA256 (RFC 2104), self-contained ---- */
struct sha256 {
    uint32_t h[8];
    uint8_t buf[64];
    uint64_t total;
    size_t fill;
};

static const uint32_t k_sha_k[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
};

static uint32_t rotr32(uint32_t x, unsigned n) { return (x >> n) | (x << (32u - n)); }

static void sha256_block(struct sha256 *c, const uint8_t *p) {
    uint32_t w[64], a, b, cc, d, e, f, g, h, t1, t2;
    unsigned i;
    for (i = 0; i < 16; i++)
        w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 |
               (uint32_t)p[4 * i + 2] << 8 | (uint32_t)p[4 * i + 3];
    for (i = 16; i < 64; i++) {
        uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    a = c->h[0]; b = c->h[1]; cc = c->h[2]; d = c->h[3];
    e = c->h[4]; f = c->h[5]; g = c->h[6]; h = c->h[7];
    for (i = 0; i < 64; i++) {
        t1 = h + (rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25)) + ((e & f) ^ (~e & g)) + k_sha_k[i] + w[i];
        t2 = (rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22)) + ((a & b) ^ (a & cc) ^ (b & cc));
        h = g; g = f; f = e; e = d + t1; d = cc; cc = b; b = a; a = t1 + t2;
    }
    c->h[0] += a; c->h[1] += b; c->h[2] += cc; c->h[3] += d;
    c->h[4] += e; c->h[5] += f; c->h[6] += g; c->h[7] += h;
}

static void sha256_init(struct sha256 *c) {
    static const uint32_t iv[8] = { 0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                                    0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u };
    memcpy(c->h, iv, sizeof(iv));
    c->total = 0;
    c->fill = 0;
}

static void sha256_update(struct sha256 *c, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    c->total += len;
    while (len > 0) {
        size_t take = 64 - c->fill;
        if (take > len) take = len;
        memcpy(c->buf + c->fill, p, take);
        c->fill += take;
        p += take;
        len -= take;
        if (c->fill == 64) {
            sha256_block(c, c->buf);
            c->fill = 0;
        }
    }
}

static void sha256_final(struct sha256 *c, uint8_t out[32]) {
    uint64_t bits = c->total * 8u;
    uint8_t pad = 0x80;
    uint8_t z = 0;
    uint8_t lenb[8];
    int i;
    sha256_update(c, &pad, 1);
    while (c->fill != 56)
        sha256_update(c, &z, 1);
    for (i = 0; i < 8; i++)
        lenb[i] = (uint8_t)(bits >> (56 - 8 * i));
    sha256_update(c, lenb, 8);
    for (i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(c->h[i] >> 24);
        out[4 * i + 1] = (uint8_t)(c->h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(c->h[i] >> 8);
        out[4 * i + 3] = (uint8_t)c->h[i];
    }
}

static void hmac_sha256(const uint8_t *key, size_t klen, const uint8_t *msg, size_t mlen,
                        uint8_t out[32]) {
    uint8_t k0[64], pad[64], inner[32];
    struct sha256 c;
    size_t i;
    memset(k0, 0, sizeof(k0));
    if (klen > 64) {
        sha256_init(&c);
        sha256_update(&c, key, klen);
        sha256_final(&c, k0);
    } else {
        memcpy(k0, key, klen);
    }
    for (i = 0; i < 64; i++) pad[i] = k0[i] ^ 0x36;
    sha256_init(&c);
    sha256_update(&c, pad, 64);
    sha256_update(&c, msg, mlen);
    sha256_final(&c, inner);
    for (i = 0; i < 64; i++) pad[i] = k0[i] ^ 0x5c;
    sha256_init(&c);
    sha256_update(&c, pad, 64);
    sha256_update(&c, inner, 32);
    sha256_final(&c, out);
}

static void hex_encode(const uint8_t *in, size_t n, char *out) {
    static const char d[] = "0123456789abcdef";
    size_t i;
    for (i = 0; i < n; i++) {
        out[2 * i] = d[in[i] >> 4];
        out[2 * i + 1] = d[in[i] & 15];
    }
    out[2 * n] = '\0';
}

/* Strict lowercase hex: exactly 2*n chars, no other byte. */
static int hex_decode(const char *s, size_t n, uint8_t *out) {
    size_t i;
    for (i = 0; i < 2 * n; i++) {
        char ch = s[i];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))
            return 0;
    }
    for (i = 0; i < n; i++) {
        unsigned hi = (unsigned)(s[2 * i] <= '9' ? s[2 * i] - '0' : s[2 * i] - 'a' + 10);
        unsigned lo = (unsigned)(s[2 * i + 1] <= '9' ? s[2 * i + 1] - '0' : s[2 * i + 1] - 'a' + 10);
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return 1;
}

/* Constant-time equality of two n-byte buffers. */
static int ct_equal(const void *a, const void *b, size_t n) {
    const volatile uint8_t *pa = (const volatile uint8_t *)a;
    const volatile uint8_t *pb = (const volatile uint8_t *)b;
    uint8_t acc = 0;
    size_t i;
    for (i = 0; i < n; i++)
        acc |= (uint8_t)(pa[i] ^ pb[i]);
    return acc == 0;
}

/* Random bytes for the challenge. 0 = ok, -1 = no entropy (caller fails closed). */
static int random_bytes(uint8_t *buf, size_t n) {
#if defined(__APPLE__)
    arc4random_buf(buf, n);
    return 0;
#else
    int fd = open("/dev/urandom", O_RDONLY);
    size_t got = 0;
    if (fd < 0)
        return -1;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) { close(fd); return -1; }
        got += (size_t)r;
    }
    close(fd);
    return 0;
#endif
}

/* ---- key-file gate (H8) ---- */

/* Pure check of a stat result: regular file, owner want_uid, mode exactly 0600, size 64
 * or 65 (hex + optional newline). NULL = ok, else the reason. Injectable for host tests. */
static const char *key_stat_check(const struct stat *st, uid_t want_uid) {
    if (!S_ISREG(st->st_mode)) return "not a regular file";
    if (st->st_uid != want_uid) return "owner is not the expected uid";
    if ((st->st_mode & 07777) != 0600) return "mode is not 0600";
    if (st->st_size != 64 && st->st_size != 65) return "size is not 64 hex chars (+ newline)";
    return NULL;
}

/* Parses the 64-hex (+ optional '\n') key text into 32 bytes. */
static const char *key_text_parse(const char *buf, size_t n, uint8_t key[32]) {
    if (n == 65 && buf[64] == '\n')
        n = 64;
    if (n != 64) return "content is not 64 hex chars (+ newline)";
    if (!hex_decode(buf, 32, key)) return "content is not lowercase hex";
    return NULL;
}

/* 0 = key loaded; -1 = gate closed (SWEET GAP line already written). O_NOFOLLOW + fstat on
 * the opened fd: no check/use race on the path. */
static int key_file_load(const char *path, uid_t want_uid, uint8_t key[32]) {
    struct stat st;
    char buf[80];
    ssize_t n;
    const char *why;
    int fd = cloexec_fd(open(path, O_RDONLY | O_NOFOLLOW));
    if (fd < 0) {
        fprintf(stderr, "SWEET GAP: devd gate: cannot open key file %s (errno=%d)\n", path, errno);
        return -1;
    }
    if (fstat(fd, &st) != 0) {
        fprintf(stderr, "SWEET GAP: devd gate: cannot stat key file %s (errno=%d)\n", path, errno);
        close(fd);
        return -1;
    }
    why = key_stat_check(&st, want_uid);
    if (why != NULL) {
        fprintf(stderr, "SWEET GAP: devd gate: key file %s: %s\n", path, why);
        close(fd);
        return -1;
    }
    n = read(fd, buf, sizeof(buf));
    close(fd);
    if (n < 0) {
        fprintf(stderr, "SWEET GAP: devd gate: cannot read key file %s (errno=%d)\n", path, errno);
        return -1;
    }
    why = key_text_parse(buf, (size_t)n, key);
    memset(buf, 0, sizeof(buf));
    if (why != NULL) {
        fprintf(stderr, "SWEET GAP: devd gate: key file %s: %s\n", path, why);
        return -1;
    }
    return 0;
}

/* The key file devd loaded (review devd-r1 F12): never a PUSH/PULL target, however it is reached (own path,
 * symlink, hard link). Set once in main() after the gate. */
static char g_key_real[PATH_MAX];
static dev_t g_key_dev;
static ino_t g_key_ino;
static int g_key_known;

static int is_key_file(const char *rpath) {
    struct stat st;
    if (!g_key_known) return 1; /* fail closed */
    if (strcmp(rpath, g_key_real) == 0) return 1;
    return stat(rpath, &st) == 0 && st.st_dev == g_key_dev && st.st_ino == g_key_ino;
}

/* ---- interface check (H11) ---- */

/* 1 when interface ifname has the IPv4 address addr (network order). */
static int iface_has_addr(const char *ifname, in_addr_t addr) {
    struct ifaddrs *list = NULL, *it;
    int found = 0;
    if (getifaddrs(&list) != 0) {
        devd_log("getifaddrs failed (errno=%d)", errno);
        return 0;
    }
    for (it = list; it != NULL; it = it->ifa_next) {
        if (it->ifa_addr == NULL || it->ifa_addr->sa_family != AF_INET || it->ifa_name == NULL)
            continue;
        if (strcmp(it->ifa_name, ifname) == 0 &&
            ((struct sockaddr_in *)(void *)it->ifa_addr)->sin_addr.s_addr == addr) {
            found = 1;
            break;
        }
    }
    freeifaddrs(list);
    return found;
}

/* exec_only = 0: PUSH policy (k_allow[], kernel-equal); 1: RUN program policy (k_run_prefixes[]);
 * 2: PULL policy = k_allow[] plus the exact k_pull_only[] paths. Test mode adds /tmp/ for all. */
static int path_allowed(const char *path, int test_mode, int exec_only) {
    size_t i;
    size_t n;
    if (path[0] != '/' || strlen(path) > 511)
        return 0;
    if (strstr(path, "/..") != NULL || strstr(path, "//") != NULL)
        return 0;
    n = strlen(path);
    if (exec_only == 2) {
        for (i = 0; i < sizeof(k_pull_only) / sizeof(k_pull_only[0]); i++)
            if (strcmp(path, k_pull_only[i]) == 0)
                return 1;
    }
    if (exec_only == 1) {
        for (i = 0; i < sizeof(k_run_prefixes) / sizeof(k_run_prefixes[0]); i++) {
            size_t pl = strlen(k_run_prefixes[i]);
            if (n > pl && strncmp(path, k_run_prefixes[i], pl) == 0)
                return 1;
        }
    } else {
        for (i = 0; i < sizeof(k_allow) / sizeof(k_allow[0]); i++) {
            size_t pl = strlen(k_allow[i].pfx);
            if (n < pl || strncmp(path, k_allow[i].pfx, pl) != 0)
                continue;
            if (k_allow[i].kind == K_EXACT) {
                if (n != pl)
                    continue;
            } else if (k_allow[i].kind == K_NAME) {
                if (n == pl || memchr(path + pl, '/', n - pl) != NULL)
                    continue;
            } else if (n == pl) {
                continue;
            }
            return 1;
        }
    }
    if (test_mode) {
        if (strncmp(path, k_test_prefix, sizeof(k_test_prefix) - 1) == 0)
            return 1;
        /* Darwin resolves /tmp -> /private/tmp; accept both so the
         * realpath() re-check cannot lock legit test-mode /tmp use out. */
        if (strncmp(path, "/private/tmp/", 13) == 0)
            return 1;
    }
    return 0;
}

/* Enforce the prefix policy on the RESOLVED path, not just the client
 * string: realpath() canonicalizes every symlinked component, so a link
 * planted inside an allowed root that points at /private/etc/ resolves
 * outside the allowlist and is rejected here. For a not-yet-existing
 * target (PUSH of a new file) the parent directory is resolved and the
 * final component re-attached before the check. On success out[] holds
 * the canonical path every later open/rename/stat must use. Residual
 * TOCTOU between this check and the open is accepted: the allowlist
 * roots are daemon-owned, and RUN exists anyway; the strict fix is an
 * openat(O_NOFOLLOW) component walk. */
static int path_resolved_allowed(const char *client_path, int test_mode, int exec_only,
                                 char *out, size_t out_sz) {
    char resolved[PATH_MAX];

    if (!path_allowed(client_path, test_mode, exec_only))
        return 0; /* caller logs the rejection with the client string */

    if (realpath(client_path, resolved) == NULL) {
        if (errno == ENOENT) {
            /* New file: resolve the parent, re-attach the base name. */
            char dir[PATH_MAX], rdir[PATH_MAX];
            const char *slash = strrchr(client_path, '/');
            const char *base;
            if (slash == NULL || slash == client_path)
                return 0; /* "/x": parent is "/", never inside an allowlist root */
            base = slash + 1;
            if ((size_t)(slash - client_path) >= sizeof(dir))
                return 0;
            memcpy(dir, client_path, (size_t)(slash - client_path));
            dir[slash - client_path] = '\0';
            if (realpath(dir, rdir) == NULL) {
                devd_log("path: cannot resolve parent %s (errno=%d)", dir, errno);
                return 0;
            }
            if (snprintf(resolved, sizeof(resolved), "%s/%s", rdir, base)
                    >= (int)sizeof(resolved)) {
                devd_log("path: resolved path too long: %s/%s", rdir, base);
                return 0;
            }
        } else {
            /* ELOOP (symlink cycle), ENOTDIR, EACCES, ...: reject. */
            devd_log("path: cannot resolve %s (errno=%d)", client_path, errno);
            return 0;
        }
    }

    if (!path_allowed(resolved, test_mode, exec_only)) {
        devd_log("path: symlink escape rejected: %s -> %s", client_path, resolved);
        return 0;
    }
    if (strlen(resolved) >= out_sz) {
        devd_log("path: resolved path exceeds buffer: %s", resolved);
        return 0;
    }
    strcpy(out, resolved);
    return 1;
}

/* ---- PUSH path rules, equal to the kernel overlay's sd_path_check() (0117; review devd-r1 F4) ---- */

static int has_suffix(const char *s, size_t n, const char *suf) {
    size_t m = strlen(suf);
    return n >= m && memcmp(s + n - m, suf, m) == 0;
}

/* NULL = ok, else the reason. Applied to the client's path after path_allowed(): reserved suffixes (the
 * restore source and the temp file belong to devd and the kernel overlay), empty or '.' components, and
 * components over 245 bytes (name.sweetorig must stay within NAME_MAX 255). */
static const char *push_name_check(const char *path) {
    size_t n = strlen(path), start = 1, i;
    if (has_suffix(path, n, ".sweettmp") || has_suffix(path, n, ".sweetorig"))
        return "reserved suffix";
    for (i = 1; i <= n; i++) {
        if (i == n || path[i] == '/') {
            size_t len = i - start;
            if (len == 0) return "empty component";
            if (len == 1 && path[start] == '.') return "'.' component";
            if (len > 245) return "component longer than 245 bytes";
            start = i + 1;
        }
    }
    return NULL;
}

/* NULL = ok, else the reason: no component of the client's path may be a symlink (the kernel overlay looks
 * every existing component up with NOFOLLOW). A component that does not exist ends the walk: the rest is new. */
static const char *push_symlink_walk(const char *path) {
    char buf[PATH_MAX];
    size_t n = strlen(path), i;
    struct stat st;
    if (n >= sizeof(buf)) return "path too long";
    memcpy(buf, path, n + 1);
    for (i = 1; i <= n; i++) {
        if (i == n || buf[i] == '/') {
            char saved = buf[i];
            buf[i] = '\0';
            if (lstat(buf, &st) != 0) {
                if (errno == ENOENT) return NULL;
                return "cannot stat a path component";
            }
            if (S_ISLNK(st.st_mode)) return "symlink component";
            if (i != n && !S_ISDIR(st.st_mode)) return "component is not a directory";
            buf[i] = saved;
        }
    }
    return NULL;
}

/* Mode of a NEW pushed file, set with fchmod() (not umask dependent): 0755 under the RUN roots (/usr/local/,
 * /private/var/dev/, and /tmp/ in test mode), 0644 everywhere else (plists, libraries, the launchd plist).
 * A replaced regular file keeps its own permission bits instead (review devd-r2 N1, see the PUSH handler).
 * Test mode only: a path under /tmp/ with a /devd-lib/ component stands in for a non-RUN root such as
 * /usr/lib/ (0644), so the host suite can test the replace rule outside the RUN roots. */
static mode_t push_mode(const char *rpath, int test_mode) {
    size_t i;
    for (i = 0; i < sizeof(k_run_prefixes) / sizeof(k_run_prefixes[0]); i++)
        if (strncmp(rpath, k_run_prefixes[i], strlen(k_run_prefixes[i])) == 0)
            return 0755;
    if (test_mode && (strncmp(rpath, k_test_prefix, sizeof(k_test_prefix) - 1) == 0 ||
                      strncmp(rpath, "/private/tmp/", 13) == 0))
        return strstr(rpath, "/devd-lib/") != NULL ? 0644 : 0755;
    return 0644;
}

/* Mode for the temp file of a PUSH (review devd-r2 N1): replacing a regular file keeps its permission bits,
 * masked to 0777 (the kernel overlay REFUSES an entry mode above 0777, 0117:464, and creates with
 * va_mode & ACCESSPERMS; devd cannot refuse a mode it did not choose, so it drops the bits); a dropped
 * setuid/setgid/sticky bit is logged. A new file gets the class default of push_mode(). */
static mode_t push_target_mode(const char *rpath, int test_mode, int replace_regular, mode_t old_mode) {
    if (!replace_regular)
        return push_mode(rpath, test_mode);
    if (old_mode & 07000)
        fprintf(stderr, "SWEET GAP: devd PUSH %s: replaced file had mode %04o; setuid/setgid/sticky dropped (kernel cap 0777)\n",
                rpath, (unsigned)(old_mode & 07777));
    return old_mode & 0777;
}

/* Creates the PUSH temp file (review devd-r1 F6): a stale regular file (a crashed push, or a hard link
 * planted at the name) is unlinked first, a non-regular one (symlink, directory, fifo) is refused, and the
 * file is created with O_EXCL|O_NOFOLLOW, so the open can never truncate through a link or follow one.
 * -1 with errno set on failure. */
static int open_push_tmp(const char *tmp_path) {
    struct stat ts;
    if (lstat(tmp_path, &ts) == 0) {
        if (!S_ISREG(ts.st_mode)) {
            errno = EEXIST;
            return -1;
        }
        if (unlink(tmp_path) != 0)
            return -1;
    } else if (errno != ENOENT) {
        return -1;
    }
    return cloexec_fd(open(tmp_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600));
}

enum line_result {
    LINE_OK,
    LINE_OVERFLOW,
    LINE_CORRUPT,
    LINE_EOF,
    LINE_ERR
};

/* Consumes bytes until '\n'. If cap exceeded or binary detected, drains
 * until '\n'. One total deadline covers the whole line: a peer trickling
 * a byte per timeout window would otherwise hold the connection (and this
 * serial daemon) open indefinitely. */
static enum line_result read_line_to(int fd, char *out, size_t cap, uint64_t timeout_ms) {
    size_t len = 0;
    int overflow = 0;
    int corrupt = 0;
    uint64_t deadline = now_ms() + timeout_ms;
    while (1) {
        char c;
        ssize_t n = recv(fd, &c, 1, MSG_DONTWAIT);
        if (n == 0) return LINE_EOF;
        if (n < 0) {
            struct pollfd pf;
            int64_t remain;
            int pr;
            if (errno == EINTR) continue;
            if (errno != EAGAIN && errno != EWOULDBLOCK) return LINE_ERR;
            remain = (int64_t)(deadline - now_ms());
            if (remain <= 0) return LINE_ERR;
            pf.fd = fd;
            pf.events = POLLIN;
            pf.revents = 0;
            pr = poll(&pf, 1, (int)remain);
            if (pr < 0) {
                if (errno == EINTR) continue;
                return LINE_ERR;
            }
            if (pr == 0) return LINE_ERR;
            continue;
        }
        if (c == '\n') {
            if (overflow) return LINE_OVERFLOW;
            if (corrupt) return LINE_CORRUPT;
            out[len] = '\0';
            return LINE_OK;
        }
        if (c == '\r') continue;
        if ((unsigned char)c < 0x20 || (unsigned char)c >= 0x7F) {
            corrupt = 1;
        }
        if (len < cap - 1) {
            out[len++] = c;
        } else {
            overflow = 1;
        }
    }
}

static enum line_result read_line(int fd, char *out, size_t cap) {
    return read_line_to(fd, out, cap, (uint64_t)DEVD_IO_TIMEOUT_SEC * 1000u);
}

/* Challenge-response (H10). 0 = authenticated; -1 = rejected/dropped (logged, ERR sent
 * where the peer is still listening). No command is parsed before this returns 0. */
static int auth_client(int fd, const uint8_t key[32]) {
    uint8_t chal[16], want[32];
    char chal_hex[33], want_hex[65], msg[64], line[128];
    enum line_result lr;
    if (random_bytes(chal, sizeof(chal)) != 0) {
        devd_gap("AUTH", "ERR: no entropy for the challenge");
        send_str(fd, "ERR: auth\n");
        return -1;
    }
    hex_encode(chal, sizeof(chal), chal_hex);
    snprintf(msg, sizeof(msg), "CHALLENGE %s\n", chal_hex);
    if (send_all(fd, msg, strlen(msg)) < 0) {
        devd_gap("AUTH", "ERR: challenge not delivered");
        return -1;
    }
    lr = read_line_to(fd, line, sizeof(line), DEVD_AUTH_TIMEOUT_MS);
    if (lr == LINE_EOF) {
        devd_gap("AUTH", "ERR: peer closed before AUTH");
        return -1;
    }
    if (lr == LINE_ERR) {
        devd_gap("AUTH", "ERR: no AUTH line within the timeout");
        send_str(fd, "ERR: auth\n");
        return -1;
    }
    if (lr != LINE_OK || strncmp(line, "AUTH ", 5) != 0 || strlen(line) != 5 + 64) {
        devd_gap("AUTH", "ERR: first line is not AUTH <64 hex>");
        send_str(fd, "ERR: auth\n");
        return -1;
    }
    hmac_sha256(key, 32, chal, sizeof(chal), want);
    hex_encode(want, sizeof(want), want_hex);
    if (!ct_equal(want_hex, line + 5, 64)) {
        devd_gap("AUTH", "ERR: HMAC mismatch");
        send_str(fd, "ERR: auth\n");
        return -1;
    }
    return 0;
}

static int is_allowed_signal(int signo) {
    switch (signo) {
        case SIGHUP:
        case SIGINT:
        case SIGKILL:
        case SIGUSR1:
        case SIGUSR2:
        case SIGTERM:
            return 1;
        default:
            return 0;
    }
}

/* Ends a RUN answer (review devd-r1 F3): a line break first when the program's last byte was not one (so
 * the end line is a line of its own), then the optional ERR text, then `RUNEND <id> RC: <rc>`. */
static int send_run_end(int fd, int last_nl, const char *err_text, const char *run_id, int rc) {
    char b[192];
    int n = 0;
    if (!last_nl)
        b[n++] = '\n';
    if (err_text != NULL)
        n += snprintf(b + n, sizeof(b) - (size_t)n, "%s", err_text);
    n += snprintf(b + n, sizeof(b) - (size_t)n, "RUNEND %s RC: %d\n", run_id, rc);
    return send_all(fd, b, (size_t)n);
}

/* Execute command with bounded timeout via pipe and poll. Answer framing (review devd-r1 F3): `RUNID <16 hex>`
 * first, then the program's output, then `RUNEND <same id> RC: <rc>`; a program cannot forge the end line
 * because it never sees the id. A request refused before the fork gets one ERR line and no RUNID.
 * Returns the command's rc (>= 0), or -1 strictly when the client connection is dead
 * (caller must drop it without further sends). cmd is the resolved program path. */
static int exec_command_bounded(int client_fd, const char *cmd, char *const argv[]) {
    int pipe_fd[2];
    uint8_t id_raw[8];
    char run_id[17];
    char id_line[32];
    int last_nl = 1; /* 1 until a program byte other than '\n' was the last one sent */
    if (random_bytes(id_raw, sizeof(id_raw)) != 0) {
        devd_log("RUN: no entropy for the run id");
        reject(client_fd, "RUN", "ERR: no entropy for the run id\n");
        return 127;
    }
    hex_encode(id_raw, sizeof(id_raw), run_id);
    if (pipe(pipe_fd) < 0) {
        devd_log("RUN: pipe failed (errno=%d)", errno);
        reject(client_fd, "RUN", "ERR: pipe failed\n");
        return 127;
    }
    if (set_cloexec(pipe_fd[0]) < 0 || set_cloexec(pipe_fd[1]) < 0) { /* both ends closed here: no leak (review devd-r1 nit) */
        devd_log("RUN: pipe close-on-exec failed (errno=%d)", errno);
        close(pipe_fd[0]);
        close(pipe_fd[1]);
        reject(client_fd, "RUN", "ERR: pipe failed\n");
        return 127;
    }

    pid_t pid = fork();
    if (pid < 0) {
        devd_log("RUN: fork failed (errno=%d)", errno);
        close(pipe_fd[0]);
        close(pipe_fd[1]);
        reject(client_fd, "RUN", "ERR: fork failed\n");
        return 127;
    }

    if (pid == 0) {
        /* Child: own process group so a timeout kill reaches grandchildren,
         * stdout+stderr into the pipe, then exec. */
        setpgid(0, 0);
        close(pipe_fd[0]);
        dup2(pipe_fd[1], STDOUT_FILENO);
        dup2(pipe_fd[1], STDERR_FILENO);
        close(pipe_fd[1]);

        signal(SIGPIPE, SIG_DFL); /* devd ignores SIGPIPE; the program must not inherit that (review devd-r1 F13) */
        execve(cmd, argv, k_run_env); /* cmd = realpath()-checked program, argv from a space split: no shell */
        _exit(127);
    }

    /* Parent process. Duplicate setpgid for the exec race: if the child
     * already exec'd, EACCES is harmless - it set its own group first. */
    setpgid(pid, pid);
    close(pipe_fd[1]);

    char buf[BUFFER_SIZE];
    struct pollfd pfd;
    pfd.fd = pipe_fd[0];
    pfd.events = POLLIN;

    int timed_out = 0;
    int client_gone = 0;
    uint64_t deadline = now_ms() + (uint64_t)DEVD_RUN_TIMEOUT_SEC * 1000u;

    snprintf(id_line, sizeof(id_line), "RUNID %s\n", run_id);
    if (send_all(client_fd, id_line, strlen(id_line)) < 0)
        client_gone = 1;

    while (!client_gone) {
        int64_t remain_ms = (int64_t)(deadline - now_ms());
        if (remain_ms <= 0) {
            timed_out = 1;
            break;
        }

        int ret = poll(&pfd, 1, (int)remain_ms);
        if (ret < 0) {
            if (errno == EINTR) continue;
            devd_log("RUN: poll failed (errno=%d)", errno);
            break;
        }
        if (ret == 0) {
            timed_out = 1;
            break;
        }

        if (pfd.revents & POLLIN) {
            ssize_t n = read(pipe_fd[0], buf, sizeof(buf));
            if (n <= 0) break; /* EOF: child and heirs released the pipe */
            if (send_all(client_fd, buf, (size_t)n) < 0) {
                client_gone = 1; /* peer stopped reading: stop feeding it */
                break;
            }
            last_nl = buf[n - 1] == '\n';
        }
        if (pfd.revents & (POLLHUP | POLLERR)) {
            /* Drain any remaining bytes */
            ssize_t n;
            while ((n = read(pipe_fd[0], buf, sizeof(buf))) > 0) {
                if (send_all(client_fd, buf, (size_t)n) < 0) {
                    client_gone = 1;
                    break;
                }
                last_nl = buf[n - 1] == '\n';
            }
            break;
        }
    }

    close(pipe_fd[0]);

    int status = 0;

    if (client_gone) {
        /* Peer is deaf/stalled: kill the tree, reap, hand the connection
         * back for dropping - any further send would just burn another
         * timeout window. */
        kill(-pid, SIGKILL);
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
        return -1;
    }

    /* Reap. If timed out, kill the whole process group (including any orphaned
     * grandchildren left by an exited shell) and exit immediately. */
    if (timed_out) {
        kill(-pid, SIGKILL);
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
        devd_log("RUN: TIMED OUT after %ds; killed pgrp %d (%s)",
                 DEVD_RUN_TIMEOUT_SEC, pid, cmd);
        devd_gap("RUN", "ERR: execution timed out");
        if (send_run_end(client_fd, last_nl, "ERR: execution timed out\n", run_id, 124) < 0)
            return -1;
        return 124;
    }

    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}

    /* Review devd-r1 F13: a grandchild that dropped the pipe would outlive the RUN as root. The leader is
     * reaped, so the group exists only while a member lives (its id is not reused meanwhile): kill() == 0
     * means survivors, which are killed and reported. A daemon started by RUN must setsid() to stay alive. */
    if (kill(-pid, SIGKILL) == 0)
        fprintf(stderr, "SWEET GAP: devd RUN left background processes in pgrp %d after %s exited; killed them\n",
                (int)pid, cmd);

    int rc = WIFEXITED(status) ? WEXITSTATUS(status)
            : WIFSIGNALED(status) ? 128 + WTERMSIG(status) : 1;

    if (send_run_end(client_fd, last_nl, NULL, run_id, rc) < 0)
        return -1;
    devd_log("RUN: rc=%d (%s)", rc, cmd);
    return rc;
}


static void handle_client(int client_fd, int test_mode, const uint8_t key[32]) {
    char buf[BUFFER_SIZE];
    tune_client_socket(client_fd);

    if (auth_client(client_fd, key) != 0)
        return;

    while (1) {
        char cmd[1024];
        enum line_result lr = read_line(client_fd, cmd, sizeof(cmd));
        if (lr == LINE_EOF || lr == LINE_ERR) {
            return;
        }
        if (lr == LINE_OVERFLOW) {
            devd_log("command line overflow (drained)");
            reject(client_fd, "?", "ERR: command too long\n");
            continue;
        }
        if (lr == LINE_CORRUPT) {
            devd_log("binary/corrupt command line rejected");
            reject(client_fd, "?", "ERR: unknown command\n");
            continue;
        }
        if (strlen(cmd) == 0) continue;

        if (strcmp(cmd, "PING") == 0) {
            char resp[64];
            devd_log("PING");
            snprintf(resp, sizeof(resp), "PONG %s\n", DEVD_VERSION);
            send_str(client_fd, resp);
        }
        else if (strncmp(cmd, "PUSH ", 5) == 0) {
            char path[512];
            char trailer[2];
            size_t total_len = 0;
            const char *push_err = NULL;
            const char *name_err = NULL;
            int cut = 0;  /* payload not attributable: drop after the ERR */
            int skip = 0; /* payload attributable: skip total_len bytes */
            char rpath[PATH_MAX];

            if (sscanf(cmd + 5, "%511s %zu%1s", path, &total_len, trailer) != 2) {
                devd_log("PUSH: invalid format: %.128s", cmd);
                push_err = "ERR: invalid PUSH format\n";
                cut = 1;
            } else if (total_len == 0) {
                /* review devd-r1 F1: a client that declared 0 but streams the file anyway (a file
                 * that grew, /proc) must not have those bytes parsed as commands: drop it. */
                devd_log("PUSH: rejected length 0");
                push_err = "ERR: push length invalid (must be 1..41943040)\n";
                cut = 1;
            } else if (total_len > MAX_PUSH_BYTES) {
                devd_log("PUSH: rejected length %zu (cap: %u)", total_len, MAX_PUSH_BYTES);
                push_err = "ERR: push length invalid (must be 1..41943040)\n";
                cut = 1;
            } else if (!path_resolved_allowed(path, test_mode, 0, rpath, sizeof(rpath))) {
                devd_log("PUSH: path rejected by policy: %s", path);
                push_err = "ERR: path not allowed\n";
                skip = 1;
            } else if (is_key_file(rpath)) {
                devd_log("PUSH: refused, %s is the devd key file", rpath);
                push_err = "ERR: path not allowed\n";
                skip = 1;
            } else if ((name_err = push_name_check(path)) != NULL ||
                       (name_err = push_symlink_walk(path)) != NULL) {
                devd_log("PUSH: path rejected (%s): %s", name_err, path);
                push_err = "ERR: path not allowed\n";
                skip = 1;
            }
            if (push_err != NULL) {
                reject(client_fd, "PUSH", push_err);
                if (cut) {
                    devd_log("PUSH: payload of the rejected header is not attributable; "
                             "closing after ERR");
                    return;
                }
                if (skip && skip_payload(client_fd, total_len) < 0) {
                    devd_log("PUSH: rejected payload could not be skipped; closing");
                    return;
                }
                continue;
            }

            devd_log("PUSH: begin path=%s len=%zu", rpath, total_len);

            char tmp_path[PATH_MAX + 16];
            char orig_path[PATH_MAX + 16];
            if (snprintf(tmp_path, sizeof(tmp_path), "%s.sweettmp", rpath)
                    >= (int)sizeof(tmp_path) ||
                snprintf(orig_path, sizeof(orig_path), "%s.sweetorig", rpath)
                    >= (int)sizeof(orig_path)) {
                devd_log("PUSH: resolved path too long: %s", rpath);
                reject(client_fd, "PUSH", "ERR: path too long\n");
                return; /* payload unread: drop the connection */
            }
            /* Replacing a regular file keeps the first original as <path>.sweetorig (0117:527-529). A backup
             * name that exists and is not a regular file blocks the push: the restore could not find the original. */
            struct stat tst, ost;
            int replace_regular = lstat(rpath, &tst) == 0 && S_ISREG(tst.st_mode);
            int orig_exists = lstat(orig_path, &ost) == 0;
            if (replace_regular && ((orig_exists && !S_ISREG(ost.st_mode)) || (!orig_exists && errno != ENOENT))) {
                devd_log("PUSH: .sweetorig of %s is not usable (exists=%d)", rpath, orig_exists);
                reject(client_fd, "PUSH", "ERR: .sweetorig is not a regular file\n");
                if (skip_payload(client_fd, total_len) < 0)
                    return;
                continue;
            }

            /* The temp must be a regular file WE create (open_push_tmp: F6). */
            mode_t pmode = push_target_mode(rpath, test_mode, replace_regular, replace_regular ? tst.st_mode : 0);
            int fd = open_push_tmp(tmp_path);
            if (fd >= 0 && fchmod(fd, pmode) != 0) {
                int fe = errno;
                close(fd);
                unlink(tmp_path);
                errno = fe;
                fd = -1;
            }
            /* 2026-11-09 (patch 0280 paths): a replaced file keeps its OWNER too (the mobile-owned
             * MobileBluetooth.debug.plist must not turn into root's after a push). */
            if (fd >= 0 && replace_regular && geteuid() == 0 && (tst.st_uid != 0 || tst.st_gid != getegid()) &&   /* review 0280 F5: root only */
                fchown(fd, tst.st_uid, tst.st_gid) != 0) {
                int fe = errno;
                devd_log("PUSH: cannot keep the owner %d:%d of %s (errno=%d)", (int)tst.st_uid, (int)tst.st_gid, rpath, fe);
                close(fd);
                unlink(tmp_path);
                errno = fe;
                fd = -1;
            }
            if (fd < 0) {
                devd_log("PUSH: cannot create temp file %s (errno=%d)", tmp_path, errno);
                reject(client_fd, "PUSH", "ERR: cannot create temp file\n");
                return; /* payload unread: drop the connection */
            }

            size_t received = 0;
            int write_err = 0;
            uint64_t xfer_deadline = now_ms() + DEVD_PUSH_DEADLINE_MS;
            while (received < total_len) {
                size_t to_read = total_len - received;
                if (to_read > sizeof(buf)) to_read = sizeof(buf);
                ssize_t n = recv(client_fd, buf, to_read, 0);
                if (n <= 0) { write_err = 1; break; }
                if (write(fd, buf, (size_t)n) != n) { write_err = 1; break; }
                received += (size_t)n;
                if ((int64_t)(xfer_deadline - now_ms()) <= 0) {
                    devd_log("PUSH: transfer exceeded %ums total deadline",
                             DEVD_PUSH_DEADLINE_MS);
                    write_err = 1;
                    break;
                }
            }

            if (!write_err && received == total_len) {
                (void)fsync(fd);
            }
            close(fd);

            if (write_err || received != total_len) {
                unlink(tmp_path);
                devd_log("PUSH: transfer failed (write_err=%d received=%zu want=%zu)",
                         write_err, received, total_len);
                reject(client_fd, "PUSH", "ERR: transfer failed\n");
                return; /* payload state unknown: drop the connection */
            }
            int moved = 0;
            if (replace_regular && !orig_exists) { /* first replacement: keep the original */
                if (rename(rpath, orig_path) != 0) {
                    devd_log("PUSH: cannot keep %s (errno=%d)", orig_path, errno);
                    unlink(tmp_path);
                    reject(client_fd, "PUSH", "ERR: cannot keep .sweetorig\n");
                    return;
                }
                moved = 1;
            }
            if (rename(tmp_path, rpath) != 0) {
                int re = errno;
                unlink(tmp_path); /* no residue after a failed rename */
                if (moved && rename(orig_path, rpath) != 0)
                    fprintf(stderr, "SWEET GAP: devd PUSH rollback failed: ORIGINAL ONLY AT %s\n", orig_path);
                devd_log("PUSH: rename failed (errno=%d): %s", re, rpath);
                reject(client_fd, "PUSH", "ERR: rename failed\n");
                return; /* the peer may still be streaming: drop the connection */
            } else {
                devd_log("PUSH: done path=%s len=%zu mode=%04o (%s)", rpath, total_len, (unsigned)pmode,
                         replace_regular ? "kept" : "new");
                send_str(client_fd, "OK\n");
            }
        }
        /* PULL <path> */
        else if (strncmp(cmd, "PULL ", 5) == 0) {
            char path[512];
            char trailer[2];
            if (sscanf(cmd + 5, "%511s%1s", path, trailer) != 1) {
                devd_log("PULL: invalid format");
                reject(client_fd, "PULL", "ERR: invalid PULL format\n");
                continue;
            }
            char rpath[PATH_MAX];
            /* client may name /var/tmp/...: the k_pull_only[] entries are /private/var/... (Darwin /var -> /private/var) */
            char cpath[520];
            if (strncmp(path, "/var/tmp/", 9) == 0)
                snprintf(cpath, sizeof(cpath), "/private%s", path);
            else
                snprintf(cpath, sizeof(cpath), "%s", path);
            if (!path_resolved_allowed(cpath, test_mode, 2, rpath, sizeof(rpath))) {
                devd_log("PULL: path rejected by policy: %s", path);
                reject(client_fd, "PULL", "ERR: path not allowed\n");
                continue;
            }
            if (is_key_file(rpath)) {
                devd_log("PULL: refused, %s is the devd key file", rpath);
                reject(client_fd, "PULL", "ERR: path not allowed\n");
                continue;
            }

            struct stat st;
            if (stat(rpath, &st) < 0) {
                devd_log("PULL: file not found: %s", rpath);
                reject(client_fd, "PULL", "ERR: file not found\n");
                continue;
            }
            if (!S_ISREG(st.st_mode)) {
                devd_log("PULL: rejected non-regular file: %s", rpath);
                reject(client_fd, "PULL", "ERR: not a regular file\n");
                continue;
            }
            if (st.st_size > (off_t)MAX_PUSH_BYTES) {
                devd_log("PULL: file exceeds %u bytes: %s", MAX_PUSH_BYTES, rpath);
                reject(client_fd, "PULL", "ERR: file too large\n");
                continue;
            }

            int fd = cloexec_fd(open(rpath, O_RDONLY));
            if (fd < 0) {
                devd_log("PULL: cannot open %s (errno=%d)", rpath, errno);
                reject(client_fd, "PULL", "ERR: cannot open file\n");
                continue;
            }

            devd_log("PULL: begin path=%s len=%lld", rpath, (long long)st.st_size);
            char hdr[64];
            snprintf(hdr, sizeof(hdr), "OK %zu\n", (size_t)st.st_size);
            send_str(client_fd, hdr);

            size_t total_sent = 0;
            ssize_t n;
            int client_stalled = 0;
            while ((n = read(fd, buf, sizeof(buf))) > 0) {
                if (send_all(client_fd, buf, (size_t)n) < 0) {
                    client_stalled = 1;
                    break;
                }
                total_sent += (size_t)n;
            }
            close(fd);
            devd_log("PULL: done path=%s sent=%zu", rpath, total_sent);
            if (client_stalled)
                return; /* peer stopped reading: drop the connection */
        }
        /* RUN /abs/program [arg ...]: no shell (H6) */
        else if (strncmp(cmd, "RUN ", 4) == 0) {
            char *argv[DEVD_RUN_MAX_ARGS + 2];
            char line[DEVD_CMD_CAP];
            char rpath[PATH_MAX];
            struct stat st;
            const char *err = NULL;
            int argc_run = 0;
            char *p;

            strncpy(line, cmd + 4, sizeof(line) - 1);
            line[sizeof(line) - 1] = '\0';
            /* split on single spaces; an empty token (leading/double/trailing space) is refused */
            for (p = line; ; ) {
                char *sp = strchr(p, ' ');
                if (sp != NULL) *sp = '\0';
                if (*p == '\0') { err = "ERR: RUN empty argument\n"; break; }
                if (argc_run >= DEVD_RUN_MAX_ARGS + 1) { err = "ERR: RUN too many arguments\n"; break; }
                argv[argc_run++] = p;
                if (sp == NULL) break;
                p = sp + 1;
            }
            if (err == NULL && !path_resolved_allowed(argv[0], test_mode, 1, rpath, sizeof(rpath)))
                err = "ERR: RUN program not allowed\n";
            if (err == NULL && (stat(rpath, &st) != 0 || !S_ISREG(st.st_mode) || (st.st_mode & 0111) == 0))
                err = "ERR: RUN program is not an executable regular file\n";
            if (err != NULL) {
                devd_log("RUN: rejected: %.128s", cmd + 4);
                reject(client_fd, "RUN", err);
                continue;
            }
            argv[0] = rpath;
            argv[argc_run] = NULL;
            devd_log("RUN: %s (%d args)", rpath, argc_run - 1);
            if (exec_command_bounded(client_fd, rpath, argv) < 0)
                return; /* peer is gone/stalled: drop the connection */
        }
        /* SIGNAL <pid> <signo> */
        else if (strncmp(cmd, "SIGNAL ", 7) == 0) {
            pid_t target_pid;
            int signo;
            if (sscanf(cmd + 7, "%d %d", &target_pid, &signo) != 2) {
                devd_log("SIGNAL: invalid format");
                reject(client_fd, "SIGNAL", "ERR: invalid SIGNAL format\n");
                continue;
            }
            if (target_pid <= 1 || target_pid == getpid()) {
                devd_log("SIGNAL: rejected pid=%d (protected target)", target_pid);
                reject(client_fd, "SIGNAL", "ERR: pid rejected\n");
                continue;
            }
            if (!is_allowed_signal(signo)) {
                devd_log("SIGNAL: rejected signo=%d (not in allowlist)", signo);
                reject(client_fd, "SIGNAL", "ERR: signal not allowed\n");
                continue;
            }
            devd_log("SIGNAL: pid=%d signo=%d", target_pid, signo);
            if (kill(target_pid, signo) == 0) {
                send_str(client_fd, "OK\n");
            } else {
                devd_log("SIGNAL: kill failed (errno=%d)", errno);
                reject(client_fd, "SIGNAL", "ERR: kill failed\n");
            }
        } else {
            devd_log("unknown command: %.64s", cmd);
            char verb[16];
            size_t vn = 0;
            while (cmd[vn] != '\0' && cmd[vn] != ' ' && vn < sizeof(verb) - 1) {
                verb[vn] = cmd[vn];
                vn++;
            }
            verb[vn] = '\0';
            reject(client_fd, verb, "ERR: unknown command\n");
        }
    }
}


/* `devd --selftest`: FIPS 180-4 / RFC 4231 vectors for the self-contained crypto, the key-file
 * stat/text checks with injected stat results, and iface_has_addr(). Exit 0 all passed, 1 not. */
static int st_pass, st_fail;

static void st_check(const char *name, int ok) {
    if (ok) {
        st_pass++;
    } else {
        st_fail++;
        printf("[-] selftest %s: FAIL\n", name);
    }
}

static int st_sha(const char *name, const void *msg, size_t n, const char *want_hex) {
    struct sha256 c;
    uint8_t d[32];
    char hex[65];
    sha256_init(&c);
    sha256_update(&c, msg, n);
    sha256_final(&c, d);
    hex_encode(d, 32, hex);
    st_check(name, strcmp(hex, want_hex) == 0);
    return 0;
}

static void st_hmac(const char *name, const uint8_t *key, size_t kl, const char *msg, const char *want_hex) {
    uint8_t d[32];
    char hex[65];
    hmac_sha256(key, kl, (const uint8_t *)msg, strlen(msg), d);
    hex_encode(d, 32, hex);
    st_check(name, strcmp(hex, want_hex) == 0);
}

static int selftest(void) {
    static const char k448[] = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    uint8_t key1[20], key6[131], k32[32];
    struct sha256 c;
    uint8_t d[32];
    char hex[65], *mil;
    struct stat stt;
    size_t i;
    in_addr_t lo = inet_addr("127.0.0.1");
    struct ifaddrs *list = NULL, *it;
    char loname[64] = "";

    st_sha("sha256 empty", "", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    st_sha("sha256 abc", "abc", 3, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    st_sha("sha256 448-bit", k448, strlen(k448), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    sha256_init(&c);
    mil = malloc(1000);
    if (mil == NULL) return 1;
    memset(mil, 'a', 1000);
    for (i = 0; i < 1000; i++) sha256_update(&c, mil, 1000);
    free(mil);
    sha256_final(&c, d);
    hex_encode(d, 32, hex);
    st_check("sha256 one million 'a'", strcmp(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0") == 0);

    memset(key1, 0x0b, sizeof(key1));
    st_hmac("hmac RFC 4231 case 1", key1, sizeof(key1), "Hi There",
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
    st_hmac("hmac RFC 4231 case 2", (const uint8_t *)"Jefe", 4, "what do ya want for nothing?",
            "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
    memset(key6, 0xaa, sizeof(key6));
    st_hmac("hmac RFC 4231 case 6 (key longer than a block)", key6, sizeof(key6),
            "Test Using Larger Than Block-Size Key - Hash Key First",
            "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");

    st_check("hex_decode accepts lowercase", hex_decode("00ff10", 3, d) && d[0] == 0 && d[1] == 0xff && d[2] == 0x10);
    st_check("hex_decode rejects uppercase", !hex_decode("00FF10", 3, d));
    st_check("hex_decode rejects a non-hex byte", !hex_decode("00fg10", 3, d));
    st_check("ct_equal equal", ct_equal("abcd", "abcd", 4));
    st_check("ct_equal differs in the last byte", !ct_equal("abcd", "abce", 4));

    memset(&stt, 0, sizeof(stt));
    stt.st_mode = S_IFREG | 0600; stt.st_uid = 0; stt.st_size = 64;
    st_check("key stat: root 0600 regular 64 bytes ok", key_stat_check(&stt, 0) == NULL);
    stt.st_size = 65;
    st_check("key stat: size 65 (with newline) ok", key_stat_check(&stt, 0) == NULL);
    stt.st_size = 10;
    st_check("key stat: short file rejected", key_stat_check(&stt, 0) != NULL);
    stt.st_size = 64; stt.st_uid = 1000;
    st_check("key stat: wrong owner rejected", key_stat_check(&stt, 0) != NULL);
    stt.st_uid = 0; stt.st_mode = S_IFREG | 0640;
    st_check("key stat: group-readable rejected", key_stat_check(&stt, 0) != NULL);
    stt.st_mode = S_IFREG | 04600;
    st_check("key stat: setuid bit rejected", key_stat_check(&stt, 0) != NULL);
    stt.st_mode = S_IFLNK | 0600;
    st_check("key stat: symlink rejected", key_stat_check(&stt, 0) != NULL);
    stt.st_mode = S_IFDIR | 0600;
    st_check("key stat: directory rejected", key_stat_check(&stt, 0) != NULL);

    st_check("key text: 64 hex ok", key_text_parse("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff", 64, k32) == NULL);
    st_check("key text: 64 hex + newline ok", key_text_parse("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff\n", 65, k32) == NULL);
    st_check("key text: uppercase rejected", key_text_parse("00112233445566778899AABBCCDDEEFF00112233445566778899aabbccddeeff", 64, k32) != NULL);
    st_check("key text: 63 chars rejected", key_text_parse("00112233445566778899aabbccddeeff00112233445566778899aabbccddeef", 63, k32) != NULL);
    st_check("key text: 65 chars without newline rejected", key_text_parse("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff0", 65, k32) != NULL);

    st_check("path /usr/lib/x allowed", path_allowed("/usr/lib/x", 0, 0));
    st_check("path /usr/lib/ itself refused (kernel: n == pl)", !path_allowed("/usr/lib/", 0, 0));
    st_check("path /usr/local/bin/x allowed", path_allowed("/usr/local/bin/x", 0, 0));
    st_check("path /private/var/dev/x allowed", path_allowed("/private/var/dev/x", 0, 0));
    st_check("path exact launchd.plist allowed", path_allowed("/System/Library/xpc/launchd.plist", 0, 0));
    st_check("path launchd.plist + suffix refused", !path_allowed("/System/Library/xpc/launchd.plist.x", 0, 0));
    st_check("path org.pixelios.devd.plist allowed", path_allowed("/System/Library/LaunchDaemons/org.pixelios.devd.plist", 0, 0));
    st_check("path org.pixelios.<name>/sub refused", !path_allowed("/System/Library/LaunchDaemons/org.pixelios.a/b", 0, 0));
    st_check("path other LaunchDaemons name refused", !path_allowed("/System/Library/LaunchDaemons/com.apple.x.plist", 0, 0));
    st_check("path /tmp/x refused outside test mode", !path_allowed("/tmp/x", 0, 0));
    st_check("path /tmp/x allowed in test mode", path_allowed("/tmp/x", 1, 0));
    st_check("path traversal refused", !path_allowed("/usr/local/../etc/x", 0, 0));
    st_check("run /usr/local/bin/x allowed", path_allowed("/usr/local/bin/x", 0, 1));
    st_check("run /private/var/dev/x allowed", path_allowed("/private/var/dev/x", 0, 1));
    st_check("run /usr/lib/x refused (PUSH root, not a RUN root)", !path_allowed("/usr/lib/x", 0, 1));
    st_check("run /bin/sh refused", !path_allowed("/bin/sh", 0, 1));
    st_check("run /private/var/x refused (only /private/var/dev/ is a RUN root)", !path_allowed("/private/var/x", 0, 1));
    st_check("run /private/var/devx refused (prefix needs the slash)", !path_allowed("/private/var/devx", 0, 1));
    st_check("run /tmp/x refused outside test mode", !path_allowed("/tmp/x", 0, 1));
    st_check("run /tmp/x allowed in test mode", path_allowed("/tmp/x", 1, 1));
    st_check("pull /private/var/tmp/sweetgl.log allowed (T-18)", path_allowed("/private/var/tmp/sweetgl.log", 0, 2));
    st_check("push /private/var/tmp/sweetgl.log refused (pull-only)", !path_allowed("/private/var/tmp/sweetgl.log", 0, 0));
    st_check("run /private/var/tmp/sweetgl.log refused", !path_allowed("/private/var/tmp/sweetgl.log", 0, 1));
    st_check("pull /private/var/tmp/other refused", !path_allowed("/private/var/tmp/other", 0, 2));
    st_check("pull /private/var/tmp/sweetgl.logx refused", !path_allowed("/private/var/tmp/sweetgl.logx", 0, 2));
    st_check("pull keeps k_allow (/usr/local/bin/x)", path_allowed("/usr/local/bin/x", 0, 2));

    st_check("push name: ordinary path ok", push_name_check("/usr/local/bin/x") == NULL);
    st_check("push name: .sweetorig refused", push_name_check("/usr/lib/x.sweetorig") != NULL);
    st_check("push name: .sweettmp refused", push_name_check("/usr/lib/x.sweettmp") != NULL);
    st_check("push name: '.' component refused", push_name_check("/usr/local/./x") != NULL);
    st_check("push name: trailing slash (empty component) refused", push_name_check("/usr/local/x/") != NULL);
    {
        char longp[600];
        memset(longp, 0, sizeof(longp));
        strcpy(longp, "/usr/local/");
        memset(longp + strlen(longp), 'a', 245);
        st_check("push name: 245-byte component ok", push_name_check(longp) == NULL);
        longp[strlen(longp)] = 'a';
        st_check("push name: 246-byte component refused", push_name_check(longp) != NULL);
    }
    st_check("push mode: /usr/local/bin/x is 0755", push_mode("/usr/local/bin/x", 0) == 0755);
    st_check("push mode: /private/var/dev/x is 0755", push_mode("/private/var/dev/x", 0) == 0755);
    st_check("push mode: /usr/lib/x is 0644", push_mode("/usr/lib/x", 0) == 0644);
    st_check("push mode: launchd.plist is 0644", push_mode("/System/Library/xpc/launchd.plist", 0) == 0644);
    st_check("push mode: org.pixelios plist is 0644", push_mode("/System/Library/LaunchDaemons/org.pixelios.devd.plist", 0) == 0644);
    st_check("push mode: /tmp/x is 0644 outside test mode", push_mode("/tmp/x", 0) == 0644);
    st_check("push mode: /tmp/x is 0755 in test mode", push_mode("/tmp/x", 1) == 0755);
    st_check("push mode: /tmp/a/devd-lib/x is 0644 in test mode (non-RUN stand-in)", push_mode("/tmp/a/devd-lib/x", 1) == 0644);
    st_check("push mode: /tmp/a/devd-lib/x is 0644 outside test mode", push_mode("/tmp/a/devd-lib/x", 0) == 0644);
    st_check("push target mode: a replaced 0755 /usr/lib file keeps 0755", push_target_mode("/usr/lib/dyld", 0, 1, S_IFREG | 0755) == 0755);
    st_check("push target mode: a replaced 0600 /usr/local file keeps 0600", push_target_mode("/usr/local/bin/x", 0, 1, S_IFREG | 0600) == 0600);
    st_check("push target mode: setuid of a replaced file is dropped", push_target_mode("/usr/lib/x", 0, 1, S_IFREG | 04755) == 0755);
    st_check("push target mode: a replaced 0775 file keeps 0775", push_target_mode("/usr/lib/x", 0, 1, S_IFREG | 0775) == 0775);
    st_check("push target mode: a replaced 0666 file keeps 0666", push_target_mode("/usr/lib/x", 0, 1, S_IFREG | 0666) == 0666);
    st_check("push target mode: a new /usr/lib file gets 0644", push_target_mode("/usr/lib/x", 0, 0, 0) == 0644);

    if (getifaddrs(&list) == 0) {
        for (it = list; it != NULL; it = it->ifa_next) {
            if (it->ifa_addr != NULL && it->ifa_addr->sa_family == AF_INET && it->ifa_name != NULL &&
                ((struct sockaddr_in *)(void *)it->ifa_addr)->sin_addr.s_addr == lo) {
                snprintf(loname, sizeof(loname), "%s", it->ifa_name);
                break;
            }
        }
        freeifaddrs(list);
    }
    if (loname[0] == '\0') {
        printf("SKIP: selftest iface: no interface carries 127.0.0.1 on this host\n");
    } else {
        st_check("iface_has_addr: loopback interface has 127.0.0.1", iface_has_addr(loname, lo));
        st_check("iface_has_addr: loopback interface lacks 192.168.7.2", !iface_has_addr(loname, inet_addr("192.168.7.2")));
        st_check("iface_has_addr: unknown interface name has nothing", !iface_has_addr("ue0-selftest-none", lo));
    }
    printf("RESULT: %s selftest %d passed, %d failed\n", st_fail == 0 ? "PASS" : "FAIL", st_pass, st_fail);
    return st_fail == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    int port = DEFAULT_PORT;
    int test_mode = 0;
    const char *key_path = NULL;
    uint8_t key[32];

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--test-mode") == 0) {
            test_mode = 1;
        } else if (strcmp(argv[i], "--key-file") == 0 && i + 1 < argc) {
            key_path = argv[++i];
        } else if (strcmp(argv[i], "--selftest") == 0) {
            return selftest();
        }
    }

    signal(SIGPIPE, SIG_IGN);

    /* G1: startup gate - the root-owned key file, never a boot-arg (H8). */
    if (key_path != NULL && !test_mode) {
        fprintf(stderr, "SWEET GAP: devd gate: --key-file is accepted only with --test-mode\n");
        return 1;
    }
    if (key_file_load(key_path != NULL ? key_path : DEVD_KEY_PATH,
                      test_mode ? geteuid() : (uid_t)DEVD_PHONE_KEY_UID, key) != 0) {
        devd_log("startup gate failed; exiting");
        return 1;
    }
    {
        const char *kp = key_path != NULL ? key_path : DEVD_KEY_PATH;
        struct stat ks;
        if (realpath(kp, g_key_real) == NULL || stat(kp, &ks) != 0) {
            fprintf(stderr, "SWEET GAP: devd gate: cannot resolve the key file %s (errno=%d)\n", kp, errno);
            return 1;
        }
        g_key_dev = ks.st_dev;
        g_key_ino = ks.st_ino;
        g_key_known = 1;
    }
    if (test_mode) {
        devd_log("test-mode: loopback bind, key file owner = the caller");
    } else if (!iface_has_addr(DEVD_PHONE_IFACE, inet_addr(DEVD_PHONE_ADDR))) {
        fprintf(stderr, "SWEET GAP: devd gate: %s is not an address of interface %s\n",
                DEVD_PHONE_ADDR, DEVD_PHONE_IFACE);
        return 1;
    }

    int server_fd = cloexec_fd(socket(AF_INET, SOCK_STREAM, 0));
    if (server_fd < 0) {
        perror("socket");
        return 1;
    }

    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = inet_addr(test_mode ? "127.0.0.1" : DEVD_PHONE_ADDR);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(server_fd);
        return 1;
    }

    if (listen(server_fd, 10) < 0) {
        perror("listen");
        close(server_fd);
        return 1;
    }

    devd_log("devd %s listening on %s:%d (%s)", DEVD_VERSION,
             test_mode ? "127.0.0.1" : DEVD_PHONE_ADDR, port,
             test_mode ? "test-mode" : "phone");

    int accept_fails = 0;
    while (1) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);
        int client_fd = accept(server_fd, (struct sockaddr *)&client_addr, &client_len);
        if (client_fd >= 0 && cloexec_fd(client_fd) < 0)
            continue; /* connection refused (fd closed, logged); keep serving */
        if (client_fd < 0) {
            if (errno == EINTR) continue;
            if (errno == ECONNABORTED || errno == ENOBUFS || errno == ENOMEM || errno == EMFILE ||
                errno == ENFILE) {
                accept_fails++;
                fprintf(stderr, "SWEET GAP: devd accept failed (errno=%d), %d of %d in a row\n",
                        errno, accept_fails, DEVD_ACCEPT_FAIL_MAX);
                if (accept_fails < DEVD_ACCEPT_FAIL_MAX) {
                    sleep(DEVD_ACCEPT_RETRY_SEC);
                    continue;
                }
                devd_log("accept failed %d times in a row; exiting non-zero", accept_fails);
                close(server_fd);
                return 1;
            }
            devd_log("accept failed (errno=%d); exiting non-zero", errno);
            close(server_fd);
            return 1;
        }
        accept_fails = 0;
        char peer[INET_ADDRSTRLEN];
        const char *peer_str = inet_ntop(AF_INET, &client_addr.sin_addr, peer, sizeof(peer));
        devd_log("client connected: %s:%d",
                 peer_str ? peer : "?", ntohs(client_addr.sin_port));
        if (client_addr.sin_addr.s_addr != inet_addr(test_mode ? "127.0.0.1" : DEVD_PHONE_PEER)) {
            fprintf(stderr, "SWEET GAP: devd dropped peer %s reason=not %s\n",
                    peer_str ? peer : "?", test_mode ? "127.0.0.1" : DEVD_PHONE_PEER);
            close(client_fd);
            continue;
        }
        handle_client(client_fd, test_mode, key);
        close(client_fd);
        devd_log("client disconnected");
    }

    close(server_fd);
    return 0;
}
