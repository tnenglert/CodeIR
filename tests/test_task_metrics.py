from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest

# Add tests directory to sys.path for the eval package
sys.path.insert(0, str(Path(__file__).parent))

from eval.metrics.compute_task_metrics import compute_task_metrics


class TestTaskMetrics(unittest.TestCase):
    def test_false_confidence_curve_present_even_with_sparse_data(self) -> None:
        payload = {
            "condition_results": {
                "semanticir_flow": [
                    {
                        "task_id": "T1",
                        "top1_hit": 0,
                        "top3_hit": 1,
                        "confidence": 2,
                        "needs_expansion": True,
                        "expansions_used": 1,
                        "orientation_tokens": 10,
                        "retrieval_tokens": 20,
                        "expansion_tokens": 5,
                        "reasoning_tokens": 15,
                        "judge_tokens": 0,
                        "total_tokens_cold": 50,
                        "total_tokens_warm": 40,
                    },
                    {
                        "task_id": "T2",
                        "top1_hit": 0,
                        "top3_hit": 0,
                        "confidence": 5,
                        "needs_expansion": False,
                        "expansions_used": 0,
                        "orientation_tokens": 0,
                        "retrieval_tokens": 18,
                        "expansion_tokens": 0,
                        "reasoning_tokens": 11,
                        "judge_tokens": 0,
                        "total_tokens_cold": 29,
                        "total_tokens_warm": 29,
                    },
                ]
            }
        }

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "results.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            metrics = compute_task_metrics(p)

        sem = metrics["conditions"]["semanticir_flow"]
        self.assertIn("3", sem["false_confidence_curve"])
        self.assertIn("4", sem["false_confidence_curve"])
        self.assertIn("5", sem["false_confidence_curve"])
        self.assertEqual(sem["calibration_status"], "insufficient_data")
        self.assertEqual(sem["task_count"], 2)
        self.assertAlmostEqual(sem["top3_hit"], 0.5)

    def test_token_equation_fixture_rows_balance(self) -> None:
        row = {
            "orientation_tokens": 12,
            "retrieval_tokens": 25,
            "expansion_tokens": 7,
            "reasoning_tokens": 18,
            "judge_tokens": 0,
            "total_tokens_cold": 62,
        }
        self.assertEqual(
            row["total_tokens_cold"],
            row["orientation_tokens"]
            + row["retrieval_tokens"]
            + row["expansion_tokens"]
            + row["reasoning_tokens"]
            + row["judge_tokens"],
        )

    def test_retrieval_recall_metrics_present_and_computed(self) -> None:
        payload = {
            "condition_results": {
                "semanticir_flow": [
                    {
                        "task_id": "T1",
                        "top1_hit": 0,
                        "top3_hit": 0,
                        "confidence": 4,
                        "needs_expansion": False,
                        "expansions_used": 0,
                        "orientation_tokens": 0,
                        "retrieval_tokens": 10,
                        "expansion_tokens": 0,
                        "reasoning_tokens": 10,
                        "judge_tokens": 0,
                        "total_tokens_cold": 20,
                        "total_tokens_warm": 20,
                        "candidate_has_any_ground_truth": False,
                        "candidate_recall_at_k": 0.0,
                        "ground_truth_in_index_count": 2,
                        "ground_truth_missing_from_index": [],
                    },
                    {
                        "task_id": "T2",
                        "top1_hit": 1,
                        "top3_hit": 1,
                        "confidence": 5,
                        "needs_expansion": False,
                        "expansions_used": 0,
                        "orientation_tokens": 0,
                        "retrieval_tokens": 10,
                        "expansion_tokens": 0,
                        "reasoning_tokens": 10,
                        "judge_tokens": 0,
                        "total_tokens_cold": 20,
                        "total_tokens_warm": 20,
                        "candidate_has_any_ground_truth": True,
                        "candidate_recall_at_k": 0.5,
                        "ground_truth_in_index_count": 2,
                        "ground_truth_missing_from_index": [],
                    },
                    {
                        "task_id": "T3",
                        "top1_hit": 0,
                        "top3_hit": 0,
                        "confidence": 2,
                        "needs_expansion": True,
                        "expansions_used": 1,
                        "orientation_tokens": 0,
                        "retrieval_tokens": 10,
                        "expansion_tokens": 5,
                        "reasoning_tokens": 10,
                        "judge_tokens": 0,
                        "total_tokens_cold": 25,
                        "total_tokens_warm": 25,
                        "candidate_has_any_ground_truth": False,
                        "candidate_recall_at_k": 0.0,
                        "ground_truth_in_index_count": 1,
                        "ground_truth_missing_from_index": ["E_MISSING"],
                    },
                ]
            }
        }

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "results.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            metrics = compute_task_metrics(p)

        sem = metrics["conditions"]["semanticir_flow"]
        self.assertAlmostEqual(sem["retrieval_recall_any_rate"], 1.0 / 3.0)
        self.assertAlmostEqual(sem["retrieval_recall_mean"], 1.0 / 6.0)
        self.assertEqual(sem["ground_truth_missing_from_index_tasks"], 1)
        self.assertEqual(sem["ground_truth_missing_from_index_entities"], 1)
        self.assertEqual(sem["candidate_miss_despite_index_presence"], 2)


if __name__ == "__main__":
    unittest.main()
