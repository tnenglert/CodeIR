"""Tests for IR compression and format conformance."""

import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ir.compressor import _build_behavior, _build_index, kind_to_opcode


class TestKindToOpcode:
    """Tests for entity kind to opcode mapping."""

    def test_standard_kinds(self):
        """Standard kinds map to correct opcodes."""
        assert kind_to_opcode("function") == "FN"
        assert kind_to_opcode("async_function") == "AFN"
        assert kind_to_opcode("method") == "MT"
        assert kind_to_opcode("async_method") == "AMT"
        assert kind_to_opcode("class") == "CLS"
        assert kind_to_opcode("interface") == "IFC"
        assert kind_to_opcode("type_alias") == "TYP"
        assert kind_to_opcode("enum") == "ENM"
        assert kind_to_opcode("namespace") == "NS"
        assert kind_to_opcode("constant") == "CST"

    def test_unknown_kind(self):
        """Unknown kinds map to ENT."""
        assert kind_to_opcode("unknown") == "ENT"
        assert kind_to_opcode("") == "ENT"


class TestBuildBehavior:
    """Tests for Behavior IR format conformance."""

    def test_calls_limited_to_six(self):
        """C= field contains at most 6 calls."""
        entity = {"kind": "function", "id": "TEST"}
        calls = ["a", "b", "c", "d", "e", "f", "g", "h"]  # 8 calls

        result = _build_behavior(entity, "test", calls, "", 0, [])

        assert "C=a,b,c,d,e,f" in result
        assert "g" not in result
        assert "h" not in result

    def test_flags_preserved(self):
        """F= flags are passed through as-is (sorting happens in locator)."""
        entity = {"kind": "function", "id": "TEST"}

        # Flags come pre-sorted from locator
        result = _build_behavior(entity, "test", [], "AEIR", 0, [])

        assert "F=AEIR" in result

    def test_empty_calls_omitted(self):
        """C= field is omitted when no calls."""
        entity = {"kind": "function", "id": "TEST"}

        result = _build_behavior(entity, "test", [], "R", 0, [])

        assert "C=" not in result

    def test_empty_flags_omitted(self):
        """F= field is omitted when no flags."""
        entity = {"kind": "function", "id": "TEST"}

        result = _build_behavior(entity, "test", ["foo"], "", 0, [])

        assert "F=" not in result

    def test_zero_assigns_omitted(self):
        """A= field is omitted when zero assignments."""
        entity = {"kind": "function", "id": "TEST"}

        result = _build_behavior(entity, "test", [], "R", 0, [])

        assert "A=" not in result

    def test_assigns_included_when_nonzero(self):
        """A= field is included when assignments > 0."""
        entity = {"kind": "function", "id": "TEST"}

        result = _build_behavior(entity, "test", [], "R", 5, [])

        assert "A=5" in result

    def test_domain_tag_not_truncated(self):
        """Domain tags are uppercased but not truncated."""
        entity = {"kind": "function", "id": "TEST"}

        result = _build_behavior(entity, "test", [], "", 0, [],
                                  module_category="core_logic",
                                  module_domain="crypto")

        assert "#CRYPTO" in result  # Not #CRYP

    def test_category_tag_truncated_to_four(self):
        """Category tags are truncated to 4 chars."""
        entity = {"kind": "function", "id": "TEST"}

        result = _build_behavior(entity, "test", [], "", 0, [],
                                  module_category="exceptions")

        assert "#EXCE" in result  # Not #EXCEPTIONS


class TestBuildIndex:
    """Tests for Index IR format conformance."""

    def test_basic_format(self):
        """Index format is TYPE ID [#DOMAIN] #CATE."""
        entity = {"kind": "method", "id": "AUTH"}

        result = _build_index(entity, "P123456", "core_logic", "auth")

        assert result == "MT AUTH #AUTH #CORE"

    def test_domain_omitted_when_unknown(self):
        """Domain tag is omitted when unknown."""
        entity = {"kind": "function", "id": "UTIL"}

        result = _build_index(entity, "P123456", "utils", "unknown")

        assert result == "FN UTIL #UTIL"
        assert "#UNKNOWN" not in result
