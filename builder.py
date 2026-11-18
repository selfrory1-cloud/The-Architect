"""Generate a TaleSpire room from a prompt using local Ollama."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from placement_solver import solve, write_slab


DEFAULT_PROMPT = (
    "Build me a 5x5 tavern room. Use the standard tavern assets, "
    "but make sure to include a 'shugun_smallDoor_01' so people can get in."
)
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:7b"
MIN_ROOM_DIMENSION = 3
MAX_ROOM_DIMENSION = 20
RECIPE_ASSET_FIELDS = ("floor_asset", "wall_asset", "corner_asset", "door_asset")


def load_smart_library(path: Path) -> dict[str, dict[str, Any]]:
    try:
        library = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} contains invalid JSON: {exc}") from exc
    if not isinstance(library, dict) or not library:
        raise ValueError(f"{path} must contain a nonempty asset object")
    for name, spec in library.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise ValueError("Each asset must have a string name and metadata object")
    return library


def system_prompt_for(library: dict[str, dict[str, Any]]) -> str:
    available_assets = json.dumps(sorted(library), ensure_ascii=True)
    return f"""You are The Architect, a thematic planner for TaleSpire.
Interpret the user's request and select a room size and semantic assets.
Do not calculate or output placement coordinates; Python constructs the layout.
Return only one JSON object with exactly these fields:
width, length, floor_asset, wall_asset, corner_asset, and optional door_asset.
width and length must be integers from {MIN_ROOM_DIMENSION} to {MAX_ROOM_DIMENSION}.
Every asset name must exactly match an entry in this list:
{available_assets}
"""


def request_recipe(
    prompt: str,
    library: dict[str, dict[str, Any]],
    *,
    model: str,
    ollama_url: str,
    timeout: float,
) -> dict[str, Any]:
    request_data = json.dumps({
        "model": model,
        "prompt": prompt,
        "system": system_prompt_for(library),
        "stream": False,
        "format": "json",
    }).encode("utf-8")
    request = Request(
        ollama_url,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), str):
        raise ValueError("Ollama response must contain a string 'response' field")
    try:
        recipe = json.loads(payload["response"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ollama returned invalid recipe JSON: {exc}") from exc
    if not isinstance(recipe, dict):
        raise ValueError("Ollama recipe must be a JSON object")
    return recipe


def validate_recipe(
    recipe: dict[str, Any],
    library: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    allowed_fields = {"width", "length", *RECIPE_ASSET_FIELDS}
    unknown_fields = set(recipe) - allowed_fields
    if unknown_fields:
        raise ValueError(f"Unknown recipe fields: {', '.join(sorted(unknown_fields))}")
    for field in ("width", "length"):
        value = recipe.get(field)
        if type(value) is not int or not MIN_ROOM_DIMENSION <= value <= MAX_ROOM_DIMENSION:
            raise ValueError(
                f"{field} must be an integer from {MIN_ROOM_DIMENSION} to {MAX_ROOM_DIMENSION}"
            )
    for field in RECIPE_ASSET_FIELDS:
        asset = recipe.get(field)
        if field == "door_asset" and asset is None:
            continue
        if not isinstance(asset, str) or asset not in library:
            raise ValueError(f"{field} must name an asset in the configured library")
    return recipe


def build_room_layout(
    assets: dict[str, dict[str, Any]],
    recipe: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Translate a validated recipe into the known-working rectangular grid layout."""
    recipe = validate_recipe(recipe, assets)
    width = recipe["width"]
    length = recipe["length"]
    floor_asset = recipe["floor_asset"]
    wall_asset = recipe["wall_asset"]
    corner_asset = recipe["corner_asset"]
    door_asset = recipe.get("door_asset")
    placements = []

    for x in range(width):
        for z in range(length):
            north = z == length - 1
            south = z == 0
            east = x == width - 1
            west = x == 0
            is_corner = (north or south) and (east or west)
            is_perimeter = north or south or east or west

            if not is_perimeter:
                placements.append({
                    "id": f"floor_{x}_{z}",
                    "asset": floor_asset,
                    "x": x,
                    "z": z,
                    "desired_front": "N",
                })
            elif is_corner:
                if south and west:
                    front = "S"
                elif south and east:
                    front = "E"
                elif north and west:
                    front = "W"
                else:
                    front = "N"
                placements.append({
                    "id": f"corner_{x}_{z}",
                    "asset": corner_asset,
                    "x": x,
                    "z": z,
                    "desired_front": front,
                })
            elif north:
                placements.append({
                    "id": f"wall_N_{x}_{z}",
                    "asset": wall_asset,
                    "x": x,
                    "z": z,
                    "desired_front": "W",
                })
            elif south:
                if x == width // 2 and door_asset:
                    placements.extend([
                        {
                            "id": f"door_floor_{x}_{z}",
                            "asset": floor_asset,
                            "x": x,
                            "z": z,
                            "desired_front": "N",
                        },
                        {
                            "id": f"door_S_{x}_{z}",
                            "asset": door_asset,
                            "x": x,
                            "z": z,
                            "desired_front": "E",
                        },
                    ])
                else:
                    placements.append({
                        "id": f"wall_S_{x}_{z}",
                        "asset": wall_asset,
                        "x": x,
                        "z": z,
                        "desired_front": "E",
                    })
            elif east:
                placements.append({
                    "id": f"wall_E_{x}_{z}",
                    "asset": wall_asset,
                    "x": x,
                    "z": z,
                    "desired_front": "N",
                })
            else:
                placements.append({
                    "id": f"wall_W_{x}_{z}",
                    "asset": wall_asset,
                    "x": x,
                    "z": z,
                    "desired_front": "S",
                })
    return {"placements": placements}


def copy_slab_to_clipboard(slab_text: str) -> str | None:
    try:
        import pyperclip
    except ImportError:
        return "Install the project dependencies to enable clipboard copying."
    try:
        pyperclip.copy(f"```{slab_text.strip()}```")
    except pyperclip.PyperclipException as exc:
        return f"Clipboard unavailable: {exc}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Natural-language build request")
    parser.add_argument("--assets", type=Path, default=Path("assets.json"))
    parser.add_argument("--output", type=Path, default=Path("generated/output_slab.txt"))
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument("--timeout", type=float, default=120.0, help="Ollama request timeout in seconds")
    parser.add_argument("--no-clipboard", action="store_true", help="Save the slab without copying it")
    args = parser.parse_args(argv)
    if not args.prompt.strip():
        parser.error("--prompt cannot be empty")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        library = load_smart_library(args.assets)
        print(f"Asking Ollama ({args.model}) for an asset recipe...")
        recipe = validate_recipe(
            request_recipe(
                args.prompt,
                library,
                model=args.model,
                ollama_url=args.ollama_url,
                timeout=args.timeout,
            ),
            library,
        )
        layout = build_room_layout(library, recipe)
        solved = solve(library, layout)
        if not solved["placements"]:
            raise ValueError("The recipe produced no placements")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_slab(solved["placements"], args.output)
        slab_text = args.output.read_text(encoding="utf-8").strip()
    except HTTPError as exc:
        print(f"Ollama returned HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as exc:
        print(f"Could not reach Ollama at {args.ollama_url}: {exc}", file=sys.stderr)
        return 1
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Generated {len(solved['placements'])} placements for {recipe['width']}x{recipe['length']}.")
    print(f"Saved TaleSpire slab to {args.output}")
    if not args.no_clipboard:
        warning = copy_slab_to_clipboard(slab_text)
        if warning:
            print(warning, file=sys.stderr)
        else:
            print("Slab copied. Paste it into TaleSpire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
