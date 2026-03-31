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

        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

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
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

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

# Strong import signals: specific enough that a single import determines domain.
_DOMAIN_IMPORTS_STRONG: Dict[str, str] = {
    # HTTP
    "requests": "http", "urllib3": "http", "aiohttp": "http",
    "httpx": "http", "httplib": "http",
    # Auth
    "jwt": "auth", "oauthlib": "auth", "authlib": "auth",
    # Crypto
    "cryptography": "crypto", "hmac": "crypto",
    # Database
    "sqlalchemy": "db", "psycopg2": "db", "pymysql": "db", "sqlite3": "db",
    "motor": "db", "pymongo": "db", "redis": "db",
    # CLI
    "argparse": "cli", "typer": "cli",
    # Parsing
    "yaml": "parse", "toml": "parse", "csv": "parse", "pickle": "parse",
    # Async
    "multiprocessing": "async", "concurrent": "async", "queue": "async",
    # Low-level net
    "socket": "net", "select": "net", "selectors": "net",
}

# Weak import signals: too commonly used as utilities to determine domain alone.
# Require a cumulative score >= 2 before assigning domain from imports.
_DOMAIN_IMPORTS_WEAK: Dict[str, str] = {
    # HTTP — urllib/http.* used for URL parsing in non-HTTP files
    "urllib": "http", "http": "http",
    # Auth
    "passlib": "auth",
    # Crypto — hashlib/secrets/ssl used incidentally in many files
    "hashlib": "crypto", "secrets": "crypto", "ssl": "crypto",
    # Database (sqlite3 moved to strong — importing it always means db work)
    # File system — pathlib/tempfile/glob used as utilities everywhere
    "pathlib": "fs", "shutil": "fs", "tempfile": "fs", "glob": "fs",
    # CLI — click used as framework decorator in non-CLI files
    "click": "cli",
    # Parsing — json imported in almost every file
    "json": "parse", "xml": "parse",
    # Async — asyncio/threading used as utilities
    "asyncio": "async", "threading": "async",
}

# Combined map for backwards-compatible lookups
_DOMAIN_IMPORTS: Dict[str, str] = {**_DOMAIN_IMPORTS_STRONG, **_DOMAIN_IMPORTS_WEAK}

_STRONG_SIGNAL_SCORE = 2  # strong import alone is sufficient
_WEAK_SIGNAL_SCORE = 1    # weak import needs a second signal to reach threshold
_DOMAIN_THRESHOLD = 2


class _DomainVisitor(ast.NodeVisitor):
    """Collect domain signals from imports using a two-tier scoring system."""

    def __init__(self) -> None:
        self.domain_scores: Dict[str, int] = {}

    def _record(self, mod: str) -> None:
        if mod in _DOMAIN_IMPORTS_STRONG:
            domain = _DOMAIN_IMPORTS_STRONG[mod]
            self.domain_scores[domain] = self.domain_scores.get(domain, 0) + _STRONG_SIGNAL_SCORE
        elif mod in _DOMAIN_IMPORTS_WEAK:
            domain = _DOMAIN_IMPORTS_WEAK[mod]
            self.domain_scores[domain] = self.domain_scores.get(domain, 0) + _WEAK_SIGNAL_SCORE

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._record(node.module.split(".")[0])

    @property
    def domain_signals(self) -> Dict[str, int]:
        """Backwards-compatible property; returns scores dict."""
        return self.domain_scores


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

    # 3. Check imports — only assign if winning domain meets threshold
    visitor = _DomainVisitor()
    visitor.visit(tree)
    qualifying = {d: s for d, s in visitor.domain_scores.items() if s >= _DOMAIN_THRESHOLD}
    if qualifying:
        return max(qualifying, key=qualifying.get)

    return "unknown"


def _get_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _get_name(node.func)
    return ""


def _classify_by_ast(
    tree: ast.Module, visitor: Optional[_ClassificationVisitor] = None,
) -> tuple:
    """Classify by AST structural signals.

    Returns (category_or_None, visitor) so the visitor can be reused by the fallback.
    """
    if visitor is None:
        visitor = _ClassificationVisitor()
        visitor.visit(tree)

    v = visitor

    # Docstring-only module
    body_without_imports = [
        n for n in tree.body
        if not isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    if (len(body_without_imports) == 1
            and isinstance(body_without_imports[0], ast.Expr)
            and isinstance(body_without_imports[0].value, (ast.Constant, ast.Str))):
        return "docs", v

    # Pure exception module
    if v.exception_class_count > 0 and v.exception_class_count == v.total_class_count and v.total_function_count == 0:
        return "exceptions", v

    # Router-heavy file
    if v.route_decorator_count >= 2:
        return "router", v
    if v.route_decorator_count == 1 and v.total_function_count <= 3:
        return "router", v

    # Schema-heavy file
    if v.schema_base_count >= 2:
        return "schema", v
    if v.schema_base_count == 1 and v.total_class_count <= 3 and v.total_function_count <= 2:
        return "schema", v

    # Compat module
    if v.compat_signal_count >= 3:
        return "compat", v

    # Constants-only module
    if v.top_level_assign_count >= 3 and v.total_function_count == 0 and v.total_class_count == 0:
        return "constants", v

    return None, v


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

    # 3. AST content analysis (visitor is reused in fallback to avoid double traversal)
    cat, visitor = _classify_by_ast(tree)
    if cat:
        return cat

    # 4. Fallback heuristic (reuses visitor from step 3)
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
        # Rust common filenames
        "mod.rs", "lib.rs", "main.rs", "error.rs", "errors.rs",
        "config.rs", "utils.rs", "types.rs", "models.rs",
    }
    if filename.lower() in common_names and len(parts) > 1:
        # Include up to 2 parent directories for context
        context_parts = parts[-3:] if len(parts) >= 3 else parts
        display_path = "/".join(context_parts)
    else:
        display_path = filename

    deps = deps_internal if deps_internal else "-"
    return f"MD {module_id} {display_path} | cat:{category} | entities:{entity_count} | deps:{deps} | churn:{churn}"


