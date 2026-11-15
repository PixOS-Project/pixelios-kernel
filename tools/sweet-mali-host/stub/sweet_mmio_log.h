/* host-test stub of bsd/dev/sweet_mmio_log.h. sweet_mali.c rr()/wr() call sweet_mmio_log() once per register access ('R' after the load, 'W' BEFORE the store):
 * the harness (sweet_mali_host.c) implements it and uses it as the hardware model's access hook. */
#ifndef STUB_SWEET_MMIO_LOG_H
#define STUB_SWEET_MMIO_LOG_H
#include <stdint.h>
#define SWEET_MMIO_GPU 0
#define SWEET_MMIO_GPU_BASE 0u
void sweet_mmio_log(int dev, char dir, uint32_t addr, int width, uint32_t v);
static inline void sweet_mmio_log_summary(int dev, const char *tag) { (void)dev; (void)tag; }
#endif
