#!/usr/bin/env python3
"""Aider Repo Map Comparison Benchmark.

Compares SemanticIR's task accuracy and token cost against Aider's repo map
on the same task pack, same models, same scoring.

Conditions:
- semanticir_flow_v2: bearings.md → L3 orientation → L1 ranking
- aider_repomap_1k: Aider map at 1024 token budget
- aider_repomap_2k: Aider map at 2048 token budget
- aider_repomap_4k: Aider map at 4096 token budget
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # tests/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))  # project root

from eval.providers import create_provider, PROVIDERS
from eval.runners.run_task_benchmark import (
    _fetch_bearings_markdown,
    _fetch_l3_index,
    _build_module_id_file_path_maps,
    _build_module_selection_prompt,
    _parse_module_selection,
    _expand_modules_to_entity_ids,
    _build_orientation_selection_context,
)
from index.store.db import connect
from index.store.fetch import get_entity_with_ir
from ir.token_count import count_tokens

# Paths
ROOT = Path(__file__).resolve().parents[3]
REPO_PATH = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"
BASELINES_DIR = ROOT / "tests" / "eval" / "baselines" / "aider"
RESULTS_DIR = ROOT / "tests" / "eval" / "results" / "aider_comparison"
TASK_PACK_PATH = ROOT / "tests" / "eval" / "test_packs" / "task_benchmark_phase_b_24.json"

CONDITIONS = (
    "semanticir_flow_v2",
    "aider_repomap_1k",
    "aider_repomap_2k",
    "aider_repomap_4k",
)


def load_task_pack() -> Dict[str, Any]:
    """Load the Phase B 24-task pack."""
    data = json.loads(TASK_PACK_PATH.read_text(encoding="utf-8"))
    tasks = data.get("tasks", [])
    if not tasks:
        raise ValueError(f"Task pack has no tasks: {TASK_PACK_PATH}")
    return data


def load_aider_map(token_budget: str) -> str:
    """Load pre-generated Aider repo map."""
    map_path = BASELINES_DIR / f"aider_map_{token_budget}.txt"
    if not map_path.exists():
        raise FileNotFoundError(
            f"Aider map not found: {map_path}\n"
            f"Run: python scripts/generate_aider_maps.py"
        )
    return map_path.read_text(encoding="utf-8")


def load_qualified_name_mapping() -> Dict[str, str]:
    """Load qualified_name -> entity_id reverse mapping for scoring Aider responses."""
    mapping_path = BASELINES_DIR / "qualified_name_to_entity_id.json"
    if not mapping_path.exists():
        raise FileNotFoundError(
            f"Entity mapping not found: {mapping_path}\n"
            f"Run: python scripts/generate_entity_mapping.py"
        )
    return json.loads(mapping_path.read_text(encoding="utf-8"))


def fetch_bearings(repo_path: Path) -> str:
    """Fetch bearings.md content."""
    bearings_path = repo_path / "bearings.md"
    if not bearings_path.exists():
        return ""
    return bearings_path.read_text(encoding="utf-8").strip()


def fetch_l3_index(repo_path: Path) -> str:
    """Fetch all L3 IR rows."""
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        return ""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT ir_text FROM ir_rows WHERE mode='L3' ORDER BY entity_id"
    ).fetchall()
    conn.close()
    return "\n".join(str(r[0]) for r in rows)


def fetch_l1_tokens(repo_path: Path, entity_ids: Sequence[str]) -> str:
    """Fetch L1 tokens for given entity IDs."""
    lines = []
    for eid in entity_ids:
        row = get_entity_with_ir(repo_path=repo_path, entity_id=eid, mode="L1")
        if row:
            lines.append(str(row.get("ir_text", "")))
    return "\n".join(lines)


def build_module_selection_prompt(task_query: str, repo_path: Path, max_modules: int = 5) -> Tuple[str, int]:
    """Build Phase 1 prompt: select relevant modules from bearings."""
    bearings = _fetch_bearings_markdown(repo_path)
    return _build_module_selection_prompt(
        task_query=task_query,
        bearings_context=bearings,
        max_module_selections=max_modules,
    )


def build_filtered_l3_prompt(
    task_query: str,
    repo_path: Path,
    module_entity_ids: List[str],
) -> Tuple[str, int]:
    """Build Phase 2 prompt: select entities from filtered L3."""
    # Get filtered L3 context (only entities in selected modules)
    l3_context = _build_orientation_selection_context(
        repo_path=repo_path,
        entity_id_filter=module_entity_ids,
        include_bearings=False,
    )

    system = """You are selecting code entities most relevant to a task.

