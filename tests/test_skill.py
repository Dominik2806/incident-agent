import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".claude" / "skills" / "incident-runbook"
EXAMPLE_REPORT = ROOT / "docs" / "examples" / "incident-report.md"
# Every example report (incident-report.md, incident-report-<name>.md, ...) follows the template.
EXAMPLE_REPORTS = sorted(EXAMPLE_REPORT.parent.glob("incident-report*.md"))

TEMPLATE_HEADINGS = [
    "Summary",
    "Severity",
    "Impact",
    "Timeline (UTC)",
    "Root cause",
    "Evidence",
    "Ruled out",
    "Immediate actions (proposed)",
    "Follow-up tasks",
    "Open questions",
]


def read(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Return the top-level `key: value` fields of the frontmatter without a YAML dependency."""
    lines = text.split("\n")
    assert lines[0].strip() == "---", "SKILL.md must start with '---'"
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def h2_headings(text: str) -> list[str]:
    return [line[3:].strip() for line in text.split("\n") if line.startswith("## ")]


def is_subsequence(required: list[str], actual: list[str]) -> bool:
    remaining = iter(actual)
    return all(heading in remaining for heading in required)


def test_parser_handles_crlf_and_h2() -> None:
    fields = parse_frontmatter("---\nname: x\ndescription: d: e\n---\nbody")
    assert fields == {"name": "x", "description": "d: e"}
    assert h2_headings("# T\n## A\n### B\n## C (x)\n") == ["A", "C (x)"]
    assert is_subsequence(["A", "C"], ["A", "B", "C"])
    assert not is_subsequence(["C", "A"], ["A", "B", "C"])


def test_skill_frontmatter() -> None:
    fields = parse_frontmatter(read("SKILL.md"))
    assert fields.get("name") == SKILL_DIR.name
    description = fields.get("description", "")
    assert description
    assert len(description) < 1024


def test_skill_is_concise() -> None:
    assert len(read("SKILL.md").splitlines()) < 100


def test_referenced_files_exist() -> None:
    text = read("SKILL.md")
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    local = [t.split("#")[0] for t in targets if not re.match(r"^[a-z]+:", t) and t.split("#")[0]]
    assert {"severity.md", "template.md"} <= set(local)
    for target in local:
        assert (SKILL_DIR / target).is_file(), f"SKILL.md references missing file {target}"


def test_severity_mentions_all_levels() -> None:
    text = read("severity.md")
    for level in ("SEV-1", "SEV-2", "SEV-3", "SEV-4"):
        assert level in text


def test_template_has_required_headings_in_order() -> None:
    assert h2_headings(read("template.md")) == TEMPLATE_HEADINGS


def test_template_header_block_precedes_first_heading() -> None:
    text = read("template.md")
    header = text.split("\n## ", 1)[0]
    assert "Proposed actions only, nothing applied" in header
    assert "Sources" in header


def test_skill_calls_fix_proposer_after_root_cause_before_report() -> None:
    text = read("SKILL.md")
    assert "`fix-proposer`" in text
    assert text.index("`root-cause`") < text.index("`fix-proposer`") < text.index("**Report.**")


def test_template_immediate_actions_ask_for_patch_rollback_and_risks() -> None:
    text = read("template.md")
    section = text.split("## Immediate actions (proposed)", 1)[1].split("\n## ", 1)[0]
    for heading in ("### Patch", "### Rollback", "### Risks"):
        assert heading in section
    assert "diff" in section


def test_example_reports_exist() -> None:
    assert EXAMPLE_REPORT in EXAMPLE_REPORTS


@pytest.mark.parametrize("report", EXAMPLE_REPORTS, ids=lambda path: path.name)
def test_example_report_follows_template(report: Path) -> None:
    text = report.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert is_subsequence(TEMPLATE_HEADINGS, h2_headings(text))
