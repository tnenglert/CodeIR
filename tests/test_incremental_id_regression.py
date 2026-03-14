from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from index.indexer import index_repo


class TestIncrementalIdRegression(unittest.TestCase):
    def _cfg(self) -> dict:
        return {
            "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".codeir"],
            "extensions": [".py"],
            "compression_level": "Behavior",
        }

    def test_colliding_entity_ids_do_not_overwrite_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
            (repo / "b.py").write_text("def foo():\n    return 2\n", encoding="utf-8")

            first = index_repo(repo, self._cfg())
            self.assertEqual(first.get("total_entities"), 2)

            # Change only b.py so incremental indexing touches one file.
            (repo / "b.py").write_text("def foo():\n    x = 1\n    return 2\n", encoding="utf-8")
            second = index_repo(repo, self._cfg())
            self.assertEqual(second.get("total_entities"), 2)

            db_path = repo / ".codeir" / "entities.db"
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT id, file_path, qualified_name FROM entities ORDER BY file_path"
            ).fetchall()
            conn.close()

            self.assertEqual(len(rows), 2)
            self.assertEqual({row[1] for row in rows}, {"a.py", "b.py"})
            self.assertEqual({row[2] for row in rows}, {"foo"})
            # IDs must remain unique after incremental update.
            self.assertEqual(len({row[0] for row in rows}), 2)


if __name__ == "__main__":
    unittest.main()
