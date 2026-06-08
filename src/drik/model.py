"""Vision-language model client.

Talks to any OpenAI-compatible ``/v1/chat/completions`` endpoint that accepts
image input (base64 data URI). Exposes exactly two primitives:

- ``localize(image, description) -> (x, y)`` in viewport pixels.
- ``ask(image, question) -> bool``.

The model is asked to return coordinates normalized to ``[0, 1000]`` (Holo's
convention); ``coord_space`` controls how the raw numbers are interpreted before
rescaling to the viewport.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

import httpx


class ModelError(Exception):
    """Base class for model-client failures."""


class LocalizationError(ModelError):
    """The model did not return parseable coordinates."""


class VQAError(ModelError):
    """The model did not return a parseable yes/no answer."""


_LOCALIZE_SYSTEM = (
    "You are a precise GUI grounding model. Locate the element the user describes "
    "and output its click position. Respond with only the coordinate."
)

# `Click(x, y)` instruction. This matches the grounding format GUI agent models
# (Holo, UI-TARS, UI-Venus, Qwen-VL) were trained on far better than a generic
# "return JSON" or "output one point" prompt — Holo in particular punts to the
# image center on the latter. _parse_coords pulls the (x, y) out of `Click(x, y)`,
# `(x,y)`, `<|box_start|>(x,y)<|box_end|>`, or `{"x":..,"y":..}` alike.
_LOCALIZE_USER = (
    "Output the click position for the following element as Click(x, y): {description}"
)

_VQA_SYSTEM = (
    "You are a careful visual question-answering model. Given a screenshot and a "
    "yes/no question about what is visible, answer with exactly one word: 'yes' or "
    "'no'. Do not explain."
)

# Pulls {"x":..,"y":..} out of model output even when wrapped in prose/markdown.
_COORD_JSON = re.compile(r'\{[^{}]*?"x"\s*:\s*(-?\d+(?:\.\d+)?)[^{}]*?"y"\s*:\s*(-?\d+(?:\.\d+)?)[^{}]*?\}')
# Fallback: first two numbers anywhere, or "(x, y)" / "x=.. y=..".
_COORD_PAIR = re.compile(r"(-?\d+(?:\.\d+)?)\D+(-?\d+(?:\.\d+)?)")
_YES = re.compile(r"\b(yes|true|correct|visible|present|y)\b", re.I)
_NO = re.compile(r"\b(no|false|incorrect|not|absent|n)\b", re.I)


@dataclass
class ModelClient:
    endpoint: str = "http://localhost:1234/v1"
    model: str = "holo-3.1-4b"
    coord_space: str = "normalized_1000"  # or "pixel"
    timeout: float = 60.0
    temperature: float = 0.0
    api_key: str = "not-needed"  # local servers ignore it; header kept for compatibility

    def __post_init__(self) -> None:
        base = self.endpoint.rstrip("/")
        if not base.endswith("/v1") and "/v1" not in base:
            base = base + "/v1"
        self._url = base + "/chat/completions"
        self._client = httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ModelClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- primitives ---------------------------------------------------------

    def localize(self, image: bytes, description: str, *, viewport: tuple[int, int]) -> tuple[int, int]:
        """Return the (x, y) pixel coordinate to click for ``description``."""
        content = self._chat(
            system=_LOCALIZE_SYSTEM,
            user_text=_LOCALIZE_USER.format(description=description),
            image=image,
        )
        raw_x, raw_y = self._parse_coords(content)
        return self._to_pixels(raw_x, raw_y, viewport=viewport)

    def ask(self, image: bytes, question: str) -> tuple[bool, str]:
        """Return (answer, raw_model_text) for a yes/no ``question``."""
        content = self._chat(
            system=_VQA_SYSTEM,
            user_text=f"{question}\nAnswer yes or no.",
            image=image,
        )
        return self._parse_yesno(content), content

    # -- internals ----------------------------------------------------------

    def _chat(self, *, system: str, user_text: str, image: bytes) -> str:
        data_uri = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            # Constrain decoding for determinism; harmless on servers that ignore them.
            "max_tokens": 64,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            # Disable "thinking" mode on servers that honor it (Holo/Qwen-style).
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            resp = self._client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ModelError(
                f"model server returned {e.response.status_code} for {self._url}: "
                f"{e.response.text[:300]}"
            ) from e
        except httpx.HTTPError as e:
            raise ModelError(f"could not reach model server at {self._url}: {e}") from e

        body = resp.json()
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"unexpected response shape from model server: {body!r}") from e

    def _parse_coords(self, content: str) -> tuple[float, float]:
        m = _COORD_JSON.search(content)
        if m:
            return float(m.group(1)), float(m.group(2))
        # Try a bare JSON parse (some models return clean JSON without our regex shape).
        try:
            obj = json.loads(content.strip())
            if isinstance(obj, dict) and "x" in obj and "y" in obj:
                return float(obj["x"]), float(obj["y"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        m = _COORD_PAIR.search(content)
        if m:
            return float(m.group(1)), float(m.group(2))
        raise LocalizationError(f"no coordinates found in model output: {content!r}")

    def _parse_yesno(self, content: str) -> bool:
        head = content.strip().lower()
        # Prefer a decisive first token, stripped of surrounding punctuation.
        tokens = head.split()
        first = tokens[0].strip(".,!:;'\"") if tokens else ""
        if first in ("yes", "true", "y"):
            return True
        if first in ("no", "false", "n"):
            return False
        # Fall back to whichever appears; "no" is checked first since "not" is a
        # strong negative signal that "yes" rarely overrides.
        if _NO.search(head) and not _YES.search(head):
            return False
        if _YES.search(head):
            return True
        if _NO.search(head):
            return False
        raise VQAError(f"could not parse yes/no from model output: {content!r}")

    def _to_pixels(self, raw_x: float, raw_y: float, *, viewport: tuple[int, int]) -> tuple[int, int]:
        w, h = viewport
        if self.coord_space == "pixel":
            x, y = raw_x, raw_y
        else:  # normalized_1000
            x = raw_x / 1000.0 * w
            y = raw_y / 1000.0 * h
        # Clamp into the viewport so a slightly-off prediction still lands on-screen.
        x = max(0, min(w - 1, int(round(x))))
        y = max(0, min(h - 1, int(round(y))))
        return x, y
