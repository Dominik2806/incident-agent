"""The report scorer: checks, ground truth, fixtures and the `score-report` command (in process)."""

import sqlite3
from pathlib import Path

import pytest

from incident_agent.data.database import build_database
from incident_agent.data.scenarios import SCENARIOS
from incident_agent.evaluation.checks import count_statements
from incident_agent.evaluation.cli import main
from incident_agent.evaluation.expected import EXPECTED, GroundTruth, ground_truth
from incident_agent.evaluation.report import (
    Report,
    default_template,
    pair_counts,
    pair_hits,
    split_confidence,
    table_counts,
)
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
    assert truths["cert-expired"].total_failed == 1961
    assert truths["cert-expired"].windows == {}
    assert truths["api-outage"].total_failed == 156
    assert truths["api-outage"].windows == {}
    assert truths["memory-leak"].total_failed == 102
    assert truths["memory-leak"].windows == {}


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
        "A wrong default could mean a wrong-currency charge; a 4xx is safer if the currency cannot"
        " be inferred.",
        "A 5xx would be inferred from the gateway status, not from the payload.",
        "The column type int64 is inferred from the sample rows.",
    ],
)
def test_legitimate_wording_is_not_an_inferred_count(line: str) -> None:
    assert count_statements(line) == []


def test_count_next_to_a_status_class_is_still_an_inferred_count() -> None:
    assert count_statements("The 4xx count was inferred (40).")
    assert count_statements("About 1.2k requests failed (estimated), mostly 5xx.")


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


# --- failed/total ("client N/M") count form --------------------------------------------------
PAIR_LINE = (
    "web-2.30.4 275/288 (95.5%), android-5.7.9 158/166 (95.2%), web-2.31.0 0/1092, ios-5.8.1 0/856"
)
TABLE_ROWS = (
    "| web-2.30.4 | 13 | 275 |\n",
    "| android-5.7.9 | 8 | 158 |\n",
    "| web-2.31.0 | 1092 | 0 |\n",
    "| ios-5.8.1 | 856 | 0 |\n",
)


def with_pair_line(line: str) -> str:
    text = good("checkout-500")
    for row in TABLE_ROWS:
        text = mutate(text, row, "")
    return text + f"\n{line}\n"


def test_failed_over_total_line_passes_both_count_checks(score) -> None:
    result = score("checkout-500", with_pair_line(PAIR_LINE))
    assert result.check("counts-present").passed
    assert result.check("counts-reconciled").passed


def test_failed_over_total_wrong_total_fails(score) -> None:
    result = score("checkout-500", with_pair_line(PAIR_LINE.replace("275/288", "275/290")))
    assert result.check("counts-present").passed
    assert not result.check("counts-reconciled").passed


def test_failed_over_total_missing_client_fails(score) -> None:
    result = score("checkout-500", with_pair_line(PAIR_LINE.replace(", ios-5.8.1 0/856", "")))
    assert {"counts-present", "counts-reconciled"} <= failed_names(result)


def test_failed_over_total_is_never_read_as_ok_over_failed(score) -> None:
    result = score("checkout-500", with_pair_line(PAIR_LINE.replace("0/1092", "1092/0")))
    assert not result.check("counts-reconciled").passed


def test_failed_over_total_swapped_numbers_fail(score) -> None:
    result = score("checkout-500", with_pair_line(PAIR_LINE.replace("275/288", "288/275")))
    assert {"counts-present", "counts-reconciled"} <= failed_names(result)


def test_table_form_still_passes_and_db_pool_is_unaffected(score) -> None:
    assert score("checkout-500", good("checkout-500")).passed
    assert score("db-pool", good("db-pool")).passed


# --- every occurrence counts, not only the first --------------------------------------------
def test_real_report_with_failed_total_line_passes_all_checks(score) -> None:
    # A real run: evidence table rows name web-2.30.4 and android-5.7.9 without counts, and the
    # counts come later as "client N/M". Synthetic data, kept unchanged.
    text = (FIXTURES / "real-checkout-500-failed-total.md").read_text(encoding="utf-8")
    result = score("checkout-500", text)
    assert len(result.results) == 8
    assert result.passed and not result.warnings


def test_client_names_in_prose_before_the_counts_line(score) -> None:
    prose = "Legacy clients web-2.30.4 and android-5.7.9 fail; web-2.31.0 and ios-5.8.1 do not.\n"
    result = score("checkout-500", with_pair_line(prose + "\n" + PAIR_LINE))
    assert result.check("counts-present").passed
    assert result.check("counts-reconciled").passed


def test_wrong_number_in_the_counts_line_fails_reconciliation(score) -> None:
    prose = "The legacy clients web-2.30.4 and android-5.7.9 are affected.\n"
    line = PAIR_LINE.replace("158/166", "158/170")
    result = score("checkout-500", with_pair_line(prose + "\n" + line))
    assert result.check("counts-present").passed
    assert not result.check("counts-reconciled").passed


def test_identical_repeats_are_fine_but_differing_repeats_conflict(score) -> None:
    repeated = with_pair_line(PAIR_LINE + "\n\nAgain: web-2.30.4 275/288.")
    assert score("checkout-500", repeated).passed
    conflicting = with_pair_line(PAIR_LINE + "\n\nAgain: web-2.30.4 270/288.")
    result = score("checkout-500", conflicting)
    assert result.check("counts-present").passed
    assert not result.check("counts-reconciled").passed
    assert "conflicting" in result.check("counts-reconciled").detail
    assert "web-2.30.4" in result.check("counts-reconciled").detail


# --- "(ok / failed)" inline list and equivalent wording of the cause --------------------------
OK_FAILED_LINE = (
    "Checkout log lines from 14:00Z by client (ok / failed): web-2.30.4 13 / 275; "
    "android-5.7.9 8 / 158; web-2.31.0 1092 / 0; ios-5.8.1 856 / 0."
)


