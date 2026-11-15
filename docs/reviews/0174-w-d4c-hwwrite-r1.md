# Review 0174 W-D4c (sweetwlan=16, DS_ACK) HW-WRITE r1

Reviewer: Opus 5.5, adversarial, 2026-10-29. Scope: `git diff 914bd19 30af750` plus `git diff 30af750 efb0390`, worktree `wip/w4-d4c` @ efb0390.
Sources: DHD tree `~/pixelios-artifacts/refs/wlan-stage5/` (dhd_msgbuf.c, dhd_pcie.c, bcmmsgbuf.h, bcmpcie.h), xnu-pristine, Linux v6.6 cache.S.
Tags: [src] read from source (file:line), [obs] observed (run / command named), [guess].

## Verdict: SAFE WITH CONDITIONS

Every store is a host-block word or PCIe core +0x140. Each one has a whitelist entry with an exact value, an old-value pin and a chain, and the
undo runs on every exit path. I found no store that could damage the hardware. The phone run must not happen until F1 and F2 are fixed or settled
by golden evidence, and F6 holds (L14 and L15 observed first). F3 to F5 are cheap fixes for the same batch.

## What was checked and holds

- Values [src]: msg_type 0x23 (bcmmsgbuf.h:259); h2d_mailbox_data_t = 8 B cmn_msg_hdr + u32 mail_box_data at +8 + 7 rsvd (bcmmsgbuf.h:181-192, 626-630), so w2 = mb_data.
  flags = ring current_phase: ctrl_subn starts at 0 (dhd_msgbuf.c:1824) and flips to 0x80 only at the ring base (dhd_msgbuf.c:10777-10787), so item 1 has 0x80.
  Epoch: seqnum starts at 254 (H2D_EPOCH_INIT_VAL, bcmmsgbuf.h:38-39; dhd_msgbuf.c:1823), so item 0 has 1 and item 1 has 255 % 253 = 2 (dhd_msgbuf.c:12400-12402).
  request_id 0 (memset, dhd_msgbuf.c:12395). DS_ACK 0x2 (bcmpcie.h:485).
  Doorbell values: 0xDD000000 | idx<<16 | rd and 0xFF000000 | idx<<16 | wr (dhd_msgbuf.c:114, 116), with D2H ctrl complete idx 2 and H2D ctrl submit idx 0 (bcmpcie.h:179, 181). That gives 0xDD020001 and 0xFF000002.
  Slot positions: the D2H-RD slot is DHD_D2H_RING_OFFSET(2) = 0 (dhd_msgbuf.c:340-343, 11898-11900) and the H2D-WR slot is 0.
  The DD doorbell is rung only in the dma_h2d_ring_upd_support && !IDMA case (dhd_msgbuf.c:11688-11694). That case holds: fw flags 0x70050107 have DMA_INDEX 0x10000 set and IDMA 0x400000 clear (bcmpcie.h:63, 88; dhd_pcie.c:13828-13838).
  The golden has 790 doorbells of class 0xdd02xxxx (docs/research/92:121).
  Item word 0 = 0x02800023 in the header (sweet_wlan_d4b.h swd4b_item_mbdata) and in the glue (SW_G2_D4C_W0) [src patch 0174:355, 2540-2551].
- D2H decode [src]: d2h_mailbox_data_t = cmn_msg_hdr (8) + compl_msg_hdr (4) + u32 at +12 (bcmmsgbuf.h:900-907), which is r2[3]. D2H_DEV_DS_ENTER_REQ = 0x2 (bcmpcie.h:511).
- Gates [src]: the header has `x->d4b >= 3` (sweet_wlan_g2.h:1381) and the glue sets `hw.d4b = level >= 16 ? 3` (patch 0174:781). The glue list adds entries only under `h->d4b >= 3` (patch 0174:435). Levels above 16 are refused (patch 0174:751).
  Order: K2's first row needs the L14 doorbell bit (swg_d4_row, sweet_wlan_g2.h:1063). The glue chain is need = `last` of the L14 doorbell (patch 0174:436).
- Whitelist [src]: exactly 14 entries (1 + 1 + 10 + 1 + 1, patch 0174:436-447). Each has an exact value under vmask, `once`, a chain and the old pinned: the RD slot old 0, the item words old 0, the WR slot old 1. The doorbells have omask 0 and are never read (sw3_g2_ok, patch 0174:136-148).
  The header rows require the same old values (sweet_wlan_g2.h:1073). Store count: 73 = 59 + 14 [obs host test, wlan_g2_host_test.c "stores=73"].
- Undo [src]: a failed row calls swg_stop, which runs swg_undo (unmask first, then EP and RC). The refusal paths return 0, and swg_go then runs swg_undo (sweet_wlan_g2.h:1413-1418).
  Every refusal raises `swl_gap("d4c", ...)` plus a line (sweet_wlan_g2.h:1151-1152). The poll end raises a gap when nothing was seen and a gap when more ring-2 items arrived.
