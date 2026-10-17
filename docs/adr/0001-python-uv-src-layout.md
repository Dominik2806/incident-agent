# 0001: Python with uv and a src layout

- **Status:** Accepted (PR #1)

## Context

The project needs a data generator, an MCP server and hook scripts, and it must run from a fresh clone with as few
steps as possible. The official MCP SDK has a Python implementation with FastMCP.

## Decision

- Python 3.12 (`requires-python = ">=3.12"`, `.python-version`), managed with uv. `uv.lock` is committed and CI runs
  `uv sync --locked`.
- A `src` layout: the package is `src/incident_agent/`, built with `uv_build`. Console scripts `generate-data` and
  `incident-logs-server` are declared in `pyproject.toml`.
- Quality tools are pytest and ruff (`ruff check` and `ruff format --check` in CI), run by GitHub Actions on every
  pull request.
- Hooks run with `uv run --no-project python ...`, so they need no virtual environment and only use the standard
  library.

## Consequences

- One tool installs Python and the dependencies and runs everything; `uv run generate-data` works on a fresh clone.
- Tests import the installed package, not the working directory, which catches packaging mistakes.
- uv is a hard requirement, also for the hooks: without it they cannot start (see
  [hooks.md](../hooks.md#limitations)).
- Code that is not part of the wheel (`demo_app/`) needs `pythonpath = ["."]` for pytest.