# ---------------------------------------------------------------------------
# Tiered bearings rendering
# ---------------------------------------------------------------------------

# Modules with >= this many entities are individually listed in tier 2
INDIVIDUAL_LISTING_THRESHOLD = 5

# Filename must appear > this many times within a category to trigger collapse
PATTERN_COLLAPSE_TRIGGER = 5


def _group_by_category(
    modules: List[Dict[str, object]],
) -> Dict[str, List[Dict[str, object]]]:
    """Group modules by category, sorted by file_path within each."""
    by_cat: Dict[str, List[Dict[str, object]]] = {}
    for mod in modules:
        by_cat.setdefault(mod["category"], []).append(mod)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda m: str(m["file_path"]))
    return by_cat


def _collapse_patterns(
    cat_mods: List[Dict[str, object]],
    module_ids: Dict[str, str],
) -> tuple:
    """Separate modules into individually listed and pattern-collapsed groups.

    Returns (individual_lines, pattern_summaries) where:
    - individual_lines: list of IR line strings for modules >= threshold
    - pattern_summaries: list of summary strings for collapsed filename patterns
    """
    individual: List[str] = []
    small: List[Dict[str, object]] = []

    for mod in cat_mods:
        ec = int(mod.get("entity_count", 0))
        if ec >= INDIVIDUAL_LISTING_THRESHOLD:
            mid = module_ids.get(str(mod["file_path"]), "MD_UNKN")
            individual.append(to_module_ir_line(
                module_id=mid, file_path=str(mod["file_path"]),
                category=str(mod["category"]),
                entity_count=ec,
                deps_internal=str(mod.get("deps_internal", "")),
            ))
        else:
            small.append(mod)

    # Group small modules by bare filename within this category
    by_filename: Dict[str, List[Dict[str, object]]] = {}
    for mod in small:
        fp = str(mod["file_path"])
        fname = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        by_filename.setdefault(fname, []).append(mod)

    pattern_summaries: List[str] = []
    ungrouped: List[Dict[str, object]] = []

    for fname, group in sorted(by_filename.items()):
        nonzero = [m for m in group if int(m.get("entity_count", 0)) > 0]
        zero = [m for m in group if int(m.get("entity_count", 0)) == 0]

        if len(group) > PATTERN_COLLAPSE_TRIGGER:
            # Collapse this pattern
            total_ents = sum(int(m.get("entity_count", 0)) for m in group)
            nonzero_count = len(nonzero)
            zero_count = len(zero)

            # Collect module IDs for searchability
            mids = sorted(
                module_ids.get(str(m["file_path"]), "")
                for m in nonzero if module_ids.get(str(m["file_path"]))
            )
            mid_range = ""
            if mids:
                if len(mids) <= 3:
                    mid_range = f" [{', '.join(mids)}]"
                else:
                    mid_range = f" [{mids[0]}..{mids[-1]}]"

            parts = [f"{fname} ×{len(group)}"]
            if total_ents > 0:
                parts.append(f"{total_ents} entities")
            if zero_count > 0:
                parts.append(f"{zero_count} empty")
            summary = " | ".join(parts) + mid_range
            pattern_summaries.append(summary)
        else:
            # Not enough repetitions to collapse — list individually (skip zero)
            ungrouped.extend(nonzero)

    # Render ungrouped small modules as individual lines
    for mod in sorted(ungrouped, key=lambda m: str(m["file_path"])):
        mid = module_ids.get(str(mod["file_path"]), "MD_UNKN")
        individual.append(to_module_ir_line(
            module_id=mid, file_path=str(mod["file_path"]),
            category=str(mod["category"]),
            entity_count=int(mod.get("entity_count", 0)),
            deps_internal=str(mod.get("deps_internal", "")),
        ))

    return individual, pattern_summaries


