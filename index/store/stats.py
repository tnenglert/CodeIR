"""Aggregation queries for CodeIR CLI stats."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from index.store.db import connect, table_exists, column_names


def _meta_int(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _meta_str(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def get_stats(repo_path: Path) -> Dict[str, Any]:
    """Compute repository stats from entities.db and mapping.db."""
    entities_db = repo_path / ".codeir" / "entities.db"
    mapping_db = repo_path / ".codeir" / "mapping.db"

    if not entities_db.exists():
        raise FileNotFoundError(f"entities DB not found: {entities_db}")
    if not mapping_db.exists():
        raise FileNotFoundError(f"mapping DB not found: {mapping_db}")

    entities_conn = connect(entities_db)
    mapping_conn = connect(mapping_db)

    # Basic entity stats
    total_entities = int(entities_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    by_kind_rows = entities_conn.execute(
        "SELECT kind, COUNT(*) AS c FROM entities GROUP BY kind ORDER BY c DESC, kind ASC"
    ).fetchall()
    files_with_entities = int(entities_conn.execute("SELECT COUNT(DISTINCT file_path) FROM entities").fetchone()[0])
    python_files_indexed = _meta_int(entities_conn, "python_files_indexed", default=0)
    compression_level = _meta_str(entities_conn, "compression_level", default="Behavior")

    # Per-level stats
    cols = column_names(entities_conn, "ir_rows")
    has_mode = "mode" in cols
    has_token_cols = "source_token_count" in cols and "ir_token_count" in cols

    level_stats: Dict[str, Dict[str, Any]] = {}
    if has_mode and has_token_cols:
        level_rows = entities_conn.execute(
            "SELECT mode, COUNT(*), COALESCE(SUM(source_token_count), 0), "
            "COALESCE(SUM(ir_token_count), 0), COALESCE(AVG(compression_ratio), 1.0) "
            "FROM ir_rows GROUP BY mode ORDER BY mode"
        ).fetchall()
        for row in level_rows:
            mode_name, count, src_tokens, ir_tokens, avg_ratio = row
            level_stats[mode_name] = {
                "entity_count": int(count),
                "source_tokens": int(src_tokens),
                "ir_tokens": int(ir_tokens),
                "ratio": float(ir_tokens) / float(src_tokens) if src_tokens else 1.0,
                "avg_entity_ratio": float(avg_ratio),
                "entities_per_200k": int(200000 / (float(ir_tokens) / int(count))) if ir_tokens > 0 else 0,
            }

    # Overall compression stats (sum across all modes, or legacy single-mode)
    if has_token_cols:
        sums = entities_conn.execute(
            "SELECT COALESCE(SUM(source_char_count), 0), COALESCE(SUM(ir_char_count), 0), "
            "COALESCE(SUM(source_token_count), 0), COALESCE(SUM(ir_token_count), 0), "
            "COALESCE(AVG(compression_ratio), 1.0) FROM ir_rows"
        ).fetchone()
        source_chars = int(sums[0])
        ir_chars = int(sums[1])
        source_tokens = int(sums[2])
        ir_tokens = int(sums[3])
        avg_ratio = float(sums[4])
        global_ratio = (ir_tokens / source_tokens) if source_tokens else 1.0
    else:
        sums = entities_conn.execute(
            "SELECT COALESCE(SUM(source_char_count), 0), COALESCE(SUM(ir_char_count), 0), "
            "COALESCE(AVG(compression_ratio), 1.0) FROM ir_rows"
        ).fetchone()
        source_chars = int(sums[0])
        ir_chars = int(sums[1])
        source_tokens = source_chars
        ir_tokens = ir_chars
        avg_ratio = float(sums[2])
        global_ratio = (ir_chars / source_chars) if source_chars else 1.0

    # Per-category stats (requires modules table)
    category_stats: List[Dict[str, Any]] = []
    if table_exists(entities_conn, "modules"):
        cat_rows = entities_conn.execute(
            "SELECT m.category, COUNT(DISTINCT m.file_path), COUNT(e.id) "
            "FROM modules m LEFT JOIN entities e ON e.file_path = m.file_path "
            "GROUP BY m.category ORDER BY COUNT(e.id) DESC"
        ).fetchall()
        for row in cat_rows:
            category_stats.append({
                "category": row[0],
                "file_count": int(row[1]),
                "entity_count": int(row[2]),
            })

    # Complexity class distribution
    complexity_stats: Dict[str, int] = {}
    ecols = column_names(entities_conn, "entities")
    if "complexity_class" in ecols:
        cc_rows = entities_conn.execute(
            "SELECT complexity_class, COUNT(*) FROM entities "
            "WHERE complexity_class IS NOT NULL GROUP BY complexity_class"
        ).fetchall()
        for row in cc_rows:
            complexity_stats[row[0]] = int(row[1])

    abbreviation_count = int(mapping_conn.execute("SELECT COUNT(*) FROM abbreviations").fetchone()[0])

    entities_conn.close()
    mapping_conn.close()

    by_kind: List[Dict[str, Any]] = [{"kind": row[0], "count": int(row[1])} for row in by_kind_rows]
    coverage_pct = (files_with_entities / python_files_indexed * 100.0) if python_files_indexed else 0.0

    return {
        "entity_count": total_entities,
        "entities_by_kind": by_kind,
        "file_coverage": {
            "files_with_entities": files_with_entities,
            "python_files_indexed": python_files_indexed,
            "coverage_percent": coverage_pct,
        },
        "compression_level": compression_level,
        "compression": {
            "source_char_count": source_chars,
            "ir_char_count": ir_chars,
            "source_token_count": source_tokens,
            "ir_token_count": ir_tokens,
            "global_ratio": global_ratio,
            "avg_entity_ratio": avg_ratio,
        },
        "level_stats": level_stats,
        "category_stats": category_stats,
        "complexity_stats": complexity_stats,
        "abbreviation_count": abbreviation_count,
    }
