/* Host test of shim/wlan_msgbuf.h (docs/research/53 section 7 step 3, section 5.2 G1): a fake dongle plays the firmware side of the
 * msgbuf ring protocol over plain host memory. The fake dongle is written from the cited DHD source and uses its OWN byte helpers and its
 * OWN epoch/checksum/phase bookkeeping (not the library's), so a wrong library constant or rule fails here instead of agreeing with itself.
 * Layout constants are checked against mirrored C structs of the cited declarations with offsetof(). No hardware, no firmware, no file. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

static unsigned gap_lines;   /* WM_GAP: rule 8, every refusal prints one line; the test counts them */
#define WM_GAP(line) do { gap_lines++; fprintf(stderr, "%s\n", (line)); } while (0)
#include "wlan_msgbuf.h"

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

/* ---- layout: mirrors of the declarations in R5 bcmpcie.h:290-433 and wlioctl.h.new:404-455 (natural alignment) ---- */
typedef struct { uint32_t low_addr, high_addr; } m_sh_addr_t;
typedef struct {
	uint32_t flags, trap_addr, assert_exp_addr, assert_file_addr, assert_line, console_addr, msgtrace_addr, fwid;
	uint16_t total_lfrag_pkt_cnt, max_host_rxbufs;
	uint32_t dma_rxoffset, h2d_mb_data_ptr, d2h_mb_data_ptr, rings_info_ptr, host_dma_scratch_buffer_len;
	m_sh_addr_t host_dma_scratch_buffer, host_scb_addr;
	uint32_t host_scb_size, buzz_dbg_ptr, flags2, host_cap;
	m_sh_addr_t host_trap_addr;
	uint32_t device_fatal_logbuf_start;
	m_sh_addr_t hoffload_addr;
	uint32_t flags3, host_cap2, host_cap3, etd_addr, device_txpost_ext_tags_bitmask, ewp_info_addr;
	uint8_t aggr[8];
} m_shared;
typedef struct {
	uint32_t ringmem_ptr, h2d_w_idx_ptr, h2d_r_idx_ptr, d2h_w_idx_ptr, d2h_r_idx_ptr;
	m_sh_addr_t h2d_w_idx_hostaddr, h2d_r_idx_hostaddr, d2h_w_idx_hostaddr, d2h_r_idx_hostaddr;
	uint16_t max_tx_flowrings, max_submission_queues, max_completion_rings, max_vdevs;
	m_sh_addr_t ifrm_w_idx_hostaddr;
	uint32_t h2d_hwa_db_idx_ptr, d2h_hwa_db_idx_ptr;
} m_ring_info;
typedef struct {
	uint16_t idx; uint8_t type, rsvd; uint16_t max_item, len_items; m_sh_addr_t base_addr;
} m_ring_mem;
typedef struct {
	uint32_t version, length;
	uint8_t BSSID[6];
	uint16_t beacon_period, capability;
	uint8_t SSID_len, SSID[32], bcnflags;
	struct { uint32_t count; uint8_t rates[16]; } rateset;
	uint16_t chanspec, atim_window;
	uint8_t dtim_period, accessnet;
	int16_t RSSI;
	int8_t phy_noise;
	uint8_t n_cap, he_cap, load;
	uint32_t nbss_cap;
	uint8_t ctl_ch, PAD[3];
	uint16_t vht_rxmcsmap, vht_txmcsmap;
	uint8_t flags, vht_cap, flags2, reserved, basic_mcs[16];
	uint16_t ie_offset, freespace2;
	uint32_t ie_length;
	int16_t SNR;
	uint16_t vht_mcsmap, vht_mcsmap_prop, vht_txmcsmap_prop;
	uint32_t he_mcsmap, he_rxmcsmap, he_txmcsmap;
} m_bss;
typedef struct { uint8_t msg_type, if_id, flags, epoch; uint32_t request_id; } m_cmn;
typedef struct {
	m_cmn h; uint32_t cmd; uint16_t trans_id, input_buf_len, output_buf_len, rsvd[3]; uint32_t lo, hi; uint32_t rsvd1[2];
} m_ioctl_req;
typedef struct {
	m_cmn h; uint16_t host_buf_len, reserved[3]; uint32_t lo, hi; uint32_t rsvd[4];
} m_buf_post;
typedef struct { m_cmn h; int16_t status; uint16_t ring_id; uint16_t resp_len, trans_id; uint32_t cmd, marker; } m_ioctl_cmplt;
typedef struct { m_cmn h; int16_t status; uint16_t ring_id; uint32_t cmd, rsvd, marker; } m_ioctl_ack;
typedef struct { m_cmn h; int16_t status; uint16_t ring_id; uint16_t event_data_len, seqnum; uint32_t rsvd, marker; } m_wlevent;
typedef struct {
	uint16_t version, flags; uint32_t event_type, status, reason, auth_type, datalen; uint8_t addr[6]; char ifname[16]; uint8_t ifidx, bsscfgidx;
} __attribute__((packed)) m_evmsg;

