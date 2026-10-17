# 0002: Synthetic, deterministic data in SQLite instead of a real log backend

- **Status:** Accepted (PR #2)

## Context

The agent needs logs, deployments and stack traces with a known incident, so that the result can be checked. A real
production system is not available, would be hard to reproduce for visitors, and could not be shared.

## Decision

- A generator in `src/incident_agent/data/` writes one day (2026-03-12 UTC) for `checkout`, `payment`, `catalog` and
  `auth` into SQLite (`logs`, `deployments`, `errors`), then adds a scenario on top.
- The output is deterministic: a fixed reference date, a seeded `random.Random` (default seed 42) and never
  `datetime.now()`. The same seed gives the same database.
- Scenarios are functions registered in `SCENARIOS`, each with an expected cause and severity in
  [scenarios.md](../scenarios.md), plus noise and red herrings so that the agent has to search.
- The database is generated into `data/` and is gitignored.

## Consequences

- Everything runs offline in seconds, and tests can pin facts (first error at 14:03:32, about 18% failure rate).
- The expected answer is known, which makes the demo and a later evaluation possible.
- The data is only as realistic as the generator. Findings do not transfer to a real system without a real data
  source (see the outlook in the [roadmap](../ROADMAP.md)).
- Determinism must be protected: see [0009](0009-scenario-isolation-and-pinned-hash.md).
