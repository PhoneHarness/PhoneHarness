from __future__ import annotations

from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient

COMMON_KEYS = {
    "HOME": 3, "BACK": 4, "ENTER": 66, "DELETE": 67,
    "MENU": 82, "APP_SWITCH": 187, "SEARCH": 84,
}


class KeyeventTool(BaseTool):
    """Send a key event to the Android device."""

    name = "gui_keyevent"
    description = (
        "Send a key event (button press) to the device. "
        "Common keys: HOME (3), BACK (4), ENTER (66), DELETE (67), MENU (82), APP_SWITCH (187). "
        "You can pass either the key name (e.g. 'BACK') or the numeric keycode (e.g. 4)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Key name (HOME/BACK/ENTER/DELETE/MENU) or numeric keycode.",
            },
        },
        "required": ["key"],
        "additionalProperties": False,
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919") -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        key_input = str(arguments.get("key", ""))
        if not key_input:
            self.set_error_type("tool_error")
            raise ValueError("gui_keyevent requires a key name or keycode")

        keycode = COMMON_KEYS.get(key_input.upper(), key_input)

        result = self._client.get("/keyevent", {"key": keycode})
        ok = result.get("ok", False)
        if not ok:
            self.set_error_type("backend_error")

        return ToolResult(
            ok=ok,
            output=f"Keyevent {key_input} (code={keycode}): {'succeeded' if ok else 'failed'}.",
            summary=f"gui_keyevent {'succeeded' if ok else 'failed'} key={key_input}",
            checks={"keyevent_executed": ok},
        )
