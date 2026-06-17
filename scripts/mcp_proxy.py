#!/usr/bin/env python3
"""Host shell proxy — runs on host, called from Termux via HTTP.

Executes shell commands on the host where CLI tools (tencent-news-cli, etc.)
are installed but unavailable on Android.

Usage:
    python3 scripts/mcp_proxy.py --port 8921 --allow-root "$PWD"
    # From Termux (via adb reverse):
    curl -s -X POST http://10.0.2.2:8921/run -d '{"cmd":"tencent-news-cli search AI --limit 5"}'

Architecture:
    Termux agent → shell_exec("curl 10.0.2.2:8921/run ...") → this proxy → host shell

This is a local development proxy. It binds to localhost by default and rejects
commands unless the requested working directory is under an allow-listed root.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "mcp_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOW_ROOTS: list[str] = []

# MCP client noise patterns to filter from stderr
_STDERR_NOISE = re.compile(
    r'(Failed to parse JSONRPC|pydantic_core\._pydantic_core\.ValidationError|'
    r'For further information visit https://errors\.pydantic|'
    r'File "/opt/homebrew/|File "/usr/|'
    r'message = types\.JSONRPCMessage|'
    r'return cls\.__pydantic_validator__|'
    r'^\s*\^+\s*$)',  # ^^^^^^^ underline lines
    re.MULTILINE
)


def _clean_tool_output(stdout: str, stderr: str, exit_code: int) -> dict:
    """Clean up tool_cli.py output for agent consumption.

    Strategy: parse inner JSON if present, extract summary + result,
    filter stderr noise. Never discard information — just reorganize.
    """
    base = {"ok": exit_code == 0, "exit_code": exit_code}

    # Try to parse stdout as JSON (mcp_proxy → tool_cli.py wraps output in JSON)
    inner = None
    try:
        inner = json.loads(stdout.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    if inner and isinstance(inner, dict) and "stdout" in inner:
        # This is a nested JSON wrapper (e.g. from another proxy layer)
        inner_stdout = inner.get("stdout", "")
        inner_stderr = inner.get("stderr", "")
        # Fall through to shared extraction below
    elif "▶ [" in stdout:
        # tool_cli.py text output format:
        # ▶ [tool.name]  dim=...
        #   参数: {...}
        #   ✅ 成功  (1.72s)
        #   输出:
        #   [...]
        inner_stdout = stdout
        inner_stderr = stderr
        # Fall through to shared extraction logic below
        pass
    else:
        # Not tool_cli.py output — return as-is (regular shell command)
        base["stdout"] = stdout
        if stderr.strip():
            base["stderr"] = stderr
        return base

    # Shared extraction logic for both JSON-wrapped and text tool_cli output
    tool_name = ""
    tool_match = re.search(r'▶ \[([^\]]+)\]', inner_stdout)
    if tool_match:
        tool_name = tool_match.group(1)

    summary = ""
    result_text = ""
    if "✅ 成功" in inner_stdout:
        summary = "success"
        output_match = re.search(r'输出[^:]*:\s*\n(.*)', inner_stdout, re.DOTALL)
        if output_match:
            result_text = output_match.group(1).strip()
            # Try to parse as JSON array and extract text fields
            try:
                result_arr = json.loads(result_text)
                if isinstance(result_arr, list):
                    texts = [item.get("text", "") for item in result_arr if isinstance(item, dict)]
                    if texts:
                        result_text = "\n".join(t for t in texts if t)
            except (json.JSONDecodeError, ValueError):
                pass
    elif "❌ 失败" in inner_stdout:
        summary = "failed"
        error_match = re.search(r'错误:\s*(.*)', inner_stdout, re.DOTALL)
        if error_match:
            result_text = error_match.group(1).strip()

    time_match = re.search(r'\((\d+\.?\d*s)\)', inner_stdout)
    elapsed = time_match.group(1) if time_match else ""

    # Clean stderr noise
    clean_stderr = _STDERR_NOISE.sub('', inner_stderr).strip()
    clean_stderr = re.sub(r'\n{3,}', '\n', clean_stderr).strip()

    base["ok"] = (summary == "success") if summary else (exit_code == 0)
    if tool_name:
        base["tool"] = tool_name
    if summary:
        base["summary"] = summary
    if elapsed:
        base["elapsed"] = elapsed
    if result_text:
        base["result"] = result_text
    if clean_stderr:
        base["stderr_filtered"] = clean_stderr
    # Raw always available
    base["raw_stdout"] = inner_stdout

    return base


class HostShellHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[host_proxy] {fmt % args}\n")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "type": "host_shell_proxy"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/upload":
            return self._handle_upload()
        if self.path != "/run":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        cmd = req.get("cmd", "")
        if not cmd:
            self._send_json(400, {"ok": False, "error": "missing 'cmd' field"})
            return

        cwd = req.get("cwd") or os.getcwd()
        try:
            cwd_real = os.path.realpath(str(cwd))
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid cwd"})
            return
        if not ALLOW_ROOTS or not any(
            cwd_real == root or cwd_real.startswith(root + os.sep) for root in ALLOW_ROOTS
        ):
            self._send_json(403, {
                "ok": False,
                "error": "cwd is outside --allow-root; pass cwd under an allowed root",
                "cwd": cwd_real,
            })
            return

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd_real,
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout = ANSI_RE.sub('', result.stdout)
            stderr = ANSI_RE.sub('', result.stderr)

            # Try to extract clean result from tool_cli.py output
            resp = _clean_tool_output(stdout, stderr, result.returncode)
            self._send_json(200, resp)
        except subprocess.TimeoutExpired:
            self._send_json(504, {"ok": False, "error": f"command timed out (60s)"})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})


    def _handle_upload(self):
        """Handle file upload from Termux.

        Usage from Termux:
            curl -s -F "file=@/path/to/local/file" http://10.0.2.2:8921/upload
            curl -s -F "file=@/path/to/file;filename=custom_name.zip" http://10.0.2.2:8921/upload

        Returns JSON: {"ok": true, "host_path": "/tmp/mcp_uploads/xxx_filename.ext"}
        """
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json(400, {"ok": False, "error": "Content-Type must be multipart/form-data. Use: curl -F 'file=@path'"})
            return

        # Parse boundary
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
        if not boundary:
            self._send_json(400, {"ok": False, "error": "missing boundary in Content-Type"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Simple multipart parser: find file content between boundaries
        boundary_bytes = boundary.encode()
        parts = body.split(b"--" + boundary_bytes)

        filename = "upload"
        file_data = None
        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            # Extract filename
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            header = part[:header_end].decode("utf-8", errors="replace")
            data = part[header_end + 4:]
            # Strip trailing \r\n--
            if data.endswith(b"\r\n"):
                data = data[:-2]

            import re as _re
            fn_match = _re.search(r'filename="([^"]+)"', header)
            if fn_match:
                filename = fn_match.group(1)
                # Sanitize
                filename = os.path.basename(filename)
            if b"name=\"file\"" in part or fn_match:
                file_data = data
                break

        if file_data is None:
            self._send_json(400, {"ok": False, "error": "no file found in upload"})
            return

        # Save with unique prefix
        safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
        host_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(host_path, "wb") as f:
            f.write(file_data)

        self._send_json(200, {
            "ok": True,
            "host_path": host_path,
            "filename": filename,
            "size": len(file_data),
        })


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Host shell proxy for Termux → host CLI execution")
    parser.add_argument("--port", type=int, default=8921)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; keep localhost unless isolated by adb/SSH")
    parser.add_argument("--allow-root", action="append", default=[], help="Allowed working-tree root for command execution; repeatable")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    global ALLOW_ROOTS
    ALLOW_ROOTS = [os.path.realpath(path) for path in args.allow_root]
    if not ALLOW_ROOTS:
        parser.error("at least one --allow-root is required")

    if args.daemon:
        if os.fork() != 0:
            return
        os.setsid()

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer((args.host, args.port), HostShellHandler)
    print(f"Host shell proxy listening on {args.host}:{args.port}")
    print("Allowed roots:")
    for root in ALLOW_ROOTS:
        print(f"  {root}")
    server.serve_forever()


if __name__ == "__main__":
    main()
