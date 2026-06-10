# Search Caller Count A/B Benchmark

This benchmark tests a narrow UX question:

Does adding `Callers=N` to `codeir search` results help models choose the
right entity faster, or does it add distracting noise?

The benchmark is intentionally focused on **triage**, not full task
completion. Each task asks the model to identify the *single best entity to
inspect first* for a concrete change or investigation.

## Why this benchmark exists

`codeir search` is often the first narrowing step. Adding caller count could:

- help models prioritize central entities more quickly
- reduce unnecessary `show` / `expand` calls
- or bias them toward high-caller entities even when the right answer is a leaf

This benchmark is designed to separate those cases.

## Task pack

The tasks live in:

- `/Users/pluto/Desktop/CodeIR/tests/eval/task_pack_search_callers_ab_v1.json`

They are balanced across two buckets:

- `central`: caller count is hypothesized to be helpful
- `leaf`: caller count might be noise or actively misleading

## Response format

Use the standard response JSON format:

```json
{
  "model": "model-name",
  "responses": {
    "S1": {
      "tool_calls": [
        "codeir search annotated entity format",
        "codeir show FRMTNNTTDNTT"
      ],
      "answer": "Inspect FRMTNNTTDNTT in cli.py first because it formats annotated entity rows.",
      "entities_mentioned": ["FRMTNNTTDNTT"]
    }
  }
}
```

## Running the benchmark

Run the same task pack twice:

1. Variant A: `codeir search` includes `Callers=N`
2. Variant B: `codeir search` does not include caller count

Keep all other conditions the same:

- same repo snapshot
- same model
- same task prompts
- same system / agent prompt
- same benchmark harness

Then score each response file:

```bash
python tests/eval/score_search_callers_ab.py responses_with_callers.json \
  --task-pack tests/eval/task_pack_search_callers_ab_v1.json

python tests/eval/score_search_callers_ab.py responses_without_callers.json \
  --task-pack tests/eval/task_pack_search_callers_ab_v1.json
```

## What the scorer measures

Per task:

- whether the model found an accepted target entity
- whether it mentioned the expected file
- whether the **first inspection command** hit the target
- how many inspection calls it made
- how many wrong inspections happened before the first hit

The scorer treats these commands as inspection steps:

- `show`
- `expand`
- `scope`
- `callers`
- `impact`
- `trace`

## How to interpret results

The two most important metrics are:

- `first_hit_rate`
- `avg_wrong_before_hit`

If `Callers=N` is helping, you should expect:

- higher `first_hit_rate`
- lower `avg_wrong_before_hit`
- especially on `central` tasks

If it is mostly noise, you will likely see:

- little or no change
- or worse behavior on `leaf` tasks

## Recommended A/B readout

Compare:

- overall score
- overall first-hit rate
- overall average inspection calls
- `central` vs `leaf` bucket splits

That split is important. A feature that only helps on `central` tasks but
hurts on `leaf` tasks may still be useful, but it should not be treated as a
universal improvement.
