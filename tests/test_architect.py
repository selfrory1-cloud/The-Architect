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


def test_wall_snapping_reproduces_talespire_corner_reference():
    reference = slab.decode(
        "H4sIAAAAAAAACjv369xFJgZGBgaGcgGn0KIwXc9pnJ37fv98PZcZKGYExAmcDAy/"
        "oDQENLAwMAAAZfi1RzgAAAA="
    )
    first, second, corner = reference
    wall = LIB.get("Tavern_Wall_01")
    assert {a.uuid for a in reference} == {wall.uuid}
    placed_corner = Placement(wall, corner.x, corner.y, corner.z, corner.rot // 6)
    placed_first = snap(wall, "W", placed_corner.port_world("E"), first.y, first.rot // 6)
    placed_second = snap(wall, "W", placed_first.port_world("E"), second.y, second.rot // 6)
    for placed, observed in ((placed_first, first), (placed_second, second)):
        assert (placed.x, placed.y, placed.z) == pytest.approx((observed.x, observed.y, observed.z))
        assert placed.rot == observed.rot


def test_single_room_perimeter_is_closed_with_one_door():
    res = make(theme="tavern", floors=1, rooms_per_floor=1, room_width=6, room_depth=4, furnish=False, roof=False)
    cats = res.stats()["by_category"]
    assert cats == {"floor": 6, "wall": 9, "door": 1}
    # Every wall segment's E port must coincide with another segment's W port (no gaps).
    segs = [p for p in res.placements if p.category in ("wall", "door")]
    w_ports = {tuple(round(v, 3) for v in p.port_world("W")) for p in segs}
    for p in segs:
        assert tuple(round(v, 3) for v in p.port_world("E")) in w_ports


def test_adjacent_rooms_share_a_wall_with_interior_door():
    res = make(theme="tavern", floors=1, rooms_per_floor=2, room_width=4, room_depth=4, furnish=False, roof=False)
    segs = [p for p in res.placements if p.category in ("wall", "door")]
    centers = [tuple(round(v, 3) for v in p.center()) for p in segs]
    assert len(centers) == len(set(centers)), "shared wall placed twice"
    assert res.stats()["by_category"]["door"] == 2  # entrance + interior


@pytest.mark.parametrize(("width", "depth"), [(8, 8), (6, 10), (10, 4)])
def test_wall_runs_match_each_floor_edge_after_door_replacement(width, depth):
    res = make(room_width=width, room_depth=depth, furnish=False, roof=False)
    segments = [p for p in res.placements if p.category in ("door", "wall")]
    expected = {
        2: (0, width, 0),
        3: (0, depth, 0),
        0: (0, width, depth),
        1: (0, depth, width),
    }
    for q, (start, end, fixed) in expected.items():
        along = q % 2
        across = 1 - along
        spans = []
        for p in segments:
            if p.q == q:
                w, e = p.port_world("W"), p.port_world("E")
                assert w[across] == e[across] == fixed
                assert p.center()[across] == fixed
                spans.append(tuple(sorted((w[along], e[along]))))
        assert sorted(spans) == [(i, i + 2) for i in range(start, end, 2)]


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
    assert len(upper_floor) == 12 - 2  # two tiles cut for the stairwell opening
    assert {p.y for p in res.placements if p.category == "roof"} == {6.0}


def test_props_stay_inside_walls_and_do_not_overlap():
    res = make(theme="tavern", floors=2, rooms_per_floor=4, room_width=8, room_depth=8, prop_density=1.0, seed=1)
    for z in res.zones:
        ox, _, oz = z.offset
        props = [p.footprint() for p in z.placements if p.category == "prop"]
        assert props
        for i, (x0, z0, x1, z1) in enumerate(props):
            assert x0 >= ox + 0.25 - 1e-6 and x1 <= ox + 8 - 0.25 + 1e-6
            assert z0 >= oz + 0.25 - 1e-6 and z1 <= oz + 8 - 0.25 + 1e-6
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
