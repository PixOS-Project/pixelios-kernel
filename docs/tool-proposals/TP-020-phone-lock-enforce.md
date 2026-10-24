---
id: TP-020
tool: "tools/phone-lock"
status: done
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-24
exposed_by: "user request 2026-10-24 after the GPU agent offered the phone and asked for a 35 min slot: the lock was held across builds and nothing enforced it"
extends: "TP-016"
related: "TP-018"
usage: "tools/phone-lock check [--token T]; xrun/probe-run --lock-wait S; tools/phone-guard (hook)"
refs: "kernel f1f1d04, f546f83 + review-12 fixes; reviews 11 (UNSAFE, fixed) and 12 (SAFE WITH CONDITIONS, applied); .claude/settings.json hook in the super repo"
---

## checked
Read `tools/phone-lock`, `probe-run`, `xrun`, `fboot.sh`, `fflash-userdata.sh`, `phone`. Extends TP-016 (the lock itself, done) and touches TP-018 (`tools/phone`). `tools/tool-proposals check lock` finds only TP-016. Review findings: (1) `phone-lock run -- probe-run|xrun` holds the lock across the build; (2) no device tool checks the lock; (3) `stale()` ended a lock at the ttl even with the holder pid alive (default 1800 s < the 2100 s GPU slot), so a live flash could be taken over; (4) waiters poll with no order; (5) the gate and probe-run builds share `shim/out`.

## why
The phone sat locked and idle for the build of every run (a shim build is ~15-70 s, a kernel image build + mkadt more) and for gate runs wrapped in `run`; agents coordinated by message instead of a queue (the GPU agent's 'tell me when a long slot is free'). A live holder could lose the lock after 30 min. Raw device commands bypassed both lock and bluejay check.

## output
`phone-lock check` prints the reason and `RESULT: PASS|FAIL owner=N held_for=Ns`, exit 0 only if the caller's token (`$PHONE_LOCK_TOKEN`, exported by `run`, xrun and probe-run) is the live lock's; the owner name is only a label (review 11 F1/F2). Guarded tools exit 1/2 with `REFUSED: the phone lock is not held ...`. xrun/probe-run report `ERROR phone lock busy: held by X ... queue=...`. `phone-guard` is a PreToolUse hook: a deny decision with the reason, never an error. Wired in the super repo's `.claude/settings.json`.

## risks
No device touched by the lock code. Behaviour changes for other agents: fboot/fflash/phone goto/run-probe/pstore-pull run refuse without the token (wrap manual slots in `phone-lock run`); a stuck live pid now keeps the lock until `force-release --yes` (the ttl no longer frees it). The hook blocks hand-typed mutating device commands (quoted text and heredoc bodies are ignored; an unbalanced quote is judged as far as it parses); read-only queries pass. Hooks load at session start: running sessions are not guarded until restarted.

## amendments
- 2026-10-24 Claude Sonnet 5.5: review 11 (`docs/reviews/11-phone-lock-enforce.md`, UNSAFE on 97f666a: no-owner check passed any live lock, shared owner names, SIGKILLed `run` freed the phone, xrun released early) fixed: token identity, process-group liveness, mutex-verified stale takeover, queue deadline, xrun keeps the lock to exit, guards in run-probe.sh and pstore-pull.py, wider phone-guard. Re-review 12: SAFE WITH CONDITIONS, conditions applied.
- 2026-10-24 Claude Sonnet 5.5: follow-up on branch `tp-020-phone-lock` (own worktree): false 'hold Power+VolDown' verdicts (probe-run 30 s wait, xrun immediate USB check, xrun hard-timeout FAIL while the reset fired) and cut log tails (usb-log.py, usbnet-log.py) fixed with tests that fail on the old code (xrun 120 s USB wait and the BACK default are untested); Opus review 13 applied (docs/reviews/13-false-alarms-and-log-tails.md); see docs/AUTOMATION.md.
