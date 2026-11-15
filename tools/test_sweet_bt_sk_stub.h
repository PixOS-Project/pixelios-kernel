/* Host stubs of the kernel/Skywalk API used by bsd/dev/sweet_bt_sk.c (patch 0286), for tools/test_sweet_bt_sk.c. Modelled on the real semantics, cited per item:
 * slot walk = bsd/skywalk/channel/channel_kern.c:103-190 (KRING_EMPTY_TX / KRING_FULL_RX, advance), slot KPIs only inside a sync (VERIFY(sk_is_sync_protected()) there),
 * kpipe user rings = the same krings the provider syncs (nx_kernel_pipe.c), packet/buflet KPIs = bsd/skywalk/packet/os_packet.h. Threads: lck_mtx is a pthread mutex,
 * msleep/wakeup a condvar, so the notify-vs-pre_disconnect race of sweet_bt_sk.c can run for real. */
#ifndef SKSTUB_H
#define SKSTUB_H
#include <pthread.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>

#define SKYWALK 1
#define CONFIG_NEXUS_KERNEL_PIPE 1
#define PWAIT 32
typedef unsigned char uuid_t[16];
typedef char uuid_string_t[37];
static inline void uuid_clear(uuid_t u) { memset(u, 0, 16); }
static inline void uuid_unparse(const uuid_t u, uuid_string_t o) {
	for (int i = 0; i < 16; i++) { sprintf(o + 2 * i + (i > 3) + (i > 5) + (i > 7) + (i > 9), "%02x", u[i]); }
	o[8] = o[13] = o[18] = o[23] = '-';
}
void proc_selfname(char *, int);

/* locks */
typedef struct { pthread_mutex_t m; } lck_mtx_t;
#define LCK_GRP_DECLARE(var, name) int var __attribute__((unused))
#define LCK_MTX_DECLARE(var, grp) lck_mtx_t var = { PTHREAD_MUTEX_INITIALIZER }
static inline void lck_mtx_lock(lck_mtx_t *l) { pthread_mutex_lock(&l->m); }
static inline void lck_mtx_unlock(lck_mtx_t *l) { pthread_mutex_unlock(&l->m); }
int msleep(void *chan, lck_mtx_t *mtx, int pri, const char *wmesg, void *ts);
void wakeup(void *chan);

/* Skywalk types */
typedef int boolean_t;
typedef struct kprov *kern_nexus_provider_t;
typedef struct kproc *proc_t;
typedef struct knexus *kern_nexus_t;
typedef uint16_t nexus_port_t;
typedef struct kchan *kern_channel_t;
typedef struct kring *kern_channel_ring_t;
typedef struct kslot *kern_channel_slot_t;
typedef uint64_t kern_packet_t;
typedef struct kbuflet *kern_buflet_t;
typedef struct kpp *kern_pbufpool_t;
typedef struct kncd *nexus_controller_t;
typedef struct knxa *nexus_attr_t;
typedef int nexus_type_t;
typedef int nexus_attr_type_t;
enum { NEXUS_TYPE_KERNEL_PIPE = 1 };
enum { NEXUS_ATTR_TX_RINGS = 1, NEXUS_ATTR_RX_RINGS, NEXUS_ATTR_TX_SLOTS, NEXUS_ATTR_RX_SLOTS, NEXUS_ATTR_SLOT_BUF_SIZE, NEXUS_ATTR_ANONYMOUS, NEXUS_ATTR_OTHER };
#define KERN_NEXUS_DOMAIN_PROVIDER_CURRENT_VERSION 1
#define KERN_NEXUS_CURRENT_VERSION 1
#define NXPIF_VIRTUAL_DEVICE 0x1
struct kern_slot_prop { int unused; };
struct kern_channel_ring_stat_increment { uint32_t kcrsi_slots_transferred; uint32_t kcrsi_bytes_transferred; };
struct kern_nexus_provider_init {
	uint32_t nxpi_version, nxpi_flags;
	errno_t (*nxpi_pre_connect)(kern_nexus_provider_t, proc_t, kern_nexus_t, nexus_port_t, kern_channel_t, void **);
	errno_t (*nxpi_connected)(kern_nexus_provider_t, kern_nexus_t, kern_channel_t);
	void (*nxpi_pre_disconnect)(kern_nexus_provider_t, kern_nexus_t, kern_channel_t);
	void (*nxpi_disconnected)(kern_nexus_provider_t, kern_nexus_t, kern_channel_t);
	errno_t (*nxpi_ring_init)(kern_nexus_provider_t, kern_nexus_t, kern_channel_t, kern_channel_ring_t, boolean_t, void **);
	void (*nxpi_ring_fini)(kern_nexus_provider_t, kern_nexus_t, kern_channel_ring_t);
	void *nxpi_slot_init, *nxpi_slot_fini;
	errno_t (*nxpi_sync_tx)(kern_nexus_provider_t, kern_nexus_t, kern_channel_ring_t, uint32_t);
	errno_t (*nxpi_sync_rx)(kern_nexus_provider_t, kern_nexus_t, kern_channel_ring_t, uint32_t);
	void *nxpi_tx_doorbell;
};
struct kern_nexus_init { uint32_t nxi_version, nxi_flags; kern_pbufpool_t nxi_tx_pbufpool, nxi_rx_pbufpool; };

