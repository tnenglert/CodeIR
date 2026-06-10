#!/usr/bin/env python3
"""Prepare baseline vs refreshed bearings artifacts for the bearings A/B benchmark.

Workflow:
  1. snapshot current code + bearings
  2. apply-baseline to patch old bearings-generation behavior and regenerate
  3. run benchmark
  4. restore to current behavior/artifacts
  5. run refreshed benchmark
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKUP_DIR = ROOT / ".tmp_bearings_ab_backup"
TARGET_REPOS = [
    ROOT / "tests" / "_local" / "testRepositories" / "_flask-main",
    ROOT / "tests" / "_local" / "testRepositories" / "tryton-main",
]


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def snapshot() -> None:
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    BACKUP_DIR.mkdir(parents=True)
    _copy(ROOT / "cli.py", BACKUP_DIR / "cli.py.current")
    _copy(ROOT / "ir" / "classifier.py", BACKUP_DIR / "classifier.py.current")
    for repo in TARGET_REPOS:
        repo_backup = BACKUP_DIR / repo.name
        _copy(repo / ".codeir" / "bearings.md", repo_backup / "bearings.md")
        _copy(repo / ".codeir" / "bearings-summary.md", repo_backup / "bearings-summary.md")
        _copy(repo / ".codeir" / "bearings", repo_backup / "bearings")
    print(f"Snapshot saved to {BACKUP_DIR}")


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"Expected text not found for replacement:\\n{old[:120]}")
    return text.replace(old, new, 1)


def apply_baseline() -> None:
    cli_path = ROOT / "cli.py"
    classifier_path = ROOT / "ir" / "classifier.py"
    cli_text = cli_path.read_text(encoding="utf-8")
    classifier_text = classifier_path.read_text(encoding="utf-8")

    cli_text = _replace_once(
        cli_text,
        '"SELECT file_path, category, domain, entity_count, deps_internal "',
        '"SELECT file_path, category, entity_count, deps_internal "',
    )
    cli_text = _replace_once(
        cli_text,
        """    modules = [
        {
            "file_path": row["file_path"],
            "category": row["category"],
            "domain": row["domain"],
            "entity_count": row["entity_count"],
            "deps_internal": row["deps_internal"],
        }
        for row in rows
    ]
""",
        """    modules = [
        {"file_path": row["file_path"], "category": row["category"],
         "entity_count": row["entity_count"], "deps_internal": row["deps_internal"]}
        for row in rows
    ]
""",
    )

    classifier_text = _replace_once(
        classifier_text,
        "# Arbitrary display threshold tuned to keep bearings readable; calibrate per\n# repo if you want more or fewer module lines before collapsing.\nINDIVIDUAL_LISTING_THRESHOLD = 5\n\n# Arbitrary pattern threshold tuned to collapse only obviously repetitive\n# filename groups; calibrate per repo if your categories are denser or sparser.\nPATTERN_COLLAPSE_TRIGGER = 5\n",
        "# Modules with >= this many entities are individually listed in tier 2\nINDIVIDUAL_LISTING_THRESHOLD = 5\n\n# Filename must appear > this many times within a category to trigger collapse\nPATTERN_COLLAPSE_TRIGGER = 5\n",
    )
    classifier_text = _replace_once(
        classifier_text,
        """def _duplicate_filenames(modules: List[Dict[str, object]]) -> Set[str]:
    counts: Dict[str, int] = {}
    for mod in modules:
        fp = str(mod["file_path"])
        fname = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        counts[fname] = counts.get(fname, 0) + 1
    return {fname for fname, count in counts.items() if count > 1}


def _collapse_patterns(
    cat_mods: List[Dict[str, object]],
    module_ids: Dict[str, str],
    duplicate_filenames: Set[str],
) -> tuple:
""",
        """def _collapse_patterns(
    cat_mods: List[Dict[str, object]],
    module_ids: Dict[str, str],
) -> tuple:
""",
    )
    for old, new in [
        (
            """                deps_internal=str(mod.get("deps_internal", "")),
                domain=str(mod.get("domain", "unknown")),
                duplicate_filenames=duplicate_filenames,
""",
            """                deps_internal=str(mod.get("deps_internal", "")),
""",
        ),
        (
            """            deps_internal=str(mod.get("deps_internal", "")),
            domain=str(mod.get("domain", "unknown")),
            duplicate_filenames=duplicate_filenames,
""",
            """            deps_internal=str(mod.get("deps_internal", "")),
""",
        ),
        (
            """        duplicate_filenames = _duplicate_filenames(cat_mods)
        individual, patterns = _collapse_patterns(cat_mods, module_ids, duplicate_filenames)
""",
            """        individual, patterns = _collapse_patterns(cat_mods, module_ids)
""",
        ),
        (
            "    duplicate_filenames = _duplicate_filenames(cat_mods)\n\n",
            "",
        ),
        (
            """            deps_internal=str(mod.get("deps_internal", "")),
            domain=str(mod.get("domain", "unknown")),
            duplicate_filenames=duplicate_filenames,
""",
            """            deps_internal=str(mod.get("deps_internal", "")),
""",
        ),
    ]:
        classifier_text = _replace_once(classifier_text, old, new)

    cli_path.write_text(cli_text, encoding="utf-8")
    classifier_path.write_text(classifier_text, encoding="utf-8")

    import subprocess

    for repo in TARGET_REPOS:
        subprocess.run(
            ["python", "cli.py", "bearings", "--repo-path", str(repo.relative_to(ROOT)), "--generate"],
            cwd=ROOT,
            check=True,
        )
    print("Applied baseline bearings logic and regenerated target repo bearings.")


def restore() -> None:
    if not BACKUP_DIR.exists():
        raise RuntimeError("No snapshot found. Run snapshot first.")
    _copy(BACKUP_DIR / "cli.py.current", ROOT / "cli.py")
    _copy(BACKUP_DIR / "classifier.py.current", ROOT / "ir" / "classifier.py")
    for repo in TARGET_REPOS:
        repo_backup = BACKUP_DIR / repo.name
        _copy(repo_backup / "bearings.md", repo / ".codeir" / "bearings.md")
        _copy(repo_backup / "bearings-summary.md", repo / ".codeir" / "bearings-summary.md")
        _copy(repo_backup / "bearings", repo / ".codeir" / "bearings")
    print("Restored current bearings code and artifacts from snapshot.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Swap baseline/refreshed bearings for A/B runs")
    parser.add_argument("action", choices=["snapshot", "apply-baseline", "restore"])
    args = parser.parse_args()

    if args.action == "snapshot":
        snapshot()
    elif args.action == "apply-baseline":
        apply_baseline()
    else:
        restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
