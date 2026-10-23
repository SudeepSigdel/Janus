"""Saved sessions are per origin, opt-in, and removable."""

from __future__ import annotations

from pathlib import Path

from janus import sessions


def test_session_file_is_per_origin(tmp_path: Path) -> None:
    a = sessions.session_file("http://127.0.0.1:8102/login", tmp_path)
    b = sessions.session_file("http://127.0.0.1:8102/issues?x=1", tmp_path)
    c = sessions.session_file("http://127.0.0.1:8101/services", tmp_path)
    assert a == b and a != c and a.parent == tmp_path


def test_forget_removes_only_that_origin(tmp_path: Path) -> None:
    keep = sessions.session_file("http://127.0.0.1:8101/", tmp_path)
    gone = sessions.session_file("http://127.0.0.1:8102/", tmp_path)
    for p in (keep, gone):
        p.write_text("{}", encoding="utf-8")
    assert sessions.forget("http://127.0.0.1:8102/login", tmp_path)
    assert not gone.exists() and keep.exists()
    assert not sessions.forget("http://127.0.0.1:8102/login", tmp_path)


class _FakeBrowser:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def new_context(self, **kwargs):
        self.kwargs = kwargs
        return object()


def test_open_context_records_video_only_when_asked(tmp_path: Path) -> None:
    browser = _FakeBrowser()
    url = "http://127.0.0.1:8101/"
    sessions.open_context(browser, url, False, tmp_path)  # type: ignore[arg-type]
    assert "record_video_dir" not in browser.kwargs
    video = tmp_path / "v"
    sessions.open_context(browser, url, False, tmp_path, record_dir=video)  # type: ignore[arg-type]
    assert browser.kwargs["record_video_dir"] == str(video)
    assert browser.kwargs["service_workers"] == "block"
