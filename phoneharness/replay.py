from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_replay_payload(source: str | Path) -> dict[str, Any]:
    path = Path(source).expanduser().resolve()
    if path.is_dir():
        journal_path = path / "journal.json"
        summary_path = path / "summary.json"
        if journal_path.is_file():
            return json.loads(journal_path.read_text(encoding="utf-8"))
        if summary_path.is_file():
            return _summary_to_replay(json.loads(summary_path.read_text(encoding="utf-8")))
        raise FileNotFoundError(f"missing journal.json or summary.json under {path}")

    if not path.is_file():
        raise FileNotFoundError(f"replay source not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if path.name == "summary.json":
        return _summary_to_replay(payload)
    return payload


def render_replay_text(source: str | Path) -> str:
    payload = load_replay_payload(source)
    lines = [
        f"run_id: {payload.get('run_id', '<unknown>')}",
        f"mode: {payload.get('mode', 'summary_only')}",
        f"success: {payload.get('success')}",
        f"status: {payload.get('status')}",
        f"control_provider: {payload.get('control_provider')}",
        f"control_model: {payload.get('control_model')}",
        f"controller_location: {payload.get('controller_location')}",
        f"active_skills: {payload.get('active_skills') or []}",
        f"called_tools: {payload.get('called_tools') or []}",
        f"final_text: {payload.get('final_text', '')}",
        f"error_type: {payload.get('error_type')}",
        f"blocker: {payload.get('blocker')}",
        "steps:",
    ]
    for step in payload.get("steps", []):
        lines.append(
            f"  step {step.get('index')}: type={step.get('step_type')} assistant={_single_line(step.get('assistant_text', ''))}"
        )
        for tool_call in step.get("tool_calls", []):
            lines.append(
                "    "
                + f"tool={tool_call.get('name')} status={tool_call.get('status')} result_ok={tool_call.get('result_ok')} summary={tool_call.get('result_summary', '')}"
            )
    return "\n".join(lines)


def _summary_to_replay(summary_payload: dict[str, Any]) -> dict[str, Any]:
    run = summary_payload.get("run") or {}
    return {
        "mode": summary_payload.get("mode", "summary_only"),
        "success": summary_payload.get("success"),
        "run_id": run.get("id"),
        "status": run.get("status"),
        "control_provider": run.get("control_provider"),
        "control_model": run.get("control_model"),
        "controller_location": summary_payload.get("controller_location"),
        "active_skills": summary_payload.get("active_skills") or run.get("active_skills") or [],
        "called_tools": _called_tools(run.get("steps") or []),
        "steps": run.get("steps") or [],
        "final_text": summary_payload.get("final_text", ""),
        "error_type": summary_payload.get("error_type"),
        "blocker": summary_payload.get("blocker"),
    }


def _called_tools(steps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in steps:
        for tool_call in step.get("tool_calls") or []:
            name = tool_call.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names


def _single_line(text: str) -> str:
    return " ".join((text or "").split())
