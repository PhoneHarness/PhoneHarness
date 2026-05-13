"""Semantic mobile action tools — structured Android intent tools.

Each tool builds an `am start` command and executes it directly via subprocess.
The intent descriptor is also returned in ToolResult for logging/debugging.
"""
from __future__ import annotations

import shlex
import subprocess
import urllib.parse
from typing import Any

from ..agent.message import ToolResult
from .base import BaseTool


class _MobileActionTool(BaseTool):
    """Base class — builds and executes an Android intent via `am start`."""

    def __init__(self, **_kw: Any) -> None:
        pass

    @staticmethod
    def _intent_result(intent: dict[str, Any], action_label: str) -> ToolResult:
        # Build am start command from intent descriptor
        cmd_parts = ["am", "start", "-f", "0x10000000"]
        action = intent.get("action")
        if action:
            cmd_parts.extend(["-a", action])
        data = intent.get("data")
        if data:
            cmd_parts.extend(["-d", data])
        mime_type = intent.get("type")
        if mime_type:
            cmd_parts.extend(["-t", mime_type])
        for key, val in intent.get("extras", {}).items():
            cmd_parts.extend(["--es", key, str(val)])
        for key, val in intent.get("extras_long", {}).items():
            cmd_parts.extend(["--el", key, str(val)])

        cmd = " ".join(shlex.quote(p) for p in cmd_parts)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10,
            )
            ok = result.returncode == 0
            output = result.stdout.strip() or result.stderr.strip()
            summary = f"{action_label} {'succeeded' if ok else 'failed'}"
            if not ok:
                summary += f": {output[:100]}"
        except subprocess.TimeoutExpired:
            ok = False
            output = "am start timed out"
            summary = f"{action_label} timed out"

        return ToolResult(
            ok=ok,
            output=f"{action_label}: {output}",
            summary=summary,
            intent=intent,
        )


# ── Individual tools, one per mobile action ──


class CreateContactTool(_MobileActionTool):
    name = "create_contact"
    description = (
        "Open the Contacts app with pre-filled fields to create a new contact. "
        "The contact creation screen will open with the provided information."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "first_name": {"type": "string", "description": "First name of the contact"},
            "last_name": {"type": "string", "description": "Last name of the contact"},
            "phone_number": {"type": "string", "description": "Phone number of the contact", "default": ""},
            "email": {"type": "string", "description": "Email address of the contact", "default": ""},
        },
        "required": ["first_name", "last_name"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        first = arguments.get("first_name", "")
        last = arguments.get("last_name", "")
        phone = arguments.get("phone_number", "")
        email = arguments.get("email", "")
        name = f"{first} {last}".strip()

        extras: dict[str, str] = {"name": name}
        if phone:
            extras["phone"] = phone
        if email:
            extras["email"] = email

        return self._intent_result(
            {
                "action": "android.intent.action.INSERT",
                "type": "vnd.android.cursor.dir/contact",
                "extras": extras,
            },
            "create_contact",
        )


class SendEmailTool(_MobileActionTool):
    name = "send_email"
    description = (
        "Open the email composer with pre-filled recipient, subject, and body. "
        "The email app will open ready for the user to review and send."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line", "default": ""},
            "body": {"type": "string", "description": "Email body text", "default": ""},
        },
        "required": ["to"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        to = arguments.get("to", "")
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")

        # Use ACTION_SENDTO + mailto: URI so the recipient is encoded in the
        # URI itself.  EXTRA_EMAIL requires a String[] which our simple
        # extras map (String→String) can't express, and putExtra(key, string)
        # is silently ignored by Gmail.
        params: dict[str, str] = {}
        if subject:
            params["subject"] = subject
        if body:
            params["body"] = body
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        mailto = f"mailto:{urllib.parse.quote(to, safe='@')}"
        if query:
            mailto += f"?{query}"

        return self._intent_result(
            {
                "action": "android.intent.action.SENDTO",
                "data": mailto,
            },
            "send_email",
        )


class ShowMapTool(_MobileActionTool):
    name = "show_map"
    description = (
        "Open the Maps app and show a location. "
        "The location can be a place name, business name, or address."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "Location to show — place name, business, or address",
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        location = arguments.get("location", "")
        encoded = urllib.parse.quote(location)
        return self._intent_result(
            {
                "action": "android.intent.action.VIEW",
                "data": f"geo:0,0?q={encoded}",
            },
            "show_map",
        )


class OpenWifiSettingsTool(_MobileActionTool):
    name = "open_wifi_settings"
    description = "Open the device WiFi settings screen."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._intent_result(
            {"action": "android.settings.WIFI_SETTINGS"},
            "open_wifi_settings",
        )


class OpenSettingsTool(_MobileActionTool):
    name = "open_settings"
    description = "Open the device main Settings screen."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._intent_result(
            {"action": "android.settings.SETTINGS"},
            "open_settings",
        )


class CreateCalendarEventTool(_MobileActionTool):
    name = "create_calendar_event"
    description = (
        "Open the Calendar app to create a new event with the given title and datetime."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title of the calendar event"},
            "datetime": {
                "type": "string",
                "description": "Date and time in ISO format YYYY-MM-DDTHH:MM:SS",
                "default": "",
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        title = arguments.get("title", "")
        dt_str = arguments.get("datetime", "")
        extras: dict[str, str] = {"title": title}
        extras_long: dict[str, int] = {}

        if dt_str:
            import datetime as _dt
            try:
                # Parse ISO format, assume local timezone if naive
                parsed = _dt.datetime.fromisoformat(dt_str)
                begin_ms = int(parsed.timestamp() * 1000)
                end_ms = begin_ms + 3600_000  # default 1 hour duration
                extras_long["beginTime"] = begin_ms
                extras_long["endTime"] = end_ms
            except ValueError:
                pass  # skip if unparseable, calendar will use defaults

        intent: dict[str, Any] = {
            "action": "android.intent.action.INSERT",
            "type": "vnd.android.cursor.item/event",
            "extras": extras,
        }
        if extras_long:
            intent["extras_long"] = extras_long
        return self._intent_result(intent, "create_calendar_event")


class OpenBrowserTool(_MobileActionTool):
    name = "open_browser"
    description = "Open a URL in the device browser."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to open"},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        url = arguments.get("url", "")
        return self._intent_result(
            {
                "action": "android.intent.action.VIEW",
                "data": url,
            },
            "open_browser",
        )


class DialPhoneTool(_MobileActionTool):
    name = "dial_phone"
    description = "Open the phone dialer with a pre-filled number."
    input_schema = {
        "type": "object",
        "properties": {
            "phone_number": {"type": "string", "description": "Phone number to dial"},
        },
        "required": ["phone_number"],
        "additionalProperties": False,
    }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        number = arguments.get("phone_number", "")
        return self._intent_result(
            {
                "action": "android.intent.action.DIAL",
                "data": f"tel:{number}",
            },
            "dial_phone",
        )


# Convenience list for registration
ALL_MOBILE_ACTION_TOOLS = [
    CreateContactTool,
    SendEmailTool,
    ShowMapTool,
    OpenWifiSettingsTool,
    OpenSettingsTool,
    CreateCalendarEventTool,
    OpenBrowserTool,
    DialPhoneTool,
]
