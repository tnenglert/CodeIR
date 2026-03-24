# Integration Tests

Integration tests verify CodeIR tool workflows against a real indexed repository. They catch regressions in resolution logic, ambiguity handling, and end-to-end tool sequences.

## Running Tests

```bash
# All tests (unit + integration)
python -m pytest tests/ -v --ignore=tests/_local

# Integration tests only
python -m pytest tests/tool_integration_test.py -v

# Specific test class
python -m pytest tests/tool_integration_test.py::TestCallersResolution -v
```

## Fixture

Tests use `tests/_local/testRepositories/_fastapi-users-master` as the fixture. This is a real codebase with:
- 748 entities
- Known ambiguity patterns (6 `update` entities, 6 `create`, etc.)
- Attribute-chain calls (`self.password_helper.hash()`)

The fixture auto-indexes if `.codeir/` doesn't exist.

---

## Test Classes

### TestCallersResolution

Tests that the `callers` command correctly resolves dependencies.

| Test | What it verifies |
|------|------------------|
| `test_hash_callers_resolved` | Qualified calls like `password_helper.hash()` bypass the stoplist and resolve. Before the fix, `callers HASH.02` returned nothing because `hash` matched the Python builtin in CALL_STOPLIST. |
| `test_local_callers_found` | Same-file callers always resolve via local resolution. This is the most reliable resolution tier. |
| `test_ambiguous_calls_surfaced` | When fuzzy resolution exceeds FUZZY_MATCH_LIMIT (4 candidates), the response includes an `ambiguous` array with candidate count. |
| `test_suggestions_for_ambiguity` | Ambiguous results include grep suggestions so the model knows how to work around the limitation. |

**Why these matter:** The callers command is critical for understanding dependencies before making changes. Silent resolution failures lead to incomplete impact analysis.

---

### TestSearchAndShow

Tests basic entity discovery and inspection.

| Test | What it verifies |
|------|------------------|
| `test_search_finds_entity` | Search by name returns relevant entities. Verifies the search index works. |
| `test_show_returns_ir` | Show command returns behavioral IR with `C=` (calls) and `F=` (flags) fields. Also verifies qualified calls appear in output. |
| `test_scope_returns_context` | Scope command returns callers, callees, and siblings for edit context. |

**Why these matter:** These are the primary orientation tools. If search or show breaks, the entire workflow fails.

---

### TestGrepFallback

Tests that grep works as a fallback when callers resolution is incomplete.

| Test | What it verifies |
|------|------------------|
| `test_grep_finds_update_calls` | Grep finds `.update(` calls in router files that callers resolution missed due to ambiguity. |
| `test_grep_finds_create_calls` | Same pattern for `.create(` calls. |

**Why these matter:** Grep is the recommended workaround when ambiguity prevents resolution. These tests ensure the fallback path works.

---

### TestImpactAnalysis

Tests dependency graph traversal for change impact.

| Test | What it verifies |
|------|------------------|
| `test_impact_traverses_callers` | Impact command follows caller relationships through the graph. Verifies `GNRTJWT` (generate_jwt) shows `JWTStrategy` as dependent. |
| `test_impact_shows_depth` | Impact output distinguishes direct vs transitive dependencies. |

**Why these matter:** Impact analysis is how you assess blast radius before making changes. Broken traversal = missed dependencies = broken code.

---

### TestWorkflowIntegration

Tests complete end-to-end workflows simulating real tasks.

| Test | What it verifies |
|------|------------------|
| `test_bug_investigation_workflow` | Simulates "find where password hashing happens": search → show → callers. Verifies the full sequence works together. |
| `test_refactor_workflow` | Simulates "understand verification flow before modifying": search → scope → impact. |

**Why these matter:** Individual tools might work but fail when combined. These tests catch integration issues.

---

### TestAmbiguityPatterns

Tests specific ambiguity patterns we've identified in the codebase.

| Test | What it verifies |
|------|------------------|
| `test_entity_collision_counts[update-6]` | Verifies there are exactly 6 entities named `update` (exceeds FUZZY_MATCH_LIMIT of 4). |
| `test_entity_collision_counts[create-6]` | Same for `create`. |
| `test_entity_collision_counts[delete-7]` | Same for `delete`. |
| `test_entity_collision_counts[get-5]` | `get` has 5 entities (at the limit). |
| `test_entity_collision_counts[hash-2]` | `hash` has only 2 entities (under limit, should resolve). |
| `test_qualified_calls_bypass_stoplist` | Verifies `ATHNTCT.02` stores qualified calls like `password_helper.hash` instead of just `hash`. |

**Why these matter:** These are regression tests for the specific bug we fixed. If entity counts change or qualified call extraction breaks, we'll know immediately.

---

## Adding New Tests

When adding tests, consider:

1. **What workflow does this test?** Map to real user tasks.
2. **What failure mode does this catch?** Be specific about the regression.
3. **Does it need the fixture?** Use `indexed_repo` fixture for tests that need the database.

Example template:

```python
def test_new_feature(self, indexed_repo):
    """One-line description of what this verifies."""
    # Step 1: Run tool
    result = run_tool("codeir_xxx", {"entity_id": "..."}, indexed_repo)

    # Step 2: Assert expected behavior
    assert result["data"] is not None
    assert "expected_field" in result["data"]
```

---

## Maintenance

- **Fixture changes:** If fastapi-users updates, collision counts may change. Update `test_entity_collision_counts` parameters.
- **New ambiguity patterns:** Add parametrized tests for newly discovered high-collision names.
- **New tools:** Add test class for each new tool following existing patterns.
