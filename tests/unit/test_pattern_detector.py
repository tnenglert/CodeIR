"""Unit tests for pattern detection."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from index.pattern_detector import (
    Pattern, PatternMember, PatternDetails, detect_patterns, get_patterns,
    get_entity_pattern, get_entity_pattern_details, _ensure_pattern_tables,
)


@pytest.fixture
def test_db():
    """Create a test database with sample entities."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "entities.db"
        conn = sqlite3.connect(db_path)

        # Create entities table
        conn.execute("""
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                calls_json TEXT DEFAULT '[]'
            )
        """)

        # Create ir_rows table
        conn.execute("""
            CREATE TABLE ir_rows (
                entity_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                ir_text TEXT,
                ir_json TEXT,
                PRIMARY KEY (entity_id, mode)
            )
        """)

        conn.commit()
        conn.close()

        yield db_path


class TestPatternTables:
    """Test pattern table creation."""

    def test_ensure_pattern_tables_creates_tables(self, test_db):
        conn = sqlite3.connect(test_db)
        _ensure_pattern_tables(conn)

        # Verify tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "patterns" in table_names
        assert "pattern_members" in table_names
        conn.close()

    def test_ensure_pattern_tables_idempotent(self, test_db):
        conn = sqlite3.connect(test_db)
        _ensure_pattern_tables(conn)
        _ensure_pattern_tables(conn)  # Should not raise
        conn.close()


class TestPatternDetection:
    """Test pattern detection logic."""

    def test_no_patterns_with_empty_db(self, test_db):
        patterns = detect_patterns(test_db, min_size=5)
        assert patterns == []

    def test_get_patterns_handles_missing_table(self, test_db):
        patterns = get_patterns(test_db)
        assert patterns == []

    def test_get_entity_pattern_handles_missing_table(self, test_db):
        result = get_entity_pattern(test_db, "SOME_ID")
        assert result is None

    def test_get_entity_pattern_details_handles_missing_table(self, test_db):
        result = get_entity_pattern_details(test_db, "SOME_ID")
        assert result is None


class TestPatternDataclass:
    """Test Pattern dataclass methods."""

    def test_to_bearings_line_with_calls_and_flags(self):
        pattern = Pattern(
            pattern_id="ModelSQL_core_logic",
            entity_type="class",
            base_class="ModelSQL",
            category="core_logic",
            member_count=42,
            common_calls=["ModelSQL", "Pool", "Transaction"],
            common_flags="IR",
            is_test_pattern=False,
        )

        line = pattern.to_bearings_line()
        assert "ModelSQL" in line
        assert "42 classes" in line
        assert "Pool" in line
        assert "IR" in line

    def test_to_bearings_line_without_flags(self):
        pattern = Pattern(
            pattern_id="Base_core_logic",
            entity_type="class",
            base_class="Base",
            category="core_logic",
            member_count=10,
            common_calls=[],
            common_flags="",
            is_test_pattern=False,
        )

        line = pattern.to_bearings_line()
        assert "Base" in line
        assert "Calls: -" in line
        assert "Flags: -" in line


class TestPatternDetailsDataclass:
    """Test PatternDetails dataclass."""

    def test_pattern_details_with_deviations(self):
        details = PatternDetails(
            base_class="ModelSQL",
            member_count=42,
            category="core_logic",
            common_calls=["Pool", "get"],
            common_flags="IR",
            extra_calls=["Transaction", "cursor"],
            extra_flags="EW",
            missing_calls=[],
        )
        assert details.base_class == "ModelSQL"
        assert details.member_count == 42
        assert "Transaction" in details.extra_calls

    def test_pattern_details_no_deviations(self):
        details = PatternDetails(
            base_class="object",
            member_count=10,
            category="core_logic",
            common_calls=["object"],
            common_flags="",
            extra_calls=[],
            extra_flags="",
            missing_calls=[],
        )
        assert details.extra_calls == []
        assert details.extra_flags == ""
        assert details.missing_calls == []
