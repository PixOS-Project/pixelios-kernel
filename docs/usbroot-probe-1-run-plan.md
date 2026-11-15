# Run plan: usbroot-probe-1 (USB read-only root boot probe, patches 0157/0150)

Renumbered 2026-10-26 (research 64): patch "0149" below is now 0157-sweet-usbroot-ro (GPU keeps 0149); probe 1-4 were run under the old name.

Branch `test/usbroot-probe` (trunk d4fa5fe + `clean/usbroot-ro`; 0157 hunk-count fix, see below). Design: `docs/research/44-usbroot-readonly.md`;
review: `docs/reviews/usbroot-ro-review.md` (on `clean/usbroot-ro`). Status (updated 2026-10-29): RUN. Probes 1-4 ran under the old patch name (see above; the run logs of probes 3 and 4 are from 2026-10-26); probe 4 (`usbroot-probe-4`, 20261026T202654Z, git 8063dd0) booted with `rd=usbroot0`, started the USB device and logged `hfs: mounted iOSRoot on device b(1, 0)` at +373 s, `RESULT: PASS 1 expectation(s) met` (*observed*, run report). No SpringBoard line in that log. The predictions below were written before the runs and are kept as they were.

## What the probe asks

Does a kernel with `rd=usbroot0 sweetusb=2` bring up the USB ECM link, get the disk size from the host (INFO), read the root image
over raw EtherType 0x88B6 frames, and reach a root-mount result? Writable `/private/var` is an open problem (research 44 F4):
a full iOS boot is NOT expected; a mount failure of var/launchd is an informative result.

## Pieces

- Kernel: `tools/xnu-local-build --label usbroot-probe` output `~/pixelios-artifacts/kernel-usbroot-probe.sweet` (sha256 985f611a2ca9bcdf3b8593b7352d3c43b2a9bf3740efe877fce3dbe1c9fef56a, built from the commit below).
- Boot-args: xrun/build-xnu-image always start with `rd=ufsroot0` and (xrun default `--rw`) `ufsrw=1 rootrw=1`, then append `--args`.
  The last duplicate key wins [read from source: pexpert/gen/bootargs.c `gotit:` keeps scanning, no break after a hit], so
  `--args "rd=usbroot0 rootrw=0 ufsrw=0"` yields `sweetusb=2 ... rd=usbroot0 rootrw=0 ufsrw=0`. 0157 refuses `rootrw=1` (sweet_usbroot.c:674 in the patch).
  [observed on host] `XNU_IMAGE_ARGS_ONLY=1 tools/build-xnu-image.sh --usbnet --rw --args "rd=usbroot0 rootrw=0 ufsrw=0"` prints that string.
  [guess] A `--no-rw` option in xrun would be cleaner; not added here (AP-58: shared tool change belongs on trunk first).
- Root image (never modified, opened "rb" only): `/tank/pixelios/ios-root4-inj6.img` (16 GiB, the root of the latest GPU runs, HANDOFF g44).
  `userdata` on the phone is not touched and not flashed for this probe.
- Host server: `tools/usbroot-host.py /tank/pixelios/ios-root4-inj6.img --iface <iface with MAC 02:00:5e:77:ee:01>`; needs CAP_NET_RAW.

## Commands

One-time host setup (replaces every hand-started server; runs as user <offload-host> with CAP_NET_RAW only, survives phone reboots; status: `tools/usbroot-status`; image: edit `/etc/usbroot-host.env`, then `tools/usbroot-serve`):

    sudo /home/<user>/.t3/worktrees/usbroot-probe/tools/usbroot-service-install

[guess] untested on a live systemd until the user runs it; `tools/usbroot-service-install --dry-run` shows everything it would do. The manual commands below are the fallback.

Host server, started by the user (root needed for AF_PACKET; the tool never escalates), in the worktree, before or right after the phone enumerates:

    sudo setpriv --reuid=<offload-host> --regid=<offload-host> --init-groups --inh-caps=+net_raw --ambient-caps=+net_raw \
      python3 /home/<user>/.t3/worktrees/usbroot-probe/tools/usbroot-host.py /tank/pixelios/ios-root4-inj6.img --max-seconds 600 \
      > /home/<user>/.t3/worktrees/usbroot-probe/logs/usbroot-host-1.log 2>&1

