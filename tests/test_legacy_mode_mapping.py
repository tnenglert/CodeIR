from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from index.indexer import index_repo, map_legacy_mode_to_level


class TestLegacyModeMapping(unittest.TestCase):
    def _cfg(self, mode: str) -> dict:
        return {
            "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".semanticir"],
            "extensions": [".py"],
            "compression_mode": mode,
        }

    def test_mode_aliases_map_to_expected_levels(self) -> None:
        self.assertEqual(map_legacy_mode_to_level("a"), "L3")
        self.assertEqual(map_legacy_mode_to_level("b"), "L1")
        self.assertEqual(map_legacy_mode_to_level("hybrid"), "L2")

    def test_index_repo_honors_legacy_mode_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "mod.py").write_text("def alpha(x):\n    return x\n", encoding="utf-8")

            for mode in ("a", "b", "hybrid"):
                expected_level = map_legacy_mode_to_level(mode)
                result = index_repo(repo, self._cfg(mode))
                self.assertEqual(result.get("compression_level"), expected_level)

                conn = sqlite3.connect(repo / ".semanticir" / "entities.db")
                modes = {row[0] for row in conn.execute("SELECT DISTINCT mode FROM ir_rows").fetchall()}
                conn.close()
                self.assertEqual(modes, {expected_level})


if __name__ == "__main__":
    unittest.main()
