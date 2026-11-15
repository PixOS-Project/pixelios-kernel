/*
 * wlan_msgbuf.h - pure host-side BCM4389 "msgbuf" (PCIe IPC v7) ring library.
 *
 * NO hardware access, NO kernel headers, NO libc: stdint/stddef only, so a host test and a later XNU
 * patch (bsd/dev/...) can both include it. The caller does everything that touches memory it does not
 * own: DMA allocation, cache clean/invalidate (docs/research/53 section 3.2), BAR stores. This file only
 * (a) lays out and validates bytes in buffers the caller hands in, and (b) computes values.
 *
 * Plan: docs/research/53-wd4-msgbuf-ring-plan.md section 7 step 3. Not yet used by any shim or patch.
 *
 * Source keys (every constant below carries "[R5 file:line]" or "[BW file:line]"; lines were opened
 * while writing this file):
 *   R5 = ~/pixelios-artifacts/refs/wlan-stage5/  (Google 4389 DHD pin of refs/wlan-stage4/SOURCE.txt:
 *        bcmpcie.h, bcmmsgbuf.h, dhd_msgbuf.c, dhd_pcie.c, pcie_core.h, bcmevent.h.new, wlioctl.h.new,
 *        wlioctl_defs.h.new)
 *   BW = ~/pixelios-artifacts/bwfm-host/  (OpenBSD bwfm, ISC; used only for the event Ethernet framing,
 *        which is not in R5: the Google bcmeth.h is NOT in the refs)
 * Tag "[guess]" marks a choice not settled by a source line.
 *
 * Byte order: ring items, the shared structure and escan results are little endian (DHD stores with
 * htol16/htol32 [R5 dhd_msgbuf.c:10880-10893]). The wl_event_msg header inside an event is network
 * order (big endian) [R5 bcmevent.h.new:57 "stored in network order"]. All access here is by byte, so
 * no alignment or host-endian assumption is made.
 */
#ifndef WLAN_MSGBUF_H
#define WLAN_MSGBUF_H

#include <stddef.h>
#include <stdint.h>

/* Rule 8 (no silent failures): the includer defines WM_GAP(const char *line) to print one line (the shim: print it to the USB log;
 * a host test: count/print it). The library has no libc, so it cannot print itself. */
#ifndef WM_GAP
#error "wlan_msgbuf.h: define WM_GAP(const char *line) before including (prints one 'WIFI GAP: ...' line, rule 8)"
#endif

/* ---- error codes (negative; 0 = ok) ---- */
#define WM_OK         0
#define WM_E_ARG     (-1)  /* bad argument / layout */
#define WM_E_FULL    (-2)  /* H2D ring full */
#define WM_E_EMPTY   (-3)  /* D2H ring has no item */
#define WM_E_BADIDX  (-4)  /* an index read from the dongle is >= max_items */
#define WM_E_EPOCH   (-5)  /* D2H epoch is not the expected one */
#define WM_E_CSUM    (-6)  /* D2H XOR checksum is not 0 */
#define WM_E_TYPE    (-7)  /* unexpected msg_type */
#define WM_E_LEN     (-8)  /* length / offset / count out of range or overflowing */
#define WM_E_FRAME   (-9)  /* event Ethernet framing is not a BCM event */
#define WM_E_VER     (-10) /* event or bss_info version not the one the offsets were taken from */
#define WM_E_SYNC    (-11) /* ring sync mode not supported by this library */

/* ---- cmn_msg_hdr_t, 8 bytes: u8 msg_type, u8 if_id, u8 flags, u8 epoch, u32 request_id [R5 bcmmsgbuf.h:181-192] */
#define WM_HDR_TYPE      0
#define WM_HDR_IFID      1
#define WM_HDR_FLAGS     2
#define WM_HDR_EPOCH     3
#define WM_HDR_REQID     4
#define WM_HDR_LEN       8

/* message types [R5 bcmmsgbuf.h:233-238] */
#define WM_MSG_IOCTLPTR_REQ       0x09
#define WM_MSG_IOCTLPTR_REQ_ACK   0x0A
#define WM_MSG_IOCTLRESP_BUF_POST 0x0B
#define WM_MSG_IOCTL_CMPLT        0x0C
#define WM_MSG_EVENT_BUF_POST     0x0D
#define WM_MSG_WL_EVENT           0x0E
/* msg_type 0 = MSG_TYPE_INVALID, a no-op item [R5 dhd_msgbuf.c:976] */
#define WM_MSG_INVALID            0x00

/* epoch: both directions start the counter at 254 and use it modulo 253, so the first epoch is 1 and the
 * values 1..252,0,1.. cycle [R5 bcmmsgbuf.h:35-39; dhd_msgbuf.c:1782 (D2H init), 1823 (H2D init)] */
