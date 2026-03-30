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
