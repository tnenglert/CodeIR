"""Purely coded module-level file classification.

Classifies Python files into semantic categories using filename patterns,
directory position, and AST structural analysis. No LLM involvement.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


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

# Domain classification - what functional/architectural concern the code participates in
DOMAINS = (
    # --- Infrastructure domains (original) ---
    "http",      # HTTP/networking: requests, responses, sessions, cookies
    "auth",      # Authentication: login, tokens, credentials, digest, basic
    "crypto",    # Cryptography: encryption, hashing, signing
    "db",        # Database: queries, connections, ORM
    "fs",        # File system: reading, writing, paths
    "cli",       # Command line: argument parsing, console I/O
    "async",     # Async/concurrency: tasks, threads, queues
    "parse",     # Parsing/encoding: JSON, XML, YAML, serialization
    "net",       # Low-level networking: sockets, TCP/UDP
    # --- Application-structure domains ---
    "ui",        # UI/rendering: templates, views, forms, widgets
    "validation", # Input validation: validators, cleaners, schema enforcement
    "i18n",      # Internationalization: translation, locale, gettext
    "task",      # Background tasks: celery, workers, jobs, scheduling
    "event",     # Events/signals: dispatch, hooks, listeners, pubsub
    "log",       # Logging/observability: logging, tracing, metrics
    "mail",      # Email/notifications: smtp, sendgrid, mailgun
    "media",     # Media processing: images, thumbnails, uploads, audio/video
    "admin",     # Admin/management: admin panels, management commands
    "cache",     # Caching: memcached, redis-as-cache, cache backends
    "misc",      # Classified — genuinely no applicable domain (cross-cutting, glue, boilerplate)
    "unknown",   # Indexer failure — parse error, missing tree, file skipped
)

# Sentinel constants — use these instead of string literals for the two
# terminal states so typos are caught at import time.
DOMAIN_MISC = "misc"
DOMAIN_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DomainContext:
    """Language-neutral hints that can refine domain resolution."""

    category: Optional[str] = None
    internal_roots: Optional[Set[str]] = None


@dataclass
class DomainEvidence:
    """Aggregated domain signals from language-specific and shared heuristics."""

    import_scores: Dict[str, int] = field(default_factory=dict)
    name_scores: Dict[str, int] = field(default_factory=dict)
    package_scores: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainDecision:
    """Resolved module-domain decision with provenance for later refinement."""

    domain: str
    source: str
    strength: str
    scores: Dict[str, int] = field(default_factory=dict)

    @property
    def is_refinable(self) -> bool:
        return self.domain in {DOMAIN_MISC, DOMAIN_UNKNOWN} or self.strength == "weak"

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
    "services": "core_logic",
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
        self.dataclass_count = 0
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

        # Check decorators — dataclass tracked separately from schema bases
        for dec in node.decorator_list:
            dec_name = _get_name(dec)
            if dec_name == "dataclass":
                self.has_dataclass_decorator = True
                self.dataclass_count += 1

        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.total_function_count += 1
        counted_route = False
        for dec in node.decorator_list:
            if counted_route:
                break
            dec_name = _get_name(dec)
            if dec_name in _ROUTE_DECORATORS:
                self.route_decorator_count += 1
                counted_route = True
            elif isinstance(dec, ast.Call):
                func_name = _get_name(dec.func)
                if func_name in _ROUTE_DECORATORS:
                    self.route_decorator_count += 1
                    counted_route = True
            elif isinstance(dec, ast.Attribute):
                if dec.attr in _ROUTE_DECORATORS:
                    self.route_decorator_count += 1
                    counted_route = True
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
    # UI/rendering domain
    "templates": "ui", "template": "ui", "views": "ui", "view": "ui",
    "forms": "ui", "form": "ui", "widgets": "ui", "widget": "ui",
    "rendering": "ui", "renderer": "ui", "layout": "ui", "layouts": "ui",
    "pages": "ui", "components": "ui",
    # Validation domain
    "validators": "validation", "validator": "validation", "validation": "validation",
    # i18n domain
    "locale": "i18n", "locales": "i18n", "translation": "i18n", "translations": "i18n",
    "i18n": "i18n", "l10n": "i18n", "messages": "i18n",
    # Task/job domain
    "tasks": "task", "workers": "task", "worker": "task", "jobs": "task",
    "job": "task", "celery": "task", "cron": "task", "scheduler": "task",
    # Event/signal domain
    "signals": "event", "signal": "event", "events": "event", "dispatch": "event",
    "hooks": "event", "listeners": "event", "handlers": "event",
    # Logging/observability domain
    "logging": "log", "logger": "log", "tracing": "log", "metrics": "log",
    "observability": "log",
    # Mail domain
    "email": "mail", "mail": "mail", "notifications": "mail", "notification": "mail",
    "smtp": "mail",
    # Media domain
    "images": "media", "image": "media", "thumbnails": "media", "thumbnail": "media",
    "media": "media", "avatar": "media", "photos": "media",
    # Admin domain
    "admin": "admin", "management": "admin", "backoffice": "admin",
    # Cache domain
    "cache": "cache", "caching": "cache",
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
    "motor": "db", "pymongo": "db",
    # CLI
    "argparse": "cli", "typer": "cli",
    # Parsing
    "yaml": "parse", "toml": "parse", "csv": "parse", "pickle": "parse",
    # Async
    "multiprocessing": "async", "concurrent": "async", "queue": "async",
    # Low-level net
    "socket": "net", "select": "net", "selectors": "net",
    # UI/rendering
    "jinja2": "ui", "mako": "ui", "wtforms": "ui", "django_crispy_forms": "ui",
    "gi": "ui", "gtk": "ui",
    # Validation
    "cerberus": "validation", "marshmallow": "validation", "voluptuous": "validation",
    "pydantic": "validation",
    # i18n
    "babel": "i18n", "gettext": "i18n",
    # Task/job
    "celery": "task", "rq": "task", "dramatiq": "task", "huey": "task",
    "apscheduler": "task",
    # Event/signal
    "blinker": "event",
    # Logging/observability
    "sentry_sdk": "log", "opentelemetry": "log", "structlog": "log",
    "loguru": "log",
    # Mail
    "sendgrid": "mail", "mailgun": "mail", "smtplib": "mail",
    # Media
    "PIL": "media", "pillow": "media", "wand": "media", "ffmpeg": "media",
    "imageio": "media",
    # Cache
    "memcache": "cache", "pymemcache": "cache", "cachetools": "cache",
    "diskcache": "cache",
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
    # Cache — redis is commonly a cache but also used as a DB/broker
    "redis": "cache",
    # Logging — stdlib logging is everywhere, not always the file's purpose
    "logging": "log",
    # i18n — locale module used for formatting, not always i18n
    "locale": "i18n",
    # Mail — email stdlib module used for MIME parsing too
    "email": "mail",
}

# Combined map for backwards-compatible lookups
_DOMAIN_IMPORTS: Dict[str, str] = {**_DOMAIN_IMPORTS_STRONG, **_DOMAIN_IMPORTS_WEAK}

# Submodule import signals: match on the full dotted path prefix.
# Catches framework-namespaced imports like `from django.forms import ...`
# where the top-level package alone is too ambiguous.
_DOMAIN_SUBMODULE_STRONG: Dict[str, str] = {
    # Django submodules
    "django.forms": "ui", "django.template": "ui", "django.views": "ui",
    "django.shortcuts": "ui",
    "django.db": "db", "django.db.models": "db",
    "django.contrib.admin": "admin",
    "django.contrib.auth": "auth",
    "django.core.mail": "mail",
    "django.core.cache": "cache",
    "django.core.validators": "validation",
    "django.dispatch": "event",
    "django.utils.translation": "i18n",
    "django.core.management": "admin",
    "django.core.files": "fs",
    "django.http": "http",
    "django.urls": "http",
    "django.test": "http",  # test client is HTTP-centric
    # Flask submodules
    "flask.views": "ui", "flask.templating": "ui",
    # GTK / GI
    "gi.repository": "ui",
    "gi.repository.gtk": "ui",
    # Tryton submodules
    "trytond.model": "db", "trytond.pool": "db",
    "trytond.wizard": "ui",
    "trytond.report": "ui",
    "trytond.ir": "db",
    "trytond.transaction": "db",
}

_STRONG_SIGNAL_SCORE = 2  # strong import alone is sufficient
_WEAK_SIGNAL_SCORE = 1    # weak import needs a second signal to reach threshold
_DOMAIN_THRESHOLD = 2
_DOMAIN_CATEGORY_HINTS: Dict[str, str] = {
    "router": "http",
    "schema": "validation",
}
_DOMAIN_SELF_PACKAGE_HINTS: Dict[str, str] = {
    "flask": "http",
    "django": "http",
    "werkzeug": "http",
    "sqlalchemy": "db",
    "trytond": "db",
}
_DOMAIN_NAME_KEYWORDS: Dict[str, str] = {
    "file": "fs",
    "files": "fs",
    "dir": "fs",
    "path": "fs",
    "save": "fs",
    "load": "fs",
    "read": "fs",
    "write": "fs",
    "upload": "fs",
    "download": "fs",
    "storage": "fs",
    "request": "http",
    "response": "http",
    "route": "http",
    "http": "http",
    "cookie": "http",
    "session": "http",
    "redirect": "http",
    "encrypt": "crypto",
    "decrypt": "crypto",
    "hash": "crypto",
    "signature": "crypto",
    "verify": "crypto",
}


class _DomainVisitor(ast.NodeVisitor):
    """Collect domain signals from imports using a two-tier scoring system."""

    def __init__(self) -> None:
        self.domain_scores: Dict[str, int] = {}

    def _record_module(self, full_module: str) -> None:
        """Score a module path, checking submodule signals first, then top-level."""
        # Check submodule signals (longest prefix match)
        for prefix, domain in _DOMAIN_SUBMODULE_STRONG.items():
            if full_module == prefix or full_module.startswith(prefix + "."):
                self.domain_scores[domain] = self.domain_scores.get(domain, 0) + _STRONG_SIGNAL_SCORE
                return

        # Fall back to top-level package lookup
        top = full_module.split(".")[0]
        if top in _DOMAIN_IMPORTS_STRONG:
            domain = _DOMAIN_IMPORTS_STRONG[top]
            self.domain_scores[domain] = self.domain_scores.get(domain, 0) + _STRONG_SIGNAL_SCORE
        elif top in _DOMAIN_IMPORTS_WEAK:
            domain = _DOMAIN_IMPORTS_WEAK[top]
            self.domain_scores[domain] = self.domain_scores.get(domain, 0) + _WEAK_SIGNAL_SCORE

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record_module(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._record_module(node.module)

    @property
    def domain_signals(self) -> Dict[str, int]:
        """Backwards-compatible property; returns scores dict."""
        return self.domain_scores


class _DomainNameVisitor(ast.NodeVisitor):
    """Collect weak domain signals from class and function names."""

    def __init__(self) -> None:
        self.domain_scores: Dict[str, int] = {}

    def _record_name(self, name: str) -> None:
        for domain, score in _score_domain_keywords(name).items():
            self.domain_scores[domain] = self.domain_scores.get(domain, 0) + score

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_name(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_name(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def _score_domain_keywords(*texts: str) -> Dict[str, int]:
    scores: Dict[str, int] = {}
    for text in texts:
        lowered = text.strip("_")
        if not lowered:
            continue
        tokens = {
            token
            for token in re.split(r"[_\W]+|(?<!^)(?=[A-Z])", lowered)
            if token
        }
        for token in tokens:
            domain = _DOMAIN_NAME_KEYWORDS.get(token.lower())
            if domain:
                scores[domain] = scores.get(domain, 0) + 1
    return scores


def _qualifying_domains(scores: Dict[str, int]) -> Dict[str, int]:
    return {domain: score for domain, score in scores.items() if score >= _DOMAIN_THRESHOLD}


def _merge_domain_scores(*score_maps: Dict[str, int]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for score_map in score_maps:
        for domain, score in score_map.items():
            merged[domain] = merged.get(domain, 0) + score
    return merged


def _package_domain_hint(file_path: Path, context: DomainContext) -> Optional[str]:
    if not context.internal_roots:
        return None
    internal_roots = {root.lower() for root in context.internal_roots}
    for part in file_path.parts[:-1]:
        lowered = part.lower()
        if lowered in internal_roots:
            return _DOMAIN_SELF_PACKAGE_HINTS.get(lowered)
    return None


def _collect_domain_evidence(
    file_path: Path,
    tree: Optional[ast.Module],
    context: DomainContext,
) -> DomainEvidence:
    evidence = DomainEvidence()

    package_domain = _package_domain_hint(file_path, context)
    if package_domain:
        evidence.package_scores[package_domain] = _STRONG_SIGNAL_SCORE

    if tree is None:
        return evidence

    import_visitor = _DomainVisitor()
    import_visitor.visit(tree)
    evidence.import_scores = dict(import_visitor.domain_scores)

    name_visitor = _DomainNameVisitor()
    name_visitor.visit(tree)
    evidence.name_scores = dict(name_visitor.domain_scores)

    return evidence


def _resolve_domain_from_evidence(
    file_path: Path,
    tree: Optional[ast.Module],
    evidence: DomainEvidence,
    context: DomainContext,
) -> DomainDecision:
    stem = file_path.stem.lower()
    if stem in _DOMAIN_FILE_PATTERNS:
        domain = _DOMAIN_FILE_PATTERNS[stem]
        return DomainDecision(domain=domain, source="filename", strength="strong")

    for part in file_path.parts:
        lower = part.lower()
        if lower in _DOMAIN_FILE_PATTERNS:
            domain = _DOMAIN_FILE_PATTERNS[lower]
            return DomainDecision(domain=domain, source="directory", strength="strong")

    combined_scores = _merge_domain_scores(evidence.package_scores, evidence.import_scores)
    qualifying = _qualifying_domains(combined_scores)
    if qualifying:
        best_domain = max(qualifying, key=qualifying.get)
        source = "self_package" if evidence.package_scores.get(best_domain, 0) >= _DOMAIN_THRESHOLD else "strong_import"
        return DomainDecision(
            domain=best_domain,
            source=source,
            strength="strong",
            scores=qualifying,
        )

    qualifying = _qualifying_domains(evidence.name_scores)
    if qualifying:
        best_domain = max(qualifying, key=qualifying.get)
        return DomainDecision(
            domain=best_domain,
            source="name_signal",
            strength="weak",
            scores=qualifying,
        )

    category_domain = _DOMAIN_CATEGORY_HINTS.get((context.category or "").lower())
    if category_domain:
        return DomainDecision(domain=category_domain, source="category_hint", strength="weak")

    if tree is not None:
        return DomainDecision(domain=DOMAIN_MISC, source="no_signal", strength="unresolved")
    return DomainDecision(domain=DOMAIN_UNKNOWN, source="parse_failure", strength="unresolved")


def classify_domain_decision(
    file_path: Path,
    tree: Optional[ast.Module],
    *,
    category: Optional[str] = None,
    internal_roots: Optional[Set[str]] = None,
) -> DomainDecision:
    """Resolve a module domain plus provenance for post-pass refinement."""
    context = DomainContext(category=category, internal_roots=internal_roots)
    evidence = _collect_domain_evidence(file_path, tree, context)
    return _resolve_domain_from_evidence(file_path, tree, evidence, context)


def infer_entity_domain_scores(
    entity_name: str,
    qualified_name: str = "",
    call_names: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Infer per-entity domain hints from names and semantic call symbols."""
    scores = _score_domain_keywords(entity_name, qualified_name)
    for call_name in call_names or []:
        for domain, score in _score_domain_keywords(call_name).items():
            scores[domain] = scores.get(domain, 0) + score
    return scores
