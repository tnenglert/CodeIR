"""Relevance scoring and ranking for entity IR rows against natural-language queries."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def score_query(row: Dict[str, Any], query_terms: List[str]) -> int:
    """Score an entity row's relevance to query terms.

    Checks qualified_name (weight 4), ir_text (3), and kind (1).
    """
    qn = str(row["qualified_name"]).lower()
    ir = str(row["ir_text"]).lower()
    kind = str(row["kind"]).lower()
    score = 0
    for t in query_terms:
        if t in qn:
            score += 4
        if t in ir:
            score += 3
        if t in kind:
            score += 1
    return score


def rank_by_query(
    rows: List[Dict[str, Any]], query: str, top_k: int = 0,
) -> List[Dict[str, Any]]:
    """Rank entity rows by relevance to a query string.

    Returns all rows sorted by score (descending), then entity_id.
    If top_k > 0, returns only the top_k results.
    """
    terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(t) >= 2]
    scored = [(score_query(row, terms), row) for row in rows]
    scored.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
    ranked = [row for _, row in scored]
    if top_k > 0:
        return ranked[:top_k]
    return ranked
