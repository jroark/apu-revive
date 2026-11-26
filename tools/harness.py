#!/usr/bin/env python3
"""Run Altera's pgdmpuz.drv under a user-mode x86 emulator (Unicorn) with its
Windows imports stubbed, so the USB traffic it would generate can be logged
("golden traces") without Windows. The USB device is simulated by FakeApu.
"""
import struct
import sys

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, UcError
from unicorn.x86_const import *

STUBS = 0x70000000
STACK_TOP = 0x7FF00000
STACK_SIZE = 0x400000
HEAP = 0x60000000
HEAP_SIZE = 0x4000000
SENTINEL = STUBS + 0xFFF0

# name -> (nargs to pop for stdcall; 0 for cdecl)
STDCALL = {
    "DeviceIoControl": 8, "CloseHandle": 1, "GetLastError": 0, "FatalAppExitA": 2,
    "GetTickCount": 0, "DisableThreadLibraryCalls": 1, "CreateFileA": 7, "GetVersion": 0,
    "GetCapture": 0, "PostMessageA": 4, "KillTimer": 2, "SetTimer": 4, "MessageBoxA": 4,
}


def decode_wire(data: bytes):
    """Decode an EP2 OUT byte stream into (kind, addr, value) port operations."""
    ops, i = [], 0
    while i < len(data):
        b = data[i]
        if b & 0xC0 == 0x80:
            ops.append(("R", b & 0x3F, None)); i += 1
        else:
            v = data[i + 1] if i + 1 < len(data) else None
            if (b, v) != (0x40, 0xFF):
                ops.append(({0x00: "W", 0x40: "W5", 0xC0: "W8"}[b & 0xC0], b & 0x3F, v))
            i += 2
    return ops


class FakeApu:
    """Minimal model: 9 V present, FPGA configures, port reads return read_value."""
    def __init__(self, log):
        self.log, self.status, self.pending, self.read_value = log, 0x40, b"", 0x00
        self.config_bytes = 0
        self.mode_config = False

    def vendor(self, req, value, data):
        self.log(f"VENDOR req={req:#x} value={value:#06x} data={data.hex()}")

    def download(self, addr, data):
        self.log(f"FWLOAD addr={addr:#06x} len={len(data)}")

    def write(self, pipe, data):
        if pipe == 4:
            cmd = data[1]
            self.mode_config = bool(cmd & 1)
            self.status = 0x40 | (cmd & 0x94) | {0x41: 0x00, 0x51: 0x08}.get(cmd, 0x28)
            self.log(f"CTL cmd={cmd:#04x} -> status {self.status:#04x}")
        elif self.mode_config:
            self.config_bytes += len(data)
            self.log(f"RBF {len(data)} bytes (total {self.config_bytes})")
        else:
            tail = len(data) % 64
            body, count = data, 0
            if tail:
                body, count = data[:-2], data[-2] | data[-1] << 8
            ops = decode_wire(body)
            for kind, addr, val in ops:
                self.log(f"  {kind:2} port {addr:#04x}" + ("" if val is None else f" = {val:#04x}"))
            self.log(f"OUT {len(data)} bytes, {len(ops)} ops, read count {count}")
            self.pending = bytes([self.read_value]) * count

    def read(self, pipe, n):
        if pipe == 3:
            return bytes([self.status])
        out, self.pending = self.pending[:n], self.pending[n:]
        self.log(f"IN {n} bytes")
        return out.ljust(n, b"\0")


