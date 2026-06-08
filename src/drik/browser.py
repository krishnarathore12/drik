"""Playwright (Chromium) browser wrapper.

Keeps ``device_scale_factor=1`` so screenshot pixels map 1:1 to click
coordinates — the model sees the same pixel grid we click on.
"""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright


class Browser:
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:3000",
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 800),
        timeout_ms: int = 30_000,
    ):
        self.base_url = base_url
        self.headless = headless
        self.viewport = viewport
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._page: Page | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        w, h = self.viewport
        self._context = self._browser.new_context(
            viewport={"width": w, "height": h},
            device_scale_factor=1,
        )
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.new_page()

    def close(self) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._context = self._browser = self._pw = self._page = None

    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("browser not started; call start() first")
        return self._page

    # -- actions ------------------------------------------------------------

    def goto(self, path_or_url: str) -> None:
        url = self._resolve(path_or_url)
        self.page.goto(url, wait_until="domcontentloaded")

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="png")

    def click(self, x: int, y: int) -> None:
        self.page.mouse.click(x, y)

    def type_text(self, text: str, *, delay_ms: int = 20) -> None:
        # keyboard.type fires per-key events into the focused element.
        self.page.keyboard.type(text, delay=delay_ms)

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)

    def scroll(self, direction: str) -> None:
        _, h = self.viewport
        dy = int(h * 0.8)
        self.page.mouse.wheel(0, dy if direction == "down" else -dy)

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    # -- helpers ------------------------------------------------------------

    def _resolve(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        # urljoin needs a trailing slash on the base to treat it as a directory,
        # but absolute paths (leading /) resolve against the origin regardless.
        return urljoin(self.base_url.rstrip("/") + "/", path_or_url.lstrip("/"))
