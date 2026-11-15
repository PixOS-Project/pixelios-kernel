# Review: clean/usbroot-ro (912d433, b39b3c2, 8e524cf), adversarial

Reviewer: Sonnet 5.5, 2026-10-26. Scope: xnu/patches/0148, 0149, tools/usbroot-host.py, tools/test_usbroot_host.py, docs/research/44.
Tags: [src] read from source (cite), [obs] observed now on the host, [guess]. Line numbers for 0148 are lines of the patch file P148 (C file line = P148 line - 135).
Nothing was run on hardware; no code under review was edited. Mutation runs used copies in ~/pixelios-artifacts/scratch/mut-*.

## Verdict: SAFE WITH CONDITIONS

The read-only property and the C bounds hold on reading [src]. Conditions: renumber (F1), fix the gs101 check order (F2), make the host
tests able to fail on the C safety logic (F3), correct the "overlay may cover /private/var" claim (F4), catch send errors in the host tool (F5).
No finding is a hardware-write risk; F2 is the only one that touches registers, and only on a non-gs101 board.

## Findings

1. **MEDIUM, patch number collision.** `wip/gpu-accel` already holds `xnu/patches/0148-sweet-vsync-interval-stats.patch` [obs: `git ls-tree wip/gpu-accel xnu/patches/`;
   `git log wip/gpu-accel` ea7e4c1 "0148: vsync callback interval histogram"]. This branch adds `0148-sweet-usbroot-ro.patch` (and 0149). Merging both gives two 0148
   files; the stack order becomes alphabetical-by-name within the number and `tools/validate-repo.py:35-36` treats a number as held. Fix: renumber this pair to the
   next free numbers via the repo's claim/next tool (0150/0151 at the time of writing, check `tools/validate-repo.py` output), and update the names in
   `tools/test_usbroot_host.py` (PATCH path), `docs/research/44-usbroot-readonly.md` and the headers of both patches.

2. **MEDIUM, `gs101 only` is checked after the probe (register writes on other boards).** `sweet_usbroot_rootdev` calls `sweet_usb_probe()` (P148:654) and only then
   refuses with `!sweet_usb_is_gs101()` (P148:655-658). On a non-gs101 board (sweet/SM7150) the probe takes the Qualcomm path: GCC reads, `sweet_usb_gadget_start`, and
   the QSCRATCH writes `*(volatile uint32_t *)(qs + QSCRATCH_HS_PHY_CTRL) = ...` (applied tree bsd/dev/sweet_usb.c ~1098-1106) [src, stack-apply scratch copy]. The doc says the
   mode "refuses non-gs101"; the refusal comes after the hardware was touched. This is the same exposure `sweetusb=2` already has today, so no new capability, but the check
   is not a guard. Fix: test the board before probing, e.g. look up the `/sweet-usb` ADT node with the `GS101_COMPAT` compare (sweet_usb.c:962-965) through a new
   `sweet_usb_board_is_gs101()` that does no MMIO, call it before `sweet_usb_probe()`, return ENODEV first.

3. **MEDIUM, the host tests cannot fail on the C safety logic.** `PhoneModel` is a Python re-implementation of `ur_xfer`/`sweet_usbroot_rx`; the `Constants` tests are
   greps over the patch text. Mutations of a copy of the patch, run against the real test file [obs]:
   | Mutation of P148 | Result |
   |---|---|
   | `if (crc != ur_get32(h + 32))` -> `if (0)` (no CRC check) | RESULT: PASS (24 tests) |
   | drop `frag_len != MIN(UR_FRAG, ur.total - frag_off)` (fragment bounds) | PASS |
   | drop `ur_get32(h + 8) != ur.seq` (stale-reply check) | PASS |
   | strategy write `error = EROFS` -> `error = 0` | PASS |
   | `&& rootrw)` -> `&& 0)` (rootrw refusal) | PASS |
   | `total > UR_READ_MAX` -> huge | PASS |
   | open: `(flags & FWRITE) ? EROFS : 0` -> `0`; `*f = 0` -> `*f = 1` | FAIL (caught, by string greps) |
   So CRC-before-use, fragment bounds, sequence handling, the strategy write refusal and the rootrw refusal are unguarded by any test. Fix: add a host test that compiles
   the C function bodies (extract `sweet_usbroot_rx` and `ur_strategy` from the patch, stub the kernel types, build with `cc -fsanitize=address,undefined`) and drives them
   with the same fault cases; at minimum add `assertIn` greps for the five missing lines (crc compare, `frag_len != MIN(`, `!= ur.seq`, `error = EROFS;` before `goto done` in the
   `!(buf_flags(bp) & B_READ)` branch, `rootrw` + `return EROFS`). A grep is weak; the compiled test is the fix. Do not claim "tested" for the C until then (rule 5).

