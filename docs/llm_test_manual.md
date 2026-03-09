# SemanticIR LLM Benchmark Manual

> [!WARNING]
> This manual is legacy and may reference commands not exposed by the current CLI.
> For current command surface and Claw-oriented integration flow, use:
> - `README.md`
> - `docs/integration_examples.md`

This manual explains how to run an end-to-end LLM benchmark for compression modes:
- `a` (pattern-role)
- `b` (semantic-lite)
- `hybrid` (combined)

You will:
1. Generate prompt packs.
2. Feed prompts into one or more LLMs.
3. Save model outputs as JSONL predictions.
4. Score results with `eval-llm-results`.

There are two benchmark styles:
- `prompt-benchmark`: query/label based (no injected code mutation)
- `bug-benchmark`: injected bug cases (recommended for realistic triage testing)

## 1. Prerequisites

You need:
- Python 3.9+
- Project root at:
  - `/Users/pluto/Desktop/CodeSummarizer`
- SemanticIR module at:
  - `/Users/pluto/Desktop/CodeSummarizer`
- Fixture repo at:
  - `tests/testRepositories/_fastapi-users-master`
- Labels file (either manually curated or auto-generated)

## 2. Generate Candidate Labels (if needed)

If you do not already have a labels file:

```bash
python3 cli.py \
  labels-from-samples \
  --artifacts-dir tests/_artifacts \
  --output tests/_artifacts/labels_candidates_latest.json \
  --count 20
```

Labels format:
```json
[
  {
    "query": "reset password invalid token",
    "expected_entity_ids": ["AMT_TSTN_41"]
  }
]
```

## 3. Generate Prompt Benchmark Pack

Create prompts for all modes:

```bash
python3 cli.py \
  prompt-benchmark \
  tests/testRepositories/_fastapi-users-master \
  --labels tests/_artifacts/labels_candidates_latest.json \
  --output-dir tests/_artifacts/prompt_benchmark_latest \
  --modes a,b,hybrid \
  --top-k 40
```

Generated files:
- `prompt_benchmark_a.jsonl`
- `prompt_benchmark_b.jsonl`
- `prompt_benchmark_hybrid.jsonl`
- `prompt_benchmark_answer_key.json`
- `prompt_benchmark_manifest.json`
- `prompt_benchmark_README.md`

## 3B. Generate Bug-Injected Benchmark Pack (Recommended)

This creates per-case cloned repos with one injected bug, then builds prompts for each mode.

```bash
python3 cli.py \
  bug-benchmark \
  tests/testRepositories/_fastapi-users-master \
  --output-dir tests/_artifacts/bug_prompt_benchmark_latest \
  --modes a,b,hybrid \
  --cases 20 \
  --top-k 40 \
  --seed 7
```

Generated files:
- `bug_prompt_benchmark_a.jsonl`
- `bug_prompt_benchmark_b.jsonl`
- `bug_prompt_benchmark_hybrid.jsonl`
- `bug_prompt_benchmark_answer_key.json`
- `bug_prompt_benchmark_manifest.json`
- `bug_prompt_benchmark_README.md`

## 4. Run Prompts Through an LLM

Each JSONL line contains a `prompt` field to send to your model.

For each row:
1. Read `case_id`, `mode`, and `prompt`.
2. Send `prompt` text to the LLM.
3. Capture model response.
4. Extract `selected_entity_ids` (top 3 IDs).

The prompt requests strict JSON output:
```json
{"selected_entity_ids": ["ID1", "ID2", "ID3"], "rationale": "..."}
```

## 5. Build Predictions JSONL

Create one JSONL file combining predictions across one or many modes.

Required keys per line:
- `case_id`
- `mode`
- `selected_entity_ids`

Example:
```json
{"case_id":"C001","mode":"a","selected_entity_ids":["MT_GTPN_04","FN_GTBC","AMT_FRGT"]}
{"case_id":"C002","mode":"a","selected_entity_ids":["AMT_FRGT","CL_TSTR_05","MT_TKNX"]}
{"case_id":"C001","mode":"b","selected_entity_ids":["MT_GTPN_04","MT_GTPN_07","FN_GTBC"]}
```

Save as:
- `tests/_artifacts/prompt_benchmark_latest/predictions_<model_name>.jsonl`

## 6. Score LLM Results

Run scorer:

```bash
python3 cli.py \
  eval-llm-results \
  --answer-key tests/_artifacts/<benchmark_dir>/<answer_key>.json \
  --predictions tests/_artifacts/<benchmark_dir>/predictions_<model_name>.jsonl \
  --output tests/_artifacts/<benchmark_dir>/llm_scoring_<model_name>.md
```

You will get:
- terminal markdown summary
- saved markdown report

## 7. Metrics Guide

Per mode:
- `top3_accuracy`: fraction of cases where at least one expected ID appears in top 3.
- `avg_false_positives_top3`: average number of wrong IDs in top 3.
- `precision_at_3`: average fraction of top-3 IDs that are correct.
- `recall_at_3`: average fraction of expected IDs recovered.
- `missing_cases`: number of answer-key cases with no prediction for this mode.

Higher is better:
- `top3_accuracy`
- `precision_at_3`
- `recall_at_3`

Lower is better:
- `avg_false_positives_top3`
- `missing_cases`

## 8. Recommended Evaluation Workflow

1. Prefer `bug-benchmark` for realistic root-cause triage evaluation.
2. Run the benchmark for model A, save `predictions_modelA.jsonl`, score it.
2. Run the benchmark for model B, save `predictions_modelB.jsonl`, score it.
3. Compare `llm_scoring_modelA.md` vs `llm_scoring_modelB.md`.
4. Pick the best compression mode per model/task profile.

## 9. Common Issues

- `answer key not found`:
  - Ensure `prompt-benchmark` ran successfully and produced `prompt_benchmark_answer_key.json`.
- `predictions file contains no valid JSONL rows`:
  - Ensure one valid JSON object per line.
- High `missing_cases`:
  - Your prediction file likely omitted some `case_id`/`mode` rows.
- Poor metrics despite good rationale:
  - Ensure IDs are exact entity IDs (case-sensitive).
