"""Integration tests for CodeIR tool workflows.

Tests real tool sequences against indexed repositories to catch:
- Resolution failures (callers missing known dependencies)
- Ambiguity patterns (high-collision names)
- Tool output consistency

See tests/INTEGRATION_TESTS.md for detailed documentation of each test.

Run: python -m pytest tests/tool_integration_test.py -v
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

# Test fixture path
FIXTURE_PATH = Path("tests/_local/testRepositories/_fastapi-users-master")


def run_cli(cmd: List[str], repo_path: Path) -> Tuple[int, str, str]:
    """Run CLI command and return (code, stdout, stderr)."""
    full_cmd = [sys.executable, "cli.py"] + cmd + ["--repo-path", str(repo_path)]
    # Go up from tests/integration/ to project root
    project_root = Path(__file__).parent.parent.parent
    result = subprocess.run(full_cmd, capture_output=True, text=True, cwd=project_root)
    return result.returncode, result.stdout, result.stderr


def run_tool(tool_name: str, args: Dict, repo_path: Path) -> Dict:
    """Run tool via runtime and return structured response."""
    from tools.runtime import run_tool as _run_tool
    return _run_tool(tool_name, args, repo_path=str(repo_path))


@pytest.fixture(scope="module")
def indexed_repo():
    """Ensure fixture is indexed before tests."""
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture not found: {FIXTURE_PATH}")

    db_path = FIXTURE_PATH / ".codeir" / "entities.db"
    if not db_path.exists():
        # Index the fixture
        code, stdout, stderr = run_cli(["index", str(FIXTURE_PATH), "--level", "Behavior"], FIXTURE_PATH.parent)
        if code != 0:
            pytest.fail(f"Failed to index fixture: {stderr}")

    return FIXTURE_PATH


class TestCallersResolution:
    """Test that callers command resolves dependencies correctly."""

    def test_hash_callers_resolved(self, indexed_repo):
        """hash() calls via attribute chains should resolve (not hit stoplist)."""
        result = run_tool("codeir_callers", {"entity_id": "HASH.02"}, indexed_repo)

        assert result["data"] is not None, "Expected callers data"
        assert result["data"]["caller_count"] > 0, "HASH.02 should have callers (qualified calls bypass stoplist)"

        # Should include known callers
        callers_raw = result["data"].get("callers_raw", "")
        assert "ATHNTCT.02" in callers_raw or "authenticate" in callers_raw.lower(), \
            "authenticate should call hash"

    def test_local_callers_found(self, indexed_repo):
        """Same-file callers should always resolve."""
        result = run_tool("codeir_callers", {"entity_id": "UPDT.03"}, indexed_repo)

        assert result["data"]["caller_count"] >= 2, "UPDT.03 should have local callers"
        callers_raw = result["data"].get("callers_raw", "")
        assert "[local]" in callers_raw, "Should have local resolution"

    def test_ambiguous_calls_surfaced(self, indexed_repo):
        """High-collision names should surface ambiguity info."""
        result = run_tool("codeir_callers", {"entity_id": "UPDT.03"}, indexed_repo)

        # Should have ambiguous calls (update has 6 entities)
        if "ambiguous" in result["data"]:
            ambiguous = result["data"]["ambiguous"]
            assert len(ambiguous) > 0, "Should have ambiguous calls"
            assert ambiguous[0]["candidate_count"] > 4, "update should exceed fuzzy limit"

        # Should have warning
        warnings = result["meta"].get("warnings", [])
        assert any("ambiguous" in w.lower() or "possible targets" in w.lower() for w in warnings), \
            "Should warn about ambiguity"

    def test_suggestions_for_ambiguity(self, indexed_repo):
        """Ambiguous results should suggest grep workaround."""
        result = run_tool("codeir_callers", {"entity_id": "UPDT.03"}, indexed_repo)

        suggestions = result["meta"].get("suggestions", [])
        assert any("grep" in s.lower() for s in suggestions), \
            "Should suggest grep for ambiguous calls"


class TestSearchAndShow:
    """Test search and show workflows."""

    def test_search_finds_entity(self, indexed_repo):
        """Search should find entities by name."""
        code, stdout, stderr = run_cli(["search", "authenticate"], indexed_repo)

        assert code == 0
        assert "ATHNTCT" in stdout, "Should find authenticate entities"
        assert "BaseUserManager.authenticate" in stdout

    def test_show_returns_ir(self, indexed_repo):
        """Show should return behavioral IR."""
        code, stdout, stderr = run_cli(["show", "ATHNTCT.02"], indexed_repo)

        assert code == 0
        assert "C=" in stdout, "Should have calls field"
        assert "F=" in stdout, "Should have flags field"
        # Verify qualified calls are shown
        assert "password_helper" in stdout or "get_by_email" in stdout

    def test_scope_returns_context(self, indexed_repo):
        """Scope should return callers, callees, siblings."""
        code, stdout, stderr = run_cli(["scope", "VRFY"], indexed_repo)

        assert code == 0
        assert "callers" in stdout.lower()
        assert "callees" in stdout.lower()
        assert "siblings" in stdout.lower()


class TestGrepFallback:
    """Test that grep works as fallback for ambiguous resolution."""

    def test_grep_finds_update_calls(self, indexed_repo):
        """Grep should find calls that callers resolution missed."""
        code, stdout, stderr = run_cli(
            ["grep", r"\.update\(", "--path", "fastapi_users/router"],
            indexed_repo
        )

        assert code == 0
        # Should find router calls to update
        assert "update" in stdout.lower()

    def test_grep_finds_create_calls(self, indexed_repo):
        """Grep should find create calls in routers."""
        code, stdout, stderr = run_cli(
            ["grep", r"\.create\(", "--path", "fastapi_users/router"],
            indexed_repo
        )

        assert code == 0
        assert "create" in stdout.lower()


class TestImpactAnalysis:
    """Test impact analysis for dependency-sensitive changes."""

    def test_impact_traverses_callers(self, indexed_repo):
        """Impact should traverse caller graph."""
        code, stdout, stderr = run_cli(["impact", "GNRTJWT", "--depth", "2"], indexed_repo)

        assert code == 0
        assert "Affected" in stdout or "affected" in stdout.lower()
        # Should find JWTStrategy as direct dependent
        assert "JWT" in stdout.upper()

    def test_impact_shows_depth(self, indexed_repo):
        """Impact should show entities at different depths."""
        code, stdout, stderr = run_cli(["impact", "HASH.02", "--depth", "2"], indexed_repo)

        assert code == 0
        # Should have depth indicators
        assert "direct" in stdout.lower() or "depth" in stdout.lower()


class TestWorkflowIntegration:
    """Test complete workflows end-to-end."""

    def test_bug_investigation_workflow(self, indexed_repo):
        """Simulate: 'find where password hashing happens'"""
        # Step 1: Search
        code, stdout, _ = run_cli(["search", "hash", "password"], indexed_repo)
        assert code == 0
        assert "HASH" in stdout

        # Step 2: Show behavior
        code, stdout, _ = run_cli(["show", "HASH.02"], indexed_repo)
        assert code == 0
        assert "C=" in stdout

        # Step 3: Check callers
        result = run_tool("codeir_callers", {"entity_id": "HASH.02"}, indexed_repo)
        assert result["data"]["caller_count"] > 0

        # Workflow complete - found the entity and its callers

    def test_refactor_workflow(self, indexed_repo):
        """Simulate: 'understand verification flow before modifying'"""
        # Step 1: Search
        code, stdout, _ = run_cli(["search", "verify"], indexed_repo)
        assert code == 0
        assert "VRFY" in stdout

        # Step 2: Scope for context
        code, stdout, _ = run_cli(["scope", "VRFY"], indexed_repo)
        assert code == 0
        assert "callers" in stdout.lower()
        assert "callees" in stdout.lower()

        # Step 3: Impact before changing
        code, stdout, _ = run_cli(["impact", "VRFY", "--depth", "2"], indexed_repo)
        assert code == 0


class TestAmbiguityPatterns:
    """Test specific ambiguity patterns we've identified."""

    @pytest.mark.parametrize("entity_name,expected_count", [
        ("update", 6),
        ("create", 6),
        ("delete", 7),
        ("get", 5),
        ("hash", 2),  # Should be low, resolved via qualified calls
    ])
    def test_entity_collision_counts(self, indexed_repo, entity_name, expected_count):
        """Verify entity collision counts match expectations."""
        import sqlite3
        db_path = indexed_repo / ".codeir" / "entities.db"
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE name = ?", [entity_name]
        ).fetchone()[0]
        conn.close()

        assert count == expected_count, f"Expected {expected_count} entities named '{entity_name}', got {count}"

    def test_qualified_calls_bypass_stoplist(self, indexed_repo):
        """Verify qualified calls like 'password_helper.hash' resolve."""
        import sqlite3
        db_path = indexed_repo / ".codeir" / "entities.db"
        conn = sqlite3.connect(db_path)

        # Check that ATHNTCT.02 has qualified hash call
        row = conn.execute(
            "SELECT calls_json FROM entities WHERE id = 'ATHNTCT.02'"
        ).fetchone()
        conn.close()

        assert row is not None
        calls = json.loads(row[0])
        assert any("hash" in c and "." in c for c in calls), \
            "Should have qualified hash call (e.g., 'password_helper.hash')"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
