# Hooks

Five hook scripts in [`.claude/hooks/`](../.claude/hooks/) apply deterministic rules, whatever the model decides:
three safety hooks on tool calls, a notification hook and a stop hook that checks the incident report. They use only
the Python standard library and are configured in [`.claude/settings.json`](../.claude/settings.json), which is
committed and shared with the team.

| Script | Event / matcher | Behavior |
|---|---|---|
| `block_destructive.py` | PreToolUse / `Bash` | Blocks destructive shell and SQL commands (exit code 2) |
| `protect_files.py` | PreToolUse / `Edit\|Write\|MultiEdit\|NotebookEdit` | Blocks changes to secrets, databases, `data/` and CI workflows (exit code 2) |
| `audit_log.py` | PostToolUse / all tools | Appends one JSON line per call to `logs/audit.jsonl` (always exit 0) |
| `notify.py` | Notification / no matcher | Desktop notification and optional Slack message (always exit 0, silent) |
| `check_report.py` | Stop / no matcher | Blocks stopping (exit code 2) while this session's incident report is incomplete |

`_common.py` is the shared helper (read stdin, resolve the project dir, write the audit log). Exit code 2 from a
PreToolUse hook blocks the call and passes stderr to Claude; exit code 2 from a Stop hook keeps Claude working and
passes stderr as the reason. Claude must then explain a block to the user instead of working around it (see
`CLAUDE.md`).

## block_destructive.py

The rules are one table (`RULES`) at the top of the file: id, case-insensitive regex, message. Blocked:

| Rule id | Blocks |
|---|---|
| `rm-recursive-force` | `rm` with recursive and force flags in any form: `-rf`, `-fr`, `-r -f`, `--recursive --force`, ... |
| `powershell-remove-recurse` | `Remove-Item ... -Recurse` (also `ri`, and `-Recurse` on `rm`/`del`/`rd` aliases) |
| `cmd-rd-s` | `rd /s`, `rmdir /s` |
| `cmd-del-s` | `del /s`, `erase /s` |
| `sql-drop` | `DROP TABLE`, `DROP DATABASE`, `DROP SCHEMA`, `DROP INDEX` |
| `sql-delete-from` | `DELETE FROM` |
| `sql-truncate` | `TRUNCATE` |
| `git-push-force` | `git push` with `--force`, `-f`, `--force-with-lease` or a `+branch` refspec |
| `git-reset-hard` | `git reset --hard` |
| `git-clean-force` | `git clean` with `-f` / `--force` |

Everything else passes (`rm file.txt`, `git push`, `uv run pytest`, `SELECT ...`). Each block message says what was
blocked, why, and what to do instead.

**Documented false positives** (accepted: the safe direction):

- The patterns also match inside strings: `echo "DROP TABLE users"` or `git commit -m "remove rm -rf"` are blocked.
- The whole command text is scanned, so a command that merely contains text like `rm -rf` is blocked too, for
  example a heredoc (`cat > notes.md <<'EOF' ... rm -rf ... EOF`) that writes documentation. This is a false
  positive that is accepted by design: Claude reports the block and the human decides (or runs the command).
- `truncate -s 0 file` (the Unix tool) is blocked by `sql-truncate`.
- `git clean -fn` (force plus dry run) is blocked.
- `git rm -rf` is blocked like `rm -rf`.

## protect_files.py

Blocks any change to:

| Rule id | Protected |
|---|---|
| `env-file` | `.env` and `.env.*` (but `.env.example` is allowed) |
| `db-file` | any `*.db`, `*.sqlite`, `*.sqlite3` file |
| `data-dir` | everything under the project's `data/` folder (`src/incident_agent/data/` is source code and is not affected) |
| `github-workflows` | everything under `.github/workflows/` |

Paths are normalized before matching: resolved against the project directory, backslashes converted, `..` and `.`
segments collapsed, trailing dots and spaces dropped, and case ignored. The block message tells Claude to ask the
human to make the change manually.

## audit_log.py

One JSON line per tool call (all tools, including MCP tools and subagent tool calls):

```json
{"ts": "2026-10-16T09:30:12.481Z", "session_id": "...", "tool_name": "Bash",
 "input": {"command": "uv run pytest"}, "result": {"status": "ok", "size": 1342}}
```

- Strings in `input` are cut at 300 characters. For file tools (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) only
  the path and the content length are logged (`content_length`, `old_string_length`, ...), never the content.
- Only `status` (`ok` / `error`) and `size` of the response are logged, never the response itself.
- The hook never blocks: it always exits 0 and swallows every error. `logs/` is created when missing and is gitignored.
- The path can be overridden with the environment variable `INCIDENT_AUDIT_LOG` (used by the tests).
- Blocked attempts from the two guards are appended to the same file by the shared helper:
  `{"decision": "blocked", "rule": "git-push-force", "tool_name": "Bash", ...}`, so the trail shows what was prevented.

