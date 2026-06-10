"""Result data structures plus console and JSON reporting."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PASS = "pass"
FAIL = "fail"
ERROR = "error"  # an action couldn't be carried out (vs. an assertion answering "no")


@dataclass
class StepResult:
    verb: str
    description: str
    raw_line: str
    line_number: int
    status: str  # PASS | FAIL | ERROR
    duration_s: float
    detail: str = ""           # human-readable note (e.g. why it failed)
    model_answer: str = ""     # raw model text, when a model call was involved
    coords: tuple[int, int] | None = None  # localized click point, when applicable
    screenshot: str | None = None          # path to the screenshot the model saw
    attempts: int = 1


@dataclass
class TestResult:
    name: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.status == PASS for s in self.steps)

    @property
    def status(self) -> str:
        return PASS if self.passed else FAIL


@dataclass
class RunResult:
    tests: list[TestResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.tests)

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def tally(self) -> tuple[int, int]:
        passed = sum(1 for t in self.tests if t.passed)
        return passed, len(self.tests)


# -- console -----------------------------------------------------------------

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _c(text: str, color: str, *, use_color: bool) -> str:
    return f"{color}{text}{_RESET}" if use_color else text


_MARK = {PASS: "✓", FAIL: "✗", ERROR: "⚠"}
_COLOR = {PASS: _GREEN, FAIL: _RED, ERROR: _YELLOW}


def print_console(run: RunResult, *, use_color: bool = True) -> None:
    for test in run.tests:
        head_color = _GREEN if test.passed else _RED
        print(_c(f"\n{_BOLD}{test.name}{_RESET}", head_color, use_color=use_color)
              if use_color else f"\n{test.name}")
        for step in test.steps:
            mark = _MARK.get(step.status, "?")
            line = f"  {mark} {step.verb} {step.description}".rstrip()
            line = _c(line, _COLOR.get(step.status, _RESET), use_color=use_color)
            timing = _c(f" ({step.duration_s:.1f}s)", _DIM, use_color=use_color)
            print(line + timing)
            if step.status != PASS and step.detail:
                print(_c(f"      → {step.detail}", _DIM, use_color=use_color))
            if step.status != PASS and step.screenshot:
                print(_c(f"      screenshot: {step.screenshot}", _DIM, use_color=use_color))

    passed, total = run.tally()
    summary = f"\n{passed}/{total} tests passed"
    color = _GREEN if passed == total else _RED
    print(_c(summary, color, use_color=use_color) if use_color else summary)


# -- JSON --------------------------------------------------------------------

def write_json(run: RunResult, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    passed, total = run.tally()
    doc = {
        "summary": {
            "passed": passed,
            "total": total,
            "ok": run.passed,
            "exit_code": run.exit_code,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "tests": [
            {
                "name": t.name,
                "status": t.status,
                "steps": [_step_to_dict(s) for s in t.steps],
            }
            for t in run.tests
        ],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _step_to_dict(step: StepResult) -> dict:
    d = asdict(step)
    if d.get("coords") is not None:
        d["coords"] = list(d["coords"])
    return d
