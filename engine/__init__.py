"""Trusted runtime for OpenClaus.

Files in this package are never writable by the evolution engine.  A running
generation may propose policy changes under ``core/``; this package remains the
independent controller and evaluator for that proposal.
"""
