"""Tests for search CLI presentation and empty states."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_cmd_search_suggests_removing_category_when_unfiltered_matches_exist(monkeypatch, capsys, tmp_path):
    calls = []

    def fake_search_entities(**kwargs):
        calls.append(kwargs)
        if kwargs.get("category") == "core_logic":
            return []
        return [
            {
                "entity_id": "RPT",
                "qualified_name": "reporting.ReportAgent",
                "file_path": "report_agent.py",
                "line": 10,
                "kind": "class",
                "line_count": 120,
            }
        ]

    monkeypatch.setattr(cli, "search_entities", fake_search_entities)

    args = Namespace(
        query=["ReportAgent"],
        repo_path=tmp_path,
        limit=50,
        category="core_logic",
        patterns=False,
    )

    cli.cmd_search(args)
    out = capsys.readouterr().out

    assert len(calls) == 2
    assert calls[0]["category"] == "core_logic"
    assert calls[1]["category"] is None
    assert "No entities found in category 'core_logic'." in out
    assert "1 match found without the category filter." in out
    assert "Try removing --category" in out


def test_cmd_search_falls_back_to_grep_message_when_no_matches_anywhere(monkeypatch, capsys, tmp_path):
    calls = []

    def fake_search_entities(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(cli, "search_entities", fake_search_entities)

    args = Namespace(
        query=["ReportAgent"],
        repo_path=tmp_path,
        limit=50,
        category="core_logic",
        patterns=False,
    )

    cli.cmd_search(args)
    out = capsys.readouterr().out

    assert len(calls) == 2
    assert calls[0]["category"] == "core_logic"
    assert calls[1]["category"] is None
    assert "No entities found. Try: codeir grep \"ReportAgent\" to search file contents." in out


def test_cmd_search_without_category_keeps_original_empty_state(monkeypatch, capsys, tmp_path):
    calls = []

    def fake_search_entities(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(cli, "search_entities", fake_search_entities)

    args = Namespace(
        query=["ReportAgent"],
        repo_path=tmp_path,
        limit=50,
        category=None,
        patterns=False,
    )

    cli.cmd_search(args)
    out = capsys.readouterr().out

    assert len(calls) == 1
    assert calls[0]["category"] is None
    assert "No entities found. Try: codeir grep \"ReportAgent\" to search file contents." in out
