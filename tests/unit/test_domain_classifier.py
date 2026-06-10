"""Tests for domain classification — new application-structure domains,
submodule import matching, scoring edge cases, and signal priority."""

import ast
from pathlib import Path

import pytest

from ir.classifier import (
    DOMAINS,
    _DomainVisitor,
    _DOMAIN_FILE_PATTERNS,
    _DOMAIN_IMPORTS_STRONG,
    _DOMAIN_IMPORTS_WEAK,
    _DOMAIN_SUBMODULE_STRONG,
    _STRONG_SIGNAL_SCORE,
    _WEAK_SIGNAL_SCORE,
    _DOMAIN_THRESHOLD,
    classify_domain,
    classify_domain_decision,
    infer_entity_domain_scores,
    propagate_domains,
)


def _parse(src: str) -> ast.Module:
    return ast.parse(src)


# ---------------------------------------------------------------------------
# DOMAINS tuple sanity
# ---------------------------------------------------------------------------

class TestDomainsTupleSanity:
    """Ensure the DOMAINS constant is well-formed."""

    def test_unknown_is_last(self):
        assert DOMAINS[-1] == "unknown"

    def test_misc_is_second_to_last(self):
        assert DOMAINS[-2] == "misc"

    def test_no_duplicates(self):
        assert len(DOMAINS) == len(set(DOMAINS))

    def test_new_domains_present(self):
        """All application-structure domains we added are in DOMAINS."""
        for d in ("ui", "validation", "i18n", "task", "event", "log",
                   "mail", "media", "admin", "cache"):
            assert d in DOMAINS, f"{d} missing from DOMAINS"

    def test_original_domains_still_present(self):
        for d in ("http", "auth", "crypto", "db", "fs", "cli",
                   "async", "parse", "net"):
            assert d in DOMAINS


# ---------------------------------------------------------------------------
# Filename / directory pattern matching for new domains
# ---------------------------------------------------------------------------

class TestNewDomainFilePatterns:
    """classify_domain should resolve new domains from filenames and dirs."""

    @pytest.mark.parametrize("path,expected", [
        # UI
        ("templates.py", "ui"),
        ("views.py", "ui"),
        ("forms.py", "ui"),
        ("widgets.py", "ui"),
        ("renderer.py", "ui"),
        ("components.py", "ui"),
        # Validation
        ("validators.py", "validation"),
        ("validation.py", "validation"),
        # i18n
        ("locale.py", "i18n"),
        ("translations.py", "i18n"),
        ("i18n.py", "i18n"),
        ("l10n.py", "i18n"),
        # Task/job
        ("tasks.py", "task"),
        ("workers.py", "task"),
        ("jobs.py", "task"),
        ("scheduler.py", "task"),
        ("cron.py", "task"),
        # Event/signal
        ("signals.py", "event"),
        ("events.py", "event"),
        ("dispatch.py", "event"),
        ("hooks.py", "event"),
        ("listeners.py", "event"),
        ("handlers.py", "event"),
        # Logging
        ("logging.py", "log"),
        ("logger.py", "log"),
        ("metrics.py", "log"),
        # Mail
        ("email.py", "mail"),
        ("mail.py", "mail"),
        ("notifications.py", "mail"),
        ("smtp.py", "mail"),
        # Media
        ("images.py", "media"),
        ("thumbnails.py", "media"),
        ("media.py", "media"),
        ("avatar.py", "media"),
        # Admin
        ("admin.py", "admin"),
        ("management.py", "admin"),
        # Cache
        ("cache.py", "cache"),
        ("caching.py", "cache"),
    ])
    def test_filename_resolves_to_domain(self, path, expected):
        assert classify_domain(Path(path), None) == expected

    @pytest.mark.parametrize("path,expected", [
        ("templates/base.py", "ui"),
        ("events/user_created.py", "event"),
        ("locale/en/strings.py", "i18n"),
        ("admin/dashboard.py", "admin"),
        ("cache/backends.py", "cache"),
        ("tasks/cleanup.py", "task"),
    ])
    def test_directory_resolves_to_domain(self, path, expected):
        """Directory part should trigger domain even if filename is generic."""
        tree = _parse("")
        assert classify_domain(Path(path), tree) == expected


