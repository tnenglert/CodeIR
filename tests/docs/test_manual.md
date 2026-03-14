# CodeIR Compression A/B Test Manual

> [!WARNING]
> This manual is legacy and contains workflows that may reference deprecated commands.
> For current commands and mode/level behavior, use:
> - `README.md`
> - `docs/integration_examples.md`

This manual explains how to run and compare compression modes:
- `a` (pattern-role)
- `b` (semantic-lite)
- `hybrid` (both)

## 1. Prerequisites

You need:
- Python 3.9+ (3.13 also works in this repo)
- The repository checked out at:
  - `/Users/pluto/Desktop/CodeSummarizer`
- Fixture repo present at:
  - `tests/testRepositories/_fastapi-users-master`

Optional but recommended:
- `tiktoken` installed for more accurate token accounting.
  - Without it, the code uses a deterministic fallback estimator.

## 2. Key Paths

- CLI entrypoint:
  - `cli.py`
- Test artifacts output directory:
  - `tests/_artifacts`

## 3. Quick Health Check

Run the test suite:

```bash
python3 -m unittest \
  tests/test_fastapi_cli_integration.py \
  tests/test_compression_sampling.py \
  tests/test_eval_modes_integration.py \
  tests/test_label_generation.py
```

Expected:
- `OK` at end of run.

## 4. Run Indexing in a Specific Mode

### Mode A
```bash
python3 cli.py \
  index \
  tests/testRepositories/_fastapi-users-master \
  --mode a
```

### Mode B
```bash
python3 cli.py \
  index \
  tests/testRepositories/_fastapi-users-master \
  --mode b
```

### Hybrid
```bash
python3 cli.py \
  index \
  tests/testRepositories/_fastapi-users-master \
  --mode hybrid
```

Expected output includes:
- `CodeIR indexing complete`
- `compression_mode: <a|b|hybrid>`

## 5. Inspect a Compressed Entity

1. Search for an entity:
```bash
python3 cli.py \
  search auth \
  --repo-path tests/testRepositories/_fastapi-users-master \
  --limit 5
```

2. Show compressed IR for one returned entity ID:
```bash
python3 cli.py \
  show <ENTITY_ID> \
  --repo-path tests/testRepositories/_fastapi-users-master
```

3. Expand raw source for verification:
```bash
python3 cli.py \
  expand <ENTITY_ID> \
  --repo-path tests/testRepositories/_fastapi-users-master
```

## 6. Generate Random Before/After Samples (20-30)

Run:

```bash
python3 -m unittest tests/test_compression_sampling.py
```

Expected:
- Test passes.
- Prints the report path, e.g.:
  - `tests/_artifacts/compression_samples_<timestamp>.md`

## 7. Build Labels for Triage Evaluation

### 7.1 Create starter template
```bash
python3 cli.py \
  labels-template \
  --output tests/_artifacts/labels_template.json
```

Edit that JSON with your real bug/task queries and expected entity IDs.

### 7.2 Auto-generate candidate labels from latest sample artifact
```bash
python3 cli.py \
  labels-from-samples \
  --artifacts-dir tests/_artifacts \
  --output tests/_artifacts/labels_candidates_latest.json \
  --count 20
```

Expected:
- Prints artifact source, output JSON path, and count.

## 8. Evaluate A vs B vs Hybrid (Scoreboard)

### 8.1 Unlabeled comparison (density-focused)
```bash
python3 cli.py \
  eval \
  tests/testRepositories/_fastapi-users-master \
  --modes a,b,hybrid \
  --output tests/_artifacts/scoreboard_latest.md
```

Outputs:
- Markdown table in terminal and file.
- Metrics: `global_ratio`, `avg_ir_tokens`, `entities_per_32k`, `distinctness`, `semantic_signal`.

### 8.2 Labeled triage comparison (accuracy-focused)
```bash
python3 cli.py \
  eval \
  tests/testRepositories/_fastapi-users-master \
  --modes a,b,hybrid \
  --labels tests/_artifacts/labels_candidates_latest.json \
  --output tests/_artifacts/scoreboard_labeled.md
```

Additional labeled metrics:
- `top3_accuracy` (higher is better)
- `avg_false_positives_top3` (lower is better)

## 9. Interpreting Results

- For maximum context density:
  - Prefer lower `avg_ir_tokens` and higher `entities_per_32k`.
- For better comprehensibility:
  - Prefer higher `semantic_signal` and better labeled triage metrics.
- Practical choice:
  - If density is critical, `a` is likely strongest.
  - If comprehension is critical, `b` or `hybrid` usually performs better.

## 10. Common Issues

- `Fixture not found`:
  - Ensure `tests/testRepositories/_fastapi-users-master` exists in ``.
- `No sampling artifacts found`:
  - Run the sampling test first (`test_compression_sampling.py`).
- `labels file must contain a JSON list`:
  - Ensure your labels file root is `[...]`.

## 11. One-Command Quick Start

Run the full workflow in one command:

```bash
scripts/run_compression_eval.sh
```

Optional arguments:

```bash
scripts/run_compression_eval.sh <fixture_path> <label_count>
```

Example:

```bash
scripts/run_compression_eval.sh \
  tests/testRepositories/_fastapi-users-master \
  30
```

This script performs:
- indexing (hybrid)
- before/after sampling test
- labels template + candidate generation
- unlabeled scoreboard
- labeled scoreboard
