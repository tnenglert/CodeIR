#!/usr/bin/env python3
"""
Claude Code Transcript Token Analyzer

Parses Claude Code's local JSONL transcript files and shows
cumulative token usage at each turn. Uses API-reported token
counts from the JSONL metadata, NOT estimates.

Usage:
    python transcript_tokens.py <path_to_jsonl_file>
    python transcript_tokens.py <path_to_jsonl_file> --turn 8
    python transcript_tokens.py <path_to_jsonl_file> --search "codeir expand"
    python transcript_tokens.py <path_to_jsonl_file> --summary

Finding your JSONL files:
    Claude Code stores transcripts in ~/.claude/projects/
    Each session gets a JSONL file. Look for files matching
    the timeframe of your session.
"""

import json
import sys
import argparse
from pathlib import Path


def parse_transcript(filepath: str) -> list[dict]:
    """Parse a JSONL transcript file and extract per-turn token usage.

    IMPORTANT: Claude Code streams responses, logging multiple assistant messages
    per API call with the same usage stats. To avoid double-counting, we only
    count the LAST assistant message before each user message (or end of file).
    This represents the final state of each API call.
    """
    # First pass: collect all entries
    entries = []
    with open(filepath, "r") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entry["_line_num"] = line_num
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    # Second pass: find the last assistant message before each user message
    # This avoids counting streaming chunks multiple times
    turns = []
    last_assistant = None
    tool_calls_in_turn = 0

    for entry in entries:
        msg_type = entry.get("type", "")

        if msg_type == "assistant" and "message" in entry:
            msg = entry["message"]
            usage = msg.get("usage", {})

            # Only consider entries with actual output
            if usage.get("output_tokens", 0) > 0:
                # Extract content preview and count tool calls
                content_preview = ""
                content = msg.get("content", [])

                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "tool_use":
                                tool_calls_in_turn += 1
                                if not content_preview:
                                    tool_name = block.get("name", "unknown")
                                    tool_input = str(block.get("input", ""))[:60]
                                    content_preview = f"[tool: {tool_name}] {tool_input}"
                            elif block.get("type") == "text" and "text" in block:
                                if not content_preview:
                                    content_preview = block["text"][:100]

                # Store this as the latest assistant state for this turn
                last_assistant = {
                    "line": entry["_line_num"],
                    "role": "assistant",
                    "content_preview": content_preview,
                    "usage": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    },
                    "tool_calls": tool_calls_in_turn,
                }

        elif msg_type == "user":
            # User message - finalize the previous assistant turn
            if last_assistant:
                turns.append(last_assistant)
                last_assistant = None
                tool_calls_in_turn = 0

    # Don't forget the last assistant turn (if conversation ends with assistant)
    if last_assistant:
        turns.append(last_assistant)

    # Third pass: compute cumulative totals
    cumulative = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    total_tool_calls = 0

    for turn in turns:
        for key in cumulative:
            cumulative[key] += turn["usage"][key]
        total_tool_calls += turn.get("tool_calls", 0)

        turn["turn_usage"] = turn["usage"]
        turn["turn_total"] = sum(turn["usage"].values())
        turn["cumulative"] = cumulative.copy()
        turn["cumulative_total"] = sum(cumulative.values())
        turn["cumulative_tool_calls"] = total_tool_calls

    return turns


def print_turn(i: int, turn: dict, verbose: bool = False):
    """Print a single turn's token information."""
    preview = turn["content_preview"].replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:77] + "..."

    print(f"\n{'='*70}")
    print(f"Turn {i+1} (line {turn['line']})  |  role: {turn['role']}")
    print(f"  Preview: {preview}")
    print(f"  This turn:  {turn['turn_total']:>8,} tokens")
    if verbose:
        u = turn["turn_usage"]
        print(f"    input:          {u['input_tokens']:>8,}")
        print(f"    output:         {u['output_tokens']:>8,}")
        print(f"    cache_creation: {u['cache_creation_input_tokens']:>8,}")
        print(f"    cache_read:     {u['cache_read_input_tokens']:>8,}")
    print(f"  Cumulative: {turn['cumulative_total']:>8,} tokens")
    if verbose:
        c = turn["cumulative"]
        print(f"    input:          {c['input_tokens']:>8,}")
        print(f"    output:         {c['output_tokens']:>8,}")
        print(f"    cache_creation: {c['cache_creation_input_tokens']:>8,}")
        print(f"    cache_read:     {c['cache_read_input_tokens']:>8,}")


