/*
 * BCM4389 shared-structure init and first DMA, run G2 (xnu patch 0174, boot-arg sweetwlan=12 = level 11 + G2): the pure part, host tested by shim/wlan_g2_host_test.c (mutants: python3 -B tools/test_wlan_wr_mutants.py --g2). It runs INSIDE W17 of sweet_wlan_wr.h (swl_w17 calls the glue's hook after swl_f3, while the CA7 still runs, and re-halts it with swl_restore right after the hook returns: R89 s.7 S7). It decides which word of the dongle's shared structure gets which value, in
 * Android's order (R94 s.2), reads every word back, polls briefly and undoes its two Command stores (R89 s.5 steps 1 and R106 s.3; the CA7 re-halt and cfg 0x84 := 0 are swl_restore's rows SWW_RG_CA and SWW_RG_W84, which W17 runs next whatever G2 returned). No asm and no device access of its own: every read and every store goes through struct swg_bus, the one store callback only from swg_put(). The glue implements that bus with its own G2 branch of sw3_wr (an independent exact-word list it recomputes from its own reads, R107 #5), so a store that both lists do not name does not happen.
 *
 * W-D4b (SPEC-WIFI rows Q1-Q8, R-D4b = docs/research/W-D4b-first-post.md; sweetwlan=13: swg_wx.d4b = 1, read-only observation (the ring-2 words after hostready; no new store class: the extra reads go through swg_win/sw_g2_bar0, i.e. cfg 0x84 / 0x80 window moves to the values the G2 list already had, review r1 F4); sweetwlan=14: d4b = 2, set by the glue): after the hostready store and the W-D4a poll, ONE ring-0 event-buffer post: mailbox mask PCIe core +0xC34 := 0xFFFF, the 40 B item in the G1 block, H2D write-index slot 0 := 1, doorbell PCIe core +0x140, a read-only poll, then 0xC34 := 0 FIRST in the undo (swg_undo). Host-block words go through struct swg_bus as space SWG_SP_HOST (offset = byte offset from the block start); the glue flushes the cache line around every host read/store. Not run on hardware; reviews 0174-w-d4b-hwwrite-r1 + r2 (conditions fixed).
 *
 * Sources: R89 = docs/research/89-wlan-g2-first-dma-spec.md (s.1 start state [obs], s.2 offsets [src bcmpcie.h], s.3 table A, s.4 poll, s.5 stop order); R91 = 91-wlan-g2-implementer-brief.md amendments 1-8; R94 = 94-golden2-cold-dhd-open.md s.2 (Android's dhd_open stores and values: these win); R106 = the RC Command store; R107 = design decisions 1-7; SPEC-WIFI D-1..D-9, C5..C8.
 *
 * Deviations from the notes, each deliberate (M1-M5 in the patch header too):
 * M1 no SRPWR request/release: G2 stores into dongle RAM and host memory only; the entry requires the SRPWR request + status bits as read [guess: R89 s.1 saw 0x30300 after the re-halt].
 * M2 the RC Command := 0x0507 is stored after the shared structure and before the EP Command (R106 s.3 order).
 * M3 the stores are rows of swg_g2[] (its own table, R107 #1), not of swl_wt[].
 * M4 every 64-bit host address is stored lo then hi from the live G1 plan (the golden only ever stored hi 0: R100 F7).
 * M5 16-bit golden stores are 32-bit here (R107 #2, D-9): max_item|len_items as one word (old 0), each index slot as a read-modify-write that keeps the upper half, the EP Command as 0x00000002/0x00000006 (Status half 0, W1C). One WIFI GAP line says so. At sweetwlan=14 (d4b 2) the ten index-slot zero stores are 16-bit (swg_put16, bus->wr16: *(volatile uint16_t *) like ml_io_write16, xnu osfmk/kern/machine.c:1050-1051) with a 32-bit read-back; the gap then names what stays 32-bit.
 * cfg 0x88 |= 0x300 (golden seq 423) is NOT stored by G2 (SPEC-WIFI D-12, review 0174-w-d4b-hwwrite-r1 F1): DHD makes it with the ARM halted (dhd_pcie.c:2224-2239), so it is stage-3 row W15 of sweet_wlan_wr.h (sweetwlan=15 only); the w15 gap stays printed below 15.
 * G2 also reads (no store) the words F1/F2 could not (swg_dump) and resolves their gaps (swl_miss_resolve); a gap prints only while its condition is true.
 */
#ifndef SWEET_WLAN_G2_H
#define SWEET_WLAN_G2_H

#include <stdint.h>
#include "sweet_wlan_wr.h"
#include "sweet_wlan_dma.h"
#include "sweet_wlan_d4b.h"

#define SWG_FLAGS2_OLD   0x40943041u    /* R89 s.1 [obs] F3, R94 step 1: the firmware's flags2 */
#define SWG_FLAGS2_BIT   0x80000000u    /* PCIE_SHARED2_H2D_D11_TX_STATUS, R94 step 1 (dhd_pcie.c:15380-15402) */
#define SWG_HOST_CAP     0x10823407u    /* R94 step 2 (bcmpcie.h:440-466 bits) */
#define SWG_SCRATCH_LEN  8u             /* R94 step 11, DMA_D2H_SCRATCH_BUF_LEN */
#define SWG_HOSTREADY    0x12345678u    /* R94 step 12, R89 A18 */
#define SWG_HOSTREADY_OFF 0x144u        /* PCIe core +0x144 (PCIH2D_DB1), R94 step 12 */
#define SWG_SRPWR_OFF    0x1E8u         /* PCIe core +0x1E8 pwrctl, R89 s.1 srpwr */
#define SWG_RC_CMD       0x00000507u    /* R106 s.2, R100 F4: the golden bridge end state */
#define SWG_CMD_OFF      0x04u          /* PCI_COMMAND (EP config space; RC DBI space R106 s.2) */
#define SWG_CMD_MASK     0x0000FFFFu
#define SWG_EP_CMD_MEM   0x00000002u    /* D-1: 0x2 then 0x6 */
#define SWG_EP_CMD_DMA   0x00000006u

/* pciedev_shared_t (R89 s.2, bcmpcie.h:344-433) */
#define SWG_SH_FLAGS     0x00u
#define SWG_SH_H2D_MB    0x28u
#define SWG_SH_D2H_MB    0x2Cu
#define SWG_SH_RINGINFO  0x30u
#define SWG_SH_SCR_LEN   0x34u
#define SWG_SH_SCR_LO    0x38u
#define SWG_SH_FLAGS2    0x50u
#define SWG_SH_HOSTCAP   0x54u
#define SWG_SH_TRAP      0x58u
/* ring_info_t (R89 s.2, bcmpcie.h:305-339): ringmem_ptr, the four TCM index array pointers, the four host addresses (8 B each), the u16 counts */
#define SWG_RI_RINGMEM   0x00u
#define SWG_RI_IDXPTR    0x04u          /* +0x04 h2d_w, +0x08 h2d_r, +0x0C d2h_w, +0x10 d2h_r */
#define SWG_RI_HOSTADDR  0x14u          /* +0x14 h2d_w, +0x1C h2d_r, +0x24 d2h_w, +0x2C d2h_r (lo, hi) */
#define SWG_RI_QUEUES    0x36u          /* u16 max_submission_queues: the high half of the +0x34 word ([obs] 0x002b0028) */
#define SWG_RI_MAXCPL    0x38u          /* u16 max_completion_rings ([obs] 4) */
#define SWG_RI_SIZE      0x4Cu          /* ringmem_ptr == ring_info + 0x4C ([obs], R89 s.7 S3 sanity rule) */
/* ring_mem_t, 16 B per ring (R89 s.2, bcmpcie.h:290-297) */
#define SWG_RM_N         5u
#define SWG_RM_STRIDE    16u
#define SWG_RM_MAX       0x04u          /* max_item | len_items << 16 */
#define SWG_RM_BASE      0x08u
#define SWG_MAX_SUB      43u            /* R89 s.1 [obs] */
#define SWG_MAX_CPL      4u
#define SWG_IDX_N        4u             /* index arrays, in SWD_IDX_H2D_W.. order */
#define SWG_IDX_SZ       4u             /* rw_index_sz: flags 2BYTE_INDICES clear (R89 s.1), = SWD_RW_INDEX_SZ */
#define SWG_IDX(k)       ((uint32_t)(k) - SWD_IDX_H2D_W)        /* SWD_IDX_* (enum 6..9) -> 0..3 */
#define SWG_FLAGS_ASSERT 0x200u
#define SWG_FLAGS_TRAP   0x400u
#define SWG_FLAGS_VER    0xFFu
#define SWG_FLAGS_2BYTE  0x00100000u    /* PCIE_SHARED_2BYTE_INDICES, bcmpcie.h:78 (refs/wlan-stage5): must be CLEAR, G1 planned rw_index_sz 4 (dhd_pcie.c:13771) */
#define SWG_SH_CONSOLE   0x14u          /* console_addr (bcmpcie.h:366, SWW_SH_CONSOLE) */
#define SWG_CON_BUF      0x08u          /* hnd_cons_t: log buffer address, size, write index at +8, +12, +16 (SWW_CON_BUF/SWW_CON_IDX, brcmfmac-pcie.c:248-250) */
#define SWG_CON_MAX      2048u          /* bytes of console text G2 prints at most (the last ones before the write index) */
#define SWG_CON_LINE     120u           /* characters of one printed console line */
#define SWG_RI_WORDS     19u            /* ring_info: SWG_RI_SIZE / 4 words, all printed */
#define SWG_VER          7u             /* R89 s.1 [obs] flags 0x70050107 */
#define SWG_SRPWR_REQ1   0x200u
#define SWG_SRPWR_ST1    0x20000u
#define SWG_PCIE_STATUS_OFF 0xC30u      /* PCIe core +0xC30: mailbox int status, read only (W-D4a s.0 table: C:507-521, golden seq 446 `0xc30 R 0x1`), same BAR0 PCIe-core page as hostready +0x144 */
#define SWG_D2H_FWHALT   0x10000000u    /* D2H_DEV_FWHALT, bcmpcie.h:528; DHD stops on it (dhd_pcie.c:12901, 13156) */
#define SWG_D2H_SLOTS    3u             /* d2h write-index slots G2 stored: rings 2, 3, 4 = slots 0..2 of the d2h_w array (the SWG_RING table above) */
#define SWG_POLLS        20u            /* [guess] budget, R89 s.4 */
#define SWG_POLL_MS      100u
#define SWG_MBX_MASK_OFF 0xC34u         /* PCIe core +0xC34: mailbox interrupt mask [src SPEC-WIFI W-D4b Q2: golden seq 434-436, read 0, write 0xFFFF, read back, MATCH] */
#define SWG_MBX_MASK_ON  0xFFFFu
#define SWG_DB0_OFF      0x140u         /* PCIe core +0x140: H2D doorbell 0 [src SPEC-WIFI W-D4b Q5: dhd_msgbuf.c:116, 11601; golden first write class MATCH] */
#define SWG_DB0_VAL      0xFF000001u    /* [src SPEC-WIFI W-D4b Q5 / W-D4b brief 2026-10-29] */
#define SWG_D4_REQID     1u             /* request_id of the one post [src W-D4b brief 2026-10-29] */
#define SWG_D4_EPOCH     1u             /* epoch of the first item of a fresh ring, and the first D2H epoch expected [src R-D4b s.1, s.3] */
#define SWG_R2_DEPTH     SWD_R2_ITEMS   /* sprint 2026-11-08: from sweet_wlan_dma.h (64); ring-2 depth in items [src 0167 swd_rings: 64 x 24 B, W-D4d s.0]; the D2H-RD / D2H-WR slots hold the slot index < 64, the epoch counts absolute items */
#define SWG_DB_WR        0xFF000000u    /* doorbell 0 after a ring-0 write-index update: | wr [src M:116 (DHD_WRPTR_UPDATE_H2D_DB_MAGIC)] */
#define SWG_DB_RD        0xDD020000u    /* doorbell 0 after a ring-2 read-index update: | rd [src M:114, 7524-7532] */
#define SWG_D4_DS_MAX    4u             /* 22b: the store budget (the glue allows 3 ring-0 items per extra round; the hold round posts 2) the glue restates (SW_G2_DS_MAX, asserted equal); the header no longer counts rounds: the DS hold allows ONE extra round, a second request is a STOP (swg_ds_front), so the budget is never reached */
/* W-D4e scan24 (sweetwlan 21..23; docs/research/W-D4e-scan24-design.md = DS, 108 = R108, M = refs/wlan-stage5/dhd_msgbuf.c, MB = bcmmsgbuf.h, WI = wlioctl.h.new, WD = wlioctl_defs.h.new, BE = bcmevent.h.new, BW = ~/pixelios-artifacts/bwfm-host, LX = refs/wlan-stage1/bcm4389-dhd_linux.c) */
#define SWG_SH_RXBUFS    0x22u          /* patch 0357: u16 max_host_rxbufs, the second u16 of the word at +0x20 (after u16 total_lfrag_pkt_cnt at +0x20) [src bcmpcie.h:373-374; Linux v6.6 pcie.c:223 BRCMF_SHARED_MAX_RXBUFPOST_OFFSET 34] */
#define SWG_SH_RXOFF     0x24u          /* pciedev_shared dma_rxoffset: 8 u32 (flags, trap, assert_exp, assert_file, assert_line, console, msgtrace, fwid) + u16 total_lfrag_pkt_cnt + u16 max_host_rxbufs [src bcmpcie.h:355-376, counted; console_addr 0x14 is the observed SWG_SH_CONSOLE] */
#define SWG_E_RID_BUFS   8u             /* ioctl-response buffers posted in all (L17..L21), one per SWD_RESPBUF_N slot [src M:153, 4286 DHD_FLOWRING_MAX_IOCTLRESPBUF_POST 8] */
#define SWG_E_EV_BUFS    8u             /* event buffers posted in all (L14 + 7 in L21), one per SWD_EVBUF_N slot; DHD posts 32 [src M:152, 4283-4286; 108 s.4 step 1 ">= 8"]: a WIFI GAP */
#define SWG_E_RID_RE0    12u            /* request_id of the first L22 re-post of a response buffer (ids 2..11 are the 10 posts of L17..L21); our own ids [src M:6355] */
#define SWG_E_EVID0      0x20u          /* request_id of event post n >= 1 = SWG_E_EVID0 + n (L14's post n = 0 keeps SWG_D4_REQID 1); our own ids, never 0 (DHD_PKTID_INVALID, M:2278) */
#define SWG_E_TRANS0     (SWG_D4D_TRANS0 + 3u)    /* transaction id of the n-th L22/L23 request = SWG_E_TRANS0 + n (L18 used TRANS0 + 1, L20 + 2) */
#define SWG_E_MAX        64u            /* E_MAX: event items consumed per boot at most (DS s.1 item 1: ring-0 index <= 97 + 12 < 128, no wrap) [decision] */
#define SWG_E_BUDGET_MS  8000u          /* the L23 poll for the scan [decision, run plan L23: <= 8 s] */
#define SWG_E_SYNC       0x1234u        /* escan sync_id [src BW bwfm.c:2229 (0x1234); 108 s.4 step 5] */
#define SWG_E_BATCH      16u            /* ring-2 items judged per batch: more than 8 event buffers + ack + completion + one DS request can ever be pending [decision] */
#define SWG_E_CLMCHUNK   1400u          /* L22c (T-0f36): bytes of CLM per clmload request, MAX_CHUNK_LEN [src 108 s.9.1: LC fwil_types.h:185, BW bwfmreg.h:795] */
#define SWG_E_CLMMAX     12u            /* L22c: chunks at most (the packed blob 15,932 B = 11 x 1400 + 532 is 12); more is a STOP, the glue list has no more [src 108 s.9.1] */
#define SWG_E_MAX_CLM    48u            /* L22c: event items consumed per boot at most under sweetwlan=24. The 12 chunks + clmload_status + country add 14 requests = 28 ring-0 items; 97 - (64 - 48) + 28 + 12 DS items = 121 < 128 (ring-0 depth), the 64 of the other levels would reach 137 and hit the ring-0 post guard (a hard STOP) [computed]. L24 (T-857c review F4): at the event cap's maximum (npartial 45) L24 ends at ring-0 index 124 with 6 commands and 126 with 7; an 8th command would reach 128 and hit the guard [observed, host probe] */
#define SWG_E_PAYW       355u           /* words of the payload shadow: the longest payload is a clmload chunk, 8 ("clmload" NUL) + 12 (wl_dload_data header) + 1400 = 1420 B = 355 words (L22c); before it the escan: 86 B = 22 words, 102 B = 26 words at sweetwlan=24 (T-0fdd) [src 108 s.9.1] */
#define SWG_J_BUDGET_MS  10000u         /* L24: the wait for the join events SET_SSID and LINK [decision, TRANSPORT block s.2: no source; the scan budget SWG_E_BUDGET_MS is 8000] */
#define SWG_Q_QUIET_MS   500u           /* L24 W-A [guess, T-ff10 proof W1]: ring 2 must show no new item for this long before the first L24 command (the dongle was still active ~2 ms after the last escan event when the first L24 doorbell wedged) */
#define SWG_Q_CAP_MS     3000u          /* L24 W-A: give up waiting for quiet after this long (a gap, then the commands go on) */
#define SWG_HB_POLLS     40u            /* L24 W-C: heartbeat lines per ring-2 wait (one per poll, 100 ms apart): a dead host shows as the lines stopping, a dead log path as their kernel stamps ending */
#define SWG_J_NCMD       6u             /* L24: H1..H5 = sup_wpa, wpa_auth, wsec, auth, mfp, join (swj_join_cmds) */
#define SWG_J_UNSUPP     (-23)          /* BCME_UNSUPPORTED as the dongle answers it (T-5323 boot: sup_wpa SET and GET) [obs] */
#define SWG_J_NCMAX      8u             /* L24: at most this many commands (SWG_J_NCMD plus a key iovar or two); the last one is always the join */
#define SWG_E_EV_SET_SSID 0u            /* WLC_E_SET_SSID [src BE:113 (S5/bcmevent.h.new, read 2026-11-02)] */
#define SWG_E_EV_LINK    16u            /* WLC_E_LINK [src BE:129] */
#define SWG_E_LINK_UP    0x01u          /* WLC_EVENT_MSG_LINK, bit of the event flags: the link is up [src BE:50] */
#define SWG_E_LINK_R_DISASSOC 2u        /* WLC_E_LINK reason WLC_E_LINK_DISASSOC "Link down because of disassoc" [src bcmevent.h.new:756] (T-32) */
#define SWG_E_FRAME      72u            /* Ethernet (14) + bcm ether header (10) + wl_event_msg (48), the event data follows [src 72 Q1 table: BW bwfmreg.h:750-790; BE:74-87] */
#define SWG_E_ETYPE      0x886Cu        /* ethertype of a BCM event frame, frame +12 big endian [src BW bwfmreg.h:781, bwfm.c:2372] */
#define SWG_E_EV_ESCAN   69u            /* WLC_E_ESCAN_RESULT [src BE:180] */
#define SWG_E_ST_OK      0u             /* WLC_E_STATUS_SUCCESS [src BE:412] */
#define SWG_E_ST_PARTIAL 8u             /* WLC_E_STATUS_PARTIAL [src BE:420] */
#define SWG_E_BSS_VER    109u           /* WL_BSS_INFO_VER_109, wl_bss_info_v109_t [src WI:347, 355-397; 72 Q2: the version the firmware sends is [guess], the record version is checked and printed] */
#define SWG_E_BSS_RMAX   1184u          /* 2026-11-11 scan records: most record bytes the KEEP-phase scan reads of one PARTIAL result = 160 (the fixed wl_bss_info_v109 part is 132 B, ie_offset follows it) + 1024 (the iOS record's IE capacity, W-D3 s.1 +0x8a); a host read of the event buffer only. The IEs the radio keeps are 512 B (SWSTA_IE_CAP); the rest is read to see a security IE past that cap. */
#define SWG_E_BSS_NEED   89u            /* bytes of a wl_bss_info_v109 record read: through ctl_ch (offset 88) [src 72 Q2 offsets, WI:355-381] */
#define SWG_D4_ITEMW     10u            /* the 40 B item as u32 words (SWD4B_ITEM_LEN / 4) */
/* W-D4c (sweetwlan=16) inband device-wake sequence, docs/research/W-D4c-ds-ack.md s.6.5 steps 3-7. The golden ring-0 items start at index 0 (ff000001); G2 L14 posts the event buffer at index 0 first, so every golden doorbell below is +1 [obs golden TW4:549990-549992, 550000, 550032-550034 of 20261028T034021Z/trace-wifi4.txt] */
#define SWG_D4C_DB_ACK   0xFF000002u    /* A1: doorbell 0 after the H2D-WR slot 0 := 2 (ring-0 item 1 = H2D_HOST_DS_ACK); value class dhd_msgbuf.c:116; stock ff000001 TW4:549990 */
#define SWG_D4C_DB_RD1   0xDD020001u    /* C1: doorbell 0 after the D2H-RD slot of ring 2 := 1; dhd_msgbuf.c:114, 7524-7532 (RD update AFTER the ack); stock TW4:549992 */
#define SWG_D4C_DB_WAKE  0xFF000003u    /* W: after H2D-WR := 3 (ring-0 item 2 = DEVICE_WAKE_ASSERT 0x40); dhd_pcie.c:12255-12313 (no exit wait, OSL_DELAY(1000)); stock ff000002 TW4:550000 */
#define SWG_D4C_DB_DEAS  0xFF000004u    /* D: after H2D-WR := 4 (ring-0 item 3 = DEVICE_WAKE_DEASSERT 0x20) on the EXIT_NOTE; dhd_pcie.c:13036-13086, 12340-12345; stock ff000003 TW4:550032 */
#define SWG_D4C_DB_RD2   0xDD020002u    /* C2: after the D2H-RD slot of ring 2 := 2, last (dhd_msgbuf.c:7524-7532); stock dd020002 TW4:550034 */
#define SWG_D4C_EP_ACK   2u             /* epochs of ring-0 items 1..3: (254 + index) % 253 [src bcmmsgbuf.h:38-39, dhd_msgbuf.c:12400-12402] */
#define SWG_D4C_EP_WAKE  3u
#define SWG_D4C_EP_DEAS  4u
#define SWG_D4C_EP_NOTE  2u             /* epoch of ring-2 item 1, the DS_EXIT_NOTE (item 0 had epoch 1) */

_Static_assert(SWD_IDX_D2H_R - SWD_IDX_H2D_W == 3, "sweet_wlan_dma.h index array order is h2d_w, h2d_r, d2h_w, d2h_r");
_Static_assert(SWG_IDX_SZ == SWD_RW_INDEX_SZ, "G1 planned the index arrays for this slot size");
_Static_assert(SWD_D2H_RXC - SWD_H2D_CTRL + 1 == SWG_RM_N, "five common rings");

enum { SWG_SP_TCM = 1, SWG_SP_EP, SWG_SP_RC, SWG_SP_PCIE, SWG_SP_WIN2, SWG_SP_HOST, SWG_SP_DATA };   /* backplane address (BAR2) / EP config / RC DBI / BAR0 PCIe core page / cfg 0x84 / W-D4b: byte offset into the G1 host block / DP15: byte offset into the DATA host block (flow ring, tx buffers; swdp_env.data) */

enum { SWG_E_STOPPED = 1, SWG_E_ENTRY, SWG_E_FLAGS, SWG_E_ORDER, SWG_E_ADDR, SWG_E_OLD, SWG_E_WR, SWG_E_READBACK, SWG_E_RCSTUCK, SWG_E_DEAD, SWG_E_FATAL };

static const char *const swg_ename[] = { "ok", "stopped", "entry state", "firmware flags", "order", "address", "old value", "store", "read-back", "rc command", "dead link", "fatal" };

struct swg_bus {
	int (*rd)(void *ctx, int sp, uint32_t a, uint32_t *v);
	int (*wr)(void *ctx, int sp, uint32_t a, uint32_t v);         /* THE store; called only by swg_put() */
	void (*delay_ms)(void *ctx, unsigned ms);
	void *ctx;
	int (*wr16)(void *ctx, int sp, uint32_t a, uint16_t v);       /* the 16-bit store (sweetwlan=14 index slots only); called only by swg_put16(); NULL = none */
	uint64_t (*stack_room)(void *ctx);      /* T-eabd: bytes between the current sp and the bottom of the thread's kernel stack, ~0ull = not readable; read-only, NULL = none (swg_hr then prints `unknown`) */
	/* Part A (sprint-wifi 2026-11-09), both optional (NULL = the per-word path everywhere): nw words of the host or DATA block at a. rd_blk: one cache pass over the range, then the words
	 * (= nw flushing rd calls). wr_blk: THE batched store, called only by swg_put_blk(); the glue judges the batch (link check before and after, the L25 judge's verdict for every word) */
	int (*rd_blk)(void *ctx, int sp, uint32_t a, uint32_t *v, uint32_t nw);
	int (*wr_blk)(void *ctx, int sp, uint32_t a, const uint32_t *v, uint32_t nw);
	/* Part A (perf-d), optional: open = 1 right before the first store of an audited DP15 plan whose LAST store is its doorbell, 0 right after the plan (made or failed). The glue checks the link
	 * once at the open and lets the plan's host/DATA stores skip their own per-store link check; the doorbell (the store that crosses PCIe) keeps its own. Same argument as the batch: a link
	 * lost inside the plan fails it before its doorbell. */
	void (*plan)(void *ctx, int open);
};

/* what W17 and the G1 plan hand in: nothing here is typed, all of it was read (sh, end) or allocated (the PAs) */
struct swg_in {
	uint32_t sh, end;               /* shared structure backplane address (W17's poll), RAM end (SWW_RAM_BASE + ram_bytes) */
	uint64_t idx[SWG_IDX_N];        /* host PAs of the index arrays, SWG_IDX(SWD_IDX_*) order */
	uint64_t ring[SWG_RM_N];
	uint64_t trap, scratch;
	uint64_t blk, evbuf;            /* W-D4b: the G1 block PA and the event buffer PA (SWD_EVBUF); host offsets are PA - blk */
	uint64_t respbuf, ioctbuf;      /* W-D4d (sweetwlan >= 17): the PAs of SWD_RESPBUF and SWD_IOCTBUF in the same block; set by the glue at every G2 level, used at sweetwlan >= 17 */
	uint32_t blk_len;               /* the planned block bytes (swd_plan.total): the host words G2 reads or stores lie inside [blk, blk + blk_len) */
	const uint8_t *clm;             /* L22c (sweetwlan >= 24, T-0f36): the CLM entry of the WLANFW container, set by the glue only after its own CRC32 check against the container entry; NULL = none (the level then STOPs with a gap before any store) */
	uint32_t clmlen;                /* L22c: the bytes at clm */
	uint64_t data;                  /* L25 (sweet_wlan_l25.h): the DATA block PA (flow ring, tx and rx buffers; 0 = the glue allocated none, L25 then refuses loudly) and its bytes */
	uint32_t data_len;
	uint32_t rxarg_set, rxarg;      /* patch 0357: boot-arg sweetwlanrxpost present (any value) and its value; the glue fills them, swl25_env clamps (swdp_rx_pick) */
};

struct swg_rt {
	uint32_t ri, rm, ra[SWG_IDX_N]; /* read at the entry: ring_info, ring_mem, the TCM index array pointers */
	uint32_t mbp;                   /* TCM[shared + SWG_SH_D2H_MB], read once at the entry: the POINTER to the d2h mailbox data (DHD dereferences it: dhd_pcie.c:6713-6715; W-D4a s.0) */
	uint32_t win;                   /* the BAR2 window G2 last selected (0 or SWW_WIN_TAIL) */
	uint32_t rc_old, ep_old, srpwr;
};

/* the words the post-hostready poll compares (W-D4a-1, SPEC C9). Reads only. The host-memory words (d2h write-index slots, ring 2 item 0) are not here: G2 keeps no mapping of the G1 block (swg_base says so). */
struct swg_pw {
	uint32_t fl, mb, dw[SWG_D2H_SLOTS], c30;        /* shared flags, TCM[mbp] (the mailbox DATA), TCM d2h_w array slots 0..2, PCIe core +0xC30 */
};

/* W-D4b Q1/Q6: the read set (reads only): the W-D4a TCM words (flags, mailbox data, tcm d2h_w[0..2], 0xC30) plus H2D-RD slot 0, D2H-WR slot 0 and the first 24 B of ring 2 (host block) */
#define SWG_D4_NV 14u
struct swg_d4s {
	struct swg_pw pw;
	uint32_t h2dr, d2hw, r2[6];
};

/* W-D4b rows: Q2 mask, Q3 the ten item words, Q4 the H2D-WR slot, Q5 doorbell 0, Q8 the unmask (undo only) */
/* W-D4c (sweetwlan=16) rows, between the doorbell and the unmask, in store order (docs/research/W-D4c-ds-ack.md s.6.5): A1 = ring-0 item 1 (DS_ACK; ten words, H2D-WR slot 0 old 1 := 2, doorbell FF000002),
 * C1 = D2H-RD slot of ring 2 old 0 := 1 + doorbell DD020001, W = ring-0 item 2 (DEVICE_WAKE_ASSERT; slot old 2 := 3, FF000003), D = ring-0 item 3 (DEVICE_WAKE_DEASSERT; slot old 3 := 4, FF000004), C2 = RD slot old 1 := 2 + DD020002 */
enum { SWG_D4_MASK, SWG_D4_ITEM0, SWG_D4_SLOT = SWG_D4_ITEM0 + SWG_D4_ITEMW, SWG_D4_DB0,
       SWG_D4C_A_ITEM0, SWG_D4C_A_SLOT = SWG_D4C_A_ITEM0 + SWG_D4_ITEMW, SWG_D4C_A_DB,
       SWG_D4C_C1_SLOT, SWG_D4C_C1_DB,
       SWG_D4C_W_ITEM0, SWG_D4C_W_SLOT = SWG_D4C_W_ITEM0 + SWG_D4_ITEMW, SWG_D4C_W_DB,
       SWG_D4C_D_ITEM0, SWG_D4C_D_SLOT = SWG_D4C_D_ITEM0 + SWG_D4_ITEMW, SWG_D4C_D_DB,
       SWG_D4C_C2_SLOT, SWG_D4C_C2_DB, SWG_D4_UNMASK, SWG_D4_N };
#define SWG_D4_ISDB(r) ((r) == SWG_D4_DB0 || (r) == SWG_D4C_A_DB || (r) == SWG_D4C_C1_DB || (r) == SWG_D4C_W_DB || (r) == SWG_D4C_D_DB || (r) == SWG_D4C_C2_DB)     /* a doorbell row: never read, no read-back */
#define SWG_D4C_ROWMASK (((1ull << (SWG_D4C_C2_DB + 1)) - 1ull) & ~((1ull << SWG_D4C_A_ITEM0) - 1ull))      /* the d4c rows A1..C2 (22b: cleared at the start of an extra DS round) */
_Static_assert(SWD4B_ITEM_LEN == 4u * SWG_D4_ITEMW, "the item is ten u32 words");
_Static_assert(SWG_D4_N <= 64, "swg_wx.d4mask is 64 bits");

/* DP15 (T-75c7): the host's shadow of the words it last wrote into a wrapping region (ring 1, the flow ring, the tx buffers); the old value of the next store there. The glue owns the arrays (nw words from pa) */
#define SWG_DP_NSH 3u
#define SWG_DP_SHADOW_ONES_OK 1  /* sprint 2026-11-08: swg_d4d_st accepts an all-ones old word when the DP15 shadow holds all ones (wlan_en0_bus.h turns its guard off) */
struct swg_dp_sh {
	uint64_t pa;
	uint32_t nw;
	uint32_t *w;
};

struct swg_ej {                 /* L24: what the join has delivered */
	int ssid_seen, link_seen, link_up;
	uint32_t ssid_status, link_flags;
};

