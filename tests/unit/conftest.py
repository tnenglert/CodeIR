"""Shared fixtures for CLI handler tests."""

import pytest


@pytest.fixture
def indexed_repo(tmp_path):
    """A repo directory with a stub .codeir store.

    Handler tests that mock the data layer still need to pass the
    require_index() guard; the stub satisfies the existence check
    without containing real data.
    """
    store = tmp_path / ".codeir"
    store.mkdir()
    (store / "entities.db").touch()
    return tmp_path
