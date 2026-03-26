"""
codeir init — Generate agent instruction files for the detected environment.

Detects Claude Code, Codex, and OpenClaw (or any combination) and drops
the right skill/instruction file in the right place. Can also target a
specific platform with --platform.

Usage:
    codeir init                      # use repo markers, fall back to runtime
    codeir init --platform current   # use current runtime only
    codeir init --platform claude    # force Claude Code only
    codeir init --platform codex     # force Codex only
    codeir init --platform openclaw  # force OpenClaw only
    codeir init --platform all       # generate for all platforms
    codeir init --list               # show what would be generated
"""

from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple


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

        **Inspect** — compact behavior snapshots for one or more entities:
        ```
        codeir show <entity_id> [<entity_id> ...] [--level Index|Behavior]
        ```
        Use this to narrow candidates quickly. If you already know you need the
        full implementation, skip `show` and use `expand`.

        **Expand** — raw source when you need to edit or verify:
        ```
        codeir expand <entity_id>              # single entity
        codeir expand <entity_id> --number     # source with line numbers for citation
        codeir expand <id1> <id2> <id3>        # multiple entities in one call
        codeir expand 'STEM.*'                 # all siblings (STEM, STEM.01, STEM.02, ...)
        ```

        **Trace** — shortest static call path between two entities:
        ```
        codeir trace <from_entity> <to_entity> [--depth N] [--resolution import|local|fuzzy|any]
        ```
        Use this for path-shaped questions like "how does X trigger Y?" or "how do
        we get from this entry point to that hook?"

        **Callers** — what depends on an entity:
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


