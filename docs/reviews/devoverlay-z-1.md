# Review devoverlay-z-1: patch 0154 (SWEETDEV v2, zlib entries), mkdevoverlay v2, test_devoverlay_kernel

Branch `tools/devoverlay-z` at 63c9847. Reviewer: Claude Opus 5.5, adversarial, 2026-10-26. Scope: `xnu/patches/0154-sweet-devoverlay-zlib.patch`
on 0117 `bsd/dev/sweet_devoverlay.c`, `tools/mkdevoverlay.py`, `tools/test_devoverlay_kernel.py`, `tools/test_mkdevoverlay.py`.
Line cites `0154:N` are lines of the patch file; `test:N` are lines of `tools/test_devoverlay_kernel.py` at 63c9847.

## Verdict: SAFE WITH CONDITIONS

The kernel code is correct as far as I could check it (no defect found in the bounds, the inflate loop, the allocator, the
verify-before-write order or restore mode). The conditions are test gaps (findings 1, 2), a cheap write-pass crc (finding 3)
and the build/hardware proof that does not exist yet (finding 5).

Conditions before the first phone run with a v2 archive: findings 1, 2, 3 fixed; finding 5 done (own-label `tools/xnu-local-build`).

## What was checked and how

- Host test (observed 2026-10-26): `python3 -I tools/test_devoverlay_kernel.py` → `Ran 8 tests in 159.923s` / `OK`, exit 0.
- Syntax-only compile of the patched file (observed): built `bsd/dev/sweet_devoverlay.c` = 0117 + `git apply` 0154 (the test's
  `full_source()`), compiled with the AP-67 recipe (`clang -target arm64-apple-macos14.0 -fsyntax-only ... -Wall`) against the
  read-only work tree `~/pixelios-artifacts/localbuild/work/usbroot-probe3`: no error, no warning. Control: renaming `inflateEnd`
  in a copy gave `error: call to undeclared function`, so the `ARM64_BOARD_CONFIG_SWEET` block is really compiled. 0154 also
  `git apply --check`s on that work tree's copy of the file. A full `tools/xnu-local-build` was NOT run (it would share the
  build machinery with other streams' labels; finding 5).
- Link symbols (observed): `llvm-nm-19` of that work tree's `kernel.development.sweet` lists `_inflate`, `_inflateInit_`,
  `_inflateEnd`; libkern zlib is `ZLIB_VERSION "1.2.3"` (`xnu/libkern/libkern/zlib.h:67`), built under `optional zlib`
  (`xnu/libkern/conf/files:76`), `inflateInit` is `inflateInit_(strm, ZLIB_VERSION, sizeof(z_stream))` (`zlib.h:1380-1381`),
  windowBits 15 → `wrap = 1`, zlib wrapper only, no gzip path (`xnu/libkern/zlib/inflate.c:193-200`).
- libkern 1.2.3 inflate vs the Python twin (observed): built `libkern/zlib/{inflate,inffast,inftrees,adler32,zutil,z_crc32}.c`
  from that work tree for the host (shims: `os/base.h` = `OS_FALLTHROUGH`, `machine/limits.h` = `<limits.h>`), linked the test's
  harness against them instead of system zlib 1.3.1 (ASan/UBSan): 0 mismatches over the full corpus (1823 archives) plus the
  extra items of findings 1/2.
- Extra mutants (observed, `/tmp/rv-dz/mut.py`, reviewer-only): see findings 1, 2, 6.
- Shellcheck: no shell file in the diff (4 files, all Python or patch).

Arithmetic, read from source (patched file):
- `sd_next` (0154:101-111): `e->size <= len - off` is checked first (0117, unchanged), so `rd64(e->data)` reads 8 bytes inside
  the entry once `size >= 8` (0154:102); `usize <= 40 MiB` (0154:106); `avail_in = (uInt)(size - 8) <= 48 MiB - 8 < 2^32`
  (0154:203). The final `off + size + 15` is unchanged from 0117 (off + size <= len).
- `sd_walk` (0154:148-153): each `usize <= 40 MiB` (0154:106) before the add and `total <= 40 MiB` after every add, so
  `total <= 80 MiB` at any point: no wrap.
- `sd_zalloc` (0154:163-164): `(uint64_t)items * size` with two `uInt` (32-bit) operands is < 2^64; `+16` cannot wrap;
  cap 16 MiB including the header. `sd_zfree` (0154:180-181) frees with the size stored at `p` = the size allocated.
  Real requests: `inflate_state` and a 32 KiB window (`inflate.c:189`, `:359`).
- `sd_rd_get` (0154:211): `n <= SD_CHUNK` and `n <= usize - off` (callers keep `off <= usize`); casts `(int)n`, `(off_t)off`
  are <= 1 MiB / 40 MiB.