(simpler alternative: the same python3 command under plain `sudo`; the image is only opened read-only either way.) [guess: the setpriv form works; the plain sudo form is certain.]
The interface `enx02005e77ee01` exists only while the phone is enumerated; the server finds it by MAC and stops with RESULT: ERROR (exit 2) otherwise.

Phone run (watcher takes the phone lock; replace KERNEL with the built kernel):

    tools/xrun --kernel KERNEL --label usbroot-probe-1 --args "rd=usbroot0 rootrw=0 ufsrw=0" --duration 300 --no-camera \
      --expect 'SWEET USBROOT: usbroot0 [0-9]+ MiB read-only' --fail-on 'Kernel trap at|panic\(cpu|panic:|Assertion failed'

Note: with `--expect` xrun stops early and resets the phone once the line is seen, which would cut the mount result. Use
`--full` (waits for `--duration`) so the root-mount outcome is also captured. [read from source: tools/xrun header]

## Expected log lines (in order, from patches 0073/0157/0150) [read from source]

1. `SWEET USB: configured (1)` and `SWEET USB: ue0 192.168.7.2/24 up` (ECM link).
2. `SWEET USBROOT: usbroot0 16384 MiB read-only, max read 16384` (INFO answered; 17179869184 B = 16384 MiB).
3. Root mount attempt from `bsd_init` (`vfs_mountroot`); then either launchd start or a mount/panic message.
Host log: `RESULT: PASS served N requests, refused 0, ignored M, io errors 0 on enx02005e77ee01` after the window.

## Pass / fail

- PASS (probe): line 2 seen AND the host server served > 0 READ requests AND a root-mount result line is in the log (success or failure).
- Informative failure: link up but no INFO reply (`SWEET USBROOT: USB link not up after 30000 ms`, `no reply`, `host status N`): record which.
- FAIL: kernel trap/panic before line 2, `SWEET USBROOT: rootrw=1 conflicts`, `the UFS root already started, refusing`, `needs boot-arg sweetusb=2`,
  or host `io errors > 0`.
- No claim of "works" without the run dir + kernel sha in HANDOFF (AGENTS.md rule 5).

## Safety

Phone side writes nothing: usbroot0 rejects writes (EROFS), `ufsrw=0 rootrw=0`, the UFS driver refuses when usbroot is active.
Host side opens the image "rb" only, no write opcode, transmits only on the interface with the phone's ECM MAC.
Unreviewed since the 0157 fix below: the build-fix is a hunk header count only.

## Build fix found while preparing

`xnu/patches/0157-sweet-usbroot-ro.patch` declared `@@ -0,0 +1,578 @@` for `sweet_usbroot.c` but the hunk has 581 lines, so `patch`
truncated the file (the last 3 lines, `return 0; }` and `#endif`) and the first local build failed with `unterminated conditional directive`
(sweet_usbroot.c:15) [observed 2026-10-26, xnu-local-build label usbroot-probe]. Fixed to 581; all other hunks of 0157 count correctly (checked with a script).
`tools/test_usbroot_c.py` did not catch it because it extracts the `+` lines itself (AP-90).

## Result (usbroot-probe-1, observed on hardware 2026-10-26)

