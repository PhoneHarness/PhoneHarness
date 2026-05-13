from __future__ import annotations

import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from ..agent.message import PathRef

TERMUX_HOME = "/data/data/com.termux/files/home"
DEFAULT_UBUNTU_ROOT = "/root/phoneharness"
# On-device fallback: when running in Termux native (no proot),
# use Termux home as the work root instead of /root/phoneharness
DEFAULT_TERMUX_WORK_ROOT = TERMUX_HOME + "/phoneharness"
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])")


def parse_path_ref(payload: dict[str, Any], *, field_name: str) -> PathRef:
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must be an object with layer/path")
    layer = payload.get("layer")
    path = payload.get("path")
    if layer not in {"host", "termux", "ubuntu"}:
        raise ValueError(f"{field_name}.layer must be one of host/termux/ubuntu")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{field_name}.path must be a non-empty string")
    return PathRef(layer=layer, path=path)


def path_ref_schema(*, description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "layer": {
                "type": "string",
                "enum": ["host", "termux", "ubuntu"],
            },
            "path": {
                "type": "string",
                "description": "Absolute path for the selected layer.",
            },
        },
        "required": ["layer", "path"],
        "additionalProperties": False,
    }


def sanitize_stream(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text).rstrip("\n")


def summarize_backend_output(stdout: str, stderr: str, *, max_lines: int = 8) -> str:
    chunks: list[str] = []
    clean_stdout = sanitize_stream(stdout)
    clean_stderr = sanitize_stream(stderr)
    if clean_stdout:
        chunks.append(_truncate_lines(clean_stdout, max_lines=max_lines))
    if clean_stderr:
        chunks.append("stderr: " + _truncate_lines(clean_stderr, max_lines=max_lines))
    return "\n".join(chunks) if chunks else "<empty>"


def summarize_backend_steps(steps: list[tuple[str, Any]]) -> str:
    sections: list[str] = []
    for label, result in steps:
        sections.append(
            f"[{label}]\n{summarize_backend_output(getattr(result, 'stdout', ''), getattr(result, 'stderr', ''))}"
        )
    return "\n\n".join(sections) if sections else "<empty>"


def extract_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed([line.strip() for line in stdout.splitlines() if line.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("stdout did not contain a JSON object line")


def termux_relative_path(path_ref: PathRef) -> str:
    if path_ref.layer != "termux":
        raise ValueError("termux_relative_path expects a termux PathRef")
    base = PurePosixPath(TERMUX_HOME)
    target = PurePosixPath(path_ref.path)
    try:
        relative = target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"termux path must stay under {TERMUX_HOME}: {path_ref.path}") from exc
    return str(relative)


def termux_path(relative_path: str) -> PathRef:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute():
        raise ValueError(f"termux relative path must not be absolute: {relative_path}")
    return PathRef(layer="termux", path=str(PurePosixPath(TERMUX_HOME) / relative))


def termux_stage_relative(namespace: str, filename: str) -> str:
    safe_name = PurePosixPath(filename or "artifact").name or "artifact"
    return str(PurePosixPath("phoneharness") / "staging" / namespace / f"{unique_suffix()}-{safe_name}")


def ubuntu_path(*parts: str, on_device: bool = False) -> PathRef:
    """Build a work path. When on_device=True, use Termux home instead of /root."""
    root = DEFAULT_TERMUX_WORK_ROOT if on_device else DEFAULT_UBUNTU_ROOT
    layer = "termux" if on_device else "ubuntu"
    current = PurePosixPath(root)
    for part in parts:
        if not part:
            continue
        piece = PurePosixPath(part)
        if piece.is_absolute():
            current = piece
        else:
            current = current / piece
    return PathRef(layer=layer, path=str(current))


def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]


def ensure_host_parent(path_ref: PathRef) -> None:
    if path_ref.layer != "host":
        return
    Path(path_ref.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _truncate_lines(text: str, *, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = lines[:max_lines]
    kept.append("...")
    return "\n".join(kept)
