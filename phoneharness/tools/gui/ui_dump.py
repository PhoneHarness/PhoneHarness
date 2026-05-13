from __future__ import annotations

from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient


class UiDumpTool(BaseTool):
    """Dump the current UI hierarchy (accessibility tree) of the Android screen."""

    name = "gui_ui_dump"
    description = (
        "Get the UI accessibility tree (XML hierarchy) of the current screen. "
        "This shows all visible UI elements with their text, bounds, and class. "
        "Use this to understand what's on screen and plan GUI actions."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919") -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        result = self._client.get("/ui_dump")
        if not result.get("ok"):
            self.set_error_type("backend_error")
            return ToolResult(
                ok=False,
                output=f"UI dump failed: {result.get('error', 'unknown')}",
                summary="gui_ui_dump failed",
                checks={"ui_dump_captured": False},
            )

        content = result.get("content", "")
        # Truncate for context efficiency
        truncated = content[:8000] if len(content) > 8000 else content
        was_truncated = len(content) > 8000

        return ToolResult(
            ok=True,
            output=f"UI hierarchy ({len(content)} chars):\n{truncated}" + ("\n[...truncated]" if was_truncated else ""),
            summary=f"gui_ui_dump succeeded, {len(content)} chars",
            checks={"ui_dump_captured": True},
        )
