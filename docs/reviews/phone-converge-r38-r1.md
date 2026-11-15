# Review phone-converge-r38 r1 (commit ac2bbba vs trunk cfaff26)

Reviewer: Sonnet 5.5, adversarial, read-only, no phone. Tags: [obs] = run now on the host, [src] = read from source (file:line), [guess].
Scratch: /tmp/20ab/r38-scratch (probe.sh = the real `agent-guard --pre` entry; t1/ = copy of tools/ for lint mutants; mut/ = agent-guard mutants).

## Verdict: SAFE WITH CONDITIONS

Nothing here writes to the phone or a partition (host-side policy code, one tool's pre-flight swapped for the existing `tools/phone goto fastboot`).
The conditions are about enforcement strength (AP-291 / R42 "hard automatic"): the lint as written cannot tell the fixed `probe-run` from the
unfixed one (F1), and two blind spots let a real run path through (F2). Fix F1 + F2 + F3 before relying on this as "enforced"; F4-F8 are cheap follow-ups.

Verification [obs]: `python3 -I tools/test_lint_phone_converge.py` -> `Ran 3 tests ... OK`; `tools/lint-phone-converge` -> `RESULT: PASS run_paths=21 hits=40 findings=0`;
`tools/lint-phone-converge --self-test` PASS; `tools/agent-guard --self-test` -> `RESULT: PASS fixtures=323 rules=41 mutants=82 survived=0 failures=0`;
`tools/probe-run --self-test` -> `self-test PASS`; `tools/test_gate_select.py` -> 29 tests OK; `tools/tool-census --check` -> `RESULT: PASS`;
`shellcheck -S warning` on tools/probe-run, run-probe.sh, check-all.sh, test_check_all_stub.sh, xrun: no findings (the diff has no other shell file;
lint-phone-converge / agent-guard are python).

## Findings

### F1 (HIGH) lint allowlist is per file, so it cannot tell the fixed probe-run from the original one that caused AP-291
- Where: tools/lint-phone-converge:84-89 (`if rel in allow: ... continue`), tools/phone-converge-allow.txt:8 (probe-run), :11 (xrun), :6 (hang-recover).
- Scenario [obs]: I put the pre-fix `tools/probe-run` (`git show cfaff26:tools/probe-run`, four `$PS --expect fastboot ...` pre-checks + fallback) into the scratch tree:
  `RESULT: PASS run_paths=21 hits=46 findings=0`. The allowlist entry for probe-run swallows every hit, and the pre-flight calls the checker through the
  variable `$PS` (tools/probe-run:16 `PS=${PR_PHONE_STATE:-tools/phone-state}`), which the literal regex `phone-state` never sees except on line 16. So the
  one file R38 was made for is exactly the one the lint cannot guard. The same holds for xrun (a new `adb get-state` gate next to the diagnostic at xrun:185 is swallowed)
  and for agent-guard / phone-guard / phone (15, 2, 5 hits swallowed).
- Fix (minimal): make an allow entry carry a pin and fail on any change: `path  hits=N  reason`, and report `ALLOW-GREW path has M hits, allowlisted N` when M != N
  (also covers STALE). Current pins from my count [obs]: abl-dmesg 3, agent-guard 15, fboot-android 2, hang-recover 1, lint-phone-converge 10, phone 5, phone-guard 2,
  probe-run 1, xrun 1. With `probe-run hits=1` the pre-fix file (7 hits) fails. Also add to HIT the indirect form: `\$\{?PS\}?\s+--expect` or, simpler, make the
  lint reject any `--expect (fastboot|recovery|android)` on a run path outside the pinned lines. Add two rows to tools/test_lint_phone_converge.py: (a) pre-fix probe-run text fails,
  (b) a second `adb get-state` added to xrun fails.

### F2 (MEDIUM) lint blind spots: `adb devices`, non-executable .py run paths, python list form
- Where: tools/lint-phone-converge:34 (HIT lacks `adb devices`), :46 (`os.access(p, os.X_OK) or n.endswith(".sh")` skips a non-executable .py).
- Scenario [obs]: (a) appending `adb devices | grep -q device` to tools/fboot.sh in scratch -> `PASS ... findings=0`, although agent-guard E24 denies exactly that on the
  agent's line (the two disagree). (b) `tools/pstore-pull.py` is a lock-taking run path (it calls `phone-lock check` at :164, `fastboot boot` :182) with a polling
  `["adb","devices"]` loop (:193) and a `["fastboot","devices"]` loop (:268), mode 0664, so it is never scanned; a new non-exec `zz.py` with `phone-lock` + `fastboot devices` -> PASS,
  after `chmod +x` -> FAIL. (c) Python list form `subprocess.run(["adb","get-state"])` appended to sparsediff.py (a scanned run path) -> PASS. (d) `tools/pix:139`
  `"phone": wrap("phone-state")` + help line 11 is an agent-facing way to run phone-state (see F8).
- Fix: HIT += `\badb(?: +-s +\S+)? +devices\b`; scan every top-level file that starts with a `#!` shebang or ends `.sh`/`.py` (not test_*); add a list-form pattern
  `["']adb["'],\s*["'](get-state|devices)["']|["']fastboot["'],\s*["']devices["']`. Then either convert pstore-pull.py's two waits to `tools/phone goto` or allowlist it
  with `hits=2` and the reason "post-run wait for the shim reset, not a pre-check". Add mutant rows for (a) and (b) to test_lint_phone_converge.py.

### F3 (MEDIUM) probe-run hides goto's user prompt: the old actionable hint is gone from the ERROR
- Where: tools/probe-run:112-113 (`timeout 150 $PH goto fastboot --timeout 120 >"$dir/goto.log" 2>&1 || report ERROR "... $(grep '^RESULT:' ...)"`).
- Scenario [src]: tools/phone:336-340 `emit_user_prompt` prints `NEED USER: hold Power+VolDown` / `check cable` (tools/phone:157-176, absent/hung rows) to stdout, and make_report prints
  extra lines such as `Timeout exceeded` (tools/phone:349-355) before RESULT. probe-run redirects all of it into goto.log and the ERROR carries only
  `RESULT: FAIL state=absent target=fastboot steps=1` (the old text said "reboot it to the bootloader (hold Power+VolDown)"). The user sees an ERROR with no instruction (rule 8: the reason must be loud).
  The stub in selftest (`start-absent-msg`) only checks the RESULT part, so this is untested.
- Fix: in the ERROR use the last 3 lines of goto.log: `$(tail -3 "$dir/goto.log" | tr '\n' ' ' | cut -c1-240)` (it holds NEED USER / Timeout exceeded / RESULT); if the exit is 124
  (timeout killed goto, no RESULT line) say so: `goto fastboot killed after 150 s`. Extend the stub with an `echo "NEED USER: hold Power+VolDown"` line and assert it appears in the ERROR.
  Also emit the goto result on success into report.txt (one line) so a converge that took steps is visible.

### F4 (LOW) phone-guard still tells agents the opposite of E24
- Where: tools/phone-guard:5 (docstring) and :19 (WHY text shown on a phone-guard deny): "Read-only queries (fastboot devices|getvar, adb devices|get-state) are allowed";
  tools/test_phone_guard.py:16-17 pins `fastboot devices`, `adb devices -l`, `adb get-state` as ALLOW.
- Scenario [obs]: both hooks are registered on PreToolUse Bash (.claude/settings.json:9 and :28). phone-guard allows `adb get-state`, agent-guard denies it with E24, deny wins.
  After a phone-guard deny on e.g. `adb reboot`, its WHY text then advises a query that E24 denies. Judgement of the author's gap: acceptable with the allowlist entry (the lint hit is
  rule text only), but the text must not contradict.
