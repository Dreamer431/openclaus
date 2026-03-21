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
    Uses a line-by-line state machine instead of regex to correctly handle
    code that contains triple-quoted strings or markdown fences internally.

    Supported file header formats:
        ### FILE: core/filename.py
        ## core/filename.py
        **core/filename.py**
        `core/filename.py`
    """
    _HEADER_PATTERNS = [
        re.compile(r"###?\s+FILE:\s*(core/[\w./]+\.py)"),
        re.compile(r"###?\s+(core/[\w./]+\.py)"),
        re.compile(r"\*\*(core/[\w./]+\.py)\*\*"),
        re.compile(r"`(core/[\w./]+\.py)`"),
    ]

    result = {}
    lines = response_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        current_file = None
        for pat in _HEADER_PATTERNS:
            m = pat.search(line)
            if m:
                current_file = m.group(1).strip()
                break

        if current_file:
            i += 1
            # Scan forward for the opening code fence
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                i += 1

            if i >= len(lines):
                break

            i += 1  # skip opening fence
            code_lines = []

            # Collect until a closing fence at column 0
            while i < len(lines):
                if lines[i].rstrip() == "```":
                    break
                code_lines.append(lines[i])
                i += 1

            content = "\n".join(code_lines)
            if not content.strip():
                i += 1
                continue

            try:
                ast.parse(content)
                result[current_file] = content
                print(f"[CODEMOD] Parsed {current_file} ({len(code_lines)} lines)")
            except SyntaxError as e:
                print(f"[CODEMOD] Skipping {current_file}: syntax error - {e}")

        i += 1

    return result
