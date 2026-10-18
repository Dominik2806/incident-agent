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

| | `checkout-500` | `db-pool` | `cert-expired` | `api-outage` |
|---|---|---|---|---|
| Severity | `SEV-2` | `SEV-3` | `SEV-1` | `SEV-2` (resolved, no downgrade) |
| Root cause names | `payment`, `v1.4.2`, `currency` and `KeyError` (or "no default" / "without a default") | `catalog`, `pool`, `exhaust`, `search` | `certificate`, `expire`, `payment`, `TLS` (or `x509`/`SSL`) | `Payvia`, an outage word (`outage`, `unavailable`, `503`, `timeout`) and a provider word (`provider`, `third-party`, `upstream`, `external`, `processor`) |
| Must not be the cause | catalog v2.8.0, auth v3.1.1 | auth v3.2.0 | auth v3.3.0 | checkout v2.7.1 |
| Ruled out | an older deployment (v2.8.0 or v3.1.1) | auth v3.2.0 | auth v3.3.0 | checkout v2.7.1 |
| Counts | per-client ok and failed for the four clients | total failed requests (555) | total failed requests (1961), no client split | total failed requests (156), no client split |

### Checks

| Check | Passes when | Gating |
|---|---|---|
| `report-complete` | every H2 of `template.md` (read at runtime) is present and not empty | yes |
| `root-cause-named` | the Root cause section contains every required term and none of the red herring | yes |
| `red-herring-ruled-out` | the Ruled out section names the red herring | yes |
| `severity` | the first `SEV-n` in the Severity section is the expected level | yes |
| `confidence` | the first word after "Confidence:" (markdown bold and punctuation ignored) is exactly `high`, `medium` or `low`. `medium-high`, `high/medium` or `80%` fail; the rest of the sentence is not read | yes |
| `counts-present` (checkout) | each of the four clients has an ok and a failed number, as a table row, as `client N/M` (failed/total), as an inline list under an explicit `(ok / failed)` or `(INFO / ERROR)` label, or as `client N ok / M failed` pairs | yes |
| `counts-reconciled` | checkout: the per-client (ok, failed) equal the database for one start time (14:00, 14:01, 14:03:00 or the first error); db-pool, cert-expired, api-outage: the true total (555, 1961, 156) appears in the report | checkout yes, the other three **warning** |
| `no-inferred-counts` | no count is presented as `inferred`, `estimated` or `extrapolated` | yes |

Notes:

- `counts-reconciled` is only a warning for `db-pool`: a live run reported 553 failed requests in the summary while the
  15-minute windows add up to 555. The numbers are still printed so the gap is visible.
- `no-inferred-counts` looks at the words next to the keyword (a table cell, or about 40 characters) after removing
  versions and times. "This is inferred from the code comment" and "the trigger is hypothesis" pass; `0 of 856, inferred`
  fails. "assumed" is deliberately not a keyword (a default currency is a legitimate assumption).
- Per-client counts are accepted in four formats. **Every** table row and every `client N/M` occurrence in the whole
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
  - **Inline list, INFO/ERROR:** `(INFO / ERROR)` (or `INFO / ERROR:`, any case) is the same label with INFO as ok and
    ERROR as failed, e.g. `Client split (INFO / ERROR): web-2.30.4 13 / 275; web-2.31.0 1092 / 0`, with the same rules
    as above. Prose such as `(1092 OK)` is not a count. The reversed `(ERROR / INFO)` is **not** a label: its bare
    pairs keep the failed/total reading, so INFO-first numbers under it fail.
  - **Explicit pair label:** `client N ok / M failed` (also `failures`) is always read as **ok = N, failed = M**,
    whatever else is on the line. It also declares that order for every bare `client N / M` that follows it on the
    **same line**, e.g. `web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; web-2.30.4 13 / 275`. Bare pairs before
    it, on other lines, or on a line without any explicit label keep the failed/total reading (an unlabeled
    `web-2.31.0 1092 / 0` stays invalid). The reversed `client N failed / M ok` is read as failed = N, ok = M. A line
    with explicit pairs of both orders (the `(ok / failed)` and `(INFO / ERROR)` labels count as ok/failed) is unreadable: it gives no
    counts and `counts-present` and `counts-reconciled` fail with "ok/failed and failed/ok pairs on one line".
  - `counts-reconciled` compares the resulting (ok, failed) exactly with the database in both forms.
