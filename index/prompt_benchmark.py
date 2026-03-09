"""Prompt benchmark pack generation for compression-mode LLM evaluation."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from index.indexer import index_repo, map_legacy_mode_to_level
from index.store.db import connect


def _load_labels(labels_path: Path) -> List[Dict[str, Any]]:
    with labels_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("labels file must contain a JSON list")
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query", "")).strip()
        expected = item.get("expected_entity_ids", [])
        if not query or not isinstance(expected, list):
            continue
        out.append({"query": query, "expected_entity_ids": [str(x) for x in expected]})
    if not out:
        raise ValueError("labels file did not contain any valid entries")
    return out


def _load_rows(repo_path: Path, level: Optional[str] = None) -> List[Dict[str, Any]]:
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    if level:
        rows = conn.execute(
            """
            SELECT
              e.id AS entity_id,
              e.qualified_name,
              e.kind,
              r.ir_text
            FROM entities AS e
            JOIN ir_rows AS r ON r.entity_id = e.id
            WHERE r.mode = ?
            """,
            (level,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
              e.id AS entity_id,
              e.qualified_name,
              e.kind,
              r.ir_text
            FROM entities AS e
            JOIN ir_rows AS r ON r.entity_id = e.id
            """
        ).fetchall()
    conn.close()
    return [
        {
            "entity_id": str(row["entity_id"]),
            "qualified_name": str(row["qualified_name"]),
            "kind": str(row["kind"]),
            "ir_text": str(row["ir_text"]),
        }
        for row in rows
    ]


def _score_query(row: Dict[str, Any], query_terms: List[str]) -> int:
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


def _rank_rows(rows: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(t) >= 2]
    scored = [(_score_query(row, terms), row) for row in rows]
    scored.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
    return [row for _, row in scored]


def _build_prompt(mode: str, query: str, ranked_rows: List[Dict[str, Any]]) -> str:
    header = [
        "You are triaging a bug against compressed code entities.",
        f"Compression mode: {mode}",
        "",
        "Task: Pick exactly 3 entity IDs that are most likely relevant to the query.",
        "Return strict JSON only: {\"selected_entity_ids\": [\"ID1\", \"ID2\", \"ID3\"], \"rationale\": \"...\"}",
        "",
        f"Query: {query}",
        "",
        "Candidates:",
    ]
    for i, row in enumerate(ranked_rows, start=1):
        header.append(
            f"{i:02d}. {row['entity_id']} | {row['qualified_name']} | {row['kind']} | {row['ir_text']}"
        )
    return "\n".join(header)


