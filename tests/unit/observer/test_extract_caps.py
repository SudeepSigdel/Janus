"""Cap/truncation helpers used by extract_snapshot, tested without a browser."""

from __future__ import annotations

from janus.observer.extract import cap_elements, cap_untrusted_text, truncate_label


def _raw_element(i: int) -> dict[str, object]:
    return {
        "tag": "a",
        "role": "link",
        "accessible_name": f"link {i}",
        "name_attr": None,
        "form_id": None,
    }


def test_cap_elements_under_limit_is_untruncated() -> None:
    raw = [_raw_element(i) for i in range(3)]
    kept, truncated = cap_elements(raw, max_elements=5)
    assert kept == raw
    assert truncated is False


def test_cap_elements_over_limit_truncates() -> None:
    raw = [_raw_element(i) for i in range(10)]
    kept, truncated = cap_elements(raw, max_elements=4)
    assert len(kept) == 4
    assert kept == raw[:4]
    assert truncated is True


def test_truncate_label_under_limit_is_unchanged() -> None:
    assert truncate_label("Submit", max_chars=80) == "Submit"


def test_truncate_label_over_limit_is_shortened_with_ellipsis() -> None:
    label = "x" * 100
    result = truncate_label(label, max_chars=10)
    assert len(result) == 10
    assert result.endswith("…")


def test_cap_untrusted_text_under_limit_keeps_all_blocks() -> None:
    blocks = ["short one", "short two"]
    kept, truncated = cap_untrusted_text(blocks, max_chars=1000)
    assert kept == blocks
    assert truncated is False


def test_cap_untrusted_text_drops_blocks_once_budget_is_spent() -> None:
    blocks = ["a" * 10, "b" * 10, "c" * 10]
    kept, truncated = cap_untrusted_text(blocks, max_chars=15)
    assert kept == ["a" * 10, "b" * 5 + "…"]
    assert truncated is True


def test_cap_untrusted_text_stops_immediately_when_budget_already_spent() -> None:
    blocks = ["a" * 10, "b" * 5]
    kept, truncated = cap_untrusted_text(blocks, max_chars=10)
    assert kept == ["a" * 10]
    assert truncated is True
