"""A fake `claude` for the run-eval tests: no network, no model.

Started as `python tests/fake_claude.py <claude flags>` with the prompt on stdin, in the working
copy of the run. Behavior comes from FAKE_CLAUDE_MODE: good, bad (wrong severity), none (no
report), error (is_error result), exit1 (non-zero exit), slow (sleeps), overwrite (writes
REPORT_NAME), leak (good report plus a Read of src/ in the audit log), denied (good report plus
a permission denial in the result), limit (usage limit from call FAKE_CLAUDE_LIMIT_AT on, earlier
calls are good; FAKE_CLAUDE_LIMIT_COST, FAKE_CLAUDE_LIMIT_REPORT=1 leaves a partial report,
FAKE_CLAUDE_LIMIT_TEXT overrides the result text), text-limit (limit words in the answer and the
report, no error), other-429 (rate_limit_error), other-500 (api_error whose text says session
limit). Every call is appended to FAKE_CLAUDE_CALLS_FILE, because the
working copy is deleted after the run.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eval"
REPORT_NAME = "2026-03-12-fake-incident.md"


def error_result(status: int, code: str, text: str, cost: float) -> dict:
    """The shape claude -p prints on an API error (observed with Claude Code 2.1.302, sanitized)."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": status,
        "api_error_origin": "first_party",
        "api_error": code,
        "terminal_reason": "api_error",
        "total_cost_usd": cost,
        "num_turns": 1,
        "duration_ms": 382,
        "result": text,
        "permission_denials": [],
    }


def main() -> int:
    prompt = sys.stdin.read()
    mode = os.environ.get("FAKE_CLAUDE_MODE", "good")
    cwd = Path.cwd()
    call = {
        "argv": sys.argv[1:],
        "stdin": prompt,
        "env": {
            k: os.environ.get(k)
            for k in (
                "INCIDENT_DB_PATH",
                "INCIDENT_AUDIT_LOG",
                "INCIDENT_NOTIFY_DESKTOP",
                "SLACK_WEBHOOK_URL",
            )
        },
        "db_exists": Path(os.environ.get("INCIDENT_DB_PATH", "")).is_file(),
        "cwd": str(cwd),
        "files": sorted(p.relative_to(cwd).as_posix() for p in cwd.rglob("*") if p.is_file()),
    }
    calls = Path(os.environ["FAKE_CLAUDE_CALLS_FILE"])
    with calls.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(call) + "\n")

    if mode == "slow":
        time.sleep(30)
    if mode == "exit1":
        print("boom", file=sys.stderr)
        return 1

    scenario = "checkout-500" if "checkout" in prompt else "db-pool"
    if mode == "limit":
        number = sum(1 for _ in calls.read_text(encoding="utf-8").splitlines())
        if number >= int(os.environ.get("FAKE_CLAUDE_LIMIT_AT", "1")):
            if os.environ.get("FAKE_CLAUDE_LIMIT_REPORT") == "1":
                (cwd / "reports").mkdir(exist_ok=True)
                text = (FIXTURES / f"good-{scenario}.md").read_text(encoding="utf-8")
                (cwd / "reports" / f"2026-03-12-{scenario}-partial.md").write_text(
                    text, encoding="utf-8"
                )
            if os.environ.get("FAKE_CLAUDE_LIMIT_LEAK") == "1":
                audit = {
                    "tool_name": "Read",
                    "input": {"file_path": "src/incident_agent/data/scenarios.py"},
                    "result": {"status": "ok", "size": 100},
                }
                with Path(os.environ["INCIDENT_AUDIT_LOG"]).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(audit) + "\n")
            default = "You've hit your session limit · resets 3:30pm (Europe/Berlin)"
            print(
                json.dumps(
                    error_result(
                        429,
                        "usage_limit_reached",
                        os.environ.get("FAKE_CLAUDE_LIMIT_TEXT", default),
                        float(os.environ.get("FAKE_CLAUDE_LIMIT_COST", "0")),
                    )
                )
            )
            return 1
        mode = "good"
    if mode == "other-429":
        print(json.dumps(error_result(429, "rate_limit_error", "Rate limited, retry later", 0.0)))
        return 1
    if mode == "other-500":
        text = "Internal error while handling your session limit"
        print(json.dumps(error_result(500, "api_error", text, 0.0)))
        return 1
    if mode == "text-limit":
        mode = "good"
        words = "usage limit reached, resets 3:30pm"
    else:
        words = "done"
    if mode in {"good", "bad", "error", "overwrite", "leak", "denied"}:
        text = (FIXTURES / f"good-{scenario}.md").read_text(encoding="utf-8")
        if words != "done":
            text += f"\nNote: the {words}.\n"
        if mode == "bad":
            text = re.sub(r"SEV-\d", "SEV-4", text)
        reports = cwd / "reports"
        reports.mkdir(exist_ok=True)
        name = REPORT_NAME if mode == "overwrite" else f"2026-03-12-{scenario}-fake.md"
        (reports / name).write_text(text, encoding="utf-8")

    if mode == "leak":
        # the shape audit_log.py writes: a Read that succeeded on an answer-key location
        audit = {
            "ts": "2026-10-18T10:00:00.000Z",
            "session_id": "fake",
            "tool_name": "Read",
            "input": {"file_path": "src/incident_agent/data/scenarios.py"},
            "result": {"status": "ok", "size": 100},
        }
        with Path(os.environ["INCIDENT_AUDIT_LOG"]).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit) + "\n")

    denied = [{"tool_name": "Read", "tool_input": {"file_path": "/elsewhere/answer.md"}}]
    result = {
        "type": "result",
        "subtype": "error_max_budget_usd" if mode == "error" else "success",
        "is_error": mode == "error",
        "total_cost_usd": float(os.environ.get("FAKE_CLAUDE_COST", "0.5")),
        "num_turns": 7,
        "duration_ms": 1234,
        "result": words,
        "permission_denials": denied if mode == "denied" else [],
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
