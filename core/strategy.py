"""
core/strategy.py - Evolution strategy selection.
Decides what the AI should try to improve each generation.
The AI can evolve this module to develop smarter strategy selection over time.
"""

# Strategies are specific and verifiable - each names a target file and exact change.
# Avoid vague goals (e.g. "improve robustness") that let the AI substitute safe but
# low-value changes like adding try/except or logging.
STRATEGIES = [
    "In core/prompts.py: improve build_improvement_prompt to include a concrete example of a good change vs a bad change, so the AI produces higher-quality improvements",
    "In core/codemod.py: add a validate_changes(original, proposed) function that checks proposed files preserve all existing top-level function signatures from the original",
    "In core/health.py: add a check that verifies all functions declared in core/evolve.py and core/health.py that bootstrap.py depends on are still callable with the correct signatures",
    "In core/evolve.py: track generation timing and compute a running success rate; log a summary line every 5 generations showing success_rate, avg_duration_seconds, and total_deploys",
    "In core/strategy.py: make get_strategy prefer strategies that succeeded recently - weight strategies by inverse of their recent failure count rather than pure round-robin",
    "In core/prompts.py: improve build_review_prompt to add a checklist item that explicitly rejects changes that do not directly implement the stated strategy (e.g. adding logging when the strategy targets a different concern)",
]


def get_strategy(generation: int, history: list) -> str:
    """
    Choose a strategy, skipping those that failed 3+ consecutive times recently.
    Falls back to full rotation if all strategies are blocked.
    """
    from collections import defaultdict

    strat_outcomes: dict[str, list[str]] = defaultdict(list)
    for entry in history:
        strat_outcomes[entry["strategy"]].append(entry["outcome"])

    blocked = {
        strat
        for strat, outcomes in strat_outcomes.items()
        if len(outcomes) >= 3 and all(o != "deploying" for o in outcomes[-3:])
    }

    available = [s for s in STRATEGIES if s not in blocked]
    if not available:
        available = list(STRATEGIES)  # unblock all to avoid deadlock

    return available[(generation - 1) % len(available)]