- Fix: keep phone-guard's allow behaviour (it guards writes), change line 5 and the WHY (19) to: "Readiness queries (fastboot devices, adb devices|get-state) are not blocked here but agent-guard E24 denies them: use tools/phone state." Update the test comment, not the ALLOW rows.

### F5 (LOW) E24 deny message: why + do-instead yes, escape hatch missing; no test pins message parts (R42)
- Where: tools/agent-guard:567-570. Real output [obs]: `agent-guard E24: hand readiness check `adb get-state` (AP-291, user R38): the run tool converges the phone: just run it (tools/phone goto STATE for a state without a run); `tools/phone state` and `tools/phone-lock status` only observe`.
- Gap: R42 asks for "escape hatch if any". There is none stated for the agent, and a hit in a script is the lint allowlist, not this hook. Say so.
- Fix: append `; no bypass on the command line: if a tool needs it, list the tool in tools/phone-converge-allow.txt with a reason (the lint), or ask the driver`. Add one self-test row asserting the E24 reason contains `goto` and `phone-converge-allow`.

### F6 (LOW) agent-guard E24 fixtures leave matching logic unpinned
- Where: tools/agent-guard:1899-1912 (fixtures), :551-561 (logic).
- Mutants I ran on a scratch copy [obs]: (m1) dropping the `-h`/`--help` exemption: `RESULT: PASS ... survived=0 failures=0` (SURVIVED); (m6) a value-less global flag stops the scan
  (`adb -d get-state` would be allowed): self-test PASS (SURVIVED). Killed: m2 (drop `fastboot devices`), m3 (drop `-s` skip), m4 (head name), m5 (drop `adb get-state`).