#define WM_EPOCH_MODULO    253u
#define WM_EPOCH_INIT_VAL  254u

/* cmn hdr flags of an H2D item = the ring's phase bit; initial 0, flipped on every wrap
 * [R5 bcmmsgbuf.h:384-385; dhd_msgbuf.c:1821-1824 (init 0), 10777-10788 (flip when slot 0 is handed out)].
 * [guess] the dongle ignores it while HOSTCAP_H2D_VALID_PHASE (0x100, bcmpcie.h:441) is clear in host_cap;
 * DHD sends it the same way with that bit clear by default (h2d_phase_supported FALSE, dhd_msgbuf.c:3887). */
#define WM_PHASE_BIT       0x80

/* ---- item sizes in bytes (IPC version >= 7) [R5 bcmmsgbuf.h:59-70] ---- */
#define WM_H2D_CTRL_ITEM   40u /* H2DRING_CTRL_SUB_ITEMSIZE (bcmmsgbuf.h:60) */
#define WM_H2D_RXPOST_ITEM 32u /* H2DRING_RXPOST_ITEMSIZE (bcmmsgbuf.h:59) */
#define WM_D2H_CTRL_ITEM   24u /* D2HRING_CTRL_CMPLT_ITEMSIZE (bcmmsgbuf.h:70) */
#define WM_D2H_TX_ITEM     24u /* D2HRING_TXCMPLT_ITEMSIZE (bcmmsgbuf.h:62) */
#define WM_D2H_RX_ITEM     40u /* D2HRING_RXCMPLT_ITEMSIZE (bcmmsgbuf.h:63) */

/* ---- common ring ids and slots in the index arrays [R5 bcmpcie.h:179-188, 218-248] ---- */
#define WM_RING_H2D_CTRL   0 /* BCMPCIE_H2D_MSGRING_CONTROL_SUBMIT */
#define WM_RING_H2D_RXPOST 1
#define WM_RING_D2H_CTRL   2 /* BCMPCIE_D2H_MSGRING_CONTROL_COMPLETE */
#define WM_RING_D2H_TX     3
#define WM_RING_D2H_RX     4
#define WM_NCOMMON_RINGS   5 /* BCMPCIE_COMMON_MSGRINGS */
#define WM_NH2D_COMMON     2 /* BCMPCIE_H2D_COMMON_MSGRINGS */
/* H2D index-array slot = ring id; D2H slot = ring id - 2 (BCMPCIE_D2H_RING_IDX, bcmpcie.h:241) */
#define WM_H2D_SLOT(ring_id) (ring_id)
#define WM_D2H_SLOT(ring_id) ((ring_id) - WM_NH2D_COMMON)

/* ---- shared-structure flags (pciedev_shared_t.flags) [R5 bcmpcie.h] ---- */
#define WM_SH_VERSION_MASK    0x000FFu    /* :54 */
#define WM_SH_ASSERT          0x00200u    /* :56 */
#define WM_SH_TRAP            0x00400u    /* :57 */
#define WM_SH_DMA_INDEX       0x10000u    /* :63 */
#define WM_SH_D2H_SYNC_SEQNUM 0x20000u    /* :73 */
#define WM_SH_D2H_SYNC_XORCSUM 0x40000u   /* :74 */
#define WM_SH_2BYTE_INDICES   0x100000u   /* :78 */
#define WM_SH_HOSTRDY_SUPPORT 0x10000000u /* :111 */
#define WM_SH_DAR             0x80000000u /* :120 */
#define WM_HOSTCAP_H2D_ENABLE_HOSTRDY 0x00000400u /* [R5 bcmpcie.h:443] */

/* ---- pciedev_shared_t (version 7 header) byte offsets, from the declaration [R5 bcmpcie.h:344-433]:
 * u32 fields back to back, sh_addr_t = {u32 low, u32 high} [R5 bcmpcie.h:35-38]. The host test mirrors
 * the declaration as a C struct and checks every offset with offsetof() (independent of this list).
 * Offsets past 0x84 are labelled [ipc v9] in the source; the real v7 length is unknown (research 53 U5). */
