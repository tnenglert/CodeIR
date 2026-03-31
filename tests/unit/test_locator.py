"""Tests for entity extraction via the locator module."""

import sys
import tempfile
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from index.locator import parse_entities_from_file


class TestParseEntities:
    """Tests for entity extraction from Python files."""

    def test_flags_sorted_alphabetically(self):
        """Extracted flags are sorted alphabetically."""
        code = '''
def example():
    try:
        if condition:
            for x in items:
                return x
    except:
        raise
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            f.flush()
            entities = parse_entities_from_file(Path(f.name))

        func = next(e for e in entities if e["name"] == "example")
        # Flags should be sorted: E, I, L, R, T
        assert func["semantic"]["flags"] == "EILRT"

    def test_calls_extracted(self):
        """Function calls are extracted with attribute chains."""
        code = '''
def example():
    foo()
    bar.baz()
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            f.flush()
            entities = parse_entities_from_file(Path(f.name))

        func = next(e for e in entities if e["name"] == "example")
        assert "foo" in func["semantic"]["calls"]
        assert "bar.baz" in func["semantic"]["calls"]  # Qualified call

    def test_assignment_count(self):
        """Assignments are counted."""
        code = '''
def example():
    x = 1
    y = 2
    z = 3
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            f.flush()
            entities = parse_entities_from_file(Path(f.name))

        func = next(e for e in entities if e["name"] == "example")
        assert func["semantic"]["assigns"] == 3
