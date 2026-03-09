from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli.py"
FASTAPI_FIXTURE = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"


class TestFastapiCliIntegration(unittest.TestCase):
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

    def test_fastapi_index_search_show_expand_stats(self) -> None:
        index = self.run_cli("index", str(FASTAPI_FIXTURE))
        self.assertEqual(index.returncode, 0, msg=index.stderr or index.stdout)
        # New output format: "Indexed X changed files" or "No changes detected"
        self.assertTrue(
            "Indexed" in index.stdout or "No changes detected" in index.stdout,
            msg=f"Unexpected index output: {index.stdout}",
        )

        search = self.run_cli("search", "fastapi_users", "--repo-path", str(FASTAPI_FIXTURE), "--limit", "5")
        self.assertEqual(search.returncode, 0, msg=search.stderr or search.stdout)
        lines = search.stdout.strip().splitlines()
        self.assertTrue(lines, msg="search returned no output")
        first_line = lines[0]

        # Match entity ID format: STEM or STEM.XX (collision suffix with dots)
        match = re.match(r"^\s*([A-Z0-9]+(?:\.[0-9]{2})?)\s{2}", first_line)
        self.assertIsNotNone(match, msg=f"Unable to parse entity id from line: {first_line}")
        entity_id = match.group(1)

        show = self.run_cli("show", entity_id, "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(show.returncode, 0, msg=show.stderr or show.stdout)
        self.assertIn(entity_id, show.stdout)

        expand = self.run_cli("expand", entity_id, "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(expand.returncode, 0, msg=expand.stderr or expand.stdout)
        self.assertTrue(expand.stdout.strip(), msg="expand returned empty output")

        stats = self.run_cli("stats", "--repo-path", str(FASTAPI_FIXTURE))
        self.assertEqual(stats.returncode, 0, msg=stats.stderr or stats.stdout)
        # New output format uses plain labels
        self.assertIn("Entities:", stats.stdout)
        self.assertIn("File coverage:", stats.stdout)
        self.assertIn("Compression level:", stats.stdout)
        self.assertIn("Abbreviations:", stats.stdout)


if __name__ == "__main__":
    unittest.main()
