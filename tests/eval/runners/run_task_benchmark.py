"""Unified task benchmark runner with credible baselines and calibrated risk metrics.

Conditions:
- raw_baseline: internal search + raw source candidates
- naive_rag_bm25: BM25 over source-only retrieval corpus
- naive_rag_embed: local MiniLM embedding retrieval over source-only corpus
- semanticir_flow_v2: filtered-L3 orientation (module select -> entity select) + L1 reasoning
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from eval.entity_family import entity_family_base
from eval.entity_family import expand_entity_family_candidates
from eval.providers import (
    AnthropicProvider,
    create_provider,
    select_provider_interactive,
    PROVIDERS,
)
from eval.retrieval.bm25_retriever import BM25Retriever
from eval.retrieval.build_raw_retrieval_corpus import build_raw_retrieval_corpus
from eval.retrieval.embedding_retriever import MiniLMRetriever
from index.locator import extract_code_slice
from index.search import search_entities
from index.store.db import connect
from index.store.fetch import get_entity_location, get_entity_with_ir
from ir.stable_ids import make_module_base_id
from ir.token_count import count_tokens


CONDITIONS = (
    "raw_baseline",
    "naive_rag_bm25",
    "naive_rag_embed",
    "semanticir_flow_v2",
)
FCR_THRESHOLDS = (3, 4, 5)

# Prefixes that LLMs sometimes incorrectly include in IDs
_ID_PREFIXES_TO_STRIP = ("MD ", "MD_", "MODULE ", "MODULE_", "FN ", "FN_", "CLS ", "CLS_", "AMT ", "AMT_", "AFN ", "AFN_", "MT ", "MT_")


def _normalize_id_match(raw_id: str, valid_ids: set[str]) -> Tuple[str, bool]:
    """Normalize an ID and match against valid IDs with prefix stripping.

    Returns (matched_id, was_fuzzy) where was_fuzzy=True if prefix stripping was needed.
    Returns ("", False) if no match found.
    """
    raw_id = str(raw_id).strip()
    if not raw_id:
        return "", False

    # Try exact match first (preserves current behavior for well-behaved models)
    if raw_id in valid_ids:
        return raw_id, False

    # Try stripping common prefixes (case-insensitive check, preserve case of suffix)
    raw_upper = raw_id.upper()
    for prefix in _ID_PREFIXES_TO_STRIP:
        if raw_upper.startswith(prefix.upper()):
            suffix = raw_id[len(prefix):].strip()
            if suffix in valid_ids:
                return suffix, True
            # Also try the suffix uppercased
            if suffix.upper() in valid_ids:
                return suffix.upper(), True

    # No match found
    return "", False


def _load_task_pack(task_pack_path: Path) -> Dict[str, Any]:
    data = json.loads(task_pack_path.read_text(encoding="utf-8"))
    tasks = data.get("tasks", [])
    if not tasks:
        raise ValueError(f"task pack has no tasks: {task_pack_path}")
    return data


def _phase_from_task_count(task_count: int) -> Tuple[str, str]:
    if task_count <= 8:
        return (
            "A",
            "Harness validation only. This sample size does not support product efficacy conclusions.",
        )
    if task_count <= 24:
        return (
            "B",
            "Preliminary efficacy signal only. Treat conclusions as directional.",
        )
    return (
        "C",
        "Decision-quality evidence tier.",
    )


def _ensure_provider_credentials(provider_name: str) -> None:
    """Ensure provider credentials exist; prompt interactively when possible."""
    env_vars = {
        "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
        "openai": ["OPENAI_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY"],
    }

    required_vars = env_vars.get(provider_name, [])
    if not required_vars:
        return  # Unknown provider, skip check

    has_creds = any(bool(os.environ.get(var)) for var in required_vars)
    if has_creds:
        return

    primary_var = required_vars[0]
    if sys.stdin.isatty():
        key = getpass.getpass(f"{primary_var} is not set. Enter key: ").strip()
        if key:
            os.environ[primary_var] = key
            return

    raise RuntimeError(
        f"Missing {provider_name} credentials. Set {primary_var} and re-run."
    )


def _fetch_raw_source(repo_path: Path, entity_id: str) -> str:
    loc = get_entity_location(repo_path=repo_path, entity_id=entity_id)
    if not loc:
        return ""
    return extract_code_slice(
        repo_path=repo_path,
        file_path=str(loc["file_path"]),
        start_line=int(loc["start_line"]),
        end_line=int(loc["end_line"]),
    )


def _fetch_l1_token(repo_path: Path, entity_id: str) -> str:
    row = get_entity_with_ir(repo_path=repo_path, entity_id=entity_id, mode="L1")
    if not row:
        return ""
    return str(row.get("ir_text", ""))


def _fetch_bearings_markdown(repo_path: Path) -> str:
    bearings_path = repo_path / "bearings.md"
    if not bearings_path.exists():
        return ""
    return bearings_path.read_text(encoding="utf-8").strip()


def _fetch_l3_index(
    repo_path: Path,
    entity_id_filter: Optional[Sequence[str]] = None,
) -> str:
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        return ""
    conn = connect(db_path)
    if entity_id_filter:
        keep: List[str] = []
        seen: set[str] = set()
        for raw in entity_id_filter:
            eid = str(raw).strip()
            if not eid or eid in seen:
                continue
            keep.append(eid)
            seen.add(eid)
        if not keep:
            conn.close()
            return ""
        placeholders = ",".join("?" for _ in keep)
        l3_rows = conn.execute(
            f"SELECT entity_id, ir_text FROM ir_rows WHERE mode='L3' AND entity_id IN ({placeholders}) ORDER BY entity_id",
            keep,
        ).fetchall()
    else:
        l3_rows = conn.execute(
            "SELECT entity_id, ir_text FROM ir_rows WHERE mode='L3' ORDER BY entity_id"
        ).fetchall()
    conn.close()
    return "\n".join(str(r[1]) for r in l3_rows)


def _build_orientation_selection_context(
    repo_path: Path,
    entity_id_filter: Optional[Sequence[str]] = None,
    include_bearings: bool = True,
) -> str:
    bearings = _fetch_bearings_markdown(repo_path) if include_bearings else ""
    l3_index = _fetch_l3_index(repo_path, entity_id_filter=entity_id_filter)

    sections: List[str] = []
    if bearings:
        sections.append(f"Bearings:\n{bearings}")
    if l3_index:
        sections.append(f"L3 index:\n{l3_index}")
    return "\n\n".join(sections)


def _load_index_entity_ids(repo_path: Path) -> set[str]:
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        return set()
    conn = connect(db_path)
    rows = conn.execute("SELECT id FROM entities").fetchall()
    conn.close()
    return {str(r[0]) for r in rows}


def _build_module_id_file_path_maps(repo_path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Build deterministic module-id mappings that match bearings generation."""
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        return {}, {}
    conn = connect(db_path)
    rows = conn.execute("SELECT file_path FROM modules ORDER BY file_path").fetchall()
    conn.close()

    file_paths = [str(r[0]) for r in rows]
    by_base: Dict[str, List[str]] = {}
    for fp in file_paths:
        base = make_module_base_id(fp)
        by_base.setdefault(base, []).append(fp)

    module_id_to_file_path: Dict[str, str] = {}
    file_path_to_module_id: Dict[str, str] = {}
    for base, paths in by_base.items():
        paths.sort()
        for idx, fp in enumerate(paths, start=1):
            module_id = base if idx == 1 else f"{base}_{idx:02d}"
            module_id_to_file_path[module_id] = fp
            file_path_to_module_id[fp] = module_id
    return module_id_to_file_path, file_path_to_module_id


