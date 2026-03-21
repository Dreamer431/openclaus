"""
core/codemod.py - File I/O, backup, restore, and git operations.
Enforces the protected-files boundary: never writes outside core/.
"""

import ast
import os
import re
import shutil
import subprocess
from datetime import datetime

PROTECTED_FILES = {"bootstrap.py", "config.yaml", "requirements.txt"}


def read_evolvable_files(core_dir: str) -> dict[str, str]:
    """Read all .py files in core/, return {relative_path: contents}."""
    result = {}
    for fname in sorted(os.listdir(core_dir)):
        if fname.endswith(".py"):
            rel_path = f"core/{fname}"
            abs_path = os.path.join(core_dir, fname)
            with open(abs_path, "r", encoding="utf-8") as f:
                result[rel_path] = f.read()
    return result


def write_files(core_dir: str, file_contents: dict[str, str]) -> None:
    """Write modified files to core/. Refuses protected paths."""
    for rel_path, content in file_contents.items():
        # Safety: reject protected files and path traversal
        basename = os.path.basename(rel_path)
        if basename in PROTECTED_FILES:
            raise ValueError(f"Refusing to write protected file: {rel_path}")
        if ".." in rel_path or not rel_path.startswith("core/"):
            raise ValueError(f"Refusing to write outside core/: {rel_path}")

        abs_path = os.path.join(core_dir, basename)
        tmp_path = abs_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, abs_path)


def backup_current(core_dir: str, backup_dir: str, generation: int) -> str:
    """Copy core/ to backups/gen_NNN_TIMESTAMP/. Return backup path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"gen_{generation:04d}_{timestamp}"
    backup_path = os.path.join(backup_dir, backup_name)
    shutil.copytree(core_dir, backup_path)
    return backup_path


def restore_backup(backup_path: str, core_dir: str) -> None:
    """Restore core/ from a backup."""
    for fname in os.listdir(backup_path):
        src = os.path.join(backup_path, fname)
        dst = os.path.join(core_dir, fname)
        shutil.copy2(src, dst)


def cleanup_old_backups(backup_dir: str, max_backups: int) -> None:
    """Delete oldest backups if over the limit."""
    entries = sorted(
        [e for e in os.listdir(backup_dir) if e.startswith("gen_")]
    )
    while len(entries) > max_backups:
        oldest = entries.pop(0)
        shutil.rmtree(os.path.join(backup_dir, oldest))


def git_commit(project_root: str, message: str) -> bool:
    """Stage core/ and create a git commit. Return True on success."""
    try:
        subprocess.run(
            ["git", "add", "core/"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=project_root,
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def git_rollback(project_root: str) -> bool:
    """Roll back the last commit. Return True on success."""
    try:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def parse_gemini_response(response_text: str) -> dict[str, str]:
    """
    Parse Gemini's response into {rel_path: file_contents}.
    Expects format:
        ### FILE: core/filename.py
        ```python
        ...code...
        ```
    """
    result = {}
    pattern = re.compile(
        r"### FILE:\s*(core/\S+\.py)\s*\n```(?:python)?\n(.*?)```",
        re.DOTALL,
    )
    for match in pattern.finditer(response_text):
        rel_path = match.group(1).strip()
        content = match.group(2)
        # Basic validation: must be parseable Python
        try:
            ast.parse(content)
            result[rel_path] = content
        except SyntaxError as e:
            print(f"[CODEMOD] Skipping {rel_path}: syntax error - {e}")
    return result
