"""Parse Markdown flow specs into Test/Step objects.

A spec is a Markdown file. A ``##`` heading starts a named test case. Each ``-``
bullet under it is one step: a leading verb plus arguments. Quoted strings are
literal input text.

Grammar (one step per bullet)::

    goto /login
    goto https://example.com/login
    click the "Sign in" button
    type "a@b.com" into the email field
    type "hello"
    press Enter
    scroll down
    wait 500ms
    wait for the spinner to disappear
    verify the dashboard is visible
    verify not an error message is shown
    screenshot

``check`` and ``assert`` are accepted as synonyms for ``verify``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Canonical verbs the runner dispatches on.
VERBS = {
    "goto",
    "click",
    "type",
    "press",
    "scroll",
    "wait",
    "verify",
    "verify_not",
    "screenshot",
}

# Synonyms collapse to a canonical verb.
SYNONYMS = {
    "check": "verify",
    "assert": "verify",
}

_QUOTED = re.compile(r'"([^"]*)"')
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds|m|min)?$", re.I)


class SpecError(Exception):
    """A malformed spec line, carrying file + line number context."""

    def __init__(self, message: str, *, file: str | Path, line_number: int, raw_line: str = ""):
        self.file = str(file)
        self.line_number = line_number
        self.raw_line = raw_line
        detail = f"{self.file}:{line_number}: {message}"
        if raw_line:
            detail += f"\n    {raw_line.strip()}"
        super().__init__(detail)


@dataclass
class Step:
    """One step of a test case."""

    verb: str
    # Free-text element/condition description with quoted literals stripped out.
    description: str
    # Literal strings pulled from double quotes, in order.
    literals: list[str]
    raw_line: str
    line_number: int
    # Verb-specific extras (e.g. {"path": "/login"}, {"key": "Enter"},
    # {"direction": "down"}, {"duration_ms": 500}, {"condition": "..."}).
    args: dict = field(default_factory=dict)

    @property
    def literal(self) -> str | None:
        """First quoted literal, if any (the common single-argument case)."""
        return self.literals[0] if self.literals else None


@dataclass
class Test:
    """A named test case: an ordered list of steps."""

    name: str
    steps: list[Step]
    line_number: int


def parse_file(path: str | Path) -> list[Test]:
    """Parse a Markdown spec file into a list of Test objects."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_text(text, file=path)


def parse_text(text: str, *, file: str | Path = "<string>") -> list[Test]:
    """Parse Markdown spec text into a list of Test objects."""
    tests: list[Test] = []
    current: Test | None = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue

        # `## Heading` opens a new test case. `#` (single) is a document title.
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level, title = heading.group(1), heading.group(2).strip()
            if len(level) >= 2:
                current = Test(name=title, steps=[], line_number=lineno)
                tests.append(current)
            # A top-level `#` title is just a document header — ignore it.
            continue

        # `- step` or `* step` bullet.
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if bullet:
            if current is None:
                raise SpecError(
                    "step appears before any '## test case' heading",
                    file=file,
                    line_number=lineno,
                    raw_line=raw,
                )
            step = _parse_step(bullet.group(1).strip(), raw_line=raw, line_number=lineno, file=file)
            current.steps.append(step)
            continue

        # Anything else that isn't blank/heading/bullet is unexpected prose.
        # We ignore non-bullet text so specs can carry explanatory paragraphs.

    return tests


def _describe(text: str) -> str:
    """Build a step description: keep quoted content, drop the quote marks, collapse whitespace."""
    unquoted = _QUOTED.sub(lambda m: m.group(1), text)
    return re.sub(r"\s+", " ", unquoted).strip()