- The Root cause check accepts `KeyError` or the same idea in words (`no default`, `without a default`), but
  `payment`, `v1.4.2` and `currency` must still appear in that section, and the red herrings must not appear in the
  **cause statement**. The required terms are searched in the whole Root cause section. The cause statement is the
  section without its **confidence statement**: that starts at the first `Confidence` label followed by `high`,
  `medium` or `low` (bold markers and a colon are fine) that begins a line, a paragraph or a sentence, and runs to the
  end of that paragraph, plus directly following paragraphs that start with `Reason`. Text before the label in the same
  paragraph and any later paragraph belong to the cause statement. Without such a label the whole section is checked.
- Known limits:
  - `db-pool`, `cert-expired` and `api-outage` `counts-reconciled` only look for the total (555, 1961, 156) anywhere in the report. It does not check what the
    number is attached to.
  - A forbidden pattern in the cause statement fails `root-cause-named` even when it is negated ("not caused by
    auth v3.2.0" fails). Put the ruling out in the Ruled out section. The confidence statement is exempt, because it
    may say what could not be excluded ("the checkout v2.7.1 deployment cannot be fully excluded"); what is checked
    there is only the level (`confidence`). A confidence statement that really blames a red herring is not detected,
    and a required cause term found only in the confidence statement still counts.
  - `red-herring-ruled-out` for `checkout-500` needs only one of the two older deployments (v2.8.0 or v3.1.1).
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
| `--total-budget-usd` | `3.00` | no new run starts unless `spent so far + per-run cap <= total cap`, so the full per-run cap is reserved before each run (see [Budget rule](#budget-rule) and [Measured baseline](#measured-baseline)) |
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

- **Working copy:** `claude` does not run in the repository. The runner builds a sanitized copy in the system temp
  directory (`<temp>/incident-eval-<random>/work`), starts `claude` with it as the current directory and deletes it
  after the run. The copy holds only `.claude/` (agents, skill, hooks, `settings.json`), `demo_app/*.py`,
  `tests/test_demo_app.py`, an empty `reports/` and a generated `CLAUDE.md`. See [Isolation and threat
  model](#isolation-and-threat-model).
- **Command:** `claude -p --output-format json --max-budget-usd ... --mcp-config <run>/mcp.json --strict-mcp-config
  --setting-sources project --tools Skill,Agent,Read,Grep,Glob,Write --allowedTools ... --permission-mode dontAsk
  --permission-prompts none --no-session-persistence`. The prompt is the demo prompt of [scenarios.md](scenarios.md),
  sent on stdin (the tool flags are variadic and would swallow an argument). There is no `--max-turns` in Claude Code
  2.1.302; the caps are the budget and the timeout, and `num_turns` is only recorded. Flags come from `claude --help` of
  the installed version, so re-check them after an upgrade. No Bash and no Edit tool exist in a run, and only
  `Edit(reports/**)` is allowed (which also covers the `Write` tool). Path rules for file writes must be written as
  `Edit(...)`, never `Write(...)`: Claude Code checks file permissions against `Edit` and `Read` rules only. Reads are
  allowed as `Read(./**)` (below the current directory, that is the working copy), never as a bare `Read`, `Grep` or
  `Glob`.
- **Database:** `build_database(scenario, 42, <run>/incident.db)` (what `generate-data` does), also used for the ground truth.
- **MCP server:** a generated config starts the server under the name `incident-logs` with `INCIDENT_DB_PATH` set to that
  database. It uses the venv's `incident-logs-server` executable, or `python -m incident_agent.mcp_server`, never
  `uv run` (which could sync and reinstall).
- **Environment:** `INCIDENT_AUDIT_LOG=<run>/audit.jsonl`, `INCIDENT_NOTIFY_DESKTOP=0`, `SLACK_WEBHOOK_URL` removed.
  Secrets are never written to the results.
- **Report:** the agent writes to `reports/` of the working copy. After the run the runner copies every file there into
  the run folder, newest first, and the newest is scored. The `reports/` of the real project is never involved, so there
  is nothing to back up or restore.
- **Contamination:** the runner scans the run's `audit.jsonl` (see [below](#isolation-and-threat-model)).

Results (gitignored, never committed): `evals/results/<UTC timestamp>/<scenario>/run-N/` holds `incident.db`, `mcp.json`,
`command.json` (with the `cwd` that was used), `workcopy-manifest.json` (every file of the copy with its sha256),
`audit.jsonl`, `claude-result.json`, `stderr.txt`, `reports/` (the picked-up report keeps its own file name, e.g.
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
(not a pass). A scenario with a **contaminated** run (see below) is labelled `CONTAMINATED`, which takes precedence over
every other label. Exit code `0` if every selected scenario passes, `1` otherwise; `--dry-run` exits `0`. The summary prints
per-check pass rates and the totals of cost, turns and duration. The data is fixed (seed 42), the model is not, so
a single run is a smoke test, not a verdict on the agent.

### Isolation and threat model

In the first live runs the agents worked in the repository root, so a repo-wide Grep, Glob or Read reached the answer key
(issue #35, ADR [0011](adr/0011-isolated-working-copy-for-live-evaluation.md)). The saved audit logs show reads of the
generator (`src/incident_agent/data/scenarios.py`) and of `docs/scenarios.md`. The fix-proposer must search code, so the
environment changed, not the agents.

- **What is in the copy:** exactly the table below, built by `workcopy.py`; `tests/test_workcopy.py` compares the built
  copy with it, with a list of forbidden paths and with a canary.

  | In the copy | Why |
  |---|---|
  | `.claude/settings.json`, `.claude/agents/*.md`, `.claude/hooks/*.py`, `.claude/skills/incident-runbook/*.md` | the agents, the skill and the hooks (standard library only; `settings.json` starts them with `uv run --no-project` from `$CLAUDE_PROJECT_DIR`, so they run without `src/`) |
  | `demo_app/**/*.py` | the fix-proposer reads the failing code |
  | `tests/test_demo_app.py` | the fix-proposer is told to prefer a change a test verifies and to name it (no generator import: the generator tests are in `tests/test_demo_frames.py`) |
  | `reports/` (empty) | the only place the agents may write (`Edit(reports/**)`) |
  | `CLAUDE.md` (generated) | a fixed neutral header plus exactly the `## Incident workflow` section of the real `CLAUDE.md`, so there is no second copy and nothing else of the real file (scenarios, the evaluation) can leak |

  Not in it: `docs/`, `src/`, `evals/`, `data/`, `logs/`, the README, CHANGELOG, the other tests, `tests/fixtures`,
  `.mcp.json` (the run passes `--mcp-config`), `.claude/settings.local.json`, `.git`.
- **Where it lives:** in the system temp directory, never inside the repository. Claude Code loads `CLAUDE.md` from the
  parent directories of its current directory, so a copy inside the repository would load the real one, and `../..` would
  lead to the answer key. The database, `mcp.json` and the audit log stay in the run folder, outside the copy.
- **What the agents may read:** `Read(./**)` is the only read rule. Checked with Claude Code 2.1.302 and
  `--permission-mode dontAsk`: a Read inside the copy works, a Read outside it (absolute path or `../`) is denied, Grep
  and Glob work inside it and an absolute path outside is denied, a `../` Glob pattern finds nothing (no leak), and a
  subagent with Read and Grep is held to the same rules. Anthropic documents Read rules for Grep and Glob as best effort,
  which is why the audit scan exists.
- **Contaminated runs:** after each run the runner scans `audit.jsonl` for Read, Grep and Glob calls whose `file_path`,
  `path`, absolute or `..` `pattern` or `glob` is outside the copy or below `docs/`, `src/`, `tests/fixtures` or
  `evals/`. A hit makes the run **contaminated**: it counts as failing every check, the scenario is labelled
  `CONTAMINATED`, `RESULT` is `FAIL` and the exit code is 1, whatever the report says. The evidence is printed under
  the run and stored in `record.json`.
- **Denied attempts:** a call the permission rules denied is listed in `permission_denials` of `claude-result.json`. It
  is recorded (`blocked_attempts` in `record.json`, one line in the summary) but does not contaminate: nothing was
  read. A PostToolUse hook does not run for a denied call, so `audit.jsonl` holds only calls that succeeded, and a hit in
  the scan means a real leak.

What the isolation does **not** protect against:

- The intentional bug of `demo_app/` and the `xfail` reason in `tests/test_demo_app.py` describe the `checkout-500`
  cause on purpose. So for `checkout-500` the answer is in files the agents are meant to read; the other three
  scenarios have no code in the copy, and the fix-proposer has to report that it did not find it.
- A user-level `~/.claude/CLAUDE.md` is still loaded (it is not repository content).
- The model's own knowledge, and the logs themselves: the MCP server is the legitimate source of evidence.
- A hole in the permission rules, or a tool that is not Read, Grep or Glob. The scan only looks at those three.
- Strings longer than 300 characters in the audit log are cut by the audit hook; the scan sees the first 300, which always
  include the start of the path.
- It is not a security boundary against a malicious agent (the same note as for the hooks, ADR
  [0005](adr/0005-hooks-as-guardrails.md)), it keeps an honest agent away from the answer key.

If removing the copy fails (on Windows a file can stay locked), the runner prints a warning and keeps the verdict;
`record.json` has `workcopy_removed: false` and the leftover folder is named in the warning.

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
- One run per scenario says nothing about how often a scenario passes. The calibration with `--runs 3` per scenario is
  below.

### Measured baseline

> **Caveat: measured before the isolation.** This baseline (and the cost table above) comes from runs in which the agents
> worked in the repository root and could read the generator and the docs (issue #35). It has to be re-measured with the
> working copy; until then treat the numbers as an upper bound. Costs and turns will change too: without the generator
> the fix-proposer finds no code for `db-pool`, `cert-expired` and `api-outage` and reports that. The numbers below are
> unchanged.

Measured on 2026-10-17 with agent v1.2.1, the scorer of `main` after #30, `--all --runs 3` (3 runs per scenario, 6 in
total), on one machine. The numbers come from the saved reports of the live run, re-scored with the current scorer
(`score-report <report> --scenario <s>`, no model, no cost).

`checkout-500`:

| Check | Passed |
|---|---|
| `report-complete`, `root-cause-named`, `red-herring-ruled-out`, `severity`, `confidence`, `counts-present`, `counts-reconciled`, `no-inferred-counts` | 3 of 3 each |

`db-pool`:

| Check | Passed |
|---|---|
| `report-complete`, `root-cause-named`, `red-herring-ruled-out`, `severity`, `confidence`, `no-inferred-counts` | 3 of 3 each |
| `counts-reconciled` (warning) | 2 of 3 (run 3 does not contain the total of 555 failed) |

| Scenario | Cost per run | Duration per run | Turns | Total |
|---|---|---|---|---|
| `checkout-500` | $0.50, $0.46, $0.46 | 133 s, 141 s, 143 s | 9, 6, 4 | $1.42, 417 s |
| `db-pool` | $0.40, $0.42, $0.36 | 129 s, 130 s, 129 s | 2, 2, 4 | $1.19, 389 s |
| both | | | | $2.61, 806 s |

How to read it:

- Three runs per scenario is a small sample. The numbers show that the agents pass with the current prompts, not a
  precise pass rate. Turns (2 to 9) vary between runs and carry no meaning.
- **The live run itself printed FAIL for `checkout-500`.** With the v1.2.1 scorer, `counts-present` and
  `counts-reconciled` passed in only 2 of 3 runs (80% is required): run 3 was a correct report that wrote
  `web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; ...`, a format the scorer did not read yet (a false negative, fixed
  in #30; this report is the `mixed-labels` fixture below). The pass counts above are from re-scoring the saved reports
  with the current scorer.
- Five real reports from live runs are regression fixtures in `tests/fixtures/eval/`, each exposing a
  format the scorer had to learn:
  - `real-checkout-500-failed-total.md`: `client N/M (pct)` pairs, read as failed/total.
  - `real-checkout-500-ok-failed-inline.md`: ok/failed counts in an inline list under an explicit `(ok / failed)` label.
  - `real-checkout-500-mixed-labels.md`: explicit `N ok / M failed` pairs mixed with bare `N / M` pairs on one line.
  - `real-checkout-500-info-error-labels.md`: counts in an inline list under an `(INFO / ERROR)` label (from the first
    isolated live run).
  - `real-api-outage-red-herring-in-confidence.md`: the red herring (`v2.7.1`) named only in the confidence statement
    of the Root cause section; it must pass.

To reproduce (about $2.61, 13 minutes; results go to `evals/results/<UTC timestamp>/`):

```bash
uv run --no-sync run-eval --all --runs 3 --total-budget-usd 4.00
```

With the default total cap of $3.00 a six-run calibration stops after five runs, although each run costs about $0.4 to
$0.5 (see the budget rule below). The `4.00` of the command above is enough for six runs at that cost.

Run it before a release and after changing `.claude/agents/` or the skill. Code: `runplan.py` (pure: command, results,
verdict), `runner.py` (the subprocess seam, folders, report pickup), `run_cli.py`. Tests: `tests/test_eval_runner.py`
with a fake `claude` script (`tests/fake_claude.py`), no network and no model.

### Budget rule

The runner reserves the full per-run cap before each run: a run starts only if

```
spent so far + per-run cap <= total cap        (spent = actual cost of the finished runs)
```

So N runs at an actual cost `c` each need a total cap of about `(N - 1) x c + per-run cap`, and `N x per-run cap` is
always enough. With the default per-run cap of $1.00:

| Runs | Needed at about $0.40 to $0.50 per run | Always safe |
|---|---|---|
| 6 | $3.00 to $3.50 (the default $3.00 stops after five; `4.00` is enough) | $6.00 |
| 12 (`--all --runs 3` with four scenarios) | roughly $6.00 ($5.40 to $6.50) | $12.00 |

Since `api-outage` exists, `--all` plans four scenarios (`checkout-500`, `db-pool`, `cert-expired`, `api-outage`). The
measured baseline above covers `checkout-500` and `db-pool` only.
