# Drik

**Vision-driven UI flow testing for localhost.** Write tests in Markdown, let a
local vision model click through your app like a human.

_Drik_ (दृक्, "sight / the act of seeing").

Each test is a plain Markdown file describing a flow in natural language ("click
the Sign in button", "verify the dashboard is visible"). Drik drives a real
Chromium browser and, at each step, screenshots the page and asks a locally-hosted
vision-language model either **where to click** or **whether something is true on
screen**. It reports pass/fail per step and exits non-zero on failure, so it slots
into CI like unit tests.

The bet: tests written against **intent** ("the Sign in button") instead of
**structure** (`#login-btn`) are more readable and survive markup changes that
break selector-based tests.

## Install

Drik uses [uv](https://docs.astral.sh/uv/) for package management.

```bash
uv sync                              # install dependencies
uv run playwright install chromium   # one-time browser download
```

## Quick start

1. Start a local OpenAI-compatible **vision** model server (see [Model serving](#model-serving)).
2. Have your app running on localhost.
3. Run a spec:

```bash
uv run drik run specs/auth.md \
  --base-url http://localhost:3000 \
  --endpoint http://localhost:1234/v1 \
  --model holo-3.1-4b \
  --report report.json
```

Exit code is `0` if every step in every test passed, `1` otherwise.

Before running real flows, validate the model + coordinate space with the bundled
calibration spec:

```bash
uv run drik run specs/calibration.md --headed
```

## How it works

```
read .md spec → launch browser at localhost → for each step:
    screenshot ──► model
        action step  → "where is <element>?"  → {x,y} → Playwright clicks/types
        assert step  → "is <statement> true?" → yes/no → record pass/fail
    → screenshot again → next step
→ print report, exit 0 (all pass) or 1 (any fail)
```

The model is used through exactly two primitives:

| Primitive | Input | Output | Used for |
|---|---|---|---|
| **Localization** | screenshot + element description | `{x, y}` in `[0,1000]` | clicks, typing targets |
| **Visual QA** | screenshot + question | `yes` / `no` | assertions, wait conditions |

Coordinates are rescaled from `[0,1000]` to the viewport's pixel size before
clicking. **The coordinate convention is model-dependent — getting it wrong is the
#1 cause of clicks landing in empty space:**

| Model family | Emits | Use |
|---|---|---|
| Qwen-VL grounding (UI-Venus, Holo, Qwen2.5/3-VL) | normalized `[0,1000]` | `--coord-space normalized_1000` (default) |
| UI-TARS | absolute pixels | `--coord-space pixel` |

`run.sh` picks this automatically from the model name. If your clicks are
consistently offset, you have the wrong space — flip it. The `--report` JSON
records the exact pixel each step clicked, so a mismatch is easy to spot.

## Spec format

A spec is a Markdown file. A `##` heading starts a named test case. Each `-`
bullet is one step: a leading verb plus arguments. Quoted strings are literal
input text. Paths are resolved against `--base-url`.

| Verb | Form | Action |
|---|---|---|
| `goto` | `goto /login` | Navigate to base URL + path (or a full URL) |
| `click` | `click the "Sign in" button` | Localize element, click it |
| `type` | `type "a@b.com" into the email field` | Localize field, focus, type text |
| `type` | `type "hello"` | Type into the currently focused element |
| `press` | `press Enter` | Keyboard key press |
| `scroll` | `scroll down` / `scroll up` | Scroll the viewport |
| `wait` | `wait 500ms` / `wait for the spinner to disappear` | Fixed delay, or poll a VQA condition until true/timeout |
| `verify` | `verify the dashboard is visible` | VQA assertion; passes if model answers yes |
| `verify not` | `verify not an error message is shown` | Passes if model answers no |
| `screenshot` | `screenshot` | Force-save a labeled screenshot |

`check` and `assert` are accepted as synonyms for `verify`.

### Example

```markdown
# Auth flows

## Successful login
- goto /login
- type "test@example.com" into the email field
- type "hunter2" into the password field
- click the "Sign in" button
- wait for the dashboard to load
- verify the user dashboard is visible
- verify not an error message is shown
```

## CLI

```
drik run <file-or-dir> [options]
```

| Flag | Default | Meaning |
|---|---|---|
| `--base-url` | `http://localhost:3000` | Root for relative paths |
| `--endpoint` | `http://localhost:1234/v1` | OpenAI-compatible model server URL |
| `--model` | `holo-3.1-4b` | Model name as the server exposes it |
| `--coord-space` | `normalized_1000` | `normalized_1000` or `pixel` |
| `--headed` / `--headless` | headless | Show or hide the browser window |
| `--viewport WxH` | `1280x800` | Browser viewport size |
| `--report PATH.json` | — | Write a machine-readable report |
| `--artifacts DIR` | `./drik-artifacts` | Where per-step screenshots go |
| `--retries N` | `1` | Retry a failed localization/action up to N times |
| `--timeout SECONDS` | `30` | Per-step model + browser timeout |
| `--no-color` | — | Disable colored console output |

## Model serving

Drik does **not** host the model — it connects to an external OpenAI-compatible
server over HTTP. The model must support **multimodal (image) input**.

> Drik targets Apple Silicon. The Holo model card's `vllm` / SGLang / GPU-Docker
> instructions are NVIDIA/CUDA only and **do not run on a Mac.**

Supported setups, in priority order:

1. **LM Studio** — GUI, one-click model load, built-in OpenAI server. Recommended
   default. Point `--endpoint` at its local server (`http://localhost:1234/v1`).
2. **mlx-vlm** — Apple MLX vision stack (`mlx-vlm`, *not* `mlx-lm`); ships an
   OpenAI-compatible server. Most native / fastest path.
3. **Ollama / llama.cpp GGUF** — only if a vision-capable build (with the `mmproj`
   projector) is confirmed working for this architecture.

**Footprint:** Holo-3.1-4B at 4-bit ≈ 2.5–3 GB RAM (fine on a 16 GB Mac);
8-bit ≈ 5–6 GB; BF16 ≈ 8–9 GB. The **9B at 8-bit (~11 GB) OOMs the Metal GPU on a
16 GB Mac** during vision prefill — stick to the 4B, or the 9B at 4-bit.

### Running Holo-3.1 on mlx-vlm (two gotchas)

`./run.sh` defaults to `pipenetwork/Holo-3.1-4B-MLX-8bit` and handles both of these,
but if you wire your own serving:

1. **Patch the vision-type guard.** Holo-3.1 reports `vision_config.model_type =
   "qwen3_5_vision"`, which mlx-vlm 0.6.0's `qwen3_vl/vision.py` rejects. Add the
   types to the allow-list (line ~200):
   ```python
   if self.model_type not in ["qwen3_vl", "qwen3_5", "qwen3_5_moe",
                              "qwen3_5_vision", "qwen3_5_moe_vision"]:
   ```
   Re-applying this after any `mlx-vlm` reinstall is required until upstream fixes it.
2. **Coordinates are normalized [0,1000]** for Holo (use `--coord-space
   normalized_1000`, the default), and the localize prompt must elicit `Click(x, y)`
   — Holo punts to the image center on a generic "output one point" prompt. Drik's
   built-in prompt already does this.

Drik sends `temperature=0` and disables thinking mode for determinism.

## Development

```bash
uv run pytest        # run the test suite
```

```
src/drik/
  cli.py       # arg parsing, entry point, exit code
  parser.py    # .md  -> Test/Step objects
  model.py     # vision-model client: localize() + ask()
  browser.py   # Playwright wrapper
  runner.py    # orchestration, retries, artifact capture
  report.py    # console + JSON output
```

## License

MIT