Return a JSON object with:
- ranked_entity_ids: list of up to 5 entity IDs, most relevant first
- confidence: integer 1-5

Use exact entity IDs from the L3 index."""

    prompt = f"""Task: {task_query}

{l3_context}

Return JSON only with ranked_entity_ids and confidence."""

    return f"{system}\n\n{prompt}", count_tokens(system + prompt)


def build_semanticir_prompt_legacy(task_query: str, repo_path: Path) -> Tuple[str, int]:
    """Build prompt for SemanticIR flow condition (LEGACY - full L3)."""
    bearings = fetch_bearings(repo_path)
    l3_index = fetch_l3_index(repo_path)

    system = """You are analyzing a codebase to identify relevant code entities for a task.

You will receive:
1. Bearings: A high-level module map of the codebase
2. L3 Index: Compressed entity tags showing type, ID, domain, and category

Return a JSON object with:
- ranked_entity_ids: list of up to 5 entity IDs, most relevant first
- confidence: integer 1-5

Select entities most relevant to the task. Use exact entity IDs from the L3 index."""

    prompt = f"""Task: {task_query}

Bearings:
{bearings}

L3 Index:
{l3_index}

Return JSON only with ranked_entity_ids and confidence."""

    return f"{system}\n\n{prompt}", count_tokens(system + prompt)


def build_aider_prompt(task_query: str, aider_map: str) -> Tuple[str, int]:
    """Build prompt for Aider repo map condition."""
    system = """You are analyzing a codebase to answer questions about its structure and behavior.

You will receive:
1. A repository map showing file paths with class and function definitions.
2. A task description asking you to identify specific code entities.

Return a JSON object with:
- ranked_entity_ids: list of up to 5 entity identifiers, most relevant first.
  Use the format: qualified function/method/class names as shown in the map
  (e.g., "JWTStrategy.read_token", "get_oauth_router", "UserManager")
- confidence: integer 1-5

Identify the entities most relevant to answering the task. Be specific — name
the exact functions, methods, or classes, not just files."""

    prompt = f"""Task: {task_query}

Repository Map:
{aider_map}

Return JSON only with ranked_entity_ids and confidence."""

    return f"{system}\n\n{prompt}", count_tokens(system + prompt)


def extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from model response."""
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


def parse_semanticir_entity_id(raw: str) -> str:
    """Extract entity ID from L3 row or raw entity ID.

    Handles formats:
    - "CRRNTSRDPNDNCY" -> "CRRNTSRDPNDNCY"
    - "AMT CRRNTSRDPNDNCY #AUTH #TEST" -> "CRRNTSRDPNDNCY"
    - "CLS INVLDD @fastapi_users/exceptions.py:8" -> "INVLDD"
    - "MT GTRGSTRRTR" -> "GTRGSTRRTR"
    """
    s = str(raw).strip()
    if not s:
        return ""

    # If it starts with a known type prefix, extract the second token (entity ID)
    type_prefixes = {"FN", "AFN", "MT", "AMT", "CLS", "MD", "ENT"}
    parts = s.split()

    if len(parts) >= 2 and parts[0] in type_prefixes:
        # Second token is the entity ID
        entity_id = parts[1]
        # Strip any @ location suffix (e.g., "INVLDD@file:line" -> "INVLDD")
        if "@" in entity_id:
            entity_id = entity_id.split("@")[0]
        return entity_id

    # Otherwise, treat the whole thing as an entity ID (strip any trailing tags)
    # Handle "CRRNTSRDPNDNCY #AUTH" -> "CRRNTSRDPNDNCY"
    if "#" in s:
        s = s.split("#")[0].strip()
    if "@" in s:
        s = s.split("@")[0].strip()
    return s


