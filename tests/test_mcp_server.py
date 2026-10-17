import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from incident_agent.mcp_server import mcp

TOOLS = {"search_logs", "get_error_stats", "get_deployments", "get_stacktrace"}


def call(name: str, arguments: dict[str, Any]) -> Any:
    _, structured = asyncio.run(mcp.call_tool(name, arguments))
    return structured


def test_server_exposes_exactly_the_four_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == TOOLS and len(tools) == 4
    for tool in tools:
        assert tool.description and tool.description.strip()
        assert "2026-03-12" in tool.description or tool.name == "get_stacktrace"


def test_parameters_are_typed_with_enums() -> None:
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schema = json.dumps(tools["search_logs"].inputSchema)
    for word in ("checkout", "payment", "catalog", "auth", "WARN", "ERROR"):
        assert word in schema
    assert tools["get_stacktrace"].inputSchema["required"] == ["error_id"]


def test_error_stats_new_parameters_are_optional() -> None:
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schema = tools["get_error_stats"].inputSchema
    assert not schema.get("required")
    assert {"start", "end", "interval_minutes", "service", "errors_only", "offset"} <= set(
        schema["properties"]
    )


def test_error_stats_truncation_is_reported_through_mcp(
    incident_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(incident_db))
    first = call("get_error_stats", {})
    assert first["truncated"] is True and first["omitted_windows"] == 945
    assert first["next_offset"] == 200 and "945" in first["hint"]
    second = call("get_error_stats", {"offset": first["next_offset"]})
    assert second["windows"][0]["window_start"] >= first["windows"][-1]["window_start"]
    narrow = call("get_error_stats", {"service": "checkout", "errors_only": True})
    assert narrow["truncated"] is False and narrow["next_offset"] is None


def test_error_stats_invalid_offset_gives_clear_error(
    incident_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(incident_db))
    with pytest.raises(ToolError, match="offset"):
        call("get_error_stats", {"offset": -1})


def test_tool_call_uses_db_from_environment(
    incident_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(incident_db))
    result = call("get_deployments", {"service": "payment"})
    assert result["deployments"][0]["version"] == "v1.4.2"
    logs = call("search_logs", {"level": "ERROR", "query": "KeyError", "limit": 1})
    stack = call("get_stacktrace", {"error_id": logs["logs"][0]["error_id"]})
    assert stack["exception_type"] == "KeyError"


def test_missing_database_gives_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "nope.db"))
    with pytest.raises(ToolError, match="generate-data --scenario checkout-500"):
        call("search_logs", {})


def test_unknown_error_id_gives_clear_error(
    incident_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(incident_db))
    with pytest.raises(ToolError) as info:
        call("get_stacktrace", {"error_id": 987654})
    assert "No error with id 987654" in str(info.value)
    assert "Traceback" not in str(info.value)


def test_invalid_enum_is_rejected(incident_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(incident_db))
    with pytest.raises(ToolError):
        call("search_logs", {"level": "FATAL"})
