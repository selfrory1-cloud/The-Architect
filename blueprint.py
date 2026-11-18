"""Compile a single-story rectangular plan with two side-by-side tavern rooms."""
from collections import deque

from asset_tools import is_number, validate_calibration
from placement_solver import OPPOSITE, TOLERANCE, place_tile, solve


FLOOR = "Tavern_Floor_01_01"
WALL = "Tavern_Wall_Floor_01_01"
CORNER = "Tavern_Wall_Floor_Corner_01_01"
DOOR = "shugun_smallDoor_01"
STEPS = {"E": (1, 0), "N": (0, 1), "W": (-1, 0), "S": (0, -1)}
FIELDS = {
    "west_room_width", "east_room_width", "depth", "west_door_row",
    "interior_opening_row", "north_door_clearance_from_east",
}


def compile_blueprint(assets: dict, plan: dict) -> dict:
    if not isinstance(plan, dict) or set(plan) != FIELDS:
        raise ValueError(f"Blueprint must contain exactly: {', '.join(sorted(FIELDS))}")
    if any(type(value) is not int for value in plan.values()):
        raise ValueError("Blueprint measurements must be integer grid squares")
    west = plan["west_room_width"]
    east = plan["east_room_width"]
    depth = plan["depth"]
    if not all(3 <= dimension <= 20 for dimension in (west, east, depth)):
        raise ValueError("Each blueprint room dimension must be between 3 and 20 squares")
    west_row = plan["west_door_row"]
    opening_row = plan["interior_opening_row"]
    if not all(1 <= row < depth - 1 for row in (west_row, opening_row)):
        raise ValueError("West door and interior opening must be between the end rows")
    clearance = plan["north_door_clearance_from_east"]
    if not 1 <= clearance <= east - 2:
        raise ValueError("North door must leave at least one square at each end of Room B's north wall")

    for asset in (FLOOR, WALL, CORNER, DOOR):
        if asset not in assets:
            raise ValueError(f"Blueprint requires the starter asset {asset}")
        spec = assets[asset]
        errors = validate_calibration(asset, spec)
        if errors:
            raise ValueError("; ".join(errors))
        if spec["kind"] != "tile" or spec["size"] != [1, 1]:
            raise ValueError(f"{asset} must be a 1x1 tile for this blueprint")
    for asset in (WALL, CORNER):
        if assets[asset]["height"] != 2:
            raise ValueError(f"{asset} must have the starter kit's nominal height of 2 units")
    surface = assets[FLOOR].get("surface_offset")
    if not is_number(surface, positive=True):
        raise ValueError(f"{FLOOR} needs a positive calibrated surface_offset")

    width = west + east
    corners = {(0, 0): "S", (0, depth - 1): "W",
               (width - 1, 0): "E", (width - 1, depth - 1): "N"}
    doors = {(0, west_row): "S", (width - 1 - clearance, depth - 1): "W"}

    def base_piece(cell: tuple[int, int]) -> dict:
        x, z = cell
        if cell in corners:
            asset, front = CORNER, corners[cell]
        elif cell in doors:
            asset, front = FLOOR, "N"
        elif z == 0:
            asset, front = WALL, "E"
        elif z == depth - 1:
            asset, front = WALL, "W"
        elif x == width - 1:
            asset, front = WALL, "N"
        elif x == 0 or (x == west and z != opening_row):
            asset, front = WALL, "S"
        else:
            asset, front = FLOOR, "N"
        return {"id": f"base_{x}_{z}", "asset": asset, "desired_front": front, "x": 0, "z": 0}

    def socket(piece: dict, side: str) -> dict:
        matches = [port for port in piece["sockets"] if port["side"] == side]
        if len(matches) != 1:
            raise ValueError(f"{piece['id']} needs exactly one {side} socket")
        return matches[0]

    def neighbors(cell: tuple[int, int]):
        x, z = cell
        for side, (dx, dz) in STEPS.items():
            nx, nz = x + dx, z + dz
            if 0 <= nx < width and 0 <= nz < depth:
                yield side, (nx, nz)

    seed = base_piece((0, 0))
    placements = {(0, 0): seed}
    pending = deque([(0, 0)])
    while pending:
        cell = pending.popleft()
        parent = placements[cell]
        oriented_parent = place_tile(assets[parent["asset"]], parent)
        for side, neighbor in neighbors(cell):
            if neighbor in placements:
                continue
            child = base_piece(neighbor)
            oriented_child = place_tile(assets[child["asset"]], child)
            source = socket(oriented_parent, side)
            target = socket(oriented_child, OPPOSITE[side])
            child["x"], child["z"] = source["x"] - target["x"], source["z"] - target["z"]
            if any(abs(child[axis] - coord) > TOLERANCE for axis, coord in zip(("x", "z"), neighbor)):
                raise ValueError(f"{child['id']}: snapped footprint does not match the blueprint grid")
            placements[neighbor] = child
            pending.append(neighbor)

    links = [
        [piece["id"], placements[neighbor]["id"]]
        for cell, piece in placements.items()
        for side, neighbor in neighbors(cell) if side in ("E", "N")
    ]
    door_pieces = [
        {"id": f"door_{x}_{z}", "asset": DOOR, "desired_front": front,
         "x": placements[x, z]["x"], "z": placements[x, z]["z"], "y": surface}
        for (x, z), front in doors.items()
    ]
    return solve(assets, {"placements": [*placements.values(), *door_pieces], "links": links})
