"""Structural pattern detection for CodeIR.

Detects groups of entities that share the same architectural role based on
base class inheritance. Patterns surface in bearings.md and enable L3 compression.
"""

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Minimum group size to become a pattern
MIN_PATTERN_SIZE = 30


@dataclass
class PatternMember:
    """An entity that belongs to a pattern."""
    entity_id: str
    name: str
    calls: List[str]
    flags: str
    extra_calls: List[str] = field(default_factory=list)
    extra_flags: str = ""
    missing_calls: List[str] = field(default_factory=list)


@dataclass
class Pattern:
    """A detected structural pattern."""
    pattern_id: str
    entity_type: str  # CLS only for v1
    base_class: str
    category: str
    member_count: int
    common_calls: List[str]
    common_flags: str
    is_test_pattern: bool
    members: List[PatternMember] = field(default_factory=list)

    def to_bearings_line(self) -> str:
        """Format pattern for bearings.md display."""
        calls_str = ", ".join(self.common_calls[:5]) if self.common_calls else "-"
        flags_str = self.common_flags if self.common_flags else "-"
        return (
            f"- {self.base_class} ({self.member_count} classes): "
            f"Calls: {calls_str}. Flags: {flags_str}."
        )


def _ensure_pattern_tables(conn: sqlite3.Connection) -> None:
    """Create pattern tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            pattern_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            base_class TEXT NOT NULL,
            category TEXT,
            member_count INTEGER NOT NULL,
            common_calls TEXT,
            common_flags TEXT,
            is_test_pattern BOOLEAN DEFAULT FALSE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pattern_members (
            entity_id TEXT NOT NULL,
            pattern_id TEXT NOT NULL,
            delta_extra_calls TEXT,
            delta_extra_flags TEXT,
            delta_missing_calls TEXT,
            PRIMARY KEY (entity_id, pattern_id),
            FOREIGN KEY (pattern_id) REFERENCES patterns(pattern_id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pattern_members_pattern ON pattern_members(pattern_id)")
    conn.commit()


def _is_test_category(category: str) -> bool:
    """Check if category is test-related."""
    return category.lower() in ("tests", "test", "testing")


def _extract_entities_with_bases(conn: sqlite3.Connection) -> List[Dict]:
    """Extract all class entities with their base classes from ir_rows."""
    query = """
        SELECT e.id, e.kind, e.name, e.qualified_name, e.file_path,
               e.calls_json, ir.ir_json
        FROM entities e
        LEFT JOIN ir_rows ir ON e.id = ir.entity_id AND ir.mode = 'Behavior'
        WHERE e.kind = 'class'
    """

    entities = []
    for row in conn.execute(query):
        ir_data = {}
        if row[6]:  # ir_json
            try:
                ir_data = json.loads(row[6])
            except json.JSONDecodeError:
                pass

        bases = ir_data.get("bases", [])
        if not bases:
            continue  # Skip classes without base classes

        entities.append({
            "id": row[0],
            "kind": row[1],
            "name": row[2],
            "qualified_name": row[3],
            "file_path": row[4],
            "calls": json.loads(row[5] or "[]"),
            "bases": bases,
            "flags": ir_data.get("flags", ""),
            "category": ir_data.get("category", ""),
        })

    return entities


def _compute_common_calls(members: List[Dict], threshold: float = 0.7) -> List[str]:
    """Find calls that appear in ≥threshold of members."""
    if not members:
        return []

    call_counts = Counter()
    for m in members:
        for c in m["calls"]:
            call_counts[c] += 1

    min_count = len(members) * threshold
    return [c for c, count in call_counts.most_common() if count >= min_count]


def _compute_common_flags(members: List[Dict], threshold: float = 0.5) -> str:
    """Find flag combination that ≥threshold of members share."""
    if not members:
        return ""

    flag_counts = Counter(m["flags"] for m in members if m["flags"])
    if not flag_counts:
        return ""

    most_common, count = flag_counts.most_common(1)[0]
    if count >= len(members) * threshold:
        return most_common
    return ""


def detect_patterns(db_path: Path, min_size: int = MIN_PATTERN_SIZE) -> List[Pattern]:
    """Detect structural patterns in an indexed repository.

    Groups class entities by (entity_type, first_base_class, category).
    Only groups with ≥min_size members become patterns.

    Args:
        db_path: Path to entities.db
        min_size: Minimum group size to form a pattern

    Returns:
        List of detected Pattern objects
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Ensure tables exist
    _ensure_pattern_tables(conn)

    # Clear existing patterns
    conn.execute("DELETE FROM pattern_members")
    conn.execute("DELETE FROM patterns")
    conn.commit()

    # Extract entities with bases
    entities = _extract_entities_with_bases(conn)

    if not entities:
        conn.close()
        return []

    # Group by (entity_type, first_base, category)
    groups: Dict[Tuple[str, str, str], List[Dict]] = defaultdict(list)
    for e in entities:
        first_base = e["bases"][0]
        key = (e["kind"], first_base, e["category"])
        groups[key].append(e)

    # Filter to groups meeting size threshold
    patterns = []
    for (entity_type, base_class, category), members in groups.items():
        if len(members) < min_size:
            continue

        # Compute common calls and flags
        common_calls = _compute_common_calls(members)
        common_flags = _compute_common_flags(members)

        # Generate pattern ID
        cat_suffix = f"_{category}" if category else ""
        pattern_id = f"{base_class}{cat_suffix}"

        is_test = _is_test_category(category)

        # Build pattern
        pattern = Pattern(
            pattern_id=pattern_id,
            entity_type=entity_type,
            base_class=base_class,
            category=category,
            member_count=len(members),
            common_calls=common_calls,
            common_flags=common_flags,
            is_test_pattern=is_test,
        )

        # Compute per-member deltas
        common_calls_set = set(common_calls)
        for m in members:
            member_calls = set(m["calls"])
            extra_calls = sorted(member_calls - common_calls_set)
            missing_calls = sorted(common_calls_set - member_calls)

            # Extra flags (flags this member has that aren't in common)
            extra_flags = ""
            if m["flags"] and m["flags"] != common_flags:
                extra_flags = m["flags"]

            pattern.members.append(PatternMember(
                entity_id=m["id"],
                name=m["name"],
                calls=m["calls"],
                flags=m["flags"],
                extra_calls=extra_calls,
                extra_flags=extra_flags,
                missing_calls=missing_calls,
            ))

        patterns.append(pattern)

    # Store patterns in database
    for p in patterns:
        conn.execute("""
            INSERT INTO patterns (pattern_id, entity_type, base_class, category,
                                  member_count, common_calls, common_flags, is_test_pattern)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p.pattern_id,
            p.entity_type,
            p.base_class,
            p.category,
            p.member_count,
            ",".join(p.common_calls),
            p.common_flags,
            p.is_test_pattern,
        ))

        for m in p.members:
            conn.execute("""
                INSERT INTO pattern_members (entity_id, pattern_id, delta_extra_calls,
                                             delta_extra_flags, delta_missing_calls)
                VALUES (?, ?, ?, ?, ?)
            """, (
                m.entity_id,
                p.pattern_id,
                ",".join(m.extra_calls) if m.extra_calls else None,
                m.extra_flags if m.extra_flags else None,
                ",".join(m.missing_calls) if m.missing_calls else None,
            ))

    conn.commit()
    conn.close()

    return patterns


def get_patterns(db_path: Path, category: Optional[str] = None,
                 include_tests: bool = True) -> List[Pattern]:
    """Load patterns from database.

    Args:
        db_path: Path to entities.db
        category: Filter to specific category (optional)
        include_tests: Whether to include test patterns

    Returns:
        List of Pattern objects (empty if table doesn't exist)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check if patterns table exists
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='patterns'"
    ).fetchone()
    if not table_check:
        conn.close()
        return []

    query = "SELECT * FROM patterns WHERE 1=1"
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)

    if not include_tests:
        query += " AND is_test_pattern = FALSE"

    query += " ORDER BY member_count DESC"

    patterns = []
    for row in conn.execute(query, params):
        pattern = Pattern(
            pattern_id=row["pattern_id"],
            entity_type=row["entity_type"],
            base_class=row["base_class"],
            category=row["category"],
            member_count=row["member_count"],
            common_calls=row["common_calls"].split(",") if row["common_calls"] else [],
            common_flags=row["common_flags"] or "",
            is_test_pattern=bool(row["is_test_pattern"]),
        )
        patterns.append(pattern)

    conn.close()
    return patterns


