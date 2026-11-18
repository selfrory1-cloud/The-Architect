# The Architect

A work-in-progress natural-language-to-TaleSpire generation engine.

## Architecture

The LLM selects themes, semantic assets, dimensions, and eventually room zones.
Python owns port-snapping, collision checks, rotation, and height calculations.
The LLM must not supply raw placement coordinates. Structural assets must not
be globally reclassified as props to hide placement errors.

The first milestone is a verified local, single-room pipeline. A web interface,
multi-room layouts, stairs, and multi-level generation come after that baseline
works inside TaleSpire.

## Current status

The original scripts and supplied examples are preserved unchanged in
`reference/original/`. You confirmed that the original `builder.py` generates a
room you can paste into TaleSpire. The root-level `builder.py` carries that
rectangular-grid workflow forward with a prompt argument, validated Ollama
recipes, bounded dimensions, configurable local model/URL, and explicit errors.

The starter library contains four calibrated assets. The separate master catalog
has 2,431 UUIDs, including 445 entries hidden by duplicate Python dictionary
keys; the lossless import/audit tool is available below. A large calibrated,
theme-agnostic asset library, actual port-to-port assembly, web interface, and multi-room generation remain
future work.

Door placement uses the floor asset's explicit `surface_offset` rather than
placing a door at the floor tile's base Y. The starter value is `0.5`, consistent
with the supplied room reference slab's half-unit floor-to-wall elevation.
Verify that corrected door elevation in TaleSpire before treating that offset as
confirmed for other floor assets.

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

1. Run the new CLI locally with your working Ollama setup and paste its output
   into TaleSpire. Keep a known-good slab as a regression fixture.
2. Verify the corrected door height in TaleSpire, then calibrate asset metadata
   from known-good builds and resolve the conflicting archive measurements.
3. Implement port-to-port placement and geometric/collision checks, then support
   multi-room recipes and multiple levels.
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
