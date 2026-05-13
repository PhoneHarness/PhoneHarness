"""PhoneHarness HTTP Server — minimal stdlib wrapper for app integration.

Exposes the existing agent loop as HTTP endpoints so that an on-device APK
can POST a user instruction and receive a JSON-lines stream of
execution events.

Endpoints:
    GET  /health        → {"ok": true}
    GET  /clear         → {"ok": true, "cleared": N} — reset conversation history
    POST /run           → JSON lines stream (Content-Type: application/x-ndjson)
                          Supports multi-turn: conversation history persists across
                          requests. Send POST /run with {"input": "...", "clear": true}
                          to start a fresh conversation.

Usage:
    python3 -m phoneharness server [--port 8920] --model doubao-2.0-pro --gui-model seed-2.0-pro

Design constraints:
    - Python stdlib only (http.server) — no FastAPI/uvicorn/flask
    - Multi-turn conversation with persistent history
    - Zero changes to agent internals
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

from pathlib import Path

from .console import ConsoleConfig, _build_registry, _get_system_prompt, _resolve_gui_backend, run_console_turn_evented
from .model.client import OpenAICompatClient


def _load_chat_html() -> str:
    """Load chat.html from common locations."""
    candidates = [
        Path(__file__).parent.parent / "chat.html",     # repo root
        Path(__file__).parent / "chat.html",             # inside package
        Path.home() / "chat.html",                       # home dir (Termux)
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return "<html><body><h1>chat.html not found</h1></body></html>"


@dataclass
class ServerConfig:
    port: int = 8920
    host: str = "0.0.0.0"
    console_config: ConsoleConfig | None = None


class AgentHandler(BaseHTTPRequestHandler):
    """HTTP handler for the PhoneHarness agent server."""

    # Silence per-request logging to stderr (too noisy for Termux)
    def log_message(self, format: str, *args: Any) -> None:
        pass

    # ── GET /health, /clear ──

    def do_GET(self) -> None:
        if self.path == "/health":
            server: AgentServer = self.server  # type: ignore[assignment]
            cc = server.console_config
            gui_model, gui_api_url, _ = _resolve_gui_backend(cc)
            health = {
                "ok": True,
                "model": cc.model,
                "gui_model": gui_model,
                "gui_mode": cc.gui_mode,
                "gui_api_url": gui_api_url,
            }
            if cc.gui_mode == "flat":
                health["flat_gui_protocol"] = cc.flat_gui_protocol
            self._send_json(health)
        elif self.path == "/clear":
            server: AgentServer = self.server  # type: ignore[assignment]
            n = len(server.conversation_history)
            server.conversation_history.clear()
            self._send_json({"ok": True, "cleared": n})
        elif self.path == "/":
            server: AgentServer = self.server  # type: ignore[assignment]
            self._send_html(server.chat_html)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")

    # ── POST /run ──

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        # Parse request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_error(HTTPStatus.BAD_REQUEST, "empty body")
            return

        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}")
            return

        user_input = body.get("input", "").strip()
        if not user_input:
            self._send_error(HTTPStatus.BAD_REQUEST, "missing 'input' field")
            return

        server: AgentServer = self.server  # type: ignore[assignment]
        agent_mode = body.get("agent_mode", "tool_calling")

        # Optional: clear history for a fresh conversation
        if body.get("clear", False):
            server.conversation_history.clear()

        # Begin streaming response (JSON lines)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit_event(event: dict[str, Any]) -> None:
            """Write one JSON line to the chunked HTTP response."""
            try:
                line = json.dumps(event, ensure_ascii=False) + "\n"
                data = line.encode("utf-8")
                # Chunked transfer encoding: size in hex + CRLF + data + CRLF
                self.wfile.write(f"{len(data):x}\r\n".encode())
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client disconnected

        try:
            if agent_mode == "seed_gui":
                # Seed GUI controller (bypasses M4 tool_calling loop)
                from .controllers.seed_gui import run_seed_gui_turn
                max_steps = body.get("max_steps", 30)
                gui_model, gui_api_url, gui_api_key = _resolve_gui_backend(server.console_config)
                run_seed_gui_turn(
                    user_input=user_input,
                    gui_proxy_url=server.console_config.gui_proxy_url,
                    max_steps=max_steps,
                    gui_model=gui_model,
                    gui_api_url=gui_api_url,
                    gui_api_key=gui_api_key,
                    on_event=emit_event,
                )
            else:
                # Default: M4 tool_calling loop (unchanged)
                if not server.conversation_history:
                    server.conversation_history.append(
                        {"role": "system", "content": server.system_prompt}
                    )
                run_console_turn_evented(
                    user_input=user_input,
                    config=server.console_config,
                    client=server.client,
                    registry=server.registry,
                    system_prompt=server.system_prompt,
                    on_event=emit_event,
                    history=server.conversation_history,
                )
        except Exception as exc:
            emit_event({"event": "error", "message": f"server_error: {exc}"})

        # Send final zero-length chunk to signal end
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Helpers ──

    def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status)


class AgentServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTPServer subclass that holds shared agent state.

    ThreadingMixIn ensures each request runs in its own thread,
    so a slow agent turn doesn't block new requests.
    """
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        console_config: ConsoleConfig,
    ) -> None:
        super().__init__(server_address, AgentHandler)
        self.console_config = console_config

        # Build shared resources once
        self.client = OpenAICompatClient(
            base_url=console_config.base_url,
            api_key=console_config.api_key,
            model=console_config.model,
        )
        _, self.registry, _ = _build_registry(console_config)
        self.system_prompt = _get_system_prompt(console_config)

        # Multi-turn conversation history (shared across requests)
        self.conversation_history: list[dict[str, Any]] = []

        # Load chat UI HTML (look for chat.html next to the phoneharness package)
        self.chat_html = _load_chat_html()


def run_server(config: ServerConfig) -> int:
    """Start the HTTP server (blocking)."""
    console_config = config.console_config or ConsoleConfig()
    gui_model, gui_api_url, _ = _resolve_gui_backend(console_config)

    server = AgentServer(
        server_address=(config.host, config.port),
        console_config=console_config,
    )

    print(f"PhoneHarness server listening on {config.host}:{config.port}")
    print(f"  Model:      {console_config.model}")
    print(f"  GUI Model:  {gui_model}")
    print(f"  GUI Mode:   {console_config.gui_mode}")
    print(f"  API:        {console_config.base_url}")
    print(f"  GUI API:    {gui_api_url}")
    print(f"  GUI:        {console_config.gui_proxy_url}")
    print(f"  Health: GET  http://localhost:{config.port}/health")
    print(f"  Run:    POST http://localhost:{config.port}/run")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()

    return 0


__all__ = ["AgentServer", "ServerConfig", "run_server"]