Run dir `logs/runs/20261026T161742Z-usbroot-probe-1` (pixelios-kernel), args `rd=usbroot0 rootrw=0 ufsrw=0`, 300 s, kernel `kernel-usbroot-probe.sweet`.
- (a) Host server NOT serving: `logs/usbroot-host-1.log` has one line, `RESULT: ERROR network: [Errno 100] Network is down` (mtime 17:16 local, before the run). The server exited before the phone enumerated, so it saw no request (0 capacity, 0 reads). The host interface `enx02005e77ee01` (192.168.7.1/24) was up afterwards. Cause: the server was started before the interface existed (guess; not checked in usbroot-host.py).
- (b) merged.log (190 USB event lines) has no `SWEET USBROOT`, `sweetusb`, `usbroot0`, root-mount or panic line. The SWEET USB gadget itself came up: `SWEET USB: running (DSTS 0x00820001 after 0 ms)`, `SWEET USB: configured (1)`, `SWEET USB: ue0 192.168.7.2/24 up (err 0)` at +93 s, then only `SWEET USB: setup ...` and `event 0x0000c046` / `0x0000e044` lines.
- (c) Boot did not reach launchd or SpringBoard: no root mount, no SpringBoard/backboardd/lockscreen markers; the log ends with USB events. Only 1 CPU was brought up (`1/1 CPUs initialized`).
- (d) `RESULT: PASS owner=usbroot held_for=541s` (no fail-on pattern hit). Also: `PHONE: to-fastboot failed after the software reset request` (check tools/phone-state).
- Verdict: inconclusive for the USB root path; rerun with the server started after the interface is up (or fix its startup race).

## Analysis of probe 1 and probe 2 (2026-10-26)

Cause of the missing `SWEET USBROOT` lines: **unknown** (not certain; no fix guessed). Facts:
- Boot args did contain `sweetusb=2` once and `rd=` twice (`rd=ufsroot0 ... rd=usbroot0`): run.json `boot_args` [observed]. The parser keeps scanning after a match and overwrites, so the LAST `rd=` wins (xnu pexpert/gen/bootargs.c, PE_parse_boot_argn_internal loop: no `break` after a non-boolean match) [read from source]. IOFindBSDRoot reads `rd` at IOKitBSDInit.cpp:705 and the usbroot branch is at :791 [read from source, work tree usbroot-probe]. So the usbroot branch should have been taken; the earlier note ("keeps the last duplicate") is consistent.
- USB was started by that path only: sweet_ufs_init (the other caller of sweet_usb_probe, 0091) refuses when `sweet_usbroot_active()` (0157 sweet_ufs.c hunk) and there is no `SWEET UFS`/`SWEET LOG` line [read from source + observed]. So sweet_usbroot_rootdev most likely ran sweet_usb_probe(); but it printed nothing before the first `SWEET USB:` line, and nothing after `configured`. Its first printf in the old code is only on an error or on success; the INFO exchange itself printed nothing until 4 tries x 2 s = 8 s elapsed (0157 UR_TRIES 4, UR_WAIT_MS 2000). That error line (`no reply`) never appeared in the 64 KiB replay or the live stream [observed].
- The live log stopped because `ud.events++ < 200` in sweet_usb.c ud_thread caps the `event 0x...` prints at 200 (sweet_usb.c:887-888) [read from source]. `0x0000c046` is EP_TX XferComplete and `0x0000e044` EP_RX XferComplete (ud_event decode: ep=(ev>>1)&0x1f, type=(ev>>6)&0xf, sweet_usb.c:826-828); each UDP log chunk (every 100 ms, ud_log_thread, sweet_usb.c:926) is one TX completion that itself prints an event line, until the cap [read from source; the 100 ms cadence in usbnet.log is observed]. They are the log's own ECM traffic, not usbroot traffic.
- Log channel and usbroot coexist: sweet_usbroot_rx() returns false unless `ur.active` and EtherType 0x88B6, so IP/ARP/UDP frames fall through to the stack (0157 sweet_usb.c ud_rx_done hunk) [read from source]. Interception cannot have dropped the UDP log (log chunks are phone->host TX, unaffected; the host->phone ARP replies are not 0x88B6).
- A silent kernel after the event cap means the absence of lines after `configured` does NOT show where it is waiting: every later printf must still have been in the ring. Remaining candidates (guess): a hang inside the link wait, sweet_usb_raw_tx or the first ur_sleep_ms before any print; or rootdev not entered.
- Host side (probe 1): the server exited with ENETDOWN before the phone enumerated (usbroot-host.py had no rebind); fixed in this commit (AutoTransport: waits by MAC, rebinds on ENETDOWN/ENODEV/ifindex change, logs each bind).

