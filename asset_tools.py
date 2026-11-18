"""Lossless asset catalog migration and calibration auditing.

These commands inspect data only. They never import or execute legacy scripts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
from uuid import UUID


CARDINALS = {"N", "E", "S", "W"}
FRONTS = CARDINALS | {"NE", "SE", "SW", "NW"}
CALIBRATION_FIELDS = ("kind", "size", "height", "front", "ports", "base_yaw")


def validate_uuid(value: object, context: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{context}: uuid must be a string")
    try:
        UUID(value)
    except ValueError as exc:
        raise ValueError(f"{context}: invalid UUID {value!r}") from exc


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_assets(path: Path) -> dict:
    assets = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)
    if not isinstance(assets, dict) or not assets:
        raise ValueError(f"{path}: expected a nonempty object mapping asset names to metadata")
    for name, spec in assets.items():
        if not name or not isinstance(spec, dict):
            raise ValueError(f"{path}: asset {name!r} must have a name and metadata object")
        validate_uuid(spec.get("uuid"), name)
    return assets


def import_catalog(source: str) -> list[dict[str, str]]:
    """Read each AST dictionary entry before duplicate names can overwrite it."""
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        raise ValueError("Expected only a MASTER_ASSET_LIBRARY dictionary assignment")
    assignment = tree.body[0]
    if (
        len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Name)
        or assignment.targets[0].id != "MASTER_ASSET_LIBRARY"
        or not isinstance(assignment.value, ast.Dict)
    ):
        raise ValueError("Expected only a MASTER_ASSET_LIBRARY dictionary assignment")
    records = []
    uuids = set()
    for key, value in zip(assignment.value.keys, assignment.value.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or not key.value:
            raise ValueError("Catalog keys must be nonempty literal strings")
        if not isinstance(value, ast.Dict):
            raise ValueError(f"{key.value}: expected literal metadata dictionary")
        fields = []
        for field in value.keys:
            if not isinstance(field, ast.Constant) or not isinstance(field.value, str):
                raise ValueError(f"{key.value}: metadata keys must be literal strings")
            fields.append(field.value)
        if len(set(fields)) != len(fields):
            raise ValueError(f"{key.value}: duplicate metadata field")
        spec = ast.literal_eval(value)
        if set(spec) != {"uuid", "name", "category"} or any(
            not isinstance(item, str) or not item.strip() for item in spec.values()
        ):
            raise ValueError(f"{key.value}: expected nonempty uuid, name, and category strings")
        validate_uuid(spec["uuid"], key.value)
        identity = UUID(spec["uuid"])
        if identity in uuids:
            raise ValueError(f"{key.value}: duplicate UUID {identity}")
        uuids.add(identity)
        records.append({"asset_key": key.value, **spec})
    if not records:
        raise ValueError("Catalog must contain at least one asset")
    return records


def is_number(value: object, *, positive: bool = False) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and (not positive or value > 0)
    )


def validate_calibration(name: str, spec: dict) -> list[str]:
    errors = []
    if not isinstance(spec.get("kind"), str) or spec["kind"] not in {"tile", "prop"}:
        errors.append("kind must explicitly be tile or prop")
    size = spec.get("size")
    valid_size = (
        isinstance(size, list)
        and len(size) == 2
        and all(is_number(value, positive=True) for value in size)
    )
    if not valid_size:
        errors.append("size must contain two positive finite numbers")
    if not is_number(spec.get("height"), positive=True):
        errors.append("height must be a positive finite number")
    if not isinstance(spec.get("front"), str) or spec["front"] not in FRONTS:
        errors.append("front must be a compass direction")
    if not is_number(spec.get("base_yaw", 0)):
        errors.append("base_yaw must be a finite number")
    ports = spec.get("ports")
    if not isinstance(ports, list):
        errors.append("ports must be an explicit list")
    else:
        if spec.get("kind") == "tile" and not ports:
            errors.append("structural tiles need calibrated connection ports")
        seen = set()
        for index, port in enumerate(ports):
            if (
                not isinstance(port, dict)
                or not isinstance(port.get("side"), str)
                or port["side"] not in CARDINALS
                or not is_number(port.get("x"))
                or not is_number(port.get("z"))
            ):
                errors.append(f"port {index} needs a cardinal side and finite x/z coordinates")
                continue
            identity = (port["side"], port["x"], port["z"])
            if identity in seen:
                errors.append(f"port {index} duplicates another port")
            seen.add(identity)
            if valid_size and not (0 <= port["x"] <= size[0] and 0 <= port["z"] <= size[1]):
                errors.append(f"port {index} is outside the calibrated footprint")
    return [f"{name}: {error}" for error in errors]


def audit_assets(assets: dict, comparison: dict | None = None) -> dict:
    errors = [
        error for name, spec in assets.items() for error in validate_calibration(name, spec)
    ]
    conflicts = []
    by_uuid = defaultdict(list)
    for name, spec in (comparison or {}).items():
        by_uuid[UUID(spec["uuid"])].append((name, spec))
    for name, spec in assets.items():
        for other_name, other in by_uuid[UUID(spec["uuid"])]:
            differences = {}
            for field in CALIBRATION_FIELDS:
                value = spec.get(field, 0 if field == "base_yaw" else None)
                other_value = other.get(field, 0 if field == "base_yaw" else None)
                if field == "ports" and isinstance(value, list) and isinstance(other_value, list):
                    same = sorted(json.dumps(port, sort_keys=True) for port in value) == sorted(
                        json.dumps(port, sort_keys=True) for port in other_value
                    )
                else:
                    same = value == other_value
                if not same:
                    differences[field] = {"current": value, "comparison": other_value}
            if differences:
                conflicts.append(
                    {"asset": name, "comparison_asset": other_name, "differences": differences}
                )
    return {
        "asset_count": len(assets),
        "errors": errors,
        "conflicts": conflicts,
        "in_game_verified": False,
        "notice": "Schema checks cannot verify physical dimensions, pivots, or in-game appearance.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("import-catalog", help="Preserve every legacy catalog entry")
    migrate.add_argument("source", type=Path)
    migrate.add_argument("--output", required=True, type=Path)
    audit = commands.add_parser("audit-assets", help="Validate and compare asset calibrations")
    audit.add_argument("assets", type=Path)
    audit.add_argument("--compare", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "import-catalog":
            records = import_catalog(args.source.read_text(encoding="utf-8-sig"))
            counts = Counter(record["asset_key"] for record in records)
            summary = {
                "asset_count": len(records),
                "unique_names": len(counts),
                "recovered_entries": sum(count - 1 for count in counts.values()),
            }
            # Exclusive creation protects original references and existing exports.
            with args.output.open("x", encoding="utf-8") as output:
                output.write(json.dumps(records, indent=2, ensure_ascii=True) + "\n")
            print(json.dumps(summary, indent=2))
            return 0
        assets = load_assets(args.assets)
        comparison = load_assets(args.compare) if args.compare else None
        report = audit_assets(assets, comparison)
        print(json.dumps(report, indent=2))
        return 1 if report["errors"] or report["conflicts"] else 0
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
