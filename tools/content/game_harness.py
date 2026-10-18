#!/usr/bin/env python3
"""Run the game's own code on OpenSPM-made data, on a PC, for tests.

A small PowerPC machine (Unicorn) with the player's codegen image (main.dol
+ relF, the same file tools/content/character_ids.py reads) loaded at its
real addresses. Tests call game functions directly (``h.call(addr, ...)``)
with OpenSPM-made data in memory: a map file through the game's map loader,
a setup file through its reader, an NPC tribe through npcEntry... The
image is never part of OpenSPM; tests that need it skip without it.

What the machine has:

  * MEM1 (24 MiB at 0x80000000, also at 0xC0000000 uncached) and MEM2
    (64 MiB at 0x90000000), hardware registers as plain memory;
  * the Gekko-only paired-single instructions, emulated (Unicorn's PowerPC
    has no paired singles): each FPR's ps0 is Unicorn's FPR, ps1 a shadow.
    Gekko's ordinary FP instructions either fill both halves (lfs, single
    arithmetic, frsp) or set ps0 and keep ps1 (lfd, fmr, double
    arithmetic). Every ordinary write a paired-single read can see (the
    nearest one before it in the same block) and every lfd restoring a
    register next to a psq_l is hooked with its kind; any other ordinary
    write is taken as a fill when it changes ps0;
  * the write-gather pipe (0xCC008000): what the CPU writes there goes to
    the open display list (GXBeginDisplayList / GXEndDisplayList are
    replaced), so a test sees the display lists the game builds;
  * replaced functions (``stub``): asserts and OSPanic raise
    ``GameAssert`` with the game's message, OSReport prints, the heap
    allocator hands out MEM2 from a bump region, and anything a test adds.

Optional dependency: ``pip install unicorn`` (tests skip without it).
"""

from __future__ import annotations

import ctypes
import math
import struct
from pathlib import Path
from typing import Callable

try:
    from unicorn import UC_ARCH_PPC, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_UNMAPPED
    from unicorn import UC_MODE_BIG_ENDIAN, UC_MODE_PPC32, UC_PROT_ALL, Uc, UcError
    from unicorn import ppc_const as P
except ImportError:  # pragma: no cover - optional
    Uc = None  # type: ignore[assignment]

MEM1_BASE, MEM1_SIZE = 0x80000000, 0x01800000
MEM2_BASE, MEM2_SIZE = 0x90000000, 0x04000000
RETURN = 0x80000400          # LR sentinel: a call ends when execution reaches it
HEAP_BEGIN, HEAP_END = 0x92000000, 0x93800000  # the harness allocator's MEM2 region
WGPIPE = 0xCC008000

# R8PE01 rev 2 (facts from the game's code)
SDA2_BASE = 0x80575C60       # r2 (__init_registers 0x8000630C)
SDA_BASE = 0x80574900        # r13
FN_ASSERT = 0x8019BB0C       # __assert(file, line, expr)
FN_ASSERT2 = 0x8019BB34      # __assert2(file, line, expr, fmt, ...)
FN_OSPANIC = 0x8019D924      # OSPanic(file, line, fmt, ...)
FN_OSREPORT = 0x8026EBB0
FN_MEM_ALLOC = 0x801A59A8    # __memAlloc(heap, size)
FN_MEM_FREE = 0x801A5A2C     # __memFree(heap, ptr)
FN_GX_BEGIN_DL = 0x80291DC8  # GXBeginDisplayList(buffer, size)
FN_GX_END_DL = 0x80291E78    # GXEndDisplayList() -> size
FN_GPU_WAIT = 0x8019CA98     # the game's wait for the GPU (GXSetDrawSync, then poll the token)
FN_DVD_PATH_TO_ENTRYNUM = 0x80279860  # DVDConvertPathToEntrynum(path) -> entry or -1
FN_FILE_ASYNCF = 0x8019F244  # fileAsyncf(type, x, fmt, ...) -> 0 busy / ready / -1 absent
FN_FILE_ALLOCF = 0x8019ED0C  # fileAllocf(type, fmt, ...) -> record (+0xA0 size, +0xA4 -> -> data)
FN_FILE_FREE = 0x8019F074


