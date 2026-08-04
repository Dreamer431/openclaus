import os
from pathlib import Path

import pytest

from engine import runtime


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "prompts.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_git(tmp_path, "init")
    runtime.run_git(tmp_path, "config", "user.name", "OpenClaus Tests")
    runtime.run_git(tmp_path, "config", "user.email", "tests@openclaus.invalid")
    runtime.run_git(tmp_path, "add", ".")
    runtime.run_git(tmp_path, "commit", "-m", "seed")
    runtime.run_git(tmp_path, "checkout", "-b", "evolve/test")
    return tmp_path


def test_safe_context_requires_clean_evolve_branch(tmp_path: Path):
    repo = make_repo(tmp_path)

    branch, head = runtime.ensure_safe_context(repo)

    assert branch == "evolve/test"
    assert head == runtime.current_head(repo)


def test_safe_context_rejects_dirty_worktree(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(runtime.SafetyError, match="dirty worktree"):
        runtime.ensure_safe_context(repo)


def test_failed_candidate_is_reverted_without_history_rewrite(tmp_path: Path):
    repo = make_repo(tmp_path)
    base = runtime.current_head(repo)
    policy = repo / "core" / "prompts.py"
    policy.write_text("VALUE = 2\n", encoding="utf-8")

    candidate_commit = runtime.commit_core(repo, "candidate", base)
    rollback_commit = runtime.revert_candidate(repo, candidate_commit)

    assert rollback_commit != candidate_commit
    assert policy.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert runtime.run_git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "3"


def test_single_instance_lock_recovers_stale_pid(tmp_path: Path):
    (tmp_path / ".openclaus.lock").write_text(
        '{"pid": 99999999, "token": "stale"}', encoding="utf-8"
    )

    with runtime.EvolutionLock(tmp_path):
        assert (tmp_path / ".openclaus.lock").exists()

    assert not (tmp_path / ".openclaus.lock").exists()


def test_pid_probe_recognizes_current_process():
    assert runtime.pid_is_alive(os.getpid()) is True
