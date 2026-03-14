from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
import unittest

from index.locator import parse_entities_from_file
from ir.abbreviations import build_abbreviation_maps
from ir.compressor import build_ir_rows


class TestIRContractSync(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.contract_path = self.repo_root / "docs" / "IR_contract_v0_2.json"
        self.contract = json.loads(self.contract_path.read_text(encoding="utf-8"))

    def test_contract_has_required_sections(self) -> None:
        self.assertEqual(self.contract.get("contract_version"), "0.2.0-as-built")
        self.assertIn("entity_prefixes", self.contract)
        self.assertIn("levels", self.contract)
        self.assertIn("Behavior", self.contract["levels"])
        self.assertIn("Index", self.contract["levels"])

    def test_canonical_preamble_files_exist(self) -> None:
        l1_rel = self.contract["canonical_preambles"]["Behavior"]
        l3_rel = self.contract["canonical_preambles"]["Index"]
        l1_path = self.repo_root / l1_rel
        l3_path = self.repo_root / l3_rel
        self.assertTrue(l1_path.exists())
        self.assertTrue(l3_path.exists())

        l1_text = l1_path.read_text(encoding="utf-8")
        l3_text = l3_path.read_text(encoding="utf-8")
        self.assertIn("AMT=async method", l1_text)
        self.assertIn("AFN=async function", l1_text)
        self.assertIn("TYPE ENTITY_ID [#DOMAIN] #CATEGORY", l3_text)

    def test_emitted_behavior_matches_contract_fields_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            src = repo / "sample.py"
            src.write_text(
                "class RequestException(Exception):\n"
                "    pass\n\n"
                "class Timeout(RequestException):\n"
                "    pass\n\n"
                "async def fetch(url):\n"
                "    try:\n"
                "        with open(url) as f:\n"
                "            data = f.read()\n"
                "        if data:\n"
                "            return data\n"
                "    except Exception:\n"
                "        raise\n",
                encoding="utf-8",
            )

            entities = parse_entities_from_file(src)
            self.assertTrue(entities)
            for idx, entity in enumerate(entities, start=1):
                entity["id"] = f"ENT_{idx:03d}"
                entity["file_path"] = "sample.py"

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

            rows_l1 = build_ir_rows(
                entities=entities,
                abbreviations=abbrev_maps,
                compression_level="Behavior",
                repo_path=repo,
                module_categories={"sample.py": "exceptions"},
                module_domains={"sample.py": "http"},
                passthrough_threshold=0,
            )

            allowed_prefixes = set(self.contract["entity_prefixes"].keys())
            allowed_flags = set(self.contract["levels"]["Behavior"]["flags"].keys())
            for row in rows_l1:
                token = str(row["ir_text"])
                pieces = token.split(" ")
                self.assertGreaterEqual(len(pieces), 2)
                self.assertIn(pieces[0], allowed_prefixes)
                # N= removed - entity ID already carries semantic abbreviation
                # C, F, A, B are optional (omitted when empty/zero)
                # If F= is present, validate its flags
                flag_match = re.search(r"\bF=([A-Z]+)", token)
                if flag_match:
                    flags = flag_match.group(1)
                    for f in flags:
                        self.assertIn(f, allowed_flags)

    def test_emitted_index_matches_contract_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            src = repo / "sample.py"
            src.write_text(
                "def send(data):\n"
                "    if data:\n"
                "        return data\n",
                encoding="utf-8",
            )
            entities = parse_entities_from_file(src)
            self.assertTrue(entities)
            for idx, entity in enumerate(entities, start=1):
                entity["id"] = f"ENT_{idx:03d}"
                entity["file_path"] = "sample.py"

            abbrev_maps = build_abbreviation_maps(
                entity_names=[str(e["qualified_name"]) for e in entities],
                file_paths=["sample.py"],
                call_symbols=[],
                compact_mode=False,
            )

            rows_l3 = build_ir_rows(
                entities=entities,
                abbreviations=abbrev_maps,
                compression_level="Index",
                repo_path=repo,
                module_categories={"sample.py": "core_logic"},
                module_domains={"sample.py": "http"},
                passthrough_threshold=0,
            )

            for row in rows_l3:
                token = str(row["ir_text"])
                self.assertRegex(token, r"^[A-Z]+\s+\S+\s+#HTTP\s+#CORE$")


if __name__ == "__main__":
    unittest.main()
