# T-fad2 HW-WRITE review r1 (commit 000869e4, event-buffer pktid lookup + slot re-post)

Reviewer: Opus, adversarial, host only (no phone). Scope: shim/sweet_wlan_g2.h, shim/sweet_wlan_g2e.h,
shim/wlan_g2_host_dongle.h, shim/wlan_g2_host_test.c at 000869e4; patch 0174 checked equal to the shim headers.

## Verdict: SAFE WITH CONDITIONS

I read the code from source and found no wrong hardware write: no double post, no unposted slot, no address outside
the evbuf block, no change to the ring-0 post count. The conditions are about tests. Two mutant rows that the commit adds
to tools/test_wlan_wr_mutants.py SURVIVE the host test, so the `--g2` mutant set cannot pass as committed. The one
host-written value that this commit changes (the buffer address in a re-post item) is not pinned by any test. Fix
findings 1 and 2 before the boot.

## Evidence run (observed, 2026-11-03, host)
- Patch == shim: the build.sh awk extraction to /tmp, then cmp: `sweet_wlan_g2.h` and `sweet_wlan_g2e.h` are identical to patch 0174.
- Brief command: `RESULT: PASS wlan_g2_host_test`.
- Mutants, each in its own /tmp dir (runner /tmp/fad2r/mut.sh, single process, worktree untouched):
  killed: LIFO re-post order, no evqb shift, frame read from the q%8 slot, epid[0]=0, epid not recorded on post,
  L21 posts all into slot 0, evqb stores slot 0, re-post id evp-8, FIFO-id fallback in lookup, unposted id -> slot 0.
  SURVIVED: (a) `x->epid[sl] = 0u` removed or `+= 0u` (= committed row test_wlan_wr_mutants.py:4172);
  (b) `rid != 0u &&` dropped (= committed row :4171); (c) the evbuf item address back to `n % SWG_E_EV_BUFS`
  (swg_e_post_ev, sweet_wlan_g2.h:2140); (d) `memset(x->epid, 0, ...)` removed (equivalent: the glue zero-inits `struct swg_wx g = { 0 }`, patch 0174:1345).
- Boot 8 log (observed): L21 posts ids 33..39 into buffers 1..7 (merged.log:984-1062); the escan completion has request_id 0x1e (merged.log:5666); the STOP is `event pktid 0x27, expected 0x1` (:5668).

## Answers to the review questions
1. Double post / unposted slot (read from source): the invariant is "(number of slots with epid != 0) + evq == 8".
   It starts as epid[0] = 1 (sweet_wlan_g2.h:2615) plus L21 slots 1..7 (sweet_wlan_g2e.h:17-22), with evq = 0
   (zero-init). A consume needs a match on epid != 0 (:2220-2228), clears it, and queues the slot (:2281-2284). A flush
   posts only evqb[0], a cleared slot that has not been re-posted, and sets epid at :2144. So a slot is never live twice,
   and evq <= 7 when evqb[evq] is written (:2282). The shift (:2163-2165) reads index k+1 <= old evq-1 <= 7.
   Failure paths: every STOP after the lookup (:2230-2252) and every swg_e_rdb error (:2235, :2255) returns before
   :2281, so nothing is consumed and nothing is posted. A failing post returns a negative value from swg_d4d_post
   (swg_stop, :1621-1646), with evq not decremented and the run stopped. Ring-0 budget: there is still exactly one
   swg_e_post_ev per consumed event, and the 7 L21 posts are unchanged, so the post count (and the SWG_E_MAX_CLM
   comment at :117) is unchanged. In FIFO order evqb[0] == evp % 8 (evp - 8 = re-posts done = index of the oldest
   consumed event), so the stores are byte-identical to before. The existing scenarios pass, which agrees.
2. Address range (read from source): buf is j in 1..7 (g2e.h:18) or evqb[0], and evqb only ever holds a matched sl < 8.
   evqb[0] is read only when evq > 0, after a write. The frame read uses the matched sl < 8 (:2229). The block has
   SWD_EVBUF_N(8) * 8192 B (sweet_wlan_dma.h:18, :108). No index can exceed 7 and none is read uninitialised.
3. Init (read from source): swg_e is the only entry (sweet_wlan_g2.h:2824, after L20). It sets epid before L21 and before
   any event is parsed (:2614-2615). swg_e requires lv > 20, so d4b >= 8 >= 2, so L14 posted id SWG_D4_REQID at
   x->in.evbuf, which is slot 0 (:2849). No WL_EVENT is consumed before swg_e: swg_d4d_scan treats one as a gap or stop
   (:1835), so slot 0 is still live with id 1 and epid[0] = 1 is correct. No path parses an event without passing L21,
   because L21 posts first (g2e.h:17-23) and only then waits.
4. Stale ids (read from source): an event id is 0x20 + n with n <= 8 + emax (at most 0x68 at level 23, 0x58 at 24).
   It never wraps, is never 0, and never equals 1. A stale or re-sent id misses (it was cleared at :2281) and gives a
   soft STOP, so no read from a re-posted buffer happens. That property is untested (finding 1).
   Response ids (observed): they reach 0x1e at the escan under clm24 (merged.log:5666), 3 below the first event id 0x21.
   They are separate tables, but see finding 4.
5. Store audit (read from source): the diff adds no store call. It changes only the arguments of the existing
   swg_d4d_post at :2142, which still goes through the swg_hoff-checked ring-0 path (:1627-1646). tools/lint_g2_stores.py
   lints run-log store lines and sees the same names and targets. Only the item word values (the address) change, and
   only in non-FIFO order.

