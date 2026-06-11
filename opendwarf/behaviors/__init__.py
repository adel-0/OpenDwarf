"""Behavior layer — long-running deterministic controllers under an LLM-authored Policy.

See NORTHSTAR.md Part II (M1). The Policy is the standing order the LLM writes;
Behaviors (added incrementally) execute under it without per-step LLM calls.
"""

from opendwarf.behaviors.policy import Policy

__all__ = ["Policy"]
