from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple


NUMERIC_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\.(?P<suffix>[0-9]{2})$")


def entity_family_base(entity_id: str) -> str:
    """Return the base entity ID used to group numeric-suffix variants.

    New format uses dots: STEM.02, STEM.03, etc.
    """
    eid = str(entity_id).strip()
    match = NUMERIC_SUFFIX_RE.match(eid)
    if match:
        return str(match.group("base"))
    return eid


def expand_entity_family_candidates(
    entity_ids: Sequence[str],
    family_index: Dict[str, List[str]],
    max_candidates: int = 20,
) -> Tuple[List[str], List[str]]:
    """Expand selected entity IDs to include all members of each selected family."""
    cap = max(1, int(max_candidates))
    selected: List[str] = []
    seen_selected: set[str] = set()
    for raw in entity_ids:
        eid = str(raw).strip()
        if not eid or eid in seen_selected:
            continue
        selected.append(eid)
        seen_selected.add(eid)

    expanded: List[str] = []
    seen_expanded: set[str] = set()
    for eid in selected:
        if len(expanded) >= cap:
            break
        expanded.append(eid)
        seen_expanded.add(eid)

    for eid in selected:
        if len(expanded) >= cap:
            break
        base = entity_family_base(eid)
        family_members = family_index.get(base, [eid])
        for member in family_members:
            mid = str(member).strip()
            if not mid or mid in seen_expanded:
                continue
            expanded.append(mid)
            seen_expanded.add(mid)
            if len(expanded) >= cap:
                break
        if len(expanded) >= cap:
            break

    selected_set = set(selected)
    added = [eid for eid in expanded if eid not in selected_set]
    return expanded, added