def generate_prompt_benchmark_pack(
    repo_path: Path,
    base_config: Dict[str, Any],
    labels_path: Path,
    output_dir: Path,
    modes: Iterable[str],
    top_k: int = 50,
    max_cases: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate prompt benchmark files for each compression mode."""
    labels = _load_labels(labels_path)
    if max_cases is not None and max_cases > 0:
        labels = labels[:max_cases]

    parsed_modes: List[str] = []
    for mode in modes:
        m = str(mode).strip().lower()
        if m in {"a", "b", "hybrid"} and m not in parsed_modes:
            parsed_modes.append(m)
    if not parsed_modes:
        parsed_modes = ["a", "b", "hybrid"]

    output_dir.mkdir(parents=True, exist_ok=True)
    case_key: List[Dict[str, Any]] = []
    mode_files: Dict[str, str] = {}

    for mode in parsed_modes:
        level = map_legacy_mode_to_level(mode)
        cfg = dict(base_config)
        cfg["compression_mode"] = mode
        cfg["compression_level"] = level
        index_repo(repo_path=repo_path, config=cfg)
        rows = _load_rows(repo_path, level=level)

        lines: List[str] = []
        for idx, item in enumerate(labels, start=1):
            ranked = _rank_rows(rows, item["query"])[: max(1, top_k)]
            case_id = f"C{idx:03d}"
            prompt = _build_prompt(mode=mode, query=item["query"], ranked_rows=ranked)
            payload = {
                "case_id": case_id,
                "mode": mode,
                "compression_level": level,
                "query": item["query"],
                "expected_entity_ids": item["expected_entity_ids"],
                "candidate_entity_ids": [r["entity_id"] for r in ranked],
                "prompt": prompt,
            }
            lines.append(json.dumps(payload, ensure_ascii=True))

            if mode == parsed_modes[0]:
                case_key.append(
                    {
                        "case_id": case_id,
                        "query": item["query"],
                        "expected_entity_ids": item["expected_entity_ids"],
                    }
                )

        mode_path = output_dir / f"prompt_benchmark_{mode}.jsonl"
        mode_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        mode_files[mode] = str(mode_path)

    key_path = output_dir / "prompt_benchmark_answer_key.json"
    key_path.write_text(json.dumps(case_key, indent=2), encoding="utf-8")

    readme_lines = [
        "# Prompt Benchmark Pack",
        "",
        "## Goal",
        "Measure how accurately an LLM can pick likely relevant entity IDs from compressed IR candidates.",
        "",
        "## Pack Directory",
        f"`{output_dir}`",
        "",
        "## Files",
    ]
    for mode in parsed_modes:
        readme_lines.append(f"- `{Path(mode_files[mode]).name}`: prompts for mode `{mode}`")
    readme_lines.extend(
        [
            f"- `{key_path.name}`: expected IDs per case",
            "",
            "## Exact Input To The Model",
            "1. Read one JSON object per line from exactly one mode file (for example `prompt_benchmark_a.jsonl`).",
            "2. Send only the value of `prompt` as the user message to the model.",
            "3. Do not add extra candidate IDs or metadata; candidates are already embedded in the prompt text.",
            "",
            "## Required Model Output",
            "Return strict JSON only:",
            "```json",
            "{\"selected_entity_ids\": [\"ID1\", \"ID2\", \"ID3\"], \"rationale\": \"...\"}",
            "```",
            "Constraints:",
            "1. Exactly 3 IDs.",
            "2. IDs must be chosen from `candidate_entity_ids` of that JSONL row.",
            "3. IDs are case-sensitive and must match exactly.",
            "",
            "## Predictions File Format",
            "Create one JSONL file with one row per prompt:",
            "```json",
            "{\"case_id\": \"C001\", \"mode\": \"a\", \"selected_entity_ids\": [\"XXXX\", \"YYYY\", \"ZZZZ\"]}",
            "```",
            "Optional keys: `rationale`, `raw_response`, `latency_ms`.",
            "",
            "## Minimal Runner Checklist",
            "1. Iterate each row from one mode file.",
            "2. Submit `row['prompt']` to the model.",
            "3. Parse model JSON output and extract `selected_entity_ids`.",
            "4. Write predictions JSONL rows containing `case_id`, `mode`, and `selected_entity_ids`.",
            "",
            "## Evaluate Results",
            "Run from the SemanticIR repo root (where `index/` is importable):",
            "```bash",
            "python3 - <<'PY'",
            "from pathlib import Path",
            "from index.prompt_scoring import score_llm_predictions, render_llm_scoring_markdown",
            "",
            f"pack = Path(r\"{output_dir}\")",
            "answer_key = pack / \"prompt_benchmark_answer_key.json\"",
            "predictions = pack / \"predictions_<model>.jsonl\"",
            "report = score_llm_predictions(answer_key, predictions)",
            "md = render_llm_scoring_markdown(report)",
            "out = pack / \"llm_scoring_<model>.md\"",
            "out.write_text(md, encoding=\"utf-8\")",
            "print(md)",
            "print(f\"\\nWrote: {out}\")",
            "PY",
            "```",
            "",
            "## Metrics",
            "- `top3_accuracy`: fraction of cases where at least one expected ID appears in top 3.",
            "- `precision_at_3`: average fraction of selected IDs that are correct.",
            "- `recall_at_3`: average fraction of expected IDs recovered.",
            "- `avg_false_positives_top3`: average number of wrong IDs in top 3 (lower is better).",
            "- `missing_cases`: answer-key cases without a prediction row for that mode (lower is better).",
            "",
            "## Common Failure Modes",
            "1. Model returns non-JSON output.",
            "2. Returned IDs are not present in the row's candidate list.",
            "3. Wrong `mode` value in predictions rows.",
            "4. Missing rows for some `case_id` values.",
        ]
    )
    readme = "\n".join(readme_lines)
    readme_path = output_dir / "prompt_benchmark_README.md"
    readme_path.write_text(readme, encoding="utf-8")

    manifest = {
        "repo_path": str(repo_path),
        "labels_path": str(labels_path),
        "modes": parsed_modes,
        "case_count": len(case_key),
        "top_k": int(top_k),
        "files": {
            **mode_files,
            "answer_key": str(key_path),
            "readme": str(readme_path),
        },
    }
    manifest_path = output_dir / "prompt_benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest
