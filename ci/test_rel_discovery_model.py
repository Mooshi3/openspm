#!/usr/bin/env python3
from dataclasses import dataclass

RAM_BASE = 0x80000000
RAM_SIZE = 24 * 1024 * 1024

@dataclass
class Meta:
    module_id: int = 1
    version: int = 3
    section_count: int = 18
    section_info_offset: int = 0x4C
    file_size: int = 0x31BEC8
    exec_index: int = 1
    linked_start: int = 0x80C0401C
    exec_size: int = 0x63D88

class Discovery:
    def __init__(self, mem: bytearray, meta=Meta()):
        self.mem = mem
        self.meta = meta
        self.scans = 0
        self.candidates = 0
        self.active = []
        self.dirty = True

    def invalidate(self):
        self.dirty = True

    def be32(self, off):
        return int.from_bytes(self.mem[off:off+4], 'big')

    def refresh(self, runtime_hint):
        m = self.meta
        if not self.dirty:
            return self.active
        if not (RAM_BASE <= runtime_hint < RAM_BASE + len(self.mem)):
            return self.active
        self.scans += 1
        table_end = m.section_info_offset + m.section_count * 8
        hint_off = runtime_hint - RAM_BASE
        begin = max(0, hint_off - m.file_size)
        end = min(hint_off, len(self.mem) - table_end)
        begin = (begin + 3) & ~3
        found = []
        for candidate in range(begin, end + 1, 4):
            self.candidates += 1
            if self.be32(candidate) != m.module_id:
                continue
            if self.be32(candidate + 0x0C) != m.section_count:
                continue
            si = self.be32(candidate + 0x10)
            linked_si = RAM_BASE + candidate + m.section_info_offset
            if si not in (m.section_info_offset, linked_si):
                continue
            if self.be32(candidate + 0x1C) != m.version:
                continue
            e = candidate + m.section_info_offset + m.exec_index * 8
            start = self.be32(e) & ~1
            size = self.be32(e + 4)
            if size != m.exec_size or not start:
                continue
            if start < m.file_size:
                start += RAM_BASE + candidate
            elif start < len(self.mem):
                start |= RAM_BASE
            if not (RAM_BASE <= start and start - RAM_BASE + size <= len(self.mem)):
                continue
            found = [start]
            break
        self.active = found
        self.dirty = False
        return found

def install(mem, candidate, linked_header=False, section_file_offset=0xDC):
    m = Meta()
    def w(off, value):
        mem[off:off+4] = int(value).to_bytes(4, 'big')
    w(candidate, m.module_id)
    w(candidate + 0x0C, m.section_count)
    w(candidate + 0x10,
      RAM_BASE + candidate + m.section_info_offset if linked_header else m.section_info_offset)
    w(candidate + 0x1C, m.version)
    e = candidate + m.section_info_offset + m.exec_index * 8
    w(e, section_file_offset | 1)
    w(e + 4, m.exec_size)

mem = bytearray(RAM_SIZE)
d = Discovery(mem)
candidate = 0x00C03F40
install(mem, candidate, linked_header=False)
prolog = 0x80C4205C
assert d.refresh(prolog) == [0x80C0401C]
assert d.scans == 1
assert d.candidates <= Meta().file_size // 4 + 2
assert d.candidates < RAM_SIZE // 4

mem2 = bytearray(RAM_SIZE)
install(mem2, candidate, linked_header=True)
d2 = Discovery(mem2)
assert d2.refresh(prolog) == [0x80C0401C]
assert d2.scans == 1

mem3 = bytearray(RAM_SIZE)
alt_candidate = 0x00600000
install(mem3, alt_candidate, linked_header=True)
d3 = Discovery(mem3)
alt_pc = RAM_BASE + alt_candidate + 0x200
assert d3.refresh(alt_pc) == [RAM_BASE + alt_candidate + 0xDC]
assert d3.scans == 1

# A failed scan is memoized until executable code is invalidated. That keeps
# DOL fallback instructions from repeatedly rescanning memory before the REL
# exists, but still detects a later OSLink load.
mem4 = bytearray(RAM_SIZE)
d4 = Discovery(mem4)
assert d4.refresh(prolog) == []
assert d4.scans == 1
assert d4.refresh(prolog + 0x100) == []
assert d4.scans == 1
d4.invalidate()
install(mem4, candidate, linked_header=True)
assert d4.refresh(prolog) == [0x80C0401C]
assert d4.scans == 2

print(f'PASS: targeted REL discovery; scans={d.scans}; candidates={d.candidates}; raw+OSLink sectionInfo accepted; allocation-independent runtime hint; invalidation-gated retry')
