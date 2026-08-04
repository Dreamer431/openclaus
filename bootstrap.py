"""Immutable launcher for the trusted OpenClaus runtime.

Usage:
  python bootstrap.py
  python bootstrap.py --dry-run
  python bootstrap.py --replace PID --token TOKEN --generation N
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
READY_MARKER = PROJECT_ROOT / ".evolve_ready"
ACK_MARKER = PROJECT_ROOT / ".evolve_ack"
ACTIVE_MARKER = PROJECT_ROOT / ".evolve_active"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_config() -> dict:
    import yaml

    config_path = PROJECT_ROOT / "config.yaml"
    example_path = PROJECT_ROOT / "config.example.yaml"
    if not config_path.exists():
        if example_path.exists():
            shutil.copy(example_path, config_path)
            print("[BOOTSTRAP] Created config.yaml from config.example.yaml")
            print("[BOOTSTRAP] Set the Gemini API key and restart.")
            raise SystemExit(0)
        raise SystemExit("[BOOTSTRAP] config.yaml is missing")

    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise SystemExit("[BOOTSTRAP] config.yaml must contain a mapping")
    api_key = str(config.get("gemini", {}).get("api_key", ""))
    if not api_key or api_key.startswith("YOUR_"):
        raise SystemExit("[BOOTSTRAP] Set a valid Gemini API key in config.yaml")
    return config


def ensure_git_repo() -> None:
    if (PROJECT_ROOT / ".git").exists():
        return
    print("[BOOTSTRAP] Initializing git repository...")
    subprocess.run(["git", "init"], cwd=PROJECT_ROOT, check=True)
    subprocess.run(["git", "add", "."], cwd=PROJECT_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", "gen-0: initial code"],
        cwd=PROJECT_ROOT,
        check=True,
    )


def run_verification() -> bool:
    from engine.verifier import run_checks

    return run_checks(PROJECT_ROOT).ok


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaus trusted launcher")
    parser.add_argument("--dry-run", action="store_true", help="verify without evolving")
    parser.add_argument("--replace", type=int, metavar="PID", help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    parser.add_argument("--generation", type=int, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def replacement_start(parent_pid: int, token: str, generation: int) -> None:
    from engine.controller import run
    from engine.runtime import pid_is_alive, read_ready_marker, write_json_atomic

    print(f"[BOOTSTRAP] Validating replacement for generation {generation}")
    if not run_verification():
        raise SystemExit("[BOOTSTRAP] Replacement verification failed")

    write_json_atomic(
        READY_MARKER,
        {
            "token": token,
            "generation": generation,
            "pid": os.getpid(),
        },
    )
    deadline = time.monotonic() + 30
    acknowledged = False
    while pid_is_alive(parent_pid) and time.monotonic() < deadline:
        ack = read_ready_marker(ACK_MARKER, token, generation)
        if ack and ack.get("pid") == parent_pid:
            acknowledged = True
            break
        time.sleep(0.1)
    if not acknowledged:
        READY_MARKER.unlink(missing_ok=True)
        raise SystemExit("[BOOTSTRAP] Parent did not acknowledge the handoff")

    write_json_atomic(
        ACTIVE_MARKER,
        {"token": token, "generation": generation, "pid": os.getpid()},
    )
    deadline = time.monotonic() + 30
    while pid_is_alive(parent_pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if pid_is_alive(parent_pid):
        READY_MARKER.unlink(missing_ok=True)
        raise SystemExit("[BOOTSTRAP] Parent did not exit after handoff")

    config = load_config()
    run(config)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.replace is not None:
        if not args.token or args.generation is None or args.generation < 1:
            raise SystemExit("[BOOTSTRAP] Invalid replacement handshake")
        if args.dry_run:
            raise SystemExit("[BOOTSTRAP] --dry-run cannot be combined with --replace")
        replacement_start(args.replace, args.token, args.generation)
        return 0

    print("[BOOTSTRAP] OpenClaus trusted runtime starting...")
    ensure_git_repo()
    if args.dry_run:
        ok = run_verification()
        print(f"[BOOTSTRAP] Verification: {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1

    from engine.controller import run
    from engine.runtime import SafetyError

    try:
        run(load_config())
    except SafetyError as exc:
        print(f"[BOOTSTRAP] SAFETY STOP: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
