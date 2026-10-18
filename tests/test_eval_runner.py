"""The live runner, tested with a fake `claude` script: no network, no model, no API key."""

import ast
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from incident_agent.evaluation import runplan
from incident_agent.evaluation.checks import CheckResult
from incident_agent.evaluation.expected import EXPECTED
from incident_agent.evaluation.run_cli import main as run_main
from incident_agent.evaluation.runner import (
    ClaudeNotFound,
    ExecResult,
    RunConfig,
    collect_reports,
    find_claude,
    run_eval,
    server_command,
    snapshot,
    subprocess_exec,
)
from incident_agent.evaluation.runplan import RunRecord

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_claude.py"
REAL_TEMPLATE = ROOT / ".claude" / "skills" / "incident-runbook" / "template.md"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    template = root / ".claude" / "skills" / "incident-runbook" / "template.md"
    template.parent.mkdir(parents=True)
    shutil.copy(REAL_TEMPLATE, template)
    (root / "reports").mkdir()
    return root


@pytest.fixture
def config(project: Path) -> RunConfig:
    return RunConfig(
        project_root=project,
        claude=[sys.executable, str(FAKE)],
        max_budget_usd=2.0,
        timeout_s=60,
        server=["fake-server", "--flag"],
        environ={"PATH": os.environ.get("PATH", ""), "SLACK_WEBHOOK_URL": "https://secret.invalid"},
    )


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "good")
    monkeypatch.delenv("FAKE_CLAUDE_COST", raising=False)


def calls(project: Path) -> list[dict]:
    path = project / "fake-calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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
    verdicts, records, session = evaluate(project, config, tmp_path)
    run_dir = session / "checkout-500" / "run-1"
    for name in ("incident.db", "mcp.json", "command.json", "audit.jsonl", "claude-result.json"):
        if name != "audit.jsonl":  # the fake does not write an audit log
            assert (run_dir / name).exists(), name
    assert (run_dir / "reports" / "2026-03-12-checkout-500-fake.md").exists()
    assert (run_dir / "score.txt").exists() and (run_dir / "record.json").exists()
    assert (session / "summary.txt").exists() and (session / "summary.json").exists()
    assert list((project / "reports").iterdir()) == []  # picked up, nothing left behind
    (call,) = calls(project)
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
    (verdict,), (record,), _ = evaluate(project, config, tmp_path)
    assert record.status == "failed" and not verdict.passed and verdict.failed_runs
    assert list((project / "reports").iterdir()) == []


def test_missing_report_reason(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "none")
    _, (record,), _ = evaluate(project, config, tmp_path)
    assert record.reason.startswith("no-report")


def test_timeout_is_a_failed_run(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    config.timeout_s = 1.5
    _, (record,), _ = evaluate(project, config, tmp_path)
    assert record.status == "failed" and "timeout" in record.reason


# --- reports/ must not be damaged ------------------------------------------------------------


def test_existing_reports_survive_even_when_overwritten(project, config, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "overwrite")
    reports = project / "reports"
    mine = reports / "2026-03-12-fake-incident.md"  # the exact name the fake agent writes
    mine.write_bytes(b"# my own report\r\nkeep these exact bytes\n")
    os.utime(mine, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    other = reports / "other.md"
    other.write_bytes(b"untouched")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in reports.iterdir()}

    (verdict,), _, session = evaluate(project, config, tmp_path)

    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in reports.iterdir()}
    assert after == before
    saved = session / "checkout-500" / "run-1" / "reports" / "2026-03-12-fake-incident.md"
    assert saved.read_text(encoding="utf-8").startswith("# ")
    assert b"my own report" not in saved.read_bytes()
    assert verdict.passed  # the run's report was scored, not the user's


def test_collect_restores_deleted_files_and_removes_new_ones(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "keep.md").write_bytes(b"keep")
    before = snapshot(reports)
    (reports / "keep.md").unlink()
    (reports / "new.md").write_bytes(b"new")
    copies = collect_reports(reports, before, tmp_path / "dest")
    assert [p.name for p in copies] == ["new.md"]
    assert (reports / "keep.md").read_bytes() == b"keep" and not (reports / "new.md").exists()


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
    assert len(calls(project)) == 3  # spent 4.5 + a cap of 2.0 would exceed 5.0: stop
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
    code = run_main(
        ["--all", "--runs", "2", "--dry-run", "--claude-path", sys.executable,
         "--results-dir", str(results)],
        exec_fn=boom,
        ask=boom,
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and not results.exists()
    assert "Planned: 2 run(s) x 3 scenario(s)" in out and "maximum $3.00" in out
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
