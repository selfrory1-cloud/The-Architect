import math
from dataclasses import replace

import pytest

from app import app
from architect import slab
from architect.builder import BuildError, Library, Placement, build, rotate, snap
from architect.query import RecipeError, apply_llm_theming, build_recipe

LIB = Library.load()
WALL_UUID = "55421077-5672-492d-9609-89befbf9eb9d"


def make(**form):
    recipe, _ = build_recipe(form, LIB)
    return build(recipe, LIB)


def test_slab_roundtrip_preserves_uuid_position_rotation():
    assets = [slab.SlabAsset(WALL_UUID, 1.25, 2.0, 3.5, 6), slab.SlabAsset(WALL_UUID, 0, 0, 0, 18)]
    code = slab.encode(assets, normalize=False)
    assert code.startswith("H4sI")
    assert slab.decode(f"```{code}```") == assets


def test_slab_normalizes_negative_coordinates():
    out = slab.decode(slab.encode([slab.SlabAsset(WALL_UUID, -0.25, 0, -2, 0)]))
    assert (out[0].x, out[0].z) == (0, 0)


def test_snap_aligns_port_under_rotation():
    wall = LIB.get("Tavern_Wall_01")
    for q in range(4):
        p = snap(wall, "W", (5.0, 7.0), 0.0, q)
        assert p.port_world("W") == pytest.approx((5.0, 7.0))
        assert math.dist(p.port_world("W"), p.port_world("E")) == pytest.approx(2.0)
    assert rotate(0, 1, 1) == (1, 0)  # north turns east (clockwise)


REF_CORNERS_WITH_FILLER = (
    "H4sIAAAAAAAACjv369xFJgYmBgaGcgGn0KIwXc9pnJ37fv98PZcTKHbTfkPTbyl7t8U/9A0UfFMTWYBiXUDFCZwNLF8Y"
    "GRgUZBiYdaB0CgOEBlIMC/gbGEHyIKADpVMYEECBuYERZM4DUYh5CsxQcZkGFgirAagLAKgNkKKcAAAA"
)
REF_HAND_ADJUSTED_8X8 = (
    "H4sIAAAAAAAACjv369xFJgZmBjhwaIDQjIyYYkwCQKJcwCm0KEzXcxpn577fP1/PBYl9gShmTgESBQYMDDqMEPoLlN7D"
    "BKFB8h+4IPIg+guUBusTh+oTh+oTh6pXhqpXhqpXhpgH0gc2VxzKB4oHMYPsaWAB0R+4IHSBOJSv3MDCAHZfA2MXUP0F"
    "SwbmQ4wQ+hcDhDaC0gxgexvAvgLqB9NA8xiNoO7/BaW7oO44BPUHAwMAtSMI2lABAAA="
)
REF_WITH_FLOOR_AND_DOOR_8X8 = (
    "H4sIAAAAAAAACmWQvUsDQRDFd7NBOVAuiEHQwB2pxKwfWNklWggWKY2HWJy4gpWFTdrDTqutYqMg+A8INoIidm4VsbBId60I"
    "gpBCTePO7YsJuMU9Zt773cxu+6f9kmMj7O/UEqec/+/lCvbTLKxtHjXmN1reyUPv+/1i3PY6n8uq2bqtn55NzUzOvTaI7"
    "VSvj3vT1fWrr8WlsL6/K2zvbi9+PtwZXb15Ox/bfto6WLE9acM1u8B9bqCPE6gD1BXGFHN+P9+FUp/y1CftQrN8"
    "gHyAfIB8BfkK8lYjOyddYIKUzofV2EvypGnRaVxO8pq7nGFO2ZDS0UMPF5aSrLr0nYYi4SZzEk7/C4Xbw9b5LD"
    "fLBN23r3HZaVhymhbh+/A9+MLtTVwELgIXgYvAReAicHR/4kiJIyWOlLjM9+F78IW7L3GkxLn74j2K8H34Hnzh"
    "3o1qjbkaczXmaszVmKv5IE+8BCfBSXASnAQnwUk+mEucwb4G+xrsa7CvwZ4KeYW8Ql4hr5BXuI9i/Xf5BWnY9t1UAwAA"
)


