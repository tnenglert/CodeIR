"""L3 Orientation Tests.

Tests whether the model can correctly identify domain and category from L3 tokens.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from eval.providers import create_provider, select_provider_interactive, PROVIDERS


DEFAULT_SAMPLE_ENTITIES = 5


DEFAULT_L3_PREAMBLE = """CodeIR L3 tokens use this format:
TYPE ENTITY_ID [#DOMAIN] #CATEGORY

Domain tags: #HTTP = HTTP/networking, #AUTH = Authentication
Category tags: #CORE = Core logic, #EXCE = Error/exception handling"""
PREAMBLE_PATH = Path(__file__).resolve().parent.parent / "preambles" / "l3_preamble.md"


def _load_l3_preamble() -> str:
    """Load canonical L3 preamble from disk with a backward-compatible fallback."""
    if PREAMBLE_PATH.exists():
        return PREAMBLE_PATH.read_text(encoding="utf-8").strip()
    return DEFAULT_L3_PREAMBLE

# Valid answers based on current L3 tag vocabulary
VALID_DOMAINS = {"A", "B"}  # A=HTTP, B=AUTH
VALID_CATEGORIES = {"A", "B"}  # A=CORE, B=EXCE

DOMAIN_TEMPLATE = """{preamble}

Given this L3 token:
{l3_token}

Which domain does this entity belong to?
A) HTTP/networking
B) Authentication

Answer with just the letter."""


def _sample_entity_ids_by_answer_pair(
    answers: Dict[str, Dict[str, str]],
    sample_size: Optional[int],
) -> List[str]:
    """Sample entities with balanced (domain, category) coverage."""
    entity_ids = list(answers.keys())
    if not sample_size or sample_size <= 0:
        return entity_ids

    buckets: Dict[str, List[str]] = {}
    for entity_id in sorted(entity_ids):
        answer = answers[entity_id]
        pair = f"{answer.get('domain', '?')}{answer.get('category', '?')}"
        buckets.setdefault(pair, []).append(entity_id)

    selected: List[str] = []
    pair_order = sorted(buckets.keys())
    while len(selected) < sample_size:
        progressed = False
        for pair in pair_order:
            bucket = buckets.get(pair, [])
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) >= sample_size:
                break
        if not progressed:
            break

    return selected

CATEGORY_TEMPLATE = """{preamble}

Given this L3 token:
{l3_token}

What category does this entity belong to?
A) Core logic
B) Error/exception handling

Answer with just the letter."""


def extract_answer(response: str) -> Optional[str]:
    """Extract single letter answer (A or B only). Returns None if not cleanly extractable."""
    response = response.strip().upper()
    if len(response) == 1 and response in 'AB':
        return response
    # Check for "A)" or "A." pattern at start
    if len(response) >= 2 and response[0] in 'AB' and response[1] in ').:':
        return response[0]
    return None