class GameAssert(Exception):
    """The game's own assert / OSPanic fired."""


class HarnessError(Exception):
    pass


def available() -> bool:
    return Uc is not None


def _f32(x: float) -> float:
    """Round to single precision (overflow -> infinity, as the FPU does)."""
    if not math.isfinite(x):
        return x
    if abs(x) > 3.4028234663852886e38:
        return math.copysign(math.inf, x)
    return struct.unpack(">f", struct.pack(">f", x))[0]


def _bits(x: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def _dbl(bits: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", bits & 0xFFFFFFFFFFFFFFFF))[0]


def _fp_fill_dest(w: int) -> int | None:
    """FPR an ordinary instruction writes into both halves: lfs(u)(x),
    single-precision arithmetic, frsp."""
    op = w >> 26
    if op in (48, 49) or op == 59 or (op == 31 and (w >> 1) & 1023 in (535, 567)) or (op == 63 and (w >> 1) & 1023 == 12):
        return (w >> 21) & 31
    return None


def _fp_keep_dest(w: int) -> int | None:
    """FPR an ordinary instruction writes into ps0 only: lfd(u)(x), fmr,
    fneg, fabs, fnabs, double arithmetic, fctiw, mffs."""
    op = w >> 26
    if op in (50, 51) or (op == 31 and (w >> 1) & 1023 in (599, 631)):
        return (w >> 21) & 31
    if op == 63 and (w >> 1) & 1023 not in (0, 32, 64, 38, 70, 134, 711, 12):
        return (w >> 21) & 31
    return None


def _ps_dest(w: int) -> int | None:
    op = w >> 26
    if op in (56, 57):
        return (w >> 21) & 31
    if op == 4 and (w >> 1) & 63 not in (7, 39) and (w >> 1) & 1023 not in (0, 32, 64, 96, 1014):
        return (w >> 21) & 31
    return None


def _ps_ps1_reads(w: int) -> list[int]:
    op = w >> 26
    if op in (60, 61):  # psq_st(u): frS unless W (ps0 only)
        return [] if (w >> 15) & 1 else [(w >> 21) & 31]
    if op != 4:
        return []
    xo6 = (w >> 1) & 63
    if xo6 in (7, 39):
        return [] if (w >> 10) & 1 else [(w >> 21) & 31]
    if xo6 in (6, 38) or (w >> 1) & 1023 == 1014:
        return []
    return sorted({(w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31})


def _keep_after(w: int):
    """How to know, before it runs, the ps0 bits a keep write leaves (None:
    cannot tell)."""
    op, d, a, b = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    if op in (50, 51):
        disp = w & 0xFFFF
        disp -= 0x10000 if disp & 0x8000 else 0
        return lambda h: struct.unpack(">Q", h.read(((h.r(a) if a or op == 51 else 0) + disp) & 0xFFFFFFFF, 8))[0]
    if op == 31:
        return lambda h: struct.unpack(">Q", h.read(((h.r(a) if a else 0) + h.r(b)) & 0xFFFFFFFF, 8))[0]
    if op == 63:
        x = (w >> 1) & 1023
        fb = lambda h: h.uc.reg_read(P.UC_PPC_REG_FPR0 + b)  # noqa: E731
        if x == 72:  # fmr
            return fb
        if x == 40:  # fneg
            return lambda h: fb(h) ^ (1 << 63)
        if x == 264:  # fabs
            return lambda h: fb(h) & ~(1 << 63)
        if x == 136:  # fnabs
            return lambda h: fb(h) | (1 << 63)
    return lambda h: None


def _scan_fp_writes(image: bytes, offs, addrs, sizes) -> dict[int, tuple[int, object]]:
    """Address -> (FPR, None for a fill or a function for the ps0 bits of a
    keep): the ordinary writes a paired-single read sees (the nearest write
    of the register before the read, in the same block). The lfd that
    restores a register a psq_l just loaded (function epilogues) is handled
    by the psq_l itself (Harness._epilogue_lfd)."""
    words: dict[int, int] = {}
    for o, a, s in list(zip(offs, addrs, sizes))[:7]:  # the text sections
        if s:
            for i in range(0, s - 3, 4):
                words[a + i] = struct.unpack_from(">I", image, o + i)[0]
    out: dict[int, tuple[int, object]] = {}
    for a, w in words.items():
        regs = _ps_ps1_reads(w)
        for r in regs:
            for i in range(1, 64):
                v = words.get(a - 4 * i)
                if v is None or (v >> 26) in (16, 18, 19) or _ps_dest(v) == r:
                    break
                if _fp_fill_dest(v) == r:
                    out[a - 4 * i] = (r, None)
                    break
                if _fp_keep_dest(v) == r:
                    out[a - 4 * i] = (r, _keep_after(v))
                    break
    return out


class Harness:
    def __init__(self, image: bytes):
        if Uc is None:
            raise HarnessError("unicorn is not installed (pip install unicorn)")
        uc = Uc(UC_ARCH_PPC, UC_MODE_PPC32 | UC_MODE_BIG_ENDIAN)
        self.uc = uc
        self._mem1 = ctypes.create_string_buffer(MEM1_SIZE)
        uc.mem_map_ptr(MEM1_BASE, MEM1_SIZE, UC_PROT_ALL, ctypes.addressof(self._mem1))
        uc.mem_map_ptr(0xC0000000, MEM1_SIZE, UC_PROT_ALL, ctypes.addressof(self._mem1))
        uc.mem_map(MEM2_BASE, MEM2_SIZE)
        uc.mem_map(0xCC000000, 0x10000)
        uc.mem_map(0xCD000000, 0x10000)
        self._load_dol(image)
        uc.mem_write(RETURN, struct.pack(">I", 0x48000000))  # b . (never run: the call stops here)
        self.heap_next = HEAP_BEGIN
        self.ps1: dict[int, float] = {}
        self.ps0_bits: dict[int, int] = {}
        self.ps_keep: dict[int, tuple[int, float]] = {}  # FPR: (ps0 bits after a keep write, ps1)
        self.gqr = [0] * 8
        self.stubs: dict[int, Callable[["Harness"], int | None]] = {}
        self.reports: list[str] = []
        self.dl_open: list | None = None  # [address, capacity, bytearray]
        self.display_lists: list[tuple[int, bytes]] = []
        self.unmapped: list[str] = []
        uc.hook_add(UC_HOOK_INTR, self._on_intr)
        for address in self._gqr_moves:
            uc.hook_add(UC_HOOK_CODE, self._on_gqr_move, begin=address, end=address)
        for address in self._fp_writes:
            uc.hook_add(UC_HOOK_CODE, self._on_fp_write, begin=address, end=address)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._on_wgpipe, begin=WGPIPE, end=WGPIPE + 0x1F)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        self._stub_hook = None
        self.stub(FN_ASSERT, self._assert)
        self.stub(FN_ASSERT2, self._assert2)
        self.stub(FN_OSPANIC, self._panic)
        self.stub(FN_OSREPORT, self._report)
        self.stub(FN_MEM_ALLOC, lambda h: h.alloc(h.r(4)))
        self.stub(FN_MEM_FREE, lambda h: 0)
        self.stub(FN_GX_BEGIN_DL, self._begin_dl)
        self.stub(FN_GX_END_DL, self._end_dl)
        self.stub(FN_GPU_WAIT, lambda h: 0)
        self._prepare_gx()

    @classmethod
    def from_file(cls, path: Path | str) -> "Harness":
        return cls(Path(path).read_bytes())

    # ---- memory -------------------------------------------------------------

    def _load_dol(self, image: bytes) -> None:
        offs = struct.unpack(">18I", image[0:72])
        addrs = struct.unpack(">18I", image[72:144])
        sizes = struct.unpack(">18I", image[144:216])
        self._gqr_moves: dict[int, tuple[int, int, int]] = {}
        for o, a, s in zip(offs, addrs, sizes):
            if s:
                self.uc.mem_write(a, image[o:o + s])
                # mtspr / mfspr GQRn: Unicorn's PowerPC ignores these SPRs, so
                # each one in the image is emulated where it is.
                for i in range(0, s - 3, 4):
                    w = struct.unpack(">I", image[o + i:o + i + 4])[0]
                    if (w & 0xFC0007FE) in (0x7C0003A6, 0x7C0002A6):
                        f = (w >> 11) & 0x3FF
                        spr = ((f & 31) << 5) | (f >> 5)
                        if 912 <= spr <= 919:
                            self._gqr_moves[a + i] = ((w & 0x7FE) == 0x3A6, spr - 912, (w >> 21) & 31)
        self._fp_writes = _scan_fp_writes(image, offs, addrs, sizes)

    def _on_fp_write(self, uc, address, size, user):
        """Before an ordinary FP write a paired-single read can see: a fill
        drops the shadow (ps1 = ps0 after it); a keep records the ps0 the
        write will leave and the ps1 it keeps."""
        hit = self._fp_writes.get(address)
        if hit is None:
            return
        n, after = hit
        self.ps_keep.pop(n, None)
        if after is None:  # fill
            self.ps0_bits.pop(n, None)
            return
        bits = after(self)
        if bits is None:  # cannot tell: take it as a fill
            self.ps0_bits.pop(n, None)
            return
        self.ps_keep[n] = (bits, self._ps1_of(n))

    def _on_gqr_move(self, uc, address, size, user):
        move = self._gqr_moves.get(address)
        if move is None:
            return
        to_gqr, n, reg = move
        if to_gqr:
            self.gqr[n] = self.r(reg)
        else:
            self.set_r(reg, self.gqr[n])
        uc.reg_write(P.UC_PPC_REG_PC, address + 4)

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def write(self, address: int, data: bytes) -> None:
        self.uc.mem_write(address, bytes(data))

    def u8(self, a: int) -> int:
        return self.read(a, 1)[0]

    def u16(self, a: int) -> int:
        return struct.unpack(">H", self.read(a, 2))[0]

    def u32(self, a: int) -> int:
        return struct.unpack(">I", self.read(a, 4))[0]

    def s32(self, a: int) -> int:
        return struct.unpack(">i", self.read(a, 4))[0]

    def f32(self, a: int) -> float:
        return struct.unpack(">f", self.read(a, 4))[0]

    def w32(self, a: int, v: int) -> None:
        self.write(a, struct.pack(">I", v & 0xFFFFFFFF))

    def wf32(self, a: int, v: float) -> None:
        self.write(a, struct.pack(">f", v))

    def cstr(self, a: int, limit: int = 256) -> str:
        if a == 0:
            return ""
        out = bytearray()
        while len(out) < limit:
            b = self.read(a + len(out), 1)[0]
            if b == 0:
                break
            out.append(b)
        return out.decode("latin1")

    def alloc(self, size: int, align: int = 32) -> int:
        a = (self.heap_next + align - 1) & ~(align - 1)
        if a + size > HEAP_END:
            raise HarnessError("harness heap exhausted")
        self.heap_next = a + size
        self.write(a, bytes(size))
        return a

    def put(self, data: bytes, align: int = 32) -> int:
        a = self.alloc(len(data), align)
        self.write(a, data)
        return a

    def put_str(self, s: str) -> int:
        return self.put(s.encode("latin1") + b"\0", 4)

    # ---- registers ----------------------------------------------------------

    def r(self, n: int) -> int:
        return self.uc.reg_read(P.UC_PPC_REG_0 + n) & 0xFFFFFFFF

    def set_r(self, n: int, v: int) -> None:
        self.uc.reg_write(P.UC_PPC_REG_0 + n, v & 0xFFFFFFFF)

    def fr(self, n: int) -> float:
        return _dbl(self.uc.reg_read(P.UC_PPC_REG_FPR0 + n))

    def set_fr(self, n: int, v: float) -> None:
        self.uc.reg_write(P.UC_PPC_REG_FPR0 + n, _bits(v))

    # ---- calls --------------------------------------------------------------

    def stub(self, address: int, fn: Callable[["Harness"], int | None]) -> None:
        """Replace the game function at `address`: fn(h) runs instead, its
        return value (if not None) goes to r3, and the call returns."""
        self.stubs[address] = fn
        self.uc.hook_add(UC_HOOK_CODE, self._on_stub, begin=address, end=address)

    def _on_stub(self, uc, address, size, user):
        fn = self.stubs.get(address)
        if fn is None:
            return
        result = fn(self)
        if result is not None:
            self.set_r(3, result)
        uc.reg_write(P.UC_PPC_REG_PC, uc.reg_read(P.UC_PPC_REG_LR))

    def call(self, address: int, *args: int, fargs: tuple[float, ...] = (), budget: int = 50_000_000) -> int:
        """Call a game function with integer args (r3..) and float args
        (f1..); returns r3. Raises GameAssert on an assert / panic."""
        uc = self.uc
        if len(args) > 8 or len(fargs) > 8:
            raise HarnessError("too many arguments")
        for i, v in enumerate(args):
            self.set_r(3 + i, v)
        for i, v in enumerate(fargs):
            self.set_fr(1 + i, v)
        self.set_r(1, 0x817F0000 - 0x100)   # a stack well clear of loaded data
        self.set_r(2, SDA2_BASE)
        self.set_r(13, SDA_BASE)
        uc.reg_write(P.UC_PPC_REG_LR, RETURN)
        uc.reg_write(P.UC_PPC_REG_MSR, uc.reg_read(P.UC_PPC_REG_MSR) | 0x2000)  # FP on
        try:
            uc.emu_start(address, RETURN, count=budget)
        except UcError as e:
            pc = uc.reg_read(P.UC_PPC_REG_PC)
            raise HarnessError(f"{e} at pc 0x{pc:08X} lr 0x{uc.reg_read(P.UC_PPC_REG_LR):08X}"
                               + (f" ({self.unmapped[-1]})" if self.unmapped else "")) from None
        pc = uc.reg_read(P.UC_PPC_REG_PC)
        if pc != RETURN:
            raise HarnessError(f"call 0x{address:08X} did not return within {budget} instructions (pc 0x{pc:08X})")
        return self.r(3)

    # ---- the game's diagnostics -------------------------------------------

    def _vformat(self, fmt: str, first_gpr: int) -> str:
        out, gpr, fpr, i = [], first_gpr, 1, 0
        while i < len(fmt):
            c = fmt[i]
            if c != "%":
                out.append(c)
                i += 1
                continue
            j = i + 1
            while j < len(fmt) and fmt[j] in "-+ #0123456789.lhz":
                j += 1
            if j >= len(fmt):
                break
            conv, spec = fmt[j], fmt[i:j + 1].replace("l", "").replace("h", "").replace("z", "")
            if conv == "%":
                out.append("%")
            elif conv in "fFeEgG":
                out.append(spec % self.fr(fpr) if fpr <= 8 else "?")
                fpr += 1
            else:
                v = self.r(gpr) if gpr <= 10 else 0
                gpr += 1
                if conv == "s":
                    out.append(self.cstr(v))
                elif conv in "di":
                    out.append(spec % (v - (1 << 32) if v & 0x80000000 else v))
                elif conv in "uxXoc":
                    out.append(spec % v if conv != "c" else chr(v & 0xFF))
                elif conv == "p":
                    out.append(f"0x{v:08X}")
            i = j + 1
        return "".join(out)

    def _assert(self, h) -> None:
        raise GameAssert(f'{self.cstr(self.r(3))}:{self.r(4)}: assertion "{self.cstr(self.r(5))}" failed')

    def _assert2(self, h) -> None:
        msg = self._vformat(self.cstr(self.r(6)), 7)
        raise GameAssert(f'{self.cstr(self.r(3))}:{self.r(4)}: assertion "{self.cstr(self.r(5))}" failed: {msg}')

    def _panic(self, h) -> None:
        raise GameAssert(f"{self.cstr(self.r(3))}:{self.r(4)}: OSPanic: {self._vformat(self.cstr(self.r(5)), 6)}")

    def _report(self, h) -> int:
        self.reports.append(self._vformat(self.cstr(self.r(3)), 4))
        return 0

    def _on_unmapped(self, uc, access, address, size, value, user):
        self.unmapped.append(f"unmapped access 0x{address:08X} size {size}")
        return False

    # ---- files ---------------------------------------------------------------

    def serve_files(self, files: dict[str, bytes]) -> list[str]:
        """Answer the game's file calls from `files` (paths as the game
        formats them, e.g. "./map/os1_01.bin"; a leading "./" is optional):
        DVDConvertPathToEntrynum (-1 when absent), fileAsyncf (ready, or -1
        when absent) and fileAllocf (a file record whose data is `files`'s).
        Returns the list the paths asked for are appended to."""
        asked: list[str] = []
        table = {k.lstrip("./"): v for k, v in files.items()}
        records: dict[str, int] = {}

        def key(path: str) -> str:
            asked.append(path)
            return path.lstrip("./")

        def entrynum(h):
            k = key(h.cstr(h.r(3)))
            return list(table).index(k) if k in table else 0xFFFFFFFF

        def async_(h):
            k = key(h._vformat(h.cstr(h.r(5)), 6))
            return 1 if k in table else 0xFFFFFFFF

        def alloc_(h):
            k = key(h._vformat(h.cstr(h.r(4)), 5))
            if k not in table:
                return 0xFFFFFFFF
            if k not in records:
                data = h.put(table[k])
                cell = h.alloc(4)
                h.w32(cell, data)
                rec = h.alloc(0xC0)
                h.w32(rec + 0xA0, len(table[k]))
                h.w32(rec + 0xA4, cell)
                records[k] = rec
            return records[k]

        self.stub(FN_DVD_PATH_TO_ENTRYNUM, entrynum)
        self.stub(FN_FILE_ASYNCF, async_)
        self.stub(FN_FILE_ALLOCF, alloc_)
        self.stub(FN_FILE_FREE, lambda h: 0)
        return asked

    def _prepare_gx(self) -> None:
        """What GXInit leaves that GX calls rely on: the texture and TLUT
        region callbacks (__GXData +1304 / +1308), here returning a zeroed
        region."""
        gx = self.u32(SDA2_BASE - 480)
        if not MEM1_BASE <= gx < MEM1_BASE + MEM1_SIZE:
            return
        region = self.alloc(0x40)
        for i, slot in enumerate((1304, 1308)):
            fn = 0x80000500 + 16 * i
            self.w32(fn, 0x4E800020)  # blr (the stub runs first)
            self.stub(fn, lambda h, region=region: region)
            if self.u32(gx + slot) == 0:
                self.w32(gx + slot, fn)

    # ---- display lists ------------------------------------------------------

    def _begin_dl(self, h) -> None:
        self.dl_open = [self.r(3), self.r(4), bytearray()]

    def _end_dl(self, h) -> int:
        if self.dl_open is None:
            return 0
        address, capacity, data = self.dl_open
        while len(data) % 32:
            data.append(0)  # GXEndDisplayList pads with NOPs to 32 bytes
        if len(data) > capacity:
            raise HarnessError(f"display list overflow ({len(data)} > {capacity})")
        self.write(address, bytes(data))
        self.display_lists.append((address, bytes(data)))
        self.dl_open = None
        return len(data)

    def _on_wgpipe(self, uc, access, address, size, value, user):
        if self.dl_open is not None:
            self.dl_open[2] += (value & ((1 << (8 * size)) - 1)).to_bytes(size, "big")

    # ---- paired singles -----------------------------------------------------

    def _ps1_of(self, n: int) -> float:
        bits = self.uc.reg_read(P.UC_PPC_REG_FPR0 + n)
        keep = self.ps_keep.get(n)
        if keep is not None:
            if keep[0] == bits:  # set by a keep write since: ps1 as it was
                return keep[1]
            del self.ps_keep[n]
        if self.ps0_bits.get(n) != bits:  # written by an ordinary FP instruction since
            return _dbl(bits)
        return self.ps1.get(n, _dbl(bits))

    def _set_ps(self, n: int, p0: float, p1: float) -> None:
        p0, p1 = _f32(p0), _f32(p1)
        bits = _bits(p0)
        self.uc.reg_write(P.UC_PPC_REG_FPR0 + n, bits)
        self.ps0_bits[n] = bits
        self.ps1[n] = p1
        self.ps_keep.pop(n, None)

    def _quant_load(self, address: int, w: int, gqr_index: int) -> tuple[float, float]:
        g = self.gqr[gqr_index]
        ltype, lscale = (g >> 16) & 7, (g >> 24) & 0x3F
        sizes = {0: 4, 4: 1, 5: 2, 6: 1, 7: 2}
        size = sizes.get(ltype, 4)
        scale = 2.0 ** -(lscale - 64 if lscale & 0x20 else lscale)

        def one(a):
            raw = self.read(a, size)
            if ltype == 0:
                return struct.unpack(">f", raw)[0]
            fmt = {4: ">B", 5: ">H", 6: ">b", 7: ">h"}[ltype]
            return struct.unpack(fmt, raw)[0] * scale

        p0 = one(address)
        p1 = 1.0 if w else one(address + size)
        return p0, p1

    def _quant_store(self, address: int, w: int, gqr_index: int, p0: float, p1: float) -> None:
        g = self.gqr[gqr_index]
        stype, sscale = g & 7, (g >> 8) & 0x3F
        sizes = {0: 4, 4: 1, 5: 2, 6: 1, 7: 2}
        size = sizes.get(stype, 4)
        scale = 2.0 ** (sscale - 64 if sscale & 0x20 else sscale)

        def one(a, v):
            if stype == 0:
                self.write(a, struct.pack(">f", _f32(v)))
                return
            if not math.isfinite(v):
                v = 0.0
            lo, hi, fmt = {4: (0, 255, ">B"), 5: (0, 65535, ">H"), 6: (-128, 127, ">b"), 7: (-32768, 32767, ">h")}[stype]
            self.write(a, struct.pack(fmt, max(lo, min(hi, int(v * scale)))))

        one(address, p0)
        if not w:
            one(address + size, p1)

    def _on_intr(self, uc, intno, user):
        pc = uc.reg_read(P.UC_PPC_REG_PC) - 4
        w = struct.unpack(">I", self.read(pc, 4))[0]
        if w == 0x44000002:  # sc (the SDK's cache-flush tail): nothing to do here
            return
        if not self._paired_single(w):
            raise HarnessError(f"unhandled instruction 0x{w:08X} at 0x{pc:08X} (interrupt {intno})")

    def _epilogue_lfd(self, n: int, p1: float) -> None:
        """A function epilogue restores a saved register with psq_l (both
        halves) then lfd (ps0 only, ps1 kept): when an lfd of the register
        follows within three FP loads, its ps0 is known now."""
        pc = self.uc.reg_read(P.UC_PPC_REG_PC)  # the instruction after the psq_l
        for i in range(3):
            v = struct.unpack(">I", self.read(pc + 4 * i, 4))[0]
            op = v >> 26
            if op == 50 and (v >> 21) & 31 == n:
                a, disp = (v >> 16) & 31, v & 0xFFFF
                disp -= 0x10000 if disp & 0x8000 else 0
                ea = ((self.r(a) if a else 0) + disp) & 0xFFFFFFFF
                self.ps_keep[n] = (struct.unpack(">Q", self.read(ea, 8))[0], _f32(p1))
                return
            if op not in (50, 56):  # only lfd / psq_l between
                return

    def _paired_single(self, w: int) -> bool:
        op = w >> 26
        d, a, b = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        c = (w >> 6) & 31
        ra = self.r(a) if a else 0
        if op == 31 and (w >> 1) & 1023 in (467, 339):  # mtspr / mfspr GQRn (912-919), HID2 (920)
            spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
            if 912 <= spr <= 919:
                if (w >> 1) & 1023 == 467:
                    self.gqr[spr - 912] = self.r(d)
                else:
                    self.set_r(d, self.gqr[spr - 912])
                return True
            if spr == 920:
                if (w >> 1) & 1023 == 339:
                    self.set_r(d, 0xA0000000)
                return True
            return False
        if op in (56, 57, 60, 61):  # psq_l, psq_lu, psq_st, psq_stu
            disp = w & 0xFFF
            if disp & 0x800:
                disp -= 0x1000
            wbit, idx = (w >> 15) & 1, (w >> 12) & 7
            ea = (ra + disp) & 0xFFFFFFFF
            if op in (56, 57):
                p0, p1 = self._quant_load(ea, wbit, idx)
                self._set_ps(d, p0, p1)
                self._epilogue_lfd(d, p1)
            else:
                self._quant_store(ea, wbit, idx, self.fr(d), self._ps1_of(d))
            if op in (57, 61):
                self.set_r(a, ea)
            return True
        if op != 4:
            return False
        xo10, xo6, xo5 = (w >> 1) & 1023, (w >> 1) & 63, (w >> 1) & 31
        if xo6 in (6, 7, 38, 39):  # psq_lx, psq_stx, psq_lux, psq_stux
            wbit, idx = (w >> 10) & 1, (w >> 7) & 7
            ea = (ra + self.r(b)) & 0xFFFFFFFF
            if xo6 in (6, 38):
                self._set_ps(d, *self._quant_load(ea, wbit, idx))
            else:
                self._quant_store(ea, wbit, idx, self.fr(d), self._ps1_of(d))
            if xo6 in (38, 39):
                self.set_r(a, ea)
            return True
        A0, A1 = self.fr(a), self._ps1_of(a)
        B0, B1 = self.fr(b), self._ps1_of(b)
        C0, C1 = self.fr(c), self._ps1_of(c)
        five = {
            18: lambda: (A0 / B0 if B0 else math.inf, A1 / B1 if B1 else math.inf),
            20: lambda: (A0 - B0, A1 - B1),
            21: lambda: (A0 + B0, A1 + B1),
            23: lambda: (C0 if A0 >= 0 else B0, C1 if A1 >= 0 else B1),
            24: lambda: (1 / B0 if B0 else math.inf, 1 / B1 if B1 else math.inf),
            25: lambda: (A0 * C0, A1 * C1),
            26: lambda: (1 / math.sqrt(B0) if B0 > 0 else math.inf, 1 / math.sqrt(B1) if B1 > 0 else math.inf),
            28: lambda: (A0 * C0 - B0, A1 * C1 - B1),
            29: lambda: (A0 * C0 + B0, A1 * C1 + B1),
            30: lambda: (-(A0 * C0 - B0), -(A1 * C1 - B1)),
            31: lambda: (-(A0 * C0 + B0), -(A1 * C1 + B1)),
            10: lambda: (A0 + B1, C1),
            11: lambda: (C0, A0 + B1),
            12: lambda: (A0 * C0, A1 * C0),
            13: lambda: (A0 * C1, A1 * C1),
            14: lambda: (A0 * C0 + B0, A1 * C0 + B1),
            15: lambda: (A0 * C1 + B0, A1 * C1 + B1),
        }
        ten = {
            40: lambda: (-B0, -B1),
            72: lambda: (B0, B1),
            136: lambda: (-abs(B0), -abs(B1)),
            264: lambda: (abs(B0), abs(B1)),
            528: lambda: (A0, B0),
            560: lambda: (A0, B1),
            592: lambda: (A1, B0),
            624: lambda: (A1, B1),
        }
        if xo10 in ten:
            self._set_ps(d, *ten[xo10]())
            return True
        if xo10 in (0, 32, 64, 96):  # ps_cmpu0 / cmpo0 / cmpu1 / cmpo1 -> CR field crfD
            x, y = (A0, B0) if xo10 in (0, 32) else (A1, B1)
            bits = 1 if (math.isnan(x) or math.isnan(y)) else (8 if x < y else 4 if x > y else 2)
            field = d >> 2
            cr = self.uc.reg_read(P.UC_PPC_REG_CR)
            shift = 28 - 4 * field
            self.uc.reg_write(P.UC_PPC_REG_CR, (cr & ~(0xF << shift)) | (bits << shift))
            return True
        if xo10 == 1014:  # dcbz_l
            return True
        if xo5 in five:
            self._set_ps(d, *five[xo5]())
            return True
        return False
