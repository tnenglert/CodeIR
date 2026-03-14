"""Bug-injection benchmark generation for compression-mode triage tests."""

from __future__ import annotations

import json
import random
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from index.indexer import index_repo, map_legacy_mode_to_level
from tests.eval.ranking import rank_by_query
from index.store.db import connect
from index.store.fetch import load_entity_ir_rows


def _load_entities(repo_path: Path) -> List[Dict[str, Any]]:
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, kind, qualified_name, file_path, start_line, end_line
        FROM entities
        WHERE kind IN ('function','async_function','method','async_method')
        ORDER BY id
        """
    ).fetchall()
    conn.close()
    return [
        {
            "id": str(r["id"]),
            "kind": str(r["kind"]),
            "qualified_name": str(r["qualified_name"]),
            "file_path": str(r["file_path"]),
            "start_line": int(r["start_line"]),
            "end_line": int(r["end_line"]),
        }
        for r in rows
    ]


def _inject_bug(repo_path: Path, entity: Dict[str, Any]) -> Tuple[str, str]:
    rel = entity["file_path"]
    path = repo_path / rel
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    s = max(1, entity["start_line"])
    e = max(s, entity["end_line"])
    seg = lines[s - 1 : e]

    bug_kind = "return_none"
    changed = False
    for i, line in enumerate(seg):
        stripped = line.strip()
        if stripped.startswith("return ") and not stripped.startswith("return None"):
            indent = line[: len(line) - len(line.lstrip())]
            seg[i] = f"{indent}return None  # BUGBENCH\n"
            changed = True
            break

    if not changed:
        bug_kind = "forced_exception"
        # Insert a deterministic failure near top of entity body.
        target_idx = 1 if len(seg) > 1 else 0
        base_line = seg[target_idx] if seg else "    pass\n"
        indent = base_line[: len(base_line) - len(base_line.lstrip())]
        seg.insert(target_idx, f"{indent}raise RuntimeError('BUGBENCH')\n")
        changed = True

    lines[s - 1 : e] = seg
    path.write_text("".join(lines), encoding="utf-8")

    qleaf = entity["qualified_name"].rsplit(".", 1)[-1].replace("_", " ")
    if bug_kind == "return_none":
        query = f"{qleaf} now returns None unexpectedly and breaks downstream logic"
    else:
        query = f"{qleaf} now raises runtime error unexpectedly during execution"
    return bug_kind, query


def _build_prompt(mode: str, case_id: str, query: str, candidates: List[Dict[str, Any]]) -> str:
    lines = [
        "You are triaging a bug in a compressed codebase.",
        f"Compression mode: {mode}",
        f"Case ID: {case_id}",
        "",
        "Task: Pick exactly 3 entity IDs most likely causing the bug.",
        "Return strict JSON only: {\"selected_entity_ids\": [\"ID1\",\"ID2\",\"ID3\"], \"rationale\": \"...\"}",
        "",
        f"Bug symptom: {query}",
        "",
        "Candidates:",
    ]
    for i, row in enumerate(candidates, start=1):
        lines.append(f"{i:02d}. {row['entity_id']} | {row['qualified_name']} | {row['kind']} | {row['ir_text']}")
    return "\n".join(lines)


def generate_bug_benchmark_pack(
    source_repo_path: Path,
    output_dir: Path,
    base_config: Dict[str, Any],
    modes: Iterable[str],
    case_count: int = 20,
    top_k: int = 50,
    seed: int = 7,
) -> Dict[str, Any]:
    """Create bug-injected benchmark cases and prompt packs.

    For each case:
    - clone source repo
    - inject one deterministic bug into one entity
    - index per mode
    - create triage prompt and answer key
    """
    if not source_repo_path.exists() or not source_repo_path.is_dir():
        raise FileNotFoundError(f"source repo not found: {source_repo_path}")

    parsed_modes: List[str] = []
    for m in modes:
        x = str(m).strip().lower()
        if x in {"a", "b", "hybrid"} and x not in parsed_modes:
            parsed_modes.append(x)
    if not parsed_modes:
        parsed_modes = ["a", "b", "hybrid"]

    output_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    # Build clean index once to pick candidate entities.
    clean_cfg = dict(base_config)
    clean_cfg["compression_mode"] = "hybrid"
    clean_cfg["compression_level"] = map_legacy_mode_to_level("hybrid")
    index_repo(repo_path=source_repo_path, config=clean_cfg)
    entities = _load_entities(source_repo_path)
    if not entities:
        raise ValueError("no function/method entities found to inject bugs into")

    rng = random.Random(seed)
    picks = entities.copy()
    rng.shuffle(picks)
    picks = picks[: min(case_count, len(picks))]

    mode_lines: Dict[str, List[str]] = {m: [] for m in parsed_modes}
    answer_key: List[Dict[str, Any]] = []

    for idx, target in enumerate(picks, start=1):
        case_id = f"C{idx:03d}"
        case_repo = cases_dir / case_id / "repo"
        case_parent = case_repo.parent
        if case_parent.exists():
            shutil.rmtree(case_parent)
        shutil.copytree(source_repo_path, case_repo, ignore=shutil.ignore_patterns(".codeir", "__pycache__", ".git"))

        bug_kind, query = _inject_bug(case_repo, target)

        expected_id = target["id"]
        answer_key.append(
            {
                "case_id": case_id,
                "query": query,
                "expected_entity_ids": [expected_id],
                "bug_kind": bug_kind,
                "target_qualified_name": target["qualified_name"],
                "case_repo_path": str(case_repo),
            }
        )

        for mode in parsed_modes:
            level = map_legacy_mode_to_level(mode)
            cfg = dict(base_config)
            cfg["compression_mode"] = mode
            cfg["compression_level"] = level
            index_repo(repo_path=case_repo, config=cfg)
            rows = load_entity_ir_rows(case_repo, level=level)
            candidates = rank_by_query(rows, query, top_k=top_k)
            prompt = _build_prompt(mode=mode, case_id=case_id, query=query, candidates=candidates)
            payload = {
                "case_id": case_id,
                "mode": mode,
                "compression_level": level,
                "query": query,
                "expected_entity_ids": [expected_id],
                "bug_kind": bug_kind,
                "target_qualified_name": target["qualified_name"],
                "case_repo_path": str(case_repo),
                "candidate_entity_ids": [c["entity_id"] for c in candidates],
                "prompt": prompt,
            }
            mode_lines[mode].append(json.dumps(payload, ensure_ascii=True))

    files: Dict[str, str] = {}
    for mode in parsed_modes:
        p = output_dir / f"bug_prompt_benchmark_{mode}.jsonl"
        p.write_text("\n".join(mode_lines[mode]) + ("\n" if mode_lines[mode] else ""), encoding="utf-8")
        files[mode] = str(p)

    key_path = output_dir / "bug_prompt_benchmark_answer_key.json"
    key_path.write_text(json.dumps(answer_key, indent=2), encoding="utf-8")

    readme = output_dir / "bug_prompt_benchmark_README.md"
    readme.write_text(
        "\n".join(
            [
                "# Bug Prompt Benchmark Pack",
                "",
                "Each case is a cloned repo with one injected bug.",
                "Use `bug_prompt_benchmark_<mode>.jsonl` prompts with an LLM and score with `eval-llm-results`.",
                "",
                "Files:",
                *[f"- `{Path(files[m]).name}`" for m in parsed_modes],
                f"- `{key_path.name}`",
            ]
        ),
        encoding="utf-8",
    )

    manifest = {
        "source_repo_path": str(source_repo_path),
        "output_dir": str(output_dir),
        "modes": parsed_modes,
        "case_count": len(answer_key),
        "top_k": int(top_k),
        "seed": int(seed),
        "files": {
            **files,
            "answer_key": str(key_path),
            "readme": str(readme),
        },
    }
    manifest_path = output_dir / "bug_prompt_benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest
