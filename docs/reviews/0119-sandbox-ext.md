# Review of kernel a369067 (patch 0119, Sandbox policy ops 5/6)

Reviewer: Sonnet 5.5, adversarial, 2026-10-25. Tags: [src] read from source/disassembly, [obs] observed by running a command now.

## Findings

1. **BLOCKER (functional, patch does not work).** `xnu/patches/0119-sweet-sandbox-extension-ops.patch:23` (`a[5] < sizeof(SWEET_SBX_TOKEN) || a[5] > 0x800` -> EINVAL).
   a[5] is the 5th libsystem argument (x4, `sxtw x21,w21` stored at sp+0x30 = struct index 5, 0x1d1af4ff0-0x1d1af4ff4), NOT a buffer size.
   Both public wrappers pass 0: `_sandbox_extension_issue_file` `mov w4,#0` @0x1d1af5084, `_sandbox_extension_issue_generic` `mov w4,#0` @0x1d1af5464 [src, ipsw dyld disass, re-run now].
   The buffer is always `malloc(0x800)` (0x1d1af4fcc). So for tccd (both paths) a[5]==0 -> EINVAL -> NULL token -> the same "Failed to issue Sandbox Extension" (code 6) as before. The fix is inert, and a hardware run would be wasted. Research 44 section 1/4 and the patch comment ("out size") mislabel index 5 [guess presented as layout].
   Fix: delete the a[5] term; keep only the NULL check on a[4]:
   `if (a[4] == 0) { return EINVAL; }` and update the comment to "{class, type, path, flags, out buffer (always malloc(0x800) by the caller), x4, x5}". The constant token is 10 bytes, far below the caller's fixed 0x800, so the copyout needs no caller-supplied size.
2. **LOW (comment/doc).** Same comment block (patch line ~14) and `docs/research/44-tccd-photos-grant.md` section 4 call index 5 "out size". Correct both after fix 1.
3. **LOW (no test).** No host test covers the arg layout; a test feeding `a={..,buf,0,0}` would have caught finding 1. Fix: add a small host test (struct of 7 u64 with a[5]=0, expect rc 0 and "sweet-ext\0" in buf; a[4]=0 expect EINVAL) or at least one hardware label check. Mutation test not possible (no test exists); I verified instead against the disassembly.

## Requirement checks

1. Bounds/failure paths [src patch]: both copyins are fixed-size on stack (56 B / 24 B); copyin failure returns EFAULT before any write; single copyout each, no kernel state set (no globals, no refs taken), so no half-set state. Op 5 writes 10 bytes into a caller buffer that is 0x800 by construction (OK once finding 1 is fixed; the a[5] bound is wrong but not needed for safety of the write because the user pointer is copyout-checked by the kernel). Op 6 writes 8 bytes to a[2]; `_sandbox_extension_consume` reads `[sp+0x18]` as u64 handle (0x1d1af4f20), matches. Token pointer a[0] and length a[1] unused, harmless. PASS except finding 1.
2. Layouts: issue = 7 x u64 at sp+8 {x0 class, x1 type, x2 path, x3 flags, buf, x4, x5}: matches the 7 x u64 and a[4]=buffer [src 0x1d1af4fe0-0x1d1af4ffc]. Consume = {token@sp, strlen+1@sp+8, &handle@sp+0x10}: matches 3 x u64, a[2]=&handle [src 0x1d1af4ef4-0x1d1af4f08]. Handle 1 -> return value 1 (not -1): OK. Only the meaning of index 5 is wrong (finding 1).
3. Numbering: `tools/patch-numbers check` PASS (no overlap across 20 refs) [obs]; 0119 is on wip/gpu-accel only, 0120 is `0120-sweet-wlan-bwfm-core.patch` on wip/w3a-s34-skip (commit 07f1f58; wip/w3a-wlan-link has none) and touches bsd/conf/files, sweet_ufs.c, sweet_wlan*.c, not sweet_amfi.c: no clash. Stack apply: `git apply --check` of 0119 on the sweet_amfi.c from the built work tree `~/pixelios-artifacts/localbuild/work/main/xnu` (122 patches applied, 0119 not yet in it) passes [obs]. xnu-local-build silently skips non-applying patches (tools/xnu-local-build:544), so check `work/*/patches.txt` skipped list after the next build.
4. Other ops: the diff changes only `case 5` and `case 6` (previously fall-through to `return ENOENT` with 0x43). Ops 4, 0x43 and default are untouched [src patch lines 46-60]. PASS. Behaviour change: op 5/6 now return 0/errno instead of ENOENT for every process (permissive, as intended).

VERDICT: UNSAFE TO RUN as is (not hardware-dangerous, but finding 1 makes it inert); SAFE WITH CONDITIONS once finding 1 is applied (remove the a[5] check, rebuild, confirm 0119 not in patches.txt skipped list).
