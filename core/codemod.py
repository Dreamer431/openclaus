"""Compatibility wrappers for trusted candidate operations."""

from __future__ import annotations

from pathlib import Path

from engine.candidate import (
    cleanup_old_backups as _cleanup_old_backups,
    parse_model_response,
    read_evolvable_files as _read_evolvable_files,
    restore_backup as _restore_backup,
    validate_changes,
    write_files as _write_files,
)


parse_gemini_response = parse_model_response


def read_evolvable_files(core_dir: str) -> dict[str, str]:
    return _read_evolvable_files(Path(core_dir).resolve().parent)


def write_files(core_dir: str, file_contents: dict[str, str]) -> None:
    _write_files(Path(core_dir).resolve().parent, file_contents)


def restore_backup(backup_path: str, core_dir: str) -> None:
    _restore_backup(Path(core_dir).resolve().parent, Path(backup_path))


def cleanup_old_backups(backup_dir: str, max_backups: int) -> None:
    _cleanup_old_backups(Path(backup_dir).resolve().parent, max_backups)


__all__ = [
    "cleanup_old_backups",
    "parse_gemini_response",
    "read_evolvable_files",
    "restore_backup",
    "validate_changes",
    "write_files",
]
