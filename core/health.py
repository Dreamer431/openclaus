"""
core/health.py - Health checks for the evolution engine.
Called by bootstrap.py after a hot deploy to verify the new version is valid.
The run_checks() signature must never change - bootstrap.py depends on it.
"""

import ast
import importlib
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
    core_dir = os.path.join(os.path.dirname(__file__))
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
    modules = ["core.prompts", "core.codemod", "core.health", "core.strategy", "core.brain"]
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
    """Verify the critical API contracts that bootstrap.py depends on."""
    # Check core.evolve.run is callable
    if "core.evolve" in sys.modules:
        del sys.modules["core.evolve"]
    try:
        import core.evolve as evolve_mod
        if not callable(getattr(evolve_mod, "run", None)):
            return False, "core.evolve.run is not callable"
    except Exception as e:
        return False, f"core.evolve failed to load: {e}"

    # Check core.health.run_checks is callable (self-referential but correct)
    if not callable(run_checks):
        return False, "core.health.run_checks is not callable"

    return True, "API contracts OK (run, run_checks)"