- Cache [src]: FlushPoC_DcacheRegion = dmb, a `dc civac` loop, dsb sy (xnu-pristine caches_asm.s:403-420). It is used because this build has no APPLE_ARM64_ARCH_FAMILY (patch 0004:226). Every host store is followed by a civac of its line (sw_g2_wr, patch 0174:651-656).
  Every host read is preceded by a civac (patch 0174:621-626). The dsb sy comes before the MMIO doorbell. Buffers are padded to 128 B (patch 0167:127, 271). Ring-0 items 0 and 1 share a line, but both are CPU-written and only device-read.
- Mutants [obs: own run, /tmp/rv-d4c/mut.py through g2_kill_test, 2026-10-29]: 7 of my 9 header mutants were killed (decode epoch, mb word index, K2 value, K1 slot value, double type gap, poll delay dropped, K-row failure returning 0). 2 survived (F5). Builder mutants exist (tools/test_wlan_wr_mutants.py:4088-4095).
- Shellcheck: the diff has no shell file (4 files: .h, .c, .py, .patch) [obs `git diff --name-only`].

## Findings

### F1 HIGH: the dongle runs inband device-wake (INB_DW), so DHD's unconditional-ack branch is the wrong model, and the code reads TCM after the ack with no device wake
- G2 stores hostcap 0x10823407 (SW_G2_HOSTCAP, patch 0174:342). It contains HOSTCAP_DS_NO_OOB_DW 0x1000 and HOSTCAP_DS_INBAND_DW 0x2000 (bcmpcie.h:445-446).
  DHD sets both bits only in its PCIE_INB_DW branch, together with `dhdpcie_bus_enab_pcie_dw(DEVICE_WAKE_INB)` (dhd_msgbuf.c:3903-3908). The firmware flags 0x70050107 also have PCIE_SHARED_INBAND_DS 0x40000000 set (bcmpcie.h:117; dhd_pcie.c:13790) [src + obs flags].
- So stock DHD takes the INB_DW state machine (dhd_pcie.c:12953-12990), not the "unconditionally" branch (dhd_pcie.c:13014-13017).
  In that machine DHD acks only when its state is DS_ACTIVE and host_active_cnt == 0. After the ack the state is DEV_SLEEP (dhdpcie_set_dongle_deepsleep, dhd_pcie.c:12780-12782).
  Any later dongle work first needs H2DMB_DS_DEVICE_WAKE_ASSERT 0x40 (bcmpcie.h:490, 504; dhd_pcie.c:12250-12290) and then the wait for DS_EXIT.
- After the K1 doorbell, swg_d4c polls TCM words: flags, mailbox data, tcm d2h_w[0..2] (swg_d4_read -> swg_pw_read, sweet_wlan_g2.h:944-950; poll at :1174-1200). After that, W17 re-halts the CA7 over the backplane.
  If the dongle has entered deep sleep, these accesses go to a sleeping backplane. Possible results are completion timeouts (all-ones reads, or an SError: [guess]) or a failed CA7 re-halt, which would leave the firmware running.
  Nothing is permanently damaged (it is reversible by reboot [guess]), but the result of the run cannot be interpreted, and rule 7 says O1/O2 must be settled first.
  The comments at sweet_wlan_g2.h:1101 and patch 0174:2503, and SPEC-WIFI K1, cite both branches without picking one. docs/research/W-D4c-ds-ack.md s.4 O1/O2 are still open.
- Fix (either):
  (a) Settle O1/O2 from golden W1 before the L16 run. Needed: the stock `ENABLING DW` line (dhd_pcie.c:15569), whether stock acks this first DS_ENTER_REQ during init, and what it writes after the ack (a 0x40 device-wake item or doorbell).
      Then follow stock. If stock asserts device_wake, add it as a reviewed row after K1.
  (b) Until then, change swg_d4c so that after the K1 doorbell it polls only host memory (h2dr, d2hw, ring-2 words) and PCIe-core +0xC30, with no TCM read.
      Print `WIFI GAP: d4c INB_DW on (hostcap 0x2000, fw flags 0x40000000): no device_wake after the DS_ACK, TCM not read` [high].
      State in the run plan that W17's CA7 re-halt may hit a deep-sleeping dongle and how the result will be read.
  Either way, correct the comment at sweet_wlan_g2.h:1101 / patch 0174:2152 and SPEC K1: the INB_DW branch applies.

### F2 MEDIUM: K2-then-K1 is the reverse of DHD's order; the "consume first" reason is a [guess] whose cite does not support it
- DHD's ctrl-complete loop calls `dhd_prot_process_msgtype` first. That call synchronously reaches dhd_prot_process_d2h_mb_data -> dhd_bus_handle_mb_data -> dhdpcie_send_mb_data -> dhd_prot_h2d_mbdata_send_ctrlmsg, which writes the item, the WR slot and the FF doorbell.
  Only after that does the loop call `dhd_prot_upd_read_idx` (RD slot + DD doorbell) (dhd_msgbuf.c:7524-7532, 12696-12704; dhd_pcie.c:13014-13017, 12788-12803) [src].
  So DHD's order is K1 then K2. The code does K2 first (sweet_wlan_g2.h:1159-1170; glue chain patch 0174:436-447).
  The research doc's reason, "DHD always consumes before handling, dhd_msgbuf.c:11659-11695" (W-D4c-ds-ack.md s.3), is a [guess]. The cited lines are only the body of upd_read_idx and say nothing about order.
