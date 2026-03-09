"""Compute primary task-level metrics from unified benchmark output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


FCR_THRESHOLDS = (3, 4, 5)


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "task_count": 0,
            "top1_hit": 0.0,
            "top3_hit": 0.0,
            "expands_per_solved_task": 0.0,
            "retrieval_recall_any_rate": 0.0,
            "retrieval_recall_mean": 0.0,
            "candidate_count_mean": 0.0,
            "candidate_count_max": 0,
            "orientation_family_added_total": 0,
            "orientation_family_expansion_tasks": 0,
            "ground_truth_missing_from_index_tasks": 0,
            "ground_truth_missing_from_index_entities": 0,
            "candidate_miss_despite_index_presence": 0,
            "false_confidence_curve": {str(t): {"events": 0, "total": 0, "rate": 0.0} for t in FCR_THRESHOLDS},
            "confidence_bins": {str(i): 0 for i in range(1, 6)},
            "confidence_examples": 0,
            "calibration_status": "insufficient_data",
            "module_selection_tokens_total": 0,
            "module_selection_tokens_mean": 0.0,
        }

    top1_hits = sum(int(r.get("top1_hit", 0)) for r in rows)
    top3_hits = sum(int(r.get("top3_hit", 0)) for r in rows)
    expansions = sum(int(r.get("expansions_used", 0)) for r in rows)
    retrieval_any_hits = sum(1 for r in rows if bool(r.get("candidate_has_any_ground_truth", False)))
    retrieval_recall_sum = sum(float(r.get("candidate_recall_at_k", 0.0)) for r in rows)
    candidate_counts = [int(r.get("candidate_count", len(r.get("candidate_ids", [])))) for r in rows]
    orientation_family_added_total = sum(int(r.get("orientation_family_added_count", 0)) for r in rows)
    orientation_family_expansion_tasks = sum(1 for r in rows if int(r.get("orientation_family_added_count", 0)) > 0)
    module_selection_tokens_total = sum(int(r.get("module_selection_tokens", 0)) for r in rows)
    missing_index_tasks = sum(1 for r in rows if len(r.get("ground_truth_missing_from_index", [])) > 0)
    missing_index_entities = sum(len(r.get("ground_truth_missing_from_index", [])) for r in rows)
    candidate_miss_despite_index = sum(
        1
        for r in rows
        if int(r.get("ground_truth_in_index_count", 0)) > 0 and not bool(r.get("candidate_has_any_ground_truth", False))
    )

    bins = {str(i): 0 for i in range(1, 6)}
    for row in rows:
        c = int(row.get("confidence", 1))
        c = max(1, min(5, c))
        bins[str(c)] += 1

    fcr: Dict[str, Dict[str, Any]] = {}
    for t in FCR_THRESHOLDS:
        events = 0
        for row in rows:
            if int(row.get("top1_hit", 0)) == 1:
                continue
            if int(row.get("confidence", 1)) < t:
                continue
            if bool(row.get("needs_expansion", False)):
                continue
            events += 1
        fcr[str(t)] = {
            "events": events,
            "total": n,
            "rate": (events / n) if n else 0.0,
        }

    solved = top3_hits
    return {
        "task_count": n,
        "top1_hit": top1_hits / n,
        "top3_hit": top3_hits / n,
        "expands_per_solved_task": (expansions / solved) if solved else 0.0,
        "retrieval_recall_any_rate": retrieval_any_hits / n,
        "retrieval_recall_mean": retrieval_recall_sum / n,
        "candidate_count_mean": (sum(candidate_counts) / n) if n else 0.0,
        "candidate_count_max": max(candidate_counts) if candidate_counts else 0,
        "orientation_family_added_total": orientation_family_added_total,
        "orientation_family_expansion_tasks": orientation_family_expansion_tasks,
        "ground_truth_missing_from_index_tasks": missing_index_tasks,
        "ground_truth_missing_from_index_entities": missing_index_entities,
        "candidate_miss_despite_index_presence": candidate_miss_despite_index,
        "false_confidence_curve": fcr,
        "confidence_bins": bins,
        "confidence_examples": n,
        "calibration_status": "calibrated" if n >= 30 else "insufficient_data",
        "module_selection_tokens_total": module_selection_tokens_total,
        "module_selection_tokens_mean": (module_selection_tokens_total / n) if n else 0.0,
    }


def compute_task_metrics(results_path: Path) -> Dict[str, Any]:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    condition_results = data.get("condition_results", {})

    out = {
        "schema": "task_metrics.v1",
        "source_results": str(results_path),
        "conditions": {},
    }

    for condition, rows in condition_results.items():
        out["conditions"][condition] = _summarize(list(rows))

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute task metrics from task benchmark output")
    parser.add_argument("results_path", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    metrics = compute_task_metrics(args.results_path)
    rendered = json.dumps(metrics, indent=2)
    print(rendered)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
