# CodeIR Tool Usage Evaluation

Evaluate how well LLMs use CodeIR tools to accomplish software engineering tasks.

## Quick Start

```bash
# 1. Run a model on the tasks (manually or via script)
# 2. Save responses to JSON
# 3. Score:
python tests/eval/score_tool_tasks.py responses.json
```

## Task Pack Structure

`task_pack_tools_v1.json` contains 10 tasks across 5 types:

| Type | Tasks | What it tests |
|------|-------|---------------|
| `targeted_bug` | A1, A2 | Narrow reasoning to find specific code |
| `conceptual_understanding` | B1, B2 | Explaining flows and architecture |
| `medium_refactor` | C1, C2 | Identifying all change points |
| `dependency_sensitive` | D1, D2 | Using impact/callers analysis |
| `search_failure` | E1, E2 | Handling ambiguous or hard-to-find code |

### Task Fields

```json
{
  "id": "A1",
  "type": "targeted_bug",
  "prompt": "The prompt to give the model",
  "ground_truth": {
    "required_entities": ["ATHNTCT.02", "HASH.02"],
    "required_concepts": ["timing attack"],
    "key_file": "fastapi_users/manager.py"
  },
  "scoring": {
    "found_entities": 40,
    "mentioned_concepts": 30,
    "identified_file": 20,
    "explained_mechanism": 10
  }
}
```

## Response Format

Save model responses as JSON:

```json
{
  "model": "claude-3-opus",
  "responses": {
    "A1": {
      "tool_calls": [
        "codeir search 'hash password'",
        "codeir show ATHNTCT.02",
        "codeir expand ATHNTCT.02"
      ],
      "answer": "The model's full response text...",
      "entities_mentioned": ["ATHNTCT.02", "HASH.02"]
    }
  }
}
```

### Response Fields

| Field | Required | Description |
|-------|----------|-------------|
| `tool_calls` | Optional | List of tool calls made (for analyzing tool usage patterns) |
| `answer` | Required | The model's full text response |
| `entities_mentioned` | Optional | Entity IDs found. If omitted, auto-extracted from answer. |

## Scoring

```bash
python tests/eval/score_tool_tasks.py responses.json
```

Output:
```
============================================================
Scoring: claude-3-opus
============================================================

[PASS   ] A1: 85/100 (85%) - targeted_bug
          found_entities: +40
          concepts: +30
          file: +15
[PARTIAL] B1: 55/100 (55%) - conceptual_understanding
          found_entities: +24
          flow_order: +31

============================================================
Summary by Type:
============================================================
  targeted_bug              170/200 (85%)
  conceptual_understanding  110/200 (55%)
  ...

============================================================
OVERALL: 450/1000 (45%)
============================================================
```

### Scoring Logic

Each task type has specific scoring criteria:

**targeted_bug:**
- Found required entities (40%)
- Mentioned key concepts (30%)
- Identified correct file (20%)
- Explained mechanism (10%)

**conceptual_understanding:**
- Found required entities (30%)
- Described flow in correct order (40%)
- Identified entry points (20%)
- Identified callbacks (10%)

**medium_refactor:**
- Found primary entity (30%)
- Identified all failure/entry points (50%)
- Suggested correct locations (20%)

**dependency_sensitive:**
- Found entity (20%)
- Ran impact or callers analysis (20%)
- Identified direct callers (30%)
- Identified affected flows (20%)
- Warned about scope (10%)

**search_failure:**
- Found relevant entities (40%)
- Identified key pattern/exception (30%)
- Traced enforcement logic (30%)

## Running Multiple Models

```bash
# Run each model, save responses
python run_model.py --model gpt-4 --output responses_gpt4.json
python run_model.py --model claude-opus --output responses_opus.json

# Score all
for f in responses_*.json; do
  python tests/eval/score_tool_tasks.py "$f" --output "scored_${f}"
done

# Compare
python compare_results.py scored_*.json
```

## Adding Tasks

1. Add task to `task_pack_tools_v1.json`
2. Include ground truth (entities, concepts, files)
3. Define scoring weights (should sum to 100)
4. Test with example response

## Fixture

Tasks use `tests/_local/testRepositories/_fastapi-users-master`. Ensure it's indexed:

```bash
python cli.py index tests/_local/testRepositories/_fastapi-users-master --level Behavior
```

## Interpreting Results

| Score | Interpretation |
|-------|----------------|
| 70%+ | PASS - Model effectively used tools |
| 40-70% | PARTIAL - Found some info but missed key points |
| <40% | FAIL - Significant gaps in tool usage or understanding |

### Common Failure Patterns

1. **Low entity scores:** Model not using search/show effectively
2. **Low flow_order scores:** Model found entities but didn't understand relationships
3. **Missing ambiguity recognition:** Model didn't notice callers limitation
4. **No grep fallback:** Model got stuck when callers was incomplete
