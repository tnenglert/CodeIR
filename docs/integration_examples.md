# Integration Examples

This document shows practical integration patterns for CodeIR in agent systems.

## 1. Typical Agent Workflow

Use this sequence for bug-triage or code-understanding sessions:

1. Index repository (`Behavior` or `Behavior+Index`).
2. Generate `bearings.md` for fast architecture orientation.
3. Use `search_entities` for candidate discovery.
4. Use `get_entity_ir` for compressed reasoning.
5. Use `expand_entity_code` only when source text is required.

## 2. CLI Bootstrap

```bash
codeir index <repo_path> --level Behavior
codeir bearings --repo-path <repo_path>
codeir rules --repo-path <repo_path>
```

## 3. Tool Wrapper Integration (Python Host)

```python
from pathlib import Path
from tools.api_schemas import search_entities, get_entity_ir, expand_entity_code

repo = Path("/path/to/repo")

hits = search_entities("oauth callback token", repo_path=repo, limit=8)
if hits["ok"] and hits["results"]:
    top = hits["results"][0]["entity_id"]
    ir = get_entity_ir(top, repo_path=repo, level="Behavior")
    # Use IR for reasoning first
    if ir["ok"]:
        print(ir["entity"]["ir_text"])
    # Expand only when needed
    src = expand_entity_code(top, repo_path=repo)
```

## 4. Suggested Tool Contract for Agent Hosts

Expose these 3 tools to the model:

- `search_entities(query, repo_path, limit)`
- `get_entity_ir(entity_id, repo_path, level)`
- `expand_entity_code(entity_id, repo_path)`

Expected behavior:

- Return JSON objects, not plain strings.
- On failures, return `{"ok": false, "error": "...", "hint": "..."}`.
- Keep IR calls as default path; expansion is opt-in.

## 5. Agent Prompting Pattern

Recommended instruction style:

- "Use `search_entities` first to narrow candidates."
- "Use `get_entity_ir` on top candidates before requesting source."
- "Call `expand_entity_code` only for patch drafting or human-readable verification."

This keeps token cost low and preserves deterministic reasoning over stable IDs.

## 6. Session Warm Start with `bearings.md`

`bearings.md` provides a module-level map with category and internal deps:

```bash
codeir bearings --repo-path <repo_path>
```

Use it as first context file in your agent workspace before running entity-level tools.
The `codeir rules` command generates a `.claude/rules/CodeIR.md` that embeds the
bearings summary inline, so both get cached as one block on the first turn.
