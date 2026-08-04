"""Atomic, branch-scoped evolution state."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FINAL_OUTCOMES = frozenset(
    {
        "deployed",
        "generation_failed",
        "no_changes",
        "rejected",
        "validation_failed",
        "write_failed",
        "verification_failed",
        "commit_failed",
        "deploy_failed",
    }
)

_LEGACY_OUTCOMES = {
    "deploying": "deployed",
    "success": "deployed",
    "gen_failed": "generation_failed",
    "health_failed": "verification_failed",
}


class StateError(RuntimeError):
    """Raised when persisted state is malformed or cannot be preserved."""


def fresh_state(branch: str) -> dict[str, Any]:
    return {"schema_version": 2, "generation": 0, "history": [], "branch": branch}


def load_state(log_dir: Path, branch: str) -> dict[str, Any]:
    """Load state for *branch*, archiving another branch's state first."""
    state_file = log_dir / "state.json"
    if not state_file.exists():
        return fresh_state(branch)

    try:
        with state_file.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Cannot read {state_file}: {exc}") from exc

    _validate_shape(state, state_file)
    stored_branch = str(state.get("branch", ""))
    if stored_branch != branch:
        _archive_state(log_dir, state, stored_branch or "unknown")
        return fresh_state(branch)

    normalized = fresh_state(branch)
    normalized["generation"] = int(state.get("generation", 0))
    normalized["history"] = [_normalize_entry(entry) for entry in state.get("history", [])]
    return normalized


def save_state(log_dir: Path, state: dict[str, Any]) -> None:
    """Atomically persist state and fsync the temporary file before replace."""
    _validate_shape(state, log_dir / "state.json")
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / "state.json"
    fd, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=log_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def record_outcome(
    state: dict[str, Any],
    generation: int,
    strategy: str,
    outcome: str,
    **metadata: Any,
) -> dict[str, Any]:
    """Record exactly one final outcome for a generation.

    Re-recording the same generation replaces the prior entry.  This prevents
    transitional values such as ``deploying`` from being counted as successes.
    """
    outcome = _LEGACY_OUTCOMES.get(outcome, outcome)
    if outcome not in FINAL_OUTCOMES:
        raise StateError(f"Unknown final outcome: {outcome}")
    if generation < 1:
        raise StateError("generation must be positive")

    entry: dict[str, Any] = {
        "generation": generation,
        "strategy": strategy,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    entry.update({key: value for key, value in metadata.items() if value is not None})

    history = [
        item for item in state.setdefault("history", [])
        if int(item.get("generation", -1)) != generation
    ]
    history.append(entry)
    history.sort(key=lambda item: int(item.get("generation", 0)))
    state["history"] = history[-200:]
    state["generation"] = max(int(state.get("generation", 0)), generation)
    state["schema_version"] = 2
    return entry


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    outcome = str(normalized.get("outcome", "generation_failed"))
    normalized["outcome"] = _LEGACY_OUTCOMES.get(outcome, outcome)
    return normalized


def _validate_shape(state: Any, source: Path) -> None:
    if not isinstance(state, dict):
        raise StateError(f"State in {source} must be a JSON object")
    if not isinstance(state.get("history", []), list):
        raise StateError(f"State history in {source} must be a list")
    try:
        generation = int(state.get("generation", 0))
    except (TypeError, ValueError) as exc:
        raise StateError(f"State generation in {source} must be an integer") from exc
    if generation < 0:
        raise StateError(f"State generation in {source} cannot be negative")


def _archive_state(log_dir: Path, state: dict[str, Any], branch: str) -> Path:
    states_dir = log_dir / "states"
    states_dir.mkdir(parents=True, exist_ok=True)
    safe_branch = re.sub(r"[^A-Za-z0-9_.-]+", "_", branch).strip("_") or "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive = states_dir / f"state_{safe_branch}_{timestamp}.json"
    save_state_to_path(archive, state)
    return archive


def save_state_to_path(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
