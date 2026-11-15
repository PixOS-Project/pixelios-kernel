#!/usr/bin/env python3
"""Patch 0365 (lowclk, boot-arg sweetsmbind: bind sm_thread to the SPI cpu / raise its priority; log wake_same/wake_other): host test. Compiles the pure block between
SMBIND-PURE-BEGIN / SMBIND-PURE-END out of xnu/patches/0365-sweet-mali-smbind.patch (mask decoding, priority choice, bind cpu choice, wake accounting), then checks every hook
by text (off = byte-identical path, bind before the IRQ setup with GAPs, priority at thread creation, ISR cpu before the stamp, wake accounting after the consume, own log row,
no register access, no new thread API beyond the existing ones) with a mutant per check. `RESULT: PASS 0365-smbind cases=N mutants=M`. Not a hardware or kernel-build test."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0365-sweet-mali-smbind.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
%s
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
	/* 0 = off: no bind, no priority change, nothing unknown */
	CHECK(smb_known(0) == 0 && smb_unknown(0) == 0 && smb_priority(0) == 0 && smb_bind_cpu(0, 0) == -1);
	/* bit 0 only: bind to the boot cpu given (not a hardcoded 0), default priority */
	CHECK(smb_known(1) == 1 && smb_unknown(1) == 0 && smb_priority(1) == 0 && smb_bind_cpu(1, 0) == 0 && smb_bind_cpu(1, 3) == 3);
	/* bit 1 only: priority 93, no bind */
	CHECK(smb_known(2) == 2 && smb_unknown(2) == 0 && smb_priority(2) == 93 && smb_bind_cpu(2, 0) == -1);
	/* both */
	CHECK(smb_known(3) == 3 && smb_priority(3) == 93 && smb_bind_cpu(3, 0) == 0);
	/* unknown bits are reported and cut, known bits survive */
	CHECK(smb_unknown(4) == 4 && smb_known(4) == 0 && smb_priority(4) == 0 && smb_bind_cpu(4, 0) == -1);
	CHECK(smb_unknown(0x13) == 0x10 && smb_known(0x13) == 3 && smb_unknown(0xffffffffu) == 0xfffffffcu);
	/* the raised priority is inside the kernel band 93..95 and above BASEPRI_PREEMPT 92 / BASEPRI_KERNEL 81, the default is 81 */
	CHECK(SMB_PRI_RAISED >= 93 && SMB_PRI_RAISED <= 95 && SMB_PRI_DEFAULT == 81);
	/* wake accounting */
	struct smb_wake w;
	memset(&w, 0, sizeof w);
	smb_wake_account(&w, 0, 0);
	smb_wake_account(&w, 0, 0);
	smb_wake_account(&w, 0, 2);
	smb_wake_account(&w, 4, 0);
	smb_wake_account(&w, 1, 1);
	CHECK(w.same == 3 && w.other == 2);
	puts("ok");
	return 0;
}
'''


def body(text):
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("--- a/"):
            on = True
        if on and l[:1] in ("+", " ") and not l.startswith("+++"):
            out.append(l[1:])
    return "\n".join(out)


def extract(src):
    m = re.search(r"/\* SMBIND-PURE-BEGIN[^\n]*\*/\n(.*?)/\* SMBIND-PURE-END \*/", src, re.S)
    if not m:
        raise SystemExit("RESULT: FAIL 0365-smbind: pure block markers not found in the patch")
    return m.group(1)


def run(func):
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "t.c")
        exe = os.path.join(d, "t")
        with open(c, "w") as f:
            f.write(HARNESS % func)
        r = subprocess.run(["cc", "-Wall", "-Werror", "-Wno-unused-function", "-o", exe, c], capture_output=True, text=True)
        if r.returncode:
            return 2, r.stderr
        r = subprocess.run([exe], capture_output=True, text=True)
        return r.returncode, r.stderr + r.stdout


TEXT = [
    ("parsed before the thread is created", r"PE_parse_boot_argn\(\"sweetsmbind\", &smv, sizeof\(smv\)\) && smv != 0\) \{.*?kernel_thread_start\(sm_thread, NULL, &sm\.thread\)"),
    ("unknown bits GAP", r"if \(smb_unknown\(smv\)\) \{\s*printf\(\"SWEET MALI GAP: sweetsmbind=%u has unknown bits %#x, ignored"),
    ("mask cut to the known bits", r"smb_mask = smb_known\(smv\);"),
    ("priority line old and new", r"printf\(\"SWEET MALI: sm_thread priority %d -> %d \(sweetsmbind=%u;"),
    ("priority only through the creation call", r"smb_priority\(smb_mask\) != 0 \? kernel_thread_start_priority\(sm_thread, NULL, smb_priority\(smb_mask\), &sm\.thread\) : kernel_thread_start\(sm_thread, NULL, &sm\.thread\)\) != KERN_SUCCESS"),
    ("bind uses the boot cpu, not a literal", r"smb_bind_cpu\(smb_mask, master_cpu\) >= 0"),
    ("bind before the IRQ setup", r"thread_bind\(bpr\);\s*thread_block\(THREAD_CONTINUE_NULL\);.*?sm_irq_setup\(\);"),
    ("bind result is checked with cpu_number", r"if \(cpu_number\(\) != bc\) \{\s*printf\(\"SWEET MALI GAP: sweetsmbind=%u: bind to cpu %d failed"),
    ("no processor is a GAP", r"bpr == PROCESSOR_NULL\) \{\s*printf\(\"SWEET MALI GAP: sweetsmbind=%u: cpu %d has no processor"),
    ("bound line", r"printf\(\"SWEET MALI: sm_thread bound to cpu %d \(sweetsmbind=%u\)\\n\", bc, smb_mask\);"),
    ("ISR records its cpu before the stamp", r"os_atomic_store\(&smb_isr_cpu, \(uint32_t\)cpu_number\(\), relaxed\);\s*os_atomic_thread_fence\(release\);\s*\}\s*\(void\)os_atomic_cmpxchg\(&cl_isr_t0, 0, t_in, relaxed\);"),
    ("wake counted after the consume, with an acquire fence", r"os_atomic_xchg\(&cl_isr_t0, 0, relaxed\) : 0;[^\n]*\n\s*if \(t_isr != 0\) \{[^\n]*\n\s*os_atomic_thread_fence\(acquire\);\s*smb_wake_account\(&smb_w, \(int\)os_atomic_load\(&smb_isr_cpu, relaxed\), cpu_number\(\)\);"),
    ("tick after the complat tick", r"cl_tick\(\);[^\n]*\n\s*smb_tick\(\);"),
    ("tick needs sweetcomplat and prints once per second", r"smb_tick\(void\)\s*\{\s*if \(!cl_on\) \{\s*return;\s*\}.*?if \(now - smb_last_us < 1000000\) \{\s*return;"),
    ("tick reads and resets under the lock", r"lck_mtx_lock\(&sm_lock\);\s*struct smb_wake w = smb_w;\s*smb_w\.same = smb_w\.other = 0;\s*lck_mtx_unlock\(&sm_lock\);"),
    ("wake row", r"SWEET PERF: complat_wake wake_same %llu wake_other %llu isr_cpu %u bound_cpu %d sweetsmbind %u\\n"),
]
NEG = [
    ("no register access in the added lines", r"(?m)^\+[^\n]*\b(wr|rr)\("),
    ("no GIC / IROUTER write", r"(?m)^\+\s*[^\s/*][^\n]*(IROUTER|gicd_|GICD)[^\n]*="),
    ("complat row of 0364 untouched", r"(?m)^[-+][^\n]*SWEET PERF: complat n "),
    ("no hardcoded bind to cpu 0", r"thread_bind\(cpu_to_processor\(0\)\)"),
    ("priority above the kernel band", r"#define SMB_PRI_RAISED (9[6-9]|1\d\d)"),
]


def main():
    text = open(PATCH).read()
    src = body(text)
    func = extract(src)
    rc, out = run(func)
    if rc:
        print(out)
        print("RESULT: FAIL 0365-smbind: harness")
        return 1
    cases = 1
    added = "\n".join(l for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    for name, rx in TEXT:
        if not re.search(rx, src, re.S):
            print("RESULT: FAIL 0365-smbind: patch text check failed: %s" % name)
            return 1
        cases += 1
    for name, rx in NEG:
        if re.search(rx, added if name != "complat row of 0364 untouched" else text):
            print("RESULT: FAIL 0365-smbind: forbidden text: %s" % name)
            return 1
        cases += 1
    mutants = [
        ("off still binds", func.replace("return (v & SMB_BIND) ? boot_cpu : -1;", "return boot_cpu;")),
        ("bind hardcoded 0", func.replace("return (v & SMB_BIND) ? boot_cpu : -1;", "return (v & SMB_BIND) ? 0 : -1;")),
        ("bit 1 binds", func.replace("return (v & SMB_BIND) ? boot_cpu : -1;", "return (v & 3u) ? boot_cpu : -1;")),
        ("priority always raised", func.replace("return (v & SMB_PRIO) ? SMB_PRI_RAISED : 0;", "return SMB_PRI_RAISED;")),
        ("priority on bit 0", func.replace("return (v & SMB_PRIO) ? SMB_PRI_RAISED : 0;", "return (v & 1u) ? SMB_PRI_RAISED : 0;")),
        ("priority 92", func.replace("#define SMB_PRI_RAISED 93", "#define SMB_PRI_RAISED 92")),
        ("priority 96 (realtime)", func.replace("#define SMB_PRI_RAISED 93", "#define SMB_PRI_RAISED 96")),
        ("default priority wrong", func.replace("#define SMB_PRI_DEFAULT 81", "#define SMB_PRI_DEFAULT 80")),
        ("unknown bits not reported", func.replace("return v & ~(SMB_BIND | SMB_PRIO);", "return 0;")),
        ("unknown bits kept", func.replace("return v & (SMB_BIND | SMB_PRIO);", "return v;")),
        ("same and other swapped", func.replace("if (isr_cpu == thread_cpu) {\n\t\tw->same++;", "if (isr_cpu != thread_cpu) {\n\t\tw->same++;")),
        ("same not counted", func.replace("w->same++;", "")),
        ("other not counted", func.replace("w->other++;", "")),
        ("bit values changed", func.replace("#define SMB_PRIO 2u", "#define SMB_PRIO 4u")),
    ]
    dead = 0
    for name, m in mutants:
        if m == func:
            print("RESULT: FAIL 0365-smbind: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m)
        if rc == 0:
            print("RESULT: FAIL 0365-smbind: mutant %s survived" % name)
            return 1
        dead += 1
    for name, rx in TEXT:
        m = re.sub(rx, "", src, count=1, flags=re.S)
        if m == src or re.search(rx, m, re.S):
            print("RESULT: FAIL 0365-smbind: text mutant %s did not remove the hook" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0365-smbind cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
