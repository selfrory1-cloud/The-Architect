"""The Architect — port-snapping physics engine.

The builder never asks the LLM for coordinates. It ingests a validated JSON recipe
(see ``query.py``) and mechanically snaps calibrated assets together through their
ports. Structural runs are assembled by aligning ports relative to each asset's pivot.

Coordinate frame: x = east, z = north, y = up. One TaleSpire tile = 1 unit.
Rotations are quarter turns ``q`` (0..3), clockwise when viewed from above.

Slab position model (verified against in-game reference slabs): the position TaleSpire
stores for an asset is the minimum (south-west) corner of its *rotated* footprint, not a
rotation pivot. Asset ``size`` and ``ports`` are given in the unrotated local footprint
``[0, w] x [0, d]``; rotating an asset keeps its footprint on the grid.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import slab

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ASSETS = ROOT / "assets.json"
DEFAULT_THEMES = ROOT / "themes.json"

# TaleSpire stores rotation in 15 degree steps; a quarter turn is 6 steps.
# Flip ROTATION_CLOCKWISE if in-game calibration shows TaleSpire turns the other way.
ROT_STEPS_PER_QUARTER = 6
ROTATION_CLOCKWISE = True

SIDES = ("N", "E", "S", "W")
_EPS = 1e-6


class BuildError(ValueError):
    pass


# --------------------------------------------------------------------------- library

@dataclass(frozen=True)
class AssetDef:
    name: str
    kind: str  # "tile" (grid collision) or "prop" (spawns exactly at origin)
    uuid: str
    size: tuple[float, float]  # (width along local x, depth along local z)
    height: float
    front: str
    ports: dict[str, tuple[float, float]]
    calibrated: bool = False

    def port(self, side: str) -> tuple[float, float]:
        try:
            return self.ports[side]
        except KeyError:
            raise BuildError(f"asset '{self.name}' has no '{side}' port; calibrate it in assets.json") from None


class Library:
    def __init__(self, assets: dict[str, AssetDef], themes: dict[str, dict]):
        self.assets = assets
        self.themes = themes

    @classmethod
    def load(cls, assets_path: Path = DEFAULT_ASSETS, themes_path: Path = DEFAULT_THEMES) -> "Library":
        raw_assets = json.loads(Path(assets_path).read_text())
        assets = {}
        for name, a in raw_assets.items():
            assets[name] = AssetDef(
                name=name,
                kind=a["kind"],
                uuid=a["uuid"],
                size=(float(a["size"][0]), float(a["size"][1])),
                height=float(a["height"]),
                front=a.get("front", "S"),
                ports={p["side"]: (float(p["x"]), float(p["z"])) for p in a.get("ports", [])},
                calibrated=bool(a.get("calibrated", False)),
            )
        themes = json.loads(Path(themes_path).read_text())
        lib = cls(assets, themes)
        lib._check_themes()
        return lib

    def _check_themes(self) -> None:
        for tname, t in self.themes.items():
            for slot in ("wall", "door", "floor", "stairs", "roof", "wall_filler"):
                if slot == "wall_filler" and not t.get(slot):
                    continue
                if t[slot] not in self.assets:
                    raise BuildError(f"theme '{tname}' references unknown {slot} asset '{t[slot]}'")
            for role, props in t.get("props", {}).items():
                for p in props:
                    if p not in self.assets:
                        raise BuildError(f"theme '{tname}' role '{role}' references unknown prop '{p}'")

    def get(self, name: str) -> AssetDef:
        try:
            return self.assets[name]
        except KeyError:
            raise BuildError(f"unknown asset '{name}'") from None

    def theme(self, name: str) -> dict:
        try:
            return self.themes[name]
        except KeyError:
            raise BuildError(f"unknown theme '{name}'") from None


# --------------------------------------------------------------------------- geometry

def rotate(x: float, z: float, q: int) -> tuple[float, float]:
    """Rotate a local offset clockwise (viewed from above) by ``q`` quarter turns."""
    for _ in range(q % 4):
        x, z = z, -x
    return x, z


def rotate_side(side: str, q: int) -> str:
    if side not in SIDES:
        return side
    return SIDES[(SIDES.index(side) + q) % 4]


@dataclass
class Placement:
    asset: AssetDef
    x: float
    y: float
    z: float
    q: int = 0
    zone: str = ""
    category: str = ""  # floor / wall / door / stairs / roof / prop

    @property
    def rot(self) -> int:
        q = self.q % 4 if ROTATION_CLOCKWISE else (-self.q) % 4
        return q * ROT_STEPS_PER_QUARTER

    def port_world(self, side: str) -> tuple[float, float]:
        lx, lz = _local_port(self.asset, side, self.q)
        return self.x + lx, self.z + lz

    def footprint(self) -> tuple[float, float, float, float]:
        w, d = self.asset.size
        if self.q % 2:
            w, d = d, w
        return self.x, self.z, self.x + w, self.z + d

    def center(self) -> tuple[float, float]:
        x0, z0, x1, z1 = self.footprint()
        return (x0 + x1) / 2, (z0 + z1) / 2

    def translated(self, dx: float, dy: float, dz: float) -> "Placement":
        return Placement(self.asset, self.x + dx, self.y + dy, self.z + dz, self.q, self.zone, self.category)


def _local_port(asset: AssetDef, side: str, q: int) -> tuple[float, float]:
    """Port offset from the rotated footprint's minimum corner (the slab position)."""
    w, d = asset.size
    corners = [rotate(cx, cz, q) for cx in (0.0, w) for cz in (0.0, d)]
    mx, mz = min(c[0] for c in corners), min(c[1] for c in corners)
    px, pz = rotate(*asset.port(side), q)
    return px - mx, pz - mz


