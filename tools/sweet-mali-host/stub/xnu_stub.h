/* host-test stand-in for the XNU headers sweet_mali.c includes (force-included with -include; the real header names resolve to empty files in this dir).
 * Only what sweet_mali.c needs; every non-trivial stub is defined in sweet_mali_host.c (fake clock, lock, sleep, printf capture). */
#ifndef XNU_STUB_H
#define XNU_STUB_H
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <string.h>
typedef int boolean_t;
#define TRUE 1
#define FALSE 0
typedef uint64_t addr64_t;
typedef uintptr_t vm_offset_t;
typedef uintptr_t vm_size_t;
typedef uint32_t ppnum_t;
typedef unsigned int natural_t;
typedef int kern_return_t;
typedef int wait_result_t;
typedef void *event_t;
typedef void *thread_t;
typedef void *processor_t;
typedef void *proc_t;
typedef void *task_t;
typedef void *host_t;
typedef int host_flavor_t;
typedef int *host_info64_t;
typedef int task_flavor_t;
typedef int *task_info_t;
typedef unsigned int mach_msg_type_number_t;
typedef void (*thread_continue_t)(void *, wait_result_t);
typedef struct { uint64_t total_user, total_system; } task_absolutetime_info_data_t;
typedef int lck_mtx_t;
#define KERN_SUCCESS 0
#define TASK_NULL ((task_t)0)
#define TASK_ABSOLUTETIME_INFO 1
#define TASK_ABSOLUTETIME_INFO_COUNT 4
#define PROC_ALLPROCLIST 1
#define PROC_NOWAITTRANS 2
#define PROC_RETURNED 0
#define MAXCOMLEN 16
#define PAGE_SIZE 0x4000ull
#define PAGE_MASK 0x3FFFull
#define NSEC_PER_USEC 1000ull
#define THREAD_UNINT 0
#define LCK_SLEEP_DEFAULT 0
#define THREAD_CONTINUE_NULL ((thread_continue_t)0)
#define KMA_DATA 1
#define KMA_ZERO 2
#define KMA_PERMANENT 4
#define VM_KERN_MEMORY_IOKIT 1
#define Z_WAITOK 1
#define Z_ZERO 2
#define HOST_CPU_LOAD_INFO 3
#define LCK_GRP_DECLARE(v, n) int v __attribute__((unused))
#define LCK_MTX_DECLARE(v, g) lck_mtx_t v __attribute__((unused))
#define __unused __attribute__((unused))
#define relaxed 0
#define os_atomic_add(p, v, o) ((void)(o), (void)__atomic_fetch_add((p), (v), __ATOMIC_RELAXED))
#define os_atomic_xchg(p, v, o) ((void)(o), __atomic_exchange_n((p), (v), __ATOMIC_RELAXED))
#define os_atomic_max(p, v, o) do { (void)(o); if (*(p) < (v)) { *(p) = (v); } } while (0)
#define PAGE_SHIFT 14
#define PROCESSOR_NULL ((processor_t)0)
#define CPU_STATE_USER 0
#define CPU_STATE_SYSTEM 1
#define CPU_STATE_IDLE 2
#define CPU_STATE_NICE 3
#define CPU_STATE_MAX 4
#define HOST_CPU_LOAD_INFO_COUNT 4
typedef struct { natural_t cpu_ticks[CPU_STATE_MAX]; } host_cpu_load_info_data_t;
int cpu_number(void);
#define printf sm_host_printf
int sm_host_printf(const char *fmt, ...);
/* harness-implemented */
void clock_get_uptime(uint64_t *t);
void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns);
void clock_interval_to_deadline(uint32_t interval, uint32_t scale, uint64_t *dl);
void delay(uint32_t us);
int lck_mtx_sleep_deadline(lck_mtx_t *l, int act, event_t ev, int intr, uint64_t dl);
void lck_mtx_lock(lck_mtx_t *l);
void lck_mtx_unlock(lck_mtx_t *l);
void thread_wakeup(event_t ev);
int PE_parse_boot_argn(const char *name, void *p, int sz);
int sweet_gpu_precheck(volatile uint32_t *g, int s2mpu_open, const char **why);
int sweet_gpu_power_on(volatile uint32_t *g);
/* never reached by the tests: bring-up, memory probe, selftest and perf-tick code that sweet_mali.c also contains (stubs return failure / no-ops) */
uint64_t thread_get_runtime_self(void);
kern_return_t kmem_alloc_contig(void *map, vm_offset_t *va, vm_size_t sz, vm_offset_t mask, int a, int b, int flags, int tag);
extern void *kernel_map;
kern_return_t kernel_thread_start(void (*fn)(void *, wait_result_t), void *arg, thread_t *th);
void thread_deallocate(thread_t th);
void thread_block(thread_continue_t c);
void *kalloc_data(size_t n, int flags);
void kfree_data(void *p, size_t n);
int proc_iterate(int flags, int (*cb)(proc_t, void *), void *arg, void *a, void *b);
int proc_pid(proc_t p);
task_t proc_task(proc_t p);
#endif
