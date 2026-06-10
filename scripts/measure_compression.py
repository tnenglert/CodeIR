#!/usr/bin/env python3
"""Measure CodeIR compression ratios and entity ID efficiency with a real tokenizer.

Usage:
    python scripts/measure_compression.py /path/to/repo [--sample-size N] [--output report.md]

Requires tiktoken:
    pip install tiktoken
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    import tiktoken
except ImportError:
    print("This script requires tiktoken. Install via: pip install tiktoken")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_encoder = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_encoder.encode(text))


# ---------------------------------------------------------------------------
# Section 1: Entity ID efficiency
# ---------------------------------------------------------------------------

def measure_entity_ids(conn: sqlite3.Connection, sample_size: int = 100) -> Dict[str, Any]:
    """Sample entities and compare token costs of bare name, qualified name, entity ID."""
    rows = conn.execute("""
        SELECT id, name, qualified_name
        FROM entities
        ORDER BY id
        LIMIT ?
    """, (sample_size,)).fetchall()

    if not rows:
        return {"sample_size": 0, "totals": {}, "averages": {}}

    totals: Dict[str, int] = {"bare": 0, "qualified": 0, "entity_id": 0}
    for entity_id, name, qualified_name in rows:
        totals["bare"]      += count_tokens(name or "")
        totals["qualified"] += count_tokens(qualified_name or name or "")
        totals["entity_id"] += count_tokens(entity_id or "")

    n = len(rows)
    return {
        "sample_size": n,
        "totals": totals,
        "averages": {k: v / n for k, v in totals.items()},
    }


def _interpret_entity_ids(data: Dict[str, Any]) -> str:
    if data["sample_size"] == 0:
        return "No entities found."
    avg = data["averages"]
    ratio_vs_bare      = avg["entity_id"] / avg["bare"]      if avg["bare"]      else 0
    ratio_vs_qualified = avg["entity_id"] / avg["qualified"] if avg["qualified"] else 0

    if avg["entity_id"] > avg["qualified"]:
        return (
            f"Entity IDs tokenize to more tokens than qualified names "
            f"({ratio_vs_bare:.2f}x bare, {ratio_vs_qualified:.2f}x qualified). "
            "This is unexpected and suggests the ID encoding should be reconsidered."
        )
    return (
        f"Entity IDs cost {ratio_vs_bare:.2f}x bare names but {ratio_vs_qualified:.2f}x "
        "qualified names. Since CodeIR uses IDs where qualified names would otherwise "
        "be needed for disambiguation, the net effect is compression."
    )


def format_entity_ids_section(data: Dict[str, Any]) -> str:
    if data["sample_size"] == 0:
        return "## Entity identifier efficiency\n\nNo entities found.\n"

    avg = data["averages"]
    tot = data["totals"]
    n   = data["sample_size"]

    bare_ratio = avg["entity_id"] / avg["bare"] if avg["bare"] else 0

    rows = [
        ("Bare name",         avg["bare"],      tot["bare"],      1.00),
        ("Qualified name",    avg["qualified"], tot["qualified"], avg["qualified"] / avg["bare"] if avg["bare"] else 0),
        ("CodeIR entity ID",  avg["entity_id"], tot["entity_id"], bare_ratio),
    ]

    col_w = [24, 13, 14, 15]
    header = (
        f"  {'Representation':<{col_w[0]}} {'Avg tokens':>{col_w[1]}} "
        f"{'Total tokens':>{col_w[2]}} {'Ratio vs bare':>{col_w[3]}}"
    )
    sep = "  " + "-" * col_w[0] + "   " + "-" * (col_w[1]-1) + "   " + "-" * (col_w[2]-1) + "   " + "-" * col_w[3]
    data_rows = "\n".join(
        f"  {label:<{col_w[0]}} {avg_t:>{col_w[1]}.1f} {total_t:>{col_w[2]},} {ratio:>{col_w[3]}.2f}x"
        for label, avg_t, total_t, ratio in rows
    )

    interp = _interpret_entity_ids(data)

    return "\n".join([
        f"## Entity identifier efficiency  (sample of {n} entities)",
        "",
        header,
        sep,
        data_rows,
        "",
        f"**Interpretation:** {interp}",
    ])


# ---------------------------------------------------------------------------
# Section 2: IR level compression
# ---------------------------------------------------------------------------

def measure_ir_levels(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Measure token counts per IR level, re-tokenizing ir_text directly."""
    modes_present = {r[0] for r in conn.execute("SELECT DISTINCT mode FROM ir_rows").fetchall()}

    results: Dict[str, Dict[str, int]] = {}
    source_from_stored_counts = False  # track where source tokens came from

    # Source: use stored source_token_count from Behavior rows (one per entity).
    # We can't re-tokenize source without Source-level rows or reading every file,
    # so we trust the stored count (computed by the indexer from the actual source text).
    anchor = "Behavior" if "Behavior" in modes_present else (
        next(iter(modes_present)) if modes_present else None
    )
    if anchor:
        row = conn.execute(
            "SELECT SUM(source_token_count), SUM(source_char_count) FROM ir_rows WHERE mode=?",
            (anchor,)
        ).fetchone()
        src_real = int(row[0] or 0)
        src_chars = int(row[1] or 0)
        src_estimate = max(1, src_chars // 4)
        # Trust stored source_token_count if non-zero — the indexer uses tiktoken when
        # available, and this script requires tiktoken, so stored counts should be real.
        src_is_estimate = src_real == 0
        if not src_is_estimate:
            source_from_stored_counts = True
        results["Source"] = {
            "real_tokens": src_real,
            "estimate_tokens": src_estimate,
            "is_estimate": src_is_estimate,
        }

    # Behavior and Index: re-tokenize ir_text directly for independent verification
    for mode in ("Behavior", "Index", "Source"):
        if mode not in modes_present:
            continue
        ir_rows = conn.execute(
            "SELECT ir_text, ir_char_count FROM ir_rows WHERE mode=?", (mode,)
        ).fetchall()
        real = sum(count_tokens(r[0] or "") for r in ir_rows)
        estimate = sum(max(1, (r[1] or 0) // 4) for r in ir_rows)

        if mode == "Source":
            # Override: Source ir_text IS the source; use this measurement
            results["Source"] = {
                "real_tokens": real,
                "estimate_tokens": estimate,
                "is_estimate": False,
            }
            source_from_stored_counts = False  # real Source rows, not stored counts
        else:
            results[mode] = {
                "real_tokens": real,
                "estimate_tokens": estimate,
                "is_estimate": False,
            }

    # Total entity count for the report header
    entity_count = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    file_count   = int(conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0])

    return {
        "levels": results,
        "entity_count": entity_count,
        "file_count": file_count,
        "source_from_stored_counts": source_from_stored_counts,
    }


def _interpret_ir_levels(levels: Dict[str, Dict[str, int]]) -> str:
    src = levels.get("Source", {})
    src_real = src.get("real_tokens", 0)
    if src_real == 0:
        return "Source token count unavailable (index does not include Source-level rows)."

    lines = []
    for level in ("Behavior", "Index"):
        if level not in levels:
            continue
        data = levels[level]
        real = data["real_tokens"]
        est  = data["estimate_tokens"]
        if real == 0:
            continue
        pct_diff = (est - real) / real * 100
        # pct_diff > 0: estimate says MORE tokens than reality → estimate is pessimistic
        #               → we're underselling; README can cite real (better) numbers
        # pct_diff < 0: estimate says FEWER tokens than reality → estimate is optimistic
        #               → we're overclaiming compression; README needs real numbers
        direction = "higher" if pct_diff > 0 else "lower"
        if abs(pct_diff) <= 15:
            verdict = f"estimate is within {abs(pct_diff):.1f}% — reasonable shortcut"
        elif pct_diff > 15:
            verdict = (
                f"estimate is {abs(pct_diff):.1f}% {direction} than reality "
                "(pessimistic — real compression is better; README can be updated)"
            )
        else:
            verdict = (
                f"estimate is {abs(pct_diff):.1f}% {direction} than reality "
                "(optimistic — overclaims compression; README needs real numbers)"
            )
        lines.append(f"  {level}: {verdict}.")

    return "\n".join(lines) if lines else "No Behavior or Index levels found."


def format_ir_levels_section(data: Dict[str, Any]) -> str:
    levels = data["levels"]
    entity_count = data["entity_count"]
    file_count   = data["file_count"]

    src_real = levels.get("Source", {}).get("real_tokens", 0)

    col_w = [18, 14, 18, 17]
    header = (
        f"  {'Level':<{col_w[0]}} {'Tokens':>{col_w[1]}} "
        f"{'Ratio vs source':>{col_w[2]}} {'4-char estimate':>{col_w[3]}}"
    )
    sep = "  " + "-"*col_w[0] + "   " + "-"*(col_w[1]-1) + "   " + "-"*(col_w[2]-1) + "   " + "-"*col_w[3]

    table_rows: List[str] = []
    for level_name in ("Source", "Behavior", "Index"):
        if level_name not in levels:
            continue
        d = levels[level_name]
        real = d["real_tokens"]
        est  = d["estimate_tokens"]
        is_est = d.get("is_estimate", False)

        if real == 0:
            real_str = "(unavailable)"
            ratio_str = "—"
        else:
            real_str = f"{real:,}"
            ratio = src_real / real if (src_real and real) else 0
            ratio_str = f"{ratio:.2f}x"

        num_w = 13  # fixed width for the token count within the estimate column
        if est > 0 and real > 0:
            pct = (est - real) / real * 100
            sign = "+" if pct >= 0 else ""
            est_str = f"{est:>{num_w},}  ({sign}{pct:.1f}%)"
        elif est > 0:
            est_str = f"{est:>{num_w},}"
        else:
            est_str = f"{'—':>{num_w}}"

        label = level_name + ("*" if is_est else "")
        table_rows.append(
            f"  {label:<{col_w[0]}} {real_str:>{col_w[1]}} "
            f"{ratio_str:>{col_w[2]}} {est_str}"
        )

    interp = _interpret_ir_levels(levels)

    lines = [
        f"## IR level compression  ({entity_count:,} entities, {file_count:,} files)",
        "",
        header,
        sep,
        "\n".join(table_rows),
        "",
        "**Interpretation:**",
    ]
    for line in interp.splitlines():
        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 4: Methodology
# ---------------------------------------------------------------------------

def format_methodology_section(
    repo_path: Path,
    entity_count: int,
    file_count: int,
    languages: List[str],
    source_from_stored_counts: bool,
) -> str:
    source_method = (
        "source_token_count column in ir_rows (pre-computed at index time via tiktoken)"
        if source_from_stored_counts
        else
        "ir_rows table, mode='Source' (re-tokenized from ir_text directly)"
    )

    lines = [
        "## Methodology",
        "",
        "  Tokenizer:        tiktoken o200k_base",
        f"  Codebase:         {repo_path}",
        f"  Entities indexed: {entity_count:,}",
        f"  Files:            {file_count:,}",
        f"  Languages:        {', '.join(languages) if languages else 'unknown'}",
        "",
        f"  Source tokens:    {source_method}",
        "  IR tokens:        ir_rows table, mode column filtered by level,",
        "                    re-tokenized from ir_text via tiktoken.",
        "  4-char estimate:  ir_char_count // 4 per row, summed.",
        "",
        "  This script:      scripts/measure_compression.py (in repo)",
        "  Reproduce:        codeir index /path/to/repo \\",
        "                    && python scripts/measure_compression.py /path/to/repo",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(repo_path: Path, sample_size: int = 100) -> str:
    entities_db = repo_path / ".codeir" / "entities.db"
    if not entities_db.exists():
        print(f"Error: no index found at {entities_db}")
        print(f"Run: codeir index {repo_path}")
        sys.exit(1)

    conn = sqlite3.connect(entities_db)

    # Section 1
    entity_id_data = measure_entity_ids(conn, sample_size=sample_size)

    # Section 2
    ir_data = measure_ir_levels(conn)

    # Languages from modules table
    try:
        lang_rows = conn.execute("SELECT file_path FROM modules").fetchall()
        ext_map = {".py": "python", ".rs": "rust", ".ts": "typescript", ".tsx": "typescript"}
        seen_langs: Dict[str, int] = {}
        for (fp,) in lang_rows:
            ext = Path(fp).suffix.lower()
            lang = ext_map.get(ext, ext.lstrip(".") or "other")
            seen_langs[lang] = seen_langs.get(lang, 0) + 1
        languages = [lang for lang, _ in sorted(seen_langs.items(), key=lambda x: -x[1])]
    except Exception:
        languages = []

    conn.close()

    divider = "\n---\n"
    title = "\n".join([
        "# CodeIR Compression Measurement",
        "",
        f"Generated by `scripts/measure_compression.py` on `{repo_path.name}`.",
        "Tokenizer: tiktoken o200k_base.",
    ])

    sections = [
        title,
        format_entity_ids_section(entity_id_data),
        format_ir_levels_section(ir_data),
        format_methodology_section(
            repo_path,
            ir_data["entity_count"],
            ir_data["file_count"],
            languages,
            ir_data.get("source_from_stored_counts", False),
        ),
    ]

    return divider.join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure CodeIR compression ratios using tiktoken o200k_base."
    )
    parser.add_argument(
        "repo_path", type=Path, nargs="?", default=Path("."),
        help="Path to indexed repository (default: current directory)",
    )
    parser.add_argument(
        "--sample-size", type=int, default=100,
        help="Number of entities to sample for ID efficiency test (default: 100)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write report to this file in addition to stdout",
    )
    args = parser.parse_args()

    report = run(args.repo_path.resolve(), sample_size=args.sample_size)
    print(report)

    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"\n(Report also written to {args.output})", file=sys.stderr)


if __name__ == "__main__":
    main()
