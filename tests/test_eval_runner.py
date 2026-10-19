"""The live runner, tested with a fake `claude` script: no network, no model, no API key."""

import ast
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from incident_agent.evaluation import runplan, workcopy
from incident_agent.evaluation.checks import CheckResult
from incident_agent.evaluation.expected import EXPECTED
from incident_agent.evaluation.run_cli import main as run_main
from incident_agent.evaluation.runner import (
    ClaudeNotFound,
    ExecResult,
    RunConfig,
    find_claude,
    pick_up_reports,
    run_eval,
    server_command,
    subprocess_exec,
)
from incident_agent.evaluation.runplan import RunRecord

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_claude.py"
FIXTURES = ROOT / "tests" / "fixtures" / "eval"
USER_REPORT = "2026-03-12-user-report.md"
# Files that exist in a real checkout and must never reach a run
DECOYS = (
    "docs/scenarios.md",
    "src/incident_agent/data/scenarios.py",
    "tests/fixtures/eval/good-db-pool.md",
    "evals/results/old/audit.jsonl",
    "README.md",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root with everything a run needs, decoys for what it must not get and a report of
    the user's own in reports/."""
    root = tmp_path / "project"
    for name, source in workcopy.plan_files(ROOT).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, root / name)
    shutil.copyfile(ROOT / "CLAUDE.md", root / "CLAUDE.md")
    for name in DECOYS:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("answer key", encoding="utf-8")
    (root / "reports").mkdir()
    (root / "reports" / USER_REPORT).write_bytes(b"# my own report\r\nkeep these exact bytes\n")
    return root


@pytest.fixture
def config(project: Path, tmp_path: Path) -> RunConfig:
    return RunConfig(
        project_root=project,
        claude=[sys.executable, str(FAKE)],
        max_budget_usd=2.0,
        timeout_s=60,
        server=["fake-server", "--flag"],
        environ={"PATH": os.environ.get("PATH", ""), "SLACK_WEBHOOK_URL": "https://secret.invalid"},
        workdir_root=tmp_path / "workroot",
    )


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "good")
    monkeypatch.setenv("FAKE_CLAUDE_CALLS_FILE", str(tmp_path / "fake-calls.jsonl"))
    monkeypatch.delenv("FAKE_CLAUDE_COST", raising=False)


def calls() -> list[dict]:
    path = Path(os.environ["FAKE_CLAUDE_CALLS_FILE"])
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def reports_state(project: Path) -> dict[str, tuple[bytes, int]]:
    reports = project / "reports"
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in reports.iterdir()}


def evaluate(project, config, tmp_path, scenarios=("checkout-500",), runs=1, total=100.0):
    # the fake claude only sees what the runner passes on: hand it the FAKE_* switches
    config.environ = {
        **config.environ,
        **{k: v for k, v in os.environ.items() if k.startswith("FAKE_")},
    }
    return run_eval(
        list(scenarios), runs, config, tmp_path / "results", total, subprocess_exec,
        stamp="T1", out=lambda _: None,
    )  # fmt: skip


# --- prompts ---------------------------------------------------------------------------------


def test_one_prompt_per_scenario_and_it_matches_the_docs():
    assert set(runplan.PROMPTS) == set(EXPECTED)
    docs = (ROOT / "docs" / "scenarios.md").read_text(encoding="utf-8")
    for scenario, prompt in runplan.PROMPTS.items():
        assert re.search(rf"\| `{scenario}` \| {re.escape(prompt)} \|", docs), scenario


def test_pure_module_has_no_process_imports():
    tree = ast.parse((ROOT / "src/incident_agent/evaluation/runplan.py").read_text("utf-8"))
    imported = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not imported & {"subprocess", "shutil", "os", "sqlite3"}


# --- command construction --------------------------------------------------------------------


def test_command_flags(tmp_path: Path):
    mcp = tmp_path / "mcp.json"
    argv = runplan.build_command(["claude"], mcp, 2.0, "sonnet")
    assert argv[:2] == ["claude", "-p"]
    assert "--max-turns" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--max-budget-usd") + 1] == "2.00"
    assert argv[argv.index("--mcp-config") + 1] == str(mcp)
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--model") + 1] == "sonnet"
    allowed = argv[argv.index("--allowedTools") + 1]
    entries = allowed.split(",")
    assert "mcp__incident-logs__*" in entries and "Edit(reports/**)" in entries
    for tool in ("Write", "Glob", "NotebookEdit", "MultiEdit"):
        assert not any(e.startswith(f"{tool}(") for e in entries)
    # reads are scoped to the working copy (the cwd); no bare Read, Grep or Glob in the allow list
    assert "Read(./**)" in entries
    assert not {"Read", "Grep", "Glob"} & set(entries)
    assert {"Read", "Grep", "Glob", "Write"} <= set(argv[argv.index("--tools") + 1].split(","))
    assert "Write" in argv[argv.index("--tools") + 1].split(",")
    assert "Bash" not in argv[argv.index("--tools") + 1]
    assert runplan.PROMPTS["db-pool"] not in argv  # the prompt goes to stdin
    assert "--model" not in runplan.build_command(["claude"], mcp, 2.0)


