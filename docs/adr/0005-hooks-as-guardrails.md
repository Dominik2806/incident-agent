# 0005: Hooks as deterministic guardrails, a safety net and not a security boundary

- **Status:** Accepted (PR #8, extended in PR #12)

## Context

Instructions in `CLAUDE.md` are followed by the model, but not guaranteed. Some actions (destructive commands,
edits to secrets or the database) should be stopped regardless of what the model decides.

## Decision

- Hooks in `.claude/hooks/`, configured in the shared `.claude/settings.json` with `$CLAUDE_PROJECT_DIR`, standard
  library only: `block_destructive.py` (PreToolUse, Bash), `protect_files.py` (PreToolUse, file tools),
  `audit_log.py` (PostToolUse), `notify.py` (Notification), `check_report.py` (Stop).
- **Guards fail closed:** invalid input or a missing command exits 2 and blocks.
- **Audit, notification and stop hooks fail open:** they must never break work or trap Claude, so errors exit 0.
- Rules are one table (`RULES`); messages are ASCII only; stdin is read as UTF-8 bytes.
- If a hook blocks something, Claude does not work around it: it tells the user and lets them decide.

## Consequences

- Dangerous commands and protected paths are stopped deterministically, and every attempt is audited.
- The rules are regular expressions: obfuscation, other tools and shell writes get through, and harmless text that
  merely contains a pattern (for example `rm -rf` inside a heredoc) is blocked. The hooks are a safety net against
  mistakes, not a security boundary ([hooks.md](../hooks.md#limitations)).
- Hooks only run in a trusted workspace and need `uv`; whoever can edit `settings.json` can change them.
