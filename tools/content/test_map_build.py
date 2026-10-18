"""map_build.py: its file formats on their own, and (with the player's
codegen image in OPENSPM_CODEGEN_DOL and `pip install unicorn`) the game's
own code reading them: the LZ decompressor, the ARC library, mapLoad from
the archive to the built map objects, and the collision ray test against the
level's floor."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import map_build as mb  # noqa: E402

FLOOR = (-500.0, 500.0, -50.0, 0.0, -100.0, 100.0)


def checker(name: str, w: int = 16, h: int = 16) -> mb.Texture:
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes((200, 160, 90, 255) if (x // 4 + y // 4) % 2 else (120, 90, 50, 255))
    return mb.Texture(name, w, h, bytes(px))


def sample_model() -> mb.MapModel:
    strips = mb.box_faces(*FLOOR)
    return mb.MapModel(
        draw_root=mb.Joint("A", children=[mb.Joint("floor", meshes=[mb.Mesh("m_ground", strips)], cull=3)]),
        hit_root=mb.Joint("S", children=[mb.Joint("h_floor", meshes=[mb.Mesh(None, strips)])]),
        materials=[mb.Material("m_ground", texture="ground")],
        textures=[checker("ground")],
    )


def test_lz10_roundtrip() -> None:
    for data in (b"", b"a", b"abcabcabcabcabcabcabc" * 50, bytes(range(256)) * 9, os.urandom(3000)):
        packed = mb.lz10_compress(data)
        assert packed[0] == 0x10 and struct.unpack("<I", packed[:4])[0] >> 8 == len(data)
        assert mb.lz10_decompress(packed) == data
    assert len(mb.lz10_compress(b"x" * 4000)) < 600  # repeats do compress (18-byte matches)


def test_u8_layout() -> None:
    arc = mb.build_u8({"dvd/map/os1_01/map.dat": b"M" * 40, "dvd/map/os1_01/texture.tpl": b"T" * 7})
    magic, root, header_size, data_offset = struct.unpack(">IIII", arc[:16])
    assert magic == 0x55AA382D and root == 0x20 and data_offset % 32 == 0
    count = struct.unpack(">I", arc[0x28:0x2C])[0]  # root directory: index of the end
    nodes = [struct.unpack(">III", arc[0x20 + 12 * i:0x2C + 12 * i]) for i in range(count)]
    names_at = 0x20 + 12 * count

    def name(n):
        o = names_at + (n[0] & 0xFFFFFF)
        return arc[o:arc.index(b"\0", o)].decode()

    assert [name(n) for n in nodes] == ["", "dvd", "map", "os1_01", "map.dat", "texture.tpl"]
    assert [n[0] >> 24 for n in nodes] == [1, 1, 1, 1, 0, 0]
    _, off, size = nodes[4]
    assert arc[off:off + size] == b"M" * 40 and off % 32 == 0


def test_tpl_rgba8() -> None:
    t = checker("t", 8, 4)
    tpl = mb.build_tpl([t])
    magic, count, table = struct.unpack(">III", tpl[:12])
    header, palette = struct.unpack(">II", tpl[table:table + 8])
    height, width, fmt, data = struct.unpack(">HHII", tpl[header:header + 12])
    assert (magic, count, height, width, fmt, palette) == (mb.TPL_MAGIC, 1, 4, 8, mb.GX_TF_RGBA8, 0)
    assert data % 32 == 0
    # First 4x4 tile: 16 (A, R) pairs then 16 (G, B) pairs; pixel (1, 0) is
    # the second pair of each.
    px = t.rgba[4:8]
    assert tpl[data + 2:data + 4] == bytes((px[3], px[0])) and tpl[data + 34:data + 36] == px[1:3]


def test_map_dat_structure() -> None:
    dat = mb.build_map_dat(sample_model())
    size, main, nrel, nchunk = struct.unpack(">IIII", dat[:16])
    assert size == len(dat) and size % 32 == 0
    data = dat[0x20:0x20 + main]
    rel = struct.unpack(f">{nrel}I", dat[0x20 + main:0x20 + main + 4 * nrel])
    for at in rel:  # every pointer field points into the data
        assert struct.unpack(">I", data[at:at + 4])[0] < main
    chunk_at = 0x20 + main + 4 * nrel
    names_at = chunk_at + 8 * nchunk
    chunks = {}
    for i in range(nchunk):
        off, noff = struct.unpack(">II", dat[chunk_at + 8 * i:chunk_at + 8 * i + 8])
        chunks[dat[names_at + noff:dat.index(b"\0", names_at + noff)].decode()] = off
    assert {"information", "vcd_table", "material_name_table", "texture_table"} <= set(chunks)
    info = chunks["information"]
    version = struct.unpack(">I", data[info:info + 4])[0]
    assert data[version:version + 8] == b"ver1.02\0"
    # Positions are stored at 1/10 (the game scales maps by 10): the floor's
    # 500-unit half-width is 50 in the file.
    vcd = chunks["vcd_table"]
    pos, shift = struct.unpack(">I", data[vcd:vcd + 4])[0], struct.unpack(">I", data[vcd + 0x44:vcd + 0x48])[0]
    values = [struct.unpack(">h", data[pos + 4 + 2 * i:pos + 6 + 2 * i])[0] / (1 << shift)
              for i in range(3 * struct.unpack(">I", data[pos:pos + 4])[0])]
    assert max(values) == 50.0 and min(values) == -50.0


# ---- the game's own code (optional) ---------------------------------------

def _harness():
    path = os.environ.get("OPENSPM_CODEGEN_DOL")
    if not path:
        return None
    import game_harness as gh
    if not gh.available():
        return None
    return gh.Harness.from_file(path)


def test_game_reads_archive() -> None:
    h = _harness()
    if h is None:
        print("skip test_game_reads_archive (OPENSPM_CODEGEN_DOL / unicorn)")
        return
    model = sample_model()
    dat, tpl = mb.build_map_dat(model), mb.build_tpl(model.textures)
    arc = mb.build_map_archive("os1_01", dat, tpl)
    raw = mb.lz10_decompress(arc)
    src, dst = h.put(arc), h.alloc(len(raw))
    h.call(0x802E0528, src, dst)  # CXUncompressLZ
    assert h.read(dst, len(raw)) == raw
    handle, info = h.alloc(0x40), h.alloc(0x20)
    h.call(0x802E06C4, dst, handle)  # ARCInitHandle
    for path, want in (("./dvd/map/os1_01/map.dat", dat), ("./dvd/map/os1_01/texture.tpl", tpl)):
        h.call(0x802E0764, handle, h.put_str(path), info)  # ARCOpen
        start, length = h.call(0x802E0E84, info), h.call(0x802E0E98, info)
        assert h.read(start, length) == want
    assert h.call(0x802E0A58, handle, h.put_str("./dvd/map/os1_01/camera_road.bin")) == 0xFFFFFFFF


def test_game_loads_map_and_collides() -> None:
    h = _harness()
    if h is None:
        print("skip test_game_loads_map_and_collides (OPENSPM_CODEGEN_DOL / unicorn)")
        return
    model = sample_model()
    h.serve_files({"map/os1_01.bin": mb.build_map_archive("os1_01", mb.build_map_dat(model), mb.build_tpl(model.textures))})
    name = h.put_str("os1_01")
    for _ in range(4):
        if h.call(0x800816E8, name) == 1:  # mapLoad, one stage per call
            break
    else:
        raise AssertionError("mapLoad did not finish")
    work = h.u32(0x80574900 - 32528)
    group = work + h.u32(work + 1688) * 844
    assert h.u32(group + 384) == 2 and h.u32(group + 392) == 2  # MapObjs (A, floor), HitObjs (S, h_floor)
    h.call(0x8006F598)  # this frame's list of hit objects
    out = h.alloc(28)

    def ray(origin, direction):
        h.wf32(out + 12, 1000.0)
        hit = h.call(0x8006ECB4, 0, out, out + 4, out + 8, out + 12, out + 16, out + 20, out + 24,
                     fargs=tuple(origin) + tuple(direction))
        return hit and (round(h.f32(out), 1), round(h.f32(out + 4), 1), round(h.f32(out + 8), 1),
                        round(h.f32(out + 20), 1))

    assert ray((0, 100, 0), (0, -1, 0)) == (0.0, 0.0, 0.0, 1.0)     # the top: normal up
    assert ray((600, 100, 0), (0, -1, 0)) == 0                       # past the end
    assert ray((0, 100, 150), (0, -1, 0)) == 0                       # behind it
    assert ray((-700, -25, 0), (1, 0, 0)) == (-500.0, -25.0, 0.0, -0.0)  # its left side
    assert ray((0, -100, 0), (0, 1, 0)) == (0.0, -50.0, 0.0, -1.0)  # its bottom: normal down


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
