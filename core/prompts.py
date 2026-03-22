"""
core/prompts.py - Prompt templates for the evolution engine.
This file is intentionally evolvable - improving these prompts is one
of the most impactful things the AI can do to improve itself.
"""

# Use a variable for the fence so this source file never contains a literal
# four-backtick line, which would confuse the parser in codemod.py.
_FENCE = chr(96) * 4  # ````

_OUTCOME_LABELS = {
    "deploying": "DEPLOYED successfully",
    "rejected": "REJECTED by reviewer",
    "validation_failed": "FAILED signature validation",
    "health_failed": "FAILED health checks",
    "write_failed": "FAILED to write to disk",
    "deploy_failed": "FAILED hot deploy",
    "no_changes": "produced NO CHANGES",
}


def build_analysis_prompt(file_contents: dict[str, str]) -> str:
    files_text = _format_files(file_contents)
    return f"""You are an AI software engineer reviewing your own source code.
Below are the Python source files that make up your evolvable modules.
Analyze them for:
- Bugs or error-prone patterns
- Missing error handling
- Performance issues
- Code clarity problems
- Missing features that would make the system more robust
- Opportunities to improve the prompts themselves

FILES:
{files_text}

Provide a concise analysis. Focus on the top 3 most impactful issues.
Be specific and actionable.
"""


def build_improvement_prompt(
    file_contents: dict[str, str], strategy: str, history: list | None = None
) -> str:
    files_text = _format_files(file_contents)
    history_section = ""
    if history:
        history_section = f"\nRECENT HISTORY (learn from past attempts — avoid repeating failures):\n{_format_history(history)}\n"

    # Detect if any files are summaries (contain "    ...") and add a note
    has_summaries = any("    ..." in content for content in file_contents.values())
    summary_note = ""
    if has_summaries:
        summary_note = """
NOTE: Files marked with "    ..." are summaries showing only signatures and imports.
Focus your changes on the file(s) with full content shown.
You may reference summaries to understand interfaces, but do NOT output modified versions of summary-only files.
"""

    return f"""You are an AI that modifies its own source code to improve itself.
Your current source files are below. Your task for this generation is:

STRATEGY: {strategy}
{history_section}
CURRENT FILES:
{files_text}
{summary_note}
Produce the improved versions of the files that need changing.
Output ONLY the modified files using this EXACT format for each file:

### FILE: core/filename.py
{_FENCE}python
# full file contents here
{_FENCE}

RULES:
- IMPORTANT: Use exactly four backticks (````) to open and close code fences, NOT three
- Do NOT place a line of four bare backticks inside Python strings; use chr(96)*4 to build the fence string at runtime if needed
- Output the COMPLETE file contents, not just diffs or patches
- Do NOT include bootstrap.py, config.yaml, or requirements.txt
- Preserve the function signature core/evolve.py:run(config) - bootstrap depends on it
- Preserve the function signature core/health.py:run_checks() - bootstrap depends on it
- Keep changes focused on the stated strategy
- Ensure all imports are valid Python
- If a file doesn't need changes, omit it from your response
"""


def build_review_prompt(
    original: dict[str, str], modified: dict[str, str], strategy: str
) -> str:
    original_text = _format_files(original)
    modified_text = _format_files(modified)
    return f"""You are a code reviewer. Determine if the proposed changes are safe and beneficial.

STRATEGY that was applied: {strategy}

Check for:
1. Syntax errors
2. Broken imports
3. Logic errors that could crash the system
4. Whether core API contracts are preserved:
   - core/evolve.py must have run(config) callable
   - core/health.py must have run_checks() callable
5. Strategy alignment: Do the changes DIRECTLY implement the stated strategy?
   REJECT if the changes are mostly unrelated to the strategy (e.g. adding error
   handling or logging when the strategy asks for a different kind of change, or
   adding docstrings when the strategy asks for new functionality). The diff must
   address what the strategy explicitly describes.

ORIGINAL FILES:
{original_text}

PROPOSED CHANGES:
{modified_text}

Respond with exactly one of:
- APPROVED - followed by a brief reason
- REJECTED - followed by the specific problems found
"""


def _format_files(file_contents: dict[str, str]) -> str:
    parts = []
    for path, content in file_contents.items():
        parts.append(f"--- {path} ---\n{content}")
    return "\n\n".join(parts)


def _format_history(history: list, limit: int = 10) -> str:
    recent = history[-limit:]
    lines = []
    for entry in recent:
        gen = entry.get("generation", "?")
        strat = entry.get("strategy", "")
        outcome = entry.get("outcome", "")
        label = _OUTCOME_LABELS.get(outcome, outcome)
        # Truncate long strategy text for readability
        strat_short = strat[:80] + "..." if len(strat) > 80 else strat
        lines.append(f"  gen-{gen}: \"{strat_short}\" → {label}")
    return "\n".join(lines)
