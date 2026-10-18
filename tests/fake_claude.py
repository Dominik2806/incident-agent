"""A fake `claude` for the run-eval tests: no network, no model.

Started as `python tests/fake_claude.py <claude flags>` with the prompt on stdin, in the working
copy of the run. Behavior comes from FAKE_CLAUDE_MODE: good, bad (wrong severity), none (no
report), error (is_error result), exit1 (non-zero exit), slow (sleeps), overwrite (writes
REPORT_NAME), leak (good report plus a Read of src/ in the audit log), denied (good report plus
a permission denial in the result). Every call is appended to FAKE_CLAUDE_CALLS_FILE, because the
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
    if mode in {"good", "bad", "error", "overwrite", "leak", "denied"}:
        text = (FIXTURES / f"good-{scenario}.md").read_text(encoding="utf-8")
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
        "result": "done",
        "permission_denials": denied if mode == "denied" else [],
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
