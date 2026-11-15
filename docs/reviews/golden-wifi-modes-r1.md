# Review golden-wifi-modes-r1: commit 17ff515 "golden-pull: 7 Wi-Fi trace modes" (tools/golden-pull +346, tools/test_golden_capture.sh +315)

Reviewer: Sonnet 5.5, adversarial, read-only (no phone, no adb, no <offload-host>, no full gate). Reviewed in a detached copy `/tmp/20aa/rev-17ff515` (the worktree was not edited).
Tags: [obs] = I ran it, [src] = file:line read, [guess] = unverified. Line numbers are of `tools/golden-pull` at 17ff515 unless a file is named.
Everything phone-side is UNVERIFIED by the author's own header (golden-pull:64-65); this review does not change that.

## Verdict: SAFE WITH CONDITIONS

No write path to any partition, block device, bootloader, modem, persist, efs, GPT, vbmeta, boot*, dtbo was found [src 907-1030, plus a grep of every added line for svc|setprop|settings|rmmod|insmod|setenforce|dmesg -c|logcat -c, only the commands listed in section "Mutating commands" matched, [obs]]. All phone-side state is RAM (tmpfs, bind mounts, tracefs) except three transient files in /data/local/tmp. Conditions before the first phone run: F1, F2, F3, F5 (F4, F6-F14 are advisory).

## Method summary

- `shellcheck -S warning` on tools/golden-pull and tools/test_golden_capture.sh: no findings, rc 0 [obs].
- `bash tools/test_golden_capture.sh` in the detached copy: first run `RESULT: FAIL ... failures=1` with the single line `GAP: tools/third_party missing` (gitignored dir absent from a fresh worktree; not a code defect, see F13) [obs]; with `tools/third_party` symlinked in: `RESULT: PASS test_golden_capture`, 1950 `ok`, 0 FAIL [obs] (log /tmp/20aa/base-test.log). The test runs under `HOME=$w/home` (test_golden_capture.sh:7), so the real `~/pixelios-artifacts/golden/.wifi-join` is not touched (its mtime stayed 2026-10-27) [obs].
- Mutation testing: 38 mutants of tools/golden-pull in copies under /tmp/20aa/mutm/M*/, each run through a trimmed copy of the wifi section (326 lines, passes: 243 ok) [obs]. 26 killed, 12 SURVIVED (list in F12). Extra behavioural probes X1-X4 in /tmp/20aa/mutm/X1.

## Mutating commands each mode emits (requirement 1)

| Mode | Mutating command | Reversible? | Undo path / after a failed run |
|---|---|---|---|
| wifi-trace-probe | none (ls, cat, grep, sha256sum, dmesg, readlink, `su --mount-master -c sh` running `true`) [src 914-923] | n/a | n/a |
| wifi-fw-bind | host: `mktemp -d`, `cp` of 3 files [src 942-948]. Phone: `adb push` x3 to /data/local/tmp/gp-wfw-* (userdata) [src 951]; `mkdir -p /mnt/gp-wfw`, `mount -t tmpfs`, `cp`, `chmod 0444`, `chcon`, `mount -o bind` over /vendor/firmware/{fw_bcmdhd.bin,bcmdhd.cal,bcmdhd_clm.blob} [src 322-330] | yes: RAM only, reboot clears it | stage-clean `rm -f` runs after push loop incl. push failure [src 973] but NOT on SIGINT/SIGTERM/kill/adb loss (F1 [obs]); rollback `wf_unbind_run` on any GAP after a push [src 975]; the empty dir /mnt/gp-wfw is never rmdir'ed (cosmetic, tmpfs) |
| wifi-fw-unbind | `umount` x3 + tmpfs [src 335-336] | n/a (it is the undo) | read-back sha vs recorded stock sha, GAP on mismatch [src 377-390]; needs `$out/wifi/fw-stock-sha.txt` of the SAME out dir (loud GAP otherwise, tested) |
| wifi-kprobes | `echo ... >> kprobe_events` x29, `echo FILTER > events/gpwifi/EV/filter` x2, `echo 1 > events/gpwifi/enable`, `echo 1 > events/cfg80211/enable` [src 303-311] | yes (RAM tracefs) | only by running wifi-kprobes-off by hand; NO auto-teardown on a GAP (F5); the prior value of cfg80211/enable is not recorded |
| wifi-kprobes-off | `echo 0 > .../enable` x2, `echo '-:gpwifi/EV' >> kprobe_events` x29 [src 312-319] | n/a | verified: gpwifi_left, gpwifi_dir, enable reads (tested) |
| wifi-scan | `echo GP-WIFI-SCAN > /dev/kmsg`, `cmd wifi start-scan` [src 1007] | yes (log line, a scan) | none needed |
| wifi-trace-pull | none on the phone (dmesg, logcat -d, dumpsys wifi, `dd if=/proc/dhd_trace`) [src 1020-1024]; host: deletes PSK-hit files [src 397] | host deletion is not reversible | see F3; no `dmesg -c` / `logcat -c` is emitted [obs grep] |

