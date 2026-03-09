"""L0 Identification Tests.

Tests whether the model can accurately describe what an entity does from raw source.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from eval.providers import AnthropicProvider


DEFAULT_SAMPLE_ENTITIES = 5


IDENTIFICATION_PROMPT = """You are given the following Python entity:

```
{l0_token}
```

Describe its purpose, key behavior, and main operations in 2-3 sentences."""

SCORER_PROMPT = """Score how well this identification describes a Python {entity_type}.

Expected purpose: {purpose}

Model's identification:
{identification}

Score 1-5:
5: Fully correct — identifies purpose, behavior, and key logic
4: Mostly correct — identifies purpose, minor details missing
3: Partially correct — gets general area right, misses specifics
2: Vaguely correct — understands it's code but wrong about purpose
1: Incorrect — fundamentally wrong about what the entity does

Reply with ONLY a single number 1-5."""


def _sample_entity_ids_by_type(
    answers: Dict[str, Dict[str, Any]],
    sample_size: Optional[int],
) -> List[str]:
    """Sample entities in a type-balanced way (class/function/method/property)."""
    entity_ids = list(answers.keys())
    if not sample_size or sample_size <= 0:
        return entity_ids

    by_type: Dict[str, List[str]] = {}
    for entity_id in sorted(entity_ids):
        entity_type = str(answers[entity_id].get("entity_type", "unknown"))
        by_type.setdefault(entity_type, []).append(entity_id)

    selected: List[str] = []
    type_order = sorted(by_type.keys())
    while len(selected) < sample_size:
        progressed = False
        for entity_type in type_order:
            bucket = by_type.get(entity_type, [])
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) >= sample_size:
                break
        if not progressed:
            break

    return selected


def run_l0_identification(
    ir_samples_path: Path,
    answer_key_path: Path,
    output_path: Path,
    model: str = "haiku-4",
    sample_size: Optional[int] = None,
    rate_limit: float = 0.3,
) -> Dict[str, Any]:
    """Run L0 identification tests."""

    provider = AnthropicProvider(model=model)
    print(f"Model: {provider.model}")
    print()

    with open(ir_samples_path) as f:
        ir_samples = json.load(f)

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    l0_answers = answer_key["l0_identification"]["answers"]

    # Get entity list
    entity_ids = _sample_entity_ids_by_type(l0_answers, sample_size)

    results = []
    scores = []

    for i, eid in enumerate(entity_ids):
        ir_data = ir_samples.get(eid, {})
        l0_token = ir_data.get("levels", {}).get("L0", "")
        expected = l0_answers[eid]

        if not l0_token:
            print(f"[{i+1}/{len(entity_ids)}] {eid}: No L0 token")
            continue

        print(f"[{i+1}/{len(entity_ids)}] {eid} ({expected['entity_name']})")

        # Get identification
        prompt = IDENTIFICATION_PROMPT.format(l0_token=l0_token)
        try:
            identification, input_tokens = provider.complete(prompt, max_tokens=300)
            time.sleep(rate_limit)

            # Score the identification
            scorer_prompt = SCORER_PROMPT.format(
                entity_type=expected["entity_type"],
                purpose=expected["purpose"],
                identification=identification,
            )
            score_response, _ = provider.complete(scorer_prompt, max_tokens=10)

            # Extract score
            match = re.search(r'[1-5]', score_response)
            score = int(match.group()) if match else 0
            scores.append(score)

            results.append({
                "entity_id": eid,
                "entity_name": expected["entity_name"],
                "l0_token_preview": l0_token[:200] + "..." if len(l0_token) > 200 else l0_token,
                "identification": identification,
                "score": score,
                "pass": score >= 3,
                "input_tokens": input_tokens,
            })

            status = "✓" if score >= 3 else "✗"
            print(f"  {status} score={score}/5 tokens={input_tokens}")

        except Exception as e:
            print(f"  Error - {e}")
            results.append({
                "entity_id": eid,
                "entity_name": expected["entity_name"],
                "error": str(e),
            })

        time.sleep(rate_limit)

    # Summary
    passed = sum(1 for r in results if r.get("pass"))
    total = len([r for r in results if "score" in r])
    mean_score = sum(scores) / len(scores) if scores else 0

    summary = {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0,
        "mean_score": mean_score,
        "score_distribution": {
            str(i): scores.count(i) for i in range(1, 6)
        },
    }

    output = {
        "test_type": "l0_identification",
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
    print(f"Mean score: {mean_score:.2f}/5")
    print(f"Pass rate:  {passed}/{total} ({100*summary['pass_rate']:.1f}%)")
    print(f"Score distribution: {summary['score_distribution']}")
    print(f"\nResults saved to: {output_path}")

    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run L0 identification tests")
    parser.add_argument("--model", default="haiku-4")
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_ENTITIES,
        help=f"Sample N entities (type-balanced, default: {DEFAULT_SAMPLE_ENTITIES}; use 0 for full set)",
    )
    parser.add_argument("--rate-limit", type=float, default=0.3)
    args = parser.parse_args()

    run_l0_identification(
        ir_samples_path=Path("eval/corpus/requests_ir_samples.json"),
        answer_key_path=Path("eval/test_packs/answer_key.json"),
        output_path=Path(f"eval/results/l0_identification_{args.model.replace('-', '_')}.json"),
        model=args.model,
        sample_size=args.sample,
        rate_limit=args.rate_limit,
    )
