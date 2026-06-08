import pytest

from drik.parser import SpecError, parse_text


def test_headings_and_steps():
    text = """
# Auth flows

## Successful login
- goto /login
- click the "Sign in" button
- verify the dashboard is visible
"""
    tests = parse_text(text)
    assert len(tests) == 1
    t = tests[0]
    assert t.name == "Successful login"
    assert [s.verb for s in t.steps] == ["goto", "click", "verify"]
    assert t.steps[0].args["path"] == "/login"


def test_top_level_title_is_not_a_test():
    text = "# Just a title\n## Real test\n- goto /\n"
    tests = parse_text(text)
    assert len(tests) == 1
    assert tests[0].name == "Real test"


def test_type_into_field():
    [t] = parse_text('## x\n- type "a@b.com" into the email field\n')
    step = t.steps[0]
    assert step.verb == "type"
    assert step.args["text"] == "a@b.com"
    assert step.args["target"] == "the email field"


def test_type_into_focused_element():
    [t] = parse_text('## x\n- type "hello"\n')
    step = t.steps[0]
    assert step.verb == "type"
    assert step.args["text"] == "hello"
    assert step.args["target"] == ""


def test_verify_not_collapses():
    [t] = parse_text("## x\n- verify not an error message is shown\n")
    step = t.steps[0]
    assert step.verb == "verify_not"
    assert "error message" in step.description
    assert step.description.lower().startswith("an error")


def test_synonyms():
    [t] = parse_text("## x\n- check the dashboard is visible\n- assert the modal is closed\n")
    assert t.steps[0].verb == "verify"
    assert t.steps[1].verb == "verify"


def test_quoted_label_kept_in_description():
    # For click, the quoted text is the element's label — the best locator — so
    # it must stay in the description (only the quote marks are dropped).
    [t] = parse_text('## x\n- click the blue "Sign in" button\n')
    step = t.steps[0]
    assert step.literals == ["Sign in"]
    assert '"' not in step.description
    assert step.description == "the blue Sign in button"


def test_verify_keeps_quoted_phrase():
    [t] = parse_text('## x\n- verify an "invalid credentials" error is shown\n')
    step = t.steps[0]
    assert step.description == "an invalid credentials error is shown"


@pytest.mark.parametrize(
    "line,unit_ms",
    [
        ("- wait 500ms", 500),
        ("- wait 2s", 2000),
        ("- wait 1.5 sec", 1500),
        ("- wait 1m", 60000),
    ],
)
def test_wait_durations(line, unit_ms):
    [t] = parse_text(f"## x\n{line}\n")
    assert t.steps[0].args["duration_ms"] == unit_ms


def test_wait_for_condition():
    [t] = parse_text("## x\n- wait for the spinner to disappear\n")
    step = t.steps[0]
    assert step.args["condition"] == "the spinner to disappear"
    assert "duration_ms" not in step.args


def test_press_key_normalized():
    [t] = parse_text("## x\n- press Enter\n- press esc\n")
    assert t.steps[0].args["key"] == "Enter"
    assert t.steps[1].args["key"] == "Escape"


def test_scroll_directions():
    [t] = parse_text("## x\n- scroll down\n- scroll up\n")
    assert t.steps[0].args["direction"] == "down"
    assert t.steps[1].args["direction"] == "up"


def test_unknown_verb_errors_with_line_number():
    with pytest.raises(SpecError) as exc:
        parse_text("## x\n- frobnicate the widget\n", file="spec.md")
    assert "frobnicate" in str(exc.value)
    assert "spec.md:2" in str(exc.value)


def test_step_before_heading_errors():
    with pytest.raises(SpecError):
        parse_text("- goto /\n")


def test_goto_requires_target():
    with pytest.raises(SpecError):
        parse_text("## x\n- goto\n")
