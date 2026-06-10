"""Tests for the search caller-count A/B benchmark scorer."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "eval" / "score_search_callers_ab.py"

spec = importlib.util.spec_from_file_location("score_search_callers_ab", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules["score_search_callers_ab"] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def test_score_task_rewards_first_hit_and_correct_entity():
    task = {
        "id": "S1",
        "type": "triage_selection",
        "ground_truth": {
            "required_entities": ["FRMTNNTTDNTT"],
            "key_file": "cli.py",
            "bucket": "central",
        },
    }
    response = {
        "tool_calls": [
            "codeir search annotated entity format",
            "codeir show FRMTNNTTDNTT",
        ],
        "answer": "Inspect FRMTNNTTDNTT in cli.py first.",
        "entities_mentioned": ["FRMTNNTTDNTT"],
    }

    scored = module.score_task(task, response)

    assert scored["score"] == 100
    assert scored["first_inspection_hit"] is True
    assert scored["wrong_inspections_before_hit"] == 0


def test_score_task_captures_wrong_inspection_before_hit():
    task = {
        "id": "S4",
        "type": "triage_selection",
        "ground_truth": {
            "required_entities": ["NRMLZGRPPTTR"],
            "key_file": "index/search.py",
            "bucket": "leaf",
        },
    }
    response = {
        "tool_calls": [
            "codeir search grep alternation",
            "codeir show GRPNTTS",
            "codeir show NRMLZGRPPTTR",
        ],
        "answer": "The normalizer is NRMLZGRPPTTR in index/search.py.",
        "entities_mentioned": ["NRMLZGRPPTTR"],
    }

    scored = module.score_task(task, response)

    assert scored["first_inspection_hit"] is False
    assert scored["wrong_inspections_before_hit"] == 1
    assert scored["score"] == 85


def test_summary_splits_by_bucket():
    scored_tasks = {
        "S1": {
            "score": 100,
            "bucket": "central",
            "first_inspection_hit": True,
            "inspection_calls": 1,
            "wrong_inspections_before_hit": 0,
        },
        "S2": {
            "score": 70,
            "bucket": "leaf",
            "first_inspection_hit": False,
            "inspection_calls": 2,
            "wrong_inspections_before_hit": 1,
        },
    }

    summary = module.summarize(scored_tasks)

    assert summary["pct"] == 85.0
    assert summary["first_hit_rate"] == 50.0
    assert summary["by_bucket"]["central"]["pct"] == 100.0
    assert summary["by_bucket"]["leaf"]["pct"] == 70.0
