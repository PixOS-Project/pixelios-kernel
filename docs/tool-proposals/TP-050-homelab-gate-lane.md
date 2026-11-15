---
id: TP-050
tool: "tools/remote-job"
status: proposed
proposed_by: "claude-opus-5-5 (driver 20l)"
date: 2026-10-29
exposed_by: "ledger R12 (user 2026-10-29 ~07:30 via W29: 'we should have one in lab-host and other in offload-host, so we double our production')"
extends: "tools/remote-job"
related: "TP-042, TP-048"
usage: "tools/remote-job --where lab-host gate-pre (new); tools/gate-import-remote OUTDIR WORKTREE (accepts where=lab-host)"
refs: "-"
---

## checked
- tools/remote-job:23,62,192 [read from source, trunk 8a521fc]: gate-pre is offload-only (`--where local` refused, no <lab-host> fallback). tools/gate-import-remote:3,39-40 imports only `where=<offload-host> ... formal (<offload-host> container, identity ...)`.
- <offload-host> runtime [obs 2026-10-29 07:3x]: `~/.local/bin/docker -> /usr/bin/podman`, rootless, `--userns=keep-id --user 1000:1000 --network=none`.
- <lab-host> [obs 2026-10-29 07:32]: only rootful docker (/var/run/docker.sock, user in the docker group = root-equivalent; global rule: not used without the user's OK); no podman, no newuidmap; /etc/subuid+subgid have <offload-host>:100000:65536. 16 cores, 31 GB, ~13 GB available, load 3-7 from its own services.
- tools/gate-identity.expected pins `image=8224150f...` (image ID) + verlist 3b391e0b: a rebuild on <lab-host> gets another image ID, so the <lab-host> lane must load the same image bytes (podman save on <offload-host> -> podman load on <lab-host>), not rebuild.
- TP-042 (heavy-job queue) covers the <lab-host> lock; TP-048 (persistent mut cache) is offload-side; nothing proposes a second formal lane.
- **Caller:** the drivers' merge gates (`tools/remote-job --where <lab-host> gate-pre` then `tools/gate-import-remote`, docs/WORKFLOW.md Commands); new helper tools/rj-local-transport is called only by tools/remote-job.

## why
One formal gate lane (<offload-host>, one rj- job at a time) serialises every stream's merge: a gate-pre takes ~1470-1485 s [obs GATE PASS 092fe27 / fc84033]. A second lane on <lab-host> with the same guarantees doubles merge throughput (user R12).

## output
- `tools/remote-job --where <lab-host> gate-pre`: the SAME job script, container check (tmp/rj-check) and container run line as <offload-host>, executed locally with rootless podman under tools/heavy-job (one <lab-host> gate at a time, shared with the drivers' local gates). RESULT tail unchanged except `where=<lab-host>` and label `formal (<lab-host> container, identity sha256:<hex>, image <id>)`; a non-identity image prints the existing pre-validation label.
- `tools/gate-import-remote`: accepts where=<offload-host> with the <offload-host> label and where=<lab-host> with the <lab-host> label; any mix (where=<lab-host> + <offload-host> label, or the reverse), a non-container local run, pre-validation, FAIL or non-identity image is REFUSED (rule 8). Exit codes unchanged (0/1/2).
- Admission: before the container starts, the <lab-host> lane measures available memory against the measured peak of one containerised gate (cgroup memory.peak, recorded in this TP after the first run); below it = `RESULT: ERROR ... memory` (no silent slow-down, no throttling; a gate that does not fit is a root-cause item).
- Image provisioning (one-off, one heavy job): `podman save pixos-gate:trixie-20261029b` (tag current since gate-image-lint; was trixie-20261029) on <offload-host>, `podman load` on <lab-host>; check image ID == gate-identity.expected image=, verlist == expected, then tools/gate-identity in the container == expected.

## risks
- Needs root on <lab-host> once: `sudo apt-get install -y podman uidmap` (the user runs it; staged). Rootful docker is NOT used (root-equivalent; would differ from <offload-host>: no keep-id).
- Never weaker than <offload-host>: same pinned image ID, same container check (no adb/fastboot, no /dev mapping, --network=none), same identity proof, clean-at-bundle tree, commit.txt == HEAD.
- Load: <lab-host> died from load before (ONE heavy job host-wide); the <lab-host> lane always goes through tools/heavy-job; the formal sizing GATE_JOBS=8 GATE_MUT_JOBS=12 is kept only if the measured peak fits.
- No phone, no USB devices reach the container.

## amendments
- 2026-10-29 driver 20m, review docs/reviews/<lab-host>-lane-r1.md (SAFE WITH CONDITIONS), all fixed on tools/<lab-host>-lane: F1 RJ_LAB_HELD trusted only if the heavy-job lock is flocked with holder label rj-JOB-<lab-host> and holder pid an ancestor; F2 admission = heavy-job class gate only (4 + 8 GB reserve), RJ_LAB_MIN_GB / RJ_LAB_MEMINFO / --mem-gb removed (was 12 + 8 = 20 GB double count); heavy-job counts ZFS ARC freeable (openzfs 2.3.9 arc_os.c:149-170, <lab-host> arc_free=5.4G [obs]); F3 rj-local-transport ssh = clean env (HOME USER LOGNAME PATH XDG_RUNTIME_DIR) in $HOME (AP-225); F4 REMOTE_JOB_LAB_PODMAN only with RJ_HOST_TEST=1; F5 cp -rL. The admission number from the measured memory.peak (output section) is still owed after the first <lab-host> run.
- 2026-10-29 driver 20m, review <lab-host>-lane-r2 (SAFE WITH CONDITIONS): lock proof uses the HEAVY_JOB_HELD label (runs nested in an outer heavy-job), fails closed without flock/readable lock; XDG_RUNTIME_DIR defaults to /run/user/UID like an ssh login.
