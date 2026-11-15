---
id: TP-045
tool: "tools/xrun"
status: building
proposed_by: "Opus 5.5 (driver 21e)"
date: 2026-10-29
exposed_by: "EL2 B2 run plan docs/runplans/el2-2-xnu.md (SPEC-EL2-PROXY-PROBE 3.4 (b) item 6): no built tool can boot XNU on a shim built with --el2-trap"
extends: "tools/xrun"
related: "TP-041"
usage: "tools/xrun --el2-trap --cpus 1 --kernel K --args \"sweetel2=1 ...\" --intent el2 [the usual xrun flags]"
refs: "-"
---

## checked
**Caller:** the EL2 B2 run plan `docs/runplans/el2-2-xnu.md` (super repo): `tools/xrun --el2-trap --intent el2 ...`. **Replaces:** nothing (extends tools/xrun).
`tools/tool-proposals check el2` = NONE; `check build-xnu-image` = EXISTS, no proposal. `grep -rli el2-trap tools docs` on trunk: nothing.
[read from source] `tools/build-xnu-image.sh:55` runs `shim/build.sh --board bluejay --avb --boot-xnu "$p" rails/dvfs/handover args` with no way to add `--el2-trap`.
`tools/probe-run --build-args` (used by el2-1-s2trap) passes flags to build.sh, but it is the shim-only probe runner: no XNU kernel, no ADT, no UDP log, no userdata boot.
The B2 build mode `build.sh --el2-trap --boot-xnu` (defines EL2_RESIDENT) exists only on kernel branch el2/b2-report (90bd124; bec6d6b refuses
every --gpu-*/--wlan-*/--touch-probe/--udump/--qrlog companion). So no tool fits; this is a flag on xrun + build-xnu-image.

## why
Without it the B2 run (the only remaining EL2 B6 step) can only be done by hand: build the shim by hand, then hand-fboot it. Hand-typed fastboot
is forbidden (AGENTS.md Tools), and it loses the lock, preflight, usb-port check, boot-args budget, run summary and --expect/--reject handling.
So the cost is not minutes. Today the run cannot be done at all within the rules.

## output
Unchanged xrun report (`RESULT: PASS|FAIL|ERROR ...`, exit 0/1/2, run dir under logs/). Added: one line `EL2: resident (build.sh --el2-trap --boot-xnu) shim sha256 <16 hex>`
in the run summary, and in --dry-run / --print-bootargs output.
Refusals (exit 2, before build, lock or phone): `--el2-trap` with `--gpu-handover`, `--wlan-hold`, `--rails`, `--dvfs`, or `--cpus` != 1
(spec 3.4 (b) item 5 (ii): 0180 needs cpus=1); `--el2-trap` without `sweetel2=1` in --args; `--el2-trap` when the checkout's build.sh has no
EL2_RESIDENT (one grep, message names el2/b2-report). run-intents.json: new intent `el2` requiring `--el2-trap`, `--cpus 1` and `sweetel2=1`.
Host test: the refusals + a dry-run that shows the build.sh argv contains `--el2-trap --boot-xnu`; a mutant that drops the passthrough fails it.

## risks
Touches the phone through the existing xrun path only (fboot of a RAM image; no partition write beyond what xrun already does). The flag changes
which shim boots: an EL2_RESIDENT shim keeps stage 2 + VBAR_EL2 live under XNU. A park in EL2 hangs the phone until Power+VolDown (spec 3.4
DECISION reason for (b)), so the run plan, not the tool, decides attended vs unattended. Must never: add --el2-trap to a default build, or pass it with
any companion flag that build.sh refuses (the tool refuses first, so build.sh's refusal is the backstop).

## amendments
- 2026-10-29 driver 21e (from 20e's acceptance): the `el2` intent is also printed in the xrun RESULT line and the run summary, so a B2 run can never look like an ordinary XNU run. Before merge: Sonnet adversarial review (refusal list, EL2_RESIDENT detection, summary line), tool-census lifecycle row (- -> active, caller super docs/runplans/el2-2-xnu.md), 20e runs the host test + mutant. Merge order 043 -> 044 -> 045, after batch-20d.
- 2026-10-29 implementer (branch tools/xrun-el2-trap): built, unreviewed: refusals in tools/xrun, passthrough in tools/build-xnu-image.sh (build.sh argv printed under XNU_IMAGE_ARGS_ONLY only with --el2-trap), intent `el2` (+ `require_opts` in tools/run-preflight c_intent), `intent=el2` on RESULT and `EL2:` line in tools/xrun_report.py; host test tools/test_xrun_el2.py.
