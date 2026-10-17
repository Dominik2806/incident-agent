"""A fake `claude` for the run-eval tests: no network, no model.

Started as `python tests/fake_claude.py <claude flags>` with the prompt on stdin, in the project
root of the test. Behavior comes from FAKE_CLAUDE_MODE: good, bad (wrong severity), none (no
report), error (is_error result), exit1 (non-zero exit), slow (sleeps), overwrite (writes
REPORT_NAME).
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
    }
    calls = cwd / "fake-calls.jsonl"
    with calls.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(call) + "\n")

    if mode == "slow":
        time.sleep(30)
    if mode == "exit1":
        print("boom", file=sys.stderr)
        return 1

    scenario = "checkout-500" if "checkout" in prompt else "db-pool"
    if mode in {"good", "bad", "error", "overwrite"}:
        text = (FIXTURES / f"good-{scenario}.md").read_text(encoding="utf-8")
        if mode == "bad":
            text = re.sub(r"SEV-\d", "SEV-4", text)
        reports = cwd / "reports"
        reports.mkdir(exist_ok=True)
        name = REPORT_NAME if mode == "overwrite" else f"2026-03-12-{scenario}-fake.md"
        (reports / name).write_text(text, encoding="utf-8")

    result = {
        "type": "result",
        "subtype": "error_max_budget_usd" if mode == "error" else "success",
        "is_error": mode == "error",
        "total_cost_usd": float(os.environ.get("FAKE_CLAUDE_COST", "0.5")),
        "num_turns": 7,
        "duration_ms": 1234,
        "result": "done",
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
