"""Deterministic stable IDs and pattern fingerprints for indexed entities."""

from __future__ import annotations

import hashlib
import re


def compact_stem(value: str) -> str:
    """Generate compact identifier via vowel-stripping.

    Used by both stable ID generation and abbreviation map building.

    For short words (<=4 chars), preserves the word intact to avoid
    ambiguous abbreviations (e.g., 'send' stays 'SEND', not 'SNDX').
    For longer words, strips vowels after the first character.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", value)
    if not cleaned:
        return "UNKN"
    # Short words: keep intact for clarity
    if len(cleaned) <= 4:
        return cleaned.upper()
    # Longer words: vowel-strip for compression, max 12 chars
    first = cleaned[0]
    rest = re.sub(r"[AEIOUaeiou]", "", cleaned[1:])
    return (first + rest).upper()[:12]


def type_prefix_for_kind(kind: str) -> str:
    """Map entity kind to type prefix (FN, AFN, MT, AMT, CLS, ST, EN, TR, CON, STC, MD)."""
    return {
        "function": "FN",
        "async_function": "AFN",
        "method": "MT",
        "async_method": "AMT",
        "class": "CLS",
        "struct": "ST",
        "enum": "EN",
        "trait": "TR",
        "constant": "CON",
        "static": "STC",
        "module": "MD",
    }.get(kind, "ENT")


def make_entity_base_id(kind: str, qualified_name: str) -> str:
    """Build deterministic base ID without collision suffix.

    Format: STEM only (e.g., AUTH, USER, SEND, VLDTPSWD).
    Type prefix is NOT included - it's already on the row as the type field.
    Short names (<=4 chars) are preserved; longer names are vowel-stripped.

    The full stable ID is reconstructed as TYPE.STEM.SUFFIX when needed.
    """
    leaf = qualified_name.rsplit(".", 1)[-1]
    return compact_stem(leaf)


def make_stable_id(kind: str, display_id: str) -> str:
    """Reconstruct full stable ID from type and display ID.

    Example: make_stable_id("async_method", "RDTKN.03") -> "AMT.RDTKN.03"
    """
    return f"{type_prefix_for_kind(kind)}.{display_id}"


def parse_stable_id(stable_id: str) -> tuple[str, str]:
    """Parse a stable ID into (type_prefix, display_id).

    Example: parse_stable_id("AMT.RDTKN.03") -> ("AMT", "RDTKN.03")
    """
    type_prefix, _, display_id = stable_id.partition(".")
    return type_prefix, display_id


def make_module_base_id(file_path: str) -> str:
    """Build deterministic base module ID from a file path.

    Format: STEM only (e.g., SESS for sessions.py, MNGR for manager.py).
    Type prefix MD is NOT included - it's already on the row as the type field.
    Uses parent directory name for __init__.py and mod.rs to avoid collisions.
    """
    parts = file_path.replace("\\", "/").rsplit("/", 1)
    filename = parts[-1] if len(parts) > 1 else parts[0]
    stem = filename.rsplit(".", 1)[0]
    if stem in ("__init__", "mod"):
        # authentication/__init__.py -> ATHN instead of INIT
        # handlers/mod.rs -> HNDLRS instead of MOD
        parent = parts[0] if len(parts) > 1 else stem
        stem = parent.rsplit("/", 1)[-1] if "/" in parent else parent
    return compact_stem(stem)


def make_pattern_id(kind: str, flags: str, call_count: int, assigns: int) -> str:
    """Generate a deterministic pattern fingerprint from structural signals.

    Two entities with the same pattern_id share structural characteristics
    (same kind, same control flow flags, similar call/assign density).
    """
    basis = f"{kind}|F={flags}|C={call_count}|A={assigns}"
    return f"P{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:6].upper()}"
