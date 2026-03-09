"""Build a source-only retrieval corpus for baseline RAG evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from index.locator import extract_code_slice
from index.store.db import connect


def _load_entity_rows(repo_path: Path) -> List[Dict[str, Any]]:
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = None
    rows = conn.execute(
        "SELECT id, qualified_name, kind, file_path, start_line, end_line "
        "FROM entities ORDER BY id"
    ).fetchall()
    conn.close()

    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "entity_id": str(row[0]),
                "qualified_name": str(row[1]),
                "kind": str(row[2]),
                "file_path": str(row[3]),
                "start_line": int(row[4]),
                "end_line": int(row[5]),
            }
        )
    return out


def build_raw_retrieval_corpus(repo_path: Path, output_path: Path) -> Dict[str, Any]:
    """Build and persist a retrieval corpus containing only raw-source docs."""
    entities = _load_entity_rows(repo_path)
    documents: List[Dict[str, Any]] = []

    for entity in entities:
        source = extract_code_slice(
            repo_path=repo_path,
            file_path=entity["file_path"],
            start_line=entity["start_line"],
            end_line=entity["end_line"],
        )
        search_text = (
            f"entity_id: {entity['entity_id']}\n"
            f"qualified_name: {entity['qualified_name']}\n"
            f"kind: {entity['kind']}\n"
            f"file: {entity['file_path']}\n"
            f"source:\n{source}"
        )

        documents.append(
            {
                **entity,
                "source": source,
                "search_text": search_text,
            }
        )

    payload = {
        "schema": "raw_retrieval_corpus.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_path": str(repo_path),
        "document_count": len(documents),
        "documents": documents,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build raw retrieval corpus for baseline RAG tests")
    parser.add_argument("repo_path", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/corpus/raw_retrieval_corpus.json"),
    )
    args = parser.parse_args()

    payload = build_raw_retrieval_corpus(args.repo_path.resolve(), args.output)
    print(
        f"Wrote {payload['document_count']} source docs to {args.output}"
    )


if __name__ == "__main__":
    main()
