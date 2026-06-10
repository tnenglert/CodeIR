"""IR generation from extracted entities at three compression levels.

Levels:
  Source   — raw source with entity boundary markers (baseline)
  Behavior — semantic-lite: opcode, entity_id, name_token, calls, flags, assigns
  Index    — structural-tag: opcode, entity_id, domain tag, category tag
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from index.locator import extract_code_slice
from ir.stable_ids import make_pattern_id
from ir.token_count import count_tokens

VALID_LEVELS = {"Source", "Behavior", "Index", "all", "Behavior+Index"}
# Arbitrary default tuned to keep tiny entities uncompressed; calibrate if your
# repo wants more or less passthrough at index time.
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
        shown_calls = calls[:6]
        call_str = ",".join(shown_calls)
        remaining_calls = len(calls) - len(shown_calls)
        if remaining_calls > 0:
            call_str += f"+{remaining_calls}"
        parts.append(f"C={call_str}")
    if flags:
        parts.append(f"F={flags}")
    if assigns > 0:
        parts.append(f"A={assigns}")
    if bases:
        parts.append(f"B={','.join(bases[:3])}")

    domain = module_domain.upper() if module_domain and module_domain not in ("unknown", "misc", "") else ""
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
    domain = module_domain.upper() if module_domain and module_domain not in ("unknown", "misc", "") else ""

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
    """Structured JSON representation for programmatic consumption.

    Receives raw (unabbreviated, untruncated) calls and bases — the DB holds
    truth; compression is applied only when rendering ir_text.
    """
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
        if module_domain and module_domain not in ("unknown", "misc", ""):
            base["domain"] = module_domain
    elif level == "Index":
        base["pattern_id"] = pattern_id
        base["category"] = module_category
        if module_domain and module_domain not in ("unknown", "misc", ""):
            base["domain"] = module_domain
    return base


# ---------------------------------------------------------------------------
# Plain rendering (render-time view over stored rows)
# ---------------------------------------------------------------------------

def render_plain_row(row: Dict[str, Any]) -> str:
    """Render a stored IR row with real names in place of compressed tokens.

    Same field layout and omission rules as the dense format, but the entity's
    qualified name appears after its ID and calls/bases use raw source names:

        MT FNLZFLSH.02 orm.Session.finalize_flush C=register,items+3 F=IR A=3 #DB #CORE

    Expects a fetch-style dict with kind, entity_id, qualified_name, ir_text,
    and a parsed ir_json dict. Source-level rows (including passthrough
    entities) return ir_text unchanged. Stores indexed before ir_json carried
    raw names render whatever was stored — re-index for fully plain output.
    """
    ir_json = row.get("ir_json") or {}
    level = str(ir_json.get("level", ""))
    if not ir_json or level == "Source":
        return str(row.get("ir_text", ""))

    opcode = kind_to_opcode(str(row.get("kind", "")))
    parts = [opcode, str(row.get("entity_id", "")), str(row.get("qualified_name", ""))]

    if level == "Behavior":
        calls = [c for c in ir_json.get("calls", []) if isinstance(c, str)]
        if calls:
            shown = calls[:6]
            call_str = ",".join(shown)
            if len(calls) > len(shown):
                call_str += f"+{len(calls) - len(shown)}"
            parts.append(f"C={call_str}")
        flags = str(ir_json.get("flags", "") or "")
        if flags:
            parts.append(f"F={flags}")
        assigns = int(ir_json.get("assigns", 0) or 0)
        if assigns > 0:
            parts.append(f"A={assigns}")
        bases = [b for b in ir_json.get("bases", []) if isinstance(b, str)]
        if bases:
            parts.append(f"B={','.join(bases[:3])}")

    domain = str(ir_json.get("domain", "") or "")
    if domain and domain not in ("unknown", "misc"):
        parts.append(f"#{domain.upper()}")
    category = str(ir_json.get("category", "") or "")
    if category:
        parts.append(f"#{category[:4].upper()}")

    return " ".join(parts)


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
        raw_calls = [call for call in semantic.get("calls", []) if isinstance(call, str)]
        raw_bases = [base for base in semantic.get("bases", []) if isinstance(base, str)]
        # Full abbreviated list: _build_behavior truncates to 6 and appends the
        # +N overflow marker itself (pre-truncating here silently dropped it).
        calls = [call_map.get(call, call) for call in raw_calls]
        flags = semantic.get("flags", "")
        assigns = int(semantic.get("assigns", 0))
        bases = [call_map.get(base, base) for base in raw_bases][:3]

        type_sig = semantic.get("type_sig", {}) or {}
        param_types = list(type_sig.get("param_types", []))
        return_type = type_sig.get("return_type")

        pattern_id = make_pattern_id(
            kind=entity["kind"], flags=flags,
            # Capped at 6 to keep fingerprints identical to stores built when
            # the call list was pre-truncated.
            call_count=min(len(calls), 6), assigns=assigns,
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
                    entity, "Source", name_token, raw_calls, flags, assigns, raw_bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "Source":
                if repo_path is None:
                    ir_text = f"ENT {entity['id']}"
                else:
                    ir_text = _build_source(entity, repo_path)
                ir_json = _build_ir_json(
                    entity, "Source", name_token, raw_calls, flags, assigns, raw_bases,
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
                    entity, "Behavior", name_token, raw_calls, flags, assigns, raw_bases,
                    pattern_id, param_types, return_type, module_category, module_domain,
                )
            elif lvl == "Index":
                ir_text = _build_index(entity, pattern_id, module_category, module_domain)
                ir_json = _build_ir_json(
                    entity, "Index", name_token, raw_calls, flags, assigns, raw_bases,
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
