"""Locate the EZ-USB firmware records embedded in MAX+PLUS II's pgdmpuz.drv.

The firmware is Altera's and is not redistributed here; it is read at run time
from the user's own copy of pgdmpuz.drv.
"""
import struct

IMAGE_BASE = 0x10000000
TABLE_VA = 0x10038010  # pgdmpuz.drv from MAX+PLUS II 10.2 (sha256 df4a6e79...)
RECLEN = 22


def records(drv: bytes):
    """Yield (address, data) for each firmware record (<=16 bytes each)."""
    off = TABLE_VA - IMAGE_BASE
    while True:
        length, addr, rtype = struct.unpack_from("<BxHB", drv, off)
        if rtype != 0:
            return
        if length > 16:
            raise ValueError("unexpected firmware record; wrong pgdmpuz.drv version?")
        yield addr, bytes(drv[off + 5 : off + 5 + length])
        off += RECLEN
