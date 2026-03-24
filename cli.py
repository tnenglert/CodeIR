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

DEFAULT_CONFIG: Dict[str, Any] = {
    "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".codeir"],
    "extensions": [".py"],
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
    p_init = sub.add_parser("init", help="Full setup: index + bearings + rules")
    p_init.add_argument("repo_path", type=Path)
    p_init.add_argument("--level", default=None, help="Compression level (default: Behavior+Index)")

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
    p_show.add_argument("entity_id")
    p_show.add_argument("--repo-path", type=Path, default=Path("."))
    p_show.add_argument("--level", default="Behavior", help="Compression level to show")
    p_show.add_argument("--full", action="store_true", help="Show full IR (skip smart pattern view)")

    # expand
    p_expand = sub.add_parser("expand", help="Show raw source for entities (supports STEM.* wildcards)")
    p_expand.add_argument("entity_ids", nargs="+", metavar="ENTITY_ID",
                          help="One or more entity IDs, or STEM.* for all siblings")
    p_expand.add_argument("--repo-path", type=Path, default=Path("."))

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

    # impact
    p_impact = sub.add_parser("impact", help="Reverse dependency analysis — what breaks if this changes")
    p_impact.add_argument("entity_id")
    p_impact.add_argument("--repo-path", type=Path, default=Path("."))
    p_impact.add_argument("--depth", type=int, default=2, help="Max traversal depth (default: 2)")
    p_impact.add_argument("--level", default="Behavior", help="IR level to show (default: Behavior)")

    # scope
    p_scope = sub.add_parser("scope", help="Minimal context needed to safely modify an entity")
    p_scope.add_argument("entity_id")
    p_scope.add_argument("--repo-path", type=Path, default=Path("."))
    p_scope.add_argument("--level", default="Behavior", help="IR level to show (default: Behavior)")

    # grep
    p_grep = sub.add_parser("grep", help="Grep source files with IR context")
    p_grep.add_argument("pattern", help="Regex pattern to search for")
    p_grep.add_argument("--repo-path", type=Path, default=Path("."))
    p_grep.add_argument("--level", default="Behavior", help="IR level to attach (default: Behavior)")
    p_grep.add_argument("--limit", type=int, default=50, help="Max result groups (default: 50)")
    p_grep.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive matching")
    p_grep.add_argument("-C", "--context", type=int, default=0, help="Show N surrounding lines per match (like grep -C)")
    p_grep.add_argument("--path", default=None, help="Scope to directory or glob (e.g., orm/ or 'engine/*.py')")
    p_grep.add_argument("-v", "--verbose", action="store_true", help="Include IR context for each entity match")

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
    """Full setup: index a repository, then generate bearings and rules."""
    repo_path = args.repo_path.resolve()
    cfg = load_config(repo_path)
    if args.level:
        cfg["compression_level"] = args.level

    # 1. Index
    print(f"Indexing {repo_path} ...")
    result = index_repo(repo_path, cfg)
    if result.get("status") == "no_changes":
        print(f"No changes detected. {result.get('files_scanned', 0)} files scanned.")
    else:
        print(f"  {result.get('total_entities', 0)} entities, "
              f"{result.get('files_changed', 0)} files changed")

    # 2. Bearings
    db_path = repo_path / ".codeir" / "entities.db"
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

    claude_dir = repo_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    summary_content = generate_summary(repo_path.name, modules, total)
    (claude_dir / "bearings-summary.md").write_text(summary_content, encoding="utf-8")

    bearings_content = generate_context_file(repo_path.name, modules, total, module_ids)
    (claude_dir / "bearings.md").write_text(bearings_content, encoding="utf-8")

    bearings_dir = claude_dir / "bearings"
    bearings_dir.mkdir(parents=True, exist_ok=True)
    by_cat: Dict[str, list] = {}
    for mod in modules:
        by_cat.setdefault(mod["category"], []).append(mod)
    for category, cat_mods in by_cat.items():
        cat_content = generate_category_file(repo_path.name, category, cat_mods, module_ids)
        (bearings_dir / f"{category}.md").write_text(cat_content, encoding="utf-8")

    print(f"  Bearings: {len(modules)} modules, {total} entities")

    # 3. Rules
    from ir.rules_generator import generate_rules_file
    try:
        rules_content = generate_rules_file(repo_path)
        rules_path = repo_path / ".claude" / "rules" / "CodeIR.md"
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        rules_path.write_text(rules_content, encoding="utf-8")
        print(f"  Rules:    {rules_path}")
    except ValueError as exc:
        print(f"Warning: could not generate rules: {exc}")

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
        print(f"No changes detected. {result.get('files_scanned', 0)} files scanned.")
        return

    print(f"Indexed {result.get('files_changed', 0)} changed files "
          f"({result.get('files_unchanged', 0)} unchanged, {result.get('files_scanned', 0)} total)")
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
    result = get_entity_with_ir(repo_path=repo_path, entity_id=args.entity_id, mode=args.level)
    if not result:
        print(f"Entity not found: {args.entity_id} (level={args.level})")
        print("Run `codeir index <repo_path>` first.")
        return

    # Header
    print(f"Entity: {result['qualified_name']}  [{result['kind']}]")
    print(f"File:   {result['file_path']}:{result['line']}")
    if result.get("mode"):
        print(f"Level:  {result['mode']}")

    # Determine if we should use smart pattern view
    use_smart_view = False
    pattern_details = None
    db_path = repo_path / ".codeir" / "entities.db"

    # Check global toggle and --full flag
    patterns_disabled = not PATTERNS_ENABLED or getattr(args, "full", False)

    if args.level == "Behavior" and not patterns_disabled:
        from index.pattern_detector import get_entity_pattern_details
        pattern_details = get_entity_pattern_details(db_path, args.entity_id)
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

        print(f"\nFull IR: codeir show {args.entity_id} --full")
    else:
        # Standard IR view (vanilla)
        ir_text = result['ir_text']

        # For Index level, add pattern marker if entity belongs to a pattern (unless patterns disabled)
        if args.level == "Index" and not patterns_disabled:
            from index.pattern_detector import get_entity_pattern
            pattern_id = get_entity_pattern(db_path, args.entity_id)
            if pattern_id:
                # Insert pattern marker after entity ID
                parts = ir_text.split(" ", 2)  # opcode, entity_id, rest
                if len(parts) >= 2:
                    ir_text = f"{parts[0]} {parts[1]} →{pattern_id}"
                    if len(parts) > 2:
                        ir_text += f" {parts[2]}"

        print(f"\n{ir_text}")


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
        print(f"\n{source}")

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

    # Print resolved callers
    for row in rows:
        marker = "~" if row["resolution"] == "fuzzy" else " "
        print(f" {marker}{row['caller_id']:20s}  {row['caller_name']:40s}  {row['caller_file']}  [{row['resolution']}]")

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
    conn.close()

    root = result["root"]
    if not root:
        print(f"Entity not found: {args.entity_id}")
        return

    print(f"Impact analysis for: {root['qualified_name']}  [{root['kind']}]")
    print(f"File: {root['file_path']}:{root['start_line']}")
    if root["ir_text"]:
        print(f"IR:   {root['ir_text']}")
    print()

    impact_by_depth = result["impact_by_depth"]
    total_affected = sum(len(items) for items in impact_by_depth.values())
    if total_affected == 0:
        print("No downstream dependents found.")
        return

    print(f"Affected: {total_affected} entities across {len(result['affected_files'])} files")
    if result["affected_categories"]:
        print(f"Categories: {', '.join(sorted(result['affected_categories']))}")
    print()

    for depth in sorted(impact_by_depth.keys()):
        items = impact_by_depth[depth]
        label = "direct" if depth == 1 else f"depth {depth}"
        print(f"--- {label} ({len(items)} entities) ---")
        for item in sorted(items, key=lambda x: (x["file_path"], x["qualified_name"])):
            marker = "~" if item["resolution"] == "fuzzy" else " "
            loc = f"{item['file_path']}:{item['start_line']}" if item["start_line"] else item["file_path"]
            print(f" {marker}{item['entity_id']:20s}  {item['qualified_name']:40s}  {loc}  [{item['kind']}]")
            if item["ir_text"]:
                print(f"  {'':20s}  IR: {item['ir_text']}")
            if depth > 1:
                print(f"  {'':20s}  via: {item['via']}")
        print()


def cmd_scope(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `codeir index <repo_path>` first.")
        return

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = compute_scope(conn, args.entity_id, level=args.level)
    finally:
        conn.close()

    root = result["root"]
    if not root:
        print(f"Entity not found: {args.entity_id}")
        return

    print(f"Scope for: {root['qualified_name']}  [{root['kind']}]")
    print(f"File: {root['file_path']}:{root['start_line']}")
    if root["ir_text"]:
        print(f"IR:   {root['ir_text']}")
    print()

    def _print_group(label: str, items: list) -> None:
        if not items:
            return
        print(f"--- {label} ({len(items)}) ---")
        for item in items:
            marker = "~" if item.get("resolution") == "fuzzy" else " "
            loc = f"{item['file_path']}:{item['start_line']}"
            print(f" {marker}{item['entity_id']:20s}  {item['qualified_name']:40s}  {loc}  [{item['kind']}]")
            if item["ir_text"]:
                print(f"  {'':20s}  IR: {item['ir_text']}")
        print()

    _print_group("callers (what calls this)", result["callers"])
    _print_group("callees (what this calls)", result["callees"])
    _print_group("siblings (same class)", result["siblings"])

    total = len(result["callers"]) + len(result["callees"]) + len(result["siblings"])
    if total == 0:
        print("No related entities found.")
    else:
        print(f"Total: {total} entities in scope")


def _print_matches(matches: list) -> None:
    """Print match lines with optional context, deduplicating overlapping context."""
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


def cmd_grep(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    try:
        results = grep_entities(
            pattern=args.pattern,
            repo_path=repo_path,
            level=args.level,
            limit=args.limit,
            ignore_case=args.ignore_case,
            context=args.context,
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
    print(f"{total_matches} matches across {entity_groups} entities and {file_groups} unmatched regions\n")

    verbose = getattr(args, "verbose", False)
    for group in results:
        match_count = len(group["matches"])
        if group["type"] == "entity":
            print(f"  {group['entity_id']:20s}  {group['qualified_name']}  [{group['kind']}]")
            print(f"  {'':20s}  {group['file_path']}:{group['start_line']}-{group['end_line']}  ({match_count} matches)")
            if verbose:
                ir_text = group.get("ir_text") or "(no IR at this level)"
                print(f"  {'':20s}  IR: {ir_text}")
            _print_matches(group["matches"])
            print()
        else:
            print(f"  {'(no entity)':20s}  {group['file_path']}  ({match_count} matches)")
            _print_matches(group["matches"])
            print()


def cmd_stats(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    stats = get_stats(repo_path)

    print(f"Entities:  {stats['entity_count']}")
    for kind_info in stats["entities_by_kind"]:
        print(f"  {kind_info['kind']:20s}  {kind_info['count']}")

    fc = stats["file_coverage"]
    print(f"\nFile coverage: {fc['files_with_entities']}/{fc['python_files_indexed']} ({fc['coverage_percent']:.1f}%)")

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

    claude_dir = repo_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Tier 1: bearings-summary.md
    summary_content = generate_summary(repo_path.name, modules, total)
    summary_path = claude_dir / "bearings-summary.md"
    summary_path.write_text(summary_content, encoding="utf-8")

    # Tier 2: bearings.md (collapsed working map)
    bearings_content = generate_context_file(repo_path.name, modules, total, module_ids)
    bearings_path = claude_dir / "bearings.md"
    bearings_path.write_text(bearings_content, encoding="utf-8")

    # Tier 3: bearings/{category}.md (full uncollapsed per category)
    bearings_dir = claude_dir / "bearings"
    bearings_dir.mkdir(parents=True, exist_ok=True)

    by_cat: Dict[str, list] = {}
    for mod in modules:
        by_cat.setdefault(mod["category"], []).append(mod)

    for category, cat_mods in by_cat.items():
        cat_content = generate_category_file(repo_path.name, category, cat_mods, module_ids, db_path=db_path)
        cat_path = bearings_dir / f"{category}.md"
        cat_path.write_text(cat_content, encoding="utf-8")

    print(f"Generated bearings ({len(modules)} modules, {total} entities):")
    print(f"  Summary:    {summary_path}")
    print(f"  Working map:{bearings_path}")
    print(f"  Categories: {bearings_dir}/ ({len(by_cat)} files)")


def _estimate_tokens(file_path: Path) -> int:
    """Estimate token count from file size (chars / 4)."""
    if not file_path.exists():
        return 0
    return file_path.stat().st_size // 4


def cmd_bearings(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    claude_dir = repo_path / ".claude"

    # --generate mode: regenerate all files
    if args.generate:
        _generate_bearings_files(repo_path)
        return

    # Check if bearings files exist
    summary_path = claude_dir / "bearings-summary.md"
    bearings_path = claude_dir / "bearings.md"
    bearings_dir = claude_dir / "bearings"

    if not summary_path.exists():
        print("No bearings files found. Run `codeir bearings --generate` first.")
        return

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
