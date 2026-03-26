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


def test_cmd_grep_count_outputs_counts_only_sorted(monkeypatch, capsys, tmp_path):
    def fake_grep_entities(**kwargs):
        return [
            {
                "type": "entity",
                "entity_id": "BBB",
                "qualified_name": "pkg.beta",
                "kind": "function",
                "file_path": "beta.py",
                "start_line": 20,
                "end_line": 40,
                "matches": [{"line": 21, "text": "x"}],
            },
            {
                "type": "entity",
                "entity_id": "AAA",
                "qualified_name": "pkg.alpha",
                "kind": "function",
                "file_path": "alpha.py",
                "start_line": 10,
                "end_line": 18,
                "matches": [{"line": 12, "text": "x"}, {"line": 14, "text": "y"}, {"line": 16, "text": "z"}],
            },
        ]

    monkeypatch.setattr(cli, "grep_entities", fake_grep_entities)

    args = Namespace(
        pattern="alpha",
        repo_path=tmp_path,
        level="Behavior",
        limit=50,
        ignore_case=False,
        context=0,
        path=["lib", "test"],
        verbose=False,
        evidence=False,
        count=True,
    )

    cli.cmd_grep(args)
    out = capsys.readouterr().out

    assert "4 matches across 2 entities and 0 unmatched regions" in out
    assert "    3  AAA" in out
    assert "    1  BBB" in out
    assert out.index("AAA") < out.index("BBB")
    assert "12:" not in out


def test_cmd_grep_passes_multiple_paths(monkeypatch, tmp_path):
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
        context=0,
        path=["lib", "test", "docs/*.rst"],
        verbose=False,
        evidence=False,
        count=False,
    )

    cli.cmd_grep(args)

    assert captured["path_filter"] == ["lib", "test", "docs/*.rst"]
