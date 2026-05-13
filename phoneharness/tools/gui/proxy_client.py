"""Base for GUI tools that call the host-side GUI proxy."""
from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request


class GUIProxyClient:
    """Thin HTTP client for the host-side gui_proxy.py."""

    def __init__(self, base_url: str = "http://10.0.2.2:8919"):
        self.base_url = base_url.rstrip("/")

    def get(self, path: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            query = parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"
        req = request.Request(url, method="GET")
        return self._do(req, timeout)

    def post_json(self, path: str, body: dict[str, Any], timeout: float = 15.0) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        return self._do(req, timeout)

    def _do(self, req: request.Request, timeout: float) -> dict:
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "error": f"HTTP {exc.code}: {raw}"}
        except error.URLError as exc:
            return {"ok": False, "error": f"connection failed: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