Nothing emits rmmod/insmod/svc/setprop/settings/`cmd wifi set-wifi-enabled`/reboot/remount/dd of=/flash [obs grep of the added lines]. The device pin (ANDROID_SERIAL after `ro.product.device == bluejay`) covers `A`, `R`, `S`, `WF`, `WFR` and `adb push` [src 199-202, 204-209, 364, 370], so the new modes cannot hit another phone.

## Open questions (requirement 2)

**(a) P12 / P22b plain `$arg4` register reads: ALLOWED, guard behaviour correct.**
`$argN` is valid only on a function-entry probe (offs == 0), `$retval` only on a return probe: `Documentation/trace/kprobetrace.rst:53-65`, quoted second-hand in docs/research/85-golden-trace-procedure.md:95 [src, not re-read: no kernel tree on this host]. P12 (`dhd_bus_cmn_writeshared`, `type=$arg4:u8`) and P22b (`dhd_send_d2h_ringcreate`, `reqid=$arg4:x32`) are entry probes with no `+OFF` (WF_PROBES lines 135, 149), so the use is legal. A plain register value is not a memory fetch; the guard only restricts a `(`-fetch whose base is `$arg4`/`%x3` [src 294-295]; tests "plain register $arg4:u8 accepted" and the five refusal tests pass [obs]; I did not mutate the `$arg4`/`%x3` case arm itself. arg order for P12 matches h1.. docs/research/golden-wifi-trace-h1b.md:8 `dhd_bus_cmn_writeshared(bus, &wr, 2, type 0x12, idx)` [src].
If the symbol does not exist the kernel write fails; the script prints `probe_fail=EV`, the host raises one GAP per missing event plus the `kprobes_registered=N != 29` GAP [src 983-985], rc 1. LOUD (tested with FAKE_KP_FAIL). If the arg INDEX is wrong the kernel cannot tell (6.1 has no BTF arg typing, [guess]), so the value is silently a wrong register: protected only by the H1 disassembly evidence, not by code. Gap on the guard side: it does not reject `$argN` on a probe with a `+OFF` (the kernel will, loudly, at registration) - harmless.

**(b) P21 offset rule "bytes 8..50 refused": RIGHT for the v1 layout, WRONG (incomplete) for v2/v3, and refusal is loud.**
Evidence in docs/research/golden-wifi-trace-h1b.md:32-33 [src]: v2/v3 `wl_scan_params`: ssid at params+4 (36 B), bssid at +0x28, bss_type +0x2e, pad +0x2f, scan_type u32 +0x30; v1: ssid +0, bssid +0x24, bss_type +0x2a, scan_type u8 +0x2b. With params at buffer+8: v1 SSID/BSSID/bss_type = buffer 8..50 (matches the rule); v2/v3 SSID = 12..47 (inside the rule) but BSSID = 48..53, bss_type 54, pad 55 (51..55 are OUTSIDE the rule). See F2. The refusal itself is loud: `fail` prints `REFUSED:` on stderr and `RESULT: FAIL`, rc 1, before any adb call (tests: all `"1 none"` cases) [obs]. Boundary at the low end is untested (M06 survives, F12). Details of the non-loud edge cases: F7.

