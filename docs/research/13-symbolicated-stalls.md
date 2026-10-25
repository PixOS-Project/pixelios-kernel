# 13 - Symbolicated SpringBoard / backboardd stacks and the PosterBoard trap (qr-20261021T212513Z)

Goal: resolve the addresses in `logs/qr-20261021T212513Z.txt` (main checkout, gitignored) so we know what PosterBoard trapped on and what
SpringBoard and backboardd are blocked in. Tags: **observed** = read from the log or from the symbolicated cache/binaries, **source** = read from
the disassembly of the build-20H392 binaries (not from source code; there is none), **guess**. Nothing here touched a phone. No Apple binary, cache
or extracted file is in git; only addresses, symbol names and one-line mnemonics are quoted.

## 0. Answer in short

1. **PosterBoard trapped on an Objective-C assertion it raises itself**, not on Metal/GPU/IOSurface/a missing service: `brk #0` at
   `-[PBFPosterExtensionDataStore _stateLock_setupSwitcherConfigurationForFirstTime] + 2040` (PosterBoard.framework, unslid `0x1c1778268`),
   the failure block of `NSAssert(!_stateLock_switcherConfiguration)` ("Invalid condition not satisfying: !_stateLock_switcherConfiguration").
   Called from `-[PBFPosterExtensionDataStore prewarmWithCompletion:]` via XPC (`-[PRSServer prewarmWithCompletion:]`). observed + source
2. **SpringBoard's main thread is no longer blocked in a sync XPC call.** In this log it sits in the idle run loop
   (`-[UIApplication _run]` -> `GSEventRunModal` -> `CFRunLoopRunSpecific` -> `mach_msg`). The sync call of docs/research/11 (the same five
   frames) is now on a worker thread (t9 in samples 9/10; t19 in the older sample at the top of the file): `xpc_connection_send_message_with_reply_sync`
   <- `-[NSXPCConnection _sendInvocation:...]`. Its peer cannot be named: the sampler keeps only 8 frames (section 5). observed
3. **backboardd has no stuck main thread either** (main = `CFRunLoopRun`, `main+0x9e40`). The only non-idle-looking thread, t5, waits forever on a
   dispatch group in `-[BKButtonHapticsController _configureAsyncWithBlock:]` (haptic home button of the iPhone X ADT never reports ready).
   It only blocks its private serial queue `com.apple.backboardd.buttonHaptics`. observed + source; "benign" is a guess
4. Everything else in SpringBoard/backboardd is idle (workq, runloop). So the stall at the logo is not a busy loop or a deadlock between the two
   processes; both wait for something that does not arrive. The one hard failure in the window is the PosterBoard crash (iOS 16 draws the lock
   screen and wallpaper through PosterBoard scenes). guess (medium confidence)
5. **Recommended next fix:** make PosterBoard survive its first prewarm (section 4); and make the sampler show full stacks so the t9 peer
   is nameable (section 5).

## 1. Tooling and method

