from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from ..agent.message import PathRef
from .adb import BackendCommandResult, BackendTimeoutError

TERMUX_PREFIX = Path("/data/data/com.termux/files/usr")


class LocalBackend:
    """Execute commands in the local environment.

    When use_proot=False (default): commands run directly via bash.
    When use_proot=True: commands are wrapped in proot-distro login ubuntu.

    On-device mode: the controller runs in Termux. All file paths are local.
    push/pull/copy operations become simple local file copies.
    """

    def __init__(
        self,
        *,
        workdir: str | Path | None = None,
        python_bin: str | None = None,
        use_proot: bool = False,
    ) -> None:
        self.workdir = Path(workdir).expanduser().resolve() if workdir else None
        self.python_bin = python_bin or sys.executable or "python3"
        self.shell_bin = self._resolve_shell_bin()
        self.use_proot = use_proot

    def exec_ubuntu(
        self,
        command: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        del serial
        if self.use_proot:
            return self._run_proot(command, timeout_seconds=timeout_seconds)
        return self._run(
            [self.shell_bin, "-lc", command],
            env=self._run_env(),
            timeout_seconds=timeout_seconds,
        )

    def exec_ubuntu_python(
        self,
        code: str,
        *,
        serial: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        del serial
        if self.use_proot:
            escaped_code = code.replace("'", "'\\''")
            proot_cmd = (
                f"source /root/venvs/clawmobile/bin/activate 2>/dev/null; "
                f"python3 -c '{escaped_code}'"
            )
            return self._run_proot(proot_cmd, env=env, timeout_seconds=timeout_seconds)
        run_env = os.environ.copy()
        run_env.update(env or {})
        run_env = self._run_env(run_env)
        return self._run(
            [self.python_bin, "-c", code],
            env=run_env,
            timeout_seconds=timeout_seconds,
        )

    def ensure_ubuntu_dir(
        self,
        directory: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        del serial
        return self.exec_ubuntu(f"mkdir -p {shlex.quote(directory)}", timeout_seconds=timeout_seconds)

    def stat_ubuntu_path(
        self,
        path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        del serial
        return self.exec_ubuntu(f"test -f {shlex.quote(path)}", timeout_seconds=timeout_seconds)

    def stat_termux_path(
        self,
        path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        del serial
        # On-device, termux paths are local
        exists = Path(path).is_file()
        return BackendCommandResult(
            argv=("test", "-f", path),
            exit_code=0 if exists else 1,
            stdout="",
            stderr="" if exists else f"file not found: {path}",
        )

    def push_termux_home(
        self,
        local_path: str,
        remote_relative: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """On-device: local copy from source to Termux home."""
        del serial, timeout_seconds
        termux_home = os.environ.get("HOME", "/data/data/com.termux/files/home")
        dest = Path(termux_home) / remote_relative
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, str(dest))
            return BackendCommandResult(
                argv=("cp", local_path, str(dest)),
                exit_code=0,
                stdout=str(dest),
                stderr="",
                resolved_path=PathRef(layer="termux", path=str(dest)),
            )
        except Exception as e:
            return BackendCommandResult(
                argv=("cp", local_path, str(dest)),
                exit_code=1,
                stdout="",
                stderr=str(e),
            )

    def pull_termux_home(
        self,
        remote_relative: str,
        local_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """On-device: local copy from Termux home to destination."""
        del serial, timeout_seconds
        termux_home = os.environ.get("HOME", "/data/data/com.termux/files/home")
        src = Path(termux_home) / remote_relative
        try:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), local_path)
            return BackendCommandResult(
                argv=("cp", str(src), local_path),
                exit_code=0,
                stdout=local_path,
                stderr="",
                resolved_path=PathRef(layer="host", path=local_path),
            )
        except Exception as e:
            return BackendCommandResult(
                argv=("cp", str(src), local_path),
                exit_code=1,
                stdout="",
                stderr=str(e),
            )

    def copy_termux_to_ubuntu(
        self,
        source_path: str,
        dest_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """On-device: simple copy (termux and ubuntu are same filesystem)."""
        del serial
        return self.exec_ubuntu(
            f"mkdir -p $(dirname {shlex.quote(dest_path)}) && cp {shlex.quote(source_path)} {shlex.quote(dest_path)}",
            timeout_seconds=timeout_seconds,
        )

    def copy_ubuntu_to_termux(
        self,
        source_path: str,
        dest_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """On-device: simple copy."""
        del serial
        return self.exec_ubuntu(
            f"mkdir -p $(dirname {shlex.quote(dest_path)}) && cp {shlex.quote(source_path)} {shlex.quote(dest_path)}",
            timeout_seconds=timeout_seconds,
        )

    def copy_ubuntu_file(
        self,
        source_path: str,
        dest_path: str,
        *,
        serial: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """On-device: copy within ubuntu filesystem."""
        del serial
        return self.exec_ubuntu(
            f"mkdir -p $(dirname {shlex.quote(dest_path)}) && cp {shlex.quote(source_path)} {shlex.quote(dest_path)}",
            timeout_seconds=timeout_seconds,
        )

    def _run_proot(
        self,
        command: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        """Execute command inside proot Ubuntu from Termux context."""
        import base64
        wrapped = f"cd /root 2>/dev/null || true; {command}"
        payload_b64 = base64.b64encode(wrapped.encode()).decode()
        argv = [
            self.shell_bin, "-c",
            f"cd /data/data/com.termux/files/home 2>/dev/null || true; "
            f"echo {payload_b64} | base64 -d | "
            f"PROOT_NO_SECCOMP=1 proot-distro login ubuntu --termux-home -- bash"
        ]
        run_env = self._run_env()
        run_env.update(env or {})
        return self._run(argv, env=run_env, timeout_seconds=timeout_seconds)

    def _resolve_shell_bin(self) -> str:
        termux_bash = TERMUX_PREFIX / "bin" / "bash"
        if termux_bash.exists():
            return str(termux_bash)
        return shutil.which("bash") or "sh"

    def _run_env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        run_env = dict(base or os.environ)
        termux_bin = str(TERMUX_PREFIX / "bin")
        path = run_env.get("PATH", "")
        if Path(termux_bin).exists() and termux_bin not in path.split(":"):
            run_env["PATH"] = f"{termux_bin}:{path}" if path else termux_bin
        return run_env

    def _run(
        self,
        argv: list[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> BackendCommandResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=str(self.workdir) if self.workdir else None,
                env=dict(env) if env is not None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendTimeoutError(
                f"local command timed out after {timeout_seconds}s: {argv}"
            ) from exc
        return BackendCommandResult(
            argv=tuple(argv),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
