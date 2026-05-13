from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..agent.message import PathRef
from ..backend import AdbBackend, BackendCommandResult
from ..util.trace import ArtifactStore


@dataclass(frozen=True)
class M0bProbeConfig:
    serial: str | None
    output_dir: str | Path
    remote_relative_path: str = "phoneharness/m0b/roundtrip_probe.txt"


@dataclass(frozen=True)
class M0bProbeOutcome:
    success: bool
    run_dir: PathRef
    trace_path: PathRef
    summary_path: PathRef
    source_file: PathRef
    remote_file: PathRef | None
    pulled_file: PathRef | None
    source_sha256: str
    pulled_sha256: str
    backend_conclusion: str
    blocker: str | None = None


@dataclass(frozen=True)
class CommandArtifacts:
    result: PathRef
    stdout: PathRef
    stderr: PathRef


def run_m0b_probe(config: M0bProbeConfig) -> M0bProbeOutcome:
    artifacts = ArtifactStore(config.output_dir, run_prefix="m0b")
    backend = AdbBackend()
    paths = artifacts.paths()

    artifacts.append_trace(
        "probe_started",
        {
            "serial": config.serial,
            "remote_relative_path": config.remote_relative_path,
        },
    )

    pwd_result = backend.exec_ubuntu("pwd", serial=config.serial)
    pwd_artifacts = _write_command_artifacts(artifacts, "pwd", pwd_result)

    python_result = backend.exec_ubuntu("python3 --version", serial=config.serial)
    python_artifacts = _write_command_artifacts(artifacts, "python_version", python_result)

    source_content = "PhoneHarness M0b roundtrip probe\n"
    source_file = artifacts.write_text("roundtrip_source.txt", source_content)
    source_sha256 = _sha256(Path(source_file.path))
    artifacts.append_trace(
        "source_prepared",
        {
            "source_file": source_file.path,
            "source_sha256": source_sha256,
        },
    )

    push_result = backend.push_termux_home(source_file.path, config.remote_relative_path, serial=config.serial)
    push_artifacts = _write_command_artifacts(artifacts, "push", push_result)

    pulled_target = Path(paths.run_dir.path) / "roundtrip_pulled.txt"
    pull_result = backend.pull_termux_home(config.remote_relative_path, pulled_target, serial=config.serial)
    pull_artifacts = _write_command_artifacts(artifacts, "pull", pull_result)

    pulled_file = pull_result.resolved_path if pull_result.ok else None
    pulled_sha256 = _sha256(Path(pulled_file.path)) if pulled_file and Path(pulled_file.path).is_file() else ""
    hashes_match = bool(source_sha256 and pulled_sha256 and source_sha256 == pulled_sha256)
    remote_file = push_result.resolved_path

    checks = {
        "pwd_ok": pwd_result.ok and bool(pwd_result.stdout.strip()),
        "python_version_ok": python_result.ok and bool(python_result.stdout.strip()),
        "push_ok": push_result.ok and remote_file is not None,
        "pull_ok": pull_result.ok and pulled_file is not None,
        "hash_match": hashes_match,
    }
    success = all(checks.values())
    blocker = None if success else _build_blocker(checks, pwd_result, python_result, push_result, pull_result)

    roundtrip_payload = {
        "source_file": source_file.to_dict(),
        "remote_file": None if remote_file is None else remote_file.to_dict(),
        "pulled_file": None if pulled_file is None else pulled_file.to_dict(),
        "source_sha256": source_sha256,
        "pulled_sha256": pulled_sha256,
        "checks": checks,
        "push_result": push_artifacts.result.to_dict(),
        "pull_result": pull_artifacts.result.to_dict(),
    }
    roundtrip_path = artifacts.write_json("roundtrip.json", roundtrip_payload)
    artifacts.append_trace(
        "roundtrip_checked",
        {
            "path": roundtrip_path.path,
            "checks": checks,
            "source_sha256": source_sha256,
            "pulled_sha256": pulled_sha256,
        },
    )

    backend_conclusion = (
        "adb -> run-as -> Termux -> proot Ubuntu backend probe passed: remote exec worked and host->Termux->host roundtrip hash matched."
        if success
        else "adb -> run-as -> Termux -> proot Ubuntu backend probe did not satisfy all M0b checks."
    )

    summary_payload = {
        "success": success,
        "serial": config.serial,
        "pwd": {
            "result_path": pwd_artifacts.result.to_dict(),
            "stdout": pwd_result.stdout,
            "stderr": pwd_result.stderr,
            "exit_code": pwd_result.exit_code,
        },
        "python_version": {
            "result_path": python_artifacts.result.to_dict(),
            "stdout": python_result.stdout,
            "stderr": python_result.stderr,
            "exit_code": python_result.exit_code,
        },
        "roundtrip": roundtrip_payload,
        "backend_conclusion": backend_conclusion,
        "blocker": blocker,
    }
    summary_path = artifacts.write_json("summary.json", summary_payload)
    artifacts.append_trace(
        "probe_finished",
        {
            "success": success,
            "summary_path": summary_path.path,
            "backend_conclusion": backend_conclusion,
            "blocker": blocker,
        },
    )

    return M0bProbeOutcome(
        success=success,
        run_dir=paths.run_dir,
        trace_path=paths.trace,
        summary_path=summary_path,
        source_file=source_file,
        remote_file=remote_file,
        pulled_file=pulled_file,
        source_sha256=source_sha256,
        pulled_sha256=pulled_sha256,
        backend_conclusion=backend_conclusion,
        blocker=blocker,
    )


def _write_command_artifacts(artifacts: ArtifactStore, name: str, result: BackendCommandResult) -> CommandArtifacts:
    stdout_path = artifacts.write_text(f"{name}.stdout.txt", result.stdout)
    stderr_path = artifacts.write_text(f"{name}.stderr.txt", result.stderr)
    result_payload = result.to_dict()
    result_payload["stdout_path"] = stdout_path.to_dict()
    result_payload["stderr_path"] = stderr_path.to_dict()
    result_path = artifacts.write_json(f"{name}.result.json", result_payload)
    artifacts.append_trace(
        "backend_command",
        {
            "name": name,
            "exit_code": result.exit_code,
            "result_path": result_path.path,
            "stdout_path": stdout_path.path,
            "stderr_path": stderr_path.path,
            "resolved_path": None if result.resolved_path is None else result.resolved_path.to_dict(),
        },
    )
    return CommandArtifacts(result=result_path, stdout=stdout_path, stderr=stderr_path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_blocker(
    checks: dict[str, bool],
    pwd_result: BackendCommandResult,
    python_result: BackendCommandResult,
    push_result: BackendCommandResult,
    pull_result: BackendCommandResult,
) -> str:
    if not checks["pwd_ok"]:
        return f"pwd failed with exit_code={pwd_result.exit_code}"
    if not checks["python_version_ok"]:
        return f"python3 --version failed with exit_code={python_result.exit_code}"
    if not checks["push_ok"]:
        return f"push failed with exit_code={push_result.exit_code}"
    if not checks["pull_ok"]:
        return f"pull failed with exit_code={pull_result.exit_code}"
    if not checks["hash_match"]:
        return "pulled file hash did not match source file hash"
    return "unknown backend probe failure"
