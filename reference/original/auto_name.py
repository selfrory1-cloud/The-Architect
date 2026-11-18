import subprocess
import re
from pathlib import Path

# 1. Read master_asset_library.py as raw text to bypass Python's duplicate key deletion
master_file = Path("master_asset_library.py")
if not master_file.exists():
    print("Error: master_asset_library.py not found.")
    exit()

master_text = master_file.read_text(encoding="utf-8")

# Use regex to find all "Name": {"uuid": "UUID"} patterns
pattern = r'"([^"]+)":\s*\{\s*"uuid":\s*"([^"]+)"'
matches = re.findall(pattern, master_text)

uuid_to_name = {}
for name, uuid in matches:
    clean_uuid = uuid.strip().lower()
    uuid_to_name[clean_uuid] = name

# 2. Run the inspect command automatically
print("Inspecting my_slab.txt for UUIDs...\n")
result = subprocess.run(
    ["python", "placement_solver.py", "inspect", "my_slab.txt"], 
    capture_output=True, 
    text=True
)

output = result.stdout
if not output:
    print("Error: No output. Make sure my_slab.txt contains a valid slab string.")
    exit()

# 3. Parse and clean the UUIDs from the terminal output
found_uuids = []
for line in output.split('\n'):
    if "UUID:" in line:
        clean_found_uuid = line.split("UUID:")[1].strip().lower()
        found_uuids.append(clean_found_uuid)

# 4. Cross-reference, fix duplicates, and generate JSON
unmatched = []
name_counts = {}

print("--- COPY THIS BLOCK INTO ASSETS.JSON ---\n")

for uuid in found_uuids:
    if uuid in uuid_to_name:
        base_name = uuid_to_name[uuid]
        
        # Make the name unique so Python doesn't delete duplicates in assets.json!
        if base_name not in name_counts:
            name_counts[base_name] = 1
            final_name = f"{base_name}_01"
        else:
            name_counts[base_name] += 1
            final_name = f"{base_name}_{name_counts[base_name]:02d}"
        
        # Decide if it should be a 'prop' or 'tile'
        kind = "tile"
        degrees_str = ""
        prop_keywords = ["chair", "table", "bed", "beam", "debris", "barrel", "prop"]
        if any(keyword in final_name.lower() for keyword in prop_keywords):
            kind = "prop"
            degrees_str = '    "degrees": 0.0,\n'

        print(f'  "{final_name}": {{')
        print(f'    "kind": "{kind}",')
        print(f'    "uuid": "{uuid}",')
        print(f'    "size": [1, 1],')
        if kind == "tile":
            print(f'    "height": 2.0,')
        print(f'{degrees_str}    "front": "S",')
        print(f'    "ports": []')
        print(f'  }},')
    else:
        unmatched.append(uuid)

# 5. Flag any missing assets
if unmatched:
    print("\n--- ⚠️ UNMATCHED UUIDS (Not in master_asset_library.py) ---")
    for u in unmatched:
        print(f"UUID: {u}")