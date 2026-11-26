"""Native device-level knowledge for MAX 7000 parts (no Altera code at run time)."""


def signature_byte(cells, k: int, cell_width: int = 10) -> int:
    """Byte `k` of the signature ("MS") row, from that row's cells as a line read returns them.

    From pgdmpuz.drv 0x10003a1c (the EPM7160E family of module types): the byte is one bit plane
    across eight cells spaced five apart, most significant bit first.
        first cell = k // (2 * width) + (40 if k is odd else 0)
        bit        = (k >> 1) % width
    Bytes 0-7 are the silicon ID; later bytes carry the device's voltage and timing codes.
    """
    base = k // (2 * cell_width) + (40 if k & 1 else 0)
    bit = (k >> 1) % cell_width
    value = 0
    for j in range(8):
        value = (value << 1) | ((cells[base + 5 * j] >> bit) & 1)
    return value


def silicon_id(cells, cell_width: int = 10) -> str:
    return bytes(signature_byte(cells, k, cell_width) for k in range(8)).decode("latin1")