4. **HIGH (functional, not safety), a read-only root probably cannot boot iOS and the overlay does not rescue it.** research/44 says "the dev overlay (research/41, patch
   0117) may cover it, untested". Established the other way: 0117 refuses on a read-only root: `if (rv == NULLVP || vfs_isrdonly(vnode_mount(rv)))` ->
   "root is not mounted read-write (rootrw=1); nothing done" (0117 patch lines 581-582) [src], and 0031 states "/private/var lives on the root, so rootrw=1 mounts it
   read-write" (0031 patch line 9 hunk) [src]. 0148 refuses `rootrw=1` (P148:650-653). So with `rd=usbroot0` nothing makes /private/var writable; launchd, configd, notifyd
   etc. will get EROFS on first write. Fix: replace the sentence in docs/research/44 with the established fact, and either (a) keep the scope to "mount + read-only boot probe"
   and say so, or (b) design the writable layer first (memory-backed overlay for /private/var), keep the gate closed until it exists. Not a code defect in 0148.

5. **LOW-MEDIUM, host tool dies with a traceback and no RESULT line when the interface goes away or the TX queue is full.** `transport.send(r)` (tools/usbroot-host.py:195-196,
   `RawTransport.send` :173-174) is not wrapped; a phone reboot removes the usb interface (ENETDOWN/ENXIO) and a 16-frame burst on a full qdisc can give ENOBUFS [guess for the
   second; the first follows from AF_PACKET semantics]. `main` has only try/finally (:232-235), so the tool ends with a Python traceback and exit 1 without `RESULT:` (the repo's
   tool convention is a RESULT line, exit 0/1/2). Fix: in `serve` catch `OSError` around `transport.send`; on ENOBUFS retry once after 1 ms, otherwise drop the frame (the phone retries);
   in `main` catch the remaining `OSError` and `return result("ERROR", "send: %s" % e, 1)`. Add a test with a transport whose send raises.

6. **LOW, timing is unmeasured and the wait loop's real length depends on timer granularity.** `ur_xfer` waits by 2000 iterations of `ur_sleep_ms(1)` (P148:402-410), each
   `assert_wait_deadline` + `thread_block`; if the kernel timer rounds a 1 ms deadline up (e.g. 10 ms), a try lasts 20 s and four tries 80 s plus the 30 s link wait before the
   panic. Not a safety issue; it makes the failure path slow. The doc already tags it [guess]. Fix: compute the deadline once per try (`clock_interval_to_deadline(UR_WAIT_MS ...)`)
   and sleep in a loop until `mach_absolute_time() >= deadline`, not by counting iterations.

7. **LOW, `ur.bad_frames++` is outside the spinlock** (P148:346) and `ur.secsize` can change under a running `ur_strategy` through DKIOCSETBLOCKSIZE (P148:530-538, no lock); same
   pattern as sweet_ufs.c (0028 lines 411-418). Benign (counter; secsize validated 512..4096 power of two, no divide by zero, block count recomputed), listed for completeness.
   Fix if touched again: take `ur_lock` in the SETBLOCKSIZE case and `os_atomic_inc` the counter.

8. **LOW, zero-length-packet exposure is avoided only by an invariant.** A host frame whose length is a multiple of 512 (USB HS bulk max packet) needs a ZLP, otherwise the single RX
   TRB never completes (existing comment sweet_usb.c ud_tx_kick_locked, TX side only). Host reply frames are 14+36+payload; every READ fragment is 1024 (totals are multiples of
   4096, host usbroot-host.py:126, phone P148:675-677) -> 1074 bytes; INFO is 66; refusals 50 [src]. Never 512k, so no ZLP is needed today. If anyone relaxes the 4096 rule the
   last fragment can hit 14+36+462 = 512. Fix: a comment at `UR_READ_MIN` (P148:180) naming this dependency, and a host-test assertion that no reply length % 512 == 0.

