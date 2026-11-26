#!/usr/bin/env python3
"""Host parts of Altera's pgm.dll next to pgdmpuz.drv in one emulator, so the programmer's own
routines (control-file loader, erase, program) make the decisions and drive the module.

pgm.dll is relocated to PGM_BASE; its hardware function table (0x1006fc90 in the original
image) is filled with the addresses of the module's exports, exactly as phm.dll would.
Imports are stubbed: the handful that matter are implemented, the rest log and return 0.
"""
import pathlib
import struct
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pefile
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EIP, UC_X86_REG_ESP

import harness
import read_siid as rs

PGM_BASE = 0x20000000
PGM_ORIG = 0x10000000
PGM_STUBS = 0x71000000
SLOT_BASE = 0x1006FC90
SLOTS = {4: "program_byte", 8: "program_bytes_on_column", 12: "program_setup", 16: "read_byte",
         20: "read_bytes_on_column", 24: "read_chgrp", 28: "read_silicon_id", 32: "bulk_erase",
         36: "bulk_program", 40: "set_chgrp_dr", 44: "set_chgrp_io", 48: "set_column_address",
         52: "set_row_address", 56: "setup", 60: "jtag_io", 64: "jtag_drscan", 68: "jtag_irscan"}
DESTRUCTIVE = ("bulk_erase", "bulk_program", "program_byte", "program_bytes_on_column")
# stdcall argument counts for system imports pgm.dll may call on our paths
STDCALL = {"GetVersion": 0, "GetTickCount": 0, "DisableThreadLibraryCalls": 1, "MessageBeep": 1,
           "GetPrivateProfileStringA": 6, "WritePrivateProfileStringA": 4, "SetCurrentDirectoryA": 1,
           "GetTempPathA": 2, "GetTempFileNameA": 4,
           # USER32 / GDI32: GUI calls are no-ops here; only the argument counts matter
           "InvalidateRect": 3, "UpdateWindow": 1, "GetDlgItem": 2, "SendDlgItemMessageA": 5,
           "EnableWindow": 2, "SetWindowTextA": 2, "GetWindowTextA": 3, "SendMessageA": 4,
           "PostMessageA": 4, "GetParent": 1, "ShowWindow": 2, "SetFocus": 1, "GetFocus": 0,
           "IsWindow": 1, "IsWindowEnabled": 1, "IsWindowVisible": 1, "IsZoomed": 1, "IsIconic": 1,
           "GetClientRect": 2, "GetWindowRect": 2, "SetWindowPos": 7, "MoveWindow": 6,
           "DestroyWindow": 1, "CreateWindowExA": 12, "GetDC": 1, "GetWindowDC": 1, "ReleaseDC": 2,
           "SetCursor": 1, "LoadCursorA": 2, "GetSystemMetrics": 1, "SetTimer": 4, "KillTimer": 2,
           "MessageBoxA": 4, "EndDialog": 2, "PtInRect": 3, "ChildWindowFromPoint": 3,
           "CheckDlgButton": 3, "IsDlgButtonChecked": 2, "SetDlgItemTextA": 3, "GetDlgItemTextA": 4,
           "SetDlgItemInt": 4, "GetDlgItemInt": 4, "CheckRadioButton": 4, "PeekMessageA": 5,
           "TranslateMessage": 1, "DispatchMessageA": 1, "GetCapture": 0, "SetCapture": 1,
           "ReleaseCapture": 0, "GetCursorPos": 1, "ScreenToClient": 2, "ClientToScreen": 2,
           "FillRect": 3, "DrawTextA": 5, "BeginPaint": 2, "EndPaint": 2, "GetSysColor": 1,
           "SelectObject": 2, "DeleteObject": 1, "SetBkColor": 2, "SetTextColor": 2, "SetBkMode": 2,
           "ExtTextOutA": 8, "Rectangle": 5, "CreateSolidBrush": 1, "CreatePen": 3,
           "GetStockObject": 1, "GetTextMetricsA": 2, "GetTextExtentPointA": 4,
           "RegOpenKeyExA": 5, "RegQueryValueExA": 6, "RegCloseKey": 1, "RegEnumKeyExA": 8}
SYSTEM_DLLS = {"kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll"}


