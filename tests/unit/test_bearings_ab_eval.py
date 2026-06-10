"""Tests for the full-bearings A/B benchmark scorer."""

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "eval" / "score_bearings_ab.py"

spec = importlib.util.spec_from_file_location("score_bearings_ab", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules["score_bearings_ab"] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def test_score_task_rewards_category_choice_and_first_hit():
    task = {
        "id": "B1",
        "type": "orientation_selection",
        "ground_truth": {
            "required_entities": ["GNRTBRNGSFLS"],
            "key_file": "cli.py",
            "accepted_categories": ["core_logic"],
            "bucket": "zone_selection",
        },
    }
    response = {
        "tool_calls": [
            "codeir bearings core_logic",
            "codeir show GNRTBRNGSFLS",
        ],
        "answer": "Inspect GNRTBRNGSFLS in cli.py first from the core_logic area.",
        "entities_mentioned": ["GNRTBRNGSFLS"],
    }

    scored = module.score_task(task, response)

    assert scored["score"] == 100
    assert scored["category_choice_accuracy"] is True
    assert scored["first_inspection_hit"] is True


def test_score_task_captures_wrong_inspection_before_hit():
    task = {
        "id": "B7",
        "type": "orientation_selection",
        "ground_truth": {
            "required_entities": ["VIEW", "MTHDVW", "ASVW"],
            "key_file": "src/flask/views.py",
            "accepted_categories": ["core_logic", "router"],
            "bucket": "duplicate_name_disambiguation",
        },
    }
    response = {
        "tool_calls": [
            "codeir bearings router --repo-path tests/_local/testRepositories/_flask-main",
            "codeir show ADD.03",
            "codeir show VIEW",
        ],
        "answer": "Use the router area, then inspect VIEW in src/flask/views.py.",
        "entities_mentioned": ["VIEW"],
    }

    scored = module.score_task(task, response)

    assert scored["category_choice_accuracy"] is True
    assert scored["first_inspection_hit"] is False
    assert scored["wrong_inspections_before_hit"] == 1
    assert scored["score"] == 88


def test_summary_splits_by_bucket_and_category_choice():
    scored_tasks = {
        "B1": {
            "score": 100,
            "bucket": "zone_selection",
            "category_choice_accuracy": True,
            "first_inspection_hit": True,
            "inspection_calls": 1,
            "wrong_inspections_before_hit": 0,
        },
        "B2": {
            "score": 68,
            "bucket": "domain_signal",
            "category_choice_accuracy": False,
            "first_inspection_hit": False,
            "inspection_calls": 2,
            "wrong_inspections_before_hit": 1,
        },
    }

    summary = module.summarize(scored_tasks)

    assert summary["pct"] == 84.0
    assert summary["first_hit_rate"] == 50.0
    assert summary["category_choice_accuracy"] == 50.0
    assert summary["by_bucket"]["zone_selection"]["pct"] == 100.0
    assert summary["by_bucket"]["domain_signal"]["pct"] == 68.0
