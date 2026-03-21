"""
core/strategy.py - Evolution strategy selection.
Decides what the AI should try to improve each generation.
The AI can evolve this module to develop smarter strategy selection over time.
"""

# Initial strategy rotation - cycles through these in order
STRATEGIES = [
    "Improve the prompt templates in core/prompts.py to produce more reliable code generation",
    "Add better error handling and logging throughout core/ modules",
    "Improve the parse_gemini_response function in core/codemod.py to handle more response formats",
    "Improve the health checks in core/health.py to catch more potential issues",
    "Refactor core/evolve.py for clarity and robustness",
    "Improve the review prompt to catch more types of bugs before they are applied",
    "Add a fitness tracking mechanism to measure improvement across generations",
    "Improve error messages and logging to make debugging easier",
]


def get_strategy(generation: int, history: list) -> str:
    """
    Choose a strategy for the given generation.
    Initially rotates through the STRATEGIES list.
    Future versions of this function may use history to make smarter choices.
    """
    index = (generation - 1) % len(STRATEGIES)
    return STRATEGIES[index]
