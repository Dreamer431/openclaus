"""
core/health.py - Health checks for the evolution engine.
Called by bootstrap.py after a hot deploy to verify the new version is valid.
The run_checks() signature must never change - bootstrap.py depends on it.
"""

import ast
import importlib
import inspect
import os
import sys


def run_checks() -> bool:
    """
    Run all health checks. Return True if all pass, False otherwise.
    This function is called by bootstrap.py and must always be importable.
    """
    checks = [
        _check_syntax,
        _check_imports,
        _check_api_contracts,
        _check_tests,
    ]
    for check in checks:
        try:
            ok, message = check()
            if ok:
                print(f"[HEALTH] OK: {message}")
            else:
                print(f"[HEALTH] FAIL: {message}")
                return False
        except Exception as e:
            print(f"[HEALTH] ERROR in {check.__name__}: {e}")
            return False
    return True


def _check_syntax() -> tuple[bool, str]:
    """Verify all core/*.py files parse as valid Python."""
    core_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in os.listdir(core_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(core_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            return False, f"Syntax error in core/{fname}: {e}"
    return True, "All core/*.py files have valid syntax"


def _check_imports() -> tuple[bool, str]:
    """Verify all core modules can be imported without crashing."""
    modules = [
        "core.prompts",
        "core.codemod",
        "core.health",
        "core.strategy",
        "core.brain",
        "core.evolve",
    ]
    for mod_name in modules:
        # Force reimport to get the latest version from disk
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            return False, f"Failed to import {mod_name}: {e}"
    return True, "All core modules imported successfully"


def _check_api_contracts() -> tuple[bool, str]:
    """
    Verify the critical API contracts that bootstrap.py depends on.
    Checks both callability and function signatures for core/evolve.py and core/health.py.
    This ensures that the evolution process can always restart and verify itself.
    """
    # 1. Check core.evolve.run(config)
    try:
        if "core.evolve" in sys.modules:
            del sys.modules["core.evolve"]
        import core.evolve as evolve_mod

        if not hasattr(evolve_mod, "run"):
            return False, "core.evolve.run is missing"

        run_fn = evolve_mod.run
        if not callable(run_fn):
            return False, "core.evolve.run is not callable"

        sig = inspect.signature(run_fn)
        params = list(sig.parameters.values())

        # bootstrap.py expects run(config)
        if len(params) != 1:
            return False, f"core.evolve.run signature mismatch: expected 1 arg, got {len(params)}"

        # Ensure it's not a keyword-only argument that would break positional call
        if params[0].kind == inspect.Parameter.KEYWORD_ONLY:
            return False, "core.evolve.run argument 'config' cannot be keyword-only"

    except Exception as e:
        return False, f"core.evolve contract check failed: {e}"

    # 2. Check core.health.run_checks()
    try:
        if "core.health" in sys.modules:
            del sys.modules["core.health"]
        import core.health as health_mod

        if not hasattr(health_mod, "run_checks"):
            return False, "core.health.run_checks is missing"

        run_hc_fn = health_mod.run_checks
        if not callable(run_hc_fn):
            return False, "core.health.run_checks is not callable"

        sig = inspect.signature(run_hc_fn)
        params = list(sig.parameters.values())

        # bootstrap.py expects run_checks() with no arguments
        if len(params) != 0:
            return False, f"core.health.run_checks signature mismatch: expected 0 args, got {len(params)}"

        # Verify return type annotation if present (bootstrap expects bool)
        if sig.return_annotation not in (inspect.Signature.empty, bool):
            return False, f"core.health.run_checks return type mismatch: expected bool, got {sig.return_annotation}"

    except Exception as e:
        return False, f"core.health contract check failed: {e}"

    return True, "API contracts and signatures OK (core.evolve.run(config), core.health.run_checks())"


def _check_tests() -> tuple[bool, str]:
    """Run pytest on core/tests/ if the directory exists. Skip if absent."""
    import subprocess
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")
    if not os.path.isdir(test_dir):
        return True, "No core/tests/ directory found, skipping"
    test_files = [f for f in os.listdir(test_dir) if f.startswith("test_") and f.endswith(".py")]
    if not test_files:
        return True, "No test files in core/tests/, skipping"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_dir, "-x", "-q", "--tb=short"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "passed"
        return True, f"Tests passed: {last_line}"
    output = (result.stdout + result.stderr)[-500:]
    return False, f"Tests failed:\n{output}"
