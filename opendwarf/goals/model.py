"""Goal data model — simplified flat goal list with structured plan steps."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GoalStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DONE = "DONE"
    DROPPED = "DROPPED"


class CompletionType(str, Enum):
    """Machine-checkable plan step completion conditions.

    Each type has clear Python-verifiable semantics:
    - TRAVEL: position changed by >= min_tiles from step start position.
    - TALK: a conversation was completed (dialogue_ended trigger fired).
    - REACH_SITE: site_name field changed to a non-empty value.
    - COMBAT: combat was resolved (combat_resolved trigger fired).
    - GET_ITEM: inventory changed (item count increased).
    - APPROACH_NPC: moved adjacent (dist<=1) to any non-hostile NPC.
    - GOTO: a goto_* movement skill reached its target (signalled via the
      'goto_arrived' trigger from the loop).
    - GENERIC: no specific condition — uses timeout only.
    """

    TRAVEL = "travel"
    TALK = "talk"
    REACH_SITE = "reach_site"
    COMBAT = "combat"
    GET_ITEM = "get_item"
    APPROACH_NPC = "approach_npc"
    GOTO = "goto"
    GENERIC = "generic"


@dataclass
class PlanStep:
    """A structured plan step with machine-checkable completion."""

    description: str
    completion_type: CompletionType
    direction: str | None = None  # for TRAVEL steps: compass direction
    min_tiles: int = 8  # for TRAVEL steps: minimum distance before complete
    max_turns: int = 15  # fallback timeout for ALL step types

    # Runtime tracking (not serialized to LLM)
    turns_elapsed: int = field(default=0, repr=False)
    start_position: tuple[int, int, int] | None = field(default=None, repr=False)
    start_inventory_count: int = field(default=-1, repr=False)
    triggered: bool = field(default=False, repr=False)  # condition-triggered completion

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "description": self.description,
            "completion": self.completion_type.value,
        }
        if self.direction:
            d["direction"] = self.direction
        if self.completion_type == CompletionType.TRAVEL:
            d["min_tiles"] = self.min_tiles
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanStep:
        ct_raw = d.get("completion", "generic")
        try:
            ct = CompletionType(ct_raw)
        except ValueError:
            ct = CompletionType.GENERIC
        return cls(
            description=d.get("description", ""),
            completion_type=ct,
            direction=d.get("direction"),
            min_tiles=d.get("min_tiles", 8),
        )


@dataclass
class Goal:
    """A goal record. Ordering is implicit — first in the list = most important."""

    id: str
    description: str
    status: GoalStatus
    created_tick: int

    @classmethod
    def new(
        cls,
        description: str,
        created_tick: int,
        *,
        status: GoalStatus = GoalStatus.ACTIVE,
    ) -> "Goal":
        return cls(
            id=str(uuid.uuid4())[:8],
            description=description,
            status=status,
            created_tick=created_tick,
        )

    def is_terminal(self) -> bool:
        return self.status in (GoalStatus.DONE, GoalStatus.DROPPED)

    def is_active(self) -> bool:
        return self.status == GoalStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "created_tick": self.created_tick,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Goal":
        return cls(
            id=d["id"],
            description=d["description"],
            status=GoalStatus(d["status"]),
            created_tick=d["created_tick"],
        )

    def summary_line(self) -> str:
        return f"[{self.status.value}] {self.description}"
