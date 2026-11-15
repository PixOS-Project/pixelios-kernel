#!/bin/bash
# fcap-pull LABEL OUTDIR: companion of a frame-capture boot (sprint GPU 2026-11-09), run beside tools/xrun INSIDE the same phone-lock hold
# (it needs PHONE_LOCK_TOKEN and HOT_OWNER). Waits for devd, runs `fcapctl prep` (mobile-writable /private/var/dev/fcap), then follows the
# run's live log and pulls every file libsweetgl announces (`sweetgl: FCAP WROTE <path> ...`) into OUTDIR, until `SWEET NAV: done` or the
# run dir stops growing for 300 s. One line per action; last line: RESULT: PASS fcap-pull files=N | RESULT: FAIL fcap-pull ... (why).
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -u
if [ "${1:-}" = --self-test ]; then bash -n "$0" && echo "RESULT: PASS fcap-pull self-test (syntax)" && exit 0; echo "RESULT: FAIL fcap-pull self-test"; exit 1; fi
LABEL=$1; OUT=$2
H=${HOT:-$HOME/.t3/worktrees/hot/tools/hot}
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
mkdir -p "$OUT"
export HOT_OWNER=${HOT_OWNER:-sprint-gpu}
export PIXOS_DEVD_TIMEOUT=${PIXOS_DEVD_TIMEOUT:-180}   # a 30 MB part over the USB network
t0=$(date +%s)
D=""
while [ -z "$D" ]; do
  D=$(ls -td "$ROOT"/logs/runs/*-"$LABEL" 2>/dev/null | head -1)
  [ -n "$D" ] && [ "$(stat -c %Y "$D")" -ge "$t0" ] || D=""
  [ $(( $(date +%s) - t0 )) -gt 1500 ] && { echo "RESULT: FAIL fcap-pull no run dir for $LABEL"; exit 1; }
  [ -z "$D" ] && sleep 5
done
echo "fcap-pull: run dir $D"
up=0
while [ $up = 0 ]; do
  if "$H" ping >/dev/null 2>&1; then up=1; else sleep 5; fi
  [ $(( $(date +%s) - t0 )) -gt 2400 ] && { echo "RESULT: FAIL fcap-pull devd never answered"; exit 1; }
done
echo "fcap-pull: devd up after $(( $(date +%s) - t0 )) s"
"$H" run /usr/local/bin/fcapctl prep 2>&1 | tail -3
# old captures (parts, taps, panel snapshots) of earlier boots stay on userdata until removed: 2.58 GB filled /private/var and truncated a
# capture with errno 28 (gputest-count5); every capture boot starts from an empty dir (the host keeps the pulled copies)
"$H" run /usr/local/bin/fcapctl clean 2>&1 | tail -1
# trigger fallback (the libsweetgl sysctl poll did not fire in gputest-fcap1): follow CAPREP/CAPREQ in the log and push the flag files through
# devd in its own loop, so a long pull never delays a request (libsweetgl ignores the request file whenever it can read the sysctl)
(
  T=$(mktemp -d); done_n=0
  while :; do
    L="$D/usbnet.log"; [ -f "$L" ] || L="$D/merged.log"
    mapfile -t ev < <(grep -a -o 'SWEET NAV: CAP\(REP start\|REP done\|REQ seq [0-9]* label [^ ]*\)' "$L" 2>/dev/null)
    while [ $done_n -lt ${#ev[@]} ]; do
      e=${ev[$done_n]}; done_n=$((done_n+1))
      case "$e" in
        *"REP start"*) echo 1 > "$T/e"; "$H" push "$T/e" /private/var/dev/fcap-eager >/dev/null 2>&1 && echo "fcap-pull: pushed eager 1" ;;
        *"REP done"*) echo 0 > "$T/e"; "$H" push "$T/e" /private/var/dev/fcap-eager >/dev/null 2>&1 && echo "fcap-pull: pushed eager 0"
          # patch 0276: the kernel's panel snapshots (taken at each capture request and at the end of the real-timing pass)
          n=$("$H" run /usr/local/bin/fcapctl panels /private/var/dev/fcap 2>&1 | sed -n 's/^snaps \([0-9]*\) of.*/\1/p' | head -1)
          echo "fcap-pull: kernel panel snapshots ${n:-none}"
          for ((k = 0; k < ${n:-0}; k++)); do
            f=$(printf 'snap-%02d.raw' "$k")
            "$H" pull "/private/var/dev/fcap/$f" "$OUT/$f" >/dev/null 2>&1 && echo "fcap-pull: pulled $f" || echo "fcap-pull: pull FAILED $f"
          done
          exit 0 ;;
        *"REQ seq"*) set -- $e; echo "$5 $7" > "$T/r"; "$H" push "$T/r" /private/var/dev/fcap-req >/dev/null 2>&1 && echo "fcap-pull: pushed req $5 $7"
 ;;
      esac
    done
    grep -a -q 'SWEET NAV: done' "$L" 2>/dev/null && exit 0
    sleep 1
  done
) &
n=0; seen=" "; last=$(date +%s)
while :; do
  L="$D/usbnet.log"; [ -f "$L" ] || L="$D/merged.log"
  while read -r p parts; do
    [ -n "$p" ] || continue
    case "$seen" in *" $p "*) continue ;; esac
    b=$(basename "$p"); ok=1; : > "$OUT/$b.tmp"
    for ((k = 0; k < parts; k++)); do
      part=$(printf '%s.%03d' "$p" "$k")
      if "$H" pull "$part" "$OUT/$b.part" >/dev/null 2>&1; then cat "$OUT/$b.part" >> "$OUT/$b.tmp"; else ok=0; echo "fcap-pull: pull FAILED $part (retry next round)"; break; fi
    done
    rm -f "$OUT/$b.part"
    if [ $ok = 1 ]; then mv "$OUT/$b.tmp" "$OUT/$b"; n=$((n+1)); seen="$seen$p "; echo "fcap-pull: pulled $b parts $parts $(stat -c %s "$OUT/$b") bytes"; fi
  done < <(grep -a -o 'FCAP \(TAP \)\?WROTE /private/var/dev/fcap/[^ ]*\.f\(cap\|tap\) parts [0-9]*' "$L" 2>/dev/null | sed 's/FCAP TAP /FCAP /' | awk '{print $3, $5}')
  grep -a -q 'SWEET NAV: done' "$L" 2>/dev/null && { sleep 3; grep -a -o 'FCAP WROTE /private/var/dev/fcap/[^ ]*\.fcap' "$L" | awk '{print $3}' | while read -r p; do case "$seen" in *" $p "*) ;; *) echo "fcap-pull: NOT pulled $p";; esac; done; break; }
  sz=$(stat -c %Y "$L" 2>/dev/null || echo 0); [ "$sz" -gt "$last" ] && last=$sz
  [ $(( $(date +%s) - last )) -gt 300 ] && { echo "fcap-pull: log quiet 300 s, stopping"; break; }
  sleep 2
done
"$H" run /usr/local/bin/fcapctl ls 2>&1 | tail -30
werr=$(grep -a -c 'SWEET GAP: fcap write error' "$L" 2>/dev/null)
if [ "${werr:-0}" -gt 0 ]; then echo "RESULT: FAIL fcap-pull files=$n out=$OUT: the recorder reported $werr write error(s) (truncated captures)"; exit 1; fi
echo "RESULT: PASS fcap-pull files=$n out=$OUT"
