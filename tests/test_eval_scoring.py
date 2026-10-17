"""The report scorer: checks, ground truth, fixtures and the `score-report` command (in process)."""

import sqlite3
from pathlib import Path

import pytest

from incident_agent.data.database import build_database
from incident_agent.data.scenarios import SCENARIOS
from incident_agent.evaluation.checks import count_statements
from incident_agent.evaluation.cli import main
from incident_agent.evaluation.expected import EXPECTED, GroundTruth, ground_truth
from incident_agent.evaluation.report import default_template
from incident_agent.evaluation.scoring import Score, format_score, score_report

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "eval"
EXAMPLES = ROOT / "docs" / "examples"
EXAMPLE_FILES = {"checkout-500": "incident-report.md", "db-pool": "incident-report-db-pool.md"}
TEMPLATE = default_template().read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def truths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, GroundTruth]:
    result = {}
    for scenario in EXPECTED:
        path = build_database(scenario, 42, tmp_path_factory.mktemp(scenario) / "incident.db")
        with sqlite3.connect(path) as conn:
            result[scenario] = ground_truth(conn, scenario)
    return result


def good(scenario: str) -> str:
    return (FIXTURES / f"good-{scenario}.md").read_text(encoding="utf-8")


def mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"fixture changed, {old!r} not found"
    return text.replace(old, new, 1)


def replace_section(text: str, heading: str, body: str) -> str:
    marker = f"## {heading}\n"
    start = text.index(marker) + len(marker)
    end = text.index("\n## ", start)
    return text[:start] + body + "\n" + text[end:]


def failed_names(score: Score) -> set[str]:
    return {r.name for r in score.failures}


@pytest.fixture
def score(truths: dict[str, GroundTruth]):
    def run(scenario: str, text: str) -> Score:
        return score_report(text, scenario, truths[scenario], TEMPLATE)

    return run


def test_every_scenario_has_an_expected_result_and_a_fixture() -> None:
    # A new scenario must add an ExpectedResult and a good fixture (see docs/evaluation.md).
    assert set(EXPECTED) == set(SCENARIOS)
    for scenario in SCENARIOS:
        assert (FIXTURES / f"good-{scenario}.md").is_file()


def test_ground_truth_matches_scenarios_md(truths: dict[str, GroundTruth]) -> None:
    window = truths["checkout-500"].windows["14:03:00"]
    assert window == {
        "web-2.31.0": (1089, 0),
        "ios-5.8.1": (846, 0),
        "web-2.30.4": (13, 275),
        "android-5.7.9": (7, 158),
    }
    assert truths["checkout-500"].total_failed == 433
    assert truths["db-pool"].total_failed == 555


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_good_fixture_passes_every_check(score, scenario: str) -> None:
    result = score(scenario, good(scenario))
    assert result.passed and not result.warnings, format_score(result)
    assert "report-complete" in {r.name for r in result.results}


def test_historical_examples_are_the_oracle(score) -> None:
    """docs/examples are real runs from before #23: the checkout one still has an inferred count."""
    checkout = score("checkout-500", (EXAMPLES / EXAMPLE_FILES["checkout-500"]).read_text("utf-8"))
    assert failed_names(checkout) == {"no-inferred-counts"}, format_score(checkout)
    db_pool = score("db-pool", (EXAMPLES / EXAMPLE_FILES["db-pool"]).read_text("utf-8"))
    assert db_pool.passed, format_score(db_pool)


def test_severity_must_match_the_expected_level(score) -> None:
    text = mutate(good("db-pool"), "**SEV-3 (Minor).**", "**SEV-2 (Major).**")
    assert failed_names(score("db-pool", text)) == {"severity"}
    text = good("checkout-500").replace("SEV-2", "SEV-5")
    assert "severity" in failed_names(score("checkout-500", text))


def test_root_cause_must_name_the_cause(score) -> None:
    text = mutate(good("checkout-500"), "Payment v1.4.2 reads", "The payment service reads")
    assert failed_names(score("checkout-500", text)) == {"root-cause-named"}
    text = replace_section(good("db-pool"), "Root cause", "The database is slow. Confidence: low.")
    assert "root-cause-named" in failed_names(score("db-pool", text))


def test_red_herring_in_root_cause_fails(score) -> None:
    text = mutate(
        good("db-pool"), "No deployment or code change explains it.", "auth v3.2.0 did it."
    )
    assert "root-cause-named" in failed_names(score("db-pool", text))


