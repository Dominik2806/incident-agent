# Safety hooks

Three hook scripts in [`.claude/hooks/`](../.claude/hooks/) apply deterministic rules to every tool call, whatever
the model decides. They use only the Python standard library and are configured in
[`.claude/settings.json`](../.claude/settings.json), which is committed and shared with the team.

| Script | Event / matcher | Behavior |
|---|---|---|
| `block_destructive.py` | PreToolUse / `Bash` | Blocks destructive shell and SQL commands (exit code 2) |
| `protect_files.py` | PreToolUse / `Edit\|Write\|MultiEdit\|NotebookEdit` | Blocks changes to secrets, databases, `data/` and CI workflows (exit code 2) |
| `audit_log.py` | PostToolUse / all tools | Appends one JSON line per call to `logs/audit.jsonl` (always exit 0) |

`_common.py` is the shared helper (read stdin, resolve the project dir, write the audit log). Exit code 2 from a
PreToolUse hook blocks the call and passes stderr to Claude. Claude must then explain the block to the user instead
of working around it (see `CLAUDE.md`).

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

## Failure policy

- The guards **fail closed**: if stdin is not valid JSON, or `tool_input` (or the command / file path) is missing,
  they exit 2 with a clear message.
- The audit hook **fails open**: it must never break work.
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

Set `INCIDENT_AUDIT_LOG` to a scratch file to keep manual tests out of `logs/audit.jsonl`. When running a manual
test through Claude's own Bash tool, remember that the command text itself is scanned: a command line that
contains `rm -rf` is blocked even if it is only an `echo` payload.

The automated tests are in `tests/test_hooks.py` (`uv run pytest tests/test_hooks.py`).

## Limitations

- **Hooks are a safety net, not a security boundary.** They protect against mistakes, not against a determined
  bypass.
- **Pattern based.** Obfuscation gets through: variables (`$cmd`), encoded or generated commands (`base64 -d | sh`,
  `python -c`), scripts that contain the dangerous command, `find ... -delete`, `shred`, other tools that delete.
- **`protect_files.py` only sees Edit, Write, MultiEdit and NotebookEdit.** It does not cover writes via the shell
  (`>`, `>>`, `tee`, `sed -i`, `mv`, `cp`, a Python one-liner). It does not resolve symlinks.
- **False positives** exist by design (see above); the cost is a blocked command that the human can run.
- **Interpreter missing.** If `uv` is not installed the hook command fails with a code other than 2, which Claude
  Code treats as non-blocking: the guards then do not run. `uv` is already required by this project.
- **Trust.** Hooks do not run before the workspace is trusted, and settings can be changed by anyone with write
  access to the repository.

## Disabling temporarily

- Open `/hooks` in Claude Code to review what is active.
- To switch all hooks off for yourself, set `"disableAllHooks": true` in `.claude/settings.local.json` (gitignored,
  never committed) and restart the session. Remove it again afterwards.
- Do not edit `.claude/settings.json` for a temporary change: it is shared with the team.
