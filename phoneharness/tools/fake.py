from __future__ import annotations

import json
from typing import Any

from ..agent.message import ToolResult
from .base import BaseTool


class EchoArgsTool(BaseTool):
    name = "echo_args"
    description = "Echo the provided arguments back to the caller for transport validation."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text payload to echo."},
            "count": {"type": "integer", "description": "A small integer for roundtrip verification."},
            "metadata": {
                "type": "object",
                "description": "Optional metadata used to confirm nested JSON survives the roundtrip.",
                "additionalProperties": True,
            },
        },
        "required": ["text", "count"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        keys = ", ".join(sorted(arguments)) or "no keys"
        return ToolResult(
            ok=True,
            output=json.dumps({"received": arguments}, ensure_ascii=False, sort_keys=True),
            summary=f"echo_args succeeded with keys: {keys}",
            exit_code=0,
            checks={
                "has_text": isinstance(arguments.get("text"), str),
                "has_count": isinstance(arguments.get("count"), int),
            },
        )


class NoopSuccessTool(BaseTool):
    name = "noop_success"
    description = "Return a successful no-op result without side effects."
    input_schema = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "Optional note to attach to the no-op."}
        },
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        note = arguments.get("note", "")
        suffix = f": {note}" if note else ""
        return ToolResult(
            ok=True,
            output=f"noop_success completed{suffix}",
            summary="noop_success returned success",
            exit_code=0,
            checks={"noop_completed": True},
        )