## Probe 2 (read-only trace, same behaviour otherwise)

New kernel `~/pixelios-artifacts/kernel-usbroot-probe2.sweet`. It prints a `SWEET USBROOT: [<ms since boot> ms] ...` trace at each step: IOFindBSDRoot sees rd (0150), rootdev entered, boot-arg sweetusb value, probe started/returned, link up, every request attempt (sent flag, answered/unanswered after N ms, bad frames), size exchange result, and `rootdev failed/ok`. On error the path waits 500 ms before returning so the 100 ms log thread flushes the lines before IOFindBSDRoot panics.
What the human does: start the (now robust) server ONCE, any time before or after plugging/booting; it waits for the interface by MAC and rebinds across the phone's reboot:

    sudo setpriv --reuid=<offload-host> --regid=<offload-host> --init-groups --inh-caps=+net_raw --ambient-caps=+net_raw \
      python3 /home/<user>/.t3/worktrees/usbroot-probe/tools/usbroot-host.py /tank/pixelios/ios-root4-inj6.img --max-seconds 900 \
      > /home/<user>/.t3/worktrees/usbroot-probe/logs/usbroot-host-2.log 2>&1

Phone run (the driver/watcher; the human also still configures 192.168.7.1/24 on the interface as xrun prints, for the UDP log):

    tools/xrun --kernel /home/<user>/pixelios-artifacts/kernel-usbroot-probe2.sweet --label usbroot-probe-2 --args "rd=usbroot0 rootrw=0 ufsrw=0" --duration 300 --full --no-camera \
      --fail-on 'Kernel trap at|panic\(cpu|panic:|Assertion failed'

Read the trace in order: no `IOFindBSDRoot rd=` line = the branch was not reached; `rootdev entered` without `starting the USB device` = refusal reason line; no `USB link up` = link wait; `unanswered` attempts = host did not answer (check logs/usbroot-host-2.log for binds/served counts).

## Probe 2 result (usbroot-probe-2b, observed on hardware 2026-10-26)

Run label `usbroot-probe-2b`, run dir `pixelios-kernel/logs/runs/20261026T165258Z-usbroot-probe-2b` (merged.log, xrun.log, report.txt, report.json), host log `~/.t3/worktrees/usbroot-probe/logs/usbroot-host-2.log`. Kernel `~/pixelios-artifacts/kernel-usbroot-probe2.sweet`, sha256 0a76768d9ac4, git d4fa5fe. Queued behind g66 with `tools/phone-lock run --owner usbroot`; run dir stamp 20261026T165258Z; image built 17:53:12 (xrun.log `image:` line); the usbroot lock was held for 16 s before the phone was touched (xrun.log `RESULT: PASS owner=usbroot held_for=16s`).