class Harness:
    def __init__(self, drv_path, device=None, verbose=True):
        self.verbose = verbose
        self.trace = []
        self.pe = pe = pefile.PE(drv_path)
        self.base = pe.OPTIONAL_HEADER.ImageBase
        uc = self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        uc.mem_map(self.base, size)
        uc.mem_write(self.base, pe.header)
        for s in pe.sections:
            uc.mem_write(self.base + s.VirtualAddress, s.get_data())
        uc.mem_map(STUBS, 0x10000)
        uc.mem_write(STUBS, b"\xc3" * 0x10000)
        uc.mem_map(STACK_TOP - STACK_SIZE, STACK_SIZE)
        uc.mem_map(HEAP, HEAP_SIZE)
        self.heap_next = HEAP + 0x10
        self.allocs = {}
        self.files = {}
        self.imports = {}
        n = 0
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            for imp in entry.imports:
                addr = STUBS + 0x100 + n * 4
                n += 1
                self.imports[addr] = imp.name.decode()
                uc.mem_write(imp.address, struct.pack("<I", addr))
        self.exports = {e.name.decode(): self.base + e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols}
        self.device = device or FakeApu(self.log)
        uc.hook_add(UC_HOOK_CODE, self._stub, begin=STUBS, end=STUBS + 0xFFFF)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        uc.reg_write(UC_X86_REG_ESP, STACK_TOP - 0x1000)

    # --- helpers ---------------------------------------------------------------
    def log(self, msg):
        self.trace.append(msg)
        if self.verbose:
            print(msg)

    def u32(self, addr):
        return struct.unpack("<I", self.uc.mem_read(addr, 4))[0]

    def cstr(self, addr):
        out = bytearray()
        while addr:
            b = self.uc.mem_read(addr, 1)[0]
            if not b:
                break
            out.append(b); addr += 1
        return out.decode("latin1")

    def alloc(self, size):
        addr = self.heap_next
        self.heap_next += (size + 0x1F) & ~0xF
        self.allocs[addr] = size
        return addr

    def put(self, data: bytes):
        addr = self.alloc(len(data) + 1)
        self.uc.mem_write(addr, data + b"\0")
        return addr

    def _unmapped(self, uc, access, addr, size, value, user):
        eip = uc.reg_read(UC_X86_REG_EIP)
        self.log(f"!! unmapped access {addr:#x} at eip {eip:#x}")
        return False

    # --- import stubs ----------------------------------------------------------
    def _stub(self, uc, addr, size, user):
        if addr == SENTINEL:
            uc.emu_stop(); return
        name = self.imports.get(addr)
        if name is None:
            return
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = self.u32(esp)
        a = lambda i: self.u32(esp + 4 + 4 * i)
        result = getattr(self, "imp_" + name, None)
        if result is None:
            self.log(f"?? unimplemented import {name}({a(0):#x}, {a(1):#x}, {a(2):#x}) from {ret:#x}")
            val = 0
        else:
            val = result(a)
        uc.reg_write(UC_X86_REG_EAX, (val or 0) & 0xFFFFFFFF)
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + 4 * STDCALL.get(name, 0))
        uc.reg_write(UC_X86_REG_EIP, ret)

    # GE.dll (signatures guessed)
    def imp_ge_get_cfg(self, a):
        self.log(f"ge_get_cfg({self.cstr(a(0))!r})"); return 0
    def imp_ge_alloc(self, a): return self.alloc(a(0))
    def imp_ge_alloc_size(self, a): return self.allocs.get(a(0), 0)
    def imp_ge_free_alloc_ptr(self, a): return 0
    def imp_ge_realloc(self, a):
        new = self.alloc(a(1)); old = self.allocs.get(a(0), 0)
        if a(0): self.uc.mem_write(new, bytes(self.uc.mem_read(a(0), min(old, a(1)))))
        return new
    # MSVCRT
    def imp_malloc(self, a): return self.alloc(a(0))
    def imp_free(self, a): return 0
    def imp__initterm(self, a): return 0
    def imp__assert(self, a):
        self.log(f"ASSERT {self.cstr(a(0))!r} {self.cstr(a(1))}:{a(2)}"); return 0
    def imp_fopen(self, a):
        path = self.cstr(a(0))
        try:
            f = open(path, "rb")
        except OSError:
            self.log(f"fopen({path!r}) failed"); return 0
        h = self.alloc(4); self.files[h] = f
        self.log(f"fopen({path!r})"); return h
    def imp_fread(self, a):
        data = self.files[a(3)].read(a(1) * a(2))
        self.uc.mem_write(a(0), data); return len(data) // max(a(1), 1)
    def imp_fclose(self, a): self.files.pop(a(0)).close(); return 0
    def imp_sprintf(self, a):
        fmt = self.cstr(a(1))
        if fmt == "%s-%03d":
            s = "%s-%03d" % (self.cstr(a(2)), a(3))
        else:
            s = fmt
            self.log(f"sprintf fmt {fmt!r} not formatted")
        self.uc.mem_write(a(0), s.encode() + b"\0"); return len(s)
    def imp_sscanf(self, a): return 0
    # KERNEL32 / USER32
    def imp_GetVersion(self, a): return 0x00000005        # NT 5.0 -> USB path
    realtime = False   # True when driving real hardware: delays must take real time
    call_timeout_s = 120

    def imp_GetTickCount(self, a):
        if self.realtime:
            import time
            return int(time.monotonic() * 1000) & 0xFFFFFFFF
        self.ticks = getattr(self, "ticks", 0) + 10; return self.ticks
    def imp_GetLastError(self, a): return 0
    def imp_DisableThreadLibraryCalls(self, a): return 1
    def imp_CloseHandle(self, a): self.log("CloseHandle"); return 1
    def imp_CreateFileA(self, a): self.log(f"CreateFile({self.cstr(a(0))!r})"); return 0x1234
    def imp_MessageBoxA(self, a):
        self.log(f"MessageBox: {self.cstr(a(1))!r} [{self.cstr(a(2))}]"); return 1
    def imp_FatalAppExitA(self, a):
        self.log(f"FatalAppExit: {self.cstr(a(1))!r}"); self.uc.emu_stop(); return 0
    def imp_GetCapture(self, a): return 0
    def imp_PostMessageA(self, a): return 1
    def imp_SetTimer(self, a): return 1
    def imp_KillTimer(self, a): return 1

    def imp_DeviceIoControl(self, a):
        code, inbuf, inlen, outbuf, outlen, pret = a(1), a(2), a(3), a(4), a(5), a(6)
        rd = lambda p, n: bytes(self.uc.mem_read(p, n)) if n else b""
        done = 0
        if code == 0x222014:
            req, value, index, length, direction = struct.unpack_from("<BxHHHB", rd(inbuf, inlen))
            self.device.vendor(req, value, bytes([rd(inbuf, inlen)[9]]))
        elif code == 0x22206D:
            self.device.download(struct.unpack("<H", rd(inbuf, 2))[0], rd(outbuf, outlen)); done = outlen
        elif code == 0x222051:
            self.device.write(self.u32(inbuf), rd(outbuf, outlen)); done = outlen
        elif code == 0x22204E:
            data = self.device.read(self.u32(inbuf), outlen)
            self.uc.mem_write(outbuf, data); done = len(data)
        else:
            self.log(f"IOCTL {code:#x} (unhandled)")
        if pret:
            self.uc.mem_write(pret, struct.pack("<I", done))
        return 1

    # --- calling into the DLL --------------------------------------------------
    def call(self, target, *args, stdcall=False):
        addr = self.exports[target] if isinstance(target, str) else target
        uc = self.uc
        esp = uc.reg_read(UC_X86_REG_ESP)
        frame = struct.pack(f"<{len(args) + 1}I", SENTINEL, *[x & 0xFFFFFFFF for x in args])
        esp -= len(frame)
        uc.mem_write(esp, frame)
        uc.reg_write(UC_X86_REG_ESP, esp)
        try:
            # Watchdog: a module call that runs longer than call_timeout_s is abandoned, so a
            # bad argument cannot leave real hardware powered indefinitely.
            uc.emu_start(addr, SENTINEL, timeout=int(self.call_timeout_s * 1e6))
            if uc.reg_read(UC_X86_REG_EIP) != SENTINEL:
                raise TimeoutError(f"module call {target} exceeded {self.call_timeout_s} s")
        except UcError as e:
            self.log(f"!! emulation error {e} at eip {uc.reg_read(UC_X86_REG_EIP):#x}")
            raise
        uc.reg_write(UC_X86_REG_ESP, esp + len(frame))
        return uc.reg_read(UC_X86_REG_EAX)

    def dllmain(self):
        entry = self.base + self.pe.OPTIONAL_HEADER.AddressOfEntryPoint
        return self.call(entry, self.base, 1, 0)


