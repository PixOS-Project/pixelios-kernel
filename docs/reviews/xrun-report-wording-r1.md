# Review r1: xrun_report host_end wording + `<N>` wildcard (commit 932841a, tools/xrun-report-wording)

Reviewer: Sonnet 5.5, adversarial. Scope: `git show 932841a` + uncommitted super `docs/KNOWN-GAPS.md` row 54.
Tags: [obs] = run here, [src] = read from source (file:line).

## Verdict: SAFE WITH CONDITIONS

The `<N>` wildcard is sound for its stated purpose (F2-F4 below). The host_end wording has one real defect (F1): the text asserts a fact it does not check.

## Findings

### F1 (HIGH, rule 8 / rule 7) New reset text says "backstop was not due" without checking it; it hides a hung phone when `--restart < --duration`
- [src] tools/xrun_report.py:539-540: the branch condition is only `restart and run.get("host_end") and run.get("early")`. The text then claims "the sweetrestart backstop (%s) was not due, so it was not exercised". Nothing compares `restart_s` (computed at :529) with the +s of `early`.
- [src] host_end is set at tools/xrun_report.py:283-286 when the wait loop ends at `end = t0 + duration` with remote, no timeout, phone gone and not back. A phone whose sweetrestart (say 100 s) FAILED to fire (hung) while `--duration 300` ran out takes exactly this path, because no `usb_back` was seen.
- [obs] synthetic run (restart=100, duration=300, host_end=1, early=T0+300, phone="to-fastboot failed after the software reset request"): reset_text = "reset by the host at the end of --duration (+300s, host_end); the sweetrestart backstop (100) was not due, so it was not exercised"; same for restart "100,bootloader". Before the change this printed "reset NOT observed ... phone hung". Both runs score PASS.
- Also [src] xrun:90-93: with remote the default duration IS `restart`, so `--restart R` with no `--duration` gives duration == R; `early - t0 >= R` is the normal case, so "was not due" is wrong there too (the timer was due at the same second).
- Fix: in tools/xrun_report.py:539-540 require `float(run["early"]) - t0 < restart_s` for the new branch (`restart_s` is already defined at :529; `t0` must be truthy). Otherwise keep the old wording, and for `early - t0 >= restart_s` use "reset NOT observed ... the sweetrestart (R s) was due before the host reset; the phone may have hung" (the backstop failing is a defect to show). Add a test with restart=100/duration=300 asserting "reset NOT observed" and not "was not due". Test 1 only covers 1650 > 120, so the mutation `restart and host_end` -> no restart_s comparison passes all 26 tests [obs].

### F2 (MEDIUM, observation) "reset by the host" is stated before the reset is verified; PASS does not depend on the PHONE line
- [src] tools/xrun:204-211: waitusb prints EARLY and run.json gets host_end BEFORE `tools/to-fastboot.sh` sends the reset; the outcome is only marked as `phone=` ("back in fastboot (...)" or "to-fastboot failed ...").
- [src] tools/xrun_report.py:578-580 gives PASS for host_end regardless of `run["phone"]`; [obs] with phone="to-fastboot failed after the software reset request" the result is still PASS (pre-existing, not introduced by 932841a, but the new wording "reset by the host" now reads as a completed reset next to PHONE: to-fastboot failed).
- The verification of the return to fastboot is therefore separate and weak: the PHONE: line (xrun:207-211) is printed, but nothing turns "to-fastboot failed" into a non-PASS.
- Fix (smallest): word it "host reset requested at the end of --duration (+%ds, host_end); outcome: see PHONE:" in tools/xrun_report.py:539, and in the PASS branch (:578) downgrade to PARTIAL when `run.get("phone","").startswith("to-fastboot failed")`. The second part may be a separate change (note in HANDOFF if not done).

### F3 (LOW) Wildcard regex: construction is correct
- [src] tools/xrun_report.py:83-89. Pieces are `re.escape`d, joined with `\d+`, so only `<n>` is special; `re.match` anchors at start and the explicit end/continuation check anchors the tail. No DoS: pieces are separated by `\d+` only, matching is linear; [obs] 30 wildcards and 5000-digit key finish in <1 ms.
- [obs] results (all with lowercase cell and key): digits exact -> True; `error 1x` -> False; missing number -> False; `12.5` / `, ...` / ` (x)` continuations -> True (same punctuation rule as the non-wildcard path, pre-existing); `foo bar 12<n>` vs `foo bar 12345` True, vs `foo bar 12` False (needs >= 1 digit); `<n> <n> <n>` matches `1 2 3`; `foo <n>` vs `foo 12 bar` False; vs `foo 12abc` False.
- Case: keys are lowercased by `_cell_keys` (`_norm(...).lower()`, :78) and `_known_other` lowercases the key (:94), so `<N>` becomes `<n>` before `_cell_match`. [obs] works with `<N>` in the real file.
- `_cell_match` is a strict-prefix safe: a key longer than the cell by a word is rejected (test row "error 12 and more" is NEW, [obs]).
- Fix: none.

