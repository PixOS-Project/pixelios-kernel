---
id: TP-037
tool: "tools/golden-unattended-image + tools/fboot-android --unattended + tools/golden-pull ui"
status: building
proposed_by: "claude-opus-5-5 (driver 8, method-plan)"
date: 2026-10-28
exposed_by: "golden-1 and golden-2 (docs/runplans/golden-1.md:54-57, golden-2.md step 3 + session notes, super): after `erase userdata` adb is off and the host key is unauthorized, so the user had to be at the phone (wizard, USB debugging, RSA prompt, Magisk su grant)"
extends: "TP-035"
related: "TP-035"
usage: "tools/golden-unattended-image build [--out DIR] [--dry-run] | verify IMG;  tools/phone-lock run --owner golden -- tools/fboot-android --unattended [--erase-userdata --yes-erase-userdata] [--dry-run];  tools/golden-pull ui dump|tap X Y|key NAME"
refs: "-"
---

## checked
`tools/tool-proposals check` unattended / adb_keys / golden-image / magisk: `RESULT: NONE` for all four [obs 2026-10-28]. TP-035 (`done`) boots only the one pinned Magisk image
(PIN 98cbf66b..., tools/fboot-android:10) and has no way to pre-authorize adb. `golden-pull ui` (screen dump before every tap) exists only on branch
tools/golden-unattended (a5669f9, +66 lines in tools/golden-pull, +28 in test_golden_capture.sh). It drove golden-2's Android UI [obs golden-2 session notes]
but is not on trunk. golden-1.md:55 recorded "a boot image that switches adb and root on by itself is NOT pursued" (method driver 4, 2026-10-27). The user's
wish of 2026-10-28 (fully unattended golden captures, super HANDOFF driver 7 section) replaces that decision.

## why
Every golden session after the userdata wipe needs a person at the phone for step 3: setup wizard, Developer options, USB debugging, the RSA prompt,
then the Magisk su grant and the "additional setup" reboot. Measured: golden-2 had the user at the phone ~15 min (03:30-03:45Z, golden-2.md:50), and
golden-1 used the "ping me, 30 min stranded rule" (golden-1.md:57). While the user is away (the normal case) a golden capture cannot run at all, and it
blocks the oracle step (PORT-METHOD) of every block. Target: zero human steps from `fboot-android` to `golden-pull stop-pull`.

