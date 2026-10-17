import asyncio
from pathlib import Path

import pytest

from incident_agent.mcp_server import mcp

AGENTS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "agents"
AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))

MCP_TOOLS = {
    f"mcp__incident-logs__{name}"
    for name in ("get_error_stats", "search_logs", "get_deployments", "get_stacktrace")
}
ALLOWED_TOOLS = MCP_TOOLS | {"Read", "Grep", "Glob"}
FORBIDDEN_TOOLS = {"Write", "Edit", "Bash", "NotebookEdit"}

TRIAGE_TOOLS = {
    "mcp__incident-logs__get_error_stats",
    "mcp__incident-logs__search_logs",
}
ROOT_CAUSE_TOOLS = MCP_TOOLS | {"Read", "Grep"}
FIX_PROPOSER_TOOLS = {"Read", "Grep", "Glob"}

REQUIRED_SECTIONS = {
    "triage": [
        "Affected services",
        "Time range",
        "Impact",
        "Preliminary severity",
        "Suspected scope",
        "Handoff notes for root-cause",
    ],
    "root-cause": [
        "Root cause",
        "Confidence",
        "Evidence",
        "Ruled out",
        "Unknowns and next checks",
        "Recommended next step",
    ],
    "fix-proposer": [
        "Code location",
        "Proposed patch",
        "Rollback alternative",
        "Risk assessment",
        "Not applied",
    ],
}


def parse_tools(value: str, block: list[str]) -> list[str]:
    """Parse `tools` as a comma-separated string, an inline [a, b] list or a `- item` block."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items = [item.strip() for item in value.split(",")]
    items += [line.strip()[1:].strip() for line in block]
    return [item for item in items if item]


def parse_agent(text: str) -> tuple[dict[str, str], list[str], str]:
    """Split an agent file into (frontmatter fields, tools, body) without a YAML dependency."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    assert lines[0].strip() == "---", "file must start with '---'"
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    fields: dict[str, str] = {}
    tools_block: list[str] = []
    current = None
    for line in lines[1:end]:
        if line.lstrip().startswith("- ") and current == "tools":
            tools_block.append(line)
        elif ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            current = key.strip()
            fields[current] = value.strip()
    tools = parse_tools(fields.get("tools", ""), tools_block)
    return fields, tools, "\n".join(lines[end + 1 :])


def load(name: str) -> tuple[dict[str, str], list[str], str]:
    return parse_agent((AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8"))


def test_parser_handles_crlf_and_list_styles() -> None:
    text = "---\r\nname: x\r\ndescription: d\r\ntools:\r\n  - Read\r\n  - Grep\r\n---\r\nbody\r\n"
    fields, tools, body = parse_agent(text)
    assert fields["name"] == "x" and tools == ["Read", "Grep"] and body.strip() == "body"
    assert parse_agent("---\nname: x\ntools: [Read, Grep]\n---\n")[1] == ["Read", "Grep"]
    assert parse_agent("---\nname: x\ntools: Read, Grep\n---\n")[1] == ["Read", "Grep"]


def test_expected_agents_exist() -> None:
    assert {"triage", "root-cause", "fix-proposer"} <= {p.stem for p in AGENT_FILES}


def test_allowlist_matches_real_server_tools() -> None:
    server_tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {t.removeprefix("mcp__incident-logs__") for t in MCP_TOOLS} == server_tools


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_agent_frontmatter_and_tools(path: Path) -> None:
    fields, tools, _ = parse_agent(path.read_text(encoding="utf-8"))
    assert fields.get("name") == path.stem
    assert fields.get("description", "").strip()
    assert tools, "tools must be listed explicitly (omitting it inherits every tool)"
    assert len(tools) == len(set(tools))
    assert set(tools) <= ALLOWED_TOOLS, set(tools) - ALLOWED_TOOLS
    assert not set(tools) & FORBIDDEN_TOOLS


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_agent_body_has_required_sections(path: Path) -> None:
    sections = REQUIRED_SECTIONS.get(path.stem)
    assert sections is not None, f"no required sections defined for {path.stem}"
    body = parse_agent(path.read_text(encoding="utf-8"))[2]
    headings = [line.lstrip("#").strip() for line in body.splitlines() if line.startswith("#")]
    for section in sections:
        assert section in headings, f"{path.stem} is missing section '{section}'"


def test_triage_has_exactly_two_tools() -> None:
    assert set(load("triage")[1]) == TRIAGE_TOOLS
    assert len(load("triage")[1]) == 2


def test_root_cause_has_all_mcp_tools_plus_read_and_grep() -> None:
    assert set(load("root-cause")[1]) == ROOT_CAUSE_TOOLS


def test_root_cause_confidence_is_exactly_one_value() -> None:
    body = load("root-cause")[2]
    assert "exactly one of `high`, `medium` or `low`" in body
    assert "Never combine or qualify the value" in body


def test_root_cause_measures_instead_of_infers() -> None:
    body = load("root-cause")[2]
    for phrase in (
        "every client version present",
        "total_matches",
        "request volume",
        "INFO plus ERROR",
        "not measured",
        "a result is truncated",
    ):
        assert phrase in body, phrase


def test_triage_starts_coarse_and_handles_truncation() -> None:
    body = load("triage")[2]
    for phrase in ("interval_minutes: 60", "errors_only", "next_offset", "omitted_windows"):
        assert phrase in body, phrase
    assert "default interval" not in body


def test_fix_proposer_has_exactly_read_grep_glob() -> None:
    tools = load("fix-proposer")[1]
    assert set(tools) == FIX_PROPOSER_TOOLS
    assert len(tools) == 3
    assert not any(t.startswith("mcp__") for t in tools)
