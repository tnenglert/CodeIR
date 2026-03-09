# SemanticIR

SemanticIR is a deterministic code compression and indexing tool for Python repositories. It produces compact IR rows, stable entity IDs, and a searchable local store designed for integration with agent frameworks like Claw.

## Current Status

**Fully implemented:**

- Python AST extraction for functions, methods, async functions/methods, and classes
- Deterministic entity IDs with collision suffixes (e.g., `FN_AUTH`, `FN_AUTH_02`)
- Incremental indexing with file hash tracking (4-pass pipeline)
- Multi-level IR output (`L0`, `L1`, `L2`, `L3`, or `all`) with L1 as default
- Repository-local SQLite store in `.semanticir/`
- CLI workflows: index, search, show, expand, compare, stats, module-map, bearings, eval, floor-test
- Tool wrappers in `tools/api_schemas.py` for agent tool-calling
- File classification into 11 categories (core_logic, router, schema, config, compat, exceptions, constants, tests, init, docs, utils)
- Passthrough mode for small entities (<12 tokens emit L0 regardless of requested level)
- Comprehensive test suite (16 tests passing)

**Not implemented:**

- Temporal history persistence (design documented in `docs/temporal_design.md`, module is a stub)
- Cross-language parsing (Python only)
- Patch rendering / code regeneration APIs

## Claw Integration

SemanticIR is designed for Claw-style agent loops where the model reasons on compressed artifacts first and expands source only when needed.

**Design principles:**

- **Deterministic outputs:** Same source produces identical IDs and IR for reproducible tool calls
- **Stable local state:** `.semanticir/` store persists across agent runs
- **Progressive disclosure:** Agents work with compressed IR by default, expanding to source explicitly
- **Fast orientation:** `bearings` command generates a compact module map for session bootstrap

**Tool API for agents:**

```python
search_entities(query, repo_path=".", limit=20)  # Text search with ranking
get_entity_ir(entity_id, repo_path=".", level="L1")  # Get IR at compression level
expand_entity_code(entity_id, repo_path=".")  # Get raw source
```

All tools return JSON with structured errors (`{"ok": false, "hint": "..."}`).

## Compression Model

### Levels

- `L0`: raw source slice with entity boundary marker.
- `L1`: semantic-lite row (`N`, `C`, `F`, `A`, `B` fields) with optional domain/category tags (`#HTTP`, `#AUTH`, `#CORE`, `#EXCE`, ...).
- `L2`: type-signature/flags focused row.
- `L3`: structural pattern ID + module category tag.
- `all`: materialize all levels for each entity.

Validation note:

- Active validation scope is `L0`, `L1`, and `L3`.
- `L2` is implemented but intentionally excluded from the current benchmark program to avoid conflating harness issues with unresolved IR-design questions.

### Legacy Mode Mapping

Older docs/scripts may refer to `a`, `b`, `hybrid`.
These are now mapped to levels for backward compatibility:

- `a` -> `L3`
- `b` -> `L1`
- `hybrid` -> `L2`

## Quick Start

### 1. Index a repository

```bash
python3 cli.py index <repo_path> --level L1
```

Or with legacy alias:

```bash
python3 cli.py index <repo_path> --mode b
```

### 2. Search and inspect entities

```bash
python3 cli.py search "auth token" --repo-path <repo_path> --limit 10
python3 cli.py show <ENTITY_ID> --repo-path <repo_path> --level L1
python3 cli.py expand <ENTITY_ID> --repo-path <repo_path>
```

### 3. Compare and summarize repository structure

```bash
python3 cli.py compare <ENTITY_ID> --repo-path <repo_path>
python3 cli.py stats --repo-path <repo_path>
python3 cli.py module-map --repo-path <repo_path>
python3 cli.py bearings --repo-path <repo_path>
```

### 4. Evaluate compression levels

```bash
python3 cli.py eval <repo_path> --levels L1 L2 L3
```

Legacy style:

```bash
python3 cli.py eval <repo_path> --modes a,b,hybrid
```

### 5. Comprehensibility floor testing