def test_env_has_notifications_off_and_no_webhook(tmp_path: Path):
    env = runplan.run_env(
        {"PATH": "p", "SLACK_WEBHOOK_URL": "x", "INCIDENT_NOTIFY_DESKTOP": "1"},
        tmp_path / "a.db",
        tmp_path / "audit.jsonl",
    )
    assert "SLACK_WEBHOOK_URL" not in env and env["PATH"] == "p"
    assert env["INCIDENT_NOTIFY_DESKTOP"] == "0"
    assert env["INCIDENT_DB_PATH"] == str(tmp_path / "a.db")
    assert env["INCIDENT_AUDIT_LOG"] == str(tmp_path / "audit.jsonl")


def test_mcp_config_keeps_server_name_and_db(tmp_path: Path):
    cfg = runplan.build_mcp_config(["py", "-m", "incident_agent.mcp_server"], tmp_path / "x.db")
    server = cfg["mcpServers"]["incident-logs"]
    assert server["command"] == "py" and server["args"] == ["-m", "incident_agent.mcp_server"]
    assert server["env"] == {"INCIDENT_DB_PATH": str(tmp_path / "x.db")}


def test_server_command_prefers_venv_script_and_never_uses_uv(tmp_path: Path):
    suffix = ".exe" if sys.platform == "win32" else ""
    python = tmp_path / ("python" + suffix)
    assert server_command(str(python)) == [str(python), "-m", "incident_agent.mcp_server"]
    script = tmp_path / ("incident-logs-server" + suffix)
    script.write_text("")
    assert server_command(str(python)) == [str(script)]


def test_find_claude(tmp_path: Path):
    exe = tmp_path / "myclaude.exe"
    exe.write_text("")
    assert find_claude(str(exe), {}) == [str(exe)]
    assert find_claude(None, {"CLAUDE_BIN": str(exe)}) == [str(exe)]
    with pytest.raises(ClaudeNotFound):
        find_claude(str(tmp_path / "missing"), {})


# --- one run through the real subprocess seam ------------------------------------------------