def _codex_instructions() -> str:
    """Return Codex-specific CodeIR guidance."""
    return textwrap.dedent("""\
        ## CodeIR — Compiled Codebase Representation

        This repository has a pre-built semantic index of the entire codebase.
        Instead of reading raw source files, use CodeIR to search, inspect, and
        trace entities at the abstraction level that matches your task.

        You must minimize total tool calls. Prefer one decisive tool call over
        several exploratory ones.

        For unfamiliar, cross-file, or architectural tasks, orient by running
        `codeir bearings` before search, grep, or expand.

        ### Commands

        **Bearings** — orient to the repo before narrowing:
        ```
        codeir bearings
        codeir bearings <category>
        codeir bearings --full
        ```
        Use this first when the task is unfamiliar, cross-cutting, or architectural.
        Bearings makes category-scoped search more effective.

        **Search** — find entities by name:
        ```
        codeir search <terms> [--category <cat>]
        ```
        After `bearings`, prefer `--category` to narrow to the most likely area.

        **Grep** — regex search across source, grouped by entity:
        ```
        codeir grep <pattern> [--path <dir_or_glob>] [--path <dir_or_glob>] [-i] [-C N] [-v]
        codeir grep <pattern> --evidence [--path <dir_or_glob>] [-i]
        codeir grep <pattern> --count [--path <dir_or_glob>] [--path <dir_or_glob>]
        ```
        Use this for census/pattern tasks where you need all occurrences, but want
        entity context alongside matches.
        Use `--evidence` instead of `rg -n ...` followed by `sed -n ...` when you
        want exact matching lines, nearby context, and the owning entity in one call.
        Use `--count` instead of `rg ... | wc -l` or `cut | sort | uniq -c` when you
        need grouped counts by entity/file without printing the match lines.

        **Inspect** — compact behavior snapshots for one or more entities:
        ```
        codeir show <entity_id> [<entity_id> ...] [--level Index|Behavior]
        ```
        Use this to narrow candidates quickly. If you already know you need the
        full implementation, skip `show` and use `expand`.

        **Expand** — raw source when you need to edit or verify:
        ```
        codeir expand <entity_id>              # single entity
        codeir expand <entity_id> --number     # source with line numbers for citation
        codeir expand <id1> <id2> <id3>        # multiple entities in one call
        codeir expand 'STEM.*'                 # all siblings (STEM, STEM.01, STEM.02, ...)
        ```

        **Trace** — shortest static call path between two entities:
        ```
        codeir trace <from_entity> <to_entity> [--depth N] [--resolution import|local|fuzzy|any]
        ```
        Use this for path-shaped questions like "how does X trigger Y?" or "how do
        we get from this entry point to that hook?"

        **Callers** — what depends on an entity:
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

        ### Three workflows

        **Show mode** — understanding tasks

        Use when the goal is to explain behavior, identify likely cause, or compare
        a few candidate entities.

        1. `codeir bearings` → orient
        2. `codeir search "..." --category <cat>` → find candidates
        3. `codeir show <id>` → read Behavior IR
        4. `codeir expand <id>` only if one finalist needs verification

        If `show` already answers the question, stop there.

        **Expand mode** — implementation tasks

        Use when the goal is to change code safely.

        1. `codeir bearings` → orient
        2. `codeir search "..." --category <cat>` → find likely edit targets
        3. `codeir show <id>` → confirm the right entity
        4. `codeir scope <id>` or `codeir callers <id>` if blast radius matters
        5. `codeir expand <id>` for the entities you will edit

        If you already expect to need full source, skip `show` and go straight to
        `expand`. Expand only the finalists you expect to modify.

        **Grep mode** — census tasks

        Use when the goal is to find patterns, conventions, or all occurrences
        across the repo.

        1. `codeir bearings` → orient
        2. `codeir grep "..." --path ...` → find matching entities
        3. `codeir show <id>` if you need behavior context
        4. `codeir expand <id>` only for representative examples

        Prefer `codeir grep` over raw text grep when entity ownership matters.
        Prefer `codeir grep --evidence` over `rg -n ...` then `sed -n ...` when you
        want exact lines and nearby proof without a separate source-read step.
        Use repeated `--path` flags instead of shell loops when you need one census
        across `lib`, `test`, `examples`, or `docs`.

        **Trace mode** — path questions

        Use when the goal is to connect an entry point to an effect, hook, or
        downstream behavior.

        1. `codeir bearings` → orient
        2. `codeir search "..." --category <cat>` → identify likely endpoints
        3. `codeir trace <from> <to>` → find the shortest static call path
        4. `codeir expand <id>` only for the path nodes that need verification

        Use `trace` instead of manually chaining `callers`, `search`, `grep`, and
        line-range reads when the task is primarily "how do we get from A to B?"

        ### Selection rules

        - You must minimize total tool calls. Prefer one decisive tool call over
          several exploratory ones.
        - Use `show` for a compact behavior snapshot only when it might change
          whether an entity is relevant.
        - Use `expand` when you already know you need the full implementation or
          expect to edit the entity.
        - Use `expand --number` when you need exact source lines with stable line
          numbers for citation or proof.
        - Do not `show` an entity immediately before `expand` unless the `show`
          result could change your decision.
        - Do not `expand` weak matches just to be sure. Keep narrowing with
          `search`, `grep`, or `show` until only a small finalist set remains.
        - After a multi-entity `show`, either discard a candidate or `expand` it.
          Do not `show` the same entity again individually unless the first output
          was incomplete.
        - Use `codeir grep --evidence` instead of `rg -n ...` followed by
          `sed -n ...` when you need exact matching lines plus nearby proof.

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

    def detect_runtime(self, env: Optional[Mapping[str, str]] = None) -> bool:
        """Return True if the active process environment looks like this platform."""
        return False

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

    def detect_runtime(self, env: Optional[Mapping[str, str]] = None) -> bool:
        runtime_env = env or os.environ
        codex_markers = (
            "CODEX_THREAD_ID",
            "CODEX_SHELL",
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
            "CODEX_SANDBOX",
            "CODEX_CI",
        )
        return any(runtime_env.get(marker) for marker in codex_markers)

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
        return frontmatter + _codex_instructions()


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


@dataclass(frozen=True)
class PlatformSelection:
    """Describe platform auto-selection for a given init invocation."""

    selected: List[Platform]
    repo_detected: List[Platform]
    runtime_detected: List[Platform]
    mode: str


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


def detect_runtime_platforms(env: Optional[Mapping[str, str]] = None) -> List[Platform]:
    """Return platforms inferred from the active runtime environment."""
    runtime_env = env or os.environ
    override = (runtime_env.get("CODEIR_CURRENT_PLATFORM") or "").strip().lower()
    if override:
        if override == "all":
            return list(ALL_PLATFORMS)
        platform = get_platform_by_name(override)
        return [platform] if platform else []

    return [p for p in ALL_PLATFORMS if p.detect_runtime(runtime_env)]


def get_platform_by_name(name: str) -> Optional[Platform]:
    """Get a platform by its short name."""
    for p in ALL_PLATFORMS:
        if p.name == name:
            return p
    return None


def select_platforms(
    repo_root: Path,
    requested_platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> PlatformSelection:
    """Resolve which platforms init should target.

    Selection precedence:
    1. Explicit --platform request
    2. Repo marker detection
    3. Runtime detection fallback
    """
    repo_detected = detect_platforms(repo_root)
    runtime_detected = detect_runtime_platforms(env)

    if requested_platform == "all":
        return PlatformSelection(list(ALL_PLATFORMS), repo_detected, runtime_detected, "all")

    if requested_platform == "current":
        return PlatformSelection(runtime_detected, repo_detected, runtime_detected, "current")

    if requested_platform:
        platform = get_platform_by_name(requested_platform)
        selected = [platform] if platform else []
        return PlatformSelection(selected, repo_detected, runtime_detected, "explicit")

    if repo_detected:
        return PlatformSelection(repo_detected, repo_detected, runtime_detected, "repo")

    return PlatformSelection(runtime_detected, repo_detected, runtime_detected, "runtime_fallback")


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
    print("--platform current to use the active runtime,")
    print("or --platform all to generate for all platforms.")
