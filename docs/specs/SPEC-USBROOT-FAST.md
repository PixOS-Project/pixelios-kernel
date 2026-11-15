# SPEC-USBROOT-FAST: make the USB root fast enough to replace the userdata flash (2026-10-28)

Goal: a root change costs a host file edit, not a ~9 min userdata flash (research 99 rank 1). Scope of this spec:
where the ~28-32 ms per 16 KiB read goes, and the changes in order of gain. Section 3 is built (host only);
section 4 is DESIGN ONLY (needs a kernel patch, review, run plan).

Tags: [obs] = phone log of a hardware run, [src] = read from source (file:line), [host] = measured on the host
(not the phone), [guess] = not checked.
Sources: probe-4 run dir `logs/runs/20261026T202654Z-usbroot-probe-4` in the test/usbroot-probe worktree
(gitignored; merged.log line numbers below), xnu/patches/0073-sweet-usb-ecm.patch (`0073`),
xnu/patches/0157-sweet-usbroot-ro.patch (`0157`), tools/usbroot-host.py.

## 1. Measured breakdown of one 16 KiB read (probe 4)

Protocol as built [src]: raw Ethernet, EtherType 0x88B6 (0157:195, usbroot-host.py:29); one request outstanding
(0157:232 `ur_lock` "one block request at a time", ur_xfer 0157:401-470); READ at most 16 KiB (0157:202, phone
clamps the host's max_read to it, 0157:746); reply in fragments of 1024 bytes (0157:200), each its own Ethernet
frame of 14 + 36 + 1024 = 1074 bytes; the phone checks seq/op/total/lba echo and `frag_len == min(1024, rest)`
(0157:340-356).

Whole run, from the `request ...` / `attempt N answered after W ms` trace pairs (0157:446,461) [obs,
merged.log:293-20761, my count with a throwaway parser of those two lines]:

| Quantity | Value |
|---|---|
| READ requests | 10131 (all 16384 bytes), 0 unanswered, 0 retries |
| span first..last request | 332.6 s (uptime 98294..430924 ms) |
| request -> answer (uptime stamps) | median 32 ms, mean 32.4, p90 33 |
| phone wait-loop count W (iterations of `ur_sleep_ms(1)`) | median 28, p10 27, p90 28, max 95 |
| answer -> next request (phone side between reads) | mean 0.39 ms; sum 3.9 s of 332.6 s |
| next request is the next 16 KiB | 9552 of 10130; 579 runs; the 4 longest are 2048 reads (32 MiB) each |
| distinct LBAs | 10118 (13 repeats) |

The mount window alone (HFBT 100.0 s .. mounted 376.2 s) holds 8514 of these reads (runplan
usbroot-probe5-hfs-trace.md:7) [obs, that plan's count].

Where the 32 ms go:
- Phone-side work between reads: ~0.4 ms (1%) [obs: answer -> next request]. The phone is waiting for the disk
  99% of the time.
- Host service (frame received -> 16 reply frames handed to the socket): 0.095 ms mean on a warm image,
  10132 probe-4 reads replayed against /tank/pixelios/ios-root4-inj6.img with tools/test_usbroot_fast.py
  `--image --log` (cache off: mean 0.095, p99 <= 0.256, max 2.2 ms) [host, 2026-10-28]. Cold (first pass after
  the image was not read for 2 days): mean 11.0 ms, p50 <= 0.128, p99 <= 524 ms, max 2.6 s [host]. Probe 4 did
  not see cold stalls: W max 95 ms and 0 retries (a > 2000 ms stall retries, 0157:207,447) [obs], so its image
  was warm [inference from the W distribution].
- One round trip with a one-fragment reply: the INFO exchange (16-byte reply = 1 frame) is `answered after 1 ms`
  (merged.log:288) [obs]. So phone TX + USB both ways + host service + the phone noticing <= ~1-2 ms.
- The rest, ~30 ms (~94%), is receiving 16 fragments: (32 - 1) / 15 = ~2.1 ms per extra fragment [obs
  arithmetic]. The first reads printed one TX-complete event (0x0000c046: ep 3 = EP_TX) and 16 RX-complete events
  (0x0000e044: ep 2 = EP_RX, type 1 XferComplete) per read (merged.log:293-312; decode ep = (ev>>1)&0x1f,
  type = (ev>>6)&0xf, 0073:406-407; EP numbers 0073:38-39) [obs + src]. Event printing stops after 200 events
  (0073:437), so later reads show none.
- Why one fragment costs ~2 ms [src, mechanism; the 2 ms itself is inference]: the RX side has ONE transfer
  buffer: `UD_RX` "one received frame" (0073:32), one TRB at UD_TRB_RX (0073:26), armed for one frame of up to
  UD_FRAME_MAX 0x800 (0073:34, 0073:228-230) and re-armed only after software handled the frame (0073:258; the
  usbroot hook re-arms too, 0157:71-74). The events are polled by ud_thread, which sleeps 1 ms per loop
  (0073:426-447; no interrupt). So fragment N+1 cannot land until the thread woke, saw fragment N and re-armed.
  The phone's own 1 ms sleeps last ~1.14 ms each (W = 28 iterations in 32 ms) [obs arithmetic]; ~2.1 ms per
  fragment is about two such ticks. Unknown U1: whether the second tick is the event-thread wake-up or the host
  controller's retry interval after the phone NAKed the OUT transfer [guess either way; settle with section 5].

Summary: host service 0.1 ms (0.3%), round trip ~1 ms (3%), phone fragment-by-fragment RX ~30 ms (94%),
phone between reads 0.4 ms (1%). The bottleneck is the phone's single RX buffer + 1 ms polled event thread,
not the host and not USB 2 bandwidth (16 KiB in 32 ms = 0.5 MB/s against a 480 Mbit/s link) [obs + src].

## 2. Changes in order of gain

| # | Change | Where | Gain on a probe-4-like boot | Status |
|---|---|---|---|---|
| P1 | RX ring: N RX TRBs + buffers instead of one, re-armed in a batch | phone, sweet_usb.c (0073) | removes ~30 of 32 ms per read if fragments then arrive back to back: ~10x [guess, needs U1] | DESIGN ONLY, hardware-programming change |
| P3 | several outstanding v1 requests + phone read-ahead window | phone, 0157 | overlaps round trips; with P1 another ~1.5-2x on sequential runs (94% of reads) [guess] | DESIGN ONLY |
| P2 | wake the waiting reader from the RX path instead of 1 ms polling in ur_xfer, plus a 100 us event-thread poll while a reply is due (P2b) | phone, patch 0172 (sweetusbrootfast=1) | P2 alone <= ~1 ms per read (3%); with P2b see section 6 [guess, run usbroot-rxfast measures it] | WRITTEN 2026-10-28 (0172, branch tools/usbroot-rxfast), compiled (xnu-local-build --label rxfast, 2026-10-28, kernel sha256 1d18258c...c3a8), host-tested, not run; no register access added |
| H1 | host chunk cache + sequential read-ahead | tools/usbroot-host.py | warm image: none [host: 0.095 -> 0.096 ms]; cold image: avoids 11 ms mean / 0.5 s p99 host stalls [host, see 3] | BUILT |
| H2 | host STATS: service time vs reply->next-request time, per run | tools/usbroot-host.py | measurement for U1 on the next run | BUILT |
| H3 | larger / merged responses from the host alone | - | impossible in v1, see below | NOT POSSIBLE |

H3: the phone rejects any reply that is not exactly the current request's fragments: seq/op/total/lba must echo
the one exchange in flight and the state must be EX_WAITING (0157:340-343), `frag_len == min(1024, total -
frag_off)` and fragment index < 32 (0157:352-354); everything else is counted as a bad frame (0157:369-374).
ECM carries one Ethernet frame per bulk transfer and the phone's RX buffer holds one frame (0073:32-34). So a host
cannot send more than 16 KiB per request, cannot use bigger fragments and cannot push read-ahead data [src].

## 3. Host side (built, tools/usbroot-host.py, wire protocol unchanged)

- BlockCache: aligned chunks (`--chunk-kib`, default 256, a multiple of 16) in an LRU (`--cache-mib`, default
  256; 0 = old behaviour, one file read per request). After the reply frames are sent, serve() calls idle(): when
  the request continued the previous one, the next chunk is read ahead while the phone receives fragments. The
  image stays opened "rb" only; the cache holds read bytes only. fstat size/mtime is compared at most every 1 s;
  a change drops the cache and logs `USBROOT GAP: image ... changed while serving` (the phone may hold stale
  metadata: reboot it). A path replaced by rename keeps serving the old inode (as before).
- Stats (bounded log2 histograms): host service time per request, and reply sent -> next request (USB + phone),
  plus cache counters; printed as `STATS:` lines at the end and every `--stats-every` s (default 60) of traffic,
  so the systemd service log shows them per boot. RESULT gains `gaps=N`.
- Loud (rule 8): every ignored frame is logged with its reason and header hex, every refusal, every I/O error,
  every repeated read of the same lba (a reply lost on the wire makes the phone retry after 2000 ms, 0157:207);
  the end summary adds `USBROOT GAP: N unexpected event(s)`. Own outgoing replies are not seen by the socket
  (probe 3 served 2553 requests with `ignored 0`, logs/usbroot-host-3.log in the usbroot-probe worktree [obs]).
- Test: `python3 -I tools/test_usbroot_fast.py`: a fake phone is the transport of the real serve() loop, checks
  every fragment like sweet_usbroot_rx and the bytes against the image, on a probe-4-shaped pattern (4 long
  sequential runs + short random runs, 16 KiB) on a 32 MiB image; re-runs every protocol test of
  test_usbroot_host.py with the cache in front; prints SPEEDUP lines (host service, page-cached file and a
  0.5 ms-per-read-call slow-store MODEL). `--image IMG --log merged.log` replays a real run's reads read-only.
- Expected gain on the phone: none for a warm image (host is 0.3% of the cycle) [host measurement]; it only
  removes cold-image stalls. The speedup has to come from section 4.

## 4. Phone side (P2 built as patch 0172, see section 6; P1 and P3 DESIGN ONLY)

P1 RX ring (largest gain). Replace the single RX TRB/buffer (0073:26,32,228-230) by a ring of N normal TRBs
(e.g. 16, one 2 KiB buffer each, so a whole 16 KiB reply fits) closed by a link TRB; ud_rx_done walks completed
TRBs in order, hands each frame to sweet_usbroot_rx / the IP path, and re-arms them. This programs DWC3 TRBs
(hardware-facing): every field (link TRB layout, HWO/CHN/IOC bits, update-transfer command) must be cited from
the Linux dwc3 driver at a pinned commit or the DWC3 databook before it is written (rule 7); table-driven,
allowlisted, reviewed by Opus. Not started. Open: the ring length that keeps the 16 KiB reply in flight when the
IP path also uses the endpoint.

P2 wake-up instead of polling. sweet_usbroot_rx sets EX_DONE under ur_spin (0157:350-363); add
a wakeup there (0172 uses its own ur_done_chan, see section 6) and let ur_xfer wait once with the 2000 ms deadline instead of 2000 x 1 ms
(0157:447-455). Must stay non-sleeping in the RX path (0157:313-314); thread_wakeup does not sleep [src:
xnu-8796.141.3 osfmk/kern/sched_prim.c:1744-1760, thread_wakeup_prim -> waitq_wakeup64_all/one, spin locks only].
The event thread still polls at 1 ms (0073:447), so this only removes the reader's own tick. WRITTEN as patch 0172 (compiled, not run)
together with P2b (section 6).

P3 multiple outstanding requests + read-ahead, protocol stays v1 on the wire. Each request is still a v1 READ of
<= 16 KiB with its own seq. The host already answers requests independently: handle() keeps no per-request state
besides counters and the last (lba, total) for the repeat log (tools/usbroot-host.py:306-345) and the serve loop answers frames in arrival order [src],
so an unchanged host serves K requests in flight. Phone changes: a slot table (seq, lba, total, mask, state,
buffer) instead of the single exchange (0157:250-258) matched by seq in sweet_usbroot_rx; a window cache of K x
16 KiB instead of one 16 KiB window (0157:246-249); on a sequential miss request the next K windows; per-slot
retry with a fresh seq as today. DKIOCGETMAXBLOCKCOUNTREAD stays max_read/secsize (0157:605-607) or grows to the
window only after this works.

Compatibility. v1 stays the default both ways. If a later change needs bigger fragments or reads (the phone's
INFO check requires frag == 1024, 0157:739; max_read is clamped to 16 KiB, 0157:746), negotiate first: the phone
sends a new opcode (e.g. 3 CAPS) once after INFO; a v1 host refuses it with status 2 BADOP
(tools/usbroot-host.py:323, op not INFO/READ) and the phone stays v1; a new host answers with its limits.
An old phone never sends op 3, so a new host serves it unchanged. Frame size ceiling for bigger fragments: the
phone RX buffer is 2048 bytes (0073:34) and usbroot frames up to that bypass the ETHER_MAX_LEN check (0157:72);
the host interface MTU (1500 by default [guess, not read on the host]) limits a reply frame to 1514 bytes, i.e.
1464 payload bytes.

## 5. Next measurements

1. Next usbroot run with this host tool: `STATS: reply sent -> next request` vs the phone's `answered after`
   gives host-side vs phone-side time per read on the same run (U1 partly).
2. A phone-side trace (opt-in boot-arg, read-only) of uptime per RX completion for a few reads would settle U1
   (event-thread tick vs host retry). Not designed here.

## 6. Patch 0172 (stage 1, WRITTEN, compiled (xnu-local-build), not run) and the RX ring (stage 2, DESIGN ONLY)

Stage 1 = patch 0172, boot-arg `sweetusbrootfast=1` (absent/0: the old code paths run unchanged and print nothing new;
other values: `SWEET USBROOT: GAP: ... ignored` and off). Only with rd=usbroot0 (set in ur_rootdev after the
gs101 board check, before sweet_usb_probe).
- P2 (reader): ur_xfer sleeps once with `lck_spin_sleep_deadline(&ur_spin, ..., &ur_done_chan, THREAD_UNINT,
  deadline)` (xnu locks.c:1067-1090: assert_wait_deadline under the lock, then unlock + thread_block, so a
  wake-up between the check and the sleep is not lost) [src]; sweet_usbroot_rx calls `thread_wakeup(&ur_done_chan)`
  after `lck_spin_unlock` when the exchange went EX_DONE [src 0172]. Same 2000 ms deadline, UR_TRIES, fail-fast.
- P2b (event thread, sweet_usb.c ud_thread): own sleep event `ud_rx_chan` (ud_log_thread keeps ud.sleep_chan);
  while a usbroot request waits (`sweet_usb_rx_hurry(true)` from ur_xfer before the send, `false` after the
  attempt) it sleeps 100 us with `assert_wait_deadline_with_leeway(..., TIMEOUT_URGENCY_SYS_CRITICAL, deadline, 0)`;
  timer_call_slop() adds no coalescing leeway for SYS_CRITICAL (xnu osfmk/kern/timer_call.c:1903-1924) [src]; the
  old 1 ms sleep uses assert_wait_deadline = TIMEOUT_URGENCY_SYS_NORMAL (sched_prim.c:1315-1317) [src], which fits
  the observed 1.14 ms per 1 ms sleep (W = 28 in 32 ms, merged.log; median W re-counted 28 over 10133
  `answered after` lines) [obs]. Idle in fast mode: 1 ms as before, woken at once by sweet_usb_rx_hurry(true).
  The loop body (GEVNTCOUNT read, events, GEVNTCOUNT ack only when count != 0, 0073:430-442) is unchanged [src]:
  no new register, field or DWC3 command; only the time between two loops is shorter, at most UR_TRIES x (UR_WAIT_MS send retries + UR_WAIT_MS wait), ~17 s per request; with the host gone ~60% duty under fail-fast (review usbroot-rxfast-r1 F5).
  100 us is a software choice (log rate / CPU), not a hardware value.
- Measurement: `SWEET USBROOT: fast stats: 256 answers (total T), request->answer us mean M min A max B, over
  8000 us S, event-thread short polls P, bad frames F` every 256 answered requests; the per-request
  `answered after W ms` keeps its format but W is elapsed ms in fast mode (not loop iterations).
- Expected [guess, both cases are mechanisms from section 1, the run decides U1]: (a) if the second ~1 ms per
  fragment is the event-thread tick, a fragment costs ~0.1-0.3 ms (re-arm + one 100 us poll), a 16 KiB read
  ~3-6 ms (5-10x); (b) if it is the host controller's retry after a NAK of the unarmed OUT endpoint (~1 ms),
  ~1.1-1.3 ms per fragment, ~18-21 ms per read (~1.6x). Mean M between the two settles U1 for stage 2.
- Host tests: `python3 -I tools/test_usbroot_c.py` applies 0172's sweet_usbroot.c section and runs test_fast (off path never
  touches the hooks; on path: bytes, one wake per answer, spurious/late/early wake-ups, W from the fake clock, exact stats and
  GAP lines, boot-arg string "1"/"0"/absent/other) and `python3 -I tools/test_usb_rxfast_c.py` runs the sweet_usb.c half
  (ud_rx_fast_wait, sweet_usb_rx_hurry, the ud_thread sleep tail: deadlines, SYS_CRITICAL, leeway 0, clear_wait race) against a
  stub scheduler [host]. Mutants M1b, M2, M3, M8, M10, M14, M16, M19 and three sweet_usb.c mutants make them FAIL (review
  usbroot-rxfast-r1 C4).

Stage 2 (P1 RX ring): NOT implemented. Every TRB field it needs can be cited from Linux v6.6
drivers/usb/dwc3 (fetched 2026-10-28 to ~/pixelios-artifacts/linux-dwc3-v6.6, raw.githubusercontent tag v6.6) [src],
but the design depends on facts that are not cited yet (rule 7), listed after the field table.

| Field / step | Value | Linux v6.6 source | Repo usage today |
|---|---|---|---|
| TRB ctrl HWO, LST, CHN, CSP | bits 0, 1, 2, 3 | core.h:831-834 | HWO/LST used, sweet_usb.c TRB_HWO/TRB_LST; CHN/CSP unused |
| TRBCTL normal / link | (1 << 4) / (8 << 4) | core.h:835,842,849 | TRBCTL_NORMAL (0073:41); link unused |
| ISP_IMI, IOC | bits 10, 11 | core.h:836-837 | used on every TRB (ud_start_trb) |
| TRB size field (remaining bytes) | bits 23:0 | core.h:820 | `UD_FRAME_MAX - (t[2] & 0xffffff)` (0073:238) |
| link TRB: bpl/bph = ring start, HWO set, never cleared | - | gadget.c:917-925 | unused |
| OUT TRB: CSP when not stream-capable, ISP_IMI for short-not-ok | - | gadget.c:1336-1342 | ISP_IMI only |
| IOC when not chained | - | gadget.c:1348-1349 | always IOC |
| OUT length must be a multiple of maxpacket (else extra TRB) | 2048 = 4 x 512 ok | gadget.c:1380-1387 | UD_FRAME_MAX 0x800 |
| Update Transfer command, resource index in DEPCMD[22:16] | cmd 7, PARAM(x) = x << 16 | core.h:547-549,558; gadget.c:1669-1671 | shim/usbgadget.c:235,257 reads/uses rsc idx for END_TRANSFER only |
| DEPEVT XferComplete / XferInProgress / XferNotReady | types 1 / 2 / 3 | core.h:1358-1360 | ud_event handles type 1 for EP_RX (0073:410) |
| DEPCFG event enables XferComplete / XferInProgress | param1 bits 8 / 9 | gadget.h:24-25; gadget.c:635-637 (all non-control eps get bit 9) | 0073:157 sets bit 8 only for EP_RX |
| reclaim: completed TRB has HWO clear; short packet ends the TD | - | gadget.c:3344-3409 | single TRB, HWO not checked |

Open questions (each blocks implementation):
- Q1 Which event a ring TRB with IOC and without LST raises on this core: Linux expects XferInProgress (type 2) and
  enables it per endpoint (gadget.c:635-637); 0073 configures EP_RX with XferComplete only (0073:157). A ring
  needs SETEPCONFIG param1 bit 9 for EP_RX, a change of the endpoint configuration the IP path also uses. Needs a
  databook citation or a measured run with the event printing on.
- Q2 Behaviour of CSP + ISP_IMI on a bulk OUT TRB ended by a short packet (every ECM frame is < 2048 bytes): Linux
  skips to the next TD (gadget.c:3355-3366 clears HWO by hand on chained TRBs); our TRBs are not chained, so each
  frame is one TD; whether the core moves on to the next HWO TRB without an Update Transfer is not cited.
- Q3 Memory: the DMA area is 16 KiB at ABL's GEVNTADR page and must stay inside the abl range
  (sweet_usb.c gs101 probe: map_hi = pool + UD_SIZE, checked against dma-pool-range) [src]. Free inside it today:
  0x1310-0x2000 and 0x3000-0x4000 [src: the UD_* offsets, 0073:23-35] = room for 3 more 2 KiB buffers (ring of 4
  with UD_RX), not 16. Growing UD_SIZE needs proof that more of the abl range is unused (not measured).
- Q4 U1 (section 1): if stage 1 run (b) shows the host NAK-retry interval is the cost, a ring of 4 removes it for
  4 frames per arm cycle; if (a), stage 1 already gets most of the gain and the ring is not worth the risk.
- Q5 TRB ring + IP path: frames for the IP stack and for usbroot share EP_RX; ordering and the re-arm of a
  partially consumed ring when the IP path fails mbuf allocation (0073 ud_rx_done drops and re-arms).
Second justification for a reviewer: an independent source for Q1/Q2 (DWC_usb3 databook "Transfer Ring" /
"Short packet" sections, or another open driver: FreeBSD dwc3 / U-Boot dwc3 gadget) must agree with Linux before
any of this is written; a stage-2 patch then needs the table-driven allowlist + always-run teardown of
shim/wlanlink.c and an Opus review (hard-rule HW-WRITE).
