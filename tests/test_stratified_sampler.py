from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest

# Add tests directory to sys.path for the eval package
sys.path.insert(0, str(Path(__file__).parent))

from eval.scripts.build_small_stratified_pack import build_small_stratified_pack


class TestStratifiedSampler(unittest.TestCase):
    def _write_manifest(self, path: Path) -> None:
        manifest = {
            "manifest_version": "sampling_manifest.v1",
            "required_strata": [
                {
                    "name": "empty_exception_classes",
                    "candidates": ["CLSA", "CLSB"],
                    "task_query": "exception handling",
                },
                {
                    "name": "async_entities",
                    "candidates": ["AMTX", "AFNY"],
                    "task_query": "async work",
                },
            ],
            "supplemental_tasks": [
                {
                    "query": "supplemental",
                    "ground_truth_entity_ids": ["FNZ"],
                    "domain": "misc",
                    "difficulty": "easy",
                }
            ],
            "target_task_count": 3,
        }
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_same_seed_reproduces_same_pack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            source = root / "source.json"
            out1 = root / "out1.json"
            out2 = root / "out2.json"

            self._write_manifest(manifest)
            source.write_text(
                json.dumps(
                    {
                        "CLSA": {},
                        "AFNY": {},
                        "FNZ": {},
                    }
                ),
                encoding="utf-8",
            )

            p1 = build_small_stratified_pack(
                manifest_path=manifest,
                source_paths=[source],
                output_path=out1,
                seed=7,
                strict=True,
            )
            p2 = build_small_stratified_pack(
                manifest_path=manifest,
                source_paths=[source],
                output_path=out2,
                seed=7,
                strict=True,
            )

            self.assertEqual(p1["tasks"], p2["tasks"])
            strata = {t.get("stratum") for t in p1["tasks"] if t.get("stratum")}
            self.assertIn("empty_exception_classes", strata)
            self.assertIn("async_entities", strata)

    def test_missing_required_strata_fails_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            source = root / "source.json"
            out = root / "out.json"

            self._write_manifest(manifest)
            source.write_text(json.dumps({"CLSA": {}}), encoding="utf-8")

            with self.assertRaises(ValueError):
                build_small_stratified_pack(
                    manifest_path=manifest,
                    source_paths=[source],
                    output_path=out,
                    seed=11,
                    strict=True,
                )


if __name__ == "__main__":
    unittest.main()