def map_aider_response_to_entity_ids(
    response_ids: List[str],
    qname_mapping: Dict[str, str],
    log_attempts: bool = False,
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """Map Aider's qualified names to SemanticIR entity IDs.

    Rules:
    1. Strip file path prefixes (e.g., "fastapi_users/exceptions.py:Class" -> "Class")
    2. Try exact match against qualified names
    3. Try suffix match (e.g., "read_token" matches "JWTStrategy.read_token")

    Returns (mapped_ids, unmapped_names, mapping_log).
    """
    mapped = []
    unmapped = []
    seen = set()
    mapping_log = []

    for raw_name in response_ids:
        raw_name = str(raw_name).strip()
        if not raw_name:
            continue

        log_entry = {"input": raw_name, "attempts": [], "result": None}

        # Rule 1a: Strip file path prefix (e.g., "fastapi_users/foo.py:ClassName" -> "ClassName")
        name = raw_name
        if ":" in name:
            name = name.split(":")[-1]
            log_entry["attempts"].append(f"stripped_file_path -> '{name}'")

        # Rule 1b: Strip Python module prefix (e.g., "exceptions.InvalidID" -> "InvalidID")
        leaf = None
        if "." in name and "/" not in name:
            # Likely a module.ClassName format, take the last part
            leaf = name.rsplit(".", 1)[-1]
            log_entry["attempts"].append(f"stripped_module_prefix -> '{leaf}'")

        # Rule 2: Try exact match first (try both name and leaf)
        for try_name in ([name, leaf] if leaf else [name]):
            if try_name in qname_mapping:
                eid = qname_mapping[try_name]
                log_entry["attempts"].append(f"exact_match('{try_name}') -> {eid}")
                if eid not in seen:
                    mapped.append(eid)
                    seen.add(eid)
                    log_entry["result"] = eid
                else:
                    log_entry["result"] = f"dup:{eid}"
                if log_attempts:
                    mapping_log.append(log_entry)
                break
        else:
            # No exact match found, continue to suffix match
            pass
        if log_entry["result"]:
            continue

        # Rule 3: Try suffix match (e.g., "read_token" matches "JWTStrategy.read_token")
        found = False
        suffix_matches = []
        # Try both name and leaf for suffix matching
        for try_name in ([name, leaf] if leaf else [name]):
            if not try_name:
                continue
            for qname, eid in qname_mapping.items():
                # Match if qname ends with .name or equals name
                if qname.endswith(f".{try_name}") or qname == try_name:
                    suffix_matches.append((qname, eid))

        log_entry["attempts"].append(f"suffix_match('{name}'/'{leaf}') -> {len(suffix_matches)} candidates")

        if suffix_matches:
            # Take first match
            qname, eid = suffix_matches[0]
            log_entry["attempts"].append(f"  selected: {qname} -> {eid}")
            if eid not in seen:
                mapped.append(eid)
                seen.add(eid)
                log_entry["result"] = eid
                found = True
            else:
                log_entry["result"] = f"dup:{eid}"
                found = True

        if not found:
            log_entry["result"] = "UNMAPPED"
            unmapped.append(raw_name)

        if log_attempts:
            mapping_log.append(log_entry)

    return mapped, unmapped, mapping_log


def score_task(
    ranked_ids: Sequence[str],
    ground_truth_ids: Sequence[str],
) -> Dict[str, Any]:
    """Score a task result against ground truth."""
    truth = set(ground_truth_ids)
    ranked = list(ranked_ids)[:5]

    top1_hit = 1 if ranked and ranked[0] in truth else 0
    top3_hit = 1 if any(e in truth for e in ranked[:3]) else 0
    any_hit = 1 if any(e in truth for e in ranked) else 0

    return {
        "top1_hit": top1_hit,
        "top3_hit": top3_hit,
        "any_hit": any_hit,
    }


def run_condition(
    condition: str,
    tasks: List[Dict[str, Any]],
    provider,
    repo_path: Path,
    qname_mapping: Dict[str, str],
    aider_maps: Dict[str, str],
    module_id_to_file_path: Optional[Dict[str, str]] = None,
    valid_module_ids: Optional[set] = None,
    max_module_selections: int = 5,
    max_module_entity_candidates: int = 40,
) -> List[Dict[str, Any]]:
    """Run a single condition across all tasks."""
    results = []

    for task in tasks:
        task_id = task.get("task_id", "unknown")
        query = task.get("query", "")
        ground_truth = task.get("ground_truth_entity_ids", [])

        print(f"  {task_id}: ", end="", flush=True)

        start_time = time.time()

        # SemanticIR uses two-phase filtered L3 approach
        if condition == "semanticir_flow_v2":
            try:
                # Phase 1: Module selection from bearings
                module_prompt, module_tokens = build_module_selection_prompt(
                    query, repo_path, max_modules=max_module_selections
                )
                module_response, _ = provider.complete(
                    module_prompt,
                    system="Return JSON only. No markdown.",
                    max_tokens=160,
                )

                # Parse selected modules
                module_ids = _parse_module_selection(
                    module_response,
                    valid_module_ids=valid_module_ids or set(),
                    max_module_selections=max_module_selections,
                )

                # Expand modules to entity IDs
                selected_paths = [
                    module_id_to_file_path[mid]
                    for mid in module_ids
                    if module_id_to_file_path and mid in module_id_to_file_path
                ]
                module_entity_ids, _ = _expand_modules_to_entity_ids(
                    repo_path=repo_path,
                    module_file_paths=selected_paths,
                    max_module_entity_candidates=max_module_entity_candidates,
                )

                # Phase 2: Entity selection from filtered L3
                if module_entity_ids:
                    prompt, l3_tokens = build_filtered_l3_prompt(query, repo_path, module_entity_ids)
                    input_tokens = module_tokens + l3_tokens
                else:
                    # Fallback to full L3 if no modules selected
                    prompt, input_tokens = build_semanticir_prompt_legacy(query, repo_path)
                    input_tokens += module_tokens

                response_text, _ = provider.complete(prompt, max_tokens=300)
                latency_ms = int((time.time() - start_time) * 1000)

            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    "task_id": task_id,
                    "condition": condition,
                    "error": str(e),
                    "ground_truth": ground_truth,
                })
                continue

        # Aider conditions use single-shot prompt
        elif condition.startswith("aider_repomap_"):
            budget = condition.split("_")[-1]  # "1k", "2k", "4k"
            aider_map = aider_maps.get(budget, "")
            prompt, input_tokens = build_aider_prompt(query, aider_map)

            try:
                response_text, reported_tokens = provider.complete(prompt, max_tokens=300)
                latency_ms = int((time.time() - start_time) * 1000)
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    "task_id": task_id,
                    "condition": condition,
                    "error": str(e),
                    "ground_truth": ground_truth,
                })
                continue
        else:
            print("SKIP (unknown condition)")
            continue

        # Parse response
        payload = extract_json(response_text)
        raw_ids = payload.get("ranked_entity_ids", [])
        if isinstance(raw_ids, str):
            raw_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
        confidence = min(5, max(1, int(payload.get("confidence", 1))))

        # Map to entity IDs
        mapping_failures = []
        if condition.startswith("aider_repomap_"):
            # Aider returns qualified names, need to map to entity IDs
            ranked_ids, mapping_failures, _ = map_aider_response_to_entity_ids(
                raw_ids, qname_mapping
            )
        else:
            # SemanticIR returns L3 rows or entity IDs, extract just the ID
            ranked_ids = [parse_semanticir_entity_id(x) for x in raw_ids]
            ranked_ids = [x for x in ranked_ids if x]  # filter empty

        # Score
        scores = score_task(ranked_ids, ground_truth)

        result = {
            "task_id": task_id,
            "condition": condition,
            "query": query,
            "ground_truth": ground_truth,
            "ranked_entity_ids": ranked_ids[:5],
            "raw_response_ids": raw_ids[:10],
            "confidence": confidence,
            "input_tokens": input_tokens,
            "latency_ms": latency_ms,
            "raw_response": response_text,
            "mapping_failures": mapping_failures,
            **scores,
        }
        results.append(result)

        hit_marker = "✓" if scores["top3_hit"] else "✗"
        print(f"{hit_marker} (conf={confidence}, tokens={input_tokens})")

    return results