def snap(asset: AssetDef, port: str, target: tuple[float, float], y: float, q: int, **kw) -> Placement:
    """Place ``asset`` (rotated by ``q``) so its ``port`` lands exactly on ``target``."""
    lx, lz = _local_port(asset, port, q)
    return Placement(asset, round(target[0] - lx, 4) + 0.0, y, round(target[1] - lz, 4) + 0.0, q, **kw)


def run_length(asset: AssetDef) -> float:
    (wx, wz), (ex, ez) = asset.port("W"), asset.port("E")
    return math.hypot(ex - wx, ez - wz)


def orient(asset: AssetDef, direction: tuple[float, float]) -> int:
    """Quarter turns that make the asset's W->E port axis point along ``direction``."""
    (wx, wz), (ex, ez) = asset.port("W"), asset.port("E")
    n = run_length(asset)
    for q in range(4):
        if math.dist(rotate((ex - wx) / n, (ez - wz) / n, q), direction) < 1e-6:
            return q
    raise BuildError(f"asset '{asset.name}' W/E ports are not axis-aligned")


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] - _EPS and b[0] < a[2] - _EPS and a[1] < b[3] - _EPS and b[1] < a[3] - _EPS


# --------------------------------------------------------------------------- zones

@dataclass
class ZoneSpec:
    """Everything a room prefab needs to compile itself in local space."""
    name: str
    role: str
    level: int
    width: int
    depth: int
    open_sides: set[str] = field(default_factory=set)  # shared walls owned by a neighbour
    door_sides: set[str] = field(default_factory=set)
    stairs_up: bool = False
    stair_col: int = 0  # floor-tile column the stairwell rises from (alternates per level)
    stair_hole: tuple[float, float, float, float] | None = None
    roof: bool = False
    props: list[str] = field(default_factory=list)


@dataclass
class Zone:
    spec: ZoneSpec
    placements: list[Placement]
    ports: dict[str, tuple[float, float]]
    stair_footprint: tuple[float, float, float, float] | None = None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _grow_floor(lib_floor: AssetDef, spec: ZoneSpec, y: float, category: str,
                hole: tuple[float, float, float, float] | None) -> list[Placement]:
    """Grow a floor by snapping tiles port-to-port (S->N, W->E) from a seed tile."""
    seed = Placement(lib_floor, 0.0, y, 0.0, 0, spec.name, category)
    bounds = (0.0, 0.0, float(spec.width), float(spec.depth))
    placed: dict[tuple[float, float], Placement] = {(0.0, 0.0): seed}
    frontier = [seed]
    while frontier:
        tile = frontier.pop()
        for out_port, in_port in (("N", "S"), ("E", "W")):
            nxt = snap(lib_floor, in_port, tile.port_world(out_port), y, 0, zone=spec.name, category=category)
            fx0, fz0, fx1, fz1 = nxt.footprint()
            if fx0 < -_EPS or fz0 < -_EPS or fx1 > bounds[2] + _EPS or fz1 > bounds[3] + _EPS:
                continue
            key = (nxt.x, nxt.z)
            if key not in placed:
                placed[key] = nxt
                frontier.append(nxt)
    tiles = sorted(placed.values(), key=lambda p: (p.z, p.x))
    if hole is not None:
        tiles = [t for t in tiles if not _overlaps(t.footprint(), hole)]
    return tiles