- Fix: add fixtures `tools/phone-state --help` allow, `adb -d get-state` deny, `adb -e devices` deny, `fastboot -s abc devices` deny, `timeout 5 sudo -n adb get-state` deny.

### F7 (LOW) E24 indirection gaps are real and undocumented
- Probes through the real `--pre` entry [obs] that ALLOW: `X=adb; $X get-state`, `ADB=adb; "$ADB" get-state`, `$(which adb) get-state`, `bash <<< "adb get-state"`,
  `echo adb get-state | sh`, `ssh host adb get-state`, `for p in adb; do $p get-state; done`, `python3 -c "subprocess.run(['adb','get-state'])"`, `bash -c 'X=tools/phone-state; $X'`.
  Same limits as phone-guard (its docstring :6 says so); E24's docstring (agent-guard:547-550) and the "Known gaps" paragraph at the top of the file do not list them.
- Fix: add one line to the Known-gaps paragraph (variables, here-strings, ssh, python -c); no code change (a static parser cannot close them; the lint covers files).

### F8 (LOW) `tools/pix phone` is an E24 bypass for phone-state and still advertised
- Where: tools/pix:11 and :139 (`"phone": wrap("phone-state")`).
- Scenario [obs]: `tools/pix phone --expect fastboot` and `python3 tools/pix phone` -> ALLOW. pix has no lock/boot word, so the lint does not see it either.
- Fix: make `pix phone` run `tools/phone state` (observe) and change help line 11 to `tools/phone state`; or add `pix phone` to E24. Also grep the other docs/skills for `tools/phone-state` instructions (not done here: docs are in the super repo).

### F9 (INFO) the author's stated gaps, judged
- probe-run `phone_back()` still uses `$PS` (tools/probe-run:48-58): ACCEPTABLE with the allowlist entry, because it classifies the OUTCOME after the shim resets the phone (PASS/FAIL/HANG/TIMEOUT), it is not a pre-check and there is nothing to converge to; the stated follow-up (replace with goto) is right but needs the shim-probe/absent transitions measured (rule 7). Condition: pin it (F1) so it stays one use.
- hang-recover polling phone-state (tools/hang-recover:21): ACCEPTABLE (it is the recovery classifier; `TRANSITION_TABLE[("hung","fastboot")]` only prompts the user, tools/phone:157-166). Pin it (F1).
- xrun `adb get-state` (xrun:185) in the PHONE: diagnostic: ACCEPTABLE, bounded `timeout 8`, observation only. Pin it (F1).
- phone-guard disagreement: text only, see F4 (not must-fix for safety, must-fix for consistency).
- Other checks, no defect: tools/run-probe.sh prints `DEPRECATED: use tools/probe-run ...` to stderr and exits 2 [obs]; no executable under tools/ calls it
  [obs grep]; remaining mentions are tools/common-files.txt:11, tools/phone-lock:9 (comment, stale: says run-probe calls phone-lock check), docs/tool-proposals/TP-020 and old reviews (history).
  tools-lifecycle.tsv:124-125 and tools-status.tsv:26 carry the deprecation and the new lint; tool-census PASS. Gate wiring: `phone-converge` is in check-all.sh ALL (:98) and lane B (:171), gate_select ALL and the UNIT_PREFIX set;
  `tools/phone-converge-allow.txt`, `tools/lint-phone-converge`, `tools/test_lint_phone_converge.py` all select it [obs]; docs-only changes do not (correct). Stale-comment nit: fix phone-lock:9 later with the run-probe removal.
