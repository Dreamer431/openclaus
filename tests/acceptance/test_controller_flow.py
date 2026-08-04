import json
from pathlib import Path
from types import SimpleNamespace

from engine import controller, runtime


ORIGINAL_PROMPTS = """def build_improvement_prompt(file_contents, strategy, history=None):
    return 'old'

def build_review_prompt(original, modified, strategy):
    return 'review'
"""

IMPROVED_PROMPTS = """def build_improvement_prompt(file_contents, strategy, history=None):
    return 'improved'

def build_review_prompt(original, modified, strategy):
    return 'review'
"""


class FakeBrain:
    def __init__(self, **kwargs):
        self.closed = False

    def generate_improvement(self, files, strategy, history):
        return {"core/prompts.py": IMPROVED_PROMPTS}

    def review_changes(self, original, modified, strategy):
        return True, "APPROVED - focused change"

    def close(self):
        self.closed = True


class FailingBrain(FakeBrain):
    def generate_improvement(self, files, strategy, history):
        raise RuntimeError("503 UNAVAILABLE")


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "prompts.py").write_text(ORIGINAL_PROMPTS, encoding="utf-8")
    (tmp_path / "core" / "strategy.py").write_text(
        "def get_strategy(generation, history):\n    return 'policy'\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text(
        "logs/\nbackups/\n.evolve_*\n.openclaus.lock\n", encoding="utf-8"
    )
    runtime.run_git(tmp_path, "init")
    runtime.run_git(tmp_path, "config", "user.name", "OpenClaus Tests")
    runtime.run_git(tmp_path, "config", "user.email", "tests@openclaus.invalid")
    runtime.run_git(tmp_path, "add", ".")
    runtime.run_git(tmp_path, "commit", "-m", "seed")
    runtime.run_git(tmp_path, "checkout", "-b", "evolve/test")
    return tmp_path


def configure_controller(monkeypatch, repo: Path):
    monkeypatch.setattr(controller, "PROJECT_ROOT", repo)
    monkeypatch.setattr(controller, "LOG_DIR", repo / "logs")
    monkeypatch.setattr(controller, "READY_MARKER", repo / ".evolve_ready")
    monkeypatch.setattr(controller, "ACK_MARKER", repo / ".evolve_ack")
    monkeypatch.setattr(controller, "ACTIVE_MARKER", repo / ".evolve_active")
    monkeypatch.setattr(controller, "Brain", FakeBrain)
    monkeypatch.setattr(controller.strategy_module, "get_strategy", lambda generation, history: "policy")
    monkeypatch.setattr(
        controller.verifier,
        "run_checks",
        lambda *args, **kwargs: SimpleNamespace(ok=True),
    )


def config() -> dict:
    return {
        "gemini": {"api_key": "test", "model": "test"},
        "evolution": {
            "max_generations": 1,
            "delay_between_generations": 0,
            "hot_deploy_timeout": 5,
        },
        "safety": {"max_backups": 2, "test_timeout": 5},
    }


def read_state(repo: Path) -> dict:
    return json.loads((repo / "logs" / "state.json").read_text(encoding="utf-8"))


def test_complete_generation_records_one_deployed_outcome(tmp_path: Path, monkeypatch):
    repo = make_repo(tmp_path)
    configure_controller(monkeypatch, repo)

    def successful_handoff(generation, timeout, on_ready):
        on_ready()
        return True, "active"

    monkeypatch.setattr(controller, "perform_hot_deploy", successful_handoff)

    controller._run_locked(config(), "evolve/test")

    persisted = read_state(repo)
    assert persisted["generation"] == 1
    assert [entry["outcome"] for entry in persisted["history"]] == ["deployed"]
    assert (repo / "core" / "prompts.py").read_text(encoding="utf-8") == IMPROVED_PROMPTS
    assert runtime.run_git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"


def test_activation_failure_reverts_and_replaces_success_outcome(tmp_path: Path, monkeypatch):
    repo = make_repo(tmp_path)
    configure_controller(monkeypatch, repo)

    def failed_after_ready(generation, timeout, on_ready):
        on_ready()
        return False, "child never became active"

    monkeypatch.setattr(controller, "perform_hot_deploy", failed_after_ready)

    controller._run_locked(config(), "evolve/test")

    persisted = read_state(repo)
    assert [entry["outcome"] for entry in persisted["history"]] == ["deploy_failed"]
    assert persisted["history"][0]["rollback_commit"]
    assert (repo / "core" / "prompts.py").read_text(encoding="utf-8") == ORIGINAL_PROMPTS
    assert runtime.run_git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "3"


def test_model_failure_still_advances_generation(tmp_path: Path, monkeypatch):
    repo = make_repo(tmp_path)
    configure_controller(monkeypatch, repo)
    monkeypatch.setattr(controller, "Brain", FailingBrain)

    controller._run_locked(config(), "evolve/test")

    persisted = read_state(repo)
    assert persisted["generation"] == 1
    assert [entry["outcome"] for entry in persisted["history"]] == ["generation_failed"]
    assert "503 UNAVAILABLE" in persisted["history"][0]["error"]
    assert runtime.run_git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"
