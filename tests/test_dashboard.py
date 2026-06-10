"""Tests for the `drik serve` dashboard: results scanning and HTML rendering."""

import json
from pathlib import Path

from drik.dashboard import Journey, render_index, render_journey, scan_results


def _write_journey(results: Path, name: str, *, ok: bool) -> Path:
    folder = results / name
    shots = folder / "artifacts"
    shots.mkdir(parents=True)
    (shots / "step_01_goto.png").write_bytes(b"\x89PNG fake")
    report = {
        "summary": {"passed": 1 if ok else 0, "total": 1, "ok": ok,
                    "exit_code": 0 if ok else 1,
                    "generated_at": "2026-06-10T00:00:00+00:00"},
        "tests": [{
            "name": "Successful login",
            "status": "pass" if ok else "fail",
            "steps": [{
                "verb": "goto", "description": "/login", "raw_line": "goto /login",
                "line_number": 2, "status": "pass" if ok else "fail",
                "duration_s": 1.2, "detail": "" if ok else "model said no",
                "model_answer": "", "coords": None,
                "screenshot": str(shots / "step_01_goto.png"), "attempts": 1,
            }],
        }],
    }
    (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return folder


def test_scan_finds_journeys_and_shots(tmp_path):
    _write_journey(tmp_path, "login", ok=True)
    _write_journey(tmp_path, "checkout", ok=False)
    (tmp_path / "not-a-journey").mkdir()  # no report.json -> ignored

    journeys = scan_results(tmp_path)
    assert [j.name for j in journeys] == ["checkout", "login"]
    assert journeys[1].ok and not journeys[0].ok
    assert "step_01_goto.png" in journeys[1].shots


def test_scan_missing_dir_is_empty(tmp_path):
    assert scan_results(tmp_path / "nope") == []


def test_scan_bad_json_reports_error(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "report.json").write_text("{not json", encoding="utf-8")
    (j,) = scan_results(tmp_path)
    assert j.report is None
    assert "report.json" in j.error


def test_index_lists_journeys_with_status(tmp_path):
    _write_journey(tmp_path, "login", ok=True)
    _write_journey(tmp_path, "checkout", ok=False)
    html = render_index(scan_results(tmp_path), tmp_path)
    assert "login" in html and "checkout" in html
    assert "/j/login" in html
    assert "badge pass" in html and "badge fail" in html


def test_index_empty_state(tmp_path):
    html = render_index([], tmp_path)
    assert "No journey results yet" in html


def test_journey_page_shows_steps_and_screenshot(tmp_path):
    _write_journey(tmp_path, "login", ok=False)
    (journey,) = scan_results(tmp_path)
    html = render_journey(journey)
    assert "Successful login" in html
    assert "goto /login" in html
    assert "model said no" in html                       # failure detail shown
    assert "/j/login/shot/step_01_goto.png" in html      # screenshot served by basename


def test_journey_page_escapes_html(tmp_path):
    journey = Journey(
        name="x", folder=tmp_path, report={
            "summary": {"passed": 0, "total": 1, "ok": False},
            "tests": [{"name": "<script>alert(1)</script>", "status": "fail",
                       "steps": []}],
        })
    html = render_journey(journey)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
