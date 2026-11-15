Re-launch brief (implementer, Sonnet; G2 t17 brief, abridged): rework W-D4c L16 in $WORKTREES/w4-d4c from e9b58ca (discard or reuse any uncommitted partial edits after reading git diff) to the stock INB_DW sequence:
A1 ack = ring-0 item 1 (mb 0x2, epoch 2), H2D-WR := 2 (old 1), DB0 0xFF000002; C1 = D2H-RD ring2 := 1 (old 0), DB0 0xDD020001; no TCM/0xC30/backplane access from A1 to end of E.
W = ring-0 item 2 (mb 0x40, epoch 3), H2D-WR := 3, DB0 0xFF000003, delay_ms(1) once.
E = host-memory-only poll (h2d_r0, D2H-WR, ring-2 item 1), all-ones = STOP, accept only D2H-WR==2 + item 1 type 0x24 epoch 2 xor ok mb exactly 0x4.
D (only on accepted note) = ring-0 item 3 (mb 0x20, epoch 4), H2D-WR := 4, DB0 0xFF000004; C2 = D2H-RD := 2 (old 1), DB0 0xDD020002; then ONE swg_d4_read with TRAP/ASSERT/FWHALT stops; line "SWEET WLAN: D4c DS handshake done (ack, wake, exit note, deassert)".
Misses loud, no deassert: "d4c no DS_EXIT_NOTE in N ms after the wake assert" [high]; return 0.
F4: pre-answer re-read q: TRAP/ASSERT/FWHALT stops + q.r2[0..5] == baseline item 0, else gap, no store.
One row per store with names ("d4c ack item w0".."w9", "d4c ack h2d_w slot 0", "d4c ack db0", "d4c rd1 ...", wake/deas/rd2), defines SWG_D4C_DB_ACK/RD1/WAKE/DEAS/RD2 with cites, SWG_D4_ISDB all 5 doorbells; rewrite comments :153/:1100.
Glue d4b>=3 whitelist chain = rows in order A1,C1,W,D,C2. Patch's bsd/dev/sweet_wlan_g2.h byte-equal to shim copy (test_wlan_wr_mutants.py:4329).
Counts 99 full / 85 no note. Host cases: full, no note, bits 0x6, wrong epoch, D2H-WR 3, dead link in E, F4 TRAP + changed item 0, no TCM read A1..E. Mutant per guard/value/order; AP-274 name diff vs e9b58ca.
Facts: docs/research/W-D4c-ds-ack.md s.6 (super), SPEC-WIFI K1 + D-13 rev t17, review docs/reviews/0174-w-d4c-hwwrite-r1.md (worktree).
Verify: python3 /tmp/g2t17/base.py $WORKTREES/w4-d4c /tmp/g2t17/ht2 -> RESULT: PASS. Commit by path, trailer "PixOS-Worker: G2".