#define OFF(t, f, v) _Static_assert(offsetof(t, f) == (v), #t "." #f)
OFF(m_shared, flags, WM_SH_FLAGS); OFF(m_shared, trap_addr, WM_SH_TRAP_ADDR); OFF(m_shared, console_addr, WM_SH_CONSOLE_ADDR);
OFF(m_shared, max_host_rxbufs, WM_SH_MAX_HOST_RXBUFS); OFF(m_shared, dma_rxoffset, WM_SH_DMA_RXOFFSET);
OFF(m_shared, h2d_mb_data_ptr, WM_SH_H2D_MB_DATA_PTR); OFF(m_shared, d2h_mb_data_ptr, WM_SH_D2H_MB_DATA_PTR);
OFF(m_shared, rings_info_ptr, WM_SH_RINGS_INFO_PTR); OFF(m_shared, host_dma_scratch_buffer_len, WM_SH_SCRATCH_LEN);
OFF(m_shared, host_dma_scratch_buffer, WM_SH_SCRATCH_ADDR); OFF(m_shared, host_scb_addr, WM_SH_HOST_SCB_ADDR);
OFF(m_shared, host_scb_size, WM_SH_HOST_SCB_SIZE); OFF(m_shared, flags2, WM_SH_FLAGS2); OFF(m_shared, host_cap, WM_SH_HOST_CAP);
OFF(m_shared, host_trap_addr, WM_SH_HOST_TRAP_ADDR); OFF(m_shared, host_cap2, WM_SH_HOST_CAP2);
_Static_assert(sizeof(m_shared) == WM_SH_LEN_V9, "shared len");
OFF(m_ring_info, ringmem_ptr, WM_RI_RINGMEM_PTR); OFF(m_ring_info, h2d_w_idx_ptr, WM_RI_H2D_W_IDX_PTR);
OFF(m_ring_info, h2d_r_idx_ptr, WM_RI_H2D_R_IDX_PTR); OFF(m_ring_info, d2h_w_idx_ptr, WM_RI_D2H_W_IDX_PTR);
OFF(m_ring_info, d2h_r_idx_ptr, WM_RI_D2H_R_IDX_PTR); OFF(m_ring_info, h2d_w_idx_hostaddr, WM_RI_H2D_W_IDX_HOSTADDR);
OFF(m_ring_info, h2d_r_idx_hostaddr, WM_RI_H2D_R_IDX_HOSTADDR); OFF(m_ring_info, d2h_w_idx_hostaddr, WM_RI_D2H_W_IDX_HOSTADDR);
OFF(m_ring_info, d2h_r_idx_hostaddr, WM_RI_D2H_R_IDX_HOSTADDR); OFF(m_ring_info, max_tx_flowrings, WM_RI_MAX_TX_FLOWRINGS);
OFF(m_ring_info, max_submission_queues, WM_RI_MAX_SUBMISSION_QUEUES); OFF(m_ring_info, max_completion_rings, WM_RI_MAX_COMPLETION_RINGS);
OFF(m_ring_info, max_vdevs, WM_RI_MAX_VDEVS);
_Static_assert(sizeof(m_ring_info) == WM_RI_LEN, "ring_info len");
OFF(m_ring_mem, idx, WM_RM_IDX); OFF(m_ring_mem, type, 2); OFF(m_ring_mem, max_item, WM_RM_MAX_ITEM);
OFF(m_ring_mem, len_items, WM_RM_LEN_ITEMS); OFF(m_ring_mem, base_addr, WM_RM_BASE_ADDR);
_Static_assert(sizeof(m_ring_mem) == WM_RM_SIZE, "ring_mem size");
OFF(m_bss, version, WM_BSS_VERSION); OFF(m_bss, length, WM_BSS_LENGTH); OFF(m_bss, BSSID, WM_BSS_BSSID);
OFF(m_bss, SSID_len, WM_BSS_SSID_LEN); OFF(m_bss, SSID, WM_BSS_SSID); OFF(m_bss, chanspec, WM_BSS_CHANSPEC);
OFF(m_bss, RSSI, WM_BSS_RSSI); OFF(m_bss, ctl_ch, WM_BSS_CTL_CH);
_Static_assert(WM_BSS_MIN_LEN == WM_BSS_CTL_CH + 1, "min len covers the last field read");
_Static_assert(WM_BSS_SSID_MAX == sizeof(((m_bss *)0)->SSID), "ssid max");
_Static_assert(sizeof(m_bss) == 144, "put_bss assumes 144 bytes");
_Static_assert(sizeof(m_cmn) == WM_HDR_LEN, "cmn hdr");
_Static_assert(sizeof(m_ioctl_req) == WM_H2D_CTRL_ITEM && sizeof(m_buf_post) == WM_H2D_CTRL_ITEM, "h2d ctrl item");
_Static_assert(sizeof(m_ioctl_cmplt) == WM_D2H_CTRL_ITEM && sizeof(m_ioctl_ack) == WM_D2H_CTRL_ITEM && sizeof(m_wlevent) == WM_D2H_CTRL_ITEM, "d2h ctrl item");
OFF(m_ioctl_req, cmd, 8); OFF(m_ioctl_req, trans_id, 12); OFF(m_ioctl_req, input_buf_len, 14); OFF(m_ioctl_req, output_buf_len, 16);
OFF(m_ioctl_req, lo, 24); OFF(m_ioctl_req, hi, 28);
OFF(m_buf_post, host_buf_len, 8); OFF(m_buf_post, lo, 16); OFF(m_buf_post, hi, 20);
OFF(m_ioctl_cmplt, status, 8); OFF(m_ioctl_cmplt, ring_id, 10); OFF(m_ioctl_cmplt, resp_len, 12); OFF(m_ioctl_cmplt, trans_id, 14);
OFF(m_ioctl_cmplt, cmd, 16); OFF(m_ioctl_cmplt, marker, 20);
OFF(m_ioctl_ack, cmd, 12); OFF(m_ioctl_ack, marker, 20);
OFF(m_wlevent, event_data_len, 12); OFF(m_wlevent, seqnum, 14); OFF(m_wlevent, marker, 20);
_Static_assert(sizeof(m_evmsg) == WM_EVMSG_LEN, "evmsg len");
OFF(m_evmsg, event_type, WM_EV_TYPE); OFF(m_evmsg, status, WM_EV_STATUS); OFF(m_evmsg, reason, WM_EV_REASON);
OFF(m_evmsg, datalen, WM_EV_DATALEN); OFF(m_evmsg, addr, WM_EV_ADDR); OFF(m_evmsg, ifname, WM_EV_IFNAME);
OFF(m_evmsg, ifidx, WM_EV_IFIDX); OFF(m_evmsg, bsscfgidx, WM_EV_BSSCFGIDX);

/* ---- the fake dongle's own helpers (independent of the library's) ---- */
static void d16(uint8_t *p, unsigned v) { p[0] = v & 0xff; p[1] = (v >> 8) & 0xff; }
static void d32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (v >> (8 * i)) & 0xff; }
static void db32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (v >> (24 - 8 * i)) & 0xff; }
static unsigned r16(const uint8_t *p) { return p[0] | (p[1] << 8); }
static uint32_t r32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

/* "host memory" the dongle can DMA: one arena; a DMA address is ARENA_PA + offset (bit 35 set like the UFS bounce buffer, 53 s3.1) */
#define ARENA_PA 0x880100000ull
static uint8_t arena[1 << 16];
static uint64_t pa_of(const void *p) { return ARENA_PA + (uint64_t)((const uint8_t *)p - arena); }
static uint8_t *va_of(uint64_t pa) { return arena + (pa - ARENA_PA); }