def test_good_run_layout_and_isolation(project, config, tmp_path):
    before = reports_state(project)
    verdicts, records, session = evaluate(project, config, tmp_path)
    run_dir = session / "checkout-500" / "run-1"
    for name in ("incident.db", "mcp.json", "command.json", "audit.jsonl", "claude-result.json"):
        if name != "audit.jsonl":  # the fake does not write an audit log
            assert (run_dir / name).exists(), name
    assert (run_dir / "reports" / "2026-03-12-checkout-500-fake.md").exists()
    assert (run_dir / "score.txt").exists() and (run_dir / "record.json").exists()
    assert (session / "summary.txt").exists() and (session / "summary.json").exists()
    assert (run_dir / "workcopy-manifest.json").exists()
    assert reports_state(project) == before  # the real reports/ was never touched
    assert not any((tmp_path / "workroot").iterdir())  # the working copy is gone
    (call,) = calls()
    # claude ran in the working copy, not in the project, and the copy held no answer key
    work = Path(call["cwd"])
    assert work != project and work.name == "work" and work.parent.name.startswith("incident-eval-")
    assert project not in work.parents and ROOT not in work.parents
    assert "CLAUDE.md" in call["files"] and ".claude/settings.json" in call["files"]
    assert not [
        f for f in call["files"] if f.split("/")[0] in {"docs", "src", "evals", "README.md"}
    ]
    assert json.loads((run_dir / "command.json").read_text("utf-8"))["cwd"] == call["cwd"]
    assert call["stdin"] == runplan.PROMPTS["checkout-500"]
    assert call["env"]["INCIDENT_NOTIFY_DESKTOP"] == "0"
    assert call["env"]["SLACK_WEBHOOK_URL"] is None
    assert call["env"]["INCIDENT_DB_PATH"] == str(run_dir / "incident.db") and call["db_exists"]
    assert call["env"]["INCIDENT_AUDIT_LOG"] == str(run_dir / "audit.jsonl")
    mcp = json.loads((run_dir / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["incident-logs"]["env"]["INCIDENT_DB_PATH"] == str(
        run_dir / "incident.db"
    )
    (record,) = records
    assert record.status == "scored" and record.cost_usd == 0.5 and record.turns == 7
    assert verdicts[0].passed and verdicts[0].label == "PASS"


def test_both_scenarios_good(project, config, tmp_path):
    verdicts, _, _ = evaluate(project, config, tmp_path, scenarios=("checkout-500", "db-pool"))
    assert [v.passed for v in verdicts] == [True, True]


def test_bad_report_fails_severity(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "bad")
    (verdict,), _, _ = evaluate(project, config, tmp_path)
    assert not verdict.passed
    assert {s.name for s in verdict.stats if not s.ok} == {"severity"}


@pytest.mark.parametrize("mode", ["none", "exit1", "error"])
def test_failed_runs_count_as_failures(project, config, tmp_path, monkeypatch, mode):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    before = reports_state(project)
    (verdict,), (record,), _ = evaluate(project, config, tmp_path)
    assert record.status == "failed" and not verdict.passed and verdict.failed_runs
    assert reports_state(project) == before


def test_missing_report_reason(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "none")
    _, (record,), _ = evaluate(project, config, tmp_path)
    assert record.reason.startswith("no-report")


def test_timeout_is_a_failed_run(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    config.timeout_s = 1.5
    _, (record,), _ = evaluate(project, config, tmp_path)
    assert record.status == "failed" and "timeout" in record.reason


# --- the real reports/ is never touched; reports are picked up from the working copy ----------


def test_existing_reports_survive_even_when_the_run_writes_the_same_name(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "overwrite")
    mine = project / "reports" / "2026-03-12-fake-incident.md"  # the name the fake agent writes
    mine.write_bytes(b"# my own report" + bytes([13, 10]) + b"keep these exact bytes" + bytes([10]))
    os.utime(mine, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    before = reports_state(project)

    (verdict,), _, session = evaluate(project, config, tmp_path)

    assert reports_state(project) == before
    saved = session / "checkout-500" / "run-1" / "reports" / "2026-03-12-fake-incident.md"
    assert saved.read_text(encoding="utf-8").startswith("# ")
    assert b"my own report" not in saved.read_bytes()
    assert verdict.passed  # the run's report was scored, not the user's


def test_pick_up_reports_copies_newest_first(tmp_path: Path):
    reports = tmp_path / "work" / "reports"
    reports.mkdir(parents=True)
    old, new = reports / "old.md", reports / "new.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    os.utime(old, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    copies = pick_up_reports(reports, tmp_path / "dest")
    assert [p.name for p in copies] == ["new.md", "old.md"]
    assert (tmp_path / "dest" / "new.md").read_text(encoding="utf-8") == "new"
    assert pick_up_reports(tmp_path / "missing", tmp_path / "dest2") == []


# --- contamination --------------------------------------------------------------------------


def test_contaminated_run_is_never_a_pass(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "leak")
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.status == "scored"  # a good report...
    assert record.contamination and record.contamination[0].startswith("Read file_path=src/")
    assert not verdict.passed and verdict.label == "CONTAMINATED"  # ...and still not a pass
    assert verdict.contaminated_runs == (record,)
    summary = (session / "summary.txt").read_text(encoding="utf-8")
    assert "CONTAMINATED: Read file_path=src/" in summary and "RESULT: FAIL" in summary
    saved = json.loads((session / "checkout-500" / "run-1" / "record.json").read_text("utf-8"))
    assert saved["contaminated"] is True and saved["contamination"] == list(record.contamination)
    overview = json.loads((session / "summary.json").read_text(encoding="utf-8"))
    assert overview["verdicts"] == {"checkout-500": "CONTAMINATED"}


def test_contamination_beats_incomplete_and_counts_as_failing_every_check():
    leaked = RunRecord("s", 1, "scored", checks=checks(root_cause_named=True, severity=True))
    leaked = RunRecord(**{**leaked.__dict__, "contamination": ("Read x (outside)",)})
    skipped = RunRecord("s", 2, "skipped", "skipped (total budget)")
    v = runplan.verdict("s", [leaked, skipped])
    assert v.label == "CONTAMINATED" and v.incomplete and not v.passed
    assert {s.name: s.passes for s in v.stats} == {"root-cause-named": 0, "severity": 0}


def test_denied_attempts_are_recorded_but_do_not_contaminate(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "denied")
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.blocked == ("Read /elsewhere/answer.md",) and not record.contamination
    assert verdict.passed and verdict.label == "PASS"
    summary = (session / "summary.txt").read_text(encoding="utf-8")
    assert "denied by the permission rules" in summary and "/elsewhere/answer.md" in summary
    saved = json.loads((session / "checkout-500" / "run-1" / "record.json").read_text("utf-8"))
    assert saved["blocked_attempts"] == ["Read /elsewhere/answer.md"]


def good_exec(leak: bool):
    """An exec seam that behaves like a good agent run in the working copy it is given."""

    def fake_exec(argv, stdin, env, cwd, timeout):
        (cwd / "reports").mkdir(exist_ok=True)
        text = (FIXTURES / "good-db-pool.md").read_text(encoding="utf-8")
        (cwd / "reports" / "2026-03-12-db-pool-fake.md").write_text(text, encoding="utf-8")
        if leak:
            line = {"tool_name": "Grep", "input": {"pattern": "x", "path": str(ROOT / "src")}}
            with open(env["INCIDENT_AUDIT_LOG"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + chr(10))
        return ExecResult(0, json.dumps({"total_cost_usd": 0.1, "num_turns": 2}), "")

    return fake_exec


@pytest.mark.parametrize(("leak", "code"), [(False, 0), (True, 1)])
def test_exit_code_follows_contamination(tmp_path, capsys, leak, code):
    args = ["--scenario", "db-pool", "--yes", "--claude-path", sys.executable]
    args += ["--results-dir", str(tmp_path)]
    assert run_main(args, exec_fn=good_exec(leak)) == code
    out = capsys.readouterr().out
    assert ("VERDICT db-pool: CONTAMINATED" in out) is leak
    assert ("RESULT: FAIL" in out) is leak


def test_rmtree_failure_is_a_warning_and_keeps_the_verdict(project, config, tmp_path, monkeypatch):
    def locked(*_args, **_kwargs):
        raise PermissionError("locked by the MCP server")

    monkeypatch.setattr(workcopy.shutil, "rmtree", locked)
    fake = {k: v for k, v in os.environ.items() if k.startswith("FAKE_")}
    config.environ = {**config.environ, **fake}
    messages: list[str] = []
    (verdict,), (record,), session = run_eval(
        ["checkout-500"], 1, config, tmp_path / "results", 100.0, subprocess_exec,
        stamp="T1", out=messages.append,
    )  # fmt: skip
    assert verdict.passed and record.status == "scored"
    assert any(m.startswith("warning: could not remove the working copy") for m in messages)
    saved = json.loads((session / "checkout-500" / "run-1" / "record.json").read_text("utf-8"))
    assert saved["workcopy_removed"] is False


# --- aggregation and verdict -----------------------------------------------------------------


def checks(**results: bool) -> tuple[CheckResult, ...]:
    return tuple(CheckResult(name.replace("_", "-"), ok) for name, ok in results.items())


def record(n: int, status="scored", **results: bool) -> RunRecord:
    return RunRecord("s", n, status, checks=checks(**results) if status == "scored" else ())


def good_run(n: int, **override: bool) -> RunRecord:
    base = {"root_cause_named": True, "severity": True, "confidence": True}
    return record(n, **{**base, **override})


def test_verdict_n1_requires_every_check():
    assert runplan.verdict("s", [good_run(1)]).passed
    assert not runplan.verdict("s", [good_run(1, confidence=False)]).passed


def test_verdict_n3_soft_checks_need_all_runs():
    two_of_three = [good_run(1), good_run(2), good_run(3, confidence=False)]
    assert not runplan.verdict("s", two_of_three).passed  # 67% < 80%


def test_verdict_n5_allows_one_soft_miss_but_no_strict_miss():
    soft = [good_run(i) for i in range(1, 5)] + [good_run(5, confidence=False)]
    assert runplan.verdict("s", soft).passed  # 4/5
    strict = [good_run(i) for i in range(1, 5)] + [good_run(5, severity=False)]
    assert not runplan.verdict("s", strict).passed
    two_soft = [good_run(i) for i in range(1, 4)] + [good_run(i, confidence=False) for i in (4, 5)]
    assert not runplan.verdict("s", two_soft).passed  # 3/5


def test_failed_run_counts_against_every_check():
    runs = [good_run(i) for i in range(1, 5)] + [RunRecord("s", 5, "failed", "no-report")]
    v = runplan.verdict("s", runs)
    assert not v.passed  # root-cause-named is 4/5
    assert {s.name: (s.passes, s.total) for s in v.stats}["root-cause-named"] == (4, 5)


def test_warnings_are_reported_separately_and_do_not_change_the_verdict():
    warn = CheckResult("counts-reconciled", False, gating=False)
    run = RunRecord("s", 1, "scored", checks=(*checks(root_cause_named=True, severity=True), warn))
    v = runplan.verdict("s", [run])
    assert v.passed
    text = runplan.format_summary([v], [run])
    assert "Warnings (do not affect the verdict)" in text and "counts-reconciled" in text
    assert "RESULT: PASS" in text


def test_all_runs_failed_is_a_failure():
    assert not runplan.verdict("s", [RunRecord("s", 1, "failed", "x")]).passed


def test_totals_and_summary():
    runs = [
        RunRecord(
            "s", 1, "scored", cost_usd=0.5, turns=7, duration_s=10, checks=checks(severity=True)
        ),
        RunRecord("s", 2, "failed", "timeout", cost_usd=1.0, turns=3, duration_s=5),
    ]
    t = runplan.totals(runs)
    assert (t.cost_usd, t.turns, t.duration_s, t.runs) == (1.5, 10, 15, 2)
    assert "cost $1.50" in runplan.format_summary([runplan.verdict("s", runs)], runs)


# --- budget stop -----------------------------------------------------------------------------


def test_total_budget_skips_remaining_runs(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_COST", "1.5")
    (verdict,), records, session = evaluate(project, config, tmp_path, runs=5, total=5.0)
    assert [r.status for r in records] == ["scored", "scored", "scored", "skipped", "skipped"]
    assert len(calls()) == 3  # spent 4.5 + a cap of 2.0 would exceed 5.0: stop
    assert verdict.incomplete and not verdict.passed and verdict.label == "INCOMPLETE"
    assert "stopped early" in (session / "summary.txt").read_text(encoding="utf-8")


def test_planned_maximum_is_limited_by_the_total_budget():
    assert runplan.planned_max_cost(5, 2, 2.0, 6.0) == 6.0
    assert runplan.planned_max_cost(1, 1, 2.0, 6.0) == 2.0


# --- CLI -------------------------------------------------------------------------------------


def boom(*_args, **_kwargs):
    raise AssertionError("claude must not be called")


def test_dry_run_calls_nothing_and_creates_nothing(project, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = tmp_path / "results"
    temp = Path(tempfile.gettempdir())
    leftovers = set(temp.glob("incident-eval-*"))
    code = run_main(
        ["--all", "--runs", "2", "--dry-run", "--claude-path", sys.executable,
         "--results-dir", str(results)],
        exec_fn=boom,
        ask=boom,
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and not results.exists()
    assert set(temp.glob("incident-eval-*")) == leftovers  # no working copy was created
    # the working copy is the cwd, and the read rule is scoped to it
    assert "cwd:      " in out and "incident-eval-<random>" in out and "sanitized copy" in out
    assert "Read(./**)" in out and "Working copy (from the project root" in out
    assert "Planned: 2 run(s) x 5 scenario(s)" in out and "maximum $3.00" in out
    assert "--strict-mcp-config" in out and "INCIDENT_NOTIFY_DESKTOP=0" in out
    assert "SLACK_WEBHOOK_URL removed" in out and "https://" not in out
    assert out.count("[checkout-500] run") == 2 and "--max-turns" not in out


def test_confirmation_is_required(tmp_path, capsys):
    args = [
        "--scenario",
        "db-pool",
        "--claude-path",
        sys.executable,
        "--results-dir",
        str(tmp_path),
    ]
    assert run_main(args, exec_fn=boom, interactive=False) == 1
    assert run_main(args, exec_fn=boom, ask=lambda _: "n", interactive=True) == 1
    out = capsys.readouterr().out
    assert "Planned: 1 run(s) x 1 scenario(s)" in out and "maximum $1.00" in out
    assert "Aborted" in out


def test_cli_end_to_end_with_fake_exec(tmp_path, capsys):
    def fake_exec(argv, stdin, env, cwd, timeout):
        return ExecResult(0, json.dumps({"total_cost_usd": 0.1, "num_turns": 2}), "")

    code = run_main(
        ["--scenario", "db-pool", "--yes", "--claude-path", sys.executable,
         "--results-dir", str(tmp_path)],
        exec_fn=fake_exec,
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1  # no report was written: a failed run
    assert "no-report" in out and "VERDICT db-pool: FAIL" in out and "Totals: 1 run(s)" in out


# --- usage limit (issue #42) -----------------------------------------------------------------

LIMIT_TEXT = "You've hit your session limit · resets 3:30pm (Europe/Berlin)"


def limit_json(**override) -> str:
    """The sanitized shape claude -p printed when the subscription limit was hit."""
    data = {
        "type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
        "api_error_origin": "first_party", "api_error": "usage_limit_reached",
        "terminal_reason": "api_error", "total_cost_usd": 0, "num_turns": 1,
        "result": LIMIT_TEXT, "permission_denials": [],
    }  # fmt: skip
    return json.dumps({**data, **override})


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_parse_result_reads_usage_limit_fields():
    parsed = runplan.parse_result(limit_json())
    assert parsed.usage_limit and parsed.is_error
    assert (parsed.api_error, parsed.api_error_status) == ("usage_limit_reached", 429)
    assert parsed.reset_hint == "3:30pm (Europe/Berlin)" and parsed.cost_usd == 0.0
    as_events = runplan.parse_result("[" + limit_json() + "]")  # list shape of older versions
    assert as_events.usage_limit and as_events.reset_hint == "3:30pm (Europe/Berlin)"


def test_reset_hint_comes_from_result_text_only_after_structured_detection():
    # the words are there, the structured fields are not: no detection, no hint
    no_code = runplan.parse_result(limit_json(api_error="rate_limit_error"))
    assert not no_code.usage_limit and no_code.reset_hint == ""
    not_error = runplan.parse_result(limit_json(is_error=False))
    assert not not_error.usage_limit and not_error.reset_hint == ""
    no_field = json.loads(limit_json())
    del no_field["api_error"]
    assert not runplan.parse_result(json.dumps(no_field)).usage_limit
    assert runplan.extract_reset_hint(None) == "" and runplan.extract_reset_hint("no time") == ""


def test_parse_result_ignores_limit_words_in_a_normal_result():
    text = "A usage limit was reached upstream; the quota resets 3:30pm"
    parsed = runplan.parse_result(json.dumps({"is_error": False, "result": text}))
    assert not parsed.usage_limit and parsed.reset_hint == "" and parsed.api_error == ""


def limit_record(n: int, **kw) -> RunRecord:
    return RunRecord("s", n, "usage_limit", "usage limit", cost_usd=0.0, **kw)


def not_run(n: int) -> RunRecord:
    return RunRecord("s", n, "skipped", runplan.NOT_RUN_LIMIT)


def test_verdict_usage_limit_is_incomplete_not_fail():
    records = [limit_record(1, reset_hint="3:30pm"), not_run(2), not_run(3)]
    v = runplan.verdict("s", records)
    assert v.label == "INCOMPLETE" and not v.passed and not v.failed_runs
    assert (v.executed, v.skipped, v.not_run_for_limit) == (0, 2, 2)
    text = runplan.format_summary([v], records)
    assert "VERDICT s: INCOMPLETE" in text and "nothing was measured" in text
    assert "resets 3:30pm" in text and "RESULT: INCOMPLETE (usage limit)" in text
    assert "FAIL" not in text


def test_usage_limit_run_is_not_in_the_pass_counts():
    v = runplan.verdict("s", [good_run(1), limit_record(2), not_run(3)])
    assert v.label == "INCOMPLETE"  # never PASS, although the counted run passed
    assert {s.name: (s.passes, s.total) for s in v.stats}["severity"] == (1, 1)
    assert v.executed == 1 and len(v.limit_runs) == 1


def test_verdict_precedence_contaminated_beats_usage_limit_beats_budget():
    leaked = limit_record(1, contamination=("Read x (outside)",))
    assert runplan.verdict("s", [leaked, not_run(2)]).label == "CONTAMINATED"
    budget = RunRecord("s", 2, "skipped", "skipped (total budget)")
    assert runplan.verdict("s", [good_run(1), budget]).label == "INCOMPLETE"
    mixed = runplan.verdict("s", [limit_record(1), budget])
    assert mixed.label == "INCOMPLETE" and mixed.not_run_for_limit == 0
    text = runplan.format_summary([mixed], [limit_record(1), budget])
    assert "stopped early by the total budget" in text and "USAGE LIMIT" in text
    done = runplan.verdict("s", [RunRecord("s", 1, "failed", "x")])
    assert done.label == "FAIL"
    both = runplan.format_summary([done, runplan.verdict("t", [not_run(1)])], [])
    assert "RESULT: FAIL" in both  # a real FAIL is not hidden by the limit


def test_complete_scenario_before_the_limit_keeps_its_verdict():
    first = [RunRecord("a", 1, "scored", checks=checks(root_cause_named=True, severity=True))]
    second = [RunRecord("b", 1, "usage_limit", "usage limit"), RunRecord("b", 2, "skipped", "x")]
    assert runplan.verdict("a", first).label == "PASS"
    assert runplan.verdict("b", second).label == "INCOMPLETE"
    text = runplan.format_summary([runplan.verdict("a", first)], first)
    assert "RESULT: PASS" in text


def test_usage_limit_in_a_single_run(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    before = reports_state(project)
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.status == "usage_limit" and record.reset_hint == "3:30pm (Europe/Berlin)"
    assert verdict.label == "INCOMPLETE" and not verdict.failed_runs and not verdict.passed
    assert reports_state(project) == before


def test_usage_limit_in_run_2_of_3_counts_run_1_and_does_not_start_run_3(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_AT", "2")
    (verdict,), records, session = evaluate(project, config, tmp_path, runs=3)
    assert [r.status for r in records] == ["scored", "usage_limit", "skipped"]
    assert records[2].reason == "not run (usage limit)"
    assert len(calls()) == 2  # run 3 never started
    assert verdict.label == "INCOMPLETE" and verdict.executed == 1
    summary = (session / "summary.txt").read_text(encoding="utf-8")
    assert "USAGE LIMIT" in summary and "resets 3:30pm (Europe/Berlin)" in summary
    assert "1 run(s) cut and 1 not run" in summary
    assert "run-2: LIMIT" in summary and "VERDICT checkout-500: INCOMPLETE" in summary


def test_usage_limit_in_first_run_of_a_multi_scenario_plan_starts_nothing_else(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    scenarios = ("checkout-500", "db-pool")
    verdicts, records, session = evaluate(project, config, tmp_path, scenarios, runs=2)
    assert [r.status for r in records] == ["usage_limit", "skipped", "skipped", "skipped"]
    assert len(calls()) == 1
    assert [v.label for v in verdicts] == ["INCOMPLETE", "INCOMPLETE"]
    summary = (session / "summary.txt").read_text(encoding="utf-8")
    assert "VERDICT db-pool: INCOMPLETE" in summary and "not run (usage limit)" in summary
    assert "RESULT: INCOMPLETE (usage limit)" in summary and "FAIL" not in summary
    assert not (session / "db-pool").exists()  # no run folder for an unstarted run


def test_usage_limit_summary_and_record_fields(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_AT", "2")
    messages: list[str] = []
    config.environ = {**config.environ, **{k: v for k, v in os.environ.items() if k[:5] == "FAKE_"}}
    _, records, session = run_eval(
        ["checkout-500"], 3, config, tmp_path / "results", 100.0, subprocess_exec,
        stamp="T1", out=messages.append,
    )  # fmt: skip
    assert any("Usage limit reached" in m and "3:30pm" in m for m in messages)
    assert any("remaining 1 run(s)" in m for m in messages)
    saved = read_json(session / "checkout-500" / "run-2" / "record.json")
    assert saved["status"] == "usage_limit" and saved["reset_hint"] == "3:30pm (Europe/Berlin)"
    assert (saved["api_error"], saved["api_error_status"]) == ("usage_limit_reached", 429)
    assert saved["reason"].startswith("usage limit") and saved["checks"] == []
    overview = read_json(session / "summary.json")
    assert overview["usage_limit"] == {
        "scenario": "checkout-500", "run": 2, "reset_hint": "3:30pm (Europe/Berlin)", "not_run": 1,
    }  # fmt: skip
    assert [r["status"] for r in overview["runs"]] == ["scored", "usage_limit", "skipped"]
    assert overview["runs"][2]["reason"] == "not run (usage limit)"
    assert overview["verdicts"] == {"checkout-500": "INCOMPLETE"}


def test_usage_limit_cost_is_counted_and_reserve_rule_is_unchanged(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_AT", "2")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_COST", "0.12")
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.5")
    _, records, session = evaluate(project, config, tmp_path, runs=3)
    assert [r.cost_usd for r in records[:2]] == [0.5, 0.12]
    assert records[2].cost_usd is None and not records[2].executed
    totals = runplan.totals(records)
    assert totals.runs == 2 and totals.cost_usd == pytest.approx(0.62)
    assert "cost $0.62" in (session / "summary.txt").read_text(encoding="utf-8")
    # the reserve rule for normal runs: per-run cap 2.0, total 5.0 stops after 3 runs of 1.5
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "good")
    monkeypatch.setenv("FAKE_CLAUDE_COST", "1.5")
    _, again, _ = evaluate(project, config, tmp_path / "again", runs=5, total=5.0)
    assert [r.status for r in again] == ["scored"] * 3 + ["skipped"] * 2


def test_usage_limit_with_contamination_is_contaminated(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_LEAK", "1")
    (verdict,), (record,), _ = evaluate(project, config, tmp_path)
    assert record.status == "usage_limit" and record.contamination
    assert verdict.label == "CONTAMINATED"


def test_cut_run_is_never_scored_even_with_a_partial_report(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_REPORT", "1")  # a complete-looking report exists
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    run_dir = session / "checkout-500" / "run-1"
    assert record.status == "usage_limit" and record.checks == ()
    assert not (run_dir / "score.txt").exists()
    saved = read_json(run_dir / "record.json")
    assert saved["checks"] == [] and saved["status"] == "usage_limit"
    assert saved["reports"] and (run_dir / saved["reports"][0]).is_file()  # kept as evidence
    assert verdict.label == "INCOMPLETE" and not verdict.stats


def test_cut_run_workcopy_is_removed_and_audit_still_scanned(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_LEAK", "1")
    (_,), (record,), session = evaluate(project, config, tmp_path)
    run_dir = session / "checkout-500" / "run-1"
    work = Path(read_json(run_dir / "command.json")["cwd"])
    assert not work.exists()
    saved = read_json(run_dir / "record.json")
    assert saved["workcopy_removed"] is True and saved["contaminated"] is True
    assert saved["contamination"][0].startswith("Read file_path=src/")
    assert (run_dir / "audit.jsonl").is_file()


def test_usage_limit_without_parsable_reset_text_is_still_incomplete(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    monkeypatch.setenv("FAKE_CLAUDE_LIMIT_TEXT", "Session limit hit, please come back later")
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.status == "usage_limit" and record.reset_hint == ""
    assert verdict.label == "INCOMPLETE"
    summary = (session / "summary.txt").read_text(encoding="utf-8")
    assert "USAGE LIMIT" in summary and "resets" not in summary
    assert "RESULT: INCOMPLETE (usage limit)" in summary
    assert read_json(session / "summary.json")["usage_limit"]["reset_hint"] == ""


def test_cli_usage_limit_exit_code_and_console_hint(tmp_path, capsys):
    def fake_exec(argv, stdin, env, cwd, timeout):
        return ExecResult(1, limit_json(), "")

    args = ["--all", "--yes", "--claude-path", sys.executable, "--results-dir", str(tmp_path)]
    assert run_main(args, exec_fn=fake_exec) == 1
    out = capsys.readouterr().out
    assert "Usage limit reached in" in out and "resets 3:30pm (Europe/Berlin)" in out
    assert out.count("VERDICT") == len(EXPECTED)
    assert "FAIL" not in out
    assert "RESULT: INCOMPLETE (usage limit)" in out


def test_report_text_with_usage_limit_words_is_not_detected(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "text-limit")
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.status == "scored" and record.reset_hint == ""
    assert verdict.passed and verdict.label == "PASS" and not verdict.limit_runs
    assert read_json(session / "summary.json")["usage_limit"] is None


def test_other_429_without_the_usage_limit_code_is_an_ordinary_failure(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "other-429")
    (verdict,), records, session = evaluate(project, config, tmp_path, runs=2)
    assert records[0].status == "failed" and records[0].reason == "claude exited with code 1"
    assert verdict.label == "FAIL" and len(calls()) == 2  # no batch stop
    saved = read_json(session / "checkout-500" / "run-1" / "record.json")
    assert (saved["api_error"], saved["api_error_status"]) == ("rate_limit_error", 429)


def test_other_api_error_is_an_ordinary_failure_even_if_its_text_says_session_limit(
    project, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "other-500")
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.status == "failed" and record.reset_hint == ""
    assert verdict.label == "FAIL" and not verdict.limit_runs
    assert "USAGE LIMIT" not in (session / "summary.txt").read_text(encoding="utf-8")
    saved = read_json(session / "checkout-500" / "run-1" / "record.json")
    assert (saved["api_error"], saved["api_error_status"]) == ("api_error", 500)


def test_normal_run_is_unchanged_by_limit_handling(project, config, tmp_path):
    (verdict,), (record,), session = evaluate(project, config, tmp_path)
    assert record.status == "scored" and verdict.passed and verdict.label == "PASS"
    summary = (session / "summary.txt").read_text(encoding="utf-8")
    assert "USAGE LIMIT" not in summary and "cut by the usage limit" not in summary
    assert "RESULT: PASS" in summary
    overview = read_json(session / "summary.json")
    assert overview["usage_limit"] is None and overview["verdicts"] == {"checkout-500": "PASS"}
