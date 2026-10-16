"""PreToolUse hook (matcher: Edit|Write|MultiEdit|NotebookEdit): protect sensitive files.

Blocks (exit code 2) any change to:
- ``.env`` and ``.env.*`` (but ``.env.example`` is allowed)
- ``*.db``, ``*.sqlite``, ``*.sqlite3`` files and everything under the project's ``data/`` folder
- everything under ``.github/workflows/``

Paths are normalized first: resolved against the project dir, backslashes converted, ``..`` and
``.`` collapsed, case folded. Limits: symlinks are not resolved and writes through the shell
(redirects, ``sed -i``, ``mv``) are not seen by this hook.
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import PurePath

import _common

PATH_KEYS = ("file_path", "notebook_path", "path")
DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")

HUMAN_NOTE = (
    "Do not work around this hook. Tell the user which change you wanted to make and ask them "
    "to make it manually."
)


def normalize(raw: str) -> str:
    """Return a casefolded, absolute-looking, forward-slash path with ``..`` resolved."""
    path = raw.replace("\\", "/")
    if os.name == "nt":
        # Git Bash style drive prefix (/c/...) -> c:/...
        path = re.sub(r"^/([A-Za-z])(?=/|$)", r"\1:", path)
    return posixpath.normpath(path).rstrip(" .").casefold() if path else ""


def is_absolute(path: str) -> bool:
    return path.startswith("/") or re.match(r"^[A-Za-z]:/", path) is not None


def resolve(raw: str, project: str) -> str:
    path = raw.replace("\\", "/")
    if not is_absolute(path):
        path = f"{project}/{path}"
    return normalize(path)


def relative_to_project(path: str, project: str) -> str | None:
    if path == project:
        return ""
    if path.startswith(project + "/"):
        return path[len(project) + 1 :]
    return None


def check(raw: str, project_dir: PurePath | str) -> tuple[str, str] | None:
    """Return (rule id, description) when the path is protected, else None."""
    project = normalize(str(project_dir))
    path = resolve(raw, project)
    rel = relative_to_project(path, project)
    base = posixpath.basename(path)
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return "env-file", "environment files (.env, .env.*) may hold secrets"
    if base.endswith(DB_SUFFIXES):
        return "db-file", "database files (*.db, *.sqlite) hold the sample or production data"
    if rel is not None:
        if rel == "data" or rel.startswith("data/"):
            return "data-dir", "the data/ folder holds the generated incident database"
        if rel.startswith(".github/workflows/"):
            return (
                "github-workflows",
                "CI workflows under .github/workflows/ are maintained by hand",
            )
    return None


def main() -> None:
    try:
        payload = _common.read_payload()
    except Exception as exc:
        _common.fail_closed("protect_files", f"stdin is not a valid JSON object ({exc})")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        _common.fail_closed("protect_files", "tool_input is missing")
    paths = [tool_input[k] for k in PATH_KEYS if isinstance(tool_input.get(k), str)]
    paths = [p for p in paths if p]
    if not paths:
        _common.fail_closed("protect_files", "tool_input has no file path")
    project = _common.project_dir()
    for raw in paths:
        hit = check(raw, project)
        if hit is not None:
            rule_id, why = hit
            shown = _common.ascii_text(_common.truncate(raw, 200))
            _common.block(
                rule_id,
                f"BLOCKED by protect_files (rule {rule_id}): {shown}\nWhy: {why}.\n{HUMAN_NOTE}",
                payload,
            )


if __name__ == "__main__":
    main()
