"""Scoring utilities for LLM prompt benchmark outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _load_answer_key(answer_key_path: Path) -> Dict[str, Dict[str, Any]]:
    with answer_key_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("answer key must be a JSON list")

    out: Dict[str, Dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id", "")).strip()
        expected = item.get("expected_entity_ids", [])
        if not case_id or not isinstance(expected, list):
            continue
        out[case_id] = {
            "case_id": case_id,
            "expected_entity_ids": [str(x) for x in expected],
            "query": str(item.get("query", "")),
        }
    if not out:
        raise ValueError("answer key contains no valid cases")
    return out


def _parse_selected_ids(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x) for x in value][:3]
    if isinstance(value, str):
        # Accept comma-separated fallback.
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return parts[:3]
    return []


def _load_predictions(predictions_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    text = predictions_path.read_text(encoding="utf-8", errors="ignore")
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id", "")).strip()
        mode = str(item.get("mode", "")).strip().lower() or "unknown"
        selected = _parse_selected_ids(item.get("selected_entity_ids"))
        if not case_id:
            continue
        rows.append(
            {
                "case_id": case_id,
                "mode": mode,
                "selected_entity_ids": selected,
                "lineno": lineno,
            }
        )
    if not rows:
        raise ValueError("predictions file contains no valid JSONL rows")
    return rows


def score_llm_predictions(answer_key_path: Path, predictions_path: Path) -> Dict[str, Any]:
    """Score LLM outputs against benchmark answer key.

    Predictions format: JSONL rows with keys:
    - case_id (required)
    - mode (optional but recommended)
    - selected_entity_ids (list[str] or comma-separated string)
    """
    key = _load_answer_key(answer_key_path)
    preds = _load_predictions(predictions_path)

    by_mode: Dict[str, List[Tuple[str, List[str]]]] = {}
    for pred in preds:
        by_mode.setdefault(pred["mode"], []).append((pred["case_id"], pred["selected_entity_ids"]))

    results: List[Dict[str, Any]] = []
    for mode, items in sorted(by_mode.items()):
        case_count = 0
        hits = 0
        fp_total = 0
        precision_sum = 0.0
        recall_sum = 0.0
        missing_cases = 0

        for case_id, selected in items:
            truth = key.get(case_id)
            if not truth:
                continue
            case_count += 1
            expected = set(truth["expected_entity_ids"])
            picked = selected[:3]
            picked_set = set(picked)
            tp = len(picked_set & expected)
            fp = len([eid for eid in picked if eid not in expected])

            if tp > 0:
                hits += 1
            fp_total += fp
            precision_sum += (tp / max(1, len(picked)))
            recall_sum += (tp / max(1, len(expected)))

        # cases in key that this mode did not produce outputs for
        produced_case_ids = {case_id for case_id, _ in items}
        for case_id in key.keys():
            if case_id not in produced_case_ids:
                missing_cases += 1

        denom = max(1, case_count)
        results.append(
            {
                "mode": mode,
                "scored_cases": case_count,
                "missing_cases": missing_cases,
                "top3_accuracy": hits / denom,
                "avg_false_positives_top3": fp_total / denom,
                "precision_at_3": precision_sum / denom,
                "recall_at_3": recall_sum / denom,
            }
        )

    return {
        "answer_key_path": str(answer_key_path),
        "predictions_path": str(predictions_path),
        "total_cases": len(key),
        "results": results,
    }


def render_llm_scoring_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# LLM Benchmark Scoring")
    lines.append("")
    lines.append(f"- answer_key: `{report['answer_key_path']}`")
    lines.append(f"- predictions: `{report['predictions_path']}`")
    lines.append(f"- total_cases: `{report['total_cases']}`")
    lines.append("")
    lines.append("| mode | scored_cases | missing_cases | top3_accuracy | avg_false_positives_top3 | precision_at_3 | recall_at_3 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in report["results"]:
        lines.append(
            f"| {row['mode']} | {row['scored_cases']} | {row['missing_cases']} | "
            f"{row['top3_accuracy']:.4f} | {row['avg_false_positives_top3']:.4f} | "
            f"{row['precision_at_3']:.4f} | {row['recall_at_3']:.4f} |"
        )
    lines.append("")
    lines.append("Higher-is-better: `top3_accuracy`, `precision_at_3`, `recall_at_3`.")
    lines.append("Lower-is-better: `avg_false_positives_top3`, `missing_cases`.")
    return "\n".join(lines)
