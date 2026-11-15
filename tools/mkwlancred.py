#!/usr/bin/env python3
"""mkwlancred: build the RAM-only WLANCRED credential blob from the kernel .env (SPEC-WIFI.md TRANSPORT block s.3, T-2952); called by tools/xrun for --intent wlan with sweetwlan >= 24, read by tools/mkpayload.py

  tools/mkwlancred.py [--env FILE] [--net 1|2]
  --net 2 (or env PIXOS_WIFI_NET=2): the SECOND network, keys PIXOS_WIFI2_SSID/_PSK/_SECURITY (the user's Pixel hotspot, 2026-11-10:
          easy to switch off for the W-D6 AP-loss test); PIXOS_WIFI2_IP/_GW are optional: absent = 0.0.0.0 in the blob = "DHCP, no
          static address" (printed `wlancred-mode: dhcp`; the kernel must accept a zero ip/gw for that, see W-D6-rejoin-dhcp research)
  tools/mkwlancred.py --self-test    format round trip on a made-up env (also run by tools/test_mkwlancred.py)

Reads PIXOS_WIFI_SSID, PIXOS_WIFI_PSK, PIXOS_WIFI_SECURITY (must read wpa2-psk), PIXOS_WIFI_IP, PIXOS_WIFI_GW from the kernel .env
(PIXOS_ENV_FILE / tools/envfile.py order; --env overrides; template: env.example at the repo root), writes the 128-byte blob to a 0600 file in a fresh 0700 directory under
$XDG_RUNTIME_DIR (tmpfs: no fallback, never the run dir, never shim/out) and prints
  wlancred: <path>
  RESULT: PASS mkwlancred bytes=128 keys=<names>
Only key NAMES, the path and the byte count reach stdout/stderr. A missing/invalid key is `RESULT: ERROR <why naming the KEY>`, exit 2:
never a default and never a value. The caller removes the directory (tools/xrun EXIT trap).

Blob layout (little endian, 128 bytes; `parse` below is the host reader, the shim reader follows this table):
  0x00 char   magic[8] = "WLANCRB1"   (the trailing 1 is the format version)
  0x08 u32    security = 0            (0 = wpa2-psk; any other value is refused)
  0x0c u32    ssid_len  1..32
  0x10 u32    psk_len   8..63
  0x14 u8     ssid[32]                (zero padded)
  0x34 u8     psk[64]                 (zero padded)
  0x74 u8     ip[4]                   (static address of the ping)
  0x78 u8     gw[4]                   (its gateway)
  0x7c u32    crc32 (zlib) over bytes 0x00..0x7b
SPEC-WIFI.md s.3 item 1 also lists a separate `u32 version`, which sums to 132 bytes; the fixed size of 128 is the stated invariant, so
the version is the last magic character.
Last line: RESULT: PASS|FAIL|ERROR mkwlancred ..."""
import argparse, ipaddress, os, struct, sys, tempfile, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import envfile  # noqa: E402
import xrun_secret_scan  # noqa: E402  (parse_env: the SAME value parsing as the scanner, so what is embedded is what the scan searches for)

MAGIC = b"WLANCRB1"
SIZE = 128
SSID_KEY, PSK_KEY, SEC_KEY, IP_KEY, GW_KEY = ("PIXOS_WIFI_SSID", "PIXOS_WIFI_PSK", "PIXOS_WIFI_SECURITY", "PIXOS_WIFI_IP", "PIXOS_WIFI_GW")
KEYS = (SSID_KEY, PSK_KEY, SEC_KEY, IP_KEY, GW_KEY)
NET2 = {k: k.replace("PIXOS_WIFI_", "PIXOS_WIFI2_") for k in KEYS}   # --net 2: the same rules on the PIXOS_WIFI2_* keys


