from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from index.locator import parse_entities_from_file
from ir.abbreviations import build_abbreviation_maps
from ir.compressor import build_ir_rows


class TestL1ExceptionSignals(unittest.TestCase):
    def test_exception_class_gets_behavioral_signals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            src = repo / "sample.py"
            src.write_text(
                "class RequestException(Exception):\n"
                "    pass\n\n"
                "class Timeout(RequestException):\n"
                "    \"\"\"The request timed out.\"\"\"\n"
                "    pass\n",
                encoding="utf-8",
            )

            entities = parse_entities_from_file(src)
            self.assertTrue(entities)

            for idx, entity in enumerate(entities, start=1):
                entity["id"] = f"ENT_{idx:03d}"
                entity["file_path"] = "sample.py"

            timeout_entity = next(e for e in entities if str(e.get("name")) == "Timeout")

            call_symbols = [
                call
                for entity in entities
                for call in list((entity.get("semantic") or {}).get("calls", []))
                if isinstance(call, str)
            ]
            abbrev_maps = build_abbreviation_maps(
                entity_names=[str(e["qualified_name"]) for e in entities],
                file_paths=["sample.py"],
                call_symbols=call_symbols,
                compact_mode=False,
            )

            rows = build_ir_rows(
                entities=entities,
                abbreviations=abbrev_maps,
                compression_level="Behavior",
                repo_path=repo,
                module_categories={"sample.py": "exceptions"},
                module_domains={"sample.py": "http"},
                passthrough_threshold=0,
            )

            timeout_row = next(r for r in rows if r["entity_id"] == timeout_entity["id"])
            token = str(timeout_row["ir_text"])

            # N= removed - entity ID already carries semantic abbreviation
            self.assertIn("C=", token)
            self.assertIn("F=", token)
            # A= is omitted when assign count is 0 (empty fields are now omitted)
            self.assertNotIn("A=0", token)
            self.assertIn("B=", token)
            self.assertNotIn("C=-", token)
            self.assertNotIn("B=-", token)
            self.assertRegex(token, r"F=[A-Z-]*X")
            self.assertIn("#HTTP", token)
            self.assertIn("#EXCE", token)


if __name__ == "__main__":
    unittest.main()