struct swg_jcmd {               /* one join command, copied by the glue from struct swj_cmd (sweet_wlan_join.h; the host test builds it the same way): cmd, the row name ("H1".."H5"), buf = name NUL + value, len = bytes used */
	uint32_t cmd;
	const char *row;
	uint8_t buf[4u * SWG_E_PAYW];
	uint32_t len;
};

struct swl25_cfg; struct swg_ev;        /* sweet_wlan_l25.h; the event record below */

struct swg_wx {
	const struct swg_bus *b;
	struct swg_in in;
	struct swg_rt rt;
	struct swg_pw base;             /* taken by swg_base() immediately before the hostready row */
	unsigned step, nstore;
	int stopped, code, restore, entered;   /* entered: swg_entry passed (no row before it) */
	const char *fail;               /* what a refusal meant, in one line */
	uint32_t mask, tried, old, rb;  /* mask: rows done (read back); tried: rows whose store was attempted (the undo keys on it) */
	int d4b;                        /* W-D4b gate, set by the glue: 0 none, 1 (sweetwlan=13) read-only observation, 2 (sweetwlan=14) observation + the post, 3 (sweetwlan=16) + the DS_ACK (W-D4c) */
	uint64_t d4mask, d4tried;       /* W-D4b rows done (read back) / stored (swg_put returned 0; review r2 F11) (SWG_D4_*); the unmask keys on d4tried */
	int d4unm;                      /* the unmask was attempted (once) */
	uint8_t d4sleep;                /* W-D4c (review r2 F1): 1 from the ack doorbell store (A1) to the wake doorbell store (W): the dongle may be in DEV_SLEEP, a stop in between runs the undo and the W17 restore against it */
	uint32_t d4w[SWG_D4_ITEMW];     /* the item words, from swd4b_item_evbuf */
	uint32_t d4cw[3][SWG_D4_ITEMW]; /* W-D4c: the words of ring-0 items 1..3 (DS_ACK, DEVICE_WAKE_ASSERT, DEVICE_WAKE_DEASSERT), from swd4b_item_mbdata */
	struct swg_d4s d4base;
	int pins_ok, rd_mb, rd_ri, rd_con, hostrd;      /* W-D4b gaps: flags 2BYTE clear + counts verified; the F1/F2 words G2 read (mailbox data, rings_info, console); the D4b host read ran */
	uint32_t con_n;                 /* console bytes printed by swg_dump */
	uint32_t d4dn;                  /* W-D4d: rows stored so far (the index into swg_d4d_seq) */
	uint8_t d4dstop;                /* W-D4d: a gap stopped the remaining levels (no further store) */
	uint8_t d4dlv;                  /* W-D4d: the highest level (17..20) that finished without a STOP, 0 = none */
	uint8_t d4c_ok;                 /* the L16 handshake completed (ack, wake, exit note, deassert, C2): W-D4d needs it */
	uint32_t d4c_wr, d4c_rd;        /* W-D4c / DS rounds (22b): the ring-0 write index and the ring-2 read index (ABSOLUTE item count) at the start of the round being made; round 0 (L16) is 1 and 0 */
	uint32_t r0sh[128u * 10u];      /* sprint 2026-11-08: ring-0 shadow, the word each slot holds (what the engine last stored; 0 = never written: the ring memory starts zeroed). swg_wx is static in the kernel glue and zeroed per call (T-eabd) */
	uint8_t quiet;                  /* sprint 2026-11-08: 1 = the per-store success lines of swg_d4d_st / DP15 are not printed (a long-lived en0 makes ~33 lines per frame); every failure still prints; nquiet counts what was skipped */
	uint32_t nquiet;
	uint8_t keep;                   /* sprint 2026-11-08 (boot arg sweetwlankeep=1, set by the glue): after a finished L26 swg_go skips swg_undo and the cfg 0x84 := 0, the dongle stays running and the host keeps the bus (en0 poll thread) */
	uint8_t krefresh;               /* Part A (review perf-c F1/F2): an RSSI refresh ioctl of the keep thread is in progress: the ring-2 wait polls in 1 ms steps (same 2 s budget) and never starts a DS round */
	uint8_t kept;                   /* set by swg_go when keep took effect: no undo stores were made, the EP bus master stays on, the RC Command stays as G2 left it, 0xC34 stays 0xFFFF if D4b stored it */
	uint32_t kr2n, kr2ev, kr2ds, kr2o, kr2c;        /* swg_keep_r2: ring-2 items consumed in total / events / DS_ENTER_REQ / other types / items whose epoch-xor check failed (engine stopped) */
	/* patch 0281 (W-D6 boot A): the keep-phase event decode, the buffer re-post and the link-down detector */
	uint32_t kev_n;                 /* keep-phase WL_EVENT items decoded (printed) */
	uint32_t kev_gap;               /* keep-phase WL_EVENT items that could not be decoded (a WIFI GAP line each) */
	uint32_t kev_rp;                /* keep-phase event buffers re-posted */
	uint32_t kevb[SWG_E_EV_BUFS];   /* buffer slots of keep-phase events consumed, re-post due (kevq entries), in consumption order */
	uint32_t kevq;
	uint8_t klink;                  /* keep link state seen by the detector: 0 = UP (the boot join), 1 = DOWN (a link-loss event was seen; stays DOWN in 0281, UP is patch 0282) */
	uint8_t kl_new;                 /* set by the detector on the UP -> DOWN edge, cleared by the keep thread once it has told iOS (radio, en0) */
	uint32_t kl_cause, kl_status, kl_reason;        /* the event that made the link DOWN */
	uint32_t kl_up_n;               /* link-up events seen while DOWN outside a re-join attempt (a GAP each, not acted on) */
	uint8_t kl_tail;                /* T-32: the trailing LINK down (reason SWG_E_LINK_R_DISASSOC) of a DEAUTH/DISASSOC loss was seen in this DOWN episode (1 also when the cause itself was a LINK down: no tail to wait for) */
	/* patch 0282 (W-D6 boot B): the re-join state machine, driven by the keep thread (sweet_wlan_l25_hs.h swl25_rj_step); the events set the flags here */
	uint8_t rj_state;               /* SWG_RJ_UP / DOWN / JOINING / KEYING; the detector sets DOWN, only swl25_rj_* move it on */
	uint8_t rj_ssid, rj_link;       /* JOINING: SET_SSID status 0 / LINK with the up flag seen since the join was sent */
	uint32_t rj_ev_n;               /* events noted while an attempt was in progress (logged) */
	uint8_t rj_lost;                /* review 0282 F3: the AP link was lost (DEAUTH / DISASSOC / LINK down) while JOINING or KEYING: the attempt fails, never UP on a dead link */
	uint8_t rj_off;                 /* review 0282 F1: a soft stop inside a re-join ioctl turned the re-join off for the rest of the boot (the keep engine goes on) */
	uint8_t esec_left, esec_done;   /* review 0282 F5: a secret payload may still sit in the DMA ioctl buffer / its completion was consumed (the scrub store is legal only then) */
	uint32_t d4wrap0;               /* sprint 2026-11-08: ring-0 wraps so far; the absolute ring-0 item count (the H2D epoch) is d4wrap0 * depth + d4wr [src DHD seqnum never resets at a wrap, sweet_wlan_data.h:17] */
	uint32_t d4wr, d4rd;            /* 22b: the host's ring-0 write slot (0..depth-1, wraps since sprint 2026-11-08; d4wrap0 counts the laps) and of ring-2 items consumed (absolute: the slot is d4rd % 64, the D2H epoch follows the absolute count, dhd_msgbuf.c:1491, 1517-1519) */
	uint8_t d4ds;                   /* 22b: extra DS rounds answered after the L16 one (0 or 1: the extra round is the DS hold, then dshold) */
	uint8_t dshold;                 /* W-D4 DS hold (research 124 s.4): 1 after a hold round (DS_ACK + wake assert + exit note, NO deassert: deep_sleep 0 equivalent); a DS_ENTER_REQ after it is a loud STOP */
	uint8_t eop;                    /* W-D4e (L21..L23): the ring-0/payload operation being made (SWG_OP_*), 0 = none: the kind of its next store row is fixed by swg_e_kind() */
	uint8_t elv;                    /* W-D4e: the level (21..23) being run, for the STOP lines */
	uint8_t hr_seen;                /* T-eabd: bit per swg_hr site already printed once */
	uint32_t hr_min;                /* T-eabd: smallest stack headroom seen so far (valid when hr_n != 0) */
	uint32_t hr_n;                  /* T-eabd: readable headroom samples taken */
	uint8_t l24db;                  /* T-c3ac: first L24 doorbell done (the PCIe probe pair ran once) */
	uint8_t l24spin;                /* T-c3ac: 1 = the next ring-2 poll delay is a per-ms spin on the host word (H2/W5), cleared by it */
	uint8_t emajor;                 /* W-D4e: scan_ver scan_ver_major of the L22 answer (2 or 3), 0 = not read */
	uint32_t erow;                  /* W-D4e: rows made so far in the current operation */
	uint32_t ncpl;                  /* W-D4e: ioctl completions consumed so far (L18 and L20 are 0 and 1) */
	uint32_t rpid[SWG_E_RID_BUFS];               /* W-D4e (T-af77): the request_id (pktid) of the ioctl-response buffer now posted in each of the SWG_E_RID_BUFS slots, 0 = none posted; a completion carries the pktid of the buffer the dongle chose [src R-108 s.2.1 line 55], any posted one */
	uint32_t evp, evn, evq;         /* W-D4e: event buffers posted (L14 is the first), event items consumed, consumed events whose buffer is not re-posted yet (deferred until after the escan START) */
	uint32_t epid[SWG_E_EV_BUFS];   /* W-D4e (T-fad2): the pktid of the event buffer now posted in each of the SWG_E_EV_BUFS slots, 0 = none posted; an event item carries the pktid of the buffer the dongle chose (boot 8 of T-fad2: 0x27, the newest of 1,0x21..0x27, first), any posted one is accepted, as the response buffers since T-af77 */
	uint32_t evqb[SWG_E_EV_BUFS];   /* W-D4e (T-fad2): the buffer slots of the consumed events whose re-post waits, in consumption order (evq entries) */
	uint32_t rxfw, rxfw_state;      /* patch 0357: pciedev_shared max_host_rxbufs (u16 at shared +0x22 = Linux BRCMF_SHARED_MAX_RXBUFPOST_OFFSET 34), read once at the D4e entry; rxfw_state = SWDP_RXFW_* (sweet_wlan_data.h) */
	uint32_t rxoff;                 /* W-D4e: pciedev_shared dma_rxoffset (shared +0x24): the event frame starts that many bytes into the event buffer (dhd_msgbuf.c:8290-8291) */
	uint32_t epay[SWG_E_PAYW];      /* W-D4e: the words of the ioctl payload buffer as the host has stored them (the old value of the next payload store) */
	uint32_t nbss, nchan[8];        /* W-D4e L23: bss records printed; bitmap of the control channels seen */
	uint32_t ereq;                  /* W-D4e: ioctl requests made by L22/L23 (the transaction id is SWG_E_TRANS0 + ereq) */
	uint32_t emax;                  /* L22c: the cap of event items consumed (SWG_E_MAX, SWG_E_MAX_CLM under sweetwlan=24) */
	struct swg_dp_sh dpsh[SWG_DP_NSH];      /* DP15: shadow regions (old value of ring-1 / flow-ring / tx-buffer words); ring 0 never wraps, its old word is 0 */
	uint16_t dppay, dpitem;         /* DP15: payload words and item words of the plan being made (they fix the kind sequence, swg_dp_kind) */
	uint32_t dpzero;                /* DP15: the shadow of a ring-0 word (always 0: ring 0 never wraps) */
	uint8_t dpx, dpsec;             /* DP15: a plan is being made (index slots compare the low 16 bits; frame words are never printed) / it is a secret plan (every store line withholds the value) */
	struct swg_ej *ej;              /* L24: while non-NULL, swg_e_items judges SET_SSID / LINK events into it */
	int esec;                       /* L24: the payload being stored (and the shadow it replaces) carries a secret: its store lines print no value */
	const struct swg_jcmd *jc; uint32_t njc;        /* L24: the join commands H1..H5 (swj_join_cmds output, built by the glue from the credentials blob), jc[0..njc) */
	const struct swl25_cfg *l25c;   /* L25/L26 (sweet_wlan_l25.h): the join context the glue delivers (station MAC, AP BSSID, PMK, RSNE, addresses); NULL = L25 prints `WIFI GAP: L25 not run` and stops */
	int (*bssget)(struct swg_wx *);        /* patch 0288: L25 hook (sweet_wlan_l25_glue.h swl25g_bss_info): the target record of a join whose AP the scan never saw, from WLC_GET_BSS_INFO; NULL = none */
	int (*bssn)(struct swg_wx *, const struct swg_ev *, uint64_t, uint32_t, uint32_t); void *bssc;       /* L23 hook (sweet_wlan_l25_rsne.h, T-37d8): called with each parsed bss record (event buffer PA, record offset, bytes of the record); NULL = none */
	/* patch 0292 (WIFI-COVERAGE D3/D4, Settings > Wi-Fi lists networks): the keep-phase scan (sweet_wlan_l25_hs.h swl25_sc_*). While sc_on is set, swg_ke_event hands the first bytes of every ESCAN_RESULT event of our sync id (status, the 12 B wl_escan_result header + the first
	 * SWG_E_BSS_NEED record bytes of a PARTIAL one) to scansink; it never reads more of the event buffer than swg_e_event does at L23. No firmware command, no store. */
	uint8_t sc_on;                  /* a keep-phase scan is collecting */
	uint8_t sc_complete;            /* ESCAN_RESULT status SUCCESS (0) of our sync id seen since sc_on was set */
	uint32_t sc_ev, sc_part, sc_other;      /* ESCAN_RESULT events of our sync id seen / of those with status PARTIAL / events of another sync id or with another status (counted, GAP) since sc_on was set */
	void (*scansink)(void *cx, uint32_t status, const uint8_t *res, uint32_t len); void *scancx;     /* NULL = none: an ESCAN_RESULT in the keep phase is then decoded and printed like any other event (and counted in sc_stray) */
	uint32_t sc_stray;              /* ESCAN_RESULT events seen in the keep phase while no scan was collecting (late events of a scan that timed out: counted, one GAP line the first time) */
	/* review 0292 F1/F2/F4/F5: escan_sync = the sync_id of the escan START that is being sent or runs (SWG_E_SYNC for the boot scan; the keep scan takes SWG_E_SYNC + 1 + (n - 1) % 255, one per scan, so a late event of an old scan can not end a new one); the judge (swl25w_chk_escan) accepts exactly this value in SWG_E_SYNC..SWG_E_SYNC + 0xFF.
	 * sc_foreign = events of ANOTHER sync id seen while collecting (GAP on the first and every 64th, the count goes into the scan-end line). sc_termd/sc_term = a firmware end status of OUR sync id other than SUCCESS / PARTIAL (abort 4, new scan 9, new assoc 10 ...): the scan ends at once.
	 * sc_await = a link loss ended the scan: the first end status (anything but PARTIAL) of escan_sync after that is looked for in the stray branch and sets sc_await_done; the re-join waits for it, at most SWL25_SC_HOLD_MS. */
	uint16_t escan_sync;
	uint8_t sc_termd, sc_await, sc_await_done;
	uint32_t sc_foreign, sc_term;
	uint8_t scfr[12u + SWG_E_BSS_RMAX];      /* 2026-11-11: the bytes of one keep-phase ESCAN_RESULT (12 B header + the record) handed to scansink; per engine (static in the kernel, T-eabd), not on the stack */
};

/* T-eabd (W-JOIN L24, docs/reviews/T-ed1c-proof.md: the L24 end is `Invalid kernel stack pointer (probable overflow)`): read-only stack headroom at a named site. Prints the first sample of each site and every new minimum
 * (`SWEET WLAN: stack headroom <site> <bytes>`); an unreadable sample prints `unknown` once per site and is a gap (Rule 8: no silent skip). No store, no hardware access. */
enum { SWG_HR_HOOK, SWG_HR_D4D, SWG_HR_EITEMS, SWG_HR_ITEM, SWG_HR_L25D, SWG_HR_L25S, SWG_HR_DPX };       /* T-73fc: the L25 sites l25_d2h, l25_send, dp_exec (hr_seen is 8 bits, 7 sites) */
static void
swg_hr(struct swg_wx *x, unsigned site, const char *name)
{
	uint64_t h = x->b->stack_room != NULL ? x->b->stack_room(x->b->ctx) : ~0ull;

	if (h == ~0ull || h > 0xFFFFFFFFull) {
		if ((x->hr_seen & (1u << site)) == 0) {
			x->hr_seen |= (uint8_t)(1u << site);
			swl_gap("g2", "stack headroom not readable", "medium");
			printf("SWEET WLAN: stack headroom %s unknown\n", name);
		}
		return;
	}
	if ((x->hr_seen & (1u << site)) == 0 || x->hr_n == 0 || (uint32_t)h < x->hr_min) {
		printf("SWEET WLAN: stack headroom %s %u\n", name, (unsigned)h);
	}
	x->hr_seen |= (uint8_t)(1u << site);
	if (x->hr_n == 0 || (uint32_t)h < x->hr_min) {
		x->hr_min = (uint32_t)h;
	}
	x->hr_n++;
}

/* ---- the table: every store G2 makes, in Android's order (R94 s.2 steps 1-12, SPEC C5/C6), ONCE each, row n only after row n-1 was read back ---- */
enum { SWG_G_SH = 1, SWG_G_RI, SWG_G_RM, SWG_G_SLOT, SWG_G_CMD };

#define SWG_RING_ROWS(r) SWG_ST_R##r##_BA, SWG_ST_R##r##_MAX, SWG_ST_R##r##_WR, SWG_ST_R##r##_RD
enum { SWG_ST_FLAGS2, SWG_ST_HOSTCAP, SWG_ST_TRAP, SWG_ST_D2HW, SWG_ST_H2DR, SWG_ST_H2DW, SWG_ST_D2HR,
       SWG_RING_ROWS(0), SWG_RING_ROWS(1), SWG_RING_ROWS(2), SWG_RING_ROWS(3), SWG_RING_ROWS(4),
       SWG_ST_SCRATCH, SWG_ST_SCRLEN, SWG_ST_RC, SWG_ST_EP, SWG_ST_READY, SWG_ST_N };

struct swg_st {
	const char *name;
	uint8_t grp, words;     /* words: 1, or 2 for a 64-bit host address (lo, hi) */
	uint8_t arg, ring;      /* RI: the index array (SWG_IDX); RM: the ring; SLOT: the array (arg) and the slot (ring); CMD: the space */
	uint32_t off;
};

