"""`drik serve` — a localhost dashboard over a directory of journey results.

Expected layout (one subfolder per user journey):

    <results>/
      login/
        report.json          # written by `drik run --report`
        artifacts/*.png      # written by `drik run --artifacts`
      checkout/
        report.json
        ...

The directory is re-scanned on every request, so re-running a journey and
refreshing the page shows fresh results. The server is read-only and binds
to localhost by default. Stdlib only — no extra dependencies.
"""

from __future__ import annotations

import html
import json
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote


@dataclass
class Journey:
    name: str
    folder: Path
    report: dict | None
    error: str = ""
    shots: dict[str, Path] = field(default_factory=dict)  # basename -> file

    @property
    def summary(self) -> dict:
        return (self.report or {}).get("summary", {})

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))

    @property
    def tally(self) -> str:
        s = self.summary
        if "passed" in s and "total" in s:
            return f"{s['passed']}/{s['total']} tests passed"
        return "no summary"


def scan_results(results_dir: Path) -> list[Journey]:
    """One Journey per subfolder of results_dir that contains a report.json."""
    journeys: list[Journey] = []
    if not results_dir.is_dir():
        return journeys
    for folder in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        report_path = folder / "report.json"
        if not report_path.is_file():
            continue
        report: dict | None
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            error = ""
        except (OSError, json.JSONDecodeError) as e:
            report, error = None, f"could not read report.json: {e}"
        shots = {p.name: p for p in sorted(folder.rglob("*.png"))}
        journeys.append(Journey(folder.name, folder, report, error, shots))
    return journeys


# -- HTML rendering -----------------------------------------------------------

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem; background: #101418; color: #e6e6e6;
       font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
