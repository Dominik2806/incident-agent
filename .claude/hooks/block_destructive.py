"""PreToolUse hook (matcher: Bash): block destructive shell commands with exit code 2.

The rules are plain regular expressions applied (case-insensitive) to the whole command string.
This is a safety net, not a security boundary: obfuscated commands get through.

Known false positives (accepted, the safe direction):
- The patterns also match inside quoted strings, heredocs and commit messages, for example
  ``echo "DROP TABLE users"`` or ``git commit -m "remove rm -rf"`` are blocked.
- ``truncate`` is blocked even when it is the Unix ``truncate -s 0 file`` tool.
- ``git clean -fn`` (force plus dry run) and ``git push origin +branch`` (force refspec) are
  blocked.
- ``docker run --rm`` is not affected; ``git rm -rf`` is blocked like ``rm -rf``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import _common

# A command segment ends at ; & | or a newline. [SEG]* stays inside the current segment.
SEG = r"[^;&|\n]*"
# Word boundary that does not treat "-" as a boundary (so "--rm" or "rm-foo" do not match "rm").
START = r"(?<![\w-])"
END = r"(?![\w-])"


class Rule(NamedTuple):
    id: str
    pattern: str
    message: str


RULES: list[Rule] = [
    Rule(
        "rm-recursive-force",
        rf"{START}rm{END}(?={SEG}\s(?:-[a-z]*r[a-z]*|--recursive){END})"
        rf"(?={SEG}\s(?:-[a-z]*f[a-z]*|--force){END})",
        "rm with recursive and force flags deletes whole trees without confirmation. "
        "Delete specific files by name, or ask the human to run the deletion.",
    ),
    Rule(
        "powershell-remove-recurse",
        # "-r" alone is only recursive for Remove-Item/ri; "rm -r" is a plain bash flag.
        rf"{START}(?:remove-item|ri){END}{SEG}\s-(?:r|rec\w*)(?=[\s:]|$)"
        rf"|{START}(?:rm|del|erase|rd|rmdir){END}{SEG}\s-rec\w*(?=[\s:]|$)",
        "Remove-Item -Recurse deletes whole trees. Remove specific files by name, "
        "or ask the human to run the deletion.",
    ),
    Rule(
        "cmd-rd-s",
        rf"{START}(?:rd|rmdir){END}{SEG}\s/s\b",
        "rd /s removes a directory tree. Remove specific files instead, or ask the human.",
    ),
    Rule(
        "cmd-del-s",
        rf"{START}(?:del|erase){END}{SEG}\s/s\b",
        "del /s deletes matching files in all subdirectories. Delete specific files, "
        "or ask the human.",
    ),
    Rule(
        "sql-drop",
        r"\bdrop\s+(?:table|database|schema|index)\b",
        "DROP destroys schema objects and their data. The incident tools are read-only; "
        "propose the statement in your answer and let the human run it.",
    ),
    Rule(
        "sql-delete-from",
        r"\bdelete\s+from\b",
        "DELETE FROM removes rows irreversibly. Propose the statement (with a WHERE clause "
        "and a backup note) in your answer and let the human run it.",
    ),
    Rule(
        "sql-truncate",
        r"\btruncate\b",
        "TRUNCATE empties a table or file irreversibly. Propose it in your answer and let the "
        "human run it.",
    ),
    Rule(
        "git-push-force",
        rf"{START}git{END}{SEG}\bpush\b{SEG}\s(?:--force\S*|-[a-z]*f[a-z]*|\+\S)(?=\s|$)",
        "A forced git push can overwrite remote history. Use a normal push, or ask the human "
        "to force-push after reviewing.",
    ),
    Rule(
        "git-reset-hard",
        rf"{START}git{END}{SEG}\breset\b{SEG}\s--hard\b",
        "git reset --hard discards uncommitted work. Use git stash or git reset --soft, "
        "or ask the human.",
    ),
    Rule(
        "git-clean-force",
        rf"{START}git{END}{SEG}\bclean\b{SEG}\s(?:--force|-[a-z]*f[a-z]*)(?=\s|$)",
        "git clean -f deletes untracked files for good. Run git clean -n (dry run) and ask "
        "the human to confirm.",
    ),
]

COMPILED = [(rule, re.compile(rule.pattern, re.IGNORECASE)) for rule in RULES]


def check(command: str) -> Rule | None:
    for rule, regex in COMPILED:
        if regex.search(command):
            return rule
    return None


def main() -> None:
    try:
        payload = _common.read_payload()
    except Exception as exc:
        _common.fail_closed("block_destructive", f"stdin is not a valid JSON object ({exc})")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        _common.fail_closed("block_destructive", "tool_input is missing")
    command = tool_input.get("command")
    if not isinstance(command, str):
        _common.fail_closed("block_destructive", "tool_input.command is missing")
    rule = check(command)
    if rule is not None:
        shown = _common.ascii_text(_common.truncate(command, 200))
        _common.block(
            rule.id,
            f"BLOCKED by block_destructive (rule {rule.id}): {shown}\n"
            f"Why: {rule.message}\n"
            "Do not work around this hook (no rewording, no other shell). "
            "Tell the user what you wanted to run and why, and let them decide.",
            payload,
        )


if __name__ == "__main__":
    main()
