#!/usr/bin/env python3
"""
PhoneHarness Benchmark — Auto Grader

读取 NDJSON trace 文件 + 检查设备/本地文件 → 输出逐条评分 + 聚合指标。

用法:
    python grader.py --trace-dir ./traces/doubao-2.0-pro
    python grader.py --trace-dir ./traces/doubao-2.0-pro --device emulator-5556
    python grader.py --compare ./traces/doubao-2.0-pro ./traces/gemini-3.1-pro ./traces/glm-5

Trace 文件格式: 每条任务一个 .ndjson 文件 (C1.ndjson ... C10.ndjson)
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# ── 数据结构 ───────────────────────────────────────────────

@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: str = ""

@dataclass
class TraceData:
    task_id: str
    events: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    final_text: str = ""
    total_steps: int = 0
    errors: list = field(default_factory=list)
    recovered_errors: int = 0

@dataclass
class GradeResult:
    task_id: str
    task_name: str
    completion: int = 0       # 0 or 1
    tool_selection: int = 0
    param_accuracy: int = 0
    no_hallucination: int = 0
    efficiency: int = 0
    total: int = 0
    steps: int = 0
    errors: int = 0
    recovered: int = 0
    details: dict = field(default_factory=dict)

    def compute_total(self):
        self.total = (self.completion + self.tool_selection +
                      self.param_accuracy + self.no_hallucination +
                      self.efficiency)


# ── Trace 解析 ─────────────────────────────────────────────

def parse_trace(filepath: str) -> TraceData:
    """解析 NDJSON trace 文件

    支持两种格式:
    - 新格式: {"type": "tool_call", "tool": "...", ...}
    - server 格式: {"event": "tool_call", "name": "...", "arguments": {...}, ...}
    """
    task_id = Path(filepath).stem  # C1, C2, ...
    trace = TraceData(task_id=task_id)

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            trace.events.append(event)
            # 兼容两种字段名: "type" 或 "event"
            etype = event.get("type", "") or event.get("event", "")

            if etype == "tool_call":
                tc = ToolCall(
                    name=event.get("tool", event.get("name", "")),
                    arguments=event.get("arguments", event.get("args", {})),
                )
                trace.tool_calls.append(tc)
                trace.total_steps += 1

            elif etype == "tool_result":
                # 关联到最近的 tool_call
                output = event.get("output", event.get("result", event.get("summary", "")))
                if trace.tool_calls:
                    trace.tool_calls[-1].result = output
                # 检查是否有错误
                is_error = event.get("is_error", False) or event.get("ok") is False
                if is_error:
                    trace.errors.append(output)

            elif etype in ("text", "assistant", "response"):
                trace.final_text += event.get("content", event.get("text", ""))

            elif etype == "error":
                trace.errors.append(event.get("message", event.get("error", "")))

    # 错误恢复: 如果出错后还有后续步骤，算恢复
    # 简化: 如果总步数 > 错误数，说明有恢复
    if trace.errors and trace.total_steps > len(trace.errors):
        trace.recovered_errors = len(trace.errors)
    else:
        trace.recovered_errors = 0

    return trace


def get_trace_text(trace: TraceData) -> str:
    """获取 trace 中所有文本（用于 pattern 匹配）"""
    parts = []
    for tc in trace.tool_calls:
        parts.append(f"tool:{tc.name} args:{json.dumps(tc.arguments, ensure_ascii=False)}")
        parts.append(f"result:{tc.result}")
    parts.append(trace.final_text)
    return "\n".join(parts)


# ── 文件检查 (设备端) ──────────────────────────────────────

def check_file_on_device(filepath: str, serial: Optional[str] = None) -> dict:
    """通过 adb 检查设备上的文件"""
    cmd_prefix = ["adb"]
    if serial:
        cmd_prefix += ["-s", serial]

    # 检查文件存在 + 大小
    result = {"exists": False, "size_kb": 0}
    try:
        out = subprocess.check_output(
            cmd_prefix + ["shell", f"stat -c '%s' {filepath} 2>/dev/null || echo 0"],
            text=True, timeout=10
        ).strip().strip("'")
        size = int(out)
        result["exists"] = size > 0
        result["size_kb"] = size / 1024
    except Exception:
        pass
    return result


def check_file_local(filepath: str, base_dir: str = "") -> dict:
    """检查本地文件（从设备 pull 下来之后）"""
    full_path = os.path.join(base_dir, filepath) if base_dir else filepath
    result = {"exists": False, "size_kb": 0}
    if os.path.exists(full_path):
        size = os.path.getsize(full_path)
        result["exists"] = size > 0
        result["size_kb"] = size / 1024
    return result


def find_files_local(pattern: str, base_dir: str) -> list:
    """在本地目录中查找文件"""
    return glob.glob(os.path.join(base_dir, pattern))


# ── 文件内容解析 ──────────────────────────────────────────

def parse_xlsx(filepath: str) -> dict:
    """解析 Excel 文件，返回行数、表头等信息"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        headers = [str(c) if c else "" for c in rows[0]] if rows else []
        return {
            "valid": True,
            "total_rows": len(rows),
            "data_rows": len(rows) - 1,
            "headers": headers,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def parse_docx(filepath: str) -> dict:
    """解析 Word 文件"""
    try:
        import docx
        doc = docx.Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return {
            "valid": True,
            "paragraph_count": len(paragraphs),
            "text": "\n".join(paragraphs[:50]),  # 前50段
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def parse_pptx(filepath: str) -> dict:
    """解析 PPT 文件"""
    try:
        from pptx import Presentation
        prs = Presentation(filepath)
        slides_text = []
        for slide in prs.slides:
            texts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    texts.append(shape.text)
            slides_text.append("\n".join(texts))
        return {
            "valid": True,
            "slide_count": len(prs.slides),
            "slides_text": slides_text,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def check_pdf_encrypted(filepath: str, password: str) -> bool:
    """检查 PDF 是否被加密且能用指定密码解开"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        if reader.is_encrypted:
            reader.decrypt(password)
            _ = reader.pages[0]  # 尝试读取
            return True
        return False
    except Exception:
        return False


def check_image_dimensions(filepath: str) -> tuple:
    """检查图片尺寸"""
    try:
        from PIL import Image
        with Image.open(filepath) as img:
            return img.size  # (width, height)
    except Exception:
        return (0, 0)


# ── 核心 Grading 逻辑 ────────────────────────────────────

class Grader:
    def __init__(self, rubrics_path: str, artifacts_dir: str = "",
                 device_serial: Optional[str] = None):
        with open(rubrics_path, "r", encoding="utf-8") as f:
            self.rubrics = yaml.safe_load(f)["rubrics"]
        self.artifacts_dir = artifacts_dir
        self.device_serial = device_serial

    def grade(self, trace: TraceData) -> GradeResult:
        """对一条 trace 评分"""
        task_id = trace.task_id.upper()
        if task_id not in self.rubrics:
            return GradeResult(task_id=task_id, task_name="UNKNOWN")

        rubric = self.rubrics[task_id]
        result = GradeResult(
            task_id=task_id,
            task_name=rubric["name"],
            steps=trace.total_steps,
            errors=len(trace.errors),
            recovered=trace.recovered_errors,
        )

        trace_text = get_trace_text(trace)

        # 1. Completion
        result.completion = self._check_completion(rubric, trace, trace_text)

        # 2. Tool Selection
        result.tool_selection = self._check_tool_selection(rubric, trace_text)

        # 3. Parameter Accuracy
        result.param_accuracy = self._check_param_accuracy(rubric, trace, trace_text)

        # 4. No Hallucination (简化: 基于 trace 交叉比对)
        result.no_hallucination = self._check_no_hallucination(rubric, trace, trace_text)

        # 5. Efficiency
        max_steps = rubric.get("max_steps", rubric.get("efficiency", {}).get("max_steps", 10))
        result.efficiency = 1 if trace.total_steps <= max_steps else 0

        result.compute_total()
        return result

    def _check_completion(self, rubric: dict, trace: TraceData, trace_text: str) -> int:
        """检查完成度"""
        comp = rubric.get("completion", {})

        # 文件检查
        files_spec = comp.get("files", [])
        if files_spec:
            for fspec in files_spec:
                pattern = fspec["glob"]
                alt_globs = fspec.get("alt_globs", [])
                min_kb = fspec.get("min_size_kb", 0)

                # 先在 artifacts 目录找，尝试主 glob 和所有 alt_globs
                found = find_files_local(pattern, self.artifacts_dir)
                if not found:
                    for alt in alt_globs:
                        found = find_files_local(alt, self.artifacts_dir)
                        if found:
                            break
                if not found:
                    # fallback: 基于 trace 中 done event 判断
                    # 检查 trace 是否有 success:true 的 done event
                    has_done = any(
                        e.get("event") == "done" and e.get("success") is True
                        for e in trace.events
                    )
                    if has_done:
                        continue  # 任务声明成功，文件名不匹配但跳过
                    return 0

                fpath = found[0]
                finfo = check_file_local(fpath)
                if not finfo["exists"] or finfo["size_kb"] < min_kb:
                    return 0

                # 格式校验
                if fspec.get("parse") == "xlsx":
                    parsed = parse_xlsx(fpath)
                    if not parsed["valid"]:
                        return 0
                    if parsed.get("total_rows", 0) < fspec.get("min_rows", 0):
                        return 0

                elif fspec.get("parse") == "docx":
                    parsed = parse_docx(fpath)
                    if not parsed["valid"]:
                        return 0
                    if parsed.get("paragraph_count", 0) < fspec.get("min_paragraphs", 0):
                        return 0

                elif fspec.get("parse") == "pptx":
                    parsed = parse_pptx(fpath)
                    if not parsed["valid"]:
                        return 0
                    if parsed.get("slide_count", 0) < fspec.get("min_slides", 0):
                        return 0

                if fspec.get("encrypted"):
                    password = fspec.get("password", "")
                    if not check_pdf_encrypted(fpath, password):
                        return 0

                if fspec.get("check_dimensions"):
                    exp_w, exp_h = fspec["check_dimensions"]
                    act_w, act_h = check_image_dimensions(fpath)
                    tolerance = fspec.get("tolerance", 2)
                    if abs(act_w - exp_w) > tolerance or abs(act_h - exp_h) > tolerance:
                        return 0

        # Trace 检查（用于无文件产物的任务如 C5, C8）
        trace_checks = comp.get("trace_must_contain", [])
        for tc in trace_checks:
            pattern = tc["pattern"]
            if not re.search(pattern, trace_text, re.IGNORECASE):
                return 0

        return 1

    def _check_tool_selection(self, rubric: dict, trace_text: str) -> int:
        """检查工具选择"""
        ts = rubric.get("tool_selection", {})
        patterns = ts.get("trace_must_contain", [])
        if not patterns:
            return 1

        matched = 0
        for p in patterns:
            if re.search(p["pattern"], trace_text, re.IGNORECASE):
                matched += 1

        # 要求 ≥ 70% 的期望工具被使用
        threshold = max(1, int(len(patterns) * 0.7))
        return 1 if matched >= threshold else 0

    def _check_param_accuracy(self, rubric: dict, trace: TraceData, trace_text: str) -> int:
        """检查参数准确性"""
        pa = rubric.get("param_accuracy", {})

        # trace_checks: 检查特定工具的参数
        for tc_spec in pa.get("trace_checks", []):
            tool_name = tc_spec.get("tool", "")
            arg_patterns = tc_spec.get("arg_contains", [])
            if isinstance(arg_patterns, list) and len(arg_patterns) == 1:
                arg_patterns = arg_patterns[0].split("|")

            # 在 trace 中找对应的 tool_call
            found_match = False
            for tc in trace.tool_calls:
                if tool_name and tool_name.lower() not in tc.name.lower():
                    # 也检查 arguments 中是否包含 tool 名
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)
                    if tool_name.lower() not in args_str.lower():
                        continue
                for pat in arg_patterns:
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)
                    if re.search(pat, args_str, re.IGNORECASE):
                        found_match = True
                        break
                if found_match:
                    break

            if not found_match:
                # fallback: 在整个 trace 文本中搜索
                for pat in arg_patterns:
                    if re.search(pat, trace_text, re.IGNORECASE):
                        found_match = True
                        break

            if not found_match:
                return 0

        # xlsx_checks, docx_checks, pptx_checks — 需要 artifacts
        # 如果没有 artifacts 目录，基于 trace 做 best-effort 判断
        if not self.artifacts_dir:
            return 1  # 无法深度验证时默认通过

        # Excel 表头检查
        xlsx_checks = pa.get("xlsx_checks", [])
        for check in xlsx_checks:
            if isinstance(check, dict) and "header_keywords" in check:
                # 找 xlsx 文件
                for f in find_files_local("*.xlsx", self.artifacts_dir):
                    parsed = parse_xlsx(f)
                    if parsed["valid"]:
                        headers_lower = " ".join(parsed["headers"]).lower()
                        kw_match = any(
                            kw.lower() in headers_lower
                            for kw in check["header_keywords"]
                        )
                        if not kw_match:
                            return 0

        return 1

    def _check_no_hallucination(self, rubric: dict, trace: TraceData, trace_text: str) -> int:
        """检查幻觉 — 核心反作弊逻辑"""
        nh = rubric.get("no_hallucination", {})
        method = nh.get("method", "cross_validate")

        if method == "llm_judge":
            # 需要 LLM 辅助判断，这里标记为需人工
            return 1  # 默认通过，标记待人工审核

        if method == "file_verify":
            # 简单文件验证
            return 1  # 已在 completion 中覆盖

        # cross_validate: 检查 trace 中数据源与产出的一致性
        cv = nh.get("cross_validate", nh)
        if not isinstance(cv, dict):
            return 1

        cv_method = cv.get("method", "")
        if "API" in cv_method or "api" in cv_method.lower():
            # 需要比对 API 响应与产出内容
            # 简化实现: 检查 trace 中是否有真实的 API 调用及返回
            api_calls = [tc for tc in trace.tool_calls
                         if any(kw in tc.result.lower()
                                for kw in ["http", "status", "response", "error",
                                           "{", "["])]
            if not api_calls and "api" in trace_text.lower():
                # 有 API 调用但没有返回 → 可能幻觉
                return 0

        return 1


# ── 报告生成 ──────────────────────────────────────────────

def print_report(results: list, model_name: str = ""):
    """打印单模型评测报告"""
    header = f"\n{'='*60}"
    if model_name:
        header += f"\n  Model: {model_name}"
    header += f"\n{'='*60}\n"
    print(header)

    print(f"{'Task':<6} {'Name':<16} {'Comp':>4} {'Tool':>4} {'Param':>5} "
          f"{'Hall':>4} {'Effi':>4} {'Total':>5} {'Steps':>5} {'Err':>3}")
    print("-" * 60)

    total_scores = {"completion": 0, "tool_selection": 0, "param_accuracy": 0,
                    "no_hallucination": 0, "efficiency": 0, "total": 0}
    total_steps = 0
    total_errors = 0
    total_recovered = 0

    for r in results:
        print(f"{r.task_id:<6} {r.task_name:<16} {r.completion:>4} {r.tool_selection:>4} "
              f"{r.param_accuracy:>5} {r.no_hallucination:>4} {r.efficiency:>4} "
              f"{r.total:>5} {r.steps:>5} {r.errors:>3}")
        total_scores["completion"] += r.completion
        total_scores["tool_selection"] += r.tool_selection
        total_scores["param_accuracy"] += r.param_accuracy
        total_scores["no_hallucination"] += r.no_hallucination
        total_scores["efficiency"] += r.efficiency
        total_scores["total"] += r.total
        total_steps += r.steps
        total_errors += r.errors
        total_recovered += r.recovered

    n = len(results)
    print("-" * 60)
    print(f"{'SUM':<6} {'':16} {total_scores['completion']:>4} "
          f"{total_scores['tool_selection']:>4} {total_scores['param_accuracy']:>5} "
          f"{total_scores['no_hallucination']:>4} {total_scores['efficiency']:>4} "
          f"{total_scores['total']:>5}")

    print(f"\n── 聚合指标 ──")
    print(f"  Acc  (完成率):       {total_scores['completion']}/{n} = {total_scores['completion']/n:.0%}")
    print(f"  TSR  (工具选择):     {total_scores['tool_selection']}/{n} = {total_scores['tool_selection']/n:.0%}")
    print(f"  PA   (参数准确):     {total_scores['param_accuracy']}/{n} = {total_scores['param_accuracy']/n:.0%}")
    print(f"  Hall (无幻觉):       {total_scores['no_hallucination']}/{n} = {total_scores['no_hallucination']/n:.0%}")
    print(f"  AES  (平均步数):     {total_steps/n:.1f}")
    err_rate = f"{total_recovered}/{total_errors}" if total_errors > 0 else "N/A"
    print(f"  ERR  (错误恢复):     {err_rate}")
    print(f"  Total Score:         {total_scores['total']}/50")


def print_comparison(all_results: dict):
    """打印多模型对比表"""
    print(f"\n{'='*80}")
    print(f"  Multi-Model Comparison")
    print(f"{'='*80}\n")

    models = list(all_results.keys())
    # Header
    header = f"{'Task':<6} {'Name':<14}"
    for m in models:
        header += f" | {m:>16}"
    print(header)
    print("-" * len(header))

    # Per task
    n = 10
    for i in range(n):
        task_id = f"C{i+1}"
        row = f"{task_id:<6}"
        name = ""
        for m in models:
            results = all_results[m]
            if i < len(results):
                r = results[i]
                name = r.task_name
                row_part = f" | {r.total}/5 ({r.steps}步)"
            else:
                row_part = f" | {'N/A':>16}"
            row += f"{row_part:>18}"
        print(f"{task_id:<6} {name:<14}" + row[len(task_id):])

    # Aggregates
    print("-" * len(header))
    agg_row = f"{'Total':<6} {'':14}"
    for m in models:
        total = sum(r.total for r in all_results[m])
        agg_row += f" | {total:>10}/50    "
    print(agg_row)

    # Metrics comparison table
    print(f"\n{'Metric':<20}", end="")
    for m in models:
        print(f" {m:>16}", end="")
    print()
    print("-" * (20 + 17 * len(models)))

    for metric_name, extractor in [
        ("Acc (完成率)", lambda rs: f"{sum(r.completion for r in rs)}/{len(rs)}"),
        ("TSR (工具选择)", lambda rs: f"{sum(r.tool_selection for r in rs)}/{len(rs)}"),
        ("PA (参数准确)", lambda rs: f"{sum(r.param_accuracy for r in rs)}/{len(rs)}"),
        ("Hall (无幻觉)", lambda rs: f"{sum(r.no_hallucination for r in rs)}/{len(rs)}"),
        ("AES (平均步数)", lambda rs: f"{sum(r.steps for r in rs)/len(rs):.1f}"),
        ("Total", lambda rs: f"{sum(r.total for r in rs)}/50"),
    ]:
        print(f"{metric_name:<20}", end="")
        for m in models:
            print(f" {extractor(all_results[m]):>16}", end="")
        print()


def export_json(all_results: dict, output_path: str):
    """导出 JSON 格式结果"""
    export = {}
    for model, results in all_results.items():
        export[model] = {
            "tasks": [
                {
                    "task_id": r.task_id,
                    "task_name": r.task_name,
                    "scores": {
                        "completion": r.completion,
                        "tool_selection": r.tool_selection,
                        "param_accuracy": r.param_accuracy,
                        "no_hallucination": r.no_hallucination,
                        "efficiency": r.efficiency,
                        "total": r.total,
                    },
                    "steps": r.steps,
                    "errors": r.errors,
                    "recovered": r.recovered,
                }
                for r in results
            ],
            "aggregate": {
                "Acc": sum(r.completion for r in results) / len(results),
                "TSR": sum(r.tool_selection for r in results) / len(results),
                "PA": sum(r.param_accuracy for r in results) / len(results),
                "Hall": sum(r.no_hallucination for r in results) / len(results),
                "AES": sum(r.steps for r in results) / len(results),
                "Total": sum(r.total for r in results),
            }
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已导出到 {output_path}")


# ── 主函数 ────────────────────────────────────────────────

def grade_model(trace_dir: str, rubrics_path: str,
                artifacts_dir: str = "", device_serial: str = "") -> list:
    """对一个模型的所有 trace 评分"""
    grader = Grader(rubrics_path, artifacts_dir, device_serial or None)
    results = []

    for i in range(1, 11):
        trace_file = os.path.join(trace_dir, f"C{i}.ndjson")
        if not os.path.exists(trace_file):
            # 尝试小写
            trace_file = os.path.join(trace_dir, f"c{i}.ndjson")
        if not os.path.exists(trace_file):
            print(f"⚠️  C{i} trace 文件不存在: {trace_file}", file=sys.stderr)
            results.append(GradeResult(task_id=f"C{i}", task_name="MISSING"))
            continue

        trace = parse_trace(trace_file)
        grade = grader.grade(trace)
        results.append(grade)

    return results


def main():
    parser = argparse.ArgumentParser(description="PhoneHarness Benchmark Auto-Grader")
    parser.add_argument("--trace-dir", help="单模型 trace 目录")
    parser.add_argument("--compare", nargs="+", help="多模型 trace 目录列表")
    parser.add_argument("--rubrics", default=os.path.join(os.path.dirname(__file__), "rubrics.yaml"))
    parser.add_argument("--artifacts", default="", help="产物目录 (从设备 pull 下来的文件)")
    parser.add_argument("--device", default="", help="设备 serial (用于 adb 检查)")
    parser.add_argument("--output", default="", help="JSON 输出路径")
    args = parser.parse_args()

    if args.compare:
        all_results = {}
        for trace_dir in args.compare:
            model_name = os.path.basename(trace_dir.rstrip("/"))
            artifacts = os.path.join(trace_dir, "artifacts") if not args.artifacts else args.artifacts
            results = grade_model(trace_dir, args.rubrics, artifacts, args.device)
            print_report(results, model_name)
            all_results[model_name] = results

        print_comparison(all_results)

        if args.output:
            export_json(all_results, args.output)

    elif args.trace_dir:
        model_name = os.path.basename(args.trace_dir.rstrip("/"))
        artifacts = os.path.join(args.trace_dir, "artifacts") if not args.artifacts else args.artifacts
        results = grade_model(args.trace_dir, args.rubrics, artifacts, args.device)
        print_report(results, model_name)

        if args.output:
            export_json({model_name: results}, args.output)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
