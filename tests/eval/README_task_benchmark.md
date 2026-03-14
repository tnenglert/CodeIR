# Task Benchmark Notes

- This benchmark compares four conditions: `raw_baseline`, `naive_rag_bm25`, `naive_rag_embed`, `codeir_flow`.
- `naive_rag_bm25` and `naive_rag_embed` are the credibility baselines for success claims.
- Success requires CodeIR to beat both naive RAG baselines on top-3 localization and warm token efficiency.
- Non-goal: `index/search.py` LIKE search is not treated as a benchmark-quality retrieval baseline.

## Scope Notes

- Phase A (8 tasks) is harness validation only.
- Do not infer product efficacy from Phase A results.
- L2 is intentionally excluded from this benchmark because its inclusion would confound harness validation with unresolved IR design questions.

## Judge Cost Policy

- Primary localization metrics are deterministic (`judge_tokens=0`).
- Optional judged metrics can be added separately with fixed judge prompt/version and explicit token accounting.
