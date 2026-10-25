# 35 - Why bluetoothd respawns every 10 s, why AegirPoster and assetsd abort (goal B-M5)

Role: researcher. Nothing touched a phone. Sources: existing runs `logs/runs/20261022T203316Z-demo2` (newest; 1955 s, cpus 8, GUI),
`...T195946Z-demo1b`, `...T182431Z-about2`. All cites are `usbnet.log` of the run unless stated; `L` = line number. Timestamps are the
epoch seconds in column 1. Tags: **observed** (read from a log / tool output), **source** (read from a file), **guess**.

## 1. Answer in short

1. `bluetoothd`: exec'd 174x in demo2 (L1014 first, L5804 last), spacing 10.1-10.2 s. **No `CRASH` line, ever** (0 in all 3 runs), so it
   does not die of a signal/exception; the console never shows its exit status. Spacing = launchd default restart throttle (10 s).
   Cause of the exit is unknown; best guess is a clean `exit()` because no Bluetooth controller exists. observed (pattern) / guess (cause).
2. `assetsd` and `AegirPoster`: both `sig 6` (SIGABRT) from `abort()` called by `std::terminate` -> `objc_terminate`: an **uncaught C++/ObjC
   exception** thrown inside a dispatch block. Identical top 8 frames in both (symbolicated, section 3). The exception text is not
   logged. observed (stack), guess (which exception).
3. Cheapest settling evidence: `StandardErrorPath`/`/dev/console` + `OS_ACTIVITY_DT_MODE=YES` for these 3 labels in the next root build
   (`tools/patch-launchd-plist.py` already does exactly that, section 5). No hardware needed to prepare it.

## 2. Per-process tally

### bluetoothd (no exit status logged)
| run | execs | first exec | last exec | CRASH lines |
|---|---|---|---|---|
| demo2 | 174 | 1792701324.897 (L1014, +5 s after the kernel banner at 701319.637 L1) | 1792703077.832 (L5804) | 0 |
| demo1b | 42 | 1792699311.121 (L752) | 1792699727.059 (L3999) | 0 |
| about2 | 37 | 1792693596.428 (L738) | 1792693961.627 (L3684) | 0 |

- Respawn interval in demo2 (L1441, L2043, L2105 and L2175-L2247 onward): 335.116, 345.228, 355.329, 365.539, 375.633, 385.726, 395.923 ...
  = 10.1-10.2 s each, always `exec xpcproxy` then `exec bluetoothd` (L2175-L2176). observed
- It starts in the very first launchd batch (L1014, same second as `identityservicesd`, `sharingd`, `backboardd`, L1008-L1016). observed
- Nearest preceding error line: none from bluetoothd. The only Bluetooth-named lines in the whole log are `exec bluetoothuserd` (L1025) and its
  two `SWEET AKS: bluetoothuserd sel 0 / sel 7 -> 0x0` (L1210-L1211); `bluetoothuserd` is not respawning. The only error lines near the first
  respawns are unrelated launchd noise (`Could not opt into 50 MB Jetsam high watermark`, L404). observed
- Hence the exit is silent: not a crash (no CRASH), not jetsam (no jetsam line), not logged. observed

### assetsd (SIGABRT)
| run | CRASH lines | exec lines | first CRASH | nearest preceding line |
|---|---|---|---|---|
| demo2 | 8 | 9 | 1792702414.858 pid 198 (L3503, +1095 s) | L3501-L3502 `SWEET AKS: assetsd sel 0 ... -> 0x0`, `sel 7 ... -> 0x0` (both succeed); L3497 `SWEET Sandbox: cloudd privilege 10001 denied` |
| demo1b | 4 | 5 | 1792699331.174 pid 234 (L2072, +23 s) | L2069-2070 same `AKS: assetsd sel 0 / sel 7 -> 0x0` |
| about2 | 4 | 5 | 1792693616.573 pid 235 (L2073, +23 s) | not read (same pattern, guess) |

- demo2: `Failed to send exception EXC_CORPSE_NOTIFY. error code: 5 for pid 198` (L3507) right after the crash, then an immediate `exec assetsd`
  (L3510): second abort pid 362 at L3518 (0.2 s later), third exec L3527. Later crashes L5494 (pid 522, +1582 s) last. observed