def _expand(fp: tuple[float, float, float, float], m: float) -> tuple[float, float, float, float]:
    return fp[0] - m, fp[1] - m, fp[2] + m, fp[3] + m


def _fill_pieces(length: float, wall: AssetDef, filler: AssetDef | None) -> list[AssetDef] | None:
    """Pieces that exactly fill ``length``: full walls plus at most one filler."""
    seg = run_length(wall)
    n, rem = divmod(length + _EPS, seg)
    rem -= _EPS
    if abs(rem) < 1e-6:
        return [wall] * int(n)
    if filler is not None and abs(rem - run_length(filler)) < 1e-6:
        return [filler] + [wall] * int(n)
    return None


def _wall_run(side: str, start: tuple[float, float], direction: tuple[float, float], length: float,
              wall: AssetDef, filler: AssetDef | None, door: AssetDef | None, y: float, spec: ZoneSpec,
              keep_clear: list[tuple[float, float, float, float]]) -> list[Placement]:
    """Snap a straight run W-port to E-port from ``start`` and verify it ends exactly at its corner."""
    pieces = _fill_pieces(length, wall, filler)
    if pieces is None:
        raise BuildError(f"side {side} of '{spec.name}' ({length} units) cannot be filled with "
                         f"'{wall.name}'{' or its filler' if filler else ''}")
    out: list[Placement] = []
    cursor = start
    for asset in pieces:
        p = snap(asset, "W", cursor, y, orient(asset, direction), zone=spec.name, category="wall")
        cursor = p.port_world("E")
        out.append(p)
    end = (start[0] + direction[0] * length, start[1] + direction[1] * length)
    if math.dist(cursor, end) > 1e-3:
        raise BuildError(f"wall side {side} of '{spec.name}' did not reach its corner; check port calibration")

    if door is not None:
        if abs(run_length(door) - run_length(wall)) > _EPS:
            raise BuildError(f"door '{door.name}' must span one '{wall.name}' segment")
        candidates = [i for i, p in enumerate(out) if p.asset is wall]
        mid = (len(out) - 1) / 2
        for i in sorted(candidates, key=lambda i: (abs(i - mid), i)):
            d = snap(door, "W", out[i].port_world("W"), y, orient(door, direction),
                     zone=spec.name, category="door")
            if not any(_overlaps(_expand(d.footprint(), 1.5), k) for k in keep_clear):
                out[i] = d
                break
        else:
            raise BuildError(f"no clear wall segment for a door on side {side} of '{spec.name}'")
    return out


def _column_extension(length: float, t: float, wall: AssetDef, filler: AssetDef | None) -> tuple[float, float]:
    """How far a column must reach into the perpendicular rows (bottom, top) to be fillable.

    Rows run the full room width; columns sit between them. If the gap is not fillable
    with walls (+ filler), the column is extended by up to one wall thickness into a row,
    which hides the overlap inside the junction.
    """
    for eb, et in ((0.0, 0.0), (t, 0.0), (0.0, t), (t, t)):
        if _fill_pieces(length + eb + et, wall, filler) is not None:
            return eb, et
    raise BuildError(f"cannot fill a {length}-unit wall column with '{wall.name}'")


def _build_walls(lib: Library, theme: dict, spec: ZoneSpec, y: float,
                 keep_clear: list[tuple[float, float, float, float]]) -> list[Placement]:
    """Assemble the room's walls inside its footprint ``[0, W] x [0, D]``.

    Rows (S/N) run the full width; columns (E/W) fill the gap between rows, using the
    theme's 1-unit filler when the gap is odd. Run directions follow the in-game
    reference builds: S row west->east (rot 0), E column south->north (rot 18),
    N row east->west (rot 12), W column north->south (rot 6). A room owns its N and E
    walls; it owns S/W walls only when no neighbour shares that side.
    """
    wall = lib.get(theme["wall"])
    filler = lib.get(theme["wall_filler"]) if theme.get("wall_filler") else None
    door = lib.get(theme["door"])
    t = wall.size[1]
    W, D = float(spec.width), float(spec.depth)
    own_s, own_w = "S" not in spec.open_sides, "W" not in spec.open_sides
    bottom = t if own_s else 0.0  # a south neighbour's N row occupies [-t, 0]
    top = D - t
    eb, et = _column_extension(top - bottom, t, wall, filler)
    col_len = top - bottom + eb + et

    runs = []
    if own_s:
        runs.append(("S", (0.0, t / 2), (1, 0), W))
    runs.append(("E", (W - t / 2, bottom - eb), (0, 1), col_len))
    runs.append(("N", (W, D - t / 2), (-1, 0), W))
    if own_w:
        runs.append(("W", (t / 2, top + et), (0, -1), col_len))

    out: list[Placement] = []
    for side, start, direction, length in runs:
        out += _wall_run(side, start, direction, length, wall, filler,
                         door if side in spec.door_sides else None, y, spec, keep_clear)
    return out


