"""Minimal tool runtime for CodeIR tool dispatch.

This is a baseline implementation for LLM tool integration.
Routes tool calls to CLI commands and returns structured JSON responses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _timestamp() -> str:
    """ISO timestamp for meta block."""
    return datetime.now(timezone.utc).isoformat()


def _make_response(
    tool: str,
    data: Dict[str, Any],
    warnings: Optional[List[str]] = None,
    suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build standard response shape."""
    return {
        "data": data,
        "meta": {
            "tool": tool,
            "timestamp": _timestamp(),
            "warnings": warnings or [],
            "suggestions": suggestions or [],
        },
    }


def _make_error(
    tool: str,
    code: str,
    message: str,
    suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build error response."""
    return {
        "data": None,
        "meta": {
            "tool": tool,
            "timestamp": _timestamp(),
            "warnings": [],
            "suggestions": suggestions or [],
        },
        "error": {
            "code": code,
            "message": message,
        },
    }


def _run_cli(cmd: List[str], repo_path: Optional[str] = None) -> tuple[int, str, str]:
    """Run CLI command and capture output."""
    full_cmd = [sys.executable, "cli.py"] + cmd
    if repo_path:
        full_cmd.extend(["--repo-path", repo_path])

    result = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    return result.returncode, result.stdout, result.stderr


# Track expand calls without prior callers check (for warning)
_session_state = {
    "checked_callers": set(),
}


def run_tool(tool_name: str, args: Dict[str, Any], repo_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Dispatch a tool call and return structured response.

    Args:
        tool_name: One of codeir_search, codeir_show, codeir_expand, etc.
        args: Tool-specific arguments as dict.
        repo_path: Optional repository path override.

    Returns:
        Structured response with data and meta blocks.
    """
    handlers = {
        "codeir_search": _handle_search,
        "codeir_show": _handle_show,
        "codeir_expand": _handle_expand,
        "codeir_callers": _handle_callers,
        "codeir_impact": _handle_impact,
        "codeir_scope": _handle_scope,
        "codeir_bearings": _handle_bearings,
        "codeir_grep": _handle_grep,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return _make_error(
            tool_name,
            "UNKNOWN_TOOL",
            f"Unknown tool: {tool_name}",
            suggestions=["codeir_search", "codeir_bearings"],
        )

    try:
        return handler(args, repo_path)
    except Exception as e:
        return _make_error(
            tool_name,
            "INTERNAL_ERROR",
            str(e),
        )


def _handle_search(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_search tool."""
    query = args.get("query")
    if not query:
        return _make_error("codeir_search", "MISSING_PARAM", "query is required")

    cmd = ["search", query]
    if args.get("category"):
        cmd.extend(["--category", args["category"]])
    if args.get("limit"):
        cmd.extend(["--limit", str(args["limit"])])

    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_search", "CLI_ERROR", stderr.strip())

    # Parse CLI output (simple line-based for now)
    results = []
    for line in stdout.strip().split("\n"):
        if line.strip():
            results.append({"raw": line.strip()})

    return _make_response(
        "codeir_search",
        {"results": results, "result_count": len(results)},
        suggestions=["codeir_show to inspect an entity"],
    )


def _handle_show(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_show tool."""
    entity_id = args.get("entity_id")
    if not entity_id:
        return _make_error("codeir_show", "MISSING_PARAM", "entity_id is required")

    cmd = ["show", entity_id]
    if args.get("level"):
        cmd.extend(["--level", args["level"]])

    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_show", "CLI_ERROR", stderr.strip())

    return _make_response(
        "codeir_show",
        {"entity_id": entity_id, "ir_text": stdout.strip()},
        suggestions=["codeir_expand for source", "codeir_callers for dependencies"],
    )


def _handle_expand(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_expand tool with dependency check warning."""
    entity_id = args.get("entity_id")
    if not entity_id:
        return _make_error("codeir_expand", "MISSING_PARAM", "entity_id is required")

    cmd = ["expand", entity_id]
    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_expand", "CLI_ERROR", stderr.strip())

    # Risk signal: warn if expanded without checking callers first
    warnings = []
    if entity_id not in _session_state["checked_callers"]:
        warnings.append("Expanded source without prior dependency check")

    return _make_response(
        "codeir_expand",
        {"entity_id": entity_id, "source": stdout.strip()},
        warnings=warnings,
        suggestions=["codeir_callers to check dependencies before editing"],
    )


def _handle_callers(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_callers tool."""
    entity_id = args.get("entity_id")
    if not entity_id:
        return _make_error("codeir_callers", "MISSING_PARAM", "entity_id is required")

    # Track that we checked callers for this entity
    _session_state["checked_callers"].add(entity_id)

    cmd = ["callers", entity_id]
    if args.get("resolution"):
        cmd.extend(["--resolution", args["resolution"]])

    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_callers", "CLI_ERROR", stderr.strip())

    # Parse output to separate resolved callers from ambiguous
    lines = stdout.strip().split("\n")
    warnings = []
    suggestions = ["codeir_impact for full dependency analysis"]
    ambiguous_calls = []

    # Find the ambiguous section if present
    ambiguous_start = None
    suggestions_start = None
    for i, line in enumerate(lines):
        if line.startswith("⚠ Ambiguous"):
            ambiguous_start = i
        elif line.startswith("💡 Suggestions"):
            suggestions_start = i

    # Extract resolved callers (before ambiguous section)
    caller_end = ambiguous_start if ambiguous_start else len(lines)
    caller_lines = [l for l in lines[:caller_end] if l.strip() and not l.startswith("No callers")]

    # Extract ambiguous info
    if ambiguous_start and suggestions_start:
        # Parse ambiguous header for count
        header = lines[ambiguous_start]
        import re
        match = re.search(r'\((\d+) potential callers, (\d+) entities', header)
        if match:
            potential_count = int(match.group(1))
            entity_count = int(match.group(2))
            # Extract entity name from header
            name_match = re.search(r"named '(\w+)'", header)
            entity_name = name_match.group(1) if name_match else "unknown"

            ambiguous_calls.append({
                "call": f"*.{entity_name}",
                "resolution": "ambiguous",
                "candidate_count": entity_count,
                "potential_callers": potential_count,
            })

            warnings.append(f"Call to '{entity_name}' has {entity_count} possible targets; {potential_count} potential callers unresolved")
            suggestions.insert(0, f"codeir_grep '\\.{entity_name}\\(' to find actual callers")

    if len(caller_lines) > 10:
        warnings.append(f"High caller count ({len(caller_lines)}) — changes may have wide impact")

    data = {
        "entity_id": entity_id,
        "callers_raw": "\n".join(lines[:caller_end]),
        "caller_count": len(caller_lines),
    }
    if ambiguous_calls:
        data["ambiguous"] = ambiguous_calls

    return _make_response("codeir_callers", data, warnings=warnings, suggestions=suggestions)


def _handle_impact(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_impact tool."""
    entity_id = args.get("entity_id")
    if not entity_id:
        return _make_error("codeir_impact", "MISSING_PARAM", "entity_id is required")

    cmd = ["impact", entity_id]
    if args.get("depth"):
        cmd.extend(["--depth", str(args["depth"])])

    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_impact", "CLI_ERROR", stderr.strip())

    return _make_response(
        "codeir_impact",
        {"entity_id": entity_id, "impact_raw": stdout.strip()},
        suggestions=["codeir_scope for edit context"],
    )


def _handle_scope(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_scope tool."""
    entity_id = args.get("entity_id")
    if not entity_id:
        return _make_error("codeir_scope", "MISSING_PARAM", "entity_id is required")

    # Track as checked (scope includes callers)
    _session_state["checked_callers"].add(entity_id)

    cmd = ["scope", entity_id]
    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_scope", "CLI_ERROR", stderr.strip())

    return _make_response(
        "codeir_scope",
        {"entity_id": entity_id, "scope_raw": stdout.strip()},
        suggestions=["codeir_expand when ready to edit"],
    )


def _handle_bearings(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_bearings tool."""
    cmd = ["bearings"]
    if args.get("category"):
        cmd.append(args["category"])
    if args.get("full"):
        cmd.append("--full")

    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_bearings", "CLI_ERROR", stderr.strip())

    return _make_response(
        "codeir_bearings",
        {"bearings_raw": stdout.strip()},
        suggestions=["codeir_search to find specific entities"],
    )


def _handle_grep(args: Dict[str, Any], repo_path: Optional[str]) -> Dict[str, Any]:
    """Handle codeir_grep tool."""
    pattern = args.get("pattern")
    if not pattern:
        return _make_error("codeir_grep", "MISSING_PARAM", "pattern is required")

    cmd = ["grep", pattern]
    if args.get("path"):
        cmd.extend(["--path", args["path"]])
    if args.get("ignore_case"):
        cmd.append("-i")
    if args.get("context_lines"):
        cmd.extend(["-C", str(args["context_lines"])])

    code, stdout, stderr = _run_cli(cmd, repo_path)

    if code != 0:
        return _make_error("codeir_grep", "CLI_ERROR", stderr.strip())

    return _make_response(
        "codeir_grep",
        {"grep_raw": stdout.strip()},
        warnings=["grep is a fallback — prefer codeir_search for structured queries"],
        suggestions=["codeir_search for semantic search"],
    )


def reset_session():
    """Reset session state (for testing)."""
    _session_state["checked_callers"].clear()


# Simple CLI interface for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python runtime.py <tool_name> '<json_args>'")
        print("Example: python runtime.py codeir_search '{\"query\": \"auth\"}'")
        sys.exit(1)

    tool = sys.argv[1]
    args = json.loads(sys.argv[2])
    result = run_tool(tool, args)
    print(json.dumps(result, indent=2))
