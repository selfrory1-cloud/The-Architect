"""Read-only searchable index of asset names and UUIDs."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "master_asset_catalog.json"


def search_catalog(query: str = "", category: str = "", limit: int = 50, offset: int = 0,
                   calibrated_uuids: set[str] | None = None) -> dict:
    """Return one page of catalog records without treating them as placement-ready assets."""
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assets = raw["assets"]
    categories = sorted({asset["category"] for asset in assets}, key=str.casefold)
    needle = query.casefold().strip()
    matches = [
        asset for asset in assets
        if (not category or asset["category"] == category)
        and (not needle or any(needle in asset[field].casefold()
                               for field in ("key", "name", "category", "uuid")))
    ]
    known = calibrated_uuids or set()
    entries = [{**asset, "calibrated": asset["uuid"] in known}
               for asset in matches[offset:offset + limit]]
    return {
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "categories": categories,
        "entries": entries,
    }