## output
Three parts, all ending in `RESULT: PASS|FAIL|ERROR ...`, exit 0/1/2, logs in `logs/golden-unattended/<stamp>/`:
1. `tools/golden-unattended-image build`: input = the pinned Magisk image (sha256 must equal the TP-035 PIN) + the host's `~/.android/adbkey.pub`. It unpacks with
   magiskboot (or Android's `unpack_bootimg`/`mkbootimg`, whichever is already installed; no new deps). It adds to the ramdisk `overlay.d/pixos-golden.rc` and
   `overlay.d/sbin/pixos_adb_keys` (= adbkey.pub). The rc, [read from source] Magisk overlay.d docs: rc files are injected into init:
   - `on post-fs-data`: `mkdir /data/misc/adb 02750 system shell`, `copy ${MAGISKTMP}/pixos_adb_keys /data/misc/adb/adb_keys`, `chown system shell`, `chmod 0640`
     (path from AOSP frameworks/native libs/adbd_auth/adbd_auth.cpp:407-410 main: adbd reads `/adb_keys` and `/data/misc/adb/adb_keys` every time, no build-type check [read from source]).
   - `setprop persist.sys.usb.config adb` + `setprop sys.usb.config adb` (init.usb.rc starts adbd on `sys.usb.config=adb` [guess until S0-2]).
   - Magisk su grant for uid 2000 (shell) via `magisk --sqlite "REPLACE INTO policies (uid,policy,until,logging,notification) VALUES(2000,2,0,1,0)"` in a
     `post-fs-data` service [guess on the schema until S0-3].
   Writes the image to `~/pixelios-factory/golden-unattended-<pin8>-<key8>.img` (outside git) + `.sha256` + a manifest (inputs and their sha256, overlay files).
   The build is reproducible: same inputs = same sha256. `verify IMG` re-runs the build in a temp dir and compares the sha256.
2. `tools/fboot-android --unattended`: same checks as today (phone lock, product=bluejay, unlocked=yes, slot pin). It boots the derived image only if `verify` passes and
   the manifest's base sha256 equals PIN. It never adds a second free-form pin. Still `fastboot boot` only (RAM), never `flash`.
3. `golden-pull ui` ported to trunk from tools/golden-unattended a5669f9 (merge, never rebase): `dump` (uiautomator XML to the out dir), `tap X Y` (refuses unless a dump
   newer than the last tap exists and the destructive-screen guard does not match, e.g. Magisk "Direct install"/"Install", which write boot_x), `key NAME`.
   It also gets the wizard skip: `golden-pull ui wizard-skip`, a bounded script of dump+tap steps with a GAP line per unknown screen. The fallback is
   `settings put global device_provisioned 1` + `settings put secure user_setup_complete 1` over root adb [guess until S0-4].
Test: `bash tools/test_golden_capture.sh` (fake adb/fastboot/magiskboot) plus new cases: wrong PIN, missing adbkey.pub, rebuild sha mismatch, ui tap without fresh dump.

Rule 7, S0 (measure before relying, ONE golden session with the user reachable, as today's step 3; each line [obs] or the part stays off):
- S0-1 on the stock BP31 build: `ls -l /data/misc/adb/adb_keys` exists after boot and `getprop ro.adb.secure` = 1. Then host `adb devices` = `device` with no prompt.
- S0-2 `getprop sys.usb.config` contains adb before the wizard, and adbd runs (`getprop init.svc.adbd` = running).
- S0-3 `su -c id` from `adb shell` gives uid=0 with no app prompt, and the Magisk app is not needed for it.
- S0-4 the wizard steps the tool must tap (dump each screen). Does Android re-write `persist.sys.usb.config` / adb_enabled from Settings on first boot (AdbService)?
Any S0 FAIL: that part prints `GOLDEN GAP: <part> not unattended` and the session falls back to "ping the user for step 3" (no silent failure, rule 8).

## risks
Touches only the bluejay, through `fastboot boot` (RAM) and `erase userdata` (pre-approved for golden sessions, PLAN-METHOD P0). The tool never flashes a partition.
Magisk's own "additional setup" and "Direct install" write boot_b/boot_x. The ui guard must refuse those screens, and in golden-2 the reboot came from additional setup:
S0-3 must show su works without it, or the tool stops with a GAP. It must never accept that write by tapping it.
The derived image holds only the host's PUBLIC adb key, and it stays out of git (`~/pixelios-factory`). It still grants root adb to that key holder for as long as
the RAM boot runs. That is acceptable: it lives only until the next reboot, and only the RAM-booted stock Android has it (the on-disk boot is untouched).
Review: Opus (hardware path: new image into fastboot boot, erase), max 3 rounds. `golden-pull ui` is read-only on hardware except taps: Sonnet.
Never print `~/pixelios-artifacts/golden/.wifi-join` or the SSID (the wifi-join step from the same branch keeps its PSK-never-printed mutant test).

## amendments
- 2026-10-28 driver 9: status approved (user, 2026-10-28 ~07:55 CET, driver 9 thread mcp:dc3559eb-5982-4cf9-8917-1a7bde4ff5a3).
- 2026-10-28 implementer: parts 1 and 2 built (tools/golden-unattended-image, fboot-android --unattended; `golden-pull ui` was already on trunk). The `wizard-skip` step of part 3 is NOT built: the wizard screens are unmeasured (S0-4, rule 7). Status `building` until it is. Every rc line stays [guess until S0-n].
- 2026-10-28 driver 12, golden-4-unattended (super docs/runplans/golden-4-unattended.md) [observed on hardware]: S0-1 PASS (host authorized after `erase userdata`, no prompt), S0-2 PASS (`ro.adb.secure=1`, `sys.usb.config=adb`, adbd running before the wizard; first wizard screen `com.android.yadayada/.DisclaimerActivity`), S0-3 FAIL (`su: inaccessible or not found`: Magisk needs its app + environment fix after a wipe, Magisk install.html step 7), S0-4 only screen 1 dumped (no taps), adb survives a reboot. Parts 1-2 are proven; part 3 needs the Magisk app (`golden-pull magisk-install`, now unattended) and the env-fix/su grant (user taps, or a pre-seeded policy: unresearched).
- 2026-10-28 driver 13, S0-3 research (local docs/research/45-magisk-su-unattended.md, Magisk v30.7) [read from source, key cites re-checked by the driver: bootstages.rs:70-72,128-131, sqlite.cpp policies schema, su/daemon.rs:238-243,270-272]: `su` is missing after a wipe because magiskd aborts post-fs-data setup when `/data/adb/magisk/busybox` is absent (only the app's fix_env, `/data` only, or direct_install, boot write, creates it); no policy row + no manager = deny. Proposed fix (/data writes only): ship busybox (APK `lib/arm64-v8a/libbusybox.so`, sha-pinned) in overlay.d, copy it to /data/adb/magisk in post-fs-data, and grant uid 2000 with `magisk --sqlite "REPLACE INTO policies (uid,policy,until,logging,notification) VALUES(2000,2,0,0,0)"` on `sys.boot_completed=1` (logging/notification 0 so the manager is never asked; the current rc uses logging=1 at post-fs-data, before magiskd). Rc ordering, `copy` exec bit and `exec u:r:magisk:s0` parsing are [guess] until a golden run.
- 2026-10-28 driver 14, review tp037-s0-3-su-r1 F5 (rule 1) [observed on hardware, read-only from earlier golden captures ~/pixelios-artifacts/golden/g4-20261028T095554Z-r2/build.txt and 20261027T220758Z/dmesg-coldboot.txt]: `PREINITDEVICE=sda8` is the `/metadata` partition (`dev.mnt.blk.metadata=[sda8]`, ext4, e2fsck'd and mounted by fs_mgr at ~19 s). The rule-1 partitions are elsewhere: persist=sda1, efs=sda5, efs_backup=sda6, modem_userdata=sda7, modem_img=sda20. /metadata is wiped by `fastboot -w` together with userdata and stock Android writes to it on every boot, so magiskd's `mkdir 0700 <metadata>/magisk` (or similar) is reversible: no stop. F5 closed.
- 2026-10-28 driver 14, golden-5-su (super docs/runplans/golden-5-su.md, out ~/pixelios-artifacts/golden/g5-20261028T120508Z{,-b2}, image 3388f814, trunk 1b6aa66) [observed on hardware]: **S0-3 PASS**: two-boot delivery works unattended: boot 1 su-prep done=ok; boot 2 /data/adb/magisk/busybox present, /data/magisk adopted, `su -c id` = uid=0(root) context=u:r:magisk:s0 with no prompt and no Magisk app. Open GAP: su-check's policy readback (`magisk --sqlite 'SELECT ... WHERE uid=2000'`) printed nothing with rc 0, cause unknown (row 6 of the run plan); measure next golden session before changing su-check.
- 2026-10-29 (R10 golden BP31, delegate 20l): `golden-unattended-image build|verify` take `--build cp3a|bp31` (default cp3a). The pin is read from tools/fboot-android: cp3a = `^PIN=`, bp31 = `^PIN_BP31=` (not a sha256 yet = ERROR, no fallback to the cp3a pin); a bp31 build needs an explicit --base. `fboot-android --unattended` now calls `verify IMG --build BUILD`. Tests: tools/test_golden_capture.sh (gui: --build cases).
