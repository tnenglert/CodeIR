"""Multi-pass repository indexing orchestration.

Pipeline:
  Pass 0: discover files + content hash -> compare against stored file_metadata -> emit changed_files
  Pass 1: parse bare entities -> classify each file into module category -> persist modules
  Pass 2: collect ALL symbols across repo -> build global abbreviation maps -> persist
  Pass 3: full semantic analysis -> passthrough threshold -> generate IR -> conditional upsert
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ir.abbreviations import build_abbreviation_maps
from ir.classifier import classify_file, classify_domain
from ir.compressor import build_ir_rows
from ir.stable_ids import make_entity_base_id
from ir.token_count import count_tokens
from index.locator import (
    compute_file_content_hash,
    discover_package_roots,
    discover_source_files,
    extract_code_slice,
    extract_import_names,
    parse_ast,
    parse_bare_entities_from_file,
    parse_entities_from_file,
    split_imports,
)
from index.mapping import load_abbreviation_maps, save_abbreviation_maps
from index.store.db import connect, ensure_store


# ---------------------------------------------------------------------------
# Entity ID assignment
# ---------------------------------------------------------------------------

LEGACY_MODE_TO_LEVEL: Dict[str, str] = {
    "a": "L3",       # pattern-focused legacy mode
    "b": "L1",       # semantic-lite legacy mode
    "hybrid": "L2",  # mixed legacy mode
}


def map_legacy_mode_to_level(mode: str) -> str:
    """Map legacy compression mode aliases to active compression levels."""
    return LEGACY_MODE_TO_LEVEL.get(str(mode).strip().lower(), "L1")


def resolve_compression_level(config: Dict[str, Any]) -> str:
    """Resolve active compression level from config with legacy mode support."""
    raw_level = str(config.get("compression_level", "")).strip().upper()
    if raw_level in {"L0", "L1", "L2", "L3", "ALL"}:
        return raw_level
    raw_mode = str(config.get("compression_mode", "")).strip().lower()
    if raw_mode:
        return map_legacy_mode_to_level(raw_mode)
    return "L1"


def _entity_base_from_id(entity_id: str) -> str:
    """Return the unsuffixed entity ID base (e.g., AUTH from AUTH.02).

    New format uses dots: STEM.SUFFIX (e.g., RDTKN.03)
    """
    head, sep, tail = entity_id.rpartition(".")
    if sep and tail.isdigit() and len(tail) == 2:
        return head
    return entity_id


def _next_entity_id(base: str, used_ids: set[str]) -> str:
    """Allocate the next available ID for a base without colliding with used IDs.

    Uses dot separator for suffixes: STEM.02, STEM.03, etc.
    """
    if base not in used_ids:
        return base
    idx = 2
    while True:
        candidate = f"{base}.{idx:02d}"
        if candidate not in used_ids:
            return candidate
        idx += 1


def _collect_existing_ids_by_base(conn: sqlite3.Connection) -> Dict[str, set[str]]:
    """Collect existing entity IDs grouped by base ID."""
    rows = conn.execute("SELECT id FROM entities").fetchall()
    out: Dict[str, set[str]] = {}
    for row in rows:
        entity_id = str(row[0])
        base = _entity_base_from_id(entity_id)
        out.setdefault(base, set()).add(entity_id)
    return out


def _assign_entity_ids(
    entities: List[dict],
    existing_ids_by_base: Optional[Dict[str, set[str]]] = None,
) -> None:
    """Assign stable deterministic IDs with collision suffixes.

    existing_ids_by_base allows changed-file entities to allocate IDs without
    colliding with unchanged entities already stored in the DB.
    """
    existing_ids_by_base = existing_ids_by_base or {}
    by_base: Dict[str, List[dict]] = {}
    for entity in entities:
        base = make_entity_base_id(kind=str(entity["kind"]), qualified_name=str(entity["qualified_name"]))
        by_base.setdefault(base, []).append(entity)

    for base, group in by_base.items():
        used_ids = set(existing_ids_by_base.get(base, set()))
        group.sort(key=lambda e: (str(e["file_path"]), int(e["start_line"]), int(e["end_line"]), str(e["qualified_name"])))
        for entity in group:
            entity_id = _next_entity_id(base, used_ids)
            entity["id"] = entity_id
            used_ids.add(entity_id)


# ---------------------------------------------------------------------------
# Pass 0: Change detection
# ---------------------------------------------------------------------------

def _detect_changes(
    conn: sqlite3.Connection, all_files: List[Path], repo_path: Path,
) -> Tuple[List[Path], List[Path]]:
    """Compare current file hashes against stored file_metadata.

    Returns (changed_files, unchanged_files).
    """
    stored_hashes: Dict[str, str] = {}
    try:
        rows = conn.execute("SELECT file_path, content_hash FROM file_metadata").fetchall()
        for row in rows:
            stored_hashes[row[0]] = row[1]
    except sqlite3.OperationalError:
        pass

    changed: List[Path] = []
    unchanged: List[Path] = []

    for file_path in all_files:
        rel_path = file_path.resolve().relative_to(repo_path.resolve()).as_posix()
        current_hash = compute_file_content_hash(file_path)
        if stored_hashes.get(rel_path) == current_hash:
            unchanged.append(file_path)
        else:
            changed.append(file_path)

    return changed, unchanged


# ---------------------------------------------------------------------------
# Pass 1: Module classification persistence
# ---------------------------------------------------------------------------

def _persist_modules(
    conn: sqlite3.Connection,
    classifications: Dict[str, str],
    file_hashes: Dict[str, str],
    entity_counts: Dict[str, int],
    file_deps: Optional[Dict[str, str]] = None,
) -> None:
    """Upsert module classifications into the modules table."""
    now = datetime.now(timezone.utc).isoformat()
    file_deps = file_deps or {}
    for rel_path, category in classifications.items():
        conn.execute(
            "INSERT INTO modules (file_path, category, content_hash, entity_count, deps_internal, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET "
            "category=excluded.category, content_hash=excluded.content_hash, "
            "entity_count=excluded.entity_count, deps_internal=excluded.deps_internal, "
            "indexed_at=excluded.indexed_at",
            (rel_path, category, file_hashes.get(rel_path, ""),
             entity_counts.get(rel_path, 0), file_deps.get(rel_path, ""), now),
        )
    conn.commit()


def _persist_file_metadata(
    conn: sqlite3.Connection,
    file_hashes: Dict[str, str],
    file_sizes: Dict[str, int],
) -> None:
    """Upsert file metadata for incremental change detection."""
    now = datetime.now(timezone.utc).isoformat()
    for rel_path, content_hash in file_hashes.items():
        conn.execute(
            "INSERT INTO file_metadata (file_path, content_hash, last_indexed_at, byte_size) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET "
            "content_hash=excluded.content_hash, last_indexed_at=excluded.last_indexed_at, "
            "byte_size=excluded.byte_size",
            (rel_path, content_hash, now, file_sizes.get(rel_path, 0)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Entity persistence (conditional upsert, NOT full wipe)
# ---------------------------------------------------------------------------

def _upsert_entities(conn: sqlite3.Connection, entities: List[dict]) -> None:
    """Upsert entities. No full wipe — only changed files' entities are updated."""
    conn.executemany(
        "INSERT INTO entities (id, kind, name, qualified_name, file_path, start_line, end_line, module_id, complexity_class) "
        "VALUES (:id, :kind, :name, :qualified_name, :file_path, :start_line, :end_line, :module_id, :complexity_class) "
        "ON CONFLICT(id) DO UPDATE SET "
        "kind=excluded.kind, name=excluded.name, qualified_name=excluded.qualified_name, "
        "file_path=excluded.file_path, start_line=excluded.start_line, end_line=excluded.end_line, "
        "module_id=excluded.module_id, complexity_class=excluded.complexity_class",
        entities,
    )
    conn.commit()


