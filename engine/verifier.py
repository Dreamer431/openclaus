"""Independent verifier for evolvable policy code."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


POLICY_FILES = ("core/prompts.py", "core/strategy.py")
ALLOWED_POLICY_IMPORTS = frozenset({"collections", "random"})
ALLOWED_TEST_IMPORTS = frozenset({"core", "pytest", "random"})
FORBIDDEN_CALLS = frozenset(
    {
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "input",
        "locals",
        "open",
        "__import__",
    }
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class VerificationResult:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def run_checks(
    project_root: Path | None = None,
    *,
    timeout: int = 60,
    verbose: bool = True,
) -> VerificationResult:
    root = (project_root or Path(__file__).resolve().parent.parent).resolve()
    checks: list[CheckResult] = []
    for check in (_check_syntax, _check_policy_safety, _check_policy_contracts, _check_policy_imports):
        try:
            result = check(root)
        except Exception as exc:
            result = CheckResult(check.__name__, False, f"{type(exc).__name__}: {exc}")
        checks.append(result)
        if verbose:
            _print_result(result)
        if not result.ok:
            return VerificationResult(tuple(checks))

    test_result = _check_tests(root, timeout)
    checks.append(test_result)
    if verbose:
        _print_result(test_result)
    return VerificationResult(tuple(checks))


def check_policy_source(rel_path: str, source: str) -> CheckResult:
    """Static defense-in-depth for code imported by the trusted controller."""
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return CheckResult("policy_safety", False, f"Syntax error in {rel_path}: {exc}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in ALLOWED_POLICY_IMPORTS:
                    return CheckResult(
                        "policy_safety", False, f"Disallowed import '{alias.name}' in {rel_path}"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or root not in ALLOWED_POLICY_IMPORTS:
                return CheckResult(
                    "policy_safety", False, f"Disallowed import from '{node.module}' in {rel_path}"
                )
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name in FORBIDDEN_CALLS:
                return CheckResult(
                    "policy_safety", False, f"Disallowed call '{name}' in {rel_path}"
                )
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return CheckResult(
                "policy_safety", False, f"Disallowed dunder attribute '{node.attr}' in {rel_path}"
            )
    return CheckResult("policy_safety", True, f"Static policy rules passed for {rel_path}")


def check_candidate_test_source(rel_path: str, source: str) -> CheckResult:
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return CheckResult("candidate_test_safety", False, f"Syntax error in {rel_path}: {exc}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = {alias.name for alias in node.names}
            roots = {module.split(".", 1)[0] for module in modules}
            unsafe_core = any(
                module.startswith("core.") and module not in {"core.prompts", "core.strategy"}
                for module in modules
            )
            if not roots.issubset(ALLOWED_TEST_IMPORTS) or unsafe_core:
                return CheckResult(
                    "candidate_test_safety", False, f"Disallowed test import in {rel_path}: {sorted(modules)}"
                )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or root not in ALLOWED_TEST_IMPORTS or (node.module or "").startswith("core.") and node.module not in {"core.prompts", "core.strategy"}:
                return CheckResult(
                    "candidate_test_safety", False, f"Disallowed test import from '{node.module}' in {rel_path}"
                )
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name in FORBIDDEN_CALLS:
                return CheckResult(
                    "candidate_test_safety", False, f"Disallowed test call '{name}' in {rel_path}"
                )
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return CheckResult(
                "candidate_test_safety", False, f"Disallowed dunder attribute '{node.attr}' in {rel_path}"
            )
    return CheckResult("candidate_test_safety", True, f"Static candidate-test rules passed for {rel_path}")


def _check_syntax(root: Path) -> CheckResult:
    count = 0
    for base in (root / "engine", root / "core"):
        for path in base.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            count += 1
    return CheckResult("syntax", True, f"All {count} engine/core Python files parse")


def _check_policy_safety(root: Path) -> CheckResult:
    for rel_path in POLICY_FILES:
        path = root / rel_path
        if not path.is_file():
            return CheckResult("policy_safety", False, f"Missing policy file: {rel_path}")
        result = check_policy_source(rel_path, path.read_text(encoding="utf-8"))
        if not result.ok:
            return result
    test_dir = root / "core" / "tests"
    if test_dir.is_dir():
        for path in sorted(test_dir.rglob("test_*.py")):
            rel_path = path.relative_to(root).as_posix()
            result = check_candidate_test_source(rel_path, path.read_text(encoding="utf-8"))
            if not result.ok:
                return result
    return CheckResult("policy_safety", True, "Evolvable policy passed static safety rules")


def _check_policy_contracts(root: Path) -> CheckResult:
    expected = {
        "core/prompts.py": {
            "build_improvement_prompt": 3,
            "build_review_prompt": 3,
        },
        "core/strategy.py": {"get_strategy": 2},
    }
    for rel_path, functions in expected.items():
        tree = ast.parse((root / rel_path).read_text(encoding="utf-8"))
        found = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name, call_count in functions.items():
            node = found.get(name)
            if node is None or not isinstance(node, ast.FunctionDef):
                return CheckResult("contracts", False, f"Missing synchronous {rel_path}:{name}")
            positional = len(node.args.posonlyargs) + len(node.args.args)
            required = positional - len(node.args.defaults)
            required_kwonly = any(default is None for default in node.args.kw_defaults)
            supports_call = (
                required <= call_count
                and (positional >= call_count or node.args.vararg is not None)
                and not required_kwonly
            )
            if not supports_call:
                return CheckResult("contracts", False, f"Incompatible signature for {rel_path}:{name}")
    return CheckResult("contracts", True, "Trusted controller policy contracts are preserved")


def _check_policy_imports(root: Path) -> CheckResult:
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(root)!r}); "
        "from core import prompts, strategy; "
        "assert callable(prompts.build_improvement_prompt); "
        "assert callable(prompts.build_review_prompt); "
        "assert callable(strategy.get_strategy)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=root,
        env=_sanitized_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-1000:].strip()
        return CheckResult("imports", False, f"Policy import failed: {detail}")
    return CheckResult("imports", True, "Policy modules import in an isolated interpreter")


def _check_tests(root: Path, timeout: int) -> CheckResult:
    targets = [path for path in (root / "core" / "tests", root / "tests" / "acceptance") if path.is_dir()]
    if not targets:
        return CheckResult("tests", False, "No candidate or fixed acceptance tests found")
    command = [
        sys.executable,
        "-m",
        "pytest",
        *(str(path) for path in targets),
        "-q",
        "-p",
        "no:cacheprovider",
        "--tb=short",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=_sanitized_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout)),
        )
    except subprocess.TimeoutExpired:
        return CheckResult("tests", False, f"Tests timed out after {timeout}s")
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return CheckResult("tests", False, output[-2000:])
    last_line = output.splitlines()[-1] if output else "passed"
    return CheckResult("tests", True, last_line)


def _sanitized_env() -> dict[str, str]:
    blocked_fragments = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in blocked_fragments)
        and not key.upper().startswith("GIT_CONFIG_")
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def _print_result(result: CheckResult) -> None:
    label = "OK" if result.ok else "FAIL"
    print(f"[VERIFY] {label}: {result.message}")
