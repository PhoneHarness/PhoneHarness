from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..agent.message import PathRef


class BackendTimeoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendCommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    resolved_path: PathRef | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "resolved_path": None if self.resolved_path is None else self.resolved_path.to_dict(),
        }


class AdbBackend:
    """Two-channel backend for host-driven execution on Android device.

    Channel 1 (run-as → proot Ubuntu):
      - exec_ubuntu, exec_ubuntu_python, copy_*, stat_ubuntu_path, ensure_ubuntu_dir
      - Has filesystem and proot access
      - NO network (SELinux runas_app restriction on API 36+)

    Channel 2 (SSH → Termux):
      - exec_termux (default --channel ssh)
      - Has network access (app context, INTERNET permission)
      - NO proot (getcwd() incompatibility in SSH sessions)

    Use Channel 1 for: file processing, Python scripts, ffmpeg, proot tools
    Use Channel 2 for: model API reachability probes, network-dependent tools
    """
    def __init__(self, *, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve() if repo_root else Path(__file__).resolve().parents[2]
        self.scripts_dir = self.repo_root / "scripts"

    def health_check(
        self,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        argv = [str(self._script_path("health_check.sh"))]
        if serial:
            argv.extend(["--serial", serial])
        return self._run(argv, timeout_seconds=timeout_seconds)

    def exec_ubuntu(
        self,
        command: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        argv = [str(self._script_path("adb_ubuntu_exec.sh"))]
        if serial:
            argv.extend(["--serial", serial])
        argv.extend(["--cmd", command])
        return self._run(argv, timeout_seconds=timeout_seconds)

    def exec_termux(
        self,
        command: str,
        *,
        serial: str | None = None,
        channel: str = "ssh",
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """Execute in Termux. channel='ssh' (default, has network) or 'runas' (no network)."""
        argv = [str(self._script_path("adb_termux_exec.sh"))]
        if serial:
            argv.extend(["--serial", serial])
        argv.extend(["--channel", channel])
        argv.extend(["--cmd", command])
        return self._run(argv, timeout_seconds=timeout_seconds)

    def exec_ubuntu_python(
        self,
        code: str,
        *,
        serial: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        argv = [str(self._script_path("adb_ubuntu_python.sh"))]
        if serial:
            argv.extend(["--serial", serial])
        for name, value in sorted((env or {}).items()):
            argv.extend(["--env", f"{name}={value}"])
        argv.extend(["--code", code])
        return self._run(argv, timeout_seconds=timeout_seconds)

    def push_termux_home(
        self,
        local_path: str | Path,
        remote_relative_path: str,
        *,
        serial: str | None = None,
        mode: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        resolved_local = Path(local_path).expanduser().resolve()
        argv = [str(self._script_path("adb_push_termux_home.sh"))]
        if serial:
            argv.extend(["--serial", serial])
        if mode:
            argv.extend(["--mode", mode])
        argv.extend([str(resolved_local), remote_relative_path])
        result = self._run(argv, timeout_seconds=timeout_seconds)
        return BackendCommandResult(
            argv=result.argv,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            resolved_path=self._path_from_stdout(result.stdout, layer="termux") if result.ok else None,
        )

    def pull_termux_home(
        self,
        remote_relative_path: str,
        local_path: str | Path,
        *,
        serial: str | None = None,
        method: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        resolved_local = Path(local_path).expanduser().resolve()
        argv = [str(self._script_path("adb_pull_termux_home.sh"))]
        if serial:
            argv.extend(["--serial", serial])
        if method:
            argv.extend(["--method", method])
        argv.extend([remote_relative_path, str(resolved_local)])
        result = self._run(argv, timeout_seconds=timeout_seconds)
        return BackendCommandResult(
            argv=result.argv,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            resolved_path=PathRef(layer="host", path=str(resolved_local)) if result.ok else None,
        )

    def copy_ubuntu_file(
        self,
        source_path: str,
        dest_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        command = self._copy_command(source_path, dest_path)
        return self.exec_ubuntu(command, serial=serial, timeout_seconds=timeout_seconds)

    def copy_termux_to_ubuntu(
        self,
        source_path: str,
        dest_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        command = self._copy_command(source_path, dest_path)
        return self.exec_ubuntu(command, serial=serial, timeout_seconds=timeout_seconds)

    def copy_ubuntu_to_termux(
        self,
        source_path: str,
        dest_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        command = self._copy_command(source_path, dest_path)
        return self.exec_ubuntu(command, serial=serial, timeout_seconds=timeout_seconds)

    def ensure_ubuntu_dir(
        self,
        directory: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        command = f"mkdir -p {shlex.quote(directory)}"
        return self.exec_ubuntu(command, serial=serial, timeout_seconds=timeout_seconds)

    def stat_ubuntu_path(
        self,
        path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        command = self._test_file_command(path)
        return self.exec_ubuntu(command, serial=serial, timeout_seconds=timeout_seconds)

    def stat_termux_path(
        self,
        path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        command = self._test_file_command(path)
        return self.exec_termux(command, serial=serial, timeout_seconds=timeout_seconds)

    def _script_path(self, name: str) -> Path:
        path = self.scripts_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"missing backend script: {path}")
        return path

    def _run(self, argv: list[str], *, timeout_seconds: float | None = None) -> BackendCommandResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendTimeoutError(
                f"command timed out after {timeout_seconds}s: {argv}"
            ) from exc
        return BackendCommandResult(
            argv=tuple(argv),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @staticmethod
    def _path_from_stdout(stdout: str, *, layer: str) -> PathRef | None:
        candidate = stdout.strip()
        if not candidate.startswith("/"):
            return None
        return PathRef(layer=layer, path=candidate)

    @staticmethod
    def _copy_command(source_path: str, dest_path: str) -> str:
        return (
            f"mkdir -p {shlex.quote(str(Path(dest_path).parent))} && "
            f"cp {shlex.quote(source_path)} {shlex.quote(dest_path)}"
        )

    @staticmethod
    def _test_file_command(path: str) -> str:
        return f"test -f {shlex.quote(path)}"