# ---------------------------------------------------------------------------
# Strong import signals for new domains
# ---------------------------------------------------------------------------

class TestNewDomainStrongImports:
    """A single strong import should be enough to classify the domain."""

    @pytest.mark.parametrize("import_stmt,expected", [
        # UI
        ("import jinja2", "ui"),
        ("import wtforms", "ui"),
        ("from mako import template", "ui"),
        ("from gi.repository import Gtk", "ui"),
        # Validation
        ("import cerberus", "validation"),
        ("import marshmallow", "validation"),
        ("import pydantic", "validation"),
        # i18n
        ("import babel", "i18n"),
        ("import gettext", "i18n"),
        # Task
        ("import celery", "task"),
        ("import dramatiq", "task"),
        ("import huey", "task"),
        ("from rq import Queue", "task"),
        ("from apscheduler import schedulers", "task"),
        # Event
        ("import blinker", "event"),
        # Logging
        ("import sentry_sdk", "log"),
        ("import structlog", "log"),
        ("import loguru", "log"),
        ("from opentelemetry import trace", "log"),
        # Mail
        ("import smtplib", "mail"),
        ("import sendgrid", "mail"),
        # Media
        ("from PIL import Image", "media"),
        ("import wand", "media"),
        ("import imageio", "media"),
        # Cache
        ("import cachetools", "cache"),
        ("import diskcache", "cache"),
        ("import pymemcache", "cache"),
    ])
    def test_strong_import_classifies_domain(self, import_stmt, expected):
        tree = _parse(import_stmt + "\n")
        # Use a neutral filename so only the import matters
        assert classify_domain(Path("stuff.py"), tree) == expected


# ---------------------------------------------------------------------------
# Weak import signals
# ---------------------------------------------------------------------------

class TestWeakImportSignals:
    """Weak imports alone shouldn't classify; two weak signals should."""

    @pytest.mark.parametrize("import_stmt", [
        "import logging",
        "import redis",
        "import email",
        "import locale",
    ])
    def test_single_weak_import_not_enough(self, import_stmt):
        """A single weak import below threshold → misc, not a domain."""
        tree = _parse(import_stmt + "\n")
        assert classify_domain(Path("stuff.py"), tree) == "misc"

    def test_two_weak_logging_imports_reach_threshold(self):
        """Two separate weak imports for the same domain should qualify."""
        # logging (weak) appears twice via import + from-import
        tree = _parse("import logging\nfrom logging import getLogger\n")
        v = _DomainVisitor()
        v.visit(tree)
        assert v.domain_scores.get("log", 0) >= _DOMAIN_THRESHOLD

    def test_two_weak_fs_imports_reach_threshold(self):
        tree = _parse("import pathlib\nimport shutil\n")
        assert classify_domain(Path("stuff.py"), tree) == "fs"

    def test_two_weak_async_imports_reach_threshold(self):
        tree = _parse("import asyncio\nimport threading\n")
        assert classify_domain(Path("stuff.py"), tree) == "async"


# ---------------------------------------------------------------------------
# Submodule import matching (Django, Flask, Tryton)
# ---------------------------------------------------------------------------

