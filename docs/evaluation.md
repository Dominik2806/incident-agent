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

| | `checkout-500` | `db-pool` | `cert-expired` | `api-outage` | `memory-leak` |
|---|---|---|---|---|---|
| Severity | `SEV-2` | `SEV-3` | `SEV-1` | `SEV-2` (resolved, no downgrade) | `SEV-3` (ongoing, non-core) |
| Root cause names | `payment`, `v1.4.2`, `currency` and `KeyError` (or "no default" / "without a default") | `catalog`, `pool`, `exhaust`, `search` | `certificate`, `expire`, `payment`, `TLS` (or `x509`/`SSL`) | `Payvia`, an outage word (`outage`, `unavailable`, `503`, `timeout`) and a provider word (`provider`, `third-party`, `upstream`, `external`, `processor`) | `catalog`, `v2.9.0` and leak or growth wording (`leak`, `unbounded`; or `memory`, `memory usage`, `memory consumption` or `rss` followed within three words, without punctuation, by grow\*, climb\*, rise/rises/rising/rose/risen or increas\*; or growing, climbing, rising or increasing followed within two words by `memory` or `rss`); a symptom alone (`OOM`, `out of memory`, container killed) does not count |
| Must not be the cause | catalog v2.8.0, auth v3.1.1 | auth v3.2.0 | auth v3.3.0 | checkout v2.7.1 | auth v3.4.0, catalog v2.8.0 |
| Ruled out | an older deployment (v2.8.0 or v3.1.1) | auth v3.2.0 | auth v3.3.0 | checkout v2.7.1 | auth v3.4.0 |
| Counts | per-client ok and failed for the four clients | total failed requests (555) | total failed requests (1961), no client split | total failed requests (156), no client split | total failed requests (102), no client split |

### Checks

| Check | Passes when | Gating |
|---|---|---|
| `report-complete` | every H2 of `template.md` (read at runtime) is present and not empty | yes |
| `root-cause-named` | the Root cause section contains every required term and none of the red herring | yes |
| `red-herring-ruled-out` | the Ruled out section names the red herring | yes |
| `severity` | the first `SEV-n` in the Severity section is the expected level | yes |
| `confidence` | the first word after "Confidence:" (markdown bold and punctuation ignored) is exactly `high`, `medium` or `low`. `medium-high`, `high/medium` or `80%` fail; the rest of the sentence is not read | yes |
| `counts-present` (checkout) | each of the four clients has an ok and a failed number, as a table row, as `client N/M` (failed/total), as an inline list under an explicit `(ok / failed)` or `(INFO / ERROR)` label, as `client N ok / M failed` pairs, or as `client (N ok)` entries followed by a shared `0 failures` | yes |
| `counts-reconciled` | checkout: the per-client (ok, failed) equal the database for one start time (14:00, 14:01, 14:03:00 or the first error); db-pool, cert-expired, api-outage, memory-leak: the true total (555, 1961, 156, 102) appears in the report | checkout yes, the other four **warning** |
| `no-inferred-counts` | no count is presented as `inferred`, `estimated` or `extrapolated` | yes |

Notes:

- `counts-reconciled` is only a warning for `db-pool`: a live run reported 553 failed requests in the summary while the
  15-minute windows add up to 555. The numbers are still printed so the gap is visible.
- `no-inferred-counts` looks at the words next to the keyword (a table cell, or about 40 characters) after removing
  versions and times. "This is inferred from the code comment" and "the trigger is hypothesis" pass; `0 of 856, inferred`
  fails. HTTP status classes (`4xx`, `5xx`) and identifiers of letters followed by digits (`int64`, `sha256`) are
  not counts, so "a 4xx is safer if the currency cannot be inferred" passes; `The 4xx count was inferred (40)` still
  fails. "assumed" is deliberately not a keyword (a default currency is a legitimate assumption).
