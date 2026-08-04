"""Compatibility health-check entry point backed by the trusted verifier."""

from pathlib import Path

from engine.verifier import run_checks as _run_checks


def run_checks() -> bool:
    project_root = Path(__file__).resolve().parent.parent
    return _run_checks(project_root).ok


__all__ = ["run_checks"]
