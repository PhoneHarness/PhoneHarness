#!/usr/bin/env python3
"""
PhoneHarness Benchmark — Runner

依次向 phoneharness server 发送 10 条任务 prompt，收集 NDJSON trace。
支持切换模型，自动清理设备产物目录。

用法:
    # 单模型
    python run_benchmark.py --model doubao-2.0-pro --server http://localhost:8920

    # 三模型对比
    python run_benchmark.py --models doubao-2.0-pro gemini-3.1-pro glm-5

    # 跑完后自动评分
    python run_benchmark.py --model doubao-2.0-pro --auto-grade
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_FILE = os.path.join(BENCHMARK_DIR, "tasks.yaml")
DEFAULT_SERVER = "http://localhost:8920"
DEFAULT_DEVICE = "emulator-5556"


def load_tasks() -> list:
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["tasks"]


def setup_output_dir(model_name: str) -> str:
    """创建输出目录: benchmark/traces/<model>/<timestamp>/"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(BENCHMARK_DIR, "traces", model_name, ts)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "artifacts"), exist_ok=True)
    return out_dir


def clean_device_home(serial: str):
    """清理设备上 home 目录的产物文件（保留测试素材）"""
    print("🧹 清理设备产物目录...")
    keep_patterns = ["ocr_test_*.png", "phoneharness", ".bashrc", ".profile"]
    # 列出 home 目录文件，删除非保留文件
    cmd = (
        f"adb -s {serial} shell "
        "'cd ~ && for f in *.xlsx *.docx *.pptx *.pdf *.jpg *.png *.txt; '  "
        "'do [ -f \"$f\" ] && rm -f \"$f\" && echo \"deleted: $f\"; done'"
    )
    subprocess.run(cmd, shell=True, capture_output=True)
    # 也清理 /sdcard/tmp/
    subprocess.run(
        f"adb -s {serial} shell 'rm -rf /sdcard/tmp && mkdir -p /sdcard/tmp'",
        shell=True, capture_output=True,
    )


def pull_artifacts(serial: str, out_dir: str):
    """从设备拉取产物文件"""
    artifacts_dir = os.path.join(out_dir, "artifacts")
    print(f"📥 拉取产物到 {artifacts_dir}")

    # 拉取 home 目录产物
    for ext in ["xlsx", "docx", "pptx", "pdf", "jpg", "png", "txt"]:
        subprocess.run(
            f"adb -s {serial} shell 'ls ~/*.{ext} 2>/dev/null' | "
            f"while read f; do adb -s {serial} pull \"$f\" {artifacts_dir}/; done",
            shell=True, capture_output=True,
        )
    # 拉取 /sdcard/tmp/ 产物
    subprocess.run(
        f"adb -s {serial} pull /sdcard/tmp/ {artifacts_dir}/sdcard_tmp/ 2>/dev/null",
        shell=True, capture_output=True,
    )