a { color: #7ab8f5; text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
h1 a { color: inherit; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; }
.sub { color: #8a93a0; margin-bottom: 1.5rem; }
.badge { display: inline-block; padding: .1rem .55rem; border-radius: 99px;
         font-size: .8rem; font-weight: 600; vertical-align: middle; }
.badge.pass { background: #143d23; color: #5fd38a; }
.badge.fail { background: #46181b; color: #f2848a; }
.badge.error { background: #4a3a12; color: #e8c46a; }
.cards { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.card { background: #181e25; border: 1px solid #232b35; border-radius: 10px;
        padding: 1rem; display: block; color: inherit; }
.card:hover { border-color: #3a4a5e; text-decoration: none; }
.card .name { font-weight: 600; margin-bottom: .35rem; }
.card .meta { color: #8a93a0; font-size: .85rem; }
table { border-collapse: collapse; width: 100%; }
td { padding: .45rem .6rem; border-top: 1px solid #232b35; vertical-align: top; }
td.mark { width: 1.5rem; text-align: center; }
td.verb { width: 6rem; color: #8a93a0; }
td.dur { width: 5rem; color: #8a93a0; text-align: right; white-space: nowrap; }
td.shot { width: 132px; }
.detail { color: #c9a36a; font-size: .85rem; margin-top: .2rem; }
.pass-mark { color: #5fd38a; } .fail-mark { color: #f2848a; } .error-mark { color: #e8c46a; }
img.thumb { width: 120px; border: 1px solid #232b35; border-radius: 6px; display: block; }
.empty { color: #8a93a0; padding: 3rem 0; text-align: center; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )


def _badge(ok: bool) -> str:
    return ("<span class='badge pass'>pass</span>" if ok
            else "<span class='badge fail'>fail</span>")


def render_index(journeys: list[Journey], results_dir: Path) -> str:
    body = ["<h1>Drik — user journeys</h1>",
            f"<div class='sub'>{html.escape(str(results_dir))}</div>"]
    if not journeys:
        body.append("<div class='empty'>No journey results yet.<br>"
                    "Run <code>drik run &lt;journey&gt;.md --report "
                    "&lt;results&gt;/&lt;name&gt;/report.json</code> "
                    "and refresh this page.</div>")
    else:
        cards = []
        for j in journeys:
            href = f"/j/{quote(j.name)}"
            status = ("<span class='badge error'>error</span>" if j.report is None
                      else _badge(j.ok))
            when = j.summary.get("generated_at", "")
            meta = html.escape(j.error if j.report is None else j.tally)
            when_html = f"<div class='meta'>{html.escape(when)}</div>" if when else ""
            cards.append(
                f"<a class='card' href='{href}'>"
                f"<div class='name'>{html.escape(j.name)} {status}</div>"
                f"<div class='meta'>{meta}</div>{when_html}</a>"
            )
        body.append(f"<div class='cards'>{''.join(cards)}</div>")
    return _page("Drik dashboard", "".join(body))


_MARKS = {"pass": ("✓", "pass-mark"), "fail": ("✗", "fail-mark"), "error": ("⚠", "error-mark")}


def render_journey(journey: Journey) -> str:
    body = [f"<h1><a href='/'>Drik</a> / {html.escape(journey.name)}</h1>"]
    if journey.report is None:
        body.append(f"<div class='sub'>{html.escape(journey.error)}</div>")
        return _page(journey.name, "".join(body))

    when = journey.summary.get("generated_at", "")
    sub = journey.tally + (f" · {when}" if when else "")
    body.append(f"<div class='sub'>{html.escape(sub)} {_badge(journey.ok)}</div>")

    for test in journey.report.get("tests", []):
        ok = test.get("status") == "pass"
        body.append(f"<h2>{html.escape(test.get('name', '?'))} {_badge(ok)}</h2>")
        rows = []
        for step in test.get("steps", []):
            mark, cls = _MARKS.get(step.get("status", ""), ("?", ""))
            desc = html.escape(f"{step.get('verb', '')} {step.get('description', '')}".strip())
            detail = step.get("detail", "")
            detail_html = (f"<div class='detail'>{html.escape(detail)}</div>"
                           if step.get("status") != "pass" and detail else "")
            shot_html = ""
            shot = step.get("screenshot")
            if shot:
                base = Path(shot).name
                if base in journey.shots:
                    src = f"/j/{quote(journey.name)}/shot/{quote(base)}"
                    shot_html = (f"<a href='{src}' target='_blank'>"
                                 f"<img class='thumb' loading='lazy' src='{src}'></a>")
            dur = step.get("duration_s")
            dur_html = f"{dur:.1f}s" if isinstance(dur, (int, float)) else ""
            rows.append(
                f"<tr><td class='mark {cls}'>{mark}</td>"
                f"<td>{desc}{detail_html}</td>"
                f"<td class='dur'>{dur_html}</td>"
                f"<td class='shot'>{shot_html}</td></tr>"
            )
        body.append(f"<table>{''.join(rows)}</table>")
    return _page(f"{journey.name} — Drik", "".join(body))


# -- HTTP server --------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    results_dir: Path  # injected by serve()

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parts = [unquote(p) for p in self.path.split("?", 1)[0].split("/") if p]
        if parts == ["favicon.ico"]:
            self.send_response(204)
            self.end_headers()
            return
        journeys = scan_results(self.results_dir)

        if not parts:
            return self._send_html(render_index(journeys, self.results_dir))

        if parts[0] == "j" and len(parts) >= 2:
            journey = next((j for j in journeys if j.name == parts[1]), None)
            if journey is None:
                return self._send_404()
            if len(parts) == 2:
                return self._send_html(render_journey(journey))
            if len(parts) == 4 and parts[2] == "shot":
                shot = journey.shots.get(parts[3])
                if shot is not None:
                    return self._send_png(shot)
        return self._send_404()

    def _send_html(self, doc: str) -> None:
        data = doc.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_png(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._send_404()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_404(self) -> None:
        data = b"not found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # silence per-request stderr noise
        pass


def serve(results_dir: Path, *, host: str = "127.0.0.1", port: int = 8123,
          open_browser: bool = True) -> int:
    handler = type("BoundHandler", (_Handler,), {"results_dir": results_dir.resolve()})
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        print(f"error: could not bind {host}:{port}: {e}")
        return 2
    url = f"http://{host}:{port}/"
    print(f"drik dashboard: {url}")
    print(f"results dir:    {results_dir.resolve()}")
    print("Ctrl-C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
