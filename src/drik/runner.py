"""Orchestrate tests: dispatch steps by verb, drive the browser via the model,
capture artifacts, and collect results.

On a failed assertion or unrecoverable action error the step is marked failed,
its screenshot is saved, and the runner continues to the next *test* (it does
not abort the whole run).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from .browser import Browser
from .model import LocalizationError, ModelClient, ModelError
from .parser import Step, Test
from .report import ERROR, FAIL, PASS, RunResult, StepResult, TestResult


@dataclass
class RunnerConfig:
    artifacts_dir: Path = Path("./drik-artifacts")
    retries: int = 1            # extra attempts for a failed localization/action
    poll_interval_ms: int = 500  # for `wait for <condition>`
    poll_timeout_ms: int = 10_000
    confirm_actions: bool = False  # optional VQA post-action confirmation (unused by default)


class Runner:
    def __init__(self, browser: Browser, model: ModelClient, config: RunnerConfig):
        self.browser = browser
        self.model = model
        self.config = config
        self.config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._shot_counter = 0

    def run(self, tests: list[Test]) -> RunResult:
        result = RunResult()
        for test in tests:
            result.tests.append(self._run_test(test))
        return result

    def _run_test(self, test: Test) -> TestResult:
        tr = TestResult(name=test.name)
        slug = _slug(test.name)
        for idx, step in enumerate(test.steps, start=1):
            sr = self._run_step(step, test_slug=slug, step_index=idx)
            tr.steps.append(sr)
            # On a hard error or assertion failure, stop this test and move on.
            if sr.status != PASS:
                break
        return tr

    def _run_step(self, step: Step, *, test_slug: str, step_index: int) -> StepResult:
        start = time.monotonic()
        attempts = 0
        last_detail = ""
        coords = None
        model_answer = ""
        status = ERROR

        # Localization/action steps get retried; assertions do not (a "no" is a
        # real answer, not a transient failure).
        max_attempts = 1 + max(0, self.config.retries)
        retriable = step.verb in ("click", "type")

        while True:
            attempts += 1
            try:
                status, last_detail, coords, model_answer = self._dispatch(step)
                if status == PASS or not retriable:
                    break
            except ModelError as e:
                status, last_detail = ERROR, str(e)
            except Exception as e:  # browser/Playwright errors
                status, last_detail = ERROR, f"{type(e).__name__}: {e}"

            if not retriable or attempts >= max_attempts or status == PASS:
                break

        shot = self._save_screenshot(test_slug, step_index, step.verb)
        return StepResult(
            verb=step.verb,
            description=step.description,
            raw_line=step.raw_line,
            line_number=step.line_number,
            status=status,
            duration_s=time.monotonic() - start,
            detail=last_detail,
            model_answer=model_answer,
            coords=coords,
            screenshot=str(shot),
            attempts=attempts,
        )

    # -- per-verb dispatch --------------------------------------------------
    # Each handler returns (status, detail, coords, model_answer).

    def _dispatch(self, step: Step):
        handler = getattr(self, f"_do_{step.verb}", None)
        if handler is None:
            return ERROR, f"no handler for verb {step.verb!r}", None, ""
        return handler(step)

    def _do_goto(self, step: Step):
        self.browser.goto(step.args["path"])
        return PASS, f"navigated to {step.args['path']}", None, ""

    def _do_click(self, step: Step):
        image = self.browser.screenshot()
        x, y = self.model.localize(image, step.description, viewport=self.browser.viewport)
        self.browser.click(x, y)
        return PASS, f"clicked at ({x},{y})", (x, y), ""

    def _do_type(self, step: Step):
        text = step.args["text"]
        target = step.args.get("target", "")
        coords = None
        if target:
            image = self.browser.screenshot()
            x, y = self.model.localize(image, target, viewport=self.browser.viewport)
            self.browser.click(x, y)  # focus the field
            coords = (x, y)
        self.browser.type_text(text)
        where = f"into {target}" if target else "into focused element"
        return PASS, f"typed {text!r} {where}", coords, ""

    def _do_press(self, step: Step):
        self.browser.press(step.args["key"])
        return PASS, f"pressed {step.args['key']}", None, ""

    def _do_scroll(self, step: Step):
        self.browser.scroll(step.args["direction"])
        return PASS, f"scrolled {step.args['direction']}", None, ""

    def _do_screenshot(self, step: Step):
        # The wrapper always saves a screenshot afterward; this verb just forces one.
        return PASS, "screenshot captured", None, ""

    def _do_wait(self, step: Step):
        if "duration_ms" in step.args:
            self.browser.wait(step.args["duration_ms"])
            return PASS, f"waited {step.args['duration_ms']}ms", None, ""
        return self._wait_for_condition(step.args["condition"])

    def _wait_for_condition(self, condition: str):
        deadline = time.monotonic() + self.config.poll_timeout_ms / 1000.0
        last_answer = ""
        question = _as_question(condition)
        while True:
            image = self.browser.screenshot()
            answer, raw = self.model.ask(image, question)
            last_answer = raw
            if answer:
                return PASS, f"condition met: {condition}", None, raw
            if time.monotonic() >= deadline:
                return FAIL, f"timed out waiting for: {condition}", None, last_answer
            self.browser.wait(self.config.poll_interval_ms)

    def _do_verify(self, step: Step):
        return self._verify(step, expect=True)

    def _do_verify_not(self, step: Step):
        return self._verify(step, expect=False)

    def _verify(self, step: Step, *, expect: bool):
        image = self.browser.screenshot()
        question = _as_question(step.description)
        answer, raw = self.model.ask(image, question)
        ok = (answer == expect)
        if ok:
            detail = f"model said {'yes' if answer else 'no'} (as expected)"
            return PASS, detail, None, raw
        detail = (
            f"expected {'yes' if expect else 'no'} but model said "
            f"{'yes' if answer else 'no'}: {step.description!r}"
        )
        return FAIL, detail, None, raw

    # -- artifacts ----------------------------------------------------------

    def _save_screenshot(self, test_slug: str, step_index: int, verb: str) -> Path:
        self._shot_counter += 1
        name = f"{test_slug}_{step_index:02d}_{verb}.png"
        path = self.config.artifacts_dir / name
        try:
            path.write_bytes(self.browser.screenshot())
        except Exception:
            # A screenshot failure shouldn't mask the step's real result.
            pass
        return path


def _as_question(statement: str) -> str:
    """Turn a declarative condition into a yes/no question for the VQA model."""
    s = statement.strip().rstrip("?.")
    if re.match(r"^(is|are|does|do|has|have|can|was|were|will)\b", s, re.I):
        return s + "?"
    return f"Is it true that {s}?"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "test"
