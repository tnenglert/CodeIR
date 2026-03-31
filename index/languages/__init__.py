"""Language frontend registry for CodeIR."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional

from index.languages.base import LanguageFrontend
from index.languages.python import PythonFrontend
from index.languages.typescript import TypeScriptFrontend


FRONTENDS: Dict[str, LanguageFrontend] = {
    "python": PythonFrontend(),
    "typescript": TypeScriptFrontend(),
}


def get_frontend(name: str) -> LanguageFrontend:
    key = str(name or "").strip().lower()
    if key not in FRONTENDS:
        raise ValueError(f"Unsupported language frontend: {name}")
    return FRONTENDS[key]


def detect_frontend(repo_path: Path, config: dict) -> LanguageFrontend:
    """Select the active frontend from explicit config or repo contents."""
    explicit = str(config.get("language", "auto")).strip().lower()
    if explicit and explicit != "auto":
        return get_frontend(explicit)

    configured_exts = {str(ext).lower() for ext in config.get("extensions", []) if ext}
    if configured_exts:
        for frontend in FRONTENDS.values():
            if configured_exts and configured_exts.issubset(set(frontend.extensions)):
                return frontend

    counts = {
        "python": 0,
        "typescript": 0,
    }
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in FRONTENDS["python"].extensions:
            counts["python"] += 1
        elif suffix in FRONTENDS["typescript"].extensions:
            counts["typescript"] += 1

    if counts["typescript"] > counts["python"]:
        return FRONTENDS["typescript"]
    return FRONTENDS["python"]


def effective_extensions(frontend: LanguageFrontend, configured: Optional[Iterable[str]]) -> list[str]:
    """Return the extensions that should be indexed for the active frontend."""
    if configured:
        allowed = [str(ext) for ext in configured if str(ext).lower() in frontend.extensions]
        if allowed:
            return allowed
    return list(frontend.extensions)