def test_api_tavern_matches_in_game_room_without_placeholder_floor_or_door():
    response = app.test_client().post("/api/generate", json={
        "theme": "tavern", "room_width": 8, "room_depth": 8, "furnish": False, "roof": False,
    })
    assert response.status_code == 200
    data = response.get_json()
    expected = [a for a in slab.decode(REF_WITH_FLOOR_AND_DOOR_8X8)
                if not a.uuid.startswith("00000000-0000-4000-8000-")]
    assert data["stats"]["by_category"] == {"floor": 56, "door": 1, "wall": 16}
    actual = slab.decode(data["slab"])
    assert len(actual) == len(expected)
    assert set(actual) == set(expected)
    assert not data["warnings"]


def _area(fp):
    return (fp[2] - fp[0]) * (fp[3] - fp[1])


def _assert_closed_ring(fps):
    """Footprints tile the rectangular ring around their bounding box with no gaps or overlaps."""
    for i, a in enumerate(fps):
        for b in fps[i + 1:]:
            assert not (a[0] < b[2] - 1e-6 and b[0] < a[2] - 1e-6 and a[1] < b[3] - 1e-6 and b[1] < a[3] - 1e-6), (a, b)
    x0, z0 = min(f[0] for f in fps), min(f[1] for f in fps)
    x1, z1 = max(f[2] for f in fps), max(f[3] for f in fps)
    t = 0.5
    for f in fps:  # every piece hugs one edge of the bounding box
        assert f[0] == x0 or f[2] == x1 or f[1] == z0 or f[3] == z1, f
    assert sum(map(_area, fps)) == pytest.approx((x1 - x0) * (z1 - z0) - (x1 - x0 - 2 * t) * (z1 - z0 - 2 * t))


