"""Compatibility entry points for the trusted evolution controller."""

from engine.controller import (
    extract_target_files as _extract_target_files,
    run,
    summarize_file as _summarize_file,
)

__all__ = ["run"]
