"""Comprehensibility floor testing harness.

Generates structured test packs with prompts and answer keys for evaluating
how well LLMs understand CodeIR compressed representations at each level.
Does NOT make LLM calls — produces JSON test packs for external scoring.

Capabilities tested:
  - Identification (graded 1-5): "What does this entity do?"
  - Differentiation (binary): "Which of these 3 entities handles X?"
  - Triage (binary): "Given this bug and 10 entities, which are involved?"
  - Reconstruction (graded 1-5): "Write pseudocode from this IR"
"""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from index.locator import extract_code_slice
from index.store.db import connect


CAPABILITIES = ("identification", "differentiation", "triage", "reconstruction")

IDENTIFICATION_RUBRIC = (
    "5: Fully correct — identifies purpose, behavior, and key logic. "
    "4: Mostly correct — identifies purpose, minor details missing. "
    "3: Partially correct — gets the general area right but misses specifics. "
    "2: Vaguely correct — understands it's code but wrong about purpose. "
    "1: Incorrect — fundamentally wrong about what the entity does."
)

RECONSTRUCTION_RUBRIC = (
    "5: Pseudocode captures all key operations, control flow, and data transformations. "
    "4: Pseudocode captures most operations, minor omissions. "
    "3: Pseudocode captures the general structure but misses important details. "
    "2: Pseudocode is only loosely related to the actual implementation. "
    "1: Pseudocode bears no meaningful resemblance to the original."
)


# ---------------------------------------------------------------------------
# Entity selection
# ---------------------------------------------------------------------------