- A cluster of aborts at 2414.858-2415.060 also contains `activityawardsd[358]` sig 6 (L3482), same 8 top frames. observed
- Signal `sig 6 code 0x0`, `reason ns 2 code 0x6: (none)` (L3506): abort, no reason string. observed

### AegirPoster (SIGABRT)
| run | CRASH | exec | first CRASH | nearest preceding line |
|---|---|---|---|---|
| demo2 | 30 | 30 | 1792702629.908 pid 430 (L4568, +1310 s) | exec at L4533 (629.097), 0.8 s earlier; L4552 `|SpringBoard| IOServiceMatchingfailed for: AppleM2ScalerCSCDriver` (629.705) |
| demo1b | 0 | 0 | - | never started |
| about2 | 2 | 2 | 1792693957.079 pid 323 (L3627) | not read |

- demo2 loop: pid 430 (L4568), 431 (L4609, 0.5 s later) ... 435 (L4738, 639.404) ... 482 (L5004, last, 770.610). Each is respawned within
  ~0.1 s, so it is a tight KeepAlive loop for ~140 s, then stops (30th = L5004). observed
- Context at start: L4522-L4531 SpringBoard prints `UICollectionViewFlowLayout ... item width must be less than the width of the UICollectionView`
  (frame 153x153) just before `exec AegirPoster` (629.097); the lock screen/poster switcher was opening (poster extension hosts
  GradientPoster, Weather... `exec GradientPosterExtension` L3884-). observed
- Related but separate: the 8 poster extensions (GradientPosterEx, ExtragalacticPos, EmojiPosterExten, WeatherPoster, UnityPoster, PridePoster ...)
  die with `sig 5 code 0x600000001 pc 0x1c7f7c900` at 2616.653 (L3888+), 2x each (xrun report `CRASHES:`). That is the SIGTRAP signature
  docs/research/13 identified as the `NSAssert` `brk #0` in PosterBoard (docs/research/13-symbolicated-stalls.md:7-9). Same pc as the
  PosterBoard trap => same assertion family (pc equality observed; "same cause" is a guess).

## 3. Symbolication (tools/sweet-crash.py, run offline, observed)

`tools/sweet-crash.py CACHE ROOT [REPORT]` (tools/sweet-crash.py:1-20, usage line L4) needs `ipsw` on PATH (it is `~/pixelios-artifacts/tools/ipsw`, a
file, not a directory in PATH; I made a symlink dir `/tmp/ipswbin`), the cache `~/pixelios-artifacts/work/dsc/cache/dyld_shared_cache_arm64`
and a ROOT only for main-binary frames (not needed here; passed a dummy). Input: demo2 L3503-3506, L4568-4571, L3482-3485. Output, all three share:

```
___pthread_kill+8 <- _pthread_kill+208 <- _abort+124 <- (libc++abi) demangling_terminate_handler+300 <- _objc_terminate+124
  <- std::__terminate <- std::terminate <- _dispatch_client_callout+36 <- (frame 8..10 below)
```
- assetsd: `_dispatch_continuation_pop` <- `_dispatch_async_redirect_invoke` <- `_dispatch_root_queue_drain` (an async block on a root queue threw).
- AegirPoster: `_dispatch_block_invoke_direct` <- `___FBSSERIALQUEUE_IS_CALLING_OUT_TO_A_BLOCK__` <- `-[FBSSerialQueue _targetQueue_performNextIfPossible]`
  (a block on a FrontBoardServices serial queue threw).
- activityawardsd (bonus): `-[ACHActivityAwardingSource currentProgressQuantityForTemplate:]` inside `-[ACHAchievementProgressEngine populateProgressAndGoalForAchievement:]`
  block => same class of failure, a data-model/DB exception.
- The bt is cut at 10 frames, so the thrower frames (what `__cxa_throw` / `objc_exception_throw` came from) are not visible. The
  `far` register is just the pthread_kill argument, ignore it.

## 4. Hypotheses and settling evidence