## notify.py

Notification event, no matcher, so every notification type fires it (`permission_prompt`, `idle_prompt`, ...).
Claude Code sends `message`, an optional `title` and `notification_type`; exit code 2 has no effect on this event.

- **Text:** `[<project folder name>] <message>`, cut to 300 characters.
- **Desktop:** Windows (PowerShell balloon tip), macOS (`osascript`), Linux (`notify-send`). The text is passed
  through environment variables, never spliced into a command line. The child process is started detached and never
  waited on; a missing tool or any failure is skipped silently.
- **Slack:** only if `SLACK_WEBHOOK_URL` starts with `http://` or `https://`. JSON POST `{"text": "..."}` with a 3
  second timeout; every failure is swallowed. The URL is a secret: it is never printed, logged or written to the
  audit log, and no error text is produced at all (error messages of `urllib` can contain the URL).
- Always exits 0 and writes nothing to stdout or stderr; without `SLACK_WEBHOOK_URL` there is no network access.

| Variable | Effect |
|---|---|
| `SLACK_WEBHOOK_URL` | Enables the Slack message. Secret. See [`.env.example`](../.env.example) |
| `INCIDENT_NOTIFY_DESKTOP=0` | Disables the desktop notification (tests, CI, headless machines) |

Claude Code does not load `.env` files. Set the variable in your shell, or in the `"env"` block of
`.claude/settings.local.json` (gitignored); never in the shared `settings.json`.

## check_report.py

Stop event, no matcher. It makes sure that an incident report written in this session is complete before Claude
stops.

1. `stop_hook_active` is true (Claude is already continuing because of a stop hook) -> exit 0 at once. This is the
   loop protection and means **one correction round**.
2. The required `##` headings are parsed at runtime from
   `.claude/skills/incident-runbook/template.md` (fenced code ignored). The hook holds no copy of the list.
3. The report is found through the audit log (`INCIDENT_AUDIT_LOG` or `logs/audit.jsonl`): the latest `Write`,
   `Edit` or `MultiEdit` record of this `session_id` whose path is under `reports/` and ends in `.md`
   (paths are normalized: backslashes, `..`, case, relative or absolute). Blocked attempts are ignored. No such
   record -> exit 0, so Claude can stop mid-analysis (for example while background subagents still run).
4. Every required heading must be present and its section must have real content: HTML comments are removed, and a
   section that is blank or identical to the template's own placeholder text counts as empty. Subsections (`###`)
   belong to their section and are not checked separately.
5. Anything missing or empty -> exit 2, rule `report-incomplete`, with a message that lists `Missing sections` and
   `Empty sections` and tells Claude to complete the report. The attempt is audited like the guards' blocks
   (`decision: blocked`, `tool_name: Stop`, the report path under `input.file_path`).

The hook **fails open**: a missing audit log, template or report, an unreadable file or invalid input exits 0.

## Failure policy

- The guards **fail closed**: if stdin is not valid JSON, or `tool_input` (or the command / file path) is missing,
  they exit 2 with a clear message.
- The audit hook, `notify.py` and `check_report.py` **fail open**: they must never break work or trap Claude.
- Encoding: stdin is read as UTF-8 bytes, the audit file is UTF-8 and all stderr text is ASCII only, so a Windows
  console with cp1252 cannot crash a guard.

## Configuration

Each hook runs as

```text
uv run --no-project --quiet python "$CLAUDE_PROJECT_DIR/.claude/hooks/<script>.py"
```

`$CLAUDE_PROJECT_DIR` is set by Claude Code, so no absolute or user-specific paths are needed. On Windows, Claude
Code runs the command in Git Bash (or PowerShell if Git Bash is missing); the command works in both, and `uv`
provides the same Python 3.12 on Windows and Linux. Hooks only run after you accept the workspace trust dialog.

## Testing a hook by hand

Pipe a payload into the script. The exit code is the result.

```bash
# bash (Git Bash, Linux, macOS); benign command: exit 0
export CLAUDE_PROJECT_DIR="$PWD"
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' \
  | uv run --no-project --quiet python .claude/hooks/block_destructive.py; echo "exit=$?"
```

```powershell
# PowerShell; protected file: prints the reason, exit 2
$env:CLAUDE_PROJECT_DIR = (Get-Location).Path
'{"tool_name":"Write","tool_input":{"file_path":".env","content":"x"}}' |
  uv run --no-project --quiet python .claude/hooks/protect_files.py; "exit=$LASTEXITCODE"
```

Notification and Stop hooks (the audit file below is a scratch file, so `logs/audit.jsonl` stays clean):