```bash
python3 cli.py floor-test generate <repo_path> --level L1 --count 15
python3 cli.py floor-test score <results_json_path>
```

### 6. Unified task benchmark (dual naive RAG baselines)

```bash
python3 tests/eval/runners/run_task_benchmark.py \
  --repo-path <repo_path> \
  --task-pack tests/eval/test_packs/task_benchmark_small.json \
  --output tests/eval/results/task_benchmark_results.json
```

Metrics reducer:

```bash
python3 tests/eval/metrics/compute_task_metrics.py \
  tests/eval/results/task_benchmark_results.json
```

## Tool API (for Agent Hosts)

`tools/api_schemas.py` exposes wrappers intended for tool-calling hosts:

- `search_entities(query, repo_path=".", limit=20)`
- `get_entity_ir(entity_id, repo_path=".", level="L1")`
- `expand_entity_code(entity_id, repo_path=".")`

All wrappers return JSON-serializable dicts and include structured errors (`ok: false`) when index artifacts are missing.

## Programmatic Usage

```python
from pathlib import Path
from index.indexer import index_repo
from tools.api_schemas import search_entities, get_entity_ir

repo = Path("/path/to/repo")
index_repo(repo, {"extensions": [".py"], "hidden_dirs": [".git", ".semanticir"], "compression_level": "L1"})

hits = search_entities("auth token", repo_path=repo, limit=5)
if hits["ok"] and hits["results"]:
    entity_id = hits["results"][0]["entity_id"]
    ir = get_entity_ir(entity_id, repo_path=repo, level="L1")
```

## Storage Layout

SemanticIR writes repository-local state to `.semanticir/`:

**entities.db:**

| Table | Description |
|-------|-------------|
| `entities` | Entity metadata: id, kind, name, qualified_name, file_path, start/end lines, module_id, complexity_class |
| `ir_rows` | Compressed IR per (entity_id, mode): ir_text, ir_json, token/char counts, compression_ratio |
| `modules` | File classifications: category, content_hash, entity_count, internal dependencies |
| `file_metadata` | Hash tracking for incremental indexing: content_hash, last_indexed_at, byte_size |
| `index_meta` | Index-level metadata (key-value store) |

**mapping.db:**

| Table | Description |
|-------|-------------|
| `abbreviations` | Token maps: map_type, original token, abbreviated token, version |
| `abbrev_meta` | Abbreviation metadata (key-value store) |

Optional config file: `.semanticir/config.json`

## Repository Structure

```text
CodeSummarizer/
├── cli.py
├── ir/
│   ├── compressor.py
│   ├── abbreviations.py
│   ├── stable_ids.py
│   ├── classifier.py
│   └── token_count.py
├── index/
│   ├── indexer.py
│   ├── locator.py
│   ├── search.py
│   ├── eval.py
│   ├── floor_test.py
│   ├── prompt_benchmark.py
│   ├── bug_benchmark.py
│   └── store/
│       ├── db.py
│       ├── fetch.py
│       ├── stats.py
│       └── schema.json
├── tools/
│   └── api_schemas.py
├── docs/
│   ├── IR_spec.md
│   ├── integration_examples.md
│   ├── temporal_design.md
│   ├── Rationale.md
│   └── Future_Considerations.md
├── tests/
│   ├── test_*.py
│   ├── eval/
│   │   ├── runners/
│   │   ├── metrics/
│   │   ├── test_packs/
│   │   └── results/
│   └── testRepositories/
└── scripts/
```

## Testing

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

## Documentation

- As-built IR spec: `docs/IR_spec_as_built_v0_2.md`
- Machine contract: `docs/IR_contract_v0_2.json`
- Target architecture: `docs/IR_target_architecture.md`
- Integration examples: `docs/integration_examples.md`
- Temporal design/status: `docs/temporal_design.md`
- Rationale: `docs/Rationale.md`
- Canonical L1 preamble: `tests/eval/preambles/l1_preamble.md`
- Canonical L3 preamble: `tests/eval/preambles/l3_preamble.md`
