"""The deterministic checks. Each one is a pure function: report in, `CheckResult` out."""

from __future__ import annotations

import re
from dataclasses import dataclass

from incident_agent.evaluation.expected import ExpectedResult, GroundTruth
from incident_agent.evaluation.report import (
    INTEGER,
    Report,
    client_candidates,
    conflicting_order_lines,
    parse_sections,
    split_confidence,
)

LEVELS = ("high", "medium", "low")
SEVERITY = re.compile(r"\bSEV-([1-4])\b")
CONFIDENCE = re.compile(r"\bconfidence\b[\s:*_`]*([A-Za-z][A-Za-z/-]*)", re.IGNORECASE)
# A count presented as inferred or estimated. "assumed" is left out on purpose: it is used for
# legitimate assumptions such as a default currency.
INFERRED = re.compile(r"\b(inferred|estimated|extrapolated)\b", re.IGNORECASE)
# Versions and times contain digits but are not counts.
VERSION = re.compile(r"\b[A-Za-z]+-\d+(?:\.\d+)+\b|\bv\d+(?:\.\d+)+\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
NEAR = 40  # characters around the keyword that count as "the same statement"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    gating: bool = True


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def check_report_complete(report: Report, template: str) -> CheckResult:
    """Every H2 of template.md is present and has content."""
    missing: list[str] = []
    empty: list[str] = []
    for name, (heading, placeholder) in parse_sections(template).items():
        body = report.sections.get(name, ("", ""))[1]
        if name not in report.sections:
            missing.append(heading)
        elif not body or squash(body) == squash(placeholder):
            empty.append(heading)
    problems = []
    if missing:
        problems.append("missing: " + ", ".join(missing))
    if empty:
        problems.append("empty: " + ", ".join(empty))
    return CheckResult("report-complete", not problems, "; ".join(problems))


def check_root_cause_named(report: Report, expected: ExpectedResult) -> CheckResult:
    body = report.section("Root cause")
    # The required terms may come from the whole section. A red herring is only held against the
    # cause statement: the confidence statement may say what could not be excluded.
    cause, _ = split_confidence(body)
    missing = [p for p in expected.cause_patterns if not re.search(p, body, re.IGNORECASE)]
    forbidden = [p for p in expected.forbidden_cause_patterns if re.search(p, cause, re.IGNORECASE)]
    problems = []
    if not body:
        problems.append("Root cause section is missing or empty")
    if missing:
        problems.append("not named: " + ", ".join(missing))
    if forbidden:
        problems.append("red herring named in Root cause: " + ", ".join(forbidden))
    return CheckResult("root-cause-named", not problems, "; ".join(problems))


def check_red_herring_ruled_out(report: Report, expected: ExpectedResult) -> CheckResult:
    body = report.section("Ruled out")
    missing = [p for p in expected.red_herring_patterns if not re.search(p, body, re.IGNORECASE)]
    detail = ("not in Ruled out: " + ", ".join(missing)) if missing else ""
    return CheckResult("red-herring-ruled-out", not missing, detail)


def check_severity(report: Report, expected: ExpectedResult) -> CheckResult:
    match = SEVERITY.search(report.section("Severity"))
    if not match:
        return CheckResult("severity", False, "no SEV-1 to SEV-4 in the Severity section")
    found = f"SEV-{match.group(1)}"
    detail = f"{found}, expected {expected.severity}"
    return CheckResult("severity", found == expected.severity, detail)


def check_confidence(report: Report) -> CheckResult:
    """The first word after "Confidence" is exactly high, medium or low; the rest is ignored."""
    match = CONFIDENCE.search(report.section("Root cause"))
    if not match:
        return CheckResult("confidence", False, "no 'Confidence: ...' in the Root cause section")
    word = match.group(1)
    return CheckResult("confidence", word.casefold() in LEVELS, f"found {word!r}")


def order_conflict_detail(report: Report, expected: ExpectedResult) -> str:
    """Why a line with explicit pairs of both orders (ok/failed and failed/ok) is unreadable."""
    lines = conflicting_order_lines(report.text, expected.clients)
    return "ok/failed and failed/ok pairs on one line: " + " | ".join(f"'{x}'" for x in lines[:3])


def check_counts_present(report: Report, expected: ExpectedResult) -> CheckResult:
    candidates = client_candidates(report.text, expected.clients)
    missing = [c for c in expected.clients if not candidates[c]]
    problems = []
    if missing:
        problems.append("no ok and failed count for: " + ", ".join(missing))
    if expected.clients and conflicting_order_lines(report.text, expected.clients):
        problems.append(order_conflict_detail(report, expected))
    return CheckResult("counts-present", not problems, "; ".join(problems))


def check_counts_reconciled(
    report: Report, expected: ExpectedResult, truth: GroundTruth
) -> CheckResult:
    """Per-client (ok, failed) equal the database for one allowed start time (checkout-500), or
    the total number of failed requests appears in the report (no clients, e.g. db-pool)."""
    name, gating = "counts-reconciled", expected.reconcile_gating
    if not expected.clients:
        numbers = {int(n.replace(",", "")) for n in INTEGER.findall(report.text)}
        found = truth.total_failed in numbers
        detail = f"total failed {truth.total_failed} " + ("found" if found else "not in the report")
        return CheckResult(name, found, detail, gating)
    if conflicting_order_lines(report.text, expected.clients):
        return CheckResult(name, False, order_conflict_detail(report, expected), gating)
    candidates = client_candidates(report.text, expected.clients)
    if missing := [c for c in expected.clients if not candidates[c]]:
        return CheckResult(name, False, "per-client counts missing: " + ", ".join(missing), gating)
    if conflicts := [c for c in expected.clients if len(candidates[c]) > 1]:
        detail = "conflicting (ok, failed) in the report: " + "; ".join(
            f"{c}: " + " vs ".join(str(n) for n in candidates[c]) for c in conflicts
        )
        return CheckResult(name, False, detail, gating)
    counts = {c: candidates[c][0] for c in expected.clients}
    for window in truth.windows.values():
        if all(window.get(c, (0, 0)) == counts[c] for c in expected.clients):
            return CheckResult(name, True, "matches the database", gating)
    nearest = next(iter(truth.windows.values()))
    differences = [
        f"{c}: report {counts[c]}, database {nearest.get(c, (0, 0))}"
        for c in expected.clients
        if nearest.get(c, (0, 0)) != counts[c]
    ]
    detail = "(ok, failed) differ from the database: " + "; ".join(differences)
    return CheckResult(name, False, detail, gating)


def count_statements(line: str) -> list[str]:
    """Pieces of a line in which a count is presented as inferred or estimated."""
    hits = []
    cells = [c for c in line.split("|") if c.strip()] if line.lstrip().startswith("|") else [line]
    for cell in cells:
        text = VERSION.sub(" ", cell)
        for keyword in INFERRED.finditer(text):
            window = text[max(0, keyword.start() - NEAR) : keyword.end() + NEAR]
            if INTEGER.search(window.replace(keyword.group(0), " ")):
                hits.append(window.strip())
    return hits


def check_no_inferred_counts(report: Report) -> CheckResult:
    hits = [hit for line in report.text.splitlines() for hit in count_statements(line)]
    return CheckResult("no-inferred-counts", not hits, "; ".join(f"'{h}'" for h in hits[:3]))
