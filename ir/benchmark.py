"""Benchmark command: one-shot report demonstrating CodeIR on a target codebase."""

from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from index.db.db import connect
from ir.classifier import CATEGORIES, DOMAINS, classify_file_with_stage
from ir.rules_generator import annotate_behavior_ir, parse_behavior_ir
from ir.token_count import count_tokens

_DIVIDER = "─" * 80


# ---------------------------------------------------------------------------
# Coverage (which classifier stage fired for each file)
# ---------------------------------------------------------------------------

def _compute_coverage(
    repo_path: Path,
    file_paths: List[str],
) -> Tuple[int, int]:
    """Return (structural_count, fallback_count) by re-classifying each file.

    Structural = stages 1-3 (filename, directory, AST).
    Fallback   = stage 4 (count-based heuristic).
    Non-Python files are counted as structural (dedicated parser).
    """
    structural = 0
    fallback = 0
    for rel_path in file_paths:
        path = Path(rel_path)
        if path.suffix != ".py":
            structural += 1
            continue
        abs_path = repo_path / rel_path
        if not abs_path.exists():
            fallback += 1
            continue
        try:
            source = abs_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
            _, stage = classify_file_with_stage(path, tree)
            if stage <= 3:
                structural += 1
            else:
                fallback += 1
        except Exception:
            fallback += 1
    return structural, fallback


# ---------------------------------------------------------------------------
# Section 1: Indexing summary
# ---------------------------------------------------------------------------

