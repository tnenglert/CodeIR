"""Score model responses against tool task ground truth.

Usage:
    python score_tool_tasks.py <response_file> --task-pack <task_pack.json>

Response file format (JSON):
{
  "model": "claude-3-opus",
  "responses": {
    "A1": {
      "tool_calls": ["search auth", "show ATHNTCT.02", "expand ATHNTCT.02"],
      "answer": "The authentication happens in... timing attack mitigation...",
      "entities_mentioned": ["ATHNTCT.02", "HASH.02"]
    },
    ...
  }
}

Output: scoring report with per-task breakdown and summary.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple


def load_task_pack(path: Path) -> Dict:
    """Load task pack JSON."""
    with open(path) as f:
        return json.load(f)


def load_responses(path: Path) -> Dict:
    """Load model response JSON."""
    with open(path) as f:
        return json.load(f)


def extract_entities_from_text(text: str) -> List[str]:
    """Extract entity IDs mentioned in text."""
    # Match patterns like ATHNTCT.02, HASH, VRFY.02, etc.
    pattern = r'\b([A-Z]{2,}(?:\.[A-Z0-9]+)?(?:\.\d{2})?)\b'
    matches = re.findall(pattern, text)
    return list(set(matches))


def check_concepts_mentioned(text: str, concepts: List[str]) -> List[str]:
    """Check which concepts are mentioned in text (case-insensitive)."""
    text_lower = text.lower()
    return [c for c in concepts if c.lower() in text_lower]


def check_files_mentioned(text: str, files: List[str]) -> List[str]:
    """Check which files are mentioned in text."""
    mentioned = []
    for f in files:
        # Check for full path or just filename
        if f in text or f.split('/')[-1] in text:
            mentioned.append(f)
    return mentioned


def score_targeted_bug(task: Dict, response: Dict) -> Tuple[int, Dict]:
    """Score a targeted_bug task."""
    gt = task["ground_truth"]
    scoring = task["scoring"]
    details = {}
    total = 0

    # Check entities found
    answer = response.get("answer", "")
    entities_mentioned = response.get("entities_mentioned", [])
    if not entities_mentioned:
        entities_mentioned = extract_entities_from_text(answer)

    found = [e for e in gt["required_entities"] if e in entities_mentioned]
    if found:
        points = scoring.get("found_entities", 0) * len(found) / len(gt["required_entities"])
        total += points
        details["found_entities"] = {"found": found, "points": points}

    # Check concepts
    if "required_concepts" in gt:
        concepts_found = check_concepts_mentioned(answer, gt["required_concepts"])
        if concepts_found:
            points = scoring.get("mentioned_concepts", 0) * len(concepts_found) / len(gt["required_concepts"])
            total += points
            details["concepts"] = {"found": concepts_found, "points": points}

    # Check file identified
    if "key_file" in gt and gt["key_file"] in answer:
        points = scoring.get("identified_file", 0)
        total += points
        details["file"] = {"found": gt["key_file"], "points": points}

    return int(total), details


def score_conceptual_understanding(task: Dict, response: Dict) -> Tuple[int, Dict]:
    """Score a conceptual_understanding task."""
    gt = task["ground_truth"]
    scoring = task["scoring"]
    details = {}
    total = 0

    answer = response.get("answer", "")
    entities_mentioned = response.get("entities_mentioned", [])
    if not entities_mentioned:
        entities_mentioned = extract_entities_from_text(answer)

    # Check entities found
    found = [e for e in gt["required_entities"] if e in entities_mentioned]
    if found:
        points = scoring.get("found_entities", 0) * len(found) / len(gt["required_entities"])
        total += points
        details["found_entities"] = {"found": found, "points": points}

    # Check flow order (if applicable)
    if "flow_order" in gt:
        flow = gt["flow_order"]
        answer_lower = answer.lower()
        # Check if concepts appear in order
        positions = []
        for concept in flow:
            pos = answer_lower.find(concept.lower().replace("_", " "))
            if pos == -1:
                pos = answer_lower.find(concept.lower())
            positions.append(pos if pos >= 0 else float('inf'))

        # Count how many are in correct relative order
        in_order = sum(1 for i in range(len(positions)-1) if positions[i] < positions[i+1])
        if in_order > 0:
            points = scoring.get("correct_flow_order", 0) * in_order / (len(flow) - 1)
            total += points
            details["flow_order"] = {"in_order": in_order, "points": points}

    # Check files
    if "key_files" in gt:
        files_found = check_files_mentioned(answer, gt["key_files"])
        if files_found:
            points = scoring.get("identified_entry_points", 0) * len(files_found) / len(gt["key_files"])
            total += points
            details["files"] = {"found": files_found, "points": points}

    return int(total), details


def score_medium_refactor(task: Dict, response: Dict) -> Tuple[int, Dict]:
    """Score a medium_refactor task."""
    gt = task["ground_truth"]
    scoring = task["scoring"]
    details = {}
    total = 0

    answer = response.get("answer", "")
    entities_mentioned = response.get("entities_mentioned", [])
    if not entities_mentioned:
        entities_mentioned = extract_entities_from_text(answer)

    # Check primary entity
    found = [e for e in gt["required_entities"] if e in entities_mentioned]
    if found:
        points = scoring.get("found_primary_entity", 0) if len(found) >= 1 else 0
        if "found_manager_entities" in scoring:
            # Split scoring for manager/router
            points = scoring.get("found_manager_entities", 0) * len(found) / len(gt["required_entities"])
        total += points
        details["found_entities"] = {"found": found, "points": points}

    # Check failure points or entry points identified
    if "failure_points" in gt:
        failure_count = 0
        for fp in gt["failure_points"]:
            if fp["description"].lower() in answer.lower():
                failure_count += 1
            elif str(fp.get("line", "")) in answer:
                failure_count += 1
        if failure_count > 0:
            points = scoring.get("identified_failure_points", 0) * failure_count / len(gt["failure_points"])
            total += points
            details["failure_points"] = {"count": failure_count, "points": points}

    if "entry_points" in gt:
        entry_count = sum(1 for ep in gt["entry_points"] if ep.lower().replace(".", " ") in answer.lower() or ep in answer)
        if entry_count > 0:
            points = scoring.get("identified_all_entry_points", 0) * entry_count / len(gt["entry_points"])
            total += points
            details["entry_points"] = {"count": entry_count, "points": points}

    return int(total), details


def score_dependency_sensitive(task: Dict, response: Dict) -> Tuple[int, Dict]:
    """Score a dependency_sensitive task."""
    gt = task["ground_truth"]
    scoring = task["scoring"]
    details = {}
    total = 0

    answer = response.get("answer", "")
    tool_calls = response.get("tool_calls", [])
    entities_mentioned = response.get("entities_mentioned", [])
    if not entities_mentioned:
        entities_mentioned = extract_entities_from_text(answer)

    # Check found entity
    found = [e for e in gt["required_entities"] if e in entities_mentioned]
    if found:
        points = scoring.get("found_entity", 0)
        total += points
        details["found_entity"] = {"found": found, "points": points}

    # Check if impact or callers was used
    tool_calls_str = " ".join(str(tc) for tc in tool_calls).lower()
    if "impact" in tool_calls_str or "callers" in tool_calls_str:
        points = scoring.get("ran_impact_or_callers", 0)
        total += points
        details["ran_analysis"] = {"points": points}

    # Check direct callers identified
    if "direct_callers" in gt:
        callers_found = [c for c in gt["direct_callers"] if c in entities_mentioned or c.lower() in answer.lower()]
        if callers_found:
            points = scoring.get("identified_direct_callers", 0) * len(callers_found) / len(gt["direct_callers"])
            total += points
            details["callers"] = {"found": callers_found, "points": points}

    # Check if recognized ambiguity (for D2 type)
    if gt.get("expected_ambiguity"):
        if "ambiguous" in answer.lower() or "multiple" in answer.lower() or "candidates" in answer.lower():
            points = scoring.get("recognized_ambiguity", 0)
            total += points
            details["ambiguity"] = {"recognized": True, "points": points}

        # Check grep fallback
        if "grep" in tool_calls_str or "grep" in answer.lower():
            points = scoring.get("used_grep_fallback", 0)
            total += points
            details["grep_fallback"] = {"used": True, "points": points}

    return int(total), details


def score_search_failure(task: Dict, response: Dict) -> Tuple[int, Dict]:
    """Score a search_failure task."""
    gt = task["ground_truth"]
    scoring = task["scoring"]
    details = {}
    total = 0

    answer = response.get("answer", "")
    tool_calls = response.get("tool_calls", [])
    entities_mentioned = response.get("entities_mentioned", [])
    if not entities_mentioned:
        entities_mentioned = extract_entities_from_text(answer)

    # Check found relevant entities
    found = [e for e in gt["required_entities"] if e in entities_mentioned]
    if found:
        points = scoring.get("found_relevant_entities", 0) * len(found) / len(gt["required_entities"])
        if "found_entity" in scoring:
            points = scoring.get("found_entity", 0) if found else 0
        total += points
        details["found_entities"] = {"found": found, "points": points}

    # Check key pattern/exception identified
    if "key_pattern" in gt and gt["key_pattern"].lower() in answer.lower():
        points = scoring.get("identified_exception", 0)
        total += points
        details["pattern"] = {"found": gt["key_pattern"], "points": points}

    # Check if recognized hook pattern (for E2)
    if "key_insight" in gt:
        insight_keywords = ["hook", "override", "empty", "subclass", "implement"]
        if any(kw in answer.lower() for kw in insight_keywords):
            points = scoring.get("recognized_hook_pattern", 0)
            total += points
            details["insight"] = {"recognized": True, "points": points}

    return int(total), details


SCORERS = {
    "targeted_bug": score_targeted_bug,
    "conceptual_understanding": score_conceptual_understanding,
    "medium_refactor": score_medium_refactor,
    "dependency_sensitive": score_dependency_sensitive,
    "search_failure": score_search_failure,
}


def score_response(task: Dict, response: Dict) -> Tuple[int, int, Dict]:
    """Score a single response against task ground truth.

    Returns (score, max_possible, details)
    """
    task_type = task["type"]
    scorer = SCORERS.get(task_type)
    if not scorer:
        return 0, 100, {"error": f"Unknown task type: {task_type}"}

    max_possible = sum(task["scoring"].values())
    score, details = scorer(task, response)
    return score, max_possible, details


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Score model responses against task pack")
    parser.add_argument("response_file", type=Path, help="JSON file with model responses")
    parser.add_argument("--task-pack", type=Path, required=True, help="JSON task pack to score against")
    parser.add_argument("--output", type=Path, help="Output JSON file for results")
    args = parser.parse_args()

    task_pack = load_task_pack(args.task_pack)
    responses = load_responses(args.response_file)

    tasks_by_id = {t["id"]: t for t in task_pack["tasks"]}
    model = responses.get("model", "unknown")

    results = {
        "model": model,
        "task_pack": str(args.task_pack),
        "scores": {},
        "by_type": {},
        "summary": {}
    }

    total_score = 0
    total_max = 0

    print(f"\n{'='*60}")
    print(f"Scoring: {model}")
    print(f"{'='*60}\n")

    for task_id, response in responses.get("responses", {}).items():
        task = tasks_by_id.get(task_id)
        if not task:
            print(f"[WARN] Unknown task: {task_id}")
            continue

        score, max_possible, details = score_response(task, response)
        total_score += score
        total_max += max_possible

        pct = (score / max_possible * 100) if max_possible > 0 else 0
        status = "PASS" if pct >= 70 else "PARTIAL" if pct >= 40 else "FAIL"

        results["scores"][task_id] = {
            "score": score,
            "max": max_possible,
            "pct": round(pct, 1),
            "status": status,
            "details": details
        }

        # Aggregate by type
        task_type = task["type"]
        if task_type not in results["by_type"]:
            results["by_type"][task_type] = {"score": 0, "max": 0, "count": 0}
        results["by_type"][task_type]["score"] += score
        results["by_type"][task_type]["max"] += max_possible
        results["by_type"][task_type]["count"] += 1

        print(f"[{status:7}] {task_id}: {score}/{max_possible} ({pct:.0f}%) - {task['type']}")
        if details:
            for key, val in details.items():
                if isinstance(val, dict) and "points" in val:
                    print(f"          {key}: +{val['points']:.0f}")

    # Summary
    overall_pct = (total_score / total_max * 100) if total_max > 0 else 0
    results["summary"] = {
        "total_score": total_score,
        "total_max": total_max,
        "pct": round(overall_pct, 1)
    }

    print(f"\n{'='*60}")
    print(f"Summary by Type:")
    print(f"{'='*60}")
    for task_type, data in results["by_type"].items():
        pct = (data["score"] / data["max"] * 100) if data["max"] > 0 else 0
        print(f"  {task_type:25} {data['score']:3}/{data['max']:3} ({pct:.0f}%)")

    print(f"\n{'='*60}")
    print(f"OVERALL: {total_score}/{total_max} ({overall_pct:.0f}%)")
    print(f"{'='*60}\n")

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {args.output}")

    return 0 if overall_pct >= 60 else 1


if __name__ == "__main__":
    sys.exit(main())