def test_red_herring_must_be_ruled_out(score) -> None:
    text = mutate(good("db-pool"), "- **auth v3.2.0 (14:50):**", "- **something else (14:50):**")
    text = text.replace("v3.2.0", "vX")  # also drop the other mentions in Ruled out
    assert "red-herring-ruled-out" in failed_names(score("db-pool", text))


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        ("**Confidence: medium.** Pool exhaustion is certain.", True),
        ("Confidence: **high**. The deploy lines up with the onset.", True),
        ("Confidence: **HIGH**", True),
        ("Confidence: high. Other parts are low, medium risk.", True),  # only the first word counts
        ("Confidence: **medium-high**", False),
        ("Confidence: **high/medium**", False),
        ("Confidence: very high", False),
        ("Confidence: 80%", False),
        ("Certainty: high", False),
    ],
)
def test_confidence_is_the_first_word_after_the_label(score, line: str, ok: bool) -> None:
    text = mutate(
        good("checkout-500"),
        "Confidence: **high**. The deploy lines up with the onset, the stack traces are identical "
        "across the window, only two client versions fail, and the code comment in the repo "
        "describes this exact behaviour. The real request payloads could not be checked.",
        line,
    )
    assert ("confidence" not in failed_names(score("checkout-500", text))) is ok


def test_missing_per_client_row_fails_counts_present(score) -> None:
    text = mutate(good("checkout-500"), "| ios-5.8.1 | 856 | 0 |\n", "")
    assert {"counts-present", "counts-reconciled"} <= failed_names(score("checkout-500", text))


def test_wrong_count_fails_reconciliation_for_checkout(score) -> None:
    text = mutate(good("checkout-500"), "| web-2.30.4 | 13 | 275 |", "| web-2.30.4 | 13 | 270 |")
    result = score("checkout-500", text)
    assert failed_names(result) == {"counts-reconciled"}
    assert "web-2.30.4" in result.check("counts-reconciled").detail


def test_counts_for_the_deployment_window_are_accepted(score) -> None:
    text = mutate(good("checkout-500"), "| web-2.31.0 | 1092 | 0 |", "| web-2.31.0 | 1089 | 0 |")
    text = mutate(text, "| ios-5.8.1 | 856 | 0 |", "| ios-5.8.1 | 846 | 0 |")
    text = mutate(text, "| android-5.7.9 | 8 | 158 |", "| android-5.7.9 | 7 | 158 |")
    assert score("checkout-500", text).passed


def test_db_pool_count_mismatch_is_only_a_warning(score) -> None:
    text = good("db-pool").replace("555", "553")
    result = score("db-pool", text)
    assert result.passed
    assert [w.name for w in result.warnings] == ["counts-reconciled"]
    output = format_score(result)
    assert output.index("Warnings") < output.index("WARN  counts-reconciled")
    assert "FAIL  counts-reconciled" not in output
    assert output.endswith("RESULT: PASS")


@pytest.mark.parametrize(
    "line",
    [
        "This is inferred from the code comment.",
        "the traffic spike as the trigger is hypothesis",
        "The traffic spike as the trigger is inferred from /search volume and latency.",
        "The v1.4.2 deploy at 14:01 is inferred to be the trigger.",
        "| web-2.31.0 | 1092 | 0 |",
    ],
)
def test_legitimate_wording_is_not_an_inferred_count(line: str) -> None:
    assert count_statements(line) == []


@pytest.mark.parametrize(
    "line",
    [
        "web-2.31.0 (0 of 1092) and ios-5.8.1 (0 of 856, inferred) are not affected.",
        "| ios-5.8.1 | 856 | 0 (inferred: 275 + 158 = 433 total KeyErrors) |",
        "- ios-5.8.1 ERROR count was inferred (0), not queried directly.",
        "The failure count is estimated at 400 requests.",
        "About 40 requests failed (estimated).",
    ],
)
def test_count_presented_as_inferred_fails(line: str) -> None:
    assert count_statements(line)


def test_inferred_count_fails_the_report(score) -> None:
    text = mutate(
        good("checkout-500"), "| ios-5.8.1 | 856 | 0 |", "| ios-5.8.1 | 856 | 0 (inferred) |"
    )
    assert failed_names(score("checkout-500", text)) == {"no-inferred-counts"}


def test_missing_and_empty_sections_fail_report_complete(score) -> None:
    text = mutate(good("db-pool"), "## Follow-up tasks", "## Follow-ups")
    result = score("db-pool", text)
    assert "Follow-up tasks" in result.check("report-complete").detail
    assert failed_names(result) == {"report-complete"}
    start = good("db-pool").index("## Open questions")
    empty = good("db-pool")[: start + len("## Open questions")] + "\n"
    assert "empty: Open questions" in score("db-pool", empty).check("report-complete").detail


def test_report_complete_is_skipped_without_a_template(truths: dict[str, GroundTruth]) -> None:
    result = score_report(good("db-pool"), "db-pool", truths["db-pool"])
    assert "report-complete" not in {r.name for r in result.results}


def test_cli_exit_codes_and_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.md"
    report.write_text(good("db-pool"), encoding="utf-8")
    assert main([str(report), "--scenario", "db-pool"]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out

    report.write_text(mutate(good("db-pool"), "SEV-3", "SEV-1"), encoding="utf-8")
    assert main([str(report), "--scenario", "db-pool"]) == 1
    assert "FAIL  severity" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main([str(tmp_path / "missing.md"), "--scenario", "db-pool"])
    with pytest.raises(SystemExit):
        main([str(report), "--scenario", "nope"])
