"""The hooks of the project must work inside the sanitized working copy, which has no `src/`.

Each test builds the copy from the real project root and runs the real hook script from it as a
subprocess, the way Claude Code does: cwd and CLAUDE_PROJECT_DIR are the copy and the payload is
JSON on stdin.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from incident_agent.evaluation import workcopy

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "eval"
SESSION = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def copy(tmp_path) -> Path:
    work = tmp_path / "work"
    workcopy.build_workcopy(ROOT, work)
    return work


@pytest.fixture
def audit(tmp_path) -> Path:
    return tmp_path / "audit.jsonl"


def run_hook(copy: Path, audit: Path, name: str, payload: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "SLACK_WEBHOOK_URL"}}
    env.update(
        CLAUDE_PROJECT_DIR=str(copy), INCIDENT_AUDIT_LOG=str(audit), INCIDENT_NOTIFY_DESKTOP="0"
    )
    return subprocess.run(
        [sys.executable, str(copy / ".claude" / "hooks" / f"{name}.py")],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=copy,
        timeout=60,
        check=False,
    )


def payload(copy: Path, event: str, tool: str | None = None, **extra) -> dict:
    data = {
        "session_id": SESSION,
        "transcript_path": str(copy / "transcript.jsonl"),
        "cwd": str(copy),
        "hook_event_name": event,
        **extra,
    }
    if tool:
        data["tool_name"] = tool
    return data


def audit_lines(audit: Path) -> list[dict]:
    if not audit.exists():
        return []
    return [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]


# --- the copy has no src/, and the hooks do not need it --------------------------------------


def test_copy_has_no_src_and_no_hook_imports_the_package(copy):
    assert not (copy / "src").exists()
    for hook in sorted((copy / ".claude" / "hooks").glob("*.py")):
        tree = ast.parse(hook.read_text(encoding="utf-8"))
        imported = {
            a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for a in n.names
        } | {
            n.module.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module
        }
        assert "incident_agent" not in imported, hook.name


def test_every_hook_command_of_settings_json_points_at_a_file_of_the_copy(copy):
    settings = json.loads((copy / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for groups in settings["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    ]
    assert len(commands) == 5
    prefix = 'uv run --no-project --quiet python "$CLAUDE_PROJECT_DIR/.claude/hooks/'
    for command in commands:
        assert command.startswith(prefix) and command.endswith('.py"'), command
        script = command[len(prefix) : -1]
        assert (copy / ".claude" / "hooks" / script).is_file(), script


# --- the guards ------------------------------------------------------------------------------


def test_block_destructive_blocks_in_the_copy(copy, audit):
    bad = payload(
        copy,
        "PreToolUse",
        "Bash",
        tool_input={"command": "rm -rf reports", "description": "clean up"},
    )
    result = run_hook(copy, audit, "block_destructive", bad)
    assert result.returncode == 2
    assert (
        b"BLOCKED by block_destructive" in result.stderr and b"rm-recursive-force" in result.stderr
    )
    (line,) = audit_lines(audit)
    assert line["decision"] == "blocked" and line["rule"] == "rm-recursive-force"

    fine = payload(copy, "PreToolUse", "Bash", tool_input={"command": "ls reports"})
    result = run_hook(copy, audit, "block_destructive", fine)
    assert result.returncode == 0 and result.stderr == b""


def test_protect_files_blocks_the_database_and_allows_reports(copy, audit):
    db = payload(
        copy,
        "PreToolUse",
        "Write",
        tool_input={"file_path": str(copy / "data" / "incident.db"), "content": "x"},
    )
    result = run_hook(copy, audit, "protect_files", db)
    assert result.returncode == 2 and b"BLOCKED by protect_files" in result.stderr
    ok = payload(
        copy,
        "PreToolUse",
        "Write",
        tool_input={"file_path": str(copy / "reports" / "2026-03-12-x.md"), "content": "x"},
    )
    assert run_hook(copy, audit, "protect_files", ok).returncode == 0


def write_audit(copy: Path, audit: Path, report: Path) -> None:
    """The line the audit hook writes for a Write of the report (same session as the Stop hook)."""
    event = payload(
        copy,
        "PostToolUse",
        "Write",
        tool_input={"file_path": str(report), "content": report.read_text(encoding="utf-8")},
        tool_response={"type": "create", "filePath": str(report)},
    )
    assert run_hook(copy, audit, "audit_log", event).returncode == 0


def test_check_report_blocks_an_incomplete_report_and_passes_a_complete_one(copy, audit):
    report = copy / "reports" / "2026-03-12-x.md"
    report.write_text("# Incident\n\n## Summary\nSomething broke.\n", encoding="utf-8")
    write_audit(copy, audit, report)
    stop = payload(copy, "Stop", stop_hook_active=False)

    result = run_hook(copy, audit, "check_report", stop)
    assert result.returncode == 2
    assert b"BLOCKED by check_report (rule report-incomplete)" in result.stderr
    assert b"Missing sections" in result.stderr and b"Root cause" in result.stderr
    assert audit_lines(audit)[-1]["rule"] == "report-incomplete"

    # one correction round only: the hook lets Claude stop when it already blocked once
    again = payload(copy, "Stop", stop_hook_active=True)
    assert run_hook(copy, audit, "check_report", again).returncode == 0

    report.write_text((FIXTURES / "good-checkout-500.md").read_text("utf-8"), encoding="utf-8")
    write_audit(copy, audit, report)
    assert run_hook(copy, audit, "check_report", stop).returncode == 0


def test_check_report_lets_claude_stop_when_no_report_was_written(copy, audit):
    stop = payload(copy, "Stop", stop_hook_active=False)
    audit.write_text("", encoding="utf-8")
    assert run_hook(copy, audit, "check_report", stop).returncode == 0


# --- audit and notify ------------------------------------------------------------------------


def test_audit_hook_appends_the_line_the_scan_reads(copy, audit):
    event = payload(
        copy,
        "PostToolUse",
        "Read",
        tool_input={"file_path": str(copy / "demo_app" / "payment" / "charge.py")},
        tool_response={"file": {"content": "..."}},
    )
    assert run_hook(copy, audit, "audit_log", event).returncode == 0
    (line,) = audit_lines(audit)
    assert line["tool_name"] == "Read" and line["session_id"] == SESSION
    assert line["input"]["file_path"].endswith("charge.py")


def test_notify_is_silent_and_exits_zero(copy, audit):
    event = payload(copy, "Notification", message="Claude needs your input")
    result = run_hook(copy, audit, "notify", event)
    assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
