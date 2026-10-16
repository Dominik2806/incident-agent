# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project skeleton: `pyproject.toml` (uv, Python 3.12, src layout), folder structure, placeholder test.
- GitHub Actions workflow running ruff and pytest on pull requests and pushes to `main`.
- README skeleton, `CONTRIBUTING.md`, `CLAUDE.md`, pull request and issue templates.
- `.gitignore` entries for `reports/`, `logs/`, `*.db` and `.env`.
- Sample data generator in `src/incident_agent/data/`: SQLite schema (`logs`, `deployments`, `errors`),
  24 hours of deterministic normal operation on the fixed date 2026-03-12 (seeded, UTC) and a scenario registry.
- Scenario `checkout-500`: `payment` v1.4.2 at 14:01, about 18% of checkouts fail with `KeyError: 'currency'`
  from 14:03, plus noise (recurring WARNs, one-off errors, older deployments).
- Console script `generate-data` (`--scenario`, `--seed`, `--output`) that prints row counts and errors per hour.
- `docs/scenarios.md` describing the scenario, its timeline, expected root cause and red herrings.
- Tests for schema, time range, determinism, incident start, error rate and consistency of failed requests.
- `/data/` in `.gitignore` for the generated database.
