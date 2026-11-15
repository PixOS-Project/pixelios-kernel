#!/bin/bash
# Host test for the simulation test seams (docs/plans/PLAN-SIM.md SIM-R1/R2/R5): XRUN_SYSFS_USB, XRUN_TO_FASTBOOT, XRUN_FBOOT, XRUN_BUILD, XRUN_USB_PORT,
# XRUN_RUN_PREFLIGHT, PIXOS_SLEEP_SCALE (tools/xrun, tools/usb-port, tools/xrun_report.py, tools/phone, tools/phone-lock, tools/simseams.py).
# One scratch checkout with FAKE sibling tools that only log their argv: no phone, no adb/fastboot, no heavy lock, nothing outside $TMPDIR.
# Rows: override honoured | unset default path unchanged (the real run's command transcript equals the one of the pre-seam tools/xrun, git $BASE, with the real
# /sys/bus/usb/devices replaced by a fake bus through a private mount namespace: unshare -r -m) | SIM SEAM ACTIVE line prints and is silent when unset.
#   bash tools/test_sim_seams.sh            -> last line RESULT: PASS|FAIL rows=N skipped=S
#   bash tools/test_sim_seams.sh --mutants  -> also removes each seam in /tmp/20ac/smut/<id>/ (or $SEAMS_MUT_DIR) and requires a failing row; RESULT: PASS|FAIL mutants killed=K survived=S
# SEAMS_ROOT=DIR: test the tools of DIR (a copy) instead of this checkout (used by --mutants); SEAMS_BASE_REPO=DIR: the git clone the "default unchanged" rows read the
# pre-seam tools/xrun from (--mutants sets it to the real clone, so those rows also run against a mutant of the default path). Mutants of the last review round: /tmp/20ac/smut2 (SEAMS_MUT_DIR). SKIP rows are loud and counted (rule 8): no unshare or no baseline git object.
set -uo pipefail
self=$(cd "$(dirname "$0")" && pwd)
here=${SEAMS_ROOT:-$(dirname "$self")}
base_repo=${SEAMS_BASE_REPO:-$here}
BASE=e4e85f6   # the last commit before the seams: tools/xrun there is the "unchanged default" reference
# Intended default changes that landed in tools/xrun AFTER $BASE (not seam effects): they are taken out of the NEW side before the
# "identical to the pre-seam xrun" rows compare, so any OTHER difference still fails. Add a line here (with its commit) for each new one.
#   sweetwdt=60 boot-arg default: kernel 67063f5d (autorecover, SoC watchdog)
#   `pf --gate-check ram`: T-f32e (which gate admitted the RAM boot, in the run summary)
#   implicit/boot-args lines of --print-bootargs carry the same sweetwdt=60
#   the `MIF CLK: ...` note line of --print-bootargs: T-5030 (MIF DVFS request state, printed with the boot-args)
#   the safety-check calls (pre, fastboot, monitor) of the scratch checkout's fake tools/safety-check, and the SIM SEAM line of the XRUN_SAFETY fake the xr helper sets: RULES H9 (the pre-seam xrun has neither)
intended() { sed -E 's/^implicit: sweetwdt=60$/implicit: none/; s/ --args sweetwdt=60( |$)/\1/; s/ sweetwdt=60( |$)/\1/g; /^(default|pf-default|ovr-pf) --gate-check ram$/d; /^MIF CLK: /d; /^(default|ovr)-safety-check /d; /^SIM SEAM ACTIVE: XRUN_SAFETY=/d'; }
# the sleeps the safety additions of xrun ask for (sim_sleep 0.5 after the monitor start; the 0.25 s bounded poll of safety_end): taken out of the NEW side like intended()
intended_sleeps() { grep -vx -e 0.5 -e 0.25 || true; }
tools=$here/tools
if [ "${1:-}" = --mutants ]; then
	md=${SEAMS_MUT_DIR:-/tmp/20ac/smut}; killed=0; survived=0; mk=0
	# id|file|python3 expression(old, new) applied to the file text: each removes ONE seam
	mutants=(
		'sysfs-xrun|xrun|s.replace("usbsys=${XRUN_SYSFS_USB:-/sys/bus/usb/devices}","usbsys=/sys/bus/usb/devices")'
		'sysfs-report|xrun_report.py|s.replace("glob.escape(simseams.sysfs_usb())","\"/sys/bus/usb/devices\"")'
		'sysfs-usbport|usb-port|s.replace("${SYSFS_USB:-${XRUN_SYSFS_USB:-/sys/bus/usb/devices}}","${SYSFS_USB:-/sys/bus/usb/devices}")'
		'hook-tofb|xrun|s.replace("tofb=${XRUN_TO_FASTBOOT:-tools/to-fastboot.sh}","tofb=tools/to-fastboot.sh")'
		'hook-fboot|xrun|s.replace("fbootx=${XRUN_FBOOT:-tools/fboot.sh}","fbootx=tools/fboot.sh")'
		'hook-build|xrun|s.replace("bxibin=${XRUN_BUILD:-tools/build-xnu-image.sh}","bxibin=tools/build-xnu-image.sh")'
		'hook-usbport|xrun|s.replace("usbportx=${XRUN_USB_PORT:-tools/usb-port}","usbportx=tools/usb-port")'
		'hook-preflight|xrun|s.replace("pf=(\"$XRUN_RUN_PREFLIGHT\")","pf=(python3 -I tools/run-preflight)")'
		'warn-xrun|xrun|s.replace("echo \"SIM SEAM ACTIVE: $n=${!n}\" >&2","true")'
		'warn-py|simseams.py|s.replace("print(\"SIM SEAM ACTIVE: %s=%s\" % (name, v), file=sys.stderr, flush=True)","pass")'
		'warn-usbport|usb-port|s.replace("echo \"SIM SEAM ACTIVE: XRUN_SYSFS_USB=$XRUN_SYSFS_USB\" >&2","true")'
		'scale-sh-ignored|xrun|s.replace("sleep \"$t\"; }","sleep \"$1\"; }")'
		'scale-sh-zero|xrun|s.replace("[ \"$t\" = 0.000000 ] || sleep","sleep")'
		'scale-sh-site-usbwait|xrun|s.replace("sim_sleep 1; done","sleep 1; done")'
		'scale-sh-site-cam|xrun|s.replace("\nsim_sleep 4\n","\nsleep 4\n")'
		'scale-sh-site-usbnet|xrun|s.replace("sim_sleep 1; kill -0 \"$usbpid\"","sleep 1; kill -0 \"$usbpid\"")'
		'scale-sh-site-post|xrun|s.replace("\nsim_sleep 3\n","\nsleep 3\n")'
		'scale-sh-badvalue|xrun|s.replace("sim_scale_ok \"$PIXOS_SLEEP_SCALE\" ||","true ||")'
		'scale-sh-locale|xrun|s.replace("export LC_ALL=C; ","")'
		'scale-sh-nolimit|xrun|s.replace("awk -v k=\"$1\" '"'"'BEGIN { exit !(k + 0 <= 1000) }'"'"'","true")'
		'scale-sh-nolen|xrun|s.replace(" && [ \"${#1}\" -le 30 ]","")'
		'scale-sh-bgwarn|xrun|s.replace("XRUN_RUN_PREFLIGHT PIXOS_SLEEP_SCALE XRUN_SAFETY\nsim_require","XRUN_RUN_PREFLIGHT PIXOS_SLEEP_SCALE XRUN_SAFETY &\nsim_require")'
		'scale-sh-frac-dropped|xrun|s.replace("sleep \"$t\"; }","sleep \"${t%.*}\"; }")'
		'scale-py-ignored|simseams.py|s.replace("max(k * seconds, MIN_POLL)","seconds")'
		'scale-py-zero|simseams.py|s.replace("return float(v)","return float(v) or 1.0")'
		'scale-py-badvalue|simseams.py|s.replace("if not re.fullmatch(","if False and not re.fullmatch(")'
		'scale-py-nolimit|simseams.py|s.replace(" or float(v) > MAX_SCALE","")'
		'scale-py-nolen|simseams.py|s.replace(" or len(v) > MAX_SCALE_CHARS","")'
		'scale-py-square|simseams.py|s.replace("max(k * seconds, MIN_POLL)","max(k * k * seconds, MIN_POLL)")'
		'scale-py-capped-at-1|simseams.py|s.replace("max(k * seconds, MIN_POLL)","max(min(k, 1.0) * seconds, MIN_POLL)")'
		'scale-py-nofloor|simseams.py|s.replace("MIN_POLL = 0.005","MIN_POLL = 0.0")'
		'scale-py-nolimit-cap|simseams.py|s.replace("min(max(k * seconds, MIN_POLL), max(limit, MIN_POLL)) if limit is not None else ","")'
		'sandbox-off|simseams.py|s.replace("if bad and not in_sandbox():","if False:")'
		'sandbox-default-lock|simseams.py|s.replace("real != os.path.realpath(DEFAULT_LOCK) and ","")'
		'sandbox-symlink|simseams.py|s.replace("real = os.path.realpath(lock)","real = os.path.abspath(lock)")'
		'sandbox-unset-ok|simseams.py|s.replace("    if not lock:\n        return False","    if not lock:\n        return True")'
		'require-any|xrun|s.replace("[ -z \"$any\" ] || python3","true || python3")'
		'require-no-tofb|xrun|s.replace("sim_require XRUN_TO_FASTBOOT XRUN_FBOOT","sim_require XRUN_FBOOT")'
		'require-no-fboot|xrun|s.replace("sim_require XRUN_TO_FASTBOOT XRUN_FBOOT XRUN_BUILD","sim_require XRUN_TO_FASTBOOT XRUN_BUILD")'
		'require-no-build|xrun|s.replace("XRUN_FBOOT XRUN_BUILD XRUN_USB_PORT","XRUN_FBOOT XRUN_USB_PORT")'
		'require-no-usbport|xrun|s.replace("XRUN_BUILD XRUN_USB_PORT XRUN_RUN_PREFLIGHT XRUN_SAFETY\n","XRUN_BUILD XRUN_RUN_PREFLIGHT XRUN_SAFETY\n")'
		'require-no-preflight|xrun|s.replace("XRUN_USB_PORT XRUN_RUN_PREFLIGHT XRUN_SAFETY\n","XRUN_USB_PORT XRUN_SAFETY\n")'
		'require-no-safety|xrun|s.replace("XRUN_USB_PORT XRUN_RUN_PREFLIGHT XRUN_SAFETY\n","XRUN_USB_PORT XRUN_RUN_PREFLIGHT\n")'
		'hook-safety|xrun|s.replace("sc=(\"$XRUN_SAFETY\")","sc=(python3 -I tools/safety-check)")'
		'mark-seams|xrun|s.replace("$R mark \"$D\" sim_seams=\"$simseams_list\"","true")'
		'mark-seams-log|xrun|s.replace(">>\"$LOG\"; }   # rule 8: the run record","> /dev/null; }   # rule 8: the run record")'
		'mark-seams-report|xrun_report.py|s.replace("out.append(\"SIM SEAMS ACTIVE: \"","out.append(\"SEAMS: \"")'
		'mark-seams-list|xrun|s.replace("simseams_list+=\"${simseams_list:+,}$n\"","true")'
		'usb-recovery-site|xrun|s.replace("18d1 \"$usbsys\"/*/idVendor; } && timeout 220","18d1 /sys/bus/usb/de?ices/*/idVendor; } && timeout 220")'
		'glob-escape|xrun_report.py|s.replace("glob.escape(simseams.sysfs_usb())","simseams.sysfs_usb()")'
		'scale-site-phone|phone|s.replace("simseams.poll(0.5)","time.sleep(0.5)")'
		'phone-poll-5|phone|s.replace("simseams.poll(0.5)","simseams.poll(5)")'
		'start-check-phone|phone|s.replace("def main(argv=None):\n    simseams.scale()","def main(argv=None):\n    pass")'
		'start-check-lock|phone-lock|s.replace("def main():\n    simseams.scale()","def main():\n    pass")'
		'scale-site-lock-poll|phone-lock|s.replace("simseams.poll(min(0.2,","time.sleep(min(0.2,")'
		'lock-poll-nolimit|phone-lock|s.replace(", limit=end - time.time())",")")'
		'lock-grace-scaled|phone-lock|s.replace("time.sleep(0.5)   # real time","simseams.poll(0.5)   # real time")'
		'lock-grace-range|phone-lock|s.replace("range(60)","range(1)")'
		'lock-grace-50|phone-lock|s.replace("time.sleep(0.5)   # real time","time.sleep(50)   # real time")'
	)
	for m in "${mutants[@]}"; do
		IFS='|' read -r id f expr <<<"$m"
		[ -z "${SEAMS_MUT_ONLY:-}" ] || [[ $id =~ $SEAMS_MUT_ONLY ]] || continue   # SEAMS_MUT_ONLY=REGEX: only the mutants whose id matches
		d=$md/$id; rm -rf "$d"; mkdir -p "$d/tools"
		cp "$here"/tools/{xrun,xrun_report.py,qrmerge.py,simseams.py,safety_lib.py,usb-port,phone,phone-lock,plock.sh} "$d/tools/"
		MUT_F="$d/tools/$f" python3 -I -c 'import os,sys; p=os.environ["MUT_F"]; s=open(p).read(); n=eval(sys.argv[1]); sys.exit(3 if n==s else (open(p,"w").write(n), 0)[1])' "$expr"
		[ $? = 0 ] || { echo "MUTANT $id: the edit did not change $f (stale needle): fix the mutant table"; survived=$((survived + 1)); continue; }
		mk=$((mk + 1))
		out=$(SEAMS_ROOT=$d SEAMS_MUT_RUN=1 SEAMS_BASE_REPO=$base_repo bash "$self/test_sim_seams.sh" 2>&1); rc=$?
		if [ $rc != 0 ] && grep -q '^FAIL ' <<<"$out"; then killed=$((killed + 1)); echo "MUTANT $id killed by: $(grep -m1 '^FAIL ' <<<"$out")"
		else survived=$((survived + 1)); echo "MUTANT $id SURVIVED (rc=$rc)"; fi
	done
	echo "RESULT: $([ "$survived" = 0 ] && echo PASS || echo FAIL) mutants=$mk killed=$killed survived=$survived"
	[ "$survived" = 0 ]; exit
