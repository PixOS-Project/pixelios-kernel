# 11 - Why SpringBoard never presents its UI on bluejay (Apple logo forever)

Question: the same XNU + iPhone X iOS 16.7 userland reached the SpringBoard home screen on the upstream Redmi Note 10 Pro ("sweet"). On the
Pixel 6a ("bluejay") launchd runs ~60 daemons, backboardd draws the Apple logo, and nothing more happens for 4-10 minutes.
Tags: **[source file:line]** = read from source, **[observed log]** = seen in a log/file named here, **[guess]**. Nothing here was run on the
phone (no adb/fastboot; read-only host work). Files: `logs/qr-20261021T201541Z.txt` (`--gui --qr 200`), `logs/qr-20261021T205516Z.txt`
(`--gui --qr 600 --restart 720`, kernel xnu-0085restart), `logs/qr-20261021T194623Z.txt` (first boot of the image, `--qr 40`, no `--gui`);
`logs/` lives in the main checkout (`<repo>/logs`), not in git. Shared-cache symbolication was **not** done (no `ipsw`
here, the cache is inside an APFS dmg); the unslid addresses are listed in section 5 so the driver can resolve them.

## 0. Answer in short

1. The stall is **not** the display, HID, battery or the panel path. SpringBoard's main thread is blocked in a synchronous XPC call
   (`xpc_connection_send_message_with_reply_sync` through a BaseBoard `BSXPCServiceConnectionProxy`, [guess] = `-[BKSSystemShellService _checkIn]`
   to backboardd), and so is one backboardd worker thread. The stacks are byte-identical (after removing the slide) at ~90 s, ~210 s and in
   both runs. Nothing has been added to the display since the first `SWEET FB: swap`. [observed log 205516, 201541]
2. The upstream history says exactly this state is the data-migration gate (NOTES.md:371, 369), but our image already carries the
   migration-skip fixes, no migration progress UI was ever presented and `migrationpluginwrapper` was never exec'd. So "migration is
   running" fits the evidence badly. What differs from upstream is that **our root has never completed a real first boot**, and we run on
   **one CPU** (the upstream README timings are for `cpus=6`). Those two, plus an unidentified peer of the sync XPC call, are the candidates.
3. The most valuable thing to do first costs nothing: **the complete, untruncated console log of the 600 s run is on the phone**
   (patch 0047 keeps a 4 MiB ring in the last 4 MiB of `userdata` and flushes it about once a second), and the QR text is a lossy view of it
   (section 2.3). Pull it from recovery before changing anything (experiment E0).

## 1. Differences between the upstream sweet boot that reached SpringBoard and ours

### 1.1 Image recipe