9. **INFO (verified, no defect).** Items checked and found correct [src]:
   - Write paths: strategy refuses non-`B_READ` with EROFS before any other work (P148:475-478); `ur_open` returns EROFS for FWRITE (:456); `ur_write` EROFS (:593); DKIOCISWRITABLE=0,
     MAXBLOCKCOUNTWRITE=0 (:545-556); no DKIOCUNMAP/format ioctl (default ENOTTY :562); mmap/strategy of the cdev are `eno_*`. Nodes mode 0440.
   - No MMIO / UFS write added: the 0148 `+` lines contain no `volatile`, `ml_io_map`, `dwc_wr`, `ufs_*` (also asserted by test_phone_never_writes). The only hardware effect is the existing
     DWC3 TX kick through `sweet_usb_raw_tx` -> `ud_tx_kick_locked` (existing code path of ud_if_output). There is no UFS write path in the diff.
   - Bounds: header read only after `len >= 14+36` and the MAC check; `frag_len <= UR_FRAG` and `len-50 >= frag_len` before CRC (no unsigned underflow, P148:300-307); CRC over
     header[0..32)+payload before any state use (:309-312); fragment must be aligned, `< total`, `idx < 32`, exact expected length (:326-330), so `memcpy(ur.buf + frag_off, ..., frag_len)`
     stays inside `buf[16384]` since `total <= 16384` is enforced in `ur_xfer` (:368) and compared to the reply (:315). Duplicates ignored by the mask. Stale/late frames rejected by
     `state != EX_WAITING` and the per-attempt seq (never 0). Error replies must carry no payload.
   - LBA: `blkno < 0` and `blkno > nbytes/secsize` rejected, `off = blkno*secsize` cannot overflow, `count > nbytes - off` rejected (:479-487); window start is aligned down and
     `nbytes` is a multiple of 4096 so the window always covers `pos`; the copy loop always makes progress.
   - ud_rx_done interception: `len >= 14 && len <= UD_FRAME_MAX` before the call (also guards the TRB residual underflow); consumed frames re-arm the RX TRB and return; everything else
     falls through to the unchanged ifnet path; with `ur.active == false` the call returns false at the first test (P148:296). No mbuf is created or freed on the consumed path; the
     function does not sleep (spinlock only, memcpy <= 1 KiB, CRC outside the lock). Locks: `ur_spin` is never held across a sleep or a call out; `ur_lock` (mutex) is only taken in
     process context (strategy, rootdev); `ud.lock` is taken in `sweet_usb_raw_tx` and not held when `ur_spin` is taken.
   - Mutual exclusion: `sweet_ufs_init` has a single caller, `sweet_ufs_rootdev` (0028 line 572), so UFS init never runs unless `rd=ufsroot0` is selected; `sweet_ufs_started` is set after the
     exclusion check; `sweet_usbroot_rootdev` refuses when UFS started (EPERM), when `sweetusb != 2` (EINVAL), with `rootrw=1` (EROFS), non-gs101 (ENODEV, but see F2), link timeout
     (ETIMEDOUT), bad INFO (EINVAL); every error returns to the 0157 hook which `panic`s with the code, like the ufsroot0 branch (applied tree IOKitBSDInit.cpp:773-801). The hook
     compiles as C++ with the same `goto iofrootx` shape as the UFS branch. `sweet_usb_probe` is called once in this mode (0091's `usb_probed` lives inside `sweet_ufs_init`, which does not run).
   - Host server: image opened `rb` only (:79), size must be a non-zero multiple of 4096 and <= 64 GiB (:81), requests with wrong MAC, magic, version, reserved field, CRC or seq 0 are
     ignored, payload/offset in a request refused, range and length checked before the read (:126-130), short read becomes ST_IO. No write opcode. Own outgoing frames looped back by
     AF_PACKET are rejected by magic and count as `ignored` (cosmetic).
   - crc32: `crc32(crc, buf, len)` in bsd/libkern/crc32.c:93-105 is zlib-compatible (init/final xor ~0), so the chained call equals Python `zlib.crc32` [src]; it is declared in
     libkern/crc.h included from bsd/libkern/libkern.h:80.

## Item 6 of the brief: unverified facts

- EtherType 0x88B6: no occurrence in the 0148-excluded repo tree (`grep -rnai 88b6`), nor in the applied xnu tree (bsd, iokit, osfmk, libkern: only our own lines) [obs].
  `/usr/include/linux/if_ether.h:94` defines `ETH_P_802_EX1 0x88B5 "802.1 Local Experimental 1"`; it has no 0x88B6 entry [obs]. That 0x88B6 is the second IEEE 802 local experimental value is
  from memory, not checked against the IEEE/IANA registry offline [guess]; collision with anything on this host or in our tree: none found. Fix: cite the registry in the doc once looked up,
  or keep the [guess] tag (the doc does).
- Writable /private/var vs overlay 0117: established as F4 (overlay needs rootrw=1, usbroot refuses it).
- Non-UFS dependence of keys/touch/GPU/wlan/log in usbroot mode: not checked by the author; they hang off `sweet_ufs_init` (0087/0088/0102/0124 hunks at `sweet_ufs_init`), which does not run, so they
  will not start [src: grep of the patch stack]. This makes the mode a "boot to launchd" aid only.

## Item 7: context hunks vs the trunk stack

`tools/stack-apply --keep` on this branch: RESULT: PASS 135/135 patches (+ 3 stagers) apply cleanly in order [obs]. Branch base is trunk (accb1f1). Not tested against `wip/gpu-accel`
(its 0148 collides by number, F1; after renumbering, re-run `tools/stack-apply` on the merged tree because 0148 touches sweet_ufs.c and sweet_usb.c hunks that 0087/0088/0102/0124 also
touch near `sweet_ufs_init`).

## Shellcheck

No shell file in the diff (`git diff --name-only trunk...HEAD`: two patches, a python tool, a python test, one markdown); nothing to run.

## Verification

`python3 tools/test_usbroot_host.py` -> `RESULT: PASS 24 tests` [obs, run at the end of this review, unmodified code].
