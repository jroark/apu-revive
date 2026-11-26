#!/usr/bin/env python3
"""Extract the EZ-USB (AN2136) 8051 firmware embedded in pgdmpuz.drv.

The table at VA 0x10038010 is an array of 22-byte Cypress INTEL_HEX_RECORDs:
  u8 length; u8 pad; u16 address; u8 type; u8 data[16]; u8 pad
terminated by a record whose type != 0.
"""
import struct
import sys

IMAGE_BASE = 0x10000000
TABLE_VA = 0x10038010
RECLEN = 22


def records(drv: bytes):
    off = TABLE_VA - IMAGE_BASE  # file alignment == section alignment in this DLL
    while True:
        length, addr, rtype = struct.unpack_from("<BxHB", drv, off)
        if rtype != 0:
            return
        yield addr, drv[off + 5 : off + 5 + length]
        off += RECLEN


def main():
    drv_path, out_path = sys.argv[1], sys.argv[2]
    drv = open(drv_path, "rb").read()
    image = bytearray(0x2000)
    hi = 0
    n = 0
    for addr, data in records(drv):
        image[addr : addr + len(data)] = data
        hi = max(hi, addr + len(data))
        n += 1
    open(out_path, "wb").write(image[:hi])
    print(f"{n} records, image 0x0000-0x{hi:04x}")


if __name__ == "__main__":
    main()
