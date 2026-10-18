"""Behavioral model for the M11B REL resolver fast path.

This does not emulate RecompCore. It protects the invariants behind the C++ optimization:
- direct DOL addresses bypass REL discovery;
- the last active REL section is a valid locality cache;
- a dirty unresolved address may rediscover once;
- repeated unresolved addresses do not repeatedly whole-RAM scan;
- non-DOL icache invalidation re-arms discovery.
"""

from dataclasses import dataclass


@dataclass
class Section:
    linked: int
    runtime: int
    size: int


class Resolver:
    def __init__(self):
        self.sections: list[Section] = []
        self.last = -1
        self.dirty = True
        self.refreshes = 0
        self.direct_dol = [(0x80004000, 0x80380000)]
        self.pending_sections: list[Section] = []

    def is_direct_dol(self, address: int) -> bool:
        return any(start <= address < end for start, end in self.direct_dol)

    def refresh(self):
        self.refreshes += 1
        self.dirty = False
        if self.sections != self.pending_sections:
            self.sections = list(self.pending_sections)
            self.last = -1

    def resolve_native(self, address: int):
        if 0 <= self.last < len(self.sections):
            sec = self.sections[self.last]
            if sec.runtime <= address < sec.runtime + sec.size:
                return sec.linked + address - sec.runtime

        if self.is_direct_dol(address):
            return address

        for i, sec in enumerate(self.sections):
            if i != self.last and sec.runtime <= address < sec.runtime + sec.size:
                self.last = i
                return sec.linked + address - sec.runtime

        if self.dirty:
            self.refresh()
            for i, sec in enumerate(self.sections):
                if sec.runtime <= address < sec.runtime + sec.size:
                    self.last = i
                    return sec.linked + address - sec.runtime
        return None

    def invalidate(self, address: int):
        if not self.is_direct_dol(address):
            self.dirty = True


r = Resolver()

assert r.resolve_native(0x80010000) == 0x80010000
assert r.refreshes == 0

r.pending_sections = [Section(0x80500000, 0x81200000, 0x10000)]
assert r.resolve_native(0x81200100) == 0x80500100
assert r.refreshes == 1
assert r.last == 0

for off in (0x104, 0x800, 0xFFFC):
    assert r.resolve_native(0x81200000 + off) == 0x80500000 + off
assert r.refreshes == 1

for _ in range(1000):
    assert r.resolve_native(0x81700000) is None
assert r.refreshes == 1

r.invalidate(0x80020000)
assert not r.dirty

r.pending_sections = [Section(0x80500000, 0x81300000, 0x10000)]
r.invalidate(0x81300000)
assert r.dirty
assert r.resolve_native(0x81300020) == 0x80500020
assert r.refreshes == 2

print("M11B REL fast-path model passed")
