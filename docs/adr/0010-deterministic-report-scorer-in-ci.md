# 0010: A deterministic report scorer runs in CI; live agent runs stay local

- **Status:** Accepted (issue #20)

## Context

Changes to the agents or the skill (for example #23: measure instead of infer, confidence as exactly `high`, `medium`
or `low`) were only noticed by reading reports by hand. Running the agent costs money, needs an API key, and gives
different text each time. CI has no secrets and must stay deterministic.

## Decision

- The evaluation is split. A **scorer** reads a finished report and checks it with plain Python against the expected
  result of the scenario (`src/incident_agent/evaluation/`, command `score-report`). No model is called, so it runs in
  `uv run pytest` on saved reports.
- The **runner** that produces reports (`claude -p`, N runs per scenario, pass-rate thresholds) is local and comes in a
  follow-up. It is never part of CI.
- Ground truth for counts comes from the deterministic database (seed 42), not from numbers copied into the code.
- Checks are exact where the contract is exact (severity, confidence, no inferred counts) and gating only where a miss is
  a real failure. `counts-reconciled` is a warning for `db-pool`, where a live run legitimately differed by two.
- `docs/examples/` stays unchanged and is scored as a historical oracle.
- Every scenario needs an `ExpectedResult` and a good fixture; a test enforces it.

## Consequences

- Regressions in the report format (the things #23 fixed) are caught in CI without a key or any cost.
- The scorer judges form and checkable facts, not quality. Patch quality and "is the confidence justified" stay human.
- Regexes on prose can be too strict or too loose; the expected patterns sit in one table per scenario so they can be
  tuned after the first live runs.
- New scenarios (#19) have a small, enforced checklist.
