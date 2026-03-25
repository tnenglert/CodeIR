#!/usr/bin/env python3
"""Analyze Claude transcript JSONL files for token usage.

Reports tokens using blog-standard methodology:
  Context tokens = input_tokens + cache_read_input_tokens
  Output tokens = output_tokens

Usage:
  python transcript_tokens.py transcript.jsonl
  python transcript_tokens.py *.jsonl --breakdown
  python transcript_tokens.py transcript.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple


class TokenStats(NamedTuple):
    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    api_calls: int

    @property
    def context_tokens(self) -> int:
        """Blog-standard context = input + cache_read."""
        return self.input_tokens + self.cache_read_tokens

    @property
    def total_tokens(self) -> int:
        return self.context_tokens + self.output_tokens


def analyze_transcript(path: Path) -> TokenStats:
    """Parse a transcript JSONL and sum token fields."""
    input_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    output_tokens = 0
    api_calls = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Look for usage data in various locations
            usage = None
            if "usage" in entry:
                usage = entry["usage"]
            elif "message" in entry and isinstance(entry["message"], dict):
                usage = entry["message"].get("usage")

            if usage and isinstance(usage, dict):
                api_calls += 1
                input_tokens += usage.get("input_tokens", 0)
                cache_read_tokens += usage.get("cache_read_input_tokens", 0)
                cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
                output_tokens += usage.get("output_tokens", 0)

    return TokenStats(
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        output_tokens=output_tokens,
        api_calls=api_calls,
    )


def format_tokens(n: int) -> str:
    """Format token count with K suffix for readability."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def print_stats(path: Path, stats: TokenStats, breakdown: bool = False) -> None:
    """Print token stats for a transcript."""
    print(f"\n{path.name}")
    print(f"  Context tokens: {format_tokens(stats.context_tokens)}")
    print(f"  Output tokens:  {format_tokens(stats.output_tokens)}")
    print(f"  API calls:      {stats.api_calls}")

    if breakdown:
        print(f"\n  Breakdown:")
        print(f"    input_tokens:          {format_tokens(stats.input_tokens)}")
        print(f"    cache_read_tokens:     {format_tokens(stats.cache_read_tokens)}")
        print(f"    cache_creation_tokens: {format_tokens(stats.cache_creation_tokens)}")
        if stats.cache_read_tokens > 0:
            cache_pct = stats.cache_read_tokens / stats.context_tokens * 100
            print(f"    cache hit rate:        {cache_pct:.1f}%")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Claude transcript JSONL files for token usage."
    )
    parser.add_argument("files", nargs="+", type=Path, help="Transcript JSONL files")
    parser.add_argument(
        "--breakdown", "-b", action="store_true",
        help="Show detailed breakdown (input vs cache_read)"
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output as JSON"
    )
    args = parser.parse_args()

    results: List[Dict] = []
    total = TokenStats(0, 0, 0, 0, 0)

    for path in args.files:
        if not path.exists():
            print(f"Warning: {path} not found", file=sys.stderr)
            continue

        stats = analyze_transcript(path)
        results.append({
            "file": str(path),
            "context_tokens": stats.context_tokens,
            "output_tokens": stats.output_tokens,
            "input_tokens": stats.input_tokens,
            "cache_read_tokens": stats.cache_read_tokens,
            "cache_creation_tokens": stats.cache_creation_tokens,
            "api_calls": stats.api_calls,
        })

        total = TokenStats(
            total.input_tokens + stats.input_tokens,
            total.cache_read_tokens + stats.cache_read_tokens,
            total.cache_creation_tokens + stats.cache_creation_tokens,
            total.output_tokens + stats.output_tokens,
            total.api_calls + stats.api_calls,
        )

        if not args.json:
            print_stats(path, stats, args.breakdown)

    if args.json:
        output = {
            "files": results,
            "total": {
                "context_tokens": total.context_tokens,
                "output_tokens": total.output_tokens,
                "input_tokens": total.input_tokens,
                "cache_read_tokens": total.cache_read_tokens,
                "cache_creation_tokens": total.cache_creation_tokens,
                "api_calls": total.api_calls,
            }
        }
        print(json.dumps(output, indent=2))
    elif len(args.files) > 1:
        print(f"\n{'='*40}")
        print(f"TOTAL ({len(results)} files)")
        print(f"  Context tokens: {format_tokens(total.context_tokens)}")
        print(f"  Output tokens:  {format_tokens(total.output_tokens)}")
        print(f"  API calls:      {total.api_calls}")


if __name__ == "__main__":
    main()