#define H2D_MAX 4
#define D2H_MAX 5
#define RESP_BUF 8192u
#define EVT_BUF 2048u
#define RXOFF 8u

struct dongle {
	/* H2D ctrl ring as the dongle sees it */
	uint8_t *h2d_ring; unsigned h2d_rd; unsigned h2d_items_seen; unsigned exp_epoch;
	/* D2H ctrl ring */
	uint8_t *d2h_ring; unsigned d2h_wr; unsigned d2h_epoch;
	/* index arrays (host memory, 4-byte slots) */
	uint8_t *h2d_w, *h2d_r, *d2h_w, *d2h_r;
	/* buffers the host posted */
	uint64_t resp_pa[16]; unsigned resp_n, resp_len[16]; uint32_t resp_id[16];
	uint64_t evt_pa[64]; unsigned evt_n, evt_len[64]; uint32_t evt_id[64];
	/* counters / observations */
	unsigned n_ioctl, n_post_resp, n_post_evt, protocol_errors;
	uint32_t last_cmd; unsigned last_trans, last_in, last_out;
	/* fault injection for the next D2H item */
	int bad_epoch, bad_csum;
};

static int quiet;
static void dg_err(struct dongle *d, const char *m, int line)
{
	if (!quiet)
		fprintf(stderr, "dongle protocol error line %d: %s\n", line, m);
	d->protocol_errors++;
}
#define DERR(d, m) dg_err(d, m, __LINE__)

/* one D2H control item: epoch = own counter 1,2..252,0,1.. ; last word = XOR of the others */
static void d2h_push(struct dongle *d, const uint8_t *body24)
{
	uint8_t *it = d->d2h_ring + (size_t)d->d2h_wr * WM_D2H_CTRL_ITEM;
	uint32_t x = 0;
	memcpy(it, body24, 24);
	it[3] = (uint8_t)(d->bad_epoch ? (d->d2h_epoch + 7) % 253 : d->d2h_epoch);
	d32(it + 20, 0);
	for (int i = 0; i < 20; i += 4) x ^= r32(it + i);
	d32(it + 20, d->bad_csum ? x ^ 0x100u : x);
	d->bad_epoch = d->bad_csum = 0;
	d->d2h_epoch = (d->d2h_epoch + 1) % 253;
	d->d2h_wr = (d->d2h_wr + 1) % D2H_MAX;
	d16(d->d2h_w, d->d2h_wr); /* slot 0 = D2H common ring 2 (ring id - 2) */
}

static void dg_event(struct dongle *d, const uint8_t *frame, unsigned flen)
{
	uint8_t it[24];
	if (!d->evt_n) { DERR(d, "no event buffer posted"); return; }
	d->evt_n--; /* take the oldest: shift */
	uint64_t pa = d->evt_pa[0]; uint32_t id = d->evt_id[0]; unsigned bl = d->evt_len[0];
	for (unsigned i = 0; i < d->evt_n; i++) { d->evt_pa[i] = d->evt_pa[i + 1]; d->evt_id[i] = d->evt_id[i + 1]; d->evt_len[i] = d->evt_len[i + 1]; }
	if (flen + RXOFF > bl) { DERR(d, "event larger than buffer"); return; }
	memset(va_of(pa), 0xEE, RXOFF);
	memcpy(va_of(pa) + RXOFF, frame, flen);
	memset(it, 0, sizeof it);
	it[0] = 0x0E; it[1] = 0; d32(it + 4, id); d16(it + 8, 0); d16(it + 10, 2); d16(it + 12, flen); d16(it + 14, 77);
	d2h_push(d, it);
}

/* the dongle drains the H2D control ring up to the write index it reads from the host's array */
static void dg_service(struct dongle *d)
{
	unsigned wr = r16(d->h2d_w); /* slot 0 = H2D ring 0 */
	while (d->h2d_rd != wr) {
		const uint8_t *it = d->h2d_ring + (size_t)d->h2d_rd * WM_H2D_CTRL_ITEM;
		unsigned pass = d->h2d_items_seen / H2D_MAX;
		unsigned want_phase = (pass % 2 == 0) ? 0x80 : 0x00; /* flips each pass, first pass 0x80 (DHD toggles when slot 0 is handed out) */
		if (it[3] != d->exp_epoch) DERR(d, "H2D epoch");
		if (it[2] != want_phase) DERR(d, "H2D phase flag");
		d->exp_epoch = (d->exp_epoch + 1) % 253;
		d->h2d_items_seen++;
		switch (it[0]) {
		case 0x0B:
		case 0x0D: {
			uint64_t pa = (uint64_t)r32(it + 16) | ((uint64_t)r32(it + 20) << 32);
			unsigned bl = r16(it + 8);
			uint32_t id = r32(it + 4);
			if (it[1] != 0 || !id || id == 0xFFFE || !bl || (pa & 3) || pa < ARENA_PA) DERR(d, "buf post fields");
			for (int k = 10; k < 16; k++) if (it[k]) DERR(d, "buf post reserved not zero");
			for (int k = 24; k < 40; k++) if (it[k]) DERR(d, "buf post rsvd not zero");
			if (it[0] == 0x0B) { d->resp_pa[d->resp_n] = pa; d->resp_len[d->resp_n] = bl; d->resp_id[d->resp_n++] = id; d->n_post_resp++; }
			else { d->evt_pa[d->evt_n] = pa; d->evt_len[d->evt_n] = bl; d->evt_id[d->evt_n++] = id; d->n_post_evt++; }
			break;
		}
		case 0x09: {
			uint64_t pa = (uint64_t)r32(it + 24) | ((uint64_t)r32(it + 28) << 32);
			uint8_t a[24] = {0}, c[24] = {0};
			d->n_ioctl++;
			d->last_cmd = r32(it + 8); d->last_trans = r16(it + 12); d->last_in = r16(it + 14); d->last_out = r16(it + 16);
			if (r32(it + 4) != 0xFFFE) DERR(d, "ioctl request_id");
			for (int k = 18; k < 24; k++) if (it[k]) DERR(d, "ioctl rsvd");
			for (int k = 32; k < 40; k++) if (it[k]) DERR(d, "ioctl rsvd1");
			if (pa < ARENA_PA) DERR(d, "ioctl input pa");
			/* ack (0xA) then complete (0xC) into the oldest posted response buffer */
			a[0] = 0x0A; d16(a + 10, 0); d32(a + 12, d->last_cmd);
			d2h_push(d, a);
			if (!d->resp_n) { c[0] = 0x0C; d16(c + 8, (unsigned)(int16_t)-9 & 0xffff); d16(c + 12, 0); d16(c + 14, d->last_trans); d32(c + 16, d->last_cmd); d2h_push(d, c); break; }
			{
				uint64_t rpa = d->resp_pa[0]; uint32_t rid = d->resp_id[0];
				d->resp_n--;
				for (unsigned i = 0; i < d->resp_n; i++) { d->resp_pa[i] = d->resp_pa[i + 1]; d->resp_id[i] = d->resp_id[i + 1]; d->resp_len[i] = d->resp_len[i + 1]; }
				if (d->last_cmd == 1) { /* WLC_GET_VERSION: a 4-byte int 0x00000030 in the response buffer, from its start */
					d32(va_of(rpa), 0x30);
					c[0] = 0x0C; d32(c + 4, rid); d16(c + 8, 0); d16(c + 10, 0); d16(c + 12, 4); d16(c + 14, d->last_trans); d32(c + 16, d->last_cmd);
				}
				d2h_push(d, c);
			}
			break;
		}
		default: DERR(d, "unknown H2D msg_type");
		}
		d->h2d_rd = (d->h2d_rd + 1) % H2D_MAX;
		d16(d->h2d_r, d->h2d_rd);
		wr = r16(d->h2d_w);
	}
}

