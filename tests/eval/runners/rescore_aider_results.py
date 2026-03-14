#!/usr/bin/env python3
"""Re-score existing Aider comparison results with fixed mapper.

Reads raw responses from saved results JSON, re-applies the mapping with
logging, and outputs new scores without making any API calls.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # tests/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))  # project root

from eval.runners.run_aider_comparison import (
    map_aider_response_to_entity_ids,
    score_task,
    load_qualified_name_mapping,
    generate_summary,
)

ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = ROOT / "tests" / "eval" / "results" / "aider_comparison"


def rescore_results(results_path: Path, output_suffix: str = "_rescored") -> None:
    """Re-score results from a saved JSON file."""

    print(f"Loading results from: {results_path}")
    data = json.loads(results_path.read_text(encoding="utf-8"))

    print("Loading entity mapping...")
    qname_mapping = load_qualified_name_mapping()
    print(f"  Mappings: {len(qname_mapping)}")

    # Track all mapping logs for audit
    all_mapping_logs: Dict[str, List[Dict]] = {}

    # Re-score each condition
    new_condition_results: Dict[str, List[Dict]] = {}

    for condition, tasks in data.get("condition_results", {}).items():
        print(f"\n{'='*70}")
        print(f"Re-scoring condition: {condition}")
        print("="*70)

        new_tasks = []
        condition_logs = []

        for task in tasks:
            task_id = task["task_id"]
            raw_ids = task.get("raw_response_ids", [])
            ground_truth = task.get("ground_truth", [])

            # Re-map for Aider conditions
            if condition.startswith("aider_repomap_"):
                ranked_ids, mapping_failures, mapping_log = map_aider_response_to_entity_ids(
                    raw_ids, qname_mapping, log_attempts=True
                )
                condition_logs.extend([{"task_id": task_id, **log} for log in mapping_log])
            else:
                # CodeIR conditions - IDs are already entity IDs
                ranked_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
                mapping_failures = []

            # Re-score
            scores = score_task(ranked_ids, ground_truth)

            # Build new task result
            new_task = {
                **task,
                "ranked_entity_ids": ranked_ids[:5],
                "mapping_failures": mapping_failures,
                **scores,
            }
            new_tasks.append(new_task)

            # Print status
            hit_marker = "✓" if scores["top3_hit"] else "✗"
            old_hit = "✓" if task.get("top3_hit") else "✗"
            change = "" if hit_marker == old_hit else f" (was {old_hit})"
            print(f"  {task_id}: {hit_marker}{change} mapped={len(ranked_ids)} failures={len(mapping_failures)}")

        new_condition_results[condition] = new_tasks
        all_mapping_logs[condition] = condition_logs

        # Compute metrics
        n = len(new_tasks)
        top1 = sum(t["top1_hit"] for t in new_tasks) / n if n else 0
        top3 = sum(t["top3_hit"] for t in new_tasks) / n if n else 0
        any_hit = sum(t["any_hit"] for t in new_tasks) / n if n else 0
        total_failures = sum(len(t["mapping_failures"]) for t in new_tasks)

        print(f"\n  Top-1: {top1:.1%}")
        print(f"  Top-3: {top3:.1%}")
        print(f"  Recall: {any_hit:.1%}")
        print(f"  Total mapping failures: {total_failures}")

    # Build new results
    new_data = {
        **data,
        "rescored_at": datetime.now(timezone.utc).isoformat(),
        "condition_results": new_condition_results,
    }

    # Save rescored results
    stem = results_path.stem
    output_path = results_path.parent / f"{stem}{output_suffix}.json"
    output_path.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
    print(f"\nRescored results saved to: {output_path}")

    # Save mapping logs for audit
    logs_path = results_path.parent / f"{stem}_mapping_log.json"
    logs_path.write_text(json.dumps(all_mapping_logs, indent=2), encoding="utf-8")
    print(f"Mapping logs saved to: {logs_path}")

    # Generate summary
    metrics_by_condition = {}
    for condition, tasks in new_condition_results.items():
        n = len(tasks)
        if n == 0:
            continue
        metrics_by_condition[condition] = {
            "top1_hit_rate": sum(t["top1_hit"] for t in tasks) / n,
            "top3_hit_rate": sum(t["top3_hit"] for t in tasks) / n,
            "candidate_recall": sum(t["any_hit"] for t in tasks) / n,
            "tokens_per_task_input": sum(t.get("input_tokens", 0) for t in tasks) / n,
            "mapping_failures": sum(len(t["mapping_failures"]) for t in tasks),
        }

    summary = generate_summary(data["model"], metrics_by_condition)
    summary_path = results_path.parent / "summary_rescored.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(f"Summary saved to: {summary_path}")

    print(f"\n{summary}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Re-score Aider comparison results")
    parser.add_argument(
        "results_file",
        nargs="?",
        default=str(RESULTS_DIR / "results_haiku_4.json"),
        help="Path to results JSON file",
    )
    args = parser.parse_args()

    results_path = Path(args.results_file)
    if not results_path.exists():
        print(f"Error: Results file not found: {results_path}")
        sys.exit(1)

    rescore_results(results_path)


if __name__ == "__main__":
    main()
