"""resident_phone_io: the run-dir and USB-log readers of RealPhone (tools/resident_session_lib.py), split out for the 800-line limit.
host-tested through tools/test_resident.py's fake phone only (these read xrun's real run dir: run.json, usbnet.log, report.txt)."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def p(*parts):
    return os.path.join(os.environ.get("RESIDENT_DIR") or os.path.expanduser("~/pixelios-artifacts/resident"), *parts)


def jload(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):   # lint-silent: expected run.json not written yet / mid-write: the default, asked again
        return default


class PhoneLogIO:
    def run_dir(self, h):
        if not h["run_dir"]:
            d = os.path.join(ROOT, "logs", "runs")
            hits = sorted(n for n in (os.listdir(d) if os.path.isdir(d) else []) if n.endswith("-" + h["label"]))
            if hits:
                h["run_dir"] = os.path.join(d, hits[-1])
        return h["run_dir"]

    def boot_started(self, h):
        """Epoch when fastboot booted the image (xrun's run.json t_boot), None before."""
        d = self.run_dir(h)
        j = jload(os.path.join(d, "run.json"), {}) if d else {}
        try:
            return float(j["t_boot"]) if j.get("t_boot") else None
        except (TypeError, ValueError):   # lint-silent: expected a run.json written mid-way: not booted yet, asked again
            return None

    def log_exists(self, h):
        d = self.run_dir(h)
        return bool(d) and os.path.exists(os.path.join(d, "usbnet.log"))

    def read_new(self, h):
        d = self.run_dir(h)
        f = os.path.join(d, "usbnet.log") if d else None
        if not f or not os.path.exists(f):
            return ""
        with open(f, "rb") as fh:
            fh.seek(h["pos"])
            data = fh.read()
        cut = data.rfind(b"\n") + 1
        h["pos"] += cut
        return data[:cut].decode(errors="replace")

    def crash_text(self, h):
        d = self.run_dir(h)
        for n in ("report.txt",):
            f = os.path.join(d, n) if d else None
            if f and os.path.exists(f):
                with open(f, errors="replace") as fh:
                    t = fh.read()
                return t[:1500]
        return "(no report.txt: xrun.out %s)" % p("boots", h["label"], "xrun.out")

    def safety_fail(self, h):
        """(item, line) of a SAFETY FAIL in this boot's xrun output (RULES H9), None when none."""
        import resident_thermal as rt
        return rt.safety_fail_in(p("boots", h["label"], "xrun.out"))
