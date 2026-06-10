"""Score the conservative full-bearings A/B benchmark.

Usage:
    python tests/eval/score_bearings_ab.py responses.json \
        --task-pack tests/eval/task_pack_bearings_ab_v1.json
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENTITY_RE = re.compile(r"\b([A-Z]{2,}(?:\.[A-Z0-9]+)?(?:\.\d{2})?)\b")
INSPECTION_COMMANDS = {"show", "expand", "scope", "callers", "impact", "trace"}
FLAGS_WITH_VALUES = {
    "--repo-path",
    "--level",
    "--depth",
    "--resolution",
    "--limit",
    "--category",
    "--path",
    "-C",
    "-A",
    "-B",
    "-o",
    "--output",
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def extract_entities_from_text(text: str) -> List[str]:
    return sorted(set(ENTITY_RE.findall(text or "")))


def _parse_codeir_call(command: str) -> Optional[Tuple[str, List[str]]]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts or parts[0] != "codeir" or len(parts) < 2:
        return None

    subcommand = parts[1]
    positionals: List[str] = []
    i = 2
    while i < len(parts):
        token = parts[i]
        if token == "--":
            positionals.extend(parts[i + 1 :])
            break
        if token.startswith("-"):
            if token in FLAGS_WITH_VALUES and i + 1 < len(parts):
                i += 2
                continue
            i += 1
            continue
        positionals.append(token)
        i += 1
    return subcommand, positionals


def _inspection_entities(tool_calls: List[str]) -> List[List[str]]:
    inspected: List[List[str]] = []
    for call in tool_calls:
        parsed = _parse_codeir_call(str(call))
        if not parsed:
            continue
        subcommand, positionals = parsed
        if subcommand not in INSPECTION_COMMANDS:
            continue
        if subcommand == "trace":
            inspected.append(positionals[:2])
        else:
            inspected.append(positionals[:])
    return inspected


def _chosen_categories(tool_calls: List[str], answer: str, accepted_categories: List[str]) -> bool:
    lowered_answer = answer.lower()
    accepted = {cat.lower() for cat in accepted_categories}
    for call in tool_calls:
        parsed = _parse_codeir_call(str(call))
        if not parsed:
            continue
        subcommand, positionals = parsed
        if subcommand != "bearings" or not positionals:
            continue
        if positionals[0].lower() in accepted:
            return True
    return any(cat in lowered_answer for cat in accepted)


def score_task(task: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    gt = task["ground_truth"]
    required = set(gt["required_entities"])
    accepted_categories = list(gt.get("accepted_categories", []))
    answer = response.get("answer", "")
    tool_calls = [str(tc) for tc in response.get("tool_calls", [])]
    entities_mentioned = set(response.get("entities_mentioned") or extract_entities_from_text(answer))

    found_target = bool(required & entities_mentioned)
    file_mentioned = gt["key_file"] in answer or Path(gt["key_file"]).name in answer
    category_choice = _chosen_categories(tool_calls, answer, accepted_categories)

    inspections = _inspection_entities(tool_calls)
    first_hit_index: Optional[int] = None
    first_hit_entities: List[str] = []
    wrong_before_hit = 0
    for idx, entity_group in enumerate(inspections):
        if required & set(entity_group):
            first_hit_index = idx
            first_hit_entities = entity_group
            break
        wrong_before_hit += 1

    score = 0
    if found_target:
        score += 45
    if file_mentioned:
        score += 15
    if category_choice:
        score += 15
    if first_hit_index == 0:
        score += 15
    elif first_hit_index == 1:
        score += 8
    if first_hit_index is not None:
        if wrong_before_hit == 0:
            score += 10
        elif wrong_before_hit == 1:
            score += 5

    return {
        "bucket": gt["bucket"],
        "found_target": found_target,
        "file_mentioned": file_mentioned,
        "category_choice_accuracy": category_choice,
        "inspection_calls": len(inspections),
        "wrong_inspections_before_hit": wrong_before_hit if first_hit_index is not None else None,
        "first_inspection_hit": first_hit_index == 0 if first_hit_index is not None else False,
        "first_hit_entities": first_hit_entities,
        "score": score,
    }


def summarize(scored_tasks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    total_score = sum(item["score"] for item in scored_tasks.values())
    total_max = 100 * len(scored_tasks)

    first_hit_rate = 0.0
    category_choice_rate = 0.0
    inspection_counts: List[int] = []
    wrong_before_hit_values: List[int] = []
    by_bucket: Dict[str, Dict[str, Any]] = {}

    for item in scored_tasks.values():
        if item["first_inspection_hit"]:
            first_hit_rate += 1
        if item["category_choice_accuracy"]:
            category_choice_rate += 1
        inspection_counts.append(item["inspection_calls"])
        if item["wrong_inspections_before_hit"] is not None:
            wrong_before_hit_values.append(item["wrong_inspections_before_hit"])

        bucket = item["bucket"]
        bucket_stats = by_bucket.setdefault(
            bucket,
            {
                "count": 0,
                "score": 0,
                "first_hit": 0,
                "category_choice": 0,
                "inspection_calls": [],
            },
        )
        bucket_stats["count"] += 1
        bucket_stats["score"] += item["score"]
        bucket_stats["inspection_calls"].append(item["inspection_calls"])
        if item["first_inspection_hit"]:
            bucket_stats["first_hit"] += 1
        if item["category_choice_accuracy"]:
            bucket_stats["category_choice"] += 1

    task_count = len(scored_tasks) or 1
    summary = {
        "total_score": total_score,
        "total_max": total_max,
        "pct": round((total_score / total_max) * 100, 1) if total_max else 0.0,
        "first_hit_rate": round((first_hit_rate / task_count) * 100, 1),
        "category_choice_accuracy": round((category_choice_rate / task_count) * 100, 1),
        "avg_inspection_calls": round(sum(inspection_counts) / len(inspection_counts), 2) if inspection_counts else 0.0,
        "avg_wrong_before_hit": round(sum(wrong_before_hit_values) / len(wrong_before_hit_values), 2) if wrong_before_hit_values else None,
        "by_bucket": {},
    }

    for bucket, stats in by_bucket.items():
        summary["by_bucket"][bucket] = {
            "count": stats["count"],
            "pct": round(stats["score"] / (stats["count"] * 100) * 100, 1) if stats["count"] else 0.0,
            "first_hit_rate": round(stats["first_hit"] / stats["count"] * 100, 1) if stats["count"] else 0.0,
            "category_choice_accuracy": round(stats["category_choice"] / stats["count"] * 100, 1) if stats["count"] else 0.0,
            "avg_inspection_calls": round(sum(stats["inspection_calls"]) / len(stats["inspection_calls"]), 2)
            if stats["inspection_calls"]
            else 0.0,
        }

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the full-bearings A/B benchmark")
    parser.add_argument("response_file", type=Path, help="JSON file with model responses")
    parser.add_argument("--task-pack", type=Path, required=True, help="Task pack JSON")
    parser.add_argument("--output", type=Path, help="Optional output JSON path")
    args = parser.parse_args()

    task_pack = load_json(args.task_pack)
    responses = load_json(args.response_file)
    tasks_by_id = {task["id"]: task for task in task_pack["tasks"]}

    scored_tasks: Dict[str, Dict[str, Any]] = {}
    for task_id, response in responses.get("responses", {}).items():
        task = tasks_by_id.get(task_id)
        if not task:
            continue
        scored_tasks[task_id] = score_task(task, response)

    summary = summarize(scored_tasks)
    result = {
        "model": responses.get("model", "unknown"),
        "task_pack": str(args.task_pack),
        "scores": scored_tasks,
        "summary": summary,
    }

    print(f"\nScoring full-bearings A/B benchmark for {result['model']}\n")
    for task_id in sorted(scored_tasks):
        item = scored_tasks[task_id]
        print(
            f"{task_id}: {item['score']}/100 | bucket={item['bucket']} | "
            f"category_choice={item['category_choice_accuracy']} | "
            f"first_hit={item['first_inspection_hit']} | "
            f"inspection_calls={item['inspection_calls']} | "
            f"wrong_before_hit={item['wrong_inspections_before_hit']}"
        )

    print("\nSummary")
    print(f"  Overall: {summary['total_score']}/{summary['total_max']} ({summary['pct']}%)")
    print(f"  First inspection hit rate: {summary['first_hit_rate']}%")
    print(f"  Category choice accuracy: {summary['category_choice_accuracy']}%")
    print(f"  Avg inspection calls: {summary['avg_inspection_calls']}")
    if summary["avg_wrong_before_hit"] is not None:
        print(f"  Avg wrong inspections before hit: {summary['avg_wrong_before_hit']}")
    for bucket, stats in sorted(summary["by_bucket"].items()):
        print(
            f"  {bucket}: {stats['pct']}% | first_hit={stats['first_hit_rate']}% | "
            f"category_choice={stats['category_choice_accuracy']}% | "
            f"avg_inspection_calls={stats['avg_inspection_calls']}"
        )

    if args.output:
        with args.output.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
