from engine.controller import extract_target_files


def test_extract_target_files_handles_existing_file():
    files = {
        "core/prompts.py": "def build_review_prompt(original, modified, strategy):\n    return ''\n",
        "core/strategy.py": "def get_strategy(generation, history):\n    return ''\n",
    }

    result = extract_target_files("In core/prompts.py: update review prompt", files)

    assert result["core/prompts.py"] == files["core/prompts.py"]
    assert "..." in result["core/strategy.py"]


def test_extract_target_files_uses_named_public_symbol():
    files = {
        "core/prompts.py": "def build_review_prompt(original, modified, strategy):\n    return ''\n",
        "core/strategy.py": "def get_strategy(generation, history):\n    return ''\n",
    }

    result = extract_target_files("Improve get_strategy selection", files)

    assert result["core/strategy.py"] == files["core/strategy.py"]
    assert "..." in result["core/prompts.py"]
