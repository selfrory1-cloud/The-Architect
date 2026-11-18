# The Architect

A work-in-progress natural-language-to-TaleSpire generation engine.

## Architecture

The LLM selects themes, semantic assets, dimensions, and eventually room zones.
Python owns port-snapping, collision checks, rotation, and height calculations.
The LLM must not supply raw placement coordinates. Structural assets must not
be globally reclassified as props to hide placement errors.

The local single-room baseline is confirmed in TaleSpire. The first multi-room
blueprint uses the same starter kit, with in-game junction verification pending.

## Current status

The original scripts and supplied examples are preserved unchanged in
`reference/original/`. You confirmed that the original `builder.py` generates a
room you can paste into TaleSpire. The root-level `builder.py` carries that
rectangular-grid workflow forward with a prompt argument, validated Ollama
recipes, bounded dimensions, configurable local model/URL, and explicit errors.

The starter library contains four calibrated assets. The separate master catalog
has 2,431 UUIDs, including 445 entries hidden by duplicate Python dictionary
keys; the lossless import/audit tool is available below. A large calibrated,
theme-agnostic asset library, general multi-room generation, and a web interface
remain future work.

Door placement uses the floor asset's explicit `surface_offset` rather than
placing a door at the floor tile's base Y. The starter value is `0.5`, consistent
with the supplied room reference slab's half-unit floor-to-wall elevation.
That door elevation was confirmed in TaleSpire for this floor and door pair;
other assets still require their own calibration.

Python 3.10+ is required. Install runtime dependencies in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` instead.

Generate and paste a room:

```sh
python builder.py --prompt "A 5 by 5 tavern with a south entrance"
```

The default model remains `qwen2.5:7b`. Set `OLLAMA_MODEL` or pass `--model` to
choose another local Ollama model; the endpoint can be configured with
`OLLAMA_URL` or `--ollama-url`. The previous 5x5 tavern prompt is used when
`--prompt` is omitted. Generated slabs are saved under `generated/` and copied
to the clipboard when available. Run with `--no-clipboard` to only save.

### Compile the measured two-room tavern

From the project directory on Windows, run:

```cmd
.venv\Scripts\python.exe builder.py --blueprint blueprints\two_room_tavern.json
```

This mode needs no Ollama or model changes. It saves `generated/blueprint_slab.txt`
separately from prompt-generated slabs and copies the new slab to the clipboard
when available. On macOS/Linux, use `python builder.py --blueprint blueprints/two_room_tavern.json`
with the virtual environment active.

The JSON describes two rectangles sharing one north-south divider. One square
is 5 tabletop feet, and rows count from zero at the south edge. The 16 x 8
example uses row 4 for the west door and interior doorway (one half-square
north of exact center). The north door has three full squares between its east
edge and Room B's east boundary. The plan uses one layer of the nominal
two-unit wall kit, no ceiling or furnishings, and no clipped corners or windows.
The images are style references, not inputs to this command.

The compiler snaps base tiles using their footprint sockets. That checks cell
alignment, not the in-game wall meshes. Divider ends use corner pieces with the
rotations measured from the supplied corrected TaleSpire slab. Door
`mount_offsets` in `assets.json` map `desired_front` to an X/Z correction from
the supporting cell origin: the north-wall door (`W`) needs +0.5 Z, while
the west (`S`) and interior (`N`) doors need no correction. These measurements
are specific to this door and kit, not generic offsets for all assets.

The example sets `interior_door` to `true` to reproduce the corrected build.
Set it to `false` or omit it to retain an open passage. The regression fixture
`tests/fixtures/corrected_two_room_tavern.txt` preserves the user-corrected slab;
the export test compares every UUID, position, rotation, and instance count.

Audit/import the master catalog:

```sh
mkdir -p generated
python asset_tools.py import-catalog reference/original/master_asset_library.py \
  --output generated/catalog.json
python asset_tools.py audit-assets reference/original/assets.json
python asset_tools.py audit-assets reference/original/assets.json \
  --compare reference/original/assets.json_archive
python -m unittest discover -s tests -v
```

The builder's test suite verifies recipe validation, deterministic solving, and
local slab encoding/decoding for the 5x5 starter layout. It cannot call the user's
Ollama service or verify appearance in TaleSpire; paste one root-level build into
TaleSpire before replacing the known-working script.

Catalog exports are lists with UUID identity and the original `asset_key`, name,
and category; repeated semantic names are preserved. Existing output files are
never overwritten. Legacy Python is parsed as literal data, never executed.

Audit exit codes are `0` for no detected issues, `1` for invalid calibration or
conflicting measurements, and `2` for file/format errors. The archive comparison
currently returns `1` intentionally: the same tavern wall UUID has a `[1, 1]`
footprint in the active file and `[1, 0.5]` under an archived alias. Other entries
have conflicting or missing ports. Neither version is automatically preferred.
Passing schema checks does not prove calibration is physically correct.

## Next milestones

1. Verify the two-room slab in TaleSpire, especially the divider junctions.
2. Calibrate more assets from known-good builds and resolve conflicting archive
   measurements.
3. Extend the blueprint format to other shapes and add geometric checks,
   stairs, and multiple levels.
4. Put the stable generation engine behind a web API and build the user-facing
   platform on top of it.

Keep the current development assistant selection for now. The prototype's
Ollama model, `qwen2.5:7b`, is a separate choice: evaluate schema compliance,
valid asset selection, prompt fidelity, latency, and hardware use on a fixed
prompt set before changing it. A larger model cannot repair incorrect geometry.

## External reference

[TalesTerrain](https://quzzar.github.io/TalesTerrain/) demonstrates browser-side
terrain generation and splitting output into manageable slabs. Its bundled
JavaScript slab writer uses format v1; the supplied slab samples use v2.
No repository license was found during initial inspection, so no external
source code has been copied. Public source visibility is not permission to
reuse it; establish applicable licenses before incorporating any implementation.
