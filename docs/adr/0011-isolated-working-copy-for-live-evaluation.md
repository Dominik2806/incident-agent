# 0011: Live evaluation runs in an isolated working copy

- **Status:** Accepted (issue #35)

## Context

`run-eval` started `claude -p` in the repository root with a bare `Read`, `Grep` and `Glob`. The saved audit logs show
the agents searching `src/incident_agent/data` and reading `scenarios.py` and `docs/scenarios.md` in nearly every run.
That is the generator and the answer key, so a pass did not show that the agents can find the cause from the logs. The
fix-proposer must search the codebase for the failing code, so it cannot simply be denied file search: the environment
has to change, not the agents.

## Decision

- Each run executes in a **sanitized working copy** built by `evaluation/workcopy.py` from an allowlist: `.claude/`
  (agents, skill, hooks, `settings.json`), `demo_app/*.py`, `tests/test_demo_app.py`, an empty `reports/` and a generated
  `CLAUDE.md`. No `docs/`, `src/`, `evals/`, `tests/fixtures`, other tests, README or CHANGELOG.
- The copy lives in the **system temp directory**, outside the repository, with its own parent folder. Claude Code loads
  `CLAUDE.md` from the parent directories of its cwd, so a copy inside the repository would load the real one, and
  `../..` would lead to the answer key. The copy is the cwd of `claude` and is removed after the run (a failing removal
  is a warning, never an error).
- `CLAUDE.md` of the copy is a fixed neutral header plus exactly the `## Incident workflow` section of the real file,
  extracted at build time. An allowlist of one section means nothing else in the real file can leak, and there is no
  second copy to keep in sync.
- `tests/test_demo_app.py` is copied because the fix-proposer is told to prefer a change a test verifies. Its tests that
  import the generator moved to `tests/test_demo_frames.py`, so the copied file does not mention the generator. The
  comments in `demo_app/` that named the generator were reworded without moving a line.
- Reads are scoped by the permission rule `Read(./**)` (relative to the cwd, that is the copy); a bare `Read`, `Grep` or
  `Glob` is no longer allowed. Probed with Claude Code 2.1.302 under `--permission-mode dontAsk`: inside reads and Grep and
  Glob work, absolute and `../` reads outside are denied, an absolute Glob outside is denied, and a subagent is held to
  the same rules. Rules with drive letters (`//c/...`) would add Windows path problems, so the cwd-relative form is used.
- After each run the runner scans `audit.jsonl` and marks the run **contaminated** when a successful Read, Grep or Glob
  targeted a place outside the copy or below `docs/`, `src/`, `tests/fixtures` or `evals/`. A contaminated run counts
  as failing every check, its scenario is labelled `CONTAMINATED`, `RESULT` is `FAIL` and the exit code is 1. Calls that
  the permission rules denied are only recorded (`permission_denials`): a PostToolUse hook does not run for them and
  nothing was read.
- Because the agents no longer see the real `reports/`, the backup and restore logic of the runner is gone: the runner
  copies the reports out of the copy.
- A canary test builds the copy from the real project root and fails if any file contains a scenario id, a red-herring
  version, the name of the generator or a distinctive answer word. A negative control proves that it fails when a word is
  planted. The skill's worked example names `checkout-500`; this is pinned as a known mention, not changed here.

## Consequences

- A pass now means the agents found the cause from the logs and the allowed files. The measured baseline of the first
  calibration was taken before this change and has to be re-measured.
- `checkout-500` stays easier than the others: its bug is in `demo_app/` on purpose, and the skill's worked example is
  about it. The other three scenarios have no code in the copy, so the fix-proposer reports that it found none and
  proposes the rollback. Costs and turns will differ from the baseline.
- The isolation keeps an honest agent away from the answer key. It is not a security boundary (the same position as ADR
  [0005](0005-hooks-as-guardrails.md)): the user-level `~/.claude/CLAUDE.md` still loads, the scan looks only at Read,
  Grep and Glob, and Anthropic documents Read rules for Grep and Glob as best effort, which is why the scan exists.
- The hooks run unchanged in the copy: they use only the standard library and are started with `uv run --no-project` from
  `$CLAUDE_PROJECT_DIR`. `tests/test_workcopy_hooks.py` runs each of them from a copy without `src/`.