class TestSubmoduleImports:
    """Dotted import paths should match framework-specific submodule signals."""

    @pytest.mark.parametrize("import_stmt,expected", [
        # Django submodules
        ("from django.forms import Form", "ui"),
        ("from django.template import loader", "ui"),
        ("from django.views import View", "ui"),
        ("from django.shortcuts import render", "ui"),
        ("from django.db import models", "db"),
        ("from django.db.models import Q", "db"),
        ("from django.contrib.admin import ModelAdmin", "admin"),
        ("from django.contrib.auth import authenticate", "auth"),
        ("from django.core.mail import send_mail", "mail"),
        ("from django.core.cache import cache", "cache"),
        ("from django.core.validators import validate_email", "validation"),
        ("from django.dispatch import receiver", "event"),
        ("from django.utils.translation import gettext_lazy", "i18n"),
        ("from django.core.management import BaseCommand", "admin"),
        ("from django.core.files import File", "fs"),
        ("from django.http import HttpResponse", "http"),
        ("from django.urls import path", "http"),
        # Flask submodules
        ("from flask.views import MethodView", "ui"),
        ("from flask.templating import render_template", "ui"),
        ("from gi.repository import Gtk", "ui"),
        # Tryton submodules
        ("from trytond.model import ModelView", "db"),
        ("from trytond.pool import Pool", "db"),
        ("from trytond.wizard import Wizard", "ui"),
        ("from trytond.report import Report", "ui"),
        ("from trytond.transaction import Transaction", "db"),
    ])
    def test_submodule_import_classifies(self, import_stmt, expected):
        tree = _parse(import_stmt + "\n")
        assert classify_domain(Path("stuff.py"), tree) == expected

    def test_submodule_deeper_nesting_still_matches(self):
        """django.db.models.fields should still match django.db prefix."""
        tree = _parse("from django.db.models.fields import CharField\n")
        assert classify_domain(Path("stuff.py"), tree) == "db"

    def test_submodule_takes_priority_over_top_level(self):
        """django.dispatch should resolve to 'event', not fall through
        to a potential top-level 'django' match."""
        tree = _parse("from django.dispatch import Signal\n")
        result = classify_domain(Path("stuff.py"), tree)
        assert result == "event"

    def test_unrecognized_django_submodule_no_match(self):
        """An unknown django.* submodule that's not in the map should not
        match anything (django alone is not in strong or weak) → misc."""
        tree = _parse("from django.conf import settings\n")
        assert classify_domain(Path("stuff.py"), tree) == "misc"


# ---------------------------------------------------------------------------
# Signal priority: filename > directory > imports
# ---------------------------------------------------------------------------

class TestSignalPriority:
    """Filename should beat directory, directory should beat imports."""

    def test_filename_beats_imports(self):
        """File named cache.py should be 'cache' even with db imports."""
        tree = _parse("import sqlalchemy\n")
        assert classify_domain(Path("cache.py"), tree) == "cache"

    def test_filename_beats_directory(self):
        """File named admin.py inside a cache/ dir should be 'admin'."""
        tree = _parse("")
        assert classify_domain(Path("cache/admin.py"), tree) == "admin"

    def test_directory_beats_imports(self):
        """File in templates/ dir should be 'ui' even with crypto imports."""
        tree = _parse("import cryptography\n")
        assert classify_domain(Path("templates/helpers.py"), tree) == "ui"

    def test_imports_used_when_no_path_signals(self):
        """Generic path, only imports determine domain."""
        tree = _parse("import celery\n")
        assert classify_domain(Path("src/core/processor.py"), tree) == "task"


# ---------------------------------------------------------------------------
# Competing signals / tie-breaking
# ---------------------------------------------------------------------------

