from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli.py"
FASTAPI_FIXTURE = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"


class TestEvalLevelsIntegration(unittest.TestCase):
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

    def test_index_level_behavior_and_search(self) -> None:
        index = self.run_cli("index", str(FASTAPI_FIXTURE), "--level", "Behavior")
        self.assertEqual(index.returncode, 0, msg=index.stderr or index.stdout)
        self.assertTrue(
            "Indexed" in index.stdout or "No changes detected" in index.stdout,
            msg=f"Unexpected index output: {index.stdout}",
        )

        search = self.run_cli("search", "auth", "--repo-path", str(FASTAPI_FIXTURE), "--limit", "1")
        self.assertEqual(search.returncode, 0, msg=search.stderr or search.stdout)
        lines = search.stdout.strip().splitlines()
        self.assertTrue(lines, msg="search returned no output")
        first = lines[0]
        entity_id = first.split()[0]

        show = self.run_cli("show", entity_id, "--repo-path", str(FASTAPI_FIXTURE), "--level", "Behavior")
        self.assertEqual(show.returncode, 0, msg=show.stderr or show.stdout)
        self.assertIn(entity_id, show.stdout)
        # Behavior includes C= (calls), F= (flags), etc. or Source raw source markers
        self.assertTrue(
            "C=" in show.stdout or "F=" in show.stdout or "[" in show.stdout,
            msg=f"Expected Behavior format in show output: {show.stdout}",
        )

    def test_eval_levels_scoreboard(self) -> None:
        ev = self.run_cli("eval", str(FASTAPI_FIXTURE), "--levels", "Behavior", "Index")
        self.assertEqual(ev.returncode, 0, msg=ev.stderr or ev.stdout)
        self.assertIn("Evaluating compression levels:", ev.stdout)
        self.assertIn("Summary", ev.stdout)
        # Check that both levels appear in the summary
        for level in ("Behavior", "Index"):
            self.assertIn(level, ev.stdout)

    def test_module_map(self) -> None:
        # Ensure indexed first
        self.run_cli("index", str(FASTAPI_FIXTURE))

        modmap = self.run_cli("module-map", "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(modmap.returncode, 0, msg=modmap.stderr or modmap.stdout)
        self.assertIn("Module Map:", modmap.stdout)
        # Should have at least one category with files
        self.assertRegex(modmap.stdout, r"##\s+\w+\s+\(\d+\s+files")


if __name__ == "__main__":
    unittest.main()