/* index slots (R94 step 9): h2d rings 0, 1 are slots 0, 1 of the h2d arrays; d2h rings 2, 3, 4 are slots 0, 1, 2 of the d2h arrays: 10 slots, WR slot then RD slot after each ring's depth */
#define SWG_RING(r, w0, r0, s) \
	[SWG_ST_R##r##_BA] = { "ring" #r " base", SWG_G_RM, 2, 0, r, SWG_RM_BASE }, \
	[SWG_ST_R##r##_MAX] = { "ring" #r " max_item|len_items", SWG_G_RM, 1, 0, r, SWG_RM_MAX }, \
	[SWG_ST_R##r##_WR] = { "ring" #r " wr slot", SWG_G_SLOT, 1, SWG_IDX(w0), s, 0 }, \
	[SWG_ST_R##r##_RD] = { "ring" #r " rd slot", SWG_G_SLOT, 1, SWG_IDX(r0), s, 0 }
static const struct swg_st swg_g2[SWG_ST_N] = {
	[SWG_ST_FLAGS2] = { "flags2", SWG_G_SH, 1, 0, 0, SWG_SH_FLAGS2 },
	[SWG_ST_HOSTCAP] = { "host_cap", SWG_G_SH, 1, 0, 0, SWG_SH_HOSTCAP },
	[SWG_ST_TRAP] = { "host_trap_addr", SWG_G_SH, 2, 0, 0, SWG_SH_TRAP },
	[SWG_ST_D2HW] = { "d2h_w_idx_hostaddr", SWG_G_RI, 2, SWG_IDX(SWD_IDX_D2H_W), 0, 0 },
	[SWG_ST_H2DR] = { "h2d_r_idx_hostaddr", SWG_G_RI, 2, SWG_IDX(SWD_IDX_H2D_R), 0, 0 },
	[SWG_ST_H2DW] = { "h2d_w_idx_hostaddr", SWG_G_RI, 2, SWG_IDX(SWD_IDX_H2D_W), 0, 0 },
	[SWG_ST_D2HR] = { "d2h_r_idx_hostaddr", SWG_G_RI, 2, SWG_IDX(SWD_IDX_D2H_R), 0, 0 },
	SWG_RING(0, SWD_IDX_H2D_W, SWD_IDX_H2D_R, 0),
	SWG_RING(1, SWD_IDX_H2D_W, SWD_IDX_H2D_R, 1),
	SWG_RING(2, SWD_IDX_D2H_W, SWD_IDX_D2H_R, 0),
	SWG_RING(3, SWD_IDX_D2H_W, SWD_IDX_D2H_R, 1),
	SWG_RING(4, SWD_IDX_D2H_W, SWD_IDX_D2H_R, 2),
	[SWG_ST_SCRATCH] = { "scratch addr", SWG_G_SH, 2, 0, 0, SWG_SH_SCR_LO },
	[SWG_ST_SCRLEN] = { "scratch len", SWG_G_SH, 1, 0, 0, SWG_SH_SCR_LEN },
	[SWG_ST_RC] = { "rc command", SWG_G_CMD, 1, SWG_SP_RC, 0, SWG_CMD_OFF },
	[SWG_ST_EP] = { "ep command", SWG_G_CMD, 1, SWG_SP_EP, 0, SWG_CMD_OFF },
	[SWG_ST_READY] = { "hostready", SWG_G_CMD, 1, SWG_SP_PCIE, 0, SWG_HOSTREADY_OFF },
};
#define SWG_SLOTS        10u            /* rows of group SWG_G_SLOT: 2 per ring (R94 step 9) */
#define SWG_IDX_SLOTS    4u             /* slots per array: 16 B arrays (G1 plan, max_completion_rings 4 x rw_index_sz 4) hold the 3 d2h slots */

_Static_assert(SWG_ST_N == 32, "swg_wx.mask is 32 bits: 7 + 5 x 4 + 2 + 3 rows");

/* the RAM word bp may be read or stored: 4-aligned, from the RAM base to the last word below ram_end (R89 s.7 S3) */
static int
swg_ram(const struct swg_wx *x, uint32_t bp)
{
	return (bp & 3u) == 0u && bp >= SWW_RAM_BASE && x->in.end > SWW_RAM_BASE && bp <= x->in.end - 4u;
}

/* the BAR2 window for bp (dhd_pcie.c:6149, 6232: addr & ~(bar size - 1)), selected through the store callback only when it changes */
static int
swg_win(struct swg_wx *x, uint32_t bp)
{
	uint32_t w = bp >= SWW_WIN_TAIL ? SWW_WIN_TAIL : 0u;

	if (w == x->rt.win) {
		return 0;
	}
	if (x->b->wr(x->b->ctx, SWG_SP_WIN2, SWW_CFG_WIN2, w)) {
		swl_gap("g2", "BAR2 window select refused", "high");
		printf("SWEET WLAN: G2 cfg 0x84 := %#x refused\n", w);
		return -1;
	}
	x->rt.win = w;
	return 0;
}

static int
swg_rd(struct swg_wx *x, uint32_t bp, uint32_t *v)
{
	if (x->stopped || !swg_ram(x, bp) || swg_win(x, bp)) {
		return -1;
	}
	return x->b->rd(x->b->ctx, SWG_SP_TCM, bp, v);
}

/* a u16 member: the half of the word it sits in (bcmpcie.h:305-339; bp & 2 picks the high half). Stores are always whole words. */
static int
swg_rd16(struct swg_wx *x, uint32_t bp, uint32_t *v)
{
	uint32_t w;

	if (swg_rd(x, bp & ~3u, &w)) {
		return -1;
	}
	*v = (bp & 2u) != 0u ? w >> 16 : (w & 0xFFFFu);
	return 0;
}

/* review 0174-g2-hwwrite-r1 F2: the byte ranges [a, a + an) and [b, b + bn) share no byte (both inside RAM: no wrap) */
static int
swg_apart(uint32_t a, uint32_t an, uint32_t b, uint32_t bn)
{
	return a + an <= b || b + bn <= a;
}

/* address of word i of row st, from the state read at the entry (never typed) */
static int
swg_addr(const struct swg_wx *x, int st, uint32_t i, uint32_t *bp)
{
	const struct swg_st *r = &swg_g2[st];

	switch (r->grp) {
	case SWG_G_SH:
		*bp = x->in.sh + r->off + 4u * i;
		return 0;
	case SWG_G_RI:
		*bp = x->rt.ri + SWG_RI_HOSTADDR + 8u * r->arg + 4u * i;
		return 0;
	case SWG_G_RM:
		*bp = x->rt.rm + SWG_RM_STRIDE * r->ring + r->off + 4u * i;
		return 0;
	case SWG_G_SLOT:
		*bp = x->rt.ra[r->arg] + SWG_IDX_SZ * r->ring;
		return 0;
	case SWG_G_CMD:
		*bp = r->off;
		return 0;
	}
	return -1;
}

static uint32_t
swg_lohi(uint64_t pa, uint32_t i)
{
	return (uint32_t)(pa >> (32u * i));
}

/* value of word i of row st, from the G1 plan, the old word (slots) or Android's constants (R94 s.2) */
static uint32_t
swg_val(const struct swg_wx *x, int st, uint32_t i, uint32_t old)
{
	const struct swg_st *r = &swg_g2[st];

	switch (st) {
	case SWG_ST_FLAGS2:
		return old | SWG_FLAGS2_BIT;
	case SWG_ST_HOSTCAP:
		return SWG_HOST_CAP;
	case SWG_ST_TRAP:
		return swg_lohi(x->in.trap, i);
	case SWG_ST_SCRATCH:
		return swg_lohi(x->in.scratch, i);
	case SWG_ST_SCRLEN:
		return SWG_SCRATCH_LEN;
	case SWG_ST_RC:
		return SWG_RC_CMD;
	case SWG_ST_EP:
		return SWG_EP_CMD_DMA;
	case SWG_ST_READY:
		return SWG_HOSTREADY;
	}
	switch (r->grp) {
	case SWG_G_RI:
		return swg_lohi(x->in.idx[r->arg], i);
	case SWG_G_RM:
		return r->off == SWG_RM_BASE ? swg_lohi(x->in.ring[r->ring], i) : ((swd_rings[r->ring].items & 0xFFFFu) | swd_rings[r->ring].isz << 16);
	case SWG_G_SLOT:
		return old & 0xFFFF0000u;       /* the 16-bit zero of the golden, the other half kept (D-9) */
	}
	return 0xFFFFFFFFu;
}

/* the old-value rule of row st (R89 s.1 start state): 1 = the word may be stored */
static int
swg_old_ok(int st, uint32_t old)
{
	switch (st) {
	case SWG_ST_FLAGS2:
		return old == SWG_FLAGS2_OLD;
	case SWG_ST_RC:
		return (old & SWG_CMD_MASK) == 0u;              /* R106 s.1 [obs] 0x00100000 */
	case SWG_ST_EP:
		return (old & SWG_CMD_MASK) == SWG_EP_CMD_MEM;  /* R89 s.1 [obs] 0x100002 */
	case SWG_ST_READY:
		return 1;                                       /* a doorbell: read and printed like Android (R94 step 12), no rule */
	}
	if (swg_g2[st].grp == SWG_G_SLOT) {
		return old != 0xFFFFFFFFu;                      /* zeroed whatever it holds (R94 step 9) */
	}
	return old == 0u;
}

/* the read-back rule: whole word equal, the Commands and the doorbell on their low half / not at all */
static int
swg_rb_ok(int st, uint32_t w, uint32_t rb)
{
	if (st == SWG_ST_READY) {
		return 1;
	}
	if (swg_g2[st].grp == SWG_G_CMD) {
		return (rb & SWG_CMD_MASK) == w;
	}
	return rb == w;
}

/* sprint 2026-11-08 ring-0 wrap: the word each ring-0 slot holds (what the engine last stored there; 0 = never written, the ring memory starts zeroed: the old rule `ring-0 old word is 0`).
 * After a wrap the old word of an item store is this shadow (x->r0sh), not 0. Reset at G2 entry. Every host store passes swg_put, so the shadow follows every ring-0 writer (W-D4c rounds, swg_d4d_post, DP15). */
#define SWG_R0_DEPTH     SWD_R0_ITEMS   /* ring-0 depth in items: sweet_wlan_dma.h (128) */
static uint32_t *
swg_r0_shadow(struct swg_wx *x, int sp, uint32_t a)
{
	uint64_t pa = x->in.blk + a;

	if (sp != SWG_SP_HOST || x->in.ring[0] == 0u || pa < x->in.ring[0] || pa - x->in.ring[0] >= (uint64_t)SWG_R0_DEPTH * 40u || (a & 3u) != 0u) {
		return NULL;
	}
	return &x->r0sh[(pa - x->in.ring[0]) / 4u];
}

static uint8_t swg_ep(uint32_t idx);
/* the epoch of the item at the current ring-0 write slot: the absolute item count (laps * depth + slot), DHD's seqnum */
static uint8_t
swg_ep0(const struct swg_wx *x)
{
	return swg_ep(x->d4wrap0 * SWG_R0_DEPTH + x->d4wr);
}

/* the ring-0 write slot after x->d4wr (wraps at the depth) */
static uint32_t
swg_r0_next(const struct swg_wx *x)
{
	return (x->d4wr + 1u) % SWG_R0_DEPTH;
}

static int
swg_put(struct swg_wx *x, int sp, uint32_t a, uint32_t v)
{
	uint32_t *sh = swg_r0_shadow(x, sp, a);
	int r;

	x->nstore++;
	r = x->b->wr(x->b->ctx, sp, a, v);
	if (r == 0 && sh != NULL) {
		*sh = v;
	}
	return r;
}

#ifdef SWEET_WLAN_DATA_H        /* Part A: only the DP15 transport (sweet_wlan_g2d.h) uses the batch */
/* Part A: nw words in one batched store (host or DATA block only, never ring 0: its shadow is per word); the same counting as nw swg_put calls. A bus without wr_blk refuses */
static int
swg_put_blk(struct swg_wx *x, int sp, uint32_t a, const uint32_t *v, uint32_t nw)
{
	if (x->b->wr_blk == NULL || nw == 0u || swg_r0_shadow(x, sp, a) != NULL || swg_r0_shadow(x, sp, a + 4u * (nw - 1u)) != NULL) {
		return -1;
	}
	x->nstore += nw;
	return x->b->wr_blk(x->b->ctx, sp, a, v, nw);
}

#endif /* SWEET_WLAN_DATA_H */

/* the 16-bit store (sweetwlan=14 index slots): the same counting, a different bus callback; a bus without wr16 refuses */
static int
swg_put16(struct swg_wx *x, int sp, uint32_t a, uint16_t v)
{
	x->nstore++;
	return x->b->wr16 == NULL ? -1 : x->b->wr16(x->b->ctx, sp, a, v);
}

static const char *const swg_d4_name[SWG_D4_N] = {
	"d4b mask", "d4b item w0", "d4b item w1", "d4b item w2", "d4b item w3", "d4b item w4", "d4b item w5", "d4b item w6", "d4b item w7", "d4b item w8", "d4b item w9",
	"d4b h2d_w slot 0", "d4b db0",
	"d4c ack item w0", "d4c ack item w1", "d4c ack item w2", "d4c ack item w3", "d4c ack item w4", "d4c ack item w5", "d4c ack item w6", "d4c ack item w7", "d4c ack item w8", "d4c ack item w9", "d4c ack h2d_w slot 0", "d4c ack db0",
	"d4c rd1 d2h_r slot 2", "d4c rd1 db0",
	"d4c wake item w0", "d4c wake item w1", "d4c wake item w2", "d4c wake item w3", "d4c wake item w4", "d4c wake item w5", "d4c wake item w6", "d4c wake item w7", "d4c wake item w8", "d4c wake item w9", "d4c wake h2d_w slot 0", "d4c wake db0",
	"d4c deas item w0", "d4c deas item w1", "d4c deas item w2", "d4c deas item w3", "d4c deas item w4", "d4c deas item w5", "d4c deas item w6", "d4c deas item w7", "d4c deas item w8", "d4c deas item w9", "d4c deas h2d_w slot 0", "d4c deas db0",
	"d4c rd2 d2h_r slot 2", "d4c rd2 db0",
	"d4b unmask",
};

/* W-D4b Q8: PCIe core +0xC34 := 0, read back, once, only if the mask store was made (swg_put returned 0, review r2 F11). Called FIRST by swg_undo (before the EP/RC restore) on every exit path after Q2, errors included. A failure is loud
 * (WIFI GAP + a STOP line) and makes the undo report FAILED; the EP/RC restores still run. 0 = nothing to do or restored, -1 = not restored. */
static int
swg_d4_unmask(struct swg_wx *x)
{
	uint32_t now = 0xFFFFFFFFu;

	if ((x->d4tried & (1ull << SWG_D4_MASK)) == 0u || x->d4unm) {
		return 0;
	}
	x->d4unm = 1;
	x->d4tried |= 1ull << SWG_D4_UNMASK;
	if (swg_put(x, SWG_SP_PCIE, SWG_MBX_MASK_OFF, 0u) || x->b->rd(x->b->ctx, SWG_SP_PCIE, SWG_MBX_MASK_OFF, &now) || now != 0u) {
		swl_gap("g2", "stop: d4b mask 0xC34 restore failed", "high");
		printf("SWEET WLAN: G2 STOP: d4b mask 0xC34 := 0 FAILED (read back %#x), interrupts may stay masked\n", now);
		return -1;
	}
	x->d4mask |= 1ull << SWG_D4_UNMASK;
	printf("SWEET WLAN: G2 store %s off %#x := 0 read back %#x\n", swg_d4_name[SWG_D4_UNMASK], SWG_MBX_MASK_OFF, now);
	return 0;
}

/* R89 s.5 step 1 + R106 s.3: EP Command := 0x0002, RC Command := its low half as read at the entry; each once, read back, failures loud. The CA7 re-halt and cfg 0x84 := 0 follow in
 * W17 (swl_restore) after the hook returns: G2 only says so. */
static int
swg_undo(struct swg_wx *x)
{
	int ok = 1;
	uint32_t now = 0xFFFFFFFFu, want = x->rt.rc_old & SWG_CMD_MASK;

	if (swg_d4_unmask(x) != 0) {    /* W-D4b Q8: the mask first, before the EP and RC restores (no-op unless the mask store was attempted) */
		ok = 0;
	}
	if ((x->tried & (1u << SWG_ST_EP)) != 0u && (swg_put(x, SWG_SP_EP, SWG_CMD_OFF, SWG_EP_CMD_MEM) || x->b->rd(x->b->ctx, SWG_SP_EP, SWG_CMD_OFF, &now) || (now & SWG_CMD_MASK) != SWG_EP_CMD_MEM)) {
		swl_gap("g2", "stop: ep command restore failed", "high");
		printf("SWEET WLAN: G2 stop: ep command := %#x FAILED (read back %#x)\n", SWG_EP_CMD_MEM, now);
		ok = 0;
	}
	if ((x->tried & (1u << SWG_ST_RC)) != 0u && (swg_put(x, SWG_SP_RC, SWG_CMD_OFF, want) || x->b->rd(x->b->ctx, SWG_SP_RC, SWG_CMD_OFF, &now) || (now & SWG_CMD_MASK) != want)) {
		swl_gap("g2", "stop: rc command restore failed", "high");
		printf("SWEET WLAN: G2 stop: rc command := %#x FAILED (read back %#x)\n", want, now);
		ok = 0;
	}
	x->restore = ok;
	printf("SWEET WLAN: G2 undo %s (ep %s, rc %s); CA7 re-halt and cfg 0x84 := 0 follow in W17\n", ok ? "ok" : "FAILED",
	       (x->tried & (1u << SWG_ST_EP)) != 0u ? "restored" : "not stored", (x->tried & (1u << SWG_ST_RC)) != 0u ? "restored" : "not stored");
	return ok ? 0 : -1;
}

static int
swg_stop(struct swg_wx *x, int code)
{
	if (!x->stopped) {
		x->stopped = 1;
		x->code = code;
		{ char gb[48]; snprintf(gb, sizeof gb, "STOP: %s", swg_ename[code]); swl_gap("g2", gb, "high"); }
		printf("SWEET WLAN: G2 STOP: %s (code %d) at step %u %s\n", swg_ename[code], code, x->step, x->step < SWG_ST_N ? swg_g2[x->step].name : x->step == SWG_ST_N ? "entry" : "d4b");
		printf("SWEET WLAN: G2 STOP: %s\n", x->fail != NULL && x->fail[0] != '\0' ? x->fail : "see the step");
		if (x->d4sleep) {
			swl_gap("d4c", "stop after the DS_ACK before the wake assert (U3)", "high");     /* swl_gap keeps 63 chars: the full reason is in the printf below */
			printf("SWEET WLAN: D4c stop between the DS_ACK doorbell and the wake doorbell: the undo and the W17 restore touch a dongle that may be in DEV_SLEEP (W-D4c-ds-ack.md s.6.6 U3)\n");
		}
		swg_undo(x);
	}
	return -code;
}

/* one row: order, ONCE, address in RAM, old value, the store, the read-back */
static int
swg_st(struct swg_wx *x, int st)
{
	const struct swg_st *r = &swg_g2[st];
	uint32_t bp, i, old = 0, rb = 0, w;
	int sp = r->grp == SWG_G_CMD ? r->arg : SWG_SP_TCM;
	int w16 = r->grp == SWG_G_SLOT && x->d4b >= 2;       /* sweetwlan=14: the golden's 16-bit slot zero (D-9), the upper half untouched by the store itself */

	if (x->stopped) {
		return -SWG_E_STOPPED;
	}
	x->step = (unsigned)st;
	x->fail = r->name;
	if (!x->entered || (x->mask & (1u << st)) != 0u || (st != 0 && (x->mask & (1u << (st - 1))) == 0u)) {
		return swg_stop(x, SWG_E_ORDER);
	}
	for (i = 0; i < r->words; i++) {
		if (swg_addr(x, st, i, &bp) || (sp == SWG_SP_TCM ? swg_rd(x, bp, &old) : x->b->rd(x->b->ctx, sp, bp, &old))) {      /* swg_rd: RAM words only */
			return swg_stop(x, SWG_E_ADDR);
		}
		x->old = old;
		if (old == 0xFFFFFFFFu && st != SWG_ST_READY) {
			return swg_stop(x, SWG_E_DEAD);
		}
		if (!swg_old_ok(st, old)) {
			return swg_stop(x, st == SWG_ST_RC ? SWG_E_RCSTUCK : SWG_E_OLD);
		}
		w = swg_val(x, st, i, old);
		x->tried |= 1u << st;
		if (w16 ? swg_put16(x, sp, bp, (uint16_t)w) : swg_put(x, sp, bp, w)) {
			return swg_stop(x, SWG_E_WR);
		}
		if (sp == SWG_SP_TCM ? swg_rd(x, bp, &rb) : x->b->rd(x->b->ctx, sp, bp, &rb)) {
			return swg_stop(x, SWG_E_READBACK);
		}
		x->rb = rb;
		if (!swg_rb_ok(st, w, rb)) {
			return swg_stop(x, st == SWG_ST_RC ? SWG_E_RCSTUCK : SWG_E_READBACK);
		}
		printf("SWEET WLAN: G2 store %s %s %#x old %#x := %#x read back %#x%s\n", r->name, sp == SWG_SP_TCM ? "bp" : "off", bp, old, w, rb, w16 ? " (16-bit store, upper half unchanged)" : "");
	}
	x->mask |= 1u << st;
	return 0;
}

static int
swg_sh_is(struct swg_wx *x, uint32_t off, uint32_t want, const char *name)
{
	uint32_t w = 0xFFFFFFFFu;

	if (swg_rd(x, x->in.sh + off, &w) || w != want) {
		x->fail = name;
		printf("SWEET WLAN: G2 entry shared +%#x = %#x, want %#x\n", off, w, want);
		return 1;
	}
	return 0;
}

/* one console line: the flush of swg_dump (a long line is wrapped, at most SWG_CON_LINE characters) */
static void
swg_con_flush(char *b, uint32_t *n)
{
	if (*n != 0u) {
		b[*n] = 0;
		printf("SWEET WLAN: fw console: %s\n", b);
		*n = 0;
	}
}

/* the hnd_cons_t parse of swl_f1_console (sweet_wlan_wr.h, patch 0134) on swg_rd: buffer, size, write index at cons+8/+12/+16, then the last min(size, SWG_CON_MAX) bytes before the index, oldest first,
 * LF ends a line, CR and NUL are dropped, anything else outside printable ASCII shows as '.'. 0 = read (x->con_n bytes); -1 = not readable (a printed line says which word) */
static int
swg_con(struct swg_wx *x, uint32_t cons)
{
	char lb[SWG_CON_LINE + 1];
	uint32_t buf = 0, size = 0, idx = 0, len, p, left, ln = 0, w = 0, c;

	if (swg_rd(x, cons + SWG_CON_BUF, &buf) || swg_rd(x, cons + SWG_CON_BUF + 4u, &size) || swg_rd(x, cons + SWG_CON_BUF + 8u, &idx)) {
		printf("SWEET WLAN: G2 console %#x: hnd_cons_t not readable\n", cons);
		return -1;
	}
	printf("SWEET WLAN: G2 console %#x: buffer %#x size %u write index %u\n", cons, buf, size, idx);
	if (size == 0u || (size & 3u) != 0u || !swg_ram(x, buf) || size > x->in.end - buf || idx > size) {
		printf("SWEET WLAN: G2 console buffer %#x size %u index %u is not a word aligned part of the RAM, not read\n", buf, size, idx);
		return -1;
	}
	len = size < SWG_CON_MAX ? size : SWG_CON_MAX;
	p = (idx % size + size - len) % size;
	for (left = len; left != 0u; left--, p = p + 1u >= size ? 0u : p + 1u) {
		if (swg_rd(x, buf + (p & ~3u), &w)) {
			swg_con_flush(lb, &ln);
			printf("SWEET WLAN: G2 console word %#x not readable, stopped\n", buf + (p & ~3u));
			return -1;
		}
		c = (w >> (8u * (p & 3u))) & 0xFFu;
		if (c == '\n') {
			swg_con_flush(lb, &ln);
		} else if (c != 0u && c != '\r') {
			if (ln == SWG_CON_LINE) {
				swg_con_flush(lb, &ln);
			}
			lb[ln++] = c >= 0x20u && c < 0x7Fu ? (char)c : '.';
		}
	}
	swg_con_flush(lb, &ln);
	x->con_n = len;
	return 0;
}

/* W-D4b gaps, reads only (no store, no window value beyond swg_win's 0 / SWW_WIN_TAIL): the words F1 and F2 could not read through their one window. The h2d and d2h mailbox DATA words (the pointers
 * at shared +0x28 / +0x2C), the ring_info words and the firmware console. Each success sets x->rd_mb / rd_ri / rd_con, which swg_miss_end() turns into swl_miss_resolve(); a word that cannot be read is a
 * printed line and a WIFI GAP of its own (never silent). Runs at the entry, before any store (so at sweetwlan=12 too). 0 always (a failed dump is a gap, not a stop). */
static int
swg_dump(struct swg_wx *x)
{
	uint32_t hp = 0, cons = 0, mh = 0, md = 0, ri[SWG_RI_WORDS], i;
	int mb_ok, ri_ok = 1, con_ok;

	for (i = 0; i < SWG_RI_WORDS; i++) {
		if (swg_rd(x, x->rt.ri + 4u * i, &ri[i])) {
			ri_ok = 0;
			ri[i] = 0xFFFFFFFFu;
		}
	}
	con_ok = !swg_rd(x, x->in.sh + SWG_SH_CONSOLE, &cons) && swg_con(x, cons) == 0;
	mb_ok = !swg_rd(x, x->in.sh + SWG_SH_H2D_MB, &hp) && !swg_rd(x, hp, &mh) && !swg_rd(x, x->rt.mbp, &md);     /* last: the mailbox data sit in the tail window of the observed boots */
	x->rd_mb = mb_ok;
	x->rd_ri = ri_ok;
	x->rd_con = con_ok;
	printf("SWEET WLAN: G2 read for F1/F2: h2d mb 0x%x d2h mb 0x%x rings_info 0x%x console %u bytes%s\n", mh, md, x->rt.ri, x->con_n, con_ok ? "" : " (not read)");
	printf("SWEET WLAN: G2 read for F1/F2: h2d mb ptr 0x%x d2h mb ptr 0x%x\n", hp, x->rt.mbp);
	for (i = 0; i < SWG_RI_WORDS; i += 4u) {
		uint32_t j;

		printf("SWEET WLAN: G2 rings_info +0x%02x:", 4u * i);
		for (j = i; j < i + 4u && j < SWG_RI_WORDS; j++) {
			printf(" 0x%x", ri[j]);
		}
		printf("\n");
	}
	if (!con_ok) {
		swl_gap("g2", "dump: firmware console not read", "medium");
	}
	if (!mb_ok) {
		swl_gap("g2", "dump: mailbox data not read", "medium");
	}
	if (!ri_ok) {
		swl_gap("g2", "dump: rings_info not read", "medium");
	}
	return 0;
}

/* end of the run (every path): the gaps that depend on what G2 read or verified. A miss is resolved only for a word G2 really read; swl_gap_summary() prints the rest. */
static void
swg_miss_end(struct swg_wx *x)
{
	if (x->rd_con) {
		swl_miss_resolve("f1", SWL_M_F1_CON);
	}
	if (x->rd_mb) {
		swl_miss_resolve("f1", SWL_M_F1_MB);
		swl_miss_resolve("f2", SWL_M_F2_MB);
	}
	if (x->rd_ri) {
		swl_miss_resolve("f1", SWL_M_F1_RI);
		swl_miss_resolve("f2", SWL_M_F2_RI);
	}
	if (x->pins_ok) {
		swl_miss_resolve("g1", SWL_M_G1);
	}
	if (x->hostrd) {
		swl_miss_resolve("g2", SWL_M_G2HOST);
	}
}

/* R89 A1-A6 and s.1: the state G2 starts from, reads only. Every pointer G2 later stores through is read and checked here. */
static int
swg_entry(struct swg_wx *x)
{
	uint32_t fl = 0, w = 0, i, sh = x->in.sh;

	x->step = SWG_ST_N;
	x->fail = "a Command or the SRPWR word could not be read";
	if (x->b->rd(x->b->ctx, SWG_SP_RC, SWG_CMD_OFF, &x->rt.rc_old) || x->b->rd(x->b->ctx, SWG_SP_EP, SWG_CMD_OFF, &x->rt.ep_old) ||
	    x->b->rd(x->b->ctx, SWG_SP_PCIE, SWG_SRPWR_OFF, &x->rt.srpwr)) {
		return swg_stop(x, SWG_E_ENTRY);
	}
	x->fail = "the shared structure is not a RAM word";          /* swg_rd refuses a word outside RAM (R89 s.7 S3); the last word G2 reads, +0x5C, is checked by swg_sh_is below */
	if (swg_rd(x, sh + SWG_SH_FLAGS, &fl) || swg_rd(x, sh + SWG_SH_RINGINFO, &x->rt.ri)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	printf("SWEET WLAN: G2 entry rc command %#x ep command %#x srpwr %#x shared %#x flags %#x ring_info %#x\n", x->rt.rc_old, x->rt.ep_old, x->rt.srpwr, sh, fl, x->rt.ri);
	x->fail = "the RC Command is not 0 in its low half (0x00100000 observed, R106 s.1)";
	if ((x->rt.rc_old & SWG_CMD_MASK) != 0u) {
		return swg_stop(x, SWG_E_RCSTUCK);
	}
	x->fail = "the EP Command is not MEM only (0x0002)";
	if ((x->rt.ep_old & SWG_CMD_MASK) != SWG_EP_CMD_MEM) {
		return swg_stop(x, SWG_E_ENTRY);
	}
	x->fail = "SRPWR request/status bits 0x20200 not both set (0x30300 observed)";
	if ((x->rt.srpwr & (SWG_SRPWR_REQ1 | SWG_SRPWR_ST1)) != (SWG_SRPWR_REQ1 | SWG_SRPWR_ST1)) {
		return swg_stop(x, SWG_E_ENTRY);
	}
	x->fail = "flags: version not 7 or assert/trap set";
	if ((fl & SWG_FLAGS_VER) != SWG_VER || (fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {
		return swg_stop(x, SWG_E_FLAGS);
	}
	x->fail = "flags: PCIE_SHARED_2BYTE_INDICES (0x100000) set, G1 planned 4-byte index slots";
	if ((fl & SWG_FLAGS_2BYTE) != 0u) {
		return swg_stop(x, SWG_E_FLAGS);
	}
	x->fail = "ring_info counts (43 submission queues, 4 completion rings) not as observed";
	if (swg_rd16(x, x->rt.ri + SWG_RI_QUEUES, &w) || w != SWG_MAX_SUB || swg_rd16(x, x->rt.ri + SWG_RI_MAXCPL, &w) || w != SWG_MAX_CPL) {
		return swg_stop(x, SWG_E_ENTRY);
	}
	x->pins_ok = 1;
	printf("SWEET WLAN: G2 pins verified: rw_index_sz %u (2BYTE clear), rings %u/%u\n", SWG_IDX_SZ, SWG_MAX_SUB, SWG_MAX_CPL);
	x->fail = "ringmem_ptr is not ring_info + 0x4C";
	if (swg_rd(x, x->rt.ri + SWG_RI_RINGMEM, &x->rt.rm) || x->rt.rm != x->rt.ri + SWG_RI_SIZE) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->fail = "ring_info + ring_mem overlaps the shared structure";      /* W-D3c: ring_info may lie above or below it (DHD accepts both, dhd_pcie.c:13872); ri < end here (the reads above), no wrap */
	if (!swg_apart(x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE, sh, SWG_SH_TRAP + 8u)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->fail = "the last ring_mem word is not a RAM word";
	if (!swg_ram(x, x->rt.rm + SWG_RM_N * SWG_RM_STRIDE - 4u)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->fail = "a ring_mem word is not 0 (not a fresh boot)";
	for (i = 0; i < SWG_RM_N * SWG_RM_STRIDE / 4u; i++) {
		if (swg_rd(x, x->rt.rm + 4u * i, &w)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (w != 0u) {
			return swg_stop(x, SWG_E_OLD);
		}
	}
	x->fail = "the d2h mailbox pointer (shared + 0x2C) is not a 4-aligned RAM word";    /* W-D4a: read once here, before any store; the poll reads TCM[p], never p (swg_ram: 4-aligned, inside RAM) */
	if (swg_rd(x, sh + SWG_SH_D2H_MB, &x->rt.mbp) || !swg_ram(x, x->rt.mbp)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->fail = "an index array pointer is not a RAM word with room for its slots";
	for (i = 0; i < SWG_IDX_N; i++) {
		if (swg_rd(x, x->rt.ri + SWG_RI_IDXPTR + 4u * i, &x->rt.ra[i]) || !swg_ram(x, x->rt.ra[i]) || !swg_ram(x, x->rt.ra[i] + SWG_IDX_SZ * (SWG_IDX_SLOTS - 1u))) {
			return swg_stop(x, SWG_E_ADDR);
		}
	}
	x->fail = "an index array overlaps the shared structure, ring_info/ring_mem or another index array";     /* review r1 F2: a slot store would clear a word another row stored */
	for (i = 0; i < SWG_IDX_N; i++) {
		if (!swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->in.sh, SWG_SH_TRAP + 8u) ||
		    !swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->rt.ri, SWG_RI_SIZE + SWG_RM_N * SWG_RM_STRIDE)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		for (w = 0; w < i; w++) {
			if (!swg_apart(x->rt.ra[i], SWG_IDX_SZ * SWG_IDX_SLOTS, x->rt.ra[w], SWG_IDX_SZ * SWG_IDX_SLOTS)) {
				return swg_stop(x, SWG_E_ADDR);
			}
		}
	}
	/* ring_info/ring_mem against the shared structure: the swg_apart check above (W-D3c; the old "ri < sh" argument no longer holds) */
	x->fail = "a ring_info host address G2 writes is not 0";
	for (i = 0; i < 2u * SWG_IDX_N; i++) {
		if (swg_rd(x, x->rt.ri + SWG_RI_HOSTADDR + 4u * i, &w) || w != 0u) {
			return swg_stop(x, SWG_E_OLD);
		}
	}
	if (swg_sh_is(x, SWG_SH_FLAGS2, SWG_FLAGS2_OLD, "flags2 is not 0x40943041") || swg_sh_is(x, SWG_SH_HOSTCAP, 0u, "host_cap is not 0") ||
	    swg_sh_is(x, SWG_SH_SCR_LEN, 0u, "scratch len is not 0") || swg_sh_is(x, SWG_SH_SCR_LO, 0u, "scratch addr lo is not 0") ||
	    swg_sh_is(x, SWG_SH_SCR_LO + 4u, 0u, "scratch addr hi is not 0") || swg_sh_is(x, SWG_SH_TRAP, 0u, "host_trap_addr lo is not 0") ||
	    swg_sh_is(x, SWG_SH_TRAP + 4u, 0u, "host_trap_addr hi is not 0")) {
		return swg_stop(x, SWG_E_OLD);
	}
	x->fail = NULL;
	x->entered = 1;
	memset(x->r0sh, 0, sizeof x->r0sh);    /* sprint 2026-11-08: no ring-0 word written yet */
	x->d4wrap0 = 0u;
	printf("SWEET WLAN: G2 entry ok ring_mem %#x index arrays %#x %#x %#x %#x\n", x->rt.rm, x->rt.ra[0], x->rt.ra[1], x->rt.ra[2], x->rt.ra[3]);
	return swg_dump(x);
}

/* W-D4a-1 (SPEC C9): the six words compared, read only. Order: when the BAR2 window already sits on the tail (the mailbox data of the observed boots is at 0x44868c, window SWW_WIN_TAIL), the mailbox
 * data is read first, so the observed layout (shared and the d2h_w array on the tail or all on 0, mailbox data on the tail) needs no move and the default host-test layout (mailbox data on the tail, everything else on 0) one move per pass; other layouts may move more, up to three per pass (each a swg_win select of 0 or SWW_WIN_TAIL, glue-list entries G2 already uses); otherwise last. The index array is the third of the four (SWD_IDX_H2D_W, H2D_R, D2H_W,
 * D2H_R: enum of patch 0167 sweet_wlan_dma.h, the static assert above): x->rt.ra[SWG_IDX(SWD_IDX_D2H_W)]. 0xC30 goes the way the hostready register +0x144 goes (SWG_SP_PCIE, glue sw_g2_bar0). */
static int
swg_pw_read(struct swg_wx *x, struct swg_pw *w)
{
	uint32_t i;
	int mb_first = x->rt.win != 0u;

	if (mb_first && swg_rd(x, x->rt.mbp, &w->mb)) {
		return -1;
	}
	if (swg_rd(x, x->in.sh + SWG_SH_FLAGS, &w->fl)) {
		return -1;
	}
	for (i = 0; i < SWG_D2H_SLOTS; i++) {
		if (swg_rd(x, x->rt.ra[SWG_IDX(SWD_IDX_D2H_W)] + SWG_IDX_SZ * i, &w->dw[i])) {
			return -1;
		}
	}
	if (x->stopped || x->b->rd(x->b->ctx, SWG_SP_PCIE, SWG_PCIE_STATUS_OFF, &w->c30)) {
		return -1;
	}
	if (!mb_first && swg_rd(x, x->rt.mbp, &w->mb)) {
		return -1;
	}
	return 0;
}

/* F1 (review 0174 W-D4a r1): a word that reads all ones is a dead link, not data (DHD: dhd_pcie.c:1231-1232 "Device is removed ... reads 0xffffffff", 1483-1484 "return error for 0xFFFFFFFF",
 * refs/wlan-stage5). Checked on every word of every read, BEFORE the FWHALT test and the change-vs-baseline test (an all-ones word would otherwise be a "change", or hide a FWHALT bit). 1 = a word is all ones, x->fail names it */
static int
swg_pw_ones(struct swg_wx *x, const struct swg_pw *w)
{
	uint32_t i;

	if (w->fl == 0xFFFFFFFFu) {
		x->fail = "the shared flags word reads all ones (dead link)";
		return 1;
	}
	if (w->mb == 0xFFFFFFFFu) {
		x->fail = "the d2h mailbox data word reads all ones (dead link)";
		return 1;
	}
	for (i = 0; i < SWG_D2H_SLOTS; i++) {
		if (w->dw[i] == 0xFFFFFFFFu) {
			x->fail = i == 0 ? "tcm d2h_w[0] reads all ones (dead link)" : i == 1 ? "tcm d2h_w[1] reads all ones (dead link)" : "tcm d2h_w[2] reads all ones (dead link)";
			return 1;
		}
	}
	if (w->c30 == 0xFFFFFFFFu) {
		x->fail = "PCIe core 0xC30 reads all ones (dead link)";
		return 1;
	}
	return 0;
}

/* bit i set: word i differs. Bits: 0 flags, 1 mb data, 2..4 tcm d2h_w[0..2], 5 c30 */
static unsigned
swg_pw_diff(const struct swg_pw *a, const struct swg_pw *b)
{
	unsigned c = 0;

	c |= a->fl != b->fl ? 1u << 0 : 0u;
	c |= a->mb != b->mb ? 1u << 1 : 0u;
	c |= a->dw[0] != b->dw[0] ? 1u << 2 : 0u;
	c |= a->dw[1] != b->dw[1] ? 1u << 3 : 0u;
	c |= a->dw[2] != b->dw[2] ? 1u << 4 : 0u;
	c |= a->c30 != b->c30 ? 1u << 5 : 0u;
	return c;
}

/* one line, every word; host fields: none (swg_base's gap) */
static void
swg_pw_print(const struct swg_wx *x, const char *what, const struct swg_pw *w, uint32_t rm)
{
	printf("SWEET WLAN: G2 %s flags 0x%x mb ptr 0x%x mb data 0x%x tcm d2h_w 0x%x 0x%x 0x%x host - c30 0x%x ring_mem idx/type 0x%x\n", what, w->fl, x->rt.mbp, w->mb, w->dw[0], w->dw[1], w->dw[2], w->c30, rm);
}

/* ring_mem idx/type words ORed: informational only (no source says the firmware writes them, W-D4a s.0 item 4) */
static int
swg_rm_or(struct swg_wx *x, uint32_t *rm)
{
	uint32_t w, i;

	*rm = 0;
	for (i = 0; i < SWG_RM_N; i++) {
		if (swg_rd(x, x->rt.rm + SWG_RM_STRIDE * i, &w)) {
			return -1;
		}
		*rm |= w;
	}
	return 0;
}

/* W-D4a-1: the baseline, taken immediately BEFORE the hostready row (after every other store); reads only */
static int
swg_base(struct swg_wx *x)
{
	x->step = SWG_ST_READY;
	x->fail = "the baseline read before hostready failed";
	if (swg_pw_read(x, &x->base)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (swg_pw_ones(x, &x->base)) {
		return swg_stop(x, SWG_E_DEAD);
	}
	/* the G1 block IS mapped (kmem_alloc_contig KMA_PERMANENT) and its VA reaches G2 as sw_hw.g2va (D-10): sweetwlan=13/14 read it in swg_d4b (a miss, resolved there); at 12 the observation is off, the true cause */
	if (x->d4b == 0) {
		swl_gap("g2", "host block not read (level 12: D4b observation off)", "medium");
	} else {
		swl_miss("g2", SWL_M_G2HOST, "medium");
	}
	printf("SWEET WLAN: G2 base flags 0x%x mb ptr 0x%x mb data 0x%x tcm d2h_w 0x%x 0x%x 0x%x host - c30 0x%x\n", x->base.fl, x->rt.mbp, x->base.mb, x->base.dw[0], x->base.dw[1], x->base.dw[2], x->base.c30);
	return 0;
}

/* W-D4a-1 (SPEC C9, replaces R89 s.4): reads only, budget SWG_POLLS; success = ANY of the six words differs from the baseline; no change is a gap, not a failure */
static int
swg_poll(struct swg_wx *x)
{
	static const char *const nm[] = { "flags", "mb data", "tcm d2h_w[0]", "tcm d2h_w[1]", "tcm d2h_w[2]", "c30" };
	struct swg_pw cur = { 0, 0, { 0, 0, 0 }, 0 };     /* a mutant that skips a read must not compare stack garbage */
	uint32_t rm = 0;
	unsigned n, ch, i;

	x->step = SWG_ST_READY;
	x->fail = "a poll read failed";
	for (n = 0; n < SWG_POLLS; n++) {
		if (swg_pw_read(x, &cur)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (swg_pw_ones(x, &cur)) {
			return swg_stop(x, SWG_E_DEAD);
		}
		if ((cur.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {
			printf("SWEET WLAN: G2 poll %u flags 0x%x mb data 0x%x TRAP/ASSERT\n", n + 1u, cur.fl, cur.mb);
			x->fail = "the firmware reports assert/trap after hostready";
			return swg_stop(x, SWG_E_FATAL);
		}
		if ((cur.mb & SWG_D2H_FWHALT) != 0u) {
			printf("SWEET WLAN: G2 poll %u mb data 0x%x FWHALT\n", n + 1u, cur.mb);
			x->fail = "the firmware reports D2H_DEV_FWHALT in the d2h mailbox after hostready";
			return swg_stop(x, SWG_E_FATAL);
		}
		ch = swg_pw_diff(&cur, &x->base);
		if (ch != 0u) {
			if (swg_rm_or(x, &rm)) {
				return swg_stop(x, SWG_E_ADDR);
			}
			printf("SWEET WLAN: G2 poll %u: change vs baseline in", n + 1u);
			for (i = 0; i < 6u; i++) {
				if ((ch & (1u << i)) != 0u) {
					printf(" %s", nm[i]);
				}
			}
			printf("\n");
			swg_pw_print(x, "poll values", &cur, rm);
			return 0;
		}
		if (n + 1u < SWG_POLLS) {
			x->b->delay_ms(x->b->ctx, SWG_POLL_MS);
		}
	}
	if (swg_rm_or(x, &rm)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	/* the what field of swl_gap_tab holds 63 chars: the list of words compared is in the final line below, not in the gap text */
	swl_gap("g2", "no change vs baseline after hostready", "medium");
	printf("SWEET WLAN: G2 poll: no change in %u polls; final", SWG_POLLS);
	printf(" flags 0x%x mb ptr 0x%x mb data 0x%x tcm d2h_w 0x%x 0x%x 0x%x host - c30 0x%x ring_mem idx/type 0x%x\n", cur.fl, x->rt.mbp, cur.mb, cur.dw[0], cur.dw[1], cur.dw[2], cur.c30, rm);
	return 0;
}

/* ---- W-D4b (SPEC-WIFI Q1-Q8): the first ring-0 post. Gated by x->d4b (sweetwlan=13 read-only, 14 with the stores) and a G2 that reached hostready and the W-D4a poll with rc 0. Every store goes through swg_put and the glue's list. ---- */

/* the host block offset of [pa, pa + n): 4-aligned, inside [in.blk, in.blk + in.blk_len). 0 = ok */
static int
swg_hoff(const struct swg_wx *x, uint64_t pa, uint32_t n, uint32_t *off)
{
	if (x->in.blk == 0u || pa < x->in.blk || (pa & 3u) != 0u || pa - x->in.blk > (uint64_t)x->in.blk_len || n > x->in.blk_len - (uint32_t)(pa - x->in.blk)) {
		return -1;
	}
	*off = (uint32_t)(pa - x->in.blk);
	return 0;
}

static int
swg_hrd(struct swg_wx *x, uint64_t pa, uint32_t *v)
{
	uint32_t o;

	if (x->stopped || swg_hoff(x, pa, 4u, &o)) {
		return -1;
	}
	return x->b->rd(x->b->ctx, SWG_SP_HOST, o, v);
}

/* Q1/Q6 reads: the W-D4a TCM words and 0xC30, then H2D-RD slot 0, D2H-WR slot 0 and the 6 words of ring-2 item k (the glue flushes the line before each host read). The baseline read uses k = 0; the post poll uses k = the baseline D2H-WR slot 0, the first item the dongle writes after it (run d4b13: the dongle had already written item 0 before the post) */
static int
swg_d4_read(struct swg_wx *x, struct swg_d4s *s, uint32_t k)
{
	uint32_t i;

	if (swg_pw_read(x, &s->pw) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)], &s->h2dr) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &s->d2hw)) {
		return -1;
	}
	for (i = 0; i < 6u; i++) {
		if (swg_hrd(x, x->in.ring[SWD_D2H_CTRL - SWD_H2D_CTRL] + 4u * i + (uint64_t)k * swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].isz, &s->r2[i])) {
			return -1;
		}
	}
	return 0;
}

/* a word that reads all ones is a dead link (as swg_pw_ones); the host words h2d_r[0] and d2h_w[0] are checked too (DHD's removed-device test is the same all-ones word) */
static int
swg_d4_ones(struct swg_wx *x, const struct swg_d4s *s)
{
	if (swg_pw_ones(x, &s->pw)) {
		return 1;
	}
	if (s->h2dr == 0xFFFFFFFFu) {
		x->fail = "host h2d_r[0] reads all ones (dead link)";
		return 1;
	}
	if (s->d2hw == 0xFFFFFFFFu) {
		x->fail = "host d2h_w[0] reads all ones (dead link)";
		return 1;
	}
	return 0;
}

static void
swg_d4_flat(const struct swg_d4s *s, uint32_t v[SWG_D4_NV])
{
	unsigned i;

	v[0] = s->pw.fl;
	v[1] = s->pw.mb;
	for (i = 0; i < SWG_D2H_SLOTS; i++) {
		v[2 + i] = s->pw.dw[i];
	}
	v[5] = s->pw.c30;
	v[6] = s->h2dr;
	v[7] = s->d2hw;
	for (i = 0; i < 6u; i++) {
		v[8 + i] = s->r2[i];
	}
}

/* W-D4c item rows: 1 and the ring-0 item index (1 ack, 2 wake, 3 deas) and the word of row r, else 0 (the L14 item, the slots, the doorbells) */
static int
swg_d4c_item(int r, unsigned *itm, unsigned *w)
{
	if (r >= SWG_D4C_A_ITEM0 && r < SWG_D4C_A_SLOT) {
		*itm = 1u;
		*w = (unsigned)(r - SWG_D4C_A_ITEM0);
	} else if (r >= SWG_D4C_W_ITEM0 && r < SWG_D4C_W_SLOT) {
		*itm = 2u;
		*w = (unsigned)(r - SWG_D4C_W_ITEM0);
	} else if (r >= SWG_D4C_D_ITEM0 && r < SWG_D4C_D_SLOT) {
		*itm = 3u;
		*w = (unsigned)(r - SWG_D4C_D_ITEM0);
	} else {
		return 0;
	}
	return 1;
}

/* the address (space, offset) of row r; host rows are offsets into the G1 block, derived from the PAs the glue handed in */
static int
swg_d4_addr(const struct swg_wx *x, int r, int *sp, uint32_t *bp)
{
	unsigned itm, w;

	switch (r) {
	case SWG_D4_MASK:
	case SWG_D4_UNMASK:
		*sp = SWG_SP_PCIE;
		*bp = SWG_MBX_MASK_OFF;
		return 0;
	case SWG_D4_DB0:
	case SWG_D4C_A_DB:
	case SWG_D4C_C1_DB:
	case SWG_D4C_W_DB:
	case SWG_D4C_D_DB:
	case SWG_D4C_C2_DB:
		*sp = SWG_SP_PCIE;
		*bp = SWG_DB0_OFF;
		return 0;
	case SWG_D4_SLOT:
	case SWG_D4C_A_SLOT:
	case SWG_D4C_W_SLOT:
	case SWG_D4C_D_SLOT:
		*sp = SWG_SP_HOST;
		return swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], 4u, bp);
	case SWG_D4C_C1_SLOT:
	case SWG_D4C_C2_SLOT:
		*sp = SWG_SP_HOST;
		return swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_R)], 4u, bp);
	}
	*sp = SWG_SP_HOST;
	if (swg_d4c_item(r, &itm, &w)) {       /* W-D4c: ring-0 item itm, itm items (SWD4B_ITEM_LEN) after the base */
		return swg_hoff(x, x->in.ring[0] + (uint64_t)(x->d4c_wr + itm - 1u) * SWD4B_ITEM_LEN + 4u * w, 4u, bp);      /* 22b: the round's items are at d4c_wr, +1, +2 (round 0: 1..3) */
	}
	return swg_hoff(x, x->in.ring[0] + 4u * (uint32_t)(r - SWG_D4_ITEM0), 4u, bp);
}

static uint32_t
swg_d4_val(const struct swg_wx *x, int r, uint32_t old)
{
	unsigned itm, w;

	switch (r) {
	case SWG_D4_MASK:
		return SWG_MBX_MASK_ON;
	case SWG_D4_DB0:
		return SWG_DB0_VAL;
	case SWG_D4_SLOT:
		return (old & 0xFFFF0000u) | 1u;        /* the 16-bit low half of the 4 B slot (2BYTE_INDICES clear), the upper half kept (D-9) */
	case SWG_D4C_A_SLOT:
		return (old & 0xFFFF0000u) | (x->d4c_wr + 1u);        /* round 0: ring 0 holds the L14 item 0 and the ack: write index 2 (d4c_wr 1) */
	case SWG_D4C_W_SLOT:
		return (old & 0xFFFF0000u) | (x->d4c_wr + 2u);
	case SWG_D4C_D_SLOT:
		return (old & 0xFFFF0000u) | (x->d4c_wr + 3u);
	case SWG_D4C_C1_SLOT:
		return (old & 0xFFFF0000u) | ((x->d4c_rd + 1u) % SWG_R2_DEPTH);        /* ring-2 request item consumed (dhd_msgbuf.c:7524-7532); same slot width rule; round 0: 1 */
	case SWG_D4C_C2_SLOT:
		return (old & 0xFFFF0000u) | ((x->d4c_rd + 2u) % SWG_R2_DEPTH);        /* ring-2 exit note consumed; round 0: 2 */
	case SWG_D4C_A_DB:
		return SWG_DB_WR | (x->d4c_wr + 1u);     /* round 0 FF000002 (SWG_D4C_DB_ACK) */
	case SWG_D4C_C1_DB:
		return SWG_DB_RD | ((x->d4c_rd + 1u) % SWG_R2_DEPTH);   /* round 0 DD020001 */
	case SWG_D4C_W_DB:
		return SWG_DB_WR | (x->d4c_wr + 2u);     /* FF000003 */
	case SWG_D4C_D_DB:
		return SWG_DB_WR | (x->d4c_wr + 3u);     /* FF000004 */
	case SWG_D4C_C2_DB:
		return SWG_DB_RD | ((x->d4c_rd + 2u) % SWG_R2_DEPTH);   /* DD020002 */
	}
	if (swg_d4c_item(r, &itm, &w)) {
		return x->d4cw[itm - 1u][w];
	}
	return x->d4w[r - SWG_D4_ITEM0];
}

/* the value a row's word must hold before its store: 0, except a slot, which holds the index of the previous post / the previous read index (round 0: A1 1, W 2, D 3, C1 0, C2 1) */
static uint32_t
swg_d4_oldv(const struct swg_wx *x, int r)
{
	switch (r) {
	case SWG_D4C_A_SLOT:
		return x->d4c_wr;
	case SWG_D4C_W_SLOT:
		return x->d4c_wr + 1u;
	case SWG_D4C_D_SLOT:
		return x->d4c_wr + 2u;
	case SWG_D4C_C1_SLOT:
		return x->d4c_rd % SWG_R2_DEPTH;
	case SWG_D4C_C2_SLOT:
		return (x->d4c_rd + 1u) % SWG_R2_DEPTH;
	}
	return 0u;
}

/* one W-D4b row: order, ONCE, address, old value (read and required 0, except the doorbell, which is never read: review r1 F6), the store, the read-back (whole word, except the doorbell), the printed line */
static int
swg_d4_row(struct swg_wx *x, int r)
{
	uint32_t bp = 0, old = 0, rb = 0, w;
	int sp = 0;

	if (x->stopped) {
		return -SWG_E_STOPPED;
	}
	x->step = SWG_ST_N + 1u + (unsigned)r;
	x->fail = swg_d4_name[r];
	if (!x->entered || (x->d4mask & (1ull << r)) != 0u || (r != 0 && (x->d4mask & (1ull << (r - 1))) == 0u)) {
		return swg_stop(x, SWG_E_ORDER);
	}
	if (swg_d4_addr(x, r, &sp, &bp) || (!SWG_D4_ISDB(r) && x->b->rd(x->b->ctx, sp, bp, &old))) {      /* review r1 F6: the doorbell is never read (the golden only writes it, seq 437/438; DHD ringbell only writes) */
		return swg_stop(x, SWG_E_ADDR);
	}
	x->old = old;
	if (old == 0xFFFFFFFFu) {
		return swg_stop(x, SWG_E_DEAD);
	}
	if (old != swg_d4_oldv(x, r)) {     /* a slot holds the previous index (swg_d4_oldv), every other word 0 */
		return swg_stop(x, SWG_E_OLD);
	}
	w = swg_d4_val(x, r, old);
	/* swg_put nonzero = nothing stored (the glue's contract, as SWT_ARMST): the bit is set after it, so a refused mask arms no unmask (review r2 F11) */
	if (swg_put(x, sp, bp, w)) {
		return swg_stop(x, SWG_E_WR);
	}
	x->d4tried |= 1ull << r;
	if (r == SWG_D4C_A_DB) {
		x->d4sleep = 1u;
	} else if (r == SWG_D4C_W_DB) {
		x->d4sleep = 0u;
	}
	if (SWG_D4_ISDB(r)) {
		x->rb = 0;
		printf("SWEET WLAN: G2 store %s off %#x := %#x (old and read-back not made, golden writes only)\n", swg_d4_name[r], bp, w);
		x->d4mask |= 1ull << r;
		return 0;
	}
	if (x->b->rd(x->b->ctx, sp, bp, &rb)) {
		return swg_stop(x, SWG_E_READBACK);
	}
	x->rb = rb;
	if (rb != w) {
		return swg_stop(x, SWG_E_READBACK);
	}
	printf("SWEET WLAN: G2 store %s %s %#x old %#x := %#x read back %#x\n", swg_d4_name[r], sp == SWG_SP_HOST ? "host" : "off", bp, old, w, rb);
	x->d4mask |= 1ull << r;
	return 0;
}

/* W-D4c: the host-memory words the DS_EXIT_NOTE poll reads (no TCM, no core register: the dongle may be in deep sleep) */
struct swg_d4e {
	uint32_t h2dr, d2hw, r2[6];     /* H2D-RD slot 0, D2H-WR slot 0, ring-2 note item (round 0: item 1) */
};

/* the host words the DS_EXIT_NOTE poll reads: H2D-RD slot 0, D2H-WR slot 0 and the 6 words of ring-2 slot `slot` (22b: round 0 reads slot 1) */
static int
swg_d4c_hread(struct swg_wx *x, struct swg_d4e *e, uint32_t slot)
{
	uint32_t i;

	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)], &e->h2dr) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &e->d2hw)) {
		return -1;
	}
	for (i = 0; i < 6u; i++) {
		if (swg_hrd(x, x->in.ring[SWD_D2H_CTRL - SWD_H2D_CTRL] + 4u * i + (uint64_t)slot * swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].isz, &e->r2[i])) {
			return -1;
		}
	}
	return 0;
}