def aggregate_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate metrics for a condition."""
    n = len(results)
    if n == 0:
        return {"task_count": 0, "top1_hit_rate": 0.0, "top3_hit_rate": 0.0}

    top1_hits = sum(r.get("top1_hit", 0) for r in results)
    top3_hits = sum(r.get("top3_hit", 0) for r in results)
    any_hits = sum(r.get("any_hit", 0) for r in results)
    total_tokens = sum(r.get("input_tokens", 0) for r in results)
    mapping_failures = sum(len(r.get("mapping_failures", [])) for r in results)

    return {
        "task_count": n,
        "top1_hit_rate": top1_hits / n,
        "top3_hit_rate": top3_hits / n,
        "candidate_recall": any_hits / n,
        "tokens_per_task_input": total_tokens / n,
        "total_input_tokens": total_tokens,
        "mapping_failures": mapping_failures,
    }


def generate_summary(
    all_results: Dict[str, List[Dict[str, Any]]],
    model_name: str,
) -> str:
    """Generate human-readable summary."""
    lines = [
        "# Aider Comparison Benchmark Results",
        "",
        f"Model: {model_name}",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Metrics by Condition",
        "",
        "| Condition | Top-1 | Top-3 | Recall | Tokens/Task | Mapping Failures |",
        "|-----------|-------|-------|--------|-------------|------------------|",
    ]

    metrics_by_condition = {}
    for condition, results in all_results.items():
        m = aggregate_metrics(results)
        metrics_by_condition[condition] = m
        lines.append(
            f"| {condition} | {m['top1_hit_rate']:.1%} | {m['top3_hit_rate']:.1%} | "
            f"{m['candidate_recall']:.1%} | {m['tokens_per_task_input']:.0f} | "
            f"{m['mapping_failures']} |"
        )

    lines.extend(["", "## Pairwise Comparison", ""])

    sir = metrics_by_condition.get("semanticir_flow_v2", {})
    for budget in ["1k", "2k", "4k"]:
        aider = metrics_by_condition.get(f"aider_repomap_{budget}", {})
        if sir and aider:
            top3_delta = (sir.get("top3_hit_rate", 0) - aider.get("top3_hit_rate", 0)) * 100
            token_delta = sir.get("tokens_per_task_input", 0) - aider.get("tokens_per_task_input", 0)
            lines.append(
                f"SemanticIR v2 vs Aider ({budget}): "
                f"top3 delta = {top3_delta:+.1f}%, token delta = {token_delta:+.0f}"
            )

    return "\n".join(lines)


def infer_provider(model: str) -> str:
    """Infer provider from model name."""
    model_lower = model.lower()
    if any(x in model_lower for x in ("haiku", "sonnet", "opus", "claude")):
        return "anthropic"
    if any(x in model_lower for x in ("gpt", "o1")):
        return "openai"
    if any(x in model_lower for x in ("gemini",)):
        return "google"
    if any(x in model_lower for x in ("deepseek",)):
        return "deepseek"
    return "openai"  # default


def main():
    parser = argparse.ArgumentParser(description="Aider Comparison Benchmark")
    parser.add_argument("--provider", default=None, help="LLM provider (auto-detected if not specified)")
    parser.add_argument("--model", default="gpt-4.1", help="Model name")
    parser.add_argument(
        "--conditions",
        type=str,
        default=",".join(CONDITIONS),
        help="Comma-separated conditions to run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show setup without running")
    args = parser.parse_args()

    # Auto-detect provider if not specified
    if args.provider is None:
        args.provider = infer_provider(args.model)

    selected_conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]

    print("=" * 70)
    print("AIDER COMPARISON BENCHMARK")
    print("=" * 70)
    print(f"Provider: {args.provider}")
    print(f"Model: {args.model}")
    print(f"Conditions: {', '.join(selected_conditions)}")
    print(f"Repository: {REPO_PATH}")
    print()

    # Load task pack
    print("Loading task pack...")
    task_pack = load_task_pack()
    tasks = task_pack.get("tasks", [])
    print(f"  Tasks: {len(tasks)}")

    # Load Aider maps
    print("Loading Aider repo maps...")
    aider_maps = {}
    for budget in ["1k", "2k", "4k"]:
        try:
            aider_maps[budget] = load_aider_map(budget)
            print(f"  {budget}: {count_tokens(aider_maps[budget])} tokens")
        except FileNotFoundError as e:
            print(f"  {budget}: NOT FOUND")

    # Load entity mapping
    print("Loading entity mapping...")
    try:
        qname_mapping = load_qualified_name_mapping()
        print(f"  Mappings: {len(qname_mapping)}")
    except FileNotFoundError as e:
        print(f"  NOT FOUND: {e}")
        qname_mapping = {}

    # Load module mappings for filtered L3
    print("Loading module mappings...")
    module_id_to_file_path, _ = _build_module_id_file_path_maps(REPO_PATH)
    valid_module_ids = set(module_id_to_file_path.keys())
    print(f"  Modules: {len(valid_module_ids)}")

    if args.dry_run:
        print("\n[DRY RUN] Would run benchmark with above configuration.")
        return

    # Create provider
    print(f"\nInitializing {args.provider}/{args.model}...")
    provider = create_provider(args.provider, args.model)

    # Run conditions
    all_results: Dict[str, List[Dict[str, Any]]] = {}

    for condition in selected_conditions:
        if condition not in CONDITIONS:
            print(f"Unknown condition: {condition}")
            continue

        # Check if Aider map exists for Aider conditions
        if condition.startswith("aider_repomap_"):
            budget = condition.split("_")[-1]
            if budget not in aider_maps:
                print(f"Skipping {condition}: map not found")
                continue

        print(f"\n{'='*70}")
        print(f"Running condition: {condition}")
        print("=" * 70)

        results = run_condition(
            condition=condition,
            tasks=tasks,
            provider=provider,
            repo_path=REPO_PATH,
            qname_mapping=qname_mapping,
            aider_maps=aider_maps,
            module_id_to_file_path=module_id_to_file_path,
            valid_module_ids=valid_module_ids,
        )
        all_results[condition] = results

        # Print condition summary
        metrics = aggregate_metrics(results)
        print(f"\n  Top-1: {metrics['top1_hit_rate']:.1%}")
        print(f"  Top-3: {metrics['top3_hit_rate']:.1%}")
        print(f"  Tokens/task: {metrics['tokens_per_task_input']:.0f}")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model_slug = args.model.replace(".", "_").replace("-", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save full results JSON (timestamped to prevent overwrites)
    results_path = RESULTS_DIR / f"results_{model_slug}_{timestamp}.json"
    output = {
        "model": args.model,
        "provider": args.provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_count": len(tasks),
        "conditions": list(all_results.keys()),
        "condition_results": all_results,
        "condition_metrics": {c: aggregate_metrics(r) for c, r in all_results.items()},
    }
    results_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nResults saved to: {results_path}")

    # Save summary markdown (timestamped to prevent overwrites)
    summary = generate_summary(all_results, args.model)
    summary_path = RESULTS_DIR / f"summary_{model_slug}_{timestamp}.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(f"Summary saved to: {summary_path}")

    # Print final summary
    print("\n" + summary)


if __name__ == "__main__":
    main()