- Per-client counts are accepted in the formats below. **Every** table row and every `client N/M` occurrence in the whole
  report is searched (the first mention of a client is often prose or an evidence table without counts). Identical
  repeats are fine; if the occurrences give different numbers, `counts-reconciled` fails with a "conflicting" detail.
  Other prose ("failed 275 of 288") is "not present".
  - **Table:** `| client | ok | failed |` (columns found from the header, else the two numbers after the name). The ok
    column header is found by the word `ok` or `success` (a whole word, any case), the failed column by `fail` or
    `error`, so `| client | Failed | OK |` and `| client | Total | OK | Failed |` are read correctly.
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
  - **Prose, errors:** two sentence shapes, as in `web-2.30.4 had 275 errors and 13 ok (95% failed), web-2.31.0 had 0
    errors in 1092`. `client ... N errors and M ok` is read as **failed = N, ok = M** (like `N failed / M ok`).
    `client ... N errors in M` is read as N failed out of M total, so ok = `M - N`; `N` greater than `M` is invalid.
    In `errors in M`, M must end the clause (a `,` `;` `)` `.` or the end of the line), optionally after one of the unit
    words `requests`, `lines` or `log lines`; so `3 errors in 14:00 UTC`, `in 14:00-15:00`, `in 856 minutes` and
    `had 0 errors in 1,948 lines of web` are not counts. `errors in M` is always failed/total (an earlier
    `(ok / failed)` label does not flip it). The word `errors` is read only in these two shapes: `N ok / M errors`,
    `N errors / M ok` and `N failures in M` are not pairs, and `FAILED_WORD` (`fail`, `failed`, `failures`) is unchanged.
  - **Explicit pair label:** `client N ok / M failed` (also `failures`) is always read as **ok = N, failed = M**,
    whatever else is on the line. It also declares that order for every bare `client N / M` that follows it on the
    **same line**, e.g. `web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; web-2.30.4 13 / 275`. Bare pairs before
    it, on other lines, or on a line without any explicit label keep the failed/total reading (an unlabeled
    `web-2.31.0 1092 / 0` stays invalid). The reversed `client N failed / M ok` is read as failed = N, ok = M. A line
    with explicit pairs of both orders (the `(ok / failed)` and `(INFO / ERROR)` labels count as ok/failed) is unreadable: it gives no
    counts and `counts-present` and `counts-reconciled` fail with "ok/failed and failed/ok pairs on one line".
  - **Client (N ok) with a shared zero:** `client (N ok)` entries, joined only by `,`, `and`, `, and` or `&`, directly
    followed by an explicit zero in the same sentence (`have 0 failures`, `had 0 failed`, `have no failures`; also
    `has`, `show(ed)`, `with`, `both`, `all` before it), e.g. `web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 0
    failures.` Every client of the list is read as **ok = N, failed = 0**. The parentheses are required, `ok` and the
    client name are case-sensitive (`(1092 OK)` stays prose). Without the zero statement, with another number
    (`3 failures`, `0.5% failures`), with `0 errors` or `zero failures`, or with the statement in another sentence, the
    entries are not counts. A wrong N is read and then fails `counts-reconciled`; a client that also has an explicit
    pair with another value is "conflicting" as usual.
  - `counts-reconciled` compares the resulting (ok, failed) exactly with the database in both forms.
- The Root cause check accepts `KeyError` or the same idea in words (`no default`, `without a default`), but
  `payment`, `v1.4.2` and `currency` must still appear in that section, and the red herrings must not appear in the
  **cause statement**. The required terms are searched in the whole Root cause section. The cause statement is the
  section without its **confidence statement**: that starts at the first `Confidence` label followed by `high`,
  `medium` or `low` (bold markers and a colon are fine) that begins a line, a paragraph or a sentence, and runs to the
  end of that paragraph, plus directly following paragraphs that start with `Reason`. Text before the label in the same
  paragraph and any later paragraph belong to the cause statement. Without such a label the whole section is checked.
