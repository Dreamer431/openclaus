"""
bootstrap.py - Immutable launcher for OpenClaus.

DO NOT modify this file - it is the stable foundation that the evolution
engine can never touch. All evolvable code lives in core/.

Usage:
  python bootstrap.py               # Normal startup
  python bootstrap.py --dry-run     # Analyze only, no modifications
  python bootstrap.py --replace <pid>  # Hot deploy: new version replacing old PID
"""

import os
import sys
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
EVOLVE_MARKER = os.path.join(PROJECT_ROOT, ".evolve_ready")

# Add project root to path so 'core' package is importable
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_config() -> dict:
    import yaml
    config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    example_path = os.path.join(PROJECT_ROOT, "config.example.yaml")

    if not os.path.exists(config_path):
        if os.path.exists(example_path):
            import shutil
            shutil.copy(example_path, config_path)
            print(f"[BOOTSTRAP] Created config.yaml from config.example.yaml")
            print(f"[BOOTSTRAP] Please set your API key in config.yaml and restart.")
            sys.exit(0)
        else:
            print("[BOOTSTRAP] ERROR: config.yaml not found. Create it from config.example.yaml.")
            sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if cfg.get("gemini", {}).get("api_key", "").startswith("YOUR_"):
        print("[BOOTSTRAP] ERROR: Please set your Gemini API key in config.yaml.")
        sys.exit(1)

    return cfg


def ensure_git_repo() -> None:
    """Initialize git repo on first run if needed."""
    git_dir = os.path.join(PROJECT_ROOT, ".git")
    if not os.path.exists(git_dir):
        print("[BOOTSTRAP] Initializing git repository...")
        subprocess.run(["git", "init"], cwd=PROJECT_ROOT, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=PROJECT_ROOT, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "gen-0: initial code"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
        print("[BOOTSTRAP] Initial git commit created.")


def run_health_checks() -> bool:
    """Import core.health and run all health checks."""
    try:
        from core.health import run_checks
        return run_checks()
    except Exception as e:
        print(f"[BOOTSTRAP] Health check import failed: {e}")
        return False


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    is_replacement = "--replace" in sys.argv

    if is_replacement:
        # We are the NEW version spawned by the old process during hot deploy.
        parent_pid = int(sys.argv[sys.argv.index("--replace") + 1])
        print(f"[BOOTSTRAP] Hot deploy mode: replacing process {parent_pid}")

        if not run_health_checks():
            print("[BOOTSTRAP] Health checks FAILED. New version aborting.")
            sys.exit(1)

        # Signal success to the parent by writing the marker file
        print("[BOOTSTRAP] Health checks PASSED. Signaling parent to exit.")
        with open(EVOLVE_MARKER, "w") as f:
            f.write("ok")

        # Give parent a moment to see the marker and exit
        import time
        time.sleep(2)

        # Now run as the new generation
        config = load_config()
        if dry_run:
            print("[BOOTSTRAP] Dry-run mode: not starting evolution loop.")
            return
        from core.evolve import run
        run(config)

    else:
        # Normal startup
        print("[BOOTSTRAP] OpenClaus starting...")
        try:
            ensure_git_repo()
        except Exception as e:
            print(f"[BOOTSTRAP] Warning: git setup failed: {e}")

        config = load_config()

        if dry_run:
            print("[BOOTSTRAP] Dry-run mode: running health checks only.")
            ok = run_health_checks()
            print(f"[BOOTSTRAP] Health: {'OK' if ok else 'FAILED'}")
            return

        from core.evolve import run
        run(config)


if __name__ == "__main__":
    main()
