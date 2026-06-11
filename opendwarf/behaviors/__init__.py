"""Behavior layer — long-running deterministic controllers under an LLM-authored Policy.

See NORTHSTAR.md Part II (M1). The Policy is the standing order the LLM writes;
Behaviors (added incrementally) execute under it without per-step LLM calls.
"""

from opendwarf.behaviors.base import Behavior, BehaviorResult, BehaviorStatus
from opendwarf.behaviors.digest import EventDigest
from opendwarf.behaviors.grind_combat import GrindCombatBehavior
from opendwarf.behaviors.interrupts import Interrupt, InterruptReason, check
from opendwarf.behaviors.patrol import PatrolBehavior
from opendwarf.behaviors.policy import Policy

__all__ = [
    "Behavior",
    "BehaviorResult",
    "BehaviorStatus",
    "EventDigest",
    "GrindCombatBehavior",
    "Interrupt",
    "InterruptReason",
    "PatrolBehavior",
    "Policy",
    "check",
]
