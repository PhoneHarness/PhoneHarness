#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("MOCK_OPENAI_HOST", "0.0.0.0")
PORT = int(os.environ.get("MOCK_OPENAI_PORT", "8900"))
CAPTURE_PATH = Path(os.environ.get("MOCK_OPENAI_CAPTURE", "/tmp/mock_openai_capture.json"))
RESPONSE_TEXT = os.environ.get("MOCK_OPENAI_RESPONSE_TEXT", "OK")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


class Handler(BaseHTTPRequestHandler):
    server_version = "mock-openai-capture/0.1"
    protocol_version = "HTTP/1.1"

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_raw": raw.decode("utf-8", errors="replace")}

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_completion(self, model: str) -> None:
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        chunks = [
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": RESPONSE_TEXT},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            },
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            payload = json.dumps(chunk, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path in ("/health", "/v1/health"):
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "gpt-5.4", "object": "model", "owned_by": "mock-openai-capture"}
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": f"not found: {self.path}"}})

    def do_POST(self) -> None:
        body = self._read_json()
        record = {
            "timestamp": time.time(),
            "method": "POST",
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body,
        }
        _write_json(CAPTURE_PATH, record)

        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": f"not found: {self.path}"}})
            return

        model = body.get("model", "gpt-5.4") if isinstance(body, dict) else "gpt-5.4"
        if isinstance(body, dict) and body.get("stream"):
            self._send_sse_completion(model)
            return
        self._send_json(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": RESPONSE_TEXT},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


if __name__ == "__main__":
    print(f"[mock-openai-capture] listening on {HOST}:{PORT}")
    print(f"[mock-openai-capture] capture file: {CAPTURE_PATH}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
