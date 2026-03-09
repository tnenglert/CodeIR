"""Comparative evaluation: Baseline (raw code) vs L1 vs L3.

Measures:
- Accuracy: How well does the model identify the entity?
- Confidence: Self-reported confidence (1-5)
- Token use: Input tokens for each representation
- Expansion frequency: How often does the model request more context?
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


@dataclass
class AnthropicProvider:
    model: str = "claude-haiku-4-5-20251001"
    _client: Any = None

    MODELS = {
        "haiku-4": "claude-haiku-4-5-20251001",
        "sonnet-4": "claude-sonnet-4-20250514",
    }

    def __post_init__(self):
        import anthropic
        self._client = anthropic.Anthropic()
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]

    def complete(self, prompt: str, system: str = "", max_tokens: int = 600) -> tuple[str, int]:
        """Returns (response_text, input_token_count)."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        input_tokens = response.usage.input_tokens
        return response.content[0].text, input_tokens


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

IDENTIFICATION_PROMPT = """You are given the following representation of a Python entity:

```
{representation}
```

Answer these questions:

1. IDENTIFICATION: What does this entity do? Describe its purpose and key operations in 2-3 sentences.

2. CONFIDENCE: How confident are you in your identification? (1=guessing, 5=certain)

3. NEED_MORE_INFO: Do you need to see more context (source code, related files) to be confident? (yes/no)

Format your response exactly as:
IDENTIFICATION: <your description>
CONFIDENCE: <1-5>
NEED_MORE_INFO: <yes/no>"""

SCORING_PROMPT = """You are evaluating how well an LLM identified a code entity.

Entity information:
- Name: {entity_name}
- Type: {entity_type}
- File: {file}
- Purpose: {reason}

LLM's identification:
{identification}

Score the identification accuracy (1-5):
5: Fully correct — identifies purpose, behavior, and key logic
4: Mostly correct — identifies purpose, minor details missing
3: Partially correct — gets the general area right but misses specifics
2: Vaguely correct — understands it's code but wrong about purpose
1: Incorrect — fundamentally wrong about what the entity does

Reply with ONLY a single number 1-5."""


def parse_response(response: str) -> Dict[str, Any]:
    """Parse structured response."""
    result = {
        "identification": "",
        "confidence": 0,
        "needs_expansion": False,
        "raw_response": response,
    }

    # Extract identification
    match = re.search(r'IDENTIFICATION:\s*(.+?)(?=\nCONFIDENCE:|$)', response, re.S)
    if match:
        result["identification"] = match.group(1).strip()

    # Extract confidence
    match = re.search(r'CONFIDENCE:\s*(\d)', response)
    if match:
        result["confidence"] = int(match.group(1))

    # Extract need_more_info
    match = re.search(r'NEED_MORE_INFO:\s*(yes|no)', response, re.I)
    if match:
        result["needs_expansion"] = match.group(1).lower() == "yes"

    return result


