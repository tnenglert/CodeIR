"""Global abbreviation map generation for semantic compression."""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterable, Optional

from ir.stable_ids import compact_stem
from ir.token_count import count_tokens


CORE_MAP: Dict[str, str] = {
    "user": "USR",
    "profile": "PROF",
    "account": "ACCT",
    "customer": "CUST",
    "request": "REQ",
    "response": "RES",
    "config": "CFG",
    "error": "ERR",
    "session": "SESS",
    "database": "DB",
    "cursor": "CUR",
    "password": "PSWD",
    "token": "TKN",
    "handler": "HNDL",
    "manager": "MGR",
    "service": "SVC",
    "message": "MSG",
    "connection": "CONN",
    "timeout": "TMOT",
    "callback": "CLBK",
    "exception": "EXCN",
    "validate": "VALD",
    "authenticate": "AUTH",
    "permission": "PERM",
    "middleware": "MDLW",
    "transport": "TRNS",
    "adapter": "ADPT",
    "certificate": "CERT",
    "redirect": "RDIR",
    "encoding": "ENCD",
    "header": "HDR",
    "cookie": "CKI",
}


def _token(prefix: str, idx: int) -> str:
    return f"{prefix}{idx:03d}"


def _shorter_token(original: str, candidate: str) -> str:
    return candidate if count_tokens(candidate) < count_tokens(original) else original


def _next_index(existing: Dict[str, str], prefix: str) -> int:
    max_idx = 0
    for token in existing.values():
        if token.startswith(prefix):
            suffix = token[len(prefix):]
            if suffix.isdigit():
                max_idx = max(max_idx, int(suffix))
    return max_idx + 1


def build_abbreviation_maps(
    entity_names: Iterable[str],
    file_paths: Iterable[str],
    call_symbols: Optional[Iterable[str]] = None,
    existing_maps: Optional[Dict[str, Dict[str, str]]] = None,
    compact_mode: bool = False,
) -> Dict[str, Dict[str, str]]:
    """Build deterministic token maps for names/files/calls with persisted stability.

    When compact_mode is True, ignores existing_maps and rebuilds all assignments
    from scratch for optimal token assignment. Use --compact CLI flag for periodic
    optimization.
    """
    if compact_mode:
        existing_maps = {}
    existing_maps = existing_maps or {}

    name_map: Dict[str, str] = OrderedDict(existing_maps.get("entity_name", {}))
    file_map: Dict[str, str] = OrderedDict(existing_maps.get("file_path", {}))
    call_map: Dict[str, str] = OrderedDict(existing_maps.get("call_name", {}))

    if compact_mode:
        name_map.clear()
        file_map.clear()
        call_map.clear()

    next_name = _next_index(name_map, "N")
    next_file = _next_index(file_map, "F")
    next_call = _next_index(call_map, "C")

    used_names = set(name_map.values())
    used_files = set(file_map.values())
    used_calls = set(call_map.values())

    for name in sorted(set(entity_names)):
        if name in name_map:
            continue
        key = name.rsplit(".", 1)[-1].lower()
        if key in CORE_MAP:
            candidate = CORE_MAP[key]
            if candidate not in used_names:
                name_map[name] = candidate
                used_names.add(candidate)
                continue
        # Try compact stem; fall back to sequential on collision
        stem = compact_stem(key)
        candidate = f"N{stem}"
        if count_tokens(candidate) <= 2 and candidate not in used_names:
            token = _shorter_token(name, candidate)
            name_map[name] = token
            used_names.add(token)
        else:
            token = _token("N", next_name)
            name_map[name] = token
            used_names.add(token)
            next_name += 1

    for path in sorted(set(file_paths)):
        if path in file_map:
            continue
        candidate = f"F{next_file:03d}"
        token = _shorter_token(path, candidate)
        file_map[path] = token
        used_files.add(token)
        if token == candidate:
            next_file += 1

    for symbol in sorted(set(call_symbols or [])):
        if symbol in call_map:
            continue
        key = symbol.lower()
        if key in CORE_MAP:
            candidate = CORE_MAP[key]
            if candidate not in used_calls:
                call_map[symbol] = candidate
                used_calls.add(candidate)
                continue
        stem = compact_stem(symbol)
        candidate = f"C{stem}"
        if candidate not in used_calls:
            token = _shorter_token(symbol, candidate)
            call_map[symbol] = token
            used_calls.add(token)
        else:
            token = _token("C", next_call)
            call_map[symbol] = token
            used_calls.add(token)
            next_call += 1

    return {"entity_name": name_map, "file_path": file_map, "call_name": call_map}
