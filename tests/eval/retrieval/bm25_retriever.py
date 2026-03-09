"""SQLite FTS5 BM25 retriever over source-only corpus documents."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List


_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _normalize_match_query(query: str) -> str:
    tokens = _WORD_RE.findall(query)
    if not tokens:
        return ""
    return " OR ".join(tokens)


class BM25Retriever:
    def __init__(self, corpus_docs: List[Dict[str, Any]]) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
            "CREATE VIRTUAL TABLE docs USING fts5("
            "entity_id UNINDEXED, qualified_name, kind, file_path, source, search_text"
            ")"
        )
        rows = [
            (
                d["entity_id"],
                d.get("qualified_name", ""),
                d.get("kind", ""),
                d.get("file_path", ""),
                d.get("source", ""),
                d.get("search_text", ""),
            )
            for d in corpus_docs
        ]
        self._conn.executemany(
            "INSERT INTO docs(entity_id, qualified_name, kind, file_path, source, search_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    @classmethod
    def from_corpus_path(cls, corpus_path: Path) -> "BM25Retriever":
        data = json.loads(corpus_path.read_text(encoding="utf-8"))
        docs = list(data.get("documents", []))
        return cls(docs)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        match_q = _normalize_match_query(query)
        if not match_q:
            return []

        cursor = self._conn.execute(
            "SELECT entity_id, qualified_name, kind, file_path, source, "
            "bm25(docs) AS score "
            "FROM docs WHERE docs MATCH ? "
            "ORDER BY score ASC LIMIT ?",
            (match_q, int(top_k)),
        )
        rows = cursor.fetchall()
        return [
            {
                "entity_id": str(r[0]),
                "qualified_name": str(r[1]),
                "kind": str(r[2]),
                "file_path": str(r[3]),
                "source": str(r[4]),
                "score": float(r[5]),
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


__all__ = ["BM25Retriever"]