def _section_indexing(
    repo_path: Path,
    index_result: Optional[Dict[str, Any]],
    elapsed: Optional[float],
    conn: sqlite3.Connection,
) -> str:
    total_entities = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    file_paths = [r[0] for r in conn.execute("SELECT file_path FROM modules").fetchall()]
    total_files = len(file_paths)

    # Language breakdown
    lang_counts: Dict[str, int] = {}
    for fp in file_paths:
        ext = Path(fp).suffix
        name = {
            ".py": "Python", ".rs": "Rust",
            ".ts": "TypeScript", ".tsx": "TypeScript",
            ".js": "JavaScript",
        }.get(ext, ext.lstrip(".").upper() or "Other")
        lang_counts[name] = lang_counts.get(name, 0) + 1

    langs_str = ", ".join(
        f"{lang} ({cnt:,} {'file' if cnt == 1 else 'files'})"
        for lang, cnt in sorted(lang_counts.items(), key=lambda x: -x[1])
    )

    # Coverage
    structural, fallback = _compute_coverage(repo_path, file_paths)
    total = structural + fallback
    if total > 0:
        struct_pct = structural / total * 100
        fall_pct   = fallback  / total * 100
        cov_str = (
            f"{struct_pct:.1f}% structural, {fall_pct:.1f}% fallback "
            f"({fallback} {'file' if fallback == 1 else 'files'})"
        )
    else:
        cov_str = "n/a"

    # Source line
    if elapsed is not None:
        timing = f" in {elapsed:.1f}s"
    else:
        timing = ""

    lines = [f"Benchmarking {repo_path}/...", ""]
    if index_result is None:
        lines.append("  Using cached index from .codeir/")
    else:
        files_changed = index_result.get("files_changed", 0)
        lines.append(f"  Indexed{timing}: {files_changed} files changed")

    lines += [
        "",
        f"  \u2713 {total_entities:,} entities across {total_files:,} "
        f"{'file' if total_files == 1 else 'files'}{timing if index_result is not None else ''}",
        f"  \u2713 Coverage: {cov_str}",
        f"  \u2713 Languages: {langs_str}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 2: Taxonomy
# ---------------------------------------------------------------------------

def _wrap_parts(parts: List[str], indent: int = 15) -> str:
    """Join a list of string parts into wrapped lines of max 80 chars."""
    prefix = " " * indent
    lines: List[str] = []
    current = ""
    for part in parts:
        sep = ", " if current else ""
        candidate = current + sep + part
        if current and len(prefix + candidate) > 80:
            lines.append(prefix + current + ",")
            current = part
        else:
            current = candidate
    if current:
        lines.append(prefix + current)
    return "\n".join(lines)


def _fmt_counts(items: List[Tuple[str, int]], max_items: int = 10, indent: int = 15) -> str:
    """Format a list of (name, count) into wrapped lines of max 80 chars."""
    top = items[:max_items]
    remainder = len(items) - max_items
    parts = [f"{name} ({cnt:,})" for name, cnt in top]
    if remainder > 0:
        parts.append(f"... and {remainder} more")
    return _wrap_parts(parts, indent=indent)


def _section_taxonomy(conn: sqlite3.Connection, entities_db: Path) -> str:
    # Categories
    cat_rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM modules GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    cat_items = [(r[0] or "unknown", r[1]) for r in cat_rows]

    # Domains
    dom_rows = conn.execute(
        "SELECT domain, COUNT(*) as cnt FROM modules "
        "GROUP BY domain ORDER BY cnt DESC"
    ).fetchall()
    dom_items = [(r[0] or "unknown", r[1]) for r in dom_rows]

    # Patterns
    from index.pattern_detector import get_patterns
    patterns = get_patterns(entities_db, include_tests=False)
    if patterns:
        pat_parts = [f"{p.base_class} ({p.member_count:,} classes)" for p in patterns[:10]]
        if len(patterns) > 10:
            pat_parts.append(f"... and {len(patterns) - 10} more")
        pat_parts = [f"{p.base_class} ({p.member_count:,} classes)" for p in patterns[:10]]
        if len(patterns) > 10:
            pat_parts.append(f"... and {len(patterns) - 10} more")
        pat_str = _wrap_parts(pat_parts, indent=15)
    else:
        pat_str = "None detected (threshold: 30+ members of a shared base class)"

    # "  Categories:  " and "  Domains:     " and "  Patterns:    " are all 15 chars
    cont = 15
    cat_str = _fmt_counts(cat_items, indent=cont)
    dom_str = _fmt_counts(dom_items, indent=cont)

    return "\n".join([
        _DIVIDER,
        "Taxonomy",
        "",
        f"  Categories:  {cat_str.lstrip()}",
        "",
        f"  Domains:     {dom_str.lstrip()}",
        "",
        f"  Patterns:    {pat_str.lstrip()}",
    ])


# ---------------------------------------------------------------------------
# Section 3: Compression
# ---------------------------------------------------------------------------

def _section_compression(conn: sqlite3.Connection) -> str:
    # Check which modes exist
    modes = {r[0] for r in conn.execute("SELECT DISTINCT mode FROM ir_rows").fetchall()}

    # Source stats: sum from Behavior rows (one source per entity)
    anchor_mode = "Behavior" if "Behavior" in modes else (next(iter(modes)) if modes else None)

    if not anchor_mode:
        return "\n".join([_DIVIDER, "Compression", "", "  No IR rows found."])

    src_tok = int(conn.execute(
        "SELECT COALESCE(SUM(source_token_count),0) FROM ir_rows WHERE mode=?",
        (anchor_mode,)
    ).fetchone()[0])
    src_chars = int(conn.execute(
        "SELECT COALESCE(SUM(source_char_count),0) FROM ir_rows WHERE mode=?",
        (anchor_mode,)
    ).fetchone()[0])
    src_estimate = max(1, src_chars // 4)

    # Determine if tiktoken was used (proxy: actual count exists + differs from estimate)
    has_tiktoken = src_tok > 0 and abs(src_tok - src_estimate) > src_estimate * 0.02
    src_label = "tiktoken cl100k_base" if has_tiktoken else "4-chars-per-token estimate"

    lines = [_DIVIDER, "Compression  (measured on this codebase)", ""]

    if has_tiktoken:
        divergence = abs(src_tok - src_estimate) / max(src_tok, 1)
        lines.append(f"  Source:      {src_tok:>13,} tokens  ({src_label})")
        lines.append(f"               {src_estimate:>13,} tokens  (4-chars-per-token estimate)")
        if divergence > 0.15:
            lines.append(
                "               Note: estimates diverge >15% — tiktoken count is authoritative."
            )
        lines.append("")
    else:
        lines.append(f"  Source:      {src_estimate:>13,} tokens  ({src_label})")
        lines.append("               Note: install `tiktoken` for tokenizer-accurate counts.")
        lines.append("")

    source_ref = src_tok if has_tiktoken else src_estimate

    for mode in ("Behavior", "Index", "Source"):
        if mode not in modes:
            continue
        ir_tok = int(conn.execute(
            "SELECT COALESCE(SUM(ir_token_count),0) FROM ir_rows WHERE mode=?",
            (mode,)
        ).fetchone()[0])
        ratio = source_ref / ir_tok if ir_tok else 0
        lines.append(f"  {mode + ':':12s} {ir_tok:>13,} tokens  ({ratio:.1f}\u00d7 reduction)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 4: Worked example
# ---------------------------------------------------------------------------

def _pick_example(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Pick one rich entity for the worked example."""
    row = conn.execute("""
        SELECT e.id, e.qualified_name, e.kind, e.file_path, e.start_line, e.end_line,
               r.ir_text AS behavior_ir,
               r.source_token_count, r.ir_token_count
        FROM entities e
        JOIN ir_rows r ON r.entity_id = e.id AND r.mode = 'Behavior'
        LEFT JOIN modules m ON m.file_path = e.file_path
        WHERE e.kind IN ('function', 'method')
          AND (m.category = 'core_logic' OR m.category IS NULL)
          AND r.ir_text LIKE '%C=%'
          AND r.ir_text LIKE '%F=%'
          AND r.ir_text LIKE '%A=%'
          AND r.source_token_count BETWEEN 100 AND 500
        ORDER BY length(r.ir_text) DESC
        LIMIT 1
    """).fetchone()
    if not row:
        # Relax constraints
        row = conn.execute("""
            SELECT e.id, e.qualified_name, e.kind, e.file_path, e.start_line, e.end_line,
                   r.ir_text AS behavior_ir,
                   r.source_token_count, r.ir_token_count
            FROM entities e
            JOIN ir_rows r ON r.entity_id = e.id AND r.mode = 'Behavior'
            WHERE e.kind IN ('function', 'method')
              AND r.ir_text LIKE '%C=%'
            ORDER BY length(r.ir_text) DESC
            LIMIT 1
        """).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "qualified_name": row[1],
        "kind": row[2],
        "file_path": row[3],
        "start_line": row[4] or 1,
        "end_line": row[5] or 1,
        "behavior_ir": row[6],
        "source_token_count": row[7] or 0,
        "behavior_token_count": row[8] or 0,
    }


def _get_index_ir(conn: sqlite3.Connection, entity_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT ir_text FROM ir_rows WHERE entity_id=? AND mode='Index'", (entity_id,)
    ).fetchone()
    return row[0] if row else None


def _elide_source(source: str, max_first: int = 25, max_last: int = 10) -> Tuple[str, int]:
    """Elide a long source listing. Returns (display_text, total_lines)."""
    lines = source.splitlines()
    total = len(lines)
    max_show = max_first + max_last
    if total <= max_show:
        return source, total
    first = lines[:max_first]
    last = lines[-max_last:]
    elided = total - max_first - max_last
    display = "\n".join(first) + f"\n    [... {elided} lines elided ...]\n" + "\n".join(last)
    return display, total


def _search_term_from_name(qualified_name: str) -> str:
    """Pick the most distinctive search term from a qualified name."""
    name = qualified_name.split(".")[-1]  # strip module prefix
    parts = [p for p in name.split("_") if len(p) > 3]
    if not parts:
        return name[:20]
    # Prefer the longest non-trivial part
    parts.sort(key=len, reverse=True)
    return parts[0]


def _section_worked_example(
    conn: sqlite3.Connection,
    repo_path: Path,
) -> str:
    example = _pick_example(conn)
    if not example:
        return "\n".join([_DIVIDER, "Worked Example", "", "  No suitable entity found."])

    behavior_ir = example["behavior_ir"]
    index_ir = _get_index_ir(conn, example["id"])
    index_tokens = count_tokens(index_ir) if index_ir else 0
    behavior_tokens = example["behavior_token_count"] or count_tokens(behavior_ir)
    source_tokens = example["source_token_count"]

    # Source slice
    from index.locator import extract_code_slice
    source_raw = extract_code_slice(
        repo_path, example["file_path"],
        example["start_line"], example["end_line"]
    )
    source_display, source_lines = _elide_source(source_raw)
    if not source_tokens:
        source_tokens = count_tokens(source_raw)

    # Annotation
    parsed = parse_behavior_ir(behavior_ir)
    calls = parsed["calls"]
    flags = parsed["flags"]
    assignments = parsed["assignments"]
    base = parsed["base"]

    flag_descriptions = {
        "R": "returns", "E": "raises", "I": "conditionals",
        "L": "loops", "T": "try/except", "W": "with",
    }

    indent_src = "    "
    indented_source = "\n".join(
        indent_src + line for line in source_display.splitlines()
    )

    # What this demonstrates bullet list
    bullets: List[str] = []
    if calls:
        call_list = ", ".join(calls[:6])
        if len(calls) > 6:
            call_list += f", ... ({len(calls)} total)"
        bullets.append(f"Calls: {call_list}")
    if flags:
        flag_desc = ", ".join(flag_descriptions[f] for f in flags if f in flag_descriptions)
        bullets.append(f"Flags: {flag_desc}")
    if assignments:
        bullets.append(f"{assignments} assignments")
    if base:
        bullets.append(f"Extends {base}")
    bullet_lines = "\n".join(f"      - {b}" for b in bullets)

    fp_display = example["file_path"]
    qn = example["qualified_name"].split(".")[-1]  # short display name

    lines = [
        _DIVIDER,
        "Worked Example",
        "",
        f"  {qn}  in  {fp_display}  (lines {example['start_line']}–{example['end_line']})",
        "",
    ]

    if index_ir:
        lines += [
            f"  Index level ({index_tokens} tokens):",
            f"    {index_ir}",
            "",
        ]

    lines += [
        f"  Behavior level ({behavior_tokens} tokens):",
        f"    {behavior_ir}",
        "",
        f"  Source level ({source_tokens} tokens):",
        indented_source,
        "",
        "  What this demonstrates:",
        "    At Behavior level, an agent learns:",
        bullet_lines,
        "    Without reading the source.",
    ]

    if index_ir:
        lines.append(f"    Token cost to orient: {index_tokens}.")
    lines += [
        f"    Token cost to understand: {behavior_tokens}.",
        f"    Token cost to verify: {source_tokens}.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 5: Try it yourself
# ---------------------------------------------------------------------------

def _section_try_it_yourself(conn: sqlite3.Connection, example: Optional[Dict[str, Any]]) -> str:
    entity_id = example["id"] if example else "<entity_id>"
    qn = example["qualified_name"] if example else "example"
    search_term = _search_term_from_name(qn)

    w = 42  # width of command column
    lines = [
        _DIVIDER,
        "Try It Yourself",
        "",
        f"  {'codeir bearings':<{w}}# see your codebase from above",
        f"  {'codeir search ' + repr(search_term):<{w}}# find related entities",
        f"  {'codeir show ' + entity_id:<{w}}# inspect this entity",
        f"  {'codeir expand ' + entity_id:<{w}}# read the full source",
        "",
        "  Reproduce these numbers:",
        f"  {'codeir benchmark':<{w}}# this command, on any codebase",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_benchmark(repo_path: Path) -> str:
    """Run benchmark on repo_path and return the formatted report."""
    repo_path = repo_path.resolve()
    entities_db = repo_path / ".codeir" / "entities.db"

    if not entities_db.exists():
        # Need to index first
        from index.indexer import index_repo
        import sys
        cfg: Dict[str, Any] = {
            "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache",
                            ".pytest_cache", ".codeir"],
            "extensions": [".py", ".rs", ".ts", ".tsx"],
            "compression_level": "Behavior+Index",
        }
        print("Indexing repository...", file=sys.stderr)
        t0 = time.time()
        index_result: Optional[Dict[str, Any]] = index_repo(repo_path, cfg)
        elapsed: Optional[float] = time.time() - t0
    else:
        index_result = None
        elapsed = None

    if not entities_db.exists():
        parts = repo_path.suffix.lower() if repo_path.suffix else ""
        return (
            f"No entities found in {repo_path}.\n"
            "Supported languages: Python (.py), Rust (.rs), TypeScript (.ts/.tsx).\n"
            "Did you point to the right directory?"
        )

    conn = connect(entities_db, read_only=True)
    conn.row_factory = sqlite3.Row

    total_entities = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    if total_entities == 0:
        conn.close()
        return (
            f"No entities found in {repo_path}.\n"
            "Supported languages: Python (.py), Rust (.rs), TypeScript (.ts/.tsx).\n"
            "Did you point to the right directory?"
        )

    example = _pick_example(conn)

    sections = [
        _section_indexing(repo_path, index_result, elapsed, conn),
        _section_taxonomy(conn, entities_db),
        _section_compression(conn),
        _section_worked_example(conn, repo_path),
        _section_try_it_yourself(conn, example),
    ]

    conn.close()
    return "\n\n".join(sections)