def compile_zone(lib: Library, theme: dict, spec: ZoneSpec, wall_height: float,
                 rng: random.Random, prop_density: float) -> Zone:
    floor_asset = lib.get(theme["floor"])
    if spec.width % floor_asset.size[0] or spec.depth % floor_asset.size[1]:
        raise BuildError(f"room size must be a multiple of the floor tile size {floor_asset.size}")

    placements = _grow_floor(floor_asset, spec, 0.0, "floor", spec.stair_hole)

    stair_fp = None
    blocked: list[tuple[float, float, float, float]] = []
    if spec.stair_hole:
        blocked.append(spec.stair_hole)
    if spec.stairs_up:
        stairs = lib.get(theme["stairs"])
        ex = spec.stair_col * floor_asset.size[0]
        entry = next((t for t in placements if t.category == "floor" and abs(t.x - ex) < _EPS and abs(t.z) < _EPS), None)
        if entry is None:
            raise BuildError(f"no floor tile for the stairwell entry in '{spec.name}'")
        st = snap(stairs, "S", entry.port_world("N"), 0.0, 0, zone=spec.name, category="stairs")
        stair_fp = st.footprint()
        if stair_fp[3] > spec.depth - floor_asset.size[1] + _EPS or stair_fp[2] > spec.width + _EPS:
            raise BuildError("room is too small for a stairwell (needs a landing tile beyond the top step)")
        placements.append(st)
        blocked.append(stair_fp)

    walls = _build_walls(lib, theme, spec, 0.0, list(blocked))
    placements += walls
    blocked += [w.footprint() for w in walls]
    if spec.stairs_up:
        blocked.append(entry.footprint())

    if spec.props and prop_density > 0:
        doors = [p.center() for p in placements if p.category == "door"]
        for tile in [p for p in placements if p.category == "floor"]:
            fp = tile.footprint()
            c = tile.center()
            if any(_overlaps(fp, b) for b in blocked) or any(math.dist(c, d) < 1.5 for d in doors):
                continue
            if rng.random() >= prop_density:
                continue
            prop = lib.get(rng.choice(spec.props))
            cand = snap(prop, "BASE", tile.port_world("TOP"), 0.0, rng.randrange(4),
                        zone=spec.name, category="prop")
            pf = cand.footprint()
            if (pf[0] < -_EPS or pf[1] < -_EPS or pf[2] > spec.width + _EPS
                    or pf[3] > spec.depth + _EPS or any(_overlaps(pf, b) for b in blocked)):
                continue
            blocked.append(pf)
            placements.append(cand)

    if spec.roof:
        roof = lib.get(theme["roof"])
        roof_spec = ZoneSpec(spec.name, spec.role, spec.level, spec.width, spec.depth)
        placements += _grow_floor(roof, roof_spec, wall_height, "roof", None)

    ports = {"W": (0.0, 0.0), "S": (0.0, 0.0), "E": (float(spec.width), 0.0), "N": (0.0, float(spec.depth))}
    return Zone(spec, placements, ports, stair_fp)


# --------------------------------------------------------------------------- assembly

