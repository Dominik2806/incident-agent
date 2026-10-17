"""Relative links in README.md and docs/**/*.md must point to an existing file and heading."""

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
# Inline links and images: [text](target) or [text](target "title"). Inline code is removed first.
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
CODE_SPAN = re.compile(r"`[^`]*`")


def prose_lines(text: str):
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield number, line


def slug(heading: str) -> str:
    """GitHub-style anchor: lowercase, drop punctuation except - and _, spaces to hyphens."""
    text = CODE_SPAN.sub(lambda m: m.group(0)[1:-1], heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    result: set[str] = set()
    for _, line in prose_lines(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def broken_links(path: Path) -> list[str]:
    problems = []
    for number, line in prose_lines(path.read_text(encoding="utf-8")):
        for target in LINK.findall(CODE_SPAN.sub("", line)):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):  # http:, https:, mailto:
                continue
            raw_file, _, fragment = target.partition("#")
            file = (path.parent / unquote(raw_file)).resolve() if raw_file else path
            where = f"{path.relative_to(ROOT)}:{number}: {target}"
            if not file.exists():
                problems.append(f"{where} (file not found)")
            elif fragment and file.suffix == ".md":
                if unquote(fragment).lower() not in anchors(file):
                    problems.append(f"{where} (no such heading)")
    return problems


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(path: Path) -> None:
    assert broken_links(path) == []


def test_slug_matches_github() -> None:
    assert slug("Limitations") == "limitations"
    assert slug("`block_destructive.py`") == "block_destructivepy"
    assert slug("Timeline (UTC)") == "timeline-utc"
    assert slug("Phase 8: Docs and release") == "phase-8-docs-and-release"


def test_docs_are_collected() -> None:
    names = {p.name for p in DOCS}
    assert {"README.md", "architecture.md", "ROADMAP.md"} <= names
