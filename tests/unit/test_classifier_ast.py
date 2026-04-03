"""Tests for AST-based file classification and domain scoring."""

import ast
from pathlib import Path

import pytest

from ir.classifier import (
    _classify_by_ast,
    _ClassificationVisitor,
    _DomainVisitor,
    _DOMAIN_THRESHOLD,
    classify_domain,
    classify_file,
    to_module_ir_line,
)


def _parse(code: str) -> ast.Module:
    return ast.parse(code)


def _visit(code: str) -> _ClassificationVisitor:
    tree = _parse(code)
    v = _ClassificationVisitor()
    v.visit(tree)
    return v


# ---------------------------------------------------------------------------
# _ClassificationVisitor
# ---------------------------------------------------------------------------

class TestClassificationVisitor:
    def test_route_decorators(self):
        """Decorated route handlers count once each."""
        v = _visit(
            "@app.get('/users')\n"
            "def get_users(): pass\n"
            "@app.post('/users')\n"
            "def create_user(): pass\n"
        )
        assert v.route_decorator_count == 2

    def test_schema_bases(self):
        v = _visit(
            "class UserSchema(BaseModel):\n"
            "    name: str\n"
            "class ItemSchema(BaseModel):\n"
            "    title: str\n"
        )
        assert v.schema_base_count == 2
        assert v.total_class_count == 2

    def test_exception_classes(self):
        """Both Exception and ValueError bases count (endswith 'Error')."""
        v = _visit(
            "class NotFoundError(Exception): pass\n"
            "class AuthError(ValueError): pass\n"
        )
        assert v.exception_class_count == 2

    def test_exception_by_name_suffix(self):
        """Classes ending with 'Error' or 'Exception' in bases."""
        v = _visit(
            "class CustomError(BaseException): pass\n"
        )
        assert v.exception_class_count == 1

    def test_dataclass_decorator(self):
        v = _visit(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Config:\n"
            "    host: str\n"
        )
        assert v.has_dataclass_decorator is True
        assert v.dataclass_count == 1
        assert v.schema_base_count == 0  # dataclass tracked separately

    def test_top_level_assigns(self):
        v = _visit("X = 1\nY = 2\nZ = 3\n")
        assert v.top_level_assign_count == 3

    def test_nested_assigns_not_counted_as_top_level(self):
        v = _visit("def f():\n    x = 1\n")
        assert v.top_level_assign_count == 0

    def test_compat_imports(self):
        v = _visit("import platform\nimport sys\nimport os\nimport ctypes\n")
        assert v.compat_signal_count >= 3

    def test_annotated_assigns(self):
        v = _visit("x: int = 1\ny: str = 'a'\n")
        assert v.top_level_assign_count == 2


# ---------------------------------------------------------------------------
# _classify_by_ast
# ---------------------------------------------------------------------------

