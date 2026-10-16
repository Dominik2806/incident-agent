"""Tests for the Notification hook (notify.py) and the Stop hook (check_report.py)."""

import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from test_hooks import (
    CHECK_REPORT,
    GARBAGE,
    NOTIFY,
    ROOT,
    read_log,
    run_hook,
    stderr_of,
)

TEMPLATE = ROOT / ".claude" / "skills" / "incident-runbook" / "template.md"
EXAMPLE_REPORT = ROOT / "docs" / "examples" / "incident-report.md"
TEMPLATE_HEADINGS = re.findall(r"^## (.+?)\s*$", TEMPLATE.read_text(encoding="utf-8"), re.M)
SECRET_PATH = "/services/T000/B000/SECRET-TOKEN-123"
SESSION = "sess-stop"


@pytest.fixture
def audit_log(tmp_path):
    return tmp_path / "audit.jsonl"


# --- notify -----------------------------------------------------------------------------------


def run_notify(payload, project, env_extra=None, audit_log=None):
    env = {"INCIDENT_NOTIFY_DESKTOP": "0", "SLACK_WEBHOOK_URL": ""}
    env.update(env_extra or {})
    return run_hook(NOTIFY, payload, project, audit_log, env)


def notification(message="Claude needs your permission"):
    return {
        "session_id": "sess-1",
        "hook_event_name": "Notification",
        "message": message,
        "title": "Permission needed",
        "notification_type": "permission_prompt",
    }


class Receiver:
    """Local HTTP server that records POST bodies, optionally never answering."""

    def __init__(self, hang=False):
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.bodies.append((self.path, self.rfile.read(length)))
                if hang:
                    time.sleep(10)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}{SECRET_PATH}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    server = Receiver()
    yield server
    server.close()


def assert_silent(result):
    assert result.returncode == 0
    assert result.stdout == b"" and result.stderr == b""


def test_notify_sends_json_to_slack_and_never_leaks_the_url(tmp_path, audit_log, receiver):
    result = run_notify(
        notification("Claude needs your permission to run Bash"),
        tmp_path,
        {"SLACK_WEBHOOK_URL": receiver.url},
        audit_log,
    )
    assert_silent(result)
    ((path, body),) = receiver.bodies
    assert path == SECRET_PATH
    assert json.loads(body) == {
        "text": f"[{tmp_path.name}] Claude needs your permission to run Bash"
    }
    assert b"SECRET-TOKEN" not in body
    assert not audit_log.exists()


def test_notify_truncates_the_message_to_300_characters(tmp_path, receiver):
    run_notify(notification("x" * 1000), tmp_path, {"SLACK_WEBHOOK_URL": receiver.url})
    ((_, body),) = receiver.bodies
    text = json.loads(body)["text"]
    assert len(text) == 300
    assert text.endswith("...")


def test_notify_without_webhook_has_no_network_access_and_no_output(tmp_path, receiver):
    assert_silent(run_notify(notification(), tmp_path))
    assert receiver.bodies == []


def test_notify_ignores_non_http_webhook_values(tmp_path):
    for value in ["file:///etc/passwd", "ftp://example.com/x", "not a url"]:
        assert_silent(run_notify(notification(), tmp_path, {"SLACK_WEBHOOK_URL": value}))


def test_notify_unreachable_url_neither_fails_nor_leaks(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}{SECRET_PATH}"  # nothing listens on this port any more
    assert_silent(run_notify(notification(), tmp_path, {"SLACK_WEBHOOK_URL": url}))


def test_notify_does_not_wait_longer_than_the_timeout(tmp_path):
    server = Receiver(hang=True)
    try:
        start = time.monotonic()
        result = run_notify(notification(), tmp_path, {"SLACK_WEBHOOK_URL": server.url})
        elapsed = time.monotonic() - start
    finally:
        server.close()
    assert_silent(result)
    assert elapsed < 8


@pytest.mark.parametrize("garbage", [*GARBAGE, b"{}", b'{"message": 42}'])
def test_notify_always_exits_zero_silently(tmp_path, receiver, garbage):
    assert_silent(run_notify(garbage, tmp_path, {"SLACK_WEBHOOK_URL": receiver.url}))


def test_notify_handles_non_ascii_messages(tmp_path, receiver):
    message = "Grüße ✓"
    run_notify(notification(message), tmp_path, {"SLACK_WEBHOOK_URL": receiver.url})
    ((_, body),) = receiver.bodies
    assert message in json.loads(body)["text"]


