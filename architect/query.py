"""The Architect — LLM "thematic seed" layer and recipe schema.

The LLM is only an interior designer: it may name rooms, pick room roles and choose
props *from the library*. It never produces coordinates. All numeric structure
(floors, rooms, room size) comes from the web form and is authoritative.

Recipe schema (output of ``build_recipe``, input of ``builder.build``)::

    {
      "theme": "tavern",
      "room_width": 8, "room_depth": 8,          # grid units, multiples of 2
      "roof": true, "furnish": true, "prop_density": 0.35, "seed": 42,
      "floors": [
        {"rooms": [{"name": "Tavern Entrance", "role": "Tavern Entrance",
                    "props": ["Bench_01", "Barrel_01"]}, ...]},
        ...
      ]
    }
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .builder import Library

LIMITS = {
    "floors": (1, 5),
    "rooms_per_floor": (1, 12),
    "room_width": (4, 40),
    "room_depth": (4, 40),
}
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "60"))


class RecipeError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _int(form: dict, key: str, default: int, errors: list[str]) -> int:
    try:
        return int(form.get(key, default))
    except (TypeError, ValueError):
        errors.append(f"{key} must be a whole number")
        return default


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "on", "yes"}
    return bool(v)


def build_recipe(form: dict, lib: Library) -> tuple[dict, list[str]]:
    """Turn consistently-formatted form inputs into a validated recipe.

    Returns ``(recipe, notes)``; raises ``RecipeError`` on invalid input.
    """
    errors: list[str] = []
    notes: list[str] = []
    theme_name = str(form.get("theme", "tavern")).lower()
    if theme_name not in lib.themes:
        errors.append(f"unknown theme '{theme_name}' (choose from {', '.join(lib.themes)})")
    floors = _int(form, "floors", 1, errors)
    rooms_per_floor = _int(form, "rooms_per_floor", 1, errors)
    width = _int(form, "room_width", 8, errors)
    depth = _int(form, "room_depth", 8, errors)
    seed = _int(form, "seed", 0, errors)
    try:
        density = float(form.get("prop_density", 0.35))
    except (TypeError, ValueError):
        errors.append("prop_density must be a number")
        density = 0.35
    if errors:
        raise RecipeError(errors)

    recipe = {
        "theme": theme_name,
        "room_width": width,
        "room_depth": depth,
        "roof": _bool(form.get("roof", True)),
        "furnish": _bool(form.get("furnish", True)),
        "prop_density": density,
        "seed": seed,
        "floors": default_floors(lib.themes[theme_name], floors, rooms_per_floor),
    }

    description = str(form.get("description", "")).strip()
    if description and _bool(form.get("use_llm", False)):
        try:
            recipe = apply_llm_theming(recipe, description, lib)
            notes.append(f"Room names/props themed by LLM ({OLLAMA_MODEL}).")
        except LLMUnavailable as exc:
            notes.append(f"LLM unavailable ({exc}); used theme defaults.")

    validate_recipe(recipe, lib)
    return recipe, notes


def default_floors(theme: dict, floors: int, rooms_per_floor: int) -> list[dict]:
    out = []
    for level in range(floors):
        roles = theme["ground_roles"] if level == 0 else theme["upper_roles"]
        rooms = []
        for i in range(rooms_per_floor):
            role = roles[i % len(roles)]
            n = sum(1 for r in rooms if r["role"] == role) + 1
            name = role if n == 1 else f"{role} {n}"
            if level > 0:
                name = f"L{level + 1} {name}"
            rooms.append({"name": name, "role": role, "props": list(theme["props"].get(role, []))})
        out.append({"rooms": rooms})
    return out


def validate_recipe(recipe: dict, lib: Library) -> None:
    errors: list[str] = []
    if recipe.get("theme") not in lib.themes:
        errors.append(f"unknown theme '{recipe.get('theme')}'")
    floors = recipe.get("floors") or []
    lo, hi = LIMITS["floors"]
    if not lo <= len(floors) <= hi:
        errors.append(f"floors must be between {lo} and {hi}")
    for key in ("room_width", "room_depth"):
        v = recipe.get(key)
        lo, hi = LIMITS[key]
        if not isinstance(v, int) or not lo <= v <= hi:
            errors.append(f"{key} must be between {lo} and {hi}")
        elif v % 2:
            errors.append(f"{key} must be even (walls and floor tiles are 2 units wide)")
    if len(floors) > 1:
        if isinstance(recipe.get("room_depth"), int) and recipe["room_depth"] < 8:
            errors.append("multi-floor builds need room_depth >= 8 to fit a stairwell and landing")
        if isinstance(recipe.get("room_width"), int) and recipe["room_width"] < 4:
            errors.append("multi-floor builds need room_width >= 4")
    if not 0 <= float(recipe.get("prop_density", 0)) <= 1:
        errors.append("prop_density must be between 0 and 1")
    prev = None
    lo, hi = LIMITS["rooms_per_floor"]
    for level, f in enumerate(floors):
        rooms = f.get("rooms") or []
        if not lo <= len(rooms) <= hi:
            errors.append(f"floor {level + 1}: rooms must be between {lo} and {hi}")
        if prev is not None and len(rooms) > prev:
            errors.append(f"floor {level + 1} has more rooms than the floor below it")
        prev = len(rooms)
        for r in rooms:
            for p in r.get("props", []):
                if p not in lib.assets or lib.assets[p].kind != "prop":
                    errors.append(f"room '{r.get('name')}': unknown prop '{p}'")
    if errors:
        raise RecipeError(errors)


# --------------------------------------------------------------------------- LLM

class LLMUnavailable(RuntimeError):
    pass


SYSTEM_PROMPT = """You are the interior designer for a TaleSpire building generator.
You NEVER output coordinates, sizes or positions. You only name rooms and choose props.
Respond with JSON only, matching exactly:
{"floors": [{"rooms": [{"name": str, "role": str, "props": [str, ...]}]}]}
Rules:
- Return exactly the number of floors and rooms per floor you are given.
- "role" must be one of the allowed roles.
- "props" must only contain names from the allowed props list (0-4 items).
- Floor 1, room 1 is the building entrance and contains the stairwell."""


def _llm_prompt(recipe: dict, description: str, lib: Library) -> str:
    theme = lib.themes[recipe["theme"]]
    roles = sorted(set(theme["ground_roles"]) | set(theme["upper_roles"]))
    props = sorted(n for n, a in lib.assets.items() if a.kind == "prop")
    shape = [len(f["rooms"]) for f in recipe["floors"]]
    return (
        f"Theme: {theme['label']}\n"
        f"User description: {description}\n"
        f"Floors: {len(shape)}; rooms per floor (ground first): {shape}\n"
        f"Allowed roles: {json.dumps(roles)}\n"
        f"Allowed props: {json.dumps(props)}\n"
        f"Current default plan: {json.dumps({'floors': recipe['floors']})}"
    )


def call_ollama(prompt: str) -> dict:
    body = json.dumps({
        "model": OLLAMA_MODEL, "system": SYSTEM_PROMPT, "prompt": prompt,
        "format": "json", "stream": False, "options": {"temperature": 0.4},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            payload = json.loads(resp.read())
        return json.loads(payload["response"])
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, OSError) as exc:
        raise LLMUnavailable(str(exc)) from exc


def apply_llm_theming(recipe: dict, description: str, lib: Library, llm=call_ollama) -> dict:
    """Merge LLM room names/roles/props into the recipe, keeping the form's structure.

    Anything the LLM gets wrong (unknown roles/props, wrong counts) falls back to defaults.
    """
    data = llm(_llm_prompt(recipe, description, lib))
    theme = lib.themes[recipe["theme"]]
    roles = set(theme["ground_roles"]) | set(theme["upper_roles"])
    llm_floors = data.get("floors") if isinstance(data, dict) else None
    if not isinstance(llm_floors, list):
        raise LLMUnavailable("LLM returned an unexpected shape")
    out_floors = []
    for level, floor in enumerate(recipe["floors"]):
        llm_rooms = []
        if level < len(llm_floors) and isinstance(llm_floors[level], dict):
            llm_rooms = llm_floors[level].get("rooms") or []
        rooms = []
        for i, default in enumerate(floor["rooms"]):
            room = dict(default)
            cand = llm_rooms[i] if i < len(llm_rooms) and isinstance(llm_rooms[i], dict) else {}
            if isinstance(cand.get("name"), str) and cand["name"].strip():
                room["name"] = cand["name"].strip()[:60]
            if cand.get("role") in roles:
                room["role"] = cand["role"]
                room["props"] = list(theme["props"].get(cand["role"], []))
            props = [p for p in cand.get("props", []) if isinstance(p, str)
                     and p in lib.assets and lib.assets[p].kind == "prop"]
            if props:
                room["props"] = props[:4]
            rooms.append(room)
        out_floors.append({"rooms": rooms})
    return {**recipe, "floors": out_floors}
