"""File discovery, language dispatch, entity extraction, and content hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, List, Optional

from index.languages import get_frontend_for_file


def discover_source_files(repo_path: Path, extensions: Iterable[str], hidden_dirs: Iterable[str]) -> List[Path]:
    """Return source files for indexing with simple directory exclusions."""
    ext_set = {ext.lower() for ext in extensions}
    hidden = set(hidden_dirs)

    files: List[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ext_set:
            continue
        if any(part in hidden for part in path.parts):
            continue
        files.append(path)
    return files


def compute_file_content_hash(file_path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    content = file_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def parse_ast(file_path: Path):
    """Parse a source file using the matching language frontend."""
    frontend = get_frontend_for_file(file_path)
    return frontend.parse_ast(file_path)


def parse_entities_from_file(file_path: Path):
    """Extract entity metadata with full semantic analysis from a source file."""
    frontend = get_frontend_for_file(file_path)
    return frontend.parse_entities_from_file(file_path, include_semantic=True)


def parse_bare_entities_from_file(file_path: Path):
    """Extract entity metadata (names/spans only) from a source file."""
    frontend = get_frontend_for_file(file_path)
    return frontend.parse_entities_from_file(file_path, include_semantic=False)


def extract_import_names(file_path: Path, tree=None, repo_path: Optional[Path] = None) -> List[str]:
    """Extract top-level import module names from a parsed source tree."""
    frontend = get_frontend_for_file(file_path)
    parsed = tree if tree is not None else frontend.parse_ast(file_path)
    if parsed is None:
        return []
    return frontend.extract_import_names(parsed, file_path=file_path, repo_path=repo_path)


def discover_package_roots(repo_path: Path, extensions: Optional[Iterable[str]] = None) -> set[str]:
    """Return internal import roots for the repository's active language."""
    if extensions:
        example_ext = next(iter(extensions), None)
        if example_ext:
            frontend = get_frontend_for_file(Path(f"placeholder{example_ext}"))
            return frontend.discover_internal_roots(repo_path)
    for candidate in (repo_path / "__init__.py",):
        if candidate.exists():
            return get_frontend_for_file(candidate).discover_internal_roots(repo_path)
    try:
        frontend = get_frontend_for_file(next(path for path in repo_path.rglob("*") if path.is_file()))
    except (StopIteration, ValueError):
        return set()
    return frontend.discover_internal_roots(repo_path)


def split_imports(
    all_imports: List[str],
    package_roots: set[str],
    file_path: Optional[Path] = None,
    repo_path: Optional[Path] = None,
    extensions: Optional[Iterable[str]] = None,
) -> tuple[List[str], List[str]]:
    """Partition imports into (internal, external) using the active frontend."""
    frontend = None
    if file_path is not None:
        frontend = get_frontend_for_file(file_path)
    elif extensions:
        example_ext = next(iter(extensions), None)
        if example_ext:
            frontend = get_frontend_for_file(Path(f"placeholder{example_ext}"))
    if frontend is None:
        internal = sorted({n for n in all_imports if n in package_roots})
        external = sorted({n for n in all_imports if n not in package_roots})
        return internal, external
    return frontend.split_imports(
        all_imports,
        internal_roots=package_roots,
        file_path=file_path,
        repo_path=repo_path,
    )


def extract_code_slice(repo_path: Path, file_path: str, start_line: int, end_line: int) -> str:
    """Return an exact inclusive line slice from a repository file."""
    abs_path = (repo_path / file_path).resolve()
    lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    if start_line <= 0:
        start_line = 1
    if end_line < start_line:
        end_line = start_line

    return "".join(lines[start_line - 1 : end_line])
