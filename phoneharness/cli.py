from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agent.loop import ProbeConfig, run_m0a_probe
from .agent.m1 import M1RunConfig, run_m1
from .agent.m2 import M2RunConfig, run_m2
from .agent.m3 import M3RunConfig, run_m3_ondevice
from .agent.m4 import M4RunConfig, run_m4_ondevice
from .probe import M0bProbeConfig, run_m0b_probe
from .replay import render_replay_text
from .skills import SkillLoaderError, load_skill_files


KNOWN_COMMANDS = {"console", "server", "m0a-probe", "m0b-probe", "m1-run", "m2-run", "m3-run", "m4-run", "skill-check", "replay-view"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PhoneHarness utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Console — the default user entry point
    console_parser = subparsers.add_parser("console", help="launch the interactive CLI-first agent console")
    console_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "doubao-2.0-pro"), help="Model id")
    console_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://10.0.2.2:8918/v1"), help="OpenAI-compatible base URL")
    console_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "test"), help="API key")
    console_parser.add_argument("--gui-proxy-url", default="http://10.0.2.2:8919", help="GUI proxy URL")
    console_parser.add_argument("--gui-model", help="Required. Inner GUI worker model id; no hidden fallback.")
    console_parser.add_argument("--gui-api-url", help="Optional GUI model API base URL. Defaults to --base-url when omitted.")
    console_parser.add_argument("--gui-api-key", help="Optional GUI model API key. Defaults to --api-key when omitted.")
    console_parser.add_argument("--gui-mode", choices=["delegated", "flat"], default="delegated",
                                help="GUI mode: delegated (default, recommended) or flat (direct gui_* tools)")
    console_parser.add_argument("--flat-gui-protocol", choices=["tool_call", "text_xml"], default="tool_call",
                                help="Flat mode GUI protocol: tool_call (JSON) or text_xml (Seed XML sub-loop)")
    console_parser.add_argument("--one-model", action="store_true", help="Alias for --gui-mode flat")
    console_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB serial (host mode)")
    console_parser.add_argument("--skill-file", action="append", default=[], help="YAML skill file(s)")

    # Server — HTTP wrapper for APK integration
    server_parser = subparsers.add_parser("server", help="launch the HTTP agent server")
    server_parser.add_argument("--port", type=int, default=8920, help="Listen port (default 8920)")
    server_parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    server_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "doubao-2.0-pro"), help="Model id")
    server_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://10.0.2.2:8918/v1"), help="OpenAI-compatible base URL")
    server_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "test"), help="API key")
    server_parser.add_argument("--gui-proxy-url", default="http://10.0.2.2:8919", help="GUI proxy URL")
    server_parser.add_argument("--gui-model", help="Required. Inner GUI worker model id; no hidden fallback.")
    server_parser.add_argument("--gui-api-url", help="Optional GUI model API base URL. Defaults to --base-url when omitted.")
    server_parser.add_argument("--gui-api-key", help="Optional GUI model API key. Defaults to --api-key when omitted.")
    server_parser.add_argument("--gui-mode", choices=["delegated", "flat"], default="delegated",
                                help="GUI mode: delegated (default, recommended) or flat (direct gui_* tools)")
    server_parser.add_argument("--flat-gui-protocol", choices=["tool_call", "text_xml"], default="tool_call",
                                help="Flat mode GUI protocol: tool_call (JSON) or text_xml (Seed XML sub-loop)")
    server_parser.add_argument("--one-model", action="store_true", help="Alias for --gui-mode flat")
    server_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB serial (host mode)")
    server_parser.add_argument("--skill-file", action="append", default=[], help="YAML skill file(s)")

    probe_parser = subparsers.add_parser("m0a-probe", help="run the M0a tool-call roundtrip probe")
    probe_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"), help="OpenAI-compatible base URL")
    probe_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="API key for the provider")
    probe_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"), help="Model id")
    probe_parser.add_argument(
        "--output-dir",
        default=str(Path("artifacts") / "m0a"),
        help="Directory where raw requests, responses, and trace artifacts are written",
    )

    backend_parser = subparsers.add_parser("m0b-probe", help="run the M0b backend probe")
    backend_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB device serial")
    backend_parser.add_argument(
        "--output-dir",
        default=str(Path("artifacts") / "m0b"),
        help="Directory where backend probe artifacts are written",
    )
    backend_parser.add_argument(
        "--remote-relative-path",
        default="phoneharness/m0b/roundtrip_probe.txt",
        help="Relative path under Termux home for the roundtrip file",
    )

    m1_parser = subparsers.add_parser("m1-run", help="run the M1 single-tool loop")
    m1_parser.add_argument("user_input", help="User instruction, for example: 在设备上执行 pwd")
    m1_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"), help="OpenAI-compatible base URL")
    m1_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="API key for the provider")
    m1_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"), help="Model id")
    m1_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB device serial")
    m1_parser.add_argument(
        "--output-dir",
        default=str(Path("artifacts") / "m1"),
        help="Directory where M1 artifacts are written",
    )

    m2_parser = subparsers.add_parser("m2-run", help="run the M2 multi-tool CLI harness")
    m2_parser.add_argument("user_input", help="User instruction for the CLI harness")
    m2_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"), help="OpenAI-compatible base URL")
    m2_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="API key for the provider")
    m2_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"), help="Model id")
    m2_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB device serial")
    m2_parser.add_argument(
        "--output-dir",
        default=str(Path("artifacts") / "m2"),
        help="Directory where M2 artifacts are written",
    )

    m3_parser = subparsers.add_parser("m3-run", help="run the M3 on-device controller spike")
    m3_parser.add_argument("user_input", help="User instruction for the on-device controller")
    m3_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"), help="OpenAI-compatible base URL")
    m3_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="API key for the provider")
    m3_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"), help="Model id")
    m3_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB device serial (host-driven mode; omit for on-device mode)")
    m3_parser.add_argument(
        "--output-dir",
        default=str(Path("artifacts") / "m3-device"),
        help="Directory where on-device artifacts are written",
    )
    m3_parser.add_argument(
        "--skill-file",
        action="append",
        default=[],
        help="YAML skill file to load and inject into the controller prompt. May be passed multiple times.",
    )

    m4_parser = subparsers.add_parser("m4-run", help="run the M4 on-device CLI+GUI controller")
    m4_parser.add_argument("user_input", help="User instruction for the CLI+GUI controller")
    m4_parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"), help="OpenAI-compatible base URL")
    m4_parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="API key for the provider")
    m4_parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"), help="Model id")
    m4_parser.add_argument("--serial", default=os.environ.get("ADB_SERIAL"), help="ADB device serial (host-driven mode)")
    m4_parser.add_argument("--gui-proxy-url", default="http://10.0.2.2:8919", help="GUI proxy URL")
    m4_parser.add_argument("--output-dir", default=str(Path("artifacts") / "m4"), help="Directory where M4 artifacts are written")
    m4_parser.add_argument("--skill-file", action="append", default=[], help="YAML skill file(s)")

    skill_parser = subparsers.add_parser("skill-check", help="validate YAML skill files")
    skill_parser.add_argument("skill_files", nargs="+", help="One or more YAML skill files")

    replay_parser = subparsers.add_parser("replay-view", help="show a minimal replay view from summary/journal")
    replay_parser.add_argument("source", help="Run directory, summary.json, or journal.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # No args → launch console (default entry point)
    if not argv:
        print("error: explicit --gui-model is required.", file=sys.stderr)
        print("try: python3 -m phoneharness console --model <control-model> --gui-model <gui-model>", file=sys.stderr)
        return 2

    if argv and argv[0] not in KNOWN_COMMANDS and not argv[0].startswith("-"):
        return _run_m2_command(
            user_input=" ".join(argv),
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            model=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
            serial=os.environ.get("ADB_SERIAL"),
            output_dir=str(Path("artifacts") / "m2"),
        )

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"console", "server"} and not getattr(args, "gui_model", None):
        parser.error("--gui-model is required; specify it explicitly even when it matches --model")

    # Resolve --one-model alias
    gui_mode = getattr(args, 'gui_mode', 'delegated')
    if getattr(args, 'one_model', False):
        gui_mode = "flat"
    flat_gui_protocol = getattr(args, 'flat_gui_protocol', 'tool_call')

    if args.command == "console":
        from .console import ConsoleConfig, run_console
        return run_console(ConsoleConfig(
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            gui_proxy_url=args.gui_proxy_url,
            gui_model=args.gui_model,
            gui_api_url=args.gui_api_url,
            gui_api_key=args.gui_api_key,
            gui_mode=gui_mode,
            flat_gui_protocol=flat_gui_protocol,
            serial=args.serial,
            skill_paths=tuple(args.skill_file),
        ))

    if args.command == "server":
        from .console import ConsoleConfig
        from .server import ServerConfig, run_server
        return run_server(ServerConfig(
            port=args.port,
            host=args.host,
            console_config=ConsoleConfig(
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                gui_proxy_url=args.gui_proxy_url,
                gui_model=args.gui_model,
                gui_api_url=args.gui_api_url,
                gui_api_key=args.gui_api_key,
                gui_mode=gui_mode,
                flat_gui_protocol=flat_gui_protocol,
                serial=args.serial,
                skill_paths=tuple(args.skill_file),
            ),
        ))

    if args.command == "m0a-probe":
        if not args.base_url:
            parser.error("--base-url is required (or set OPENAI_BASE_URL)")
        if not args.api_key:
            parser.error("--api-key is required (or set OPENAI_API_KEY)")
        outcome = run_m0a_probe(
            ProbeConfig(
                base_url=args.base_url,
                api_key=args.api_key,
                model=args.model,
                output_dir=args.output_dir,
            )
        )
        print(f"success={outcome.success}")
        print(f"run_dir={outcome.run_dir.path}")
        print(f"trace={outcome.trace_path.path}")
        print(f"requests={[ref.path for ref in outcome.request_paths]}")
        print(f"responses={[ref.path for ref in outcome.response_paths]}")
        print(f"tool_calls={[ref.path for ref in outcome.tool_call_paths]}")
        print(f"tool_results={[ref.path for ref in outcome.tool_result_paths]}")
        print(f"final_text={outcome.final_text!r}")
        print(f"provider_conclusion={outcome.provider_conclusion}")
        if outcome.blocker:
            print(f"blocker={outcome.blocker}")
        return 0 if outcome.success else 1

    if args.command == "m0b-probe":
        outcome = run_m0b_probe(
            M0bProbeConfig(
                serial=args.serial,
                output_dir=args.output_dir,
                remote_relative_path=args.remote_relative_path,
            )
        )
        print(f"success={outcome.success}")
        print(f"run_dir={outcome.run_dir.path}")
        print(f"trace={outcome.trace_path.path}")
        print(f"summary={outcome.summary_path.path}")
        print(f"source_file={outcome.source_file.path}")
        print(f"remote_file={None if outcome.remote_file is None else outcome.remote_file.path}")
        print(f"pulled_file={None if outcome.pulled_file is None else outcome.pulled_file.path}")
        print(f"source_sha256={outcome.source_sha256}")
        print(f"pulled_sha256={outcome.pulled_sha256}")
        print(f"backend_conclusion={outcome.backend_conclusion}")
        if outcome.blocker:
            print(f"blocker={outcome.blocker}")
        return 0 if outcome.success else 1

    if args.command == "m1-run":
        return _run_m1_command(
            user_input=args.user_input,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            serial=args.serial,
            output_dir=args.output_dir,
        )

    if args.command == "m2-run":
        return _run_m2_command(
            user_input=args.user_input,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            serial=args.serial,
            output_dir=args.output_dir,
        )

    if args.command == "m3-run":
        return _run_m3_command(
            user_input=args.user_input,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            serial=args.serial,
            output_dir=args.output_dir,
            skill_files=tuple(args.skill_file),
        )

    if args.command == "m4-run":
        return _run_m4_command(
            user_input=args.user_input,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            serial=args.serial,
            gui_proxy_url=args.gui_proxy_url,
            output_dir=args.output_dir,
            skill_files=tuple(args.skill_file),
        )

    if args.command == "skill-check":
        try:
            skills = load_skill_files(args.skill_files)
        except SkillLoaderError as exc:
            print(f"error={exc}")
            return 1
        print(f"loaded_skills={[skill.name for skill in skills]}")
        print(f"skill_sources={[skill.source_path for skill in skills]}")
        return 0

    if args.command == "replay-view":
        print(render_replay_text(args.source))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _run_m1_command(
    *,
    user_input: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
    serial: str | None,
    output_dir: str,
) -> int:
    if not base_url:
        raise SystemExit("error: OPENAI_BASE_URL or --base-url is required for M1")
    if not api_key:
        raise SystemExit("error: OPENAI_API_KEY or --api-key is required for M1")

    outcome = run_m1(
        M1RunConfig(
            user_input=user_input,
            base_url=base_url,
            api_key=api_key,
            model=model,
            serial=serial,
            output_dir=output_dir,
        )
    )
    print(f"success={outcome.success}")
    print(f"run_dir={outcome.run_dir.path}")
    print(f"trace={outcome.trace_path.path}")
    print(f"summary={outcome.summary_path.path}")
    print(f"requests={[ref.path for ref in outcome.request_paths]}")
    print(f"responses={[ref.path for ref in outcome.response_paths]}")
    print(f"tool_calls={[ref.path for ref in outcome.tool_call_paths]}")
    print(f"tool_results={[ref.path for ref in outcome.tool_result_paths]}")
    print(f"backend_result={None if outcome.backend_result_path is None else outcome.backend_result_path.path}")
    print(f"backend_stdout={None if outcome.backend_stdout_path is None else outcome.backend_stdout_path.path}")
    print(f"backend_stderr={None if outcome.backend_stderr_path is None else outcome.backend_stderr_path.path}")
    print(f"backend_exit_code={outcome.backend_exit_code}")
    print(f"final_text={outcome.final_text!r}")
    print(f"run_conclusion={outcome.run_conclusion}")
    if outcome.blocker:
        print(f"blocker={outcome.blocker}")
    return 0 if outcome.success else 1


