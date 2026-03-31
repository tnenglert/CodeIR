#!/usr/bin/env python3
"""CodeIR CLI entrypoint.

Commands:
  init         — Full setup: index + bearings + rules in one step
  index        — Index a repository with multi-pass pipeline
  search       — Search entities in an indexed repository
  show         — Display compressed IR for an entity
  expand       — Display raw source code for an entity
  compare      — Side-by-side comparison of all compression levels for an entity
  callers      — Show what calls a given entity (reverse lookup)
  impact       — Reverse dependency analysis — what breaks if this changes
  scope        — Minimal context needed to safely modify an entity
  grep         — Grep source files with IR context for matching entities
  stats        — Show repository index statistics
  module-map   — Display classified module map with dependencies
  bearings     — Generate bearings.md agent orientation context file
  patterns     — List detected structural patterns (base-class clusters ≥30 entities)
  rules        — Generate .claude/rules/CodeIR.md agent instructions with repo-specific examples
  eval         — Evaluate compression levels side-by-side
  floor-test   — Comprehensibility floor testing (generate/score)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

from index.indexer import index_repo, map_legacy_mode_to_level
from index.locator import extract_code_slice
from index.search import compute_impact, compute_scope, grep_entities, search_entities
from index.store.db import column_names, connect
from index.store.fetch import get_entities_by_pattern, get_entity_all_levels, get_entity_location, get_entity_with_ir
from index.store.stats import get_stats


# Pattern feature toggle - set to False for vanilla CodeIR testing
PATTERNS_ENABLED = True

# Default truncation limit for entity lists (callers, impact, scope)
# Override with CODEIR_LIST_LIMIT environment variable
import os
DEFAULT_LIST_LIMIT = int(os.environ.get("CODEIR_LIST_LIMIT", "15"))


def get_entity_annotations(conn: sqlite3.Connection, entity_ids: list) -> Dict[str, Dict]:
    """Fetch annotation metadata for a list of entity IDs.

    Returns dict mapping entity_id to:
      - caller_count: number of callers
      - pattern_base: base class name if in a pattern, else None
      - kind: entity kind (function, method, class, etc.)
      - line_count: end_line - start_line + 1
    """
    if not entity_ids:
        return {}

    placeholders = ",".join("?" * len(entity_ids))

    # Get basic entity info
    entity_rows = conn.execute(f"""
        SELECT id, kind, start_line, end_line
        FROM entities
        WHERE id IN ({placeholders})
    """, entity_ids).fetchall()

    annotations = {}
    for row in entity_rows:
        eid = row[0]
        start = row[2] or 0
        end = row[3] or 0
        annotations[eid] = {
            "caller_count": 0,
            "pattern_base": None,
            "kind": row[1],
            "line_count": max(1, end - start + 1),
        }

    # Get caller counts
    caller_rows = conn.execute(f"""
        SELECT entity_id, COUNT(*) as cnt
        FROM callers
        WHERE entity_id IN ({placeholders})
        GROUP BY entity_id
    """, entity_ids).fetchall()

    for row in caller_rows:
        if row[0] in annotations:
            annotations[row[0]]["caller_count"] = row[1]

    # Get pattern membership (if patterns table exists)
    try:
        pattern_rows = conn.execute(f"""
            SELECT pm.entity_id, p.base_class
            FROM pattern_members pm
            JOIN patterns p ON pm.pattern_id = p.pattern_id
            WHERE pm.entity_id IN ({placeholders})
        """, entity_ids).fetchall()

        for row in pattern_rows:
            if row[0] in annotations:
                annotations[row[0]]["pattern_base"] = row[1]
    except sqlite3.OperationalError:
        pass  # patterns table doesn't exist

    return annotations


def format_annotated_entity(
    entity_id: str,
    file_path: str,
    annotations: Dict[str, Dict],
    marker: str = " ",
    show_ir: bool = False,
    ir_text: str = "",
) -> str:
    """Format an entity line with inline annotations.

    Output format:
      {marker}{entity_id:20s}  [{N} callers] {→Pattern}  {file_path}  [{kind}, ~{lines} lines]
    """
    ann = annotations.get(entity_id, {})
    caller_count = ann.get("caller_count", 0)
    pattern_base = ann.get("pattern_base")
    kind = ann.get("kind", "?")
    line_count = ann.get("line_count", 0)

    # Format caller count
    caller_str = f"[{caller_count} callers]" if caller_count > 0 else "[0 callers]"

    # Format pattern (if any)
    pattern_str = f"→{pattern_base}" if pattern_base else ""

    # Format kind and lines
    kind_str = f"[{kind}, ~{line_count} lines]"

    # Build the line with aligned columns
    # Entity ID (20), caller count (12), pattern (12), file path (variable), kind/lines
    line = f"{marker}{entity_id:20s}  {caller_str:12s} {pattern_str:12s}  {file_path:40s}  {kind_str}"

    return line


def _entity_sort_key(entity_id: str, file_path: str, annotations: Dict[str, Dict]) -> tuple:
    """Compute sort key for smart truncation.

    Priority order (lower = better):
    1. Core logic before tests (tier 0 vs 1)
    2. High caller-count before zero-caller (within tier)
    3. Pattern outliers before standard pattern members
    4. File path for consistency within same priority

    Returns tuple for sorting: (is_test, -caller_count, is_pattern_member, file_path)
    """
    ann = annotations.get(entity_id, {})
    caller_count = ann.get("caller_count", 0)
    pattern_base = ann.get("pattern_base")

    # Tier 0: core logic, Tier 1: tests
    is_test = 1 if ("test" in file_path.lower() or "/tests/" in file_path.lower()) else 0

    # Higher caller count = more important (negate for ascending sort)
    neg_caller_count = -caller_count

    # Pattern members are standard/predictable, outliers are more interesting
    is_pattern_member = 1 if pattern_base else 0

    return (is_test, neg_caller_count, is_pattern_member, file_path)


def smart_truncate_entities(
    entities: list,
    annotations: Dict[str, Dict],
    limit: int = DEFAULT_LIST_LIMIT,
    show_all: bool = False,
    entity_id_key: str = "entity_id",
    file_path_key: str = "file_path",
) -> tuple:
    """Sort entities by priority and truncate to limit.

    Args:
        entities: List of entity dicts
        annotations: Annotation dict from get_entity_annotations
        limit: Max entities to return (ignored if show_all=True)
        show_all: If True, return all entities (no truncation)
        entity_id_key: Key to extract entity_id from each entity dict
        file_path_key: Key to extract file_path from each entity dict

    Returns:
        (sorted_entities, total_count, was_truncated)
    """
    if not entities:
        return [], 0, False

    total = len(entities)

    # Sort by priority
    sorted_entities = sorted(
        entities,
        key=lambda e: _entity_sort_key(
            e.get(entity_id_key, ""),
            e.get(file_path_key, ""),
            annotations
        )
    )

    if show_all or total <= limit:
        return sorted_entities, total, False

    return sorted_entities[:limit], total, True


DEFAULT_CONFIG: Dict[str, Any] = {
    "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".codeir"],
    "compression_level": "Behavior+Index",
}


def load_config(repo_path: Path) -> Dict[str, Any]:
    """Load optional config from <repo>/.codeir/config.json."""
    cfg = dict(DEFAULT_CONFIG)
    cfg_path = repo_path / ".codeir" / "config.json"
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        if isinstance(user_cfg, dict):
            cfg.update(user_cfg)
    return cfg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codeir", description="CodeIR — semantic compression and indexing for codebases")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Full setup: index + bearings + platform instructions")
    p_init.add_argument("repo_path", type=Path, nargs="?", default=None,
                        help="Repository path (default: auto-detect from cwd)")
    p_init.add_argument("--level", default=None, help="Compression level (default: Behavior+Index)")
    p_init.add_argument("--platform", choices=["claude", "codex", "openclaw", "current", "all"],
                        default=None, help="Target platform (default: repo auto-detect, then runtime)")
    p_init.add_argument("--list", action="store_true", dest="list_only",
                        help="Show what would be generated without writing files")
    p_init.add_argument("--force", action="store_true",
                        help="Overwrite existing instruction files")
    p_init.add_argument("--skip-index", action="store_true",
                        help="Skip indexing, only generate bearings and instructions")

    # index
    p_index = sub.add_parser("index", help="Index a repository")
    p_index.add_argument("repo_path", type=Path)
    p_index.add_argument("--level", default=None, help="Compression level: Source, Behavior, Index, Behavior+Index, or all (default: Behavior+Index)")
    p_index.add_argument("--mode", default=None, help="Legacy mode alias: a, b, or hybrid")
    p_index.add_argument("--compact", action="store_true", help="Rebuild abbreviation maps from scratch")

    # search
    p_search = sub.add_parser("search", help="Search entities")
    p_search.add_argument("query", nargs="+", help="Search terms (space-separated, OR logic with term-count ranking)")
    p_search.add_argument("--repo-path", type=Path, default=Path("."))
    p_search.add_argument("--limit", type=int, default=50)
    p_search.add_argument("--category", default=None, help="Filter by module category (e.g., core_logic, tests)")
    p_search.add_argument("--patterns", action="store_true", help="Show pattern membership markers (→BaseClass)")

    # show
    p_show = sub.add_parser("show", help="Show entity IR")
    p_show.add_argument("entity_ids", nargs="+", metavar="ENTITY_ID")
    p_show.add_argument("--repo-path", type=Path, default=Path("."))
    p_show.add_argument("--level", default="Behavior", help="Compression level to show")
    p_show.add_argument("--full", action="store_true", help="Show full IR (skip smart pattern view)")

    # expand
    p_expand = sub.add_parser("expand", help="Show raw source for entities (supports STEM.* wildcards)")
    p_expand.add_argument("entity_ids", nargs="+", metavar="ENTITY_ID",
                          help="One or more entity IDs, or STEM.* for all siblings")
    p_expand.add_argument("--repo-path", type=Path, default=Path("."))
    p_expand.add_argument(
        "--number",
        action="store_true",
        help="Show source with line numbers for easier citation",
    )

    # compare
    p_compare = sub.add_parser("compare", help="Compare all compression levels for an entity")
    p_compare.add_argument("entity_id")
    p_compare.add_argument("--repo-path", type=Path, default=Path("."))

    # stats
    p_stats = sub.add_parser("stats", help="Show index statistics")
    p_stats.add_argument("--repo-path", type=Path, default=Path("."))

    # module-map
    p_modmap = sub.add_parser("module-map", help="Display classified module map")
    p_modmap.add_argument("--repo-path", type=Path, default=Path("."))

    # bearings
    p_bearings = sub.add_parser("bearings", help="Show orientation context with tiered menu")
    p_bearings.add_argument("category", nargs="?", default=None, help="Category to show (e.g., core_logic, tests)")
    p_bearings.add_argument("--repo-path", type=Path, default=Path("."))
    p_bearings.add_argument("--full", action="store_true", help="Output full bearings.md")
    p_bearings.add_argument("--generate", action="store_true", help="Generate/regenerate bearings files")

    # callers
    p_callers = sub.add_parser("callers", help="Show what calls a given entity")
    p_callers.add_argument("entity_id")
    p_callers.add_argument("--repo-path", type=Path, default=Path("."))
    p_callers.add_argument("--resolution", default=None,
                           help="Filter by resolution type: import, local, fuzzy")
    p_callers.add_argument("--all", action="store_true", dest="show_all",
                           help="Show all callers (no truncation)")

    # impact
    p_impact = sub.add_parser("impact", help="Reverse dependency analysis — what breaks if this changes")
    p_impact.add_argument("entity_id")
    p_impact.add_argument("--repo-path", type=Path, default=Path("."))
    p_impact.add_argument("--depth", type=int, default=2, help="Max traversal depth (default: 2)")
    p_impact.add_argument("--level", default="Behavior", help="IR level to show (default: Behavior)")
    p_impact.add_argument(
        "--exclude-area",
        action="append",
        choices=["lib", "test", "tests", "examples", "docs", "other"],
        help="Exclude impacted entities in the given area (repeatable)",
    )
    p_impact.add_argument("--all", action="store_true", dest="show_all",
                           help="Show all affected entities (no truncation)")

    # scope
    p_scope = sub.add_parser("scope", help="Minimal context needed to safely modify an entity")
    p_scope.add_argument("entity_id")
    p_scope.add_argument("--repo-path", type=Path, default=Path("."))
    p_scope.add_argument("--level", default="Behavior", help="IR level to show (default: Behavior)")
    p_scope.add_argument("--all", action="store_true", dest="show_all",
                           help="Show all related entities (no truncation)")

    # trace
    p_trace = sub.add_parser("trace", help="Find call path between two entities")
    p_trace.add_argument("from_entity", help="Starting entity ID")
    p_trace.add_argument("to_entity", help="Target entity ID")
    p_trace.add_argument("--repo-path", type=Path, default=Path("."))
    p_trace.add_argument("--depth", type=int, default=10, help="Maximum search depth (default: 10)")
    p_trace.add_argument("--resolution", choices=["import", "local", "fuzzy", "any"],
                        default="any", help="Filter edges by resolution confidence (default: any)")

    # grep
    p_grep = sub.add_parser("grep", help="Grep source files with IR context")
    p_grep.add_argument("pattern", help="Regex pattern to search for")
    p_grep.add_argument("--repo-path", type=Path, default=Path("."))
    p_grep.add_argument("--level", default="Behavior", help="IR level to attach (default: Behavior)")
    p_grep.add_argument("--limit", type=int, default=50, help="Max result groups (default: 50)")
    p_grep.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive matching")
    p_grep.add_argument("-C", "--context", type=int, default=0, help="Show N surrounding lines per match (like grep -C)")
    p_grep.add_argument(
        "--path",
        action="append",
        default=None,
        help="Scope to directory or glob (repeatable, e.g., --path orm/ --path tests/)",
    )
    p_grep.add_argument("-v", "--verbose", action="store_true", help="Include IR context for each entity match")
    p_grep.add_argument(
        "--count",
        action="store_true",
        help="Show match counts only, grouped by entity/file and sorted by count",
    )
    p_grep.add_argument(
        "--evidence",
        action="store_true",
        help="Use instead of `rg -n ...` then `sed -n ...`: include exact matching lines, nearby context, and IR in one call",
    )

    # patterns
    p_patterns = sub.add_parser("patterns", help="List detected structural patterns")
    p_patterns.add_argument("--repo-path", type=Path, default=Path("."))
    p_patterns.add_argument("--category", default=None, help="Filter by category")
    p_patterns.add_argument("--min-size", type=int, default=30, help="Minimum pattern size (default: 30)")
    p_patterns.add_argument("--include-tests", action="store_true", help="Include test patterns")

    # rules
    p_rules = sub.add_parser("rules", help="Generate .claude/rules/CodeIR.md agent instructions")
    p_rules.add_argument("repo_path", nargs="?", type=Path, default=None, help="Repository path (default: .)")
    p_rules.add_argument("--repo-path", type=Path, default=Path("."), dest="repo_path_flag")
    p_rules.add_argument("--output", type=Path, default=None, help="Output path (default: .claude/rules/CodeIR.md)")

    # eval
    p_eval = sub.add_parser("eval", help="Evaluate compression levels")
    p_eval.add_argument("repo_path", type=Path)
    p_eval.add_argument("--levels", nargs="+", default=["Behavior", "Index"])
    p_eval.add_argument("--modes", default=None, help="Legacy modes: comma-separated a,b,hybrid")
    p_eval.add_argument("--output", type=Path, default=None)

    # floor-test
    p_floor = sub.add_parser("floor-test", help="Comprehensibility floor testing")
    floor_sub = p_floor.add_subparsers(dest="floor_action", required=True)

    p_floor_gen = floor_sub.add_parser("generate", help="Generate test pack")
    p_floor_gen.add_argument("repo_path", type=Path)
    p_floor_gen.add_argument("--level", default="Behavior", help="Compression level for test pack")
    p_floor_gen.add_argument("--count", type=int, default=15, help="Number of test entities")
    p_floor_gen.add_argument("--seed", type=int, default=42, help="Random seed for entity selection")
    p_floor_gen.add_argument("--output", type=Path, default=None, help="Output JSON path")

    p_floor_score = floor_sub.add_parser("score", help="Score test results and produce floor report")
    p_floor_score.add_argument("results_path", type=Path, help="Path to scored results JSON")
    p_floor_score.add_argument("--output", type=Path, default=None, help="Output floor report path")

    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    """Full setup: index a repository, then generate bearings and platform instructions."""
    from ir.init import (
        find_repo_root, generate_instructions, print_detection_help,
        select_platforms, ALL_PLATFORMS,
    )

    def _platform_names(platforms: list) -> str:
        return ", ".join(platform.display_name for platform in platforms) if platforms else "none"

    def _platform_keys(platforms: list) -> set[str]:
        return {platform.name for platform in platforms}

    # Resolve repo path (auto-detect if not provided)
    if args.repo_path:
        repo_path = args.repo_path.resolve()
    else:
        repo_path = find_repo_root()

    selection = select_platforms(repo_path, args.platform)
    platforms = selection.selected

    # If --list only, just show what would be generated and exit
    if args.list_only:
        if args.platform is None:
            print(f"Repo-detected platforms: {_platform_names(selection.repo_detected)}")
            print(f"Current runtime platform: {_platform_names(selection.runtime_detected)}")
            if selection.repo_detected and selection.runtime_detected:
                repo_keys = _platform_keys(selection.repo_detected)
                runtime_keys = _platform_keys(selection.runtime_detected)
                if repo_keys != runtime_keys:
                    print("Auto-selecting repo-detected platforms.")
            elif selection.mode == "runtime_fallback" and platforms:
                print("No repo markers found. Falling back to current runtime.")
            print()

        if args.platform == "current" and not platforms:
            print("No current runtime platform detected.")
            print("Use --platform <name> or --platform all to choose explicitly.")
            return

        if not platforms:
            print_detection_help()
            print()
            print("A full init without detection will still default to Claude Code.")
            return
        results = generate_instructions(repo_path, platforms, dry_run=True)
        for platform, path, status in results:
            rel = path.relative_to(repo_path)
            action = "create" if status == "would_create" else "overwrite"
            print(f"  -> {platform.display_name:12s} -> {rel}  (would {action})")
        return

    if args.platform == "current" and not platforms:
        print("No current runtime platform detected.")
        print("Use --platform <name> or --platform all to choose explicitly.")
        return

    if args.platform is None:
        repo_keys = _platform_keys(selection.repo_detected)
        runtime_keys = _platform_keys(selection.runtime_detected)
        if selection.repo_detected and selection.runtime_detected and repo_keys != runtime_keys:
            print(
                "Note: current runtime looks like "
                f"{_platform_names(selection.runtime_detected)}, but repo markers select "
                f"{_platform_names(selection.repo_detected)}."
            )
            print("Using repo markers. Use --platform current or --platform all to override.")
        elif selection.mode == "runtime_fallback" and platforms:
            print(f"No repo markers detected. Using current runtime platform: {_platform_names(platforms)}.")

    # 1. Index (unless --skip-index)
    if not args.skip_index:
        cfg = load_config(repo_path)
        if args.level:
            cfg["compression_level"] = args.level

        print(f"Indexing {repo_path} ...")
        result = index_repo(repo_path, cfg)
        if result.get("status") == "no_changes":
            print(f"No changes detected. {result.get('files_scanned', 0)} files scanned.")
        else:
            print(f"  {result.get('total_entities', 0)} entities, "
                  f"{result.get('files_changed', 0)} files changed")

    # 2. Bearings
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("Error: No index found. Run without --skip-index first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT file_path, category, entity_count, deps_internal "
            "FROM modules ORDER BY category, file_path"
        ).fetchall()
        total = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    except sqlite3.OperationalError:
        print("Warning: could not generate bearings (no module data).")
        conn.close()
        return
    conn.close()

    modules = [
        {"file_path": r["file_path"], "category": r["category"],
         "entity_count": r["entity_count"], "deps_internal": r["deps_internal"]}
        for r in rows
    ]
    module_ids = _compute_module_ids(modules)

    from ir.classifier import generate_context_file, generate_summary, generate_category_file

    bearings_paths = _get_bearings_paths(repo_path)
    bearings_paths["base"].mkdir(parents=True, exist_ok=True)

    summary_content = generate_summary(repo_path.name, modules, total)
    bearings_paths["summary"].write_text(summary_content, encoding="utf-8")

    bearings_content = generate_context_file(repo_path.name, modules, total, module_ids)
    bearings_paths["map"].write_text(bearings_content, encoding="utf-8")

    bearings_paths["categories"].mkdir(parents=True, exist_ok=True)
    by_cat: Dict[str, list] = {}
    for mod in modules:
        by_cat.setdefault(mod["category"], []).append(mod)
    for category, cat_mods in by_cat.items():
        cat_content = generate_category_file(repo_path.name, category, cat_mods, module_ids)
        (bearings_paths["categories"] / f"{category}.md").write_text(cat_content, encoding="utf-8")

    print(f"  Bearings: {len(modules)} modules, {total} entities")

    # 3. Platform instructions
    if not platforms:
        # No repo/runtime platforms detected — default to Claude Code instructions
        from ir.init import ClaudeCode
        platforms = [ClaudeCode()]

    results = generate_instructions(repo_path, platforms, dry_run=False, force=args.force)

    for platform, path, status in results:
        rel = path.relative_to(repo_path)
        if status == "created":
            print(f"  {platform.display_name:12s} -> {rel}")
        elif status == "exists":
            print(f"  {platform.display_name:12s} -> {rel}  (exists, use --force to overwrite)")

    print("Done.")


def cmd_index(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    cfg = load_config(repo_path)
    if args.level and args.mode:
        print("Use either --level or --mode, not both.")
        return
    if args.level:
        cfg["compression_level"] = args.level
    elif args.mode:
        cfg["compression_mode"] = args.mode
    if args.compact:
        cfg["compact_mode"] = True

    result = index_repo(repo_path, cfg)

    if result.get("status") == "no_changes":
        language = result.get("language", "python")
        print(f"No changes detected. {result.get('files_scanned', 0)} {language} files scanned.")
        _ensure_agent_rules(repo_path)
        return

    print(f"Indexed {result.get('files_changed', 0)} changed files "
          f"({result.get('files_unchanged', 0)} unchanged, {result.get('files_scanned', 0)} total)")
    print(f"  Language: {result.get('language', 'python')}")
    print(f"  Entities indexed: {result.get('entities_indexed', 0)} (total: {result.get('total_entities', 0)})")
    print(f"  IR rows: {result.get('ir_rows', 0)} (total: {result.get('total_ir_rows', 0)})")
    print(f"  Abbreviations: {result.get('abbreviations', 0)}")
    print(f"  Caller links: {result.get('caller_relationships', 0)}")

    # Show ambiguous calls summary
    ambiguous = result.get('ambiguous_calls', [])
    if ambiguous:
        # Group by call_name for summary
        by_name = {}
        for a in ambiguous:
            name = a["call_name"]
            by_name[name] = by_name.get(name, 0) + 1
        top_ambiguous = sorted(by_name.items(), key=lambda x: -x[1])[:5]
        summary = ", ".join(f"{name}({cnt})" for name, cnt in top_ambiguous)
        print(f"  Ambiguous calls: {len(ambiguous)} ({summary})")

    print(f"  Level: {result.get('compression_level', 'Behavior')}")
    print(f"  Store: {result.get('store_dir', '')}")

    # Run pattern detection
    from index.pattern_detector import detect_patterns
    db_path = repo_path / ".codeir" / "entities.db"
    patterns = detect_patterns(db_path)
    if patterns:
        non_test = [p for p in patterns if not p.is_test_pattern]
        test_pats = [p for p in patterns if p.is_test_pattern]
        coverage = sum(p.member_count for p in non_test)
        print(f"  Patterns: {len(non_test)} structural ({coverage} entities), {len(test_pats)} test")

    # Checkpoint WAL into main DB so the store works in sandboxed/immutable
    # environments (e.g. Codex exec) without needing WAL file access.
    _checkpoint_store(repo_path)

    # Ensure agent discovery files exist so both Claude and Codex find CodeIR.
    _ensure_agent_rules(repo_path)


def _checkpoint_store(repo_path: Path) -> None:
    """Merge WAL journal into main DB files and switch to DELETE mode.

    This makes .codeir/*.db self-contained single files that can be opened
    with ``immutable=1`` in restricted environments.
    """
    store_dir = repo_path / ".codeir"
    for db_name in ("entities.db", "mapping.db"):
        db_path = store_dir / db_name
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.close()
        except sqlite3.OperationalError:
            pass  # DB may already be in DELETE mode or read-only


# ---------------------------------------------------------------------------
# Claude / Codex discovery files
# ---------------------------------------------------------------------------

_CLAUDE_RULES_TEXT = """\
## Access to CodeIR

Commands: `bearings` | `search` | `grep` | `show` | `expand` | `callers` | `impact` | `scope`

This repository includes a pre-built working model of the entire codebase — its structure, logic, and relationships — that fits in your context window.

Orient via `codeir bearings` — shows project summary with a menu of category-specific views and token estimates. For large codebases, load only the categories you need.

Find the central explanation first. Then identify contributing factors.
Start from a concrete example before reasoning from abstractions.

### Workflow starting points

**Bug fix / investigation** — find the problem fast:

1. `codeir bearings` → orient
2. `codeir search` → find the most likely entity
3. `codeir show` → read its Behavior IR
4. `codeir expand` → read source, form your hypothesis

**Stop here** for specific fixes. If the source confirms your hypothesis, propose the fix. For investigations, continue to map the full control flow before concluding.

**Architecture / refactor** — understand before changing:

1. `codeir bearings` → orient on project structure
2. `codeir search` → find relevant entities
3. `codeir show` → understand behavior and call relationships
4. `codeir callers` / `codeir impact` → map what depends on your target
5. `codeir scope` → get the full context needed for safe modification
6. `codeir expand` → read source for entities you need to change

**Integration audit** — what needs to change to add X:

1. `codeir search` → find analogous existing integrations
2. `codeir expand` → inspect their definition, registration, and dispatch path
3. `codeir callers` → trace what else depends on those paths
4. `codeir grep "<existing_item_name>"` → find hidden couplings, hardcoded names, allowlists, prompts, counters, and assumptions

### Commands

**Bearings** — orientation context with automatic tiering:
```
codeir bearings                    # summary + menu with token estimates
codeir bearings [category]         # specific category (e.g., core_logic)
codeir bearings --full             # full module map
```

**Search** — find entities by name, file, or kind:
```
codeir search <query> [--category <category>]
```
Multiple terms use OR logic with ranking. Use `--category` to filter (e.g., `--category core_logic` to skip tests).

**Grep** — regex search across source files, grouped by entity:
```
codeir grep <pattern> [--path <dir_or_glob>] [-i] [-C N] [-v]
```
Use `--path` to scope (e.g., `--path orm/`). Use `-v` for full IR context per match.

**Inspect** — view what an entity does without reading source:
```
codeir show <entity_id> [--level Index|Behavior]
```
Index = what it is. Behavior = what it does and calls. Default is Behavior.

**Expand** — retrieve raw source when you need to edit or verify:
```
codeir expand <entity_id>
```

**Trace** — find what depends on an entity before changing it:
```
codeir callers <entity_id>
```
Results marked `~` are probable but not certain. Use `--resolution local` for same-file only.

**Impact** — reverse dependency analysis (BFS through callers):
```
codeir impact <entity_id> [--depth N]
```
Default depth 2. Shows affected entities grouped by distance, with dependency chain.

**Scope** — minimal context needed to safely modify an entity:
```
codeir scope <entity_id>
```
Returns the entity's callers, callees, and sibling methods (same class). Use before editing to understand what you might break and what the entity depends on.

### Reading compressed representations

Behavior fields:
- `FN` / `CLS` / `MT` / `AMT` — function, class, method, async method
- `C=` — calls made
- `F=` — flags: `R`=returns, `E`=raises, `I`=conditionals, `L`=loops, `T`=try/except, `W`=with
- `A=` — assignment count
- `B=` — base class
- `#TAG` — domain and category (e.g., `#DB #CORE`)

### Structural patterns

CodeIR detects recurring structural patterns — groups of 30+ classes sharing the same base class and role. Patterns appear in bearings under "Structural Patterns" and in `show` output for pattern members.

When you `show` a pattern member, the output highlights only how it **deviates** from its pattern — standard fields are labeled, not repeated. If you need the full IR, use `--full`.

Use `--patterns` on search to see pattern markers. Run `codeir patterns` for a summary of all detected patterns.
"""

_CODEX_SKILL_TEXT = """\
---
name: codeir
description: >
  Use this skill when exploring, understanding, searching, or modifying
  code in this repository. CodeIR provides a pre-built semantic index of
  the entire codebase — search by name, grep by content, inspect behavior
  summaries, trace callers and impact, and expand to source only when needed.
  Triggers: any code navigation, architecture questions, bug investigation,
  refactoring planning, or dependency analysis. Do NOT use for non-code tasks.
---

## CodeIR — Compiled Codebase Representation

This repository has a pre-built semantic index of the entire codebase.
Instead of reading raw source files, use CodeIR to search, inspect, and
trace entities at the abstraction level that matches your task.

For unfamiliar, cross-file, or architectural tasks, orient by running
`codeir bearings` before search, grep, or expand.

### Commands

**Bearings** — orient to the repo before narrowing:
```
codeir bearings
codeir bearings <category>
codeir bearings --full
```
Use this first when the task is unfamiliar, cross-cutting, or architectural.

**Search** — find entities by name:
```
codeir search <terms> [--category <cat>]
```
After `bearings`, prefer `--category` to narrow to the most likely area.

**Grep** — regex search across source, grouped by entity:
```
codeir grep <pattern> [--path <dir_or_glob>] [-i] [-C N] [-v]
codeir grep <pattern> --evidence [--path <dir_or_glob>] [-i]
codeir grep <pattern> --count [--path <dir_or_glob>]
```
Use `--evidence` instead of `rg -n ...` followed by `sed -n ...` when you
want exact matching lines, nearby context, and the owning entity in one call.

**Inspect** — compact behavior snapshots:
```
codeir show <entity_id> [--level Index|Behavior]
```

**Expand** — raw source when you need to edit or verify:
```
codeir expand <entity_id>
codeir expand <entity_id> --number     # with line numbers
codeir expand <id1> <id2> <id3>        # multiple entities
```

**Callers** — what depends on an entity:
```
codeir callers <entity_id>
```

**Impact** — reverse dependency analysis:
```
codeir impact <entity_id> [--depth N]
```

**Scope** — minimal context to safely modify an entity:
```
codeir scope <entity_id>
```

### Workflow

1. `codeir bearings` → orient
2. `codeir search "..." --category <cat>` → find candidates
3. `codeir show <id>` → read Behavior IR
4. `codeir expand <id>` → verify in source, then act

Use `callers`, `impact`, and `scope` when planning changes and you need
to understand blast radius.

### Reading compressed representations

Behavior fields:
- `FN` / `CLS` / `MT` / `AMT` — function, class, method, async method
- `C=` — calls made
- `F=` — flags: `R`=returns, `E`=raises, `I`=conditionals, `L`=loops, `T`=try/except, `W`=with
- `A=` — assignment count
- `B=` — base class
- `#TAG` — domain and category tags

### Annotated entity lists

Output from `callers`, `impact`, and `scope` includes inline triage metadata:
```
  CMPT.02         [47 callers] →ModelSQL   core_logic/tax.py      [class, ~180 lines]
  GTMVLN.03       [3 callers]              core_logic/move.py     [method, ~25 lines]
```

Results are smart-sorted (high-caller core logic first, tests last) and
truncated to 15 by default. Use `--all` to see the complete list.
"""


def _ensure_agent_rules(repo_path: Path) -> None:
    """Create .claude/rules/CodeIR.md and .agents/skills/codeir/SKILL.md.

    These files tell Claude and Codex that CodeIR is available in this repo.
    Only written if the files don't already exist (never overwrites user edits).
    """
    claude_path = repo_path / ".claude" / "rules" / "CodeIR.md"
    if not claude_path.exists():
        claude_path.parent.mkdir(parents=True, exist_ok=True)
        claude_path.write_text(_CLAUDE_RULES_TEXT, encoding="utf-8")

    codex_path = repo_path / ".agents" / "skills" / "codeir" / "SKILL.md"
    if not codex_path.exists():
        codex_path.parent.mkdir(parents=True, exist_ok=True)
        codex_path.write_text(_CODEX_SKILL_TEXT, encoding="utf-8")

    # AGENTS.md at repo root — Codex reads this for skill discovery.
    # Append if exists (don't clobber user content), create if not.
    agents_md_path = repo_path / "AGENTS.md"
    marker = "<!-- codeir-skill -->"
    if agents_md_path.exists():
        existing = agents_md_path.read_text(encoding="utf-8")
        if marker not in existing:
            with agents_md_path.open("a", encoding="utf-8") as f:
                f.write(f"\n\n{marker}\n{_CODEX_SKILL_TEXT}")
    else:
        agents_md_path.write_text(f"{marker}\n{_CODEX_SKILL_TEXT}", encoding="utf-8")


def cmd_search(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    results = search_entities(
        query=" ".join(args.query), repo_path=repo_path, limit=args.limit,
        category=getattr(args, "category", None),
    )
    if not results:
        query_str = " ".join(args.query)
        print(f"No entities found. Try: codeir grep \"{query_str}\" to search file contents.")
        return

    # Load pattern memberships if requested and patterns are enabled
    show_patterns = getattr(args, "patterns", False) and PATTERNS_ENABLED
    if show_patterns:
        from index.pattern_detector import get_entity_pattern
        db_path = repo_path / ".codeir" / "entities.db"

    for r in results:
        line_info = f", ~{r['line_count']} lines" if r.get('line_count') else ""
        if show_patterns:
            pattern_id = get_entity_pattern(db_path, r['entity_id'])
            pattern_marker = f" →{pattern_id}" if pattern_id else ""
            print(f"  {r['entity_id']:20s}{pattern_marker:15s}  {r['qualified_name']:40s}  {r['file_path']}:{r['line']}  [{r['kind']}{line_info}]")
        else:
            print(f"  {r['entity_id']:20s}  {r['qualified_name']:40s}  {r['file_path']}:{r['line']}  [{r['kind']}{line_info}]")


def cmd_show(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    any_found = False
    missing_ids = []

    for entity_id in args.entity_ids:
        result = get_entity_with_ir(repo_path=repo_path, entity_id=entity_id, mode=args.level)
        if not result:
            missing_ids.append(entity_id)
            continue

        if any_found:
            print()
        any_found = True

        start_line = int(result.get("start_line") or result["line"])
        end_line = int(result.get("end_line") or start_line)
        span = f"{start_line}-{end_line}" if end_line > start_line else str(start_line)

        # Header
        print(f"{result['qualified_name']} [{result['kind']}]  {result['file_path']}:{span}")

        # Determine if we should use smart pattern view
        use_smart_view = False
        pattern_details = None
        db_path = repo_path / ".codeir" / "entities.db"

        # Check global toggle and --full flag
        patterns_disabled = not PATTERNS_ENABLED or getattr(args, "full", False)

        if args.level == "Behavior" and not patterns_disabled:
            from index.pattern_detector import get_entity_pattern_details
            pattern_details = get_entity_pattern_details(db_path, entity_id)
            use_smart_view = pattern_details is not None

        if use_smart_view and pattern_details:
            # Smart pattern-aware view
            cat_suffix = f" in {pattern_details.category}" if pattern_details.category else ""
            print(f"\nPattern: {pattern_details.base_class} ({pattern_details.member_count} members{cat_suffix})")

            calls_str = ", ".join(pattern_details.common_calls[:5]) if pattern_details.common_calls else "-"
            flags_str = pattern_details.common_flags if pattern_details.common_flags else "-"
            print(f"  Standard calls: {calls_str}")
            print(f"  Standard flags: {flags_str}")

            # Deviations
            has_deviations = (
                pattern_details.extra_calls or
                pattern_details.extra_flags or
                pattern_details.missing_calls
            )
            if has_deviations:
                print(f"\nDeviations:")
                if pattern_details.extra_calls:
                    print(f"  Extra calls: {', '.join(pattern_details.extra_calls)}")
                if pattern_details.extra_flags:
                    print(f"  Extra flags: {pattern_details.extra_flags}")
                if pattern_details.missing_calls:
                    print(f"  Missing calls: {', '.join(pattern_details.missing_calls)}")
            else:
                print(f"\nDeviations: none (fully standard)")

            print(f"\nFull IR: codeir show {entity_id} --full")
        else:
            # Standard IR view (vanilla)
            ir_text = result['ir_text']

            # For Index level, add pattern marker if entity belongs to a pattern (unless patterns disabled)
            if args.level == "Index" and not patterns_disabled:
                from index.pattern_detector import get_entity_pattern
                pattern_id = get_entity_pattern(db_path, entity_id)
                if pattern_id:
                    # Insert pattern marker after entity ID
                    parts = ir_text.split(" ", 2)  # opcode, entity_id, rest
                    if len(parts) >= 2:
                        ir_text = f"{parts[0]} {parts[1]} →{pattern_id}"
                        if len(parts) > 2:
                            ir_text += f" {parts[2]}"

            print(f"{ir_text}")

    for entity_id in missing_ids:
        if any_found:
            print()
        print(f"Entity not found: {entity_id} (level={args.level})")
        any_found = True

    if missing_ids and len(missing_ids) == len(args.entity_ids):
        print("Run `codeir index <repo_path>` first.")


def cmd_expand(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()

    # Collect all entities from provided IDs (expanding wildcards)
    all_entities = []
    not_found = []

    for pattern in args.entity_ids:
        if pattern.endswith(".*"):
            # Wildcard pattern: STEM.*
            matches = get_entities_by_pattern(repo_path=repo_path, pattern=pattern)
            if matches:
                all_entities.extend(matches)
            else:
                not_found.append(pattern)
        else:
            # Exact entity ID
            loc = get_entity_location(repo_path=repo_path, entity_id=pattern)
            if loc:
                all_entities.append(loc)
            else:
                not_found.append(pattern)

    if not_found:
        for nf in not_found:
            print(f"Entity not found: {nf}")
        if not all_entities:
            return

    # Batch mode: multiple entities
    is_batch = len(all_entities) > 1

    if is_batch:
        print(f"=== Expanding {len(all_entities)} entities ===\n")

    def _format_source(source: str, start_line: int, number: bool) -> str:
        if not number:
            return source
        lines = source.splitlines()
        if not lines:
            return source
        width = len(str(start_line + len(lines) - 1))
        numbered = "\n".join(
            f"{start_line + i:>{width}d}: {line}" for i, line in enumerate(lines)
        )
        if source.endswith("\n"):
            numbered += "\n"
        return numbered

    for i, loc in enumerate(all_entities):
        if is_batch and i > 0:
            print("\n" + "─" * 60 + "\n")

        source = extract_code_slice(
            repo_path=repo_path,
            file_path=str(loc["file_path"]),
            start_line=int(loc["start_line"]),
            end_line=int(loc["end_line"]),
        )
        print(f"Entity: {loc['qualified_name']}  [{loc['kind']}]")
        print(f"File:   {loc['file_path']}:{loc['start_line']}-{loc['end_line']}")
        print(f"\n{_format_source(source, int(loc['start_line']), args.number)}")

    # Dependency summary footer (only for single entity)
    if not is_batch and all_entities:
        entity_id = all_entities[0]["entity_id"]
        db_path = repo_path / ".codeir" / "entities.db"
        if db_path.exists():
            conn = connect(db_path)
            try:
                totals = conn.execute("""
                    SELECT COUNT(*) as caller_count,
                           COUNT(DISTINCT caller_file) as file_count
                    FROM callers
                    WHERE entity_id = ?
                """, [entity_id]).fetchone()
                caller_count = totals[0] if totals else 0
                file_count = totals[1] if totals else 0

                if caller_count > 0:
                    top_files = conn.execute("""
                        SELECT caller_name, caller_file, COUNT(*) as refs
                        FROM callers
                        WHERE entity_id = ?
                        GROUP BY caller_file
                        ORDER BY refs DESC, caller_file
                        LIMIT 3
                    """, [entity_id]).fetchall()

                    caller_word = "caller" if caller_count == 1 else "callers"
                    file_word = "file" if file_count == 1 else "files"
                    print(f"\n\u26a0 {caller_count} {caller_word} across {file_count} {file_word}")
                    for row in top_files:
                        caller_name = row[0].split(".")[-1] if "." in row[0] else row[0]
                        file_path = row[1]
                        refs = row[2]
                        ref_word = "ref" if refs == 1 else "refs"
                        print(f"  {caller_name} ({file_path}) \u2014 {refs} {ref_word}")
                    if file_count > 3:
                        print(f"  Run `codeir callers {entity_id}` for full list.")
            finally:
                conn.close()


def cmd_compare(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    levels = get_entity_all_levels(repo_path=repo_path, entity_id=args.entity_id)
    if not levels:
        print(f"Entity not found: {args.entity_id}")
        print("Run `codeir index <repo> --level all` to generate all compression levels.")
        return

    first = levels[0]
    print(f"## {first['entity_id']} ({first['qualified_name']})")
    print(f"File: {first['file_path']}:{first['start_line']}-{first['end_line']}  [{first['kind']}]")

    # Show source (location already available from first level row)
    source = extract_code_slice(
        repo_path=repo_path,
        file_path=str(first["file_path"]),
        start_line=int(first["start_line"]),
        end_line=int(first["end_line"]),
    )
    if source:
        src_tokens = first.get("source_token_count", "?")
        print(f"\n### Source ({src_tokens} tokens):")
        print(source)

    for row in levels:
        ir_tokens = row.get("ir_token_count", "?")
        ratio = row.get("compression_ratio", "?")
        if isinstance(ratio, float):
            ratio = f"{ratio:.2f}"
        print(f"\n### {row['mode']} ({ir_tokens} tokens, ratio {ratio}):")
        print(row["ir_text"])


def cmd_callers(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Get entity name for ambiguity check
        entity_row = conn.execute(
            "SELECT name, qualified_name FROM entities WHERE id = ?",
            [args.entity_id]
        ).fetchone()
        entity_name = entity_row["name"] if entity_row else args.entity_id

        # Get resolved callers
        sql = """
            SELECT caller_id, caller_name, caller_file, resolution
            FROM callers
            WHERE entity_id = ?
        """
        params: list = [args.entity_id]

        if args.resolution:
            sql += " AND resolution = ?"
            params.append(args.resolution)

        sql += " ORDER BY resolution, caller_name"
        rows = conn.execute(sql, params).fetchall()

        # Get annotations for all callers
        caller_ids = [row["caller_id"] for row in rows]
        annotations = get_entity_annotations(conn, caller_ids)

        # Check for ambiguous calls - entities that call this name but weren't resolved
        # Look for calls_json containing ".{name}" pattern (qualified calls)
        ambiguous_pattern = f'%.{entity_name}"%'
        ambiguous_sql = """
            SELECT id, qualified_name, file_path, calls_json
            FROM entities
            WHERE calls_json LIKE ?
            AND id NOT IN (SELECT caller_id FROM callers WHERE entity_id = ?)
        """
        ambiguous_rows = conn.execute(ambiguous_sql, [ambiguous_pattern, args.entity_id]).fetchall()

        # Count how many entities share this name (for context)
        name_count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE name = ?", [entity_name]
        ).fetchone()[0]

    finally:
        conn.close()

    if not rows and not ambiguous_rows:
        print(f"No callers found for: {args.entity_id}")
        return

    # Convert to list of dicts for sorting
    caller_list = [
        {
            "entity_id": row["caller_id"],
            "file_path": row["caller_file"],
            "resolution": row["resolution"],
        }
        for row in rows
    ]

    # Smart sort and truncate
    show_all = getattr(args, "show_all", False)
    sorted_callers, total, was_truncated = smart_truncate_entities(
        caller_list, annotations, show_all=show_all
    )

    # Print header
    if was_truncated:
        print(f"Callers of {args.entity_id} (showing {len(sorted_callers)} of {total}):")
    else:
        print(f"Callers of {args.entity_id} ({total} callers):")

    # Print resolved callers with annotations
    for caller in sorted_callers:
        marker = "~" if caller["resolution"] == "fuzzy" else " "
        print(format_annotated_entity(
            entity_id=caller["entity_id"],
            file_path=caller["file_path"],
            annotations=annotations,
            marker=marker,
        ))

    # Print truncation notice
    if was_truncated:
        remaining = total - len(sorted_callers)
        print(f"\n  ... and {remaining} more — run `codeir callers {args.entity_id} --all` for complete list")

    # Print ambiguous callers with actionable info
    if ambiguous_rows:
        print(f"\n⚠ Ambiguous calls ({len(ambiguous_rows)} potential callers, {name_count} entities named '{entity_name}'):")
        for row in ambiguous_rows[:5]:  # Show top 5
            # Extract the matching call from calls_json
            import json
            try:
                calls = json.loads(row["calls_json"])
                matching = [c for c in calls if c.endswith(f".{entity_name}")]
                call_str = matching[0] if matching else entity_name
            except:
                call_str = entity_name
            print(f"   {row['id']:20s}  calls {call_str:30s}  {row['file_path']}")

        if len(ambiguous_rows) > 5:
            print(f"   ... and {len(ambiguous_rows) - 5} more")

        print(f"\n💡 Suggestions:")
        print(f"   codeir grep '\\.{entity_name}\\(' --path <dir>")
        print(f"   codeir grep '{entity_name}\\(' --path <relevant_dir>")


def cmd_impact(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    result = compute_impact(conn, args.entity_id, depth=args.depth, level=args.level)

    root = result["root"]
    if not root:
        conn.close()
        print(f"Entity not found: {args.entity_id}")
        return

    # Collect all entity IDs for annotations
    impact_by_depth = result["impact_by_depth"]
    all_entity_ids = []
    for items in impact_by_depth.values():
        all_entity_ids.extend(item["entity_id"] for item in items)

    annotations = get_entity_annotations(conn, all_entity_ids)
    conn.close()

    print(f"Impact analysis for: {root['qualified_name']}  [{root['kind']}]")
    print(f"File: {root['file_path']}:{root['start_line']}")
    if root["ir_text"]:
        print(f"IR:   {root['ir_text']}")
    print()

    excluded_areas = {
        ("test" if area == "tests" else area)
        for area in (getattr(args, "exclude_area", None) or [])
    }
    if excluded_areas:
        filtered_by_depth = {}
        for depth, items in impact_by_depth.items():
            kept = [item for item in items if _area_for_path(item["file_path"]) not in excluded_areas]
            if kept:
                filtered_by_depth[depth] = kept
        impact_by_depth = filtered_by_depth

    total_affected = sum(len(items) for items in impact_by_depth.values())
    if total_affected == 0:
        if excluded_areas:
            print(f"All downstream dependents were excluded by area filter: {', '.join(sorted(excluded_areas))}")
        else:
            print("No downstream dependents found.")
        return

    area_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    file_counts: Dict[str, int] = {}
    for items in impact_by_depth.values():
        for item in items:
            area = _area_for_path(item["file_path"])
            area_counts[area] = area_counts.get(area, 0) + 1
            file_counts[item["file_path"]] = file_counts.get(item["file_path"], 0) + 1
            category = item.get("category")
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1

    print(f"Affected: {total_affected} entities across {len(file_counts)} files")
    depth_summary = ", ".join(f"d{depth}={len(items)}" for depth, items in sorted(impact_by_depth.items()))
    if depth_summary:
        print(f"By depth: {depth_summary}")
    if area_counts:
        area_summary = " ".join(
            f"{area}={count}" for area, count in sorted(area_counts.items(), key=lambda kv: (kv[0] != 'lib', kv[0]))
        )
        print(f"By area: {area_summary}")
    if category_counts:
        category_summary = " ".join(
            f"{cat}={count}" for cat, count in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        )
        print(f"By category: {category_summary}")
    if file_counts:
        top_files = ", ".join(
            f"{path}={count}" for path, count in sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        )
        print(f"Top files: {top_files}")
    if excluded_areas:
        print(f"Excluded areas: {', '.join(sorted(excluded_areas))}")
    print()

    show_all = getattr(args, "show_all", False)
    any_truncated = False

    for depth in sorted(impact_by_depth.keys()):
        items = impact_by_depth[depth]

        # Smart sort and truncate each depth level
        sorted_items, total, was_truncated = smart_truncate_entities(
            items, annotations, show_all=show_all
        )
        if was_truncated:
            any_truncated = True

        label = "direct" if depth == 1 else f"depth {depth}"
        if was_truncated:
            print(f"--- {label} (showing {len(sorted_items)} of {total}) ---")
        else:
            print(f"--- {label} ({total} entities) ---")

        for item in sorted_items:
            marker = "~" if item["resolution"] == "fuzzy" else " "
            print(format_annotated_entity(
                entity_id=item["entity_id"],
                file_path=item["file_path"],
                annotations=annotations,
                marker=marker,
            ))
            if depth > 1:
                print(f"  {'':20s}  via: {item['via']}")

        if was_truncated:
            remaining = total - len(sorted_items)
            print(f"  ... and {remaining} more at this depth")
        print()

    if any_truncated:
        print(f"Run `codeir impact {args.entity_id} --all` for complete list")


def cmd_scope(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    result = compute_scope(conn, args.entity_id, level=args.level)

    root = result["root"]
    if not root:
        conn.close()
        print(f"Entity not found: {args.entity_id}")
        return

    # Collect all entity IDs for annotations
    all_entity_ids = []
    for group in [result["callers"], result["callees"], result["siblings"]]:
        all_entity_ids.extend(item["entity_id"] for item in group)

    annotations = get_entity_annotations(conn, all_entity_ids)
    conn.close()

    print(f"Scope for: {root['qualified_name']}  [{root['kind']}]")
    print(f"File: {root['file_path']}:{root['start_line']}")
    if root["ir_text"]:
        print(f"IR:   {root['ir_text']}")
    print()

    show_all = getattr(args, "show_all", False)
    any_truncated = False

    def _print_group(label: str, items: list) -> bool:
        """Print a group with smart truncation. Returns True if truncated."""
        nonlocal any_truncated
        if not items:
            return False

        sorted_items, total, was_truncated = smart_truncate_entities(
            items, annotations, show_all=show_all
        )
        if was_truncated:
            any_truncated = True

        if was_truncated:
            print(f"--- {label} (showing {len(sorted_items)} of {total}) ---")
        else:
            print(f"--- {label} ({total}) ---")

        for item in sorted_items:
            marker = "~" if item.get("resolution") == "fuzzy" else " "
            print(format_annotated_entity(
                entity_id=item["entity_id"],
                file_path=item["file_path"],
                annotations=annotations,
                marker=marker,
            ))

        if was_truncated:
            remaining = total - len(sorted_items)
            print(f"  ... and {remaining} more")
        print()
        return was_truncated

    _print_group("callers (what calls this)", result["callers"])
    _print_group("callees (what this calls)", result["callees"])
    _print_group("siblings (same class)", result["siblings"])

    total = len(result["callers"]) + len(result["callees"]) + len(result["siblings"])
    if total == 0:
        print("No related entities found.")
    else:
        print(f"Total: {total} entities in scope")
        if any_truncated:
            print(f"Run `codeir scope {args.entity_id} --all` for complete list")


def cmd_trace(args: argparse.Namespace) -> None:
    """Find shortest call path between two entities using BFS."""
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    from_id = args.from_entity.upper()
    to_id = args.to_entity.upper()
    max_depth = args.depth
    resolution_filter = args.resolution

    # Verify both entities exist
    from_entity = conn.execute(
        "SELECT id, qualified_name, file_path, start_line, kind FROM entities WHERE id = ?",
        (from_id,)
    ).fetchone()
    to_entity = conn.execute(
        "SELECT id, qualified_name, file_path, start_line, kind FROM entities WHERE id = ?",
        (to_id,)
    ).fetchone()

    if not from_entity:
        print(f"Entity not found: {from_id}")
        conn.close()
        return
    if not to_entity:
        print(f"Entity not found: {to_id}")
        conn.close()
        return

    if from_id == to_id:
        print("Call path found (0 hops):")
        print()
        print(f"  {from_entity['id']:<16} {from_entity['qualified_name']:<40} [{from_entity['kind']}]")
        print(f"  {'':16} {from_entity['file_path']}:{from_entity['start_line']}")
        print()
        print("(static call path - does not guarantee runtime execution order)")
        conn.close()
        return

    # Build resolution filter clause
    if resolution_filter == "any":
        res_clause = ""
        res_params = []
    elif resolution_filter == "fuzzy":
        # Include all resolutions (fuzzy is lowest confidence, so include everything)
        res_clause = ""
        res_params = []
    elif resolution_filter == "local":
        # Include local and import (exclude fuzzy)
        res_clause = " AND resolution IN ('local', 'import')"
        res_params = []
    elif resolution_filter == "import":
        # Only import resolution
        res_clause = " AND resolution = 'import'"
        res_params = []
    else:
        res_clause = ""
        res_params = []

    # BFS to find shortest path
    # Forward edges: SELECT entity_id FROM callers WHERE caller_id = ?
    # (entity_id is the callee, caller_id is who's calling)
    from collections import deque

    queue = deque([(from_id, [from_id])])
    visited = {from_id}

    found_path = None

    while queue and not found_path:
        current, path = queue.popleft()

        if len(path) > max_depth:
            continue

        # Get forward edges (what does current call?)
        query = f"SELECT entity_id, resolution FROM callers WHERE caller_id = ?{res_clause}"
        callees = conn.execute(query, (current,)).fetchall()

        for row in callees:
            callee_id = row["entity_id"]
            resolution = row["resolution"]

            if callee_id == to_id:
                found_path = path + [callee_id]
                break

            if callee_id not in visited:
                visited.add(callee_id)
                queue.append((callee_id, path + [callee_id]))

    if not found_path:
        print(f"No call path found from {from_id} to {to_id}")
        print(f"  (searched {len(visited)} entities, max depth {max_depth})")
        if resolution_filter != "any":
            print(f"  (resolution filter: {resolution_filter})")
        conn.close()
        return

    # Fetch entity details for the path
    path_entities = []
    for entity_id in found_path:
        row = conn.execute(
            "SELECT id, qualified_name, file_path, start_line, kind FROM entities WHERE id = ?",
            (entity_id,)
        ).fetchone()
        if row:
            path_entities.append(dict(row))
        else:
            path_entities.append({"id": entity_id, "qualified_name": "?", "file_path": "?", "start_line": 0, "kind": "?"})

    conn.close()

    # Output
    hops = len(found_path) - 1
    print(f"Call path found ({hops} hop{'s' if hops != 1 else ''}):")
    print()

    for i, entity in enumerate(path_entities):
        indent = "  "
        name = entity["qualified_name"]
        loc = f"{entity['file_path']}:{entity['start_line']}"
        kind = entity["kind"]

        print(f"{indent}{entity['id']:<16} {name:<40} [{kind}]")
        print(f"{indent}{'':16} {loc}")

        if i < len(path_entities) - 1:
            print(f"{indent}{'':16} |")
            print(f"{indent}{'':16} v calls")

    print()
    print("(static call path - does not guarantee runtime execution order)")


def _print_matches(matches: list, max_matches: int | None = None) -> int:
    """Print match lines with optional context, deduplicating overlapping context."""
    if max_matches is not None:
        matches = matches[:max_matches]

    pad = f"  {'':20s}    "
    shown_lines: set = set()
    for i, m in enumerate(matches):
        # Context before
        for ctx in m.get("context_before", []):
            if ctx["line"] not in shown_lines:
                shown_lines.add(ctx["line"])
                print(f"{pad}{ctx['line']:>5d}  {ctx['text']}")
        # Match line (marked with arrow)
        marker = " ←" if m.get("context_before") or m.get("context_after") else ""
        shown_lines.add(m["line"])
        print(f"{pad}{m['line']:>5d}: {m['text']}{marker}")
        # Context after
        for ctx in m.get("context_after", []):
            if ctx["line"] not in shown_lines:
                shown_lines.add(ctx["line"])
                print(f"{pad}{ctx['line']:>5d}  {ctx['text']}")
        # Separator between non-adjacent match groups
        if i < len(matches) - 1:
            next_m = matches[i + 1]
            next_start = next_m.get("context_before", [])
            next_first_line = next_start[0]["line"] if next_start else next_m["line"]
            last_shown = m.get("context_after", [])
            last_line = last_shown[-1]["line"] if last_shown else m["line"]
            if next_first_line > last_line + 1:
                print(f"{pad}  ...")
    return len(matches)


def _area_for_path(file_path: str) -> str:
    normalized = (file_path or "").lstrip("./")
    if normalized.startswith("lib/"):
        return "lib"
    if normalized.startswith("test/") or normalized.startswith("tests/"):
        return "test"
    if normalized.startswith("examples/"):
        return "examples"
    if normalized.startswith("doc/") or normalized.startswith("docs/"):
        return "docs"
    return "other"


def cmd_grep(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    evidence = getattr(args, "evidence", False)
    context = args.context if args.context else (2 if evidence else 0)
    try:
        results = grep_entities(
            pattern=args.pattern,
            repo_path=repo_path,
            level=args.level,
            limit=args.limit,
            ignore_case=args.ignore_case,
            context=context,
            path_filter=args.path,
        )
    except FileNotFoundError:
        print("No index found. Run `codeir index <repo_path>` first.")
        return
    except ValueError as exc:
        print(str(exc))
        return

    if not results:
        print("No matches found.")
        return

    total_matches = sum(len(r["matches"]) for r in results)
    entity_groups = sum(1 for r in results if r["type"] == "entity")
    file_groups = sum(1 for r in results if r["type"] == "file")
    count_only = getattr(args, "count", False)
    print(f"{total_matches} matches across {entity_groups} entities and {file_groups} unmatched regions\n")

    verbose = getattr(args, "verbose", False) or evidence
    max_matches = 3 if evidence else None
    if count_only:
        sorted_results = sorted(
            results,
            key=lambda group: (-len(group["matches"]), group.get("file_path", ""), group.get("entity_id", "")),
        )
        for group in sorted_results:
            match_count = len(group["matches"])
            if group["type"] == "entity":
                print(
                    f"  {match_count:>5}  {group['entity_id']:20s}  "
                    f"{group['qualified_name']}  {group['file_path']}:{group['start_line']}-{group['end_line']}"
                )
            else:
                print(f"  {match_count:>5}  {'(no entity)':20s}  {group['file_path']}")
        return

    for group in results:
        match_count = len(group["matches"])
        if group["type"] == "entity":
            print(f"  {group['entity_id']:20s}  {group['qualified_name']}  [{group['kind']}]")
            print(f"  {'':20s}  {group['file_path']}:{group['start_line']}-{group['end_line']}  ({match_count} matches)")
            if verbose:
                ir_text = group.get("ir_text") or "(no IR at this level)"
                print(f"  {'':20s}  IR: {ir_text}")
            shown = _print_matches(group["matches"], max_matches=max_matches)
            if evidence and match_count > shown:
                print(f"  {'':20s}    ... {match_count - shown} more matches in this entity")
            print()
        else:
            print(f"  {'(no entity)':20s}  {group['file_path']}  ({match_count} matches)")
            shown = _print_matches(group["matches"], max_matches=max_matches)
            if evidence and match_count > shown:
                print(f"  {'':20s}    ... {match_count - shown} more matches in this file")
            print()


def cmd_stats(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    stats = get_stats(repo_path)

    print(f"Entities:  {stats['entity_count']}")
    print(f"Language:  {stats.get('source_language', 'python')}")
    for kind_info in stats["entities_by_kind"]:
        print(f"  {kind_info['kind']:20s}  {kind_info['count']}")

    fc = stats["file_coverage"]
    print(f"\nFile coverage: {fc['files_with_entities']}/{fc['source_files_indexed']} ({fc['coverage_percent']:.1f}%)")

    print(f"\nCompression level: {stats.get('compression_level', 'unknown')}")
    c = stats["compression"]
    print(f"  Source tokens: {c['source_token_count']:,}")
    print(f"  IR tokens:     {c['ir_token_count']:,}")
    print(f"  Global ratio:  {c['global_ratio']:.4f}")
    print(f"  Avg ratio:     {c['avg_entity_ratio']:.4f}")

    # Per-level stats
    level_stats = stats.get("level_stats", {})
    if level_stats:
        print("\nPer-level breakdown:")
        for mode, ls in sorted(level_stats.items()):
            print(f"  {mode}: {ls['entity_count']} entities, "
                  f"{ls['ir_tokens']:,} IR tokens, "
                  f"ratio {ls['ratio']:.4f}, "
                  f"~{ls['entities_per_200k']:,} entities/200k ctx")

    # Per-category stats
    category_stats = stats.get("category_stats", [])
    if category_stats:
        print("\nModule categories:")
        for cs in category_stats:
            print(f"  {cs['category']:15s}  {cs['file_count']} files, {cs['entity_count']} entities")

    # Complexity distribution
    complexity_stats = stats.get("complexity_stats", {})
    if complexity_stats:
        print("\nComplexity distribution:")
        for cls, count in sorted(complexity_stats.items()):
            print(f"  {cls:10s}  {count}")

    print(f"\nAbbreviations: {stats['abbreviation_count']}")


def cmd_module_map(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        cols = column_names(conn, "modules")
        if not cols:
            print("No module classifications found. Re-index to generate module map.")
            return

        has_deps = "deps_internal" in cols
        select = "file_path, category, entity_count"
        if has_deps:
            select += ", deps_internal"

        modules = conn.execute(
            f"SELECT {select} FROM modules ORDER BY category, file_path"
        ).fetchall()
    finally:
        conn.close()

    if not modules:
        print("No modules indexed.")
        return

    # Group by category
    categories: Dict[str, list] = {}
    for row in modules:
        deps = row["deps_internal"] if has_deps else ""
        categories.setdefault(row["category"], []).append(
            (row["file_path"], row["entity_count"], deps)
        )

    repo_name = repo_path.name
    print(f"# Module Map: {repo_name}\n")
    for category in sorted(categories.keys()):
        files = categories[category]
        total_entities = sum(ec for _, ec, _ in files)
        print(f"## {category} ({len(files)} files, {total_entities} entities)")
        for file_path, entity_count, deps in files:
            deps_str = f"  deps: {deps}" if deps else ""
            print(f"  {file_path} — {entity_count} entities{deps_str}")
        print()


def _compute_module_ids(modules: list) -> Dict[str, str]:
    """Assign deterministic module IDs with collision suffixes."""
    from ir.stable_ids import make_module_base_id

    by_base: Dict[str, list] = {}
    for mod in modules:
        fp = str(mod["file_path"]) if isinstance(mod, dict) else str(mod[0])
        base = make_module_base_id(fp)
        by_base.setdefault(base, []).append(fp)
    result: Dict[str, str] = {}
    for base, paths in by_base.items():
        paths.sort()
        for idx, fp in enumerate(paths, start=1):
            result[fp] = base if idx == 1 else f"{base}_{idx:02d}"
    return result


def _get_bearings_paths(repo_path: Path, legacy: bool = False) -> Dict[str, Path]:
    """Return the canonical bearings file paths for a repository."""
    base_dir = repo_path / (".claude" if legacy else ".codeir")
    return {
        "base": base_dir,
        "summary": base_dir / "bearings-summary.md",
        "map": base_dir / "bearings.md",
        "categories": base_dir / "bearings",
    }


def _resolve_bearings_paths(repo_path: Path) -> tuple[Dict[str, Path], bool]:
    """Return the preferred readable bearings paths and whether they are legacy."""
    current_paths = _get_bearings_paths(repo_path)
    if current_paths["summary"].exists():
        return current_paths, False

    legacy_paths = _get_bearings_paths(repo_path, legacy=True)
    if legacy_paths["summary"].exists():
        return legacy_paths, True

    return current_paths, False


def _generate_bearings_files(repo_path: Path) -> None:
    """Generate all bearings files (summary, full, per-category)."""
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            "SELECT file_path, category, entity_count, deps_internal "
            "FROM modules ORDER BY category, file_path"
        ).fetchall()
    except sqlite3.OperationalError:
        print("No module classifications found. Re-index to generate bearings.")
        conn.close()
        return

    try:
        total = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    except sqlite3.OperationalError:
        total = 0
    finally:
        conn.close()

    modules = [
        {"file_path": row["file_path"], "category": row["category"],
         "entity_count": row["entity_count"], "deps_internal": row["deps_internal"]}
        for row in rows
    ]

    module_ids = _compute_module_ids(modules)

    from ir.classifier import generate_context_file, generate_summary, generate_category_file

    paths = _get_bearings_paths(repo_path)
    paths["base"].mkdir(parents=True, exist_ok=True)

    # Tier 1: bearings-summary.md
    summary_content = generate_summary(repo_path.name, modules, total)
    paths["summary"].write_text(summary_content, encoding="utf-8")

    # Tier 2: bearings.md (collapsed working map)
    bearings_content = generate_context_file(repo_path.name, modules, total, module_ids)
    paths["map"].write_text(bearings_content, encoding="utf-8")

    # Tier 3: bearings/{category}.md (full uncollapsed per category)
    paths["categories"].mkdir(parents=True, exist_ok=True)

    by_cat: Dict[str, list] = {}
    for mod in modules:
        by_cat.setdefault(mod["category"], []).append(mod)

    for category, cat_mods in by_cat.items():
        cat_content = generate_category_file(repo_path.name, category, cat_mods, module_ids, db_path=db_path)
        cat_path = paths["categories"] / f"{category}.md"
        cat_path.write_text(cat_content, encoding="utf-8")

    print(f"Generated bearings ({len(modules)} modules, {total} entities):")
    print(f"  Summary:    {paths['summary']}")
    print(f"  Working map:{paths['map']}")
    print(f"  Categories: {paths['categories']}/ ({len(by_cat)} files)")


def _estimate_tokens(file_path: Path) -> int:
    """Estimate token count from file size (chars / 4)."""
    if not file_path.exists():
        return 0
    return file_path.stat().st_size // 4


def cmd_bearings(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()

    # --generate mode: regenerate all files
    if args.generate:
        _generate_bearings_files(repo_path)
        return

    # Check if bearings files exist
    paths, using_legacy_paths = _resolve_bearings_paths(repo_path)
    summary_path = paths["summary"]
    bearings_path = paths["map"]
    bearings_dir = paths["categories"]

    if not summary_path.exists():
        print("No bearings files found. Run `codeir bearings --generate` first.")
        return

    if using_legacy_paths:
        print("Using legacy bearings files from `.claude/`. Run `codeir bearings --generate` to migrate them to `.codeir/`.")
        print()

    # --full mode: output full bearings.md
    if args.full:
        if not bearings_path.exists():
            print("bearings.md not found. Run `codeir bearings --generate` first.")
            return
        print(bearings_path.read_text(encoding="utf-8"))
        return

    # Category mode: output specific category file
    if args.category:
        cat_path = bearings_dir / f"{args.category}.md"
        if not cat_path.exists():
            # List available categories
            available = [f.stem for f in bearings_dir.glob("*.md")] if bearings_dir.exists() else []
            print(f"Category '{args.category}' not found.")
            if available:
                print(f"Available: {', '.join(sorted(available))}")
            return
        print(cat_path.read_text(encoding="utf-8"))
        return

    # Default: show summary + menu with token estimates
    print(summary_path.read_text(encoding="utf-8"))

    # Build menu with token estimates
    full_tokens = _estimate_tokens(bearings_path)
    print(f"---")
    print(f"Full bearings: `codeir bearings --full` (~{full_tokens:,} tokens)")

    if bearings_dir.exists():
        cat_files = sorted(bearings_dir.glob("*.md"))
        if cat_files:
            print(f"\nBy category:")
            for cat_path in cat_files:
                cat_tokens = _estimate_tokens(cat_path)
                print(f"  {cat_path.stem:15s}  `codeir bearings {cat_path.stem}` (~{cat_tokens:,} tokens)")


def cmd_patterns(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    from index.pattern_detector import detect_patterns, get_patterns

    # Detect patterns if not already done
    patterns = get_patterns(db_path, category=args.category, include_tests=args.include_tests)

    if not patterns:
        # Try detecting first
        detected = detect_patterns(db_path, min_size=args.min_size)
        patterns = [p for p in detected if args.include_tests or not p.is_test_pattern]
        if args.category:
            patterns = [p for p in patterns if p.category == args.category]

    if not patterns:
        print(f"No patterns found with ≥{args.min_size} members.")
        if not args.include_tests:
            print("Try --include-tests to see test patterns.")
        return

    # Group by category
    by_cat: Dict[str, list] = {}
    for p in patterns:
        by_cat.setdefault(p.category or "uncategorized", []).append(p)

    total_coverage = sum(p.member_count for p in patterns)

    print(f"Structural Patterns (≥{args.min_size} members):\n")

    for category in sorted(by_cat.keys()):
        cat_patterns = sorted(by_cat[category], key=lambda p: -p.member_count)
        cat_coverage = sum(p.member_count for p in cat_patterns)
        print(f"{category}: ({cat_coverage} entities in {len(cat_patterns)} patterns)")

        for p in cat_patterns:
            calls_str = ", ".join(p.common_calls[:4]) if p.common_calls else "-"
            flags_str = p.common_flags if p.common_flags else "-"
            test_marker = " [test]" if p.is_test_pattern else ""
            print(f"  {p.base_class:20s} {p.member_count:4d} classes   "
                  f"calls: {calls_str:30s}  flags: {flags_str}{test_marker}")
        print()

    print(f"Total: {total_coverage} entities in {len(patterns)} patterns")


def cmd_rules(args: argparse.Namespace) -> None:
    repo_path = (args.repo_path or args.repo_path_flag).resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    from ir.rules_generator import generate_rules_file

    try:
        content = generate_rules_file(repo_path)
    except ValueError as exc:
        print(str(exc))
        return

    output = args.output or (repo_path / ".claude" / "rules" / "CodeIR.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Generated rules: {output}")


def cmd_eval(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    cfg = load_config(repo_path)
    levels = list(args.levels)

    # Backward-compatible mode aliases (a,b,hybrid) -> levels.
    if args.modes:
        mode_parts = [m.strip().lower() for m in str(args.modes).split(",") if m.strip()]
        mapped_levels = [map_legacy_mode_to_level(m) for m in mode_parts]
        if mapped_levels:
            # Preserve order while removing duplicates.
            seen = set()
            levels = []
            for lvl in mapped_levels:
                if lvl not in seen:
                    seen.add(lvl)
                    levels.append(lvl)

    print(f"Evaluating compression levels: {levels}")
    print()

    results = {}
    for level in levels:
        cfg_copy = dict(cfg)
        cfg_copy["compression_level"] = level
        stats = index_repo(repo_path, cfg_copy)
        level_stats = get_stats(repo_path)
        results[level] = {
            "stats": stats,
            "full_stats": level_stats,
        }
        c = level_stats["compression"]
        print(f"{level}: {c['source_token_count']:,} src tokens -> {c['ir_token_count']:,} IR tokens "
              f"(ratio {c['global_ratio']:.4f})")

    # Summary
    print("\n--- Summary ---")
    print(f"{'Level':<8} {'IR Tokens':>12} {'Ratio':>10} {'Entities/200k':>15}")
    for level in levels:
        ls = results[level]["full_stats"].get("level_stats", {}).get(level, {})
        c = results[level]["full_stats"]["compression"]
        e200k = ls.get("entities_per_200k", "N/A")
        print(f"{level:<8} {c['ir_token_count']:>12,} {c['global_ratio']:>10.4f} {e200k:>15}")

    if args.output:
        args.output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"\nResults written to {args.output}")


def cmd_floor_test(args: argparse.Namespace) -> None:
    if args.floor_action == "generate":
        from tests.eval.floor_test import generate_test_pack

        repo_path = args.repo_path.resolve()
        pack = generate_test_pack(
            repo_path=repo_path,
            compression_level=args.level,
            entity_count=args.count,
            seed=args.seed,
        )
        output = args.output or Path(f"floor_test_pack_{args.level}.json")
        output.write_text(json.dumps(pack, indent=2), encoding="utf-8")
        print(f"Generated {len(pack['tests'])} tests for {pack['entity_count']} entities at {pack['level']}")
        print(f"Output: {output}")

    elif args.floor_action == "score":
        from tests.eval.eval import floor_report, render_floor_matrix_markdown

        report = floor_report(args.results_path)
        md = render_floor_matrix_markdown(report)
        print(md)

        if args.output:
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"\nFloor report written to {args.output}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "init": cmd_init,
        "index": cmd_index,
        "search": cmd_search,
        "show": cmd_show,
        "expand": cmd_expand,
        "compare": cmd_compare,
        "callers": cmd_callers,
        "impact": cmd_impact,
        "scope": cmd_scope,
        "trace": cmd_trace,
        "grep": cmd_grep,
        "stats": cmd_stats,
        "module-map": cmd_module_map,
        "bearings": cmd_bearings,
        "patterns": cmd_patterns,
        "rules": cmd_rules,
        "eval": cmd_eval,
        "floor-test": cmd_floor_test,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
