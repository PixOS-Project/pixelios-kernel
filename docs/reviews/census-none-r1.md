# Review census-none-r1 (branch tools/census-none, commit 4abe371)

Reviewer: Sonnet 5.5, adversarial. Tags: [obs] = run/measured here, [src] = read from source.

## Verdict: SAFE WITH CONDITIONS
No safety or gate-correctness defect. The new unit test does not test the change (F1), and the Deprecated section of TOOLS.md prints a bare `none` (F2). Both are low-cost fixes.

## Checks asked
1. TSV rewrite: PASS [obs]. Python field-wise diff against `git show trunk:tools/tools-status.tsv`: 26 lines (25 + final newline) on both sides; exactly 6 rows differ (acpm-verify, check-exports.py, s33-decode.py, s35-decode.py, sparsediff.py, xnu-cc.py), each in field index 2 only (`''` -> `'none'`), all rows 6 fields, tabs kept, file still ends in `\n` (xxd tail).
2. `none` masking: no breakage [src tools/tool-census]. `replaced_by` is read at :397 (stored), :588 (the NO-REPLACED-BY warn), :642-643 (TOOLS.md status cell), :655 (Deprecated section). It is never resolved to a tool name or checked for existence (existing free-text values such as `pixelios-userland tools/check-ios-root.py` prove that). STALE-DEPRECATED (:574-580), NO-DEPRECATED-LINE (:583) and DEPRECATE-NO-REASON (:585) do not use it. The lifecycle log match (:597-607) uses `since`/`new`, not replaced_by. `none` cannot hide a missing reason or evidence: :585 still raises DEPRECATE-NO-REASON, and :395 raises the length error. The 6 tools' first 15 lines all carry `DEPRECATED: use nothing (no replacement; ...)` [obs head -15]. The one place it misleads is F2.
3. `replaced` + `none`: a (minor) defect, not rejected by the code. Status `replaced` means "a named tool took over", so `none` contradicts it. Nothing checks it (the :588 branch covers both statuses, and no check runs on the value). Low risk (no row does it today, awk over the TSV [obs]) but easy to close, see F3.
4. New test: FAILS TO DISCRIMINATE (F1).
5. TOOLS.md: consistent [obs]. `TOOL_CENSUS_STRICT_SUPER=1 tools/tool-census --check` -> `RESULT: PASS tools=102 active=90 deprecated=8 allowlisted=4 nocaller=6 doc_only=8`, no TOOLS-MD-STALE and no SUPER-CALLERS-STALE; the diff touches only the 6 status cells and the 6 Deprecated-section lines (matches the render code); super-callers.tsv unchanged and still current.

Test run [obs]: `python3 -m unittest discover -s <wt>/tools -p test_tool_census.py` -> Ran 63 tests, OK. shellcheck: no shell file in the diff.

## Findings

F1 (medium) tools/test_tool_census.py:278-285 - the new test passes on trunk code [obs]. Trunk tool-census + the new test file: `Ran 1 test ... OK`. `"none"` is already truthy at trunk's `not r["replaced_by"]`, so the test asserts only what already held. Mutant A (revert the render change to `(by none)`): 63/63 still pass [obs], so the "(no replacement)" display is untested. Only a mutant that makes `none` warn fails the test (kills 1) [obs]. The real new behaviours (render text, new warn hint) have no test.
Fix: in the same test, after `--check`, run `--write` (or whatever helper the render tests at ~:179 use) and assert `"deprecated (no replacement)" in md` and `"(by none)" not in md`; add `self.assertIn("name one, or write `none`", out)` to test_no_replaced_by_warns (:269) so the new warn text is pinned. Re-run mutant A and confirm it fails.

F2 (low) tools/tool-census:655 and tools/TOOLS.md Deprecated section - the line renders `...since 2026-10-29): none job done: ...`; a bare "none" glued to the reason reads like the first word of the reason (before: empty). [obs diff]
Fix: render it like :643, e.g. `rb = "" if not rb else "(no replacement)" if rb == "none" else "-> " + rb`, use it in the f-string, regenerate with `tools/tool-census --write` and commit TOOLS.md together with tools/super-callers.tsv (AP-218).

F3 (low) tools/tool-census:588 - `replaced` with replaced_by `none` or empty is accepted (empty only warns). Fix: add `if r["status"] == "replaced" and r["replaced_by"] in ("", "none"): finds.append("REPLACED-NO-TOOL tools/%s is replaced but names no replacing tool (use deprecated + none)" % t)`, plus one test. Optional; may be deferred, none of the current rows is affected.

F4 (nit) tools/tool-census:588 - the match is case-sensitive and exact: `None`/`nothing` renders as "(by None)". Optional: `.lower()` compare, or reject unknown free text. Not needed for the 6 rows.

## Not found
No stray edits in the 6 tool files (diff touches only tool-census, test, TSV, TOOLS.md), no secrets, no write paths changed, no phone interaction.

## Fixes applied (driver 20q)
F1 test now asserts the rendered TOOLS.md; F2 Deprecated section renders "(no replacement)"; F3 REPLACED-NO-TOOL finding + test; F4 (case) not applied, nit.
