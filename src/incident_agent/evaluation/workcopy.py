"""The sanitized working copy of a live run (ADR 0011).

`run-eval` starts `claude` inside a copy of the project that holds only what the agents need, so
that repo-wide Read, Grep and Glob cannot reach the generator, the docs or the answer key. The copy
lives in the system temp directory, outside the repository: Claude Code loads `CLAUDE.md` from the
parent directories of its cwd and a copy inside the repository would load the real one.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

# The only files that are copied, as glob patterns relative to the project root (`*.py` and `*.md`
# never match a directory, `__pycache__` is skipped). tests/test_workcopy.py compares the built
# copy with this table and with a list of forbidden paths.
ALLOWED_GLOBS: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/agents/*.md",
    ".claude/hooks/*.py",
    ".claude/skills/incident-runbook/*.md",
    "demo_app/**/*.py",
    "tests/test_demo_app.py",
)
EMPTY_DIRS: tuple[str, ...] = ("reports",)
GENERATED = "CLAUDE.md"
WORKFLOW_HEADING = "## Incident workflow"
DIR_PREFIX = "incident-eval-"
WORK_NAME = "work"

NEUTRAL_HEADER = """\
# Project instructions

This is a small production system with the services checkout, payment, catalog and auth. You are
its on-call agent. Logs, deployments and stack traces are available through the `incident-logs`
MCP server; the source of the checkout and payment services is in `demo_app/`.

"""


class WorkcopyError(Exception):
    pass


def plan_files(project_root: Path) -> dict[str, Path]:
    """Relative posix name -> source file for everything that is copied verbatim."""
    found: dict[str, Path] = {}
    for pattern in ALLOWED_GLOBS:
        for path in sorted(project_root.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                found[path.relative_to(project_root).as_posix()] = path
    return found


def neutral_claude_md(real_text: str) -> str:
    """Neutral header plus exactly the `## Incident workflow` section of the real CLAUDE.md.

    An allowlist of one section: whatever else the real file says (scenarios, the evaluation) can
    never leak, and there is no second copy to keep in sync.
    """
    lines = real_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == WORKFLOW_HEADING)
    except StopIteration:
        raise WorkcopyError(f"CLAUDE.md has no '{WORKFLOW_HEADING}' section") from None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = "\n".join(lines[start:end]).strip()
    if section == WORKFLOW_HEADING:
        raise WorkcopyError(f"the '{WORKFLOW_HEADING}' section of CLAUDE.md is empty")
    return NEUTRAL_HEADER + section + "\n"


def build_workcopy(project_root: Path, dest: Path) -> list[str]:
    """Copy the allowlist into `dest` (empty or missing), write the neutral CLAUDE.md and create the
    empty `reports/`. Returns the sorted relative names of all files in the copy."""
    if dest.exists() and any(dest.iterdir()):
        raise WorkcopyError(f"working copy target is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    files = plan_files(project_root)
    if not files:
        raise WorkcopyError(f"nothing to copy from {project_root}")
    for name, source in files.items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    claude_md = project_root / GENERATED
    if not claude_md.is_file():
        raise WorkcopyError(f"{claude_md} not found")
    (dest / GENERATED).write_text(
        neutral_claude_md(claude_md.read_text(encoding="utf-8")), encoding="utf-8", newline="\n"
    )
    for name in EMPTY_DIRS:
        (dest / name).mkdir(exist_ok=True)
    return sorted([*files, GENERATED])


def manifest(work: Path, names: list[str]) -> dict[str, str]:
    """Relative name -> sha256 of every file, for the run folder."""
    return {n: hashlib.sha256((work / n).read_bytes()).hexdigest() for n in names}


def default_root() -> Path:
    return Path(tempfile.gettempdir())


def create_workdir(root: Path | None = None) -> Path:
    """A fresh `<root>/incident-eval-xxxx/work`; the parent holds nothing else."""
    base = root if root is not None else default_root()
    base.mkdir(parents=True, exist_ok=True)
    parent = Path(tempfile.mkdtemp(prefix=DIR_PREFIX, dir=base)).resolve()  # resolve: no 8.3 names
    work = parent / WORK_NAME
    work.mkdir()
    return work


def planned_workdir(root: Path | None = None) -> Path:
    """Where a copy would be created (dry run); creates nothing."""
    base = root if root is not None else default_root()
    return base / f"{DIR_PREFIX}<random>" / WORK_NAME


def _clear_readonly_and_retry(func: Callable, path: str, _exc: object) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_workdir(work: Path, out: Callable[[str], None] = print) -> bool:
    """Delete the copy and its parent. A failure (Windows file locks) is a warning, not an error."""
    parent = work.parent
    if not parent.name.startswith(DIR_PREFIX) or work.name != WORK_NAME:
        out(f"warning: not removing {work}: it does not look like a run working copy")
        return False
    try:
        if not parent.exists():
            return True
        shutil.rmtree(parent, onexc=_clear_readonly_and_retry)
        return True
    except OSError as error:
        out(f"warning: could not remove the working copy {parent}: {error}")
        return False