## Findings

1. HIGH - Two committed mutant rows survive, so the `--g2` mutant set fails (tools/test_wlan_wr_mutants.py:4171-4172, test wlan_g2_host_test.c:1821-1828).
   The fake dongle never re-sends a consumed pktid and never sends pktid 0. The checks that stop a second read of a
   re-posted (live) buffer are therefore unpinned: the consume clear at sweet_wlan_g2.h:2281 and the `rid != 0u` guard
   at :2220.
   Fix: add two faults to wlan_g2_host_dongle.h. EF_PKTSTALE: event fault_n carries the pktid of the event before it
   (keep `ge.lastid` in ge_event). EF_PKTID0: event fault_n carries id 0. Deliver both in the same ring-2 batch as the
   previous event, so its slot is still empty (in L23 the flush runs after each batch), or deliver them in L21/L22,
   where nothing is re-posted. Add rows to the fc[] table (wlan_g2_host_test.c:1734ff) that expect
   `L23 STOP, no further store: event pktid 0x.. is not a posted event buffer` and `event pktid 0 is not a posted event buffer`.
   Confirm with the two rows plus my mutant (a).

2. HIGH - The re-post buffer address, the one hardware-written value this commit changes, is not pinned (sweet_wlan_g2.h:2140; dongle wlan_g2_host_dongle.h:345-349).
   Mutant (c), the address back to `x->in.evbuf + (n % 8) * LEN` with the printed `buffer %u` still `buf`, passes every check. In the evlifo run it posts slot 0's address (still live as id 1) under id 40 and leaves slot 7 unposted. That is exactly the double post this review was asked to rule out. The test only checks the printf text and x->epid, and the fake dongle believes any address.
   Fix: in gd_ring0 type 0x0D (wlan_g2_host_dongle.h:345), before appending, set `ge.badpost++` when the new pa is equal to any live `ge.evpa[ge.evh..ge.evn)`, is outside `[PA + off(SWD_EVBUF), + SWD_EVBUF_N*SWD_EVBUF_LEN)`, or is not a multiple of SWD_EVBUF_LEN from the base. Add `CHECK(ge.badpost == 0)` to scan_run, or after every scenario. In the evlifo scenario also check `ge.evpa[]` of the 4 re-posts against `PA + off + {7,6,5,4} * 8192`. Add mutant (c) as a row.

3. LOW - There is no compile-time check that SWG_E_EV_BUFS == SWD_EVBUF_N (sweet_wlan_g2.h:107; sweet_wlan_dma.h:18). The slot bound is now carried only by the evqb invariant.
   Fix: `_Static_assert(SWG_E_EV_BUFS == SWD_EVBUF_N, "one event slot per G1 event buffer");` next to :107. Also add a defensive
   `if (buf >= SWG_E_EV_BUFS) { x->fail = "d4e event slot out of range"; return swg_stop(x, SWG_E_ADDR); }` at the top of
   swg_e_post_ev (:2139), with a mutant that drops it, which is killed by a direct unit call with buf = 8.

4. LOW (pre-existing, not introduced) - The response and event pktid ranges are 3 apart under clm24. Response ids reach
   0x1e (observed merged.log:5666; SWG_E_RID_RE0 + c - 2, :2597) and event ids start at 0x21 (:109). DHD uses one pktid map
   for both pools. Three more L22 requests would make a response id numerically equal to a live event id. The host
   lookups stay separate (:2415 and :2220), but the dongle may not keep them apart [guess].
   Fix: `_Static_assert(SWG_E_RID_RE0 + <max L22/L23 requests> < SWG_E_EVID0 + 1u, ...)` or move SWG_E_EVID0 to 0x80. Moving it changes the FIFO store bytes, so it needs its own change and a re-pin.

5. LOW - The comment at swg_e_event (sweet_wlan_g2.h:2202) still says "pktid = the FIFO id".
   Fix: "pktid = the id of any posted event buffer (x->epid), the frame read from that slot".

6. LOW - Mutant (d) (no `memset(x->epid)`) survives because it is equivalent under the zero-init glue (patch 0174:1345) and the host tests' memset.
   Fix: either note it as EQUIVALENT in the row list, or keep the memset and add no row. No code change is needed.

## Driver follow-up (T-fad2, after r1)
- HIGH 1 and HIGH 2 fixed in the next commit: fake dongle faults EF_STALEID (previous pktid again) and EF_ID0 (pktid 0), `ge.badpost` (an event-buffer post outside the block, misaligned or while still posted) checked after every scan scenario, the four evlifo re-post addresses pinned (ids 40..43 in slots 7..4). [observed: host test PASS]
- The new EF_ID0 fault found a defect r1 had marked correct: with `rid == 0` the lookup loop condition `rid != 0u && sl < 8` failed at once and left `sl = 0`, so pktid 0 was accepted as slot 0 (and slot 0 re-posted twice). Fixed: the guard moved into the loop body, a miss always ends at `sl == SWG_E_EV_BUFS`. [observed: failing before, passing after]
- LOW 3 (static assert / buf guard) not added: `swg_e_post_ev` is called with `j < SWG_E_EV_BUFS` and `evqb[0] < SWG_E_EV_BUFS` by construction (lookup bound), pinned by the badpost check in every scenario. LOW 4 (3-id margin response/event pktids) predates the commit. LOW 5 comment fixed.
