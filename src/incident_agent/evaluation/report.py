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


PAIR_GAP = r"[^\d/\n|]{0,30}?"
NUMBER = r"\d+(?:,\d{3})*"
# "(ok / failed)" or "ok / failed:" names the order of the numbers in an inline list.
OK_FAILED_LABEL = re.compile(
    r"\(\s*ok\s*/\s*fail(?:ed|ures)?\s*\)|\bok\s*/\s*failed\s*:", re.IGNORECASE
)
PLAIN_INT = re.compile(r"^\d[\d,]*(?![\d:.])")


def pair_counts(text: str, client: str) -> list[tuple[int, int]]:
    """(ok, failed) for every "<client> N/M" in the text.

    By default N is failed out of M total, so ok = M - N, and N greater than M is not a valid pair.
    If an explicit "(ok / failed)" label comes earlier on the same line, the pair is read as
    ok = N, failed = M instead (any values, 0 included). An optional percentage is ignored.
    """
    pattern = re.compile(
        re.escape(client) + rf"(?![\w-]|\.\d){PAIR_GAP}({NUMBER})\s*/\s*({NUMBER})(?![\d/])"
    )
    found = []
    for match in pattern.finditer(text):
        first, second = (int(g.replace(",", "")) for g in match.groups())
        line_start = text.rfind("\n", 0, match.start()) + 1
        if OK_FAILED_LABEL.search(text, line_start, match.start()):
            found.append((first, second))
        elif first <= second:
            found.append((second - first, first))
    return found


def table_counts(text: str, client: str) -> list[tuple[int, int]]:
    """(ok, failed) of every table row that names the client and carries both numbers.

    Columns come from the nearest table header with an "ok"/"success" and a "fail"/"error" column;
    without a header the first two cells after the client name must be plain integers (so rows of
    an evidence table, with times and request ids, give nothing). With a header the first integer
    of a cell is the count, so "0 (inferred: ...)" reads as 0 (another check rejects it).
    """
    found: list[tuple[int, int]] = []
    ok_col = failed_col = None
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            ok_col = failed_col = None
            continue
        cells = table_cells(line)
        lowered = [c.casefold() for c in cells]
        ok_idx = next((i for i, c in enumerate(lowered) if re.search(r"ok|success", c)), None)
        fail_idx = next((i for i, c in enumerate(lowered) if re.search(r"fail|error", c)), None)
        if ok_idx is not None and fail_idx is not None and not any(first_int(c) for c in cells):
            ok_col, failed_col = ok_idx, fail_idx
            continue
        index = next((i for i, c in enumerate(cells) if c.startswith(client)), None)
        if index is None:
            continue
        if ok_col is not None and failed_col is not None:
            pair = cells[ok_col : ok_col + 1], cells[failed_col : failed_col + 1]
            values = [first_int(c[0]) if c else None for c in pair]
        else:
            pair = cells[index + 1 : index + 3]
            values = [first_int(c) if PLAIN_INT.match(c) else None for c in pair]
            values += [None] * (2 - len(values))
        if None not in values:
            found.append((values[0], values[1]))
    return found


def client_candidates(text: str, clients: tuple[str, ...]) -> dict[str, list[tuple[int, int]]]:
    """Every distinct (ok, failed) found for each client, in order of appearance.

    All table rows and all "client N/M" occurrences in the whole text are searched. A client
    without any is mapped to an empty list; more than one entry means conflicting numbers.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    for client in clients:
        found = table_counts(text, client) + pair_counts(text, client)
        result[client] = list(dict.fromkeys(found))
    return result