fi

w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
n=0; bad=0; skipped=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=$((bad + 1)); fi; }
skip() { n=$((n + 1)); skipped=$((skipped + 1)); echo "SKIP $1"; }
elapsed() { local t0 t1; t0=$(date +%s.%N); "$@" >/dev/null 2>&1; t1=$(date +%s.%N); awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.3f", b - a }'; }
lt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }   # a < b

# ---------- shell helper rows (the function text is cut out of tools/xrun, so the real code runs)
sed -n '/^sim_warn() {/,/^sim_warn [A-Z]/p' "$tools/xrun" | sed '$d' >"$w/helpers.sh"
ok "xrun defines sim_warn and sim_sleep" "$(grep -q '^sim_sleep() ' "$w/helpers.sh" && grep -q '^sim_warn() ' "$w/helpers.sh" && echo 1 || echo 0)"
t_unset=$(env -u PIXOS_SLEEP_SCALE bash -c '. "$1"; sim_sleep 1' _ "$w/helpers.sh" 2>&1 >/dev/null; elapsed env -u PIXOS_SLEEP_SCALE bash -c '. "$1"; sim_sleep 1' _ "$w/helpers.sh")
ok "sh unset: sim_sleep 1 sleeps real time (>= 0.9 s, was $t_unset)" "$(lt 0.9 "$t_unset" && echo 1 || echo 0)"
t=$(elapsed env PIXOS_SLEEP_SCALE=0.01 bash -c '. "$1"; sim_sleep 3' _ "$w/helpers.sh")
ok "sh scale 0.01: sim_sleep 3 is fast (< 0.5 s, was $t)" "$(lt "$t" 0.5 && echo 1 || echo 0)"
t=$(elapsed env PIXOS_SLEEP_SCALE=0 bash -c '. "$1"; sim_sleep 3' _ "$w/helpers.sh")
ok "sh scale 0: sim_sleep 3 does not sleep (< 0.3 s, was $t)" "$(lt "$t" 0.3 && echo 1 || echo 0)"
t=$(elapsed env PIXOS_SLEEP_SCALE=0.5 bash -c '. "$1"; sim_sleep 2' _ "$w/helpers.sh")
ok "sh scale 0.5: sim_sleep 2 sleeps about 1 s (0.8..1.5, was $t)" "$(lt 0.8 "$t" && lt "$t" 1.5 && echo 1 || echo 0)"
long31=$(printf '9%.0s' $(seq 31)); longfrac="0.$(printf '0%.0s' $(seq 30))1";   # 32 chars, value ~1e-31: only the length bound rejects it
 long400=$(printf '9%.0s' $(seq 400))