@dataclass
class BuildResult:
    placements: list[Placement]
    zones: list[Zone]
    slab_code: str
    warnings: list[str]

    def stats(self) -> dict:
        by_cat: dict[str, int] = {}
        for p in self.placements:
            by_cat[p.category] = by_cat.get(p.category, 0) + 1
        return {"total": len(self.placements), "by_category": by_cat,
                "unique_assets": len({p.asset.uuid for p in self.placements}),
                "slab_chars": len(self.slab_code)}

    def preview(self) -> dict:
        levels: dict[int, list[dict]] = {}
        level_of = {z.spec.name: z.spec.level for z in self.zones}
        for p in self.placements:
            if p.category == "roof":
                continue
            x0, z0, x1, z1 = p.footprint()
            levels.setdefault(level_of.get(p.zone, 0), []).append({
                "x": x0, "z": z0, "w": x1 - x0, "d": z1 - z0,
                "category": p.category, "asset": p.asset.name, "zone": p.zone,
            })
        rooms = [{"name": z.spec.name, "role": z.spec.role, "level": z.spec.level,
                  "x": z.ports["S"][0] + z.offset[0], "z": z.ports["S"][1] + z.offset[2],
                  "w": z.spec.width, "d": z.spec.depth} for z in self.zones]
        return {"levels": {str(k): v for k, v in sorted(levels.items())}, "rooms": rooms}


def _grid(n: int) -> int:
    return max(1, math.ceil(math.sqrt(n)))


def build(recipe: dict, lib: Library | None = None) -> BuildResult:
    """Compile a validated recipe (see ``query.validate_recipe``) into placements + slab."""
    lib = lib or Library.load()
    theme = lib.theme(recipe["theme"])
    width, depth = int(recipe["room_width"]), int(recipe["room_depth"])
    floors = recipe["floors"]
    rng = random.Random(recipe.get("seed", 0))
    density = float(recipe.get("prop_density", 0.35)) if recipe.get("furnish", True) else 0.0
    wall_height = lib.get(theme["wall"]).height
    cols = _grid(len(floors[0]["rooms"]))

    zones: list[Zone] = []
    used_names: set[str] = set()
    stair_hole = None
    for level, floor in enumerate(floors):
        rooms = floor["rooms"]
        has_above = level + 1 < len(floors)
        rooms_above = len(floors[level + 1]["rooms"]) if has_above else 0
        cells = {(i // cols, i % cols) for i in range(len(rooms))}
        y = level * wall_height  # strict stacking on the wall height from the library

        # Zone-level port snapping: each room's W port snaps to its west neighbour's E
        # port; the first room of a row snaps its S port to the N port of the row below.
        offsets: dict[tuple[int, int], tuple[float, float]] = {}
        for i, room in enumerate(rooms):
            r, c = divmod(i, cols)
            if c > 0:
                ox, oz = offsets[(r, c - 1)]
                offsets[(r, c)] = (ox + width, oz)  # neighbour E port (w,0) -> our W port (0,0)
            elif r > 0:
                ox, oz = offsets[(r - 1, 0)]
                offsets[(r, c)] = (ox, oz + depth)  # neighbour N port (0,d) -> our S port (0,0)
            else:
                offsets[(r, c)] = (0.0, 0.0)

            name = room.get("name") or f"L{level + 1} Room {i + 1}"
            if name in used_names:
                name = f"{name} (L{level + 1}-{i + 1})"
            used_names.add(name)
            spec = ZoneSpec(
                name=name,
                role=room.get("role", ""),
                level=level,
                width=width,
                depth=depth,
                props=list(room.get("props", [])),
                roof=bool(recipe.get("roof", True)) and i >= rooms_above,
            )
            if (r, c - 1) in cells:
                spec.open_sides.add("W")
            if (r - 1, c) in cells:
                spec.open_sides.add("S")
            if (r, c + 1) in cells:
                spec.door_sides.add("E")
            if (r + 1, c) in cells:
                spec.door_sides.add("N")
            if level == 0 and i == 0:
                spec.door_sides.add("S")  # exterior entrance
            if i == 0:
                spec.stairs_up = has_above
                spec.stair_col = level % 2
                spec.stair_hole = stair_hole

            zone = compile_zone(lib, theme, spec, wall_height, rng, density)
            ox, oz = offsets[(r, c)]
            zone.offset = (ox, y, oz)
            zone.placements = [p.translated(ox, y, oz) for p in zone.placements]
            zones.append(zone)
            if i == 0:
                stair_hole = zone.stair_footprint

    placements = [p for z in zones for p in z.placements]
    warnings = []
    uncal = sorted({p.asset.name for p in placements if not p.asset.calibrated})
    if uncal:
        warnings.append("Uncalibrated assets (placeholder UUIDs — will not appear in TaleSpire until "
                        "calibrated in assets.json): " + ", ".join(uncal))
    code = slab.encode([slab.SlabAsset(p.asset.uuid, p.x, p.y, p.z, p.rot) for p in placements])
    return BuildResult(placements, zones, code, warnings)
