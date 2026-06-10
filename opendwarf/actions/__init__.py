"""Extensible action framework: registry + multi-tick skills.

The LLM picks *intents* (action strings). The registry maps each intent to one
of three kinds:
  - key:     single deferred input (move, attack, talk, wait, escape...)
  - skill:   a multi-tick deterministic controller stepped by the loop with no
             LLM calls in between (pathfinding, fast travel, menu sequences)
  - context: conversation choice selection

New DF capabilities (trade, sneak, butcher, climb...) are added as new
ActionSpecs / Skill subclasses — the loop never changes.
"""

from opendwarf.actions.registry import ActionKind, ActionSpec, ActionRegistry
from opendwarf.actions.skills import (
    Skill,
    SkillContext,
    SkillResult,
    SkillStatus,
    FastTravelController,
    RouteExecutor,
)

__all__ = [
    "ActionKind",
    "ActionSpec",
    "ActionRegistry",
    "Skill",
    "SkillContext",
    "SkillResult",
    "SkillStatus",
    "FastTravelController",
    "RouteExecutor",
]
