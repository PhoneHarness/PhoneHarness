from __future__ import annotations

from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient


class SwipeTool(BaseTool):
    """Swipe on the Android screen from one point to another."""

    name = "gui_swipe"
    description = (
        "Swipe on the device screen from (x1, y1) to (x2, y2). "
        "Use this for scrolling: swipe up = scroll down, swipe down = scroll up. "
        "Typical screen size is 1080x2400. Duration is in milliseconds (default 300)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "x1": {"type": "integer", "description": "Start X coordinate."},
            "y1": {"type": "integer", "description": "Start Y coordinate."},
            "x2": {"type": "integer", "description": "End X coordinate."},
            "y2": {"type": "integer", "description": "End Y coordinate."},
            "duration": {"type": "integer", "description": "Swipe duration in ms (default 300).", "default": 300},
        },
        "required": ["x1", "y1", "x2", "y2"],
        "additionalProperties": False,
    }

    _DEVICE_W = 1080
    _DEVICE_H = 2400

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919",
                 normalized_coords: bool = False) -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self._normalized = normalized_coords
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        x1 = arguments.get("x1")
        y1 = arguments.get("y1")
        x2 = arguments.get("x2")
        y2 = arguments.get("y2")
        duration = arguments.get("duration", 300)

        for name, val in [("x1", x1), ("y1", y1), ("x2", x2), ("y2", y2)]:
            if not isinstance(val, (int, float)):
                self.set_error_type("tool_error")
                raise ValueError(f"gui_swipe requires numeric {name}")

        if self._normalized:
            from .screenshot import _FLAT_MODE_WIDTH
            scale = self._DEVICE_W / _FLAT_MODE_WIDTH
            x1, y1, x2, y2 = int(x1*scale), int(y1*scale), int(x2*scale), int(y2*scale)
        else:
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

        result = self._client.get("/swipe", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration})
        ok = result.get("ok", False)
        if not ok:
            self.set_error_type("backend_error")

        return ToolResult(
            ok=ok,
            output=f"Swipe ({x1},{y1})→({x2},{y2}) duration={duration}ms: {'succeeded' if ok else 'failed'}.",
            summary=f"gui_swipe {'succeeded' if ok else 'failed'} ({x1},{y1})→({x2},{y2})",
            checks={"swipe_executed": ok},
        )