def run_l3_orientation(
    ir_samples_path: Path,
    answer_key_path: Path,
    output_path: Path,
    provider_name: str = "anthropic",
    model: str = "haiku-4",
    sample_size: Optional[int] = None,
    rate_limit: float = 0.3,
) -> Dict[str, Any]:
    """Run L3 orientation tests."""

    provider = create_provider(provider_name, model)
    l3_preamble = _load_l3_preamble()
    print(f"Model: {provider.model}")
    print()

    with open(ir_samples_path) as f:
        ir_samples = json.load(f)

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    l3_answers = answer_key["l3_orientation"]["answers"]

    # Get entity list
    entity_ids = _sample_entity_ids_by_answer_pair(l3_answers, sample_size)

    results = []
    domain_correct = 0
    domain_total = 0
    category_correct = 0
    category_total = 0

    skipped = 0
    for i, eid in enumerate(entity_ids):
        ir_data = ir_samples.get(eid, {})
        l3_token = ir_data.get("levels", {}).get("L3", "")
        expected = l3_answers[eid]

        if not l3_token:
            print(f"[{i+1}/{len(entity_ids)}] {eid}: No L3 token")
            continue

        # Skip entities whose expected answers aren't in our limited option set
        if expected["domain"] not in VALID_DOMAINS or expected["category"] not in VALID_CATEGORIES:
            print(f"[{i+1}/{len(entity_ids)}] {eid}: Skipped (answer not in option set)")
            skipped += 1
            continue

        print(f"[{i+1}/{len(entity_ids)}] {eid}")

        # Domain question
        prompt = DOMAIN_TEMPLATE.format(preamble=l3_preamble, l3_token=l3_token)
        try:
            response, tokens = provider.complete(
                prompt,
                system="Respond with a single letter only. No explanation.",
                max_tokens=10,
            )
            answer = extract_answer(response)
            is_correct = answer == expected["domain"]
            domain_total += 1
            if is_correct:
                domain_correct += 1

            results.append({
                "entity_id": eid,
                "question_type": "domain",
                "l3_token": l3_token,
                "expected": expected["domain"],
                "got": answer,
                "raw_response": response.strip(),
                "correct": is_correct,
                "tokens": tokens,
            })
            status = "✓" if is_correct else "✗"
            print(f"  domain: {status} expected={expected['domain']} got={answer}")

        except Exception as e:
            print(f"  domain: Error - {e}")
            results.append({
                "entity_id": eid,
                "question_type": "domain",
                "error": str(e),
            })

        time.sleep(rate_limit)

        # Category question
        prompt = CATEGORY_TEMPLATE.format(preamble=l3_preamble, l3_token=l3_token)
        try:
            response, tokens = provider.complete(
                prompt,
                system="Respond with a single letter only. No explanation.",
                max_tokens=10,
            )
            answer = extract_answer(response)
            is_correct = answer == expected["category"]
            category_total += 1
            if is_correct:
                category_correct += 1

            results.append({
                "entity_id": eid,
                "question_type": "category",
                "l3_token": l3_token,
                "expected": expected["category"],
                "got": answer,
                "raw_response": response.strip(),
                "correct": is_correct,
                "tokens": tokens,
            })
            status = "✓" if is_correct else "✗"
            print(f"  category: {status} expected={expected['category']} got={answer}")

        except Exception as e:
            print(f"  category: Error - {e}")
            results.append({
                "entity_id": eid,
                "question_type": "category",
                "error": str(e),
            })

        time.sleep(rate_limit)

    # Summary
    summary = {
        "total": len(results),
        "skipped": skipped,
        "domain": {
            "total": domain_total,
            "correct": domain_correct,
            "accuracy": domain_correct / domain_total if domain_total else 0,
        },
        "category": {
            "total": category_total,
            "correct": category_correct,
            "accuracy": category_correct / category_total if category_total else 0,
        },
        "overall": {
            "total": domain_total + category_total,
            "correct": domain_correct + category_correct,
            "accuracy": (domain_correct + category_correct) / (domain_total + category_total) if (domain_total + category_total) else 0,
        },
    }

    output = {
        "test_type": "l3_orientation",
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
    if skipped:
        print(f"Skipped:  {skipped} (answer not in limited option set)")
    print(f"Domain:   {domain_correct}/{domain_total} ({100*summary['domain']['accuracy']:.1f}%)")
    print(f"Category: {category_correct}/{category_total} ({100*summary['category']['accuracy']:.1f}%)")
    print(f"Overall:  {summary['overall']['correct']}/{summary['overall']['total']} ({100*summary['overall']['accuracy']:.1f}%)")
    print(f"\nResults saved to: {output_path}")

    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run L3 orientation tests")
    parser.add_argument(
        "--provider",
        choices=list(PROVIDERS.keys()),
        help="LLM provider (if not set, will prompt interactively)",
    )
    parser.add_argument("--model", help="Model name (uses provider default if not set)")
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_ENTITIES,
        help=f"Sample N entities (balanced by domain/category label pair, default: {DEFAULT_SAMPLE_ENTITIES}; use 0 for full set)",
    )
    parser.add_argument("--rate-limit", type=float, default=0.3)
    args = parser.parse_args()

    # Interactive provider/model selection if not specified
    if args.provider:
        provider_name = args.provider
        model = args.model or PROVIDERS[provider_name]["default"]
    else:
        provider_name, model = select_provider_interactive()
        if args.model:
            model = args.model

    run_l3_orientation(
        ir_samples_path=Path(__file__).parent.parent / "corpus" / "requests_ir_samples.json",
        answer_key_path=Path(__file__).parent.parent / "test_packs" / "answer_key.json",
        output_path=Path(__file__).parent.parent / "results" / f"l3_orientation_{provider_name}_{model.replace('-', '_')}.json",
        provider_name=provider_name,
        model=model,
        sample_size=args.sample,
        rate_limit=args.rate_limit,
    )
