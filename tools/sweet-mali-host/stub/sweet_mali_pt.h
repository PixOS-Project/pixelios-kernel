/* host-test stub of bsd/dev/sweet_mali_pt.h: only the names sweet_mali.c uses; the page-table code is not under test (the job state machine is).
 * Hidden behaviour: map/unmap/va_alloc succeed trivially, nothing is tracked. */
#ifndef STUB_SWEET_MALI_PT_H
#define STUB_SWEET_MALI_PT_H
#include <stdint.h>
#define MALI_VA_START 0x100000000ull
#define MALI_VA_END   0x200000000ull
#define MALI_PG       0x1000ull
#define MALI_F_EXEC   1u
struct mali_va { int dummy; };
struct mali_pt { uint64_t l0_pa, l1_pa, l2_pa[4], l3_pa[4096]; void *l2[4], *l3[4096]; };
struct mali_pt_ops { uint64_t *(*alloc)(void *ctx, uint64_t *pa); void *ctx; };
static inline uint64_t mali_va_alloc(struct mali_va *v, uint32_t n, uint32_t a) { (void)v; (void)n; (void)a; return MALI_VA_START; }
static inline int mali_va_free(struct mali_va *v, uint64_t va, uint32_t n) { (void)v; (void)va; (void)n; return 0; }
static inline int mali_pt_init(struct mali_pt *p, struct mali_pt_ops o) { (void)p; (void)o; return 0; }
static inline int mali_pt_map(struct mali_pt *p, uint64_t va, uint64_t pa, unsigned f) { (void)p; (void)va; (void)pa; (void)f; return 0; }
static inline int mali_pt_unmap(struct mali_pt *p, uint64_t va) { (void)p; (void)va; return 0; }
#endif
