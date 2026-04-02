"""IR generation from extracted entities at three compression levels.

Levels:
  Source   — raw source with entity boundary markers (baseline)
  Behavior — semantic-lite: opcode, entity_id, name_token, calls, flags, assigns
  Index    — structural-tag: opcode, entity_id, domain tag, category tag
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ir.stable_ids import make_pattern_id
from ir.token_count import count_tokens
from index.locator import extract_code_slice

VALID_LEVELS = {"Source", "Behavior", "Index", "all", "Behavior+Index"}
PASSTHROUGH_TOKEN_THRESHOLD_DEFAULT = 12


def kind_to_opcode(kind: str) -> str:
    from ir.stable_ids import type_prefix_for_kind
    return type_prefix_for_kind(kind)


# ---------------------------------------------------------------------------
# Level builders
# ---------------------------------------------------------------------------

def _build_source(entity: dict, repo_path: Path) -> str:
    """Raw source with entity boundary markers."""
    source = extract_code_slice(
        repo_path=repo_path,
        file_path=str(entity["file_path"]),
        start_line=int(entity["start_line"]),
        end_line=int(entity["end_line"]),
    )
    opcode = kind_to_opcode(entity["kind"])
    return f"[{opcode} {entity['id']} @{entity['file_path']}:{entity['start_line']}]\n{source}"


def _build_behavior(
    entity: dict,
    name_token: str,
    calls: List[str],
    flags: str,
    assigns: int,
    bases: List[str],
    module_category: str = "",
    module_domain: str = "",
) -> str:
    """Semantic-lite: semantic refs, flags, assignment density, inheritance hints.

    N= field removed: entity ID already carries semantic abbreviation via compact_stem.
    Sequential integers (N179) provided no signal; semantic labels (USR) were redundant.

    Fields are omitted when empty/zero to save tokens:
    - C= omitted if no calls
    - F= omitted if no flags
    - A= omitted if zero assigns
    - B= omitted if no base classes
    """
    opcode = kind_to_opcode(entity["kind"])

    # Build token with only non-empty fields (N= removed - redundant with entity ID)
    parts = [opcode, entity["id"]]

    if calls:
        parts.append(f"C={','.join(calls[:6])}")
    if flags:
        parts.append(f"F={flags}")
    if assigns > 0:
        parts.append(f"A={assigns}")
    if bases:
        parts.append(f"B={','.join(bases[:3])}")

    domain = module_domain.upper() if module_domain and module_domain != "unknown" else ""
    category = module_category[:4].upper() if module_category else ""
    if domain:
        parts.append(f"#{domain}")
    if category:
        parts.append(f"#{category}")

    return " ".join(parts)


def _build_index(entity: dict, pattern_id: str, module_category: str, module_domain: str = "",
                 structural_pattern: str = "") -> str:
    """Structural tag row: entity type, ID, pattern reference, domain tag, category tag.

    Format: MT SEND.03 →ModelSQL #HTTP #CORE
    Pattern reference (→PatternName) appears for entities that belong to a structural pattern.
    Domain tag comes first (primary orientation signal), then category.
    """
    opcode = kind_to_opcode(entity["kind"])
    cat = module_category[:4].upper() if module_category else "UNKN"
    domain = module_domain.upper() if module_domain and module_domain != "unknown" else ""

    parts = [opcode, entity['id']]

    # Add pattern reference if entity belongs to a structural pattern
    if structural_pattern:
        parts.append(f"→{structural_pattern}")

    if domain:
        parts.append(f"#{domain}")
    parts.append(f"#{cat}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# IR JSON builder
# ---------------------------------------------------------------------------

def _build_ir_json(entity: dict, level: str, name_token: str,
                   calls: List[str], flags: str, assigns: int,
                   bases: List[str],
                   pattern_id: str, param_types: List[str],
                   return_type: Optional[str], module_category: str,
                   module_domain: str = "") -> dict:
    """Structured JSON representation for programmatic consumption."""
    base = {
        "op": kind_to_opcode(entity["kind"]),
        "id": entity["id"],
        "level": level,
        "sp": [entity["start_line"], entity["end_line"]],
    }
    if level == "Behavior":
        base["n"] = name_token
        base["calls"] = calls
        base["flags"] = flags
        base["assigns"] = assigns
        base["bases"] = bases
        if module_category:
            base["category"] = module_category
        if module_domain and module_domain != "unknown":
            base["domain"] = module_domain
    elif level == "Index":
        base["pattern_id"] = pattern_id
        base["category"] = module_category
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ir_rows(
    entities: Iterable[dict],
    abbreviations: Dict[str, Dict[str, str]],
    compression_level: str = "Behavior",
    repo_path: Optional[Path] = None,
    module_categories: Optional[Dict[str, str]] = None,
    module_domains: Optional[Dict[str, str]] = None,
    passthrough_threshold: int = PASSTHROUGH_TOKEN_THRESHOLD_DEFAULT,
) -> List[dict]:
    """Generate compact IR rows at the specified compression level.

    Args:
        entities: Extracted entities with semantic metadata.
        abbreviations: Token maps for names/files/calls.
        compression_level: One of 'Source', 'Behavior', 'Index', or 'all'.
        repo_path: Repository root (required for Source extraction).
        module_categories: {file_path: category} from classifier (required for Index).
        module_domains: {file_path: domain} from classifier (required for Index domain tags).
        passthrough_threshold: Entities with <= this many source tokens emit Source for all levels.
    """
    level = compression_level.strip()
    level_upper = level.upper()
    if level_upper == "ALL":
        levels_to_generate = ["Source", "Behavior", "Index"]
    elif level_upper == "BEHAVIOR+INDEX":
        levels_to_generate = ["Behavior", "Index"]
    elif level in ("Source", "Behavior", "Index"):
        levels_to_generate = [level]
    else:
        levels_to_generate = ["Behavior", "Index"]
    module_categories = module_categories or {}
    module_domains = module_domains or {}
    name_map = abbreviations.get("entity_name", {})
    call_map = abbreviations.get("call_name", {})

    rows: List[dict] = []
    for entity in entities:
        entity_name = entity.get("qualified_name", entity["name"])
        name_token = name_map.get(entity_name, entity_name)
        semantic = entity.get("semantic", {}) or {}
        calls = [call_map.get(call, call) for call in semantic.get("calls", [])][:6]
        flags = semantic.get("flags", "")
        assigns = int(semantic.get("assigns", 0))
        bases = [call_map.get(base, base) for base in semantic.get("bases", [])][:3]

        type_sig = semantic.get("type_sig", {}) or {}
        param_types = list(type_sig.get("param_types", []))
        return_type = type_sig.get("return_type")

        pattern_id = make_pattern_id(
            kind=entity["kind"], flags=flags,
            call_count=len(calls), assigns=assigns,
        )
        module_category = module_categories.get(str(entity.get("file_path", "")), "core_logic")
        module_domain = module_domains.get(str(entity.get("file_path", "")), "")

        # Passthrough: tiny entities emit Source regardless of requested level
        is_passthrough = False
        if passthrough_threshold > 0 and repo_path is not None:
            source_text = extract_code_slice(
                repo_path=repo_path,
                file_path=str(entity["file_path"]),
                start_line=int(entity["start_line"]),
                end_line=int(entity["end_line"]),
            )
            src_tokens = count_tokens(source_text)
            is_passthrough = src_tokens <= passthrough_threshold

        for lvl in levels_to_generate:
            if is_passthrough and lvl != "Source":
                # For passthrough entities, all non-Source levels get the Source representation
                ir_text = _build_source(entity, repo_path) if repo_path else f"ENT {entity['id']}"
                ir_json = _build_ir_json(
                    entity, "Source", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "Source":
                if repo_path is None:
                    ir_text = f"ENT {entity['id']}"
                else:
                    ir_text = _build_source(entity, repo_path)
                ir_json = _build_ir_json(
                    entity, "Source", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "Behavior":
                ir_text = _build_behavior(
                    entity,
                    name_token,
                    calls,
                    flags,
                    assigns,
                    bases,
                    module_category=module_category,
                    module_domain=module_domain,
                )
                ir_json = _build_ir_json(
                    entity, "Behavior", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "Index":
                ir_text = _build_index(entity, pattern_id, module_category, module_domain)
                ir_json = _build_ir_json(
                    entity, "Index", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            else:
                continue

            rows.append({
                "entity_id": entity["id"],
                "mode": lvl,
                "ir_text": ir_text,
                "ir_json": json.dumps(ir_json, separators=(",", ":"), sort_keys=True),
            })

    return rows