- Fix: swap the order to follow DHD.
  Header: the enum (sweet_wlan_g2.h:155) becomes ... SWG_D4_DB0, SWG_D4C_ITEM0..SLOT, SWG_D4C_DBWR, SWG_D4C_RD, SWG_D4C_DBRD, SWG_D4_UNMASK. Run the K1 loop first and K2 last.
  Glue: chain the ten item words after the L14 doorbell, then the WR slot and the FF doorbell, then the RD slot and the DD doorbell (patch 0174:435-448).
  Host test: invert the strstr order check (wlan_g2_host_test.c:1978-1981) and the expected `c[]` record order. Mutants: invert 'd4c: K1 before K2 (order ...)' (tools/test_wlan_wr_mutants.py:3905-3906).
  Docs: correct W-D4c-ds-ack.md s.3 with the 7524-7532 cite.
  Alternatively, keep K2-first only with golden W1 evidence of the order of the first 0xdd020001 against the next 0xff00xxxx after DS_ENTER_REQ, and cite it.

### F3 LOW: the CTR_EL0 check refuses only lines larger than the pad, not lines smaller than XNU's fixed 64 B stride
- FlushPoC_DcacheRegion aligns down and steps by 1<<MMU_CLINE, and MMU_CLINE is 6 = 64 B (patch 0004:48; caches_asm.s:407-418). It does not use CTR_EL0.
  If DminLine were below 64 B, a 4 B maintenance at an offset of 32 or more inside a 64 B granule would civac the wrong line. The arm check is only `dline > SWD_PAD` (patch 0174:798). [Tensor G1 cores report 64 B: guess; the arm line prints it.]
- Fix: patch 0174:798 becomes `} else if (dline < (1u << 6) || dline > SWD_PAD) {`, with the gap text "D-cache line not in [64, 128] B (XNU stride 64 B, pad 128 B)". Add one host or object check, or a comment citing patch 0004:48.

### F4 LOW: the pre-answer read is not checked for trap, assert, FWHALT or a changed item 0 before the first store
- swg_d4c re-reads q (sweet_wlan_g2.h:1135-1149) but checks only all-ones and D2H-WR. The answer is built from the baseline item (`mb = b->r2[3]`, :1110), which is up to 19 polls old.
- Fix: after `swg_d4_ones(x, &q)`, stop with SWG_E_FATAL and a line, as the poll does at :1182-1191, if `q.pw.fl & (SWG_FLAGS_ASSERT|SWG_FLAGS_TRAP)` or `q.pw.mb & SWG_D2H_FWHALT`.
  Refuse with `no = "ring-2 item 0 changed since the baseline, not answered"` if `memcmp(q.r2, b->r2, sizeof q.r2) != 0`. Add one host case for each and one mutant for each.

### F5 LOW: two of the reviewer's mutants survive the host test
- [obs /tmp/rv-d4c/mut.py] Survivor 1: dropping the pre-answer `swg_d4_ones(x, &q)` stop (sweet_wlan_g2.h:1139-1141). It survives because an all-ones D2H-WR read is then refused only by `n > 1` with a gap: no stop, no SWG_E_DEAD, and the undo still runs.
  Fix: add a host case where the pre-answer D2H-WR read returns 0xFFFFFFFF. Expect a STOP dead link, 59 stores and restore ok. Add the mutant 'd4c: pre-answer dead link not checked'.
- Survivor 2: `ex = ((... d2hw ...) != 0 && ...)` replaced by `ex = (1 && ...)` (:1119). It survives because it is equivalent in effect: the later `n == 0` refusal covers it (AP-26). No fix, but say so in a comment if a mutant is ever added there.

### F6 CONDITION: level 16 stacks W15 and L14 below it, and neither is observed on hardware yet
- `level >= 16` implies the d4b post (L14) and W15 (cfg 0x88 |= 0x300) (patch 0174:781, 808). docs/HANDOFF.md:2165 and :2198 show the d4b14 run plan is still DRAFT, and there is no d4b15 run [obs grep].
  W-D4c-ds-ack.md s.5 asks for one variable per run.
- Fix (run order, no code): run L16 only after L14 and L15 each have a PASS result. The L16 run plan names both as preconditions.

### F7 INFO: the decode stays correct whatever the flags byte holds (efb0390)
- swg_d4c reads the type from in[0] and the epoch from in[3] (swd4b_d2h_check, sweet_wlan_d4b.h). It never looks at flags (in[2]) or if_id (in[1]), and no guard compares the whole w0.
  The XOR covers all six words, so a flags change only moves r2[5] (the fake does that, wlan_g2_host_test.c:548-550). The only whole-w0 compare is the test's expected log line `ring2 w 0x1800024 ...`, which is intended.
  The fake's "flags 0x80 only when type != 0" keeps the empty-slot case all zero, which the `no ring-2 item 0` refusal needs. No change needed.
