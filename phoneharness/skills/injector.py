from __future__ import annotations

from .loader import SkillDefinition


def inject_skill_prompt(base_prompt: str, skills: tuple[SkillDefinition, ...]) -> str:
    if not skills:
        return base_prompt

    blocks = [base_prompt.rstrip(), "", "## Active Skills"]
    for skill in skills:
        blocks.extend(
            [
                f"### Skill: {skill.name}",
                f"Description: {skill.description}",
            ]
        )
        if skill.allowed_tools:
            blocks.append("Allowed tools: " + ", ".join(skill.allowed_tools))
        if skill.preconditions:
            blocks.append("Preconditions: " + ", ".join(skill.preconditions))
        if skill.verification:
            blocks.append("Verification focus: " + ", ".join(skill.verification))
        if skill.failure_patterns:
            blocks.append("Failure patterns: " + ", ".join(skill.failure_patterns))
        blocks.append(skill.prompt_extension.rstrip())
        blocks.append("")
    return "\n".join(blocks).rstrip()