def _upsert_ir_rows(conn: sqlite3.Connection, ir_rows: List[dict]) -> None:
    """Upsert IR rows with composite PK (entity_id, mode)."""
    conn.executemany(
        "INSERT INTO ir_rows (entity_id, mode, ir_text, ir_json, "
        "source_char_count, ir_char_count, source_token_count, ir_token_count, compression_ratio) "
        "VALUES (:entity_id, :mode, :ir_text, :ir_json, "
        ":source_char_count, :ir_char_count, :source_token_count, :ir_token_count, :compression_ratio) "
        "ON CONFLICT(entity_id, mode) DO UPDATE SET "
        "ir_text=excluded.ir_text, ir_json=excluded.ir_json, "
        "source_char_count=excluded.source_char_count, ir_char_count=excluded.ir_char_count, "
        "source_token_count=excluded.source_token_count, ir_token_count=excluded.ir_token_count, "
        "compression_ratio=excluded.compression_ratio",
        ir_rows,
    )
    conn.commit()


def _upsert_index_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO index_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def _remove_stale_entities(conn: sqlite3.Connection, current_rel_paths: set[str]) -> None:
    """Remove entities and modules for files that no longer exist in the repo."""
    stored_paths = {row[0] for row in conn.execute("SELECT DISTINCT file_path FROM entities").fetchall()}
    stale = stored_paths - current_rel_paths
    if stale:
        placeholders = ",".join("?" for _ in stale)
        conn.execute(f"DELETE FROM entities WHERE file_path IN ({placeholders})", list(stale))
        conn.execute(f"DELETE FROM modules WHERE file_path IN ({placeholders})", list(stale))
        conn.execute(f"DELETE FROM file_metadata WHERE file_path IN ({placeholders})", list(stale))
        conn.commit()


