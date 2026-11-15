"""xnu patch 0352 (sweet-sampler-deep): host test of the pure core of bsd/dev/sweet_sample.c.
The block between SMP_CORE_BEGIN and SMP_CORE_END (added by xnu/patches/0352-sweet-sampler-deep.patch to the file created by 0054) is extracted from the patch text and
compiled with the host C compiler: the decimal boot-arg parser (range, defaults, GAP cases) and the backtrace row split (first row format, bt[k] rows, 8 addresses per row,
every row shorter than 200 characters, worst case name length). Static checks: the defaults and the 48-frame constant in the patch text, both boot-args registered.
The kernel glue (PE_parse_boot_arg_str, the frame walk with vm_map_read_user, printf) is NOT run here: only a build + a boot shows it."""
import os, re, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATCH = os.path.join(ROOT, "xnu", "patches", "0352-sweet-sampler-deep.patch")
TEXT = open(PATCH).read()

def core():
    out, on = [], False
    for ln in TEXT.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            b = ln[1:]
            if "SMP_CORE_BEGIN" in b:
                on = True
            elif "SMP_CORE_END" in b:
                on = False
            elif on:
                out.append(b)
    return "\n".join(out)

DRIVER = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#define NULL_UNUSED
%(core)s
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %%d: %%s\n", __LINE__, #c); bad++; } } while (0)
static int dec(const char *s, int lo, int hi, int def, int *o) { return sweet_sample_dec(s, lo, hi, def, o); }
int main(void) {
	int v;
	/* parser */
	CHK(dec("5", 5, 600, 120, &v) == 0 && v == 5);
	CHK(dec("600", 5, 600, 120, &v) == 0 && v == 600);
	CHK(dec("0060", 5, 600, 120, &v) == 0 && v == 60);
	CHK(dec("4", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("601", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("0x10", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("-7", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("+7", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("12a", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("1 2", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("9999999999999", 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec(NULL, 5, 600, 120, &v) == -1 && v == 120);
	CHK(dec("1", 1, 60, 40, &v) == 0 && v == 1);
	CHK(dec("60", 1, 60, 40, &v) == 0 && v == 60);
	CHK(dec("0", 1, 60, 40, &v) == -1 && v == 40);
	CHK(dec("61", 1, 60, 40, &v) == -1 && v == 40);
	CHK(SMP_FIRST_S == 90 && SMP_IV_DEF == 120 && SMP_IV_MIN == 5 && SMP_IV_MAX == 600);
	CHK(SMP_MAX_DEF == 40 && SMP_MAX_MIN == 1 && SMP_MAX_MAX == 60 && SMP_ROW_LIM == 200);
	/* rows */
	uint64_t bt[48];
	for (int i = 0; i < 48; i++) bt[i] = 0x1cb7a8000ull + i * 0x1111111ull;
	const char *names[] = {"coreduetd", "a", "0123456789012345678901234567890"};
	for (int ni = 0; ni < 3; ni++) {
		for (int nbt = 0; nbt <= 48; nbt++) {
			int k = 0, rows = 0, first = 1;
			do {
				char row[256];
				int got = sweet_sample_row(row, sizeof row, names[ni], 123, ~0ull, ~0ull, ~0ull, bt, nbt, k, first);
				CHK(strlen(row) < 200);
				CHK(got <= 8);
				if (nbt > 0) CHK(got > 0);
				char want[64];
				if (first) CHK(strncmp(row, "SMP ", 4) == 0 && strstr(row, " pc 0x") && strstr(row, " lr 0x") && strstr(row, " sp 0x") && strstr(row, " bt") );
				else { snprintf(want, sizeof want, " t123 bt[%%d] 0x", k); CHK(strstr(row, want) != NULL); }
				/* addresses on the row are exactly bt[k..k+got-1] in order */
				const char *p = strstr(row, " bt");
				if (!first) p = strstr(row, "] ");
				int cnt = 0;
				while (p && (p = strstr(p, " 0x")) != NULL) {
					unsigned long long a; sscanf(p + 1, "%%llx", &a);
					CHK(k + cnt < nbt && a == bt[k + cnt]);
					cnt++; p += 3;
				}
				CHK(cnt == got);
				k += got; first = 0; rows++;
				if (got == 0) break;
			} while (k < nbt);
			CHK(k == nbt);
		}
	}
	/* first row format, byte for byte, as 0054 printed it */
	{
		char row[256];
		int got = sweet_sample_row(row, sizeof row, "coreduetd", 2, 0x10, 0x20, 0x30, bt, 3, 0, 1);
		CHK(got == 3);
		CHK(strcmp(row, "SMP coreduetd t2 pc 0x10 lr 0x20 sp 0x30 bt 0x1cb7a8000 0x1cc8b9111 0x1cd9ca222") == 0);
		got = sweet_sample_row(row, sizeof row, "coreduetd", 2, 0, 0, 0, bt, 20, 8, 0);
		CHK(got == 8 && strncmp(row, "SMP coreduetd t2 bt[8] 0x", 25) == 0);
		got = sweet_sample_row(row, sizeof row, "coreduetd", 2, 0, 0, 0, bt, 20, 16, 0);
		CHK(got == 4);
	}
	printf(bad ? "FAILED %%d\n" : "OK\n", bad);
	return bad != 0;
}
'''

class T(unittest.TestCase):
    def test_core_compiles_and_passes(self):
        c = core()
        self.assertIn("sweet_sample_dec", c)
        with tempfile.TemporaryDirectory() as d:
            src, exe = os.path.join(d, "t.c"), os.path.join(d, "t")
            open(src, "w").write(DRIVER % {"core": c})
            r = subprocess.run(["cc", "-Wall", "-Werror", "-O1", "-fsanitize=address,undefined", "-o", exe, src], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)

    def test_static_constants(self):
        self.assertRegex(TEXT, r"\+#define SWEET_SMP_BT_MAX 48\b")
        self.assertRegex(TEXT, r"\+#define SMP_ROW_LIM\s+200\b")
        self.assertRegex(TEXT, r'PE_parse_boot_arg_str\("sweetsampleiv"')
        self.assertRegex(TEXT, r'PE_parse_boot_arg_str\("sweetsamplemax"')
        self.assertIn("SWEET GAP: %s=", TEXT)
        self.assertIn("PE_parse_boot_arg_str", TEXT)
        self.assertNotRegex(TEXT, r"\+.*(ml_io_write|IOWrite|writel|ufs)", "read-only sampler")

    def test_bootargs_registered(self):
        known = open(os.path.join(HERE, "bootargs-known.txt")).read()
        for a in ("sweetsampleiv", "sweetsamplemax"):
            self.assertRegex(known, r"(?m)^%s\tpatch:0352$" % a)

if __name__ == "__main__":
    unittest.main()
