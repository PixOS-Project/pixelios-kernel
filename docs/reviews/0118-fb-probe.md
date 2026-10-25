# Review: 0118-sweet-fb-content-probe.patch (e85742f~1..e85742f)

Verdict: SAFE WITH CONDITIONS (one low-severity condition, no blocker).
Tags: [src] = read from source, [obs] = observed now.

## Verified
1. Bounds [src]: the probe sits inside `if (src != 0 && bpe == 4) { ... }`, after the copy loop, so it only runs when the `last <= getLength()` guard passed with w,h>0 and the clamped sx,sy. For gy,gx in 0..63: gy*h/64 <= floor(63h/64) <= h-1 and gx*w/64 <= w-1 (h,w >= 1). The highest byte read is offset + (sy+h-1)*bpr + (sx+w)*4 = `last`, which is the exclusive end the guard bounds. Reads stay inside the copied region; no read goes beyond what the memcpy already reads.
2. Overflow [src]: gy*h and gx*w are done in int64 (<= 63*2^31). Row term is cast to uint64 after sy+(int32)(...), and sy+idx <= sy+h-1 < s->height, so no int32 overflow and no negative value (sy>=0 after clamp). Multiplication by bytesPerRow is uint64. Same arithmetic shape as the `last` calculation. Nothing found.
3. Concurrency [src]: present() has one caller, vsyncFired (SweetFramebuffer.cpp ~l.336), running from one IOTimerEventSource, so sweet_fb_present_count is single-writer; it needs no atomic. Same pattern as the existing sweet_fb_swaps_logged. The surface can be written concurrently by the producer, which only makes nz/hash noisy, never unsafe. The probe holds the `s` reference taken by sweet_surface_copy (released after the block).
4. Log volume [src]: one IOLog per 100 increments. The counter increments once per enabled layer per present (not per present as the comment says), so at 60 Hz x N layers it is 0.6*N lines/s; bounded and constant. Cost: 4096 loads per 100 layer-presents, negligible.
5. Hardware writes [src]: none. The only stores are to locals (nz, hash) and the IOLog buffer. No MMIO, no write to the surface or to fPixels.
6. Apply [obs]: `tools/stack-apply --only-new 0118` -> `RESULT: PASS 0118-sweet-fb-content-probe.patch applies cleanly (whole stack: 118/118 patches ...)`.

## Findings
F1 (low, CR-20 alignment) SweetFramebuffer.cpp, probe line `uint32_t px = *(const uint32_t *)(row + ...)`: s->offset and s->bytesPerRow come from the client and their alignment is not checked; the copy path uses memcpy (alignment-agnostic) but the probe does a typed 4-byte dereference (unaligned access is UB in C; also faults if the mapping is Device memory rather than Normal). Fix: replace with `uint32_t px; memcpy(&px, row + (uint64_t)((int64_t)gx * w / 64) * 4, 4);` (the compiler emits the same load when aligned).
F2 (trivial) comment says "every 100th present" but the counter increments per layer; either fix the comment ("every 100th layer present") or increment once per present. Log text `content #%u` prints the layer-present index, not the present index.
F3 (trivial) the counter wraps at 2^32 (4294967296 % 100 != 0), skipping one interval after about 800 days at 60 Hz; ignorable.
