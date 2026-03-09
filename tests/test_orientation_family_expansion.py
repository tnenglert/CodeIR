from __future__ import annotations

import sys
from pathlib import Path
import unittest

# Add tests/eval to sys.path for the eval package
sys.path.insert(0, str(Path(__file__).parent / "eval"))
sys.path.insert(0, str(Path(__file__).parent))

from eval.entity_family import entity_family_base
from eval.entity_family import expand_entity_family_candidates


class TestOrientationFamilyExpansion(unittest.TestCase):
    def test_entity_family_base_strips_numeric_suffix(self) -> None:
        self.assertEqual(entity_family_base("GTSRDB.03"), "GTSRDB")
        self.assertEqual(entity_family_base("DCDJWT"), "DCDJWT")

    def test_expand_entity_family_candidates_adds_family_members(self) -> None:
        families = {
            "GTSRDB": ["GTSRDB", "GTSRDB.02", "GTSRDB.03"],
            "DCDJWT": ["DCDJWT"],
        }

        expanded, added = expand_entity_family_candidates(
            ["GTSRDB.03", "GTSRDB.03", "DCDJWT"],
            families,
        )

        self.assertEqual(
            expanded,
            ["GTSRDB.03", "DCDJWT", "GTSRDB", "GTSRDB.02"],
        )
        self.assertEqual(added, ["GTSRDB", "GTSRDB.02"])

    def test_expand_entity_family_candidates_handles_unknown_family(self) -> None:
        expanded, added = expand_entity_family_candidates(["HTTPRRR"], {})
        self.assertEqual(expanded, ["HTTPRRR"])
        self.assertEqual(added, [])

    def test_expand_entity_family_candidates_respects_cap(self) -> None:
        families = {
            "GTSRDB": [
                "GTSRDB",
                "GTSRDB.02",
                "GTSRDB.03",
                "GTSRDB.04",
            ],
            "DCDJWT": ["DCDJWT", "DCDJWT.02"],
        }

        expanded, added = expand_entity_family_candidates(
            ["GTSRDB.03", "DCDJWT"],
            families,
            max_candidates=3,
        )

        self.assertEqual(expanded, ["GTSRDB.03", "DCDJWT", "GTSRDB"])
        self.assertEqual(added, ["GTSRDB"])


if __name__ == "__main__":
    unittest.main()