#define WM_SH_FLAGS                 0x00
#define WM_SH_TRAP_ADDR             0x04
#define WM_SH_CONSOLE_ADDR          0x14
#define WM_SH_MAX_HOST_RXBUFS       0x22 /* u16 */
#define WM_SH_DMA_RXOFFSET          0x24 /* rx data offset: event buffers start here (dhd_msgbuf.c:8291) */
#define WM_SH_H2D_MB_DATA_PTR       0x28
#define WM_SH_D2H_MB_DATA_PTR       0x2C
#define WM_SH_RINGS_INFO_PTR        0x30
#define WM_SH_SCRATCH_LEN           0x34 /* host writes */
#define WM_SH_SCRATCH_ADDR          0x38 /* host writes, 8 bytes */
#define WM_SH_HOST_SCB_ADDR         0x40 /* 8 bytes; DO NOT WRITE (53 s1.1 conflict note) */
#define WM_SH_HOST_SCB_SIZE         0x48
#define WM_SH_FLAGS2                0x50
#define WM_SH_HOST_CAP              0x54 /* host writes */
#define WM_SH_HOST_TRAP_ADDR        0x58 /* 8 bytes */
#define WM_SH_HOST_CAP2             0x70
#define WM_SH_LEN_V9                0x8C /* sizeof(pciedev_shared_t) as declared */

/* ---- ring_info_t offsets [R5 bcmpcie.h:305-339] ---- */
#define WM_RI_RINGMEM_PTR           0x00
#define WM_RI_H2D_W_IDX_PTR         0x04
#define WM_RI_H2D_R_IDX_PTR         0x08
#define WM_RI_D2H_W_IDX_PTR         0x0C
#define WM_RI_D2H_R_IDX_PTR         0x10
#define WM_RI_H2D_W_IDX_HOSTADDR    0x14 /* 8 bytes, host writes */
#define WM_RI_H2D_R_IDX_HOSTADDR    0x1C
#define WM_RI_D2H_W_IDX_HOSTADDR    0x24
#define WM_RI_D2H_R_IDX_HOSTADDR    0x2C
#define WM_RI_MAX_TX_FLOWRINGS      0x34 /* u16 */
#define WM_RI_MAX_SUBMISSION_QUEUES 0x36 /* u16 */
#define WM_RI_MAX_COMPLETION_RINGS  0x38 /* u16 */
#define WM_RI_MAX_VDEVS             0x3A /* u16 */
#define WM_RI_LEN                   0x4C

/* ---- ring_mem_t (16 bytes per ring) offsets [R5 bcmpcie.h:290-297] ---- */
#define WM_RM_IDX        0x00 /* u16 */
#define WM_RM_TYPE       0x02 /* u8 */
#define WM_RM_MAX_ITEM   0x04 /* u16, host writes */
#define WM_RM_LEN_ITEMS  0x06 /* u16, host writes */
#define WM_RM_BASE_ADDR  0x08 /* sh_addr_t, host writes */
#define WM_RM_SIZE       16

/* ---- PCIe-core doorbell registers (BAR0 window onto the PCIe core), non-DAR [R5 pcie_core.h:1030-1031;
 * dhd_pcie.c:659-666 uses PCIH2D_MailBox when PCIE_SHARED_DAR is clear] ---- */
#define WM_REG_H2D_DB0  0x140u
#define WM_REG_H2D_DB1  0x144u
/* hostready value written to DB1 [R5 dhd_pcie.c:9244]. */
#define WM_HOSTREADY_DB_VALUE 0x12345678u

/* ---- ioctl ---- */
#define WM_IOCTL_REQ_PKTID   0xFFFEu /* request_id of every IOCTLPTR_REQ [R5 dhd_msgbuf.c:2279] */
#define WM_IOCTL_MAX_RQSTLEN 2008u   /* 2048 - 40 [R5 dhd_msgbuf.c:129-130] */
#define WM_IOCTL_MAXLEN      8192u   /* WLC_IOCTL_MAXLEN [R5 wlioctl_defs.h.new:667] */
#define WM_IOCTL_RESPBUF_LEN 8192u   /* DHD_FLOWRING_IOCTL_BUFPOST_PKTSZ [R5 dhd_msgbuf.c:149] */
#define WLC_GET_VERSION      1u      /* [R5 wlioctl_defs.h.new:681] */
/* BCMPCIE_SUCCESS 0, BCMPCIE_NO_IOCTLRESP_BUF 9 (compl_hdr.status) [R5 bcmmsgbuf.h:740, 749] */
#define WM_STATUS_SUCCESS    0
#define WM_STATUS_NO_IOCTLRESP_BUF 9

/* DMA buffer rules the dongle needs: non-zero address, 4-byte aligned low word, no 32-bit carry over the
 * buffer [R5 dhd_msgbuf.c:1845-1864, DMA_ALIGN_LEN 4 at :138] */
#define WM_DMA_ALIGN 4u

/* ---- events (Ethernet-framed) ----
 * frame = ether_header(14) + bcm eth hdr(10) + wl_event_msg(48) + data. Ether type 0x886c, OUI 00:10:18,
 * usr_subtype 1 = event [BW bwfmreg.h:755-784]; wl_event_msg_t fields [R5 bcmevent.h.new:71-87], event
 * struct version 2 [R5 bcmevent.h.new:46]. */
