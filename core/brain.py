"""Compatibility import for the trusted model client.

The active implementation lives in :mod:`engine.model_client` and is outside
the evolvable write allowlist.
"""

from engine.model_client import Brain

__all__ = ["Brain"]
