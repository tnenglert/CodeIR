"""Focused parser tests for grep/expand compatibility affordances."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_grep_parser_accepts_positional_paths_and_category():
    parser = cli.build_parser()
    args = parser.parse_args(["grep", "needle", "src/app.py", "lib/", "--category", "core_logic"])

    assert args.command == "grep"
    assert args.pattern == "needle"
    assert args.path_positional == ["src/app.py", "lib/"]
    assert args.category == "core_logic"


def test_grep_parser_accepts_before_after_context_flags():
    parser = cli.build_parser()
    args = parser.parse_args(["grep", "needle", "-A", "3", "-B", "1"])

    assert args.after_context == 3
    assert args.before_context == 1


def test_expand_parser_accepts_limit():
    parser = cli.build_parser()
    args = parser.parse_args(["expand", "AAA", "--limit", "10"])

    assert args.command == "expand"
    assert args.entity_ids == ["AAA"]
    assert args.limit == 10
