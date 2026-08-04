"""Process and Git safety primitives for the trusted controller."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


class SafetyError(RuntimeError):
    """Raised when an evolution run would be unsafe."""


def run_git(project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SafetyError(f"git {' '.join(args)} failed: {detail}")
    return result


def current_branch(project_root: Path) -> str:
    return run_git(project_root, "branch", "--show-current").stdout.strip()


def current_head(project_root: Path) -> str:
    return run_git(project_root, "rev-parse", "HEAD").stdout.strip()


def validate_branch_name(branch: str) -> None:
    if not branch.startswith("evolve/") or len(branch) <= len("evolve/"):
        raise SafetyError(
            f"Refusing to evolve branch '{branch or '(detached HEAD)'}'. "
            "Create or switch to an evolve/<seed> branch first."
        )


def ensure_clean_worktree(project_root: Path) -> None:
    result = run_git(project_root, "status", "--porcelain", "--untracked-files=normal")
    if result.stdout.strip():
        preview = "\n".join(result.stdout.strip().splitlines()[:20])
        raise SafetyError(f"Refusing to evolve a dirty worktree:\n{preview}")


def ensure_safe_context(project_root: Path) -> tuple[str, str]:
    branch = current_branch(project_root)
    validate_branch_name(branch)
    ensure_clean_worktree(project_root)
    return branch, current_head(project_root)


def commit_core(project_root: Path, message: str, expected_head: str) -> str:
    if current_head(project_root) != expected_head:
        raise SafetyError("HEAD changed while preparing the candidate; refusing to commit")
    run_git(project_root, "add", "--", "core/")
    staged = run_git(project_root, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        raise SafetyError("Candidate produced no staged changes")
    if staged.returncode != 1:
        raise SafetyError("Unable to inspect staged candidate changes")
    run_git(project_root, "commit", "-m", message)
    return current_head(project_root)


def unstage_core(project_root: Path) -> None:
    run_git(project_root, "restore", "--staged", "--", "core/", check=False)


def revert_candidate(project_root: Path, candidate_commit: str) -> str:
    """Revert a failed candidate without rewriting branch history."""
    if current_head(project_root) != candidate_commit:
        raise SafetyError(
            "HEAD moved after the candidate commit; refusing automatic rollback"
        )
    ensure_clean_worktree(project_root)
    run_git(project_root, "revert", "--no-edit", candidate_commit)
    return current_head(project_root)


class EvolutionLock:
    """Cross-process single-instance lock with stale-PID recovery."""

    def __init__(self, project_root: Path, timeout: float = 0.0):
        self.path = project_root / ".openclaus.lock"
        self.timeout = max(0.0, timeout)
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self) -> "EvolutionLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = json.dumps({"pid": os.getpid(), "token": self.token})
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                self.acquired = True
                return self
            except FileExistsError:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise SafetyError(f"Another OpenClaus process holds {self.path.name}")
                time.sleep(0.25)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        self.acquired = False

    def _remove_stale_lock(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", -1))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                if time.time() - self.path.stat().st_mtime < 2.0:
                    return False
            except OSError:
                return True
            pid = -1
        if pid > 0 and pid_is_alive(pid):
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except ProcessLookupError:
            return False

    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, pid
    )
    if not handle:
        return False
    try:
        wait_timeout = 0x00000102
        result = kernel32.WaitForSingleObject(handle, 0)
        return result == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_ready_marker(path: Path, token: str, generation: int) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if payload.get("token") != token or payload.get("generation") != generation:
        return None
    try:
        payload["pid"] = int(payload["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    return payload
