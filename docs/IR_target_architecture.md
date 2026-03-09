# SemanticIR Target Architecture

This document captures intended behavior that is not yet guaranteed by current implementation.

## Intent

- Use `L3` for global orientation.
- Use `L1` for navigation and relevance filtering.
- Require `L0` verification before write-time actions.
- Optimize compression policy from observed expansion behavior.

## Target Runtime Behaviors

- First-contact promotion to `L1` within a session.
- Automatic `L0` gating for write operations.
- Pre-flight freshness checks before serving retrievals.
- Session-level expansion logging and policy feedback loops.

## Evaluation Direction

- Validate task-level quality against credible baselines.
- Track net token economics per completed task.
- Calibrate false-confidence risk over observed confidence labels.

## Separation Rule

- `docs/IR_spec_as_built_v0_2.md` defines current contract.
- This file defines targets and roadmap expectations.
