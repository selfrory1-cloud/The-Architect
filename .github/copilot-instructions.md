## GitHub Copilot System Context: "The Architect" (TaleSpire Procedural Generation Engine)

**Project Intent:**
"The Architect" is a Python-based procedural generation engine for the TaleSpire VTT. It translates natural language prompts or visual blueprint outlines into functional, structurally sound TaleSpire slab codes (Gzip/Base64 strings starting with `H4sIA...`).

**Core Architecture (Strict Separation of Concerns):**
Never instruct the LLM to calculate raw 2D/3D grid coordinates. Small parameter LLMs fail at spatial geometry. The system relies on a strict "Left Brain / Right Brain" division:

* **The LLM (Thematic Seed Generator):** Acts purely as the interior designer. It parses the prompt (or visual blueprint), selects appropriate thematic assets from the library, and outputs a basic JSON recipe containing only the requested dimensions, biome, and semantic asset names.
* **Python (The Physics Engine):** Acts as the construction crew. It ingests the LLM's JSON recipe and uses a **Wave Function Collapse / Port-Snapping** algorithm to mechanically snap the assets together.

**TaleSpire Best Practices & Physics Rules:**
When generating code, analyzing assets, or expanding the builder script, you must strictly adhere to the following TaleSpire geometric rules:

1. **Pivot Point Anomalies:** TaleSpire 3D assets rotate around specific origins (e.g., a bottom-left edge), not a dead-center pivot. Absolute grid coordinates cause assets to swing off-grid like hinges. Always rely on Port-Snapping to connect assets rather than absolute X/Z coordinate loops.
2. **Port-Snapping Logic:** Assets must be connected via defined "ports" (connection sockets like North, South, East, West). Python reads an open port on a floor tile and snaps a wall tile's matching port directly into it, guaranteeing zero gaps and no overlapping.
3. **Collision Engine Bypassing (Tiles vs. Props):** Structural assets designated as `"kind": "tile"` enforce strict grid collision rules. If assets need to overlap, sink into the floor, or bypass collision physics, they must be classified as `"kind": "prop"` in the library so they spawn exactly at their origin point.
4. **Multi-Level Stacking (The Y-Axis):** Verticality is achieved by mapping the strict height of assets. Standard walls are exactly `2.0` units high. To build multiple levels, the Python solver must reference the `"height"` integer in the library to calculate the `pos_y` offset for the next floor.
5. **Multi-Level / Blueprint Assembly:** When parsing visual blueprints or multi-room layouts, the Python engine must break the build into discrete "zones" or "prefabs" (e.g., "Tavern Entrance", "Dining Area", "Stairwell"). The script should compile these sub-assemblies locally before merging them into a single encoded master slab.

**The Data Structure (`assets.json`):**
Base TaleSpire assets often lack explicitly programmed connection sockets in their raw data. All assets added to `assets.json` must be manually calibrated with explicitly mapped ports and height integers to fuel the builder engine.

```json
"Tavern_Wall_01": {
    "kind": "tile",
    "uuid": "55421077-5672-492d-9609-89befbf9eb9d",
    "size": [2, 0.5],
    "height": 2.0,
    "front": "S",
    "ports": [
      {"side": "W", "x": 0, "z": 0.25},
      {"side": "E", "x": 2, "z": 0.25}
    ]
}
```

**Copilot Guidelines:**

* When writing or updating Python logic, focus entirely on expanding the Port-Snapping engine (`builder.py`) to handle complex intersections (T-junctions, stairs, multi-level roofs).
* Never revert to rigid `for x in range(...)` coordinate loops for structural wall placements.
* When expanding the LLM logic (`query.py`), focus strictly on prompt engineering, JSON schema enforcement, and parsing blueprint logic into structured JSON recipes.
