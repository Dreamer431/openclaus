"""
core/prompts.py - Prompt templates for the evolution engine.
This file is intentionally evolvable - improving these prompts is one
of the most impactful things the AI can do to improve itself.
"""


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


def build_improvement_prompt(file_contents: dict[str, str], strategy: str) -> str:
    files_text = _format_files(file_contents)
    return f"""You are an AI that modifies its own source code to improve itself.
Your current source files are below. Your task for this generation is:

STRATEGY: {strategy}

CURRENT FILES:
{files_text}

Produce the improved versions of the files that need changing.
Output ONLY the modified files using this EXACT format for each file:

### FILE: core/filename.py
```python
# full file contents here
```

RULES:
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
4. Whether the changes actually accomplish the stated strategy
5. Whether core API contracts are preserved:
   - core/evolve.py must have run(config) callable
   - core/health.py must have run_checks() callable

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
