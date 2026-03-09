"""Run L3 orientation tests.

Orientation tests verify that L3 tokens provide enough information to
identify domain and component - the actual job of L3 compression.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# Domain answer mapping
DOMAIN_MAP = {
    'A': 'HTTP/networking',
    'B': 'authentication',
    'C': 'data encoding',
    'D': 'error handling',
    'E': 'cryptography',
    'F': 'file I/O',
}

# Component answer mapping
COMPONENT_MAP = {
    'A': 'request handling',
    'B': 'response handling',
    'C': 'session management',
    'D': 'connection management',
    'E': 'utility/helper',
}


@dataclass
class AnthropicProvider:
    """Anthropic Claude provider."""
    model: str = "claude-haiku-4-5-20251001"
    _client: Any = None

    MODELS = {
        "haiku-3": "claude-3-haiku-20240307",
        "sonnet-3": "claude-3-sonnet-20240229",
        "opus-3": "claude-3-opus-20240229",
        "sonnet-3.5": "claude-3-5-sonnet-20241022",
        "haiku-3.5": "claude-3-5-haiku-20241022",
        "haiku-4": "claude-haiku-4-5-20251001",
        "sonnet-4": "claude-sonnet-4-20250514",
        "opus-4": "claude-opus-4-20250514",
    }

    def __post_init__(self):
        import anthropic
        self._client = anthropic.Anthropic()
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]

    def complete(self, prompt: str, max_tokens: int = 100, system: str = "") -> str:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        return response.content[0].text

    @property
    def model_name(self) -> str:
        return self.model


def extract_answer(response: str) -> Optional[str]:
    """Extract single letter answer from response.

    Only accepts clean, unambiguous answers. Returns None if the response
    doesn't clearly indicate a single letter choice. False negatives are
    preferable to false positives.
    """
    response = response.strip().upper()

    # Direct single letter answer (ideal case with system prompt)
    if len(response) == 1 and response in 'ABCDEF':
        return response

    # "A)" or "A." or "A:" at start of response
    match = re.match(r'^([A-F])[).:\s]', response)
    if match:
        return match.group(1)

    # "Answer: A" or "Option: A" pattern
    match = re.search(r'(?:answer|option)[:\s]*([A-F])\b', response, re.I)
    if match:
        return match.group(1).upper()

    # No fallback to first character - that causes false positives
    # when models start with "Based", "Certainly", etc.
    return None


def score_orientation(response: str, test: Dict[str, Any]) -> tuple[int, str]:
    """Score an orientation test response.

    Returns (score, explanation).
    """
    answer = extract_answer(response)
    if not answer:
        return 0, f"Could not extract answer from: {response[:50]}"

    sub_type = test.get('sub_type', 'domain')
    answer_key = test['answer_key']

    if sub_type == 'domain':
        expected = answer_key['expected_domain']
        answer_map = DOMAIN_MAP
    else:
        expected = answer_key['expected_component']
        answer_map = COMPONENT_MAP

    given = answer_map.get(answer, 'unknown')

    if given.lower() == expected.lower():
        return 1, f"Correct: {answer} = {given}"
    else:
        return 0, f"Wrong: {answer} ({given}) != {expected}"


def run_orientation_tests(
    test_pack_path: Path,
    output_path: Path,
    model: str = "haiku-4",
    sample_size: Optional[int] = None,
    rate_limit: float = 0.3,
) -> Dict[str, Any]:
    """Run orientation tests."""
    if not HAS_ANTHROPIC:
        raise RuntimeError("anthropic package not installed")

    provider = AnthropicProvider(model=model)
    print(f"Model: {provider.model_name}")
    print()

    with open(test_pack_path) as f:
        test_pack = json.load(f)

    tests = test_pack['tests']

    # Sample if requested
    if sample_size:
        by_type: Dict[str, List] = {}
        for t in tests:
            by_type.setdefault(t.get('sub_type', 'domain'), []).append(t)
        tests = []
        for type_tests in by_type.values():
            tests.extend(type_tests[:sample_size])

    results: List[Dict[str, Any]] = []

    # System prompt for clean single-letter responses
    system_prompt = "Respond with a single letter only. No explanation."

    for i, test in enumerate(tests):
        sub_type = test.get('sub_type', 'domain')
        print(f"[{i+1}/{len(tests)}] {test['test_id']} ({sub_type}): {test['entity_name']}")

        try:
            llm_response = provider.complete(test['prompt'], max_tokens=10, system=system_prompt)
        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                'test_id': test['test_id'],
                'sub_type': sub_type,
                'entity_id': test['entity_id'],
                'score': 0,
                'error': str(e),
            })
            time.sleep(rate_limit)
            continue

        score, explanation = score_orientation(llm_response, test)
        print(f"  {explanation}")

        results.append({
            'test_id': test['test_id'],
            'sub_type': sub_type,
            'entity_id': test['entity_id'],
            'entity_name': test['entity_name'],
            'response': llm_response.strip(),
            'score': score,
            'explanation': explanation,
        })

        time.sleep(rate_limit)

    # Aggregate by sub_type
    by_type: Dict[str, List[int]] = {}
    for r in results:
        by_type.setdefault(r['sub_type'], []).append(r['score'])

    summary = {
        'total_tests': len(results),
        'model': provider.model_name,
        'by_type': {},
    }

    for sub_type, scores in sorted(by_type.items()):
        summary['by_type'][sub_type] = {
            'count': len(scores),
            'correct': sum(scores),
            'accuracy': sum(scores) / len(scores) if scores else 0,
        }

    # Overall
    all_scores = [r['score'] for r in results]
    summary['overall'] = {
        'total': len(all_scores),
        'correct': sum(all_scores),
        'accuracy': sum(all_scores) / len(all_scores) if all_scores else 0,
    }

    output = {
        'test_type': 'orientation',
        'model': provider.model_name,
        'summary': summary,
        'results': results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {output_path}")
    print("\nSummary:")
    for sub_type, stats in summary['by_type'].items():
        print(f"  {sub_type}: {stats['correct']}/{stats['count']} ({stats['accuracy']:.1%})")
    print(f"  OVERALL: {summary['overall']['correct']}/{summary['overall']['total']} ({summary['overall']['accuracy']:.1%})")

    return output


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run L3 orientation tests')
    parser.add_argument('--model', default='haiku-4', help='Model to test')
    parser.add_argument('--sample', type=int, help='Sample N tests per type')
    args = parser.parse_args()

    run_orientation_tests(
        test_pack_path=Path('eval/test_packs/orientation_tests.json'),
        output_path=Path(f'eval/results/orientation_{args.model.replace("-", "_")}.json'),
        model=args.model,
        sample_size=args.sample,
    )