def _run_m2_command(
    *,
    user_input: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
    serial: str | None,
    output_dir: str,
) -> int:
    if not base_url:
        raise SystemExit("error: OPENAI_BASE_URL or --base-url is required for M2")
    if not api_key:
        raise SystemExit("error: OPENAI_API_KEY or --api-key is required for M2")

    from .backend import AdbBackend
    from .tools.file_transfer import FileTransferTool
    from .tools.python_exec import PythonExecTool
    from .tools.shell_exec import ShellExecTool
    from .tools.task_health_check import TaskHealthCheckTool
    from .tools.task_pdf_merge import TaskPdfMergeTool
    from .tools.task_word_append import TaskWordAppendTool
    from .tools.registry import ToolRegistry

    backend = AdbBackend()
    registry = ToolRegistry([
        TaskHealthCheckTool(backend=backend, serial=serial),
        TaskPdfMergeTool(backend=backend, serial=serial),
        TaskWordAppendTool(backend=backend, serial=serial),
        FileTransferTool(backend=backend, serial=serial),
        PythonExecTool(backend=backend, serial=serial),
        ShellExecTool(backend=backend, serial=serial),
    ])

    outcome = run_m2(
        M2RunConfig(
            user_input=user_input,
            base_url=base_url,
            api_key=api_key,
            model=model,
            output_dir=output_dir,
            tool_registry=registry,
        )
    )
    print(f"success={outcome.success}")
    print(f"run_dir={outcome.run_dir.path}")
    print(f"trace={outcome.trace_path.path}")
    print(f"summary={outcome.summary_path.path}")
    print(f"requests={[ref.path for ref in outcome.request_paths]}")
    print(f"responses={[ref.path for ref in outcome.response_paths]}")
    print(f"tool_calls={[ref.path for ref in outcome.tool_call_paths]}")
    print(f"tool_results={[ref.path for ref in outcome.tool_result_paths]}")
    print(f"final_text={outcome.final_text!r}")
    print(f"run_conclusion={outcome.run_conclusion}")
    if outcome.error_type:
        print(f"error_type={outcome.error_type}")
    if outcome.blocker:
        print(f"blocker={outcome.blocker}")
    return 0 if outcome.success else 1


