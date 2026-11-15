---
id: TP-048
tool: "tools/remote-job"
status: proposed
proposed_by: "claude-opus-5-5 (driver 20k)"
date: 2026-10-29
exposed_by: "ledger R8 (user 2026-10-29 ~07:2x via W28): faster offload-host gate-pre; KEEP ONE offload-host job (W28 decision, user 'if not the correct decision just keep 1'). 0181 gate-pre 092fe27: elapsed 1470 s, wlan-mutants 1464 s, MUTCACHE hits=0 fresh=957"
extends: "tools/remote-job"
related: "TP-042"
usage: "tools/remote-job gate-pre (unchanged; the container gets a persistent offload-host mutant cache instead of a per-job one)"
refs: "-"
---

## checked
`tools/tool-proposals check concurrent|slots` -> NONE. TP-042 (heavy-job <offload-host> queue, building) is the queue, not the gate contents. tools/remote-job:66-71 sets `GATE_MUT_CACHE="$M/mut-cache"` with M=/job, the per-job checkout, removed after every job, so each gate-pre starts with an empty cache. tools/wlan_mutant_cache.py:1-6,15,42-49 already makes a persistent cache safe: the key is sha256 of CACHE_VERSION + gcc --version + glibc version + flags + chip + header + both test sources; only deterministic verdicts are stored; every run spot-checks SPOT_K=12 hits + every equivalent survivor (hourly salt) and quarantine() renames the whole dir on a mismatch (:107-115). The local formal gate already uses the persistent host cache (~/.cache/pixelios/wlan-mutants), so this is not a new trust model. 2 concurrent slots were considered and dropped (W28, R8): one gate-pre already drives <offload-host> load1 to ~22 on 16 cores [obs 06:34], two would split the same CPU, and <offload-host>' other T3 instance has priority.
**Caller:** unchanged: `tools/remote-job gate-pre` as run by the drivers' merge gates (super docs/WORKFLOW.md Commands) and its re-exec under `tools/heavy-job <offload-host> run`. **Replaces:** the per-job GATE_MUT_CACHE=$M/mut-cache (deleted after every job).

## why
[obs] 0181 gate-pre (092fe27, logs/remote-jobs/gate-pre/out/ in worktree dk-hfs-dataprotect): RESULT elapsed=1470 s; timing table (gate-full.log:26702-26726) wlan-mutants 1464 s, the critical path (lane C starts at t=0); next longest build-wlan 593 s, build-gpupower 395 s, build-gpujob 309 s, unit 204 s; MUTCACHE line gate-full.log:26637 `hits=0 fresh=957 stored=957`. The same suite locally: 2240 s cold (0181-unit) vs 851 s cached (0182-unit) [obs W28]. Prediction [guess, to be measured]: with a warm cache lane C drops to a few minutes (12 spot checks + equivalent survivors re-run), and the critical path becomes lane A (build 155 s, then the parallel builds, max build-wlan 593 s, then host-tests + boot-safety ~52 s): about 800 s, so 1470 s -> 800-950 s per gate-pre, 2.4 -> ~4 gate-pres per hour, at the same peak load. The first gate-pre after a gcc/glibc/image change or a header change stays cold by design.

## output
1. remote-job gate-pre: `-v "$HOME/work/pixos/mut-cache:/mut-cache:rw,Z"` added to the container prefix of gate-pre only (and the unit / wlan-mutants jobs if they run in the same container; say which), `GATE_MUT_CACHE=/mut-cache`; the host dir is created 0700 by remote-job before the run, owned by the uid the container runs as (--user 1000:1000, tools/remote-job:82). Nothing else of ~ is mounted rw.
2. Loud, never hidden (rule 8): remote-job greps the MUTCACHE line from gate-full.log and puts `mutcache=<hits>/<fresh>/<state>` into its RESULT tail; a missing MUTCACHE line, cache=ro/off, store_failed>0 or a quarantine (`.bad-<epoch>` dir appears) prints `REMOTE: MUTCACHE ...` and a counted WARN. No fallback to the per-job dir: a broken persistent cache shows up, it does not get silently replaced.
3. Hygiene: quarantined `mut-cache.bad-*` dirs are listed (age, size) in the TP-042 resubmit check output; size and entry count printed per run. Pruning old entries goes to TP-033 (artifacts-prune), not here.
4. Formal provenance unchanged: `formal` stays tied to tools/gate-identity (the cache is not part of the identity, as on the local gate); the review checks that claim.
5. Measure: the next 3 gate-pres after the merge record elapsed + MUTCACHE in the ledger (R8), compared with 0181's 1470 s. Then the next cold phase: lane A builds have no compiler cache (build-wlan 593 s builds 5 variants); a ccache-style follow-up only after measuring how much of lane A is repeated compile work.

## risks
<offload-host> only (host-side, never a device). A poisoned or stale entry would turn a real mutant kill into a stale verdict: covered by the content key (compiler, libc, flags, sources, CACHE_VERSION), the per-run spot check and quarantine; the review must try to break that (an image change that keeps gcc --version text but changes behaviour; concurrent writers, though ONE <offload-host> job is the rule). The mount must stay out of the bundle/checkout (AP-110 class) and out of git. No change to the one-job rule, nice/ionice or the container's --network=none.

## amendments
- 2026-10-29 driver 20l (review offload-mutcache-r1, kernel tools/offload-mutcache 93d4df8): the host dir is the ROOT <work-dir>/mut-cache-root (0700) mounted at /mut-cache; the cache is the subdir /mut-cache/<gate image id, 12 hex> (F1: renaming a bind-mount point fails EBUSY [obs unshare -rm], so quarantine renames the subdir to <id>.bad-<epoch>; F2: one cache per gate image). The REMOTE line says "quarantined" only when the test printed `cache dir renamed`, else "NOT quarantined, delete by hand". warn= in the RESULT tail is ADVISORY: gate-import-remote imports a PASS with warn>0 (F4 decision: a cache fault only costs time, a spot-check MISMATCH fails the gate itself). The old <work-dir>/mut-cache (if any) is unused and can be deleted by hand.
