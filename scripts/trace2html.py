#!/usr/bin/env python3
"""
trace2html — 将 agent trace 目录转化为可视化 HTML

用法:
  # 单个 trace 目录
  python scripts/trace2html.py benchmark/traces/.../20260415_204106

  # 批量：扫描某个 suite 下所有 run（递归查找含 .nested.ndjson 的目录）
  python scripts/trace2html.py benchmark/traces/hybrid_bench/4app_new --all

  # 生成后自动在浏览器打开
  python scripts/trace2html.py benchmark/traces/.../20260415_204106 --open

  # 强制覆盖已有的 trace-viewer.html
  python scripts/trace2html.py benchmark/traces/.../20260415_204106 --force

输入要求:
  目录下需要有:
    <task_id>.nested.ndjson   — 内层 seed_gui trace（含 timing + CoT）
    <task_id>.report.json     — 验证结果（可选）
    <task_id>_screenshots/    — 每步截图（可选）

输出:
  在同目录生成 trace-viewer.html，双击即可在浏览器查看
"""

import json, os, sys, argparse, subprocess, platform
from pathlib import Path


# ── Parse ──────────────────────────────────────────────────────────────────

import re as _re

def _parse_ndjson(path):
    """Read ndjson file, return list of dicts."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _split_model_raw_action(raw_action: str, reasoning: str = ""):
    """Split mixed model output into display-only rationale + final action."""
    raw = (raw_action or "").strip()
    if not raw:
        return "", ""
    action_patterns = [
        r'(<tool_call>.*?</tool_call>)\s*$',
        r'(<function=.*?</function>)\s*$',
        r'((?:do\s*\(\s*action\s*=|finish\s*\().*?\)\s*)$',
    ]
    for pat in action_patterns:
        m = _re.search(pat, raw, _re.DOTALL)
        if not m:
            continue
        action = m.group(1).strip()
        rationale = raw[:m.start(1)].strip()
        if reasoning:
            rationale = ""
        return rationale[:2000], action[:1000]
    return "", raw[:1000]


def _extract_steps(records, screenshot_dir):
    """Extract step list from ndjson records."""
    steps = []
    for r in records:
        if r.get("event") != "tool_result":
            continue
        sn = r["step"]
        step_ev = next((x for x in records if x.get("event") == "step" and x.get("step") == sn), {})
        tool_ev = next((x for x in records if x.get("event") == "tool_call" and x.get("step") == sn), {})
        timing = r.get("timing_ms", {})
        tokens = step_ev.get("tokens", {})
        args = tool_ev.get("arguments", {})
        reasoning = step_ev.get("reasoning", "")
        raw_action = step_ev.get("raw_action", "")
        model_rationale, model_action = _split_model_raw_action(raw_action, reasoning)
        steps.append({
            "num": sn,
            "ssMs": timing.get("screenshot", 0),
            "llmMs": timing.get("llm", 0),
            "actionMs": timing.get("action", 0),
            "totalMs": timing.get("total", 0),
            "tokensIn": tokens.get("prompt", 0),
            "tokensOut": tokens.get("completion", 0),
            "actionType": args.get("action_type", tool_ev.get("name", "")),
            "tool": tool_ev.get("name", ""),
            "params": {k: v for k, v in args.items() if k != "action_type"},
            "summary": r.get("summary", ""),
            "reasoning": reasoning,
            "rawAction": raw_action,
            "modelRationale": model_rationale,
            "modelAction": model_action,
            "img": f"{screenshot_dir}/step_{sn:03d}.png",
        })
    return steps


def _extract_outer_steps(records, base_dir="", task_id=""):
    """Extract steps from outer agent loop ndjson (non-nested format).

    Events: start, step, thinking, reasoning, tool_call, tool_result,
            assistant_text, done

    When a step is run_seed_gui_subtask and nested traces + screenshots exist,
    the inner steps are inlined with their screenshots.
    """
    steps = []
    reasoning_by_step = {}
    tool_call_by_step = {}
    tool_result_by_step = {}

    for r in records:
        ev = r.get("event", "")
        sn = r.get("step")
        if ev == "reasoning":
            reasoning_by_step[sn] = r.get("content", "")
        elif ev == "tool_call":
            tool_call_by_step[sn] = r
        elif ev == "tool_result":
            tool_result_by_step[sn] = r

    current_step = None
    for r in records:
        ev = r.get("event", "")
        if ev == "step":
            current_step = r.get("step")
        elif ev == "reasoning" and current_step is not None:
            reasoning_by_step[current_step] = r.get("content", "")

    # Find available nested traces and screenshot dirs for inlining
    nested_files = []  # [(index, ndjson_path, screenshot_dir)]
    if base_dir and task_id:
        idx = 0
        for f in sorted(os.listdir(base_dir)):
            m = _re.match(rf'^{_re.escape(task_id)}\.nested(?:_(\d+))?\.ndjson$', f)
            if m:
                idx += 1
                suffix = m.group(1) or ""
                ss_dir = f"{task_id}_screenshots_{suffix}" if suffix else f"{task_id}_screenshots"
                nested_files.append((idx, os.path.join(base_dir, f), ss_dir))

    # Build step objects
    gui_subtask_idx = 0
    for sn in sorted(tool_result_by_step.keys()):
        tr = tool_result_by_step[sn]
        tc = tool_call_by_step.get(sn, {})
        args = tc.get("arguments", {})
        tool_name = tc.get("name", "")
        timing = tr.get("timing_ms", {})
        tokens = tr.get("tokens", {})

        # Extract inline screenshot from gui_screenshot tool results
        # The screenshot base64 may be in the tool_result output or stored separately
        inline_img_b64 = ""
        if tool_name == "gui_screenshot" and tr.get("ok"):
            # Check if there's a screenshot in the trace event
            # gui_screenshot returns image via ToolResult.image_base64 which gets stored in the event
            output_str = str(tr.get("output", ""))
            # The image isn't in the ndjson output (too large), but we can save a placeholder
            # and check if the screenshot was saved to a file
            if base_dir and task_id:
                # Look for screenshot files saved by the outer loop
                import glob
                ss_candidates = glob.glob(os.path.join(base_dir, f"*screenshot*step_{sn:03d}*"))
                if ss_candidates:
                    inline_img_b64 = ss_candidates[0]  # path to image file

        step_data = {
            "num": sn,
            "ssMs": timing.get("screenshot", 0),
            "llmMs": timing.get("llm", 0),
            "actionMs": timing.get("action", 0),
            "totalMs": timing.get("total", 0),
            "tokensIn": tokens.get("prompt", 0),
            "tokensOut": tokens.get("completion", 0),
            "actionType": tool_name,
            "tool": tool_name,
            "params": args,
            "summary": tr.get("summary", ""),
            "output": tr.get("output", ""),
            "reasoning": reasoning_by_step.get(sn, ""),
            "ok": tr.get("ok", True),
            "img": inline_img_b64,
            "isGuiScreenshot": tool_name == "gui_screenshot",
        }

        # For run_seed_gui_subtask: inline nested screenshots
        if tool_name == "run_seed_gui_subtask" and gui_subtask_idx < len(nested_files):
            _, nested_path, ss_dir = nested_files[gui_subtask_idx]
            gui_subtask_idx += 1
            nested_records = _parse_ndjson(nested_path)
            inner_steps = _extract_steps(nested_records, ss_dir)

            step_data["isHeader"] = True
            steps.append(step_data)
            for inner in inner_steps:
                inner["isInner"] = True
                steps.append(inner)
        else:
            steps.append(step_data)

    # Add final assistant_text as a virtual step
    done_ev = next((r for r in records if r.get("event") == "done"), {})
    final_text = done_ev.get("text", "")
    if final_text:
        last_sn = (steps[-1]["num"] + 1) if steps else 1
        # Find reasoning for the final step: it's stored under the outer step number
        # which is max(tool_result step numbers) + 1
        outer_final_sn = (max(tool_result_by_step.keys()) + 1) if tool_result_by_step else last_sn
        final_reasoning = reasoning_by_step.get(outer_final_sn, "")
        # Reuse last screenshot as final state
        last_img = ""
        for s in reversed(steps):
            if s.get("img"):
                last_img = s["img"]
                break
        steps.append({
            "num": last_sn,
            "ssMs": 0, "llmMs": 0, "actionMs": 0, "totalMs": 0,
            "tokensIn": 0, "tokensOut": 0,
            "actionType": "answer",
            "tool": "final_answer",
            "params": {},
            "summary": final_text,
            "output": final_text,
            "reasoning": final_reasoning,
            "ok": True,
            "img": last_img,
        })

    return steps


def parse_trace_dir(base_dir):
    """解析一个 trace 目录，返回 [(task_id, task_obj, steps), ...]

    支持三种格式:
      1. <task_id>.nested.ndjson + <task_id>_screenshots/  (内层 seed_gui trace)
      2. <task_id>.nested_<app>.ndjson + <task_id>_screenshots_<app>/  (多子任务)
      3. <task_id>.ndjson  (外层 agent loop trace，带 reasoning + output)
    """
    files = os.listdir(base_dir)

    # 找所有 nested 文件，按 task_id 分组
    # 匹配: XXX.nested.ndjson 或 XXX.nested_yyy.ndjson
    nested_pattern = _re.compile(r'^(.+?)\.nested(?:_(.+))?\.ndjson$')

    # task_id -> [(ndjson_filename, app_suffix_or_None), ...]
    task_nested = {}
    for f in sorted(files):
        m = nested_pattern.match(f)
        if m:
            task_id, app_suffix = m.group(1), m.group(2)
            task_nested.setdefault(task_id, []).append((f, app_suffix))

    # Also find outer agent traces: <task_id>.ndjson (not .nested, not .report)
    outer_pattern = _re.compile(r'^(.+?)\.ndjson$')
    task_outer = {}
    for f in sorted(files):
        if ".nested" in f or ".report" in f:
            continue
        m = outer_pattern.match(f)
        if m:
            task_id = m.group(1)
            task_outer[task_id] = f

    if not task_nested and not task_outer:
        return []

    results = []
    for task_id, nested_list in task_nested.items():
        report_path = os.path.join(base_dir, f"{task_id}.report.json")
        report = {}
        if os.path.exists(report_path):
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)

        # 判断是否有 app 后缀的子任务文件
        # 数字后缀 (_1, _2, _3) = 同任务的多轮 GUI 尝试，全部按顺序合并
        # 非数字后缀 (_weibo, _xiaohongshu) = 同任务的多 app 子步骤，全部合并
        app_files = [(f, app) for f, app in nested_list if app is not None and not app.isdigit()]
        num_files = sorted([(f, app) for f, app in nested_list if app is not None and app.isdigit()],
                           key=lambda x: int(x[1]))
        plain_files = [(f, app) for f, app in nested_list if app is None]

        if app_files:
            # 多 app 子任务（如 weibo + xiaohongshu），全部合并
            use_files = app_files
        elif num_files:
            # 数字后缀：全部合并，标记为"第N轮尝试"
            use_files = num_files
        else:
            use_files = plain_files

        all_steps = []
        all_start_texts = []
        global_step_num = 0

        for nf, app_suffix in use_files:
            records = _parse_ndjson(os.path.join(base_dir, nf))

            start_text = next(
                (r.get("user_input", "") for r in records if r.get("event") == "start"), ""
            )
            all_start_texts.append(start_text)

            # 确定截图目录
            if app_suffix:
                ss_dir = f"{task_id}_screenshots_{app_suffix}"
            else:
                ss_dir = f"{task_id}_screenshots"

            steps = _extract_steps(records, ss_dir)

            # 给每步加 app 标签，重新编全局序号
            for s in steps:
                global_step_num += 1
                s["globalNum"] = global_step_num
                if app_suffix:
                    s["app"] = app_suffix

            all_steps.extend(steps)

        # 合并 start text
        if len(all_start_texts) == 1:
            combined_name = all_start_texts[0]
        else:
            combined_name = " → ".join(all_start_texts)

        task_obj = {
            "id": report.get("task_id", task_id),
            "name": combined_name,
            "shortName": report.get("task_name", task_id),
            "result": report.get("status", "UNKNOWN"),
            "score": report.get("score", 0),
            "difficulty": report.get("difficulty", ""),
            "detail": report.get("detail", ""),
            "blocker": report.get("blocker", ""),
            "elapsed": report.get("elapsed", 0),
            "subtasks": [app for _, app in use_files if app] or [],
        }
        results.append((task_id, task_obj, all_steps))

    # Parse outer agent traces (preferred over nested when both exist)
    for task_id, ndjson_file in task_outer.items():
        if task_id in task_nested:
            # Outer trace is the main view; remove nested-only result
            results = [r for r in results if r[0] != task_id]

        records = _parse_ndjson(os.path.join(base_dir, ndjson_file))
        if not records:
            continue

        report_path = os.path.join(base_dir, f"{task_id}.report.json")
        report = {}
        if os.path.exists(report_path):
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)

        start_text = next(
            (r.get("user_input", "") for r in records if r.get("event") == "start"), ""
        )
        done_ev = next((r for r in records if r.get("event") == "done"), {})

        steps = _extract_outer_steps(records, base_dir, task_id)
        for i, s in enumerate(steps):
            s["globalNum"] = i + 1

        task_obj = {
            "id": report.get("task_id", task_id),
            "name": start_text,
            "shortName": report.get("task_name", task_id),
            "result": report.get("status", "PASS" if done_ev.get("success") else "FAIL"),
            "score": report.get("score", 0),
            "difficulty": report.get("difficulty", ""),
            "detail": report.get("detail", ""),
            "blocker": report.get("blocker", ""),
            "elapsed": report.get("elapsed", done_ev.get("duration", 0)),
            "verifier_type": report.get("verifier_type", ""),
            "tool_calls": report.get("tool_calls", []),
            "subtasks": [],
        }
        results.append((task_id, task_obj, steps))

    return results


# ── Generate ───────────────────────────────────────────────────────────────

def generate_html(base_dir, task_id, task_obj, steps, force=False):
    """生成 HTML，返回输出路径"""
    nested_count = sum(1 for f in os.listdir(base_dir) if f.endswith(".nested.ndjson"))
    if nested_count > 1:
        out_path = os.path.join(base_dir, f"{task_id}.trace-viewer.html")
    else:
        out_path = os.path.join(base_dir, "trace-viewer.html")

    if os.path.exists(out_path) and not force:
        print(f"  Skip (exists): {out_path}  — use --force to overwrite")
        return out_path

    task_json = json.dumps(task_obj, ensure_ascii=False, indent=2)
    steps_json = json.dumps(steps, ensure_ascii=False, indent=2)
    subtitle = f"{task_id} · {task_obj['shortName']}"
    title = f"Agent Trace Viewer — {task_id} {task_obj['shortName']}"

    html = HTML_TEMPLATE
    html = html.replace("__TASK_JSON__", task_json)
    html = html.replace("__STEPS_JSON__", steps_json)
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__TITLE__", title)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    kb = os.path.getsize(out_path) / 1024
    print(f"  ✓ {out_path}  ({kb:.0f} KB, {len(steps)} steps, {task_obj['result']})")
    return out_path


# ── Find dirs ──────────────────────────────────────────────────────────────

def find_trace_dirs(root):
    """递归查找含 trace ndjson 的目录（nested 或 outer）"""
    result = []
    for dirpath, _, filenames in os.walk(root):
        has_nested = any(_re.match(r'.+\.nested.*\.ndjson$', f) for f in filenames)
        has_outer = any(
            f.endswith('.ndjson') and '.nested' not in f and '.report' not in f
            for f in filenames
        )
        if has_nested or has_outer:
            result.append(dirpath)
    return sorted(result)


def open_in_browser(path):
    s = platform.system()
    if s == "Darwin":
        subprocess.run(["open", path])
    elif s == "Linux":
        subprocess.run(["xdg-open", path])
    else:
        os.startfile(path)


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="trace2html: agent trace 目录 → 可视化 HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python scripts/trace2html.py benchmark/traces/.../20260415_204106
  python scripts/trace2html.py benchmark/traces/.../20260415_204106 --open
  python scripts/trace2html.py benchmark/traces/hybrid_bench/4app_new --all --force
""",
    )
    parser.add_argument("path", help="trace 目录路径（或 --all 时的父级目录）")
    parser.add_argument("--all", action="store_true", help="递归扫描所有含 nested trace 的子目录")
    parser.add_argument("--open", action="store_true", help="生成后自动在浏览器打开")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有的 HTML")
    args = parser.parse_args()

    target = os.path.abspath(args.path)
    if not os.path.isdir(target):
        print(f"Error: {target} is not a directory")
        sys.exit(1)

    # 收集要处理的目录
    if args.all:
        dirs = find_trace_dirs(target)
        if not dirs:
            print(f"No trace directories found under {target}")
            sys.exit(1)
        print(f"Found {len(dirs)} trace directories under {target}\n")
    else:
        dirs = [target]

    # 处理
    generated = []
    for d in dirs:
        print(f"📂 {os.path.relpath(d, os.getcwd())}")
        traces = parse_trace_dir(d)
        if not traces:
            print("  (no nested trace found, skip)")
            continue
        for task_id, task_obj, steps in traces:
            out = generate_html(d, task_id, task_obj, steps, force=args.force)
            if out:
                generated.append(out)

    # 汇总
    print(f"\nDone: {len(generated)} HTML files")

    # 打开
    if args.open and generated:
        for p in generated:
            open_in_browser(p)


