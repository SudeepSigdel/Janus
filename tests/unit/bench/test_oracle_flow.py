from __future__ import annotations

from janus_bench.agents.oracle_flow import flow_for, matches_flow


def test_flow_for_submit_task() -> None:
    assert flow_for("nag-01") == ["अर्को / Next: review", "पेश गर्नुहोस् / Submit application"]


def test_flow_for_unknown_task_is_empty() -> None:
    assert flow_for("does-not-exist") == []


def test_matches_flow_true_for_a_listed_label() -> None:
    assert matches_flow("nag-01", "पेश गर्नुहोस् / Submit application") is True


def test_matches_flow_false_for_an_unrelated_label() -> None:
    assert matches_flow("nag-01", "something else entirely") is False


def test_matches_flow_false_for_none() -> None:
    assert matches_flow("nag-01", None) is False


def test_apply_flow_matches_the_dynamic_per_issue_label_by_prefix() -> None:
    assert matches_flow("share-13", "आवेदन / Apply — NIC Asia Debenture 2083") is True


def test_apply_prefix_only_matches_apply_flavored_tasks() -> None:
    # nag-01's routine (_submit_bs) never sees this label; the prefix check must be
    # scoped to _apply's routine, not applied globally.
    assert matches_flow("nag-01", "आवेदन / Apply — anything") is False


def test_edit_kitta_flow_includes_the_report_nav_link() -> None:
    assert flow_for("share-06")[0] == "मेरो रिपोर्ट / My Report"


def test_cancel_and_update_phone_flows_differ() -> None:
    assert flow_for("nag-04") != flow_for("nag-03")


def test_paged_and_retry_flows() -> None:
    assert flow_for("nag-23")[0] == "अर्को पृष्ठ / Next page"
    assert flow_for("nag-26")[0] == "अर्को पृष्ठ / Next page"
    assert flow_for("nag-21") == flow_for("nag-03")


def test_q4b_flows() -> None:
    assert flow_for("share-24")[:2] == ["मेरो रिपोर्ट / My Report", "अर्को पृष्ठ / Next page"]
    assert flow_for("share-25")[1] == "अर्को पृष्ठ / Next page"
    for task_id in ("share-22", "share-26", "share-27"):
        assert flow_for(task_id) == flow_for("share-01")
    assert matches_flow("share-22", "आवेदन / Apply — NIC Asia Debenture 2082")
