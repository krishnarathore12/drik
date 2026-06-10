"""Managed local model serving: `drik model ...` and auto-start from `drik run`.

Drik can host the vision model itself on Apple Silicon via mlx-vlm instead of
requiring an already-running OpenAI-compatible server. The serving stack lives
in its own venv under ~/.drik/mlx-venv — mlx-vlm is a heavy, macOS-only
dependency that must not burden drik's own install — created on first use with
uv. The model is downloaded once into the shared Hugging Face cache.

mlx-vlm 0.6.0 rejects Holo-3.1's `vision_config.model_type` of
"qwen3_5_vision"; after every install the venv's source gets the small
allow-list patch (see patch_vision_guard) until upstream fixes it.

The server is left running after `drik run` exits so re-runs are instant;
`drik model stop` shuts it down.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

DEFAULT_REPO = "pipenetwork/Holo-3.1-4B-MLX-8bit"
STATE_DIR = Path(os.environ.get("DRIK_HOME", str(Path.home() / ".drik")))
VENV_DIR = STATE_DIR / "mlx-venv"
LOG_FILE = STATE_DIR / "mlx-server.log"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _echo(msg: str) -> None:
    print(msg, file=sys.stderr)


# -- endpoint probing ---------------------------------------------------------

def endpoint_alive(endpoint: str, timeout: float = 3.0) -> bool:
    try:
        r = httpx.get(endpoint.rstrip("/") + "/models", timeout=timeout)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


def first_model_id(endpoint: str, timeout: float = 5.0) -> str | None:
    """The id of the first model the server reports, for a default --model."""
    try:
        r = httpx.get(endpoint.rstrip("/") + "/models", timeout=timeout)
        data = r.json().get("data", [])
        return data[0]["id"] if data else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def local_port(endpoint: str) -> int | None:
    """The endpoint's port if it points at this machine, else None."""
    u = urlparse(endpoint)
    if u.hostname not in _LOCAL_HOSTS:
        return None
    return u.port or (443 if u.scheme == "https" else 80)


def supported_platform() -> bool:
    import platform
    return platform.system() == "Darwin" and platform.machine() == "arm64"


# -- managed mlx-vlm environment ----------------------------------------------

def _venv_python() -> Path:
    return VENV_DIR / "bin" / "python"


def ensure_env() -> Path | None:
    """Create the mlx-vlm venv if missing; return its python (None on failure)."""
    py = _venv_python()
    if py.exists():
        return py
    uv = shutil.which("uv")
    if uv is None:
        _echo("error: 'uv' is required to set up the model environment.")
        _echo("install it:  curl -LsSf https://astral.sh/uv/install.sh | sh")
        return None
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _echo(f"▶ Creating model venv at {VENV_DIR} (one-time)…")
    try:
        subprocess.run([uv, "venv", str(VENV_DIR), "--python", "3.12"],
                       check=True)
        subprocess.run([uv, "pip", "install", "--python", str(py), "mlx-vlm"],
                       check=True)
    except subprocess.CalledProcessError as e:
        _echo(f"error: could not set up mlx-vlm environment: {e}")
        return None
    _apply_holo_patch(py)
    return py


_GUARD_RE = re.compile(r"(self\.model_type not in \[)([^\]]*)(\])")


def patch_vision_guard(src: str) -> str | None:
    """Add Holo-3.1's vision model types to mlx-vlm's allow-list guard.

    Returns the patched source, or None if the source is already patched or
    the guard couldn't be located (e.g. upstream restructured the check).
    """
    if "qwen3_5_vision" in src:
        return None
    m = _GUARD_RE.search(src)
    if m is None:
        return None
    items = m.group(2).rstrip()
    extra = '"qwen3_5_vision", "qwen3_5_moe_vision"'
    new_items = f"{items}, {extra}" if items.strip() else extra
    return src[: m.start(2)] + new_items + src[m.end(2):]


