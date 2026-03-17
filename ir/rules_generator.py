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

Commands: `bearings` | `search` | `grep` | `show` | `expand` | `callers` | `impact` | `scope`

This repository includes a pre-built working model of the entire codebase — its structure, logic, and relationships — that fits in your context window.

Orient via `codeir bearings` — shows project summary with a menu of category-specific views and token estimates. For large codebases, load only the categories you need.

### Two workflows

**Bug fix / investigation** — find the problem fast:

1. `codeir bearings` → orient
2. `codeir search` → find the most likely entity
3. `codeir show` → read its Behavior IR
4. `codeir expand` → read source, form your hypothesis

**Stop here.** If the source confirms your hypothesis, propose the fix. Do not expand additional entities to verify what you can already see. Do not search for how the bug is triggered elsewhere. If your theory is wrong, you'll know — go back to step 2.

**Architecture / refactor** — understand before changing:

1. `codeir bearings` → orient on project structure
2. `codeir search` → find relevant entities
3. `codeir show` → understand behavior and call relationships
4. `codeir callers` / `codeir impact` → map what depends on your target
5. `codeir scope` → get the full context needed for safe modification
6. `codeir expand` → read source for entities you need to change

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

Example:
```
{behavior_ir}
```
→ {behavior_annotation}
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
        behavior_ir, behavior_annotation = _get_behavior_example(conn)
    finally:
        conn.close()

    return _TEMPLATE.format(
        behavior_ir=behavior_ir,
        behavior_annotation=behavior_annotation,
    )