/* ---- escan event frame, built big-endian where DHD says so ---- */
static unsigned put_bss(uint8_t *p, unsigned ielen, const uint8_t bssid[6], const char *ssid, unsigned ch, int rssi)
{
	unsigned len = 144 + ielen; /* sizeof(m_bss) + IEs */
	memset(p, 0, len);
	d32(p + 0, 109); d32(p + 4, len);
	memcpy(p + 8, bssid, 6);
	p[18] = (uint8_t)strlen(ssid); memcpy(p + 19, ssid, strlen(ssid));
	d16(p + 72, 0x1000 | ch); d16(p + 78, (unsigned)rssi & 0xffff); p[88] = (uint8_t)ch;
	d16(p + 116, 144); d32(p + 120, ielen);
	return len;
}
static unsigned put_event(uint8_t *f, unsigned type, unsigned status, const uint8_t *data, unsigned dlen)
{
	memset(f, 0, 72);
	f[12] = 0x88; f[13] = 0x6c;                            /* ethertype 0x886c */
	f[14] = 0x80; f[15] = 0x01; f[16] = 0; f[17] = 0x30;   /* bcmeth subtype, length (BE) */
	f[18] = 0;                                             /* version */
	f[19] = 0x00; f[20] = 0x10; f[21] = 0x18;              /* OUI */
	f[22] = 0; f[23] = 1;                                  /* usr_subtype 1 = event (BE) */
	f[24] = 0; f[25] = 2;                                  /* wl_event_msg version 2 (BE) */
	db32(f + 28, type); db32(f + 32, status); db32(f + 44, dlen);
	f[24 + 46] = 0; f[24 + 47] = 0;
	memcpy(f + 72, data, dlen);
	return 72 + dlen;
}

/* ---- host-side fixture: rings + index arrays + dongle, all in the arena ---- */
struct host {
	wm_ring h2d, d2h;
	struct dongle d;
	uint32_t sync; /* shared flags */
};
static void host_init(struct host *h)
{
	memset(arena, 0, sizeof arena);
	memset(h, 0, sizeof *h);
	h->sync = 0x70050107u;
	CHECK(wm_ring_init(&h->h2d, arena + 0x0000, WM_RING_H2D_CTRL, H2D_MAX, WM_H2D_CTRL_ITEM, 0) == WM_OK);
	CHECK(wm_ring_init(&h->d2h, arena + 0x1000, WM_RING_D2H_CTRL, D2H_MAX, WM_D2H_CTRL_ITEM, 1) == WM_OK);
	h->d.h2d_ring = h->h2d.base; h->d.d2h_ring = h->d2h.base;
	h->d.h2d_w = arena + 0x2000; h->d.h2d_r = arena + 0x2100; h->d.d2h_w = arena + 0x2200; h->d.d2h_r = arena + 0x2300;
	h->d.exp_epoch = 1; h->d.d2h_epoch = 1;
}
#define H2D_W(h) ((volatile uint8_t *)(h)->d.h2d_w)
#define H2D_R(h) ((volatile uint8_t *)(h)->d.h2d_r)
#define D2H_W(h) ((volatile uint8_t *)(h)->d.d2h_w)
#define D2H_R(h) ((volatile uint8_t *)(h)->d.d2h_r)

/* host: reserve + fill + publish (index store, then doorbell value returned); the dongle then services */
static int host_post_buf(struct host *h, uint8_t type, uint32_t id, uint8_t *buf, unsigned len)
{
	uint8_t *it; int r;
	r = wm_h2d_reserve(&h->h2d, wm_idx_get(H2D_R(h), WM_H2D_SLOT(WM_RING_H2D_CTRL)), &it);
	if (r) return r;
	r = wm_put_buf_post(it, type, id, len, pa_of(buf));
	if (r) return r;
	wm_idx_set(H2D_W(h), WM_H2D_SLOT(WM_RING_H2D_CTRL), h->h2d.wr);
	CHECK(wm_db0_value(&h->h2d) == (0xFF000000u | h->h2d.wr));
	dg_service(&h->d);
	return 0;
}
static int host_next(struct host *h, const uint8_t **it)
{
	return wm_d2h_peek(&h->d2h, wm_idx_get(D2H_W(h), WM_D2H_SLOT(WM_RING_D2H_CTRL)), h->sync, it);
}
static void host_pop(struct host *h)
{
	wm_idx_set(D2H_R(h), WM_D2H_SLOT(WM_RING_D2H_CTRL), wm_d2h_pop(&h->d2h));
}

