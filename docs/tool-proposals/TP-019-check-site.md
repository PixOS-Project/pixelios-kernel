---
id: TP-019
tool: "tools/check-site"
status: done
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-24
exposed_by: "docs-site review 2026-10-24: 11 of 13 pages had stale claims, one dead cross-repo link, found by hand"
extends: ""
related: ""
usage: "tools/check-site [--kernel DIR] [--userland DIR] [--max-age-days N]"
refs: "pixelios-site 9839ef4 (tools/check-site.py, tools/test_check_site.py, .github/workflows/check.yml)"
---

## checked
`tools/tool-proposals check site` and `docs`: no match. Lives in the `pixelios-site` repo (`tools/check-site.py`), not here. The existing gate is `mkdocs build --strict` in `pages.yml`; it only sees links inside the site. Related: none.

## why
The 2026-10-24 review took about 25 tool calls and one session of reading to find stale Wi-Fi status, an obsolete licence note and dead links. Checks: (1) every `github.com/PixOS-Project/*/blob|tree/main/PATH` link resolves in a checkout of the kernel/userland repos; (2) `STATUS.md` rows saying works/observed carry a date and a run label or build id (hard rule 5); (3) `Last update` in `STATUS.md` is not older than N days (default 7) behind the newest commit to the kernel `docs/WIFI-COVERAGE.md`; (4) secrets scan for serial/IMEI/key/local-path patterns (hard rule 4). It flags drift; it cannot prove a claim true.

## output
One line per finding (`file:line rule message`), last line `RESULT: PASS|FAIL|ERROR n findings`; exit 0/1/2. Runs in `pages.yml` on push/PR and in a weekly scheduled workflow that opens an issue on FAIL. No log dir; stdout is the report.

## risks
No device touched, no network except `git`/Actions checkout, bounded (seconds). Must never write to any repo.

## amendments
(none)
