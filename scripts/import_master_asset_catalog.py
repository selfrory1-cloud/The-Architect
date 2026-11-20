#!/usr/bin/env python3
"""Safely import a literal MASTER_ASSET_LIBRARY assignment as catalog JSON."""
from __future__ import annotations

import argparse
import ast
import json
import uuid
from pathlib import Path


def extract_catalog(source: str) -> list[dict[str, str]]:
    """Parse catalog literals without importing or executing the input Python file."""
    module = ast.parse(source)
    matches = []
    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == "MASTER_ASSET_LIBRARY" for target in targets):
            matches.append(node.value)
    if len(matches) != 1 or not isinstance(matches[0], ast.Dict):
        raise ValueError("source must contain exactly one literal MASTER_ASSET_LIBRARY dictionary")

    assets = []
    seen_uuids: set[str] = set()
    for key_node, value_node in zip(matches[0].keys, matches[0].values):
        if key_node is None:
            raise ValueError("dictionary unpacking is not supported")
        key, value = ast.literal_eval(key_node), ast.literal_eval(value_node)
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError("each catalog entry must have a string key and literal object")
        try:
            asset_uuid = str(uuid.UUID(value["uuid"]))
            name, category = value["name"], value["category"]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"invalid catalog entry {key!r}: expected uuid, name, and category") from exc
        if not isinstance(name, str) or not isinstance(category, str):
            raise ValueError(f"invalid catalog entry {key!r}: name and category must be strings")
        if asset_uuid in seen_uuids:
            raise ValueError(f"duplicate asset UUID {asset_uuid}")
        seen_uuids.add(asset_uuid)
        assets.append({"key": key, "name": name, "category": category, "uuid": asset_uuid})
    return assets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="master_asset_library.py from the local session")
    parser.add_argument("--output", type=Path, default=Path("data/master_asset_catalog.json"))
    args = parser.parse_args()

    assets = extract_catalog(args.source.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({
            "schema_version": 1,
            "source": "User-provided MASTER_ASSET_LIBRARY literal",
            "entry_count": len(assets),
            "assets": assets,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Imported {len(assets)} catalog entries to {args.output}")


if __name__ == "__main__":
    main()
