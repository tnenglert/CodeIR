"""Run comprehensibility floor tests using LLM evaluation.

Multi-model test harness for evaluating CodeIR compression levels.
Tests how well different LLMs understand compressed code representations.

Supported providers:
  - Anthropic: claude-3-haiku, claude-3-sonnet, claude-3-opus, claude-3.5-sonnet, claude-sonnet-4
  - OpenAI: (planned)
  - Google Gemini: (planned)
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

class LLMProvider(Protocol):
    """Protocol for LLM providers."""
    def complete(self, prompt: str, max_tokens: int = 500) -> str: ...
    @property
    def model_name(self) -> str: ...


@dataclass
class AnthropicProvider:
    """Anthropic Claude provider."""
    model: str = "claude-sonnet-4-20250514"
    _client: Any = None

    MODELS = {
        # Claude 3 family
        "haiku-3": "claude-3-haiku-20240307",
        "sonnet-3": "claude-3-sonnet-20240229",
        "opus-3": "claude-3-opus-20240229",
        # Claude 3.5 family
        "sonnet-3.5": "claude-3-5-sonnet-20241022",
        "haiku-3.5": "claude-3-5-haiku-20241022",
        # Claude 4 family (current)
        "haiku-4": "claude-haiku-4-5-20251001",
        "sonnet-4": "claude-sonnet-4-20250514",
        "opus-4": "claude-opus-4-20250514",
    }

    def __post_init__(self):
        import anthropic
        self._client = anthropic.Anthropic()
        # Resolve short name to full model ID
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    @property
    def model_name(self) -> str:
        return self.model


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

SCORE_PROMPT = """You are evaluating how well an LLM understood a code entity from its compressed representation.

Entity information:
- Name: {entity_name}
- Type: {entity_type}
- File: {file}
- Purpose: {reason}

LLM Response:
{response}

{rubric}

