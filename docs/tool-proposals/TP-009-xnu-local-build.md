---
id: TP-009
tool: "tools/xnu-local-build"
status: done
proposed_by: "Claude Sonnet 5.5 (delegate, on the driver's order)"
date: 2026-10-22
exposed_by: "every xnu patch iteration waited ~4 min (8 min worst case) for the GitHub macOS CI round trip"
extends: ""
related: "TP-005"
usage: "tools/xnu-local-build [--fresh] [--stack DIR] [--compare CI_KERNEL] [--jobs N] [--timeout SEC]"
refs: "docs/LOCAL-BUILD.md"
---

## checked
tools/ci-kernel (waits for CI, does not build), tools/xnu-cc.py (replays single compile commands from a CI log, syntax check only,
needs an Apple clang), tools/stack-apply (patch apply check only). None builds a kernel. Related: TP-005.

## why
CI round trip ~4 min per kernel. Local Linux build, measured 2026-10-22: cold 71 s, no-op 3 s, one changed patch 16 s.

## output
Short report, last line `RESULT: PASS|FAIL|ERROR ...`; exit 0/1/2; kernel copied to ~/pixelios-artifacts/localbuild/out/<label>/kernel.development.sweet (per label since 2026-10-28);
logs in ~/pixelios-artifacts/scratch/xnu-local-build.log and <work tree>/build.log.

## risks
No device access, no root. The local kernel is not byte-identical to CI (stock clang, theos ld64, stand-in iig); boot-test it on the phone
before trusting it, and use CI for anything that gets committed as a result. Uses ~3.2 GB under ~/pixelios-artifacts/localbuild; one make -j4 at a time.

## amendments
(none)
