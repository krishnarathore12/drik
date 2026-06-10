"""Command-line entry point for Drik.

Usage:
    drik run <file-or-dir> [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, serving
from .browser import Browser
from .model import ModelClient
from .parser import SpecError, Test, parse_file
from .report import print_console, write_json
from .runner import Runner, RunnerConfig
from .serving import DEFAULT_REPO


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "model":
        return _cmd_model(args)

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drik",
        description="Vision-driven UI flow testing for localhost.",
    )
    p.add_argument("--version", action="version", version=f"drik {__version__}")
    sub = p.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run a spec file or a directory of specs")
    run.add_argument("path", help="a .md spec file or a directory of them")
    run.add_argument("--base-url", default="http://localhost:3000",
                     help="root for relative paths (default: http://localhost:3000)")
    run.add_argument("--endpoint", default="http://localhost:1234/v1",
                     help="OpenAI-compatible model server URL (default: LM Studio)")
    run.add_argument("--model", default=None,
                     help="model name as the server exposes it "
                          "(default: first model the server reports)")
    run.add_argument("--model-repo", default=None, metavar="HF_REPO",
                     help="model to auto-serve when no server is running "
                          f"(default: {DEFAULT_REPO})")
    run.add_argument("--no-auto-model", action="store_true",
                     help="fail instead of starting a model server when the "
                          "endpoint is unreachable")
    run.add_argument("--coord-space", default="normalized_1000",
                     choices=["normalized_1000", "pixel"],
                     help="how to interpret the model's raw coordinates")

    headmode = run.add_mutually_exclusive_group()
    headmode.add_argument("--headed", dest="headless", action="store_false",
                          help="show the browser window")
    headmode.add_argument("--headless", dest="headless", action="store_true",
                          help="run the browser headless (default)")
    run.set_defaults(headless=True)

    run.add_argument("--viewport", default="1280x800", metavar="WxH",
                     help="browser viewport size (default: 1280x800)")
    run.add_argument("--report", metavar="PATH.json",
                     help="write a machine-readable JSON report")
    run.add_argument("--artifacts", default="./drik-artifacts", metavar="DIR",
                     help="directory for per-step screenshots (default: ./drik-artifacts)")
    run.add_argument("--retries", type=int, default=1,
                     help="retry a failed localization/action up to N times (default: 1)")
    run.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS",
                     help="per-step model + browser timeout (default: 30)")
    run.add_argument("--no-color", action="store_true", help="disable colored output")

    serve = sub.add_parser("serve", help="serve a localhost dashboard of journey results")
    serve.add_argument("results", nargs="?", default="./drik/results",
                       help="results directory, one subfolder per journey "
                            "(default: ./drik/results)")
    serve.add_argument("--host", default="127.0.0.1",
                       help="address to bind (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8123,
                       help="port to listen on (default: 8123)")
    serve.add_argument("--no-open", action="store_true",
                       help="don't open the dashboard in a browser")

    model = sub.add_parser(
        "model", help="manage drik's own local vision-model server "
                      "(Apple Silicon, via mlx-vlm)")
    msub = model.add_subparsers(dest="model_command", required=True)

    mstart = msub.add_parser("start", help="download (if needed) and start the model server")
    mstart.add_argument("--repo", default=DEFAULT_REPO, metavar="HF_REPO",
                        help=f"Hugging Face model repo (default: {DEFAULT_REPO})")
    mstart.add_argument("--port", type=int, default=1234,
                        help="port to serve on (default: 1234)")
    mstart.add_argument("--wait", type=float, default=300.0, metavar="SECONDS",
                        help="how long to wait for the server to come up (default: 300)")

    mstop = msub.add_parser("stop", help="stop the managed model server")
    mstop.add_argument("--port", type=int, default=1234)

    mstatus = msub.add_parser("status", help="check whether a model server is answering")
    mstatus.add_argument("--port", type=int, default=1234)
    return p


def _cmd_run(args) -> int:
    try:
        viewport = _parse_viewport(args.viewport)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    specs = _collect_specs(args.path)
    if specs is None:
        return 2
    if not specs:
        print(f"error: no .md specs found at {args.path}", file=sys.stderr)
        return 2

    # Parse everything up front so a malformed spec fails fast with a clear error.
    tests: list[Test] = []
    try:
        for spec in specs:
            tests.extend(parse_file(spec))
    except SpecError as e:
        print(f"spec error: {e}", file=sys.stderr)
        return 2

    if not tests:
        print("error: specs contained no '## test case' headings", file=sys.stderr)
        return 2

    # Make sure a model server is answering — starting one ourselves if the
    # endpoint is local, we're on Apple Silicon, and auto-start isn't disabled.
    repo = args.model_repo or DEFAULT_REPO
    if not serving.endpoint_alive(args.endpoint):
        if args.no_auto_model:
            print(f"error: no model server at {args.endpoint} "
                  "(--no-auto-model given)", file=sys.stderr)
            return 2
        if not serving.ensure_running(args.endpoint, repo):
            return 2
    model_name = args.model or serving.first_model_id(args.endpoint) or repo

    model = ModelClient(
        endpoint=args.endpoint,
        model=model_name,
        coord_space=args.coord_space,
        timeout=args.timeout,
    )
    browser = Browser(
        base_url=args.base_url,
        headless=args.headless,
        viewport=viewport,
        timeout_ms=int(args.timeout * 1000),
    )
    config = RunnerConfig(
        artifacts_dir=Path(args.artifacts),
        retries=args.retries,
    )

    try:
        browser.start()
    except Exception as e:
        print(f"error: could not launch browser: {e}", file=sys.stderr)
        print("hint: run 'uv run playwright install chromium' first.", file=sys.stderr)
        model.close()
        return 2

    try:
        runner = Runner(browser, model, config)
        result = runner.run(tests)
    finally:
        browser.close()
        model.close()

    print_console(result, use_color=not args.no_color)
    if args.report:
        write_json(result, args.report)
        print(f"\nreport written to {args.report}")

    return result.exit_code


def _cmd_model(args) -> int:
    endpoint = f"http://127.0.0.1:{args.port}/v1"
    if args.model_command == "start":
        if serving.endpoint_alive(endpoint):
            print(f"a model server is already answering at {endpoint}")
            return 0
        if not serving.supported_platform():
            print("error: 'drik model start' requires Apple Silicon (mlx-vlm); "
                  "on other platforms run an OpenAI-compatible vision server "
                  "such as LM Studio.", file=sys.stderr)
            return 2
        return 0 if serving.start_server(args.repo, args.port, wait_s=args.wait) else 1
    if args.model_command == "stop":
        return 0 if serving.stop_server(args.port) else 1
    if args.model_command == "status":
        if serving.endpoint_alive(endpoint):
            model = serving.first_model_id(endpoint)
            print(f"up at {endpoint}" + (f" (model: {model})" if model else ""))
            return 0
        print(f"down — nothing answering at {endpoint}")
        return 1
    return 2


def _cmd_serve(args) -> int:
    from .dashboard import serve

    results_dir = Path(args.results)
    if not results_dir.is_dir():
        print(f"note: {results_dir} does not exist yet; the dashboard will be "
              "empty until journeys are run", file=sys.stderr)
    return serve(results_dir, host=args.host, port=args.port,
                 open_browser=not args.no_open)


def _collect_specs(path_str: str) -> list[Path] | None:
    path = Path(path_str)
    if not path.exists():
        print(f"error: path not found: {path}", file=sys.stderr)
        return None
    if path.is_file():
        return [path]
    return sorted(path.glob("*.md"))


def _parse_viewport(s: str) -> tuple[int, int]:
    m = s.lower().replace(" ", "").split("x")
    if len(m) != 2 or not all(part.isdigit() for part in m):
        raise ValueError(f"invalid viewport {s!r}; expected WxH like 1280x800")
    return int(m[0]), int(m[1])


if __name__ == "__main__":
    sys.exit(main())