def select_net(env, net):
    """-> (env view with the PIXOS_WIFI_* names, dhcp flag). net 2 maps PIXOS_WIFI2_*; a missing WIFI2 ip/gw = zero (DHCP)."""
    if net == 1:
        return env, False
    view = {k: env.get(NET2[k]) for k in KEYS}
    dhcp = not view.get(IP_KEY) and not view.get(GW_KEY)
    if dhcp:
        view[IP_KEY] = view[GW_KEY] = "0.0.0.0"
    elif not view.get(IP_KEY) or not view.get(GW_KEY):
        raise CredError("set both %s and %s, or neither (DHCP)" % (NET2[IP_KEY], NET2[GW_KEY]))
    return view, dhcp
SECURITY_WPA2_PSK = "wpa2-psk"
DIR_PREFIX = "wlancred-"
FMT = "<8s3I32s64s4s4sI"


class CredError(Exception):
    """Message names a KEY and a rule, never a value."""


def _need(env, key):
    v = env.get(key)
    if v is None or v == "":
        raise CredError("missing or empty key %s in the kernel .env" % key)
    return v


def _quad(env, key, allow_zero=False):
    v = _need(env, key)
    try:
        a = ipaddress.IPv4Address(v)
        if int(a) == 0 and not allow_zero:
            raise CredError("key %s is 0.0.0.0 (only the second network may use DHCP)" % key)
        return a.packed
    except ValueError:
        raise CredError("key %s is not a dotted quad" % key) from None


def build_blob(env, dhcp=False):
    """-> 128 bytes. Raises CredError (no value in the text). dhcp: ip/gw are zero on purpose (the second network)."""
    ssid, psk = _need(env, SSID_KEY), _need(env, PSK_KEY)
    if _need(env, SEC_KEY).lower() != SECURITY_WPA2_PSK:
        raise CredError("key %s must read %s (the only security the shim implements)" % (SEC_KEY, SECURITY_WPA2_PSK))
    sb, pb = ssid.encode("utf-8"), psk.encode("utf-8")
    if not 1 <= len(sb) <= 32:
        raise CredError("key %s must be 1..32 bytes" % SSID_KEY)
    if not 8 <= len(pb) <= 63 or not all(32 <= c <= 126 for c in pb):
        raise CredError("key %s must be 8..63 printable ASCII characters (a 64-digit hex PMK is not supported)" % PSK_KEY)
    ip, gw = _quad(env, IP_KEY, dhcp), _quad(env, GW_KEY, dhcp)
    body = struct.pack(FMT[:-1], MAGIC, 0, len(sb), len(pb), sb, pb, ip, gw)
    blob = body + struct.pack("<I", zlib.crc32(body) & 0xffffffff)
    assert len(blob) == SIZE
    return blob


def parse(blob):
    """-> dict(ssid bytes, psk bytes, ip str, gw str). Raises ValueError (no value in the text): the host twin of the shim's validation
    (size, magic, crc, security, lengths, zero padding)."""
    if len(blob) != SIZE:
        raise ValueError("wlancred blob is %d bytes, need %d" % (len(blob), SIZE))
    magic, sec, sl, pl, ssid, psk, ip, gw, crc = struct.unpack(FMT, blob)
    if magic != MAGIC:
        raise ValueError("wlancred blob has the wrong magic")
    if zlib.crc32(blob[:SIZE - 4]) & 0xffffffff != crc:
        raise ValueError("wlancred blob crc mismatch")
    if sec != 0:
        raise ValueError("wlancred blob security is not wpa2-psk")
    if not 1 <= sl <= 32 or not 8 <= pl <= 63:
        raise ValueError("wlancred blob lengths out of range")
    if any(ssid[sl:]) or any(psk[pl:]):
        raise ValueError("wlancred blob padding is not zero")
    return {"ssid": ssid[:sl], "psk": psk[:pl], "ip": str(ipaddress.IPv4Address(ip)), "gw": str(ipaddress.IPv4Address(gw))}