class Host:
    def __init__(self, part, real=False, vendor=rs.V):
        self.part = part
        self.pcf_path, self.part_id = rs.lookup_part(part)
        self.messages = []
        if real:
            h = harness.Harness(vendor + "/pgdmpuz.drv", verbose=False)
            h.device = harness.UsbApu(lambda m: None)
            h.realtime = True
            h.dllmain()
            h.call("pgd_setup", 12, 0x44, h.alloc(1024))
            if h.call("pgd_setup", 1, 0x280, h.put((vendor + "/pgdmpuz.rbf").encode())) != 1:
                raise RuntimeError("init failed")
            self.measured_calibration = rs.apply_calibration(h)
        else:
            h = harness.boot_sim()
        self.h = h
        self.module_calls = []
        self._ret_hooks = {}
        # Module calls that change a chip. Blocked by default: they are logged and report
        # success to pgm.dll without reaching the module. Empty this set only deliberately.
        self.blocked = set(DESTRUCTIVE)
        self._load_pgm(vendor + "/pgm.dll")

    # --- loading -----------------------------------------------------------------
    def va(self, orig):
        return orig - PGM_ORIG + PGM_BASE

    def _load_pgm(self, path):
        pe = self.pe = pefile.PE(path)
        pe.relocate_image(PGM_BASE)
        uc = self.h.uc
        size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        uc.mem_map(PGM_BASE, size)
        uc.mem_write(PGM_BASE, bytes(pe.get_memory_mapped_image()))
        uc.mem_map(PGM_STUBS, 0x10000)
        uc.mem_write(PGM_STUBS, b"\xc3" * 0x10000)
        self.imports = {}
        n = 0
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            dll = entry.dll.decode().lower()
            for imp in entry.imports:
                addr = PGM_STUBS + 0x100 + 4 * n
                n += 1
                name = imp.name.decode() if imp.name else f"ord{imp.ordinal}"
                self.imports[addr] = (dll, name)
                uc.mem_write(imp.address - PGM_ORIG + PGM_BASE if imp.address < PGM_BASE else imp.address,
                             struct.pack("<I", addr))
        # hardware function table -> thunks that log, then jump to the module's exports
        self.thunks = {}
        for off, name in SLOTS.items():
            taddr = PGM_STUBS + 0x8000 + off
            self.thunks[taddr] = name
            uc.mem_write(self.va(SLOT_BASE + off), struct.pack("<I", taddr))
        uc.hook_add(UC_HOOK_CODE, self._stub, begin=PGM_STUBS, end=PGM_STUBS + 0xFFFF)
        # log pgm.dll's own debug messages and numbered user messages as they are issued
        uc.hook_add(UC_HOOK_CODE, self._dbg, begin=self.va(0x1000506A), end=self.va(0x1000506A))
        uc.hook_add(UC_HOOK_CODE, self._msgid, begin=self.va(0x10004EA8), end=self.va(0x10004EA8))
        # pgm.dll's yes/no prompt (0x1000500d): answered from self.prompt_answer (None = let
        # pgm.dll's own code run, which with the GUI stubbed returns "no")
        uc.hook_add(UC_HOOK_CODE, self._prompt, begin=self.va(0x1000500D), end=self.va(0x1000500D))
        # pgm.dll's "current device" pointer normally set by its init code (0x10005c08)
        uc.mem_write(self.va(0x1006FC24), struct.pack("<I", self.va(0x1006F790)))

    # --- helpers -----------------------------------------------------------------
    def u32(self, a): return self.h.u32(a)
    def cstr(self, a): return self.h.cstr(a)
    def log(self, msg):
        self.messages.append(msg)

    def _stub(self, uc, addr, size, user):
        if addr in self.thunks:                       # pgm -> module call
            name = self.thunks[addr]
            esp = uc.reg_read(UC_X86_REG_ESP)
            args = [self.u32(esp + 4 + 4 * i) for i in range(6)]
            self.module_calls.append((name, args))
            if name in self.blocked:                  # dry run: report, do not touch the chip
                self.log("BLOCKED pgd_%s(%s)" % (name, ", ".join("%#x" % a for a in args)))
                uc.reg_write(UC_X86_REG_EAX, 1)
                uc.reg_write(UC_X86_REG_EIP, self.u32(esp))
                uc.reg_write(UC_X86_REG_ESP, esp + 4)
                return
            self.log("MODULE pgd_%s(%s)" % (name, ", ".join("%#x" % a for a in args)))
            ret = self.u32(esp)                       # log the module's return value too
            idx = len(self.messages) - 1
            def on_return(uc2, a2, s2, u2, idx=idx, box=[]):
                if not box:
                    box.append(1)
                    self.messages[idx] += " -> %#x" % uc2.reg_read(UC_X86_REG_EAX)
            if ret not in self._ret_hooks:
                self._ret_hooks[ret] = []
                uc.hook_add(UC_HOOK_CODE, lambda u, a, sz, d, r=ret: self._ret_hooks[r].pop(0)(u, a, sz, d) if self._ret_hooks[r] else None, begin=ret, end=ret)
            self._ret_hooks[ret].append(on_return)
            uc.reg_write(UC_X86_REG_EIP, self.h.exports["pgd_" + name])
            return
        if addr not in self.imports:
            return
        dll, name = self.imports[addr]
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = self.u32(esp)
        a = lambda i: self.u32(esp + 4 + 4 * i)
        fn = getattr(self, "imp_" + name, None) or getattr(self.h, "imp_" + name, None)
        if fn is not None:
            val = fn(a)
        else:
            if dll in SYSTEM_DLLS and name not in STDCALL:
                raise RuntimeError(f"unhandled system import {dll}!{name} called from {ret:#x}")
            self.log(f"stub {dll}!{name}({a(0):#x}, {a(1):#x}, {a(2):#x})")
            val = 0
        pop = STDCALL.get(name, 0) if dll in SYSTEM_DLLS else 0
        uc.reg_write(UC_X86_REG_EAX, (val or 0) & 0xFFFFFFFF)
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + 4 * pop)
        uc.reg_write(UC_X86_REG_EIP, ret)

    def _dbg(self, uc, addr, size, user):
        esp = uc.reg_read(UC_X86_REG_ESP)
        fmt = self.cstr(self.u32(esp + 4))
        args = [self.u32(esp + 8 + 4 * i) for i in range(fmt.count("%"))]
        try:
            vals = tuple(self.cstr(a) if spec == "s" else a
                         for a, spec in zip(args, __import__("re").findall(r"%[-0-9.l]*([a-zA-Z])", fmt)))
            text = fmt % vals
        except Exception:
            text = fmt + " " + str([hex(a) for a in args])
        self.log("PGM: " + text)

    prompt_answer = None

    def _prompt(self, uc, addr, size, user):
        esp = uc.reg_read(UC_X86_REG_ESP)
        self.log("PGM PROMPT id %#x -> %s" % (self.u32(esp + 4), self.prompt_answer))
        if self.prompt_answer is not None:
            uc.reg_write(UC_X86_REG_EAX, self.prompt_answer)
            uc.reg_write(UC_X86_REG_EIP, self.u32(esp))
            uc.reg_write(UC_X86_REG_ESP, esp + 4)

    def _msgid(self, uc, addr, size, user):
        esp = uc.reg_read(UC_X86_REG_ESP)
        self.log("PGM message id %#x" % self.u32(esp + 4))

    # --- imports that matter -----------------------------------------------------
    def imp_ge_mode_pcf_part_name(self, a):
        return self.h.put(pathlib.Path(self.pcf_path).stem.encode())
    def imp_ge_get_file_ext(self, a):
        return self.h.put(b"pcf")
    def imp_ge_get_exe_path(self, a):
        self.h.uc.mem_write(a(0), self.pcf_path.encode() + b"\0")
        return 0
    def imp_ge_file_size(self, a):
        return pathlib.Path(self.cstr(a(0))).stat().st_size
    def imp_ge_fread_header(self, a):
        self.h.files[a(0)].read(8)                  # 'PCF\0' + version
        return 0
    def imp_ge_mode_generic_part_type(self, a):
        return self.part_id
    def imp_ge_get_cfg(self, a):
        self.log("ge_get_cfg(%r)" % self.cstr(a(0)))
        return 0
    def imp_ge_get_app_cfg(self, a): return 0
    def imp_ge_alloc(self, a): return self.h.alloc(a(0))
    def imp_ge_alloc_size(self, a): return self.h.allocs.get(a(0), 0)
    def imp_ge_free_alloc_ptr(self, a): return 0
    def imp_ge_realloc(self, a): return self.h.imp_ge_realloc(a)
    def imp_pgm_get_user_abort(self, a): return 0
    def imp_fseek(self, a):
        self.h.files[a(0)].seek(a(1) if a(1) < 0x80000000 else a(1) - (1 << 32), a(2)); return 0
    def imp_strncpy(self, a):
        s = bytes(self.h.uc.mem_read(a(1), a(2))); s = s.split(b"\0")[0]
        self.h.uc.mem_write(a(0), s.ljust(a(2), b"\0")); return a(0)
    def imp__strupr(self, a):
        s = self.cstr(a(0)).upper(); self.h.uc.mem_write(a(0), s.encode() + b"\0"); return a(0)
    def imp_strrchr(self, a):
        s = self.cstr(a(0)); i = s.rfind(chr(a(1) & 0xFF)); return a(0) + i if i >= 0 else 0
    def imp_strchr(self, a):
        s = self.cstr(a(0)); i = s.find(chr(a(1) & 0xFF)); return a(0) + i if i >= 0 else 0
    def imp_atoi(self, a):
        try: return int(self.cstr(a(0)).strip() or 0)
        except ValueError: return 0
    def imp_sprintf(self, a):
        self.h.uc.mem_write(a(0), self.cstr(a(1)).encode() + b"\0"); return 0
    def imp_vsprintf(self, a):
        self.h.uc.mem_write(a(0), self.cstr(a(1)).encode() + b"\0"); return 0
    def imp_msg_vmsg(self, a):
        self.log("msg_vmsg(%#x)" % a(0)); return 0
    def imp_msg_msg(self, a):
        self.log("msg_msg(%#x)" % a(0)); return 0

    # --- pgm.dll state -----------------------------------------------------------
    def g(self, orig):                       # read a pgm.dll global by its original address
        return self.u32(self.va(orig))

    def setg(self, orig, value):
        self.h.uc.mem_write(self.va(orig), struct.pack("<I", value & 0xFFFFFFFF))

    def load_device(self, variant=0):
        """Run pgm.dll's own control-file loader and variant selection.

        pgm_load_control_file (0x10031078) reads the whole .pcf (one block per variant) into
        structures hung off the device record; pgm_generate_tree (0x100324ce) then copies two
        pointers into globals and calls the variant selector (0x100320bb), which sets the
        voltage record (0x1006f588), row table (0x1006fc2c, count 0x1006e474) and the rest.
        Only those two pointer copies are done by hand here.
        """
        self.describe_hardware()
        if self.call(0x10031078) != 1:
            raise RuntimeError("pgm_load_control_file failed")
        dev = self.g(0x1006FC24)
        self.setg(0x1006FC28, self.u32(dev + 0x47C))
        self.setg(0x1006FD20, self.u32(dev + 0x438))
        if self.call(0x100320BB, variant) != 1:
            raise RuntimeError("variant selection failed")
        return dev

    def describe_hardware(self):
        """What pgm.dll does at 0x1003e224: ask the (already opened) module to describe the
        hardware into pgm.dll's own struct, check it, and copy three fields.
        For the APU the module reports version 0x14, state 1, and (0, 7, 0xc)."""
        info = self.va(0x1006FC38)
        if self.h.call("pgd_setup", 12, 0x44, info) != 1:
            raise RuntimeError("module refused to describe the hardware")
        if self.g(0x1006FC70) != 0x14 or self.g(0x1006FC48) not in (1, 2):
            raise RuntimeError("unexpected hardware description")
        for dst, src in ((0x1006E498, 0x1006FC4C), (0x1006E49C, 0x1006FC50), (0x1006E4A0, 0x1006FC54)):
            self.setg(dst, self.g(src))
        self.setg(0x1006E35C, 1)                    # "hardware present"
        return self.g(0x1006E498), self.g(0x1006E49C), self.g(0x1006E4A0)

    def row_table(self):
        base, n = self.g(0x1006FC2C), self.g(0x1006E474)
        rows = []
        for i in range(n):
            start, count, _, flag, p1, _, p2 = struct.unpack("<7I", self.h.uc.mem_read(base + 0x1C * i, 0x1C))
            rows.append(dict(index=i, start=start, count=count, flag=flag))
        return rows

    # --- calling pgm.dll ---------------------------------------------------------
    def call(self, orig_addr, *args):
        return self.h.call(self.va(orig_addr), *args)


if __name__ == "__main__":
    host = Host(sys.argv[1] if len(sys.argv) > 1 else "EPM7064LC84-15")
    dev = host.load_device()
    rows = host.row_table()
    rec = host.g(0x1006F588)
    print("part type %d, %d variants; %d rows, %d flagged for reading" % (
        host.u32(dev + 0x44C), host.u32(dev + 0x24), len(rows), sum(1 for r in rows if r["flag"])))
    print("voltage record:", struct.unpack("<9H", host.h.uc.mem_read(rec, 18)))
