from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from index.prompt_scoring import render_llm_scoring_markdown, score_llm_predictions


class TestPromptScoring(unittest.TestCase):
    def test_scoring_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            answer_key = root / "answer_key.json"
            preds = root / "preds.jsonl"

            answer_key.write_text(
                json.dumps(
                    [
                        {"case_id": "C001", "query": "auth token", "expected_entity_ids": ["E1", "E2"]},
                        {"case_id": "C002", "query": "reset password", "expected_entity_ids": ["E3"]},
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )
            preds.write_text(
                "\n".join(
                    [
                        json.dumps({"case_id": "C001", "mode": "a", "selected_entity_ids": ["E2", "E9", "E8"]}),
                        json.dumps({"case_id": "C002", "mode": "a", "selected_entity_ids": ["E3", "E7", "E6"]}),
                        json.dumps({"case_id": "C001", "mode": "b", "selected_entity_ids": ["E9", "E8", "E7"]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = score_llm_predictions(answer_key, preds)
            self.assertEqual(report["total_cases"], 2)
            self.assertEqual(len(report["results"]), 2)

            modes = {r["mode"]: r for r in report["results"]}
            self.assertIn("a", modes)
            self.assertIn("b", modes)
            self.assertAlmostEqual(modes["a"]["top3_accuracy"], 1.0, places=5)
            self.assertAlmostEqual(modes["b"]["top3_accuracy"], 0.0, places=5)
            self.assertEqual(modes["b"]["missing_cases"], 1)

            md = render_llm_scoring_markdown(report)
            self.assertIn("LLM Benchmark Scoring", md)
            self.assertIn("| a |", md)
            self.assertIn("| b |", md)


if __name__ == "__main__":
    unittest.main()