Based on this rubric, what score (1-5) does this response deserve?
Reply with ONLY a single number 1-5."""


def score_response(
    provider: LLMProvider,
    response: str,
    answer_key: Dict[str, Any],
    rubric: str,
) -> int:
    """Score an LLM response using the same or different model."""
    prompt = SCORE_PROMPT.format(
        entity_name=answer_key['entity_name'],
        entity_type=answer_key['entity_type'],
        file=answer_key['file'],
        reason=answer_key['reason'],
        response=response,
        rubric=rubric,
    )
    try:
        score_text = provider.complete(prompt, max_tokens=10).strip()
        match = re.search(r'[1-5]', score_text)
        return int(match.group()) if match else 0
    except Exception as e:
        print(f"  Scoring error: {e}")
        return 0


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_identification_tests(
    test_pack_path: Path,
    output_path: Path,
    model: str = "sonnet-4",
    scorer_model: Optional[str] = None,
    levels: Optional[List[str]] = None,
    sample_size: Optional[int] = None,
    rate_limit: float = 0.5,
) -> Dict[str, Any]:
    """Run identification tests with specified model.

    Args:
        test_pack_path: Path to the test pack JSON
        output_path: Path to write results
        model: Model to test (short name or full ID)
        scorer_model: Model for scoring (defaults to same as test model)
        levels: Which levels to test (default: all)
        sample_size: Limit tests per level (for quick runs)
        rate_limit: Seconds between API calls

    Returns:
        Results dict with per-level scores
    """
    provider = AnthropicProvider(model=model)
    scorer = AnthropicProvider(model=scorer_model or model)

    print(f"Test model: {provider.model_name}")
    print(f"Scorer model: {scorer.model_name}")
    print()

    with open(test_pack_path) as f:
        test_pack = json.load(f)

    tests = test_pack['tests']
    if levels:
        tests = [t for t in tests if t['level'] in levels]

    # Sample if requested
    if sample_size:
        by_level: Dict[str, List] = {}
        for t in tests:
            by_level.setdefault(t['level'], []).append(t)
        tests = []
        for level_tests in by_level.values():
            tests.extend(level_tests[:sample_size])

    results: List[Dict[str, Any]] = []

    for i, test in enumerate(tests):
        print(f"[{i+1}/{len(tests)}] {test['test_id']} ({test['level']}): {test['entity_name']}")

        # Get LLM response to the identification prompt
        try:
            llm_response = provider.complete(test['prompt'])
        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                'test_id': test['test_id'],
                'level': test['level'],
                'entity_id': test['entity_id'],
                'score': 0,
                'error': str(e),
            })
            time.sleep(rate_limit)
            continue

        # Score the response
        score = score_response(scorer, llm_response, test['answer_key'], test['rubric'])
        print(f"  Score: {score}/5")

        results.append({
            'test_id': test['test_id'],
            'level': test['level'],
            'entity_id': test['entity_id'],
            'entity_name': test['entity_name'],
            'response': llm_response,
            'score': score,
        })

        time.sleep(rate_limit)

    # Aggregate by level
    by_level_scores: Dict[str, List[int]] = {}
    for r in results:
        by_level_scores.setdefault(r['level'], []).append(r['score'])

    summary = {
        'total_tests': len(results),
        'model': provider.model_name,
        'scorer_model': scorer.model_name,
        'by_level': {},
    }

    for level, scores in sorted(by_level_scores.items()):
        valid_scores = [s for s in scores if s > 0]
        summary['by_level'][level] = {
            'count': len(scores),
            'mean_score': sum(valid_scores) / len(valid_scores) if valid_scores else 0,
            'pass_rate': sum(1 for s in valid_scores if s >= 3) / len(valid_scores) if valid_scores else 0,
            'scores': scores,
        }

    output = {
        'test_type': 'identification',
        'model': provider.model_name,
        'scorer_model': scorer.model_name,
        'summary': summary,
        'results': results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {output_path}")
    print("\nSummary:")
    for level, stats in summary['by_level'].items():
        print(f"  {level}: mean={stats['mean_score']:.2f}, pass_rate={stats['pass_rate']:.1%}")

    return output


def run_multi_model_tests(
    test_pack_path: Path,
    output_dir: Path,
    models: List[str],
    scorer_model: str = "sonnet-4",
    levels: Optional[List[str]] = None,
    sample_size: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run tests across multiple models for comparison.

    Args:
        test_pack_path: Path to the test pack JSON
        output_dir: Directory for results
        models: List of models to test
        scorer_model: Consistent scorer for fair comparison
        levels: Which levels to test
        sample_size: Limit tests per level

    Returns:
        Dict mapping model -> results
    """
    all_results = {}

    for model in models:
        print(f"\n{'='*60}")
        print(f"Testing model: {model}")
        print('='*60 + "\n")

        output_path = output_dir / f"identification_{model.replace('-', '_')}.json"

        try:
            results = run_identification_tests(
                test_pack_path=test_pack_path,
                output_path=output_path,
                model=model,
                scorer_model=scorer_model,
                levels=levels,
                sample_size=sample_size,
            )
            all_results[model] = results
        except Exception as e:
            print(f"Error testing {model}: {e}")
            all_results[model] = {'error': str(e)}

    # Print comparison summary
    print(f"\n{'='*60}")
    print("MULTI-MODEL COMPARISON")
    print('='*60)

    levels_seen = set()
    for r in all_results.values():
        if 'summary' in r:
            levels_seen.update(r['summary']['by_level'].keys())

    for level in sorted(levels_seen):
        print(f"\n{level}:")
        for model, result in all_results.items():
            if 'summary' in result:
                stats = result['summary']['by_level'].get(level, {})
                mean = stats.get('mean_score', 0)
                rate = stats.get('pass_rate', 0)
                print(f"  {model:20s}: mean={mean:.2f}, pass={rate:.1%}")
            else:
                print(f"  {model:20s}: ERROR")

    # Save comparison summary
    comparison_path = output_dir / "comparison_summary.json"
    comparison = {
        'models': models,
        'scorer_model': scorer_model,
        'results': {
            model: result.get('summary', {'error': result.get('error')})
            for model, result in all_results.items()
        }
    }
    with open(comparison_path, 'w') as f:
        json.dump(comparison, f, indent=2)
    print(f"\nComparison saved to: {comparison_path}")

    return all_results


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run floor tests')
    parser.add_argument('--model', default='sonnet-4', help='Model to test')
    parser.add_argument('--scorer', default=None, help='Model for scoring')
    parser.add_argument('--levels', nargs='+', default=['L0', 'L1', 'L3'])
    parser.add_argument('--sample', type=int, help='Sample N tests per level')
    parser.add_argument('--multi', nargs='+', help='Test multiple models')
    args = parser.parse_args()

    test_pack = Path('eval/test_packs/identification_tests.json')
    output_dir = Path('eval/results')

    if args.multi:
        run_multi_model_tests(
            test_pack_path=test_pack,
            output_dir=output_dir,
            models=args.multi,
            scorer_model=args.scorer or 'sonnet-4',
            levels=args.levels,
            sample_size=args.sample,
        )
    else:
        run_identification_tests(
            test_pack_path=test_pack,
            output_path=output_dir / f"identification_{args.model.replace('-', '_')}.json",
            model=args.model,
            scorer_model=args.scorer,
            levels=args.levels,
            sample_size=args.sample,
        )
