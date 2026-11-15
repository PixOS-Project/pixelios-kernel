#!/usr/bin/env python3
"""Regenerate soc/google/gs101/shim/ufsphy_tables.h from soc/google/gs101/docs/07-gs101-ufs-bringup.md (Appendix A).

--check: build the text in memory, compare with the committed header, print RESULT: PASS/FAIL (exit 0/1; 2 = usage/error).
Called by tools/validate-repo.py (gate step validate).
"""
import re, sys, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
spec = (root / 'soc/google/gs101/docs/07-gs101-ufs-bringup.md').read_text()

def rows(name):
    # Table `NAME` ... then markdown rows "| n | COMN|TRSV | 0xOFF | 0xVAL | tag |" until the table ends.
    i = spec.index('Table `%s`' % name)
    res = []
    for line in spec[i:].splitlines()[1:]:
        m = re.match(r'\|\s*\d+\s*\|\s*(COMN|TRSV)\s*\|\s*(0x[0-9A-Fa-f]+)\s*\|\s*(0x[0-9A-Fa-f]+)\s*\|', line)
        if m:
            res.append((m.group(1), int(m.group(2), 16), int(m.group(3), 16)))
        elif res:
            break
    return res

out = ['/* Generated from soc/google/gs101/docs/07-gs101-ufs-bringup.md Appendix A (Linux phy-gs101-ufs.c, commit 72a16bc);',
       ' * do not edit by hand: tools/gen-ufsphy-tables.py regenerates it. COMN rows write lane 0 only,',
       ' * TRSV rows write lane 0 at (off<<2) and lane 1 at ((off+0x200)<<2). */',
       '#pragma once', '#include <stdint.h>', '', 'struct ufsphy_row { uint8_t trsv; uint16_t off; uint8_t val; };', '']
for n in ('tensor_gs101_pre_init_cfg', 'tensor_gs101_pre_pwr_hs_config', 'tensor_gs101_post_pwr_hs_config'):
    out.append('static const struct ufsphy_row %s[] = {' % n.replace('tensor_gs101_', 'ufsphy_'))
    out += ['\t{ %d, 0x%03X, 0x%02X },' % (1 if k == 'TRSV' else 0, o, v) for k, o, v in rows(n)]
    out += ['};', '']
text = '\n'.join(out)
hdr = root / 'soc/google/gs101/shim/ufsphy_tables.h'
if len(sys.argv) > 1:
    if sys.argv[1:] != ['--check']:
        print('usage: gen-ufsphy-tables.py [--check]\nRESULT: ERROR bad arguments')
        sys.exit(2)
    have = hdr.read_text() if hdr.exists() else ''
    if have == text:
        print('RESULT: PASS ufsphy_tables.h matches the spec tables')
        sys.exit(0)
    a, b = have.splitlines(), text.splitlines()
    n = next((i for i in range(max(len(a), len(b))) if i >= len(a) or i >= len(b) or a[i] != b[i]), 0)
    print('first differing line %d: have=%r want=%r' % (n + 1, a[n] if n < len(a) else None, b[n] if n < len(b) else None))
    print('RESULT: FAIL ufsphy_tables.h differs from the spec tables (run tools/gen-ufsphy-tables.py)')
    sys.exit(1)
hdr.write_text(text)