```bash
# bash: desktop toast (unset INCIDENT_NOTIFY_DESKTOP or set it to 1), no Slack
export CLAUDE_PROJECT_DIR="$PWD" INCIDENT_AUDIT_LOG=/tmp/audit-test.jsonl
echo '{"message":"Claude needs your permission"}' \
  | uv run --no-project --quiet python .claude/hooks/notify.py; echo "exit=$?"
# Stop hook: pretend session "demo" wrote reports/test.md, then run the check
mkdir -p reports && printf '## Summary\nonly this\n' > reports/test.md
echo '{"session_id":"demo","tool_name":"Write","input":{"file_path":"reports/test.md"}}' > "$INCIDENT_AUDIT_LOG"
echo '{"session_id":"demo","stop_hook_active":false}' \
  | uv run --no-project --quiet python .claude/hooks/check_report.py; echo "exit=$?"   # exit 2
```

```powershell
# PowerShell: Slack test against a webhook of your own (the URL is never echoed by the hook)
$env:CLAUDE_PROJECT_DIR = (Get-Location).Path
$env:INCIDENT_AUDIT_LOG = "$env:TEMP\audit-test.jsonl"
$env:INCIDENT_NOTIFY_DESKTOP = "0"
$env:SLACK_WEBHOOK_URL = "<your webhook url>"
'{"message":"Claude needs your permission"}' |
  uv run --no-project --quiet python .claude/hooks/notify.py; "exit=$LASTEXITCODE"
Remove-Item Env:SLACK_WEBHOOK_URL
# Stop hook against an incomplete report -> exit 2 and the list of sections
New-Item -ItemType Directory -Force reports | Out-Null
Set-Content reports\test.md "## Summary`nonly this" -Encoding utf8
'{"session_id":"demo","tool_name":"Write","input":{"file_path":"reports/test.md"}}' |
  Set-Content $env:INCIDENT_AUDIT_LOG -Encoding utf8
'{"session_id":"demo","stop_hook_active":false}' |
  uv run --no-project --quiet python .claude/hooks/check_report.py; "exit=$LASTEXITCODE"
```

Delete `reports/test.md` afterwards.

Set `INCIDENT_AUDIT_LOG` to a scratch file to keep manual tests out of `logs/audit.jsonl`. When running a manual
test through Claude's own Bash tool, remember that the command text itself is scanned: a command line that
contains `rm -rf` is blocked even if it is only an `echo` payload.

The automated tests are in `tests/test_hooks.py` and `tests/test_hooks_notify_stop.py`
(`uv run pytest tests/test_hooks.py tests/test_hooks_notify_stop.py`). They set `INCIDENT_NOTIFY_DESKTOP=0`, so no
real notification pops up.

## Limitations

- **Hooks are a safety net, not a security boundary.** They protect against mistakes, not against a determined
  bypass.
- **Pattern based.** Obfuscation gets through: variables (`$cmd`), encoded or generated commands (`base64 -d | sh`,
  `python -c`), scripts that contain the dangerous command, `find ... -delete`, `shred`, other tools that delete.
- **`protect_files.py` only sees Edit, Write, MultiEdit and NotebookEdit.** It does not cover writes via the shell
  (`>`, `>>`, `tee`, `sed -i`, `mv`, `cp`, a Python one-liner). It does not resolve symlinks.
- **False positives** exist by design (see above); the cost is a blocked command that the human can run.
- **One correction round.** Because of `stop_hook_active`, `check_report.py` blocks at most once per stop. If Claude
  still leaves the report incomplete, it is allowed to stop (the alternative is an endless loop).
- **Report detection relies on the audit log.** Only reports written with `Write`, `Edit` or `MultiEdit` in this
  session are seen; a report created through the shell (`>`, `tee`) is not, and without `audit_log.py` running
  nothing is checked. Subsections (`###`) and the quality of the content are not verified, only that a section is
  not empty.
- **Notifications are best effort.** No delivery guarantee: a missing `notify-send`, Focus Assist / Do Not Disturb or
  a Slack outage mean no message, and nothing reports it.
- **The Slack webhook URL is a secret.** Keep it out of the repository and `settings.json`. Whoever has it can post to
  your channel, and the notification text (which can mention tools and file names) leaves your machine.
- **Interpreter missing.** If `uv` is not installed the hook command fails with a code other than 2, which Claude
  Code treats as non-blocking: the guards then do not run. `uv` is already required by this project.
- **Trust.** Hooks do not run before the workspace is trusted, and settings can be changed by anyone with write
  access to the repository.

## Disabling temporarily

- Open `/hooks` in Claude Code to review what is active.
- To switch all hooks off for yourself, set `"disableAllHooks": true` in `.claude/settings.local.json` (gitignored,
  never committed) and restart the session. Remove it again afterwards.
- Do not edit `.claude/settings.json` for a temporary change: it is shared with the team.