def write_blob(blob, base):
    """Fresh 0700 dir under BASE, 0600 file in it -> file path."""
    d = tempfile.mkdtemp(prefix=DIR_PREFIX, dir=base)     # mkdtemp creates mode 0700
    p = os.path.join(d, "blob")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)
    return p


def self_test():
    env = {SSID_KEY: "selftest-net", PSK_KEY: "selftest-passphrase", SEC_KEY: SECURITY_WPA2_PSK, IP_KEY: "192.0.2.10", GW_KEY: "192.0.2.1"}
    ok = True
    blob = build_blob(env)
    ok &= len(blob) == SIZE and parse(blob) == {"ssid": b"selftest-net", "psk": b"selftest-passphrase", "ip": "192.0.2.10", "gw": "192.0.2.1"}
    bad = bytearray(blob); bad[40] ^= 1
    try:
        parse(bytes(bad)); ok = False       # a flipped bit must fail the crc
    except ValueError:  # lint-silent: expected a flipped bit must fail the crc, that is the check
        pass
    try:
        build_blob({k: v for k, v in env.items() if k != IP_KEY}); ok = False
    except CredError as e:
        ok &= IP_KEY in str(e)
    e2 = {"PIXOS_WIFI2_SSID": "hotspot-net", "PIXOS_WIFI2_PSK": "hotspot-passphrase", "PIXOS_WIFI2_SECURITY": SECURITY_WPA2_PSK}
    v2, dh = select_net(e2, 2)
    ok &= dh and parse(build_blob(v2, dh)) == {"ssid": b"hotspot-net", "psk": b"hotspot-passphrase", "ip": "0.0.0.0", "gw": "0.0.0.0"}
    try:
        build_blob(dict(env, **{IP_KEY: "0.0.0.0"})); ok = False   # network 1 never takes a zero address
    except CredError as e:
        ok &= IP_KEY in str(e)
    print("RESULT: %s mkwlancred self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    ap = argparse.ArgumentParser(prog="mkwlancred", description=__doc__.split("\n")[0])
    ap.add_argument("--env", help="env file (default: PIXOS_ENV_FILE, else the kernel clone .env via tools/envfile.py)")
    ap.add_argument("--net", type=int, choices=(1, 2), default=int(os.environ.get("PIXOS_WIFI_NET") or 1),
                    help="1 = PIXOS_WIFI_* (default), 2 = PIXOS_WIFI2_* (ip/gw optional = DHCP); env PIXOS_WIFI_NET")
    a = ap.parse_args(argv)     # exit 2 on a usage error
    try:
        base = os.environ.get("XDG_RUNTIME_DIR")
        if not base or not os.path.isdir(base):
            raise CredError("XDG_RUNTIME_DIR is unset or not a directory: the blob must live on tmpfs, there is no fallback")
        path = a.env or envfile.find()
        if not path or not os.path.isfile(path):
            raise CredError("no kernel .env found (PIXOS_ENV_FILE / tools/envfile.py order)")
        try:
            env = xrun_secret_scan.parse_env(path)
        except xrun_secret_scan.ScanError as e:
            raise CredError(str(e)) from None
        view, dhcp = select_net(env, a.net)
        out = write_blob(build_blob(view, dhcp), base)
    except CredError as e:
        print("RESULT: ERROR mkwlancred %s" % e)
        return 2
    except OSError as e:        # the text may carry a path, never a value: class name only
        print("RESULT: ERROR mkwlancred cannot write the blob (%s)" % type(e).__name__)
        return 2
    print("wlancred: %s" % out)
    if dhcp:
        print("wlancred-mode: dhcp (no static ip/gw for network 2)")   # NOT "wlancred: ": tools/xrun takes every such line as the blob path
    print("RESULT: PASS mkwlancred bytes=%d net=%d keys=%s" % (SIZE, a.net, ",".join(KEYS if a.net == 1 else (NET2[k] for k in KEYS))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