class TestCompetingSignals:
    """When multiple domains score above threshold, highest score wins."""

    def test_strongest_import_wins(self):
        """Two strong signals: the one with more imports wins."""
        tree = _parse(
            "import sqlalchemy\nfrom sqlalchemy import Column\n"
            "import smtplib\n"
        )
        # db has 2 strong (score=4), mail has 1 strong (score=2)
        assert classify_domain(Path("stuff.py"), tree) == "db"

    def test_strong_beats_weak_accumulation(self):
        """One strong import (score=2) should tie or beat two weak (score=2).
        With a tie the max() picks one deterministically."""
        tree = _parse(
            "import celery\n"       # task: strong (2)
            "import pathlib\n"       # fs: weak (1)
            "import shutil\n"        # fs: weak (1) → fs total = 2
        )
        result = classify_domain(Path("stuff.py"), tree)
        # Both task and fs reach threshold=2. max() with dict ordering
        # picks whichever scores higher; they're equal so either is acceptable
        assert result in ("task", "fs")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_none_tree_with_domain_filename(self):
        """classify_domain should work with tree=None if filename matches."""
        assert classify_domain(Path("signals.py"), None) == "event"
        assert classify_domain(Path("cache.py"), None) == "cache"

    def test_none_tree_with_generic_filename(self):
        """tree=None and no filename signal → unknown (indexer failure, not misc)."""
        assert classify_domain(Path("stuff.py"), None) == "unknown"

    def test_empty_source_file(self):
        """Valid empty tree → misc (all signals tried, none applied)."""
        tree = _parse("")
        assert classify_domain(Path("stuff.py"), tree) == "misc"

    def test_relative_import_no_crash(self):
        """from . import X has module=None; should not crash. No signals → misc."""
        tree = _parse("from . import utils\n")
        assert classify_domain(Path("stuff.py"), tree) == "misc"

    def test_deeply_nested_path_matches_first_directory(self):
        """If multiple directory parts match, the first one encountered wins."""
        tree = _parse("")
        # admin/ appears before cache/ in the path parts
        result = classify_domain(Path("admin/cache/backend.py"), tree)
        assert result == "admin"

    def test_case_sensitive_filename(self):
        """Filename matching is case-insensitive via .stem.lower()."""
        assert classify_domain(Path("ADMIN.py"), None) == "admin"
        assert classify_domain(Path("Cache.py"), None) == "cache"

    def test_case_sensitive_directory(self):
        """Directory matching is case-insensitive via .lower()."""
        tree = _parse("")
        assert classify_domain(Path("Templates/helper.py"), tree) == "ui"

    def test_import_star_doesnt_crash(self):
        """from module import * should not crash the visitor."""
        tree = _parse("from celery import *\n")
        assert classify_domain(Path("stuff.py"), tree) == "task"

    def test_submodule_exact_match_vs_prefix(self):
        """'django.db' should match both 'from django.db import X'
        and 'from django.db.models import X'."""
        tree1 = _parse("from django.db import connection\n")
        tree2 = _parse("from django.db.models import Model\n")
        assert classify_domain(Path("stuff.py"), tree1) == "db"
        assert classify_domain(Path("stuff.py"), tree2) == "db"

    def test_submodule_no_partial_string_match(self):
        """'trytond.ir' should not accidentally match 'trytond.irc' (if it existed).
        Prefix matching requires an exact segment boundary."""
        tree = _parse("from trytond.irc import something\n")
        # 'trytond.irc' should NOT match 'trytond.ir' prefix because
        # the check is `startswith(prefix + ".")` — 'trytond.irc' does not
        # start with 'trytond.ir.'
        result = classify_domain(Path("stuff.py"), tree)
        assert result != "db"  # should NOT match trytond.ir → db

    def test_multiple_submodule_imports_accumulate(self):
        """Two imports from different django submodules of the same domain
        should accumulate score."""
        tree = _parse(
            "from django.forms import Form\n"
            "from django.views import View\n"
        )
        v = _DomainVisitor()
        v.visit(tree)
        # Each is a strong signal (score=2), so ui should get score=4
        assert v.domain_scores.get("ui", 0) == 4

    def test_mixed_submodule_and_toplevel_imports(self):
        """Submodule and top-level imports for different domains both score."""
        tree = _parse(
            "from django.forms import Form\n"   # ui via submodule
            "import celery\n"                     # task via top-level
        )
        v = _DomainVisitor()
        v.visit(tree)
        assert v.domain_scores.get("ui", 0) >= _DOMAIN_THRESHOLD
        assert v.domain_scores.get("task", 0) >= _DOMAIN_THRESHOLD


