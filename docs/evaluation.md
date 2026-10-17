# Evaluation

Does the agent find the expected root cause? The answer is checked in two layers. This page describes the first one,
which is the report scorer, and the second one, the local runner.

| Layer | What it does | Needs an API key | Runs in CI |
|---|---|---|---|
| **Scorer** (`score-report`) | Checks a finished incident report against the expected result of a scenario | No | Yes (`uv run pytest`) |
| **Runner** (`run-eval`) | Runs the agent N times per scenario and scores each report | Yes | No, local only |

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
| Root cause names | `payment`, `v1.4.2`, `currency` and `KeyError` (or "no default" / "without a default") | `catalog`, `pool`, `exhaust`, `search` |
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
| `counts-present` (checkout) | each of the four clients has an ok and a failed number, as a table row, as `client N/M` (failed/total) or as an inline list under an explicit `(ok / failed)` label | yes |
| `counts-reconciled` | checkout: the per-client (ok, failed) equal the database for one start time (14:00, 14:01, 14:03:00 or the first error); db-pool: the true total (555) appears in the report | checkout yes, db-pool **warning** |
| `no-inferred-counts` | no count is presented as `inferred`, `estimated` or `extrapolated` | yes |

Notes:

- `counts-reconciled` is only a warning for `db-pool`: a live run reported 553 failed requests in the summary while the
  15-minute windows add up to 555. The numbers are still printed so the gap is visible.
- `no-inferred-counts` looks at the words next to the keyword (a table cell, or about 40 characters) after removing
  versions and times. "This is inferred from the code comment" and "the trigger is hypothesis" pass; `0 of 856, inferred`
  fails. "assumed" is deliberately not a keyword (a default currency is a legitimate assumption).
- Per-client counts are accepted in three formats. **Every** table row and every `client N/M` occurrence in the whole
  report is searched (the first mention of a client is often prose or an evidence table without counts). Identical
  repeats are fine; if the occurrences give different numbers, `counts-reconciled` fails with a "conflicting" detail.
  Other prose ("failed 275 of 288") is "not present".
  - **Table:** `| client | ok | failed |` (columns found from the header, else the two numbers after the name).
  - **Pair, failed/total:** the client name followed on the same line by `N/M`, optionally with a percentage, e.g.
    `web-2.30.4 275/288 (95.5%), web-2.31.0 0/1092`. `N` is read as **failed** out of `M` total, so ok = `M - N`; it is
    never read as ok/failed. `N` greater than `M` is invalid and counts as missing.
  - **Inline list, ok/failed:** if `(ok / failed)` (or `ok / failed:`) appears earlier on the same line, every
    `client N/M` after it on that line is read as **ok = N, failed = M**, zeros included, e.g.
    `by client (ok / failed): web-2.30.4 13 / 275; web-2.31.0 1092 / 0`. An explicit label wins over the failed/total
    reading; without a label the failed/total rule above applies unchanged. The label only covers its own line and
    must come before the list. Prose such as "had 0 failures in 1,948 lines" is not a count.
  - `counts-reconciled` compares the resulting (ok, failed) exactly with the database in both forms.
- The Root cause check accepts `KeyError` or the same idea in words (`no default`, `without a default`), but
  `payment`, `v1.4.2` and `currency` must still appear in that section, and the red herrings must not.
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

## The runner

`run-eval` runs the full triage, root-cause, fix-proposer and report flow through the `incident-runbook` skill with
`claude -p`, once per run, against a freshly generated database, and scores each report with the scorer above. It calls
a model, costs money and is **never part of CI** (and never started by the agent itself).

```bash
uv run --no-sync run-eval --scenario checkout-500                 # one run (the default), asks to confirm
uv run --no-sync run-eval --scenario db-pool --runs 3
uv run --no-sync run-eval --all --runs 3 --yes                    # every scenario, no confirmation
uv run --no-sync run-eval --all --dry-run                         # print the commands, call nothing
```

