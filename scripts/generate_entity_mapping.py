#!/usr/bin/env python3
"""Generate entity ID to qualified name mapping for Aider benchmark comparison.

This creates a JSON file mapping SemanticIR entity IDs to their qualified names,
which is needed to score Aider's output (which uses qualified names) against
SemanticIR's ground truth (which uses entity IDs).
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_PATH = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"
OUTPUT_DIR = ROOT / "tests" / "eval" / "baselines" / "aider"


def main():
    db_path = REPO_PATH / ".semanticir" / "entities.db"

    if not db_path.exists():
        print(f"Error: Index not found at {db_path}")
        print("Run: python cli.py index tests/testRepositories/_fastapi-users-master")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all entities with their qualified names
    rows = conn.execute("""
        SELECT id, qualified_name, kind, file_path, start_line
        FROM entities
    """).fetchall()

    conn.close()

    # Build mapping: entity_id -> qualified_name
    mapping = {}
    for row in rows:
        mapping[row["id"]] = {
            "qualified_name": row["qualified_name"],
            "kind": row["kind"],
            "file_path": row["file_path"],
            "start_line": row["start_line"],
        }

    # Also build reverse mapping for scoring
    reverse_mapping = {}
    for entity_id, info in mapping.items():
        qname = info["qualified_name"]
        # Store both full qualified name and leaf name
        reverse_mapping[qname] = entity_id
        # Also store just the leaf (last part after .)
        leaf = qname.rsplit(".", 1)[-1]
        if leaf not in reverse_mapping:
            reverse_mapping[leaf] = entity_id

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Save main mapping
    output_path = OUTPUT_DIR / "entity_id_to_qualified_name.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
    print(f"Saved entity mapping to: {output_path}")
    print(f"  Total entities: {len(mapping)}")

    # Save reverse mapping for scoring
    reverse_path = OUTPUT_DIR / "qualified_name_to_entity_id.json"
    with open(reverse_path, "w", encoding="utf-8") as f:
        json.dump(reverse_mapping, f, indent=2)
    print(f"Saved reverse mapping to: {reverse_path}")
    print(f"  Total mappings: {len(reverse_mapping)}")

    # Show some examples
    print("\nSample mappings:")
    for i, (eid, info) in enumerate(list(mapping.items())[:5]):
        print(f"  {eid} -> {info['qualified_name']}")


if __name__ == "__main__":
    main()