# ---------------------------------------------------------------------------
# Shared resolver refinements
# ---------------------------------------------------------------------------

class TestSharedResolverRefinements:
    """Category, self-package, and propagation rules should refine misc."""

    def test_router_category_defaults_to_http(self):
        tree = _parse(
            "def index():\n    return 'ok'\n"
            "def update():\n    return 'done'\n"
        )
        assert classify_domain(Path("blog.py"), tree, category="router") == "http"

    def test_schema_category_defaults_to_validation(self):
        tree = _parse(
            "class UserSchema(BaseModel):\n    name: str\n"
        )
        assert classify_domain(Path("models.py"), tree, category="schema") == "validation"

    def test_self_package_hint_can_classify_framework_source(self):
        tree = _parse(
            "class Flask:\n"
            "    def handle_http_exception(self, error):\n"
            "        return error\n"
        )
        assert (
            classify_domain(
                Path("src/flask/app.py"),
                tree,
                internal_roots={"flask"},
            )
            == "http"
        )

    def test_self_package_hint_requires_known_internal_root(self):
        tree = _parse("class Flask:\n    pass\n")
        assert (
            classify_domain(
                Path("src/flask/app.py"),
                tree,
                internal_roots={"myapp"},
            )
            == "misc"
        )

    def test_name_signals_can_recover_filesystem_managers(self):
        tree = _parse(
            "class ProjectManager:\n"
            "    def _get_project_dir(self):\n"
            "        return 'dir'\n"
            "    def save_file_to_project(self):\n"
            "        return True\n"
            "    def get_project_files(self):\n"
            "        return []\n"
        )
        assert classify_domain(Path("project_manager.py"), tree) == "fs"

    def test_propagation_can_upgrade_misc_files(self):
        file_domains = {
            "flask/app.py": "misc",
            "flask/helpers.py": "http",
            "flask/response.py": "http",
        }
        file_imports = {
            "flask/app.py": ["flask.helpers", "flask.response"],
            "flask/helpers.py": [],
            "flask/response.py": [],
        }
        all_rel_paths = list(file_domains)

        propagate_domains(file_domains, file_imports, all_rel_paths)

        assert file_domains["flask/app.py"] == "http"

    def test_decision_marks_category_hint_as_weak(self):
        tree = _parse("def index():\n    return 'ok'\n")
        decision = classify_domain_decision(Path("blog.py"), tree, category="router")
        assert decision.domain == "http"
        assert decision.source == "category_hint"
        assert decision.strength == "weak"

    def test_decision_marks_filename_match_as_strong(self):
        decision = classify_domain_decision(Path("auth.py"), None)
        assert decision.domain == "auth"
        assert decision.source == "filename"
        assert decision.strength == "strong"

    def test_entity_domain_scores_use_call_names(self):
        scores = infer_entity_domain_scores(
            entity_name="ProjectManager",
            qualified_name="app.project.ProjectManager.save_file",
            call_names=["read_path", "write_file"],
        )
        assert scores["fs"] >= 3


# ---------------------------------------------------------------------------
# Original domains still work with new code paths
# ---------------------------------------------------------------------------

class TestOriginalDomainsUnchanged:
    """Regression: original infrastructure domains still classify correctly."""

    def test_http_from_filename(self):
        assert classify_domain(Path("requests.py"), None) == "http"

    def test_auth_from_filename(self):
        assert classify_domain(Path("auth.py"), None) == "auth"

    def test_db_from_import(self):
        tree = _parse("import sqlalchemy\n")
        assert classify_domain(Path("stuff.py"), tree) == "db"

    def test_crypto_from_import(self):
        tree = _parse("import cryptography\n")
        assert classify_domain(Path("stuff.py"), tree) == "crypto"

    def test_cli_from_import(self):
        tree = _parse("import argparse\n")
        assert classify_domain(Path("stuff.py"), tree) == "cli"

    def test_async_from_two_weak(self):
        tree = _parse("import asyncio\nimport threading\n")
        assert classify_domain(Path("stuff.py"), tree) == "async"

    def test_net_from_import(self):
        tree = _parse("import socket\n")
        assert classify_domain(Path("stuff.py"), tree) == "net"

    def test_parse_from_import(self):
        tree = _parse("import yaml\n")
        assert classify_domain(Path("stuff.py"), tree) == "parse"

    def test_fs_from_directory(self):
        tree = _parse("")
        assert classify_domain(Path("storage/backend.py"), tree) == "fs"

    def test_misc_fallback(self):
        """Valid tree with no domain signals → misc, not unknown."""
        tree = _parse("x = 1\n")
        assert classify_domain(Path("stuff.py"), tree) == "misc"


