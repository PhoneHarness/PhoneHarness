"""MCP-Bench tool wrappers.

Reads tool schemas from configs/mcp_bench/ and creates BaseTool instances
for the phoneharness registry.

CLI tools execute via subprocess (command_template).
Skill tools execute via python3 subprocess (import + call function).
function_call tools are skipped (already in phoneharness as mobile_actions).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..agent.message import ToolResult
from .base import BaseTool


def _find_configs_dir() -> Path:
    """Find configs/mcp_bench/ directory."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "configs" / "mcp_bench",
        Path.home() / "configs" / "mcp_bench",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return candidates[0]


class MCPBenchCLITool(BaseTool):
    """Wrapper for MCP-Bench CLI tools (termux-api, adb commands)."""

    def __init__(self, tool_name: str, tool_config: dict):
        self.name = tool_name
        self.description = tool_config.get("description", "")
        self.input_schema = tool_config.get("parameters", {"type": "object", "properties": {}})
        self._template = tool_config.get("command_template", "")
        self._source = tool_config.get("source", "")
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        if not self._template:
            return ToolResult(ok=False, output="No command template", summary="no template")

        # Fill template with arguments
        cmd = self._template
        for key, value in arguments.items():
            cmd = cmd.replace(f"{{{key}}}", str(value))
        # Remove unfilled placeholders
        cmd = re.sub(r'\s*-\S+\s+\{[^}]+\}', '', cmd)
        cmd = re.sub(r'\{[^}]+\}', '', cmd)
        cmd = re.sub(r'\s+', ' ', cmd).strip()

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                return ToolResult(
                    ok=False,
                    output=f"Exit {result.returncode}: {result.stderr.strip()[:200]}",
                    summary=f"{self.name} failed (exit {result.returncode})",
                )
            return ToolResult(
                ok=True,
                output=output[:2000] if output else "(empty output)",
                summary=f"{self.name} succeeded",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output="timeout", summary=f"{self.name} timeout")
        except Exception as exc:
            return ToolResult(ok=False, output=str(exc), summary=f"{self.name} error")


class MCPBenchSkillTool(BaseTool):
    """Wrapper for MCP-Bench Skill tools (Python functions)."""

    def __init__(self, tool_name: str, tool_config: dict):
        self.name = tool_name
        self.description = tool_config.get("description", "")
        self.input_schema = tool_config.get("parameters", {"type": "object", "properties": {}})
        self._module = tool_config.get("module", "")
        self._function = tool_config.get("function", tool_name)
        self._python_lib = tool_config.get("python_lib", "")
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        args_json = json.dumps(arguments, ensure_ascii=False)

        # Execute via subprocess to isolate from main process
        code = (
            f"import json, sys\n"
            f"try:\n"
            f"    from {self._module} import {self._function}\n"
            f"    args = json.loads({args_json!r})\n"
            f"    result = {self._function}(**args)\n"
            f"    print(json.dumps({{'ok': True, 'output': str(result)[:500]}}))\n"
            f"except Exception as e:\n"
            f"    print(json.dumps({{'ok': False, 'error': f'{{type(e).__name__}}: {{e}}'}}))\n"
        )

        try:
            result = subprocess.run(
                ["python3", "-c", code],
                capture_output=True, text=True, timeout=30,
            )
            # Find last JSON line in output
            for line in reversed(result.stdout.strip().splitlines()):
                try:
                    data = json.loads(line.strip())
                    if data.get("ok"):
                        return ToolResult(ok=True, output=data.get("output", ""), summary=f"{self.name} succeeded")
                    else:
                        return ToolResult(ok=False, output=data.get("error", "unknown"), summary=f"{self.name} failed")
                except json.JSONDecodeError:
                    continue
            return ToolResult(ok=False, output=result.stderr[:200] or "no output", summary=f"{self.name} failed")
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output="timeout", summary=f"{self.name} timeout")
        except Exception as exc:
            return ToolResult(ok=False, output=str(exc), summary=f"{self.name} error")


def load_mcp_bench_tools() -> list[BaseTool]:
    """Load all MCP-Bench tools from config files. Returns list of BaseTool instances."""
    configs_dir = _find_configs_dir()
    tools: list[BaseTool] = []

    # CLI tools
    cli_path = configs_dir / "cli_tools.json"
    if cli_path.exists():
        cli_config = json.loads(cli_path.read_text(encoding="utf-8"))
        for name, info in cli_config.get("tools", {}).items():
            if info.get("type") == "function_call":
                continue  # Skip — already in phoneharness as mobile_actions
            tools.append(MCPBenchCLITool(name, info))

    # Skill tools
    skill_path = configs_dir / "skill_tools.json"
    if skill_path.exists():
        skill_config = json.loads(skill_path.read_text(encoding="utf-8"))
        for name, info in skill_config.get("tools", {}).items():
            tools.append(MCPBenchSkillTool(name, info))

    return tools
