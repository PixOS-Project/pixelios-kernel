#!/usr/bin/env python3
"""Tests for tools/pixnet.c (host build, Linux) and the DNS keys of tools/pixstate.c. Host only, no phone, no network.

pixnet.c is compiled with the host gcc (-Wall -Wextra -Werror) and run against a missing interface and with bad arguments;
the steps that need a real link (dns, tcp, tls) are NOT run here: the TLS code is Apple-only (SecureTransport) and is covered
by the iOS cross-build in tools/test_pixping.py (pixping-stage builds it) and by the on-phone run. Last line:
RESULT: PASS|FAIL tests=N failures=F errors=E skipped=S ; exit 0 only if no failure/error.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import os, re, socket, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class Host(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='pixnet-test-')
        cls.exe = os.path.join(cls.tmp.name, 'pixnet')
        p = subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-O2', '-o', cls.exe, os.path.join(HERE, 'pixnet.c')], capture_output=True, text=True)
        assert p.returncode == 0, 'host gcc failed: ' + p.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_px(self, *args):
        p = subprocess.run([self.exe, *args], capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout

    def test_missing_interface_fails_loudly_exit_3(self):
        rc, out = self.run_px('-W', '1', '-i', 'nosuch0')
        self.assertEqual(rc, 3, out)
        self.assertRegex(out, r'(?m)^pixnet: FAIL iface iface=nosuch0 addr=no route=no waited_s=1 \(interface missing\)$')
        for step in ('tcp-ip', 'dns', 'tcp-name', 'http-date', 'tls', 'tls-noverify', 'https-get', 'download'):
            self.assertRegex(out, r'(?m)^pixnet: GAP %s skipped: interface not ready$' % step)
        self.assertRegex(out, r'(?m)^pixnet: RESULT pass=1 fail=1 gap=8$')
        self.assertTrue(out.splitlines()[-1].startswith('pixnet: RESULT '))

    def test_every_line_has_the_documented_shape(self):
        rc, out = self.run_px('-W', '0', '-i', 'nosuch0')
        for l in out.splitlines():
            self.assertRegex(l, r'^pixnet: (start |delay |waiting |PASS |FAIL |GAP |RESULT )', l)

    def test_argument_errors_exit_2_with_gap(self):
        for args in (['-a', 'x'], ['-a', '1.2.3'], ['-n', 'bad name'], ['-n', ''], ['-W', '-1'], ['-W', 'x'], ['-d', '5', '-u', 'ftp://h/x'],
                     ['-u', 'http://'], ['extra'], ['-z'], ['-i', 'x' * 40]):
            rc, out = self.run_px(*args)
            self.assertEqual(rc, 2, (args, out))
            self.assertIn('pixnet: GAP', out, args)

    def test_stdout_is_unbuffered(self):
        p = subprocess.Popen([self.exe, '-W', '3', '-i', 'nosuch0'], stdout=subprocess.PIPE, text=True)
        self.assertTrue(p.stdout.readline().startswith('pixnet: start '))
        self.assertIsNone(p.poll(), 'first line must arrive while the process still waits')
        p.communicate(timeout=10)

    def test_no_download_url_is_a_gap_not_a_default(self):
        src = open(os.path.join(HERE, 'pixnet.c')).read()
        self.assertIn('skipped: -u URL not given (no default third-party file)', src)
        self.assertNotRegex(src, r'https?://(?!\*)[a-z0-9.-]+\.[a-z]{2,}/\S*\.(zip|bin|iso|img|dmg|tar)')


class Served(unittest.TestCase):
    """pixnet against a local TCP server on loopback (-i lo -R: no default route on lo, -P/-u point at the server)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='pixnet-test2-')
        cls.exe = os.path.join(cls.tmp.name, 'pixnet')
        p = subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-O2', '-o', cls.exe, os.path.join(HERE, 'pixnet.c')], capture_output=True, text=True)
        assert p.returncode == 0, 'host gcc failed: ' + p.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def serve(self, reply, hold=0.0):
        """one listener, every connection gets `reply` (bytes) then stays open `hold` s; returns the port"""
        ls = socket.socket()
        ls.bind(('127.0.0.1', 0))
        ls.listen(8)
        self.addCleanup(ls.close)

        def one(c):
            try:
                c.settimeout(2)
                try:
                    c.recv(4096)
                except OSError:  # lint-silent: expected client closed before sending (fake server)
                    pass
                c.sendall(reply)
                time.sleep(hold)
            except OSError:  # lint-silent: expected client went away mid-reply (fake server)
                pass
            finally:
                c.close()

        def loop():
            while True:
                try:
                    c, _ = ls.accept()
                except OSError:  # lint-silent: expected listener closed at test teardown
                    return
                threading.Thread(target=one, args=(c,), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()
        return ls.getsockname()[1]

    def run_px(self, *args):
        p = subprocess.run([self.exe, '-i', 'lo', '-R', '-W', '2', '-n', 'localhost', *args], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout

    def test_route_skip_prints_route_skipped(self):
        rc, out = self.run_px('-a', '127.0.0.1')
        self.assertRegex(out, r'(?m)^pixnet: PASS iface iface=lo addr=yes route=skipped\(-R\) ')

    def test_read_error_after_headers_fails_download(self):
        # headers + 3 body bytes of 100000, then silence: a read timeout after the headers must be a FAIL, not a PASS with 3 bytes
        port = self.serve(b'HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\nabc', hold=8)
        rc, out = self.run_px('-a', '127.0.0.1', '-d', '2', '-u', 'http://127.0.0.1:%d/x' % port)
        self.assertRegex(out, r'(?m)^pixnet: FAIL download .*err="read timeout after \d+ ms"', out)
        self.assertNotRegex(out, r'(?m)^pixnet: PASS download', out)
        self.assertEqual(rc, 1, out)

    def test_http_500_is_not_a_pass(self):
        port = self.serve(b'HTTP/1.1 500 Oops\r\nContent-Length: 2\r\nConnection: close\r\n\r\nno')
        rc, out = self.run_px('-a', '127.0.0.1', '-d', '2', '-u', 'http://127.0.0.1:%d/x' % port)
        self.assertRegex(out, r'(?m)^pixnet: FAIL download .*err="HTTP status 500"', out)
        self.assertEqual(rc, 1, out)

    def test_clean_download_passes_and_host_header_has_port(self):
        seen = []
        ls = socket.socket()
        ls.bind(('127.0.0.1', 0))
        ls.listen(8)
        self.addCleanup(ls.close)

        def loop():
            while True:
                try:
                    c, _ = ls.accept()
                except OSError:  # lint-silent: expected listener closed at test teardown
                    return
                seen.append(c.recv(4096))
                c.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nhello')
                c.close()
        threading.Thread(target=loop, daemon=True).start()
        port = ls.getsockname()[1]
        rc, out = self.run_px('-a', '127.0.0.1', '-d', '1', '-u', 'http://localhost:%d/x' % port)
        self.assertRegex(out, r'(?m)^pixnet: PASS download ', out)
        self.assertTrue(seen and b'\r\nHost: localhost:%d\r\n' % port in seen[0], seen[:1])

    def test_http_date_port_option_and_1971_date_with_good_host_clock(self):
        # -T without root; the host clock is right, so -T is "not needed" here. The 1971/status refusal before settimeofday is
        # tested in the Driver test (set_clock_from_date called directly: the EPERM line must not appear)
        self.assertNotEqual(os.geteuid(), 0, 'run this test without root')
        port = self.serve(b'HTTP/1.1 200 OK\r\nDate: Fri, 01 Jan 1971 00:00:00 GMT\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        rc, out = self.run_px('-a', '127.0.0.1', '-P', str(port), '-T')
        self.assertRegex(out, r'(?m)^pixnet: PASS http-date dst=127.0.0.1 port=%d status=200 ' % port)
        self.assertRegex(out, r'(?m)^pixnet: GAP clock-set not needed')
        self.assertNotIn('settimeofday', out)


DRIVER = r'''
#define main pixnet_main
#include "pixnet.c"
#undef main
#include <sys/socket.h>
static void fail(const char *m) { printf("DRIVER-FAIL %s\n", m); exit(1); }
int main(void)
{
	char msg[200], why[100];
	/* F4: year/status checks run before settimeofday (a refusal is -3, a reached settimeofday is 0 or -1) */
	if (set_clock_from_date("Fri, 01 Jan 1971 00:00:00", 200, msg, sizeof msg) != -3) fail("1971 accepted");
	if (set_clock_from_date("Fri, 01 Jan 2101 00:00:00", 200, msg, sizeof msg) != -3) fail("2101 accepted");
	if (set_clock_from_date("Tue, 01 Jan 2030 00:00:00", 404, msg, sizeof msg) != -3) fail("status 404 accepted");
	if (set_clock_from_date("garbage", 200, msg, sizeof msg) != -2) fail("garbage not -2");
	if (geteuid() != 0) {
		int r = set_clock_from_date("Tue, 01 Jan 2030 00:00:00", 200, msg, sizeof msg);
		if (r != -1 || !strstr(msg, "settimeofday errno")) fail("valid date did not reach settimeofday");
	}
	/* F1 via http_do over a socketpair: headers + 3 bytes then a stall */
	int sv[2];
	if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) fail("socketpair");
	const char *rep = "HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\nabc";
	if (write(sv[1], rep, strlen(rep)) < 0) fail("write");
	struct conn c; memset(&c, 0, sizeof c); c.fd = sv[0];
	struct http h;
	int64_t dl = now_ms() + 1500;
	if (http_do(&c, "GET", "h", 80, "/", &h, INT64_MAX, dl) != -1) fail("read error after headers returned 0");
	/* F1/status: get_verdict */
	struct http g; memset(&g, 0, sizeof g);
	g.status = 500; g.complete = 1;
	if (get_verdict(&g, 100, now_ms() + 1000, why, sizeof why) == 0) fail("500 accepted");
	g.status = 404;
	if (get_verdict(&g, 100, now_ms() + 1000, why, sizeof why) == 0) fail("404 accepted");
	g.status = 200; g.complete = 0; g.body = 3;
	if (get_verdict(&g, 100, now_ms() + 100000, why, sizeof why) == 0) fail("truncated body accepted");
	g.complete = 1;
	if (get_verdict(&g, 100, now_ms() + 100000, why, sizeof why) != 0) fail("good response refused");
	/* URL: control characters and overlong paths refused */
	struct url u;
	if (parse_url("http://h/a b", &u) == 0) fail("space in path accepted");
	if (parse_url("http://h/a\r\nX: y", &u) == 0) fail("CRLF in path accepted");
	if (parse_url("http://h:8080/ok", &u) != 0 || u.port != 8080) fail("good url refused");
	printf("DRIVER-OK\n");
	return 0;
}
'''


class Driver(unittest.TestCase):
    def test_driver(self):
        with tempfile.TemporaryDirectory(prefix='pixnet-drv-') as d:
            src = os.path.join(d, 'drv.c')
            open(src, 'w').write(DRIVER)
            exe = os.path.join(d, 'drv')
            p = subprocess.run(['gcc', '-Wall', '-Wextra', '-O2', '-I', HERE, '-o', exe, src], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run([exe], capture_output=True, text=True, timeout=30)
            self.assertIn('DRIVER-OK', p.stdout + p.stderr)


class PixstateDnsKeys(unittest.TestCase):
    def test_regex_has_the_three_dns_keys(self):
        s = open(os.path.join(HERE, 'pixstate.c')).read()
        m = re.search(r'scCopyKeys\(st, CFSTR\("([^"]+)"\)\)', s)
        self.assertTrue(m, 'key regex not found')
        rx = m.group(1)
        for key in ('State:/Network/Global/DNS', 'State:/Network/Service/ABCD-1/DNS', 'Setup:/Network/Global/DNS'):
            self.assertIsNotNone(re.fullmatch(rx, key), key)
        self.assertIsNotNone(re.fullmatch(rx, 'State:/Network/Global/IPv4'), 'old keys kept')


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    bad = len(res.failures) + len(res.errors)
    print('RESULT: %s tests=%d failures=%d errors=%d skipped=%d' % ('FAIL' if bad else 'PASS', res.testsRun, len(res.failures), len(res.errors), len(res.skipped)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
