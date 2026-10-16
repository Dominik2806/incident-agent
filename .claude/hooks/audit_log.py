"""PostToolUse hook (all tools): append one JSON line per tool call to logs/audit.jsonl.

Never blocks and never fails: every error is swallowed and the exit code is always 0.
The path can be overridden with the environment variable INCIDENT_AUDIT_LOG.
Only a compact summary of tool_input and the status and size of tool_response are logged;
file content and full tool responses are never written.
"""

from __future__ import annotations

import json
from typing import Any

import _common


def result_summary(response: Any) -> dict[str, Any]:
    if response is None:
        return {"status": "unknown", "size": 0}
    failed = isinstance(response, dict) and any(
        response.get(key) for key in ("is_error", "isError", "interrupted", "error")
    )
    try:
        size = len(json.dumps(response, default=str, ensure_ascii=False))
    except Exception:
        size = len(str(response))
    return {"status": "error" if failed else "ok", "size": size}


def main() -> None:
    try:
        payload = _common.read_payload()
        _common.append_audit(
            {
                "ts": _common.utc_now(),
                "session_id": payload.get("session_id"),
                "tool_name": payload.get("tool_name"),
                "input": _common.summarize_input(payload.get("tool_input")),
                "result": result_summary(payload.get("tool_response")),
            }
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
