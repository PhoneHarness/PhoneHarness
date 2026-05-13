#!/usr/bin/env python3
"""Lightweight GUI proxy server for PhoneHarness on-device controller.

Runs on the host. Accepts HTTP requests from the device-side controller,
executes Android GUI commands (screencap, input, uiautomator) via adb shell,
and returns results.

Usage:
    python3 scripts/gui_proxy.py [--port 8919] [--serial emulator-5556]

The on-device controller calls this via:
    http://10.0.2.2:8919/screenshot
    http://10.0.2.2:8919/tap?x=100&y=200
    http://10.0.2.2:8919/ui_dump
"""

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class GUIProxyHandler(BaseHTTPRequestHandler):
    serial: str = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._json_response({"status": "ok", "type": "gui_proxy"})
            return

        if parsed.path == "/screenshot":
            self._handle_screenshot()
            return

        if parsed.path == "/screenshot/raw":
            self._handle_screenshot_raw()
            return

        if parsed.path == "/tap":
            x = params.get("x", [None])[0]
            y = params.get("y", [None])[0]
            if x is None or y is None:
                self._json_response({"ok": False, "error": "x and y required"}, status=400)
                return
            self._handle_tap(int(x), int(y))
            return

        if parsed.path == "/swipe":
            x1 = params.get("x1", [None])[0]
            y1 = params.get("y1", [None])[0]
            x2 = params.get("x2", [None])[0]
            y2 = params.get("y2", [None])[0]
            duration = params.get("duration", ["300"])[0]
            if not all([x1, y1, x2, y2]):
                self._json_response({"ok": False, "error": "x1,y1,x2,y2 required"}, status=400)
                return
            self._handle_swipe(int(x1), int(y1), int(x2), int(y2), int(duration))
            return

        if parsed.path == "/type":
            text = params.get("text", [None])[0]
            if text is None:
                self._json_response({"ok": False, "error": "text required"}, status=400)
                return
            self._handle_type(text)
            return

        if parsed.path == "/keyevent":
            key = params.get("key", [None])[0]
            if key is None:
                self._json_response({"ok": False, "error": "key required"}, status=400)
                return
            self._handle_keyevent(key)
            return

        if parsed.path == "/launch":
            package = params.get("package", [None])[0]
            if not package:
                self._json_response({"ok": False, "error": "package required"}, status=400)
                return
            pkg = shlex.quote(package)
            resolved = self._adb_shell(f"cmd package resolve-activity --brief {pkg} | tail -n 1")
            component = resolved["stdout"].strip().splitlines()[-1] if resolved["stdout"].strip() else ""
            if "/" in component and "No activity" not in component:
                result = self._adb_shell(f"am start -n {shlex.quote(component)}")
            else:
                component = ""
                result = self._adb_shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            self._json_response({
                "ok": result["exit_code"] == 0,
                "package": package,
                "component": component,
                "stdout": result["stdout"][-500:],
                "stderr": result["stderr"][-500:],
            })
            return

        if parsed.path == "/ui_dump":
            self._handle_ui_dump()
            return

        self._json_response({"ok": False, "error": f"unknown endpoint: {parsed.path}"}, status=404)

    def do_POST(self):
        # For endpoints that need body data
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
        parsed = urlparse(self.path)

        if parsed.path == "/type":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}
            text = data.get("text", "")
            if not text:
                self._json_response({"ok": False, "error": "text required"}, status=400)
                return
            self._handle_type(text)
            return

        self.do_GET()

    def _handle_screenshot(self):
        """Take screenshot via adb shell screencap, return as base64 PNG."""
        remote_path = "/data/local/tmp/phoneharness_screenshot.png"
        result = self._adb_shell(f"screencap -p {remote_path}")
        if result["exit_code"] != 0:
            self._json_response({"ok": False, "error": result["stderr"], "exit_code": result["exit_code"]})
            return

        # Pull screenshot to temp file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        pull_result = subprocess.run(
            ["adb", "-s", self.serial, "pull", remote_path, tmp_path],
            capture_output=True, text=True, timeout=10
        )
        if pull_result.returncode != 0:
            self._json_response({"ok": False, "error": f"pull failed: {pull_result.stderr}"})
            return

        # Read and base64 encode
        png_data = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)

        self._json_response({
            "ok": True,
            "format": "png",
            "size_bytes": len(png_data),
            "base64": base64.b64encode(png_data).decode("ascii"),
        })

    def _handle_screenshot_raw(self):
        """Take screenshot and return raw PNG bytes (for curl --output)."""
        remote_path = "/data/local/tmp/phoneharness_screenshot.png"
        result = self._adb_shell(f"screencap -p {remote_path}")
        if result["exit_code"] != 0:
            self._json_response({"ok": False, "error": result["stderr"]})
            return
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        pull_result = subprocess.run(
            ["adb", "-s", self.serial, "pull", remote_path, tmp_path],
            capture_output=True, text=True, timeout=10
        )
        if pull_result.returncode != 0:
            self._json_response({"ok": False, "error": f"pull failed: {pull_result.stderr}"})
            return
        png_data = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png_data)))
        self.end_headers()
        self.wfile.write(png_data)

    def _handle_tap(self, x: int, y: int):
        result = self._adb_shell(f"input tap {x} {y}")
        self._json_response({
            "ok": result["exit_code"] == 0,
            "action": "tap",
            "x": x, "y": y,
            "exit_code": result["exit_code"],
            "stderr": result["stderr"],
        })

    def _handle_swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int):
        result = self._adb_shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
        self._json_response({
            "ok": result["exit_code"] == 0,
            "action": "swipe",
            "from": [x1, y1], "to": [x2, y2], "duration": duration,
            "exit_code": result["exit_code"],
        })

    def _handle_type(self, text: str):
        import base64 as b64mod
        # Use ADBKeyboard base64 broadcast for reliable CJK input
        encoded = b64mod.b64encode(text.encode("utf-8")).decode("ascii")
        result = self._adb_shell(f"am broadcast -a ADB_INPUT_B64 --es msg {encoded}")
        self._json_response({
            "ok": result["exit_code"] == 0,
            "action": "type",
            "text_length": len(text),
            "exit_code": result["exit_code"],
        })

    def _handle_keyevent(self, key: str):
        result = self._adb_shell(f"input keyevent {key}")
        self._json_response({
            "ok": result["exit_code"] == 0,
            "action": "keyevent",
            "key": key,
            "exit_code": result["exit_code"],
        })

    def _handle_ui_dump(self):
        """Dump UI hierarchy via uiautomator."""
        remote_path = "/data/local/tmp/phoneharness_ui_dump.xml"
        result = self._adb_shell(f"uiautomator dump {remote_path}")
        if result["exit_code"] != 0:
            self._json_response({"ok": False, "error": result["stderr"], "exit_code": result["exit_code"]})
            return

        # Read the XML
        cat_result = self._adb_shell(f"cat {remote_path}")
        xml_content = cat_result["stdout"]

        # Parse into a simplified text representation
        self._json_response({
            "ok": True,
            "format": "xml",
            "size_bytes": len(xml_content),
            "content": xml_content,
        })

    def _adb_shell(self, command: str) -> dict:
        try:
            result = subprocess.run(
                ["adb", "-s", self.serial, "shell", command],
                capture_output=True, text=True, timeout=15
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "timeout"}

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Quiet logging
        pass


def _daemonize(logfile: str) -> None:
    """Detach from the parent process group and redirect stdio to a logfile."""
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def main():
    parser = argparse.ArgumentParser(description="PhoneHarness GUI Proxy Server")
    parser.add_argument("--port", type=int, default=8919)
    parser.add_argument("--serial", default="emulator-5556")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--logfile", default="/tmp/gui_proxy.log")
    args = parser.parse_args()

    if args.daemon:
        _daemonize(args.logfile)

    GUIProxyHandler.serial = args.serial
    server = HTTPServer(("0.0.0.0", args.port), GUIProxyHandler)
    print(f"GUI proxy listening on 0.0.0.0:{args.port} (serial={args.serial})")
    print(f"Device endpoint: http://10.0.2.2:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
