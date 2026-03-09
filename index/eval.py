"""Compression level evaluation and comprehensibility floor report."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from index.indexer import index_repo
from index.store.db import connect


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_rows(repo_path: Path, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load entity+IR rows from entities.db, optionally filtered by compression level."""
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    if mode:
        rows = conn.execute(
            """
            SELECT
              e.id AS entity_id,
              e.qualified_name,
              e.kind,
              r.ir_text,
              r.ir_json,
              r.source_token_count,
              r.ir_token_count,
              r.compression_ratio,
              r.mode
            FROM entities AS e
            JOIN ir_rows AS r ON r.entity_id = e.id
            WHERE r.mode = ?
            """,
            (mode,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
              e.id AS entity_id,
              e.qualified_name,
              e.kind,
              r.ir_text,
              r.ir_json,
              r.source_token_count,
              r.ir_token_count,
              r.compression_ratio,
              r.mode
            FROM entities AS e
            JOIN ir_rows AS r ON r.entity_id = e.id
            """
        ).fetchall()
    conn.close()

    out: List[Dict[str, Any]] = []
    for row in rows:
        ir_json: Dict[str, Any]
        try:
            ir_json = json.loads(row["ir_json"])
        except Exception:
            ir_json = {}
        out.append(
            {
                "entity_id": row["entity_id"],
                "qualified_name": row["qualified_name"],
                "kind": row["kind"],
                "ir_text": row["ir_text"],
                "ir_json": ir_json,
                "source_token_count": int(row["source_token_count"]),
                "ir_token_count": int(row["ir_token_count"]),
                "compression_ratio": float(row["compression_ratio"]),
                "mode": row["mode"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Level metrics
# ---------------------------------------------------------------------------

def _level_metrics(rows: List[Dict[str, Any]], level: str) -> Dict[str, float]:
    """Compute aggregate metrics for a single compression level."""
    n = len(rows) or 1
    src_tokens = sum(int(r["source_token_count"]) for r in rows)
    ir_tokens = sum(int(r["ir_token_count"]) for r in rows)
    avg_ir_tokens = ir_tokens / n

    entities_per_200k = int(200000 / avg_ir_tokens) if avg_ir_tokens > 0 else 0

    signatures: set[str] = set()
    for row in rows:
        signatures.add(str(row.get("ir_text", "")))
    distinctness = (len(signatures) / n) if n else 0.0

    return {
        "level": level,
        "entity_count": float(len(rows)),
        "source_tokens": float(src_tokens),
        "ir_tokens": float(ir_tokens),
        "global_ratio": (ir_tokens / src_tokens) if src_tokens else 1.0,
        "avg_ir_tokens": avg_ir_tokens,
        "entities_per_200k": float(entities_per_200k),
        "distinctness": distinctness,
    }


# ---------------------------------------------------------------------------
# Level evaluation
# ---------------------------------------------------------------------------

def evaluate_compression_levels(
    repo_path: Path,
    base_config: Dict[str, Any],
    levels: tuple[str, ...] = ("L1", "L2", "L3"),
) -> Dict[str, Any]:
    """Run indexing per level and return a side-by-side scoreboard."""
    results: List[Dict[str, Any]] = []
    for level in levels:
        cfg = dict(base_config)
        cfg["compression_level"] = level
        index_repo(repo_path=repo_path, config=cfg)
        rows = _load_rows(repo_path, mode=level)
        metrics = _level_metrics(rows, level=level)
        results.append(metrics)

    return {
        "repo_path": str(repo_path),
        "levels": list(levels),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Scoreboard rendering
# ---------------------------------------------------------------------------

def render_scoreboard_markdown(report: Dict[str, Any]) -> str:
    """Render level comparison as a markdown table."""
    rows = report["results"]

    lines: List[str] = []
    lines.append("# Compression Level Scoreboard")
    lines.append("")
    lines.append(f"- repo: `{report['repo_path']}`")
    lines.append(f"- levels: `{', '.join(report['levels'])}`")
    lines.append("")

    header_cols = ["level", "global_ratio", "avg_ir_tokens", "entities_per_200k", "distinctness"]
    header = "| " + " | ".join(header_cols) + " |"
    sep = "|" + "|".join(["---" if c == "level" else "---:" for c in header_cols]) + "|"
    lines.append(header)
    lines.append(sep)
    for row in rows:
        values = [
            f"{row['level']}",
            f"{row['global_ratio']:.4f}",
            f"{row['avg_ir_tokens']:.2f}",
            f"{row['entities_per_200k']:.0f}",
            f"{row['distinctness']:.4f}",
        ]
        lines.append("| " + " | ".join(values) + " |")
    lines.append("")
    lines.append("Higher-is-better: `entities_per_200k`, `distinctness`.")
    lines.append("Lower-is-better: `global_ratio`, `avg_ir_tokens`.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comprehensibility floor report
# ---------------------------------------------------------------------------

CAPABILITIES = ("identification", "differentiation", "triage", "reconstruction")
FLOOR_THRESHOLDS = {
    "identification": 3.0,  # graded 1-5, minimum acceptable
    "triage": 0.6,          # binary pass rate, minimum acceptable
}


def load_floor_results(results_path: Path) -> Dict[str, Any]:
    """Load scored floor test results from a JSON file.

    Expected format:
    {
        "scores": [
            {"level": "L0", "capability": "identification", "score": 4.5},
            {"level": "L0", "capability": "differentiation", "score": 1.0},
            ...
        ]
    }
    """
    with results_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def floor_report(results_path: Path) -> Dict[str, Any]:
    """Produce the comprehensibility floor matrix from scored results.

    Returns:
        {
            "matrix": {
                "L0": {"identification": 5.0, "differentiation": 1.0, ...},
                "L1": {...},
                ...
            },
            "floor_level": "L1",  # lowest level meeting thresholds
            "thresholds": {...},
        }
    """
    data = load_floor_results(results_path)
    scores = data.get("scores", [])

    # Aggregate scores: level -> capability -> list of scores
    buckets: Dict[str, Dict[str, List[float]]] = {}
    for entry in scores:
        level = str(entry.get("level", ""))
        cap = str(entry.get("capability", ""))
        score = float(entry.get("score", 0))
        buckets.setdefault(level, {}).setdefault(cap, []).append(score)

    # Build matrix: level -> capability -> mean score
    matrix: Dict[str, Dict[str, float]] = {}
    for level in sorted(buckets.keys()):
        matrix[level] = {}
        for cap in CAPABILITIES:
            vals = buckets.get(level, {}).get(cap, [])
            matrix[level][cap] = (sum(vals) / len(vals)) if vals else 0.0

    # Determine floor level: highest compression (L3 > L2 > L1 > L0) that still
    # meets all thresholds. We iterate from most compressed to least.
    floor_level: Optional[str] = None
    for level in reversed(sorted(matrix.keys())):
        meets_all = True
        for cap, threshold in FLOOR_THRESHOLDS.items():
            if matrix.get(level, {}).get(cap, 0.0) < threshold:
                meets_all = False
                break
        if meets_all:
            floor_level = level
            break

    return {
        "matrix": matrix,
        "floor_level": floor_level,
        "thresholds": dict(FLOOR_THRESHOLDS),
    }


def render_floor_matrix_markdown(floor_data: Dict[str, Any]) -> str:
    """Render the comprehensibility floor matrix as markdown."""
    matrix = floor_data.get("matrix", {})
    floor_level = floor_data.get("floor_level")
    levels = sorted(matrix.keys())

    lines: List[str] = []
    lines.append("# Comprehensibility Floor Matrix")
    lines.append("")

    if floor_level:
        lines.append(f"**Floor level: {floor_level}**")
    else:
        lines.append("**Floor level: NONE (no level meets all thresholds)**")
    lines.append("")

    # Header
    header = "| Capability | " + " | ".join(levels) + " |"
    sep = "|---|" + "|".join(["---:" for _ in levels]) + "|"
    lines.append(header)
    lines.append(sep)

    for cap in CAPABILITIES:
        values = []
        for level in levels:
            score = matrix.get(level, {}).get(cap, 0.0)
            values.append(f"{score:.2f}")
        lines.append(f"| {cap} | " + " | ".join(values) + " |")

    lines.append("")
    thresholds = floor_data.get("thresholds", {})
    if thresholds:
        lines.append("Thresholds: " + ", ".join(f"{k} >= {v}" for k, v in sorted(thresholds.items())))

    return "\n".join(lines)
