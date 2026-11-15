"""Host test of the per-second udpdown row of tools/pixperf.c (lowclk A3 prep): compiles the UDPDOWN-ACCT block and feeds it a fake sequence.
Row: `PIXPERF UDPDOWN t=<s> got=<this second> lost=<gap-based, this second> bytes=<this second>`.
Prints `RESULT: PASS pixperf-udpdown-acct cases=N`."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "pixperf.c")).read()

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
%s
static int fails;
#define ROW(want) do { udd_tick(&a, got, maxs, bytes, o, sizeof o); if (strcmp(o, want)) { fprintf(stderr, "got '%%s' want '%%s'\n", o, want); fails++; } } while (0)
int main(void)
{
	udd_acct_t a = { 0, 0, 0, 0 };
	char o[128];
	uint32_t got = 0, maxs = 0;
	long long bytes = 0;
	/* second 1: seq 0..9 all arrive */
	got = 10; maxs = 10; bytes = 12000;
	ROW("PIXPERF UDPDOWN t=1 got=10 lost=0 bytes=12000");
	/* second 2: seq 10..19 sent, 13 and 14 missing -> maxseq 20, got 18 */
	got = 18; maxs = 20; bytes = 12000 + 8 * 1200;
	ROW("PIXPERF UDPDOWN t=2 got=8 lost=2 bytes=9600");
	/* second 3: nothing arrives: zeros, the same row shape */
	ROW("PIXPERF UDPDOWN t=3 got=0 lost=0 bytes=0");
	/* second 4: late 13 fills an old gap (got +1, maxs same): lost clamped to 0, not -1 */
	got = 19; bytes += 1200;
	ROW("PIXPERF UDPDOWN t=4 got=1 lost=0 bytes=1200");
	/* second 5: seq 20..29 sent, only 29 arrives -> maxs 30, got 20: 9 new gaps */
	got = 20; maxs = 30; bytes += 1200;
	ROW("PIXPERF UDPDOWN t=5 got=1 lost=9 bytes=1200");
	/* the cumulative loss now is maxs - got = 10 = (2 - 1) + 9 after the clamp row: 1 gap healed in sec 4 */
	if (maxs - got != 10) fails++;
	puts(fails ? "bad" : "ok");
	return fails != 0;
}
'''


def main():
    m = re.search(r"/\* UDPDOWN-ACCT-BEGIN.*?/\* UDPDOWN-ACCT-END \*/", SRC, re.S)
    if not m:
        print("RESULT: FAIL pixperf-udpdown-acct: block markers missing in tools/pixperf.c")
        return 1
    with tempfile.TemporaryDirectory() as d:
        c, exe = os.path.join(d, "t.c"), os.path.join(d, "t")
        open(c, "w").write(HARNESS % m.group(0))
        r = subprocess.run(["cc", "-Wall", "-Werror", "-o", exe, c], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            print("RESULT: FAIL pixperf-udpdown-acct: compile")
            return 1
        r = subprocess.run([exe], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr + r.stdout)
            print("RESULT: FAIL pixperf-udpdown-acct: rows")
            return 1
    # the final summary format must stay as it was
    for needle in (' got=%u lost_upto_max=%u reord=%u dup=%u maxseq=%u', 'pixperf: RESULT test=%s secs=%.1f bytes=%lld mbit=%.1f%s %s (client view; the server line counts)'):
        if needle not in SRC:
            print("RESULT: FAIL pixperf-udpdown-acct: final summary format changed")
            return 1
    print("RESULT: PASS pixperf-udpdown-acct cases=6")
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
