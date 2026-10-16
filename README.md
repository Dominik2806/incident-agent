# incident-agent

[![CI](https://github.com/Dominik2806/incident-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Dominik2806/incident-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Claude Code agent that investigates production incidents using subagents, a skill, a custom MCP server and hooks.

> **Status:** early development. See the [roadmap](docs/ROADMAP.md) for the current phase.

## What it does

The agent mimics an on-call engineer. Given a report such as

> Since about 2 pm customers are getting 500 errors at checkout. What is going on?

it triages the incident, finds the root cause in the logs, proposes a fix and writes an incident report.
The agent proposes, the human decides: nothing is applied automatically.

No real production environment is needed. A script generates realistic sample logs with a built-in incident,
and a custom MCP server makes them queryable for Claude. Everything runs locally.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

### Generate the sample data

```bash
uv run generate-data --scenario checkout-500
```

This writes a SQLite database to `data/incident.db` (gitignored, overwritten on every run) with 24 hours of
logs for the services `checkout`, `payment`, `catalog` and `auth`, including a built-in incident, and prints
row counts and an hour-by-hour table of errors per service. Options:

| Option | Default | Meaning |
|---|---|---|
| `--scenario` | `checkout-500` | Scenario to generate |
| `--seed` | `42` | Random seed; the same seed always produces identical data |
| `--output` | `data/incident.db` | Path of the database file |

The scenarios are described in [docs/scenarios.md](docs/scenarios.md).

More commands will be added as the project grows.

## Project layout

```text
.claude/        subagents, skills and hooks for Claude Code
.github/        CI workflow, PR and issue templates
src/incident_agent/   Python package (log generator, queries, MCP server)
demo_app/       mini checkout service with a built-in bug
tests/          pytest suite
docs/           roadmap, architecture and examples
```

## Documentation

- [Roadmap](docs/ROADMAP.md)
- [Scenarios](docs/scenarios.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