/* the stops of every TCM read of the handshake (as the L14 poll): assert/trap in the shared flags, FWHALT in the d2h mailbox data. 0 = none; negative = stopped (swg_stop ran swg_undo) */
static int
swg_d4c_stops(struct swg_wx *x, const struct swg_d4s *s, const char *when)
{
	if ((s->pw.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {
		printf("SWEET WLAN: D4c %s flags 0x%x mb data 0x%x TRAP/ASSERT\n", when, s->pw.fl, s->pw.mb);
		x->fail = "the firmware reports assert/trap during the d4c handshake";
		return swg_stop(x, SWG_E_FATAL);
	}
	if ((s->pw.mb & SWG_D2H_FWHALT) != 0u) {
		printf("SWEET WLAN: D4c %s mb data 0x%x FWHALT\n", when, s->pw.mb);
		x->fail = "the firmware reports D2H_DEV_FWHALT in the d2h mailbox during the d4c handshake";
		return swg_stop(x, SWG_E_FATAL);
	}
	return 0;
}

/* rows from..to in order; 0 = all stored, else the stop */
static int
swg_d4c_rows(struct swg_wx *x, int from, int to)
{
	int r, ex;

	for (r = from; r <= to; r++) {
		ex = swg_d4_row(x, r);
		if (ex != 0) {
			return ex;
		}
	}
	return 0;
}

_Static_assert(SWG_D4C_DB_ACK == (SWG_DB_WR | 2u) && SWG_D4C_DB_WAKE == (SWG_DB_WR | 3u) && SWG_D4C_DB_DEAS == (SWG_DB_WR | 4u) && SWG_D4C_DB_RD1 == (SWG_DB_RD | 1u) && SWG_D4C_DB_RD2 == (SWG_DB_RD | 2u), "round 0 (L16) keeps today's doorbell values");
_Static_assert(SWG_D4C_EP_ACK == (254u + 1u) % 253u && SWG_D4C_EP_WAKE == (254u + 2u) % 253u && SWG_D4C_EP_DEAS == (254u + 3u) % 253u && SWG_D4C_EP_NOTE == (254u + 1u) % 253u, "round 0 (L16) keeps today's epochs");
#define SWG_EPOCH_INIT   254u           /* H2D_EPOCH_INIT_VAL = D2H_EPOCH_INIT_VAL = 253 + 1 [src MB:35-39]; item epoch = (254 + ring item index) % 253 [src M:6357, 10883, 1500; no ring wrap before 128 / 64 items] */
#define SWG_EPOCH_MOD    253u           /* [src MB:35, 38] */

static uint8_t
swg_ep(uint32_t idx)
{
	return (uint8_t)((SWG_EPOCH_INIT + idx) % SWG_EPOCH_MOD);
}

/* 22b: ONE DS_ENTER_REQ round, the L16 handshake parameterised by the CURRENT ring-0 write index (x->d4c_wr: the ack goes to item d4c_wr, the wake to +1, the deassert (round 0 only) to +2) and ring-2 read index (x->d4c_rd, absolute:
 * the request is item d4c_rd, the exit note d4c_rd + 1). rn = 0 is L16 (today's exact stores: wr 1, rd 0; a miss is a WIFI GAP and rc 0, d4c_ok stays 0); rn >= 1 is an extra round (a miss is a loud STOP, the same undo path; d4sleep is set at the ack doorbell and cleared at the wake doorbell
 * exactly as in round 0, so a stop between them runs the same undo and W17 restore, review d4c r2 F1). The caller proved the request item (type 0x24, epoch, XOR, mailbox data exactly DS_ENTER_REQ). 0 = done, or round 0 refused with a gap; negative = stopped */
static int
swg_ds_round(struct swg_wx *x, unsigned rn)
{
	struct swg_d4s cur;
	struct swg_d4e e;
	const uint32_t wr = x->d4c_wr, rd = x->d4c_rd, ns = (rd + 1u) % SWG_R2_DEPTH, ne = (rd + 2u) % SWG_R2_DEPTH;
	uint32_t i, n, waited = 0;
	uint8_t in[SWD4B_D2H_LEN], it[SWD4B_ITEM_LEN], type = 0;
	int ex;
	char gb[64], pf[24];

	if (rn == 0u) {
		snprintf(pf, sizeof pf, "D4c");
	} else {
		snprintf(pf, sizeof pf, "D4 DS round %u", rn);
		x->d4mask &= ~SWG_D4C_ROWMASK;      /* the rows of the previous round are done: this round makes them again, in the same order (the d4b rows before them stay done) */
		swl_gap("d4ds", "DS hold, no DEASSERT", "medium");      /* research 124 s.4: deep_sleep 0 equivalent (dhd_pcie.c:8132-8148, 13076-13085); the host_active_cnt deferral is NOT the cause of the repeats (s.3) and stays unimplemented: host_active_cnt is 0 whenever this single-threaded port looks at ring 2 */
	}
	swd4b_item_mbdata(it, SWD4B_H2D_DS_ACK, swg_ep(wr));
	for (i = 0; i < SWG_D4_ITEMW; i++) {
		x->d4cw[0][i] = swd4b_get32(it + 4u * i);
	}
	swd4b_item_mbdata(it, SWD4B_H2D_DS_WAKE, swg_ep(wr + 1u));
	for (i = 0; i < SWG_D4_ITEMW; i++) {
		x->d4cw[1][i] = swd4b_get32(it + 4u * i);
	}
	swd4b_item_mbdata(it, SWD4B_H2D_DS_DEAS, swg_ep(wr + 2u));
	for (i = 0; i < SWG_D4_ITEMW; i++) {
		x->d4cw[2][i] = swd4b_get32(it + 4u * i);
	}
	ex = swg_d4c_rows(x, SWG_D4C_A_ITEM0, SWG_D4C_C1_DB);       /* A1 (the ack), then C1 (the read index) */
	if (ex != 0) {
		return ex;
	}
	if (rn == 0u) {
		printf("SWEET WLAN: D4c ring-2 item 0 DS_ENTER_REQ answered (DS_ACK)\n");
	}
	ex = swg_d4c_rows(x, SWG_D4C_W_ITEM0, SWG_D4C_W_DB);        /* W (the device wake) */
	if (ex != 0) {
		return ex;
	}
	x->b->delay_ms(x->b->ctx, 1u);      /* OSL_DELAY(1000), dhd_pcie.c:12300ff */
	waited = 1u;
	x->step = SWG_ST_N + 1u + SWG_D4C_W_DB;
	for (n = 0; n < SWG_POLLS; n++) {
		x->fail = "a d4c exit-note poll read failed";
		if (swg_d4c_hread(x, &e, ns)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (e.h2dr == 0xFFFFFFFFu || e.d2hw == 0xFFFFFFFFu) {
			x->fail = "host h2d_r[0] or d2h_w[0] reads all ones (dead link) in the d4c exit-note poll";
			return swg_stop(x, SWG_E_DEAD);
		}
		if ((e.d2hw & 0xFFFFu) != ns) {
			break;
		}
		if (n + 1u < SWG_POLLS) {
			x->b->delay_ms(x->b->ctx, SWG_POLL_MS);
			waited += SWG_POLL_MS;
		}
	}
	printf("SWEET WLAN: %s exit-note poll after %u ms: h2d_r0 0x%x d2h_w0 0x%x ring2 item %u w 0x%x 0x%x 0x%x 0x%x 0x%x 0x%x\n", pf, waited, e.h2dr, e.d2hw, rd + 1u, e.r2[0], e.r2[1], e.r2[2], e.r2[3], e.r2[4], e.r2[5]);
	gb[0] = '\0';
	if ((e.d2hw & 0xFFFFu) == ns) {
		snprintf(gb, sizeof gb, "no DS_EXIT_NOTE in %u ms after the wake assert", waited);
	} else if ((e.d2hw & 0xFFFFu) != ne) {
		snprintf(gb, sizeof gb, "d2h ring 2 write index %u after the wake, expected %u", e.d2hw & 0xFFFFu, ne);
	} else {
		for (i = 0; i < SWD4B_D2H_LEN; i++) {
			in[i] = (uint8_t)(e.r2[i / 4u] >> (8u * (i % 4u)));
		}
		ex = swd4b_d2h_check(in, swg_ep(rd + 1u), &type);
		if (ex != 0) {
			snprintf(gb, sizeof gb, "ring-2 item %u failed the epoch/xor check (%d)", rd + 1u, ex);
		} else if (type != SWD4B_MSG_D2H_MBDATA) {
			snprintf(gb, sizeof gb, "ring-2 item %u type 0x%x is not D2H_MAILBOX_DATA", rd + 1u, type);
		} else if (e.r2[3] != SWD4B_D2H_DS_EXIT_NOTE) {
			snprintf(gb, sizeof gb, "ring-2 item %u mailbox data 0x%x is not DS_EXIT_NOTE only", rd + 1u, e.r2[3]);
		}
	}
	if (gb[0] != '\0') {
		if (rn == 0u) {
			swl_gap("d4c", gb, "high");
			return 0;       /* no deassert: stock stays in DEV_WAKE (d4c_ok stays 0) */
		}
		printf("SWEET WLAN: D4 DS round %u STOP, no deassert: %s\n", rn, gb);
		x->fail = "the DS round got no valid DS_EXIT_NOTE after the wake assert";
		return swg_stop(x, SWG_E_FATAL);
	}
	if (rn == 0u) {
		ex = swg_d4c_rows(x, SWG_D4C_D_ITEM0, SWG_D4C_C2_DB);   /* D (the deassert), then C2 (the read index) */
	} else {
		/* hold round: no D rows (no item, no slot, no doorbell); their d4mask bits are set WITHOUT a store (swg_d4_row keeps the strict order, C2 needs D_DB done), d4tried untouched */
		x->d4mask |= ((1ull << (SWG_D4C_D_DB + 1)) - 1ull) & ~((1ull << SWG_D4C_D_ITEM0) - 1ull);
		printf("SWEET WLAN: D4 DS round %u: DEVICE_WAKE_DEASSERT NOT sent, device_wake held (deep_sleep 0 equivalent, dhd_pcie.c:8132-8148, 13076-13085)\n", rn);
		ex = swg_d4c_rows(x, SWG_D4C_C2_SLOT, SWG_D4C_C2_DB);   /* C2 (the read index) */
	}
	if (ex != 0) {
		return ex;
	}
	x->step = SWG_ST_N + 1u + SWG_D4C_C2_DB;
	x->fail = "the d4c final read failed";
	if (swg_d4_read(x, &cur, 0u)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (swg_d4_ones(x, &cur)) {
		return swg_stop(x, SWG_E_DEAD);
	}
	ex = swg_d4c_stops(x, &cur, "final read");
	if (ex != 0) {
		return ex;
	}
	x->d4rd = rd + 2u;
	if (rn == 0u) {
		x->d4wr = wr + 3u;
		printf("SWEET WLAN: D4c DS handshake done (ack, wake, exit note, deassert)\n");
		x->d4c_ok = 1u;
	} else {
		x->d4wr = wr + 2u;
		x->d4ds = (uint8_t)rn;
		x->dshold = 1u;
		printf("SWEET WLAN: D4 DS round %u answered (DS_ACK, wake, exit note, DS hold, no deassert): ring-2 items %u..%u, ring-0 items %u..%u, ring-0 WR %u -> %u, ring-2 RD %u -> %u\n", rn, rd, rd + 1u, wr, wr + 1u, wr, wr + 2u, rd, rd + 2u);
	}
	return 0;
}

/* W-D4c (sweetwlan=16 only): the stock inband-device-wake sequence for the dongle's DS_ENTER_REQ, docs/research/W-D4c-ds-ack.md s.6.5 steps 3-7 [src + obs golden TW4:549990-550034].
 * The dongle pre-wrote ring-2 item 0 [obs wlan-runG2-d4b13: type 0x24, epoch 1, XOR ok, d2h_mailbox_data u32 at byte 12 = r2[3] = 0x2; bcmmsgbuf.h:900-907, bcmpcie.h:511]. INB_DW is on (hostcap 0x2000, fw flags 0x40000000: review d4c r1 F1; dhd_msgbuf.c:3904-3908, dhd_pcie.c:12953-12990), host_active_cnt is 0 so DHD acks at once with a ring-0 item (use_mailbox 0, flags 0x70050107, dhd_pcie.c:13893, 12788-12803), in this order (review d4c r1 F2):
 *   A1 ack: ring-0 item 1 (H2D_HOST_DS_ACK 0x2, bcmpcie.h:485, epoch 2), H2D-WR slot 0 := 2, doorbell 0 := FF000002 (dhd_msgbuf.c:7524-7532: the item first, the read index after);
 *   C1 consume: D2H-RD slot of ring 2 := 1, doorbell 0 := DD020001. From the ack doorbell to the end of E the header makes no TCM, 0xC30 or backplane access (the dongle may be in DEV_SLEEP, s.6.4). The GLUE does read EP config 0x80 (non-posted CfgRd) before each BAR0 store, the C1 and W doorbells included, while the dongle may be in DEV_SLEEP; stock rings without reads (dhd_pcie.c:12562-12600); kept on purpose (window validation), tracked as U3 [obs review d4c r2 F2];
 *   W wake: ring-0 item 2 (DEVICE_WAKE_ASSERT 0x40, bcmpcie.h:490, epoch 3), H2D-WR := 3, doorbell 0 := FF000003, then 1 ms (OSL_DELAY(1000); the exit wait is dead code, dhd_pcie.c:12255-12330). W timing vs stock [obs review d4c r2 F5]: stock rings W 16.3 ms after the ack (TW4:549992 -> 550000), G2 rings it microseconds after the ack (no delay between the rows) [src]; whether the firmware still sends an EXIT_NOTE is unknown [guess];
 *   E poll of HOST MEMORY ONLY for ring-2 item 1 = D2H_DEV_DS_EXIT_NOTE 0x4 (bcmpcie.h:512); accepted only as D2H-WR 2, type 0x24, epoch 2, XOR ok, mailbox data exactly 0x4;
 *   D deassert, only on an accepted note (dhd_pcie.c:13036-13086, 12340-12345): ring-0 item 3 (H2DMB_DS_ACTIVE 0x20, bcmpcie.h:489, epoch 4), H2D-WR := 4, doorbell 0 := FF000004; then C2: D2H-RD := 2, doorbell 0 := DD020002;
 *   then ONE full read (TCM too) with the stops of the L14 poll. A miss (no note, wrong item) is a WIFI GAP with no deassert (stock stays DEV_WAKE) and rc 0.
 * Every failed precondition is a WIFI GAP with its reason and NO store (rule 8). 0 = done or refused with a gap; negative = stopped (swg_stop ran swg_undo). */
static int
swg_d4c(struct swg_wx *x, int wrap)
{
	const struct swg_d4s *b = &x->d4base;
	struct swg_d4s q;
	uint32_t i, n, mb = b->r2[3];
	uint8_t in[SWD4B_D2H_LEN], type = 0;
	int ex;
	const char *no = NULL;
	char gb[64];

	for (i = 0; i < SWD4B_D2H_LEN; i++) {
		in[i] = (uint8_t)(b->r2[i / 4u] >> (8u * (i % 4u)));
	}
	ex = (((b->pw.dw[0] | b->d2hw) & 0xFFFFu) != 0u && (b->r2[0] | b->r2[1] | b->r2[2] | b->r2[3] | b->r2[4] | b->r2[5]) != 0u) ? swd4b_d2h_check(in, (uint8_t)SWG_D4_EPOCH, &type) : -1;
	if (wrap) {
		no = "d2h ring 2 wrapped before the post, nothing answered";
	} else if (ex < 0) {
		no = "no ring-2 item 0 at the baseline, nothing to answer";
	} else if (ex != 0) {
		no = "ring-2 item 0 failed the epoch/xor check, not answered";
	} else if (type != SWD4B_MSG_D2H_MBDATA) {
		no = "ring-2 item 0 is not D2H_MAILBOX_DATA, not answered";
	} else if ((mb & ~SWD4B_D2H_DS_ENTER_REQ) != 0u) {
		snprintf(gb, sizeof gb, "d2h mailbox bits 0x%x not handled", mb & ~SWD4B_D2H_DS_ENTER_REQ);
		no = gb;
	} else if ((mb & SWD4B_D2H_DS_ENTER_REQ) == 0u) {
		no = "ring-2 item 0 mailbox data has no DS_ENTER_REQ, not answered";
	}
	if (no == NULL) {
		x->fail = "the d4c pre-answer read failed";
		if (swg_d4_read(x, &q, 0u)) {       /* the D2H-WR slot as it is now, not as the L14 poll left it */
			return swg_stop(x, SWG_E_ADDR);
		}
		if (swg_d4_ones(x, &q)) {
			return swg_stop(x, SWG_E_DEAD);
		}
		ex = swg_d4c_stops(x, &q, "pre-answer read");     /* review d4c r1 F4: a trap/assert/FWHALT since the baseline stops before any store */
		if (ex != 0) {
			return ex;
		}
		n = q.d2hw & 0xFFFFu;
		if (memcmp(q.r2, b->r2, sizeof q.r2) != 0) {
			no = "ring-2 item 0 changed since the baseline, not answered";
		} else if (n > 1u) {
			snprintf(gb, sizeof gb, "d2h ring 2 holds items 1..%u, not consumed, no answer", n - 1u);
			no = gb;
		} else if (n == 0u) {
			no = "d2h_w0 is 0 now, item 0 is not there, no answer";
		}
	}
	if (no != NULL) {
		swl_gap("d4c", no, "high");
		printf("SWEET WLAN: D4c not answered: %s (item 0 type 0x%x mailbox data 0x%x epoch/xor check %d)\n", no, type, mb, ex);
		return 0;
	}
	x->d4c_wr = 1u;     /* round 0: ring 0 holds the L14 item 0, the ack goes to item 1; ring 2: the request is item 0 */
	x->d4c_rd = 0u;
	return swg_ds_round(x, 0u);
}
/* ---- W-D4d (sweetwlan 17..20, docs/research/W-D4d-ioctl-get-version.md = R-D4d; M = dhd_msgbuf.c, MB = bcmmsgbuf.h of refs/wlan-stage5, LX = refs/wlan-stage1/bcm4389-dhd_linux.c). Runs only after the L16 handshake completed (d4c_ok). ---- L17: ONE ioctl-response buffer post (type 0x0B) at the current ring-0 write index, then a READ-ONLY watch of ring 2. L18: + ONE WLC_GET_VERSION request (type 0x09), ack and completion read and printed, no read-index update. L19: + the read-index update, the response bytes printed, ONE response buffer re-posted. L20: + ONE GET_VAR "cap" the same way. Every store is a row of swg_d4d_seq (kind and order fixed), made through swg_d4d_st, and an entry of the glue's own list. Store order per item: payload (flushed by the glue after the store) -> the 40 B item -> H2D-WR slot -> doorbell 0 (R-D4d s.2.2 order, M:10904-10922). A gap in this block stops the REMAINING levels (d4dstop): no further store, rc 0 (rule 8). */
#define SWG_D4D_IDX0     4u             /* ring-0 index of the first W-D4d item: L14 event post (0) + L16 DS_ACK, wake, deassert (1..3) are in [src SWG_D4C_* rows above]; the live H2D-WR slot is read and compared */
#define SWG_D4D_RD0      2u             /* ring-2 read index after L16: items 0 (DS_ENTER_REQ) and 1 (EXIT_NOTE) consumed, C2 stored 2 [src SWG_D4C_C2_SLOT above] */
#define SWG_D4D_DB_WR    0xFF000000u    /* doorbell 0 after a ring-0 write-index update: 0xFF000000 | (ring idx 0 << 16) | wr [src M:116 (DHD_WRPTR_UPDATE_H2D_DB_MAGIC)] */
#define SWG_D4D_DB_RD    0xDD020000u    /* doorbell 0 after a ring-2 read-index update: 0xDD000000 | (ring idx 2 << 16) | rd [src M:114, 7524-7532] */
#define SWG_D4D_RID0     2u             /* request_id of the response buffer of the n-th post = SWG_D4D_RID0 + n (our own ids, never 0 = DHD_PKTID_INVALID, DHD_PKTID_INVALID (0U), M:2278; 1 is the L14 event buffer) [src M:6355] */
#define SWG_D4D_TRANS0   0x0100u        /* our own start of the ioctl transaction id; the n-th request uses TRANS0 + n (DHD pre-increments, M:10889-10890); any value the completion echoes [guess: the firmware echoes it, R-D4d s.2.4] */
#define SWG_D4D_VER_LEN  4u             /* WLC_GET_VERSION: one int in and out [src R-D4d s.2.2; BW bwfm.c:1799-1805] */
#define SWG_D4D_CAP_LEN  1023u          /* GET_VAR "cap": the ioctl length = res_len of dhd_iovar = sizeof(fw_capabilities) - 1 [src LX:10855-10862 (cap_buf_size - 1); WLC_IOCTL_MAXBUF_FWCAP 1024, bcm4389-dhd.h:1411; in-file pattern M:5366-5375 (bzero buf, bcm_mkiovar name, dhd_wl_ioctl_cmd(WLC_GET_VAR, buf, sizeof(buf))); the 1023 is [guess: dhd_iovar body not in refs]; never asserted against a dongle answer] */
#define SWG_D4D_CAP_W0   0x00706163u    /* "cap\0" little endian: the iovar name, NUL terminated, at the start of the payload buffer (a GET_VAR carries the name) [src wlioctl_defs.h.new:946 "get value of named variable"; LX:10861 name "cap"] */
#define SWG_D4D_RSP_MAX  1024u          /* response bytes read for printing: cap's out length 1023 + NUL */
_Static_assert(SWG_D4D_RSP_MAX % 4u == 0u, "swg_d4d_rsp stores whole words into a buffer of SWG_D4D_RSP_MAX bytes");
#define SWG_D4D_MAXIT    4u             /* ring-2 items shown per scan */
#define SWG_D4D_LINE     120u           /* characters of cap text per log line (a line stays below 200 chars) */

enum { SWG_K_ITEM, SWG_K_PAY, SWG_K_H2DW, SWG_K_D2HR, SWG_K_DB };
#define SWG_D4D_ITEMS    SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM, SWG_K_ITEM
#define SWG_D4D_POST     SWG_D4D_ITEMS, SWG_K_H2DW, SWG_K_DB
/* the store sequence of all four levels; swg_d4d_cut[d4b - 4] = how many rows a level may make */
static const uint8_t swg_d4d_seq[] = {
	SWG_D4D_POST,                                                   /* L17: response buffer post (12 rows) */
	SWG_D4D_POST,                                                   /* L18: GET_VERSION request (12 rows; its 4 B payload is the zeroed start of the payload buffer, no store) */
	SWG_K_D2HR, SWG_K_DB, SWG_D4D_POST,                             /* L19: ring-2 read index + doorbell, response buffer re-post (14 rows) */
	SWG_K_PAY, SWG_D4D_POST, SWG_K_D2HR, SWG_K_DB, SWG_D4D_POST,    /* L20: "cap\0" payload, GET_VAR request, read index + doorbell, re-post (27 rows) */
};
static const uint8_t swg_d4d_cut[4] = { 12, 24, 38, 65 };
_Static_assert(sizeof swg_d4d_seq == 65, "the W-D4d store sequence is 12 + 12 + 14 + 27 rows");

#define SWG_D4D_GAP(x, ...) do { char gb_[64]; snprintf(gb_, sizeof gb_, __VA_ARGS__); swl_gap("d4d", gb_, "high"); (x)->d4dstop = 1u; printf("SWEET WLAN: D4d STOP, no further store: %s\n", gb_); } while (0)

/* a finished D4d level: the highest level reached, and (when it is the requested one) the one level-end line */
static void
swg_d4d_done(struct swg_wx *x, unsigned n, unsigned lv, uint32_t wr, uint32_t rd)
{
	x->d4dlv = (uint8_t)n;
	if (lv == n || (n == 20u && lv > 20u)) {     /* W-D4e: the L20 end state is the line the scan24 run plan checks at sweetwlan=21..23 */
		printf("SWEET WLAN: D4d L%u done (ring-0 WR %u, ring-2 RD %u)\n", n, wr, rd);
	}
}

enum { SWG_OP_NONE, SWG_OP_POST, SWG_OP_CONS, SWG_OP_PAY, SWG_OP_DPFC, SWG_OP_DPPOST, SWG_OP_DPTX, SWG_OP_DPCONS };   /* DP15: flow create, ring-1/flow post, tx (payload + flow post), D2H consume */

/* W-D4e: the kind of row `row` of an operation: a ring-0 post is 10 item words, the H2D-WR slot, doorbell 0 (swg_d4d_post); a ring-2 consume is the D2H-RD slot, doorbell 0 (swg_d4d_consume); a payload word is one row (swg_e_pay). -1 = no such row */
static int
swg_e_kind(unsigned op, uint32_t row)
{
	if (op == SWG_OP_POST) {
		return row < 10u ? SWG_K_ITEM : row == 10u ? SWG_K_H2DW : row == 11u ? SWG_K_DB : -1;
	}
	if (op == SWG_OP_CONS) {
		return row == 0u ? SWG_K_D2HR : row == 1u ? SWG_K_DB : -1;
	}
	if (op == SWG_OP_PAY) {
		return row == 0u ? SWG_K_PAY : -1;
	}
	return -1;
}

/* DP15 (TRANSPORT s.1 item 4): the kind of row `row` of a data-path plan. flow create = [H2DW, ITEM*, H2DW, DB]; ring-1 / flow post = [ITEM*, H2DW, DB]; tx = [PAY*, ITEM*, H2DW, DB]; consume = [D2HR, DB]. pw / iw = payload / item words. -1 = no such row */
static int
swg_dp_kind(unsigned op, uint32_t row, uint32_t pw, uint32_t iw)
{
	uint32_t r = row;

	if (op == SWG_OP_DPCONS) {
		return r == 0u ? SWG_K_D2HR : r == 1u ? SWG_K_DB : -1;
	}
	if (op == SWG_OP_DPFC) {
		if (r == 0u) {
			return SWG_K_H2DW;
		}
		r--;
	}
	if (op == SWG_OP_DPTX) {
		if (r < pw) {
			return SWG_K_PAY;
		}
		r -= pw;
	}
	if (op != SWG_OP_DPFC && op != SWG_OP_DPPOST && op != SWG_OP_DPTX) {
		return -1;
	}
	return r < iw ? SWG_K_ITEM : r == iw ? SWG_K_H2DW : r == iw + 1u ? SWG_K_DB : -1;
}

/* the kind row `erow` of the operation being made must have (W-D4e operations and DP15 plans) */
static int
swg_e_kindx(const struct swg_wx *x)
{
	if (x->eop >= SWG_OP_DPFC) {
		return swg_dp_kind(x->eop, x->erow, x->dppay, x->dpitem);
	}
	return swg_e_kind(x->eop, x->erow);
}

/* one W-D4d store: kind and order from swg_d4d_seq, ONCE, the old word required (oldv; the doorbell is never read), the store, the read-back (not the doorbell), the printed line. low16: a write-index slot, whose upper half is kept (D-9) */
static int
swg_d4d_st(struct swg_wx *x, int kind, int sp, uint32_t bp, uint32_t oldv, uint32_t v, int low16, const char *nm)
{
	uint32_t old = 0, rb = 0, w = v;

	if (x->stopped) {
		return -SWG_E_STOPPED;
	}
	x->step = SWG_ST_N + 1u + SWG_D4_N + x->d4dn;
	x->fail = nm;
	if (!x->entered || x->d4b < 4 || x->d4b > 13 || (x->d4dn < sizeof swg_d4d_seq ? ((x->d4b <= 7 && x->d4dn >= swg_d4d_cut[x->d4b - 4]) || swg_d4d_seq[x->d4dn] != kind) : (x->d4b < 8 || swg_e_kindx(x) != kind))) {
		return swg_stop(x, SWG_E_ORDER);        /* rows 0..64: the L17..L20 table; later rows (W-D4e): the operation being made fixes the kind of its next row */
	}
	if (kind != SWG_K_DB) {
		if (x->b->rd(x->b->ctx, sp, bp, &old)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		x->old = old;
		if (old == 0xFFFFFFFFu && (kind != SWG_K_PAY || x->dpx != 0u) && !(x->dpx != 0u && oldv == 0xFFFFFFFFu)) {      /* sprint 2026-11-08: DP15 reuse of a word the engine itself stored as all ones (a broadcast DA in a flow item, an all-ones payload word) is not a dead link: the shadow says so; the shadow compare below still catches a mismatch */
			/* L24: a payload word of host memory may legitimately be all ones (the escan bssid / times, the join scan params, are -1): its old value is still required to equal the shadow below. DP15 (SPEC-WIFI TRANSPORT s.1 item 5): all-ones read = dead link for every word, payload included (merge of T-75c7 with T-23da, T-e6e4) */
			return swg_stop(x, SWG_E_DEAD);
		}
		if (x->dpx != 0u && low16 ? ((old ^ oldv) & 0xFFFFu) != 0u : old != oldv) {      /* DP15: an index slot compares its low 16 bits only (TRANSPORT s.1 item 5) */
			return swg_stop(x, SWG_E_OLD);
		}
		if (low16) {
			w = (old & 0xFFFF0000u) | (v & 0xFFFFu);
		}
	}
	if (swg_put(x, sp, bp, w)) {
		return swg_stop(x, SWG_E_WR);
	}
	x->d4dn++;
	x->erow++;
	if (kind == SWG_K_DB) {
		x->rb = 0;
		if (x->quiet) {
			x->nquiet++;
			return 0;
		}
		printf("SWEET WLAN: G2 store %s off %#x := %#x (old and read-back not made, golden writes only)\n", nm, bp, w);
		return 0;
	}
	if (x->b->rd(x->b->ctx, sp, bp, &rb)) {
		return swg_stop(x, SWG_E_READBACK);
	}
	x->rb = rb;
	if (rb != w) {
		return swg_stop(x, SWG_E_READBACK);
	}
	if (x->dpx != 0u && kind == SWG_K_PAY) {
		return 0;       /* DP15: frame words are never printed; the executor prints "payload <len> B" once */
	}
	if (x->quiet) {
		x->nquiet++;        /* sprint 2026-11-08: the read-back was compared above; only the line is skipped */
		return 0;
	}
	if (x->dpx != 0u && x->dpsec != 0u) {
		printf("SWEET WLAN: G2 store %s host %#x (value withheld)\n", nm, bp);     /* TRANSPORT s.1 item 7: the read-back was compared silently */
		return 0;
	}
	if (x->esec) {
		printf("SWEET WLAN: G2 store %s host %#x (value withheld)\n", nm, bp);      /* a secret payload word: neither the new, the old nor the read-back value is printed */
	} else {
		printf("SWEET WLAN: G2 store %s host %#x old %#x := %#x read back %#x\n", nm, bp, old, w, rb);
	}
	return 0;
}

#ifdef SWEET_WLAN_DATA_H
/* Part A (sprint-wifi 2026-11-09): nw consecutive DP15 ITEM or PAYLOAD words in ONE batch, only in quiet mode (no per-word line is printed there either). The same checks as nw calls of swg_d4d_st, word for word:
 * the kind of every row (all made BEFORE the first store), the old word of every word read (one cache pass) and compared with its shadow (the all-ones dead-link rule included), one batched store
 * (the glue judges every word), the read-back of every word compared. d4dn / erow / nquiet / old / rb end as after the per-word calls. Words: file-scope BSS (T-73fc: no 1500 B stack arrays) */
#define SWG_BLK_MAX 400u
static uint32_t swg_blk_old[SWG_BLK_MAX], swg_blk_rb[SWG_BLK_MAX];
static int
swg_d4d_st_blk(struct swg_wx *x, int kind, int sp, uint32_t bp, const uint32_t *shw, const uint32_t *v, uint32_t nw, const char *nm)
{
	const uint32_t d0 = x->d4dn, e0 = x->erow;
	uint32_t k;

	if (x->stopped) {
		return -SWG_E_STOPPED;
	}
	x->fail = nm;
	if (nw == 0u || nw > SWG_BLK_MAX || !x->quiet || x->dpx == 0u || (kind != SWG_K_ITEM && kind != SWG_K_PAY) || x->b->rd_blk == NULL) {
		return swg_stop(x, SWG_E_ORDER);
	}
	for (k = 0; k < nw; k++) {      /* the row check of swg_d4d_st for each word, in order (rows past the L17..L20 table: the operation fixes the kind of each row) */
		x->step = SWG_ST_N + 1u + SWG_D4_N + x->d4dn;
		if (!x->entered || x->d4b < 4 || x->d4b > 13 || (x->d4dn < sizeof swg_d4d_seq ? ((x->d4b <= 7 && x->d4dn >= swg_d4d_cut[x->d4b - 4]) || swg_d4d_seq[x->d4dn] != kind) : (x->d4b < 8 || swg_e_kindx(x) != kind))) {
			return swg_stop(x, SWG_E_ORDER);
		}
		x->d4dn++;
		x->erow++;
	}
	x->d4dn = d0;
	x->erow = e0;
	if (x->b->rd_blk(x->b->ctx, sp, bp, swg_blk_old, nw)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	for (k = 0; k < nw; k++) {
		x->old = swg_blk_old[k];
		if (swg_blk_old[k] == 0xFFFFFFFFu && (kind != SWG_K_PAY || x->dpx != 0u) && !(x->dpx != 0u && shw[k] == 0xFFFFFFFFu)) {
			return swg_stop(x, SWG_E_DEAD);
		}
		if (swg_blk_old[k] != shw[k]) {
			return swg_stop(x, SWG_E_OLD);
		}
	}
	if (swg_put_blk(x, sp, bp, v, nw)) {
		return swg_stop(x, SWG_E_WR);
	}
	x->d4dn = d0 + nw;
	x->erow = e0 + nw;
	if (x->b->rd_blk(x->b->ctx, sp, bp, swg_blk_rb, nw)) {
		return swg_stop(x, SWG_E_READBACK);
	}
	for (k = 0; k < nw; k++) {
		x->rb = swg_blk_rb[k];
		if (swg_blk_rb[k] != v[k]) {
			return swg_stop(x, SWG_E_READBACK);
		}
	}
	if (kind == SWG_K_ITEM) {
		x->nquiet += nw;            /* swg_d4d_st counts a quiet item word; a DP15 payload word returns before the count */
	}
	return 0;
}

#endif /* SWEET_WLAN_DATA_H */

/* a ring-0 post: the ten item words at the host's current ring-0 slot (x->d4wr, 22b: moved by DS rounds), the H2D-WR slot idx -> next, doorbell 0 := 0xFF000000 | next [src M:116 DHD_WRPTR_UPDATE_H2D_DB_MAGIC];
 * x->d4wr := next. Sprint 2026-11-08: the ring wraps (next = (idx + 1) % depth, DHD's ring write pointer wraps the same way); the old word of each item word is the slot's shadow (0 on the first lap), and the slot
 * is refused while the dongle's ring-0 read index (H2D-RD slot 0) says the ring is full (next == rd). 0 = made; negative = stopped */
static int
swg_d4d_post(struct swg_wx *x, const uint8_t it[SWD4B_ITEM_LEN])
{
	const uint32_t idx = x->d4wr, nwr = swg_r0_next(x);
	uint32_t k, off, rd = 0u, *sh;
	int ex;

	x->fail = "the d4d ring-0 item is outside the G1 block or the ring-0 slot is past the depth";
	if (idx >= swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items || swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items != SWG_R0_DEPTH) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->fail = "the d4d ring-0 read index (H2D-RD slot 0) is unreadable, outside the ring, or the ring is full";
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)], &rd) || rd == 0xFFFFFFFFu || (rd & 0xFFFFu) >= SWG_R0_DEPTH || nwr == (rd & 0xFFFFu)) {
		printf("SWEET WLAN: D4d ring-0 post refused: slot %u next %u h2d_rd %#x\n", idx, nwr, rd);
		return swg_stop(x, SWG_E_ADDR);
	}
	if (nwr == 0u) {
		printf("SWEET WLAN: D4d ring-0 wrap: slot %u -> 0 (lap %u, h2d_rd %u)\n", idx, x->d4wrap0 + 1u, rd & 0xFFFFu);
	}
	x->eop = SWG_OP_POST;
	x->erow = 0u;
	for (k = 0; k < SWG_D4_ITEMW; k++) {
		x->fail = "the d4d ring-0 item is outside the G1 block";
		if (swg_hoff(x, x->in.ring[0] + (uint64_t)idx * SWD4B_ITEM_LEN + 4u * k, 4u, &off) || (sh = swg_r0_shadow(x, SWG_SP_HOST, off)) == NULL) {
			return swg_stop(x, SWG_E_ADDR);
		}
		ex = swg_d4d_st(x, SWG_K_ITEM, SWG_SP_HOST, off, *sh, swd4b_get32(it + 4u * k), 0, "d4d item word");
		if (ex != 0) {
			return ex;
		}
	}
	x->fail = "the d4d H2D-WR slot is outside the G1 block";
	if (swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], 4u, &off)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	ex = swg_d4d_st(x, SWG_K_H2DW, SWG_SP_HOST, off, idx, nwr, 1, "d4d h2d_w slot 0");
	if (ex != 0) {
		return ex;
	}
	ex = swg_d4d_st(x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, SWG_D4D_DB_WR | nwr, 0, "d4d db0 (h2d wr)");
	x->d4wrap0 += nwr == 0u ? 1u : 0u;
	x->d4wr = nwr;
	return ex;
}

/* the ring-2 read-index update: the D2H-RD slot rd_old -> rd (ABSOLUTE counts; the slot holds the count mod 64, 22b), doorbell 0 := 0xDD020000 | slot; x->d4rd := rd. rd_old must be the host's own count */
static int
swg_d4d_consume(struct swg_wx *x, uint32_t rd_old, uint32_t rd)
{
	uint32_t off;
	int ex;

	if (rd_old != x->d4rd || rd < rd_old || rd - rd_old >= SWG_R2_DEPTH) {
		x->fail = "the d4d read index update does not start at the host's read count or spans the ring";
		return swg_stop(x, SWG_E_ORDER);
	}

	x->eop = SWG_OP_CONS;
	x->erow = 0u;
	x->fail = "the d4d D2H-RD slot is outside the G1 block";
	if (swg_hoff(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_R)], 4u, &off)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	ex = swg_d4d_st(x, SWG_K_D2HR, SWG_SP_HOST, off, rd_old % SWG_R2_DEPTH, rd % SWG_R2_DEPTH, 1, "d4d d2h_r slot 2");
	if (ex != 0) {
		return ex;
	}
	ex = swg_d4d_st(x, SWG_K_DB, SWG_SP_PCIE, SWG_DB0_OFF, 0u, SWG_D4D_DB_RD | (rd % SWG_R2_DEPTH), 0, "d4d db0 (d2h rd)");
	x->d4rd = rd;
	return ex;
}

