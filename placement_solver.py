#!/usr/bin/env python3
"""Rule-based TaleSpire tile orientation + connection validation.

Designed for layout JSON from an LLM or Python generator. Wall/corner yaw is
calculated from calibrated asset ports and desired connections, not guessed.

TaleSpire uses X/Z for the horizontal plane, Y for height.
For tiles, x/z is the minimum corner of the rotated footprint.
Calibration: asset 'size', 'ports', and 'front' describe the asset at base_yaw.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
CARDINALS = {"N", "E", "S", "W"}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
TOLERANCE = 1e-5


def spin(direction: str, quarter_turns: int) -> str:
    """Clockwise steps, with +Z=N and +X=E."""
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown compass direction: {direction!r}")
    return DIRECTIONS[(DIRECTIONS.index(direction) + 2 * quarter_turns) % 8]


def rotated_size(width: float, depth: float, turns: int) -> tuple[float, float]:
    return (depth, width) if turns % 2 else (width, depth)


def rotate_port(port: dict, width: float, depth: float, turns: int) -> dict:
    """Rotate local port around a rectangular asset; normalize to MIN corner.

    For a +90-degree yaw, local x/z -> (z, width-x).
    This is a geometric rule for *tile footprints*, not prop origins.
    """
    x, z = float(port["x"]), float(port["z"])
    turns %= 4
    if turns == 0:
        rx, rz = x, z
    elif turns == 1:
        rx, rz = z, width - x
    elif turns == 2:
        rx, rz = width - x, depth - z
    else:
        rx, rz = depth - z, x
    return {"x": rx, "z": rz, "side": spin(port["side"], turns)}


def allowed_turns(spec: dict, placement: dict) -> list[int]:
    target = placement.get("target_ports")
    want_front = placement.get("desired_front")
    if target is not None:
        if any(side not in CARDINALS for side in target):
            raise ValueError(f"{placement['id']}: target_ports must be N,E,S,W")
        if len(set(target)) != len(target):
            raise ValueError(f"{placement['id']}: duplicate target ports")
        target = set(target)
    if want_front is not None and "front" not in spec:
        raise ValueError(f"{placement['id']}: desired_front set but asset has no calibrated 'front'")
    options = []
    for turns in range(4):
        ports = {spin(p["side"], turns) for p in spec.get("ports", [])}
        if target is not None and ports != target:
            continue
        if want_front is not None and spin(spec["front"], turns) != want_front:
            continue
        options.append(turns)
    return options


def place_tile(spec: dict, item: dict) -> dict:
    choices = allowed_turns(spec, item)
    if not choices:
        raise ValueError(f"{item['id']}: impossible orientation: target_ports={item.get('target_ports')}, "
                         f"desired_front={item.get('desired_front')}; check asset calibration or tile type")
    if len(choices) > 1:
        raise ValueError(f"{item['id']}: ambiguous orientation {choices}; add 'desired_front' "
                         f"or more specific calibrated connections")
    turns = choices[0]
    w, d = map(float, spec["size"])
    rw, rd = rotated_size(w, d, turns)
    # 'x' and 'z' are always the desired minimum corner AFTER rotation.
    # For center placement use anchor='center' and supply the actual center.
    x, z = float(item["x"]), float(item["z"])
    if item.get("anchor", "min") == "center":
        x -= rw / 2
        z -= rd / 2
    elif item.get("anchor", "min") != "min":
        raise ValueError(f"{item['id']}: tile anchor must be 'min' or 'center'")
    yaw = (float(spec.get("base_yaw", 0)) + turns * 90) % 360
    result = {"id": item["id"], "asset": item["asset"], "uuid": spec["uuid"],
              "kind": "tile", "x": x, "y": float(item.get("y", 0)), "z": z,
              "degrees": yaw, "width": rw, "depth": rd, "turns": turns}
    result["sockets"] = [
        {"x": x + (r := rotate_port(p, w, d, turns))["x"],
         "z": z + r["z"], "side": r["side"]}
        for p in spec.get("ports", [])
    ]
    return result


def place_prop(spec: dict, item: dict) -> dict:
    if "degrees" not in item:
        raise ValueError(f"{item['id']}: props need an explicit calibrated degrees value; "
                         "tile min-corner placement rules do not apply to prop origins")
    deg = float(item["degrees"])
    if abs(deg / 15 - round(deg / 15)) > TOLERANCE:
        raise ValueError(f"{item['id']}: prop degrees must be divisible by 15")
    return {"id": item["id"], "asset": item["asset"], "uuid": spec["uuid"],
            "kind": "prop", "x": float(item["x"]), "y": float(item.get("y", 0)),
            "z": float(item["z"]), "degrees": deg % 360}


def validate_links(placed: list[dict], links: list[list[str]]) -> None:
    by_id = {p["id"]: p for p in placed}
    errors = []
    for link in links:
        if len(link) != 2:
            errors.append(f"Invalid link: {link}")
            continue
        a_id, b_id = link
        if a_id not in by_id or b_id not in by_id:
            errors.append(f"Unknown asset ID in link {a_id!r} <-> {b_id!r}")
            continue
        a, b = by_id[a_id], by_id[b_id]
        if "sockets" not in a or "sockets" not in b:
            errors.append(f"Cannot validate prop connection {a_id} <-> {b_id}")
            continue
        linked = any(
            abs(pa["x"] - pb["x"]) <= TOLERANCE
            and abs(pa["z"] - pb["z"]) <= TOLERANCE
            and pa["side"] == OPPOSITE[pb["side"]]
            for pa in a["sockets"] for pb in b["sockets"]
        )
        if not linked:
            errors.append(f"{a_id} <-> {b_id}: sockets do not physically meet and face each other")
    if errors:
        raise ValueError("Connection validation FAILED:\n  " + "\n  ".join(errors))


def solve(assets: dict, layout: dict) -> dict:
    result = []
    ids = set()
    for item in layout["placements"]:
        if item["id"] in ids:
            raise ValueError(f"Duplicate placement ID {item['id']}")
        ids.add(item["id"])
        spec = assets[item["asset"]]
        if spec.get("kind", "tile") == "tile":
            result.append(place_tile(spec, item))
        elif spec["kind"] == "prop":
            result.append(place_prop(spec, item))
        else:
            raise ValueError(f"Unknown asset kind for {item['asset']}")
    validate_links(result, layout.get("links", []))
    return {"placements": result, "links_checked": len(layout.get("links", []))}


def write_slab(placed: list[dict], path: Path) -> None:
    # Optional writer, using the publicly available talespire-encoding package.
    try:
        from ts_encoding import TSSlab
    except ImportError as exc:
        raise RuntimeError("Run: python -m pip install talespire-encoding") from exc
    by_uuid = defaultdict(list)
    # The compact slab v2 positions are non-negative local coordinates.
    # Shift the whole layout together if any coordinate is negative.
    # This preserves relative geometry; world placement happens at paste-time.
    shifts = {axis: max(0.0, -min(item[axis] for item in placed)) for axis in ("x", "y", "z")} if placed else {"x": 0.0, "y": 0.0, "z": 0.0}
    for item in placed:
        guid = item["uuid"]
        if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", guid):
            raise ValueError(f"Replace the example UUID for {item['asset']!r} before exporting slabs")
        coords = {axis: item[axis] + shifts[axis] for axis in ("x", "y", "z")}
        if any(value > 655.35 for value in coords.values()):
            raise ValueError("One slab exceeds the v2 coordinate range (655.35 tile units); "
                             "split the build into smaller regional slabs")
        by_uuid[guid].append({"degrees": item["degrees"], "pos_x": coords["x"],
                              "pos_y": coords["y"], "pos_z": coords["z"]})
    slab = TSSlab()
    slab.data["layout_count"] = len(by_uuid)
    slab.data["layouts"] = [
        {"instance_count": len(instances), "instances": instances, "reserved": 0, "uuid": uuid}
        for uuid, instances in by_uuid.items()
    ]
    path.write_text(slab.encode_slab(), encoding="utf-8")


def inspect_slab(path: Path) -> None:
    try:
        from ts_encoding import TSSlab
    except ImportError as exc:
        raise RuntimeError("Run: python -m pip install talespire-encoding") from exc
    slab = TSSlab()
    raw = path.read_text(encoding="utf-8").strip().strip('`').strip()
    slab.decode_slab(raw)
    for layout in slab.data["layouts"]:
        print(f"UUID: {layout['uuid']}")
        for instance in layout["instances"]:
            print("  ", instance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    inp = sub.add_parser("inspect", help="Decode a reference TaleSpire slab (single-tile calibration)")
    inp.add_argument("path", type=Path)
    sol = sub.add_parser("solve", help="Choose tile yaw, place, validate, and optionally encode")
    sol.add_argument("--assets", required=True, type=Path)
    sol.add_argument("--layout", required=True, type=Path)
    sol.add_argument("--output", default=Path("solved_placements.json"), type=Path)
    sol.add_argument("--slab", type=Path, help="Optional .slab.txt TaleSpire clipboard string")
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            inspect_slab(args.path)
            return
        assets = json.loads(args.assets.read_text(encoding="utf-8"))
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        output = solve(assets, layout)
        args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"Valid: {len(output['placements'])} placements; {output['links_checked']} links; "
              f"saved {args.output}")
        if args.slab:
            write_slab(output["placements"], args.slab)
            print(f"Encoded TaleSpire slab: {args.slab}")
    except (KeyError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