| Item | Value |
|---|---|
| ipsw | blacktop/ipsw **v3.1.739** (BuildCommit `1f8b657858d7510219f59bf9ba183bf4c0a59104`), release asset `ipsw_3.1.739_linux_x86_64.tar.gz` |
| tarball sha256 | `8124bdba7902e7627bae1bb2e1117c9cfcdc31de0f65ec55e341565ac6029a01` (equals the line in the release's `checksums.txt`; `checksums.txt.sig` cosign signature not checked) |
| extracted binary sha256 | `7f22f07821cd964c0333172ef9bac734b53df5035a5e7c8ce9ec1580891f6d4c` at `~/pixelios-artifacts/tools/ipsw` (outside git) |
| Shared cache | `dyld_shared_cache_arm64` + `.01`..`.42` + `.symbols` (3.3 GB) from the SystemOS cryptex `087-86593-047.dmg` (sha256 `9cd738f3...86849`), copied to `~/pixelios-artifacts/work/dsc/cache/`. Main file sha256 `76874e83...65aa` |
| How extracted | `apfs-fuse` as a normal user (`/dev/fuse` is mode 0666, no root needed), then unmounted; `ipsw` itself was not needed for extraction |
| Main binaries | `backboardd` (sha256 `7aa6f8bf...267c`) and `SpringBoard` from the OS root dmg `087-86698-044.dmg`, in `~/pixelios-artifacts/work/dsc/bin/` |
| Resolution | unslid = runtime - slide (slide `0x276f4000` for every process in the log); `ipsw dyld a2s [-i] CACHE ADDR`, `ipsw dyld a2f`, `ipsw dyld disass CACHE --vaddr A --count N`, `ipsw dyld dump`, `ipsw macho disass/a2s` for backboardd. `tools/sweet-crash.py` produces the same symbols (run on this log, ~2 min, one ipsw call per address) |

Caveats: the first 15 lines of the log are the tail of an older sample whose header scrolled out of the QR snapshot; I used the file's slide for
them (same boot). The QR snapshot cuts lines at 159 chars, so the last address of such a line may be partial and was dropped.

## 2. PosterBoard crash (observed)

`CRASH PosterBoard[375] sig 5 code 0x600000001 sub 0x1e8e6c268 slide 0x276f4000 mh 0x1022e8000`, `reason ns 2 code 0x5: (none)`.
`code 0x600000001` = EXC_BREAKPOINT / EXC_ARM_BREAKPOINT (guess from the encoding, consistent with the instruction below).

| Frame | Runtime | Unslid | Image :: symbol + offset |
|---|---|---|---|
| pc, lr (lr == pc) | `0x1e8e6c268` | `0x1c1778268` | PosterBoard :: `-[PBFPosterExtensionDataStore _stateLock_setupSwitcherConfigurationForFirstTime]` + 2040 |
| far | `0x1e8e6ba70` | `0x1c1777a70` | same function, entry (stale register, not a fault address) |
| bt0 | `0x1e8e72304` | `0x1c177e304` | PosterBoard :: `-[PBFPosterExtensionDataStore _stateLock_updateExtensions:refreshDescriptors:powerLogReason:galleryUpdateOptions:queuedUpOperations:]` + 5308 |
| bt1 | `0x1e8e58c5c` | `0x1c1764c5c` | PosterBoard :: `-[PBFPosterExtensionDataStore executeUpdate:refreshStrategy:galleryUpdateOptions:powerLogReason:completion:]` + 1508 |
| bt2 | `0x1e8e58644` | `0x1c1764644` | PosterBoard :: `-[PBFPosterExtensionDataStore prewarmWithCompletion:]` + 144 |
| bt3 | `0x1e8eda204` | `0x1c17e6204` | PosterBoard :: `-[PBFPosterExtensionDataStoreXPCServiceGlue server:prewarmWithCompletion:]` + 64 |
| bt4 | `0x1cadad318` | `0x1a36b9318` | PosterBoardServices :: `-[PRSServer prewarmWithCompletion:]` + 164 |
| bt5..bt9 | `0x1c30a772c` .. `0x1c30a6f1c` | `0x19b9b372c` .. `0x19b9b2f1c` | BoardServices :: `+[BSXPCServiceConnectionProxy invokeMethod:onTarget:withMessage:forConnection:]`, `BSXPCServiceConnectionEventHandler connection:handleMessage:` ... (incoming XPC message) |

What the trap is (source, from `ipsw dyld disass`):

- `0x1c1778264: bl <stub>` then `0x1c1778268: brk #0`. The stub targets, resolved with `ipsw dyld a2s -i`: `__bs_set_crash_log_message` (BaseBoard),
  `__os_log_error_impl` (libsystem_trace), `_objc_msgSend$UTF8String`, `_objc_msgSend$stringWithFormat:`. The oslog format right before it is
  `failure in %{public}@ of <%{public}@:%p> (%{public}@:%i) : %{public}@` (PosterBoard `__oslogstring`). The `brk` follows a noreturn crash-log call:
  this is PosterBoard's own assertion macro (an `NSAssert`-style `BSAssert`), not a hardware fault.
- Function entry: `ldr x8, [x0, #0x98]; cbnz x8, <failure block>` - i.e. it fails if the ivar at +0x98 is non-nil. The failure block builds
  `stringWithFormat:@"Invalid condition not satisfying: %@", @"!_stateLock_switcherConfiguration"` (CFStrings at `0x1e11644a0` -> `0x1c1869a08`, and the
  format at `0x1c1867317`). A second assertion in the same function checks the byte at +0xe9 (`!_stateLock_initialExtensionsLoaded`).
- The only writers of ivar +0x98 in the whole PosterBoard image (scanned in the full disassembly): the function itself (`0x1c1777ebc`) and the lazy
  getter `-[PBFPosterExtensionDataStore _stateLock_switcherConfiguration]` (`0x1c1778c3c`: if nil, `_stateLock_buildSwitcherConfigurationWithContext:outMutated:`
  and store). The getter is called from 15 call sites in client-request paths (`switcherConfiguration`, `fetchHomeScreenAssociatedPosterConfiguration...`,
  `_stateLock_fetchPosterSnapshots...`, `createPosterConfiguration...`, `importPosterConfiguration...`, ...), never from `_stateLock_updateExtensions` itself.
- The caller (`...updateExtensions...` at `0x1c177e2c4`): `if (!_stateLock_initialExtensionsLoaded) [self _stateLock_setupSwitcherConfigurationForFirstTime]; else
  [self _stateLock_transitionToNewSwitcherConfigurationAfterAddedExtensions:deletedExtensions:error:]`.

So at the first prewarm the in-memory switcher configuration was already non-nil: something read or built it before the first update ran (a client
request that reached the data store before `prewarm`, or state loaded from a previous run). Which one cannot be told from this log. guess

Also observed: after the crash `exec PosterBoard` appears again (launchd respawn). The snapshot hashes lines ignoring digits, so a second `CRASH
PosterBoard[<other pid>]` line would be dropped (docs/research/11 section 2.3): the crash count is unknown. guess: it crash-loops.

## 3. Blocked stacks (observed, final samples 9/10 unless noted; `tools/sweet-crash.py` output gives the same)

### SpringBoard (pid 26, mh `0x102ea8000`)

| Thread | Frames (leaf first), image :: symbol |
|---|---|
| t0 (main) | libsystem_kernel `_mach_msg2_trap`+8 `0x1c2611030` <- `_mach_msg2_internal`+76 <- `_mach_msg_overwrite`+484 <- `_mach_msg`+20 <- CoreFoundation `__CFRunLoopServiceMachPort`+156 `0x186e5c148` <- `__CFRunLoopRun`+1208 <- `CFRunLoopRunSpecific`+584 <- GraphicsServices `_GSEventRunModal`+160 `0x1bef35998` <- UIKitCore `-[UIApplication _run]`+868 `0x1890f434c` (frames beyond 8 not sampled) |
| t9 (older sample: t19) | `_mach_msg2_trap` <- `_mach_msg2_internal` <- `_mach_msg_overwrite` <- `_mach_msg` <- libdispatch `_dispatch_mach_send_and_wait_for_reply`+532 `0x18db6a308` <- `_dispatch_mach_send_with_result_and_wait_for_reply`+56 `0x18db6a694` <- libxpc `_xpc_connection_send_message_with_reply_sync`+236 `0x1d1bee0ec` <- Foundation `___NSXPCCONNECTION_IS_WAITING_FOR_A_SYNCHRONOUS_REPLY__`+12 `0x18120c224` <- Foundation `-[NSXPCConnection _sendInvocation:orArguments:count:methodSignature:selector:withProxy:]`+2024 `0x1811a5cb0` (stack ends at the 8-frame limit; unchanged in samples 9, 10, 12) |
| t16 (older sample only) | `_mach_msg` <- libxpc `_xpc_pipe_routine`+352 <- `_xpc_interface_routine`+180 <- `_xpc_look_up_endpoint`+264 <- `_xpc_connection_bootstrap_look_up_slow`+284: a bootstrap lookup waiting for launchd's reply; gone in samples 9/10 |
| t4 (older sample only) | libsystem_kernel `___psynch_cvwait` <- SpotlightUI `_SPFastApplicationsGetWithWait`+80 <- `___22-[SPUIAppService init]_block_invoke`+88 (waits for the app list; transient) |
| t1 (older sample only) | libdispatch `_dispatch_kq_poll` <- CoreAnalytics `____ZN13CoreAnalytics6Client14initConnectionEv_block_invoke`+120 <- libxpc `_xpc_connection_call_event_handler` (CoreAnalytics connecting; transient) |
| t3, t6, t11 | NSRunLoop `runMode:beforeDate:` run loops (idle); t5/t12/t1/t2 `___workq_kernreturn` (idle pool); t7 CoreMotion/CommonUtilities `CFRunLoopRun`; t8 `-[UIViewInProcessAnimationManager startAdvancingAnimationManager:]` semaphore (idle) |

The SpringBoard t6 and t12 stacks of the older sample (LaunchServices `BindingEvaluation`, `SecTaskLoadEntitlements`) are running code, i.e. SpringBoard was still working then.

### backboardd (pid 56, mh `0x102b9c000`; vmaddr = address - mh + 0x100000000)

| Thread | Frames |
|---|---|
| t0 (main) | `_mach_msg` <- CoreFoundation `__CFRunLoopServiceMachPort`/`__CFRunLoopRun`/`CFRunLoopRunSpecific` <- backboardd `0x102ba5e40` = vmaddr `0x100009e40` (inside `func_100009664`, `main`'s body; stripped binary) <- dyld `start`+1860 `0x1a4624344`. Idle run loop |
| t5 | libsystem_kernel `___ulock_wait`+8 <- libdispatch `__dlock_wait`+52 <- `__dispatch_group_wait_slow`+52 `0x18db684a0` <- **backboardd `0x102bb4a78` = vmaddr `0x100018a78`** <- `_dispatch_call_block_and_release` <- ... `_dispatch_workloop_worker_thread`. The block at `0x100018a58` is created in `-[BKButtonHapticsController _configureAsyncWithBlock:]` (`adr x8, 0x100018a58` at `0x100016948`); its body is `dispatch_group_wait(self->_group, DISPATCH_TIME_FOREVER)` then calls the user block (source). The group is created and entered in `-[BKButtonHapticsController initWithBHButtonHapticsController:]` (queue label `com.apple.backboardd.buttonHaptics`) and left only by the ready-notification block (`dispatch_group_leave` at `0x100018abc`), registered with `-[BHButtonHapticsController scheduleReadyNotificationOnDispatchQueue:withBlock:]`. `_configureAsyncWithBlock:` is used when `-[BSPlatform homeButtonType] == 2` (ADT `/buttons home-button-type 2`, `tools/mkadt.py:218-224`) |
| t1 | IOKit `___IOHIDServiceRunLoopCompatibilityThread`+276 in `CFRunLoopRun` (idle) |
| t3 | QuartzCore `CA::Render::Server::server_thread`+552 in `mach_msg` (idle) |
| t4, t6, t7, t8 | QuartzCore `CA::WindowServer::IOMFBServer::thread_body`+428 in `CFRunLoopRun` (idle, 4 threads) |
| t9 | CoreMotion run-loop thread (idle) |
| t11 | libdispatch `_dispatch_worker_thread` timed semaphore (idle pool) |
| t2, t12 | `___workq_kernreturn` (idle pool) |

backboardd t10 (named in the request) exists only in `qr-20261021T213452Z.txt` sample 12, where it is `___workq_kernreturn` (idle); it is not in 212513. The sync-XPC
worker of docs/research/11 (old backboardd t2/t10/t14) is not present in these samples. The log `qr-20261021T213452Z.txt` shows the same state in sample 12 (same slide).

## 4. Interpretation

- **PosterBoard** (observed + source): an invariant of its own data store is violated at the very first `prewarm`. It is not a service-lookup failure, not Metal, not IOSurface.
  The process is respawned by launchd (guess: it crash-loops). SpringBoard's PRSService client talks to this service.
- **SpringBoard main thread** (observed): idle in the run loop, so SpringBoard finished `SBSystemAppMain` and now waits for events. The earlier "main
  thread blocked in `-[BKSSystemShellService _checkIn]`" reading of docs/research/11 does not hold for this run: the call is on t9, not on main, and goes through Foundation's
  `NSXPCConnection` (the frames show `-[NSXPCConnection _sendInvocation:...]`; the BaseBoard `BSXPCServiceConnectionProxy` transport seen in the PosterBoard stack is a different class). Why main moved on versus
  the 205516 run is unknown; guess: the kernel of this run includes patch 0086 (`xnu-0086*` builds exist from 23:09), which changes IOKit quiescence; not verified.
- **backboardd** (observed): nothing blocks it except the haptics queue. The same ADT is used on sweet (which reached SpringBoard), so the haptics wait is probably benign. guess
- **What is the logo waiting for?** guess (medium): backboardd's boot logo is dropped when SpringBoard presents its first scene. On iOS 16 that is the lock screen,
  rendered through PosterBoard scenes; with PosterBoard dead, no poster scene exists. Counter-evidence: SpringBoard t9's sync NSXPC call to an unknown peer could equally gate it; it is the only
  blocked call left in both processes.

**Single most likely next fix: get PosterBoard through its first `prewarm`.** Evidence: it is the only hard failure in the window, it sits on the code path SpringBoard
uses for the lock-screen/home-screen wallpaper, and the assertion names its cause (a switcher configuration already in memory before the first update).
Cheapest steps, in order:
1. Host-only: look in the flashed root (`<tank>/pixelios/ios-root.img` with the `tools/check-ios-root.py` HFS reader) for a PosterBoard data container
   (strings in the image: `PRBPosterExtensionDataStore`, `SwitcherConfigurationOrdering.plist`, `Extensions`, `GalleryCache`); if one exists, the pulled upstream root had it created by a real data migration. guess: the path under `/var/mobile/Library` is not verified.
2. On the next flash, ship the root without any PosterBoard container (fresh), and capture the first PosterBoard crash with the sampler change below (the dedupe hides the second crash).
3. If it still asserts: the cause is call order (a client request reaching the store before `prewarm`); then the fix is to delay SpringBoard's first poster request, which is a userland
   change we have no precedent for - ask whether to patch PosterBoard or accept it.

## 5. Tooling findings

- `tools/sweet-crash.py` had one bug that blocked this: lines above the first `slide .. mh ..` header (the QR snapshot scrolls headers out) used slide 0 and resolved
  to wrong cache addresses (`AppStoreOverlays`, `HealthPlatformCore` ...). Fixed: the first header's slide is the default; the last address of a 159-char line is dropped (partial). Nothing else changed.
- `xnu/patches/0054` samples at most **8 frames** per thread (`i < 8`) and `0083` cuts a log line at 159 chars: t9's caller of the NSXPC call is not recoverable from any QR log.
  Raising the depth to ~24 and printing the frames over several `SMP ... bt+N` lines of <= 150 chars each would name the peer; add the pid to the dedupe hash or keep CRASH lines out of it so repeated crashes show.
  Not done here (kernel patch change, needs a CI run).
- The unslid cache addresses in docs/research/11 section 5 match this log (`0x18db6a308`, `0x18db6a694`, `0x1d1bee0ec`, `0x18120c224`, `0x1811a5cb0` = the t9 frames above).