def test_real_report_with_inline_ok_failed_list_passes(score) -> None:
    # A real run (kept verbatim): counts as an inline "(ok / failed)" list with 0 values, and a Root
    # cause that says "with no default" instead of naming KeyError.
    text = (FIXTURES / "real-checkout-500-ok-failed-inline.md").read_text(encoding="utf-8")
    assert "KeyError" not in text.split("## Root cause")[1].split("## Evidence")[0]
    result = score("checkout-500", text)
    assert len(result.results) == 8
    assert result.passed and not result.warnings, format_score(result)


def test_labeled_list_is_read_as_ok_failed_including_zero(score) -> None:
    result = score("checkout-500", with_pair_line(OK_FAILED_LINE))
    assert result.check("counts-present").passed
    assert result.check("counts-reconciled").passed


def test_prose_with_zero_failures_adds_no_count(score) -> None:
    prose = "web-2.31.0 and ios-5.8.1 had 0 failures in 1,948 lines.\n"
    result = score("checkout-500", with_pair_line(OK_FAILED_LINE + "\n\n" + prose))
    assert result.check("counts-reconciled").passed


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("web-2.31.0 1092 / 0", "web-2.31.0 0 / 1092"),  # swapped ok/failed
        ("web-2.30.4 13 / 275", "web-2.30.4 275 / 13"),  # swapped ok/failed
        ("web-2.30.4 13 / 275", "web-2.30.4 13 / 270"),  # wrong count
    ],
)
def test_labeled_list_wrong_numbers_fail(score, old: str, new: str) -> None:
    result = score("checkout-500", with_pair_line(OK_FAILED_LINE.replace(old, new)))
    assert result.check("counts-present").passed
    assert not result.check("counts-reconciled").passed


def test_labeled_list_missing_client_fails(score) -> None:
    result = score("checkout-500", with_pair_line(OK_FAILED_LINE.replace(" ios-5.8.1 856 / 0", "")))
    assert {"counts-present", "counts-reconciled"} <= failed_names(result)


def test_labeled_list_client_twice_with_different_numbers_conflicts(score) -> None:
    text = with_pair_line(OK_FAILED_LINE + "\n\nLater (ok / failed): web-2.30.4 13 / 270.")
    result = score("checkout-500", text)
    assert not result.check("counts-reconciled").passed
    assert "conflicting" in result.check("counts-reconciled").detail


def test_label_applies_only_to_its_own_line(score) -> None:
    # The label on the first line must not turn the next line's "N/M" into ok/failed.
    text = with_pair_line("Counts (ok / failed) are below.\n" + PAIR_LINE)
    assert score("checkout-500", text).passed  # failed/total reading, as before
    unlabeled = with_pair_line(OK_FAILED_LINE.replace("(ok / failed)", ""))
    assert not score("checkout-500", unlabeled).check("counts-present").passed


def test_cause_may_say_no_default_instead_of_keyerror(score) -> None:
    body = 'Payment v1.4.2 reads `payload["currency"]` without a default. Confidence: **high**.'
    text = replace_section(good("checkout-500"), "Root cause", body)
    assert score("checkout-500", text).passed


@pytest.mark.parametrize(
    "body",
    [
        "Payment v1.4.2 breaks legacy clients on currency. Confidence: **high**.",
        "A KeyError in the service on currency. Confidence: **high**.",  # no payment, no v1.4.2
        "Payment v1.4.2 reads it with no default. Confidence: **high**.",  # no currency
        "Payment reads currency with no default. Confidence: **high**.",  # no v1.4.2
        "catalog v2.8.0 caused it: payment v1.4.2 KeyError currency. Confidence: **high**.",
        "auth v3.1.1 caused it: payment v1.4.2 KeyError currency. Confidence: **high**.",
        "",
    ],
)
def test_cause_still_needs_every_other_term_and_no_red_herring(score, body: str) -> None:
    text = replace_section(good("checkout-500"), "Root cause", body)
    assert "root-cause-named" in failed_names(score("checkout-500", text))


# --- explicit per-pair label: "N ok / M failed" declares the order of the bare pairs after it ----
MIXED_LINE = (
    "web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; web-2.30.4 13 / 275; android-5.7.9 8 / 158."
)
MIXED_FIXTURE = "real-checkout-500-mixed-labels.md"


def mixed(line: str = MIXED_LINE) -> str:
    """The real report with its counts line replaced."""
    return mutate((FIXTURES / MIXED_FIXTURE).read_text(encoding="utf-8"), MIXED_LINE, line)


def test_real_report_with_mixed_explicit_and_bare_pairs_passes(score) -> None:
    # A real run (kept verbatim): "web-2.31.0 1092 ok / 0 failed" followed by bare "N / M" pairs.
    result = score("checkout-500", (FIXTURES / MIXED_FIXTURE).read_text(encoding="utf-8"))
    assert len(result.results) == 8
    assert result.passed and not result.warnings, format_score(result)
    assert result.check("counts-reconciled").detail == "matches the database"


@pytest.mark.parametrize(
    "line",
    [
        MIXED_LINE.replace("failed;", "failures;"),
        MIXED_LINE.replace("1092 ok / 0 failed", "1,092 ok / 0 failed"),
    ],
)
def test_explicit_pair_variants_are_accepted(score, line: str) -> None:
    assert score("checkout-500", mixed(line)).passed


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("1092 ok / 0 failed", "0 ok / 1092 failed"),  # swapped explicit pair
        ("13 / 275", "13 / 270"),  # wrong bare number
        ("8 / 158", "158 / 8"),  # swapped bare number
    ],
)
def test_mixed_line_wrong_numbers_fail(score, old: str, new: str) -> None:
    result = score("checkout-500", mixed(MIXED_LINE.replace(old, new)))
    assert not result.check("counts-reconciled").passed


def test_mixed_line_missing_client_fails(score) -> None:
    result = score("checkout-500", mixed(MIXED_LINE.replace(" android-5.7.9 8 / 158", "")))
    assert {"counts-present", "counts-reconciled"} <= failed_names(result)


def test_mixed_line_client_twice_with_different_numbers_conflicts(score) -> None:
    result = score("checkout-500", mixed(MIXED_LINE + " Again: web-2.30.4 13 / 270."))
    assert not result.check("counts-reconciled").passed
    assert "conflicting" in result.check("counts-reconciled").detail


