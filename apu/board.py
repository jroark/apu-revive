"""Native board-level operations on a configured APU (no Altera code at run time).

Each function documents where its sequence was recovered from; see re/protocol.md.
"""
from . import protocol
from .device import Apu

PORT_DAC_BUS = 0x01      # bit 5 doubles as the adapter-ID strobe
PORT_STATUS = 0x08
ADAPTER_STROBE = 0x20

# Adapter IDs observed on real hardware (the module accepts many more; see re/protocol.md)
KNOWN_ADAPTERS = {9: "PLMJ7000-44", 55: "PLMJ7000-84"}


def startup_words():
    """The first twelve port writes pgdmpuz.drv issues after configuring the FPGA
    (re/traces/init_real.txt). Opcode-5 writes set up ports 1, 2, 8, 9 and 0x0a; without them
    port 8 reads 0. The meaning of the individual port 8 / port 9 values is not decoded."""
    w = lambda port, val: protocol.word(protocol.OP_WRITE, port, val)
    d = lambda port, val: protocol.word(protocol.OP_WRITE5, port, val)
    return [w(8, 0), w(8, 1), w(8, 3), w(0, 0),
            d(1, 0xFF), d(2, 0xFF), d(8, 0xFF), d(9, 0xFF), d(0x0A, 0xFF),
            w(0, 0), w(9, 1), w(9, 0x7F)]


def startup(apu: Apu):
    apu.xfer(startup_words())


def adapter_id(apu: Apu) -> int:
    """Read the fitted adapter's ID (0..0x7e).

    From pgdmpuz.drv 0x100063a0: raise port 1 bit 5, read port 8, drop the strobe; done twice
    and the two readings must agree. The ID is the low 7 bits.
    """
    readings = []
    for _ in range(2):
        apu.write_port(PORT_DAC_BUS, ADAPTER_STROBE)
        readings.append(apu.read_port(PORT_STATUS) & 0x7F)
        apu.write_port(PORT_DAC_BUS, 0x00)
    if readings[0] != readings[1]:
        raise RuntimeError(f"adapter ID unstable: {readings}")
    return readings[0]


# --- voltage DACs (two TLC5620 quad 8-bit DACs on a bit-banged serial bus on port 1) ---------
DAC_FRAME_START = 0xC0
DAC_CLOCK_HIGH = 0x82      # | data bit
DAC_CLOCK_LOW = 0x80       # | data bit
DAC_LOAD = {"A": 0x88, "B": 0x84}

# Channel roles inferred from how the module's final DAC codes follow the requested voltages
# (simulation, tools/dac_decode.py). Full-scale values are approximate.
DAC_VPD = ("B", 0)         # programming-pin rail: code ~= mV * 256 / 20000
DAC_VPG = ("B", 1)         # second programming rail: one data point (9000 mV -> 118)
DAC_VMARG = ("B", 2)       # margin rail: code ~= mV * 256 / 10000
DAC_VCC = ("B", 3)         # supply rail: ~10 V full scale; the module ramps it one code at a
                           # time and trims it against feedback read on port 1 (not yet native)
DAC_REFERENCE = ("A", 0)   # set to 45 at start-up and before every set-up; purpose unconfirmed


def dac_words(chip: str, channel: int, rng: int, code: int):
    """Port writes for one TLC5620 command: 0xC0, then eleven bits (A1 A0 RNG D7..D0) each as
    clock-high / clock-low writes with the data on bit 0, then the chip's load strobe, then 0.
    Recovered from the module's start-up trace (25 writes per command)."""
    if chip not in DAC_LOAD or not 0 <= channel <= 3 or rng not in (0, 1) or not 0 <= code <= 255:
        raise ValueError("bad DAC command")
    bits = [(channel >> 1) & 1, channel & 1, rng] + [(code >> i) & 1 for i in range(7, -1, -1)]
    values = [DAC_FRAME_START]
    for b in bits:
        values += [DAC_CLOCK_HIGH | b, DAC_CLOCK_LOW | b]
    values += [DAC_LOAD[chip], 0x00]
    return [protocol.word(protocol.OP_WRITE, PORT_DAC_BUS, v) for v in values]


def set_dac(apu: Apu, chip: str, channel: int, code: int, rng: int = 1):
    apu.xfer(dac_words(chip, channel, rng, code))


# --- status LED -----------------------------------------------------------------------------
PORT_LED = 0x00
LED_OFF, LED_A, LED_B = 0, 1, 2


