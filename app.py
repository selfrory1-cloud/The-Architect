"""The Architect — web front end.

Run:  python app.py   (then open http://localhost:5000)
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from architect import slab
from architect.builder import BuildError, Library, build
from architect.query import LIMITS, OLLAMA_MODEL, RecipeError, build_recipe

app = Flask(__name__)


def library() -> Library:
    # Reloaded per request so edits to assets.json / themes.json apply without a restart.
    return Library.load()


@app.get("/")
def index():
    lib = library()
    themes = {k: v["label"] for k, v in lib.themes.items()}
    return render_template("index.html", themes=themes, limits=LIMITS, ollama_model=OLLAMA_MODEL)


@app.post("/api/generate")
def api_generate():
    form = request.get_json(silent=True) or request.form.to_dict()
    lib = library()
    try:
        recipe, notes = build_recipe(form, lib)
        result = build(recipe, lib)
    except RecipeError as exc:
        return jsonify({"ok": False, "errors": exc.errors}), 400
    except (BuildError, slab.SlabError) as exc:
        return jsonify({"ok": False, "errors": [str(exc)]}), 400
    return jsonify({
        "ok": True,
        "slab": result.slab_code,
        "recipe": recipe,
        "stats": result.stats(),
        "warnings": result.warnings,
        "notes": notes,
        "preview": result.preview(),
    })


@app.post("/api/decode")
def api_decode():
    code = (request.get_json(silent=True) or {}).get("slab", "")
    try:
        assets = slab.decode(code)
    except slab.SlabError as exc:
        return jsonify({"ok": False, "errors": [str(exc)]}), 400
    known = {a.uuid: name for name, a in library().assets.items()}
    summary: dict[str, dict] = {}
    for a in assets:
        s = summary.setdefault(a.uuid, {"uuid": a.uuid, "library_name": known.get(a.uuid), "count": 0, "instances": []})
        s["count"] += 1
        s["instances"].append({"x": a.x, "y": a.y, "z": a.z, "rot": a.rot})
    return jsonify({"ok": True, "total": len(assets), "assets": list(summary.values())})


@app.get("/api/library")
def api_library():
    lib = library()
    return jsonify({
        "themes": lib.themes,
        "assets": {n: {"kind": a.kind, "uuid": a.uuid, "calibrated": a.calibrated,
                       "size": a.size, "height": a.height} for n, a in lib.assets.items()},
    })


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