def select_test_entities(
    repo_path: Path,
    count: int = 15,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """Select diverse test entities across complexity classes and module categories.

    Tries to pick entities from different complexity_class values and module
    categories to ensure broad coverage.
    """
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT e.id, e.qualified_name, e.kind, e.file_path, e.start_line, e.end_line, "
        "e.complexity_class, e.module_id "
        "FROM entities e ORDER BY e.id"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    entities = [dict(row) for row in rows]

    # Group by (complexity_class, category) for diverse selection
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for e in entities:
        key = f"{e.get('complexity_class', 'unknown')}|{e.get('module_id', 'unknown')}"
        buckets.setdefault(key, []).append(e)

    rng = random.Random(seed)
    selected: List[Dict[str, Any]] = []
    bucket_keys = list(buckets.keys())
    rng.shuffle(bucket_keys)

    # Round-robin from buckets until we have enough
    idx = 0
    while len(selected) < count and any(buckets.values()):
        key = bucket_keys[idx % len(bucket_keys)]
        if buckets.get(key):
            entity = rng.choice(buckets[key])
            buckets[key].remove(entity)
            selected.append(entity)
        idx += 1
        if idx > len(bucket_keys) * 100:
            break

    return selected[:count]


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _get_entity_ir(
    repo_path: Path, entity_id: str, level: str,
) -> Optional[str]:
    """Fetch the IR text for an entity at a specific level."""
    db_path = repo_path / ".codeir" / "entities.db"
    conn = connect(db_path)
    row = conn.execute(
        "SELECT ir_text FROM ir_rows WHERE entity_id = ? AND mode = ?",
        (entity_id, level),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _get_entity_source(repo_path: Path, entity: Dict[str, Any]) -> str:
    """Get the source code for an entity (used for answer keys)."""
    return extract_code_slice(
        repo_path=repo_path,
        file_path=str(entity["file_path"]),
        start_line=int(entity["start_line"]),
        end_line=int(entity["end_line"]),
    )


def _build_identification_prompt(ir_text: str, level: str) -> str:
    return (
        f"You are given the following CodeIR representation (level {level}) of a Python entity:\n\n"
        f"```\n{ir_text}\n```\n\n"
        "What does this entity do? Describe its purpose, behavior, and key operations."
    )


def _build_differentiation_prompt(
    ir_lines: List[str], target_description: str, level: str,
) -> str:
    numbered = "\n".join(f"{i+1}. `{line}`" for i, line in enumerate(ir_lines))
    return (
        f"You are given {len(ir_lines)} CodeIR representations (level {level}):\n\n"
        f"{numbered}\n\n"
        f"Which one handles: {target_description}?\n"
        "Answer with the number only."
    )


def _build_triage_prompt(
    bug_description: str, ir_lines: List[str], level: str,
) -> str:
    numbered = "\n".join(f"{i+1}. `{line}`" for i, line in enumerate(ir_lines))
    return (
        f"Bug report: {bug_description}\n\n"
        f"You are given {len(ir_lines)} CodeIR representations (level {level}):\n\n"
        f"{numbered}\n\n"
        "Which entities are most likely involved in this bug? List the numbers."
    )


def _build_reconstruction_prompt(ir_text: str, level: str) -> str:
    return (
        f"You are given the following CodeIR representation (level {level}) of a Python entity:\n\n"
        f"```\n{ir_text}\n```\n\n"
        "Write pseudocode that approximates the original implementation of this entity."
    )


# ---------------------------------------------------------------------------
# Test pack generation
# ---------------------------------------------------------------------------

def generate_test_pack(
    repo_path: Path,
    compression_level: str = "L1",
    entity_count: int = 15,
    seed: int = 42,
) -> Dict[str, Any]:
    """Generate a complete test pack at a given compression level.

    Returns a dict with test prompts, answer keys, and rubrics.
    Does NOT make LLM calls.
    """
    entities = select_test_entities(repo_path, count=entity_count, seed=seed)
    if not entities:
        return {
            "repo_path": str(repo_path),
            "level": compression_level,
            "entity_count": 0,
            "tests": [],
        }

    rng = random.Random(seed)
    tests: List[Dict[str, Any]] = []
    test_counter = 0

    for entity in entities:
        ir_text = _get_entity_ir(repo_path, str(entity["id"]), compression_level)
        if not ir_text:
            continue

        source = _get_entity_source(repo_path, entity)

        # Identification test
        test_counter += 1
        tests.append({
            "test_id": f"T{test_counter:03d}",
            "capability": "identification",
            "entity_id": str(entity["id"]),
            "prompt": _build_identification_prompt(ir_text, compression_level),
            "answer_key": {
                "expected_source": source,
                "entity_name": str(entity["qualified_name"]),
                "scoring": "graded",
                "max_score": 5,
            },
            "rubric": IDENTIFICATION_RUBRIC,
        })

        # Reconstruction test
        test_counter += 1
        tests.append({
            "test_id": f"T{test_counter:03d}",
            "capability": "reconstruction",
            "entity_id": str(entity["id"]),
            "prompt": _build_reconstruction_prompt(ir_text, compression_level),
            "answer_key": {
                "expected_source": source,
                "entity_name": str(entity["qualified_name"]),
                "scoring": "graded",
                "max_score": 5,
            },
            "rubric": RECONSTRUCTION_RUBRIC,
        })

    # Differentiation tests: groups of 3 entities
    diff_groups = [entities[i:i+3] for i in range(0, len(entities) - 2, 3)]
    for group in diff_groups:
        target = rng.choice(group)
        ir_lines = []
        for e in group:
            ir = _get_entity_ir(repo_path, str(e["id"]), compression_level)
            if ir:
                ir_lines.append(ir)
        if len(ir_lines) < 3:
            continue

        target_idx = group.index(target) + 1
        test_counter += 1
        tests.append({
            "test_id": f"T{test_counter:03d}",
            "capability": "differentiation",
            "entity_id": str(target["id"]),
            "prompt": _build_differentiation_prompt(
                ir_lines, str(target["qualified_name"]), compression_level,
            ),
            "answer_key": {
                "expected_answer": str(target_idx),
                "target_entity": str(target["qualified_name"]),
                "scoring": "binary",
                "max_score": 1,
            },
            "rubric": "1: Correct number selected. 0: Wrong number selected.",
        })

    # Triage tests: pick 10 entities, select 2 as "bug-related"
    if len(entities) >= 10:
        triage_pool = rng.sample(entities, 10)
        bug_entities = rng.sample(triage_pool, min(2, len(triage_pool)))
        ir_lines = []
        for e in triage_pool:
            ir = _get_entity_ir(repo_path, str(e["id"]), compression_level)
            if ir:
                ir_lines.append(ir)

        if len(ir_lines) >= 10:
            bug_names = [str(e["qualified_name"]) for e in bug_entities]
            bug_indices = [triage_pool.index(e) + 1 for e in bug_entities]
            test_counter += 1
            tests.append({
                "test_id": f"T{test_counter:03d}",
                "capability": "triage",
                "entity_id": ",".join(str(e["id"]) for e in bug_entities),
                "prompt": _build_triage_prompt(
                    f"Error occurs in {bug_names[0]} when processing input",
                    ir_lines,
                    compression_level,
                ),
                "answer_key": {
                    "expected_indices": bug_indices,
                    "bug_entities": bug_names,
                    "scoring": "binary",
                    "max_score": 1,
                },
                "rubric": "1: At least one correct entity identified. 0: No correct entities identified.",
            })

    return {
        "repo_path": str(repo_path),
        "level": compression_level,
        "entity_count": len(entities),
        "tests": tests,
    }


def generate_all_level_packs(
    repo_path: Path,
    levels: tuple[str, ...] = ("L0", "L1", "L2", "L3"),
    entity_count: int = 15,
    seed: int = 42,
) -> Dict[str, Dict[str, Any]]:
    """Generate test packs for all specified levels."""
    return {
        level: generate_test_pack(repo_path, level, entity_count, seed)
        for level in levels
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_test_results(
    test_pack: Dict[str, Any],
    scored_responses: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Score LLM responses against answer keys.

    Args:
        test_pack: The original test pack from generate_test_pack.
        scored_responses: List of {"test_id": str, "score": float}.

    Returns:
        Per-capability aggregation with mean scores and pass rates.
    """
    score_map = {r["test_id"]: float(r["score"]) for r in scored_responses}

    by_capability: Dict[str, List[float]] = {}
    for test in test_pack.get("tests", []):
        cap = test["capability"]
        tid = test["test_id"]
        if tid in score_map:
            by_capability.setdefault(cap, []).append(score_map[tid])

    result: Dict[str, Any] = {}
    for cap in CAPABILITIES:
        scores = by_capability.get(cap, [])
        if scores:
            max_score = 5.0 if cap in ("identification", "reconstruction") else 1.0
            result[cap] = {
                "mean_score": sum(scores) / len(scores),
                "count": len(scores),
                "pass_rate": sum(1 for s in scores if s >= max_score * 0.6) / len(scores),
            }
        else:
            result[cap] = {"mean_score": 0.0, "count": 0, "pass_rate": 0.0}

    all_scores = [s for cap_scores in by_capability.values() for s in cap_scores]
    result["overall"] = {
        "total_tests": len(all_scores),
        "mean_score": (sum(all_scores) / len(all_scores)) if all_scores else 0.0,
    }

    return result