The `run-eval` command exists after a `uv sync`. Until then use
`uv run --no-sync python -m incident_agent.evaluation.run_cli ...`. Do not run `uv sync` while a run is live: on Windows
the running MCP server locks its executable.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--scenario S` / `--all` | required | one scenario, or every scenario in `EXPECTED` |
| `--runs N` | `1` | runs per scenario, sequential, no retries |
| `--max-budget-usd` | `1.00` | hard cap per run (`claude --max-budget-usd`) |
| `--total-budget-usd` | `3.00` | no new run starts if it could push the spent total over this |
| `--timeout-s` | `900` | wall-clock limit per run; the process tree is killed, the run counts as failed |
| `--model` | CLI default | passed to `claude --model` |
| `--claude-path` | `$CLAUDE_BIN`, then `PATH` | the `claude` executable (`.exe` or `.cmd` both work) |
| `--results-dir` | `evals/results` | where results go |
| `--dry-run` | off | print the planned commands, paths and environment, call nothing, create nothing |
| `--yes` | off | skip the confirmation (without it a non-terminal run is refused) |

The budget defaults come from the first measured runs (see the cost table): the observed cost is about half of the
per-run cap. Both are flags.
Before a live run `run-eval` prints, for example, `Planned: 1 run(s) x 1 scenario(s) (db-pool), up to $1.00 per run,
maximum $1.00 (total cap $3.00)` and asks `Proceed? [y/N]`.

### What one run does

- **Command:** `claude -p --output-format json --max-budget-usd ... --mcp-config <run>/mcp.json --strict-mcp-config
  --setting-sources project --tools Skill,Agent,Read,Grep,Glob,Write --allowedTools ... --permission-mode dontAsk
  --permission-prompts none --no-session-persistence`. The prompt is the demo prompt of [scenarios.md](scenarios.md),
  sent on stdin (the tool flags are variadic and would swallow an argument). There is no `--max-turns` in Claude Code
  2.1.302; the caps are the budget and the timeout, and `num_turns` is only recorded. Flags come from `claude --help` of
  the installed version, so re-check them after an upgrade. No Bash and no Edit tool exist in a run, and only
  `Edit(reports/**)` is allowed (which also covers the `Write` tool). Path rules for file writes must be written as
  `Edit(...)`, never `Write(...)`: Claude Code checks file permissions against `Edit` and `Read` rules only.
- **Database:** `build_database(scenario, 42, <run>/incident.db)` (what `generate-data` does), also used for the ground truth.
- **MCP server:** a generated config starts the server under the name `incident-logs` with `INCIDENT_DB_PATH` set to that
  database. It uses the venv's `incident-logs-server` executable, or `python -m incident_agent.mcp_server`, never
  `uv run` (which could sync and reinstall).
- **Environment:** `INCIDENT_AUDIT_LOG=<run>/audit.jsonl`, `INCIDENT_NOTIFY_DESKTOP=0`, `SLACK_WEBHOOK_URL` removed.
  Secrets are never written to the results.
- **Report:** the agent writes to `reports/` in the project. The runner remembers the bytes of every file there before the
  run, copies each new or changed file into the run folder and **restores** `reports/`: new files are removed, a file the
  run overwrote gets its original bytes back, a deleted one is recreated. If several reports appear, the newest is scored.

Results (gitignored, never committed): `evals/results/<UTC timestamp>/<scenario>/run-N/` holds `incident.db`, `mcp.json`,
`command.json`, `audit.jsonl`, `claude-result.json`, `stderr.txt`, `reports/` (the picked-up report keeps its own file name, e.g.
`reports/2026-03-12-checkout-500.md`), `score.txt` and `record.json`; the timestamp folder holds `summary.txt` and `summary.json`.

### Verdict

A scenario passes if `root-cause-named` and `severity` pass in **100%** of the runs and every other gating check passes in
at least **80%** of them (exact integer arithmetic). A run without a report, with an error result, a non-zero exit or a
timeout counts as failing every check.

| Runs | `root-cause-named`, `severity` | every other gating check |
|---|---|---|
| 1 | 1 of 1 | 1 of 1 |
| 3 | 3 of 3 | 3 of 3 (2 of 3 is 67%) |
| 5 | 5 of 5 | 4 of 5 |

Warnings (for example `counts-reconciled` on `db-pool`) are counted and printed separately and never change the verdict.
If the total budget stops the loop, the remaining runs are recorded as skipped and the scenario is labelled `INCOMPLETE`
(not a pass). Exit code `0` if every selected scenario passes, `1` otherwise; `--dry-run` exits `0`. The summary prints
per-check pass rates and the totals of cost, turns and duration. The data is fixed (seed 42), the model is not, so
a single run is a smoke test, not a verdict on the agent.

### Cost per run

First real measurements (Claude Code 2.1.302, project hooks and agents, **one run per scenario**; `total_cost_usd` and
`num_turns` are in `claude-result.json`). This is a cost measurement, not a pass rate.

| Scenario | Cost | Duration | Note |
|---|---|---|---|
| `checkout-500` | $0.50 | 114 s | before the per-client counts parser fix |
| `checkout-500` | $0.44 | 113 s | after the fix |
| `db-pool` | $0.51 | 125 s | |
| `--all` (checkout-500 + db-pool) | $0.95 | 239 s | total of one run per scenario |

- The reported turns were 2 to 9 and vary between runs. They are only recorded and carry no meaning for cost.
- One run per scenario says nothing about how often a scenario passes. **Before relying on the thresholds, calibrate
  with `--runs 3` per scenario** (about $3 in total, which is also the default total cap).

Run it before a release and after changing `.claude/agents/` or the skill. Code: `runplan.py` (pure: command, results,
verdict), `runner.py` (the subprocess seam, folders, report pickup), `run_cli.py`. Tests: `tests/test_eval_runner.py`
with a fake `claude` script (`tests/fake_claude.py`), no network and no model.
