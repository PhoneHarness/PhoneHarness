from __future__ import annotations

from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient


class TapTool(BaseTool):
    """Tap at a specific coordinate on the Android screen."""

    name = "gui_tap"
    description = (
        "Tap at screen coordinates (x, y) on the device. "
        "Use gui_screenshot or gui_ui_dump first to identify the target location."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X coordinate to tap."},
            "y": {"type": "integer", "description": "Y coordinate to tap."},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    }

    # Device resolution — used to scale normalized coords back to pixels
    _DEVICE_W = 1080
    _DEVICE_H = 2400

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919",
                 normalized_coords: bool = False) -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self._normalized = normalized_coords
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        x = arguments.get("x")
        y = arguments.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            self.set_error_type("tool_error")
            raise ValueError("gui_tap requires numeric x and y")

        # In flat/normalized mode, coords are in screenshot space (1000×N).
        # Scale back to device pixels.
        if self._normalized:
            from .screenshot import _FLAT_MODE_WIDTH
            scale = self._DEVICE_W / _FLAT_MODE_WIDTH  # 1080/1000 = 1.08
            actual_x = int(x * scale)
            actual_y = int(y * scale)
        else:
            actual_x, actual_y = int(x), int(y)

        result = self._client.get("/tap", {"x": actual_x, "y": actual_y})
        ok = result.get("ok", False)
        if not ok:
            self.set_error_type("backend_error")

        coord_info = f"({x},{y})→pixel({actual_x},{actual_y})" if self._normalized else f"({actual_x}, {actual_y})"
        return ToolResult(
            ok=ok,
            output=f"Tap at {coord_info}: {'succeeded' if ok else 'failed'}. {result.get('stderr', '')}",
            summary=f"gui_tap {'succeeded' if ok else 'failed'} at {coord_info}",
            checks={"tap_executed": ok},
        )
