"""Packaging integrity checks.

Catches the class of bug where code is added but pyproject.toml isn't
updated to match — missing packages, undeclared dependencies, etc.
These only manifest after pip install, so source-tree tests miss them
without an explicit check like this.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _parse_pyproject_packages() -> set[str]:
    """Extract the packages list from pyproject.toml."""
    text = PYPROJECT.read_text()
    m = re.search(r'packages\s*=\s*\[([^\]]+)\]', text)
    assert m, "Could not find packages list in pyproject.toml"
    return {s.strip().strip('"').strip("'") for s in m.group(1).split(",")}


def _parse_pyproject_all_deps() -> set[str]:
    """Extract all declared dependency names (core + every optional group)."""
    text = PYPROJECT.read_text()
    deps: set[str] = set()
    # Matches lines like: "tree-sitter>=0.21", "anthropic", etc.
    for m in re.finditer(r'"([a-zA-Z][a-zA-Z0-9_-]*)', text):
        deps.add(m.group(1).lower().replace("-", "_"))
    return deps


def _find_real_packages() -> set[str]:
    """Find all directories under ROOT that contain __init__.py.

    Skips tests, fixtures, hidden dirs, and .codeir.
    Returns dotted package names (e.g., 'index.lang').
    """
    packages: set[str] = set()
    skip = {"tests", "scripts", ".git", ".codeir", "__pycache__", ".venv", "venv"}

    for init in ROOT.rglob("__init__.py"):
        rel = init.parent.relative_to(ROOT)
        parts = rel.parts

        # Skip anything rooted in a non-package directory
        if parts and parts[0] in skip:
            continue

        dotted = ".".join(parts)
        if dotted:
            packages.add(dotted)

    return packages


def _find_third_party_imports() -> dict[str, list[str]]:
    """Scan non-test .py files for top-level imports that aren't stdlib or local.

    Returns {module_root: [files_that_import_it]}.
    Only checks unconditional top-level imports (not inside try/except or if blocks).
    """
    import sys
    if hasattr(sys, "stdlib_module_names"):
        stdlib = set(sys.stdlib_module_names)
    else:
        # Python 3.9 fallback — cover the modules actually used in this project
        import pkgutil
        stdlib = {m.name for m in pkgutil.iter_modules() if m.ispkg or not hasattr(m, 'module_finder')}
        stdlib |= {
            "__future__", "abc", "argparse", "ast", "asyncio", "base64",
            "bisect", "builtins", "collections", "contextlib", "copy",
            "csv", "dataclasses", "datetime", "enum", "fnmatch", "functools",
            "hashlib", "html", "http", "importlib", "inspect", "io", "itertools",
            "json", "logging", "math", "multiprocessing", "operator", "os",
            "pathlib", "pickle", "platform", "pprint", "queue", "random", "re",
            "shutil", "signal", "socket", "sqlite3", "string", "struct",
            "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
            "traceback", "typing", "unittest", "urllib", "uuid", "warnings",
            "xml", "zipfile",
        }

    local_packages = _find_real_packages()
    local_roots = {p.split(".")[0] for p in local_packages}

    third_party: dict[str, list[str]] = {}

    for py_file in ROOT.glob("**/*.py"):
        rel = py_file.relative_to(ROOT)
        parts = rel.parts
        if parts[0] in ("tests", "scripts", ".git", "venv", ".venv"):
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue

        for node in ast.iter_child_nodes(tree):
            # Skip imports inside try/except (those are gated)
            if isinstance(node, ast.Try):
                continue

            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    roots = [node.module.split(".")[0]]

            for root in roots:
                if root in stdlib or root in local_roots:
                    continue
                third_party.setdefault(root, []).append(str(rel))

    return third_party


class TestPackagesListed:
    def test_all_packages_in_pyproject(self):
        """Every directory with __init__.py must appear in pyproject.toml packages."""
        declared = _parse_pyproject_packages()
        actual = _find_real_packages()
        missing = actual - declared
        assert not missing, (
            f"Packages exist on disk but are missing from pyproject.toml: {sorted(missing)}. "
            f"Add them to [tool.setuptools] packages."
        )

    def test_no_stale_packages_in_pyproject(self):
        """Every entry in pyproject.toml packages should exist on disk."""
        declared = _parse_pyproject_packages()
        actual = _find_real_packages()
        stale = declared - actual
        assert not stale, (
            f"Packages listed in pyproject.toml but not found on disk: {sorted(stale)}. "
            f"Remove them or create the missing __init__.py."
        )


class TestDependenciesDeclared:
    def test_third_party_imports_are_declared(self):
        """Unconditional third-party imports must appear in pyproject.toml dependencies."""
        declared = _parse_pyproject_all_deps()
        third_party = _find_third_party_imports()

        undeclared = {
            mod: files for mod, files in third_party.items()
            if mod.lower().replace("-", "_") not in declared
        }

        assert not undeclared, (
            f"Third-party modules imported unconditionally but not declared in pyproject.toml: "
            f"{', '.join(f'{mod} (in {files[0]})' for mod, files in sorted(undeclared.items()))}. "
            f"Add them to [project.dependencies] or [project.optional-dependencies], "
            f"or gate the import with try/except."
        )


class TestOptionalDependencyGating:
    def test_rust_frontend_imports_without_optional_dependencies(self):
        """Rust frontend module should import cleanly when optional deps are absent.

        The feature may be unusable until extras are installed, but import-time
        failure would break clean environments before the user ever opts into Rust.
        """
        rust_module = ROOT / "index" / "rust_language.py"
        script = textwrap.dedent(
            f"""
            import builtins
            import importlib.util
            import sys

            real_import = builtins.__import__

            def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
                blocked = {{"tree_sitter", "tree_sitter_rust"}}
                if name in blocked or any(name.startswith(mod + ".") for mod in blocked):
                    raise ModuleNotFoundError(name)
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = fake_import
            sys.path.insert(0, {str(ROOT)!r})

            spec = importlib.util.spec_from_file_location("rust_language_probe", {str(rust_module)!r})
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            frontend = module.RustFrontend()
            try:
                frontend._parser_instance()
            except RuntimeError as exc:
                print(exc)
            else:
                raise SystemExit("expected RuntimeError when rust deps are missing")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 0, result.stderr or result.stdout
        assert "tree-sitter" in result.stdout
        assert "rust extra" in result.stdout.lower()

    def test_typescript_frontend_imports_without_optional_dependencies(self):
        """TypeScript frontend module should import cleanly when deps are absent."""
        ts_module = ROOT / "index" / "typescript_language.py"
        script = textwrap.dedent(
            f"""
            import builtins
            import importlib.util
            import sys
            import tempfile
            from pathlib import Path

            real_import = builtins.__import__

            def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
                blocked = {{"tree_sitter", "tree_sitter_typescript"}}
                if name in blocked or any(name.startswith(mod + ".") for mod in blocked):
                    raise ModuleNotFoundError(name)
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = fake_import
            sys.path.insert(0, {str(ROOT)!r})

            spec = importlib.util.spec_from_file_location("typescript_language_probe", {str(ts_module)!r})
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            with tempfile.TemporaryDirectory() as tmp:
                file_path = Path(tmp) / "demo.ts"
                file_path.write_text("export const answer = 42;\\n", encoding="utf-8")
                frontend = module.TypeScriptFrontend()
                try:
                    frontend.parse_ast(file_path)
                except RuntimeError as exc:
                    print(exc)
                else:
                    raise SystemExit("expected RuntimeError when typescript deps are missing")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 0, result.stderr or result.stdout
        assert "tree-sitter" in result.stdout
        assert "typescript extra" in result.stdout.lower()