### F4 (MEDIUM, rule 8) The wildcard turns the two "bad" combinations of the registered gap into KNOWN
- The registered meaning (KNOWN-GAPS.md:54, 0177 r2) is: ENOMEM for buffers < 608 B (stock behaviour), truncated only for buffers >= 608 B. With `<N>` for the buffer size, `kern.bootargs truncated to 127-byte buffer, ...` (the closed 0177 v1 regression, KNOWN-GAPS.md:55: launchd cut to 127 chars, boot stalled) and `ENOMEM to 1024-byte buffer` (a >=608 B reader that should have been cut) are both now KNOWN, [obs] `_known_other` returns True for "kern.bootargs truncated to 127-byte buffer, line 926 chars, error 0". Previously the entire family was NEW (false NEW), now a real regression of 0177 would be marked known (false known is worse, rule 8).
- Fix: either (a) accept and say so in the row: "any buffer size is matched; truncated below 608 B is the 0177 v1 bug and is NOT covered by this registration" and have the run plan carry an `--expect-absent 'kern.bootargs truncated to (1|2)?[0-9]{1,2}-byte|truncated to [1-5][0-9]{2}-byte'` row for bootargs runs; or (b) register the family with literal anchors: split into the observed buffer sizes (`ENOMEM to 128-byte` / `256-byte`, `truncated to 608-byte`) with `<N>` only for line length and errno. (b) is stricter and cheap; row 54 is edited anyway.

### F5 (LOW) Wildcard does not work together with the `_norm` rewrites; undocumented
- [src] tools/xrun_report.py:50-55: keys are `_norm`ed, which rewrites `pid 54` -> `pid N`, `found 3` -> `found N`, `0x1ce` -> `0xN`. A cell written `pid <N>` or `0x<N>` therefore never matches a key (`pid N` has no digits). [obs-by-read, not run] The `_cell_match` docstring only says "digits"; it should say `<N>` cannot stand where `_norm` has already substituted `N`/`0xN` (write `pid N`, `0xN` literally).
- Fix: one sentence in the `_cell_match` docstring (tools/xrun_report.py:84) and in docs/KNOWN-GAPS.md header (row-format note).

### F6 (INFO) Other cells containing `<N>`
- [obs] `grep -c '<[Nn]>' docs/KNOWN-GAPS.md docs/KNOWN-GAPS-WIFI.md` = 1 / 0. The only cell is row 54 itself (KNOWN-GAPS.md:54); no other cell changes meaning. [obs] `_cell_keys` on the real file produces exactly the two keys `kern.bootargs enomem to <n>-byte buffer, line <n> chars, error <n>` and `kern.bootargs truncated to ...`. Row 54 has no `|` inside, ends with `)` (so the greedy `\(.*\)\s*$` strip at :72 removes the whole note, including the inner `(comm cut at 16)`), and the note's backticks and `{ENOMEM or truncated}` do not reach the key. Pre-existing and unrelated: row 55's first cell is a whole paragraph (does not end with `)`), so its "key" is the full paragraph; harmless.
- Residual: the `(...)` note strip means a stray `)` or a note-less `(` earlier in a cell would change the key silently; the row-54 text is fine as it stands.

### F7 (INFO) Tests
- [obs] `python3 -m unittest discover -s tools -p test_xrun_report.py` in the worktree: Ran 26 tests, OK. (Under `python3 -I` the PIL-gated Report class is skipped: use the plain command the brief gives.)
- [obs] Against trunk's xrun_report.py (git show 932841a~1, full tools copy in /tmp): exactly the two new tests FAIL (`test_host_end_before_sweetrestart_is_not_reported_as_unobserved`, `test_n_wildcard_matches_digits_only`), the other 24 pass.
- [obs] Mutations: (1) new branch replaced by `if False` -> host_end test FAILS; (2) `\d+` -> `.+` -> wildcard test FAILS; (3) end/continuation check removed (`return bool(m)`) -> wildcard test FAILS. Good coverage of the wildcard; the host_end test does not cover F1 (restart < duration) or the `early` missing branch.
- Shellcheck: no shell files in the diff (`git show --stat`: tools/xrun_report.py, tools/test_xrun_report.py only).

## Answers to the brief
1. host_end can hide a hang: yes, when `--restart < --duration` (F1); not otherwise. host_end is set at xrun_report.py:285 only for remote, no hard timeout, phone gone and not returned. The phone's return to fastboot is verified only by `$R mark phone=` (xrun:206-211) and does not gate PASS (F2).
2. Regex: correct and linear (F3); false-known risk is semantic, via a free buffer size (F4).
3. No other `<N>` in either file (F6).
4. 26 OK; both new tests fail on trunk's xrun_report (F7).

## Conditions for SAFE
- Fix F1 (required) and add the restart < duration test.
- F4 (a) or (b), F2 wording, F5 docstring: should-do; note in HANDOFF if deferred.

## Fixes applied (driver 20q)
F1 new wording only when early-t0 < restart (else old wording kept) + test; F2 wording "reset sent by the host" (PASS ignoring a failed to-fastboot step is pre-existing: noted in HANDOFF, not changed here); F4 KNOWN-GAPS cell keeps buffer sizes literal (128/256 ENOMEM, 608 truncated) + test that 127 stays NEW; F5 docstring.
