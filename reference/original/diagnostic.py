import pyperclip
from ts_encoding import TSSlab

# The unknown UUIDs we extracted from your inspect command
unknown_uuids = [
    "05b8b832-e457-4ea7-88d5-25fa44037f39",
    "2fc1f203-3702-4126-8777-5f94067a0c9d",
    "910bdbf7-1cdf-4601-929a-9c89c5eaa8be",
    "d3ec0216-95e4-44cc-b076-74f39504166e",
    "db822c28-e616-42b8-99c1-e848906fbc9c",
    "e62c6746-cecf-46bf-8b20-f81738f1d220",
    "f12afdd6-a171-4a24-b3db-935c05e7aaf9",
    "f8fdaf2d-3b5b-47d8-8e1d-077994535798",
    "ff6b5a27-a041-4ba1-ad33-01d82c77bb34"
]

slab = TSSlab()
layouts = []

print("Building visual diagnostic lineup...")

# Place each asset in a straight line on the X-axis, spaced by 2 units
for index, uuid in enumerate(unknown_uuids):
    layouts.append({
        "uuid": uuid,
        "reserved": 0,
        "instance_count": 1,
        "instances": [{"pos_x": float(index * 2), "pos_y": 0.0, "pos_z": 0.0, "degrees": 0.0}]
    })

slab.data["layouts"] = layouts
slab.data["layout_count"] = len(layouts)

raw_code = slab.encode_slab().strip()
formatted_code = raw_code if raw_code.startswith("```") else f"```{raw_code}```"

pyperclip.copy(formatted_code)
print("\n[SUCCESS] Diagnostic Lineup copied to clipboard!")
print("Paste it into TaleSpire and read them left-to-right to identify them.")