# Review xrun-el2-b3-r1: tools/xrun --el2-b3 (kernel branch tools/xrun-el2-b3, commit 879b068)

Reviewer: Sonnet 5.5, adversarial, 2026-10-29. Read-only on the worktree; mutants and probes ran in /tmp/rv copies. No phone command was run.
Tags: [obs] = run on the host now, [src] = read from source (file:line), [guess].

## Verdict: SAFE WITH CONDITIONS

The B2 path is unchanged, every refusal exits 2 before any build, lock or phone step, and the tests catch the mutants that matter. One real evasion of the PMU-page refusal (F1, a newline in `--args`) must be closed before a B3 run is called "gated". Nothing found that can write hardware by itself; the risk is a B3 run with `sweetgpu`/`sweetwlan` that the tool claims to forbid.

## Checks (all [obs] unless noted)

- a. Ordering [src tools/xrun:39-60 on the branch]: option loop, then the el2 block, then the `--dry-run` exit, then `run-preflight --intent-check`, then CAM_HOST, kernel check, usb-port, userdata check, build, lock. Nothing before the el2 block touches hardware (lines 28-39 are comments and `cd`, `A=`, `R=`). Refusal matrix run through `--print-bootargs`, `--dry-run`, `--kernel /nonexistent` (tests/refused() does all four): exit 2 plus `RESULT: ERROR`. Hand probes of 12 `--args` shapes below.
- b. Token matching, see F1/F2 for the two disagreements. Held: double space, tab, `sweetel2=2,sweetwlan=1` (refused as "must be sweetel2=2"), NBSP between tokens (bash does not split it, so the merged token is refused as "must be sweetel2=2"). Not matched (correct, XNU needs a space-preceded key [guess, not checked in XNU source]): `foo=sweetgpu=1`, `"sweetgpu=1"` (leading quote), `-sweetgpu`, `SWEETGPU=1` (XNU keys are case sensitive [guess]).
- c. B2 byte-for-byte: 10 cases x {`--print-bootargs`, `--kernel /nonexistent`} through `git show trunk:tools/xrun` (+ trunk build-xnu-image.sh, run-preflight, run-intents.json) vs the branch with the same B2-only build.sh: identical stdout+stderr+rc in all 20 (good, sweetel2=2 refused, duplicates, cpus 8, intent none, no args, --rails, no --el2-trap, no flags at all, --dry-run). The `want` variable keeps the B2 messages `must be sweetel2=1` / `exactly one sweetel2=1 in --args (found N sweetel2= tokens)` verbatim.
- d. Mutation (copies under /tmp/rv, original untouched), tests = tools/test_xrun_el2.py:

| Mutant | Caught by |
|---|---|
| PMU check disabled (all four prefixes) | test_b3_refusals |
| only `sweettouch*` / only `sweetmali*` dropped | test_b3_refusals (each) |
| `--el2-b3` dropped from the `bxi` array in xrun | test_b3_good_passes_both_flags_to_build_sh |
| `el2arg+=(--el2-b3)` dropped in build-xnu-image.sh | test_b3_build_xnu_image_backstop + test_b3_good_passes... |
| EL2_B3 grep dropped in xrun | test_b3_refusals |
| EL2_B3 grep dropped in build-xnu-image.sh | test_b3_build_xnu_image_backstop |
| `want` fixed to 1 | test_b3_good... + test_b3_refusals |
| "--el2-b3 needs --el2-trap" dropped in xrun | test_b3_refusals |
| same, in build-xnu-image.sh | test_b3_build_xnu_image_backstop |
| `single_args_with` ignored in run-preflight | test_intent_el2_b3_wants_sweetel2_2 |
| PMU loop looks only at the first token | test_b3_refusals |
| **mark text `el2="resident (build.sh --el2-trap --boot-xnu)"` (B3 flag dropped from the run record, xrun:127)** | **nothing: 18 tests OK (F4)** |

- e. `shellcheck -S warning tools/xrun tools/build-xnu-image.sh`: clean (rc 0). `tools/run-intents.json` parses. `python3 tools/test_xrun_el2.py`: `Ran 18 tests`, `OK`. `python3 tools/test_run_preflight.py`: `Ran 71 tests`, `OK`.
- Real build.sh: on kernel branch el2/b3-trap (/home/<user>/.t3/worktrees/el2-b3-trap, shim/build.sh:235) the line is `\t\t\tCFLAGS="$CFLAGS -DEL2_B3"` inside `if [ "$el2_b3" = 1 ]` under `--el2-trap --boot-xnu`; the regex `^[[:space:]]*CFLAGS="\$CFLAGS -DEL2_B3"` matches it, and `--el2-b3` there is `build.sh:69` and passed in this position by build-xnu-image.sh:`bargv` (`--board bluejay --avb --el2-trap --el2-b3 --boot-xnu`). The trunk shim/build.sh in this worktree has no EL2_B3, so the precondition refuses until the merge, as the spec wants.

## Findings

### F1 (Medium) A newline in `--args` evades the PMU-page refusal and the sweetel2 count
- Where: tools/xrun (the `read -ra at <<< "$args"` in the sweetel2 loop and again in the new PMU loop, both inside the el2 block).
- Why [obs]: `read` stops at the first newline, so only line 1 is vetted. Probe, B3 mode with a valid build.sh:
  `tools/xrun --el2-trap --el2-b3 --intent el2 --cpus 1 --print-bootargs --args $'sweetel2=2\n sweetgpu=1'` exits 0 and prints `boot-args: ... sweetel2=2` newline ` sweetgpu=1 sweetremote=1`. The same string with a space or tab instead of the newline is refused (`--el2-b3: boot-arg sweetgpu=1 is refused`).
  `tools/run-preflight --intent-check el2` passes the same argv too (its `" ".join(...).split()` sees both tokens, but the el2 intent has no PMU rule), so no later layer catches it. build-xnu-image.sh does not reject it either.
