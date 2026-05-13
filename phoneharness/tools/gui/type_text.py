from __future__ import annotations

from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient


class TypeTextTool(BaseTool):
    """Type text on the Android device (simulates keyboard input)."""

    name = "gui_type"
    description = (
        "Type text into the currently focused input field on the device. "
        "Make sure an input field is focused (tap on it first) before calling this tool."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type."},
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919") -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        text = arguments.get("text", "")
        if not isinstance(text, str) or not text:
            self.set_error_type("tool_error")
            raise ValueError("gui_type requires a non-empty text string")

        result = self._client.get("/type", {"text": text})
        ok = result.get("ok", False)
        if not ok:
            self.set_error_type("backend_error")

        return ToolResult(
            ok=ok,
            output=f"Type '{text[:50]}{'...' if len(text) > 50 else ''}': {'succeeded' if ok else 'failed'}.",
            summary=f"gui_type {'succeeded' if ok else 'failed'} ({len(text)} chars)",
            checks={"type_executed": ok},
        )
