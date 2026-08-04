import json
from pathlib import Path

import pytest

from engine import candidate, runtime, state, verifier
from engine.controller import extract_target_files


def test_candidate_writer_allows_policy_file(tmp_path: Path):
    (tmp_path / "core").mkdir()

    candidate.write_files(tmp_path, {"core/prompts.py": "def value():\n    return 1\n"})

    assert (tmp_path / "core" / "prompts.py").read_text(encoding="utf-8").endswith("return 1\n")


@pytest.mark.parametrize(
    "path",
    [
        "bootstrap.py",
        "engine/verifier.py",
        "core/health.py",
        "tests/acceptance/test_bypass.py",
        "core/tests/../../engine/verifier.py",
    ],
)
def test_candidate_writer_rejects_trusted_paths(tmp_path: Path, path: str):
    (tmp_path / "core").mkdir()

    with pytest.raises(ValueError, match="Refusing"):
        candidate.write_files(tmp_path, {path: "pass\n"})


def test_parser_discards_protected_output():
    fence = "`" * 4
    response = f"### FILE: engine/verifier.py\n{fence}python\ndef bypass():\n    return True\n{fence}"

    assert candidate.parse_model_response(response) == {}


def test_policy_safety_rejects_file_and_system_access():
    imported = verifier.check_policy_source("core/prompts.py", "import os\n")
    opened = verifier.check_policy_source("core/prompts.py", "def x():\n    return open('x')\n")

    assert imported.ok is False
    assert opened.ok is False


def test_policy_safety_allows_strategy_imports():
    source = "import random\nfrom collections import defaultdict\ndef choose():\n    return random.Random(1)\n"

    assert verifier.check_policy_source("core/strategy.py", source).ok is True


def test_candidate_test_cannot_import_trusted_engine():
    result = verifier.check_candidate_test_source(
        "core/tests/test_bypass.py",
        "from engine import verifier\ndef test_bypass():\n    assert verifier\n",
    )

    assert result.ok is False


def test_state_has_one_final_outcome_per_generation(tmp_path: Path):
    current = state.fresh_state("evolve/test")
    state.record_outcome(current, 1, "strategy", "generation_failed")
    state.record_outcome(current, 1, "strategy", "deployed", commit="abc")
    state.save_state(tmp_path, current)
    loaded = state.load_state(tmp_path, "evolve/test")

    assert loaded["generation"] == 1
    assert loaded["history"] == [current["history"][0]]
    assert loaded["history"][0]["outcome"] == "deployed"
    assert loaded["history"][0]["commit"] == "abc"


def test_state_normalizes_legacy_success(tmp_path: Path):
    payload = {
        "generation": 2,
        "branch": "evolve/test",
        "history": [{"generation": 2, "strategy": "s", "outcome": "deploying"}],
    }
    (tmp_path / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = state.load_state(tmp_path, "evolve/test")

    assert loaded["history"][0]["outcome"] == "deployed"


def test_state_archives_another_branch_without_overwrite(tmp_path: Path):
    previous = state.fresh_state("evolve/old")
    state.record_outcome(previous, 1, "s", "deployed")
    state.save_state(tmp_path, previous)

    loaded = state.load_state(tmp_path, "evolve/new")
    archives = list((tmp_path / "states").glob("state_evolve_old_*.json"))

    assert loaded == state.fresh_state("evolve/new")
    assert len(archives) == 1


def test_unknown_outcome_fails_closed():
    with pytest.raises(state.StateError, match="Unknown final outcome"):
        state.record_outcome(state.fresh_state("evolve/test"), 1, "s", "deploying-now")


@pytest.mark.parametrize("branch", ["main", "", "feature/test"])
def test_evolution_rejects_non_evolve_branches(branch: str):
    with pytest.raises(runtime.SafetyError, match="Refusing to evolve"):
        runtime.validate_branch_name(branch)


def test_evolution_accepts_named_evolve_branch():
    runtime.validate_branch_name("evolve/seed-v0.8")


def test_ready_marker_requires_token_and_generation(tmp_path: Path):
    marker = tmp_path / ".evolve_ready"
    marker.write_text(
        json.dumps({"token": "right", "generation": 4, "pid": 123}),
        encoding="utf-8",
    )

    assert runtime.read_ready_marker(marker, "wrong", 4) is None
    assert runtime.read_ready_marker(marker, "right", 3) is None
    assert runtime.read_ready_marker(marker, "right", 4)["pid"] == 123


def test_targeting_summarizes_non_target_policy():
    files = {
        "core/prompts.py": "def build():\n    return 'full'\n",
        "core/strategy.py": "def get_strategy(generation, history):\n    return 'x'\n",
    }

    result = extract_target_files("In core/prompts.py: improve build", files)

    assert result["core/prompts.py"] == files["core/prompts.py"]
    assert "..." in result["core/strategy.py"]