def _expand_modules_to_entity_ids(
    *,
    repo_path: Path,
    module_file_paths: Sequence[str],
    max_module_entity_candidates: int,
) -> Tuple[List[str], Dict[str, int]]:
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        return [], {}

    keep_modules: List[str] = []
    seen_modules: set[str] = set()
    for raw in module_file_paths:
        fp = str(raw).strip()
        if not fp or fp in seen_modules:
            continue
        keep_modules.append(fp)
        seen_modules.add(fp)
    if not keep_modules:
        return [], {}

    conn = connect(db_path)
    placeholders = ",".join("?" for _ in keep_modules)
    rows = conn.execute(
        f"SELECT id, module_id FROM entities WHERE module_id IN ({placeholders}) ORDER BY module_id, id",
        keep_modules,
    ).fetchall()
    conn.close()

    cap = max(1, int(max_module_entity_candidates))
    entity_ids: List[str] = []
    entities_per_module: Dict[str, int] = {}
    for row in rows:
        if len(entity_ids) >= cap:
            break
        eid = str(row[0]).strip()
        module_id = str(row[1]).strip()
        if not eid:
            continue
        entity_ids.append(eid)
        entities_per_module[module_id] = int(entities_per_module.get(module_id, 0)) + 1
    return entity_ids, entities_per_module


def _build_entity_family_index(repo_path: Path) -> Dict[str, List[str]]:
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        return {}
    conn = connect(db_path)
    rows = conn.execute("SELECT id FROM entities ORDER BY id").fetchall()
    conn.close()

    families: Dict[str, List[str]] = {}
    for row in rows:
        eid = str(row[0])
        base = entity_family_base(eid)
        families.setdefault(base, []).append(eid)
    return families

def _retrieval_diagnostic(
    *,
    truth: set[str],
    candidate_ids: Sequence[str],
    index_entity_ids: set[str],
) -> Dict[str, Any]:
    gt = sorted(str(x) for x in truth)
    in_index = [e for e in gt if e in index_entity_ids]
    missing_from_index = [e for e in gt if e not in index_entity_ids]
    candidate_set = {str(x) for x in candidate_ids}
    in_candidates = [e for e in gt if e in candidate_set]
    gt_count = len(gt)
    candidate_recall = (len(in_candidates) / gt_count) if gt_count else 0.0
    return {
        "ground_truth_count": gt_count,
        "ground_truth_in_index_count": len(in_index),
        "ground_truth_missing_from_index": missing_from_index,
        "ground_truth_in_candidate_count": len(in_candidates),
        "ground_truth_in_candidate_ids": in_candidates,
        "candidate_has_any_ground_truth": bool(in_candidates),
        "candidate_recall_at_k": candidate_recall,
    }


def _retrieve_internal(
    repo_path: Path,
    query: str,
    top_k: int,
    level: str,
) -> List[Dict[str, Any]]:
    hits = search_entities(query=query, repo_path=repo_path, limit=int(top_k))
    out: List[Dict[str, Any]] = []

    for hit in hits:
        eid = str(hit["entity_id"])
        if level == "L1":
            rep = _fetch_l1_token(repo_path, eid)
        else:
            rep = _fetch_raw_source(repo_path, eid)
        if not rep:
            continue

        out.append(
            {
                "entity_id": eid,
                "qualified_name": str(hit.get("qualified_name", "")),
                "kind": str(hit.get("kind", "")),
                "file_path": str(hit.get("file_path", "")),
                "representation": rep,
            }
        )
    return out


