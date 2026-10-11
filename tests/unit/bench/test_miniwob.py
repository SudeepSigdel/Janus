"""janus_bench.external.miniwob (docs/PLAN.md B1): split file, summary, browser smoke."""

from __future__ import annotations

from pathlib import Path

import pytest

from janus_bench.external import miniwob as mw


def test_split_has_125_tasks_in_disjoint_categories() -> None:
    smoke, category = mw.load_split()
    assert len(category) == 125
    counts = {c: sum(v == c for v in category.values()) for c in set(category.values())}
    assert counts == {"structured": 55, "read": 38, "coordinate": 32}
    assert set(smoke) <= {t for t, c in category.items() if c == "structured"}


def test_summarize_reports_per_category_and_total() -> None:
    records = [
        {"category": "structured", "success": True},
        {"category": "structured", "success": False},
        {"category": "read", "success": False},
    ]
    text = mw.summarize(records)
    assert "structured  1/2 = 50.0%" in text
    assert "read        0/1 = 0.0%" in text
    assert "TOTAL       1/3 = 33.3%" in text


def test_run_refuses_a_missing_checkout(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="miniwob-plusplus"):
        mw.run(
            agent="null", tasks=[], category={}, repeats=1, out=tmp_path / "x.jsonl", root=tmp_path
        )


@pytest.mark.browser
@pytest.mark.skipif(not (mw.DEFAULT_ROOT / "miniwob").is_dir(), reason="no MiniWoB checkout")
def test_scripted_click_scores_and_null_does_not(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with mw.serve(mw.DEFAULT_ROOT) as base, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context().new_page()
            goal = mw.start_episode(page, f"{base}/miniwob/click-test.html", seed=1)
            assert goal == "Click the button."
            assert mw.episode_state(page)[2] is False
            page.click("#subbtn")
            raw, _, done = mw.episode_state(page)
            assert done and raw == 1.0
        finally:
            browser.close()
    out = tmp_path / "null.jsonl"
    records = mw.run(
        agent="null",
        tasks=["click-test"],
        category={"click-test": "structured"},
        repeats=1,
        out=out,
    )
    assert [r.success for r in records] == [False]
    assert mw.run(agent="null", tasks=["click-test"], category={}, repeats=1, out=out) == []
