from engine.candidate import parse_model_response, validate_changes


def test_parse_model_response_uses_four_backtick_fences():
    fence = chr(96) * 4
    response = f"### FILE: core/prompts.py\n{fence}python\ndef hello():\n    return 1\n{fence}"

    parsed = parse_model_response(response)

    assert parsed["core/prompts.py"] == "def hello():\n    return 1"


def test_parse_model_response_supports_mixed_fence_styles():
    three = chr(96) * 3
    four = chr(96) * 4
    response = (
        f"**core/prompts.py**\n{four}python\ndef one():\n    pass\n{four}\n"
        f"{three}python core/strategy.py\ndef two():\n    pass\n{three}"
    )

    assert set(parse_model_response(response)) == {"core/prompts.py", "core/strategy.py"}


def test_parse_model_response_detects_test_filename_inside_block():
    fence = chr(96) * 3
    response = f"{fence}python\n# core/tests/test_internal.py\ndef value():\n    return 42\n{fence}"

    parsed = parse_model_response(response)

    assert "core/tests/test_internal.py" in parsed


def test_parse_model_response_rejects_unclosed_fence():
    fence = chr(96) * 4
    response = f"### FILE: core/prompts.py\n{fence}python\ndef value():\n    return 1\n"

    assert parse_model_response(response) == {}


def test_parse_model_response_skips_patch_and_invalid_python():
    fence = chr(96) * 3
    patch = f"### FILE: core/prompts.py\n{fence}\n--- a/core/prompts.py\n+++ b/core/prompts.py\n@@\n{fence}"
    invalid = f"### FILE: core/prompts.py\n{fence}\ndef broken(\n{fence}"

    assert parse_model_response(patch) == {}
    assert parse_model_response(invalid) == {}


def test_validate_changes_catches_public_signature_mismatch():
    original = {"core/strategy.py": "def transform(value: int) -> int:\n    return value\n"}
    proposed = {"core/strategy.py": "def transform(value: str) -> int:\n    return 1\n"}

    assert validate_changes(original, proposed) is False


def test_validate_changes_allows_private_signature_changes():
    original = {"core/strategy.py": "def _helper(value: int) -> int:\n    return value\n"}
    proposed = {"core/strategy.py": "def _helper(value: int, extra: int = 0) -> int:\n    return value + extra\n"}

    assert validate_changes(original, proposed) is True
