"""Tests for the dev-only Review Dock endpoint: POST /api/review-note.

Two independent guards keep this OFF for real users; here we exercise the
server-side env-gate (403 unless AUTOCUE_REVIEW_DOCK=1) plus the append format,
sanitisation, and validation. The endpoint writes crew/REVIEW-NOTES.md relative
to cwd, so every "enabled" test chdirs to a tmp_path sandbox.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocue.serve.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def _enable(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCUE_REVIEW_DOCK", "1")
    monkeypatch.chdir(tmp_path)


def _notes(tmp_path) -> str:
    return (tmp_path / "crew" / "REVIEW-NOTES.md").read_text(encoding="utf-8")


class TestReviewNoteEndpoint:
    def test_403_when_env_unset(self, client, monkeypatch, tmp_path):
        monkeypatch.delenv("AUTOCUE_REVIEW_DOCK", raising=False)
        monkeypatch.chdir(tmp_path)
        r = client.post("/api/review-note", json={"page": "cues", "note": "hi"})
        assert r.status_code == 403
        # Nothing should be written when disabled.
        assert not (tmp_path / "crew" / "REVIEW-NOTES.md").exists()

    def test_403_when_env_not_one(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOCUE_REVIEW_DOCK", "0")
        monkeypatch.chdir(tmp_path)
        r = client.post("/api/review-note", json={"page": "cues", "note": "hi"})
        assert r.status_code == 403

    def test_appends_line_and_returns_ok(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "cues", "note": "make it pop"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        content = _notes(tmp_path)
        lines = content.splitlines()
        assert len(lines) == 1
        # [YYYY-MM-DD HH:MM:SS] [cues] make it pop
        import re

        assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[cues\] make it pop$", lines[0]), lines[0]
        assert content.endswith("\n")

    def test_creates_crew_dir_if_missing(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        assert not (tmp_path / "crew").exists()
        r = client.post("/api/review-note", json={"page": "x", "note": "y"})
        assert r.status_code == 200
        assert (tmp_path / "crew" / "REVIEW-NOTES.md").exists()

    def test_appends_multiple_lines(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        client.post("/api/review-note", json={"page": "a", "note": "first"})
        client.post("/api/review-note", json={"page": "b", "note": "second"})
        lines = _notes(tmp_path).splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("[a] first")
        assert lines[1].endswith("[b] second")

    def test_422_on_empty_note(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "cues", "note": "   "})
        assert r.status_code == 422
        assert not (tmp_path / "crew" / "REVIEW-NOTES.md").exists()

    def test_422_on_missing_note(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "cues"})
        assert r.status_code == 422

    def test_newline_injection_collapses_to_one_line(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        r = client.post(
            "/api/review-note",
            json={"page": "cues", "note": "line one\nINJECTED\r\nline three"},
        )
        assert r.status_code == 200
        lines = _notes(tmp_path).splitlines()
        assert len(lines) == 1, lines
        assert "INJECTED" in lines[0]  # collapsed onto the single note line

    def test_page_truncated_to_64(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        long_page = "p" * 200
        r = client.post("/api/review-note", json={"page": long_page, "note": "n"})
        assert r.status_code == 200
        line = _notes(tmp_path).splitlines()[0]
        # The bracketed page segment carries at most 64 chars.
        page_seg = line.split("] [", 1)[1].split("]", 1)[0]
        assert len(page_seg) == 64

    def test_page_defaults_to_unknown_when_blank(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "   ", "note": "n"})
        assert r.status_code == 200
        assert "[unknown] n" in _notes(tmp_path).splitlines()[0]

    def test_page_defaults_to_unknown_when_omitted(self, client, monkeypatch, tmp_path):
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"note": "n"})
        assert r.status_code == 200
        assert "[unknown] n" in _notes(tmp_path).splitlines()[0]

    def test_page_newline_injection_cannot_forge_a_second_line(self, client, monkeypatch, tmp_path):
        """Auditor #1: a newline in `page` must NOT split the written line — the
        'one line per note' invariant covers the WHOLE line, not just the note."""
        _enable(monkeypatch, tmp_path)
        r = client.post(
            "/api/review-note",
            json={
                "page": "home\n[2099-01-01 00:00:00] [admin] FORGED",
                "note": "real",
            },
        )
        assert r.status_code == 200
        content = _notes(tmp_path)
        lines = content.splitlines()
        assert len(lines) == 1, lines  # exactly ONE physical line, no forged entry
        # The single line carries no embedded newline (rstrip the trailing \n only).
        assert "\n" not in content.rstrip("\n")
        # The forged token survives only as inert text inside the [page] segment.
        assert "FORGED" in lines[0]

    def test_page_strips_bracket_framing_chars(self, client, monkeypatch, tmp_path):
        """`[`/`]` in page would break the [page] framing → stripped out."""
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "ho[me]", "note": "n"})
        assert r.status_code == 200
        line = _notes(tmp_path).splitlines()[0]
        page_seg = line.split("] [", 1)[1].split("]", 1)[0]
        assert "[" not in page_seg and "]" not in page_seg, page_seg

    def test_long_note_is_capped(self, client, monkeypatch, tmp_path):
        """A pathologically long note is bounded (schema max_length=2000 → 422)."""
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "p", "note": "x" * 5000})
        assert r.status_code == 422
        assert not (tmp_path / "crew" / "REVIEW-NOTES.md").exists()

    def test_note_at_cap_is_accepted_and_bounded(self, client, monkeypatch, tmp_path):
        """A note exactly at the cap is accepted; the written note never exceeds 2000."""
        _enable(monkeypatch, tmp_path)
        r = client.post("/api/review-note", json={"page": "p", "note": "y" * 2000})
        assert r.status_code == 200
        line = _notes(tmp_path).splitlines()[0]
        note_seg = line.split("] ", 2)[2]
        assert len(note_seg) <= 2000
