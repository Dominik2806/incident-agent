"""Parse an incident report (Markdown) into sections and per-client counts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FENCE = re.compile(r"^\s*(```|~~~)")
H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*#*[ \t]*$")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
INTEGER = re.compile(r"\d[\d,]*")
MARKUP = re.compile(r"[*_`]")

TEMPLATE = Path(".claude") / "skills" / "incident-runbook" / "template.md"


def default_template() -> Path:
    """template.md of the runbook skill, found from the repository root (source checkout)."""
    return Path(__file__).resolve().parents[3] / TEMPLATE


def key(heading: str) -> str:
    return heading.strip().casefold()


def parse_sections(text: str) -> dict[str, tuple[str, str]]:
    """Map casefolded H2 heading -> (heading, body without HTML comments). Fences are skipped."""
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


@dataclass(frozen=True)
class Report:
    text: str
    sections: dict[str, tuple[str, str]]

    @classmethod
    def parse(cls, text: str) -> Report:
        return cls(text=text, sections=parse_sections(text))

    def section(self, heading: str) -> str:
        """Body of an H2 section, or an empty string if it is missing."""
        return self.sections.get(key(heading), ("", ""))[1]


def first_int(cell: str) -> int | None:
    match = INTEGER.search(cell)
    return int(match.group(0).replace(",", "")) if match else None


def table_cells(line: str) -> list[str]:
    return [MARKUP.sub("", c).strip() for c in line.strip().strip("|").split("|")]


def client_counts(text: str, clients: tuple[str, ...]) -> dict[str, tuple[int | None, int | None]]:
    """(ok, failed) per client from the first table row that names it.

    Columns come from the nearest table header with an "ok"/"success" and a "fail"/"error" column;
    without a header the first two numeric cells after the client name are (ok, failed). The first
    integer of a cell is the count, so "0 (inferred: ...)" reads as 0 (another check rejects it).
    """
    found: dict[str, tuple[int | None, int | None]] = {}
    ok_col = failed_col = None
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            ok_col = failed_col = None
            continue
        cells = table_cells(line)
        lowered = [c.casefold() for c in cells]
        ok_idx = next((i for i, c in enumerate(lowered) if re.search(r"\bok\b|success", c)), None)
        fail_idx = next((i for i, c in enumerate(lowered) if re.search(r"fail|error", c)), None)
        if ok_idx is not None and fail_idx is not None and not any(first_int(c) for c in cells):
            ok_col, failed_col = ok_idx, fail_idx
            continue
        for client in clients:
            index = next((i for i, c in enumerate(cells) if c.startswith(client)), None)
            if index is None or client in found:
                continue
            if ok_col is not None and failed_col is not None:
                ok_cell, failed_cell = (
                    cells[ok_col : ok_col + 1],
                    cells[failed_col : failed_col + 1],
                )
            else:
                ok_cell, failed_cell = cells[index + 1 : index + 2], cells[index + 2 : index + 3]
            found[client] = (
                first_int(ok_cell[0]) if ok_cell else None,
                first_int(failed_cell[0]) if failed_cell else None,
            )
    return found
