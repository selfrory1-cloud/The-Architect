import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from asset_tools import audit_assets, import_catalog, load_assets, main


ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "reference" / "original"
UUID_A = "cf6063bb-5c6e-4107-b3e9-9c0c5ac75768"
UUID_B = "05b8b832-e457-4ea7-88d5-25fa44037f39"


class CatalogTests(unittest.TestCase):
    def test_duplicate_names_keep_both_uuid_records(self):
        source = f'''MASTER_ASSET_LIBRARY = {{
            "floor": {{"uuid": "{UUID_A}", "name": "Floor", "category": "Tavern"}},
            "floor": {{"uuid": "{UUID_B}", "name": "Floor", "category": "Tavern"}}
        }}'''
        records = import_catalog(source)
        self.assertEqual([record["uuid"] for record in records], [UUID_A, UUID_B])
        self.assertEqual([record["asset_key"] for record in records], ["floor", "floor"])

    def test_source_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            source = f'MASTER_ASSET_LIBRARY = __import__("pathlib").Path({str(marker)!r}).touch()'
            with self.assertRaises(ValueError):
                import_catalog(source)
            self.assertFalse(marker.exists())

    def test_rejects_duplicate_metadata_fields(self):
        source = f'''MASTER_ASSET_LIBRARY = {{
            "floor": {{"uuid": "{UUID_A}", "uuid": "{UUID_B}",
                      "name": "Floor", "category": "Tavern"}}
        }}'''
        with self.assertRaisesRegex(ValueError, "duplicate metadata field"):
            import_catalog(source)

    def test_supplied_catalog_preserves_all_entries(self):
        tree_source = (REFERENCES / "master_asset_library.py").read_text()
        records = import_catalog(tree_source)
        self.assertEqual(len(records), 2431)
        self.assertEqual(len({record["uuid"] for record in records}), 2431)
        self.assertEqual(len({record["asset_key"] for record in records}), 1986)

    def test_export_refuses_to_overwrite_and_round_trips_records(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "catalog.json"
            args = [
                "import-catalog", str(REFERENCES / "master_asset_library.py"),
                "--output", str(output),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            original = output.read_text()
            self.assertEqual(len(json.loads(original)), 2431)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
            self.assertEqual(output.read_text(), original)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.assets = load_assets(REFERENCES / "assets.json")

    def test_supplied_active_library_is_not_claimed_in_game_verified(self):
        report = audit_assets(self.assets)
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["in_game_verified"])

    def test_archive_conflict_is_found_by_uuid_not_asset_name(self):
        archive = load_assets(REFERENCES / "assets.json_archive")
        report = audit_assets(self.assets, archive)
        wall = next(
            conflict for conflict in report["conflicts"]
            if conflict["comparison_asset"] == "tavern_wall_narrow"
        )
        self.assertEqual(wall["differences"]["size"], {
            "current": [1, 1], "comparison": [1, 0.5],
        })

    def test_rejects_bad_calibration_without_mutating_inputs(self):
        spec = self.assets["Tavern_Floor_01_01"]
        spec["size"] = [True, 1]
        spec["height"] = float("nan")
        spec["ports"] = []
        before = json.dumps(self.assets)
        errors = audit_assets(self.assets)["errors"]
        self.assertTrue(any("size" in error for error in errors))
        self.assertTrue(any("height" in error for error in errors))
        self.assertTrue(any("ports" in error for error in errors))
        self.assertEqual(json.dumps(self.assets), before)

    def test_rejects_nonfinite_and_out_of_bounds_ports(self):
        ports = self.assets["Tavern_Floor_01_01"]["ports"]
        ports[0]["x"] = float("inf")
        ports[1]["z"] = 2
        errors = audit_assets(self.assets)["errors"]
        self.assertTrue(any("finite x/z" in error for error in errors))
        self.assertTrue(any("outside" in error for error in errors))

    def test_invalid_direction_types_are_reported(self):
        spec = self.assets["Tavern_Floor_01_01"]
        spec["kind"] = []
        spec["front"] = {}
        errors = audit_assets(self.assets)["errors"]
        self.assertTrue(any("kind" in error for error in errors))
        self.assertTrue(any("front" in error for error in errors))

    def test_port_order_and_object_key_order_do_not_create_conflicts(self):
        comparison = json.loads(json.dumps(self.assets))
        for spec in comparison.values():
            spec["ports"] = [dict(reversed(list(port.items()))) for port in reversed(spec["ports"])]
        self.assertEqual(audit_assets(self.assets, comparison)["conflicts"], [])

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assets.json"
            path.write_text('{"floor": {}, "floor": {}}')
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                load_assets(path)

    def test_audit_conflicts_return_failure_status(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = main([
                "audit-assets", str(REFERENCES / "assets.json"),
                "--compare", str(REFERENCES / "assets.json_archive"),
            ])
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
