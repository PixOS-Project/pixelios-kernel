#!/usr/bin/env python3
"""Count the non-zero BLOCK-byte blocks of a (sparse) image: roughly what fastboot's sparse upload sends. Streaming read; holes are
skipped with SEEK_DATA/SEEK_HOLE (a filesystem without SEEK_DATA reports the whole file as data). Prints 'NONZERO_BLOCKS TOTAL_BLOCKS' (total rounds up)."""
import os, sys

BLOCK = 4096
CHUNK = 1 << 22  # 4 MiB, a multiple of BLOCK


def count(path):
    size = os.stat(path).st_size
    zero = bytes(BLOCK)
    nz = 0
    with open(path, "rb", buffering=0) as f:
        fd = f.fileno()
        pos = 0
        while pos < size:
            try:
                pos = os.lseek(fd, pos, os.SEEK_DATA)
                end = os.lseek(fd, pos, os.SEEK_HOLE)
            except OSError:  # ENXIO: only a hole remains
                break
            pos -= pos % BLOCK
            f.seek(pos)
            while pos < end:
                buf = f.read(min(CHUNK, end - pos))
                if not buf:
                    break
                for o in range(0, len(buf), BLOCK):
                    if buf[o:o + BLOCK] != zero[:len(buf) - o]:
                        nz += 1
                pos += len(buf)
    return nz, -(-size // BLOCK)


if __name__ == "__main__":
    a, b = count(sys.argv[1])
    print(a, b)
