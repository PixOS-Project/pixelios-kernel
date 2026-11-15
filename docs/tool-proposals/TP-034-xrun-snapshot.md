---
id: TP-034
tool: "tools/phone-lock run --snapshot"
status: proposed
proposed_by: "claude-opus-5-5 (driver, mutants-speed thread)"
date: 2026-10-27
exposed_by: "2026-10-27 19:03Z: the merge of tools/gate-container-fixes into trunk (no phone tool changed) was stopped by the watcher brief's 'phone lock held -> stop' step while g104 ran; landed only at 77e7d43 after the run"
extends: "tools/phone-lock"
related: "TP-032"
usage: "tools/phone-lock run --snapshot --owner NAME -- tools/xrun ARGS   # runs ARGS from a frozen copy of tools/ at HEAD"
refs: "-"
---

## checked
`tools/tool-proposals check snapshot|frozen|phone-lock run`: RESULT NONE for all three. agent-guard E8b (tools/agent-guard r_e8b) already lets a
`git merge` in the shared clone through when `git diff HEAD TARGET` leaves PHONE_TOOLS unchanged, but PHONE_TOOLS is only 7 files
(probe-run, xrun, fboot.sh, plock.sh, phone-lock, phone, fflash-userdata.sh). [src, grep of `tools/NAME` references] xrun and phone also call
to-fastboot.sh, phone-state, usbnet-log.py, sweet-usbnet.sh, run-preflight, xrun_report.py, mkadt.py, imgmeta.py, envfile.py, run-intents.json,
hang-recover, usb-log.py, sweet-reset-bl; the text closure covers most of tools/ (comments included), so growing PHONE_TOOLS would either miss a helper
or block every merge. Hence the briefs' blanket lock check, which is safe but serializes all trunk merges behind phone runs.

## why
A phone run takes 7-25 min (g104 held the lock 414 s of a 1500 s TTL when the merge stopped) and the GPU stream runs them back to back, so trunk merges
(tools, gate fixes) wait for a free window or need a retry delegate (2 Haiku delegates for one merge on 2026-10-27). With the run executing a frozen
copy, the shared clone can be merged at any time; E8b and the lock check in merge briefs can go.

## output
`phone-lock run --snapshot` makes `git worktree add --detach $RUN_DIR/tools-snap HEAD` (or `git archive HEAD tools shim/out/... | tar -x`), prints
`SNAPSHOT <sha> <path>`, runs the command with cwd in the snapshot, removes it after the run (kept on failure for TP-032 postmortem). Last line of the
wrapped command unchanged; extra line `RESULT: PASS snapshot=<sha>` / `RESULT: ERROR snapshot failed` (exit 2, command not started). Log in the run dir.

## risks
Touches the phone only through the wrapped command (unchanged). Must never run a snapshot of a dirty tree without saying so (print `dirty=N`, or
refuse). Artifacts the tools read from the shared clone by absolute path (logs/, .env, shim/out builds, tools/third_party) must still resolve:
the snapshot needs those as symlinks or the run's paths must stay absolute; a wrong path must fail loudly before the phone is touched (rule 8).
Needs an Opus review (hardware-path tools).

## amendments
(none)
