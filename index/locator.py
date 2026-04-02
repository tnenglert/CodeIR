"""File discovery, content hashing, and source-slice utilities.

Language-specific parsing and entity extraction are handled by the
language frontend protocol (see ``index.language_base``).  Use
``get_frontend_for_file()`` from ``index.languages`` to obtain the
right frontend, then call its methods directly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, List

from index.languages import path_matches_extensions


def discover_source_files(repo_path: Path, extensions: Iterable[str], hidden_dirs: Iterable[str]) -> List[Path]:
    """Return source files for indexing with simple directory exclusions."""
    hidden = set(hidden_dirs)

    files: List[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in hidden for part in path.parts):
            continue
        if not path_matches_extensions(path, extensions):
            continue
        files.append(path)
    return files


def compute_file_content_hash(file_path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    content = file_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def extract_code_slice(repo_path: Path, file_path: str, start_line: int, end_line: int) -> str:
    """Return an exact inclusive line slice from a repository file."""
    abs_path = (repo_path / file_path).resolve()
    lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    if start_line <= 0:
        start_line = 1
    if end_line < start_line:
        end_line = start_line

    return "".join(lines[start_line - 1 : end_line])