def _run_m4_command(
    *,
    user_input: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
    serial: str | None,
    gui_proxy_url: str,
    output_dir: str,
    skill_files: tuple[str, ...],
) -> int:
    if not base_url:
        raise SystemExit("error: OPENAI_BASE_URL or --base-url is required for M4")
    if not api_key:
        raise SystemExit("error: OPENAI_API_KEY or --api-key is required for M4")

    try:
        outcome = run_m4_ondevice(
            M4RunConfig(
                user_input=user_input,
                base_url=base_url,
                api_key=api_key,
                model=model,
                output_dir=output_dir,
                skill_paths=skill_files,
                serial=serial,
                gui_proxy_url=gui_proxy_url,
            )
        )
    except SkillLoaderError as exc:
        print(f"error={exc}")
        return 1

    print(f"success={outcome.success}")
    print(f"run_dir={outcome.run_dir.path}")
    print(f"trace={outcome.trace_path.path}")
    print(f"summary={outcome.summary_path.path}")
    print(f"journal={outcome.journal_path.path}")
    print(f"active_skills={list(outcome.active_skills)}")
    print(f"requests={[ref.path for ref in outcome.request_paths]}")
    print(f"responses={[ref.path for ref in outcome.response_paths]}")
    print(f"tool_calls={[ref.path for ref in outcome.tool_call_paths]}")
    print(f"tool_results={[ref.path for ref in outcome.tool_result_paths]}")
    print(f"final_text={outcome.final_text!r}")
    print(f"run_conclusion={outcome.run_conclusion}")
    if outcome.error_type:
        print(f"error_type={outcome.error_type}")
    if outcome.blocker:
        print(f"blocker={outcome.blocker}")
    return 0 if outcome.success else 1