def test_bare_pairs_on_a_line_without_an_explicit_label_stay_failed_over_total(score) -> None:
    result = score("checkout-500", mixed(MIXED_LINE.replace("1092 ok / 0 failed", "1092 / 0")))
    assert not result.check("counts-present").passed  # "1092 / 0" is failed 1092 of total 0
    assert "web-2.31.0" in result.check("counts-present").detail


def test_explicit_label_on_another_line_does_not_apply(score) -> None:
    rest = MIXED_LINE.replace("web-2.31.0 1092 ok / 0 failed; ", "")
    result = score("checkout-500", mixed("web-2.31.0 1092 ok / 0 failed.\n- " + rest))
    assert not result.check("counts-present").passed
    assert "ios-5.8.1" in result.check("counts-present").detail


def test_bare_pair_before_the_explicit_pair_is_not_affected(score) -> None:
    before = MIXED_LINE.replace("ios-5.8.1 856 / 0; ", "").replace(
        "web-2.31.0", "ios-5.8.1 0/856; web-2.31.0"
    )
    assert score("checkout-500", mixed(before)).passed
    dropped = before.replace("ios-5.8.1 0/856", "ios-5.8.1 856 / 0")
    assert not score("checkout-500", mixed(dropped)).check("counts-present").passed


def test_explicit_pairs_of_both_orders_on_one_line_are_unreadable(score) -> None:
    line = (
        "web-2.31.0 1092 ok / 0 failed; ios-5.8.1 0 failed / 856 ok; web-2.30.4 13 / 275; "
        "android-5.7.9 8 / 158."
    )
    result = score("checkout-500", mixed(line))
    for name in ("counts-present", "counts-reconciled"):
        assert not result.check(name).passed
        assert "one line" in result.check(name).detail


def test_cert_expired_severity_is_sev_1(score) -> None:
    text = mutate(good("cert-expired"), "**SEV-1 (Critical).**", "**SEV-2 (Major).**")
    assert failed_names(score("cert-expired", text)) == {"severity"}


def test_cert_expired_blaming_the_red_herring_fails(score) -> None:
    text = replace_section(
        good("cert-expired"),
        "Root cause",
        "The auth v3.3.0 key rotation broke checkout. Confidence: high.",
    )
    result = score("cert-expired", text)
    assert "root-cause-named" in failed_names(result)
    assert "red herring named" in result.check("root-cause-named").detail


def test_cert_expired_naming_no_cause_fails(score) -> None:
    text = replace_section(
        good("cert-expired"), "Root cause", "Checkout is failing. Confidence: low."
    )
    result = score("cert-expired", text)
    assert failed_names(result) == {"root-cause-named"}
    assert "not named" in result.check("root-cause-named").detail


def test_cert_expired_red_herring_must_be_ruled_out(score) -> None:
    text = good("cert-expired").replace("v3.3.0", "vX")
    assert failed_names(score("cert-expired", text)) == {"red-herring-ruled-out"}


def test_cert_expired_count_mismatch_is_only_a_warning(score) -> None:
    result = score("cert-expired", good("cert-expired").replace("1,961", "1,900"))
    assert result.passed
    assert [w.name for w in result.warnings] == ["counts-reconciled"]


def test_api_outage_severity_is_sev_2(score) -> None:
    text = mutate(good("api-outage"), "**SEV-2 (Major).**", "**SEV-3 (Minor).**")
    assert failed_names(score("api-outage", text)) == {"severity"}


def test_api_outage_blaming_the_red_herring_fails(score) -> None:
    text = replace_section(
        good("api-outage"),
        "Root cause",
        "The checkout v2.7.1 connection reuse broke payments. Confidence: high.",
    )
    result = score("api-outage", text)
    assert "root-cause-named" in failed_names(result)
    assert "red herring named" in result.check("root-cause-named").detail


def test_api_outage_naming_no_cause_fails(score) -> None:
    text = replace_section(
        good("api-outage"), "Root cause", "Checkout is failing for some customers. Confidence: low."
    )
    result = score("api-outage", text)
    assert failed_names(result) == {"root-cause-named"}
    assert "not named" in result.check("root-cause-named").detail


def test_api_outage_red_herring_must_be_ruled_out(score) -> None:
    text = good("api-outage").replace("v2.7.1", "vX")
    assert failed_names(score("api-outage", text)) == {"red-herring-ruled-out"}


def test_api_outage_count_mismatch_is_only_a_warning(score) -> None:
    text = good("api-outage")
    assert "156" in text
    result = score("api-outage", text.replace("156", "150"))
    assert result.passed
    assert [w.name for w in result.warnings] == ["counts-reconciled"]


# --- memory-leak ------------------------------------------------------------------------------

LEAK_CAUSE = "Catalog v2.9.0 leaks memory until the container is OOMKilled."


def memory_leak_with(root_cause: str) -> str:
    return replace_section(good("memory-leak"), "Root cause", root_cause)


def test_memory_leak_severity_is_sev_3(score) -> None:
    text = mutate(good("memory-leak"), "**SEV-3 (Minor).**", "**SEV-2 (Major).**")
    assert failed_names(score("memory-leak", text)) == {"severity"}


def test_memory_leak_blaming_the_red_herring_fails(score) -> None:
    text = memory_leak_with(
        "The auth v3.4.0 session cache leaks memory and the catalog container is killed. "
        "Confidence: high."
    )
    result = score("memory-leak", text)
    assert "root-cause-named" in failed_names(result)
    assert "red herring named" in result.check("root-cause-named").detail


def test_memory_leak_blaming_the_older_cache_deployment_fails(score) -> None:
    text = memory_leak_with(
        "Catalog v2.9.0 leaks memory because of the catalog v2.8.0 category cache. "
        "Confidence: high."
    )
    result = score("memory-leak", text)
    assert "root-cause-named" in failed_names(result)
    assert "red herring named" in result.check("root-cause-named").detail


