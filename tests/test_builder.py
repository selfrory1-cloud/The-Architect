import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder import (
    build_room_layout,
    load_smart_library,
    main,
    request_recipe,
    system_prompt_for,
    validate_recipe,
)
from placement_solver import solve, write_slab
from ts_encoding import TSSlab


ROOT = Path(__file__).resolve().parents[1]


class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = load_smart_library(ROOT / "assets.json")
        cls.recipe = {
            "width": 5,
            "length": 5,
            "floor_asset": "Tavern_Floor_01_01",
            "wall_asset": "Tavern_Wall_Floor_01_01",
            "corner_asset": "Tavern_Wall_Floor_Corner_01_01",
            "door_asset": "shugun_smallDoor_01",
        }

    def test_confirmed_5_by_5_room_recipe_solves(self):
        layout = build_room_layout(self.assets, self.recipe)
        solved = solve(self.assets, layout)
        self.assertEqual(len(layout["placements"]), 26)
        self.assertEqual(len(solved["placements"]), 26)
        self.assertEqual(
            sum(item["asset"] == "shugun_smallDoor_01" for item in solved["placements"]),
            1,
        )
        self.assertTrue(all(item["kind"] == "tile" for item in solved["placements"]))

    def test_recipe_rejects_coordinate_fields_and_unknown_assets(self):
        with self.assertRaisesRegex(ValueError, "Unknown recipe fields"):
            validate_recipe({**self.recipe, "x": 4}, self.assets)
        with self.assertRaisesRegex(ValueError, "floor_asset"):
            validate_recipe({**self.recipe, "floor_asset": "hallucinated_asset"}, self.assets)

    def test_recipe_dimensions_must_be_bounded_integers(self):
        for width in (True, 2, 21, 5.0):
            with self.subTest(width=width), self.assertRaisesRegex(ValueError, "width"):
                validate_recipe({**self.recipe, "width": width}, self.assets)

    def test_system_prompt_gives_asset_names_not_coordinates(self):
        prompt = system_prompt_for(self.assets)
        self.assertIn("Do not calculate or output placement coordinates", prompt)
        self.assertIn('"Tavern_Floor_01_01"', prompt)

    def test_ollama_request_uses_json_mode_and_configured_model(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"response": json.dumps(self.recipe)}).encode()

            recipe = {
                "width": 5,
                "length": 5,
                "floor_asset": "Tavern_Floor_01_01",
                "wall_asset": "Tavern_Wall_Floor_01_01",
                "corner_asset": "Tavern_Wall_Floor_Corner_01_01",
            }

        with patch("builder.urlopen", return_value=FakeResponse()) as urlopen:
            recipe = request_recipe(
                "A tavern",
                self.assets,
                model="configured-model",
                ollama_url="http://localhost:11434/api/generate",
                timeout=17,
            )
        self.assertEqual(recipe["width"], 5)
        request = urlopen.call_args.args[0]
        timeout = urlopen.call_args.kwargs["timeout"]
        body = json.loads(request.data)
        self.assertEqual(body["model"], "configured-model")
        self.assertEqual(body["format"], "json")
        self.assertFalse(body["stream"])
        self.assertEqual(timeout, 17)

    def test_confirmed_layout_encodes_and_decodes_as_slab_v2(self):
        solved = solve(
            self.assets,
            build_room_layout(self.assets, self.recipe),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test_slab.txt"
            write_slab(solved["placements"], output)
            encoded = output.read_text(encoding="utf-8").strip()
            self.assertTrue(encoded.startswith("H4sIA"))
            decoded = TSSlab()
            decoded.decode_slab(encoded)
            self.assertEqual(decoded.data["layout_count"], 4)
            self.assertEqual(
                sum(group["instance_count"] for group in decoded.data["layouts"]),
                26,
            )

    def test_cli_writes_a_slab_using_a_mock_ollama_response(self):
        response_data = {"response": json.dumps(self.recipe)}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(response_data).encode()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "room.slab.txt"
            with patch("builder.urlopen", return_value=FakeResponse()):
                status = main([
                    "--prompt", "A test tavern",
                    "--assets", str(ROOT / "assets.json"),
                    "--output", str(output),
                    "--no-clipboard",
                ])
            self.assertEqual(status, 0)
            self.assertTrue(output.read_text().startswith("H4sIA"))


if __name__ == "__main__":
    unittest.main()