#include "sweet_wlan_g2d.h"

/* ---- W-D4e scan24 (sweetwlan 21..23, docs/research/W-D4e-scan24-design.md): L21 = 7 more response buffers + 7 more event buffers, L22 = scan_ver, sup_wpa, event_msgs_ext GET, event_msgs_ext SET (bit 69 only), WLC_UP, L23 = passive escan and its events.
 * Every ring-0 / payload store is a row of an operation (swg_d4d_post, swg_d4d_consume, swg_e_pay) and an entry of the glue's own list. A refusal in this block is a SOFT stop (x->d4dstop, rc 0, the undo runs): "no further store". ---- */
struct swg_eio {                /* the ioctl in flight: what its ack and completion must echo */
	int on, soft;
	uint32_t cmd, trans, slot;     /* slot: the response-buffer slot of the completion's request_id (set when the completion is accepted) */
	unsigned got;                   /* bit 0 ack seen, bit 1 completion seen */
	struct swd4b_ioctl_d2h cm;
	const uint8_t *req;             /* T-bd94: the payload shadow (name + arg) the host posted, for the dump of a failed completion; NULL = no payload */
	uint32_t reqn;
};

struct swg_ew {                 /* L23: what the scan has delivered */
	int done;                       /* 1 = ESCAN_RESULT status SUCCESS, 2 = another status ended it */
	uint32_t nres;                  /* ESCAN_RESULT events with status PARTIAL seen */
};

struct swg_ev {                 /* one parsed event */
	uint32_t type, status, flags, dlen, edlen, sync, bssn, bver, cs, ctl;
	uint32_t reason, auth;          /* T-73fc: wl_event_msg reason / auth_type (BE u32 at frame +36 / +40, bcmevent.h) */
	int rssi, has_res, has_bss;
	uint8_t bssid[6], sl, ssid[32];
};
#include "sweet_wlan_g2r.h"

#define SWG_E_STOP(x, ...) do { char gb_[64]; snprintf(gb_, sizeof gb_, __VA_ARGS__); swl_gap("scan", gb_, "high"); (x)->d4dstop = 1u; printf("SWEET WLAN: L%u STOP, no further store: %s\n", (unsigned)(x)->elv, gb_); } while (0)

static uint32_t
swg_e_be32(const uint8_t *p)
{
	return (uint32_t)p[3] | ((uint32_t)p[2] << 8) | ((uint32_t)p[1] << 16) | ((uint32_t)p[0] << 24);
}

static uint32_t
swg_e_le16(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8);
}

/* the payload of an ioctl: `name` NUL terminated, then argn bytes of arg (zeros when arg is NULL), zero filled to n bytes [src BW bwfm.c:1815-1843: buf = name\0 + data, length strlen(name) + 1 + len] */
static void
swg_e_bytes(uint8_t *b, uint32_t n, const char *name, const uint8_t *arg, uint32_t argn)
{
	uint32_t i, nl = 0;

	for (i = 0; i < 4u * SWG_E_PAYW; i++) {
		b[i] = 0u;
	}
	while (name[nl] != '\0') {
		b[nl] = (uint8_t)name[nl];
		nl++;
	}
	for (i = 0; arg != NULL && i < argn && nl + 1u + i < n; i++) {
		b[nl + 1u + i] = arg[i];
	}
}