| # | Item | Upstream sweet | Ours | Tag |
|---|---|---|---|---|
| I1 | `tools/sweet-root-tweaks.py` | upstream copy | **byte-identical** (`diff upstream/tools/sweet-root-tweaks.py tools/sweet-root-tweaks.py` is empty). LastSystemVersion + DMUserDataDisposition at `tools/sweet-root-tweaks.py:133-135`, containers `:74`, launchd `:57`, VirtualAudio `:110`, springboard/purplebuddy prefs below | source |
| I2 | `tools/mkiosroot.sh` | upstream copy | same steps; only the host-tool preflight and the apfs-fuse path differ (diff) | source |
| I3 | Contents of `com.apple.migration.plist` in the image we flashed | v12: plain plist with `DMUserDataDisposition` written **by datamigrator itself** + `LastSystemVersion=20H392` (NOTES.md:365) | `{DMUserDataDisposition: {SyntheticDidUpgrade: False, buildVersion: 20H392}, LastSystemVersion: 20H392}` (read from `<tank>/pixelios/ios-root.img`, the flashed image, sha256 9fee6b0e..., with `tools/check-ios-root.py`'s HFS reader). The disposition dict is a **hand-written guess** of datamigrator's own structure | observed (file), guess (structure) |
| I4 | springboard / purplebuddy prefs | same | same: `BKNoWatchdogs`, `FBLaunchWatchdogExceptions`, `SetupDone`, `SetupFinishedAllSteps`, `SetupVersion 11` (read from the image) | observed (file) |
| **I5** | **History of the root** | v10 = "the pulled phone root", i.e. the root **after a real DataMigration had completed once** (boot 0053s, ~60 min) + fsck; v11/v12 = that + prefs (NOTES.md:369, 373). The home-screen image v14 sits on that lineage | **fresh** `mkiosroot.sh` output + tweaks, flashed once; `LastSystemVersion` is set from the very first boot so **no migration ever ran on it**. `tools/check-ios-root.py` never checks any migration state | source (NOTES.md, tools), guess (consequence) |
| I6 | v15 extras | `preferences.plist` with a "USB Ethernet" service (NOTES.md:298) | not present; irrelevant for the GUI | source |
| I7 | State after boot 1 | n/a | the image is read-write mounted (`rootrw=1`); boot 1 (`194623`) ended by a reset (or hard power-off), later boots (201541, 205516) run on the mutated root. The host file is the pre-boot image, so the phone's root now differs from what I inspected | observed (reset), guess (effect) |

### 1.2 ADT (`tools/mkadt.py`, dumped from `~/pixelios-artifacts/xnu-0085-root/A.adt` with `tools/adt.py`)

| # | Item | Result |
|---|---|---|
| A1 | `/chosen/unique-chip-id`, `chip-id` | present (`mkadt.py:111-112`, ADT dump: `01 00 00 54 45 45 57 53`, `0x8015`), so IOPlatformUUID exists as in upstream (NOTES.md:367). observed (ADT dump) |
| A2 | `/chosen/display-scale` = 3 | present (`mkadt.py:105`). observed (ADT dump) |
| A3 | `/product` (iPhone X) | present in this ADT (`mkadt.py:227-228`), 60+ properties. observed (ADT dump) |
| A4 | `/buttons` `home-button-type` 2 | present (`mkadt.py:218-224`). observed |
| A5 | Security status props, `/chosen/crypto-hash-method`, `boot-manifest-hash`, `/defaults/no-effaceable-storage` | present, same as upstream. source |
| A6 | `compatible` still contains `xiaomi,sweet` (SweetPlatformExpert match) | yes (`mkadt.py:255`); kernel log shows `SWEET: platform expert registered`. observed (qr-20261021T192657Z.txt:61) |
| A7 | **Differences that are real**: timebase 24,576,000 Hz (sweet 19,200,000, `soc/google/gs101/soc.json` vs `mkadt.py:32`); 8 CPU nodes with gs101 MPIDRs 0x0..0x700 (sweet `core<<8`); RAM 4 GiB at 0x880000000 (sweet highmem 0xfcc00000 at 0x100000000, similar size); `sweet-ufs` is `google,gs101-ufs` | source. None of these is a plausible SpringBoard blocker: wall-clock vs kernel-clock match (QR takeover at +602 s for `sweetqr=600`, HANDOFF.md:368 gives boot 22:42:48 and takeover ~22:52:50), memory is the same order |

### 1.3 Boot-args (`tools/build-xnu-image.sh:18-23`) vs upstream README (`UPSTREAM_README.md:105,109-119`)

| Arg | Upstream | Ours | Note |
|---|---|---|---|
| `cpus` | **6** (README); `cpus=1` "the safe fallback" | **1** | the README's "about 4 minutes on the Apple logo" (`UPSTREAM_README.md:34,129`) is for 6 cores; upstream's own early boots at 1 CPU: "Every migration run takes 1+ hour on cpus=1" (NOTES.md:369). SMP on gs101 is untried (`docs/PLAN.md:34` "Boot `cpus=1`"; `docs/research/05-fact-check.md:306-312` first PSCI CPU_ON caveat) |
| `sweetfb_noconsole=1 sweetfb_hz=30` | yes | yes with `--gui` (run 1 `194623` had neither) | |
| `ufsrw=1 rootrw=1` | yes | yes with `--rw` | |
| `sweetusb=0` | absent | present | USB probe off, unrelated to the GUI |
| `sweetkeys=0 sweettouch=0` | listed as optional switches (`UPSTREAM_README.md:119`) | both 0 | see 1.4 |
| `sweetqr=N` | n/a | present in the runs read | QR takeover mutes the console at N s |

### 1.4 SWEET-conditional kernel code that bluejay switches off, and HID

- `sweetkeys=0` / `sweettouch=0` only skip the **hardware drivers** (SPMI keys, GENI touch): `xnu/patches/0060-sweet-keys-touch-drivers.patch:169-171,775-778`.
  The HID *services* are not part of that: `SweetHIDButtons` / `SweetHIDTouch` are matched on `IOResources` by the personalities in
  `0058-sweet-hid-event-services.patch:17-25` and call `registerService()` in `start()`. observed: `SWEET HID: buttons registered` and
  `SWEET HID: touch registered` on bluejay with `SWEET KEYS: disabled (sweetkeys=0)` / `SWEET TOUCH: disabled (sweettouch=0)`
  (qr-20261021T192657Z.txt:65-66 and :95-96, qr-20261021T173713Z.txt:64-65,95-96; both are read-only boots from before the journal fix, but the HID registration happens at kext start, before the root mount).
- backboardd's `IOServiceFirstMatch IOHIDDevice ...` line is cut off by the 159-char line limit of the QR snapshot (section 2.3), so the
  result of that lookup is not visible in the QR logs. [observed log]
- What upstream did without any HID device: the Setup "Hello" screen was drawn **before** patch 0058 existed ("Input: no touch, no power
  button, no volume buttons (no HID drivers ...). Setup is stuck on Hello", NOTES.md:358) and the first pixels came even earlier (NOTES.md:373).
  So a missing HID device never stopped backboardd/SpringBoard from presenting; it only meant no input. [source NOTES.md]
- Battery: `SWEET BATTERY: static fallback, 100% 4200mV external=1 charging=0` (qr-20261021T192657Z.txt:67), same code as sweet (0062). observed
- Display: `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0xfac00000, default surface 1, presented as 1125x2436` (qr-20261021T192657Z.txt:68), `display on`,
  one `swap`; panel is the same 1080x2400 as sweet. observed
- Kernel versions: the GUI runs used xnu-0085 (CI 39644276616) and xnu-0085restart; both contain 0054-0079 (sampler, AKS 35/101, throttled
  present, no ext resolver, work-interval privilege, pid_hibernate). HANDOFF.md:339,367.

## 2. What the logs show

### 2.1 Order of events (qr-20261021T205516Z.txt, `--gui --qr 600`)

1. launchd spawns daemons (`exec ...`); `SWEET AKS: <name> sel 0` answered 0 for each.
2. backboardd (pid 56): `IOK backboardd: matching ... backlight-control`, `path IODeviceTree:/arm-io`, `IOServiceFirstMatch IOHIDDevice`,
   `open IOPMrootDomain`, `path IODeviceTree:/product`, `path IODeviceTree:/chosen`, `SWEET FB: display on`, `matching1 AGXAccelerator -> no`,
   `matching1 IOCoreSurfaceRoot -> IOC...`, `open IOCoreSurfaceRoot`, `SWEET SURF: create id 2 1125x2436 BGRA`, `trap 2`,
   `SWEET FB: swap 2 layer 0 surface 2`. That is the Apple logo.
3. `SWEET AKS: SpringBoard sel 0`, `chronod sel 0`, `exec liveactivitiesd`, `SWEET FB: thermalmonitord sel 78`, `exec com.apple.datamigrator`,
   `cloudd`, `securityd sel 10 -> 0xe00002c7` (known stub), more daemons, `identityservicesd privilege 12001 denied`.
4. `SMP --- sample 0` and `sample 1` (see 2.2), then `SWEET QR: taking over the screen`.
5. No `CRASH ...` line, no `|SpringBoard|`/`|backboardd|` /dev/null copies, no `migrationpluginwrapper`, no second `SWEET FB: swap`
   anywhere in the window. observed

### 2.2 backboardd and SpringBoard (SMP sampler, patch 0054)

- SpringBoard (pid 26, started ~30 processes before backboardd pid 56): 2-3 threads only. t0 (main) is in `mach_msg2_trap` under
  `dispatch_mach_send_and_wait_for_reply` -> `xpc_connection_send_message_with_reply_sync` -> `___forwarding___`. t2 is the mach-msg listener
  thread. No other thread appears: SpringBoard never got past the first synchronous call. observed (205516 sample 0 and 1; 201541 sample 0 and 1)
- The unslid addresses of the t0 stack are **identical in the two boots** even though the shared-cache slide differs (0x2e6c8000 in 205516,
  0x2dd58000 in 201541): `0x18db6a308 0x18db6a694 0x1d1bee0ec 0x18120c224 0x1811a5cb0`. observed (computed from the two logs, section 5)
- backboardd (pid 56): main thread in the CFRunLoop wait; ~10 pool/CFRunLoop threads idle in `mach_msg`; **one worker thread has the same
  sync-XPC stack as SpringBoard's main thread** (205516 sample 0 t14, sample 1 t10; 201541 t2 in both samples). In 205516 sample 0, thread t2 is a
  call from backboardd's own code (`0x100326ae0`) through a framework (`0x19c55685c`...) into libxpc and is **gone in sample 1**: a bounded call
  that completed in between. [guess] = `DMPerformMigrationIfNeeded` -> datamigrator (it is the only call that fits: backboardd main binary into a
  framework into sync XPC, exactly when datamigrator is exec'd). Unverified until symbolicated.
- Upstream's own conclusion for the same stack: "SpringBoard waits in `-[BKSSystemShellService _checkIn]` (synchronous BSXPC call to
  backboardd, from FBSystemShellInitialize <- SBSystemAppMain) - backboardd holds the system shell check-in until the data migration ends"
  (NOTES.md:371, 372). source

### 2.3 What the QR text cannot show (important)

`sweet_log_snapshot` (`xnu/patches/0083-sweet-qr-log.patch:43-100`) (a) **drops every line whose hash, ignoring digits
0-9, equals one of the last 32 kept lines**, and (b) **cuts every line at 159 characters** (`char line[160]`). Consequences:

- Samples 2-9 (sampler schedule: 90 s, then every 120 s, 10 at most, `0054-sweet-process-sampler.patch:204-208`) must exist by 600 s but are
  missing from both QR files. Each of their lines equals the same line of the previous sample (identical stacks) and is dropped by (a);
  only a new line (`SMP backboardd t13 pc 0x0 ...`, the last line before the takeover banner) survives. So **the missing samples are themselves
  evidence that nothing changed between ~210 s and 600 s** (guess based on source, consistent with the observed ends of both files).
- Repeated `exec bluetoothd` / `fairplayd.H2` (relaunch loops, NOTES.md:373) are dropped the same way, so the QR tail is no measure of how
  busy the system still is.
- `SMP ... bt` is cut at 159 characters, so the deep frames are lost; the persistent ring has them all.
- `sweetiolog` stops after **400 lines for all processes together** (`0049-sweet-iokit-trace.patch:13,29`); the absence of SpringBoard
  `IOK` lines is therefore not evidence that SpringBoard did no IOKit calls.

### 2.4 Expected lines that are missing here

From the upstream successful logs (NOTES.md:353, 356, 373, 311) the progress markers after the logo are: `SWEET SURF: ... create id 3` and
a stream of `SWEET FB: N swaps` (every 1000 swaps) once SpringBoard/backboardd compose UI frames (during migration even 58 Hz); `exec
migrationpluginwrapper` if a migration runs; `SWEET HID: key/touch` lines once input exists; the lock screen. Here:

- **Second present**: run 1 (`194623`, no `--gui`, first boot of the flashed image) logged `SWEET FB: swap 5 layer 0 surface 3` right after
  `thermalmonitord` (qr-20261021T194623Z.txt: lines near `SWEET FB: thermalmonitord`); both `--gui` runs (201541, 205516) log only `swap 2 surface 2`.
  The first 16 *presented* frames are logged (`0046-sweet-iosurface-iomfb.patch:1243`; the log call is in `AppleCLCD::present`, `0056` moved
  it behind the vsync timer), so a second frame would have appeared. observed. **Run 1 got further than the later runs on the same image.**
  Either the `--gui` args matter (I found no code path where they should: `sweetfb_noconsole` only sets `sweet_console_muted`,
  `0067-sweet-console-mute.patch`; `sweetfb_hz=30` gives `fFlushTicks = 2`, `0056-sweet-fb-throttled-present.patch:25`) or the root changed after boot 1 (I7). guess
- No `SWEET FB: N swaps` line ever: fewer than 1000 swaps. A migration with a progress UI draws at tens of Hz (NOTES.md:371), so the progress UI
  is not running. observed
- No `migrationpluginwrapper` exec although datamigrator was exec'd (upstream saw both when migrating, NOTES.md:368). observed (window starts
  before and covers the datamigrator exec; the first occurrence of an exec line is never dropped by the dedupe)

## 3. Hypotheses, ranked, with one cheap experiment each

Probabilities are my guesses, not measurements.

### H1 (~35 %) - The root never had a real first boot; SpringBoard/backboardd wait on state the skipped migration would have created

- For: I5 (the working upstream image descended from a completed real migration; ours is a fresh skeleton with migration *skipped from boot 1*);
  upstream's own bring-up shows SpringBoard's first synchronous call gated on the migration machinery (NOTES.md:371); datamigrator was exec'd
  and a backboardd call into a framework completed between 90 s and 210 s (2.2) - but the follow-up never happened; the disposition dict in
  I3 is invented, so datamigrator may "decide" differently from what we believe; run 1 progressed further (a second frame) than later boots (I7).
- Against: no progress UI, no `migrationpluginwrapper` (2.1, 2.4); the upstream README claims the same tools yield a booting image
  (`UPSTREAM_README.md:129`) - but those tools are byte-identical to ours and I could not find evidence that upstream ever built a root from
  the IPSW and booted it without a prior real migration. guess
- **E1 (needs an image change + reflash, user confirmation, ~10 min flash)**: rebuild the root *without* the two `merge_pref(root,
  'com.apple.migration', ...)` keys (`tools/sweet-root-tweaks.py:133-135`), flash, and boot with `--rw --gui --qr off --restart 5400` at
  `cpus=1`. A real migration then runs (upstream: ~60 min at 58 Hz, ~30 min with `sweetfb_hz`, NOTES.md:371). If the home/lock screen appears
  afterwards and a *second* boot (with the keys added back, or simply again) also reaches it, H1 is confirmed. Do E0 and E3 first, they are free.
  No edit to `sweet-root-tweaks.py` was made: nothing there is a clear bug (see section 6).

### H2 (~30 %) - It is only slow: one A55 at an unknown clock, 60 daemons, polled UFS; upstream's 4-5 minutes are for six cores

- For: `cpus=1` vs `cpus=6` (1.3); no cpufreq driver exists in XNU, so the CPU runs at whatever ABL left (unknown on gs101; upstream sweet logged
  "quiet after ~47 s", NOTES.md:372, while the exec stream here is still running at ~210 s: 55 `exec` lines in the 120 s between samples 0 and 1);
  SMP is where `sweet` got its speed (0065, 0078); `sweetfb_hz=30` is a CPU copy of 10 MB per frame (0056).
- Against: the stacks do not change between 210 s and 600 s (2.3), which means the blocked threads were not woken up in ~6 minutes. A merely
  slow peer would normally finish within that time. Also backboardd drew the logo at <40 s (run 1).
- **E3 (boot-arg only, no flash)**: `tools/build-xnu-image.sh --rw --gui --qr off --restart 2700` (45 min, no QR so the screen shows the
  GUI) and read the ESP32-CAM frames at 10/20/30/40 min. If the lock screen appears, H2 is confirmed; if nothing changes for 45 min, it is
  rejected. In parallel (second run) `--args "cpus=4"` tests SMP (risk: untried on gs101, may hang or panic; the restart timer cleans up).

### H3 (~25 %) - A third party never answers the synchronous XPC call (backboardd worker thread and/or SpringBoard main thread)

- For: both processes sit in the same libxpc sync-call frames (2.2) and stay there; there is no crash, so the peer is hung, not dead, or it is
  a mutual wait (SpringBoard main -> backboardd check-in; backboardd worker -> something that needs SpringBoard or a daemon that is not running
  or is itself blocked). The unslid stack is deterministic across boots (2.2).
- Against: nothing contradicts it; it is just not identified. It overlaps H1/H2 (the peer may be datamigrator, lsd, runningboardd,
  containermanagerd or a starved daemon).
- **E2 (no flash; boot-arg + symbolication)**: (a) symbolicate the addresses in section 5 with `ipsw dyld a2s` (inside `tools/rea/rea.sh`,
  or apfs-fuse as a normal user on `~/pixelios-artifacts/ipsw/087-86593-047.dmg`); the frames `0x18db6a308`, `0x19c55685c`, `0x100326ae0` and the
  Objective-C caller above `0x1811a5cb0` name the call and thus the peer. (b) Sample the suspects: `--args "sweetsample=SpringBoard,backboardd,com.apple.datamigrator,lsd,runningboardd,containermanagerd,installd,launchd"`
  (process names are matched against `proc_name`, `0054-...patch:137-198`; keep `--qr` small, the console ring is 4 MiB and the full text is
  read via E0, not through QR).

### H4 (<=10 % each) - rejected or unlikely

| Hypothesis | Evidence against | Experiment if wanted |
|---|---|---|
| Missing HID device | services registered (observed, 1.4); upstream drew UI without any HID (NOTES.md:358) | none |
| Display / `sweetfb_hz` / power | `display on`, panel shows the logo, `present` logs a frame; no new frame requested because backboardd/SpringBoard are blocked, not because the panel refuses | `--args "sweetfb_hz=60"` or drop `--gui` (that is E4 below) |
| Migration still running with UI | no progress frames, no `migrationpluginwrapper` (2.4) | covered by E1 |
| Thermal / battery service | `thermalmonitord` call answered 0, battery static fallback registered (observed) | none |
| Watchdog kill of SpringBoard | `BKNoWatchdogs` in the image (observed), no `CRASH`/re-exec | none |
| Memory pressure / jetsam | no re-exec of SpringBoard/backboardd (observed); 4 GiB like sweet highmem | add `sweetsample` and look at `vm_page_free_count` only if H1-H3 fail |
| Wrong clock rate | `sweetqr=600` fires at +602 s wall (observed), samples line up | none |

## 4. Experiments, cheapest first (all by the driver; I ran none)

- **E0 (zero risk, do first): pull the full console log of the last run.** Patch 0047 flushes the console to the last 4 MiB + 4 KiB of
  `userdata` (`0047-sweet-persistent-console-log.patch:30-110`); the phone lands in recovery after the software reset (HANDOFF.md:365),
  and `tools/sweet-log-pull.sh` reads those blocks with `dd` (reads only). Command (after `tools/to-fastboot.sh` is not needed; adb in recovery):
  `adb shell getprop ro.product.device` must say `bluejay`, then `tools/sweet-log-pull.sh OUT.txt`. The tool was written for OrangeFox
  (root); stock recovery may refuse `dd` of the block device - then `fastboot boot` the OrangeFox image the repo kept under `recovery/` (gitignored).
  Each boot restarts the ring, so do it **before** the next XNU boot. Then grep OUT.txt for: `CRASH`, `SMP --- sample`, untruncated
  `SMP SpringBoard t0 ... bt`, `exec migrationpluginwrapper`, `SWEET FB:`, `|SpringBoard|`, `IOK SpringBoard`, the count of `exec` lines per minute (is launchd still
  spawning at minute 8?), and the last line number of the file (does the console go silent?).
- **E4 (boot-arg only)**: repeat run 1's exact arguments (`tools/build-xnu-image.sh --rw --qr 200`, i.e. without `--gui`). If the second
  `SWEET FB: swap 5 ... surface 3` returns, the `--gui` flags change behaviour (then read why), otherwise the root state changed after boot 1 (I7).
- **E3** and **E2** as in H2/H3. **E1** last (reflash).
- A finer log without a new kernel: `--args "sweetqr_tail=30000"` is capped by the 48 KiB work buffer (`SWEET_SNAP_WORK` in 0083) and does not undo
  the dedupe; E0 is the better channel.

## 5. Addresses to symbolicate (shared cache offsets, slide removed)

Computed as `runtime - slide`; the same values come out of 205516 (slide 0x2e6c8000) and 201541 (slide 0x2dd58000), so they are stable per image.
For `ipsw dyld a2s CACHE ADDR` / the REA tools. The cache is `dyld_shared_cache_arm64` of build 20H392 (SystemOS cryptex `087-86593-047.dmg`).

| Thread | Unslid frames (leaf first) |
|---|---|
| SpringBoard t0 (main) = backboardd persistent worker | `0x1c2611030` mach_msg2_trap, `0x1c2622b18`, `0x1c2622db8`, `0x1c2611524`, **`0x18db6a308`**, **`0x18db6a694`**, `0x1d1bee0ec`, `0x18120c224`, `0x1811a5cb0` (cut at 159 chars; the rest is in the E0 log) |
| SpringBoard t2 | `0x1c2611ad0`, `0x1d1b08140`, `0x181c897b8`, `0x181bf0bc0`, `0x181cb6238`, `0x181bdb7bc`, `0x181c102bc`, `0x1a26928bc`, `0x18db977a8` |
| backboardd t2, sample 0 only | `0x18e560d08`, `0x18e55b988`, `0x19c57bcc0`, `0x19c5789d4`, **`0x19c55685c`**, `0x19c5569bc`, then main binary |
| backboardd t0 | `0x186e5c148`, `0x186e5d2e0`, `0x186e61d20` (CFRunLoop wait) |
| backboardd main binary (mh 0x100258000, vmaddr `addr - mh + 0x100000000`) | `0x100326ae0` (caller of the framework call), `0x100009e40` (main) |

## 6. Notes on `tools/sweet-root-tweaks.py` (not edited)

- No clear low-risk bug. Two observations only: (1) the `DMUserDataDisposition` dict (`:135`) is not copied from a real datamigrator run (I3);
  if E1 shows a real migration works, copy the structure datamigrator writes and use it instead of the guess. (2) Neither `build-ios-root.sh` nor
  `check-ios-root.py` verifies the migration plist, `springboard.plist` or `purplebuddy.plist`; adding a check would catch a wrong image early
  (a host-side check, no flash). Not done here to keep this change documentation-only.

## 7. What I could not do

- No symbolication (no `ipsw`, cache in an APFS dmg): the call names in 2.2 are guesses based on upstream's NOTES.md:371-372 and on the
  address pattern, flagged as such.
- I did not see the full console log (E0) or the camera frames; every statement about "nothing changes after ~210 s" rests on the QR text
  with its dedupe (2.3) and on the unchanged sample stacks. observed + source