def score_identification(provider: AnthropicProvider, identification: str, answer_key: Dict) -> int:
    """Score an identification using the model."""
    prompt = SCORING_PROMPT.format(
        entity_name=answer_key['entity_name'],
        entity_type=answer_key['entity_type'],
        file=answer_key['file'],
        reason=answer_key['reason'],
        identification=identification,
    )
    response, _ = provider.complete(prompt, max_tokens=10)
    match = re.search(r'[1-5]', response)
    return int(match.group()) if match else 0


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_comparison_tests(
    test_pack_path: Path,
    ir_samples_path: Path,
    output_path: Path,
    model: str = "haiku-4",
    sample_size: Optional[int] = None,
    rate_limit: float = 0.5,
) -> Dict[str, Any]:
    """Run comparison tests across baseline, L1, and L3."""

    if not HAS_ANTHROPIC:
        raise RuntimeError("anthropic package not installed")

    provider = AnthropicProvider(model=model)
    print(f"Model: {provider.model}")
    print()

    with open(test_pack_path) as f:
        test_pack = json.load(f)

    with open(ir_samples_path) as f:
        ir_samples = json.load(f)

    # Get unique entities (not per-level)
    entities = {}
    for test in test_pack['tests']:
        eid = test['entity_id']
        if eid not in entities:
            entities[eid] = {
                'entity_id': eid,
                'entity_name': test['entity_name'],
                'answer_key': test['answer_key'],
            }

    entity_list = list(entities.values())
    if sample_size:
        entity_list = entity_list[:sample_size]

    results = {
        'baseline': [],
        'L1': [],
        'L3': [],
    }

    for i, entity in enumerate(entity_list):
        eid = entity['entity_id']
        name = entity['entity_name']
        answer_key = entity['answer_key']
        ir_data = ir_samples.get(eid, {})

        print(f"[{i+1}/{len(entity_list)}] {name}")

        for condition in ['baseline', 'L1', 'L3']:
            # Get representation
            if condition == 'baseline':
                rep = ir_data.get('levels', {}).get('L0', '')
                # For baseline, we use L0 which is raw source with markers
            elif condition == 'L1':
                rep = ir_data.get('levels', {}).get('L1', '')
            else:  # L3
                rep = ir_data.get('levels', {}).get('L3', '')

            if not rep:
                print(f"  {condition}: No representation available")
                continue

            # Run identification
            prompt = IDENTIFICATION_PROMPT.format(representation=rep)
            try:
                response, input_tokens = provider.complete(prompt)
                parsed = parse_response(response)

                # Score the identification
                accuracy = score_identification(provider, parsed['identification'], answer_key)

                result = {
                    'entity_id': eid,
                    'entity_name': name,
                    'condition': condition,
                    'input_tokens': input_tokens,
                    'accuracy': accuracy,
                    'confidence': parsed['confidence'],
                    'needs_expansion': parsed['needs_expansion'],
                    'identification': parsed['identification'][:200],  # Truncate for storage
                }
                results[condition].append(result)

                print(f"  {condition}: acc={accuracy}/5, conf={parsed['confidence']}/5, "
                      f"expand={parsed['needs_expansion']}, tokens={input_tokens}")

            except Exception as e:
                print(f"  {condition}: Error - {e}")
                results[condition].append({
                    'entity_id': eid,
                    'entity_name': name,
                    'condition': condition,
                    'error': str(e),
                })

            time.sleep(rate_limit)

    # Compute summary statistics
    summary = {}
    for condition in ['baseline', 'L1', 'L3']:
        cond_results = [r for r in results[condition] if 'accuracy' in r]
        if cond_results:
            summary[condition] = {
                'count': len(cond_results),
                'accuracy_mean': sum(r['accuracy'] for r in cond_results) / len(cond_results),
                'accuracy_pass_rate': sum(1 for r in cond_results if r['accuracy'] >= 3) / len(cond_results),
                'confidence_mean': sum(r['confidence'] for r in cond_results) / len(cond_results),
                'expansion_rate': sum(1 for r in cond_results if r['needs_expansion']) / len(cond_results),
                'tokens_mean': sum(r['input_tokens'] for r in cond_results) / len(cond_results),
                'tokens_total': sum(r['input_tokens'] for r in cond_results),
            }

    output = {
        'model': provider.model,
        'entity_count': len(entity_list),
        'summary': summary,
        'results': results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    # Print comparison table
    print("\n" + "="*70)
    print("COMPARISON SUMMARY")
    print("="*70)
    print(f"{'Metric':<20} {'Baseline':>12} {'L1':>12} {'L3':>12}")
    print("-"*70)

    if summary:
        metrics = ['accuracy_mean', 'accuracy_pass_rate', 'confidence_mean', 'expansion_rate', 'tokens_mean']
        labels = ['Accuracy (1-5)', 'Pass Rate', 'Confidence (1-5)', 'Expansion Rate', 'Avg Tokens']

        for metric, label in zip(metrics, labels):
            vals = []
            for cond in ['baseline', 'L1', 'L3']:
                if cond in summary:
                    v = summary[cond].get(metric, 0)
                    if 'rate' in metric.lower():
                        vals.append(f"{v:.1%}")
                    elif 'tokens' in metric.lower():
                        vals.append(f"{v:.0f}")
                    else:
                        vals.append(f"{v:.2f}")
                else:
                    vals.append("N/A")
            print(f"{label:<20} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")

    print("="*70)
    print(f"\nResults saved to: {output_path}")

    return output


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run comparison tests')
    parser.add_argument('--model', default='haiku-4')
    parser.add_argument('--sample', type=int, help='Sample N entities')
    args = parser.parse_args()

    run_comparison_tests(
        test_pack_path=Path('eval/test_packs/identification_tests.json'),
        ir_samples_path=Path('eval/corpus/requests_ir_samples.json'),
        output_path=Path('eval/results/comparison_results.json'),
        model=args.model,
        sample_size=args.sample,
    )