if __name__ == "__main__":
    vendor = sys.argv[1]
    h = Harness(vendor + "/pgdmpuz.drv")
    print("DllMain ->", h.dllmain())
    rbf = h.put((vendor + "/pgdmpuz.rbf").encode())
    print("setup(12) ->", h.call("pgd_setup", 12, 0, 0, 0, 0x44, 0))
    print("setup(1) ->", h.call("pgd_setup", 1, 0, rbf, 0, 0, 0))


class UsbApu:
    """Bridge the emulated module's I/O to the real APU through apu.device."""
    PIPE_EP = {1: 0x82, 2: 0x02, 3: 0x84, 4: 0x04, 5: 0x86}

    def __init__(self, log):
        from apu.device import Apu
        self.log, self.apu = log, Apu.open()

    def vendor(self, req, value, data):
        self.log(f"VENDOR req={req:#x} value={value:#06x} data={data.hex()}")
        self.apu.dev.ctrl_transfer(0x40, req, value, 0, data, 5000)

    def download(self, addr, data):
        self.apu.dev.ctrl_transfer(0x40, 0xA0, addr, 0, data, 5000)

    mode_config = False

    def write(self, pipe, data):
        if pipe == 4:
            self.mode_config = bool(data[1] & 1)
            self.log(f"CTL cmd={data[1]:#04x}")
        elif self.mode_config:
            self.log(f"RBF {len(data)} bytes")
        else:
            body, count = data, 0
            if len(data) % 64:
                body, count = data[:-2], data[-2] | data[-1] << 8
            for kind, addr, val in decode_wire(body):
                self.log(f"  {kind:2} port {addr:#04x}" + ("" if val is None else f" = {val:#04x}"))
            self.log(f"OUT {len(data)} bytes, read count {count}")
        self.apu.dev.write(self.PIPE_EP[pipe], data, 30000)

    def read(self, pipe, n):
        data = bytes(self.apu.dev.read(self.PIPE_EP[pipe], n, 5000))
        self.log(f"USB IN pipe {pipe}: {data.hex()}")
        return data


class ReplayApu(FakeApu):
    """FakeApu that answers port reads with data recorded from the real unit
    (lines 'USB IN pipe 1: <hex>' in a trace), then falls back to read_value."""
    def __init__(self, log, trace_path):
        super().__init__(log)
        self.recorded = [bytes.fromhex(l.split(": ")[1]) for l in open(trace_path)
                         if l.startswith("USB IN pipe 1:")]

    def read(self, pipe, n):
        if pipe != 3 and self.recorded and len(self.recorded[0]) == n:
            self.pending = b""
            self.log(f"IN {n} bytes (replayed)")
            return self.recorded.pop(0)
        return super().read(pipe, n)


def boot_sim(vendor="vendor/maxplus2-10.2/MAX+plus_II_BASELINE", trace="re/traces/init_real.txt"):
    """Emulated module, initialised against the replay device. Returns the Harness."""
    h = Harness(vendor + "/pgdmpuz.drv", verbose=False)
    h.device = ReplayApu(h.log, trace)
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    if h.call("pgd_setup", 1, 0x280, h.put((vendor + "/pgdmpuz.rbf").encode())) != 1:
        raise RuntimeError("emulated init failed")
    return h
