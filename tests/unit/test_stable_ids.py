"""Tests for stable ID generation."""

import pytest
from ir.stable_ids import compact_stem, make_entity_base_id, make_module_base_id, make_stable_id, parse_stable_id, type_prefix_for_kind


class TestCompactStem:
    """Tests for the compact_stem vowel-stripping algorithm."""

    def test_short_words_preserved(self):
        """Words <= 4 chars are kept intact."""
        assert compact_stem("send") == "SEND"
        assert compact_stem("user") == "USER"
        assert compact_stem("get") == "GET"
        assert compact_stem("id") == "ID"

    def test_vowel_stripping(self):
        """Longer words have vowels stripped after first char."""
        assert compact_stem("login") == "LGN"
        assert compact_stem("required") == "RQRD"
        assert compact_stem("response") == "RSPNS"
        assert compact_stem("Blueprint") == "BLPRNT"

    def test_max_12_chars(self):
        """Stems are truncated to 12 characters max."""
        result = compact_stem("_split_blueprint_path")
        assert result == "SPLTBLPRNTPT"
        assert len(result) == 12

        result = compact_stem("InvalidPasswordException")
        assert result == "INVLDPSSWRDX"
        assert len(result) == 12

    def test_non_alpha_stripped(self):
        """Non-alphanumeric characters are removed."""
        assert compact_stem("__init__") == "INIT"
        assert compact_stem("_private_method") == "PRVTMTHD"

    def test_empty_returns_unkn(self):
        """Empty or all-special-char input returns UNKN."""
        assert compact_stem("") == "UNKN"
        assert compact_stem("___") == "UNKN"


class TestMakeEntityBaseId:
    """Tests for entity base ID generation."""

    def test_extracts_leaf_name(self):
        """Uses the leaf (last part) of qualified name."""
        assert make_entity_base_id("function", "module.submodule.my_func") == "MYFNC"
        assert make_entity_base_id("method", "MyClass.my_method") == "MYMTHD"

    def test_simple_name(self):
        """Works with unqualified names."""
        assert make_entity_base_id("function", "authenticate") == "ATHNTCT"


class TestStableIdParsing:
    """Tests for stable ID construction and parsing."""

    def test_make_stable_id(self):
        """Constructs full stable ID from type and display ID."""
        assert make_stable_id("async_method", "RDTKN.03") == "AMT.RDTKN.03"
        assert make_stable_id("function", "AUTH") == "FN.AUTH"
        assert make_stable_id("class", "USER") == "CLS.USER"

    def test_parse_stable_id(self):
        """Parses stable ID back into components."""
        assert parse_stable_id("AMT.RDTKN.03") == ("AMT", "RDTKN.03")
        assert parse_stable_id("FN.AUTH") == ("FN", "AUTH")
        assert parse_stable_id("CLS.USER.02") == ("CLS", "USER.02")


class TestMakeModuleBaseId:
    def test_strips_compound_typescript_suffix(self):
        assert make_module_base_id("src/types/api.d.ts") == "API"


class TestRustKindPrefixes:
    def test_rust_kind_prefixes(self):
        assert type_prefix_for_kind("struct") == "ST"
        assert type_prefix_for_kind("enum") == "EN"
        assert type_prefix_for_kind("trait") == "TRT"
        assert type_prefix_for_kind("trait_method") == "TMT"
        assert type_prefix_for_kind("constant") == "CST"


class TestTypeScriptKindPrefixes:
    def test_typescript_kind_prefixes(self):
        assert type_prefix_for_kind("interface") == "IFC"
        assert type_prefix_for_kind("type_alias") == "TYP"
        assert type_prefix_for_kind("namespace") == "NS"
