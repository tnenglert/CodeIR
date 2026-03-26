"""Tests for grep CLI presentation modes."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_cmd_grep_evidence_sets_default_context_and_prints_ir(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_grep_entities(**kwargs):
        captured.update(kwargs)
        return [
            {
                "type": "entity",
                "entity_id": "FOO",
                "qualified_name": "pkg.foo",
                "kind": "function",
                "file_path": "foo.py",
                "start_line": 10,
                "end_line": 40,
                "ir_text": "FN FOO C=bar F=IR A=2 #CORE",
                "matches": [
                    {"line": 12, "text": "alpha()", "context_before": [], "context_after": []},
                    {"line": 18, "text": "beta()", "context_before": [], "context_after": []},
                    {"line": 24, "text": "gamma()", "context_before": [], "context_after": []},
                    {"line": 30, "text": "delta()", "context_before": [], "context_after": []},
                ],
            }
        ]

    monkeypatch.setattr(cli, "grep_entities", fake_grep_entities)

    args = Namespace(
        pattern="alpha|beta",
        repo_path=tmp_path,
        level="Behavior",
        limit=50,
        ignore_case=False,
        context=0,
        path="lib",
        verbose=False,
        evidence=True,
    )

    cli.cmd_grep(args)
    out = capsys.readouterr().out

    assert captured["context"] == 2
    assert "IR: FN FOO C=bar F=IR A=2 #CORE" in out
    assert "12: alpha()" in out
    assert "18: beta()" in out
    assert "24: gamma()" in out
    assert "30: delta()" not in out
    assert "... 1 more matches in this entity" in out


def test_cmd_grep_evidence_preserves_explicit_context(monkeypatch, tmp_path):
    captured = {}

    def fake_grep_entities(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli, "grep_entities", fake_grep_entities)

    args = Namespace(
        pattern="alpha",
        repo_path=tmp_path,
        level="Behavior",
        limit=50,
        ignore_case=False,
        context=1,
        path=None,
        verbose=False,
        evidence=True,
    )

    cli.cmd_grep(args)

    assert captured["context"] == 1
