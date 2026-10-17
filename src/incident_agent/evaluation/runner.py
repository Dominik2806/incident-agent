"""The live runner: starts `claude -p` for each run, picks up the report and scores it.

Local only, never called from CI. The only place that starts a process is `subprocess_exec`, the
seam
that tests replace (or point at a fake `claude` script). Pure logic is in `runplan.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from incident_agent.data.constants import DEFAULT_SEED
from incident_agent.data.database import build_database
from incident_agent.evaluation import runplan
from incident_agent.evaluation.expected import ground_truth
from incident_agent.evaluation.runplan import RunRecord, ScenarioVerdict
from incident_agent.evaluation.scoring import format_score, score_report

CLAUDE_ENV = "CLAUDE_BIN"
SERVER_EXE = "incident-logs-server"


@dataclass(frozen=True)
class ExecResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


ExecFn = Callable[[Sequence[str], str, Mapping[str, str], Path, float], ExecResult]


def subprocess_exec(
    argv: Sequence[str], stdin: str, env: Mapping[str, str], cwd: Path, timeout: float
) -> ExecResult:
    """Run a command with the prompt on stdin. On timeout the whole process tree is killed (on
    Windows the MCP server child would otherwise keep its executable locked)."""
    proc = subprocess.Popen(  # noqa: S603 - argv is built by us, no shell
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
        return ExecResult(proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            proc.kill()
        out, err = proc.communicate()
        return ExecResult(None, out or "", err or "", timed_out=True)


class ClaudeNotFound(Exception):
    pass


def find_claude(explicit: str | None = None, environ: Mapping[str, str] | None = None) -> list[str]:
    """`--claude-path`, then `CLAUDE_BIN`, then PATH (shutil.which honors PATHEXT: .exe, .cmd)."""
    environ = os.environ if environ is None else environ
    candidate = explicit or environ.get(CLAUDE_ENV)
    if candidate:
        path = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if not path:
            raise ClaudeNotFound(f"claude executable not found: {candidate}")
        return [path]
    found = shutil.which("claude")
    if not found:
        raise ClaudeNotFound(
            "claude executable not found on PATH (use --claude-path or CLAUDE_BIN)"
        )
    return [found]


def server_command(python: str | None = None) -> list[str]:
    """Start the MCP server without `uv`: the venv's console script if present, else `python -m`.
    Never `uv run`, which may sync and cannot replace an executable a running server has locked."""
    python = python or sys.executable
    exe = Path(python).parent / (SERVER_EXE + (".exe" if sys.platform == "win32" else ""))
    if exe.exists():
        return [str(exe)]
    return [python, "-m", "incident_agent.mcp_server"]


def snapshot(reports_dir: Path) -> dict[str, tuple[int, bytes]]:
    """Relative name -> (mtime_ns, original bytes) for every file in reports/."""
    if not reports_dir.is_dir():
        return {}
    return {
        p.relative_to(reports_dir).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
        for p in reports_dir.rglob("*")
        if p.is_file()
    }


def collect_reports(
    reports_dir: Path, before: Mapping[str, tuple[int, bytes]], dest: Path
) -> list[Path]:
    """Copy the files the run created or changed into `dest`, then put reports/ back as it was.

    A pre-existing file the run overwrote gets its original bytes (and mtime) back; a pre-existing
    file the run deleted is recreated; new files are removed from reports/. Returns the copies,
    newest first.
    """
    after = snapshot(reports_dir)
    copies: list[tuple[int, Path]] = []
    for name, (mtime, data) in after.items():
        old = before.get(name)
        if old is not None and old[0] == mtime and old[1] == data:
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copies.append((mtime, target))
        source = reports_dir / name
        if old is None:
            source.unlink()
        else:
            source.write_bytes(old[1])
            os.utime(source, ns=(old[0], old[0]))
    for name, (mtime, data) in before.items():
        if name not in after:
            restored = reports_dir / name
            restored.parent.mkdir(parents=True, exist_ok=True)
            restored.write_bytes(data)
            os.utime(restored, ns=(mtime, mtime))
    for directory in sorted(
        (p for p in reports_dir.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)
    ):
        if not any(directory.iterdir()) and directory.relative_to(reports_dir).as_posix() not in {
            Path(n).parent.as_posix() for n in before
        }:
            directory.rmdir()
    return [path for _, path in sorted(copies, key=lambda c: c[0], reverse=True)]


@dataclass
class RunConfig:
    project_root: Path
    claude: Sequence[str]
    max_budget_usd: float = runplan.DEFAULT_MAX_BUDGET_USD
    timeout_s: float = runplan.DEFAULT_TIMEOUT_S
    model: str | None = None
    seed: int = DEFAULT_SEED
    server: Sequence[str] = field(default_factory=server_command)
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"


def template_text(project_root: Path) -> str | None:
    path = project_root / ".claude" / "skills" / "incident-runbook" / "template.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def run_once(
    scenario: str, number: int, run_dir: Path, config: RunConfig, exec_fn: ExecFn
) -> RunRecord:
    """One headless run against a fresh database; never raises for a bad run, it records it."""
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "incident.db"
    audit_log = run_dir / "audit.jsonl"
    mcp_path = run_dir / "mcp.json"
    build_database(scenario, config.seed, db_path)
    mcp_path.write_text(
        json.dumps(runplan.build_mcp_config(config.server, db_path), indent=2), encoding="utf-8"
    )
    argv = runplan.build_command(config.claude, mcp_path, config.max_budget_usd, config.model)
    (run_dir / "command.json").write_text(
        json.dumps({"argv": argv, "stdin": runplan.PROMPTS[scenario]}, indent=2), encoding="utf-8"
    )
    env = runplan.run_env(config.environ, db_path, audit_log)

    before = snapshot(config.reports_dir)
    started = time.monotonic()
    result = exec_fn(argv, runplan.PROMPTS[scenario], env, config.project_root, config.timeout_s)
    duration = time.monotonic() - started
    copies = collect_reports(config.reports_dir, before, run_dir / "reports")

    (run_dir / "claude-result.json").write_text(result.stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    parsed = runplan.parse_result(result.stdout)

    def record(status: str, reason: str = "", checks=()) -> RunRecord:
        out = RunRecord(
            scenario=scenario,
            run=number,
            status=status,
            reason=reason,
            cost_usd=parsed.cost_usd,
            turns=parsed.turns,
            duration_s=duration,
            checks=tuple(checks),
        )
        (run_dir / "record.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "reason": reason,
                    "cost_usd": parsed.cost_usd,
                    "turns": parsed.turns,
                    "duration_s": round(duration, 1),
                    "subtype": parsed.subtype,
                    "reports": [p.relative_to(run_dir).as_posix() for p in copies],
                    "checks": [
                        {"name": c.name, "passed": c.passed, "gating": c.gating, "detail": c.detail}
                        for c in checks
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return out

    if result.timed_out:
        return record("failed", f"timeout after {config.timeout_s:.0f}s")
    if result.returncode != 0:
        return record("failed", f"claude exited with code {result.returncode}")
    if parsed.is_error:
        return record("failed", f"claude reported an error ({parsed.subtype or 'unknown'})")
    reports = [p for p in copies if p.suffix == ".md"]
    if not reports:
        return record("failed", "no-report: no report was written to reports/")

    text = reports[0].read_text(encoding="utf-8")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        truth = ground_truth(conn, scenario)
    finally:
        conn.close()
    score = score_report(text, scenario, truth, template_text(config.project_root))
    (run_dir / "score.txt").write_text(format_score(score) + "\n", encoding="utf-8")
    note = f"{len(reports)} reports written, scored the newest" if len(reports) > 1 else ""
    return record("scored", note, score.results)


def timestamp_now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def run_eval(
    scenarios: Sequence[str],
    runs: int,
    config: RunConfig,
    results_root: Path,
    total_budget_usd: float,
    exec_fn: ExecFn = subprocess_exec,
    stamp: str | None = None,
    out: Callable[[str], None] = print,
) -> tuple[list[ScenarioVerdict], list[RunRecord], Path]:
    """Run every scenario `runs` times in order. When the next run could exceed the total budget,
    the rest is skipped (recorded, not scored). No retries."""
    session = results_root / (stamp or timestamp_now())
    records: list[RunRecord] = []
    spent = 0.0
    stopped = False
    for scenario in scenarios:
        for number in range(1, runs + 1):
            if stopped or spent + config.max_budget_usd > total_budget_usd:
                if not stopped:
                    out(
                        f"Total budget ${total_budget_usd:.2f} would be exceeded "
                        f"(spent ${spent:.2f}); skipping the remaining runs."
                    )
                stopped = True
                records.append(RunRecord(scenario, number, "skipped", "skipped (total budget)"))
                continue
            out(f"[{scenario}] run {number}/{runs} ...")
            record = run_once(
                scenario, number, session / scenario / f"run-{number}", config, exec_fn
            )
            spent += record.cost_usd if record.cost_usd is not None else config.max_budget_usd
            records.append(record)
            out(
                f"[{scenario}] run {number}: {record.status}"
                + (f" ({record.reason})" if record.reason else "")
            )
    verdicts = [runplan.verdict(s, [r for r in records if r.scenario == s]) for s in scenarios]
    summary = runplan.format_summary(verdicts, records, config.model)
    session.mkdir(parents=True, exist_ok=True)
    (session / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    (session / "summary.json").write_text(
        json.dumps(
            {
                "verdicts": {v.scenario: v.label for v in verdicts},
                "totals": runplan.totals(records).__dict__,
                "runs": [
                    {"scenario": r.scenario, "run": r.run, "status": r.status, "reason": r.reason}
                    for r in records
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return verdicts, records, session


def dry_run_text(
    scenarios: Sequence[str], runs: int, config: RunConfig, results_root: Path, stamp: str
) -> str:
    """What would run. Creates nothing and calls nothing; secret values are never printed."""
    lines = ["DRY RUN: no claude process is started, no file is created."]
    for scenario in scenarios:
        for number in range(1, runs + 1):
            run_dir = results_root / stamp / scenario / f"run-{number}"
            db_path, mcp_path = run_dir / "incident.db", run_dir / "mcp.json"
            argv = runplan.build_command(
                config.claude, mcp_path, config.max_budget_usd, config.model
            )
            lines += [
                "",
                f"[{scenario}] run {number}/{runs}",
                f"  cwd:      {config.project_root}",
                f"  command:  {subprocess.list2cmdline(argv)}",
                f"  stdin:    {runplan.PROMPTS[scenario]}",
                f"  database: {db_path} (scenario {scenario}, seed {config.seed})",
                f"  mcp.json: {json.dumps(runplan.build_mcp_config(config.server, db_path))}",
            ]
            for key, value in runplan.env_overrides(db_path, run_dir / "audit.jsonl").items():
                lines.append(f"  env:      {key}={value}")
            lines.append("  env:      " + ", ".join(f"{k} removed" for k in runplan.SECRET_ENV))
    return "\n".join(lines)
