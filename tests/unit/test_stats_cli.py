"""Tests for stats CLI presentation."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli


def test_cmd_stats_prints_classification_quality(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        cli,
        "get_stats",
        lambda repo_path: {
            "source_language": "mixed",
            "source_languages": ["python", "rust"],
            "entity_count": 12,
            "entities_by_kind": [{"kind": "function", "count": 8}],
            "file_coverage": {
                "files_with_entities": 4,
                "source_files_indexed": 4,
                "coverage_percent": 100.0,
            },
            "classification_quality": {
                "structural_files": 3,
                "fallback_files": 1,
                "structural_percent": 75.0,
                "fallback_percent": 25.0,
                "specific_domains": 2,
                "misc_domains": 1,
                "unknown_domains": 1,
                "specific_percent": 50.0,
                "misc_percent": 25.0,
                "unknown_percent": 25.0,
            },
            "compression_level": "Behavior",
            "compression": {
                "source_token_count": 100,
                "ir_token_count": 25,
                "global_ratio": 0.25,
                "avg_entity_ratio": 0.5,
            },
            "level_stats": {},
            "category_stats": [],
            "complexity_stats": {},
            "abbreviation_count": 7,
        },
    )

    cli.cmd_stats(Namespace(repo_path=tmp_path))
    out = capsys.readouterr().out

    assert "Classification quality:" in out
    assert "Category classifier: 3/4 structural (75.0%), 1 fallback (25.0%)" in out
    assert "Domain classifier:   2 specific (50.0%), 1 misc (25.0%), 1 unknown (25.0%)" in out
