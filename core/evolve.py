"""
core/evolve.py - The evolution loop orchestrator.
Entry point: run(config) - called by bootstrap.py.
This signature must never change.
"""

import os
import sys
import subprocess
import time
import json
from datetime import datetime

from core import strategy as strategy_mod
from core import codemod
from core import health
from core.brain import Brain


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(PROJECT_ROOT, "core")
BACKUP_DIR = os.path.join(PROJECT_ROOT, "backups")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
EVOLVE_MARKER = os.path.join(PROJECT_ROOT, ".evolve_ready")
STATE_FILE = os.path.join(LOG_DIR, "state.json")


def run(config: dict) -> None:
    """
    Main evolution loop. Called by bootstrap.py.
    Each iteration = one generation of self-improvement.
    """
    gemini_cfg = config["gemini"]
    evo_cfg = config["evolution"]
    safety_cfg = config["safety"]

    brain = Brain(api_key=gemini_cfg["api_key"], model=gemini_cfg["model"])
    state = _load_state()
    generation = state.get("generation", 0)
    history = state.get("history", [])
    max_generations = evo_cfg.get("max_generations", 50)

    _log(f"OpenClaus starting at generation {generation}")

    while generation < max_generations:
        generation += 1
        _log(f"\n{'='*50}")
        _log(f"GENERATION {generation}")
        _log(f"{'='*50}")

        # Step 1: Read current evolvable source files
        current_files = codemod.read_evolvable_files(CORE_DIR)
        _log(f"Read {len(current_files)} source files")

        # Step 2: Choose strategy for this generation
        chosen_strategy = strategy_mod.get_strategy(generation, history)
        _log(f"Strategy: {chosen_strategy}")

        # Step 3: Ask Gemini to generate improvements
        _log("Generating improvements with Gemini...")
        try:
            proposed_files = brain.generate_improvement(current_files, chosen_strategy)
        except Exception as e:
            _log(f"Code generation failed: {e}. Skipping generation.")
            time.sleep(evo_cfg.get("delay_between_generations", 5))
            continue

        if not proposed_files:
            _log("Gemini returned no changes. Skipping generation.")
            _append_history(history, generation, chosen_strategy, "no_changes")
            _save_state(generation, history)
            time.sleep(evo_cfg.get("delay_between_generations", 5))
            continue

        _log(f"Gemini proposed changes to: {list(proposed_files.keys())}")

        # Step 4: Self-review the proposed changes
        _log("Running self-review...")
        try:
            approved, reasoning = brain.review_changes(
                current_files, proposed_files, chosen_strategy
            )
        except Exception as e:
            _log(f"Review failed: {e}. Skipping generation.")
            time.sleep(evo_cfg.get("delay_between_generations", 5))
            continue

        _log(f"Review result: {'APPROVED' if approved else 'REJECTED'}")
        _log(f"Reasoning: {reasoning[:200]}...")

        if not approved:
            _append_history(history, generation, chosen_strategy, "rejected")
            _save_state(generation, history)
            time.sleep(evo_cfg.get("delay_between_generations", 5))
            continue

        # Step 5: Backup current core/
        backup_path = codemod.backup_current(CORE_DIR, BACKUP_DIR, generation)
        _log(f"Backup saved to {backup_path}")
        codemod.cleanup_old_backups(BACKUP_DIR, safety_cfg.get("max_backups", 20))

        # Step 6: Write proposed files to disk
        try:
            codemod.write_files(CORE_DIR, proposed_files)
            _log("Changes written to disk")
        except Exception as e:
            _log(f"Write failed: {e}. Rolling back.")
            codemod.restore_backup(backup_path, CORE_DIR)
            _append_history(history, generation, chosen_strategy, "write_failed")
            _save_state(generation, history)
            continue

        # Step 7: Health check the new code
        _log("Running health checks...")
        if not health.run_checks():
            _log("Health checks FAILED. Rolling back.")
            codemod.restore_backup(backup_path, CORE_DIR)
            _append_history(history, generation, chosen_strategy, "health_failed")
            _save_state(generation, history)
            continue

        _log("Health checks PASSED")

        # Step 8: Git commit
        commit_msg = f"gen-{generation}: {chosen_strategy[:60]}"
        if codemod.git_commit(PROJECT_ROOT, commit_msg):
            _log(f"Git commit: {commit_msg}")
        else:
            _log("Git commit failed (continuing anyway)")

        # Step 9: Save state before hot deploy
        _append_history(history, generation, chosen_strategy, "deploying")
        _save_state(generation, history)

        # Step 10: Hot deploy - spawn new process with updated code
        _log("Initiating hot deploy...")
        _perform_hot_deploy(
            generation=generation,
            backup_path=backup_path,
            project_root=PROJECT_ROOT,
            timeout=evo_cfg.get("hot_deploy_timeout", 30),
        )

        # If we reach here, hot deploy failed and we rolled back
        _log("Hot deploy failed. Continuing with rolled-back code.")
        _append_history(history, generation, chosen_strategy, "deploy_failed")
        _save_state(generation, history)
        time.sleep(evo_cfg.get("delay_between_generations", 5))

    _log(f"Reached max_generations ({max_generations}). Stopping.")


def _perform_hot_deploy(
    generation: int, backup_path: str, project_root: str, timeout: int
) -> None:
    """
    Spawn a new process with the updated code.
    If successful, exit this process (the new one takes over).
    If failed, roll back and return.
    """
    # Clean up stale marker
    if os.path.exists(EVOLVE_MARKER):
        os.remove(EVOLVE_MARKER)

    bootstrap = os.path.join(project_root, "bootstrap.py")
    child = subprocess.Popen(
        [sys.executable, bootstrap, "--replace", str(os.getpid())],
        cwd=project_root,
    )
    _log(f"Spawned new process PID {child.pid}")

    # Poll for success marker
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(EVOLVE_MARKER):
            os.remove(EVOLVE_MARKER)
            _log(f"[GEN-{generation}] New version is healthy. Exiting old process.")
            sys.exit(0)
        if child.poll() is not None:
            _log(f"Child process exited with code {child.returncode}")
            break
        time.sleep(0.5)
    else:
        _log(f"Hot deploy timed out after {timeout}s")

    # Hot deploy failed - clean up
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()

    # Roll back files and git
    _log("Rolling back to previous version...")
    codemod.restore_backup(backup_path, CORE_DIR)
    codemod.git_rollback(project_root)


def _get_current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _load_state() -> dict:
    current_branch = _get_current_branch()
    fresh = {"generation": 0, "history": [], "branch": current_branch}

    if not os.path.exists(STATE_FILE):
        return fresh

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    stored_branch = state.get("branch", "")
    if stored_branch and stored_branch != current_branch:
        # Archive old branch's state, start fresh for this branch
        safe_name = stored_branch.replace("/", "_").replace("-", "_").replace(".", "_")
        archive_path = os.path.join(LOG_DIR, f"state_{safe_name}.json")
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        _log(f"Archived state for '{stored_branch}' -> {os.path.basename(archive_path)}")
        return fresh

    state["branch"] = current_branch
    return state


def _save_state(generation: int, history: list) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    branch = _get_current_branch()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"generation": generation, "history": history, "branch": branch}, f, indent=2)


def _append_history(history: list, generation: int, strategy: str, outcome: str) -> None:
    history.append({
        "generation": generation,
        "strategy": strategy,
        "outcome": outcome,
        "timestamp": datetime.now().isoformat(),
    })
    # Keep history bounded
    if len(history) > 200:
        history[:] = history[-200:]


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}"
    print(line)
    # Also append to log file
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "evolution.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")