def _run_m3_command(
    *,
    user_input: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
    serial: str | None,
    output_dir: str,
    skill_files: tuple[str, ...],
) -> int:
    if not base_url:
        raise SystemExit("error: OPENAI_BASE_URL or --base-url is required for M3")
    if not api_key:
        raise SystemExit("error: OPENAI_API_KEY or --api-key is required for M3")

    try:
        outcome = run_m3_ondevice(
            M3RunConfig(
                user_input=user_input,
                base_url=base_url,
                api_key=api_key,
                model=model,
                output_dir=output_dir,
                skill_paths=skill_files,
                serial=serial,
            )
        )
    except SkillLoaderError as exc:
        print(f"error={exc}")
        return 1

    print(f"success={outcome.success}")
    print(f"run_dir={outcome.run_dir.path}")
    print(f"trace={outcome.trace_path.path}")
    print(f"summary={outcome.summary_path.path}")
    print(f"journal={outcome.journal_path.path}")
    print(f"active_skills={list(outcome.active_skills)}")
    print(f"requests={[ref.path for ref in outcome.request_paths]}")
    print(f"responses={[ref.path for ref in outcome.response_paths]}")
    print(f"tool_calls={[ref.path for ref in outcome.tool_call_paths]}")
    print(f"tool_results={[ref.path for ref in outcome.tool_result_paths]}")
    print(f"final_text={outcome.final_text!r}")
    print(f"run_conclusion={outcome.run_conclusion}")
    if outcome.error_type:
        print(f"error_type={outcome.error_type}")
    if outcome.blocker:
        print(f"blocker={outcome.blocker}")
    return 0 if outcome.success else 1