**(c) Firmware staged in /data/local/tmp/gp-wfw-* then removed.**
- Removal is guaranteed on: sha mismatch (nothing is pushed), push failure (stage-clean still runs, [src 951-973]; the test does not cover it, M16 survives), a bind GAP (stage-clean runs before the rollback).
- Removal is NOT guaranteed on SIGTERM/SIGINT/SIGHUP/SIGKILL of golden-pull, host crash, or a lost adb connection between the first push and the stage-clean line: there is no trap and no second chance except a rerun of wifi-fw-bind [obs, X2: TERM sent while the 2nd push was running: `stage-clean-sent=0`, 2 pushes logged]. See F1.
- A failed stage-clean (su denied, timeout) is a loud GAP ("no 'stage_clean=ok' marker") [src 365] and the files stay; there is no retry.
- Firmware kept out of git and logs: host copies live in `mktemp -d` removed by the EXIT trap [src 942]; the out dir keeps only sha256 lines (fw-bind.txt, fw-stock-sha.txt, fw-readback.txt) [src 953-968]; the dir is refused if inside a git checkout [src 193]. The firmware itself is never pulled back.
- Size/permission: tmpfs `size=16m` vs files 1,764,018 + 27,062 + 15,932 B [obs `stat` of ~/pixelios-artifacts/wlan-fw/files] fits. Staged files get the host file mode via adb push, owner shell; root reads them [guess]. `chcon $l` on tmpfs, `ls -Z | cut -d' ' -f1` field position and Magisk `su --mount-master` are guesses the header already declares [src 65].
- Not firmware-related but worth stating: the bound firmware is the NEWER 20.101.91 over the stock 20.101.87 (docs/runplans/golden-wifi-trace.md:10) [src]. A dongle trap on it is RAM-only and undone by unbind/reboot [guess]; it is not a flash.

## Findings

### F1 [Medium, condition] wifi-fw-bind leaves firmware copies (and possibly an active bind) on any interrupt; no trap
golden-pull:942 (only trap is `rm -rf "$x"` on the host dir), 951 (push loop), 973 (stage-clean), 975 (rollback). The cleanup is straight-line code after the pushes. [obs X2] a TERM during the second push left 2 pushes logged and zero `rm -f` scripts sent. Same for Ctrl-C or the 600 s/60 s timeouts killing the parent, and for a TERM after the bind but before the readback (bind stays active until the user runs unbind or reboots).
Failure scenario: the driver kills a hung run; /data/local/tmp/gp-wfw-* (userdata, Broadcom firmware) stays on the phone, a later `wifi-fw-bind` then hits `bind_already=1` or the next full run assumes a clean state.
Fix: set `trap 'wf_fw_cleanup' EXIT INT TERM HUP` right before the first push, where `wf_fw_cleanup` sends the stage-clean script (guarded, 60 s) and, if `pushed=1` and a bind may exist, runs `wf_unbind_run`; clear the trap after the normal path. Add a host test with the fake adb sleeping on a push (script X2 in /tmp/20aa/mutm/X1/tools/xtest.sh) that asserts a stage-clean script was sent after kill -TERM.

### F2 [Medium, condition] P21 guard invariant "no SSID/BSSID bytes" is false for the v2/v3 layout; v1 row reads BSSID bytes there
golden-pull:270-277 (rule), 145-146 (rows), tests test_golden_capture.sh:1310 (accepts `+51(%x2):u8`). In v2/v3 (h1b:32 [src]) buffer bytes 48..53 are the BSSID, 54 bss_type, 55 pad. Both P21 rows are armed together at the same site (h1b:40 [src]), so the v1 row's `stype=+51(%x2):u8` and `nprobes=+52(%x2):s32` print BSSID bytes 3..5 + bss_type + pad as integers whenever the live layout is v2/v3 (the expected one, sv==2|3). On a directed scan/connect this is the target AP's BSSID. The tool masks BSSIDs everywhere else (bt_mask, S1 MAC check) while stop-pull's trace is unmasked by design (AP-282), so this lands in an unmasked file. The SSID itself is not exposed in either layout (v2/v3 SSID 12..47 is inside 8..50) [src].
Fix (pick one, decide explicitly): (1) refuse offsets 8..55 in `wf_p21_field` (`off <= 55`) and drop `stype=+51` and `nprobes=+52` from the v1 row (the v1 row keeps ver/act/sync + `active=+56 passive=+60 home=+64 chnum=+68`; in v2/v3 those bytes are scan_type/nprobes/active/passive: harmless); update the tests at 1310 (`+51` must now be refused) and the run plan P21 row; or (2) keep the rows and write in the run plan that BSSID bytes appear in trace-*.txt, and add the BSSID-derived integers to the S1/readme caveats. (1) is the shorter diff and keeps the stated invariant.

