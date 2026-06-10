---
name: drik
description: >
  Vision-driven UI testing of localhost web apps with Drik. Use when the user
  asks to write, run, or view user-journey / UI-flow / end-to-end tests for a
  web app — especially "test this flow like a user would" requests. Covers
  installing the drik CLI, authoring Markdown journey specs in drik/journeys/,
  running them with results saved to drik/results/<journey>/, and serving a
  localhost dashboard of the results with `drik serve`.
license: MIT
metadata:
  version: 0.2.0
  homepage: https://github.com/krishnarathore12/drik
compatibility: >
  Requires uv (or pipx) and Chromium via Playwright. Managed model serving
  needs Apple Silicon; other platforms need an external OpenAI-compatible
  vision model server (e.g. LM Studio).
---

# Drik — vision-driven user-journey tests

Drik runs UI tests written as plain Markdown. It drives a real Chromium
browser and, at each step, screenshots the page and asks a locally-hosted
vision-language model either *where to click* or *whether something is true on
screen*. Tests target **intent** ("the Sign in button"), not selectors, so
they survive markup changes. Exit code 0 = all steps passed.

## Setup (once per machine)

1. Check whether drik is installed: `drik --version`. If missing:

   ```bash
   uv tool install drik
   ```

   (No uv? `pipx install drik`, or install uv first:
   `curl -LsSf https://astral.sh/uv/install.sh | sh`.)

2. Install the browser (downloads Chromium once, shared machine-wide):

   ```bash
   uvx --from drik playwright install chromium
   ```

3. The vision model server: **on Apple Silicon, drik handles this itself.**
   If nothing answers at the endpoint, `drik run` auto-creates an mlx-vlm
   environment under `~/.drik/`, downloads Holo-3.1-4B (one-time, ~3 GB),
   and starts the server — so the FIRST run can take several minutes; give
   it a 15-minute timeout. To pay that cost up front instead:

   ```bash
   drik model start        # idempotent; reuses a running server
   drik model status       # up/down + which model is loaded
   drik model stop         # shut it down when done
   ```

   The server is left running between runs so re-runs are fast.
   On non-Apple-Silicon machines drik cannot host the model: ask the user to
   start an OpenAI-compatible **vision** server (e.g. LM Studio with
   Holo-3.1-4B on port 1234) — do not try to start one yourself there.

4. The app under test must be running on localhost. Note its URL — every run
   needs `--base-url`.

## Folder conventions (per project)

Create these at the project root the first time:

```
drik/
  journeys/    # one .md spec per user journey  (login.md, checkout.md, ...)
  results/     # one folder per journey, written by runs — never edit by hand
    login/
      report.json
      artifacts/*.png
```

Journey name = spec filename stem. Add `drik/results/` to `.gitignore`;
`drik/journeys/` should be committed.

## Writing a journey spec

A spec is Markdown: a `##` heading starts a named test case, each `-` bullet
is one step (leading verb + arguments). Quoted strings are literal input.
Paths resolve against `--base-url`.

| Verb | Example | Action |
|---|---|---|
| `goto` | `goto /login` | navigate to base URL + path |
| `click` | `click the "Sign in" button` | vision-locate element, click |
| `type` | `type "a@b.com" into the email field` | locate field, focus, type |
| `press` | `press Enter` | keyboard key |
| `scroll` | `scroll down` / `scroll up` | scroll viewport |
| `wait` | `wait 500ms` / `wait for the spinner to disappear` | delay or poll a visual condition |
| `verify` | `verify the dashboard is visible` | visual yes/no assertion |
| `verify not` | `verify not an error message is shown` | passes if answer is no |
| `screenshot` | `screenshot` | force-save a labeled screenshot |

Example `drik/journeys/login.md`:

```markdown
# Login journey

## Successful login
- goto /login
- type "test@example.com" into the email field
- type "hunter2" into the password field
- click the "Sign in" button
- wait for the dashboard to load
- verify the user dashboard is visible
- verify not an error message is shown
```

Authoring rules:
- One user journey per file; multiple `## test cases` per file are fine.
- Describe elements the way a human would see them ("the blue Submit
  button"), not by id/class — the model only sees the screenshot.
- Assert only things visible in the viewport; `scroll down` first if needed.
- Insert `wait for <condition>` after navigation or slow renders before
  asserting.

## Running journeys

Run each journey with its results routed to its own folder:

```bash
drik run drik/journeys/<name>.md \
  --base-url http://localhost:3000 \
  --report drik/results/<name>/report.json \
  --artifacts drik/results/<name>/artifacts
```

To run all journeys, loop over `drik/journeys/*.md` the same way (one command
per file so each journey keeps its own results folder). Runs are slow — a
local vision model is queried per step — so expect ~2–10 s per step and set
generous Bash timeouts (5+ min per journey).

`--model` can be omitted — drik asks the server which model it is serving.
Useful flags: `--headed` (visible browser), `--endpoint` (non-default model
server), `--model-repo` (different model for auto-serving),
`--no-auto-model` (fail instead of starting a server), `--coord-space pixel`
(UI-TARS-style models; keep the default `normalized_1000` for Holo/Qwen-VL).

After a run, read `report.json` to see which step failed and why
(`detail`, `model_answer`), and view the step's screenshot under
`artifacts/` to diagnose.

## Viewing results — dashboard

Serve a localhost dashboard of every journey's latest results:

```bash
drik serve drik/results --port 8123 --no-open
```

Run it in the background (it blocks), then give the user the URL
`http://127.0.0.1:8123/`. The index lists each user journey with pass/fail;
clicking a journey shows every test, step, failure detail, and screenshot.
The page re-scans the results folder on refresh, so leave the server running
across re-runs. If the port is busy, pick another with `--port`.

## Troubleshooting

- **Clicks land in empty space / wrong place** → wrong `--coord-space` for
  the model; flip it. `report.json` records the exact pixel clicked.
- **`could not launch browser`** → rerun the `playwright install chromium`
  step above.
- **Model errors / connection refused mid-run** → the model server died;
  check `~/.drik/mlx-server.log`, then `drik model start` and re-run.
- **Auto-start fails on non-Mac hardware** → expected; the user must run an
  external vision model server (step 3 of Setup).
- **Assertion flaps** → rephrase it more concretely ("the heading says
  'Welcome'" rather than "the page looks right").
