# Wi-Fi engine capacity: every ring and buffer, and what happens when it runs out

The bring-up engine (G2 + W-D4d/e + L25/L26, `shim/sweet_wlan_g2*.h`, `shim/sweet_wlan_l25*.h`) was built to run once per boot. Each limit below stops a run
when it is reached; this table lists them so a limit fails in a host test before it fails on the phone. Depths come from ONE place,
`sweet_wlan_dma.h` `SWD_R*_ITEMS` (xnu patch 0167); `wlan_l25_wl_host_test.c` checks the copies at compile time.

| Ring / buffer | Size | Wraps / reused? | When it runs out | Source |
|---|---|---|---|---|
| ring 0, H2D control submit (ioctls, buffer posts, flow create) | 128 x 40 B | wraps since 2026-11-08 (slot and WR mod 128, epoch over the absolute count, ring-full check against H2D-RD) | `D4d ring-0 post refused` (ring full) | `swg_d4d_post`, `swg_dp_room`, `swl25w_ok` L1/L2/L7 |
| ring 1, H2D rx post | 2048 x 32 B, 16 used | NO: 16 posts once, no re-post | `WIFI GAP: L25 rx buffers exhausted` after 16 received frames (M1, M3 and any LAN broadcast count) | `swl25_rxposts`, `SWL25_RXPOSTS`, `SWL25W_RX_N` |
| flow ring (tx post) | 64 x 48 B, 16 shadowed | NO: 16 tx buffers, each used once | the 17th send is refused (no shadow) | `SWL25_SHADOW_ITEMS`, `SWL25W_TX_N` |
| ring 2, D2H control complete | 64 x 24 B | wraps (consume = slot mod 64, absolute epoch) | - | `swg_d4d_consume` |
| ring 3, D2H tx complete | 2048 x 24 B | wraps (DP15 consume) | - | `swdp_plan_consume` |
| ring 4, D2H rx complete | 1024 x 40 B | wraps (DP15 consume) | - | `swdp_plan_consume` |
| ioctl response buffers | 8 x 8192 B | re-posted after each answer | - | `swg_e_call` |
| event buffers | 8 x 8192 B | re-posted | DHD posts 32 (`WIFI GAP: scan 8 event buffers posted`) | `SWD_EVBUF_N` |
| L26 pings | 4 echoes | - | the run ends after 4 (W-PING asks 10: needs tx reuse) | `SWL25_PINGS` |
| DS (deep-sleep) rounds | ring-0 items at d4c_wr..+2, no wrap | NO: refused within 4 slots of the ring end, and after a lap their old word is not 0 | `G2 STOP` | `swg_d4c_*`, `sweet_wlan_g2d.h` round check |

Open: rx re-post and tx reuse (rows 2-3) are what a long-lived `en0` needs (W-IOSPING); the en0 live-bus host build works on them.
