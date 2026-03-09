# Temporal Layer Design (Current Status + Plan)

## Current Status

Temporal tracking is not implemented in the current runtime.

- `index/temporal.py` is currently a stub.
- No temporal tables are created in the active schema.
- CLI does not expose temporal commands yet.

This means SemanticIR currently behaves as a snapshot index with deterministic incremental updates, not history-aware analytics.

## Why This Matters for Claw Compatibility

For Claw/OpenClaw/NanoClaw workflows, temporal support should enable:

- "What changed most recently?" style retrieval.
- churn-based ranking during triage.
- entity stability signals for prioritization.

Until implemented, agents should not assume availability of history, churn, or commit-linked metadata.

## Proposed v1 Temporal Scope

### Data model (high-level)

- `entity_events`
  - `entity_id`
  - `event_type` (`created`, `signature_changed`, `moved`, `deleted`, `behavior_changed`)
  - `indexed_at`
  - `details_json`
- `entity_rollups`
  - `entity_id`
  - `change_count`
  - `last_changed_at`
  - `churn_30d`

### Pipeline hook points

1. During incremental pass, diff old/new entity snapshots.
2. Emit semantic events per changed entity.
3. Update rollups for fast retrieval in tool calls.

### Intended tool additions

- `get_entity_history(entity_id, repo_path, limit=20)`
- `list_hotspots(repo_path, window_days=30, limit=50)`

## Backward-Compatible Rollout

Temporal features should be optional and non-breaking:

- Default behavior remains current snapshot indexing.
- Temporal writes guarded behind config flag (for example `temporal_mode`).
- Existing tool payloads remain unchanged when temporal is disabled.