def _remove_changed_file_entities(conn: sqlite3.Connection, changed_rel_paths: List[str]) -> None:
    """Remove entities for files that are about to be re-indexed."""
    if not changed_rel_paths:
        return
    placeholders = ",".join("?" for _ in changed_rel_paths)
    conn.execute(f"DELETE FROM entities WHERE file_path IN ({placeholders})", changed_rel_paths)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_all_entity_names(conn: sqlite3.Connection, new_entities: List[dict]) -> List[str]:
    """Collect all entity qualified names from DB (unchanged) + new entities (changed)."""
    names: set[str] = set()
    try:
        for row in conn.execute("SELECT DISTINCT qualified_name FROM entities"):
            names.add(row[0])
    except sqlite3.OperationalError:
        pass
    for entity in new_entities:
        names.add(str(entity.get("qualified_name", entity.get("name", ""))))
    return sorted(names)


def _classify_complexity(entity: dict, source_tokens: int) -> str:
    """Classify entity complexity based on source token count."""
    if source_tokens <= 12:
        return "simple"
    if source_tokens <= 100:
        return "moderate"
    return "complex"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def index_repo(repo_path: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run multi-pass index pipeline and return stats."""
    schema_path = Path(__file__).resolve().parent / "store" / "schema.json"
    store_paths = ensure_store(repo_path=repo_path, schema_path=schema_path)

    compression_level = resolve_compression_level(config)
    compact_mode = bool(config.get("compact_mode", False))
    passthrough_threshold = int(config.get("passthrough_threshold", 12))

    # -----------------------------------------------------------------------
    # Pass 0: Discovery + incremental change detection
    # -----------------------------------------------------------------------
    all_files = discover_source_files(
        repo_path=repo_path,
        extensions=config.get("extensions", [".py"]),
        hidden_dirs=config.get("hidden_dirs", []),
    )

    entities_conn = connect(store_paths["entities_db"])
    changed_files, unchanged_files = _detect_changes(entities_conn, all_files, repo_path)

    # Build set of all current relative paths for stale detection
    all_rel_paths: set[str] = set()
    for f in all_files:
        all_rel_paths.add(f.resolve().relative_to(repo_path.resolve()).as_posix())

    # Detect compression level change — if the requested level differs from
    # the stored level, force a full re-index so IR rows are regenerated.
    stored_level = ""
    try:
        row = entities_conn.execute(
            "SELECT value FROM index_meta WHERE key='compression_level'"
        ).fetchone()
        stored_level = row[0] if row else ""
    except Exception:
        pass

    level_changed = (
        compression_level != stored_level
        and stored_level != ""
    )
    if level_changed:
        changed_files = list(all_files)
        unchanged_files = []

    if not changed_files:
        # Clean up stale entries even when no files changed
        _remove_stale_entities(entities_conn, all_rel_paths)
        entities_conn.close()
        return {
            "status": "no_changes",
            "store_dir": str(store_paths["store_dir"]),
            "files_scanned": len(all_files),
            "files_changed": 0,
            "files_unchanged": len(unchanged_files),
        }

    # -----------------------------------------------------------------------
    # Pass 1: Bare parse + module classification
    # -----------------------------------------------------------------------
    file_classifications: Dict[str, str] = {}
    file_domains: Dict[str, str] = {}
    file_hashes: Dict[str, str] = {}
    file_sizes: Dict[str, int] = {}
    bare_entities: List[dict] = []
    changed_rel_paths: List[str] = []
    package_roots = discover_package_roots(repo_path)
    file_deps: Dict[str, str] = {}

    for file_path in changed_files:
        rel_path = file_path.resolve().relative_to(repo_path.resolve()).as_posix()
        changed_rel_paths.append(rel_path)
        content_hash = compute_file_content_hash(file_path)
        file_hashes[rel_path] = content_hash
        file_sizes[rel_path] = file_path.stat().st_size

        tree = parse_ast(file_path)
        if tree is None:
            file_classifications[rel_path] = "core_logic"
            file_domains[rel_path] = "unknown"
            continue

        category = classify_file(file_path, tree)
        domain = classify_domain(file_path, tree)
        file_classifications[rel_path] = category
        file_domains[rel_path] = domain

        all_imports = extract_import_names(tree, file_path)
        internal_deps, _ = split_imports(all_imports, package_roots)
        file_deps[rel_path] = ",".join(internal_deps)

        bare = parse_bare_entities_from_file(file_path)
        for entity in bare:
            entity["file_path"] = rel_path
        bare_entities.extend(bare)

    # Remove old entities for changed files before re-inserting
    _remove_changed_file_entities(entities_conn, changed_rel_paths)
    existing_ids_by_base = _collect_existing_ids_by_base(entities_conn)

    # Compute entity counts per file from bare entities
    entity_counts: Dict[str, int] = {}
    for entity in bare_entities:
        fp = str(entity["file_path"])
        entity_counts[fp] = entity_counts.get(fp, 0) + 1

    _persist_modules(entities_conn, file_classifications, file_hashes, entity_counts, file_deps)
    _persist_file_metadata(entities_conn, file_hashes, file_sizes)

    # -----------------------------------------------------------------------
    # Pass 2: Global abbreviation maps
    # -----------------------------------------------------------------------
    mapping_conn = connect(store_paths["mapping_db"])
    existing_maps = load_abbreviation_maps(mapping_conn)

    all_entity_names = _collect_all_entity_names(entities_conn, bare_entities)
    all_file_paths = sorted(all_rel_paths)

    # First build without call symbols (not yet known)
    abbrev_maps = build_abbreviation_maps(
        entity_names=all_entity_names,
        file_paths=all_file_paths,
        call_symbols=[],
        existing_maps=existing_maps,
        compact_mode=compact_mode,
    )

    # -----------------------------------------------------------------------
    # Pass 3: Full semantic analysis + IR generation
    # -----------------------------------------------------------------------
    full_entities: List[dict] = []
    for file_path in changed_files:
        rel_path = file_path.resolve().relative_to(repo_path.resolve()).as_posix()
        parsed = parse_entities_from_file(file_path)
        for entity in parsed:
            entity["file_path"] = rel_path
        full_entities.extend(parsed)

    _assign_entity_ids(full_entities, existing_ids_by_base=existing_ids_by_base)

    # Rebuild abbreviations now that call symbols are known
    call_symbols = [
        call
        for entity in full_entities
        for call in list((entity.get("semantic") or {}).get("calls", []))
        if isinstance(call, str)
    ]
    abbrev_maps = build_abbreviation_maps(
        entity_names=all_entity_names,
        file_paths=all_file_paths,
        call_symbols=call_symbols,
        existing_maps=existing_maps,
        compact_mode=compact_mode,
    )

    # Generate IR rows
    ir_rows = build_ir_rows(
        entities=full_entities,
        abbreviations=abbrev_maps,
        compression_level=compression_level,
        repo_path=repo_path,
        module_categories=file_classifications,
        module_domains=file_domains,
        passthrough_threshold=passthrough_threshold,
    )

    # Compute compression metrics for each IR row
    entity_by_id = {entity["id"]: entity for entity in full_entities}
    for row in ir_rows:
        entity = entity_by_id.get(row["entity_id"])
        if entity:
            source_text = extract_code_slice(
                repo_path=repo_path,
                file_path=str(entity["file_path"]),
                start_line=int(entity["start_line"]),
                end_line=int(entity["end_line"]),
            )
            source_char_count = len(source_text)
            ir_char_count = len(str(row["ir_text"]))
            source_token_count = count_tokens(source_text)
            ir_token_count = count_tokens(str(row["ir_text"]))
            row["source_char_count"] = source_char_count
            row["ir_char_count"] = ir_char_count
            row["source_token_count"] = source_token_count
            row["ir_token_count"] = ir_token_count
            row["compression_ratio"] = (ir_token_count / source_token_count) if source_token_count else 1.0
        else:
            row.setdefault("source_char_count", 0)
            row.setdefault("ir_char_count", 0)
            row.setdefault("source_token_count", 0)
            row.setdefault("ir_token_count", 0)
            row.setdefault("compression_ratio", 1.0)

    # Assign complexity class and module_id to full entities for DB persistence
    for entity in full_entities:
        source_text = extract_code_slice(
            repo_path=repo_path,
            file_path=str(entity["file_path"]),
            start_line=int(entity["start_line"]),
            end_line=int(entity["end_line"]),
        )
        src_tokens = count_tokens(source_text)
        entity["complexity_class"] = _classify_complexity(entity, src_tokens)
        entity["module_id"] = str(entity["file_path"])

    # Persist
    _upsert_entities(entities_conn, full_entities)
    _upsert_ir_rows(entities_conn, ir_rows)
    _remove_stale_entities(entities_conn, all_rel_paths)

    total_entities = entities_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    total_ir_rows = entities_conn.execute("SELECT COUNT(*) FROM ir_rows").fetchone()[0]

    _upsert_index_meta(entities_conn, "python_files_indexed", str(len(all_files)))
    _upsert_index_meta(entities_conn, "entities", str(total_entities))
    _upsert_index_meta(entities_conn, "ir_rows", str(total_ir_rows))
    _upsert_index_meta(entities_conn, "compression_level", compression_level)
    entities_conn.close()

    abbrev_count = save_abbreviation_maps(mapping_conn, abbrev_maps)
    mapping_conn.close()

    return {
        "store_dir": str(store_paths["store_dir"]),
        "files_scanned": len(all_files),
        "files_changed": len(changed_files),
        "files_unchanged": len(unchanged_files),
        "entities_indexed": len(full_entities),
        "total_entities": total_entities,
        "ir_rows": len(ir_rows),
        "total_ir_rows": total_ir_rows,
        "abbreviations": abbrev_count,
        "compression_level": compression_level,
    }
