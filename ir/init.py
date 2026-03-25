"""
codeir init — Generate agent instruction files for the detected environment.

Detects Claude Code, Codex, and OpenClaw (or any combination) and drops
the right skill/instruction file in the right place. Can also target a
specific platform with --platform.

Usage:
    codeir init                     # auto-detect and generate
    codeir init --platform claude   # force Claude Code only
    codeir init --platform codex    # force Codex only
    codeir init --platform openclaw # force OpenClaw only
    codeir init --platform all      # generate for all platforms
    codeir init --list              # show what would be generated
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Shared instruction content
# ---------------------------------------------------------------------------
# The core instructions are platform-agnostic. Each platform wrapper adds
# its own frontmatter/metadata and adjusts file paths, but the body is
# identical — one source of truth.

def _core_instructions() -> str:
    """Return the platform-agnostic CodeIR instruction body.

    This is the single source of truth. Platform wrappers prepend their
    own metadata/frontmatter but share this body verbatim.
    """
    return textwrap.dedent("""\
        ## CodeIR — Compiled Codebase Representation

        This repository has a pre-built semantic index of the entire codebase.
        Instead of reading raw source files, use CodeIR to search, inspect, and
        trace entities at the abstraction level that matches your task.

        **Strategy:** Form a hypothesis early. After 2–3 lookups, propose it.
        Use `expand` to verify on the specific entity, then act. Don't map the
        full call graph to confirm what you can already see.

        ### Commands

        **Search** — find entities by name:
        ```
        codeir search <terms> [--category <cat>]
        ```

        **Grep** — regex search across source, grouped by entity:
        ```
        codeir grep <pattern> [--path <dir_or_glob>] [-i] [-C N] [-v]
        ```

        **Inspect** — what an entity does, without reading source:
        ```
        codeir show <entity_id> [--level Index|Behavior]
        ```

        **Expand** — raw source when you need to edit or verify:
        ```
        codeir expand <entity_id>              # single entity
        codeir expand <id1> <id2> <id3>        # multiple entities in one call
        codeir expand 'STEM.*'                 # all siblings (STEM, STEM.01, STEM.02, ...)
        ```

        **Trace** — what depends on an entity:
        ```
        codeir callers <entity_id>
        ```

        **Impact** — reverse dependency analysis:
        ```
        codeir impact <entity_id> [--depth N]
        ```

        **Scope** — minimal context to safely modify an entity:
        ```
        codeir scope <entity_id>
        ```

        ### Annotated entity lists

        Output from `callers`, `impact`, and `scope` includes inline triage metadata:
        ```
          CMPT.02         [47 callers] →ModelSQL   core_logic/tax.py      [class, ~180 lines]
          GTMVLN.03       [3 callers]              core_logic/move.py     [method, ~25 lines]
        ```

        - `[N callers]` — connectivity/importance
        - `→Pattern` — pattern membership (standard infrastructure)
        - `[kind, ~N lines]` — entity type and size

        Results are smart-sorted (high-caller core logic first, tests last) and
        truncated to 15 by default. Use `--all` to see the complete list.

        ### Reading compressed representations

        Behavior fields:
        - `FN` / `CLS` / `MT` / `AMT` — function, class, method, async method
        - `C=` — calls made
        - `F=` — flags: `R`=returns, `E`=raises, `I`=conditionals, `L`=loops, `T`=try/except, `W`=with
        - `A=` — assignment count
        - `B=` — base class
        - `#TAG` — domain and category tags

        ### Workflow

        1. `codeir search "flush"` → find relevant entities
        2. `codeir show FLSH.04` → read behavior IR
        3. `codeir expand FLSH.04` → verify hypothesis in source, then act

        Use `callers`, `impact`, and `scope` when planning changes and you need
        to understand blast radius. They're safety checks, not mandatory steps.
    """)


# ---------------------------------------------------------------------------
# Platform definitions
# ---------------------------------------------------------------------------

class Platform:
    """Base class for a target agent platform."""

    name: str = ""
    display_name: str = ""

    def detect(self, repo_root: Path) -> bool:
        """Return True if this platform's config directory exists."""
        raise NotImplementedError

    def target_path(self, repo_root: Path) -> Path:
        """Return the path where the instruction file should be written."""
        raise NotImplementedError

    def render(self) -> str:
        """Return the full file content including any platform-specific wrapper."""
        raise NotImplementedError


class ClaudeCode(Platform):
    name = "claude"
    display_name = "Claude Code"

    def detect(self, repo_root: Path) -> bool:
        return (repo_root / ".claude").is_dir()

    def target_path(self, repo_root: Path) -> Path:
        return repo_root / ".claude" / "rules" / "codeir.md"

    def render(self) -> str:
        # Claude Code rules files are plain markdown — no frontmatter needed.
        # .claude/rules/ files are auto-loaded at session start.
        return _core_instructions()


