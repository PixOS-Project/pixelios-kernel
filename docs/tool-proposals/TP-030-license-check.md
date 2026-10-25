---
id: TP-030
tool: "tools/check_rules/r30_license.py (rule R30 of tools/check-rules.py)"
status: building
proposed_by: "Claude Sonnet 5.5 (implementer, standalone T5; text from docs/PLAN-STANDALONE.md section 4)"
date: 2026-10-25
exposed_by: "PLAN-STANDALONE: 94 ios-sweet files shipped without notice (759b3e3); no file-level licence tracking"
extends: "tools/check-rules.py"
related: ""
usage: "python3 tools/check-rules.py --strict   (runs in the gate like R01-R22)"
refs: "docs/PLAN-STANDALONE.md T5, tools/license-map.txt, LICENSES/, THIRD_PARTY.md"
---

## checked
`tools/tool-proposals check license`: no existing tool tracks licences. Extends the rule-module mechanism of tools/check-rules.py (R01-R22, ratchet baseline); R30 uses an empty baseline (every finding is fixed, none baselined).

## why
Fail the gate when a tracked file has no known licence, when a header disagrees with the licence map, or when a banned origin (firmware, Apple payload, key, secret) is tracked. 235 of 235 source files carry no SPDX header, so the map tools/license-map.txt (glob, SPDX expression, origin; first match wins) is the single record and avoids touching every file.

## output
One finding per violation, then check-rules' `RESULT: PASS|FAIL n new, m baselined` line (exit 0/1/2). Implemented checks:
1. every tracked path matches a map line (no catch-all); map ids are in {MIT, ISC, APSL-2.0, BSD-2-Clause, BSD-3-Clause, Apache-2.0}; each id has `LICENSES/*<id>*.txt`.
2. an `SPDX-License-Identifier` in a file must name only ids of its map entry (so a GPL/LGPL/AGPL id always fails).
3. a `Copyright` line naming Apple/NeXT/CMU needs APSL-2.0, Broadcom/Wildt needs ISC, Nayuki needs an origin naming nayuki, dipa-studio needs an origin naming ios-sweet, Cambus needs BSD-2-Clause in the entry.
4. forbidden tracked names (*.img, *.bin, *.ipsw, *.dmg, *.im4p, keys, .env, ...) and the dirs ipsw/, Payload/; the allowlist is empty.
Not built yet (open items from the plan): banned marker strings in text (EXPORT_SYMBOL, MODULE_LICENSE, "GNU General Public License", Broadcom-WL-IPTag, PolyForm) and the optional line-run similarity check against the GPL refs ($PIXELIOS_REFS, T8).
Tests: tools/test_check_rules_r30.py, one planted violation per failure mode plus one mutant per content check.

## risks
Read-only, no device. False positives on documents that quote licence names: docs/*, LICENSES/*, THIRD_PARTY.md, tools/testdata and the test files are not content-scanned (they are still map-checked). Content checks see only the first-match licence, so a file that is really two licences must have a combined expression in the map.

## amendments
(none)
