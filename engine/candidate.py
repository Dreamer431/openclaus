"""Trusted parsing, validation, backup, and writes for candidate policies."""

from __future__ import annotations

import ast
import os
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath


EVOLVABLE_FILES = frozenset({"core/prompts.py", "core/strategy.py"})
EVOLVABLE_TEST_PREFIX = "core/tests/"


def is_evolvable_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return normalized in EVOLVABLE_FILES or (
        normalized.startswith(EVOLVABLE_TEST_PREFIX)
        and normalized.endswith(".py")
        and normalized != f"{EVOLVABLE_TEST_PREFIX}__init__.py"
    )


def read_evolvable_files(project_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel_path in sorted(EVOLVABLE_FILES):
        path = project_root / PurePosixPath(rel_path)
        if path.is_symlink():
            raise ValueError(f"Refusing symlinked policy file: {path}")
        if path.is_file():
            result[rel_path] = path.read_text(encoding="utf-8")

    test_dir = project_root / "core" / "tests"
    if test_dir.is_dir():
        for path in sorted(test_dir.rglob("test_*.py")):
            if path.is_symlink():
                raise ValueError(f"Refusing symlinked candidate file: {path}")
            rel_path = path.relative_to(project_root).as_posix()
            result[rel_path] = path.read_text(encoding="utf-8")
    return result


def write_files(project_root: Path, file_contents: dict[str, str]) -> None:
    """Atomically write candidate files after resolving every path."""
    core_root = (project_root / "core").resolve()
    for raw_path, content in file_contents.items():
        rel_path = raw_path.replace("\\", "/")
        if not is_evolvable_path(rel_path):
            raise ValueError(f"Refusing to write protected path: {raw_path}")
        pure_path = PurePosixPath(rel_path)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise ValueError(f"Refusing unsafe candidate path: {raw_path}")

        target = (project_root / pure_path).resolve(strict=False)
        try:
            target.relative_to(core_root)
        except ValueError as exc:
            raise ValueError(f"Refusing path outside core/: {raw_path}") from exc

        _reject_symlink_components(project_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_suffix(target.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)


def validate_changes(original: dict[str, str], proposed: dict[str, str]) -> bool:
    """Preserve all existing public top-level function and class signatures."""
    for rel_path, proposed_content in proposed.items():
        if not is_evolvable_path(rel_path):
            print(f"[VALIDATE] Protected output path: {rel_path}")
            return False
        try:
            proposed_tree = ast.parse(proposed_content)
        except SyntaxError as exc:
            print(f"[VALIDATE] Syntax error in proposed {rel_path}: {exc}")
            return False

        if rel_path not in original:
            continue
        try:
            original_tree = ast.parse(original[rel_path])
        except SyntaxError as exc:
            print(f"[VALIDATE] Existing syntax error in {rel_path}: {exc}")
            return False

        original_symbols = _public_symbols(original_tree)
        proposed_symbols = _public_symbols(proposed_tree)
        for name, original_node in original_symbols.items():
            proposed_node = proposed_symbols.get(name)
            if proposed_node is None:
                print(f"[VALIDATE] Missing public symbol {name} in {rel_path}")
                return False
            if type(original_node) is not type(proposed_node):
                print(f"[VALIDATE] Symbol type changed for {rel_path}:{name}")
                return False
            if isinstance(original_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _signature(original_node) != _signature(proposed_node):
                    print(f"[VALIDATE] Signature mismatch in {rel_path}:{name}")
                    return False
    return True


def backup_current(project_root: Path, generation: int) -> Path:
    backup_dir = project_root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"gen_{generation:04d}_{timestamp}"
    shutil.copytree(
        project_root / "core",
        backup_path,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
    )
    return backup_path


def restore_backup(project_root: Path, backup_path: Path) -> None:
    core_dir = (project_root / "core").resolve()
    backup_dir = backup_path.resolve()
    if not backup_dir.is_dir() or backup_dir.parent != (project_root / "backups").resolve():
        raise ValueError(f"Invalid backup path: {backup_path}")
    if core_dir.parent != project_root.resolve():
        raise ValueError(f"Invalid core path: {core_dir}")
    shutil.rmtree(core_dir)
    shutil.copytree(backup_dir, core_dir)


def cleanup_old_backups(project_root: Path, max_backups: int) -> None:
    backup_dir = project_root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    limit = max(1, int(max_backups))
    entries = sorted(path for path in backup_dir.iterdir() if path.is_dir() and path.name.startswith("gen_"))
    for oldest in entries[:-limit]:
        if oldest.resolve().parent != backup_dir.resolve():
            raise ValueError(f"Refusing to remove unexpected backup path: {oldest}")
        shutil.rmtree(oldest)


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


def parse_model_response(response_text: str) -> dict[str, str]:
    """Parse complete Python files from three- or four-backtick blocks."""
    lines = response_text.splitlines()
    result: dict[str, str] = {}
    current_file: str | None = None
    index = 0
    four = "`" * 4
    three = "`" * 3

    while index < len(lines):
        header_path = _extract_file_path(lines[index])
        if header_path:
            current_file = header_path
        stripped = lines[index].lstrip()
        marker = four if stripped.startswith(four) else three if stripped.startswith(three) else None
        if not marker:
            index += 1
            continue

        block_file = _find_core_path(lines[index]) or current_file
        index += 1
        code_lines: list[str] = []
        closed = False
        while index < len(lines):
            if lines[index].strip() == marker:
                index += 1
                closed = True
                break
            code_lines.append(lines[index])
            index += 1
        if not closed:
            current_file = None
            continue
        if not block_file:
            block_file = next(
                (_find_core_path(line) for line in code_lines[:5] if _find_core_path(line)),
                None,
            )

        content = "\n".join(code_lines)
        if not block_file or not content.strip() or not is_evolvable_path(block_file):
            current_file = None
            continue
        first = next((line.strip() for line in content.splitlines() if line.strip()), "")
        if first.startswith(("diff --git", "--- ", "+++ ", "@@")):
            current_file = None
            continue
        try:
            ast.parse(content)
        except SyntaxError:
            current_file = None
            continue
        result[block_file] = content
        current_file = None
    return result


def _extract_file_path(text: str) -> str | None:
    for pattern in _HEADER_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip().replace("\\", "/")
    return None


def _find_core_path(text: str) -> str | None:
    match = _ANY_PATH_PATTERN.search(text)
    return match.group(1).strip().replace("\\", "/") if match else None


def _reject_symlink_components(project_root: Path, target: Path) -> None:
    current = project_root.resolve()
    relative = target.relative_to(current)
    for component in relative.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ValueError(f"Refusing symlinked candidate path: {target}")


def _public_symbols(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    }


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[object, ...]:
    def annotation(value: ast.AST | None) -> str | None:
        return ast.dump(value) if value is not None else None

    args = node.args
    return (
        type(node).__name__,
        tuple((arg.arg, annotation(arg.annotation)) for arg in args.posonlyargs),
        tuple((arg.arg, annotation(arg.annotation)) for arg in args.args),
        (args.vararg.arg, annotation(args.vararg.annotation)) if args.vararg else None,
        tuple((arg.arg, annotation(arg.annotation)) for arg in args.kwonlyargs),
        (args.kwarg.arg, annotation(args.kwarg.annotation)) if args.kwarg else None,
        len(args.defaults),
        tuple(default is not None for default in args.kw_defaults),
        annotation(node.returns),
    )