def _parse_step(body: str, *, raw_line: str, line_number: int, file: str | Path) -> Step:
    first = body.split(maxsplit=1)
    if not first:
        raise SpecError("empty step", file=file, line_number=line_number, raw_line=raw_line)

    token = first[0].lower()
    rest = first[1] if len(first) > 1 else ""

    # Description = the remainder after the verb. Keep the *text* inside quotes
    # (drop only the quote marks): for `click`/`verify` the quoted words are the
    # element's label or the phrase to check — the best locator — so they must
    # stay in the description. `type` later pulls its quoted value back out as
    # input and keeps only the target field as its description.
    literals = _QUOTED.findall(rest)
    description = _describe(rest)

    # `verify not ...` collapses to the verify_not verb.
    verb = SYNONYMS.get(token, token)
    if verb == "verify" and rest.lower().startswith("not "):
        verb = "verify_not"
        rest = rest[4:].strip()
        # Re-derive literals/description for the post-"not" remainder.
        literals = _QUOTED.findall(rest)
        description = _describe(rest)

    if verb not in VERBS:
        raise SpecError(
            f"unrecognized verb {token!r} (expected one of: "
            f"goto, click, type, press, scroll, wait, verify, verify not, screenshot, "
            f"check, assert)",
            file=file,
            line_number=line_number,
            raw_line=raw_line,
        )

    args: dict = {}

    if verb == "goto":
        target = rest.strip()
        if not target:
            raise SpecError("goto needs a path or URL", file=file, line_number=line_number, raw_line=raw_line)
        args["path"] = target

    elif verb == "press":
        key = rest.strip()
        if not key:
            raise SpecError("press needs a key (e.g. 'press Enter')", file=file, line_number=line_number, raw_line=raw_line)
        args["key"] = _normalize_key(key)

    elif verb == "scroll":
        direction = rest.strip().lower() or "down"
        if direction not in ("up", "down"):
            raise SpecError(
                f"scroll direction must be 'up' or 'down', got {direction!r}",
                file=file,
                line_number=line_number,
                raw_line=raw_line,
            )
        args["direction"] = direction

    elif verb == "wait":
        _parse_wait(rest, args, raw_line=raw_line, line_number=line_number, file=file)

    elif verb == "type":
        # Either `type "text" into the X field` or `type "text"` (focused element).
        if not literals:
            raise SpecError(
                'type needs quoted text (e.g. \'type "hello" into the email field\')',
                file=file,
                line_number=line_number,
                raw_line=raw_line,
            )
        args["text"] = literals[0]
        # Target description follows "into"; absent means type into focused element.
        m = re.search(r"\binto\b(.*)$", description, re.I)
        target = m.group(1).strip() if m else ""
        args["target"] = target  # "" => type into currently focused element
        description = target

    elif verb == "click":
        if not description:
            raise SpecError("click needs an element description", file=file, line_number=line_number, raw_line=raw_line)

    elif verb in ("verify", "verify_not"):
        if not description:
            raise SpecError(
                f"{verb.replace('_', ' ')} needs a statement to check",
                file=file,
                line_number=line_number,
                raw_line=raw_line,
            )

    # screenshot takes an optional label (the description).

    return Step(
        verb=verb,
        description=description,
        literals=literals,
        raw_line=raw_line,
        line_number=line_number,
        args=args,
    )


def _parse_wait(rest: str, args: dict, *, raw_line: str, line_number: int, file: str | Path) -> None:
    rest = rest.strip()
    if not rest:
        raise SpecError(
            "wait needs a duration (e.g. 'wait 500ms') or condition "
            "(e.g. 'wait for the spinner to disappear')",
            file=file,
            line_number=line_number,
            raw_line=raw_line,
        )

    # `wait for <condition>` => poll a VQA condition.
    cond = re.match(r"^for\s+(.*)$", rest, re.I)
    if cond:
        args["condition"] = cond.group(1).strip()
        return

    # Otherwise a fixed duration like `500ms`, `2s`, `1.5 sec`.
    dur = _DURATION.match(rest)
    if not dur:
        raise SpecError(
            f"could not parse wait duration {rest!r} (try '500ms' or '2s')",
            file=file,
            line_number=line_number,
            raw_line=raw_line,
        )
    value = float(dur.group(1))
    unit = (dur.group(2) or "ms").lower()
    if unit in ("ms",):
        ms = value
    elif unit in ("s", "sec", "secs", "seconds"):
        ms = value * 1000
    elif unit in ("m", "min"):
        ms = value * 60_000
    else:  # pragma: no cover - regex constrains units
        ms = value
    args["duration_ms"] = int(ms)


# Map friendly key names to Playwright key identifiers where they differ.
_KEY_ALIASES = {
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "esc": "Escape",
    "escape": "Escape",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
}


def _normalize_key(key: str) -> str:
    """Normalize a human key name to a Playwright key, preserving chords like 'Control+A'."""
    if "+" in key:
        parts = [p.strip() for p in key.split("+")]
        return "+".join(_normalize_key(p) for p in parts)
    return _KEY_ALIASES.get(key.lower(), key if len(key) == 1 else key.capitalize())
