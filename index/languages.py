"""Language frontend registry and repository language resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from index.python_language import PythonFrontend
from index.typescript_language import TypeScriptFrontend


class LanguageFrontend:
    """Shared interface for language-specific indexing frontends."""

    name: str = ""
    extensions: Tuple[str, ...] = ()

    def parse_ast(self, file_path: Path) -> Any:
        raise NotImplementedError

    def parse_entities_from_file(self, file_path: Path, include_semantic: bool = True) -> List[Dict[str, object]]:
        raise NotImplementedError

    def extract_import_names(self, tree: Any, file_path: Optional[Path] = None, repo_path: Optional[Path] = None) -> List[str]:
        raise NotImplementedError

    def discover_internal_roots(self, repo_path: Path) -> set[str]:
        raise NotImplementedError

    def split_imports(
        self,
        all_imports: Sequence[str],
        internal_roots: set[str],
        file_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
    ) -> tuple[List[str], List[str]]:
        raise NotImplementedError

    def classify_file(self, file_path: Path, tree: Any) -> str:
        raise NotImplementedError

    def classify_domain(self, file_path: Path, tree: Any) -> str:
        raise NotImplementedError

    def build_import_map(self, tree: Any, file_path: Path, repo_path: Path) -> Dict[str, str]:
        raise NotImplementedError

    @property
    def stoplist(self) -> set[str]:
        return set()


_FRONTENDS: Tuple[LanguageFrontend, ...] = (
    PythonFrontend(),
    TypeScriptFrontend(),
)
_BY_NAME = {frontend.name: frontend for frontend in _FRONTENDS}
_BY_EXTENSION = {
    ext: frontend
    for frontend in _FRONTENDS
    for ext in frontend.extensions
}


def available_languages() -> Tuple[str, ...]:
    return tuple(sorted(_BY_NAME))


def get_frontend(name: str) -> LanguageFrontend:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported language '{name}'. Available: {', '.join(available_languages())}"
        ) from exc


def get_frontend_for_file(file_path: Path) -> LanguageFrontend:
    frontend = _BY_EXTENSION.get(file_path.suffix.lower())
    if frontend is None:
        raise ValueError(f"Unsupported source file extension: {file_path.suffix}")
    return frontend


def get_frontend_for_extensions(extensions: Iterable[str]) -> LanguageFrontend:
    matched = {
        get_frontend_for_file(Path(f"placeholder{ext}")).name
        for ext in extensions
    }
    if not matched:
        raise ValueError("No source extensions configured")
    if len(matched) > 1:
        raise ValueError(
            "Mixed-language indexing is not supported in Phase 1; "
            "configure a single language or a single language's extensions."
        )
    return get_frontend(next(iter(matched)))


def infer_repo_frontend(repo_path: Path, hidden_dirs: Iterable[str]) -> LanguageFrontend:
    """Infer the repository language from the source files present."""
    hidden = set(hidden_dirs)
    counts: Dict[str, int] = {name: 0 for name in available_languages()}

    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in hidden for part in path.parts):
            continue
        frontend = _BY_EXTENSION.get(path.suffix.lower())
        if frontend is None:
            continue
        counts[frontend.name] += 1

    nonzero = {name: count for name, count in counts.items() if count > 0}
    if not nonzero:
        raise ValueError(
            "Could not infer repository language. "
            "No supported source files found for Python or TypeScript."
        )
    if len(nonzero) > 1:
        raise ValueError(
            "Mixed-language repository detected. Phase 1 supports Python-only or "
            "TypeScript-only indexing; set `language` or `extensions` in `.codeir/config.json`."
        )
    return get_frontend(next(iter(nonzero)))


def resolve_frontend_config(repo_path: Path, config: Dict[str, Any]) -> tuple[LanguageFrontend, List[str]]:
    """Resolve the active frontend and source extensions for an indexing run."""
    language = config.get("language")
    if language:
        frontend = get_frontend(str(language).strip().lower())
        return frontend, list(frontend.extensions)

    extensions = config.get("extensions")
    if extensions:
        frontend = get_frontend_for_extensions(extensions)
        return frontend, list(extensions)

    frontend = infer_repo_frontend(repo_path, config.get("hidden_dirs", []))
    return frontend, list(frontend.extensions)