def test_memory_leak_naming_no_cause_fails(score) -> None:
    text = memory_leak_with("Catalog is restarting. Confidence: low.")
    result = score("memory-leak", text)
    assert failed_names(result) == {"root-cause-named"}
    assert "not named" in result.check("root-cause-named").detail


def test_memory_leak_leak_not_named_fails(score) -> None:
    text = memory_leak_with(
        "The catalog v2.9.0 deployment broke the product pages. Confidence: low."
    )
    result = score("memory-leak", text)
    assert failed_names(result) == {"root-cause-named"}
    assert "not named" in result.check("root-cause-named").detail


def test_memory_leak_wrong_version_fails(score) -> None:
    text = memory_leak_with("The catalog deployment leaks memory. Confidence: high.")
    result = score("memory-leak", text)
    assert failed_names(result) == {"root-cause-named"}
    assert "not named" in result.check("root-cause-named").detail


def test_memory_leak_red_herring_must_be_ruled_out(score) -> None:
    text = good("memory-leak").replace("v3.4.0", "vX")
    assert failed_names(score("memory-leak", text)) == {"red-herring-ruled-out"}


def test_memory_leak_count_mismatch_is_only_a_warning(score) -> None:
    text = good("memory-leak")
    assert "102" in text
    result = score("memory-leak", text.replace("102", "100"))
    assert result.passed
    assert [w.name for w in result.warnings] == ["counts-reconciled"]


@pytest.mark.parametrize(
    "cause",
    [
        "Catalog v2.9.0 has a memory leak.",
        "Catalog v2.9.0 shows unbounded memory growth.",
        "In catalog v2.9.0 the memory grows steadily until the limit is reached.",
        "Catalog v2.9.0 shows climbing memory usage after every restart.",
        "Catalog v2.9.0 keeps an unbounded cache.",
        "Catalog v2.9.0 has a growing memory footprint.",
        "In catalog v2.9.0 the memory usage is growing.",
        "In catalog v2.9.0 memory keeps climbing.",
        "In catalog v2.9.0 Memory consumption rises steadily.",
        "In catalog v2.9.0 RSS grows linearly.",
        "Catalog v2.9.0 has ever-growing memory.",
    ],
)
def test_memory_leak_synonyms_of_the_leak_idea_pass(score, cause: str) -> None:
    text = memory_leak_with(
        f"{cause}\n\n**Confidence: high.** The container was killed four times."
    )
    assert root_cause_detail(score, text, "memory-leak") == (True, "")


@pytest.mark.parametrize(
    "cause",
    [
        "Catalog v2.9.0 is OOM-killed by the platform.",
        "Catalog v2.9.0 makes the container run out of memory.",
        "Catalog v2.9.0 hit its memory limit and was OOM-killed.",
        "Traffic grows and the container of catalog v2.9.0 runs out of memory.",
    ],
)
def test_memory_leak_symptom_only_oom_fails(score, cause: str) -> None:
    """The version is guessable as the latest catalog deployment; the leak is the point."""
    text = memory_leak_with(
        f"{cause}\n\n**Confidence: high.** The container was killed four times."
    )
    result = score("memory-leak", text)
    assert failed_names(result) == {"root-cause-named"}
    assert "not named" in result.check("root-cause-named").detail


def test_memory_leak_cause_without_growth_wording_does_not_pass(score) -> None:
    text = memory_leak_with("Catalog v2.9.0 leaves no room for the comparison endpoint.")
    passed, detail = root_cause_detail(score, text, "memory-leak")
    assert not passed and "not named" in detail


def test_memory_leak_red_herring_only_in_the_confidence_statement_passes(score) -> None:
    text = memory_leak_with(
        f"{LEAK_CAUSE}\n\n**Confidence: medium.** "
        "The auth v3.4.0 deployment cannot be fully excluded."
    )
    assert root_cause_detail(score, text, "memory-leak") == (True, "")


# --- the red herring in the confidence statement ---------------------------------------------

CAUSE = (
    "The external card processor Payvia was unavailable from 16:05 to 17:20 UTC. It returned "
    "HTTP 503 or timed out, so checkout returned 502 or 504."
)
HERRING_NOTE = "the checkout v2.7.1 deployment cannot be fully excluded."


def api_outage_with(root_cause: str) -> str:
    return replace_section(good("api-outage"), "Root cause", root_cause)


def root_cause_detail(score, text: str, scenario: str = "api-outage") -> tuple[bool, str]:
    result = score(scenario, text).check("root-cause-named")
    return result.passed, result.detail


def test_real_report_with_the_red_herring_in_the_confidence_statement_passes(score) -> None:
    text = (FIXTURES / "real-api-outage-red-herring-in-confidence.md").read_text(encoding="utf-8")
    root_cause = Report.parse(text).section("Root cause")
    assert "v2.7.1" in root_cause  # the herring really is in the section, in the confidence line
    cause, confidence = split_confidence(root_cause)
    assert "v2.7.1" not in cause and "v2.7.1" in confidence
    result = score("api-outage", text)
    assert result.passed, format_score(result)


@pytest.mark.parametrize(
    "label",
    [
        "**Confidence: medium.**",
        "Confidence: **medium**.",
        "Confidence: medium.",
        "confidence: Medium",
    ],
)
def test_red_herring_only_in_the_confidence_paragraph_passes(score, label: str) -> None:
    text = api_outage_with(f"{CAUSE}\n\n{label} There is no provider data, so {HERRING_NOTE}")
    assert root_cause_detail(score, text) == (True, "")


def test_red_herring_in_the_confidence_line_directly_below_the_cause_passes(score) -> None:
    text = api_outage_with(f"{CAUSE}\nConfidence: high. Reason: {HERRING_NOTE}")
    assert root_cause_detail(score, text) == (True, "")


def test_red_herring_in_a_separate_reason_paragraph_passes(score) -> None:
    text = api_outage_with(
        f"{CAUSE}\n\nConfidence: **medium**.\n\nReason: no data, so {HERRING_NOTE}"
    )
    assert root_cause_detail(score, text) == (True, "")