for bad_v in abc -1 1e3 0x1 1,5 ' 1' 1001 99999 1000.001 "$long31" "$long400" "$longfrac" １ ０.５ 1.５; do   # last three: fullwidth digits (F5)
	o=$(env LC_ALL=en_US.utf8 PIXOS_SLEEP_SCALE="$bad_v" timeout 5 bash -c '. "$1"; sim_warn; echo reached' _ "$w/helpers.sh" 2>&1); rc=$?
	ok "sh scale '${bad_v:0:40}' is SIM SEAM ERROR, exit 2, not silently real time (bounds 1000 / 30 chars, ASCII digits in a UTF-8 locale)" "$([ $rc = 2 ] && grep -q '^SIM SEAM ERROR: PIXOS_SLEEP_SCALE=' <<<"$o" && ! grep -q reached <<<"$o" && echo 1 || echo 0)"
done
for good_v in 0 00 0.5 1000 1000.0 "$(printf '1%.0s' $(seq 30))"; do
	o=$(env LC_ALL=en_US.utf8 PIXOS_SLEEP_SCALE="$good_v" bash -c '. "$1"; sim_warn; echo reached' _ "$w/helpers.sh" 2>&1)
	ok "sh scale '${good_v:0:40}' is accepted (inside 0..1000, <= 30 chars)" "$([ "$o" = reached ] || { [ "${#good_v}" = 30 ] && grep -q '^SIM SEAM ERROR' <<<"$o"; } && echo 1 || echo 0)"
done
# the exact scaled value reaches sleep (a recording sleep on PATH, filled in below once the fake bin exists): MO
mkdir -p "$w/fakebin"; printf '#!/bin/bash\necho "$*" >>"$SLEEPLOG"\n' >"$w/fakebin/sleep"; chmod +x "$w/fakebin/sleep"
: >"$w/sleeps.h"; env PATH="$w/fakebin:$PATH" SLEEPLOG="$w/sleeps.h" PIXOS_SLEEP_SCALE=0.5 bash -c '. "$1"; sim_sleep 3; sim_sleep 1; sim_sleep 0.4' _ "$w/helpers.sh"
ok "sh scale 0.5: sim_sleep 3 / 1 / 0.4 hand sleep exactly 1.500000 / 0.500000 / 0.200000 (fractions kept; recorded: $(tr '\n' ' ' <"$w/sleeps.h"))" "$([ "$(tr '\n' ' ' <"$w/sleeps.h")" = '1.500000 0.500000 0.200000 ' ] && echo 1 || echo 0)"
o=$(env PIXOS_SLEEP_SCALE=0.25 XRUN_BUILD=/x/y bash -c '. "$1"; sim_warn PIXOS_SLEEP_SCALE XRUN_BUILD XRUN_FBOOT' _ "$w/helpers.sh" 2>&1 >/dev/null)
ok "sh WARN: one SIM SEAM ACTIVE line per active name, none for unset XRUN_FBOOT" "$([ "$o" = $'SIM SEAM ACTIVE: PIXOS_SLEEP_SCALE=0.25\nSIM SEAM ACTIVE: XRUN_BUILD=/x/y' ] && echo 1 || echo 0)"
o=$(env -u PIXOS_SLEEP_SCALE -u XRUN_BUILD bash -c '. "$1"; sim_warn PIXOS_SLEEP_SCALE XRUN_BUILD' _ "$w/helpers.sh" 2>&1)
ok "sh WARN: nothing printed when unset" "$([ -z "$o" ] && echo 1 || echo 0)"
# wiring of the xrun sleep sites (the e2e rows below cannot reach the camera and usbnet branches)
ok "xrun: exactly 6 sim_sleep call sites (usb wait, camera start, usbnet start, safety monitor start, post-wait, recovery retry T-2cec/T-35ba)" "$([ "$(grep -v '^sim_sleep() ' "$tools/xrun" | grep -c 'sim_sleep [0-9]')" = 6 ] && echo 1 || echo 0)"
ok "xrun: no other bare 'sleep N' except the two camera-kill grace sleeps and the 0.25 s bounded poll of safety_end (RULES H9)" "$([ "$(grep -v '^sim_sleep() \|^	\[ -n "\${PIXOS_SLEEP_SCALE' "$tools/xrun" | grep -v '^#' | grep -cE '(^|[;{] *|[ 	])sleep [0-9]')" = 3 ] && grep -v '^#' "$tools/xrun" | grep -q 'safety_end() .*sleep 0.25' && echo 1 || echo 0)"
ok "xrun: /sys/bus/usb/devices appears once in code (the default of usbsys)" "$([ "$(grep -v '^#' "$tools/xrun" | grep -c '/sys/bus/usb/devices')" = 1 ] && echo 1 || echo 0)"

# ---------- python helper rows (a recording time.sleep: the rows read WHAT was slept, not how long a loaded host took, AP-316)
py() { env -u PIXOS_SLEEP_SCALE -u XRUN_SYSFS_USB "$@"; }
spy='import sys; sys.path.insert(0, sys.argv[1]); import simseams; rec = []; simseams.time.sleep = rec.append; simseams.poll(float(sys.argv[2]), *[float(a) for a in sys.argv[3:]]); print(rec[0] if rec else "none")'
r=$(py python3 -I -c "$spy" "$tools" 0.3 2>/dev/null)
ok "py unset: poll(0.3) is time.sleep(0.3) exactly (was '$r')" "$([ "$r" = 0.3 ] && echo 1 || echo 0)"
r=$(py python3 -I -c "$spy" "$tools" 0.3 1 2>/dev/null)
ok "py unset: poll(0.3, limit=1) is still time.sleep(0.3) (the limit only caps a scaled poll; was '$r')" "$([ "$r" = 0.3 ] && echo 1 || echo 0)"
for row in "0.5 1 0.5" "2 1 2.0" "0.01 3 0.03" "0 3 0.005" "0 0.5 0.005" "1000 0.2 200.0"; do
	set -- $row
	r=$(py env PIXOS_SLEEP_SCALE="$1" python3 -I -c "$spy" "$tools" "$2" 2>/dev/null)
	ok "py scale $1: poll($2) sleeps exactly $3 (scale 0 = the 5 ms floor, no busy spin; was '$r')" "$([ "$r" = "$3" ] && echo 1 || echo 0)"
done
r=$(py env PIXOS_SLEEP_SCALE=100 python3 -I -c "$spy" "$tools" 0.2 1 2>/dev/null)
ok "py scale 100: poll(0.2, limit=1) is capped at the limit 1.0 (a --wait bound; was '$r')" "$([ "$r" = 1.0 ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=100 python3 -I -c "$spy" "$tools" 0.2 0 2>/dev/null)
ok "py scale 100: poll(0.2, limit=0) is the floor 0.005 (was '$r')" "$([ "$r" = 0.005 ] && echo 1 || echo 0)"
for bad_v in abc -1 1e3 0x1 1,5 1001 99999 1000.001 "$long31" "$long400" "$longfrac" １ ０.５ 1.５; do
	o=$(py env LC_ALL=en_US.utf8 PIXOS_SLEEP_SCALE="$bad_v" python3 -I -c "$spy" "$tools" 0.01 2>&1); rc=$?
	ok "py scale '${bad_v:0:40}' is SIM SEAM ERROR, exit 2 (bounds 1000 / 30 chars, ASCII digits)" "$([ $rc = 2 ] && grep -q '^SIM SEAM ERROR: PIXOS_SLEEP_SCALE=' <<<"$o" && echo 1 || echo 0)"
done
for good_v in 0 00 0.5 1000 1000.0; do
	o=$(py env PIXOS_SLEEP_SCALE="$good_v" python3 -I -c "$spy" "$tools" 0.01 2>&1); rc=$?
	ok "py scale '$good_v' is accepted" "$([ $rc = 0 ] && ! grep -q 'SIM SEAM ERROR' <<<"$o" && echo 1 || echo 0)"
done
o=$(py env PIXOS_SLEEP_SCALE=0.01 python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import simseams; simseams.time.sleep = lambda s: None; simseams.poll(0.1); simseams.poll(0.1)' "$tools" 2>&1 >/dev/null)
ok "py WARN: SIM SEAM ACTIVE: PIXOS_SLEEP_SCALE=0.01 once for two polls" "$([ "$o" = 'SIM SEAM ACTIVE: PIXOS_SLEEP_SCALE=0.01' ] && echo 1 || echo 0)"
o=$(py python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import simseams; simseams.time.sleep = lambda s: None; simseams.poll(0.01); simseams.sysfs_usb()' "$tools" 2>&1)
ok "py WARN: nothing printed when unset" "$([ -z "$o" ] && echo 1 || echo 0)"
# phone-lock queue poll and process-group grace (module-driven: time.sleep recorded, the loops spin through)
cat >"$w/lockpoll.py" <<'EOF'
import importlib.machinery, importlib.util, os, sys, tempfile, time
os.environ["PHONE_LOCK_DIR"] = os.path.join(tempfile.mkdtemp(), "lock")
tools = sys.argv[1]
sys.path.insert(0, tools)
loader = importlib.machinery.SourceFileLoader("phone_lock", os.path.join(tools, "phone-lock"))
spec = importlib.util.spec_from_loader("phone_lock", loader)
pl = importlib.util.module_from_spec(spec)
loader.exec_module(pl)
rec = []
real = time.sleep
def fake(s):
    rec.append(s)
    real(0.001)   # keep the loops from hammering the disk
time.sleep = fake   # one module object: simseams.time, pl.time and this one are the same
assert pl.acquire("a", 0, 100, os.getpid(), "x")[0] == 0
pl.acquire("b", 1, 100, os.getpid(), "y")           # held by "a": polls for 1 s of real time
print("%d %.4f %.4f" % (len(rec), min(rec) if rec else -1, max(rec) if rec else -1))
EOF
r=$(py python3 -I "$w/lockpoll.py" "$tools" 2>/dev/null)
ok "phone-lock acquire poll: default sleeps 0.2 s (many polls; was '$r')" "$(set -- $r; [ "$1" -gt 3 ] 2>/dev/null && [ "$3" = 0.2000 ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=0.01 python3 -I "$w/lockpoll.py" "$tools" 2>/dev/null)
ok "phone-lock acquire poll: PIXOS_SLEEP_SCALE=0.01 scales it to the 5 ms floor (was '$r')" "$(set -- $r; [ "$1" -gt 3 ] 2>/dev/null && [ "$2" = 0.0050 ] && [ "$3" = 0.0050 ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=0 python3 -I "$w/lockpoll.py" "$tools" 2>/dev/null)
ok "phone-lock acquire poll: scale 0 never sleeps less than the 5 ms floor, no busy spin (was '$r')" "$(set -- $r; [ "$1" -gt 3 ] 2>/dev/null && [ "$2" = 0.0050 ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=0.5 python3 -I "$w/lockpoll.py" "$tools" 2>/dev/null)
ok "phone-lock acquire poll: scale 0.5 sleeps 0.1 s exactly (was '$r')" "$(set -- $r; [ "$1" -gt 3 ] 2>/dev/null && [ "$3" = 0.1000 ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=100 python3 -I "$w/lockpoll.py" "$tools" 2>/dev/null)
ok "phone-lock acquire --wait 1 at scale 100: no poll outlasts the wait (max <= 1.0; was '$r')" "$(set -- $r; [ "$1" -ge 1 ] 2>/dev/null && awk -v m="$3" 'BEGIN { exit !(m <= 1.0001) }' && echo 1 || echo 0)"
cat >"$w/lockgrace.py" <<'EOF'
import argparse, importlib.machinery, importlib.util, os, sys, tempfile, time
os.environ["PHONE_LOCK_DIR"] = os.path.join(tempfile.mkdtemp(), "lock")
tools = sys.argv[1]
sys.path.insert(0, tools)
loader = importlib.machinery.SourceFileLoader("phone_lock", os.path.join(tools, "phone-lock"))
spec = importlib.util.spec_from_loader("phone_lock", loader)
pl = importlib.util.module_from_spec(spec)
loader.exec_module(pl)
rec = []
real = time.sleep
def fake(s):
    rec.append(s)
    real(0.001)
time.sleep = fake
# a child that leaves a backgrounded process in its group: run() keeps the lock 60 x 0.5 s (the 30 s grace), whatever the scale
rc = pl.run(argparse.Namespace(owner="t", wait=0, ttl=100, cmd=["bash", "-c", "sleep 3 & exit 0"]))
g = [x for x in rec if x == 0.5]   # the grace sleeps; since 2026-11-09 run() then stops the leftovers (0.2 s polls of phone_lock_end.stop_group)
print("%d %.4f %.4f" % (len(g), min(g) if g else -1, max(g) if g else -1), file=sys.stderr)
EOF
for sc in "" 0 0.01; do
	r=$(py env ${sc:+PIXOS_SLEEP_SCALE=$sc} python3 -I "$w/lockgrace.py" "$tools" 2>&1 >/dev/null | tail -n 1)
	ok "phone-lock run grace: scale '${sc:-unset}' still asks for 60 real sleeps of 0.5 s (30 s grace; was '$r')" "$([ "$r" = "60 0.5000 0.5000" ] && echo 1 || echo 0)"
done
# the same through the CLI: scale 0 keeps the lock until the backgrounded child of the group is gone (the run returns only then)
mkdir -p "$w/lkcli"; rm -f "$w/lkcli/child.pid"
py env PHONE_LOCK_DIR="$w/lkcli/lock" PIXOS_SLEEP_SCALE=0 python3 -I "$tools/phone-lock" run -- bash -c 'sleep 2 & echo $! >"$1"; exit 0' _ "$w/lkcli/child.pid" >/dev/null 2>&1
cpid=$(cat "$w/lkcli/child.pid" 2>/dev/null)
ok "phone-lock run at scale 0: the lock outlived the child's group (the backgrounded child pid ${cpid:-?} is gone when run returned)" "$([ -n "$cpid" ] && ! kill -0 "$cpid" 2>/dev/null && echo 1 || echo 0)"
[ -z "$cpid" ] || kill "$cpid" 2>/dev/null || true
ok "phone, phone-lock: no raw time.sleep except the one real_sleep of phone_lock_end (grace, stop, end, follow); simseams.poll at 1 (phone) + 1 (phone-lock acquire) sites; no simseams.sleep left" "$([ "$(grep -c 'time\.sleep(' "$tools/phone")" = 0 ] && [ "$(grep -c 'time\.sleep(' "$tools/phone-lock")" = 0 ] && [ "$(grep -c 'time\.sleep(' "$tools/phone_lock_end.py")" = 1 ] && [ "$(grep -c 'simseams\.poll(' "$tools/phone")" = 1 ] && [ "$(grep -c 'simseams\.poll(' "$tools/phone-lock")" = 1 ] && ! grep -q 'simseams\.sleep' "$tools/phone" "$tools/phone-lock" && echo 1 || echo 0)"
# one goto pass of tools/phone: the poll between passes is simseams.poll(0.5) (ME), driven with every collaborator stubbed (no adb, no fastboot)
cat >"$w/gotopoll.py" <<'EOF'
import argparse, importlib.machinery, importlib.util, os, sys, types
tools = sys.argv[1]
sys.path.insert(0, tools)
loader = importlib.machinery.SourceFileLoader("phone_mod", os.path.join(tools, "phone"))
spec = importlib.util.spec_from_loader("phone_mod", loader)
ph = importlib.util.module_from_spec(spec)
loader.exec_module(ph)
rec = []
ph.simseams.time.sleep = rec.append
states = iter(["android", "android", "android", "fastboot"])   # initial observe, top of pass 1, after the table command (not there yet: sleep), top of pass 2
ph.observe_state = lambda logger=None: next(states)
ph.safe_device_cmd = lambda cmd, timeout_s=15, logger=None, **kw: (0, "", "")
ph.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
logger = types.SimpleNamespace(log=lambda *a, **k: None, console=lambda *a, **k: None, emit_user_prompt=lambda *a, **k: None, close=lambda: None)
ph.make_report = lambda logger, verdict, state, target, steps, extra=None: verdict
v = ph.cmd_goto(argparse.Namespace(target="fastboot", timeout=60, dry_run=False, yes_user_actions=False), logger)
print(v, rec)
EOF
r=$(py python3 -I "$w/gotopoll.py" "$tools" 2>&1 | tail -n 1)
ok "phone goto: one pass between two observations sleeps exactly 0.5 s by default (was '$r')" "$([ "$r" = "PASS [0.5]" ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=0.01 python3 -I "$w/gotopoll.py" "$tools" 2>&1 | tail -n 1)
ok "phone goto: PIXOS_SLEEP_SCALE=0.01 polls 0.005 s (was '$r')" "$([ "$r" = "PASS [0.005]" ] && echo 1 || echo 0)"
r=$(py env PIXOS_SLEEP_SCALE=0 python3 -I "$w/gotopoll.py" "$tools" 2>&1 | tail -n 1)
ok "phone goto: PIXOS_SLEEP_SCALE=0 polls the 5 ms floor, not zero (was '$r')" "$([ "$r" = "PASS [0.005]" ] && echo 1 || echo 0)"
# a bad scale stops phone and phone-lock at start, before any command (F6): fake adb/fastboot/lsusb only, the real ones are not on PATH
mkdir -p "$w/fk"; for t in adb fastboot lsusb ping ip; do printf '#!/bin/bash\necho "%s $*" >>"$FKLOG"\nexit 1\n' "$t" >"$w/fk/$t"; chmod +x "$w/fk/$t"; done
pybin=$(command -v python3); : >"$w/fk.calls"
o=$(env PATH="$w/fk" FKLOG="$w/fk.calls" PIXOS_SLEEP_SCALE=abc "$pybin" -I "$tools/phone" state 2>&1); rc=$?
ok "phone: PIXOS_SLEEP_SCALE=abc is exit 2 at start, no adb/fastboot/lsusb/ping called, no RESULT line (rc=$rc)" "$([ $rc = 2 ] && grep -q '^SIM SEAM ERROR: PIXOS_SLEEP_SCALE=abc' <<<"$o" && ! grep -q '^RESULT' <<<"$o" && [ ! -s "$w/fk.calls" ] && echo 1 || echo 0)"
rm -f "$w/lkcli/X"
o=$(env PHONE_LOCK_DIR="$w/lkcli/lock2" PIXOS_SLEEP_SCALE=abc "$pybin" -I "$tools/phone-lock" run -- touch "$w/lkcli/X" 2>&1); rc=$?
ok "phone-lock run: PIXOS_SLEEP_SCALE=abc is exit 2 at start; the command never ran (no X) and no lock dir (rc=$rc)" "$([ $rc = 2 ] && grep -q '^SIM SEAM ERROR: PIXOS_SLEEP_SCALE=abc' <<<"$o" && [ ! -e "$w/lkcli/X" ] && [ ! -e "$w/lkcli/lock2" ] && echo 1 || echo 0)"
o=$(env PHONE_LOCK_DIR="$w/lkcli/lock3" PIXOS_SLEEP_SCALE=0.5 "$pybin" -I "$tools/phone-lock" status 2>&1)
ok "phone-lock: a valid scale prints SIM SEAM ACTIVE at start even when no sleep happens (status)" "$(grep -qx 'SIM SEAM ACTIVE: PIXOS_SLEEP_SCALE=0.5' <<<"$o" && echo 1 || echo 0)"

# ---------- usb-port and xrun_report: XRUN_SYSFS_USB
mkdir -p "$w/bus/1-1" "$w/bus/empty"
echo 18d1 >"$w/bus/1-1/idVendor"; echo 4ee0 >"$w/bus/1-1/idProduct"; echo 480 >"$w/bus/1-1/speed"
o=$(env -u SYSFS_USB XRUN_SYSFS_USB="$w/bus" bash "$tools/usb-port" 2>"$w/up.err")
ok "usb-port honours XRUN_SYSFS_USB (finds the fake 1-1 at 480)" "$(grep -q '^usb-port: 1-1 speed=480 product=4ee0' <<<"$o" && grep -q '^RESULT: PASS' <<<"$o" && echo 1 || echo 0)"
ok "usb-port WARN: SIM SEAM ACTIVE: XRUN_SYSFS_USB=... on stderr, stdout unchanged" "$(grep -qx "SIM SEAM ACTIVE: XRUN_SYSFS_USB=$w/bus" "$w/up.err" && ! grep -q SIM <<<"$o" && echo 1 || echo 0)"
o=$(env -u SYSFS_USB XRUN_SYSFS_USB="$w/bus/empty" bash "$tools/usb-port" 2>/dev/null)
ok "usb-port: empty fake bus gives 0 Google devices (the real bus is not read)" "$(grep -q 'usbport: 0 Google' <<<"$o" && echo 1 || echo 0)"
o=$(env SYSFS_USB="$w/bus/empty" XRUN_SYSFS_USB="$w/bus" bash "$tools/usb-port" 2>/dev/null)
ok "usb-port: the older SYSFS_USB wins when both are set" "$(grep -q 'usbport: 0 Google' <<<"$o" && echo 1 || echo 0)"
o=$(env -u SYSFS_USB -u XRUN_SYSFS_USB bash "$tools/usb-port" 2>&1 >/dev/null)
ok "usb-port: nothing on stderr when unset" "$([ -z "$o" ] && echo 1 || echo 0)"
rp='import sys; sys.path.insert(0, sys.argv[1]); import xrun_report as r; print(r.usb_present(), r.usb_ids())'
o=$(py env XRUN_SYSFS_USB="$w/bus" python3 -c "$rp" "$tools" 2>&1)
ok "xrun_report usb_present/usb_ids honour XRUN_SYSFS_USB (+ WARN)" "$([ "$o" = $'SIM SEAM ACTIVE: XRUN_SYSFS_USB='"$w/bus"$'\nTrue 18d1:4ee0' ] && echo 1 || echo 0)"
o=$(py env XRUN_SYSFS_USB="$w/bus/empty" python3 -c "$rp" "$tools" 2>/dev/null)
ok "xrun_report: empty fake bus -> False, ''" "$([ "$o" = "False " ] && echo 1 || echo 0)"
mkdir -p "$w/br[1]/1-1" "$w/st*r/1-1"; for b in "br[1]" "st*r"; do echo 18d1 >"$w/$b/1-1/idVendor"; echo 4ee0 >"$w/$b/1-1/idProduct"; done
for b in "br[1]" "st*r"; do
	o=$(py env XRUN_SYSFS_USB="$w/$b" python3 -c "$rp" "$tools" 2>/dev/null)
	ok "xrun_report: a glob character in XRUN_SYSFS_USB ('$b') is a literal directory name (glob.escape; True 18d1:4ee0; was '$o')" "$([ "$o" = "True 18d1:4ee0" ] && echo 1 || echo 0)"
done
want=$(compgen -G '/sys/bus/usb/devices/*/idVendor' >/dev/null && grep -lsx 18d1 /sys/bus/usb/devices/*/idVendor >/dev/null && echo True || echo False)
o=$(py python3 -c "$rp" "$tools" 2>&1 | head -1)
ok "xrun_report unset: reads the real /sys/bus/usb/devices (independent grep says $want) and prints no WARN" "$([ "${o%% *}" = "$want" ] && ! grep -q SIM <<<"$o" && echo 1 || echo 0)"

# ---------- scratch checkout with fake sibling tools, one real xrun cycle (--no-camera --no-usbnet)
mkck() {   # DIR XRUN_FILE
	local d=$1 x=$2 t
	mkdir -p "$d/tools" "$d/shim/out" "$d/logs" "$d/docs"
	cp "$x" "$d/tools/xrun"; cp "$tools"/{xrun_report.py,qrmerge.py,simseams.py,safety_lib.py,plock.sh,overlay_std.py,hfs_read.py,mkdevoverlay.py,xrun_early.py,crash_summary.py,panic_capture.py,xrun_secret_scan.py,envfile.py} "$d/tools/"; chmod +x "$d/tools/xrun"
	cat >"$d/tools/fakebody" <<'EOF'
# sourced by every fake: logs "TAG args" and answers like the real tool would on a healthy fastboot phone
echo "$TAG $*" >>"$CALLS"
EOF
	for t in to-fastboot.sh fboot.sh usb-port build-xnu-image.sh xnu-remote-build phone-lock phone; do
		mkfake "$d/tools/$t" "$t" default
	done
	mksafe "$d/tools/safety-check" default   # what the unset-XRUN_SAFETY default path (python3 -I tools/safety-check) runs in the scratch checkout
	printf '#!/usr/bin/env python3\nimport os, sys\nopen(os.environ["CALLS"], "a").write("pf-default " + " ".join(sys.argv[1:]) + "\\n")\nif "--userdata-check" in sys.argv:\n    print("RESULT: PASS userdata ios")\n' >"$d/tools/run-preflight"
	: >"$d/kernel"; : >"$d/adt"
}
mksafe() {   # PATH TAGKIND -> a fake safety-check (XRUN_SAFETY): pre passes, fastboot passes, monitor lives until its --until file exists, notify ok; logs "<kind>-safety-check <verb>"
	local f=$1 kind=$2
	cat >"$f" <<EOF
#!/usr/bin/env python3
import os, sys, time
a = sys.argv[1:]
open(os.environ["CALLS"], "a").write("$kind-safety-check %s\n" % (a[0] if a else ""))
if a[:1] == ["pre"]:
    print("SAFETY PASS pre S7: fake")
    print("RESULT: PASS safety-check pre fake")
elif a[:1] == ["monitor"]:
    until = a[a.index("--until") + 1] if "--until" in a else None
    while not (until and os.path.exists(until)):
        time.sleep(0.05)
EOF
	chmod +x "$f"
}
mkfake() {   # PATH NAME TAGKIND -> an executable fake that logs "<kind>-<name> argv"
	local f=$1 name=$2 kind=$3
	{
		echo '#!/bin/bash'
		echo "echo \"$kind-$name \$*\" >>\"\$CALLS\""
		case $name in
			usb-port) echo 'echo "usb-port: 1-1 speed=5000 product=4ee0"; echo "RESULT: PASS usbport 1-1 speed=5000 sweetusb3=1"' ;;
			build-xnu-image.sh) echo 'if [ -n "${XNU_IMAGE_ARGS_ONLY:-}" ]; then echo "boot-args: fake-only"; exit 0; fi; mkdir -p shim/out; echo img >shim/out/shim-boot-avb.img; echo "{}" >shim/out/shim-boot-avb.img.meta.json; echo "boot-args: fake"' ;;
			xnu-remote-build) echo 'echo "XNU BUILD: fake"' ;;
			phone-lock) echo 'exit 0' ;;
			phone) echo 'echo "RESULT: PASS state=fastboot target=none steps=0"' ;;
		esac
	} >"$f"
	chmod +x "$f"
}
xr() {   # DIR TAG ENV... -- ARGS -> runs DIR/tools/xrun in DIR; stdout+stderr to $w/out.$TAG, transcript to $w/calls.$TAG, rc in $w/rc.$TAG
	local d=$1 tag=$2; shift 2
	local envs=(); while [ "$1" != -- ]; do envs+=("$1"); shift; done; shift
	: >"$w/calls.$tag"; : >"$w/sleeps.$tag"; rm -rf "$d/logs/runs"
	( cd "$d" && env PATH="$w/fakebin:$PATH" SLEEPLOG="$w/sleeps.$tag" CALLS="$w/calls.$tag" XRUN_PHONE="$d/tools/phone" XRUN_NO_RUNPLAN="sim seam host test, not a run" ARTIFACTS="$w/art" PHONE_LOCK_DIR="$w/xlock/lock" XRUN_SAFETY="$w/safe-ovr" "${envs[@]}" \
		timeout 120 ./tools/xrun --kernel "$d/kernel" --adt "$d/adt" --no-camera --no-usbnet --duration 1 --restart 1 --label simseam "$@" ) >"$w/out.$tag" 2>&1
	echo $? >"$w/rc.$tag"
	sed -E 's/[0-9]{8}T[0-9]{6}Z/TS/g; s#'"$d"'#CK#g' "$w/calls.$tag" >"$w/norm.$tag"
}
mkdir -p "$w/art" "$w/fakebin"
printf '#!/bin/bash\necho "$*" >>"$SLEEPLOG"\n' >"$w/fakebin/sleep"; chmod +x "$w/fakebin/sleep"   # a recording sleep: the e2e rows read WHICH sleeps ran, not how long the (loaded) host took
mksafe "$w/safe-ovr" ovr
mkck "$w/new" "$tools/xrun"
sed -i 's#^TAG=.*##' "$w/new/tools/fakebody"

# one run with EVERY hook, a fake bus and scale 0: each override is called, no default sibling is
ovr="$w/ovr"; mkdir -p "$ovr"
for t in to-fastboot.sh fboot.sh usb-port build-xnu-image.sh; do mkfake "$ovr/$t" "$t" ovr; done
printf '#!/bin/bash\necho "ovr-pf $*" >>"$CALLS"\ncase " $* " in *" --userdata-check "*) echo "RESULT: PASS userdata ios" ;; esac\n' >"$ovr/pf"; chmod +x "$ovr/pf"
xr "$w/new" hooks XRUN_SYSFS_USB="$w/bus" XRUN_TO_FASTBOOT="$ovr/to-fastboot.sh" XRUN_FBOOT="$ovr/fboot.sh" XRUN_BUILD="$ovr/build-xnu-image.sh" XRUN_USB_PORT="$ovr/usb-port" XRUN_RUN_PREFLIGHT="$ovr/pf" PIXOS_SLEEP_SCALE=0 --
c=$w/calls.hooks
ok "e2e hooks run reached fboot (rc/out: $(cat "$w/rc.hooks"), $(grep -c . "$c") calls)" "$(grep -q '^ovr-fboot.sh ' "$c" && echo 1 || echo 0)"
for h in "to-fastboot.sh:XRUN_TO_FASTBOOT" "fboot.sh:XRUN_FBOOT" "build-xnu-image.sh:XRUN_BUILD" "usb-port:XRUN_USB_PORT" "pf:XRUN_RUN_PREFLIGHT"; do
	nm=${h%%:*}; var=${h##*:}; dflt="default-$nm"; [ "$nm" = pf ] && dflt="pf-default"
	ok "$var: the override is called and the sibling default is not" "$(grep -q "^ovr-$nm " "$c" && ! grep -q "^$dflt " "$c" && echo 1 || echo 0)"
done
ok "XRUN_SAFETY: the override is called for pre, fastboot and monitor (once each), the sibling default tools/safety-check is not" "$([ "$(grep -c '^ovr-safety-check pre$' "$c")" = 1 ] && [ "$(grep -c '^ovr-safety-check fastboot$' "$c")" = 1 ] && [ "$(grep -c '^ovr-safety-check monitor$' "$c")" = 1 ] && ! grep -q '^default-safety-check ' "$c" && echo 1 || echo 0)"
ok "XRUN_SAFETY: the transcript order is pre, fastboot, monitor (pre before the lock and fboot, monitor right before fboot)" "$([ "$(grep -n '^ovr-safety-check pre$' "$c" | cut -d: -f1)" -lt "$(grep -n '^ovr-safety-check fastboot$' "$c" | cut -d: -f1)" ] && [ "$(grep -n '^ovr-safety-check fastboot$' "$c" | cut -d: -f1)" -lt "$(grep -n '^ovr-safety-check monitor$' "$c" | cut -d: -f1)" ] && [ "$(grep -n '^ovr-safety-check monitor$' "$c" | cut -d: -f1)" -lt "$(grep -n '^ovr-fboot.sh ' "$c" | cut -d: -f1)" ] && echo 1 || echo 0)"
ok "XRUN_RUN_PREFLIGHT: called for --intent-check and both --userdata-check" "$([ "$(grep -c '^ovr-pf --userdata-check' "$c")" = 2 ] && grep -q '^ovr-pf --intent-check' "$c" && echo 1 || echo 0)"
ok "XRUN_USB_PORT: called before the build and again after the lock (2 calls); XRUN_TO_FASTBOOT 1 call with 200" "$([ "$(grep -c '^ovr-usb-port ' "$c")" = 2 ] && [ "$(grep -c '^ovr-to-fastboot.sh 200$' "$c")" = 1 ] && echo 1 || echo 0)"
ok "XRUN_SYSFS_USB: the fake bus satisfied the 18d1 wait (to-fastboot was reached; real bus not needed)" "$(grep -q '^ovr-to-fastboot.sh ' "$c" && echo 1 || echo 0)"
for v in "XRUN_SYSFS_USB=$w/bus" "XRUN_TO_FASTBOOT=$ovr/to-fastboot.sh" "XRUN_FBOOT=$ovr/fboot.sh" "XRUN_BUILD=$ovr/build-xnu-image.sh" "XRUN_USB_PORT=$ovr/usb-port" "XRUN_RUN_PREFLIGHT=$ovr/pf" "PIXOS_SLEEP_SCALE=0" "XRUN_SAFETY=$w/safe-ovr"; do
	ok "WARN: SIM SEAM ACTIVE: ${v%%=*} printed by xrun" "$(grep -qxF "SIM SEAM ACTIVE: $v" "$w/out.hooks" && echo 1 || echo 0)"
done
ok "WARN: xrun_report.py/usb-port children are silent for what xrun already announced? (xrun_report prints its own XRUN_SYSFS_USB line)" "$([ "$(grep -cxF "SIM SEAM ACTIVE: XRUN_SYSFS_USB=$w/bus" "$w/out.hooks")" -ge 1 ] && echo 1 || echo 0)"
# F2: the run record names the active seams (the run dir of the hooks run is still there: the next xr call deletes it)
rd=$(ls -d "$w"/new/logs/runs/*-simseam 2>/dev/null | head -n 1)
seams7="XRUN_SYSFS_USB,XRUN_TO_FASTBOOT,XRUN_FBOOT,XRUN_BUILD,XRUN_USB_PORT,XRUN_RUN_PREFLIGHT,PIXOS_SLEEP_SCALE,XRUN_SAFETY"
ok "F2: run.json carries sim_seams=<every active seam>" "$(grep -q "\"sim_seams\": \"$seams7\"" "$rd/run.json" 2>/dev/null && echo 1 || echo 0)"
ok "F2: xrun.log carries the SIM SEAMS ACTIVE line" "$(grep -q "^SIM SEAMS ACTIVE: $seams7 (this is NOT a real-phone run)" "$rd/xrun.log" 2>/dev/null && echo 1 || echo 0)"
ok "F2: report.txt (run summary) carries the SIM SEAMS ACTIVE line" "$(grep -q "^SIM SEAMS ACTIVE: $seams7 (test overrides" "$rd/report.txt" 2>/dev/null && echo 1 || echo 0)"
ok "F2: report.json carries sim_seams" "$(grep -q "\"sim_seams\": \"$seams7\"" "$rd/report.json" 2>/dev/null && echo 1 || echo 0)"
ok "F1: the same hooks run under a \$TMPDIR lock was accepted (no REFUSED line)" "$(! grep -q 'SIM SEAM REFUSED' "$w/out.hooks" && echo 1 || echo 0)"
# F1: every command seam alone is REFUSED on a non-sandbox lock; nothing runs (no sibling called, no run dir, no lock)
fakelock="$HOME/pixelios-artifacts/phone.lock"; mkdir -p "$w/home"; ln -s "$fakelock" "$w/lnk"
ref() {   # TAG VAR VALUE LOCKDIR (extra row text)
	xr "$w/new" "$1" "$2=$3" PHONE_LOCK_DIR="$4" XRUN_SYSFS_USB="$w/bus" XRUN_USB_WAIT=1 PIXOS_SLEEP_SCALE=0 --
	ok "F1: $2 with PHONE_LOCK_DIR='${4/#$w/W}' is SIM SEAM REFUSED, exit 2, no sibling tool called, no run dir ($5)" "$([ "$(cat "$w/rc.$1")" = 2 ] && grep -q "^SIM SEAM REFUSED: $2 needs a sandbox" "$w/out.$1" && [ ! -s "$w/calls.$1" ] && [ ! -d "$w/new/logs/runs" ] && echo 1 || echo 0)"
}
for h in "to-fastboot.sh:XRUN_TO_FASTBOOT" "fboot.sh:XRUN_FBOOT" "build-xnu-image.sh:XRUN_BUILD" "usb-port:XRUN_USB_PORT" "pf:XRUN_RUN_PREFLIGHT"; do
	nm=${h%%:*}; var=${h##*:}
	ref "ref-$nm" "$var" "$ovr/$nm" "" "lock dir unset"
done
ref ref-safety XRUN_SAFETY "$w/safe-ovr" "" "lock dir unset: a fake safety-check would drop the H9 checks"
ref ref-safety-true XRUN_SAFETY /bin/true "" "/bin/true as safety-check: no pre line, no monitor"
ref ref-default XRUN_FBOOT "$ovr/fboot.sh" "$fakelock" "the real lock path"
ref ref-link XRUN_FBOOT "$ovr/fboot.sh" "$w/lnk" "a symlink under TMPDIR onto the real lock"
ref ref-outside XRUN_TO_FASTBOOT "$ovr/to-fastboot.sh" "/var/tmp/pixos-notmp-lock" "a lock dir outside TMPDIR"
ref ref-true XRUN_RUN_PREFLIGHT /bin/true "" "/bin/true as the preflight drops the run-plan check"
# the default-lock check on the python CLI itself, including a TMPDIR that contains the real lock (HOME under TMPDIR)
req() { env -u PHONE_LOCK_DIR "$@" "$pybin" -I "$tools/simseams.py" require XRUN_FBOOT XRUN_BUILD 2>&1; }
pybin=$(command -v python3)
o=$(req XRUN_FBOOT=x PHONE_LOCK_DIR="$w/home/pixelios-artifacts/phone.lock" HOME="$w/home" TMPDIR="$w/home"); rc=$?
ok "F1: simseams require refuses the default lock even when \$TMPDIR contains it (HOME under TMPDIR; rc=$rc)" "$([ $rc = 2 ] && grep -q 'SIM SEAM REFUSED: XRUN_FBOOT' <<<"$o" && echo 1 || echo 0)"
o=$(req XRUN_FBOOT=x PHONE_LOCK_DIR="$w/home/other/lock" HOME="$w/home" TMPDIR="$w/home"); rc=$?
ok "F1: simseams require accepts another lock dir under \$TMPDIR (rc=$rc)" "$([ $rc = 0 ] && [ -z "$o" ] && echo 1 || echo 0)"
o=$(req PHONE_LOCK_DIR="$HOME/pixelios-artifacts/phone.lock"); rc=$?
ok "F1: simseams require with no seam variable set is silent success even on the real lock (rc=$rc)" "$([ $rc = 0 ] && [ -z "$o" ] && echo 1 || echo 0)"
o=$(req XRUN_FBOOT=x XRUN_BUILD=y PHONE_LOCK_DIR=""); rc=$?
ok "F1: simseams require prints one REFUSED line per set seam (rc=$rc)" "$([ $rc = 2 ] && [ "$(grep -c '^SIM SEAM REFUSED' <<<"$o")" = 2 ] && echo 1 || echo 0)"
# a bad scale is refused before the lock and before any sibling (MA2): rc 2, nothing called, no run dir
xr "$w/new" badscale PIXOS_SLEEP_SCALE=abc --
ok "PIXOS_SLEEP_SCALE=abc: xrun exits 2 before the lock: SIM SEAM ERROR, no sibling tool called (not even phone-lock), no run dir" "$([ "$(cat "$w/rc.badscale")" = 2 ] && grep -q '^SIM SEAM ERROR: PIXOS_SLEEP_SCALE=abc' "$w/out.badscale" && [ ! -s "$w/calls.badscale" ] && [ ! -d "$w/new/logs/runs" ] && echo 1 || echo 0)"
xr "$w/new" badscale2 LC_ALL=en_US.utf8 PIXOS_SLEEP_SCALE=１ --
ok "PIXOS_SLEEP_SCALE=<fullwidth 1> in a UTF-8 locale: xrun exits 2, nothing called (shell and Python agree)" "$([ "$(cat "$w/rc.badscale2")" = 2 ] && grep -q '^SIM SEAM ERROR' "$w/out.badscale2" && [ ! -s "$w/calls.badscale2" ] && echo 1 || echo 0)"
# the post-run recovery site reads the same bus variable as the wait (MB): the glob must be spelled out 5 times (2 waits + 2 EARLY recovery checks + 1 SAFETY park check)
ok "xrun: \"\$usbsys\"/*/idVendor is spelled 5 times (usb wait x2, EARLY recovery x2, SAFETY park x1): no site reads another bus path" "$([ "$(grep -o '"\$usbsys"/\*/idVendor' "$tools/xrun" | wc -l)" = 5 ] && [ "$(grep -v '^#' "$tools/xrun" | grep -c 'idVendor')" = "$(grep -v '^#' "$tools/xrun" | grep 'idVendor' | grep -c usbsys)" ] && echo 1 || echo 0)"
# an empty fake bus: the wait ends in the 18d1 refusal and nothing reaches the phone steps
xr "$w/new" nobus XRUN_SYSFS_USB="$w/bus/empty" XRUN_USB_WAIT=1 XRUN_TO_FASTBOOT="$ovr/to-fastboot.sh" XRUN_FBOOT="$ovr/fboot.sh" XRUN_BUILD="$ovr/build-xnu-image.sh" XRUN_USB_PORT="$ovr/usb-port" XRUN_RUN_PREFLIGHT="$ovr/pf" PIXOS_SLEEP_SCALE=0 --
ok "XRUN_SYSFS_USB empty bus: refused with 'no Google USB device (18d1)', to-fastboot/fboot never called" "$(grep -q 'no Google USB device (18d1) appeared within 1 s' "$w/out.nobus" && ! grep -q '^ovr-to-fastboot\|^ovr-fboot' "$w/calls.nobus" && echo 1 || echo 0)"
# --print-bootargs goes through XRUN_BUILD
xr "$w/new" pba XRUN_BUILD="$ovr/build-xnu-image.sh" -- --print-bootargs
ok "XRUN_BUILD also serves --print-bootargs" "$(grep -q '^ovr-build-xnu-image.sh ' "$w/calls.pba" && grep -q 'boot-args: ovr\|boot-args: fake-only' "$w/out.pba" && echo 1 || echo 0)"

# ---------- scale applied to the xrun run: scale 0 sleeps nothing (default: see the unchanged-default rows below)
ok "e2e: PIXOS_SLEEP_SCALE=0 run executed no sleep at all, except the 0.25 s bounded poll of safety_end (recording sleep on PATH saw $(grep -c . "$w/sleeps.hooks") calls)" "$([ -f "$w/sleeps.hooks" ] && ! grep -qvx 0.25 "$w/sleeps.hooks" && echo 1 || echo 0)"

# ---------- unset default path unchanged: pre-seam xrun (git $BASE) vs this xrun, same fakes, same fake bus mounted over /sys/bus/usb/devices
if ! git -C "$base_repo" cat-file -e "$BASE:tools/xrun" 2>/dev/null; then
	skip "default path unchanged vs $BASE: no such git object here (shallow copy?) -- NOT proven in this run"
elif ! unshare -r -m true 2>/dev/null; then
	skip "default path unchanged vs $BASE: unshare -r -m not permitted here -- NOT proven in this run"
else
	git -C "$base_repo" show "$BASE:tools/xrun" >"$w/xrun.base"
	mkck "$w/base" "$w/xrun.base"
	cat >"$w/inns.sh" <<'EOF'
#!/bin/bash
# runs inside `unshare -r -m`: the fake bus replaces the real /sys/bus/usb/devices so the unset default path sees an 18d1 device
mount --bind "$1" /sys/bus/usb/devices || exit 99
shift
exec "$@"
EOF
	for variant in base new; do
		: >"$w/calls.$variant"; : >"$w/sleeps.$variant"; rm -rf "$w/$variant/logs/runs"
		( cd "$w/$variant" && env -u XRUN_SYSFS_USB -u XRUN_TO_FASTBOOT -u XRUN_FBOOT -u XRUN_BUILD -u XRUN_USB_PORT -u XRUN_RUN_PREFLIGHT -u XRUN_SAFETY -u PIXOS_SLEEP_SCALE \
			PATH="$w/fakebin:$PATH" SLEEPLOG="$w/sleeps.$variant" CALLS="$w/calls.$variant" XRUN_PHONE="$w/$variant/tools/phone" XRUN_NO_RUNPLAN="sim seam host test, not a run" ARTIFACTS="$w/art" \
			unshare -r -m bash "$w/inns.sh" "$w/bus" timeout 120 ./tools/xrun --kernel "$w/$variant/kernel" --adt "$w/$variant/adt" --no-camera --no-usbnet --duration 1 --restart 1 --label simseam ) >"$w/out.$variant" 2>&1
		echo $? >"$w/rc.$variant"
		sed -E 's/[0-9]{8}T[0-9]{6}Z/TS/g; s#'"$w/$variant"'#CK#g' "$w/calls.$variant" >"$w/norm.$variant"
	done
	ok "default: the pre-seam xrun ($BASE) reached fboot on the fake bus (control for the next rows; $(grep -c . "$w/norm.base") calls)" "$(grep -q '^default-fboot.sh ' "$w/norm.base" && echo 1 || echo 0)"
	intended <"$w/norm.new" >"$w/norm.new.i"
	ok "default: command transcript (every sibling call + argv) identical to the pre-seam xrun (minus the listed intended deltas)" "$(diff -q "$w/norm.base" "$w/norm.new.i" >/dev/null && [ -s "$w/norm.new" ] && echo 1 || echo 0)"
	ok "default: exit code identical (base $(cat "$w/rc.base"), new $(cat "$w/rc.new"))" "$([ "$(cat "$w/rc.base")" = "$(cat "$w/rc.new")" ] && echo 1 || echo 0)"
	rd=$(ls -d "$w"/new/logs/runs/*-simseam 2>/dev/null | head -n 1)
	ok "default: no sim_seams in the run record, no SIM SEAMS line in xrun.log or the report when nothing is set (run dir found: ${rd:+yes})" "$([ -n "$rd" ] && ! grep -q 'sim_seams' "$rd/run.json" && ! grep -qs 'SIM SEAMS' "$rd/xrun.log" "$rd/report.txt" && echo 1 || echo 0)"
	ok "default: no SIM SEAM line in the output when nothing is set" "$(! grep -q 'SIM SEAM' "$w/out.new" && echo 1 || echo 0)"
	intended_sleeps <"$w/sleeps.new" >"$w/sleeps.new.i"
	ok "default: the unset run asks for the same real sleeps as the pre-seam run, unscaled (recording sleep: base '$(tr "\n" , <"$w/sleeps.base")' new '$(tr "\n" , <"$w/sleeps.new")', minus the safety sleeps 0.5/0.25; incl. the 3 s post-wait)" "$(diff -q "$w/sleeps.base" "$w/sleeps.new.i" >/dev/null && grep -qx 3 "$w/sleeps.new" && grep -qx 0.5 "$w/sleeps.new" && echo 1 || echo 0)"
fi
# --print-bootargs needs neither bus nor namespace: the pre-seam xrun and this one print the same and call the same build
if git -C "$base_repo" cat-file -e "$BASE:tools/xrun" 2>/dev/null; then
	git -C "$base_repo" show "$BASE:tools/xrun" >"$w/xrun.base"
	[ -d "$w/base" ] || mkck "$w/base" "$w/xrun.base"
	cp "$w/xrun.base" "$w/base/tools/xrun"
	xr "$w/base" pba0 -- --print-bootargs --cpus 2; xr "$w/new" pba1 -- --print-bootargs --cpus 2
	ok "default --print-bootargs: output and build-xnu-image.sh argv identical to the pre-seam xrun (minus the listed intended deltas)" "$(diff -q <(sed "s#$w/base#CK#g" "$w/out.pba0") <(sed "s#$w/new#CK#g" "$w/out.pba1" | intended) >/dev/null && diff -q "$w/norm.pba0" <(intended <"$w/norm.pba1") >/dev/null && [ -s "$w/norm.pba1" ] && echo 1 || echo 0)"
else skip "default --print-bootargs vs $BASE: no such git object here -- NOT proven in this run"; fi

echo "RESULT: $([ "$bad" = 0 ] && echo PASS || echo FAIL) rows=$n skipped=$skipped failed=$bad"
[ "$bad" = 0 ]
