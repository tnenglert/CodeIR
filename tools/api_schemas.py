"""Tool-facing wrappers around CodeIR index APIs.

These wrappers return JSON-serializable dict payloads for agent tool-calling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from index.locator import extract_code_slice
from index.search import search_entities as _search_entities
from index.store.fetch import get_entity_location, get_entity_with_ir


def _repo_path(repo_path: str | Path = ".") -> Path:
    return Path(repo_path).resolve()


def _error(message: str, hint: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "error": message}
    if hint:
        out["hint"] = hint
    return out


def search_entities(query: str, repo_path: str | Path = ".", limit: int = 20) -> Dict[str, Any]:
    """Search indexed entities by query text."""
    if not str(query).strip():
        return _error("query must be non-empty")

    try:
        results = _search_entities(query=query, repo_path=_repo_path(repo_path), limit=int(limit))
    except FileNotFoundError as exc:
        return _error(str(exc), hint="Run index first: codeir index <repo_path>")

    return {
        "ok": True,
        "query": query,
        "count": len(results),
        "results": results,
    }


def get_entity_ir(entity_id: str, repo_path: str | Path = ".", level: str = "Behavior") -> Dict[str, Any]:
    """Fetch compressed IR for one entity ID."""
    if not str(entity_id).strip():
        return _error("entity_id must be non-empty")

    try:
        row = get_entity_with_ir(
            repo_path=_repo_path(repo_path),
            entity_id=entity_id,
            mode=str(level),
        )
    except FileNotFoundError as exc:
        return _error(str(exc), hint="Run index first: codeir index <repo_path>")

    if not row:
        return _error(
            f"entity not found: {entity_id}",
            hint=f"Check ID and level (requested level={level}).",
        )

    return {
        "ok": True,
        "entity": row,
    }


def expand_entity_code(entity_id: str, repo_path: str | Path = ".") -> Dict[str, Any]:
    """Fetch raw source snippet and location metadata for one entity ID."""
    if not str(entity_id).strip():
        return _error("entity_id must be non-empty")

    repo = _repo_path(repo_path)
    try:
        loc = get_entity_location(repo_path=repo, entity_id=entity_id)
    except FileNotFoundError as exc:
        return _error(str(exc), hint="Run index first: codeir index <repo_path>")

    if not loc:
        return _error(f"entity not found: {entity_id}")

    source = extract_code_slice(
        repo_path=repo,
        file_path=str(loc["file_path"]),
        start_line=int(loc["start_line"]),
        end_line=int(loc["end_line"]),
    )

    return {
        "ok": True,
        "entity_id": str(loc["entity_id"]),
        "qualified_name": str(loc["qualified_name"]),
        "kind": str(loc["kind"]),
        "file": str(loc["file_path"]),
        "span": [int(loc["start_line"]), int(loc["end_line"])],
        "source": source,
    }
