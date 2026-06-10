"""Read commands in an unindexed repo must exit 1 with guidance, no traceback,
and must never create an empty .codeir store as a side effect."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli

READ_COMMANDS = [
    ["search", "foo"],
    ["show", "ENTITY"],
    ["expand", "ENTITY"],
    ["compare", "ENTITY"],
    ["callers", "ENTITY"],
    ["impact", "ENTITY"],
    ["scope", "ENTITY"],
    ["trace", "FROM", "TO"],
    ["grep", "pattern"],
    ["stats"],
    ["module-map"],
    ["bearings"],
    ["patterns"],
    ["rules"],
]


@pytest.mark.parametrize("argv", READ_COMMANDS, ids=lambda c: c[0])
def test_read_command_without_index_exits_with_guidance(argv, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codeir", *argv, "--repo-path", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "No CodeIR index found" in out
    assert "codeir index" in out


@pytest.mark.parametrize("argv", READ_COMMANDS, ids=lambda c: c[0])
def test_read_command_without_index_creates_no_store(argv, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codeir", *argv, "--repo-path", str(tmp_path)])

    with pytest.raises(SystemExit):
        cli.main()

    assert not (tmp_path / ".codeir").exists()