def test_confidence_statement_inline_in_the_cause_paragraph_is_split_at_the_label(score) -> None:
    text = api_outage_with(f"{CAUSE} Confidence: **medium**. No provider data, so {HERRING_NOTE}")
    assert root_cause_detail(score, text) == (True, "")
    cause, confidence = split_confidence(f"{CAUSE} Confidence: **medium**. Then {HERRING_NOTE}")
    assert cause.startswith("The external card processor") and "v2.7.1" not in cause
    assert confidence.startswith("Confidence: **medium**")


@pytest.mark.parametrize(
    ("scenario", "herring", "cause"),
    [
        ("api-outage", "checkout v2.7.1", CAUSE),
        (
            "cert-expired",
            "auth v3.3.0",
            "The TLS certificate of payment expired at 15:20, so the handshake fails.",
        ),
        ("memory-leak", "auth v3.4.0", LEAK_CAUSE),
    ],
)
def test_red_herring_in_the_cause_paragraph_still_fails(
    score, scenario: str, herring: str, cause: str
) -> None:
    for wording in (f"The {herring} deployment broke it.", f"It is not caused by {herring}."):
        text = replace_section(
            good(scenario), "Root cause", f"{cause} {wording}\n\n**Confidence: high.** Clear."
        )
        passed, detail = root_cause_detail(score, text, scenario)
        assert not passed and "red herring named in Root cause" in detail, wording


def test_blaming_the_red_herring_fails_even_if_the_confidence_line_does_not(score) -> None:
    text = api_outage_with(
        "The checkout v2.7.1 deployment broke payments (Payvia provider outage ruled out).\n\n"
        "**Confidence: high.** The error messages are consistent."
    )
    passed, detail = root_cause_detail(score, text)
    assert not passed and "red herring named" in detail


def test_without_a_confidence_statement_the_whole_section_is_checked(score) -> None:
    text = api_outage_with(f"{CAUSE}\n\nNote: {HERRING_NOTE}")
    passed, detail = root_cause_detail(score, text)
    assert not passed and "red herring named" in detail


def test_the_word_confidence_in_the_cause_sentence_does_not_split(score) -> None:
    section = f"With high confidence, the deployment v2.7.1 is the cause. {CAUSE}"
    assert split_confidence(section) == (section, "")
    passed, detail = root_cause_detail(score, api_outage_with(section))
    assert not passed and "red herring named" in detail


def test_a_later_paragraph_after_the_confidence_statement_is_cause_statement_again(score) -> None:
    text = api_outage_with(
        f"{CAUSE}\n\n**Confidence: high.** Clear.\n\nIn fact the v2.7.1 deployment did it."
    )
    passed, detail = root_cause_detail(score, text)
    assert not passed and "red herring named" in detail


def test_required_cause_terms_are_still_searched_in_the_whole_section(score) -> None:
    text = api_outage_with(
        "Checkout is failing.\n\n**Confidence: high.** Payvia, the provider, had an outage."
    )
    passed, detail = root_cause_detail(score, text)
    assert passed, detail
    text = api_outage_with("Checkout is failing.\n\n**Confidence: low.** Unknown.")
    passed, detail = root_cause_detail(score, text)
    assert not passed and "not named" in detail


def test_split_confidence_without_a_label_returns_the_whole_section() -> None:
    assert split_confidence("Pool exhausted.\n\nNothing else.") == (
        "Pool exhausted.\n\nNothing else.",
        "",
    )


# --- "(INFO / ERROR)" is an ok / failed label ----------------------------------------------------
INFO_ERROR_FIXTURE = "real-checkout-500-info-error-labels.md"
INFO_ERROR_LINE = (
    "Client split in checkout logs from 14:00Z (INFO / ERROR): web-2.30.4 13 / 275; "
    "android-5.7.9 8 / 158; web-2.31.0 1092 / 0; ios-5.8.1 856 / 0."
)


def info_error(line: str = INFO_ERROR_LINE) -> str:
    """The real report with its Evidence counts line replaced."""
    text = (FIXTURES / INFO_ERROR_FIXTURE).read_text(encoding="utf-8")
    return mutate(text, INFO_ERROR_LINE, line)


def test_real_report_with_info_error_labels_passes(score) -> None:
    # A real run (kept verbatim): "(INFO / ERROR): web-2.30.4 13 / 275; ... web-2.31.0 1092 / 0".
    result = score("checkout-500", (FIXTURES / INFO_ERROR_FIXTURE).read_text(encoding="utf-8"))
    assert len(result.results) == 8
    assert result.passed and not result.warnings, format_score(result)
    assert result.check("counts-reconciled").detail == "matches the database"


def test_info_error_label_is_read_as_ok_failed_including_zero(score) -> None:
    result = score("checkout-500", info_error())
    assert result.check("counts-present").passed
    assert result.check("counts-reconciled").passed


@pytest.mark.parametrize(
    "label",
    [
        ("(INFO / ERROR)", "INFO / ERROR:"),
        ("(INFO / ERROR)", "(info / error)"),
        ("(INFO / ERROR)", "(INFO/ERROR)"),
    ],
)
def test_info_error_label_variants_are_accepted(score, label: tuple[str, str]) -> None:
    assert score("checkout-500", info_error(INFO_ERROR_LINE.replace(*label))).passed


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("web-2.31.0 1092 / 0", "web-2.31.0 0 / 1092"),  # swapped
        ("web-2.30.4 13 / 275", "web-2.30.4 275 / 13"),  # swapped
        ("web-2.30.4 13 / 275", "web-2.30.4 13 / 270"),  # wrong number
    ],
)
def test_info_error_wrong_numbers_fail(score, old: str, new: str) -> None:
    result = score("checkout-500", info_error(INFO_ERROR_LINE.replace(old, new)))
    assert result.check("counts-present").passed
    assert not result.check("counts-reconciled").passed
    assert "differ from the database" in result.check("counts-reconciled").detail


def test_info_error_missing_client_fails(score) -> None:
    result = score("checkout-500", info_error(INFO_ERROR_LINE.replace(" ios-5.8.1 856 / 0", "")))
    assert {"counts-present", "counts-reconciled"} <= failed_names(result)
    assert "ios-5.8.1" in result.check("counts-present").detail
    assert "ios-5.8.1" in result.check("counts-reconciled").detail