- Inflate loop (0154:221-230): `Z_STREAM_END` before the chunk is full → EIO; any rc other than `Z_OK`/`Z_STREAM_END`
  (`Z_BUF_ERROR`, `Z_DATA_ERROR`, `Z_NEED_DICT`, `Z_MEM_ERROR`, `Z_STREAM_ERROR`) → EIO; no progress → EIO (never loops).
  Close (0154:239-247): exactly `usize` read, then one more `inflate` into a 1-byte window must return `Z_STREAM_END`, write
  nothing and leave `avail_in == 0` (no trailing bytes). An FDICT stream (`Z_NEED_DICT`) was tested and refused (observed).
- Order: `sd_verify` (crc over the inflated bytes + stream shape) runs in `sd_one` before `sd_same`/`sd_write_tmp`
  (0154:352); `sd_write_tmp` re-checks the stream shape in `sd_rd_close` BEFORE `vnode_setattr`/fsync/rename (0154:342),
  and an error removes the tmp (0117 `sd_one`, unchanged). Restore mode never inflates: `sd_verify` is behind `!restore`
  and the restore branch returns before `sd_same`/`sd_write_tmp` (patched file `sd_one`, restore block).
- v1: `sd_next` sets `usize = size`, `z = false`; `sd_verify` is the old crc; `sd_same`/`sd_write_tmp` copy through `buf`
  with `memcpy` (same bytes, same offsets); `sd_walk` adds nothing. The writer's v1 output is pinned by sha256 (test:320-329).
- Context: called once from `bsd_init` after `siginit`, before `bsd_utaskbootstrap` (0117:20-28), a thread that may block;
  `kalloc_data(..., Z_WAITOK)` and `inflate` take no lock. Stack: `struct sd_rd` (z_stream, 112 bytes on LP64 for 1.2.3,
  plus 16) in one frame at a time; `buf` is 2 MiB heap (0154:405), split verify/inflate scratch vs read buffer in `sd_same`.
- Paths/allowlist: no change to `sd_path_check`, `sd_names`, `sd_allow` or the parent checks (diff touches none of them).

## Findings

### 1. Medium (test gap): the "more output than declared" close check is an unkilled mutant
`0154:244` `r->zs.avail_out != 1 ||`. Mutant (delete it) SURVIVES the branch corpus (observed). Every declared-size lie in
`corpus_v2` (test:189-190, 201-203) keeps the crc of the FULL data (`z2`, test:177), so `crc == e->crc` fails first and masks the
stream check (AP-79). With a consistent liar (crc over the declared prefix) the mutant is killed 4/4 (observed).
**Fix:** in `corpus_v2` add
```python
rnd = bytes(R.randrange(256) for _ in range(5000))
out.append(h(1) + z2(p, rnd, decl=4999, crc=zlib.crc32(rnd[:4999])))       # one byte more than declared, crc of the prefix
out.append(h(1) + z2(p, b'Z', decl=0, crc=0))
out.append(h(1) + z2(p, b'abc' * 1000 + b'Z', decl=3000, crc=zlib.crc32(b'abc' * 1000)))
out.append(h(1) + z2(p, b'abc' * 1000, decl=2999, crc=zlib.crc32((b'abc' * 1000)[:2999])))
```
and in `MUTANTS` add `('v2 more output than declared', 'r->zs.avail_out != 1 ||', '')`.