def _apply_holo_patch(py: Path) -> None:
    try:
        out = subprocess.run(
            [str(py), "-c",
             "import mlx_vlm.models.qwen3_vl.vision as v; print(v.__file__)"],
            check=True, capture_output=True, text=True,
        )
        path = Path(out.stdout.strip())
        src = path.read_text(encoding="utf-8")
    except (subprocess.CalledProcessError, OSError) as e:
        _echo(f"warning: could not locate mlx-vlm vision module to patch: {e}")
        return
    if "qwen3_5_vision" in src:
        return  # already patched (or fixed upstream)
    patched = patch_vision_guard(src)
    if patched is None:
        _echo("warning: could not auto-patch mlx-vlm's vision-type guard; "
              "Holo-3.1 may be rejected (see README, 'Running Holo-3.1 on mlx-vlm')")
        return
    path.write_text(patched, encoding="utf-8")
    _echo("✓ patched mlx-vlm vision-type guard for Holo-3.1")


def download_model(py: Path, repo: str) -> bool:
    """Fetch the model into the HF cache (idempotent; shows progress)."""
    _echo(f"▶ Ensuring model {repo} is downloaded (one-time, a few GB)…")
    code = ("import sys; from huggingface_hub import snapshot_download; "
            "snapshot_download(sys.argv[1])")
    try:
        subprocess.run([str(py), "-c", code, repo], check=True)
        return True
    except subprocess.CalledProcessError:
        _echo(f"error: could not download {repo}. Set HF_TOKEN if the download "
              "was rate-limited, then retry.")
        return False


# -- server lifecycle ----------------------------------------------------------

def _pid_file(port: int) -> Path:
    return STATE_DIR / f"mlx-server-{port}.pid"


def start_server(repo: str, port: int, wait_s: float = 300.0) -> bool:
    """Start `mlx_vlm server` detached; block until it answers or fails."""
    py = ensure_env()
    if py is None:
        return False
    if not download_model(py, repo):
        return False

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _echo(f"▶ Starting model server on :{port} (loads the model into memory, "
          "~20–40 s)…")
    log = open(LOG_FILE, "ab")
    proc = subprocess.Popen(
        [str(py), "-m", "mlx_vlm", "server", "--model", repo, "--port", str(port)],
        stdout=log, stderr=log, start_new_session=True,
    )
    _pid_file(port).write_text(str(proc.pid), encoding="utf-8")

    endpoint = f"http://127.0.0.1:{port}/v1"
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _echo(f"error: model server exited early; last log lines "
                  f"({LOG_FILE}):")
            _tail_log()
            return False
        if endpoint_alive(endpoint):
            _echo(f"✓ model server ready at {endpoint} "
                  f"(pid {proc.pid}; stop with: drik model stop --port {port})")
            return True
        time.sleep(2)
    _echo(f"error: model server did not become ready within {wait_s:.0f}s; "
          f"see {LOG_FILE}")
    return False


def stop_server(port: int) -> bool:
    pidfile = _pid_file(port)
    if not pidfile.exists():
        _echo(f"no managed server recorded for port {port} "
              f"(if one is running anyway: pkill -f 'mlx_vlm server')")
        return False
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        _echo(f"✓ stopped model server (pid {pid})")
        stopped = True
    except (ValueError, ProcessLookupError):
        _echo("server was not running")
        stopped = False
    except PermissionError:
        _echo(f"error: not permitted to stop pid from {pidfile}")
        return False
    pidfile.unlink(missing_ok=True)
    return stopped


def ensure_running(endpoint: str, repo: str = DEFAULT_REPO,
                   wait_s: float = 300.0) -> bool:
    """Make sure an OpenAI-compatible vision server answers at endpoint.

    Reuses any server already listening there (drik-managed or not). Otherwise
    starts a managed mlx-vlm server — only possible for a localhost endpoint
    on Apple Silicon.
    """
    if endpoint_alive(endpoint):
        return True
    port = local_port(endpoint)
    if port is None:
        _echo(f"error: no model server at {endpoint}, and it is not a "
              "localhost endpoint drik can start one on.")
        return False
    if not supported_platform():
        _echo(f"error: no model server at {endpoint}. Drik can only host the "
              "model itself on Apple Silicon (via mlx-vlm); start an "
              "OpenAI-compatible vision server (e.g. LM Studio) and retry.")
        return False
    _echo(f"no model server at {endpoint} — starting one…")
    return start_server(repo, port, wait_s=wait_s)


def _tail_log(lines: int = 20) -> None:
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines()[-lines:]:
            _echo(f"  {line}")
    except OSError:
        pass
