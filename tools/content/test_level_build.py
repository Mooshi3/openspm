"""level_build.py and the sample level: the pieces' joints on their own, and
(with the player's codegen image in OPENSPM_CODEGEN_DOL and
`pip install unicorn`) the game's own code on the sample level: mapLoad,
the hit-object lookup by name a pipe descriptor uses, the A2D / A3D group
attributes the init script sets, and the collision ray test on the ground,
the step, a platform and the pipe tops."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import level_build as lb  # noqa: E402
import map_build as mb  # noqa: E402
import sample_level  # noqa: E402

HIT_NAME_TO_PTR = 0x8006F7A8
HIT_GRP_ATTR_ON = 0x8006CE4C  # what evt_hitobj_attr_onoff(1, 1, name, attr) calls
MAP_LOAD = 0x800816E8
HIT_LIST = 0x8006F598
HIT_CHECK = 0x8006ECB4


def names(j: mb.Joint) -> list[str]:
    return [j.name] + [n for c in j.children for n in names(c)]


def test_pipe_pieces() -> None:
    lv = lb.Level("os1_99")
    lv.texture(lb.tex_pipe())
    lv.material("m", "pipe")
    lv.solid("floor", (-100, 100, -50, 0, -50, 50), top="m")
    lv.pipe("dokan_1", x=0, z=0, base=0, top=50, material="m")
    m = lv.model()
    assert names(m.draw_root) == ["A", "floor", "dokan_1"]
    assert names(m.hit_root) == ["S", "h_floor", "h_dokan_1", "A2D", "A2D_dokan_1", "A3D", "A3D_dokan_1"]
    assert lv.pipes["dokan_1"] == (0, 50, 0)
    try:
        lv.pipe("dokan_1", x=10, z=0, base=0, top=50, material="m")
    except ValueError:
        pass
    else:
        raise AssertionError("a second piece with the same name must be refused")


def test_textures_are_square_powers_of_two() -> None:
    for t in sample_level.build().textures.values():
        assert t.width in (8, 16, 32, 64, 128) and t.height in (8, 16, 32, 64, 128), t.name
        assert len(t.rgba) == 4 * t.width * t.height


def test_sample_level_builds() -> None:
    lv = sample_level.build()
    arc = lv.archive()
    assert arc[0] == 0x10 and len(arc) < 200_000
    assert set(lv.pipes) == {"dokan_1", "dokan_2"}
    assert lv.pipes["dokan_1"] == sample_level.LAYOUT["entrance_pipe"]
    assert lv.pipes["dokan_2"] == sample_level.LAYOUT["exit_pipe"]


# ---- the game's own code (optional) ---------------------------------------

def _harness():
    path = os.environ.get("OPENSPM_CODEGEN_DOL")
    if not path:
        return None
    import game_harness as gh
    if not gh.available():
        return None
    return gh.Harness.from_file(path)


def test_harness_lfs_fills_both_halves() -> None:
    """Gekko's lfs writes both halves of an FPR: a stale ps1 left by an
    earlier paired-single result must not survive it (PSMTXTrans loads 0.0
    with lfs and stores both halves)."""
    h = _harness()
    if h is None:
        print("skip test_harness_lfs_fills_both_halves (OPENSPM_CODEGEN_DOL / unicorn)")
        return
    for n in (0, 4):  # PSMTXTrans's two constants
        h._set_ps(n, 0.0, -150.0)
    m = h.alloc(48)
    h.call(0x80278704, m, fargs=(1.0, 2.0, 3.0))  # PSMTXTrans
    assert [h.f32(m + 4 * k) for k in range(12)] == [1, 0, 0, 1, 0, 1, 0, 2, 0, 0, 1, 3]


def test_game_loads_sample_level() -> None:
    h = _harness()
    if h is None:
        print("skip test_game_loads_sample_level (OPENSPM_CODEGEN_DOL / unicorn)")
        return
    lv = sample_level.build()
    h.serve_files({f"map/{sample_level.MAP}.bin": lv.archive()})
    name = h.put_str(sample_level.MAP)
    for _ in range(4):
        if h.call(MAP_LOAD, name) == 1:
            break
    else:
        raise AssertionError("mapLoad did not finish")
    m = lv.model()
    work = h.u32(0x80574900 - 32528)
    group = work + h.u32(work + 1688) * 844
    assert h.u32(group + 384) == len(names(m.draw_root))
    assert h.u32(group + 392) == len(names(m.hit_root))

    # The names a pipe descriptor and the init script use resolve.
    def hitobj(n: str) -> int:
        return h.call(HIT_NAME_TO_PTR, h.put_str(n))

    for n in ("A2D_dokan_1", "A3D_dokan_1", "A2D_dokan_2", "A3D_dokan_2", "A2D", "A3D", "h_ground_1", "edge_front"):
        assert hitobj(n) != 0, n
    assert hitobj("dokan_9") == 0
    # The init script's attributes: no assert, and the group's objects take them.
    h.call(HIT_GRP_ATTR_ON, 0, h.put_str("A2D"), 0x40000000)
    h.call(HIT_GRP_ATTR_ON, 0, h.put_str("A3D"), 0x20000000)

    h.call(HIT_LIST)
    out = h.alloc(28)

    def down(x: float, z: float = 0.0, y: float = 400.0):
        h.wf32(out + 12, 1000.0)
        hit = h.call(HIT_CHECK, 0, out, out + 4, out + 8, out + 12, out + 16, out + 20, out + 24,
                     fargs=(x, y, z, 0.0, -1.0, 0.0))
        return hit and round(h.f32(out + 4), 1)

    assert down(0.0) == 0.0             # the low meadow
    assert down(275.0) == 25.0           # the stone step
    assert down(470.0) == 140.0          # a wooden platform
    assert down(560.0) == 50.0           # the higher meadow
    assert down(-575.0) == 50.0          # the entrance pipe's top
    assert down(1350.0) == 50.0          # the exit pipe's top
    assert down(1200.0, z=140.0) == 0.0  # the ground reaches the depth edges
    print("  hit objects:", h.u32(group + 392), "draw objects:", h.u32(group + 384))


if __name__ == "__main__":
    for fn_name, fn in sorted(globals().items()):
        if fn_name.startswith("test_") and callable(fn):
            fn()
            print("ok", fn_name)