# ---------------------------------------------------------------------------
# Domain report — classifier health check across real source files
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def domain_report():
    """Walk the CodeIR project once, classify every .py, return the report.

    Scoped to the module so the filesystem walk and parse only happen once
    regardless of how many TestDomainReport tests consume this fixture.
    """
    repo_root = Path(__file__).resolve().parents[2]
    skip_dirs = {"__pycache__", ".git", ".codeir", "node_modules"}
    by_domain: dict = {}
    parse_errors = 0
    total = 0

    for py_file in sorted(repo_root.rglob("*.py")):
        if any(part in skip_dirs for part in py_file.parts):
            continue
        if "tests/_local" in py_file.as_posix():
            continue

        total += 1
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            parse_errors += 1
            by_domain["unknown"] = by_domain.get("unknown", 0) + 1
            continue

        domain = classify_domain(py_file.relative_to(repo_root), tree)
        by_domain[domain] = by_domain.get(domain, 0) + 1

    return {"by_domain": by_domain, "parse_errors": parse_errors, "total": total}


class TestDomainReport:
    """Classify every .py file in the CodeIR project itself and report the
    domain distribution.  Fails if 'unknown' (a parse-failure sentinel) appears
    on more than UNKNOWN_THRESHOLD percent of files.

    Run with -s to see the full domain breakdown printed to stdout.
    """

    UNKNOWN_THRESHOLD_PCT = 2.0
    MISC_CEILING_PCT = 60.0

    def test_unknown_rate_is_low(self, domain_report):
        """unknown% must stay below threshold — it signals indexer failures."""
        total = domain_report["total"]
        by_domain = domain_report["by_domain"]
        unknown = by_domain.get("unknown", 0)

        print(f"\n--- Domain Report ({total} files) ---")
        for domain, count in sorted(by_domain.items(), key=lambda x: -x[1]):
            pct = count * 100.0 / total if total else 0
            bar = "!" if domain == "unknown" else ("~" if domain == "misc" else " ")
            print(f"  {bar} {domain:<12} {count:4d}  ({pct:.1f}%)")
        print(f"  parse errors: {domain_report['parse_errors']}")
        print("------------------------------------")

        unknown_pct = unknown * 100.0 / total if total else 0
        assert unknown_pct <= self.UNKNOWN_THRESHOLD_PCT, (
            f"'unknown' rate {unknown_pct:.1f}% exceeds {self.UNKNOWN_THRESHOLD_PCT}% — "
            f"{unknown}/{total} files. 'unknown' should only appear on parse failures."
        )

    def test_misc_rate_is_reasonable(self, domain_report):
        """misc% is informational — flag if it exceeds a generous ceiling.

        A very high misc rate suggests the domain vocabulary is too narrow.
        """
        total = domain_report["total"]
        misc = domain_report["by_domain"].get("misc", 0)

        misc_pct = misc * 100.0 / total if total else 0
        assert misc_pct <= self.MISC_CEILING_PCT, (
            f"'misc' rate {misc_pct:.1f}% exceeds {self.MISC_CEILING_PCT}% — "
            f"{misc}/{total} files. Consider expanding the domain vocabulary in ir/classifier.py."
        )
