import json
import pyperclip
import requests
from placement_solver import solve, write_slab
from pathlib import Path

ASSETS_FILE = Path("assets.json")
SLAB_FILE = Path("output_slab.txt")
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:7b"

if not ASSETS_FILE.exists():
    print("Error: assets.json not found.")
    exit()

with open(ASSETS_FILE, "r", encoding="utf-8") as f:
    calibrated_assets = json.load(f)

# --- THE ARCHITECT'S OVERRIDE ---
for key in calibrated_assets.keys():
    calibrated_assets[key]["kind"] = "prop"
# --------------------------------

available_pieces = list(calibrated_assets.keys())

system_prompt = f"""
You are The Architect, an AI spatial planner for TaleSpire. 
Instead of calculating grid coordinates yourself, you will simply output a "Recipe" for a room.
Output ONLY valid JSON. Do not include markdown formatting or conversational text.

Available assets: {available_pieces}

Format rules:
- 'width' and 'length' must be integers.
- All asset fields MUST exactly match one of the names in the 'Available assets' list.

Example output format:
{{
  "width": 3,
  "length": 3,
  "floor_asset": "Tavern_Floor_01_01",
  "north_wall_asset": "Tavern_Wall_Floor_01_01",
  "south_wall_asset": "Tavern_Wall_Floor_01_01",
  "east_wall_asset": "Tavern_Wall_Floor_With_Window_01",
  "west_wall_asset": "Tavern_Wall_Floor_With_Window_01",
  "corner_prop_asset": "Tavern_Wall_Floor_Corner_01_01"
}}
"""

user_prompt = "Design a 3x3 enclosed tavern room. Use 'Tavern_Floor_01_01' for the center floors. Place 'Tavern_Wall_Floor_01_01' on the North and South walls, 'Tavern_Wall_Floor_With_Window_01' on the East and West walls. Put a 'Tavern_Wall_Floor_Corner_01_01' in the corners."

print(f"Asking Ollama ({MODEL_NAME}) to pick the assets...")

try:
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL_NAME,
        "prompt": user_prompt,
        "system": system_prompt,
        "stream": False
    })
    response.raise_for_status()
    llm_output = response.json()["response"].strip()
    
    recipe = json.loads(llm_output)
    print("\n[SUCCESS] AI generated the recipe! Python is now building the exact grid...")

    # --- THE ARCHITECT'S ENGINE (Python does the math perfectly) ---
    placements = []
    w = recipe.get("width", 3)
    l = recipe.get("length", 3)
    
    # Bulletproof fallbacks in case the AI hallucinates or forgets a key
    asset_N = recipe.get("north_wall_asset", "Tavern_Wall_Floor_01_01")
    asset_S = recipe.get("south_wall_asset", "Tavern_Wall_Floor_01_01")
    asset_E = recipe.get("east_wall_asset", "Tavern_Wall_Floor_With_Window_01")
    asset_W = recipe.get("west_wall_asset", "Tavern_Wall_Floor_With_Window_01")
    asset_C = recipe.get("corner_prop_asset", "Tavern_Wall_Floor_Corner_01_01")
    asset_F = recipe.get("floor_asset", "Tavern_Floor_01_01")
    
    # Build Mutually Exclusive Grid (1 piece per coordinate)
    for x in range(w):
        for z in range(l):
            # Define what type of coordinate this is
            is_corner = (x == 0 and z == 0) or (x == w-1 and z == 0) or (x == 0 and z == l-1) or (x == w-1 and z == l-1)
            is_edge_N = (z == l-1 and not is_corner)
            is_edge_S = (z == 0 and not is_corner)
            is_edge_E = (x == w-1 and not is_corner)
            is_edge_W = (x == 0 and not is_corner)
            
            # Place the correct asset based on the coordinate type
            if is_corner:
                if x == 0 and z == 0: deg = 90.0
                elif x == w-1 and z == 0: deg = 0.0
                elif x == 0 and z == l-1: deg = 180.0
                else: deg = 270.0
                if asset_C:
                    placements.append({"id": f"corner_{x}_{z}", "asset": asset_C, "x": x, "z": z, "degrees": deg})
            
            elif is_edge_N and asset_N:
                placements.append({"id": f"wall_N_{x}_{z}", "asset": asset_N, "x": x, "z": z, "degrees": 180.0})
            elif is_edge_S and asset_S:
                placements.append({"id": f"wall_S_{x}_{z}", "asset": asset_S, "x": x, "z": z, "degrees": 0.0})
            elif is_edge_E and asset_E:
                placements.append({"id": f"wall_E_{x}_{z}", "asset": asset_E, "x": x, "z": z, "degrees": 270.0})
            elif is_edge_W and asset_W:
                placements.append({"id": f"wall_W_{x}_{z}", "asset": asset_W, "x": x, "z": z, "degrees": 90.0})
            
            else: 
                # If it's not a corner and not an edge, it must be an interior floor
                if asset_F:
                    placements.append({"id": f"floor_{x}_{z}", "asset": asset_F, "x": x, "z": z, "degrees": 0.0})

    layout = {"placements": placements}
    # ---------------------------------------------------------------

    solved_layout = solve(calibrated_assets, layout)
    write_slab(solved_layout["placements"], SLAB_FILE)
    
    final_slab = SLAB_FILE.read_text(encoding="utf-8")
    pyperclip.copy(f"```{final_slab.strip()}```")
    
    print("\n[SUCCESS] Master Slab Generated!")
    print("Paste it directly into TaleSpire (Ctrl+V)!")

except requests.exceptions.ConnectionError:
    print("\n[ERROR] Could not connect to Ollama. Is the Ollama app running?")
except json.JSONDecodeError:
    print("\n[ERROR] Ollama did not return valid JSON. It hallucinated text. Try running it again.")
    print("Raw Output:", llm_output)
except Exception as e:
    print(f"\n[ERROR]: {e}")