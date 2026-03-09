#!/usr/bin/env python3
"""SemanticIR CLI entrypoint.

Commands:
  index        — Index a repository with multi-pass pipeline
  search       — Search entities in an indexed repository
  show         — Display compressed IR for an entity
  expand       — Display raw source code for an entity
  compare      — Side-by-side comparison of all compression levels for an entity
  stats        — Show repository index statistics
  module-map   — Display classified module map with dependencies
  bearings     — Generate bearings.md agent orientation context file
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
from index.search import search_entities
from index.store.db import connect
from index.store.fetch import get_entity_all_levels, get_entity_location, get_entity_with_ir
from index.store.stats import get_stats


DEFAULT_CONFIG: Dict[str, Any] = {
    "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".semanticir"],
    "extensions": [".py"],
    "compression_level": "L1",
}


def load_config(repo_path: Path) -> Dict[str, Any]:
    """Load optional config from <repo>/.semanticir/config.json."""
    cfg = dict(DEFAULT_CONFIG)
    cfg_path = repo_path / ".semanticir" / "config.json"
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        if isinstance(user_cfg, dict):
            cfg.update(user_cfg)
    return cfg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="semanticir", description="SemanticIR — semantic compression and indexing for codebases")
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p_index = sub.add_parser("index", help="Index a repository")
    p_index.add_argument("repo_path", type=Path)
    p_index.add_argument("--level", default=None, help="Compression level: L0, L1, L2, L3, or all")
    p_index.add_argument("--mode", default=None, help="Legacy mode alias: a, b, or hybrid")
    p_index.add_argument("--compact", action="store_true", help="Rebuild abbreviation maps from scratch")

    # search
    p_search = sub.add_parser("search", help="Search entities")
    p_search.add_argument("query")
    p_search.add_argument("--repo-path", type=Path, default=Path("."))
    p_search.add_argument("--limit", type=int, default=50)

    # show
    p_show = sub.add_parser("show", help="Show entity IR")
    p_show.add_argument("entity_id")
    p_show.add_argument("--repo-path", type=Path, default=Path("."))
    p_show.add_argument("--level", default="L1", help="Compression level to show")

    # expand
    p_expand = sub.add_parser("expand", help="Show raw source for an entity")
    p_expand.add_argument("entity_id")
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
    p_bearings = sub.add_parser("bearings", help="Generate bearings.md context file")
    p_bearings.add_argument("--repo-path", type=Path, default=Path("."))
    p_bearings.add_argument("--output", type=Path, default=None, help="Output path (default: bearings.md)")

    # eval
    p_eval = sub.add_parser("eval", help="Evaluate compression levels")
    p_eval.add_argument("repo_path", type=Path)
    p_eval.add_argument("--levels", nargs="+", default=["L1", "L2", "L3"])
    p_eval.add_argument("--modes", default=None, help="Legacy modes: comma-separated a,b,hybrid")
    p_eval.add_argument("--output", type=Path, default=None)

    # floor-test
    p_floor = sub.add_parser("floor-test", help="Comprehensibility floor testing")
    floor_sub = p_floor.add_subparsers(dest="floor_action", required=True)

    p_floor_gen = floor_sub.add_parser("generate", help="Generate test pack")
    p_floor_gen.add_argument("repo_path", type=Path)
    p_floor_gen.add_argument("--level", default="L1", help="Compression level for test pack")
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
    print(f"  Level: {result.get('compression_level', 'L1')}")
    print(f"  Store: {result.get('store_dir', '')}")


def cmd_search(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    results = search_entities(query=args.query, repo_path=repo_path, limit=args.limit)
    if not results:
        print("No entities found.")
        return
    for r in results:
        print(f"  {r['entity_id']:20s}  {r['qualified_name']:40s}  {r['file_path']}:{r['line']}  [{r['kind']}]")


def cmd_show(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    result = get_entity_with_ir(repo_path=repo_path, entity_id=args.entity_id, mode=args.level)
    if not result:
        print(f"Entity not found: {args.entity_id} (level={args.level})")
        print("Run `semanticir index <repo_path>` first.")
        return
    print(f"Entity: {result['qualified_name']}  [{result['kind']}]")
    print(f"File:   {result['file_path']}:{result['line']}")
    if result.get("mode"):
        print(f"Level:  {result['mode']}")
    print(f"\n{result['ir_text']}")


def cmd_expand(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    loc = get_entity_location(repo_path=repo_path, entity_id=args.entity_id)
    if not loc:
        print(f"Entity not found: {args.entity_id}")
        return
    source = extract_code_slice(
        repo_path=repo_path,
        file_path=str(loc["file_path"]),
        start_line=int(loc["start_line"]),
        end_line=int(loc["end_line"]),
    )
    print(f"Entity: {loc['qualified_name']}  [{loc['kind']}]")
    print(f"File:   {loc['file_path']}:{loc['start_line']}-{loc['end_line']}")
    print(f"\n{source}")


def cmd_compare(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    levels = get_entity_all_levels(repo_path=repo_path, entity_id=args.entity_id)
    if not levels:
        print(f"Entity not found: {args.entity_id}")
        print("Run `semanticir index <repo> --level all` to generate all compression levels.")
        return

    first = levels[0]
    print(f"## {first['entity_id']} ({first['qualified_name']})")
    print(f"File: {first['file_path']}:{first['start_line']}-{first['end_line']}  [{first['kind']}]")

    # Show source
    loc = get_entity_location(repo_path=repo_path, entity_id=args.entity_id)
    if loc:
        source = extract_code_slice(
            repo_path=repo_path,
            file_path=str(loc["file_path"]),
            start_line=int(loc["start_line"]),
            end_line=int(loc["end_line"]),
        )
        src_tokens = levels[0].get("source_token_count", "?")
        print(f"\n### Source ({src_tokens} tokens):")
        print(source)

    for row in levels:
        ir_tokens = row.get("ir_token_count", "?")
        ratio = row.get("compression_ratio", "?")
        if isinstance(ratio, float):
            ratio = f"{ratio:.2f}"
        print(f"\n### {row['mode']} ({ir_tokens} tokens, ratio {ratio}):")
        print(row["ir_text"])


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
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `semanticir index <repo_path>` first.")
        return

    conn = connect(db_path)

    # Get module classifications — try with deps_internal first, fall back gracefully
    try:
        modules = conn.execute(
            "SELECT m.file_path, m.category, m.entity_count, m.deps_internal "
            "FROM modules m ORDER BY m.category, m.file_path"
        ).fetchall()
        has_deps = True
    except sqlite3.OperationalError:
        try:
            modules = conn.execute(
                "SELECT m.file_path, m.category, m.entity_count "
                "FROM modules m ORDER BY m.category, m.file_path"
            ).fetchall()
            has_deps = False
        except sqlite3.OperationalError:
            print("No module classifications found. Re-index to generate module map.")
            conn.close()
            return

    if not modules:
        print("No modules indexed.")
        conn.close()
        return

    # Group by category
    categories: Dict[str, list] = {}
    for row in modules:
        file_path, category, entity_count = row[0], row[1], row[2]
        deps = row[3] if has_deps and len(row) > 3 else ""
        categories.setdefault(category, []).append((file_path, entity_count, deps))

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

    conn.close()


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


def cmd_bearings(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    db_path = repo_path / ".semanticir" / "entities.db"
    if not db_path.exists():
        print("No index found. Run `semanticir index <repo_path>` first.")
        return

    conn = connect(db_path)

    # Fetch modules with deps
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
    conn.close()

    modules = [
        {"file_path": r[0], "category": r[1],
         "entity_count": r[2], "deps_internal": r[3]}
        for r in rows
    ]

    # Assign module IDs (computed, not persisted)
    module_ids = _compute_module_ids(modules)

    from ir.classifier import generate_context_file
    content = generate_context_file(repo_path.name, modules, total, module_ids)

    output = args.output or Path("bearings.md")
    output.write_text(content, encoding="utf-8")
    print(f"Generated {output} ({len(modules)} modules, {total} entities)")


def cmd_eval(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    cfg = load_config(repo_path)
    levels = [str(l).upper() for l in args.levels]

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
        from index.floor_test import generate_test_pack

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
        from index.eval import floor_report, render_floor_matrix_markdown

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
        "index": cmd_index,
        "search": cmd_search,
        "show": cmd_show,
        "expand": cmd_expand,
        "compare": cmd_compare,
        "stats": cmd_stats,
        "module-map": cmd_module_map,
        "bearings": cmd_bearings,
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