/* store the words of b[0..n) that differ from the payload shadow: one SWG_K_PAY row each (old = the shadow word); the glue's list has the same words, built from its own constants */
static int
swg_e_pay(struct swg_wx *x, const uint8_t *b, uint32_t n)
{
	uint32_t i, v, off;
	int ex;

	if (n > 4u * SWG_E_PAYW) {
		x->fail = "a d4e payload is longer than the shadow";
		return swg_stop(x, SWG_E_ORDER);
	}
	for (i = 0; i < (n + 3u) / 4u; i++) {
		v = swd4b_get32(b + 4u * i);
		if (v == x->epay[i]) {
			continue;
		}
		x->fail = "the d4e payload word is outside the G1 block";
		if (swg_hoff(x, x->in.ioctbuf + 4u * i, 4u, &off)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		x->eop = SWG_OP_PAY;
		x->erow = 0u;
		ex = swg_d4d_st(x, SWG_K_PAY, SWG_SP_HOST, off, x->epay[i], v, 0, "d4e payload word");
		if (ex != 0) {
			return ex;
		}
		x->epay[i] = v;
	}
	return 0;
}

/* a response / event buffer post: n-th slot, our own request_id */
static int
swg_e_post_resp(struct swg_wx *x, uint32_t id, uint32_t buf)
{
	uint8_t it[SWD4B_ITEM_LEN];
	int ex;

	swd4b_item_respbuf(it, id, (uint16_t)SWD_RESPBUF_LEN, x->in.respbuf + (uint64_t)buf * SWD_RESPBUF_LEN, swg_ep0(x));
	printf("SWEET WLAN: D4e respbuf post: id %u buffer %u, ring-0 idx %u epoch %u, H2D-WR %u, doorbell %#x\n", id, buf, x->d4wr, swg_ep0(x), swg_r0_next(x), SWG_D4D_DB_WR | swg_r0_next(x));
	ex = swg_d4d_post(x, it);
	if (ex == 0) {
		x->rpid[buf] = id;
	}
	return ex;
}

/* the slot whose posted response buffer has pktid `id`, SWG_E_RID_BUFS when none does (id 0 is DHD_PKTID_INVALID and never matches a slot: an empty slot holds 0). The dongle chooses the buffer, not the host [src R-108 s.2.1 line 55; boot 3 of T-382c: id 11 answered when the oldest posted was 4] */
static uint32_t
swg_e_rslot(const struct swg_wx *x, uint32_t id)
{
	uint32_t j;

	for (j = 0; id != 0u && j < SWG_E_RID_BUFS; j++) {
		if (x->rpid[j] == id) {
			return j;
		}
	}
	return SWG_E_RID_BUFS;
}

static int
swg_e_post_ev(struct swg_wx *x, uint32_t n, uint32_t buf)
{
	uint8_t it[SWD4B_ITEM_LEN];
	const uint32_t id = SWG_E_EVID0 + n;       /* n >= 1: L14 posted event buffer 0 (id SWG_D4_REQID); buf < SWG_E_EV_BUFS: the slot, n % 8 in the first round and the slot of the consumed event afterwards */
	int ex;

	swd4b_item_evbuf(it, id, (uint16_t)SWD_EVBUF_LEN, x->in.evbuf + (uint64_t)buf * SWD_EVBUF_LEN, swg_ep0(x));
	if (!x->kept || x->kev_rp < 8u || (x->kev_rp & 63u) == 0u) {       /* review 0281 F4: in the keep phase the first 8 re-posts, then one line in 64 (kev_rp stays exact; the en0 lock is held here) */
		printf("SWEET WLAN: D4e evbuf post: id %u buffer %u, ring-0 idx %u epoch %u, H2D-WR %u, doorbell %#x\n", id, buf, x->d4wr, swg_ep0(x), swg_r0_next(x), SWG_D4D_DB_WR | swg_r0_next(x));
	}
	ex = swg_d4d_post(x, it);
	if (ex == 0) {
		x->epid[buf] = id;
	}
	return ex;
}

/* re-post, in consumption order, the buffers of the events consumed so far, each in the slot it was consumed from under a new pktid (deferred until the escan START is posted, DS s.1 item 2) */
static int
swg_e_evflush(struct swg_wx *x)
{
	uint32_t k;
	int ex;

	while (x->evq > 0u) {
		ex = swg_e_post_ev(x, x->evp, x->evqb[0]);
		if (ex != 0) {
			return ex;
		}
		x->evp++;
		x->evq--;
		for (k = 0; k < x->evq; k++) {
			x->evqb[k] = x->evqb[k + 1u];
		}
	}
	return 0;
}

/* n bytes at byte offset off of the host buffer at pa, whole words (little endian) */
static int
swg_e_rdb(struct swg_wx *x, uint64_t pa, uint32_t off, uint32_t n, uint8_t *out)
{
	uint32_t i, k, v;

	for (i = off & ~3u; i < off + n; i += 4u) {
		x->fail = "a d4e buffer read failed";
		if (swg_hrd(x, pa + i, &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		for (k = 0; k < 4u; k++) {
			if (i + k >= off && i + k < off + n) {
				out[i + k - off] = (uint8_t)(v >> (8u * k));
			}
		}
	}
	return 0;
}

static void
swg_e_hex(const char *what, const uint8_t *b, uint32_t n)
{
	char line[2u * 96u + 1u];
	uint32_t i;

	for (i = 0; i < n && i < 96u; i++) {
		snprintf(line + 2u * i, 3u, "%02x", b[i]);
	}
	printf("SWEET WLAN: D4e %s hex %s\n", what, line);
}

/* patch 0281: the buffer slot of the posted event buffer with pktid `rid`, SWG_E_EV_BUFS when none has it (pktid 0 never matches: an empty slot holds 0) */
static uint32_t
swg_e_evslot(const struct swg_wx *x, uint32_t rid)
{
	uint32_t sl;

	for (sl = 0; sl < SWG_E_EV_BUFS; sl++) {
		if (rid != 0u && x->epid[sl] == rid) {
			break;
		}
	}
	return sl;
}

/* patch 0281: the pure parse of the 72 B event frame header fr (the layout of the table in docs/research/W-D6-rejoin-dhcp.md s.2: Ethernet header with ethertype at +12, bcm header with OUI at +19..21 and usr_subtype at +22,
 * wl_event_msg flags +26 (u16), event_type +28, status +32, reason +36, auth_type +40, datalen +44, all big endian [src BW bwfmreg.h:750-790, BE:50]); dlen = the item's event_data_len. It touches no engine state (no L23 scan state, no evq / evn / emax).
 * 0 = parsed into *ev (dlen, type, status, flags, reason, auth, edlen), 1 = not a BCM event frame (ethertype / OUI / usr_subtype), 2 = the frame's datalen is above the data the item holds */
static int
swg_e_fparse(const uint8_t fr[SWG_E_FRAME], uint32_t dlen, struct swg_ev *ev)
{
	ev->dlen = dlen;
	if (((uint32_t)fr[12] << 8 | fr[13]) != SWG_E_ETYPE || fr[19] != 0x00u || fr[20] != 0x10u || fr[21] != 0x18u || ((uint32_t)fr[22] << 8 | fr[23]) != 1u) {
		return 1;
	}
	ev->type = swg_e_be32(fr + 28);
	ev->status = swg_e_be32(fr + 32);
	ev->flags = (uint32_t)fr[26] << 8 | fr[27];      /* wl_event_msg flags, big endian u16 at frame +26 [src BW bwfmreg.h:750-790, BE:50] */
	ev->reason = swg_e_be32(fr + 36);
	ev->auth = swg_e_be32(fr + 40);
	ev->edlen = swg_e_be32(fr + 44);
	if (dlen < SWG_E_FRAME || ev->edlen > dlen - SWG_E_FRAME) {
		return 2;
	}
	return 0;
}

/* the event in ring-2 item `in` (type 0xE = wlevent_req_msg_t: request_id +4, status +8, ring_id +10, event_data_len +12, seqnum +14, marker +20 [src MB:796-809]; epoch and XOR checked by the caller; the frame starts rx_dataoffset bytes into the posted buffer, M:8286-8294): pktid = the id of the posted buffer the dongle chose (x->epid), the 72 B frame header at buffer + rx_dataoffset, and for ESCAN_RESULT the wl_escan_result header and the first wl_bss_info record.
 * 0 = parsed, stop = d4dstop set (nothing consumed). x->evn counts consumed events. */
static int
swg_e_event(struct swg_wx *x, const uint8_t in[SWD4B_D2H_LEN], struct swg_ev *ev)
{
	const uint32_t q = x->evn, rid = swd4b_get32(in + 4);
	uint8_t fr[SWG_E_FRAME + 12u + SWG_E_BSS_NEED];
	uint32_t dlen = swg_e_le16(in + 12), rb = 0, sl;
	uint64_t bp;
	int ex, pr;

	memset(ev, 0, sizeof *ev);
	if (q >= x->emax) {
		printf("SWEET WLAN: L%u STOP event cap %u reached, no further store\n", (unsigned)x->elv, x->emax);
		swl_gap("scan", x->emax == SWG_E_MAX ? "event cap 64 reached" : "event cap 48 reached", "high");
		x->d4dstop = 1u;
		return 0;
	}
	sl = swg_e_evslot(x, rid);       /* the dongle chooses the buffer, not the host (id 0 is DHD_PKTID_INVALID and never matches: an empty slot holds 0); a miss gives SWG_E_EV_BUFS */
	if (sl >= SWG_E_EV_BUFS) {
		SWG_E_STOP(x, "event pktid %#x is not a posted event buffer", rid);
		return 0;
	}
	bp = x->in.evbuf + (uint64_t)sl * SWD_EVBUF_LEN;
	if (dlen < SWG_E_FRAME || x->rxoff > SWD_EVBUF_LEN || dlen > SWD_EVBUF_LEN - x->rxoff) {
		SWG_E_STOP(x, "event data length %u, rx offset %u", dlen, x->rxoff);
		return 0;
	}
	ex = swg_e_rdb(x, bp, x->rxoff, SWG_E_FRAME, fr);
	if (ex != 0) {
		return ex;
	}
	if (q == 0u) {
		swg_e_hex("first event frame header", fr, SWG_E_FRAME);
	}
	pr = swg_e_fparse(fr, dlen, ev);       /* patch 0281: the frame parse is shared with the keep phase (swg_ke_event) */
	if (pr == 1) {
		SWG_E_STOP(x, "event frame ethertype %#x oui %02x%02x%02x", (uint32_t)fr[12] << 8 | fr[13], fr[19], fr[20], fr[21]);
		return 0;
	}
	if (pr == 2) {
		SWG_E_STOP(x, "event datalen %u above the frame data %u", ev->edlen, dlen - SWG_E_FRAME);
		return 0;
	}
	if (ev->type == SWG_E_EV_ESCAN && ev->edlen >= 12u) {
		rb = ev->status == SWG_E_ST_PARTIAL && ev->edlen >= 12u + SWG_E_BSS_NEED ? 12u + SWG_E_BSS_NEED : 12u;
		ex = swg_e_rdb(x, bp, x->rxoff + SWG_E_FRAME, rb, fr + SWG_E_FRAME);
		if (ex != 0) {
			return ex;
		}
		if (x->nbss == 0u && ev->status == SWG_E_ST_PARTIAL) {
			swg_e_hex("first escan result data", fr + SWG_E_FRAME, rb);
		}
		ev->has_res = 1;
		ev->sync = swg_e_le16(fr + SWG_E_FRAME + 8);
		if (ev->sync == SWG_E_SYNC) {
			swg_rec_add(ev->status, fr + SWG_E_FRAME, rb);       /* T-08fd: kept for the replay after boot */
		}
		ev->bssn = swg_e_le16(fr + SWG_E_FRAME + 10);
		if (rb > 12u && ev->bssn >= 1u) {
			const uint8_t *r = fr + SWG_E_FRAME + 12;
			ev->has_bss = 1;
			ev->bver = swd4b_get32(r);
			memcpy(ev->bssid, r + 8, 6);
			ev->sl = r[18];
			memcpy(ev->ssid, r + 19, 32);
			ev->cs = swg_e_le16(r + 72);
			ev->rssi = (int)(int16_t)swg_e_le16(r + 78);
			ev->ctl = r[88];
			ex = x->bssn == NULL ? 0 : x->bssn(x, ev, bp, x->rxoff + SWG_E_FRAME + 12u, ev->edlen - 12u);
			if (ex != 0) {
				return ex;
			}
		}
	}
	x->epid[sl] = 0u;       /* consumed: the same pktid is not accepted again until the slot is re-posted under a new id */
	x->evqb[x->evq] = sl;
	x->evn++;
	x->evq++;
	return 0;
}

#define SWG_E_DUMP_MAX  256u    /* T-bd94: bytes of a failed ioctl's request and response that are printed (the escan's 86 B fit) */

/* T-bd94: one hex row of a failed ioctl, 32 bytes per row, from `from` of n bytes */
static void
swg_e_dumprow(const char *what, unsigned cmd, const uint8_t *b, uint32_t n)
{
	uint32_t i, j;

	if (n == 0u) {
		printf("SWEET WLAN: D4e failed ioctl cmd %u %s 0 bytes\n", cmd, what);
	}
	for (i = 0; i < n; i += 32u) {
		char line[80];
		uint32_t p = 0;

		for (j = i; j < n && j < i + 32u; j++) {
			p += (uint32_t)snprintf(line + p, sizeof line - p, "%02x", b[j]);
		}
		printf("SWEET WLAN: D4e failed ioctl cmd %u %s bytes %u..%u of %u hex %s\n", cmd, what, i, j - 1u, n, line);
	}
}

/* T-bd94 (boot 6 of T-a0aa: escan completion status -26, resp_len 86, bytes unseen): before the STOP for a non-zero completion status, print the request bytes the host posted and the response bytes the dongle left in the buffer its request_id names (read-only host reads of the posted buffer, no store of any kind; a read that does not work is a WIFI GAP, never a hard stop).
 * `f` is the completion, `io` the ioctl in flight (io->req NULL = it had no payload) */
static void
swg_e_dumpfail(struct swg_wx *x, const struct swg_eio *io, const struct swd4b_ioctl_d2h *f)
{
	uint8_t rb[SWG_E_DUMP_MAX];
	uint32_t n = f->resp_len < SWG_E_DUMP_MAX ? f->resp_len : SWG_E_DUMP_MAX, sl, i, v;
	char gb[64];

	snprintf(gb, sizeof gb, "ioctl cmd %u status %d: bytes dumped", f->cmd, f->status);
	swl_gap("scan", gb, "high");
	if (io->req != NULL) {
		swg_e_dumprow("request", f->cmd, io->req, io->reqn < SWG_E_DUMP_MAX ? io->reqn : SWG_E_DUMP_MAX);
	} else {
		printf("SWEET WLAN: D4e failed ioctl cmd %u request had no payload\n", f->cmd);
	}
	sl = swg_e_rslot(x, f->request_id);
	if (sl >= SWG_E_RID_BUFS) {
		swl_gap("scan", "failed ioctl: request_id not posted, no response dump", "high");
		return;
	}
	for (i = 0; i < n; i += 4u) {
		if (swg_hrd(x, x->in.respbuf + (uint64_t)sl * SWD_RESPBUF_LEN + i, &v)) {
			swl_gap("scan", "failed ioctl: response buffer read failed, dump cut", "high");
			n = i;
			break;
		}
		rb[i] = (uint8_t)v;
		rb[i + 1u] = (uint8_t)(v >> 8);
		rb[i + 2u] = (uint8_t)(v >> 16);
		rb[i + 3u] = (uint8_t)(v >> 24);
	}
	printf("SWEET WLAN: D4e failed ioctl cmd %u status %d resp_len %u buffer slot %u\n", f->cmd, f->status, f->resp_len, sl);
	swg_e_dumprow("response", f->cmd, rb, n < f->resp_len ? n : f->resp_len);
}

/* L24: judge one event for the join wait (x->ej != NULL): SET_SSID keeps its status, LINK its flags; the texts are TRANSPORT block s.4's. Other events are only counted by the caller */
static void
swg_j_ev(struct swg_wx *x, const struct swg_ev *ev)
{
	struct swg_ej *j = x->ej;

	if (ev->type == SWG_E_EV_SET_SSID) {
		j->ssid_seen = 1;
		j->ssid_status = ev->status;
		printf("SWEET WLAN: join set_ssid status=%u\n", ev->status);
	} else if (ev->type == SWG_E_EV_LINK) {
		j->link_seen = 1;
		j->link_flags = ev->flags;
		j->link_up = (ev->flags & SWG_E_LINK_UP) != 0u;
		if (j->link_up) {
			printf("SWEET WLAN: join link up\n");
		} else {
			printf("SWEET WLAN: join link event, link not up (flags %#x)\n", ev->flags);
		}
	}
}

/* ---- patch 0281 (W-D6 boot A): the keep phase decodes every WL_EVENT, tells the link-loss events from the rest, and re-posts the event buffer it consumed.
 * Codes [src OBSD bwfmreg.h:660-676 (SET_SSID 0, DEAUTH 5, DEAUTH_IND 6, DISASSOC 11, DISASSOC_IND 12, LINK 16); LNX fweh.h / cfg80211.c:6274-6287 brcmf_is_linkdown as quoted in docs/research/W-D6-rejoin-dhcp.md s.2 (not re-read here)]. ---- */
#define SWG_E_EV_DEAUTH       5u
#define SWG_E_EV_DEAUTH_IND   6u
#define SWG_E_EV_DISASSOC     11u
#define SWG_E_EV_DISASSOC_IND 12u
enum { SWG_KL_NONE = 0, SWG_KL_DOWN = 1, SWG_KL_UP = 2 };
/* patch 0282: the re-join states. UP -> DOWN (the 0281 detector) -> JOINING (join sent) -> KEYING (SET_SSID 0 + LINK up seen, the host 4-way runs) -> UP. */
enum { SWG_RJ_UP = 0, SWG_RJ_DOWN = 1, SWG_RJ_JOINING = 2, SWG_RJ_KEYING = 3 };
#define SWG_RJ_BACKOFF_MAX_MS 30000u

/* patch 0282: the wait after attempt n (1-based) failed, ms: 2 s, 4 s, 8 s, 16 s, then 30 s for ever [decision: the user's 2026-11-10 brief; no vendor value exists, OpenBSD has none (docs/research/W-D6-rejoin-dhcp.md s.6 step 5)]. Pure. */
static inline uint32_t
swg_rj_backoff_ms(uint32_t n)
{
	uint32_t ms = 2000u;

	while (n > 1u && ms < SWG_RJ_BACKOFF_MAX_MS) {
		ms *= 2u;
		n--;
	}
	return ms < SWG_RJ_BACKOFF_MAX_MS ? ms : SWG_RJ_BACKOFF_MAX_MS;
}

/* what one decoded event says about the link. DOWN: LINK with the up flag (bit 0x01) clear, DEAUTH, DEAUTH_IND, DISASSOC, DISASSOC_IND, SET_SSID with a non-zero status (LNX brcmf_is_linkdown minus DISASSOC, which the research adds, logged).
 * UP: LINK with the up flag set, SET_SSID status 0. Everything else says nothing (SWG_KL_NONE). Pure. */
static int
swg_ke_class(uint32_t type, uint32_t flags, uint32_t status)
{
	switch (type) {
	case SWG_E_EV_LINK:
		return (flags & SWG_E_LINK_UP) != 0u ? SWG_KL_UP : SWG_KL_DOWN;
	case SWG_E_EV_SET_SSID:
		return status != 0u ? SWG_KL_DOWN : SWG_KL_UP;
	case SWG_E_EV_DEAUTH:
	case SWG_E_EV_DEAUTH_IND:
	case SWG_E_EV_DISASSOC:
	case SWG_E_EV_DISASSOC_IND:
		return SWG_KL_DOWN;
	default:
		return SWG_KL_NONE;
	}
}

/* review 0282 F3: the events that say the AP dropped the station (not a firmware retry's SET_SSID status): DEAUTH, DEAUTH_IND, DISASSOC, DISASSOC_IND, LINK with the up flag clear. Pure. */
static inline int
swg_ke_lost(const struct swg_ev *ev)
{
	switch (ev->type) {
	case SWG_E_EV_DEAUTH:
	case SWG_E_EV_DEAUTH_IND:
	case SWG_E_EV_DISASSOC:
	case SWG_E_EV_DISASSOC_IND:
		return 1;
	case SWG_E_EV_LINK:
		return (ev->flags & SWG_E_LINK_UP) == 0u;
	default:
		return 0;
	}
}

/* T-32 [obs allon-stress3 +2632.766..775]: a DEAUTH_IND (6) makes the link DOWN and the first re-join attempt is sent at once; the firmware's own LINK down for the SAME lost association
 * (type 16, up flag clear, reason 2 = WLC_E_LINK_DISASSOC [src bcmevent.h.new:756]) arrived 3 ms after the join and failed attempt 1 as "link lost while joining"; attempt 2's join then
 * aborted attempt 1's still running join (SET_SSID status 4 = WLC_E_STATUS_ABORT [src bcmevent.h.new:416]) and came up 2.4 s later. OpenBSD bwfm acts on LINK only past SCAN, i.e. a
 * link-down while not linked says nothing [src bwfm.c:2738-2742]. So: once per DOWN episode whose cause was DEAUTH/DEAUTH_IND/DISASSOC/DISASSOC_IND, the first LINK down with reason 2
 * before any link-up is that tail: noted, never a verdict. Any other loss (a second LINK down, another reason, a new DEAUTH) still fails the attempt. Pure. */
static inline int
swg_ke_tail(const struct swg_wx *x, const struct swg_ev *ev)
{
	if (x->kl_tail != 0u || ev->type != SWG_E_EV_LINK || (ev->flags & SWG_E_LINK_UP) != 0u || ev->reason != SWG_E_LINK_R_DISASSOC) {
		return 0;
	}
	return x->kl_cause == SWG_E_EV_DEAUTH || x->kl_cause == SWG_E_EV_DEAUTH_IND || x->kl_cause == SWG_E_EV_DISASSOC || x->kl_cause == SWG_E_EV_DISASSOC_IND;
}

/* the detector: UP -> DOWN exactly once. Patch 0282: the DOWN edge also starts the re-join machine (rj_state DOWN: swl25_rj_step makes the first attempt at once). A link-up event (SET_SSID status 0, LINK up) while an attempt is JOINING is the expected answer to
 * the join: it is recorded for swl25_rj_step. Any other link-up event while DOWN (no attempt in progress, or already KEYING) is a counted GAP and changes nothing. No firmware command, no store. */
static void
swg_ke_note(struct swg_wx *x, const struct swg_ev *ev)
{
	const int c = swg_ke_class(ev->type, ev->flags, ev->status);

	if (c == SWG_KL_DOWN && x->klink == 0u) {
		x->klink = 1u;
		x->kl_new = 1u;
		x->rj_state = SWG_RJ_DOWN;
		x->rj_ssid = x->rj_link = 0u;
		x->rj_lost = 0u;
		x->kl_cause = ev->type;
		x->kl_status = ev->status;
		x->kl_reason = ev->reason;
		x->kl_tail = ev->type == SWG_E_EV_LINK ? 1u : 0u;      /* T-32: a LINK-down cause has no trailing LINK down */
		printf("SWEET WLAN: keep link DOWN cause %u status %u reason %u\n", ev->type, ev->status, ev->reason);
	} else if (x->klink != 0u && (x->rj_state == SWG_RJ_DOWN || x->rj_state == SWG_RJ_JOINING) && swg_ke_tail(x, ev)) {
		x->kl_tail = 1u;
		if (x->rj_state == SWG_RJ_JOINING) {
			x->rj_ev_n++;
		}
		printf("SWEET WLAN: keep rejoin: trailing LINK down (reason 2 = disassoc) of the lost association (cause %u) noted %s, not a verdict\n", x->kl_cause,
		    x->rj_state == SWG_RJ_JOINING ? "while joining, the attempt goes on" : "before the attempt");
	} else if (x->klink != 0u && x->rj_state == SWG_RJ_JOINING) {
		x->rj_ev_n++;
		if (swg_ke_lost(ev)) {
			x->rj_lost = 1u;       /* review 0282 F3: the AP dropped us again: the attempt fails (swl25_rj_step), it does not wait for its budget */
		}
		if (ev->type == SWG_E_EV_SET_SSID && ev->status == 0u) {
			x->rj_ssid = 1u;
		} else if (ev->type == SWG_E_EV_LINK && (ev->flags & SWG_E_LINK_UP) != 0u) {
			x->rj_link = 1u;
		} else {
			/* a stale or unrelated event while waiting (e.g. the firmware's own retries of the lost link): logged, not a verdict; the attempt ends by its budget */
			printf("SWEET WLAN: keep rejoin: event type %u status %u noted while joining, not a link-up answer\n", ev->type, ev->status);
		}
	} else if (x->klink != 0u && x->rj_state == SWG_RJ_KEYING) {
		x->rj_ev_n++;
		if (c == SWG_KL_DOWN) {
			x->rj_lost = 1u;       /* review 0282 F3: never post UP on a dead link */
			printf("SWEET WLAN: keep rejoin: link-loss event type %u status %u reason %u noted during the 4-way, the attempt fails\n", ev->type, ev->status, ev->reason);
		}
	} else if (c == SWG_KL_UP && x->klink != 0u) {
		x->kl_up_n++;
		printf("WIFI GAP: keep link UP event outside a re-join attempt ignored (type %u)\n", ev->type);
		swl_gap("keep", "link-up event while DOWN outside a re-join attempt ignored", "medium");
	}
}

/* patch 0292: a keep-phase ESCAN_RESULT (WLC_E_ESCAN_RESULT 69, status 8 = PARTIAL carries one bss record, 0 = SUCCESS ends the scan [src SWG_E_ST_PARTIAL / SWG_E_ST_OK, brcmfmac fweh.h, OpenBSD bwfmreg.h]). ev = the parsed frame header, sl = the event buffer slot (its pktid
 * is checked by the caller). While sc_on and a sink is set: read the 12 B result header (+ the first SWG_E_BSS_NEED record bytes of a PARTIAL one, the same reads as swg_e_event at L23) and hand them to scansink; an event whose sync_id is not ours is counted (sc_other) and NOT handed on;
 * SUCCESS sets sc_complete. With no scan collecting, the event is a late one of a scan that timed out: counted (sc_stray), a GAP line the first time and every 64th. The sink gets (status, bytes, length) and MUST NOT print an SSID or BSSID. 0 = done; negative = a failed host read. */
static int
swg_ke_escan(struct swg_wx *x, const struct swg_ev *ev, uint32_t sl)
{
	uint8_t *const fr = x->scfr;
	uint32_t rb, rb2, bl, room, sync;
	int ex;

	if (!x->sc_on || x->scansink == NULL) {
		x->sc_stray++;
		if (x->sc_await && !x->sc_await_done && ev->status != SWG_E_ST_PARTIAL && ev->edlen >= 12u) {       /* review 0292 F4: the end status of the scan a link loss cut short (read the 12 B header for its sync id) */
			ex = swg_e_rdb(x, x->in.evbuf + (uint64_t)sl * SWD_EVBUF_LEN, x->rxoff + SWG_E_FRAME, 12u, fr);
			if (ex != 0) {
				return ex;
			}
			if (swg_e_le16(fr + 8) == x->escan_sync) {
				x->sc_await_done = 1u;
				x->sc_term = ev->status;
			}
		}
		if (x->sc_stray == 1u || (x->sc_stray & 63u) == 0u) {
			printf("WIFI GAP: keep: ESCAN_RESULT event outside a scan (late event of an earlier scan, or none was requested), ignored (%u so far)\n", x->sc_stray);
			swl_gap("scan", "ESCAN_RESULT outside a keep-phase scan ignored", "low");
		}
		return 0;
	}
	if (ev->edlen < 12u) {
		x->sc_other++;
		printf("WIFI GAP: keep scan: ESCAN_RESULT event with %u data bytes (< the 12 B header), ignored\n", ev->edlen);
		swl_gap("scan", "ESCAN_RESULT event shorter than its header ignored", "medium");
		return 0;
	}
	rb = ev->status == SWG_E_ST_PARTIAL && ev->edlen >= 12u + SWG_E_BSS_NEED ? 12u + SWG_E_BSS_NEED : 12u;
	memset(fr, 0, sizeof x->scfr);
	ex = swg_e_rdb(x, x->in.evbuf + (uint64_t)sl * SWD_EVBUF_LEN, x->rxoff + SWG_E_FRAME, rb, fr);
	if (ex != 0) {
		return ex;
	}
	if (rb > 12u && swg_e_le16(fr + 8) == x->escan_sync) {
		/* 2026-11-11 scan records: read the rest of the bss record (its own length field, the event's data length and SWG_E_BSS_RMAX bound it; the event buffer slot bound is SWD_EVBUF_LEN) so the radio gets the rates tail, the SNR and the IEs. A read of the host event buffer only. */
		bl = swd4b_get32(fr + 12u + 4u);
		room = ev->edlen - 12u;       /* the record bytes the event holds */
		rb2 = 12u + (bl < room ? bl : room);
		if (rb2 > 12u + SWG_E_BSS_RMAX) {
			rb2 = 12u + SWG_E_BSS_RMAX;
		}
		if (rb2 > rb && x->rxoff <= SWD_EVBUF_LEN && SWG_E_FRAME + rb2 <= SWD_EVBUF_LEN - x->rxoff) {
			ex = swg_e_rdb(x, x->in.evbuf + (uint64_t)sl * SWD_EVBUF_LEN, x->rxoff + SWG_E_FRAME + rb, rb2 - rb, fr + rb);
			if (ex != 0) {
				return ex;
			}
			rb = rb2;
		}
	}
	sync = swg_e_le16(fr + 8);
	if (sync != x->escan_sync) {
		x->sc_foreign++;
		if (x->sc_foreign == 1u || (x->sc_foreign & 63u) == 0u) {       /* review 0292 F1 (rule 7): the first and every 64th, the total goes into the scan-end line */
			printf("WIFI GAP: keep scan: ESCAN_RESULT of another sync id %#x ignored (%u so far; ours is %#x)\n", sync, x->sc_foreign, (uint32_t)x->escan_sync);
			swl_gap("scan", "ESCAN_RESULT of another sync id ignored", "low");
		}
		return 0;       /* another scan's event (the firmware's own, or a stale sync id): not ours */
	}
	x->sc_ev++;
	if (ev->status == SWG_E_ST_OK) {
		x->sc_complete = 1u;
	} else if (ev->status == SWG_E_ST_PARTIAL) {
		x->sc_part++;
	} else {
		x->sc_other++;       /* review 0292 F5: any other status of our scan is an END (FAIL 1, TIMEOUT 2, ABORT 4, NEWSCAN 9, NEWASSOC 10, SUPPRESS 12, NOCHANS 13 [src BE:412-424]): swl25_sc_step ends the scan with a GAP naming it */
		x->sc_termd = 1u;
		x->sc_term = ev->status;
	}
	x->scansink(x->scancx, ev->status, fr, rb);
	return 0;
}

/* one keep-phase WL_EVENT item `in` (ring-2 item k, epoch and XOR already checked): find the posted buffer by its pktid, read the 72 B frame header, decode, print ONE line (no addresses, no ifname), run the detector, and queue the slot for the re-post
 * (swg_ke_flush, called when no ioctl is in flight). It never touches the L23 scan state, evq, evn or emax and never stops the engine for a malformed event: an unknown or short frame is a `WIFI GAP: keep event` line (counted kev_gap) and, when the buffer is known, still re-posted.
 * 0 = done; negative = stopped (a failed host read). */
static int
swg_ke_event(struct swg_wx *x, const uint8_t in[SWD4B_D2H_LEN], uint32_t k)
{
	uint8_t fr[SWG_E_FRAME];
	struct swg_ev ev;
	const uint32_t rid = swd4b_get32(in + 4), dlen = swg_e_le16(in + 12);
	uint32_t sl;
	int ex, pr;

	memset(&ev, 0, sizeof ev);
	sl = swg_e_evslot(x, rid);
	if (sl >= SWG_E_EV_BUFS) {
		x->kev_gap++;
		printf("WIFI GAP: keep event item %u: pktid %#x is not a posted event buffer, not decoded, no buffer to re-post, item_len %u\n", k, rid, dlen);
		swl_gap("keep", "event pktid is not a posted event buffer, not decoded", "medium");
		return 0;
	}
	x->epid[sl] = 0u;       /* consumed: the pktid is not accepted again until the slot is re-posted under a new id */
	if (x->kevq < SWG_E_EV_BUFS) {
		x->kevb[x->kevq++] = sl;
	} else {
		swl_gap("keep", "event re-post queue full, a buffer is not re-posted", "high");       /* cannot happen (at most 8 buffers are posted); never silent */
		printf("WIFI GAP: keep event: the re-post queue is full, buffer %u is not re-posted\n", sl);
	}
	if (dlen < SWG_E_FRAME || x->rxoff > SWD_EVBUF_LEN || dlen > SWD_EVBUF_LEN - x->rxoff) {
		x->kev_gap++;
		printf("WIFI GAP: keep event item %u: item_len %u is shorter than the %u B frame header or outside the buffer (rx offset %u), not decoded\n", k, dlen, SWG_E_FRAME, x->rxoff);
		swl_gap("keep", "event item shorter than the frame header, not decoded", "medium");
		return 0;
	}
	ex = swg_e_rdb(x, x->in.evbuf + (uint64_t)sl * SWD_EVBUF_LEN, x->rxoff, SWG_E_FRAME, fr);
	if (ex != 0) {
		return ex;
	}
	pr = swg_e_fparse(fr, dlen, &ev);
	if (pr != 0) {
		x->kev_gap++;
		printf("WIFI GAP: keep event item %u: %s, item_len %u, not decoded\n", k, pr == 1 ? "not a BCM event frame (ethertype / OUI / usr_subtype)" : "event datalen above the frame data", dlen);
		swl_gap("keep", "event frame not decoded", "medium");
		return 0;
	}
	x->kev_n++;
	if (ev.type == SWG_E_EV_ESCAN && x->sc_on && x->scansink != NULL) {
		return swg_ke_escan(x, &ev, sl);       /* patch 0292: a result of the keep-phase scan is collected, not printed one line each (the scan prints its own start and done lines) */
	}
	if (x->kev_n <= 256u || (x->kev_n & 63u) == 0u) {       /* a roaming storm prints the first 256, then every 64th (all are counted) */
		printf("SWEET WLAN: keep event type %u flags 0x%x status %u reason %u auth %u datalen %u item_len %u\n", ev.type, ev.flags, ev.status, ev.reason, ev.auth, ev.edlen, dlen);
	}
	if (ev.type == SWG_E_EV_ESCAN) {
		return swg_ke_escan(x, &ev, sl);       /* patch 0292: no scan is collecting: a late event of a scan that timed out, counted (sc_stray) and told once by a GAP */
	}
	swg_ke_note(x, &ev);
	return 0;
}

/* re-post the event buffers the keep phase consumed (as Linux msgbuf.c brcmf_msgbuf_process_event -> brcmf_msgbuf_rxbuf_event_post, msgbuf.c:1132-1148 and OpenBSD if_bwfm_pci.c:1747-1760 do for every event, bwfm_pci_fill_rx_rings), each under a fresh pktid, in
 * consumption order. First the buffers of boot events whose re-post was still deferred (evq, swg_e_evflush: the same store kind). Never inside an ioctl: the caller is swg_keep_r2 (the keep thread, no call in flight). The stores are the existing swg_d4d_post kind (ten
 * item words, H2D-WR slot, doorbell 0), judged by the L25 whitelist (class L2 buffer post, L7 index, L9 doorbell). 0 = done; negative = stopped */
static int
swg_ke_flush(struct swg_wx *x)
{
	uint32_t k;
	int ex;

	if (x->evq > 0u) {
		printf("SWEET WLAN: keep: %u boot event buffer(s) still waiting for their re-post, posting them now\n", x->evq);
		swl_gap("keep", "boot event buffers re-posted late", "low");
		ex = swg_e_evflush(x);
		if (ex != 0) {
			return ex;
		}
	}
	while (x->kevq > 0u) {
		ex = swg_e_post_ev(x, x->evp, x->kevb[0]);
		if (ex != 0) {
			return ex;
		}
		x->evp++;
		x->kev_rp++;
		x->kevq--;
		for (k = 0; k < x->kevq; k++) {
			x->kevb[k] = x->kevb[k + 1u];
		}
	}
	return 0;
}

/* the ring-2 items [d4rd, w) (at most SWG_E_BATCH), in order: the acks / completions of `io`, events. A type-0x24 item behind the first is left for the next round (swg_ds_front looks at the front item only). *e = the index after the last item judged.
 * L23 (sc != NULL) judges the ESCAN_RESULT events. 0 = done (d4dstop set = a soft stop, nothing of the offending item consumed); negative = stopped */
static int
swg_e_items(struct swg_wx *x, uint32_t w, struct swg_eio *io, struct swg_ew *sc, uint32_t *e)
{
	uint8_t in[SWD4B_D2H_LEN], type = 0;
	struct swd4b_ioctl_d2h f;
	struct swg_ev ev;
	char sb[80], gs[64];
	uint32_t k, sl = SWG_E_RID_BUFS;
	int chk, ex;

	swg_hr(x, SWG_HR_EITEMS, "e_items");
	*e = x->d4rd;
	for (k = x->d4rd; k < w && k < x->d4rd + SWG_E_BATCH && !x->d4dstop; k++) {
		ex = swg_d4d_r2(x, k, in);
		if (ex != 0) {
			return ex;
		}
		chk = swd4b_d2h_check(in, swg_ep(k), &type);
		if (type == SWD4B_MSG_D2H_MBDATA && k != x->d4rd) {
			break;
		}
		if (chk != 0) {
			printf("SWEET WLAN: D4e ring2 item %u raw 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x epoch/xor check %d\n", k, swd4b_get32(in), swd4b_get32(in + 4), swd4b_get32(in + 8), swd4b_get32(in + 12), swd4b_get32(in + 16), swd4b_get32(in + 20), chk);
			SWG_E_STOP(x, "ring-2 item %u epoch/xor check failed (%d)", k, chk);
			break;
		}
		if (type == SWD4B_MSG_IOCTL_ACK || type == SWD4B_MSG_IOCTL_CMPLT) {
			(void)swd4b_ioctl_d2h(in, &f);
			swg_hr(x, SWG_HR_ITEM, "d4d_item");
			printf("SWEET WLAN: D4d ring2 item %u raw 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x epoch/xor check %d\n", k, swd4b_get32(in), swd4b_get32(in + 4), swd4b_get32(in + 8), swd4b_get32(in + 12), swd4b_get32(in + 16), swd4b_get32(in + 20), chk);
			printf("SWEET WLAN: D4d ring2 item %u type 0x%x epoch 0x%x request_id 0x%x status %d ring_id %u cmd %u trans_id 0x%x resp_len %u\n", k, f.type, f.epoch, f.request_id, f.status, f.ring_id, f.cmd, f.trans_id, f.resp_len);
			if (io == NULL || !io->on) {
				SWG_E_STOP(x, "ring-2 item %u (type 0x%x) but no request was sent", k, type);
			} else if (f.status != 0 && !(type == SWD4B_MSG_IOCTL_CMPLT && io->soft)) {
				if (type == SWD4B_MSG_IOCTL_CMPLT) {
					swg_e_dumpfail(x, io, &f);
				}
				SWG_E_STOP(x, "ring-2 item %u type 0x%x status %d != 0", k, type, f.status);
			} else if (f.cmd != io->cmd) {
				SWG_E_STOP(x, "ring-2 item %u cmd %u, expected %u", k, f.cmd, io->cmd);
			} else if (type == SWD4B_MSG_IOCTL_ACK) {
				if (f.request_id != SWD4B_IOCTL_REQID) {
					SWG_E_STOP(x, "ack request_id 0x%x, expected 0xfffe", f.request_id);
				} else if ((io->got & 1u) != 0u) {
					SWG_E_STOP(x, "second ioctl ack at ring-2 item %u", k);
				}
				io->got |= 1u;
			} else {
				if (f.trans_id != io->trans) {
					SWG_E_STOP(x, "completion trans_id 0x%x, expected 0x%x", f.trans_id, io->trans);
				} else if ((sl = swg_e_rslot(x, f.request_id)) >= SWG_E_RID_BUFS) {
					SWG_E_STOP(x, "completion request_id %u is not a posted response buffer", f.request_id);
				} else if (f.resp_len > SWD_RESPBUF_LEN) {
					SWG_E_STOP(x, "completion resp_len %u above the %u B buffer", f.resp_len, SWD_RESPBUF_LEN);
				} else if ((io->got & 2u) != 0u) {
					SWG_E_STOP(x, "second ioctl completion at ring-2 item %u", k);
				}
				io->got |= 2u;
				io->slot = sl;
				io->cm = f;
			}
		} else if (type == SWD4B_MSG_WL_EVENT && x->kept) {
			/* patch 0281: an event that arrives inside a keep-phase ioctl (the RSSI refresh) is decoded by the keep path, not by the L23 scan path (evq / evn / emax, scan state); its buffer is re-posted after the call, in swg_keep_r2 */
			ex = swg_ke_event(x, in, k);
			if (ex != 0) {
				return ex;
			}
			x->kr2ev++;
			x->kr2n++;
		} else if (type == SWD4B_MSG_WL_EVENT) {
			ex = swg_e_event(x, in, &ev);
			if (ex != 0) {
				return ex;
			}
			if (x->d4dstop) {
				break;
			}
			printf("SWEET WLAN: L%u event item %u: type %u status %u datalen %u pktid %#x rx offset %u\n", (unsigned)x->elv, k, ev.type, ev.status, ev.dlen, swd4b_get32(in + 4), x->rxoff);
			if (x->elv >= 24u && ev.type != SWG_E_EV_ESCAN) {        /* T-73fc: why the AP/firmware ended or changed the link (LINK 16 flags bit0 = up; DEAUTH 5/6, DISASSOC 11/12 reason = 802.11 reason code) */
				printf("SWEET WLAN: L%u event detail (item %u): type %u flags %#x status %u reason %u auth_type %u\n", (unsigned)x->elv, k, ev.type, ev.flags, ev.status, ev.reason, ev.auth);
			}
			if (x->ej != NULL) {
				swg_j_ev(x, &ev);
			}
			if (sc == NULL) {
				*e = k + 1u;
				continue;
			}
			if (!ev.has_res) {       /* has_res: an ESCAN_RESULT with its 12 B header */
				swl_gap("scan", "event other than a full ESCAN_RESULT seen", "medium");
			} else if (ev.sync != SWG_E_SYNC) {
				swl_gap("scan", "ESCAN_RESULT sync_id differs from ours", "medium");
				printf("SWEET WLAN: L23 escan event 69 status %u sync %#x (ours %#x), ignored\n", ev.status, ev.sync, SWG_E_SYNC);
			} else {
				printf("SWEET WLAN: L23 escan event 69 status %u sync %#x bss_count %u\n", ev.status, ev.sync, ev.bssn);
				if (ev.status == SWG_E_ST_PARTIAL) {
					sc->nres++;
					if (ev.bssn > 1u) {
						swl_gap("scan", "bss_count above 1, only the first record parsed", "medium");
					}
					if (!ev.has_bss) {
						swl_gap("scan", "PARTIAL result shorter than one bss record", "medium");
					} else if (ev.bver != SWG_E_BSS_VER) {
						swl_gap("scan", "bss_info version is not 109, not parsed", "high");
						printf("SWEET WLAN: L23 bss_info record version %u, expected %u: not parsed\n", ev.bver, SWG_E_BSS_VER);
					} else {
						const uint32_t ch = ev.ctl != 0u ? ev.ctl : (ev.cs & 0xFFu);

						swg_e_ssid(&ev, sb, sizeof sb);
						printf("SWEET WLAN: L23 bss %u chan %u rssi %d chanspec %#x bssid %02x:%02x:%02x:%02x:%02x:%02x ssid %s\n", x->nbss, ch, ev.rssi, ev.cs, ev.bssid[0], ev.bssid[1], ev.bssid[2], ev.bssid[3], ev.bssid[4], ev.bssid[5], sb);
						x->nbss++;
						x->nchan[(ch >> 5) & 7u] |= 1u << (ch & 31u);
					}
				} else if (ev.status == SWG_E_ST_OK) {
					sc->done = 1;
				} else {
					snprintf(gs, sizeof gs, "status %u in an ESCAN_RESULT, scan ended", ev.status);
					swl_gap("scan", gs, "high");
					sc->done = 2;
				}
			}
		} else {
			SWG_E_STOP(x, "unexpected ring-2 type 0x%x at item %u", type, k);
			break;
		}
		*e = k + 1u;
	}
	return 0;
}

/* ring 2 looked at ONCE (no delay) between two operations: a pending DS request is answered first (swg_ds_front, inside swg_d4d_waitn), events that came by themselves are consumed (their re-post waits for the escan START), anything else is a soft stop. 0 = done or soft stop; negative = stopped */
static int
swg_e_idle(struct swg_wx *x)
{
	struct swg_eio io;
	uint32_t w = 0, waited = 0, e = 0;
	int ex;

	memset(&io, 0, sizeof io);
	ex = swg_d4d_waitn(x, 0u, 1u, &w, &waited);
	if (ex != 0) {
		return ex;
	}
	if (w < x->d4rd) {
		SWG_E_STOP(x, "d2h write index %u went below the read count %u", w, x->d4rd);
		return 0;
	}
	if (w == x->d4rd) {
		return 0;
	}
	ex = swg_e_items(x, w, &io, NULL, &e);
	if (ex != 0 || x->d4dstop || e == x->d4rd) {
		return ex;
	}
	return swg_d4d_consume(x, x->d4rd, e);
}

/* T-c3ac H1 (W4): the endpoint's error and link registers around the FIRST L24 doorbell, READS only (EP config space through the CFG window, the same bus the Command reads use): cfg 0x04 Command/Status (Status = high half: master/target abort, system error),
 * 0xBC LNKCTL/LNKSTA (link status = high half; research 36 s.2, 38 item 10, wlanlink S29 logs it), 0x100 AER capability header (observed 0x13C10001, research 36 HO:218) and 0x104 AER Uncorrectable Status (pci_regs.h PCI_ERR_UNCOR_STATUS; CTO = bit 14, UR = bit 20, research 36).
 * The "post" probe follows the posted doorbell store: its FIRST read is a non-posted config TLP, a fence (the posted write has reached the endpoint or its fault surfaces here, inside a logged window), and it is announced by a line BEFORE the read, so a hang in it is told by the log ending there.
 * A failed read, an all-ones word or a set AER bit is a loud gap (rule 8); nothing is stored and nothing stops. No RC register is read: no RC AER offset is cited in the sources (rule 7). */
static const struct { uint32_t off; const char *nm; } swg_l24_probe_regs[] = { { 0x04u, "Command/Status" }, { 0xBCu, "LNKCTL/LNKSTA" }, { 0x100u, "AER header" }, { 0x104u, "AER UESta" } };

static void
swg_l24_probe(struct swg_wx *x, const char *tag)
{
	unsigned i;
	uint32_t v;

	printf("SWEET WLAN: L24 probe %s: begin (ep cfg 0x04, 0xBC, 0x100, 0x104; reads only)\n", tag);
	for (i = 0u; i < sizeof swg_l24_probe_regs / sizeof swg_l24_probe_regs[0]; i++) {
		v = 0u;
		if (x->b->rd(x->b->ctx, SWG_SP_EP, swg_l24_probe_regs[i].off, &v)) {
			printf("SWEET WLAN: L24 probe %s: ep cfg %#x (%s) READ FAILED\n", tag, swg_l24_probe_regs[i].off, swg_l24_probe_regs[i].nm);
			swl_gap("l24pr", "L24 probe: an endpoint config read failed", "high");
			continue;
		}
		printf("SWEET WLAN: L24 probe %s: ep cfg %#x (%s) = %#x\n", tag, swg_l24_probe_regs[i].off, swg_l24_probe_regs[i].nm, v);
		if (v == 0xFFFFFFFFu) {
			printf("SWEET WLAN: L24 probe %s: ep cfg %#x reads all ones: the endpoint does not answer (dead link or completion timeout)\n", tag, swg_l24_probe_regs[i].off);
			swl_gap("l24pr", "L24 probe: endpoint config reads all ones", "high");
		} else if (swg_l24_probe_regs[i].off == 0x104u && v != 0u) {
			printf("SWEET WLAN: L24 probe %s: AER UESta %#x: completion timeout (bit 14) %u, unsupported request (bit 20) %u\n", tag, v, (v >> 14) & 1u, (v >> 20) & 1u);
			swl_gap("l24pr", "L24 probe: AER uncorrectable status is set", "high");
		}
	}
}

/* the ioctl payload shadow: file scope, not on the kernel stack (review T-4adb-hwwrite-r1 C2; G2 is single-threaded and runs once per boot, sw_hw is static for the same reason). Review 0282 F5: file scope so a secret-path call can wipe it on EVERY way out */
static uint8_t swg_e_b[4u * SWG_E_PAYW];

/* One ioctl: the payload (name + arg, wlen bytes; name NULL = no payload, WLC_UP), the request, then ring-2 items until its ack and completion are in (events in between are consumed, their buffers re-posted only in L23 = sc != NULL, after the START), each batch consumed. The completion's request_id is the pktid of the response buffer the dongle used: any pktid still posted (x->rpid) is accepted (T-af77; boot 3 of T-382c got id 11 with ids 4..11 posted), consumed once; nrsp response bytes are read from that buffer's slot and the buffer re-posted into the same slot (repost != 0, id SWG_E_RID_RE0 + c - 2; with repost 0 the slot stays empty). soft: a non-zero completion status is recorded, not a stop. 0 = done or soft stop (d4dstop); negative = stopped */
static int
swg_e_call(struct swg_wx *x, uint32_t cmd, const char *what, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, int soft, uint32_t nrsp, uint8_t *rsp, int repost, struct swd4b_ioctl_d2h *cmo, struct swg_ew *sc)
{
	uint8_t *const b = swg_e_b;
	struct swg_eio io;
	const uint32_t c = x->ncpl, tr = SWG_E_TRANS0 + x->ereq;       /* c >= 2 here: L18 and L20 were completions 0 and 1 (swg_e) */
	uint32_t w = 0, waited = 0, e = 0, spent = 0, buf;
	int ex;

	memset(&io, 0, sizeof io);
	x->esec_done = 0u;
	if (name != NULL && wlen > 4u * SWG_E_PAYW) {
		SWG_E_STOP(x, "payload %u B above the %u B shadow", wlen, 4u * SWG_E_PAYW);
		return 0;
	}
	ex = swg_e_idle(x);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (name != NULL) {
		swg_e_bytes(b, wlen, name, arg, argn);
		ex = swg_e_pay(x, b, wlen);
		if (ex != 0) {
			return ex;
		}
		io.req = b;
		io.reqn = wlen;
	}
	x->ereq++;
	if (x->elv == 24u && x->l24db == 0u) {
		swg_l24_probe(x, "pre");        /* T-c3ac H1: before the request (its host stores, the slot store and the doorbell) */
	}
	ex = swg_d4d_request(x, cmd, tr, wlen, what);
	if (ex != 0) {
		return ex;
	}
	if (x->elv == 24u) {
		printf("SWEET WLAN: L24 hb: %s posted, doorbell written, polling ring 2 (ring-0 WR %u, ring-2 RD %u)\n", what, x->d4wr, x->d4rd);     /* W-C: the line right after the doorbell store returns */
		if (x->l24db == 0u) {
			x->l24db = 1u;
			swg_l24_probe(x, "post");   /* T-c3ac H1: the first read is the fence for the posted doorbell store */
			x->l24spin = 1u;            /* T-c3ac H2: the first poll delay is a per-ms spin */
		}
	}
	io.on = 1;
	io.soft = soft;
	io.cmd = cmd;
	io.trans = tr;
	if (sc != NULL) {
		ex = swg_e_evflush(x);
		if (ex != 0) {
			return ex;
		}
	}
	while (io.got != 3u) {
		ex = swg_d4d_wait(x, 1u, &w, &waited);
		if (ex != 0) {
			return ex;
		}
		spent += waited;
		if (w < x->d4rd) {
			SWG_E_STOP(x, "d2h write index %u went below the read count %u", w, x->d4rd);
			return 0;
		}
		if (w == x->d4rd) {
			SWG_E_STOP(x, "no ioctl %s in %u ms", io.got == 0u ? "answer" : "completion", spent);
			return 0;
		}
		ex = swg_e_items(x, w, &io, sc, &e);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (e > x->d4rd) {
			ex = swg_d4d_consume(x, x->d4rd, e);
			if (ex != 0) {
				return ex;
			}
			if (sc != NULL) {
				ex = swg_e_evflush(x);
				if (ex != 0) {
					return ex;
				}
			}
		}
	}
	x->l24spin = 0u;        /* T-c3ac: a first doorbell answered before any poll delay leaves no spin armed for a later wait */
	buf = io.slot;
	x->rpid[buf] = 0u;      /* consumed: the same pktid is not accepted again until it is re-posted under a new id */
	if (nrsp != 0u) {
		uint32_t n = io.cm.resp_len < nrsp ? io.cm.resp_len : nrsp, i, v;

		for (i = 0; i < n; i += 4u) {
			x->fail = "a d4e response buffer read failed";
			if (swg_hrd(x, x->in.respbuf + (uint64_t)buf * SWD_RESPBUF_LEN + i, &v)) {
				return swg_stop(x, SWG_E_ADDR);
			}
			swd4b_put32(rsp + i, v);
		}
	}
	*cmo = io.cm;
	x->ncpl++;
	x->esec_done = 1u;      /* the completion is consumed: the payload buffer may be written again (review 0282 F5) */
	if (repost) {
		return swg_e_post_resp(x, SWG_E_RID_RE0 + c - 2u, buf);
	}
	return 0;
}

#include "sweet_wlan_g2e.h"

/* review 0282 F5: the payload shadow wiped with the compiler-barrier store loop (the same as swl25_wipe) */
static inline void
swg_e_wipe_b(void)
{
	volatile uint8_t *v = (volatile uint8_t *)swg_e_b;
	uint32_t i;

	for (i = 0; i < sizeof swg_e_b; i++) {
		v[i] = 0u;
	}
	__asm__ volatile("" ::: "memory");
}

/* review 0282 F5: the scrub of the ioctl payload buffer after a secret call whose own scrub did not run (a soft stop). Only when the completion was consumed (x->esec_done: no ioctl in transit, so the store is the plain
 * free-payload write swg_e_call_secret makes) and the engine is not stopped (the caller cleared the soft stop). The same store kind, values withheld. 0 = done or nothing to do, negative = the bus stopped */
static inline int
swg_e_scrub(struct swg_wx *x)
{
	uint8_t z[4u * SWG_E_PAYW];
	int ex;

	if (!x->esec_left || !x->esec_done || x->d4dstop || x->stopped) {
		return 0;
	}
	memset(z, 0, sizeof z);
	x->esec = 1;
	ex = swg_e_pay(x, z, sizeof z);
	x->esec = 0;
	if (ex == 0 && !x->d4dstop) {
		x->esec_left = 0u;
	}
	return ex;
}

/* swg_e_call for a payload that carries a secret (wsec_key, L25 H8/H9): every payload store line prints "(value withheld)" (x->esec), and afterwards the payload words are zeroed again (stores withheld too), so no key word stays in the ioctl buffer or in the shadow whose old values later lines print. 0 = done; negative = stopped */
static inline int
swg_e_call_secret(struct swg_wx *x, uint32_t cmd, const char *what, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, int soft, uint32_t nrsp, uint8_t *rsp, int repost, struct swd4b_ioctl_d2h *cmo)
{
	uint8_t z[4u * SWG_E_PAYW];
	int ex, ex2 = 0;

	x->esec_left = 1u;
	x->esec = 1;
	ex = swg_e_call(x, cmd, what, name, arg, argn, wlen, soft, nrsp, rsp, repost, cmo, NULL);
	if (ex == 0 && !x->d4dstop) {
		memset(z, 0, sizeof z);
		ex2 = swg_e_pay(x, z, sizeof z);
		if (ex2 == 0) {
			x->esec_left = 0u;
		}
	}
	swg_e_wipe_b();         /* review 0282 F5: the shadow of the last secret payload, on every way out (stop paths included) */
	x->esec = 0;
	return ex != 0 ? ex : ex2;
}

#define SWG_J_STOP(x, area, ...) do { char gb_[64]; snprintf(gb_, sizeof gb_, __VA_ARGS__); swl_gap(area, gb_, "high"); (x)->d4dstop = 1u; printf("SWEET WLAN: L%u STOP, no further store: %s %s\n", (unsigned)(x)->elv, area, gb_); } while (0)

/* the length of the NUL terminated name at the start of a command buffer, at most n (n = no NUL) */
static uint32_t
swg_j_nlen(const uint8_t *b, uint32_t n)
{
	uint32_t i = 0;

	while (i < n && b[i] != 0u) {
		i++;
	}
	return i;
}

/* 1 when the NUL terminated name at the start of b is exactly `s` */
static int
swg_j_isname(const uint8_t *b, const char *s)
{
	uint32_t i = 0;

	while (s[i] != '\0' && b[i] == (uint8_t)s[i]) {
		i++;
	}
	return s[i] == '\0' && b[i] == 0u;
}

/* L24 W-A [guess, T-ff10 proof W1]: before the first L24 command wait until ring 2 shows no new item for SWG_Q_QUIET_MS (polls of SWG_POLL_MS, no store of its own; an item that does arrive goes through swg_e_idle, which answers a DS request or consumes an event, and restarts the quiet time), at most SWG_Q_CAP_MS. The cap is a gap, not a stop. 0 = done or soft stop; negative = stopped */
static int
swg_j_quiet(struct swg_wx *x)
{
	uint32_t quiet = 0, spent = 0, w = 0, waited = 0, busy = 0;
	int ex;

	while (quiet < SWG_Q_QUIET_MS && spent < SWG_Q_CAP_MS) {
		ex = swg_d4d_waitn(x, 0u, 1u, &w, &waited);
		if (ex != 0) {
			return ex;
		}
		if (w < x->d4rd) {
			SWG_E_STOP(x, "d2h write index %u went below the read count %u", w, x->d4rd);
			return 0;
		}
		if (w != x->d4rd) {
			busy++;
			quiet = 0;
			ex = swg_e_idle(x);
			if (ex != 0 || x->d4dstop) {
				return ex;
			}
		} else {
			quiet += SWG_POLL_MS;
		}
		x->b->delay_ms(x->b->ctx, SWG_POLL_MS);
		spent += SWG_POLL_MS;
	}
	printf("SWEET WLAN: L24 quiet wait %u ms (%u busy polls, ring-2 RD %u): %s\n", spent, busy, x->d4rd, quiet >= SWG_Q_QUIET_MS ? "ring 2 quiet" : "cap reached, ring 2 still busy");
	if (quiet < SWG_Q_QUIET_MS) {
		swl_gap("H0", "L24 quiet wait cap reached: ring 2 still busy", "medium");
	}
	return 0;
}

/* L24: the event mask for the join: event_msgs_ext SET_MASK (command 3, ver 1, len 26) with bits 69 (ESCAN_RESULT, kept), 0 (SET_SSID) and 16 (LINK) [src BE:113,129,180; TRANSPORT block s.2 L24; U-ev: further events are not needed [guess]] */
static int
swg_j_mask(struct swg_wx *x, unsigned lv)
{
	/* T-924f (sweetwlan >= 25 only): + DEAUTH 5, DEAUTH_IND 6, DISASSOC 11, DISASSOC_IND 12, PSK_SUP 46 [src bcmevent.h.new:118,119,124,125,158 (refs wlan-stage5)], so an AP or firmware give-up after the M2 shows its reason code in the L25 `event detail` line
	 * (boot 20261107T213158Z had only bits 69, 0, 16 on, so a deauth was invisible by construction) [read from source: this function]. The consumed event buffers are not re-posted in L25 (the 8 posted buffers are all there is) [read: swg_e_evflush callers] */
	static const uint32_t bits[8] = { SWG_E_EV_ESCAN, SWG_E_EV_SET_SSID, SWG_E_EV_LINK, 5u, 6u, 11u, 12u, 46u };
	const uint32_t nb = lv >= 25u ? 8u : 3u;
	uint8_t arg[30];
	struct swd4b_ioctl_d2h cm;
	uint32_t i;
	int ex;

	memset(arg, 0, sizeof arg);
	arg[0] = 1u;
	arg[1] = 3u;
	arg[2] = 26u;
	for (i = 0; i < nb; i++) {
		arg[4u + bits[i] / 8u] |= (uint8_t)(1u << (bits[i] % 8u));
	}
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR event_msgs_ext", "event_msgs_ext", arg, 30u, 15u + 30u, 0, 0u, NULL, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L24 event_msgs_ext set: %s on, status %d\n", nb > 3u ? "bits 69, 0, 16, 5, 6, 11, 12, 46" : "bits 69, 0, 16", (int)cm.status);
	return 0;
}

/* L24: the join ioctl (H5) and the wait for SET_SSID status 0 and LINK up, at most SWG_J_BUDGET_MS [decision] after the completion. Events that arrive inside the ioctl are judged by swg_e_items (x->ej). 0 = done or soft stop; negative = stopped */
static int
swg_j_join(struct swg_wx *x, const struct swg_jcmd *c, struct swg_ej *j)
{
	char what[16 + 4u * SWG_E_PAYW];
	struct swd4b_ioctl_d2h cm;
	struct swg_eio io;
	const char *name = (const char *)c->buf;
	uint32_t nl = swg_j_nlen(c->buf, sizeof c->buf), spent = 0, polls, w = 0, waited = 0, e = 0;
	int ex;

	memset(&io, 0, sizeof io);
	snprintf(what, sizeof what, "WLC_SET_VAR %s", name);
	x->ej = j;
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, what, name, c->buf + nl + 1u, c->len - nl - 1u, c->len, 1, 0u, NULL, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L24 H5 join status %d\n", (int)cm.status);
	if (cm.status != 0) {
		SWG_J_STOP(x, "H5", "join ioctl refused status=%d", (int)cm.status);
		return 0;
	}
	ex = swg_e_evflush(x);
	if (ex != 0) {
		return ex;
	}
	while (!(j->ssid_seen && (j->ssid_status != 0u || j->link_up)) && spent < SWG_J_BUDGET_MS) {
		polls = (SWG_J_BUDGET_MS - spent) / SWG_POLL_MS;
		ex = swg_d4d_waitn(x, 1u, polls, &w, &waited);
		if (ex != 0) {
			return ex;
		}
		spent += waited + SWG_POLL_MS;
		if (w < x->d4rd) {
			SWG_E_STOP(x, "d2h write index %u went below the read count %u", w, x->d4rd);
			return 0;
		}
		if (w == x->d4rd) {
			break;
		}
		ex = swg_e_items(x, w, &io, NULL, &e);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (e > x->d4rd) {
			ex = swg_d4d_consume(x, x->d4rd, e);
			if (ex != 0) {
				return ex;
			}
			ex = swg_e_evflush(x);
			if (ex != 0) {
				return ex;
			}
		}
	}
	if (j->ssid_seen && j->ssid_status != 0u) {
		SWG_J_STOP(x, "H5", "join set_ssid status=%u", j->ssid_status);
	} else if (!j->ssid_seen || !j->link_up) {
		SWG_J_STOP(x, "H5", "join no SET_SSID/LINK event in %u ms", spent);
	}
	return 0;
}

/* L24 = H1-H5 (TRANSPORT block s.2): the event mask, then each of swj_join_cmds' commands H1-H4 as one SET_VAR through swg_e_call (a non-zero completion status = a `WIFI GAP: H<n> ...` and a stop), then the join and its events. The dongle is left associated; the undo path runs as in L23. 0 = done or soft stop; negative = stopped */
static int
swg_e_l24(struct swg_wx *x, unsigned lv)
{
	struct swg_ej ej;
	struct swd4b_ioctl_d2h cm;
	char what[16 + 4u * SWG_E_PAYW];
	uint32_t i, nl;
	int ex;

	memset(&ej, 0, sizeof ej);
	if (x->jc == NULL || x->njc < SWG_J_NCMD || x->njc > SWG_J_NCMAX) {
		swl_gap("H5", "join not run: no join commands (SSID/credentials not delivered)", "high");
		printf("SWEET WLAN: L24 not run: no join commands (%u of %u)\n", x->jc == NULL ? 0u : x->njc, SWG_J_NCMD);
		return 0;
	}
	for (i = 0; i < x->njc; i++) {
		nl = swg_j_nlen(x->jc[i].buf, sizeof x->jc[i].buf);
		if (x->jc[i].cmd != SWD4B_WLC_SET_VAR || nl == 0u || nl + 1u > x->jc[i].len || x->jc[i].len > sizeof x->jc[i].buf || (i + 1u == x->njc && !swg_j_isname(x->jc[i].buf, "join"))) {
			SWG_J_STOP(x, "H5", "join command %u malformed, nothing sent", i);
			return 0;
		}
	}
	ex = swg_j_quiet(x);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swg_j_mask(x, lv);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	for (i = 0; i + 1u < x->njc; i++) {
		const char *name = (const char *)x->jc[i].buf;

		nl = swg_j_nlen(x->jc[i].buf, sizeof x->jc[i].buf);
		snprintf(what, sizeof what, "WLC_SET_VAR %s", name);
		if (swg_j_isname(x->jc[i].buf, "wsec_key") || swg_j_isname(x->jc[i].buf, "cur_etheraddr")) {      /* a key iovar (L25 H8/H9 use the same path) or the T-857c station MAC knob: secret-quiet */
			ex = swg_e_call_secret(x, SWD4B_WLC_SET_VAR, what, name, x->jc[i].buf + nl + 1u, x->jc[i].len - nl - 1u, x->jc[i].len, 1, 0u, NULL, 1, &cm);
		} else {
			ex = swg_e_call(x, SWD4B_WLC_SET_VAR, what, name, x->jc[i].buf + nl + 1u, x->jc[i].len - nl - 1u, x->jc[i].len, 1, 0u, NULL, 1, &cm, NULL);
		}
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		printf("SWEET WLAN: L24 %s %s status %d\n", x->jc[i].row, name, (int)cm.status);
		if (i == 0u && cm.status == SWG_J_UNSUPP) {      /* T-9c82: the firmware has no sup_wpa iovar (the L22 GET answered -23 too): loud gap, the join goes on [guess on the reason] */
			swl_gap("H1", "sup_wpa iovar not present (status -23), skipped", "medium");
			printf("SWEET WLAN: L24 H1 sup_wpa not present, continuing\n");
			continue;
		}
		if (cm.status != 0) {
			if (i == 0u) {
				SWG_J_STOP(x, "H1", "sup_wpa set 0 refused status=%d", (int)cm.status);
			} else {
				SWG_J_STOP(x, x->jc[i].row, "%.24s refused status=%d", name, (int)cm.status);
			}
			return 0;
		}
		if (i == 0u) {
			printf("SWEET WLAN: sup_wpa set 0 ok\n");
		}
	}
	ex = swg_j_join(x, &x->jc[x->njc - 1u], &ej);
	x->ej = NULL;
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L24 join done: SET_SSID status %u, link up, ring-0 WR %u, ring-2 RD %u\n", ej.ssid_status, x->d4wr, x->d4rd);
	swg_d4d_done(x, 24u, lv, x->d4wr, x->d4rd);
	return 0;
}

#if defined(SWEET_WLAN_DATA_H) && defined(SWEET_WLAN_JOIN_H)
static int swl25_run(struct swg_wx *x, unsigned lv);        /* sweet_wlan_l25_hs.h: the includer of data.h + join.h includes l25.h and l25_hs.h after this file */
#endif

/* W-D4e, called by swg_d4d after the L20 end. 0 = done or soft stop; negative = stopped */
static int
swg_e(struct swg_wx *x, unsigned lv)
{
	int ex;

	x->elv = 21u;
	x->emax = lv >= 24u ? SWG_E_MAX_CLM : SWG_E_MAX;
	x->ncpl = 2u;           /* L18 and L20 completions */
	memset(x->rpid, 0, sizeof x->rpid);
	memset(x->epid, 0, sizeof x->epid);
	x->epid[0] = SWG_D4_REQID;              /* slot 0 holds the L14 post (id 1); L21 posts ids 0x21..0x27 in slots 1..7 */
	x->rpid[0] = SWG_D4D_RID0 + 2u;         /* slot 0 holds the L20 re-post (ids 2 and 3 were consumed by L18 and L20); L21 posts ids 5..11 in slots 1..7 (swg_e_l21) */
	x->epay[0] = SWG_D4D_CAP_W0;
	x->fail = "the d4e dma_rxoffset read failed";
	if (swg_rd(x, x->in.sh + SWG_SH_RXOFF, &x->rxoff)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (x->rxoff == 0xFFFFFFFFu) {
		x->fail = "dma_rxoffset reads all ones (dead link)";
		return swg_stop(x, SWG_E_DEAD);
	}
	printf("SWEET WLAN: D4e dma_rxoffset (shared +0x24) %u\n", x->rxoff);
	/* patch 0357: one read-only 16-bit read of max_host_rxbufs (Linux brcmf_pcie_read_tcm16 at sharedram_addr + 34): the rx pool size input. Not a store, no new register; a failed read or all ones is a GAP and the pool stays 16.
	 * rxfw_state 0 = unread, 1 = read, 2 = failed (SWDP_RXFW_UNREAD / OK / FAIL of sweet_wlan_data.h, checked by a _Static_assert in sweet_wlan_l25.h) */
	x->rxfw = 0u;
	x->rxfw_state = 0u;
	if (swg_rd16(x, x->in.sh + SWG_SH_RXBUFS, &x->rxfw) != 0 || x->rxfw == 0xFFFFu) {
		x->rxfw_state = 2u;
		printf("WIFI GAP: shared max_host_rxbufs (+0x22) unreadable or all ones (%#x): the rx pool stays 16\n", x->rxfw);
		swl_gap("g2", "shared max_host_rxbufs unreadable, rx pool 16", "medium");
	} else {
		x->rxfw_state = 1u;
		printf("SWEET WLAN: D4e max_host_rxbufs (shared +0x22) %u\n", x->rxfw);
	}
	ex = swg_e_l21(x);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	swg_d4d_done(x, 21u, lv, x->d4wr, x->d4rd);
	if (lv == 21u) {
		return 0;
	}
	x->elv = 22u;
	ex = swg_e_l22(x, lv >= 24u);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	swg_d4d_done(x, 22u, lv, x->d4wr, x->d4rd);
	if (lv == 22u) {
		return 0;
	}
	x->elv = 23u;
	ex = swg_e_l23(x, lv);
	if (ex != 0 || x->d4dstop || lv <= 23u || x->d4dlv != 23u) {
		return ex;      /* L24 only after a finished scan: x->d4dlv is 23 only when the scan reported SUCCESS */
	}
	x->elv = 24u;
#if defined(SWEET_WLAN_DATA_H) && defined(SWEET_WLAN_JOIN_H)
	ex = swg_e_l24(x, lv);
	if (ex != 0 || x->d4dstop || lv <= 24u || x->d4dlv != 24u) {
		return ex;      /* L25 only after a finished join: x->d4dlv is 24 only when SET_SSID status 0 and link up were seen */
	}
	x->elv = 25u;
	return swl25_run(x, lv);
#else
	return swg_e_l24(x, lv);
#endif
}

/* W-D4d, called by swg_d4b after swg_d4c. 0 = done or refused with a gap; negative = stopped (swg_stop ran swg_undo) */
static int
swg_d4d(struct swg_wx *x)
{
	const unsigned lv = (unsigned)x->d4b + 13u;     /* sweetwlan 17..20 */
	uint8_t it[SWD4B_ITEM_LEN], rsp[SWG_D4D_RSP_MAX];
	struct swd4b_ioctl_d2h cm;
	uint32_t wr = 0, rd = 0, d2hw = 0, w = 0, waited = 0, v = 0, w0 = 0, off;
	unsigned got = 0;
	int ex;

	swg_hr(x, SWG_HR_D4D, "d4d");
	memset(&cm, 0, sizeof cm);
	if (!x->d4c_ok) {
		swl_gap("d4d", "not run: the L16 DS handshake did not complete", "high");
		printf("SWEET WLAN: D4d not run: the L16 DS handshake (ack, wake, exit note, deassert) did not complete\n");
		return 0;
	}
	x->fail = "the d4d state read failed";
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], &wr) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_R)], &rd) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &d2hw)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (wr != 0xFFFFFFFFu && rd != 0xFFFFFFFFu && d2hw != 0xFFFFFFFFu && (d2hw & 0xFFFFu) != (rd & 0xFFFFu) && rd == x->d4rd % SWG_R2_DEPTH) {
		/* 22b: ring 2 already holds an item before the first post (the F1 run: a second DS_ENTER_REQ at item 2): a DS request at the front is answered first (swg_ds_front), the state is read again */
		ex = swg_ds_front(x);
		if (ex < 0) {
			return ex;
		}
		while (ex == 1) {
			if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], &wr) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_R)], &rd) || swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &d2hw)) {
				return swg_stop(x, SWG_E_ADDR);
			}
			ex = (d2hw & 0xFFFFu) != (rd & 0xFFFFu) && d2hw != 0xFFFFFFFFu ? swg_ds_front(x) : 0;
			if (ex < 0) {
				return ex;
			}
		}
	}
	printf("SWEET WLAN: D4d L%u start: h2d_w0 0x%x d2h_r0 0x%x d2h_w0 0x%x (expected ring-0 write index %u, ring-2 read index %u)\n", lv, wr, rd, d2hw, x->d4wr, x->d4rd % SWG_R2_DEPTH);
	if (wr == 0xFFFFFFFFu || rd == 0xFFFFFFFFu || d2hw == 0xFFFFFFFFu) {
		x->fail = "a d4d index slot reads all ones (dead link)";
		return swg_stop(x, SWG_E_DEAD);
	}
	if (wr != x->d4wr) {
		SWG_D4D_GAP(x, "ring-0 write index 0x%x, expected %u", wr, x->d4wr);
		return 0;
	}
	if (rd != x->d4rd % SWG_R2_DEPTH) {
		SWG_D4D_GAP(x, "ring-2 read index 0x%x, expected %u", rd, x->d4rd % SWG_R2_DEPTH);
		return 0;
	}
	if ((d2hw & 0xFFFFu) != rd) {
		if (((d2hw & 0xFFFFu) + SWG_R2_DEPTH - rd) % SWG_R2_DEPTH < SWG_R2_DEPTH / 2u) {
			ex = swg_d4d_scan(x, x->d4rd, x->d4rd + ((d2hw & 0xFFFFu) + SWG_R2_DEPTH - rd) % SWG_R2_DEPTH, 0, 0u, 0u, 0u, &cm, &got);
			if (ex != 0) {
				return ex;
			}
		}
		SWG_D4D_GAP(x, "ring-2 write index 0x%x before the first post, expected %u", d2hw, rd);
		return 0;
	}
	x->fail = "the d4d response or payload buffer is not a 4 KiB aligned range inside the G1 block";
	if (swg_hoff(x, x->in.respbuf, SWD_RESPBUF_LEN, &off) || (x->in.respbuf & (SWD_D4D_ALIGN - 1u)) != 0u || (lv >= 18u && (swg_hoff(x, x->in.ioctbuf, SWD_IOCTBUF_LEN, &off) || (x->in.ioctbuf & (SWD_D4D_ALIGN - 1u)) != 0u))) {
		SWG_D4D_GAP(x, "response or payload buffer outside the G1 block");
		return 0;
	}
	if (lv < 21u) {
		swl_gap("d4d", "only 1 ioctl response buffer posted (DHD posts 8)", "low");     /* DHD_FLOWRING_MAX_IOCTLRESPBUF_POST 8 [src M:153, 4286]; L21 posts the other 7 */
	}
	swl_gap("d4d", "ring-0 posts without a DS wake assert (stock asserts, U6)", "medium");      /* stock asserts before every post, M:6221 [src R-D4d s.7 U6] */

	/* L17 */
	swd4b_item_respbuf(it, SWG_D4D_RID0, (uint16_t)SWD_RESPBUF_LEN, x->in.respbuf, swg_ep0(x));
	printf("SWEET WLAN: D4d L17 ioctl-response buffer post: id %u, %u B at pa %#llx, ring-0 idx %u epoch %u, H2D-WR %u, doorbell %#x\n", SWG_D4D_RID0, SWD_RESPBUF_LEN, (unsigned long long)x->in.respbuf, x->d4wr, swg_ep0(x), swg_r0_next(x), SWG_D4D_DB_WR | swg_r0_next(x));
	ex = swg_d4d_post(x, it);
	if (ex != 0) {
		return ex;
	}
	ex = swg_d4d_wait(x, 0u, &w, &waited);      /* the L17 watch (read-only, at every level >= 17): any pending item is one nobody asked for (a DS request is answered first, 22b) */
	if (ex != 0) {
		return ex;
	}
	printf("SWEET WLAN: D4d L17 watch after %u ms: d2h_w0 %u\n", waited, w);
	if (w != x->d4rd) {
		return swg_d4d_scan(x, x->d4rd, w, 0, 0u, 0u, 0u, &cm, &got);      /* any new item is a gap (nothing was asked) and stops the later levels */
	}
	printf("SWEET WLAN: D4d L17 no new ring-2 item in %u ms (expected: no request sent)\n", waited);
	swg_d4d_done(x, 17u, lv, x->d4wr, x->d4rd);
	if (lv == 17u) {
		return 0;
	}

	/* L18: WLC_GET_VERSION. The payload is the zeroed start of the payload buffer (G1 zeroed and flushed it): its first word must still read 0 */
	x->fail = "the d4d payload buffer start is not zero";
	if (swg_hrd(x, x->in.ioctbuf, &v)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (v != 0u) {
		SWG_D4D_GAP(x, "payload buffer word 0 is 0x%x, expected 0", v);
		return 0;
	}
	ex = swg_d4d_request(x, SWD4B_WLC_GET_VERSION, SWG_D4D_TRANS0 + 1u, SWG_D4D_VER_LEN, "WLC_GET_VERSION");
	if (ex != 0) {
		return ex;
	}
	ex = swg_d4d_answer(x, x->d4rd, SWD4B_WLC_GET_VERSION, SWG_D4D_TRANS0 + 1u, SWG_D4D_RID0, &cm, &w);
	if (ex == 0 && !x->d4dstop) {
		swg_d4d_done(x, 18u, lv, x->d4wr, x->d4rd);
	}
	if (ex != 0 || x->d4dstop || lv == 18u) {
		return ex;
	}

	/* L19: read the response bytes, consume, re-post one buffer */
	ex = swg_d4d_rsp(x, cm.resp_len, rsp);
	if (ex != 0) {
		return ex;
	}
	if (cm.resp_len == 0u) {
		swl_gap("d4d", "completion resp_len is 0, no response bytes", "high");
	}
	if (cm.resp_len > SWG_D4D_RSP_MAX) {
		swl_gap("d4d", "resp_len above 1024: only the first 1024 B printed", "medium");
	}
	printf("SWEET WLAN: D4d WLC_GET_VERSION resp_len %u\n", cm.resp_len);
	swg_d4d_hex(rsp, cm.resp_len > SWG_D4D_RSP_MAX ? SWG_D4D_RSP_MAX : cm.resp_len);
	ex = swg_d4d_repost(x, x->d4rd, w, SWG_D4D_RID0 + 1u);
	if (ex == 0) {
		swg_d4d_done(x, 19u, lv, x->d4wr, x->d4rd);
	}
	if (ex != 0 || lv == 19u) {
		return ex;
	}

	/* L20: GET_VAR "cap". The payload "cap\0" is stored first (flushed by the glue), then the request */
	x->fail = "the d4d payload buffer is outside the G1 block";
	if (swg_hoff(x, x->in.ioctbuf, 4u, &off)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->fail = "the d4d response buffer word 0 is not readable";
	if (swg_hrd(x, x->in.respbuf, &w0)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	printf("SWEET WLAN: D4d respbuf word0 before cap 0x%x\n", w0);
	ex = swg_d4d_st(x, SWG_K_PAY, SWG_SP_HOST, off, 0u, SWG_D4D_CAP_W0, 0, "d4d payload word 0");
	if (ex != 0) {
		return ex;
	}
	ex = swg_d4d_request(x, SWD4B_WLC_GET_VAR, SWG_D4D_TRANS0 + 2u, SWG_D4D_CAP_LEN, "WLC_GET_VAR cap");
	if (ex != 0) {
		return ex;
	}
	ex = swg_d4d_answer(x, x->d4rd, SWD4B_WLC_GET_VAR, SWG_D4D_TRANS0 + 2u, SWG_D4D_RID0 + 1u, &cm, &w);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swg_d4d_rsp(x, cm.resp_len, rsp);
	if (ex != 0) {
		return ex;
	}
	printf("SWEET WLAN: D4d WLC_GET_VAR cap resp_len %u\n", cm.resp_len);
	if (cm.resp_len >= 4u) {
		if (swg_hrd(x, x->in.respbuf, &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (v == w0) {
			swl_gap("d4d", "respbuf word 0 unchanged after cap (stale L18 answer?)", "medium");
		}
	}
	if (cm.resp_len > SWG_D4D_RSP_MAX) {
		swl_gap("d4d", "resp_len above 1024: only the first 1024 B printed", "medium");
	}
	swg_d4d_cap(rsp, cm.resp_len > SWG_D4D_RSP_MAX ? SWG_D4D_RSP_MAX : cm.resp_len);
	ex = swg_d4d_repost(x, x->d4rd, w, SWG_D4D_RID0 + 2u);
	if (ex == 0) {
		swg_d4d_done(x, 20u, lv, x->d4wr, x->d4rd);
		if (lv > 20u) {
			return swg_e(x, lv);        /* W-D4e (sweetwlan=21..24) */
		}
	}
	return ex;
}

/* Q1, Q2-Q5, Q6, Q7 (read-only part). 0 = done (a poll without a dongle write is a printed gap, not a failure); negative = stopped (swg_stop ran swg_undo: the unmask first) */
static int
swg_d4b(struct swg_wx *x)
{
	static const char *const nm[SWG_D4_NV] = { "flags", "mb data", "tcm d2h_w[0]", "tcm d2h_w[1]", "tcm d2h_w[2]", "c30", "h2d_r0", "d2h_w0", "ring2 w0", "ring2 w1", "ring2 w2", "ring2 w3", "ring2 w4", "ring2 w5" };
	struct swg_d4s cur, prev;
	uint32_t a[SWG_D4_NV], b[SWG_D4_NV], i, o, waited = 0;
	uint8_t it[SWD4B_ITEM_LEN], in[SWD4B_D2H_LEN], type = 0;
	unsigned n, seen = 0, hseen = 0;
	uint32_t k = 0, imax = 0;       /* the first ring-2 item the dongle writes after the baseline (index k) and the ring-2 item count */
	int r, ex, xb, raised, wrap = 0;
	char gb[64];

	x->step = SWG_ST_N + 1u;
	if (x->d4b >= 2) {
		x->fail = "the d4b event buffer is not a 4 KiB aligned range inside the G1 block";
		if (swg_hoff(x, x->in.evbuf, SWD_EVBUF_LEN, &o) || (x->in.evbuf & (SWD_EVBUF_ALIGN - 1u)) != 0u) {
			return swg_stop(x, SWG_E_ADDR);
		}
		swd4b_item_evbuf(it, SWG_D4_REQID, (uint16_t)SWD_EVBUF_LEN, x->in.evbuf, (uint8_t)SWG_D4_EPOCH);
		for (i = 0; i < SWG_D4_ITEMW; i++) {
			x->d4w[i] = (uint32_t)it[4u * i] | ((uint32_t)it[4u * i + 1u] << 8) | ((uint32_t)it[4u * i + 2u] << 16) | ((uint32_t)it[4u * i + 3u] << 24);
		}
		swl_gap("d4b", "event buffer size unknown, using 8192", "medium");
		swl_gap("d4b", "interrupts not enabled, mask 0xFFFF, no ELBI/GIC path", "low");     /* the spec text (poll only) does not fit the 63 chars of a gap entry */
		if (x->d4b < 8) {
			swl_gap("d4b", "only 1 buffer posted (DHD posts 8+32)", "low");     /* L21 (d4b >= 8) posts 8 + 8 and says so in its own gap */
		}
		printf("WIFI GAP: d4b interrupts not enabled, mask 0xFFFF with no ELBI/GIC path (poll only)\n");     /* the full text of the spec line; the table entry above is the 63-char form */
	}
	/* host reads: flush_dcache64 = dc civac per line + dsb sy here, an invalidate for a line with no CPU-dirty byte; resolved by evidence in sw_g2_rd (caches_asm.s:403-420, Linux cache.S:136-163, 4 B reads, 128 B padded buffers, CPU stores civac'd at once) and the line size is checked at the arm (CTR_EL0): no gap */
	/* sweetwlan=13: the read-only observation (no store). The dongle was seen to advance TCM d2h_w[0] by itself after hostready (run G2-d4a), so the TCM words are read as well as the host block */
	x->fail = "the d4b observation read failed";
	if (swg_d4_read(x, &x->d4base, 0u)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	x->hostrd = 1;          /* the host block words were read (swg_d4_read ok): the miss of swg_base is resolved */
	if (swg_d4_ones(x, &x->d4base)) {
		return swg_stop(x, SWG_E_DEAD);
	}
	for (i = 0; i < SWD4B_D2H_LEN; i++) {
		in[i] = (uint8_t)(x->d4base.r2[i / 4u] >> (8u * (i % 4u)));
	}
	ex = (((x->d4base.pw.dw[0] | x->d4base.d2hw) & 0xFFFFu) != 0u && (x->d4base.r2[0] | x->d4base.r2[1] | x->d4base.r2[2] | x->d4base.r2[3] | x->d4base.r2[4] | x->d4base.r2[5]) != 0u) ? swd4b_d2h_check(in, (uint8_t)SWG_D4_EPOCH, &type) : -1;
	xb = ex == SWD4B_E_XOR || (ex == SWD4B_E_EPOCH && swd4b_d2h_check(in, in[3], &type) == SWD4B_E_XOR);
	printf("SWEET WLAN: D4b ro h2d_r0 0x%x d2h_w0 host 0x%x tcm 0x%x ring2 w 0x%x 0x%x 0x%x 0x%x 0x%x 0x%x type 0x%x epoch 0x%x xor %s c30 0x%x\n", x->d4base.h2dr, x->d4base.d2hw, x->d4base.pw.dw[0],
	       x->d4base.r2[0], x->d4base.r2[1], x->d4base.r2[2], x->d4base.r2[3], x->d4base.r2[4], x->d4base.r2[5], in[0], in[3], ex < 0 ? "n/a" : xb ? "bad" : "ok", x->d4base.pw.c30);
	if ((x->d4base.pw.dw[0] & 0xFFFFu) != 0u && (x->d4base.r2[0] | x->d4base.r2[1] | x->d4base.r2[2] | x->d4base.r2[3] | x->d4base.r2[4] | x->d4base.r2[5]) == 0u) {
		swl_gap("d4b", "dongle advanced ring 2 but host item is zero", "high");     /* the 63-char form; the full spec text follows */
		printf("WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache)\n");
	}
	raised = ex >= 0;
	if (ex >= 0) {
		if (ex != 0) {
			swl_gap("d4b", "epoch/xor mismatch", "high");
			printf("SWEET WLAN: D4b epoch/xor mismatch: check %d, epoch 0x%x, want 0x%x\n", ex, in[3], SWG_D4_EPOCH);
		}
		if (x->d4b < 3 || ex != 0 || type != SWD4B_MSG_D2H_MBDATA) {      /* sweetwlan=16: a clean D2H_MAILBOX_DATA item 0 is answered or refused by swg_d4c, which raises its own gap */
			snprintf(gb, sizeof gb, "ring-2 item type 0x%x not handled", type);
			swl_gap("d4b", gb, "high");
		}
	}
	if (x->d4b < 2) {
		return 0;
	}
	printf("SWEET WLAN: D4b base h2d_r0 0x%x d2h_w0 0x%x ring2 w0 0x%x c30 0x%x\n", x->d4base.h2dr, x->d4base.d2hw, x->d4base.r2[0], x->d4base.pw.c30);
	for (r = 0; r <= SWG_D4_DB0; r++) {
		ex = swg_d4_row(x, r);
		if (ex != 0) {
			return ex;
		}
	}
	x->step = SWG_ST_N + 1u + SWG_D4_DB0;
	prev = x->d4base;
	cur = x->d4base;
	k = x->d4base.d2hw & 0xFFFFu;
	imax = swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].items & 0xFFFFu;
	if (k >= imax) {
		wrap = 1;
		k = 0;      /* the item is not decoded; the reads stay inside the ring */
		swl_gap("d4b", "d2h ring 2 wrap before the post, item not decoded", "high");
		printf("WIFI GAP: d4b d2h ring 2 wrap before the post (baseline d2h_w0 0x%x, max items %u), item not decoded\n", x->d4base.d2hw, imax);
	}
	for (n = 0; n < SWG_POLLS; n++) {
		x->fail = "a d4b poll read failed";
		if (swg_d4_read(x, &cur, k)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (swg_d4_ones(x, &cur)) {
			return swg_stop(x, SWG_E_DEAD);
		}
		if ((cur.pw.fl & (SWG_FLAGS_ASSERT | SWG_FLAGS_TRAP)) != 0u) {
			printf("SWEET WLAN: D4b poll %u flags 0x%x mb data 0x%x TRAP/ASSERT\n", n + 1u, cur.pw.fl, cur.pw.mb);
			x->fail = "the firmware reports assert/trap after the d4b doorbell";
			return swg_stop(x, SWG_E_FATAL);
		}
		if ((cur.pw.mb & SWG_D2H_FWHALT) != 0u) {
			printf("SWEET WLAN: D4b poll %u mb data 0x%x FWHALT\n", n + 1u, cur.pw.mb);
			x->fail = "the firmware reports D2H_DEV_FWHALT in the d2h mailbox after the d4b doorbell";
			return swg_stop(x, SWG_E_FATAL);
		}
		if (n == 0u && k != 0u) {
			for (i = 0; i < 6u; i++) {
				prev.r2[i] = cur.r2[i];
			}
			/* the baseline holds item 0, the poll item k: no baseline of item k, so the first poll is not a change of it */
		}
		swg_d4_flat(&prev, a);
		swg_d4_flat(&cur, b);
		for (i = 0; i < SWG_D4_NV; i++) {
			if (a[i] != b[i]) {
				printf("SWEET WLAN: D4b poll %u: %s 0x%x -> 0x%x\n", n + 1u, nm[i], a[i], b[i]);
				seen = 1;
				if (i >= 6u) {      /* review r1 F3: only h2d_r0, d2h_w0 and the ring-2 words are host memory the dongle wrote; flags, mailbox data, TCM d2h_w and 0xC30 move on their own */
					hseen = 1;
				}
			}
		}
		prev = cur;
		if ((cur.d2hw & 0xFFFFu) != (x->d4base.d2hw & 0xFFFFu)) {
			break;
		}
		if (n + 1u < SWG_POLLS) {
			x->b->delay_ms(x->b->ctx, SWG_POLL_MS);
			waited += SWG_POLL_MS;
		}
	}
	for (i = 0; i < SWD4B_D2H_LEN; i++) {
		in[i] = (uint8_t)(cur.r2[i / 4u] >> (8u * (i % 4u)));
	}
	/* item k carries epoch 1 + k: the D2H epoch starts at 1 and counts +1 per item mod 253 [src R-D4b s.3, M:1782]; k < 64 (the ring-2 count) never reaches the modulo */
	ex = (!wrap && ((cur.d2hw | cur.pw.dw[0]) & 0xFFFFu) != 0u && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) != 0u) ? swd4b_d2h_check(in, (uint8_t)(SWG_D4_EPOCH + k), &type) : -1;
	xb = ex == SWD4B_E_XOR || (ex == SWD4B_E_EPOCH && swd4b_d2h_check(in, in[3], &type) == SWD4B_E_XOR);
	printf("SWEET WLAN: D4b poll h2d_r0 0x%x d2h_w0 0x%x ring2 item %u type 0x%x epoch 0x%x xor %s c30 0x%x tcm d2h_w 0x%x 0x%x 0x%x\n", cur.h2dr, cur.d2hw, k, in[0], in[3], ex < 0 ? "n/a" : xb ? "bad" : "ok", cur.pw.c30, cur.pw.dw[0], cur.pw.dw[1], cur.pw.dw[2]);
	/* the baseline raised the gaps of item 0; a post item k != 0 is another item and raises its own (rule 8) */
	if (ex >= 0 && (!raised || k != 0u)) {
		if (ex != 0) {
			swl_gap("d4b", "epoch/xor mismatch", "high");
			printf("SWEET WLAN: D4b epoch/xor mismatch: check %d, epoch 0x%x, want 0x%x\n", ex, in[3], SWG_D4_EPOCH + k);
		}
		snprintf(gb, sizeof gb, "ring-2 item type 0x%x not handled", type);
		swl_gap("d4b", gb, "high");
	}
	if (wrap) {
		goto nozero;        /* no item was read (the wrap gap above says so): the zero-item check would be false */
	}
	if (((cur.d2hw & 0xFFFFu) > k || (cur.pw.dw[0] & 0xFFFFu) > k) && (cur.r2[0] | cur.r2[1] | cur.r2[2] | cur.r2[3] | cur.r2[4] | cur.r2[5]) == 0u) {     /* review r1 F3: the same zero-item check as the baseline, after the poll; only when an index passed item k (d4b13: D2H-WR 1 = item 0 only, an unanswered post leaves item 1 zero and that is no gap) */
		swl_gap("d4b", "dongle advanced ring 2 but host item is zero", "high");
		printf("WIFI GAP: d4b dongle advanced ring 2 but host item is zero (inbound DMA to 0x8_ PA not seen or cache), after the poll\n");
	}
nozero:
	if (!hseen) {
		snprintf(gb, sizeof gb, "no dongle write seen in %u ms", waited);
		swl_gap("d4b", gb, "high");
		if (seen) {
			printf("SWEET WLAN: D4b only TCM/core words changed (no host-memory write)\n");
		}
	}
	ex = x->d4b >= 3 ? swg_d4c(x, wrap) : 0;      /* W-D4c (sweetwlan=16): the DS_ACK, after the L14 poll */
	if (ex == 0 && x->d4b >= 4) {
		ex = swg_d4d(x);        /* W-D4d (sweetwlan=17..20): the response buffer, GET_VERSION, GET_VAR cap */
	}
	return ex;
}

/* the whole run: entry, the 32 rows in order, the poll, (W-D4b) the post, the undo. 0 = done and undone; negative = stopped (undone as far as it got). The window is left at 0 for W17's restore. */
static int
swg_go(struct swg_wx *x)
{
	int rc, st;

	x->rt.win = 0;          /* W17 hands over with cfg 0x84 = 0 (swl_f3 at level >= 10); swg_win stores only on a change */
	rc = swg_entry(x);
	for (st = 0; rc == 0 && st < SWG_ST_N; st++) {
		if (st == SWG_ST_READY) {
			rc = swg_base(x);       /* W-D4a: the baseline reads, before the hostready store, after every other store */
			if (rc != 0) {
				break;
			}
		}
		rc = swg_st(x, st);
	}
	if (rc == 0) {
		swl_gap("g2", x->d4b >= 2 ? "ring max/len and Command stores still 32-bit (D-9)" : "16-bit golden stores made as 32-bit words (D-9)", "low");
		printf("SWEET WLAN: G2 stores done n=%u\n", x->nstore);
		rc = swg_poll(x);
	}
	if (!x->d4b) {
		printf("SWEET WLAN: D4b not run: sweetwlan level 13/14 not set (G2 only)\n");
	} else if (rc != 0) {
		printf("SWEET WLAN: D4b not run: G2 did not reach hostready and the poll with rc 0 (rc %d)\n", rc);
	} else {
		rc = swg_d4b(x);
	}
	if (rc == 0 && x->keep && !x->d4dstop && x->d4dlv >= 26u) {
		/* sprint 2026-11-08: keep. The undo is NOT run: EP Command stays 0x6 (bus master ON), the RC Command stays as G2 stored it, the BAR2 window cfg 0x84 is not moved back, and the D4b mailbox mask 0xC34 (if stored) is not cleared */
		x->kept = 1;
		printf("SWEET WLAN: G2 keep: dongle left running (undo skipped: EP bus master stays on, RC command kept)\n");
		swl_gap("g2", "keep: undo skipped, EP bus master/RC cmd/0xC34 not restored", "high");
		printf("WIFI GAP: g2 keep: not restored: EP Command 0x2 (bus master stays ON), RC Command low half, D4b mask 0xC34 := 0, BAR2 window cfg 0x84 := 0 (W17 stores that last one); the host-side shadows and indexes stay live\n");
	} else {
		if (rc == 0) {
			rc = swg_undo(x);
			if (rc) {
				x->stopped = 1;
				x->code = SWG_E_STOPPED;
			}
		}
		if (x->rt.win != 0u && swg_win(x, 0u) != 0) {
			rc = rc ? rc : -SWG_E_WR;
		}
	}
	if (x->keep && !x->kept) {
		printf("SWEET WLAN: G2 keep not taken: rc=%d d4dstop=%u level reached %u (< 26): the normal undo ran\n", rc, (unsigned)x->d4dstop, (unsigned)x->d4dlv);
		swl_gap("g2", "keep requested, not taken (run stopped before L26 finished)", "high");
	}
	swg_miss_end(x);
	printf("SWEET WLAN: G2 %s rc=%d stores=%u mask %#x restore=%s\n", rc ? "STOPPED" : "done", rc, x->nstore, x->mask, x->kept ? "kept (undo skipped)" : x->restore ? "ok" : "FAILED");
	return rc;
}

/* sprint 2026-11-08 (keep, boot arg sweetwlankeep=1): ring 2 of the long-lived engine, one pass, called by the glue's poll thread under its en0 lock (never concurrently with an en0 transmit or a swg call). Nothing here answers the dongle: the L22..L26 callers
 * (swg_e_idle -> swg_e_items) STOP on any item they did not ask for, stop at an event cap of 48 and answer a DS request with ring-0 posts the L25 store judge refuses (fail closed); a stop would take the whole data path down. So each pending item is only read
 * (swg_d4d_r2), its epoch/xor checked, classified, PRINTED as not handled (rule 8: a WIFI GAP line per item up to 16 per class, then one per 64 - swl_gap counts every call, so gaps=N in the run summary stays exact) and consumed with the one existing store pair of
 * swg_d4d_consume (D2H-RD slot of ring 2, doorbell 0): no new store kind. Patch 0281: every WL_EVENT is decoded (swg_ke_event) and its buffer re-posted (swg_ke_flush: the ten item words, H2D-WR slot and doorbell 0 of swg_d4d_post, judged by the L25 whitelist) so the dongle does not run out of the eight event buffers; ioctl-response buffers are not re-posted here (the ioctl path re-posts them). A failed epoch/xor check, a write index far behind
 * the read count, or a firmware halt/trap mailbox word ends the data path (d4dstop, loud); a DS_ENTER_REQ is consumed WITHOUT the ack (counted kr2ds, GAP high): whether the firmware then keeps the link is the open question of the keep boot.
 * 0 = done (d4dstop set = a soft stop); negative = stopped (swg_stop ran swg_undo: a dead link or a failed read). Returns after at most SWG_E_BATCH items; the next pass takes the rest. */
static inline int
swg_keep_r2(struct swg_wx *x)
{
	uint8_t in[SWD4B_D2H_LEN], type = 0;
	uint32_t v = 0u, pend, k, n = 0u, mb;
	uint32_t *cnt;
	const char *cls;
	int chk, ex;

	if (!x->kept || x->stopped || x->d4dstop) {
		return 0;
	}
	if (x->kevq > 0u || x->evq > 0u) {      /* patch 0281: buffers of events consumed inside a keep ioctl (the RSSI refresh) are re-posted now, no call is in flight */
		ex = swg_ke_flush(x);
		if (ex != 0) {
			return ex;
		}
	}
	x->fail = "a keep ring-2 write-index read failed";
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (v == 0xFFFFFFFFu) {
		x->fail = "host d2h_w[0] reads all ones (dead link) in the keep poll";
		return swg_stop(x, SWG_E_DEAD);
	}
	pend = ((v & 0xFFFFu) + SWG_R2_DEPTH - x->d4rd % SWG_R2_DEPTH) % SWG_R2_DEPTH;
	if (pend == 0u) {
		return 0;
	}
	if (pend >= SWG_R2_DEPTH / 2u) {
		SWG_E_STOP(x, "keep: ring-2 write slot %u is far behind the read count %u", v & 0xFFFFu, x->d4rd);
		return 0;
	}
	for (k = x->d4rd; k < x->d4rd + pend && n < SWG_E_BATCH && !x->d4dstop; k++) {
		ex = swg_d4d_r2(x, k, in);
		if (ex != 0) {
			return ex;
		}
		chk = swd4b_d2h_check(in, swg_ep(k), &type);
		if (chk != 0) {
			x->kr2c++;
			printf("SWEET WLAN: keep ring2 item %u raw 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x epoch/xor check %d\n", k, swd4b_get32(in), swd4b_get32(in + 4), swd4b_get32(in + 8), swd4b_get32(in + 12), swd4b_get32(in + 16), swd4b_get32(in + 20), chk);
			SWG_E_STOP(x, "keep: ring-2 item %u epoch/xor check failed (%d)", k, chk);
			break;
		}
		mb = 0u;
		if (type == SWD4B_MSG_D2H_MBDATA) {
			mb = swd4b_get32(in + 12);
			cnt = mb == SWD4B_D2H_DS_ENTER_REQ ? &x->kr2ds : &x->kr2o;
			cls = mb == SWD4B_D2H_DS_ENTER_REQ ? "DS_ENTER_REQ NOT ANSWERED" : "mailbox data";
		} else if (type == SWD4B_MSG_WL_EVENT) {
			cnt = &x->kr2ev;
			cls = "event";
		} else {
			cnt = &x->kr2o;
			cls = type == SWD4B_MSG_IOCTL_ACK || type == SWD4B_MSG_IOCTL_CMPLT ? "ioctl ack/completion (no request was sent)" : "type";
		}
		(*cnt)++;
		x->kr2n++;
		if (type == SWD4B_MSG_WL_EVENT) {       /* patch 0281: decoded, printed (one line), judged by the link detector and queued for its buffer re-post; no longer an unhandled item */
			ex = swg_ke_event(x, in, k);
			if (ex != 0) {
				return ex;
			}
			n++;
			continue;
		}
		if (*cnt <= 16u || (*cnt & 63u) == 0u) {
			printf("WIFI GAP: keep ring-2 item %u type 0x%x (%s, class count %u, mailbox data %#x) not handled, consumed without an answer\n", k, type, cls, *cnt, mb);
		}
		swl_gap("keep", type == SWD4B_MSG_D2H_MBDATA ? (mb == SWD4B_D2H_DS_ENTER_REQ ? "ring-2 DS_ENTER_REQ consumed, not acked" : "ring-2 mailbox data consumed, not handled") : "ring-2 item consumed, not handled", "high");
		if (type == SWD4B_MSG_D2H_MBDATA && (mb & (SWG_D2H_FWHALT | 0xE1000000u)) != 0u) {
			x->kr2c++;
			printf("SWEET WLAN: keep ring2 item %u mailbox data %#x is a firmware halt/trap code: the data path ends\n", k, mb);
			SWG_E_STOP(x, "keep: firmware halt/trap mailbox data %#x", mb);
			n++;
			break;
		}
		n++;
	}
	if (n == 0u || x->d4dstop) {
		return 0;       /* a stop (epoch/xor, firmware halt): no further store */
	}
	ex = swg_d4d_consume(x, x->d4rd, x->d4rd + n);
	if (ex != 0) {
		return ex;
	}
	return x->kevq > 0u ? swg_ke_flush(x) : 0;       /* patch 0281: the consumed events' buffers go back to the dongle (ring-0 posts after the ring-2 read index, as the L21/L24 flush does) */
}

/* patch 0281 (checklist item 3; review F1: moved here from sweet_wlan_l25_hs.h swl25_rssi_drain, which calls it): ring 2 carries events now (an AP loss makes several), so the idle check of swl25_live_rssi could skip every refresh. While D2H-W != the read count run
 * one more keep pass (swg_keep_r2: decode, consume, re-post), at most 4 extra passes of SWG_E_BATCH items. 0 = ring 2 idle or the passes ran (the refresh makes its own idle check and says so when it still skips), 1 = a soft stop (x->d4dstop, the data
 * path ends in the caller's loop), negative = the bus stopped. No store of its own. */
static inline int
swg_rssi_drain(struct swg_wx *x)
{
	uint32_t v = 0u;
	unsigned dr;
	int ex;

	for (dr = 0; dr < 4u; dr++) {
		if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v) || v == 0xFFFFFFFFu || (v & 0xFFFFu) == x->d4rd % SWG_R2_DEPTH) {
			break;
		}
		ex = swg_keep_r2(x);
		if (ex < 0 || x->stopped) {
			return -1;
		}
		if (x->d4dstop) {
			return 1;
		}
	}
	return 0;
}

#endif /* SWEET_WLAN_G2_H */