def classify_domain(
    file_path: Path,
    tree: Optional[ast.Module],
    *,
    category: Optional[str] = None,
    internal_roots: Optional[Set[str]] = None,
) -> str:
    """Classify a file by domain based on shared and language-specific hints.

    `category` and `internal_roots` are optional contextual hints supplied by
    the indexer so the same resolver can be reused across languages.
    """
    return classify_domain_decision(
        file_path,
        tree,
        category=category,
        internal_roots=internal_roots,
    ).domain


# ---------------------------------------------------------------------------
# Layer 2: Internal-import domain propagation
# ---------------------------------------------------------------------------

def _build_module_name_index(all_rel_paths: List[str]) -> Dict[str, str]:
    """Build a map from possible import names to file paths.

    For a file at 'trytond/model/modelview.py', generates these keys:
      - 'trytond.model.modelview' -> 'trytond/model/modelview.py'
      - 'trytond.model'           -> 'trytond/model/modelview.py'  (if not already taken)
      - 'model.modelview'         -> ...
      - 'modelview'               -> ...

    For __init__.py files, the package name is the directory:
      - 'trytond/model/__init__.py' -> key 'trytond.model'
    """
    index: Dict[str, str] = {}

    for rel_path in all_rel_paths:
        if not rel_path.endswith(".py"):
            continue

        # Convert path to dotted module segments
        stripped = rel_path[:-3]  # remove .py
        if stripped.endswith("/__init__"):
            stripped = stripped[:-9]  # remove /__init__
        parts = stripped.replace("/", ".").split(".")

        # Register progressively shorter suffixes (longest wins on conflict)
        for i in range(len(parts)):
            key = ".".join(parts[i:])
            if key and key not in index:
                index[key] = rel_path

    return index


