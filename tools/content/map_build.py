#!/usr/bin/env python3
"""Build Super Paper Mario map files from OpenSPM level descriptions.

Everything here is written from scratch by OpenSPM: the file formats are
described in docs/CUSTOM-LEVELS.md (each field checked against the game's own
loader, tools/content/game_harness.py runs that loader on these files), and
the geometry, textures and names are the level's own. No game data goes in.

A map is three files the game loads by the map's file name (``mapLoad``):

  * ``map.dat``: the map model. A relocatable file (header, data, a table of
    the data's pointer fields, named chunks) with a joint tree: a root, a
    drawn subtree and a collision ("hit") subtree, whose names the
    ``information`` chunk gives. Joints carry meshes; a mesh is a list of
    triangle strips, each a GX display list of 16-bit indices into the
    ``vcd_table`` arrays (positions s16, normals s8, colours RGBA8, texture
    coordinates s16), with a material (colour, texture, blend);
  * ``texture.tpl``: the textures, in the order of the ``texture_table``;
  * both packed into ``map/<file>.bin``: an LZ-compressed U8 archive with the
    paths ``./dvd/map/<file>/map.dat`` and ``./dvd/map/<file>/texture.tpl``
    (what retail maps are; the game also takes loose ``map/<file>/map.dat``).

``Level`` turns boxes (platforms, walls) into drawn faces and collision, and
named zones (doors, pipes, loading zones) into hit objects the game's door
code finds by name.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# A small linker: blobs, pointer fields, strings


class Linker:
    """Lays out named blobs and resolves pointer fields between them.

    A pointer field is (blob, offset) -> (target blob, target offset); in
    the file it holds the target's offset from the start of the data, and
    its position goes into the relocation table (the game adds the data's
    address to each)."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytearray] = {}
        self.align: dict[str, int] = {}
        self.order: list[str] = []
        self.pointers: list[tuple[str, int, str, int]] = []
        self.strings: dict[str, int] = {}
        self.string_data = bytearray()

    def add(self, name: str, data: bytes | bytearray, align: int = 4) -> str:
        if name in self.blobs:
            raise ValueError(f"duplicate blob {name}")
        self.blobs[name] = bytearray(data)
        self.align[name] = align
        self.order.append(name)
        return name

    def point(self, blob: str, offset: int, target: str, target_offset: int = 0) -> None:
        self.pointers.append((blob, offset, target, target_offset))

    def string(self, blob: str, offset: int, text: str) -> None:
        if text not in self.strings:
            self.strings[text] = len(self.string_data)
            self.string_data += text.encode("ascii") + b"\0"
        self.pointers.append((blob, offset, "$strings", self.strings[text]))

    def link(self) -> tuple[bytes, list[int], dict[str, int]]:
        """Returns (data, pointer field offsets, blob offsets)."""
        if "$strings" not in self.blobs:
            self.add("$strings", self.string_data, 4)
        else:
            self.blobs["$strings"] = bytearray(self.string_data)
        out = bytearray()
        where: dict[str, int] = {}
        for name in self.order:
            a = self.align[name]
            while len(out) % a:
                out.append(0)
            where[name] = len(out)
            out += self.blobs[name]
        while len(out) % 32:
            out.append(0)
        fields = []
        for blob, offset, target, target_offset in self.pointers:
            at = where[blob] + offset
            struct.pack_into(">I", out, at, where[target] + target_offset)
            fields.append(at)
        return bytes(out), sorted(fields), where


# ---------------------------------------------------------------------------
# Map model

GX_TRIANGLESTRIP_VAT0 = 0x98
# The game draws and collides every map with a uniform scale of 10 on its root
# (mapLoad: PSMTXScale with the constant 10.0 at r2-28876): positions in the
# file are world units / 10. Everything this module takes is in world units.
WORLD_SCALE = 10.0
NORMAL_SHIFT = 6  # the game reads normals as s8 with 6 fraction bits
ELEMENT_POSITION, ELEMENT_NORMAL, ELEMENT_COLOR0, ELEMENT_TEX0 = 0x1, 0x2, 0x4, 0x10

BLEND_OPAQUE, BLEND_CLIP, BLEND_TRANSLUCENT = 0, 1, 2


