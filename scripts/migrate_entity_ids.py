#!/usr/bin/env python3
"""Migrate entity IDs from old format (TYPE_STEM_XX) to new format (STEM.XX).

Old format: AMT_RDTKN_03, FN_AUTH, CLS_USER_02
New format: RDTKN.03, AUTH, USER.02

The type prefix is removed (it's redundant with the row's type field).
Underscores in suffixes become dots.
"""

import json
import re
import sqlite3
from pathlib import Path

# Mapping from old format entity IDs to new format
# Built by comparing old index with new index via qualified_name


def build_old_to_new_mapping(old_db_path: Path, new_db_path: Path) -> dict:
    """Build mapping from old entity IDs to new entity IDs via qualified_name."""
    mapping = {}

    # Load old IDs by qualified_name
    old_conn = sqlite3.connect(old_db_path)
    old_rows = old_conn.execute(
        "SELECT id, qualified_name FROM entities"
    ).fetchall()
    old_conn.close()

    old_by_qname = {qname: eid for eid, qname in old_rows}

    # Load new IDs by qualified_name
    new_conn = sqlite3.connect(new_db_path)
    new_rows = new_conn.execute(
        "SELECT id, qualified_name FROM entities"
    ).fetchall()
    new_conn.close()

    new_by_qname = {qname: eid for eid, qname in new_rows}

    # Build mapping
    for qname, old_id in old_by_qname.items():
        if qname in new_by_qname:
            new_id = new_by_qname[qname]
            if old_id != new_id:
                mapping[old_id] = new_id

    return mapping


def migrate_entity_id(old_id: str) -> str:
    """Convert old-format entity ID to new format.

    AMT_RDTKN_03 -> RDTKN.03
    FN_AUTH -> AUTH
    CLS_USER_02 -> USER.02
    """
    # Pattern: TYPE_STEM or TYPE_STEM_XX
    # Types: FN, AFN, MT, AMT, CLS, MD, ENT
    type_prefixes = ("FN_", "AFN_", "MT_", "AMT_", "CLS_", "MD_", "ENT_")

    for prefix in type_prefixes:
        if old_id.startswith(prefix):
            rest = old_id[len(prefix):]
            # Check for numeric suffix like _02, _03
            match = re.match(r"^(.+?)_(\d{2})$", rest)
            if match:
                stem, suffix = match.groups()
                return f"{stem}.{suffix}"
            else:
                return rest

    # No recognized prefix, return as-is
    return old_id


def migrate_json_file(file_path: Path, dry_run: bool = False) -> dict:
    """Migrate entity IDs in a JSON file."""
    content = file_path.read_text(encoding="utf-8")
    original = content

    # Find all old-format entity IDs and replace them
    type_prefixes = ["FN", "AFN", "MT", "AMT", "CLS", "MD", "ENT"]
    prefix_pattern = "|".join(type_prefixes)

    # Match TYPE_STEM or TYPE_STEM_XX patterns
    pattern = re.compile(
        rf'\b({prefix_pattern})_([A-Z0-9]+(?:_[0-9]{{2}})?)\b'
    )

    replacements = {}

    def replace_match(m):
        old_id = m.group(0)
        new_id = migrate_entity_id(old_id)
        if old_id != new_id:
            replacements[old_id] = new_id
        return new_id

    migrated = pattern.sub(replace_match, content)

    stats = {
        "file": str(file_path),
        "replacements": len(replacements),
        "changed": migrated != original,
    }

    if replacements:
        print(f"\n{file_path.name}:")
        for old, new in sorted(replacements.items())[:10]:
            print(f"  {old} -> {new}")
        if len(replacements) > 10:
            print(f"  ... and {len(replacements) - 10} more")

    if not dry_run and migrated != original:
        file_path.write_text(migrated, encoding="utf-8")
        print(f"  Written: {file_path}")

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Migrate entity IDs to new format")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("files", nargs="*", help="Files to migrate (default: task packs)")
    args = parser.parse_args()

    # Default files to migrate
    if not args.files:
        test_packs_dir = Path("tests/eval/test_packs")
        files = list(test_packs_dir.glob("*.json"))
        # Add other relevant files
        files.extend([
            Path("tests/eval/corpus/requests_ir_samples.json"),
        ])
        files = [f for f in files if f.exists() and not f.name.endswith(".bak")]
    else:
        files = [Path(f) for f in args.files]

    print(f"{'DRY RUN - ' if args.dry_run else ''}Migrating {len(files)} files...")

    total_replacements = 0
    for f in files:
        if f.exists():
            stats = migrate_json_file(f, dry_run=args.dry_run)
            total_replacements += stats["replacements"]

    print(f"\nTotal replacements: {total_replacements}")
    if args.dry_run:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
