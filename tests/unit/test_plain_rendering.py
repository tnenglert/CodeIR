"""Tests for plain (real-name) rendering and raw ir_json storage."""

import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli
from ir.compressor import build_ir_rows, render_plain_row


def _row(level="Behavior", **ir_json_extra):
    ir_json = {"op": "MT", "id": "FNLZ.02", "level": level, "sp": [10, 20]}
    ir_json.update(ir_json_extra)
    return {
        "entity_id": "FNLZ.02",
        "qualified_name": "orm.Session.finalize",
        "kind": "method",
        "ir_text": "MT FNLZ.02 C=RGSTR,items F=IR A=3 #DB #CORE",
        "ir_json": ir_json,
    }


class TestRenderPlainRow:
    def test_behavior_row_uses_real_names(self):
        row = _row(
            calls=["register_persistent", "items"],
            flags="IR",
            assigns=3,
            bases=[],
            category="core_logic",
            domain="db",
        )
        out = render_plain_row(row)
        assert out == (
            "MT FNLZ.02 orm.Session.finalize "
            "C=register_persistent,items F=IR A=3 #DB #CORE"
        )

    def test_empty_fields_omitted(self):
        row = _row(calls=[], flags="", assigns=0, bases=[], category="core_logic")
        out = render_plain_row(row)
        assert out == "MT FNLZ.02 orm.Session.finalize #CORE"
        assert "C=" not in out
        assert "F=" not in out
        assert "A=" not in out

    def test_calls_truncated_to_six_with_count(self):
        row = _row(calls=[f"call_{i}" for i in range(9)], flags="", assigns=0, bases=[])
        out = render_plain_row(row)
        assert "C=call_0,call_1,call_2,call_3,call_4,call_5+3" in out
        assert "call_6" not in out

    def test_bases_truncated_to_three(self):
        row = _row(calls=[], flags="", assigns=0, bases=["A", "B", "C", "D"])
        out = render_plain_row(row)
        assert "B=A,B,C" in out
        assert "D" not in out.split("B=")[1]

    def test_index_row_renders_name_and_tags(self):
        row = _row(level="Index", pattern_id="P123ABC", category="router", domain="http")
        out = render_plain_row(row)
        assert out == "MT FNLZ.02 orm.Session.finalize #HTTP #ROUT"

    def test_source_row_passes_through(self):
        row = _row(level="Source")
        row["ir_text"] = "[MT FNLZ.02 @orm/session.py:10]\ndef finalize(self): ..."
        assert render_plain_row(row) == row["ir_text"]

    def test_missing_ir_json_falls_back_to_ir_text(self):
        row = _row()
        row["ir_json"] = {}
        assert render_plain_row(row) == row["ir_text"]

    def test_misc_and_unknown_domains_suppressed(self):
        for domain in ("misc", "unknown"):
            row = _row(calls=[], flags="", assigns=0, bases=[], domain=domain)
            assert "#MISC" not in render_plain_row(row)
            assert "#UNKNOWN" not in render_plain_row(row)


class TestRawIrJsonStorage:
    def _entity(self):
        return {
            "id": "FNLZ.02",
            "kind": "method",
            "name": "finalize",
            "qualified_name": "orm.Session.finalize",
            "file_path": "orm/session.py",
            "start_line": 10,
            "end_line": 20,
            "semantic": {
                "calls": [f"call_{i}" for i in range(8)] + ["register_persistent"],
                "flags": "IR",
                "assigns": 3,
                "bases": ["ModelSQL"],
            },
        }

    def _abbreviations(self):
        return {
            "entity_name": {},
            "file_path": {},
            "call_name": {"register_persistent": "CRGSTR", "ModelSQL": "CMDLSQL"},
        }

    def test_ir_json_keeps_raw_untruncated_calls_and_bases(self):
        rows = build_ir_rows(
            entities=[self._entity()],
            abbreviations=self._abbreviations(),
            compression_level="Behavior",
            repo_path=None,
            passthrough_threshold=0,
        )
        assert len(rows) == 1
        ir_json = json.loads(rows[0]["ir_json"])
        assert len(ir_json["calls"]) == 9  # untruncated
        assert "register_persistent" in ir_json["calls"]  # unabbreviated
        assert ir_json["bases"] == ["ModelSQL"]

    def test_ir_text_still_abbreviated_and_truncated(self):
        rows = build_ir_rows(
            entities=[self._entity()],
            abbreviations=self._abbreviations(),
            compression_level="Behavior",
            repo_path=None,
            passthrough_threshold=0,
        )
        ir_text = rows[0]["ir_text"]
        assert "+3" in ir_text  # 9 calls -> 6 shown +3
        assert "B=CMDLSQL" in ir_text
        assert "register_persistent" not in ir_text

    def test_index_ir_json_includes_domain(self):
        rows = build_ir_rows(
            entities=[self._entity()],
            abbreviations=self._abbreviations(),
            compression_level="Index",
            repo_path=None,
            module_categories={"orm/session.py": "core_logic"},
            module_domains={"orm/session.py": "db"},
            passthrough_threshold=0,
        )
        ir_json = json.loads(rows[0]["ir_json"])
        assert ir_json["domain"] == "db"
        assert ir_json["category"] == "core_logic"


class TestShowPlainCli:
    def test_cmd_show_plain_renders_real_names(self, monkeypatch, capsys, indexed_repo):
        monkeypatch.setattr(
            cli,
            "get_entity_with_ir",
            lambda repo_path, entity_id, mode: {
                "entity_id": "FNLZ.02",
                "qualified_name": "orm.Session.finalize",
                "file_path": "orm/session.py",
                "start_line": 10,
                "end_line": 20,
                "line": 10,
                "kind": "method",
                "ir_text": "MT FNLZ.02 C=CRGSTR F=IR #DB #CORE",
                "ir_json": {
                    "op": "MT",
                    "id": "FNLZ.02",
                    "level": "Behavior",
                    "sp": [10, 20],
                    "calls": ["register_persistent"],
                    "flags": "IR",
                    "assigns": 0,
                    "bases": [],
                    "category": "core_logic",
                    "domain": "db",
                },
            },
        )

        args = Namespace(
            entity_ids=["FNLZ.02"],
            repo_path=indexed_repo,
            level="Behavior",
            full=False,
            plain=True,
        )
        cli.cmd_show(args)
        out = capsys.readouterr().out

        assert "MT FNLZ.02 orm.Session.finalize C=register_persistent F=IR #DB #CORE" in out
        assert "C=CRGSTR" not in out

    def test_cmd_show_without_plain_keeps_dense_text(self, monkeypatch, capsys, indexed_repo):
        monkeypatch.setattr(
            cli,
            "get_entity_with_ir",
            lambda repo_path, entity_id, mode: {
                "entity_id": "FNLZ.02",
                "qualified_name": "orm.Session.finalize",
                "file_path": "orm/session.py",
                "start_line": 10,
                "end_line": 20,
                "line": 10,
                "kind": "method",
                "ir_text": "MT FNLZ.02 C=CRGSTR F=IR #DB #CORE",
                "ir_json": {},
            },
        )

        args = Namespace(
            entity_ids=["FNLZ.02"],
            repo_path=indexed_repo,
            level="Behavior",
            full=True,
            plain=False,
        )
        cli.cmd_show(args)
        out = capsys.readouterr().out

        assert "MT FNLZ.02 C=CRGSTR F=IR #DB #CORE" in out