class TestClassifyByAst:
    def test_router_heavy(self):
        tree = _parse(
            "@app.get('/a')\ndef a(): pass\n"
            "@app.post('/b')\ndef b(): pass\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat == "router"

    def test_single_route_small_file(self):
        tree = _parse(
            "@app.get('/a')\ndef a(): pass\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat == "router"

    def test_single_route_large_file_not_router(self):
        """One route in a larger file should not dominate classification."""
        tree = _parse(
            "@app.get('/a')\ndef a(): pass\n"
            "def b(): pass\n"
            "def c(): pass\n"
            "def d(): pass\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat is None

    def test_schema_heavy(self):
        tree = _parse(
            "class A(BaseModel):\n    x: int\n"
            "class B(BaseModel):\n    y: str\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat == "schema"

    def test_dataclass_only_small_file_is_schema(self):
        """A small file of dataclasses should still count as schema-like."""
        tree = _parse(
            "from dataclasses import dataclass\n"
            "@dataclass\nclass Config:\n    host: str\n"
            "@dataclass\nclass State:\n    active: bool\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat == "schema"

    def test_pure_exceptions(self):
        tree = _parse(
            "class NotFoundError(Exception): pass\n"
            "class TimeoutError(Exception): pass\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat == "exceptions"

    def test_constants_only(self):
        tree = _parse("X = 1\nY = 2\nZ = 3\n")
        cat, _ = _classify_by_ast(tree)
        assert cat == "constants"

    def test_compat_module(self):
        tree = _parse(
            "import platform\nimport sys\nimport ctypes\n"
            "if sys.platform == 'win32': pass\n"
        )
        cat, _ = _classify_by_ast(tree)
        assert cat == "compat"

    def test_docstring_only(self):
        tree = _parse('"""This module does nothing."""\n')
        cat, _ = _classify_by_ast(tree)
        assert cat == "docs"

    def test_no_signal(self):
        tree = _parse("def foo(): pass\ndef bar(): pass\ndef baz(): pass\ndef qux(): pass\n")
        cat, _ = _classify_by_ast(tree)
        assert cat is None  # no strong signal

    def test_schema_not_dominant_when_many_methods(self):
        """The MiroFish bug: 2 BaseModel classes + many methods = core_logic, not schema."""
        # Simulate report_agent.py: 2 dataclasses + 1 big class with 20 methods
        code = (
            "from dataclasses import dataclass\n"
            "@dataclass\nclass Report:\n    title: str\n"
            "@dataclass\nclass ReportSection:\n    name: str\n"
            "class ReportAgent:\n"
            + "".join(f"    def method_{i}(self): pass\n" for i in range(20))
        )
        tree = _parse(code)
        cat, _ = _classify_by_ast(tree)
        assert cat is None  # too much behavioral density for schema

    def test_schema_not_dominant_with_basemodel_and_service_logic(self):
        """Schema bases should not override obviously behavior-heavy service files."""
        code = (
            "class Report(BaseModel):\n    title: str\n"
            "class ReportSection(BaseModel):\n    name: str\n"
            "class ReportManager:\n"
            + "".join(f"    def action_{i}(self): pass\n" for i in range(18))
        )
        tree = _parse(code)
        cat, _ = _classify_by_ast(tree)
        assert cat is None

    def test_schema_dominant_with_few_methods(self):
        """Pure schema file: several BaseModel classes, minimal logic."""
        code = (
            "class User(BaseModel):\n    name: str\n"
            "class Item(BaseModel):\n    title: str\n"
            "class Order(BaseModel):\n    total: float\n"
            "def validate_order(o): pass\n"
        )
        tree = _parse(code)
        cat, _ = _classify_by_ast(tree)
        assert cat == "schema"

    def test_dataclass_half_weight(self):
        """Dataclasses contribute half-weight to schema signal."""
        # 2 dataclasses = effective 1.0, not enough alone
        code = (
            "from dataclasses import dataclass\n"
            "@dataclass\nclass Config:\n    host: str\n"
            "@dataclass\nclass State:\n    active: bool\n"
            "class Manager:\n"
            + "".join(f"    def method_{i}(self): pass\n" for i in range(15))
        )
        tree = _parse(code)
        cat, _ = _classify_by_ast(tree)
        assert cat is None  # dataclasses alone don't dominate

    def test_router_no_double_count(self):
        """@app.get('/x') should count as 1 route, not 2."""
        code = "@app.get('/a')\ndef a(): pass\n"
        tree = _parse(code)
        v = _ClassificationVisitor()
        v.visit(tree)
        assert v.route_decorator_count == 1


# ---------------------------------------------------------------------------
# classify_file (full pipeline)
# ---------------------------------------------------------------------------

class TestClassifyFile:
    def test_filename_overrides_ast(self):
        """test_foo.py should classify as 'tests' even with route decorators."""
        tree = _parse("@app.get('/a')\ndef a(): pass\n")
        assert classify_file(Path("test_foo.py"), tree) == "tests"

    def test_directory_overrides_ast(self):
        tree = _parse("def foo(): pass\n")
        assert classify_file(Path("tests/helpers/util.py"), tree) == "tests"

    def test_ast_fallback(self):
        tree = _parse(
            "@app.get('/a')\ndef a(): pass\n"
            "@app.post('/b')\ndef b(): pass\n"
        )
        assert classify_file(Path("views.py"), tree) == "router"

    def test_fallback_utils(self):
        """Few definitions, no assigns → utils."""
        tree = _parse("def helper(): pass\n")
        assert classify_file(Path("stuff.py"), tree) == "utils"

    def test_small_service_named_file_still_utils_without_other_signals(self):
        """Name alone should not force core_logic outside explicit directory hints."""
        tree = _parse("def run(): pass\n")
        assert classify_file(Path("report_agent.py"), tree) == "utils"

    def test_fallback_constants_no_defs(self):
        """No functions/classes but has assigns → constants."""
        tree = _parse("X = 1\n")
        assert classify_file(Path("stuff.py"), tree) == "constants"

    def test_fallback_docs_empty(self):
        """No definitions, no assigns → docs."""
        tree = _parse("")
        assert classify_file(Path("stuff.py"), tree) == "docs"

    def test_fallback_core_logic(self):
        """Many definitions → core_logic."""
        tree = _parse("def a(): pass\ndef b(): pass\ndef c(): pass\ndef d(): pass\n")
        assert classify_file(Path("stuff.py"), tree) == "core_logic"

    def test_services_directory_is_core_logic(self):
        tree = _parse("class ReportAgent:\n    def run(self): pass\n")
        assert classify_file(Path("services/report_agent.py"), tree) == "core_logic"

    def test_models_directory_not_unconditional_schema(self):
        """models/ directory no longer forces schema — AST analysis runs."""
        tree = _parse("def a(): pass\ndef b(): pass\ndef c(): pass\ndef d(): pass\n")
        result = classify_file(Path("models/user.py"), tree)
        assert result != "schema"  # should fall through to AST/fallback

    def test_models_directory_can_still_be_schema_from_ast(self):
        """Removing the directory rule should not prevent true schema files."""
        tree = _parse(
            "class User(BaseModel):\n    name: str\n"
            "class Item(BaseModel):\n    title: str\n"
        )
        assert classify_file(Path("models/user.py"), tree) == "schema"

    def test_filename_rule_still_overrides_services_directory(self):
        """Priority order should remain filename before directory."""
        tree = _parse("class ReportAgent:\n    def run(self): pass\n")
        assert classify_file(Path("services/test_report_agent.py"), tree) == "tests"


# ---------------------------------------------------------------------------
# _DomainVisitor
# ---------------------------------------------------------------------------

class TestDomainVisitor:
    def test_strong_import(self):
        tree = _parse("import requests\n")
        v = _DomainVisitor()
        v.visit(tree)
        assert v.domain_scores.get("http", 0) >= _DOMAIN_THRESHOLD

    def test_weak_import_alone_insufficient(self):
        tree = _parse("import json\n")
        v = _DomainVisitor()
        v.visit(tree)
        assert v.domain_scores.get("parse", 0) < _DOMAIN_THRESHOLD

    def test_two_weak_imports_reach_threshold(self):
        tree = _parse("import json\nimport xml\n")
        v = _DomainVisitor()
        v.visit(tree)
        assert v.domain_scores.get("parse", 0) >= _DOMAIN_THRESHOLD

    def test_from_import(self):
        tree = _parse("from sqlalchemy import Column\n")
        v = _DomainVisitor()
        v.visit(tree)
        assert v.domain_scores.get("db", 0) >= _DOMAIN_THRESHOLD


# ---------------------------------------------------------------------------
# classify_domain
# ---------------------------------------------------------------------------

class TestClassifyDomain:
    def test_filename_pattern(self):
        tree = _parse("")
        assert classify_domain(Path("auth.py"), tree) == "auth"

    def test_directory_pattern(self):
        tree = _parse("")
        assert classify_domain(Path("crypto/utils.py"), tree) == "crypto"

    def test_import_based(self):
        tree = _parse("import requests\n")
        assert classify_domain(Path("client.py"), tree) == "http"  # filename wins

    def test_import_based_no_filename_match(self):
        tree = _parse("import requests\n")
        assert classify_domain(Path("stuff.py"), tree) == "http"

    def test_unknown_fallback(self):
        tree = _parse("x = 1\n")
        assert classify_domain(Path("stuff.py"), tree) == "unknown"


# ---------------------------------------------------------------------------
# to_module_ir_line
# ---------------------------------------------------------------------------

class TestModuleIrLine:
    def test_basic(self):
        line = to_module_ir_line("ID", "src/router.py", "router", 5, "auth,db")
        assert "router.py" in line
        assert "cat:router" in line
        assert "entities:5" in line
        assert "deps:auth,db" in line

    def test_common_name_includes_parent(self):
        """Common filenames like utils.py should include parent dirs."""
        line = to_module_ir_line("ID", "auth/strategy/utils.py", "utils", 3, "")
        assert "strategy/utils.py" in line

    def test_empty_deps(self):
        line = to_module_ir_line("ID", "foo.py", "core_logic", 1, "")
        assert "deps:-" in line

    def test_unique_filename_no_parent(self):
        line = to_module_ir_line("ID", "src/my_unique_module.py", "core_logic", 10, "auth")
        assert line.startswith("my_unique_module.py")
