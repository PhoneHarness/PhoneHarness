"""MCP-style aliases for GUI tools.

Wraps existing GUI tools under a unified MCP naming convention
(mcp__android_gui__*) so they appear alongside MCP-Bench tools
in the same registry namespace. Execution is identical — same
GUIProxyClient, same gui_proxy endpoints.
"""
from __future__ import annotations

from typing import Any

from ..agent.message import ToolResult
from .base import BaseTool
from .gui.proxy_client import GUIProxyClient


class MCPScreenshotTool(BaseTool):
    name = "mcp__android_gui__screenshot"
    description = (
        "Take a screenshot of the current Android device screen. "
        "Returns the screenshot image for visual inspection."
    )
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919"):
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        result = self._client.get("/screenshot")
        if not result.get("ok"):
            return ToolResult(ok=False, output=f"Screenshot failed: {result.get('error')}",
                              summary="screenshot failed")
        b64 = result.get("base64", "")
        size = result.get("size_bytes", 0)
        return ToolResult(
            ok=True,
            output=f"Screenshot captured ({size} bytes). Image attached.",
            summary=f"screenshot captured, {size} bytes",
            image_base64=b64,  # PNG base64 — no JPEG compression
            image_mime_type="image/png",
        )


class MCPTapTool(BaseTool):
    name = "mcp__android_gui__tap"
    description = "Tap at screen coordinates (x, y) on the Android device."
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X pixel coordinate (0-1080)"},
            "y": {"type": "integer", "description": "Y pixel coordinate (0-2400)"},
        },
        "required": ["x", "y"],
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919"):
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        x, y = arguments.get("x", 0), arguments.get("y", 0)
        result = self._client.get("/tap", {"x": x, "y": y})
        ok = result.get("ok", False)
        return ToolResult(ok=ok, output=f"Tap ({x},{y}): {'ok' if ok else 'failed'}",
                          summary=f"tap ({x},{y}) {'ok' if ok else 'failed'}")


class MCPSwipeTool(BaseTool):
    name = "mcp__android_gui__swipe"
    description = "Swipe on the Android device screen from (x1,y1) to (x2,y2)."
    input_schema = {
        "type": "object",
        "properties": {
            "x1": {"type": "integer", "description": "Start X"},
            "y1": {"type": "integer", "description": "Start Y"},
            "x2": {"type": "integer", "description": "End X"},
            "y2": {"type": "integer", "description": "End Y"},
            "duration": {"type": "integer", "description": "Duration in ms", "default": 300},
        },
        "required": ["x1", "y1", "x2", "y2"],
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919"):
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        result = self._client.get("/swipe", {
            "x1": arguments.get("x1"), "y1": arguments.get("y1"),
            "x2": arguments.get("x2"), "y2": arguments.get("y2"),
            "duration": arguments.get("duration", 300),
        })
        ok = result.get("ok", False)
        return ToolResult(ok=ok, output=f"Swipe: {'ok' if ok else 'failed'}",
                          summary=f"swipe {'ok' if ok else 'failed'}")


class MCPTypeTool(BaseTool):
    name = "mcp__android_gui__type"
    description = "Type text into the focused input field on Android (supports Chinese via ADBKeyboard)."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type"},
        },
        "required": ["text"],
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919"):
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        text = arguments.get("text", "")
        result = self._client.get("/type", {"text": text})
        ok = result.get("ok", False)
        return ToolResult(ok=ok, output=f"Type '{text[:30]}': {'ok' if ok else 'failed'}",
                          summary=f"type {'ok' if ok else 'failed'} ({len(text)} chars)")


class MCPKeyeventTool(BaseTool):
    name = "mcp__android_gui__keyevent"
    description = "Send a key event to Android (HOME, BACK, ENTER, etc.)."
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Key name: HOME, BACK, ENTER, etc."},
        },
        "required": ["key"],
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919"):
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        key = arguments.get("key", "")
        # Normalize: allow "HOME" or "KEYCODE_HOME"
        if not key.startswith("KEYCODE_"):
            key = f"KEYCODE_{key.upper()}"
        result = self._client.get("/keyevent", {"key": key})
        ok = result.get("ok", False)
        return ToolResult(ok=ok, output=f"Keyevent {key}: {'ok' if ok else 'failed'}",
                          summary=f"keyevent {key} {'ok' if ok else 'failed'}")


class MCPUiDumpTool(BaseTool):
    name = "mcp__android_gui__ui_dump"
    description = "Get the UI accessibility tree of the current Android screen (element names, bounds, classes)."
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919"):
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        result = self._client.get("/ui_dump")
        if not result.get("ok"):
            return ToolResult(ok=False, output=f"UI dump failed: {result.get('error')}",
                              summary="ui_dump failed")
        content = result.get("content", "")
        return ToolResult(ok=True, output=content[:5000], summary=f"ui_dump ok, {len(content)} chars")


def load_gui_mcp_aliases(gui_proxy_url: str = "http://10.0.2.2:8919") -> list[BaseTool]:
    """Return all GUI MCP alias tools."""
    return [
        MCPScreenshotTool(gui_proxy_url=gui_proxy_url),
        MCPTapTool(gui_proxy_url=gui_proxy_url),
        MCPSwipeTool(gui_proxy_url=gui_proxy_url),
        MCPTypeTool(gui_proxy_url=gui_proxy_url),
        MCPKeyeventTool(gui_proxy_url=gui_proxy_url),
        MCPUiDumpTool(gui_proxy_url=gui_proxy_url),
    ]
