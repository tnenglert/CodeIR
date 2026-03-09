"""Build a deterministic small task pack with required edge-case strata."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set


def _load_available_entity_ids(paths: Sequence[Path]) -> Set[str]:
    ids: Set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # Format: {documents:[{entity_id:...}]}
            docs = data.get("documents")
            if isinstance(docs, list):
                for doc in docs:
                    if isinstance(doc, dict) and "entity_id" in doc:
                        ids.add(str(doc["entity_id"]))
                continue

            # Format: {ENTITY_ID: {"levels": {...}}, ...}
            if data and all(isinstance(v, dict) and "levels" in v for v in data.values()):
                ids.update(str(k) for k in data.keys())
                continue

            # Generic fallback: accept dict entries that look like entity IDs.
            # New format: STEM or STEM.XX (all uppercase, optional .NN suffix)
            # Old format: TYPE_STEM or TYPE_STEM_XX
            for key in data.keys():
                if isinstance(key, str) and key.isupper():
                    ids.add(key)
                elif isinstance(key, str) and "." in key and key.replace(".", "").isupper():
                    ids.add(key)
                elif isinstance(key, str) and "_" in key and key.split("_", 1)[0].isupper():
                    ids.add(key)
    return ids


def _phase_label(task_count: int) -> tuple[str, str]:
    if task_count <= 8:
        return (
            "A",
            "Harness validation only. This sample size does not support product efficacy conclusions.",
        )
    if task_count <= 24:
        return (
            "B",
            "Preliminary efficacy signal only.",
        )
    return ("C", "Decision-quality evidence tier.")


def build_small_stratified_pack(
    manifest_path: Path,
    source_paths: Sequence[Path],
    output_path: Path,
    seed: int = 42,
    strict: bool = True,
    target_count_override: int | None = None,
) -> Dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_strata = list(manifest.get("required_strata", []))
    supplemental = list(manifest.get("supplemental_tasks", []))
    target_count = int(target_count_override or manifest.get("target_task_count", 8))

    available_ids = _load_available_entity_ids(source_paths)
    rng = random.Random(seed)

    tasks: List[Dict[str, Any]] = []
    missing_strata: List[str] = []

    for idx, stratum in enumerate(required_strata, start=1):
        name = str(stratum.get("name", f"stratum_{idx}"))
        candidates = [str(c) for c in stratum.get("candidates", [])]
        present = [c for c in candidates if c in available_ids]

        if not present:
            missing_strata.append(name)
            if strict:
                continue
            else:
                continue

        chosen = rng.choice(sorted(present))
        row = {
            "task_id": f"STR{idx:03d}",
            "query": str(stratum.get("task_query", name)),
            "ground_truth_entity_ids": [chosen],
            "difficulty": "edge",
            "domain": "mixed",
            "requires_write_safety": False,
            "stratum": name,
        }
        if "cross_module" in stratum:
            row["cross_module"] = bool(stratum.get("cross_module"))
        if "trick" in stratum:
            row["trick"] = bool(stratum.get("trick"))
        if "note" in stratum:
            row["note"] = str(stratum.get("note"))
        tasks.append(row)

    if strict and missing_strata:
        missing_text = ", ".join(missing_strata)
        raise ValueError(
            f"Missing required strata in provided sources: {missing_text}. "
            "Provide additional source corpora containing those entity IDs."
        )

    rng.shuffle(supplemental)
    sup_idx = 1
    for item in supplemental:
        if len(tasks) >= target_count:
            break
        row = {
            "task_id": f"SUP{sup_idx:03d}",
            "query": str(item.get("query", "")),
            "ground_truth_entity_ids": [str(x) for x in item.get("ground_truth_entity_ids", [])],
            "difficulty": str(item.get("difficulty", "medium")),
            "domain": str(item.get("domain", "mixed")),
            "requires_write_safety": bool(item.get("requires_write_safety", False)),
        }
        if "cross_module" in item:
            row["cross_module"] = bool(item.get("cross_module"))
        if "trick" in item:
            row["trick"] = bool(item.get("trick"))
        if "note" in item:
            row["note"] = str(item.get("note"))
        tasks.append(row)
        sup_idx += 1

    phase, phase_note = _phase_label(len(tasks))
    payload = {
        "pack_version": "task_benchmark.v1",
        "source_manifest": str(manifest_path),
        "seed": int(seed),
        "target_task_count": int(target_count),
        "phase": phase,
        "phase_note": phase_note,
        "l2_scope_note": "L2 is excluded intentionally to avoid conflating harness bugs with unresolved IR-design questions.",
        "defaults": {
            "top_k": 5,
            "max_expansions": 1,
        },
        "strata_required": [str(s.get("name", "")) for s in required_strata],
        "tasks": tasks,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic small stratified benchmark pack")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("eval/test_packs/sampling_manifest_v1.json"),
    )
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        default=[Path("eval/corpus/requests_ir_samples.json")],
        help="Source JSON files used to resolve available entity IDs. Can be provided multiple times.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/test_packs/task_benchmark_small.json"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--target-count",
        type=int,
        default=None,
        help="Override manifest target task count for this run.",
    )
    parser.add_argument(
        "--non-strict",
        action="store_true",
        help="Allow pack generation even if required strata are missing.",
    )
    args = parser.parse_args()

    payload = build_small_stratified_pack(
        manifest_path=args.manifest,
        source_paths=args.source,
        output_path=args.output,
        seed=args.seed,
        strict=not args.non_strict,
        target_count_override=args.target_count,
    )
    print(f"Generated {len(payload['tasks'])} tasks at {args.output}")


if __name__ == "__main__":
    main()