# --- check_report (Stop) ------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A scratch project that carries the real template, with a reports/ folder."""
    target = tmp_path / "proj" / ".claude" / "skills" / "incident-runbook"
    target.mkdir(parents=True)
    (target / "template.md").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "proj" / "reports").mkdir()
    return tmp_path / "proj"


def stop(session=SESSION, **extra):
    return {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": False, **extra}


def audit_write(audit_log, file_path, session=SESSION, tool="Write", **extra):
    record = {
        "ts": "2026-10-16T09:30:12.481Z",
        "session_id": session,
        "tool_name": tool,
        "input": {"file_path": file_path, "content_length": 10},
        "result": {"status": "ok", "size": 5},
        **extra,
    }
    with open(audit_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def put_report(project, text, name="2026-03-12-checkout.md"):
    path = project / "reports" / name
    path.write_text(text, encoding="utf-8")
    return path


def example():
    return EXAMPLE_REPORT.read_text(encoding="utf-8")


def drop_section(text, heading):
    pattern = rf"^## {re.escape(heading)}\s*$.*?(?=^## |\Z)"
    return re.sub(pattern, "", text, flags=re.S | re.M)


def blank_section(text, heading, replacement=""):
    pattern = rf"(^## {re.escape(heading)}\s*$)(.*?)(?=^## |\Z)"

    def fill(match):
        return match.group(1) + "\n" + replacement + "\n\n"

    return re.sub(pattern, fill, text, flags=re.S | re.M)


def run_stop(project, audit_log, payload=None):
    return run_hook(CHECK_REPORT, stop() if payload is None else payload, project, audit_log)


def test_stop_ignores_active_stop_hook_even_with_incomplete_report(project, audit_log):
    audit_write(audit_log, str(put_report(project, "# Report\n\n## Summary\nx\n")))
    assert_silent(run_stop(project, audit_log, stop(stop_hook_active=True)))


def test_stop_without_audit_log_exits_zero(project, audit_log):
    assert_silent(run_stop(project, audit_log))


def test_stop_without_report_write_in_this_session_exits_zero(project, audit_log):
    audit_write(audit_log, str(project / "src" / "a.py"))
    audit_write(audit_log, str(project / "reports" / "notes.txt"))
    audit_write(audit_log, str(project / "docs" / "reports" / "x.md"))
    assert_silent(run_stop(project, audit_log))


def test_stop_accepts_the_complete_example_report(project, audit_log):
    audit_write(audit_log, str(put_report(project, example())))
    result = run_stop(project, audit_log)
    assert result.returncode == 0, stderr_of(result)
    assert result.stderr == b""


def test_stop_blocks_when_a_section_is_missing(project, audit_log):
    audit_write(audit_log, str(put_report(project, drop_section(example(), "Ruled out"))))
    result = run_stop(project, audit_log)
    assert result.returncode == 2
    message = stderr_of(result)
    assert "rule report-incomplete" in message
    assert "Missing sections: 'Ruled out'" in message
    assert "Empty sections" not in message
    assert "reports/2026-03-12-checkout.md" in message
    assert message.isascii()


@pytest.mark.parametrize(
    "filler",
    ["", "   \n", "<!-- Only the placeholder comment. -->", "<!--\nmulti\nline\n-->"],
)
def test_stop_blocks_an_empty_section(project, audit_log, filler):
    audit_write(audit_log, str(put_report(project, blank_section(example(), "Evidence", filler))))
    result = run_stop(project, audit_log)
    assert result.returncode == 2
    message = stderr_of(result)
    assert "Empty sections: 'Evidence'" in message
    assert "Missing sections" not in message


def test_stop_blocks_an_unfilled_template(project, audit_log):
    audit_write(audit_log, str(put_report(project, TEMPLATE.read_text(encoding="utf-8"))))
    result = run_stop(project, audit_log)
    assert result.returncode == 2
    message = stderr_of(result)
    for heading in TEMPLATE_HEADINGS:
        assert f"'{heading}'" in message


def test_stop_lists_missing_and_empty_sections_together(project, audit_log):
    text = blank_section(drop_section(example(), "Open questions"), "Impact")
    audit_write(audit_log, str(put_report(project, text)))
    message = stderr_of(run_stop(project, audit_log))
    assert "Missing sections: 'Open questions'" in message
    assert "Empty sections: 'Impact'" in message


def test_stop_ignores_headings_inside_code_fences(project, audit_log):
    text = drop_section(example(), "Open questions") + "\n```\n## Open questions\ntext\n```\n"
    audit_write(audit_log, str(put_report(project, text)))
    assert "Missing sections: 'Open questions'" in stderr_of(run_stop(project, audit_log))


def test_stop_ignores_reports_of_other_sessions_and_blocked_records(project, audit_log):
    bad = put_report(project, "## Summary\nx\n", "other.md")
    audit_write(audit_log, str(bad), session="someone-else")
    audit_write(audit_log, str(bad), decision="blocked", rule="env-file")
    assert_silent(run_stop(project, audit_log))


def test_stop_checks_the_latest_report_write_of_the_session(project, audit_log):
    bad = put_report(project, "## Summary\nx\n", "old.md")
    good = put_report(project, example(), "new.md")
    audit_write(audit_log, str(bad))
    audit_write(audit_log, str(good), tool="Edit")
    assert_silent(run_stop(project, audit_log))
    audit_write(audit_log, str(bad), tool="MultiEdit")
    assert run_stop(project, audit_log).returncode == 2


@pytest.mark.parametrize("style", ["relative", "backslash", "dotdot", "upper"])
def test_stop_normalizes_report_paths(project, audit_log, style):
    path = put_report(project, "## Summary\nx\n")
    given = {
        "relative": "reports/2026-03-12-checkout.md",
        "backslash": "reports\\2026-03-12-checkout.md",
        "dotdot": str(project / "src" / ".." / "reports" / "2026-03-12-checkout.md"),
        "upper": str(path).upper(),
    }[style]
    if style == "upper" and not (project / "REPORTS").exists():
        pytest.skip("needs a case-insensitive file system (Windows, macOS)")
    audit_write(audit_log, given)
    assert run_stop(project, audit_log).returncode == 2


@pytest.mark.parametrize("garbage", GARBAGE)
def test_stop_fails_open_on_garbage(project, audit_log, garbage):
    assert_silent(run_hook(CHECK_REPORT, garbage, project, audit_log))


def test_stop_fails_open_on_unusable_inputs(project, audit_log):
    # report file missing, garbage lines in the audit log, payload without session_id
    audit_write(audit_log, str(project / "reports" / "gone.md"))
    with open(audit_log, "a", encoding="utf-8") as handle:
        handle.write("not json\n[1]\n")
    assert_silent(run_stop(project, audit_log))
    assert_silent(run_stop(project, audit_log, {"hook_event_name": "Stop"}))
    # unreadable report: a directory with the report's name
    (project / "reports" / "dir.md").mkdir()
    audit_write(audit_log, str(project / "reports" / "dir.md"))
    assert_silent(run_stop(project, audit_log))


def test_stop_fails_open_without_template(project, audit_log):
    audit_write(audit_log, str(put_report(project, "## Summary\nx\n")))
    (project / ".claude" / "skills" / "incident-runbook" / "template.md").unlink()
    assert_silent(run_stop(project, audit_log))


def test_stop_blocked_attempt_is_audited(project, audit_log):
    audit_write(audit_log, str(put_report(project, drop_section(example(), "Evidence"))))
    assert run_stop(project, audit_log).returncode == 2
    record = read_log(audit_log)[-1]
    assert record["decision"] == "blocked"
    assert record["rule"] == "report-incomplete"
    assert record["session_id"] == SESSION
    assert record["tool_name"] == "Stop"
    assert record["input"]["file_path"] == "reports/2026-03-12-checkout.md"
    # a blocked record must not be mistaken for a report write on the next run
    assert run_stop(project, audit_log).returncode == 2


@pytest.mark.parametrize("heading", TEMPLATE_HEADINGS)
def test_every_template_heading_is_detected(project, audit_log, heading):
    """Parametrized from template.md at collection time, so template changes are picked up."""
    audit_write(audit_log, str(put_report(project, drop_section(example(), heading))))
    result = run_stop(project, audit_log)
    assert result.returncode == 2, heading
    assert f"Missing sections: '{heading}'" in stderr_of(result)


def test_template_headings_are_not_copied_into_the_hook():
    source = CHECK_REPORT.read_text(encoding="utf-8")
    assert len(TEMPLATE_HEADINGS) >= 8
    for heading in TEMPLATE_HEADINGS:
        assert heading not in source


def test_example_report_has_every_template_heading():
    report = example()
    for heading in TEMPLATE_HEADINGS:
        assert re.search(rf"^## {re.escape(heading)}\s*$", report, re.M), heading
