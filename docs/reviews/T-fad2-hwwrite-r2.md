# T-fad2 HW-WRITE review r2 (`git diff 000869e4 8d276919`, final swg_e_event / swg_e_evflush / swg_e_post_ev)

Reviewer: Opus, adversarial, host only (no phone). Worktree at 8d276919. Every run was done in its own /tmp copy of
shim/out (runner /tmp/fad2r2/mut.py). The worktree was not edited except for this file and the brief's own copy into shim/out.

## Verdict: SAFE

Both HIGH findings of r1 are closed: each of the five mutants in the brief is now KILLED. The lookup fix is correct. The fake
dongle changes only add checks. The patch equals the shim. What is left is LOW: one STOP-path property that the source
guarantees but no test pins, and r1 LOW 3, which is still not applied. Neither one blocks the boot.

## Evidence (observed, 2026-11-03, host)
- Brief command: `RESULT: PASS wlan_g2_host_test`.
- Patch 0174 == shim: the build.sh:697/702 awk extracts to /tmp/fad2r2/pt, then cmp. `sweet_wlan_g2.h` and `sweet_wlan_g2e.h` are equal to
  shim/ and to shim/out. `sweet_wlan_d4b.h` exists only in the patch, and the patch copy equals shim/out.
- Mutants (gcc + run, each in /tmp/fad2r2/m_*):
  (a) `swg_e_post_ev(x, x->evp, x->evp % SWG_E_EV_BUFS)`: KILLED (test:1486 badpost, :1830, :1831)
  (b) `x->epid[sl] = 0u;` dropped: KILLED (:1486, :1778 EF_STALEID row)
  (c) `rid != 0u &&` dropped: KILLED (:1486, :1778 EF_ID0 row)
  (d) `x->evqb[x->evq] = 0u`: KILLED (33 checks, incl. :1486 and :1522)
  (e) frame read from slot `q % 8`: KILLED (:1829-1831)
  Extra: the old loop shape `rid != 0u && sl < ...` (the r1 bug: exit with sl=0): KILLED. epid not set on post: KILLED.
  Miss falls through to slot 0: KILLED. LIFO re-post order: KILLED. No evqb shift: KILLED.
  The miss STOP without its `return 0` (sl=0, goes on): SURVIVED, see finding 1.

## Answers
1. Lookup (read from source sweet_wlan_g2.h:2220-2228): the loop always runs to SWG_E_EV_BUFS unless
   `rid != 0 && epid[sl] == rid`. So rid 0 and any unposted rid both give sl == 8, then the SWG_E_STOP + `return 0` at :2225-2227. The STOP
   comes before :2229 (bp), the reads (:2235, :2255) and the consume (:2281-2284). The caller breaks on d4dstop (:2431-2433),
   and swg_e_ioctl returns at :2564-2566, before the swg_e_evflush at :2574. Nothing is consumed or posted. This is also observed:
   the new EF_STALEID / EF_ID0 rows end with `evn 1 evq 1` (E_DBG), meaning only event 1 was consumed.
2. HIGH 1 closed: the EF_STALEID (n 2, id 1 still unre-posted in the same batch) and EF_ID0 rows kill (b) and (c), and the committed
   rows tools/test_wlan_wr_mutants.py:4171-4172 now have needles that match exactly once. HIGH 2 closed: `ge.badpost`
   (wlan_g2_host_dongle.h:357-367) flags a buffer outside the block, a misaligned buffer, or a buffer that is still live
   (`evpa[evh..evn)`, slot 0 seeded live by ge_seed :73-80). It is checked on every scan_run (test:1486) and kills (a), (d) and LIFO order.
   The row for (a) is committed (:4176).
3. Additive (observed): the 000869e4 test.c + dongle.h, built against the current headers, PASS. With E_DBG, the 87 scenario state lines of
   old and new are identical. The only difference is the 2 new fault rows inserted after EF_PKTID. The new enums sit before EF_N (EF_N is not used
   elsewhere), so the old fault numbers are unchanged. `lastid` is only stored. `badpost` only counts. gd_arm zeroes ge (test:1264).
   The evlifo rotation is unchanged in this diff (it was reviewed in r1).
4. Patch hunks == shim headers (see Evidence).

## Findings

1. LOW - "Nothing consumed on a pktid STOP" is not pinned (sweet_wlan_g2.h:2227; test wlan_g2_host_test.c:1778-1779).
   If the `return 0` after the miss STOP is replaced by `sl = 0u;`, the mutant SURVIVES. It reads slot 0 and clears epid[0], evn++, evq++.
   For hardware writes it is equivalent today, because the caller stops on d4dstop before any flush (:2431, :2565). But a later caller
   that flushes after a soft stop would re-post a live slot.
   Fix (tested in /tmp/fad2r2/chk_*: PASS on base, kills this mutant with 3 FAILs): in the fc[] loop after test:1779 add
   `if (fc[k].fault == EF_PKTID || fc[k].fault == EF_STALEID || fc[k].fault == EF_ID0) { CHECK(x.evn == fc[k].n - 1u && x.evq == fc[k].n - 1u); }`
   and add the mutant as a row next to tools/test_wlan_wr_mutants.py:4173
   (`"		SWG_E_STOP(x, \"event pktid %#x is not a posted event buffer\", rid);\n		return 0;\n	}\n	bp"` -> same with `sl = 0u;` instead of `return 0;`).
2. LOW (r1 finding 3, still open) - there is no `_Static_assert(SWG_E_EV_BUFS == SWD_EVBUF_N, ...)` next to sweet_wlan_g2.h:107, and no `buf >= SWG_E_EV_BUFS`
   guard at the top of swg_e_post_ev (:2134). The fake dongle hard-codes `8u * SWD_EVBUF_LEN` (dongle:360). Fix as r1 finding 3, and in dongle:360 use
   SWD_EVBUF_N instead of 8u.
3. LOW (r1 finding 4, pre-existing) - the response and event pktid ranges are 3 apart under clm24. This diff does not change it.