### 2. Medium (test gap, wrong equivalence claim): the per-entry inflated cap is not equivalent to the sum cap
test:280 says the `e->usize > SD_MAXINF` mutant is equivalent. It is not: without it the u64 sum in `sd_walk` (0154:149) wraps,
e.g. usize 30 + usize 2^64-25 sums to 5. The mutant is killed by
`hdr(2, 2) + z2(b'/usr/lib/a', b'abc' * 10) + z2(b'/usr/lib/b', b'x', decl=2**64 - 25)` (observed: C accepts the archive,
Python refuses it). Kernel effect without the cap (read from source): `sd_write_tmp` would inflate up to the declared size, so a
<= 48 MiB bomb stream could write tens of GiB into userdata; the per-entry cap is a safety bound and must be pinned.
**Fix:** add that archive to `corpus_v2`, add `('v2 entry cap', 'e->usize > SD_MAXINF', '0')` to `MUTANTS`, delete the comment
at test:280 (and correct the matching claim in 0154:149's comment: "each <= 40 MiB" is what stops the wrap).

### 3. Low (defence in depth, AP-74): the write pass does not re-check the crc it was verified with
`0154:325-342` `sd_write_tmp` re-inflates and checks only the stream shape; the crc was checked over an earlier inflate
(`sd_verify`, 0154:254-271). Equal output relies on the archive memory not changing between the passes (true at bsd_init as
far as I can see, guess: no other writer of the memdev range before launchd) and on no bit flip. It costs one crc pass.
**Fix:** in `sd_write_tmp` keep `uint32_t crc = 0;`, after a successful `sd_rd_get(&r, buf, n)` do
`crc = sd_crc32_cont(crc, buf, n);`, and after `err = sd_rd_close(&r, err);` add `if (!err && crc != e->crc) { err = EIO; }`
(applies to v1 too; the rename is then never reached with bytes that were not crc-checked).

### 4. Low (test fidelity): host twin links system zlib 1.3.1, the kernel links libkern zlib 1.2.3
test:86 `libs = ['-lz']`. I showed agreement once (0 mismatches, 1823 archives, observed above), but nothing keeps it.
**Fix (optional):** when `XNU_TREE` (or an existing `~/pixelios-artifacts/localbuild/work/*/xnu`) is set, build the harness
against `libkern/zlib/{inflate,inffast,inftrees,adler32,zutil,z_crc32}.c` with two shim headers (`os/base.h`:
`#define OS_FALLTHROUGH __attribute__((fallthrough))`; `machine/limits.h`: `#include <limits.h>`) and `-I` the dir holding
`libkern/zlib.h` + `zconf.h` copied as `zlib.h`/`zconf.h`; skip when absent.

### 5. Condition (no proof yet): kernel build and device result
Commit message says "kernel build not run"; `sd_same`/`sd_write_tmp`/`sd_one` are after `host-test end` and never run on the
host. **Fix:** `tools/xnu-local-build --label devoverlay-z --stack xnu/patches` (AP-54, own label) before any phone run; the
first v2 run should carry one identical file (expect `skipped ... (identical)`), one changed file (`wrote`), one entry with a bad
crc (`refused ... (bad stream or crc mismatch)`), then a `sweetdev=restore` boot; tag results observed with date + build id.

### 6. Info: surviving mutants judged equivalent (reasoned, observed survival)
Survived the corpus and judged equivalent or host-untestable: `sd_rd_get` early-end check vs no-progress guard (each alone
refuses: after an early `Z_STREAM_END` the next call makes no progress); `sd_rd_get` argument check (callers never exceed it);
`r->off != r->e->usize` in close (loops always read exactly `usize`); `sd_zalloc` 16 MiB cap and `inflateInit` rc (calloc
never fails on the host; in the kernel a failed init leaves `state == NULL`, `inflate` returns `Z_STREAM_ERROR` → EIO);
`verify loop !err` (a failed inflate keeps failing). Matches the double-defence note at test:278-279.

### 7. Info: patch header still says "unreviewed"
0154:7. **Fix:** cite this review (`docs/reviews/devoverlay-z-1.md`, SAFE WITH CONDITIONS) once findings 1-3 are fixed.

## Round 2 (adversarial re-check of 621f465..86e1d6b, Sonnet 5.5, 2026-10-26)

Verdict: SAFE WITH CONDITIONS (one low-severity test gap; no safety defect found).

Verified (observed unless tagged):
- `git apply --check` of patch 0154 on the sweet_devoverlay.c that 0117 creates (same method as tools/test_devoverlay_kernel.py:56-70): rc=0, single file, hunk counts consistent. `python3 -I tools/test_devoverlay_kernel.py`: "Ran 8 tests ... OK".
- Write-pass crc (0154 hunk @@ -353,16 +514,27 @@): `crc` is a local `uint32_t = 0` per call, so it resets per entry; it is updated by `sd_crc32_cont(crc, buf, n)` on exactly the n bytes handed to vn_rdwr, before the write. `sd_crc32_cont` takes `~crc` in and returns `~c` (patch lines ~72-86; the same continuation sd_verify uses), 32-bit unsigned, no signed overflow. EIO is set after sd_rd_close and before setattr/fsync (read from source).
- Failure path (read from source, 0117 sd_one): mismatch -> sd_write_tmp closes vp and returns EIO -> sd_one has `moved == false` (the rename of target to .sweetorig only happens after sd_write_tmp succeeds), so no rollback needed, `sd_rm(tmp)` removes the temp, the old target is untouched, sd_nr++ and return. sd_rd_open failure leaves state NULL (memset), so sd_rd_close is safe.
- build-xnu-image.sh: `compress=""` default, passed to mkdevoverlay only as `${compress:+"$compress"}` (v1 output unchanged, golden sha test exists); `--compress` without `--overlay` exits 2 ("--compress needs --overlay", run). shellcheck -S warning: clean.

Findings:
1. LOW (test gap): the new write-pass check `if (!err && crc != e->crc) err = EIO;` is outside the host-test region, and nothing in tools/test_devoverlay_kernel.py references `crc != e->crc`, `sd_crc32_cont` in sd_write_tmp or sd_write_tmp (grep, observed). Deleting the check, or moving it after the rename path, survives all 8 tests. Fix: add a source-structure test that sd_write_tmp contains `crc = sd_crc32_cont(crc, buf, n)` before `vn_rdwr`, and `crc != e->crc` with `err = EIO` between `sd_rd_close` and `VATTR_INIT`; or a mutant list entry on the full source that is killed by that text check. Device proof stays pending.
2. LOW (test gap): tools/test_build_overlay_args.py has no case for `--compress` (refusal without --overlay, default passes no flag). Fix: add both cases (XNU_IMAGE_ARGS_ONLY hook runs after the refusal, so it works).
3. INFO: the check is redundant with sd_verify unless the archive RAM disk changes between passes (the only thing it guards); acceptable defence in depth.

Neither finding blocks a device run; both should be closed before the patch is called reviewed.
