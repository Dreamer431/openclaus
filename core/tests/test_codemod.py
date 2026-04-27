from core.codemod import parse_gemini_response, validate_changes


def test_parse_gemini_response_uses_four_backtick_fences():
    fence = chr(96) * 4
    response = f"### FILE: core/example.py\n{fence}python\ndef hello():\n    return 1\n{fence}"

    parsed = parse_gemini_response(response)

    assert parsed["core/example.py"] == "def hello():\n    return 1"


def test_parse_gemini_response_supports_mixed_fence_styles():
    f3 = chr(96) * 3
    f4 = chr(96) * 4
    response = (
        f"**core/one.py**\n{f4}python\ndef one():\n    pass\n{f4}\n"
        f"{f3}python core/two.py\ndef two():\n    pass\n{f3}"
    )

    parsed = parse_gemini_response(response)

    assert set(parsed) == {"core/one.py", "core/two.py"}


def test_parse_gemini_response_detects_filename_inside_code_block():
    fence = chr(96) * 3
    response = f"{fence}python\n# core/internal.py\ndef value():\n    return 42\n{fence}"

    parsed = parse_gemini_response(response)

    assert "core/internal.py" in parsed
    assert "def value():" in parsed["core/internal.py"]


def test_parse_gemini_response_normalizes_windows_style_paths():
    fence = chr(96) * 3
    response = f"File: core\\nested\\module.py\n{fence}\ndef ok():\n    return True\n{fence}"

    parsed = parse_gemini_response(response)

    assert "core/nested/module.py" in parsed


def test_parse_gemini_response_skips_patch_output():
    fence = chr(96) * 3
    response = f"### FILE: core/bad.py\n{fence}\n--- a/core/bad.py\n+++ b/core/bad.py\n@@\n{fence}"

    parsed = parse_gemini_response(response)

    assert parsed == {}


def test_parse_gemini_response_skips_invalid_python():
    fence = chr(96) * 3
    response = f"### FILE: core/bad.py\n{fence}\ndef broken(\n{fence}"

    parsed = parse_gemini_response(response)

    assert parsed == {}


def test_validate_changes_catches_public_signature_mismatch():
    original = {"core/logic.py": "def transform(value: int) -> int:\n    return value\n"}
    proposed = {"core/logic.py": "def transform(value: str) -> int:\n    return 1\n"}

    assert validate_changes(original, proposed) is False


def test_validate_changes_allows_private_signature_changes():
    original = {"core/logic.py": "def _helper(value: int) -> int:\n    return value\n"}
    proposed = {"core/logic.py": "def _helper(value: int, extra: int = 0) -> int:\n    return value + extra\n"}

    assert validate_changes(original, proposed) is True