def test_info_error_client_twice_with_different_numbers_conflicts(score) -> None:
    result = score(
        "checkout-500",
        info_error(INFO_ERROR_LINE + "\n\nLater (INFO / ERROR): web-2.30.4 13 / 270."),
    )
    assert not result.check("counts-reconciled").passed
    assert "conflicting" in result.check("counts-reconciled").detail


@pytest.mark.parametrize(
    "line",
    [
        INFO_ERROR_LINE.replace("(INFO / ERROR)", "(ERROR / INFO)"),  # INFO-first numbers
        INFO_ERROR_LINE.replace("(INFO / ERROR)", "(ERROR / INFO)")
        .replace("13 / 275", "275 / 13")
        .replace("8 / 158", "158 / 8")
        .replace("1092 / 0", "0 / 1092")
        .replace("856 / 0", "0 / 856"),  # ERROR-first numbers: still no failed-first label
    ],
)
def test_error_info_is_not_a_label(score, line: str) -> None:
    result = score("checkout-500", info_error(line))
    assert not result.check("counts-reconciled").passed


def test_error_info_label_with_info_first_numbers_reports_missing_clients(score) -> None:
    result = score(
        "checkout-500", info_error(INFO_ERROR_LINE.replace("(INFO / ERROR)", "(ERROR / INFO)"))
    )
    assert "web-2.31.0, ios-5.8.1" in result.check("counts-present").detail


def test_info_error_label_applies_only_to_its_own_line(score) -> None:
    rest = INFO_ERROR_LINE.split("): ", 1)[1]
    text = info_error(
        "Client split in checkout logs from 14:00Z (INFO / ERROR) is below.\n- " + rest
    )
    result = score("checkout-500", text)
    assert not result.check("counts-present").passed
    assert "web-2.31.0" in result.check("counts-present").detail


def test_info_error_label_with_failed_ok_pair_on_one_line_conflicts(score) -> None:
    line = INFO_ERROR_LINE.replace("web-2.31.0 1092 / 0", "web-2.31.0 0 failed / 1092 ok")
    result = score("checkout-500", info_error(line))
    assert not result.check("counts-present").passed
    assert "ok/failed and failed/ok pairs on one line" in result.check("counts-present").detail


# --- prose pairs: "N errors and M ok" and "N errors in M" ----------------------------------------
PROSE_FIXTURE = "real-checkout-500-prose-errors-and-ok.md"
PROSE_LINE = (
    "web-2.30.4 had 275 errors and 13 ok (95% failed), android-5.7.9 had 158 errors and 8 ok "
    "(95% failed), web-2.31.0 had 0 errors in 1092, ios-5.8.1 had 0 errors in 856."
)


def prose(line: str = PROSE_LINE, *, old: str = PROSE_LINE) -> str:
    """The real report with its per-client sentence replaced."""
    return mutate((FIXTURES / PROSE_FIXTURE).read_text(encoding="utf-8"), old, line)


def test_real_report_with_prose_errors_and_ok_passes(score) -> None:
    # A real run (kept verbatim): "web-2.30.4 had 275 errors and 13 ok", "had 0 errors in 1092".
    result = score("checkout-500", (FIXTURES / PROSE_FIXTURE).read_text(encoding="utf-8"))
    assert len(result.results) == 8
    assert result.passed and not result.warnings, format_score(result)
    assert result.check("counts-reconciled").detail == "matches the database"


def test_prose_errors_and_ok_is_read_as_failed_and_ok() -> None:
    assert pair_counts("web-2.30.4 had 275 errors and 13 ok (95% failed)", "web-2.30.4") == [
        (13, 275)
    ]
    assert pair_counts("android-5.7.9 had 1 error and 8 ok", "android-5.7.9") == [(8, 1)]


def test_prose_errors_in_total_is_read_as_failed_of_total_including_zero() -> None:
    assert pair_counts("web-2.31.0 had 0 errors in 1092,", "web-2.31.0") == [(1092, 0)]
    assert pair_counts("ios-5.8.1 had 5 errors in 1,000.", "ios-5.8.1") == [(995, 5)]


def test_prose_swapped_numbers_fail(score) -> None:
    line = PROSE_LINE.replace("275 errors and 13 ok", "13 errors and 275 ok")
    result = score("checkout-500", prose(line))
    assert result.check("counts-present").passed
    reconciled = result.check("counts-reconciled")
    assert not reconciled.passed
    assert reconciled.detail == (
        "(ok, failed) differ from the database: web-2.30.4: report (275, 13), database (13, 275)"
    )


def test_prose_reversed_word_order_is_not_a_pair(score) -> None:
    # "275 ok and 13 errors" is not one of the two shapes: the client has no count at all.
    result = score(
        "checkout-500", prose(PROSE_LINE.replace("275 errors and 13 ok", "275 ok and 13 errors"))
    )
    assert result.check("counts-present").detail == "no ok and failed count for: web-2.30.4"
    assert result.check("counts-reconciled").detail == "per-client counts missing: web-2.30.4"


def test_prose_wrong_number_fails(score) -> None:
    result = score("checkout-500", prose(PROSE_LINE.replace("158 errors", "150 errors")))
    assert result.check("counts-present").passed
    assert result.check("counts-reconciled").detail == (
        "(ok, failed) differ from the database: android-5.7.9: report (8, 150), database (8, 158)"
    )


def test_prose_missing_client_fails(score) -> None:
    result = score("checkout-500", prose(PROSE_LINE.replace(", ios-5.8.1 had 0 errors in 856", "")))
    assert result.check("counts-present").detail == "no ok and failed count for: ios-5.8.1"
    assert result.check("counts-reconciled").detail == "per-client counts missing: ios-5.8.1"


def test_prose_client_twice_with_different_numbers_conflicts(score) -> None:
    line = PROSE_LINE + " Later: web-2.30.4 had 270 errors and 13 ok."
    result = score("checkout-500", prose(line))
    assert result.check("counts-reconciled").detail == (
        "conflicting (ok, failed) in the report: web-2.30.4: (13, 275) vs (13, 270)"
    )


