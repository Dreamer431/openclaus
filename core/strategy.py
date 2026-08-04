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
    "In core/prompts.py: improve build_improvement_prompt with one concise example that contrasts a focused policy change with an unrelated low-value change",
    "In core/prompts.py: improve build_review_prompt so it rejects proposals that weaken tests, broaden the allowed paths, or bypass the trusted engine boundary",
    "In core/prompts.py: improve _format_history to expose duration_seconds, changed_files, and failure details without making the prompt excessively long",
    "In core/strategy.py: improve get_strategy so repeated no_changes or rejected outcomes reduce a strategy's weight without permanently starving exploration",
    "In core/strategy.py: use deployed history metadata such as duration_seconds and changed_files as a small bounded quality signal while keeping selection deterministic per generation",
    "In core/strategy.py: improve exploration so every available strategy receives a minimum probability and recently repeated strategies cool down predictably",
    "In core/tests/: add focused pytest regression tests for core.strategy.get_strategy covering deployed, rejected, cooldown, and previously unseen strategies",
    "In core/tests/: add focused pytest tests for core.prompts ensuring trusted-boundary rules and final outcome labels remain present in generated prompts",
]


def get_strategy(generation: int, history: list) -> str:
    """
    Choose a strategy, preferring those that succeeded recently.
    Weights strategies by recent success/failure ratio plus a small
    exploration bonus for strategies that have not been tried recently.
    Blocks strategies that failed 3+ consecutive times recently.
    Also enforces a cooldown: same strategy cannot be chosen more than 3 consecutive times.
    """
    # Group outcomes by strategy
    strat_history = defaultdict(list)
    for entry in history:
        strat_history[entry["strategy"]].append(entry["outcome"])

    # Identify blocked strategies (3 consecutive failures)
    blocked = set()
    for strat, outcomes in strat_history.items():
        if len(outcomes) >= 3 and all(o != "deployed" for o in outcomes[-3:]):
            blocked.add(strat)

    # Cooldown: block strategy used 3+ consecutive times (regardless of outcome)
    if len(history) >= 3:
        last_three = [e["strategy"] for e in history[-3:]]
        if len(set(last_three)) == 1:
            blocked.add(last_three[0])

    available = [s for s in STRATEGIES if s not in blocked]
    if not available:
        available = list(STRATEGIES)  # unblock all to avoid deadlock

    # Calculate weights based on recent performance
    # "Recent" = last 5 attempts of that specific strategy
    weights = []
    for strat in available:
        outcomes = strat_history.get(strat, [])
        recent = outcomes[-5:]

        # Only a completed, token-authenticated process handoff is a success.
        failures = len([o for o in recent if o != "deployed"])
        successes = len([o for o in recent if o == "deployed"])

        # Weight calculation:
        # We use (1 + successes) / (1 + failures) to prefer success while
        # penalizing failure, and providing a baseline weight for new strategies.
        weight = (1.0 + successes) / (1.0 + failures)
        weight += _exploration_bonus(strat, history)
        weights.append(weight)

    # Use a local Random instance seeded by generation for deterministic
    # but weighted selection within a single generation run.
    rng = random.Random(generation)
    return rng.choices(available, weights=weights, k=1)[0]


def _exploration_bonus(strategy: str, history: list) -> float:
    """
    Return a bounded bonus for strategies not tried recently.
    Kept small so exploration helps variety without overwhelming success data.
    """
    if not history:
        return 0.25

    for distance, entry in enumerate(reversed(history), start=0):
        if entry["strategy"] == strategy:
            return min(distance * 0.05, 0.5)

    return 0.5
