"""
core/strategy.py - Evolution strategy selection.
Decides what the AI should try to improve each generation.
The AI can evolve this module to develop smarter strategy selection over time.
"""

import random
from collections import defaultdict

# Strategies are specific and verifiable - each names a target file and exact change.
# Avoid vague goals (e.g. "improve robustness") that let the AI substitute safe but
# low-value changes like adding try/except or logging.
STRATEGIES = [
    "In core/prompts.py: improve build_improvement_prompt to include a concrete example of a good change vs a bad change, so the AI produces higher-quality improvements",
    "In core/codemod.py: improve parse_gemini_response to handle edge cases like partial file output, missing file headers, or mixed fence styles within a single response",
    "In core/health.py: add a check that verifies all functions declared in core/evolve.py and core/health.py that bootstrap.py depends on are still callable with the correct signatures",
    "In core/evolve.py: track generation timing and compute a running success rate; log a summary line every 5 generations showing success_rate, avg_duration_seconds, and total_deploys",
    "In core/strategy.py: implement an 'exploration' bonus where strategies that have never been tried or haven't been tried in a long time get a weight boost to ensure variety",
    "In core/prompts.py: improve build_review_prompt to add a checklist item that explicitly rejects changes that do not directly implement the stated strategy (e.g. adding logging when the strategy targets a different concern)",
]


def get_strategy(generation: int, history: list) -> str:
    """
    Choose a strategy, preferring those that succeeded recently.
    Weights strategies by the ratio of successes to failures in recent history.
    Blocks strategies that failed 3+ consecutive times recently.
    """
    # Group outcomes by strategy
    strat_history = defaultdict(list)
    for entry in history:
        strat_history[entry["strategy"]].append(entry["outcome"])

    # Identify blocked strategies (3 consecutive failures)
    blocked = set()
    for strat, outcomes in strat_history.items():
        if len(outcomes) >= 3 and all(o != "deploying" for o in outcomes[-3:]):
            blocked.add(strat)

    available = [s for s in STRATEGIES if s not in blocked]
    if not available:
        available = list(STRATEGIES)  # unblock all to avoid deadlock

    # Calculate weights based on recent performance
    # "Recent" = last 5 attempts of that specific strategy
    weights = []
    for strat in available:
        outcomes = strat_history.get(strat, [])
        recent = outcomes[-5:]

        # In our history, "deploying" is the marker for a successful transition
        # to a new process. All other outcomes (rejected, health_failed, etc.)
        # indicate a failure to reach that stage.
        failures = len([o for o in recent if o != "deploying"])
        successes = len([o for o in recent if o == "deploying"])

        # Weight calculation:
        # We use (1 + successes) / (1 + failures) to prefer success while
        # penalizing failure, and providing a baseline weight for new strategies.
        weight = (1.0 + successes) / (1.0 + failures)
        weights.append(weight)

    # Use a local Random instance seeded by generation for deterministic
    # but weighted selection within a single generation run.
    rng = random.Random(generation)
    return rng.choices(available, weights=weights, k=1)[0]
