"""Language frontend registry and source-file matching helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from index.language_base import LanguageFrontend
from index.python_language import PythonFrontend


def _load_rust_frontend():
    from index.rust_language import RustFrontend

    return RustFrontend()


def _load_typescript_frontend():
    from index.typescript_language import TypeScriptFrontend

    return TypeScriptFrontend()


_NORM_CACHE: Dict[Tuple[str, ...], Tuple[str, ...]] = {}


def normalize_extensions(extensions: Iterable[str]) -> Tuple[str, ...]:
    """Return normalized lowercase extensions with a leading dot.

    Results are cached to avoid repeated normalization in hot loops
    (e.g., per-file during discovery).
    """
    raw = tuple(str(e) for e in extensions)
    if raw in _NORM_CACHE:
        return _NORM_CACHE[raw]
    normalized: List[str] = []
    for ext in raw:
        value = ext.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        normalized.append(value)
    result = tuple(dict.fromkeys(normalized))
    _NORM_CACHE[raw] = result
    return result


def path_matches_extensions(file_path: Path, extensions: Iterable[str]) -> bool:
    """Return True when a path matches one of the configured source extensions."""
    name = file_path.name.lower()
    return any(name.endswith(ext) for ext in normalize_extensions(extensions))


_FRONTEND_SPECS: Dict[str, Dict[str, object]] = {
    "python": {
        "extensions": (".py",),
        "factory": PythonFrontend,
    },
    "rust": {
        "extensions": (".rs",),
        "factory": _load_rust_frontend,
    },
    "typescript": {
        "extensions": (".ts", ".tsx", ".d.ts"),
        "factory": _load_typescript_frontend,
    },
}
_FRONTEND_CACHE: Dict[str, LanguageFrontend] = {}


def _frontend_factory(name: str) -> Callable[[], LanguageFrontend]:
    try:
        return _FRONTEND_SPECS[name]["factory"]  # type: ignore[return-value]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported language '{name}'. Available: {', '.join(available_languages())}"
        ) from exc


def frontend_extensions(name: str) -> Tuple[str, ...]:
    try:
        extensions = _FRONTEND_SPECS[name]["extensions"]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported language '{name}'. Available: {', '.join(available_languages())}"
        ) from exc
    return tuple(extensions)  # type: ignore[arg-type]


def available_languages() -> Tuple[str, ...]:
    return tuple(sorted(_FRONTEND_SPECS))


def get_frontend(name: str) -> LanguageFrontend:
    if name not in _FRONTEND_CACHE:
        frontend = _frontend_factory(name)()
        _FRONTEND_CACHE[name] = frontend
    return _FRONTEND_CACHE[name]


_SUFFIX_CACHE: Dict[str, LanguageFrontend] = {}


def get_frontend_for_file(file_path: Path) -> LanguageFrontend:
    suffix = file_path.suffix.lower()
    if suffix in _SUFFIX_CACHE:
        return _SUFFIX_CACHE[suffix]

    normalized_name = file_path.name.lower()
    matches = [
        name
        for name in available_languages()
        if any(normalized_name.endswith(ext) for ext in frontend_extensions(name))
    ]
    if len(matches) == 1:
        frontend = get_frontend(matches[0])
        _SUFFIX_CACHE[suffix] = frontend
        return frontend
    if not matches:
        raise ValueError(f"Unsupported source file: {file_path.name}")
    raise ValueError(f"Ambiguous frontend match for source file: {file_path.name}")


def get_frontend_for_extensions(extensions: Iterable[str]) -> LanguageFrontend:
    frontends = get_frontends_for_extensions(extensions)
    if len(frontends) != 1:
        raise ValueError(
            "Mixed-language extension sets require per-file dispatch; "
            "use get_frontends_for_extensions() instead."
        )
    return frontends[0]


def get_frontends_for_extensions(extensions: Iterable[str]) -> Tuple[LanguageFrontend, ...]:
    normalized = normalize_extensions(extensions)
    if not normalized:
        raise ValueError("No source extensions configured")

    names: List[str] = []
    unmatched: List[str] = []
    for ext in normalized:
        matches = [
            name
            for name in available_languages()
            if ext in frontend_extensions(name)
        ]
        if not matches:
            unmatched.append(ext)
            continue
        for name in matches:
            if name not in names:
                names.append(name)

    if unmatched:
        raise ValueError(
            f"Unsupported source extensions: {', '.join(unmatched)}"
        )

    return tuple(get_frontend(name) for name in names)


def _detect_frontends_in_repo(
    repo_path: Path,
    hidden_dirs: Iterable[str],
) -> tuple[Tuple[LanguageFrontend, ...], List[str]]:
    hidden = set(hidden_dirs)
    detected_languages: List[str] = []
    detected_extensions: List[str] = []

    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in hidden for part in path.parts):
            continue

        for language in available_languages():
            extensions = frontend_extensions(language)
            if not path_matches_extensions(path, extensions):
                continue
            if language not in detected_languages:
                detected_languages.append(language)
            for ext in extensions:
                if path.name.lower().endswith(ext) and ext not in detected_extensions:
                    detected_extensions.append(ext)
            break

    if not detected_languages:
        return (get_frontend("python"),), list(frontend_extensions("python"))

    return (
        tuple(get_frontend(name) for name in detected_languages),
        detected_extensions,
    )


def resolve_frontend_config(
    repo_path: Path,
    config: Dict[str, Any],
) -> tuple[Tuple[LanguageFrontend, ...], List[str]]:
    """Resolve the active frontends and extensions for an indexing run."""
    language = str(config.get("language", "")).strip().lower()
    if language:
        frontend = get_frontend(language)
        return (frontend,), list(frontend.extensions)

    extensions = config.get("extensions")
    if extensions:
        frontends = get_frontends_for_extensions(extensions)
        return frontends, list(normalize_extensions(extensions))

    return _detect_frontends_in_repo(
        repo_path=repo_path,
        hidden_dirs=config.get("hidden_dirs", []),
    )