@pytest.mark.parametrize("code", [REF_CORNERS_WITH_FILLER, REF_HAND_ADJUSTED_8X8])
def test_slab_position_is_min_corner_of_rotated_footprint(code):
    by_uuid = {a.uuid: a for a in LIB.assets.values()}
    walls = [Placement(by_uuid[a.uuid], a.x, a.y, a.z, a.rot // 6) for a in slab.decode(code)
             if a.uuid in by_uuid and by_uuid[a.uuid].name.startswith("Tavern_Wall")]
    _assert_closed_ring([w.footprint() for w in walls])


@pytest.mark.parametrize(("width", "depth"), [(8, 8), (6, 4), (6, 10), (10, 4)])
def test_single_room_walls_form_closed_ring_inside_floor(width, depth):
    res = make(room_width=width, room_depth=depth, furnish=False, roof=False)
    segs = [p for p in res.placements if p.category in ("wall", "door")]
    _assert_closed_ring([p.footprint() for p in segs])
    floor = [p.footprint() for p in res.placements if p.category == "floor"]
    assert (min(f[0] for f in floor), min(f[1] for f in floor)) == (0, 0.5)
    assert (max(f[2] for f in floor), max(f[3] for f in floor)) == (width, depth - 0.5)
    for p in segs:
        assert (p.x * 2).is_integer() and (p.z * 2).is_integer()
        fx0, fz0, fx1, fz1 = p.footprint()
        assert fx0 >= 0 and fz0 >= 0 and fx1 <= width and fz1 <= depth
    wall_rot = {"S": 0, "W": 6, "N": 12, "E": 18}
    for p in segs:
        if p.asset.name == "Tavern_Wall_01":
            fx0, fz0, fx1, fz1 = p.footprint()
            side = "S" if fz0 == 0 and fx1 - fx0 == 2 else "N" if fz1 == depth and fx1 - fx0 == 2 else "W" if fx0 == 0 else "E"
            assert p.rot == wall_rot[side], (side, p)


def test_single_room_counts_use_filler_for_odd_columns():
    res = make(theme="tavern", floors=1, rooms_per_floor=1, room_width=6, room_depth=4, furnish=False, roof=False)
    assert res.stats()["by_category"] == {"floor": 18, "wall": 10, "door": 1}
    assert sum(p.asset.name == "Tavern_Wall_Filler_01" for p in res.placements) == 3


def test_adjacent_rooms_share_a_wall_with_interior_door():
    res = make(theme="tavern", floors=1, rooms_per_floor=2, room_width=4, room_depth=4, furnish=False, roof=False)
    segs = [p for p in res.placements if p.category in ("wall", "door")]
    centers = [tuple(round(v, 3) for v in p.center()) for p in segs]
    assert len(centers) == len(set(centers)), "shared wall placed twice"
    assert res.stats()["by_category"]["door"] == 2  # entrance + interior


def test_wall_run_rejects_miscalibrated_door_even_if_loop_would_close():
    door = LIB.get("Tavern_Door_01")
    broken = replace(door, ports={**door.ports, "E": (2, 0.5)})
    lib = Library({**LIB.assets, door.name: broken}, LIB.themes)
    recipe, _ = build_recipe({}, lib)
    with pytest.raises(BuildError, match="must span one"):
        build(recipe, lib)


def test_multi_floor_stacks_on_wall_height_with_stairwell_and_hole():
    res = make(theme="tavern", floors=3, rooms_per_floor=1, room_width=6, room_depth=8, furnish=False, roof=True)
    ys = sorted({p.y for p in res.placements if p.category == "floor"})
    assert ys == [0.0, 2.0, 4.0]
    stairs = [p for p in res.placements if p.category == "stairs"]
    assert [s.y for s in stairs] == [0.0, 2.0]
    assert stairs[0].footprint() != stairs[1].footprint()  # alternate columns
    upper_floor = [p for p in res.placements if p.category == "floor" and p.y == 2.0]
    assert len(upper_floor) == 6 * 7 - 8  # eight 1x1 tiles cut for the stairwell opening
    assert {p.y for p in res.placements if p.category == "roof"} == {6.0}


def test_props_stay_inside_walls_and_do_not_overlap():
    res = make(theme="tavern", floors=2, rooms_per_floor=4, room_width=8, room_depth=8, prop_density=1.0, seed=1)
    for z in res.zones:
        ox, _, oz = z.offset
        props = [p.footprint() for p in z.placements if p.category == "prop"]
        assert props
        walls = [p.footprint() for p in z.placements if p.category in ("wall", "door")]
        for i, (x0, z0, x1, z1) in enumerate(props):
            assert x0 >= ox and x1 <= ox + 8 and z0 >= oz and z1 <= oz + 8
            for w in walls:
                assert not (x0 < w[2] and w[0] < x1 and z0 < w[3] and w[1] < z1)
            for o in props[i + 1:]:
                assert not (x0 < o[2] and o[0] < x1 and z0 < o[3] and o[1] < z1)


def test_deterministic_for_seed_and_slab_decodes():
    a = make(theme="dungeon", floors=2, rooms_per_floor=3, room_width=6, room_depth=8, seed=7)
    b = make(theme="dungeon", floors=2, rooms_per_floor=3, room_width=6, room_depth=8, seed=7)
    assert a.slab_code == b.slab_code
    assert len(slab.decode(a.slab_code)) == len(a.placements)
    assert any("Uncalibrated" in w for w in a.warnings)


@pytest.mark.parametrize("form,msg", [
    ({"room_width": 5}, "even"),
    ({"floors": 2, "room_depth": 6}, "stairwell"),
    ({"theme": "spaceship"}, "unknown theme"),
    ({"rooms_per_floor": 0}, "rooms must be"),
])
def test_invalid_form_rejected(form, msg):
    with pytest.raises(RecipeError) as exc:
        build_recipe(form, LIB)
    assert msg in str(exc.value)


def test_llm_theming_is_sanitized_and_keeps_structure():
    recipe, _ = build_recipe({"theme": "tavern", "floors": 1, "rooms_per_floor": 2}, LIB)
    fake = lambda prompt: {"floors": [{"rooms": [
        {"name": "The Salty Dog", "role": "Bar", "props": ["Barrel_01", "Laser_Cannon"], "x": 99},
        {"name": "", "role": "Spaceport"},
        {"name": "extra room"},
    ]}]}
    out = apply_llm_theming(recipe, "harbour tavern", LIB, llm=fake)
    rooms = out["floors"][0]["rooms"]
    assert len(rooms) == 2
    assert rooms[0] == {"name": "The Salty Dog", "role": "Bar", "props": ["Barrel_01"]}
    assert rooms[1] == recipe["floors"][0]["rooms"][1]


def test_api_generate_and_decode():
    client = app.test_client()
    resp = client.post("/api/generate", json={"theme": "tavern", "floors": 2, "rooms_per_floor": 2,
                                              "room_width": 8, "room_depth": 8, "furnish": True})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"]
    assert set(data["preview"]["levels"]) == {"0", "1"}
    dec = client.post("/api/decode", json={"slab": data["slab"]}).get_json()
    assert dec["total"] == data["stats"]["total"]
    assert any(a["library_name"] == "Tavern_Wall_01" for a in dec["assets"])
    bad = client.post("/api/generate", json={"room_width": 3})
    assert bad.status_code == 400 and bad.get_json()["errors"]
