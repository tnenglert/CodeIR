"""Auto-generate .claude/rules/CodeIR.md with repo-specific IR example."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from index.store.db import connect
from ir.compressor import kind_to_opcode


# ---------------------------------------------------------------------------
# IR text parsing
# ---------------------------------------------------------------------------

_FLAG_DESCRIPTIONS = {
    "R": "returns",
    "E": "raises",
    "I": "conditionals",
    "L": "loops",
    "T": "try/except",
    "W": "with",
}


def parse_behavior_ir(ir_text: str) -> Dict[str, Any]:
    """Parse a Behavior IR line into structured fields.

    Example input: 'FN INDXRP C=a,b,c F=ILRT A=77 B=Foo #DB #CORE'
    """
    result: Dict[str, Any] = {
        "type": "", "id": "", "calls": [], "flags": "",
        "assignments": 0, "base": "", "tags": [],
    }
    if not ir_text:
        return result

    # Extract tags (#WORD)
    tags = re.findall(r"#(\w+)", ir_text)
    result["tags"] = tags

    # Extract type and id (first two tokens)
    tokens = ir_text.split()
    if len(tokens) >= 2:
        result["type"] = tokens[0]
        result["id"] = tokens[1]

    # Extract C= calls
    m = re.search(r"C=([^\s]+)", ir_text)
    if m:
        result["calls"] = m.group(1).split(",")

    # Extract F= flags
    m = re.search(r"F=([A-Z]+)", ir_text)
    if m:
        result["flags"] = m.group(1)

    # Extract A= assignments
    m = re.search(r"A=(\d+)", ir_text)
    if m:
        result["assignments"] = int(m.group(1))

    # Extract B= base class
    m = re.search(r"B=([^\s#]+)", ir_text)
    if m:
        result["base"] = m.group(1)

    return result


def _describe_flags(flags: str) -> str:
    """Convert flag string to readable description. E.g. 'EILRT' -> 'raises, conditionals, loops, returns, try/except'."""
    parts = [_FLAG_DESCRIPTIONS[f] for f in flags if f in _FLAG_DESCRIPTIONS]
    return ", ".join(parts)


def annotate_behavior_ir(qualified_name: str, kind: str, ir_text: str) -> str:
    """Generate a human-readable annotation for a Behavior IR line."""
    parsed = parse_behavior_ir(ir_text)
    parts = []

    # Name
    parts.append(f"`{qualified_name}`.")

    # Assignments
    if parsed["assignments"] > 0:
        parts.append(f"{parsed['assignments']} assignments,")

    # Flags
    if parsed["flags"]:
        parts.append(f"{_describe_flags(parsed['flags'])}.")

    # Base class
    if parsed["base"]:
        bases = parsed["base"].split(",")
        if len(bases) == 1:
            parts.append(f"Extends `{bases[0]}`.")
        else:
            parts.append(f"Extends {', '.join(f'`{b}`' for b in bases)}.")

    # Calls summary
    calls = parsed["calls"]
    if len(calls) > 5:
        parts.append(f"Calls {len(calls)} functions including `{calls[0]}` and `{calls[1]}`.")
    elif calls:
        parts.append(f"Calls {', '.join(f'`{c}`' for c in calls)}.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_module_line_example(conn: sqlite3.Connection) -> str:
    """Get a representative module line for the Orient section."""
    row = conn.execute("""
        SELECT m.file_path, m.category, m.entity_count
        FROM modules m
        WHERE m.entity_count > 5
        ORDER BY m.entity_count DESC
        LIMIT 1
    """).fetchone()
    if row:
        fp = Path(row[0]).name
        from ir.stable_ids import make_module_base_id
        mid = make_module_base_id(row[0])
        return f"MD {mid} {fp} | cat:{row[1]} | entities:{row[2]} | deps:- | churn:-"
    return "MD MAIN main.py | cat:core_logic | entities:50 | deps:- | churn:-"


def _get_behavior_example(conn: sqlite3.Connection) -> Tuple[str, str]:
    """Pick one rich Behavior IR line and annotate it.

    Returns (ir_text, annotation).
    """
    row = conn.execute("""
        SELECT e.qualified_name, e.kind, r.ir_text
        FROM entities e
        JOIN ir_rows r ON r.entity_id = e.id AND r.mode = 'Behavior'
        LEFT JOIN modules m ON m.file_path = e.file_path
        WHERE e.kind IN ('function', 'method')
          AND (m.category = 'core_logic' OR m.category IS NULL)
          AND r.ir_text LIKE '%C=%'
          AND r.ir_text LIKE '%F=%'
          AND r.ir_text LIKE '%A=%'
        ORDER BY length(r.ir_text) DESC
        LIMIT 1
    """).fetchone()
    if not row:
        return ("FN EXAMPLE C=foo,bar F=ILR A=10 #CORE",
                "`example`. 10 assignments, conditionals, loops, returns. Calls `foo`, `bar`.")
    ir_text = row["ir_text"]
    annotation = annotate_behavior_ir(row["qualified_name"], row["kind"], ir_text)
    return ir_text, annotation


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
## Access to CodeIR

Commands: `search` (by name) | `grep` (by content) | `show` | `expand` | `callers` | `impact` | `scope`

This repository includes a pre-built working model of the entire codebase — its structure, logic, and relationships — that fits in your context window. It's the equivalent of having already read every file and retained the important parts: what each piece does, what it calls, where it fits in the architecture.

### Codebase overview

{bearings_summary}

For the full working map, read `.claude/bearings.md`. For large codebases, drill into specific categories via `.claude/bearings/{{category}}.md`.

### How to use it

The working model is served through **CodeIR**, which gives you three levels of depth. Start shallow, go deeper only as needed.

**Orient** — read bearings files under `.claude/`:
- **`.claude/bearings.md`** — Full working map: every module with ID, filename, category, entity count, and dependencies.
- **`.claude/bearings/{{category}}.md`** — Per-category detail for large codebases. Load only what you need.

Module line format: `{module_line_example}`

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
When planning changes to a method, run `codeir callers <entity>` and expand at least one caller to check sequencing and context constraints.

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

Example:
```
{behavior_ir}
```
→ {behavior_annotation}

### Example workflow

Search doesn't find what you need? Grep for it in source, then drill down:

1. `codeir search "flush"` → no relevant results
2. `codeir grep "def flush" --path orm/` → finds entity `FLSH.04` in `orm/session.py`
3. `codeir show FLSH.04` → see Behavior IR: what it calls, flags, assignments
4. `codeir callers FLSH.04` → see what depends on it
5. `codeir impact FLSH.04 --depth 2` → understand blast radius before changing
6. `codeir expand FLSH.04` → read source only for the entity you need to modify

Start at the highest level of abstraction. Drop to source when you need to verify behavior, check sequencing, or understand how your target is called.
"""


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_rules_file(repo_path: Path) -> str:
    """Generate the complete .claude/rules/CodeIR.md content for a repository."""
    db_path = repo_path / ".codeir" / "entities.db"
    if not db_path.exists():
        raise FileNotFoundError(f"entities DB not found: {db_path}")

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        module_line_example = _get_module_line_example(conn)
        behavior_ir, behavior_annotation = _get_behavior_example(conn)
    finally:
        conn.close()

    # Read bearings summary to embed inline
    bearings_summary_path = repo_path / ".claude" / "bearings-summary.md"
    if bearings_summary_path.exists():
        bearings_summary = bearings_summary_path.read_text(encoding="utf-8").strip()
    else:
        bearings_summary = "(Run `codeir bearings --repo-path .` to generate the codebase overview.)"

    return _TEMPLATE.format(
        bearings_summary=bearings_summary,
        module_line_example=module_line_example,
        behavior_ir=behavior_ir,
        behavior_annotation=behavior_annotation,
    )
