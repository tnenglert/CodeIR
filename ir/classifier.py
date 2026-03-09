"""Purely coded module-level file classification.

Classifies Python files into semantic categories using filename patterns,
directory position, and AST structural analysis. No LLM involvement.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional


CATEGORIES = (
    "core_logic",
    "router",
    "schema",
    "config",
    "compat",
    "exceptions",
    "constants",
    "tests",
    "init",
    "docs",
    "utils",
)

# Domain classification - what the code does, not what type of code it is
DOMAINS = (
    "http",      # HTTP/networking: requests, responses, sessions, cookies
    "auth",      # Authentication: login, tokens, credentials, digest, basic
    "crypto",    # Cryptography: encryption, hashing, signing
    "db",        # Database: queries, connections, ORM
    "fs",        # File system: reading, writing, paths
    "cli",       # Command line: argument parsing, console I/O
    "async",     # Async/concurrency: tasks, threads, queues
    "parse",     # Parsing/encoding: JSON, XML, YAML, serialization
    "net",       # Low-level networking: sockets, TCP/UDP
    "unknown",   # No clear domain signal
)

# ---------------------------------------------------------------------------
# Filename-based rules (highest priority, handles ~60 % of cases)
# ---------------------------------------------------------------------------

_FILENAME_RULES: List[tuple[callable, str]] = []


def _fn_rule(fn, cat: str) -> None:
    _FILENAME_RULES.append((fn, cat))


_fn_rule(lambda p: p.name == "__init__.py", "init")
_fn_rule(lambda p: p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py", "tests")
_fn_rule(lambda p: p.name in {"config.py", "settings.py", "conf.py", "configuration.py"}, "config")
_fn_rule(lambda p: p.name in {"exceptions.py", "errors.py", "exc.py"}, "exceptions")
_fn_rule(lambda p: p.name in {"constants.py", "consts.py", "const.py"}, "constants")


def _classify_by_filename(file_path: Path) -> Optional[str]:
    for rule_fn, category in _FILENAME_RULES:
        if rule_fn(file_path):
            return category
    return None


# ---------------------------------------------------------------------------
# Directory-based rules
# ---------------------------------------------------------------------------

_DIR_KEYWORDS: Dict[str, str] = {
    "tests": "tests",
    "test": "tests",
    "testing": "tests",
    "config": "config",
    "configuration": "config",
    "schemas": "schema",
    "models": "schema",
}


def _classify_by_directory(file_path: Path) -> Optional[str]:
    for part in file_path.parts:
        lower = part.lower()
        if lower in _DIR_KEYWORDS:
            return _DIR_KEYWORDS[lower]
    return None


# ---------------------------------------------------------------------------
# AST-based structural analysis (second priority)
# ---------------------------------------------------------------------------

_ROUTE_DECORATORS = {"route", "get", "post", "put", "patch", "delete", "head", "options", "api_view", "websocket"}
_SCHEMA_BASES = {"BaseModel", "Schema", "Serializer", "TypedDict", "NamedTuple"}
_COMPAT_IMPORTS = {"platform", "sys", "ctypes", "struct"}
_COMPAT_ATTRS = {"sys.version", "sys.version_info", "os.name", "platform.system", "sys.platform"}


class _ClassificationVisitor(ast.NodeVisitor):
    """Collect structural signals for file classification."""

    def __init__(self) -> None:
        self.route_decorator_count = 0
        self.schema_base_count = 0
        self.exception_class_count = 0
        self.total_class_count = 0
        self.total_function_count = 0
        self.top_level_assign_count = 0
        self.compat_signal_count = 0
        self.has_dataclass_decorator = False
        self.docstring_only = False
        self._depth = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.total_class_count += 1

        # Check base classes
        for base in node.bases:
            base_name = _get_name(base)
            if base_name in _SCHEMA_BASES:
                self.schema_base_count += 1
            if base_name in {"Exception", "BaseException", "Error"} or base_name.endswith("Error") or base_name.endswith("Exception"):
                self.exception_class_count += 1

        # Check decorators
        for dec in node.decorator_list:
            dec_name = _get_name(dec)
            if dec_name == "dataclass":
                self.has_dataclass_decorator = True
                self.schema_base_count += 1

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.total_function_count += 1
        for dec in node.decorator_list:
            dec_name = _get_name(dec)
            if dec_name in _ROUTE_DECORATORS:
                self.route_decorator_count += 1
            # Also check for app.route / router.get style
            if isinstance(dec, ast.Call):
                func_name = _get_name(dec.func)
                if func_name in _ROUTE_DECORATORS:
                    self.route_decorator_count += 1
            elif isinstance(dec, ast.Attribute):
                if dec.attr in _ROUTE_DECORATORS:
                    self.route_decorator_count += 1
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._depth == 0:
            self.top_level_assign_count += 1

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._depth == 0:
            self.top_level_assign_count += 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] in _COMPAT_IMPORTS:
                self.compat_signal_count += 1

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.split(".")[0] in _COMPAT_IMPORTS:
            self.compat_signal_count += 1


# ---------------------------------------------------------------------------
# Domain classification rules
# ---------------------------------------------------------------------------

# File/directory name patterns that indicate domain
_DOMAIN_FILE_PATTERNS: Dict[str, str] = {
    # HTTP domain
    "requests": "http", "response": "http", "sessions": "http", "session": "http",
    "cookies": "http", "cookie": "http", "adapters": "http", "adapter": "http",
    "http": "http", "urls": "http", "url": "http", "headers": "http",
    "api": "http", "client": "http", "transport": "http",
    # Auth domain
    "auth": "auth", "authentication": "auth", "login": "auth", "oauth": "auth",
    "credentials": "auth", "token": "auth", "tokens": "auth", "jwt": "auth",
    "permissions": "auth", "password": "auth",
    # Crypto domain
    "crypto": "crypto", "encryption": "crypto", "hash": "crypto", "signing": "crypto",
    "certs": "crypto", "certificates": "crypto", "ssl": "crypto", "tls": "crypto",
    # Database domain (note: "models" removed - too ambiguous, could be HTTP models)
    "database": "db", "db": "db", "orm": "db", "query": "db",
    "queries": "db", "migrations": "db", "sql": "db",
    # File system domain
    "files": "fs", "storage": "fs", "upload": "fs", "download": "fs",
    # CLI domain
    "cli": "cli", "commands": "cli", "console": "cli", "terminal": "cli",
    # Parsing domain
    "json": "parse", "xml": "parse", "yaml": "parse", "parser": "parse",
    "serializer": "parse", "encoding": "parse", "codec": "parse",
}

# Import patterns that indicate domain
_DOMAIN_IMPORTS: Dict[str, str] = {
    # HTTP
    "requests": "http", "urllib": "http", "urllib3": "http", "aiohttp": "http",
    "httpx": "http", "httplib": "http", "http.client": "http", "http.server": "http",
    "http.cookies": "http", "http.cookiejar": "http",
    # Auth
    "jwt": "auth", "oauthlib": "auth", "authlib": "auth",
    # Crypto
    "cryptography": "crypto", "hashlib": "crypto", "hmac": "crypto",
    "ssl": "crypto", "secrets": "crypto",
    # Database
    "sqlalchemy": "db", "psycopg2": "db", "pymysql": "db", "sqlite3": "db",
    "motor": "db", "pymongo": "db", "redis": "db",
    # File system
    "pathlib": "fs", "shutil": "fs", "tempfile": "fs", "glob": "fs",
    # CLI
    "argparse": "cli", "click": "cli", "typer": "cli",
    # Parsing
    "json": "parse", "xml": "parse", "yaml": "parse", "toml": "parse",
    "csv": "parse", "pickle": "parse",
    # Async
    "asyncio": "async", "threading": "async", "multiprocessing": "async",
    "concurrent": "async", "queue": "async",
    # Low-level net
    "socket": "net", "select": "net", "selectors": "net",
}


class _DomainVisitor(ast.NodeVisitor):
    """Collect domain signals from imports."""

    def __init__(self) -> None:
        self.domain_signals: Dict[str, int] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            if mod in _DOMAIN_IMPORTS:
                domain = _DOMAIN_IMPORTS[mod]
                self.domain_signals[domain] = self.domain_signals.get(domain, 0) + 1

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            mod = node.module.split(".")[0]
            if mod in _DOMAIN_IMPORTS:
                domain = _DOMAIN_IMPORTS[mod]
                self.domain_signals[domain] = self.domain_signals.get(domain, 0) + 1


def classify_domain(file_path: Path, tree: ast.Module) -> str:
    """Classify a file by domain based on filename, directory, and imports.

    Returns one of: http, auth, crypto, db, fs, cli, async, parse, net, unknown
    """
    # 1. Check filename
    stem = file_path.stem.lower()
    if stem in _DOMAIN_FILE_PATTERNS:
        return _DOMAIN_FILE_PATTERNS[stem]

    # 2. Check directory parts
    for part in file_path.parts:
        lower = part.lower()
        if lower in _DOMAIN_FILE_PATTERNS:
            return _DOMAIN_FILE_PATTERNS[lower]

    # 3. Check imports
    visitor = _DomainVisitor()
    visitor.visit(tree)
    if visitor.domain_signals:
        # Return domain with most signals
        return max(visitor.domain_signals, key=visitor.domain_signals.get)

    return "unknown"


def _get_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _get_name(node.func)
    return ""


def _classify_by_ast(tree: ast.Module) -> Optional[str]:
    visitor = _ClassificationVisitor()
    visitor.visit(tree)

    v = visitor

    # Docstring-only module
    body_without_imports = [
        n for n in tree.body
        if not isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    if len(body_without_imports) <= 1 and body_without_imports and isinstance(body_without_imports[0], ast.Expr) and isinstance(body_without_imports[0].value, (ast.Constant, ast.Str)):
        return "docs"

    # Pure exception module
    if v.exception_class_count > 0 and v.exception_class_count == v.total_class_count and v.total_function_count == 0:
        return "exceptions"

    # Router-heavy file
    if v.route_decorator_count >= 2:
        return "router"
    if v.route_decorator_count == 1 and v.total_function_count <= 3:
        return "router"

    # Schema-heavy file
    if v.schema_base_count >= 2:
        return "schema"
    if v.schema_base_count == 1 and v.total_class_count <= 3 and v.total_function_count <= 2:
        return "schema"

    # Compat module
    if v.compat_signal_count >= 3:
        return "compat"

    # Constants-only module
    if v.top_level_assign_count >= 3 and v.total_function_count == 0 and v.total_class_count == 0:
        return "constants"

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_file(file_path: Path, tree: ast.Module) -> str:
    """Return a category string for a Python file.

    Signal hierarchy: filename -> directory -> AST content -> fallback.
    """
    # 1. Filename patterns
    cat = _classify_by_filename(file_path)
    if cat:
        return cat

    # 2. Directory patterns
    cat = _classify_by_directory(file_path)
    if cat:
        return cat

    # 3. AST content analysis
    cat = _classify_by_ast(tree)
    if cat:
        return cat

    # 4. Fallback heuristic
    visitor = _ClassificationVisitor()
    visitor.visit(tree)
    total_defs = visitor.total_function_count + visitor.total_class_count
    if total_defs == 0:
        return "constants" if visitor.top_level_assign_count > 0 else "docs"
    if total_defs <= 3 and visitor.top_level_assign_count == 0:
        return "utils"

    return "core_logic"


def classify_files(file_entries: List[Dict[str, object]]) -> Dict[str, str]:
    """Batch classify files. Each entry needs 'file_path' (Path) and 'tree' (ast.Module)."""
    return {
        str(entry["file_path"]): classify_file(Path(entry["file_path"]), entry["tree"])
        for entry in file_entries
    }


# ---------------------------------------------------------------------------
# Module IR lines + context file generation
# ---------------------------------------------------------------------------

def to_module_ir_line(
    module_id: str, file_path: str, category: str,
    entity_count: int, deps_internal: str, churn: str = "-",
) -> str:
    """Emit a compact module IR line.

    Format: MD SESS sessions.py | cat:core_logic | entities:12 | deps:auth,utils | churn:-

    For files with common names (jwt.py, base.py, etc.), includes parent directories
    to disambiguate: auth/strategy/jwt.py vs jwt.py
    """
    # Use shortened path that keeps enough context to disambiguate
    parts = file_path.split("/") if "/" in file_path else [file_path]
    filename = parts[-1]

    # For common filenames, include parent path for disambiguation
    common_names = {
        "jwt.py", "base.py", "models.py", "utils.py", "helpers.py",
        "db.py", "app.py", "main.py", "users.py", "schemas.py",
        "__init__.py", "strategy.py", "config.py", "settings.py",
    }
    if filename.lower() in common_names and len(parts) > 1:
        # Include up to 2 parent directories for context
        context_parts = parts[-3:] if len(parts) >= 3 else parts
        display_path = "/".join(context_parts)
    else:
        display_path = filename

    deps = deps_internal if deps_internal else "-"
    return f"MD {module_id} {display_path} | cat:{category} | entities:{entity_count} | deps:{deps} | churn:{churn}"


def generate_context_file(
    repo_name: str, modules: List[Dict[str, object]],
    total_entities: int, module_ids: Dict[str, str],
) -> str:
    """Produce bearings.md — agent orientation context for a codebase.

    Groups modules by category, emits compact IR lines per module,
    and provides a structural overview suitable for dropping into an
    agent workspace as the first file read on entry.
    """
    by_cat: Dict[str, List[Dict[str, object]]] = {}
    for mod in modules:
        by_cat.setdefault(mod["category"], []).append(mod)

    lines: List[str] = []
    lines.append(f"# {repo_name}")
    lines.append("")
    lines.append(f"Files: {len(modules)} | Entities: {total_entities}")
    lines.append("")
    for category in sorted(by_cat):
        cat_mods = sorted(by_cat[category], key=lambda m: str(m["file_path"]))
        cat_entities = sum(int(m.get("entity_count", 0)) for m in cat_mods)
        lines.append(f"## {category} ({len(cat_mods)} files, {cat_entities} entities)")
        lines.append("```")
        for mod in cat_mods:
            mid = module_ids.get(str(mod["file_path"]), "MD_UNKN")
            lines.append(to_module_ir_line(
                module_id=mid, file_path=str(mod["file_path"]),
                category=str(mod["category"]),
                entity_count=int(mod.get("entity_count", 0)),
                deps_internal=str(mod.get("deps_internal", "")),
            ))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
