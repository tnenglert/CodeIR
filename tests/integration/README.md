# Integration Tests

The old fixture-backed integration workflow tests were retired after the
`_fastapi-users-master` benchmark fixture went stale and was removed from
the repository.

At the moment, CodeIR coverage comes from:

- focused unit tests for frontends and parsing behavior
- mixed-language indexing tests that exercise real indexing passes
- packaging tests that catch missing package and optional-dependency errors

If we add end-to-end integration tests again, they should be built around a
small maintained fixture created specifically for those workflows rather than
an old benchmark checkout.
