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


# "Confidence: high", "**Confidence: high.**", "Confidence: **medium**": the label of the confidence
# statement. The level word keeps an ordinary "with high confidence" in a sentence from matching.
CONFIDENCE_LABEL = re.compile(r"\bconfidence\b[\s:*_`]*(?:high|medium|low)\b", re.IGNORECASE)
LABEL_MARKUP = " \t*_`"
BLANK_LINE = re.compile(r"\n[ \t]*\n")


def split_confidence(section: str) -> tuple[str, str]:
    """Split a Root cause section into (cause statement, confidence statement).

    The confidence statement starts at the first confidence label that begins the section, a line
    or a sentence, and runs to the end of that paragraph, plus the directly following paragraphs
    that start with "Reason". Everything else, including the text before the label in the same
    paragraph and any later paragraph, is the cause statement. Without a label the whole section
    is the cause statement.
    """
    for match in CONFIDENCE_LABEL.finditer(section):
        before = section[: match.start()].rstrip(LABEL_MARKUP)
        if before and not before.endswith(("\n", ".", "!", "?")):
            continue
        end = len(section)
        for blank in BLANK_LINE.finditer(section, match.end()):
            following = section[blank.end() :].lstrip(LABEL_MARKUP + ">-")
            if not following.casefold().startswith("reason"):
                end = blank.start()
                break
        return before + "\n" + section[end:], section[len(before) : end].lstrip()
    return section, ""


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
FAILED_WORD = r"fail(?:ed|ures)?"
PLAIN_INT = re.compile(r"^\d[\d,]*(?![\d:.])")


@dataclass(frozen=True)
class PairHit:
    """One "<client> N ... M" occurrence. kind: "ok-failed" and "failed-ok" are explicit pairs
    ("N ok / M failed", "N failed / M ok"); "bare" is a plain "N / M"."""

    client: str
    line_start: int
    start: int
    end: int
    kind: str
    first: int
    second: int


def pair_hits(text: str, clients: tuple[str, ...]) -> list[PairHit]:
    """Every "<client> N/M" (bare or with an explicit ok/failed label on the pair), by position."""
    hits = []
    for client in clients:
        pattern = re.compile(
            re.escape(client)
            + rf"(?![\w-]|\.\d){PAIR_GAP}({NUMBER})"
            + rf"(?:\s*ok\s*/\s*(?P<okf>{NUMBER})\s*{FAILED_WORD}\b"
            + rf"|\s*{FAILED_WORD}\s*/\s*(?P<fok>{NUMBER})\s*ok\b"
            + rf"|\s*/\s*(?P<bare>{NUMBER})(?![\d/]))"
        )
        for match in pattern.finditer(text):
            group = next(g for g in ("okf", "fok", "bare") if match.group(g) is not None)
            kind = {"okf": "ok-failed", "fok": "failed-ok"}.get(group, group)
            first, second = (int(g.replace(",", "")) for g in (match.group(1), match.group(group)))
            line_start = text.rfind("\n", 0, match.start()) + 1
            hits.append(
                PairHit(client, line_start, match.start(), match.end(), kind, first, second)
            )
    return sorted(hits, key=lambda h: h.start)


def resolve_pairs(
    text: str, clients: tuple[str, ...]
) -> tuple[dict[str, list[tuple[int, int]]], list[str]]:
    """(ok, failed) per client, and the lines that declare conflicting orders.

    An explicit pair ("N ok / M failed", "N failed / M ok") is always read as written. It also
    declares its order for the bare "N / M" pairs that follow it on the same line, as does an
    earlier "(ok / failed)" label. Otherwise N is failed out of M total, so ok = M - N, and N
    greater than M is not a valid pair. A line with explicit pairs of both orders (the label counts
    as ok-failed) is unreadable: it gives no counts and is returned as a snippet. An optional
    percentage is ignored.
    """
    found: dict[str, list[tuple[int, int]]] = {c: [] for c in clients}
    conflicts: list[str] = []
    lines: dict[int, list[PairHit]] = {}
    for hit in pair_hits(text, clients):
        lines.setdefault(hit.line_start, []).append(hit)
    for line_start, hits in lines.items():
        line_end = text.find("\n", line_start)
        line_end = len(text) if line_end < 0 else line_end
        kinds = {h.kind for h in hits if h.kind != "bare"}
        if kinds == {"ok-failed", "failed-ok"} or (
            "failed-ok" in kinds and OK_FAILED_LABEL.search(text, line_start, line_end)
        ):
            conflicts.append(text[line_start:line_end].strip()[:120])
            continue
        for hit in hits:
            declared = next((h.kind for h in hits if h.kind != "bare" and h.end <= hit.start), None)
            if declared is None and OK_FAILED_LABEL.search(text, line_start, hit.start):
                declared = "ok-failed"
            if hit.kind == "failed-ok" or (hit.kind == "bare" and declared == "failed-ok"):
                found[hit.client].append((hit.second, hit.first))
            elif hit.kind == "ok-failed" or declared == "ok-failed":
                found[hit.client].append((hit.first, hit.second))
            elif hit.first <= hit.second:
                found[hit.client].append((hit.second - hit.first, hit.first))
    return found, conflicts


def pair_counts(text: str, client: str, clients: tuple[str, ...] = ()) -> list[tuple[int, int]]:
    """(ok, failed) for every "<client> N/M" in the text; see `resolve_pairs`."""
    return resolve_pairs(text, tuple(dict.fromkeys((client, *clients))))[0][client]


def conflicting_order_lines(text: str, clients: tuple[str, ...]) -> list[str]:
    """Lines (as snippets) whose explicit pairs declare conflicting ok/failed orders."""
    return resolve_pairs(text, clients)[1]


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
    pairs = resolve_pairs(text, clients)[0]
    return {c: list(dict.fromkeys(table_counts(text, c) + pairs[c])) for c in clients}
