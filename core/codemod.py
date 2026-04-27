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
    """Read all .py files in core/ recursively, return {relative_path: contents}."""
    project_root = os.path.dirname(core_dir)
    result = {}
    for root, _dirs, files in os.walk(core_dir):
        for fname in sorted(files):
            if fname.endswith(".py"):
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, project_root).replace("\\", "/")
                with open(abs_path, "r", encoding="utf-8") as f:
                    result[rel_path] = f.read()
    return result


def write_files(core_dir: str, file_contents: dict[str, str]) -> None:
    """Write modified files to core/ (including subdirs). Refuses protected paths."""
    project_root = os.path.dirname(core_dir)
    for rel_path, content in file_contents.items():
        # Safety: reject protected files and path traversal
        basename = os.path.basename(rel_path)
        if basename in PROTECTED_FILES:
            raise ValueError(f"Refusing to write protected file: {rel_path}")
        if ".." in rel_path or not rel_path.startswith("core/"):
            raise ValueError(f"Refusing to write outside core/: {rel_path}")

        abs_path = os.path.join(project_root, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        tmp_path = abs_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, abs_path)


def validate_changes(original: dict[str, str], proposed: dict[str, str]) -> bool:
    """
    Verify that proposed changes do not break existing top-level function signatures.
    Checks names, argument structure, and type annotations.
    Returns True if valid, False otherwise.
    """
    for rel_path, prop_content in proposed.items():
        if rel_path not in original:
            continue

        try:
            orig_tree = ast.parse(original[rel_path])
            prop_tree = ast.parse(prop_content)
        except Exception as e:
            print(f"[VALIDATE] Syntax error in proposed {rel_path}: {e}")
            return False

        def get_functions(tree):
            return {
                n.name: n
                for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

        orig_funcs = get_functions(orig_tree)
        prop_funcs = get_functions(prop_tree)

        for name, o_node in orig_funcs.items():
            if name.startswith("_"):
                continue  # private helpers may be freely refactored
            if name not in prop_funcs:
                print(f"[VALIDATE] Missing function {name} in {rel_path}")
                return False

            p_node = prop_funcs[name]

            # Ensure sync/async status is preserved
            if type(o_node) is not type(p_node):
                print(f"[VALIDATE] Sync/Async mismatch in {rel_path}:{name}")
                return False

            # Check return annotation
            o_ret = ast.dump(o_node.returns) if o_node.returns else None
            p_ret = ast.dump(p_node.returns) if p_node.returns else None
            if o_ret != p_ret:
                print(f"[VALIDATE] Return annotation mismatch in {rel_path}:{name}")
                return False

            o_args = o_node.args
            p_args = p_node.args

            def extract_arg_data(args_obj):
                def ann(node):
                    return ast.dump(node) if node else None

                return {
                    "posonly": [(a.arg, ann(a.annotation)) for a in getattr(args_obj, "posonlyargs", [])],
                    "args": [(a.arg, ann(a.annotation)) for a in args_obj.args],
                    "vararg": (args_obj.vararg.arg, ann(args_obj.vararg.annotation)) if args_obj.vararg else None,
                    "kwonly": [(a.arg, ann(a.annotation)) for a in args_obj.kwonlyargs],
                    "kwarg": (args_obj.kwarg.arg, ann(args_obj.kwarg.annotation)) if args_obj.kwarg else None,
                    "defaults_count": len(args_obj.defaults),
                    "kw_defaults_count": len([d for d in args_obj.kw_defaults if d is not None]),
                }

            if extract_arg_data(o_args) != extract_arg_data(p_args):
                print(f"[VALIDATE] Signature mismatch in {rel_path}:{name}")
                return False

    return True


def backup_current(core_dir: str, backup_dir: str, generation: int) -> str:
    """Copy core/ to backups/gen_NNN_TIMESTAMP/. Return backup path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"gen_{generation:04d}_{timestamp}"
    backup_path = os.path.join(backup_dir, backup_name)
    shutil.copytree(core_dir, backup_path)
    return backup_path


def restore_backup(backup_path: str, core_dir: str) -> None:
    """Restore core/ from a backup."""
    shutil.rmtree(core_dir)
    shutil.copytree(backup_path, core_dir)


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


_PATH_RE = r"(core[/\\][\w./\\-]+\.py)"
_HEADER_PATTERNS = [
    re.compile(r"###?\s+FILE:\s*" + _PATH_RE, re.I),
    re.compile(r"###?\s+" + _PATH_RE, re.I),
    re.compile(r"---+\s+" + _PATH_RE + r"\s+---+", re.I),
    re.compile(r"\*\*" + _PATH_RE + r"\*\*", re.I),
    re.compile(r"`" + _PATH_RE + r"`", re.I),
    re.compile(r"File:\s*" + _PATH_RE, re.I),
    re.compile(r"^\s*" + _PATH_RE + r"\s*$", re.I),
]
_ANY_PATH_PATTERN = re.compile(_PATH_RE, re.I)


def _extract_file_path(text: str) -> str | None:
    """Return a normalized core/*.py path from a recognized header."""
    for pat in _HEADER_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip().replace("\\", "/")
    return None


def _find_core_path(text: str) -> str | None:
    """Return any normalized core/*.py path mentioned in text."""
    m = _ANY_PATH_PATTERN.search(text)
    return m.group(1).strip().replace("\\", "/") if m else None


def _looks_like_patch(content: str) -> bool:
    """Reject diffs; the evolution engine requires complete files."""
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return first.startswith(("diff --git", "--- ", "+++ ", "@@"))


def parse_gemini_response(response_text: str) -> dict[str, str]:
    """
    Parse Gemini's response into {rel_path: file_contents}.
    Handles 3- and 4-backtick fences in one response, several common file
    header formats, and filenames embedded in the opening fence or first code
    lines. Invalid Python and patch/diff output are ignored.
    """
    lines = response_text.splitlines()
    result = {}
    current_file = None
    i = 0
    f4 = chr(96) * 4
    f3 = chr(96) * 3

    while i < len(lines):
        path = _extract_file_path(lines[i])
        if path:
            current_file = path

        stripped = lines[i].lstrip()
        marker = f4 if stripped.startswith(f4) else f3 if stripped.startswith(f3) else None
        if not marker:
            i += 1
            continue

        block_file = _find_core_path(lines[i]) or current_file
        i += 1
        code_lines = []
        while i < len(lines):
            if lines[i].strip() == marker:
                i += 1
                break
            code_lines.append(lines[i])
            i += 1

        if not block_file:
            for code_line in code_lines[:5]:
                block_file = _find_core_path(code_line)
                if block_file:
                    break

        content = "\n".join(code_lines)
        if not block_file or not content.strip():
            current_file = None
            continue

        if _looks_like_patch(content):
            print(f"[CODEMOD] Skipping {block_file}: expected full file, got patch/diff.")
        else:
            try:
                ast.parse(content)
                result[block_file] = content
                print(f"[CODEMOD] Parsed {block_file} ({len(code_lines)} lines)")
            except SyntaxError as e:
                print(f"[CODEMOD] Skipping {block_file}: syntax error - {e}")

        current_file = None

    return result