def run_task(server_url: str, task_id: str, prompt: str,
             model: str, out_dir: str, timeout: int = 300) -> dict:
    """向 phoneharness server 发送一条任务，收集 NDJSON 响应"""
    import urllib.request

    url = f"{server_url}/run"
    payload = json.dumps({
        "input": prompt,
        "model": model,
    }).encode("utf-8")

    trace_file = os.path.join(out_dir, f"{task_id}.ndjson")
    meta = {
        "task_id": task_id,
        "model": model,
        "prompt": prompt,
        "start_time": datetime.now().isoformat(),
        "events": 0,
        "tool_calls": 0,
        "errors": 0,
        "elapsed_seconds": 0,
    }

    print(f"\n{'─'*50}")
    print(f"▶ {task_id}: {prompt[:60]}...")
    print(f"  Model: {model} | Server: {server_url}")

    start = time.time()
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(trace_file, "w", encoding="utf-8") as f:
                for line in resp:
                    decoded = line.decode("utf-8").strip()
                    if not decoded:
                        continue
                    f.write(decoded + "\n")
                    f.flush()
                    meta["events"] += 1

                    try:
                        event = json.loads(decoded)
                        # 兼容 "type" 和 "event" 两种字段名
                        etype = event.get("type", "") or event.get("event", "")
                        if etype == "tool_call":
                            meta["tool_calls"] += 1
                            tool_name = event.get("tool", event.get("name", "?"))
                            print(f"  🔧 {tool_name}")
                        elif etype == "tool_result":
                            is_err = event.get("is_error", False) or event.get("ok") is False
                            if is_err:
                                meta["errors"] += 1
                                print(f"  ❌ Error in tool result")
                            else:
                                print(f"  ✅ {event.get('name', '?')} ok")
                        elif etype in ("text", "assistant_text"):
                            text = event.get("content", event.get("text", ""))[:80]
                            if text:
                                print(f"  💬 {text}...")
                        elif etype == "done":
                            print(f"  🏁 Done")
                        elif etype == "error":
                            meta["errors"] += 1
                            print(f"  ❌ {event.get('message', '?')}")
                    except json.JSONDecodeError:
                        pass

    except Exception as e:
        meta["error"] = str(e)
        print(f"  ❌ Request failed: {e}")

    elapsed = time.time() - start
    meta["elapsed_seconds"] = round(elapsed, 1)
    meta["end_time"] = datetime.now().isoformat()

    # 写 meta
    meta_file = os.path.join(out_dir, f"{task_id}.meta.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"  ⏱ {elapsed:.1f}s | {meta['tool_calls']} tool calls | {meta['errors']} errors")
    return meta


def run_benchmark(model: str, server_url: str, serial: str,
                  auto_grade: bool = False, skip_clean: bool = False):
    """跑一个模型的完整 benchmark"""
    tasks = load_tasks()
    out_dir = setup_output_dir(model)

    print(f"\n{'='*60}")
    print(f"  PhoneHarness Benchmark")
    print(f"  Model: {model}")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}")

    if not skip_clean:
        clean_device_home(serial)

    all_meta = []
    for task in tasks:
        task_id = task["id"]
        prompt = task["prompt"]
        meta = run_task(server_url, task_id, prompt, model, out_dir)
        all_meta.append(meta)

        # 每条任务间隔 5 秒，让设备喘口气
        time.sleep(5)

    # 拉取产物
    pull_artifacts(serial, out_dir)

    # 写汇总
    summary_file = os.path.join(out_dir, "summary.json")
    summary = {
        "model": model,
        "timestamp": datetime.now().isoformat(),
        "total_tasks": len(tasks),
        "total_elapsed": sum(m["elapsed_seconds"] for m in all_meta),
        "total_tool_calls": sum(m["tool_calls"] for m in all_meta),
        "total_errors": sum(m["errors"] for m in all_meta),
        "tasks": all_meta,
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  Benchmark 完成!")
    print(f"  总耗时: {summary['total_elapsed']:.0f}s")
    print(f"  总工具调用: {summary['total_tool_calls']}")
    print(f"  总错误: {summary['total_errors']}")
    print(f"  Trace 目录: {out_dir}")
    print(f"{'='*60}")

    # 自动评分
    if auto_grade:
        print("\n🏆 自动评分中...")
        grader_cmd = [
            sys.executable, os.path.join(BENCHMARK_DIR, "grader.py"),
            "--trace-dir", out_dir,
            "--artifacts", os.path.join(out_dir, "artifacts"),
            "--output", os.path.join(out_dir, "grades.json"),
        ]
        subprocess.run(grader_cmd)

    return out_dir


def main():
    parser = argparse.ArgumentParser(description="PhoneHarness Benchmark Runner")
    parser.add_argument("--model", help="单模型名称 (如 doubao-2.0-pro)")
    parser.add_argument("--models", nargs="+", help="多模型列表")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"phoneharness server URL (默认 {DEFAULT_SERVER})")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"设备 serial (默认 {DEFAULT_DEVICE})")
    parser.add_argument("--auto-grade", action="store_true", help="跑完后自动评分")
    parser.add_argument("--skip-clean", action="store_true", help="跳过设备清理")
    parser.add_argument("--task", help="只跑指定任务 (如 C3)")
    args = parser.parse_args()

    if args.models:
        # 多模型依次跑
        trace_dirs = []
        for model in args.models:
            out_dir = run_benchmark(
                model, args.server, args.device,
                auto_grade=False, skip_clean=args.skip_clean,
            )
            trace_dirs.append(out_dir)

        # 统一评分对比
        print("\n🏆 多模型对比评分...")
        grader_cmd = [
            sys.executable, os.path.join(BENCHMARK_DIR, "grader.py"),
            "--compare", *trace_dirs,
            "--output", os.path.join(BENCHMARK_DIR, "traces", "comparison.json"),
        ]
        subprocess.run(grader_cmd)

    elif args.model:
        if args.task:
            # 只跑单条任务
            tasks = load_tasks()
            task = next((t for t in tasks if t["id"] == args.task), None)
            if not task:
                print(f"❌ 任务 {args.task} 不存在")
                sys.exit(1)
            out_dir = setup_output_dir(args.model)
            run_task(args.server, task["id"], task["prompt"], args.model, out_dir)
        else:
            run_benchmark(args.model, args.server, args.device,
                          args.auto_grade, args.skip_clean)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
