"""L1 Navigation Tests.

Tests whether the model can correctly determine if an entity is relevant to a task.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from eval.providers import AnthropicProvider


DEFAULT_SAMPLE_ENTITIES = 5


DEFAULT_L1_PREAMBLE = """CodeIR is a compressed representation of Python code entities. Notation guide:

Entity types: MT=method, FN=function, CLS=class
Entity IDs: STEM or STEM.XX (e.g., AUTH, RDTKN.03). Full stable ID = TYPE.STEM.SUFFIX
Fields:
  C= semantic references (calls and class inheritance refs), comma-separated
  F= behavioral flags:
     E=raises, I=conditionals, L=loops, T=try/except, W=with-context, A=await, R=returns, X=exception-type class
  A= assignment density (count of assignment operations)
  B= class base references (for non-classes this may be "-")
Optional tags:
  #HTTP/#AUTH/#PARSE/... = module domain
  #CORE/#EXCE/... = module category

Example: CLS TMT C=RequestException F=X B=RequestException #HTTP #EXCE
Means: an exception-like class with timeout semantics, inheriting from RequestException,
in HTTP domain and exception/error category."""
PREAMBLE_PATH = Path(__file__).resolve().parent.parent / "preambles" / "l1_preamble.md"


def _load_l1_preamble() -> str:
    """Load canonical L1 preamble from disk with a backward-compatible fallback."""
    if PREAMBLE_PATH.exists():
        return PREAMBLE_PATH.read_text(encoding="utf-8").strip()
    return DEFAULT_L1_PREAMBLE

QUESTION_TEMPLATE = """{preamble}

Given this L1 token:
{l1_token}

Is this entity likely involved in {task_description}?"""


def _entity_kind(entity_id: str) -> str:
    return entity_id.split("_", 1)[0] if "_" in entity_id else "UNK"


def _sample_tests_by_entity_kind(
    tests: List[Dict[str, Any]],
    sample_size: Optional[int],
) -> List[Dict[str, Any]]:
    """Sample entities in a kind-balanced way, preserving paired yes/no cases."""
    if not sample_size or sample_size <= 0:
        return tests

    by_entity: Dict[str, List[Dict[str, Any]]] = {}
    for test in tests:
        by_entity.setdefault(test["entity_id"], []).append(test)

    by_kind: Dict[str, List[str]] = {}
    for entity_id in sorted(by_entity.keys()):
        by_kind.setdefault(_entity_kind(entity_id), []).append(entity_id)

    selected_entities: List[str] = []
    kind_order = sorted(by_kind.keys())
    while len(selected_entities) < sample_size:
        progressed = False
        for kind in kind_order:
            bucket = by_kind.get(kind, [])
            if not bucket:
                continue
            selected_entities.append(bucket.pop(0))
            progressed = True
            if len(selected_entities) >= sample_size:
                break
        if not progressed:
            break

    selected = set(selected_entities)
    return [test for test in tests if test["entity_id"] in selected]


def extract_answer(response: str) -> Optional[str]:
    """Extract yes/no answer. Returns None if not cleanly extractable."""
    response = response.strip().lower()
    if response in ("yes", "no"):
        return response
    # Check for "yes." or "no," etc
    if response.startswith("yes"):
        return "yes"
    if response.startswith("no"):
        return "no"
    return None


def run_l1_navigation(
    ir_samples_path: Path,
    answer_key_path: Path,
    output_path: Path,
    model: str = "haiku-4",
    sample_size: Optional[int] = None,
    rate_limit: float = 0.3,
) -> Dict[str, Any]:
    """Run L1 navigation tests."""

    provider = AnthropicProvider(model=model)
    l1_preamble = _load_l1_preamble()
    print(f"Model: {provider.model}")
    print()

    with open(ir_samples_path) as f:
        ir_samples = json.load(f)

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    tests = answer_key["l1_navigation"]["tests"]
    tests = _sample_tests_by_entity_kind(tests, sample_size)

    results = []
    yes_correct = 0
    yes_total = 0
    no_correct = 0
    no_total = 0

    for i, test in enumerate(tests):
        eid = test["entity_id"]
        task = test["task"]
        expected = test["expected"]

        ir_data = ir_samples.get(eid, {})
        l1_token = ir_data.get("levels", {}).get("L1", "")

        if not l1_token:
            print(f"[{i+1}/{len(tests)}] {eid}: No L1 token")
            continue

        prompt = QUESTION_TEMPLATE.format(
            preamble=l1_preamble,
            l1_token=l1_token,
            task_description=task,
        )

        try:
            response, tokens = provider.complete(
                prompt,
                system="Answer yes or no only.",
                max_tokens=10,
            )
            answer = extract_answer(response)
            is_correct = answer == expected

            if expected == "yes":
                yes_total += 1
                if is_correct:
                    yes_correct += 1
            else:
                no_total += 1
                if is_correct:
                    no_correct += 1

            results.append({
                "entity_id": eid,
                "task": task,
                "l1_token": l1_token,
                "expected": expected,
                "got": answer,
                "raw_response": response.strip(),
                "correct": is_correct,
                "tokens": tokens,
            })

            status = "✓" if is_correct else "✗"
            print(f"[{i+1}/{len(tests)}] {status} {eid}: {task[:40]}... exp={expected} got={answer}")

        except Exception as e:
            print(f"[{i+1}/{len(tests)}] {eid}: Error - {e}")
            results.append({
                "entity_id": eid,
                "task": task,
                "error": str(e),
            })

        time.sleep(rate_limit)

    # Summary
    total = yes_total + no_total
    correct = yes_correct + no_correct
    summary = {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0,
        "yes_cases": {
            "total": yes_total,
            "correct": yes_correct,
            "accuracy": yes_correct / yes_total if yes_total else 0,
        },
        "no_cases": {
            "total": no_total,
            "correct": no_correct,
            "accuracy": no_correct / no_total if no_total else 0,
        },
    }

    output = {
        "test_type": "l1_navigation",
        "model": provider.model,
        "summary": summary,
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Yes cases: {yes_correct}/{yes_total} ({100*summary['yes_cases']['accuracy']:.1f}%)")
    print(f"No cases:  {no_correct}/{no_total} ({100*summary['no_cases']['accuracy']:.1f}%)")
    print(f"Overall:   {correct}/{total} ({100*summary['accuracy']:.1f}%)")
    print(f"\nResults saved to: {output_path}")

    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run L1 navigation tests")
    parser.add_argument("--model", default="haiku-4")
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_ENTITIES,
        help=f"Sample N entities (kind-balanced, preserves yes/no pairs, default: {DEFAULT_SAMPLE_ENTITIES}; use 0 for full set)",
    )
    parser.add_argument("--rate-limit", type=float, default=0.3)
    args = parser.parse_args()

    run_l1_navigation(
        ir_samples_path=Path("eval/corpus/requests_ir_samples.json"),
        answer_key_path=Path("eval/test_packs/answer_key.json"),
        output_path=Path(f"eval/results/l1_navigation_{args.model.replace('-', '_')}.json"),
        model=args.model,
        sample_size=args.sample,
        rate_limit=args.rate_limit,
    )
