"""Tests for the hook scripts in .claude/hooks/ (run as subprocesses, like Claude Code does)."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = ROOT / ".claude" / "hooks"
SETTINGS = ROOT / ".claude" / "settings.json"
BLOCK = HOOKS_DIR / "block_destructive.py"
PROTECT = HOOKS_DIR / "protect_files.py"
AUDIT = HOOKS_DIR / "audit_log.py"


def run_hook(script, stdin, project, audit_log=None, env_extra=None):
    """Run a hook script with the given stdin (dict or raw bytes) and return the result."""
    if not isinstance(stdin, bytes):
        stdin = json.dumps(stdin, ensure_ascii=False).encode("utf-8")
    env = dict(os.environ)
    env.pop("INCIDENT_AUDIT_LOG", None)
    env.pop("PYTHONIOENCODING", None)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    if audit_log is not None:
        env["INCIDENT_AUDIT_LOG"] = str(audit_log)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(script)],
        input=stdin,
        capture_output=True,
        env=env,
        cwd=project,
        timeout=30,
    )


def stderr_of(result):
    return result.stderr.decode("utf-8", errors="replace")


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def write(path, tool="Write"):
    return {"tool_name": tool, "tool_input": {"file_path": path, "content": "x"}}


def read_log(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture
def audit_log(tmp_path):
    return tmp_path / "audit.jsonl"


# --- block_destructive ------------------------------------------------------------------------

BLOCKED_COMMANDS = [
    ("rm -rf build", "rm-recursive-force"),
    ("rm -fr build", "rm-recursive-force"),
    ("rm -Rf build", "rm-recursive-force"),
    ("rm -r -f build", "rm-recursive-force"),
    ("rm -f -r build", "rm-recursive-force"),
    ("rm --recursive --force build", "rm-recursive-force"),
    ("rm -r --force build", "rm-recursive-force"),
    ("rm build -rf", "rm-recursive-force"),
    ("cd x && sudo rm -rf /", "rm-recursive-force"),
    ("RM -RF build", "rm-recursive-force"),
    ("Remove-Item -Recurse -Force build", "powershell-remove-recurse"),
    ("remove-item build -recurse", "powershell-remove-recurse"),
    ("rd /s /q build", "cmd-rd-s"),
    ("rmdir /S build", "cmd-rd-s"),
    ("del /s *.log", "cmd-del-s"),
    ("DROP TABLE errors", "sql-drop"),
    ("sqlite3 data/incident.db 'drop database x'", "sql-drop"),
    ("DROP SCHEMA public", "sql-drop"),
    ("drop index idx_logs_ts", "sql-drop"),
    ("DELETE FROM logs", "sql-delete-from"),
    ("sqlite3 db 'delete from logs where 1'", "sql-delete-from"),
    ("TRUNCATE TABLE logs", "sql-truncate"),
    ("truncate logs", "sql-truncate"),
    ("git push --force", "git-push-force"),
    ("git push -f", "git-push-force"),
    ("git push origin main --force", "git-push-force"),
    ("git push --force-with-lease", "git-push-force"),
    ("git push --force-with-lease=main origin", "git-push-force"),
    ("git push -uf origin main", "git-push-force"),
    ("git reset --hard", "git-reset-hard"),
    ("git reset --hard HEAD~1", "git-reset-hard"),
    ("git clean -f", "git-clean-force"),
    ("git clean -fd", "git-clean-force"),
    ("git clean -xdf", "git-clean-force"),
    ("git clean --force", "git-clean-force"),
    # documented false positive: the pattern also matches inside a string
    ('echo "DROP TABLE users"', "sql-drop"),
]

ALLOWED_COMMANDS = [
    "rm file.txt",
    "rm -r build",
    "rm -f file.txt",
    "git push",
    "git push origin feature/safety-hooks",
    "git push -u origin feature/fix-hooks",
    "git status",
    "git log --oneline",
    "git reset --soft HEAD~1",
    "git clean -n",
    "uv run pytest",
    "uv run ruff check",
    "ls -la",
    "echo hello",
    "SELECT * FROM logs WHERE level = 'ERROR'",
    "sqlite3 data/incident.db 'SELECT count(*) FROM errors'",
    "Get-ChildItem -Recurse",
    "del file.txt",
    "docker run --rm alpine true",
]


@pytest.mark.parametrize(("command", "rule"), BLOCKED_COMMANDS)
def test_destructive_commands_are_blocked(tmp_path, audit_log, command, rule):
    result = run_hook(BLOCK, bash(command), tmp_path, audit_log)
    assert result.returncode == 2, stderr_of(result)
    message = stderr_of(result)
    assert f"rule {rule}" in message
    assert "Why:" in message
    assert "Do not work around" in message


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_other_commands_are_allowed(tmp_path, audit_log, command):
    result = run_hook(BLOCK, bash(command), tmp_path, audit_log)
    assert result.returncode == 0, stderr_of(result)
    assert result.stderr == b""
    assert not audit_log.exists()


# --- protect_files ----------------------------------------------------------------------------

PROTECTED_PATHS = [
    (".env", "env-file"),
    (".env.local", "env-file"),
    (".env.production", "env-file"),
    ("sub/dir/.env", "env-file"),
    (".ENV", "env-file"),
    ("src/../.env", "env-file"),
    ("src\\..\\.env", "env-file"),
    ("data/incident.db", "db-file"),
    ("DATA\\incident.db", "db-file"),
    ("docs/../data/incident.db", "db-file"),
    ("x.db", "db-file"),
    ("cache.sqlite", "db-file"),
    ("cache.SQLITE3", "db-file"),
    ("data/notes.txt", "data-dir"),
    ("DATA/Notes.txt", "data-dir"),
    ("docs/../data/notes.txt", "data-dir"),
    ("data", "data-dir"),
    (".github/workflows/ci.yml", "github-workflows"),
    (".github\\workflows\\ci.yml", "github-workflows"),
    (".GITHUB/Workflows/CI.yml", "github-workflows"),
    ("src/../.github/workflows/new.yml", "github-workflows"),
]

ALLOWED_PATHS = [
    ".env.example",
    "docs/.env.example",
    "src/incident_agent/data/schema.py",
    "src/incident_agent/queries.py",
    "docs/hooks.md",
    "README.md",
    "tests/test_hooks.py",
    ".github/ISSUE_TEMPLATE/bug.md",
    ".github/pull_request_template.md",
    "src/../docs/hooks.md",
    "database.py",
    "environment.md",
]


@pytest.mark.parametrize(("path", "rule"), PROTECTED_PATHS)
def test_protected_relative_paths_are_blocked(tmp_path, audit_log, path, rule):
    result = run_hook(PROTECT, write(path), tmp_path, audit_log)
    assert result.returncode == 2, stderr_of(result)
    message = stderr_of(result)
    assert f"rule {rule}" in message
    assert "ask them to make it manually" in message


@pytest.mark.parametrize(
    ("relative", "rule"),
    [
        (".env", "env-file"),
        ("data/incident.db", "db-file"),
        ("data/x.txt", "data-dir"),
        (".github/workflows/ci.yml", "github-workflows"),
    ],
)
def test_protected_absolute_paths_are_blocked(tmp_path, audit_log, relative, rule):
    absolute = str(tmp_path / relative)
    result = run_hook(PROTECT, write(absolute), tmp_path, audit_log)
    assert result.returncode == 2, stderr_of(result)
    assert f"rule {rule}" in stderr_of(result)
    # upper-cased absolute path, with a ".." segment added
    shouted = str(tmp_path / "src" / ".." / relative).upper()
    assert run_hook(PROTECT, write(shouted), tmp_path, audit_log).returncode == 2


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_protected_paths_are_blocked_for_every_editing_tool(tmp_path, audit_log, tool):
    assert run_hook(PROTECT, write(".env", tool), tmp_path, audit_log).returncode == 2


def test_notebook_path_is_protected(tmp_path, audit_log):
    payload = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "data/a.ipynb"}}
    assert run_hook(PROTECT, payload, tmp_path, audit_log).returncode == 2
    payload["tool_input"]["notebook_path"] = "docs/a.ipynb"
    assert run_hook(PROTECT, payload, tmp_path, audit_log).returncode == 0


@pytest.mark.parametrize("path", ALLOWED_PATHS)
def test_other_paths_are_allowed(tmp_path, audit_log, path):
    result = run_hook(PROTECT, write(path), tmp_path, audit_log)
    assert result.returncode == 0, stderr_of(result)
    assert result.stderr == b""
    assert run_hook(PROTECT, write(str(tmp_path / path)), tmp_path, audit_log).returncode == 0


# --- audit_log --------------------------------------------------------------------------------


def post_payload(tool_name, tool_input, tool_response=None):
    return {
        "session_id": "sess-1",
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
    }


def test_audit_writes_one_line_per_call(tmp_path, audit_log):
    payload = post_payload("Bash", {"command": "uv run pytest"}, {"stdout": "ok", "stderr": ""})
    for _ in range(2):
        assert run_hook(AUDIT, payload, tmp_path, audit_log).returncode == 0
    records = read_log(audit_log)
    assert len(records) == 2
    record = records[0]
    assert record["session_id"] == "sess-1"
    assert record["tool_name"] == "Bash"
    assert record["input"] == {"command": "uv run pytest"}
    assert record["result"]["status"] == "ok"
    assert record["result"]["size"] > 0
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", record["ts"])


def test_audit_truncates_long_strings_and_never_logs_responses(tmp_path, audit_log):
    payload = post_payload("Bash", {"command": "a" * 1000}, {"stdout": "SECRET" * 1000})
    assert run_hook(AUDIT, payload, tmp_path, audit_log).returncode == 0
    raw = audit_log.read_text(encoding="utf-8")
    record = json.loads(raw)
    command = record["input"]["command"]
    assert command.startswith("a" * 300)
    assert "a" * 301 not in command
    assert len(command) < 1000
    assert "SECRET" not in raw
    assert record["result"]["size"] >= 6000


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_audit_never_logs_file_content(tmp_path, audit_log, tool):
    tool_input = {
        "file_path": "src/app.py",
        "content": "TOPSECRET-content " * 100,
        "old_string": "TOPSECRET-old",
        "new_string": "TOPSECRET-new",
    }
    assert run_hook(AUDIT, post_payload(tool, tool_input), tmp_path, audit_log).returncode == 0
    raw = audit_log.read_text(encoding="utf-8")
    assert "TOPSECRET" not in raw
    logged = json.loads(raw)["input"]
    assert logged["file_path"] == "src/app.py"
    assert logged["content_length"] == len("TOPSECRET-content " * 100)
    assert logged["old_string_length"] == len("TOPSECRET-old")
    assert logged["new_string_length"] == len("TOPSECRET-new")


def test_audit_multiedit_logs_lengths_only(tmp_path, audit_log):
    edits = [{"old_string": "SECRET-a", "new_string": "SECRET-bb"}]
    payload = post_payload("MultiEdit", {"file_path": "a.py", "edits": edits})
    assert run_hook(AUDIT, payload, tmp_path, audit_log).returncode == 0
    raw = audit_log.read_text(encoding="utf-8")
    assert "SECRET" not in raw
    logged = json.loads(raw)["input"]
    assert logged["edits_count"] == 1
    assert logged["edits_length"] == len("SECRET-a") + len("SECRET-bb")


def test_audit_covers_mcp_tools_and_flags_errors(tmp_path, audit_log):
    name = "mcp__incident-logs__search_logs"
    ok = post_payload(name, {"service": "payment", "limit": 5}, [{"type": "text", "text": "x"}])
    bad = post_payload("Bash", {"command": "false"}, {"is_error": True})
    assert run_hook(AUDIT, ok, tmp_path, audit_log).returncode == 0
    assert run_hook(AUDIT, bad, tmp_path, audit_log).returncode == 0
    first, second = read_log(audit_log)
    assert first["tool_name"] == name
    assert first["input"] == {"service": "payment", "limit": 5}
    assert first["result"]["status"] == "ok"
    assert second["result"]["status"] == "error"


def test_audit_creates_missing_directory_for_override(tmp_path):
    target = tmp_path / "missing" / "dir" / "audit.jsonl"
    assert (
        run_hook(AUDIT, post_payload("Read", {"file_path": "a"}), tmp_path, target).returncode == 0
    )
    assert len(read_log(target)) == 1


def test_audit_defaults_to_logs_dir_of_project(tmp_path):
    result = run_hook(AUDIT, post_payload("Read", {"file_path": "a"}), tmp_path)
    assert result.returncode == 0
    assert len(read_log(tmp_path / "logs" / "audit.jsonl")) == 1


def test_audit_never_fails_when_log_cannot_be_written(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    result = run_hook(AUDIT, post_payload("Read", {}), tmp_path, blocker / "audit.jsonl")
    assert result.returncode == 0


@pytest.mark.parametrize(
    ("script", "payload", "rule"),
    [
        (BLOCK, bash("rm -rf build"), "rm-recursive-force"),
        (BLOCK, bash("git push --force"), "git-push-force"),
        (PROTECT, write(".env"), "env-file"),
        (PROTECT, write(".github/workflows/ci.yml"), "github-workflows"),
    ],
)
def test_blocked_attempts_are_audited(tmp_path, audit_log, script, payload, rule):
    payload = {**payload, "session_id": "sess-9"}
    assert run_hook(script, payload, tmp_path, audit_log).returncode == 2
    (record,) = read_log(audit_log)
    assert record["decision"] == "blocked"
    assert record["rule"] == rule
    assert record["tool_name"] == payload["tool_name"]
    assert record["session_id"] == "sess-9"
    assert "content" not in record["input"]


# --- failure policy ---------------------------------------------------------------------------

GARBAGE = [b"not json", b"", b"[1, 2]", b'"text"', b"\xff\xfe\x00", b"{"]


@pytest.mark.parametrize("garbage", GARBAGE)
def test_audit_hook_fails_open_on_garbage(tmp_path, audit_log, garbage):
    assert run_hook(AUDIT, garbage, tmp_path, audit_log).returncode == 0


@pytest.mark.parametrize("script", [BLOCK, PROTECT])
@pytest.mark.parametrize("garbage", GARBAGE)
def test_guards_fail_closed_on_garbage(tmp_path, audit_log, script, garbage):
    result = run_hook(script, garbage, tmp_path, audit_log)
    assert result.returncode == 2
    message = stderr_of(result)
    assert "fails closed" in message
    assert message.isascii()


@pytest.mark.parametrize("script", [BLOCK, PROTECT])
@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Bash"},
        {"tool_name": "Bash", "tool_input": None},
        {"tool_name": "Bash", "tool_input": "rm -rf /"},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash", "tool_input": {"command": 42}},
        {"tool_name": "Write", "tool_input": {"file_path": ""}},
    ],
)
def test_guards_fail_closed_without_usable_tool_input(tmp_path, audit_log, script, payload):
    assert run_hook(script, payload, tmp_path, audit_log).returncode == 2


def test_audit_tolerates_missing_fields(tmp_path, audit_log):
    assert run_hook(AUDIT, {"tool_name": "Read"}, tmp_path, audit_log).returncode == 0
    assert run_hook(AUDIT, {}, tmp_path, audit_log).returncode == 0
    assert len(read_log(audit_log)) == 2


# --- non-ASCII input (UTF-8 stdin, cp1252 console) ---------------------------------------------

ENCODINGS = [None, "cp1252", "utf-8"]


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_non_ascii_input_is_handled_by_every_hook(tmp_path, audit_log, encoding):
    env = {"PYTHONIOENCODING": encoding} if encoding else {}

    def run(script, payload):
        return run_hook(script, payload, tmp_path, audit_log, env)

    # block_destructive
    assert run(BLOCK, bash('echo "Gr\u00fc\u00dfe aus M\u00fcnchen"')).returncode == 0
    blocked = run(BLOCK, bash("rm -rf Gr\u00fc\u00dfe"))
    assert blocked.returncode == 2
    assert blocked.stderr.decode("ascii")  # pure ASCII, no UnicodeDecodeError
    assert "Gr\\xfc\\xdfe" in blocked.stderr.decode("ascii")
    # protect_files
    assert run(PROTECT, write("docs/\u00dcbersicht.md")).returncode == 0
    blocked = run(PROTECT, write("data/Gr\u00f6\u00dfe.db"))
    assert blocked.returncode == 2
    assert blocked.stderr.decode("ascii")
    assert run(PROTECT, write(str(tmp_path / "M\u00fcnchen" / ".env"))).returncode == 2
    # audit_log
    payload = post_payload("Bash", {"command": "echo \u00e4\u00f6\u00fc"}, {"stdout": "\u00e4"})
    assert run(AUDIT, payload).returncode == 0
    records = read_log(audit_log)
    assert records[-1]["input"]["command"] == "echo \u00e4\u00f6\u00fc"


# --- settings.json ----------------------------------------------------------------------------


def hook_commands():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    for event, groups in settings["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                yield event, group.get("matcher"), hook


def is_absolute_script_path(path):
    return re.match(r"^([A-Za-z]:|[/\\])", path) is not None


def test_settings_is_valid_json_with_the_three_hooks():
    found = {(event, matcher) for event, matcher, _ in hook_commands()}
    assert found == {
        ("PreToolUse", "Bash"),
        ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit"),
        ("PostToolUse", None),
    }


def test_settings_scripts_exist_and_use_project_dir():
    scripts = set()
    for _, _, hook in hook_commands():
        assert hook["type"] == "command"
        command = hook["command"]
        assert "$CLAUDE_PROJECT_DIR" in command
        (script,) = re.findall(r'"([^"]+\.py)"', command)
        assert script.startswith("$CLAUDE_PROJECT_DIR/"), script
        assert not is_absolute_script_path(script)
        assert "\\" not in script
        relative = script.removeprefix("$CLAUDE_PROJECT_DIR/")
        assert (ROOT / relative).is_file(), relative
        scripts.add(relative)
    assert scripts == {
        ".claude/hooks/block_destructive.py",
        ".claude/hooks/protect_files.py",
        ".claude/hooks/audit_log.py",
    }


def test_absolute_path_check_flags_only_real_absolute_paths():
    assert is_absolute_script_path("C:/Users/me/hooks/a.py")
    assert is_absolute_script_path("c:\\Users\\me\\hooks\\a.py")
    assert is_absolute_script_path("/home/me/hooks/a.py")
    assert is_absolute_script_path("\\\\server\\share\\a.py")
    assert not is_absolute_script_path("$CLAUDE_PROJECT_DIR/.claude/hooks/a.py")
    assert not is_absolute_script_path(".claude/hooks/a.py")


def test_hooks_do_not_hardcode_user_paths():
    for script in HOOKS_DIR.glob("*.py"):
        text = script.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\\/]Users", text), script.name
        assert "/home/" not in text, script.name
