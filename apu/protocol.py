"""Pure (no-USB) encoding of the APU wire protocol, recovered from
ezusbclient.c inside pgdmpuz.drv. See re/protocol.md.
"""

# Opcodes carried in the top byte of a 32-bit command word:
#   word = op << 24 | addr << 16 | data
OP_NOP = 2
OP_READ = 3      # wire: 0x80+addr            -> one reply byte
OP_WRITE = 4     # wire: addr, data
OP_WRITE5 = 5    # wire: 0x40+addr, data
OP_WRITE8 = 8    # wire: 0xC0+addr, data
NOP_PAIR = b"\x40\xff"
PACKET = 64


def word(op: int, addr: int = 0, data: int = 0) -> int:
    return (op & 0xFF) << 24 | (addr & 0xFF) << 16 | (data & 0xFFFF)


def encode(words):
    """Return (wire_bytes, number_of_reply_bytes) for a list of command words."""
    out = bytearray()
    reads = 0
    for w in words:
        op, addr, data = w >> 24, (w >> 16) & 0xFF, w & 0xFF
        if op == OP_READ:
            out.append((0x80 + addr) & 0xFF)
            reads += 1
        elif op == OP_WRITE:
            out += bytes((addr, data))
        elif op == OP_WRITE5:
            out += bytes(((0x40 + addr) & 0xFF, data))
        elif op == OP_WRITE8:
            out += bytes(((0xC0 + addr) & 0xFF, data))
        elif op == 1:
            raise ValueError("CMD 01 not supported")
        else:
            out += NOP_PAIR
    return bytes(out), reads


def pad(wire: bytes) -> bytes:
    """Pad a write-only stream to whole 64-byte packets with NOP pairs.

    The firmware treats any short OUT packet as a read request, so a
    write-only stream must end exactly on a packet boundary.
    """
    if len(wire) % 2:
        raise ValueError("write-only stream must have even length")
    rem = len(wire) % PACKET
    return wire + NOP_PAIR * ((PACKET - rem) // 2)


def pad_with_reads(wire: bytes, reads: int) -> bytes:
    """Terminate a stream that contains `reads` read commands.

    The final packet must be short and end with the 16-bit little-endian
    number of reply bytes; the firmware then returns them on EP2 IN.
    """
    if not 0 < reads < 0x10000:
        raise ValueError("read count out of range")
    fill = PACKET - len(wire) % PACKET
    if fill < 5:  # keep the trailer from straddling a packet boundary
        wire += NOP_PAIR * ((fill + 1) // 2)
    return wire + NOP_PAIR + bytes((reads & 0xFF, reads >> 8))


# FPGA (FLEX 10K100E) configuration -----------------------------------------
RBF_PREFIX = 11          # 0xFF bytes prepended to the .rbf
RBF_CHUNK = 0xFF00       # bytes per bulk write
CTL_CONFIG_START = 0x41
CTL_CONFIG_DATA = 0x51
CTL_CONFIG_END = 0x94
ST_MASK = 0x28           # two status bits checked by the client

_PERM = [(i >> 3) | ((i << 3) & 0x3F) for i in range(PACKET)]


def rbf_stream(rbf: bytes) -> bytes:
    """Prefix and reorder an .rbf exactly as the Windows client sends it."""
    buf = b"\xff" * RBF_PREFIX + rbf
    if len(buf) % PACKET:
        raise ValueError("rbf length + 11 must be a multiple of 64")
    out = bytearray(len(buf))
    for n in range(len(buf)):
        out[n] = buf[(n & ~0x3F) + _PERM[n & 0x3F]]
    return bytes(out)


def rbf_chunks(rbf: bytes):
    s = rbf_stream(rbf)
    for i in range(0, len(s), RBF_CHUNK):
        yield s[i : i + RBF_CHUNK]