def generate_summary(
    repo_name: str, modules: List[Dict[str, object]],
    total_entities: int,
) -> str:
    """Tier 1: bearings-summary.md — table of contents (~1-2k tokens).

    Repo-level stats and category listing with counts.
    """
    by_cat = _group_by_category(modules)

    lines: List[str] = []
    lines.append(f"# {repo_name}")
    lines.append("")
    lines.append(f"Files: {len(modules)} | Entities: {total_entities}")
    lines.append("")

    lines.append("## Categories")
    lines.append("")
    for category in sorted(by_cat):
        cat_mods = by_cat[category]
        cat_entities = sum(int(m.get("entity_count", 0)) for m in cat_mods)
        nonzero = sum(1 for m in cat_mods if int(m.get("entity_count", 0)) > 0)
        lines.append(f"- **{category}**: {len(cat_mods)} files, {cat_entities} entities ({nonzero} non-empty)")

    lines.append("")
    lines.append("Full module map: `.codeir/bearings.md`")
    lines.append("Per-category detail: `.codeir/bearings/{category}.md`")
    lines.append("")
    return "\n".join(lines)


def generate_context_file(
    repo_name: str, modules: List[Dict[str, object]],
    total_entities: int, module_ids: Dict[str, str],
) -> str:
    """Tier 2: bearings.md — collapsed working map.

    Modules with >= INDIVIDUAL_LISTING_THRESHOLD entities are listed individually.
    Repeated filenames with low entity counts are collapsed into pattern summaries.
    Zero-entity modules appear only in pattern counts.
    """
    by_cat = _group_by_category(modules)

    lines: List[str] = []
    lines.append(f"# {repo_name}")
    lines.append("")
    lines.append(f"Files: {len(modules)} | Entities: {total_entities}")
    lines.append("")
    lines.append("> IR for triage. `callers`/`impact` for blast radius. `expand` where mechanism is ambiguous. Targeted code reads to fill in understanding or plan changes.")
    lines.append("")

    for category in sorted(by_cat):
        cat_mods = by_cat[category]
        cat_entities = sum(int(m.get("entity_count", 0)) for m in cat_mods)
        individual, patterns = _collapse_patterns(cat_mods, module_ids)

        lines.append(f"## {category} ({len(cat_mods)} files, {cat_entities} entities)")
        if individual:
            lines.append("```")
            for line in individual:
                lines.append(line)
            lines.append("```")
        if patterns:
            lines.append("")
            lines.append("Patterns:")
            for p in patterns:
                lines.append(f"  {p}")
        lines.append("")

    return "\n".join(lines)


def generate_category_file(
    repo_name: str, category: str,
    cat_modules: List[Dict[str, object]],
    module_ids: Dict[str, str],
    db_path: Optional[Path] = None,
) -> str:
    """Tier 3: bearings/{category}.md — full uncollapsed detail for one category.

    Args:
        repo_name: Repository name for header
        category: Category name
        cat_modules: List of modules in this category
        module_ids: Mapping from file_path to module ID
        db_path: Optional path to entities.db for pattern lookup
    """
    cat_mods = sorted(cat_modules, key=lambda m: str(m["file_path"]))
    cat_entities = sum(int(m.get("entity_count", 0)) for m in cat_mods)

    lines: List[str] = []
    lines.append(f"# {repo_name} — {category}")
    lines.append("")
    lines.append(f"Files: {len(cat_mods)} | Entities: {cat_entities}")
    lines.append("")

    # Add pattern summary if available
    if db_path and db_path.exists():
        pattern_summary = _get_pattern_summary_for_category(db_path, category, cat_entities)
        if pattern_summary:
            lines.append(pattern_summary)
            lines.append("")

    lines.append("### Modules")
    lines.append("```")
    for mod in cat_mods:
        mid = module_ids.get(str(mod["file_path"]), "MD_UNKN")
        lines.append(to_module_ir_line(
            module_id=mid, file_path=str(mod["file_path"]),
            category=category,
            entity_count=int(mod.get("entity_count", 0)),
            deps_internal=str(mod.get("deps_internal", "")),
        ))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _get_pattern_summary_for_category(db_path: Path, category: str, total_entities: int) -> Optional[str]:
    """Generate pattern summary block for a category."""
    try:
        from index.pattern_detector import get_patterns
    except ImportError:
        return None

    patterns = get_patterns(db_path, category=category)
    if not patterns:
        return None

    is_test = category.lower() in ("tests", "test", "testing")

    if is_test:
        # Compact format for test patterns
        pattern_list = ", ".join(f"{p.base_class} ({p.member_count})" for p in patterns)
        return f"**Patterns:** {pattern_list}"

    # Full format for non-test patterns
    lines = ["### Structural Patterns"]

    total_in_patterns = sum(p.member_count for p in patterns)

    for p in patterns:
        calls_str = ", ".join(p.common_calls[:5]) if p.common_calls else "-"
        flags_str = p.common_flags if p.common_flags else "-"
        lines.append(f"- **{p.base_class}** ({p.member_count} classes): Calls: {calls_str}. Flags: {flags_str}.")

    if total_entities > 0:
        lines.append(f"\n→ {total_in_patterns} of {total_entities} entities follow known patterns.")

    return "\n".join(lines)