- Known limits:
  - `no-inferred-counts` is lexical: a bare number about a non-count near `inferred` (`status code 404 is inferred`)
    still fires. Only status classes and letters-then-digits names are ignored.
  - `client (N ok)` counts only with the parentheses and the three zero phrases above. The scorer keeps learning one
    wording at a time (#38, #41, #44, #47); a fixed shape in the report template is a possible follow-up, not decided.
  - `db-pool`, `cert-expired`, `api-outage` and `memory-leak` `counts-reconciled` only look for the total (555, 1961, 156, 102) anywhere in the report. It does not check what the
    number is attached to. For `memory-leak` all catalog ERROR lines are 108 (102 plus four kill lines plus two shared
    one-offs), so a report that counts every ERROR line gets the warning.
  - `memory-leak` `root-cause-named` needs leak or growth wording next to `catalog` and `v2.9.0`. A cause statement with
    only the symptom ("catalog v2.9.0 is OOM-killed", "runs out of memory") fails, because the version alone is
    guessable: v2.9.0 is also the latest `catalog` deployment. The scorer cannot check that the agent dated the leak
    (flat memory until 10:20, a slope from then on, 176 minutes from a restart to the next kill), nor that it read
    the memory lines at all. What discriminates is therefore the two red herrings (`auth` v3.4.0 must not be in the
    cause statement and must be ruled out, `catalog` v2.8.0 must not be in the cause statement) and the growth wording.
    Whether the growth wording rests on the memory trend is a human judgement.
  - `memory-leak` data, for reading the counts: 28,977 logs, 107 errors, 4 deployments; memory lines of `catalog` 286,
    `auth` 288 and `payment` 288, of which 34 are high-memory warnings (all `catalog`). `checkout` has no memory
    lines, because every `checkout` log line must end in `client=<version>`. The control group is `auth` and `payment`.
  - A forbidden pattern in the cause statement fails `root-cause-named` even when it is negated ("not caused by
    auth v3.2.0" fails). Put the ruling out in the Ruled out section. The confidence statement is exempt, because it
    may say what could not be excluded ("the checkout v2.7.1 deployment cannot be fully excluded"); what is checked
    there is only the level (`confidence`). A confidence statement that really blames a red herring is not detected,
    and a required cause term found only in the confidence statement still counts.
  - `red-herring-ruled-out` for `checkout-500` needs only one of the two older deployments (v2.8.0 or v3.1.1).
  - A red herring is detected by its version string only. A cause statement that keeps the deployment open without
    the version ("the checkout deployment at 15:55 is a coincidental, unproven alternative") is not detected, while
    the same sentence with `v2.7.1` fails `root-cause-named` (issue #43). The `root-cause` agent and the report
    template now say that the cause statement names the cause only and that an alternative which could not be ruled out
    goes to the confidence reason and to Ruled out. The scorer is unchanged and still cannot see an unversioned hedge,
    so read the cause statements by hand when you measure this.
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
timeout counts as failing every check. The one exception is a run cut by the Claude usage limit (see [Usage
limit](#usage-limit)): it measured nothing, so it is not counted at all.

| Runs | `root-cause-named`, `severity` | every other gating check |
|---|---|---|
| 1 | 1 of 1 | 1 of 1 |
| 3 | 3 of 3 | 3 of 3 (2 of 3 is 67%) |
| 5 | 5 of 5 | 4 of 5 |

Warnings (for example `counts-reconciled` on `db-pool`) are counted and printed separately and never change the verdict.
If the total budget stops the loop, the remaining runs are recorded as skipped and the scenario is labelled `INCOMPLETE`
(not a pass). A scenario with a **contaminated** run (see below) is labelled `CONTAMINATED`, which takes precedence over
every other label. Exit code `0` if every selected scenario passes, `1` otherwise (`FAIL`, `INCOMPLETE`, `CONTAMINATED`,
including a usage-limit stop; there is no separate code); `--dry-run` exits `0`. The summary prints
per-check pass rates and the totals of cost, turns and duration. The data is fixed (seed 42), the model is not, so
a single run is a smoke test, not a verdict on the agent.

| Label | When (first match wins) | Exit code |
|---|---|---|
| `CONTAMINATED` | a run read outside the working copy or an answer-key location (also a run cut by the usage limit) | 1 |
| `INCOMPLETE` | a run hit the usage limit or was not started because of it | 1 |
| `INCOMPLETE` | runs were skipped by the total budget | 1 |
| `PASS` | every run complete and every check meets its rate | 0 |
| `FAIL` | anything else | 1 |

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
  cause on purpose. So for `checkout-500` the answer is in files the agents are meant to read; the other four
  scenarios have no code in the copy, and the fix-proposer has to report that it did not find it (all 9 reports of
  `db-pool`, `cert-expired` and `api-outage` in the 2026-10-18 baseline say so, and so do 2 of 2 reports of `memory-leak`
  in its first live calibration of 2026-10-19: no catalog source is in the repository).
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
`num_turns` are in `claude-result.json`). This is a cost measurement, not a pass rate. It was taken **before the
isolation** and is kept as history; the numbers after the isolation are in [Measured baseline](#measured-baseline).

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

Measured on 2026-10-18 with `--all --runs 3` (3 runs per scenario, 12 in total), every run in the isolated working copy
(see [Isolation and threat model](#isolation-and-threat-model)) and with the neutral worked example in the skill (#40), on one
machine. Code state: `main` at commit `0d817a8` (the merge of #40; the calibration ran on the branch of #40 with
identical content), which is unreleased after v1.2.2 and will be released as v1.3.0. The folder `20261018T120326Z` of the
same day was cut short by a usage limit (429) and is not used.

Method: the live verdicts are the ones `run-eval` printed (`summary.txt` of each folder). The saved reports were then
**rescored** with the scorer of `main` after #44 (it includes #39 and #44), against the ground truth of each run's own
database, and the verdict was computed with `runplan.verdict`, the code that `run-eval` uses. Cost, turns and duration
come from `record.json` and `claude-result.json`.

| Scenario | Live verdict | Rescored verdict | Cost per run | Total cost | Turns | Duration per run | Total |
|---|---|---|---|---|---|---|---|
| `cert-expired` | PASS | PASS | $0.48, $0.41, $0.41 | $1.30 | 2, 2, 2 | 120 s, 120 s, 123 s | 362 s |
| `checkout-500` | FAIL | PASS | $0.38, $0.42, $0.38 | $1.18 | 2, 3, 3 | 115 s, 118 s, 116 s | 349 s |
| `db-pool` | PASS | PASS | $0.40, $0.37, $0.37 | $1.14 | 2, 2, 2 | 122 s, 107 s, 116 s | 345 s |
| `api-outage` | FAIL | FAIL | $0.39, $0.36, $0.45 | $1.20 | 9, 2, 2 | 118 s, 113 s, 130 s | 361 s |
| all 12 runs | | | $0.36 to $0.48 (mean $0.40) | $4.82 | 33 | 107 to 130 s (mean 118 s) | 1,416 s (23.6 min) |

The totals are sums of the unrounded durations and can differ by 1 s from the sum of the rounded values shown (for
example `cert-expired`: 119.5 s + 119.5 s + 123.0 s = 362 s, shown as 120 s + 120 s + 123 s).

Passed runs per check (3 runs each; only `checkout-500` has per-client counts, and `counts-reconciled` is only a
warning for the other three). Where the rescore differs from the live run, both are shown:

| Check | `cert-expired` | `checkout-500` | `db-pool` | `api-outage` |
|---|---|---|---|---|
| `report-complete` | 3/3 | 3/3 | 3/3 | 3/3 |
| `root-cause-named` (100% required) | 3/3 | 3/3 | 3/3 | **2/3** live and rescored |
| `red-herring-ruled-out` | 3/3 | 3/3 | 3/3 | 3/3 |
| `severity` (100% required) | 3/3 | 3/3 | 3/3 | 3/3 |
| `confidence` | 3/3 | 3/3 | 3/3 | 3/3 |
| `counts-present` | n/a | live 2/3, rescored 3/3 | n/a | n/a |
| `counts-reconciled` | 3/3 (warning) | live 2/3, rescored 3/3 | 3/3 (warning) | 3/3 (warning) |
| `no-inferred-counts` | 3/3 | 3/3 | 3/3 | 3/3 |

What the numbers show:

- `cert-expired` and `db-pool` pass every check in 3 of 3 runs.
- The severity was the expected one in 12 of 12 runs, so the neutral worked example did not move any severity.
- **`checkout-500` printed FAIL live and passes rescored.** In run 3 the per-client counts were prose
  (`web-2.30.4 had 275 errors and 13 ok (95% failed)`, `web-2.31.0 had 0 errors in 1092`), a format the scorer did not
  read (issue #41, fixed in #44). The report was correct, and all four pairs match the database. This report is the
  `prose-errors-and-ok` fixture below.
- **`api-outage` fails `root-cause-named` in 1 of 3 runs** (issue #43). Run 3 writes in the cause statement that the
  checkout v2.7.1 deployment "could not be fully excluded", which names the red herring. The agents keep that deployment
  open as a candidate because the checkout code is not in the working copy. Run 1 hedges in the same way ("a
  coincidental, unproven alternative") but passes, because the check looks for the version string only; run 2 does not
  mention it in the cause. This was decided in #43: the agents changed and the scorer did not. The measurement after the
  change is in [Check after the root-cause change](#check-after-the-root-cause-change-43-2026-10-18).
- **Isolation held:** `contamination` is empty and `permission_denials` and `blocked_attempts` are 0 in all 12 runs.
- Where the code is not in the copy, the fix-proposer says so: all 9 reports of `cert-expired`, `db-pool` and
  `api-outage` say "No patch" because the failing code is not in the repository. A rollback is proposed in 2 of the 9,
  as a hedge (`db-pool` run 1: catalog v2.8.0 as a test of the hypothesis; `api-outage` run 1: checkout v2.7.1,
  "low-confidence"); the other 7 advise against it or call it not useful.
- Three runs per scenario is a small sample. The numbers show how the agents behave with the current prompts
  (`cert-expired` and `db-pool` pass, `checkout-500` passes after the scorer fix, `api-outage` fails in 1 of 3 runs),
  not a precise pass rate. Turns (2 to 9) vary between runs and carry no meaning.

Six real reports from live runs are regression fixtures in `tests/fixtures/eval/`, each exposing a format or a case the
scorer had to learn:

- `real-checkout-500-failed-total.md`: `client N/M (pct)` pairs, read as failed/total.
- `real-checkout-500-ok-failed-inline.md`: ok/failed counts in an inline list under an explicit `(ok / failed)` label.
- `real-checkout-500-mixed-labels.md`: explicit `N ok / M failed` pairs mixed with bare `N / M` pairs on one line.
- `real-checkout-500-info-error-labels.md`: counts in an inline list under an `(INFO / ERROR)` label (from the first
  isolated live run).
- `real-checkout-500-prose-errors-and-ok.md`: counts written as prose, `had 275 errors and 13 ok` and
  `had 0 errors in 1092` (from the calibration above).
- `real-api-outage-red-herring-in-confidence.md`: the red herring (`v2.7.1`) named only in the confidence statement
  of the Root cause section; it must pass.

#### History: calibration of 2026-10-17 (not comparable)

The first calibration (agents v1.2.1, `--all --runs 3`, two scenarios, 6 runs) was taken **before the isolation**. The
agents worked in the repository root and could read the generator and the docs (issue #35), and the skill's worked
example named `checkout-500` (issue #37), so the answer key was reachable. Its numbers are kept for the record and are
**not comparable** with the baseline above (treat them as an upper bound):

| Scenario | Live verdict | Rescored verdict | Cost per run | Duration per run | Total | Turns |
|---|---|---|---|---|---|---|
| `checkout-500` | FAIL | PASS (all checks 3/3) | $0.50, $0.46, $0.46 | 133 s, 141 s, 143 s | $1.42, 417 s | 9, 6, 4 |
| `db-pool` | PASS | PASS (`counts-reconciled` warning 2/3) | $0.40, $0.42, $0.36 | 129 s, 130 s, 129 s | $1.19, 389 s | 2, 2, 4 |
| both | | | | | $2.61, 806 s | |

The live FAIL of `checkout-500` was also a scorer format miss: run 3 wrote
`web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; ...` (fixed in #30, the `mixed-labels` fixture).

To reproduce (about $4.82 and 24 minutes for 12 runs; results go to `evals/results/<UTC timestamp>/`):

```bash
uv run --no-sync run-eval --all --runs 3 --total-budget-usd 12.00
```

`12.00` is 12 runs times the default per-run cap of $1.00, which is always enough (see the budget rule below). `6.00`
is enough only while the average cost stays below about $0.45 per run; with the default total cap of $3.00 the
plan stops long before the 12th run.

Run it before a release and after changing `.claude/agents/` or the skill. Code: `runplan.py` (pure: command, results,
verdict), `runner.py` (the subprocess seam, folders, report pickup), `run_cli.py`. Tests: `tests/test_eval_runner.py`
with a fake `claude` script (`tests/fake_claude.py`), no network and no model.

### Check after the root-cause change (#43, 2026-10-18)

Measured on 2026-10-18 after the change of #43 (the `root-cause` agent and the report template keep an alternative that
could not be ruled out out of the cause statement). Same method as the baseline: isolated working copy, live verdicts
from `summary.txt`, cost, turns and duration from `record.json`. Code state: `main` after the scorer fix for
#47 plus the wording change of #43, which was not committed yet when it was measured and is the change this entry
describes; unreleased after v1.2.2.
`api-outage` ran 5 times because it is the scenario that showed the problem; the other three ran 3 times each, to see
whether the change moved them. The folder `20261018T155048Z` (`cert-expired`) was cut by a usage limit in run 3 (the
first live use of the `INCOMPLETE` handling of #42; 2 runs scored PASS, $0.83 in total including $0.03 for the cut run)
and is not used.

| Scenario | Runs | Verdict | Cost per run | Total cost | Turns | Duration per run | Total |
|---|---|---|---|---|---|---|---|
| `api-outage` | 5 | PASS | $0.60, $0.48, $0.45, $0.45, $0.43 | $2.41 | 2, 2, 2, 2, 2 | 147 s, 125 s, 142 s, 129 s, 116 s | 660 s |
| `cert-expired` | 3 | PASS | $0.41, $0.39, $0.39 | $1.19 | 2, 2, 2 | 127 s, 122 s, 126 s | 375 s |
| `db-pool` | 3 | PASS | $0.45, $0.40, $0.36 | $1.21 | 2, 2, 4 | 121 s, 124 s, 114 s | 359 s |
| `checkout-500` | 3 | live FAIL, rescored PASS | $0.37, $0.39, $0.39 | $1.15 | 2, 3, 2 | 116 s, 125 s, 114 s | 355 s |
| all 14 runs | | | $0.36 to $0.60 (mean $0.43) | $5.96 | | | about 29 minutes |

Totals are sums of the unrounded durations and can differ by 1 s from the sum of the rounded values shown.

- **Gating checks:** every gating check passes in every run of `api-outage` (5/5), `cert-expired` (3/3) and `db-pool`
  (3/3). The `counts-reconciled` warning is 5/5 for `api-outage`, 3/3 for `cert-expired` and 2/3 for `db-pool` (run 2
  does not contain the total of 555; baseline 3/3, and the pre-isolation calibration also had 2/3).
- **`checkout-500` printed FAIL live and passes rescored.** In run 3 `counts-present`, `counts-reconciled` and
  `no-inferred-counts` were scorer false results on a correct report (issue #47, fixed). Rescored with the fixed
  scorer, every check is 3/3.
- Severity was the expected one in 14 of 14 runs, and no run was labelled `CONTAMINATED`.

**Read by hand** (the scorer cannot see an unversioned hedge): in 0 of 5 `api-outage` reports does the cause statement
name or hedge the checkout deployment (baseline: 2 of 3). The deployment stays in Ruled out in 5 of 5 reports ("not
excluded", with evidence for and against), in the confidence reason in 2 of 5 (runs 3 and 4), and run 5 says generally
that a cause on our side is not ruled out. Run 3 also says in its Summary that the deployment is not excluded, so the
uncertainty is kept, only moved out of the cause statement.

**Confidence:** run 1 reports confidence high while saying the deployment is not excluded by direct evidence. The scorer
does not check whether a confidence level is justified.

**Side effect (recorded, not tuned):** the fix-proposer lists a rollback of the checkout deployment in 5 of 5
`api-outage` reports (baseline: 1 of 3), always with the caveat that it would not fix a provider outage. In 2 of 5 (runs
3 and 4) it is worded as an instruction.

Five runs is a small sample. It supports the change, but it is not a pass rate.

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
| 12 (`--all --runs 3` with four scenarios) | $5.40 at $0.40 per run, $5.95 at $0.45, $6.50 at $0.50 (`6.00` is enough only below about $0.45) | $12.00 |
| 15 (`--all --runs 3` with five scenarios) | $6.60 at $0.40 per run, $7.30 at $0.45, $8.00 at $0.50 (`8.00` is enough up to about $0.50) | $15.00 |

Since `memory-leak` exists, `--all` plans five scenarios (`checkout-500`, `db-pool`, `cert-expired`, `api-outage`,
`memory-leak`). The measured baseline above covers the first four; `memory-leak` was measured separately, see below.

### First live calibration of `memory-leak` (2026-10-19)

Measured on 2026-10-19 with `--scenario memory-leak --runs 2`, in the isolated working copy, same method as the baseline
(live verdicts from `summary.txt`, cost, turns and duration from `record.json`). It is **2 runs, not 3** like the
four-scenario baseline, so it is a first look and not a pass rate. The results folder `evals/results/20261019T150230Z` is
gitignored.

| Runs | Verdict | Cost per run | Total cost | Turns | Duration per run |
|---|---|---|---|---|---|
| 2 | PASS | $0.49, $0.41 | $0.91 | 2, 2 | 119 s, 144 s |

- **Checks:** every check passes in both runs, including `root-cause-named`. Severity is `SEV-3` and confidence is
  `medium` in both. `counts-reconciled` is ok in both (102 found, no warning). No run was `CONTAMINATED`.
- **Reports, read by hand:** both name a memory leak in `catalog` v2.9.0, rule out `auth` v3.4.0 and `catalog` v2.8.0,
  and report 102 failed requests. Both `fix-proposer` runs say that no catalog source is in the repository.
- **How the agents worked:** both filtered the memory search by service and time range, so the 200-row cap did not mislead
  them, and one run reported its truncation. Both dated the knee at 10:20 from the `catalog` memory lines. Neither used
  the `auth` and `payment` memory lines (the control group) or the 176-minute restart cadence: the cadence and the
  control group went unused, and both reports gave confidence `medium`.
- **Cost:** $0.41 to $0.49 per run, inside the $0.40 to $0.50 range of the budget table. The 15-run row (`--all --runs 3`,
  five scenarios) therefore holds: about $7.30 at $0.45 per run, `8.00` is enough up to about $0.50.
- **Limits of the sample:** two runs say nothing about how often the scenario passes. Two limits of the scorer showed up in
  these reports, listed in [scenarios.md](scenarios.md#known-limits-of-the-scorer).

### Usage limit

A Claude subscription has a session limit. When it is hit, `claude -p` exits with code 1 and prints a normal JSON result
with `is_error: true`, `api_error_status: 429`, `api_error: "usage_limit_reached"` and a result text such as
`You've hit your session limit · resets 3:30pm (Europe/Berlin)` (the `subtype` still says `success`, so it is useless).

- **Detection:** `is_error` is true **and** `api_error` equals `usage_limit_reached`, read from the parsed result before the
  exit code is looked at. Text is never used: a report or answer that mentions a usage limit is not detected, and a
  different error (another status, `rate_limit_error`, a crash, a budget stop) stays an ordinary failed run.
  `api_error` and `api_error_status` are written to `record.json` for every run.
- **Effect:** the run gets status `usage_limit`: it is never scored (even if it left a partial report, which is kept in the
  run folder as evidence), not counted in the pass rates and not a failure. Its working copy is still removed and its audit
  log still scanned, so contamination still wins. The plan stops: every later run is recorded as `not run (usage limit)` in
  `summary.txt` and `summary.json` (no run folder, no `record.json`), the affected scenarios are `INCOMPLETE`, the summary
  prints `USAGE LIMIT ... resets <time>` and the last line is `RESULT: INCOMPLETE (usage limit)`. If the text has no
  `resets ...` part the hint is empty and the verdict is the same. Scenarios that finished before the cut keep their verdict.
- **Cost:** a cut run counts with the cost claude reported (zero for an instant cut, partial for a cut mid-run) in the totals
  and in the spent amount; unstarted runs cost and reserve nothing. The [budget rule](#budget-rule) is unchanged.
- **Advice:** run per scenario (`--scenario X`) instead of `--all`, so a cut loses less, and do not use Claude Code
  interactively while a run is live: the session limit is shared with your subscription. After the reset, rerun the
  affected scenarios.
- **Not done:** no automatic sleep until the reset, no retry and no resume of the unfinished runs (a possible later `--resume`
  idea), and no early stop after several instant zero-cost failures.
