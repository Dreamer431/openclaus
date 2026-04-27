from core.evolve import _extract_target_files


def test_extract_target_files_handles_nested_existing_file():
    files = {
        "core/codemod.py": "def parse_gemini_response(response_text: str) -> dict[str, str]:\n    return {}\n",
        "core/tests/test_codemod.py": "def test_existing():\n    assert True\n",
    }

    result = _extract_target_files(
        "In core/tests/test_codemod.py: update parser regression tests",
        files,
    )

    assert result["core/tests/test_codemod.py"] == files["core/tests/test_codemod.py"]
    assert result["core/codemod.py"] != files["core/codemod.py"]
    assert "..." in result["core/codemod.py"]


def test_extract_target_files_uses_named_public_symbols_for_new_test_strategy():
    files = {
        "core/codemod.py": (
            "def parse_gemini_response(response_text: str) -> dict[str, str]:\n"
            "    return {}\n\n"
            "def validate_changes(original: dict[str, str], proposed: dict[str, str]) -> bool:\n"
            "    return True\n"
        ),
        "core/prompts.py": "def build_review_prompt(original, modified, strategy):\n    return ''\n",
    }

    result = _extract_target_files(
        "In core/tests/: create core/tests/test_codemod.py tests for "
        "parse_gemini_response and validate_changes",
        files,
    )

    assert result["core/codemod.py"] == files["core/codemod.py"]
    assert result["core/prompts.py"] != files["core/prompts.py"]
    assert "..." in result["core/prompts.py"]
