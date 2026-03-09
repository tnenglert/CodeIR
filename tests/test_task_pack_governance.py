from __future__ import annotations

import json
from pathlib import Path
import unittest


class TestTaskPackGovernance(unittest.TestCase):
    def test_small_pack_has_phase_a_warning_and_l2_scope_note(self) -> None:
        pack_path = Path(__file__).resolve().parent / "eval" / "test_packs" / "task_benchmark_small.json"
        data = json.loads(pack_path.read_text(encoding="utf-8"))

        self.assertEqual(data.get("phase"), "A")
        self.assertIn("Harness validation", str(data.get("phase_note", "")))
        self.assertIn("L2", str(data.get("l2_scope_note", "")))
        self.assertLessEqual(len(data.get("tasks", [])), 8)


if __name__ == "__main__":
    unittest.main()