errno_t kern_nexus_controller_create(nexus_controller_t *);
errno_t kern_nexus_get_default_domain_provider(nexus_type_t, uuid_t *);
errno_t kern_nexus_attr_create(nexus_attr_t *);
errno_t kern_nexus_attr_set(nexus_attr_t, nexus_attr_type_t, uint64_t);
void kern_nexus_attr_destroy(nexus_attr_t);
errno_t kern_nexus_controller_register_provider(nexus_controller_t, const uuid_t, const uint8_t *, const struct kern_nexus_provider_init *, uint32_t, nexus_attr_t, uuid_t *);
errno_t kern_nexus_controller_deregister_provider(nexus_controller_t, const uuid_t);
errno_t kern_nexus_controller_alloc_provider_instance(nexus_controller_t, const uuid_t, const void *, void *, uuid_t *, const struct kern_nexus_init *);
void kern_nexus_controller_destroy(nexus_controller_t);
errno_t kern_nexus_get_pbufpool(const kern_nexus_t, kern_pbufpool_t *, kern_pbufpool_t *);
uint32_t kern_channel_notify(const kern_channel_ring_t, uint32_t);
uint32_t kern_channel_reclaim(const kern_channel_ring_t);
kern_channel_slot_t kern_channel_get_next_slot(const kern_channel_ring_t, const kern_channel_slot_t, struct kern_slot_prop *);
void kern_channel_advance_slot(const kern_channel_ring_t, kern_channel_slot_t);
void kern_channel_increment_ring_stats(kern_channel_ring_t, struct kern_channel_ring_stat_increment *);
kern_packet_t kern_channel_slot_get_packet(const kern_channel_ring_t, const kern_channel_slot_t);
errno_t kern_channel_slot_attach_packet(const kern_channel_ring_t, const kern_channel_slot_t, kern_packet_t);
kern_buflet_t kern_packet_get_next_buflet(const kern_packet_t, const kern_buflet_t);
uint8_t *kern_buflet_get_data_address(const kern_buflet_t);
uint16_t kern_buflet_get_data_length(const kern_buflet_t);
uint16_t kern_buflet_get_data_offset(const kern_buflet_t);
errno_t kern_buflet_set_data_length(const kern_buflet_t, uint16_t);
errno_t kern_buflet_set_data_offset(const kern_buflet_t, uint16_t);
errno_t kern_packet_finalize(const kern_packet_t);
errno_t kern_pbufpool_alloc_nosleep(const kern_pbufpool_t, uint32_t, kern_packet_t *);
void kern_pbufpool_free(const kern_pbufpool_t, kern_packet_t);
#endif