def print_summary(turns: list[dict]):
    """Print session summary."""
    if not turns:
        print("No turns with token data found.")
        return

    final = turns[-1]["cumulative"]
    total = turns[-1]["cumulative_total"]
    total_tool_calls = turns[-1].get("cumulative_tool_calls", 0)

    # Calculate total input (fresh + cache)
    total_input = (
        final["input_tokens"]
        + final["cache_creation_input_tokens"]
        + final["cache_read_input_tokens"]
    )

    print(f"\n{'='*70}")
    print(f"SESSION SUMMARY")
    print(f"{'='*70}")
    print(f"  API calls (turns):   {len(turns):>10}")
    print(f"  Tool calls:          {total_tool_calls:>10}")
    print()
    print(f"  Input breakdown:")
    print(f"    Fresh input:       {final['input_tokens']:>10,}")
    print(f"    Cache read:        {final['cache_read_input_tokens']:>10,}")
    print(f"    Cache creation:    {final['cache_creation_input_tokens']:>10,}")
    print(f"    ─────────────────────────────")
    print(f"    Total input:       {total_input:>10,}")
    print()
    print(f"  Output:              {final['output_tokens']:>10,}")
    print(f"  ═════════════════════════════════")
    print(f"  GRAND TOTAL:         {total_input + final['output_tokens']:>10,}")

    if final["cache_read_input_tokens"] + final["cache_creation_input_tokens"] > 0:
        cache_total = final["cache_read_input_tokens"] + final["cache_creation_input_tokens"]
        hit_rate = final["cache_read_input_tokens"] / cache_total * 100
        print(f"\n  Cache hit rate:      {hit_rate:>9.1f}%")

    # Cost estimate (Sonnet 3.5 pricing)
    input_cost = (final["input_tokens"] / 1_000_000) * 3.0
    cache_read_cost = (final["cache_read_input_tokens"] / 1_000_000) * 0.30
    cache_create_cost = (final["cache_creation_input_tokens"] / 1_000_000) * 3.75
    output_cost = (final["output_tokens"] / 1_000_000) * 15.0
    total_cost = input_cost + cache_read_cost + cache_create_cost + output_cost

    print(f"\n  Est. cost (Sonnet 3.5 pricing):")
    print(f"    Input ($3/MTok):   ${input_cost:>9.4f}")
    print(f"    Cache rd ($0.30):  ${cache_read_cost:>9.4f}")
    print(f"    Cache cr ($3.75):  ${cache_create_cost:>9.4f}")
    print(f"    Output ($15/MTok): ${output_cost:>9.4f}")
    print(f"    ─────────────────────────────")
    print(f"    Total:             ${total_cost:>9.4f}")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze token usage from Claude Code JSONL transcripts"
    )
    parser.add_argument("filepath", help="Path to JSONL transcript file")
    parser.add_argument(
        "--turn", type=int, default=None,
        help="Show cumulative usage up to this turn number"
    )
    parser.add_argument(
        "--search", type=str, default=None,
        help="Search for a string in turn content and show usage at that point"
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Show only the session summary"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show per-category token breakdown for each turn"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Show a compact one-line-per-turn listing"
    )

    args = parser.parse_args()

    filepath = Path(args.filepath)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    turns = parse_transcript(str(filepath))

    if not turns:
        print("No turns with token usage data found in this file.")
        print("This might not be a Claude Code transcript, or the format may differ.")
        sys.exit(1)

    if args.summary:
        print_summary(turns)
        return

    if args.list:
        print(f"{'Turn':>5} {'Line':>6} {'Role':<12} {'This Turn':>10} {'Cumulative':>12}  Preview")
        print("-" * 100)
        for i, turn in enumerate(turns):
            preview = turn["content_preview"].replace("\n", " ")[:50]
            print(
                f"{i+1:>5} {turn['line']:>6} {turn['role']:<12} "
                f"{turn['turn_total']:>10,} {turn['cumulative_total']:>12,}  {preview}"
            )
        print()
        print_summary(turns)
        return

    if args.search:
        found = False
        for i, turn in enumerate(turns):
            if args.search.lower() in turn["content_preview"].lower():
                print(f"Found '{args.search}' at turn {i+1}:")
                print_turn(i, turn, verbose=args.verbose)
                print_summary_at_turn(turns, i)
                found = True
        if not found:
            print(f"'{args.search}' not found in turn previews.")
            print("Note: previews are truncated to 100 chars. Try a shorter search term.")
        return

    if args.turn:
        target = min(args.turn, len(turns))
        for i in range(target):
            print_turn(i, turns[i], verbose=args.verbose)
        print(f"\n--- Cumulative at turn {target}: {turns[target-1]['cumulative_total']:,} tokens ---")
        return

    # Default: show all turns
    for i, turn in enumerate(turns):
        print_turn(i, turn, verbose=args.verbose)

    print_summary(turns)


def print_summary_at_turn(turns: list[dict], turn_index: int):
    """Print cumulative summary up to a specific turn."""
    turn = turns[turn_index]
    c = turn["cumulative"]
    print(f"\n  Cumulative through turn {turn_index + 1}:")
    print(f"    Total:           {turn['cumulative_total']:>10,}")
    print(f"    input:           {c['input_tokens']:>10,}")
    print(f"    output:          {c['output_tokens']:>10,}")
    print(f"    cache_creation:  {c['cache_creation_input_tokens']:>10,}")
    print(f"    cache_read:      {c['cache_read_input_tokens']:>10,}")


if __name__ == "__main__":
    main()