| # | Claim | Tag | Cheapest evidence |
|---|---|---|---|
| H1 | assetsd/AegirPoster/activityawardsd abort from uncaught exceptions in async/serial-queue blocks | observed (stack) | already in hand |
| H2 | The exception is a missing-data/file/DB or entitlement error from the migrated, mostly empty data volume (assetsd = photo library DB, ACH = Fitness data, Aegir = poster data store), not a kernel bug | guess | `StandardErrorPath` + `OS_ACTIVITY_DT_MODE=YES` prints the reason ("*** Terminating app due to uncaught exception ...") |
| H3 | AegirPoster needs a GPU/Metal/media service (`IOServiceMatchingfailed for: AppleM2ScalerCSCDriver` L4552 is SpringBoard's, 0.1 s before the first abort); docs/research/22 suspects Astronomy/Metal | guess, unverified | same stderr line; or sampler on the AegirPoster pid |
| H4 | assetsd abort is triggered by a client (suggestd pid 197 thread watermark at L3509, cloudd sandbox deny L3497, migration plugins L3480-L3482) rather than by assetsd itself | guess | exception text again |
| H5 | bluetoothd exits with its own clean/`exit(1)` path because `AppleBluetooth*`/BT HCI IOKit services are absent (no BT in the ADT/kernel) | guess | its stderr, plus launchd exit status; see below |
| H6 | The 10.1 s spacing = launchd `ThrottleInterval` default 10 s (restart of a `KeepAlive` job), so the exit happens within <1 s of start | guess (default value not checked in this run's plist; the plist is not mounted here) | read `com.apple.bluetoothd` in `/System/Library/xpc/launchd.plist` (plistlib) for `KeepAlive`/`ThrottleInterval` |

## 5. Next evidence (one root build, per AP-05 batch with A6)

1. Build `launchd.plist` with `tools/patch-launchd-plist.py IN OUT com.apple.bluetoothd com.apple.assetsd com.apple.AegirPoster ...`
   (upstream/tools/patch-launchd-plist.py:1-17: stdout/stderr -> `/dev/console`, `OS_ACTIVITY_DT_MODE=YES`, `CFLOG_FORCE_STDERR=1`, renames `_PanicOnCrash`).
   The label for AegirPoster is an extension service, not a plain job: check its real label first (guess: appears under extensionkitservice
   via xpcproxy; `StandardErrorPath` may not apply). Fall back to the xnu `applog_name=` idea in docs/research/22:133.
2. The usbnet log must forward console text (check that `/dev/console` lines reach usbnet.log; the `|SpringBoard|` prefix lines show
   stderr already does for SpringBoard).
3. For bluetoothd the exit status is not on any console. Option: make launchd log it (xnu `proc_exit` line on exit for named processes)
   or read it from the sampler. A cheaper first step is the stderr text above: a clean `exit()` normally prints a reason.

## 6. Unknown (needs)

- bluetoothd exit status and reason (needs stderr/exit logging, step 5).
- The exception class/text for assetsd and AegirPoster (needs stderr, or full backtrace beyond frame 10: sweet-crash prints only 10 frames; the
  console report is capped there).
- Whether the "Aegir" in AegirPoster is Astronomy (unverified; research/22:134, 454).
- Why assetsd pid 198 ran ~56 s (exec L2164 at 358.063) before aborting at 2414.858 in demo2 but only ~23 s in demo1b/about2.
- Whether bluetoothd's `ThrottleInterval` is 10 (plist not mounted/read here).

## 7. Implementer steps (drafted, no code)

1. Extract `/System/Library/xpc/launchd.plist` from the current root with plistlib; list `com.apple.bluetoothd`, `com.apple.assetsd`,
   AegirPoster-related jobs (KeepAlive, ThrottleInterval, Label, MachServices); record into this note.
2. Add those labels to the root build's call of `patch-launchd-plist.py` (userland `tools/build-ios-root*.sh`, where it is invoked; grep first,
   my search found only the upstream copy referencing it).
3. Confirm console output reaches usbnet.log (`SWEET`-console to UDP path) with one short existing-run check.
4. Batch with the A6 migration-profile changes into one root rebuild + one `tools/fflash-userdata.sh` (AP-05).
5. After the run: `tools/xrun --dry-run --from-dir DIR`, grep stderr text for each of the three processes, update H2-H6 here with observed.
6. If exit reason points at BT hardware absence: do not add a throttle; disable the job (`Disabled` key) only if the exit is by design.