#define WM_ETH_HLEN         14
#define WM_ETH_TYPE_BCM     0x886Cu /* [BW bwfmreg.h:781] */
#define WM_BCMETH_LEN       10      /* subtype u16, length u16, version u8, oui[3], usr_subtype u16 */
#define WM_BCMETH_OUI_OFF   5       /* from the bcm eth hdr start */
#define WM_BCMETH_USRSUB_OFF 8
#define WM_BCM_OUI0 0x00 /* [BW bwfmreg.h:760] */
#define WM_BCM_OUI1 0x10
#define WM_BCM_OUI2 0x18
#define WM_BCM_SUBTYPE_EVENT 1u /* [BW bwfmreg.h:762] */
#define WM_EVMSG_LEN        48 /* 2+2+4*5+6+16+1+1 */
#define WM_EVMSG_OFF        (WM_ETH_HLEN + WM_BCMETH_LEN) /* 24 */
#define WM_EV_HDR_LEN       (WM_EVMSG_OFF + WM_EVMSG_LEN) /* 72 */
#define WM_EVMSG_VERSION    2u /* BCM_EVENT_MSG_VERSION */
/* offsets inside wl_event_msg_t */
#define WM_EV_VERSION    0 /* u16 BE */
#define WM_EV_FLAGS      2
#define WM_EV_TYPE       4 /* u32 BE */
#define WM_EV_STATUS     8
#define WM_EV_REASON     12
#define WM_EV_AUTHTYPE   16
#define WM_EV_DATALEN    20
#define WM_EV_ADDR       24
#define WM_EV_IFNAME     30
#define WM_EV_IFIDX      46
#define WM_EV_BSSCFGIDX  47
#define WLC_E_ESCAN_RESULT     69u /* [R5 bcmevent.h.new:180] */
#define WLC_E_STATUS_SUCCESS   0u  /* [R5 bcmevent.h.new:412] */
#define WLC_E_STATUS_PARTIAL   8u  /* [R5 bcmevent.h.new:420] */
#define WLC_E_STATUS_ABORT     4u  /* [R5 bcmevent.h.new:416] */

/* wl_escan_result: u32 buflen, u32 version, u16 sync_id, u16 bss_count, bss_info[] [R5 wlioctl.h.new:1108-1114];
 * all little endian (the OpenBSD parser uses letoh on them, BW bwfm.c:2640-2700). buflen counts from the
 * start of this structure [BW bwfm.c:2640-2700 compares it with the bytes received]. */
#define WM_ESC_BUFLEN   0
#define WM_ESC_VERSION  4
#define WM_ESC_SYNC_ID  8
#define WM_ESC_BSS_COUNT 10
#define WM_ESC_FIXED    12
/* wl_bss_info_v109 field offsets [R5 wlioctl.h.new:404-455] (version 109 [R5 wlioctl.h.new:347]); the
 * OpenBSD struct bwfm_bss_info (BW bwfmreg.h:347-380) gives the same offsets for these fields. Checked by
 * offsetof() against a mirrored struct in the host test. */
#define WM_BSS_VERSION_109 109u
#define WM_BSS_VERSION  0
#define WM_BSS_LENGTH   4  /* u32: byte length of this record incl. IEs */
#define WM_BSS_BSSID    8
#define WM_BSS_SSID_LEN 18
#define WM_BSS_SSID     19
#define WM_BSS_SSID_MAX 32
#define WM_BSS_CHANSPEC 72 /* u16 */
#define WM_BSS_RSSI     78 /* i16, dBm */
#define WM_BSS_CTL_CH   88 /* u8, 802.11N control channel number */
#define WM_BSS_MIN_LEN  89 /* last byte read is CTL_CH */

/* ================= byte helpers ================= */
static inline uint16_t wm_le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t wm_le32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline uint16_t wm_be16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static inline uint32_t wm_be32(const uint8_t *p)
{
	return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}
static inline void wm_put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void wm_put32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
static inline void wm_put64(uint8_t *p, uint64_t v) { wm_put32(p, (uint32_t)v); wm_put32(p + 4, (uint32_t)(v >> 32)); }
static inline void wm_zero(uint8_t *p, size_t n) { while (n--) *p++ = 0; }

/* DMA address rule above. 1 = ok. */
static inline int wm_dma_ok(uint64_t pa, uint32_t len)
{
	uint32_t lo = (uint32_t)pa;
	if (pa == 0 || len == 0 || (lo & (WM_DMA_ALIGN - 1u)))
		return 0;
	return (uint32_t)(lo + len) >= lo; /* the dongle does 32-bit pointer arithmetic */
}

