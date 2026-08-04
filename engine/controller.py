"""Trusted evolution controller.

The model may change policy files under ``core/``.  This module owns writes,
verification, state, Git operations, and process handoff, so candidates cannot
approve or deploy themselves.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from core import strategy as strategy_module
from engine import candidate, runtime, state as state_store, verifier
from engine.model_client import Brain


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
READY_MARKER = PROJECT_ROOT / ".evolve_ready"
ACK_MARKER = PROJECT_ROOT / ".evolve_ack"
ACTIVE_MARKER = PROJECT_ROOT / ".evolve_active"


def run(config: dict) -> None:
    """Run autonomous policy evolution on a clean ``evolve/*`` branch."""
    branch, _head = runtime.ensure_safe_context(PROJECT_ROOT)
    with runtime.EvolutionLock(PROJECT_ROOT):
        _run_locked(config, branch)


def _run_locked(config: dict, branch: str) -> None:
    gemini_config = config["gemini"]
    evolution_config = config["evolution"]
    safety_config = config["safety"]
    brain = Brain(
        api_key=gemini_config["api_key"],
        model=gemini_config["model"],
        max_retries=evolution_config.get("api_max_retries", 2),
    )
    try:
        state = state_store.load_state(LOG_DIR, branch)
        history = state["history"]
        generation = int(state["generation"])
        maximum = int(evolution_config.get("max_generations", 50))
        delay = max(0, float(evolution_config.get("delay_between_generations", 5)))
        _log(f"Trusted controller starting on {branch} at generation {generation}")

        while generation < maximum:
            generation += 1
            started = time.monotonic()
            _log(f"GENERATION {generation}")
            original = candidate.read_evolvable_files(PROJECT_ROOT)
            chosen_strategy = strategy_module.get_strategy(generation, history)
            context = extract_target_files(chosen_strategy, original)
            _log(f"Strategy: {chosen_strategy}")

            try:
                proposed = brain.generate_improvement(context, chosen_strategy, history)
            except Exception as exc:
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "generation_failed",
                    started,
                    error=f"{type(exc).__name__}: {exc}",
                )
                _pause(delay)
                continue

            if not proposed:
                _finish(state, generation, chosen_strategy, "no_changes", started)
                _pause(delay)
                continue

            try:
                approved, reasoning = brain.review_changes(original, proposed, chosen_strategy)
            except Exception as exc:
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "generation_failed",
                    started,
                    error=f"Review failed: {type(exc).__name__}: {exc}",
                )
                _pause(delay)
                continue
            if not approved:
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "rejected",
                    started,
                    review=reasoning[:1000],
                )
                _pause(delay)
                continue
            if not candidate.validate_changes(original, proposed):
                _finish(state, generation, chosen_strategy, "validation_failed", started)
                _pause(delay)
                continue

            expected_head = runtime.current_head(PROJECT_ROOT)
            backup_path = candidate.backup_current(PROJECT_ROOT, generation)
            candidate.cleanup_old_backups(
                PROJECT_ROOT, int(safety_config.get("max_backups", 20))
            )
            try:
                candidate.write_files(PROJECT_ROOT, proposed)
            except Exception as exc:
                candidate.restore_backup(PROJECT_ROOT, backup_path)
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "write_failed",
                    started,
                    error=f"{type(exc).__name__}: {exc}",
                )
                _pause(delay)
                continue

            verification = verifier.run_checks(
                PROJECT_ROOT,
                timeout=int(safety_config.get("test_timeout", 60)),
            )
            if not verification.ok:
                candidate.restore_backup(PROJECT_ROOT, backup_path)
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "verification_failed",
                    started,
                    changed_files=sorted(proposed),
                )
                _pause(delay)
                continue

            try:
                commit = runtime.commit_core(
                    PROJECT_ROOT,
                    f"gen-{generation}: {chosen_strategy[:60]}",
                    expected_head,
                )
            except Exception as exc:
                runtime.unstage_core(PROJECT_ROOT)
                candidate.restore_backup(PROJECT_ROOT, backup_path)
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "commit_failed",
                    started,
                    error=f"{type(exc).__name__}: {exc}",
                )
                _pause(delay)
                continue

            def mark_deployed() -> None:
                _finish(
                    state,
                    generation,
                    chosen_strategy,
                    "deployed",
                    started,
                    commit=commit,
                    changed_files=sorted(proposed),
                    review=reasoning[:1000],
                )

            deployed, detail = perform_hot_deploy(
                generation=generation,
                timeout=int(evolution_config.get("hot_deploy_timeout", 30)),
                on_ready=mark_deployed,
            )
            if deployed:
                return

            rollback_commit = runtime.revert_candidate(PROJECT_ROOT, commit)
            _finish(
                state,
                generation,
                chosen_strategy,
                "deploy_failed",
                started,
                commit=commit,
                rollback_commit=rollback_commit,
                error=detail,
                changed_files=sorted(proposed),
            )
            _pause(delay)

        _log(f"Reached max_generations ({maximum}). Stopping.")
    finally:
        brain.close()


def perform_hot_deploy(generation: int, timeout: int, on_ready) -> tuple[bool, str]:
    for marker_path in (READY_MARKER, ACK_MARKER, ACTIVE_MARKER):
        marker_path.unlink(missing_ok=True)
    token = uuid.uuid4().hex
    bootstrap = PROJECT_ROOT / "bootstrap.py"
    child = subprocess.Popen(
        [
            sys.executable,
            str(bootstrap),
            "--replace",
            str(os.getpid()),
            "--token",
            token,
            "--generation",
            str(generation),
        ],
        cwd=PROJECT_ROOT,
    )
    deadline = time.monotonic() + max(5, timeout)
    while time.monotonic() < deadline:
        marker = runtime.read_ready_marker(READY_MARKER, token, generation)
        if marker and marker.get("pid") == child.pid:
            try:
                on_ready()
                runtime.write_json_atomic(
                    ACK_MARKER,
                    {"token": token, "generation": generation, "pid": os.getpid()},
                )
            except Exception as exc:
                _terminate_child(child)
                _cleanup_handoff_markers()
                return False, f"failed to persist handoff: {type(exc).__name__}: {exc}"

            while time.monotonic() < deadline:
                active = runtime.read_ready_marker(ACTIVE_MARKER, token, generation)
                if active and active.get("pid") == child.pid:
                    _cleanup_handoff_markers()
                    return True, f"replacement process {child.pid} is active"
                if child.poll() is not None:
                    _cleanup_handoff_markers()
                    return False, f"replacement process exited before activation: {child.returncode}"
                time.sleep(0.1)
            break
        if child.poll() is not None:
            return False, f"replacement process exited with code {child.returncode}"
        time.sleep(0.25)

    _terminate_child(child)
    _cleanup_handoff_markers()
    return False, f"replacement process did not become ready within {timeout}s"


def _terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _cleanup_handoff_markers() -> None:
    for marker_path in (READY_MARKER, ACK_MARKER, ACTIVE_MARKER):
        marker_path.unlink(missing_ok=True)


def extract_target_files(strategy: str, all_files: dict[str, str]) -> dict[str, str]:
    targets = {
        match.replace("\\", "/")
        for match in re.findall(r"core/[\w./-]+\.py", strategy)
        if match.replace("\\", "/") in all_files
    }
    if not targets:
        targets.update(_files_for_named_symbols(strategy, all_files))
    if not targets:
        return all_files
    return {
        path: content if path in targets else summarize_file(content)
        for path, content in all_files.items()
    }


def summarize_file(content: str) -> str:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    source_lines = content.splitlines()
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = ast.get_source_segment(content, node)
            if segment:
                lines.append(segment)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.extend((source_lines[node.lineno - 1], "    ..."))
        elif isinstance(node, ast.ClassDef):
            lines.append(f"class {node.name}:")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.extend(("    " + source_lines[item.lineno - 1].lstrip(), "        ..."))
    return "\n".join(lines)


def _files_for_named_symbols(strategy: str, all_files: dict[str, str]) -> set[str]:
    words = set(re.findall(r"\b[A-Za-z_]\w*\b", strategy))
    matches: set[str] = set()
    for path, content in all_files.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.name.startswith("_")
            and node.name in words
            for node in tree.body
        ):
            matches.add(path)
    return matches


def _finish(
    state: dict,
    generation: int,
    strategy: str,
    outcome: str,
    started: float,
    **metadata: object,
) -> None:
    duration = round(time.monotonic() - started, 3)
    state_store.record_outcome(
        state,
        generation,
        strategy,
        outcome,
        duration_seconds=duration,
        **metadata,
    )
    state_store.save_state(LOG_DIR, state)
    _log(f"Generation {generation} finished: {outcome} ({duration:.3f}s)")


def _pause(delay: float) -> None:
    if delay:
        time.sleep(delay)


def _log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "evolution.log").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
