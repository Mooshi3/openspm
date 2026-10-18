#!/usr/bin/env python3
"""A level description to the game's map files (docs/CUSTOM-LEVELS.md).

On top of map_build.py (the file formats): a Level is a list of pieces in
world units, each turned into what a game map has for it.

  solid    a box you see and stand on: a draw joint (its top in one
           material, the other faces in another) and a collision joint.
  decor    a box you only see.
  backdrop a big picture behind the level (draw only); backdrop_end one
           across the far end, for the 3D view.
  pipe     a warp pipe: body and rim you see and bump into, and the rim's
           top as two named collision objects, "A2D_<name>" under the group
           "A2D" and "A3D_<name>" under "A3D". A pipe descriptor in the map's
           init script names them (DokanDesc hit names); the script also sets
           the groups' attributes, like the game's maps:
             evt_hitobj_attr_onoff(1, 1, "A2D", 0x40000000)
             evt_hitobj_attr_onoff(1, 1, "A3D", 0x20000000)
  zone     an invisible named collision box (a door's loading zone, a
           trigger the script checks).

Textures are drawn here from code (the tex_* helpers below): nothing in a
built level comes from the game.

  level = Level("os1_01")
  level.solid("ground", (-600, 1200, -100, 0, -150, 150), top="grass", side="dirt")
  level.pipe("dokan_1", x=-500, z=0, base=0, top=50)
  open("os1_01.bin", "wb").write(level.archive())
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import map_build as mb

HIT_2D_ONLY_GROUP = "A2D"
HIT_3D_ONLY_GROUP = "A3D"


# ---------------------------------------------------------------------------
# Textures (RGBA8, drawn from code)


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _image(name: str, w: int, h: int, pixel) -> mb.Texture:
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes(_clamp(c) for c in pixel(x, y))
    return mb.Texture(name, w, h, bytes(px))


def tex_grass(name: str = "grass", size: int = 32, seed: int = 1) -> mb.Texture:
    rnd = random.Random(seed)
    spots = {(rnd.randrange(size), rnd.randrange(size)) for _ in range(size * 2)}

    def px(x, y):
        g = 150 + 25 * math.sin(x * 0.7) * math.cos(y * 0.5)
        if (x, y) in spots:
            return (120, 215, 90, 255)
        return (60, g, 50, 255)

    return _image(name, size, size, px)


def tex_dirt(name: str = "dirt", size: int = 32, seed: int = 2) -> mb.Texture:
    rnd = random.Random(seed)
    noise = [[rnd.random() for _ in range(size)] for _ in range(size)]

    def px(x, y):
        band = 0.85 + 0.15 * math.sin(y * 0.6)
        n = noise[y][x]
        base = (150, 100, 60) if n > 0.15 else (110, 72, 42)
        if y < 3:  # a grass lip along the top edge
            return (70, 150 + 30 * n, 55, 255)
        return (base[0] * band, base[1] * band, base[2] * band, 255)

    return _image(name, size, size, px)


def tex_bricks(name: str = "bricks", size: int = 32, color=(190, 110, 70)) -> mb.Texture:
    def px(x, y):
        row = y // 8
        off = 8 if row % 2 else 0
        mortar = y % 8 == 7 or (x + off) % 16 == 15
        if mortar:
            return (230, 220, 200, 255)
        shade = 1.0 - 0.15 * ((x + off) % 16) / 16
        return (color[0] * shade, color[1] * shade, color[2] * shade, 255)

    return _image(name, size, size, px)


def tex_stone(name: str = "stone", size: int = 32, seed: int = 3) -> mb.Texture:
    rnd = random.Random(seed)
    noise = [[rnd.random() for _ in range(size)] for _ in range(size)]

    def px(x, y):
        edge = x % 16 in (0, 15) or y % 16 in (0, 15)
        v = 150 + 40 * noise[y][x] - (40 if edge else 0)
        return (v, v, v + 10, 255)

    return _image(name, size, size, px)


def tex_pipe(name: str = "pipe", size: int = 32, color=(40, 190, 70)) -> mb.Texture:
    """Lit across: a light band left of centre, dark at the edges."""

    def px(x, y):
        t = x / (size - 1)
        light = 0.55 + 0.6 * math.exp(-((t - 0.3) ** 2) / 0.02) - 0.35 * (abs(t - 0.5) * 2) ** 3
        if t > 0.92 or t < 0.05:
            light = 0.35
        return (color[0] * light + 30 * (light > 1.0), color[1] * light, color[2] * light, 255)

    return _image(name, size, size, px)


def tex_wood(name: str = "wood", size: int = 32) -> mb.Texture:
    def px(x, y):
        grain = 0.8 + 0.2 * math.sin(y * 0.9 + math.sin(x * 0.3) * 2)
        plank = 0.75 if x % 16 == 0 else 1.0
        return (170 * grain * plank, 115 * grain * plank, 60 * grain * plank, 255)

    return _image(name, size, size, px)


def tex_sky(name: str = "sky", w: int = 32, h: int = 64, seed: int = 4) -> mb.Texture:
    """Top to bottom: deep blue to pale, with soft round clouds."""
    rnd = random.Random(seed)
    clouds = [(rnd.uniform(0, w), rnd.uniform(h * 0.15, h * 0.6), rnd.uniform(3, 6)) for _ in range(5)]

    def px(x, y):
        t = y / (h - 1)
        r, g, b = 70 + 120 * t, 130 + 90 * t, 230 + 20 * t
        for cx, cy, cr in clouds:
            dx = min(abs(x - cx), w - abs(x - cx))  # wraps sideways
            d = math.hypot(dx, (y - cy) * 1.6)
            if d < cr:
                k = min(1.0, (cr - d) / 1.5)
                r, g, b = r + (255 - r) * k, g + (255 - g) * k, b + (255 - b) * k
        return (r, g, b, 255)

    return _image(name, w, h, px)


# ---------------------------------------------------------------------------
# The level


Box = tuple[float, float, float, float, float, float]  # x0, x1, y0, y1, z0, z1


@dataclass
class Level:
    file: str
    textures: dict[str, mb.Texture] = field(default_factory=dict)
    materials: dict[str, mb.Material] = field(default_factory=dict)
    draw: list[mb.Joint] = field(default_factory=list)
    hit: list[mb.Joint] = field(default_factory=list)
    hit_2d: list[mb.Joint] = field(default_factory=list)
    hit_3d: list[mb.Joint] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    pipes: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # name: top centre
    zones: dict[str, Box] = field(default_factory=dict)

    def texture(self, tex: mb.Texture) -> str:
        self.textures[tex.name] = tex
        return tex.name

    def material(self, name: str, texture: str | None = None, color=(255, 255, 255, 255),
                 blend: int = mb.BLEND_OPAQUE) -> str:
        if texture is not None and texture not in self.textures:
            raise ValueError(f"material {name}: add texture {texture} first")
        self.materials[name] = mb.Material(name, color=color, texture=texture, blend=blend)
        return name

    def _name(self, name: str) -> str:
        if name in self.names or len(name) > 60:
            raise ValueError(f"piece name {name!r} taken or too long")
        self.names.add(name)
        return name

    def _mat(self, name: str) -> str:
        if name not in self.materials:
            raise ValueError(f"unknown material {name}")
        return name

    def solid(self, name: str, box: Box, top: str, side: str | None = None, tile: float = 50.0,
              collide: bool = True) -> None:
        """A box you see and (collide=True) stand on and bump into."""
        name = self._name(name)
        side = side or top
        meshes = [mb.Mesh(self._mat(top), mb.box_faces(*box, tile=tile, faces="+y"))]
        rest = mb.box_faces(*box, tile=tile, faces="+x-x-y+z-z")
        meshes.append(mb.Mesh(self._mat(side), rest))
        self.draw.append(mb.Joint(name, meshes=meshes, cull=3))
        if collide:
            self.hit.append(mb.Joint(f"h_{name}", meshes=[mb.Mesh(None, mb.box_faces(*box))]))

    def decor(self, name: str, box: Box, material: str, tile: float = 50.0, faces: str = "+x-x+y-y+z-z") -> None:
        """A box you only see."""
        self.draw.append(mb.Joint(self._name(name), meshes=[mb.Mesh(self._mat(material),
                                                                     mb.box_faces(*box, tile=tile, faces=faces))],
                                  cull=3))

    def backdrop(self, name: str, x0: float, x1: float, y0: float, y1: float, z: float, material: str,
                 repeat_x: float = 1.0) -> None:
        """A picture behind the level, facing the 2D camera, stretched over
        the rectangle (repeat_x times across)."""
        n = (0.0, 0.0, 1.0)
        strip = [mb.Vertex((x0, y1, z), n, (0.0, 0.0)), mb.Vertex((x1, y1, z), n, (repeat_x, 0.0)),
                 mb.Vertex((x0, y0, z), n, (0.0, 1.0)), mb.Vertex((x1, y0, z), n, (repeat_x, 1.0))]
        self.draw.append(mb.Joint(self._name(name), meshes=[mb.Mesh(self._mat(material), [strip])], cull=3))

    def backdrop_end(self, name: str, x: float, z0: float, z1: float, y0: float, y1: float, material: str,
                     repeat_z: float = 1.0) -> None:
        """A picture across the level's far end at `x`, facing back along -x:
        what the 3D camera (looking along +x) sees behind the level."""
        n = (-1.0, 0.0, 0.0)
        strip = [mb.Vertex((x, y1, z0), n, (0.0, 0.0)), mb.Vertex((x, y1, z1), n, (repeat_z, 0.0)),
                 mb.Vertex((x, y0, z0), n, (0.0, 1.0)), mb.Vertex((x, y0, z1), n, (repeat_z, 1.0))]
        self.draw.append(mb.Joint(self._name(name), meshes=[mb.Mesh(self._mat(material), [strip])], cull=3))

    def zone(self, name: str, box: Box) -> None:
        """An invisible named collision box."""
        self.hit.append(mb.Joint(self._name(name), meshes=[mb.Mesh(None, mb.box_faces(*box))]))
        self.zones[name] = box

    def pipe(self, name: str, x: float, z: float, base: float, top: float, material: str,
             width: float = 50.0, rim: float = 6.0, rim_height: float = 20.0) -> None:
        """A warp pipe standing on `base` with its opening at `top`, centred
        on (x, z). Its rim's top is the named pair of collision objects a
        pipe descriptor uses."""
        name = self._name(name)
        hw = width / 2.0
        rw = hw + rim
        body: Box = (x - hw, x + hw, base, top - rim_height, z - hw, z + hw)
        ring: Box = (x - rw, x + rw, top - rim_height, top, z - rw, z + rw)
        self.draw.append(mb.Joint(name, cull=3, meshes=[
            mb.Mesh(self._mat(material), mb.box_faces(*body, tile=width, faces="+x-x+z-z")),
            mb.Mesh(self._mat(material), mb.box_faces(*ring, tile=2 * rw, faces="+x-x+y-y+z-z")),
        ]))
        self.hit.append(mb.Joint(f"h_{name}", meshes=[
            mb.Mesh(None, mb.box_faces(*body, faces="+x-x+z-z")),
            mb.Mesh(None, mb.box_faces(*ring, faces="+x-x-y+z-z")),
        ]))
        top_face = mb.box_faces(*ring, faces="+y")
        self.hit_2d.append(mb.Joint(f"{HIT_2D_ONLY_GROUP}_{name}", meshes=[mb.Mesh(None, top_face)]))
        self.hit_3d.append(mb.Joint(f"{HIT_3D_ONLY_GROUP}_{name}", meshes=[mb.Mesh(None, top_face)]))
        self.pipes[name] = (x, top, z)

    # -- output ---------------------------------------------------------------

    def model(self) -> mb.MapModel:
        hit_children = list(self.hit)
        if self.hit_2d:
            hit_children.append(mb.Joint(HIT_2D_ONLY_GROUP, children=list(self.hit_2d)))
        if self.hit_3d:
            hit_children.append(mb.Joint(HIT_3D_ONLY_GROUP, children=list(self.hit_3d)))
        if not self.draw or not hit_children:
            raise ValueError("a level needs something to draw and something to stand on")
        return mb.MapModel(draw_root=mb.Joint("A", children=list(self.draw)),
                           hit_root=mb.Joint("S", children=hit_children),
                           materials=list(self.materials.values()), textures=list(self.textures.values()))

    def files(self) -> tuple[bytes, bytes]:
        m = self.model()
        return mb.build_map_dat(m), mb.build_tpl(m.textures)

    def archive(self) -> bytes:
        dat, tpl = self.files()
        return mb.build_map_archive(self.file, dat, tpl)
