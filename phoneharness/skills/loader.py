from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


class SkillLoaderError(ValueError):
    pass


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    prompt_extension: str
    source_path: str
    allowed_tools: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    failure_patterns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt_extension": self.prompt_extension,
            "source_path": self.source_path,
            "allowed_tools": list(self.allowed_tools),
            "preconditions": list(self.preconditions),
            "verification": list(self.verification),
            "failure_patterns": list(self.failure_patterns),
        }


def load_skill_file(path: str | Path) -> SkillDefinition:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise SkillLoaderError(f"skill file not found: {resolved}")
    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SkillLoaderError(f"invalid YAML in {resolved}: {exc}") from exc

    if not isinstance(payload, dict):
        raise SkillLoaderError(f"skill file must contain a YAML mapping: {resolved}")

    name = _require_string(payload, "name", resolved)
    description = _require_string(payload, "description", resolved)
    prompt_extension = _require_string(payload, "prompt_extension", resolved)

    return SkillDefinition(
        name=name,
        description=description,
        prompt_extension=prompt_extension,
        source_path=str(resolved),
        allowed_tools=_optional_string_list(payload, "allowed_tools", resolved),
        preconditions=_optional_string_list(payload, "preconditions", resolved),
        verification=_optional_string_list(payload, "verification", resolved),
        failure_patterns=_optional_string_list(payload, "failure_patterns", resolved),
    )


def load_skill_files(paths: Iterable[str | Path]) -> tuple[SkillDefinition, ...]:
    skills = [load_skill_file(path) for path in paths]
    names = [skill.name for skill in skills]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SkillLoaderError(f"duplicate skill names are not allowed: {', '.join(duplicates)}")
    return tuple(skills)


def _require_string(payload: dict[str, Any], key: str, source: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillLoaderError(f"{source}: field `{key}` must be a non-empty string")
    return value.strip()


def _optional_string_list(payload: dict[str, Any], key: str, source: Path) -> tuple[str, ...]:
    value = payload.get(key, [])
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SkillLoaderError(f"{source}: field `{key}` must be a list of non-empty strings")
    return tuple(item.strip() for item in value)