- Host server (observed, usbroot-host-2.log): bound enx02005e77ee01 at 17:40:01 (ifindex 497, bind #1); lost at 17:42:23 (interface gone; cause not shown in the log); bound again at 17:54:47 (ifindex 498, bind #2); lost at 17:58:21 ("Network is down") when the run ended. The host log has no request lines, so the request counts below come from the phone side.
- Phone-side requests (observed, merged.log `SWEET USBROOT:` lines): one disk-size request (op 1, lba 0x0, total 16, seq 1) at 684492 ms; the first attempt went unanswered after 2000 ms (status 5), the retry (seq 2) was answered after 10 ms, status 0. Then 1395 op-2 (read) requests, seq 3..1396, all answered on the first attempt with status 0 and 0 bad frames; per-request latency 27 to 78 ms; lba steps of 0x20 in the tail. Units of "total 16384" are as the driver logs them (not converted here).
- Trace (observed): `IOFindBSDRoot rd=usbroot0` (merged.log:129); `rootdev entered` 684005 ms; `sweetusb=2` 684005 ms; `sweet_usb_probe returned` 684062 ms; `USB link up` 684491 ms; `usbroot0 16384 MiB read-only, max read 16384` and `rootdev ok` 686806 ms; `SWEET USB: configured (1)` and `ue0 192.168.7.2/24 up` at about +93 s (merged.log, report.txt MARKERS).
- Failure (observed): the first `cannot mount root, errno = 19` at merged.log:467, preceded by `hfs_mountfs: encountered failure 22` and `hfs_mount: ... for device unknown-dev`. The retries repeat (mount(2) ... mount(35) failed, about one per second) until the host ended the run at +301 s. Panic grep: only `debug_log_init: Error!! gPanicBase is still not initialized`; no `Kernel trap`, no `panic(cpu`, no `Assertion failed`. The run never reached launchd.
- How far boot got (observed): IOFindBSDRoot, the USB gadget (configured, ue0 up) and about 1396 root reads; no launchd, no SpringBoard, no GUI. Whether the mount failure is the read data or the HFS path after the reads is not shown by this run (guess: the HFS path, because the reads themselves answered with status 0; not verified).
- CPUs (observed): `SWEET CPU: requested=8 selected=8 (limit=8)`; cpu=0 came alive; cpu=1 CPU_ON target=0x100 entry_pa=0x8814c3000 context=1.
- Boot-args (observed, xrun.log `image:` line): the appended args contain both `rd=ufsroot0` and `rd=usbroot0`, and both `ufsrw=1 rootrw=1` and `rootrw=0 ufsrw=0`. The trace used `rd=usbroot0`. Which of the duplicate keys wins is not checked here (guess: the last one; unverified).
- Final results (observed): report.txt `RESULT: FAIL root mount failed (34 line(s)); the run never reached launchd: cannot mount root, errno = 19`; xrun.log `RESULT: FAIL state=recovery target=fastboot steps=1` (the phone did not return to fastboot after the software reset request; xrun.log `NEED USER: check cable / hold Power+VolDown`). Phone-lock: `RESULT: FAIL owner=usbroot held_for=415s`.
- Next (guess, not decided here): the human holds Power+VolDown to get back to fastboot before the next probe; the duplicate `rd=` / `rootrw=` boot-args are a candidate to fix before probe 3.

## Probe 3 (patch 0151: read-only mount skips the late journal init)

Cause [read from source, research 55]: hfs_vfsutils.c:735-739 runs `hfs_late_journal_init` on a read-only mount (image: journaled, lastMountedVersion `H+Lx`, JIB flags 0x5), which writes the journal header and gets EROFS from usbroot0. Patch 0151 adds `&& !(hfsmp->hfs_flags & HFS_READ_ONLY)` (SWEET only); the ro branch at :796 then mounts without a journal. HFS_READ_ONLY is set at hfs_vfsops.c:1351 from `vfs_isrdonly` (MNT_RDONLY stays set unless rootrw=1, vfs_subr.c:1263-1268). Host check: `tools/usbroot-image-check.py /tank/pixelios/ios-root4-inj6.img` (RESULT PASS = patched ro mount does not write). Unbuilt-on-hardware; the end proof is `hfs: mounted`/launchd instead of `Initializing the journal`.

Duplicate boot-args [read from source]: xrun always adds `--rw` (tools/xrun:31) so build-xnu-image.sh puts `rd=ufsroot0` first (:32) and `ufsrw=1 rootrw=1` (:35) before `--args` (:41); PE_parse_boot_argn_internal keeps scanning after a match (the `break` leaves only the switch, `goto gotit` continues, bootargs.c:162-173), so the LAST duplicate wins. `--args "rd=usbroot0 rootrw=0 ufsrw=0"` is therefore enough; no tool change.

Server command: as in probe 2 (log usbroot-host-3.log). Phone run (driver/watcher):

    tools/xrun --intent none --kernel ~/pixelios-artifacts/kernel-usbroot-probe3.sweet --label usbroot-probe-3 --cpus 8 --args "rd=usbroot0 rootrw=0 ufsrw=0" --duration 300 --no-camera --full --fail-on 'Kernel trap at|panic\(cpu|panic:|Assertion failed'
