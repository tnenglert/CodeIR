<!-- codeir-skill -->
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
