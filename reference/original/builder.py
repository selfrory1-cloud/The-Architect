import json
import pyperclip
import requests
from pathlib import Path
from placement_solver import solve, write_slab

ASSETS_FILE = Path("assets.json")
SLAB_FILE = Path("output_slab.txt")
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:7b"

def load_smart_library():
    """Loads the assets.json file containing the crucial port connection data."""
    if not ASSETS_FILE.exists():
        print("[ERROR] assets.json not found. Please run your extraction process first.")
        return {}
    with open(ASSETS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def build_port_snapped_room(assets, recipe):
    """The core procedural generation algorithm."""
    width = recipe.get("width", 3)
    length = recipe.get("length", 3)
    
    # Extract AI's chosen assets, with fallbacks just in case
    floor_asset = recipe.get("floor_asset", "Tavern_Floor_01_01")
    wall_asset = recipe.get("wall_asset", "Tavern_Wall_Floor_01_01")
    corner_asset = recipe.get("corner_asset", "Tavern_Wall_Floor_Corner_01_01")
    door_asset = recipe.get("door_asset") 
    
    print(f"Building a {width}x{length} room...")
    placements = []
    
    for x in range(width):
        for z in range(length):
            # 1. Evaluate coordinates
            is_north_edge = (z == length - 1)
            is_south_edge = (z == 0)
            is_east_edge = (x == width - 1)
            is_west_edge = (x == 0)
            
            is_corner = (is_north_edge or is_south_edge) and (is_east_edge or is_west_edge)
            is_perimeter = is_north_edge or is_south_edge or is_east_edge or is_west_edge
            
            # 2. Place center floors ONLY
            if not is_perimeter:
                placements.append({
                    "id": f"floor_{x}_{z}", 
                    "asset": floor_asset, 
                    "x": x, 
                    "z": z, 
                    "desired_front": "N"
                })
            
            # 3. Snap corners with inward-facing rotations
            elif is_corner:
                if is_south_edge and is_west_edge: front = "S"
                elif is_south_edge and is_east_edge: front = "E"
                elif is_north_edge and is_west_edge: front = "W"
                elif is_north_edge and is_east_edge: front = "N"
                placements.append({"id": f"corner_{x}_{z}", "asset": corner_asset, "x": x, "z": z, "desired_front": front})
            
            # 4. Snap flat edge walls (and our new door + floor stack!)
            else:
                if is_north_edge:
                    placements.append({"id": f"wall_N_{x}_{z}", "asset": wall_asset, "x": x, "z": z, "desired_front": "W"})
                elif is_south_edge:
                    # Place the door exactly in the middle of the South wall
                    if x == width // 2 and door_asset:
                        # Drop the floor tile first
                        placements.append({"id": f"door_floor_{x}_{z}", "asset": floor_asset, "x": x, "z": z, "desired_front": "N"})
                        # Snap the door on top of it
                        placements.append({"id": f"door_S_{x}_{z}", "asset": door_asset, "x": x, "z": z, "desired_front": "E"})
                    else:
                        placements.append({"id": f"wall_S_{x}_{z}", "asset": wall_asset, "x": x, "z": z, "desired_front": "E"})
                elif is_east_edge:
                    placements.append({"id": f"wall_E_{x}_{z}", "asset": wall_asset, "x": x, "z": z, "desired_front": "N"})
                elif is_west_edge:
                    placements.append({"id": f"wall_W_{x}_{z}", "asset": wall_asset, "x": x, "z": z, "desired_front": "S"})

    return {"placements": placements}

if __name__ == "__main__":
    library = load_smart_library()
    
    if not library:
        exit()

    available_pieces = list(library.keys())
    
    # --- AI INSTRUCTIONS ---
    system_prompt = f"""
    You are The Architect, an AI spatial planner for TaleSpire. 
    You do NOT calculate coordinates. You only provide a JSON recipe for a room based on the user's request.
    Output ONLY valid JSON. Do not include markdown formatting or conversational text.

    Available assets in library: {available_pieces}

    Format rules:
    - 'width' and 'length' must be integers.
    - All asset fields MUST exactly match one of the names in the 'Available assets' list.

    Example output format:
    {{
      "width": 5,
      "length": 4,
      "floor_asset": "Tavern_Floor_01_01",
      "wall_asset": "Tavern_Wall_Floor_01_01",
      "corner_asset": "Tavern_Wall_Floor_Corner_01_01",
      "door_asset": "shugun_smallDoor_01"
    }}
    """
    
    user_prompt = "Build me a 5x5 tavern room. Use the standard tavern assets, but make sure to include a 'shugun_smallDoor_01' so people can get in."
    
    print(f"Asking Ollama ({MODEL_NAME}) for the recipe...")
    
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
        print("\n[SUCCESS] AI provided the recipe!")
        
        layout = build_port_snapped_room(library, recipe)
        
        if layout["placements"]:
            solved_layout = solve(library, layout)
            write_slab(solved_layout["placements"], SLAB_FILE)
            
            final_slab = SLAB_FILE.read_text(encoding="utf-8")
            pyperclip.copy(f"```{final_slab.strip()}```")
            print("\n[SUCCESS] Master Slab Generated! Paste it into TaleSpire (Ctrl+V)!")
            print(f"Dimensions built: {recipe.get('width')}x{recipe.get('length')}")
            
    except requests.exceptions.ConnectionError:
        print("\n[ERROR] Could not connect to Ollama. Is the Ollama app running?")
    except json.JSONDecodeError:
        print("\n[ERROR] Ollama did not return valid JSON. It hallucinated text. Try running it again.")
        print("Raw Output:", llm_output)
    except Exception as e:
        print(f"\n[ERROR]: {e}")