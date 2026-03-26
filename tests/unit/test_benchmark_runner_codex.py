"""Tests for Codex benchmark transcript parsing."""

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "_local" / "PromptBenchmarksAutorun" / "benchmark_runner.py"

spec = importlib.util.spec_from_file_location("benchmark_runner", MODULE_PATH)
benchmark_runner = importlib.util.module_from_spec(spec)
sys.modules["benchmark_runner"] = benchmark_runner
assert spec.loader is not None
spec.loader.exec_module(benchmark_runner)


def test_parse_codex_transcript_extracts_tools_tokens_and_final_response(tmp_path):
    transcript = tmp_path / "codex.jsonl"
    lines = [
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 120,
                        "cached_input_tokens": 30,
                        "output_tokens": 45,
                    }
                },
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "codeir search xyz.action", "workdir": "/tmp/repo"}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "parallel",
                "arguments": json.dumps(
                    {
                        "tool_uses": [
                            {
                                "recipient_name": "functions.exec_command",
                                "parameters": {"cmd": "codeir expand XYZ.01", "workdir": "/tmp/repo"},
                            },
                            {
                                "recipient_name": "functions.write_stdin",
                                "parameters": {"session_id": 7, "chars": ""},
                            },
                        ]
                    }
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** End Patch\n",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Working on it"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Done"}],
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    stats, tool_calls, bash_commands, final_response = benchmark_runner.parse_codex_transcript(transcript)

    assert stats.input_tokens == 120
    assert stats.cache_read_tokens == 30
    assert stats.output_tokens == 45
    assert tool_calls == {
        "exec_command": 2,
        "write_stdin": 1,
        "apply_patch": 1,
    }
    assert bash_commands == [
        "codeir search xyz.action",
        "codeir expand XYZ.01",
    ]
    assert final_response == "Done"

    invocations = benchmark_runner.extract_codeir_invocations(bash_commands)
    assert invocations == [
        {
            "command": "codeir search xyz.action",
            "subcommand": "search",
            "subject": "xyz.action",
            "positionals": ["xyz.action"],
        },
        {
            "command": "codeir expand XYZ.01",
            "subcommand": "expand",
            "subject": "XYZ.01",
            "positionals": ["XYZ.01"],
        },
    ]


def test_build_benchmark_prompt_adds_no_codeir_instruction():
    prompt = benchmark_runner.build_benchmark_prompt("Investigate the bug", no_codeir=True)

    assert "do not use the `codeir` CLI" in prompt
    assert prompt.endswith("Investigate the bug")


def test_detect_no_codeir_violations_flags_codeir_and_related_artifacts():
    commands = [
        "codeir search xyz.action",
        "sed -n '1,220p' /repo/.agents/skills/codeir/SKILL.md",
        "ls .codeir",
        "rg foo src",
    ]

    violations = benchmark_runner.detect_no_codeir_violations(commands)

    assert violations == [
        "codeir search xyz.action",
        "sed -n '1,220p' /repo/.agents/skills/codeir/SKILL.md",
        "ls .codeir",
    ]
