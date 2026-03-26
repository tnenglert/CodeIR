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

You must minimize total tool calls. Prefer one decisive tool call over
several exploratory ones.

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
Bearings makes category-scoped search more effective.

**Search** — find entities by name:
```
codeir search <terms> [--category <cat>]
```
After `bearings`, prefer `--category` to narrow to the most likely area.

**Grep** — regex search across source, grouped by entity:
```
codeir grep <pattern> [--path <dir_or_glob>] [--path <dir_or_glob>] [-i] [-C N] [-v]
codeir grep <pattern> --evidence [--path <dir_or_glob>] [-i]
codeir grep <pattern> --count [--path <dir_or_glob>] [--path <dir_or_glob>]
```
Use this for census/pattern tasks where you need all occurrences, but want
entity context alongside matches.
Use `--evidence` instead of `rg -n ...` followed by `sed -n ...` when you
want exact matching lines, nearby context, and the owning entity in one call.
Use `--count` instead of `rg ... | wc -l` or `cut | sort | uniq -c` when you
need grouped counts by entity/file without printing the match lines.

**Inspect** — compact behavior snapshots for one or more entities:
```
codeir show <entity_id> [<entity_id> ...] [--level Index|Behavior]
```
Use this to narrow candidates quickly. If you already know you need the
full implementation, skip `show` and use `expand`.

**Expand** — raw source when you need to edit or verify:
```
codeir expand <entity_id>              # single entity
codeir expand <entity_id> --number     # source with line numbers for citation
codeir expand <id1> <id2> <id3>        # multiple entities in one call
codeir expand 'STEM.*'                 # all siblings (STEM, STEM.01, STEM.02, ...)
```

**Trace** — shortest static call path between two entities:
```
codeir trace <from_entity> <to_entity> [--depth N] [--resolution import|local|fuzzy|any]
```
Use this for path-shaped questions like "how does X trigger Y?" or "how do
we get from this entry point to that hook?"

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

### Three workflows

**Show mode** — understanding tasks

Use when the goal is to explain behavior, identify likely cause, or compare
a few candidate entities.

1. `codeir bearings` → orient
2. `codeir search "..." --category <cat>` → find candidates
3. `codeir show <id>` → read Behavior IR
4. `codeir expand <id>` only if one finalist needs verification

If `show` already answers the question, stop there.

**Expand mode** — implementation tasks

Use when the goal is to change code safely.

1. `codeir bearings` → orient
2. `codeir search "..." --category <cat>` → find likely edit targets
3. `codeir show <id>` → confirm the right entity
4. `codeir scope <id>` or `codeir callers <id>` if blast radius matters
5. `codeir expand <id>` for the entities you will edit

If you already expect to need full source, skip `show` and go straight to
`expand`. Expand only the finalists you expect to modify.

**Grep mode** — census tasks

Use when the goal is to find patterns, conventions, or all occurrences
across the repo.

1. `codeir bearings` → orient
2. `codeir grep "..." --path ...` → find matching entities
3. `codeir show <id>` if you need behavior context
4. `codeir expand <id>` only for representative examples

Prefer `codeir grep` over raw text grep when entity ownership matters.
Prefer `codeir grep --evidence` over `rg -n ...` then `sed -n ...` when you
want exact lines and nearby proof without a separate source-read step.
Use repeated `--path` flags instead of shell loops when you need one census
across `lib`, `test`, `examples`, or `docs`.

**Trace mode** — path questions

Use when the goal is to connect an entry point to an effect, hook, or
downstream behavior.

1. `codeir bearings` → orient
2. `codeir search "..." --category <cat>` → identify likely endpoints
3. `codeir trace <from> <to>` → find the shortest static call path
4. `codeir expand <id>` only for the path nodes that need verification

Use `trace` instead of manually chaining `callers`, `search`, `grep`, and
line-range reads when the task is primarily "how do we get from A to B?"

### Selection rules

- You must minimize total tool calls. Prefer one decisive tool call over
  several exploratory ones.
- Use `show` for a compact behavior snapshot only when it might change whether an
  entity is relevant.
- Use `expand` when you already know you need the full implementation or
  expect to edit the entity.
- Use `expand --number` when you need exact source lines with stable line
  numbers for citation or proof.
- Do not `show` an entity immediately before `expand` unless the `show`
  result could change your decision.
- Do not `expand` weak matches just to be sure. Keep narrowing with
  `search`, `grep`, or `show` until only a small finalist set remains.
- After a multi-entity `show`, either discard a candidate or `expand` it.
  Do not `show` the same entity again individually unless the first output
  was incomplete.
- Use `codeir grep --evidence` instead of `rg -n ...` followed by
  `sed -n ...` when you need exact matching lines plus nearby proof.

### Annotated entity lists

Output from `callers`, `impact`, and `scope` includes inline triage metadata:
```
  CMPT.02         [47 callers] →ModelSQL   core_logic/tax.py      [class, ~180 lines]
  GTMVLN.03       [3 callers]              core_logic/move.py     [method, ~25 lines]
```

- `[N callers]` — connectivity/importance
- `→Pattern` — pattern membership (standard infrastructure)
- `[kind, ~N lines]` — entity type and size

Results are smart-sorted (high-caller core logic first, tests last) and
truncated to 15 by default. Use `--all` to see the complete list.

### Reading compressed representations

Behavior fields:
- `FN` / `CLS` / `MT` / `AMT` — function, class, method, async method
- `C=` — calls made
- `F=` — flags: `R`=returns, `E`=raises, `I`=conditionals, `L`=loops, `T`=try/except, `W`=with
- `A=` — assignment count
- `B=` — base class
- `#TAG` — domain and category tags