class Codex(Platform):
    name = "codex"
    display_name = "Codex"

    def detect(self, repo_root: Path) -> bool:
        # Codex uses .codex/ for config and .agents/skills/ for repo skills.
        # Either signals a Codex-enabled project.
        return (
            (repo_root / ".codex").is_dir()
            or (repo_root / ".agents").is_dir()
            or (repo_root / "AGENTS.md").exists()
        )

    def target_path(self, repo_root: Path) -> Path:
        # Repo-scoped skills live in .agents/skills/<name>/SKILL.md
        return repo_root / ".agents" / "skills" / "codeir" / "SKILL.md"

    def render(self) -> str:
        frontmatter = textwrap.dedent("""\
            ---
            name: codeir
            description: >
              Use this skill when exploring, understanding, searching, or modifying
              code in this repository. CodeIR provides a pre-built semantic index of
              the entire codebase — search by name, grep by content, inspect behavior
              summaries, trace callers and impact, and expand to source only when needed.
              Triggers: any code navigation, architecture questions, bug investigation,
              refactoring planning, or dependency analysis. Do NOT use for non-code tasks.
            ---

        """)
        return frontmatter + _core_instructions()


class OpenClaw(Platform):
    name = "openclaw"
    display_name = "OpenClaw"

    def detect(self, repo_root: Path) -> bool:
        # OpenClaw workspace skills live in <workspace>/skills/
        # The global config dir is ~/.openclaw/ but for repo-scoped use,
        # we look for a skills/ dir or openclaw.json at the repo root.
        return (
            (repo_root / ".openclaw").is_dir()
            or (repo_root / "openclaw.json").exists()
        )

    def target_path(self, repo_root: Path) -> Path:
        # Workspace-scoped skills: <workspace>/skills/<name>/SKILL.md
        # This follows the AgentSkills spec that OpenClaw adopted.
        return repo_root / "skills" / "codeir" / "SKILL.md"

    def render(self) -> str:
        # OpenClaw uses AgentSkills-compatible SKILL.md with YAML frontmatter.
        # The metadata block tells OpenClaw what the skill needs to run.
        frontmatter = textwrap.dedent("""\
            ---
            name: codeir
            description: >
              Use this skill when exploring, understanding, searching, or modifying
              code in this repository. CodeIR provides a pre-built semantic index of
              the entire codebase — search by name, grep by content, inspect behavior
              summaries, trace callers and impact, and expand to source only when needed.
            metadata:
              openclaw:
                emoji: "🔬"
                requires:
                  bins:
                    - codeir
            ---

        """)
        return frontmatter + _core_instructions()


# Registry of all supported platforms
ALL_PLATFORMS: List[Platform] = [ClaudeCode(), Codex(), OpenClaw()]


# ---------------------------------------------------------------------------
# Detection and generation logic
# ---------------------------------------------------------------------------

def find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk up from `start` (default: cwd) to find a git root, or return start."""
    current = (start or Path.cwd()).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    # No git root found — use the starting directory
    return (start or Path.cwd()).resolve()


def detect_platforms(repo_root: Path) -> List[Platform]:
    """Return platforms whose config directories exist under repo_root."""
    return [p for p in ALL_PLATFORMS if p.detect(repo_root)]


def get_platform_by_name(name: str) -> Optional[Platform]:
    """Get a platform by its short name."""
    for p in ALL_PLATFORMS:
        if p.name == name:
            return p
    return None


def generate_instructions(
    repo_root: Path,
    platforms: List[Platform],
    dry_run: bool = False,
    force: bool = False,
) -> List[Tuple[Platform, Path, str]]:
    """Generate instruction files for the given platforms.

    Returns a list of (platform, path, status) tuples where status is one of:
    'created', 'exists', 'overwritten', or 'would_create'.
    """
    results = []
    for platform in platforms:
        target = platform.target_path(repo_root)

        if dry_run:
            status = "would_create" if not target.exists() else "would_overwrite"
            results.append((platform, target, status))
            continue

        if target.exists() and not force:
            results.append((platform, target, "exists"))
            continue

        # Create parent directories
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        content = platform.render()
        target.write_text(content, encoding="utf-8")

        status = "overwritten" if target.exists() else "created"
        results.append((platform, target, "created"))

    return results


def print_detection_help() -> None:
    """Print help message when no platforms are detected."""
    print("No agent environments detected.")
    print()
    print("Looked for:")
    print("  Claude Code  ->  .claude/")
    print("  Codex        ->  .codex/ or .agents/ or AGENTS.md")
    print("  OpenClaw     ->  .openclaw/ or openclaw.json")
    print()
    print("Use --platform <name> to generate for a specific platform,")
    print("or --platform all to generate for all platforms.")