### F3 [Medium, condition] S1 PSK scan runs over the whole `--out` dir and deletes any hitting file, including the secret file itself
golden-pull:395-397 (`grep -rlaF -f psk.lit -- "$out"` then `rm -f -- "$f"`), default secret path `$root/.wifi-join` (255). [obs X1] with the secret placed inside the out dir: `GAP: S1 PSK hit: .wifi-join (file deleted, nothing copied)` and the file is gone. `--out ~/pixelios-artifacts/golden` (the root) is a plausible typo/habit and would delete the secret plus every older capture that happens to contain the PSK string (and an 8-char PSK that is a common word could delete innocent captures, [guess]). Also, `grep` exits 2 when ANY file is unreadable even if others matched, and the `*)` arm then raises only a generic GAP and deletes nothing [src 396-399]: the hits are left on disk.
Fix: (a) in `wf_secret_init` refuse (`fail`) when the resolved secret path is inside `$out` (`case "$(realpath -m "$wf_secret")" in "$(realpath -m "$out")"/*) fail ...`); (b) add `--exclude-dir`/skip for the secret path; (c) in the `*)` arm still delete the files in `$lst` when `$lst` is non-empty (rc 2 with output) and say so. Test: copy of X1 asserting rc 1 and that the secret file still exists.

### F4 [Low-Medium] PSK is not part of the mask; it reaches the host disk until S1 deletes the file; CRLF secret file defeats S1
golden-pull:358-359 (`wifi_mask` masks SSID and MAC only), 348 (`psk=$(sed -n 2p ...)` keeps a trailing CR), 350. A pull that contains the PSK is written in full (WFR writes `wifi_mask` output to `$out/wifi/...`), then deleted by S1 at the end of wifi-trace-pull. If the run is interrupted between the pull and S1, or S1 fails with rc 2 (F3), the PSK stays on disk. If `.wifi-join` has CRLF line ends the PSK literal contains `\r` and never matches anything (silent S1 pass) [src 348, guess that the file can be CRLF].
Fix: strip `\r` (`tr -d '\r'`) in `wf_secret_init` and `fail` if a line still has a control character; add a `psk.sed` (literal escaped like ssid.sed, replacement `<psk>`) to `wifi_mask` so the secret is never written, keeping S1 as the verifier. Test: pull with FAKE_PSKLEAK and assert the PSK is already `<psk>` in the file before S1 (and the S1 count 0).

### F5 [Medium, condition] wifi-kprobes has no teardown on failure, and wifi-kprobes-off does not restore the prior cfg80211 state
golden-pull:977-993 vs bind's rollback at 975. With any GAP (a probe_fail, count mismatch, filter read-back) the successful probes stay registered and enabled and `events/cfg80211/enable` stays 1, then the script returns rc 1. The run plan (docs/runplans/golden-wifi-trace.md:85) says "T5 wifi-kprobes (FAIL -> skip to restore ...)" and does not say that the restore runs `wifi-kprobes-off`. Armed probes are RAM and a reboot clears them, but a failed arm followed by the caller continuing the Wi-Fi run is exactly the "unprobed/half-probed run" the plan wants to avoid, and stale probes make the next `wifi-kprobes` fail with EEXIST per event (loud, but noisy). `-off` writes `0` to `events/cfg80211/enable` without knowing whether it was already 1 before [src 314].
Fix: in the wifi-kprobes branch, when `bad > 0` after the checks, send `wf_kpoff_script` (as bind does for unbind) and print "wifi-kprobes: GAPs above, disarmed". Record the previous `cat events/cfg80211/enable` in the arm script (`cfg80211_prev=`) and restore that value in -off (or document that 0 is assumed). Add the "FAIL -> wifi-kprobes-off" step to run-plan T5.

### F6 [Low] P21 rule is keyed on the row LABEL, not on the probed site
golden-pull:293 `if [ "$nm" = P21 ]`. [obs X4] a row labelled `P99` with `wl_do_escan+0xfe4 a=+8(%x2):u32` (ssid_len) is accepted, rc 0. The test at 1312 asserts that `+8(%x2)` is accepted "outside P21" for an unrelated symbol, which is fine, but nothing stops the same site under another label.
Fix: apply `wf_p21_field` when the SYMBOL is `wl_do_escan` (any offset) or `wldev_iovar_setbuf`, i.e. `[[ "$t" == *" wl_do_escan"* ]]`, regardless of `$nm`.