/* ================= ring state (host side) ================= */
typedef struct {
	uint8_t *base;       /* host VA of the ring buffer (item i at base + i * item_len) */
	uint16_t id;         /* ring id (0..4 common) */
	uint16_t max_items;
	uint16_t item_len;   /* bytes; multiple of 4 */
	uint16_t wr;         /* H2D: next slot to hand out. D2H: last write index seen */
	uint16_t rd;         /* D2H: next slot to read */
	uint32_t seqnum;     /* epoch counter; epoch = seqnum % 253 */
	uint8_t phase;       /* H2D phase bit (see WM_PHASE_BIT) */
	uint8_t is_d2h;
} wm_ring;

static inline int wm_ring_init(wm_ring *r, uint8_t *base, uint16_t id, uint16_t max_items, uint16_t item_len, int is_d2h)
{
	if (!r || !base || max_items < 2 || item_len < WM_HDR_LEN || (item_len & 3u) || id > 0xFFu)
		return WM_E_ARG;
	r->base = base; r->id = id; r->max_items = max_items; r->item_len = item_len;
	r->wr = 0; r->rd = 0; r->seqnum = WM_EPOCH_INIT_VAL; r->phase = 0; r->is_d2h = is_d2h ? 1 : 0;
	return WM_OK;
}

/* ---- index slots: 4-byte slots (2BYTE_INDICES clear in flags 0x70050107, 4-byte rw_index_sz
 * [R5 dhd_pcie.c:13771-13772]); DHD reads and writes only the low 16 bits of a slot with htol16/LTOH16
 * [R5 dhd_msgbuf.c:11917, 11998-12000]; we do the same (the upper half is left alone / ignored).
 * `slots` points to the start of one of the four host arrays. Cache maintenance is the caller's. */
