"""The sanitized working copy of a live run (ADR 0011): content, canary and the contamination scan.

The copy is always built from the REAL project root, never from a fixture, so a new file that leaks
into the allowlist or a new mention of an answer in an allowed file fails here.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from incident_agent.evaluation import runplan, workcopy
from incident_agent.evaluation.expected import EXPECTED
from incident_agent.evaluation.workcopy import WorkcopyError, neutral_claude_md

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
SKILL = ".claude/skills/incident-runbook"


@pytest.fixture(scope="module")
def real_copy(tmp_path_factory) -> tuple[Path, list[str]]:
    work = tmp_path_factory.mktemp("real") / "work"
    return work, workcopy.build_workcopy(ROOT, work)


def files_in(work: Path) -> set[str]:
    return {p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()}


# --- what the copy contains ------------------------------------------------------------------


def test_copy_contains_exactly_the_allowed_files(real_copy):
    work, names = real_copy
    on_disk = files_in(work)
    assert on_disk == set(names)
    # written down independently of ALLOWED_GLOBS
    allowed_dirs = (
        ".claude/agents/",
        ".claude/hooks/",
        f"{SKILL}/",
        "demo_app/",
    )
    for name in on_disk:
        assert name in {".claude/settings.json", "tests/test_demo_app.py", "CLAUDE.md"} or (
            name.startswith(allowed_dirs) and name.endswith((".py", ".md"))
        ), name
    assert {
        ".claude/agents/triage.md",
        ".claude/agents/root-cause.md",
        ".claude/agents/fix-proposer.md",
        f"{SKILL}/SKILL.md",
        f"{SKILL}/severity.md",
        f"{SKILL}/template.md",
        ".claude/hooks/_common.py",
        ".claude/hooks/block_destructive.py",
        ".claude/hooks/check_report.py",
        "demo_app/payment/charge.py",
        "demo_app/checkout/server.py",
        "tests/test_demo_app.py",
    } <= on_disk
    assert not [n for n in on_disk if "__pycache__" in n or n.endswith((".pyc", ".db"))]
    assert (work / "reports").is_dir() and not list((work / "reports").iterdir())
    assert (work / ".claude" / "settings.json").read_bytes() == (
        ROOT / ".claude" / "settings.json"
    ).read_bytes()


FORBIDDEN = (
    "docs",
    "src",
    "evals",
    "data",
    "logs",
    ".git",
    ".github",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    ".mcp.json",
    ".env.example",
    "tests/fixtures",
    "tests/conftest.py",
    "tests/test_demo_frames.py",
    "tests/test_eval_runner.py",
    "tests/test_generator.py",
    ".claude/settings.local.json",
)


def test_copy_contains_none_of_the_forbidden_paths(real_copy):
    work, _ = real_copy
    for path in FORBIDDEN:
        assert not (work / path).exists(), path
    assert [p.name for p in (work / "tests").iterdir()] == ["test_demo_app.py"]
    # the check is meaningful: the real repo does have the answer key
    for path in ("docs", "src", "tests/fixtures", "README.md", "tests/test_demo_frames.py"):
        assert (ROOT / path).exists(), path
    assert not [n for n in files_in(work) if n.split("/")[0] in {"docs", "src", "evals"}]


def test_demo_app_tests_in_the_copy_do_not_know_the_generator():
    text = (ROOT / "tests" / "test_demo_app.py").read_text(encoding="utf-8")
    assert "incident_agent" not in text and "conn" not in text and "generator" not in text


# --- the neutral CLAUDE.md -------------------------------------------------------------------


def test_claude_md_is_header_plus_the_incident_workflow_section(real_copy):
    work, _ = real_copy
    text = (work / "CLAUDE.md").read_text(encoding="utf-8")
    real = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith(workcopy.NEUTRAL_HEADER)
    section = text[len(workcopy.NEUTRAL_HEADER) :]
    assert section.startswith("## Incident workflow") and section.strip() in real
    assert "incident-runbook" in section and "never apply fixes" in section.lower()
    assert not re.search(r"^## (?!Incident workflow)", text, re.MULTILINE)


def test_claude_md_build_stops_at_the_next_heading_and_needs_the_section():
    built = neutral_claude_md(
        "# T\n\n## Incident workflow\nuse the skill\n\n## Conventions\nSECRET\n"
    )
    assert "use the skill" in built and "SECRET" not in built and "Conventions" not in built
    with pytest.raises(WorkcopyError):
        neutral_claude_md("# T\n\n## Conventions\nSECRET\n")
    with pytest.raises(WorkcopyError):
        neutral_claude_md("# T\n\n## Incident workflow\n\n## Conventions\nx\n")


def test_build_refuses_a_non_empty_target(tmp_path):
    (tmp_path / "x").write_text("x", encoding="utf-8")
    with pytest.raises(WorkcopyError):
        workcopy.build_workcopy(ROOT, tmp_path)


# --- the canary ------------------------------------------------------------------------------

SCENARIO_IDS = sorted(EXPECTED)
RED_HERRING_VERSIONS = ("2.8.0", "3.1.1", "3.2.0", "3.3.0", "2.7.1")
ANSWER_WORDS = (
    "Payvia",
    "SSLCertVerificationError",
    "notAfter",
    "db_pool",
    "pool_size",
    "provider_client",
    "payment_client",
    "_post_with_retries",
    "red herring",
    "red-herring",
    "ExpectedResult",
    "ground_truth",
    "cause_patterns",
    "LEGACY_CLIENT_VERSIONS",
    "KEYERROR_FRAMES",
    "NOISE_SKIP_ONE_OFFS",
)
GENERATOR_WORDS = ("scenarios.py", "incident_agent", "generator", "frames.py", "src/incident_agent")
CANARIES = (*SCENARIO_IDS, *RED_HERRING_VERSIONS, *ANSWER_WORDS, *GENERATOR_WORDS)
# Known and accepted: the skill's worked example is about checkout-500 (the skill is not changed by
# this work; see ADR 0011). Pinned by file, word and exact count so that no new mention can hide.
KNOWN_MENTIONS = {
    (f"{SKILL}/severity.md", "checkout-500"): 1,
    (f"{SKILL}/SKILL.md", "checkout-500"): 1,
}


def canary_hits(copy: Path, words=CANARIES, known=None) -> list[str]:
    known = KNOWN_MENTIONS if known is None else known
    hits = []
    for path in sorted(p for p in copy.rglob("*") if p.is_file()):
        rel = path.relative_to(copy).as_posix()
        text = path.read_bytes().decode("utf-8", errors="ignore").casefold()
        for word in words:
            found = text.count(word.casefold())
            expected = known.get((rel, word), 0)
            if found != expected:
                hits.append(f"{rel}: '{word}' x{found} (expected {expected})")
    for (rel, word), count in known.items():
        if not (copy / rel).is_file():
            hits.append(f"{rel}: pinned file is missing ({word} x{count})")
    return hits


def test_canary_words_cover_every_red_herring_of_the_registry():
    versions = set()
    for expected in EXPECTED.values():
        for pattern in (*expected.forbidden_cause_patterns, *expected.red_herring_patterns):
            versions |= {v.replace("\\", "") for v in re.findall(r"\d+\\?\.\d+\\?\.\d+", pattern)}
    assert versions and versions <= set(RED_HERRING_VERSIONS)
    assert SCENARIO_IDS == ["api-outage", "cert-expired", "checkout-500", "db-pool"]


def test_canary_finds_nothing_in_the_copy_built_from_the_real_repo(real_copy):
    work, _ = real_copy
    assert canary_hits(work) == []


@pytest.fixture
def planted(real_copy, tmp_path) -> Path:
    copy = tmp_path / "planted"
    shutil.copytree(real_copy[0], copy)
    return copy


@pytest.mark.parametrize(
    "word",
    [
        "db-pool",  # a scenario id
        "cert-expired",
        "3.2.0",  # a red-herring version
        "2.7.1",
        "Payvia",  # an answer word
        "db_pool",
        "scenarios.py",  # the generator
        "incident_agent",
        "generator",
        "ExpectedResult",
    ],
)
def test_negative_control_a_planted_canary_word_fails_the_check(planted, word):
    target = planted / ".claude" / "agents" / "triage.md"
    target.write_text(target.read_text(encoding="utf-8") + f"\nnote: {word.upper()}\n", "utf-8")
    hits = canary_hits(planted)
    assert hits and any(f"'{word}'" in hit and "triage.md" in hit for hit in hits)


def test_negative_control_the_pinned_skill_mentions_cannot_go_stale_or_grow(planted):
    severity = planted / SKILL / "severity.md"
    text = severity.read_text(encoding="utf-8")
    severity.write_text(text + "\nsee also checkout-500\n", encoding="utf-8")
    assert any("severity.md: 'checkout-500' x2 (expected 1)" in h for h in canary_hits(planted))
    severity.write_text(text.replace("checkout-500", "the checkout"), encoding="utf-8")
    assert any("severity.md: 'checkout-500' x0 (expected 1)" in h for h in canary_hits(planted))
    (planted / "docs").mkdir()
    (planted / "docs" / "scenarios.md").write_text("db-pool", encoding="utf-8")
    assert any(h.startswith("docs/scenarios.md") for h in canary_hits(planted))


# --- the contamination scan, on lines written by the real audit hook -------------------------

STYLES = {
    "posix": ("/tmp/sandbox/work", "/home/someone/repo", "/"),
    "windows": ("C:\\sandbox\\work", "C:\\Users\\someone\\repo", "\\"),
}


@pytest.fixture(params=sorted(STYLES))
def style(request):
    work, elsewhere, sep = STYLES[request.param]

    def inside(rel: str) -> str:
        return work + sep + rel.replace("/", sep)

    def outside(rel: str) -> str:
        return elsewhere + sep + rel.replace("/", sep)

    return work, inside, outside


def audit_text(tmp_path: Path, calls: list[tuple[str, dict]]) -> str:
    """Run the real audit hook once per call (a PostToolUse payload) and return its log."""
    log = tmp_path / "audit.jsonl"
    log.unlink(missing_ok=True)
    for tool, tool_input in calls:
        payload = {
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "tool_response": {"content": "..."},
        }
        subprocess.run(
            [sys.executable, str(HOOKS / "audit_log.py")],
            input=json.dumps(payload).encode(),
            env={"INCIDENT_AUDIT_LOG": str(log), "PATH": ""},
            check=True,
            capture_output=True,
        )
    return log.read_text(encoding="utf-8")


def scan(tmp_path, work, *calls: tuple[str, dict]) -> tuple[str, ...]:
    return runplan.scan_audit(audit_text(tmp_path, list(calls)), work)


def test_audit_line_shape_is_what_the_scan_reads(tmp_path):
    text = audit_text(tmp_path, [("Grep", {"pattern": "x", "path": "p", "glob": "*.py"})])
    (record,) = [json.loads(line) for line in text.splitlines()]
    assert set(record) == {"ts", "session_id", "tool_name", "input", "result"}
    assert record["input"] == {"pattern": "x", "path": "p", "glob": "*.py"}
    assert record["result"]["status"] == "ok"


def test_grep_and_glob_fields_are_recorded_untruncated(tmp_path):
    glob = "src/" + "a" * 246  # 250 characters
    assert len(glob) == 250
    text = audit_text(
        tmp_path,
        [
            ("Grep", {"pattern": "x", "path": "p" * 250, "glob": glob}),
            ("Glob", {"pattern": glob, "path": "q" * 250}),
            ("Read", {"file_path": "r" * 250}),
        ],
    )
    grep, glob_call, read = (json.loads(line)["input"] for line in text.splitlines())
    assert grep["path"] == "p" * 250 and grep["glob"] == glob
    assert glob_call == {"pattern": glob, "path": "q" * 250}
    assert read["file_path"] == "r" * 250
    assert "...[+" not in text


def test_long_values_keep_their_first_300_characters_and_are_scanned_on_that_prefix(
    tmp_path, style
):
    work, inside, outside = style
    far = outside("x") + "y" * 400
    text = audit_text(tmp_path, [("Grep", {"pattern": "a", "glob": far})])
    shown = json.loads(text)["input"]["glob"]
    assert shown.startswith(far[:300]) and shown.endswith("...[+" + str(len(far) - 300) + " chars]")
    assert len(runplan.scan_audit(text, work)) == 1
    near = inside("demo_app/") + "y" * 400
    assert scan(tmp_path, work, ("Read", {"file_path": near})) == ()


@pytest.mark.parametrize(
    ("tool", "field", "value", "reason"),
    [
        ("Read", "file_path", "OUT:docs/scenarios.md", "outside the working copy"),
        ("Read", "file_path", "../outside.md", "outside the working copy"),
        ("Read", "file_path", "demo_app/../../outside.md", "outside the working copy"),
        ("Read", "file_path", "src/incident_agent/data/scenarios.py", "answer-key location (src/)"),
        ("Read", "file_path", "docs/scenarios.md", "answer-key location (docs/)"),
        ("Read", "file_path", "IN:docs/examples/report.md", "answer-key location (docs/)"),
        ("Read", "file_path", "tests/fixtures/eval/good.md", "(tests/fixtures/)"),
        ("Read", "file_path", "IN:tests/fixtures", "(tests/fixtures/)"),
        ("Read", "file_path", "evals/results/a/audit.jsonl", "answer-key location (evals/)"),
        ("Read", "file_path", "WORK2:file.md", "outside the working copy"),
        ("Grep", "path", "OUT:src", "outside the working copy"),
        ("Grep", "path", "..", "outside the working copy"),
        ("Grep", "path", "IN:src", "answer-key location (src/)"),
        ("Grep", "path", "src", "answer-key location (src/)"),
        ("Grep", "glob", "OUT:**/*.py", "outside the working copy"),
        ("Grep", "glob", "../docs/**", "outside the working copy"),
        ("Glob", "path", "OUT:docs", "outside the working copy"),
        ("Glob", "pattern", "OUT:docs/*.md", "outside the working copy"),
        ("Glob", "pattern", "../outside/*.txt", "outside the working copy"),
        ("Glob", "pattern", "IN:src/**/*.py", "answer-key location (src/)"),
    ],
)
def test_scan_flags_places_outside_the_copy_and_the_denylist(
    tmp_path, style, tool, field, value, reason
):
    work, inside, outside = style
    sep = "\\" if "\\" in work else "/"
    if value.startswith("OUT:"):
        value = outside(value[4:])
    elif value.startswith("IN:"):
        value = inside(value[3:])
    elif value.startswith("WORK2:"):
        value = work + "2" + sep + value[6:]  # same prefix, another directory
    elif sep == "\\":
        value = value.replace("/", "\\")
    tool_input = {"pattern": "x", field: value} if tool == "Grep" else {field: value}
    if tool == "Glob" and field != "pattern":
        tool_input["pattern"] = "**/*"
    (hit,) = scan(tmp_path, work, (tool, tool_input))
    assert hit.startswith(f"{tool} {field}=") and reason in hit


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "demo_app/payment/charge.py"}),
        ("Read", {"file_path": "IN:demo_app/payment/charge.py"}),
        ("Read", {"file_path": ".claude/skills/incident-runbook/severity.md"}),
        ("Read", {"file_path": "IN:.claude/agents/triage.md"}),
        ("Read", {"file_path": "tests/test_demo_app.py"}),
        ("Read", {"file_path": "IN:CLAUDE.md"}),
        ("Read", {"file_path": "reports/2026-03-12-x.md"}),
        ("Read", {"file_path": "demo_app/../.claude/settings.json"}),
        ("Read", {"file_path": "IN:tests/test_demo_app.py", "offset": 1, "limit": 95}),
        ("Grep", {"pattern": "currency", "output_mode": "content"}),
        ("Grep", {"pattern": "currency", "path": "IN:"}),
        ("Grep", {"pattern": "currency", "path": "demo_app"}),
        ("Grep", {"pattern": "currency", "path": "IN:demo_app"}),
        ("Grep", {"pattern": "x", "glob": "!{docs,tests,.git}/**", "head_limit": 30}),
        ("Grep", {"pattern": "x", "glob": "*.py", "-i": True}),
        ("Grep", {"pattern": "x", "glob": "demo_app/**"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"pattern": "demo_app/**/*", "path": "IN:"}),
        ("Glob", {"pattern": "**/{search,db_pool}.py"}),
        ("Glob", {"pattern": "reports/*"}),
        ("Bash", {"command": "cat OUT:x"}),
        ("Write", {"file_path": "OUT:x.md", "content": "x"}),
        ("Agent", {"subagent_type": "triage", "prompt": "OUT:x"}),
    ],
)
def test_scan_does_not_flag_the_demo_app_the_claude_folder_or_other_tools(
    tmp_path, style, tool, tool_input
):
    work, inside, outside = style
    sep = "\\" if "\\" in work else "/"

    def fix(value):
        if not isinstance(value, str):
            return value
        if "OUT:" in value:
            return value.replace("OUT:", outside("") if value.startswith("OUT:") else "")
        if value.startswith("IN:"):
            return inside(value[3:]) if value[3:] else work
        return value.replace("/", sep) if sep == "\\" and "{" not in value else value

    assert scan(tmp_path, work, (tool, {k: fix(v) for k, v in tool_input.items()})) == ()


def test_scan_handles_windows_case_and_git_bash_drive_prefix(tmp_path):
    work = "C:\\sandbox\\work"
    assert scan(tmp_path, work, ("Read", {"file_path": "c:\\SANDBOX\\Work\\Demo_App\\a.py"})) == ()
    (hit,) = scan(tmp_path, work, ("Read", {"file_path": "/c/sandbox/work/docs/a.md"}))
    assert "answer-key location (docs/)" in hit
    assert scan(tmp_path, work, ("Read", {"file_path": "/c/sandbox/work/demo_app/a.py"})) == ()
    (hit,) = scan(tmp_path, work, ("Read", {"file_path": "D:\\sandbox\\work\\demo_app\\a.py"}))
    assert "outside the working copy" in hit


def test_scan_ignores_garbage_lines_and_reports_every_hit(tmp_path):
    good = audit_text(
        tmp_path,
        [("Read", {"file_path": "docs/a.md"}), ("Grep", {"pattern": "x", "path": "../x"})],
    )
    text = "not json\n[1, 2]\n" + good + "\n"
    hits = runplan.scan_audit(text, "/tmp/sandbox/work")
    assert [h.split(" ")[0] for h in hits] == ["Read", "Grep"]
    assert runplan.scan_audit("", "/tmp/sandbox/work") == ()


def test_parse_result_reads_permission_denials():
    text = json.dumps(
        {
            "type": "result",
            "permission_denials": [
                {"tool_name": "Read", "tool_input": {"file_path": "/x/y.md"}},
                {"tool_name": "Grep", "tool_input": {"pattern": "a", "path": "/x"}},
                {"tool_name": "Glob", "tool_input": {"pattern": "/x/*.txt"}},
            ],
        }
    )
    assert runplan.parse_result(text).denials == ("Read /x/y.md", "Grep /x", "Glob /x/*.txt")
    assert runplan.parse_result(json.dumps({"permission_denials": "oops"})).denials == ()
    assert runplan.parse_result("{}").denials == ()


# --- the working directory is created and removed safely --------------------------------------


def test_workdir_lives_under_the_given_root_and_is_removed_with_its_parent(tmp_path):
    work = workcopy.create_workdir(tmp_path / "root")
    assert work.name == "work" and work.parent.name.startswith("incident-eval-")
    assert work.parent.parent == (tmp_path / "root").resolve()
    (work / "reports").mkdir()
    (work / "reports" / "ro.md").write_text("x", encoding="utf-8")
    (work / "reports" / "ro.md").chmod(0o444)
    assert workcopy.remove_workdir(work) is True
    assert not work.parent.exists()


def test_remove_workdir_refuses_a_path_that_is_not_a_run_copy(tmp_path, capsys):
    victim = tmp_path / "precious" / "work"
    victim.mkdir(parents=True)
    messages: list[str] = []
    assert workcopy.remove_workdir(victim, messages.append) is False
    assert victim.exists() and "does not look like a run working copy" in messages[0]