- The lint passes for a real reason on the real tree [obs]: removing any one of the 9 allowlist lines in the scratch copy gives `RESULT: FAIL` (abl-dmesg 3, agent-guard 15, fboot-android 2, hang-recover 1, lint-phone-converge 10, phone 5, probe-run 1, xrun 1, phone-guard 2 findings), and a `fastboot devices` line added to fboot.sh fails with `READINESS-CHECK tools/fboot.sh:17`. The weakness is F1/F2, not an allowlist that swallows everything.
- super-callers.tsv gains 19 unrelated runplan/workflow rows (census regeneration). Scope noise, harmless; say so in the commit message next time.

## E24 probe table (real `tools/agent-guard --pre`, run via /tmp/20ab/r38-scratch/probe.sh) [obs]

DENY (correct): `adb get-state`; `tools/phone-state`; absolute path `.../pixelios-kernel/tools/phone-state`; `./phone-state`; `tools/./phone-state`; `python3 tools/phone-state`;
`bash tools/phone-state`; `sh -c 'tools/phone-state'`; `bash -c "adb get-state"`; `bash -lc 'adb get-state'`; `eval "adb get-state"`; `timeout 5 adb get-state`; `env FOO=1 adb get-state`;
`FOO=1 tools/phone-state`; `sudo adb get-state`; `/usr/bin/adb get-state`; `/usr/bin/fastboot devices`; `command|exec|nohup|setsid|stdbuf|xargs|watch adb get-state`; `adb -s abc get-state`;
`adb -sabc get-state`; `adb -s abc devices`; `adb devices -l`; `fastboot -s abc devices`; `fastboot devices -l`; `a=$(adb get-state)`; `while ...; do adb get-state; done`; `if adb get-state; then ..`;
`(adb get-state)`; `{ adb get-state; }`; `adb get-state </dev/null`; `cd tools && ./phone-state`; `tools/phone goto fastboot && adb get-state`; `tools/phone-state --expect fastboot --self-test` (only a[0] exempt).
ALLOW (correct, no false positive): `tools/phone state`; `tools/phone  state`; `tools/phone goto fastboot`; `tools/phone-lock status`; `tools/phone-state --self-test|-h`; `grep -n phone-state docs/WORKFLOW.md`;
`cat|less|sed|cp|rm|chmod|git add|ls|test -x tools/phone-state`; `git log -- tools/phone-state`; `git commit -m "drop phone-state check; adb get-state"`; `git commit -m x -m "fastboot devices"`;
`echo adb get-state`; `printf 'adb devices\n'`; `adb version`; `adb --version`; `fastboot --version`; `adb shell getprop sys.boot_completed`; `tools/phone-state2`; `tools/lint-phone-converge`;
`python3 tools/test_lint_phone_converge.py`; `grep -rn "adb get-state" tools/`; `git commit -F /tmp/msg`.
ALLOW (false negatives): see F7 and F8. Other rules still fire first where applicable: `adb -L tcp:5037 get-state` -> E3a (E24's flag list lacks -L, harmless), `adb wait-for-device` -> E3b.
No false positive found (reading docs, grep, git messages, tools/phone itself).

## Proposed antipattern (not written, brief says review file only; the driver may add it)
AP-NEW "A lint whose exemption list is per file cannot tell the fixed file from the unfixed one": an allowlist entry that swallows all hits of a file hides the very regression the lint was built for
(R38: the pre-fix probe-run passes the new lint). Do instead: pin each entry to a hit count or exact line text and fail on growth; run the lint on the pre-fix file as a test row. Enforced by: none yet (F1).
