# Contributing

Thanks for your interest in `incident-agent`. The project is built in phases described in
[docs/ROADMAP.md](docs/ROADMAP.md); that file is the source of truth for scope and process.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
```

## Workflow

- `main` is protected. No direct pushes; changes arrive via pull request and CI must be green.
- One short-lived branch per phase or change, created from `main`.
- PRs are squash-merged, and the branch is deleted afterwards.
- Keep PRs small. Two manageable pull requests are better than one huge one.

### Branch names

| Prefix | Used for | Example |
|---|---|---|
| `feature/` | New functionality | `feature/mcp-log-server` |
| `fix/` | Bug fix | `fix/stats-timezone` |
| `docs/` | Documentation only | `docs/release-v1` |
| `chore/` | Setup, CI, dependencies | `chore/repo-setup` |

### Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/): `feat`, `fix`, `docs`, `test`, `chore`.
Example: `feat(mcp): add get_error_stats tool`.

## Definition of Done

- **Works:** acceptance criteria are met and checked by hand.
- **Tested:** new logic has pytest tests; CI is green.
- **Clean:** ruff reports nothing; no secrets and no generated files in the commit.
- **Documented:** README or `docs/` are updated and the CHANGELOG has an entry under "Unreleased".
- **CLAUDE.md current:** new commands and conventions are recorded.

## Language

Code, commits, documentation and the README are written in English.
