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
| `architect/builder.py` | Port-snapping engine: floor growth, anchored wall runs with fillers, doors, stairwells, multi-level stacking, zone prefabs |
| `architect/slab.py` | TaleSpire slab v2 encoder/decoder ([spec](https://github.com/Bouncyrock/DumbSlabStats/blob/master/format.md)) |
| `assets.json` | Calibrated asset library (uuid, kind, size, height, ports) |
| `themes.json` | Themes → wall/door/floor/stairs/roof assets, room roles and prop sets |

How a build is assembled:
1. Each room is compiled as a local **zone prefab**: floor tiles grow by snapping S→N / W→E ports. Walls sit
   *inside* the room footprint: the S and N rows run the full width, and the E and W columns fill the gap between
   them. Each run snaps every wall's `W` port to the previous `E` port and must end exactly at its corner, or the
   build fails. Odd-length columns use the theme's 1-unit `wall_filler`. A door replaces a full wall segment,
   using a filler beside it if needed. Props snap their `BASE` port to a floor tile's `TOP` port and never overlap walls.
2. Zones snap together through zone ports (W↔E, S↔N). Shared walls are placed once, with an interior door.
3. Levels stack at `level × wall height` (from `assets.json`). Room 1 of each level gets a stairwell
   (alternating columns) and the level above gets a matching floor opening. Rooms with nothing above get a flat roof.
4. All zones are merged and encoded into one slab.

## Calibrating assets
Tavern, House, and Shop use real floor, door, wall, and filler assets. Their 1×1 floor tiles start half a
tile inside the south and north rows, and the 1-unit door and filler together replace a 2-unit wall.
Roofs, stairs, and furnishings are still placeholders. To generate a single-floor room without those
markers, uncheck **Furnish rooms** and **Flat roof**. To calibrate more assets:
1. In TaleSpire, place the asset alone, select and copy it.
2. Paste the slab into the **Slab decoder** on the site to read its UUID.
3. Update the entry in `assets.json`: `uuid`, `size`, `height`, `ports`, and set `"calibrated": true`.

Geometry, verified against in-game reference slabs:
- **Slab position = minimum (south-west) corner of the asset's rotated footprint**, not a pivot point. Asset
  `size` and `ports` in `assets.json` are measured in the unrotated footprint `[0, w] × [0, d]`.
- A 90-degree clockwise turn advances rotation by 6 (mod 24). Wall runs use S row 0, W column 6, N row 12,
  E column 18, so every wall faces into the room.
- `Tavern_Wall_01` is 2 × 0.5 with ports `W=(0, 0.25)` and `E=(2, 0.25)`. `Tavern_Wall_Filler_01`
  (`82b03fd9-…`) is 0.5 × 1 with ports `W=(0.25, 0)` and `E=(0.25, 1)`.
- Where an inner column meets a shared row between stacked rooms, the column reaches 0.5 into that row,
  which gives a small hidden T-junction overlap.

The dedicated corner piece (`ce1e617e-f031-4011-b64e-ba262082b20f`) is not used yet. Room dimensions must be
even to fit the 2-unit wall runs. Placeholder assets can appear as missing-asset markers in TaleSpire.

## Tests
```bash
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

On Windows CMD, replace `.venv/bin/python` with `.venv\Scripts\python.exe`.
GitHub Actions runs the same suite on Linux and Windows on pull requests and pushes to `main`.

The [8×8 Tavern](tests/fixtures/slabs/tavern-8x8.json) and
[22×8 Tavern](tests/fixtures/slabs/tavern-22x8.json) are correctness benchmarks, not speed benchmarks.
They record the form inputs and reference slab exports. The test compares decoded UUIDs, positions,
rotations, and duplicate counts, not gzip bytes. Only the two documented markers are excluded from
the 8×8 reference; nothing is filtered out of generated output.

For layout changes, use a short-lived branch and a focused pull request. Add a reference fixture
for new layouts once they have been checked in TaleSpire. Only update an existing reference for an
intentional placement change, and explain the in-game check in the pull request.