#define WM_IDX_SZ 4u
static inline uint32_t wm_idx_get(const volatile uint8_t *slots, unsigned slot)
{
	const volatile uint8_t *p = slots + (size_t)slot * WM_IDX_SZ;
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8);
}
static inline void wm_idx_set(volatile uint8_t *slots, unsigned slot, uint32_t v)
{
	volatile uint8_t *p = slots + (size_t)slot * WM_IDX_SZ;
	p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
/* size of one host index array for n rings */
static inline uint32_t wm_idx_array_bytes(unsigned nrings) { return (uint32_t)nrings * WM_IDX_SZ; }

/* ================= H2D producer ================= */
/* Hand out the next slot of an H2D ring. rd_idx = the read index the dongle published (from the H2D read
 * index slot). Full when next(wr) == rd [R5 bcmpcie.h:560-562 CHECK_WRITE_SPACE, which leaves one slot
 * empty]. The item is zeroed; epoch = seqnum % 253 and the phase flag are filled in, seqnum++ and the phase
 * flips when slot 0 is handed out [R5 dhd_msgbuf.c:10777-10788, 10881-10884]. The caller then fills the
 * rest with wm_put_*(), flushes the item, calls wm_h2d_publish(). */
static inline int wm_h2d_reserve(wm_ring *r, uint32_t rd_idx, uint8_t **item)
{
	uint16_t next;
	uint8_t *it;
	if (!r || !item || r->is_d2h)
		return WM_E_ARG;
	if (rd_idx >= r->max_items)
		return WM_E_BADIDX;
	next = (uint16_t)((r->wr + 1u >= r->max_items) ? 0u : r->wr + 1u);
	if (next == rd_idx)
		return WM_E_FULL;
	it = r->base + (size_t)r->wr * r->item_len;
	if (r->wr == 0)
		r->phase = r->phase ? 0 : WM_PHASE_BIT;
	wm_zero(it, r->item_len);
	it[WM_HDR_FLAGS] = r->phase;
	it[WM_HDR_EPOCH] = (uint8_t)(r->seqnum % WM_EPOCH_MODULO);
	r->seqnum++;
	r->wr = next;
	*item = it;
	return WM_OK;
}

/* Doorbell value for DB0 after the write index was stored: 0xFF000000 | ring_id << 16 | wr
 * [R5 dhd_msgbuf.c:116, used at :11601]. */
static inline uint32_t wm_db0_value(const wm_ring *r)
{
	return 0xFF000000u | ((uint32_t)r->id << 16) | r->wr;
}
/* The write-index slot value to store (low 16 bits) is r->wr; ordering rule: items flushed, then the index
 * stored, then DB0 [R5 dhd_msgbuf.c:11566-11603]. */

/* ---- item builders (item = pointer from wm_h2d_reserve, already zeroed) ---- */
/* IOCTLPTR_REQ, 40 bytes [R5 bcmmsgbuf.h:390-407]: hdr; u32 cmd @8; u16 trans_id @12; u16 input_buf_len
 * @14; u16 output_buf_len @16; u16 rsvd[3] @18; u64 host_input_buf_addr @24 (low then high); u32 rsvd1[2]
 * @32. request_id is DHD_IOCTL_REQ_PKTID [R5 dhd_msgbuf.c:10882]. in_len above 2008 is refused (DHD clamps,
 * :10829); out_len above 8192 is refused. */
static inline int wm_put_ioctl_req(uint8_t *item, uint8_t if_id, uint32_t cmd, uint16_t trans_id,
				   uint32_t in_len, uint32_t out_len, uint64_t in_pa)
{
	if (!item || in_len > WM_IOCTL_MAX_RQSTLEN || out_len > WM_IOCTL_MAXLEN)
		return WM_E_LEN;
	if (!wm_dma_ok(in_pa, in_len ? in_len : 1))
		return WM_E_ARG;
	item[WM_HDR_TYPE] = WM_MSG_IOCTLPTR_REQ;
	item[WM_HDR_IFID] = if_id;
	wm_put32(item + WM_HDR_REQID, WM_IOCTL_REQ_PKTID);
	wm_put32(item + 8, cmd);
	wm_put16(item + 12, trans_id);
	wm_put16(item + 14, in_len);
	wm_put16(item + 16, out_len);
	wm_put64(item + 24, in_pa);
	return WM_OK;
}
/* IOCTLRESP_BUF_POST (0xB) / EVENT_BUF_POST (0xD), 40 bytes [R5 bcmmsgbuf.h:410-420]: hdr; u16
 * host_buf_len @8; u16 reserved[3] @10; u64 host_buf_addr @16 (low then high); u32 rsvd[4] @24. request_id =
 * the pktid the host will recognise on the completion; if_id 0 [R5 dhd_msgbuf.c:6340-6390]. pktid 0 is
 * DHD_PKTID_INVALID [R5 dhd_msgbuf.c:2278] and 0xFFFE is the ioctl request id: both refused. */
static inline int wm_put_buf_post(uint8_t *item, uint8_t msg_type, uint32_t pktid, uint32_t buf_len, uint64_t pa)
{
	if (!item || (msg_type != WM_MSG_IOCTLRESP_BUF_POST && msg_type != WM_MSG_EVENT_BUF_POST))
		return WM_E_TYPE;
	if (pktid == 0 || pktid == WM_IOCTL_REQ_PKTID || buf_len == 0 || buf_len > 0xFFFFu || !wm_dma_ok(pa, buf_len))
		return WM_E_ARG;
	item[WM_HDR_TYPE] = msg_type;
	item[WM_HDR_IFID] = 0;
	wm_put32(item + WM_HDR_REQID, pktid);
	wm_put16(item + 8, buf_len);
	wm_put64(item + 16, pa);
	return WM_OK;
}

/* hostready doorbell: write WM_HOSTREADY_DB_VALUE to WM_REG_H2D_DB1 (see constants) after the shared
 * structure and ring_mem are written [R5 dhd_pcie.c:9244; DHD order dhd_msgbuf.c:4427-4431]. */

/* ================= D2H consumer ================= */
/* XOR of all 32-bit words of an item; 0 = valid. The checksum sits in the last word
 * [R5 dhd_msgbuf.c:1480-1481, 1527-1530]. */
static inline uint32_t wm_xor32(const uint8_t *item, size_t len)
{
	uint32_t x = 0;
	size_t i;
	for (i = 0; i + 4 <= len; i += 4)
		x ^= wm_le32(item + i);
	return x;
}

/* Look at the next D2H item. wr_idx = the D2H write index the dongle published (low 16 bits of its slot);
 * sync_flags = the shared `flags` word. Items are available while rd != wr [R5 dhd_msgbuf.c:12252,
 * READ_AVAIL_SPACE bcmpcie.h:557]. With D2H_SYNC_XORCSUM the item is accepted only if its epoch equals
 * seqnum % 253 AND the XOR of its words is 0 [R5 dhd_msgbuf.c:1491, 1527-1531]; with neither sync bit no
 * check is made [R5 dhd_msgbuf.c:1568-1583]; the SEQNUM-only mode (marker word) is not implemented
 * (WM_E_SYNC). On a bad item the ring state is NOT advanced (DHD skips it and leaks the pktid, dhd_msgbuf.c:1558;
 * the bring-up stops instead, 53 s5.2 G3). The item stays valid until wm_d2h_pop(). */
static inline int wm_d2h_peek(wm_ring *r, uint32_t wr_idx, uint32_t sync_flags, const uint8_t **item)
{
	const uint8_t *it;
	if (!r || !item || !r->is_d2h)
		return WM_E_ARG;
	if (wr_idx >= r->max_items || r->rd >= r->max_items)
		return WM_E_BADIDX;
	r->wr = (uint16_t)wr_idx;
	if (r->wr == r->rd)
		return WM_E_EMPTY;
	if ((sync_flags & WM_SH_D2H_SYNC_SEQNUM) && !(sync_flags & WM_SH_D2H_SYNC_XORCSUM)) {
		WM_GAP("WIFI GAP: msgbuf D2H SEQNUM-only sync mode is not supported (WM_E_SYNC)");
		return WM_E_SYNC;
	}
	it = r->base + (size_t)r->rd * r->item_len;
	if (sync_flags & WM_SH_D2H_SYNC_XORCSUM) {
		if (it[WM_HDR_EPOCH] != (uint8_t)(r->seqnum % WM_EPOCH_MODULO))
			return WM_E_EPOCH;
		if (wm_xor32(it, r->item_len) != 0)
			return WM_E_CSUM;
	}
	*item = it;
	return WM_OK;
}
/* Consume the item last returned by wm_d2h_peek(): rd wraps at max_items, seqnum++ [R5 dhd_msgbuf.c:1530
 * "ring->seqnum++"]. Returns the new read index to store in the D2H read-index slot. */
static inline uint32_t wm_d2h_pop(wm_ring *r)
{
	r->rd = (uint16_t)((r->rd + 1u >= r->max_items) ? 0u : r->rd + 1u);
	r->seqnum++;
	return r->rd;
}

/* ---- control completions, 24 bytes [R5 bcmmsgbuf.h:767-809]: hdr(8); compl_hdr: i16 status @8, u16 ring_id
 * @10; then per type:
 *   IOCTLPTR_REQ_ACK (0xA): u32 cmd @12, u32 rsvd @16, marker @20
 *   IOCTL_CMPLT      (0xC): u16 resp_len @12, u16 trans_id @14, u32 cmd @16, marker @20
 *   WL_EVENT         (0xE): u16 event_data_len @12, u16 seqnum @14, u32 rsvd @16, marker @20 */
typedef struct {
	uint8_t type;
	uint8_t if_id;
	uint32_t request_id; /* ACK: n/a; CMPLT/EVENT: pktid of the posted buffer */
	int16_t status;
	uint16_t ring_id;
	uint32_t cmd;        /* ACK, CMPLT */
	uint16_t trans_id;   /* CMPLT */
	uint16_t resp_len;   /* CMPLT */
	uint16_t event_data_len; /* EVENT */
	uint16_t event_seq;  /* EVENT */
} wm_cmplt;

static inline int wm_parse_cmplt(const uint8_t *item, size_t len, wm_cmplt *c)
{
	if (!item || !c || len != WM_D2H_CTRL_ITEM)
		return WM_E_LEN;
	wm_zero((uint8_t *)c, sizeof(*c));
	c->type = item[WM_HDR_TYPE];
	c->if_id = item[WM_HDR_IFID];
	c->request_id = wm_le32(item + WM_HDR_REQID);
	c->status = (int16_t)wm_le16(item + 8);
	c->ring_id = wm_le16(item + 10);
	switch (c->type) {
	case WM_MSG_IOCTLPTR_REQ_ACK:
		c->cmd = wm_le32(item + 12);
		return WM_OK;
	case WM_MSG_IOCTL_CMPLT:
		c->resp_len = wm_le16(item + 12);
		c->trans_id = wm_le16(item + 14);
		c->cmd = wm_le32(item + 16);
		return WM_OK;
	case WM_MSG_WL_EVENT:
		c->event_data_len = wm_le16(item + 12);
		c->event_seq = wm_le16(item + 14);
		return WM_OK;
	default:
		WM_GAP("WIFI GAP: msgbuf unhandled completion msg_type (WM_E_TYPE)");
		return WM_E_TYPE;
	}
}

/* Where the bytes of a completion are inside the posted DMA buffer of length buf_len.
 * ioctl response: from the start of the buffer, resp_len bytes [R5 dhd_msgbuf.c:7908, 7946-7960, copies from
 * PKTDATA]. event: after the shared dma_rxoffset, event_data_len bytes [R5 dhd_msgbuf.c:8266, 8291]. The
 * sum is checked without overflow (the lengths come from the dongle). */
static inline int wm_payload_range(uint32_t buf_len, uint32_t offset, uint32_t len, uint32_t *start)
{
	if (offset > buf_len || len > buf_len - offset)
		return WM_E_LEN;
	*start = offset;
	return WM_OK;
}

/* ================= events ================= */
typedef struct {
	uint32_t event_type, status, reason, datalen;
	uint8_t ifidx, bsscfgidx;
	const uint8_t *data; /* points into the frame, datalen bytes */
} wm_event;

/* Parse an Ethernet-framed BCM event (`f`, `len` = event_data_len after the rx offset was skipped). */
static inline int wm_parse_event(const uint8_t *f, size_t len, wm_event *ev)
{
	const uint8_t *h, *m;
	if (!f || !ev)
		return WM_E_ARG;
	if (len < WM_EV_HDR_LEN)
		return WM_E_LEN;
	if (wm_be16(f + 12) != WM_ETH_TYPE_BCM)
		return WM_E_FRAME;
	h = f + WM_ETH_HLEN;
	m = f + WM_EVMSG_OFF;
	if (h[WM_BCMETH_OUI_OFF] != WM_BCM_OUI0 || h[WM_BCMETH_OUI_OFF + 1] != WM_BCM_OUI1 ||
	    h[WM_BCMETH_OUI_OFF + 2] != WM_BCM_OUI2 || wm_be16(h + WM_BCMETH_USRSUB_OFF) != WM_BCM_SUBTYPE_EVENT)
		return WM_E_FRAME;
	if (wm_be16(m + WM_EV_VERSION) != WM_EVMSG_VERSION)
		return WM_E_VER;
	ev->event_type = wm_be32(m + WM_EV_TYPE);
	ev->status = wm_be32(m + WM_EV_STATUS);
	ev->reason = wm_be32(m + WM_EV_REASON);
	ev->datalen = wm_be32(m + WM_EV_DATALEN);
	ev->ifidx = m[WM_EV_IFIDX];
	ev->bsscfgidx = m[WM_EV_BSSCFGIDX];
	if (ev->datalen > len - WM_EV_HDR_LEN)
		return WM_E_LEN;
	ev->data = f + WM_EV_HDR_LEN;
	return WM_OK;
}

typedef struct {
	uint8_t bssid[6];
	uint8_t ssid_len;
	uint8_t ssid[WM_BSS_SSID_MAX];
	uint8_t channel;   /* ctl_ch */
	uint16_t chanspec;
	int16_t rssi;      /* dBm */
} wm_bss;

/* Parse the data of a WLC_E_ESCAN_RESULT event with status PARTIAL: wl_escan_result + bss records.
 * Every length comes from the dongle: buflen must lie in [12, n]; each record's length must be at least
 * WM_BSS_MIN_LEN and fit in what is left; SSID_len <= 32; bss_count must fit the caller's array and the
 * records must not run past buflen (same checks as BW bwfm.c:2640-2700, plus overflow-safe arithmetic).
 * bss_info version must be 109 (the version the offsets were taken from; anything else -> WM_E_VER). */
static inline int wm_parse_escan(const uint8_t *d, size_t n, uint16_t *sync_id, wm_bss *out, unsigned nmax,
				 unsigned *count)
{
	uint32_t buflen, left, rl;
	unsigned cnt, i, k;
	const uint8_t *p;
	if (!d || !out || !count)
		return WM_E_ARG;
	*count = 0;
	if (n < WM_ESC_FIXED)
		return WM_E_LEN;
	buflen = wm_le32(d + WM_ESC_BUFLEN);
	if (buflen < WM_ESC_FIXED || buflen > n)
		return WM_E_LEN;
	cnt = wm_le16(d + WM_ESC_BSS_COUNT);
	if (cnt > nmax)
		return WM_E_LEN;
	if (sync_id)
		*sync_id = wm_le16(d + WM_ESC_SYNC_ID);
	p = d + WM_ESC_FIXED;
	left = buflen - WM_ESC_FIXED;
	for (i = 0; i < cnt; i++) {
		if (left < WM_BSS_MIN_LEN)
			return WM_E_LEN;
		rl = wm_le32(p + WM_BSS_LENGTH);
		if (rl < WM_BSS_MIN_LEN || rl > left)
			return WM_E_LEN;
		if (wm_le32(p + WM_BSS_VERSION) != WM_BSS_VERSION_109)
			return WM_E_VER;
		if (p[WM_BSS_SSID_LEN] > WM_BSS_SSID_MAX)
			return WM_E_LEN;
		for (k = 0; k < 6; k++)
			out[i].bssid[k] = p[WM_BSS_BSSID + k];
		out[i].ssid_len = p[WM_BSS_SSID_LEN];
		for (k = 0; k < WM_BSS_SSID_MAX; k++)
			out[i].ssid[k] = (k < out[i].ssid_len) ? p[WM_BSS_SSID + k] : 0;
		out[i].chanspec = wm_le16(p + WM_BSS_CHANSPEC);
		out[i].rssi = (int16_t)wm_le16(p + WM_BSS_RSSI);
		out[i].channel = p[WM_BSS_CTL_CH];
		p += rl;
		left -= rl;
	}
	*count = cnt;
	return WM_OK;
}

#endif /* WLAN_MSGBUF_H */