- Failure scenario: a scripted or copy-pasted multi-line `--args` (a heredoc, a quoted block) yields a B3 run with `sweetgpu=1` on the 0x17460000 page the stage-2 trap table is built around, past a refusal that the report and the review el2-b3-design-r1 F10 say exists. Whether XNU treats ` sweetgpu=1` after a newline as a key is a [guess] (space-preceded, so probably yes; isargsep not checked in the XNU source here). The pre-existing B2 sweetel2 count has the same blind spot, but there the python intent check also counts, so only the PMU rule is exposed.
- Fix (xrun, el2 block, right after `el2err=""`, B2 and B3 alike): refuse anything but graphic characters and plain spaces:
  `[ -z "$el2err" ] && case $args in *[![:graph:] ]*) el2err="--el2-trap: --args may hold only printable characters and plain spaces (a tab/newline/NBSP splits differently in bash read and in run-preflight)" ;; esac`
  and add to test_b3_refusals (and a B2 twin) `for a in ("sweetel2=2\n sweetgpu=1", "sweetel2=2\tsweetgpu=1", "sweetel2=2\xa0sweetgpu=1")`. Note the existing tab case is only refused because bash splits it, not because of a rule; the test should pin the rule.

### F2 (Low) The two token views are not the same function
- tools/xrun:56-60 (`read -ra`, splits on space/tab/newline, first line only) vs tools/run-preflight:`c_intent` (`str.split()`, all Unicode whitespace). [obs] `sweetel2=2<NBSP>sweetgpu=1`: xrun refuses (one merged token), intent-check passes; `sweetel2=2\n sweetgpu=1`: xrun passes, intent-check passes. Currently harmless because xrun runs first and the intent rule is only sweetel2; it becomes a bug the day a rule lives only in one layer (the PMU rule already lives only in xrun: `--intent-check` and a direct `tools/build-xnu-image.sh --el2-trap --el2-b3 --args "sweetel2=2 sweetgpu=1"` both accept it [obs, rc 0, argv printed]).
- Fix: either state in the el2 block comment that xrun is the only place for the PMU rule, or add `"forbid_arg_prefixes_with": {"--el2-b3": ["sweetgpu","sweetwlan","sweetmali","sweettouch"]}` to run-intents.json and check it in c_intent next to `single_args_with`, so `--intent-check` and tools that call it agree. The F1 fix plus one shared definition removes the disagreement.

### F3 (Low) build-xnu-image.sh backstop does not repeat the PMU rule or the sweetel2=2 rule
- tools/build-xnu-image.sh:38 checks only "needs --el2-trap" and the EL2_B3 grep [obs: direct call with `--args "sweetel2=2 sweetgpu=1"` returns 0]. tools/xrun is the only entry that is guarded. Every B3 run is meant to go through xrun (run plan docs/runplans/el2-4-b3.md), so Low; the backstop is explicitly named "backstop of tools/xrun" and the same applies to the existing B2 rules.
- Fix (optional): add `case " $extra " in *" sweetgpu"*|*" sweetwlan"*|*" sweetmali"*|*" sweettouch"*) echo "--el2-b3: ... PMU page" >&2; exit 2 ;; esac` guarded by `[ "$el2b3" = 0 ] ||` before the `args=` line; test beside test_b3_build_xnu_image_backstop.

### F4 (Low) The B3 marker in the run record is untested, and RESULT does not say B3
- tools/xrun:127 (`$R mark "$D" el2="resident (build.sh --el2-trap${el2b3:+ --el2-b3} --boot-xnu) ..."`) is only reachable on a real run, so the host tests never execute it (mutant: dropping the flag there passes all 18 tests). The RESULT line is unchanged as asked (`${el2:+intent=el2}`, tools/xrun:112), so a B2 and a B3 run differ only in that mark and in the `EL2:` line; the sweetel2=2 in `boot_args=` is the other tell.
- Failure scenario: a B3 run is later read as a B2 run because the mark regressed; nothing fails.
- Fix: give the mark text a helper (one variable `el2desc="build.sh --el2-trap${el2b3:+ --el2-b3} --boot-xnu"` used by both the `--print-bootargs` echo and the mark) so the tested line and the recorded line cannot differ. TP-045 (docs/tool-proposals/TP-045-xrun-el2-trap.md:28) still documents only the B2 text of the `EL2:` line.

### F5 (Info) test_b3_refusals is one test with ~20 sub-cases
- A failure names the test, not the case; `refused()` has no `subTest`. All mutants above were still caught, so Info only. Fix: wrap each loop iteration in `with self.subTest(a=a):`.

### F6 (Info) Prefix list rests on one source
- `sweetmali*` and `sweettouch*` are in the brief but not in the G1 literal grep of SPEC-EL2-PROXY-PROBE (only `sweetgpu`, `sweetwlan` have page-A literals [src docs/specs/SPEC-EL2-PROXY-PROBE.md G1]; G11 lists `sweetgpujob` too, covered by the prefix). Extra refusals are safe. The G1 grep must be repeated by the implementer on the rebuilt kernel (the spec says so); if a further arg appears there (for example a new `sweetrst`-style key) this list needs it. [guess] no other known arg in tools/bootargs-known.txt touches PMU page A.

## Antipattern
Added AP-245 to docs/ANTIPATTERNS.md in the super repo (`read -ra <<<` vets only the first line of a multi-line boot-arg string).
