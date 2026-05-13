#!/usr/bin/env python3
"""Run small direct GUI-model smoke tests and write trace2html-compatible files."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib import parse, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoneharness.controllers.seed_gui import run_seed_gui_turn


CASES = [
    {
        "id": "wifi_settings",
        "setup": {"package": "com.android.settings", "required_ui_any": ["Settings", "Search settings"]},
        "goal": "当前已经打开系统设置首页。请进入 Wi-Fi 或 Network & internet 相关页面；如果已经看到 Internet、Wi-Fi 或 AndroidWifi 等网络相关设置，就直接完成。",
        "max_steps": 8,
    },
    {
        "id": "settings_display",
        "setup": {"package": "com.android.settings", "required_ui_any": ["Settings", "Search settings"]},
        "goal": "当前已经打开系统设置首页。请进入 Display 或显示相关页面；如果看到 Brightness、Dark theme、Display size 等显示设置，就直接完成。",
        "max_steps": 8,
    },
    {
        "id": "settings_battery",
        "setup": {"package": "com.android.settings", "required_ui_any": ["Settings", "Search settings"]},
        "goal": "当前已经打开系统设置首页。请进入 Battery 或电池相关页面；如果看到 Battery usage、Battery Saver 或电量百分比等电池设置，就直接完成。",
        "max_steps": 8,
    },
    {
        "id": "settings_apps",
        "setup": {"package": "com.android.settings", "required_ui_any": ["Settings", "Search settings"]},
        "goal": "当前已经打开系统设置首页。请进入 Apps 或应用相关页面；如果看到 Recently opened apps、Default apps 或 App info 等应用设置，就直接完成。",
        "max_steps": 8,
    },
    {
        "id": "calculator_3937",
        "setup": {"package": "com.google.android.calculator"},
        "goal": "请打开计算器应用，并使用屏幕按钮计算 368 乘以 12 再减去 479，看到结果 3937 后完成。",
        "max_steps": 12,
    },
]


def _get_json(url: str, timeout: int = 20) -> dict:
    with request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gui_get(gui_proxy_url: str, path: str, params: dict | None = None) -> dict:
    url = f"{gui_proxy_url.rstrip('/')}{path}"
    if params:
        url = f"{url}?{parse.urlencode(params)}"
    return _get_json(url)


def _run_adb(serial: str, args: list[str]) -> None:
    if not serial:
        return
    subprocess.run(["adb", "-s", serial] + args, check=False, capture_output=True, text=True, timeout=10)


def _setup_case(gui_proxy_url: str, case: dict, adb_serial: str = "") -> None:
    _gui_get(gui_proxy_url, "/keyevent", {"key": "KEYCODE_HOME"})
    time.sleep(0.5)
    package = case.get("setup", {}).get("package")
    if package:
        _run_adb(adb_serial, ["shell", "am", "force-stop", package])
        time.sleep(0.3)
        result = _gui_get(gui_proxy_url, "/launch", {"package": package})
        if not result.get("ok"):
            raise RuntimeError(f"launch failed for {package}: {result}")
        time.sleep(2)
    required = case.get("setup", {}).get("required_ui_any") or []
    if required:
        dump = _gui_get(gui_proxy_url, "/ui_dump")
        content = dump.get("content", "") if dump.get("ok") else ""
        if not any(token in content for token in required):
            raise RuntimeError(f"setup UI check failed; expected any of {required}")


def _write_trace(run_dir: Path, case_id: str, events: list[dict], report: dict) -> None:
    screenshot_dir = run_dir / f"{case_id}_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    clean_events = []
    for event in events:
        copied = dict(event)
        b64 = copied.pop("screenshot_b64", None)
        if b64 and copied.get("event") == "step":
            step = int(copied.get("step", 0))
            (screenshot_dir / f"step_{step:03d}.png").write_bytes(base64.b64decode(b64))
        clean_events.append(copied)

    (run_dir / f"{case_id}.nested.ndjson").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in clean_events),
        encoding="utf-8",
    )
    (run_dir / f"{case_id}.report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct Seed/AutoGLM GUI smoke")
    parser.add_argument("--gui-model", required=True)
    parser.add_argument("--gui-api-url", required=True)
    parser.add_argument("--gui-api-key", required=True)
    parser.add_argument("--gui-proxy-url", default="http://127.0.0.1:8919")
    parser.add_argument("--output-root", default="benchmark/traces/gui_smoke")
    parser.add_argument("--run-tag", default="")
    parser.add_argument("--cases", help="Comma-separated case ids to run")
    parser.add_argument("--adb-serial", default="", help="Optional adb serial for force-stop during setup")
    args = parser.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_model = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.gui_model)
    tag = f"{args.run_tag}_" if args.run_tag else ""
    run_dir = Path(args.output_root) / f"{tag}{safe_model}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    selected = CASES
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        selected = [c for c in CASES if c["id"] in wanted]
        missing = wanted - {c["id"] for c in selected}
        if missing:
            raise ValueError(f"Unknown case ids: {sorted(missing)}")

    results = []
    for case in selected:
        case_id = case["id"]
        print(f"=== {case_id}: {args.gui_model} ===", flush=True)
        events: list[dict] = []
        try:
            _setup_case(args.gui_proxy_url, case, adb_serial=args.adb_serial)
        except Exception as exc:
            report = {
                "task_id": case_id,
                "task_name": case_id,
                "status": "ENV_FAIL",
                "score": 0,
                "difficulty": "gui_smoke",
                "verifier_type": "manual_trace_review",
                "detail": f"setup failed: {exc}",
                "blocker": "env_setup_failed",
                "elapsed": 0,
                "steps": 0,
                "tool_calls": [],
                "gui_model": args.gui_model,
                "gui_api_url": args.gui_api_url,
            }
            _write_trace(run_dir, case_id, events, report)
            results.append(report)
            print(f"{case_id}: ENV_FAIL {exc}", flush=True)
            continue
        started = time.time()
        ok = run_seed_gui_turn(
            case["goal"],
            max_steps=case["max_steps"],
            gui_proxy_url=args.gui_proxy_url,
            gui_model=args.gui_model,
            gui_api_url=args.gui_api_url,
            gui_api_key=args.gui_api_key,
            on_event=events.append,
        )
        elapsed = round(time.time() - started, 1)
        step_count = sum(1 for e in events if e.get("event") == "step")
        errors = [e.get("message", "") for e in events if e.get("event") == "error"]
        report = {
            "task_id": case_id,
            "task_name": case_id,
            "status": "PASS" if ok else "FAIL",
            "score": 1 if ok else 0,
            "difficulty": "gui_smoke",
            "verifier_type": "manual_trace_review",
            "detail": f"direct GUI smoke: ok={ok}, steps={step_count}, errors={errors[:3]}",
            "blocker": "" if ok else "gui_smoke_failed",
            "elapsed": elapsed,
            "steps": step_count,
            "tool_calls": [e.get("name") for e in events if e.get("event") == "tool_call"],
            "gui_model": args.gui_model,
            "gui_api_url": args.gui_api_url,
        }
        _write_trace(run_dir, case_id, events, report)
        results.append(report)
        print(f"{case_id}: {report['status']} steps={step_count} elapsed={elapsed}s", flush=True)

    summary = {
        "gui_model": args.gui_model,
        "gui_api_url": args.gui_api_url,
        "run_dir": str(run_dir),
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "tasks": results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    subprocess.run(["python3", "scripts/trace2html_all.py", str(run_dir)], check=False)
    print(f"run_dir={run_dir}")
    print(f"dashboard={run_dir / 'all-traces.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
