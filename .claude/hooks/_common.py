"""Shared helpers for the Claude Code hook scripts (standard library only).

Conventions:
- stdin is read as UTF-8 bytes (a Windows console may default to cp1252).
- Everything written to stderr is ASCII only, so no console encoding can crash a guard.
- The audit file is always opened as UTF-8 and written as ASCII-escaped JSON lines.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

MAX_STRING = 300
# Keys whose values are file content: only the length is ever logged.
CONTENT_KEYS = {"content", "new_string", "old_string", "new_source", "old_source"}


def read_payload() -> dict[str, Any]:
    """Parse the hook payload from stdin. Raises ValueError on anything but a JSON object."""
    # utf-8-sig also accepts a BOM, which PowerShell adds when piping text.
    raw = sys.stdin.buffer.read().decode("utf-8-sig")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("payload is not a JSON object")
    return data


def project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def audit_path() -> Path:
    override = os.environ.get("INCIDENT_AUDIT_LOG")
    if override:
        return Path(override)
    return project_dir() / "logs" / "audit.jsonl"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def truncate(text: str, limit: int = MAX_STRING) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[+{len(text) - limit} chars]"


def summarize_input(tool_input: Any) -> dict[str, Any]:
    """Compact, safe summary of tool_input: long strings cut, file content reduced to its length."""
    if not isinstance(tool_input, dict):
        return {"value": truncate(json.dumps(tool_input, default=str, ensure_ascii=False))}
    summary: dict[str, Any] = {}
    for key, value in tool_input.items():
        if key in CONTENT_KEYS and isinstance(value, str):
            summary[f"{key}_length"] = len(value)
        elif key == "edits" and isinstance(value, list):
            summary["edits_count"] = len(value)
            summary["edits_length"] = sum(
                len(v)
                for e in value
                if isinstance(e, dict)
                for v in e.values()
                if isinstance(v, str)
            )
        elif isinstance(value, str):
            summary[key] = truncate(value)
        elif value is None or isinstance(value, bool | int | float):
            summary[key] = value
        else:
            summary[key] = truncate(json.dumps(value, default=str, ensure_ascii=False))
    return summary


def append_audit(record: dict[str, Any]) -> None:
    """Append one JSON line to the audit log. Never raises."""
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass


def ascii_text(text: str) -> str:
    """Escape non-ASCII characters so the text is safe for any console encoding."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _stderr(message: str) -> None:
    try:
        sys.stderr.write(ascii_text(message) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def block(rule_id: str, message: str, payload: dict[str, Any]) -> NoReturn:
    """Record the blocked attempt in the audit log, explain on stderr and exit with code 2."""
    append_audit(
        {
            "ts": utc_now(),
            "session_id": payload.get("session_id"),
            "tool_name": payload.get("tool_name"),
            "decision": "blocked",
            "rule": rule_id,
            "input": summarize_input(payload.get("tool_input")),
        }
    )
    _stderr(message)
    sys.exit(2)


def fail_closed(hook: str, reason: str) -> NoReturn:
    """Guards refuse to continue when they cannot understand their input."""
    append_audit(
        {
            "ts": utc_now(),
            "hook": hook,
            "decision": "blocked",
            "rule": "invalid-input",
            "reason": ascii_text(reason),
        }
    )
    _stderr(
        f"BLOCKED by {hook}: {reason}. The hook fails closed when its input is not valid, "
        "so the tool call was not run. Do not retry blindly; tell the user about this error."
    )
    sys.exit(2)
