import json
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from blueprint import CORNER, DOOR, FLOOR, WALL, compile_blueprint
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
BLUEPRINT = ROOT / "blueprints" / "two_room_tavern.json"


class BlueprintTests(unittest.TestCase):
    def setUp(self):
        self.assets = load_smart_library(ROOT / "assets.json")
        self.plan = json.loads(BLUEPRINT.read_text(encoding="utf-8"))

    def test_tavern_has_one_divider_with_corner_junctions_and_three_doors(self):
        solved = compile_blueprint(self.assets, self.plan)
        base = {
            (piece["x"], piece["z"]): piece["asset"]
            for piece in solved["placements"] if piece["y"] == 0
        }
        self.assertEqual(len(solved["placements"]), 131)
        self.assertEqual(set(base), {(x, z) for x in range(16) for z in range(8)})
        self.assertEqual([base[8, z] for z in range(8)],
                         [CORNER] + [WALL] * 3 + [FLOOR] + [WALL] * 2 + [CORNER])
        self.assertEqual([base[7, z] for z in range(1, 7)], [FLOOR] * 6)
        self.assertEqual({cell for cell, asset in base.items() if asset == CORNER},
                         {(0, 0), (0, 7), (15, 0), (15, 7), (8, 0), (8, 7)})
        self.assertEqual(solved["links_checked"], 232)

    def test_blueprint_cli_matches_every_corrected_slab_instance_without_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tavern.txt"
            with patch("builder.request_recipe") as request_recipe:
                status = main([
                    "--blueprint", str(BLUEPRINT), "--assets", str(ROOT / "assets.json"),
                    "--output", str(output), "--no-clipboard",
                ])
            request_recipe.assert_not_called()
            self.assertEqual(status, 0)
            slab = TSSlab()
            slab.decode_slab(output.read_text(encoding="utf-8"))
        reference = TSSlab()
        reference.decode_slab(
            (ROOT / "tests" / "fixtures" / "corrected_two_room_tavern.txt")
            .read_text(encoding="utf-8").strip().strip("`").strip()
        )

        def instances(data):
            return Counter(
                (group["uuid"], item["pos_x"], item["pos_y"], item["pos_z"], item["degrees"])
                for group in data["layouts"] for item in group["instances"]
            )

        self.assertEqual(instances(slab.data), instances(reference.data))

    def test_interior_door_can_remain_an_open_passage(self):
        for value in (False, None):
            plan = dict(self.plan)
            if value is None:
                del plan["interior_door"]
            else:
                plan["interior_door"] = value
            solved = compile_blueprint(self.assets, plan)
            self.assertEqual(sum(piece["asset"] == DOOR for piece in solved["placements"]), 2)

    def test_rejects_missing_door_origin_calibration(self):
        del self.assets[DOOR]["mount_offsets"]["W"]
        with self.assertRaisesRegex(ValueError, "mount_offsets"):
            compile_blueprint(self.assets, self.plan)

    def test_rejects_corner_door_and_a_misaligned_socket(self):
        with self.assertRaisesRegex(ValueError, "North door"):
            compile_blueprint(self.assets, {**self.plan, "north_door_clearance_from_east": 0})
        self.assets[CORNER]["ports"][0]["z"] = 0.75
        with self.assertRaisesRegex(ValueError, "snapped footprint"):
            compile_blueprint(self.assets, self.plan)


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
        door = next(item for item in solved["placements"] if item["id"] == "door_S_2_0")
        door_floor = next(item for item in solved["placements"] if item["id"] == "door_floor_2_0")
        self.assertEqual(door["y"], 0.5)
        self.assertEqual(door_floor["y"], 0.0)
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

    def test_door_requires_calibrated_floor_surface_offset(self):
        for value in (None, float("nan"), -0.5):
            with self.subTest(surface_offset=value):
                assets = json.loads(json.dumps(self.assets))
                if value is None:
                    del assets["Tavern_Floor_01_01"]["surface_offset"]
                else:
                    assets["Tavern_Floor_01_01"]["surface_offset"] = value
                with self.assertRaisesRegex(ValueError, "surface_offset"):
                    validate_recipe(self.recipe, assets)

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
            door_group = next(
                group for group in decoded.data["layouts"]
                if group["uuid"] == self.assets["shugun_smallDoor_01"]["uuid"]
            )
            self.assertEqual(door_group["instances"][0]["pos_y"], 0.5)

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
