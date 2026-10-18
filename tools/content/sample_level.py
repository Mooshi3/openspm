#!/usr/bin/env python3
"""The sample level "os1_01" (mods/sample_level, docs/CUSTOM-LEVELS.md).

A short side-scrolling meadow, made only of boxes and textures drawn here:

  x -700   a brick wall, the entrance pipe (dokan_1) and the guide NPC
  x -250   a row of blocks (placed by the init script), ground at y 0
  x  300   a two-block step up to a higher meadow with wooden platforms
  x  800   steps down again; the last NPC
  x 1350   the exit pipe (dokan_2) back to Flipside; a brick wall at 1500

Invisible walls at z = +-150 keep the player on the ground in 3D.
Everything the mod's init script places (blocks, coins, NPCs, enemies) uses
the positions in LAYOUT, which mods/sample_level/openspm_sample_level.c
repeats.

  sample_level.py OUT_DIR    writes OUT_DIR/map/os1_01.bin
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import level_build as lb  # noqa: E402

MAP = "os1_01"
DEPTH = 150.0  # the ground runs from z -150 to 150

LAYOUT = {
    "entrance_pipe": (-575.0, 50.0, 0.0),  # top centre
    "exit_pipe": (1350.0, 50.0, 0.0),
    "guide": (-400.0, 0.0, 0.0),
    "sage": (1000.0, 0.0, 0.0),
    "walls": (-700.0, 1500.0),
}


def build() -> lb.Level:
    lv = lb.Level(MAP)
    for tex in (lb.tex_grass(), lb.tex_dirt(), lb.tex_bricks(), lb.tex_stone(), lb.tex_pipe(), lb.tex_wood(),
                lb.tex_sky(), lb.tex_grass("hill", seed=9)):
        lv.texture(tex)
    lv.material("m_grass", "grass")
    lv.material("m_dirt", "dirt")
    lv.material("m_bricks", "bricks")
    lv.material("m_stone", "stone")
    lv.material("m_pipe", "pipe")
    lv.material("m_wood", "wood")
    lv.material("m_sky", "sky")
    lv.material("m_hill", "hill", color=(170, 210, 170, 255))

    d = DEPTH
    # The ground: low meadow, the higher middle, low again.
    lv.solid("ground_1", (-700, 300, -150, 0, -d, d), top="m_grass", side="m_dirt")
    lv.solid("ground_2", (300, 800, -150, 50, -d, d), top="m_grass", side="m_dirt")
    lv.solid("ground_3", (800, 1500, -150, 0, -d, d), top="m_grass", side="m_dirt")
    # Walls at both ends.
    lv.solid("wall_l", (-760, -700, -150, 600, -d, d), top="m_bricks")
    lv.solid("wall_r", (1500, 1560, -150, 600, -d, d), top="m_bricks")
    # A stone step to get up, wooden platforms above the middle.
    lv.solid("step_1", (250, 300, 0, 25, -d, d), top="m_stone")
    lv.solid("plat_1", (420, 520, 125, 140, -40, 40), top="m_wood")
    lv.solid("plat_2", (600, 700, 175, 190, -40, 40), top="m_wood")
    lv.solid("crate_1", (-120, -70, 0, 50, -25, 25), top="m_wood")
    # The pipes.
    lv.pipe("dokan_1", x=-575, z=0, base=0, top=50, material="m_pipe")
    lv.pipe("dokan_2", x=1350, z=0, base=0, top=50, material="m_pipe")
    # Invisible walls along the depth edges (3D).
    lv.zone("edge_front", (-700, 1500, -150, 600, d, d + 20))
    lv.zone("edge_back", (-700, 1500, -150, 600, -d - 20, -d))
    # Scenery: the sky and far hills behind the level.
    lv.backdrop("sky", -1500, 2300, -300, 900, -700, "m_sky", repeat_x=6.0)
    lv.backdrop_end("sky_end", 2300, -1500, 1500, -300, 900, "m_sky", repeat_z=5.0)
    lv.decor("far_ground", (-1500, 2300, -200, -150, -1500, 1500), "m_hill", tile=200.0, faces="+y")
    lv.decor("hill_1", (-650, -250, -150, 120, -420, -300), "m_hill", faces="+x-x+y+z")
    lv.decor("hill_2", (-100, 450, -150, 200, -500, -380), "m_hill", faces="+x-x+y+z")
    lv.decor("hill_3", (600, 1100, -150, 150, -440, -320), "m_hill", faces="+x-x+y+z")
    lv.decor("hill_4", (1150, 1550, -150, 240, -520, -400), "m_hill", faces="+x-x+y+z")
    return lv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args(argv)
    out = args.out_dir / "map" / f"{MAP}.bin"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build().archive()
    if not out.exists() or out.read_bytes() != data:
        out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
