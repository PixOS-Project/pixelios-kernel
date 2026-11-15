/* Host compile + run of the KERNEL glue of bsd/dev/sweet_chg.c (xnu patch 0283): the branch the production kernel uses (not SWEET_CHG_HOST), against stubs of
 * the XNU functions it calls. tools/test_chg_probe.py compiles it with -Wall -Wextra -Werror (so `const volatile` is type-checked: a store through a mapping
 * would not compile) and feeds it the /sweet-chg properties that tools/mkadt.py really generated (file argv[1]: lines `name hexbytes`).
 *   argv[2] = open | closed | off | nonode
 * Mappings are mmap'ed fake register pages filled by PHYSICAL address (independent copy of the note's addresses). In `closed` the HSI2C12 mapping is
 * PROT_NONE: any access to it kills this process with SIGSEGV, which the test reports as a failure. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <unistd.h>
#include <sys/mman.h>

typedef uintptr_t vm_offset_t;
typedef uintptr_t vm_size_t;
typedef int DTEntry;
#define kSuccess 0
typedef int kern_return_t;

static struct { char name[64]; uint8_t *v; unsigned n; } dt_prop[128];
static int dt_n, mode_closed, mode_off, mode_nonode;
static struct { uint64_t base; uint64_t len; uint8_t *p; } maps[16];
static int nmaps;

static void
segv(int sig)
{
	(void)sig;
	const char m[] = "CASE glue_no_access_to_gated_block FAIL SIGSEGV: the gated hsi2c12 window was touched\n";
	(void)!write(1, m, sizeof(m) - 1);
	_exit(3);
}

static kern_return_t
SecureDTLookupEntry(void *x, const char *path, DTEntry *e)
{
	(void)x;
	*e = 1;
	return (!mode_nonode && strcmp(path, "/sweet-chg") == 0) ? kSuccess : 1;
}

static kern_return_t
SecureDTGetProperty(DTEntry e, const char *name, void const **v, unsigned int *size)
{
	int i;

	(void)e;
	for (i = 0; i < dt_n; i++) {
		if (strcmp(dt_prop[i].name, name) == 0) {
			*v = dt_prop[i].v;
			*size = dt_prop[i].n;
			return kSuccess;
		}
	}
	return 1;
}

static int
PE_parse_boot_argn(const char *name, void *out, int len)
{
	if (mode_off && strcmp(name, "sweetchgprobe") == 0 && len == 4) {
		uint32_t z = 0;

		memcpy(out, &z, 4);
		return 1;
	}
	return 0;
}

static void absolutetime_to_nanoseconds(uint64_t a, uint64_t *ns) { *ns = a; }
static uint64_t mach_absolute_time(void) { static uint64_t t = 1000000000ull; return t += 1000000ull; }

/* fake register value by physical address (note 1.3 open pattern; hsi2c12 words are recognisable markers) */
static uint32_t
phys_word(uint64_t pa)
{
	switch (pa) {
	case 0x10C00650: return 0x10;
	case 0x10C01810: return 0;
	case 0x10C02030: case 0x10C02050: case 0x10C0206C: return 0x200000;
	case 0x10C03030: return 0x3F0002;
	case 0x1E0810E8: return 1;
	case 0x1E0818E0: return 0;
	case 0x1E082104: return 0x200000;
	case 0x10C21010: return 0x4;
	case 0x10C40060: return 0x00220000;
	case 0x10C40068: return 0x00330000;
	case 0x10D50000 + 0xC4: return 0xC0DE00C4;
	case 0x10D50000 + 0x68: return 33u << 16;
	default: return 0xDEAD0000u | (uint32_t)(pa & 0xffff);
	}
}

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
vm_offset_t
ml_io_map(vm_offset_t pa, vm_size_t len)
{
	uint8_t *p = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
	uint64_t a;

	if (p == MAP_FAILED || nmaps >= 16 || (pa & 0x3fff) != 0 || (len & 0x3fff) != 0) {
		return 0;               /* the production code must hand ml_io_map whole 16 KiB pages */
	}
	for (a = 0; a < len; a += 4) {
		uint32_t w = phys_word(pa + a);

		memcpy(p + a, &w, 4);
	}
	maps[nmaps].base = pa;
	maps[nmaps].len = len;
	maps[nmaps].p = p;
	nmaps++;
	if (mode_closed && pa == 0x10D50000) {
		mprotect(p, len, PROT_NONE);
	}
	return (vm_offset_t)p;
}

#include CHG_SRC

int
main(int argc, char **argv)
{
	FILE *f;
	char line[512];
	int i;

	if (argc < 3) {
		return 2;
	}
	mode_closed = strcmp(argv[2], "closed") == 0;
	mode_off = strcmp(argv[2], "off") == 0;
	mode_nonode = strcmp(argv[2], "nonode") == 0;
	f = fopen(argv[1], "r");
	if (f == NULL) {
		return 2;
	}
	while (fgets(line, sizeof(line), f) != NULL) {
		char *sp = strchr(line, ' ');
		unsigned k, n;

		if (sp == NULL) {
			continue;
		}
		*sp++ = 0;
		n = (unsigned)strlen(sp);
		while (n > 0 && (sp[n - 1] == '\n' || sp[n - 1] == ' ')) {
			sp[--n] = 0;
		}
		snprintf(dt_prop[dt_n].name, sizeof(dt_prop[dt_n].name), "%.63s", line);
		dt_prop[dt_n].n = n / 2;
		dt_prop[dt_n].v = aligned_alloc(8, 16 + n / 2);
		for (k = 0; k < n / 2; k++) {
			unsigned b;

			sscanf(sp + 2 * k, "%2x", &b);
			dt_prop[dt_n].v[k] = (uint8_t)b;
		}
		dt_n++;
	}
	fclose(f);
	setvbuf(stdout, NULL, _IONBF, 0);
	signal(SIGSEGV, segv);
	for (i = 0; i < 4; i++) {
		sweet_chg_tick(0);
	}
	printf("GLUE nmaps=%d\n", nmaps);
	return 0;
}
