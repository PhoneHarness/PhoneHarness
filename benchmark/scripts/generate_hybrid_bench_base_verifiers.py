#!/usr/bin/env python3
"""Generate one unified minimal base-verifier file for Hybrid-Bench.

The goal is not to fully grade task quality. It only assigns one simple
"result completion" verifier per task so the benchmark can get a stable 0/1
completion signal without relying on LLM-as-judge.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
WORKBOOK = ROOT / "benchmark" / "Hybrid-Bench.xlsx"
OUTPUT_BY_SHEET_DIR = ROOT / "benchmark" / "tasks" / "hybrid_bench_base_verifiers_by_sheet"
SHEETS = ["4app_new", "30app_new", "68app_new"]


EMAIL_TEXT_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
EMAIL_RE = re.compile(EMAIL_TEXT_RE)
DATE_RE = r"\d{4}[年/-]\d{1,2}[月/-]\d{1,2}(?:日)?"


def load_rows(sheet_name: str) -> list[dict[str, Any]]:
    wb = load_workbook(WORKBOOK, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    header_idx = None
    for i, row in enumerate(rows):
        if row and "ID" in row:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Header row not found in {sheet_name}")
    headers = list(rows[header_idx])
    data: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if not any(v is not None and v != "" for v in row):
            continue
        rec = dict(zip(headers, row))
        rec["_source_row"] = source_row
        data.append(rec)
    return data


def extract_tools(chain_text: str | None) -> list[str]:
    text = str(chain_text or "")
    tools: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^\d+\.\s*([^:：]+)", line)
        if not m:
            continue
        tools.append(m.group(1).strip())
    return tools


def find_email(text: str) -> str | None:
    m = EMAIL_RE.search(text or "")
    return m.group(0) if m else None


def find_recipient_name(text: str) -> str | None:
    # Examples:
    # "发邮件给刘强email address..."
    # "发邮件给周敏"
    # "备注给陈丽"
    name_stop = rf"(?=email address|{EMAIL_TEXT_RE}|[，,。；;：:\s]|$)"
    patterns = [
        rf"发邮件给([\u4e00-\u9fffA-Za-z]{{1,8}}?){name_stop}",
        rf"给([\u4e00-\u9fffA-Za-z]{{1,8}}?)(?=email address)",
        rf"备注给([\u4e00-\u9fffA-Za-z]{{1,8}}?){name_stop}",
        rf"告诉我([\u4e00-\u9fffA-Za-z]{{1,8}}?){name_stop}",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "")
        if m:
            return m.group(1)
    return None


def build_task_uid(sheet: str, task_id: str, source_row: int, id_count: int) -> str:
    """Build a unique task UID.

    Most workbook rows can use <sheet>:<task_id>. If the workbook reuses the
    same task_id inside a sheet, append the source row as a stable suffix.
    """
    base = f"{sheet}:{task_id}"
    if id_count <= 1:
        return base
    return f"{base}:r{source_row}"


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False, width=1000)


def infer_attachment_hint(tools: list[str], prompt: str, chain: str) -> dict[str, Any]:
    joined = " ".join(tools) + " " + prompt + " " + chain
    hint: dict[str, Any] = {}
    if any(tool in tools for tool in ["docx_create"]):
        hint["requires_attachment"] = True
        hint["attachment_ext_any"] = [".docx"]
    elif any(tool in tools for tool in ["xlsx_create"]):
        hint["requires_attachment"] = True
        hint["attachment_ext_any"] = [".xlsx"]
    elif any(tool in tools for tool in ["pptx_create"]):
        hint["requires_attachment"] = True
        hint["attachment_ext_any"] = [".pptx"]
    elif any(tool in tools for tool in ["qrcode_generate"]):
        hint["requires_attachment"] = True
        hint["attachment_ext_any"] = [".png", ".jpg", ".jpeg"]
    elif "screencap" in joined or "截图" in joined:
        hint["requires_attachment"] = True
        hint["attachment_ext_any"] = [".png"]
    elif "压缩包" in joined or "zip" in joined.lower():
        hint["requires_attachment"] = True
        hint["attachment_ext_any"] = [".zip", ".rar", ".7z"]
    return hint


def maybe_exact_math_result(prompt: str, chain: str) -> list[str] | None:
    text = prompt + "\n" + chain
    expr_match = re.search(r"计算式[「\"]([^」\"]+)[」\"]", text)
    if not expr_match:
        if "327" in text and "48" in text and "159" in text:
            return ["15537"]
        return None
    expr = expr_match.group(1)
    if not re.fullmatch(r"[\d+\-*/ ().]+", expr):
        return None
    try:
        value = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - strictly sanitized above
    except Exception:
        return None
    return [str(value)]


def answer_verifier(prompt: str, tools: list[str], chain: str) -> dict[str, Any]:
    params: dict[str, Any] = {"min_length": 20}
    text = prompt + "\n" + chain

    exact = maybe_exact_math_result(prompt, chain)
    if exact:
        params = {"required_exact": exact}
        return {"type": "answer_contains", "params": params}

    required_any: list[str] = []
    required_all: list[str] = []
    required_patterns: list[str] = []

    if "AI" in text or "人工智能" in text:
        required_any.extend(["AI", "人工智能"])
    if "新闻" in text or "热搜" in text:
        required_any.extend(["新闻", "热点"])
    if "电量" in text:
        required_patterns.append(r"\b\d{1,3}%\b")
    if "WiFi" in text or "WIFI" in text or "wifi" in text:
        required_patterns.append(r"(WiFi|WIFI|wifi|ssid|SSID)")
    if "做法" in text or "攻略" in text:
        required_any.extend(["做法", "攻略", "教程"])
    if "价格" in text or "差价" in text or "比价" in text:
        required_any.extend(["价格", "差价", "贵", "便宜"])
        required_patterns.append(r"\d+(?:\.\d+)?")
        params["min_number_count"] = 2
    if "上映日期" in text or "报名时间" in text or "时间" in text:
        required_patterns.append(DATE_RE)
    if "播放量" in text:
        required_all.append("播放量")
    if "点赞" in text:
        required_all.append("点赞")
    if "评分" in text:
        required_any.append("评分")
    if "天气" in text:
        required_any.append("天气")
    if "民宿" in text:
        required_any.append("民宿")
    if "路线" in text or "导航" in text:
        required_any.extend(["路线", "导航"])

    if required_any:
        params["required_any_keywords"] = sorted(set(required_any))
    if required_all:
        params["required_all_keywords"] = sorted(set(required_all))
    if required_patterns:
        params["required_patterns"] = required_patterns
    return {"type": "answer_contains", "params": params}


def infer_system_setting(prompt: str, chain: str) -> dict[str, Any] | None:
    text = prompt + "\n" + chain
    if "深色模式" in text:
        return {
            "type": "system_setting",
            "params": {"target": "dark_mode_enabled"},
        }
    if "字体" in text and ("最大" in text or "调大" in text):
        return {
            "type": "system_setting",
            "params": {"target": "font_scale_max"},
        }
    if "亮度" in text and ("最大" in text or "调高" in text or "调亮" in text):
        return {
            "type": "system_setting",
            "params": {"target": "brightness_high"},
        }
    return None


def infer_artifact_verifier(prompt: str, tools: list[str], chain: str) -> dict[str, Any] | None:
    text = prompt + "\n" + chain
    if "docx_create" in tools:
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "docx", "ext_any": [".docx"], "min_size_bytes": 1024},
        }
    if "xlsx_create" in tools:
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "xlsx", "ext_any": [".xlsx"], "min_size_bytes": 1024},
        }
    if "pptx_create" in tools:
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "pptx", "ext_any": [".pptx"], "min_size_bytes": 1024},
        }
    if "qrcode_generate" in tools:
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "image", "ext_any": [".png", ".jpg", ".jpeg"], "min_size_bytes": 512},
        }
    if "image_compress" in tools and "发邮件" not in text:
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "image_or_archive", "ext_any": [".jpg", ".jpeg", ".png", ".zip"], "min_size_bytes": 512},
        }
    if "screencap" in text and "发邮件" not in text and "发给我" in prompt:
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "screenshot", "ext_any": [".png"], "min_size_bytes": 512},
        }
    if "pdf" in text.lower():
        return {
            "type": "artifact_exists",
            "params": {"artifact_kind": "pdf", "ext_any": [".pdf"], "min_size_bytes": 1024},
        }
    return None


def infer_email_verifier(prompt: str, tools: list[str], chain: str) -> dict[str, Any] | None:
    text = prompt + "\n" + chain
    if "发邮件" not in text and not any("send_email" in tool for tool in tools):
        return None
    params: dict[str, Any] = {}
    email = find_email(text)
    if email:
        params["recipient_email"] = email
    name = find_recipient_name(text)
    if name:
        params["recipient_name"] = name
    params.update(infer_attachment_hint(tools, prompt, chain))
    if "链接" in text:
        params["body_should_contain_url"] = True
    return {"type": "email_sent", "params": params}


def infer_calendar_verifier(prompt: str, tools: list[str], chain: str) -> dict[str, Any] | None:
    text = prompt + "\n" + chain
    if "create_calendar_event" not in tools and "日历" not in text and "提醒" not in text:
        return None
    keywords: list[str] = []
    if "永辉" in text:
        keywords.append("永辉")
    if "配送" in text:
        keywords.append("配送")
    if "健身" in text or "锻炼" in text:
        keywords.extend(["健身", "锻炼"])
    if "旅行" in text or "出发" in text:
        keywords.extend(["旅行", "出发"])
    if "更新" in text:
        keywords.append("更新")
    params: dict[str, Any] = {}
    if keywords:
        params["title_keywords_any"] = sorted(set(keywords))
    if "明天" in text:
        params["datetime_hint"] = "tomorrow"
    if "下月" in text:
        params["datetime_hint"] = "next_month"
    return {"type": "calendar_event_created", "params": params}


def infer_tool_called(prompt: str, tools: list[str], chain: str) -> dict[str, Any] | None:
    text = prompt + "\n" + chain
    preferred: list[str] = []
    if any(tool.startswith("play_") for tool in tools):
        preferred.extend([tool for tool in tools if tool.startswith("play_")])
    for candidate in [
        "create_contact",
        "show_map",
        "open_browser",
        "dial_phone",
        "open_wifi_settings",
        "open_settings",
        "search_tencent_news",
        "search_weibo_hot",
        "websearch-text.prompt_search",
        "get_battery_status",
        "get_wifi_info",
    ]:
        if candidate in tools:
            preferred.append(candidate)
    if "screencap" in text:
        preferred.append("screencap")
    if not preferred:
        return None
    return {"type": "tool_called", "params": {"tool_name_any": list(dict.fromkeys(preferred)), "must_succeed": True}}


def infer_base_verifier(rec: dict[str, Any]) -> dict[str, Any]:
    prompt = str(rec.get("用户指令 (Prompt)") or "")
    chain = str(rec.get("期望工具链") or "")
    tools = extract_tools(chain)

    # Strongest / most objective signals first.
    for fn in (
        infer_artifact_verifier,
        infer_email_verifier,
        infer_calendar_verifier,
        infer_system_setting,
        infer_tool_called,
    ):
        verifier = fn(prompt, tools, chain) if fn in (infer_artifact_verifier, infer_email_verifier, infer_calendar_verifier, infer_tool_called) else fn(prompt, chain)
        if verifier is not None:
            return verifier

    return answer_verifier(prompt, tools, chain)


def strength_for(verifier: dict[str, Any]) -> str:
    t = verifier["type"]
    if t in {"artifact_exists", "system_setting"}:
        return "strong"
    if t in {"email_sent", "calendar_event_created"}:
        return "medium"
    if t in {"tool_called"}:
        return "weak"
    return "weak"


def coverage_gap_for(verifier: dict[str, Any]) -> str:
    t = verifier["type"]
    if t == "artifact_exists":
        return "只验证产物存在，不验证内容质量或信息完整性"
    if t == "email_sent":
        return "只验证邮件侧效果，不验证前置 GUI/检索步骤是否完全正确"
    if t == "calendar_event_created":
        return "只验证已创建提醒，不验证时间与标题是否完全最优"
    if t == "system_setting":
        return "只验证系统目标状态，不验证具体 GUI 操作路径"
    if t == "tool_called":
        return "只验证关键工具被成功调用，不验证最终用户结果质量"
    return "只验证最终回答中的最低关键信号，不验证来源与计算正确性"


def main() -> None:
    datasets: dict[str, dict[str, Any]] = {}
    verifier_counts: Counter[str] = Counter()
    verifier_counts_by_sheet: dict[str, Counter[str]] = {}
    total_tasks = 0
    colliding_task_refs: list[str] = []
    colliding_task_refs_by_sheet: dict[str, list[str]] = {}

    for sheet in SHEETS:
        rows = load_rows(sheet)
        id_counts = Counter(str(rec["ID"]) for rec in rows)
        sheet_colliding_refs = [
            f"{sheet}:{task_id}" for task_id, count in sorted(id_counts.items()) if count > 1
        ]
        colliding_task_refs.extend(sheet_colliding_refs)
        colliding_task_refs_by_sheet[sheet] = sheet_colliding_refs
        sheet_verifier_counts: Counter[str] = Counter()
        task_entries: list[dict[str, Any]] = []
        for rec in rows:
            verifier = infer_base_verifier(rec)
            verifier_counts[verifier["type"]] += 1
            sheet_verifier_counts[verifier["type"]] += 1
            total_tasks += 1
            task_id = str(rec["ID"])
            task_entries.append(
                {
                    "task_uid": build_task_uid(
                        sheet=sheet,
                        task_id=task_id,
                        source_row=rec["_source_row"],
                        id_count=id_counts[task_id],
                    ),
                    "task_id": task_id,
                    "task_name": rec["任务名称"],
                    "scene_category": rec["场景类别"],
                    "task_type": rec["类型"],
                    "difficulty": rec["难度"],
                    "prompt": rec["用户指令 (Prompt)"],
                    "dimension_sequence": rec["维度序列"],
                    "keep_flag": rec.get("是否保留"),
                    "source_row": rec["_source_row"],
                    "base_verifier": verifier,
                    "score": {"pass": 1, "fail": 0},
                    "strength": strength_for(verifier),
                    "coverage_gap": coverage_gap_for(verifier),
                }
            )

        datasets[sheet] = {
            "task_count": len(task_entries),
            "tasks": task_entries,
        }
        verifier_counts_by_sheet[sheet] = sheet_verifier_counts

    seen_task_uids: set[str] = set()
    duplicate_task_uids: list[str] = []
    for dataset in datasets.values():
        for task in dataset["tasks"]:
            task_uid = task["task_uid"]
            if task_uid in seen_task_uids:
                duplicate_task_uids.append(task_uid)
            seen_task_uids.add(task_uid)
    if duplicate_task_uids:
        raise ValueError(f"Duplicate task_uid after generation: {sorted(set(duplicate_task_uids))}")

    id_policy = {
        "task_uid_format": "<sheet>:<task_id>",
        "collision_suffix": ":r<source_row> (only when a sheet reuses the same task_id)",
        "note": "task_id is not globally unique across sheets, and the workbook may also reuse an ID inside one sheet; always key by task_uid",
    }
    scoring = {"mode": "binary", "pass": 1, "fail": 0}
    verifier_types = {
        "artifact_exists": "Check that a required output artifact exists with a plausible extension and size.",
        "email_sent": "Check that an email side effect happened with the intended recipient and optional attachment/url constraints.",
        "calendar_event_created": "Check that a calendar reminder/event was created with minimal expected hints.",
        "system_setting": "Check that a system-level state reached the requested target.",
        "tool_called": "Check that a key tool/action was successfully invoked when no stronger result signal exists.",
        "answer_contains": "Fallback verifier: check that the final answer contains the minimum expected result signal.",
    }

    for sheet in SHEETS:
        sheet_payload = {
            "version": 1,
            "source_workbook": "benchmark/Hybrid-Bench.xlsx",
            "source_sheet": sheet,
            "id_policy": id_policy,
            "scoring": scoring,
            "task_pass_rule": "base_verifier_pass",
            "verifier_types": verifier_types,
            "summary": {
                "sheet": sheet,
                "task_count": datasets[sheet]["task_count"],
                "colliding_task_refs": colliding_task_refs_by_sheet[sheet],
                "verifier_type_counts": dict(sorted(verifier_counts_by_sheet[sheet].items())),
            },
            "tasks": datasets[sheet]["tasks"],
        }
        write_yaml(OUTPUT_BY_SHEET_DIR / f"{sheet}.yaml", sheet_payload)

    print(f"Wrote by-sheet dir: {OUTPUT_BY_SHEET_DIR}")
    print(f"Tasks: {total_tasks}")
    print(f"Sheet task counts: { {sheet: datasets[sheet]['task_count'] for sheet in SHEETS} }")
    print(f"Colliding task refs: {colliding_task_refs}")
    print(f"Verifier counts: {dict(sorted(verifier_counts.items()))}")


if __name__ == "__main__":
    main()