def get_entity_pattern(db_path: Path, entity_id: str) -> Optional[str]:
    """Get the base class name for an entity's pattern, if any.

    Args:
        db_path: Path to entities.db
        entity_id: Entity ID to look up

    Returns:
        Base class name (e.g., "ModelSQL") or None if entity isn't in a pattern
    """
    conn = sqlite3.connect(db_path)

    # Check if pattern_members table exists
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pattern_members'"
    ).fetchone()
    if not table_check:
        conn.close()
        return None

    # Join to get base_class from patterns table
    row = conn.execute("""
        SELECT p.base_class
        FROM pattern_members pm
        JOIN patterns p ON pm.pattern_id = p.pattern_id
        WHERE pm.entity_id = ?
    """, (entity_id,)).fetchone()
    conn.close()

    return row[0] if row else None


@dataclass
class PatternDetails:
    """Full pattern info for smart show output."""
    base_class: str
    member_count: int
    category: str
    common_calls: List[str]
    common_flags: str
    extra_calls: List[str]
    extra_flags: str
    missing_calls: List[str]


def get_entity_pattern_details(db_path: Path, entity_id: str) -> Optional[PatternDetails]:
    """Get full pattern details for an entity, including deviations.

    Args:
        db_path: Path to entities.db
        entity_id: Entity ID to look up

    Returns:
        PatternDetails with pattern info and member deviations, or None if not in pattern
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check if tables exist
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pattern_members'"
    ).fetchone()
    if not table_check:
        conn.close()
        return None

    # Join to get all pattern info + member deltas
    row = conn.execute("""
        SELECT p.base_class, p.member_count, p.category, p.common_calls, p.common_flags,
               pm.delta_extra_calls, pm.delta_extra_flags, pm.delta_missing_calls
        FROM pattern_members pm
        JOIN patterns p ON pm.pattern_id = p.pattern_id
        WHERE pm.entity_id = ?
    """, (entity_id,)).fetchone()
    conn.close()

    if not row:
        return None

    return PatternDetails(
        base_class=row["base_class"],
        member_count=row["member_count"],
        category=row["category"] or "",
        common_calls=row["common_calls"].split(",") if row["common_calls"] else [],
        common_flags=row["common_flags"] or "",
        extra_calls=row["delta_extra_calls"].split(",") if row["delta_extra_calls"] else [],
        extra_flags=row["delta_extra_flags"] or "",
        missing_calls=row["delta_missing_calls"].split(",") if row["delta_missing_calls"] else [],
    )


def get_pattern_summary_for_bearings(db_path: Path, category: str) -> Optional[str]:
    """Generate pattern summary block for a category's bearings section.

    Args:
        db_path: Path to entities.db
        category: Category to summarize

    Returns:
        Formatted pattern summary or None if no patterns
    """
    patterns = get_patterns(db_path, category=category)

    if not patterns:
        return None

    is_test_category = _is_test_category(category)

    if is_test_category:
        # Compact format for test patterns
        pattern_list = ", ".join(f"{p.base_class} ({p.member_count})" for p in patterns)
        return f"Patterns: {pattern_list}"

    # Full format for non-test patterns
    lines = ["### Structural Patterns"]

    total_in_patterns = sum(p.member_count for p in patterns)

    for p in patterns:
        lines.append(p.to_bearings_line())

    # Get total entities in category for context
    conn = sqlite3.connect(db_path)
    total_in_category = conn.execute("""
        SELECT COUNT(*) FROM entities e
        JOIN ir_rows ir ON e.id = ir.entity_id AND ir.mode = 'Behavior'
        WHERE json_extract(ir.ir_json, '$.category') = ?
    """, (category,)).fetchone()[0]
    conn.close()

    if total_in_category > 0:
        lines.append(f"→ {total_in_patterns} of {total_in_category} entities follow known patterns.")

    return "\n".join(lines)
