"""Progressive skill loading tool — agent calls this to get full tool instructions on demand."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ..agent.message import ToolResult
from .base import BaseTool

# Skill files directory (relative to working dir or absolute)
SKILL_DIRS = [
    Path(os.environ.get("HOME", ".")) / "skills",
    Path(__file__).resolve().parent.parent.parent / "skills",
]


def _find_skill_file(name: str) -> Path | None:
    for d in SKILL_DIRS:
        p = d / f"{name}.yaml"
        if p.is_file():
            return p
    return None


def _list_skills() -> list[dict[str, str]]:
    seen = set()
    skills = []
    for d in SKILL_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            if f.stem in ("index", "available_apis", "file_output_paths") or f.stem in seen:
                continue
            seen.add(f.stem)
            try:
                with open(f, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                desc = data.get("description", "")
            except Exception:
                desc = ""
            skills.append({"name": f.stem, "description": desc})
    return skills


class LoadSkillTool(BaseTool):
    name = "load_skill"
    description = "Load detailed instructions for a specific skill category. Call with skill name (e.g. 'email', 'news', 'file') to get full API usage. Call with 'list' to see all available skills."
    input_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Skill name to load (e.g. 'email', 'news', 'file', 'device'), or 'list' to see all available skills.",
            },
        },
        "required": ["skill"],
    }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        skill_name = args.get("skill", "").strip()

        if not skill_name:
            return ToolResult(ok=False, output="Missing 'skill' parameter", summary="Missing skill name")

        if skill_name == "list":
            skills = _list_skills()
            if not skills:
                return ToolResult(ok=False, output="No skill files found", summary="No skills available")
            lines = ["Available skills:"]
            for s in skills:
                lines.append(f"  {s['name']:12s} — {s['description']}")
            output = "\n".join(lines)
            return ToolResult(ok=True, output=output, summary=f"{len(skills)} skills available")

        path = _find_skill_file(skill_name)
        if path is None:
            available = [s["name"] for s in _list_skills()]
            return ToolResult(
                ok=False,
                output=f"Skill '{skill_name}' not found. Available: {', '.join(available)}",
                summary=f"Skill '{skill_name}' not found",
            )

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            prompt = data.get("prompt_extension", "")
            if not prompt:
                return ToolResult(ok=False, output=f"Skill '{skill_name}' has no prompt content", summary="Empty skill")
            return ToolResult(ok=True, output=prompt, summary=f"Loaded skill '{skill_name}'")
        except Exception as e:
            return ToolResult(ok=False, output=str(e), summary=f"Error loading skill: {e}")
