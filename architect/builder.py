"""The Architect — port-snapping physics engine.

The builder never asks the LLM for coordinates. It ingests a validated JSON recipe
(see ``query.py``) and mechanically snaps calibrated assets together through their
ports. Structural runs are assembled by aligning ports relative to each asset's pivot.

Coordinate frame: x = east, z = north, y = up. One TaleSpire tile = 1 unit.
Rotations are quarter turns ``q`` (0..3) about the asset's origin, clockwise when
viewed from above.
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
            for slot in ("wall", "door", "floor", "stairs", "roof"):
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
        px, pz = rotate(*self.asset.port(side), self.q)
        return self.x + px, self.z + pz

    def footprint(self) -> tuple[float, float, float, float]:
        w, d = self.asset.size
        x0, z0 = (0.0, 0.0)
        if self.category in ("wall", "door"):
            x0, center_z = self.asset.port("W")
            w = self.asset.port("E")[0] - x0
            z0 = center_z - d / 2
        pts = [rotate(cx, cz, self.q) for cx in (x0, x0 + w) for cz in (z0, z0 + d)]
        xs = [self.x + p[0] for p in pts]
        zs = [self.z + p[1] for p in pts]
        return min(xs), min(zs), max(xs), max(zs)

    def center(self) -> tuple[float, float]:
        x0, z0, x1, z1 = self.footprint()
        return (x0 + x1) / 2, (z0 + z1) / 2

    def translated(self, dx: float, dy: float, dz: float) -> "Placement":
        return Placement(self.asset, self.x + dx, self.y + dy, self.z + dz, self.q, self.zone, self.category)


def snap(asset: AssetDef, port: str, target: tuple[float, float], y: float, q: int, **kw) -> Placement:
    """Place ``asset`` (rotated by ``q``) so its ``port`` lands exactly on ``target``."""
    px, pz = rotate(*asset.port(port), q)
    return Placement(asset, round(target[0] - px, 4), y, round(target[1] - pz, 4), q, **kw)


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


def _chain_walls(lib: Library, theme: dict, spec: ZoneSpec, y: float,
                 keep_clear: list[tuple[float, float, float, float]]) -> list[Placement]:
    """Walk the room perimeter clockwise, snapping each wall's W port to the previous E port.

    The chain starts at the south-east corner. Each side is a run of walls with a fixed
    rotation chosen so the wall front faces into the room; turning a corner only changes
    the rotation, the snap target is still the previous wall's E port. Doors go on the
    segment closest to the middle of a side whose approach is not blocked (``keep_clear``).
    """
    wall = lib.get(theme["wall"])
    door = lib.get(theme["door"])
    west, east = wall.port("W"), wall.port("E")
    seg = east[0] - west[0]
    if seg <= _EPS or not math.isclose(east[1], west[1], abs_tol=_EPS):
        raise BuildError(f"wall '{wall.name}' must have straight W and E ports")
    runs = (("S", 2, spec.width), ("W", 3, spec.depth), ("N", 0, spec.width), ("E", 1, spec.depth))
    cursor = (float(spec.width), 0.0)
    out: list[Placement] = []
    for side, q, length in runs:
        count = int(round(length / seg))
        if not math.isclose(count * seg, length, abs_tol=_EPS):
            raise BuildError(f"wall '{wall.name}' cannot fill side {side} of '{spec.name}' exactly")
        dx, dz = rotate(length, 0, q)
        end = (cursor[0] + dx, cursor[1] + dz)
        door_idx = -1
        if side in spec.door_sides:
            dw, de = door.port("W"), door.port("E")
            if math.dist((de[0] - dw[0], de[1] - dw[1]), (seg, 0)) > _EPS:
                raise BuildError(f"door '{door.name}' must span one '{wall.name}' segment")
            probe, segs = cursor, []
            for _ in range(count):
                p = snap(door, "W", probe, y, q, category="door")
                segs.append(p.footprint())
                probe = p.port_world("E")
            for i in sorted(range(count), key=lambda i: (abs(i - (count - 1) / 2), i)):
                if not any(_overlaps(_expand(segs[i], 1.5), k) for k in keep_clear):
                    door_idx = i
                    break
            if door_idx < 0:
                raise BuildError(f"no clear wall segment for a door on side {side} of '{spec.name}'")
        for i in range(count):
            asset = door if i == door_idx else wall
            p = snap(asset, "W", cursor, y, q, zone=spec.name, category="door" if i == door_idx else "wall")
            cursor = p.port_world("E")
            if side not in spec.open_sides:
                out.append(p)
        if math.dist(cursor, end) > 1e-3:
            raise BuildError(f"wall side {side} of '{spec.name}' did not reach its corner; check port calibration")
        cursor = end
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

    placements += _chain_walls(lib, theme, spec, 0.0, list(blocked))
    if spec.stairs_up:
        blocked.append(entry.footprint())

    if spec.props and prop_density > 0:
        wall_thickness = lib.get(theme["wall"]).size[1]
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
            half = wall_thickness / 2
            if (pf[0] < half - _EPS or pf[1] < half - _EPS or pf[2] > spec.width - half + _EPS
                    or pf[3] > spec.depth - half + _EPS or any(_overlaps(pf, b) for b in blocked)):
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