### F7 [Low] An arithmetic error in the P21 guard is not a refusal: `+08(%x2)` prints a bash error and the tool says RESULT: PASS
golden-pull:276 `off=$(( ${BASH_REMATCH[1]} ))`. [obs X3] row `a=+08(%x2):u32` gives `line 277: 08: value too great for base` followed by `RESULT: PASS wifi-kprobes gaps=0 dry=1`. The kernel would reject `08` too (loud at registration), and `+010` is octal 8 in both bash and the kernel's kstrtol(base 0) so that one is refused correctly; the problem is only that "guard could not evaluate" falls through to PASS (rule 8). Also `0xFFFFFFFFFFFFFFF8` wraps to -8 and passes the `off <= 50 && end >= 8` test (the kernel rejects it, ERANGE) [guess].
Fix: tighten the regex to `0x[0-9a-fA-F]{1,4}|0|[1-9][0-9]{0,4}` and write `off=$(( ... )) || fail "$cmd: probe $nm: cannot evaluate offset"`.

### F8 [Low] never-write guard is a denylist with gaps and a loose `rm` exception
golden-pull:260-263. Not matched by `wf_forbid`: `svc`, `setprop`, `setenforce`, `dmesg -c`/`dmesg -C`, `logcat -c`, `cmd wifi set-wifi-enabled`, `mv`, `truncate`, `> /dev/...` (only `of=/dev` and `/dev/block` are listed), `kill`, `ip link`. None is emitted today [obs grep], so this is hardening. The exception `t=${s//"rm -f /data/local/tmp/gp-wfw-"/}` removes the prefix wherever it occurs, so `rm -f /data/local/tmp/gp-wfw-x /data/other` would pass the guard [src 262].
Fix: add `svc |setprop|setenforce|dmesg -[cC]|logcat -c|set-wifi-enabled|(^|[^a-z])(mv|truncate|kill) ` to `wf_forbid`, and replace the prefix exception by an exact match of the single stage-clean string. Tests: loop over every forbidden word and assert REFUSED (today only `reboot` is exercised; M03 `rmmod|insmod` removal survives).

### F9 [Low] `dd if=/proc/dhd_trace` is assumed read-only
golden-pull:1022, header says "READ-ONLY" (75). A proc read handler of a debug-console ring can consume (advance a read pointer) [guess]; nothing in the cited sources says it cannot. Fix: before the first run read the `dhd_trace` proc read handler in the module source/disassembly (tools/rea) and, if it consumes, say so in the header and move the pull before `wifi-kprobes-off` or read it once only. At minimum change the header word from READ-ONLY to "read, may consume [guess]".

### F10 [Low] Scan-result SSID column mask: byte counting, untested edge
golden-pull:357 `.{45}` / `.{32}` assume 64 chars before the SSID (ScanResultUtil.dumpScanResults format, quoted from android14-release, [guess] for BP31). `sed -E` counts bytes in the C locale and characters in UTF-8, while the Java formatter counts UTF-16 chars: a non-ASCII SSID shifts the columns. A format mismatch is caught as "unparseable line" only when the row also fails `^  <masked>  .*<ssid-masked>` (it would not for a shifted-by-one mask). [obs M20] changing `.{45}` to `.{44}` survives because the fake SSIDs are all shorter than 31 characters.
Fix: add a 32-character SSID and a multibyte SSID to the fake scan and assert the SSID text is fully masked; make the mask robust by anchoring on the two-space gap before the flags column instead of a fixed width, or mask by position with `LC_ALL=C` plus a post-check that no row keeps more than the expected number of non-space chars before `<ssid-masked>`.

### F11 [Low] S1 only matches the literal SSID/PSK; hex-encoded forms in logcat/dumpsys are not checked
golden-pull:393-415. wpa_supplicant/wificond can print the SSID as hex (`ssid=4d79...`, hexdump lines) [guess]. Fix: also grep (`-i`) the hex encoding of the SSID (`printf %s "$ssid" | od -An -tx1 | tr -d ' \n'`) and the colon-separated form; PSK-derived values (PMK) are redacted by wpa_supplicant by default [guess].