@dataclass
class Texture:
    name: str
    width: int
    height: int
    rgba: bytes  # width * height * 4, rows top to bottom
    wrap_s: int = 1  # GX_REPEAT
    wrap_t: int = 1


@dataclass
class Material:
    name: str
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    texture: str | None = None
    blend: int = BLEND_OPAQUE
    vertex_colors: bool = False
    tev_mode: int = 0
    uv_scale: tuple[float, float] = (1.0, 1.0)


@dataclass
class Vertex:
    pos: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float] = (0.0, 0.0)
    color: tuple[int, int, int, int] = (255, 255, 255, 255)


@dataclass
class Mesh:
    material: str | None  # None: collision only
    strips: list[list[Vertex]]


@dataclass
class Joint:
    name: str
    meshes: list[Mesh] = field(default_factory=list)
    children: list["Joint"] = field(default_factory=list)
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    hit_attr: int = 0  # drawMode +8: collision attributes
    cull: int = 1      # drawMode +1: 1 = back faces culled

    def bbox(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Local bounds in file units (world / WORLD_SCALE)."""
        pts = [tuple(c / WORLD_SCALE for c in v.pos) for m in self.meshes for s in m.strips for v in s]
        if not pts:
            return (-0.1, -0.1, -0.1), (0.1, 0.1, 0.1)
        return (tuple(min(p[i] for p in pts) for i in range(3)),  # type: ignore[return-value]
                tuple(max(p[i] for p in pts) for i in range(3)))


@dataclass
class MapModel:
    draw_root: Joint
    hit_root: Joint
    materials: list[Material]
    textures: list[Texture]
    root_name: str = "world_root"


class _VertexArrays:
    """The vcd_table arrays: each distinct value once, 16-bit indices."""

    def __init__(self) -> None:
        self.data: dict[str, list] = {"pos": [], "nrm": [], "clr": [], "tex": []}
        self.index: dict[str, dict] = {k: {} for k in self.data}

    def put(self, kind: str, value) -> int:
        idx = self.index[kind]
        if value not in idx:
            idx[value] = len(self.data[kind])
            self.data[kind].append(value)
            if len(self.data[kind]) > 0xFFFF:
                raise ValueError(f"too many distinct {kind} values for 16-bit indices")
        return idx[value]


def _shift_for(values: list[tuple[float, ...]]) -> int:
    """The largest fraction-bit count that keeps every value in s16."""
    biggest = max((abs(c) for v in values for c in v), default=0.0)
    shift = 0
    while shift < 15 and biggest * (1 << (shift + 1)) <= 32767:
        shift += 1
    return shift


def _q16(v: float, shift: int) -> int:
    return max(-32768, min(32767, int(round(v * (1 << shift)))))


def _q8(v: float) -> int:
    return max(-128, min(127, int(round(v * (1 << NORMAL_SHIFT)))))


def build_map_dat(model: MapModel) -> bytes:
    L = Linker()
    arrays = _VertexArrays()
    materials = {m.name: m for m in model.materials}
    for m in model.materials:
        if m.texture is not None and m.texture not in {t.name for t in model.textures}:
            raise ValueError(f"material {m.name}: unknown texture {m.texture}")

    # Pass 1: index every vertex attribute (meshes keep the indices).
    def element_mask(mesh: Mesh) -> int:
        mask = ELEMENT_POSITION | ELEMENT_NORMAL
        mat = materials.get(mesh.material) if mesh.material else None
        if mat is not None and mat.vertex_colors:
            mask |= ELEMENT_COLOR0
        if mat is not None and mat.texture is not None:
            mask |= ELEMENT_TEX0
        return mask

    indexed: dict[int, list[list[tuple[int, ...]]]] = {}

    def index_joint(j: Joint) -> None:
        for mesh in j.meshes:
            mask = element_mask(mesh)
            mat = materials.get(mesh.material) if mesh.material else None
            out = []
            for strip in mesh.strips:
                if len(strip) < 3:
                    raise ValueError(f"joint {j.name}: a strip needs 3 or more vertices")
                verts = []
                for v in strip:
                    t = [arrays.put("pos", tuple(c / WORLD_SCALE for c in v.pos))]
                    n = v.normal
                    length = math.sqrt(sum(c * c for c in n)) or 1.0
                    t.append(arrays.put("nrm", tuple(_q8(c / length) for c in n)))
                    if mask & ELEMENT_COLOR0:
                        t.append(arrays.put("clr", v.color))
                    if mask & ELEMENT_TEX0:
                        su, sv = mat.uv_scale if mat else (1.0, 1.0)
                        t.append(arrays.put("tex", (v.uv[0] * su, v.uv[1] * sv)))
                    verts.append(tuple(t))
                out.append(verts)
            indexed[id(mesh)] = out
        for c in j.children:
            index_joint(c)

    index_joint(model.draw_root)
    index_joint(model.hit_root)
    pos_shift = _shift_for(arrays.data["pos"])
    tex_shift = _shift_for(arrays.data["tex"]) if arrays.data["tex"] else 0

    # Vertex data (32-byte aligned arrays, each: u32 count, then the values).
    def array_blob(name: str, values: list, pack) -> str | None:
        if not values:
            return None
        data = bytearray(struct.pack(">I", len(values)))
        for v in values:
            data += pack(v)
        return L.add(name, data, 32)

    pos_blob = array_blob("vtx:pos", arrays.data["pos"], lambda v: struct.pack(">hhh", *(_q16(c, pos_shift) for c in v)))
    nrm_blob = array_blob("vtx:nrm", arrays.data["nrm"], lambda v: struct.pack(">bbb", *v))
    tex_blob = array_blob("vtx:tex", arrays.data["tex"], lambda v: struct.pack(">hh", *(_q16(c, tex_shift) for c in v)))
    clr_blob = array_blob("vtx:clr", arrays.data["clr"], lambda v: struct.pack(">BBBB", *v))

    # Textures and samplers.
    tex_index = {t.name: i for i, t in enumerate(model.textures)}
    for t in model.textures:
        blob = L.add(f"texture:{t.name}", struct.pack(">IIHHI", 0, 0, t.width, t.height, 0))
        L.string(blob, 0, t.name)

    # Materials (0x114 bytes).
    for m in model.materials:
        data = bytearray(0x114)
        struct.pack_into(">BBBB", data, 0x4, *m.color)
        data[0x8] = 1 if m.vertex_colors else 0  # colour source: vertex or material
        data[0xA] = m.blend
        data[0xB] = 1 if m.texture is not None else 0
        for i in range(8):  # texture coordinate transforms: scale 1
            struct.pack_into(">ff", data, 0x2C + i * 0x1C + 0x8, 1.0, 1.0)
        struct.pack_into(">I", data, 0x10C, 0xFFFFFFFF)
        blob = L.add(f"material:{m.name}", data)
        L.string(blob, 0, m.name)
        if m.texture is not None:
            t = model.textures[tex_index[m.texture]]
            sampler = L.add(f"sampler:{m.name}", struct.pack(">IIBBBB", 0, 0, t.wrap_s, t.wrap_t, 0, 0))
            L.point(sampler, 0, f"texture:{m.texture}")
            L.point(blob, 0xC, sampler)
        tev = L.add(f"tev:{m.name}", struct.pack(">III", m.tev_mode << 24, 0, 0))
        L.point(blob, 0x110, tev)

    # Meshes: polygon display lists, then the mesh header.
    vcd_name = "vcd_table"
    mesh_names: dict[int, str] = {}

    def link_meshes(j: Joint) -> None:
        for k, mesh in enumerate(j.meshes):
            mask = element_mask(mesh)
            strips = indexed[id(mesh)]
            poly_names = []
            for s, verts in enumerate(strips):
                dl = bytearray(struct.pack(">BH", GX_TRIANGLESTRIP_VAT0, len(verts)))
                for t in verts:
                    for i in t:
                        dl += struct.pack(">H", i)
                while len(dl) % 32:
                    dl.append(0)  # GX NOPs
                poly_names.append(L.add(f"poly:{j.name}:{k}:{s}", dl, 32))
            head = bytearray(0x10 + 8 * len(strips))
            head[0] = 1
            head[3] = 1  # polygons are display lists
            struct.pack_into(">iI", head, 4, len(strips), mask)
            name = L.add(f"mesh:{j.name}:{k}", head, 32)
            L.point(name, 0xC, vcd_name)
            for s, pn in enumerate(poly_names):
                L.point(name, 0x10 + 8 * s, pn)
                struct.pack_into(">I", L.blobs[name], 0x14 + 8 * s, len(L.blobs[pn]))
            mesh_names[id(mesh)] = name
        for c in j.children:
            link_meshes(c)

    link_meshes(model.draw_root)
    link_meshes(model.hit_root)

    # Joints (pre-order), each with its draw mode.
    root = Joint(model.root_name, children=[model.draw_root, model.hit_root])
    names_seen: set[str] = set()

    def link_joint(j: Joint, parent: str | None, prev: str | None, next_: str | None) -> str:
        if j.name in names_seen:
            raise ValueError(f"duplicate joint name {j.name}")
        names_seen.add(j.name)
        blob = f"joint:{j.name}"
        data = bytearray(0x60 + 8 * len(j.meshes))
        struct.pack_into(">fff", data, 0x18, 1.0, 1.0, 1.0)
        struct.pack_into(">fff", data, 0x30, *(c / WORLD_SCALE for c in j.translation))
        lo, hi = j.bbox()
        struct.pack_into(">ffffff", data, 0x3C, *lo, *hi)
        struct.pack_into(">I", data, 0x5C, len(j.meshes))
        L.add(blob, data)
        L.string(blob, 0, j.name)
        L.string(blob, 4, "mesh" if j.meshes else "null")
        if parent:
            L.point(blob, 0x8, parent)
        if prev:
            L.point(blob, 0x14, prev)
        if parent is not None:  # the root has no draw mode
            mode = bytearray(0x14)
            mode[1] = j.cull
            struct.pack_into(">I", mode, 0x8, j.hit_attr)
            dm = L.add(f"drawmode:{j.name}", mode)
            L.point(blob, 0x58, dm)
        for k, mesh in enumerate(j.meshes):
            if mesh.material is not None:
                L.point(blob, 0x60 + 8 * k, f"material:{mesh.material}")
            L.point(blob, 0x64 + 8 * k, mesh_names[id(mesh)])
        child_blobs = [f"joint:{c.name}" for c in j.children]
        for i, c in enumerate(j.children):
            link_joint(c, blob, child_blobs[i - 1] if i else None,
                       child_blobs[i + 1] if i + 1 < len(j.children) else None)
        if j.children:
            L.point(blob, 0xC, child_blobs[0])
        if next_:
            L.point(blob, 0x10, next_)
        return blob

    root_blob = link_joint(root, None, None, None)

    # vcd_table
    vcd = bytearray(0x68)
    struct.pack_into(">I", vcd, 0x8, 1 if clr_blob else 0)
    struct.pack_into(">I", vcd, 0x14, 1 if tex_blob else 0)
    struct.pack_into(">I", vcd, 0x44, pos_shift)
    struct.pack_into(">I", vcd, 0x48, tex_shift)
    L.add(vcd_name, vcd)
    if pos_blob:
        L.point(vcd_name, 0x0, pos_blob)
    if nrm_blob:
        L.point(vcd_name, 0x4, nrm_blob)
    if clr_blob:
        L.point(vcd_name, 0xC, clr_blob)
    if tex_blob:
        L.point(vcd_name, 0x18, tex_blob)

    # The other chunks.
    mnt = bytearray(4 + 8 * len(model.materials))
    struct.pack_into(">I", mnt, 0, len(model.materials))
    L.add("material_name_table", mnt)
    for i, m in enumerate(model.materials):
        L.string("material_name_table", 4 + 8 * i, m.name)
        L.point("material_name_table", 8 + 8 * i, f"material:{m.name}")
    L.add("light_table", struct.pack(">I", 0))
    L.add("fog_table", struct.pack(">IIffI", 0, 0, 0.0, 1000.0, 0x000000FF))
    tt = bytearray(4 + 4 * len(model.textures))
    struct.pack_into(">I", tt, 0, len(model.textures))
    L.add("texture_table", tt)
    for i, t in enumerate(model.textures):
        L.string("texture_table", 4 + 4 * i, t.name)
    L.add("curve_table", struct.pack(">I", 0))
    L.add("animation_table", struct.pack(">I", 0))
    info = L.add("information", bytearray(0x14))
    L.string(info, 0x0, "ver1.02")
    L.point(info, 0x4, root_blob)
    L.string(info, 0x8, model.draw_root.name)
    L.string(info, 0xC, model.hit_root.name)
    L.string(info, 0x10, "OpenSPM map")

    data, fields, where = L.link()
    chunk_names = ["animation_table", "curve_table", "fog_table", "information", "light_table",
                   "material_name_table", "texture_table", "vcd_table"]
    names = bytearray()
    chunks = bytearray()
    for n in chunk_names:
        chunks += struct.pack(">II", where[n], len(names))
        names += n.encode("ascii") + b"\0"
    reloc = b"".join(struct.pack(">I", f) for f in fields)
    body = data + reloc + bytes(chunks) + bytes(names)
    while (0x20 + len(body)) % 32:
        body += b"\0"
    header = struct.pack(">IIII16x", 0x20 + len(body), len(data), len(fields), len(chunk_names))
    return header + body


# ---------------------------------------------------------------------------
# TPL (RGBA8 images)

TPL_MAGIC = 0x0020AF30
GX_TF_RGBA8 = 6


def _rgba8_tiles(t: Texture) -> bytes:
    w, h = t.width, t.height
    if w % 4 or h % 4:
        raise ValueError(f"texture {t.name}: width and height must be multiples of 4")
    out = bytearray()
    px = t.rgba
    for ty in range(0, h, 4):
        for tx in range(0, w, 4):
            ar = bytearray()
            gb = bytearray()
            for y in range(ty, ty + 4):
                for x in range(tx, tx + 4):
                    o = 4 * (y * w + x)
                    r, g, b, a = px[o], px[o + 1], px[o + 2], px[o + 3]
                    ar += bytes((a, r))
                    gb += bytes((g, b))
            out += ar + gb
    return bytes(out)


def build_tpl(textures: list[Texture]) -> bytes:
    n = len(textures)
    table_at = 0xC
    headers_at = table_at + 8 * n
    data_at = (headers_at + 0x24 * n + 31) & ~31
    images = []
    offset = data_at
    for t in textures:
        img = _rgba8_tiles(t)
        images.append((offset, img))
        offset = (offset + len(img) + 31) & ~31
    out = bytearray(offset)
    struct.pack_into(">III", out, 0, TPL_MAGIC, n, table_at)
    for i, t in enumerate(textures):
        h_at = headers_at + 0x24 * i
        struct.pack_into(">II", out, table_at + 8 * i, h_at, 0)
        struct.pack_into(">HHIIIIIIfBBBB", out, h_at, t.height, t.width, GX_TF_RGBA8, images[i][0],
                         t.wrap_s, t.wrap_t, 1, 1, 0.0, 0, 0, 0, 0)  # filters: linear
        out[images[i][0]:images[i][0] + len(images[i][1])] = images[i][1]
    return bytes(out)


# ---------------------------------------------------------------------------
# U8 archive and LZ compression


def build_u8(files: dict[str, bytes]) -> bytes:
    """A U8 archive of `files` ({"dvd/map/x/map.dat": data}); the paths the
    game opens are "./" + these."""
    tree: dict = {}
    for path, data in files.items():
        parts = path.strip("/").split("/")
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = data
    nodes: list[list] = []  # [type, name, a, b]
    names = bytearray(b"\0")

    def name_off(name: str) -> int:
        o = len(names)
        names.extend(name.encode("ascii") + b"\0")
        return o

    def walk(d: dict, name: str, parent: int) -> None:
        me = len(nodes)
        nodes.append([1, name_off(name) if me else 0, parent, 0])
        for key in sorted(d, key=lambda k: k.lower()):
            v = d[key]
            if isinstance(v, dict):
                walk(v, key, me)
            else:
                nodes.append([0, name_off(key), v, len(v)])
        nodes[me][3] = len(nodes)

    walk(tree, "", 0)
    header_size = 12 * len(nodes) + len(names)
    data_offset = (0x20 + header_size + 31) & ~31
    out = bytearray(data_offset)
    blobs = bytearray()
    for i, (kind, noff, a, b) in enumerate(nodes):
        if kind == 0:
            at = data_offset + len(blobs)
            blobs += a
            while len(blobs) % 32:
                blobs.append(0)
            struct.pack_into(">III", out, 0x20 + 12 * i, noff, at, b)
        else:
            struct.pack_into(">III", out, 0x20 + 12 * i, (1 << 24) | noff, a, b)
    out[0x20 + 12 * len(nodes):0x20 + 12 * len(nodes) + len(names)] = names
    struct.pack_into(">IIII", out, 0, 0x55AA382D, 0x20, header_size, data_offset)
    return bytes(out) + bytes(blobs)


def lz10_compress(data: bytes) -> bytes:
    """Nintendo LZ77 (type 0x10), greedy matching over a 4 KiB window."""
    n = len(data)
    if n >= 1 << 24:
        raise ValueError("too large for an LZ10 header")
    out = bytearray(struct.pack("<I", (n << 8) | 0x10))
    i = 0
    heads: dict[bytes, list[int]] = {}
    while i < n:
        flag_at = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len, best_dist = 0, 0
            key = data[i:i + 3]
            if len(key) == 3:
                for j in reversed(heads.get(key, [])[-64:]):
                    dist = i - j
                    if dist > 0x1000:
                        break
                    length = 3
                    while length < 18 and i + length < n and data[j + length] == data[i + length]:
                        length += 1
                    if length > best_len:
                        best_len, best_dist = length, dist
                        if length == 18:
                            break
            if best_len >= 3:
                flags |= 0x80 >> bit
                v = ((best_len - 3) << 12) | (best_dist - 1)
                out += struct.pack(">H", v)
                for k in range(i, i + best_len):
                    heads.setdefault(data[k:k + 3], []).append(k)
                i += best_len
            else:
                heads.setdefault(key, []).append(i)
                out.append(data[i])
                i += 1
        out[flag_at] = flags
    while len(out) % 4:
        out.append(0)
    return bytes(out)


def lz10_decompress(data: bytes) -> bytes:
    if data[0] != 0x10:
        raise ValueError("not LZ10")
    size = struct.unpack("<I", data[:4])[0] >> 8
    out = bytearray()
    i = 4
    while len(out) < size:
        flags = data[i]
        i += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                v = struct.unpack(">H", data[i:i + 2])[0]
                i += 2
                length, dist = (v >> 12) + 3, (v & 0xFFF) + 1
                for _ in range(length):
                    out.append(out[-dist])
            else:
                out.append(data[i])
                i += 1
    return bytes(out)


def build_map_archive(file_name: str, map_dat: bytes, tpl: bytes, extra: dict[str, bytes] | None = None) -> bytes:
    files = {f"dvd/map/{file_name}/map.dat": map_dat, f"dvd/map/{file_name}/texture.tpl": tpl}
    files.update(extra or {})
    return lz10_compress(build_u8(files))


# ---------------------------------------------------------------------------
# Levels from boxes


def box_faces(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float,
              tile: float = 50.0, faces: str = "+x-x+y-y+z-z") -> list[list[Vertex]]:
    """The faces of an axis-aligned box as triangle strips, outward normals,
    texture coordinates in `tile` world units per texture repeat."""
    strips = []

    def quad(a, b, c, d, n, uvs):
        # strip order a, b, d, c makes the two triangles a-b-d and b-d-c (clockwise from outside)
        strips.append([Vertex(a, n, uvs[0]), Vertex(b, n, uvs[1]), Vertex(d, n, uvs[3]), Vertex(c, n, uvs[2])])

    def uv(u0, u1, v0, v1):
        return [(u0 / tile, v0 / tile), (u1 / tile, v0 / tile), (u1 / tile, v1 / tile), (u0 / tile, v1 / tile)]

    if "+z" in faces:  # front (toward the 2D camera)
        quad((x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1), (0, 0, 1), uv(x0, x1, -y1, -y0))
    if "-z" in faces:
        quad((x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0), (0, 0, -1), uv(-x1, -x0, -y1, -y0))
    if "+y" in faces:  # top
        quad((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (0, 1, 0), uv(x0, x1, z0, z1))
    if "-y" in faces:
        quad((x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (x0, y0, z0), (0, -1, 0), uv(x0, x1, z0, z1))
    if "+x" in faces:
        quad((x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1), (1, 0, 0), uv(-z1, -z0, -y1, -y0))
    if "-x" in faces:
        quad((x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0), (-1, 0, 0), uv(z0, z1, -y1, -y0))
    return strips
