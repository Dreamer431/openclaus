"""Run the complete local OpenClaus verification gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str]) -> None:
    print(f"> {' '.join(command)}", flush=True)
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def main() -> int:
    run([sys.executable, "bootstrap.py", "--dry-run"])
    run(["git", "diff", "--check"])
    print("OpenClaus verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