### F12 [Low] Test gaps: surviving mutants (38 run, 26 killed, 12 survived)
Phone-side bind/unbind scripts are never executed by a shell in the test (only kprobe scripts are, test_golden_capture.sh:1077-1086); the fake adb answers from `$WSTATE`. Hence:
- M15 unbind without `--mount-master` survives (only the bind and readback are counted at 1227).
- M17 removal of the `bind_already` branch inside the phone script survives (the fake emits `bind_already=1` itself).
- M18 removal of `chmod 0444` survives.
- M27 removal of the stock-sha format check survives.
- M16 stage-clean only if pushed survives (the push-failure test checks `--mount-master` only; the staged files of a partial push stay on the phone in that mutant).
- M12 removal of `wf_guard` in `WFR` survives (the forbidden-word test uses the scan script only).
- M13 (no quote check in the probe guard), M14 (no filter charset check), M38 (no `wf_guard` of probe text/filter inside `wf_probe_guard`) survive: no test feeds a quote, a bad filter character or a forbidden word through the table.
- M03 (`rmmod|insmod` removed from `wf_forbid`) survives; only `reboot` is tested.
- M06 (`-ge 9` instead of `-ge 8`) survives: no test fetches a field that ends exactly at byte 8 (e.g. `+5(%x2):u32` must be refused).
- M20 (scan column `.{44}`) survives (F10).
Fix: (1) execute the generated bind/unbind/readback scripts with `sh` against shim `mount`, `umount`, `chcon`, `ls`, `sha256sum`, `cp`, `mkdir` and a fake `/proc/mounts` file (rewrite paths like the kprobe fake does); (2) add the tests named above; (3) add a test that sends SIGTERM during a push (F1).
Killed (for the record): M01, M02, M04, M05, M07-M11, M19, M21-M26, M28-M30, M33, M40-M45 (26).

### F13 [Info] The new test needs the gitignored `tools/third_party`
test_golden_capture.sh (existing line, `GAP: tools/third_party missing (tools/fetch-android-tools.sh)`) [obs]: in a fresh/detached worktree the whole file ends `RESULT: FAIL ... failures=1` although every wifi check is ok. The driver's gate copy must have `tools/third_party` (symlink or fetch) or it will read this as a wifi failure.

### F14 [Low] No module identity check before arming offset probes
golden-pull:144-146 (`dhd_get_fw_capabilities+0x80`, `wl_do_escan+0xfe4`) are offsets derived from one disassembly (h1b:25, 36 [src]). wifi-trace-probe checks only that the symbols exist in kallsyms [src 929], not that the loaded `bcmdhd4389` is the same build. A different build puts the probe on another (still 4-byte-aligned, legal) instruction; the kernel cannot detect it, the values would be garbage and, for P21, `%x2` could be a different buffer [guess]. No crash expected (kprobe fetches are fault-safe) [guess].
Fix: in wifi-trace-probe record `sha256sum` of the loaded .ko (path from `/proc/modules`/`modinfo`) and GAP if it differs from the sha of the module that was disassembled (add a `WF_KO_PIN`), and have it printed in the run summary.

## Input validation (requirement 3)

- Mode args: `wifi-fw-bind` takes exactly one existing directory, used only on the host with quoted expansions [src 179, 944-948]; no other new mode accepts an argument [src 169]. Nothing a user types reaches an adb string: out dir, DIR and GP_WIFI_SECRET stay on the host [src 255, 191-203]. No shell-injection path found.
- SSID/PSK never go to the phone or argv; literal files are mode 600 (`umask 077`, tested with a grep wrapper, M09 killed) in a mktemp dir removed on EXIT (M10 killed). The EXIT trap also runs on TERM/HUP [obs: a bash script with the same trap ran it on both]. SIGKILL leaves the temp dir (mode 700) [guess].
- Probe table texts, event names, symbols, filters: whitelisted by regex and per-field regex, `'` refused, filter charset fixed [src 278-302]. The unquoted `for fe in $f` (288) globs; a glob that expands to a file name in the cwd could replace a field before validation - only reachable by editing the table, not by arguments [guess]. Fix if desired: `set -f` around the loop.
- Table probe texts go to the phone inside single quotes in a stdin script (`echo '$t' >> ...`) [src 305] and cannot break out because `'` is refused.

## Test evidence (requirement 4)

- Baseline: PASS, 1950 ok [obs].
- shellcheck -S warning: clean on both files [obs].
- Mutation table above and F12. Scripts: /tmp/20aa/mutm/run.sh, muts.txt, muts2.txt, logs in /tmp/20aa/mutm/M*/log.
- Claims in the test file that I checked as sound: it holds its own copy of the never-write list and the probe count 29 (independent oracle, test_golden_capture.sh:1171, 1176); pins are compared against literals (1179); S1 modes check file mode 600 (1368-1370).

## Suggested antipattern entry (not written: this brief allows only this file)

"A fake adb that answers a phone-side script from its own state (`bind_already`, `unbound_*`) instead of executing the script lets any mutation of that script survive; execute generated phone scripts with shimmed commands, as is already done for the kprobe scripts." (family of AP-124/125)