def _retrieve_bm25(
    retriever: BM25Retriever,
    query: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    rows = retriever.search(query, top_k=top_k)
    return [
        {
            "entity_id": str(r["entity_id"]),
            "qualified_name": str(r.get("qualified_name", "")),
            "kind": str(r.get("kind", "")),
            "file_path": str(r.get("file_path", "")),
            "representation": str(r.get("source", "")),
            "retrieval_score": float(r.get("score", 0.0)),
        }
        for r in rows
    ]


def _retrieve_embed(
    retriever: MiniLMRetriever,
    query: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    rows = retriever.search(query, top_k=top_k)
    return [
        {
            "entity_id": str(r["entity_id"]),
            "qualified_name": str(r.get("qualified_name", "")),
            "kind": str(r.get("kind", "")),
            "file_path": str(r.get("file_path", "")),
            "representation": str(r.get("source", "")),
            "retrieval_score": float(r.get("score", 0.0)),
        }
        for r in rows
    ]


def _lift_bm25_to_l1_candidates(
    *,
    repo_path: Path,
    retriever: BM25Retriever,
    query: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    bm_rows = _retrieve_bm25(retriever=retriever, query=query, top_k=top_k)
    lifted: List[Dict[str, Any]] = []
    for row in bm_rows:
        eid = str(row["entity_id"])
        l1 = _fetch_l1_token(repo_path, eid)
        if not l1:
            continue
        lifted.append(
            {
                "entity_id": eid,
                "qualified_name": str(row.get("qualified_name", "")),
                "kind": str(row.get("kind", "")),
                "file_path": str(row.get("file_path", "")),
                "representation": l1,
                "retrieval_score": float(row.get("retrieval_score", 0.0)),
            }
        )
    return lifted


def _hydrate_candidates_from_entity_ids(
    *,
    repo_path: Path,
    entity_ids: Sequence[str],
    level: str,
) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for raw_eid in entity_ids:
        eid = str(raw_eid).strip()
        if not eid or eid in seen:
            continue
        loc = get_entity_location(repo_path=repo_path, entity_id=eid)
        if not loc:
            continue
        rep = _fetch_l1_token(repo_path, eid) if level == "L1" else _fetch_raw_source(repo_path, eid)
        if not rep:
            continue
        out.append(
            {
                "entity_id": eid,
                "qualified_name": str(loc.get("qualified_name", "")),
                "kind": str(loc.get("kind", "")),
                "file_path": str(loc.get("file_path", "")),
                "representation": rep,
            }
        )
        seen.add(eid)
    return out


def _candidate_block(candidates: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for idx, item in enumerate(candidates, start=1):
        parts.append(
            f"[{idx}] ID={item['entity_id']} NAME={item.get('qualified_name', '-') } "
            f"KIND={item.get('kind', '-')} FILE={item.get('file_path', '-')}\n"
            f"{item['representation']}"
        )
    return "Candidates:\n" + "\n\n".join(parts)


def _build_localization_prompt(
    condition: str,
    task_query: str,
    candidates: Sequence[Dict[str, Any]],
    orientation_segment: str = "",
    expansion_segment: str = "",
) -> Tuple[str, Dict[str, int]]:
    instruction = (
        "You are localizing likely-relevant code entities for a developer task.\n"
        "Select up to 3 entity IDs from the candidate list.\n"
        "If current context is insufficient, set needs_expansion to yes.\n"
        "Return JSON only with exact keys: ranked_entity_ids, confidence, needs_expansion.\n"
        "- ranked_entity_ids: array of up to 3 entity ID values (e.g., [\"GTRGSTRRTR\", \"ATHNTCTR\"]), NOT list indices\n"
        "- confidence: integer 1-5\n"
        "- needs_expansion: \"yes\" or \"no\"\n"
    )
    task_seg = f"Condition: {condition}\nTask: {task_query}\n"
    candidate_seg = _candidate_block(candidates)
    footer = "\nReturn JSON only."

    orientation = (
        f"\nOrientation context:\n{orientation_segment}\n"
        if orientation_segment
        else ""
    )
    expansion = (
        f"\nAdditional raw expansion context:\n{expansion_segment}\n"
        if expansion_segment
        else ""
    )

    prompt = instruction + "\n" + task_seg + orientation + "\n" + candidate_seg + expansion + footer

    orientation_tokens = count_tokens(orientation) if orientation else 0
    retrieval_tokens = count_tokens(candidate_seg)
    expansion_tokens = count_tokens(expansion) if expansion else 0
    prompt_tokens = count_tokens(prompt)
    reasoning_tokens = max(prompt_tokens - orientation_tokens - retrieval_tokens - expansion_tokens, 0)

    buckets = {
        "orientation_tokens": int(orientation_tokens),
        "retrieval_tokens": int(retrieval_tokens),
        "expansion_tokens": int(expansion_tokens),
        "reasoning_tokens": int(reasoning_tokens),
    }
    return prompt, buckets


def _build_orientation_selection_prompt(
    *,
    task_query: str,
    orientation_context: str,
    top_k: int,
) -> Tuple[str, int]:
    instruction = (
        "You are selecting retrieval candidates for code localization.\n"
        "Do NOT answer the task.\n"
        f"Return JSON only: {{\"candidate_entity_ids\": [\"ENTITY_ID\", ...]}} with up to {int(top_k)} entity ID values.\n"
        "Use the actual ID values from orientation context (e.g., GTRGSTRRTR, ATHNTCTR), NOT list indices.\n"
        "Prioritize entities most likely relevant to the task."
    )
    prompt = (
        f"{instruction}\n\n"
        f"Task:\n{task_query}\n\n"
        f"Orientation context:\n{orientation_context}\n\n"
        "Return JSON only."
    )
    return prompt, int(count_tokens(prompt))


def _build_module_selection_prompt(
    *,
    task_query: str,
    bearings_context: str,
    max_module_selections: int,
) -> Tuple[str, int]:
    instruction = (
        "You are selecting modules that are most likely to contain relevant code entities.\n"
        "Do NOT answer the task.\n"
        f"Return JSON only: {{\"module_ids\": [\"MD_...\"]}} with 1-{int(max_module_selections)} module IDs.\n"
        "Only include module IDs that appear in bearings context.\n"
        "Select the minimum focused set of modules."
    )
    prompt = (
        f"{instruction}\n\n"
        f"Task:\n{task_query}\n\n"
        f"Bearings context:\n{bearings_context}\n\n"
        "Return JSON only."
    )
    return prompt, int(count_tokens(prompt))


def _extract_json(text: str) -> Dict[str, Any]:
    raw = text.strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    match = re.search(r"\{.*\}", raw, flags=re.S)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {}


def _parse_orientation_candidates(
    response_text: str,
    *,
    valid_entity_ids: set[str],
    top_k: int,
) -> List[str]:
    payload = _extract_json(response_text)
    raw_ids: Any = payload.get("candidate_entity_ids")
    if raw_ids is None:
        raw_ids = payload.get("ranked_entity_ids", payload.get("entity_ids", []))
    if isinstance(raw_ids, str):
        raw_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
    if not isinstance(raw_ids, list):
        raw_ids = []

    out: List[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        raw_eid = str(item).strip()
        if not raw_eid:
            continue
        matched_eid, was_fuzzy = _normalize_id_match(raw_eid, valid_entity_ids)
        if not matched_eid or matched_eid in seen:
            continue
        if was_fuzzy:
            print(f"  [fuzzy entity match] '{raw_eid}' -> '{matched_eid}'")
        out.append(matched_eid)
        seen.add(matched_eid)
        if len(out) >= int(top_k):
            break
    return out


def _parse_module_selection(
    response_text: str,
    *,
    valid_module_ids: set[str],
    max_module_selections: int,
) -> List[str]:
    payload = _extract_json(response_text)
    raw_ids: Any = payload.get("module_ids", payload.get("modules", []))
    if isinstance(raw_ids, str):
        raw_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
    if not isinstance(raw_ids, list):
        raw_ids = []

    out: List[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        raw_mid = str(item).strip()
        if not raw_mid:
            continue
        matched_mid, was_fuzzy = _normalize_id_match(raw_mid, valid_module_ids)
        if not matched_mid or matched_mid in seen:
            continue
        if was_fuzzy:
            print(f"  [fuzzy module match] '{raw_mid}' -> '{matched_mid}'")
        out.append(matched_mid)
        seen.add(matched_mid)
        if len(out) >= int(max_module_selections):
            break
    return out


def _parse_model_decision(response_text: str, candidate_ids: Sequence[str]) -> Dict[str, Any]:
    payload = _extract_json(response_text)
    allowed = set(candidate_ids)

    ranked = payload.get("ranked_entity_ids", [])
    if isinstance(ranked, str):
        ranked = [p.strip() for p in ranked.split(",") if p.strip()]
    if not isinstance(ranked, list):
        ranked = []

    filtered: List[str] = []
    seen: set[str] = set()
    for item in ranked:
        raw_eid = str(item).strip()
        if not raw_eid:
            continue
        matched_eid, was_fuzzy = _normalize_id_match(raw_eid, allowed)
        if not matched_eid or matched_eid in seen:
            continue
        if was_fuzzy:
            print(f"  [fuzzy ranking match] '{raw_eid}' -> '{matched_eid}'")
        filtered.append(matched_eid)
        seen.add(matched_eid)
        if len(filtered) >= 3:
            break

    conf_raw = payload.get("confidence", 1)
    try:
        confidence = int(conf_raw)
    except Exception:
        confidence = 1
    confidence = max(1, min(5, confidence))

    needs = str(payload.get("needs_expansion", "no")).strip().lower()
    needs_expansion = needs in {"yes", "true", "1"}

    return {
        "ranked_entity_ids": filtered,
        "confidence": confidence,
        "needs_expansion": needs_expansion,
        "raw_response": response_text.strip(),
    }


def _score_hits(ranked_entity_ids: Sequence[str], truth: set[str]) -> Dict[str, int]:
    ranked = list(ranked_entity_ids)
    top1 = 1 if ranked and ranked[0] in truth else 0
    top3 = 1 if any(e in truth for e in ranked[:3]) else 0
    return {"top1_hit": top1, "top3_hit": top3}


def _aggregate_condition_metrics(task_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(task_rows)
    if n == 0:
        return {
            "task_count": 0,
            "top1_hit_rate": 0.0,
            "top3_hit_rate": 0.0,
            "expands_per_solved_task": 0.0,
            "retrieval_recall_any_rate": 0.0,
            "retrieval_recall_mean": 0.0,
            "candidate_count_mean": 0.0,
            "candidate_count_max": 0,
            "orientation_family_added_total": 0,
            "orientation_family_expansion_tasks": 0,
            "ground_truth_missing_from_index_tasks": 0,
            "ground_truth_missing_from_index_entities": 0,
            "candidate_miss_despite_index_presence": 0,
            "false_confidence_curve": {str(t): {"events": 0, "total": 0, "rate": 0.0} for t in FCR_THRESHOLDS},
            "confidence_bin_counts": {str(i): 0 for i in range(1, 6)},
            "confidence_examples": 0,
            "calibration_status": "insufficient_data",
            "judge_tokens": 0,
            "module_selection_tokens_total": 0,
            "module_selection_tokens_mean": 0.0,
            "total_tokens_cold": 0,
            "total_tokens_warm": 0,
            "tokens_per_completed_task_cold": 0.0,
            "tokens_per_completed_task_warm": 0.0,
        }

    top1_hits = sum(int(r.get("top1_hit", 0)) for r in task_rows)
    top3_hits = sum(int(r.get("top3_hit", 0)) for r in task_rows)
    total_expands = sum(int(r.get("expansions_used", 0)) for r in task_rows)
    retrieval_any_hits = sum(1 for r in task_rows if bool(r.get("candidate_has_any_ground_truth", False)))
    retrieval_recall_sum = sum(float(r.get("candidate_recall_at_k", 0.0)) for r in task_rows)
    candidate_counts = [int(r.get("candidate_count", len(r.get("candidate_ids", [])))) for r in task_rows]
    orientation_family_added_total = sum(int(r.get("orientation_family_added_count", 0)) for r in task_rows)
    orientation_family_expansion_tasks = sum(
        1 for r in task_rows if int(r.get("orientation_family_added_count", 0)) > 0
    )
    missing_index_tasks = sum(1 for r in task_rows if len(r.get("ground_truth_missing_from_index", [])) > 0)
    missing_index_entities = sum(len(r.get("ground_truth_missing_from_index", [])) for r in task_rows)
    candidate_miss_despite_index = sum(
        1
        for r in task_rows
        if int(r.get("ground_truth_in_index_count", 0)) > 0 and not bool(r.get("candidate_has_any_ground_truth", False))
    )

    confidence_bins = {str(i): 0 for i in range(1, 6)}
    for row in task_rows:
        c = int(row.get("confidence", 1))
        c = max(1, min(5, c))
        confidence_bins[str(c)] += 1

    fcr_curve: Dict[str, Dict[str, Any]] = {}
    for t in FCR_THRESHOLDS:
        events = 0
        for row in task_rows:
            if int(row.get("top1_hit", 0)) == 1:
                continue
            if int(row.get("confidence", 1)) < t:
                continue
            if bool(row.get("needs_expansion", False)):
                continue
            events += 1
        fcr_curve[str(t)] = {
            "events": events,
            "total": n,
            "rate": (events / n) if n else 0.0,
        }

    total_tokens_cold = sum(int(r.get("total_tokens_cold", 0)) for r in task_rows)
    total_tokens_warm = sum(int(r.get("total_tokens_warm", 0)) for r in task_rows)
    judge_tokens = sum(int(r.get("judge_tokens", 0)) for r in task_rows)
    module_selection_tokens_total = sum(int(r.get("module_selection_tokens", 0)) for r in task_rows)

    solved = top3_hits
    return {
        "task_count": n,
        "top1_hit_rate": top1_hits / n,
        "top3_hit_rate": top3_hits / n,
        "expands_per_solved_task": (total_expands / solved) if solved else 0.0,
        "retrieval_recall_any_rate": retrieval_any_hits / n,
        "retrieval_recall_mean": retrieval_recall_sum / n,
        "candidate_count_mean": (sum(candidate_counts) / n) if n else 0.0,
        "candidate_count_max": max(candidate_counts) if candidate_counts else 0,
        "orientation_family_added_total": orientation_family_added_total,
        "orientation_family_expansion_tasks": orientation_family_expansion_tasks,
        "ground_truth_missing_from_index_tasks": missing_index_tasks,
        "ground_truth_missing_from_index_entities": missing_index_entities,
        "candidate_miss_despite_index_presence": candidate_miss_despite_index,
        "false_confidence_curve": fcr_curve,
        "confidence_bin_counts": confidence_bins,
        "confidence_examples": n,
        "calibration_status": "calibrated" if n >= 30 else "insufficient_data",
        "judge_tokens": judge_tokens,
        "module_selection_tokens_total": module_selection_tokens_total,
        "module_selection_tokens_mean": (module_selection_tokens_total / n) if n else 0.0,
        "total_tokens_cold": total_tokens_cold,
        "total_tokens_warm": total_tokens_warm,
        "tokens_per_completed_task_cold": (total_tokens_cold / solved) if solved else float(total_tokens_cold),
        "tokens_per_completed_task_warm": (total_tokens_warm / solved) if solved else float(total_tokens_warm),
    }


def _print_summary(summary: Dict[str, Any]) -> None:
    print("\n" + "=" * 88)
    print("TASK BENCHMARK SUMMARY")
    print("=" * 88)
    print(f"Phase: {summary['phase']} | Tasks: {summary['task_count']}")
    cfg = summary.get("config", {})
    emb_backend = cfg.get("embedding_backend", "unknown")
    emb_model = cfg.get("embedding_model", "unknown")
    print(f"Embedding baseline backend: {emb_backend} ({emb_model})")
    print(summary["phase_note"])
    print("L2 scope note: L2 excluded intentionally to avoid conflating harness quality with unresolved IR design.")
    print("\nCondition metrics:")
    print(
        f"{'Condition':<18} {'Top1':>8} {'Top3':>8} {'Exp/Solved':>12} "
        f"{'Warm Tok/Done':>14} {'FCR@4':>10}"
    )
    print("-" * 88)
    for condition in summary["condition_summaries"]:
        c = summary["condition_summaries"][condition]
        fcr4 = c["false_confidence_curve"]["4"]["rate"]
        print(
            f"{condition:<18} {c['top1_hit_rate']:>8.2%} {c['top3_hit_rate']:>8.2%} "
            f"{c['expands_per_solved_task']:>12.2f} {c['tokens_per_completed_task_warm']:>14.1f} "
            f"{fcr4:>10.2%}"
        )

    gate = summary.get("success_gate", {})
    if gate.get("vs_bm25") or gate.get("vs_embed"):
        print("\nSuccess gate (semanticir_flow_v2 must beat both naive RAG baselines):")
        if gate.get("vs_bm25"):
            print(
                f"- vs BM25: accuracy={gate['vs_bm25']['accuracy_better']} warm_tokens={gate['vs_bm25']['warm_tokens_better']}"
            )
        if gate.get("vs_embed"):
            print(
                f"- vs Embed: accuracy={gate['vs_embed']['accuracy_better']} warm_tokens={gate['vs_embed']['warm_tokens_better']}"
            )
        if gate.get("claim_success") is not None:
            print(f"- claim_success: {gate['claim_success']}")

    deltas = summary.get("pairwise_deltas", {})
    if deltas:
        print("\nPairwise deltas:")
        for label, payload in deltas.items():
            print(
                f"- {label}: top3_delta={payload['top3_hit_rate_delta']:+.2%}, "
                f"warm_tokens_delta={payload['tokens_per_completed_task_warm_delta']:+.1f}"
            )


def _validate_smoke_test_results(results: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate smoke test results and return (passed, issues)."""
    issues = []

    for cond, tasks in results.get("condition_results", {}).items():
        if not tasks:
            continue

        # Check 1: Module selection failures (all zeros = broken)
        if cond == "semanticir_flow_v2":
            module_counts = [len(t.get("module_selection_module_ids", [])) for t in tasks]
            if all(c == 0 for c in module_counts):
                issues.append(f"{cond}: Module selection returned 0 modules for all tasks (prefix mismatch?)")

            # Check 2: Orientation mode fallback
            modes = [t.get("orientation_mode", "") for t in tasks]
            fallback_count = sum(1 for m in modes if "fallback" in m)
            if fallback_count == len(tasks):
                issues.append(f"{cond}: All tasks fell back to full L3 (module selection broken)")

        # Check 3: Truncated responses (incomplete JSON)
        truncated = 0
        for t in tasks:
            raw = t.get("raw_response", "").strip()
            # Strip markdown code blocks if present
            if raw.endswith("```"):
                raw = raw[:-3].strip()
            # Check if JSON appears complete (ends with } or ])
            if raw and not (raw.endswith("}") or raw.endswith("]")):
                truncated += 1
        if truncated > 0:
            issues.append(f"{cond}: {truncated}/{len(tasks)} tasks have truncated JSON responses")

        # Check 4: Zero accuracy with returned IDs
        hits = sum(1 for t in tasks if t.get("top3_hit"))
        returned_ids = sum(1 for t in tasks if t.get("ranked_entity_ids"))
        if hits == 0 and returned_ids == len(tasks):
            issues.append(f"{cond}: 0% accuracy despite model returning IDs (format mismatch?)")

        # Check 5: All confidence=1 (model not following format)
        confs = [t.get("confidence", 0) for t in tasks]
        if all(c == 1 for c in confs):
            issues.append(f"{cond}: All tasks have confidence=1 (model may not be following prompt format)")

    return len(issues) == 0, issues


def run_task_benchmark(
    repo_path: Path,
    task_pack_path: Path,
    output_path: Path,
    raw_corpus_path: Path,
    provider_name: str = "anthropic",
    model: str = "",
    top_k: int = 5,
    max_expansions: int = 1,
    max_family_candidates: int = 20,
    orientation_mode: str = "filtered_l3",
    max_module_selections: int = 5,
    max_module_entity_candidates: int = 40,
    rate_limit: float = 0.3,
    embedding_model: str = "all-MiniLM-L6-v2",
    conditions: Optional[List[str]] = None,
    smoke_test: bool = False,
) -> Dict[str, Any]:
    # Determine which conditions to run (default: all)
    selected_conditions = tuple(conditions) if conditions else CONDITIONS
    for c in selected_conditions:
        if c not in CONDITIONS:
            raise ValueError(f"Unknown condition: {c!r}. Valid: {CONDITIONS}")
    task_pack = _load_task_pack(task_pack_path)
    tasks = list(task_pack.get("tasks", []))

    # Smoke test: limit to 5 tasks for quick validation
    if smoke_test:
        tasks = tasks[:5]
        print(f"[SMOKE TEST] Running {len(tasks)} tasks to validate model behavior...")

    phase = str(task_pack.get("phase", "")).strip().upper()
    phase_note = str(task_pack.get("phase_note", "")).strip()
    if not phase:
        phase, phase_note = _phase_from_task_count(len(tasks))

    if not raw_corpus_path.exists():
        build_raw_retrieval_corpus(repo_path=repo_path, output_path=raw_corpus_path)

    bm25 = BM25Retriever.from_corpus_path(raw_corpus_path)
    embed = MiniLMRetriever.from_corpus_path(raw_corpus_path, model_name=embedding_model)
    embedding_backend = getattr(embed, "backend", "unknown")
    # Only require real embedding backend if naive_rag_embed is in selected conditions
    if "naive_rag_embed" in selected_conditions and embedding_backend == "tfidf_fallback":
        raise RuntimeError(
            "Benchmark requires real embedding backend (minilm, mpnet) for naive_rag_embed condition. "
            f"Got embedding_backend={embedding_backend!r}. "
            "Install sentence-transformers, or exclude naive_rag_embed via --conditions flag."
        )

    llm_mode = "remote"
    llm_error = ""

    # Use default model for provider if not specified
    if not model:
        model = PROVIDERS[provider_name]["default"]

    _ensure_provider_credentials(provider_name)
    try:
        provider = create_provider(provider_name, model)
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise RuntimeError(
            f"Failed to initialize {provider_name} provider for remote model calls. "
            f"Check API credentials and re-run."
        ) from exc

    orientation_mode_norm = str(orientation_mode).strip().lower()
    if orientation_mode_norm not in {"filtered_l3", "full_l3"}:
        raise ValueError(f"Unsupported orientation_mode={orientation_mode!r}; expected filtered_l3 or full_l3")

    bearings_context = _fetch_bearings_markdown(repo_path)
    orientation_selection_context_full = _build_orientation_selection_context(repo_path)
    index_entity_ids = _load_index_entity_ids(repo_path)
    module_id_to_file_path, _ = _build_module_id_file_path_maps(repo_path)
    valid_module_ids = set(module_id_to_file_path.keys())
    entity_family_index = _build_entity_family_index(repo_path)

    condition_results: Dict[str, List[Dict[str, Any]]] = {c: [] for c in selected_conditions}

    for idx, task in enumerate(tasks, start=1):
        task_id = str(task.get("task_id", f"T{idx:03d}"))
        query = str(task.get("query", "")).strip()
        truth = set(str(x) for x in task.get("ground_truth_entity_ids", []))
        if not query or not truth:
            continue
        truth_module_paths: Dict[str, str] = {}
        for teid in truth:
            loc = get_entity_location(repo_path=repo_path, entity_id=teid)
            if not loc:
                continue
            truth_module_paths[str(teid)] = str(loc.get("file_path", ""))

        print(f"[{idx}/{len(tasks)}] {task_id}: {query}")

        for condition in selected_conditions:
            retrieval_backend = ""
            orientation_selection_candidate_ids: List[str] = []
            orientation_selection_response = ""
            orientation_family_candidate_ids: List[str] = []
            orientation_family_added_ids: List[str] = []
            module_selection_module_ids: List[str] = []
            module_selection_response = ""
            module_selection_entity_ids: List[str] = []
            module_selection_entities_per_module: Dict[str, int] = {}
            module_selection_tokens = 0
            module_selection_l3_tokens = 0
            gt_in_selected_modules = 0
            orientation_mode_used = orientation_mode_norm if condition == "semanticir_flow_v2" else ""
            orientation_phase_tokens = 0
            api_tokens_0 = 0
            if condition == "raw_baseline":
                candidates = _retrieve_internal(repo_path=repo_path, query=query, top_k=top_k, level="L0")
                retrieval_backend = "internal_like_l0"
                if not candidates:
                    candidates = _retrieve_bm25(retriever=bm25, query=query, top_k=top_k)
                    retrieval_backend = "bm25_fallback_from_internal_like_l0"
            elif condition == "semanticir_flow_v2":
                candidates = []
                retrieval_backend = (
                    "orientation_filtered_l3_llm_selection"
                    if orientation_mode_norm == "filtered_l3"
                    else "orientation_full_l3_llm_selection"
                )
                orientation_selection_context = ""

                if orientation_mode_norm == "filtered_l3" and bearings_context and valid_module_ids:
                    module_prompt, module_selection_tokens = _build_module_selection_prompt(
                        task_query=query,
                        bearings_context=bearings_context,
                        max_module_selections=max_module_selections,
                    )
                    try:
                        module_response, api_tokens_mod = provider.complete(
                            module_prompt,
                            system="Return JSON only. No markdown.",
                            max_tokens=160,
                        )
                        module_selection_response = module_response.strip()
                        api_tokens_0 += int(api_tokens_mod)
                        module_selection_module_ids = _parse_module_selection(
                            module_selection_response,
                            valid_module_ids=valid_module_ids,
                            max_module_selections=max_module_selections,
                        )
                    except Exception as exc:  # pragma: no cover - depends on runtime env
                        raise RuntimeError(
                            f"Module selection call failed for task {task_id} under {condition}."
                        ) from exc

                    selected_module_paths = [
                        module_id_to_file_path[mid]
                        for mid in module_selection_module_ids
                        if mid in module_id_to_file_path
                    ]
                    if selected_module_paths:
                        module_selection_entity_ids, module_selection_entities_per_module = _expand_modules_to_entity_ids(
                            repo_path=repo_path,
                            module_file_paths=selected_module_paths,
                            max_module_entity_candidates=max_module_entity_candidates,
                        )
                        selected_set = set(selected_module_paths)
                        gt_in_selected_modules = sum(
                            1 for fp in truth_module_paths.values() if fp and fp in selected_set
                        )
                        orientation_selection_context = _build_orientation_selection_context(
                            repo_path=repo_path,
                            entity_id_filter=module_selection_entity_ids,
                            include_bearings=False,
                        )

                    if not orientation_selection_context:
                        orientation_mode_used = "filtered_l3_fallback_full"
                        retrieval_backend = "orientation_filtered_l3_fallback_full_llm_selection"
                        orientation_selection_context = orientation_selection_context_full
                elif orientation_mode_norm == "full_l3":
                    orientation_selection_context = orientation_selection_context_full
                else:
                    orientation_mode_used = "filtered_l3_fallback_full"
                    retrieval_backend = "orientation_filtered_l3_missing_context_fallback_full_llm_selection"
                    orientation_selection_context = orientation_selection_context_full

                module_selection_l3_tokens = (
                    int(count_tokens(orientation_selection_context))
                    if orientation_selection_context
                    else 0
                )

                if orientation_selection_context:
                    orient_prompt, orientation_phase_tokens = _build_orientation_selection_prompt(
                        task_query=query,
                        orientation_context=orientation_selection_context,
                        top_k=top_k,
                    )
                    try:
                        orient_response, api_tokens_orient = provider.complete(
                            orient_prompt,
                            system="Return JSON only. No markdown.",
                            max_tokens=220,
                        )
                        api_tokens_0 += int(api_tokens_orient)
                        orientation_selection_response = orient_response.strip()
                        orientation_selection_candidate_ids = _parse_orientation_candidates(
                            orientation_selection_response,
                            valid_entity_ids=index_entity_ids,
                            top_k=top_k,
                        )
                    except Exception as exc:  # pragma: no cover - depends on runtime env
                        raise RuntimeError(
                            f"Orientation selection call failed for task {task_id} under {condition}."
                        ) from exc
                elif not orientation_selection_context:
                    retrieval_backend = "orientation_context_missing_bm25_l1_lift"

                if orientation_selection_candidate_ids:
                    orientation_family_candidate_ids, orientation_family_added_ids = expand_entity_family_candidates(
                        orientation_selection_candidate_ids,
                        entity_family_index,
                        max_candidates=max_family_candidates,
                    )
                    candidates = _hydrate_candidates_from_entity_ids(
                        repo_path=repo_path,
                        entity_ids=orientation_family_candidate_ids,
                        level="L1",
                    )
                    if not candidates:
                        retrieval_backend = "orientation_llm_unresolvable_ids_bm25_l1_lift"
                if not candidates:
                    candidates = _lift_bm25_to_l1_candidates(
                        repo_path=repo_path,
                        retriever=bm25,
                        query=query,
                        top_k=top_k,
                    )
                    if retrieval_backend.endswith("_llm_selection"):
                        retrieval_backend = "orientation_llm_empty_bm25_l1_lift"
            elif condition == "naive_rag_bm25":
                candidates = _retrieve_bm25(retriever=bm25, query=query, top_k=top_k)
                retrieval_backend = "bm25"
            elif condition == "naive_rag_embed":
                candidates = _retrieve_embed(retriever=embed, query=query, top_k=top_k)
                retrieval_backend = f"embed_{embedding_backend}"
            else:
                candidates = []
                retrieval_backend = "unknown"

            candidate_ids = [str(c["entity_id"]) for c in candidates]
            retrieval_diag = _retrieval_diagnostic(
                truth=truth,
                candidate_ids=candidate_ids,
                index_entity_ids=index_entity_ids,
            )

            if not candidates:
                condition_results[condition].append(
                    {
                        "task_id": task_id,
                        "query": query,
                        "retrieval_query": query,
                        "retrieval_backend": retrieval_backend,
                        "condition": condition,
                        "error": "no candidates",
                        "ground_truth_entity_ids": sorted(truth),
                        "top1_hit": 0,
                        "top3_hit": 0,
                        "expansions_used": 0,
                        "confidence": 1,
                        "needs_expansion": True,
                        "module_selection_tokens": int(module_selection_tokens),
                        "orientation_tokens": int(orientation_phase_tokens),
                        "retrieval_tokens": 0,
                        "expansion_tokens": 0,
                        "reasoning_tokens": 0,
                        "judge_tokens": 0,
                        "total_tokens_cold": int(module_selection_tokens) + int(orientation_phase_tokens),
                        "total_tokens_warm": 0,
                        "api_input_tokens": int(api_tokens_0),
                        "candidate_ids": [],
                        "candidate_count": 0,
                        "orientation_mode": orientation_mode_used,
                        "orientation_total_tokens": int(module_selection_tokens) + int(orientation_phase_tokens),
                        "module_selection_module_ids": module_selection_module_ids,
                        "module_selection_response": module_selection_response,
                        "module_selection_entity_count": int(len(module_selection_entity_ids)),
                        "module_selection_entities_per_module": module_selection_entities_per_module,
                        "module_selection_l3_tokens": int(module_selection_l3_tokens),
                        "gt_in_selected_modules_count": int(gt_in_selected_modules),
                        "gt_in_selected_modules_rate": (
                            float(gt_in_selected_modules) / float(len(truth_module_paths))
                            if truth_module_paths
                            else 0.0
                        ),
                        "orientation_selection_candidate_ids": orientation_selection_candidate_ids,
                        "orientation_family_candidate_ids": orientation_family_candidate_ids,
                        "orientation_family_added_ids": orientation_family_added_ids,
                        "orientation_family_added_count": int(len(orientation_family_added_ids)),
                        "orientation_selection_response": orientation_selection_response,
                        **retrieval_diag,
                    }
                )
                print(f"  {condition}: no candidates")
                continue

            orientation_text = ""

            prompt, buckets = _build_localization_prompt(
                condition=condition,
                task_query=query,
                candidates=candidates,
                orientation_segment=orientation_text,
            )

            api_tokens_1 = 0
            try:
                response, api_tokens_1 = provider.complete(
                    prompt,
                    system="Return JSON only. No markdown.",
                    max_tokens=220,
                )
                decision = _parse_model_decision(response, [c["entity_id"] for c in candidates])
            except Exception as exc:  # pragma: no cover - depends on runtime env
                raise RuntimeError(
                    f"Localization call failed for task {task_id} under {condition}."
                ) from exc

            expansions_used = 0
            expansion_text = ""
            api_tokens_2 = 0
            extra_buckets = {
                "orientation_tokens": 0,
                "retrieval_tokens": 0,
                "expansion_tokens": 0,
                "reasoning_tokens": 0,
            }

            if decision["needs_expansion"] and max_expansions > 0:
                top_choice = decision["ranked_entity_ids"][0] if decision["ranked_entity_ids"] else candidates[0]["entity_id"]
                expanded = _fetch_raw_source(repo_path, top_choice)
                if expanded:
                    expansions_used = 1
                    expansion_text = f"Entity ID: {top_choice}\n{expanded}"
                    prompt2, extra_buckets = _build_localization_prompt(
                        condition=condition,
                        task_query=query,
                        candidates=candidates,
                        orientation_segment="",
                        expansion_segment=expansion_text,
                    )
                    try:
                        response2, api_tokens_2 = provider.complete(
                            prompt2,
                            system="Return JSON only. No markdown.",
                            max_tokens=220,
                        )
                        decision = _parse_model_decision(response2, [c["entity_id"] for c in candidates])
                    except Exception as exc:  # pragma: no cover - depends on runtime env
                        raise RuntimeError(
                            f"Expansion follow-up call failed for task {task_id} under {condition}."
                        ) from exc

            scores = _score_hits(decision["ranked_entity_ids"], truth)

            orientation_tokens = (
                buckets["orientation_tokens"] + extra_buckets["orientation_tokens"] + int(orientation_phase_tokens)
            )
            retrieval_tokens = buckets["retrieval_tokens"] + extra_buckets["retrieval_tokens"]
            expansion_tokens = buckets["expansion_tokens"] + extra_buckets["expansion_tokens"]
            reasoning_tokens = buckets["reasoning_tokens"] + extra_buckets["reasoning_tokens"]
            judge_tokens = 0

            total_tokens_cold = (
                int(module_selection_tokens)
                + orientation_tokens
                + retrieval_tokens
                + expansion_tokens
                + reasoning_tokens
                + judge_tokens
            )
            total_tokens_warm = retrieval_tokens + expansion_tokens + reasoning_tokens + judge_tokens

            row = {
                "task_id": task_id,
                "query": query,
                "retrieval_query": query,
                "retrieval_backend": retrieval_backend,
                "condition": condition,
                "ground_truth_entity_ids": sorted(truth),
                "ranked_entity_ids": decision["ranked_entity_ids"],
                "top1_hit": scores["top1_hit"],
                "top3_hit": scores["top3_hit"],
                "confidence": int(decision["confidence"]),
                "needs_expansion": bool(decision["needs_expansion"]),
                "expansions_used": expansions_used,
                "module_selection_tokens": int(module_selection_tokens),
                "orientation_tokens": orientation_tokens,
                "retrieval_tokens": retrieval_tokens,
                "expansion_tokens": expansion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "judge_tokens": judge_tokens,
                "total_tokens_cold": total_tokens_cold,
                "total_tokens_warm": total_tokens_warm,
                "api_input_tokens": int(api_tokens_0) + int(api_tokens_1) + int(api_tokens_2),
                "candidate_ids": candidate_ids,
                "candidate_count": int(len(candidate_ids)),
                "orientation_mode": orientation_mode_used,
                "orientation_total_tokens": int(module_selection_tokens) + int(orientation_tokens),
                "module_selection_module_ids": module_selection_module_ids,
                "module_selection_response": module_selection_response,
                "module_selection_entity_count": int(len(module_selection_entity_ids)),
                "module_selection_entities_per_module": module_selection_entities_per_module,
                "module_selection_l3_tokens": int(module_selection_l3_tokens),
                "gt_in_selected_modules_count": int(gt_in_selected_modules),
                "gt_in_selected_modules_rate": (
                    float(gt_in_selected_modules) / float(len(truth_module_paths))
                    if truth_module_paths
                    else 0.0
                ),
                "orientation_selection_candidate_ids": orientation_selection_candidate_ids,
                "orientation_family_candidate_ids": orientation_family_candidate_ids,
                "orientation_family_added_ids": orientation_family_added_ids,
                "orientation_family_added_count": int(len(orientation_family_added_ids)),
                "orientation_selection_response": orientation_selection_response,
                "raw_response": decision["raw_response"],
                **retrieval_diag,
            }
            condition_results[condition].append(row)

            status = "ok" if scores["top3_hit"] else "miss"
            print(
                f"  {condition}: {status}, conf={decision['confidence']}, expand={expansions_used}, "
                f"top1={scores['top1_hit']}, top3={scores['top3_hit']}"
            )

            if rate_limit > 0:
                time.sleep(rate_limit)

    bm25.close()

    condition_summaries = {
        condition: _aggregate_condition_metrics(rows)
        for condition, rows in condition_results.items()
    }

    # Build success gate and pairwise deltas only when all required conditions are present
    sir = condition_summaries.get("semanticir_flow_v2")
    b = condition_summaries.get("naive_rag_bm25")
    e = condition_summaries.get("naive_rag_embed")

    success_gate = {}
    pairwise_deltas = {}

    if sir and b:
        success_gate["vs_bm25"] = {
            "accuracy_better": sir["top3_hit_rate"] > b["top3_hit_rate"],
            "warm_tokens_better": sir["tokens_per_completed_task_warm"] < b["tokens_per_completed_task_warm"],
        }
        pairwise_deltas["vs_bm25"] = {
            "top3_hit_rate_delta": sir["top3_hit_rate"] - b["top3_hit_rate"],
            "tokens_per_completed_task_warm_delta": sir["tokens_per_completed_task_warm"]
            - b["tokens_per_completed_task_warm"],
        }

    if sir and e:
        success_gate["vs_embed"] = {
            "accuracy_better": sir["top3_hit_rate"] > e["top3_hit_rate"],
            "warm_tokens_better": sir["tokens_per_completed_task_warm"] < e["tokens_per_completed_task_warm"],
        }
        pairwise_deltas["vs_embed"] = {
            "top3_hit_rate_delta": sir["top3_hit_rate"] - e["top3_hit_rate"],
            "tokens_per_completed_task_warm_delta": sir["tokens_per_completed_task_warm"]
            - e["tokens_per_completed_task_warm"],
        }

    # claim_success requires all comparisons to pass (only valid when all conditions present)
    if "vs_bm25" in success_gate and "vs_embed" in success_gate:
        success_gate["claim_success"] = (
            success_gate["vs_bm25"]["accuracy_better"]
            and success_gate["vs_bm25"]["warm_tokens_better"]
            and success_gate["vs_embed"]["accuracy_better"]
            and success_gate["vs_embed"]["warm_tokens_better"]
        )
    else:
        success_gate["claim_success"] = None  # Cannot determine without all conditions

    output = {
        "schema": "task_benchmark_results.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "model": provider.model,
        "repo_path": str(repo_path),
        "task_pack": str(task_pack_path),
        "raw_corpus": str(raw_corpus_path),
        "phase": phase,
        "phase_note": phase_note,
        "task_count": len(tasks),
        "config": {
            "top_k": int(top_k),
            "max_expansions": int(max_expansions),
            "max_family_candidates": int(max_family_candidates),
            "orientation_mode": orientation_mode_norm,
            "max_module_selections": int(max_module_selections),
            "max_module_entity_candidates": int(max_module_entity_candidates),
            "rate_limit": float(rate_limit),
            "embedding_model": embedding_model,
            "embedding_backend": embedding_backend,
            "judge_mode": "deterministic",
            "judge_prompt_version": "none",
            "llm_mode": llm_mode,
            "llm_error": llm_error,
        },
        "assumptions": {
            "small_sample_mode": True,
            "l2_scope": "excluded_from_validation",
            "false_confidence_mode": "curve_only_until_30_examples",
            "non_goal": "index.search LIKE retrieval is not a benchmark baseline",
            "orientation_prompt_cacheability": "high_same_bearings_and_l3_context_across_tasks",
        },
        "condition_summaries": condition_summaries,
        "pairwise_deltas": pairwise_deltas,
        "success_gate": success_gate,
        "condition_results": condition_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    _print_summary(output)
    print(f"\nResults saved to: {output_path.resolve()}")

    # Smoke test validation
    if smoke_test:
        print("\n" + "=" * 60)
        print("SMOKE TEST VALIDATION")
        print("=" * 60)
        passed, issues = _validate_smoke_test_results(output)
        if passed:
            print("PASSED: No issues detected. Safe to run full benchmark.")
        else:
            print("FAILED: Issues detected:")
            for issue in issues:
                print(f"  - {issue}")
            print("\nFix these issues before running the full benchmark.")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unified task benchmark")
    parser.add_argument("--repo-path", type=Path, required=True)
    parser.add_argument(
        "--task-pack",
        type=Path,
        default=Path(__file__).parent.parent / "test_packs" / "task_benchmark_phase_b_24.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,  # Will be auto-generated with timestamp
        help="Output path. If not set, auto-generates timestamped filename in tests/eval/results/",
    )
    parser.add_argument(
        "--raw-corpus",
        type=Path,
        default=Path(__file__).parent.parent / "corpus" / "fastapi_users_raw_retrieval_corpus.json",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "google", "deepseek"],
        default=None,
        help="LLM provider (if not set, will prompt interactively)",
    )
    parser.add_argument("--model", default="", help="Model name (uses provider default if not set)")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-expansions", type=int, default=1)
    parser.add_argument("--max-family-candidates", type=int, default=20)
    parser.add_argument(
        "--orientation-mode",
        choices=["filtered_l3", "full_l3"],
        default="filtered_l3",
    )
    parser.add_argument("--max-module-selections", type=int, default=5)
    parser.add_argument("--max-module-entity-candidates", type=int, default=40)
    parser.add_argument("--rate-limit", type=float, default=0.3)
    parser.add_argument("--embedding-model", default="all-MiniLM-L6-v2")
    parser.add_argument(
        "--conditions",
        type=str,
        default=None,
        help=f"Comma-separated list of conditions to run. Valid: {', '.join(CONDITIONS)}. Default: all.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run only 5 tasks and validate model behavior before full benchmark.",
    )
    args = parser.parse_args()

    # Parse conditions
    selected_conditions = None
    if args.conditions:
        selected_conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]

    # Auto-generate timestamped output path if not specified
    if args.output is None:
        from datetime import datetime
        results_dir = Path(__file__).parent.parent / "results"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        conditions_slug = "_".join(selected_conditions) if selected_conditions else "all"
        args.output = results_dir / f"task_benchmark_{conditions_slug}_{timestamp}.json"
        print(f"Output will be saved to: {args.output}")

    # Safety check: never overwrite existing results
    if args.output.exists():
        raise FileExistsError(
            f"Output file already exists: {args.output}\n"
            "Refusing to overwrite. Use a different --output path or delete the file manually."
        )

    # Interactive provider selection if not specified
    if args.provider is None:
        provider_name, model = select_provider_interactive()
        if args.model:
            model = args.model  # Override with explicit model if provided
    else:
        provider_name = args.provider
        model = args.model

    run_task_benchmark(
        repo_path=args.repo_path.resolve(),
        task_pack_path=args.task_pack,
        output_path=args.output,
        raw_corpus_path=args.raw_corpus,
        provider_name=provider_name,
        model=model,
        top_k=args.top_k,
        max_expansions=args.max_expansions,
        max_family_candidates=args.max_family_candidates,
        orientation_mode=args.orientation_mode,
        max_module_selections=args.max_module_selections,
        max_module_entity_candidates=args.max_module_entity_candidates,
        rate_limit=args.rate_limit,
        embedding_model=args.embedding_model,
        conditions=selected_conditions,
        smoke_test=args.smoke_test,
    )


if __name__ == "__main__":
    main()
