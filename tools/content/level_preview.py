#!/usr/bin/env python3
"""A picture of a level (level_build.Level) from a camera, for checking a
layout without the game: the draw joints' triangles, textured and depth
sorted per pixel, through a perspective camera like the game's (25 degree
vertical field of view). Pure Python (slow but small): a few seconds for a
640 x 360 picture.

  level_preview.py OUT.png                      the sample level, three views
  level_preview.py OUT.png --view 2d --x 0      one view, the camera at x
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import level_build as lb  # noqa: E402
import map_build as mb  # noqa: E402

FOV_Y = 25.0
CLEAR = (40, 40, 48)


def _png(path: Path, w: int, h: int, rgb: bytearray) -> None:
    raw = b"".join(b"\0" + bytes(rgb[3 * w * y:3 * w * (y + 1)]) for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _triangles(level: lb.Level):
    """(three vertices, material) for every drawn triangle."""
    mats = level.materials
    for j in level.draw:
        for mesh in j.meshes:
            for strip in mesh.strips:
                for i in range(len(strip) - 2):
                    yield (strip[i], strip[i + 1], strip[i + 2]), mats[mesh.material]


def render(level: lb.Level, eye, target, w: int = 640, h: int = 360) -> bytearray:
    fx, fy, fz = (target[i] - eye[i] for i in range(3))
    n = math.sqrt(fx * fx + fy * fy + fz * fz)
    fwd = (fx / n, fy / n, fz / n)
    right = (fwd[1] * 0 - fwd[2] * 1, fwd[2] * 0 - fwd[0] * 0, fwd[0] * 1 - fwd[1] * 0)  # fwd x up
    n = math.sqrt(sum(c * c for c in right))
    right = tuple(c / n for c in right)
    up = (right[1] * fwd[2] - right[2] * fwd[1], right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0])
    focal = (h / 2) / math.tan(math.radians(FOV_Y) / 2)
    rgb = bytearray(bytes(CLEAR) * (w * h))
    depth = [math.inf] * (w * h)
    textures = level.textures

    def project(p):
        d = (p[0] - eye[0], p[1] - eye[1], p[2] - eye[2])
        z = d[0] * fwd[0] + d[1] * fwd[1] + d[2] * fwd[2]
        x = d[0] * right[0] + d[1] * right[1] + d[2] * right[2]
        y = d[0] * up[0] + d[1] * up[1] + d[2] * up[2]
        return w / 2 + focal * x / z, h / 2 - focal * y / z, z

    for verts, mat in _triangles(level):
        pts = [project(v.pos) for v in verts]
        if min(p[2] for p in pts) <= 1.0:
            continue
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = pts
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-6:
            continue
        tex = textures.get(mat.texture) if mat.texture else None
        su, sv = mat.uv_scale
        uvs = [(v.uv[0] * su / p[2], v.uv[1] * sv / p[2]) for v, p in zip(verts, pts)]
        iz = [1.0 / p[2] for p in pts]
        shade = 0.75 + 0.25 * max(0.0, verts[0].normal[1]) + 0.1 * verts[0].normal[2]
        for py in range(max(0, int(min(y0, y1, y2))), min(h - 1, int(max(y0, y1, y2))) + 1):
            for px in range(max(0, int(min(x0, x1, x2))), min(w - 1, int(max(x0, x1, x2))) + 1):
                cx, cy = px + 0.5, py + 0.5
                a = ((x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)) / area
                b = ((x2 - cx) * (y0 - cy) - (x0 - cx) * (y2 - cy)) / area
                c = 1.0 - a - b
                if a < 0 or b < 0 or c < 0:
                    continue
                inv = a * iz[0] + b * iz[1] + c * iz[2]
                z = 1.0 / inv
                k = py * w + px
                if z >= depth[k]:
                    continue
                depth[k] = z
                col = mat.color
                if tex is not None:
                    u = (a * uvs[0][0] + b * uvs[1][0] + c * uvs[2][0]) * z
                    v = (a * uvs[0][1] + b * uvs[1][1] + c * uvs[2][1]) * z
                    tx = int(u * tex.width) % tex.width
                    ty = int(v * tex.height) % tex.height
                    o = 4 * (ty * tex.width + tx)
                    col = tuple(tex.rgba[o + i] * mat.color[i] // 255 for i in range(3))
                rgb[3 * k:3 * k + 3] = bytes(min(255, int(ch * shade)) for ch in col[:3])
    return rgb


def views(level_x: float, kind: str):
    if kind == "2d":
        return (level_x, 95.0, 700.0), (level_x, 85.0, 0.0)
    if kind == "3d":  # as content_maps.h: half the distance, higher, inside the start
        return (max(level_x - 350.0, -425.0 - 245.0), 200.0, 0.0), (level_x, 85.0, 0.0)
    if kind == "overview":
        return (level_x, 260.0, 3300.0), (level_x, 120.0, 0.0)
    raise ValueError(kind)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--view", choices=("2d", "3d", "overview"))
    ap.add_argument("--x", type=float, default=0.0)
    args = ap.parse_args(argv)
    import sample_level
    level = sample_level.build()
    w, h = 640, 360
    if args.view:
        eye, target = views(args.x, args.view)
        _png(args.out, w, h, render(level, eye, target, w, h))
        return 0
    shots = [views(400.0, "overview"), views(-425.0, "2d"), views(400.0, "2d"), views(-150.0, "3d")]
    rows = [render(level, e, t, w, h) for e, t in shots]
    _png(args.out, w, h * len(rows), b"".join(rows))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
