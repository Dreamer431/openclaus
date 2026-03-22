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


_HEADER_PATTERNS = [
    re.compile(r"###?\s+FILE:\s*(core/[\w./]+\.py)"),
    re.compile(r"###?\s+(core/[\w./]+\.py)"),
    re.compile(r"\*\*(core/[\w./]+\.py)\*\*"),
    re.compile(r"`(core/[\w./]+\.py)`"),
]


def _parse_with_fence(lines: list[str], open_prefix: str, close_val: str) -> dict[str, str]:
    """Inner parser for a specific fence style."""
    result = {}
    i = 0

    while i < len(lines):
        current_file = None
        for pat in _HEADER_PATTERNS:
            m = pat.search(lines[i])
            if m:
                current_file = m.group(1).strip()
                break

        if current_file:
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith(open_prefix):
                i += 1

            if i >= len(lines):
                break

            i += 1  # skip opening fence
            code_lines = []

            while i < len(lines):
                if lines[i].rstrip() == close_val:
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


def parse_gemini_response(response_text: str) -> dict[str, str]:
    """
    Parse Gemini's response into {rel_path: file_contents}.
    Tries 4-backtick fences first (avoids collision with triple-backtick strings
    in generated code), then falls back to 3-backtick fences.
    """
    lines = response_text.splitlines()

    result = _parse_with_fence(lines, open_prefix="````", close_val="````")
    if result:
        return result

    return _parse_with_fence(lines, open_prefix="```", close_val="```")