# ── HTML Template ──────────────────────────────────────────────────────────
# 数据通过 json.dumps 注入，不存在引号转义问题

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--surface2:#242836;--border:#2e3348;--text:#e4e6f0;--text2:#8b8fa8;--accent:#6c7cff;--red:#ff6b6b;--green:#5cffa0;--cyan:#5ce1ff;--orange:#ffaa5c;--radius:10px}
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
  .header{position:sticky;top:0;z-index:100;background:rgba(15,17,23,0.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:16px 32px;display:flex;align-items:center;gap:16px}
  .header h1{font-size:18px;font-weight:600;background:linear-gradient(135deg,var(--accent),var(--cyan));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .header .subtitle{color:var(--text2);font-size:13px}
  .main{max-width:1400px;margin:0 auto;padding:24px 32px 64px}
  .task-title{font-size:15px;font-weight:600;margin-bottom:20px;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);display:flex;align-items:center;gap:10px;flex-wrap:wrap;line-height:1.6}
  .badge{font-size:11px;padding:3px 10px;border-radius:100px;font-weight:600;flex-shrink:0}
  .badge-fail{background:rgba(255,107,107,0.15);color:var(--red)}.badge-pass{background:rgba(92,255,160,0.15);color:var(--green)}
  .task-detail{font-size:12px;color:var(--text2);margin-left:auto}
  .summary-bar{display:flex;gap:14px;margin-bottom:24px;flex-wrap:wrap}
  .summary-card{flex:1;min-width:130px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px}
  .summary-card .label{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
  .summary-card .value{font-size:24px;font-weight:700;margin-top:4px}
  .ok-badge{font-size:10px;padding:2px 8px;border-radius:100px;font-weight:600}.ok-true{background:rgba(92,255,160,0.15);color:var(--green)}.ok-false{background:rgba(255,107,107,0.15);color:var(--red)}
  .step-card.is-inner{margin-left:28px;border-left:3px solid var(--cyan);opacity:0.95}
  .step-card.is-header{border-left:3px solid var(--accent);background:var(--surface2)}
  .summary-card .sub{font-size:11px;color:var(--text2);margin-top:2px}
  .section-title{font-size:14px;font-weight:600;margin-bottom:12px;color:var(--text2)}
  .waterfall{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:28px;overflow-x:auto}
  .wf-row{display:flex;align-items:center;gap:10px;padding:4px 0;border-bottom:1px solid rgba(46,51,72,0.3)}.wf-row:last-of-type{border-bottom:none}
  .wf-label{width:150px;flex-shrink:0;font-size:11px;color:var(--text2);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .wf-bars{flex:1;display:flex;height:20px;min-width:250px}
  .wf-bar{height:100%;border-radius:3px;min-width:2px;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:600;color:rgba(255,255,255,0.85);cursor:default}
  .bar-ss{background:linear-gradient(90deg,#5ce1ff,#3db8d8)}.bar-llm{background:linear-gradient(90deg,#6c7cff,#4e5bbd)}.bar-act{background:linear-gradient(90deg,#5cffa0,#3dcc7a)}
  .bar-llm.bottleneck{background:linear-gradient(90deg,#ff6b6b,#cc4444);box-shadow:0 0 10px rgba(255,107,107,0.3)}
  .wf-time{width:55px;flex-shrink:0;font-size:11px;color:var(--text2);text-align:right;font-variant-numeric:tabular-nums}
  .wf-legend{display:flex;gap:14px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}
  .legend-i{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text2)}.legend-dot{width:10px;height:10px;border-radius:3px}
  .step-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:12px;overflow:hidden;transition:border-color .2s}
  .step-card:hover{border-color:var(--accent)}.step-card.is-bn{border-color:var(--red)}.step-card.is-bn .step-hdr{background:rgba(255,107,107,0.06)}
  .step-hdr{display:flex;align-items:center;gap:12px;padding:10px 16px;cursor:pointer;user-select:none}
  .step-num{width:28px;height:28px;border-radius:7px;background:var(--surface2);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--accent);flex-shrink:0}
  .is-bn .step-num{background:rgba(255,107,107,0.15);color:var(--red)}
  .step-info{flex:1;min-width:0}.step-action{font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px}
  .step-detail{font-size:11px;color:var(--text2);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .step-chips{display:flex;gap:6px;flex-shrink:0;flex-wrap:wrap}
  .chip{font-size:10px;padding:2px 8px;border-radius:100px;font-weight:500;font-variant-numeric:tabular-nums}
  .chip-ss{background:rgba(92,225,255,0.12);color:var(--cyan)}.chip-llm{background:rgba(108,124,255,0.12);color:var(--accent)}.chip-act{background:rgba(92,255,160,0.12);color:var(--green)}
  .chip-bn{background:rgba(255,107,107,0.15);color:var(--red)}.chip-call{background:rgba(255,170,92,0.15);color:var(--orange)}
  .bn-badge{font-size:9px;padding:2px 7px;border-radius:100px;background:rgba(255,107,107,0.15);color:var(--red);font-weight:600}
  .arrow{color:var(--text2);font-size:14px;transition:transform .2s;flex-shrink:0}.step-card.open .arrow{transform:rotate(180deg)}
  .step-body{max-height:0;overflow:hidden;transition:max-height .35s ease}.step-card.open .step-body{max-height:3000px}
  .step-body-inner{padding:0 16px 16px;display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .step-img{border-radius:8px;overflow:hidden;border:1px solid var(--border);background:#000;display:flex;align-items:center;justify-content:center;max-height:480px}
  .step-img img{width:100%;height:100%;object-fit:contain}.step-img .no-img{color:var(--text2);font-size:12px;padding:36px;text-align:center}
  .detail-tbl{font-size:12px}.d-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(46,51,72,0.4)}.d-row:last-child{border-bottom:none}
  .d-label{color:var(--text2)}.d-val{font-weight:500;font-variant-numeric:tabular-nums}
  .tok-bar-bg{height:5px;border-radius:3px;background:var(--surface2);overflow:hidden;margin-top:6px}.tok-bar-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--accent),var(--cyan))}
  .tok-label{font-size:9px;color:var(--text2);margin-top:2px}
  .thought-box{grid-column:1/-1;background:var(--surface2);border-radius:8px;padding:12px 14px;font-size:12px;line-height:1.7;color:var(--text2);max-height:220px;overflow-y:auto;white-space:pre-wrap;word-break:break-word}
  .thought-box strong{color:var(--text)}
  @media(max-width:768px){.main{padding:0 16px 48px}.step-body-inner{grid-template-columns:1fr}}
  ::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>
<div class="header"><h1>Agent Trace Viewer</h1><span class="subtitle">__SUBTITLE__</span></div>
<div class="main" id="app"></div>
<script>
const TASK = __TASK_JSON__;
const STEPS = __STEPS_JSON__;

const fmtMs=ms=>!ms||ms<=0?'-':ms<1000?Math.round(ms)+'ms':(ms/1000).toFixed(1)+'s';
const fmtS=ms=>(ms/1000).toFixed(1)+'s';
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML;};

const hasTiming=STEPS.some(s=>s.totalMs>0);
const isOuterTrace=!hasTiming&&TASK.elapsed>0;
const totalTime=hasTiming?STEPS.reduce((s,st)=>s+st.totalMs,0):(TASK.elapsed*1000);
const totalLLM=STEPS.reduce((s,st)=>s+st.llmMs,0);
const totalSS=STEPS.reduce((s,st)=>s+st.ssMs,0);
const totalTokIn=STEPS.reduce((s,st)=>s+st.tokensIn,0);
const totalTokOut=STEPS.reduce((s,st)=>s+st.tokensOut,0);
const maxLLM=Math.max(...STEPS.map(s=>s.llmMs));
const BN_THRESHOLD_MS=10000;
const bnIdx=maxLLM>=BN_THRESHOLD_MS?STEPS.findIndex(s=>s.llmMs===maxLLM):-1;
const maxTotal=Math.max(...STEPS.map(s=>s.totalMs));
const maxTokOut=Math.max(...STEPS.map(s=>s.tokensOut),1);
const callUserCount=STEPS.filter(s=>s.actionType==='call_user').length;

const app=document.getElementById('app');
let h='';

const rb=TASK.result==='PASS'
  ?'<span class="badge badge-pass">✓ PASS</span>'
  :'<span class="badge badge-fail">✗ '+esc(TASK.result)+'</span>';

h+=`<div class="task-title">${rb}<span>${esc(TASK.name)}</span><span class="task-detail">${esc(TASK.detail)}</span></div>`;

h+=`<div class="summary-bar">
  <div class="summary-card"><div class="label">总耗时</div><div class="value" style="color:var(--accent)">${fmtS(totalTime)}</div><div class="sub">${STEPS.length} 步 · ${esc(TASK.difficulty)}</div></div>
  <div class="summary-card"><div class="label">LLM 耗时</div><div class="value" style="color:var(--accent)">${fmtS(totalLLM)}</div><div class="sub">${totalTime>0?(totalLLM/totalTime*100).toFixed(0):0}% 占比</div></div>
  <div class="summary-card"><div class="label">截图耗时</div><div class="value" style="color:var(--cyan)">${fmtS(totalSS)}</div><div class="sub">${totalTime>0?(totalSS/totalTime*100).toFixed(0):0}% 占比</div></div>
  <div class="summary-card"><div class="label">Tokens</div><div class="value" style="color:var(--green)">${(totalTokIn+totalTokOut).toLocaleString()}</div><div class="sub">in ${totalTokIn.toLocaleString()} / out ${totalTokOut.toLocaleString()}</div></div>
  ${callUserCount>0?`<div class="summary-card"><div class="label">call_user</div><div class="value" style="color:var(--orange)">${callUserCount} 次</div><div class="sub">模型求助</div></div>`:''}
  ${TASK.blocker?`<div class="summary-card"><div class="label">Blocker</div><div class="value" style="color:var(--orange);font-size:13px">${esc(TASK.blocker)}</div><div class="sub">${esc(TASK.detail)}</div></div>`:''}
  ${TASK.subtasks&&TASK.subtasks.length>0?`<div class="summary-card"><div class="label">子任务</div><div class="value" style="color:var(--cyan);font-size:16px">${TASK.subtasks.map(esc).join(' → ')}</div><div class="sub">${TASK.subtasks.length} 个 APP</div></div>`:''}
  ${TASK.verifier_type?`<div class="summary-card"><div class="label">验证方式</div><div class="value" style="font-size:16px;color:var(--orange)">${esc(TASK.verifier_type)}</div><div class="sub">${TASK.tool_calls?TASK.tool_calls.map(esc).join(', '):''}</div></div>`:''}
</div>`;

// Verdict: PASS/FAIL 判定理由
const _lastStep = STEPS[STEPS.length-1];
const _lastR = _lastStep ? _lastStep.reasoning : '';
const _vc = TASK.result==='PASS' ? 'var(--green)' : 'var(--red)';
const _vbg = TASK.result==='PASS' ? 'rgba(92,255,160,0.06)' : 'rgba(255,107,107,0.06)';
const _vi = TASK.result==='PASS' ? '✅' : '❌';
let _vl = [];
if(TASK.detail) _vl.push('<strong>验证结果：</strong>' + esc(TASK.detail));
if(TASK.blocker) _vl.push('<strong>Blocker：</strong>' + esc(TASK.blocker));
if(_lastR) _vl.push('<strong>模型最终判断：</strong>' + esc(_lastR.length>300?_lastR.slice(0,300)+'…':_lastR));
if(_vl.length>0){
  h+=`<div style="margin-bottom:18px;padding:14px 16px;background:${_vbg};border:1px solid ${_vc};border-radius:var(--radius);border-left:4px solid ${_vc}">
    <div style="font-size:13px;font-weight:600;color:${_vc};margin-bottom:8px">${_vi} ${TASK.result==='PASS'?'成功':'失败'}判定理由</div>
    <div style="font-size:12px;line-height:1.7;color:var(--text2)">${_vl.join('<br><br>')}</div>
  </div>`;
}

if(!isOuterTrace){
h+=`<div class="section-title">⏱ 时间瀑布图</div><div class="waterfall">`;
for(let i=0;i<STEPS.length;i++){
  const s=STEPS[i],isBn=i===bnIdx;
  const ssPct=s.ssMs/maxTotal*100,llmPct=s.llmMs/maxTotal*100,actPct=s.actionMs/maxTotal*100;
  const wfNum=s.globalNum||s.num;
  const wfApp=s.app?(/^\d+$/.test(s.app)?' [R'+s.app+']':' ['+esc(s.app)+']'):'';
  h+=`<div class="wf-row">
    <div class="wf-label">Step ${wfNum}${wfApp} · ${esc(s.actionType)}</div>
    <div class="wf-bars">
      <div class="wf-bar bar-ss" style="width:${Math.max(ssPct,0.5)}%" title="截图 ${fmtMs(s.ssMs)}">${ssPct>6?fmtMs(s.ssMs):''}</div>
      <div class="wf-bar bar-llm${isBn?' bottleneck':''}" style="width:${Math.max(llmPct,0.5)}%" title="LLM ${fmtMs(s.llmMs)}">${llmPct>6?fmtMs(s.llmMs):''}</div>
      ${s.actionMs>0?`<div class="wf-bar bar-act" style="width:${Math.max(actPct,0.5)}%" title="Action ${fmtMs(s.actionMs)}">${actPct>6?fmtMs(s.actionMs):''}</div>`:''}
    </div>
    <div class="wf-time">${fmtS(s.totalMs)}</div>
  </div>`;
}
h+=`<div class="wf-legend">
  <div class="legend-i"><div class="legend-dot" style="background:var(--cyan)"></div>截图</div>
  <div class="legend-i"><div class="legend-dot" style="background:var(--accent)"></div>LLM</div>
  <div class="legend-i"><div class="legend-dot" style="background:var(--green)"></div>Action</div>
  <div class="legend-i"><div class="legend-dot" style="background:var(--red)"></div>瓶颈</div>
</div></div>`;
} else {
  // Outer trace: show tool flow instead of waterfall
  h+=`<div class="section-title">🔗 工具调用流</div><div class="waterfall">`;
  const flowSteps=STEPS.filter(s=>s.tool!=='final_answer');
  for(let i=0;i<flowSteps.length;i++){
    const s=flowSteps[i];
    const okIcon=s.ok?'✅':'❌';
    h+=`<div class="wf-row">
      <div class="wf-label">Step ${s.globalNum||s.num}</div>
      <div style="flex:1;font-size:12px;display:flex;align-items:center;gap:8px">
        <span style="font-weight:600;color:var(--accent)">${esc(s.tool)}</span>
        <span>${okIcon}</span>
        <span style="color:var(--text2)">${esc(s.summary.length>80?s.summary.slice(0,80)+'…':s.summary)}</span>
      </div>
    </div>`;
  }
  h+=`</div>`;
}

h+=`<div class="section-title">📋 分步详情 · ${STEPS.length} 步（点击展开截图 + CoT）</div>`;
let lastApp='';
for(let i=0;i<STEPS.length;i++){
  const s=STEPS[i],isBn=i===bnIdx,isCall=s.actionType==='call_user';
  if(s.app&&s.app!==lastApp){lastApp=s.app;const appLabel=/^\d+$/.test(s.app)?'🔄 第'+s.app+'轮 GUI 尝试':'📱 '+esc(s.app);h+=`<div style="margin:18px 0 10px;padding:8px 14px;background:var(--surface2);border-radius:8px;font-size:13px;font-weight:600;color:var(--cyan);border-left:3px solid var(--cyan)">${appLabel}</div>`;}
  const paramStr=Object.entries(s.params||{}).map(([k,v])=>k+': '+JSON.stringify(v)).join(', ')||'—';
  const dispNum=s.globalNum||s.num;
  const isInner=s.isInner||false,isHdr=s.isHeader||false;
  h+=`<div class="step-card${isBn?' is-bn':''}${isInner?' is-inner':''}${isHdr?' is-header':''}" data-idx="${i}">
    <div class="step-hdr">
      <div class="step-num">${dispNum}</div>
      <div class="step-info">
        <div class="step-action">${esc(s.tool)} → ${esc(s.actionType)}${isBn?' <span class="bn-badge">⚠ 瓶颈</span>':''}${isCall?' <span class="bn-badge" style="background:rgba(255,170,92,0.15);color:var(--orange)">📞 求助</span>':''}${s.ok===false?' <span class="ok-badge ok-false">FAIL</span>':s.ok===true&&s.tool!=='final_answer'?' <span class="ok-badge ok-true">OK</span>':''}</div>
        <div class="step-detail">${esc(s.summary.length>150?s.summary.slice(0,150)+'…':s.summary)} · ${esc(paramStr)}</div>
      </div>
      <div class="step-chips">
        ${!isOuterTrace?`<span class="chip chip-ss">截图 ${fmtMs(s.ssMs)}</span>
        <span class="chip ${isBn?'chip-bn':'chip-llm'}">LLM ${fmtMs(s.llmMs)}</span>
        ${s.actionMs>0?`<span class="chip chip-act">执行 ${fmtMs(s.actionMs)}</span>`:''}`
        :`${s.reasoning?'<span class="chip chip-llm">有 CoT</span>':s.modelRationale?'<span class="chip chip-llm">有模型说明</span>':''}`}
        ${isCall?'<span class="chip chip-call">call_user</span>':''}
      </div>
      <div class="arrow">▾</div>
    </div>
    <div class="step-body"><div class="step-body-inner" ${!s.img?'style="grid-template-columns:1fr"':''}>
      ${s.img?`<div class="step-img"><img src="${s.img}" alt="Step ${s.num}" onerror="this.outerHTML='<div class=no-img>📷 截图加载失败</div>'"></div>`:''}
      <div class="detail-tbl">
        <div class="d-row"><span class="d-label">总耗时</span><span class="d-val">${fmtMs(s.totalMs)}</span></div>
        <div class="d-row"><span class="d-label">截图</span><span class="d-val">${fmtMs(s.ssMs)}</span></div>
        <div class="d-row"><span class="d-label">LLM</span><span class="d-val">${fmtMs(s.llmMs)}</span></div>
        <div class="d-row"><span class="d-label">Action</span><span class="d-val">${fmtMs(s.actionMs)}</span></div>
        <div class="d-row"><span class="d-label">Tokens (in)</span><span class="d-val">${s.tokensIn.toLocaleString()}</span></div>
        <div class="d-row"><span class="d-label">Tokens (out)</span><span class="d-val">${s.tokensOut.toLocaleString()}</span></div>
        <div class="tok-bar-bg"><div class="tok-bar-fill" style="width:${(s.tokensOut/maxTokOut*100).toFixed(1)}%"></div></div>
        <div class="tok-label">输出 token 占比（max = ${maxTokOut}）</div>
      </div>
      ${s.reasoning?`<div class="thought-box"><strong>🧠 CoT 思考链：</strong>\n${esc(s.reasoning)}</div>`:''}
      ${!s.reasoning&&s.modelRationale?`<div class="thought-box"><strong>🧠 模型原始说明：</strong>\n${esc(s.modelRationale)}</div>`:''}
      ${s.modelAction?`<div class="thought-box" style="border-left:3px solid var(--orange)"><strong>🎯 模型动作：</strong>\n${esc(s.modelAction)}</div>`:''}
      ${s.output&&s.output!==s.summary?`<div class="thought-box" style="border-left:3px solid var(--cyan)"><strong>📤 完整输出：</strong>\n${esc(s.output)}</div>`
       :s.summary?`<div class="thought-box" style="border-left:3px solid var(--green)"><strong>📋 Action：</strong> ${esc(s.tool)} → ${esc(s.summary)}${Object.keys(s.params||{}).length>0?'\\n参数: '+esc(JSON.stringify(s.params)):''}</div>`:''}
    </div></div>
  </div>`;
}

app.innerHTML=h;
document.querySelectorAll('.step-hdr').forEach(hdr=>{
  hdr.addEventListener('click',()=>hdr.parentElement.classList.toggle('open'));
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
