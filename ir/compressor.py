"""IR generation from extracted entities at four compression levels.

Levels:
  L0 — raw source with entity boundary markers (baseline)
  L1 — semantic-lite: opcode, entity_id, name_token, calls, flags, assigns
  L2 — aggressive: opcode, entity_id, param_types, return_type, flags
  L3 — structural-tag: opcode, entity_id, domain tag, category tag
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ir.stable_ids import make_pattern_id
from ir.token_count import count_tokens
from index.locator import extract_code_slice

VALID_LEVELS = {"L0", "L1", "L2", "L3", "all"}
PASSTHROUGH_TOKEN_THRESHOLD_DEFAULT = 12


def _kind_opcode(kind: str) -> str:
    return {
        "function": "FN",
        "async_function": "AFN",
        "method": "MT",
        "async_method": "AMT",
        "class": "CLS",
    }.get(kind, "ENT")


# ---------------------------------------------------------------------------
# Level builders
# ---------------------------------------------------------------------------

def _build_L0(entity: dict, repo_path: Path) -> str:
    """Raw source with entity boundary markers."""
    source = extract_code_slice(
        repo_path=repo_path,
        file_path=str(entity["file_path"]),
        start_line=int(entity["start_line"]),
        end_line=int(entity["end_line"]),
    )
    opcode = _kind_opcode(entity["kind"])
    return f"[{opcode} {entity['id']} @{entity['file_path']}:{entity['start_line']}]\n{source}"


def _build_L1(
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
    opcode = _kind_opcode(entity["kind"])

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


def _build_L2(entity: dict, param_types: List[str], return_type: Optional[str], flags: str) -> str:
    """Aggressive: type signatures + flags only."""
    opcode = _kind_opcode(entity["kind"])
    params = ",".join(param_types) if param_types else "-"
    ret = return_type or "?"
    flag_text = flags or "-"
    return f"{opcode} {entity['id']} P=({params})->{ret};F={flag_text}"


def _build_L3(entity: dict, pattern_id: str, module_category: str, module_domain: str = "") -> str:
    """Structural tag row: entity type, ID, domain tag, category tag.

    Format: MT SEND.03 #HTTP #CORE
    Domain tag comes first (primary orientation signal), then category.

    Note: pattern_id is kept in storage for change detection but omitted from
    the text representation served to models (zero semantic signal for selection).
    """
    opcode = _kind_opcode(entity["kind"])
    cat = module_category[:4].upper() if module_category else "UNKN"
    domain = module_domain.upper() if module_domain and module_domain != "unknown" else ""

    if domain:
        return f"{opcode} {entity['id']} #{domain} #{cat}"
    else:
        return f"{opcode} {entity['id']} #{cat}"


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
        "op": _kind_opcode(entity["kind"]),
        "id": entity["id"],
        "level": level,
        "sp": [entity["start_line"], entity["end_line"]],
    }
    if level == "L1":
        base["n"] = name_token
        base["calls"] = calls
        base["flags"] = flags
        base["assigns"] = assigns
        base["bases"] = bases
        if module_category:
            base["category"] = module_category
        if module_domain and module_domain != "unknown":
            base["domain"] = module_domain
    elif level == "L2":
        base["param_types"] = param_types
        base["return_type"] = return_type
        base["flags"] = flags
    elif level == "L3":
        base["pattern_id"] = pattern_id
        base["category"] = module_category
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ir_rows(
    entities: Iterable[dict],
    abbreviations: Dict[str, Dict[str, str]],
    compression_level: str = "L1",
    repo_path: Optional[Path] = None,
    module_categories: Optional[Dict[str, str]] = None,
    module_domains: Optional[Dict[str, str]] = None,
    passthrough_threshold: int = PASSTHROUGH_TOKEN_THRESHOLD_DEFAULT,
) -> List[dict]:
    """Generate compact IR rows at the specified compression level.

    Args:
        entities: Extracted entities with semantic metadata.
        abbreviations: Token maps for names/files/calls.
        compression_level: One of 'L0', 'L1', 'L2', 'L3', or 'all'.
        repo_path: Repository root (required for L0 source extraction).
        module_categories: {file_path: category} from classifier (required for L3).
        module_domains: {file_path: domain} from classifier (required for L3 domain tags).
        passthrough_threshold: Entities with <= this many source tokens emit L0 for all levels.
    """
    level = compression_level.strip().upper()
    if level == "ALL":
        levels_to_generate = ["L0", "L1", "L2", "L3"]
    elif level in ("L0", "L1", "L2", "L3"):
        levels_to_generate = [level]
    else:
        levels_to_generate = ["L1"]
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

        # Passthrough: tiny entities emit L0 regardless of requested level
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
            if is_passthrough and lvl != "L0":
                # For passthrough entities, all non-L0 levels get the L0 representation
                ir_text = _build_L0(entity, repo_path) if repo_path else f"ENT {entity['id']}"
                ir_json = _build_ir_json(
                    entity, "L0", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "L0":
                if repo_path is None:
                    ir_text = f"ENT {entity['id']}"
                else:
                    ir_text = _build_L0(entity, repo_path)
                ir_json = _build_ir_json(
                    entity, "L0", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "L1":
                ir_text = _build_L1(
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
                    entity, "L1", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "L2":
                ir_text = _build_L2(entity, param_types, return_type, flags)
                ir_json = _build_ir_json(
                    entity, "L2", name_token, calls, flags, assigns, bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "L3":
                ir_text = _build_L3(entity, pattern_id, module_category, module_domain)
                ir_json = _build_ir_json(
                    entity, "L3", name_token, calls, flags, assigns, bases,
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
