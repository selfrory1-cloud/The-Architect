# The Architect
Procedural TaleSpire builder. Fill in a web form (theme, floors, rooms per floor, room size…) and get a
ready-to-paste TaleSpire slab code (`H4sIA...`) plus a floor-plan preview.

## Quick start
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip ...
.venv/bin/python app.py                           # open http://127.0.0.1:5000
```
Copy the slab code from the page and paste it into TaleSpire (Ctrl+V).

Environment options: `PORT`, `HOST`, `FLASK_DEBUG=1`, and for the optional LLM step
`OLLAMA_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` (default `llama3.1:8b`), `OLLAMA_TIMEOUT`.

## Architecture
| File | Role |
|------|------|
| `app.py`, `templates/`, `static/` | Flask web UI + JSON API (`/api/generate`, `/api/decode`, `/api/library`) |
| `architect/query.py` | Form → validated JSON recipe. Optional Ollama LLM only names rooms / picks props — never coordinates |
| `architect/builder.py` | Port-snapping engine: floor growth, clockwise wall chains, doors, stairwells, multi-level stacking, zone prefabs |
| `architect/slab.py` | TaleSpire slab v2 encoder/decoder ([spec](https://github.com/Bouncyrock/DumbSlabStats/blob/master/format.md)) |
| `assets.json` | Calibrated asset library (uuid, kind, size, height, ports) |
| `themes.json` | Themes → wall/door/floor/stairs/roof assets, room roles and prop sets |

How a build is assembled:
1. Each room is compiled as a local **zone prefab**: floor tiles grow by snapping S→N / W→E ports; walls are
   chained around the perimeter, each wall's `W` port snapped to the previous wall's `E` port (the chain must
   reach each of the four corners exactly or the build fails); doors must span the same distance as the
   wall segment they replace; props snap their `BASE` port to a floor tile's `TOP` port.
2. Zones snap together through zone ports (W↔E, S↔N). Shared walls are placed once, with an interior door.
3. Levels stack at `level × wall height` (from `assets.json`). Room 1 of each level gets a stairwell
   (alternating columns) and the level above gets a matching floor opening. Rooms with nothing above get a flat roof.
4. All zones are merged and encoded into one slab.

## Calibrating assets
Only `Tavern_Wall_01` has a real UUID. Every other asset has a placeholder UUID and `"calibrated": false`
(the site warns about these; they won't appear in TaleSpire). To calibrate:
1. In TaleSpire, place the asset alone, select and copy it.
2. Paste the slab into the **Slab decoder** on the site to read its UUID.
3. Update the entry in `assets.json`: `uuid`, `size`, `height`, `ports`, and set `"calibrated": true`.

The clockwise rotation direction is confirmed: a 90-degree clockwise turn advances rotation by 6 modulo 24.
The supplied three-wall reference also establishes `Tavern_Wall_01`'s connection ports at local
`W=(-0.5, 0)` and `E=(1.5, 0)`, rather than the assumed `(0, 0.25)` and `(2, 0.25)`.
The preview draws straight walls and doors around their connection line using the configured thickness.
The Tavern reference slabs identify the dedicated corner (`ce1e617e-f031-4011-b64e-ba262082b20f`)
and the 1-unit filler (`82b03fd9-1afb-463f-a3f8-2f30204d6561`). The straight-wall reference
confirms that the filler bridges a 1-unit gap at rotation step 6 when the long walls are at step 12.
Neither piece is used in generation yet. Room dimensions still need to be even because the floor
uses 2×2 tiles. Placeholder assets can appear as missing-asset markers in TaleSpire.

## Tests
```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