int main(void)
{
	/* ---- 1. constants against the cited decodes ---- */
	CHECK((0x70050107u & WM_SH_VERSION_MASK) == 7);
	CHECK(0x70050107u & WM_SH_D2H_SYNC_XORCSUM);
	CHECK(0x70050107u & WM_SH_DMA_INDEX);
	CHECK(0x70050107u & WM_SH_HOSTRDY_SUPPORT);
	CHECK(!(0x70050107u & WM_SH_2BYTE_INDICES));
	CHECK(!(0x70050107u & WM_SH_DAR));
	CHECK(!(0x70050107u & (WM_SH_ASSERT | WM_SH_TRAP)));
	CHECK(WM_HOSTREADY_DB_VALUE == 0x12345678u && WM_REG_H2D_DB0 == 0x140 && WM_REG_H2D_DB1 == 0x144);
	CHECK(WM_D2H_SLOT(WM_RING_D2H_CTRL) == 0 && WM_D2H_SLOT(WM_RING_D2H_TX) == 1 && WM_D2H_SLOT(WM_RING_D2H_RX) == 2);
	CHECK(WM_EV_HDR_LEN == 72);

	/* ---- 2. doorbell value shape ---- */
	{
		wm_ring r;
		uint8_t b[64];
		CHECK(wm_ring_init(&r, b, 0, 4, 40, 0) == WM_OK);
		r.wr = 3;
		CHECK(wm_db0_value(&r) == 0xFF000003u);
		CHECK(wm_ring_init(&r, b, 4, 8, 24, 1) == WM_OK);
		r.wr = 0x12;
		CHECK(wm_db0_value(&r) == 0xFF040012u);
		CHECK(wm_ring_init(&r, b, 0x100, 4, 40, 0) == WM_E_ARG);   /* ring id does not fit the 0xFF00xxxx shape */
		CHECK(wm_ring_init(&r, b, 0, 1, 40, 0) == WM_E_ARG);       /* a 1-item ring can never hold an item */
		CHECK(wm_ring_init(&r, b, 0, 4, 42, 0) == WM_E_ARG);       /* item length not a multiple of 4 */
	}

	/* ---- 3. H2D index wrap, full detection, phase flip, epoch over >253 items ---- */
	{
		struct host h;
		uint8_t *pbuf = arena + 0xE000;
		host_init(&h);
		/* 3 items fit (4-slot ring keeps one empty); the 4th must be refused while the dongle has not read */
		uint8_t *it;
		for (int i = 0; i < 3; i++) {
			CHECK(wm_h2d_reserve(&h.h2d, 0, &it) == WM_OK);
			CHECK(it[3] == (uint8_t)(i + 1) && it[2] == 0x80);   /* first epoch 1, first-pass phase bit set */
		}
		CHECK(h.h2d.wr == 3);
		CHECK(wm_h2d_reserve(&h.h2d, 0, &it) == WM_E_FULL);
		CHECK(h.h2d.wr == 3 && h.h2d.seqnum == 254u + 3u);        /* a refusal changes nothing */
		CHECK(wm_h2d_reserve(&h.h2d, 4, &it) == WM_E_BADIDX);      /* rd index from the dongle out of range */
		CHECK(wm_h2d_reserve(&h.h2d, 0xFFFF, &it) == WM_E_BADIDX);
		CHECK(wm_h2d_reserve(&h.h2d, 1, &it) == WM_OK);            /* dongle read one: slot 3 is free, wr wraps to 0 */
		CHECK(h.h2d.wr == 0 && it[3] == 4);
		CHECK(wm_h2d_reserve(&h.h2d, 1, &it) == WM_E_FULL);        /* next(0)=1 == rd */
		CHECK(wm_db0_value(&h.h2d) == 0xFF000000u);
		/* long run through the real fake dongle: 700 buffer posts, ring of 4, epochs wrap 252->0->1 several times; the dongle checks epoch and phase of every one */
		host_init(&h);
		for (unsigned i = 0; i < 700; i++) {
			int r = host_post_buf(&h, (i & 1) ? WM_MSG_EVENT_BUF_POST : WM_MSG_IOCTLRESP_BUF_POST, 1000 + i, pbuf, 64);
			CHECK(r == 0);
			if (r) break;
			h.d.resp_n = h.d.evt_n = 0;                            /* the fake keeps tables small */
		}
		CHECK(h.d.protocol_errors == 0);
		CHECK(h.d.n_post_resp + h.d.n_post_evt == 700);
		CHECK(h.d.h2d_rd == 700 % H2D_MAX);
		/* if the dongle stops reading, the host sees FULL at exactly 3 pending */
		host_init(&h);
		for (int i = 0; i < 3; i++) CHECK(host_post_buf(&h, WM_MSG_EVENT_BUF_POST, 5 + i, pbuf, 64) == 0);
		CHECK(h.d.h2d_rd == 3);   /* dongle drained them, so the ring is empty again: no false FULL */
		CHECK(wm_idx_get(H2D_R(&h), 0) == 3);
	}

	/* ---- 4. D2H validation: good items, wrap, bad epoch, bad checksum, empty, bad index; ring not advanced on failure ---- */
	{
		struct host h;
		const uint8_t *it;
		uint8_t body[24] = {0};
		host_init(&h);
		CHECK(host_next(&h, &it) == WM_E_EMPTY);
		/* 600 good items through a 5-slot ring: epochs 1..252,0,1.. ; producer one ahead of consumer */
		for (unsigned i = 0; i < 600; i++) {
			body[0] = 0x0A; d32(body + 12, i);
			d2h_push(&h.d, body);
			CHECK(host_next(&h, &it) == WM_OK);
			CHECK(it[0] == 0x0A && r32(it + 12) == i);
			host_pop(&h);
		}
		CHECK(h.d2h.rd == 600 % D2H_MAX && wm_idx_get(D2H_R(&h), 0) == 600 % D2H_MAX);
		CHECK(host_next(&h, &it) == WM_E_EMPTY);
		/* bad epoch: refused, nothing advanced, stays refused */
		h.d.bad_epoch = 1; d2h_push(&h.d, body);
		CHECK(host_next(&h, &it) == WM_E_EPOCH);
		CHECK(host_next(&h, &it) == WM_E_EPOCH);
		CHECK(h.d2h.rd == 600 % D2H_MAX && h.d2h.seqnum == 254u + 600u);
		/* drop that item for the next case */
		host_init(&h);
		h.d.bad_csum = 1; d2h_push(&h.d, body);
		CHECK(host_next(&h, &it) == WM_E_CSUM);
		CHECK(h.d2h.rd == 0);
		/* a single flipped payload bit with a once-valid epoch */
		host_init(&h);
		d2h_push(&h.d, body);
		h.d2h.base[9] ^= 0x01;
		CHECK(host_next(&h, &it) == WM_E_CSUM);
		/* the same item passes when the dongle does not advertise XORCSUM (no-sync mode) and is refused for SEQNUM-only */
		h.sync = 0x70050107u & ~WM_SH_D2H_SYNC_XORCSUM;
		CHECK(host_next(&h, &it) == WM_OK);
		h.sync = (0x70050107u & ~WM_SH_D2H_SYNC_XORCSUM) | WM_SH_D2H_SYNC_SEQNUM;
		CHECK(host_next(&h, &it) == WM_E_SYNC);
		/* write index from the dongle out of range, and a corrupt read index in our own state */
		h.sync = 0x70050107u;
		CHECK(wm_d2h_peek(&h.d2h, D2H_MAX, h.sync, &it) == WM_E_BADIDX);
		CHECK(wm_d2h_peek(&h.d2h, 0xFFFF, h.sync, &it) == WM_E_BADIDX);
		CHECK(wm_d2h_peek(&h.h2d, 1, h.sync, &it) == WM_E_ARG);    /* wrong direction */
		/* an all-zero item (never DMAed) is not accepted: epoch 0 != 1 */
		host_init(&h);
		d16(h.d.d2h_w, 1);
		CHECK(host_next(&h, &it) == WM_E_EPOCH);
	}

	/* ---- 5. ioctl round trip: GET_VERSION, with posted response + event buffers ---- */
	{
		struct host h;
		static uint8_t *respbuf, *evtbuf, *inbuf;
		const uint8_t *it;
		wm_cmplt c;
		uint8_t *slot;
		uint32_t at;
		host_init(&h);
		respbuf = arena + 0x4000; evtbuf = arena + 0x8000; inbuf = arena + 0xC000;
		memset(respbuf, 0xCC, RESP_BUF);
		for (unsigned i = 0; i < 2; i++) CHECK(host_post_buf(&h, WM_MSG_IOCTLRESP_BUF_POST, 100 + i, respbuf + i * 0, RESP_BUF) == 0);
		CHECK(host_post_buf(&h, WM_MSG_EVENT_BUF_POST, 200, evtbuf, EVT_BUF) == 0);
		CHECK(h.d.resp_n == 2 && h.d.evt_n == 1 && h.d.protocol_errors == 0);
		/* request: cmd 1, 4-byte input buffer, 4-byte output expected */
		CHECK(wm_h2d_reserve(&h.h2d, wm_idx_get(H2D_R(&h), 0), &slot) == WM_OK);
		CHECK(wm_put_ioctl_req(slot, 0, WLC_GET_VERSION, 7, 4, 4, pa_of(inbuf)) == WM_OK);
		wm_idx_set(H2D_W(&h), 0, h.h2d.wr);
		dg_service(&h.d);
		CHECK(h.d.n_ioctl == 1 && h.d.last_cmd == 1 && h.d.last_trans == 7 && h.d.last_in == 4 && h.d.last_out == 4 && h.d.protocol_errors == 0);
		/* ack then completion */
		CHECK(host_next(&h, &it) == WM_OK);
		CHECK(wm_parse_cmplt(it, h.d2h.item_len, &c) == WM_OK);
		CHECK(c.type == WM_MSG_IOCTLPTR_REQ_ACK && c.cmd == 1 && c.status == 0);
		host_pop(&h);
		CHECK(host_next(&h, &it) == WM_OK);
		CHECK(wm_parse_cmplt(it, h.d2h.item_len, &c) == WM_OK);
		CHECK(c.type == WM_MSG_IOCTL_CMPLT && c.cmd == 1 && c.trans_id == 7 && c.resp_len == 4 && c.status == 0 && c.request_id == 100);
		CHECK(wm_payload_range(RESP_BUF, 0, c.resp_len, &at) == WM_OK && at == 0);
		CHECK(r32(respbuf) == 0x30);
		host_pop(&h);
		CHECK(host_next(&h, &it) == WM_E_EMPTY);
		/* a response length that does not fit the buffer, with an offset, and with wrap-around values */
		CHECK(wm_payload_range(RESP_BUF, 0, RESP_BUF + 1, &at) == WM_E_LEN);
		CHECK(wm_payload_range(RESP_BUF, RESP_BUF, 0, &at) == WM_OK);
		CHECK(wm_payload_range(RESP_BUF, RESP_BUF + 1, 0, &at) == WM_E_LEN);
		CHECK(wm_payload_range(RESP_BUF, 0xFFFFFFF0u, 0x20, &at) == WM_E_LEN);
		/* no response buffer left posted -> the dongle answers status -9 (BCMPCIE_NO_IOCTLRESP_BUF) */
		h.d.resp_n = 0;
		CHECK(wm_h2d_reserve(&h.h2d, wm_idx_get(H2D_R(&h), 0), &slot) == WM_OK);
		CHECK(wm_put_ioctl_req(slot, 0, WLC_GET_VERSION, 8, 0, 4, pa_of(inbuf)) == WM_OK);
		wm_idx_set(H2D_W(&h), 0, h.h2d.wr);
		dg_service(&h.d);
		CHECK(host_next(&h, &it) == WM_OK); host_pop(&h);            /* ack */
		CHECK(host_next(&h, &it) == WM_OK);
		CHECK(wm_parse_cmplt(it, h.d2h.item_len, &c) == WM_OK);
		CHECK(c.type == WM_MSG_IOCTL_CMPLT && c.status == -WM_STATUS_NO_IOCTLRESP_BUF && c.trans_id == 8);
		host_pop(&h);
		CHECK(h.d.protocol_errors == 0);
	}

	/* ---- 6. event post -> WL_EVENT -> ESCAN_RESULT with 2 BSS; then the SUCCESS end event ---- */
	{
		struct host h;
		uint8_t *evtbuf = arena + 0x8000;
		const uint8_t *it;
		wm_cmplt c;
		wm_event ev;
		wm_bss bss[4];
		unsigned cnt = 99;
		uint16_t sid = 0;
		static uint8_t esc[1024], frame[1200];
		uint8_t a1[6] = {0x02, 0x11, 0x22, 0x33, 0x44, 0x55}, a2[6] = {0xA0, 0xB1, 0xC2, 0xD3, 0xE4, 0xF5};
		unsigned n, flen;
		uint32_t at;
		host_init(&h);
		CHECK(host_post_buf(&h, WM_MSG_EVENT_BUF_POST, 300, evtbuf, EVT_BUF) == 0);
		CHECK(host_post_buf(&h, WM_MSG_EVENT_BUF_POST, 301, evtbuf + EVT_BUF, EVT_BUF) == 0);
		/* wl_escan_result: buflen, version, sync_id, bss_count, then two records (second with 20 bytes of IEs) */
		memset(esc, 0, sizeof esc);
		n = 12;
		n += put_bss(esc + n, 0, a1, "pixos-test", 6, -52);
		n += put_bss(esc + n, 20, a2, "Cafe 5G", 36, -71);
		d32(esc, n); d32(esc + 4, 109); d16(esc + 8, 0x1234); d16(esc + 10, 2);
		flen = put_event(frame, 69, 8, esc, n);
		dg_event(&h.d, frame, flen);
		CHECK(host_next(&h, &it) == WM_OK);
		CHECK(wm_parse_cmplt(it, h.d2h.item_len, &c) == WM_OK && c.type == WM_MSG_WL_EVENT && c.request_id == 300 && c.event_data_len == flen && c.event_seq == 77);
		CHECK(wm_payload_range(EVT_BUF, RXOFF, c.event_data_len, &at) == WM_OK && at == RXOFF);
		CHECK(wm_parse_event(evtbuf + at, c.event_data_len, &ev) == WM_OK);
		CHECK(ev.event_type == WLC_E_ESCAN_RESULT && ev.status == WLC_E_STATUS_PARTIAL && ev.datalen == n);
		CHECK(wm_parse_escan(ev.data, ev.datalen, &sid, bss, 4, &cnt) == WM_OK);
		CHECK(cnt == 2 && sid == 0x1234);
		CHECK(memcmp(bss[0].bssid, a1, 6) == 0 && bss[0].ssid_len == 10 && memcmp(bss[0].ssid, "pixos-test", 10) == 0 && bss[0].channel == 6 && bss[0].rssi == -52 && bss[0].chanspec == 0x1006);
		CHECK(memcmp(bss[1].bssid, a2, 6) == 0 && bss[1].ssid_len == 7 && memcmp(bss[1].ssid, "Cafe 5G", 7) == 0 && bss[1].channel == 36 && bss[1].rssi == -71);
		host_pop(&h);
		/* end of scan: status SUCCESS, no data */
		flen = put_event(frame, 69, 0, esc, 0);
		dg_event(&h.d, frame, flen);
		CHECK(host_next(&h, &it) == WM_OK);
		CHECK(wm_parse_cmplt(it, h.d2h.item_len, &c) == WM_OK && c.request_id == 301);
		CHECK(wm_parse_event(evtbuf + EVT_BUF + RXOFF, c.event_data_len, &ev) == WM_OK);
		CHECK(ev.event_type == 69 && ev.status == WLC_E_STATUS_SUCCESS && ev.datalen == 0);
		host_pop(&h);
		/* no event buffer left: the fake dongle must see that as its own error (proves it checks) */
		{ unsigned e0 = h.d.protocol_errors; quiet = 1; dg_event(&h.d, frame, flen); quiet = 0; CHECK(h.d.protocol_errors == e0 + 1); }
		CHECK(h.d.protocol_errors == 1);   /* only that deliberate one */

		/* ---- 7. hostile input: every case must be refused ---- */
		/* event framing */
		n = 12 + put_bss(esc + 12, 0, a1, "x", 1, -1);
		d32(esc, n); d16(esc + 10, 1);
		flen = put_event(frame, 69, 8, esc, n);
		CHECK(wm_parse_event(frame, flen, &ev) == WM_OK);
		CHECK(wm_parse_event(frame, WM_EV_HDR_LEN - 1, &ev) == WM_E_LEN);          /* truncated header */
		CHECK(wm_parse_event(frame, 0, &ev) == WM_E_LEN);
		CHECK(wm_parse_event(frame, flen - 1, &ev) == WM_E_LEN);                   /* datalen exceeds the frame */
		db32(frame + 44, 0xFFFFFFFFu);
		CHECK(wm_parse_event(frame, flen, &ev) == WM_E_LEN);                       /* oversized datalen */
		db32(frame + 44, n);
		frame[13] = 0x00;
		CHECK(wm_parse_event(frame, flen, &ev) == WM_E_FRAME);                     /* not ethertype 0x886c */
		frame[13] = 0x6c; frame[20] ^= 1;
		CHECK(wm_parse_event(frame, flen, &ev) == WM_E_FRAME);                     /* wrong OUI */
		frame[20] ^= 1; frame[23] = 2;
		CHECK(wm_parse_event(frame, flen, &ev) == WM_E_FRAME);                     /* not the event subtype */
		frame[23] = 1; frame[25] = 1;
		CHECK(wm_parse_event(frame, flen, &ev) == WM_E_VER);                       /* event struct version */
		frame[25] = 2;
		CHECK(wm_parse_event(frame, flen, &ev) == WM_OK);
		/* escan result */
		CHECK(wm_parse_escan(esc, 11, &sid, bss, 4, &cnt) == WM_E_LEN);            /* shorter than the fixed part */
		CHECK(wm_parse_escan(esc, n - 1, &sid, bss, 4, &cnt) == WM_E_LEN);         /* buflen larger than the data */
		CHECK(cnt == 0);
		d32(esc, 11);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* buflen smaller than the fixed part */
		d32(esc, 0xFFFFFFFFu);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);
		d32(esc, n);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_OK && cnt == 1);
		d16(esc + 10, 5);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* bss_count above the caller's array */
		d16(esc + 10, 2);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* count says 2, only one record is there */
		d16(esc + 10, 0xFFFF);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 0xFFFF, &cnt) == WM_E_LEN);        /* huge count, nothing behind it */
		d16(esc + 10, 1);
		d32(esc + 12 + 4, 0xFFFFFFF0u);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* record length overflow */
		d32(esc + 12 + 4, 0);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* record length 0 */
		d32(esc + 12 + 4, WM_BSS_MIN_LEN - 1);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* shorter than the fields we read */
		d32(esc + 12 + 4, n - 12 + 1);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* longer than what is left */
		d32(esc + 12 + 4, n - 12);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_OK);
		esc[12 + 18] = 33;
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_LEN);             /* SSID_len > 32 */
		esc[12 + 18] = 1;
		d32(esc + 12, 114);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_E_VER);             /* bss_info version not 109 */
		d32(esc + 12, 109);
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_OK);
		/* an SSID_len shorter than the 32-byte field must not leak the bytes after it */
		esc[12 + 19 + 3] = 'Z';
		CHECK(wm_parse_escan(esc, n, &sid, bss, 4, &cnt) == WM_OK && bss[0].ssid[3] == 0);
		/* five real records but room for four: refused before any write past the caller's array */
		{
			static uint8_t five[1024];
			wm_bss guard[6];
			unsigned m = 12;
			memset(guard, 0x5A, sizeof guard);
			for (int i = 0; i < 5; i++) m += put_bss(five + m, 0, a1, "s", 1 + i, -40);
			d32(five, m); d32(five + 4, 109); d16(five + 8, 1); d16(five + 10, 5);
			CHECK(wm_parse_escan(five, m, &sid, guard, 5, &cnt) == WM_OK && cnt == 5);
			memset(guard, 0x5A, sizeof guard);
			CHECK(wm_parse_escan(five, m, &sid, guard, 4, &cnt) == WM_E_LEN && cnt == 0);
			CHECK(((uint8_t *)&guard[0])[0] == 0x5A && ((uint8_t *)&guard[4])[0] == 0x5A);
		}
		/* completion parsing */
		{
			uint8_t g[24] = {0};
			CHECK(wm_parse_cmplt(g, 23, &c) == WM_E_LEN);
			CHECK(wm_parse_cmplt(g, 25, &c) == WM_E_LEN);
			g[0] = 0x0F;
			CHECK(wm_parse_cmplt(g, 24, &c) == WM_E_TYPE);                         /* a TX_POST type on the control ring */
			g[0] = 0;
			CHECK(wm_parse_cmplt(g, 24, &c) == WM_E_TYPE);                         /* msg_type 0 = invalid */
		}
	}

	/* ---- 8. builders refuse bad arguments; fields land at the cited offsets ---- */
	{
		uint8_t it[40];
		m_ioctl_req q;
		m_buf_post bp;
		memset(it, 0, sizeof it);
		CHECK(wm_put_ioctl_req(it, 0, 262, 9, 2009, 4, ARENA_PA) == WM_E_LEN);     /* 2008 is the maximum */
		CHECK(wm_put_ioctl_req(it, 0, 262, 9, 2008, 4, ARENA_PA) == WM_OK);
		CHECK(wm_put_ioctl_req(it, 0, 262, 9, 4, 8193, ARENA_PA) == WM_E_LEN);
		CHECK(wm_put_ioctl_req(it, 0, 262, 9, 4, 4, ARENA_PA + 2) == WM_E_ARG);    /* unaligned */
		CHECK(wm_put_ioctl_req(it, 0, 262, 9, 4, 4, 0) == WM_E_ARG);               /* null */
		CHECK(wm_put_ioctl_req(it, 0, 262, 9, 4, 4, 0xFFFFFFFCull) == WM_E_ARG);   /* 32-bit carry over the buffer */
		memset(it, 0, sizeof it);
		CHECK(wm_put_ioctl_req(it, 3, 0x01020304u, 0x0506, 0x0708, 0x090A, 0x123456780ull) == WM_OK);
		memcpy(&q, it, sizeof q);
		CHECK(q.h.msg_type == 9 && q.h.if_id == 3 && q.h.request_id == 0xFFFE && q.cmd == 0x01020304u && q.trans_id == 0x0506 && q.input_buf_len == 0x0708 &&
		      q.output_buf_len == 0x090A && q.lo == 0x23456780u && q.hi == 1 && q.rsvd1[0] == 0 && q.rsvd1[1] == 0 && q.rsvd[0] == 0);
		memset(it, 0, sizeof it);
		CHECK(wm_put_buf_post(it, 0x0C, 5, 64, ARENA_PA) == WM_E_TYPE);
		CHECK(wm_put_buf_post(it, WM_MSG_EVENT_BUF_POST, 0, 64, ARENA_PA) == WM_E_ARG);       /* pktid 0 = invalid */
		CHECK(wm_put_buf_post(it, WM_MSG_EVENT_BUF_POST, 0xFFFE, 64, ARENA_PA) == WM_E_ARG);  /* the ioctl request id */
		CHECK(wm_put_buf_post(it, WM_MSG_EVENT_BUF_POST, 5, 0, ARENA_PA) == WM_E_ARG);
		CHECK(wm_put_buf_post(it, WM_MSG_EVENT_BUF_POST, 5, 0x10000, ARENA_PA) == WM_E_ARG);  /* host_buf_len is 16 bit */
		CHECK(wm_put_buf_post(it, WM_MSG_EVENT_BUF_POST, 5, 64, ARENA_PA + 1) == WM_E_ARG);
		CHECK(wm_put_buf_post(it, WM_MSG_IOCTLRESP_BUF_POST, 0x01020304u, 8192, 0x2AABBCCD0ull) == WM_OK);
		memcpy(&bp, it, sizeof bp);
		CHECK(bp.h.msg_type == 0x0B && bp.h.if_id == 0 && bp.h.request_id == 0x01020304u && bp.host_buf_len == 8192 && bp.lo == 0xAABBCCD0u && bp.hi == 2);
	}

	/* ---- 9. index slot access: 4-byte slots, low 16 bits only (DHD), neighbours untouched ---- */
	{
		uint8_t a[16];
		memset(a, 0xAA, sizeof a);
		wm_idx_set(a, 1, 0x1234);
		CHECK(a[3] == 0xAA && a[4] == 0x34 && a[5] == 0x12 && a[6] == 0xAA && a[7] == 0xAA && a[8] == 0xAA);
		CHECK(wm_idx_get(a, 1) == 0x1234 && wm_idx_get(a, 0) == 0xAAAA);
		CHECK(wm_idx_array_bytes(5) == 20);
	}

	if (fails) {
		fprintf(stderr, "RESULT: FAIL wlan_msgbuf_host_test (%u checks)\n", fails);
		return 1;
	}
	fprintf(stderr, "RESULT: PASS wlan_msgbuf_host_test\n");
	return 0;
}
