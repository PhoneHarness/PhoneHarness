from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..agent.message import PathRef
from ..skills.loader import SkillDefinition


def build_journal_payload(
    *,
    summary_payload: dict[str, Any],
    skills: tuple[SkillDefinition, ...],
    injected_system_prompt: str,
    controller_location: dict[str, str],
) -> dict[str, Any]:
    run = summary_payload.get("run") or {}
    steps = run.get("steps") or []

    called_tools: list[str] = []
    step_entries: list[dict[str, Any]] = []
    for step in steps:
        tool_calls = step.get("tool_calls") or []
        for tool_call in tool_calls:
            name = tool_call.get("name")
            if isinstance(name, str) and name and name not in called_tools:
                called_tools.append(name)
        step_entries.append(
            {
                "index": step.get("index"),
                "step_type": step.get("step_type"),
                "assistant_text": step.get("assistant_text", ""),
                "request_path": step.get("request_path"),
                "response_path": step.get("response_path"),
                "tool_calls": tool_calls,
            }
        )

    return {
        "mode": summary_payload.get("mode", "m3_ondevice"),
        "success": bool(summary_payload.get("success")),
        "run_id": run.get("id"),
        "user_input": run.get("user_input"),
        "status": run.get("status"),
        "control_provider": run.get("control_provider"),
        "control_model": run.get("control_model"),
        "controller_location": controller_location,
        "active_skills": list(run.get("active_skills") or summary_payload.get("active_skills") or []),
        "skills": [skill.to_dict() for skill in skills],
        "called_tools": called_tools,
        "steps": step_entries,
        "artifacts": list(run.get("artifacts") or []),
        "final_text": summary_payload.get("final_text", ""),
        "error_type": summary_payload.get("error_type"),
        "blocker": summary_payload.get("blocker"),
        "trace_path": summary_payload.get("trace_path"),
        "summary_path": summary_payload.get("summary_path"),
        "tool_call_paths": summary_payload.get("tool_call_paths") or [],
        "tool_result_paths": summary_payload.get("tool_result_paths") or [],
        "request_paths": summary_payload.get("request_paths") or [],
        "response_paths": summary_payload.get("response_paths") or [],
        "injected_system_prompt": injected_system_prompt,
        "run_conclusion": summary_payload.get("run_conclusion", ""),
    }


def write_journal(
    *,
    summary_path: PathRef,
    skills: tuple[SkillDefinition, ...],
    injected_system_prompt: str,
    controller_location: dict[str, str],
) -> PathRef:
    summary_file = Path(summary_path.path)
    summary_payload = json.loads(summary_file.read_text(encoding="utf-8"))
    journal_payload = build_journal_payload(
        summary_payload=summary_payload,
        skills=skills,
        injected_system_prompt=injected_system_prompt,
        controller_location=controller_location,
    )
    journal_file = summary_file.with_name("journal.json")
    journal_file.write_text(json.dumps(journal_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return PathRef(layer=summary_path.layer, path=str(journal_file))


def update_summary_for_m3(
    *,
    summary_path: PathRef,
    journal_path: PathRef,
    active_skills: tuple[str, ...],
    skill_sources: tuple[str, ...],
    controller_location: dict[str, str],
    run_conclusion: str,
) -> None:
    summary_file = Path(summary_path.path)
    payload = json.loads(summary_file.read_text(encoding="utf-8"))
    payload["mode"] = "m3_ondevice"
    payload["active_skills"] = list(active_skills)
    payload["skill_sources"] = list(skill_sources)
    payload["controller_location"] = controller_location
    payload["journal_path"] = journal_path.to_dict()
    payload["run_conclusion"] = run_conclusion
    payload["summary_path"] = summary_path.to_dict()
    summary_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
