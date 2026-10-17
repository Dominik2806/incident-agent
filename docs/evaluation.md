# Evaluation

Does the agent find the expected root cause? The answer is checked in two layers. This page describes the first one,
which exists (the report scorer), and the second one, which is planned (a local runner).

| Layer | What it does | Needs an API key | Runs in CI |
|---|---|---|---|
| **Scorer** (`score-report`) | Checks a finished incident report against the expected result of a scenario | No | Yes (`uv run pytest`) |
| **Runner** (`run-eval`, follow-up) | Runs the agent N times per scenario and scores each report | Yes | No, local only |

The scorer calls no model. Same report in, same verdict out. See
[ADR 0010](adr/0010-deterministic-report-scorer-in-ci.md) for the reasoning.

## The scorer

```bash
uv run score-report docs/examples/incident-report-db-pool.md --scenario db-pool
uv run score-report reports/2026-03-12-checkout-500.md --scenario checkout-500
```

The command builds the scenario database in a temporary folder (deterministic, seed `42` unless you pass `--seed`; or
pass `--db`), reads the ground truth from it, prints one line per check and exits `0` if every gating check passes,
`1` otherwise. Warnings (see below) are listed separately and never change the exit code.

Code: `src/incident_agent/evaluation/` (`expected.py` expected results and ground truth, `report.py` Markdown parsing,
`checks.py` one function per check, `scoring.py` orchestration and output, `cli.py`). Tests: `tests/test_eval_scoring.py`.

### Expected result per scenario

Values come from [scenarios.md](scenarios.md) and live in `EXPECTED` in `expected.py`.

| | `checkout-500` | `db-pool` |
|---|---|---|
| Severity | `SEV-2` | `SEV-3` |
| Root cause names | `payment`, `v1.4.2`, `KeyError`, `currency` | `catalog`, `pool`, `exhaust`, `search` |
| Must not be the cause | catalog v2.8.0, auth v3.1.1 | auth v3.2.0 |
| Ruled out | an older deployment (v2.8.0 or v3.1.1) | auth v3.2.0 |
| Counts | per-client ok and failed for the four clients | total failed requests (555) |

### Checks

| Check | Passes when | Gating |
|---|---|---|
| `report-complete` | every H2 of `template.md` (read at runtime) is present and not empty | yes |
| `root-cause-named` | the Root cause section contains every required term and none of the red herring | yes |
| `red-herring-ruled-out` | the Ruled out section names the red herring | yes |
| `severity` | the first `SEV-n` in the Severity section is the expected level | yes |
| `confidence` | the first word after "Confidence:" (markdown bold and punctuation ignored) is exactly `high`, `medium` or `low`. `medium-high`, `high/medium` or `80%` fail; the rest of the sentence is not read | yes |
| `counts-present` (checkout) | a table row for each of the four clients has an ok and a failed number | yes |
| `counts-reconciled` | checkout: the per-client (ok, failed) equal the database for one start time (14:00, 14:01, 14:03:00 or the first error); db-pool: the true total (555) appears in the report | checkout yes, db-pool **warning** |
| `no-inferred-counts` | no count is presented as `inferred`, `estimated` or `extrapolated` | yes |

Notes:

- `counts-reconciled` is only a warning for `db-pool`: a live run reported 553 failed requests in the summary while the
  15-minute windows add up to 555. The numbers are still printed so the gap is visible.
- `no-inferred-counts` looks at the words next to the keyword (a table cell, or about 40 characters) after removing
  versions and times. "This is inferred from the code comment" and "the trigger is hypothesis" pass; `0 of 856, inferred`
  fails. "assumed" is deliberately not a keyword (a default currency is a legitimate assumption).
- Per-client counts are read from Markdown tables (`| client | ok | failed |`). Counts only in prose are "not present".
- Not checked: quality of the patch, wording, and whether the confidence level is justified. Those need a human or an LLM
  judge, and would not be deterministic.

### Reference reports

- `tests/fixtures/eval/good-<scenario>.md`: reports that pass every check. A negative test changes one thing in them
  (severity, a section, a count) and expects exactly that check to fail.
- `docs/examples/` stays unchanged as the historical oracle (real runs from before #23):
  `incident-report-db-pool.md` passes, `incident-report.md` fails only `no-inferred-counts` (its "ios-5.8.1 (0 of 856,
  inferred)"). A test pins this.

### Adding a scenario

1. Register the generator function and describe it in [scenarios.md](scenarios.md) (as before).
2. Add an `ExpectedResult` to `EXPECTED` in `expected.py` (and ground-truth queries in `ground_truth` if the counts differ).
3. Add `tests/fixtures/eval/good-<scenario>.md`.

`test_every_scenario_has_an_expected_result_and_a_fixture` fails until all three exist.

## The runner (follow-up, not in this change)

`run-eval` will run the full triage, root-cause and fix-proposer flow through the `incident-runbook` skill with
`claude -p`, once per run, and score each report with the scorer above. Planned design:

- **Per run:** a fresh database (`INCIDENT_DB_PATH`), a generated `--mcp-config` (project MCP servers cannot be approved in
  `-p` mode), pre-allowed tools only, `--max-turns` and `--max-budget-usd` caps, a separate `INCIDENT_AUDIT_LOG`, desktop
  and Slack notifications off. The new report is moved into a results folder next to the JSON result (cost, turns) and
  the audit log. Exact `claude` flags are checked against the installed version when it is built.
- **N runs per scenario**, sequential (default 5). The data is fixed (seed 42), the model is not, so a single run is
  never a verdict.
- **Thresholds:** `root-cause-named` and `severity` must pass in **100%** of the runs. Every other check must pass in
  about **80%** of them (4 of 5). Per-check pass rates are printed so a flaky check is visible.
- **Results:** a gitignored folder (like `reports/` and `logs/`); cost per run and in total is recorded, not guessed.
- **Not in CI:** it needs an API key (no secrets in CI) and costs money. Run it before a release and after changing
  `.claude/agents/` or the skill.
