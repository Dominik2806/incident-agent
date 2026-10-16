"""Stop hook: verify an incident report written in this session before Claude stops.

Required H2 headings are parsed at runtime from the runbook skill's template.md (no copy here).
The report is found through the audit log: the latest Write/Edit/MultiEdit of this session_id on
reports/*.md. No report written -> exit 0 (Claude may stop mid-analysis). Missing or empty
sections -> exit 2 via the shared block helper (rule report-incomplete), so Claude keeps working.

Loop protection: stop_hook_active true -> exit 0 immediately (one correction round only).
Fails open: any unexpected error exits 0, this hook must never trap Claude.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import _common
import protect_files

RULE_ID = "report-incomplete"
TEMPLATE = Path(".claude") / "skills" / "incident-runbook" / "template.md"
REPORT_TOOLS = {"Write", "Edit", "MultiEdit"}
FENCE = re.compile(r"^\s*(```|~~~)")
H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*#*[ \t]*$")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def key(heading: str) -> str:
    return heading.strip().casefold()


def parse_sections(text: str) -> dict[str, tuple[str, str]]:
    """Map casefolded H2 heading -> (heading, body without HTML comments); fences are skipped."""
    sections: dict[str, tuple[str, list[str]]] = {}
    current: str | None = None
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else H2.match(line)
        if match:
            current = key(match.group(1))
            sections.setdefault(current, (match.group(1).strip(), []))
        elif current is not None:
            sections[current][1].append(line)
    return {
        k: (head, COMMENT.sub("", "\n".join(body)).strip()) for k, (head, body) in sections.items()
    }


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def report_path(payload: dict[str, Any], project: Path) -> Path | None:
    """Latest report file written by this session according to the audit log."""
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return None
    root = protect_files.normalize(str(project))
    found: str | None = None
    with open(_common.audit_path(), encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or "decision" in record:
                continue
            if record.get("session_id") != session or record.get("tool_name") not in REPORT_TOOLS:
                continue
            tool_input = record.get("input")
            raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
            if not isinstance(raw, str) or not raw:
                continue
            rel = protect_files.relative_to_project(protect_files.resolve(raw, root), root)
            if rel and rel.startswith("reports/") and rel.endswith(".md"):
                found = raw
    if found is None:
        return None
    path = Path(found.replace("\\", "/")) if not protect_files.is_absolute(found) else Path(found)
    return path if path.is_absolute() else project / path


def check(report: str, template: str) -> tuple[list[str], list[str]]:
    """Return (missing headings, empty headings) of the report against the template."""
    required = parse_sections(template)
    actual = parse_sections(report)
    missing: list[str] = []
    empty: list[str] = []
    for name, (heading, placeholder) in required.items():
        if name not in actual:
            missing.append(heading)
            continue
        body = actual[name][1]
        if not body or squash(body) == squash(placeholder):
            empty.append(heading)
    return missing, empty


def describe(label: str, headings: list[str]) -> str:
    return f"{label}: " + "; ".join(f"'{h}'" for h in headings) + "\n" if headings else ""


def main() -> None:
    payload = _common.read_payload()
    if payload.get("stop_hook_active"):
        return
    project = _common.project_dir()
    template = (project / TEMPLATE).read_text(encoding="utf-8")
    path = report_path(payload, project)
    if path is None:
        return
    missing, empty = check(path.read_text(encoding="utf-8"), template)
    if not missing and not empty:
        return
    try:
        shown = path.resolve().relative_to(project.resolve()).as_posix()
    except Exception:
        shown = path.name
    message = (
        f"BLOCKED by check_report (rule {RULE_ID}): the incident report {shown} is not complete.\n"
        + describe("Missing sections", missing)
        + describe("Empty sections", empty)
        + "Complete the report now: add the missing sections and fill the empty ones with real "
        "content from the analysis (edit the existing file, keep the template headings, do not "
        "leave placeholder comments as the only content). Do not apply any fix. "
        "After that you may finish."
    )
    audit_payload = {**payload, "tool_name": "Stop", "tool_input": {"file_path": shown}}
    _common.block(RULE_ID, message, audit_payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