def test_prose_client_twice_with_the_same_numbers_is_fine(score) -> None:
    line = PROSE_LINE + " Again: web-2.30.4 had 275 errors and 13 ok."
    assert score("checkout-500", prose(line)).passed


def test_prose_errors_in_total_with_n_greater_than_m_is_invalid(score) -> None:
    line = PROSE_LINE.replace(
        "web-2.31.0 had 0 errors in 1092", "web-2.31.0 had 2000 errors in 1092"
    )
    result = score("checkout-500", prose(line))
    assert result.check("counts-present").detail == "no ok and failed count for: web-2.31.0"
    assert result.check("counts-reconciled").detail == "per-client counts missing: web-2.31.0"


@pytest.mark.parametrize("unit", ["requests", "lines", "log lines"])
def test_prose_errors_in_total_accepts_one_unit_word(score, unit: str) -> None:
    result = score(
        "checkout-500", prose(PROSE_LINE.replace("errors in 1092,", f"errors in 1092 {unit},"))
    )
    assert result.passed, format_score(result)


@pytest.mark.parametrize(
    "tail",
    [
        "errors in 14:00 UTC",  # a time, not a total
        "errors in 14:00-15:00",  # a window
        "errors in 856 minutes",  # any other unit word
        "errors in 856 log lines of web",  # more than one unit word
        "errors in 856 of 900",
        "errors in 856/900",
    ],
)
def test_prose_errors_in_is_not_a_count_for_times_windows_and_other_words(score, tail: str) -> None:
    line = PROSE_LINE.replace("errors in 856.", tail + ".")
    result = score("checkout-500", prose(line))
    assert result.check("counts-present").detail == "no ok and failed count for: ios-5.8.1"


def test_prose_time_window_with_a_digit_count_is_not_read() -> None:
    assert pair_counts("ios-5.8.1 had 3 errors in 14:00 UTC.", "ios-5.8.1") == []
    assert pair_counts("ios-5.8.1 had 3 errors in 14:00-15:00.", "ios-5.8.1") == []


def test_prose_errors_in_total_ignores_an_earlier_ok_failed_label(score) -> None:
    line = (
        "(ok / failed): web-2.30.4 13 ok / 275 failed; android-5.7.9 8 / 158; "
        "web-2.31.0 had 0 errors in 1092, ios-5.8.1 had 0 errors in 856."
    )
    result = score("checkout-500", prose(line))
    assert result.check("counts-present").passed
    assert result.check("counts-reconciled").detail == "matches the database"


def test_prose_errors_and_ok_with_an_ok_failed_label_conflicts(score) -> None:
    line = "(ok / failed) " + PROSE_LINE
    result = score("checkout-500", prose(line))
    assert "ok/failed and failed/ok pairs on one line" in result.check("counts-present").detail
    assert "ok/failed and failed/ok pairs on one line" in result.check("counts-reconciled").detail


def test_prose_errors_and_ok_conflicts_with_an_ok_failed_pair_on_one_line(score) -> None:
    line = PROSE_LINE.replace(
        "android-5.7.9 had 158 errors and 8 ok", "android-5.7.9 8 ok / 158 failed"
    )
    result = score("checkout-500", prose(line))
    assert "ok/failed and failed/ok pairs on one line" in result.check("counts-reconciled").detail


def test_ok_slash_errors_is_still_not_a_pair() -> None:
    # FAILED_WORD is unchanged: "errors" is only read in the two prose shapes.
    assert pair_counts("web-2.30.4 13 ok / 275 errors", "web-2.30.4") == []
    assert pair_counts("web-2.30.4 275 errors / 13 ok", "web-2.30.4") == []


def test_prose_numbers_before_a_client_name_are_not_read() -> None:
    text = '13 web-2.30.4 and 8 android-5.7.9 checkout "ok" lines are unexplained.'
    assert pair_counts(text, "web-2.30.4", ("android-5.7.9",)) == []
    assert pair_counts(text, "android-5.7.9", ("web-2.30.4",)) == []


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("unit", ["", " requests", " log lines"])
def test_prose_errors_in_total_may_end_a_line_without_punctuation(newline: str, unit: str) -> None:
    text = (
        f"web-2.31.0 had 0 errors in 1092{unit}{newline}"
        f"ios-5.8.1 had 0 errors in 856{unit}{newline}next"
    )
    clients = ("web-2.31.0", "ios-5.8.1")
    assert len(pair_hits(text, clients)) == 2
    assert pair_counts(text, "web-2.31.0", clients) == [(1092, 0)]
    assert pair_counts(text, "ios-5.8.1", clients) == [(856, 0)]


@pytest.mark.parametrize("unit", ["", " lines"])
def test_prose_errors_in_total_may_end_the_text(unit: str) -> None:
    assert pair_counts(f"ios-5.8.1 had 0 errors in 856{unit}", "ios-5.8.1") == [(856, 0)]


@pytest.mark.parametrize(
    "text",
    [
        "ios-5.8.1 had 3 errors in 14:00 UTC\nnext",
        "ios-5.8.1 had 3 errors in 14:00-15:00\r\nnext",
        "ios-5.8.1 had 0 errors in 1,948 lines of web\nnext",
        "ios-5.8.1 had 0 errors in 1092 minutes\nnext",
    ],
)
def test_prose_line_end_does_not_make_other_text_a_count(text: str) -> None:
    assert pair_counts(text, "ios-5.8.1") == []


def test_prose_unit_word_is_not_taken_from_the_next_line() -> None:
    # 1092 ends its line, so it is the total; "lines" on the next line is not its unit.
    text = "ios-5.8.1 had 0 errors in 1092\nlines of web"
    assert pair_counts(text, "ios-5.8.1") == [(1092, 0)]


PAREN_FIXTURE = "real-checkout-500-ok-in-parentheses-zero-failures.md"
PAREN_LINE = "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 0 failures."
PAREN_CLIENTS = ("web-2.30.4", "android-5.7.9", "web-2.31.0", "ios-5.8.1")


