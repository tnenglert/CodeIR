from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from index.indexer import index_repo
from tools.api_schemas import expand_entity_code, get_entity_ir, search_entities


class TestApiSchemas(unittest.TestCase):
    def _cfg(self) -> dict:
        return {
            "hidden_dirs": [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".semanticir"],
            "extensions": [".py"],
            "compression_level": "L1",
        }

    def test_tool_wrappers_use_real_index_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "svc.py").write_text(
                "def alpha_token(user_id):\n"
                "    token = str(user_id)\n"
                "    return token\n",
                encoding="utf-8",
            )
            index_repo(repo, self._cfg())

            search = search_entities("alpha", repo_path=repo)
            self.assertTrue(search["ok"])
            self.assertGreater(search["count"], 0)
            entity_id = search["results"][0]["entity_id"]

            ir = get_entity_ir(entity_id, repo_path=repo, level="L1")
            self.assertTrue(ir["ok"])
            self.assertEqual(ir["entity"]["entity_id"], entity_id)
            self.assertIn("ir_text", ir["entity"])

            expanded = expand_entity_code(entity_id, repo_path=repo)
            self.assertTrue(expanded["ok"])
            self.assertEqual(expanded["entity_id"], entity_id)
            self.assertIn("def alpha_token", expanded["source"])

    def test_tool_wrappers_return_structured_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            res = search_entities("anything", repo_path=repo)
            self.assertFalse(res["ok"])
            self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
