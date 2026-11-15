#!/usr/bin/env python3
"""cam_bracket: alternate the ESP32 camera's manual exposure during a run, so the saved frames hold both a dark-page and a white-page
readable exposure (user goal 2026-11-09 item 7; obs 2026-11-08/09: at aec_value 400 white Settings pages saturate, 140-220 reads them,
dark pages need ~400; wlan-wui-c, bt-mgr2, wlan-perf-d2).

  cam_bracket.py --until-file F --log TSV [--values 180,400] [--period 8]
      reads /status, then sets aec_value to each value in turn every PERIOD s (GET /control?var=aec_value&val=V&key=PASSWORD, the camera's
      own API), writes `<epoch>\t<aec_value>` to TSV at every switch, and puts the value it found at start back when F appears, on
      SIGTERM/SIGINT, or on any error. Needs manual exposure (aec=0): with aec=1 it changes nothing and says so (CAM BRACKET GAP).
  Credentials: CAM_HOST / CAM_PASSWORD from the gitignored .env (never printed). Stdlib only.
Exit 0 after the restore; 2 when the camera cannot be read at start (the run goes on without bracketing; xrun prints the GAP line)."""
import argparse, json, os, signal, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def env():
    out = {}
    for p in (os.path.join(os.path.dirname(HERE), ".env"),):
        try:
            for l in open(p):
                if "=" in l and not l.lstrip().startswith("#"):
                    k, v = l.strip().split("=", 1)
                    out[k] = v.strip().strip("\"'")
        except FileNotFoundError:   # lint-silent: expected a worktree without .env: the caller prints CAM BRACKET GAP from the exit code
            pass
    out.update({k: v for k, v in os.environ.items() if k.startswith("CAM_")})
    return out


class Cam:
    def __init__(self, host, key, timeout=4):
        self.base, self.key, self.timeout = "http://%s" % host, key, timeout

    def status(self):
        return json.load(urllib.request.urlopen(self.base + "/status", timeout=self.timeout))

    def set(self, var, val):
        q = urllib.parse.urlencode({"var": var, "val": str(val), "key": self.key})
        urllib.request.urlopen(self.base + "/control?" + q, timeout=self.timeout).read()


def run(a, cam, sleep=time.sleep, now=time.time):
    try:
        st = cam.status()
    except (OSError, ValueError) as e:
        print("CAM BRACKET GAP: camera status not readable (%s): no bracketing this run" % type(e).__name__)
        return 2
    if st.get("aec") not in (0, "0"):
        print("CAM BRACKET GAP: the camera is on auto exposure (aec=%s): aec_value would do nothing, no bracketing" % st.get("aec"))
        return 2
    start = st.get("aec_value")
    vals = [int(v) for v in a.values.split(",")]
    stop = {"now": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.__setitem__("now", True))
    print("CAM BRACKET: aec_value %s at start; alternating %s every %g s; restored at the end" % (start, ",".join(map(str, vals)), a.period))
    i, rc = 0, 0
    try:
        with open(a.log, "a") as log:
            while not stop["now"] and not os.path.exists(a.until_file):
                v = vals[i % len(vals)]
                cam.set("aec_value", v)
                log.write("%.3f\t%d\n" % (now(), v))
                log.flush()
                i += 1
                t_end = now() + a.period
                while now() < t_end and not stop["now"] and not os.path.exists(a.until_file):
                    sleep(0.5)
    except (OSError, ValueError) as e:
        print("CAM BRACKET GAP: a switch failed after %d switches (%s); restoring" % (i, type(e).__name__))
        rc = 1
    finally:
        try:
            if start is not None:
                cam.set("aec_value", start)
            print("CAM BRACKET: %d switches, aec_value restored to %s" % (i, start))
        except (OSError, ValueError) as e:
            print("CAM BRACKET GAP: restore to %s FAILED (%s): set it by hand (/control?var=aec_value)" % (start, type(e).__name__))
            rc = 1
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--until-file", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--values", default="180,400")
    ap.add_argument("--period", type=float, default=8.0)
    a = ap.parse_args()
    e = env()
    if not e.get("CAM_HOST"):
        print("CAM BRACKET GAP: CAM_HOST not set (.env): no bracketing")
        return 2
    return run(a, Cam(e["CAM_HOST"], e.get("CAM_PASSWORD", "")))


if __name__ == "__main__":
    sys.exit(main())
