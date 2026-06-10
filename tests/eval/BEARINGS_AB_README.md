# Full Bearings A/B Benchmark

This benchmark tests a narrow UX question:

Does a conservative full-bearings refresh help models pick the right next
inspection target faster, or is the current bearings format already sufficient?

The benchmark is intentionally focused on **orientation and triage**, not full
task completion.

## Why this benchmark exists

`codeir bearings --full` is meant to help a model land in a repo cold and pick
the next category, file, or entity to inspect. The conservative refresh in this
benchmark is intentionally small:

- better duplicate-filename disambiguation
- `dom:<domain>` on module lines when known
- cleaner omission of empty/noisy fields

This benchmark is designed to measure whether those primitive signals improve
first-step navigation without turning bearings into a recommendation engine.

The current task pack uses two fixture repos with different shapes:

- Flask, for smaller-library/app orientation and duplicate-name pressure
- Tryton, for large repo orientation and server-vs-client disambiguation

## Task pack

The tasks live in:

- `/Users/pluto/Desktop/CodeIR/tests/eval/task_pack_bearings_ab_v1.json`
- `/Users/pluto/Desktop/CodeIR/tests/eval/task_pack_bearings_ab_v2.json`

They are balanced across three buckets:

- `zone_selection`
- `duplicate_name_disambiguation`
- `domain_signal`

## Running the benchmark

Run the same task pack twice:

1. Variant A: current full bearings
2. Variant B: refreshed full bearings

Keep all other conditions the same:

- same repo snapshot
- same model
- same system / agent prompt
- same benchmark harness

For the current `v1` pack, that means generating and benchmarking bearings for:

- `/Users/pluto/Desktop/CodeIR/tests/_local/testRepositories/_flask-main`
- `/Users/pluto/Desktop/CodeIR/tests/_local/testRepositories/tryton-main`

The `v2` pack uses the same repos and targets, but explicitly asks the model to
verify its chosen entity with `show`, `expand`, `scope`, `callers`, `impact`,
or `trace` before answering.

To switch the repo between refreshed and baseline bearings without manual patching:

```bash
python tests/eval/scripts/bearings_ab_swap.py snapshot
python tests/eval/scripts/bearings_ab_swap.py apply-baseline
# run baseline benchmark
python tests/eval/scripts/bearings_ab_swap.py restore
# run refreshed benchmark
```

Then score each response file:

```bash
python tests/eval/score_bearings_ab.py responses_baseline.json \
  --task-pack tests/eval/task_pack_bearings_ab_v1.json

python tests/eval/score_bearings_ab.py responses_refreshed.json \
  --task-pack tests/eval/task_pack_bearings_ab_v1.json
```

## What the scorer measures

Per task:

- whether the model found an accepted target entity
- whether it mentioned the expected file
- whether it selected the expected category/zone
- whether the first inspection command hit an accepted target
- how many wrong inspections happened before the first hit

The scorer treats these commands as inspection steps:

- `show`
- `expand`
- `scope`
- `callers`
- `impact`
- `trace`

## How to interpret results

The most important metrics are:

- `first_hit_rate`
- `category_choice_accuracy`
- `avg_wrong_before_hit`

If the conservative bearings refresh is helping, you should expect:

- higher or equal `first_hit_rate`
- higher `category_choice_accuracy`
- lower `avg_wrong_before_hit`
- especially on `duplicate_name_disambiguation` and `domain_signal` tasks

If it is mostly noise, you will likely see:

- little or no change
- or regressions in one of the task buckets

## Recommended A/B readout

Compare:

- overall score
- overall first-hit rate
- overall category-choice accuracy
- overall average inspection calls
- bucket splits (`zone_selection`, `duplicate_name_disambiguation`, `domain_signal`)

That bucket split matters. If the refresh only helps on duplicate-name tasks,
that is still useful, but it should not be treated as a general bearings
improvement without evidence from the other buckets.
