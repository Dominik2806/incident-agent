"""Run all checks for a scenario and format the result."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.evaluation import checks
from incident_agent.evaluation.checks import CheckResult
from incident_agent.evaluation.expected import EXPECTED, GroundTruth
from incident_agent.evaluation.report import Report


@dataclass(frozen=True)
class Score:
    scenario: str
    results: tuple[CheckResult, ...]

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.gating and not r.passed)

    @property
    def warnings(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if not r.gating and not r.passed)

    @property
    def passed(self) -> bool:
        return not self.failures

    def check(self, name: str) -> CheckResult:
        return next(r for r in self.results if r.name == name)


def score_report(
    text: str, scenario: str, truth: GroundTruth, template: str | None = None
) -> Score:
    """Score a report. `template` is the text of template.md; None skips report-complete."""
    expected = EXPECTED[scenario]
    report = Report.parse(text)
    results: list[CheckResult] = []
    if template is not None:
        results.append(checks.check_report_complete(report, template))
    results += [
        checks.check_root_cause_named(report, expected),
        checks.check_red_herring_ruled_out(report, expected),
        checks.check_severity(report, expected),
        checks.check_confidence(report),
    ]
    if expected.clients:
        results.append(checks.check_counts_present(report, expected))
    results += [
        checks.check_counts_reconciled(report, expected, truth),
        checks.check_no_inferred_counts(report),
    ]
    return Score(scenario=scenario, results=tuple(results))


def format_score(score: Score) -> str:
    """Gating checks first; non-gating checks are listed separately as warnings."""
    lines = [f"Scenario: {score.scenario}"]
    for result in score.results:
        if result.gating:
            status = "PASS" if result.passed else "FAIL"
            lines.append(f"  {status}  {result.name}  {result.detail}".rstrip())
    warnings = [r for r in score.results if not r.gating]
    if warnings:
        lines.append("Warnings (do not affect the result):")
        for result in warnings:
            status = "ok  " if result.passed else "WARN"
            lines.append(f"  {status}  {result.name}  {result.detail}".rstrip())
    lines.append("RESULT: " + ("PASS" if score.passed else "FAIL"))
    return "\n".join(lines)