def led_words(state: int):
    """From pgdmpuz.drv 0x10003f60 / real trace: opcode-5 write F7 to port 0, then the LED bits
    (state 1 -> bit 7, state 2 -> bit 6, state 0 -> neither). Which colour each state shows has
    not been observed."""
    value = {LED_OFF: 0x00, LED_A: 0x80, LED_B: 0x40}[state]
    return [protocol.word(protocol.OP_WRITE5, PORT_LED, 0xF7), protocol.word(protocol.OP_WRITE, PORT_LED, value)]


def set_led(apu: Apu, state: int):
    apu.xfer(led_words(state))


# --- on-board ADC (serial, shares the port-1 bus) ------------------------------------------
ADC_IDLE, ADC_CLOCK, ADC_WAIT = 0x90, 0x92, 0x80


def _adc_pass_words(channel: int, read: bool):
    w = lambda v: protocol.word(protocol.OP_WRITE, PORT_DAC_BUS, v)
    words = [w(ADC_IDLE)] * 8
    for shift in (3, 2, 1, 0, None, None, None, None):          # 4 address bits, then 4 zeros
        bit = (channel >> shift) & 1 if shift is not None else 0
        words += [w(ADC_IDLE | bit), w(ADC_CLOCK | bit)]
        if read:
            words.append(protocol.word(protocol.OP_READ, PORT_DAC_BUS))
    return words


def adc_read(apu: Apu, channel: int) -> int:
    """Read one of the unit's 12 ADC channels (8-bit result).

    From pgdmpuz.drv 0x10007ad0: pass 1 clocks out the channel address and waits for
    conversion (write 0x80, read port 1 until bit 1 is set, at most ten tries); pass 2 repeats
    the clocking and samples port 1 after each clock (bit 1 = valid, bit 0 = data, MSB first).
    """
    if not 0 <= channel < 12:
        raise ValueError("ADC channel 0..11")
    apu.xfer(_adc_pass_words(channel, read=False))
    for _ in range(10):
        ready = apu.xfer([protocol.word(protocol.OP_WRITE, PORT_DAC_BUS, ADC_WAIT),
                          protocol.word(protocol.OP_READ, PORT_DAC_BUS)])[0]
        if ready & 2:
            break
    samples = apu.xfer(_adc_pass_words(channel, read=True))
    apu.write_port(PORT_DAC_BUS, 0x00)
    value = 0
    for sample in samples:
        if not sample & 2:
            raise RuntimeError("ADC sample not valid: %s" % samples.hex())
        value = (value << 1) | (sample & 1)
    return value


# --- rails: DAC channel, ADC channel and scales --------------------------------------------
# DAC code = mV * 255 / full_scale (module helpers 0x100069b0 ... 0x10006850). ADC channels and
# their scales were found on hardware by stepping one DAC at a time (re/protocol.md):
# VPD and VPG read back at about 0.72 counts per DAC count; VCC and VMARG at 1:1 on a shared
# channel (either DAC alone moves it).
RAILS = {
    #        DAC          DAC full scale (mV)   ADC channel   ADC mV per count (approximate)
    "vpd":   (DAC_VPD,    20000,                7,            109.0),
    "vpg":   (DAC_VPG,    20000,                6,            109.0),
    "vmarg": (DAC_VMARG,  10000,                3,            39.2),
    "vcc":   (DAC_VCC,    10000,                3,            39.2),
}


def rail_code(name: str, millivolts: int) -> int:
    _, full_scale, _, _ = RAILS[name]
    code = millivolts * 255 // full_scale
    if not 0 <= code <= 255:
        raise ValueError("voltage out of range for " + name)
    return code


def set_rail(apu: Apu, name: str, millivolts: int):
    """Open-loop: write the DAC code for a rail. The vendor module additionally trims the code
    against the ADC; that trim is not implemented here."""
    (chip, channel), _, _, _ = RAILS[name]
    set_dac(apu, chip, channel, rail_code(name, millivolts))


def read_rail(apu: Apu, name: str) -> float:
    """Approximate rail voltage in millivolts from the on-board ADC."""
    _, _, channel, mv_per_count = RAILS[name]
    return adc_read(apu, channel) * mv_per_count


def vpd_code(millivolts: int) -> int:
    """DAC code for the VPD rail as the module computes it (0x10018ae5): above 5 V the request is
    raised by (mV - 5000) / 50 before conversion."""
    if millivolts > 5000:
        millivolts += (millivolts - 5000) // 50
    return millivolts * 255 // 20000


def vpg_code(millivolts: int) -> int:
    """DAC code for the VPG rail (0x10018b21): above 5 V raised by (mV - 5000) / 12."""
    if millivolts > 5000:
        millivolts += (millivolts - 5000) // 12
    return millivolts * 255 // 20000