def paren(line: str = PAREN_LINE) -> str:
    """The real report with its shared-zero sentence replaced."""
    return mutate((FIXTURES / PAREN_FIXTURE).read_text(encoding="utf-8"), PAREN_LINE, line)


def test_real_report_with_ok_in_parentheses_and_zero_failures_passes(score) -> None:
    # A real run (kept verbatim): "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 0 failures".
    result = score("checkout-500", (FIXTURES / PAREN_FIXTURE).read_text(encoding="utf-8"))
    assert len(result.results) == 8
    assert result.passed and not result.warnings, format_score(result)
    assert result.check("counts-reconciled").detail == "matches the database"


@pytest.mark.parametrize(
    "line",
    [
        "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) are fine.",
        "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 3 failures.",
        "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 0.5% failures.",
        "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 0 errors.",
        "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok). Nothing else has 0 failures.",
    ],
)
def test_ok_in_parentheses_without_a_zero_statement_stays_missing(score, line: str) -> None:
    result = score("checkout-500", paren(line))
    assert failed_names(result) == {"counts-present", "counts-reconciled"}
    for name in ("counts-present", "counts-reconciled"):
        assert result.check(name).detail.endswith("web-2.31.0, ios-5.8.1")


def test_ok_in_parentheses_with_a_nonzero_shared_statement_stays_missing() -> None:
    text = "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 3 failures."
    assert pair_counts(text, "web-2.31.0", PAREN_CLIENTS) == []
    assert pair_counts(text, "ios-5.8.1", PAREN_CLIENTS) == []


def test_ok_in_parentheses_zero_statement_in_the_next_sentence_is_not_shared() -> None:
    text = "web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) were checked. Both have 0 failures."
    assert pair_counts(text, "web-2.31.0", PAREN_CLIENTS) == []


def test_ok_in_parentheses_wrong_number_fails_reconciliation(score) -> None:
    result = score("checkout-500", paren(PAREN_LINE.replace("1092", "1093")))
    assert failed_names(result) == {"counts-reconciled"}
    assert "web-2.31.0: report (1093, 0)" in result.check("counts-reconciled").detail


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("web-2.31.0 (1,092 ok), ios-5.8.1 (856 ok) have 0 failed.", [(1092, 0), (856, 0)]),
        ("web-2.31.0 (1092 ok), and ios-5.8.1 (856 ok) have no failures", [(1092, 0), (856, 0)]),
        ("web-2.31.0 (1092 ok) & ios-5.8.1 (856 ok) both had no failures.", [(1092, 0), (856, 0)]),
        ("web-2.31.0 (1092 ok) has 0 failures.", [(1092, 0)]),
    ],
)
def test_ok_in_parentheses_single_client_and_zero_failed_variants(
    line: str, expected: list[tuple[int, int]]
) -> None:
    found = [
        pair
        for client in ("web-2.31.0", "ios-5.8.1")
        for pair in pair_counts(line, client, PAREN_CLIENTS)
    ]
    assert found == expected


def test_ok_in_parentheses_shared_zero_conflicting_with_an_explicit_pair_is_a_conflict(
    score,
) -> None:
    # The same client also has an explicit pair with another value: both are kept, nothing is
    # silently overwritten, and the existing "conflicting" rule of counts-reconciled applies.
    text = paren(PAREN_LINE + "\n\nLater: ios-5.8.1 850 ok / 6 failed.")
    assert pair_counts(text, "ios-5.8.1", PAREN_CLIENTS) == [(850, 6), (856, 0)]
    result = score("checkout-500", text)
    assert failed_names(result) == {"counts-reconciled"}
    detail = result.check("counts-reconciled").detail
    assert detail.startswith("conflicting (ok, failed) in the report: ios-5.8.1: ")
    assert "(850, 6)" in detail and "(856, 0)" in detail


def test_ok_in_parentheses_is_case_sensitive_so_uppercase_ok_stays_prose() -> None:
    # The INFO/ERROR report writes "(1092 OK) ... had 0 failures" next to its labelled list.
    text = "web-2.31.0 (1092 OK) and ios-5.8.1 (856 OK) had 0 failures."
    assert pair_counts(text, "web-2.31.0", PAREN_CLIENTS) == []


# --- table header: the ok column is found by the word ok or success ---
@pytest.mark.parametrize(
    ("header", "row", "expected"),
    [
        ("| client | OK | Failed |", "| web-2.30.4 | 13 | 275 |", [(13, 275)]),
        ("| client | ok | failed |", "| web-2.30.4 | 13 | 275 |", [(13, 275)]),
        ("| client | Success | Error |", "| web-2.30.4 | 13 | 275 |", [(13, 275)]),
        # Shapes the old header regex (backspaces, no word boundary) misread positionally.
        ("| client | Failed | OK |", "| web-2.30.4 | 275 | 13 |", [(13, 275)]),
        ("| client | Total | OK | Failed |", "| web-2.30.4 | 288 | 13 | 275 |", [(13, 275)]),
        ("| client | Failed | Total | OK |", "| web-2.30.4 | 275 | 288 | 13 |", [(13, 275)]),
    ],
)
def test_table_ok_column_is_found_by_the_header_word(
    header: str, row: str, expected: list[tuple[int, int]]
) -> None:
    text = "\n".join([header, "|---|---|---|", row, ""])
    assert table_counts(text, "web-2.30.4") == expected


def test_table_with_a_reordered_ok_header_passes_the_report(score) -> None:
    table = good("checkout-500")
    for old, new in (
        ("| client | ok | failed |", "| client | failed | ok |"),
        ("| web-2.30.4 | 13 | 275 |", "| web-2.30.4 | 275 | 13 |"),
        ("| android-5.7.9 | 8 | 158 |", "| android-5.7.9 | 158 | 8 |"),
        ("| web-2.31.0 | 1092 | 0 |", "| web-2.31.0 | 0 | 1092 |"),
        ("| ios-5.8.1 | 856 | 0 |", "| ios-5.8.1 | 0 | 856 |"),
    ):
        table = mutate(table, old, new)
    result = score("checkout-500", table)
    assert result.passed, format_score(result)