def extract_full_import_paths(tree: ast.Module) -> List[str]:
    """Extract full dotted import paths from an AST (not truncated to top-level).

    Returns e.g. ['trytond.model', 'trytond.pool', 'datetime'] for:
      from trytond.model import ModelView
      from trytond.pool import Pool
      import datetime
    """
    paths: List[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                paths.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                paths.append(node.module)
    return paths


def propagate_domains(
    file_domains: Dict[str, str],
    file_imports: Dict[str, List[str]],
    all_rel_paths: List[str],
    min_signals: int = 2,
) -> None:
    """Propagate domains to unclassified files via their internal import graph.

    For each file with domain='unknown', resolve its imports to files in the repo.
    If the resolved files have known (non-unknown/misc) domains, vote on the most
    common. Only assign if at least `min_signals` imports agree on the same domain.

    Modifies file_domains in place.

    Args:
        file_domains: {rel_path: domain} — modified in place.
        file_imports: {rel_path: [full_dotted_import_path, ...]} — from extract_full_import_paths.
        all_rel_paths: all file paths in the repo (for building the module name index).
        min_signals: minimum number of resolved imports that must agree. Must be > 0.
    """
    if min_signals <= 0:
        raise ValueError(f"min_signals must be positive, got {min_signals}")

    module_index = _build_module_name_index(all_rel_paths)
    _non_domain = {DOMAIN_UNKNOWN, DOMAIN_MISC}

    unresolved_files = [fp for fp, d in file_domains.items() if d in {DOMAIN_UNKNOWN, DOMAIN_MISC}]

    for rel_path in unresolved_files:
        import_paths = file_imports.get(rel_path, [])
        if not import_paths:
            continue

        domain_votes: Dict[str, int] = {}
        for imp in import_paths:
            resolved_path = module_index.get(imp)
            if resolved_path is None:
                # Walk progressively shorter prefixes:
                # 'trytond.model.modelview' → 'trytond.model' → 'trytond'
                # Pre-split once to avoid repeated str.split in the inner loop.
                parts = imp.split(".")
                for end in range(len(parts) - 1, 0, -1):
                    resolved_path = module_index.get(".".join(parts[:end]))
                    if resolved_path is not None:
                        break

            if resolved_path is None:
                continue

            dep_domain = file_domains.get(resolved_path, DOMAIN_UNKNOWN)
            if dep_domain not in _non_domain:
                domain_votes[dep_domain] = domain_votes.get(dep_domain, 0) + 1

        if not domain_votes:
            continue

        best_domain = max(domain_votes, key=domain_votes.get)
        if domain_votes[best_domain] >= min_signals:
            file_domains[rel_path] = best_domain


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

    Schema classification uses dominance logic: schema signals must represent
    a significant fraction of the file's content. A file with 2 BaseModel classes
    and 40 methods is core_logic with embedded schemas, not a schema file.
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

    # Schema classification with dominance check.
    # Effective schema signal: full-weight for BaseModel/Schema bases,
    # half-weight for dataclasses (commonly used for internal state, not just schemas).
    effective_schema = v.schema_base_count + v.dataclass_count * 0.5
    behavioral_density = v.total_function_count + v.total_class_count
    schema_dominant = (
        effective_schema >= 2 and behavioral_density <= effective_schema * 5
    ) or (
        effective_schema >= 1 and v.total_class_count <= 3 and v.total_function_count <= 2
    )
    if schema_dominant:
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


def classify_file_with_stage(file_path: Path, tree: ast.Module) -> Tuple[str, int]:
    """Like classify_file but also returns which stage fired (1–4).

    Stage 1 = filename pattern, 2 = directory pattern,
    3 = AST content analysis, 4 = count-based fallback.
    """
    cat = _classify_by_filename(file_path)
    if cat:
        return cat, 1

    cat = _classify_by_directory(file_path)
    if cat:
        return cat, 2

    cat, visitor = _classify_by_ast(tree)
    if cat:
        return cat, 3

    # Stage 4: fallback heuristic
    total_defs = visitor.total_function_count + visitor.total_class_count
    if total_defs == 0:
        return ("constants" if visitor.top_level_assign_count > 0 else "docs"), 4
    if total_defs <= 3 and visitor.top_level_assign_count == 0:
        return "utils", 4
    return "core_logic", 4


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
    domain: str = "unknown",
    duplicate_filenames: Optional[Set[str]] = None,
) -> str:
    """Emit a compact module IR line.

    Format: classifier.py | cat:core_logic | entities:12 | deps:auth,utils | churn:-

    ``module_id`` is accepted for call-site compatibility but not included in
    the output (models confused compressed stems with entity IDs).

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
    duplicate_filenames = duplicate_filenames or set()
    if (filename.lower() in common_names or filename in duplicate_filenames) and len(parts) > 1:
        # Include up to 3 parent directories for stronger disambiguation.
        context_parts = parts[-4:] if len(parts) >= 4 else parts
        display_path = "/".join(context_parts)
    else:
        display_path = filename

    parts_out = [display_path, f"cat:{category}"]
    if domain and domain != "unknown":
        parts_out.append(f"dom:{domain}")
    parts_out.append(f"entities:{entity_count}")
    if deps_internal:
        parts_out.append(f"deps:{deps_internal}")
    if churn and churn != "-":
        parts_out.append(f"churn:{churn}")
    return " | ".join(parts_out)


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


def _duplicate_filenames(modules: List[Dict[str, object]]) -> Set[str]:
    """Return bare filenames that appear more than once in a module set."""
    counts: Dict[str, int] = {}
    for mod in modules:
        fp = str(mod["file_path"])
        fname = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        counts[fname] = counts.get(fname, 0) + 1
    return {fname for fname, count in counts.items() if count > 1}


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
    duplicate_filenames = _duplicate_filenames(cat_mods)

    for mod in cat_mods:
        ec = int(mod.get("entity_count", 0))
        if ec >= INDIVIDUAL_LISTING_THRESHOLD:
            mid = module_ids.get(str(mod["file_path"]), "MD_UNKN")
            individual.append(to_module_ir_line(
                module_id=mid, file_path=str(mod["file_path"]),
                category=str(mod["category"]),
                entity_count=ec,
                deps_internal=str(mod.get("deps_internal", "")),
                domain=str(mod.get("domain", "unknown")),
                duplicate_filenames=duplicate_filenames,
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

            parts = [f"{fname} ×{len(group)}"]
            if total_ents > 0:
                parts.append(f"{total_ents} entities")
            if zero_count > 0:
                parts.append(f"{zero_count} empty")
            summary = " | ".join(parts)
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
            domain=str(mod.get("domain", "unknown")),
            duplicate_filenames=duplicate_filenames,
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

    duplicate_filenames = _duplicate_filenames(cat_mods)

    lines.append("### Modules")
    lines.append("```")
    for mod in cat_mods:
        mid = module_ids.get(str(mod["file_path"]), "MD_UNKN")
        lines.append(to_module_ir_line(
            module_id=mid, file_path=str(mod["file_path"]),
            category=category,
            entity_count=int(mod.get("entity_count", 0)),
            deps_internal=str(mod.get("deps_internal", "")),
            domain=str(mod.get("domain", "unknown")),
            duplicate_filenames=duplicate_filenames,
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
