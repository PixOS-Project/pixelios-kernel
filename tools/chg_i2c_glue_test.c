/* Host compile + run of the KERNEL glue of bsd/dev/sweet_chg_i2c.c (xnu patch 0284) against XNU stubs: type-checks the real store function and ADT parsing, with the
 * HSI2C12 mapping PROT_READ so any store would SIGSEGV. argv[1] = /sweet-chg properties (`name hexbytes` lines, from tools/mkadt.py), argv[2] = default | on-gate-closed | nonode.
 * `default`: sweetchgi2c absent -> nothing mapped/read/written. `on-gate-closed`: sweetchgi2c=1 but sweet_chg_gate_still_open() says 0 -> mapped, still zero stores. */
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
static int dt_n, m_on, m_nonode, nmaps;

static void segv(int s) { (void)s; const char m[] = "CASE i2c_glue_no_store FAIL SIGSEGV: a store reached the PROT_READ window\n"; (void)!write(1, m, sizeof(m) - 1); _exit(3); }
static kern_return_t SecureDTLookupEntry(void *x, const char *p, DTEntry *e) { (void)x; *e = 1; return (!m_nonode && strcmp(p, "/sweet-chg") == 0) ? kSuccess : 1; }
static kern_return_t SecureDTGetProperty(DTEntry e, const char *n, void const **v, unsigned int *sz)
{
	(void)e;
	for (int i = 0; i < dt_n; i++) {
		if (strcmp(dt_prop[i].name, n) == 0) { *v = dt_prop[i].v; *sz = dt_prop[i].n; return kSuccess; }
	}
	return 1;
}
static int PE_parse_boot_argn(const char *n, void *out, int len)
{
	if (m_on && strcmp(n, "sweetchgi2c") == 0 && len == 4) { uint32_t o = 1; memcpy(out, &o, 4); return 1; }
	if (m_on && strcmp(n, "sweetchgicl") == 0 && len == 4) { uint32_t o = 900; memcpy(out, &o, 4); return 1; }
	return 0;
}
static void absolutetime_to_nanoseconds(uint64_t a, uint64_t *ns) { *ns = a; }
static uint64_t mach_absolute_time(void) { static uint64_t t = 1000000000ull; return t += 1000000ull; }
int sweet_chg_gate_still_open(void) { printf("STUB gate_still_open -> 0\n"); return 0; }
uint32_t sweet_usb_ss_configured(void) { return 0; }

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t len)
{
	uint8_t *p = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
	if (p == MAP_FAILED || pa != 0x10D50000 || len != 0x4000) { printf("GLUE bad ml_io_map(%#lx, %#lx)\n", (unsigned long)pa, (unsigned long)len); return 0; }
	mprotect(p, len, PROT_READ);
	nmaps++;
	return (vm_offset_t)p;
}

#include CHG_I2C_SRC

int main(int argc, char **argv)
{
	FILE *f;
	char line[512];

	if (argc < 3) { return 2; }
	m_on = strcmp(argv[2], "default") != 0;
	m_nonode = strcmp(argv[2], "nonode") == 0;
	f = fopen(argv[1], "r");
	if (!f) { return 2; }
	while (fgets(line, sizeof(line), f)) {
		char *sp = strchr(line, ' ');
		unsigned k, n;
		if (!sp) { continue; }
		*sp++ = 0;
		n = (unsigned)strlen(sp);
		while (n && (sp[n - 1] == '\n' || sp[n - 1] == ' ')) { sp[--n] = 0; }
		snprintf(dt_prop[dt_n].name, sizeof(dt_prop[dt_n].name), "%.63s", line);
		dt_prop[dt_n].n = n / 2;
		dt_prop[dt_n].v = aligned_alloc(8, 16 + n / 2);
		for (k = 0; k < n / 2; k++) { unsigned b; sscanf(sp + 2 * k, "%2x", &b); dt_prop[dt_n].v[k] = (uint8_t)b; }
		dt_n++;
	}
	fclose(f);
	setvbuf(stdout, NULL, _IONBF, 0);
	signal(SIGSEGV, segv);
	for (int i = 0; i < 6; i++) { sweet_chg_i2c_tick(0); }
	printf("GLUE nmaps=%d xfers=%u nwr=%u\n", nmaps, i2c_xfers, i2c_nwr);
	return 0;
}
