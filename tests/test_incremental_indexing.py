from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli.py"
FASTAPI_FIXTURE = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"


class TestIncrementalIndexing(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not FASTAPI_FIXTURE.exists():
            raise unittest.SkipTest(f"Fixture not found: {FASTAPI_FIXTURE}")

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_second_index_detects_no_changes(self) -> None:
        """Indexing twice should detect no changes on the second run."""
        first = self.run_cli("index", str(FASTAPI_FIXTURE))
        self.assertEqual(first.returncode, 0, msg=first.stderr or first.stdout)

        second = self.run_cli("index", str(FASTAPI_FIXTURE))
        self.assertEqual(second.returncode, 0, msg=second.stderr or second.stdout)
        self.assertIn("No changes detected", second.stdout)

    def test_module_map_shows_categories(self) -> None:
        """Module map should display categories after indexing."""
        self.run_cli("index", str(FASTAPI_FIXTURE))

        modmap = self.run_cli("module-map", "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(modmap.returncode, 0, msg=modmap.stderr or modmap.stdout)
        self.assertIn("Module Map:", modmap.stdout)
        # At least one category should appear
        self.assertRegex(modmap.stdout, r"##\s+\w+")

    def test_compare_after_level_all(self) -> None:
        """Compare command should work after indexing with --level all."""
        index = self.run_cli("index", str(FASTAPI_FIXTURE), "--level", "all")
        self.assertEqual(index.returncode, 0, msg=index.stderr or index.stdout)

        # Find an entity to compare
        search = self.run_cli("search", "auth", "--repo-path", str(FASTAPI_FIXTURE), "--limit", "1")
        self.assertEqual(search.returncode, 0, msg=search.stderr or search.stdout)
        lines = search.stdout.strip().splitlines()
        self.assertTrue(lines, msg="search returned no results")
        entity_id = lines[0].split()[0]

        compare = self.run_cli("compare", entity_id, "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(compare.returncode, 0, msg=compare.stderr or compare.stdout)
        self.assertIn(entity_id, compare.stdout)
        # Should show at least L0 and L1 sections
        self.assertTrue(
            "L0" in compare.stdout or "L1" in compare.stdout,
            msg=f"Expected level headers in compare output: {compare.stdout}",
        )

    def test_stats_shows_per_level_data(self) -> None:
        """Stats should show per-level breakdown after indexing with --level all."""
        self.run_cli("index", str(FASTAPI_FIXTURE), "--level", "all")

        stats = self.run_cli("stats", "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(stats.returncode, 0, msg=stats.stderr or stats.stdout)
        self.assertIn("Per-level breakdown:", stats.stdout)
        self.assertIn("Module categories:", stats.stdout)
        self.assertIn("Complexity distribution:", stats.stdout)


if __name__ == "__main__":
    unittest.main()
